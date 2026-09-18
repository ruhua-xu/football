"""Offline daily workflow over the frozen V4 / strategy / return source graph."""

import hashlib

from football_system.application.market_v2 import FusionRequestV4, PacketRequestV4, PlanRequestV2
from football_system.application.prospective_requests import LockProspectiveRequestV1, ReviewAuditRequestV1
from football_system.application.return_distribution import OptimizeReturnRequestV1
from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id
from football_system.domain.market_v2 import content_hash, revalidate
from football_system.domain.prospective import CorrectionReasonV1
from football_system.domain.services.prospective import assert_before_kickoff, render_packet_markdown, require


class ProspectiveService:
    def __init__(self, repository, market_service, return_service, *, verify_manual):
        self.repository = repository
        self.market = market_service
        self.returns = return_service
        self.verify_manual = verify_manual

    def create_epoch(self, request):
        previous = self.repository.retry("EPOCH_CREATE", request)
        if previous is not None:
            return previous
        self.returns.repository.save(self.returns.policy)
        self.returns.repository.save(self.returns.objective)
        return self.repository.create_epoch(request)

    def import_evidence(self, request):
        previous = self.repository.retry("EVIDENCE_IMPORT", request)
        if previous is not None:
            return previous
        request = revalidate(request)
        return self.repository.import_evidence(request, self.verify_manual(request.evidence))

    def prepare(self, request):
        previous = self.repository.retry("PREPARE", request)
        if previous is not None:
            return previous
        self.market.packet(PacketRequestV4(analysis_id=request.analysis_id))
        return self.repository.prepare(request)

    def packet_files(self, run):
        require(run.packet is not None, run.reason or "PACKET_UNAVAILABLE")
        packet = self.market.repository.load(run.packet.artifact_id, "ANALYSIS_PACKET_V4")
        snapshots = {u.snapshot.artifact_id: self.repository.load(u.snapshot.artifact_id) for u in run.evidence}
        return canonical_json(packet), render_packet_markdown(run, packet, snapshots)

    def lock(self, request, *, review_bytes=None, reasons=None):
        request = revalidate(request)
        reasons = tuple(CorrectionReasonV1.model_validate(r.model_dump()) if isinstance(r, CorrectionReasonV1)
                        else CorrectionReasonV1.model_validate(r) for r in reasons) if reasons is not None else None
        if review_bytes is not None:
            require(len(review_bytes) <= 4*1024*1024, "REVIEW_TOO_LARGE")
            require(reasons is not None, "EXTERNAL_REVIEW_REQUIRES_CORRECTION_SIDECAR")
        else:
            require(reasons is None, "SIDECAR_WITHOUT_EXTERNAL_REVIEW")
        workflow_hash = content_hash("PROSPECTIVE_LOCK_WORKFLOW_V1", dict(request=request,
            raw_review_hash=hashlib.sha256(review_bytes).hexdigest() if review_bytes is not None else None, reasons=reasons))
        previous = self.repository.retry_workflow(request.request_key, workflow_hash)
        if previous is not None:
            return previous
        run = self.repository.load(request.run_id)
        epoch = self.repository.load(run.epoch.artifact_id)
        assert_before_kickoff(epoch, run, self.repository.clock.now())
        packet = self.market.repository.load(run.packet.artifact_id, "ANALYSIS_PACKET_V4")
        if review_bytes is None:
            review_bytes = canonical_json(dict(schema_version="LLM_REVIEW_V4", analysis_id=run.analysis.artifact_id,
                packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=[dict(
                    match_id=u.review_context.identity.match_id, market_key=u.review_context.market_key,
                    review_context_id=u.review_context_id, review_context_hash=u.review_context_hash,
                    status="UNAVAILABLE", failure_code="MODEL_UNAVAILABLE" if u.review_context.quant_status == "MODEL_UNAVAILABLE" else "SKIPPED_DISABLED",
                    limitations=["Optional external review not supplied"])
                    for u in packet.market_units])).encode()
            reasons = tuple(CorrectionReasonV1(match_id=u.review_context.identity.match_id,
                market_key=u.review_context.market_key, review_context_id=u.review_context_id,
                categories=("DATA_QUALITY_DOWNGRADE",), assertion_class="ANALYSIS",
                rationale="Optional external review not supplied; frozen V4 abstention semantics apply.") for u in packet.market_units)
        review = self.market.review_import(canonical_json(packet).encode(), review_bytes)
        audit = self.repository.import_review_audit(ReviewAuditRequestV1(
            request_key=stable_id("prospective-review", request.request_key, workflow_hash), run_id=run.artifact_id,
            review_id=review.artifact_id, reasons=reasons))
        fusion = self.market.fusion(FusionRequestV4(analysis_id=run.analysis.artifact_id, review_id=review.artifact_id,
                                                  policy=epoch.configuration.fusion_policy))
        plan = self.market.plan(PlanRequestV2(fusion_id=fusion.artifact_id, budget_fen=run.budget_fen,
            profile=epoch.configuration.strategy_profile, requests=request.strategy_requests))
        optimizer = self.returns.optimize(OptimizeReturnRequestV1(plan_id=plan.artifact_id,
            expected_policy_hash=epoch.configuration.return_policy.content_hash,
            expected_objective_hash=epoch.configuration.objective_profile.content_hash))
        return self.repository.lock(LockProspectiveRequestV1(request_key=request.request_key, run_id=run.artifact_id,
            correction_audit_id=audit.artifact_id, optimizer_run_id=optimizer.artifact_id,
            invalidation_reason=request.invalidation_reason, workflow_hash=workflow_hash))

    def import_result(self, request):
        previous = self.repository.retry("RESULT_IMPORT", request)
        if previous is not None:
            return previous
        request = revalidate(request)
        return self.repository.import_result(request, self.verify_manual(request.result))

    def settle(self, request):
        return self.repository.settle(request)

    def report(self, request):
        return self.repository.report(request)

    def close_epoch(self, request):
        return self.repository.close_epoch(request)
