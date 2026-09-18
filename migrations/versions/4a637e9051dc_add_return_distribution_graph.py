"""Add exact portfolio return distribution and marginal optimizer graphs."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.return_distribution_schema import (
    RETURN_TABLES, return_distribution_tables, return_distribution_triggers,
)

revision = "4a637e9051dc"
down_revision = "39526d8f40cb"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("return distribution supports SQLite only")
    for table in return_distribution_tables(sa.MetaData()).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for sql in return_distribution_triggers().values():
        op.execute(sql)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline downgrade cannot verify populated return graph")
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if any(connection.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {name})")) for name in RETURN_TABLES):
                raise RuntimeError("cannot downgrade populated return distribution graph")
            for name in return_distribution_triggers():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            for name in reversed(RETURN_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {name}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
