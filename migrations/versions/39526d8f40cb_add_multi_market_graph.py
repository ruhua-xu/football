"""Add versioned multi-market, V4 review and expanded system pass graphs."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.market_v2_schema import (
    MARKET_V2_TABLES,
    market_v2_tables,
    market_v2_triggers,
)

revision = "39526d8f40cb"
down_revision = "28415c7e39ba"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("multi-market supports SQLite only")
    for table in market_v2_tables(sa.MetaData()).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for sql in market_v2_triggers().values():
        op.execute(sql)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline downgrade cannot verify populated market graph")
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if any(
                conn.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {name})"))
                for name in MARKET_V2_TABLES
            ):
                raise RuntimeError("cannot downgrade populated multi-market graph")
            for name in market_v2_triggers():
                conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            for name in reversed(MARKET_V2_TABLES):
                conn.exec_driver_sql(f"DROP TABLE {name}")
            conn.exec_driver_sql("COMMIT")
        except BaseException:
            conn.exec_driver_sql("ROLLBACK")
            raise
