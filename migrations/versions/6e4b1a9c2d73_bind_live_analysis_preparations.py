"""bind live analysis preparations

Revision ID: 6e4b1a9c2d73
Revises: 3cb19bcbdd88
Create Date: 2026-09-04 16:00:00.000000
"""

from typing import Sequence, Union, cast

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Connection

from football_system.infrastructure.database.immutability import (
    install_live_analysis_run_preparation_v1_triggers,
)


revision: str = "6e4b1a9c2d73"
down_revision: Union[str, None] = "3cb19bcbdd88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _require_sqlite_dialect()
    op.create_table(
        "live_analysis_run_preparations",
        sa.Column("analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("preparation_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preparation_id"],
            ["live_analysis_preparations.preparation_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("analysis_run_id"),
    )
    op.create_index(
        "ix_live_analysis_run_preparation_preparation",
        "live_analysis_run_preparations",
        ["preparation_id"],
        unique=False,
    )
    install_live_analysis_run_preparation_v1_triggers(
        cast(Connection, _AlembicConnection())
    )


def downgrade() -> None:
    _require_sqlite_dialect()
    _require_empty_lineage()
    op.execute("DROP TRIGGER IF EXISTS trg_analysis_runs_completion_live_preparation")
    for suffix in (
        "lineage_insert",
        "append_only_delete",
        "append_only_update",
        "immutable_insert_existing",
    ):
        op.execute(
            "DROP TRIGGER IF EXISTS "
            f"trg_live_analysis_run_preparations_{suffix}"
        )
    op.drop_index(
        "ix_live_analysis_run_preparation_preparation",
        table_name="live_analysis_run_preparations",
    )
    op.drop_table("live_analysis_run_preparations")


class _AlembicConnection:
    dialect = type("_Dialect", (), {"name": "sqlite"})()

    def exec_driver_sql(self, statement: str) -> None:
        op.execute(statement)


def _require_empty_lineage() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline live-analysis-preparation downgrade is unsupported because "
            "lineage must be checked"
        )
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM live_analysis_run_preparations)")
    ):
        raise RuntimeError(
            "cannot downgrade live-analysis-preparation schema while immutable "
            "lineage exists"
        )


def _require_sqlite_dialect() -> None:
    dialect = op.get_context().dialect.name
    if dialect != "sqlite":
        raise RuntimeError(
            f"Unsupported database backend '{dialect}'; "
            "football-system v0.4.0 supports SQLite only."
        )
