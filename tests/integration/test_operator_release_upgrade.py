"""Release-maintenance failures cannot reopen identity/data gates."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from scripts import daily_operator as daily
from scripts import operator_release_upgrade as upgrade
from tests.integration.openfootball_upgrade_support import archive_v110, run_v110
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock


@pytest.fixture
def legacy(tmp_path):
    checkout = archive_v110(tmp_path / "v110")
    base = tmp_path / "中文 space"
    (base / "football_runtime").mkdir(parents=True)
    (base / "football_backups").mkdir()
    result = run_v110(checkout, """
import json,sys
from pathlib import Path
from datetime import datetime,timezone
from scripts.daily_operator import Operator
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
base=Path(sys.argv[2])
op=Operator(base/'football_runtime/v1.1.0',base/'football_backups/v1.1.0',synthetic=True,clock=SyntheticProspectiveClock(datetime(2030,1,1,tzinfo=timezone.utc)))
print(json.dumps(op.initialize(op.confirmation)))
""", base)
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
        upgrade.rebind_release(legacy,before_backup=before["path"],confirmation="UPGRADE "+str(legacy.root)+" TO 1.2.0")
    assert (legacy.root / "operator-install.json").read_bytes() == original
    assert not (legacy.root / "release-v1.2.0").exists()
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
        upgrade.rebind_release(legacy,before_backup=before["path"],confirmation="UPGRADE "+str(legacy.root)+" TO 1.2.0")
    assert (legacy.root / "operator.lock").is_file()
    assert (legacy.root / "release-v1.2.0/intent.json").is_file()
    assert not (legacy.root / "release-v1.2.0/receipt.json").exists()
    with pytest.raises(daily.PreparationError,match="BACKUP_INSTALLATION_MISMATCH"):
        legacy.check()
