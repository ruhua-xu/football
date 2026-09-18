"""Deterministic marginal search, explicitly not a global/exhaustive optimizer."""

from collections import Counter
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import fixed_decimal, revalidate
from football_system.domain.return_distribution import (
    PRECISION, ReturnAllocationRequestV1, ReturnOptimizationRunV1, ReturnOptimizationStepV1,
)
from football_system.domain.services.return_distribution import (
    ReturnDistributionError, evaluate_prepared, number, prepare_source,
)


def dominates(left, right):
    larger = ("expected_profit_fen", "p_break_even_or_better", "p_2x_budget", "p_3x_budget")
    smaller = ("p_loss", "p_deep_loss", "expected_shortfall_to_budget_fen")
    return (all(getattr(left, key) >= getattr(right, key) for key in larger)
            and all(getattr(left, key) <= getattr(right, key) for key in smaller)
            and (any(getattr(left, key) > getattr(right, key) for key in larger)
                 or any(getattr(left, key) < getattr(right, key) for key in smaller)))


def allocation_key(requested):
    return tuple(sorted((a.ticket_candidate_id, a.multiplier) for a in requested))


class SearchBudget:
    def __init__(self, prepared, policy, objective):
        self.prepared, self.policy, self.objective = prepared, policy, objective
        self.calls = 0
        self.work = 0
        self.metrics = {}  # metrics only, never retain all trial distributions/supports
        self.frontier = {}
        self.rejected = Counter()

    def evaluate(self, requested, *, count=True):
        if self.calls >= self.objective.max_optimizer_evaluations:
            raise ReturnDistributionError("SEARCH_SPACE_TOO_LARGE")
        self.calls += 1
        value = evaluate_prepared(self.prepared, requested, self.policy, self.objective,
                                 work_budget=self.objective.max_optimizer_work-self.work)
        if value.reason == "OPTIMIZER_WORK_LIMIT":
            raise ReturnDistributionError("SEARCH_SPACE_TOO_LARGE")
        if value.distribution is not None:
            self.work += value.distribution.work_units
        if count:
            if not value.feasible:
                for reason in value.violations:
                    self.rejected[reason] += 1
            else:
                key = allocation_key(value.requested)
                if key not in self.metrics:
                    self.metrics[key] = value.metrics
                    if not any(dominates(m, value.metrics) for m in self.frontier.values()):
                        self.frontier = {k: m for k, m in self.frontier.items() if not dominates(value.metrics, m)}
                        self.frontier[key] = value.metrics
        return value


@fixed_decimal(PRECISION)
def optimize_prepared(prepared, policy, objective):
    search = SearchBudget(prepared, policy, objective)
    baseline = evaluate_prepared(prepared, (), policy, objective)
    legacy = None
    result = None
    steps = []
    catalog = tuple(ArtifactRefV1.of(c) for _, c in sorted(prepared.catalog.items()))
    if baseline.feasible:
        search.metrics[()] = baseline.metrics
        search.frontier[()] = baseline.metrics

    def finish(status, reason):
        utility = result.utility if result is not None else None
        old_utility = legacy.utility if legacy is not None else None
        return ReturnOptimizationRunV1.freeze(
            binding=prepared.binding, policy=policy, objective=objective, candidate_catalog=catalog,
            status=status, reason=reason, baseline=baseline, result=result, legacy=legacy,
            utility=utility, legacy_v2_utility=old_utility,
            utility_delta=number(utility-old_utility) if utility is not None and old_utility is not None else None,
            steps=tuple(steps), rejected_proposals=dict(sorted(search.rejected.items())),
            evaluated_candidate_count=len(search.metrics), pareto_candidate_count=len(search.frontier),
            dominated_candidate_count=len(search.metrics)-len(search.frontier),
        )

    if len(catalog) > objective.max_optimizer_candidates:
        return finish("OPTIMIZER_UNAVAILABLE", "CANDIDATE_SEARCH_SPACE_TOO_LARGE")
    if not baseline.feasible:
        return finish("OPTIMIZER_UNAVAILABLE", baseline.reason or "NO_BET_BASELINE_UNAVAILABLE")
    current = baseline
    try:
        legacy = search.evaluate(tuple(ReturnAllocationRequestV1(
            ticket_candidate_id=t.candidate.artifact_id, multiplier=t.multiplier) for t in prepared.plan.tickets), count=False)
        absolute = min(policy.max_selected_tickets, objective.absolute_max_tickets,
                       prepared.plan.profile.absolute_max_tickets, prepared.binding.constraints.absolute_max_tickets)

        def best_increment(proposals):
            options = []
            # Retain compact scalar summaries, not a frontier of huge support arrays.
            for candidate_id, multiplier, requested in proposals:
                value = search.evaluate(requested)
                if value.feasible:
                    options.append((candidate_id, multiplier, value.requested, value.metrics, value.utility,
                                    value.distribution.content_hash))
            eligible = [o for o in options if o[4] > current.utility
                        and o[4]-current.utility >= prepared.binding.constraints.min_marginal_score
                        and not dominates(current.metrics, o[3])
                        and not any(dominates(other[3], o[3]) for other in options)]
            if not eligible:
                return None
            return min(eligible, key=lambda o: (-o[4], o[3].stake_fen, allocation_key(o[2])))

        while len(current.requested) < absolute:
            chosen_ids = {a.ticket_candidate_id for a in current.requested}
            proposals = [(identity, 1, (*current.requested, ReturnAllocationRequestV1(ticket_candidate_id=identity, multiplier=1)))
                         for identity in sorted(prepared.catalog) if identity not in chosen_ids]
            best = best_increment(proposals)
            if best is None:
                break
            value = search.evaluate(best[2], count=False)
            if value.utility != best[4] or value.distribution.content_hash != best[5]:
                raise ValueError("deterministic trial reconstruction mismatch")
            steps.append(ReturnOptimizationStepV1(phase="OPEN_TICKET", ticket_candidate_id=best[0], multiplier=1,
                before_utility=current.utility, after_utility=value.utility,
                marginal_utility=number(value.utility-current.utility), evaluated_distribution_hash=value.distribution.content_hash))
            current = value
        while current.requested:
            proposals = []
            for a in current.requested:
                if a.multiplier >= min(50, prepared.binding.rules.max_multiplier):
                    continue
                requested = tuple(ReturnAllocationRequestV1(ticket_candidate_id=x.ticket_candidate_id,
                    multiplier=x.multiplier+int(x.ticket_candidate_id == a.ticket_candidate_id)) for x in current.requested)
                proposals.append((a.ticket_candidate_id, a.multiplier+1, requested))
            best = best_increment(proposals)
            if best is None:
                break
            value = search.evaluate(best[2], count=False)
            if value.utility != best[4] or value.distribution.content_hash != best[5]:
                raise ValueError("deterministic increment reconstruction mismatch")
            steps.append(ReturnOptimizationStepV1(phase="INCREMENT_MULTIPLIER", ticket_candidate_id=best[0], multiplier=best[1],
                before_utility=current.utility, after_utility=value.utility,
                marginal_utility=number(value.utility-current.utility), evaluated_distribution_hash=value.distribution.content_hash))
            current = value
        result = current if current.utility > baseline.utility else baseline
        return finish("OPTIMIZED" if result.requested else "NO_BET", None if result.requested else "NO_POSITIVE_MARGINAL_UTILITY")
    except ReturnDistributionError as error:
        search.rejected[error.reason] += 1
        return finish("OPTIMIZER_UNAVAILABLE", error.reason)


def optimize_portfolio(plan, *, policy, objective):
    return optimize_prepared(prepare_source(plan), revalidate(policy), revalidate(objective))
