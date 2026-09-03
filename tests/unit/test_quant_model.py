import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.application.quant_model import (
    MVP_INPUT_MANIFEST_V3,
    build_model_input_manifest_json,
    freeze_elo_evaluation,
    freeze_elo_model_state,
    project_available_model_quant,
)
from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.market import MarketKey, MarketType
from football_system.domain.prediction import (
    QuantModelEvaluation,
    QuantModelEvaluationStatus,
    QuantModelStateArtifact,
)
from football_system.domain.services.elo_baseline import (
    ELO_THREE_WAY_BASELINE_V1,
    EloBaselineConfig,
    EloPredictionRequest,
    EloRegularTimeResult,
    EloThreeWayBaseline,
)

UTC = timezone.utc
CUTOFF = datetime(2024, 8, 10, 12, tzinfo=UTC)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)


def _result() -> EloRegularTimeResult:
    return EloRegularTimeResult(
        match_result_id="result-1-v1",
        match_id="history-1",
        season_id="2024",
        home_team_id="alpha",
        away_team_id="bravo",
        kickoff_at_utc=CUTOFF - timedelta(days=2),
        available_at_utc=CUTOFF - timedelta(days=2, hours=-2),
        ingested_at_utc=CUTOFF - timedelta(days=1),
        home_goals=2,
        away_goals=0,
        payload_hash=match_result_payload_sha256(2, 0),
    )


def _request(*, away_team_id: str = "bravo") -> EloPredictionRequest:
    return EloPredictionRequest(
        match_id="target-1",
        season_id="2024",
        home_team_id="alpha",
        away_team_id=away_team_id,
        kickoff_at_utc=CUTOFF + timedelta(days=1),
        cutoff_at_utc=CUTOFF,
    )


def _frozen_state(
    *, minimum_prior_matches: int = 1
) -> tuple[EloThreeWayBaseline, QuantModelStateArtifact]:
    baseline = EloThreeWayBaseline(
        EloBaselineConfig(
            minimum_prior_matches=minimum_prior_matches,
            home_advantage=Decimal("80"),
        )
    )
    state = baseline.rebuild_state(
        (_result(),),
        CUTOFF,
        target_season_id="2024",
    )
    artifact = freeze_elo_model_state(
        analysis_run_id="run-1",
        baseline=baseline,
        state=state,
        generated_at_utc=CUTOFF,
    )
    return baseline, artifact


def test_available_elo_output_projects_to_generic_quant_prediction() -> None:
    baseline, state = _frozen_state()
    prediction = baseline.predict(_request(), (_result(),))
    evaluation = freeze_elo_evaluation(
        analysis_run_id="run-1",
        model_state=state,
        prediction=prediction,
        market=MARKET,
        evaluated_at_utc=CUTOFF,
    )

    projected = project_available_model_quant(
        model_state=state,
        evaluation=evaluation,
    )

    assert evaluation.status is QuantModelEvaluationStatus.AVAILABLE
    assert projected is not None
    assert projected.probabilities == prediction.probabilities
    assert projected.method == ELO_THREE_WAY_BASELINE_V1
    assert projected.quant_model_evaluation_id == (evaluation.quant_model_evaluation_id)
    assert len(state.state_payload_hash) == 64
    assert state.training_facts[0].match_result_id == "result-1-v1"


def test_unavailable_elo_output_remains_auditable_without_quant_projection() -> None:
    baseline, state = _frozen_state(minimum_prior_matches=2)
    prediction = baseline.predict(_request(away_team_id="promoted"), (_result(),))
    evaluation = freeze_elo_evaluation(
        analysis_run_id="run-1",
        model_state=state,
        prediction=prediction,
        market=MARKET,
        evaluated_at_utc=CUTOFF,
    )

    assert evaluation.status is QuantModelEvaluationStatus.UNAVAILABLE
    assert evaluation.unavailable_reason == "INSUFFICIENT_PRIOR_MATCHES"
    assert evaluation.probabilities is None
    assert (
        project_available_model_quant(model_state=state, evaluation=evaluation) is None
    )
    assert "p_market" not in evaluation.output_json


def test_model_artifact_and_evaluation_payload_hashes_reject_tampering() -> None:
    baseline, state = _frozen_state()
    prediction = baseline.predict(_request(), (_result(),))
    evaluation = freeze_elo_evaluation(
        analysis_run_id="run-1",
        model_state=state,
        prediction=prediction,
        market=MARKET,
        evaluated_at_utc=CUTOFF,
    )
    state_payload = state.model_dump(mode="python")
    state_payload["state_json"] = state_payload["state_json"].replace(
        '"home_goals":2', '"home_goals":0'
    )
    with pytest.raises(ValidationError, match="state payload hash"):
        QuantModelStateArtifact.model_validate(state_payload)

    evaluation_payload = evaluation.model_dump(mode="python")
    evaluation_payload["output_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="output hash"):
        QuantModelEvaluation.model_validate(evaluation_payload)


def test_model_input_manifest_v3_is_stable_and_excludes_manual_contract() -> None:
    _, state = _frozen_state()

    first = build_model_input_manifest_json(
        competitions=(),
        teams=(),
        matches=(),
        mappings=(),
        market_snapshots=(),
        sporttery_snapshots=(),
        model_states=(state,),
    )
    second = build_model_input_manifest_json(
        competitions=(),
        teams=(),
        matches=(),
        mappings=(),
        market_snapshots=(),
        sporttery_snapshots=(),
        model_states=(state,),
    )

    payload = json.loads(first)
    assert first == second
    assert payload["version"] == MVP_INPUT_MANIFEST_V3
    assert payload["quant_model_states"][0]["state_hash"] == state.state_hash
    assert "manual_quant_inputs" not in payload
    assert len(hashlib.sha256(first.encode("utf-8")).hexdigest()) == 64
