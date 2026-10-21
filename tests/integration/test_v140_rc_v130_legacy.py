"""The actual active release is1.3.0: preserve its genuine synthetic lock history."""
import json
import os
import subprocess
import sys

from alembic import command
import pytest
from sqlalchemy import text

from football_system.infrastructure.database.portfolio_schema import assert_portfolio_schema,assert_legacy_mirrors
from football_system.infrastructure.database.session import create_database_engine
from tests.integration.openfootball_upgrade_support import sqlite_snapshot
from tests.integration.test_portfolio_backfill import genuine_database,config


@pytest.mark.parametrize("postlock",[False,True])
def test_genuine_v130_decision_lock_history_upgrades_roundtrips_and_old_binary_refuses_new_head(tmp_path,postlock):
    path,legacy,old=genuine_database(tmp_path,postlock=postlock,release="1.3.0")
    before=sqlite_snapshot(path)
    assert before["head"]=="8ea7bcd49510" and before["rows"]["rb_locks"]
    cfg=config(path)
    command.upgrade(cfg,"head")
    command.check(cfg)
    after=sqlite_snapshot(path)
    assert after["head"]=="a0c9def6b732"
    for part in ("schema","rows","layout","artifacts"):
        assert all(after[part][key]==value for key,value in before[part].items()),part
    engine=create_database_engine("sqlite:///"+path.as_posix())
    try:
        with engine.connect() as connection:
            assert_portfolio_schema(connection)
            assert_legacy_mirrors(connection)
            assert connection.scalar(text("PRAGMA foreign_keys"))==1
            counts={name:connection.scalar(text("SELECT count(*) FROM "+name)) for name in
                ("rb_locks","rb_prediction_versions","pf_shared_heads","pf_shared_slots","pf_shared_versions")}
            assert counts["rb_prediction_versions"]==counts["pf_shared_versions"]>0
    finally:
        engine.dispose()
    code="""
import sys
from pathlib import Path
import football_system
from football_system.interfaces.real_bridge_cli import dispatch_real_bridge
assert football_system.__version__=='1.3.0' and Path(sys.argv[1]) in Path(football_system.__file__).parents
assert dispatch_real_bridge(['show','--database-url','sqlite:///'+Path(sys.argv[2]).as_posix(),
    '--evidence-root',sys.argv[3],'--artifact-id',sys.argv[4]])==1
print('ACTUAL_V130_BINARY_NEW_HEAD_REFUSED')
"""
    result=subprocess.run([sys.executable,"-B","-c",code,str(old),str(path),str(path.parent),legacy.get("report_id",legacy["lock_id"])],cwd=old,
        env=os.environ|{"PYTHONPATH":str(old/"src"),"PYTHONUTF8":"1"},capture_output=True,text=True,encoding="utf-8",timeout=60)
    assert result.returncode==0 and "ACTUAL_V130_BINARY_NEW_HEAD_REFUSED" in result.stdout,result.stdout+result.stderr
    assert sqlite_snapshot(path)==after
    command.downgrade(cfg,"8ea7bcd49510")
    assert sqlite_snapshot(path)==before
    command.upgrade(cfg,"head")
    command.check(cfg)
    (tmp_path/"rc-v130-legacy-proof.json").write_text(json.dumps(dict(status="PASS_GENUINE_V130_LEGACY_UPGRADE",
        release_commit="0f677e834d5e2b6eb092ecaf86e4c70a60e38e98",postlock_version_chain=postlock,counts=counts,
        old_rows_ddl_json_preserved=True,empty_roundtrip=True,actual_v130_unknown_head_refused=True,
        classification="SYNTHETIC_SOFTWARE_ACCEPTANCE",production_touched=False),indent=2),encoding="utf-8")
