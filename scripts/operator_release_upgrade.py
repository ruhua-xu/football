"""Controlled v1.2 operator maintenance; no prediction, source admission or networking.

Only an idle INPUT_PREPARATION installation is supported. Backups use SQLite's
snapshot API; active database paths/inodes/application_ids are never replaced.
An interrupted rebind retains its lock and journal for explicit maintenance.
"""

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from scripts import daily_operator as daily


def digest_file(path):
    with daily.safe_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def database_state(path):
    path = daily.safe_path(path)
    daily.require(path.is_file(), "DATABASE_MISSING_OR_REPLACED")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        daily.require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "DATABASE_INTEGRITY_FAILED")
        daily.require(not db.execute("PRAGMA foreign_key_check").fetchall(), "DATABASE_FK_FAILED")
        objects = {kind+":"+name: [owner, daily.sha(daily.encoded(sql))] for kind, name, owner, sql in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name<>'alembic_version' AND name NOT LIKE 'sqlite_stat%' ORDER BY type,name")}
        tables = {}
        for key in objects:
            if not key.startswith("table:"):
                continue
            name = key.split(":", 1)[1]
            quoted = '"'+name.replace('"', '""')+'"'
            rows = sorted(json.dumps([{"blob_hex": x.hex()} if isinstance(x, bytes) else x for x in row],
                ensure_ascii=False, separators=(",", ":"), allow_nan=False) for row in db.execute("SELECT * FROM "+quoted))
            tables[name] = dict(count=len(rows), sha256=daily.sha(daily.encoded(rows)))
        ofp = None
        if "ofp_source_records" in tables:
            counts = db.execute("SELECT count(*),coalesce(sum(included),0),coalesce(sum(NOT included),0) FROM ofp_source_records").fetchone()
            roots = db.execute("SELECT json_extract(artifact_json,'$.payload.subject.mapping_root'),json_extract(artifact_json,'$.payload.subject.training_window_hash'),json_extract(artifact_json,'$.payload.facts_root') FROM ofp_artifacts WHERE kind='DATA_BINDING' ORDER BY artifact_id").fetchall()
            ofp = dict(source_records=counts[0], admitted_facts=counts[1], exceptions=counts[2], roots=[list(r) for r in roots])
        return dict(file_identity=daily.file_identity(path), application_id=db.execute("PRAGMA application_id").fetchone()[0],
            head=db.execute("SELECT version_num FROM alembic_version").fetchone()[0], objects=objects, tables=tables, ofp=ofp,
            sha256=digest_file(path))


def inspect_installation(operator):
    original = daily.read_bytes(operator.root / "operator-install.json")
    daily.require(daily.read_bytes(operator.backups / "installation.json") == original, "BACKUP_INSTALLATION_MISMATCH")
    record = daily.read_json(operator.root / "operator-install.json")
    daily.require(record["mode"] == daily.MODE and record["runtime"] == str(operator.root)
        and record["backups"] == str(operator.backups), "INSTALLATION_PATH_MISMATCH")
    daily.installation_profile(record)  # Fixed legacy V1, or the exact released V2 identity.
    daily.require(set(record["databases"]) == {"production.sqlite", "synthetic.sqlite"}
        and len({v["application_id"] for v in record["databases"].values()}) == 2, "DATABASE_ROLES_MUST_BE_DISTINCT")
    # This closeout never guesses retention/recovery for prior input operations.
    for name in ("ops/p", "ops/s", "inbox"):
        directory = daily.safe_path(operator.root / name)
        daily.require(directory.is_dir() and not any(directory.iterdir()), "IDLE_INPUT_PREPARATION_REQUIRED")
    daily.require(not operator._partial_backups(), "RECOVERY_REQUIRED_PARTIAL_BACKUP")
    states = {}
    for name, expected in record["databases"].items():
        path = daily.safe_path(operator.root / "db" / name)
        daily.require(path.is_file() and daily.file_identity(path) == expected["file_identity"], "DATABASE_MISSING_OR_REPLACED")
        state = database_state(path)
        daily.require(state["application_id"] == expected["application_id"], "DATABASE_IDENTITY_MISMATCH")
        daily.require(state["head"] in {daily.HEAD, daily.RELEASE_HEAD}, "MIGRATION_HEAD_MISMATCH")
        states[name] = state
    return record, original, states


@contextmanager
def maintenance_lock(operator):
    lock = operator.root / "operator.lock"
    try:
        daily.write_new(lock, daily.encoded(dict(pid=os.getpid(), at=operator.clock.now().isoformat(), mode="V120_RELEASE_MAINTENANCE")))
    except FileExistsError:
        raise daily.PreparationError("OPERATOR_BUSY_OR_RECOVERY_REQUIRED") from None
    try:
        yield
    finally:
        lock.unlink()


def release_backup(operator, *, phase, evidence_root=None, evidence=None):
    """Formal dual-DB, original-installation and explicitly authorized evidence backup."""
    daily.require(phase in {"BEFORE", "AFTER"}, "BACKUP_PHASE_REQUIRED")
    with maintenance_lock(operator):
        installation, original, before = inspect_installation(operator)
        if phase == "AFTER":
            daily.require(installation["schema_version"] == daily.INSTALL_V2, "RELEASE_REBIND_REQUIRED")
            operator.check()
        payloads = {}
        for name, authorization in (evidence or {}).items():
            expiry = authorization.get("retention_until_utc")
            daily.require(authorization.get("permanent") is True if expiry is None else daily.timestamp(expiry) > operator.clock.now(),
                "EXPIRED_DELETE_ONLY")
            raw = daily.read_bytes(daily.relative(evidence_root, name))
            daily.require(daily.sha(raw) == authorization["sha256"], "BACKUP_SOURCE_HASH_MISMATCH")
            payloads[name] = raw
        destination = operator.backups / ("release12-"+uuid.uuid4().hex[:12])
        destination.mkdir(exist_ok=False)
        for name, state in before.items():
            source_path = operator.root / "db" / name
            target_path = destination / name
            with closing(sqlite3.connect(source_path.as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(target_path)) as target:
                source.backup(target)
            target_state = database_state(target_path)
            daily.require(all(target_state[k] == state[k] for k in ("head", "application_id", "objects", "tables", "ofp")), "BACKUP_DATABASE_INVALID")
        daily.write_new(destination / "operator-install.json", original)
        daily.write_new(destination / "backup-installation.json", original)
        for name, raw in payloads.items():
            target = daily.relative(destination / "evidence", name)
            target.parent.mkdir(parents=True, exist_ok=True)
            expiry = evidence[name].get("retention_until_utc")
            daily.require(expiry is None or daily.timestamp(expiry) > operator.clock.now(), "EXPIRED_DELETE_ONLY")
            daily.write_new(target, raw)
        daily.require(inspect_installation_without_backup_scan(operator) == (installation, original, before), "RUNTIME_CHANGED_DURING_BACKUP")
        files = {p.relative_to(destination).as_posix(): dict(bytes=p.stat().st_size, sha256=digest_file(p))
            for p in sorted(destination.rglob("*")) if p.is_file()}
        manifest = dict(schema_version="DAILY_OPERATOR_RELEASE_BACKUP_V1", backup_id=destination.name, phase=phase,
            installation_id=installation["installation_id"], created_at_utc=operator.clock.now().isoformat(), status="COMPLETE",
            runtime=str(operator.root), backups=str(operator.backups), installation_sha256=daily.sha(original),
            databases=before, files=files, evidence_authorization=evidence or {}, mode=daily.MODE)
        daily.write_new(destination / "backup-manifest.json", daily.encoded(manifest))
        daily.write_new(destination / "COMPLETE", b"INPUT_PREPARATION_RELEASE_BACKUP\n")
        return dict(path=str(destination), **manifest)


def inspect_installation_without_backup_scan(operator):
    """Recheck identities/state while our own snapshot directory is still partial."""
    original = daily.read_bytes(operator.root / "operator-install.json")
    daily.require(original == daily.read_bytes(operator.backups / "installation.json"), "BACKUP_INSTALLATION_MISMATCH")
    record = daily.read_json(operator.root / "operator-install.json")
    states = {name: database_state(operator.root / "db" / name) for name in record["databases"]}
    return record, original, states


def verify_backup(operator, path):
    path = daily.safe_path(path)
    daily.require(path.parent == operator.backups and (path / "COMPLETE").is_file(), "COMPLETE_RELEASE_BACKUP_REQUIRED")
    manifest = daily.read_json(path / "backup-manifest.json")
    daily.require(manifest["schema_version"] == "DAILY_OPERATOR_RELEASE_BACKUP_V1" and manifest["phase"] == "BEFORE"
        and manifest["status"] == "COMPLETE" and manifest["runtime"] == str(operator.root)
        and manifest["backups"] == str(operator.backups) and manifest["backup_id"] == path.name, "COMPLETE_RELEASE_BACKUP_REQUIRED")
    daily.require({"production.sqlite", "synthetic.sqlite", "operator-install.json", "backup-installation.json"} <= set(manifest["files"]),
        "COMPLETE_RELEASE_BACKUP_REQUIRED")
    daily.require({p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()}
        == set(manifest["files"]) | {"backup-manifest.json", "COMPLETE"}, "BACKUP_FILE_SET_CHANGED")
    for authorization in manifest["evidence_authorization"].values():
        expiry = authorization.get("retention_until_utc")
        daily.require(authorization.get("permanent") is True if expiry is None else daily.timestamp(expiry) > operator.clock.now(), "EXPIRED_DELETE_ONLY")
    for name, expected in manifest["files"].items():
        file = daily.relative(path, name)
        daily.require(file.is_file() and file.stat().st_size == expected["bytes"] and digest_file(file) == expected["sha256"], "BACKUP_FILE_CHANGED")
    for name, expected in manifest["databases"].items():
        actual = database_state(path / name)
        daily.require(actual["file_identity"] != expected["file_identity"] and all(actual[k] == expected[k]
            for k in ("head", "application_id", "objects", "tables", "ofp")), "BACKUP_DATABASE_INVALID")
    return manifest


def upgrade_existing_database(path):
    from alembic import command
    from alembic.config import Config
    from football_system.interfaces.cli import _resource_root

    daily.require(path.is_file(), "DATABASE_MISSING_OR_REPLACED")
    resources = _resource_root()
    config = Config(str(resources / "alembic.ini"))
    config.set_main_option("script_location", str(resources / "migrations"))
    # mode=rw refuses missing paths; do not use an implicit-create SQLite URL.
    config.set_main_option("sqlalchemy.url", ("sqlite:///"+path.as_uri()+"?mode=rw&uri=true").replace("%", "%%"))
    command.upgrade(config, daily.RELEASE_HEAD)
    command.check(config)


def _replace_manifest(path, original, new):
    daily.require(daily.read_bytes(path) == original, "INSTALLATION_CHANGED_DURING_REBIND")
    temporary = path.with_name("."+path.name+".v120-new")
    daily.write_new(temporary, new)
    os.replace(temporary, path)


def rebind_release(operator, *, before_backup, confirmation):
    """Explicit one-time rebind; a failure after intent retains the maintenance lock."""
    daily.require(confirmation == "UPGRADE "+str(operator.root)+" TO 1.2.0", "RELEASE_UPGRADE_NOT_CONFIRMED")
    daily.verify_core()
    backup = verify_backup(operator, Path(before_backup))
    record, original, before = inspect_installation(operator)
    daily.require(record["schema_version"] == "DAILY_OPERATOR_INSTALL_V1", "LEGACY_RELEASE_INSTALLATION_REQUIRED")
    daily.require(backup["installation_id"] == record["installation_id"] and backup["installation_sha256"] == daily.sha(original)
        and backup["databases"] == before, "BACKUP_NOT_CURRENT")
    journal = operator.root / "release-v1.2.0"
    daily.require(not journal.exists(), "RELEASE_UPGRADE_ALREADY_STARTED_REVIEW_REQUIRED")
    lock = operator.root / "operator.lock"
    try:
        daily.write_new(lock, daily.encoded(dict(pid=os.getpid(), at=operator.clock.now().isoformat(), mode="V120_RELEASE_REBIND")))
    except FileExistsError:
        raise daily.PreparationError("OPERATOR_BUSY_OR_RECOVERY_REQUIRED") from None
    try:
        daily.require(inspect_installation(operator) == (record, original, before), "RUNTIME_CHANGED_BEFORE_REBIND")
        software = daily.release_software_identity()
        new = dict(record, schema_version=daily.INSTALL_V2, software_identity=software, operator_code_hash=daily.operator_code_hash())
        journal.mkdir(exist_ok=False)
        daily.write_new(journal / "intent.json", daily.encoded(dict(before_backup=backup["backup_id"], confirmation=confirmation,
            recorded_at_utc=operator.clock.now().isoformat(), old_installation_sha256=daily.sha(original), new_installation=new, databases=before)))
        after = {}
        for name, previous in before.items():
            path = operator.root / "db" / name
            if previous["head"] != daily.RELEASE_HEAD:
                upgrade_existing_database(path)
            state = database_state(path)
            daily.require(state["head"] == daily.RELEASE_HEAD and state["file_identity"] == previous["file_identity"]
                and state["application_id"] == previous["application_id"], "DATABASE_IDENTITY_CHANGED_DURING_UPGRADE")
            daily.require(all(state["objects"].get(k) == v for k, v in previous["objects"].items())
                and all(state["tables"].get(k) == v for k, v in previous["tables"].items()), "OLD_DATABASE_CONTENT_CHANGED")
            if previous["head"] == daily.RELEASE_HEAD:
                daily.require(state == previous, "CURRENT_HEAD_DATABASE_CHANGED")
            after[name] = state
        daily.require(software == daily.release_software_identity() and new["operator_code_hash"] == daily.operator_code_hash(),
            "IMPLEMENTATION_CHANGED_DURING_REBIND")
        replacement = daily.encoded(new)
        _replace_manifest(operator.backups / "installation.json", original, replacement)
        _replace_manifest(operator.root / "operator-install.json", original, replacement)
        daily.require(operator.check() == new, "RELEASE_REBIND_VALIDATION_FAILED")
        receipt = dict(schema_version="DAILY_OPERATOR_RELEASE_REBIND_V1", status="COMPLETE", installation_id=record["installation_id"],
            before_backup=backup["backup_id"], recorded_at_utc=operator.clock.now().isoformat(), old_installation_sha256=daily.sha(original),
            new_installation_sha256=daily.sha(replacement), software_identity=software, operator_code_hash=new["operator_code_hash"],
            databases_before=before, databases_after=after, mode=daily.MODE, old_rows_unchanged=True,
            real_provider_http=0, llm_api_http=0, real_observations=0, auto_betting="NO")
        daily.write_new(journal / "receipt.json", daily.encoded(receipt))
    except BaseException:
        # No rollback/downgrade/replacement of databases or silent lock deletion.
        raise
    else:
        lock.unlink()
        return receipt
