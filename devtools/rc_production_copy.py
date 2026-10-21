"""Verify a pinned production snapshot only in a new inactive private sandbox.

Run using the RC's installed-wheel interpreter. The active source is read as
bytes, never opened through SQLite; every SQL connection targets a private copy.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sqlite3
import sys

import football_system

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import event,text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from scripts.operator_release_upgrade import database_state  # noqa: E402
from tests.integration.openfootball_upgrade_support import sqlite_snapshot  # noqa: E402
from football_system.infrastructure.database.session import create_database_engine,create_session_factory  # noqa: E402
from football_system.infrastructure.database.portfolio_schema import assert_portfolio_schema,assert_legacy_mirrors  # noqa: E402
from football_system.infrastructure.database.openfootball_scoped_schema import assert_scoped_head  # noqa: E402
from football_system.infrastructure.database.real_bridge_head import assert_real_bridge_head  # noqa: E402
from football_system.interfaces.cli import _resource_root  # noqa: E402

OLD="8ea7bcd49510"
SCOPED="9fb8cde5a621"
HEAD="a0c9def6b732"


def identity(path):
    value=path.stat()
    return [value.st_dev,value.st_ino]


def snapshot(path):
    value=sqlite_snapshot(path)
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        value["sqlite_master"]=connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        value["version_rows"]=connection.execute("SELECT * FROM alembic_version").fetchall()
        value["integrity_check"]=connection.execute("PRAGMA integrity_check").fetchall()
        value["foreign_key_check"]=connection.execute("PRAGMA foreign_key_check").fetchall()
    assert value["integrity_check"]==[("ok",)] and value["foreign_key_check"]==[]
    return value


def digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()


def configuration(path,resources):
    cfg=Config(str(resources/"alembic.ini"))
    cfg.set_main_option("prepend_sys_path","")
    cfg.set_main_option("script_location",str(resources/"migrations"))
    cfg.set_main_option("sqlalchemy.url",("sqlite:///"+path.as_uri()+"?mode=rw&uri=true").replace("%","%%"))
    return cfg


def validate(path,head):
    engine=create_database_engine("sqlite:///"+path.as_posix())
    try:
        with create_session_factory(engine)() as session:
            assert session.scalar(text("PRAGMA foreign_keys"))==1
            assert_real_bridge_head(session)
            if head in {SCOPED,HEAD}:
                assert_scoped_head(session)
            if head==HEAD:
                assert_portfolio_schema(session.connection())
                assert_legacy_mirrors(session.connection())
    finally:
        engine.dispose()


def fault_case(path,cfg,direction,point,prefix,target):
    before=snapshot(path)
    observed={"ddl_transactions":[],"fault":None}
    def fail(connection):
        observed["fault"]=dict(head=connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one(),
            active_transaction=connection.connection.driver_connection.in_transaction,foreign_keys=connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
        raise RuntimeError("RC_COPY_INJECTED_"+point)
    def before_sql(connection,cursor,statement,parameters,context,executemany):
        sql=statement.lstrip().replace('"','')
        ddl=(sql.startswith(("CREATE TABLE "+prefix,"DROP TABLE "+prefix)) or
            (sql.startswith(("CREATE TRIGGER","DROP TRIGGER")) and "trg_"+prefix in sql))
        if ddl:
            observed["ddl_transactions"].append(connection.connection.driver_connection.in_transaction)
        if point=="version_before" and sql.startswith("UPDATE alembic_version"):
            fail(connection)
    def after_sql(connection,cursor,statement,parameters,context,executemany):
        sql=statement.lstrip().replace('"','')
        if point=="table" and sql.startswith(("CREATE TABLE " if direction=="upgrade" else "DROP TABLE ")+prefix):
            fail(connection)
        if point=="trigger" and sql.startswith("CREATE TRIGGER" if direction=="upgrade" else "DROP TRIGGER") and "trg_"+prefix in sql:
            fail(connection)
        if point=="version_after" and sql.startswith("UPDATE alembic_version"):
            fail(connection)
    def commit(connection):
        if point=="commit":
            fail(connection)
    hooks=(("before_cursor_execute",before_sql),("after_cursor_execute",after_sql),("commit",commit))
    for name,callback in hooks:
        event.listen(Engine,name,callback)
    try:
        try:
            getattr(command,direction)(cfg,target)
        except RuntimeError as error:
            assert str(error)=="RC_COPY_INJECTED_"+point
        else:
            raise AssertionError("RC_COPY_FAULT_NOT_REACHED")
    finally:
        for name,callback in hooks:
            event.remove(Engine,name,callback)
    after=snapshot(path)
    equal={part:after[part]==before[part] for part in before}
    assert all(equal.values()),equal
    assert observed["fault"]["active_transaction"] and observed["fault"]["foreign_keys"]==1
    assert observed["ddl_transactions"] and all(observed["ddl_transactions"])
    getattr(command,direction)(cfg,target)  # Same-file retry only after complete rollback proof.
    assert snapshot(path)["head"]==target
    getattr(command,"downgrade" if direction=="upgrade" else "upgrade")(cfg,before["head"])
    assert snapshot(path)==before
    return dict(direction=direction,target=target,fault_point=point,checkpoint=before["head"],observed=observed,equal=equal,
        before_sha256=digest(before),after_failure_sha256=digest(after),retry_and_roundtrip=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary",type=Path,required=True)
    parser.add_argument("--private-root",type=Path,required=True)
    parser.add_argument("--work",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    prefix=Path(sys.prefix).resolve()
    assert football_system.__version__==importlib.metadata.version("football-system")=="1.4.0"
    assert prefix in Path(football_system.__file__).resolve().parents,"INSTALLED_RC_WHEEL_REQUIRED"
    resources=_resource_root().resolve()
    assert resources.is_relative_to(prefix),"INSTALLED_MIGRATION_RESOURCES_REQUIRED"
    boundary=json.loads(args.boundary.read_bytes())
    source=next(Path(name) for name in boundary["files"] if Path(name).name=="production.sqlite")
    expected=boundary["files"][str(source)]
    private=args.private_root.resolve()
    work=args.work.resolve()
    assert private.is_dir() and work.parent==private and not work.exists()
    assert not any((parent/".git").exists() for parent in (private,*private.parents))
    assert not work.is_relative_to(source.parent.parent) and args.output.parent.is_dir() and not args.output.exists()
    def source_bytes():
        assert all(not source.with_name(source.name+suffix).exists() for suffix in ("-wal","-shm","-journal")),"SOURCE_REQUIRES_QUIESCENT_SNAPSHOT"
        before=source.stat()
        raw=source.read_bytes()
        after=source.stat()
        assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
        assert identity(source)==expected["file_identity"] and hashlib.sha256(raw).hexdigest()==expected["sha256"]
        return raw
    seed=source_bytes()
    work.mkdir()
    (work/"INACTIVE_COPY_ONLY.txt").write_text("Private RC verification copy; no active operator installation or production binding.\n",encoding="utf-8")
    def clone(name,raw):
        path=work/(name+".sqlite")
        with path.open("xb") as stream:
            stream.write(raw)
        assert identity(path)!=identity(source) and path.stat().st_nlink==1
        return path
    path=clone("success",seed)
    cfg=configuration(path,resources)
    script=ScriptDirectory.from_config(cfg)
    assert script.get_heads()==[HEAD]
    revision=HEAD
    chain=[]
    while revision!=OLD:
        chain.append(revision)
        revision=script.get_revision(revision).down_revision
        assert revision is not None
    chain=[OLD,*reversed(chain)]
    assert chain==[OLD,SCOPED,HEAD]
    baseline=snapshot(path)
    source_state=database_state(path)
    assert baseline["head"]==OLD
    assert source_state["ofp"]["source_records"]==612 and source_state["ofp"]["exceptions"]==13 and source_state["ofp"]["admitted_facts"]==599
    idle=("rb_programs","rb_model_pins","rb_epochs","rb_runs","rb_locks","pv_epochs","pv_runs","pv_locks")
    assert all(source_state["tables"][name]["count"]==0 for name in idle)
    images={OLD:seed}
    checkpoints=[]
    for target in (SCOPED,HEAD):
        command.upgrade(cfg,target)
        actual=snapshot(path)
        validate(path,target)
        assert all(all(actual[part][key]==value for key,value in baseline[part].items()) for part in ("schema","rows","layout","artifacts"))
        state=database_state(path)
        assert state["ofp"]==source_state["ofp"] and state["application_id"]==source_state["application_id"]
        assert all(state["tables"][name]["count"]==0 for name in idle)
        images[target]=path.read_bytes()
        checkpoints.append(dict(head=target,state_sha256=digest(actual),old_ddl_rows_constraints_preserved=True,
            old_roots=state["ofp"],schema_guards="PASS",integrity_check="ok",foreign_key_check=[]))
    command.check(cfg)
    command.downgrade(cfg,OLD)
    assert snapshot(path)==baseline
    command.upgrade(cfg,HEAD)
    command.check(cfg)
    cases=[]
    for revision,parent,table_prefix in ((SCOPED,OLD,"ofpv2_"),(HEAD,SCOPED,"pf_")):
        for direction in ("upgrade","downgrade"):
            for point in ("table","trigger","version_before","version_after","commit"):
                start,target=(parent,revision) if direction=="upgrade" else (revision,parent)
                case=clone(revision+"-"+direction+"-"+point,images[start])
                cases.append(fault_case(case,configuration(case,resources),direction,point,table_prefix,target))
    # A genuine API rejection populates only the test copy's PF rejection ledger;
    # no program, model pin, epoch, run or successful decision/Portfolio lock is created.
    refused=clone("populated-refusal",images[HEAD])
    engine=create_database_engine("sqlite:///"+refused.as_posix())
    from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
    from football_system.infrastructure.database.portfolio_repository import SqlAlchemyPortfolioRepository
    from football_system.infrastructure.files.training_evidence import LocalTrainingEvidence
    from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
    from football_system.application.portfolio_requests import REQUEST_TYPES
    try:
        evidence=work/"synthetic-rejection-evidence"
        evidence.mkdir()
        bridge=SqlAlchemyRealBridgeRepository(create_session_factory(engine),evidence_root=evidence,
            clock=SyntheticProspectiveClock(datetime(2030,1,1,tzinfo=timezone.utc)))
        portfolio=SqlAlchemyPortfolioRepository(bridge,budget_evidence=LocalTrainingEvidence(evidence,trusted_authorities={}))
        try:
            portfolio.execute("lock",REQUEST_TYPES["lock"](request_key="RC_SYNTHETIC_MISSING_ASSEMBLY",assembly_id="SELF_AUTHORED_MISSING"))
        except ValueError:
            pass
        else:
            raise AssertionError("SYNTHETIC_MISSING_ASSEMBLY_MUST_BE_REJECTED")
    finally:
        engine.dispose()
    before=snapshot(refused)
    assert len(before["rows"]["pf_rejections"])==1 and before["rows"]["pf_locks"]==[]
    try:
        command.downgrade(configuration(refused,resources),SCOPED)
    except RuntimeError as error:
        assert "populated Portfolio B artifacts" in str(error)
    else:
        raise AssertionError("POPULATED_DOWNGRADE_MUST_BE_REFUSED")
    assert snapshot(refused)==before
    assert source_bytes()==seed
    result=dict(status="PASS_INACTIVE_PRODUCTION_V130_COPY_UPGRADE",at_utc=datetime.now(timezone.utc).isoformat(),
        active_runtime_version="1.3.0",tested_package_version="1.4.0",installed_package_origin=str(Path(football_system.__file__).resolve()),
        installed_resources=str(resources),source_sha256=expected["sha256"],source_file_identity=expected["file_identity"],
        private_work=str(work),copy_file_identity=identity(path),active_installation_created=False,actual_dag_path=chain,
        baseline_state_sha256=digest(baseline),baseline_table_counts={name:value["count"] for name,value in source_state["tables"].items()},
        production_roots=source_state["ofp"],checkpoints=checkpoints,failure_cases=cases,transaction_unit="ONE_ALEMBIC_REVISION",
        whole_two_revision_operation_single_transaction=False,empty_downgrade_reupgrade=True,populated_downgrade_refusal=True,
        existing_production_legacy_locks=source_state["tables"]["rb_locks"]["count"],
        legacy_nonempty_coverage="Separate genuine v1.3.0 synthetic DecisionLock version0/1 tests and installed-wheel operator acceptance",
        real_lifecycle_records_created=0,synthetic_rejection_created_in_inactive_copy_only=True,production_sql_connections=0,production_writes=0)
    with args.output.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(dict(status=result["status"],path=chain,failure_cases=len(cases),production_writes=0,active_installation_created=False),indent=2))


if __name__=="__main__":
    main()
