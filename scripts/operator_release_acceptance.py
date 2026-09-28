"""Isolated installed-wheel acceptance of the v1.2 maintenance path (synthetic)."""

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def exercise(work):
    from scripts import daily_operator as daily
    from scripts.operator_release_upgrade import release_backup, rebind_release, upgrade_existing_database, database_state
    from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
    from football_system.domain.openfootball_production import OpenFootballProductionArtifactV1
    from football_system.domain.archive import canonical_json
    from contextlib import closing
    import sqlite3

    daily.verify_core()  # Real wheel provenance/RECORD, version and frozen math; not mocked.
    work.mkdir(parents=True, exist_ok=False)
    old = work / "v110"
    old.mkdir()
    reference = "5ed940a8af8077be80549603a2da38aea77fc1bf"
    raw = subprocess.check_output(["git", "archive", reference, "src", "config", "migrations", "alembic.ini",
        "scripts/daily_operator.py", "scripts/preparation_inputs.py", "daily.cmd"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        archive.extractall(old, filter="data")
    (work / "football_runtime").mkdir()
    (work / "football_backups").mkdir()
    code = """
import json,sys
from pathlib import Path
from datetime import datetime,timezone
import football_system
from scripts.daily_operator import Operator
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
assert football_system.__version__=='1.1.0'
assert Path(sys.argv[1]) in Path(football_system.__file__).parents
base=Path(sys.argv[2])
op=Operator(base/'football_runtime/v1.1.0',base/'football_backups/v1.1.0',synthetic=True,clock=SyntheticProspectiveClock(datetime(2030,1,1,tzinfo=timezone.utc)))
print(json.dumps(op.initialize(op.confirmation)))
"""
    env = os.environ | {"PYTHONPATH": str(old / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    legacy = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code, str(old), str(work)], cwd=old, env=env, text=True, encoding="utf-8").splitlines()[-1])
    op = daily.Operator(work / "football_runtime/v1.1.0", work / "football_backups/v1.1.0",
        synthetic=True, clock=SyntheticProspectiveClock(datetime(2030, 1, 1, tzinfo=timezone.utc)))
    production = op.root / "db/production.sqlite"
    upgrade_existing_database(production)
    artifact = OpenFootballProductionArtifactV1.freeze(kind="DATA_BINDING", payload={"classification": "SYNTHETIC_RELEASE_ACCEPTANCE_ONLY"},
        parents=(), recorded_at_utc=op.clock.now())
    with closing(sqlite3.connect(production)) as db:
        db.execute("INSERT INTO ofp_artifacts VALUES (?,?,?,?,?)", (artifact.artifact_id, artifact.kind, artifact.artifact_hash,
            canonical_json(artifact), artifact.model_dump(mode="json")["recorded_at_utc"]))
        db.commit()
    before_state = database_state(production)
    before = release_backup(op, phase="BEFORE")
    result = rebind_release(op, before_backup=before["path"], confirmation="UPGRADE "+str(op.root)+" TO 1.2.0")
    assert result["status"] == "COMPLETE" and database_state(production) == before_state
    assert op.check()["installation_id"] == legacy["installation_id"]
    assert op.validate()["status"] == "FILES_CHECKED"
    after = release_backup(op, phase="AFTER")
    summary = dict(status="PASS",provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE_ONLY",version="1.2.0",head=daily.RELEASE_HEAD,
        software_identity=result["software_identity"],operator_code_hash=result["operator_code_hash"],
        both_database_identities_preserved=True,production_bytes_unchanged=True,old_rows_unchanged=True,
        old_heads={n:s["head"] for n,s in result["databases_before"].items()},new_heads={n:s["head"] for n,s in result["databases_after"].items()},
        before_backup=before["backup_id"],after_backup=after["backup_id"],operator_state="FILES_CHECKED",mode=daily.MODE,
        real_provider_http=0,llm_api_http=0,real_observations=0,auto_betting="NO")
    (work / "acceptance-summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    return summary


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    def denied(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN_IN_RELEASE_ACCEPTANCE")
    socket.socket.connect = denied
    print(json.dumps(exercise(Path(sys.argv[1]).resolve()), sort_keys=True))
    print("V120_OPERATOR_INSTALLED_RELEASE_ACCEPTANCE_PASS")
