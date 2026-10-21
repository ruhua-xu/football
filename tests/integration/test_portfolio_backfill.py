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


def genuine_database(tmp_path, *, postlock=False, release="1.3.1"):
    references={"1.3.0":"0f677e834d5e2b6eb092ecaf86e4c70a60e38e98","1.3.1":"f689ff7340598fd37f061473383461117ecb637d"}
    old = tmp_path / ("published-v131" if release=="1.3.1" else "published-v130")
    old.mkdir()
    raw = subprocess.check_output(["git", "archive", references[release], "src", "config", "migrations", "scripts", "data/fixtures", "alembic.ini", "pyproject.toml"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        archive.extractall(old, filter="data")
    work = tmp_path / "old-lifecycle"
    code = """
from datetime import timedelta
import json,socket,sys
from pathlib import Path
from sqlalchemy import text
import football_system
from scripts.real_bridge_acceptance import exercise
from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
assert football_system.__version__==sys.argv[4] and Path(sys.argv[1]) in Path(football_system.__file__).parents
def denied(*args,**kwargs):raise AssertionError('GENUINE_LEGACY_ACCEPTANCE_NETWORK_FORBIDDEN')
socket.socket.connect=denied
if sys.argv[3]=='False':
    summary=exercise(Path(sys.argv[2]))
else:
    # Stop the real old lifecycle at its first committed lock, while all source
    # quotes and pre-kickoff times remain valid for a genuine post-lock revision.
    class Captured(Exception):pass
    held={}
    original=SqlAlchemyRealBridgeRepository.execute
    def capture(self,operation,request):
        value=original(self,operation,request)
        if operation=='lock' and request.request_key=='lock':
            held.update(repository=self,lock=value)
            raise Captured()
        return value
    SqlAlchemyRealBridgeRepository.execute=capture
    try:exercise(Path(sys.argv[2]))
    except Captured:pass
    finally:SqlAlchemyRealBridgeRepository.execute=original
    repo,lock=held['repository'],held['lock']
    repo.clock.at+=timedelta(seconds=1)
    with repo.sessions() as session:
        initial=json.loads(session.execute(text("SELECT request_json FROM rb_receipts WHERE request_key='prepare'")).scalar_one())
    initial.update(request_key='old-binary-postlock-prepare',supersedes_lock_id=lock.artifact_id)
    run=repo.execute('prepare',REQUEST_TYPES['prepare'].model_validate(initial))
    repo.clock.at+=timedelta(seconds=1)
    revision=repo.execute('lock',REQUEST_TYPES['lock'](request_key='old-binary-postlock-lock',run_id=run.artifact_id,
        invalidation_reason='self-authored old-binary prediction revision'))
    with repo.sessions() as session:
        versions=[list(row) for row in session.execute(text('SELECT version,previous_version,count(*) FROM rb_prediction_versions GROUP BY version,previous_version ORDER BY version'))]
        assert versions==[[0,None,2],[1,0,2]]
        assert not session.execute(text('PRAGMA foreign_key_check')).all()
    assert repo.audit(revision.artifact_id)['status']=='AUDIT_PASS'
    summary=dict(provenance='SYNTHETIC_SOFTWARE_ACCEPTANCE',migration_head='8ea7bcd49510',lock_id=lock.artifact_id,
        replacement_lock_id=revision.artifact_id,versions=versions,old_api_postlock_revision=True,real_observation_count=0)
print(json.dumps(summary))
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(old), str(work), str(postlock), release], cwd=old,
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


@pytest.mark.parametrize("postlock",[False,True])
def test_complete_genuine_v131_ledger_backfill_and_empty_portfolio_roundtrip(tmp_path,postlock):
    path,legacy,old = genuine_database(tmp_path,postlock=postlock)
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
            if postlock:
                assert [list(row) for row in conn.execute(text("SELECT version,previous_version,count(*) FROM pf_shared_versions GROUP BY version,previous_version ORDER BY version"))]==legacy["versions"]
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
    refusal = subprocess.run([sys.executable,"-B","-c",old_reader,str(old),str(path),str(path.parent),legacy.get("report_id",legacy["lock_id"])],cwd=old,
        env=os.environ|{"PYTHONPATH":str(old/"src"),"PYTHONUTF8":"1"},capture_output=True,text=True,encoding="utf-8",timeout=60)
    assert refusal.returncode==0 and "ACTUAL_V131_BINARY_UNKNOWN_HEAD_REFUSAL" in refusal.stdout,refusal.stdout+refusal.stderr
    assert sqlite_snapshot(path)==after
    if postlock:
        remaining=[]
        def fail_after_mirror_delete(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith("DELETE FROM pf_shared_versions WHERE version="):
                remaining.append(connection.scalar(text("SELECT count(*) FROM pf_shared_versions")))
                raise ValueError("SYNTHETIC_EMPTY_DOWNGRADE_FAILURE")
        event.listen(Engine,"after_cursor_execute",fail_after_mirror_delete)
        try:
            with pytest.raises(ValueError,match="SYNTHETIC_EMPTY_DOWNGRADE_FAILURE"):
                command.downgrade(cfg,"8ea7bcd49510")
        finally:
            event.remove(Engine,"after_cursor_execute",fail_after_mirror_delete)
        assert remaining==[2] and sqlite_snapshot(path)==after
    # Pure legacy shadows are derived metadata; empty PF business can downgrade.
    command.downgrade(cfg, "8ea7bcd49510")
    assert sqlite_snapshot(path) == before
    command.upgrade(cfg, HEAD)
    command.check(cfg)
    (tmp_path / "portfolio-genuine-backfill-proof.json").write_text(json.dumps(dict(status="PASS_GENUINE_LEGACY_BACKFILL",
        archived_commit="f689ff7340598fd37f061473383461117ecb637d", old_rows_ddl_json_unchanged=True,
        counts=counts, legacy_lifecycle=legacy, actual_v131_binary_unknown_head_refused=True,postlock_version_chain=postlock,
        derived_mirror_delete_failure_rolls_back=postlock,
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
