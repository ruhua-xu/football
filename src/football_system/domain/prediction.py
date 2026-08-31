from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import MarketKey, ThreeWayProbability


class FusionPolicyName(StrEnum):
    QUANT_ONLY_V1 = "QUANT_ONLY_V1"
    MARKET_QUANT_BLEND_V1 = "MARKET_QUANT_BLEND_V1"
    LLM_REVIEW_DELTA_V1 = "LLM_REVIEW_DELTA_V1"


class MarketPrediction(DomainModel):
    prediction_id: Identifier
    analysis_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    probabilities: ThreeWayProbability
    input_snapshot_ids: tuple[Identifier, ...]
    devig_method: str = "NORMALIZED_INVERSE_V1"
    devig_version: str = "1"
    overround: Decimal = Field(gt=0)
    generated_at_utc: UtcDateTime


class QuantPrediction(DomainModel):
    prediction_id: Identifier
    analysis_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    probabilities: ThreeWayProbability
    manual_input_id: Identifier
    input_payload_hash: Identifier
    method: str = "MANUAL"
    method_version: str = "MANUAL_V1"
    entered_at_utc: UtcDateTime


class ManualQuantInput(DomainModel):
    input_id: Identifier
    match_id: Identifier
    market: MarketKey
    probabilities: ThreeWayProbability
    available_at_utc: UtcDateTime
    payload_hash: Identifier


class FinalPrediction(DomainModel):
    prediction_id: Identifier
    analysis_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    probabilities: ThreeWayProbability
    market_prediction_id: Identifier | None = None
    quant_prediction_id: Identifier | None = None
    llm_assessment_id: Identifier | None = None
    fusion_policy: FusionPolicyName
    fusion_version: str = "1"
    fusion_config_json: str
    fallback_code: str | None = None
    confidence: Decimal = Field(default=Decimal(1), ge=0, le=1)
    generated_at_utc: UtcDateTime


class FusionInputs(DomainModel):
    analysis_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    p_market: MarketPrediction | None = None
    p_quant: QuantPrediction | None = None


class FusionConfig(DomainModel):
    quant_weight: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)


class FusionInputsUnavailable(ValueError):
    pass


class FusionPolicy(Protocol):
    name: FusionPolicyName
    version: str

    def fuse(
        self,
        inputs: FusionInputs,
        config: FusionConfig,
        generated_at_utc: UtcDateTime,
    ) -> FinalPrediction: ...
