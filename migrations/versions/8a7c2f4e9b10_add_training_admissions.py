"""Add controlled rights/capture and training fact admissions.

Revision ID: 8a7c2f4e9b10
Revises: 6e4b1a9c2d73
"""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.training_admission_schema import (
    TRAINING_ADMISSION_TABLES,
    training_admission_tables_v1,
    training_admission_trigger_sql_v1,
)

revision = "8a7c2f4e9b10"
down_revision = "6e4b1a9c2d73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("training admissions support SQLite only")
    tables = training_admission_tables_v1(sa.MetaData(), sa.DateTime(timezone=True))
    for table in tables.values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in training_admission_trigger_sql_v1().values():
        op.execute(statement)


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("offline training downgrade cannot verify empty lineage")
    for table in TRAINING_ADMISSION_TABLES:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError(
                "cannot downgrade training admissions while immutable lineage exists"
            )
    for name in training_admission_trigger_sql_v1():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in reversed(TRAINING_ADMISSION_TABLES):
        op.drop_table(table)
