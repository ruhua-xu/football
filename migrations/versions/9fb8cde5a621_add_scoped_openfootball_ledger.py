"""Add independent qualified competition/instance/model provenance ledgers."""
from alembic import op
import sqlalchemy as sa

revision = "9fb8cde5a621"
down_revision = "8ea7bcd49510"
branch_labels = None
depends_on = None


def upgrade():
    from football_system.infrastructure.database.openfootball_scoped_schema import TABLES, scoped_tables, install_scoped_triggers
    connection = op.get_bind()
    # Alembic owns commit/rollback and the head update; SQLite DDL needs BEGIN.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    tables = scoped_tables(sa.MetaData())
    for name in TABLES:
        tables[name].create(connection, checkfirst=True)
    install_scoped_triggers(connection)


def downgrade():
    from football_system.infrastructure.database.openfootball_scoped_schema import TABLES, scoped_triggers
    connection = op.get_bind()
    # Alembic owns commit/rollback and the head update; SQLite DDL needs BEGIN.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if any(connection.scalar(sa.text("SELECT count(*) FROM " + name)) for name in TABLES) or connection.scalar(sa.text(
        "SELECT count(*) FROM rb_artifacts WHERE schema_version='REAL_MODEL_PIN_V1' AND json_extract(artifact_json,'$.model_source.source_type')='OPENFOOTBALL_QUALIFIED_V2'")):
        raise RuntimeError("populated scoped OpenFootball ledger cannot be downgraded")
    for name in scoped_triggers():
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS " + name)
    for name in reversed(TABLES):
        op.drop_table(name)
