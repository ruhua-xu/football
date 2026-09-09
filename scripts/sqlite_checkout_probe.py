"""Bounded CI#17 reproduction against the real checkout code and original tests.

Uses in-memory SQLite only. Does not commit a business transaction or change
autocommit/PRAGMAs to manufacture a result. State observations contain no data.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import weakref
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WORKSPACE), str(WORKSPACE / "src")]

import sqlalchemy  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402

from football_system.infrastructure.database import session as database  # noqa: E402
from tests.integration.test_database_schema import (  # noqa: E402
    test_external_sqlite_engines_restore_pragmas_on_every_checkout as original_test,
)


def state(engine):
    callback = database._configure_sqlite_connection
    pool = engine.pool
    result = {
        "pool_class": type(pool).__name__,
        "pool_id": id(pool),
        "registry_contains": event.contains(pool, "checkout", callback),
        "actual_listener_present": callback in pool.dispatch.checkout,
        "actual_listener_count": len(tuple(pool.dispatch.checkout)),
    }
    with engine.connect() as connection:
        raw = connection.connection.dbapi_connection
        result.update(
            dbapi_in_transaction=raw.in_transaction,
            sqlalchemy_in_transaction=connection.in_transaction(),
            isolation_level=raw.isolation_level,
            autocommit=getattr(raw, "autocommit", "PRE_PY312"),
        )
        cursor = raw.cursor()
        try:
            result["foreign_keys"] = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
            result["recursive_triggers"] = cursor.execute("PRAGMA recursive_triggers").fetchone()[0]
        finally:
            cursor.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-defect", action="store_true")
    parser.add_argument("--pairs", type=int, default=40)
    parser.add_argument("--lifetime-iterations", type=int, default=2000)
    args = parser.parse_args()
    if not 1 <= args.pairs <= 200 or not 1 <= args.lifetime_iterations <= 10000:
        parser.error("probe bounds exceeded")
    print(json.dumps({"python": sys.version, "platform": platform.platform(),
                      "sqlalchemy": sqlalchemy.__version__, "sqlite": sqlite3.sqlite_version}), flush=True)
    original_configure = database.configure_sqlite_engine
    original_contains = event.contains
    last_engine = None
    configuring = False
    last_decision = {}
    evidence = None

    def traced(engine):
        nonlocal last_engine, configuring
        last_engine = weakref.ref(engine)
        configuring = True
        try:
            return original_configure(engine)
        finally:
            configuring = False

    def contains(target, identifier, callback):
        result = original_contains(target, identifier, callback)
        if configuring and identifier == "checkout" and callback is database._configure_sqlite_connection:
            last_decision.update(configuration_pool_id=id(target), configuration_registry_contains=result)
        return result

    database.configure_sqlite_engine = traced
    event.contains = contains
    try:
        for index in range(args.pairs):
            for operation in (database.create_schema, database.create_session_factory):
                try:
                    original_test(operation)
                except AssertionError:
                    engine = last_engine() if last_engine is not None else None
                    if engine is None:
                        raise
                    evidence = {"case": operation.__name__, "pair": index, **last_decision, **state(engine)}
                    break
            if evidence is not None:
                break
        if evidence is None:
            for index in range(args.lifetime_iterations):
                engine = create_engine("sqlite:///:memory:")
                traced(engine)
                callback = database._configure_sqlite_connection
                if callback not in engine.pool.dispatch.checkout:
                    evidence = {"case": "pool_lifetime", "iteration": index, **last_decision, **state(engine)}
                    break
                engine.dispose()
    finally:
        database.configure_sqlite_engine = original_configure
        event.contains = original_contains
    if evidence is not None:
        print(json.dumps(evidence, sort_keys=True), flush=True)
        print("::warning title=SQLite checkout defect reproduced::" + json.dumps(evidence, sort_keys=True), flush=True)
        proved = (evidence.get("configuration_registry_contains") is True
                  and evidence.get("configuration_pool_id") == evidence["pool_id"]
                  and evidence["actual_listener_present"] is False
                  and evidence["dbapi_in_transaction"] is False
                  and evidence["foreign_keys"] == 0)
        return 0 if args.expect_defect and proved else 1
    print("::notice title=SQLite checkout probe::No defect observed in bounded original-test and pool-lifetime probes", flush=True)
    return 1 if args.expect_defect else 0


if __name__ == "__main__":
    raise SystemExit(main())
