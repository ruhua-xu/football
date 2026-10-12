"""Verified typed adapters around unchanged Strategy/Return/Settlement kernels."""
from dataclasses import dataclass, field

from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import revalidate
from football_system.domain.portfolio_math import (
    PortfolioAnalysisInputV1, PortfolioFusionInputV1, PortfolioStrategySourceV1, PortfolioStrategyPlanV1, PortfolioNumericalConfigurationV1,
)
from football_system.domain.real_bridge import require
from football_system.domain.services.strategy_pass_v2 import outcome_candidates, plan_values
from football_system.domain.services.return_distribution import binding_for
from football_system.domain.services.return_optimizer import optimize_prepared
from football_system.domain.services.prospective import probability_frames, settlement_values


@dataclass
class PortfolioPreparedReturnInput:
    plan: PortfolioStrategyPlanV1
    binding: object = field(init=False)
    catalog: dict = field(init=False)
    functions: dict = field(init=False)
    marginals: dict = field(init=False)
    units: dict = field(init=False)
    hints: dict = field(init=False)

    def __post_init__(self):
        require(type(self.plan) is PortfolioStrategyPlanV1, "PF_VERIFIED_PLAN_REQUIRED")
        self.plan = revalidate(self.plan)
        self.binding = binding_for(self.plan)
        self.catalog = {c.artifact_id: c for c in self.plan.candidates}
        self.functions = {}
        self.marginals = {(r.match_id, r.market_key.canonical): r for r in self.plan.source.fusion.results}
        self.units = {u.artifact_id: u for u in self.plan.source.analysis.units}
        eligible = {(s.match_id, s.market_key.canonical, s.outcome) for s in self.plan.source.selections if s.status == "ELIGIBLE"}
        roles = {}
        for request in self.plan.requests:
            signature = (request.pass_type, tuple((c.match_id, c.market_key.canonical,
                tuple(o for o in c.outcomes if (c.match_id, c.market_key.canonical, o) in eligible)) for c in request.choices))
            roles[signature] = request.role
        self.hints = {c.artifact_id: roles.get((c.pass_type, tuple((s.match_id, s.market_key.canonical,
            tuple(o.outcome for o in s.candidates)) for s in c.choice_sets))) for c in self.plan.candidates}


def calculate_portfolio(snapshots, analyses, fusions, budget, requests, policy, objective):
    require(len(snapshots) == len(analyses) == len(fusions) >= 2, "PF_COMPLETE_SOURCE_SET_REQUIRED")
    configuration = PortfolioNumericalConfigurationV1(members=tuple(a.configuration for a in analyses))
    require(all(f.policy == configuration.fusion for f in fusions),
        "PF_NUMERICAL_POLICIES_INCOMPATIBLE")
    units = tuple(sorted((u for a in analyses for u in a.units), key=lambda u: (u.identity.match_id, u.market_key.canonical)))
    results = tuple(sorted((r for f in fusions for r in f.results), key=lambda r: (r.match_id, r.market_key.canonical)))
    analysis = PortfolioAnalysisInputV1.freeze(snapshots=tuple(ArtifactRefV1.of(s) for s in snapshots), units=units, configuration=configuration)
    fusion = PortfolioFusionInputV1.freeze(analysis=ArtifactRefV1.of(analysis), source_fusions=tuple(ArtifactRefV1.of(f) for f in fusions),
        policy=configuration.fusion, results=results)
    source = PortfolioStrategySourceV1.freeze(analysis=analysis, fusion=fusion, budget_authority=ArtifactRefV1.of(budget),
        budget_fen=budget.content_payload.budget_fen, selections=outcome_candidates(analysis, fusion))
    requests = tuple(sorted((revalidate(r) for r in requests), key=canonical_json))
    plan = PortfolioStrategyPlanV1.freeze(**plan_values(source, configuration.strategy, requests))
    optimizer = optimize_prepared(PortfolioPreparedReturnInput(plan), policy, objective)
    require(optimizer.status in {"OPTIMIZED", "NO_BET"} and optimizer.result is not None and optimizer.result.feasible,
        "PF_OPTIMIZER_UNAVAILABLE")
    return plan, optimizer


@dataclass(frozen=True)
class VerifiedReviewSource:
    analysis: object
    review: object
    fusion: object


@dataclass(frozen=True)
class VerifiedReviewFrameInput:
    source: VerifiedReviewSource


def source_frames(analysis, review, fusion, audit):
    from football_system.domain.services.review_v4 import fuse_v4
    require(audit.review == ArtifactRefV1.of(review) and review.packet.analysis == ArtifactRefV1.of(analysis)
        and fusion == fuse_v4(analysis, review, fusion.policy), "PF_REVIEW_FRAME_REPLAY_MISMATCH")
    return probability_frames(VerifiedReviewFrameInput(VerifiedReviewSource(analysis, review, fusion)), audit)


@dataclass(frozen=True)
class PortfolioSettlementSourceView:
    """Kernel source is the actual Portfolio Assembly, never a forged run."""
    assembly: object
    identities: tuple
    @property
    def artifact_id(self): return self.assembly.artifact_id
    @property
    def content_hash(self): return self.assembly.content_hash
    @property
    def schema_version(self): return self.assembly.schema_version
    @property
    def data_classification(self): return "REAL_SOURCE_DATA"


@dataclass(frozen=True)
class PortfolioSettlementLockView:
    lock: object
    assembly: object
    evaluation: object
    @property
    def artifact_id(self): return self.lock.artifact_id
    @property
    def content_hash(self): return self.lock.content_hash
    @property
    def schema_version(self): return self.lock.schema_version
    @property
    def run(self): return ArtifactRefV1.of(self.assembly)
    @property
    def return_evaluation(self): return ArtifactRefV1.of(self.evaluation)
    @property
    def locked_at_utc(self): return self.lock.content_payload.locked_at_utc
    @property
    def budget_fen(self): return self.lock.content_payload.budget_fen
    @property
    def stake_fen(self): return self.lock.content_payload.stake_fen
    @property
    def cash_fen(self): return self.lock.content_payload.cash_fen


def settle_portfolio(assembly, lock, evaluation, identities, observations, at):
    source = PortfolioSettlementSourceView(assembly, identities)
    view = PortfolioSettlementLockView(lock, assembly, evaluation)
    return settlement_values(source, view, evaluation, observations, at)
