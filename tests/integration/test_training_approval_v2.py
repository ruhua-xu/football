"""SYNTHETIC_CONTRACT_TEST_ONLY: public V2 recorder, actual temporary review bytes.

Only the existing BridgeDouble substitutes pilot evidence. Admission, production,
inference, audit and downstream repositories remain real. No real-source bypass.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import json
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError

from football_system.domain.archive import canonical_json
from football_system.domain.production_release import (
    TRAINING_HISTORY_APPROVAL_PAYLOAD_V2,
    ProductionGrantKind,
    ProductionGrantV1,
    TrainingHistoryApprovalPayloadV2,
    TrainingHistoryApprovalV2,
    parse_training_history_approval,
)
from football_system.domain.training_admission import (
    LocalReviewerAttestationContentV1,
    LocalReviewerAttestationV1,
)
from football_system.infrastructure.database import (
    production_quant_repository as persistence,
)
from football_system.infrastructure.database.approval_v2_schema import (
    approval_v2_trigger_sql,
)
from football_system.infrastructure.database.production_quant_schema import (
    production_quant_trigger_sql_v1,
)
from tests.integration import test_production_quant_persistence as legacy
from tests.integration import test_production_inference as inference_tests
from tests.integration import test_production_audit as audit_tests

lane = legacy.lane
production = legacy.production


def prepare_values(context, value=None, *, updates=None, predecessor=None):
    value = value or legacy.manifest(context)
    if not hasattr(context, "approval_v2_authority"):
        context.approval_v2_authority = legacy.write_evidence(
            context.lane.root,
            "approval-v2-authority.json",
            dict(
                schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
                issued_by="governance",
                authorized_reviewer="reviewer",
                source_ids=["source"],
                attested_schema_versions=[TRAINING_HISTORY_APPROVAL_PAYLOAD_V2],
                effective_at_utc="2026-01-01T00:00:00Z",
                expires_at_utc="2031-01-01T00:00:00Z",
            ),
        )
        authority = context.approval_v2_authority
        context.lane.repo.evidence.trusted_authorities[authority.evidence_reference] = (
            authority.evidence_sha256
        )
    grants = []
    for kind in sorted(ProductionGrantKind):
        values = dict(
            grant=kind,
            effective_at_utc=value.content_payload.persisted_at_utc,
            expires_at_utc=legacy.END,
            retention_rule="test append-only retention"
            if kind in (legacy.STATE, legacy.AUDIT)
            else None,
            retention=legacy.horizon()
            if kind in (legacy.STATE, legacy.AUDIT)
            else None,
        )
        values.update((updates or {}).get(kind, {}))
        grants.append(ProductionGrantV1(**values))
    return dict(
        manifest_id=value.artifact_id,
        grants=tuple(grants),
        approver="reviewer",
        reviewer_authority=context.approval_v2_authority,
        retention_compatibility="APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE",
        supersedes_approval=None if predecessor is None else predecessor.reference(),
        supersession_effective_at_utc=None
        if predecessor is None
        else context.lane.clock.value + timedelta(hours=1),
        superseded_grants=() if predecessor is None else (legacy.TRAINING,),
    )


def prepare_v2(context, value=None, **kwargs):
    return context.repo.prepare_approval(**prepare_values(context, value, **kwargs))


def review_v2(context, payload, *, document_updates=None, attestation_updates=None):
    at = context.lane.clock()
    document = legacy.review_document(
        schema=payload.payload_version, digest=payload.approval_payload_hash, at=at
    )
    document.update(document_updates or {})
    evidence = legacy.write_evidence(
        context.lane.root, f"approval-v2-review-{at.timestamp()}.json", document
    )
    values = dict(
        attested_schema_version=payload.payload_version,
        attested_payload_hash=payload.approval_payload_hash,
        authorized_reviewer=payload.approver,
        reviewer_authority_reference=payload.authority_reference,
        authority_sha256=payload.authority_sha256,
        reviewed_at_utc=at,
        evidence=evidence,
    )
    values.update(attestation_updates or {})
    return LocalReviewerAttestationV1.freeze(
        content_payload=LocalReviewerAttestationContentV1(**values)
    )


def record_v2(context, value=None, *, key="approval-v2", **kwargs):
    payload = prepare_v2(context, value, **kwargs)
    return context.repo.record_approval(
        key, approval_payload=payload, reviewer_attestation=review_v2(context, payload)
    )


@pytest.fixture
def inference_v2(lane, monkeypatch):
    build = legacy.build
    with monkeypatch.context() as scoped:
        scoped.setattr(
            legacy, "build", lambda context: build(context, record_v2(context))
        )
        return inference_tests.inference.__wrapped__(lane, monkeypatch)


def test_prepare_readonly_original_review_exact_request_and_actual_clocks(production):
    values = prepare_values(production)
    before = legacy.counts(production)
    payload = production.repo.prepare_approval(**values)
    assert production.repo.prepare_approval(**values) == payload
    assert legacy.counts(production) == before
    wire = canonical_json(payload)
    assert all(
        key not in wire
        for key in (
            "recorded_at_utc",
            "persisted_at_utc",
            "actual_",
            "approved_at_utc",
            "raw_evidence",
        )
    )
    review = review_v2(production, payload)
    review_bytes = canonical_json(review)
    initial = len(production.lane.clock.calls)
    approval = production.repo.record_approval(
        "record", approval_payload=payload, reviewer_attestation=review
    )
    assert isinstance(approval, TrainingHistoryApprovalV2)
    assert approval.recorded_at_utc == production.lane.clock.calls[initial]
    assert approval.persisted_at_utc == production.lane.clock.calls[initial + 1]
    assert approval.subject == payload
    assert canonical_json(approval.content_payload.reviewer_attestation) == review_bytes
    assert (
        approval.content_payload.request_sha256
        == persistence._request(
            "record_approval",
            "record",
            "operator",
            approval_payload=payload,
            reviewer_attestation=review,
        )["request_sha256"]
    )
    with production.lane.sessions.begin() as session:
        row = persistence._required(
            session, "training_history_approval_events", approval.artifact_id
        )
        assert row["approved_at_utc"] == approval.recorded_at_utc
        assert row["persisted_at_utc"] == approval.persisted_at_utc
        assert (
            canonical_json(
                parse_training_history_approval(json.loads(row["artifact_json"]))
            )
            == row["artifact_json"]
        )
    calls = len(production.lane.clock.calls)
    assert (
        production.repo.record_approval(
            "record", approval_payload=payload, reviewer_attestation=review
        )
        == approval
    )
    assert len(production.lane.clock.calls) == calls
    assert legacy.counts(production)["training_history_approval_events"] == 1
    assert legacy.counts(production)["training_history_approval_grants"] == 4
    with pytest.raises(ValueError, match="retry request"):
        production.repo.record_approval(
            "record",
            approval_payload=payload,
            reviewer_attestation=review_v2(production, payload),
        )


def test_public_recorder_through_live_audit_review_fusion_and_portfolio(
    inference_v2, monkeypatch, tmp_path
):
    assert isinstance(inference_v2.release.training_approval, TrainingHistoryApprovalV2)
    audited = audit_tests.audited.__wrapped__(inference_v2, monkeypatch)
    audit_tests.test_atomic_packet_sidecar_exact_retry_and_full_downstream(
        audited, tmp_path
    )
    assert audit_tests.counts(audited)["portfolio_revisions"] == 1
    legacy.revoke(inference_v2.production, inference_v2.release.training_approval)
    with pytest.raises(ValueError, match="revoked"):
        audited.auditor.gate_run(audited.run_id)


@pytest.mark.parametrize("kind", list(ProductionGrantKind))
@pytest.mark.parametrize(
    "expiry_boundary", ["after-parent", "after-final-verification"]
)
def test_all_four_grants_checked_at_recording_and_last_boundary(
    production, monkeypatch, kind, expiry_boundary
):
    value = legacy.manifest(production)
    end = production.lane.clock.value + timedelta(minutes=1)
    changes = {"expires_at_utc": end}
    if kind in (legacy.STATE, legacy.AUDIT):
        changes["retention"] = legacy.horizon(end)
    payload = prepare_v2(production, value, updates={kind: changes})
    review = review_v2(production, payload)
    original_insert = persistence._insert

    def expire_after_parent(session, name, values):
        original_insert(session, name, values)
        if name == "training_history_approval_events":
            production.lane.clock.value = end

    if expiry_boundary == "after-parent":
        monkeypatch.setattr(persistence, "_insert", expire_after_parent)
    else:
        boundary = production.repo._approval_boundary

        def slow_final_verification(session, *args):
            result = boundary(session, *args)
            if session.scalar(
                text("SELECT COUNT(*) FROM training_history_approval_events")
            ):
                production.lane.clock.value = end
            return result

        monkeypatch.setattr(
            production.repo, "_approval_boundary", slow_final_verification
        )
    before = legacy.counts(production)
    with pytest.raises(ValueError, match="grant is not active"):
        production.repo.record_approval(
            "expiry", approval_payload=payload, reviewer_attestation=review
        )
    assert legacy.counts(production) == before


@pytest.mark.parametrize("kind", list(ProductionGrantKind))
def test_future_grant_does_not_infer_other_permissions(production, kind):
    value = legacy.manifest(production)
    payload = prepare_v2(
        production,
        value,
        updates={
            kind: {"effective_at_utc": production.lane.clock.value + timedelta(hours=1)}
        },
    )
    before = legacy.counts(production)
    with pytest.raises(ValueError, match="grant is not active"):
        production.repo.record_approval(
            "future",
            approval_payload=payload,
            reviewer_attestation=review_v2(production, payload),
        )
    assert legacy.counts(production) == before


@pytest.mark.parametrize(
    "document_updates,attestation_updates",
    [
        ({"attested_payload_hash": "0" * 64}, {}),
        ({"attested_schema_version": "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"}, {}),
        ({"authorized_reviewer": "other"}, {}),
        ({"source_ids": ["other"]}, {}),
        ({"retention_compatible": False}, {}),
        ({}, {"reviewed_at_utc": legacy.END}),
        ({}, {"attested_schema_version": "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"}),
    ],
)
def test_original_local_review_must_bind_exact_subject_schema_identity_and_time(
    production, document_updates, attestation_updates
):
    payload = prepare_v2(production)
    review = review_v2(
        production,
        payload,
        document_updates=document_updates,
        attestation_updates=attestation_updates,
    )
    before = legacy.counts(production)
    with pytest.raises(ValueError):
        production.repo.record_approval(
            "bad-review", approval_payload=payload, reviewer_attestation=review
        )
    assert legacy.counts(production) == before


@pytest.mark.parametrize(
    "field",
    [
        "manifest",
        "source_rights",
        "technical_evidence",
        "build_recipe",
        "code_revision",
        "approver",
        "authority_sha256",
        "grants",
    ],
)
def test_mutated_subject_old_review_and_rehashed_wrong_context_fail(production, field):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    values = payload.model_dump(mode="python")
    if field in ("manifest", "technical_evidence", "build_recipe"):
        values[field] = legacy.ref("switched")
    elif field == "source_rights":
        values[field][0]["terms_sha256"] = "0" * 64
    elif field == "grants":
        values[field][0]["retention_rule"] = "changed policy"
    else:
        values[field] = "0" * 64 if field == "authority_sha256" else "switched"
    changed = TrainingHistoryApprovalPayloadV2.model_validate(values)
    before = legacy.counts(production)
    with pytest.raises(ValueError):
        production.repo.record_approval(
            "mutated", approval_payload=changed, reviewer_attestation=review
        )
    # Even a new test review cannot authorize false persisted context/pins.
    if field != "grants":
        with pytest.raises(ValueError):
            production.repo.record_approval(
                "rehashed",
                approval_payload=changed,
                reviewer_attestation=review_v2(production, changed),
            )
    assert legacy.counts(production) == before


@pytest.mark.parametrize(
    "missing", ["authority", "review", "terms", "raw", "code", "pilot"]
)
def test_missing_authority_materials_or_current_context_cannot_record(
    production, monkeypatch, missing
):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    if missing == "authority":
        production.lane.repo.evidence.trusted_authorities.pop(
            payload.authority_reference
        )
    elif missing == "review":
        (
            production.lane.root / review.content_payload.evidence.evidence_reference
        ).unlink()
    elif missing == "terms":
        (production.lane.root / "terms.txt").unlink()
    elif missing == "raw":
        (production.lane.root / "fixture.json").unlink()
    elif missing == "code":
        monkeypatch.setattr(persistence, "_code_revision", lambda: "changed-package")
    else:
        production.bridge.fail = True
    before = legacy.counts(production)
    with pytest.raises(ValueError):
        production.repo.record_approval(
            "missing", approval_payload=payload, reviewer_attestation=review
        )
    assert legacy.counts(production) == before


def test_same_key_concurrent_retry_serializes_to_one_row(production):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    barrier = Barrier(2)

    def record():
        repository = persistence.SqlAlchemyProductionQuantRepository(
            production.lane.sessions,
            admission_repository=production.lane.repo,
            pilot_repository=production.bridge,
            operator_id="operator",
            clock=production.lane.clock,
        )
        barrier.wait(timeout=15)
        return repository.record_approval(
            "concurrent", approval_payload=payload, reviewer_attestation=review
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(record) for _ in range(2)]
        values = [future.result(timeout=120) for future in futures]
    assert values[0] == values[1]
    assert legacy.counts(production)["training_history_approval_events"] == 1


def test_v1_request_identity_cannot_be_reused_for_v2_and_v1_authority_cannot_switch_schema(
    production,
):
    old = legacy.seed_approval_fixture(production)
    with production.lane.sessions.begin() as session:
        row = dict(
            persistence._required(
                session, "training_history_approval_events", old.artifact_id
            )
        )
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    with pytest.raises(ValueError, match="retry request"):
        production.repo.record_approval(
            row["request_key"], approval_payload=payload, reviewer_attestation=review
        )
    values = payload.model_dump() | {
        "authority_reference": production.authority.evidence_reference,
        "authority_sha256": production.authority.evidence_sha256,
        "supersedes_approval": old.reference(),
        "supersession_effective_at_utc": production.lane.clock.value
        + timedelta(hours=1),
        "superseded_grants": (legacy.TRAINING,),
    }
    unlicensed = TrainingHistoryApprovalPayloadV2.model_validate(values)
    with pytest.raises(ValueError, match="exact payload|authority"):
        production.repo.record_approval(
            "v1-authority",
            approval_payload=unlicensed,
            reviewer_attestation=review_v2(production, unlicensed),
        )
    with production.lane.sessions.begin() as session:
        assert (
            dict(
                persistence._required(
                    session, "training_history_approval_events", old.artifact_id
                )
            )
            == row
        )
    assert production.repo.load_approval(old.artifact_id) == old


def test_midchildren_failure_rolls_back_then_exact_retry_records(
    production, monkeypatch
):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    before = legacy.counts(production)
    original = persistence._insert
    calls = []

    def fail(session, name, values):
        original(session, name, values)
        if name == "training_history_approval_grants":
            calls.append(name)
            if len(calls) == 2:
                raise RuntimeError("test child failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(persistence, "_insert", fail)
        with pytest.raises(RuntimeError, match="test child failure"):
            production.repo.record_approval(
                "rollback", approval_payload=payload, reviewer_attestation=review
            )
    assert legacy.counts(production) == before
    assert production.repo.record_approval(
        "rollback", approval_payload=payload, reviewer_attestation=review
    )


@pytest.mark.parametrize(
    "bad",
    [
        "schema",
        "subject-schema",
        "recorded-microsecond",
        "operator",
        "subject-observation",
        "grant-json",
        "grants-incomplete",
    ],
)
def test_replacement_sql_guards_reject_forged_projections_and_roll_back(
    production, monkeypatch, bad
):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    original = persistence._insert

    def mutate(session, name, values):
        if bad == "grant-json" and name == "training_history_approval_grants":
            document = json.loads(values["artifact_json"])
            document["grant"] = "NOT_A_PERMISSION"
        elif name == "training_history_approval_events":
            document = json.loads(values["artifact_json"])
            content = document["content_payload"]
            if bad == "schema":
                document["schema_version"] = "TRAINING_HISTORY_APPROVAL_V3"
            elif bad == "subject-schema":
                content["approval_payload"]["payload_version"] = (
                    "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"
                )
            elif bad == "recorded-microsecond":
                content["recorded_at_utc"] = (
                    (values["approved_at_utc"] + timedelta(microseconds=1))
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            elif bad == "operator":
                content["operator_id"] = "different"
            elif bad == "subject-observation":
                content["approval_payload"]["persisted_at_utc"] = content[
                    "persisted_at_utc"
                ]
            elif bad == "grants-incomplete":
                content["approval_payload"]["grants"] = (
                    content["approval_payload"]["grants"][:1] * 4
                )
        else:
            return original(session, name, values)
        values = persistence._row(
            name,
            **{
                **{key: value for key, value in values.items() if key != "row_sha256"},
                "artifact_json": canonical_json(document),
            },
        )
        return original(session, name, values)

    monkeypatch.setattr(persistence, "_insert", mutate)
    before = legacy.counts(production)
    with pytest.raises(IntegrityError, match="approval"):
        production.repo.record_approval(
            "sql-guard", approval_payload=payload, reviewer_attestation=review
        )
    assert legacy.counts(production) == before


def test_v2_recording_authority_expiry_and_no_invented_separation_of_duties(production):
    payload = prepare_v2(production)
    review = review_v2(
        production, payload, document_updates={"prepared_by": "reviewer"}
    )
    repository = persistence.SqlAlchemyProductionQuantRepository(
        production.lane.sessions,
        admission_repository=production.lane.repo,
        pilot_repository=production.bridge,
        operator_id="reviewer",
        clock=production.lane.clock,
    )
    approval = repository.record_approval(
        "same-actor", approval_payload=payload, reviewer_attestation=review
    )
    assert (
        approval.content_payload.operator_id == approval.subject.approver == "reviewer"
    )
    # Expire the V2 authority through a separately named test-only pin. Never
    # overwrite the authority referenced by a V1 or previously recorded approval.
    authority = legacy.write_evidence(
        production.lane.root,
        "expired-v2-authority.json",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by="governance",
            authorized_reviewer="reviewer",
            source_ids=["source"],
            attested_schema_versions=[TRAINING_HISTORY_APPROVAL_PAYLOAD_V2],
            effective_at_utc="2026-01-01T00:00:00Z",
            expires_at_utc=production.lane.clock.value,
        ),
    )
    production.lane.repo.evidence.trusted_authorities[authority.evidence_reference] = (
        authority.evidence_sha256
    )
    payload = TrainingHistoryApprovalPayloadV2.model_validate(
        payload.model_dump()
        | {
            "authority_reference": authority.evidence_reference,
            "authority_sha256": authority.evidence_sha256,
            "supersedes_approval": approval.reference(),
            "supersession_effective_at_utc": production.lane.clock.value
            + timedelta(hours=1),
            "superseded_grants": (legacy.TRAINING,),
        }
    )
    review = review_v2(production, payload)
    before = legacy.counts(production)
    with pytest.raises(ValueError, match="authority"):
        production.repo.record_approval(
            "expired-authority", approval_payload=payload, reviewer_attestation=review
        )
    assert legacy.counts(production) == before


def test_v1_predecessor_v2_successor_and_minimal_withdrawal_after_material_loss(
    production, monkeypatch
):
    predecessor = legacy.seed_approval_fixture(production)
    successor = record_v2(production, predecessor=predecessor)
    assert production.repo.load_approval(predecessor.artifact_id) == predecessor
    assert production.repo.load_approval(successor.artifact_id) == successor
    release = legacy.build(production, successor)
    (production.lane.root / "terms.txt").unlink()
    (
        production.lane.root
        / successor.content_payload.reviewer_attestation.content_payload.evidence.evidence_reference
    ).unlink()
    monkeypatch.setattr(persistence, "_code_revision", lambda: "drifted")
    production.bridge.fail = True
    event, intent, review = legacy.revoke(production, successor, release=release)
    assert event.content_payload.approval == successor.reference()
    assert (
        production.repo.record_revocation(
            "revocation", revocation_request=intent, review=review
        )
        == event
    )


def test_populated_v1_upgrade_preserves_every_row_request_json_checksum_and_columns(
    production,
):
    approval = legacy.seed_approval_fixture(production)
    legacy.build(production, approval)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(production.lane.engine.url))
    with production.lane.engine.begin() as connection:
        for name in approval_v2_trigger_sql():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        for statement in production_quant_trigger_sql_v1().values():
            connection.exec_driver_sql(statement)
    command.stamp(config, "c2ebf618d354")

    def snapshot():
        with production.lane.engine.connect() as connection:
            return {
                name: tuple(connection.execute(text(f"SELECT * FROM {name}")).all())
                for name in legacy.PRODUCTION_QUANT_TABLES
            }

    before = snapshot()
    columns = repr(
        inspect(production.lane.engine).get_columns("training_history_approval_events")
    )
    command.upgrade(config, "d3fc0729e465")
    assert snapshot() == before
    assert (
        repr(
            inspect(production.lane.engine).get_columns(
                "training_history_approval_events"
            )
        )
        == columns
    )
    assert production.repo.load_approval(approval.artifact_id) == approval
    record_v2(production, predecessor=approval)
    with pytest.raises(RuntimeError, match="immutable approval V2"):
        command.downgrade(config, "c2ebf618d354")


def test_migration_offline_replaces_triggers_only_and_refuses_offline_downgrade():
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "c2ebf618d354:d3fc0729e465", sql=True)
    sql = output.getvalue()
    assert "CREATE TRIGGER" in sql and "DROP TRIGGER" in sql
    assert "ALTER TABLE" not in sql and "UPDATE training_history" not in sql
    with pytest.raises(RuntimeError, match="offline approval V2 downgrade"):
        command.downgrade(config, "d3fc0729e465:c2ebf618d354", sql=True)


@pytest.mark.parametrize("withdrawal", ["revocation", "successor"])
@pytest.mark.parametrize(
    "review_kind", ["original", "path-alias", "reencoded", "fresh"]
)
def test_withdrawn_review_cannot_issue_another_root_with_a_new_request_key(
    production, withdrawal, review_kind
):
    payload = prepare_v2(production)
    attestation = review_v2(production, payload)
    original = production.repo.record_approval(
        "original", approval_payload=payload, reviewer_attestation=attestation
    )
    if withdrawal == "revocation":
        legacy.revoke(production, original, kind=legacy.TRAINING)
    else:
        successor = record_v2(production, predecessor=original)
        production.lane.clock.value = successor.subject.supersession_effective_at_utc
    supplied = attestation
    if review_kind in {"path-alias", "reencoded"}:
        evidence = attestation.content_payload.evidence
        raw = production.lane.repo.evidence.read(
            evidence.evidence_reference, evidence.evidence_sha256
        )
        if review_kind == "reencoded":
            raw = json.dumps(json.loads(raw), indent=2).encode()
        alias = legacy.write_evidence(production.lane.root, "review-alias.json", raw)
        supplied = LocalReviewerAttestationV1.freeze(
            content_payload=attestation.content_payload.model_copy(
                update={"evidence": alias}
            )
        )
    elif review_kind == "fresh":
        supplied = review_v2(production, payload)
    before = legacy.counts(production)
    with pytest.raises(
        ValueError, match="already recorded|explicit fresh reviewed successor"
    ):
        production.repo.record_approval(
            "renamed-request", approval_payload=payload, reviewer_attestation=supplied
        )
    assert (
        production.repo.record_approval(
            "original", approval_payload=payload, reviewer_attestation=attestation
        )
        == original
    )
    with pytest.raises(ValueError, match="revoked|superseded"):
        legacy.build(production, original)
    assert legacy.counts(production) == before


def test_withdrawn_scope_cannot_reset_via_authority_alias_or_new_manifest(production):
    original = record_v2(production)
    legacy.revoke(production, original, kind=legacy.TRAINING)
    authority = production.approval_v2_authority
    alias = legacy.write_evidence(
        production.lane.root,
        "authority-alias.json",
        production.lane.repo.evidence.read(
            authority.evidence_reference, authority.evidence_sha256
        ),
    )
    production.lane.repo.evidence.trusted_authorities[alias.evidence_reference] = (
        alias.evidence_sha256
    )
    values = prepare_values(production, legacy.manifest(production, key="new-manifest"))
    values["reviewer_authority"] = alias
    payload = production.repo.prepare_approval(**values)
    assert payload.manifest != original.subject.manifest
    assert payload.approval_payload_hash != original.subject.approval_payload_hash
    before = legacy.counts(production)
    with pytest.raises(ValueError, match="explicit fresh reviewed successor"):
        production.repo.record_approval(
            "renamed-authority",
            approval_payload=payload,
            reviewer_attestation=review_v2(production, payload),
        )
    assert legacy.counts(production) == before


def test_fresh_successor_must_review_withdrawal_and_explicitly_restore_affected_grants(
    production,
):
    original = record_v2(production)
    payload = prepare_v2(production, predecessor=original)
    stale_review = review_v2(production, payload)
    legacy.revoke(production, original, kind=legacy.TRAINING)
    before = legacy.counts(production)
    with pytest.raises(ValueError, match="acknowledge recorded withdrawals"):
        production.repo.record_approval(
            "stale-successor",
            approval_payload=payload,
            reviewer_attestation=stale_review,
        )
    wrong_grants = TrainingHistoryApprovalPayloadV2.model_validate(
        payload.model_dump() | {"superseded_grants": (legacy.INFERENCE,)}
    )
    with pytest.raises(ValueError, match="explicitly reauthorize affected grants"):
        production.repo.record_approval(
            "wrong-grants",
            approval_payload=wrong_grants,
            reviewer_attestation=review_v2(production, wrong_grants),
        )
    assert legacy.counts(production) == before
    review = review_v2(production, payload)
    successor = production.repo.record_approval(
        "renewed", approval_payload=payload, reviewer_attestation=review
    )
    assert successor.subject.supersedes_approval == original.reference()
    assert successor.content_payload.reviewer_attestation == review
    production.lane.clock.value = payload.supersession_effective_at_utc
    assert legacy.build(production, successor).training_approval == successor
    with pytest.raises(ValueError, match="revoked|superseded"):
        legacy.build(production, original, key="withdrawn-release")


@pytest.mark.parametrize(
    "kind",
    ["original", "path-alias", "fresh", "missing-grant-policy"],
)
def test_sql_guard_independently_rejects_review_replay_and_scope_reset(
    production, monkeypatch, kind
):
    original = record_v2(production)
    legacy.revoke(production, original, kind=legacy.TRAINING)
    payload = original.subject
    review = original.content_payload.reviewer_attestation
    if kind == "path-alias":
        evidence = review.content_payload.evidence
        alias = legacy.write_evidence(
            production.lane.root,
            "sql-review-alias.json",
            production.lane.repo.evidence.read(
                evidence.evidence_reference, evidence.evidence_sha256
            ),
        )
        review = LocalReviewerAttestationV1.freeze(
            content_payload=review.content_payload.model_copy(
                update={"evidence": alias}
            )
        )
    elif kind == "fresh":
        review = review_v2(production, payload)
    elif kind == "missing-grant-policy":
        payload = prepare_v2(production, predecessor=original)
        payload = TrainingHistoryApprovalPayloadV2.model_validate(
            payload.model_dump() | {"superseded_grants": (legacy.INFERENCE,)}
        )
        review = review_v2(production, payload)
    # Exercise the independent SQLite barrier, not a seeded successful approval.
    monkeypatch.setattr(
        production.repo, "_assert_approval_reauthorization", lambda *args: None
    )
    before = legacy.counts(production)
    with pytest.raises(IntegrityError, match="review replay|fresh successor policy"):
        production.repo.record_approval(
            "sql-replay", approval_payload=payload, reviewer_attestation=review
        )
    assert legacy.counts(production) == before


@pytest.mark.parametrize("operation", ["approval", "build"])
@pytest.mark.parametrize("expire_source", [False, True])
def test_last_clock_checks_source_rights_without_any_further_io(
    production, monkeypatch, operation, expire_source
):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    approval = None
    if operation == "build":
        approval = production.repo.record_approval(
            "approval", approval_payload=payload, reviewer_attestation=review
        )
    before = legacy.counts(production)
    cutoff = production.lane.clock()
    expiry = production.admission.source_rights_admission.content_payload.rights_payload.expires_at_utc
    calls, final = [], False
    original_clock = production.lane.clock
    original_read = production.lane.repo.evidence.read
    original_revision = persistence._code_revision

    def clock():
        nonlocal final
        at = expiry if expire_source and len(calls) == 4 else original_clock()
        calls.append(at)
        if len(calls) == 5:
            final = True
        return at

    def guard_sql(*args):
        assert not final, "database I/O after final clock"

    def read(*args, **kwargs):
        assert not final, "evidence I/O after final clock"
        return original_read(*args, **kwargs)

    def revision():
        assert not final, "package I/O after final clock"
        return original_revision()

    def execute():
        if operation == "approval":
            return production.repo.record_approval(
                "approval", approval_payload=payload, reviewer_attestation=review
            )
        return production.repo.build_release(
            "release", approval.artifact_id, cutoff, legacy.horizon(), legacy.horizon()
        )

    monkeypatch.setattr(production.repo, "_clock", clock)
    monkeypatch.setattr(production.lane.repo.evidence, "read", read)
    monkeypatch.setattr(persistence, "_code_revision", revision)
    event.listen(production.lane.engine, "before_cursor_execute", guard_sql)
    try:
        if expire_source:
            with pytest.raises(
                ValueError, match="source rights admission is not active"
            ):
                execute()
        else:
            assert execute()
        assert len(calls) == 5
    finally:
        final = False
        event.remove(production.lane.engine, "before_cursor_execute", guard_sql)
    if expire_source:
        assert legacy.counts(production) == before


@pytest.mark.parametrize("withdrawal", ["revocation", "successor"])
@pytest.mark.parametrize("kind", [legacy.TRAINING, legacy.STATE, legacy.AUDIT])
def test_build_checks_scheduled_withdrawal_after_final_snapshot_io(
    production, monkeypatch, withdrawal, kind
):
    original = record_v2(production)
    if withdrawal == "revocation":
        effective = production.lane.clock.value + timedelta(hours=1)
        legacy.revoke(production, original, kind=kind, effective_at=effective)
    else:
        values = prepare_values(production, predecessor=original)
        values["superseded_grants"] = (kind,)
        payload = production.repo.prepare_approval(**values)
        successor = production.repo.record_approval(
            "successor",
            approval_payload=payload,
            reviewer_attestation=review_v2(production, payload),
        )
        effective = successor.subject.supersession_effective_at_utc
    before = legacy.counts(production)
    collect = production.repo.correction_events_in_session
    snapshots = []

    def finish_io(session, history, at=None):
        result = collect(session, history, at)
        if at is None:
            snapshots.append(True)
            production.lane.clock.value = effective
        return result

    monkeypatch.setattr(production.repo, "correction_events_in_session", finish_io)
    with pytest.raises(ValueError, match="revoked|superseded"):
        legacy.build(production, original)
    assert snapshots == [True]
    assert legacy.counts(production) == before


@pytest.mark.parametrize("withdrawal", ["revocations", "successors"])
def test_build_rejects_incomplete_snapshot_even_for_future_effective_events(
    production, monkeypatch, withdrawal
):
    original = record_v2(production)
    if withdrawal == "revocations":
        legacy.revoke(
            production,
            original,
            kind=legacy.TRAINING,
            effective_at=production.lane.clock.value + timedelta(hours=1),
        )
    else:
        record_v2(production, key="successor", predecessor=original)
    before = legacy.counts(production)
    authorize = production.repo._authorization

    def omit(session, *args):
        return authorize(session, *args).model_copy(update={withdrawal: ()})

    monkeypatch.setattr(production.repo, "_authorization", omit)
    with pytest.raises(ValueError, match="complete transaction event snapshots"):
        legacy.build(production, original)
    assert legacy.counts(production) == before


def test_concurrent_new_request_keys_cannot_spend_one_review_twice(production):
    payload = prepare_v2(production)
    review = review_v2(production, payload)
    barrier = Barrier(2)

    def record(key):
        repository = persistence.SqlAlchemyProductionQuantRepository(
            production.lane.sessions,
            admission_repository=production.lane.repo,
            pilot_repository=production.bridge,
            operator_id="operator",
            clock=production.lane.clock,
        )
        barrier.wait(timeout=15)
        try:
            return repository.record_approval(
                key, approval_payload=payload, reviewer_attestation=review
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(record, key) for key in ("first-key", "second-key")]
        results = [future.result(timeout=120) for future in futures]
    assert sum(isinstance(result, TrainingHistoryApprovalV2) for result in results) == 1
    assert (
        sum(
            isinstance(result, str) and "already recorded" in result
            for result in results
        )
        == 1
    )
    assert legacy.counts(production)["training_history_approval_events"] == 1
    assert legacy.counts(production)["training_history_approval_grants"] == 4


def test_fresh_review_can_follow_an_effective_successor(production):
    original = record_v2(production)
    successor = record_v2(production, key="successor", predecessor=original)
    production.lane.clock.value = successor.subject.supersession_effective_at_utc
    renewed = record_v2(production, key="renewed", predecessor=successor)
    assert renewed.subject.supersedes_approval == successor.reference()
    production.lane.clock.value = renewed.subject.supersession_effective_at_utc
    assert legacy.build(production, renewed).training_approval == renewed
    with pytest.raises(ValueError, match="superseded"):
        legacy.build(production, successor, key="old-release")
