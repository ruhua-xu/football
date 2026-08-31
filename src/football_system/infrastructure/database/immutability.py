from __future__ import annotations

from sqlalchemy import Connection


RUN_LOOKUPS = {
    "analysis_run_matches": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "market_probabilities": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "market_probability_outcomes": (
        "SELECT 1 FROM analysis_runs r JOIN market_probabilities p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.market_probability_id = {row}.market_probability_id "
        "AND r.status = 'COMPLETED'"
    ),
    "market_probability_inputs": (
        "SELECT 1 FROM analysis_runs r JOIN market_probabilities p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.market_probability_id = {row}.market_probability_id "
        "AND r.status = 'COMPLETED'"
    ),
    "quant_predictions": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "quant_prediction_outcomes": (
        "SELECT 1 FROM analysis_runs r JOIN quant_predictions p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.quant_prediction_id = {row}.quant_prediction_id "
        "AND r.status = 'COMPLETED'"
    ),
    "final_predictions": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "final_prediction_outcomes": (
        "SELECT 1 FROM analysis_runs r JOIN final_predictions p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.final_prediction_id = {row}.final_prediction_id "
        "AND r.status = 'COMPLETED'"
    ),
    "bet_candidates": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "ticket_candidates": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "ticket_candidate_legs": (
        "SELECT 1 FROM analysis_runs r JOIN ticket_candidates c "
        "ON c.analysis_run_id = r.analysis_run_id "
        "WHERE c.ticket_candidate_id = {row}.ticket_candidate_id "
        "AND r.status = 'COMPLETED'"
    ),
    "portfolios": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "tickets": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.portfolio_id = {row}.portfolio_id AND r.status = 'COMPLETED'"
    ),
    "ticket_legs": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "JOIN tickets t ON t.portfolio_id = p.portfolio_id "
        "WHERE t.ticket_id = {row}.ticket_id AND r.status = 'COMPLETED'"
    ),
}

SOURCE_TABLES = (
    "providers",
    "bookmakers",
    "competitions",
    "teams",
    "matches",
    "provider_match_mappings",
    "market_odds_snapshots",
    "market_odds_quotes",
    "sporttery_bonus_snapshots",
    "sporttery_bonus_quotes",
    "manual_quant_inputs",
    "manual_quant_input_outcomes",
)

SOURCE_CHILD_INSERT_LOOKUPS = {
    "market_odds_quotes": (
        "SELECT 1 FROM analysis_runs r JOIN analysis_run_matches m "
        "ON m.analysis_run_id = r.analysis_run_id "
        "WHERE m.market_odds_snapshot_id = NEW.snapshot_id "
        "AND r.status = 'COMPLETED'"
    ),
    "sporttery_bonus_quotes": (
        "SELECT 1 FROM analysis_runs r JOIN analysis_run_matches m "
        "ON m.analysis_run_id = r.analysis_run_id "
        "WHERE m.sporttery_bonus_snapshot_id = NEW.snapshot_id "
        "AND r.status = 'COMPLETED'"
    ),
    "manual_quant_input_outcomes": (
        "SELECT 1 FROM analysis_runs r JOIN quant_predictions p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.manual_input_id = NEW.input_id AND r.status = 'COMPLETED'"
    ),
}


def install_sqlite_immutability_triggers(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_sealed_update
        BEFORE UPDATE ON analysis_runs
        WHEN OLD.status = 'COMPLETED'
        BEGIN
            SELECT RAISE(ABORT, 'sealed AnalysisRun is immutable');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_sealed_delete
        BEFORE DELETE ON analysis_runs
        WHEN OLD.status = 'COMPLETED'
        BEGIN
            SELECT RAISE(ABORT, 'sealed AnalysisRun is immutable');
        END
        """
    )
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
                CREATE TRIGGER IF NOT EXISTS trg_{table_name}_sealed_{action.lower()}
                BEFORE {action} ON {table_name}
                WHEN {condition}
                BEGIN
                    SELECT RAISE(ABORT, 'sealed AnalysisRun artifacts are immutable');
                END
                """
            )
    for table_name in SOURCE_TABLES:
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'source records are append-only');
                END
                """
            )
    for table_name, lookup in SOURCE_CHILD_INSERT_LOOKUPS.items():
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table_name}_sealed_insert
            BEFORE INSERT ON {table_name}
            WHEN EXISTS ({lookup})
            BEGIN
                SELECT RAISE(ABORT, 'sealed source aggregate is immutable');
            END
            """
        )
