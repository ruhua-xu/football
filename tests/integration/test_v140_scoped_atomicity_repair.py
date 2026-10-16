"""Actual Alembic DDL/head failures must preserve the complete genuine old ledger."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3

from alembic import command
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from football_system.infrastructure.database import openfootball_scoped_schema as scoped
from football_system.infrastructure.database.migrations import upgrade_database
from tests.integration.openfootball_upgrade_support import sqlite_snapshot
from tests.integration.test_portfolio_backfill import ROOT, config, genuine_database

OLD="8ea7bcd49510"
SCOPED="9fb8cde5a621"
FINAL="a0c9def6b732"


@pytest.fixture(scope="module")
def released_image(tmp_path_factory):
    path,_,_=genuine_database(tmp_path_factory.mktemp("atomicity-v131"))
    snapshot=sqlite_snapshot(path)
    assert snapshot["head"]==OLD and snapshot["rows"]["rb_locks"]
    return path.read_bytes()


def fingerprint(value):
    # Snapshot keys include (type,name) tuples; keep them, definitions and every
    # original row/artifact string without reparsing business JSON.
    return hashlib.sha256(repr(value).encode()).hexdigest()


def checked_snapshot(path):
    value=sqlite_snapshot(path)  # Full schema, rows, FK/index layout and exact artifact JSON.
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        value["sqlite_master"]=connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        value["alembic_version_rows"]=connection.execute("SELECT * FROM alembic_version").fetchall()
        value["integrity_check"]=connection.execute("PRAGMA integrity_check").fetchall()
        value["foreign_key_check"]=connection.execute("PRAGMA foreign_key_check").fetchall()
    assert value["integrity_check"]==[("ok",)] and value["foreign_key_check"]==[]
    return value


class InjectedFailure(RuntimeError):
    pass


@contextmanager
def inject(point,direction):
    seen={"tables":0,"triggers":0,"ddl_transactions":[],"fault":None}
    def fail(connection):
        seen["fault"]=dict(head=connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one(),
            driver_in_transaction=connection.connection.driver_connection.in_transaction,
            foreign_keys=connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
        raise InjectedFailure(point)
    def before(connection,cursor,statement,parameters,context,executemany):
        sql=statement.lstrip().replace('"','')
        if sql.startswith(("CREATE TABLE ofpv2_","DROP TABLE ofpv2_")) or (sql.startswith(("CREATE TRIGGER","DROP TRIGGER")) and "trg_ofpv2_" in sql):
            seen["ddl_transactions"].append(connection.connection.driver_connection.in_transaction)
        if point=="version_before" and sql.startswith("UPDATE alembic_version"):
            fail(connection)
    def after(connection,cursor,statement,parameters,context,executemany):
        sql=statement.lstrip().replace('"','')
        table=sql.startswith("CREATE TABLE ofpv2_" if direction=="upgrade" else "DROP TABLE ofpv2_")
        trigger=sql.startswith("CREATE TRIGGER" if direction=="upgrade" else "DROP TRIGGER") and "trg_ofpv2_" in sql
        seen["tables"]+=int(table)
        seen["triggers"]+=int(trigger)
        if (point=="table" and table and seen["tables"]==2) or (point=="trigger" and trigger and seen["triggers"]==2):
            fail(connection)
        if point=="after_ddl":
            if direction=="upgrade" and trigger and seen["triggers"]==len(scoped.scoped_triggers()):
                fail(connection)
            if direction=="downgrade" and table and seen["tables"]==len(scoped.TABLES):
                fail(connection)
        if point=="version_after" and sql.startswith("UPDATE alembic_version"):
            fail(connection)
    def commit(connection):
        if point=="commit":
            fail(connection)
    listeners=(("before_cursor_execute",before),("after_cursor_execute",after),("commit",commit))
    for name,callback in listeners:
        event.listen(Engine,name,callback)
    try:
        yield seen
    finally:
        for name,callback in listeners:
            event.remove(Engine,name,callback)


@pytest.mark.parametrize("direction",["upgrade","downgrade"])
@pytest.mark.parametrize("point",["table","trigger","after_ddl","version_before","version_after","commit"])
def test_scoped_failure_rolls_back_ddl_rows_constraints_and_head_then_retry_succeeds(tmp_path,released_image,direction,point):
    path=tmp_path/"ledger.sqlite"
    path.write_bytes(released_image)  # Every case owns a NEW physical file; never reuse a failed DB.
    cfg=config(path)
    if direction=="downgrade":
        command.upgrade(cfg,SCOPED)
    before=checked_snapshot(path)
    target=SCOPED if direction=="upgrade" else OLD
    operation=getattr(command,direction)
    with inject(point,direction) as observed,pytest.raises(InjectedFailure,match=point):
        operation(cfg,target)
    after=checked_snapshot(path)
    equal={part:after[part]==before[part] for part in before}
    proof=dict(direction=direction,fault_point=point,observed=observed,equal=equal,before_sha256=fingerprint(before),
        after_failure_sha256=fingerprint(after),original_head=before["head"],head_after_failure=after["head"],
        retry_attempted=False,production_touched=False)
    output=tmp_path/"scoped-atomicity-proof.json"
    output.write_text(json.dumps(proof,indent=2),encoding="utf-8")
    assert all(equal.values()),equal
    assert observed["fault"] and observed["fault"]["foreign_keys"]==1
    assert observed["ddl_transactions"] and all(observed["ddl_transactions"])
    assert observed["fault"]["driver_in_transaction"]
    assert observed["fault"]["head"]==(target if point in {"version_after","commit"} else before["head"])
    # Retry only after proving rollback. Never repair a contaminated failed image
    # and then label that repaired image as proof of atomic rollback.
    operation(cfg,target)
    retried=checked_snapshot(path)
    assert retried["head"]==target
    getattr(command,"downgrade" if direction=="upgrade" else "upgrade")(cfg,before["head"])
    assert checked_snapshot(path)==before
    proof.update(status="PASS_ATOMIC_DDL_HEAD_ROLLBACK_AND_RETRY",retry_attempted=True,retry_head=retried["head"],roundtrip_exact=True)
    output.write_text(json.dumps(proof,indent=2),encoding="utf-8")


@pytest.mark.parametrize("direction",["upgrade","downgrade"])
def test_scoped_entry_respects_an_existing_real_sqlite_transaction(tmp_path,released_image,direction):
    path=tmp_path/"existing-transaction.sqlite"
    path.write_bytes(released_image)
    cfg=config(path)
    if direction=="downgrade":
        command.upgrade(cfg,SCOPED)
    target=SCOPED if direction=="upgrade" else OLD
    starts=[]
    def begin(connection):
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()==1
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        starts.append(connection.connection.driver_connection.in_transaction)
    event.listen(Engine,"begin",begin)
    try:
        getattr(command,direction)(cfg,target)
    finally:
        event.remove(Engine,"begin",begin)
    assert starts and all(starts) and checked_snapshot(path)["head"]==target


def test_fresh_public_entry_and_genuine_legacy_roundtrip_reupgrade(tmp_path,released_image):
    fresh=tmp_path/"fresh.sqlite"
    upgrade_database("sqlite:///"+fresh.as_posix(),ROOT/"alembic.ini")
    command.check(config(fresh))
    assert checked_snapshot(fresh)["head"]==FINAL
    path=tmp_path/"legacy.sqlite"
    path.write_bytes(released_image)
    before=checked_snapshot(path)
    cfg=config(path)
    command.upgrade(cfg,FINAL)
    command.check(cfg)
    command.downgrade(cfg,OLD)
    assert checked_snapshot(path)==before
    command.upgrade(cfg,FINAL)
    command.check(cfg)
    assert checked_snapshot(path)["head"]==FINAL


def test_populated_scoped_downgrade_refusal_preserves_full_database(tmp_path):
    from scripts.scoped_model_acceptance_inputs import graph,source,qualify
    g=graph(tmp_path/"populated")
    try:
        qualify(g,source(g))
        cfg=config(g.database)
        command.downgrade(cfg,SCOPED)  # PF business empty; scoped business populated.
        before=checked_snapshot(g.database)
        with pytest.raises(RuntimeError,match="populated scoped OpenFootball ledger"):
            command.downgrade(cfg,OLD)
        assert checked_snapshot(g.database)==before
    finally:
        g.engine.dispose()
