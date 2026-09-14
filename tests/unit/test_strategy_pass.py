import json
from datetime import datetime, timezone
from decimal import Decimal
from itertools import combinations
from pathlib import Path

import pytest

from football_system.domain.betting import (
    CandidateStatus,
    PassType,
    PortfolioConstraints,
    SportteryRules,
    TicketCandidate,
)
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayProbability,
)
from football_system.domain.match import (
    FixedBonusQuote,
    SaleStatus,
    SportteryBonusSnapshot,
)
from football_system.domain.prediction import FinalPrediction, FusionPolicyName
from football_system.domain.services.betting import (
    build_selection_candidates,
    build_two_leg_ticket_candidates,
)
from football_system.domain.services.strategy_pass import (
    build_strategy_plan,
    generate_candidates,
    structural_risk,
)
from football_system.domain.strategy_pass import (
    StrategyParentV1,
    StrategyPassPlanV1,
    StrategyProfileV1,
    StrategySourceV1,
    SystemTicketCandidateV1,
    SystemTicketV1,
    TicketRole,
    TicketRoleRequestV1,
    ticket_spec,
)

NOW = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)
CASES = json.loads(
    Path("data/fixtures/strategy_pass_v1.json").read_text(encoding="utf-8")
)["cases"]


def source(
    prices=("2", "3", "4", "5"),
    *,
    budget=10000,
    threshold="0.01",
    roi="0.02",
    probability="0.6",
    rules=None,
):
    snapshots = []
    selections = []
    for i, price in enumerate(prices):
        match_id = f"synthetic-{i}"
        snapshot = SportteryBonusSnapshot(
            snapshot_id=f"sp-{i}",
            match_id=match_id,
            provider_code="MOCK_SPORTTERY",
            sporttery_match_no=match_id,
            market=MARKET,
            quotes=tuple(
                FixedBonusQuote(selection=s, fixed_bonus=v)
                for s, v in zip(SelectionKey, (price, "1.1", "1.1"), strict=True)
            ),
            sale_status=SaleStatus.OPEN,
            captured_at_utc=NOW,
            available_at_utc=NOW,
            ingested_at_utc=NOW,
            source_snapshot_key=match_id,
            payload_hash="a" * 64,
        )
        p = Decimal(probability)
        prediction = FinalPrediction(
            prediction_id=f"final-{i}",
            analysis_run_id="synthetic-run",
            match_id=match_id,
            market=MARKET,
            probabilities=ThreeWayProbability(
                home_win=p, draw=(1 - p) / 2, away_win=(1 - p) / 2
            ),
            quant_prediction_id=f"q-{i}",
            fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
            fusion_config_json="{}",
            generated_at_utc=NOW,
        )
        snapshots.append(snapshot)
        selections.extend(
            build_selection_candidates(prediction, snapshot, Decimal(threshold))
        )
    return StrategySourceV1(
        parent=StrategyParentV1(
            kind="ANALYSIS_RUN",
            analysis_run_id="synthetic-run",
            analysis_run_hash="b" * 64,
            source_id="synthetic-run",
            source_hash="b" * 64,
            as_of_at_utc=NOW,
        ),
        selections=tuple(sorted(selections, key=lambda s: s.candidate_id)),
        odds_snapshots=tuple(sorted(snapshots, key=lambda s: s.snapshot_id)),
        budget_fen=budget,
        rules=rules or SportteryRules(version="SYNTHETIC_V1"),
        constraints=PortfolioConstraints(),
        min_selection_ev=threshold,
        min_ticket_roi=roi,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_fixed_acceptance_a_to_e(case):
    s = source(
        case["home_prices"],
        threshold=case["min_selection_ev"],
        roi=case["min_ticket_roi"],
    )
    profile = StrategyProfileV1()
    plan = build_strategy_plan(s, profile)
    assert {c.pass_type.value for c in plan.candidates} == set(case["expected_passes"])
    assert len(plan.tickets) <= 4
    assert not plan.risk.concentrated
    assert plan == StrategyPassPlanV1.model_validate_json(plan.model_dump_json())
    assert plan == build_strategy_plan(s, profile)
    if case["id"] == "D":
        assert (
            plan.status == "NO_BET"
            and plan.total_stake_fen == 0
            and plan.cash_fen == 10000
        )
    if case["id"] == "E":
        pairs = build_strategy_plan(
            s, StrategyProfileV1(pass_types=(PassType.TWO_FOLD_ONE,))
        )
        assert len(pairs.tickets) == 1
        assert any(d.code == "STRUCTURAL_CONCENTRATION_LIMIT" for d in pairs.decisions)


@pytest.mark.parametrize(
    "pass_type,n,count,stake,payout",
    [
        (PassType.TWO_FOLD_ONE, 2, 1, 200, 1200),
        (PassType.THREE_FOLD_FOUR, 3, 4, 800, 10000),
        (PassType.FOUR_FOLD_ELEVEN, 4, 11, 2200, 69000),
    ],
)
def test_hand_computed_constituents_and_all_outcome_payouts(
    pass_type, n, count, stake, payout
):
    s = source(("2", "3", "4", "5")[:n])
    c = next(
        c
        for c in generate_candidates(s, StrategyProfileV1())
        if c.pass_type == pass_type
    )
    assert (
        c.atomic_bet_count == count
        and c.base_stake_fen == stake
        and c.max_payout_fen == payout
    )
    assert {a.selection_ids for a in c.atomic_bets} == {
        tuple(s.candidate_id for s in comb)
        for size in range(2, n + 1)
        for comb in combinations(c.selections, size)
    }
    assert len({a.atomic_bet_id for a in c.atomic_bets}) == count
    assert len(c.payout_states) == 3**n
    assert (
        next(
            x
            for x in c.payout_states
            if all(o == SelectionKey.HOME_WIN for o in x.outcomes)
        ).gross_payout_fen
        == payout
    )
    assert all(
        x.gross_payout_fen == 0
        for x in c.payout_states
        if sum(o == SelectionKey.HOME_WIN for o in x.outcomes) < 2
    )
    # With only the SP=2 and SP=3 selections correct, exactly their pair pays.
    outcomes = tuple(
        SelectionKey.HOME_WIN if x.fixed_bonus in (2, 3) else SelectionKey.DRAW
        for x in c.selections
    )
    partial = next(x for x in c.payout_states if x.outcomes == outcomes)
    assert partial.gross_payout_fen == 1200 and len(partial.winning_atomic_bet_ids) == 1


def test_existing_2x1_economics_and_wire_remain_equal():
    s = source(("2.035", "3.041"), probability="0.612345678912")
    new = generate_candidates(s, StrategyProfileV1())[0]
    old = build_two_leg_ticket_candidates(s.selections, s.rules, s.min_ticket_roi)[0]
    assert new.max_payout_fen == old.gross_payout_fen
    assert new.expected_gross_payout_fen == old.expected_gross_payout_fen
    assert new.expected_profit_fen == old.expected_profit_fen
    assert new.expected_roi == old.expected_roi
    assert (
        TicketCandidate.model_validate_json(old.model_dump_json()).model_dump_json()
        == old.model_dump_json()
    )
    with pytest.raises(ValueError):
        TicketCandidate.model_validate(old.model_dump() | {"pass_type": "3X4"})


@pytest.mark.parametrize("bad", [True, False, 1.5, "200", -1])
def test_money_requires_integer_fen(bad):
    with pytest.raises(ValueError):
        StrategySourceV1.model_validate(source().model_dump() | {"budget_fen": bad})


@pytest.mark.parametrize("budget", [0, 100, 200, 799, 800, 2199, 2200, 1000000])
def test_budget_multiplier_and_cash_limits(budget):
    p = build_strategy_plan(source(budget=budget), StrategyProfileV1())
    assert p.total_stake_fen <= budget and p.cash_fen + p.total_stake_fen == budget
    assert all(
        t.stake_fen == 200 * t.candidate.atomic_bet_count * t.multiplier <= 600000
        and 1 <= t.multiplier <= 50
        for t in p.tickets
    )
    if budget < 200:
        assert p.status == "NO_BET"


def test_ticket_cap_can_downgrade_instead_of_aborting_catalog():
    p = build_strategy_plan(
        source(rules=SportteryRules(version="LIMITED", max_ticket_stake_fen=400)),
        StrategyProfileV1(),
    )
    assert len(p.candidates) == 11
    assert p.tickets and all(
        t.candidate.pass_type == PassType.TWO_FOLD_ONE and t.stake_fen <= 400
        for t in p.tickets
    )


def test_same_match_mutual_exclusion_and_rejected_selection_cannot_enter_ticket():
    s = source()
    c = generate_candidates(s, StrategyProfileV1())[0]
    bad = c.selections[0].model_copy(update={"match_id": c.selections[1].match_id})
    with pytest.raises(ValueError, match="distinct"):
        ticket_spec(
            c.source_hash,
            (bad, c.selections[1]),
            c.odds_refs,
            PassType.TWO_FOLD_ONE,
            s.rules,
        )
    bad = c.selections[0].model_copy(
        update={
            "status": CandidateStatus.REJECTED,
            "rejection_code": "EV_BELOW_THRESHOLD",
        }
    )
    with pytest.raises(ValueError, match="ineligible"):
        ticket_spec(
            c.source_hash, (bad, *c.selections[1:]), c.odds_refs, c.pass_type, s.rules
        )


def test_negative_ev_role_request_never_restores_candidate():
    s = source(("1.1", "1.1", "1.1"))
    ids = tuple(
        sorted(
            x.candidate_id for x in s.selections if x.selection == SelectionKey.HOME_WIN
        )
    )[:2]
    req = TicketRoleRequestV1(
        pass_type=PassType.TWO_FOLD_ONE, selection_ids=ids, role=TicketRole.HEDGE
    )
    p = build_strategy_plan(s, StrategyProfileV1(), (req,))
    assert p.status == "NO_BET" and not p.candidates


def test_duplicate_equivalent_role_requests_rejected():
    s = source(("2", "3"))
    c = generate_candidates(s, StrategyProfileV1())[0]
    a = TicketRoleRequestV1(
        pass_type=c.pass_type,
        selection_ids=tuple(x.candidate_id for x in c.selections),
        role=TicketRole.PRIMARY,
    )
    b = a.model_copy(update={"role": TicketRole.HEDGE})
    with pytest.raises(ValueError, match="multiple roles"):
        build_strategy_plan(s, StrategyProfileV1(), (a, b))


def test_qualified_small_hedge_has_a_real_core_loss_witness():
    s = source(("4", "4", "2", "2"))
    profile = StrategyProfileV1(pass_types=(PassType.TWO_FOLD_ONE,))
    c = next(
        c
        for c in generate_candidates(s, profile)
        if all(x.fixed_bonus == 2 for x in c.selections)
    )
    req = TicketRoleRequestV1(
        pass_type=c.pass_type,
        selection_ids=tuple(x.candidate_id for x in c.selections),
        role=TicketRole.HEDGE,
    )
    p = build_strategy_plan(s, profile, (req,))
    hedge = next(t for t in p.tickets if t.role == TicketRole.HEDGE)
    assert hedge.stake_fen <= s.budget_fen * Decimal("0.10")
    witness = hedge.hedge_witness
    atom = next(
        a for a in c.atomic_bets if a.atomic_bet_id == witness.surviving_atomic_bet_id
    )
    assert witness.failed_core_selection_id not in atom.selection_ids
    assert set(atom.selection_ids) & set(witness.independently_eligible_selection_ids)


def test_core_dependency_is_measured_on_constituent_stake():
    s = source(("2", "3", "4"))
    c = next(
        c
        for c in generate_candidates(s, StrategyProfileV1())
        if c.pass_type == PassType.THREE_FOLD_FOUR
    )
    risk = structural_risk([(c, 2)], StrategyProfileV1())
    assert set(risk.atomic_selection_dependency_fen.values()) == {1200}
    assert set(risk.core_selection_dependency_ratio.values()) == {Decimal("0.75")}
    assert not risk.single_selection_wipeout_ids


@pytest.mark.parametrize(
    "mutation",
    ["payout", "missing_atomic", "odds_ref", "role", "multiplier", "catalog", "cash"],
)
def test_resealed_tampering_fails_independent_replay(mutation):
    p = build_strategy_plan(source(), StrategyProfileV1())
    content = p.model_dump(exclude={"schema_version", "plan_id", "plan_hash"})
    if mutation == "payout":
        content["candidates"][0]["max_payout_fen"] += 1
    elif mutation == "missing_atomic":
        content["candidates"][0]["atomic_bets"] = ()
    elif mutation == "odds_ref":
        content["candidates"][0]["odds_refs"][0]["payload_hash"] = "wrong"
    elif mutation == "role":
        content["tickets"][0]["role"] = TicketRole.LONGSHOT
    elif mutation == "multiplier":
        content["tickets"][0]["multiplier"] = True
    elif mutation == "catalog":
        content["candidates"] = content["candidates"][:-1]
    else:
        content["cash_fen"] += 200
    with pytest.raises(ValueError):
        StrategyPassPlanV1.freeze(**content)


@pytest.mark.parametrize(
    "field,value",
    [
        ("absolute_max_tickets", 9),
        ("preferred_max_tickets", True),
        ("preferred_primary_max", 4),
        ("pass_types", ("4X11", "2X1")),
    ],
)
def test_profile_rejects_invalid_limits(field, value):
    with pytest.raises(ValueError):
        StrategyProfileV1.model_validate({field: value})


def test_generation_bound_fails_without_partial_catalog():
    with pytest.raises(ValueError, match="candidate bound"):
        build_strategy_plan(source(), StrategyProfileV1(max_generated_candidates=1))


def test_preferred_is_soft_absolute_is_hard_and_extra_tickets_need_new_matches():
    s = source(("4",) * 16, budget=100000)
    profile = StrategyProfileV1(
        pass_types=(PassType.TWO_FOLD_ONE,), max_input_matches=16
    )
    p = build_strategy_plan(s, profile)
    assert len(p.tickets) == 8
    assert sum(t.role == TicketRole.PRIMARY for t in p.tickets) == 3
    used = set()
    for t in p.tickets:
        current = {s.match_id for s in t.candidate.selections}
        if t.ticket_no > 4:
            assert current - used
        used |= current
    lower = build_strategy_plan(
        s, profile.model_copy(update={"absolute_max_tickets": 5})
    )
    assert len(lower.tickets) == 5


def test_zero_budget_does_not_enter_combinatorial_search():
    p = build_strategy_plan(
        source(("4",) * 16, budget=0),
        StrategyProfileV1(max_input_matches=2, max_generated_candidates=1),
    )
    assert p.status == "NO_BET" and not p.candidates and p.cash_fen == 0


def test_hedge_core_limit_applies_during_multiplier_growth():
    s = source(("4", "4", "2", "2"))
    profile = StrategyProfileV1(
        pass_types=(PassType.TWO_FOLD_ONE,),
        preferred_max_tickets=2,
        absolute_max_tickets=2,
        preferred_primary_max=2,
    )
    c = next(
        c
        for c in generate_candidates(s, profile)
        if all(x.fixed_bonus == 2 for x in c.selections)
    )
    request = TicketRoleRequestV1(
        pass_type=c.pass_type,
        selection_ids=tuple(x.candidate_id for x in c.selections),
        role=TicketRole.HEDGE,
    )
    p = build_strategy_plan(s, profile, (request,))
    assert {t.role: t.stake_fen for t in p.tickets} == {
        TicketRole.PRIMARY: 3000,
        TicketRole.HEDGE: 1000,
    }
    assert p.cash_fen == 6000  # 3:1 core limit binds even with cash still available.


def test_allocated_ticket_rejects_incorrect_fen_rounding():
    p = build_strategy_plan(source(("2.035", "3.041")), StrategyProfileV1())
    t = p.tickets[0]
    assert t.max_payout_fen == 1238 * t.multiplier
    with pytest.raises(ValueError):
        SystemTicketV1.model_validate(t.model_dump() | {"stake_fen": t.stake_fen + 1})
    with pytest.raises(ValueError):
        SystemTicketCandidateV1.model_validate(
            t.candidate.model_dump() | {"base_stake_fen": True}
        )
