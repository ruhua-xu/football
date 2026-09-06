"""Add mandatory approved packet audit companions and downstream guards.

Revision ID: c2ebf618d354
Revises: b1dae507c243
"""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.production_audit_schema import (
    PRODUCTION_AUDIT_TABLES,
    production_audit_tables_v1,
    production_audit_trigger_sql_v1,
)

revision = "c2ebf618d354"
down_revision = "b1dae507c243"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("production audit persistence supports SQLite only")
    for table in production_audit_tables_v1(
        sa.MetaData(), sa.DateTime(timezone=True)
    ).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in production_audit_trigger_sql_v1().values():
        op.execute(statement)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline production audit downgrade cannot verify empty lineage"
        )
    for table in PRODUCTION_AUDIT_TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError(
                "cannot downgrade while immutable production audit exists"
            )
    for name in production_audit_trigger_sql_v1():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in reversed(PRODUCTION_AUDIT_TABLES):
        op.drop_table(table)
