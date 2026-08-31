from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import SelectionKey, ThreeWayProbability

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
    avoid_outcomes: tuple[ReviewOutcomeOpinion, ...] = Field(
        default=(), max_length=16
    )
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
