from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_analysis import (
    ArtifactRefV1,
    FootballEvidenceV1,
    MarketMatchIdentityV1,
    MarketModelLineageV1,
)
from football_system.domain.market_v2 import (
    Hash,
    MarketArtifact,
    MarketKeyV2,
    MarketProbabilityDistributionV1,
    MultiMarketModel,
    OutcomeKeyV1,
    Probability,
)

MAX_V4_BYTES = 4 * 1024 * 1024


def _require_unique(values, label):
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")


class MarketReviewContextV4(MarketArtifact):
    schema_version: Literal["MARKET_REVIEW_CONTEXT_V4"] = "MARKET_REVIEW_CONTEXT_V4"
    identity: MarketMatchIdentityV1
    market_key: MarketKeyV2
    decision_cutoff: UtcDateTime
    p_market: MarketProbabilityDistributionV1 | None
    p_quant: MarketProbabilityDistributionV1 | None
    market_status: Literal["AVAILABLE", "MARKET_UNAVAILABLE"]
    quant_status: Literal["AVAILABLE", "MODEL_UNAVAILABLE"]
    unavailable_reason: str | None
    data_quality: Probability
    model_lineage: MarketModelLineageV1
    evidence: tuple[FootballEvidenceV1, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def context(self):
        if tuple(e.artifact_id for e in self.evidence) != tuple(
            sorted({e.artifact_id for e in self.evidence})
        ):
            raise ValueError("V4 evidence must have unique canonical IDs")
        if self.market_status != (
            "AVAILABLE" if self.p_market else "MARKET_UNAVAILABLE"
        ) or self.quant_status != (
            "AVAILABLE" if self.p_quant else "MODEL_UNAVAILABLE"
        ):
            raise ValueError("V4 context availability mismatch")
        if any(
            p is not None and p.market_key != self.market_key
            for p in (self.p_market, self.p_quant)
        ):
            raise ValueError("V4 context distribution uses wrong market")
        if any(
            e.match_id != self.identity.match_id
            or e.ingested_at_utc > self.decision_cutoff
            for e in self.evidence
        ):
            raise ValueError("V4 evidence mismatch")
        return self


class PacketMarketUnitV4(MultiMarketModel):
    review_context: MarketReviewContextV4
    review_context_id: Identifier
    review_context_hash: Hash
    evidence_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def binding(self):
        c = self.review_context
        if (self.review_context_id, self.review_context_hash) != (
            c.artifact_id,
            c.content_hash,
        ) or self.evidence_ids != tuple(e.artifact_id for e in c.evidence):
            raise ValueError("V4 exact review context/evidence binding mismatch")
        return self


class AnalysisPacketV4(MarketArtifact):
    schema_version: Literal["ANALYSIS_PACKET_V4"] = "ANALYSIS_PACKET_V4"
    analysis: ArtifactRefV1
    market_units: tuple[PacketMarketUnitV4, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def units(self):
        keys = tuple(
            (u.review_context.identity.match_id, u.review_context.market_key.canonical)
            for u in self.market_units
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("V4 requires unique ordered match-market units")
        return self


class MarketScenarioV4(MultiMarketModel):
    scenario_id: Identifier
    scenario_type: Literal["MAIN", "SECONDARY", "UPSET"]
    description: str = Field(min_length=1, max_length=2000)
    outcomes: tuple[OutcomeKeyV1, ...] = Field(max_length=31)
    trigger_conditions: tuple[
        Annotated[str, Field(min_length=1, max_length=2000)], ...
    ] = Field(max_length=16)
    evidence_refs: tuple[Identifier, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_refs(self):
        _require_unique(self.outcomes, "scenario outcomes")
        _require_unique(self.evidence_refs, "scenario evidence refs")
        return self


class MarketCounterScenarioV4(MultiMarketModel):
    if_scenario_id: Identifier
    alternative_scenario_id: Identifier
    fails_outcomes: tuple[OutcomeKeyV1, ...] = Field(max_length=31)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_refs: tuple[Identifier, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_refs(self):
        _require_unique(self.fails_outcomes, "counter-scenario failed outcomes")
        _require_unique(self.evidence_refs, "counter-scenario evidence refs")
        return self


class MarketReviewCommonV4(MultiMarketModel):
    match_id: Identifier
    market_key: MarketKeyV2
    review_context_id: Identifier
    review_context_hash: Hash
    limitations: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded_text(self):
        if any(not s.strip() or len(s) > 2000 for s in self.limitations):
            raise ValueError("invalid review limitations")
        _require_unique(self.limitations, "review limitations")
        return self


class ValidMarketReviewV4(MarketReviewCommonV4):
    status: Literal["VALID"] = "VALID"
    p_llm: MarketProbabilityDistributionV1
    assessment_confidence: Probability
    scenarios: tuple[MarketScenarioV4, ...] = Field(max_length=16)
    preferred_outcomes: tuple[OutcomeKeyV1, ...] = Field(max_length=31)
    avoid_outcomes: tuple[OutcomeKeyV1, ...] = Field(max_length=31)
    counter_scenarios: tuple[MarketCounterScenarioV4, ...] = Field(max_length=16)
    risk_tags: tuple[str, ...] = Field(max_length=32)
    reasoning_summary: str = Field(min_length=1, max_length=6000)
    evidence_refs: tuple[Identifier, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def market(self):
        if self.p_llm.market_key != self.market_key:
            raise ValueError("P_llm market mismatch")
        for keys in (
            self.preferred_outcomes,
            self.avoid_outcomes,
            *(s.outcomes for s in self.scenarios),
            *(s.fails_outcomes for s in self.counter_scenarios),
        ):
            _require_unique(keys, "review outcomes")
            if any(k not in self.market_key.catalog for k in keys):
                raise ValueError("review outcome catalog mismatch")
        if set(self.preferred_outcomes) & set(self.avoid_outcomes):
            raise ValueError("preferred and avoid outcomes must not overlap")
        scenario_ids = tuple(s.scenario_id for s in self.scenarios)
        _require_unique(scenario_ids, "scenario IDs")
        known = set(scenario_ids)
        if any(
            c.if_scenario_id not in known or c.alternative_scenario_id not in known
            for c in self.counter_scenarios
        ):
            raise ValueError("unknown counter-scenario reference in market review")
        if any(not t.strip() or len(t) > 160 for t in self.risk_tags):
            raise ValueError("invalid risk tag")
        _require_unique(self.risk_tags, "review risk tags")
        _require_unique(self.evidence_refs, "review evidence refs")
        return self


class UnavailableMarketReviewV4(MarketReviewCommonV4):
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    failure_code: Literal[
        "MODEL_UNAVAILABLE",
        "INSUFFICIENT_EVIDENCE",
        "INVALID_CONTEXT",
        "SKIPPED_DISABLED",
    ] = "MODEL_UNAVAILABLE"


MarketReviewV4 = Annotated[
    ValidMarketReviewV4 | UnavailableMarketReviewV4, Field(discriminator="status")
]


class LLMReviewV4(MultiMarketModel):
    schema_version: Literal["LLM_REVIEW_V4"] = "LLM_REVIEW_V4"
    analysis_id: Identifier
    packet_id: Identifier
    packet_hash: Hash
    market_reviews: tuple[MarketReviewV4, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self):
        keys = tuple((r.match_id, r.market_key.canonical) for r in self.market_reviews)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("review requires ordered unique match-market keys")
        return self


class ImportedReviewV4(MarketArtifact):
    schema_version: Literal["IMPORTED_LLM_REVIEW_V4"] = "IMPORTED_LLM_REVIEW_V4"
    packet: AnalysisPacketV4
    submission: LLMReviewV4
    raw_review_json: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=1, max_length=MAX_V4_BYTES
    )
    raw_review_hash: Hash

    @model_validator(mode="after")
    def checked(self):
        from football_system.domain.services.review_v4 import (
            check_review,
            parse_v4_json,
        )
        import hashlib

        raw = self.raw_review_json.encode("utf-8")
        if (
            hashlib.sha256(raw).hexdigest() != self.raw_review_hash
            or LLMReviewV4.model_validate(parse_v4_json(raw)) != self.submission
        ):
            raise ValueError("V4 raw review changed")
        check_review(self.packet, self.submission)
        return self


class GenericFusionPolicyV1(MultiMarketModel):
    name: Literal["GENERIC_LLM_REVIEW_DELTA_V1"] = "GENERIC_LLM_REVIEW_DELTA_V1"
    max_probability_delta: Probability = Decimal("0.08")


class GenericFusionUnitV1(MultiMarketModel):
    unit_id: Identifier
    market_key: MarketKeyV2
    match_id: Identifier
    p_final: MarketProbabilityDistributionV1 | None
    influence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    fallback_code: str | None


class GenericFusionRunV1(MarketArtifact):
    schema_version: Literal["GENERIC_FUSION_RUN_V1"] = "GENERIC_FUSION_RUN_V1"
    analysis: ArtifactRefV1
    review: ArtifactRefV1
    policy: GenericFusionPolicyV1
    results: tuple[GenericFusionUnitV1, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def keys(self):
        keys = tuple((r.match_id, r.market_key.canonical) for r in self.results)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("fusion units must be ordered and unique")
        return self
