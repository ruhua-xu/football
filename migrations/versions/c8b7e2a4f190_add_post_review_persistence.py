"""add post-review fusion and portfolio revision persistence

Revision ID: c8b7e2a4f190
Revises: 9d4e6f1a2c70
Create Date: 2026-08-31 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8b7e2a4f190"
down_revision: Union[str, None] = "9d4e6f1a2c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POST_RUN_PARENT_LOOKUPS = {
    "fusion_runs": (
        "SELECT 1 FROM analysis_runs r JOIN llm_review_artifacts a "
        "ON a.review_artifact_id = NEW.llm_review_artifact_id "
        "WHERE r.analysis_run_id = NEW.parent_analysis_run_id "
        "AND r.status = 'COMPLETED' "
        "AND r.completed_at_utc IS NOT NULL "
        "AND a.parent_analysis_run_id = r.analysis_run_id"
    ),
    "fusion_run_results": (
        "SELECT 1 FROM fusion_runs f "
        "JOIN analysis_runs r ON r.analysis_run_id = f.parent_analysis_run_id "
        "JOIN analysis_run_matches m "
        "ON m.analysis_run_id = f.parent_analysis_run_id "
        "AND m.internal_match_id = NEW.internal_match_id "
        "JOIN final_predictions p "
        "ON p.final_prediction_id = NEW.base_prediction_id "
        "AND p.analysis_run_id = f.parent_analysis_run_id "
        "AND p.internal_match_id = NEW.internal_match_id "
        "WHERE f.fusion_run_id = NEW.fusion_run_id "
        "AND r.status = 'COMPLETED' "
        "AND r.completed_at_utc IS NOT NULL "
        "AND p.market_key = NEW.market_key "
        "AND p.market_type = NEW.market_type "
        "AND (p.handicap_value = NEW.handicap_value OR "
        "(p.handicap_value IS NULL AND NEW.handicap_value IS NULL))"
    ),
    "portfolio_revisions": (
        "SELECT 1 FROM fusion_runs f JOIN analysis_runs r "
        "ON r.analysis_run_id = f.parent_analysis_run_id "
        "WHERE f.fusion_run_id = NEW.fusion_run_id "
        "AND f.parent_analysis_run_id = NEW.parent_analysis_run_id "
        "AND r.status = 'COMPLETED' "
        "AND r.completed_at_utc IS NOT NULL"
    ),
}

POST_RUN_INSERT_CONFLICTS = {
    "fusion_runs": (
        "SELECT 1 FROM fusion_runs f "
        "WHERE f.fusion_run_id = NEW.fusion_run_id "
        "OR (f.parent_analysis_run_id = NEW.parent_analysis_run_id "
        "AND f.llm_review_artifact_id = NEW.llm_review_artifact_id "
        "AND f.fusion_policy = NEW.fusion_policy "
        "AND f.fusion_version = NEW.fusion_version "
        "AND f.config_hash = NEW.config_hash)"
    ),
    "fusion_run_results": (
        "SELECT 1 FROM fusion_run_results x "
        "WHERE x.fusion_result_id = NEW.fusion_result_id "
        "OR (x.fusion_run_id = NEW.fusion_run_id "
        "AND x.internal_match_id = NEW.internal_match_id) "
        "OR (x.fusion_run_id = NEW.fusion_run_id "
        "AND x.base_prediction_id = NEW.base_prediction_id)"
    ),
    "portfolio_revisions": (
        "SELECT 1 FROM portfolio_revisions p "
        "WHERE p.portfolio_revision_id = NEW.portfolio_revision_id "
        "OR (p.fusion_run_id = NEW.fusion_run_id "
        "AND p.revision_policy = NEW.revision_policy "
        "AND p.revision_version = NEW.revision_version "
        "AND p.config_hash = NEW.config_hash)"
    ),
}


def upgrade() -> None:
    op.create_table(
        "fusion_runs",
        sa.Column("fusion_run_id", sa.String(length=160), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("llm_review_artifact_id", sa.String(length=160), nullable=False),
        sa.Column("fusion_policy", sa.String(length=80), nullable=False),
        sa.Column("fusion_version", sa.String(length=40), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(config_hash) = 64", name="ck_fusion_run_config_hash_length"
        ),
        sa.ForeignKeyConstraint(
            ["parent_analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["llm_review_artifact_id"],
            ["llm_review_artifacts.review_artifact_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fusion_run_id"),
        sa.UniqueConstraint(
            "parent_analysis_run_id",
            "llm_review_artifact_id",
            "fusion_policy",
            "fusion_version",
            "config_hash",
            name="uq_fusion_run_idempotency",
        ),
    )
    op.create_table(
        "fusion_run_results",
        sa.Column("fusion_result_id", sa.String(length=160), nullable=False),
        sa.Column("fusion_run_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("market_key", sa.String(length=120), nullable=False),
        sa.Column("market_type", sa.String(length=64), nullable=False),
        sa.Column("handicap_value", sa.Numeric(8, 3), nullable=True),
        sa.Column("base_prediction_id", sa.String(length=160), nullable=False),
        sa.Column("p_base_json", sa.Text(), nullable=False),
        sa.Column("p_llm_json", sa.Text(), nullable=True),
        sa.Column("raw_probability_delta_json", sa.Text(), nullable=True),
        sa.Column("applied_probability_delta_json", sa.Text(), nullable=False),
        sa.Column("confidence_factor", sa.Numeric(18, 12), nullable=False),
        sa.Column("data_quality_factor", sa.Numeric(18, 12), nullable=False),
        sa.Column("p_final_json", sa.Text(), nullable=False),
        sa.Column("fallback_code", sa.String(length=80), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "confidence_factor >= 0 AND confidence_factor <= 1",
            name="ck_fusion_result_confidence_factor",
        ),
        sa.CheckConstraint(
            "data_quality_factor >= 0 AND data_quality_factor <= 1",
            name="ck_fusion_result_data_quality_factor",
        ),
        sa.CheckConstraint(
            "(p_llm_json IS NULL AND raw_probability_delta_json IS NULL) OR "
            "(p_llm_json IS NOT NULL AND raw_probability_delta_json IS NOT NULL)",
            name="ck_fusion_result_llm_delta_pair",
        ),
        sa.CheckConstraint(
            "length(result_hash) = 64", name="ck_fusion_result_hash_length"
        ),
        sa.ForeignKeyConstraint(
            ["fusion_run_id"], ["fusion_runs.fusion_run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_prediction_id"],
            ["final_predictions.final_prediction_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fusion_result_id"),
        sa.UniqueConstraint(
            "fusion_run_id", "internal_match_id", name="uq_fusion_result_match"
        ),
        sa.UniqueConstraint(
            "fusion_run_id",
            "base_prediction_id",
            name="uq_fusion_result_base_prediction",
        ),
    )
    op.create_table(
        "portfolio_revisions",
        sa.Column("portfolio_revision_id", sa.String(length=160), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("fusion_run_id", sa.String(length=160), nullable=False),
        sa.Column("revision_policy", sa.String(length=80), nullable=False),
        sa.Column("revision_version", sa.String(length=40), nullable=False),
        sa.Column("generated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("revision_json", sa.Text(), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "length(config_hash) = 64",
            name="ck_portfolio_revision_config_hash_length",
        ),
        sa.CheckConstraint(
            "length(revision_hash) = 64",
            name="ck_portfolio_revision_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["parent_analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fusion_run_id"], ["fusion_runs.fusion_run_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("portfolio_revision_id"),
        sa.UniqueConstraint(
            "fusion_run_id",
            "revision_policy",
            "revision_version",
            "config_hash",
            name="uq_portfolio_revision_idempotency",
        ),
    )
    _install_triggers(op.get_bind())


def downgrade() -> None:
    _drop_triggers(op.get_bind())
    op.drop_table("portfolio_revisions")
    op.drop_table("fusion_run_results")
    op.drop_table("fusion_runs")


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
                BEGIN
                    SELECT RAISE(ABORT, 'post-run artifacts are append-only');
                END
                """
            )
        conflict_lookup = POST_RUN_INSERT_CONFLICTS[table_name]
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only_insert_existing
            BEFORE INSERT ON {table_name}
            WHEN EXISTS ({conflict_lookup})
            BEGIN
                SELECT RAISE(ABORT, 'post-run artifacts are append-only');
            END
            """
        )


def _drop_triggers(connection: object) -> None:
    for table_name in POST_RUN_PARENT_LOOKUPS:
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_completed_parent_insert"
        )
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_insert_existing"
        )
        for action in ("update", "delete"):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{action}"
            )
