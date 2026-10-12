"""Actual independent writer processes and durable crash rollback."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import text
from tests.integration.portfolio_fixtures import setup, prepare

ROOT=Path(__file__).resolve().parents[2]


def start(g,kind,operation,request,*,crash=False):
    child=subprocess.Popen([sys.executable,"-B","-m","tests.integration.portfolio_process_worker"],cwd=ROOT,
        env=os.environ|{"PYTHONUTF8":"1","PYTHONDONTWRITEBYTECODE":"1"},stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True,encoding="utf-8")
    job=dict(classification="SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE",root=str(g.root),clock=g.clock.value.isoformat(),
        authorities=g.evidence.trusted_authorities,kind=kind,operation=operation,request=request,crash=crash)
    child.stdin.write(json.dumps(job)+"\n")
    child.stdin.flush()
    assert child.stdout.readline().strip()=="READY"
    return child


@pytest.mark.parametrize("operation",["lock","replace"])
def test_two_independent_processes_have_one_successful_legacy_or_portfolio_owner(tmp_path,operation):
    g=setup(tmp_path)
    try:
        prepare(g,"2X1")
        source=g.sources[0]
        request=dict(request_key="legacy-process",run_id=source.run.artifact_id) if operation=="lock" else dict(
            request_key="replacement-process",**source.prepare,expected_head_id=source.run.artifact_id,reason="synthetic cross-process race")
        children=[start(g,"PF","lock",dict(request_key="pf-process",assembly_id=g.assembly.artifact_id)),start(g,"RB",operation,request)]
        for child in children:
            child.stdin.write("START\n")
            child.stdin.flush()
        outcomes=[]
        for child in children:
            out,error=child.communicate(timeout=180)
            assert child.returncode==0,error
            outcomes.append(json.loads(out)["outcome"])
        assert sorted(outcomes)==["COMMITTED","REJECTED"]
        with g.sessions() as s:
            locks=s.scalar(text("SELECT count(*) FROM pf_locks"))
            assert s.scalar(text("SELECT count(*) FROM pf_budget_claims"))==locks
            assert s.scalar(text("SELECT count(*) FROM pf_shared_heads"))==(2 if locks else 1)
            assert not s.execute(text("PRAGMA foreign_key_check")).all()
    finally:
        g.engine.dispose()


def test_process_death_after_final_seal_before_commit_leaves_no_partial_claims(tmp_path):
    g=setup(tmp_path)
    try:
        prepare(g,"2X1")
        child=start(g,"PF","lock",dict(request_key="crashing-pf",assembly_id=g.assembly.artifact_id),crash=True)
        _,error=child.communicate("START\n",timeout=180)
        assert child.returncode==73,error
        g.engine.dispose()
        with g.sessions() as s:
            for name in ("pf_locks","pf_shared_heads","pf_shared_slots","pf_shared_versions","pf_budget_claims"):
                assert s.scalar(text("SELECT count(*) FROM "+name))==0
            assert s.scalar(text("SELECT count(*) FROM pf_receipts WHERE request_key='crashing-pf'"))==0
            assert not s.execute(text("PRAGMA foreign_key_check")).all()
        assert g.pf_call("lock","crashing-pf",assembly_id=g.assembly.artifact_id).schema_version=="ATOMIC_PORTFOLIO_LOCK_V1"
    finally:
        g.engine.dispose()
