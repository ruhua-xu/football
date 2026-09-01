from __future__ import annotations

import json
import tomllib
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from football_system.application.backtest import (
    EXPLICIT_CHRONOLOGICAL_SLATE_POLICY,
    SUPPORTED_BACKTEST_POLICIES,
    WALK_FORWARD_BACKTEST_VERSION,
    BacktestSlatePlan,
)
from football_system.application.historical_archive import HistoricalArchiveSummary
from football_system.config import AppSettings
from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestMetrics,
    BacktestProbabilityMetrics,
    BacktestRun,
    BacktestSlice,
    canonical_archive_provenance,
)
from football_system.domain.betting import (
    Portfolio,
    PortfolioConstraints,
    PortfolioStatus,
)
from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.settlement import (
    MatchResult,
    PortfolioSettlement,
    PortfolioSettlementResult,
    Settlement,
)


class _FixtureModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


FixtureOutcome = Literal["HOME_WIN", "DRAW", "AWAY_WIN", "MISSING_RESULT"]
FixtureMetadataValue = str | int | bool


class BacktestFixtureArchive(_FixtureModel):
    filename: str = Field(min_length=1)
    dataset_kind: HistoricalArchiveDatasetKind
    record_count: int = Field(ge=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BacktestFixturePortfolio(_FixtureModel):
    preferred_max_tickets: int = Field(ge=1)
    absolute_max_tickets: int = Field(ge=1)
    extra_ticket_min_roi: Decimal = Field(ge=0, allow_inf_nan=False)
    operational_complexity_penalty: Decimal = Field(ge=0, allow_inf_nan=False)
    max_match_exposure_ratio: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    max_selection_exposure_ratio: Decimal = Field(
        ge=0,
        le=1,
        allow_inf_nan=False,
    )
    concentration_penalty: Decimal = Field(ge=0, allow_inf_nan=False)
    min_marginal_score: Decimal = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_ticket_limits(self) -> Self:
        if self.preferred_max_tickets > self.absolute_max_tickets:
            raise ValueError(
                "fixture preferred_max_tickets cannot exceed absolute_max_tickets"
            )
        return self


class BacktestFixtureSporttery(_FixtureModel):
    rules_version: str = Field(min_length=1)
    base_stake_fen: int = Field(gt=0)
    max_multiplier: int = Field(ge=1)
    max_ticket_stake_fen: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_stake_limit(self) -> Self:
        if self.max_ticket_stake_fen < self.base_stake_fen:
            raise ValueError("fixture max_ticket_stake_fen must cover one base stake")
        return self


class BacktestFixtureStrategy(_FixtureModel):
    name: FusionPolicyName
    quant_weight: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    expected_no_bet_slate_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.name not in SUPPORTED_BACKTEST_POLICIES:
            raise ValueError(
                "fixture strategy must be QUANT_ONLY_V1 or MARKET_QUANT_BLEND_V1"
            )
        if len(self.expected_no_bet_slate_ids) != len(
            set(self.expected_no_bet_slate_ids)
        ):
            raise ValueError("fixture expected NO_BET slate IDs must be unique")
        return self


class BacktestFixtureSlate(_FixtureModel):
    slate_id: Identifier
    decision_as_of_at_utc: UtcDateTime
    evaluation_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    match_ids: tuple[Identifier, ...] = Field(min_length=1)
    date: Date | None = None
    evaluation_outcomes: tuple[FixtureOutcome, ...] = ()
    expected_settled_match_count: int | None = Field(default=None, ge=0)
    quant_only_status: PortfolioStatus | None = None
    quant_only_ticket_count: int | None = Field(default=None, ge=0)
    quant_only_cash_fen: int | None = Field(default=None, ge=0)
    market_quant_blend_status: PortfolioStatus | None = None
    market_quant_blend_ticket_count: int | None = Field(default=None, ge=0)
    market_quant_blend_cash_fen: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_slate(self) -> Self:
        BacktestSlatePlan(
            decision_as_of_at_utc=self.decision_as_of_at_utc,
            evaluation_as_of_at_utc=self.evaluation_as_of_at_utc,
            kickoff_from_utc=self.kickoff_from_utc,
            kickoff_to_utc=self.kickoff_to_utc,
            match_ids=self.match_ids,
        )
        if len(self.match_ids) != len(set(self.match_ids)):
            raise ValueError("fixture slate match IDs must be unique")
        if self.evaluation_outcomes and len(self.evaluation_outcomes) != len(
            self.match_ids
        ):
            raise ValueError("fixture evaluation outcomes must cover every match")
        if (
            self.expected_settled_match_count is not None
            and self.expected_settled_match_count > len(self.match_ids)
        ):
            raise ValueError("fixture settled match count exceeds its match count")
        return self

    def to_plan(self) -> BacktestSlatePlan:
        return BacktestSlatePlan(
            decision_as_of_at_utc=self.decision_as_of_at_utc,
            evaluation_as_of_at_utc=self.evaluation_as_of_at_utc,
            kickoff_from_utc=self.kickoff_from_utc,
            kickoff_to_utc=self.kickoff_to_utc,
            match_ids=self.match_ids,
        )


class BacktestFixtureConfig(_FixtureModel):
    dataset_id: Identifier | None = None
    classification: str | None = Field(default=None, min_length=1)
    performance_warning: str | None = Field(default=None, min_length=1)
    manifest_label: str | None = Field(default=None, min_length=1)
    archive_schema_version: Identifier | None = None
    provider_code: Identifier
    market_bookmaker_code: Identifier
    data_mode: HistoricalDataMode
    budget_fen: int = Field(ge=0)
    min_selection_ev: Decimal = Field(ge=0, allow_inf_nan=False)
    min_ticket_roi: Decimal = Field(ge=0, allow_inf_nan=False)
    portfolio: BacktestFixturePortfolio
    sporttery: BacktestFixtureSporttery
    strategies: tuple[BacktestFixtureStrategy, ...] = Field(min_length=1)
    slates: tuple[BacktestFixtureSlate, ...] = Field(min_length=1)
    slate_policy: str = Field(min_length=1)
    backtest_version: str | None = Field(default=None, min_length=1)
    slate_count: int | None = Field(default=None, ge=1)
    matches_per_slate: int | None = Field(default=None, ge=1)
    expected_match_count: int | None = Field(default=None, ge=1)
    expected_final_outcome_counts: dict[FixtureOutcome, int] = Field(
        default_factory=dict
    )
    invalid_examples: dict[str, str] = Field(default_factory=dict)
    archives: tuple[BacktestFixtureArchive, ...] = ()
    special_cases: dict[str, dict[str, FixtureMetadataValue]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_fixture(self) -> Self:
        strategy_names = [strategy.name for strategy in self.strategies]
        if len(strategy_names) != len(set(strategy_names)):
            raise ValueError("fixture strategy names must be unique")
        slate_ids = [slate.slate_id for slate in self.slates]
        if len(slate_ids) != len(set(slate_ids)):
            raise ValueError("fixture slate IDs must be unique")
        all_match_ids = [
            match_id for slate in self.slates for match_id in slate.match_ids
        ]
        if len(all_match_ids) != len(set(all_match_ids)):
            raise ValueError("fixture match IDs must be unique across slates")
        for previous, current in zip(self.slates, self.slates[1:]):
            if current.decision_as_of_at_utc <= previous.evaluation_as_of_at_utc:
                raise ValueError(
                    "fixture slates must be chronological and non-overlapping"
                )
        if self.slate_count is not None and self.slate_count != len(self.slates):
            raise ValueError("fixture slate_count does not match slates")
        if self.matches_per_slate is not None and any(
            len(slate.match_ids) != self.matches_per_slate for slate in self.slates
        ):
            raise ValueError("fixture matches_per_slate does not match slates")
        if self.expected_match_count is not None and self.expected_match_count != len(
            all_match_ids
        ):
            raise ValueError("fixture expected_match_count does not match slates")
        archive_kinds = [archive.dataset_kind for archive in self.archives]
        if len(archive_kinds) != len(set(archive_kinds)):
            raise ValueError("fixture archive dataset kinds must be unique")
        if archive_kinds and set(archive_kinds) != set(HistoricalArchiveDatasetKind):
            raise ValueError(
                "fixture archives must cover every historical dataset kind"
            )
        if self.expected_final_outcome_counts and (
            any(value < 0 for value in self.expected_final_outcome_counts.values())
            or set(self.expected_final_outcome_counts)
            != {"HOME_WIN", "DRAW", "AWAY_WIN", "MISSING_RESULT"}
            or sum(self.expected_final_outcome_counts.values()) != len(all_match_ids)
        ):
            raise ValueError("fixture outcome counts do not cover every match")
        return self

    @property
    def plans(self) -> tuple[BacktestSlatePlan, ...]:
        return tuple(slate.to_plan() for slate in self.slates)

    def strategy(self, policy: FusionPolicyName) -> BacktestFixtureStrategy:
        matches = tuple(item for item in self.strategies if item.name is policy)
        if len(matches) != 1:
            raise ValueError(f"fixture has no unique strategy for {policy.value}")
        return matches[0]

    def validate_against_settings(self, settings: AppSettings) -> None:
        if self.data_mode is not settings.backtest.data_mode:
            raise ValueError(
                "fixture data_mode does not match config backtest.data_mode"
            )
        if settings.backtest.version != WALK_FORWARD_BACKTEST_VERSION:
            raise ValueError(
                "config backtest.version does not match the walk-forward version"
            )
        if (
            self.backtest_version is not None
            and self.backtest_version != settings.backtest.version
        ):
            raise ValueError(
                "fixture backtest_version does not match config backtest.version"
            )
        if self.slate_policy != settings.backtest.slates.policy:
            raise ValueError(
                "fixture slate_policy does not match config backtest.slates.policy"
            )
        if self.slate_policy != EXPLICIT_CHRONOLOGICAL_SLATE_POLICY:
            raise ValueError("fixture slate_policy is not supported by walk-forward V1")

    def analysis_settings(
        self,
        base: AppSettings,
        policy: FusionPolicyName,
        *,
        quant_weight: Decimal | None = None,
        min_selection_ev: Decimal | None = None,
        min_ticket_roi: Decimal | None = None,
    ) -> AppSettings:
        strategy = self.strategy(policy)
        payload = base.model_dump(mode="python")
        payload["analysis"].update(
            {
                "fusion_policy": policy.value,
                "quant_weight": (
                    strategy.quant_weight if quant_weight is None else quant_weight
                ),
                "min_selection_ev": (
                    self.min_selection_ev
                    if min_selection_ev is None
                    else min_selection_ev
                ),
                "min_ticket_roi": (
                    self.min_ticket_roi if min_ticket_roi is None else min_ticket_roi
                ),
            }
        )
        payload["portfolio"] = {
            field: getattr(self.portfolio, field)
            for field in BacktestFixturePortfolio.model_fields
        }
        payload["sporttery"] = {
            field: getattr(self.sporttery, field)
            for field in BacktestFixtureSporttery.model_fields
        }
        return AppSettings.model_validate(payload)

    @property
    def constraints(self) -> PortfolioConstraints:
        return PortfolioConstraints.model_validate(
            {
                field: getattr(self.portfolio, field)
                for field in BacktestFixturePortfolio.model_fields
            }
        )

    def validate_result_match_ids(
        self,
        match_ids_by_slate: tuple[tuple[str, ...], ...],
    ) -> None:
        if len(match_ids_by_slate) != len(self.slates):
            raise ValueError("walk-forward result does not cover every fixture slate")
        for slate, actual in zip(self.slates, match_ids_by_slate, strict=True):
            actual_ids = tuple(actual)
            actual_set = set(actual_ids)
            if len(actual_ids) != len(actual_set) or any(
                match_id not in slate.match_ids for match_id in actual_ids
            ):
                raise ValueError(
                    "walk-forward fixture selection contains unexpected match IDs"
                )
            expected_order = tuple(
                match_id for match_id in slate.match_ids if match_id in actual_set
            )
            if actual_ids != expected_order:
                raise ValueError(
                    "walk-forward fixture selection changed configured match order"
                )


def load_backtest_fixture(path: str | Path) -> BacktestFixtureConfig:
    fixture_path = Path(path)
    with fixture_path.open("rb") as stream:
        return BacktestFixtureConfig.model_validate(tomllib.load(stream))


def expected_match_ids_from_analysis_manifest(
    manifest_json: str,
    expected_match_ids: tuple[str, ...],
    missing_decision_match_ids: tuple[str, ...],
) -> tuple[str, ...]:
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError as error:
        raise ValueError("analysis input manifest is invalid JSON") from error
    matches = manifest.get("matches") if isinstance(manifest, dict) else None
    if not isinstance(matches, list):
        raise ValueError("analysis input manifest has no match records")
    decision_match_ids = tuple(
        item.get("match_id") if isinstance(item, dict) else None for item in matches
    )
    if any(
        not isinstance(match_id, str) or not match_id for match_id in decision_match_ids
    ):
        raise ValueError("analysis input manifest has an invalid match record")
    actual_decision_ids = tuple(str(match_id) for match_id in decision_match_ids)
    if len(actual_decision_ids) != len(set(actual_decision_ids)):
        raise ValueError("analysis input manifest has duplicate match records")
    missing_match_set = set(missing_decision_match_ids)
    if (
        len(expected_match_ids) != len(set(expected_match_ids))
        or len(missing_decision_match_ids) != len(missing_match_set)
        or missing_decision_match_ids
        != tuple(
            match_id
            for match_id in expected_match_ids
            if match_id in missing_match_set
        )
    ):
        raise ValueError("persisted slice expected or missing match IDs are invalid")
    expected_decision_ids = tuple(
        match_id
        for match_id in expected_match_ids
        if match_id not in missing_match_set
    )
    if actual_decision_ids != expected_decision_ids:
        raise ValueError(
            "analysis input manifest decision matches conflict with persisted slice"
        )
    return expected_match_ids


class BacktestReportData(DomainModel):
    backtest_run: BacktestRun
    slices: tuple[BacktestSlice, ...]
    metrics: BacktestMetrics
    expected_match_ids_by_slice: tuple[tuple[Identifier, ...], ...]
    archive_manifests: tuple[HistoricalArchiveManifest, ...] = ()

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        run_id = self.backtest_run.backtest_run_id
        if self.metrics.backtest_run_id != run_id:
            raise ValueError("backtest report metrics reference another run")
        if self.metrics.backtest_version != self.backtest_run.backtest_version:
            raise ValueError("backtest report metrics use another version")
        if self.metrics.data_mode is not self.backtest_run.data_mode:
            raise ValueError("backtest report metrics use another data mode")
        if any(item.backtest_run_id != run_id for item in self.slices):
            raise ValueError("backtest report slices reference another run")
        if any(
            item.data_mode is not self.backtest_run.data_mode for item in self.slices
        ):
            raise ValueError("backtest report slices use another data mode")
        if len({item.slice_id for item in self.slices}) != len(self.slices):
            raise ValueError("backtest report slice IDs must be unique")
        actual_slice_ids = tuple(item.slice_id for item in self.slices)
        if (
            self.backtest_run.expected_slice_ids
            and actual_slice_ids != self.backtest_run.expected_slice_ids
        ):
            raise ValueError("backtest report slices do not match expected slice IDs")
        if len(self.expected_match_ids_by_slice) != len(self.slices):
            raise ValueError(
                "backtest report requires expected matches for every slice"
            )
        for item, expected_match_ids in zip(
            self.slices,
            self.expected_match_ids_by_slice,
            strict=True,
        ):
            if expected_match_ids != item.expected_match_ids:
                raise ValueError(
                    "backtest report expected match IDs conflict with slice lineage"
                )
            if len(expected_match_ids) != len(set(expected_match_ids)):
                raise ValueError("backtest report expected match IDs must be unique")
            if len(expected_match_ids) != item.match_count:
                raise ValueError("backtest report expected match count is inconsistent")
            if any(
                match_id not in expected_match_ids
                for match_id in item.missing_decision_match_ids
            ):
                raise ValueError(
                    "backtest report missing decision IDs are outside expected matches"
                )
            issue_match_ids = tuple(
                issue.match_id for issue in item.match_result_issues
            )
            if any(match_id not in expected_match_ids for match_id in issue_match_ids):
                raise ValueError(
                    "backtest report result issues are outside expected matches"
                )
            if item.settled_match_count > item.match_count - len(
                item.missing_decision_match_ids
            ):
                raise ValueError(
                    "backtest report settled matches exceed decision coverage"
                )
        if self.metrics.slate_count != len(self.slices):
            raise ValueError("backtest report slice count does not match metrics")
        if sum(item.match_count for item in self.slices) != self.metrics.match_count:
            raise ValueError("backtest report match count does not match metrics")
        if (
            sum(item.settled_match_count for item in self.slices)
            != self.metrics.settled_match_count
        ):
            raise ValueError(
                "backtest report settled match count does not match metrics"
            )
        if sum(item.ticket_count for item in self.slices) != self.metrics.ticket_count:
            raise ValueError("backtest report ticket count does not match metrics")
        if (
            sum(item.settled_ticket_count for item in self.slices)
            != self.metrics.settled_ticket_count
        ):
            raise ValueError(
                "backtest report settled ticket count does not match metrics"
            )
        if any(
            manifest.data_mode is not self.backtest_run.data_mode
            for manifest in self.archive_manifests
        ):
            raise ValueError(
                "backtest report archive provenance uses another data mode"
            )
        if len({item.archive_id for item in self.archive_manifests}) != len(
            self.archive_manifests
        ):
            raise ValueError("backtest report archive manifest IDs must be unique")
        if self.backtest_run.archive_provenance:
            actual_provenance = canonical_archive_provenance(
                tuple(
                    BacktestArchiveProvenance.from_manifest(manifest)
                    for manifest in self.archive_manifests
                )
            )
            if actual_provenance != self.backtest_run.archive_provenance:
                raise ValueError(
                    "backtest report manifests do not match run archive provenance"
                )
        return self


class BacktestReportComparison(DomainModel):
    left: BacktestReportData
    right: BacktestReportData

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        validate_backtest_comparison(self.left, self.right)
        return self


def validate_backtest_comparison(
    left: BacktestReportData,
    right: BacktestReportData,
) -> None:
    # Revalidate copied instances so comparison cannot bypass report lineage checks.
    BacktestReportData.model_validate(
        left.model_dump(mode="python", exclude_computed_fields=True)
    )
    BacktestReportData.model_validate(
        right.model_dump(mode="python", exclude_computed_fields=True)
    )
    policies = {
        left.backtest_run.strategy_version,
        right.backtest_run.strategy_version,
    }
    expected_policies = {item.value for item in SUPPORTED_BACKTEST_POLICIES}
    if policies != expected_policies:
        raise ValueError(
            "backtest comparison requires exactly QUANT_ONLY_V1 and "
            "MARKET_QUANT_BLEND_V1"
        )

    left_run = left.backtest_run
    right_run = right.backtest_run
    checks = (
        ("data mode", left_run.data_mode, right_run.data_mode),
        ("backtest version", left_run.backtest_version, right_run.backtest_version),
        (
            "date window",
            (left_run.date_from, left_run.date_to),
            (right_run.date_from, right_run.date_to),
        ),
        ("code revision", left_run.code_revision, right_run.code_revision),
        (
            "archive provenance",
            left_run.archive_provenance,
            right_run.archive_provenance,
        ),
        (
            "expected slice IDs",
            _comparison_expected_slice_structure(left),
            _comparison_expected_slice_structure(right),
        ),
        (
            "expected match IDs",
            left.expected_match_ids_by_slice,
            right.expected_match_ids_by_slice,
        ),
        (
            "strategy configuration",
            _policy_neutral_strategy_config(left_run),
            _policy_neutral_strategy_config(right_run),
        ),
        (
            "metric configuration",
            (
                left.metrics.metrics_version,
                left.metrics.log_loss_clip_version,
                left.metrics.log_loss_epsilon,
            ),
            (
                right.metrics.metrics_version,
                right.metrics.log_loss_clip_version,
                right.metrics.log_loss_epsilon,
            ),
        ),
        (
            "slice cutoffs and replay lineage",
            _comparison_slice_keys(left.slices),
            _comparison_slice_keys(right.slices),
        ),
    )
    mismatches = [
        label for label, left_value, right_value in checks if left_value != right_value
    ]
    if mismatches:
        raise ValueError(
            "backtest comparison requires identical " + ", ".join(mismatches)
        )


def render_backtest_report(report: BacktestReportData) -> str:
    run = report.backtest_run
    metrics = report.metrics
    lines = [
        "# Walk-Forward Backtest Report",
        "",
        f"**data_mode_label: {run.data_mode.report_label}**",
    ]
    lines.extend(_archive_warning_lines(report.archive_manifests))
    lines.extend(_coverage_warning_lines(report))
    lines.extend(
        [
            "",
            "## Run Lineage",
            f"- backtest_run_id: {run.backtest_run_id}",
            f"- backtest_version: {run.backtest_version}",
            f"- fusion_policy: {run.strategy_version}",
            f"- strategy_config_hash: {run.strategy_config_hash}",
            f"- strategy_config_json: `{run.strategy_config_json}`",
            f"- code_revision: {run.code_revision}",
            f"- date_from: {run.date_from.isoformat()}",
            f"- date_to: {run.date_to.isoformat()}",
            f"- created_at_utc: {_timestamp(run.created_at_utc)}",
            f"- execution_created_at_utc: {_timestamp(run.created_at_utc)}",
            f"- status: {run.status.value}",
            "",
            "## Probability Metrics",
            f"- metrics_version: {metrics.metrics_version}",
            f"- log_loss_clip_version: {metrics.log_loss_clip_version}",
            f"- log_loss_epsilon: {_value(metrics.log_loss_epsilon)}",
        ]
    )
    _append_probability_metrics(lines, "P_market", metrics.p_market, metrics)
    _append_probability_metrics(lines, "P_quant", metrics.p_quant, metrics)
    _append_probability_metrics(lines, "P_final", metrics.p_final, metrics)

    lines.extend(
        [
            "",
            "## Aggregate Metrics",
            f"- slate_count: {metrics.slate_count}",
            f"- settled_slate_count: {metrics.settled_slate_count}",
            f"- slate_coverage: {_value(metrics.slate_coverage)}",
            f"- match_count: {metrics.match_count}",
            f"- settled_match_count: {metrics.settled_match_count}",
            f"- match_coverage: {_value(metrics.match_coverage)}",
            f"- ticket_count: {metrics.ticket_count}",
            f"- settled_ticket_count: {metrics.settled_ticket_count}",
            f"- ticket_coverage: {_value(metrics.ticket_coverage)}",
            f"- total_budget: {_money(metrics.total_budget_fen)}",
            f"- total_stake: {_money(metrics.total_stake_fen)}",
            f"- total_settled_stake: {_money(metrics.total_settled_stake_fen)}",
            f"- total_cash: {_money(metrics.total_cash_fen)}",
            f"- gross_payout: {_money(metrics.gross_payout_fen)}",
            f"- profit_loss: {_money(metrics.profit_loss_fen)}",
            f"- ROI_on_budget: {_value(metrics.roi_on_budget)}",
            f"- ROI_on_deployed: {_value(metrics.roi_on_deployed)}",
            f"- winning_ticket_count: {metrics.winning_ticket_count}",
            f"- ticket_hit_rate: {_value(metrics.ticket_hit_rate)}",
            f"- NO_BET_count: {metrics.no_bet_count}",
            f"- NO_BET_ratio: {_value(metrics.no_bet_ratio)}",
            f"- max_drawdown: {_money(metrics.max_drawdown_fen)}",
            f"- max_consecutive_losing_slates: {metrics.max_consecutive_losing_slates}",
            f"- average_ticket_odds: {_value(metrics.average_ticket_odds)}",
            "- average_ticket_probability: "
            f"{_value(metrics.average_ticket_probability)}",
            f"- average_selection_EV: {_value(metrics.average_selection_ev)}",
            f"- max_match_exposure: {_money(metrics.max_match_exposure_fen)}",
            f"- max_selection_exposure: {_money(metrics.max_selection_exposure_fen)}",
            "- realized_loss_when_top_exposure_failed: "
            f"{_money(metrics.realized_loss_when_top_exposure_failed_fen)}",
            "- realized_loss_when_top_two_exposure_failed: "
            f"{_money(metrics.realized_loss_when_top_two_exposure_failed_fen)}",
            "",
            "## Slices",
        ]
    )
    for index, (item, expected_match_ids) in enumerate(
        zip(report.slices, report.expected_match_ids_by_slice, strict=True),
        start=1,
    ):
        decision_match_count = item.match_count - len(item.missing_decision_match_ids)
        lines.extend(
            [
                f"### Slice {index}",
                f"- slice_id: {item.slice_id}",
                f"- analysis_run_id: {item.analysis_run_id}",
                f"- decision_as_of_at_utc: {_timestamp(item.decision_as_of_at_utc)}",
                f"- kickoff_from_utc: {_timestamp(item.kickoff_from_utc)}",
                f"- kickoff_to_utc: {_timestamp(item.kickoff_to_utc)}",
                "- evaluation_as_of_at_utc: "
                f"{_timestamp(item.evaluation_as_of_at_utc)}",
                "- expected_match_ids: " + ",".join(expected_match_ids),
                f"- decision_match_count: {decision_match_count}",
                "- decision_coverage: "
                f"{_ratio_value(decision_match_count, item.match_count)}",
                "- missing_decision_match_ids: "
                + (",".join(item.missing_decision_match_ids) or "NONE"),
                f"- decision_input_manifest_hash: {item.decision_input_manifest_hash}",
                "- match_result_ids: " + (",".join(item.match_result_ids) or "NONE"),
                "- match_result_issues: "
                + (
                    ",".join(
                        f"{issue.match_id}:{issue.reason.value}"
                        + (f":{issue.detail}" if issue.detail is not None else "")
                        for issue in item.match_result_issues
                    )
                    or "NONE"
                ),
                f"- match_count: {item.match_count}",
                f"- settled_match_count: {item.settled_match_count}",
                f"- match_coverage: {_value(item.match_coverage)}",
                f"- ticket_count: {item.ticket_count}",
                f"- settled_ticket_count: {item.settled_ticket_count}",
                f"- ticket_coverage: {_value(item.ticket_coverage)}",
            ]
        )

    if report.archive_manifests:
        lines.extend(["", "## Archive Provenance"])
        for manifest in report.archive_manifests:
            lines.extend(
                [
                    f"### {manifest.dataset_kind.value}",
                    f"- archive_id: {manifest.archive_id}",
                    f"- archive_schema_version: {manifest.archive_schema_version}",
                    f"- provider_code: {manifest.provider_code}",
                    f"- payload_sha256: {manifest.payload_sha256}",
                    f"- source_reference: {manifest.source_reference}",
                    f"- source_description: {manifest.source_description}",
                    f"- license_note: {manifest.license_note}",
                ]
            )
    return "\n".join(lines)


def render_backtest_comparison(comparison: BacktestReportComparison) -> str:
    left = comparison.left
    right = comparison.right
    left_metrics = left.metrics
    right_metrics = right.metrics
    lines = [
        "# Backtest Comparison",
        "",
        f"**data_mode_label: {left.backtest_run.data_mode.report_label}**",
    ]
    lines.extend(
        _archive_warning_lines(
            left.archive_manifests + right.archive_manifests,
        )
    )
    lines.extend(
        [
            "",
            "## Side-by-Side Metrics",
            "| metric | left | right |",
            "| --- | --- | --- |",
            _comparison_row(
                "fusion_policy",
                left.backtest_run.strategy_version,
                right.backtest_run.strategy_version,
            ),
            _comparison_row(
                "backtest_run_id",
                left.backtest_run.backtest_run_id,
                right.backtest_run.backtest_run_id,
            ),
            _comparison_row(
                "Brier (P_final)",
                left_metrics.p_final.multiclass_brier_score,
                right_metrics.p_final.multiclass_brier_score,
            ),
            _comparison_row(
                "LogLoss (P_final)",
                left_metrics.p_final.multiclass_log_loss,
                right_metrics.p_final.multiclass_log_loss,
            ),
            _comparison_row(
                "ROI_on_budget",
                left_metrics.roi_on_budget,
                right_metrics.roi_on_budget,
            ),
            _comparison_row(
                "ROI_on_deployed",
                left_metrics.roi_on_deployed,
                right_metrics.roi_on_deployed,
            ),
            _comparison_row(
                "Drawdown (fen)",
                left_metrics.max_drawdown_fen,
                right_metrics.max_drawdown_fen,
            ),
            _comparison_row(
                "NO_BET_count",
                left_metrics.no_bet_count,
                right_metrics.no_bet_count,
            ),
            _comparison_row(
                "NO_BET_ratio",
                left_metrics.no_bet_ratio,
                right_metrics.no_bet_ratio,
            ),
            _comparison_row(
                "Ticket Hit Rate",
                left_metrics.ticket_hit_rate,
                right_metrics.ticket_hit_rate,
            ),
            "",
            "## Left Full Report",
            render_backtest_report(left),
            "",
            "## Right Full Report",
            render_backtest_report(right),
        ]
    )
    return "\n".join(lines)


def render_historical_archive_summary(summary: HistoricalArchiveSummary) -> str:
    action = "validated" if summary.operation == "VALIDATE" else "imported/registered"
    lines = [
        "# Historical Archive",
        f"**data_mode_label: {summary.report_label}**",
    ]
    lines.extend(_archive_warning_lines(summary.manifests))
    lines.extend(
        [
            f"operation: {summary.operation}",
            f"status: {action}",
            f"directory: {summary.directory}",
            f"archive_count: {summary.archive_count}",
            f"record_count: {summary.record_count}",
            f"registration_scope: {summary.registration_scope}",
            f"materialization_policy: {summary.materialization_policy}",
        ]
    )
    if summary.imported_at_utc is not None:
        lines.append(f"imported_at_utc: {_timestamp(summary.imported_at_utc)}")
        lines.append(
            "registered_archive_ids: "
            + (", ".join(summary.registered_archive_ids) or "NONE")
        )
        lines.append(
            "existing_archive_ids: "
            + (", ".join(summary.existing_archive_ids) or "NONE")
        )
    for item in summary.per_kind:
        lines.append(
            f"- {item.dataset_kind.value}: archives={item.archive_count}; "
            f"records={item.record_count}; checksums={','.join(item.checksums)}"
        )
    return "\n".join(lines)


def render_settlement_report(
    portfolio: PortfolioSettlement,
    tickets: tuple[Settlement, ...],
) -> str:
    if tuple(item.settlement_id for item in tickets) != portfolio.ticket_settlement_ids:
        raise ValueError("settlement report tickets do not match portfolio lineage")
    lines = [
        "# Portfolio Settlement Report",
        "",
        "## Lineage",
        f"- portfolio_settlement_id: {portfolio.portfolio_settlement_id}",
        f"- settlement_kind: {portfolio.settlement_kind}",
        f"- scope_kind: {portfolio.scope_kind.value}",
        f"- parent_analysis_run_id: {portfolio.parent_analysis_run_id}",
        f"- decision_scope_id: {portfolio.decision_scope_id}",
        f"- portfolio_id: {portfolio.portfolio_id}",
        "- supersedes_portfolio_settlement_id: "
        f"{portfolio.supersedes_portfolio_settlement_id or 'NONE'}",
        f"- settlement_policy_version: {portfolio.settlement_policy_version}",
        f"- settled_at_utc: {_timestamp(portfolio.settled_at_utc)}",
        f"- NO_BET: {'true' if not tickets else 'false'}",
        "",
        "## Financials",
        f"- budget: {_money(portfolio.budget_fen)}",
        f"- deployed_stake: {_money(portfolio.deployed_stake_fen)}",
        f"- original_cash: {_money(portfolio.original_cash_fen)}",
        f"- gross_ticket_payout: {_money(portfolio.gross_ticket_payout_fen)}",
        f"- ending_capital: {_money(portfolio.ending_capital_fen)}",
        f"- profit_loss: {_money(portfolio.profit_loss_fen)}",
        f"- ROI_on_budget: {_value(portfolio.roi_on_budget)}",
        f"- ROI_on_deployed: {_value(portfolio.roi_on_deployed)}",
        "",
        "## Ticket Settlements",
    ]
    if not tickets:
        lines.append("- ticket_settlements: EMPTY")
    for item in tickets:
        lines.extend(
            [
                f"### {item.ticket_id}",
                f"- settlement_id: {item.settlement_id}",
                f"- status: {item.status.value}",
                f"- match_result_ids: {','.join(item.match_result_ids)}",
                f"- frozen_stake: {_money(item.stake_fen)}",
                f"- frozen_gross_payout: {_money(item.gross_payout_fen)}",
                f"- profit_loss: {_money(item.profit_loss_fen)}",
                f"- payout_policy_version: {item.payout_policy_version}",
                f"- settlement_policy_version: {item.settlement_policy_version}",
                f"- settled_at_utc: {_timestamp(item.settled_at_utc)}",
                "- supersedes_settlement_id: "
                f"{item.supersedes_settlement_id or 'NONE'}",
            ]
        )
    return "\n".join(lines)


def render_settlement_result(
    portfolio: Portfolio,
    result: PortfolioSettlementResult,
    data_mode: HistoricalDataMode,
    archive_manifests: tuple[HistoricalArchiveManifest, ...] = (),
) -> str:
    if result.portfolio_id != portfolio.portfolio_id:
        raise ValueError("settlement result references another portfolio")
    lines = [
        "# Settlement Creation Result",
        f"**data_mode_label: {data_mode.report_label}**",
    ]
    lines.extend(_archive_warning_lines(archive_manifests))
    lines.extend(
        [
            f"portfolio_id: {portfolio.portfolio_id}",
            f"analysis_run_id: {portfolio.analysis_run_id}",
            f"portfolio_status: {portfolio.status.value}",
            f"settlement_reason: {result.reason.value}",
        ]
    )
    for item in result.ticket_results:
        lines.extend(
            [
                f"ticket_id: {item.ticket_id}",
                f"coverage_reason: {item.coverage.reason.value}",
                "covered_match_ids: "
                + (",".join(item.coverage.covered_match_ids) or "NONE"),
                "missing_match_ids: "
                + (",".join(item.coverage.missing_match_ids) or "NONE"),
            ]
        )
        if item.coverage.issues:
            lines.append(
                "settlement_issues: "
                + ",".join(
                    f"{issue.match_id}:{issue.reason.value}"
                    for issue in item.coverage.issues
                )
            )
        if item.coverage.unsupported_reasons:
            lines.append(
                "unsupported_reasons: "
                + ",".join(reason.value for reason in item.coverage.unsupported_reasons)
            )
        if item.settlement is not None:
            lines.extend(
                [
                    f"settlement_id: {item.settlement.settlement_id}",
                    f"status: {item.settlement.status.value}",
                    f"frozen_stake: {_money(item.settlement.stake_fen)}",
                    f"frozen_gross_payout: {_money(item.settlement.gross_payout_fen)}",
                    f"profit_loss: {_money(item.settlement.profit_loss_fen)}",
                    "supersedes_settlement_id: "
                    f"{item.settlement.supersedes_settlement_id or 'NONE'}",
                ]
            )
    if result.unsupported_reasons:
        lines.append(
            "portfolio_unsupported_reasons: "
            + ",".join(reason.value for reason in result.unsupported_reasons)
        )
    if result.portfolio_settlement is None:
        lines.append("portfolio_capital: UNAVAILABLE_INCOMPLETE_COVERAGE")
    else:
        settlement = result.portfolio_settlement
        lines.extend(
            [
                f"portfolio_settlement_id: {settlement.portfolio_settlement_id}",
                f"portfolio_capital: {_money(settlement.ending_capital_fen)}",
                f"portfolio_profit_loss: {_money(settlement.profit_loss_fen)}",
                f"ROI_on_budget: {_value(settlement.roi_on_budget)}",
                f"ROI_on_deployed: {_value(settlement.roi_on_deployed)}",
                "supersedes_portfolio_settlement_id: "
                f"{settlement.supersedes_portfolio_settlement_id or 'NONE'}",
            ]
        )
    return "\n".join(lines)


def render_match_results(
    requested_match_ids: tuple[str, ...],
    as_of_at_utc: datetime,
    results: tuple[MatchResult, ...],
    provider_code: str | None,
) -> str:
    requested = set(requested_match_ids)
    if any(result.match_id not in requested for result in results):
        raise ValueError("match result report contains an unrequested match")
    by_match = {result.match_id: result for result in results}
    if len(by_match) != len(results):
        raise ValueError("match result report contains duplicate matches")
    missing = tuple(
        match_id for match_id in requested_match_ids if match_id not in by_match
    )
    lines = [
        "# Match Results",
        f"as_of_at_utc: {_timestamp(as_of_at_utc)}",
        f"provider_code: {provider_code or 'ANY'}",
        f"requested_match_count: {len(requested_match_ids)}",
        f"result_count: {len(results)}",
        "missing_match_ids: " + (",".join(missing) or "NONE"),
    ]
    if not results:
        lines.append("match_results: EMPTY")
        return "\n".join(lines)
    for match_id in requested_match_ids:
        item = by_match.get(match_id)
        if item is None:
            continue
        lines.extend(
            [
                f"## {item.match_id}",
                f"- match_result_id: {item.match_result_id}",
                f"- provider_code: {item.provider_code}",
                f"- outcome: {item.three_way_selection().value}",
                f"- score: {item.home_goals}-{item.away_goals}",
                f"- observed_at_utc: {_timestamp(item.observed_at_utc)}",
                f"- available_at_utc: {_timestamp(item.available_at_utc)}",
                f"- ingested_at_utc: {_timestamp(item.ingested_at_utc)}",
                "- supersedes_match_result_id: "
                f"{item.supersedes_match_result_id or 'NONE'}",
            ]
        )
    return "\n".join(lines)


def archive_is_synthetic(manifests: tuple[HistoricalArchiveManifest, ...]) -> bool:
    return any(
        "synthetic"
        in " ".join(
            (
                manifest.source_reference,
                manifest.source_description,
                manifest.license_note,
            )
        ).lower()
        for manifest in manifests
    )


def _coverage_warning_lines(report: BacktestReportData) -> list[str]:
    missing_decisions = tuple(
        item for item in report.slices if item.missing_decision_match_ids
    )
    incomplete_results = tuple(
        item
        for item in report.slices
        if item.settled_match_count
        < item.match_count - len(item.missing_decision_match_ids)
    )
    unsupported_results = tuple(
        (item, issue) for item in report.slices for issue in item.match_result_issues
    )
    lines: list[str] = []
    if missing_decisions:
        lines.extend(["", "**PARTIAL DECISION COVERAGE**"])
        lines.extend(
            "- decision_coverage_warning: "
            f"{item.slice_id}; missing="
            f"{','.join(item.missing_decision_match_ids)}"
            for item in missing_decisions
        )
    if incomplete_results:
        lines.extend(["", "**PARTIAL MATCH RESULT COVERAGE**"])
        lines.extend(
            "- match_result_coverage_warning: "
            f"{item.slice_id}; settled={item.settled_match_count}; "
            f"decision_matches="
            f"{item.match_count - len(item.missing_decision_match_ids)}"
            for item in incomplete_results
        )
    if unsupported_results:
        lines.extend(["", "**UNSUPPORTED_SETTLEMENT_CASE**"])
        lines.extend(
            "- settlement_issue: "
            f"{item.slice_id}; match={issue.match_id}; reason={issue.reason.value}; "
            f"detail={issue.detail or 'NONE'}"
            for item, issue in unsupported_results
        )
    return lines


def _archive_warning_lines(
    manifests: tuple[HistoricalArchiveManifest, ...],
) -> list[str]:
    if not archive_is_synthetic(manifests):
        return []
    return [
        "",
        "**SYNTHETIC ACCEPTANCE DATA**",
        "",
        "**NOT REAL HISTORICAL PERFORMANCE**",
    ]


def _append_probability_metrics(
    lines: list[str],
    label: str,
    values: BacktestProbabilityMetrics,
    aggregate: BacktestMetrics,
) -> None:
    components = values.brier_by_outcome
    lines.extend(
        [
            "",
            f"### {label}",
            f"- sample_count: {values.sample_count}",
            f"- multiclass_Brier: {_value(values.multiclass_brier_score)}",
            f"- Brier_H: {_value(components.home_win)}",
            f"- Brier_D: {_value(components.draw)}",
            f"- Brier_A: {_value(components.away_win)}",
            f"- multiclass_LogLoss: {_value(values.multiclass_log_loss)}",
            f"- log_loss_clip_version: {aggregate.log_loss_clip_version}",
            f"- log_loss_epsilon: {_value(aggregate.log_loss_epsilon)}",
            f"- ECE: {_value(values.expected_calibration_error)}",
            "- calibration_bins:",
        ]
    )
    for item in values.calibration_bins:
        lines.append(
            f"  - {item.label}: mean_probability="
            f"{_value(item.mean_predicted_probability)}; frequency="
            f"{_value(item.observed_frequency)}; absolute_gap="
            f"{_value(item.absolute_gap)}; count={item.count}"
        )


def _policy_neutral_strategy_config(run: BacktestRun) -> dict[str, object]:
    try:
        payload = json.loads(run.strategy_config_json)
    except json.JSONDecodeError as error:
        raise ValueError("backtest strategy configuration is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("backtest strategy configuration must be an object")
    payload.pop("quant_weight", None)
    return payload


def _comparison_slice_keys(
    slices: tuple[BacktestSlice, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.decision_as_of_at_utc,
            item.kickoff_from_utc,
            item.kickoff_to_utc,
            item.evaluation_as_of_at_utc,
            item.expected_match_ids,
            item.decision_input_manifest_hash,
            item.match_result_ids,
            item.match_result_issues,
            item.missing_decision_match_ids,
            item.match_count,
            item.settled_match_count,
            item.settled_ticket_count,
            item.unsettled_ticket_count,
            item.coverage,
        )
        for item in slices
    )


def _comparison_expected_slice_structure(
    report: BacktestReportData,
) -> tuple[tuple[object, ...], ...]:
    # Slice IDs are run-scoped, so compare their already-validated definitions.
    return tuple(
        (
            slice_no,
            item.decision_as_of_at_utc,
            item.kickoff_from_utc,
            item.kickoff_to_utc,
            item.evaluation_as_of_at_utc,
            item.expected_match_ids,
        )
        for slice_no, item in enumerate(report.slices, start=1)
    )


def _comparison_row(label: str, left: object, right: object) -> str:
    return f"| {label} | {_value(left)} | {_value(right)} |"


def _money(value: int) -> str:
    return f"{value} fen ({Decimal(value) / Decimal(100):.2f} Yuan)"


def _value(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, Decimal):
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text == "-0" else text
    return str(value)


def _ratio_value(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "N/A"
    return _value(Decimal(numerator) / Decimal(denominator))


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


format_backtest_report = render_backtest_report
format_backtest_comparison = render_backtest_comparison
format_settlement_report = render_settlement_report
