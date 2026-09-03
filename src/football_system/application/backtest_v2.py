from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from pydantic import Field, model_validator

from football_system.application.backtest import (
    SUPPORTED_BACKTEST_POLICIES,
    _realized_top_exposure_loss,
    _risk_report_for,
    _ticket_odds,
    _validate_evaluation_batch,
    _validate_settlement_artifacts,
)
from football_system.application.environment import (
    CrossEnvironmentInputError,
    RuntimeDataModeError,
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    RuntimeProvenance,
    is_mock_provider_code,
    require_provider_runtime_provenance,
)
from football_system.application.model_analysis import (
    ModelAnalysisDecision,
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.models import AnalysisArtifacts
from football_system.application.ports.data_providers import (
    ArchivedHistoricalDataProvider,
    ArchivedMatchResultBatch,
    MatchResultQuery,
)
from football_system.application.quant_model import (
    MVP_INPUT_MANIFEST_V3,
    build_model_input_manifest_json,
)
from football_system.application.settlement import SettlementService
from football_system.domain.analysis import AnalysisRunStatus
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestDataMode,
    BacktestMetricsConfig,
    BacktestRun,
    BacktestRunStatus,
    BacktestSlateSnapshot,
    BacktestStrategySnapshot,
    canonical_archive_provenance,
    sha256_text,
)
from football_system.domain.backtest_v2 import (
    BACKTEST_V2,
    BacktestV2DecisionSnapshot,
    BacktestV2MatchSnapshot,
    BacktestV2Metrics,
    BacktestV2ModelEvaluationRef,
    BacktestV2Slice,
    BacktestV2TrainingSourceRef,
)
from football_system.domain.betting import (
    PortfolioConstraints,
    PortfolioStatus,
)
from football_system.domain.common import DomainModel, Identifier, UtcDateTime, stable_id, utc_now
from football_system.domain.prediction import (
    FusionPolicyName,
    ModelQuantPrediction,
)
from football_system.domain.services.backtest_v2_metrics import (
    calculate_backtest_v2_metrics,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloTrainingFact,
)
from football_system.domain.settlement import (
    MatchResult,
    PortfolioSettlement,
    PortfolioSettlementResult,
    Settlement,
    SettlementScope,
    SettlementStatus,
)

EXPLICIT_CHRONOLOGICAL_SLATE_POLICY_V2 = "DAILY_FIXED_CUTOFF_V2"


class BacktestV2SlatePlan(DomainModel):
    competition_id: Identifier
    season_id: Identifier
    decision_as_of_at_utc: UtcDateTime
    evaluation_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    match_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan(self) -> BacktestV2SlatePlan:
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("BACKTEST_V2 kickoff window is invalid")
        if self.decision_as_of_at_utc > self.kickoff_from_utc:
            raise ValueError("BACKTEST_V2 decision cutoff follows kickoff")
        if self.evaluation_as_of_at_utc <= self.kickoff_to_utc:
            raise ValueError("BACKTEST_V2 evaluation cutoff must follow kickoff")
        if len(self.match_ids) != len(set(self.match_ids)):
            raise ValueError("BACKTEST_V2 slate match IDs must be unique")
        return self


class WalkForwardBacktestV2Request(DomainModel):
    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    fusion_policy: FusionPolicyName
    slates: tuple[BacktestV2SlatePlan, ...] = Field(min_length=1)
    budget_fen: int = Field(ge=0)
    quant_weight: Decimal = Field(ge=0, le=1)
    min_selection_ev: Decimal = Field(ge=0)
    min_ticket_roi: Decimal = Field(ge=0)
    constraints: PortfolioConstraints
    elo_config: EloBaselineConfig
    backtest_version: str = BACKTEST_V2
    metrics_config: BacktestMetricsConfig = Field(default_factory=BacktestMetricsConfig)
    archive_provenance: tuple[BacktestArchiveProvenance, ...] = ()
    execution_time_utc: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_request(self) -> WalkForwardBacktestV2Request:
        if self.backtest_version != BACKTEST_V2:
            raise ValueError("unexpected walk-forward backtest version")
        if self.fusion_policy not in SUPPORTED_BACKTEST_POLICIES:
            raise ValueError("BACKTEST_V2 supports only deterministic quant fusion")
        for previous, current in zip(self.slates, self.slates[1:]):
            if current.decision_as_of_at_utc <= previous.evaluation_as_of_at_utc:
                raise ValueError("BACKTEST_V2 slates must be chronological and disjoint")
        all_match_ids = tuple(
            match_id for slate in self.slates for match_id in slate.match_ids
        )
        if len(all_match_ids) != len(set(all_match_ids)):
            raise ValueError("BACKTEST_V2 target matches cannot repeat across slates")
        object.__setattr__(
            self,
            "archive_provenance",
            canonical_archive_provenance(self.archive_provenance),
        )
        if (
            self.execution_time_utc is not None
            and self.execution_time_utc < self.slates[-1].evaluation_as_of_at_utc
        ):
            raise ValueError("BACKTEST_V2 execution precedes final evaluation cutoff")
        return self


class WalkForwardBacktestV2SlateResult(DomainModel):
    plan: BacktestV2SlatePlan
    model_decision: ModelAnalysisDecision
    match_result_batch: ArchivedMatchResultBatch
    portfolio_settlement_result: PortfolioSettlementResult
    backtest_slice: BacktestV2Slice
    slate_snapshot: BacktestSlateSnapshot
    ticket_settlements: tuple[Settlement, ...]
    portfolio_settlement: PortfolioSettlement | None = None


class WalkForwardBacktestV2Result(DomainModel):
    request: WalkForwardBacktestV2Request
    backtest_run: BacktestRun
    slate_results: tuple[WalkForwardBacktestV2SlateResult, ...]
    metrics: BacktestV2Metrics

    @model_validator(mode="after")
    def validate_graph(self) -> WalkForwardBacktestV2Result:
        _validate_result_graph(self)
        return self

    @property
    def analysis_artifacts(self) -> tuple[AnalysisArtifacts, ...]:
        return tuple(item.model_decision.analysis_artifacts for item in self.slate_results)

    @property
    def backtest_slices(self) -> tuple[BacktestV2Slice, ...]:
        return tuple(item.backtest_slice for item in self.slate_results)

    @property
    def slate_snapshots(self) -> tuple[BacktestSlateSnapshot, ...]:
        return tuple(item.slate_snapshot for item in self.slate_results)


class WalkForwardBacktestV2Service:
    def __init__(
        self,
        model_analysis_service: RunModelAnalysisService,
        historical_data_provider: ArchivedHistoricalDataProvider,
        settlement_service: SettlementService | None = None,
    ) -> None:
        self._model_analysis_service = model_analysis_service
        self._historical_data_provider = historical_data_provider
        self._settlement_service = settlement_service or SettlementService()

    async def run(
        self,
        request: WalkForwardBacktestV2Request,
    ) -> WalkForwardBacktestV2Result:
        request = WalkForwardBacktestV2Request.model_validate(
            request.model_dump(mode="python", exclude_computed_fields=True)
        )
        runtime_provenance = {
            **self._model_analysis_service.declared_provider_runtime_provenance(),
            "match_result": require_provider_runtime_provenance(
                self._historical_data_provider,
                "match_result",
            ),
        }
        validate_backtest_v2_runtime_provenance(
            request.data_mode,
            runtime_provenance,
        )
        execution_time = request.execution_time_utc or utc_now()
        if execution_time < request.slates[-1].evaluation_as_of_at_utc:
            raise ValueError("BACKTEST_V2 execution precedes final evaluation cutoff")
        expected_slice_ids = tuple(
            _slice_id(request, index, plan)
            for index, plan in enumerate(request.slates, start=1)
        )
        slate_results: list[WalkForwardBacktestV2SlateResult] = []
        for index, plan in enumerate(request.slates, start=1):
            analysis_run_id = _analysis_run_id(request, index, plan)
            model_decision = await self._model_analysis_service.run_decision(
                RunModelAnalysisRequest(
                    as_of_at_utc=plan.decision_as_of_at_utc,
                    kickoff_from_utc=plan.kickoff_from_utc,
                    kickoff_to_utc=plan.kickoff_to_utc,
                    budgets_fen=(request.budget_fen,),
                    fusion_policy=request.fusion_policy,
                    min_selection_ev=request.min_selection_ev,
                    min_ticket_roi=request.min_ticket_roi,
                    analysis_run_id=analysis_run_id,
                    execution_time_utc=execution_time,
                    allow_partial_inputs=True,
                    expected_match_ids=plan.match_ids,
                    competition_id=plan.competition_id,
                    season_id=plan.season_id,
                    elo_config=request.elo_config,
                    quant_weight=request.quant_weight,
                    constraints=request.constraints,
                )
            )
            decision_snapshot = _freeze_decision_snapshot(
                request,
                plan,
                expected_slice_ids[index - 1],
                model_decision,
                execution_time,
            )
            analyzed_match_ids = decision_snapshot.analyzed_match_ids
            if not analyzed_match_ids:
                raise ValueError("BACKTEST_V2 slice has no decision-ready matches")

            archived_results = await self._historical_data_provider.fetch_archived_match_results(
                MatchResultQuery(
                    match_ids=analyzed_match_ids,
                    as_of_at_utc=plan.evaluation_as_of_at_utc,
                )
            )
            archived_results = ArchivedMatchResultBatch.model_validate(
                archived_results.model_dump(
                    mode="python",
                    exclude_computed_fields=True,
                )
            )
            _validate_archived_results(
                archived_results,
                analyzed_match_ids,
                plan,
                request,
                runtime_provenance["match_result"],
            )
            artifacts = model_decision.analysis_artifacts
            portfolio = artifacts.portfolios[0]
            portfolio_match_ids = {
                leg.match_id
                for ticket in portfolio.tickets
                for leg in ticket.candidate.legs
            }
            plain_batch = archived_results.to_match_result_batch()
            settlement_result = self._settlement_service.settle_portfolio(
                SettlementScope.for_analysis_run(artifacts.analysis_run.analysis_run_id),
                portfolio,
                (
                    result
                    for result in plain_batch.results
                    if result.match_id in portfolio_match_ids
                ),
                plan.evaluation_as_of_at_utc,
                result_issues=(
                    issue
                    for issue in plain_batch.issues
                    if issue.match_id in portfolio_match_ids
                ),
            )
            outputs = _build_slate_outputs(
                request=request,
                plan=plan,
                decision=model_decision,
                decision_snapshot=decision_snapshot,
                archived_results=archived_results,
                settlement_result=settlement_result,
            )
            slate_results.append(outputs)

        code_revisions = {
            item.model_decision.analysis_artifacts.analysis_run.code_revision
            for item in slate_results
        }
        if len(code_revisions) != 1:
            raise ValueError("BACKTEST_V2 slates used different code revisions")
        backtest_run = BacktestRun(
            backtest_run_id=request.backtest_run_id,
            backtest_version=BACKTEST_V2,
            data_mode=request.data_mode,
            date_from=request.slates[0].kickoff_from_utc.date(),
            date_to=request.slates[-1].kickoff_to_utc.date(),
            strategy_snapshot=_strategy_snapshot(request),
            code_revision=next(iter(code_revisions)),
            created_at_utc=execution_time,
            status=BacktestRunStatus.COMPLETED,
            archive_provenance=request.archive_provenance,
            expected_slice_ids=expected_slice_ids,
        )
        metrics = calculate_backtest_v2_metrics(
            backtest_run,
            (item.backtest_slice for item in slate_results),
            (item.slate_snapshot for item in slate_results),
            request.metrics_config,
        )
        return validate_walk_forward_backtest_v2_result(
            WalkForwardBacktestV2Result(
                request=request,
                backtest_run=backtest_run,
                slate_results=tuple(slate_results),
                metrics=metrics,
            )
        )


def validate_backtest_v2_runtime_provenance(
    data_mode: BacktestDataMode,
    provenance_by_role: Mapping[str, RuntimeProvenance],
) -> None:
    expected_roles = {
        "fixture",
        "market_odds",
        "sporttery",
        "model_training",
        "match_result",
    }
    if set(provenance_by_role) != expected_roles:
        raise ValueError("BACKTEST_V2 requires provenance for every provider role")
    provenance = tuple(provenance_by_role.values())
    if any(item.data_mode is not data_mode for item in provenance):
        raise RuntimeDataModeError("BACKTEST_V2 provider data modes do not match")
    mock_flags = tuple(
        item.is_mock or is_mock_provider_code(item.provider_code) for item in provenance
    )
    if any(mock_flags) and not all(mock_flags):
        raise CrossEnvironmentInputError("BACKTEST_V2 cannot mix mock and real providers")
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
                "BACKTEST_V2 mock providers use an incompatible runtime"
            )
        return
    RuntimeEnvironmentGuard(expected_environment).validate(provenance)


def validate_walk_forward_backtest_v2_result(
    result: WalkForwardBacktestV2Result,
) -> WalkForwardBacktestV2Result:
    return WalkForwardBacktestV2Result.model_validate(
        result.model_dump(mode="python", exclude_computed_fields=True)
    )


def _freeze_decision_snapshot(
    request: WalkForwardBacktestV2Request,
    plan: BacktestV2SlatePlan,
    slice_id: str,
    decision: ModelAnalysisDecision,
    execution_time: UtcDateTime,
) -> BacktestV2DecisionSnapshot:
    artifacts = AnalysisArtifacts.model_validate(
        decision.analysis_artifacts.model_dump(
            mode="python",
            exclude_computed_fields=True,
        )
    )
    run = artifacts.analysis_run
    if (
        run.status is not AnalysisRunStatus.COMPLETED
        or run.as_of_at_utc != plan.decision_as_of_at_utc
        or run.started_at_utc != execution_time
        or run.completed_at_utc != execution_time
        or run.input_manifest_version != MVP_INPUT_MANIFEST_V3
    ):
        raise ValueError("BACKTEST_V2 requires a frozen manifest-V3 AnalysisRun")
    expected_manifest = build_model_input_manifest_json(
        competitions=artifacts.competitions,
        teams=artifacts.teams,
        matches=artifacts.matches,
        mappings=artifacts.provider_mappings,
        market_snapshots=artifacts.market_odds_snapshots,
        sporttery_snapshots=artifacts.sporttery_bonus_snapshots,
        model_states=artifacts.quant_model_states,
    )
    if (
        expected_manifest != run.input_manifest_json
        or sha256_text(expected_manifest) != run.input_manifest_hash
    ):
        raise ValueError("BACKTEST_V2 model input manifest is inconsistent")
    if len(artifacts.quant_model_states) != 1:
        raise ValueError("BACKTEST_V2 requires exactly one quant model state")
    if len(artifacts.portfolios) != 1:
        raise ValueError("BACKTEST_V2 requires exactly one portfolio")
    portfolio = artifacts.portfolios[0]
    if portfolio.budget_fen != request.budget_fen or portfolio.constraints != request.constraints:
        raise ValueError("BACKTEST_V2 portfolio contradicts its request")
    analyzed_match_ids = tuple(match.match_id for match in artifacts.matches)
    analyzed_set = set(analyzed_match_ids)
    if analyzed_match_ids != tuple(
        match_id for match_id in plan.match_ids if match_id in analyzed_set
    ):
        raise ValueError("BACKTEST_V2 analysis match order is inconsistent")
    if any(
        match.competition_id != plan.competition_id
        or not (plan.kickoff_from_utc <= match.kickoff_at_utc <= plan.kickoff_to_utc)
        for match in artifacts.matches
    ):
        raise ValueError("BACKTEST_V2 analysis crosses its competition or window")
    state = artifacts.quant_model_states[0]
    if (
        state.cutoff_at_utc != plan.decision_as_of_at_utc
        or state.season_id != plan.season_id
        or state.config_hash != request.elo_config.config_hash
    ):
        raise ValueError("BACKTEST_V2 model state contradicts its request")
    source_by_result_id = {
        source.result.match_result_id: source
        for source in decision.training_history.sources
    }
    training_sources: list[BacktestV2TrainingSourceRef] = []
    for fact in state.training_facts:
        source = source_by_result_id.get(fact.match_result_id)
        expected_fact = (
            EloTrainingFact.from_result(sequence=fact.sequence, result=source.result)
            if source is not None
            else None
        )
        if source is None or (
            source.result.match_id != fact.match_id
            or source.result.payload_hash != fact.source_payload_hash
            or expected_fact is None
            or expected_fact.fact_hash != fact.fact_hash
        ):
            raise ValueError("BACKTEST_V2 training fact lacks its exact archive source")
        _require_archive(request.archive_provenance, source.archive)
        training_sources.append(
            BacktestV2TrainingSourceRef(
                sequence=fact.sequence,
                match_result_id=fact.match_result_id,
                match_id=fact.match_id,
                source_payload_hash=fact.source_payload_hash,
                fact_hash=fact.fact_hash,
                available_at_utc=source.result.available_at_utc,
                ingested_at_utc=source.result.ingested_at_utc,
                archive_id=source.archive.archive_id,
                archive_schema_version=source.archive.archive_schema_version,
                archive_provider_code=source.archive.provider_code,
                archive_payload_sha256=source.archive.payload_sha256,
            )
        )
    evaluations = {item.match_id: item for item in artifacts.quant_model_evaluations}
    markets = {item.match_id: item for item in artifacts.market_predictions}
    quants = {
        item.match_id: item
        for item in artifacts.quant_predictions
        if isinstance(item, ModelQuantPrediction)
    }
    finals = {item.match_id: item for item in artifacts.final_predictions}
    evaluation_refs: list[BacktestV2ModelEvaluationRef] = []
    for match_id in analyzed_match_ids:
        evaluation = evaluations[match_id]
        market = markets[match_id]
        quant = quants.get(match_id)
        final = finals.get(match_id)
        if final is not None and final.fusion_policy is not request.fusion_policy:
            raise ValueError("BACKTEST_V2 final prediction uses another fusion policy")
        evaluation_refs.append(
            BacktestV2ModelEvaluationRef(
                match_id=match_id,
                quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
                status=evaluation.status,
                unavailable_reason=evaluation.unavailable_reason,
                output_hash=evaluation.output_hash,
                model_prediction_hash=evaluation.model_prediction_hash,
                market_prediction_id=market.prediction_id,
                quant_prediction_id=(quant.prediction_id if quant is not None else None),
                final_prediction_id=(final.prediction_id if final is not None else None),
                p_market=market.probabilities,
                p_quant=(quant.probabilities if quant is not None else None),
                p_final=(final.probabilities if final is not None else None),
            )
        )
    return BacktestV2DecisionSnapshot.freeze(
        backtest_run_id=request.backtest_run_id,
        slice_id=slice_id,
        analysis_run_id=run.analysis_run_id,
        decision_as_of_at_utc=plan.decision_as_of_at_utc,
        decision_input_manifest_hash=run.input_manifest_hash,
        quant_model_state_id=state.quant_model_state_id,
        model_name=state.model_name,
        model_version=state.model_version,
        calibration_label=state.calibration_label,
        model_config_hash=state.config_hash,
        state_hash=state.state_hash,
        state_payload_hash=state.state_payload_hash,
        training_data_hash=state.training_data_hash,
        expected_match_ids=plan.match_ids,
        analyzed_match_ids=analyzed_match_ids,
        missing_decision_match_ids=tuple(
            match_id for match_id in plan.match_ids if match_id not in analyzed_set
        ),
        training_sources=tuple(training_sources),
        evaluations=tuple(evaluation_refs),
    )


def _validate_archived_results(
    archived: ArchivedMatchResultBatch,
    match_ids: tuple[str, ...],
    plan: BacktestV2SlatePlan,
    request: WalkForwardBacktestV2Request,
    runtime_provenance: RuntimeProvenance,
) -> None:
    plain = archived.to_match_result_batch()
    _validate_evaluation_batch(
        plain,
        match_ids,
        plan,
        request.data_mode,
        runtime_provenance,
    )
    source_ids = tuple(source.result.match_result_id for source in archived.sources)
    if source_ids != tuple(result.match_result_id for result in plain.results):
        raise ValueError("BACKTEST_V2 result archive lineage is inconsistent")
    for source in archived.sources:
        if source.archive.provider_code != source.result.provider_code:
            raise ValueError("BACKTEST_V2 result archive provider is inconsistent")
        _require_archive(request.archive_provenance, source.archive)


def _build_slate_outputs(
    *,
    request: WalkForwardBacktestV2Request,
    plan: BacktestV2SlatePlan,
    decision: ModelAnalysisDecision,
    decision_snapshot: BacktestV2DecisionSnapshot,
    archived_results: ArchivedMatchResultBatch,
    settlement_result: PortfolioSettlementResult,
) -> WalkForwardBacktestV2SlateResult:
    artifacts = decision.analysis_artifacts
    plain_batch = archived_results.to_match_result_batch()
    result_by_match = {item.match_id: item for item in plain_batch.results}
    issue_by_match = {item.match_id: item for item in plain_batch.issues}
    source_by_match = {item.result.match_id: item for item in archived_results.sources}
    portfolio = artifacts.portfolios[0]
    ticket_settlements = _validate_settlement_artifacts(
        artifacts,
        portfolio,
        result_by_match,
        issue_by_match,
        settlement_result,
        plan,
    )
    evaluation_by_match = {
        item.match_id: item for item in decision_snapshot.evaluations
    }
    match_snapshots = tuple(
        _match_snapshot(
            request,
            decision_snapshot.slice_id,
            result_by_match[match_id],
            source_by_match[match_id].archive,
            evaluation_by_match[match_id],
        )
        for match_id in decision_snapshot.analyzed_match_ids
        if match_id in result_by_match
    )
    ordered_issues = tuple(
        issue_by_match[match_id]
        for match_id in decision_snapshot.analyzed_match_ids
        if match_id in issue_by_match
    )
    backtest_slice = BacktestV2Slice.freeze(
        slice_id=decision_snapshot.slice_id,
        backtest_run_id=request.backtest_run_id,
        data_mode=request.data_mode,
        decision_as_of_at_utc=plan.decision_as_of_at_utc,
        kickoff_from_utc=plan.kickoff_from_utc,
        kickoff_to_utc=plan.kickoff_to_utc,
        evaluation_as_of_at_utc=plan.evaluation_as_of_at_utc,
        decision_snapshot=decision_snapshot,
        match_snapshots=match_snapshots,
        match_result_issues=ordered_issues,
    )
    settled_by_ticket = {
        item.ticket_id: item for item in ticket_settlements
    }
    settled_stake = sum(item.stake_fen for item in ticket_settlements)
    gross_payout = sum(item.gross_payout_fen for item in ticket_settlements)
    risk = _risk_report_for(artifacts, portfolio)
    slate_snapshot = BacktestSlateSnapshot(
        backtest_run_id=request.backtest_run_id,
        data_mode=request.data_mode,
        slice_id=decision_snapshot.slice_id,
        decision_as_of_at_utc=plan.decision_as_of_at_utc,
        match_count=len(plan.match_ids),
        settled_match_count=len(match_snapshots),
        ticket_count=len(portfolio.tickets),
        settled_ticket_count=len(ticket_settlements),
        winning_ticket_count=sum(
            item.status is SettlementStatus.WON for item in ticket_settlements
        ),
        budget_fen=portfolio.budget_fen,
        stake_fen=portfolio.total_stake_fen,
        settled_stake_fen=settled_stake,
        cash_fen=portfolio.cash_position.amount_fen,
        gross_payout_fen=gross_payout,
        profit_loss_fen=gross_payout - settled_stake,
        is_no_bet=portfolio.status is PortfolioStatus.NO_BET,
        ticket_odds=tuple(_ticket_odds(item) for item in portfolio.tickets),
        ticket_probabilities=tuple(
            item.probability_any_payout for item in portfolio.tickets
        ),
        selection_evs=tuple(
            leg.ev for ticket in portfolio.tickets for leg in ticket.candidate.legs
        ),
        max_match_exposure_fen=risk.max_match_exposure_fen,
        max_selection_exposure_fen=max(
            (item.exposed_stake_fen for item in risk.selection_exposures),
            default=0,
        ),
        realized_loss_when_top_exposure_failed_fen=_realized_top_exposure_loss(
            portfolio,
            risk,
            result_by_match,
            settled_by_ticket,
            1,
        ),
        realized_loss_when_top_two_exposure_failed_fen=_realized_top_exposure_loss(
            portfolio,
            risk,
            result_by_match,
            settled_by_ticket,
            2,
        ),
    )
    return WalkForwardBacktestV2SlateResult(
        plan=plan,
        model_decision=decision,
        match_result_batch=archived_results,
        portfolio_settlement_result=settlement_result,
        backtest_slice=backtest_slice,
        slate_snapshot=slate_snapshot,
        ticket_settlements=ticket_settlements,
        portfolio_settlement=settlement_result.portfolio_settlement,
    )


def _match_snapshot(
    request: WalkForwardBacktestV2Request,
    slice_id: str,
    result: MatchResult,
    archive: BacktestArchiveProvenance,
    evaluation: BacktestV2ModelEvaluationRef,
) -> BacktestV2MatchSnapshot:
    return BacktestV2MatchSnapshot(
        backtest_run_id=request.backtest_run_id,
        data_mode=request.data_mode,
        slice_id=slice_id,
        match_id=result.match_id,
        match_result_id=result.match_result_id,
        match_result_payload_hash=result.payload_hash,
        match_result_archive_id=archive.archive_id,
        match_result_archive_schema_version=archive.archive_schema_version,
        match_result_archive_provider_code=archive.provider_code,
        match_result_archive_payload_sha256=archive.payload_sha256,
        outcome=result.three_way_selection(),
        quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
        quant_status=evaluation.status,
        p_market=evaluation.p_market,
        p_quant=evaluation.p_quant,
        p_final=evaluation.p_final,
    )


def _validate_result_graph(result: WalkForwardBacktestV2Result) -> None:
    request = result.request
    run = result.backtest_run
    if tuple(item.plan for item in result.slate_results) != request.slates:
        raise ValueError("BACKTEST_V2 result does not cover its requested slates")
    expected_slice_ids = tuple(
        _slice_id(request, index, plan)
        for index, plan in enumerate(request.slates, start=1)
    )
    if (
        run.backtest_run_id != request.backtest_run_id
        or run.backtest_version != BACKTEST_V2
        or run.data_mode != request.data_mode
        or run.strategy_snapshot != _strategy_snapshot(request)
        or run.archive_provenance != request.archive_provenance
        or run.expected_slice_ids != expected_slice_ids
        or run.status is not BacktestRunStatus.COMPLETED
    ):
        raise ValueError("BACKTEST_V2 run metadata is inconsistent")
    if run.created_at_utc < request.slates[-1].evaluation_as_of_at_utc:
        raise ValueError("BACKTEST_V2 run predates its evaluation cutoff")
    if request.execution_time_utc is not None and run.created_at_utc != request.execution_time_utc:
        raise ValueError("BACKTEST_V2 run uses another execution time")
    for index, item in enumerate(result.slate_results, start=1):
        expected_decision = _freeze_decision_snapshot(
            request,
            item.plan,
            expected_slice_ids[index - 1],
            item.model_decision,
            run.created_at_utc,
        )
        if item.backtest_slice.decision_snapshot != expected_decision:
            raise ValueError("BACKTEST_V2 decision snapshot is not reproducible")
        expected_outputs = _build_slate_outputs(
            request=request,
            plan=item.plan,
            decision=item.model_decision,
            decision_snapshot=expected_decision,
            archived_results=item.match_result_batch,
            settlement_result=item.portfolio_settlement_result,
        )
        if item != expected_outputs:
            raise ValueError("BACKTEST_V2 slice does not match its frozen artifacts")
    expected_metrics = calculate_backtest_v2_metrics(
        run,
        result.backtest_slices,
        result.slate_snapshots,
        request.metrics_config,
    )
    if result.metrics != expected_metrics:
        raise ValueError("BACKTEST_V2 metrics do not match frozen snapshots")


def _strategy_snapshot(
    request: WalkForwardBacktestV2Request,
) -> BacktestStrategySnapshot:
    return BacktestStrategySnapshot.from_config(
        request.fusion_policy.value,
        {
            "backtest_version": BACKTEST_V2,
            "budget_fen": request.budget_fen,
            "quant_weight": request.quant_weight,
            "min_selection_ev": request.min_selection_ev,
            "min_ticket_roi": request.min_ticket_roi,
            "portfolio_constraints": request.constraints,
            "slate_policy": EXPLICIT_CHRONOLOGICAL_SLATE_POLICY_V2,
            "quant_source": "MODEL",
            "model_name": "ELO_THREE_WAY_BASELINE_V1",
            "calibration_label": "BASELINE_UNCALIBRATED",
            "model_config": request.elo_config,
            "model_config_hash": request.elo_config.config_hash,
            "unavailable_policy": "INCLUDE_IN_COVERAGE_NO_PROJECTION_V1",
        },
    )


def _analysis_run_id(
    request: WalkForwardBacktestV2Request,
    slice_no: int,
    plan: BacktestV2SlatePlan,
) -> str:
    return stable_id(
        "walk-forward-model-analysis-v2",
        request.backtest_run_id,
        slice_no,
        plan.competition_id,
        plan.season_id,
        plan.decision_as_of_at_utc.isoformat(),
        *plan.match_ids,
    )


def _slice_id(
    request: WalkForwardBacktestV2Request,
    slice_no: int,
    plan: BacktestV2SlatePlan,
) -> str:
    return stable_id(
        "walk-forward-slice-v2",
        request.backtest_run_id,
        slice_no,
        plan.decision_as_of_at_utc.isoformat(),
        plan.evaluation_as_of_at_utc.isoformat(),
        *plan.match_ids,
    )


def _require_archive(
    registered: tuple[BacktestArchiveProvenance, ...],
    source: BacktestArchiveProvenance,
) -> None:
    if source not in registered:
        raise ValueError(
            f"BACKTEST_V2 source archive is absent from run provenance: {source.archive_id}"
        )
