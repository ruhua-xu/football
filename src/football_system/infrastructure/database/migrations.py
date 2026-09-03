from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from football_system.infrastructure.database.session import (
    ensure_sqlite_database_parent,
    require_sqlite_database_url,
)


def upgrade_database(
    database_url: str,
    alembic_ini_path: str | Path = "alembic.ini",
) -> None:
    database = require_sqlite_database_url(database_url)
    ensure_sqlite_database_parent(database)
    ini_path = Path(alembic_ini_path).resolve()
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(ini_path.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
