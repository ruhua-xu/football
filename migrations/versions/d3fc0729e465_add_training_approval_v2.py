"""Add authorized approval V2 envelopes without rewriting immutable V1 rows.

Revision ID: d3fc0729e465
Revises: c2ebf618d354
"""

from alembic import op
import sqlalchemy as sa

from football_system.infrastructure.database.approval_v2_schema import (
    approval_v2_trigger_sql,
)
from football_system.infrastructure.database.production_quant_schema import (
    production_quant_trigger_sql_v1,
)

revision = "d3fc0729e465"
down_revision = "c2ebf618d354"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        raise RuntimeError("approval V2 persistence supports SQLite only")
    for name, statement in approval_v2_trigger_sql().items():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute(statement)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline approval V2 downgrade cannot verify immutable lineage"
        )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM training_history_approval_events "
            "WHERE json_extract(artifact_json, '$.schema_version') IS NOT 'TRAINING_HISTORY_APPROVAL_V1')"
        )
    ):
        raise RuntimeError("cannot downgrade while immutable approval V2 exists")
    previous = production_quant_trigger_sql_v1()
    for name in approval_v2_trigger_sql():
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
        if name in previous:
            op.execute(previous[name])
