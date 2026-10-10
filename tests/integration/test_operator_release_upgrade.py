"""Release-maintenance failures cannot reopen identity/data gates."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from scripts import daily_operator as daily
from scripts import operator_release_upgrade as upgrade
from scripts.operator_release_acceptance import previous_installation
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock


@pytest.fixture(params=["1.1.0", "1.2.0", "1.3.0"])
def legacy(tmp_path, request):
    result = previous_installation(tmp_path / "中文 space", request.param)
    return daily.Operator(result["runtime"],result["backups"],synthetic=True,clock=SyntheticProspectiveClock(datetime(2030,1,1,tzinfo=timezone.utc)))


def test_release_entry_requires_an_installed_noneditable_wheel():
    import football_system
    # Full source pytest intentionally imports the checkout. Wheel E2E exercises
    # the genuine positive RECORD/provenance path without this source fixture.
    assert Path(daily.PROJECT / "src") in Path(football_system.__file__).parents
    with pytest.raises(daily.PreparationError, match="INSTALLED_WHEEL_REQUIRED"):
        daily.verify_core()


def test_legacy_backup_preserves_both_original_databases(legacy):
    _, raw, before = upgrade.inspect_installation(legacy)
    backup = upgrade.release_backup(legacy,phase="BEFORE")
    assert upgrade.verify_backup(legacy,Path(backup["path"]))["databases"] == before
    assert upgrade.inspect_installation(legacy)[1:] == (raw,before)


def test_patch_requires_explicit_new_confirmation_before_any_maintenance(legacy):
    before = upgrade.inspect_installation(legacy)
    with pytest.raises(daily.PreparationError, match="RELEASE_UPGRADE_NOT_CONFIRMED"):
        upgrade.rebind_release(legacy, before_backup="unused", confirmation="UPGRADE " + str(legacy.root) + " TO 1.3.0")
    assert upgrade.inspect_installation(legacy) == before
    assert not (legacy.root / "release-v1.3.1").exists()
    assert not (legacy.root / "operator.lock").exists()


@pytest.mark.parametrize("fault",["missing","replaced","wrong_head","unfinished","expired","corrupt_backup"])
def test_rebind_preconditions_fail_before_mutation(legacy,monkeypatch,tmp_path,fault):
    before = upgrade.release_backup(legacy,phase="BEFORE")
    original = (legacy.root / "operator-install.json").read_bytes()
    # Only the installed-origin boundary is simulated here; the real boundary
    # and successful rebind both run in isolated-wheel acceptance.
    monkeypatch.setattr(daily,"verify_core",daily.verify_frozen_core)
    if fault == "missing":
        legacy.database.unlink()
    elif fault == "replaced":
        with closing(sqlite3.connect(legacy.database)) as db:
            db.execute("PRAGMA application_id=42")
    elif fault == "wrong_head":
        with closing(sqlite3.connect(legacy.database)) as db:
            db.execute("UPDATE alembic_version SET version_num='wrong'")
            db.commit()
    elif fault == "unfinished":
        (legacy.operations / "unfinished").mkdir()
    elif fault == "expired":
        with pytest.raises(daily.PreparationError,match="EXPIRED_DELETE_ONLY"):
            upgrade.release_backup(legacy,phase="BEFORE",evidence_root=tmp_path,
                evidence={"must-not-be-read.json":dict(sha256="0"*64,retention_until_utc="2029-01-01T00:00:00Z")})
        assert not (legacy.root / "operator.lock").exists()
        return
    else:
        (Path(before["path"]) / "operator-install.json").write_bytes(b"changed")
    with pytest.raises(daily.PreparationError):
        upgrade.rebind_release(legacy,before_backup=before["path"],confirmation="UPGRADE "+str(legacy.root)+" TO 1.3.1")
    assert (legacy.root / "operator-install.json").read_bytes() == original
    assert not (legacy.root / "release-v1.3.1").exists()
    if fault == "missing":
        assert not legacy.database.exists()


def test_interrupted_manifest_rebind_stays_locked_and_fail_closed(legacy,monkeypatch):
    before = upgrade.release_backup(legacy,phase="BEFORE")
    monkeypatch.setattr(daily,"verify_core",daily.verify_frozen_core)
    original = upgrade._replace_manifest
    def interrupted(path,old,new):
        if path.name == "operator-install.json":
            raise OSError("synthetic interruption after first manifest")
        original(path,old,new)
    monkeypatch.setattr(upgrade,"_replace_manifest",interrupted)
    with pytest.raises(OSError,match="synthetic interruption"):
        upgrade.rebind_release(legacy,before_backup=before["path"],confirmation="UPGRADE "+str(legacy.root)+" TO 1.3.1")
    assert (legacy.root / "operator.lock").is_file()
    assert (legacy.root / "release-v1.3.1/intent.json").is_file()
    assert not (legacy.root / "release-v1.3.1/receipt.json").exists()
    assert daily.read_bytes(legacy.backups / "installation.json") != daily.read_bytes(legacy.root / "operator-install.json")
    with pytest.raises(daily.PreparationError,match="BACKUP_INSTALLATION_MISMATCH|OPERATOR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED"):
        legacy.check()


@pytest.mark.parametrize("field", ["software_identity", "operator_code_hash"])
@pytest.mark.parametrize("version", ["1.2.0", "1.3.0"])
def test_previous_release_binding_tamper_rejected_before_maintenance(tmp_path, field, version):
    record = previous_installation(tmp_path / "old", version)
    if field == "software_identity":
        record[field] = dict(record[field], implementation_revision="package:"+"0"*64)
    else:
        record[field] = "0"*64
    with pytest.raises(daily.PreparationError):
        upgrade.maintenance_profile(record)


def test_installed_maintenance_does_not_prepend_source_metadata(tmp_path, monkeypatch):
    from alembic import command
    path = tmp_path / "existing.sqlite"
    path.touch()
    seen = []
    monkeypatch.setattr(command, "upgrade", lambda config, head: seen.append((config.get_main_option("prepend_sys_path"), head)))
    monkeypatch.setattr(command, "check", lambda config: None)
    upgrade.upgrade_existing_database(path)
    assert seen == [("", "8ea7bcd49510")]


@pytest.mark.parametrize("version", ["1.2.0", "1.3.0"])
def test_previous_release_fixture_reproduces_released_bytes_under_lf_git_default(tmp_path, monkeypatch, version):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    record = previous_installation(tmp_path / "published", version)
    expected = {"1.2.0": (upgrade.RELEASED_V120_IDENTITY, upgrade.RELEASED_V120_OPERATOR_HASH),
        "1.3.0": (upgrade.RELEASED_V130_IDENTITY, upgrade.RELEASED_V130_OPERATOR_HASH)}[version]
    assert (record["software_identity"], record["operator_code_hash"]) == expected
