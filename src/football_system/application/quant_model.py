from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from football_system.domain.common import UtcDateTime, stable_id
from football_system.domain.market import MarketKey
from football_system.domain.prediction import (
    ModelQuantPrediction,
    QuantModelEvaluation,
    QuantModelEvaluationStatus,
    QuantModelStateArtifact,
    QuantModelTrainingFactRef,
)
from football_system.domain.services.elo_baseline import (
    EloBaselinePrediction,
    EloBaselineState,
    EloThreeWayBaseline,
)

MVP_INPUT_MANIFEST_V3 = "MVP_INPUT_MANIFEST_V3"


def freeze_elo_model_state(
    *,
    analysis_run_id: str,
    baseline: EloThreeWayBaseline,
    state: EloBaselineState,
    generated_at_utc: UtcDateTime,
) -> QuantModelStateArtifact:
    if state.config_hash != baseline.config_hash:
        raise ValueError("Elo state config hash does not match the baseline")
    config_json = _canonical_json(baseline.config.model_dump(mode="json"))
    state_json = _canonical_json(state.model_dump(mode="json"))
    return QuantModelStateArtifact(
        quant_model_state_id=stable_id(
            "quant-model-state",
            analysis_run_id,
            state.model_name,
            state.model_version,
            state.state_hash,
        ),
        analysis_run_id=analysis_run_id,
        model_name=state.model_name,
        model_version=state.model_version,
        calibration_label=state.calibration_label,
        config_json=config_json,
        config_hash=state.config_hash,
        cutoff_at_utc=state.cutoff_at_utc,
        season_id=state.season_id,
        state_json=state_json,
        state_hash=state.state_hash,
        state_payload_hash=_sha256(state_json),
        training_data_hash=state.training_data_hash,
        training_facts=tuple(
            QuantModelTrainingFactRef(
                sequence=fact.sequence,
                match_result_id=fact.match_result_id,
                match_id=fact.match_id,
                source_payload_hash=fact.source_payload_hash,
                fact_hash=fact.fact_hash,
            )
            for fact in state.training_facts
        ),
        generated_at_utc=generated_at_utc,
    )


def freeze_elo_evaluation(
    *,
    analysis_run_id: str,
    model_state: QuantModelStateArtifact,
    prediction: EloBaselinePrediction,
    market: MarketKey,
    evaluated_at_utc: UtcDateTime,
) -> QuantModelEvaluation:
    if model_state.analysis_run_id != analysis_run_id:
        raise ValueError("Elo model state belongs to a different AnalysisRun")
    if (
        prediction.model_name != model_state.model_name
        or prediction.model_version != model_state.model_version
        or prediction.calibration_label != model_state.calibration_label
        or prediction.config_hash != model_state.config_hash
        or prediction.state_hash != model_state.state_hash
        or prediction.training_data_hash != model_state.training_data_hash
        or prediction.training_result_ids
        != tuple(fact.match_result_id for fact in model_state.training_facts)
    ):
        raise ValueError("Elo prediction does not belong to the frozen model state")
    if prediction.match_id in {fact.match_id for fact in model_state.training_facts}:
        raise ValueError("Elo target match cannot occur in model training facts")
    if evaluated_at_utc < model_state.cutoff_at_utc:
        raise ValueError("Elo evaluation cannot precede its state cutoff")
    output_json = _canonical_json(prediction.model_dump(mode="json"))
    status = QuantModelEvaluationStatus(prediction.status.value)
    return QuantModelEvaluation(
        quant_model_evaluation_id=stable_id(
            "quant-model-evaluation",
            analysis_run_id,
            model_state.quant_model_state_id,
            prediction.match_id,
            market.canonical,
            prediction.prediction_hash,
        ),
        analysis_run_id=analysis_run_id,
        quant_model_state_id=model_state.quant_model_state_id,
        match_id=prediction.match_id,
        market=market,
        status=status,
        unavailable_reason=(
            prediction.reason.value if prediction.reason is not None else None
        ),
        probabilities=prediction.probabilities,
        output_json=output_json,
        output_hash=_sha256(output_json),
        model_prediction_hash=prediction.prediction_hash,
        evaluated_at_utc=evaluated_at_utc,
    )


def project_available_model_quant(
    *,
    model_state: QuantModelStateArtifact,
    evaluation: QuantModelEvaluation,
) -> ModelQuantPrediction | None:
    if evaluation.quant_model_state_id != model_state.quant_model_state_id:
        raise ValueError("model evaluation belongs to a different state")
    if evaluation.analysis_run_id != model_state.analysis_run_id:
        raise ValueError("model evaluation belongs to a different AnalysisRun")
    if evaluation.status is QuantModelEvaluationStatus.UNAVAILABLE:
        return None
    if evaluation.probabilities is None:
        raise ValueError("available model evaluation is missing probabilities")
    return ModelQuantPrediction(
        prediction_id=stable_id(
            "p-quant",
            evaluation.analysis_run_id,
            evaluation.match_id,
            evaluation.market.canonical,
        ),
        analysis_run_id=evaluation.analysis_run_id,
        match_id=evaluation.match_id,
        market=evaluation.market,
        probabilities=evaluation.probabilities,
        quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
        method=model_state.model_name,
        method_version=model_state.model_version,
        generated_at_utc=evaluation.evaluated_at_utc,
    )


def build_model_input_manifest_json(
    *,
    competitions: Iterable,
    teams: Iterable,
    matches: Iterable,
    mappings: Iterable,
    market_snapshots: Iterable,
    sporttery_snapshots: Iterable,
    model_states: Iterable[QuantModelStateArtifact],
) -> str:
    return _canonical_json(
        {
            "version": MVP_INPUT_MANIFEST_V3,
            "competitions": _manifest_records(competitions, "competition_id"),
            "teams": _manifest_records(teams, "team_id"),
            "matches": _manifest_records(matches, "match_id"),
            "provider_mappings": _manifest_records(mappings, "mapping_id"),
            "market_odds_snapshots": _manifest_records(market_snapshots, "snapshot_id"),
            "sporttery_bonus_snapshots": _manifest_records(
                sporttery_snapshots, "snapshot_id"
            ),
            "quant_model_states": _manifest_records(
                model_states, "quant_model_state_id"
            ),
        }
    )


def _manifest_records(items: Iterable, identity_field: str) -> tuple[dict, ...]:
    records = []
    for item in sorted(items, key=lambda value: getattr(value, identity_field)):
        record = item.model_dump(mode="json")
        if "quotes" in record:
            record["quotes"] = sorted(
                record["quotes"], key=lambda quote: quote["selection"]
            )
        records.append(record)
    return tuple(records)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
