"""Bind pinned production inference atomically to completed analyses.

Revision ID: b1dae507c243
Revises: a0c9e4f6b132
"""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.production_inference_schema import (
    PRODUCTION_INFERENCE_TABLES,
    production_inference_tables_v1,
    production_inference_trigger_sql_v1,
)

revision = "b1dae507c243"
down_revision = "a0c9e4f6b132"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("production inference persistence supports SQLite only")
    tables = production_inference_tables_v1(sa.MetaData(), sa.DateTime(timezone=True))
    for table in tables.values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in production_inference_trigger_sql_v1().values():
        op.execute(statement)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline production inference downgrade cannot verify empty lineage"
        )
    for table in PRODUCTION_INFERENCE_TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError(
                "cannot downgrade while immutable production inference lineage exists"
            )
    for name in production_inference_trigger_sql_v1():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in reversed(PRODUCTION_INFERENCE_TABLES):
        op.drop_table(table)
