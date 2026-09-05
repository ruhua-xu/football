from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from football_system.infrastructure.database.immutability import (
    install_sqlite_immutability_triggers,
)
from football_system.infrastructure.database.models import Base


def create_database_engine(database_url: str, echo: bool = False) -> Engine:
    url = require_sqlite_database_url(database_url)
    ensure_sqlite_database_parent(url)
    kwargs: dict[str, object] = {"echo": echo}
    kwargs["connect_args"] = {"check_same_thread": False}
    if url.database in {None, "", ":memory:"}:
        kwargs["poolclass"] = StaticPool
    return configure_sqlite_engine(create_engine(database_url, **kwargs))


def configure_sqlite_engine(engine: Engine) -> Engine:
    _require_sqlite_backend(engine.dialect.name)
    if not event.contains(engine.pool, "checkout", _configure_sqlite_connection):
        event.listen(engine.pool, "checkout", _configure_sqlite_connection)
    return engine


def create_schema(engine: Engine) -> None:
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        install_sqlite_immutability_triggers(connection)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    configure_sqlite_engine(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def require_sqlite_database_url(database_url: str) -> URL:
    url = make_url(database_url)
    _require_sqlite_backend(url.get_backend_name())
    return url


def ensure_sqlite_database_parent(url: URL) -> None:
    if url.database not in {None, "", ":memory:"}:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def _require_sqlite_backend(backend: str) -> None:
    if backend != "sqlite":
        raise ValueError(
            f"Unsupported database backend '{backend}'; "
            "football-system v0.5.0 supports SQLite only."
        )


def _configure_sqlite_connection(
    dbapi_connection: object,
    connection_record: object,
    connection_proxy: object,
) -> None:
    del connection_record, connection_proxy
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA recursive_triggers=ON")
    finally:
        cursor.close()
