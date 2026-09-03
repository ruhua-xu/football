from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from pydantic import Field, model_validator

from football_system.application.environment import (
    CrossEnvironmentInputError,
    ProviderRuntimeProvenanceMismatchError,
    ProviderRuntimeProvenanceRequiredError,
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    RuntimeDataModeError,
    RuntimeProvenance,
    is_mock_provider_code,
    require_provider_runtime_provenance,
)
from football_system.application.models import AnalysisArtifacts
from football_system.application.ports.data_providers import (
    HistoricalDataProvider,
    MatchResultBatch,
    MatchResultQuery,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
    build_input_manifest_json,
)
from football_system.application.settlement import SettlementService
from football_system.config import AppSettings
from football_system.domain.analysis import AnalysisRunStatus
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestDataMode,
    BacktestMatchSnapshot,
    BacktestMetrics,
    BacktestMetricsConfig,
    BacktestRun,
    BacktestRunStatus,
    BacktestSlateSnapshot,
    BacktestSlice,
    BacktestStrategySnapshot,
    canonical_archive_provenance,
    sha256_text,
)
from football_system.domain.betting import (
    Portfolio,
    PortfolioConstraints,
    PortfolioStatus,
    TicketAllocation,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
    utc_now,
)
from football_system.domain.match import MarketOddsSnapshot, SportteryBonusSnapshot
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.risk import PortfolioRiskReport
from football_system.domain.services.backtest_metrics import (
    calculate_backtest_metrics,
)
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    PortfolioSettlement,
    PortfolioSettlementResult,
    Settlement,
    SettlementResultReason,
    SettlementScope,
    SettlementStatus,
)

WALK_FORWARD_BACKTEST_VERSION = "BACKTEST_V1"
EXPLICIT_CHRONOLOGICAL_SLATE_POLICY = "DAILY_FIXED_CUTOFF_V1"
SUPPORTED_BACKTEST_POLICIES = frozenset(
    {
        FusionPolicyName.QUANT_ONLY_V1,
        FusionPolicyName.MARKET_QUANT_BLEND_V1,
    }
)


class BacktestSlatePlan(DomainModel):
    """An explicit decision and evaluation boundary for one fixed slate."""

    decision_as_of_at_utc: UtcDateTime
    evaluation_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    match_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_timeline(self) -> BacktestSlatePlan:
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("slate kickoff window is invalid")
        if self.decision_as_of_at_utc > self.kickoff_from_utc:
            raise ValueError("slate decision cutoff cannot follow its kickoff window")
        if self.evaluation_as_of_at_utc <= self.kickoff_to_utc:
            raise ValueError("slate evaluation cutoff must follow its kickoff window")
        if len(self.match_ids) != len(set(self.match_ids)):
            raise ValueError("backtest slate match IDs must be unique")
        return self


class WalkForwardBacktestRequest(DomainModel):
    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    fusion_policy: FusionPolicyName
    slates: tuple[BacktestSlatePlan, ...] = Field(min_length=1)
    budget_fen: int = Field(ge=0)
    quant_weight: Decimal = Field(ge=0, le=1)
    min_selection_ev: Decimal = Field(ge=0)
    min_ticket_roi: Decimal = Field(ge=0)
    constraints: PortfolioConstraints
    backtest_version: Identifier = WALK_FORWARD_BACKTEST_VERSION
    metrics_config: BacktestMetricsConfig = Field(default_factory=BacktestMetricsConfig)
    archive_provenance: tuple[BacktestArchiveProvenance, ...] = ()
    execution_time_utc: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_strategy_and_slates(self) -> WalkForwardBacktestRequest:
        if self.fusion_policy not in SUPPORTED_BACKTEST_POLICIES:
            raise ValueError(
                "walk-forward V1 supports only QUANT_ONLY_V1 and MARKET_QUANT_BLEND_V1"
            )
        for previous, current in zip(self.slates, self.slates[1:]):
            if current.decision_as_of_at_utc <= previous.evaluation_as_of_at_utc:
                raise ValueError(
                    "slates must be in strict chronological order without overlap"
                )
        object.__setattr__(
            self,
            "archive_provenance",
            canonical_archive_provenance(self.archive_provenance),
        )
        if (
            self.execution_time_utc is not None
            and self.execution_time_utc < self.slates[-1].evaluation_as_of_at_utc
        ):
            raise ValueError(
                "backtest execution time cannot precede the final evaluation cutoff"
            )
        return self


class WalkForwardBacktestSlateResult(DomainModel):
    plan: BacktestSlatePlan
    analysis_artifacts: AnalysisArtifacts
    match_result_batch: MatchResultBatch
    portfolio_settlement_result: PortfolioSettlementResult
    backtest_slice: BacktestSlice
    match_snapshots: tuple[BacktestMatchSnapshot, ...]
    slate_snapshot: BacktestSlateSnapshot
    ticket_settlements: tuple[Settlement, ...]
    portfolio_settlement: PortfolioSettlement | None = None

    @model_validator(mode="after")
    def validate_artifact_graph(self) -> WalkForwardBacktestSlateResult:
        _validate_slate_result_outputs(
            self,
            backtest_run_id=self.backtest_slice.backtest_run_id,
            data_mode=self.backtest_slice.data_mode,
            slice_id=self.backtest_slice.slice_id,
        )
        return self


class WalkForwardBacktestResult(DomainModel):
    request: WalkForwardBacktestRequest
    backtest_run: BacktestRun
    slate_results: tuple[WalkForwardBacktestSlateResult, ...]
    metrics: BacktestMetrics

    @model_validator(mode="after")
    def validate_result(self) -> WalkForwardBacktestResult:
        _validate_result_graph(self)
        return self

    @property
    def analysis_artifacts(self) -> tuple[AnalysisArtifacts, ...]:
        return tuple(item.analysis_artifacts for item in self.slate_results)

    @property
    def match_result_batches(self) -> tuple[MatchResultBatch, ...]:
        return tuple(item.match_result_batch for item in self.slate_results)

    @property
    def portfolio_settlement_results(
        self,
    ) -> tuple[PortfolioSettlementResult, ...]:
        return tuple(item.portfolio_settlement_result for item in self.slate_results)

    @property
    def backtest_slices(self) -> tuple[BacktestSlice, ...]:
        return tuple(item.backtest_slice for item in self.slate_results)

    @property
    def match_snapshots(self) -> tuple[BacktestMatchSnapshot, ...]:
        return tuple(
            snapshot for item in self.slate_results for snapshot in item.match_snapshots
        )

    @property
    def slate_snapshots(self) -> tuple[BacktestSlateSnapshot, ...]:
        return tuple(item.slate_snapshot for item in self.slate_results)

    @property
    def ticket_settlements(self) -> tuple[Settlement, ...]:
        return tuple(
            settlement
            for item in self.slate_results
            for settlement in item.ticket_settlements
        )

    @property
    def portfolio_settlements(self) -> tuple[PortfolioSettlement, ...]:
        return tuple(
            item.portfolio_settlement
            for item in self.slate_results
            if item.portfolio_settlement is not None
        )


class BacktestComparisonSide(DomainModel):
    fusion_policy: FusionPolicyName
    backtest_run_id: Identifier
    metrics: BacktestMetrics


class BacktestComparisonResult(DomainModel):
    left: BacktestComparisonSide
    right: BacktestComparisonSide


def validate_backtest_runtime_provenance(
    data_mode: BacktestDataMode,
    provenance_by_role: Mapping[str, RuntimeProvenance],
) -> None:
    expected_roles = {
        "fixture",
        "market_odds",
        "sporttery",
        "manual_quant",
        "match_result",
    }
    if set(provenance_by_role) != expected_roles:
        raise ProviderRuntimeProvenanceRequiredError(
            "walk-forward backtest requires provenance for every provider role"
        )
    provenance = tuple(provenance_by_role.values())
    if any(item.data_mode is not data_mode for item in provenance):
        raise RuntimeDataModeError(
            "walk-forward provider data modes must match the backtest request"
        )
    mock_flags = tuple(
        item.is_mock or is_mock_provider_code(item.provider_code) for item in provenance
    )
    if any(mock_flags) and not all(mock_flags):
        raise CrossEnvironmentInputError(
            "walk-forward backtest cannot mix mock and real providers"
        )
    expected_environment = (
        RuntimeEnvironment.RESEARCH
        if data_mode is BacktestDataMode.SOURCE_TIME_RESEARCH
        else RuntimeEnvironment.LIVE
    )
    if all(mock_flags):
        if any(
            item.environment not in {RuntimeEnvironment.MOCK, expected_environment}
            for item in provenance
        ):
            raise CrossEnvironmentInputError(
                "synthetic acceptance providers use an incompatible runtime"
            )
        return
    RuntimeEnvironmentGuard(expected_environment).validate(provenance)


class WalkForwardBacktestService:
    def __init__(
        self,
        run_analysis_service: RunAnalysisService,
        historical_data_provider: HistoricalDataProvider,
        settlement_service: SettlementService | None = None,
    ) -> None:
        self._run_analysis_service = run_analysis_service
        self._historical_data_provider = historical_data_provider
        self._settlement_service = settlement_service or SettlementService()

    async def run(
        self,
        request: WalkForwardBacktestRequest,
    ) -> WalkForwardBacktestResult:
        request = WalkForwardBacktestRequest.model_validate(
            request.model_dump(mode="python", exclude_computed_fields=True)
        )
        runtime_provenance = (
            self._run_analysis_service.declared_provider_runtime_provenance()
        )
        result_provenance = require_provider_runtime_provenance(
            self._historical_data_provider,
            "match_result",
        )
        validate_backtest_runtime_provenance(
            request.data_mode,
            {**runtime_provenance, "match_result": result_provenance},
        )
        execution_time_utc = request.execution_time_utc or utc_now()
        if execution_time_utc < request.slates[-1].evaluation_as_of_at_utc:
            raise ValueError(
                "backtest execution time cannot precede the final evaluation cutoff"
            )
        expected_slice_ids = tuple(
            _slice_id(request, slate_no, plan)
            for slate_no, plan in enumerate(request.slates, start=1)
        )
        slate_results: list[WalkForwardBacktestSlateResult] = []
        for slate_no, plan in enumerate(request.slates, start=1):
            slice_id = expected_slice_ids[slate_no - 1]
            analysis_run_id = _analysis_run_id(request, slate_no, plan)
            frozen_artifacts = await self._run_decision_phase(
                request,
                plan,
                analysis_run_id,
                execution_time_utc,
            )
            slate_results.append(
                await self._run_evaluation_phase(
                    request,
                    plan,
                    slice_id,
                    frozen_artifacts,
                    result_provenance,
                )
            )

        code_revisions = {
            item.analysis_artifacts.analysis_run.code_revision for item in slate_results
        }
        if len(code_revisions) != 1:
            raise ValueError("walk-forward slates used different code revisions")
        backtest_run = BacktestRun(
            backtest_run_id=request.backtest_run_id,
            backtest_version=request.backtest_version,
            data_mode=request.data_mode,
            date_from=request.slates[0].kickoff_from_utc.date(),
            date_to=request.slates[-1].kickoff_to_utc.date(),
            strategy_snapshot=_strategy_snapshot(request),
            code_revision=next(iter(code_revisions)),
            created_at_utc=execution_time_utc,
            status=BacktestRunStatus.COMPLETED,
            archive_provenance=request.archive_provenance,
            expected_slice_ids=expected_slice_ids,
        )
        match_snapshots = tuple(
            snapshot for item in slate_results for snapshot in item.match_snapshots
        )
        slate_snapshots = tuple(item.slate_snapshot for item in slate_results)
        metrics = calculate_backtest_metrics(
            backtest_run,
            match_snapshots,
            slate_snapshots,
            request.metrics_config,
        )
        return validate_walk_forward_backtest_result(
            WalkForwardBacktestResult(
                request=request,
                backtest_run=backtest_run,
                slate_results=tuple(slate_results),
                metrics=metrics,
            )
        )

    async def _run_decision_phase(
        self,
        request: WalkForwardBacktestRequest,
        plan: BacktestSlatePlan,
        analysis_run_id: str,
        execution_time_utc: UtcDateTime,
    ) -> AnalysisArtifacts:
        artifacts = await self._run_analysis_service.run(
            RunAnalysisRequest(
                as_of_at_utc=plan.decision_as_of_at_utc,
                kickoff_from_utc=plan.kickoff_from_utc,
                kickoff_to_utc=plan.kickoff_to_utc,
                budgets_fen=(request.budget_fen,),
                fusion_policy=request.fusion_policy,
                min_selection_ev=request.min_selection_ev,
                min_ticket_roi=request.min_ticket_roi,
                analysis_run_id=analysis_run_id,
                execution_time_utc=execution_time_utc,
                allow_partial_inputs=True,
                expected_match_ids=plan.match_ids or None,
            )
        )
        frozen = AnalysisArtifacts.model_validate(artifacts.model_dump(mode="python"))
        _validate_frozen_decision(
            frozen,
            request,
            plan,
            analysis_run_id,
            execution_time_utc,
        )
        return frozen

    async def _run_evaluation_phase(
        self,
        request: WalkForwardBacktestRequest,
        plan: BacktestSlatePlan,
        slice_id: str,
        frozen_artifacts: AnalysisArtifacts,
        result_provenance: RuntimeProvenance,
    ) -> WalkForwardBacktestSlateResult:
        match_ids = tuple(match.match_id for match in frozen_artifacts.matches)
        if match_ids:
            result_batch = await self._historical_data_provider.fetch_match_results(
                MatchResultQuery(
                    match_ids=match_ids,
                    as_of_at_utc=plan.evaluation_as_of_at_utc,
                )
            )
            result_batch = MatchResultBatch.model_validate(
                result_batch.model_dump(
                    mode="python",
                    exclude_computed_fields=True,
                )
            )
        else:
            result_batch = MatchResultBatch(
                as_of_at_utc=plan.evaluation_as_of_at_utc,
                results=(),
                mappings=(),
            )
        _validate_evaluation_batch(
            result_batch,
            match_ids,
            plan,
            request.data_mode,
            result_provenance,
        )

        portfolio = frozen_artifacts.portfolios[0]
        portfolio_match_ids = {
            leg.match_id
            for ticket in portfolio.tickets
            for leg in ticket.candidate.legs
        }
        settlement_result = self._settlement_service.settle_portfolio(
            SettlementScope.for_analysis_run(
                frozen_artifacts.analysis_run.analysis_run_id
            ),
            portfolio,
            (
                result
                for result in result_batch.results
                if result.match_id in portfolio_match_ids
            ),
            plan.evaluation_as_of_at_utc,
            result_issues=(
                issue
                for issue in result_batch.issues
                if issue.match_id in portfolio_match_ids
            ),
        )
        (
            backtest_slice,
            match_snapshots,
            slate_snapshot,
            ticket_settlements,
            portfolio_settlement,
        ) = _build_slate_result_outputs(
            backtest_run_id=request.backtest_run_id,
            data_mode=request.data_mode,
            slice_id=slice_id,
            plan=plan,
            artifacts=frozen_artifacts,
            result_batch=result_batch,
            settlement_result=settlement_result,
        )
        return WalkForwardBacktestSlateResult(
            plan=plan,
            analysis_artifacts=frozen_artifacts,
            match_result_batch=result_batch,
            portfolio_settlement_result=settlement_result,
            backtest_slice=backtest_slice,
            match_snapshots=match_snapshots,
            slate_snapshot=slate_snapshot,
            ticket_settlements=ticket_settlements,
            portfolio_settlement=portfolio_settlement,
        )

    @staticmethod
    def compare(
        left: WalkForwardBacktestResult,
        right: WalkForwardBacktestResult,
    ) -> BacktestComparisonResult:
        return compare_backtests(left, right)


def validate_walk_forward_backtest_result(
    result: WalkForwardBacktestResult,
) -> WalkForwardBacktestResult:
    """Reparse and recompute a complete result graph before it is persisted."""
    return WalkForwardBacktestResult.model_validate(
        result.model_dump(mode="python", exclude_computed_fields=True)
    )


def _validate_result_graph(result: WalkForwardBacktestResult) -> None:
    request = result.request
    run = result.backtest_run
    if tuple(item.plan for item in result.slate_results) != request.slates:
        raise ValueError("backtest result does not cover the requested slates")

    expected_slice_ids = tuple(
        _slice_id(request, slate_no, plan)
        for slate_no, plan in enumerate(request.slates, start=1)
    )
    if run.backtest_run_id != request.backtest_run_id:
        raise ValueError("backtest result references another request")
    if run.backtest_version != request.backtest_version:
        raise ValueError("backtest result version is inconsistent")
    if run.data_mode != request.data_mode:
        raise ValueError("backtest result data mode is inconsistent")
    if run.strategy_snapshot != _strategy_snapshot(request):
        raise ValueError("backtest result strategy snapshot is inconsistent")
    if run.archive_provenance != request.archive_provenance:
        raise ValueError("backtest run archive provenance is inconsistent")
    if run.expected_slice_ids != expected_slice_ids:
        raise ValueError("backtest run expected slice IDs are inconsistent")
    if (
        run.date_from != request.slates[0].kickoff_from_utc.date()
        or run.date_to != request.slates[-1].kickoff_to_utc.date()
    ):
        raise ValueError("backtest run date range is inconsistent")
    if run.status != BacktestRunStatus.COMPLETED:
        raise ValueError("walk-forward backtest run must be completed")
    final_evaluation = request.slates[-1].evaluation_as_of_at_utc
    if run.created_at_utc < final_evaluation:
        raise ValueError(
            "backtest execution time cannot precede the final evaluation cutoff"
        )
    if (
        request.execution_time_utc is not None
        and run.created_at_utc != request.execution_time_utc
    ):
        raise ValueError("backtest run does not use the requested execution time")

    code_revisions = {
        item.analysis_artifacts.analysis_run.code_revision
        for item in result.slate_results
    }
    if code_revisions != {run.code_revision}:
        raise ValueError("backtest run code revision is inconsistent with its slates")

    for slate_no, (plan, slate_result, slice_id) in enumerate(
        zip(
            request.slates,
            result.slate_results,
            expected_slice_ids,
            strict=True,
        ),
        start=1,
    ):
        analysis_run_id = _analysis_run_id(request, slate_no, plan)
        _validate_frozen_decision(
            slate_result.analysis_artifacts,
            request,
            plan,
            analysis_run_id,
            run.created_at_utc,
        )
        _validate_slate_result_outputs(
            slate_result,
            backtest_run_id=run.backtest_run_id,
            data_mode=run.data_mode,
            slice_id=slice_id,
        )

    expected_metrics = calculate_backtest_metrics(
        run,
        result.match_snapshots,
        result.slate_snapshots,
        request.metrics_config,
    )
    if result.metrics != expected_metrics:
        raise ValueError("backtest metrics do not match validated snapshots")


def _validate_slate_result_outputs(
    result: WalkForwardBacktestSlateResult,
    *,
    backtest_run_id: str,
    data_mode: BacktestDataMode,
    slice_id: str,
) -> None:
    expected_match_ids = result.plan.match_ids or tuple(
        match.match_id for match in result.analysis_artifacts.matches
    )
    if (
        result.backtest_slice.kickoff_from_utc,
        result.backtest_slice.kickoff_to_utc,
        result.backtest_slice.expected_match_ids,
    ) != (
        result.plan.kickoff_from_utc,
        result.plan.kickoff_to_utc,
        expected_match_ids,
    ):
        raise ValueError("backtest slice does not match planned slate structure")
    expected = _build_slate_result_outputs(
        backtest_run_id=backtest_run_id,
        data_mode=data_mode,
        slice_id=slice_id,
        plan=result.plan,
        artifacts=result.analysis_artifacts,
        result_batch=result.match_result_batch,
        settlement_result=result.portfolio_settlement_result,
    )
    actual = (
        result.backtest_slice,
        result.match_snapshots,
        result.slate_snapshot,
        result.ticket_settlements,
        result.portfolio_settlement,
    )
    labels = (
        "backtest slice",
        "settled match snapshots",
        "backtest slate snapshot",
        "ticket settlement artifacts",
        "portfolio settlement artifact",
    )
    for label, actual_value, expected_value in zip(
        labels,
        actual,
        expected,
        strict=True,
    ):
        if actual_value != expected_value:
            raise ValueError(f"{label} does not match frozen replay artifacts")


def _build_slate_result_outputs(
    *,
    backtest_run_id: str,
    data_mode: BacktestDataMode,
    slice_id: str,
    plan: BacktestSlatePlan,
    artifacts: AnalysisArtifacts,
    result_batch: MatchResultBatch,
    settlement_result: PortfolioSettlementResult,
) -> tuple[
    BacktestSlice,
    tuple[BacktestMatchSnapshot, ...],
    BacktestSlateSnapshot,
    tuple[Settlement, ...],
    PortfolioSettlement | None,
]:
    analysis_run = artifacts.analysis_run
    if (
        analysis_run.as_of_at_utc != plan.decision_as_of_at_utc
        or analysis_run.status != AnalysisRunStatus.COMPLETED
    ):
        raise ValueError("frozen AnalysisRun does not match the slate timeline")
    _validate_analysis_input_manifest(artifacts)
    if len(artifacts.portfolios) != 1:
        raise ValueError("walk-forward V1 requires exactly one budget portfolio")

    match_ids = tuple(match.match_id for match in artifacts.matches)
    analyzed_match_ids = set(match_ids)
    expected_match_ids = plan.match_ids or match_ids
    expected_match_set = set(expected_match_ids)
    if any(match_id not in expected_match_set for match_id in match_ids):
        raise ValueError("frozen analysis contains an unexpected decision match")
    missing_decision_match_ids = tuple(
        match_id
        for match_id in expected_match_ids
        if match_id not in analyzed_match_ids
    )
    _validate_evaluation_batch(result_batch, match_ids, plan, data_mode)
    result_by_match = {result.match_id: result for result in result_batch.results}
    issue_by_match = {issue.match_id: issue for issue in result_batch.issues}
    ordered_results = tuple(
        result_by_match[match.match_id]
        for match in artifacts.matches
        if match.match_id in result_by_match
    )
    ordered_issues = tuple(
        issue_by_match[match.match_id]
        for match in artifacts.matches
        if match.match_id in issue_by_match
    )
    portfolio = artifacts.portfolios[0]
    ticket_settlements = _validate_settlement_artifacts(
        artifacts,
        portfolio,
        result_by_match,
        issue_by_match,
        settlement_result,
        plan,
    )

    market_by_match = _unique_by_match(
        artifacts.market_predictions,
        "market prediction",
    )
    quant_by_match = _unique_by_match(
        artifacts.quant_predictions,
        "quant prediction",
    )
    final_by_match = _unique_by_match(
        artifacts.final_predictions,
        "final prediction",
    )
    match_snapshots = tuple(
        BacktestMatchSnapshot(
            backtest_run_id=backtest_run_id,
            data_mode=data_mode,
            slice_id=slice_id,
            match_id=match.match_id,
            outcome=result_by_match[match.match_id].three_way_selection(),
            p_market=market_by_match[match.match_id].probabilities,
            p_quant=quant_by_match[match.match_id].probabilities,
            p_final=final_by_match[match.match_id].probabilities,
        )
        for match in artifacts.matches
        if match.match_id in result_by_match
    )
    settled_ticket_count = len(ticket_settlements)
    backtest_slice = BacktestSlice(
        slice_id=slice_id,
        backtest_run_id=backtest_run_id,
        data_mode=data_mode,
        decision_as_of_at_utc=plan.decision_as_of_at_utc,
        kickoff_from_utc=plan.kickoff_from_utc,
        kickoff_to_utc=plan.kickoff_to_utc,
        evaluation_as_of_at_utc=plan.evaluation_as_of_at_utc,
        analysis_run_id=analysis_run.analysis_run_id,
        decision_input_manifest_hash=analysis_run.input_manifest_hash,
        match_result_ids=tuple(item.match_result_id for item in ordered_results),
        match_result_issues=ordered_issues,
        expected_match_ids=expected_match_ids,
        missing_decision_match_ids=missing_decision_match_ids,
        match_count=len(expected_match_ids),
        settled_match_count=len(match_snapshots),
        settled_ticket_count=settled_ticket_count,
        unsettled_ticket_count=len(portfolio.tickets) - settled_ticket_count,
    )

    risk_report = _risk_report_for(artifacts, portfolio)
    settled_by_ticket = {
        settlement.ticket_id: settlement for settlement in ticket_settlements
    }
    settled_stake = sum(item.stake_fen for item in ticket_settlements)
    gross_payout = sum(item.gross_payout_fen for item in ticket_settlements)
    slate_snapshot = BacktestSlateSnapshot(
        backtest_run_id=backtest_run_id,
        data_mode=data_mode,
        slice_id=slice_id,
        decision_as_of_at_utc=plan.decision_as_of_at_utc,
        match_count=len(expected_match_ids),
        settled_match_count=len(match_snapshots),
        ticket_count=len(portfolio.tickets),
        settled_ticket_count=settled_ticket_count,
        winning_ticket_count=sum(
            item.status == SettlementStatus.WON for item in ticket_settlements
        ),
        budget_fen=portfolio.budget_fen,
        stake_fen=portfolio.total_stake_fen,
        settled_stake_fen=settled_stake,
        cash_fen=portfolio.cash_position.amount_fen,
        gross_payout_fen=gross_payout,
        profit_loss_fen=gross_payout - settled_stake,
        is_no_bet=portfolio.status == PortfolioStatus.NO_BET,
        ticket_odds=tuple(_ticket_odds(item) for item in portfolio.tickets),
        ticket_probabilities=tuple(
            item.probability_any_payout for item in portfolio.tickets
        ),
        selection_evs=tuple(
            leg.ev for ticket in portfolio.tickets for leg in ticket.candidate.legs
        ),
        max_match_exposure_fen=risk_report.max_match_exposure_fen,
        max_selection_exposure_fen=max(
            (item.exposed_stake_fen for item in risk_report.selection_exposures),
            default=0,
        ),
        realized_loss_when_top_exposure_failed_fen=_realized_top_exposure_loss(
            portfolio,
            risk_report,
            result_by_match,
            settled_by_ticket,
            1,
        ),
        realized_loss_when_top_two_exposure_failed_fen=(
            _realized_top_exposure_loss(
                portfolio,
                risk_report,
                result_by_match,
                settled_by_ticket,
                2,
            )
        ),
    )
    return (
        backtest_slice,
        match_snapshots,
        slate_snapshot,
        ticket_settlements,
        settlement_result.portfolio_settlement,
    )


def _validate_settlement_artifacts(
    artifacts: AnalysisArtifacts,
    portfolio: Portfolio,
    result_by_match: dict[str, MatchResult],
    issue_by_match: dict[str, MatchSettlementIssue],
    settlement_result: PortfolioSettlementResult,
    plan: BacktestSlatePlan,
) -> tuple[Settlement, ...]:
    expected_ticket_ids = tuple(ticket.ticket_id for ticket in portfolio.tickets)
    if (
        settlement_result.portfolio_id != portfolio.portfolio_id
        or tuple(item.ticket_id for item in settlement_result.ticket_results)
        != expected_ticket_ids
    ):
        raise ValueError("portfolio settlement does not cover the frozen tickets")
    if settlement_result.portfolio_unsupported_reasons:
        raise ValueError("walk-forward settlement cannot use unsupported semantics")

    settlements: list[Settlement] = []
    has_missing = False
    has_issues = False
    scope = SettlementScope.for_analysis_run(artifacts.analysis_run.analysis_run_id)
    for ticket, ticket_result in zip(
        portfolio.tickets,
        settlement_result.ticket_results,
        strict=True,
    ):
        expected_match_ids = tuple(leg.match_id for leg in ticket.candidate.legs)
        covered_match_ids = tuple(
            match_id for match_id in expected_match_ids if match_id in result_by_match
        )
        expected_issues = tuple(
            issue_by_match[match_id]
            for match_id in expected_match_ids
            if match_id in issue_by_match
        )
        missing_match_ids = tuple(
            match_id
            for match_id in expected_match_ids
            if match_id not in result_by_match and match_id not in issue_by_match
        )
        coverage = ticket_result.coverage
        if (
            coverage.expected_match_ids != expected_match_ids
            or coverage.covered_match_ids != covered_match_ids
            or coverage.missing_match_ids != missing_match_ids
            or coverage.issues != expected_issues
            or coverage.unsupported_reasons
        ):
            raise ValueError("ticket settlement coverage contradicts visible results")
        if expected_issues:
            has_issues = True
            if (
                ticket_result.reason
                != SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
                or ticket_result.settlement is not None
            ):
                raise ValueError("unsupported ticket settlement is inconsistent")
            continue
        if missing_match_ids:
            has_missing = True
            if (
                ticket_result.reason != SettlementResultReason.MISSING_RESULT
                or ticket_result.settlement is not None
            ):
                raise ValueError("partial ticket settlement is inconsistent")
            continue

        settlement = ticket_result.settlement
        if ticket_result.reason != SettlementResultReason.SETTLED or settlement is None:
            raise ValueError("complete ticket results require a settlement")
        ordered_results = tuple(
            result_by_match[match_id] for match_id in expected_match_ids
        )
        won = all(
            result.three_way_selection() == leg.selection
            for leg, result in zip(ticket.candidate.legs, ordered_results, strict=True)
        )
        expected_status = SettlementStatus.WON if won else SettlementStatus.LOST
        expected_payout = ticket.potential_gross_payout_fen if won else 0
        if (
            settlement.scope_kind != scope.scope_kind
            or settlement.parent_analysis_run_id != scope.parent_analysis_run_id
            or settlement.decision_scope_id != scope.decision_scope_id
            or settlement.portfolio_id != portfolio.portfolio_id
            or settlement.ticket_id != ticket.ticket_id
            or settlement.match_result_ids
            != tuple(result.match_result_id for result in ordered_results)
            or settlement.status != expected_status
            or settlement.stake_fen != ticket.stake_fen
            or settlement.gross_payout_fen != expected_payout
            or settlement.profit_loss_fen != expected_payout - ticket.stake_fen
            or settlement.payout_policy_version
            != ticket.candidate.payout_policy_version
            or settlement.settled_at_utc != plan.evaluation_as_of_at_utc
            or settlement.supersedes_settlement_id is not None
        ):
            raise ValueError(
                "ticket settlement lineage contradicts frozen replay artifacts"
            )
        settlements.append(settlement)

    expected_reason = SettlementResultReason.SETTLED
    if has_issues:
        expected_reason = SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
    elif has_missing:
        expected_reason = SettlementResultReason.MISSING_RESULT
    if settlement_result.reason != expected_reason:
        raise ValueError("portfolio settlement coverage is inconsistent")
    portfolio_settlement = settlement_result.portfolio_settlement
    if has_issues or has_missing:
        if portfolio_settlement is not None:
            raise ValueError("partial portfolio cannot contain a settlement")
        return tuple(settlements)
    if portfolio_settlement is None:
        raise ValueError("fully settled portfolio requires an aggregation")

    ticket_settlement_ids = tuple(item.settlement_id for item in settlements)
    gross_payout = sum(item.gross_payout_fen for item in settlements)
    ending_capital = portfolio.cash_position.amount_fen + gross_payout
    if (
        portfolio_settlement.scope_kind != scope.scope_kind
        or portfolio_settlement.parent_analysis_run_id != scope.parent_analysis_run_id
        or portfolio_settlement.decision_scope_id != scope.decision_scope_id
        or portfolio_settlement.portfolio_id != portfolio.portfolio_id
        or portfolio_settlement.ticket_settlement_ids != ticket_settlement_ids
        or portfolio_settlement.budget_fen != portfolio.budget_fen
        or portfolio_settlement.deployed_stake_fen != portfolio.total_stake_fen
        or portfolio_settlement.original_cash_fen != portfolio.cash_position.amount_fen
        or portfolio_settlement.gross_ticket_payout_fen != gross_payout
        or portfolio_settlement.ending_capital_fen != ending_capital
        or portfolio_settlement.profit_loss_fen != ending_capital - portfolio.budget_fen
        or portfolio_settlement.settled_at_utc != plan.evaluation_as_of_at_utc
        or portfolio_settlement.supersedes_portfolio_settlement_id is not None
        or any(
            item.settlement_policy_version
            != portfolio_settlement.settlement_policy_version
            for item in settlements
        )
    ):
        raise ValueError("portfolio settlement contradicts frozen replay artifacts")
    return tuple(settlements)


def compare_backtests(
    left: WalkForwardBacktestResult,
    right: WalkForwardBacktestResult,
) -> BacktestComparisonResult:
    if {left.request.fusion_policy, right.request.fusion_policy} != set(
        SUPPORTED_BACKTEST_POLICIES
    ):
        raise ValueError(
            "backtest comparison requires QUANT_ONLY_V1 and MARKET_QUANT_BLEND_V1"
        )
    mismatches: list[str] = []
    checks = (
        (
            "backtest version",
            left.request.backtest_version,
            right.request.backtest_version,
        ),
        ("data mode", left.request.data_mode, right.request.data_mode),
        (
            "code revision",
            left.backtest_run.code_revision,
            right.backtest_run.code_revision,
        ),
        (
            "archive provenance",
            left.backtest_run.archive_provenance,
            right.backtest_run.archive_provenance,
        ),
        ("slate plans/window", left.request.slates, right.request.slates),
        (
            "expected decision match IDs",
            tuple(plan.match_ids for plan in left.request.slates),
            tuple(plan.match_ids for plan in right.request.slates),
        ),
        (
            "expected slice structure",
            _comparison_slice_structure(left),
            _comparison_slice_structure(right),
        ),
        ("budget", left.request.budget_fen, right.request.budget_fen),
        (
            "selection EV threshold",
            left.request.min_selection_ev,
            right.request.min_selection_ev,
        ),
        (
            "ticket ROI threshold",
            left.request.min_ticket_roi,
            right.request.min_ticket_roi,
        ),
        ("portfolio constraints", left.request.constraints, right.request.constraints),
        (
            "metrics configuration",
            left.request.metrics_config,
            right.request.metrics_config,
        ),
        (
            "decision input manifests",
            tuple(
                item.analysis_artifacts.analysis_run.input_manifest_hash
                for item in left.slate_results
            ),
            tuple(
                item.analysis_artifacts.analysis_run.input_manifest_hash
                for item in right.slate_results
            ),
        ),
        (
            "decision input manifest hashes",
            tuple(
                item.backtest_slice.decision_input_manifest_hash
                for item in left.slate_results
            ),
            tuple(
                item.backtest_slice.decision_input_manifest_hash
                for item in right.slate_results
            ),
        ),
        (
            "evaluation result versions",
            tuple(
                tuple(
                    result.match_result_id for result in item.match_result_batch.results
                )
                for item in left.slate_results
            ),
            tuple(
                tuple(
                    result.match_result_id for result in item.match_result_batch.results
                )
                for item in right.slate_results
            ),
        ),
        (
            "match result IDs",
            tuple(item.backtest_slice.match_result_ids for item in left.slate_results),
            tuple(item.backtest_slice.match_result_ids for item in right.slate_results),
        ),
        (
            "MatchSettlementIssue lineage",
            tuple(
                (
                    item.match_result_batch.issues,
                    item.backtest_slice.match_result_issues,
                )
                for item in left.slate_results
            ),
            tuple(
                (
                    item.match_result_batch.issues,
                    item.backtest_slice.match_result_issues,
                )
                for item in right.slate_results
            ),
        ),
        (
            "missing decision match IDs",
            tuple(
                item.backtest_slice.missing_decision_match_ids
                for item in left.slate_results
            ),
            tuple(
                item.backtest_slice.missing_decision_match_ids
                for item in right.slate_results
            ),
        ),
    )
    for label, left_value, right_value in checks:
        if left_value != right_value:
            mismatches.append(label)
    if mismatches:
        raise ValueError(
            "backtest comparison requires identical " + ", ".join(mismatches)
        )
    left = validate_walk_forward_backtest_result(left)
    right = validate_walk_forward_backtest_result(right)
    return BacktestComparisonResult(
        left=BacktestComparisonSide(
            fusion_policy=left.request.fusion_policy,
            backtest_run_id=left.backtest_run.backtest_run_id,
            metrics=left.metrics,
        ),
        right=BacktestComparisonSide(
            fusion_policy=right.request.fusion_policy,
            backtest_run_id=right.backtest_run.backtest_run_id,
            metrics=right.metrics,
        ),
    )


compare_backtest_results = compare_backtests


def _comparison_slice_structure(
    result: WalkForwardBacktestResult,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.plan.decision_as_of_at_utc,
            item.plan.evaluation_as_of_at_utc,
            item.plan.kickoff_from_utc,
            item.plan.kickoff_to_utc,
            item.plan.match_ids,
            item.backtest_slice.kickoff_from_utc,
            item.backtest_slice.kickoff_to_utc,
            item.backtest_slice.expected_match_ids,
            item.backtest_slice.match_count,
            item.backtest_slice.settled_match_count,
            item.backtest_slice.settled_ticket_count,
            item.backtest_slice.unsettled_ticket_count,
            item.backtest_slice.coverage,
            item.backtest_slice.missing_decision_match_ids,
            item.backtest_slice.match_result_issues,
            tuple(match.match_id for match in item.analysis_artifacts.matches),
        )
        for item in result.slate_results
    )


def _strategy_snapshot(
    request: WalkForwardBacktestRequest,
) -> BacktestStrategySnapshot:
    return BacktestStrategySnapshot.from_config(
        request.fusion_policy.value,
        {
            "budget_fen": request.budget_fen,
            "quant_weight": request.quant_weight,
            "min_selection_ev": request.min_selection_ev,
            "min_ticket_roi": request.min_ticket_roi,
            "portfolio_constraints": request.constraints,
            "slate_policy": EXPLICIT_CHRONOLOGICAL_SLATE_POLICY,
        },
    )


def _analysis_run_id(
    request: WalkForwardBacktestRequest,
    slate_no: int,
    plan: BacktestSlatePlan,
) -> str:
    return stable_id(
        "walk-forward-analysis",
        request.backtest_run_id,
        slate_no,
        plan.decision_as_of_at_utc.isoformat(),
        plan.kickoff_from_utc.isoformat(),
        plan.kickoff_to_utc.isoformat(),
        *plan.match_ids,
    )


def _slice_id(
    request: WalkForwardBacktestRequest,
    slate_no: int,
    plan: BacktestSlatePlan,
) -> str:
    return stable_id(
        "walk-forward-slice",
        request.backtest_run_id,
        slate_no,
        plan.decision_as_of_at_utc.isoformat(),
        plan.evaluation_as_of_at_utc.isoformat(),
        *plan.match_ids,
    )


def _validate_frozen_decision(
    artifacts: AnalysisArtifacts,
    request: WalkForwardBacktestRequest,
    plan: BacktestSlatePlan,
    analysis_run_id: str,
    execution_time_utc: UtcDateTime,
) -> None:
    analysis_run = artifacts.analysis_run
    if (
        analysis_run.analysis_run_id != analysis_run_id
        or analysis_run.as_of_at_utc != plan.decision_as_of_at_utc
    ):
        raise ValueError("AnalysisRun does not match the planned decision cutoff")
    if analysis_run.status != AnalysisRunStatus.COMPLETED:
        raise ValueError("AnalysisRun must be completed before evaluation")
    if (
        analysis_run.run_kind != "MVP_ANALYSIS"
        or analysis_run.replay_of_run_id is not None
    ):
        raise ValueError("walk-forward V1 supports only base AnalysisRun artifacts")
    if (
        analysis_run.started_at_utc != execution_time_utc
        or analysis_run.completed_at_utc != execution_time_utc
    ):
        raise ValueError("AnalysisRun does not use the frozen execution time")
    _validate_analysis_input_manifest(artifacts)
    if sha256_text(analysis_run.config_json) != analysis_run.config_hash:
        raise ValueError("AnalysisRun configuration hash is inconsistent")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON value is not allowed: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        values: dict[str, object] = {}
        for key, value in pairs:
            if key in values:
                raise ValueError(f"duplicate AnalysisRun configuration key: {key}")
            values[key] = value
        return values

    try:
        config = json.loads(
            analysis_run.config_json,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("AnalysisRun request configuration is invalid") from error
    analysis_request_config = (
        config.get("request") if isinstance(config, dict) else None
    )
    settings_config = config.get("settings") if isinstance(config, dict) else None
    try:
        stored_settings = AppSettings.model_validate(settings_config)
    except ValueError as error:
        raise ValueError("AnalysisRun settings configuration is invalid") from error
    expected_config_keys = {
        "fusion_policy",
        "budgets_fen",
        "min_selection_ev",
        "min_ticket_roi",
        "allow_partial_inputs",
        "expected_match_ids",
    }
    actual_config_keys = (
        set(analysis_request_config)
        if isinstance(analysis_request_config, dict)
        else set()
    )
    if not isinstance(analysis_request_config, dict) or actual_config_keys not in (
        expected_config_keys,
        {*expected_config_keys, "provider_runtime_provenance"},
    ):
        raise ValueError("AnalysisRun request configuration is invalid")
    has_provider_provenance = "provider_runtime_provenance" in analysis_request_config
    requires_provider_provenance = (
        stored_settings.runtime.environment is not RuntimeEnvironment.MOCK
    )
    if has_provider_provenance is not requires_provider_provenance:
        raise ValueError("AnalysisRun provider runtime provenance is invalid")
    stored_provider_provenance: dict[str, RuntimeProvenance] | None = None
    if has_provider_provenance:
        stored_provenance = analysis_request_config["provider_runtime_provenance"]
        expected_roles = {"fixture", "market_odds", "sporttery", "manual_quant"}
        if (
            not isinstance(stored_provenance, dict)
            or set(stored_provenance) != expected_roles
        ):
            raise ValueError("AnalysisRun provider runtime provenance is invalid")
        try:
            stored_provider_provenance = {
                role: RuntimeProvenance.model_validate(stored_provenance[role])
                for role in sorted(expected_roles)
            }
            runtime = (
                RuntimeEnvironment.RESEARCH
                if request.data_mode is BacktestDataMode.SOURCE_TIME_RESEARCH
                else RuntimeEnvironment.LIVE
            )
            if stored_settings.runtime.environment is not runtime:
                raise ValueError("stored runtime does not match backtest data mode")
            RuntimeEnvironmentGuard(runtime).validate(
                stored_provider_provenance.values()
            )
        except ValueError as error:
            raise ValueError(
                "AnalysisRun provider runtime provenance is invalid"
            ) from error
        if any(
            item.data_mode is not request.data_mode
            for item in stored_provider_provenance.values()
        ):
            raise ValueError("AnalysisRun provider runtime provenance is invalid")
    if stored_settings.backtest.data_mode is not request.data_mode:
        raise ValueError("AnalysisRun settings use another backtest data mode")
    fusion_policy = analysis_request_config["fusion_policy"]
    budgets = analysis_request_config["budgets_fen"]
    min_selection_ev = analysis_request_config["min_selection_ev"]
    min_ticket_roi = analysis_request_config["min_ticket_roi"]
    partial_inputs = analysis_request_config["allow_partial_inputs"]
    config_match_ids = analysis_request_config["expected_match_ids"]
    if (
        type(fusion_policy) is not str
        or not isinstance(budgets, list)
        or len(budgets) != 1
        or type(budgets[0]) is not int
        or type(min_selection_ev) is not str
        or type(min_ticket_roi) is not str
        or type(partial_inputs) is not bool
        or not isinstance(config_match_ids, list)
        or any(
            type(match_id) is not str
            or not match_id
            or match_id != match_id.strip()
            or len(match_id) > 160
            for match_id in config_match_ids
        )
        or len(config_match_ids) != len(set(config_match_ids))
    ):
        raise ValueError("AnalysisRun request configuration has invalid types")
    try:
        stored_min_selection_ev = Decimal(min_selection_ev)
        stored_min_ticket_roi = Decimal(min_ticket_roi)
    except InvalidOperation as error:
        raise ValueError("AnalysisRun request thresholds are invalid") from error
    if not stored_min_selection_ev.is_finite() or not stored_min_ticket_roi.is_finite():
        raise ValueError("AnalysisRun request thresholds are invalid")
    expected_config_match_ids = tuple(config_match_ids)
    if (
        fusion_policy != request.fusion_policy.value
        or tuple(budgets) != (request.budget_fen,)
        or stored_min_selection_ev != request.min_selection_ev
        or stored_min_ticket_roi != request.min_ticket_roi
        or partial_inputs is not True
        or expected_config_match_ids != plan.match_ids
    ):
        raise ValueError("AnalysisRun decision scope is inconsistent with the slate")

    cutoff = plan.decision_as_of_at_utc
    point_in_time_items = (
        *artifacts.matches,
        *artifacts.provider_mappings,
        *artifacts.manual_quant_inputs,
    )
    if any(item.available_at_utc > cutoff for item in point_in_time_items):
        raise ValueError("frozen analysis contains an input after the decision cutoff")
    if any(
        timestamp > cutoff
        for snapshot in (
            *artifacts.market_odds_snapshots,
            *artifacts.sporttery_bonus_snapshots,
        )
        for timestamp in _backtest_snapshot_timestamps(snapshot)
    ):
        raise ValueError("frozen analysis contains future odds or bonus snapshots")
    if request.data_mode is BacktestDataMode.SOURCE_TIME_RESEARCH and any(
        snapshot.ingested_at_utc != snapshot.available_at_utc
        for snapshot in (
            *artifacts.market_odds_snapshots,
            *artifacts.sporttery_bonus_snapshots,
        )
    ):
        raise ValueError(
            "source-time frozen snapshots must use the source-time ingestion boundary"
        )
    if any(
        not (
            snapshot.captured_at_utc
            <= snapshot.available_at_utc
            <= snapshot.ingested_at_utc
        )
        for snapshot in (
            *artifacts.market_odds_snapshots,
            *artifacts.sporttery_bonus_snapshots,
        )
    ):
        raise ValueError(
            "frozen snapshot timestamps must follow captured, available, ingested"
        )
    if any(
        not (plan.kickoff_from_utc <= match.kickoff_at_utc <= plan.kickoff_to_utc)
        for match in artifacts.matches
    ):
        raise ValueError("AnalysisRun contains a match outside the slate window")
    analyzed_match_ids = tuple(match.match_id for match in artifacts.matches)
    if len(analyzed_match_ids) != len(set(analyzed_match_ids)):
        raise ValueError("AnalysisRun match IDs must be unique")
    mapping_ids = tuple(mapping.mapping_id for mapping in artifacts.provider_mappings)
    if len(mapping_ids) != len(set(mapping_ids)):
        raise ValueError("AnalysisRun provider mapping IDs must be unique")
    mapped_sources = {
        (mapping.provider_code, mapping.internal_match_id)
        for mapping in artifacts.provider_mappings
    }
    mapped_match_ids = {
        mapping.internal_match_id for mapping in artifacts.provider_mappings
    }
    if any(match_id not in mapped_match_ids for match_id in analyzed_match_ids):
        raise ValueError("AnalysisRun match is missing its provider mapping")
    if any(
        (snapshot.provider_code, snapshot.match_id) not in mapped_sources
        for snapshot in (
            *artifacts.market_odds_snapshots,
            *artifacts.sporttery_bonus_snapshots,
        )
    ):
        raise ValueError("AnalysisRun snapshot is missing its provider mapping")
    if stored_provider_provenance is not None:
        expected_codes = {
            role: item.provider_code
            for role, item in stored_provider_provenance.items()
        }
        allowed_mapping_codes = {
            expected_codes["fixture"],
            expected_codes["market_odds"],
            expected_codes["sporttery"],
        }
        if any(
            mapping.provider_code not in allowed_mapping_codes
            for mapping in artifacts.provider_mappings
        ):
            raise ValueError("AnalysisRun provider mapping provenance is invalid")
        if (
            any(
                (expected_codes["fixture"], match_id) not in mapped_sources
                for match_id in analyzed_match_ids
            )
            or any(
                snapshot.provider_code != expected_codes["market_odds"]
                for snapshot in artifacts.market_odds_snapshots
            )
            or any(
                snapshot.provider_code != expected_codes["sporttery"]
                for snapshot in artifacts.sporttery_bonus_snapshots
            )
        ):
            raise ValueError("AnalysisRun frozen input provenance is invalid")
    if plan.match_ids:
        analyzed = set(analyzed_match_ids)
        expected = set(plan.match_ids)
        if any(match_id not in expected for match_id in analyzed_match_ids):
            raise ValueError("AnalysisRun contains a match outside the expected slate")
        if analyzed_match_ids != tuple(
            match_id for match_id in plan.match_ids if match_id in analyzed
        ):
            raise ValueError("AnalysisRun match order is inconsistent with the slate")

    expected_matches = set(analyzed_match_ids)
    for predictions, label in (
        (artifacts.market_predictions, "market predictions"),
        (artifacts.quant_predictions, "quant predictions"),
        (artifacts.final_predictions, "final predictions"),
    ):
        if set(_unique_by_match(predictions, label)) != expected_matches:
            raise ValueError(f"frozen {label} do not cover every slate match")
    if any(
        prediction.fusion_policy != request.fusion_policy
        for prediction in artifacts.final_predictions
    ):
        raise ValueError("frozen final predictions use another fusion policy")
    if request.fusion_policy is FusionPolicyName.MARKET_QUANT_BLEND_V1:
        for prediction in artifacts.final_predictions:
            config = json.loads(prediction.fusion_config_json)
            try:
                stored_weight = Decimal(config["quant_weight"])
            except (InvalidOperation, KeyError, TypeError) as error:
                raise ValueError(
                    "frozen blend prediction has an invalid fusion configuration"
                ) from error
            if stored_weight != request.quant_weight:
                raise ValueError("frozen blend prediction uses another quant weight")
    if len(artifacts.portfolios) != 1:
        raise ValueError("walk-forward V1 requires exactly one budget portfolio")
    portfolio = artifacts.portfolios[0]
    if portfolio.budget_fen != request.budget_fen:
        raise ValueError("frozen portfolio does not match the strategy budget")
    if portfolio.constraints != request.constraints:
        raise ValueError("frozen portfolio does not match the strategy constraints")


def _backtest_snapshot_timestamps(
    snapshot: MarketOddsSnapshot | SportteryBonusSnapshot,
) -> tuple[UtcDateTime, ...]:
    return (
        snapshot.captured_at_utc,
        snapshot.available_at_utc,
        snapshot.ingested_at_utc,
    )


def _validate_evaluation_batch(
    batch: MatchResultBatch,
    match_ids: tuple[str, ...],
    plan: BacktestSlatePlan,
    data_mode: BacktestDataMode,
    runtime_provenance: RuntimeProvenance | None = None,
) -> None:
    cutoff = plan.evaluation_as_of_at_utc
    if batch.as_of_at_utc != cutoff:
        raise ValueError("match result provider used a different evaluation cutoff")
    if runtime_provenance is not None:
        expected_provider_code = runtime_provenance.provider_code
        if any(
            result.provider_code != expected_provider_code for result in batch.results
        ) or any(
            mapping.provider_code != expected_provider_code
            for mapping in batch.mappings
        ):
            raise ProviderRuntimeProvenanceMismatchError(
                "match result provider emitted data outside declared provider provenance"
            )
    expected = set(match_ids)
    if any(result.match_id not in expected for result in batch.results):
        raise ValueError("match result provider returned a match outside the slate")
    issue_match_ids = tuple(issue.match_id for issue in batch.issues)
    if len(issue_match_ids) != len(set(issue_match_ids)):
        raise ValueError("match result provider returned duplicate issues")
    if any(match_id not in expected for match_id in issue_match_ids):
        raise ValueError("match result provider returned an issue outside the slate")
    if {result.match_id for result in batch.results} & set(issue_match_ids):
        raise ValueError("match result provider returned both result and issue")
    if any(mapping.internal_match_id not in expected for mapping in batch.mappings):
        raise ValueError("match result provider returned a mapping outside the slate")
    mapped_matches = {mapping.internal_match_id for mapping in batch.mappings}
    if any(match_id not in mapped_matches for match_id in issue_match_ids):
        raise ValueError("match result provider returned an unmapped issue")
    if any(
        result.available_at_utc > cutoff or result.ingested_at_utc > cutoff
        for result in batch.results
    ) or any(mapping.available_at_utc > cutoff for mapping in batch.mappings):
        raise ValueError(
            "match result provider returned data after the evaluation cutoff"
        )
    if data_mode is BacktestDataMode.SOURCE_TIME_RESEARCH and any(
        result.ingested_at_utc != result.available_at_utc for result in batch.results
    ):
        raise ValueError(
            "source-time match results must use the source-time ingestion boundary"
        )


def _unique_by_match(items: tuple, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        if item.match_id in result:
            raise ValueError(
                f"frozen analysis has multiple {label} for {item.match_id}"
            )
        result[item.match_id] = item
    return result


def _validate_analysis_input_manifest(artifacts: AnalysisArtifacts) -> None:
    analysis_run = artifacts.analysis_run
    if (
        sha256_text(analysis_run.input_manifest_json)
        != analysis_run.input_manifest_hash
    ):
        raise ValueError("AnalysisRun input manifest hash is inconsistent")
    expected_manifest_json = build_input_manifest_json(
        artifacts.competitions,
        artifacts.teams,
        artifacts.matches,
        artifacts.provider_mappings,
        artifacts.market_odds_snapshots,
        artifacts.sporttery_bonus_snapshots,
        artifacts.manual_quant_inputs,
    )
    if analysis_run.input_manifest_json != expected_manifest_json:
        raise ValueError("AnalysisRun input manifest does not match frozen inputs")


def _risk_report_for(
    artifacts: AnalysisArtifacts,
    portfolio: Portfolio,
) -> PortfolioRiskReport:
    reports = tuple(
        report
        for report in artifacts.portfolio_risk_reports
        if report.portfolio_id == portfolio.portfolio_id
    )
    if len(reports) != 1:
        raise ValueError("frozen portfolio requires exactly one risk report")
    return reports[0]


def _ticket_odds(ticket: TicketAllocation) -> Decimal:
    odds = Decimal(1)
    for leg in ticket.candidate.legs:
        odds *= leg.fixed_bonus
    return odds


def _realized_top_exposure_loss(
    portfolio: Portfolio,
    risk_report: PortfolioRiskReport,
    result_by_match: dict[str, MatchResult],
    settled_by_ticket: dict[str, Settlement],
    exposure_count: int,
) -> int:
    top_match_ids = {
        item.match_id
        for item in sorted(
            risk_report.match_exposures,
            key=lambda item: (-item.exposed_stake_fen, item.match_id),
        )[:exposure_count]
    }
    failed_ticket_ids: set[str] = set()
    for ticket in portfolio.tickets:
        settlement = settled_by_ticket.get(ticket.ticket_id)
        if settlement is None or settlement.status != SettlementStatus.LOST:
            continue
        if any(
            leg.match_id in top_match_ids
            and leg.match_id in result_by_match
            and result_by_match[leg.match_id].three_way_selection() != leg.selection
            for leg in ticket.candidate.legs
        ):
            failed_ticket_ids.add(ticket.ticket_id)
    return sum(
        ticket.stake_fen
        for ticket in portfolio.tickets
        if ticket.ticket_id in failed_ticket_ids
    )
