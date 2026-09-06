"""Add append-only offline quant integrity pilots.

Revision ID: 9b8d3e5f0a21
Revises: 8a7c2f4e9b10
"""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.quant_integrity_schema import (
    QUANT_INTEGRITY_TABLES,
    quant_integrity_tables_v1,
    quant_integrity_trigger_sql_v1,
)

revision = "9b8d3e5f0a21"
down_revision = "8a7c2f4e9b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("quant integrity pilots support SQLite only")
    tables = quant_integrity_tables_v1(sa.MetaData(), sa.DateTime(timezone=True))
    for table in tables.values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in quant_integrity_trigger_sql_v1().values():
        op.execute(statement)


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("offline pilot downgrade cannot verify empty lineage")
    for table in QUANT_INTEGRITY_TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError("cannot downgrade while immutable pilot lineage exists")
    for name in quant_integrity_trigger_sql_v1():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in reversed(QUANT_INTEGRITY_TABLES):
        op.drop_table(table)
