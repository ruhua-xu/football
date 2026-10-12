"""Add Portfolio B graphs and atomic shared legacy/PF ownership; preserve old rows."""
from alembic import op
import sqlalchemy as sa

revision = "a0c9def6b732"
down_revision = "9fb8cde5a621"
branch_labels = None
depends_on = None


def upgrade():
    from football_system.infrastructure.database.portfolio_schema import TABLES, portfolio_tables, install_portfolio_triggers, backfill_legacy_ownership, assert_portfolio_schema
    connection = op.get_bind()
    # SQLite's legacy driver mode does not start a transaction for DDL. Ensure
    # schema, triggers, complete backfill and proof are one writer transaction.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.scalar(sa.text("SELECT count(*) FROM sqlite_master WHERE name LIKE 'pf_%' OR name LIKE 'trg_pf_%'")):
        raise RuntimeError("partial or preexisting Portfolio schema requires investigation")
    tables = portfolio_tables(sa.MetaData())
    for name in TABLES:
        tables[name].create(connection)
    install_portfolio_triggers(connection)
    assert_portfolio_schema(connection)
    backfill_legacy_ownership(connection)


def downgrade():
    from football_system.infrastructure.database.portfolio_schema import TABLES, portfolio_triggers, assert_legacy_mirrors
    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.scalar(sa.text("SELECT count(*) FROM pf_artifacts")) or connection.scalar(sa.text("SELECT count(*) FROM pf_receipts")) or connection.scalar(sa.text("SELECT count(*) FROM pf_rejections")):
        raise RuntimeError("populated Portfolio B artifacts cannot be downgraded")
    if connection.scalar(sa.text("SELECT count(*) FROM pf_shared_heads WHERE portfolio_lock_id IS NOT NULL")) or connection.scalar(sa.text("SELECT count(*) FROM pf_shared_slots WHERE initial_portfolio_lock_id IS NOT NULL")):
        raise RuntimeError("populated Portfolio B ownership cannot be downgraded")
    assert_legacy_mirrors(connection)
    for name in portfolio_triggers():
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS " + name)
    for name in reversed(TABLES):
        op.drop_table(name)
