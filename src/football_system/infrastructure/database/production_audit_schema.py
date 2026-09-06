"""Frozen SQLite companion schema for c2ebf618d354, without V3 wire changes."""

import sqlalchemy as sa


PRODUCTION_AUDIT_TABLES = (
    "production_audit_bundles",
    "production_audit_packet_requirements",
)


def production_audit_tables_v1(metadata, datetime_type):
    bundle = sa.Table(
        "production_audit_bundles",
        metadata,
        sa.Column("packet_id", sa.String(160), primary_key=True),
        sa.Column("analysis_run_id", sa.String(160), nullable=False, unique=True),
        sa.Column("quant_model_state_id", sa.String(160), nullable=False),
        sa.Column("release_id", sa.String(160), nullable=False),
        sa.Column("plan_id", sa.String(160), nullable=False),
        sa.Column("audit_id", sa.String(160), nullable=False, unique=True),
        sa.Column("audit_hash", sa.String(64), nullable=False),
        sa.Column("packet_hash", sa.String(64), nullable=False),
        sa.Column("audit_json", sa.Text(), nullable=False),
        sa.Column("generated_at_utc", datetime_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["packet_id"], ["analysis_packets.packet_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id", "quant_model_state_id", "release_id", "plan_id"],
            [
                f"quant_model_state_production_releases.{key}"
                for key in (
                    "analysis_run_id",
                    "quant_model_state_id",
                    "release_id",
                    "plan_id",
                )
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "json_valid(audit_json) AND json_type(audit_json) = 'object' "
            "AND length(CAST(audit_json AS BLOB)) <= 2097152"
        ),
        *[
            sa.CheckConstraint(f"length({key}) = 64 AND {key} NOT GLOB '*[^0-9a-f]*'")
            for key in ("audit_hash", "packet_hash")
        ],
    )
    # AFTER INSERT enqueues this deferred obligation. Packet first, sidecar second,
    # but neither can commit on its own, including through direct SQL.
    required = sa.Table(
        "production_audit_packet_requirements",
        metadata,
        sa.Column("packet_id", sa.String(160), primary_key=True),
        sa.ForeignKeyConstraint(
            ["packet_id"], ["analysis_packets.packet_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["packet_id"],
            ["production_audit_bundles.packet_id"],
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    return {table.name: table for table in (bundle, required)}


def admitted_training_sql_v1(run_id):
    """Durable risk detection, not proof of approval or current authorization."""
    return f"""EXISTS (
        SELECT 1 FROM quant_model_states s
        JOIN quant_model_training_facts f ON f.quant_model_state_id = s.quant_model_state_id
        JOIN match_result_admissions a ON a.match_result_id = f.match_result_id
        WHERE s.analysis_run_id = {run_id}
    )"""


def approved_run_sql_v1(run_id):
    return f"""EXISTS (SELECT 1 FROM analysis_runs a
        WHERE a.analysis_run_id = {run_id} AND (
            json_extract(a.config_json, '$.request.model_training_use_class') = 'APPROVED_TRAINING_HISTORY'
            OR json_extract(a.config_json, '$.request.production_model_release_id') IS NOT NULL
            OR json_extract(a.config_json, '$.request.production_target_acceptance_plan_id') IS NOT NULL
        )) OR EXISTS (SELECT 1 FROM quant_model_state_production_releases b
            WHERE b.analysis_run_id = {run_id})
        OR EXISTS (SELECT 1 FROM analysis_run_target_acceptance_plans b
            WHERE b.analysis_run_id = {run_id})
        OR {admitted_training_sql_v1(run_id)}"""


def production_audit_trigger_sql_v1():
    result = {}

    def reject(table, suffix, action, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} "
            f"{('WHEN ' + condition) if condition else ''} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    for table in PRODUCTION_AUDIT_TABLES:
        for action in ("UPDATE", "DELETE"):
            reject(
                table,
                f"append_only_{action.lower()}",
                action,
                "",
                "production audit is append-only",
            )
        reject(
            table,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {table} WHERE packet_id = NEW.packet_id)",
            "immutable production audit already exists",
        )

    pinned = approved_run_sql_v1("NEW.parent_analysis_run_id")
    reject(
        "analysis_packets",
        "production_v3_insert",
        "INSERT",
        f"({pinned}) AND NEW.schema_version <> 'ANALYSIS_PACKET_V3'",
        "approved production audit requires ANALYSIS_PACKET_V3",
    )
    name = "trg_analysis_packets_production_audit_required_insert"
    result[name] = f"""CREATE TRIGGER IF NOT EXISTS {name}
        AFTER INSERT ON analysis_packets WHEN {pinned}
        BEGIN INSERT INTO production_audit_packet_requirements(packet_id)
        VALUES (NEW.packet_id); END"""
    projections = " OR ".join(
        f"json_extract(NEW.audit_json, '$.{path}') IS NOT NEW.{column}"
        for path, column in (
            ("artifact_id", "audit_id"),
            ("content_hash", "audit_hash"),
            ("content_payload.analysis_run_id", "analysis_run_id"),
            ("content_payload.packet.artifact_id", "packet_id"),
            ("content_payload.packet.content_hash", "packet_hash"),
            ("content_payload.quant_model_state_id", "quant_model_state_id"),
            ("content_payload.release.artifact_id", "release_id"),
            ("content_payload.target_acceptance_plan.artifact_id", "plan_id"),
        )
    )
    reject(
        "production_audit_bundles",
        "lineage_insert",
        "INSERT",
        f"""
        {projections}
        OR json_extract(NEW.audit_json, '$.schema_version') IS NOT 'APPROVED_TRAINING_HISTORY_AUDIT_V1'
        OR julianday(json_extract(NEW.audit_json, '$.content_payload.generated_at_utc')) IS NOT julianday(NEW.generated_at_utc)
        OR NOT EXISTS (SELECT 1 FROM analysis_packets p
            JOIN analysis_runs a ON a.analysis_run_id = p.parent_analysis_run_id
            JOIN analysis_run_target_acceptance_plans t ON t.analysis_run_id = a.analysis_run_id
            WHERE p.packet_id = NEW.packet_id AND p.packet_hash = NEW.packet_hash
            AND p.schema_version = 'ANALYSIS_PACKET_V3' AND a.status = 'COMPLETED'
            AND a.analysis_run_id = NEW.analysis_run_id
            AND t.quant_model_state_id = NEW.quant_model_state_id
            AND t.release_id = NEW.release_id AND t.plan_id = NEW.plan_id)
        """,
        "production audit packet/binding projection mismatch",
    )
    for table, packet in (
        ("llm_review_artifacts", "NEW.packet_id"),
        (
            "fusion_runs",
            "(SELECT packet_id FROM llm_review_artifacts WHERE review_artifact_id = NEW.llm_review_artifact_id)",
        ),
        (
            "portfolio_revisions",
            "(SELECT r.packet_id FROM fusion_runs f JOIN llm_review_artifacts r ON r.review_artifact_id = f.llm_review_artifact_id WHERE f.fusion_run_id = NEW.fusion_run_id)",
        ),
    ):
        reject(
            table,
            "production_audit_insert",
            "INSERT",
            f"""
            ({pinned}) AND NOT EXISTS (
                SELECT 1 FROM production_audit_bundles b
                JOIN analysis_packets p ON p.packet_id = b.packet_id
                JOIN production_audit_packet_requirements q ON q.packet_id = b.packet_id
                WHERE b.analysis_run_id = NEW.parent_analysis_run_id
                AND b.packet_id = {packet} AND b.packet_hash = p.packet_hash)
            """,
            "approved downstream artifact requires production audit sidecar",
        )
    return result
