"""Meaningful feedback/cache integrity and immutable-fixture isolation checks."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest
from sqlalchemy import text

from devtools import evidence, gates, wheel_cache
from devtools.pytest_metrics import pytest_collection_modifyitems
from football_system.infrastructure.database.session import create_database_engine,create_schema
from tests.support.empty_database import fresh_empty_database,_image

ROOT=Path(__file__).resolve().parents[2]


def test_order_permutation_is_repeatable_complete_and_does_not_touch_application_rng():
    import random
    from types import SimpleNamespace
    def ordered(seed):
        config=SimpleNamespace(getoption=lambda name:seed)
        items=[SimpleNamespace(nodeid=str(i)) for i in range(30)]
        pytest_collection_modifyitems(config,items)
        return [i.nodeid for i in items]
    state=random.getstate()
    assert ordered(137)==ordered(137) and ordered(137)!=ordered(271)
    assert set(ordered(137))=={str(i) for i in range(30)}
    assert random.getstate()==state


def test_full_release_cannot_be_narrowed_to_an_area():
    assert gates.selections("FULL_RELEASE",None)==["tests","devtools/tests"]
    with pytest.raises(ValueError,match="CANNOT_BE_NARROWED"):
        gates.selections("FULL_RELEASE","portfolio")


@pytest.mark.parametrize("area",sorted(gates.PATTERNS))
def test_each_fast_area_has_real_tests_and_integration_keeps_full_modules(area):
    for mode in ("FAST","INTEGRATION"):
        selected=gates.selections(mode,area)
        assert selected and all((ROOT/p).exists() for p in selected)
        assert set(gates.FREEZE)<=set(selected)


def fake_proof(root):
    identity=dict(workspace=dict(clean=True,commit="a"*40,tree="b"*40),environment=dict(python="test",utc_date="2030-01-01"),configuration=dict(mode="FAST",seed=7))
    raw=b"<testsuites><testsuite><testcase name='one'/></testsuite></testsuites>"
    (root/"junit.xml").write_bytes(raw)
    value=dict(status="PASS",identity=identity,reusable_for_development=True,tests=1,failed=0,steps=[dict(returncode=0)],
        artifacts=[dict(path="junit.xml",sha256=evidence.digest(raw))])
    (root/"receipt.json").write_text(json.dumps(value),encoding="utf-8")
    return identity


def test_development_pass_reuse_requires_exact_environment_configuration_and_intact_artifacts(tmp_path):
    identity=fake_proof(tmp_path)
    assert evidence.verified_reuse(tmp_path,identity,mode="FAST")
    for section,key,value in (("workspace","commit","c"*40),("workspace","tree","c"*40),("workspace","clean",False),
        ("environment","python","different"),("environment","utc_date","2030-01-02"),("configuration","seed",9)):
        changed=deepcopy(identity)
        changed[section][key]=value
        assert evidence.verified_reuse(tmp_path,changed,mode="FAST") is None
    assert evidence.verified_reuse(tmp_path,identity,mode="FULL_RELEASE") is None
    (tmp_path/"junit.xml").write_bytes(b"changed")
    assert evidence.verified_reuse(tmp_path,identity,mode="FAST") is None


@pytest.mark.parametrize("status",["FAIL","INCOMPLETE","CANCELLED"])
def test_nonpassing_or_incomplete_evidence_never_reused(tmp_path,status):
    identity=fake_proof(tmp_path)
    value=json.loads((tmp_path/"receipt.json").read_bytes())
    value["status"]=status
    (tmp_path/"receipt.json").write_text(json.dumps(value),encoding="utf-8")
    assert evidence.verified_reuse(tmp_path,identity,mode="FAST") is None


def snapshot(path):
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as connection:
        schema=connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        rows={r[1]:connection.execute('SELECT * FROM "'+r[1]+'" ORDER BY rowid').fetchall() for r in schema if r[0]=="table" and not r[1].startswith("sqlite_")}
    return schema,rows


@pytest.mark.parametrize("kind",["metadata","migrated"])
def test_images_are_empty_and_each_test_gets_an_independent_mutable_file(tmp_path,kind):
    a,b=tmp_path/"a.sqlite",tmp_path/"b.sqlite"
    assert fresh_empty_database(a,kind=kind) and fresh_empty_database(b,kind=kind)
    assert (a.stat().st_dev,a.stat().st_ino)!=(b.stat().st_dev,b.stat().st_ino)
    before=snapshot(b)
    engine=create_database_engine("sqlite:///"+a.as_posix())
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys"))==1
            assert connection.scalar(text("PRAGMA recursive_triggers"))==1
            connection.execute(text("INSERT INTO competitions(competition_id,canonical_key,name,country_code) VALUES('isolation-a','isolation-a','SYNTHETIC ONLY','GB')"))
    finally:
        engine.dispose()
    assert snapshot(b)==before
    c=tmp_path/"c.sqlite"
    assert fresh_empty_database(c,kind=kind) and snapshot(c)==before
    assert isinstance(_image(kind)[0],bytes)
    assert not fresh_empty_database(a,kind=kind)  # No overwrite of existing mutable state.


def test_cached_metadata_schema_and_marker_rows_match_real_cold_initialization(tmp_path):
    cold,warm=tmp_path/"cold.sqlite",tmp_path/"warm.sqlite"
    engine=create_database_engine("sqlite:///"+cold.as_posix())
    try:
        create_schema(engine)
    finally:
        engine.dispose()
    fresh_empty_database(warm)
    assert snapshot(cold)==snapshot(warm)


def test_separate_processes_never_share_mutable_test_database_state(tmp_path):
    code="""
from pathlib import Path
import sqlite3,sys
from tests.support.empty_database import fresh_empty_database
p=Path(sys.argv[1]); fresh_empty_database(p)
with sqlite3.connect(p) as c:
    assert c.execute('SELECT count(*) FROM competitions').fetchone()[0]==0
    c.execute('INSERT INTO competitions(competition_id,canonical_key,name,country_code) VALUES(?,?,?,?)',(sys.argv[2],sys.argv[2],'SYNTHETIC','GB'))
"""
    children=[subprocess.Popen([sys.executable,"-B","-c",code,str(tmp_path/(name+".sqlite")),name],cwd=ROOT,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE) for name in ("one","two")]
    for child in children:
        out,error=child.communicate(timeout=120)
        assert child.returncode==0,(out,error)
    assert snapshot(tmp_path/"one.sqlite")[1]["competitions"]!=snapshot(tmp_path/"two.sqlite")[1]["competitions"]


def miniature_project(path):
    (path/"src/football_system").mkdir(parents=True)
    (path/"src/football_system/__init__.py").write_text('__version__="0.0.0"\n',encoding="utf-8")
    (path/"README.md").write_text("fixture",encoding="utf-8")
    (path/"alembic.ini").write_text("fixture resource",encoding="utf-8")
    (path/"pyproject.toml").write_text('[project]\nname="football-system"\nversion="0.0.0"\n[tool.setuptools.packages.find]\nwhere=["src"]\n[tool.setuptools.data-files]\nfootball_system_resources=["alembic.ini"]\n',encoding="utf-8")


def test_identical_wheel_inputs_build_once_and_changed_input_or_corruption_never_reuses(tmp_path,monkeypatch):
    project=tmp_path/"project"
    miniature_project(project)
    monkeypatch.setattr(wheel_cache,"environment_identity",lambda:dict(utc_date="2030-01-01",python="fixed",backend="fixed"))
    builds=[]
    def build(folder,configuration):
        builds.append(folder)
        with zipfile.ZipFile(folder/"football_system-0.0.0-py3-none-any.whl","w") as archive:
            archive.writestr("football_system/__init__.py",(project/"src/football_system/__init__.py").read_bytes())
            archive.writestr("football_system-0.0.0.data/data/football_system_resources/alembic.ini",(project/"alembic.ini").read_bytes())
            archive.writestr("football_system-0.0.0.dist-info/METADATA","Name: football-system\r\nVersion: 0.0.0\r\n")
    first=wheel_cache.ensure_wheel(project,tmp_path/"cache",builder=build)
    second=wheel_cache.ensure_wheel(project,tmp_path/"cache",builder=build)
    assert len(builds)==1 and first["wheel_sha256"]==second["wheel_sha256"] and second["status"]=="REUSED_EXACT_WHEEL"
    (project/"alembic.ini").write_text("changed resource",encoding="utf-8")
    third=wheel_cache.ensure_wheel(project,tmp_path/"cache",builder=build)
    assert len(builds)==2 and third["build_key"]!=second["build_key"]
    Path(third["wheel"]).write_bytes(b"corrupt")
    with pytest.raises(AssertionError):
        wheel_cache.ensure_wheel(project,tmp_path/"cache",builder=build)
