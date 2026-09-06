"""Local CLI contract tests only. No real rights, source data or production approval.

Admission tests use actual temporary evidence and SQLite repositories. The pilot
test reuses an explicitly SYNTHETIC_CONTRACT_ONLY test port, never a real pilot.
Downstream tests reuse controlled seeded approval fixtures, never a public writer
or official production activation. All review files remain in pytest temp storage.
"""

import hashlib
import json
import socket
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id, utc_now
from football_system.domain.production_release import RetentionHorizonV1
from football_system.infrastructure.database.quant_integrity_repository import (
    SqlAlchemyQuantIntegrityRepository,
)
from football_system.interfaces import production_quant_cli as cli
from football_system.interfaces.cli import main
from tests.integration import test_production_audit as audit_tests
from tests.integration import test_production_inference as inference_tests
from tests.integration import test_quant_integrity_persistence as pilot_tests
from tests.integration import test_training_admission_persistence as admission_tests

ROOT = Path(__file__).resolve().parents[2]
SECRET = "synthetic-production-cli-secret"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("production-quant must not connect to the network")

    # Windows asyncio uses a loopback socketpair internally, not a provider request.
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.UrllibTransport.send",
        forbidden,
    )
    monkeypatch.setenv("SPORTMONKS_API_TOKEN", SECRET)
    monkeypatch.setenv("THE_ODDS_API_KEY", SECRET)


@pytest.fixture
def lane(tmp_path):
    cli.upgrade_database(
        f"sqlite:///{(tmp_path / 'lane.db').as_posix()}", ROOT / "alembic.ini"
    )
    yield from admission_tests.lane.__wrapped__(tmp_path)


inference = inference_tests.inference


@pytest.fixture
def audited_cli(inference, monkeypatch):
    context = audit_tests.audited.__wrapped__(inference, monkeypatch)
    monkeypatch.setattr(cli, "utc_now", context.lane.clock)
    # Reuse the established synthetic pilot bridge; all downstream repositories/gates are real.
    monkeypatch.setattr(
        cli,
        "SqlAlchemyQuantIntegrityRepository",
        lambda *args, **kwargs: context.inference.production.bridge,
    )
    return context


def write_request(root, value, name="request.json"):
    path = root / name
    path.write_bytes(
        value if isinstance(value, bytes) else canonical_json(value).encode()
    )
    return path


def options(context, request, database="lane.db"):
    pins = write_request(
        context.root,
        cli.AuthorityPinsV1(
            trusted_authorities=context.repo.evidence.trusted_authorities
        ),
        "pins.json",
    )
    return [
        "--request",
        str(request),
        "--database-url",
        f"sqlite:///{(context.root / database).as_posix()}",
        "--evidence-root",
        str(context.root),
        "--authority-pins",
        str(pins),
        "--operator",
        "operator",
    ]


def invoke(context, command, request, capsys, *, extra=(), database="lane.db"):
    path = write_request(context.root, request)
    result = main(
        ["production-quant", command, *options(context, path, database), *extra]
    )
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    output = captured.out if result == 0 else captured.err.splitlines()[-1]
    document = json.loads(output)
    assert output.rstrip("\n") == canonical_json(document)
    return result, document


def no_database_calls(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("database touched before input preflight")

    for name in ("upgrade_database", "create_database_engine", "create_engine"):
        monkeypatch.setattr(cli, name, forbidden)
    return calls


def rights_request(lane):
    return cli.RightsRecordRequestV1(
        request_key="cli-rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )


def test_help_and_all_exact_schemas_are_side_effect_free(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    calls = no_database_calls(monkeypatch)
    monkeypatch.setattr(
        cli, "_read_json", lambda *args: pytest.fail("schema read a file")
    )
    assert main(["production-quant", "--help"]) == 0
    help_text = capsys.readouterr().out
    for command in cli.COMMAND_MODELS:
        assert command in help_text
        with pytest.raises(SystemExit) as exited:
            main(["production-quant", command, "--help"])
        assert exited.value.code == 0
        assert "--authority-pins" in capsys.readouterr().out
        assert main(["production-quant", command, "--print-schema"]) == 0
        schema = json.loads(capsys.readouterr().out)
        assert schema == cli.COMMAND_MODELS[command].model_json_schema()
        assert schema["additionalProperties"] is False
    assert main(["production-quant", "--print-schema"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["blocked_commands"] == ["approval-record"]
    for name, model in cli.SCHEMA_TYPES.items():
        assert main(["production-quant", "--print-schema", "--type", name]) == 0
        assert json.loads(capsys.readouterr().out) == model.model_json_schema()
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", tuple(cli.COMMAND_MODELS))
def test_commands_require_explicit_local_context(
    command, tmp_path, capsys, monkeypatch
):
    calls = no_database_calls(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert main(["production-quant", command]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "INVALID_LOCAL_INPUT"
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "arguments",
    [
        [SECRET],
        ["inspect", "--unknown", SECRET],
        ["--print-schema", "--type", SECRET],
    ],
)
def test_argument_errors_never_echo_values(arguments, capsys):
    assert main(["production-quant", *arguments]) == 2
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert json.loads(captured.err)["code"] == "INVALID_LOCAL_INPUT"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\xff",
        b"{} {}",
        b"[]",
        b'{"request_key":"one","request_key":"two"}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b"[" * 1200 + b"]" * 1200,
        ('{"' + SECRET + '":"do not echo this unknown field"}').encode(),
    ],
)
def test_strict_request_rejected_before_database(lane, capsys, monkeypatch, payload):
    calls = no_database_calls(monkeypatch)
    result, error = invoke(lane, "capture", payload, capsys, database="absent.db")
    assert result == 2
    assert error["code"] == "INVALID_LOCAL_INPUT"
    assert calls == []
    assert not (lane.root / "absent.db").exists()


def test_request_and_pin_byte_limits_precede_database(lane, capsys, monkeypatch):
    calls = no_database_calls(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(cli, "MAX_REQUEST_BYTES", 64)
        assert invoke(lane, "rights-record", rights_request(lane), capsys)[0] == 2
    with monkeypatch.context() as patch:
        patch.setattr(cli, "MAX_PINS_BYTES", 16)
        assert invoke(lane, "rights-record", rights_request(lane), capsys)[0] == 2
    assert calls == []


@pytest.mark.parametrize(
    "bad_file", ["authority.json", "rights-review.json", "terms.txt"]
)
def test_rights_evidence_preflight_precedes_database(
    lane, capsys, monkeypatch, bad_file
):
    calls = no_database_calls(monkeypatch)
    (lane.root / bad_file).write_bytes(SECRET.encode())
    result, error = invoke(lane, "rights-record", rights_request(lane), capsys)
    assert result == 2
    assert error["code"] == "INVALID_LOCAL_INPUT"
    assert calls == []


def test_unpinned_authority_and_non_strict_pins_are_rejected(lane, capsys, monkeypatch):
    path = write_request(lane.root, rights_request(lane))
    arguments = options(lane, path)
    calls = no_database_calls(monkeypatch)
    for payload in (
        b'{"trusted_authorities":{},"trusted_authorities":{}}',
        canonical_json({"trusted_authorities": {"authority.json": "0" * 64}}).encode(),
        canonical_json(
            {"trusted_authorities": {"../authority.json": "0" * 64}}
        ).encode(),
    ):
        (lane.root / "pins.json").write_bytes(payload)
        assert main(["production-quant", "rights-record", *arguments]) == 2
        assert SECRET not in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize(
    "reference",
    ["../raw.json", "raw\\data.json", "/raw.json", "C:raw.json", "a//raw.json"],
)
def test_capture_path_syntax_preflight_never_reads_provider_bytes(
    lane, capsys, monkeypatch, reference
):
    request = dict(
        request_key="capture",
        source_rights_admission_id="not-yet-recorded",
        source_id="source",
        provider_code="PROVIDER",
        evidence_reference=reference,
    )
    reads = []
    monkeypatch.setattr(
        cli.LocalTrainingEvidence, "read", lambda *args: reads.append(args)
    )
    calls = no_database_calls(monkeypatch)
    assert invoke(lane, "capture", request, capsys)[0] == 2
    assert reads == []
    assert calls == []


def test_capture_rights_gate_precedes_provider_reads_including_expired_retry(
    lane, capsys, monkeypatch
):
    recorded = lane.repo.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    admission_tests.seed_identities(lane)
    write_request(lane.root, {"private": SECRET}, "raw.json")
    write_request(lane.root, {"private": SECRET}, "other.json")
    monkeypatch.setattr(cli, "utc_now", lane.clock)
    events = []
    original_read = cli.LocalTrainingEvidence.read
    original_rights = cli.SqlAlchemyTrainingAdmissionRepository._rights

    def verified_rights(self, session, admission_id):
        value = original_rights(self, session, admission_id)
        events.append("verified-rights")
        return value

    def read(self, reference, expected_sha256=None):
        if reference in {"raw.json", "other.json"}:
            assert "verified-rights" in events
            events.append(reference)
        return original_read(self, reference, expected_sha256)

    monkeypatch.setattr(cli.LocalTrainingEvidence, "read", read)
    monkeypatch.setattr(
        cli.SqlAlchemyTrainingAdmissionRepository, "_rights", verified_rights
    )
    request = cli.CaptureRequestV1(
        request_key="capture",
        source_rights_admission_id=recorded.source_rights_admission_id,
        source_id="source",
        provider_code="PROVIDER",
        evidence_reference="raw.json",
    )
    for update in (
        {"source_rights_admission_id": "missing-rights"},
        {"source_id": "outside-scope"},
    ):
        events.clear()
        assert (
            invoke(lane, "capture", request.model_copy(update=update), capsys)[0] == 1
        )
        assert not {"raw.json", "other.json"}.intersection(events)
    events.clear()
    result, receipt = invoke(lane, "capture", request, capsys)
    assert result == 0 and "raw.json" in events
    lane.clock.value = lane.rights.expires_at_utc + timedelta(seconds=1)
    for update in (
        {"request_key": "new-after-expiry"},
        {"evidence_reference": "other.json"},
        {"source_id": "outside-scope"},
    ):
        events.clear()
        assert (
            invoke(lane, "capture", request.model_copy(update=update), capsys)[0] == 1
        )
        assert not {"raw.json", "other.json"}.intersection(events)
    events.clear()
    assert invoke(lane, "capture", request, capsys)[1] == receipt
    assert "raw.json" in events and "other.json" not in events
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM training_capture_receipts"))
            == 1
        )


@pytest.mark.parametrize(
    "payload", [b'{"secret":NaN}', b'{"secret":1,"secret":2}', b" " * 8193]
)
def test_capture_raw_is_parsed_and_bounded_only_after_rights(
    lane, capsys, monkeypatch, payload
):
    recorded = lane.repo.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    admission_tests.seed_identities(lane)
    (lane.root / "raw.json").write_bytes(payload)
    monkeypatch.setattr(cli, "utc_now", lane.clock)
    monkeypatch.setattr(cli, "MAX_EVIDENCE_BYTES", 4096)
    result, error = invoke(
        lane,
        "capture",
        dict(
            request_key="invalid-capture",
            source_rights_admission_id=recorded.source_rights_admission_id,
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference="raw.json",
        ),
        capsys,
    )
    assert result == 1 and error["code"] == "OPERATION_REJECTED"
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM training_capture_receipts"))
            == 0
        )


def test_inference_context_resolves_current_clock_and_honors_explicit_clock(
    lane, monkeypatch
):
    monkeypatch.setattr(cli, "utc_now", lane.clock)
    for explicit in (None, admission_tests.Clock()):
        repositories = cli.production_inference_context(
            lane.sessions,
            evidence=lane.repo.evidence,
            operator_id="operator",
            clock=explicit,
        )
        assert isinstance(repositories[1], SqlAlchemyQuantIntegrityRepository)
        assert all(
            repository._clock is (lane.clock if explicit is None else explicit)
            for repository in repositories
        )
        assert repositories[4]._inference is repositories[3]


def test_request_models_revalidate_nested_seals_and_constructed_ids(lane):
    invalid = rights_request(lane).model_copy(
        update={
            "reviewer_attestation": lane.attestation.model_copy(
                update={"attestation_hash": "0" * 64}
            ),
        }
    )
    with pytest.raises(ValidationError):
        cli.RightsRecordRequestV1.model_validate(invalid)
    request = cli.CaptureRequestV1.model_construct(
        request_key=" whitespace ",
        source_rights_admission_id="rights",
        source_id="source",
        provider_code="PROVIDER",
        evidence_reference="raw.json",
    )
    with pytest.raises(ValidationError):
        cli.CaptureRequestV1.model_validate(request)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///:memory:",
        "sqlite:///file:private.db?mode=rw&uri=true",
        "postgresql://operator:synthetic-production-cli-secret@localhost/private",
        "sqlite:///private.db?mode=ro",
        "sqlite://",
    ],
)
def test_nonlocal_or_ephemeral_database_urls_are_rejected_before_io(
    lane, capsys, monkeypatch, url
):
    path = write_request(lane.root, rights_request(lane))
    arguments = options(lane, path)
    arguments[arguments.index("--database-url") + 1] = url
    calls = no_database_calls(monkeypatch)
    assert main(["production-quant", "rights-record", *arguments]) == 2
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert calls == []


def test_output_cannot_alias_a_new_database(lane, capsys, monkeypatch):
    calls = no_database_calls(monkeypatch)
    result, _ = invoke(
        lane,
        "rights-record",
        rights_request(lane),
        capsys,
        database="new.db",
        extra=("--output", str(lane.root / "new.db")),
    )
    assert result == 2
    assert calls == []
    assert not (lane.root / "new.db").exists()


def test_valid_write_can_migrate_a_fresh_database(lane, capsys):
    result, recorded = invoke(
        lane, "rights-record", rights_request(lane), capsys, database="fresh.db"
    )
    assert result == 0 and recorded["status"] == "RECORDED"
    with sqlite3.connect(lane.root / "fresh.db") as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "c2ebf618d354"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_rights_admissions"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM training_history_approval_events"
            ).fetchone()[0]
            == 0
        )


def test_rights_capture_retry_and_read_only_inspect_never_export_raw(
    lane, capsys, monkeypatch
):
    result, recorded = invoke(lane, "rights-record", rights_request(lane), capsys)
    assert result == 0
    assert invoke(lane, "rights-record", rights_request(lane), capsys)[1] == recorded
    admission_tests.seed_identities(lane)
    raw = {"unmapped_private_payload": SECRET, "large_audit_field": "x" * (1024 * 1024)}
    write_request(lane.root, raw, "raw.json")
    request = cli.CaptureRequestV1(
        request_key="cli-capture",
        source_rights_admission_id=recorded["result"]["source_rights_admission_id"],
        source_id="source",
        provider_code="PROVIDER",
        evidence_reference="raw.json",
    )
    result, capture = invoke(lane, "capture", request, capsys)
    assert result == 0
    assert capture["result"]["upstream_acquired_at_utc"] is None
    assert capture["result"]["capture_kind"] == "LOCAL_FILE_IMPORT"
    assert invoke(lane, "capture", request, capsys)[1] == capture
    assert (
        invoke(
            lane,
            "capture",
            request.model_copy(update={"provider_code": "OTHER"}),
            capsys,
        )[0]
        == 1
    )
    original_execute = cli._execute

    def assert_read_only(command, request, admissions, pilots, production, **kwargs):
        with admissions._sessions.kw["bind"].connect() as connection:
            with pytest.raises(OperationalError, match="readonly"):
                connection.execute(text("CREATE TABLE must_not_write (id INTEGER)"))
        return original_execute(
            command, request, admissions, pilots, production, **kwargs
        )

    monkeypatch.setattr(cli, "_execute", assert_read_only)
    monkeypatch.setattr(
        cli, "upgrade_database", lambda *args: pytest.fail("read migrated")
    )
    before = (lane.root / "lane.db").read_bytes()
    result, inspected = invoke(
        lane,
        "inspect",
        {
            "target": {
                "kind": "capture",
                "artifact_id": capture["result"]["capture_receipt_id"],
            },
        },
        capsys,
    )
    assert result == 0
    assert inspected["result"] == capture["result"]
    assert "unmapped_private_payload" not in canonical_json(inspected)
    assert (lane.root / "lane.db").read_bytes() == before
    (lane.root / "raw.json").write_bytes(SECRET.encode())
    result, error = invoke(
        lane,
        "inspect",
        {
            "target": {
                "kind": "capture",
                "artifact_id": capture["result"]["capture_receipt_id"],
            },
        },
        capsys,
    )
    assert result == 1
    assert error["code"] == "OPERATION_REJECTED"


def test_admit_round_trip_atomic_output_and_no_overwrite(lane, capsys, monkeypatch):
    admission_tests.prepare(lane)
    request = cli.AdmitRequestV1(
        request_key="cli-admit",
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        submissions=lane.submissions,
    )
    output = lane.root / "admission-output.json"
    result, receipt = invoke(
        lane, "admit", request, capsys, extra=("--output", str(output))
    )
    assert result == 0
    payload = output.read_bytes()
    assert receipt["output_sha256"] == hashlib.sha256(payload).hexdigest()
    artifact = json.loads(payload)["result"]
    assert artifact["content_payload"]["admitted_fact_count"] == 2
    assert artifact["content_payload"]["retrospective"] is True
    result, inspected = invoke(
        lane,
        "inspect",
        {
            "target": {
                "kind": "admission",
                "artifact_id": artifact["training_fact_admission_id"],
            },
        },
        capsys,
    )
    assert result == 0 and inspected["result"] == artifact
    assert invoke(lane, "admit", request, capsys)[1]["result"] == artifact
    calls = no_database_calls(monkeypatch)
    assert (
        invoke(lane, "admit", request, capsys, extra=("--output", str(output)))[0] == 2
    )
    assert output.read_bytes() == payload
    assert calls == []


def test_admit_adapter_is_preflighted_before_database(lane, capsys, monkeypatch):
    admission_tests.prepare(lane)
    (lane.root / "adapter.json").write_bytes(b'{"duplicate":1,"duplicate":2}')
    calls = no_database_calls(monkeypatch)
    assert (
        invoke(
            lane,
            "admit",
            {
                "request_key": "cli-admit",
                "source_rights_admission_id": lane.recorded.source_rights_admission_id,
                "submissions": lane.submissions,
            },
            capsys,
        )[0]
        == 2
    )
    assert calls == []


@pytest.mark.parametrize("command", ["inspect", "revoke-prepare"])
def test_reads_never_create_or_migrate_old_databases(
    lane, capsys, monkeypatch, command
):
    monkeypatch.setattr(
        cli, "upgrade_database", lambda *args: pytest.fail("read migrated")
    )
    request = (
        {"target": {"kind": "rights", "artifact_id": "absent"}}
        if command == "inspect"
        else {
            "approval_id": "absent",
            "release_id": None,
            "affected_grants": ["PRODUCTION_MODEL_INFERENCE"],
            "actor": "reviewer",
            "reviewer_authority": lane.authority,
            "reason": "withdraw test scope",
        }
    )
    result, error = invoke(lane, command, request, capsys, database="absent.db")
    assert result == 1 and error["code"] == "DATABASE_NOT_READY"
    assert not (lane.root / "absent.db").exists()
    old = lane.root / "old.db"
    with sqlite3.connect(old) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('6e4b1a9c2d73')")
    before = old.read_bytes()
    result, error = invoke(lane, command, request, capsys, database="old.db")
    assert result == 1 and error["code"] == "OPERATION_REJECTED"
    assert old.read_bytes() == before


def test_approval_remains_actionably_blocked_without_opening_database(
    lane, capsys, monkeypatch
):
    calls = no_database_calls(monkeypatch)
    request = {
        "request_key": "blocked-approval",
        "manifest_id": "not-an-approved-manifest",
        "grants": [
            {
                "grant": "PRODUCTION_MODEL_INFERENCE",
                "effective_at_utc": utc_now(),
                "expires_at_utc": None,
                "retention_rule": None,
                "retention": None,
            }
        ],
        "review": lane.attestation.content_payload.evidence,
        "reviewer_authority": lane.authority,
    }
    result, error = invoke(
        lane, "approval-record", request, capsys, database="blocked.db"
    )
    assert result == 3
    assert error["status"] == "BLOCKED"
    assert error["code"] == "APPROVAL_RECORDING_CONTRACT_CONFLICT"
    assert "persisted_at_utc" in error["message"]
    assert "new review/recording envelope" in error["message"]
    assert calls == []
    assert not (lane.root / "blocked.db").exists()


def test_production_commands_do_not_create_missing_prerequisites(lane, capsys):
    horizon = RetentionHorizonV1(indefinite=True, retain_until_at_utc=None)
    now = utc_now()
    requests = {
        "manifest": {
            "request_key": "missing-manifest",
            "admission_ids": [SECRET],
            "training_window": pilot_tests._definition(
                pilot_tests._admission()
            ).training_window,
            "integrity_pilot_scope_id": "unverified-scope",
            "attestation_id": "missing-attestation",
        },
        "release-build": {
            "request_key": "missing-release",
            "approval_id": SECRET,
            "training_cutoff_at_utc": now,
            "state_retention_horizon": horizon,
            "audit_retention_horizon": horizon,
        },
        "target-plan": {
            "request_key": "missing-target",
            "release_id": SECRET,
            "targets": [
                {
                    "match_id": "future",
                    "home_team_id": "home",
                    "away_team_id": "away",
                    "kickoff_at_utc": now + timedelta(days=2),
                }
            ],
            "kickoff_window_start_at_utc": now + timedelta(days=1),
            "kickoff_window_end_at_utc": now + timedelta(days=3),
            "decision_as_of_at_utc": now + timedelta(days=1),
            "selection_rule": "KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1",
        },
        "revoke": {
            "request_key": "missing-revocation",
            "revocation_request": {
                "approval": {"artifact_id": SECRET, "content_hash": "0" * 64},
                "release": None,
                "source_ids": ["source"],
                "affected_grants": ["PRODUCTION_MODEL_INFERENCE"],
                "actor": "reviewer",
                "reviewer_authority": lane.authority,
                "reason": "test withdrawal",
                "effective_at_utc": None,
            },
            "review": lane.attestation.content_payload.evidence,
        },
    }
    for command, request in requests.items():
        result, error = invoke(lane, command, request, capsys)
        assert result == 1 and error["code"] == "OPERATION_REJECTED"
    with lane.engine.connect() as connection:
        for table in (
            "training_history_approval_events",
            "production_quant_model_releases",
            "production_target_acceptance_plans",
            "training_history_revocation_events",
        ):
            assert connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0


def test_atomic_writer_supports_over_1mb_but_enforces_bound_and_no_overwrite(
    tmp_path, monkeypatch
):
    content = canonical_json({"local_contract": "x" * (1024 * 1024 + 1)})
    path = tmp_path / "large.json"
    size, digest = cli.write_local_json(path, content)
    assert size > 1024 * 1024
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        cli.write_local_json(path, content)
    monkeypatch.setattr(cli, "MAX_OUTPUT_BYTES", 16)
    with pytest.raises(ValueError):
        cli.write_local_json(tmp_path / "too-large.json", content)
    assert not (tmp_path / "too-large.json").exists()
    assert list(tmp_path.glob(".quant-*.tmp")) == []


def test_atomic_writer_cleans_up_after_publish_race(tmp_path, monkeypatch):
    path = tmp_path / "race.json"
    original_link = cli.os.link

    def racing_link(source, destination):
        destination.write_bytes(b"existing private output")
        original_link(source, destination)

    monkeypatch.setattr(cli.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        cli.write_local_json(path, canonical_json({"new": True}))
    assert path.read_bytes() == b"existing private output"
    assert list(tmp_path.glob(".quant-*.tmp")) == []


def test_output_failure_warns_about_already_persisted_operation(
    lane, capsys, monkeypatch
):
    def failed_writer(*args):
        raise OSError(SECRET)

    monkeypatch.setattr(cli, "write_local_json", failed_writer)
    result, error = invoke(
        lane,
        "rights-record",
        rights_request(lane),
        capsys,
        extra=("--output", str(lane.root / "unwritten.json")),
    )
    assert result == 1
    assert error["code"] == "OUTPUT_NOT_WRITTEN"
    assert "already be persisted" in error["message"]
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM source_rights_admissions"))
            == 1
        )
    assert not (lane.root / "unwritten.json").exists()


def test_synthetic_pilot_cli_retains_failures_and_terminal_disclosure(
    tmp_path, capsys, monkeypatch
):
    cli.upgrade_database(
        f"sqlite:///{(tmp_path / 'pilot.db').as_posix()}", ROOT / "alembic.ini"
    )
    generator = pilot_tests.pilot.__wrapped__(tmp_path)
    pilot = next(generator)
    try:
        recipe = pilot.root / "recipe.json"
        original = recipe.read_bytes()
        for invalid in (b'{"bad":NaN}', b" " + original):
            recipe.write_bytes(invalid)
            digest = hashlib.sha256(invalid).hexdigest()
            pin = pilot.definition.build_recipe
            definition = pilot.definition.model_copy(
                update={
                    "build_recipe": pin.model_copy(
                        update={
                            "recipe_hash": digest,
                            "evidence": pin.evidence.model_copy(
                                update={"evidence_sha256": digest}
                            ),
                        }
                    ),
                }
            )
            with monkeypatch.context() as patch:
                calls = no_database_calls(patch)
                assert (
                    invoke(
                        pilot,
                        "pilot-plan",
                        {"definition": definition},
                        capsys,
                        database="pilot.db",
                    )[0]
                    == 2
                )
                assert calls == []
        recipe.write_bytes(original)
        # Explicit synthetic admission port, real CLI/service/pilot SQLite transactions.
        monkeypatch.setattr(
            cli,
            "SqlAlchemyTrainingAdmissionRepository",
            lambda *args, **kwargs: pilot.adapter,
        )
        monkeypatch.setattr(cli, "utc_now", pilot.clock)
        result, plan = invoke(
            pilot,
            "pilot-plan",
            {"definition": pilot.definition},
            capsys,
            database="pilot.db",
        )
        assert result == 0
        assert pilot.adapter.result_reads == 0
        assert (
            plan["integrity_pilot_scope_id"]
            == pilot.definition.integrity_pilot_scope_id
        )
        assert (
            plan["result"]["content_payload"]["definition"]["provenance"][
                "evidence_use"
            ]
            == "SYNTHETIC_CONTRACT_ONLY"
        )
        request = {"plan_ref": plan["reference"]}
        pilot.adapter.fail_results = True
        assert invoke(pilot, "pilot-run", request, capsys, database="pilot.db")[0] == 1
        pilot.adapter.fail_results = False
        result, report = invoke(
            pilot, "pilot-run", request, capsys, database="pilot.db"
        )
        assert result == 0
        assert (
            report["result"]["content_payload"]["provenance"]["production_authorized"]
            is False
        )
        result, attempts = invoke(
            pilot,
            "inspect",
            {
                "target": {
                    "kind": "pilot-attempts",
                    "series_id": pilot.definition.integrity_pilot_series_id,
                },
            },
            capsys,
            database="pilot.db",
        )
        assert result == 0
        assert [a["content_payload"]["status"] for a in attempts["result"]] == [
            "FAILED",
            "COMPLETED",
        ]
        result, terminal = invoke(
            pilot, "pilot-attest", request, capsys, database="pilot.db"
        )
        assert result == 0
        assert terminal["result"]["content_payload"]["attempt_count"] == 2
        assert invoke(pilot, "pilot-run", request, capsys, database="pilot.db")[0] == 1
        result, inspected = invoke(
            pilot,
            "inspect",
            {
                "target": {"kind": "pilot-report", "reference": report["reference"]},
            },
            capsys,
            database="pilot.db",
        )
        assert result == 0 and inspected["result"] == report["result"]
        with pytest.raises(ValueError, match="never technical production evidence"):
            pilot.repo.technical_evidence(terminal["result"]["artifact_id"], None)
    finally:
        generator.close()


def test_pilot_reference_model_rejects_a_non_plan_artifact():
    with pytest.raises(ValidationError):
        cli.PilotReferenceRequestV1.model_validate(
            {
                "plan_ref": {
                    "schema_version": "PRODUCTION_QUANT_INTEGRITY_PILOT_REPORT_V1",
                    "content_hash": "0" * 64,
                    "artifact_id": stable_id(
                        "PRODUCTION_QUANT_INTEGRITY_PILOT_REPORT_V1", "0" * 64
                    ),
                }
            }
        )


def test_cli_audited_bundle_review_fusion_portfolio_round_trip(audited_cli, capsys):
    from football_system.infrastructure.files.production_audit_bundle import (
        read_production_audit_bundle,
    )

    context, lane = audited_cli, audited_cli.lane
    bundle_path = lane.root / "cli-bundle"
    request = cli.BundleExportRequestV1(
        analysis_run_id=context.run_id, bundle_directory=bundle_path
    )
    result, exported = invoke(lane, "bundle-export", request, capsys)
    assert result == 0 and exported["result"]["bundle_published"] is True
    bundle = read_production_audit_bundle(bundle_path)
    audit = bundle.audit.content_payload
    assert audit.release == context.inference.release.reference()
    assert audit.target_acceptance_plan == context.inference.plan.reference()
    assert (
        audit.decision_as_of_at_utc
        == context.inference.bundle.preparation.decision_as_of_at_utc
    )
    assert audit.generated_at_utc == bundle.packet.generated_at_utc
    assert audit.model_training_use_class == "APPROVED_TRAINING_HISTORY"
    assert audit.decision_data_mode == "LIVE_STRICT"
    for member in bundle_path.iterdir():
        assert SECRET.encode() not in member.read_bytes()
        assert b"unmapped_audit_field" not in member.read_bytes()
        assert b"Test-only terms" not in member.read_bytes()
    review_path = write_request(
        lane.root, audit_tests.review_bytes(bundle.packet), "cli-review.json"
    )
    config = write_request(
        lane.root, b'[runtime]\nenvironment = "live"\n', "downstream.toml"
    )
    review_request = cli.ReviewImportRequestV1(
        bundle_directory=bundle_path, review_file=review_path
    )
    result, imported = invoke(lane, "review-import", review_request, capsys)
    assert result == 0
    assert imported["result"]["packet_id"] == bundle.packet.packet_id
    fusion_request = cli.FusionCreateRequestV1(
        review_artifact_id=imported["result"]["review_artifact_id"], config_file=config
    )
    result, fusion = invoke(lane, "fusion-create", fusion_request, capsys)
    assert result == 0 and fusion["result"]["parent_analysis_run_id"] == context.run_id
    revision_request = cli.PortfolioReviseRequestV1(
        fusion_run_id=fusion["result"]["fusion_run_id"], config_file=config
    )
    result, revision = invoke(lane, "portfolio-revise", revision_request, capsys)
    assert (
        result == 0
        and revision["result"]["fusion_run_id"] == fusion["result"]["fusion_run_id"]
    )
    before = audit_tests.counts(context)
    for command, replay, expected in (
        ("review-import", review_request, imported),
        ("fusion-create", fusion_request, fusion),
        ("portfolio-revise", revision_request, revision),
    ):
        assert invoke(lane, command, replay, capsys)[1] == expected
    assert invoke(lane, "bundle-export", request, capsys)[0] == 2
    assert read_production_audit_bundle(bundle_path) == bundle
    assert audit_tests.counts(context) == before
    assert context.auditor.gate_run(context.run_id) == bundle.audit


def test_malformed_downstream_files_are_rejected_before_database_and_raw_reads(
    audited_cli, capsys, monkeypatch
):
    from football_system.infrastructure.files.production_audit_bundle import (
        AUDIT_FILENAME,
        MANIFEST_FILENAME,
        PACKET_FILENAME,
        read_production_audit_bundle,
    )

    context, lane = audited_cli, audited_cli.lane
    bundle_path = lane.root / "preflight-bundle"
    assert (
        invoke(
            lane,
            "bundle-export",
            cli.BundleExportRequestV1(
                analysis_run_id=context.run_id, bundle_directory=bundle_path
            ),
            capsys,
        )[0]
        == 0
    )
    bundle = read_production_audit_bundle(bundle_path)
    raw_review = audit_tests.review_bytes(bundle.packet)
    review_path = write_request(lane.root, raw_review, "review.json")
    request = cli.ReviewImportRequestV1(
        bundle_directory=bundle_path, review_file=review_path
    )
    database_bytes = (lane.root / "lane.db").read_bytes()
    counts = audit_tests.counts(context)
    reads = []
    original_read = cli.LocalTrainingEvidence.read

    def no_raw(self, reference, expected_sha256=None):
        if reference in {"fixture.json", "scope.json", "result.json"}:
            reads.append(reference)
            raise AssertionError("malformed downstream input read provider bytes")
        return original_read(self, reference, expected_sha256)

    monkeypatch.setattr(cli.LocalTrainingEvidence, "read", no_raw)
    with monkeypatch.context() as patch:
        calls = no_database_calls(patch)
        for name, invalid in (
            (AUDIT_FILENAME, None),
            (PACKET_FILENAME, b'{"x":1,"x":2}'),
            (MANIFEST_FILENAME, ('{"raw_payload":"' + SECRET + '"}').encode()),
        ):
            member = bundle_path / name
            original = member.read_bytes()
            if invalid is None:
                member.unlink()
            else:
                member.write_bytes(invalid)
            result, error = invoke(
                lane, "review-import", request, capsys, database="absent.db"
            )
            assert result == 2 and error["code"] == "INVALID_LOCAL_INPUT"
            member.write_bytes(original)
        wrong_binding = json.loads(raw_review)
        wrong_binding["packet_hash"] = "0" * 64
        for invalid in (
            b"\xff",
            b'{"x":NaN}',
            b'{"x":1,"x":2}',
            canonical_json(wrong_binding).encode(),
            b" " * (cli.MAX_CONTRACT_FILE_BYTES + 1),
            ('{"private":"' + SECRET + '"}').encode(),
        ):
            review_path.write_bytes(invalid)
            assert (
                invoke(lane, "review-import", request, capsys, database="absent.db")[0]
                == 2
            )
        assert calls == []
    assert reads == []
    assert not (lane.root / "absent.db").exists()
    assert (lane.root / "lane.db").read_bytes() == database_bytes
    assert audit_tests.counts(context) == counts


@pytest.mark.parametrize("command", ["fusion-create", "portfolio-revise"])
def test_downstream_config_preflight_is_bounded_and_sanitized(
    lane, capsys, monkeypatch, command
):
    config = lane.root / "invalid.toml"
    model = (
        cli.FusionCreateRequestV1
        if command == "fusion-create"
        else cli.PortfolioReviseRequestV1
    )
    field = "review_artifact_id" if command == "fusion-create" else "fusion_run_id"
    request = model.model_validate({field: "missing-parent", "config_file": config})
    before = (lane.root / "lane.db").read_bytes()
    calls = no_database_calls(monkeypatch)
    for content in (
        b"[broken",
        b"\xff",
        b"[analysis]\nmin_selection_ev = -1\n",
        ('unknown_private_field = "' + SECRET + '"').encode(),
        b" " * (cli.MAX_CONFIG_BYTES + 1),
    ):
        config.write_bytes(content)
        assert invoke(lane, command, request, capsys, database="absent.db")[0] == 2
    assert calls == []
    assert not (lane.root / "absent.db").exists()
    assert (lane.root / "lane.db").read_bytes() == before


def test_legacy_commands_fail_closed_for_approved_runs_without_auditor(
    audited_cli, capsys
):
    context, lane = audited_cli, audited_cli.lane
    database = f"sqlite:///{(lane.root / 'lane.db').as_posix()}"
    config = write_request(
        lane.root, b'[runtime]\nenvironment = "live"\n', "legacy.toml"
    )
    base = ["--database-url", database, "--config", str(config)]
    legacy_export = [
        "analysis-packet",
        "export",
        *base,
        "--analysis-run-id",
        context.run_id,
        "--schema-version",
        "ANALYSIS_PACKET_V3",
        "--output",
        str(lane.root / "forbidden.json"),
    ]
    with pytest.raises(SystemExit) as error:
        main(legacy_export)
    assert error.value.code == 2
    assert "concrete production audit" in capsys.readouterr().err
    assert not any(audit_tests.counts(context).values())
    packet, packet_json, artifact, fusion, _ = audit_tests.downstream(context)
    packet_path = write_request(lane.root, packet_json.encode(), "legacy-packet.json")
    review_path = write_request(
        lane.root, audit_tests.review_bytes(packet), "legacy-review.json"
    )
    before = audit_tests.counts(context)
    for arguments in (
        legacy_export,
        [
            "llm-review",
            "import",
            *base,
            "--packet",
            str(packet_path),
            "--review",
            str(review_path),
        ],
        [
            "fusion-run",
            "create",
            *base,
            "--review-artifact-id",
            artifact.review_artifact_id,
        ],
        [
            "portfolio-revision",
            "create",
            *base,
            "--fusion-run-id",
            fusion.fusion_run_id,
        ],
    ):
        with pytest.raises(SystemExit) as error:
            main(arguments)
        assert error.value.code == 2
        captured = capsys.readouterr()
        assert "concrete production audit" in captured.err
        assert SECRET not in captured.out + captured.err
    assert audit_tests.counts(context) == before
    assert not (lane.root / "forbidden.json").exists()
