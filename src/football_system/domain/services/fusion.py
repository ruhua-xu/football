from __future__ import annotations

import json
from decimal import Decimal

from football_system.domain.common import UtcDateTime, stable_id
from football_system.domain.market import ThreeWayProbability
from football_system.domain.prediction import (
    FinalPrediction,
    FusionConfig,
    FusionInputs,
    FusionInputsUnavailable,
    FusionPolicy,
    FusionPolicyName,
)
from football_system.domain.services.probability import quantize_three_way_probability


class QuantOnlyPolicy:
    name = FusionPolicyName.QUANT_ONLY_V1
    version = "1"

    def fuse(
        self,
        inputs: FusionInputs,
        config: FusionConfig,
        generated_at_utc: UtcDateTime,
    ) -> FinalPrediction:
        del config
        if inputs.p_quant is None:
            raise FusionInputsUnavailable("QUANT_ONLY_V1 requires P_quant")
        if (
            inputs.p_quant.analysis_run_id != inputs.analysis_run_id
            or inputs.p_quant.match_id != inputs.match_id
            or inputs.p_quant.market != inputs.market
        ):
            raise ValueError("P_quant does not match fusion context")
        return FinalPrediction(
            prediction_id=stable_id(
                "final", inputs.analysis_run_id, inputs.match_id, inputs.market.canonical, self.name
            ),
            analysis_run_id=inputs.analysis_run_id,
            match_id=inputs.match_id,
            market=inputs.market,
            probabilities=inputs.p_quant.probabilities,
            quant_prediction_id=inputs.p_quant.prediction_id,
            fusion_policy=self.name,
            fusion_config_json="{}",
            generated_at_utc=generated_at_utc,
        )


class MarketQuantBlendPolicy:
    name = FusionPolicyName.MARKET_QUANT_BLEND_V1
    version = "1"

    def fuse(
        self,
        inputs: FusionInputs,
        config: FusionConfig,
        generated_at_utc: UtcDateTime,
    ) -> FinalPrediction:
        if inputs.p_market is None or inputs.p_quant is None:
            raise FusionInputsUnavailable(
                "MARKET_QUANT_BLEND_V1 requires P_market and P_quant"
            )
        if (
            inputs.p_market.match_id != inputs.match_id
            or inputs.p_quant.match_id != inputs.match_id
            or inputs.p_market.analysis_run_id != inputs.analysis_run_id
            or inputs.p_quant.analysis_run_id != inputs.analysis_run_id
            or inputs.p_market.market != inputs.market
            or inputs.p_quant.market != inputs.market
        ):
            raise ValueError("fusion predictions do not match context")
        weight = config.quant_weight
        market_weight = Decimal(1) - weight
        p_market = inputs.p_market.probabilities
        p_quant = inputs.p_quant.probabilities
        probabilities = quantize_three_way_probability(
            ThreeWayProbability(
                home_win=weight * p_quant.home_win + market_weight * p_market.home_win,
                draw=weight * p_quant.draw + market_weight * p_market.draw,
                away_win=weight * p_quant.away_win + market_weight * p_market.away_win,
            )
        )
        config_json = json.dumps(
            {"quant_weight": str(weight)}, separators=(",", ":"), sort_keys=True
        )
        return FinalPrediction(
            prediction_id=stable_id(
                "final", inputs.analysis_run_id, inputs.match_id, inputs.market.canonical, self.name
            ),
            analysis_run_id=inputs.analysis_run_id,
            match_id=inputs.match_id,
            market=inputs.market,
            probabilities=probabilities,
            market_prediction_id=inputs.p_market.prediction_id,
            quant_prediction_id=inputs.p_quant.prediction_id,
            fusion_policy=self.name,
            fusion_config_json=config_json,
            generated_at_utc=generated_at_utc,
        )


def get_fusion_policy(name: FusionPolicyName | str) -> FusionPolicy:
    policy_name = FusionPolicyName(name)
    if policy_name == FusionPolicyName.QUANT_ONLY_V1:
        return QuantOnlyPolicy()
    if policy_name == FusionPolicyName.MARKET_QUANT_BLEND_V1:
        return MarketQuantBlendPolicy()
    raise ValueError(f"unsupported fusion policy: {name}")
