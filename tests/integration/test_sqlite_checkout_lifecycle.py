"""Checkout contracts using only synthetic SQLite databases under tmp_path."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool, QueuePool, SingletonThreadPool, StaticPool

from football_system.infrastructure.database import session as database


@pytest.fixture(
    params=[{}, {"isolation_level": "DEFERRED"}], ids=["default", "deferred"]
)
def connect_args(request):
    return request.param


@pytest.fixture(
    params=[StaticPool, SingletonThreadPool, QueuePool, NullPool],
    ids=lambda poolclass: poolclass.__name__,
)
def engine(request, tmp_path, connect_args):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'synthetic-checkout.db').as_posix()}",
        poolclass=request.param,
        connect_args=connect_args,
    )

    @event.listens_for(engine, "connect")
    def disable_pragmas(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=OFF").close()
        dbapi_connection.execute("PRAGMA recursive_triggers=OFF").close()

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "accept_engine",
    [
        database.configure_sqlite_engine,
        database.create_schema,
        database.create_session_factory,
    ],
)
def test_checkout_ignores_registry_false_positive(engine, monkeypatch, accept_engine):
    callback = database._configure_sqlite_connection
    original_contains = event.contains

    def stale_contains(target, identifier, listener):
        if target is engine.pool and identifier == "checkout" and listener is callback:
            return True
        return original_contains(target, identifier, listener)

    # Force the observed registry/dispatch disagreement without relying on ID reuse.
    monkeypatch.setattr(event, "contains", stale_contains)
    assert event.contains(engine.pool, "checkout", callback) is True
    assert tuple(engine.pool.dispatch.checkout).count(callback) == 0
    with engine.connect() as connection:
        raw = connection.connection.dbapi_connection
        assert raw.in_transaction is False
        assert connection.in_transaction() is False
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert raw.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        connection.exec_driver_sql(
            "CREATE TABLE checkout_parent (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE checkout_child (parent_id INTEGER REFERENCES checkout_parent(id))"
        )
        connection.commit()

    accept_engine(engine)
    assert tuple(engine.pool.dispatch.checkout).count(callback) == 1
    for _ in range(2):
        with engine.connect() as connection:
            raw = connection.connection.dbapi_connection
            assert raw.in_transaction is False
            assert connection.in_transaction() is False
            assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert raw.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
            with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
                connection.exec_driver_sql("INSERT INTO checkout_child VALUES (999)")
            connection.rollback()
            assert (
                connection.exec_driver_sql("SELECT count(*) FROM checkout_child").scalar()
                == 0
            )
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("PRAGMA recursive_triggers=OFF")
            assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
            assert raw.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
    assert tuple(engine.pool.dispatch.checkout).count(callback) == 1


def test_disposed_and_copied_pools_configure_exactly_once(engine):
    callback = database._configure_sqlite_connection
    statements = []

    @event.listens_for(engine, "connect")
    def trace_configuration(dbapi_connection, connection_record):
        dbapi_connection.set_trace_callback(statements.append)

    database.configure_sqlite_engine(engine)
    for transition in ("initial", "dispose", "recreate"):
        previous_pool = engine.pool
        if transition == "dispose":
            engine.dispose()
        elif transition == "recreate":
            engine.pool = previous_pool.recreate()
            previous_pool.dispose()
        if transition != "initial":
            assert engine.pool is not previous_pool
        assert tuple(engine.pool.dispatch.checkout).count(callback) == 1
        database.configure_sqlite_engine(engine)
        database.create_session_factory(engine)
        database.configure_sqlite_engine(engine)
        assert tuple(engine.pool.dispatch.checkout).count(callback) == 1

        for _ in range(2):
            statements.clear()
            with engine.connect() as connection:
                assert statements.count("PRAGMA foreign_keys=ON") == 1
                assert statements.count("PRAGMA recursive_triggers=ON") == 1
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                assert (
                    connection.exec_driver_sql("PRAGMA recursive_triggers").scalar() == 1
                )
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.exec_driver_sql("PRAGMA recursive_triggers=OFF")


@pytest.mark.parametrize("foreign_keys", [0, 1], ids=["fk_off", "fk_on"])
def test_configuration_preserves_active_business_transaction(engine, foreign_keys):
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE checkout_business (id INTEGER PRIMARY KEY)"
        )
        connection.commit()
        raw = connection.connection.dbapi_connection
        raw.execute(f"PRAGMA foreign_keys={foreign_keys}").close()
        connection.exec_driver_sql("INSERT INTO checkout_business VALUES (1)")
        assert raw.in_transaction is True
        transaction = connection.get_transaction()
        assert transaction is not None and transaction.is_active
        settings = (raw.isolation_level, raw.autocommit)
        statements = []
        raw.set_trace_callback(statements.append)
        try:
            database.configure_sqlite_engine(engine)
            if foreign_keys:
                engine.pool.dispatch.checkout(raw, None, None)
            else:
                # Invoke dispatch directly so pool invalidation cannot hide a
                # callback that committed or rolled back the caller's work.
                with pytest.raises(RuntimeError, match="foreign_keys"):
                    engine.pool.dispatch.checkout(raw, None, None)
            assert raw.in_transaction is True
            assert connection.get_transaction() is transaction
            assert transaction.is_active
            assert (raw.isolation_level, raw.autocommit) == settings
            assert statements and all(sql.startswith("PRAGMA ") for sql in statements)
        finally:
            raw.set_trace_callback(None)

        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == foreign_keys
        if foreign_keys:
            assert raw.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
        assert connection.exec_driver_sql("SELECT id FROM checkout_business").all() == [
            (1,)
        ]
        with closing(sqlite3.connect(engine.url.database)) as observer:
            assert observer.execute("SELECT id FROM checkout_business").fetchall() == []
            connection.rollback()
            assert (
                connection.exec_driver_sql("SELECT id FROM checkout_business").all() == []
            )
            assert observer.execute("SELECT id FROM checkout_business").fetchall() == []


@pytest.mark.parametrize("pragma", ["foreign_keys", "recursive_triggers"])
def test_configuration_fails_closed_when_pragma_assignment_is_ignored(engine, pragma):
    with engine.connect() as connection:
        raw = connection.connection.dbapi_connection

        def authorize(action, name, value, database_name, trigger_name):
            if action == sqlite3.SQLITE_PRAGMA and name == pragma and value is not None:
                return sqlite3.SQLITE_IGNORE
            return sqlite3.SQLITE_OK

        raw.set_authorizer(authorize)
        try:
            database.configure_sqlite_engine(engine)
            with pytest.raises(RuntimeError, match=pragma):
                engine.pool.dispatch.checkout(raw, None, None)
            assert raw.in_transaction is False
            assert raw.execute(f"PRAGMA {pragma}").fetchone()[0] == 0
        finally:
            raw.set_authorizer(None)
