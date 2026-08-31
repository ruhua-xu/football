"""add offline analysis packet and LLM review bridge

Revision ID: 7a2c5e8f9b31
Revises: 4f9b2d7c1a60
Create Date: 2026-08-31 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a2c5e8f9b31"
down_revision: Union[str, None] = "4f9b2d7c1a60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POST_RUN_PARENT_LOOKUPS = {
    "analysis_packets": "SELECT 1 FROM analysis_runs r WHERE r.analysis_run_id = NEW.parent_analysis_run_id AND r.status = 'COMPLETED'",
    "llm_review_artifacts": "SELECT 1 FROM analysis_runs r WHERE r.analysis_run_id = NEW.parent_analysis_run_id AND r.status = 'COMPLETED'",
}


def upgrade() -> None:
    op.create_table(
        "analysis_packets",
        sa.Column("packet_id", sa.String(length=160), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("generated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("packet_json", sa.Text(), nullable=False),
        sa.Column("packet_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("length(packet_hash) = 64", name="ck_packet_hash_length"),
        sa.ForeignKeyConstraint(
            ["parent_analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("packet_id"),
        sa.UniqueConstraint(
            "parent_analysis_run_id",
            "schema_version",
            name="uq_analysis_packet_run_schema",
        ),
        sa.UniqueConstraint(
            "packet_id",
            "parent_analysis_run_id",
            "packet_hash",
            name="uq_analysis_packet_binding",
        ),
    )
    op.create_table(
        "llm_review_artifacts",
        sa.Column("review_artifact_id", sa.String(length=160), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("packet_id", sa.String(length=160), nullable=False),
        sa.Column("packet_hash", sa.String(length=64), nullable=False),
        sa.Column("review_schema_version", sa.String(length=80), nullable=False),
        sa.Column("imported_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_review_json", sa.Text(), nullable=False),
        sa.Column("raw_review_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_review_json", sa.Text(), nullable=False),
        sa.Column("normalized_review_hash", sa.String(length=64), nullable=False),
        sa.Column("validator_version", sa.String(length=80), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(packet_hash) = 64", name="ck_review_packet_hash_length"
        ),
        sa.CheckConstraint(
            "length(raw_review_hash) = 64", name="ck_review_raw_hash_length"
        ),
        sa.CheckConstraint(
            "length(normalized_review_hash) = 64",
            name="ck_review_normalized_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["packet_id", "parent_analysis_run_id", "packet_hash"],
            [
                "analysis_packets.packet_id",
                "analysis_packets.parent_analysis_run_id",
                "analysis_packets.packet_hash",
            ],
            ondelete="RESTRICT",
            name="fk_review_packet_binding",
        ),
        sa.PrimaryKeyConstraint("review_artifact_id"),
        sa.UniqueConstraint(
            "packet_id",
            "normalized_review_hash",
            "validator_version",
            name="uq_llm_review_normalized",
        ),
    )
    _install_triggers(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    _drop_triggers(connection)
    op.drop_table("llm_review_artifacts")
    op.drop_table("analysis_packets")


def _install_triggers(connection: object) -> None:
    for table_name, parent_lookup in POST_RUN_PARENT_LOOKUPS.items():
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER trg_{table_name}_completed_parent_insert
            BEFORE INSERT ON {table_name}
            WHEN NOT EXISTS ({parent_lookup})
            BEGIN
                SELECT RAISE(ABORT, 'post-run artifact requires a completed AnalysisRun');
            END
            """
        )
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN SELECT RAISE(ABORT, 'post-run artifacts are append-only'); END
                """
            )


def _drop_triggers(connection: object) -> None:
    for table_name in POST_RUN_PARENT_LOOKUPS:
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_completed_parent_insert"
        )
        for action in ("update", "delete"):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{action}"
            )
