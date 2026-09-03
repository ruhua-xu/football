"""add quant model lineage

Revision ID: b7d4e9f2c631
Revises: a6c1f9e3b742
Create Date: 2026-09-03 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d4e9f2c631"
down_revision: Union[str, None] = "a6c1f9e3b742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


IMMUTABLE_INSERT_KEYS = {
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
    "analysis_run_matches": (("analysis_run_id", "internal_match_id"),),
    "quant_predictions": (
        ("quant_prediction_id",),
        ("analysis_run_id", "internal_match_id", "market_key"),
    ),
}

RUN_LOOKUPS = {
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
    "quant_predictions": (
        "SELECT 1 FROM analysis_runs r "
        "WHERE r.analysis_run_id = {row}.analysis_run_id AND r.status = 'COMPLETED'"
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
}

MODEL_TABLES = (
    "quant_model_states",
    "quant_model_training_facts",
    "quant_model_evaluations",
)


def upgrade() -> None:
    _require_sqlite_dialect()
    _create_model_tables()
    _drop_altered_table_triggers()
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        with op.batch_alter_table(
            "analysis_run_matches", recreate="always"
        ) as batch_op:
            batch_op.alter_column(
                "manual_quant_input_id",
                existing_type=sa.String(length=160),
                nullable=True,
            )
            batch_op.add_column(
                sa.Column(
                    "quant_model_evaluation_id",
                    sa.String(length=160),
                    nullable=True,
                )
            )
            batch_op.create_unique_constraint(
                "uq_analysis_run_match_model_evaluation",
                ["quant_model_evaluation_id"],
            )
            batch_op.create_foreign_key(
                "fk_analysis_run_match_model_evaluation",
                "quant_model_evaluations",
                [
                    "quant_model_evaluation_id",
                    "analysis_run_id",
                    "internal_match_id",
                ],
                [
                    "quant_model_evaluation_id",
                    "analysis_run_id",
                    "internal_match_id",
                ],
                ondelete="RESTRICT",
            )
            batch_op.create_check_constraint(
                "ck_analysis_run_match_quant_source",
                "(manual_quant_input_id IS NOT NULL AND "
                "quant_model_evaluation_id IS NULL) OR "
                "(manual_quant_input_id IS NULL AND "
                "quant_model_evaluation_id IS NOT NULL)",
            )
        with op.batch_alter_table(
            "quant_predictions", recreate="always"
        ) as batch_op:
            batch_op.alter_column(
                "manual_input_id",
                existing_type=sa.String(length=160),
                nullable=True,
            )
            batch_op.alter_column(
                "input_payload_hash",
                existing_type=sa.String(length=160),
                nullable=True,
            )
            batch_op.alter_column(
                "entered_at_utc",
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
            )
            batch_op.add_column(
                sa.Column(
                    "quant_model_evaluation_id",
                    sa.String(length=160),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "generated_at_utc",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
            batch_op.create_unique_constraint(
                "uq_quant_prediction_model_evaluation",
                ["quant_model_evaluation_id"],
            )
            batch_op.create_foreign_key(
                "fk_quant_prediction_model_evaluation",
                "quant_model_evaluations",
                [
                    "quant_model_evaluation_id",
                    "analysis_run_id",
                    "internal_match_id",
                ],
                [
                    "quant_model_evaluation_id",
                    "analysis_run_id",
                    "internal_match_id",
                ],
                ondelete="RESTRICT",
            )
            batch_op.create_check_constraint(
                "ck_quant_prediction_source",
                "(manual_input_id IS NOT NULL AND input_payload_hash IS NOT NULL "
                "AND quant_model_evaluation_id IS NULL "
                "AND entered_at_utc IS NOT NULL AND generated_at_utc IS NULL) OR "
                "(manual_input_id IS NULL AND input_payload_hash IS NULL "
                "AND quant_model_evaluation_id IS NOT NULL "
                "AND entered_at_utc IS NULL AND generated_at_utc IS NOT NULL)",
            )
    finally:
        connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
    _install_triggers()


def downgrade() -> None:
    _require_sqlite_dialect()
    _require_empty_model_lineage()
    _drop_new_triggers()
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        with op.batch_alter_table(
            "quant_predictions", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_quant_prediction_model_evaluation", type_="foreignkey"
            )
            batch_op.drop_constraint(
                "uq_quant_prediction_model_evaluation", type_="unique"
            )
            batch_op.drop_constraint(
                "ck_quant_prediction_source", type_="check"
            )
            batch_op.drop_column("generated_at_utc")
            batch_op.drop_column("quant_model_evaluation_id")
            batch_op.alter_column(
                "entered_at_utc",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )
            batch_op.alter_column(
                "input_payload_hash",
                existing_type=sa.String(length=160),
                nullable=False,
            )
            batch_op.alter_column(
                "manual_input_id",
                existing_type=sa.String(length=160),
                nullable=False,
            )
        with op.batch_alter_table(
            "analysis_run_matches", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_analysis_run_match_model_evaluation", type_="foreignkey"
            )
            batch_op.drop_constraint(
                "uq_analysis_run_match_model_evaluation", type_="unique"
            )
            batch_op.drop_constraint(
                "ck_analysis_run_match_quant_source", type_="check"
            )
            batch_op.drop_column("quant_model_evaluation_id")
            batch_op.alter_column(
                "manual_quant_input_id",
                existing_type=sa.String(length=160),
                nullable=False,
            )
    finally:
        connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
    op.drop_table("quant_model_evaluations")
    op.drop_table("quant_model_training_facts")
    op.drop_table("quant_model_states")
    _install_legacy_altered_table_triggers()


def _create_model_tables() -> None:
    hash_check = "length({name}) = 64 AND {name} NOT GLOB '*[^0-9a-f]*'"
    op.create_table(
        "quant_model_states",
        sa.Column("quant_model_state_id", sa.String(length=160), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("model_name", sa.String(length=80), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("calibration_label", sa.String(length=80), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("cutoff_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("season_id", sa.String(length=160), nullable=True),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("state_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("training_data_hash", sa.String(length=64), nullable=False),
        sa.Column("training_fact_count", sa.Integer(), nullable=False),
        sa.Column("generated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            hash_check.format(name="config_hash"),
            name="ck_quant_model_state_config_hash",
        ),
        sa.CheckConstraint(
            hash_check.format(name="state_hash"),
            name="ck_quant_model_state_hash",
        ),
        sa.CheckConstraint(
            hash_check.format(name="state_payload_hash"),
            name="ck_quant_model_state_payload_hash",
        ),
        sa.CheckConstraint(
            hash_check.format(name="training_data_hash"),
            name="ck_quant_model_training_data_hash",
        ),
        sa.CheckConstraint(
            "training_fact_count >= 0",
            name="ck_quant_model_training_fact_count",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("quant_model_state_id"),
        sa.UniqueConstraint(
            "quant_model_state_id",
            "analysis_run_id",
            name="uq_quant_model_state_run_lineage",
        ),
        sa.UniqueConstraint(
            "analysis_run_id",
            "model_name",
            "model_version",
            "state_hash",
            name="uq_quant_model_state_version",
        ),
    )
    op.create_table(
        "quant_model_training_facts",
        sa.Column("quant_model_state_id", sa.String(length=160), nullable=False),
        sa.Column("fact_sequence", sa.Integer(), nullable=False),
        sa.Column("match_result_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("source_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("fact_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "fact_sequence >= 0",
            name="ck_quant_model_training_fact_sequence",
        ),
        sa.CheckConstraint(
            hash_check.format(name="source_payload_hash"),
            name="ck_quant_model_training_source_hash",
        ),
        sa.CheckConstraint(
            hash_check.format(name="fact_hash"),
            name="ck_quant_model_training_fact_hash",
        ),
        sa.ForeignKeyConstraint(
            ["quant_model_state_id"],
            ["quant_model_states.quant_model_state_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_result_id"],
            ["match_results.match_result_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("quant_model_state_id", "fact_sequence"),
        sa.UniqueConstraint(
            "quant_model_state_id",
            "match_result_id",
            name="uq_quant_model_training_result",
        ),
    )
    op.create_table(
        "quant_model_evaluations",
        sa.Column("quant_model_evaluation_id", sa.String(length=160), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("quant_model_state_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("market_key", sa.String(length=120), nullable=False),
        sa.Column("market_type", sa.String(length=64), nullable=False),
        sa.Column("handicap_value", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("unavailable_reason", sa.String(length=160), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("model_prediction_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_quant_model_evaluation_status",
        ),
        sa.CheckConstraint(
            "(status = 'AVAILABLE' AND unavailable_reason IS NULL) OR "
            "(status = 'UNAVAILABLE' AND unavailable_reason IS NOT NULL "
            "AND length(trim(unavailable_reason)) > 0)",
            name="ck_quant_model_evaluation_availability",
        ),
        sa.CheckConstraint(
            hash_check.format(name="output_hash"),
            name="ck_quant_model_evaluation_output_hash",
        ),
        sa.CheckConstraint(
            hash_check.format(name="model_prediction_hash"),
            name="ck_quant_model_prediction_hash",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.analysis_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quant_model_state_id", "analysis_run_id"],
            [
                "quant_model_states.quant_model_state_id",
                "quant_model_states.analysis_run_id",
            ],
            name="fk_quant_model_evaluation_state_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("quant_model_evaluation_id"),
        sa.UniqueConstraint(
            "quant_model_evaluation_id",
            "analysis_run_id",
            "internal_match_id",
            name="uq_quant_model_evaluation_run_match",
        ),
        sa.UniqueConstraint(
            "analysis_run_id",
            "internal_match_id",
            "market_key",
            name="uq_quant_model_evaluation",
        ),
    )


def _install_triggers() -> None:
    _install_immutable_insert_triggers(IMMUTABLE_INSERT_KEYS)
    _install_run_sealing_triggers(RUN_LOOKUPS)
    for table_name in MODEL_TABLES:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'quant model artifacts are append-only');
                END
                """
            )
    for table_name, invalid_condition in LINEAGE_GUARDS.items():
        for action in ("INSERT", "UPDATE"):
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_lineage_{action.lower()}
                BEFORE {action} ON {table_name}
                WHEN {invalid_condition}
                BEGIN
                    SELECT RAISE(ABORT, 'artifact lineage is inconsistent');
                END
                """
            )
    op.execute(_completion_trigger_sql())


def _install_legacy_altered_table_triggers() -> None:
    keys = {
        "analysis_run_matches": (("analysis_run_id", "internal_match_id"),),
        "quant_predictions": (
            ("quant_prediction_id",),
            ("analysis_run_id", "internal_match_id", "market_key"),
        ),
    }
    lookups = {
        table_name: RUN_LOOKUPS[table_name]
        for table_name in ("analysis_run_matches", "quant_predictions")
    }
    _install_immutable_insert_triggers(keys)
    _install_run_sealing_triggers(lookups)


def _install_immutable_insert_triggers(key_map: dict) -> None:
    for table_name, key_sets in key_map.items():
        conflict_condition = " OR ".join(
            "("
            + " AND ".join(
                f"existing.{column} = NEW.{column}" for column in key_set
            )
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


def _install_run_sealing_triggers(lookups: dict) -> None:
    for table_name, lookup in lookups.items():
        for action in ("INSERT", "UPDATE", "DELETE"):
            rows = ("NEW",) if action == "INSERT" else ("OLD",)
            if action == "UPDATE":
                rows = ("OLD", "NEW")
            condition = " OR ".join(
                f"EXISTS ({lookup.format(row=row)})" for row in rows
            )
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_sealed_{action.lower()}
                BEFORE {action} ON {table_name}
                WHEN {condition}
                BEGIN
                    SELECT RAISE(ABORT, 'sealed AnalysisRun artifacts are immutable');
                END
                """
            )


def _completion_trigger_sql() -> str:
    return """
        CREATE TRIGGER trg_analysis_runs_completion_quant_model_graph
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


def _drop_altered_table_triggers() -> None:
    for table_name in ("analysis_run_matches", "quant_predictions"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_insert_existing")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_lineage_insert")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_lineage_update")
        for action in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_sealed_{action}")


def _drop_new_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_analysis_runs_completion_quant_model_graph")
    for table_name in LINEAGE_GUARDS:
        for action in ("insert", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_lineage_{action}")
    for table_name in MODEL_TABLES:
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{action}")
    for table_name in IMMUTABLE_INSERT_KEYS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_insert_existing")
    for table_name in RUN_LOOKUPS:
        for action in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_sealed_{action}")


def _require_empty_model_lineage() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline quant-model downgrade is unsupported because lineage must be checked"
        )
    connection = op.get_bind()
    for table_name in MODEL_TABLES:
        if connection.scalar(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})")
        ):
            raise RuntimeError(
                "cannot downgrade quant-model schema while model lineage exists"
            )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_runs "
            "WHERE input_manifest_version = 'MVP_INPUT_MANIFEST_V3')"
        )
    ):
        raise RuntimeError(
            "cannot downgrade quant-model schema while V3 AnalysisRuns exist"
        )


def _require_sqlite_dialect() -> None:
    dialect = op.get_context().dialect.name
    if dialect != "sqlite":
        raise RuntimeError(
            f"Unsupported database backend '{dialect}'; "
            "football-system v0.4.0 supports SQLite only."
        )
