"""Fixed synthetic P_final vectors wrapped in frozen V2 contracts; no model fitting."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from football_system.domain.archive import canonical_json
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.market_analysis import LegacyThreeWayInputV1, MarketMatchIdentityV1, MarketModelLineageV1, MultiMarketAnalysisV1
from football_system.domain.market_v2 import MarketKeyV2, MarketOutcomeProbabilityV1, MarketProbabilityDistributionV1, MarketPriceV1, SportteryFixedBonusSnapshotV2, fixed_decimal
from football_system.domain.return_distribution import ReturnDistributionPolicyV1, ReturnObjectiveProfileV1, ReturnAllocationRequestV1
from football_system.domain.services.market_analysis import build_market_unit
from football_system.domain.services.review_v4 import export_packet_v4, import_v4_bytes, fuse_v4
from football_system.domain.services.strategy_pass_v2 import strategy_source_v2, build_strategy_v2
from football_system.domain.strategy_pass_v2 import MatchChoiceRequestV1, TicketRequestV2, StrategyProfileV2

DAY = datetime(2026, 1, 1, tzinfo=timezone.utc)


def policies(**limits):
    return ReturnDistributionPolicyV1.freeze(code_hash="f"*64, **limits), ReturnObjectiveProfileV1.freeze()


@fixed_decimal(512)
def fixed_plan(probabilities, prices, tickets, *, budget=10000, profile=None, max_multiplier=50, constraints=None, min_ev="0"):
    key = MarketKeyV2(market_type="THREE_WAY")
    units = []
    for name in sorted(probabilities):
        values = probabilities[name]
        if isinstance(values, str):
            p = Decimal(values)
            values = (p, 1-p, Decimal(0))
        marginal = MarketProbabilityDistributionV1(market_key=key, outcomes=tuple(
            MarketOutcomeProbabilityV1(outcome=o, probability=p) for o, p in zip(key.catalog, values, strict=True)))
        values = prices[name]
        if isinstance(values, str):
            values = (values, "1.01", "1.01")
        identity = MarketMatchIdentityV1(match_id=name, competition_id="return-synthetic", season_id="2026",
            home_team_id=name+"-home", away_team_id=name+"-away", home_team_name="Synthetic home", away_team_name="Synthetic away", kickoff_at_utc=DAY+timedelta(days=2))
        sp = SportteryFixedBonusSnapshotV2.freeze(match_id=name, market_key=key,
            prices=tuple(MarketPriceV1(outcome=o, price=p) for o, p in zip(key.catalog, values, strict=True)),
            provider_code="SYNTHETIC_FIXED", source_identity=name, source_artifact_id="synthetic-sp-"+name,
            source_artifact_hash="e"*64, captured_at_utc=DAY, available_at_utc=DAY, ingested_at_utc=DAY, sale_status="OPEN")
        legacy = LegacyThreeWayInputV1.freeze(analysis_run_id="synthetic-frozen-"+name, analysis_run_hash="b"*64,
            identity=identity, decision_cutoff=DAY,
            model_lineage=MarketModelLineageV1(model_name="ELO_THREE_WAY_BASELINE_V1", state_id="synthetic-state-"+name,
                state_hash="a"*64, config_hash="c"*64, training_data_hash="d"*64, training_cutoff_at_utc=DAY, generated_at_utc=DAY),
            p_market=marginal, p_quant=marginal, p_base=marginal, unavailable_reason=None)
        units.append(build_market_unit(identity=identity, market_key=key, decision_cutoff=DAY, sporttery=sp,
            consensus=None, legacy_source=legacy, base_policy="LEGACY_FROZEN_THREE_WAY"))
    analysis = MultiMarketAnalysisV1.freeze(units=tuple(units), budgets_fen=tuple(sorted({0, budget})),
        rules=SportteryRules(version="SYNTHETIC_FROZEN_RETURN_INPUT", max_multiplier=max_multiplier),
        constraints=constraints or PortfolioConstraints(), min_selection_ev=min_ev, min_ticket_roi="0")
    packet = export_packet_v4(analysis)
    # Explicit synthetic SKIPPED_DISABLED records: no LLM probabilities/review call.
    raw = json.dumps(dict(schema_version="LLM_REVIEW_V4", analysis_id=analysis.artifact_id,
        packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=[dict(
            match_id=u.review_context.identity.match_id, market_key=u.review_context.market_key.model_dump(mode="json"),
            review_context_id=u.review_context_id, review_context_hash=u.review_context_hash,
            status="UNAVAILABLE", failure_code="SKIPPED_DISABLED", limitations=["Fixed synthetic input only"])
            for u in packet.market_units])).encode()
    review = import_v4_bytes(canonical_json(packet).encode(), raw)
    fusion = fuse_v4(analysis, review)
    source = strategy_source_v2(analysis, review, fusion, budget)
    requests = []
    for spec in tickets:
        choices = spec["choices"]
        if not isinstance(choices, dict):
            choices = {name: ("HOME_WIN",) for name in choices}
        requests.append(TicketRequestV2(pass_type=spec.get("pass_type", "2X1"), role=spec.get("role"),
            choices=tuple(MatchChoiceRequestV1(match_id=name, market_key=key, outcomes=tuple(outcomes)) for name, outcomes in sorted(choices.items()))))
    return build_strategy_v2(source, profile or StrategyProfileV2(), tuple(requests))


def allocations(plan, multiplier=1):
    return tuple(ReturnAllocationRequestV1(ticket_candidate_id=c.artifact_id, multiplier=multiplier) for c in plan.candidates)


def candidate_by_matches(plan, ids):
    return next(c for c in plan.candidates if {s.match_id for s in c.choice_sets} == set(ids))
