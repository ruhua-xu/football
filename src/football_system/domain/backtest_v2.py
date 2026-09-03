from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Self

from pydantic import Field, computed_field, model_validator

from football_system.domain.backtest import (
    RATIO_QUANTUM,
    BacktestDataMode,
    BacktestProbabilityMetrics,
    canonical_json,
    sha256_text,
)
from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import SelectionKey, ThreeWayProbability
from football_system.domain.prediction import QuantModelEvaluationStatus
from football_system.domain.settlement import MatchSettlementIssue

BACKTEST_V2 = "BACKTEST_V2"
BACKTEST_V2_DECISION_SNAPSHOT_V1 = "BACKTEST_V2_DECISION_SNAPSHOT_V1"
BACKTEST_V2_SLICE_V1 = "BACKTEST_V2_SLICE_V1"
BACKTEST_METRICS_V2 = "BACKTEST_METRICS_V2"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class BacktestV2TrainingSourceRef(DomainModel):
    sequence: int = Field(ge=0, strict=True)
    match_result_id: Identifier
    match_id: Identifier
    source_payload_hash: str = Field(pattern=SHA256_PATTERN)
    fact_hash: str = Field(pattern=SHA256_PATTERN)
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    archive_id: Identifier
    archive_schema_version: Identifier
    archive_provider_code: Identifier
    archive_payload_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.available_at_utc > self.ingested_at_utc:
            raise ValueError("training source ingestion cannot precede availability")
        return self


class BacktestV2ModelEvaluationRef(DomainModel):
    match_id: Identifier
    quant_model_evaluation_id: Identifier
    status: QuantModelEvaluationStatus
    unavailable_reason: Identifier | None = None
    output_hash: str = Field(pattern=SHA256_PATTERN)
    model_prediction_hash: str = Field(pattern=SHA256_PATTERN)
    market_prediction_id: Identifier
    quant_prediction_id: Identifier | None = None
    final_prediction_id: Identifier | None = None
    p_market: ThreeWayProbability
    p_quant: ThreeWayProbability | None = None
    p_final: ThreeWayProbability | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.status is QuantModelEvaluationStatus.AVAILABLE:
            if (
                self.unavailable_reason is not None
                or self.quant_prediction_id is None
                or self.final_prediction_id is None
                or self.p_quant is None
                or self.p_final is None
            ):
                raise ValueError(
                    "available model evaluation requires quant and final decisions"
                )
        elif (
            self.unavailable_reason is None
            or self.quant_prediction_id is not None
            or self.final_prediction_id is not None
            or self.p_quant is not None
            or self.p_final is not None
        ):
            raise ValueError(
                "unavailable model evaluation cannot contain quant or final decisions"
            )
        return self


class BacktestV2DecisionSnapshot(DomainModel):
    snapshot_version: str = BACKTEST_V2_DECISION_SNAPSHOT_V1
    backtest_run_id: Identifier
    slice_id: Identifier
    analysis_run_id: Identifier
    decision_as_of_at_utc: UtcDateTime
    decision_input_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    quant_model_state_id: Identifier
    model_name: Identifier
    model_version: Identifier
    calibration_label: Identifier
    model_config_hash: str = Field(pattern=SHA256_PATTERN)
    state_hash: str = Field(pattern=SHA256_PATTERN)
    state_payload_hash: str = Field(pattern=SHA256_PATTERN)
    training_data_hash: str = Field(pattern=SHA256_PATTERN)
    expected_match_ids: tuple[Identifier, ...]
    analyzed_match_ids: tuple[Identifier, ...]
    missing_decision_match_ids: tuple[Identifier, ...] = ()
    training_sources: tuple[BacktestV2TrainingSourceRef, ...] = ()
    evaluations: tuple[BacktestV2ModelEvaluationRef, ...]
    snapshot_hash: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def freeze(cls, **values: object) -> BacktestV2DecisionSnapshot:
        draft = cls.model_construct(**values, snapshot_hash="0" * 64)
        return cls.model_validate(
            {**values, "snapshot_hash": _payload_hash(draft, "snapshot_hash")}
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.snapshot_version != BACKTEST_V2_DECISION_SNAPSHOT_V1:
            raise ValueError("unexpected BACKTEST_V2 decision snapshot version")
        if len(self.expected_match_ids) != len(set(self.expected_match_ids)):
            raise ValueError("BACKTEST_V2 expected match IDs must be unique")
        expected = set(self.expected_match_ids)
        analyzed = set(self.analyzed_match_ids)
        if len(analyzed) != len(self.analyzed_match_ids) or not analyzed.issubset(expected):
            raise ValueError("BACKTEST_V2 analyzed matches must be unique and expected")
        if self.analyzed_match_ids != tuple(
            match_id for match_id in self.expected_match_ids if match_id in analyzed
        ):
            raise ValueError("BACKTEST_V2 analyzed matches must preserve expected order")
        missing = tuple(
            match_id for match_id in self.expected_match_ids if match_id not in analyzed
        )
        if self.missing_decision_match_ids != missing:
            raise ValueError("BACKTEST_V2 missing decision matches are inconsistent")
        if tuple(item.match_id for item in self.evaluations) != self.analyzed_match_ids:
            raise ValueError("BACKTEST_V2 requires one ordered evaluation per decision")
        evaluation_ids = tuple(
            item.quant_model_evaluation_id for item in self.evaluations
        )
        if len(evaluation_ids) != len(set(evaluation_ids)):
            raise ValueError("BACKTEST_V2 model evaluation IDs must be unique")
        if tuple(item.sequence for item in self.training_sources) != tuple(
            range(len(self.training_sources))
        ):
            raise ValueError("BACKTEST_V2 training source sequence must be contiguous")
        training_result_ids = tuple(
            item.match_result_id for item in self.training_sources
        )
        if len(training_result_ids) != len(set(training_result_ids)):
            raise ValueError("BACKTEST_V2 training result IDs must be unique")
        if any(
            item.match_id in expected for item in self.training_sources
        ):
            raise ValueError("BACKTEST_V2 target match cannot occur in training")
        if any(
            item.available_at_utc > self.decision_as_of_at_utc
            or item.ingested_at_utc > self.decision_as_of_at_utc
            for item in self.training_sources
        ):
            raise ValueError("BACKTEST_V2 training source crosses decision cutoff")
        if _payload_hash(self, "snapshot_hash") != self.snapshot_hash:
            raise ValueError("BACKTEST_V2 decision snapshot hash is inconsistent")
        return self

    @computed_field
    @property
    def unavailable_match_ids(self) -> tuple[str, ...]:
        return tuple(
            item.match_id
            for item in self.evaluations
            if item.status is QuantModelEvaluationStatus.UNAVAILABLE
        )


class BacktestV2MatchSnapshot(DomainModel):
    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    slice_id: Identifier
    match_id: Identifier
    match_result_id: Identifier
    match_result_payload_hash: str = Field(pattern=SHA256_PATTERN)
    match_result_archive_id: Identifier
    match_result_archive_schema_version: Identifier
    match_result_archive_provider_code: Identifier
    match_result_archive_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome: SelectionKey
    quant_model_evaluation_id: Identifier
    quant_status: QuantModelEvaluationStatus
    p_market: ThreeWayProbability
    p_quant: ThreeWayProbability | None = None
    p_final: ThreeWayProbability | None = None

    @model_validator(mode="after")
    def validate_probability_streams(self) -> Self:
        if self.quant_status is QuantModelEvaluationStatus.AVAILABLE:
            if self.p_quant is None or self.p_final is None:
                raise ValueError("available BACKTEST_V2 match requires all probabilities")
        elif self.p_quant is not None or self.p_final is not None:
            raise ValueError(
                "unavailable BACKTEST_V2 match cannot contain quant or final probabilities"
            )
        return self


class BacktestV2Slice(DomainModel):
    slice_version: str = BACKTEST_V2_SLICE_V1
    slice_id: Identifier
    backtest_run_id: Identifier
    data_mode: BacktestDataMode
    decision_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    evaluation_as_of_at_utc: UtcDateTime
    decision_snapshot: BacktestV2DecisionSnapshot
    match_snapshots: tuple[BacktestV2MatchSnapshot, ...]
    match_result_issues: tuple[MatchSettlementIssue, ...] = ()
    slice_hash: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def freeze(cls, **values: object) -> BacktestV2Slice:
        draft = cls.model_construct(**values, slice_hash="0" * 64)
        return cls.model_validate(
            {**values, "slice_hash": _payload_hash(draft, "slice_hash")}
        )

    @model_validator(mode="after")
    def validate_slice(self) -> Self:
        if self.slice_version != BACKTEST_V2_SLICE_V1:
            raise ValueError("unexpected BACKTEST_V2 slice version")
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("BACKTEST_V2 kickoff window is invalid")
        if self.decision_as_of_at_utc > self.kickoff_from_utc:
            raise ValueError("BACKTEST_V2 decision cutoff follows kickoff")
        if self.evaluation_as_of_at_utc <= self.kickoff_to_utc:
            raise ValueError("BACKTEST_V2 evaluation cutoff must follow kickoff")
        decision = self.decision_snapshot
        if (
            decision.backtest_run_id != self.backtest_run_id
            or decision.slice_id != self.slice_id
            or decision.decision_as_of_at_utc != self.decision_as_of_at_utc
        ):
            raise ValueError("BACKTEST_V2 decision snapshot has inconsistent lineage")
        snapshot_ids = tuple(item.match_id for item in self.match_snapshots)
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("BACKTEST_V2 match snapshots must be unique")
        if snapshot_ids != tuple(
            match_id
            for match_id in decision.analyzed_match_ids
            if match_id in set(snapshot_ids)
        ):
            raise ValueError("BACKTEST_V2 match snapshots must preserve decision order")
        evaluation_by_match = {item.match_id: item for item in decision.evaluations}
        for snapshot in self.match_snapshots:
            evaluation = evaluation_by_match.get(snapshot.match_id)
            if evaluation is None or (
                snapshot.backtest_run_id != self.backtest_run_id
                or snapshot.slice_id != self.slice_id
                or snapshot.quant_model_evaluation_id
                != evaluation.quant_model_evaluation_id
                or snapshot.quant_status != evaluation.status
                or snapshot.p_market != evaluation.p_market
                or snapshot.p_quant != evaluation.p_quant
                or snapshot.p_final != evaluation.p_final
            ):
                raise ValueError("BACKTEST_V2 match snapshot contradicts its decision")
        issue_ids = tuple(item.match_id for item in self.match_result_issues)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("BACKTEST_V2 result issues must be unique")
        if any(match_id not in decision.analyzed_match_ids for match_id in issue_ids):
            raise ValueError("BACKTEST_V2 result issue references an unknown decision")
        if set(issue_ids) & set(snapshot_ids):
            raise ValueError("BACKTEST_V2 match cannot have a result and an issue")
        if _payload_hash(self, "slice_hash") != self.slice_hash:
            raise ValueError("BACKTEST_V2 slice hash is inconsistent")
        return self

    @computed_field
    @property
    def expected_match_ids(self) -> tuple[str, ...]:
        return self.decision_snapshot.expected_match_ids

    @computed_field
    @property
    def analyzed_match_ids(self) -> tuple[str, ...]:
        return self.decision_snapshot.analyzed_match_ids

    @computed_field
    @property
    def unavailable_match_ids(self) -> tuple[str, ...]:
        return self.decision_snapshot.unavailable_match_ids

    @computed_field
    @property
    def match_result_ids(self) -> tuple[str, ...]:
        return tuple(item.match_result_id for item in self.match_snapshots)


class BacktestV2Metrics(DomainModel):
    backtest_run_id: Identifier
    backtest_version: str = BACKTEST_V2
    data_mode: BacktestDataMode
    metrics_version: str = BACKTEST_METRICS_V2
    log_loss_clip_version: Identifier
    log_loss_epsilon: Decimal = Field(gt=0, lt=0.5)
    p_market: BacktestProbabilityMetrics
    p_quant: BacktestProbabilityMetrics
    p_final: BacktestProbabilityMetrics
    slate_count: int = Field(ge=0)
    planned_target_count: int = Field(ge=0)
    decision_target_count: int = Field(ge=0)
    result_target_count: int = Field(ge=0)
    quant_available_count: int = Field(ge=0)
    quant_unavailable_count: int = Field(ge=0)
    quant_scored_count: int = Field(ge=0)
    decision_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    result_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    quant_availability: Decimal | None = Field(default=None, ge=0, le=1)
    quant_scored_coverage: Decimal | None = Field(default=None, ge=0, le=1)
    ticket_count: int = Field(ge=0)
    settled_ticket_count: int = Field(ge=0)
    total_budget_fen: int = Field(ge=0)
    total_stake_fen: int = Field(ge=0)
    total_settled_stake_fen: int = Field(ge=0)
    gross_payout_fen: int = Field(ge=0)
    profit_loss_fen: int
    roi_on_budget: Decimal | None
    roi_on_deployed: Decimal | None
    no_bet_count: int = Field(ge=0)
    max_drawdown_fen: int = Field(ge=0)
    max_consecutive_losing_slates: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        if self.backtest_version != BACKTEST_V2:
            raise ValueError("unexpected BACKTEST_V2 metrics backtest version")
        if self.metrics_version != BACKTEST_METRICS_V2:
            raise ValueError("unexpected BACKTEST_V2 metrics version")
        if self.decision_target_count > self.planned_target_count:
            raise ValueError("decision target count exceeds planned targets")
        if self.result_target_count > self.decision_target_count:
            raise ValueError("result target count exceeds decisions")
        if (
            self.quant_available_count + self.quant_unavailable_count
            != self.decision_target_count
        ):
            raise ValueError("model availability must account for every decision")
        if self.quant_scored_count > min(
            self.quant_available_count,
            self.result_target_count,
        ):
            raise ValueError("quant scored count exceeds available settled decisions")
        expected_ratios = (
            (self.decision_coverage, self.decision_target_count, self.planned_target_count),
            (self.result_coverage, self.result_target_count, self.planned_target_count),
            (
                self.quant_availability,
                self.quant_available_count,
                self.decision_target_count,
            ),
            (
                self.quant_scored_coverage,
                self.quant_scored_count,
                self.planned_target_count,
            ),
            (self.roi_on_budget, self.profit_loss_fen, self.total_budget_fen),
            (self.roi_on_deployed, self.profit_loss_fen, self.total_stake_fen),
        )
        if any(actual != _ratio(value, total) for actual, value, total in expected_ratios):
            raise ValueError("BACKTEST_V2 derived ratio is inconsistent")
        if self.p_market.sample_count != self.result_target_count:
            raise ValueError("P_market metrics must cover every visible result")
        if (
            self.p_quant.sample_count != self.quant_scored_count
            or self.p_final.sample_count != self.quant_scored_count
        ):
            raise ValueError("P_quant/P_final metrics must use the available cohort")
        if self.settled_ticket_count > self.ticket_count:
            raise ValueError("settled ticket count exceeds frozen tickets")
        if self.profit_loss_fen != self.gross_payout_fen - self.total_settled_stake_fen:
            raise ValueError("BACKTEST_V2 profit/loss is inconsistent")
        if self.no_bet_count > self.slate_count:
            raise ValueError("BACKTEST_V2 NO_BET count exceeds slate count")
        return self


def decision_snapshot_hash(snapshot: BacktestV2DecisionSnapshot) -> str:
    return _payload_hash(snapshot, "snapshot_hash")


def backtest_v2_slice_hash(backtest_slice: BacktestV2Slice) -> str:
    return _payload_hash(backtest_slice, "slice_hash")


def _payload_hash(value: DomainModel, hash_field: str) -> str:
    payload = value.model_dump(
        mode="python",
        exclude={hash_field},
        exclude_computed_fields=True,
    )
    return sha256_text(canonical_json(payload))


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATIO_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
