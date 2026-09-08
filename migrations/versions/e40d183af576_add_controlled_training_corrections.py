"""Add controlled immutable training corrections and version normalized guards.

Revision ID: e40d183af576
Revises: d3fc0729e465 (approval-intent migration, maintained separately)
"""

from alembic import op

from football_system.infrastructure.database.training_correction_schema import (
    downgrade_training_corrections_v2,
    upgrade_training_corrections_v2,
)

revision = "e40d183af576"
down_revision = "d3fc0729e465"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline correction migration cannot verify preserved result checksums"
        )
    # SQLite cannot disable FK enforcement inside a transaction. The actual
    # rebuild below starts its own exclusive, rollback-safe transaction.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            upgrade_training_corrections_v2(connection)
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline correction downgrade cannot verify empty controlled lineage"
        )
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            downgrade_training_corrections_v2(connection)
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
