from decimal import Decimal, localcontext, ROUND_DOWN
from itertools import product
import json
from pathlib import Path

import pytest

from football_system.domain.archive import canonical_json
from football_system.domain.return_distribution import ReturnSupportPointV1
from football_system.domain.services.return_distribution import evaluate_portfolio, metric_values, payout_for_assignment
from football_system.domain.services.settlement_v2 import settle_strategy_v2
from football_system.domain.strategy_pass_v2 import StrategyProfileV2
from tests.unit.return_distribution_fixtures import fixed_plan, policies, allocations
from tests.unit.test_strategy_pass_v2 import match_results


def evaluate(plan, requested=None, **limits):
    policy, objective = policies(**limits)
    return evaluate_portfolio(plan, allocations(plan) if requested is None else requested, policy=policy, objective=objective)


def points(evaluation):
    assert evaluation.status == "AVAILABLE", evaluation.reason
    return {p.gross_payout_fen: p.probability for p in evaluation.distribution.support}


def oracle(plan, requested):
    """EXHAUSTIVE_TEST_ORACLE: full catalogs, not the production REST/component kernel."""
    candidates = {c.artifact_id: c for c in plan.candidates}
    matches = sorted({s.match_id for a in requested for s in candidates[a.ticket_candidate_id].choice_sets})
    distributions = {r.match_id: r.p_final for r in plan.source.fusion.results}
    result = {}
    with localcontext() as context:
        context.prec = 512
        for states in product(*(distributions[m].outcomes for m in matches)):
            mass = Decimal(1)
            for state in states:
                mass *= state.probability
            if mass == 0:
                continue
            world = dict(zip(matches, (s.outcome for s in states), strict=True))
            gross = sum(a.gross_payout_fen * request.multiplier for request in requested
                        for a in candidates[request.ticket_candidate_id].atomic_bets
                        if all(world[leg.match_id] == leg.outcome for leg in a.legs))
            result[gross] = result.get(gross, Decimal(0)) + mass
    return result


@pytest.mark.parametrize("budget", [0, 10000])
def test_a_no_bet_cash_and_zero_budget_metrics(budget):
    plan = fixed_plan({"A": ".5", "B": ".5"}, {"A": "2", "B": "2"}, [{"choices": "AB"}], budget=budget)
    e = evaluate(plan, ())
    m = e.metrics
    assert points(e) == {0: Decimal(1)}
    assert (m.stake_fen, m.cash_fen, m.median_ending_capital_fen) == (0, budget, budget)
    assert m.expected_gross_payout_fen == m.expected_profit_fen == m.expected_shortfall_to_budget_fen == 0
    assert m.expected_ending_capital_fen == budget
    assert m.p_gross_payout_gt_zero == m.p_loss == 0
    assert m.p_break_even_or_better == 1
    assert m.p_2x_budget == m.p_3x_budget == m.p_deep_loss == int(budget == 0)
    assert m.expected_profit_ratio == (None if budget == 0 else Decimal(0))


def test_b_single_2x1_hand_case():
    plan = fixed_plan({"A": ".5", "B": ".5"}, {"A": "3", "B": "2"}, [{"choices": "AB"}])
    e = evaluate(plan)
    assert points(e) == {0: Decimal(".75"), 1200: Decimal(".25")}
    assert e.metrics.expected_gross_payout_fen == 300
    assert e.metrics.expected_profit_fen == 100
    assert e.distribution.components[0].world_state_count == 4


def test_c_shared_match_is_one_random_variable_not_squared():
    plan = fixed_plan(dict.fromkeys("ABC", ".5"), dict.fromkeys("ABC", "3"), [{"choices": "AB"}, {"choices": "AC"}])
    e = evaluate(plan)
    assert points(e) == {0: Decimal(".625"), 1800: Decimal(".25"), 3600: Decimal(".125")}
    assert points(e)[3600] != Decimal(".5") ** 4
    assert len(e.distribution.matches) == 3
    assert len(e.distribution.components) == 1
    assert e.distribution.components[0].world_state_count == 8
    assert not e.feasible and "EXPANDED_STRUCTURAL_CONCENTRATION" in e.violations
    assert points(e) == oracle(plan, allocations(plan))


def test_e_simple_multiple_or_and_rest():
    plan = fixed_plan({"A": (".4", ".4", ".2"), "B": ".5"}, {"A": ("3", "4", "1.01"), "B": "2"},
                      [{"choices": {"A": ("HOME_WIN", "DRAW"), "B": ("HOME_WIN",)}}])
    e = evaluate(plan)
    assert points(e) == {0: Decimal(".6"), 1200: Decimal(".2"), 1600: Decimal(".2")}
    a = next(m for m in e.distribution.matches if m.match_id == "A")
    assert [(s.outcome, s.probability) for s in a.states] == [("HOME_WIN", Decimal(".4")), ("DRAW", Decimal(".4")), (None, Decimal(".2"))]


def test_full_catalog_has_no_rest_and_zero_probability_rest_is_exact():
    plan = fixed_plan({"A": (".4", ".3", ".3"), "B": ".5"}, {"A": ("3", "4", "4"), "B": "2"},
                      [{"choices": {"A": ("HOME_WIN", "DRAW", "AWAY_WIN"), "B": ("HOME_WIN",)}}])
    e = evaluate(plan)
    assert all(s.outcome is not None for s in e.distribution.matches[0].states)
    assert sum(points(e).values()) == 1


def test_f_3x4_eight_world_hand_case():
    plan = fixed_plan(dict.fromkeys("ABC", ".5"), dict.fromkeys("ABC", "2"), [{"pass_type": "3X4", "choices": "ABC"}])
    e = evaluate(plan)
    assert points(e) == {0: Decimal(".5"), 800: Decimal(".375"), 4000: Decimal(".125")}
    assert e.distribution.components[0].world_state_count == 8
    assert e.metrics.expected_gross_payout_fen == 800


def test_g_4x11_exact_oracle_and_metrics():
    plan = fixed_plan(dict.fromkeys("ABCD", ".5"), dict.fromkeys("ABCD", "2"), [{"pass_type": "4X11", "choices": "ABCD"}])
    e = evaluate(plan)
    assert points(e) == {0: Decimal(".3125"), 800: Decimal(".375"), 4000: Decimal(".25"), 14400: Decimal(".0625")}
    assert points(e) == oracle(plan, allocations(plan))
    assert e.metrics.expected_gross_payout_fen == 2200
    assert e.metrics.median_ending_capital_fen == 8600
    assert e.metrics.p_2x_budget == Decimal(".0625") and e.metrics.p_3x_budget == 0
    assert e.metrics.expected_shortfall_to_budget_fen == Decimal("1212.5")


def test_h_components_convolution_matches_whole_world_oracle():
    plan = fixed_plan(dict.fromkeys("ABCDEFG", ".5"), dict.fromkeys("ABCDEFG", "3"),
        [{"choices": "AB"}, {"choices": "AC"}, {"choices": "DE"}, {"choices": "FG"}],
        profile=StrategyProfileV2(max_choice_set_overlap="1"))
    e = evaluate(plan)
    assert points(e) == oracle(plan, allocations(plan))
    assert sorted(c.world_state_count for c in e.distribution.components) == [4, 4, 8]
    assert sum(c.world_state_count for c in e.distribution.components) < 2**7


def test_i_cash_retained_and_t_settlement_v2_consistency():
    plan = fixed_plan({"A": ".5", "B": ".5"}, {"A": "2", "B": "2"}, [{"choices": "AB"}], max_multiplier=30)
    requested = tuple({"ticket_candidate_id": t.candidate.artifact_id, "multiplier": t.multiplier} for t in plan.tickets)
    e = evaluate(plan, requested)
    assert e.metrics.stake_fen == 6000 and e.metrics.cash_fen == 4000
    assert e.metrics.p_deep_loss == 0
    for a, b in product((0, 1), repeat=2):
        scores = {"A": (a, 0 if a else 1), "B": (b, 0 if b else 1)}
        results, at = match_results(plan, scores)
        settled = settle_strategy_v2(plan, results, at)
        assignment = {"A": "HOME_WIN" if a else "AWAY_WIN", "B": "HOME_WIN" if b else "AWAY_WIN"}
        gross = payout_for_assignment(e.distribution, assignment)
        assert gross == settled.gross_payout_fen
        assert e.metrics.cash_fen + gross == settled.ending_capital_fen


def test_j_all_metric_thresholds_and_lower_median():
    support = tuple(ReturnSupportPointV1(gross_payout_fen=g, probability=p) for g, p in
                    [(0, ".1"), (5000, ".2"), (10000, ".3"), (20000, ".25"), (30000, ".15")])
    m = metric_values(support, 10000, 10000, 0)
    assert [m[k] for k in ("p_gross_payout_gt_zero", "p_break_even_or_better", "p_2x_budget", "p_3x_budget", "p_loss", "p_deep_loss")] == list(map(Decimal, (".9", ".7", ".4", ".15", ".3", ".1")))
    assert m["expected_gross_payout_fen"] == m["expected_ending_capital_fen"] == 13500
    assert m["expected_profit_fen"] == 3500 and m["expected_profit_ratio"] == Decimal(".35")
    assert m["median_ending_capital_fen"] == 10000 and m["expected_shortfall_to_budget_fen"] == 2000
    boundary = metric_values((ReturnSupportPointV1(gross_payout_fen=2000, probability="1"),), 10003, 10003, 0)
    assert boundary["deep_loss_threshold_fen"] == 2000 and boundary["p_deep_loss"] == 1


def test_formal_acceptance_fixture_hand_goldens_match_implementation():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "data/fixtures/return_distribution_v1.json").read_bytes())
    assert set(fixture["cases"]) == set("ABCDEFGHIJKLMNOPQRST")
    for label, pass_type, matches in (("B", "2X1", "AB"), ("F", "3X4", "ABC"), ("G", "4X11", "ABCD")):
        case = fixture["cases"][label]
        prices = dict(zip(matches, case["sp"], strict=True)) if "sp" in case else dict.fromkeys(matches, case["sp_each"])
        plan = fixed_plan(dict.fromkeys(matches, ".5"), prices, [{"choices": matches, "pass_type": pass_type}])
        assert points(evaluate(plan)) == {g: Decimal(p) for g, p in case["gross_support"]}


def test_tiny_positive_mass_is_not_rounded_or_discarded():
    p = (".9999999999999999999999999999", "1e-28", "0")
    plan = fixed_plan(dict.fromkeys("AB", p), dict.fromkeys("AB", "2"), [{"choices": "AB"}])
    e = evaluate(plan)
    with localcontext() as context:
        context.prec = 512
        assert points(e)[0] == 2*Decimal("1e-28")-Decimal("1e-56")
        assert sum(points(e).values()) == 1


def test_finite_tiny_profit_ratio_is_not_display_quantized_to_zero():
    with localcontext() as context:
        context.prec = 512
        q = Decimal("1e-80")
        support = (ReturnSupportPointV1(gross_payout_fen=10000, probability=1-q),
                   ReturnSupportPointV1(gross_payout_fen=10001, probability=q))
    values = metric_values(support, 10000, 10000, 0)
    assert values["expected_profit_fen"] == Decimal("1e-80")
    assert values["expected_profit_ratio"] == Decimal("1e-84")


def test_inexact_input_mass_is_unavailable_not_normalized():
    plan = fixed_plan({"A": (".6", ".2", ".1999999"), "B": ".5"}, {"A": "2", "B": "2"}, [{"choices": "AB"}])
    e = evaluate(plan)
    assert e.status == "DISTRIBUTION_UNAVAILABLE" and e.reason == "P_FINAL_MASS_NOT_EXACTLY_ONE"
    assert plan.source.fusion.results[0].p_final.outcomes[-1].probability == Decimal(".1999999")


@pytest.mark.parametrize("limits", [{"max_component_world_states": 3}, {"max_distribution_support": 1}, {"max_unique_matches": 1}, {"max_evaluation_work": 1}])
def test_o_complexity_is_explicit_unavailable(limits):
    plan = fixed_plan(dict.fromkeys("AB", ".5"), dict.fromkeys("AB", "3"), [{"choices": "AB"}])
    e = evaluate(plan, **limits)
    assert e.status == "DISTRIBUTION_UNAVAILABLE" and e.reason == "DISTRIBUTION_COMPLEXITY_LIMIT"
    assert e.distribution is None and e.utility is None


def test_p_candidate_order_and_ambient_context_are_irrelevant():
    plan = fixed_plan(dict.fromkeys("ABCD", ".6"), dict.fromkeys("ABCD", "3"), [{"choices": "AB"}, {"choices": "CD"}])
    expected = canonical_json(evaluate(plan))
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        assert canonical_json(evaluate(plan, tuple(reversed(allocations(plan))))) == expected


def test_o_convolution_pair_guard_is_independent_of_component_world_guard():
    plan = fixed_plan(dict.fromkeys("ABCD", ".5"), dict.fromkeys("ABCD", "3"), [{"choices": "AB"}, {"choices": "CD"}])
    e = evaluate(plan, max_convolution_products=3)
    assert e.status == "DISTRIBUTION_UNAVAILABLE" and e.reason == "DISTRIBUTION_COMPLEXITY_LIMIT"


def test_small_exhaustive_allocation_oracle_is_only_test_code():
    plan = fixed_plan(dict.fromkeys("ABCD", ".6"), dict.fromkeys("ABCD", "3"), [{"choices": "AB"}, {"choices": "CD"}], budget=1000, max_multiplier=2)
    for multipliers in product(range(3), repeat=2):
        requested = tuple(type(allocations(plan)[0])(ticket_candidate_id=c.artifact_id, multiplier=m)
                          for c, m in zip(plan.candidates, multipliers, strict=True) if m)
        assert points(evaluate(plan, requested)) == oracle(plan, requested)


def test_component_preflight_precedes_any_world_materialization(monkeypatch):
    from football_system.domain.services import return_distribution as kernel
    plan = fixed_plan(dict.fromkeys("AB", ".5"), dict.fromkeys("AB", "3"), [{"choices": "AB"}])
    monkeypatch.setattr(kernel, "product", lambda *a, **kw: pytest.fail("enumeration happened before bound"))
    e = evaluate(plan, max_component_world_states=3)
    assert e.reason == "DISTRIBUTION_COMPLEXITY_LIMIT"


def test_normal_four_ticket_twelve_match_components_are_bounded_and_fast():
    from time import perf_counter
    from football_system.domain.services.return_distribution import prepare_source, evaluate_prepared
    names = "ABCDEFGHIJKL"
    plan = fixed_plan(dict.fromkeys(names, ".5"), dict.fromkeys(names, "3"),
                     [{"choices": group, "pass_type": "3X4"} for group in ("ABC", "DEF", "GHI", "JKL")])
    policy, objective = policies()
    prepared = prepare_source(plan)
    started = perf_counter()
    e = evaluate_prepared(prepared, allocations(plan), policy, objective)
    elapsed = perf_counter()-started
    assert e.status == "AVAILABLE" and len(e.distribution.components) == 4
    assert sum(c.world_state_count for c in e.distribution.components) == 32
    assert e.distribution.work_units < 2000
    assert elapsed < 20  # Generous bounded regression, not an extreme performance claim.
