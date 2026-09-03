"""add backtest v2 lineage

Revision ID: c4e8a1d7f205
Revises: b7d4e9f2c631
Create Date: 2026-09-03 16:00:00.000000
"""

from typing import Sequence, Union, cast

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Connection

from football_system.infrastructure.database.immutability import (
    install_backtest_v2_v1_triggers,
)


revision: str = "c4e8a1d7f205"
down_revision: Union[str, None] = "b7d4e9f2c631"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKTEST_V2_TABLES = (
    "backtest_v2_runs",
    "backtest_v2_run_archives",
    "backtest_v2_slices",
    "backtest_v2_training_sources",
    "backtest_v2_evaluation_refs",
    "backtest_v2_result_sources",
    "backtest_v2_slice_ticket_settlements",
    "backtest_v2_metric_snapshots",
)


def upgrade() -> None:
    _require_sqlite_dialect()
    _create_tables()
    install_backtest_v2_v1_triggers(cast(Connection, _AlembicConnection()))


def downgrade() -> None:
    _require_sqlite_dialect()
    _require_empty_backtest_v2_lineage()
    _drop_triggers()
    for table_name in reversed(BACKTEST_V2_TABLES):
        op.drop_table(table_name)


def _create_tables() -> None:
    op.create_table(
        "backtest_v2_runs",
        sa.Column("backtest_run_id", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("backtest_version", sa.String(length=80), nullable=False),
        sa.Column("data_mode", sa.String(length=40), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("strategy_version", sa.String(length=80), nullable=False),
        sa.Column("strategy_config_json", sa.Text(), nullable=False),
        sa.Column("strategy_config_hash", sa.String(length=64), nullable=False),
        sa.Column("code_revision", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_slice_count", sa.Integer(), nullable=False),
        sa.Column("run_json", sa.Text(), nullable=False),
        sa.Column("run_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "schema_version = 'BACKTEST_V2_RUN_RECORD_V1'",
            name="ck_backtest_v2_run_schema",
        ),
        sa.CheckConstraint(
            "backtest_version = 'BACKTEST_V2'",
            name="ck_backtest_v2_run_version",
        ),
        sa.CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_backtest_v2_run_data_mode",
        ),
        sa.CheckConstraint(
            "date_from <= date_to",
            name="ck_backtest_v2_run_dates",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED')",
            name="ck_backtest_v2_run_status",
        ),
        sa.CheckConstraint(
            "expected_slice_count > 0",
            name="ck_backtest_v2_run_slice_count",
        ),
        sa.CheckConstraint(
            "length(strategy_config_hash) = 64 AND length(run_hash) = 64",
            name="ck_backtest_v2_run_hashes",
        ),
        sa.PrimaryKeyConstraint("backtest_run_id"),
        sa.UniqueConstraint("run_hash", name="uq_backtest_v2_run_hash"),
    )
    op.create_table(
        "backtest_v2_run_archives",
        sa.Column("backtest_run_id", sa.String(length=160), nullable=False),
        sa.Column("archive_id", sa.String(length=160), nullable=False),
        sa.Column("archive_no", sa.Integer(), nullable=False),
        sa.Column("archive_payload_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "archive_no > 0",
            name="ck_backtest_v2_run_archive_no",
        ),
        sa.ForeignKeyConstraint(
            ["archive_id"],
            ["historical_archive_imports.archive_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_v2_runs.backtest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_run_id", "archive_id"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "archive_no",
            name="uq_backtest_v2_run_archive_no",
        ),
    )
    op.create_table(
        "backtest_v2_slices",
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=False),
        sa.Column("backtest_run_id", sa.String(length=160), nullable=False),
        sa.Column("slice_no", sa.Integer(), nullable=False),
        sa.Column("slice_version", sa.String(length=80), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("quant_model_state_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_id", sa.String(length=160), nullable=False),
        sa.Column(
            "portfolio_settlement_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("data_mode", sa.String(length=40), nullable=False),
        sa.Column("decision_as_of_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evaluation_as_of_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_target_count", sa.Integer(), nullable=False),
        sa.Column("decision_target_count", sa.Integer(), nullable=False),
        sa.Column("result_target_count", sa.Integer(), nullable=False),
        sa.Column("quant_available_count", sa.Integer(), nullable=False),
        sa.Column("quant_unavailable_count", sa.Integer(), nullable=False),
        sa.Column("decision_snapshot_json", sa.Text(), nullable=False),
        sa.Column("decision_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("slice_json", sa.Text(), nullable=False),
        sa.Column("slice_hash", sa.String(length=64), nullable=False),
        sa.Column("settlement_result_json", sa.Text(), nullable=False),
        sa.Column("settlement_result_hash", sa.String(length=64), nullable=False),
        sa.Column("slate_snapshot_json", sa.Text(), nullable=False),
        sa.Column("slate_snapshot_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "slice_version = 'BACKTEST_V2_SLICE_V1'",
            name="ck_backtest_v2_slice_version",
        ),
        sa.CheckConstraint("slice_no > 0", name="ck_backtest_v2_slice_no"),
        sa.CheckConstraint(
            "decision_as_of_at_utc < evaluation_as_of_at_utc",
            name="ck_backtest_v2_slice_cutoffs",
        ),
        sa.CheckConstraint(
            "planned_target_count >= decision_target_count AND "
            "decision_target_count >= result_target_count AND "
            "quant_available_count + quant_unavailable_count = "
            "decision_target_count",
            name="ck_backtest_v2_slice_counts",
        ),
        sa.CheckConstraint(
            "length(decision_snapshot_hash) = 64 AND length(slice_hash) = 64 "
            "AND length(settlement_result_hash) = 64 "
            "AND length(slate_snapshot_hash) = 64",
            name="ck_backtest_v2_slice_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_v2_runs.backtest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_settlement_id"],
            ["portfolio_settlements.portfolio_settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quant_model_state_id", "analysis_run_id"],
            [
                "quant_model_states.quant_model_state_id",
                "quant_model_states.analysis_run_id",
            ],
            name="fk_backtest_v2_slice_model_state",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_slice_id"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "slice_no",
            name="uq_backtest_v2_slice_no",
        ),
        sa.UniqueConstraint("slice_hash", name="uq_backtest_v2_slice_hash"),
    )
    op.create_table(
        "backtest_v2_training_sources",
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=False),
        sa.Column("training_sequence", sa.Integer(), nullable=False),
        sa.Column("match_result_id", sa.String(length=160), nullable=False),
        sa.Column("archive_id", sa.String(length=160), nullable=False),
        sa.Column("source_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("fact_hash", sa.String(length=64), nullable=False),
        sa.Column("archive_payload_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "training_sequence >= 0",
            name="ck_backtest_v2_training_sequence",
        ),
        sa.CheckConstraint(
            "length(source_payload_hash) = 64 AND length(fact_hash) = 64 "
            "AND length(archive_payload_sha256) = 64",
            name="ck_backtest_v2_training_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["archive_id"],
            ["historical_archive_imports.archive_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_slice_id"],
            ["backtest_v2_slices.backtest_slice_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_result_id"],
            ["match_results.match_result_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_slice_id", "training_sequence"),
        sa.UniqueConstraint(
            "backtest_slice_id",
            "match_result_id",
            name="uq_backtest_v2_training_result",
        ),
    )
    op.create_table(
        "backtest_v2_evaluation_refs",
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=False),
        sa.Column("decision_no", sa.Integer(), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column(
            "quant_model_evaluation_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("model_prediction_hash", sa.String(length=64), nullable=False),
        sa.Column("market_prediction_id", sa.String(length=160), nullable=False),
        sa.Column("quant_prediction_id", sa.String(length=160), nullable=True),
        sa.Column("final_prediction_id", sa.String(length=160), nullable=True),
        sa.CheckConstraint(
            "decision_no > 0",
            name="ck_backtest_v2_evaluation_no",
        ),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_backtest_v2_evaluation_status",
        ),
        sa.CheckConstraint(
            "(status = 'AVAILABLE' AND quant_prediction_id IS NOT NULL "
            "AND final_prediction_id IS NOT NULL) OR "
            "(status = 'UNAVAILABLE' AND quant_prediction_id IS NULL "
            "AND final_prediction_id IS NULL)",
            name="ck_backtest_v2_evaluation_projection",
        ),
        sa.CheckConstraint(
            "length(output_hash) = 64 AND length(model_prediction_hash) = 64",
            name="ck_backtest_v2_evaluation_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_slice_id"],
            ["backtest_v2_slices.backtest_slice_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["final_prediction_id"],
            ["final_predictions.final_prediction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["market_prediction_id"],
            ["market_probabilities.market_probability_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quant_model_evaluation_id"],
            ["quant_model_evaluations.quant_model_evaluation_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quant_prediction_id"],
            ["quant_predictions.quant_prediction_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_slice_id", "decision_no"),
        sa.UniqueConstraint(
            "backtest_slice_id",
            "internal_match_id",
            name="uq_backtest_v2_evaluation_match",
        ),
    )
    op.create_table(
        "backtest_v2_result_sources",
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=False),
        sa.Column("result_no", sa.Integer(), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("match_result_id", sa.String(length=160), nullable=False),
        sa.Column("archive_id", sa.String(length=160), nullable=False),
        sa.Column("source_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("archive_payload_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "result_no > 0",
            name="ck_backtest_v2_result_no",
        ),
        sa.CheckConstraint(
            "length(source_payload_hash) = 64 AND length(archive_payload_sha256) = 64",
            name="ck_backtest_v2_result_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["archive_id"],
            ["historical_archive_imports.archive_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_slice_id"],
            ["backtest_v2_slices.backtest_slice_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_result_id"],
            ["match_results.match_result_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_slice_id", "result_no"),
        sa.UniqueConstraint(
            "backtest_slice_id",
            "internal_match_id",
            name="uq_backtest_v2_result_match",
        ),
    )
    op.create_table(
        "backtest_v2_slice_ticket_settlements",
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=False),
        sa.Column("settlement_no", sa.Integer(), nullable=False),
        sa.Column("settlement_id", sa.String(length=160), nullable=False),
        sa.CheckConstraint(
            "settlement_no > 0",
            name="ck_backtest_v2_slice_ticket_settlement_no",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_slice_id"],
            ["backtest_v2_slices.backtest_slice_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["ticket_settlements.settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_slice_id", "settlement_no"),
        sa.UniqueConstraint(
            "backtest_slice_id",
            "settlement_id",
            name="uq_backtest_v2_slice_ticket_settlement",
        ),
    )
    op.create_table(
        "backtest_v2_metric_snapshots",
        sa.Column("metric_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("backtest_run_id", sa.String(length=160), nullable=False),
        sa.Column("metric_version", sa.String(length=80), nullable=False),
        sa.Column("as_of_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("metrics_hash", sa.String(length=64), nullable=False),
        sa.Column("lineage_json", sa.Text(), nullable=False),
        sa.Column("lineage_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "metric_version = 'BACKTEST_METRICS_V2'",
            name="ck_backtest_v2_metric_version",
        ),
        sa.CheckConstraint(
            "as_of_at_utc <= calculated_at_utc",
            name="ck_backtest_v2_metric_timeline",
        ),
        sa.CheckConstraint(
            "length(metrics_hash) = 64 AND length(lineage_hash) = 64 "
            "AND length(snapshot_hash) = 64",
            name="ck_backtest_v2_metric_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_v2_runs.backtest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("metric_snapshot_id"),
        sa.UniqueConstraint("backtest_run_id"),
        sa.UniqueConstraint(
            "snapshot_hash",
            name="uq_backtest_v2_metric_hash",
        ),
    )


class _AlembicConnection:
    def exec_driver_sql(self, statement: str) -> None:
        op.execute(statement)


def _drop_triggers() -> None:
    child_tables = BACKTEST_V2_TABLES[1:]
    custom_triggers = (
        "trg_backtest_v2_runs_insert_running",
        "trg_backtest_v2_runs_update",
        "trg_backtest_v2_runs_delete",
        "trg_backtest_v2_run_archives_lineage_insert",
        "trg_backtest_v2_slices_lineage_insert",
        "trg_backtest_v2_training_sources_lineage_insert",
        "trg_backtest_v2_evaluation_refs_lineage_insert",
        "trg_backtest_v2_result_sources_lineage_insert",
        "trg_backtest_v2_slice_ticket_settlements_lineage_insert",
        "trg_backtest_v2_metrics_lineage_insert",
        "trg_backtest_v2_runs_completion",
    )
    for trigger_name in custom_triggers:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    for table_name in BACKTEST_V2_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_insert_existing")
    for table_name in child_tables:
        for action in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_sealed_{action}")
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{action}")


def _require_empty_backtest_v2_lineage() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline BACKTEST_V2 downgrade is unsupported because lineage must be checked"
        )
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM backtest_v2_runs)")):
        raise RuntimeError(
            "cannot downgrade BACKTEST_V2 schema while backtest lineage exists"
        )


def _require_sqlite_dialect() -> None:
    dialect = op.get_context().dialect.name
    if dialect != "sqlite":
        raise RuntimeError(
            f"Unsupported database backend '{dialect}'; "
            "football-system v0.4.0 supports SQLite only."
        )
