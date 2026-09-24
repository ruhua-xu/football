"""Preparation glue only: isolated synthetic DBs, fabricated inputs, network denied."""

from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
import inspect
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile

import pytest

from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from scripts import daily_operator as module
from scripts.daily_operator import Operator, PreparationError, encoded, sha
from tests.contract.test_fixture_manual_provider import _write_archive
from tests.contract.test_sporttery_manual_provider import _document_data
from tests.integration.openfootball_upgrade_support import archive_v110, run_v110
from tests.integration.test_market_v2_upgrade import config_for
from alembic import command

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
KICKOFF = NOW+timedelta(days=1)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Preparation must not send provider/LLM/network requests")
    monkeypatch.setattr(socket, "create_connection", denied)
    original = socket.socket.connect
    def guarded(connection, address):
        # Windows asyncio uses a local socketpair for its wake-up pipe, not HTTP.
        if isinstance(address, tuple) and address[0] == "127.0.0.1" and any(
                frame.function == "_fallback_socketpair" for frame in inspect.stack(context=0)):
            return original(connection, address)
        return denied()
    monkeypatch.setattr(socket.socket, "connect", guarded)


def operator_at(root):
    # Frozen manual raw archive filenames are long. Own a short, disposable
    # sandbox rather than changing any released filename/normalization behavior.
    parent = Path(tempfile.gettempdir()) / "opencode"
    sandbox = tempfile.TemporaryDirectory(prefix="dop", dir=parent if parent.is_dir() else None)
    base = Path(sandbox.name)
    operator = Operator(base / "football_runtime/v1.1.0", base / "football_backups/v1.1.0",
        synthetic=True, clock=SyntheticProspectiveClock(NOW))
    operator._test_sandbox = sandbox
    return operator


@pytest.fixture
def op(tmp_path):
    operator = operator_at(tmp_path)
    operator.initialize(operator.confirmation)
    yield operator
    operator._test_sandbox.cleanup()


def package(op, name, kind, file, *, lifetime=5):
    path = op.root / "inbox" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_bytes(encoded(dict(schema_version="DAILY_INPUT_PACKAGE_V1", kind=kind, file=file,
        rights_reference="self-authored SYNTHETIC test only", retention_until_utc=(NOW+timedelta(days=lifetime)).isoformat(),
        durable_ledger_authorized=True, local_backup_authorized=True)))
    return path


def fixtures(op):
    path = package(op, "fixtures", "FIXTURE", "reviewed-fixture-manual.json")
    _write_archive(path, kickoff_at_utc=KICKOFF)
    try:
        return op.import_package("fixtures", "FIXTURE")
    except PreparationError as error:
        if error.__context__ is not None:
            raise error.__context__  # Diagnostics contain only self-authored test data.
        raise


def slate(op, name="slate"):
    path = package(op, name, "SLATE", "slate.json")
    proof = b"FABRICATED SLATE ONLY"
    (path / "source.txt").write_bytes(proof)
    candidates = [dict(sporttery_match_no="TEST-"+str(i), match_date=KICKOFF.date().isoformat(),
        kickoff_at_utc=at, home_label="Fabricated Harbour FC" if i == 0 else "Synthetic Home "+str(i),
        away_label="Fabricated Orchard FC" if i == 0 else "Synthetic Away "+str(i), competition_label="Fabricated Coastal League",
        three_way_sp={"home_win": "2.11", "draw": "3.16", "away_win": "3.57"})
        for i, at in enumerate((KICKOFF.isoformat(), KICKOFF.astimezone(timezone(timedelta(hours=8))).isoformat(),
                               (KICKOFF+timedelta(seconds=1)).isoformat(), (KICKOFF+timedelta(microseconds=1)).isoformat()))]
    data = dict(schema_version="SPORTTERY_DAILY_SLATE_INPUT_V1", snapshot_id="synthetic-slate", captured_at_utc=NOW.isoformat(),
        source_reference="synthetic://slate", source_artifact_path="source.txt", source_artifact_sha256=sha(proof),
        entered_by="operator", review_level="SELF_REVIEWED", reviewed_by="operator", reviewed_at_utc=NOW.isoformat(), candidates=candidates)
    (path / "slate.json").write_bytes(encoded(data))
    return path


def evidence(op, name="facts"):
    path = package(op, name, "EVIDENCE", "evidence.json")
    proof = b"FABRICATED LINEUP UNKNOWN"
    (path / "source.txt").write_bytes(proof)
    with closing(sqlite3.connect(op.database)) as db:
        match, team = db.execute("SELECT internal_match_id,home_team_id FROM matches").fetchone()
    claim = dict(schema_version="MANUAL_VERIFIED_IMPORT_V1", match_id=match, data_classification="SYNTHETIC",
        source_identity="synthetic-facts", source_reference="synthetic://facts", source_file="source.txt", source_hash=sha(proof),
        rights_basis="SELF_OBSERVED", rights_reference="self-authored synthetic", retention_until_utc=(NOW+timedelta(days=5)).isoformat(),
        verified_by="operator", verified_at_utc=NOW.isoformat(), captured_at_utc=NOW.isoformat(), available_at_utc=NOW.isoformat(),
        published_at_utc=None, fact_category="LINEUP", assertion_class="FACT", confidence="0",
        structured_payload=dict(category="LINEUP", team_id=team, status="UNKNOWN"))
    (path / "evidence.json").write_bytes(encoded(dict(request_key="test-evidence-"+name, evidence=claim)))
    return path


def test_explicit_initialization_and_physical_separation(tmp_path):
    op = operator_at(tmp_path)
    with pytest.raises(PreparationError, match="NOT_CONFIRMED"):
        op.initialize("yes")
    assert not op.root.exists() and not op.backups.exists()
    record = op.initialize(op.confirmation)
    assert op.check() == record
    assert record["schema_version"] == "DAILY_OPERATOR_INSTALL_V2"
    identity = record["software_identity"]
    assert identity["software"] == "football-system" and identity["software_version"] == "1.2.0"
    assert identity["execution_profile"] == "OPENFOOTBALL_RELEASE_V1"
    assert identity["migration_head"] == "7d96abc3840f"
    assert identity["implementation_revision"].startswith("package:")
    assert record["databases"]["production.sqlite"] != record["databases"]["synthetic.sqlite"]
    with pytest.raises(PreparationError, match="PARTIAL_INSTALLATION"):
        op.initialize(op.confirmation)


def test_missing_db_or_manifest_does_not_create_second_database(op):
    path = op.database
    path.unlink()
    with pytest.raises(PreparationError, match="MISSING_OR_REPLACED"):
        op.missing()
    assert not path.exists()
    (op.root / "operator-install.json").unlink()
    with pytest.raises(PreparationError, match="MISSING_OR_OVERSIZE"):
        op.check()
    with pytest.raises(PreparationError, match="PARTIAL_INSTALLATION"):
        op.initialize(op.confirmation)


def test_copied_synthetic_cannot_impersonate_production(op):
    production = op.root / "db/production.sqlite"
    shutil.copyfile(op.database, production)
    with pytest.raises(PreparationError, match="IDENTITY_MISMATCH"):
        op.check()


@pytest.mark.parametrize("argument", ["--force", "--skip", "--backdate", "prepare", "lock", "settle", "report", "packet-export", "ingest-market-odds"])
def test_no_command_forwarding_or_overrides(argument, monkeypatch):
    monkeypatch.setattr(module, "verify_core", lambda: pytest.fail("invalid argument reached startup"))
    messages = []
    assert module.main([argument], input_fn=lambda _: pytest.fail("unexpected prompt"), output=messages.append) == 2
    assert module.BANNER in messages


def test_exact_utc_buckets_and_no_prediction_artifacts(op):
    fixtures(op)
    slate(op)
    result = op.import_package("slate", "SLATE")
    buckets = result["result"]["exact_kickoff_buckets"]
    assert len(buckets) == 3 and len(buckets[0]["members"]) == 2
    assert buckets[1]["kickoff_at_utc"].endswith("00.000001Z") and buckets[2]["kickoff_at_utc"].endswith("01Z")
    assert buckets[0]["members"][0]["match_id"] is not None
    assert result["result"]["artifact"]["analysis_status"] == "NO_ANALYSIS"
    assert op.import_package("slate", "SLATE") == result
    with closing(sqlite3.connect(op.database)) as db:
        for table in ("pv_runs", "pv_locks", "pv_epochs", "pv_settlements", "pv_reports", "mm_analyses", "mm_packets", "rd_runs"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert op.missing()["real_performance"] == "INSUFFICIENT_PROSPECTIVE_SAMPLE"


def test_fixture_sporttery_and_evidence_use_frozen_input_services(op):
    fixture = fixtures(op)
    assert fixture["status"] == "INPUT_IMPORTED"
    path = package(op, "sporttery", "SPORTTERY", "sp.json")
    raw = b"SYNTHETIC SP SOURCE"
    (path / "source.txt").write_bytes(raw)
    data = _document_data("source.txt", sha(raw), review_level="SELF_REVIEWED", entered_by="operator", reviewed_by="operator")
    data["captured_at_utc"] = data["reviewed_at_utc"] = NOW.isoformat()
    data["records"][0]["kickoff_at_utc"] = KICKOFF.isoformat()
    (path / "sp.json").write_bytes(encoded(data))
    sp = op.import_package("sporttery", "SPORTTERY")
    # Missing provider aliases must be exposed, never guessed. A maintainer's
    # existing frozen identity-review API supplies the explicit test mapping.
    assert sp["status"] == "INPUT_ISSUES"
    issue = sp["result"]["reconciliation"]["unresolved"][0]
    from football_system.application.live_sources import IdentityReviewDocument
    from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository
    from football_system.infrastructure.database.session import create_session_factory
    with closing(sqlite3.connect(op.database)) as db:
        match = db.execute("SELECT internal_match_id FROM matches").fetchone()[0]
    engine = op._engine()
    try:
        SqlAlchemyLiveSourceRepository(create_session_factory(engine), clock=op.clock.now).import_identity_review(
            IdentityReviewDocument(review_id="synthetic-identity-review", source_ingestion_id=sp["result"]["artifact"]["ingestion_id"],
                reviewed_by="synthetic-maintainer", reviewed_at_utc=NOW, mappings=(dict(provider_code=issue["provider_code"],
                    external_namespace=issue["external_namespace"], external_match_id=issue["external_match_id"], internal_match_id=match),)))
    finally:
        engine.dispose()
    second = package(op, "sporttery-reviewed", "SPORTTERY", "sp.json")
    (second / "source.txt").write_bytes(raw)
    data["snapshot_id"] = "new-synthetic-snapshot-after-identity-review"
    (second / "sp.json").write_bytes(encoded(data))
    imported = op.import_package("sporttery-reviewed", "SPORTTERY")
    assert imported["status"] == "INPUT_IMPORTED", json.dumps(imported["result"]["reconciliation"])
    evidence(op)
    fact = op.import_package("facts", "EVIDENCE")
    assert op.import_package("facts", "EVIDENCE") == fact
    assert fact["data_classification"] == "SYNTHETIC"
    with closing(sqlite3.connect(op.database)) as db:
        assert db.execute("SELECT count(*) FROM sporttery_bonus_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM pv_evidence").fetchone()[0] == 1
        snapshot = json.loads(db.execute("SELECT artifact_json FROM pv_artifacts WHERE schema_version='EVIDENCE_SNAPSHOT_V1'").fetchone()[0])
        assert snapshot["freshness_status"] == "UNKNOWN"
        assert snapshot["claim"]["structured_payload"]["status"] == "UNKNOWN"
        assert db.execute("SELECT count(*) FROM pv_locks").fetchone()[0] == 0
    assert op.validate()["status"] == "FILES_CHECKED"


def test_invalid_schema_path_and_expiry_precede_core_intent(op):
    path = slate(op)
    data = json.loads((path / "slate.json").read_bytes())
    data["source_artifact_path"] = "../../secret.txt"
    (path / "slate.json").write_bytes(encoded(data))
    with pytest.raises(PreparationError, match="VALIDATION_FAILED_NO_IMPORT"):
        op.import_package("slate", "SLATE")
    assert not list(op.operations.iterdir())
    with pytest.raises(PreparationError, match="CONTAINED_PATH"):
        op.import_package("../other", "SLATE")
    with pytest.raises(PreparationError, match="KIND_NOT_ALLOWED"):
        op.import_package("slate", "LOCK")
    meta = json.loads((path / "package.json").read_bytes())
    meta["retention_until_utc"] = NOW.isoformat()
    (path / "package.json").write_bytes(encoded(meta))
    (path / "slate.json").unlink()
    with pytest.raises(PreparationError, match="EXPIRED_DELETE_ONLY"):
        op.import_package("slate", "SLATE")
    assert not list(op.operations.iterdir())


def test_intent_after_commit_failure_blocks_future_writes(op, monkeypatch):
    from scripts import preparation_inputs
    fixtures(op)
    evidence(op)
    original = preparation_inputs.apply_input
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated output crash after core commit")
    monkeypatch.setattr(preparation_inputs, "apply_input", commit_then_fail)
    with pytest.raises(PreparationError, match="IMPORT_FAILED_REVIEW_REQUIRED"):
        op.import_package("facts", "EVIDENCE")
    with closing(sqlite3.connect(op.database)) as db:
        assert db.execute("SELECT count(*) FROM pv_evidence").fetchone()[0] == 1
    with pytest.raises(PreparationError, match="RECOVERY_REQUIRED_UNFINISHED"):
        op.import_package("facts", "EVIDENCE")
    assert op.validate()["unfinished_operations"]
    assert op.backup()["operator_state"] == "RECOVERY_REQUIRED"


def test_backup_contains_wal_commit_and_detects_payload_corruption(op):
    fixtures(op)
    connection = sqlite3.connect(op.database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE synthetic_backup_marker(value TEXT)")
    connection.execute("INSERT INTO synthetic_backup_marker VALUES ('committed-in-wal')")
    connection.commit()
    try:
        backup = op.backup()
        root = Path(backup["path"])
        assert (root / "COMPLETE").is_file()
        with closing(sqlite3.connect(root / "synthetic.sqlite")) as restored:
            assert restored.execute("SELECT value FROM synthetic_backup_marker").fetchone()[0] == "committed-in-wal"
        for name, spec in backup["files"].items():
            assert sha((root / name).read_bytes()) == spec["sha256"]
    finally:
        connection.close()
    payload = next(op.operations.glob("*/payload/fixture-source.txt"))
    payload.write_bytes(b"tampered")
    with pytest.raises(PreparationError, match="HASH_MISMATCH"):
        op.validate()
    with pytest.raises(PreparationError, match="HASH_MISMATCH"):
        op.backup()


def test_expired_bytes_not_read_by_validation_backup_or_receipt(op, monkeypatch):
    result = fixtures(op)
    op.clock.at = NOW+timedelta(days=6)
    original = module.read_bytes
    def read_allowed(path):
        assert "payload" not in Path(path).parts and "c" not in Path(path).parts
        return original(path)
    monkeypatch.setattr(module, "read_bytes", read_allowed)
    assert op.validate()["expired_delete_only"] == [result["operation_id"]]
    assert op.receipt(result["operation_id"]) == result
    backup = op.backup()
    assert backup["excluded_expired"]
    assert not any("payload/" in name or "/c/" in name for name in backup["files"])


@pytest.fixture
def legacy_installation(tmp_path):
    checkout = archive_v110(tmp_path / "v110")
    base = tmp_path / "legacy-install"
    (base / "football_runtime").mkdir(parents=True)
    (base / "football_backups").mkdir()
    result = run_v110(checkout, """
import json, sys
from pathlib import Path
from datetime import datetime, timezone
import football_system
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from scripts.daily_operator import Operator
assert football_system.__version__ == '1.1.0'
assert Path(sys.argv[1]) in Path(football_system.__file__).parents
base = Path(sys.argv[2])
op = Operator(base/'football_runtime/v1.1.0', base/'football_backups/v1.1.0', synthetic=True,
    clock=SyntheticProspectiveClock(datetime(2030,1,1,tzinfo=timezone.utc)))
record = op.initialize(op.confirmation)
assert op.check() == record
print(json.dumps(dict(runtime=str(op.root),backups=str(op.backups),installation=record)))
""", base)
    op = Operator(result["runtime"], result["backups"], synthetic=True, clock=SyntheticProspectiveClock(NOW))
    return op, result["installation"]


def test_old_v110_installation_old_head_is_valid_without_rewriting(legacy_installation):
    op, original = legacy_installation
    path = op.root / "operator-install.json"
    before = path.read_bytes()
    assert original["schema_version"] == "DAILY_OPERATOR_INSTALL_V1"
    assert original["operator_code_hash"] == module.LEGACY_OPERATOR_CODE_HASH
    assert op.check() == original and op.validate()["status"] == "FILES_CHECKED"
    assert path.read_bytes() == before
    # Compatibility validation does not grant candidate writers access to V1.
    with pytest.raises(PreparationError, match="OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED"):
        with op.guard(writing=True):
            pytest.fail("candidate writer accepted legacy installation")


def test_old_v110_installation_new_head_requires_review(legacy_installation):
    op, original = legacy_installation
    manifest_before = (op.root / "operator-install.json").read_bytes()
    command.upgrade(config_for(op.database), "7d96abc3840f")
    with pytest.raises(PreparationError, match="OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED"):
        op.check()
    assert (op.root / "operator-install.json").read_bytes() == manifest_before
    with closing(sqlite3.connect(op.database)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "7d96abc3840f"


@pytest.mark.parametrize("field,value", [("software_version", "1.0.0"), ("migration_head", "6c859ab273fe"),
    ("implementation_revision", "package:"+"0"*64), ("execution_profile", "RELEASED_V110")])
def test_candidate_installation_rejects_changed_software_identity(op, field, value):
    record = module.read_json(op.root / "operator-install.json")
    record["software_identity"][field] = value
    # Both files are test-owned; keeping them mutually equal must not create trust.
    (op.root / "operator-install.json").write_bytes(encoded(record))
    (op.backups / "installation.json").write_bytes(encoded(record))
    with pytest.raises(PreparationError, match="OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED"):
        op.check()


def test_candidate_backup_binds_version_implementation_and_head(op):
    record = op.check()
    backup = op.backup()
    assert backup["schema_version"] == "DAILY_OPERATOR_BACKUP_V2"
    assert backup["software_identity"] == record["software_identity"]
    assert backup["operator_code_hash"] == record["operator_code_hash"]
    assert backup["migration_head"] == "7d96abc3840f"


def test_busy_stale_lock_and_clock_regression_are_fail_closed(op):
    slate(op)
    (op.root / "operator.lock").write_bytes(b"possibly-crashed-owner")
    with pytest.raises(PreparationError, match="BUSY_OR_RECOVERY"):
        op.import_package("slate", "SLATE")
    assert (op.root / "operator.lock").read_bytes() == b"possibly-crashed-owner"
    (op.root / "operator.lock").unlink()  # Test-owned sandbox only.
    op.import_package("slate", "SLATE")
    op.clock.at -= timedelta(seconds=1)
    with pytest.raises(PreparationError, match="CLOCK_REGRESSION"):
        op.import_package("slate", "SLATE")


@pytest.mark.parametrize("path", ["provider_capability_20260917/data.json", "bundesliga_acceptance_20260911/x.txt", ".env"])
def test_closed_capture_and_secret_paths_rejected_without_reads(tmp_path, path):
    with pytest.raises(PreparationError, match="FORBIDDEN"):
        module.safe_path(tmp_path / path)


def test_frozen_core_version_hashes_and_input_only_surface():
    module.verify_frozen_core()
    for method in ("prepare", "lock", "settle", "report", "create_epoch", "replace", "train", "fetch", "export_packet"):
        assert not hasattr(Operator, method)
    assert not any(name in vars(module) for name in ("ProspectiveService", "TheOddsApiMarketOddsProvider"))


def test_menu_six_only_and_initialization_confirmation(tmp_path, monkeypatch):
    op = operator_at(tmp_path)
    monkeypatch.setattr(module, "verify_core", lambda: None)
    monkeypatch.setattr(module, "Operator", lambda *args, **kwargs: op)
    with pytest.raises(PreparationError, match="NOT_CONFIRMED"):
        module.main([], input_fn=lambda _: "no", output=lambda _: None)
    assert not op.root.exists()
    answers = iter([op.confirmation, "prepare", "4", "5", "6", "0"])
    messages = []
    assert module.main([], input_fn=lambda _: next(answers), output=messages.append) == 0
    assert any("ACTION_NOT_ALLOWED" in value for value in messages)
    assert any("PRODUCTION_DECISION_ADAPTER_UNAVAILABLE" in value for value in messages)
    with closing(sqlite3.connect(op.root / "db/production.sqlite")) as db:
        assert db.execute("SELECT count(*) FROM pv_runs").fetchone()[0] == 0
    op._test_sandbox.cleanup()


def test_wrong_head_and_hardlink_refused(op):
    with closing(sqlite3.connect(op.database)) as db:
        db.execute("UPDATE alembic_version SET version_num='not-approved'")
        db.commit()
    with pytest.raises(PreparationError, match="HEAD_MISMATCH"):
        op.missing()
    source = op.root / "test.txt"
    source.write_bytes(b"test-owned fixture")
    alias = op.root / "linked.txt"
    os.link(source, alias)
    try:
        with pytest.raises(PreparationError, match="HARDLINK_FORBIDDEN"):
            module.read_bytes(alias)
    finally:
        alias.unlink()


def test_cross_install_receipt_copy_is_not_trusted(op, tmp_path):
    slate(op)
    result = op.import_package("slate", "SLATE")
    other = operator_at(tmp_path)
    other.initialize(other.confirmation)
    folder = op.operations / result["operation_id"][:20]
    shutil.copytree(folder, other.operations / folder.name)
    try:
        with pytest.raises(PreparationError, match="INSTALLATION_MISMATCH"):
            other.validate()
    finally:
        other._test_sandbox.cleanup()


def test_partial_backup_is_not_complete_and_blocks_new_writes(op, monkeypatch):
    slate(op)
    op.import_package("slate", "SLATE")
    original = module.write_new
    def fail_manifest(path, raw):
        if Path(path).name == "backup-manifest.json":
            raise OSError("synthetic backup failure")
        return original(path, raw)
    monkeypatch.setattr(module, "write_new", fail_manifest)
    with pytest.raises(OSError):
        op.backup()
    assert op.validate()["partial_backups"]
    assert not list(op.backups.glob("*/COMPLETE"))
    with pytest.raises(PreparationError, match="PARTIAL_BACKUP"):
        op.import_package("slate", "SLATE")
