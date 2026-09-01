"""add historical settlement and backtest persistence

Revision ID: f3a1c6d8e204
Revises: c8b7e2a4f190
Create Date: 2026-09-01 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a1c6d8e204"
down_revision: Union[str, None] = "c8b7e2a4f190"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


IMMUTABLE_INSERT_KEYS = {
    "historical_archive_imports": (
        ("archive_id",),
        ("provider_code", "dataset_kind", "payload_sha256"),
    ),
    "match_results": (
        ("match_result_id",),
        ("provider_id", "source_result_key"),
        ("supersedes_match_result_id",),
    ),
    "ticket_settlements": (
        ("settlement_id",),
        ("settlement_hash",),
        ("supersedes_settlement_id",),
    ),
    "ticket_settlement_match_results": (
        ("settlement_id", "leg_no"),
        ("settlement_id", "match_result_id"),
        ("settlement_id", "internal_match_id"),
    ),
    "portfolio_settlements": (
        ("portfolio_settlement_id",),
        ("settlement_hash",),
        ("supersedes_portfolio_settlement_id",),
    ),
    "portfolio_settlement_tickets": (
        ("portfolio_settlement_id", "settlement_no"),
        ("portfolio_settlement_id", "settlement_id"),
    ),
    "backtest_runs": (("backtest_run_id",), ("run_hash",)),
    "backtest_slices": (
        ("backtest_slice_id",),
        ("backtest_run_id", "slice_no"),
        ("slice_hash",),
    ),
    "backtest_metric_snapshots": (
        ("metric_snapshot_id",),
        ("backtest_run_id", "metric_scope", "metric_key", "snapshot_no"),
        ("snapshot_hash",),
    ),
    "backtest_metric_settlements": (("metric_snapshot_id", "portfolio_settlement_id"),),
    "backtest_metric_ticket_settlements": (("metric_snapshot_id", "settlement_id"),),
}


def upgrade() -> None:
    _require_sqlite_dialect()
    op.create_table(
        "historical_archive_imports",
        sa.Column("archive_id", sa.String(length=160), nullable=False),
        sa.Column("archive_schema_version", sa.String(length=80), nullable=False),
        sa.Column("provider_code", sa.String(length=160), nullable=False),
        sa.Column("dataset_kind", sa.String(length=80), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_reference", sa.String(length=2048), nullable=False),
        sa.Column("source_description", sa.Text(), nullable=False),
        sa.Column("license_note", sa.String(length=2048), nullable=False),
        sa.Column("data_mode", sa.String(length=40), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("imported_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(archive_schema_version)) > 0",
            name="ck_historical_archive_import_schema_version",
        ),
        sa.CheckConstraint(
            "length(trim(provider_code)) > 0",
            name="ck_historical_archive_import_provider_code",
        ),
        sa.CheckConstraint(
            "dataset_kind IN ('FIXTURES', 'MARKET_ODDS', 'SPORTTERY_BONUS', "
            "'MANUAL_QUANT', 'MATCH_RESULTS', 'PROVIDER_MAPPINGS')",
            name="ck_historical_archive_import_dataset_kind",
        ),
        sa.CheckConstraint(
            "length(trim(source_reference)) > 0",
            name="ck_historical_archive_import_source_reference",
        ),
        sa.CheckConstraint(
            "length(trim(source_description)) > 0",
            name="ck_historical_archive_import_source_description",
        ),
        sa.CheckConstraint(
            "length(trim(license_note)) > 0",
            name="ck_historical_archive_import_license_note",
        ),
        sa.CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_historical_archive_import_data_mode",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_historical_archive_import_payload_hash_length",
        ),
        sa.CheckConstraint(
            "record_count >= 0",
            name="ck_historical_archive_import_record_count",
        ),
        sa.CheckConstraint(
            "created_at_utc <= imported_at_utc",
            name="ck_historical_archive_import_timeline",
        ),
        sa.PrimaryKeyConstraint("archive_id"),
        sa.UniqueConstraint(
            "provider_code",
            "dataset_kind",
            "payload_sha256",
            name="uq_historical_archive_import_checksum_identity",
        ),
    )
    op.create_index(
        "ix_historical_archive_import_provider_dataset_created",
        "historical_archive_imports",
        ["provider_code", "dataset_kind", "created_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_historical_archive_import_mode_imported",
        "historical_archive_imports",
        ["data_mode", "imported_at_utc"],
        unique=False,
    )

    op.create_table(
        "match_results",
        sa.Column("match_result_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("provider_id", sa.String(length=160), nullable=False),
        sa.Column("provider_mapping_id", sa.String(length=160), nullable=False),
        sa.Column("home_goals", sa.Integer(), nullable=False),
        sa.Column("away_goals", sa.Integer(), nullable=False),
        sa.Column("observed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_result_key", sa.String(length=160), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_match_result_id", sa.String(length=160), nullable=True),
        sa.CheckConstraint("away_goals >= 0", name="ck_match_result_away_goals"),
        sa.CheckConstraint("home_goals >= 0", name="ck_match_result_home_goals"),
        sa.CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_match_result_payload_hash_length",
        ),
        sa.CheckConstraint(
            "observed_at_utc <= available_at_utc AND "
            "available_at_utc <= ingested_at_utc",
            name="ck_match_result_timeline",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_mapping_id"],
            ["provider_match_mappings.mapping_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_match_result_id",
                "internal_match_id",
                "provider_id",
            ],
            [
                "match_results.match_result_id",
                "match_results.internal_match_id",
                "match_results.provider_id",
            ],
            name="fk_match_result_supersession_lineage",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("match_result_id"),
        sa.UniqueConstraint(
            "match_result_id",
            "internal_match_id",
            "provider_id",
            name="uq_match_result_lineage_binding",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "source_result_key",
            name="uq_match_result_source_key",
        ),
        sa.UniqueConstraint(
            "supersedes_match_result_id",
            name="uq_match_result_superseded_once",
        ),
    )
    op.create_index(
        "ix_match_results_match_provider_cutoff",
        "match_results",
        [
            "internal_match_id",
            "provider_id",
            "available_at_utc",
            "ingested_at_utc",
        ],
        unique=False,
    )
    op.create_index(
        "uq_match_results_provider_match_root",
        "match_results",
        ["provider_id", "internal_match_id"],
        unique=True,
        sqlite_where=sa.text("supersedes_match_result_id IS NULL"),
        postgresql_where=sa.text("supersedes_match_result_id IS NULL"),
    )
    op.create_table(
        "ticket_settlements",
        sa.Column("settlement_id", sa.String(length=160), nullable=False),
        sa.Column("settlement_kind", sa.String(length=40), nullable=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("decision_scope_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_revision_id", sa.String(length=160), nullable=True),
        sa.Column("portfolio_id", sa.String(length=160), nullable=False),
        sa.Column("ticket_id", sa.String(length=160), nullable=False),
        sa.Column("base_portfolio_id", sa.String(length=160), nullable=True),
        sa.Column("base_ticket_id", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("stake_fen", sa.Integer(), nullable=False),
        sa.Column("gross_payout_fen", sa.Integer(), nullable=False),
        sa.Column("profit_loss_fen", sa.Integer(), nullable=False),
        sa.Column("payout_policy_version", sa.String(length=80), nullable=False),
        sa.Column("settlement_policy_version", sa.String(length=80), nullable=False),
        sa.Column("settled_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settlement_json", sa.Text(), nullable=False),
        sa.Column("settlement_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_settlement_id", sa.String(length=160), nullable=True),
        sa.CheckConstraint(
            "gross_payout_fen >= 0",
            name="ck_ticket_settlement_gross_payout",
        ),
        sa.CheckConstraint(
            "length(settlement_hash) = 64",
            name="ck_ticket_settlement_hash_length",
        ),
        sa.CheckConstraint(
            "profit_loss_fen = gross_payout_fen - stake_fen",
            name="ck_ticket_settlement_profit_loss",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('ANALYSIS_RUN', 'PORTFOLIO_REVISION')",
            name="ck_ticket_settlement_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'ANALYSIS_RUN' "
            "AND decision_scope_id = parent_analysis_run_id "
            "AND portfolio_revision_id IS NULL "
            "AND base_portfolio_id IS NOT NULL "
            "AND base_ticket_id IS NOT NULL "
            "AND base_portfolio_id = portfolio_id "
            "AND base_ticket_id = ticket_id) OR "
            "(scope_kind = 'PORTFOLIO_REVISION' "
            "AND portfolio_revision_id IS NOT NULL "
            "AND decision_scope_id = portfolio_revision_id "
            "AND base_portfolio_id IS NULL "
            "AND base_ticket_id IS NULL)",
            name="ck_ticket_settlement_scope_binding",
        ),
        sa.CheckConstraint(
            "(status = 'WON' AND gross_payout_fen > 0) OR "
            "(status = 'LOST' AND gross_payout_fen = 0)",
            name="ck_ticket_settlement_status_payout",
        ),
        sa.CheckConstraint("stake_fen > 0", name="ck_ticket_settlement_stake"),
        sa.CheckConstraint(
            "settlement_kind = 'BACKTEST'", name="ck_ticket_settlement_kind"
        ),
        sa.ForeignKeyConstraint(
            ["base_portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_ticket_id"], ["tickets.ticket_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_revision_id"],
            ["portfolio_revisions.portfolio_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_settlement_id",
                "scope_kind",
                "parent_analysis_run_id",
                "decision_scope_id",
                "portfolio_id",
                "ticket_id",
            ],
            [
                "ticket_settlements.settlement_id",
                "ticket_settlements.scope_kind",
                "ticket_settlements.parent_analysis_run_id",
                "ticket_settlements.decision_scope_id",
                "ticket_settlements.portfolio_id",
                "ticket_settlements.ticket_id",
            ],
            name="fk_ticket_settlement_correction_lineage",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("settlement_id"),
        sa.UniqueConstraint(
            "settlement_id",
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            "ticket_id",
            name="uq_ticket_settlement_lineage_binding",
        ),
        sa.UniqueConstraint("settlement_hash", name="uq_ticket_settlement_hash"),
        sa.UniqueConstraint(
            "supersedes_settlement_id",
            name="uq_ticket_settlement_superseded_once",
        ),
    )
    op.create_index(
        "ix_ticket_settlements_scope_ticket_cutoff",
        "ticket_settlements",
        ["decision_scope_id", "ticket_id", "settled_at_utc"],
        unique=False,
    )
    op.create_index(
        "uq_ticket_settlements_logical_root",
        "ticket_settlements",
        [
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            "ticket_id",
        ],
        unique=True,
        sqlite_where=sa.text("supersedes_settlement_id IS NULL"),
        postgresql_where=sa.text("supersedes_settlement_id IS NULL"),
    )
    op.create_table(
        "ticket_settlement_match_results",
        sa.Column("settlement_id", sa.String(length=160), nullable=False),
        sa.Column("leg_no", sa.Integer(), nullable=False),
        sa.Column("match_result_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.CheckConstraint(
            "leg_no IN (1, 2)", name="ck_ticket_settlement_result_leg_no"
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
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["ticket_settlements.settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("settlement_id", "leg_no"),
        sa.UniqueConstraint(
            "settlement_id",
            "internal_match_id",
            name="uq_ticket_settlement_internal_match",
        ),
        sa.UniqueConstraint(
            "settlement_id",
            "match_result_id",
            name="uq_ticket_settlement_match_result",
        ),
    )
    op.create_table(
        "portfolio_settlements",
        sa.Column("portfolio_settlement_id", sa.String(length=160), nullable=False),
        sa.Column("settlement_kind", sa.String(length=40), nullable=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("decision_scope_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_revision_id", sa.String(length=160), nullable=True),
        sa.Column("portfolio_id", sa.String(length=160), nullable=False),
        sa.Column("base_portfolio_id", sa.String(length=160), nullable=True),
        sa.Column("budget_fen", sa.Integer(), nullable=False),
        sa.Column("total_stake_fen", sa.Integer(), nullable=False),
        sa.Column("cash_fen", sa.Integer(), nullable=False),
        sa.Column("gross_payout_fen", sa.Integer(), nullable=False),
        sa.Column("profit_loss_fen", sa.Integer(), nullable=False),
        sa.Column("ticket_count", sa.Integer(), nullable=False),
        sa.Column("settlement_policy_version", sa.String(length=80), nullable=False),
        sa.Column("settled_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settlement_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "supersedes_portfolio_settlement_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.CheckConstraint("budget_fen >= 0", name="ck_portfolio_settlement_budget"),
        sa.CheckConstraint("cash_fen >= 0", name="ck_portfolio_settlement_cash"),
        sa.CheckConstraint(
            "total_stake_fen + cash_fen = budget_fen",
            name="ck_portfolio_settlement_capital_balance",
        ),
        sa.CheckConstraint(
            "gross_payout_fen >= 0",
            name="ck_portfolio_settlement_gross_payout",
        ),
        sa.CheckConstraint(
            "length(settlement_hash) = 64",
            name="ck_portfolio_settlement_hash_length",
        ),
        sa.CheckConstraint(
            "profit_loss_fen = gross_payout_fen - total_stake_fen",
            name="ck_portfolio_settlement_profit_loss",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('ANALYSIS_RUN', 'PORTFOLIO_REVISION')",
            name="ck_portfolio_settlement_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'ANALYSIS_RUN' "
            "AND decision_scope_id = parent_analysis_run_id "
            "AND portfolio_revision_id IS NULL "
            "AND base_portfolio_id IS NOT NULL "
            "AND base_portfolio_id = portfolio_id) OR "
            "(scope_kind = 'PORTFOLIO_REVISION' "
            "AND portfolio_revision_id IS NOT NULL "
            "AND decision_scope_id = portfolio_revision_id "
            "AND base_portfolio_id IS NULL)",
            name="ck_portfolio_settlement_scope_binding",
        ),
        sa.CheckConstraint(
            "settlement_kind = 'BACKTEST'",
            name="ck_portfolio_settlement_kind",
        ),
        sa.CheckConstraint(
            "ticket_count >= 0", name="ck_portfolio_settlement_ticket_count"
        ),
        sa.CheckConstraint(
            "total_stake_fen >= 0", name="ck_portfolio_settlement_stake"
        ),
        sa.ForeignKeyConstraint(
            ["base_portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_revision_id"],
            ["portfolio_revisions.portfolio_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_portfolio_settlement_id",
                "scope_kind",
                "parent_analysis_run_id",
                "decision_scope_id",
                "portfolio_id",
            ],
            [
                "portfolio_settlements.portfolio_settlement_id",
                "portfolio_settlements.scope_kind",
                "portfolio_settlements.parent_analysis_run_id",
                "portfolio_settlements.decision_scope_id",
                "portfolio_settlements.portfolio_id",
            ],
            name="fk_portfolio_settlement_correction_lineage",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("portfolio_settlement_id"),
        sa.UniqueConstraint(
            "portfolio_settlement_id",
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            name="uq_portfolio_settlement_lineage_binding",
        ),
        sa.UniqueConstraint("settlement_hash", name="uq_portfolio_settlement_hash"),
        sa.UniqueConstraint(
            "supersedes_portfolio_settlement_id",
            name="uq_portfolio_settlement_superseded_once",
        ),
    )
    op.create_index(
        "ix_portfolio_settlements_scope_portfolio_cutoff",
        "portfolio_settlements",
        ["decision_scope_id", "portfolio_id", "settled_at_utc"],
        unique=False,
    )
    op.create_index(
        "uq_portfolio_settlements_logical_root",
        "portfolio_settlements",
        [
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
        ],
        unique=True,
        sqlite_where=sa.text("supersedes_portfolio_settlement_id IS NULL"),
        postgresql_where=sa.text("supersedes_portfolio_settlement_id IS NULL"),
    )
    op.create_table(
        "portfolio_settlement_tickets",
        sa.Column("portfolio_settlement_id", sa.String(length=160), nullable=False),
        sa.Column("settlement_no", sa.Integer(), nullable=False),
        sa.Column("settlement_id", sa.String(length=160), nullable=False),
        sa.CheckConstraint(
            "settlement_no > 0", name="ck_portfolio_settlement_ticket_no"
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_settlement_id"],
            ["portfolio_settlements.portfolio_settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["ticket_settlements.settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("portfolio_settlement_id", "settlement_no"),
        sa.UniqueConstraint(
            "portfolio_settlement_id",
            "settlement_id",
            name="uq_portfolio_settlement_ticket",
        ),
    )
    op.create_table(
        "backtest_runs",
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
        sa.Column("engine_version", sa.String(length=80), nullable=False),
        sa.Column("backtest_mode", sa.String(length=40), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("input_manifest_version", sa.String(length=80), nullable=False),
        sa.Column("input_manifest_json", sa.Text(), nullable=False),
        sa.Column("input_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("run_hash", sa.String(length=64), nullable=False),
        sa.Column("replay_of_backtest_run_id", sa.String(length=160), nullable=True),
        sa.CheckConstraint(
            "length(config_hash) = 64",
            name="ck_backtest_run_config_hash_length",
        ),
        sa.CheckConstraint(
            "length(input_manifest_hash) = 64",
            name="ck_backtest_run_manifest_hash_length",
        ),
        sa.CheckConstraint("length(run_hash) = 64", name="ck_backtest_run_hash_length"),
        sa.CheckConstraint(
            "backtest_mode = 'STRICT_POINT_IN_TIME'",
            name="ck_backtest_run_strict_mode",
        ),
        sa.CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_backtest_run_data_mode",
        ),
        sa.CheckConstraint(
            "date_from <= date_to",
            name="ck_backtest_run_date_order",
        ),
        sa.CheckConstraint(
            "length(trim(backtest_version)) > 0",
            name="ck_backtest_run_backtest_version",
        ),
        sa.CheckConstraint(
            "length(trim(strategy_version)) > 0",
            name="ck_backtest_run_strategy_version",
        ),
        sa.CheckConstraint(
            "length(strategy_config_hash) = 64",
            name="ck_backtest_run_strategy_hash_length",
        ),
        sa.CheckConstraint(
            "length(trim(code_revision)) > 0",
            name="ck_backtest_run_code_revision",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_backtest_run_status",
        ),
        sa.CheckConstraint(
            "started_at_utc <= completed_at_utc AND completed_at_utc <= created_at_utc",
            name="ck_backtest_run_timeline",
        ),
        sa.ForeignKeyConstraint(
            ["replay_of_backtest_run_id"],
            ["backtest_runs.backtest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_run_id"),
        sa.UniqueConstraint("run_hash", name="uq_backtest_run_hash"),
    )
    op.create_index(
        "ix_backtest_runs_completed_at",
        "backtest_runs",
        ["completed_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_backtest_runs_mode_created",
        "backtest_runs",
        ["data_mode", "created_at_utc", "backtest_run_id"],
        unique=False,
    )
    op.create_table(
        "backtest_slices",
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=False),
        sa.Column("backtest_run_id", sa.String(length=160), nullable=False),
        sa.Column("slice_no", sa.Integer(), nullable=False),
        sa.Column("slice_version", sa.String(length=80), nullable=False),
        sa.Column("parent_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("data_mode", sa.String(length=40), nullable=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False),
        sa.Column("decision_scope_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_revision_id", sa.String(length=160), nullable=True),
        sa.Column("decision_as_of_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evaluation_as_of_at_utc", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slice_manifest_json", sa.Text(), nullable=False),
        sa.Column("slice_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("slice_hash", sa.String(length=64), nullable=False),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("settled_match_count", sa.Integer(), nullable=False),
        sa.Column("settled_ticket_count", sa.Integer(), nullable=False),
        sa.Column("unsettled_ticket_count", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.Numeric(24, 12), nullable=True),
        sa.CheckConstraint("slice_no > 0", name="ck_backtest_slice_no"),
        sa.CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_backtest_slice_data_mode",
        ),
        sa.CheckConstraint(
            "decision_as_of_at_utc < evaluation_as_of_at_utc",
            name="ck_backtest_slice_cutoffs",
        ),
        sa.CheckConstraint(
            "length(slice_hash) = 64", name="ck_backtest_slice_hash_length"
        ),
        sa.CheckConstraint(
            "length(slice_manifest_hash) = 64",
            name="ck_backtest_slice_manifest_hash_length",
        ),
        sa.CheckConstraint(
            "match_count >= 0 AND settled_match_count >= 0 AND "
            "settled_match_count <= match_count",
            name="ck_backtest_slice_match_counts",
        ),
        sa.CheckConstraint(
            "settled_ticket_count >= 0 AND unsettled_ticket_count >= 0",
            name="ck_backtest_slice_ticket_counts",
        ),
        sa.CheckConstraint(
            "((settled_ticket_count + unsettled_ticket_count = 0 "
            "AND coverage IS NULL) OR "
            "(settled_ticket_count + unsettled_ticket_count > 0 "
            "AND coverage IS NOT NULL AND coverage >= 0 AND coverage <= 1 "
            "AND abs(coverage * (settled_ticket_count + unsettled_ticket_count) "
            "- settled_ticket_count) <= "
            "0.000000000001 * (settled_ticket_count + unsettled_ticket_count)))",
            name="ck_backtest_slice_coverage",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('ANALYSIS_RUN', 'PORTFOLIO_REVISION')",
            name="ck_backtest_slice_scope_kind",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'ANALYSIS_RUN' "
            "AND decision_scope_id = parent_analysis_run_id "
            "AND portfolio_revision_id IS NULL) OR "
            "(scope_kind = 'PORTFOLIO_REVISION' "
            "AND portfolio_revision_id IS NOT NULL "
            "AND decision_scope_id = portfolio_revision_id)",
            name="ck_backtest_slice_scope_binding",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.backtest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_revision_id"],
            ["portfolio_revisions.portfolio_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("backtest_slice_id"),
        sa.UniqueConstraint(
            "backtest_run_id", "slice_no", name="uq_backtest_slice_number"
        ),
        sa.UniqueConstraint(
            "backtest_slice_id",
            "backtest_run_id",
            name="uq_backtest_slice_run_binding",
        ),
        sa.UniqueConstraint("slice_hash", name="uq_backtest_slice_hash"),
    )
    op.create_index(
        "ix_backtest_slices_run_evaluation",
        "backtest_slices",
        ["backtest_run_id", "evaluation_as_of_at_utc"],
        unique=False,
    )
    op.create_table(
        "backtest_metric_snapshots",
        sa.Column("metric_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("backtest_run_id", sa.String(length=160), nullable=False),
        sa.Column("backtest_slice_id", sa.String(length=160), nullable=True),
        sa.Column("snapshot_no", sa.Integer(), nullable=False),
        sa.Column("metric_scope", sa.String(length=40), nullable=False),
        sa.Column("metric_key", sa.String(length=160), nullable=False),
        sa.Column("metric_version", sa.String(length=80), nullable=False),
        sa.Column("as_of_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("metrics_hash", sa.String(length=64), nullable=False),
        sa.Column("lineage_json", sa.Text(), nullable=False),
        sa.Column("lineage_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "as_of_at_utc <= calculated_at_utc",
            name="ck_backtest_metric_timeline",
        ),
        sa.CheckConstraint(
            "length(lineage_hash) = 64",
            name="ck_backtest_metric_lineage_hash_length",
        ),
        sa.CheckConstraint(
            "metric_scope IN ('RUN', 'SLICE')",
            name="ck_backtest_metric_scope",
        ),
        sa.CheckConstraint(
            "(metric_scope = 'RUN' AND backtest_slice_id IS NULL) OR "
            "(metric_scope = 'SLICE' AND backtest_slice_id IS NOT NULL)",
            name="ck_backtest_metric_scope_binding",
        ),
        sa.CheckConstraint(
            "length(metrics_hash) = 64",
            name="ck_backtest_metric_payload_hash_length",
        ),
        sa.CheckConstraint(
            "length(snapshot_hash) = 64",
            name="ck_backtest_metric_snapshot_hash_length",
        ),
        sa.CheckConstraint("snapshot_no > 0", name="ck_backtest_metric_snapshot_no"),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["backtest_runs.backtest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_slice_id", "backtest_run_id"],
            ["backtest_slices.backtest_slice_id", "backtest_slices.backtest_run_id"],
            name="fk_backtest_metric_slice_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("metric_snapshot_id"),
        sa.UniqueConstraint(
            "backtest_run_id",
            "metric_scope",
            "metric_key",
            "snapshot_no",
            name="uq_backtest_metric_sequence",
        ),
        sa.UniqueConstraint("snapshot_hash", name="uq_backtest_metric_snapshot_hash"),
    )
    op.create_index(
        "ix_backtest_metrics_run_key_cutoff",
        "backtest_metric_snapshots",
        ["backtest_run_id", "metric_scope", "metric_key", "as_of_at_utc"],
        unique=False,
    )
    op.create_table(
        "backtest_metric_settlements",
        sa.Column("metric_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_settlement_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["metric_snapshot_id"],
            ["backtest_metric_snapshots.metric_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_settlement_id"],
            ["portfolio_settlements.portfolio_settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("metric_snapshot_id", "portfolio_settlement_id"),
    )
    op.create_table(
        "backtest_metric_ticket_settlements",
        sa.Column("metric_snapshot_id", sa.String(length=160), nullable=False),
        sa.Column("settlement_id", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["metric_snapshot_id"],
            ["backtest_metric_snapshots.metric_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["ticket_settlements.settlement_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("metric_snapshot_id", "settlement_id"),
    )
    _install_triggers()


def downgrade() -> None:
    _require_sqlite_dialect()
    _drop_triggers()
    op.drop_table("backtest_metric_ticket_settlements")
    op.drop_table("backtest_metric_settlements")
    op.drop_index(
        "ix_backtest_metrics_run_key_cutoff",
        table_name="backtest_metric_snapshots",
    )
    op.drop_table("backtest_metric_snapshots")
    op.drop_index("ix_backtest_slices_run_evaluation", table_name="backtest_slices")
    op.drop_table("backtest_slices")
    op.drop_index("ix_backtest_runs_mode_created", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_completed_at", table_name="backtest_runs")
    op.drop_table("backtest_runs")
    op.drop_table("portfolio_settlement_tickets")
    op.drop_index(
        "uq_portfolio_settlements_logical_root",
        table_name="portfolio_settlements",
    )
    op.drop_index(
        "ix_portfolio_settlements_scope_portfolio_cutoff",
        table_name="portfolio_settlements",
    )
    op.drop_table("portfolio_settlements")
    op.drop_table("ticket_settlement_match_results")
    op.drop_index(
        "uq_ticket_settlements_logical_root",
        table_name="ticket_settlements",
    )
    op.drop_index(
        "ix_ticket_settlements_scope_ticket_cutoff",
        table_name="ticket_settlements",
    )
    op.drop_table("ticket_settlements")
    op.drop_index("uq_match_results_provider_match_root", table_name="match_results")
    op.drop_index("ix_match_results_match_provider_cutoff", table_name="match_results")
    op.drop_table("match_results")
    op.drop_index(
        "ix_historical_archive_import_mode_imported",
        table_name="historical_archive_imports",
    )
    op.drop_index(
        "ix_historical_archive_import_provider_dataset_created",
        table_name="historical_archive_imports",
    )
    op.drop_table("historical_archive_imports")


def _install_triggers() -> None:
    for table_name, key_sets in IMMUTABLE_INSERT_KEYS.items():
        conflict_condition = " OR ".join(
            "("
            + " AND ".join(f"existing.{column} = NEW.{column}" for column in key_set)
            + ")"
            for key_set in key_sets
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable_insert_existing
            BEFORE INSERT ON {table_name}
            WHEN EXISTS (
                SELECT 1 FROM {table_name} existing
                WHERE {conflict_condition}
            )
            BEGIN
                SELECT RAISE(ABORT, 'immutable record already exists');
            END
            """
        )
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'historical artifacts are append-only');
                END
                """
            )
    for statement in _lineage_trigger_statements():
        op.execute(statement)


def _lineage_trigger_statements() -> tuple[str, ...]:
    return (
        """
        CREATE TRIGGER trg_match_results_provider_mapping_insert
        BEFORE INSERT ON match_results
        WHEN NOT EXISTS (
            SELECT 1 FROM provider_match_mappings m
            WHERE m.mapping_id = NEW.provider_mapping_id
            AND m.provider_id = NEW.provider_id
            AND m.internal_match_id = NEW.internal_match_id
            AND m.available_at_utc <= NEW.available_at_utc
            AND m.available_at_utc <= NEW.ingested_at_utc
        )
        BEGIN
            SELECT RAISE(ABORT, 'match result requires a provider match mapping');
        END
        """,
        """
        CREATE TRIGGER trg_match_results_supersession_insert
        BEFORE INSERT ON match_results
        WHEN NEW.supersedes_match_result_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM match_results previous
            WHERE previous.match_result_id = NEW.supersedes_match_result_id
            AND previous.internal_match_id = NEW.internal_match_id
            AND previous.provider_id = NEW.provider_id
            AND previous.available_at_utc <= NEW.available_at_utc
            AND previous.ingested_at_utc < NEW.ingested_at_utc
        )
        BEGIN
            SELECT RAISE(ABORT, 'match result supersession must reference an earlier version');
        END
        """,
        """
        CREATE TRIGGER trg_ticket_settlements_completed_parent_insert
        BEFORE INSERT ON ticket_settlements
        WHEN NOT EXISTS (
            SELECT 1 FROM analysis_runs r
            WHERE r.analysis_run_id = NEW.parent_analysis_run_id
            AND r.status = 'COMPLETED'
            AND r.completed_at_utc IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'ticket settlement requires a completed AnalysisRun');
        END
        """,
        """
        CREATE TRIGGER trg_ticket_settlements_base_lineage_insert
        BEFORE INSERT ON ticket_settlements
        WHEN NEW.scope_kind = 'ANALYSIS_RUN' AND NOT EXISTS (
            SELECT 1 FROM tickets t
            JOIN portfolios p ON p.portfolio_id = t.portfolio_id
            JOIN analysis_runs r ON r.analysis_run_id = p.analysis_run_id
            WHERE t.ticket_id = NEW.base_ticket_id
            AND p.portfolio_id = NEW.base_portfolio_id
            AND r.analysis_run_id = NEW.parent_analysis_run_id
            AND NEW.decision_scope_id = r.analysis_run_id
            AND NEW.ticket_id = t.ticket_id
            AND NEW.portfolio_id = p.portfolio_id
            AND NEW.stake_fen = t.stake_fen
            AND NEW.payout_policy_version = t.payout_policy_version
            AND (NEW.status = 'LOST' OR
                 NEW.gross_payout_fen = t.potential_gross_payout_fen)
            AND r.status = 'COMPLETED'
            AND r.completed_at_utc IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'base ticket settlement lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_ticket_settlements_revision_lineage_insert
        BEFORE INSERT ON ticket_settlements
        WHEN NEW.scope_kind = 'PORTFOLIO_REVISION' AND NOT EXISTS (
            SELECT 1
            FROM portfolio_revisions revision,
                 json_each(revision.revision_json, '$.portfolios') portfolio,
                 json_each(portfolio.value, '$.tickets') ticket
            WHERE revision.portfolio_revision_id = NEW.portfolio_revision_id
            AND revision.parent_analysis_run_id = NEW.parent_analysis_run_id
            AND NEW.decision_scope_id = revision.portfolio_revision_id
            AND json_extract(portfolio.value, '$.portfolio_id') = NEW.portfolio_id
            AND json_extract(ticket.value, '$.ticket_id') = NEW.ticket_id
            AND CAST(json_extract(ticket.value, '$.stake_fen') AS INTEGER) =
                NEW.stake_fen
            AND json_extract(ticket.value, '$.candidate.payout_policy_version') =
                NEW.payout_policy_version
            AND (NEW.status = 'LOST' OR CAST(json_extract(
                ticket.value, '$.potential_gross_payout_fen'
            ) AS INTEGER) = NEW.gross_payout_fen)
        )
        BEGIN
            SELECT RAISE(ABORT, 'revision ticket settlement lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_ticket_settlements_correction_insert
        BEFORE INSERT ON ticket_settlements
        WHEN NEW.supersedes_settlement_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ticket_settlements previous
            WHERE previous.settlement_id = NEW.supersedes_settlement_id
            AND previous.scope_kind = NEW.scope_kind
            AND previous.parent_analysis_run_id = NEW.parent_analysis_run_id
            AND previous.decision_scope_id = NEW.decision_scope_id
            AND previous.portfolio_id = NEW.portfolio_id
            AND previous.ticket_id = NEW.ticket_id
            AND previous.settled_at_utc <= NEW.settled_at_utc
            AND json_valid(NEW.settlement_json)
            AND json_type(
                NEW.settlement_json, '$.match_result_ids'
            ) = 'array'
            AND json_array_length(
                NEW.settlement_json, '$.match_result_ids'
            ) = (
                SELECT COUNT(*)
                FROM ticket_settlement_match_results previous_link
                WHERE previous_link.settlement_id = previous.settlement_id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM ticket_settlement_match_results previous_link
                WHERE previous_link.settlement_id = previous.settlement_id
                AND NOT EXISTS (
                    SELECT 1
                    FROM json_each(
                        NEW.settlement_json, '$.match_result_ids'
                    ) current_link
                    JOIN match_results current_result
                      ON current_result.match_result_id = current_link.value
                    WHERE CAST(current_link.key AS INTEGER) + 1 =
                        previous_link.leg_no
                    AND (
                        current_result.match_result_id =
                            previous_link.match_result_id
                        OR current_result.supersedes_match_result_id =
                            previous_link.match_result_id
                    )
                )
            )
            AND EXISTS (
                SELECT 1
                FROM ticket_settlement_match_results previous_link
                JOIN json_each(
                    NEW.settlement_json, '$.match_result_ids'
                ) current_link
                  ON CAST(current_link.key AS INTEGER) + 1 =
                     previous_link.leg_no
                WHERE previous_link.settlement_id = previous.settlement_id
                AND current_link.value <> previous_link.match_result_id
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'settlement correction must reference a not-later version');
        END
        """,
        """
        CREATE TRIGGER trg_ticket_settlement_results_lineage_insert
        BEFORE INSERT ON ticket_settlement_match_results
        WHEN NOT EXISTS (
            SELECT 1 FROM ticket_settlements settlement
            JOIN match_results result
              ON result.match_result_id = NEW.match_result_id
             AND result.internal_match_id = NEW.internal_match_id
            WHERE settlement.settlement_id = NEW.settlement_id
            AND result.ingested_at_utc <= settlement.settled_at_utc
            AND json_extract(
                settlement.settlement_json,
                '$.match_result_ids[' || (NEW.leg_no - 1) || ']'
            ) = NEW.match_result_id
            AND NOT EXISTS (
                SELECT 1 FROM match_results successor
                WHERE successor.supersedes_match_result_id =
                    result.match_result_id
                AND successor.available_at_utc <= settlement.settled_at_utc
                AND successor.ingested_at_utc <= settlement.settled_at_utc
            )
            AND (
                settlement.supersedes_settlement_id IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM ticket_settlement_match_results previous_link
                    WHERE previous_link.settlement_id =
                        settlement.supersedes_settlement_id
                    AND previous_link.leg_no = NEW.leg_no
                    AND (
                        result.match_result_id = previous_link.match_result_id
                        OR result.supersedes_match_result_id =
                            previous_link.match_result_id
                    )
                )
            )
            AND (
                (settlement.scope_kind = 'ANALYSIS_RUN' AND EXISTS (
                    SELECT 1 FROM ticket_legs leg
                    WHERE leg.ticket_id = settlement.base_ticket_id
                    AND leg.internal_match_id = NEW.internal_match_id
                )) OR
                (settlement.scope_kind = 'PORTFOLIO_REVISION' AND EXISTS (
                    SELECT 1
                    FROM portfolio_revisions revision,
                         json_each(revision.revision_json, '$.portfolios') portfolio,
                         json_each(portfolio.value, '$.tickets') ticket,
                         json_each(ticket.value, '$.candidate.legs') leg
                    WHERE revision.portfolio_revision_id =
                        settlement.portfolio_revision_id
                    AND json_extract(portfolio.value, '$.portfolio_id') =
                        settlement.portfolio_id
                    AND json_extract(ticket.value, '$.ticket_id') =
                        settlement.ticket_id
                    AND json_extract(leg.value, '$.match_id') =
                        NEW.internal_match_id
                ))
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'ticket settlement result lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_portfolio_settlements_completed_parent_insert
        BEFORE INSERT ON portfolio_settlements
        WHEN NOT EXISTS (
            SELECT 1 FROM analysis_runs r
            WHERE r.analysis_run_id = NEW.parent_analysis_run_id
            AND r.status = 'COMPLETED'
            AND r.completed_at_utc IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'portfolio settlement requires a completed AnalysisRun');
        END
        """,
        """
        CREATE TRIGGER trg_portfolio_settlements_base_lineage_insert
        BEFORE INSERT ON portfolio_settlements
        WHEN NEW.scope_kind = 'ANALYSIS_RUN' AND NOT EXISTS (
            SELECT 1 FROM portfolios p
            JOIN analysis_runs r ON r.analysis_run_id = p.analysis_run_id
            WHERE p.portfolio_id = NEW.base_portfolio_id
            AND NEW.portfolio_id = p.portfolio_id
            AND NEW.parent_analysis_run_id = r.analysis_run_id
            AND NEW.decision_scope_id = r.analysis_run_id
            AND NEW.budget_fen = p.budget_fen
            AND NEW.total_stake_fen = p.total_stake_fen
            AND NEW.cash_fen = p.unused_budget_fen
            AND r.status = 'COMPLETED'
            AND r.completed_at_utc IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'base portfolio settlement lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_portfolio_settlements_revision_lineage_insert
        BEFORE INSERT ON portfolio_settlements
        WHEN NEW.scope_kind = 'PORTFOLIO_REVISION' AND NOT EXISTS (
            SELECT 1
            FROM portfolio_revisions revision,
                 json_each(revision.revision_json, '$.portfolios') portfolio
            WHERE revision.portfolio_revision_id = NEW.portfolio_revision_id
            AND revision.parent_analysis_run_id = NEW.parent_analysis_run_id
            AND NEW.decision_scope_id = revision.portfolio_revision_id
            AND json_extract(portfolio.value, '$.portfolio_id') = NEW.portfolio_id
            AND CAST(json_extract(portfolio.value, '$.budget_fen') AS INTEGER) =
                NEW.budget_fen
            AND CAST(json_extract(portfolio.value, '$.total_stake_fen') AS INTEGER) =
                NEW.total_stake_fen
            AND CAST(json_extract(portfolio.value, '$.unused_budget_fen') AS INTEGER) =
                NEW.cash_fen
        )
        BEGIN
            SELECT RAISE(ABORT, 'revision portfolio settlement lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_portfolio_settlements_correction_insert
        BEFORE INSERT ON portfolio_settlements
        WHEN NEW.supersedes_portfolio_settlement_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM portfolio_settlements previous
            WHERE previous.portfolio_settlement_id =
                NEW.supersedes_portfolio_settlement_id
            AND previous.scope_kind = NEW.scope_kind
            AND previous.parent_analysis_run_id = NEW.parent_analysis_run_id
            AND previous.decision_scope_id = NEW.decision_scope_id
            AND previous.portfolio_id = NEW.portfolio_id
            AND previous.settled_at_utc <= NEW.settled_at_utc
            AND previous.ticket_count = NEW.ticket_count
            AND NEW.ticket_count > 0
        )
        BEGIN
            SELECT RAISE(ABORT, 'portfolio correction must reference a not-later version');
        END
        """,
        """
        CREATE TRIGGER trg_portfolio_settlement_tickets_lineage_insert
        BEFORE INSERT ON portfolio_settlement_tickets
        WHEN NOT EXISTS (
            SELECT 1
            FROM portfolio_settlements portfolio
            JOIN ticket_settlements ticket
              ON ticket.settlement_id = NEW.settlement_id
            WHERE portfolio.portfolio_settlement_id =
                NEW.portfolio_settlement_id
            AND ticket.scope_kind = portfolio.scope_kind
            AND ticket.parent_analysis_run_id = portfolio.parent_analysis_run_id
            AND ticket.decision_scope_id = portfolio.decision_scope_id
            AND ticket.portfolio_id = portfolio.portfolio_id
            AND ticket.settlement_policy_version =
                portfolio.settlement_policy_version
            AND ticket.settled_at_utc <= portfolio.settled_at_utc
            AND NOT EXISTS (
                SELECT 1 FROM ticket_settlements successor
                WHERE successor.supersedes_settlement_id = ticket.settlement_id
                AND successor.settled_at_utc <= portfolio.settled_at_utc
            )
            AND (
                SELECT COUNT(*) FROM portfolio_settlement_tickets current_link
                WHERE current_link.portfolio_settlement_id =
                    portfolio.portfolio_settlement_id
            ) < portfolio.ticket_count
            AND NOT EXISTS (
                SELECT 1
                FROM portfolio_settlement_tickets current_link
                JOIN ticket_settlements current_ticket
                  ON current_ticket.settlement_id = current_link.settlement_id
                WHERE current_link.portfolio_settlement_id =
                    portfolio.portfolio_settlement_id
                AND current_ticket.ticket_id = ticket.ticket_id
            )
            AND (
                portfolio.supersedes_portfolio_settlement_id IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM portfolio_settlement_tickets previous_link
                    JOIN ticket_settlements previous_ticket
                      ON previous_ticket.settlement_id =
                         previous_link.settlement_id
                    WHERE previous_link.portfolio_settlement_id =
                        portfolio.supersedes_portfolio_settlement_id
                    AND previous_ticket.ticket_id = ticket.ticket_id
                    AND (
                        ticket.settlement_id = previous_ticket.settlement_id
                        OR ticket.supersedes_settlement_id =
                            previous_ticket.settlement_id
                    )
                )
            )
            AND (
                portfolio.supersedes_portfolio_settlement_id IS NULL
                OR (
                    SELECT COUNT(*)
                    FROM portfolio_settlement_tickets current_link
                    WHERE current_link.portfolio_settlement_id =
                        portfolio.portfolio_settlement_id
                ) + 1 < portfolio.ticket_count
                OR ticket.settlement_id <> (
                    SELECT previous_ticket.settlement_id
                    FROM portfolio_settlement_tickets previous_link
                    JOIN ticket_settlements previous_ticket
                      ON previous_ticket.settlement_id =
                         previous_link.settlement_id
                    WHERE previous_link.portfolio_settlement_id =
                        portfolio.supersedes_portfolio_settlement_id
                    AND previous_ticket.ticket_id = ticket.ticket_id
                )
                OR EXISTS (
                    SELECT 1
                    FROM portfolio_settlement_tickets current_link
                    JOIN ticket_settlements current_ticket
                      ON current_ticket.settlement_id = current_link.settlement_id
                    JOIN portfolio_settlement_tickets previous_link
                      ON previous_link.portfolio_settlement_id =
                         portfolio.supersedes_portfolio_settlement_id
                    JOIN ticket_settlements previous_ticket
                      ON previous_ticket.settlement_id =
                         previous_link.settlement_id
                     AND previous_ticket.ticket_id = current_ticket.ticket_id
                    WHERE current_link.portfolio_settlement_id =
                        portfolio.portfolio_settlement_id
                    AND current_ticket.settlement_id <>
                        previous_ticket.settlement_id
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'portfolio ticket settlement lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_backtest_runs_replay_insert
        BEFORE INSERT ON backtest_runs
        WHEN NEW.replay_of_backtest_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM backtest_runs previous
            WHERE previous.backtest_run_id = NEW.replay_of_backtest_run_id
            AND previous.status = 'COMPLETED'
            AND previous.completed_at_utc <= NEW.started_at_utc
            AND previous.backtest_version = NEW.backtest_version
            AND previous.data_mode = NEW.data_mode
            AND previous.date_from = NEW.date_from
            AND previous.date_to = NEW.date_to
            AND previous.strategy_version = NEW.strategy_version
            AND previous.strategy_config_hash = NEW.strategy_config_hash
            AND previous.code_revision = NEW.code_revision
            AND previous.schema_version = NEW.schema_version
            AND previous.engine_version = NEW.engine_version
            AND previous.config_hash = NEW.config_hash
            AND previous.input_manifest_version = NEW.input_manifest_version
            AND previous.input_manifest_hash = NEW.input_manifest_hash
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest replay must reference an earlier run');
        END
        """,
        """
        CREATE TRIGGER trg_backtest_slices_lineage_insert
        BEFORE INSERT ON backtest_slices
        WHEN NOT EXISTS (
            SELECT 1 FROM analysis_runs analysis
            JOIN backtest_runs run ON run.backtest_run_id = NEW.backtest_run_id
            WHERE analysis.analysis_run_id = NEW.parent_analysis_run_id
            AND analysis.status = 'COMPLETED'
            AND analysis.completed_at_utc IS NOT NULL
            AND analysis.as_of_at_utc = NEW.decision_as_of_at_utc
            AND run.data_mode = NEW.data_mode
            AND NEW.slice_version = 'BACKTEST_SLICE_RECORD_V2'
            AND json_valid(NEW.slice_manifest_json)
            AND json_extract(
                NEW.slice_manifest_json, '$.decision_input_manifest_hash'
            ) = analysis.input_manifest_hash
            AND julianday(NEW.decision_as_of_at_utc) <= julianday(json_extract(
                NEW.slice_manifest_json, '$.kickoff_from_utc'
            ))
            AND julianday(json_extract(
                NEW.slice_manifest_json, '$.kickoff_from_utc'
            )) <= julianday(json_extract(
                NEW.slice_manifest_json, '$.kickoff_to_utc'
            ))
            AND julianday(json_extract(
                NEW.slice_manifest_json, '$.kickoff_to_utc'
            )) < julianday(NEW.evaluation_as_of_at_utc)
            AND date(json_extract(
                NEW.slice_manifest_json, '$.kickoff_from_utc'
            )) >= run.date_from
            AND date(json_extract(
                NEW.slice_manifest_json, '$.kickoff_to_utc'
            )) <= run.date_to
            AND (
                COALESCE(json_array_length(
                    run.input_manifest_json, '$.expected_slice_ids'
                ), 0) = 0
                OR EXISTS (
                    SELECT 1 FROM json_each(
                        run.input_manifest_json, '$.expected_slice_ids'
                    ) expected
                    WHERE expected.value = NEW.backtest_slice_id
                    AND CAST(expected.key AS INTEGER) + 1 = NEW.slice_no
                )
            )
            AND json_type(
                NEW.slice_manifest_json, '$.expected_match_ids'
            ) = 'array'
            AND json_array_length(
                NEW.slice_manifest_json, '$.expected_match_ids'
            ) = NEW.match_count
            AND json_array_length(
                NEW.slice_manifest_json, '$.expected_match_ids'
            ) = (
                SELECT COUNT(DISTINCT expected.value)
                FROM json_each(
                    NEW.slice_manifest_json, '$.expected_match_ids'
                ) expected
                WHERE expected.type = 'text' AND expected.value <> ''
            )
            AND json_type(
                NEW.slice_manifest_json, '$.missing_decision_match_ids'
            ) = 'array'
            AND json_array_length(
                NEW.slice_manifest_json, '$.missing_decision_match_ids'
            ) = (
                SELECT COUNT(DISTINCT missing.value)
                FROM json_each(
                    NEW.slice_manifest_json, '$.missing_decision_match_ids'
                ) missing
                WHERE missing.type = 'text' AND missing.value <> ''
            )
            AND NOT EXISTS (
                SELECT 1 FROM json_each(
                    NEW.slice_manifest_json, '$.missing_decision_match_ids'
                ) missing
                WHERE NOT EXISTS (
                    SELECT 1 FROM json_each(
                        NEW.slice_manifest_json, '$.expected_match_ids'
                    ) expected
                    WHERE expected.value = missing.value
                )
                OR EXISTS (
                    SELECT 1 FROM analysis_run_matches match_link
                    WHERE match_link.analysis_run_id = analysis.analysis_run_id
                    AND match_link.internal_match_id = missing.value
                )
            )
            AND json_valid(analysis.input_manifest_json)
            AND json_type(analysis.input_manifest_json, '$.matches') = 'array'
            AND json_array_length(
                analysis.input_manifest_json, '$.matches'
            ) = NEW.match_count - json_array_length(
                NEW.slice_manifest_json, '$.missing_decision_match_ids'
            )
            AND json_array_length(
                analysis.input_manifest_json, '$.matches'
            ) = (
                SELECT COUNT(DISTINCT json_extract(
                    manifest_match.value, '$.match_id'
                ))
                FROM json_each(
                    analysis.input_manifest_json, '$.matches'
                ) manifest_match
                WHERE json_type(manifest_match.value, '$.match_id') = 'text'
                AND json_extract(manifest_match.value, '$.match_id') <> ''
            )
            AND NOT EXISTS (
                SELECT 1 FROM json_each(
                    NEW.slice_manifest_json, '$.expected_match_ids'
                ) expected
                WHERE NOT EXISTS (
                    SELECT 1 FROM json_each(
                        NEW.slice_manifest_json, '$.missing_decision_match_ids'
                    ) missing
                    WHERE missing.value = expected.value
                )
                AND json_extract(
                    analysis.input_manifest_json,
                    '$.matches[' || (
                        SELECT COUNT(*)
                        FROM json_each(
                            NEW.slice_manifest_json, '$.expected_match_ids'
                        ) prior
                        WHERE CAST(prior.key AS INTEGER) <
                            CAST(expected.key AS INTEGER)
                        AND NOT EXISTS (
                            SELECT 1 FROM json_each(
                                NEW.slice_manifest_json,
                                '$.missing_decision_match_ids'
                            ) missing
                            WHERE missing.value = prior.value
                        )
                    ) || '].match_id'
                ) IS NOT expected.value
            )
            AND NOT EXISTS (
                SELECT 1 FROM json_each(
                    analysis.input_manifest_json, '$.matches'
                ) manifest_match
                WHERE NOT EXISTS (
                    SELECT 1 FROM analysis_run_matches match_link
                    WHERE match_link.analysis_run_id = analysis.analysis_run_id
                    AND match_link.internal_match_id = json_extract(
                        manifest_match.value, '$.match_id'
                    )
                )
            )
            AND NOT EXISTS (
                SELECT 1 FROM analysis_run_matches match_link
                WHERE match_link.analysis_run_id = analysis.analysis_run_id
                AND NOT EXISTS (
                    SELECT 1 FROM json_each(
                        analysis.input_manifest_json, '$.matches'
                    ) manifest_match
                    WHERE json_extract(
                        manifest_match.value, '$.match_id'
                    ) = match_link.internal_match_id
                )
            )
            AND NOT EXISTS (
                SELECT 1 FROM analysis_run_matches match_link
                JOIN matches decision_match
                  ON decision_match.internal_match_id = match_link.internal_match_id
                WHERE match_link.analysis_run_id = analysis.analysis_run_id
                AND (
                    julianday(decision_match.kickoff_at_utc) < julianday(
                        json_extract(
                            NEW.slice_manifest_json, '$.kickoff_from_utc'
                        )
                    )
                    OR julianday(decision_match.kickoff_at_utc) > julianday(
                        json_extract(
                            NEW.slice_manifest_json, '$.kickoff_to_utc'
                        )
                    )
                )
            )
            AND json_type(
                NEW.slice_manifest_json, '$.match_result_ids'
            ) = 'array'
            AND json_array_length(
                NEW.slice_manifest_json, '$.match_result_ids'
            ) = NEW.settled_match_count
            AND json_array_length(
                NEW.slice_manifest_json, '$.match_result_ids'
            ) = (
                SELECT COUNT(DISTINCT result.internal_match_id)
                FROM json_each(
                    NEW.slice_manifest_json, '$.match_result_ids'
                ) linked
                JOIN match_results result
                  ON result.match_result_id = linked.value
            )
            AND NOT EXISTS (
                SELECT 1 FROM json_each(
                    NEW.slice_manifest_json, '$.match_result_ids'
                ) linked
                WHERE linked.type <> 'text'
                OR NOT EXISTS (
                    SELECT 1 FROM match_results result
                    JOIN analysis_run_matches match_link
                      ON match_link.analysis_run_id = analysis.analysis_run_id
                     AND match_link.internal_match_id = result.internal_match_id
                    WHERE result.match_result_id = linked.value
                    AND result.available_at_utc <= NEW.evaluation_as_of_at_utc
                    AND result.ingested_at_utc <= NEW.evaluation_as_of_at_utc
                    AND NOT EXISTS (
                        SELECT 1 FROM match_results successor
                        WHERE successor.supersedes_match_result_id =
                            result.match_result_id
                        AND successor.available_at_utc <=
                            NEW.evaluation_as_of_at_utc
                        AND successor.ingested_at_utc <=
                            NEW.evaluation_as_of_at_utc
                    )
                )
            )
            AND json_type(
                NEW.slice_manifest_json, '$.match_result_issues'
            ) = 'array'
            AND json_array_length(
                NEW.slice_manifest_json, '$.match_result_issues'
            ) = (
                SELECT COUNT(DISTINCT json_extract(
                    issue.value, '$.match_id'
                ))
                FROM json_each(
                    NEW.slice_manifest_json, '$.match_result_issues'
                ) issue
                WHERE json_type(issue.value, '$.match_id') = 'text'
            )
            AND NOT EXISTS (
                SELECT 1 FROM json_each(
                    NEW.slice_manifest_json, '$.match_result_issues'
                ) issue
                WHERE NOT EXISTS (
                    SELECT 1 FROM analysis_run_matches match_link
                    WHERE match_link.analysis_run_id = analysis.analysis_run_id
                    AND match_link.internal_match_id =
                        json_extract(issue.value, '$.match_id')
                )
                OR NOT EXISTS (
                    SELECT 1 FROM provider_match_mappings mapping
                    WHERE mapping.internal_match_id =
                        json_extract(issue.value, '$.match_id')
                    AND mapping.available_at_utc <=
                        NEW.evaluation_as_of_at_utc
                )
                OR EXISTS (
                    SELECT 1 FROM json_each(
                        NEW.slice_manifest_json, '$.match_result_ids'
                    ) linked
                    JOIN match_results result
                      ON result.match_result_id = linked.value
                    WHERE result.internal_match_id =
                        json_extract(issue.value, '$.match_id')
                )
            )
            AND NEW.settled_match_count + json_array_length(
                NEW.slice_manifest_json, '$.match_result_issues'
            ) + json_array_length(
                NEW.slice_manifest_json, '$.missing_decision_match_ids'
            ) <= NEW.match_count
            AND (
                (NEW.scope_kind = 'ANALYSIS_RUN'
                 AND NEW.decision_scope_id = analysis.analysis_run_id
                 AND NEW.portfolio_revision_id IS NULL) OR
                (NEW.scope_kind = 'PORTFOLIO_REVISION' AND EXISTS (
                    SELECT 1 FROM portfolio_revisions revision
                    WHERE revision.portfolio_revision_id =
                        NEW.portfolio_revision_id
                    AND revision.portfolio_revision_id = NEW.decision_scope_id
                    AND revision.parent_analysis_run_id = analysis.analysis_run_id
                ))
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest slice lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_backtest_metrics_lineage_insert
        BEFORE INSERT ON backtest_metric_snapshots
        WHEN NOT EXISTS (
            SELECT 1 FROM backtest_runs run
            WHERE run.backtest_run_id = NEW.backtest_run_id
            AND (
                (NEW.metric_scope = 'RUN' AND NEW.backtest_slice_id IS NULL) OR
                (NEW.metric_scope = 'SLICE' AND EXISTS (
                    SELECT 1 FROM backtest_slices slice
                    WHERE slice.backtest_slice_id = NEW.backtest_slice_id
                    AND slice.backtest_run_id = NEW.backtest_run_id
                    AND slice.evaluation_as_of_at_utc = NEW.as_of_at_utc
                ))
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest metric lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_backtest_metric_settlements_lineage_insert
        BEFORE INSERT ON backtest_metric_settlements
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_metric_snapshots metric
            JOIN portfolio_settlements settlement
              ON settlement.portfolio_settlement_id =
                 NEW.portfolio_settlement_id
            WHERE metric.metric_snapshot_id = NEW.metric_snapshot_id
            AND settlement.settled_at_utc <= metric.as_of_at_utc
            AND EXISTS (
                SELECT 1 FROM backtest_slices slice
                WHERE slice.backtest_run_id = metric.backtest_run_id
                AND slice.parent_analysis_run_id =
                    settlement.parent_analysis_run_id
                AND slice.decision_scope_id = settlement.decision_scope_id
                AND settlement.settled_at_utc <= slice.evaluation_as_of_at_utc
                AND (metric.backtest_slice_id IS NULL OR
                     metric.backtest_slice_id = slice.backtest_slice_id)
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest metric settlement lineage is inconsistent');
        END
        """,
        """
        CREATE TRIGGER trg_backtest_metric_ticket_settlements_lineage_insert
        BEFORE INSERT ON backtest_metric_ticket_settlements
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_metric_snapshots metric
            JOIN ticket_settlements settlement
              ON settlement.settlement_id = NEW.settlement_id
            WHERE metric.metric_snapshot_id = NEW.metric_snapshot_id
            AND settlement.settled_at_utc <= metric.as_of_at_utc
            AND EXISTS (
                SELECT 1 FROM backtest_slices slice
                WHERE slice.backtest_run_id = metric.backtest_run_id
                AND slice.parent_analysis_run_id =
                    settlement.parent_analysis_run_id
                AND slice.decision_scope_id = settlement.decision_scope_id
                AND settlement.settled_at_utc <= slice.evaluation_as_of_at_utc
                AND (
                    metric.backtest_slice_id IS NULL
                    OR metric.backtest_slice_id = slice.backtest_slice_id
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest metric ticket lineage is inconsistent');
        END
        """,
    )


def _drop_triggers() -> None:
    for table_name in IMMUTABLE_INSERT_KEYS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_insert_existing")
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{action}")
    trigger_names = (
        "trg_match_results_provider_mapping_insert",
        "trg_match_results_supersession_insert",
        "trg_ticket_settlements_completed_parent_insert",
        "trg_ticket_settlements_base_lineage_insert",
        "trg_ticket_settlements_revision_lineage_insert",
        "trg_ticket_settlements_correction_insert",
        "trg_ticket_settlement_results_lineage_insert",
        "trg_portfolio_settlements_completed_parent_insert",
        "trg_portfolio_settlements_base_lineage_insert",
        "trg_portfolio_settlements_revision_lineage_insert",
        "trg_portfolio_settlements_correction_insert",
        "trg_portfolio_settlement_tickets_lineage_insert",
        "trg_backtest_runs_replay_insert",
        "trg_backtest_slices_lineage_insert",
        "trg_backtest_metrics_lineage_insert",
        "trg_backtest_metric_settlements_lineage_insert",
        "trg_backtest_metric_ticket_settlements_lineage_insert",
    )
    for trigger_name in trigger_names:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _require_sqlite_dialect() -> None:
    dialect = op.get_context().dialect.name
    if dialect != "sqlite":
        raise RuntimeError(
            f"Unsupported database backend '{dialect}'; "
            "football-system v0.4.0 supports SQLite only."
        )
