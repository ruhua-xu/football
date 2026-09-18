"""Versioned 0.9 contracts; consume sealed V2 graphs, never manufacture P_final."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from football_system.domain.betting import PassType, PortfolioConstraints, SportteryRules
from football_system.domain.common import Identifier
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import (
    Hash, MarketArtifact, MarketKeyV2, MarketProbabilityDistributionV1,
    MultiMarketModel, OutcomeKeyV1, decimal_value, fixed_decimal,
)
from football_system.domain.strategy_pass import TicketRole
from football_system.domain.strategy_pass_v2 import HedgeWitnessV2, Money, StructuralRiskV2

PRECISION = 512
MAX_GROSS_FEN = 9223372036854775807
ExactProbability = Annotated[Decimal, BeforeValidator(decimal_value), Field(ge=0, le=1, max_digits=337, decimal_places=336, allow_inf_nan=False)]
FiniteDecimal = Annotated[Decimal, BeforeValidator(decimal_value), Field(allow_inf_nan=False)]
Coefficient = Annotated[Decimal, BeforeValidator(decimal_value), Field(ge=0, max_digits=29, decimal_places=28, allow_inf_nan=False)]


class ReturnDistributionPolicyV1(MarketArtifact):
    schema_version: Literal["RETURN_DISTRIBUTION_POLICY_V1"] = "RETURN_DISTRIBUTION_POLICY_V1"
    code_hash: Hash
    algorithm: Literal["RELEVANT_STATE_COMPONENT_CONVOLUTION_V1"] = "RELEVANT_STATE_COMPONENT_CONVOLUTION_V1"
    probability_assumption: Literal["INDEPENDENT_MATCHES_V1"] = "INDEPENDENT_MATCHES_V1"
    same_match_policy: Literal["CROSS_MARKET_SAME_MATCH_UNSUPPORTED"] = "CROSS_MARKET_SAME_MATCH_UNSUPPORTED"
    decimal_precision: Literal[512] = 512
    probability_policy: Literal["EXACT_FINITE_DECIMAL_NO_QUANTIZATION_V1"] = "EXACT_FINITE_DECIMAL_NO_QUANTIZATION_V1"
    ratio_policy: Literal["FIXED_CONTEXT_512_HALF_EVEN_ZERO_BUDGET_NULL_V1"] = "FIXED_CONTEXT_512_HALF_EVEN_ZERO_BUDGET_NULL_V1"
    deep_loss_policy: Literal["FLOOR_BUDGET_DIV_5_V1"] = "FLOOR_BUDGET_DIV_5_V1"
    max_component_world_states: int = Field(default=65536, ge=1, le=65536, strict=True)
    max_distribution_support: int = Field(default=65536, ge=1, le=65536, strict=True)
    max_unique_matches: int = Field(default=12, ge=1, le=12, strict=True)
    max_selected_tickets: int = Field(default=8, ge=1, le=8, strict=True)
    max_convolution_products: int = Field(default=1048576, ge=1, le=4194304, strict=True)
    max_evaluation_work: int = Field(default=16777216, ge=1, le=67108864, strict=True)
    max_gross_payout_fen: Literal[9223372036854775807] = MAX_GROSS_FEN
    max_budget_digits: Literal[128] = 128


class ReturnPositiveWeightsV1(MultiMarketModel):
    expected_profit_ratio: Coefficient = Decimal("0.45")
    p_gross_payout_gt_zero: Coefficient = Decimal("0.10")
    p_2x_budget: Coefficient = Decimal("0.20")
    p_3x_budget: Coefficient = Decimal("0.10")


class ReturnNegativeWeightsV1(MultiMarketModel):
    p_loss: Coefficient = Decimal("0.10")
    p_deep_loss: Coefficient = Decimal("0.05")


class ReturnObjectiveProfileV1(MarketArtifact):
    schema_version: Literal["RETURN_OBJECTIVE_PROFILE_V1"] = "RETURN_OBJECTIVE_PROFILE_V1"
    calibration_label: Literal["UNCALIBRATED_STRATEGY_POLICY_V1"] = "UNCALIBRATED_STRATEGY_POLICY_V1"
    optimization_method: Literal["RETURN_DISTRIBUTION_MARGINAL_V1"] = "RETURN_DISTRIBUTION_MARGINAL_V1"
    positive_weights: ReturnPositiveWeightsV1 = ReturnPositiveWeightsV1()
    negative_weights: ReturnNegativeWeightsV1 = ReturnNegativeWeightsV1()
    max_optimizer_candidates: int = Field(default=32, ge=1, le=32, strict=True)
    preferred_max_tickets: int = Field(default=4, ge=1, le=8, strict=True)
    absolute_max_tickets: int = Field(default=8, ge=1, le=8, strict=True)
    max_optimizer_evaluations: int = Field(default=4096, ge=1, le=4096, strict=True)
    max_optimizer_work: int = Field(default=67108864, ge=1, le=67108864, strict=True)
    tie_break: Literal["UTILITY_DESC_STAKE_ASC_CANONICAL_ALLOCATION_ASC_V1"] = "UTILITY_DESC_STAKE_ASC_CANONICAL_ALLOCATION_ASC_V1"
    dominance_policy: Literal["FEASIBLE_INCREMENT_FRONTIER_V1"] = "FEASIBLE_INCREMENT_FRONTIER_V1"

    @model_validator(mode="after")
    def limits(self):
        if self.preferred_max_tickets > self.absolute_max_tickets:
            raise ValueError("preferred ticket limit exceeds absolute")
        return self


class ReturnSourceBindingV1(MultiMarketModel):
    plan: ArtifactRefV1
    source: ArtifactRefV1
    analysis: ArtifactRefV1
    fusion: ArtifactRefV1
    strategy_profile_hash: Hash
    candidate_catalog_hash: Hash
    candidate_count: int = Field(ge=0, le=4096, strict=True)
    budget_fen: Money
    rules: SportteryRules
    constraints: PortfolioConstraints


class MatchReturnStateV1(MultiMarketModel):
    outcome: OutcomeKeyV1 | None  # None is REST, an equivalence class, never a sampled outcome.
    probability: ExactProbability


class RelevantMatchStateV1(MarketArtifact):
    schema_version: Literal["RELEVANT_MATCH_STATE_V1"] = "RELEVANT_MATCH_STATE_V1"
    match_id: Identifier
    market_key: MarketKeyV2
    unit: ArtifactRefV1
    fusion: ArtifactRefV1
    sp_snapshot: ArtifactRefV1
    marginal: MarketProbabilityDistributionV1
    p_final_hash: Hash
    states: tuple[MatchReturnStateV1, ...] = Field(min_length=1, max_length=31)

    @model_validator(mode="after")
    @fixed_decimal(PRECISION)
    def exact_partition(self):
        if self.marginal.market_key != self.market_key or self.marginal.distribution_hash != self.p_final_hash:
            raise ValueError("P_final hash/market binding mismatch")
        if sum((p.probability for p in self.marginal.outcomes), Decimal(0)) != 1:
            raise ValueError("P_FINAL_MASS_NOT_EXACTLY_ONE")
        chosen = tuple(s.outcome for s in self.states if s.outcome is not None)
        if not chosen or chosen != tuple(o for o in self.market_key.catalog if o in chosen):
            raise ValueError("relevant outcomes must be unique, nonempty and canonical")
        expected = tuple(MatchReturnStateV1(outcome=o, probability=self.marginal.probability(o)) for o in chosen)
        if len(chosen) < len(self.market_key.catalog):
            expected += (MatchReturnStateV1(outcome=None, probability=1-sum((s.probability for s in expected), Decimal(0))),)
        if self.states != expected or sum((s.probability for s in self.states), Decimal(0)) != 1:
            raise ValueError("REST/marginal partition mismatch")
        return self


class ReturnLegV1(MultiMarketModel):
    outcome_candidate: ArtifactRefV1
    match_id: Identifier
    market_key: MarketKeyV2
    outcome: OutcomeKeyV1


class AtomicReturnV1(MultiMarketModel):
    atomic: ArtifactRefV1
    gross_payout_fen: Money
    legs: tuple[ReturnLegV1, ...] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def unique_matches(self):
        ids = tuple(c.match_id for c in self.legs)
        if ids != tuple(sorted(set(ids))) or any(c.outcome not in c.market_key.catalog for c in self.legs):
            raise ValueError("atomic requirements must use unique ordered matches and real outcomes")
        return self


class TicketReturnFunctionV1(MarketArtifact):
    schema_version: Literal["TICKET_RETURN_FUNCTION_V1"] = "TICKET_RETURN_FUNCTION_V1"
    candidate: ArtifactRefV1
    source: ArtifactRefV1
    pass_type: PassType
    unit_stake_fen: Money
    atomic_bets: tuple[AtomicReturnV1, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def graph(self):
        ids = tuple(a.atomic.artifact_id for a in self.atomic_bets)
        if len(ids) != len(set(ids)) or self.unit_stake_fen != 200 * len(ids):
            raise ValueError("expanded atomic identity/stake mismatch")
        return self


class ReturnAllocationRequestV1(MultiMarketModel):
    ticket_candidate_id: Identifier
    multiplier: int = Field(ge=1, le=50, strict=True)


class ReturnSelectedTicketV1(ReturnAllocationRequestV1):
    candidate_hash: Hash
    role: TicketRole
    pass_type: PassType
    stake_fen: Money
    hedge_witness: HedgeWitnessV2 | None = None


class ReturnSupportPointV1(MultiMarketModel):
    gross_payout_fen: Annotated[int, Field(ge=0, le=MAX_GROSS_FEN, strict=True)]
    probability: ExactProbability

    @model_validator(mode="after")
    def positive(self):
        if self.probability <= 0:
            raise ValueError("support omits only exact zero mass")
        return self


class ReturnComponentV1(MultiMarketModel):
    match_ids: tuple[Identifier, ...]
    ticket_candidate_ids: tuple[Identifier, ...]
    world_state_count: int = Field(ge=1, le=65536, strict=True)
    support: tuple[ReturnSupportPointV1, ...] = Field(min_length=1, max_length=65536)


class PortfolioReturnDistributionV1(MarketArtifact):
    schema_version: Literal["PORTFOLIO_RETURN_DISTRIBUTION_V1"] = "PORTFOLIO_RETURN_DISTRIBUTION_V1"
    binding: ReturnSourceBindingV1
    policy: ReturnDistributionPolicyV1
    probability_assumption: Literal["INDEPENDENT_MATCHES_V1"] = "INDEPENDENT_MATCHES_V1"
    matches: tuple[RelevantMatchStateV1, ...] = Field(max_length=12)
    ticket_functions: tuple[TicketReturnFunctionV1, ...] = Field(max_length=8)
    allocations: tuple[ReturnAllocationRequestV1, ...] = Field(max_length=8)
    stake_fen: Money
    cash_fen: Money
    components: tuple[ReturnComponentV1, ...] = Field(max_length=8)
    support: tuple[ReturnSupportPointV1, ...] = Field(min_length=1, max_length=65536)
    work_units: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.return_distribution import distribution_values
        expected = distribution_values(self.binding, self.policy, self.matches, self.ticket_functions, self.allocations)
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("return distribution component/convolution replay mismatch")
        return self


class ReturnDistributionMetricsV1(MarketArtifact):
    schema_version: Literal["RETURN_DISTRIBUTION_METRICS_V1"] = "RETURN_DISTRIBUTION_METRICS_V1"
    distribution: ArtifactRefV1
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    deep_loss_threshold_fen: Money
    p_gross_payout_gt_zero: ExactProbability
    p_break_even_or_better: ExactProbability
    p_2x_budget: ExactProbability
    p_3x_budget: ExactProbability
    p_loss: ExactProbability
    p_deep_loss: ExactProbability
    expected_gross_payout_fen: FiniteDecimal
    expected_ending_capital_fen: FiniteDecimal
    expected_profit_fen: FiniteDecimal
    expected_profit_ratio: FiniteDecimal | None
    median_ending_capital_fen: Money
    expected_shortfall_to_budget_fen: FiniteDecimal

    @model_validator(mode="after")
    @fixed_decimal(PRECISION)
    def consistency(self):
        if self.cash_fen + self.stake_fen != self.budget_fen or self.deep_loss_threshold_fen != self.budget_fen // 5:
            raise ValueError("metrics cash/stake/threshold mismatch")
        if (self.p_loss + self.p_break_even_or_better != 1
                or not self.p_3x_budget <= self.p_2x_budget <= self.p_break_even_or_better
                or self.expected_ending_capital_fen != self.cash_fen + self.expected_gross_payout_fen
                or self.expected_profit_fen != self.expected_ending_capital_fen - self.budget_fen
                or self.expected_gross_payout_fen < 0 or self.expected_ending_capital_fen < 0
                or self.expected_shortfall_to_budget_fen < 0
                or self.expected_shortfall_to_budget_fen > self.budget_fen
                or self.median_ending_capital_fen < self.cash_fen
                or (self.budget_fen > 0 and self.p_deep_loss > self.p_loss)
                or (self.expected_profit_ratio is None) != (self.budget_fen == 0)):
            raise ValueError("metrics arithmetic/probability mismatch")
        if self.budget_fen and self.expected_profit_ratio != self.expected_profit_fen / Decimal(self.budget_fen):
            raise ValueError("expected profit ratio differs from fixed-context division")
        return self


class ReturnEvaluationV1(MarketArtifact):
    schema_version: Literal["RETURN_EVALUATION_V1"] = "RETURN_EVALUATION_V1"
    binding: ReturnSourceBindingV1
    policy: ReturnDistributionPolicyV1
    objective: ReturnObjectiveProfileV1
    requested: tuple[ReturnAllocationRequestV1, ...] = Field(max_length=8)
    status: Literal["AVAILABLE", "DISTRIBUTION_UNAVAILABLE"]
    reason: str | None
    distribution: PortfolioReturnDistributionV1 | None
    metrics: ReturnDistributionMetricsV1 | None
    selected: tuple[ReturnSelectedTicketV1, ...] = Field(max_length=8)
    risk: StructuralRiskV2 | None
    feasible: bool
    violations: tuple[str, ...]
    utility: FiniteDecimal | None

    @model_validator(mode="after")
    def consistent(self):
        from football_system.domain.services.return_distribution import calculate_metrics, distribution_utility
        ids = tuple(a.ticket_candidate_id for a in self.requested)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("canonical unique allocation required")
        if self.status == "AVAILABLE":
            if (self.reason is not None or self.distribution is None or self.metrics is None
                    or self.distribution.binding != self.binding or self.distribution.policy != self.policy
                    or self.distribution.allocations != self.requested
                    or self.metrics != calculate_metrics(self.distribution)
                    or self.utility != distribution_utility(self.metrics, self.objective)
                    or self.feasible != (not self.violations)):
                raise ValueError("evaluation metrics/binding/utility mismatch")
        elif (self.reason is None or self.distribution is not None or self.metrics is not None
              or self.utility is not None or self.feasible or self.selected):
            raise ValueError("unavailable evaluation cannot carry a recommendation")
        return self


class ReturnOptimizationStepV1(MultiMarketModel):
    phase: Literal["OPEN_TICKET", "INCREMENT_MULTIPLIER"]
    ticket_candidate_id: Identifier
    multiplier: int = Field(ge=1, le=50, strict=True)
    before_utility: FiniteDecimal
    after_utility: FiniteDecimal
    marginal_utility: FiniteDecimal
    evaluated_distribution_hash: Hash  # replay checksum, not a retained distribution FK


class ReturnOptimizationRunV1(MarketArtifact):
    schema_version: Literal["RETURN_OPTIMIZATION_RUN_V1"] = "RETURN_OPTIMIZATION_RUN_V1"
    binding: ReturnSourceBindingV1
    policy: ReturnDistributionPolicyV1
    objective: ReturnObjectiveProfileV1
    candidate_catalog: tuple[ArtifactRefV1, ...] = Field(max_length=4096)
    optimization_method: Literal["RETURN_DISTRIBUTION_MARGINAL_V1"] = "RETURN_DISTRIBUTION_MARGINAL_V1"
    probability_assumption: Literal["INDEPENDENT_MATCHES_V1"] = "INDEPENDENT_MATCHES_V1"
    same_match_policy: Literal["CROSS_MARKET_SAME_MATCH_UNSUPPORTED"] = "CROSS_MARKET_SAME_MATCH_UNSUPPORTED"
    optimality: Literal["DETERMINISTIC_MARGINAL_NOT_GLOBAL_OPTIMUM"] = "DETERMINISTIC_MARGINAL_NOT_GLOBAL_OPTIMUM"
    status: Literal["OPTIMIZED", "NO_BET", "OPTIMIZER_UNAVAILABLE"]
    reason: str | None
    baseline: ReturnEvaluationV1
    result: ReturnEvaluationV1 | None
    legacy: ReturnEvaluationV1 | None
    utility: FiniteDecimal | None
    legacy_v2_utility: FiniteDecimal | None
    utility_delta: FiniteDecimal | None
    steps: tuple[ReturnOptimizationStepV1, ...] = Field(max_length=400)
    rejected_proposals: dict[str, int]
    evaluated_candidate_count: int = Field(ge=0, le=4096, strict=True)
    dominated_candidate_count: int = Field(ge=0, le=4096, strict=True)
    pareto_candidate_count: int = Field(ge=0, le=4096, strict=True)
    dominance_scope: Literal["UNIQUE_EVALUATED_FEASIBLE_ALLOCATIONS_INCLUDING_NO_BET"] = "UNIQUE_EVALUATED_FEASIBLE_ALLOCATIONS_INCLUDING_NO_BET"

    @model_validator(mode="after")
    @fixed_decimal(PRECISION)
    def invariants(self):
        if (self.evaluated_candidate_count != self.dominated_candidate_count + self.pareto_candidate_count
                or tuple(r.artifact_id for r in self.candidate_catalog) != tuple(sorted({r.artifact_id for r in self.candidate_catalog}))
                or len(self.candidate_catalog) != self.binding.candidate_count or self.baseline.requested):
            raise ValueError("run catalog/baseline/dominance counts mismatch")
        if self.status == "OPTIMIZER_UNAVAILABLE":
            if self.result is not None or self.utility is not None or self.reason is None:
                raise ValueError("unavailable optimizer cannot retain a partial recommendation")
        else:
            if self.result is None or not self.result.feasible or self.utility != self.result.utility:
                raise ValueError("optimizer result must be a feasible exact evaluation")
            if (self.status == "NO_BET") != (not self.result.requested):
                raise ValueError("NO_BET must retain cash baseline")
            if self.status == "OPTIMIZED" and not self.utility > self.baseline.utility:
                raise ValueError("optimized utility must beat NO_BET")
        expected_legacy = self.legacy.utility if self.legacy is not None else None
        delta = self.utility - expected_legacy if self.utility is not None and expected_legacy is not None else None
        if self.legacy_v2_utility != expected_legacy or self.utility_delta != delta:
            raise ValueError("legacy software comparison mismatch")
        for step in self.steps:
            if step.marginal_utility != step.after_utility - step.before_utility or step.marginal_utility <= 0:
                raise ValueError("accepted steps must have strictly positive marginal utility")
        return self


RETURN_ARTIFACT_TYPES = {cls.model_fields["schema_version"].default: cls for cls in (
    ReturnDistributionPolicyV1, ReturnObjectiveProfileV1, RelevantMatchStateV1,
    TicketReturnFunctionV1, PortfolioReturnDistributionV1, ReturnDistributionMetricsV1,
    ReturnEvaluationV1, ReturnOptimizationRunV1,
)}
