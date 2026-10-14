"""Process-local immutable empty-schema images; every test owns a new SQLite file.

Only the shared functional fixtures opt in. Migration/upgrade/downgrade tests still
exercise real migrations. No model, approval, source, clock or business row is cached.
"""
from contextlib import closing
from functools import lru_cache
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from football_system.infrastructure.database.session import create_database_engine, create_schema
from football_system.infrastructure.database.migrations import upgrade_database

ROOT=Path(__file__).resolve().parents[2]


def _empty_contract(connection, kind):
    allowed={"training_correction_schema_versions"}
    if kind=="migrated":
        allowed|={"alembic_version","pf_backfill_proofs"}
    tables=[row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for name in tables:
        count=connection.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
        if name in allowed:
            assert count==1,(name,count)
        else:
            assert count==0,(name,"BUSINESS_DATA_MUST_NEVER_BE_CACHED")
    assert connection.execute("SELECT preserved_result_count FROM training_correction_schema_versions").fetchone()[0]==0
    assert connection.execute("PRAGMA foreign_key_check").fetchall()==[]
    assert connection.execute("PRAGMA integrity_check").fetchall()==[("ok",)]


@lru_cache(maxsize=2)
def _image(kind):
    assert kind in {"metadata","migrated"}
    # The source is built from current real code once in each worker process.
    # Cached bytes, not a Connection/Engine/Session/path, are shared afterward.
    with tempfile.TemporaryDirectory(prefix="fs-empty-") as folder:
        path=Path(folder)/"seed.sqlite"
        if kind=="migrated":
            upgrade_database("sqlite:///"+path.as_posix(),ROOT/"alembic.ini")
        else:
            engine=create_database_engine("sqlite:///"+path.as_posix())
            try:
                create_schema(engine)
            finally:
                engine.dispose()
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            _empty_contract(connection,kind)
            raw=connection.serialize()
    return raw,hashlib.sha256(raw).digest()


def fresh_empty_database(path, *, kind="metadata"):
    """Return False for an existing file; never overwrite or rebind any database."""
    path=Path(path)
    if path.exists():
        return False
    assert path.parent.is_dir()
    assert not any((p/".git").exists() for p in (path.parent.resolve(),*path.parent.resolve().parents)),"test DB must be outside Git"
    raw,digest=_image(kind)
    assert hashlib.sha256(raw).digest()==digest
    with path.open("xb") as stream:
        stream.write(raw)
    return True
