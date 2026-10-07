"""Additive schema upgrades preserve genuine legacy pins and reject data loss."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
from alembic import command
from sqlalchemy import inspect, text

from tests.integration.test_market_v2_upgrade import config_for
from tests.integration.openfootball_upgrade_support import sqlite_snapshot
from tests.integration import test_openfootball_production_binding as synthetic_data
from tests.integration.ofp_real_pin_fixtures import through_openfootball_binding,qualify_synthetic_source
from tests.integration.test_ofp_real_model_pin import create_bridge,pin_request
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.ofp_real_model_pin_schema import HEAD, TABLE, assert_ofp_real_model_pin_schema
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from scripts.real_bridge_acceptance import AT
from football_system.domain.archive import canonical_json

ROOT=Path(__file__).resolve().parents[2]
source_graph=synthetic_data.graph


def test_fresh_upgrade_empty_downgrade_and_reupgrade(tmp_path):
    path=tmp_path/"fresh-synthetic.sqlite"
    cfg=config_for(path)
    command.upgrade(cfg,"7d96abc3840f")
    old=sqlite_snapshot(path)
    command.upgrade(cfg,HEAD)
    command.check(cfg)
    after=sqlite_snapshot(path)
    assert after["head"]==HEAD
    for key,value in old["schema"].items():
        assert after["schema"][key]==value
    for key,value in old["rows"].items():
        assert after["rows"][key]==value and after["layout"][key]==old["layout"][key]
    assert set(after["rows"])-set(old["rows"])=={TABLE}
    engine=create_database_engine("sqlite:///"+path.as_posix())
    with engine.connect() as conn:
        assert_ofp_real_model_pin_schema(conn)
    assert {f["referred_table"] for f in inspect(engine).get_foreign_keys(TABLE)}=={"rb_model_pins","ofp_artifacts"}
    engine.dispose()
    command.downgrade(cfg,"7d96abc3840f")
    assert sqlite_snapshot(path)==old
    command.upgrade(cfg,HEAD)
    command.check(cfg)


def test_genuine_v120_legacy_pin_bytes_and_anchor_replay_survive_upgrade(tmp_path):
    old=tmp_path/"v120"
    old.mkdir()
    raw=subprocess.check_output(["git","archive","v1.2.0","src","config","migrations","alembic.ini","scripts/real_bridge_acceptance.py"],cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        archive.extractall(old,filter="data")
    path=tmp_path/"legacy-populated.sqlite"
    code="""
import hashlib,json,sys
from pathlib import Path
from datetime import timedelta
import football_system
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.domain.archive import canonical_json
from scripts.real_bridge_acceptance import fixed_state,AT
root=Path(sys.argv[1]);db=Path(sys.argv[2]);assert root in Path(football_system.__file__).parents
url='sqlite:///'+db.as_posix();upgrade_database(url,root/'alembic.ini')
engine=create_database_engine(url);sessions=create_session_factory(engine)
clock=SyntheticProspectiveClock(AT+timedelta(minutes=1))
proof=b'SELF_AUTHORED_LEGACY_SYNTHETIC_ONLY';(db.parent/'legacy-proof.txt').write_bytes(proof)
repo=SqlAlchemyRealBridgeRepository(sessions,evidence_root=db.parent,clock=clock)
def call(op,key,**values):return repo.execute(op,REQUEST_TYPES[op](request_key=key,**values))
program=call('program','program',observation_program_id='genuine-v120-legacy-only',competition_ids=('rb-competition',),season_id='2026/27',scope_reference='SYNTHETIC_SOFTWARE_ACCEPTANCE',provenance='SYNTHETIC_SOFTWARE_ACCEPTANCE')
ad=call('admission','ad',program_id=program.artifact_id,kind='MODEL',source_identity='legacy-fixed-state',state='ADMITTED',effective_at_utc=AT,expires_at_utc=AT+timedelta(days=20),rights_reference='synthetic',verified_by='synthetic',evidence_file='legacy-proof.txt',evidence_hash=hashlib.sha256(proof).hexdigest())
pin=call('model-pin','pin',program_id=program.artifact_id,admission_id=ad.artifact_id,source_identity='legacy-fixed-state',scope_match_ids=('rb-match-a',),test_state=fixed_state())
policy=call('policy','policy',program_id=program.artifact_id,result_source_identity='synthetic-result',maximum_odds_age_seconds=3600,minimum_bookmaker_count=2)
anchor=call('anchor','anchor',program_id=program.artifact_id,policy_id=policy.artifact_id,model_pin_id=pin.artifact_id,starts_at_utc=AT+timedelta(minutes=2),ends_at_utc=AT+timedelta(days=1))
print(json.dumps(dict(pin_id=pin.artifact_id,pin_json=canonical_json(pin),anchor_id=anchor.artifact_id,anchor_json=canonical_json(anchor))))
engine.dispose()
"""
    result=subprocess.run([sys.executable,"-B","-c",code,str(old),str(path)],cwd=old,
        env=os.environ|{"PYTHONPATH":str(old/"src"),"PYTHONDONTWRITEBYTECODE":"1","PYTHONIOENCODING":"utf-8"},capture_output=True,text=True,encoding="utf-8",timeout=600)
    assert result.returncode==0,result.stdout+result.stderr
    prior=json.loads(result.stdout.splitlines()[-1])
    before=sqlite_snapshot(path)
    cfg=config_for(path)
    command.upgrade(cfg,HEAD)
    after=sqlite_snapshot(path)
    for key,value in before["schema"].items():
        assert after["schema"][key]==value
    for key,value in before["rows"].items():
        assert after["rows"][key]==value
    assert after["artifacts"]==before["artifacts"]
    engine=create_database_engine("sqlite:///"+path.as_posix())
    repo=SqlAlchemyRealBridgeRepository(create_session_factory(engine),evidence_root=tmp_path,clock=SyntheticProspectiveClock(AT))
    for kind in ("pin","anchor"):
        assert canonical_json(repo.load(prior[kind+"_id"]))==prior[kind+"_json"]
        assert repo.audit(prior[kind+"_id"])["status"]=="AUDIT_PASS"
    engine.dispose()
    command.downgrade(cfg,"7d96abc3840f")
    assert sqlite_snapshot(path)==before


def test_populated_ofp_pin_downgrade_refused_without_changes(source_graph,monkeypatch):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    pin=g.bridge.execute("model-pin",pin_request(g))
    before=sqlite_snapshot(g.database)
    with pytest.raises(RuntimeError,match="populated OpenFootball real model pins"):
        command.downgrade(config_for(g.database),"7d96abc3840f")
    assert sqlite_snapshot(g.database)==before
    assert g.bridge.load(pin.artifact_id)==pin
    with g.engine.connect() as conn:
        assert conn.scalar(text("PRAGMA foreign_keys"))==1
        assert conn.execute(text("PRAGMA foreign_key_check")).all()==[]


def test_7d_legacy_compatibility_does_not_grant_ofp_pin_without_extension(source_graph,monkeypatch):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    cfg=config_for(g.database)
    command.downgrade(cfg,"7d96abc3840f")
    with pytest.raises(ValueError,match="OFP_MODEL_PIN_SCHEMA_OR_GUARDS_REQUIRED"):
        g.bridge.execute("model-pin",pin_request(g))
    command.upgrade(cfg,HEAD)
    pin=g.bridge.execute("model-pin",pin_request(g,request_key="pin-after-explicit-schema-upgrade"))
    assert pin.schema_version=="REAL_MODEL_PIN_V1" and g.bridge.load(pin.artifact_id)==pin
