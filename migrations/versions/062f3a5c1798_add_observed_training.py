"""Add current-snapshot observed training without converting historical evidence."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.observed_training_schema import (
    OBSERVED_TRAINING_TABLES,
    OBSERVED_CAPTURE_ORDINALS,
    observed_capture_ordinal_backfill_sql_v1,
    observed_training_tables_v1,
    observed_training_trigger_sql_v1,
)
from football_system.infrastructure.database.training_correction_schema import (
    training_correction_trigger_sql_v2,
)

revision = "062f3a5c1798"
down_revision = "f51e294b0687"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("observed training supports SQLite only")
    for table in observed_training_tables_v1(
        sa.MetaData(), sa.DateTime(timezone=True)
    ).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for name, sql in observed_training_trigger_sql_v1().items():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute(sql)
    op.execute(observed_capture_ordinal_backfill_sql_v1())


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline observed downgrade cannot verify empty lineage")
    # This is schema migration only, never a business admission transaction.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for table in OBSERVED_TRAINING_TABLES:
                if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
                    raise RuntimeError(
                        "cannot downgrade while immutable observed lineage exists"
                    )
            previous = training_correction_trigger_sql_v2()
            for name in observed_training_trigger_sql_v1():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
                if name in previous:
                    connection.exec_driver_sql(previous[name])
            for table in reversed(OBSERVED_TRAINING_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {table}")
            connection.exec_driver_sql(f"DROP TABLE {OBSERVED_CAPTURE_ORDINALS}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
