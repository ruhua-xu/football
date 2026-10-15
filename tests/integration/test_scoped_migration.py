"""A genuine released-code legacy DB upgrades losslessly to the scoped ledger."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.database.openfootball_scoped_schema import HEAD, TABLES, assert_scoped_head
from tests.integration.openfootball_upgrade_support import sqlite_snapshot

ROOT = Path(__file__).resolve().parents[2]


def test_actual_v131_database_rows_ddl_json_survive_scoped_upgrade_and_empty_roundtrip(tmp_path):
    old = tmp_path / "published-v131"
    old.mkdir()
    archive = subprocess.check_output(["git", "archive", "f689ff7340598fd37f061473383461117ecb637d", "src", "config", "migrations", "scripts", "data/fixtures", "alembic.ini", "pyproject.toml"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(archive)) as stream:
        stream.extractall(old, filter="data")
    work = tmp_path / "genuine-legacy-lifecycle"
    code = """
import json,sys
from pathlib import Path
import football_system
from scripts.real_bridge_acceptance import exercise
assert football_system.__version__=='1.3.1' and Path(sys.argv[1]) in Path(football_system.__file__).parents
print(json.dumps(exercise(Path(sys.argv[2]))))
"""
    process = subprocess.run([sys.executable, "-B", "-c", code, str(old), str(work)], cwd=old,
        env=os.environ | {"PYTHONPATH": str(old / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert process.returncode == 0, process.stdout + process.stderr
    legacy = json.loads(process.stdout.splitlines()[-1])
    path = work / "acceptance.db"
    before = sqlite_snapshot(path)
    assert before["head"] == "8ea7bcd49510" and before["rows"]["rb_prediction_slots"] and before["rows"]["rb_head_consumptions"]
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", ("sqlite:///" + path.as_posix()).replace("%", "%%"))
    command.upgrade(cfg, HEAD)
    command.check(cfg)
    after = sqlite_snapshot(path)
    assert after["head"] == HEAD
    for part in ("schema", "rows", "layout", "artifacts"):
        assert all(after[part][key] == value for key, value in before[part].items()), part
    assert set(after["rows"]) - set(before["rows"]) == set(TABLES)
    engine = create_database_engine("sqlite:///" + path.as_posix())
    try:
        with create_session_factory(engine)() as session:
            assert_scoped_head(session)
            assert session.scalar(text("SELECT count(*) FROM rb_locks")) > 0
    finally:
        engine.dispose()
    command.downgrade(cfg, "8ea7bcd49510")
    assert sqlite_snapshot(path) == before
    command.upgrade(cfg, HEAD)
    command.check(cfg)
    final = sqlite_snapshot(path)
    for part in ("schema", "rows", "layout", "artifacts"):
        assert all(final[part][key] == value for key, value in before[part].items())
    (tmp_path / "scoped-genuine-v131-upgrade-proof.json").write_text(json.dumps(dict(status="PASS_GENUINE_RELEASED_CODE_DB_UPGRADE",
        release="f689ff7340598fd37f061473383461117ecb637d", legacy_lifecycle=legacy, old_rows_ddl_artifact_json_unchanged=True,
        added_tables=list(TABLES), empty_roundtrip=True, production_touched=False), indent=2), encoding="utf-8")
