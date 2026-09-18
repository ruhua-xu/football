from decimal import Decimal, localcontext, ROUND_UP
from itertools import combinations

from football_system.domain.archive import canonical_json
from football_system.domain.return_distribution import ReturnObjectiveProfileV1
from football_system.domain.services.return_distribution import evaluate_portfolio
from football_system.domain.services.return_optimizer import dominates, optimize_portfolio
from football_system.domain.strategy_pass_v2 import StrategyProfileV2
from tests.unit.return_distribution_fixtures import fixed_plan, policies, allocations, candidate_by_matches


def optimize(plan, **limits):
    policy, _ = policies()
    return optimize_portfolio(plan, policy=policy, objective=ReturnObjectiveProfileV1.freeze(**limits))


def test_n_no_bet_beats_all_nonpositive_marginal_candidates():
    plan = fixed_plan(dict.fromkeys("AB", ".5"), dict.fromkeys("AB", "2.01"), [{"choices": "AB"}])
    run = optimize(plan)
    assert run.status == "NO_BET" and not run.result.selected
    assert run.utility == run.baseline.utility == 0
    assert run.result.metrics.cash_fen == 10000
    assert run.steps == ()


def test_k_diversification_opening_prefers_lower_roi_independent_ticket():
    plan = fixed_plan({**dict.fromkeys("ABCD", ".6"), **dict.fromkeys("EFG", ".59")}, dict.fromkeys("ABCDEFG", "2"),
        [{"choices": "ABC", "pass_type": "3X4"}, {"choices": "ABD", "pass_type": "3X4"}, {"choices": "EFG", "pass_type": "3X4"}],
        profile=StrategyProfileV2(max_choice_set_overlap="1"))
    independent = candidate_by_matches(plan, "EFG")
    assert independent.expected_roi < candidate_by_matches(plan, "ABC").expected_roi
    run = optimize(plan)
    openings = [s for s in run.steps if s.phase == "OPEN_TICKET"]
    assert run.status == "OPTIMIZED" and len(openings) >= 2
    assert openings[1].ticket_candidate_id == independent.artifact_id
    assert all(s.marginal_utility > 0 for s in run.steps)
    assert run.optimization_method == "RETURN_DISTRIBUTION_MARGINAL_V1"
    assert run.optimality == "DETERMINISTIC_MARGINAL_NOT_GLOBAL_OPTIMUM"


def test_l_positive_hedge_witness_is_selected_only_with_positive_utility():
    plan = fixed_plan({"A": ".6", "B": ".6", "C": ".5", "D": ".5"}, {"A": "4", "B": "4", "C": "2", "D": "2"},
        [{"choices": "AB", "role": "PRIMARY"}, {"choices": "CD", "role": "HEDGE"}])
    run = optimize(plan)
    hedge = next(t for t in run.result.selected if t.role == "HEDGE")
    assert hedge.hedge_witness is not None
    assert hedge.hedge_witness.independent_outcome_keys
    assert hedge.stake_fen <= Decimal(".1") * run.binding.budget_fen
    assert next(s for s in run.steps if s.phase == "OPEN_TICKET" and s.ticket_candidate_id == hedge.ticket_candidate_id).marginal_utility > 0


def test_l_eligible_hedge_with_negative_distribution_delta_is_not_forced():
    plan = fixed_plan({"A": ".6", "B": ".6", "C": ".5", "D": ".5"}, {"A": "2", "B": "5.5", "C": "2", "D": "2"},
        [{"choices": "AB", "role": "PRIMARY"}, {"choices": "CD", "role": "HEDGE"}], budget=2000)
    policy, profile = policies()
    primary = candidate_by_matches(plan, "AB")
    alone = evaluate_portfolio(plan, [{"ticket_candidate_id": primary.artifact_id, "multiplier": 1}], policy=policy, objective=profile)
    combined = evaluate_portfolio(plan, allocations(plan), policy=policy, objective=profile)
    assert combined.feasible and any(t.hedge_witness for t in combined.selected)
    assert combined.utility < alone.utility
    run = optimize(plan)
    assert not any(t.role == "HEDGE" for t in run.result.selected)


def test_m_longshot_p3x_contribution_and_small_budget():
    plan = fixed_plan({"A": ".6", "B": ".6", "C": ".1", "D": ".1"}, {"A": "4", "B": "4", "C": "20", "D": "20"},
        [{"choices": "AB", "role": "PRIMARY"}, {"choices": "CD", "role": "LONGSHOT"}])
    run = optimize(plan)
    longshot = next(t for t in run.result.selected if t.role == "LONGSHOT")
    assert run.result.metrics.p_3x_budget > 0
    assert longshot.stake_fen <= 1000
    assert next(s for s in run.steps if s.phase == "OPEN_TICKET" and s.ticket_candidate_id == longshot.ticket_candidate_id).marginal_utility > 0


def test_m_high_payout_without_utility_is_rejected_and_bad_ev_never_restored():
    for p in (".05", ".01"):
        plan = fixed_plan({"A": ".6", "B": ".6", "C": p, "D": p}, {"A": "2", "B": "5.5", "C": "20", "D": "20"},
            [{"choices": "AB", "role": "PRIMARY"}, {"choices": "CD", "role": "LONGSHOT"}], budget=2000)
        run = optimize(plan)
        assert not any(t.role == "LONGSHOT" for t in run.result.selected)
        if p == ".01":
            assert len(plan.candidates) == 1
            assert any("INELIGIBLE_OUTCOME_EXCLUDED" in d for d in plan.decisions)
            assert len(run.candidate_catalog) == 1


def test_dominance_is_counted_and_obvious_dominated_candidate_not_prioritized():
    plan = fixed_plan(dict.fromkeys("ABCD", ".6"), {"A": "5", "B": "5", "C": "4", "D": "4"},
                      [{"choices": "AB"}, {"choices": "CD"}])
    policy, profile = policies()
    left = candidate_by_matches(plan, "AB")
    right = candidate_by_matches(plan, "CD")
    a = evaluate_portfolio(plan, [{"ticket_candidate_id": left.artifact_id, "multiplier": 1}], policy=policy, objective=profile)
    b = evaluate_portfolio(plan, [{"ticket_candidate_id": right.artifact_id, "multiplier": 1}], policy=policy, objective=profile)
    assert a.utility > 0 and b.utility > 0 and dominates(a.metrics, b.metrics)
    run = optimize(plan)
    assert run.steps[0].ticket_candidate_id == left.artifact_id
    assert run.dominated_candidate_count > 0
    assert run.dominated_candidate_count + run.pareto_candidate_count == run.evaluated_candidate_count


def test_o_catalog_bound_is_not_top_n_and_preserves_v2_recommendation():
    requests = [{"choices": pair} for pair in combinations("ABCDEFGH", 2)]
    requests += [{"choices": triple, "pass_type": "3X4"} for triple in tuple(combinations("ABCDEFGH", 3))[:5]]
    plan = fixed_plan(dict.fromkeys("ABCDEFGH", ".6"), dict.fromkeys("ABCDEFGH", "3"), requests)
    original = canonical_json(plan)
    run = optimize(plan)
    assert len(plan.candidates) == len(run.candidate_catalog) == 33
    assert run.status == "OPTIMIZER_UNAVAILABLE" and run.reason == "CANDIDATE_SEARCH_SPACE_TOO_LARGE"
    assert run.result is None and run.legacy is None
    assert canonical_json(plan) == original


def test_optimizer_work_and_evaluation_guards_have_no_partial_recommendation():
    plan = fixed_plan(dict.fromkeys("AB", ".6"), dict.fromkeys("AB", "3"), [{"choices": "AB"}])
    for limits in ({"max_optimizer_evaluations": 1}, {"max_optimizer_work": 1}):
        run = optimize(plan, **limits)
        assert run.status == "OPTIMIZER_UNAVAILABLE" and run.reason == "SEARCH_SPACE_TOO_LARGE"
        assert run.result is None and run.utility is None


def test_p_optimizer_determinism_and_legacy_reference_not_anchor():
    plan = fixed_plan(dict.fromkeys("ABCD", ".5"), dict.fromkeys("ABCD", "2.01"), [{"choices": "AB"}, {"choices": "CD"}])
    assert plan.tickets  # Old nonnegative ROI allocation spends; new utility can reject.
    expected = optimize(plan)
    assert expected.status == "NO_BET" and expected.legacy is not None
    assert expected.legacy.requested
    assert expected.utility_delta == expected.utility-expected.legacy_v2_utility
    with localcontext() as context:
        context.prec = 7
        context.rounding = ROUND_UP
        assert canonical_json(optimize(plan)) == canonical_json(expected)
