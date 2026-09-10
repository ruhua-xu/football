"""SYNTHETIC_CONTRACT_TEST_ONLY: invented bytes/reviews in isolated pytest storage.

No real activation, acquisition, keys or DRAFT. Fixture entry points route through
the CLI's concrete repositories, not seeded approvals. The positive fixture uses
REAL_SOURCE-shaped contracts with full persisted technical verification, never
actual vendor rights. Synthetic pilots and adversarial echo bridges must fail
production promotion; there is no runtime allow-synthetic flag or verifier bypass.
"""

import hashlib
import json
import os
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from football_system.domain.archive import canonical_json
from football_system.domain.common import utc_now
from football_system.domain.observed_training import (
    CurrentSnapshotCollectionScopeV1,
    ObservedCollectionScopeAdmissionV1,
    ObservedSnapshotAdmissionV1,
    ObservedSnapshotContextV1,
    ObservedSnapshotSubjectV1,
    ObservedSnapshotSubmissionV1,
    observed_snapshot_root,
)
from football_system.domain.production_release import (
    ObservedFactRefV1,
    ProductionGrantKind,
    ProductionQuantModelReleaseV1,
    ProductionTargetAcceptancePlanV1,
    TrainingHistoryApprovalPayloadV2,
    TrainingHistoryApprovalV2,
    TrainingHistoryManifestV1,
)
from football_system.domain.quant_integrity import (
    QuantIntegrityAttestationV1,
    QuantIntegrityPlanV1,
    QuantIntegrityReportV1,
)
from football_system.domain.training_admission import SourceRightsAdmissionV1
from football_system.infrastructure.database.models import (
    QuantIntegrityAttestationRecord,
    QuantIntegrityPlanRecord,
    QuantIntegrityReportRecord,
)
from football_system.infrastructure.database.observed_training_repository import (
    SqlAlchemyObservedTrainingRepository,
)
from football_system.infrastructure.database.training_admission_repository import (
    TrainingCaptureReceiptV1,
)
from football_system.infrastructure.files.production_audit_bundle import (
    read_production_audit_bundle,
)
from football_system.interfaces import production_quant_cli as cli
from football_system.interfaces.cli import main
from tests.e2e import test_production_live_cli as live_cli
from tests.e2e import test_production_quant_cli as previous
from tests.integration import test_observed_quant_workflow as workflow
from tests.integration import test_observed_training_admission as obs
from tests.integration import test_production_audit as audit
from tests.integration import test_production_inference as live
from tests.integration import test_training_approval_v2 as approvals

lane = previous.lane
offline = previous.offline
VERSIONS = {
    "observed-scope-prepare": "PRODUCTION_QUANT_OBSERVED_SCOPE_PREPARE_REQUEST_V1",
    "observed-scope-record": "PRODUCTION_QUANT_OBSERVED_SCOPE_RECORD_REQUEST_V1",
    "observed-prepare": "PRODUCTION_QUANT_OBSERVED_PREPARE_REQUEST_V1",
    "observed-admit": "PRODUCTION_QUANT_OBSERVED_ADMIT_REQUEST_V1",
    "observed-context": "PRODUCTION_QUANT_OBSERVED_CONTEXT_REQUEST_V1",
    "observed-inspect": "PRODUCTION_QUANT_OBSERVED_INSPECT_REQUEST_V1",
}


@pytest.fixture(autouse=True)
def no_key_reads(monkeypatch, offline):
    original = type(os.environ).__getitem__

    def read(environment, key):
        assert key.upper() not in {"SPORTMONKS_API_TOKEN", "THE_ODDS_API_KEY"}
        return original(environment, key)

    monkeypatch.setattr(type(os.environ), "__getitem__", read)


def tagged(command, values):
    return {"schema_version": VERSIONS[command], **values}


def invoke(lane, command, request, capsys, *, output=None, database="lane.db"):
    lane.cli_serial = getattr(lane, "cli_serial", 0) + 1
    output = output or lane.root / f"private-cli-{lane.cli_serial}.json"
    code, receipt = previous.invoke(
        lane,
        command,
        request,
        capsys,
        database=database,
        extra=("--output", str(output)),
    )
    if code:
        return code, receipt
    wire = output.read_bytes()
    assert receipt["output_bytes"] == len(wire)
    assert receipt["output_sha256"] == hashlib.sha256(wire).hexdigest()
    assert len(canonical_json(receipt)) < 2500
    assert not {"result", "review_subjects", "record_references"} & receipt.keys()
    assert previous.SECRET.encode() not in wire
    response = json.loads(wire)
    assert wire == (canonical_json(response) + "\n").encode()
    lane.last_receipt = receipt
    return code, response


def public(lane, command, request, capsys, model=None):
    code, response = invoke(lane, command, request, capsys)
    assert code == 0, response
    return model.model_validate(response["result"]) if model else response


@pytest.fixture
def synthetic_writer(lane, capsys, monkeypatch):
    monkeypatch.setattr(cli, "utc_now", lane.clock)
    lane.cli_requests, lane.cli_responses = {}, {}

    def operation(command, values, model=None):
        request = tagged(command, values) if command in VERSIONS else values
        response = public(lane, command, request, capsys)
        lane.cli_requests[command], lane.cli_responses[command] = request, response
        return model.model_validate(response["result"]) if model else response

    def capture(**values):
        # Original, noncanonical native bytes with private unrelated metadata.
        path = lane.root / values["evidence_reference"]
        raw = json.loads(path.read_bytes())
        raw["private_provider_metadata"] = previous.SECRET
        path.write_bytes(json.dumps(raw, indent=2).encode())
        lane.original_bytes = path.read_bytes()
        return operation("capture", values, TrainingCaptureReceiptV1)

    monkeypatch.setattr(
        lane.repo,
        "record",
        lambda **v: operation("rights-record", v, SourceRightsAdmissionV1),
    )
    monkeypatch.setattr(lane.repo, "capture_local_json", capture)

    def prepare(**values):
        response = operation("observed-prepare", values)
        return tuple(
            ObservedSnapshotSubjectV1.model_validate(s) for s in response["result"]
        )

    # Only the fixture's constructor alias is replaced. CLI constructs the real class.
    with monkeypatch.context() as scoped:
        scoped.setattr(
            workflow,
            "SqlAlchemyObservedTrainingRepository",
            lambda repo: SimpleNamespace(
                prepare_scope=lambda **v: operation(
                    "observed-scope-prepare", v, CurrentSnapshotCollectionScopeV1
                ),
                record_scope=lambda **v: operation(
                    "observed-scope-record", v, ObservedCollectionScopeAdmissionV1
                ),
                prepare=prepare,
                admit=lambda **v: operation(
                    "observed-admit", v, ObservedSnapshotAdmissionV1
                ),
            ),
        )
        workflow.observed_lane.__wrapped__(lane, monkeypatch)
    lane.observed = SqlAlchemyObservedTrainingRepository(lane.repo)
    return lane


def context(lane, capsys, *, cutoff=None, exclude=()):
    return public(
        lane,
        "observed-context",
        tagged(
            "observed-context",
            dict(
                scope=lane.cli_responses["observed-scope-record"]["reference"],
                selection_cutoff_at_utc=cutoff or lane.clock(),
                exclude_match_ids=exclude,
            ),
        ),
        capsys,
    )["result"]


def pilot(lane, capsys, monkeypatch, *, evidence_use="SYNTHETIC_CONTRACT_ONLY"):
    pinned = context(lane, capsys)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            lane.observed,
            "load_context",
            lambda scope_id: ObservedSnapshotContextV1.model_validate(
                pinned["observed_context"]
            ),
        )
        scoped.setattr(
            workflow,
            "QuantIntegrityPilotService",
            lambda *a: SimpleNamespace(
                seal_plan=lambda definition: public(
                    lane,
                    "pilot-plan",
                    {"definition": definition},
                    capsys,
                    QuantIntegrityPlanV1,
                ),
                run=lambda plan_ref: public(
                    lane,
                    "pilot-run",
                    {"plan_ref": plan_ref},
                    capsys,
                    QuantIntegrityReportV1,
                ),
                seal_terminal_attestation=lambda plan_ref: public(
                    lane,
                    "pilot-attest",
                    {"plan_ref": plan_ref},
                    capsys,
                    QuantIntegrityAttestationV1,
                ),
            ),
        )
        result = workflow.run_integrity(lane, evidence_use=evidence_use)
    assert (
        result.definition.observed_context.model_dump(mode="json")
        == pinned["observed_context"]
    )
    return result


@pytest.fixture
def synthetic_real_source_contract(synthetic_writer, capsys, monkeypatch):
    """Invented contract simulation, not real data or permission to activate it."""
    return synthetic_writer, pilot(
        synthetic_writer, capsys, monkeypatch, evidence_use="REAL_SOURCE"
    )


def test_cli_shared_original_bytes_exact_subjects_and_current_context(
    synthetic_writer, capsys, monkeypatch
):
    lane = synthetic_writer
    scope = lane.cli_responses["observed-scope-prepare"]
    assert scope["status"] == "PREPARED"
    assert scope["source_scope_subject_hash"] == lane.scope.subject.subject_hash
    assert scope["review_subject"] == {
        "schema_version": "CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1",
        "payload_hash": lane.scope.subject.subject_hash,
    }
    assert (
        lane.scope.subject.source_rights_admission_hash == lane.recorded.admission_hash
    )
    preview = lane.cli_responses["observed-prepare"]
    subjects = tuple(
        ObservedSnapshotSubjectV1.model_validate(s) for s in preview["result"]
    )
    assert preview["review_subject_count"] == len(subjects) == 6
    assert preview["review_subjects"] == [
        {"schema_version": s.schema_version, "payload_hash": s.subject_hash}
        for s in subjects
    ]
    assert [r.subject for r in lane.base.records] == list(subjects)
    assert [r.reviewer_attestation for r in lane.base.records] == [
        s.reviewer_attestation
        for s in lane.cli_requests["observed-admit"]["submissions"]
    ]
    assert obs.counts(lane)["training_capture_receipts"] == 1
    assert (lane.root / "shared-native.json").read_bytes() == lane.original_bytes
    digest = hashlib.sha256(lane.original_bytes).hexdigest()
    for i, record in enumerate(lane.base.records):
        s = record.subject
        assert s.fixture_capture == s.season_capture == s.result_capture
        assert s.input.fixture == s.input.season == s.input.result
        assert s.result_capture.record_pointer == f"/data/{i}"
        assert s.result_capture.payload_sha256 == digest
        assert s.result_capture.outcome_sha256 == s.inspection.outcome_sha256
        assert (
            s.result_capture.capture_ordinal > lane.scope.capture_receipt_high_watermark
        )
        assert s.inspection.field_evidence["raw_payload_sha256"] == digest
        assert s.inspection.field_evidence["record_pointer"] == f"/data/{i}"
        assert s.inspection.field_evidence["scope"]
        assert s.inspection.field_evidence["scores"]
        assert s.upstream_publication_at_utc is s.provider_finalized_at_utc is None
        assert (
            s.inspection.provider_publication_at_utc
            is s.inspection.provider_version_id
            is None
        )
        assert s.result_capture.capture_kind == "LOCAL_FILE_IMPORT"
        assert record.normalized_result.observed_at_utc == s.capture_observed_at_utc
        assert record.normalized_result.available_at_utc == record.registered_at_utc
    assert len({s.result_capture.capture_ordinal for s in subjects}) == 1
    assert (
        context(lane, capsys, cutoff=lane.base.registered_at_utc)["selected_heads"]
        == []
    )
    current = context(lane, capsys, exclude=("sm-match-0",))
    graph = ObservedSnapshotContextV1.model_validate(current["observed_context"])
    assert current["scope"] == lane.cli_responses["observed-scope-record"]["reference"]
    assert current["context_root"] == graph.base_root
    assert current["selected_versions_root"] == observed_snapshot_root(
        graph.records[1:]
    )
    assert len(current["selected_heads"]) == 5 and len(graph.records) == 6
    assert graph.actual_at_utc in lane.clock.calls
    before = obs.counts(lane)
    assert (
        public(lane, "observed-admit", lane.cli_requests["observed-admit"], capsys)
        == lane.cli_responses["observed-admit"]
    )
    assert obs.counts(lane) == before
    monkeypatch.setattr(
        cli, "utc_now", lambda: lane.rights.expires_at_utc + timedelta(seconds=1)
    )
    inspected = public(
        lane,
        "observed-inspect",
        tagged("observed-inspect", {"admission_id": lane.base.admission_id}),
        capsys,
    )
    assert inspected["result"] == lane.base.model_dump(mode="json")
    assert inspected["record_references"] == [
        ObservedFactRefV1.of(r).model_dump(mode="json") for r in lane.base.records
    ]
    code, error = invoke(
        lane,
        "observed-context",
        tagged(
            "observed-context",
            dict(
                scope=current["scope"],
                selection_cutoff_at_utc=lane.clock(),
            ),
        ),
        capsys,
    )
    assert code == 1 and error["persistence"] == "NO_NEW_ROWS"


def test_scope_prepare_actual_clock_no_new_review_or_old_grant_extension(
    synthetic_writer, capsys, monkeypatch
):
    lane = synthetic_writer
    before = (lane.root / "lane.db").read_bytes()
    review_files = {p.name: p.read_bytes() for p in lane.root.glob("*review*.json")}
    monkeypatch.setattr(cli, "utc_now", utc_now)
    response = public(
        lane,
        "observed-scope-prepare",
        lane.cli_requests["observed-scope-prepare"],
        capsys,
    )
    assert response == lane.cli_responses["observed-scope-prepare"]
    assert (lane.root / "lane.db").read_bytes() == before
    assert {
        p.name: p.read_bytes() for p in lane.root.glob("*review*.json")
    } == review_files
    assert "reviewed_at_utc" not in response["result"]
    assert "recorded_at_utc" not in response["result"]
    request = dict(
        lane.cli_requests["observed-scope-record"],
        reviewer_attestation=lane.attestation,
    )
    calls = previous.no_database_calls(monkeypatch)
    code, error = invoke(lane, "observed-scope-record", request, capsys)
    assert code == 2 and error["persistence"] == "NO_DATABASE_OPENED"
    assert not calls


@pytest.mark.parametrize(
    "bad",
    [
        "untagged",
        "old-scope",
        "old-terms",
        "actual-time",
        "http-budget",
        "missing-authority",
        "mixed-basis",
        "untagged-subject",
    ],
)
def test_new_requests_fail_preflight_without_provider_bytes(
    synthetic_writer, capsys, monkeypatch, bad
):
    lane = synthetic_writer
    command = "observed-scope-prepare"
    request = json.loads(canonical_json(lane.cli_requests[command]))
    if bad == "untagged":
        del request["schema_version"]
    elif bad == "old-scope":
        request["schema_version"] = "ORIGINAL_SCOPE_V1"
    elif bad == "old-terms":
        request["user_terms_resolution"] = (
            lane.attestation.content_payload.evidence.model_dump(mode="json")
        )
    elif bad == "actual-time":
        request["actual_at_utc"] = "2025-01-01T00:00:00Z"
    elif bad == "http-budget":
        request["max_http_requests"] = request.pop("max_capture_receipts")
    else:
        command = "observed-admit"
        request = json.loads(canonical_json(lane.cli_requests[command]))
        if bad == "missing-authority":
            lane.repo.evidence.trusted_authorities.pop(
                lane.observed_authority.evidence_reference
            )
        elif bad == "mixed-basis":
            request["submissions"][0]["subject"]["evidence_basis"] = (
                "VERIFIED_HISTORICAL_SOURCE_TIME"
            )
        else:
            del request["submissions"][0]["subject"]["schema_version"]
    reads = []
    original = cli.LocalTrainingEvidence.read

    def read(self, reference, expected_sha256=None):
        if reference == "shared-native.json":
            reads.append(reference)
            pytest.fail("preflight read provider bytes")
        return original(self, reference, expected_sha256)

    monkeypatch.setattr(cli.LocalTrainingEvidence, "read", read)
    calls = previous.no_database_calls(monkeypatch)
    code, error = invoke(lane, command, request, capsys, database="absent.db")
    assert code == 2 and error["persistence"] == "NO_DATABASE_OPENED"
    assert not reads and not calls and not (lane.root / "absent.db").exists()


@pytest.mark.parametrize(
    "command",
    [
        "observed-scope-prepare",
        "observed-prepare",
        "observed-context",
        "observed-inspect",
    ],
)
def test_reads_are_ro_no_migration_and_no_caller_context_clock(
    synthetic_writer, capsys, monkeypatch, command
):
    lane = synthetic_writer
    requests = {
        **lane.cli_requests,
        "observed-context": tagged(
            "observed-context",
            dict(
                scope=lane.cli_responses["observed-scope-record"]["reference"],
                selection_cutoff_at_utc=lane.clock(),
            ),
        ),
        "observed-inspect": tagged(
            "observed-inspect", {"admission_id": lane.base.admission_id}
        ),
    }
    # Re-prepare a new local observation, not the already-admitted receipt.
    if command == "observed-prepare":
        pending = successor(lane, capsys)
        requests[command] = tagged(
            command,
            dict(
                scope_id=lane.scope.scope_id,
                snapshots=tuple(s.subject.input for s in pending["submissions"]),
            ),
        )
    original = cli._execute

    def readonly(command, request, admissions, pilots, production, **kwargs):
        with admissions._sessions.kw["bind"].connect() as connection:
            with pytest.raises(OperationalError, match="readonly"):
                connection.execute(text("CREATE TABLE forbidden_write (id INTEGER)"))
        return original(command, request, admissions, pilots, production, **kwargs)

    monkeypatch.setattr(cli, "_execute", readonly)
    monkeypatch.setattr(
        cli, "upgrade_database", lambda *a: pytest.fail("read migrated")
    )
    old = lane.root / "old.db"
    with sqlite3.connect(old) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('f51e294b0687')")
    old_bytes = old.read_bytes()
    for database in ("absent.db", "old.db"):
        code, error = invoke(
            lane, command, requests[command], capsys, database=database
        )
        assert code == 1 and error["persistence"] == "NO_NEW_ROWS"
    assert not (lane.root / "absent.db").exists() and old.read_bytes() == old_bytes
    before = (lane.root / "lane.db").read_bytes()
    code, response = invoke(lane, command, requests[command], capsys)
    assert code == 0, response
    assert (lane.root / "lane.db").read_bytes() == before

    def output_failure(*args):
        raise OSError(previous.SECRET)

    with monkeypatch.context() as scoped:
        scoped.setattr(cli, "write_local_json", output_failure)
        code, error = invoke(lane, command, requests[command], capsys)
    assert code == 1 and error["code"] == "OUTPUT_NOT_WRITTEN"
    assert error["persistence"] == "NO_NEW_ROWS"
    assert (lane.root / "lane.db").read_bytes() == before
    if command == "observed-context":
        for extra in (
            {"actual_at": lane.clock()},
            {"actual_at_utc": lane.clock()},
            {"exclude_match_ids": ["x", "x"]},
        ):
            calls = previous.no_database_calls(monkeypatch)
            assert invoke(lane, command, {**requests[command], **extra}, capsys)[0] == 2
            assert not calls


def test_current_rights_gate_before_new_provider_reads(
    synthetic_writer, capsys, monkeypatch
):
    lane = synthetic_writer
    reads = []
    original = cli.LocalTrainingEvidence.read

    def read(self, reference, expected_sha256=None):
        if reference == "shared-native.json":
            reads.append(reference)
        return original(self, reference, expected_sha256)

    monkeypatch.setattr(cli.LocalTrainingEvidence, "read", read)
    monkeypatch.setattr(
        cli, "utc_now", lambda: lane.rights.expires_at_utc + timedelta(seconds=1)
    )
    for command in ("capture", "observed-prepare", "observed-admit"):
        request = dict(lane.cli_requests[command])
        if "request_key" in request:
            request["request_key"] = "expired-new"
        code, error = invoke(lane, command, request, capsys)
        assert code == 1 and error["code"] == "OPERATION_REJECTED"
        assert not reads


def successor(lane, capsys):
    # Fixture capture/review helpers are external to the CLI; no automatic signing.
    item = obs.capture(lane, raw=obs.raw_fixture(goals=0)).model_copy(
        update={
            "home_team_alias_id": "sm-home",
            "away_team_alias_id": "sm-away",
        }
    )
    preview = public(
        lane,
        "observed-prepare",
        tagged(
            "observed-prepare",
            dict(
                scope_id=lane.scope.scope_id,
                snapshots=(item,),
            ),
        ),
        capsys,
    )
    subjects = tuple(
        ObservedSnapshotSubjectV1.model_validate(s) for s in preview["result"]
    )
    return tagged(
        "observed-admit",
        dict(
            request_key="cli-successor",
            scope_id=lane.scope.scope_id,
            submissions=tuple(
                ObservedSnapshotSubmissionV1(
                    subject=s, reviewer_attestation=obs.review(lane, s)
                )
                for s in subjects
            ),
        ),
    )


def test_atomic_write_output_collision_postcommit_file_and_exact_retry(
    synthetic_writer, capsys, monkeypatch
):
    lane = synthetic_writer
    request = successor(lane, capsys)
    before = obs.counts(lane)
    existing = previous.write_request(
        lane.root, b"private existing output", "collision.json"
    )
    with monkeypatch.context() as scoped:
        calls = previous.no_database_calls(scoped)
        code, error = invoke(lane, "observed-admit", request, capsys, output=existing)
        assert code == 2 and error["persistence"] == "NO_DATABASE_OPENED"
        code, error = previous.invoke(lane, "observed-admit", request, capsys)
        assert code == 2 and error["persistence"] == "NO_DATABASE_OPENED"
        assert not calls
    assert existing.read_bytes() == b"private existing output"
    assert obs.counts(lane) == before

    def fail_after_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith(
            (
                "INSERT INTO observed_snapshot_admissions",
                "INSERT INTO observed_collection_scopes",
            )
        ):
            raise OSError(previous.SECRET)

    event.listen(Engine, "after_cursor_execute", fail_after_insert)
    try:
        for command, candidate in (
            ("observed-admit", request),
            (
                "observed-scope-record",
                dict(
                    lane.cli_requests["observed-scope-record"],
                    request_key="scope-atomic",
                ),
            ),
        ):
            code, error = invoke(lane, command, candidate, capsys)
            assert code == 1 and error["persistence"] == "ATOMIC_NO_NEW_ROWS"
            assert obs.counts(lane) == before
    finally:
        event.remove(Engine, "after_cursor_execute", fail_after_insert)
    original_write = cli.write_local_json
    path = lane.root / "published-before-error.json"

    def fail_after_publication(*args):
        original_write(*args)
        raise OSError(previous.SECRET)

    with monkeypatch.context() as scoped:
        scoped.setattr(cli, "write_local_json", fail_after_publication)
        code, error = invoke(lane, "observed-admit", request, capsys, output=path)
    assert code == 1 and error["persistence"] == "MAY_HAVE_PERSISTED"
    assert (
        error["code"] == "OUTPUT_NOT_WRITTEN" and "file may exist" in error["message"]
    )
    assert path.is_file()
    stored = json.loads(path.read_bytes())
    assert public(lane, "observed-admit", request, capsys) == stored
    assert obs.counts(lane)["observed_snapshot_admissions"] == 2
    assert obs.counts(lane)["match_results"] == 7
    admitted = ObservedSnapshotAdmissionV1.model_validate(stored["result"])
    head = admitted.records[0].subject
    prior = lane.base.records[0].subject
    assert head.result_capture.capture_ordinal > prior.result_capture.capture_ordinal
    assert head.result_capture.outcome_sha256 == head.inspection.outcome_sha256
    assert head.result_capture.outcome_sha256 != prior.result_capture.outcome_sha256
    assert (
        public(
            lane,
            "observed-inspect",
            tagged("observed-inspect", {"admission_id": lane.base.admission_id}),
            capsys,
            ObservedSnapshotAdmissionV1,
        )
        == lane.base
    )
    changed = dict(
        request,
        submissions=tuple(
            ObservedSnapshotSubmissionV1(
                subject=s.subject, reviewer_attestation=obs.review(lane, s.subject)
            )
            for s in request["submissions"]
        ),
    )
    assert (
        invoke(lane, "observed-admit", changed, capsys)[1]["persistence"]
        == "ATOMIC_NO_NEW_ROWS"
    )
    old = context(
        lane, capsys, cutoff=lane.base.registered_at_utc + timedelta(microseconds=1)
    )
    current = context(lane, capsys)
    assert old["selected_heads"][0]["version_id"] == lane.base.records[0].version_id
    assert (
        current["selected_heads"][0]["version_id"]
        == stored["result"]["records"][0]["version_id"]
    )


def test_observed_exact_schema_and_no_untagged_plan_or_mixed_manifest(
    synthetic_writer, capsys, monkeypatch
):
    lane = synthetic_writer
    result = pilot(lane, capsys, monkeypatch)
    assert isinstance(
        cli.PilotPlanRequest.model_validate({"definition": result.definition}).root,
        cli.ObservedPilotPlanRequestV1,
    )
    for name in (
        "CurrentSnapshotCollectionScopeV1",
        "CurrentSnapshotUserTermsResolutionV1",
        "ObservedSnapshotSubjectV1",
        "ObservedSnapshotContextV1",
        "ObservedQuantIntegrityPlanDefinitionV1",
        "ObservedQuantIntegrityBuildRecipeV1",
    ):
        assert main(["production-quant", "--print-schema", "--type", name]) == 0
        assert (
            json.loads(capsys.readouterr().out)
            == cli.SCHEMA_TYPES[name].model_json_schema()
        )
    for bad in ("definition-version", "context-version", "historical-context"):
        raw = result.definition.model_dump(mode="json")
        if bad == "definition-version":
            raw.pop("schema_version")
        elif bad == "context-version":
            raw["observed_context"].pop("schema_version")
        else:
            raw["correction_context"] = {
                "schema_version": "TRAINING_HISTORY_CONTEXT_PIN_V2"
            }
        with pytest.raises(ValidationError):
            cli.PilotPlanRequest.model_validate({"definition": raw})
    request = manifest_request(lane, result)
    assert isinstance(
        cli.ManifestRequest.model_validate(request).root, cli.ObservedManifestRequestV1
    )
    for changes in (
        {"schema_version": "PRODUCTION_QUANT_MANIFEST_REQUEST_V2"},
        {"correction_context": {}},
        {"context_registered_at_utc": lane.clock()},
        {"exclude_match_ids": ("match", "match")},
    ):
        with pytest.raises(ValidationError):
            cli.ManifestRequest.model_validate({**request, **changes})
    untagged = dict(request)
    untagged.pop("schema_version")
    with pytest.raises(ValidationError):
        cli.ManifestRequest.model_validate(untagged)
    report = result.report.content_payload
    assert report.strict_walk_forward.model_dump() == {
        "status": "UNAVAILABLE",
        "reason": "UNPROVEN_HISTORICAL_VERSION_TIME",
        "metrics": None,
    }
    assert (
        report.probability_metrics
        is report.historical_model_availability
        is report.availability_denominator
        is report.calibration_observation_count
        is None
    )
    assert report.capture_receipt_count == 1 and report.training_fact_count == 6
    assert report.provenance.assessment_kind == "OBSERVED_COHORT_STRUCTURAL_REPLAY"
    assert report.provenance.evidence_use == "SYNTHETIC_CONTRACT_ONLY"
    assert report.provenance.production_authorized is False
    for kind, artifact in (
        ("pilot-plan", result.plan),
        ("pilot-report", result.report),
    ):
        response = public(
            lane,
            "inspect",
            {
                "target": {
                    "kind": kind,
                    "reference": cli.IntegrityArtifactRefV1.of(artifact),
                }
            },
            capsys,
        )
        assert response["result"] == artifact.model_dump(mode="json")
    code, error = invoke(lane, "manifest", request, capsys)
    assert code == 1 and error["code"] == "OPERATION_REJECTED"
    history = workflow.prepare_observed_training_history(
        admissions=(lane.base,),
        observed_context=result.definition.observed_context,
        source_rights_admission=lane.recorded,
        training_window=lane.window,
        integrity_pilot_scope_id=result.definition.integrity_pilot_scope_id,
        selection_cutoff_at_utc=result.definition.terminal_projection.training_cutoff_at_utc,
        exclude_match_ids=("match",),
    )
    with pytest.raises(ValueError, match="SYNTHETIC_CONTRACT_ONLY"):
        lane.pilot.technical_evidence(result.terminal.artifact_id, history)
    # The old test bridge is now adversarial input, never positive authority.
    original_factory = cli.SqlAlchemyProductionQuantRepository
    adversarial_echo = workflow.ContractTestOnlyTechnicalBridge(lane.pilot)

    def echo_claim(*args, **values):
        values["pilot_repository"] = adversarial_echo
        return original_factory(*args, **values)

    with monkeypatch.context() as scoped:
        scoped.setattr(cli, "SqlAlchemyProductionQuantRepository", echo_claim)
        code, error = invoke(lane, "manifest", request, capsys)
    assert code == 1 and error["code"] == "OPERATION_REJECTED"
    assert adversarial_echo.values == {}
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM training_history_manifests"))
            == 0
        )


def manifest_request(lane, result):
    return dict(
        schema_version="PRODUCTION_QUANT_OBSERVED_MANIFEST_REQUEST_V1",
        request_key="observed-cli-manifest",
        admission_ids=(lane.base.admission_id,),
        training_window=lane.window,
        integrity_pilot_scope_id=result.definition.integrity_pilot_scope_id,
        attestation_id=result.terminal.artifact_id,
        observed_context=result.definition.observed_context,
        selection_cutoff_at_utc=result.definition.terminal_projection.training_cutoff_at_utc,
        exclude_match_ids=("match",),
    )


def test_public_replay_manifest_approval_v2_release_and_existing_live_audit_flow(
    synthetic_real_source_contract, capsys, monkeypatch
):
    lane, result = synthetic_real_source_contract
    assert result.report.content_payload.provenance.evidence_use == "REAL_SOURCE"
    assert result.report.content_payload.provenance.production_authorized is False
    with lane.sessions() as session:
        for model, artifact in (
            (QuantIntegrityPlanRecord, result.plan),
            (QuantIntegrityReportRecord, result.report),
            (QuantIntegrityAttestationRecord, result.terminal),
        ):
            row = session.get(model, artifact.artifact_id)
            assert row is not None
            assert row.artifact_json == canonical_json(artifact)
    _, concrete_pilot, production_repo, inference_repo, _ = (
        cli.production_inference_context(
            lane.sessions,
            evidence=lane.repo.evidence,
            operator_id="operator",
            clock=lane.clock,
        )
    )
    assert type(concrete_pilot) is workflow.SqlAlchemyQuantIntegrityRepository
    assert type(production_repo) is workflow.SqlAlchemyProductionQuantRepository
    assert production_repo.pilot_repository is concrete_pilot
    production = SimpleNamespace(
        lane=lane,
        repo=production_repo,
    )
    manifest = public(
        lane,
        "manifest",
        manifest_request(lane, result),
        capsys,
        TrainingHistoryManifestV1,
    )
    assert (
        public(
            lane,
            "manifest",
            manifest_request(lane, result),
            capsys,
            TrainingHistoryManifestV1,
        )
        == manifest
    )
    assert (
        manifest.content_payload.history.observed_context
        == result.definition.observed_context
    )
    for name, artifact in (
        ("plan", result.plan),
        ("report", result.report),
        ("attestation", result.terminal),
    ):
        reference = getattr(manifest.content_payload.technical_evidence, name)
        assert (reference.artifact_id, reference.content_hash) == (
            artifact.artifact_id,
            artifact.content_hash,
        )
    updates = {
        kind: dict(
            expires_at_utc=workflow.END,
            **(
                {"retention": workflow.horizon()}
                if kind
                in (
                    ProductionGrantKind.DERIVED_MODEL_STATE_RETENTION,
                    ProductionGrantKind.AUDIT_HASH_RETENTION,
                )
                else {}
            ),
        )
        for kind in ProductionGrantKind
    }
    payload = public(
        lane,
        "approval-prepare",
        approvals.prepare_values(production, manifest, updates=updates),
        capsys,
        TrainingHistoryApprovalPayloadV2,
    )
    review = approvals.review_v2(production, payload)
    request = cli.ApprovalRecordRequestV2(
        request_key="observed-cli-approval",
        approval_payload=payload,
        reviewer_attestation=review,
    )
    approval = public(
        lane, "approval-record", request, capsys, TrainingHistoryApprovalV2
    )
    assert (
        public(lane, "approval-record", request, capsys, TrainingHistoryApprovalV2)
        == approval
    )
    assert approval.content_payload.reviewer_attestation == review
    release = public(
        lane,
        "release-build",
        dict(
            request_key="observed-cli-release",
            approval_id=approval.artifact_id,
            training_cutoff_at_utc=result.definition.terminal_projection.training_cutoff_at_utc,
            state_retention_horizon=workflow.horizon(),
            audit_retention_horizon=workflow.horizon(),
        ),
        capsys,
        ProductionQuantModelReleaseV1,
    )
    assert production.repo.load_release(release.artifact_id) == release
    for boundary in (
        release.content_payload.build_start_authorization,
        release.content_payload.build_completion_authorization,
    ):
        current = boundary.content_payload.current
        observed = current.observed_context
        assert observed.schema_version == "OBSERVED_AUTHORIZATION_CONTEXT_V2"
        assert observed.admission_prefix.admission.artifact_id == lane.base.admission_id
        assert (
            observed.admission_prefix.admission.content_hash == lane.base.content_hash
        )
        assert (
            observed.admission_prefix.admission_high_watermark
            == lane.base.admission_sequence
        )
        assert observed.actual_at_utc == current.actual_at_utc
        assert observed.actual_at_utc in lane.clock.calls
    production.release, production.manifest = release, manifest
    sources = live.SqlAlchemyLiveSourceRepository(
        lane.sessions, clock=lambda: live.PERSISTED
    )
    sources.save_market_odds_ingestion(live._market_capture())
    sources.save_sporttery_ingestion(live._sporttery_capture())
    lane.clock.value = live.PERSISTED
    inference = SimpleNamespace(
        label="CONTRACT_TEST_ONLY_NOT_REAL_ACTIVATION",
        lane=lane,
        production=production,
        release=release,
        live=sources,
        repository=inference_repo,
    )
    monkeypatch.setattr(
        production.repo,
        "seal_target_plan",
        lambda *args: public(
            lane,
            "target-plan",
            dict(zip(cli.TargetPlanRequestV1.model_fields, args, strict=True)),
            capsys,
            ProductionTargetAcceptancePlanV1,
        ),
    )
    inference.plan, inference.bundle = live.plan_and_bundle(
        inference, live.DECISION, "observed-cli-targets"
    )
    # Exercise default live repository wiring, not the legacy bridge fixture.
    lane.clock.value = live.CREATED
    monkeypatch.setattr(live_cli.entry, "utc_now", lane.clock)
    monkeypatch.setattr(live.model_analysis, "utc_now", lane.clock)
    monkeypatch.setattr(audit.review_bridge, "utc_now", lane.clock)
    monkeypatch.setattr(audit.post_review, "utc_now", lane.clock)
    assert main(live_cli.arguments(inference)) == 0
    captured = capsys.readouterr()
    assert "APPROVED_TRAINING_HISTORY" in captured.out
    assert previous.SECRET not in captured.out + captured.err
    binding = inference_repo.load_binding("production-cli-analysis")
    for current in (binding.start_authorization, binding.completion_authorization):
        assert current.observed_context.admission_prefix == (
            release.content_payload.build_completion_authorization.content_payload.current.observed_context.admission_prefix
        )
        assert current.observed_context.actual_at_utc == current.actual_at_utc
    bundle_path = lane.root / "private-observed-audit-bundle"
    public(
        lane,
        "bundle-export",
        cli.BundleExportRequestV1(
            analysis_run_id="production-cli-analysis", bundle_directory=bundle_path
        ),
        capsys,
    )
    bundle = read_production_audit_bundle(bundle_path)
    assert bundle.packet.schema_version == "ANALYSIS_PACKET_V3"
    assert bundle.audit.content_payload.approval == approval.reference()
    assert (
        bundle.audit.content_payload.model_training_evidence_basis
        == "CURRENT_SNAPSHOT_OBSERVED"
    )
    assert "model_training_source_mode" not in bundle.audit.content_payload.model_dump()
    review_path = previous.write_request(
        lane.root, audit.review_bytes(bundle.packet), "external-llm-review.json"
    )
    imported = public(
        lane,
        "review-import",
        cli.ReviewImportRequestV1(
            bundle_directory=bundle_path, review_file=review_path
        ),
        capsys,
    )
    config = previous.write_request(
        lane.root, b'[runtime]\nenvironment = "live"\n', "observed-live.toml"
    )
    fusion = public(
        lane,
        "fusion-create",
        cli.FusionCreateRequestV1(
            review_artifact_id=imported["result"]["review_artifact_id"],
            config_file=config,
        ),
        capsys,
    )
    revision = public(
        lane,
        "portfolio-revise",
        cli.PortfolioReviseRequestV1(
            fusion_run_id=fusion["result"]["fusion_run_id"], config_file=config
        ),
        capsys,
    )
    assert revision["result"]["portfolio_revision_id"]
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM training_history_approval_events")
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM training_history_approval_grants")
            )
            == 4
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert live.model_counts(inference)["analysis_runs"] == 1
