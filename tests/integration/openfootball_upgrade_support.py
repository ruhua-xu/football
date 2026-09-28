"""Genuine release archives and lossless SQLite schema/row comparison helpers.

Only self-authored synthetic data in pytest sandboxes. Never production evidence.
"""

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from football_system.infrastructure.database.models import Base
from football_system.infrastructure.database.openfootball_production_schema import OFP_TABLES, openfootball_production_triggers

ROOT = Path(__file__).resolve().parents[2]
V110 = "5ed940a8af8077be80549603a2da38aea77fc1bf"


def archive_v110(destination):
    assert subprocess.check_output(["git", "rev-parse", "v1.1.0^{}"], cwd=ROOT).decode().strip() == V110
    destination.mkdir()
    raw = subprocess.check_output(["git", "archive", V110, "src", "config", "data/fixtures", "migrations",
        "scripts/market_expansion_acceptance.py", "scripts/daily_operator.py", "scripts/preparation_inputs.py",
        "daily.cmd", "alembic.ini", "pyproject.toml", "README.md"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        archive.extractall(destination, filter="data")
    return destination


def run_v110(checkout, code, *arguments):
    result = subprocess.run([sys.executable, "-B", "-c", code, str(checkout), *map(str, arguments)], cwd=checkout,
        env=dict(os.environ, PYTHONPATH=str(checkout / "src"), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _row_json(row):
    return json.dumps(tuple({"sqlite_blob_hex": value.hex()} if isinstance(value, bytes) else value for value in row),
        sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sqlite_snapshot(path):
    """DDL bytes + row multiset + every FK/index definition, not reflection order."""
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        schema = {(kind, name): (owner, sql) for kind, name, owner, sql in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name<>'alembic_version' AND name NOT LIKE 'sqlite_stat%'"
        )}
        tables = sorted(name for kind, name in schema if kind == "table")
        rows, layout, artifacts = {}, {}, {}
        for table in tables:
            name = _identifier(table)
            rows[table] = sorted(_row_json(row) for row in conn.execute(f"SELECT * FROM {name}"))
            columns = conn.execute(f"PRAGMA table_xinfo({name})").fetchall()
            # Enumeration order is not an index's identity; keep all actual properties.
            indexes = {row[1]: dict(unique=row[2], origin=row[3], partial=row[4],
                columns=conn.execute(f"PRAGMA index_xinfo({_identifier(row[1])})").fetchall(),
                sql=schema[("index", row[1])][1]) for row in conn.execute(f"PRAGMA index_list({name})").fetchall()}
            layout[table] = dict(columns=columns, foreign_keys=sorted(conn.execute(f"PRAGMA foreign_key_list({name})").fetchall()), indexes=indexes)
            for column in (row[1] for row in columns if row[1] == "artifact_json"):
                values = sorted(row[0] for row in conn.execute(f"SELECT {_identifier(column)} FROM {name} WHERE {_identifier(column)} IS NOT NULL"))
                artifacts[table] = values  # Original strings, NEVER deserialize/re-serialize.
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    return dict(schema=schema, rows=rows, layout=layout, artifacts=artifacts, head=head)


def _new_ddl_normalized(sql):
    # SQLite omits IF NOT EXISTS in sqlite_master. Only new-object formatting is
    # normalized; every pre-existing DDL string is compared byte-for-byte.
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", sql.strip().removesuffix(";"))
    return "".join(part if i % 2 else re.sub(r"\s+", " ", part).replace(" IF NOT EXISTS ", " ")
        for i, part in enumerate(parts))


def _new_table_signature(sql):
    """Ignore table-constraint emission order only, never columns or literals."""
    value = _new_ddl_normalized(sql)
    start, end = value.index("("), value.rindex(")")
    inner, pieces, begin, depth, quote, i = value[start + 1:end], [], 0, 0, None, 0
    while i < len(inner):
        character = inner[i]
        if quote:
            if character == quote:
                if i + 1 < len(inner) and inner[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif character in ("'", '"'):
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            pieces.append(inner[begin:i].strip())
            begin = i + 1
        i += 1
    assert quote is None and depth == 0
    pieces.append(inner[begin:].strip())
    prefixes = ("CONSTRAINT ", "PRIMARY KEY", "UNIQUE", "CHECK", "FOREIGN KEY")
    columns = tuple(p for p in pieces if not p.startswith(prefixes))
    constraints = tuple(sorted(p for p in pieces if p.startswith(prefixes)))
    return value[:start].strip(), columns, constraints, value[end + 1:].strip()


def _assert_new_constraint_indexes(table, actual):
    """Every PK/UNIQUE still needs its actual physical, unique nonpartial index."""
    expected = [("pk", tuple(c.name for c in table.primary_key.columns))]
    expected += [("u", tuple(c.name for c in constraint.columns)) for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)]
    found = []
    for name, index in actual.items():
        assert name.startswith("sqlite_autoindex_" + table.name + "_")
        assert index["unique"] == 1 and index["partial"] == 0 and index["sql"] is None
        key_columns = [row for row in index["columns"] if row[5] == 1]
        assert all(row[3] == 0 and row[4] == "BINARY" for row in key_columns)
        found.append((index["origin"], tuple(row[2] for row in key_columns)))
    assert sorted(found) == sorted(expected), ("NEW_CONSTRAINT_INDEX_MISMATCH", table.name)


def assert_openfootball_additive(before, after):
    assert set(before["schema"]) <= set(after["schema"])
    for key, value in before["schema"].items():
        assert after["schema"][key] == value, ("OLD_DDL_CHANGED", key)
    for name in before["rows"]:
        assert after["rows"][name] == before["rows"][name], ("OLD_ROWS_CHANGED", name)
        assert after["layout"][name] == before["layout"][name], ("OLD_CONSTRAINT_OR_INDEX_CHANGED", name)
    assert all(after["artifacts"][name] == values for name, values in before["artifacts"].items())
    added = set(after["schema"]) - set(before["schema"])
    triggers = openfootball_production_triggers()
    assert {name for kind, name in added if kind == "table"} == set(OFP_TABLES)
    assert {name for kind, name in added if kind == "trigger"} == set(triggers)
    assert not any(kind == "view" for kind, _ in added)
    assert all(after["schema"][key][0] in OFP_TABLES for key in added if key[0] == "index")
    for name in OFP_TABLES:
        expected = str(sa.schema.CreateTable(Base.metadata.tables[name]).compile(dialect=sqlite.dialect()))
        assert _new_table_signature(after["schema"][("table", name)][1]) == _new_table_signature(expected), name
        _assert_new_constraint_indexes(Base.metadata.tables[name], after["layout"][name]["indexes"])
        assert not after["rows"][name]
    for name, expected in triggers.items():
        assert _new_ddl_normalized(after["schema"][("trigger", name)][1]) == _new_ddl_normalized(expected), name
    return dict(old_tables=len(before["rows"]), old_rows=sum(map(len, before["rows"].values())),
        old_objects=len(before["schema"]), old_artifact_json_count=sum(map(len, before["artifacts"].values())),
        old_artifact_json_sha256=hashlib.sha256(json.dumps(before["artifacts"], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        added_tables=sorted(OFP_TABLES), added_triggers=sorted(triggers),
        added_indexes=sorted(name for kind, name in added if kind == "index"),
        old_rows_unchanged=True, old_ddl_bytes_unchanged=True, old_constraints_indexes_unchanged=True,
        old_artifact_json_bytes_unchanged=True, reflection_order_ignored_only_as_enumeration=True,
        new_table_constraint_emission_order_normalized=True, all_new_pk_unique_indexes_verified=True)
