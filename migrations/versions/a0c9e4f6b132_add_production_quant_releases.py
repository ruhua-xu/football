"""Add sealed production history, approval, release and target graphs.

Revision ID: a0c9e4f6b132
Revises: 9b8d3e5f0a21
"""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.production_quant_schema import (
    PRODUCTION_QUANT_TABLES,
    production_quant_tables_v1,
    production_quant_trigger_sql_v1,
)

revision = "a0c9e4f6b132"
down_revision = "9b8d3e5f0a21"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("production quant persistence supports SQLite only")
    tables = production_quant_tables_v1(sa.MetaData(), sa.DateTime(timezone=True))
    for table in tables.values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in production_quant_trigger_sql_v1().values():
        op.execute(statement)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline production downgrade cannot verify empty lineage")
    for table in PRODUCTION_QUANT_TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError(
                "cannot downgrade while immutable production lineage exists"
            )
    for name in production_quant_trigger_sql_v1():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in reversed(PRODUCTION_QUANT_TABLES):
        op.drop_table(table)
