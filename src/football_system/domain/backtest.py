from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum, StrEnum
from typing import Any, Self

from pydantic import Field, computed_field, model_validator

from football_system.domain.archive import (
    RETROSPECTIVE_RESEARCH_LABEL,
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
)
from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import SelectionKey, ThreeWayProbability
from football_system.domain.settlement import MatchSettlementIssue

RATIO_QUANTUM = Decimal("0.000000000001")
RETROSPECTIVE_SOURCE_TIME_RESEARCH = RETROSPECTIVE_RESEARCH_LABEL
BacktestDataMode = HistoricalDataMode


class BacktestRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def canonical_json(value: object) -> str:
    """Return the stable JSON representation used by replay hashes."""
    return json.dumps(
        _canonical_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class BacktestStrategySnapshot(DomainModel):
    strategy_version: Identifier
    strategy_config_json: str = Field(min_length=2)
    strategy_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_config(
        cls,
        strategy_version: str,
        config: Mapping[str, object],
    ) -> BacktestStrategySnapshot:
        config_json = canonical_json(config)
        return cls(
            strategy_version=strategy_version,
            strategy_config_json=config_json,
            strategy_config_hash=sha256_text(config_json),
        )

    @model_validator(mode="after")
    def validate_config(self) -> BacktestStrategySnapshot:
        payload = _strict_json_object(self.strategy_config_json)
        if canonical_json(payload) != self.strategy_config_json:
            raise ValueError("strategy config JSON must be canonical")
        if sha256_text(self.strategy_config_json) != self.strategy_config_hash:
            raise ValueError("strategy config hash does not match canonical JSON")
        return self


class BacktestArchiveProvenance(DomainModel):
    archive_id: Identifier
    archive_schema_version: Identifier
    provider_code: Identifier
    dataset_kind: HistoricalArchiveDatasetKind
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_manifest(
        cls,
        manifest: HistoricalArchiveManifest,
    ) -> Self:
        return cls(
            archive_id=manifest.archive_id,
            archive_schema_version=manifest.archive_schema_version,
            provider_code=manifest.provider_code,
            dataset_kind=manifest.dataset_kind,
            payload_sha256=manifest.payload_sha256,
        )


def canonical_archive_provenance(
    provenance: Sequence[BacktestArchiveProvenance],
) -> tuple[BacktestArchiveProvenance, ...]:
    items = tuple(provenance)
    archive_ids = [item.archive_id for item in items]
    if len(archive_ids) != len(set(archive_ids)):
        raise ValueError("archive provenance IDs must be unique")
    identities = [
        (item.provider_code, item.dataset_kind, item.payload_sha256) for item in items
    ]
    if len(identities) != len(set(identities)):
        raise ValueError(
            "archive provenance provider/kind/hash identities must be unique"
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                item.archive_id,
                item.archive_schema_version,
                item.provider_code,
                item.dataset_kind.value,
                item.payload_sha256,
            ),
        )
    )


class BacktestRun(DomainModel):
    backtest_run_id: Identifier
    backtest_version: Identifier
    data_mode: BacktestDataMode
    date_from: date
    date_to: date
    strategy_snapshot: BacktestStrategySnapshot
    code_revision: Identifier
    created_at_utc: UtcDateTime
    status: BacktestRunStatus
    archive_provenance: tuple[BacktestArchiveProvenance, ...] = ()
    expected_slice_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_date_range(self) -> BacktestRun:
        if self.date_from > self.date_to:
            raise ValueError("backtest date_from cannot be after date_to")
        object.__setattr__(
            self,
            "archive_provenance",
            canonical_archive_provenance(self.archive_provenance),
        )
        if len(self.expected_slice_ids) != len(set(self.expected_slice_ids)):
            raise ValueError("expected backtest slice IDs must be unique")
        return self

    @computed_field
    @property
    def strategy_version(self) -> str:
        return self.strategy_snapshot.strategy_version

    @computed_field
    @property
    def strategy_config_json(self) -> str:
        return self.strategy_snapshot.strategy_config_json

    @computed_field
    @property
    def strategy_config_hash(self) -> str:
        return self.strategy_snapshot.strategy_config_hash

    @computed_field
    @property
    def data_mode_label(self) -> str:
        return self.data_mode.report_label

    @computed_field
    @property
    def retrospective(self) -> bool:
        return self.data_mode.is_retrospective


class BacktestSlice(DomainModel):
    slice_id: Identifier
    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    decision_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    evaluation_as_of_at_utc: UtcDateTime
    analysis_run_id: Identifier
    decision_input_manifest_hash: str = Field(
        default="0" * 64,
        pattern=r"^[0-9a-f]{64}$",
    )
    match_result_ids: tuple[Identifier, ...] = ()
    match_result_issues: tuple[MatchSettlementIssue, ...] = ()
    expected_match_ids: tuple[Identifier, ...]
    missing_decision_match_ids: tuple[Identifier, ...] = ()
    match_count: int = Field(ge=0)
    settled_match_count: int = Field(ge=0)
    settled_ticket_count: int = Field(ge=0)
    unsettled_ticket_count: int = Field(ge=0)
    coverage: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_slice(self) -> BacktestSlice:
        if self.evaluation_as_of_at_utc <= self.decision_as_of_at_utc:
            raise ValueError("evaluation cutoff must be after decision cutoff")
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("backtest slice kickoff window is invalid")
        if self.decision_as_of_at_utc > self.kickoff_from_utc:
            raise ValueError("backtest slice decision cutoff follows kickoff window")
        if self.evaluation_as_of_at_utc <= self.kickoff_to_utc:
            raise ValueError("backtest slice evaluation cutoff must follow kickoff window")
        if len(self.expected_match_ids) != len(set(self.expected_match_ids)):
            raise ValueError("backtest slice expected match IDs must be unique")
        if len(self.expected_match_ids) != self.match_count:
            raise ValueError("backtest slice expected match count is inconsistent")
        if self.settled_match_count > self.match_count:
            raise ValueError("settled match count cannot exceed match count")
        if len(self.match_result_ids) != len(set(self.match_result_ids)):
            raise ValueError("backtest slice result IDs must be unique")
        if (
            self.match_result_ids
            and len(self.match_result_ids) != self.settled_match_count
        ):
            raise ValueError("backtest slice result IDs must cover every settled match")
        issue_match_ids = tuple(issue.match_id for issue in self.match_result_issues)
        if len(issue_match_ids) != len(set(issue_match_ids)):
            raise ValueError("backtest slice result issues must be unique by match")
        if len(self.missing_decision_match_ids) != len(
            set(self.missing_decision_match_ids)
        ):
            raise ValueError("missing decision match IDs must be unique")
        expected_match_set = set(self.expected_match_ids)
        missing_match_set = set(self.missing_decision_match_ids)
        if any(
            match_id not in expected_match_set
            for match_id in self.missing_decision_match_ids
        ):
            raise ValueError("missing decision matches must be expected")
        if self.missing_decision_match_ids != tuple(
            match_id
            for match_id in self.expected_match_ids
            if match_id in missing_match_set
        ):
            raise ValueError("missing decision match IDs must preserve expected order")
        if any(match_id not in expected_match_set for match_id in issue_match_ids):
            raise ValueError("result issues must reference expected matches")
        if set(issue_match_ids) & missing_match_set:
            raise ValueError("result issues cannot reference missing decision matches")
        issue_match_set = set(issue_match_ids)
        if issue_match_ids != tuple(
            match_id
            for match_id in self.expected_match_ids
            if match_id in issue_match_set
        ):
            raise ValueError("result issues must preserve expected match order")
        decision_match_count = self.match_count - len(self.missing_decision_match_ids)
        if self.settled_match_count + len(issue_match_ids) > decision_match_count:
            raise ValueError(
                "backtest slice result coverage cannot exceed decision match count"
            )
        ticket_count = self.settled_ticket_count + self.unsettled_ticket_count
        expected_coverage = _ratio(self.settled_ticket_count, ticket_count)
        if self.coverage is None and expected_coverage is not None:
            object.__setattr__(self, "coverage", expected_coverage)
        elif self.coverage != expected_coverage:
            raise ValueError("slice ticket coverage is inconsistent")
        return self

    @computed_field
    @property
    def ticket_count(self) -> int:
        return self.settled_ticket_count + self.unsettled_ticket_count

    @computed_field
    @property
    def match_coverage(self) -> Decimal | None:
        return _ratio(self.settled_match_count, self.match_count)

    @computed_field
    @property
    def ticket_coverage(self) -> Decimal | None:
        return self.coverage

    @computed_field
    @property
    def data_mode_label(self) -> str:
        return self.data_mode.report_label


class BacktestMetricsConfig(DomainModel):
    metrics_version: Identifier = "BACKTEST_METRICS_V1"
    log_loss_clip_version: Identifier = "EPSILON_CLIP_V1"
    log_loss_epsilon: Decimal = Field(default=Decimal("0.000001"), gt=0, lt=0.5)


class BacktestMatchSnapshot(DomainModel):
    """One settled match and its frozen decision-time probability streams."""

    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    slice_id: Identifier
    match_id: Identifier
    outcome: SelectionKey
    p_market: ThreeWayProbability
    p_quant: ThreeWayProbability
    p_final: ThreeWayProbability

    @computed_field
    @property
    def data_mode_label(self) -> str:
        return self.data_mode.report_label


class BacktestSlateSnapshot(DomainModel):
    """Small immutable aggregate supplied by each future walk-forward slice."""

    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    slice_id: Identifier
    decision_as_of_at_utc: UtcDateTime
    match_count: int = Field(ge=0)
    settled_match_count: int = Field(ge=0)
    ticket_count: int = Field(ge=0)
    settled_ticket_count: int = Field(ge=0)
    winning_ticket_count: int = Field(ge=0)
    budget_fen: int = Field(ge=0)
    stake_fen: int = Field(ge=0)
    settled_stake_fen: int = Field(ge=0)
    cash_fen: int = Field(ge=0)
    gross_payout_fen: int = Field(ge=0)
    profit_loss_fen: int
    is_no_bet: bool
    ticket_odds: tuple[Decimal, ...] = ()
    ticket_probabilities: tuple[Decimal, ...] = ()
    selection_evs: tuple[Decimal, ...] = ()
    max_match_exposure_fen: int = Field(default=0, ge=0)
    max_selection_exposure_fen: int = Field(default=0, ge=0)
    realized_loss_when_top_exposure_failed_fen: int = Field(default=0, ge=0)
    realized_loss_when_top_two_exposure_failed_fen: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_slate(self) -> BacktestSlateSnapshot:
        if self.settled_match_count > self.match_count:
            raise ValueError("settled match count cannot exceed match count")
        if self.settled_ticket_count > self.ticket_count:
            raise ValueError("settled ticket count cannot exceed ticket count")
        if self.winning_ticket_count > self.settled_ticket_count:
            raise ValueError("winning ticket count cannot exceed settled ticket count")
        if self.cash_fen + self.stake_fen != self.budget_fen:
            raise ValueError("slate cash and stake must equal budget")
        if self.settled_stake_fen > self.stake_fen:
            raise ValueError("settled stake cannot exceed deployed stake")
        if self.profit_loss_fen != self.gross_payout_fen - self.settled_stake_fen:
            raise ValueError("slate profit/loss is inconsistent")
        if self.settled_ticket_count == 0 and any(
            (self.settled_stake_fen, self.winning_ticket_count, self.gross_payout_fen)
        ):
            raise ValueError("unsettled slate cannot contain settlement financials")
        if self.settled_ticket_count > 0 and self.settled_stake_fen == 0:
            raise ValueError("settled tickets require settled stake")
        if self.winning_ticket_count == 0 and self.gross_payout_fen != 0:
            raise ValueError("slate without winning tickets cannot contain payout")
        if self.winning_ticket_count > 0 and self.gross_payout_fen == 0:
            raise ValueError("winning tickets require gross payout")
        if self.settled_ticket_count == self.ticket_count:
            if self.settled_stake_fen != self.stake_fen:
                raise ValueError("fully settled slate must settle all deployed stake")
        if self.is_no_bet != (self.ticket_count == 0):
            raise ValueError("NO_BET status must match an empty ticket slate")
        if self.ticket_count == 0:
            if any(
                (
                    self.stake_fen,
                    self.settled_stake_fen,
                    self.gross_payout_fen,
                    self.profit_loss_fen,
                )
            ):
                raise ValueError("NO_BET slate cannot contain betting financials")
        elif self.stake_fen == 0:
            raise ValueError("ticket slate requires deployed stake")
        if len(self.ticket_odds) != self.ticket_count:
            raise ValueError("ticket odds must cover every ticket")
        if len(self.ticket_probabilities) != self.ticket_count:
            raise ValueError("ticket probabilities must cover every ticket")
        if self.ticket_count > 0 and not self.selection_evs:
            raise ValueError("ticket slate requires selection EV observations")
        if any(
            not value.is_finite()
            for values in (
                self.ticket_odds,
                self.ticket_probabilities,
                self.selection_evs,
            )
            for value in values
        ):
            raise ValueError("slate metric observations must be finite")
        if any(value <= 1 for value in self.ticket_odds):
            raise ValueError("ticket odds must be greater than one")
        if any(value < 0 or value > 1 for value in self.ticket_probabilities):
            raise ValueError("ticket probabilities must be between zero and one")
        if self.max_match_exposure_fen > self.stake_fen:
            raise ValueError("match exposure cannot exceed deployed stake")
        if self.max_selection_exposure_fen > self.stake_fen:
            raise ValueError("selection exposure cannot exceed deployed stake")
        if self.realized_loss_when_top_exposure_failed_fen > self.settled_stake_fen:
            raise ValueError("top exposure loss cannot exceed settled stake")
        if self.realized_loss_when_top_two_exposure_failed_fen > self.settled_stake_fen:
            raise ValueError("top-two exposure loss cannot exceed settled stake")
        return self

    @computed_field
    @property
    def data_mode_label(self) -> str:
        return self.data_mode.report_label


class BacktestBrierComponents(DomainModel):
    home_win: Decimal | None = Field(default=None, ge=0)
    draw: Decimal | None = Field(default=None, ge=0)
    away_win: Decimal | None = Field(default=None, ge=0)


class BacktestCalibrationBin(DomainModel):
    label: str
    lower_bound: Decimal = Field(ge=0, le=1)
    upper_bound: Decimal = Field(ge=0, le=1)
    includes_upper_bound: bool = False
    count: int = Field(ge=0)
    mean_predicted_probability: Decimal | None = Field(default=None, ge=0, le=1)
    observed_frequency: Decimal | None = Field(default=None, ge=0, le=1)
    absolute_gap: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_bin(self) -> BacktestCalibrationBin:
        if self.lower_bound >= self.upper_bound:
            raise ValueError("calibration bin bounds are invalid")
        observations = (
            self.mean_predicted_probability,
            self.observed_frequency,
            self.absolute_gap,
        )
        if self.count == 0 and any(value is not None for value in observations):
            raise ValueError("empty calibration bin cannot contain observations")
        if self.count > 0 and any(value is None for value in observations):
            raise ValueError("populated calibration bin requires observations")
        return self


class BacktestProbabilityMetrics(DomainModel):
    sample_count: int = Field(ge=0)
    multiclass_brier_score: Decimal | None = Field(default=None, ge=0)
    brier_by_outcome: BacktestBrierComponents
    multiclass_log_loss: Decimal | None = Field(default=None, ge=0)
    calibration_bins: tuple[BacktestCalibrationBin, ...]
    expected_calibration_error: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_probability_metrics(self) -> BacktestProbabilityMetrics:
        values = (
            self.multiclass_brier_score,
            self.multiclass_log_loss,
            self.expected_calibration_error,
            self.brier_by_outcome.home_win,
            self.brier_by_outcome.draw,
            self.brier_by_outcome.away_win,
        )
        if self.sample_count == 0 and any(value is not None for value in values):
            raise ValueError("empty probability metrics cannot contain scores")
        if self.sample_count > 0 and any(value is None for value in values):
            raise ValueError("probability metrics require complete scores")
        if len(self.calibration_bins) != 10:
            raise ValueError("probability metrics require ten calibration bins")
        for index, item in enumerate(self.calibration_bins):
            lower = Decimal(index) / Decimal(10)
            upper = Decimal(index + 1) / Decimal(10)
            if (
                item.label != f"{lower:.1f}-{upper:.1f}"
                or item.lower_bound != lower
                or item.upper_bound != upper
                or item.includes_upper_bound != (index == 9)
            ):
                raise ValueError("calibration bin boundaries are inconsistent")
        if sum(item.count for item in self.calibration_bins) != self.sample_count * 3:
            raise ValueError("calibration bins must cover every outcome probability")
        components = (
            self.brier_by_outcome.home_win,
            self.brier_by_outcome.draw,
            self.brier_by_outcome.away_win,
        )
        if self.sample_count > 0 and self.multiclass_brier_score != sum(
            (value for value in components if value is not None),
            Decimal(0),
        ):
            raise ValueError("multiclass Brier score must equal outcome components")
        return self


class BacktestMetrics(DomainModel):
    backtest_run_id: Identifier
    backtest_version: Identifier
    data_mode: BacktestDataMode
    metrics_version: Identifier
    log_loss_clip_version: Identifier
    log_loss_epsilon: Decimal = Field(gt=0, lt=0.5)
    p_market: BacktestProbabilityMetrics
    p_quant: BacktestProbabilityMetrics
    p_final: BacktestProbabilityMetrics
    slate_count: int = Field(ge=0)
    settled_slate_count: int = Field(ge=0)
    slate_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    match_count: int = Field(ge=0)
    settled_match_count: int = Field(ge=0)
    match_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    ticket_count: int = Field(ge=0)
    settled_ticket_count: int = Field(ge=0)
    ticket_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    total_budget_fen: int = Field(ge=0)
    total_stake_fen: int = Field(ge=0)
    total_settled_stake_fen: int = Field(ge=0)
    total_cash_fen: int = Field(ge=0)
    gross_payout_fen: int = Field(ge=0)
    profit_loss_fen: int
    roi_on_budget: Decimal | None
    roi_on_deployed: Decimal | None
    winning_ticket_count: int = Field(ge=0)
    ticket_hit_rate: Decimal | None = Field(default=None, ge=0, le=1)
    no_bet_count: int = Field(ge=0)
    no_bet_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    max_drawdown_fen: int = Field(ge=0)
    max_consecutive_losing_slates: int = Field(ge=0)
    average_ticket_odds: Decimal | None = Field(default=None, gt=1)
    average_ticket_probability: Decimal | None = Field(default=None, ge=0, le=1)
    average_selection_ev: Decimal | None = None
    max_match_exposure_fen: int = Field(ge=0)
    max_selection_exposure_fen: int = Field(ge=0)
    realized_loss_when_top_exposure_failed_fen: int = Field(ge=0)
    realized_loss_when_top_two_exposure_failed_fen: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_metrics(self) -> BacktestMetrics:
        if self.settled_slate_count > self.slate_count:
            raise ValueError("settled slate count cannot exceed slate count")
        if self.settled_match_count > self.match_count:
            raise ValueError("settled match count cannot exceed match count")
        if self.settled_ticket_count > self.ticket_count:
            raise ValueError("settled ticket count cannot exceed ticket count")
        if self.winning_ticket_count > self.settled_ticket_count:
            raise ValueError("winning ticket count cannot exceed settled ticket count")
        if self.total_cash_fen + self.total_stake_fen != self.total_budget_fen:
            raise ValueError("aggregate cash and stake must equal budget")
        if self.total_settled_stake_fen > self.total_stake_fen:
            raise ValueError("aggregate settled stake cannot exceed deployed stake")
        if self.profit_loss_fen != (
            self.gross_payout_fen - self.total_settled_stake_fen
        ):
            raise ValueError("aggregate profit/loss is inconsistent")
        expected_values = {
            "slate coverage": (
                self.slate_coverage,
                _ratio(self.settled_slate_count, self.slate_count),
            ),
            "match coverage": (
                self.match_coverage,
                _ratio(self.settled_match_count, self.match_count),
            ),
            "ticket coverage": (
                self.ticket_coverage,
                _ratio(self.settled_ticket_count, self.ticket_count),
            ),
            "ROI on budget": (
                self.roi_on_budget,
                _ratio(self.profit_loss_fen, self.total_budget_fen),
            ),
            "ROI on deployed": (
                self.roi_on_deployed,
                _ratio(self.profit_loss_fen, self.total_stake_fen),
            ),
            "ticket hit rate": (
                self.ticket_hit_rate,
                _ratio(self.winning_ticket_count, self.settled_ticket_count),
            ),
            "NO_BET ratio": (
                self.no_bet_ratio,
                _ratio(self.no_bet_count, self.slate_count),
            ),
        }
        for label, (actual, expected) in expected_values.items():
            if actual != expected:
                raise ValueError(f"{label} is inconsistent")
        if self.no_bet_count > self.slate_count:
            raise ValueError("NO_BET count cannot exceed slate count")
        if any(
            item.sample_count != self.settled_match_count
            for item in (self.p_market, self.p_quant, self.p_final)
        ):
            raise ValueError("probability metrics must cover every settled match")
        return self

    @computed_field
    @property
    def data_mode_label(self) -> str:
        return self.data_mode.report_label

    @computed_field
    @property
    def retrospective(self) -> bool:
        return self.data_mode.is_retrospective


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATIO_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )


def _strict_json_object(value: str) -> dict[str, Any]:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {constant}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(
            value,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid strategy config JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ValueError("strategy config JSON must be an object")
    return payload


def _canonical_value(value: object) -> object:
    if isinstance(value, DomainModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal cannot be serialized")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("JSON datetime must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical JSON object keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite float cannot be serialized")
    return value
