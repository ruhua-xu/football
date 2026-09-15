from datetime import timedelta
from decimal import Decimal, localcontext, ROUND_DOWN
from itertools import combinations
from math import prod

import pytest

from football_system.application.review_v4 import (
    export_packet_v4,
    fuse_v4,
    import_v4_bytes,
)
from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.betting import (
    PassType,
    PortfolioConstraints,
    SportteryRules,
)
from football_system.domain.market_analysis import (
    MultiMarketAnalysisV1,
    MarketMatchIdentityV1,
    MarketModelLineageV1,
    LegacyThreeWayInputV1,
)
from football_system.domain.market_v2 import (
    MarketPriceV1,
    SportteryFixedBonusSnapshotV2,
    distribution,
    market_consensus,
)
from football_system.domain.services.market_analysis import build_market_unit
from football_system.domain.services.poisson_goals import train_poisson
from football_system.domain.services.strategy_pass_v2 import (
    build_strategy_v2,
    strategy_source_v2,
    expanded_count,
    structural_risk_v2,
    payout_states,
)
from football_system.domain.services.settlement_v2 import settle_strategy_v2
from football_system.domain.strategy_pass_v2 import (
    MatchChoiceRequestV1,
    StrategyProfileV2,
    TicketRequestV2,
    StrategyPassPlanV2,
)
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    UnsupportedSettlementReason,
)
from tests.unit.test_market_v2 import goal_fixture, market
from tests.unit.test_review_v4 import review_bytes


def multi_fixture(
    counts=(1, 2, 2, 1),
    *,
    negative_fifth=True,
    budget=10000,
    all_below=False,
    other=False,
    other_outcome="HOME_OTHER",
):
    cohort, request = goal_fixture()
    kinds = ("THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE", "THREE_WAY")
    selections = (
        ("HOME_WIN",),
        ("GOALS_2", "GOALS_3", "GOALS_4"),
        ("SCORE_3_1", "SCORE_4_1", "SCORE_5_1"),
        ("AWAY_WIN",),
    )
    if other:
        kinds = ("THREE_WAY", "CORRECT_SCORE", "CORRECT_SCORE", "THREE_WAY")
        selections = (selections[0], (other_outcome,), selections[2], selections[3])
    units = []
    requests = []
    for i, count in enumerate(counts):
        match = f"target-{i}"
        key = market(kinds[i])
        at = request.generated_at_utc
        identity = MarketMatchIdentityV1(
            match_id=match,
            competition_id="league",
            season_id="2026",
            home_team_id="home",
            away_team_id="away",
            home_team_name="Synthetic Home",
            away_team_name="Synthetic Away",
            kickoff_at_utc=request.kickoff_at_utc,
        )
        prices = {o: "1.1" for o in key.catalog}
        values = (
            ("2",)
            if i == 0
            else ("6", "8", "9")
            if kinds[i] == "TOTAL_GOALS"
            else ("10000",)
            if other and i == 1
            else ("40", "100", "1.1" if negative_fifth else "1000")
            if kinds[i] == "CORRECT_SCORE"
            else ("6",)
        )
        for outcome, price in zip(selections[i], values):
            prices[outcome] = price
        if all_below:
            prices = {o: "1.1" for o in key.catalog}
        sp = SportteryFixedBonusSnapshotV2.freeze(
            match_id=match,
            market_key=key,
            prices=tuple(
                MarketPriceV1(outcome=o, price=prices[o]) for o in key.catalog
            ),
            provider_code="SYNTHETIC",
            source_identity=match,
            source_artifact_id="sp-" + match,
            source_artifact_hash="e" * 64,
            captured_at_utc=at,
            available_at_utc=at,
            ingested_at_utc=at,
            sale_status="OPEN",
        )
        legacy = None
        goal = None
        if kinds[i] == "THREE_WAY":
            p = distribution(key, [Decimal("0.6"), Decimal("0.2"), Decimal("0.2")])
            legacy = LegacyThreeWayInputV1.freeze(
                analysis_run_id="legacy-synthetic",
                analysis_run_hash="b" * 64,
                identity=identity,
                decision_cutoff=at,
                model_lineage=MarketModelLineageV1(
                    model_name="ELO_THREE_WAY_BASELINE_V1",
                    state_id="old-state",
                    state_hash="a" * 64,
                    config_hash="c" * 64,
                    training_data_hash="d" * 64,
                    training_cutoff_at_utc=at,
                    generated_at_utc=at,
                ),
                p_market=p,
                p_quant=p,
                p_base=p,
                unavailable_reason=None,
            )
        else:
            goal = train_poisson(cohort, request.model_copy(update={"match_id": match}))
        units.append(
            build_market_unit(
                identity=identity,
                market_key=key,
                decision_cutoff=at,
                sporttery=sp,
                consensus=None if legacy else market_consensus(match, key, (), at),
                goal_state=goal,
                legacy_source=legacy,
                base_policy="LEGACY_FROZEN_THREE_WAY" if legacy else "QUANT_ONLY_V1",
            )
        )
        requests.append(
            MatchChoiceRequestV1(
                match_id=match, market_key=key, outcomes=selections[i][:count]
            )
        )
    analysis = MultiMarketAnalysisV1.freeze(
        units=tuple(units),
        budgets_fen=tuple(sorted({0, budget})),
        rules=SportteryRules(version="SYNTHETIC_V2"),
        constraints=PortfolioConstraints(),
        min_selection_ev="0.02",
        min_ticket_roi="0.02",
    )
    packet = export_packet_v4(analysis)
    review = import_v4_bytes(canonical_json(packet).encode(), review_bytes(packet))
    fusion = fuse_v4(analysis, review)
    return strategy_source_v2(analysis, review, fusion, budget), tuple(requests)


@pytest.mark.parametrize(
    "pass_type,counts,total,stake",
    [
        (PassType.TWO_FOLD_ONE, (1, 2), 2, 400),
        (PassType.THREE_FOLD_FOUR, (1, 2, 2), 12, 2400),
        (PassType.FOUR_FOLD_ELEVEN, (1, 2, 2, 1), 29, 5800),
    ],
)
def test_f_g_h_exact_cartesian_expansion_and_hand_stakes(
    pass_type, counts, total, stake
):
    source, choices = multi_fixture(counts)
    plan = build_strategy_v2(
        source, requests=(TicketRequestV2(pass_type=pass_type, choices=choices),)
    )
    c = plan.candidates[0]
    assert expanded_count(pass_type, counts) == total == len(c.atomic_bets)
    assert c.unit_stake_fen == stake
    expected = {}
    for size in range(2, len(counts) + 1):
        for subset in combinations(range(len(counts)), size):
            expected[tuple(f"target-{i}" for i in subset)] = prod(
                counts[i] for i in subset
            )
    actual = {}
    for a in c.atomic_bets:
        key = tuple(leg.match_id for leg in a.legs)
        actual[key] = actual.get(key, 0) + 1
    assert actual == expected
    assert len({a.logical_key for a in c.atomic_bets}) == total
    assert c.reconstructable_state_count <= 256
    assert (
        len(payout_states(c.choice_sets, c.atomic_bets, 256))
        == c.reconstructable_state_count
    )
    assert StrategyPassPlanV2.model_validate_json(plan.model_dump_json()) == plan
    if len(counts) == 2:
        assert c.max_payout_fen == 3200  # mutually exclusive B choices cannot both pay
    if len(counts) == 3:
        assert c.max_payout_fen == 523200
    if len(counts) == 4:
        assert c.max_payout_fen == 3794400


def match_results(plan, scores):
    at = max(u.identity.kickoff_at_utc for u in plan.source.analysis.units) + timedelta(
        hours=3
    )
    return tuple(
        MatchResult(
            match_result_id="result-" + m,
            match_id=m,
            provider_code="SYNTHETIC",
            home_goals=h,
            away_goals=a,
            observed_at_utc=at,
            available_at_utc=at,
            ingested_at_utc=at,
            source_result_key="source-" + m,
            payload_hash=match_result_payload_sha256(h, a),
        )
        for m, (h, a) in sorted(scores.items())
    ), at


def test_i_mixed_market_payout_and_missing_unsupported_semantics():
    source, choices = multi_fixture((1, 2))
    plan = build_strategy_v2(
        source, requests=(TicketRequestV2(pass_type="2X1", choices=choices),)
    )
    results, at = match_results(plan, {"target-0": (2, 0), "target-1": (2, 1)})
    s = settle_strategy_v2(plan, results, at)
    t = plan.tickets[0]
    assert s.gross_payout_fen == 3200 * t.multiplier
    assert len([a for a in s.tickets[0].atomic_results if a.won]) == 1
    assert settle_strategy_v2(plan, results[:1], at).reason == "MISSING_RESULT"
    issue = MatchSettlementIssue(
        match_id="target-1", reason=UnsupportedSettlementReason.VOID
    )
    bad = settle_strategy_v2(plan, results[:1], at, issues=(issue,))
    assert bad.reason == "UNSUPPORTED_SETTLEMENT_CASE" and bad.gross_payout_fen is None


def test_j_negative_ev_is_removed_with_explicit_reason():
    source, choices = multi_fixture((1, 2, 3))
    plan = build_strategy_v2(
        source, requests=(TicketRequestV2(pass_type="3X4", choices=choices),)
    )
    score_set = plan.candidates[0].choice_sets[2]
    assert tuple(c.outcome.value for c in score_set.candidates) == (
        "SCORE_3_1",
        "SCORE_4_1",
    )
    assert plan.candidates[0].expanded_atomic_bet_count == 12
    assert any("SCORE_5_1:INELIGIBLE_OUTCOME_EXCLUDED" in d for d in plan.decisions)


def test_k_expansion_guard_runs_before_any_atomic_materialization(monkeypatch):
    source, choices = multi_fixture((1, 2, 2, 1))
    from football_system.domain.strategy_pass_v2 import ExpandedAtomicBetV2

    monkeypatch.setattr(
        ExpandedAtomicBetV2,
        "freeze",
        lambda **_: pytest.fail("materialized before expansion bound"),
    )
    with pytest.raises(ValueError, match="before materialization"):
        build_strategy_v2(
            source,
            StrategyProfileV2(max_expanded_atomic_bets_per_ticket=28),
            requests=(TicketRequestV2(pass_type="4X11", choices=choices),),
        )


def test_n_below_gate_is_no_bet_and_zero_budget_remains_zero():
    source, choices = multi_fixture((1, 2), all_below=True)
    plan = build_strategy_v2(
        source, requests=(TicketRequestV2(pass_type="2X1", choices=choices),)
    )
    assert (
        plan.status == "NO_BET" and plan.total_stake_fen == 0 and plan.cash_fen == 10000
    )


def test_o_same_match_cross_market_graph_rejected():
    with pytest.raises(ValueError, match="cross-market"):
        TicketRequestV2(
            pass_type="2X1",
            choices=(
                MatchChoiceRequestV1(
                    match_id="same", market_key=market(), outcomes=("HOME_WIN",)
                ),
                MatchChoiceRequestV1(
                    match_id="same",
                    market_key=market("TOTAL_GOALS"),
                    outcomes=("GOALS_3",),
                ),
            ),
        )


@pytest.mark.parametrize(
    "outcome,score",
    [("HOME_OTHER", (6, 1)), ("DRAW_OTHER", (4, 4)), ("AWAY_OTHER", (1, 6))],
)
def test_p_other_uses_other_quote_not_nearest_explicit_score(outcome, score):
    source, choices = multi_fixture((1, 1), other=True, other_outcome=outcome)
    plan = build_strategy_v2(
        source, requests=(TicketRequestV2(pass_type="2X1", choices=choices),)
    )
    results, at = match_results(plan, {"target-0": (1, 0), "target-1": score})
    s = settle_strategy_v2(plan, results, at)
    assert s.gross_payout_fen == 200 * 2 * 10000 * plan.tickets[0].multiplier


def test_or_choices_do_not_make_each_individual_outcome_indispensable():
    source, choices = multi_fixture((1, 2))
    plan = build_strategy_v2(
        source, requests=(TicketRequestV2(pass_type="2X1", choices=choices),)
    )
    candidate = plan.candidates[0]
    risk = structural_risk_v2([(candidate, 1)], StrategyProfileV2())
    options = candidate.choice_sets[1].candidates
    assert all(c.logical_key not in risk.single_outcome_wipeout for c in options)
    assert {risk.outcome_exposure_fen[c.logical_key] for c in options} == {200}
    assert risk.match_exposure_fen["target-1"] == 400


def test_hedge_v2_has_a_real_score_and_surviving_expanded_atomic():
    from football_system.domain.market_v2 import settle_market

    source, choices = multi_fixture((1, 2, 2, 1))
    plan = build_strategy_v2(
        source,
        requests=(
            TicketRequestV2(pass_type="2X1", choices=choices[:2], role="PRIMARY"),
            TicketRequestV2(pass_type="2X1", choices=choices[2:], role="HEDGE"),
        ),
    )
    hedge = next(t for t in plan.tickets if t.role == "HEDGE")
    witness = hedge.hedge_witness
    assert all(
        settle_market(
            c.market_key, witness.failure.home_goals, witness.failure.away_goals
        )
        not in c.outcomes
        for c in witness.failure.failed_choice_sets
    )
    atomic = next(
        a
        for a in hedge.candidate.atomic_bets
        if a.artifact_id == witness.surviving_atomic_bet_id
    )
    assert witness.independent_outcome_keys
    assert all(
        c.match_id != witness.failure.match_id
        or settle_market(
            c.market_key, witness.failure.home_goals, witness.failure.away_goals
        )
        == c.outcome
        for c in atomic.legs
    )
    assert hedge.stake_fen <= source.budget_fen * Decimal("0.10")


def test_v2_plan_identity_replay_is_decimal_context_independent():
    source, choices = multi_fixture((1, 2))
    request = TicketRequestV2(pass_type="2X1", choices=choices)
    expected = build_strategy_v2(source, requests=(request,))
    with localcontext() as context:
        context.prec = 15
        context.rounding = ROUND_DOWN
        assert build_strategy_v2(source, requests=(request,)) == expected
