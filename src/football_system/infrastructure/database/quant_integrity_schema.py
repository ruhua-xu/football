"""Frozen SQLite pilot schema shared by runtime and revision 9b8d3e5f0a21.

Reservations are immutable events; completion is a separate append. No mutable
RUNNING flag, deletion-based recovery, or reusable terminal series exists.
"""

import sqlalchemy as sa


PREFIX = "production_quant_integrity_pilot_"
QUANT_INTEGRITY_TABLES = tuple(
    PREFIX + suffix
    for suffix in (
        "series",
        "plans",
        "reservations",
        "outputs",
        "reports",
        "attempts",
        "summaries",
        "attestations",
    )
)


def quant_integrity_tables_v1(
    metadata: sa.MetaData, datetime_type: sa.types.TypeEngine
) -> dict[str, sa.Table]:
    def col(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=False, **kw)

    def fk(name, table, key="artifact_id", *, deferred=False):
        return sa.ForeignKeyConstraint(
            [name],
            [f"{PREFIX}{table}.{key}"],
            ondelete="RESTRICT",
            **({"deferrable": True, "initially": "DEFERRED"} if deferred else {}),
        )

    def envelope(schema):
        return [
            col("artifact_id", primary_key=True),
            col("content_hash", sa.String(64), unique=True),
            col("artifact_json", sa.Text()),
            col("operator_id"),
            col("persisted_at_utc", datetime_type),
            col("row_sha256", sa.String(64)),
            sa.CheckConstraint(
                "json_valid(artifact_json) AND json_type(artifact_json) = 'object'"
            ),
            sa.CheckConstraint(
                "json_extract(artifact_json, '$.artifact_id') IS artifact_id AND "
                "json_extract(artifact_json, '$.content_hash') IS content_hash"
            ),
            sa.CheckConstraint(
                f"json_extract(artifact_json, '$.schema_version') IS '{schema}'"
            ),
        ]

    def series_link():
        return [col("series_id"), fk("series_id", "series", "series_id")]

    def table(suffix, *items):
        items = list(items)
        for item in list(items):
            if isinstance(item, sa.Column) and item.name.endswith(
                ("hash", "sha256", "root")
            ):
                items.append(
                    sa.CheckConstraint(
                        f"length({item.name}) = 64 AND {item.name} NOT GLOB '*[^0-9a-f]*'"
                    )
                )
        return sa.Table(PREFIX + suffix, metadata, *items)

    tables = [
        table(
            "series",
            col("series_id", primary_key=True),
            col("scope_hash", sa.String(64)),
            col("series_sequence", sa.Integer()),
            col("evidence_use", sa.String(32)),
            sa.Column(
                "previous_attestation_id", sa.String(160), nullable=True, unique=True
            ),
            col("operator_id"),
            col("persisted_at_utc", datetime_type),
            col("row_sha256", sa.String(64)),
            fk("previous_attestation_id", "attestations", deferred=True),
            sa.UniqueConstraint("scope_hash", "series_sequence"),
            sa.CheckConstraint("series_sequence > 0"),
            sa.CheckConstraint(
                "evidence_use IN ('SYNTHETIC_CONTRACT_ONLY', 'REAL_SOURCE')"
            ),
        ),
        table(
            "plans",
            *envelope("PRODUCTION_QUANT_INTEGRITY_PILOT_PLAN_V1"),
            *series_link(),
            col("scope_hash", sa.String(64)),
            col("evidence_use", sa.String(32)),
            col("sealed_at_utc", datetime_type),
            sa.CheckConstraint("sealed_at_utc <= persisted_at_utc"),
        ),
        table(
            "reservations",
            *envelope("QUANT_INTEGRITY_ATTEMPT_RESERVATION_V1"),
            *series_link(),
            col("sequence", sa.Integer()),
            col("plan_id"),
            fk("plan_id", "plans"),
            col("actual_started_at_utc", datetime_type),
            sa.UniqueConstraint("series_id", "sequence"),
            sa.CheckConstraint("sequence > 0"),
            sa.CheckConstraint("actual_started_at_utc <= persisted_at_utc"),
        ),
        table(
            "outputs",
            *envelope("PRODUCTION_QUANT_INTEGRITY_PILOT_V1"),
            col("plan_id"),
            fk("plan_id", "plans"),
        ),
        table(
            "reports",
            *envelope("PRODUCTION_QUANT_INTEGRITY_PILOT_REPORT_V1"),
            col("plan_id"),
            fk("plan_id", "plans"),
            col("output_id"),
            fk("output_id", "outputs"),
        ),
        table(
            "attempts",
            *envelope("PRODUCTION_QUANT_INTEGRITY_PILOT_ATTEMPT_V1"),
            *series_link(),
            col("sequence", sa.Integer()),
            col("plan_id"),
            fk("plan_id", "plans"),
            col("reservation_id", unique=True),
            fk("reservation_id", "reservations"),
            col("status", sa.String(16)),
            col("actual_completed_at_utc", datetime_type),
            sa.Column("output_id", sa.String(160), nullable=True),
            fk("output_id", "outputs"),
            sa.Column("report_id", sa.String(160), nullable=True),
            fk("report_id", "reports"),
            sa.UniqueConstraint("series_id", "sequence"),
            sa.CheckConstraint(
                "sequence > 0 AND actual_completed_at_utc <= persisted_at_utc"
            ),
            sa.CheckConstraint(
                "(status = 'FAILED' AND output_id IS NULL AND report_id IS NULL) OR "
                "(status = 'COMPLETED' AND output_id IS NOT NULL AND report_id IS NOT NULL)"
            ),
        ),
        table(
            "summaries",
            *envelope("PRODUCTION_QUANT_INTEGRITY_PILOT_SUMMARY_V1"),
            *series_link(),
            # A summary is a terminal seal only when its matching attestation
            # commits in the same transaction. Neither can exist alone.
            fk("series_id", "attestations", "series_id", deferred=True),
            col("attempt_count", sa.Integer()),
            col("attempt_root", sa.String(64)),
            col("generated_at_utc", datetime_type),
            sa.UniqueConstraint("series_id"),
            sa.CheckConstraint(
                "attempt_count > 0 AND generated_at_utc <= persisted_at_utc"
            ),
        ),
        table(
            "attestations",
            *envelope("PRODUCTION_QUANT_INTEGRITY_PILOT_ATTESTATION_V1"),
            *series_link(),
            col("summary_id", unique=True),
            fk("summary_id", "summaries"),
            col("plan_id"),
            fk("plan_id", "plans"),
            col("report_id"),
            fk("report_id", "reports"),
            col("attempt_count", sa.Integer()),
            col("attempt_root", sa.String(64)),
            col("evidence_use", sa.String(32)),
            col("attested_at_utc", datetime_type),
            sa.UniqueConstraint("series_id"),
            sa.CheckConstraint(
                "attempt_count > 0 AND attested_at_utc <= persisted_at_utc"
            ),
        ),
    ]
    return {item.name: item for item in tables}


def quant_integrity_trigger_sql_v1() -> dict[str, str]:
    result = {}

    def trigger(suffix, name, event, condition, message):
        table = PREFIX + suffix
        name = f"trg_{table}_{name}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {event} ON {table} "
            f"{('WHEN ' + condition) if condition else ''} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    keys = {
        "series": (("series_id",), ("scope_hash", "series_sequence")),
        "plans": (),
        "reservations": (("series_id", "sequence"),),
        "outputs": (),
        "reports": (),
        "attempts": (("series_id", "sequence"), ("reservation_id",)),
        "summaries": (("series_id",),),
        "attestations": (("series_id",), ("summary_id",)),
    }
    for suffix, extra_keys in keys.items():
        groups = (
            extra_keys
            if suffix == "series"
            else (("artifact_id",), ("content_hash",), *extra_keys)
        )
        conflict = " OR ".join(
            "(" + " AND ".join(f"e.{k} = NEW.{k}" for k in group) + ")"
            for group in groups
        )
        trigger(
            suffix,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {PREFIX}{suffix} e WHERE {conflict})",
            "immutable pilot record already exists",
        )
        for action in ("UPDATE", "DELETE"):
            trigger(
                suffix,
                f"append_only_{action.lower()}",
                action,
                "",
                "pilot lineage is append-only",
            )
        if suffix in {"plans", "reservations", "attempts"}:
            trigger(
                suffix,
                "terminal_insert",
                "INSERT",
                f"EXISTS (SELECT 1 FROM {PREFIX}summaries WHERE series_id = NEW.series_id)",
                "terminal pilot series is closed",
            )

    trigger(
        "series",
        "continuous_insert",
        "INSERT",
        f"""
        NEW.series_sequence <> 1 + (SELECT COUNT(*) FROM {PREFIX}series WHERE scope_hash = NEW.scope_hash)
        OR (NEW.series_sequence = 1 AND NEW.previous_attestation_id IS NOT NULL)
        OR (NEW.series_sequence > 1 AND NOT EXISTS (
            SELECT 1 FROM {PREFIX}series s JOIN {PREFIX}attestations a ON a.series_id = s.series_id
            WHERE s.scope_hash = NEW.scope_hash AND s.series_sequence = NEW.series_sequence - 1
            AND a.artifact_id = NEW.previous_attestation_id AND a.evidence_use = NEW.evidence_use
            AND a.persisted_at_utc <= NEW.persisted_at_utc))
        """,
        "pilot series must disclose its immediately preceding terminal series",
    )
    trigger(
        "plans",
        "lineage_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM {PREFIX}series s WHERE s.series_id = NEW.series_id
            AND s.scope_hash = NEW.scope_hash AND s.evidence_use = NEW.evidence_use)
        OR json_extract(NEW.artifact_json, '$.content_payload.definition.integrity_pilot_series_id') IS NOT NEW.series_id
        OR json_extract(NEW.artifact_json, '$.content_payload.definition.provenance.evidence_use') IS NOT NEW.evidence_use
        """,
        "pilot plan series/provenance mismatch",
    )
    profile_fields = ("implementation_code_revision", "build_recipe")
    plan_mismatch = " OR ".join(
        f"json_extract(p.artifact_json, '$.content_payload.definition.{field}') IS NOT "
        f"json_extract(NEW.artifact_json, '$.content_payload.definition.{field}')"
        for field in profile_fields
    )
    trigger(
        "plans",
        "code_recipe_insert",
        "INSERT",
        f"EXISTS (SELECT 1 FROM {PREFIX}plans p WHERE p.series_id = NEW.series_id AND ({plan_mismatch}))",
        "incompatible pilot series code/recipe transition",
    )
    reservation_mismatch = " OR ".join(
        f"json_extract(p.artifact_json, '$.content_payload.definition.{field}') IS NOT "
        f"json_extract(other.artifact_json, '$.content_payload.definition.{field}')"
        for field in profile_fields
    )
    trigger(
        "reservations",
        "code_recipe_insert",
        "INSERT",
        f"EXISTS (SELECT 1 FROM {PREFIX}plans p JOIN {PREFIX}plans other ON other.series_id = p.series_id "
        f"WHERE p.artifact_id = NEW.plan_id AND ({reservation_mismatch}))",
        "incompatible pilot series code/recipe transition",
    )
    trigger(
        "reservations",
        "continuous_insert",
        "INSERT",
        f"""
        NEW.sequence <> 1 + (SELECT COUNT(*) FROM {PREFIX}reservations WHERE series_id = NEW.series_id)
        OR EXISTS (SELECT 1 FROM {PREFIX}reservations r WHERE r.series_id = NEW.series_id
            AND NOT EXISTS (SELECT 1 FROM {PREFIX}attempts a WHERE a.reservation_id = r.artifact_id))
        OR NOT EXISTS (SELECT 1 FROM {PREFIX}plans p WHERE p.artifact_id = NEW.plan_id
            AND p.series_id = NEW.series_id AND p.persisted_at_utc <= NEW.actual_started_at_utc)
        OR EXISTS (SELECT 1 FROM {PREFIX}attempts a WHERE a.series_id = NEW.series_id
            AND a.persisted_at_utc > NEW.actual_started_at_utc)
        OR json_extract(NEW.artifact_json, '$.content_payload.sequence') IS NOT NEW.sequence
        OR json_extract(NEW.artifact_json, '$.content_payload.prior_attempt_count') IS NOT NEW.sequence - 1
        OR json_extract(NEW.artifact_json, '$.content_payload.plan_ref.artifact_id') IS NOT NEW.plan_id
        OR json_extract(NEW.artifact_json, '$.content_payload.integrity_pilot_series_id') IS NOT NEW.series_id
        """,
        "pilot reservation must be continuous with one pending attempt",
    )
    for suffix in ("outputs", "reports"):
        trigger(
            suffix,
            "reserved_insert",
            "INSERT",
            f"""
            NOT EXISTS (SELECT 1 FROM {PREFIX}reservations r WHERE r.plan_id = NEW.plan_id
                AND NOT EXISTS (SELECT 1 FROM {PREFIX}attempts a WHERE a.reservation_id = r.artifact_id)
                AND NOT EXISTS (SELECT 1 FROM {PREFIX}summaries s WHERE s.series_id = r.series_id))
            OR json_extract(NEW.artifact_json, '$.content_payload.plan_ref.artifact_id') IS NOT NEW.plan_id
            """,
            "pilot output/report requires an open durable reservation",
        )
    trigger(
        "reports",
        "output_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM {PREFIX}outputs o WHERE o.artifact_id = NEW.output_id AND o.plan_id = NEW.plan_id)
        OR json_extract(NEW.artifact_json, '$.content_payload.output_ref.artifact_id') IS NOT NEW.output_id
        """,
        "pilot report output mismatch",
    )
    trigger(
        "attempts",
        "completion_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM {PREFIX}reservations r WHERE r.artifact_id = NEW.reservation_id
            AND r.series_id = NEW.series_id AND r.sequence = NEW.sequence AND r.plan_id = NEW.plan_id
            AND r.actual_started_at_utc <= NEW.actual_completed_at_utc)
        OR (NEW.status = 'COMPLETED' AND NOT EXISTS (
            SELECT 1 FROM {PREFIX}reports p WHERE p.artifact_id = NEW.report_id
            AND p.output_id = NEW.output_id AND p.plan_id = NEW.plan_id))
        OR json_extract(NEW.artifact_json, '$.content_payload.reservation.artifact_id') IS NOT NEW.reservation_id
        OR json_extract(NEW.artifact_json, '$.content_payload.status') IS NOT NEW.status
        OR json_extract(NEW.artifact_json, '$.content_payload.output_ref.artifact_id') IS NOT NEW.output_id
        OR json_extract(NEW.artifact_json, '$.content_payload.report_ref.artifact_id') IS NOT NEW.report_id
        """,
        "pilot attempt completion lineage mismatch",
    )
    trigger(
        "summaries",
        "complete_insert",
        "INSERT",
        f"""
        NEW.attempt_count <> (SELECT COUNT(*) FROM {PREFIX}reservations WHERE series_id = NEW.series_id)
        OR NEW.attempt_count <> (SELECT COUNT(*) FROM {PREFIX}attempts WHERE series_id = NEW.series_id)
        OR json_array_length(NEW.artifact_json, '$.content_payload.attempts') IS NOT NEW.attempt_count
        OR json_extract(NEW.artifact_json, '$.content_payload.attempt_count') IS NOT NEW.attempt_count
        OR json_extract(NEW.artifact_json, '$.content_payload.attempt_root') IS NOT NEW.attempt_root
        OR EXISTS (SELECT 1 FROM {PREFIX}attempts a WHERE a.series_id = NEW.series_id
            AND (json_extract(NEW.artifact_json, '$.content_payload.attempts[' || (a.sequence - 1) || '].artifact_id')
                IS NOT a.artifact_id OR a.persisted_at_utc > NEW.generated_at_utc))
        """,
        "pilot summary must include all completed reservations",
    )
    trigger(
        "attestations",
        "terminal_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM {PREFIX}summaries s JOIN {PREFIX}attempts a ON a.series_id = s.series_id
            JOIN {PREFIX}plans p ON p.artifact_id = a.plan_id
            WHERE s.artifact_id = NEW.summary_id AND s.series_id = NEW.series_id
            AND s.attempt_count = NEW.attempt_count AND s.attempt_root = NEW.attempt_root
            AND a.sequence = NEW.attempt_count AND a.status = 'COMPLETED'
            AND a.plan_id = NEW.plan_id AND a.report_id = NEW.report_id
            AND p.evidence_use = NEW.evidence_use AND s.persisted_at_utc <= NEW.persisted_at_utc)
        OR json_extract(NEW.artifact_json, '$.content_payload.summary_ref.artifact_id') IS NOT NEW.summary_id
        OR json_extract(NEW.artifact_json, '$.content_payload.plan_ref.artifact_id') IS NOT NEW.plan_id
        OR json_extract(NEW.artifact_json, '$.content_payload.report_ref.artifact_id') IS NOT NEW.report_id
        OR json_extract(NEW.artifact_json, '$.content_payload.attempt_count') IS NOT NEW.attempt_count
        OR json_extract(NEW.artifact_json, '$.content_payload.attempt_root') IS NOT NEW.attempt_root
        OR json_extract(NEW.artifact_json, '$.content_payload.provenance.evidence_use') IS NOT NEW.evidence_use
        """,
        "pilot terminal attestation must seal the exact final report and attempt root",
    )
    return result
