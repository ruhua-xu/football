"""SYNTHETIC_CONTRACT_TEST_ONLY, no public approval bypass or real web review.

The isolated inference fixture persists actual admitted facts and normalized
state. It explicitly seeds a test-only approval and uses a labeled pilot double.
No review or source approval artifact is published outside pytest temp storage.
"""

import asyncio
import hashlib
import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError

from football_system.application import post_review, review_bridge
from football_system.application.post_review import (
    CreateFusionRunService,
    CreatePortfolioRevisionService,
)
from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
    build_analysis_packet_v3,
    canonical_json,
    validate_review_files,
)
from football_system.domain.production_release import ApprovedTrainingHistoryAuditV1
from football_system.infrastructure.database.models import Base
from football_system.infrastructure.database.post_review_repositories import (
    SqlAlchemyPostReviewRepository,
)
from football_system.infrastructure.database.production_audit_repository import (
    SqlAlchemyProductionAuditRepository,
)
from football_system.infrastructure.database.production_audit_schema import (
    PRODUCTION_AUDIT_TABLES,
    production_audit_trigger_sql_v1,
)
from football_system.infrastructure.database.review_repositories import (
    SqlAlchemyReviewArtifactRepository,
)
from football_system.infrastructure.database.session import create_database_engine
from football_system.infrastructure.files.production_audit_bundle import (
    export_production_audit_bundle,
    import_production_audit_bundle,
    read_production_audit_bundle,
    validate_production_audit_pair,
)
from tests.integration import test_production_inference as fixtures
from tests.integration import test_production_quant_persistence as production

lane = fixtures.lane
inference = fixtures.inference


@pytest.fixture
def audited(inference, monkeypatch):
    inference.lane.clock.value = fixtures.CREATED
    artifacts = asyncio.run(fixtures.service(inference).run(inference.request))
    auditor = SqlAlchemyProductionAuditRepository(
        inference.lane.sessions,
        production_repository=inference.production.repo,
        inference_repository=inference.repository,
        clock=inference.lane.clock,
    )
    review = SqlAlchemyReviewArtifactRepository(
        inference.lane.sessions, audit_repository=auditor
    )
    post = SqlAlchemyPostReviewRepository(
        inference.lane.sessions, audit_repository=auditor
    )
    monkeypatch.setattr(review_bridge, "utc_now", inference.lane.clock)
    monkeypatch.setattr(post_review, "utc_now", inference.lane.clock)
    return SimpleNamespace(
        inference=inference,
        lane=inference.lane,
        auditor=auditor,
        review=review,
        post=post,
        artifacts=artifacts,
        run_id=artifacts.analysis_run.analysis_run_id,
    )


def export(context):
    return ExportAnalysisPacketService(context.review).export(
        context.run_id, "ANALYSIS_PACKET_V3"
    )


def review_bytes(packet):
    return canonical_json(
        {
            "schema_version": "LLM_REVIEW_V3",
            "analysis_run_id": packet.analysis_run.analysis_run_id,
            "packet_id": packet.packet_id,
            "packet_hash": packet.packet_hash,
            "match_reviews": [
                dict(
                    status="UNAVAILABLE",
                    match_id=match.match_id,
                    market_key=match.market_key,
                    review_context_id=match.review_context_id,
                    review_context_hash=match.review_context_hash,
                    failure_code="MODEL_UNAVAILABLE",
                    limitations=[
                        "SYNTHETIC_CONTRACT_TEST_ONLY: no web review performed"
                    ],
                )
                for match in packet.matches
            ],
        }
    ).encode("utf-8")


def downstream(context):
    packet, packet_json = export(context)
    artifact = ImportLLMReviewService(context.review).import_review(
        packet_json.encode(), review_bytes(packet)
    )
    fusion = CreateFusionRunService(context.post, fixtures.SETTINGS).create(
        artifact.review_artifact_id
    )
    revision = CreatePortfolioRevisionService(context.post, fixtures.SETTINGS).create(
        fusion.fusion_run_id
    )
    return packet, packet_json, artifact, fusion, revision


def counts(context):
    with context.lane.engine.connect() as connection:
        return {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM {name}"))
            for name in (
                "analysis_packets",
                *PRODUCTION_AUDIT_TABLES,
                "llm_review_artifacts",
                "fusion_runs",
                "portfolio_revisions",
            )
        }


def test_atomic_packet_sidecar_exact_retry_and_full_downstream(audited, tmp_path):
    packet, packet_json, artifact, fusion, revision = downstream(audited)
    source = audited.review.load_packet_source_v3(audited.run_id)
    assert (
        canonical_json(
            build_analysis_packet_v3(source, packet.generated_at_utc).model_dump(
                mode="json"
            )
        )
        == packet_json
    )
    audit = audited.auditor.gate_run(audited.run_id)
    assert audit.content_payload.generated_at_utc == packet.generated_at_utc
    assert (
        audit.content_payload.quant_model_state_id
        == audited.artifacts.quant_model_states[0].quant_model_state_id
    )
    before, clock_calls = counts(audited), len(audited.lane.clock.calls)
    assert export(audited) == (packet, packet_json)
    assert audited.auditor.load_audit(packet.packet_id) == audit
    assert audited.post.save_fusion_run(fusion) == fusion
    assert audited.post.save_portfolio_revision(revision) == revision
    assert counts(audited) == before
    assert len(audited.lane.clock.calls) > clock_calls
    path = export_production_audit_bundle(
        tmp_path / "export",
        analysis_run_id=audited.run_id,
        review_repository=audited.review,
        audit_repository=audited.auditor,
    )
    assert read_production_audit_bundle(path).audit == audit
    assert (
        import_production_audit_bundle(
            path,
            review_bytes(packet),
            review_repository=audited.review,
            audit_repository=audited.auditor,
        )
        == artifact
    )
    assert counts(audited) == before


@pytest.mark.parametrize(
    "dependency", [None, SimpleNamespace(gate_run=lambda run: None)]
)
def test_missing_or_noop_auditor_never_bypasses_direct_and_cached_paths(
    audited, dependency
):
    packet, packet_json, artifact, fusion, revision = downstream(audited)
    review = SqlAlchemyReviewArtifactRepository(
        audited.lane.sessions, audit_repository=dependency
    )
    post = SqlAlchemyPostReviewRepository(
        audited.lane.sessions, audit_repository=dependency
    )
    operations = (
        lambda: ExportAnalysisPacketService(review).export(
            audited.run_id, "ANALYSIS_PACKET_V3"
        ),
        lambda: review.load_packet_source_v3(audited.run_id),
        lambda: review.load_analysis_packet(packet.packet_id),
        lambda: review.save_analysis_packet(packet, packet_json),
        lambda: review.save_analysis_packet(
            packet.model_copy(
                update={
                    "analysis_run": packet.analysis_run.model_copy(
                        update={"analysis_run_id": "unapproved-caller-supplied-run"}
                    )
                }
            ),
            packet_json,
        ),
        lambda: review.save_llm_review(artifact),
        lambda: ImportLLMReviewService(review).import_review(
            packet_json.encode(), review_bytes(packet)
        ),
        lambda: post.load_fusion_source(artifact.review_artifact_id),
        lambda: post.find_fusion_run(fusion.fusion_run_id),
        lambda: post.save_fusion_run(fusion),
        lambda: post.find_portfolio_revision(revision.portfolio_revision_id),
        lambda: post.save_portfolio_revision(revision),
        lambda: CreateFusionRunService(post, fixtures.SETTINGS).create(
            artifact.review_artifact_id
        ),
        lambda: CreatePortfolioRevisionService(post, fixtures.SETTINGS).create(
            fusion.fusion_run_id
        ),
    )
    before = counts(audited)
    for operation in operations:
        with pytest.raises(ValueError, match="concrete production audit"):
            operation()
    assert counts(audited) == before


@pytest.mark.parametrize(
    "failure", ["sidecar", "facts", "state", "binding", "resealed-sidecar"]
)
def test_tampered_persisted_state_or_sidecar_fails_closed(audited, failure):
    packet, packet_json, artifact, fusion, revision = downstream(audited)
    audit = audited.auditor.load_audit(packet.packet_id)
    # Trusted test DDL simulates corruption; not an exposed application operation.
    with audited.lane.engine.begin() as connection:
        for name in tuple(
            connection.scalars(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        ):
            connection.execute(text(f"DROP TRIGGER {name}"))
        if failure == "sidecar":
            connection.execute(text("DELETE FROM production_audit_packet_requirements"))
            connection.execute(text("DELETE FROM production_audit_bundles"))
        elif failure == "facts":
            connection.execute(
                text(
                    "UPDATE quant_model_training_facts SET fact_hash = :hash WHERE fact_sequence = 0"
                ),
                {"hash": "0" * 64},
            )
        elif failure == "state":
            connection.execute(text("UPDATE quant_model_states SET state_json='{}'"))
        elif failure == "binding":
            connection.execute(text("DELETE FROM analysis_run_target_acceptance_plans"))
        else:
            changed = ApprovedTrainingHistoryAuditV1.freeze(
                content_payload=audit.content_payload.model_copy(
                    update={"approved_facts_hash": "0" * 64}
                )
            )
            # The public byte boundary accepts a correctly rehashed sidecar; the
            # local repository must still reject its false release provenance.
            validate_production_audit_pair(
                packet_json.encode(), canonical_json(changed).encode()
            )
            table = Base.metadata.tables["production_audit_bundles"]
            connection.execute(
                table.update().values(
                    audit_json=canonical_json(changed),
                    audit_id=changed.artifact_id,
                    audit_hash=changed.content_hash,
                )
            )
    for operation in (
        lambda: export(audited),
        lambda: audited.review.save_llm_review(artifact),
        lambda: audited.post.save_fusion_run(fusion),
        lambda: audited.post.save_portfolio_revision(revision),
        lambda: audited.auditor.gate_run(audited.run_id),
    ):
        with pytest.raises(ValueError):
            operation()


def test_cached_packet_expiry_and_standalone_historical_validation(audited):
    packet, packet_json = export(audited)
    raw = review_bytes(packet)
    audited.lane.clock.value = production.END
    assert validate_review_files(packet_json.encode(), raw)[0] == packet
    before = counts(audited)
    for operation in (
        lambda: export(audited),
        lambda: audited.review.find_analysis_packet(
            audited.run_id, "ANALYSIS_PACKET_V3"
        ),
        lambda: audited.review.save_analysis_packet(packet, packet_json),
        lambda: ImportLLMReviewService(audited.review).import_review(
            packet_json.encode(), raw
        ),
    ):
        with pytest.raises(ValueError):
            operation()
    assert counts(audited) == before


def test_missing_auditor_rejects_first_export_without_new_artifacts(audited):
    repository = SqlAlchemyReviewArtifactRepository(audited.lane.sessions)
    with pytest.raises(ValueError, match="concrete production audit"):
        ExportAnalysisPacketService(repository).export(
            audited.run_id, "ANALYSIS_PACKET_V3"
        )
    assert not any(counts(audited).values())


def test_state_binding_detects_approved_run_even_if_marker_is_removed(audited):
    export(audited)
    with audited.lane.engine.begin() as connection:
        for name in tuple(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='analysis_runs'"
                )
            )
        ):
            connection.execute(text(f"DROP TRIGGER {name}"))
        connection.execute(text("UPDATE analysis_runs SET config_json='{}'"))
    with pytest.raises(ValueError, match="concrete production audit"):
        SqlAlchemyReviewArtifactRepository(audited.lane.sessions).find_analysis_packet(
            audited.run_id, "ANALYSIS_PACKET_V3"
        )
    with pytest.raises(ValueError):
        export(audited)


def test_noop_public_methods_cannot_replace_repository_boundary_checks(
    audited, monkeypatch
):
    export(audited)
    production.revoke(
        audited.inference.production,
        audited.inference.release.training_approval,
        release=audited.inference.release,
    )
    for method in (
        "gate_run",
        "begin_in_session",
        "finish_in_session",
        "revalidate_in_session",
    ):
        monkeypatch.setattr(audited.auditor, method, lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="revoked"):
        export(audited)


def test_actual_expiry_during_bundle_staging_leaves_no_published_directory(
    audited, tmp_path, monkeypatch
):
    from football_system.infrastructure.files import production_audit_bundle as files

    packet, _ = export(audited)
    original_audit = audited.auditor.load_audit(packet.packet_id)
    before = counts(audited)
    original_read = files.read_production_audit_bundle

    def expire_after_staging(path):
        bundle = original_read(path)
        audited.lane.clock.value = production.END
        return bundle

    monkeypatch.setattr(files, "read_production_audit_bundle", expire_after_staging)
    target = tmp_path / "never-published"
    with pytest.raises(ValueError):
        export_production_audit_bundle(
            target,
            analysis_run_id=audited.run_id,
            review_repository=audited.review,
            audit_repository=audited.auditor,
        )
    assert not target.exists()
    assert tuple(tmp_path.glob("*.staging")) == ()
    assert counts(audited) == before
    with audited.lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT audit_id FROM production_audit_bundles"))
            == original_audit.artifact_id
        )


@pytest.mark.parametrize("stage", ["packet", "import", "fusion", "revision"])
def test_revocation_effective_at_final_boundary_rolls_back_new_artifacts(
    audited, monkeypatch, stage
):
    packet = packet_json = artifact = fusion = None
    if stage != "packet":
        packet, packet_json = export(audited)
    if stage in {"fusion", "revision"}:
        artifact = ImportLLMReviewService(audited.review).import_review(
            packet_json.encode(), review_bytes(packet)
        )
    if stage == "revision":
        fusion = CreateFusionRunService(audited.post, fixtures.SETTINGS).create(
            artifact.review_artifact_id
        )
    effective = audited.lane.clock.value + timedelta(hours=1)
    production.revoke(
        audited.inference.production,
        audited.inference.release.training_approval,
        release=audited.inference.release,
        effective_at=effective,
    )
    before = counts(audited)
    original = audited.auditor._validate_sidecar

    def late(session, operation, context, completion):
        value = original(session, operation, context, completion)
        table = {
            "packet": "production_audit_bundles",
            "import": "llm_review_artifacts",
            "fusion": "fusion_runs",
            "revision": "portfolio_revisions",
        }[stage]
        if session.scalar(text(f"SELECT COUNT(*) FROM {table}")) > before[table]:
            audited.lane.clock.value = effective
        return value

    monkeypatch.setattr(audited.auditor, "_validate_sidecar", late)
    operation = {
        "packet": lambda: export(audited),
        "import": lambda: ImportLLMReviewService(audited.review).import_review(
            packet_json.encode(), review_bytes(packet)
        ),
        "fusion": lambda: CreateFusionRunService(
            audited.post, fixtures.SETTINGS
        ).create(artifact.review_artifact_id),
        "revision": lambda: CreatePortfolioRevisionService(
            audited.post, fixtures.SETTINGS
        ).create(fusion.fusion_run_id),
    }[stage]
    with pytest.raises(ValueError, match="revoked"):
        operation()
    assert counts(audited) == before


@pytest.mark.parametrize(
    "stage,filtered_recording,revoked_during_io",
    [
        ("packet", False, True),
        ("packet_cached", False, True),
        ("import", False, True),
        ("import_cached", False, True),
        ("fusion", False, True),
        ("fusion_cached", False, True),
        ("revision", False, True),
        ("revision_cached", False, True),
        ("fusion", True, True),
        ("fusion", False, False),
    ],
)
def test_actual_time_gate_follows_last_authorization_io(
    audited, monkeypatch, stage, filtered_recording, revoked_during_io
):
    packet = packet_json = artifact = fusion = None
    if stage != "packet":
        packet, packet_json = export(audited)
    if stage not in {"packet", "packet_cached", "import"}:
        artifact = ImportLLMReviewService(audited.review).import_review(
            packet_json.encode(), review_bytes(packet)
        )
    if stage in {"fusion_cached", "revision", "revision_cached"}:
        fusion = CreateFusionRunService(audited.post, fixtures.SETTINGS).create(
            artifact.review_artifact_id
        )
    if stage == "revision_cached":
        CreatePortfolioRevisionService(audited.post, fixtures.SETTINGS).create(
            fusion.fusion_run_id
        )
    effective = audited.lane.clock.value + timedelta(hours=1)
    revocation, _, _ = production.revoke(
        audited.inference.production,
        audited.inference.release.training_approval,
        release=audited.inference.release,
        effective_at=effective,
    )
    before = counts(audited)
    original_finish = SqlAlchemyProductionAuditRepository.finish_in_session
    original_sidecar = audited.auditor._validate_sidecar
    original_authorization = audited.inference.production.repo.authorization_in_session
    original_evidence_read = (
        audited.inference.production.repo.admission_repository.evidence.read
    )
    observed = dict(finishing=False, armed=False, jumped=False, sampled=False)
    # A separately observed audit clock also exercises rows recorded after the
    # reader's supplied time. Their durable presence must not be filtered away.
    audit_time = SimpleNamespace(
        value=revocation.content_payload.recorded_at_utc - timedelta(seconds=1)
    )

    def clock():
        at = audit_time.value if filtered_recording else audited.lane.clock()
        if observed["jumped"]:
            observed["sampled"] = True
            assert (at >= effective) == revoked_during_io
        return at

    def finish(repository, session, operation):
        observed["finishing"] = True
        try:
            return original_finish(repository, session, operation)
        finally:
            observed["finishing"] = False

    def sidecar(*args):
        value = original_sidecar(*args)
        if observed["finishing"]:
            observed["armed"] = True
        return value

    def authorization(*args, **kwargs):
        assert not observed["sampled"], "authorization I/O after final clock sample"
        current = original_authorization(*args, **kwargs)
        if observed["armed"]:
            assert current.actual_at_utc < effective
            assert (revocation in current.revocations) == (not filtered_recording)
            observed["jumped"] = True
            advanced = effective + timedelta(seconds=1 if revoked_during_io else -3)
            audited.lane.clock.value = advanced
            audit_time.value = advanced
        return current

    def no_sql_after_sample(*args):
        assert not observed["sampled"], "SQL after final clock sample"

    def evidence_read(*args, **kwargs):
        assert not observed["sampled"], "evidence I/O after final clock sample"
        return original_evidence_read(*args, **kwargs)

    monkeypatch.setattr(audited.auditor, "_clock", clock)
    monkeypatch.setattr(
        SqlAlchemyProductionAuditRepository, "finish_in_session", finish
    )
    monkeypatch.setattr(audited.auditor, "_validate_sidecar", sidecar)
    monkeypatch.setattr(
        audited.inference.production.repo, "authorization_in_session", authorization
    )
    monkeypatch.setattr(
        audited.inference.production.repo.admission_repository.evidence,
        "read",
        evidence_read,
    )
    operation = {
        "packet": lambda: export(audited),
        "import": lambda: ImportLLMReviewService(audited.review).import_review(
            packet_json.encode(), review_bytes(packet)
        ),
        "fusion": lambda: CreateFusionRunService(
            audited.post, fixtures.SETTINGS
        ).create(artifact.review_artifact_id),
        "revision": lambda: CreatePortfolioRevisionService(
            audited.post, fixtures.SETTINGS
        ).create(fusion.fusion_run_id),
    }[stage.removesuffix("_cached")]
    event.listen(audited.lane.engine, "before_cursor_execute", no_sql_after_sample)
    try:
        if revoked_during_io:
            with pytest.raises(ValueError, match="revoked"):
                operation()
        else:
            operation()
            before["fusion_runs"] += 1
    finally:
        event.remove(audited.lane.engine, "before_cursor_execute", no_sql_after_sample)
    assert observed["jumped"] and observed["sampled"]
    assert counts(audited) == before


def _strip_production_metadata(context):
    """Trusted DDL models a historical corrupt run, never an approval workflow."""
    removable = (
        "production_audit_packet_requirements",
        "production_audit_bundles",
        "analysis_run_target_acceptance_plans",
        "quant_model_state_production_releases",
    )
    with context.lane.engine.begin() as connection:
        for name, table in connection.execute(
            text("SELECT name, tbl_name FROM sqlite_master WHERE type='trigger'")
        ).all():
            if table in (*removable, "analysis_runs"):
                connection.execute(text(f"DROP TRIGGER {name}"))
        for table in removable:
            connection.execute(text(f"DELETE FROM {table}"))
        payload = json.loads(context.artifacts.analysis_run.config_json)
        for name in (
            "model_training_use_class",
            "model_training_source_mode",
            "production_model_release_id",
            "production_target_acceptance_plan_id",
        ):
            payload["request"].pop(name, None)
        config = canonical_json(payload)
        connection.execute(
            text("UPDATE analysis_runs SET config_json=:config, config_hash=:hash"),
            {"config": config, "hash": hashlib.sha256(config.encode()).hexdigest()},
        )
        assert (
            connection.scalar(
                text("""
            SELECT COUNT(*) FROM quant_model_training_facts f
            JOIN quant_model_states s ON s.quant_model_state_id=f.quant_model_state_id
            JOIN match_result_admissions a ON a.match_result_id=f.match_result_id
            WHERE s.analysis_run_id=:run
        """),
                {"run": context.run_id},
            )
            > 0
        )
        assert all(
            connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0
            for table in removable
        )


def test_stripped_admitted_graph_requires_auditor_on_cached_reads_and_saves(audited):
    packet, packet_json, artifact, fusion, revision = downstream(audited)
    _strip_production_metadata(audited)
    review = SqlAlchemyReviewArtifactRepository(audited.lane.sessions)
    post = SqlAlchemyPostReviewRepository(audited.lane.sessions)
    before = counts(audited)
    for operation in (
        lambda: ExportAnalysisPacketService(review).export(
            audited.run_id, "ANALYSIS_PACKET_V3"
        ),
        lambda: review.find_analysis_packet(audited.run_id, "ANALYSIS_PACKET_V3"),
        lambda: review.load_packet_source_v3(audited.run_id),
        lambda: review.load_analysis_packet(packet.packet_id),
        lambda: review.save_analysis_packet(packet, packet_json),
        lambda: review.save_llm_review(artifact),
        lambda: ImportLLMReviewService(review).import_review(
            packet_json.encode(), review_bytes(packet)
        ),
        lambda: post.load_fusion_source(artifact.review_artifact_id),
        lambda: post.find_fusion_run(fusion.fusion_run_id),
        lambda: post.save_fusion_run(fusion),
        lambda: post.find_portfolio_revision(revision.portfolio_revision_id),
        lambda: post.save_portfolio_revision(revision),
        lambda: CreateFusionRunService(post, fixtures.SETTINGS).create(
            artifact.review_artifact_id
        ),
        lambda: CreatePortfolioRevisionService(post, fixtures.SETTINGS).create(
            fusion.fusion_run_id
        ),
    ):
        with pytest.raises(ValueError, match="concrete production audit"):
            operation()
    with pytest.raises(ValueError, match="exact inference binding"):
        export(audited)
    assert counts(audited) == before


def test_stripped_admitted_graph_first_packet_has_deferred_audit_obligation(audited):
    source = audited.review.load_packet_source_v3(audited.run_id)
    packet = build_analysis_packet_v3(source, audited.lane.clock())
    packet_json = canonical_json(packet.model_dump(mode="json"))
    _strip_production_metadata(audited)
    repository = SqlAlchemyReviewArtifactRepository(audited.lane.sessions)
    with pytest.raises(ValueError, match="concrete production audit"):
        repository.save_analysis_packet(packet, packet_json)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with audited.lane.engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["analysis_packets"]
                .insert()
                .values(
                    packet_id=packet.packet_id,
                    parent_analysis_run_id=audited.run_id,
                    schema_version=packet.schema_version,
                    generated_at_utc=packet.generated_at_utc,
                    packet_json=packet_json,
                    packet_hash=packet.packet_hash,
                )
            )
    assert not any(counts(audited).values())


def test_direct_sql_packet_cannot_commit_without_atomic_sidecar(audited):
    source = audited.review.load_packet_source_v3(audited.run_id)
    packet = build_analysis_packet_v3(source, audited.lane.clock())
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with audited.lane.engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["analysis_packets"]
                .insert()
                .values(
                    packet_id=packet.packet_id,
                    parent_analysis_run_id=audited.run_id,
                    schema_version=packet.schema_version,
                    generated_at_utc=packet.generated_at_utc,
                    packet_json=canonical_json(packet.model_dump(mode="json")),
                    packet_hash=packet.packet_hash,
                )
            )
    assert not any(counts(audited).values())


def test_same_session_audit_token_cannot_cross_transactions(audited):
    export(audited)
    with audited.lane.sessions() as session:
        with session.begin():
            session.execute(text("BEGIN IMMEDIATE"))
            operation = audited.auditor.begin_in_session(session, audited.run_id)
        with session.begin():
            session.execute(text("BEGIN IMMEDIATE"))
            with pytest.raises(ValueError, match="another transaction"):
                audited.auditor.finish_in_session(session, operation)


def test_audit_immutability_and_nonempty_downgrade(audited):
    export(audited)
    for sql in (
        "DELETE FROM production_audit_bundles",
        "UPDATE production_audit_bundles SET audit_json='{}'",
        "INSERT OR REPLACE INTO production_audit_bundles SELECT * FROM production_audit_bundles",
        "DELETE FROM production_audit_packet_requirements",
    ):
        with pytest.raises(IntegrityError, match="append-only|immutable|projection"):
            with audited.lane.engine.begin() as connection:
                connection.execute(text(sql))
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(audited.lane.engine.url))
    command.stamp(config, "c2ebf618d354")
    with pytest.raises(RuntimeError, match="immutable production audit"):
        command.downgrade(config, "b1dae507c243")


def test_audit_migration_runtime_offline_and_empty_downgrade(tmp_path):
    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / 'audit-migration.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "c2ebf618d354")
    engine = create_database_engine(url)
    assert set(PRODUCTION_AUDIT_TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "c2ebf618d354"
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert set(production_audit_trigger_sql_v1()) <= set(
            connection.scalars(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        )
    engine.dispose()
    command.downgrade(config, "b1dae507c243")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "b1dae507c243:c2ebf618d354", sql=True)
    assert "DEFERRABLE INITIALLY DEFERRED" in output.getvalue()
    with pytest.raises(RuntimeError, match="offline"):
        command.downgrade(config, "c2ebf618d354:b1dae507c243", sql=True)
