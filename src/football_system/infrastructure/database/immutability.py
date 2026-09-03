from __future__ import annotations

from sqlalchemy import Connection


RUN_LOOKUPS = {
    "backtest_v2_run_archives": (
        "SELECT 1 FROM backtest_v2_runs r "
        "WHERE r.backtest_run_id = {row}.backtest_run_id "
        "AND r.status = 'COMPLETED'"
    ),
    "backtest_v2_slices": (
        "SELECT 1 FROM backtest_v2_runs r "
        "WHERE r.backtest_run_id = {row}.backtest_run_id "
        "AND r.status = 'COMPLETED'"
    ),
    "backtest_v2_training_sources": (
        "SELECT 1 FROM backtest_v2_runs r JOIN backtest_v2_slices s "
        "ON s.backtest_run_id = r.backtest_run_id "
        "WHERE s.backtest_slice_id = {row}.backtest_slice_id "
        "AND r.status = 'COMPLETED'"
    ),
    "backtest_v2_evaluation_refs": (
        "SELECT 1 FROM backtest_v2_runs r JOIN backtest_v2_slices s "
        "ON s.backtest_run_id = r.backtest_run_id "
        "WHERE s.backtest_slice_id = {row}.backtest_slice_id "
        "AND r.status = 'COMPLETED'"
    ),
    "backtest_v2_result_sources": (
        "SELECT 1 FROM backtest_v2_runs r JOIN backtest_v2_slices s "
        "ON s.backtest_run_id = r.backtest_run_id "
        "WHERE s.backtest_slice_id = {row}.backtest_slice_id "
        "AND r.status = 'COMPLETED'"
    ),
    "backtest_v2_slice_ticket_settlements": (
        "SELECT 1 FROM backtest_v2_runs r JOIN backtest_v2_slices s "
        "ON s.backtest_run_id = r.backtest_run_id "
        "WHERE s.backtest_slice_id = {row}.backtest_slice_id "
        "AND r.status = 'COMPLETED'"
    ),
    "backtest_v2_metric_snapshots": (
        "SELECT 1 FROM backtest_v2_runs r "
        "WHERE r.backtest_run_id = {row}.backtest_run_id "
        "AND r.status = 'COMPLETED'"
    ),
    "quant_model_states": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
    "quant_model_training_facts": (
        "SELECT 1 FROM analysis_runs r JOIN quant_model_states s "
        "ON s.analysis_run_id = r.analysis_run_id "
        "WHERE s.quant_model_state_id = {row}.quant_model_state_id "
        "AND r.status = 'COMPLETED'"
    ),
    "quant_model_evaluations": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
    ),
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
    "provider_team_aliases",
    "provider_competition_mappings",
    "canonical_match_identities",
    "provider_match_mappings",
    "fixture_ingestion_captures",
    "fixture_observations",
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

FIXTURE_IDENTITY_ORIGIN_RULES = {
    "matches": "ingestion.available_at_utc = NEW.available_at_utc",
    "provider_team_aliases": (
        "ingestion.provider_id = NEW.provider_id "
        "AND ingestion.language = NEW.language "
        "AND ingestion.team_type = NEW.team_type "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
    "provider_competition_mappings": (
        "ingestion.provider_id = NEW.provider_id "
        "AND ingestion.provider_competition_id = NEW.provider_competition_id "
        "AND ingestion.season = NEW.season "
        "AND ingestion.competition_type = NEW.competition_type "
        "AND ingestion.language = NEW.language "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
    "canonical_match_identities": (
        "ingestion.season = NEW.season "
        "AND ingestion.competition_type = NEW.competition_type "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
    "provider_match_mappings": (
        "ingestion.provider_id = NEW.provider_id "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
}

IMMUTABLE_INSERT_KEYS = {
    "backtest_v2_runs": (
        ("backtest_run_id",),
        ("run_hash",),
    ),
    "backtest_v2_run_archives": (
        ("backtest_run_id", "archive_id"),
        ("backtest_run_id", "archive_no"),
    ),
    "backtest_v2_slices": (
        ("backtest_slice_id",),
        ("backtest_run_id", "slice_no"),
        ("slice_hash",),
    ),
    "backtest_v2_training_sources": (
        ("backtest_slice_id", "training_sequence"),
        ("backtest_slice_id", "match_result_id"),
    ),
    "backtest_v2_evaluation_refs": (
        ("backtest_slice_id", "decision_no"),
        ("backtest_slice_id", "internal_match_id"),
    ),
    "backtest_v2_result_sources": (
        ("backtest_slice_id", "result_no"),
        ("backtest_slice_id", "internal_match_id"),
    ),
    "backtest_v2_slice_ticket_settlements": (
        ("backtest_slice_id", "settlement_no"),
        ("backtest_slice_id", "settlement_id"),
    ),
    "backtest_v2_metric_snapshots": (
        ("metric_snapshot_id",),
        ("backtest_run_id",),
        ("snapshot_hash",),
    ),
    "historical_archive_imports": (
        ("archive_id",),
        ("provider_code", "dataset_kind", "payload_sha256"),
    ),
    "analysis_runs": (("analysis_run_id",),),
    "quant_model_states": (
        ("quant_model_state_id",),
        ("analysis_run_id", "model_name", "model_version", "state_hash"),
    ),
    "quant_model_training_facts": (
        ("quant_model_state_id", "fact_sequence"),
        ("quant_model_state_id", "match_result_id"),
    ),
    "quant_model_evaluations": (
        ("quant_model_evaluation_id",),
        ("analysis_run_id", "internal_match_id", "market_key"),
    ),
    "providers": (("provider_id",), ("code",)),
    "bookmakers": (("bookmaker_id",), ("code",)),
    "competitions": (("competition_id",), ("canonical_key",)),
    "teams": (("team_id",), ("canonical_key",)),
    "matches": (("internal_match_id",),),
    "provider_team_aliases": (
        ("alias_id",),
        (
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "internal_team_id",
        ),
    ),
    "provider_competition_mappings": (
        ("mapping_id",),
        (
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "internal_competition_id",
        ),
    ),
    "canonical_match_identities": (("internal_match_id",),),
    "provider_match_mappings": (
        ("mapping_id",),
        ("provider_id", "external_namespace", "external_match_id"),
    ),
    "fixture_ingestion_captures": (
        ("ingestion_id",),
        ("raw_artifact_id",),
    ),
    "fixture_observations": (
        ("observation_id",),
        ("ingestion_id", "internal_match_id"),
        ("ingestion_id", "provider_mapping_id"),
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
    "market_probability_outcomes": (("market_probability_id", "selection_key"),),
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

HISTORICAL_APPEND_ONLY_TABLES = (
    "historical_archive_imports",
    "match_results",
    "ticket_settlements",
    "ticket_settlement_match_results",
    "portfolio_settlements",
    "portfolio_settlement_tickets",
    "backtest_runs",
    "backtest_slices",
    "backtest_metric_snapshots",
    "backtest_metric_settlements",
    "backtest_metric_ticket_settlements",
    "backtest_v2_run_archives",
    "backtest_v2_slices",
    "backtest_v2_training_sources",
    "backtest_v2_evaluation_refs",
    "backtest_v2_result_sources",
    "backtest_v2_slice_ticket_settlements",
    "backtest_v2_metric_snapshots",
)

MODEL_APPEND_ONLY_TABLES = (
    "quant_model_states",
    "quant_model_training_facts",
    "quant_model_evaluations",
)

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
    "quant_model_states": (
        "NOT EXISTS (SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = NEW.analysis_run_id "
        "AND r.status = 'RUNNING' "
        "AND r.as_of_at_utc = NEW.cutoff_at_utc "
        "AND NEW.generated_at_utc >= r.as_of_at_utc)"
    ),
    "quant_model_training_facts": (
        "NOT EXISTS (SELECT 1 FROM quant_model_states s "
        "JOIN match_results result "
        "ON result.match_result_id = NEW.match_result_id "
        "WHERE s.quant_model_state_id = NEW.quant_model_state_id "
        "AND result.internal_match_id = NEW.internal_match_id "
        "AND result.payload_hash = NEW.source_payload_hash "
        "AND result.available_at_utc <= s.cutoff_at_utc "
        "AND result.ingested_at_utc <= s.cutoff_at_utc) "
        "OR EXISTS (SELECT 1 FROM quant_model_states s "
        "JOIN match_results successor "
        "ON successor.supersedes_match_result_id = NEW.match_result_id "
        "WHERE s.quant_model_state_id = NEW.quant_model_state_id "
        "AND successor.available_at_utc <= s.cutoff_at_utc "
        "AND successor.ingested_at_utc <= s.cutoff_at_utc)"
    ),
    "quant_model_evaluations": (
        "NOT EXISTS (SELECT 1 FROM quant_model_states s "
        "WHERE s.quant_model_state_id = NEW.quant_model_state_id "
        "AND s.analysis_run_id = NEW.analysis_run_id "
        "AND NEW.evaluated_at_utc >= s.cutoff_at_utc) "
        "OR EXISTS (SELECT 1 FROM quant_model_training_facts f "
        "WHERE f.quant_model_state_id = NEW.quant_model_state_id "
        "AND f.internal_match_id = NEW.internal_match_id) "
        "OR json_valid(NEW.output_json) = 0 "
        "OR json_type(NEW.output_json) <> 'object' "
        "OR json_extract(NEW.output_json, '$.match_id') <> NEW.internal_match_id "
        "OR json_extract(NEW.output_json, '$.status') <> NEW.status "
        "OR json_extract(NEW.output_json, '$.prediction_hash') "
        "<> NEW.model_prediction_hash"
    ),
    "analysis_run_matches": (
        "NOT EXISTS (SELECT 1 FROM market_odds_snapshots odds "
        "JOIN sporttery_bonus_snapshots bonus "
        "ON bonus.snapshot_id = NEW.sporttery_bonus_snapshot_id "
        "WHERE odds.snapshot_id = NEW.market_odds_snapshot_id "
        "AND odds.internal_match_id = NEW.internal_match_id "
        "AND bonus.internal_match_id = NEW.internal_match_id) "
        "OR (NEW.manual_quant_input_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM manual_quant_inputs input "
        "WHERE input.input_id = NEW.manual_quant_input_id "
        "AND input.internal_match_id = NEW.internal_match_id)) "
        "OR (NEW.quant_model_evaluation_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM quant_model_evaluations evaluation "
        "WHERE evaluation.quant_model_evaluation_id = "
        "NEW.quant_model_evaluation_id "
        "AND evaluation.analysis_run_id = NEW.analysis_run_id "
        "AND evaluation.internal_match_id = NEW.internal_match_id))"
    ),
    "quant_predictions": (
        "(NEW.manual_input_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM manual_quant_inputs input "
        "WHERE input.input_id = NEW.manual_input_id "
        "AND input.internal_match_id = NEW.internal_match_id "
        "AND input.market_key = NEW.market_key "
        "AND input.payload_hash = NEW.input_payload_hash)) "
        "OR (NEW.quant_model_evaluation_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM quant_model_evaluations evaluation "
        "JOIN quant_model_states state "
        "ON state.quant_model_state_id = evaluation.quant_model_state_id "
        "WHERE evaluation.quant_model_evaluation_id = "
        "NEW.quant_model_evaluation_id "
        "AND evaluation.analysis_run_id = NEW.analysis_run_id "
        "AND evaluation.internal_match_id = NEW.internal_match_id "
        "AND evaluation.market_key = NEW.market_key "
        "AND evaluation.status = 'AVAILABLE' "
        "AND state.model_name = NEW.method "
        "AND state.model_version = NEW.method_version))"
    ),
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
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_completion_quant_model_graph
        BEFORE UPDATE OF status ON analysis_runs
        WHEN NEW.status = 'COMPLETED' AND OLD.status <> 'COMPLETED' AND (
            EXISTS (
                SELECT 1 FROM quant_model_states state
                WHERE state.analysis_run_id = NEW.analysis_run_id AND (
                    state.cutoff_at_utc <> NEW.as_of_at_utc
                    OR json_valid(state.config_json) = 0
                    OR json_type(state.config_json) <> 'object'
                    OR json_valid(state.state_json) = 0
                    OR json_type(state.state_json) <> 'object'
                    OR json_extract(state.state_json, '$.state_hash')
                       <> state.state_hash
                    OR json_extract(state.state_json, '$.training_data_hash')
                       <> state.training_data_hash
                    OR json_extract(state.state_json, '$.config_hash')
                       <> state.config_hash
                    OR (SELECT COUNT(*) FROM quant_model_training_facts fact
                        WHERE fact.quant_model_state_id =
                              state.quant_model_state_id)
                       <> state.training_fact_count
                    OR (state.training_fact_count > 0 AND (
                        (SELECT MIN(fact.fact_sequence)
                         FROM quant_model_training_facts fact
                         WHERE fact.quant_model_state_id =
                               state.quant_model_state_id) <> 0
                        OR (SELECT MAX(fact.fact_sequence)
                            FROM quant_model_training_facts fact
                            WHERE fact.quant_model_state_id =
                                  state.quant_model_state_id)
                           <> state.training_fact_count - 1
                    ))
                    OR EXISTS (
                        SELECT 1
                        FROM quant_model_training_facts fact
                        JOIN match_results successor
                          ON successor.supersedes_match_result_id =
                             fact.match_result_id
                        WHERE fact.quant_model_state_id =
                              state.quant_model_state_id
                        AND successor.available_at_utc <= state.cutoff_at_utc
                        AND successor.ingested_at_utc <= state.cutoff_at_utc
                    )
                )
            )
            OR EXISTS (
                SELECT 1 FROM quant_model_evaluations evaluation
                WHERE evaluation.analysis_run_id = NEW.analysis_run_id AND (
                    (SELECT COUNT(*) FROM analysis_run_matches context
                     WHERE context.analysis_run_id = evaluation.analysis_run_id
                     AND context.internal_match_id = evaluation.internal_match_id
                     AND context.quant_model_evaluation_id =
                         evaluation.quant_model_evaluation_id) <> 1
                    OR (evaluation.status = 'AVAILABLE' AND
                        (SELECT COUNT(*) FROM quant_predictions prediction
                         WHERE prediction.quant_model_evaluation_id =
                               evaluation.quant_model_evaluation_id) <> 1)
                    OR (evaluation.status = 'UNAVAILABLE' AND
                        EXISTS (SELECT 1 FROM quant_predictions prediction
                                WHERE prediction.quant_model_evaluation_id =
                                      evaluation.quant_model_evaluation_id))
                )
            )
            OR (NEW.input_manifest_version = 'MVP_INPUT_MANIFEST_V3' AND (
                NOT EXISTS (SELECT 1 FROM quant_model_states state
                            WHERE state.analysis_run_id = NEW.analysis_run_id)
                OR EXISTS (SELECT 1 FROM analysis_run_matches context
                           WHERE context.analysis_run_id = NEW.analysis_run_id
                           AND context.quant_model_evaluation_id IS NULL)
                OR (SELECT COUNT(*) FROM quant_model_evaluations evaluation
                    WHERE evaluation.analysis_run_id = NEW.analysis_run_id)
                   <> (SELECT COUNT(*) FROM analysis_run_matches context
                       WHERE context.analysis_run_id = NEW.analysis_run_id)
            ))
            OR (NEW.input_manifest_version <> 'MVP_INPUT_MANIFEST_V3' AND EXISTS (
                SELECT 1 FROM quant_model_states state
                WHERE state.analysis_run_id = NEW.analysis_run_id
            ))
        )
        BEGIN
            SELECT RAISE(ABORT, 'completed AnalysisRun requires a valid quant model graph');
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
            "("
            + " AND ".join(f"existing.{column} = NEW.{column}" for column in key_set)
            + ")"
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
    for table_name in MODEL_APPEND_ONLY_TABLES:
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS
                  trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'quant model artifacts are append-only');
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
    _install_fixture_ingestion_lineage_trigger(connection)
    _install_historical_lineage_triggers(connection)
    install_backtest_v2_v1_triggers(connection)


def _install_fixture_ingestion_lineage_trigger(connection: Connection) -> None:
    for table_name, rule in FIXTURE_IDENTITY_ORIGIN_RULES.items():
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS
              trg_{table_name}_fixture_ingestion_origin_insert
            BEFORE INSERT ON {table_name}
            WHEN NEW.fixture_ingestion_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM fixture_ingestion_captures ingestion
                WHERE ingestion.ingestion_id = NEW.fixture_ingestion_id
                AND {rule}
            )
            BEGIN
                SELECT RAISE(ABORT, 'fixture identity origin is inconsistent');
            END
            """
        )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_fixture_observations_lineage_insert
        BEFORE INSERT ON fixture_observations
        WHEN NOT EXISTS (
            SELECT 1
            FROM fixture_ingestion_captures ingestion
            JOIN provider_match_mappings mapping
              ON mapping.mapping_id = NEW.provider_mapping_id
            WHERE ingestion.ingestion_id = NEW.ingestion_id
            AND mapping.provider_id = ingestion.provider_id
            AND mapping.internal_match_id = NEW.internal_match_id
            AND mapping.available_at_utc <= NEW.available_at_utc
            AND ingestion.available_at_utc = NEW.available_at_utc
        )
        BEGIN
            SELECT RAISE(ABORT, 'fixture observation lineage is inconsistent');
        END
        """
    )


def install_backtest_v2_v1_triggers(connection: Connection) -> None:
    """Install the frozen trigger set introduced with BACKTEST_V2 record V1."""
    table_names = (
        "backtest_v2_runs",
        "backtest_v2_run_archives",
        "backtest_v2_slices",
        "backtest_v2_training_sources",
        "backtest_v2_evaluation_refs",
        "backtest_v2_result_sources",
        "backtest_v2_slice_ticket_settlements",
        "backtest_v2_metric_snapshots",
    )
    for table_name in table_names:
        key_sets = IMMUTABLE_INSERT_KEYS[table_name]
        conflict_condition = " OR ".join(
            "("
            + " AND ".join(f"existing.{column} = NEW.{column}" for column in key_set)
            + ")"
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
    for table_name in table_names[1:]:
        lookup = RUN_LOOKUPS[table_name]
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
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS
                  trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'historical artifacts are append-only');
                END
                """
            )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_runs_insert_running
        BEFORE INSERT ON backtest_v2_runs
        WHEN NEW.status <> 'RUNNING'
          OR json_valid(NEW.run_json) = 0
          OR json_type(NEW.run_json) <> 'object'
          OR json_extract(NEW.run_json, '$.backtest_run_id') <> NEW.backtest_run_id
          OR json_extract(NEW.run_json, '$.backtest_version') <> 'BACKTEST_V2'
          OR json_extract(NEW.run_json, '$.status') <> 'COMPLETED'
          OR json_array_length(json_extract(NEW.run_json, '$.expected_slice_ids'))
             <> NEW.expected_slice_count
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 run must begin with frozen RUNNING data');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_runs_update
        BEFORE UPDATE ON backtest_v2_runs
        WHEN OLD.status <> 'RUNNING'
          OR NEW.status <> 'COMPLETED'
          OR NEW.backtest_run_id <> OLD.backtest_run_id
          OR NEW.schema_version <> OLD.schema_version
          OR NEW.backtest_version <> OLD.backtest_version
          OR NEW.data_mode <> OLD.data_mode
          OR NEW.date_from <> OLD.date_from
          OR NEW.date_to <> OLD.date_to
          OR NEW.strategy_version <> OLD.strategy_version
          OR NEW.strategy_config_json <> OLD.strategy_config_json
          OR NEW.strategy_config_hash <> OLD.strategy_config_hash
          OR NEW.code_revision <> OLD.code_revision
          OR NEW.created_at_utc <> OLD.created_at_utc
          OR NEW.expected_slice_count <> OLD.expected_slice_count
          OR NEW.run_json <> OLD.run_json
          OR NEW.run_hash <> OLD.run_hash
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 run is immutable');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_runs_delete
        BEFORE DELETE ON backtest_v2_runs
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 run is append-only');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_run_archives_lineage_insert
        BEFORE INSERT ON backtest_v2_run_archives
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_v2_runs run
            JOIN historical_archive_imports archive
              ON archive.archive_id = NEW.archive_id
            WHERE run.backtest_run_id = NEW.backtest_run_id
              AND run.status = 'RUNNING'
              AND archive.data_mode = run.data_mode
              AND archive.payload_sha256 = NEW.archive_payload_sha256
              AND json_extract(
                    run.run_json,
                    '$.archive_provenance[' || (NEW.archive_no - 1) || '].archive_id'
                  ) = NEW.archive_id
              AND json_extract(
                    run.run_json,
                    '$.archive_provenance[' || (NEW.archive_no - 1) ||
                    '].archive_schema_version'
                  ) = archive.archive_schema_version
              AND json_extract(
                    run.run_json,
                    '$.archive_provenance[' || (NEW.archive_no - 1) ||
                    '].provider_code'
                  ) = archive.provider_code
              AND json_extract(
                    run.run_json,
                    '$.archive_provenance[' || (NEW.archive_no - 1) ||
                    '].dataset_kind'
                  ) = archive.dataset_kind
              AND json_extract(
                    run.run_json,
                    '$.archive_provenance[' || (NEW.archive_no - 1) ||
                    '].payload_sha256'
                  ) = archive.payload_sha256
        )
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 archive lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_slices_lineage_insert
        BEFORE INSERT ON backtest_v2_slices
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_v2_runs run
            JOIN analysis_runs analysis
              ON analysis.analysis_run_id = NEW.analysis_run_id
            JOIN quant_model_states state
              ON state.quant_model_state_id = NEW.quant_model_state_id
             AND state.analysis_run_id = analysis.analysis_run_id
            WHERE run.backtest_run_id = NEW.backtest_run_id
              AND run.status = 'RUNNING'
              AND run.data_mode = NEW.data_mode
              AND analysis.status = 'COMPLETED'
              AND analysis.input_manifest_version = 'MVP_INPUT_MANIFEST_V3'
              AND analysis.as_of_at_utc = NEW.decision_as_of_at_utc
              AND state.cutoff_at_utc = NEW.decision_as_of_at_utc
              AND EXISTS (
                  SELECT 1 FROM portfolios portfolio
                  WHERE portfolio.portfolio_id = NEW.portfolio_id
                    AND portfolio.analysis_run_id = NEW.analysis_run_id
              )
              AND json_extract(
                    run.run_json,
                    '$.expected_slice_ids[' || (NEW.slice_no - 1) || ']'
                  ) = NEW.backtest_slice_id
        )
        OR json_valid(NEW.decision_snapshot_json) = 0
        OR json_type(NEW.decision_snapshot_json) <> 'object'
        OR json_extract(NEW.decision_snapshot_json, '$.snapshot_hash')
           <> NEW.decision_snapshot_hash
        OR json_extract(NEW.decision_snapshot_json, '$.slice_id')
           <> NEW.backtest_slice_id
        OR json_extract(NEW.decision_snapshot_json, '$.analysis_run_id')
           <> NEW.analysis_run_id
        OR json_extract(NEW.decision_snapshot_json, '$.quant_model_state_id')
           <> NEW.quant_model_state_id
        OR json_valid(NEW.slice_json) = 0
        OR json_type(NEW.slice_json) <> 'object'
        OR json_extract(NEW.slice_json, '$.slice_hash') <> NEW.slice_hash
        OR json_extract(NEW.slice_json, '$.slice_id') <> NEW.backtest_slice_id
        OR json_array_length(
               json_extract(NEW.decision_snapshot_json, '$.expected_match_ids')
           ) <> NEW.planned_target_count
        OR json_array_length(
               json_extract(NEW.decision_snapshot_json, '$.analyzed_match_ids')
           ) <> NEW.decision_target_count
        OR json_array_length(
               json_extract(NEW.slice_json, '$.match_snapshots')
           ) <> NEW.result_target_count
        OR json_valid(NEW.settlement_result_json) = 0
        OR json_type(NEW.settlement_result_json) <> 'object'
        OR json_extract(NEW.settlement_result_json, '$.portfolio_id')
           <> NEW.portfolio_id
        OR (
            json_extract(
                NEW.settlement_result_json,
                '$.portfolio_settlement.portfolio_settlement_id'
            ) IS NOT NEW.portfolio_settlement_id
        )
        OR (
            NEW.portfolio_settlement_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM portfolio_settlements settlement
                WHERE settlement.portfolio_settlement_id =
                      NEW.portfolio_settlement_id
                  AND settlement.parent_analysis_run_id = NEW.analysis_run_id
                  AND settlement.portfolio_id = NEW.portfolio_id
                  AND settlement.settled_at_utc = NEW.evaluation_as_of_at_utc
            )
        )
        OR json_valid(NEW.slate_snapshot_json) = 0
        OR json_type(NEW.slate_snapshot_json) <> 'object'
        OR json_extract(NEW.slate_snapshot_json, '$.backtest_run_id')
           <> NEW.backtest_run_id
        OR json_extract(NEW.slate_snapshot_json, '$.slice_id')
           <> NEW.backtest_slice_id
        OR json_extract(NEW.slate_snapshot_json, '$.match_count')
           <> NEW.planned_target_count
        OR json_extract(NEW.slate_snapshot_json, '$.settled_match_count')
           <> NEW.result_target_count
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 slice lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_training_sources_lineage_insert
        BEFORE INSERT ON backtest_v2_training_sources
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_v2_slices slice
            JOIN quant_model_training_facts fact
              ON fact.quant_model_state_id = slice.quant_model_state_id
             AND fact.fact_sequence = NEW.training_sequence
             AND fact.match_result_id = NEW.match_result_id
             AND fact.source_payload_hash = NEW.source_payload_hash
             AND fact.fact_hash = NEW.fact_hash
            JOIN match_results result
              ON result.match_result_id = fact.match_result_id
            JOIN historical_archive_imports archive
              ON archive.archive_id = NEW.archive_id
             AND archive.payload_sha256 = NEW.archive_payload_sha256
            JOIN backtest_v2_run_archives run_archive
              ON run_archive.backtest_run_id = slice.backtest_run_id
             AND run_archive.archive_id = archive.archive_id
             AND run_archive.archive_payload_sha256 = archive.payload_sha256
            WHERE slice.backtest_slice_id = NEW.backtest_slice_id
              AND result.payload_hash = NEW.source_payload_hash
              AND result.available_at_utc <= slice.decision_as_of_at_utc
              AND result.ingested_at_utc <= slice.decision_as_of_at_utc
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.training_sources[' || NEW.training_sequence ||
                    '].match_result_id'
                  ) = NEW.match_result_id
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.training_sources[' || NEW.training_sequence ||
                    '].source_payload_hash'
                  ) = NEW.source_payload_hash
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.training_sources[' || NEW.training_sequence || '].fact_hash'
                  ) = NEW.fact_hash
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.training_sources[' || NEW.training_sequence || '].archive_id'
                  ) = NEW.archive_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 training source lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_evaluation_refs_lineage_insert
        BEFORE INSERT ON backtest_v2_evaluation_refs
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_v2_slices slice
            JOIN quant_model_evaluations evaluation
              ON evaluation.quant_model_evaluation_id =
                 NEW.quant_model_evaluation_id
             AND evaluation.analysis_run_id = slice.analysis_run_id
             AND evaluation.quant_model_state_id = slice.quant_model_state_id
             AND evaluation.internal_match_id = NEW.internal_match_id
             AND evaluation.status = NEW.status
              AND evaluation.output_hash = NEW.output_hash
              AND evaluation.model_prediction_hash = NEW.model_prediction_hash
             JOIN market_probabilities market
               ON market.market_probability_id = NEW.market_prediction_id
              AND market.analysis_run_id = slice.analysis_run_id
              AND market.internal_match_id = NEW.internal_match_id
            WHERE slice.backtest_slice_id = NEW.backtest_slice_id
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) || '].match_id'
                  ) = NEW.internal_match_id
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) ||
                    '].quant_model_evaluation_id'
                  ) = NEW.quant_model_evaluation_id
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) || '].status'
                  ) = NEW.status
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) || '].output_hash'
                  ) = NEW.output_hash
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) ||
                    '].model_prediction_hash'
                  ) = NEW.model_prediction_hash
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) ||
                    '].market_prediction_id'
                  ) = NEW.market_prediction_id
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) ||
                    '].quant_prediction_id'
                  ) IS NEW.quant_prediction_id
              AND json_extract(
                    slice.decision_snapshot_json,
                    '$.evaluations[' || (NEW.decision_no - 1) ||
                    '].final_prediction_id'
                  ) IS NEW.final_prediction_id
        )
        OR (NEW.status = 'AVAILABLE' AND NOT EXISTS (
            SELECT 1
            FROM quant_predictions quant
            JOIN final_predictions final
              ON final.final_prediction_id = NEW.final_prediction_id
             AND final.analysis_run_id = quant.analysis_run_id
             AND final.internal_match_id = quant.internal_match_id
             AND final.quant_prediction_id = quant.quant_prediction_id
            WHERE quant.quant_prediction_id = NEW.quant_prediction_id
              AND quant.quant_model_evaluation_id =
                  NEW.quant_model_evaluation_id
              AND quant.internal_match_id = NEW.internal_match_id
        ))
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 evaluation lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_result_sources_lineage_insert
        BEFORE INSERT ON backtest_v2_result_sources
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_v2_slices slice
            JOIN match_results result
              ON result.match_result_id = NEW.match_result_id
             AND result.internal_match_id = NEW.internal_match_id
             AND result.payload_hash = NEW.source_payload_hash
            JOIN historical_archive_imports archive
              ON archive.archive_id = NEW.archive_id
             AND archive.payload_sha256 = NEW.archive_payload_sha256
            JOIN backtest_v2_run_archives run_archive
              ON run_archive.backtest_run_id = slice.backtest_run_id
             AND run_archive.archive_id = archive.archive_id
             AND run_archive.archive_payload_sha256 = archive.payload_sha256
            WHERE slice.backtest_slice_id = NEW.backtest_slice_id
              AND result.available_at_utc <= slice.evaluation_as_of_at_utc
              AND result.ingested_at_utc <= slice.evaluation_as_of_at_utc
              AND json_extract(
                    slice.slice_json,
                    '$.match_snapshots[' || (NEW.result_no - 1) || '].match_id'
                  ) = NEW.internal_match_id
              AND json_extract(
                    slice.slice_json,
                    '$.match_snapshots[' || (NEW.result_no - 1) ||
                    '].match_result_id'
                  ) = NEW.match_result_id
              AND json_extract(
                    slice.slice_json,
                    '$.match_snapshots[' || (NEW.result_no - 1) ||
                    '].match_result_payload_hash'
                  ) = NEW.source_payload_hash
              AND json_extract(
                    slice.slice_json,
                    '$.match_snapshots[' || (NEW.result_no - 1) ||
                    '].match_result_archive_id'
                  ) = NEW.archive_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 result source lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS
          trg_backtest_v2_slice_ticket_settlements_lineage_insert
        BEFORE INSERT ON backtest_v2_slice_ticket_settlements
        WHEN NOT EXISTS (
            SELECT 1
            FROM backtest_v2_slices slice
            JOIN ticket_settlements settlement
              ON settlement.settlement_id = NEW.settlement_id
             AND settlement.parent_analysis_run_id = slice.analysis_run_id
             AND settlement.portfolio_id = slice.portfolio_id
             AND settlement.settled_at_utc = slice.evaluation_as_of_at_utc
            JOIN json_each(
                slice.settlement_result_json,
                '$.ticket_results'
            ) ticket_result
              ON json_extract(
                    ticket_result.value,
                    '$.settlement.settlement_id'
                 ) = NEW.settlement_id
            WHERE slice.backtest_slice_id = NEW.backtest_slice_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 ticket settlement lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_metrics_lineage_insert
        BEFORE INSERT ON backtest_v2_metric_snapshots
        WHEN NOT EXISTS (
            SELECT 1 FROM backtest_v2_runs run
            WHERE run.backtest_run_id = NEW.backtest_run_id
              AND run.status = 'RUNNING'
        )
        OR json_valid(NEW.metrics_json) = 0
        OR json_type(NEW.metrics_json) <> 'object'
        OR json_extract(NEW.metrics_json, '$.backtest_run_id')
           <> NEW.backtest_run_id
        OR json_extract(NEW.metrics_json, '$.metrics_version')
           <> NEW.metric_version
        OR json_valid(NEW.lineage_json) = 0
        OR json_type(NEW.lineage_json) <> 'object'
        OR json_array_length(
               json_extract(NEW.lineage_json, '$.backtest_slice_ids')
           ) <> (
               SELECT COUNT(*) FROM backtest_v2_slices slice
               WHERE slice.backtest_run_id = NEW.backtest_run_id
           )
        OR EXISTS (
            SELECT 1 FROM backtest_v2_slices slice
            WHERE slice.backtest_run_id = NEW.backtest_run_id
              AND NOT EXISTS (
                  SELECT 1 FROM json_each(
                      NEW.lineage_json,
                      '$.backtest_slice_ids'
                  ) lineage
                  WHERE lineage.value = slice.backtest_slice_id
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 metric lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_v2_runs_completion
        BEFORE UPDATE OF status ON backtest_v2_runs
        WHEN NEW.status = 'COMPLETED' AND (
            (SELECT COUNT(*) FROM backtest_v2_slices slice
             WHERE slice.backtest_run_id = NEW.backtest_run_id)
            <> NEW.expected_slice_count
            OR (SELECT COUNT(*) FROM backtest_v2_run_archives archive
                WHERE archive.backtest_run_id = NEW.backtest_run_id)
               <> json_array_length(
                      json_extract(NEW.run_json, '$.archive_provenance')
                  )
            OR EXISTS (
                SELECT 1
                FROM json_each(NEW.run_json, '$.expected_slice_ids') expected
                WHERE NOT EXISTS (
                    SELECT 1 FROM backtest_v2_slices slice
                    WHERE slice.backtest_run_id = NEW.backtest_run_id
                      AND slice.backtest_slice_id = expected.value
                )
            )
            OR (SELECT COUNT(*) FROM backtest_v2_metric_snapshots metric
                WHERE metric.backtest_run_id = NEW.backtest_run_id) <> 1
            OR EXISTS (
                SELECT 1
                FROM backtest_v2_slices slice
                WHERE slice.backtest_run_id = NEW.backtest_run_id AND (
                    (SELECT COUNT(*) FROM backtest_v2_training_sources source
                     WHERE source.backtest_slice_id = slice.backtest_slice_id)
                    <> (SELECT COUNT(*) FROM quant_model_training_facts fact
                        WHERE fact.quant_model_state_id =
                              slice.quant_model_state_id)
                    OR (SELECT COUNT(*) FROM backtest_v2_evaluation_refs ref
                        WHERE ref.backtest_slice_id = slice.backtest_slice_id)
                       <> slice.decision_target_count
                    OR (SELECT COUNT(*) FROM backtest_v2_evaluation_refs ref
                        WHERE ref.backtest_slice_id = slice.backtest_slice_id
                          AND ref.status = 'AVAILABLE')
                       <> slice.quant_available_count
                    OR (SELECT COUNT(*) FROM backtest_v2_evaluation_refs ref
                        WHERE ref.backtest_slice_id = slice.backtest_slice_id
                          AND ref.status = 'UNAVAILABLE')
                       <> slice.quant_unavailable_count
                    OR (SELECT COUNT(*) FROM backtest_v2_result_sources source
                        WHERE source.backtest_slice_id = slice.backtest_slice_id)
                       <> slice.result_target_count
                    OR (SELECT COUNT(*)
                        FROM backtest_v2_slice_ticket_settlements source
                        WHERE source.backtest_slice_id = slice.backtest_slice_id)
                       <> (SELECT COUNT(*)
                           FROM json_each(
                               slice.settlement_result_json,
                               '$.ticket_results'
                           ) ticket_result
                           WHERE json_type(
                               ticket_result.value,
                               '$.settlement'
                           ) = 'object')
                    OR (SELECT COUNT(*)
                        FROM backtest_v2_slice_ticket_settlements source
                        WHERE source.backtest_slice_id = slice.backtest_slice_id)
                       <> json_extract(
                              slice.slate_snapshot_json,
                              '$.settled_ticket_count'
                          )
                    OR EXISTS (
                        SELECT 1
                        FROM backtest_v2_training_sources source
                        JOIN quant_model_training_facts fact
                          ON fact.quant_model_state_id = slice.quant_model_state_id
                         AND fact.fact_sequence = source.training_sequence
                        JOIN json_each(
                            slice.decision_snapshot_json,
                            '$.expected_match_ids'
                        ) target
                          ON target.value = fact.internal_match_id
                        WHERE source.backtest_slice_id = slice.backtest_slice_id
                    )
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'BACKTEST_V2 completion graph is incomplete');
        END
        """
    )


def _install_historical_lineage_triggers(connection: Connection) -> None:
    for table_name in HISTORICAL_APPEND_ONLY_TABLES:
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'historical artifacts are append-only');
                END
                """
            )

    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_match_results_provider_mapping_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_match_results_supersession_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_settlements_completed_parent_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_settlements_base_lineage_insert
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
            AND (
                NEW.status = 'LOST'
                OR NEW.gross_payout_fen = t.potential_gross_payout_fen
            )
            AND r.status = 'COMPLETED'
            AND r.completed_at_utc IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'base ticket settlement lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_settlements_revision_lineage_insert
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
            AND (
                NEW.status = 'LOST'
                OR CAST(json_extract(
                    ticket.value, '$.potential_gross_payout_fen'
                ) AS INTEGER) = NEW.gross_payout_fen
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'revision ticket settlement lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_settlements_correction_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ticket_settlement_results_lineage_insert
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
                ))
                OR
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_portfolio_settlements_completed_parent_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_portfolio_settlements_base_lineage_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_portfolio_settlements_revision_lineage_insert
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
            AND CAST(json_extract(
                portfolio.value, '$.total_stake_fen'
            ) AS INTEGER) = NEW.total_stake_fen
            AND CAST(json_extract(
                portfolio.value, '$.unused_budget_fen'
            ) AS INTEGER) = NEW.cash_fen
        )
        BEGIN
            SELECT RAISE(ABORT, 'revision portfolio settlement lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_portfolio_settlements_correction_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_portfolio_settlement_tickets_lineage_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_runs_replay_insert
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_slices_lineage_insert
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
                 AND NEW.portfolio_revision_id IS NULL)
                OR
                (NEW.scope_kind = 'PORTFOLIO_REVISION' AND EXISTS (
                    SELECT 1 FROM portfolio_revisions revision
                    WHERE revision.portfolio_revision_id =
                        NEW.portfolio_revision_id
                    AND revision.portfolio_revision_id = NEW.decision_scope_id
                    AND revision.parent_analysis_run_id =
                        analysis.analysis_run_id
                ))
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest slice lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_metrics_lineage_insert
        BEFORE INSERT ON backtest_metric_snapshots
        WHEN NOT EXISTS (
            SELECT 1 FROM backtest_runs run
            WHERE run.backtest_run_id = NEW.backtest_run_id
            AND (
                (NEW.metric_scope = 'RUN' AND NEW.backtest_slice_id IS NULL)
                OR
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
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_metric_settlements_lineage_insert
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
                AND settlement.settled_at_utc <=
                    slice.evaluation_as_of_at_utc
                AND (
                    metric.backtest_slice_id IS NULL
                    OR metric.backtest_slice_id = slice.backtest_slice_id
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'backtest metric settlement lineage is inconsistent');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_backtest_metric_ticket_settlements_lineage_insert
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
        """
    )
