"""Add fixed OpenFootball production binding without widening observed V1."""

from alembic import op
from alembic.operations import Operations, ops
import sqlalchemy as sa

from football_system.infrastructure.database.openfootball_production_schema import (
    OFP_TABLES, openfootball_production_tables, openfootball_production_triggers,
)

revision = "7d96abc3840f"
down_revision = "6c859ab273fe"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("OpenFootball production binding supports SQLite only")
    metadata = sa.MetaData()
    sa.Table("matches", metadata, sa.Column("internal_match_id", sa.String(160), primary_key=True))
    for table in openfootball_production_tables(metadata).values():
        Operations(op.get_context()).invoke(ops.CreateTableOp.from_table(table))
    for statement in openfootball_production_triggers().values():
        op.execute(statement)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("offline downgrade cannot establish an empty OpenFootball ledger")
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if any(connection.scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {name})")) for name in OFP_TABLES):
                raise RuntimeError("cannot downgrade populated OpenFootball production binding")
            for name in openfootball_production_triggers():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            for name in reversed(OFP_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {name}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
