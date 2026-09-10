"""Bind observed structural integrity to the existing production lifecycle."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.observed_quant_schema import (
    OBSERVED_QUANT_TABLES,
    observed_quant_tables_v1,
    observed_quant_trigger_sql_v1,
)

revision = "17304b6d28a9"
down_revision = "062f3a5c1798"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("observed quant supports SQLite only")
    for table in observed_quant_tables_v1(sa.MetaData()).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for name, sql in observed_quant_trigger_sql_v1().items():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute(sql)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline downgrade cannot verify immutable observed lineage")
    from football_system.infrastructure.database.versioned_quant_schema import (
        versioned_quant_trigger_sql_v2,
    )

    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for table in OBSERVED_QUANT_TABLES:
                if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
                    raise RuntimeError(
                        "cannot downgrade while observed quant lineage exists"
                    )
            previous = versioned_quant_trigger_sql_v2()
            for name in observed_quant_trigger_sql_v1():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
                if name in previous:
                    connection.exec_driver_sql(previous[name])
            for table in reversed(OBSERVED_QUANT_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {table}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
