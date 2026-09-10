"""Synthetic public-writer regressions for planning and versioned SQL boundaries."""

from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.domain.quant_integrity import (
    AdmittedFactRefV1,
    IntegrityArtifactRefV1,
    QuantIntegrityPlanDefinitionV1,
    QuantIntegritySliceV1,
    QuantIntegrityTargetV1,
)
from football_system.infrastructure.database import quant_integrity_repository as q
from football_system.infrastructure.database.quant_integrity_schema import (
    QUANT_INTEGRITY_TABLES,
)
from football_system.infrastructure.files.training_evidence import (
    json_pointer,
    strict_json_bytes,
    training_review_input_sha256,
)
from tests.integration import test_corrected_quant_workflow as workflow
from tests.integration import test_training_admission_persistence as admissions

actual_admission = workflow.actual_admission
lane = workflow.lane
production = workflow.production
corrected = workflow.corrected


def definition_for(lane, *, versioned, correction_ids=()):
    context = lane.corrections.load_context(
        base_admissions=(lane.base_pin,),
        correction_ids=correction_ids,
        actual_at=lane.clock(),
    )
    definition = workflow.pilot_definition(lane, context)
    if versioned:
        return definition
    facts = {
        f.content_payload.normalized_result.match_id: f for f in lane.admission.facts
    }
    slices = tuple(
        QuantIntegritySliceV1.freeze(
            content_payload=s.content_payload.model_copy(
                update={
                    "targets": tuple(
                        QuantIntegrityTargetV1(
                            **t.model_dump(exclude={"schema_version", "fact"}),
                            fact=AdmittedFactRefV1.of(facts[t.fact.match_id]),
                        )
                        for t in s.content_payload.targets
                    )
                }
            )
        )
        for s in definition.slices
    )
    return QuantIntegrityPlanDefinitionV1.model_validate(
        {
            **definition.model_dump(
                exclude={
                    "schema_version",
                    "correction_context",
                    "context_registered_at_utc",
                    "slices",
                }
            ),
            "slices": slices,
        }
    )


def joint_captures(lane, document, alias):
    result = []
    for name in ("joint.json", "joint-alias.json") if alias else ("joint.json",):
        admissions.write_evidence(lane.root, name, document)
        result.append(
            lane.repo.capture_local_json(
                request_key=name,
                source_rights_admission_id=lane.recorded.source_rights_admission_id,
                source_id="source",
                provider_code="PROVIDER",
                evidence_reference=name,
            )
        )
    return result[0], result[-1]


def admit_joint_base(lane, alias):
    document = {
        role: strict_json_bytes(lane.repo.evidence.read(role + ".json"))
        for role in ("fixture", "scope", "result")
    }
    metadata, result = joint_captures(lane, document, alias)
    reviewed = lane.clock()
    submissions = []
    for i, original in enumerate(lane.submissions):
        c = original.candidate
        fixture = c.fixture_source.model_copy(
            update={
                "fixture_source_archive_id": metadata.capture_receipt_id,
                "fixture_source_archive_payload_sha256": metadata.payload_sha256,
                "fixture_source_archive_created_at_utc": metadata.archive_created_at_utc,
                "fixture_source_record_id": f"/fixture/records/{i}",
                "local_imported_at_utc": metadata.local_imported_at_utc,
                "registered_at_utc": metadata.registered_at_utc,
            }
        )
        membership = type(c.season_membership).freeze(
            content_payload=c.season_membership.content_payload.model_copy(
                update={
                    "fixture_source_record_id": fixture.fixture_source_record_id,
                    "provider_scope_raw_artifact_id": metadata.capture_receipt_id,
                    "provider_scope_payload_sha256": metadata.payload_sha256,
                    "provider_scope_created_at_utc": metadata.archive_created_at_utc,
                    "local_imported_at_utc": metadata.local_imported_at_utc,
                    "registered_at_utc": metadata.registered_at_utc,
                    "reviewed_at_utc": reviewed,
                }
            )
        )
        admitted_result = type(c.match_result_admission).freeze(
            content_payload=c.match_result_admission.content_payload.model_copy(
                update={
                    "raw_artifact_id": result.capture_receipt_id,
                    "raw_artifact_payload_sha256": result.payload_sha256,
                    "raw_artifact_created_at_utc": result.archive_created_at_utc,
                    "local_imported_at_utc": result.local_imported_at_utc,
                    "registered_at_utc": result.registered_at_utc,
                    "reviewed_at_utc": reviewed,
                }
            )
        )
        candidate = c.model_copy(
            update={
                "fixture_source": fixture,
                "season_membership": membership,
                "match_result_admission": admitted_result,
            }
        )
        evidence = original.source_evidence.model_copy(
            update={
                role: getattr(original.source_evidence, role).model_copy(
                    update={
                        "capture_receipt_id": (
                            result if role == "result" else metadata
                        ).capture_receipt_id,
                        "record_pointer": f"/{role}/records/{i}",
                    }
                )
                for role in ("fixture", "scope", "result")
            }
        )
        review = admissions.write_evidence(
            lane.root,
            f"joint-review-{i}.json",
            admissions.review_document(
                schema="TRAINING_FACT_REVIEW_INPUT_V1",
                digest=training_review_input_sha256(candidate, evidence),
                at=reviewed,
            ),
        )
        submissions.append(
            original.model_copy(
                update={
                    "candidate": candidate,
                    "source_evidence": evidence,
                    "reviewer_evidence": review,
                }
            )
        )
    lane.admission = lane.repo.admit(
        request_key="joint-admission",
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        submissions=tuple(submissions),
    )
    return metadata, result


@pytest.fixture
def capture_lane(tmp_path, monkeypatch, versioned, alias):
    # Change only the test input assembly BEFORE the first public admission.
    # Re-admitting changed captures would itself require a controlled correction.
    def admit(lane):
        lane.joint_receipts = admit_joint_base(lane, alias)
        return lane.admission

    with monkeypatch.context() as scoped:
        if not versioned:
            scoped.setattr(admissions, "admit", admit)
        generator = workflow.pilot_tests.actual_admission.__wrapped__(
            tmp_path, monkeypatch, SimpleNamespace(param=2)
        )
        lane = next(generator)
        try:
            yield lane
        finally:
            generator.close()


@pytest.mark.parametrize("versioned", [False, True], ids=["V1", "V2"])
@pytest.mark.parametrize(
    "alias", [False, True], ids=["same-receipt", "same-payload-alias"]
)
def test_joint_score_capture_is_rejected_before_plan_or_raw_read(
    capture_lane, monkeypatch, versioned, alias
):
    lane = capture_lane
    monkeypatch.setattr(q, "_code_revision", lambda: "package:synthetic-joint-capture")
    correction_ids = ()
    if not versioned:
        metadata, result = lane.joint_receipts
        workflow.enable_corrections(lane, lane.admission)
    else:
        workflow.enable_corrections(lane, lane.admission)
        original, _ = workflow.reviewed_intent(lane)
        document = {}
        for role in ("fixture", "scope", "result"):
            ref = getattr(original.evidence, role)
            _, raw = lane.repo.load_capture(ref.capture_receipt_id)
            document[role] = json_pointer(strict_json_bytes(raw), ref.record_pointer)
        metadata, result = joint_captures(lane, document, alias)
        evidence = original.evidence.model_copy(
            update={
                role: lane.corrections.capture_reference(
                    (result if role == "result" else metadata).capture_receipt_id,
                    record_pointer="/" + role,
                )
                for role in ("fixture", "scope", "result")
            }
        )
        intent = lane.corrections.prepare(
            predecessor_version_id=lane.base.version_id,
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            evidence=evidence,
            match_result_id=original.match_result_id,
        )
        review = admissions.write_evidence(
            lane.root,
            "joint-correction-review.json",
            admissions.review_document(
                schema=workflow.CORRECTION_INTENT_V2,
                digest=intent.intent_hash,
                at=lane.clock(),
            ),
        )
        correction_ids = (workflow.record(lane, (intent, review)).artifact_id,)
    definition = definition_for(
        lane, versioned=versioned, correction_ids=correction_ids
    )
    repo = q.SqlAlchemyQuantIntegrityRepository(
        lane.sessions,
        admission_repository=lane.repo,
        clock=lane.clock,
        operator_id="operator",
    )
    read, capture = lane.repo.evidence.read, lane.repo._capture
    result_reads, queries = [], []

    def no_joint_bytes(reference, *args, **kwargs):
        if reference in {metadata.evidence_reference, result.evidence_reference}:
            result_reads.append(reference)
            pytest.fail("joint target scores opened before plan/reservation")
        return read(reference, *args, **kwargs)

    def no_joint_capture(session, receipt_id):
        if receipt_id in {metadata.capture_receipt_id, result.capture_receipt_id}:
            pytest.fail("joint captured payload fetched before plan/reservation")
        return capture(session, receipt_id)

    def query(conn, cursor, statement, parameters, context, executemany):
        queries.append((statement, parameters))

    event.listen(lane.engine, "before_cursor_execute", query)
    try:
        with monkeypatch.context() as guarded:
            guarded.setattr(lane.repo.evidence, "read", no_joint_bytes)
            guarded.setattr(lane.repo, "_capture", no_joint_capture)
            guarded.setattr(
                lane.repo,
                "_load",
                lambda *a, **kw: pytest.fail("pre-plan full admission read"),
            )
            with pytest.raises(
                ValueError, match="separate metadata documents are required"
            ):
                QuantIntegrityPilotService(repo, lane.clock).seal_plan(definition)
    finally:
        event.remove(lane.engine, "before_cursor_execute", query)
    assert not result_reads
    assert not any(
        "payload_bytes" in sql and metadata.capture_receipt_id in str(params)
        for sql, params in queries
    )
    with lane.engine.connect() as connection:
        for table in QUANT_INTEGRITY_TABLES:
            assert (
                connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
                == 0
            )


@pytest.mark.parametrize("failure", ["guard-null", "check-null", "missing-fk"])
def test_version_context_sql_requires_nonnull_typed_correction_fk(corrected, failure):
    value = workflow.record(corrected.lane, workflow.reviewed_intent(corrected.lane))
    manifest = workflow.corrected_manifest(corrected, (value.artifact_id,))
    table = workflow.legacy.persistence._table("training_history_version_context")
    with corrected.lane.engine.connect() as connection:
        original = dict(
            connection.execute(
                select(table).where(table.c.correction_id == value.artifact_id)
            )
            .mappings()
            .one()
        )
        maximum = connection.execute(
            select(q.func.max(table.c.context_sequence))
        ).scalar_one()
    row = {
        **original,
        "context_sequence": maximum + 1,
        "revision_sequence": original["revision_sequence"] + 1,
        "predecessor_version_id": original["version_id"],
        "version_id": "missing-correction",
        "correction_id": "missing-correction" if failure == "missing-fk" else None,
    }
    document = strict_json_bytes(original["artifact_json"].encode())
    document["predecessor"] = dict(document["reference"])
    document["reference"]["artifact_id"] = row["version_id"]
    document["revision_sequence"] = row["revision_sequence"]
    row["artifact_json"] = q.canonical_json(document)
    expected = (
        "FOREIGN KEY"
        if failure == "missing-fk"
        else "CHECK constraint"
        if failure == "check-null"
        else "typed lineage"
    )
    with pytest.raises(IntegrityError, match=expected):
        with corrected.lane.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN")
            connection.exec_driver_sql(
                "DROP TRIGGER trg_training_history_version_context_sealed_insert"
            )
            if failure != "guard-null":
                connection.exec_driver_sql(
                    "DROP TRIGGER trg_training_history_version_context_lineage_insert"
                )
            connection.execute(table.insert().values(**row))
    assert corrected.repo.load_manifest(manifest.artifact_id) == manifest


@pytest.mark.parametrize("migrated", [False, True], ids=["runtime", "migrated"])
def test_f51_downgrade_preserves_populated_exact_v1_plan_rows(
    actual_admission, monkeypatch, migrated
):
    lane = actual_admission
    monkeypatch.setattr(q, "_code_revision", lambda: "package:synthetic-v1-downgrade")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(lane.engine.url))
    command.stamp(config, "head")
    if migrated:
        command.downgrade(config, "e40d183af576")
        command.upgrade(config, "head")
    workflow.enable_corrections(lane, lane.admission)
    definition = definition_for(lane, versioned=False)
    assert "schema_version" not in definition.model_dump()
    repo = q.SqlAlchemyQuantIntegrityRepository(
        lane.sessions,
        admission_repository=lane.repo,
        clock=lane.clock,
        operator_id="operator",
    )
    service = QuantIntegrityPilotService(repo, lane.clock)
    plan = service.seal_plan(definition)
    report = service.run(IntegrityArtifactRefV1.of(plan))

    def snapshot():
        with lane.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(f"SELECT * FROM {table}").all()
                for table in QUANT_INTEGRITY_TABLES
            }

    before = snapshot()
    command.downgrade(config, "e40d183af576")
    assert snapshot() == before
    assert repo.load_plan(IntegrityArtifactRefV1.of(plan)) == plan
    assert repo.load_report(IntegrityArtifactRefV1.of(report)) == report
    command.upgrade(config, "head")
    assert snapshot() == before
    command.check(config)


@pytest.mark.parametrize("kind", ["V2", "unknown", "missing-definition"])
def test_f51_downgrade_refuses_nonlegacy_plan_without_dropping_guards(
    actual_admission, monkeypatch, kind
):
    lane = actual_admission
    monkeypatch.setattr(q, "_code_revision", lambda: "package:synthetic-unknown-plan")
    workflow.enable_corrections(lane, lane.admission)
    definition = definition_for(lane, versioned=kind == "V2")
    repo = q.SqlAlchemyQuantIntegrityRepository(
        lane.sessions,
        admission_repository=lane.repo,
        clock=lane.clock,
        operator_id="operator",
    )
    plan = QuantIntegrityPilotService(repo, lane.clock).seal_plan(definition)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(lane.engine.url))
    command.stamp(config, "head")
    command.downgrade(config, "f51e294b0687")
    if kind != "V2":
        document = plan.model_dump(mode="json")
        if kind == "unknown":
            document["content_payload"]["definition"]["schema_version"] = "UNKNOWN"
        else:
            document["content_payload"]["definition"] = {}
        with lane.engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER trg_production_quant_integrity_pilot_plans_append_only_update"
            )
            connection.execute(
                text(
                    "UPDATE production_quant_integrity_pilot_plans SET artifact_json=:json"
                ),
                {"json": q.canonical_json(document)},
            )
    from tests.integration.test_database_schema import (
        _schema_signature,
        _trigger_signature,
    )

    before = _schema_signature(lane.engine), _trigger_signature(lane.engine)
    with pytest.raises(RuntimeError, match="(versioned|unknown).*pilot plan"):
        command.downgrade(config, "e40d183af576")
    assert (_schema_signature(lane.engine), _trigger_signature(lane.engine)) == before
    with lane.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == "f51e294b0687"
        )
