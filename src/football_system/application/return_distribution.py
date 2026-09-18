"""Sealed-source-only evaluation/search use cases; no inference/provider adapter."""

from pydantic import Field

from football_system.application.ports.return_distribution import ReturnDistributionRepository
from football_system.domain.common import Identifier
from football_system.domain.market_v2 import Hash, MultiMarketModel
from football_system.domain.return_distribution import ReturnAllocationRequestV1, ReturnEvaluationV1, ReturnOptimizationRunV1
from football_system.domain.services.return_distribution import evaluate_portfolio
from football_system.domain.services.return_optimizer import optimize_portfolio


class EvaluateReturnRequestV1(MultiMarketModel):
    plan_id: Identifier
    allocations: tuple[ReturnAllocationRequestV1, ...] = Field(max_length=8)
    expected_policy_hash: Hash | None = None
    expected_objective_hash: Hash | None = None


class OptimizeReturnRequestV1(MultiMarketModel):
    plan_id: Identifier
    expected_policy_hash: Hash | None = None
    expected_objective_hash: Hash | None = None


class ReturnDistributionService:
    def __init__(self, repository: ReturnDistributionRepository, policy, objective):
        self.repository, self.policy, self.objective = repository, policy, objective

    def _check(self, request):
        if request.expected_policy_hash not in (None, self.policy.content_hash) or request.expected_objective_hash not in (None, self.objective.content_hash):
            raise ValueError("RETURN_CONFIGURATION_HASH_MISMATCH")

    def evaluate(self, request):
        self._check(request)
        value = evaluate_portfolio(self.repository.load_plan(request.plan_id), request.allocations,
                                   policy=self.policy, objective=self.objective)
        return self.repository.save(value)

    def optimize(self, request):
        self._check(request)
        value = optimize_portfolio(self.repository.load_plan(request.plan_id), policy=self.policy, objective=self.objective)
        return self.repository.save(value)


def return_report(value):
    if not isinstance(value, (ReturnEvaluationV1, ReturnOptimizationRunV1)):
        raise ValueError("show requires a sealed return evaluation or optimization run")
    run = isinstance(value, ReturnOptimizationRunV1)
    evaluation = value.result if run else value
    distribution = evaluation.distribution if evaluation is not None else None
    metrics = evaluation.metrics if evaluation is not None else None
    stake = distribution.stake_fen if distribution is not None else 0
    return dict(
        artifact_id=value.artifact_id, content_hash=value.content_hash, schema_version=value.schema_version,
        status=value.status, reason=value.reason, budget_fen=value.binding.budget_fen,
        stake_fen=stake, cash_fen=value.binding.budget_fen-stake,
        ticket_count=len(evaluation.selected) if evaluation is not None else 0,
        selected=evaluation.selected if evaluation is not None else (),
        metrics=metrics, utility=value.utility,
        legacy_v2_utility=value.legacy_v2_utility if run else None,
        utility_delta=value.utility_delta if run else None,
        feasible=evaluation.feasible if evaluation is not None else False,
        violations=evaluation.violations if evaluation is not None else (value.reason,),
        assumptions=("INDEPENDENT_MATCHES_V1", "CROSS_MARKET_SAME_MATCH_UNSUPPORTED"),
        optimization_method=value.optimization_method if run else None,
        dominated_candidate_count=value.dominated_candidate_count if run else None,
        pareto_candidate_count=value.pareto_candidate_count if run else None,
        policy_hash=value.policy.content_hash, objective_hash=value.objective.content_hash,
        code_hash=value.policy.code_hash, artifact=value,
    )
