from datetime import datetime, timezone
from decimal import Decimal

import pytest

from football_system.domain.market import MarketKey, MarketType, ThreeWayProbability
from football_system.domain.prediction import (
    FusionConfig,
    FusionInputs,
    FusionInputsUnavailable,
    FusionPolicyName,
    MarketPrediction,
    QuantPrediction,
)
from football_system.domain.services.fusion import get_fusion_policy

NOW = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)


def market_prediction() -> MarketPrediction:
    return MarketPrediction(
        prediction_id="p-market",
        analysis_run_id="run-1",
        match_id="match-1",
        market=MARKET,
        probabilities=ThreeWayProbability(
            home_win=Decimal("0.50"),
            draw=Decimal("0.30"),
            away_win=Decimal("0.20"),
        ),
        input_snapshot_ids=("odds-1",),
        overround=Decimal("1.05"),
        generated_at_utc=NOW,
    )


def quant_prediction() -> QuantPrediction:
    return QuantPrediction(
        prediction_id="p-quant",
        analysis_run_id="run-1",
        match_id="match-1",
        market=MARKET,
        probabilities=ThreeWayProbability(
            home_win=Decimal("0.60"),
            draw=Decimal("0.25"),
            away_win=Decimal("0.15"),
        ),
        manual_input_id="manual-1",
        input_payload_hash="hash-manual-1",
        entered_at_utc=NOW,
    )


def test_quant_only_policy_has_explicit_lineage() -> None:
    result = get_fusion_policy(FusionPolicyName.QUANT_ONLY_V1).fuse(
        FusionInputs(
            analysis_run_id="run-1",
            match_id="match-1",
            market=MARKET,
            p_quant=quant_prediction(),
        ),
        FusionConfig(),
        NOW,
    )

    assert result.probabilities == quant_prediction().probabilities
    assert result.quant_prediction_id == "p-quant"
    assert result.market_prediction_id is None
    assert result.fusion_policy == FusionPolicyName.QUANT_ONLY_V1


def test_market_quant_blend_is_deterministic() -> None:
    result = get_fusion_policy(FusionPolicyName.MARKET_QUANT_BLEND_V1).fuse(
        FusionInputs(
            analysis_run_id="run-1",
            match_id="match-1",
            market=MARKET,
            p_market=market_prediction(),
            p_quant=quant_prediction(),
        ),
        FusionConfig(quant_weight=Decimal("0.70")),
        NOW,
    )

    assert result.probabilities.home_win == Decimal("0.5700")
    assert result.probabilities.draw == Decimal("0.2650")
    assert result.probabilities.away_win == Decimal("0.1650")
    assert result.market_prediction_id == "p-market"
    assert result.quant_prediction_id == "p-quant"


def test_blend_requires_both_inputs() -> None:
    with pytest.raises(FusionInputsUnavailable):
        get_fusion_policy(FusionPolicyName.MARKET_QUANT_BLEND_V1).fuse(
            FusionInputs(
                analysis_run_id="run-1",
                match_id="match-1",
                market=MARKET,
                p_quant=quant_prediction(),
            ),
            FusionConfig(),
            NOW,
        )


def test_fusion_rejects_cross_run_prediction() -> None:
    with pytest.raises(ValueError, match="fusion context"):
        get_fusion_policy(FusionPolicyName.QUANT_ONLY_V1).fuse(
            FusionInputs(
                analysis_run_id="run-2",
                match_id="match-1",
                market=MARKET,
                p_quant=quant_prediction(),
            ),
            FusionConfig(),
            NOW,
        )
