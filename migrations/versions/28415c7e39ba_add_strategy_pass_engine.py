"""Versioned strategy profiles, constituent tickets and backtest settlements."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.strategy_pass_schema import (
    STRATEGY_TABLES,
    strategy_pass_tables_v1,
    strategy_pass_trigger_sql_v1,
)

revision = "28415c7e39ba"
down_revision = "17304b6d28a9"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("strategy pass supports SQLite only")
    for table in strategy_pass_tables_v1(sa.MetaData()).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for sql in strategy_pass_trigger_sql_v1().values():
        op.execute(sql)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline downgrade cannot verify sealed strategy lineage")
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if any(
                connection.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {t})"))
                for t in STRATEGY_TABLES
            ):
                raise RuntimeError("cannot downgrade while strategy artifacts exist")
            for name in strategy_pass_trigger_sql_v1():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            for name in reversed(STRATEGY_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {name}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
