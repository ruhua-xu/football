"""Actual released-code database, full legacy ledger, additive shared-owner backfill."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, event
from sqlalchemy.engine import Engine

from football_system.infrastructure.database.session import create_database_engine
from football_system.infrastructure.database.portfolio_schema import HEAD, assert_portfolio_schema, assert_legacy_mirrors
from tests.integration.openfootball_upgrade_support import sqlite_snapshot

ROOT = Path(__file__).resolve().parents[2]


def genuine_database(tmp_path):
    old = tmp_path / "published-v131"
    old.mkdir()
    raw = subprocess.check_output(["git", "archive", "f689ff7340598fd37f061473383461117ecb637d", "src", "config", "migrations", "scripts", "data/fixtures", "alembic.ini", "pyproject.toml"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        archive.extractall(old, filter="data")
    work = tmp_path / "old-lifecycle"
    code = """
import json,sys
from pathlib import Path
import football_system
from scripts.real_bridge_acceptance import exercise
assert football_system.__version__=='1.3.1' and Path(sys.argv[1]) in Path(football_system.__file__).parents
print(json.dumps(exercise(Path(sys.argv[2]))))
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(old), str(work)], cwd=old,
        env=os.environ | {"PYTHONPATH": str(old / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    legacy = json.loads(result.stdout.splitlines()[-1])
    path = work / "acceptance.db"
    return path,legacy,old


def config(path):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", ("sqlite:///" + path.as_posix()).replace("%", "%%"))
    return cfg


def test_complete_genuine_v131_ledger_backfill_and_empty_portfolio_roundtrip(tmp_path):
    path,legacy,old = genuine_database(tmp_path)
    before = sqlite_snapshot(path)
    cfg = config(path)
    command.upgrade(cfg, HEAD)
    command.check(cfg)
    after = sqlite_snapshot(path)
    for part in ("schema", "rows", "layout", "artifacts"):
        assert all(after[part][key] == value for key, value in before[part].items()), part
    engine = create_database_engine("sqlite:///" + path.as_posix())
    try:
        with engine.connect() as conn:
            assert_portfolio_schema(conn)
            assert_legacy_mirrors(conn)
            counts = {}
            for old_name, new_name in (("rb_head_consumptions", "pf_shared_heads"), ("rb_prediction_slots", "pf_shared_slots"), ("rb_prediction_versions", "pf_shared_versions")):
                old_count = conn.scalar(text("SELECT count(*) FROM " + old_name))
                assert old_count > 0 and old_count == conn.scalar(text("SELECT count(*) FROM " + new_name))
                counts[old_name] = old_count
            assert conn.scalar(text("SELECT count(*) FROM pf_artifacts")) == 0
            assert conn.scalar(text("SELECT count(*) FROM pf_backfill_proofs")) == 1
    finally:
        engine.dispose()
    old_reader = """
import sys
from pathlib import Path
import football_system
from football_system.interfaces.real_bridge_cli import dispatch_real_bridge
assert football_system.__version__=='1.3.1' and Path(sys.argv[1]) in Path(football_system.__file__).parents
assert dispatch_real_bridge(['show','--database-url','sqlite:///'+Path(sys.argv[2]).as_posix(),
    '--evidence-root',sys.argv[3],'--artifact-id',sys.argv[4]])==1
print('ACTUAL_V131_BINARY_UNKNOWN_HEAD_REFUSAL')
"""
    refusal = subprocess.run([sys.executable,"-B","-c",old_reader,str(old),str(path),str(path.parent),legacy["report_id"]],cwd=old,
        env=os.environ|{"PYTHONPATH":str(old/"src"),"PYTHONUTF8":"1"},capture_output=True,text=True,encoding="utf-8",timeout=60)
    assert refusal.returncode==0 and "ACTUAL_V131_BINARY_UNKNOWN_HEAD_REFUSAL" in refusal.stdout,refusal.stdout+refusal.stderr
    assert sqlite_snapshot(path)==after
    # Pure legacy shadows are derived metadata; empty PF business can downgrade.
    command.downgrade(cfg, "8ea7bcd49510")
    assert sqlite_snapshot(path) == before
    command.upgrade(cfg, HEAD)
    command.check(cfg)
    (tmp_path / "portfolio-genuine-backfill-proof.json").write_text(json.dumps(dict(status="PASS_GENUINE_LEGACY_BACKFILL",
        archived_commit="f689ff7340598fd37f061473383461117ecb637d", old_rows_ddl_json_unchanged=True,
        counts=counts, legacy_lifecycle=legacy, actual_v131_binary_unknown_head_refused=True,
        empty_pf_roundtrip=True, production_touched=False), indent=2), encoding="utf-8")


@pytest.mark.parametrize("fault",["partial_backfill","preexisting_partial_schema","missing_legacy_seal"])
def test_migration_refuses_partial_history_and_rolls_back_all_new_ddl_and_claims(tmp_path,fault):
    path,_,_=genuine_database(tmp_path)
    cfg=config(path)
    command.upgrade(cfg,"9fb8cde5a621")
    if fault=="preexisting_partial_schema":
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE pf_artifacts(incomplete TEXT)")
    elif fault=="missing_legacy_seal":
        with sqlite3.connect(path) as conn:
            guards=conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='rb_seals'").fetchall()
            for name,_ in guards:
                conn.execute('DROP TRIGGER "'+name+'"')
            conn.execute("DELETE FROM rb_seals WHERE rowid=(SELECT min(rowid) FROM rb_seals)")
            for _,sql in guards:
                conn.execute(sql)
    def snapshot():
        if fault=="missing_legacy_seal":
            # This deliberately malformed history must retain its exact bytes in
            # the logical dump; the normal helper correctly rejects its bad FK.
            with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as conn:
                return tuple(conn.iterdump())
        return sqlite_snapshot(path)
    before=snapshot()
    seen=[]
    def fail(connection,cursor,statement,parameters,context,executemany):
        if fault=="partial_backfill" and statement.startswith("INSERT INTO pf_shared_slots("):
            seen.append(connection.scalar(text("SELECT count(*) FROM pf_shared_heads")))
            raise ValueError("SYNTHETIC_PARTIAL_BACKFILL_FAILURE")
    event.listen(Engine,"before_cursor_execute",fail)
    try:
        with pytest.raises((ValueError,RuntimeError)):
            command.upgrade(cfg,HEAD)
    finally:
        event.remove(Engine,"before_cursor_execute",fail)
    assert snapshot()==before
    if fault=="partial_backfill":
        assert seen and seen[0]>0
