from football_system.domain.market_analysis import (
    MarketAnalysisUnitV1,
    MarketModelLineageV1,
    base_probability,
)
from football_system.domain.market_v2 import MarketTypeV1, revalidate
from football_system.domain.services.poisson_goals import project_score_grid


def unit_values(
    identity, market, cutoff, sporttery, consensus, goal, legacy, policy, weight
):
    if (
        cutoff >= identity.kickoff_at_utc
        or sporttery.match_id != identity.match_id
        or sporttery.market_key != market
        or sporttery.ingested_at_utc > cutoff
    ):
        raise ValueError("market unit SP identity/time mismatch")
    if market.market_type == MarketTypeV1.THREE_WAY:
        if (
            goal is not None
            or legacy is None
            or consensus is not None
            or policy != "LEGACY_FROZEN_THREE_WAY"
        ):
            raise ValueError("THREE_WAY must use explicit frozen legacy source")
        revalidate(legacy)
        if legacy.identity != identity or legacy.decision_cutoff != cutoff:
            raise ValueError("legacy THREE_WAY source mismatch")
        lineage = legacy.model_lineage
        quant = legacy.p_quant
        base = legacy.p_base
        reason = legacy.unavailable_reason
    else:
        if (
            consensus is None
            or consensus.match_id != identity.match_id
            or consensus.market_key != market
            or consensus.decision_cutoff != cutoff
        ):
            raise ValueError("market consensus unit binding mismatch")
        if goal is None or legacy is not None:
            raise ValueError("new markets require the independent Poisson source")
        revalidate(goal)
        r = goal.request
        if (
            r.match_id,
            r.competition_id,
            r.season_id,
            r.home_team_id,
            r.away_team_id,
            r.kickoff_at_utc,
        ) != (
            identity.match_id,
            identity.competition_id,
            identity.season_id,
            identity.home_team_id,
            identity.away_team_id,
            identity.kickoff_at_utc,
        ) or r.generated_at_utc > cutoff:
            raise ValueError("Poisson model scope/time differs from market unit")
        lineage = MarketModelLineageV1(
            model_name=goal.config.model_name,
            state_id=goal.artifact_id,
            state_hash=goal.content_hash,
            config_hash=goal.config.config_hash,
            training_data_hash=goal.training_data_hash,
            training_cutoff_at_utc=r.training_cutoff_at_utc,
            generated_at_utc=r.generated_at_utc,
        )
        quant = project_score_grid(goal.grid, market) if goal.grid else None
        base = base_probability(quant, consensus.probabilities, policy, weight)
        reason = (
            goal.reason
            if quant is None
            else "MARKET_UNAVAILABLE"
            if base is None
            else None
        )
    return dict(
        model_lineage=lineage,
        p_quant=quant,
        p_base=base,
        quant_status="AVAILABLE" if quant is not None else "MODEL_UNAVAILABLE",
        unavailable_reason=reason,
    )


def build_market_unit(
    *,
    identity,
    market_key,
    decision_cutoff,
    sporttery,
    consensus,
    goal_state=None,
    legacy_source=None,
    base_policy="QUANT_ONLY_V1",
    quant_weight="0.70",
    data_quality="0.25",
    evidence=(),
):
    from decimal import Decimal
    from football_system.domain.market_v2 import decimal_value

    weight = Decimal(decimal_value(quant_weight))
    return MarketAnalysisUnitV1.freeze(
        identity=identity,
        market_key=market_key,
        decision_cutoff=decision_cutoff,
        sporttery=sporttery,
        consensus=consensus,
        goal_state=goal_state,
        legacy_source=legacy_source,
        base_policy=base_policy,
        quant_weight=weight,
        data_quality=data_quality,
        evidence=tuple(sorted(evidence, key=lambda e: e.artifact_id)),
        **unit_values(
            identity,
            market_key,
            decision_cutoff,
            sporttery,
            consensus,
            goal_state,
            legacy_source,
            base_policy,
            weight,
        ),
    )
