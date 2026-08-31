"""add portfolio cash, exposure, and stress artifacts

Revision ID: 4f9b2d7c1a60
Revises: e3754eb9a102
Create Date: 2026-08-31 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f9b2d7c1a60"
down_revision: Union[str, None] = "e3754eb9a102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RUN_LOOKUPS = {
    "portfolio_cash_positions": "SELECT 1 FROM analysis_runs r JOIN portfolios p ON p.analysis_run_id = r.analysis_run_id WHERE p.portfolio_id = {row}.portfolio_id AND r.status = 'COMPLETED'",
    "portfolio_risk_reports": "SELECT 1 FROM analysis_runs r WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'",
    "portfolio_match_exposures": "SELECT 1 FROM analysis_runs r JOIN portfolio_risk_reports x ON x.analysis_run_id = r.analysis_run_id WHERE x.risk_report_id = {row}.risk_report_id AND r.status = 'COMPLETED'",
    "portfolio_selection_exposures": "SELECT 1 FROM analysis_runs r JOIN portfolio_risk_reports x ON x.analysis_run_id = r.analysis_run_id WHERE x.risk_report_id = {row}.risk_report_id AND r.status = 'COMPLETED'",
    "portfolio_stress_results": "SELECT 1 FROM analysis_runs r JOIN portfolio_risk_reports x ON x.analysis_run_id = r.analysis_run_id WHERE x.risk_report_id = {row}.risk_report_id AND r.status = 'COMPLETED'",
    "portfolio_stress_ticket_results": "SELECT 1 FROM analysis_runs r JOIN portfolio_risk_reports x ON x.analysis_run_id = r.analysis_run_id JOIN portfolio_stress_results s ON s.risk_report_id = x.risk_report_id WHERE s.scenario_id = {row}.scenario_id AND r.status = 'COMPLETED'",
}


def upgrade() -> None:
    op.create_table(
        "portfolio_cash_positions",
        sa.Column("cash_position_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_id", sa.String(length=160), nullable=False),
        sa.Column("amount_fen", sa.Integer(), nullable=False),
        sa.Column("expected_profit_fen", sa.Numeric(24, 8), nullable=False),
        sa.CheckConstraint("amount_fen >= 0", name="ck_cash_position_amount"),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["portfolios.portfolio_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("cash_position_id"),
        sa.UniqueConstraint("portfolio_id"),
    )
    op.create_table(
        "portfolio_risk_reports",
        sa.Column("risk_report_id", sa.String(length=160), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_id", sa.String(length=160), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("budget_fen", sa.Integer(), nullable=False),
        sa.Column("total_stake_fen", sa.Integer(), nullable=False),
        sa.Column("cash_fen", sa.Integer(), nullable=False),
        sa.Column("cash_ratio", sa.Numeric(18, 12), nullable=True),
        sa.Column("expected_profit_fen", sa.Numeric(24, 8), nullable=False),
        sa.Column("total_stake_at_risk_fen", sa.Integer(), nullable=False),
        sa.Column("max_single_ticket_exposure_fen", sa.Integer(), nullable=False),
        sa.Column("max_match_exposure_fen", sa.Integer(), nullable=False),
        sa.CheckConstraint("budget_fen >= 0", name="ck_risk_budget"),
        sa.CheckConstraint("total_stake_fen >= 0", name="ck_risk_stake"),
        sa.CheckConstraint("cash_fen >= 0", name="ck_risk_cash"),
        sa.CheckConstraint(
            "total_stake_fen + cash_fen = budget_fen",
            name="ck_risk_capital_balance",
        ),
        sa.CheckConstraint(
            "total_stake_at_risk_fen >= 0", name="ck_risk_stake_at_risk"
        ),
        sa.CheckConstraint(
            "max_single_ticket_exposure_fen >= 0", name="ck_risk_max_ticket"
        ),
        sa.CheckConstraint(
            "max_match_exposure_fen >= 0", name="ck_risk_max_match"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["portfolios.portfolio_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("risk_report_id"),
        sa.UniqueConstraint("portfolio_id"),
    )
    op.create_table(
        "portfolio_match_exposures",
        sa.Column("exposure_id", sa.String(length=160), nullable=False),
        sa.Column("risk_report_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("exposed_stake_fen", sa.Integer(), nullable=False),
        sa.Column("budget_ratio", sa.Numeric(18, 12), nullable=True),
        sa.Column("deployed_ratio", sa.Numeric(18, 12), nullable=True),
        sa.Column("ticket_count", sa.Integer(), nullable=False),
        sa.Column("ticket_ids_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "exposed_stake_fen >= 0", name="ck_match_exposure_stake"
        ),
        sa.CheckConstraint("ticket_count > 0", name="ck_match_exposure_tickets"),
        sa.ForeignKeyConstraint(
            ["risk_report_id"],
            ["portfolio_risk_reports.risk_report_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("exposure_id"),
        sa.UniqueConstraint(
            "risk_report_id",
            "internal_match_id",
            name="uq_risk_match_exposure",
        ),
    )
    op.create_table(
        "portfolio_selection_exposures",
        sa.Column("exposure_id", sa.String(length=160), nullable=False),
        sa.Column("risk_report_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("market_key", sa.String(length=120), nullable=False),
        sa.Column("selection_key", sa.String(length=64), nullable=False),
        sa.Column("exposed_stake_fen", sa.Integer(), nullable=False),
        sa.Column("budget_ratio", sa.Numeric(18, 12), nullable=True),
        sa.Column("deployed_ratio", sa.Numeric(18, 12), nullable=True),
        sa.Column("ticket_count", sa.Integer(), nullable=False),
        sa.Column("ticket_ids_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "exposed_stake_fen >= 0", name="ck_selection_exposure_stake"
        ),
        sa.CheckConstraint(
            "ticket_count > 0", name="ck_selection_exposure_tickets"
        ),
        sa.ForeignKeyConstraint(
            ["risk_report_id"],
            ["portfolio_risk_reports.risk_report_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("exposure_id"),
        sa.UniqueConstraint(
            "risk_report_id",
            "internal_match_id",
            "market_key",
            "selection_key",
            name="uq_risk_selection_exposure",
        ),
    )
    op.create_table(
        "portfolio_stress_results",
        sa.Column("scenario_id", sa.String(length=160), nullable=False),
        sa.Column("risk_report_id", sa.String(length=160), nullable=False),
        sa.Column("portfolio_id", sa.String(length=160), nullable=False),
        sa.Column("scenario_key", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("outcomes_json", sa.Text(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("scenario_exposed_stake_fen", sa.Integer(), nullable=False),
        sa.Column("scenario_exposure_ratio", sa.Numeric(18, 12), nullable=True),
        sa.Column("gross_payout_fen", sa.Integer(), nullable=True),
        sa.Column("ending_capital_fen", sa.Integer(), nullable=True),
        sa.Column("profit_loss_fen", sa.Integer(), nullable=True),
        sa.Column("capital_recovery_ratio", sa.Numeric(24, 8), nullable=True),
        sa.Column("minimum_ending_capital_fen", sa.Integer(), nullable=False),
        sa.Column("maximum_ending_capital_fen", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "scenario_exposed_stake_fen >= 0", name="ck_stress_exposed_stake"
        ),
        sa.CheckConstraint(
            "minimum_ending_capital_fen >= 0", name="ck_stress_min_capital"
        ),
        sa.CheckConstraint(
            "maximum_ending_capital_fen >= minimum_ending_capital_fen",
            name="ck_stress_capital_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["risk_report_id"],
            ["portfolio_risk_reports.risk_report_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["portfolios.portfolio_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("scenario_id"),
        sa.UniqueConstraint(
            "risk_report_id", "scenario_key", name="uq_risk_stress_scenario"
        ),
    )
    op.create_table(
        "portfolio_stress_ticket_results",
        sa.Column("scenario_id", sa.String(length=160), nullable=False),
        sa.Column("ticket_id", sa.String(length=160), nullable=False),
        sa.Column("result_state", sa.String(length=64), nullable=False),
        sa.Column("gross_payout_fen", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["portfolio_stress_results.scenario_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.ticket_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("scenario_id", "ticket_id"),
    )
    _install_triggers(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    _drop_triggers(connection)
    op.drop_table("portfolio_stress_ticket_results")
    op.drop_table("portfolio_stress_results")
    op.drop_table("portfolio_selection_exposures")
    op.drop_table("portfolio_match_exposures")
    op.drop_table("portfolio_risk_reports")
    op.drop_table("portfolio_cash_positions")


def _install_triggers(connection: object) -> None:
    for table_name, lookup in RUN_LOOKUPS.items():
        for action in ("INSERT", "UPDATE", "DELETE"):
            rows = ("NEW",) if action == "INSERT" else ("OLD",)
            if action == "UPDATE":
                rows = ("OLD", "NEW")
            condition = " OR ".join(
                f"EXISTS ({lookup.format(row=row)})" for row in rows
            )
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER trg_{table_name}_sealed_{action.lower()}
                BEFORE {action} ON {table_name}
                WHEN {condition}
                BEGIN
                    SELECT RAISE(ABORT, 'sealed AnalysisRun artifacts are immutable');
                END
                """
            )


def _drop_triggers(connection: object) -> None:
    for table_name in RUN_LOOKUPS:
        for action in ("insert", "update", "delete"):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_sealed_{action}"
            )
