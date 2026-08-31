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
    "portfolio_cash_positions": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.portfolio_id = {row}.portfolio_id AND r.status = 'COMPLETED'"
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
    "portfolio_risk_reports": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "WHERE p.portfolio_id = {row}.portfolio_id AND r.status = 'COMPLETED'"
    ),
    "portfolio_match_exposures": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "JOIN portfolio_risk_reports x ON x.portfolio_id = p.portfolio_id "
        "WHERE x.risk_report_id = {row}.risk_report_id "
        "AND r.status = 'COMPLETED'"
    ),
    "portfolio_selection_exposures": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "JOIN portfolio_risk_reports x ON x.portfolio_id = p.portfolio_id "
        "WHERE x.risk_report_id = {row}.risk_report_id "
        "AND r.status = 'COMPLETED'"
    ),
    "portfolio_stress_results": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "JOIN portfolio_risk_reports x ON x.portfolio_id = p.portfolio_id "
        "WHERE x.risk_report_id = {row}.risk_report_id "
        "AND r.status = 'COMPLETED'"
    ),
    "portfolio_stress_ticket_results": (
        "SELECT 1 FROM analysis_runs r JOIN portfolios p "
        "ON p.analysis_run_id = r.analysis_run_id "
        "JOIN portfolio_risk_reports x ON x.portfolio_id = p.portfolio_id "
        "JOIN portfolio_stress_results s ON s.risk_report_id = x.risk_report_id "
        "WHERE s.scenario_id = {row}.scenario_id AND r.status = 'COMPLETED'"
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

IMMUTABLE_INSERT_KEYS = {
    "analysis_runs": (("analysis_run_id",),),
    "providers": (("provider_id",), ("code",)),
    "bookmakers": (("bookmaker_id",), ("code",)),
    "competitions": (("competition_id",), ("canonical_key",)),
    "teams": (("team_id",), ("canonical_key",)),
    "matches": (("internal_match_id",),),
    "provider_match_mappings": (
        ("mapping_id",),
        ("provider_id", "external_namespace", "external_match_id"),
    ),
    "market_odds_snapshots": (
        ("snapshot_id",),
        ("provider_id", "source_snapshot_key"),
    ),
    "market_odds_quotes": (("snapshot_id", "selection_key"),),
    "sporttery_bonus_snapshots": (
        ("snapshot_id",),
        ("provider_id", "source_snapshot_key"),
    ),
    "sporttery_bonus_quotes": (("snapshot_id", "selection_key"),),
    "manual_quant_inputs": (
        ("input_id",),
        ("internal_match_id", "market_key", "available_at_utc", "payload_hash"),
    ),
    "manual_quant_input_outcomes": (("input_id", "selection_key"),),
    "analysis_run_matches": (("analysis_run_id", "internal_match_id"),),
    "market_probabilities": (
        ("market_probability_id",),
        ("analysis_run_id", "internal_match_id", "market_key"),
    ),
    "market_probability_outcomes": (
        ("market_probability_id", "selection_key"),
    ),
    "market_probability_inputs": (
        ("market_probability_id", "market_odds_snapshot_id"),
    ),
    "quant_predictions": (
        ("quant_prediction_id",),
        ("analysis_run_id", "internal_match_id", "market_key"),
    ),
    "quant_prediction_outcomes": (("quant_prediction_id", "selection_key"),),
    "final_predictions": (
        ("final_prediction_id",),
        ("analysis_run_id", "internal_match_id", "market_key"),
    ),
    "final_prediction_outcomes": (("final_prediction_id", "selection_key"),),
    "bet_candidates": (
        ("candidate_id",),
        ("analysis_run_id", "internal_match_id", "market_key", "selection_key"),
    ),
    "ticket_candidates": (("ticket_candidate_id",),),
    "ticket_candidate_legs": (
        ("ticket_candidate_id", "leg_no"),
        ("ticket_candidate_id", "internal_match_id"),
    ),
    "portfolios": (
        ("portfolio_id",),
        ("analysis_run_id", "budget_fen"),
    ),
    "portfolio_cash_positions": (
        ("cash_position_id",),
        ("portfolio_id",),
    ),
    "tickets": (("ticket_id",), ("portfolio_id", "ticket_no")),
    "ticket_legs": (
        ("ticket_id", "leg_no"),
        ("ticket_id", "internal_match_id"),
    ),
    "portfolio_risk_reports": (("risk_report_id",), ("portfolio_id",)),
    "portfolio_match_exposures": (
        ("exposure_id",),
        ("risk_report_id", "internal_match_id"),
    ),
    "portfolio_selection_exposures": (
        ("exposure_id",),
        ("risk_report_id", "internal_match_id", "market_key", "selection_key"),
    ),
    "portfolio_stress_results": (
        ("scenario_id",),
        ("risk_report_id", "scenario_key"),
    ),
    "portfolio_stress_ticket_results": (("scenario_id", "ticket_id"),),
}

POST_RUN_PARENT_LOOKUPS = {
    "analysis_packets": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = NEW.parent_analysis_run_id "
        "AND r.status = 'COMPLETED'"
    ),
    "llm_review_artifacts": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = NEW.parent_analysis_run_id "
        "AND r.status = 'COMPLETED'"
    ),
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
    "analysis_packets": (
        "SELECT 1 FROM analysis_packets p WHERE p.packet_id = NEW.packet_id "
        "OR (p.parent_analysis_run_id = NEW.parent_analysis_run_id "
        "AND p.schema_version = NEW.schema_version)"
    ),
    "llm_review_artifacts": (
        "SELECT 1 FROM llm_review_artifacts a "
        "WHERE a.review_artifact_id = NEW.review_artifact_id "
        "OR (a.packet_id = NEW.packet_id "
        "AND a.normalized_review_hash = NEW.normalized_review_hash "
        "AND a.validator_version = NEW.validator_version)"
    ),
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

LINEAGE_GUARDS = {
    "portfolio_cash_positions": (
        "NOT EXISTS (SELECT 1 FROM portfolios p "
        "WHERE p.portfolio_id = NEW.portfolio_id "
        "AND p.unused_budget_fen = NEW.amount_fen "
        "AND NEW.expected_profit_fen = 0)"
    ),
    "portfolio_risk_reports": (
        "NOT EXISTS (SELECT 1 FROM portfolios p "
        "WHERE p.portfolio_id = NEW.portfolio_id "
        "AND p.analysis_run_id = NEW.analysis_run_id "
        "AND p.budget_fen = NEW.budget_fen "
        "AND p.total_stake_fen = NEW.total_stake_fen "
        "AND p.unused_budget_fen = NEW.cash_fen)"
    ),
    "portfolio_match_exposures": (
        "NOT EXISTS (SELECT 1 FROM portfolio_risk_reports x "
        "JOIN tickets t ON t.portfolio_id = x.portfolio_id "
        "JOIN ticket_legs l ON l.ticket_id = t.ticket_id "
        "WHERE x.risk_report_id = NEW.risk_report_id "
        "AND l.internal_match_id = NEW.internal_match_id)"
    ),
    "portfolio_selection_exposures": (
        "NOT EXISTS (SELECT 1 FROM portfolio_risk_reports x "
        "JOIN tickets t ON t.portfolio_id = x.portfolio_id "
        "JOIN ticket_legs l ON l.ticket_id = t.ticket_id "
        "JOIN bet_candidates b ON b.candidate_id = l.candidate_id "
        "WHERE x.risk_report_id = NEW.risk_report_id "
        "AND l.internal_match_id = NEW.internal_match_id "
        "AND b.market_key = NEW.market_key "
        "AND b.selection_key = NEW.selection_key)"
    ),
    "portfolio_stress_results": (
        "NOT EXISTS (SELECT 1 FROM portfolio_risk_reports x "
        "WHERE x.risk_report_id = NEW.risk_report_id "
        "AND x.portfolio_id = NEW.portfolio_id)"
    ),
    "portfolio_stress_ticket_results": (
        "NOT EXISTS (SELECT 1 FROM portfolio_stress_results s "
        "JOIN portfolio_risk_reports x ON x.risk_report_id = s.risk_report_id "
        "JOIN tickets t ON t.ticket_id = NEW.ticket_id "
        "WHERE s.scenario_id = NEW.scenario_id "
        "AND s.portfolio_id = x.portfolio_id "
        "AND t.portfolio_id = s.portfolio_id)"
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
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_completed_insert
        BEFORE INSERT ON analysis_runs
        WHEN NEW.status = 'COMPLETED'
        BEGIN
            SELECT RAISE(ABORT, 'AnalysisRun must be completed by a validated transition');
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
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_completion_risk_graph
        BEFORE UPDATE OF status ON analysis_runs
        WHEN NEW.status = 'COMPLETED' AND OLD.status <> 'COMPLETED' AND (
            NEW.completed_at_utc IS NULL
            OR NOT EXISTS (
                SELECT 1 FROM analysis_run_matches m
                WHERE m.analysis_run_id = NEW.analysis_run_id
            )
            OR NOT EXISTS (
                SELECT 1 FROM portfolios p
                WHERE p.analysis_run_id = NEW.analysis_run_id
            )
            OR EXISTS (
                SELECT 1 FROM portfolios p
                WHERE p.analysis_run_id = NEW.analysis_run_id AND (
                    p.total_stake_fen + p.unused_budget_fen <> p.budget_fen
                    OR p.total_stake_fen <> COALESCE((
                        SELECT SUM(t.stake_fen) FROM tickets t
                        WHERE t.portfolio_id = p.portfolio_id
                    ), 0)
                    OR (SELECT COUNT(*) FROM portfolio_cash_positions c
                        WHERE c.portfolio_id = p.portfolio_id) <> 1
                    OR NOT EXISTS (
                        SELECT 1 FROM portfolio_cash_positions c
                        WHERE c.portfolio_id = p.portfolio_id
                        AND c.amount_fen = p.unused_budget_fen
                        AND c.expected_profit_fen = 0
                    )
                    OR (SELECT COUNT(*) FROM portfolio_risk_reports x
                        WHERE x.portfolio_id = p.portfolio_id) <> 1
                    OR p.status NOT IN ('RECOMMENDED', 'NO_BET')
                    OR (p.status = 'RECOMMENDED' AND (
                        p.no_bet_reason IS NOT NULL
                        OR NOT EXISTS (
                            SELECT 1 FROM tickets t
                            WHERE t.portfolio_id = p.portfolio_id
                        )
                    ))
                    OR (p.status = 'NO_BET' AND (
                        p.no_bet_reason IS NULL
                        OR p.no_bet_reason NOT IN (
                            'NO_BET_DATA_QUALITY',
                            'NO_BET_NO_VALUE',
                            'NO_BET_NO_FEASIBLE_TICKET',
                            'NO_BET_RISK_LIMIT'
                        )
                        OR EXISTS (
                            SELECT 1 FROM tickets t
                            WHERE t.portfolio_id = p.portfolio_id
                        )
                    ))
                )
            )
            OR EXISTS (
                SELECT 1
                FROM portfolio_risk_reports x
                JOIN portfolios p ON p.portfolio_id = x.portfolio_id
                WHERE p.analysis_run_id = NEW.analysis_run_id AND (
                    x.analysis_run_id <> p.analysis_run_id
                    OR x.budget_fen <> p.budget_fen
                    OR x.total_stake_fen <> p.total_stake_fen
                    OR x.cash_fen <> p.unused_budget_fen
                    OR (p.budget_fen = 0 AND x.cash_ratio IS NOT NULL)
                    OR (p.budget_fen > 0 AND (
                        x.cash_ratio IS NULL
                        OR ABS(x.cash_ratio -
                            CAST(p.unused_budget_fen AS REAL) / p.budget_fen
                        ) > 0.000000000001
                    ))
                    OR ABS(x.expected_profit_fen - COALESCE((
                        SELECT SUM(t.expected_profit_fen) FROM tickets t
                        WHERE t.portfolio_id = p.portfolio_id
                    ), 0)) > 0.00000001
                    OR x.total_stake_at_risk_fen <> p.total_stake_fen
                    OR x.max_single_ticket_exposure_fen <> COALESCE((
                        SELECT MAX(t.stake_fen) FROM tickets t
                        WHERE t.portfolio_id = p.portfolio_id
                    ), 0)
                    OR x.max_match_exposure_fen <> COALESCE((
                        SELECT MAX(e.exposed_stake_fen)
                        FROM portfolio_match_exposures e
                        WHERE e.risk_report_id = x.risk_report_id
                    ), 0)
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'completed AnalysisRun requires a valid risk graph');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_completion_exposures
        BEFORE UPDATE OF status ON analysis_runs
        WHEN NEW.status = 'COMPLETED' AND OLD.status <> 'COMPLETED' AND (
            EXISTS (
                SELECT 1
                FROM portfolio_risk_reports x
                JOIN portfolios p ON p.portfolio_id = x.portfolio_id
                JOIN tickets t ON t.portfolio_id = p.portfolio_id
                JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                WHERE p.analysis_run_id = NEW.analysis_run_id
                GROUP BY x.risk_report_id, l.internal_match_id
                HAVING NOT EXISTS (
                    SELECT 1 FROM portfolio_match_exposures e
                    WHERE e.risk_report_id = x.risk_report_id
                    AND e.internal_match_id = l.internal_match_id
                )
            )
            OR EXISTS (
                SELECT 1
                FROM portfolio_match_exposures e
                JOIN portfolio_risk_reports x ON x.risk_report_id = e.risk_report_id
                JOIN portfolios p ON p.portfolio_id = x.portfolio_id
                WHERE p.analysis_run_id = NEW.analysis_run_id AND (
                    e.exposed_stake_fen <> COALESCE((
                        SELECT SUM(t.stake_fen)
                        FROM tickets t
                        WHERE t.portfolio_id = p.portfolio_id
                        AND EXISTS (
                            SELECT 1 FROM ticket_legs l
                            WHERE l.ticket_id = t.ticket_id
                            AND l.internal_match_id = e.internal_match_id
                        )
                    ), 0)
                    OR e.ticket_count <> (
                        SELECT COUNT(*)
                        FROM tickets t
                        WHERE t.portfolio_id = p.portfolio_id
                        AND EXISTS (
                            SELECT 1 FROM ticket_legs l
                            WHERE l.ticket_id = t.ticket_id
                            AND l.internal_match_id = e.internal_match_id
                        )
                    )
                    OR (p.budget_fen > 0 AND (
                        e.budget_ratio IS NULL
                        OR ABS(e.budget_ratio -
                            CAST(e.exposed_stake_fen AS REAL) / p.budget_fen
                        ) > 0.000000000001
                    ))
                    OR (p.total_stake_fen > 0 AND (
                        e.deployed_ratio IS NULL
                        OR ABS(e.deployed_ratio -
                            CAST(e.exposed_stake_fen AS REAL) / p.total_stake_fen
                        ) > 0.000000000001
                    ))
                    OR CASE
                        WHEN json_valid(e.ticket_ids_json) = 0 THEN 1
                        WHEN json_type(e.ticket_ids_json) <> 'array' THEN 1
                        ELSE (
                            json_array_length(e.ticket_ids_json) <> e.ticket_count
                            OR (SELECT COUNT(DISTINCT value)
                                FROM json_each(e.ticket_ids_json)) <> e.ticket_count
                            OR EXISTS (
                                SELECT 1 FROM json_each(e.ticket_ids_json) j
                                WHERE NOT EXISTS (
                                    SELECT 1 FROM tickets t
                                    JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                                    WHERE t.ticket_id = j.value
                                    AND t.portfolio_id = p.portfolio_id
                                    AND l.internal_match_id = e.internal_match_id
                                )
                            )
                        )
                    END
                )
            )
            OR EXISTS (
                SELECT 1
                FROM portfolio_risk_reports x
                JOIN portfolios p ON p.portfolio_id = x.portfolio_id
                JOIN tickets t ON t.portfolio_id = p.portfolio_id
                JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                JOIN bet_candidates b ON b.candidate_id = l.candidate_id
                WHERE p.analysis_run_id = NEW.analysis_run_id
                GROUP BY x.risk_report_id, l.internal_match_id,
                         b.market_key, b.selection_key
                HAVING NOT EXISTS (
                    SELECT 1 FROM portfolio_selection_exposures e
                    WHERE e.risk_report_id = x.risk_report_id
                    AND e.internal_match_id = l.internal_match_id
                    AND e.market_key = b.market_key
                    AND e.selection_key = b.selection_key
                )
            )
            OR EXISTS (
                SELECT 1
                FROM portfolio_selection_exposures e
                JOIN portfolio_risk_reports x ON x.risk_report_id = e.risk_report_id
                JOIN portfolios p ON p.portfolio_id = x.portfolio_id
                WHERE p.analysis_run_id = NEW.analysis_run_id AND (
                    e.exposed_stake_fen <> COALESCE((
                        SELECT SUM(t.stake_fen)
                        FROM tickets t
                        JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                        JOIN bet_candidates b ON b.candidate_id = l.candidate_id
                        WHERE t.portfolio_id = p.portfolio_id
                        AND l.internal_match_id = e.internal_match_id
                        AND b.market_key = e.market_key
                        AND b.selection_key = e.selection_key
                    ), 0)
                    OR e.ticket_count <> (
                        SELECT COUNT(*)
                        FROM tickets t
                        JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                        JOIN bet_candidates b ON b.candidate_id = l.candidate_id
                        WHERE t.portfolio_id = p.portfolio_id
                        AND l.internal_match_id = e.internal_match_id
                        AND b.market_key = e.market_key
                        AND b.selection_key = e.selection_key
                    )
                    OR (p.budget_fen > 0 AND (
                        e.budget_ratio IS NULL
                        OR ABS(e.budget_ratio -
                            CAST(e.exposed_stake_fen AS REAL) / p.budget_fen
                        ) > 0.000000000001
                    ))
                    OR (p.total_stake_fen > 0 AND (
                        e.deployed_ratio IS NULL
                        OR ABS(e.deployed_ratio -
                            CAST(e.exposed_stake_fen AS REAL) / p.total_stake_fen
                        ) > 0.000000000001
                    ))
                    OR CASE
                        WHEN json_valid(e.ticket_ids_json) = 0 THEN 1
                        WHEN json_type(e.ticket_ids_json) <> 'array' THEN 1
                        ELSE (
                            json_array_length(e.ticket_ids_json) <> e.ticket_count
                            OR (SELECT COUNT(DISTINCT value)
                                FROM json_each(e.ticket_ids_json)) <> e.ticket_count
                            OR EXISTS (
                                SELECT 1 FROM json_each(e.ticket_ids_json) j
                                WHERE NOT EXISTS (
                                    SELECT 1 FROM tickets t
                                    JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                                    JOIN bet_candidates b ON b.candidate_id = l.candidate_id
                                    WHERE t.ticket_id = j.value
                                    AND t.portfolio_id = p.portfolio_id
                                    AND l.internal_match_id = e.internal_match_id
                                    AND b.market_key = e.market_key
                                    AND b.selection_key = e.selection_key
                                )
                            )
                        )
                    END
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'completed AnalysisRun requires valid exposures');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_completion_stress
        BEFORE UPDATE OF status ON analysis_runs
        WHEN NEW.status = 'COMPLETED' AND OLD.status <> 'COMPLETED' AND EXISTS (
            SELECT 1
            FROM portfolio_stress_results s
            JOIN portfolio_risk_reports x ON x.risk_report_id = s.risk_report_id
            JOIN portfolios p ON p.portfolio_id = x.portfolio_id
            WHERE p.analysis_run_id = NEW.analysis_run_id AND (
                s.portfolio_id <> p.portfolio_id
                OR (SELECT COUNT(*) FROM portfolio_stress_ticket_results r
                    WHERE r.scenario_id = s.scenario_id) <>
                   (SELECT COUNT(*) FROM tickets t
                    WHERE t.portfolio_id = p.portfolio_id)
                OR s.is_complete <> CASE WHEN EXISTS (
                    SELECT 1 FROM portfolio_stress_ticket_results r
                    WHERE r.scenario_id = s.scenario_id
                    AND r.result_state = 'ALIVE'
                ) THEN 0 ELSE 1 END
                OR s.scenario_exposed_stake_fen <> COALESCE((
                    SELECT SUM(t.stake_fen)
                    FROM portfolio_stress_ticket_results r
                    JOIN tickets t ON t.ticket_id = r.ticket_id
                    WHERE r.scenario_id = s.scenario_id
                    AND r.result_state = 'LOST'
                ), 0)
                OR (p.budget_fen = 0 AND s.scenario_exposure_ratio IS NOT NULL)
                OR (p.budget_fen > 0 AND (
                    s.scenario_exposure_ratio IS NULL
                    OR ABS(s.scenario_exposure_ratio -
                        CAST(s.scenario_exposed_stake_fen AS REAL) / p.budget_fen
                    ) > 0.000000000001
                ))
                OR s.minimum_ending_capital_fen <> p.unused_budget_fen + COALESCE((
                    SELECT SUM(r.gross_payout_fen)
                    FROM portfolio_stress_ticket_results r
                    WHERE r.scenario_id = s.scenario_id
                    AND r.result_state = 'WON'
                ), 0)
                OR s.maximum_ending_capital_fen <>
                   s.minimum_ending_capital_fen + COALESCE((
                    SELECT SUM(t.potential_gross_payout_fen)
                    FROM portfolio_stress_ticket_results r
                    JOIN tickets t ON t.ticket_id = r.ticket_id
                    WHERE r.scenario_id = s.scenario_id
                    AND r.result_state = 'ALIVE'
                ), 0)
                OR EXISTS (
                    SELECT 1
                    FROM portfolio_stress_ticket_results r
                    JOIN tickets t ON t.ticket_id = r.ticket_id
                    WHERE r.scenario_id = s.scenario_id AND (
                        t.portfolio_id <> p.portfolio_id
                        OR r.result_state NOT IN ('LOST', 'WON', 'ALIVE')
                        OR (r.result_state = 'WON' AND
                            r.gross_payout_fen <> t.potential_gross_payout_fen)
                        OR (r.result_state <> 'WON' AND
                            r.gross_payout_fen IS NOT NULL)
                    )
                )
                OR (s.is_complete = 1 AND (
                    s.gross_payout_fen <> COALESCE((
                        SELECT SUM(r.gross_payout_fen)
                        FROM portfolio_stress_ticket_results r
                        WHERE r.scenario_id = s.scenario_id
                        AND r.result_state = 'WON'
                    ), 0)
                    OR s.ending_capital_fen <> s.minimum_ending_capital_fen
                    OR s.profit_loss_fen <> s.ending_capital_fen - p.budget_fen
                    OR s.minimum_ending_capital_fen <>
                       s.maximum_ending_capital_fen
                    OR (p.budget_fen = 0 AND
                        s.capital_recovery_ratio IS NOT NULL)
                    OR (p.budget_fen > 0 AND (
                        s.capital_recovery_ratio IS NULL
                        OR ABS(s.capital_recovery_ratio -
                            CAST(s.ending_capital_fen AS REAL) / p.budget_fen
                        ) > 0.000000000001
                    ))
                ))
                OR (s.is_complete = 0 AND (
                    s.gross_payout_fen IS NOT NULL
                    OR s.ending_capital_fen IS NOT NULL
                    OR s.profit_loss_fen IS NOT NULL
                    OR s.capital_recovery_ratio IS NOT NULL
                ))
                OR CASE
                    WHEN json_valid(s.outcomes_json) = 0 THEN 1
                    WHEN json_type(s.outcomes_json) <> 'array' THEN 1
                    ELSE (
                        json_array_length(s.outcomes_json) <>
                        (SELECT COUNT(DISTINCT json_extract(value, '$.match_id'))
                         FROM json_each(s.outcomes_json))
                        OR EXISTS (
                            SELECT 1 FROM json_each(s.outcomes_json) o
                            WHERE json_type(o.value) IS NOT 'object'
                            OR json_type(o.value, '$.match_id') IS NOT 'text'
                            OR json_type(o.value, '$.selection') IS NOT 'text'
                            OR json_extract(o.value, '$.selection') NOT IN (
                                'HOME_WIN', 'DRAW', 'AWAY_WIN'
                            )
                            OR NOT EXISTS (
                                SELECT 1 FROM tickets t
                                JOIN ticket_legs l ON l.ticket_id = t.ticket_id
                                WHERE t.portfolio_id = p.portfolio_id
                                AND l.internal_match_id =
                                    json_extract(o.value, '$.match_id')
                            )
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM portfolio_stress_ticket_results r
                            JOIN tickets t ON t.ticket_id = r.ticket_id
                            WHERE r.scenario_id = s.scenario_id
                            AND r.result_state <> CASE
                                WHEN EXISTS (
                                    SELECT 1
                                    FROM ticket_legs l
                                    JOIN bet_candidates b
                                      ON b.candidate_id = l.candidate_id
                                    JOIN json_each(s.outcomes_json) o
                                      ON json_extract(o.value, '$.match_id') =
                                         l.internal_match_id
                                    WHERE l.ticket_id = t.ticket_id
                                    AND json_extract(o.value, '$.selection') <>
                                        b.selection_key
                                ) THEN 'LOST'
                                WHEN NOT EXISTS (
                                    SELECT 1
                                    FROM ticket_legs l
                                    WHERE l.ticket_id = t.ticket_id
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM json_each(s.outcomes_json) o
                                        WHERE json_extract(
                                            o.value, '$.match_id'
                                        ) = l.internal_match_id
                                    )
                                ) THEN 'WON'
                                ELSE 'ALIVE'
                            END
                        )
                    )
                END
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'completed AnalysisRun requires valid stress results');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_completion_stress_coverage
        BEFORE UPDATE OF status ON analysis_runs
        WHEN NEW.status = 'COMPLETED' AND EXISTS (
            SELECT 1
            FROM portfolios p
            JOIN portfolio_risk_reports x ON x.portfolio_id = p.portfolio_id
            WHERE p.analysis_run_id = NEW.analysis_run_id AND (
                ((SELECT COUNT(*) FROM tickets t
                  WHERE t.portfolio_id = p.portfolio_id) = 0 AND (
                    (SELECT COUNT(*) FROM portfolio_stress_results s
                     WHERE s.risk_report_id = x.risk_report_id) <> 1
                    OR NOT EXISTS (
                        SELECT 1 FROM portfolio_stress_results s
                        WHERE s.risk_report_id = x.risk_report_id
                        AND s.scenario_key = 'CASH_BASELINE'
                    )
                ))
                OR ((SELECT COUNT(*) FROM tickets t
                     WHERE t.portfolio_id = p.portfolio_id) > 0 AND (
                    (SELECT COUNT(*) FROM portfolio_stress_results s
                     WHERE s.risk_report_id = x.risk_report_id) <> 3
                    OR NOT EXISTS (
                        SELECT 1 FROM portfolio_stress_results s
                        WHERE s.risk_report_id = x.risk_report_id
                        AND s.scenario_key = 'TOP_EXPOSURE_MATCH_ADVERSE'
                    )
                    OR NOT EXISTS (
                        SELECT 1 FROM portfolio_stress_results s
                        WHERE s.risk_report_id = x.risk_report_id
                        AND s.scenario_key = 'TOP_TWO_EXPOSURE_MATCHES_ADVERSE'
                    )
                    OR NOT EXISTS (
                        SELECT 1 FROM portfolio_stress_results s
                        WHERE s.risk_report_id = x.risk_report_id
                        AND s.scenario_key = 'ALL_EXPOSED_MATCHES_ADVERSE'
                    )
                ))
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'completed AnalysisRun requires complete stress coverage');
        END
        """
    )
    for table_name, key_sets in IMMUTABLE_INSERT_KEYS.items():
        conflict_condition = " OR ".join(
            "(" + " AND ".join(
                f"existing.{column} = NEW.{column}" for column in key_set
            ) + ")"
            for key_set in key_sets
        )
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table_name}_immutable_insert_existing
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
    for table_name, parent_lookup in POST_RUN_PARENT_LOOKUPS.items():
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table_name}_completed_parent_insert
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
                CREATE TRIGGER IF NOT EXISTS trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'post-run artifacts are append-only');
                END
                """
            )
        conflict_lookup = POST_RUN_INSERT_CONFLICTS[table_name]
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table_name}_append_only_insert_existing
            BEFORE INSERT ON {table_name}
            WHEN EXISTS ({conflict_lookup})
            BEGIN
                SELECT RAISE(ABORT, 'post-run artifacts are append-only');
            END
            """
        )
    for table_name, invalid_condition in LINEAGE_GUARDS.items():
        for action in ("INSERT", "UPDATE"):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table_name}_lineage_{action.lower()}
                BEFORE {action} ON {table_name}
                WHEN {invalid_condition}
                BEGIN
                    SELECT RAISE(ABORT, 'artifact lineage is inconsistent');
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
