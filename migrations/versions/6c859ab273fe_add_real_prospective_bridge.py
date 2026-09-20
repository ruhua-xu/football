"""Add the independent real prospective bridge ledger; preserve all v1.0 objects."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.real_bridge_schema import RB_TABLES, INDEXES, real_bridge_tables, real_bridge_triggers

revision = "6c859ab273fe"
down_revision = "5b748fa162ed"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("real bridge supports SQLite only")
    for table in real_bridge_tables(sa.MetaData()).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in (*real_bridge_triggers().values(), *INDEXES):
        op.execute(statement)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline downgrade cannot verify empty real bridge ledger")
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if any(connection.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {name})")) for name in RB_TABLES):
                raise RuntimeError("cannot downgrade populated real bridge ledger")
            for name in real_bridge_triggers():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            for name in reversed(RB_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {name}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
