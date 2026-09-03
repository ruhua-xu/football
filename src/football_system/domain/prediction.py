from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import Field, model_validator

from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    normalize_utc,
)
from football_system.domain.market import MarketKey, ThreeWayProbability

SHA256_PATTERN = r"^[0-9a-f]{64}$"


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


class QuantModelTrainingFactRef(DomainModel):
    sequence: int = Field(ge=0, strict=True)
    match_result_id: Identifier
    match_id: Identifier
    source_payload_hash: str = Field(pattern=SHA256_PATTERN)
    fact_hash: str = Field(pattern=SHA256_PATTERN)


class QuantModelStateArtifact(DomainModel):
    quant_model_state_id: Identifier
    analysis_run_id: Identifier
    model_name: Identifier
    model_version: Identifier
    calibration_label: Identifier
    config_json: str = Field(min_length=2)
    config_hash: str = Field(pattern=SHA256_PATTERN)
    cutoff_at_utc: UtcDateTime
    season_id: Identifier | None = None
    state_json: str = Field(min_length=2)
    state_hash: str = Field(pattern=SHA256_PATTERN)
    state_payload_hash: str = Field(pattern=SHA256_PATTERN)
    training_data_hash: str = Field(pattern=SHA256_PATTERN)
    training_facts: tuple[QuantModelTrainingFactRef, ...] = ()
    generated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if self.generated_at_utc < self.cutoff_at_utc:
            raise ValueError("model state cannot be generated before its cutoff")
        config = _canonical_json_object(self.config_json, "model config")
        state = _canonical_json_object(self.state_json, "model state")
        if _sha256(self.config_json) != self.config_hash:
            raise ValueError("model config hash does not match canonical JSON")
        if _sha256(self.state_json) != self.state_payload_hash:
            raise ValueError("model state payload hash does not match canonical JSON")
        expected_state_fields = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "calibration_label": self.calibration_label,
            "config_hash": self.config_hash,
            "state_hash": self.state_hash,
            "training_data_hash": self.training_data_hash,
        }
        if any(state.get(key) != value for key, value in expected_state_fields.items()):
            raise ValueError("model state JSON does not match artifact lineage")
        if not isinstance(config, dict) or not isinstance(state, dict):
            raise ValueError("model config and state JSON must be objects")
        state_cutoff = state.get("cutoff_at_utc")
        if not isinstance(state_cutoff, str) or _parse_utc(state_cutoff) != (
            self.cutoff_at_utc
        ):
            raise ValueError("model state cutoff does not match artifact cutoff")
        sequences = tuple(fact.sequence for fact in self.training_facts)
        if sequences != tuple(range(len(self.training_facts))):
            raise ValueError("model training fact sequence must be contiguous")
        result_ids = tuple(fact.match_result_id for fact in self.training_facts)
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("model training result IDs must be unique")
        state_facts = state.get("training_facts")
        expected_facts = [
            {
                "sequence": fact.sequence,
                "match_result_id": fact.match_result_id,
                "match_id": fact.match_id,
                "source_payload_hash": fact.source_payload_hash,
                "fact_hash": fact.fact_hash,
            }
            for fact in self.training_facts
        ]
        if (
            not isinstance(state_facts, list)
            or not all(isinstance(item, dict) for item in state_facts)
            or [
                {
                    "sequence": item.get("sequence"),
                    "match_result_id": item.get("match_result_id"),
                    "match_id": item.get("match_id"),
                    "source_payload_hash": item.get("source_payload_hash"),
                    "fact_hash": item.get("fact_hash"),
                }
                for item in state_facts
            ]
            != expected_facts
        ):
            raise ValueError("model state JSON does not match training fact references")
        return self


class QuantModelEvaluationStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class QuantModelEvaluation(DomainModel):
    quant_model_evaluation_id: Identifier
    analysis_run_id: Identifier
    quant_model_state_id: Identifier
    match_id: Identifier
    market: MarketKey
    status: QuantModelEvaluationStatus
    unavailable_reason: Identifier | None = None
    probabilities: ThreeWayProbability | None = None
    output_json: str = Field(min_length=2)
    output_hash: str = Field(pattern=SHA256_PATTERN)
    model_prediction_hash: str = Field(pattern=SHA256_PATTERN)
    evaluated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        output = _canonical_json_object(self.output_json, "model output")
        if _sha256(self.output_json) != self.output_hash:
            raise ValueError("model output hash does not match canonical JSON")
        if output.get("match_id") != self.match_id:
            raise ValueError("model output match does not match evaluation")
        if output.get("status") != self.status.value:
            raise ValueError("model output status does not match evaluation")
        if output.get("prediction_hash") != self.model_prediction_hash:
            raise ValueError("model prediction hash does not match output JSON")
        if self.status is QuantModelEvaluationStatus.AVAILABLE:
            if self.probabilities is None or self.unavailable_reason is not None:
                raise ValueError(
                    "available model evaluation requires probabilities and no reason"
                )
            if output.get("reason") is not None:
                raise ValueError("available model output cannot contain a reason")
            if (
                ThreeWayProbability.model_validate(output.get("probabilities"))
                != self.probabilities
            ):
                raise ValueError("model output probabilities do not match evaluation")
        else:
            if self.probabilities is not None or self.unavailable_reason is None:
                raise ValueError(
                    "unavailable model evaluation requires a reason and no probabilities"
                )
            if output.get("probabilities") is not None or output.get("reason") != (
                self.unavailable_reason
            ):
                raise ValueError("model output reason does not match evaluation")
        return self


class ModelQuantPrediction(DomainModel):
    prediction_id: Identifier
    analysis_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    probabilities: ThreeWayProbability
    quant_model_evaluation_id: Identifier
    method: Identifier
    method_version: Identifier
    generated_at_utc: UtcDateTime


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
    p_quant: QuantPrediction | ModelQuantPrediction | None = None


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


def _canonical_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    if (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        != value
    ):
        raise ValueError(f"{label} JSON must be canonical")
    return payload


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    return normalize_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
