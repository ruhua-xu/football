"""Exact finite-Decimal return algebra under INDEPENDENT_MATCHES_V1.

Only the portfolio kernel enumerates states; it never computes SP, EV or P_final.
All caches below live within one evaluation/optimization, not across requests.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, Rounded, ROUND_HALF_EVEN, localcontext
from itertools import product
from math import prod

from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import content_hash, fixed_decimal, revalidate
from football_system.domain.return_distribution import (
    PRECISION, AtomicReturnV1, MatchReturnStateV1, PortfolioReturnDistributionV1,
    RelevantMatchStateV1, ReturnAllocationRequestV1, ReturnComponentV1,
    ReturnDistributionMetricsV1, ReturnEvaluationV1,
    ReturnLegV1, ReturnSelectedTicketV1,
    ReturnSourceBindingV1, ReturnSupportPointV1, TicketReturnFunctionV1,
)
from football_system.domain.services.payout import calculate_stake_fen
from football_system.domain.services.strategy_pass_v2 import hedge_v2, structural_risk_v2
from football_system.domain.strategy_pass import TicketRole
from football_system.domain.strategy_pass_v2 import StrategyPassPlanV2


class ReturnDistributionError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def require(condition, reason):
    if not condition:
        raise ReturnDistributionError(reason)


@fixed_decimal(PRECISION)
def number(value):
    """Canonical exact Decimal; 512 digits exceed the proved finite products."""
    value = Decimal(value)
    return Decimal(0) if value == 0 else value.normalize()


def canonical_allocations(allocations):
    values = tuple(revalidate(a) if isinstance(a, ReturnAllocationRequestV1) else ReturnAllocationRequestV1.model_validate(a) for a in allocations)
    require(len(values) <= 8, "DISTRIBUTION_COMPLEXITY_LIMIT")
    require(len({a.ticket_candidate_id for a in values}) == len(values), "DUPLICATE_TICKET_CANDIDATE")
    return tuple(sorted(values, key=lambda a: a.ticket_candidate_id))


def binding_for(plan):
    refs = tuple(ArtifactRefV1.of(c) for c in sorted(plan.candidates, key=lambda c: c.artifact_id))
    return ReturnSourceBindingV1(
        plan=ArtifactRefV1.of(plan), source=ArtifactRefV1.of(plan.source),
        analysis=ArtifactRefV1.of(plan.source.analysis), fusion=ArtifactRefV1.of(plan.source.fusion),
        strategy_profile_hash=plan.profile.profile_hash,
        candidate_catalog_hash=content_hash("RETURN_CANDIDATE_CATALOG_V1", refs), candidate_count=len(refs),
        budget_fen=plan.source.budget_fen, rules=plan.source.analysis.rules, constraints=plan.source.analysis.constraints,
    )


def ticket_function(candidate):
    return TicketReturnFunctionV1.freeze(
        candidate=ArtifactRefV1.of(candidate), source=candidate.source,
        pass_type=candidate.pass_type, unit_stake_fen=candidate.unit_stake_fen,
        atomic_bets=tuple(AtomicReturnV1(
            atomic=ArtifactRefV1.of(a), gross_payout_fen=a.gross_payout_fen,
            legs=tuple(ReturnLegV1(outcome_candidate=ArtifactRefV1.of(c), match_id=c.match_id,
                                   market_key=c.market_key, outcome=c.outcome) for c in a.legs),
        ) for a in candidate.atomic_bets),
    )


@dataclass
class PreparedReturnSource:
    plan: StrategyPassPlanV2
    binding: ReturnSourceBindingV1
    catalog: dict
    functions: dict
    marginals: dict
    units: dict
    hints: dict


def prepare_source(plan):
    if type(plan) is not StrategyPassPlanV2:
        raise ValueError("sealed StrategyPassPlanV2 required")
    plan = revalidate(plan)
    catalog = {c.artifact_id: c for c in plan.candidates}
    eligible = {(s.match_id, s.market_key.canonical, s.outcome) for s in plan.source.selections if s.status == "ELIGIBLE"}
    hints = {}
    for request in plan.requests:
        signature = (request.pass_type, tuple((c.match_id, c.market_key.canonical,
            tuple(o for o in c.outcomes if (c.match_id, c.market_key.canonical, o) in eligible)) for c in request.choices))
        hints[signature] = request.role
    roles = {}
    for c in plan.candidates:
        signature = (c.pass_type, tuple((s.match_id, s.market_key.canonical, tuple(o.outcome for o in s.candidates)) for s in c.choice_sets))
        roles[c.artifact_id] = hints.get(signature)
    return PreparedReturnSource(
        plan, binding_for(plan), catalog, {},
        {(r.match_id, r.market_key.canonical): r for r in plan.source.fusion.results},
        {u.artifact_id: u for u in plan.source.analysis.units}, roles,
    )


@fixed_decimal(PRECISION)
def relevant_matches(prepared, candidates, policy):
    keys, relevant = {}, defaultdict(set)
    for c in candidates:
        for choice in c.choice_sets:
            previous = keys.setdefault(choice.match_id, choice.market_key)
            require(previous == choice.market_key, "CROSS_MARKET_JOINT_UNAVAILABLE")
            relevant[choice.match_id].update(x.outcome for x in choice.candidates)
    require(len(keys) <= policy.max_unique_matches, "DISTRIBUTION_COMPLEXITY_LIMIT")
    values = []
    for match_id, key in sorted(keys.items()):
        result = prepared.marginals.get((match_id, key.canonical))
        require(result is not None and result.p_final is not None, "P_FINAL_UNAVAILABLE")
        marginal = result.p_final
        require(sum((p.probability for p in marginal.outcomes), Decimal(0)) == 1, "P_FINAL_MASS_NOT_EXACTLY_ONE")
        chosen = tuple(o for o in key.catalog if o in relevant[match_id])
        states = tuple(MatchReturnStateV1(outcome=o, probability=number(marginal.probability(o))) for o in chosen)
        if len(chosen) != len(key.catalog):
            rest = 1 - sum((s.probability for s in states), Decimal(0))
            require(rest >= 0, "NEGATIVE_REST_PROBABILITY")
            states += (MatchReturnStateV1(outcome=None, probability=number(rest)),)
        unit = prepared.units[result.unit_id]
        values.append(RelevantMatchStateV1.freeze(
            match_id=match_id, market_key=key, unit=ArtifactRefV1.of(unit), fusion=prepared.binding.fusion,
            sp_snapshot=ArtifactRefV1.of(unit.sporttery), marginal=marginal,
            p_final_hash=marginal.distribution_hash, states=states,
        ))
    return tuple(values)


def connected_components(functions):
    remaining = {f.candidate.artifact_id: f for f in functions}
    groups = []
    while remaining:
        seed = min(remaining)
        group = [remaining.pop(seed)]
        matches = {c.match_id for a in group[0].atomic_bets for c in a.legs}
        changed = True
        while changed:
            changed = False
            for identity, function in sorted(tuple(remaining.items())):
                ids = {c.match_id for a in function.atomic_bets for c in a.legs}
                if ids & matches:
                    matches.update(ids)
                    group.append(remaining.pop(identity))
                    changed = True
        groups.append((tuple(sorted(matches)), tuple(sorted(group, key=lambda f: f.candidate.artifact_id))))
    return tuple(sorted(groups, key=lambda g: (g[0], tuple(f.candidate.artifact_id for f in g[1]))))


def support_points(values, limit):
    require(len(values) <= limit, "DISTRIBUTION_COMPLEXITY_LIMIT")
    require(all(p >= 0 for p in values.values()) and sum(values.values(), Decimal(0)) == 1, "EXACT_PROBABILITY_CLOSURE_FAILED")
    return tuple(ReturnSupportPointV1(gross_payout_fen=g, probability=number(p)) for g, p in sorted(values.items()) if p != 0)


def gross_for_world(function, world):
    """Uses sealed atomic gross amounts, not odds multiplication. REST misses every leg."""
    return sum(a.gross_payout_fen for a in function.atomic_bets if all(world[c.match_id] == c.outcome for c in a.legs))


@fixed_decimal(PRECISION)
def distribution_values(binding, policy, matches, functions, allocations, *, work_budget=None):
    ids = tuple(a.ticket_candidate_id for a in allocations)
    require(ids == tuple(sorted(set(ids))) and len(ids) <= policy.max_selected_tickets, "DISTRIBUTION_COMPLEXITY_LIMIT")
    require(tuple(f.candidate.artifact_id for f in functions) == ids, "TICKET_FUNCTION_BINDING_MISMATCH")
    require(tuple(m.match_id for m in matches) == tuple(sorted({m.match_id for m in matches})), "MATCH_STATE_ORDER_MISMATCH")
    require(len(matches) <= policy.max_unique_matches and len(str(binding.budget_fen)) <= policy.max_budget_digits, "DISTRIBUTION_COMPLEXITY_LIMIT")
    by_match = {m.match_id: m for m in matches}
    multipliers = {a.ticket_candidate_id: a.multiplier for a in allocations}
    selected = defaultdict(set)
    for f in functions:
        require(f.source == binding.source, "TICKET_SOURCE_BINDING_MISMATCH")
        for a in f.atomic_bets:
            for leg in a.legs:
                require(leg.match_id in by_match and by_match[leg.match_id].market_key == leg.market_key, "CROSS_MARKET_JOINT_UNAVAILABLE")
                selected[leg.match_id].add(leg.outcome)
    require(set(selected) == set(by_match), "EXTRA_OR_MISSING_MATCH_STATE")
    for mid, outcomes in selected.items():
        require({s.outcome for s in by_match[mid].states if s.outcome is not None} == outcomes, "RELEVANT_STATE_UNION_MISMATCH")
    stake = 0
    for f in functions:
        try:
            amount = calculate_stake_fen(len(f.atomic_bets), multipliers[f.candidate.artifact_id], binding.rules)
        except ValueError as error:
            raise ReturnDistributionError("BANKROLL_OR_TICKET_LIMIT") from error
        require(amount == f.unit_stake_fen * multipliers[f.candidate.artifact_id] and amount <= 600000, "BANKROLL_OR_TICKET_LIMIT")
        stake += amount
    require(stake <= binding.budget_fen, "BUDGET_EXCEEDED")
    groups = connected_components(functions)
    work = 0
    # All component world counts are checked before enumerating even the first one.
    for mids, fs in groups:
        count = prod(len(by_match[m].states) for m in mids)
        require(count <= policy.max_component_world_states, "DISTRIBUTION_COMPLEXITY_LIMIT")
        work += count * sum(len(a.legs) for f in fs for a in f.atomic_bets)
    require(work <= policy.max_evaluation_work, "DISTRIBUTION_COMPLEXITY_LIMIT")
    require(work_budget is None or work <= work_budget, "OPTIMIZER_WORK_LIMIT")
    components = []
    exact = Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)
    exact.traps[Inexact] = True
    exact.traps[Rounded] = True
    with localcontext(exact):
        for mids, fs in groups:
            values = defaultdict(Decimal)
            choices = tuple(by_match[m].states for m in mids)
            for states in product(*choices):
                probability = prod((s.probability for s in states), start=Decimal(1))
                if probability == 0:  # Exact zero only; no threshold or sampling.
                    continue
                world = dict(zip(mids, (s.outcome for s in states), strict=True))
                gross = sum(gross_for_world(f, world) * multipliers[f.candidate.artifact_id] for f in fs)
                require(gross <= policy.max_gross_payout_fen, "DISTRIBUTION_AMOUNT_LIMIT")
                values[gross] += probability
                require(len(values) <= policy.max_distribution_support, "DISTRIBUTION_COMPLEXITY_LIMIT")
            points = support_points(values, policy.max_distribution_support)
            components.append(ReturnComponentV1(match_ids=mids, ticket_candidate_ids=tuple(f.candidate.artifact_id for f in fs),
                                               world_state_count=prod(map(len, choices)), support=points))
        combined = {0: Decimal(1)}
        for component in components:
            pairs = len(combined) * len(component.support)
            require(pairs <= policy.max_convolution_products, "DISTRIBUTION_COMPLEXITY_LIMIT")
            work += pairs
            require(work <= policy.max_evaluation_work, "DISTRIBUTION_COMPLEXITY_LIMIT")
            require(work_budget is None or work <= work_budget, "OPTIMIZER_WORK_LIMIT")
            values = defaultdict(Decimal)
            for left, probability in sorted(combined.items()):
                for point in component.support:
                    gross = left + point.gross_payout_fen
                    require(gross <= policy.max_gross_payout_fen, "DISTRIBUTION_AMOUNT_LIMIT")
                    values[gross] += probability * point.probability
                    require(len(values) <= policy.max_distribution_support, "DISTRIBUTION_COMPLEXITY_LIMIT")
            combined = values
        support = support_points(combined, policy.max_distribution_support)
    return dict(stake_fen=stake, cash_fen=binding.budget_fen-stake,
                components=tuple(components), support=support, work_units=work)


@fixed_decimal(PRECISION)
def metric_values(support, budget, stake, cash):
    require(cash + stake == budget, "METRICS_CASH_MISMATCH")
    require(sum((p.probability for p in support), Decimal(0)) == 1, "EXACT_PROBABILITY_CLOSURE_FAILED")
    require(tuple(p.gross_payout_fen for p in support) == tuple(sorted({p.gross_payout_fen for p in support})), "NONCANONICAL_DISTRIBUTION_SUPPORT")
    values = [(p.gross_payout_fen, cash+p.gross_payout_fen, p.probability) for p in support]
    def probability(predicate):
        return number(sum((p for g, e, p in values if predicate(g, e)), Decimal(0)))
    gross = number(sum((Decimal(g)*p for g, _, p in values), Decimal(0)))
    ending = number(Decimal(cash) + gross)
    profit = number(ending-budget)
    cumulative, median = Decimal(0), None
    for _, e, p in values:
        cumulative += p
        if cumulative >= Decimal("0.5"):
            median = e
            break
    ratio = number(profit/Decimal(budget)) if budget else None
    return dict(budget_fen=budget, stake_fen=stake, cash_fen=cash, deep_loss_threshold_fen=budget//5,
        p_gross_payout_gt_zero=probability(lambda g, e: g > 0),
        p_break_even_or_better=probability(lambda g, e: e >= budget),
        p_2x_budget=probability(lambda g, e: e >= 2*budget), p_3x_budget=probability(lambda g, e: e >= 3*budget),
        p_loss=probability(lambda g, e: e < budget), p_deep_loss=probability(lambda g, e: e <= budget//5),
        expected_gross_payout_fen=gross, expected_ending_capital_fen=ending, expected_profit_fen=profit,
        expected_profit_ratio=ratio, median_ending_capital_fen=median,
        expected_shortfall_to_budget_fen=number(sum((Decimal(max(budget-e, 0))*p for _, e, p in values), Decimal(0))))


def calculate_metrics(distribution):
    return ReturnDistributionMetricsV1.freeze(distribution=ArtifactRefV1.of(distribution), **metric_values(
        distribution.support, distribution.binding.budget_fen, distribution.stake_fen, distribution.cash_fen))


@fixed_decimal(PRECISION)
def distribution_utility(metrics, objective):
    positive = sum((getattr(metrics, name) or Decimal(0))*weight for name, weight in objective.positive_weights.model_dump().items())
    penalty = sum(getattr(metrics, name)*weight for name, weight in objective.negative_weights.model_dump().items())
    return number(positive-penalty)


@fixed_decimal(PRECISION)
def feasibility(prepared, allocations, objective):
    plan, budget = prepared.plan, prepared.binding.budget_fen
    profile, constraints = plan.profile, plan.source.analysis.constraints
    candidates = [prepared.catalog[a.ticket_candidate_id] for a in allocations]
    amounts = {a.ticket_candidate_id: a.multiplier for a in allocations}
    roles, witnesses, violations = {}, {}, set()
    explicit_primary = sum(prepared.hints[c.artifact_id] == TicketRole.PRIMARY for c in candidates)
    automatic_slots = max(0, profile.preferred_primary_max-explicit_primary)
    for c in candidates:
        role = prepared.hints[c.artifact_id]
        if role is None:
            role = TicketRole.PRIMARY if automatic_slots else TicketRole.SECONDARY
            automatic_slots -= int(role == TicketRole.PRIMARY)
        roles[c.artifact_id] = role
    if sum(r == TicketRole.PRIMARY for r in roles.values()) > profile.preferred_primary_max:
        violations.add("PRIMARY_ROLE_LIMIT")
    primary = [c for c in candidates if roles[c.artifact_id] in {TicketRole.PRIMARY, TicketRole.SECONDARY}]
    auxiliary = [c for c in candidates if roles[c.artifact_id] in {TicketRole.HEDGE, TicketRole.LONGSHOT}]
    if len(auxiliary) > profile.max_auxiliary_tickets:
        violations.add("AUXILIARY_TICKET_LIMIT")
    preferred = min(profile.preferred_max_tickets, constraints.preferred_max_tickets, objective.preferred_max_tickets)
    absolute = min(profile.absolute_max_tickets, constraints.absolute_max_tickets, objective.absolute_max_tickets)
    if len(candidates) > absolute:
        violations.add("ABSOLUTE_TICKET_LIMIT")
    used = set()
    selected = []
    for index, c in enumerate(candidates):
        role, multiplier = roles[c.artifact_id], amounts[c.artifact_id]
        stake = c.unit_stake_fen * multiplier
        if c.expected_roi < plan.source.analysis.min_ticket_roi:
            violations.add("TICKET_ROI_BELOW_THRESHOLD")
        if role in {TicketRole.HEDGE, TicketRole.LONGSHOT}:
            if stake > budget * profile.max_auxiliary_budget_ratio:
                violations.add("AUXILIARY_BUDGET_LIMIT")
            if role == TicketRole.HEDGE:
                witnesses[c.artifact_id] = hedge_v2(c, primary)
                if witnesses[c.artifact_id] is None:
                    violations.add("NO_SURVIVING_EXPANDED_WITNESS")
            elif (not primary or Decimal(c.max_payout_fen)/c.unit_stake_fen < profile.longshot_min_max_return_multiple
                  or not ({leg.logical_key for a in c.atomic_bets for leg in a.legs}
                          - {leg.logical_key for p in primary for a in p.atomic_bets for leg in a.legs})):
                violations.add("LONGSHOT_QUALIFICATION_FAILED")
        mids = {s.match_id for s in c.choice_sets}
        if index >= preferred and (not mids-used or c.expected_roi-constraints.operational_complexity_penalty*(index-preferred+1) < constraints.extra_ticket_min_roi):
            violations.add("EXTRA_TICKET_QUALIFICATION_FAILED")
        used.update(mids)
        selected.append(ReturnSelectedTicketV1(ticket_candidate_id=c.artifact_id, multiplier=multiplier,
            candidate_hash=c.content_hash, role=role, pass_type=c.pass_type, stake_fen=stake,
            hedge_witness=witnesses.get(c.artifact_id)))
    risk = structural_risk_v2([(c, amounts[c.artifact_id]) for c in candidates], profile)
    if risk.concentrated:
        violations.add("EXPANDED_STRUCTURAL_CONCENTRATION")
    if any(v > budget*constraints.max_match_exposure_ratio for v in risk.match_exposure_fen.values()):
        violations.add("MATCH_EXPOSURE_LIMIT")
    if any(v > budget*constraints.max_selection_exposure_ratio for v in risk.outcome_exposure_fen.values()):
        violations.add("OUTCOME_EXPOSURE_LIMIT")
    return tuple(selected), risk, tuple(sorted(violations))


@fixed_decimal(PRECISION)
def evaluate_prepared(prepared, allocations, policy, objective, *, work_budget=None):
    allocations = canonical_allocations(allocations)
    try:
        require(all(a.ticket_candidate_id in prepared.catalog for a in allocations), "UNKNOWN_TICKET_CANDIDATE")
        require(len(allocations) <= policy.max_selected_tickets, "DISTRIBUTION_COMPLEXITY_LIMIT")
        candidates = tuple(prepared.catalog[a.ticket_candidate_id] for a in allocations)
        matches = relevant_matches(prepared, candidates, policy)
        functions = []
        for candidate in candidates:
            if candidate.artifact_id not in prepared.functions:
                prepared.functions[candidate.artifact_id] = ticket_function(candidate)
            functions.append(prepared.functions[candidate.artifact_id])
        functions = tuple(functions)
        values = distribution_values(prepared.binding, policy, matches, functions, allocations, work_budget=work_budget)
        distribution = PortfolioReturnDistributionV1.freeze(binding=prepared.binding, policy=policy, matches=matches,
            ticket_functions=functions, allocations=allocations, **values)
        metrics = calculate_metrics(distribution)
        selected, risk, violations = feasibility(prepared, allocations, objective)
        return ReturnEvaluationV1.freeze(binding=prepared.binding, policy=policy, objective=objective, requested=allocations,
            status="AVAILABLE", reason=None, distribution=distribution, metrics=metrics, selected=selected,
            risk=risk, feasible=not violations, violations=violations, utility=distribution_utility(metrics, objective))
    except ReturnDistributionError as error:
        return ReturnEvaluationV1.freeze(binding=prepared.binding, policy=policy, objective=objective, requested=allocations,
            status="DISTRIBUTION_UNAVAILABLE", reason=error.reason, distribution=None, metrics=None, selected=(),
            risk=None, feasible=False, violations=(error.reason,), utility=None)


def evaluate_portfolio(plan, allocations, *, policy, objective):
    return evaluate_prepared(prepare_source(plan), allocations, revalidate(policy), revalidate(objective))


def payout_for_assignment(distribution, assignment):
    """Map concrete outcomes to the exact selected-or-REST equivalence classes."""
    require(set(assignment) == {s.match_id for s in distribution.matches}, "WORLD_MATCH_COVERAGE_MISMATCH")
    world = {}
    for match in distribution.matches:
        outcome = assignment[match.match_id]
        if outcome is not None:
            require(outcome in match.market_key.catalog, "WORLD_OUTCOME_NOT_IN_CATALOG")
        options = {s.outcome for s in match.states}
        world[match.match_id] = outcome if outcome in options else None
        require(world[match.match_id] in options, "WORLD_REST_UNAVAILABLE")
    multipliers = {a.ticket_candidate_id: a.multiplier for a in distribution.allocations}
    return sum(gross_for_world(f, world)*multipliers[f.candidate.artifact_id] for f in distribution.ticket_functions)
