"""Add OFP source provenance for real model pins without changing legacy FKs."""
from alembic import op
import sqlalchemy as sa

revision = "8ea7bcd49510"
down_revision = "7d96abc3840f"
branch_labels = None
depends_on = None


def upgrade():
    from football_system.infrastructure.database.ofp_real_model_pin_schema import ofp_real_model_pin_table, install_ofp_real_model_pin_triggers
    connection = op.get_bind()
    ofp_real_model_pin_table(sa.MetaData()).create(connection, checkfirst=True)
    install_ofp_real_model_pin_triggers(connection)


def downgrade():
    connection = op.get_bind()
    # A deleted companion in a damaged DB must not make an OFP pin downgradeable.
    if connection.scalar(sa.text("SELECT count(*) FROM ofp_real_model_pins")) or connection.scalar(sa.text(
            "SELECT count(*) FROM rb_artifacts WHERE schema_version='REAL_MODEL_PIN_V1' AND json_extract(artifact_json,'$.model_source.source_type')='OPENFOOTBALL'")):
        raise RuntimeError("populated OpenFootball real model pins cannot be downgraded")
    from football_system.infrastructure.database.ofp_real_model_pin_schema import ofp_real_model_pin_triggers
    for name in ofp_real_model_pin_triggers():
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS "+name)
    op.drop_table("ofp_real_model_pins")
