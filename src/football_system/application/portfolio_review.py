"""Persist a normal verified V4 review/audit/fusion without locking or budgeting."""
from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.prospective import CorrectionReasonV1
from football_system.domain.real_bridge import RealReviewAuditV1, require
from football_system.domain.services.real_bridge import real_packet
from football_system.domain.services.review_v4 import import_v4_bytes, fuse_v4
from football_system.domain.services.prospective import correction_values


def import_source_review(repository, session, request, at):
    run = repository._get(session, request.run_id, "REAL_PROSPECTIVE_RUN_V1")
    repository._head_free(session, run)
    repository._epoch(session, run.epoch.artifact_id, at)
    repository._kickoff(session, repository._ref(session, run.bucket, "REAL_KICKOFF_BUCKET_V1"), at)
    anchor = repository._ref(session, run.anchor, "REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
    analysis = repository._ref(session, run.analysis, "REAL_MULTI_MARKET_ANALYSIS_V1")
    require(analysis.status == "READY", "BUCKET_UNAVAILABLE")
    repository._sources_current(session, analysis, anchor, at)
    packet = real_packet(analysis)
    require(ArtifactRefV1.of(packet) == run.packet, "PF_ORIGINAL_PACKET_REQUIRED")
    if request.raw_review is None:
        require(request.reasons is None, "REASONS_WITHOUT_REVIEW")
        raw = canonical_json(dict(schema_version="LLM_REVIEW_V4", analysis_id=analysis.artifact_id,
            packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=[dict(
                match_id=u.review_context.identity.match_id, market_key=u.review_context.market_key,
                review_context_id=u.review_context_id, review_context_hash=u.review_context_hash,
                status="UNAVAILABLE", failure_code="SKIPPED_DISABLED", limitations=["Optional web review not supplied"])
                for u in packet.market_units]))
        reasons = tuple(CorrectionReasonV1(match_id=u.review_context.identity.match_id, market_key=u.review_context.market_key,
            review_context_id=u.review_context_id, categories=("DATA_QUALITY_DOWNGRADE",), assertion_class="ANALYSIS",
            rationale="Explicit optional-review abstention") for u in packet.market_units)
    else:
        require(request.reasons is not None, "COMPLETE_REVIEW_SIDECAR_REQUIRED")
        raw, reasons = request.raw_review, request.reasons
    review = import_v4_bytes(canonical_json(packet).encode(), raw.encode())
    snapshots = {u.snapshot.artifact_id: repository.pv._load(session, u.snapshot.artifact_id) for u in run.evidence}
    checked = correction_values(run, review, reasons, snapshots, at, run.clock_basis)
    audit = RealReviewAuditV1.freeze(**repository._context(session, run, at), run=checked["run"], review=checked["review"], reasons=checked["reasons"])
    fusion = fuse_v4(analysis, review, anchor.policy.configuration.fusion)
    return audit, (review, fusion), ()
