from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import (
    MarketKey,
    SelectionKey,
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.prediction import (
    ModelQuantPrediction,
    QuantModelEvaluationStatus,
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"
MAX_PACKET_MATCHES = 256
MAX_CONTRACT_FILE_BYTES = 1_000_000
MAX_CONTRACT_DECIMAL_PLACES = 18
MAX_CONTRACT_SIGNIFICANT_DIGITS = 24
ExactJsonText = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1)]


class AnalysisPacketRun(DomainModel):
    analysis_run_id: Identifier
    as_of_at_utc: UtcDateTime
    completed_at_utc: UtcDateTime
    pipeline_version: str
    code_revision: str
    input_manifest_version: str
    input_manifest_hash: str = Field(pattern=SHA256_PATTERN)


class AnalysisPacketRunV3(AnalysisPacketRun):
    started_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_timeline(self) -> AnalysisPacketRunV3:
        if not (self.as_of_at_utc <= self.started_at_utc <= self.completed_at_utc):
            raise ValueError("analysis packet V3 run timeline is inconsistent")
        return self


class PacketMarketPrediction(DomainModel):
    prediction_id: Identifier
    probabilities: ThreeWayProbability
    input_snapshot_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_inputs(self) -> PacketMarketPrediction:
        _require_unique(self.input_snapshot_ids, "market prediction input snapshots")
        _validate_probability_precision(self.probabilities, "market probabilities")
        return self


class PacketQuantPrediction(DomainModel):
    prediction_id: Identifier
    probabilities: ThreeWayProbability
    manual_input_id: Identifier
    input_payload_hash: Identifier

    @model_validator(mode="after")
    def validate_probabilities(self) -> PacketQuantPrediction:
        _validate_probability_precision(self.probabilities, "quant probabilities")
        return self


class AnalysisPacketMatch(DomainModel):
    match_id: Identifier
    competition_id: Identifier
    competition_name: str = Field(min_length=1, max_length=160)
    home_team_id: Identifier
    home_team_name: str = Field(min_length=1, max_length=160)
    away_team_id: Identifier
    away_team_name: str = Field(min_length=1, max_length=160)
    kickoff_at_utc: UtcDateTime
    market_key: str = Field(min_length=1, max_length=120)
    context_hash: Identifier
    p_market: PacketMarketPrediction
    p_quant: PacketQuantPrediction
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_evidence(self) -> AnalysisPacketMatch:
        _require_unique(self.evidence_ids, "packet evidence IDs")
        return self


class AnalysisPacketSource(DomainModel):
    analysis_run: AnalysisPacketRun
    matches: tuple[AnalysisPacketMatch, ...] = Field(
        min_length=1, max_length=MAX_PACKET_MATCHES
    )

    @model_validator(mode="after")
    def validate_matches(self) -> AnalysisPacketSource:
        match_ids = [match.match_id for match in self.matches]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("analysis packet source requires unique matches")
        return self


class AnalysisPacket(DomainModel):
    schema_version: Literal["ANALYSIS_PACKET_V1"]
    packet_id: Identifier
    generated_at_utc: UtcDateTime
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    analysis_run: AnalysisPacketRun
    matches: tuple[AnalysisPacketMatch, ...] = Field(
        min_length=1, max_length=MAX_PACKET_MATCHES
    )

    @model_validator(mode="after")
    def validate_matches(self) -> AnalysisPacket:
        match_ids = [match.match_id for match in self.matches]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("analysis packet requires unique matches")
        if self.generated_at_utc < self.analysis_run.completed_at_utc:
            raise ValueError("analysis packet cannot predate run completion")
        return self


class PacketInternationalOdds(DomainModel):
    snapshot_id: Identifier
    provider_id: Identifier
    provider_name: str = Field(min_length=1, max_length=160)
    bookmaker_id: Identifier
    bookmaker_name: str = Field(min_length=1, max_length=160)
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    payload_hash: Identifier
    odds: ThreeWayMarketOdds


class PacketSportteryOdds(DomainModel):
    snapshot_id: Identifier
    provider_id: Identifier
    provider_name: str = Field(min_length=1, max_length=160)
    sporttery_match_no: Identifier
    sale_status: str = Field(min_length=1, max_length=64)
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    payload_hash: Identifier
    odds: ThreeWayFixedBonus


class PacketRestDays(DomainModel):
    home: int | None = Field(default=None, ge=0, le=365)
    away: int | None = Field(default=None, ge=0, le=365)


class PacketEvidence(DomainModel):
    evidence_id: Identifier
    category: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=10_000)
    source_kind: str = Field(min_length=1, max_length=80)
    source_name: str = Field(min_length=1, max_length=320)
    source_reference: str = Field(min_length=1, max_length=500)
    source_record_id: Identifier
    source_payload_hash: Identifier
    observed_at_utc: UtcDateTime
    available_at_utc: UtcDateTime


class PacketDataQualityStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class PacketDataQuality(DomainModel):
    status: PacketDataQualityStatus
    score: Decimal = Field(ge=0, le=1)
    available_fields: tuple[str, ...] = Field(default=(), max_length=32)
    missing_fields: tuple[str, ...] = Field(default=(), max_length=32)
    notes: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_fields(self) -> PacketDataQuality:
        _validate_decimal_precision(self.score, "packet data quality score")
        _require_unique(self.available_fields, "available review context fields")
        _require_unique(self.missing_fields, "missing review context fields")
        _require_unique(self.notes, "data quality notes")
        if set(self.available_fields) & set(self.missing_fields):
            raise ValueError(
                "review context fields cannot be both available and missing"
            )
        return self


class MatchReviewContext(DomainModel):
    sporttery_odds: PacketSportteryOdds | None = None
    international_odds: PacketInternationalOdds | None = None
    odds_movement_summary: str | None = Field(default=None, max_length=4_000)
    recent_form: str | None = Field(default=None, max_length=4_000)
    home_away_form: str | None = Field(default=None, max_length=4_000)
    rest_days: PacketRestDays | None = None
    schedule_context: str | None = Field(default=None, max_length=4_000)
    injuries: tuple[str, ...] = Field(default=(), max_length=128)
    suspensions: tuple[str, ...] = Field(default=(), max_length=128)
    expected_lineup: tuple[str, ...] = Field(default=(), max_length=64)
    confirmed_lineup: tuple[str, ...] = Field(default=(), max_length=64)
    evidence: tuple[PacketEvidence, ...] = Field(default=(), max_length=128)
    data_quality: PacketDataQuality

    @model_validator(mode="after")
    def validate_collections(self) -> MatchReviewContext:
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        _require_unique(evidence_ids, "review context evidence IDs")
        _require_unique(self.injuries, "review context injuries")
        _require_unique(self.suspensions, "review context suspensions")
        _require_unique(self.expected_lineup, "expected lineup players")
        _require_unique(self.confirmed_lineup, "confirmed lineup players")
        return self


class AnalysisPacketMatchSourceV2(AnalysisPacketMatch):
    review_context: MatchReviewContext

    @model_validator(mode="after")
    def validate_context_evidence(self) -> AnalysisPacketMatchSourceV2:
        context_evidence_ids = tuple(
            item.evidence_id for item in self.review_context.evidence
        )
        if set(self.evidence_ids) != set(context_evidence_ids):
            raise ValueError("packet evidence IDs must match review context evidence")
        return self


class AnalysisPacketMatchV2(AnalysisPacketMatchSourceV2):
    review_context_id: Identifier
    review_context_hash: str = Field(pattern=SHA256_PATTERN)


class AnalysisPacketSourceV2(DomainModel):
    analysis_run: AnalysisPacketRun
    matches: tuple[AnalysisPacketMatchSourceV2, ...] = Field(
        min_length=1, max_length=MAX_PACKET_MATCHES
    )

    @model_validator(mode="after")
    def validate_matches(self) -> AnalysisPacketSourceV2:
        match_ids = [match.match_id for match in self.matches]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("analysis packet V2 source requires unique matches")
        return self


class AnalysisPacketV2(DomainModel):
    schema_version: Literal["ANALYSIS_PACKET_V2"]
    packet_id: Identifier
    generated_at_utc: UtcDateTime
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    analysis_run: AnalysisPacketRun
    matches: tuple[AnalysisPacketMatchV2, ...] = Field(
        min_length=1, max_length=MAX_PACKET_MATCHES
    )

    @model_validator(mode="after")
    def validate_matches(self) -> AnalysisPacketV2:
        match_ids = [match.match_id for match in self.matches]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("analysis packet V2 requires unique matches")
        if self.generated_at_utc < self.analysis_run.completed_at_utc:
            raise ValueError("analysis packet cannot predate run completion")
        return self


class PacketQuantModelStateV3(DomainModel):
    quant_model_state_id: Identifier
    analysis_run_id: Identifier
    model_name: Identifier
    model_version: Identifier
    calibration_label: Identifier
    config_hash: str = Field(pattern=SHA256_PATTERN)
    cutoff_at_utc: UtcDateTime
    season_id: Identifier | None = None
    state_hash: str = Field(pattern=SHA256_PATTERN)
    state_payload_hash: str = Field(pattern=SHA256_PATTERN)
    training_data_hash: str = Field(pattern=SHA256_PATTERN)
    training_fact_count: int = Field(ge=0, strict=True)
    training_match_ids: tuple[Identifier, ...] = ()
    training_result_ids: tuple[Identifier, ...] = ()
    generated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_training_lineage(self) -> PacketQuantModelStateV3:
        if self.generated_at_utc < self.cutoff_at_utc:
            raise ValueError("packet model state cannot be generated before its cutoff")
        if not (
            self.training_fact_count
            == len(self.training_match_ids)
            == len(self.training_result_ids)
        ):
            raise ValueError("packet model state training count is inconsistent")
        if len(self.training_result_ids) != len(set(self.training_result_ids)):
            raise ValueError("packet model training result IDs must be unique")
        return self


class PacketQuantModelEvaluationV3(DomainModel):
    quant_model_evaluation_id: Identifier
    analysis_run_id: Identifier
    quant_model_state_id: Identifier
    match_id: Identifier
    market: MarketKey
    status: QuantModelEvaluationStatus
    unavailable_reason: Identifier | None = None
    probabilities: ThreeWayProbability | None = None
    output_hash: str = Field(pattern=SHA256_PATTERN)
    model_prediction_hash: str = Field(pattern=SHA256_PATTERN)
    evaluated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_availability(self) -> PacketQuantModelEvaluationV3:
        if self.status is QuantModelEvaluationStatus.AVAILABLE:
            if self.probabilities is None or self.unavailable_reason is not None:
                raise ValueError(
                    "available packet model evaluation requires probabilities"
                )
        elif self.probabilities is not None or self.unavailable_reason is None:
            raise ValueError(
                "unavailable packet model evaluation requires a reason only"
            )
        return self


class PacketManualQuantLineageV3(DomainModel):
    source_kind: Literal["MANUAL"] = "MANUAL"
    status: Literal["AVAILABLE"] = "AVAILABLE"
    prediction: PacketQuantPrediction


class PacketModelQuantLineageV3(DomainModel):
    source_kind: Literal["MODEL"] = "MODEL"
    status: QuantModelEvaluationStatus
    evaluation: PacketQuantModelEvaluationV3
    prediction: ModelQuantPrediction | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> PacketModelQuantLineageV3:
        if self.status is not self.evaluation.status:
            raise ValueError("packet model status must match its evaluation")
        if self.status is QuantModelEvaluationStatus.AVAILABLE:
            if self.prediction is None:
                raise ValueError("available packet model lineage requires a prediction")
            if (
                self.prediction.quant_model_evaluation_id
                != self.evaluation.quant_model_evaluation_id
                or self.prediction.analysis_run_id != self.evaluation.analysis_run_id
                or self.prediction.match_id != self.evaluation.match_id
                or self.prediction.market != self.evaluation.market
                or self.prediction.probabilities != self.evaluation.probabilities
            ):
                raise ValueError(
                    "packet model prediction has inconsistent evaluation lineage"
                )
        elif self.prediction is not None:
            raise ValueError(
                "unavailable packet model lineage cannot contain a prediction"
            )
        return self


PacketQuantLineageV3 = Annotated[
    PacketManualQuantLineageV3 | PacketModelQuantLineageV3,
    Field(discriminator="source_kind"),
]


class AnalysisPacketMatchSourceV3(AnalysisPacketMatchSourceV2):
    p_quant: PacketQuantLineageV3

    @model_validator(mode="after")
    def validate_quant_lineage(self) -> AnalysisPacketMatchSourceV3:
        if isinstance(self.p_quant, PacketModelQuantLineageV3):
            evaluation = self.p_quant.evaluation
            if (
                evaluation.match_id != self.match_id
                or evaluation.market.canonical != self.market_key
            ):
                raise ValueError(
                    "packet model evaluation does not match its match context"
                )
        return self


class AnalysisPacketMatchV3(AnalysisPacketMatchSourceV3):
    review_context_id: Identifier
    review_context_hash: str = Field(pattern=SHA256_PATTERN)


class AnalysisPacketSourceV3(DomainModel):
    analysis_run: AnalysisPacketRunV3
    quant_model_states: tuple[PacketQuantModelStateV3, ...] = Field(
        default=(), max_length=MAX_PACKET_MATCHES
    )
    matches: tuple[AnalysisPacketMatchSourceV3, ...] = Field(
        min_length=1, max_length=MAX_PACKET_MATCHES
    )

    @model_validator(mode="after")
    def validate_lineage(self) -> AnalysisPacketSourceV3:
        _validate_packet_v3_lineage(
            self.analysis_run,
            self.quant_model_states,
            self.matches,
            "analysis packet V3 source",
        )
        return self


class AnalysisPacketV3(DomainModel):
    schema_version: Literal["ANALYSIS_PACKET_V3"]
    packet_id: Identifier
    generated_at_utc: UtcDateTime
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    analysis_run: AnalysisPacketRunV3
    quant_model_states: tuple[PacketQuantModelStateV3, ...] = Field(
        default=(), max_length=MAX_PACKET_MATCHES
    )
    matches: tuple[AnalysisPacketMatchV3, ...] = Field(
        min_length=1, max_length=MAX_PACKET_MATCHES
    )

    @model_validator(mode="after")
    def validate_lineage(self) -> AnalysisPacketV3:
        _validate_packet_v3_lineage(
            self.analysis_run,
            self.quant_model_states,
            self.matches,
            "analysis packet V3",
        )
        if self.generated_at_utc < self.analysis_run.completed_at_utc:
            raise ValueError("analysis packet cannot predate run completion")
        return self


class StoredAnalysisPacket(DomainModel):
    packet_id: Identifier
    parent_analysis_run_id: Identifier
    schema_version: str
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    packet_json: str


class ReviewScenarioType(StrEnum):
    MAIN = "MAIN"
    SECONDARY = "SECONDARY"
    UPSET = "UPSET"


class ReviewStrength(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LLMReviewFailureCode(StrEnum):
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    INVALID_CONTEXT = "INVALID_CONTEXT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


class ReviewScenario(DomainModel):
    scenario_id: Identifier
    scenario_type: ReviewScenarioType
    market_key: str = Field(min_length=1, max_length=120)
    outcome: SelectionKey
    summary: str = Field(min_length=1, max_length=2_000)
    trigger_conditions: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_collections(self) -> ReviewScenario:
        _require_unique(self.trigger_conditions, "scenario trigger conditions")
        _require_unique(self.evidence_ids, "scenario evidence IDs")
        return self


class ReviewOutcomeOpinion(DomainModel):
    market_key: str = Field(min_length=1, max_length=120)
    outcome: SelectionKey
    strength: ReviewStrength
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_evidence(self) -> ReviewOutcomeOpinion:
        _require_unique(self.evidence_ids, "opinion evidence IDs")
        return self


class ReviewCounterScenario(DomainModel):
    if_scenario_id: Identifier
    fails_outcome: SelectionKey
    alternative_scenario_id: Identifier
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_evidence(self) -> ReviewCounterScenario:
        _require_unique(self.evidence_ids, "counter-scenario evidence IDs")
        return self


class ValidLLMMatchReview(DomainModel):
    status: Literal["VALID"] = "VALID"
    match_id: Identifier
    market_key: str = Field(min_length=1, max_length=120)
    p_llm: ThreeWayProbability
    assessment_confidence: Decimal = Field(ge=0, le=1)
    scenarios: tuple[ReviewScenario, ...] = Field(default=(), max_length=16)
    preferred_outcomes: tuple[ReviewOutcomeOpinion, ...] = Field(
        default=(), max_length=16
    )
    avoid_outcomes: tuple[ReviewOutcomeOpinion, ...] = Field(default=(), max_length=16)
    counter_scenarios: tuple[ReviewCounterScenario, ...] = Field(
        default=(), max_length=16
    )
    risk_tags: tuple[str, ...] = Field(default=(), max_length=32)
    reasoning_summary: str = Field(min_length=1, max_length=4_000)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_semantics(self) -> ValidLLMMatchReview:
        _validate_probability_precision(self.p_llm, "LLM probabilities")
        _validate_decimal_precision(
            self.assessment_confidence,
            "assessment confidence",
        )
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("LLM review scenario IDs must be unique")
        known_scenarios = set(scenario_ids)
        if any(
            item.if_scenario_id not in known_scenarios
            or item.alternative_scenario_id not in known_scenarios
            for item in self.counter_scenarios
        ):
            raise ValueError("counter scenario references an unknown scenario")
        preferred = {
            (item.market_key, item.outcome) for item in self.preferred_outcomes
        }
        avoided = {(item.market_key, item.outcome) for item in self.avoid_outcomes}
        if len(preferred) != len(self.preferred_outcomes):
            raise ValueError("preferred outcome opinions must be unique")
        if len(avoided) != len(self.avoid_outcomes):
            raise ValueError("avoided outcome opinions must be unique")
        if preferred & avoided:
            raise ValueError("an outcome cannot be both preferred and avoided")
        counter_keys = {
            (item.if_scenario_id, item.fails_outcome, item.alternative_scenario_id)
            for item in self.counter_scenarios
        }
        if len(counter_keys) != len(self.counter_scenarios):
            raise ValueError("counter scenarios must be unique")
        _require_unique(self.risk_tags, "risk tags")
        _require_unique(self.limitations, "review limitations")
        return self


class UnavailableLLMMatchReview(DomainModel):
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    match_id: Identifier
    market_key: str = Field(min_length=1, max_length=120)
    failure_code: LLMReviewFailureCode
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_limitations(self) -> UnavailableLLMMatchReview:
        _require_unique(self.limitations, "review limitations")
        return self


LLMMatchReview = Annotated[
    ValidLLMMatchReview | UnavailableLLMMatchReview,
    Field(discriminator="status"),
]


class ValidLLMMatchReviewV2(ValidLLMMatchReview):
    review_context_id: Identifier
    review_context_hash: str = Field(pattern=SHA256_PATTERN)


class UnavailableLLMMatchReviewV2(UnavailableLLMMatchReview):
    review_context_id: Identifier
    review_context_hash: str = Field(pattern=SHA256_PATTERN)


LLMMatchReviewV2 = Annotated[
    ValidLLMMatchReviewV2 | UnavailableLLMMatchReviewV2,
    Field(discriminator="status"),
]


class LLMReviewSubmission(DomainModel):
    schema_version: Literal["LLM_REVIEW_V1"]
    analysis_run_id: Identifier
    packet_id: Identifier
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    match_reviews: tuple[LLMMatchReview, ...] = Field(max_length=MAX_PACKET_MATCHES)

    @model_validator(mode="after")
    def validate_matches(self) -> LLMReviewSubmission:
        match_ids = [review.match_id for review in self.match_reviews]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("LLM review requires one unique result per match")
        return self


class LLMReviewSubmissionV2(DomainModel):
    schema_version: Literal["LLM_REVIEW_V2"]
    analysis_run_id: Identifier
    packet_id: Identifier
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    match_reviews: tuple[LLMMatchReviewV2, ...] = Field(max_length=MAX_PACKET_MATCHES)

    @model_validator(mode="after")
    def validate_matches(self) -> LLMReviewSubmissionV2:
        match_ids = [review.match_id for review in self.match_reviews]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("LLM review V2 requires one unique result per match")
        return self


class LLMReviewSubmissionV3(DomainModel):
    schema_version: Literal["LLM_REVIEW_V3"]
    analysis_run_id: Identifier
    packet_id: Identifier
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    match_reviews: tuple[LLMMatchReviewV2, ...] = Field(max_length=MAX_PACKET_MATCHES)

    @model_validator(mode="after")
    def validate_matches(self) -> LLMReviewSubmissionV3:
        match_ids = [review.match_id for review in self.match_reviews]
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("LLM review V3 requires one unique result per match")
        return self


AnalysisPacketContract = AnalysisPacket | AnalysisPacketV2 | AnalysisPacketV3
AnalysisPacketSourceContract = (
    AnalysisPacketSource | AnalysisPacketSourceV2 | AnalysisPacketSourceV3
)
LLMReviewSubmissionContract = (
    LLMReviewSubmission | LLMReviewSubmissionV2 | LLMReviewSubmissionV3
)


class LLMReviewArtifact(DomainModel):
    review_artifact_id: Identifier
    parent_analysis_run_id: Identifier
    packet_id: Identifier
    packet_hash: str = Field(pattern=SHA256_PATTERN)
    review_schema_version: str
    imported_at_utc: UtcDateTime
    raw_review_json: ExactJsonText
    raw_review_hash: str = Field(pattern=SHA256_PATTERN)
    normalized_review_json: ExactJsonText
    normalized_review_hash: str = Field(pattern=SHA256_PATTERN)
    validator_version: str = "OFFLINE_REVIEW_VALIDATOR_V1"
    source_kind: Literal["OFFLINE_FILE"] = "OFFLINE_FILE"


def _validate_packet_v3_lineage(
    run: AnalysisPacketRunV3,
    states: tuple[PacketQuantModelStateV3, ...],
    matches: tuple[AnalysisPacketMatchSourceV3 | AnalysisPacketMatchV3, ...],
    label: str,
) -> None:
    match_ids = tuple(match.match_id for match in matches)
    if len(match_ids) != len(set(match_ids)):
        raise ValueError(f"{label} requires unique matches")
    state_ids = tuple(state.quant_model_state_id for state in states)
    if len(state_ids) != len(set(state_ids)):
        raise ValueError(f"{label} requires unique model states")
    referenced_state_ids = {
        match.p_quant.evaluation.quant_model_state_id
        for match in matches
        if isinstance(match.p_quant, PacketModelQuantLineageV3)
    }
    if referenced_state_ids != set(state_ids):
        raise ValueError(f"{label} model-state lineage is incomplete")
    if referenced_state_ids and run.input_manifest_version != "MVP_INPUT_MANIFEST_V3":
        raise ValueError(f"{label} model lineage requires MVP_INPUT_MANIFEST_V3")
    if any(state.analysis_run_id != run.analysis_run_id for state in states) or any(
        match.p_quant.evaluation.analysis_run_id != run.analysis_run_id
        for match in matches
        if isinstance(match.p_quant, PacketModelQuantLineageV3)
    ):
        raise ValueError(f"{label} model lineage must belong to its AnalysisRun")
    state_by_id = {state.quant_model_state_id: state for state in states}
    has_manual_lineage = any(
        isinstance(match.p_quant, PacketManualQuantLineageV3) for match in matches
    )
    if has_manual_lineage and referenced_state_ids:
        raise ValueError(f"{label} cannot mix manual and model quant lineage")
    target_match_ids = set(match_ids)
    for match in matches:
        if not isinstance(match.p_quant, PacketModelQuantLineageV3):
            continue
        state = state_by_id[match.p_quant.evaluation.quant_model_state_id]
        if state.cutoff_at_utc != run.as_of_at_utc:
            raise ValueError(f"{label} model-state cutoff must match its AnalysisRun")
        if not (
            run.started_at_utc <= state.generated_at_utc <= run.completed_at_utc
        ) or not (
            run.started_at_utc
            <= match.p_quant.evaluation.evaluated_at_utc
            <= run.completed_at_utc
        ):
            raise ValueError(f"{label} model timestamps are outside its AnalysisRun")
        if target_match_ids & set(state.training_match_ids):
            raise ValueError(f"{label} target matches cannot appear in model training")
        prediction = match.p_quant.prediction
        if prediction is not None and (
            prediction.method != state.model_name
            or prediction.method_version != state.model_version
            or not (
                run.started_at_utc
                <= prediction.generated_at_utc
                <= run.completed_at_utc
            )
        ):
            raise ValueError(f"{label} model prediction version is inconsistent")


def _require_unique(values: tuple, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _validate_probability_precision(
    probabilities: ThreeWayProbability,
    label: str,
) -> None:
    for selection, value in probabilities.items():
        _validate_decimal_precision(value, f"{label} {selection.value}")


def _validate_decimal_precision(value: Decimal, label: str) -> None:
    if value == 0:
        return
    _, digits, exponent = value.as_tuple()
    trailing_zeros = 0
    for digit in reversed(digits):
        if digit != 0:
            break
        trailing_zeros += 1
    significant_digits = len(digits) - trailing_zeros
    effective_exponent = exponent + trailing_zeros
    if (
        significant_digits > MAX_CONTRACT_SIGNIFICANT_DIGITS
        or effective_exponent < -MAX_CONTRACT_DECIMAL_PLACES
    ):
        raise ValueError(f"{label} exceeds contract decimal precision")
