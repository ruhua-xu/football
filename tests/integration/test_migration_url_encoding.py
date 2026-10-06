"""Migration URL interpolation preserves literal/escaped percent paths."""
from pathlib import Path
import sqlite3

from sqlalchemy.engine import URL

from football_system.infrastructure.database.migrations import upgrade_database


def test_rendered_sqlite_url_with_percent_is_not_config_interpolation(tmp_path):
    root=tmp_path/"synthetic % path"
    root.mkdir()
    database=root/"self-authored.sqlite"
    url=URL.create("sqlite+pysqlite",database=str(database)).render_as_string(hide_password=False)
    upgrade_database(url,Path(__file__).resolve().parents[2]/"alembic.ini")
    with sqlite3.connect(database.as_uri()+"?mode=ro",uri=True) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]=="8ea7bcd49510"
        assert connection.execute("PRAGMA integrity_check").fetchall()==[("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall()==[]
