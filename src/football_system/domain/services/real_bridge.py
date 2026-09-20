"""Validated new real types around unchanged V4/Strategy/Return pure kernels."""

from dataclasses import dataclass, field

from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import content_hash, revalidate
from football_system.domain.prospective import CorrectionReasonV1
from football_system.domain.real_bridge import (
    RealAnalysisUnitV1, RealMultiMarketAnalysisV1, RealReviewAuditV1, RealStrategySourceV1, RealStrategyPlanV1,
    RealCalculationBindingV1, DecisionLockV2, require,
)
from football_system.domain.return_distribution import ReturnSourceBindingV1
from football_system.domain.services.prospective import correction_values, probability_frames
from football_system.domain.services.review_v4 import export_packet_v4, import_v4_bytes, fuse_v4
from football_system.domain.services.strategy_pass_v2 import outcome_candidates, plan_values
from football_system.domain.services.return_distribution import binding_for
from football_system.domain.services.return_optimizer import optimize_prepared


def context(value, at, *, sequence):
    return dict(provenance=value.provenance, event_at_utc=at, clock_basis=value.clock_basis,receipt_sequence=sequence)


@dataclass
class RealPreparedReturnInput:
    """Own typed validated input, never cast to the old StrategyPassPlanV2."""
    plan: RealStrategyPlanV1
    binding: ReturnSourceBindingV1 = field(init=False)
    catalog: dict = field(init=False)
    functions: dict = field(init=False)
    marginals: dict = field(init=False)
    units: dict = field(init=False)
    hints: dict = field(init=False)

    def __post_init__(self):
        require(type(self.plan) is RealStrategyPlanV1, "REAL_PLAN_REQUIRED")
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


def real_packet(analysis):
    require(type(analysis) is RealMultiMarketAnalysisV1 and all(type(u) is RealAnalysisUnitV1 for u in analysis.units), "REAL_ANALYSIS_REQUIRED")
    return export_packet_v4(revalidate(analysis))


def calculate(run, analysis, anchor, request, snapshots, at, return_policy, objective, *, sequence):
    require(type(analysis) is RealMultiMarketAnalysisV1 and analysis.status == "READY", "BUCKET_UNAVAILABLE")
    packet = real_packet(analysis)
    if request.raw_review is None:
        require(request.reasons is None, "REASONS_WITHOUT_REVIEW")
        raw = canonical_json(dict(schema_version="LLM_REVIEW_V4", analysis_id=analysis.artifact_id,
            packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=[dict(
                match_id=u.review_context.identity.match_id, market_key=u.review_context.market_key,
                review_context_id=u.review_context_id, review_context_hash=u.review_context_hash,
                status="UNAVAILABLE", failure_code="SKIPPED_DISABLED", limitations=["Optional web review not supplied"])
                for u in packet.market_units]))
        reasons = tuple(CorrectionReasonV1(match_id=u.review_context.identity.match_id,
            market_key=u.review_context.market_key, review_context_id=u.review_context_id,
            categories=("DATA_QUALITY_DOWNGRADE",), assertion_class="ANALYSIS", rationale="Explicit optional-review abstention") for u in packet.market_units)
    else:
        require(request.reasons is not None, "COMPLETE_REVIEW_SIDECAR_REQUIRED")
        raw, reasons = request.raw_review, request.reasons
    review = import_v4_bytes(canonical_json(packet).encode(), raw.encode())
    # The frozen validator is a value operation, not an old persistence or run factory.
    checked = correction_values(run, review, reasons, snapshots, at, run.clock_basis)
    audit = RealReviewAuditV1.freeze(**context(run, at,sequence=sequence), run=checked["run"], review=checked["review"], reasons=checked["reasons"])
    fusion = fuse_v4(analysis, review, anchor.policy.configuration.fusion)
    source = RealStrategySourceV1.freeze(**context(run, at,sequence=sequence), analysis=analysis, review=review, fusion=fusion,
        budget_fen=run.budget_fen, selections=outcome_candidates(analysis, fusion))
    requests = tuple(sorted((revalidate(r) for r in request.strategy_requests), key=canonical_json))
    plan = RealStrategyPlanV1.freeze(**context(run, at,sequence=sequence), **plan_values(source, anchor.policy.configuration.strategy, requests))
    optimizer = optimize_prepared(RealPreparedReturnInput(plan), return_policy, objective)
    calculation = RealCalculationBindingV1.freeze(**context(run, at,sequence=sequence), run=ArtifactRefV1.of(run), plan=ArtifactRefV1.of(plan),
        optimizer=optimizer, review_audit=audit)
    return plan, calculation


def decision_values(run, analysis, anchor, plan, calculation, at, revalidation_hash):
    require(calculation.optimizer.result is not None and calculation.optimizer.result.feasible, "OPTIMIZER_UNAVAILABLE")
    # probability_frames only reads the already verified reasons and source layers.
    frames = probability_frames(plan, calculation.review_audit)
    evaluation = calculation.optimizer.result
    values = dict(**context(run, at,sequence=calculation.receipt_sequence), program=run.program, run=ArtifactRefV1.of(run), epoch=run.epoch, anchor=run.anchor,
        bucket=run.bucket, analysis=run.analysis, packet=run.packet, review=ArtifactRefV1.of(plan.source.review),
        calculation=ArtifactRefV1.of(calculation), strategy_plan=ArtifactRefV1.of(plan), return_evaluation=ArtifactRefV1.of(evaluation),
        locked_at_utc=at, earliest_kickoff_at_utc=min(i.kickoff_at_utc for i in run.identities), frames=frames,
        selected=evaluation.selected, budget_fen=run.budget_fen, stake_fen=evaluation.distribution.stake_fen,
        cash_fen=evaluation.distribution.cash_fen, configuration_hash=run.configuration_hash,
        implementation_hash=anchor.implementation.bridge_hash, revalidation_hash=revalidation_hash)
    values["full_dependency_hash"] = content_hash("REAL_LOCK_DEPENDENCIES_V1", values)
    return DecisionLockV2.freeze(**values)
