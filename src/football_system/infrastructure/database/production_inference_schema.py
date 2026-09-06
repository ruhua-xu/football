"""Frozen SQLite companion schema for b1dae507c243; no V3/state changes."""

import sqlalchemy as sa


PRODUCTION_INFERENCE_TABLES = (
    "quant_model_state_production_releases",
    "analysis_run_target_acceptance_plans",
)


def production_inference_tables_v1(metadata, datetime_type):
    def col(name, type_=sa.String(160), **kwargs):
        return sa.Column(name, type_, nullable=False, **kwargs)

    def fk(names, table, targets=None):
        names = (names,) if isinstance(names, str) else names
        targets = names if targets is None else targets
        return sa.ForeignKeyConstraint(
            names, [f"{table}.{key}" for key in targets], ondelete="RESTRICT"
        )

    keys = ("analysis_run_id", "quant_model_state_id", "release_id", "plan_id")
    state = sa.Table(
        PRODUCTION_INFERENCE_TABLES[0],
        metadata,
        col("quant_model_state_id", primary_key=True),
        col("analysis_run_id", unique=True),
        col("release_id"),
        col("plan_id"),
        col("released_state_core_hash", sa.String(64)),
        col("training_cutoff_at_utc", datetime_type),
        col("training_data_hash", sa.String(64)),
        col("approved_facts_hash", sa.String(64)),
        col("binding_json", sa.Text()),
        col("binding_hash", sa.String(64)),
        sa.UniqueConstraint(*keys),
        fk(("quant_model_state_id", "analysis_run_id"), "quant_model_states"),
        fk("analysis_run_id", "analysis_runs"),
        fk("release_id", "production_quant_model_releases"),
        fk("plan_id", "production_target_acceptance_plans"),
        sa.CheckConstraint(
            "json_valid(binding_json) AND json_type(binding_json) = 'object'"
        ),
        *[
            sa.CheckConstraint(f"length({name}) = 64 AND {name} NOT GLOB '*[^0-9a-f]*'")
            for name in (
                "released_state_core_hash",
                "training_data_hash",
                "approved_facts_hash",
                "binding_hash",
            )
        ],
    )
    target = sa.Table(
        PRODUCTION_INFERENCE_TABLES[1],
        metadata,
        col("analysis_run_id", primary_key=True),
        col("quant_model_state_id"),
        col("release_id"),
        col("plan_id"),
        fk(keys, state.name),
        fk("analysis_run_id", "analysis_runs"),
        fk("quant_model_state_id", "quant_model_states"),
        fk("release_id", "production_quant_model_releases"),
        fk("plan_id", "production_target_acceptance_plans"),
    )
    return {item.name: item for item in (state, target)}


def production_inference_trigger_sql_v1():
    result = {}

    def trigger(table, suffix, action, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} "
            f"{('WHEN ' + condition) if condition else ''} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    for table in PRODUCTION_INFERENCE_TABLES:
        for action in ("UPDATE", "DELETE"):
            trigger(
                table,
                f"append_only_{action.lower()}",
                action,
                "",
                "production inference binding is append-only",
            )
        trigger(
            table,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {table} b WHERE b.analysis_run_id = NEW.analysis_run_id)",
            "immutable production inference binding already exists",
        )
        trigger(
            table,
            "lineage_insert",
            "INSERT",
            """NOT EXISTS (
                SELECT 1 FROM analysis_runs a
                JOIN quant_model_states s ON s.analysis_run_id = a.analysis_run_id
                JOIN production_target_acceptance_plans p ON p.plan_id = NEW.plan_id
                WHERE a.analysis_run_id = NEW.analysis_run_id AND a.status = 'RUNNING'
                AND s.quant_model_state_id = NEW.quant_model_state_id
                AND p.release_id = NEW.release_id
                AND json_extract(a.config_json, '$.request.model_training_use_class') = 'APPROVED_TRAINING_HISTORY'
                AND json_extract(a.config_json, '$.request.production_model_release_id') = NEW.release_id
                AND json_extract(a.config_json, '$.request.production_target_acceptance_plan_id') = NEW.plan_id
            )""",
            "production inference binding lineage mismatch",
        )
    trigger(
        PRODUCTION_INFERENCE_TABLES[0],
        "projection_insert",
        "INSERT",
        " OR ".join(
            [
                f"json_extract(NEW.binding_json, '$.{path}') IS NOT NEW.{column}"
                for path, column in (
                    ("analysis_run_id", "analysis_run_id"),
                    ("quant_model_state_id", "quant_model_state_id"),
                    ("release.artifact_id", "release_id"),
                    ("target_acceptance_plan.artifact_id", "plan_id"),
                    ("released_state_core_hash", "released_state_core_hash"),
                    ("training_data_hash", "training_data_hash"),
                    ("approved_facts_hash", "approved_facts_hash"),
                )
            ]
        ),
        "production inference binding JSON projection mismatch",
    )
    marker = "json_extract(NEW.config_json, '$.request.model_training_use_class')"
    pinned = f"""({marker} = 'APPROVED_TRAINING_HISTORY'
        OR json_extract(NEW.config_json, '$.request.production_model_release_id') IS NOT NULL
        OR json_extract(NEW.config_json, '$.request.production_target_acceptance_plan_id') IS NOT NULL)"""
    admitted = " OR ".join(
        f"""EXISTS (
            SELECT 1 FROM quant_model_states s
            JOIN quant_model_training_facts f ON f.quant_model_state_id = s.quant_model_state_id
            JOIN {table} admission ON admission.match_result_id = f.match_result_id
            WHERE s.analysis_run_id = NEW.analysis_run_id
        )"""
        for table in ("match_result_admissions", "training_fact_bindings")
    )
    exact = """EXISTS (
        SELECT 1 FROM quant_model_state_production_releases b
        JOIN analysis_run_target_acceptance_plans t
          ON t.analysis_run_id = b.analysis_run_id AND t.quant_model_state_id = b.quant_model_state_id
          AND t.release_id = b.release_id AND t.plan_id = b.plan_id
        JOIN quant_model_states s ON s.quant_model_state_id = b.quant_model_state_id
        JOIN production_quant_model_releases r ON r.release_id = b.release_id
        JOIN production_target_acceptance_plans p ON p.plan_id = b.plan_id AND p.release_id = r.release_id
        JOIN live_analysis_run_preparations l ON l.analysis_run_id = b.analysis_run_id
        WHERE b.analysis_run_id = NEW.analysis_run_id
        AND s.analysis_run_id = NEW.analysis_run_id
        AND NEW.input_manifest_version = 'MVP_INPUT_MANIFEST_V3'
        AND json_extract(NEW.config_json, '$.settings.runtime.environment') = 'live'
        AND json_extract(NEW.config_json, '$.request.model_training_use_class') = 'APPROVED_TRAINING_HISTORY'
        AND json_extract(NEW.config_json, '$.request.production_model_release_id') = r.release_id
        AND json_extract(NEW.config_json, '$.request.production_target_acceptance_plan_id') = p.plan_id
        AND json_extract(NEW.config_json, '$.request.live_source_preparation_id') = l.preparation_id
        AND NEW.as_of_at_utc = p.decision_as_of_at_utc AND s.cutoff_at_utc = NEW.as_of_at_utc
        AND r.persisted_at_utc < NEW.as_of_at_utc
        AND b.training_cutoff_at_utc = r.training_cutoff_at_utc
        AND b.released_state_core_hash = r.released_state_core_hash
        AND b.training_data_hash = s.training_data_hash
        AND json_extract(b.binding_json, '$.release.content_hash') = r.release_hash
        AND json_extract(b.binding_json, '$.target_acceptance_plan.content_hash') = p.plan_hash
        AND json_extract(r.artifact_json, '$.content_payload.released_state_core.content_payload.training_data_hash') = b.training_data_hash
        AND json_extract(r.artifact_json, '$.content_payload.released_state_core.content_payload.approved_facts_hash') = b.approved_facts_hash
        AND julianday(json_extract(b.binding_json, '$.start_authorization.actual_at_utc')) = julianday(NEW.started_at_utc)
        AND julianday(json_extract(b.binding_json, '$.completion_authorization.actual_at_utc')) = julianday(NEW.completed_at_utc)
        AND (SELECT COUNT(*) FROM quant_model_states q WHERE q.analysis_run_id = NEW.analysis_run_id) = 1
        AND s.training_fact_count = r.fact_count
        AND (SELECT COUNT(*) FROM quant_model_training_facts f WHERE f.quant_model_state_id = s.quant_model_state_id) = r.fact_count
        AND NOT EXISTS (
            SELECT 1 FROM quant_model_training_facts f WHERE f.quant_model_state_id = s.quant_model_state_id
            AND NOT EXISTS (SELECT 1 FROM production_quant_model_release_facts rf
                WHERE rf.release_id = r.release_id AND rf.fact_sequence = f.fact_sequence
                AND rf.match_result_id = f.match_result_id AND rf.elo_fact_hash = f.fact_hash)
        )
        AND (SELECT COUNT(*) FROM analysis_run_matches m WHERE m.analysis_run_id = NEW.analysis_run_id) = p.target_count
        AND NOT EXISTS (
            SELECT 1 FROM production_target_acceptance_matches pt WHERE pt.plan_id = p.plan_id
            AND (NEW.completed_at_utc >= pt.kickoff_at_utc OR NOT EXISTS (
                SELECT 1 FROM analysis_run_matches m
                JOIN quant_model_evaluations e ON e.quant_model_evaluation_id = m.quant_model_evaluation_id
                JOIN matches fixture ON fixture.internal_match_id = m.internal_match_id
                JOIN canonical_match_identities identity ON identity.internal_match_id = m.internal_match_id
                JOIN fixture_observations o ON o.observation_id = json_extract(m.context_json, '$.fixture_observation_id')
                WHERE m.analysis_run_id = NEW.analysis_run_id AND m.internal_match_id = pt.internal_match_id
                AND e.quant_model_state_id = s.quant_model_state_id AND e.status = 'AVAILABLE'
                AND o.internal_match_id = pt.internal_match_id AND o.kickoff_at_utc = pt.kickoff_at_utc
                AND fixture.competition_id = json_extract(p.artifact_json, '$.content_payload.competition_id')
                AND identity.season = json_extract(p.artifact_json, '$.content_payload.production_target_season_id')
                AND fixture.home_team_id = json_extract(p.artifact_json, '$.content_payload.targets[' || pt.target_sequence || '].home_team_id')
                AND fixture.away_team_id = json_extract(p.artifact_json, '$.content_payload.targets[' || pt.target_sequence || '].away_team_id')
            ))
        )
    )"""
    extra = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table} b WHERE b.analysis_run_id = NEW.analysis_run_id)"
        for table in PRODUCTION_INFERENCE_TABLES
    )
    prediction_counts = " OR ".join(
        f"(SELECT COUNT(*) FROM {table} p WHERE p.analysis_run_id = NEW.analysis_run_id) "
        "<> (SELECT COUNT(*) FROM analysis_run_matches m WHERE m.analysis_run_id = NEW.analysis_run_id)"
        for table in ("market_probabilities", "quant_predictions", "final_predictions")
    )
    outcomes = " AND ".join(
        f"(SELECT COUNT(*) FROM {table} o WHERE o.{key} = {alias}.{key}) = 3 "
        f"AND NOT EXISTS (SELECT 1 FROM {table} o WHERE o.{key} = {alias}.{key} "
        "AND o.selection_key NOT IN ('HOME_WIN', 'DRAW', 'AWAY_WIN'))"
        for table, key, alias in (
            ("market_probability_outcomes", "market_probability_id", "pm"),
            ("quant_prediction_outcomes", "quant_prediction_id", "q"),
            ("final_prediction_outcomes", "final_prediction_id", "pf"),
        )
    )
    # Structural coverage and probability links complement the repository's
    # exact Decimal replay. SQLite blend comparisons allow only rounding residue.
    prediction_graph = f"""NOT EXISTS (
        SELECT 1 FROM analysis_run_matches m WHERE m.analysis_run_id = NEW.analysis_run_id
        AND NOT EXISTS (
            SELECT 1 FROM quant_model_evaluations e
            JOIN quant_model_states s ON s.quant_model_state_id = e.quant_model_state_id
            JOIN market_probabilities pm ON pm.analysis_run_id = m.analysis_run_id
                AND pm.internal_match_id = m.internal_match_id AND pm.market_key = e.market_key
            JOIN quant_predictions q ON q.analysis_run_id = m.analysis_run_id
                AND q.internal_match_id = m.internal_match_id AND q.market_key = e.market_key
                AND q.quant_model_evaluation_id = e.quant_model_evaluation_id
            JOIN final_predictions pf ON pf.analysis_run_id = m.analysis_run_id
                AND pf.internal_match_id = m.internal_match_id AND pf.market_key = e.market_key
                AND pf.quant_prediction_id = q.quant_prediction_id
            WHERE e.quant_model_evaluation_id = m.quant_model_evaluation_id
            AND e.analysis_run_id = m.analysis_run_id AND e.internal_match_id = m.internal_match_id
            AND e.status = 'AVAILABLE' AND s.analysis_run_id = m.analysis_run_id
            AND pm.market_type = e.market_type AND pm.handicap_value IS e.handicap_value
            AND q.market_type = e.market_type AND q.handicap_value IS e.handicap_value
            AND pf.market_type = e.market_type AND pf.handicap_value IS e.handicap_value
            AND pm.devig_method = 'NORMALIZED_INVERSE_V1' AND pm.devig_version = '1'
            AND pm.generated_at_utc = NEW.started_at_utc AND pf.generated_at_utc = NEW.started_at_utc
            AND q.generated_at_utc = e.evaluated_at_utc AND q.method = s.model_name AND q.method_version = s.model_version
            AND (SELECT COUNT(*) FROM market_probability_inputs i WHERE i.market_probability_id = pm.market_probability_id) = 1
            AND EXISTS (SELECT 1 FROM market_probability_inputs i WHERE i.market_probability_id = pm.market_probability_id
                AND i.market_odds_snapshot_id = m.market_odds_snapshot_id)
            AND pf.fusion_policy = json_extract(NEW.config_json, '$.request.fusion_policy')
            AND pf.fusion_version = '1' AND pf.llm_assessment_id IS NULL
            AND pf.fallback_code IS NULL AND pf.confidence = 1
            AND ((pf.fusion_policy = 'QUANT_ONLY_V1' AND pf.market_probability_id IS NULL AND json(pf.fusion_config_json) = '{{}}')
                OR (pf.fusion_policy = 'MARKET_QUANT_BLEND_V1' AND pf.market_probability_id = pm.market_probability_id
                    AND CAST(json_extract(pf.fusion_config_json, '$.quant_weight') AS REAL) BETWEEN 0 AND 1
                    AND CAST(json_extract(pf.fusion_config_json, '$.quant_weight') AS REAL)
                        = CAST(json_extract(NEW.config_json, '$.request.quant_weight') AS REAL)))
            AND {outcomes}
            AND NOT EXISTS (SELECT 1 FROM quant_prediction_outcomes o WHERE o.quant_prediction_id = q.quant_prediction_id
                AND o.probability IS NOT CAST(json_extract(e.output_json, '$.probabilities.' || lower(o.selection_key)) AS REAL))
            AND NOT EXISTS (
                SELECT 1 FROM final_prediction_outcomes f
                JOIN quant_prediction_outcomes qo ON qo.quant_prediction_id = q.quant_prediction_id AND qo.selection_key = f.selection_key
                JOIN market_probability_outcomes mo ON mo.market_probability_id = pm.market_probability_id AND mo.selection_key = f.selection_key
                WHERE f.final_prediction_id = pf.final_prediction_id
                AND ((pf.fusion_policy = 'QUANT_ONLY_V1' AND f.probability IS NOT qo.probability)
                    OR (pf.fusion_policy = 'MARKET_QUANT_BLEND_V1' AND abs(f.probability - (
                        CAST(json_extract(pf.fusion_config_json, '$.quant_weight') AS REAL) * qo.probability
                        + (1 - CAST(json_extract(pf.fusion_config_json, '$.quant_weight') AS REAL)) * mo.probability
                    )) > 0.000000000002))
            )
        )
    )"""
    for action in ("INSERT", "UPDATE"):
        trigger(
            "analysis_runs",
            f"production_completion_{action.lower()}",
            action,
            f"NEW.status = 'COMPLETED' AND (({pinned} AND NOT {exact}) OR (({extra}) AND {marker} IS NOT 'APPROVED_TRAINING_HISTORY'))",
            "completed production analysis requires exact release and target bindings",
        )
        trigger(
            "analysis_runs",
            f"production_sources_completion_{action.lower()}",
            action,
            f"NEW.status = 'COMPLETED' AND ({admitted}) AND NOT {exact}",
            "admitted research training results require exact production bindings",
        )
        trigger(
            "analysis_runs",
            f"production_predictions_completion_{action.lower()}",
            action,
            f"NEW.status = 'COMPLETED' AND ({pinned} OR {admitted}) AND ({prediction_counts} OR NOT {prediction_graph})",
            "completed production analysis requires exact base prediction graph",
        )
    return result
