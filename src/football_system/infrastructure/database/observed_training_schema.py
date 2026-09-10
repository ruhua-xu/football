"""Frozen additive observed schema V1, shared with revision 062f3a5c1798.

Only typed normalization bindings authorize observed writes. No normalized table
is rebuilt, no historical row/index is replaced, and no cross-basis upgrade exists.
Capture ordinals are append-only INTEGER PRIMARY KEY values assigned inside the
original receipt insert transaction; unlike an implicit rowid they survive VACUUM.
"""

import sqlalchemy as sa

from football_system.infrastructure.database.training_correction_schema import (
    training_correction_trigger_sql_v2,
)

OBSERVED_TRAINING_TABLES = (
    "observed_collection_scopes",
    "observed_snapshot_admissions",
    "observed_snapshot_records",
    "observed_result_bindings",
)
OBSERVED_CAPTURE_ORDINALS = "observed_capture_ordinals"


def observed_training_tables_v1(metadata, datetime_type):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    def fk(name, table, target, *, deferred=False):
        return sa.ForeignKeyConstraint(
            [name],
            [f"{table}.{target}"],
            ondelete="RESTRICT",
            **({"deferrable": True, "initially": "DEFERRED"} if deferred else {}),
        )

    def common():
        return [c("artifact_json", sa.Text()), c("row_sha256", sa.String(64))]

    def request():
        return [
            c("request_key", unique=True),
            c("request_sha256", sa.String(64), unique=True),
            c("request_json", sa.Text()),
            c("operator_id"),
        ]

    def table(name, *items):
        checks = []
        for col in items:
            if isinstance(col, sa.Column) and col.name.endswith(("_hash", "sha256")):
                checks.append(
                    sa.CheckConstraint(
                        f"length({col.name}) = 64 AND {col.name} NOT GLOB '*[^0-9a-f]*'"
                    )
                )
            if isinstance(col, sa.Column) and col.name.endswith("_json"):
                checks.append(
                    sa.CheckConstraint(
                        f"json_valid({col.name}) AND json_type({col.name}) = 'object'"
                    )
                )
        return sa.Table(name, metadata, *items, *checks)

    tables = [
        table(
            OBSERVED_TRAINING_TABLES[0],
            c("scope_id", primary_key=True),
            c("subject_hash", sa.String(64), unique=True),
            c("content_hash", sa.String(64), unique=True),
            c("source_rights_admission_id"),
            c("provider_id"),
            c("canonical_competition_id"),
            c("recorded_at_utc", datetime_type),
            c("capture_receipt_high_watermark", sa.Integer()),
            *common(),
            *request(),
            sa.CheckConstraint("capture_receipt_high_watermark >= 0"),
            fk(
                "source_rights_admission_id",
                "source_rights_admissions",
                "source_rights_admission_id",
            ),
            fk("provider_id", "providers", "provider_id"),
            fk("canonical_competition_id", "competitions", "competition_id"),
        ),
        table(
            OBSERVED_TRAINING_TABLES[1],
            c("admission_id", primary_key=True),
            c("scope_id"),
            c("admission_sequence", sa.Integer()),
            c("content_hash", sa.String(64), unique=True),
            c("record_count", sa.Integer()),
            c("actual_started_at_utc", datetime_type),
            c("verified_at_utc", datetime_type),
            c("registered_at_utc", datetime_type),
            *common(),
            *request(),
            fk("scope_id", OBSERVED_TRAINING_TABLES[0], "scope_id"),
            sa.UniqueConstraint("scope_id", "admission_sequence"),
            sa.CheckConstraint("record_count > 0 AND admission_sequence >= 0"),
            sa.CheckConstraint(
                "actual_started_at_utc <= verified_at_utc AND verified_at_utc <= registered_at_utc"
            ),
        ),
        table(
            OBSERVED_TRAINING_TABLES[2],
            c("version_id", primary_key=True),
            c("content_hash", sa.String(64), unique=True),
            c("subject_hash", sa.String(64), unique=True),
            c("admission_id"),
            c("scope_id"),
            c("stream_id"),
            c("source_id"),
            c("source_rights_admission_id"),
            c("provider_id"),
            c("namespace"),
            c("fixture_key"),
            c("internal_match_id"),
            c("provider_mapping_id"),
            c("home_team_alias_id"),
            c("away_team_alias_id"),
            c("competition_mapping_id"),
            c("fixture_capture_id"),
            c("season_capture_id"),
            c("result_capture_id"),
            c("revision_sequence", sa.Integer()),
            c("predecessor_id", nullable=True, unique=True),
            c("capture_observed_at_utc", datetime_type),
            c("verified_at_utc", datetime_type),
            c("registered_at_utc", datetime_type),
            c("evidence_basis", sa.String(64)),
            c("trainable", sa.Boolean()),
            c("upstream_publication_at_utc", datetime_type, nullable=True),
            c("provider_finalized_at_utc", datetime_type, nullable=True),
            c("match_result_id", nullable=True, unique=True),
            *common(),
            fk(
                "admission_id",
                OBSERVED_TRAINING_TABLES[1],
                "admission_id",
                deferred=True,
            ),
            fk("scope_id", OBSERVED_TRAINING_TABLES[0], "scope_id"),
            fk(
                "source_rights_admission_id",
                "source_rights_admissions",
                "source_rights_admission_id",
            ),
            fk("provider_id", "providers", "provider_id"),
            fk("internal_match_id", "matches", "internal_match_id"),
            fk("provider_mapping_id", "provider_match_mappings", "mapping_id"),
            fk("home_team_alias_id", "provider_team_aliases", "alias_id"),
            fk("away_team_alias_id", "provider_team_aliases", "alias_id"),
            fk("competition_mapping_id", "provider_competition_mappings", "mapping_id"),
            *[
                fk(
                    f"{role}_capture_id",
                    "training_capture_receipts",
                    "capture_receipt_id",
                )
                for role in ("fixture", "season", "result")
            ],
            fk("match_result_id", "match_results", "match_result_id"),
            sa.ForeignKeyConstraint(
                ["predecessor_id", "stream_id"],
                [
                    "observed_snapshot_records.version_id",
                    "observed_snapshot_records.stream_id",
                ],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("version_id", "stream_id"),
            sa.UniqueConstraint("stream_id", "revision_sequence"),
            sa.UniqueConstraint(
                "provider_id", "internal_match_id", "revision_sequence"
            ),
            sa.UniqueConstraint(
                "provider_id", "namespace", "fixture_key", "revision_sequence"
            ),
            sa.UniqueConstraint("admission_id", "internal_match_id"),
            sa.CheckConstraint(
                "revision_sequence >= 0 AND ((revision_sequence = 0 AND predecessor_id IS NULL) OR (revision_sequence > 0 AND predecessor_id IS NOT NULL AND predecessor_id <> version_id))"
            ),
            sa.CheckConstraint(
                "capture_observed_at_utc <= verified_at_utc AND verified_at_utc <= registered_at_utc"
            ),
            sa.CheckConstraint(
                "evidence_basis = 'CURRENT_SNAPSHOT_OBSERVED' AND upstream_publication_at_utc IS NULL AND provider_finalized_at_utc IS NULL"
            ),
            sa.CheckConstraint(
                "trainable IN (0, 1) AND trainable = (match_result_id IS NOT NULL)"
            ),
        ),
        table(
            OBSERVED_TRAINING_TABLES[3],
            c("version_id", primary_key=True),
            c("stream_id"),
            c("evidence_basis", sa.String(64)),
            c("match_result_id", unique=True),
            c("provider_id"),
            c("internal_match_id"),
            c("provider_mapping_id"),
            c("home_goals", sa.Integer()),
            c("away_goals", sa.Integer()),
            c("observed_at_utc", datetime_type),
            c("available_at_utc", datetime_type),
            c("ingested_at_utc", datetime_type),
            c("source_result_key"),
            c("payload_hash", sa.String(64)),
            c("supersedes_match_result_id", nullable=True, unique=True),
            *common(),
            fk("version_id", OBSERVED_TRAINING_TABLES[2], "version_id", deferred=True),
            fk("match_result_id", "match_results", "match_result_id", deferred=True),
            fk("supersedes_match_result_id", "match_results", "match_result_id"),
            fk("provider_id", "providers", "provider_id"),
            fk("internal_match_id", "matches", "internal_match_id"),
            fk("provider_mapping_id", "provider_match_mappings", "mapping_id"),
            sa.CheckConstraint(
                "evidence_basis = 'CURRENT_SNAPSHOT_OBSERVED' AND home_goals >= 0 AND away_goals >= 0"
            ),
            sa.CheckConstraint(
                "observed_at_utc <= available_at_utc AND available_at_utc = ingested_at_utc"
            ),
        ),
    ]
    tables.append(
        sa.Table(
            OBSERVED_CAPTURE_ORDINALS,
            metadata,
            c("capture_ordinal", sa.Integer(), primary_key=True),
            c("capture_receipt_id", unique=True),
            c("receipt_hash", sa.String(64), nullable=True),
            c("capture_row_sha256", sa.String(64)),
            fk("capture_receipt_id", "training_capture_receipts", "capture_receipt_id"),
            sa.CheckConstraint("capture_ordinal > 0"),
            sqlite_autoincrement=True,
        )
    )
    return {t.name: t for t in tables}


def observed_training_trigger_sql_v1():
    result = {}

    def guard(table, suffix, event, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER {name} BEFORE {event} ON {table} {('WHEN ' + condition) if condition else ''} BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    for action in ("UPDATE", "DELETE"):
        guard(
            OBSERVED_CAPTURE_ORDINALS,
            f"append_only_{action.lower()}",
            action,
            "",
            "capture ordinal lineage is append-only",
        )
    guard(
        OBSERVED_CAPTURE_ORDINALS,
        "immutable_insert_existing",
        "INSERT",
        "EXISTS (SELECT 1 FROM observed_capture_ordinals o WHERE o.capture_receipt_id = NEW.capture_receipt_id OR o.capture_ordinal = NEW.capture_ordinal)",
        "immutable capture ordinal already exists",
    )
    guard(
        OBSERVED_CAPTURE_ORDINALS,
        "binding_insert",
        "INSERT",
        """
        NEW.capture_ordinal <> -1 OR NOT EXISTS (SELECT 1 FROM training_capture_receipts c
          WHERE c.capture_receipt_id = NEW.capture_receipt_id AND c.row_sha256 = NEW.capture_row_sha256
          AND json_extract(c.artifact_json, '$.receipt_hash') IS NEW.receipt_hash)
        """,
        "capture ordinal must be DB-assigned and bound to sealed receipt metadata",
    )
    result["trg_training_capture_receipts_observed_ordinal_insert"] = """
        CREATE TRIGGER trg_training_capture_receipts_observed_ordinal_insert
        AFTER INSERT ON training_capture_receipts BEGIN
          INSERT INTO observed_capture_ordinals (capture_receipt_id, receipt_hash, capture_row_sha256)
          VALUES (NEW.capture_receipt_id, json_extract(NEW.artifact_json, '$.receipt_hash'), NEW.row_sha256);
        END
    """

    keys = ("scope_id", "admission_id", "version_id", "version_id")
    for table, key in zip(OBSERVED_TRAINING_TABLES, keys, strict=True):
        for event in ("UPDATE", "DELETE"):
            guard(
                table,
                f"append_only_{event.lower()}",
                event,
                "",
                "observed lineage is append-only",
            )
        conflicts = [f"x.{key} = NEW.{key}"]
        if table in OBSERVED_TRAINING_TABLES[:2]:
            conflicts += [
                "x.request_key = NEW.request_key",
                "x.request_sha256 = NEW.request_sha256",
                "x.content_hash = NEW.content_hash",
            ]
        if table == OBSERVED_TRAINING_TABLES[0]:
            conflicts += ["x.subject_hash = NEW.subject_hash"]
        if table == OBSERVED_TRAINING_TABLES[1]:
            conflicts += [
                "(x.scope_id = NEW.scope_id AND x.admission_sequence = NEW.admission_sequence)"
            ]
        if table == OBSERVED_TRAINING_TABLES[2]:
            conflicts += [
                "x.predecessor_id = NEW.predecessor_id",
                "x.subject_hash = NEW.subject_hash",
                "x.content_hash = NEW.content_hash",
                "(x.stream_id = NEW.stream_id AND x.revision_sequence = NEW.revision_sequence)",
                "(x.provider_id = NEW.provider_id AND x.internal_match_id = NEW.internal_match_id AND x.revision_sequence = NEW.revision_sequence)",
                "(x.provider_id = NEW.provider_id AND x.namespace = NEW.namespace AND x.fixture_key = NEW.fixture_key AND x.revision_sequence = NEW.revision_sequence)",
                "(x.admission_id = NEW.admission_id AND x.internal_match_id = NEW.internal_match_id)",
                "x.match_result_id = NEW.match_result_id",
            ]
        if table == OBSERVED_TRAINING_TABLES[3]:
            conflicts += [
                "x.match_result_id = NEW.match_result_id",
                "x.supersedes_match_result_id = NEW.supersedes_match_result_id",
            ]
        guard(
            table,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {table} x WHERE {' OR '.join(conflicts)})",
            "immutable observed identity already exists",
        )

    invalid_subject_provenance = """
        json_type(NEW.artifact_json, '$.subject.source_data_mode') IS NOT 'text'
        OR json_extract(NEW.artifact_json, '$.subject.source_data_mode') IS NOT 'SOURCE_TIME_RESEARCH'
        OR json_type(NEW.artifact_json, '$.subject.retrospective') IS NOT 'true'
    """
    shadow_provenance = """
        json_type(NEW.artifact_json, '$.source_data_mode') IS NOT NULL
        OR json_type(NEW.artifact_json, '$.retrospective') IS NOT NULL
    """
    for table in ("observed_collection_scopes", "observed_snapshot_records"):
        guard(
            table,
            "retrospective_insert",
            "INSERT",
            f"({invalid_subject_provenance}) OR ({shadow_provenance})",
            "observed provenance requires SOURCE_TIME_RESEARCH and retrospective true on its subject",
        )
    guard(
        "observed_snapshot_admissions",
        "retrospective_insert",
        "INSERT",
        f"({shadow_provenance}) OR EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.records') record WHERE {invalid_subject_provenance.replace('NEW.artifact_json', 'record.value')})",
        "observed admission requires uniform retrospective SOURCE_TIME_RESEARCH subjects",
    )
    guard(
        "observed_collection_scopes",
        "capture_watermark_insert",
        "INSERT",
        """
        NEW.capture_receipt_high_watermark IS NOT (SELECT COALESCE(MAX(capture_ordinal), 0) FROM observed_capture_ordinals)
        OR json_type(NEW.artifact_json, '$.capture_receipt_high_watermark') IS NOT 'integer'
        OR json_extract(NEW.artifact_json, '$.capture_receipt_high_watermark') IS NOT NEW.capture_receipt_high_watermark
        """,
        "observed scope requires the actual capture ledger high-watermark",
    )

    guard(
        "observed_snapshot_records",
        "sealed_insert",
        "INSERT",
        "EXISTS (SELECT 1 FROM observed_snapshot_admissions a WHERE a.admission_id = NEW.admission_id)",
        "sealed observed admission cannot gain children",
    )
    guard(
        "observed_result_bindings",
        "sealed_insert",
        "INSERT",
        "EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.version_id = NEW.version_id)",
        "sealed observed version cannot gain a result",
    )
    guard(
        "observed_snapshot_records",
        "head_insert",
        "INSERT",
        """
        (NEW.revision_sequence > 0 AND NOT EXISTS (SELECT 1 FROM observed_snapshot_records p
          JOIN observed_snapshot_admissions a ON a.admission_id = p.admission_id
          WHERE p.version_id = NEW.predecessor_id AND p.stream_id = NEW.stream_id
          AND p.revision_sequence + 1 = NEW.revision_sequence AND p.scope_id = NEW.scope_id
          AND p.provider_id = NEW.provider_id AND p.internal_match_id = NEW.internal_match_id
          AND p.source_id = NEW.source_id AND p.namespace = NEW.namespace AND p.fixture_key = NEW.fixture_key
          AND a.scope_id = NEW.scope_id AND p.admission_id <> NEW.admission_id
          AND a.admission_sequence < (SELECT COUNT(*) FROM observed_snapshot_admissions x WHERE x.scope_id = NEW.scope_id)
          AND p.capture_observed_at_utc <= NEW.capture_observed_at_utc AND p.registered_at_utc <= NEW.registered_at_utc
          AND json_extract(NEW.artifact_json, '$.subject.result_capture.capture_ordinal') > json_extract(p.artifact_json, '$.subject.result_capture.capture_ordinal')
          AND json_extract(NEW.artifact_json, '$.subject.fixture_capture.capture_ordinal') >= json_extract(p.artifact_json, '$.subject.fixture_capture.capture_ordinal')
          AND json_extract(NEW.artifact_json, '$.subject.season_capture.capture_ordinal') >= json_extract(p.artifact_json, '$.subject.season_capture.capture_ordinal')))
        OR EXISTS (SELECT 1 FROM observed_snapshot_records p WHERE p.stream_id = NEW.stream_id AND p.revision_sequence >= NEW.revision_sequence)
        OR EXISTS (SELECT 1 FROM observed_snapshot_records p WHERE p.provider_id = NEW.provider_id AND p.stream_id <> NEW.stream_id
          AND (p.internal_match_id = NEW.internal_match_id OR (p.namespace = NEW.namespace AND p.fixture_key = NEW.fixture_key)))
        """,
        "observed version requires a sealed earlier predecessor and advancing capture ordinals; no fork/cycle",
    )
    guard(
        "observed_snapshot_records",
        "cross_basis_insert",
        "INSERT",
        """
        EXISTS (SELECT 1 FROM training_fact_bindings b JOIN provider_match_mappings m ON m.mapping_id = b.provider_mapping_id
          WHERE m.provider_id = NEW.provider_id AND (b.internal_match_id = NEW.internal_match_id OR (m.external_namespace = NEW.namespace AND m.external_match_id = NEW.fixture_key)))
        OR EXISTS (SELECT 1 FROM training_correction_streams s WHERE s.provider_id = NEW.provider_id AND (s.internal_match_id = NEW.internal_match_id OR (s.namespace = NEW.namespace AND s.fixture_key = NEW.fixture_key)))
        OR EXISTS (SELECT 1 FROM match_results r WHERE r.provider_id = NEW.provider_id AND r.internal_match_id = NEW.internal_match_id
          AND NOT EXISTS (SELECT 1 FROM observed_result_bindings b WHERE b.match_result_id = r.match_result_id))
        """,
        "cross-basis or unqualified result stream requires an explicit reviewed path",
    )
    guard(
        "training_fact_bindings",
        "observed_basis_insert",
        "INSERT",
        """
        EXISTS (SELECT 1 FROM observed_snapshot_records r JOIN provider_match_mappings m ON m.mapping_id = NEW.provider_mapping_id
          WHERE r.provider_id = m.provider_id AND (r.internal_match_id = NEW.internal_match_id OR (r.namespace = m.external_namespace AND r.fixture_key = m.external_match_id)))
        """,
        "observed stream cannot automatically cross into verified historical basis",
    )

    capture_checks = []
    for role in ("fixture", "season", "result"):
        capture_checks.append(f"""NOT EXISTS (SELECT 1 FROM training_capture_receipts c JOIN observed_collection_scopes s ON s.scope_id = NEW.scope_id
          JOIN observed_capture_ordinals o ON o.capture_receipt_id = c.capture_receipt_id
          WHERE c.capture_receipt_id = NEW.{role}_capture_id AND c.provider_id = NEW.provider_id
          AND c.source_id = NEW.source_id AND c.source_rights_admission_id = NEW.source_rights_admission_id
          AND s.source_rights_admission_id = c.source_rights_admission_id AND s.provider_id = c.provider_id
          AND o.capture_ordinal > s.capture_receipt_high_watermark
          AND o.capture_row_sha256 = c.row_sha256 AND o.receipt_hash IS json_extract(c.artifact_json, '$.receipt_hash')
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.capture_ordinal') = o.capture_ordinal
          AND s.recorded_at_utc <= c.local_imported_at_utc AND c.registered_at_utc <= NEW.verified_at_utc
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.capture_receipt_id') = c.capture_receipt_id
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.payload_sha256') = c.payload_sha256
          AND json_type(NEW.artifact_json, '$.subject.{role}_capture.outcome_sha256') = 'text'
          AND length(json_extract(NEW.artifact_json, '$.subject.{role}_capture.outcome_sha256')) = 64
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.outcome_sha256') NOT GLOB '*[^0-9a-f]*'
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.outcome_sha256') = json_extract(NEW.artifact_json, '$.subject.result_capture.outcome_sha256')
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.capture_record_count') BETWEEN 1 AND 50
          AND json_extract(NEW.artifact_json, '$.subject.{role}_capture.capture_record_count') = CASE json_type(CAST(c.payload_bytes AS TEXT), '$.data')
            WHEN 'object' THEN 1 WHEN 'array' THEN json_array_length(CAST(c.payload_bytes AS TEXT), '$.data') ELSE 0 END)""")
    guard(
        "observed_snapshot_records",
        "capture_insert",
        "INSERT",
        " OR ".join(capture_checks),
        "observed capture requires predeclared reviewed scope and exact receipt lineage",
    )
    guard(
        "observed_snapshot_records",
        "identity_insert",
        "INSERT",
        """
        NOT EXISTS (SELECT 1 FROM provider_match_mappings m WHERE m.mapping_id = NEW.provider_mapping_id
          AND m.provider_id = NEW.provider_id AND m.internal_match_id = NEW.internal_match_id
          AND m.external_namespace = NEW.namespace AND m.external_match_id = NEW.fixture_key)
        OR json_extract(NEW.artifact_json, '$.version_id') IS NOT NEW.version_id
        OR json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.content_hash
        OR json_extract(NEW.artifact_json, '$.subject.revision_sequence') IS NOT NEW.revision_sequence
        OR json_extract(NEW.artifact_json, '$.subject.predecessor_id') IS NOT NEW.predecessor_id
        OR json_extract(NEW.artifact_json, '$.subject.evidence_basis') IS NOT NEW.evidence_basis
        OR json_type(NEW.artifact_json, '$.subject.upstream_publication_at_utc') IS NOT 'null'
        OR json_type(NEW.artifact_json, '$.subject.provider_finalized_at_utc') IS NOT 'null'
        OR COALESCE(json_type(NEW.artifact_json, '$.subject.resolved_home_team_id'), 'missing') NOT IN ('text', 'null')
        OR COALESCE(json_type(NEW.artifact_json, '$.subject.resolved_away_team_id'), 'missing') NOT IN ('text', 'null')
        OR NEW.trainable IS NOT (
          json_extract(NEW.artifact_json, '$.subject.inspection.trainable') = 1
          AND json_extract(NEW.artifact_json, '$.subject.resolved_home_team_id') IS json_extract(NEW.artifact_json, '$.subject.identity.internal_home_team_id')
          AND json_extract(NEW.artifact_json, '$.subject.resolved_away_team_id') IS json_extract(NEW.artifact_json, '$.subject.identity.internal_away_team_id')
          AND json_extract(NEW.artifact_json, '$.subject.inspection.kickoff_at_utc') IS json_extract(NEW.artifact_json, '$.subject.identity.kickoff_at_utc'))
        """,
        "observed identity/basis projection mismatch",
    )

    fields = (
        "match_result_id",
        "provider_id",
        "internal_match_id",
        "provider_mapping_id",
        "home_goals",
        "away_goals",
        "observed_at_utc",
        "available_at_utc",
        "ingested_at_utc",
        "source_result_key",
        "payload_hash",
        "supersedes_match_result_id",
    )
    equalities = " AND ".join(f"b.{field} IS NEW.{field}" for field in fields)
    authorized = f"""EXISTS (SELECT 1 FROM observed_result_bindings b WHERE {equalities}
        AND b.evidence_basis = 'CURRENT_SNAPSHOT_OBSERVED'
        AND NOT EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.version_id = b.version_id))"""
    guard(
        "match_results",
        "observed_stream_insert",
        "INSERT",
        f"""
        EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.provider_id = NEW.provider_id AND r.internal_match_id = NEW.internal_match_id)
        AND NOT ({authorized})
        """,
        "observed result stream requires a typed controlled normalization transaction",
    )
    # Preserve the V2 guard verbatim apart from authorizing equal *local* clocks
    # for an exact deferred observed binding. No upstream revision order is forged.
    name = "trg_match_results_supersession_insert"
    result[name] = training_correction_trigger_sql_v2()[name].replace(
        "p.ingested_at_utc < NEW.ingested_at_utc",
        f"(p.ingested_at_utc < NEW.ingested_at_utc OR (p.ingested_at_utc = NEW.ingested_at_utc AND ({authorized})))",
    )
    guard(
        "observed_result_bindings",
        "predecessor_insert",
        "INSERT",
        """
        EXISTS (SELECT 1 FROM match_results r WHERE r.provider_id = NEW.provider_id AND r.internal_match_id = NEW.internal_match_id
          AND r.match_result_id <> COALESCE(NEW.supersedes_match_result_id, '')
          AND NOT EXISTS (SELECT 1 FROM match_results child WHERE child.supersedes_match_result_id = r.match_result_id))
        OR (NEW.supersedes_match_result_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM observed_result_bindings b JOIN match_results p ON p.match_result_id = b.match_result_id
          WHERE b.match_result_id = NEW.supersedes_match_result_id AND b.stream_id = NEW.stream_id
          AND p.provider_id = NEW.provider_id AND p.internal_match_id = NEW.internal_match_id
          AND p.observed_at_utc <= NEW.observed_at_utc AND p.ingested_at_utc <= NEW.ingested_at_utc))
        """,
        "observed normalization requires actual observed normalized predecessor",
    )
    result_checks = " AND ".join(f"b.{field} IS r.{field}" for field in fields)
    guard(
        "observed_snapshot_records",
        "result_insert",
        "INSERT",
        f"""
        (NEW.trainable = 1 AND NOT EXISTS (SELECT 1 FROM observed_result_bindings b JOIN match_results r ON r.match_result_id = b.match_result_id
          WHERE b.version_id = NEW.version_id AND b.stream_id = NEW.stream_id AND b.match_result_id = NEW.match_result_id
          AND b.provider_id = NEW.provider_id AND b.internal_match_id = NEW.internal_match_id AND b.provider_mapping_id = NEW.provider_mapping_id
          AND b.observed_at_utc = (SELECT c.local_imported_at_utc FROM training_capture_receipts c WHERE c.capture_receipt_id = NEW.result_capture_id)
          AND b.observed_at_utc <= NEW.capture_observed_at_utc AND b.available_at_utc = NEW.registered_at_utc AND b.ingested_at_utc = NEW.registered_at_utc
          AND json_extract(NEW.artifact_json, '$.normalized_result') = json(b.artifact_json) AND {result_checks}))
        OR (NEW.trainable = 0 AND (EXISTS (SELECT 1 FROM observed_result_bindings b WHERE b.version_id = NEW.version_id)
          OR json_type(NEW.artifact_json, '$.normalized_result') IS NOT 'null'))
        """,
        "observed version requires exact typed result binding or explicit withdrawal",
    )
    guard(
        "observed_snapshot_admissions",
        "complete_insert",
        "INSERT",
        """
        NEW.admission_sequence <> (SELECT COUNT(*) FROM observed_snapshot_admissions a WHERE a.scope_id = NEW.scope_id)
        OR EXISTS (SELECT 1 FROM observed_snapshot_admissions a WHERE a.scope_id = NEW.scope_id AND a.registered_at_utc > NEW.actual_started_at_utc)
        OR (SELECT COUNT(*) FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id) <> NEW.record_count
        OR json_array_length(NEW.artifact_json, '$.records') IS NOT NEW.record_count
        OR EXISTS (SELECT 1 FROM observed_snapshot_records r JOIN observed_snapshot_records p ON p.version_id = r.predecessor_id
          LEFT JOIN observed_snapshot_admissions a ON a.admission_id = p.admission_id
          WHERE r.admission_id = NEW.admission_id AND (a.admission_id IS NULL OR a.scope_id <> NEW.scope_id OR a.admission_sequence >= NEW.admission_sequence))
        OR (SELECT COUNT(*) FROM observed_result_bindings b JOIN observed_snapshot_records r ON r.version_id = b.version_id WHERE r.admission_id = NEW.admission_id)
          <> (SELECT COUNT(*) FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id AND r.trainable = 1)
        OR (SELECT COUNT(*) FROM observed_snapshot_records r JOIN match_results m ON m.match_result_id = r.match_result_id WHERE r.admission_id = NEW.admission_id)
          <> (SELECT COUNT(*) FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id AND r.trainable = 1)
        OR EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id AND
          (r.scope_id <> NEW.scope_id OR r.verified_at_utc <> NEW.verified_at_utc OR r.registered_at_utc <> NEW.registered_at_utc
           OR NOT EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.records') j WHERE json(j.value) = json(r.artifact_json))))
        """,
        "observed admission requires complete exact child graph",
    )
    included = """
        SELECT ids.value AS fixture_key,
          json_extract(season.value, '$.provider_season_id') AS provider_season_id,
          json_extract(season.value, '$.canonical_season_id') AS canonical_season_id
        FROM observed_collection_scopes s, json_each(s.artifact_json, '$.subject.seasons') season,
          json_each(season.value, '$.expected_fixture_ids') ids
        WHERE s.scope_id = NEW.scope_id AND NOT EXISTS (
          SELECT 1 FROM json_each(season.value, '$.exceptions') exception
          WHERE json_extract(exception.value, '$.provider_fixture_key') = ids.value)
    """
    matches = """r.fixture_key = expected.fixture_key
        AND json_extract(r.artifact_json, '$.subject.inspection.provider_season_id') = expected.provider_season_id
        AND json_extract(r.artifact_json, '$.subject.identity.season') = expected.canonical_season_id"""
    guard(
        "observed_snapshot_admissions",
        "scope_cohort_insert",
        "INSERT",
        f"""
        EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id
          AND NOT EXISTS (SELECT 1 FROM ({included}) expected WHERE {matches}))
        OR (NEW.admission_sequence = 0 AND (
          (SELECT COUNT(*) FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id) <> (SELECT COUNT(*) FROM ({included}))
          OR EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id AND r.revision_sequence <> 0)
          OR EXISTS (SELECT 1 FROM ({included}) expected WHERE NOT EXISTS (
            SELECT 1 FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id AND {matches}))))
        """,
        "first observed admission requires exact declared scope cohort and complete roots",
    )
    role_receipts = " UNION ".join(
        f"SELECT {role}_capture_id FROM observed_snapshot_records WHERE scope_id = NEW.scope_id"
        for role in ("fixture", "season", "result")
    )
    guard(
        "observed_snapshot_admissions",
        "scope_limits_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM observed_collection_scopes s WHERE s.scope_id = NEW.scope_id
          AND json_type(s.artifact_json, '$.subject.max_snapshot_records') = 'integer'
          AND json_type(s.artifact_json, '$.subject.max_capture_receipts') = 'integer'
          AND (SELECT COUNT(*) FROM observed_snapshot_records r WHERE r.scope_id = NEW.scope_id)
            <= json_extract(s.artifact_json, '$.subject.max_snapshot_records')
          AND (SELECT COUNT(*) FROM ({role_receipts}))
            <= json_extract(s.artifact_json, '$.subject.max_capture_receipts'))
        """,
        "observed scope receipt/record limit exceeded",
    )

    # Seal the exact source selection, including negative evidence (no other
    # candidates). Later catalog inserts cannot change this immutable assessment.
    evidence_checks = [
        """r.capture_observed_at_utc IS NOT (
        SELECT MAX(c.local_imported_at_utc) FROM training_capture_receipts c
        WHERE c.capture_receipt_id IN (r.fixture_capture_id, r.season_capture_id, r.result_capture_id))"""
    ]
    for name, table, key, predicate in (
        (
            "match_mappings",
            "provider_match_mappings",
            "mapping_id",
            "i.mapping_id = r.provider_mapping_id OR i.supersedes_mapping_id = r.provider_mapping_id",
        ),
        (
            "team_aliases",
            "provider_team_aliases",
            "alias_id",
            """i.alias_id IN (r.home_team_alias_id, r.away_team_alias_id)
            OR (i.provider_id = r.provider_id AND i.available_at_utc <= r.capture_observed_at_utc AND (
              (i.provider_team_id = json_extract(r.artifact_json, '$.subject.inspection.provider_home_team_id')
               AND i.team_type = (SELECT team_type FROM provider_team_aliases WHERE alias_id = r.home_team_alias_id))
              OR (i.provider_team_id = json_extract(r.artifact_json, '$.subject.inspection.provider_away_team_id')
               AND i.team_type = (SELECT team_type FROM provider_team_aliases WHERE alias_id = r.away_team_alias_id))))""",
        ),
        (
            "competition_mappings",
            "provider_competition_mappings",
            "mapping_id",
            """i.mapping_id = r.competition_mapping_id
            OR (i.provider_id = r.provider_id AND i.available_at_utc <= r.capture_observed_at_utc
              AND i.provider_competition_id = json_extract(r.artifact_json, '$.subject.inspection.provider_competition_id')
              AND i.season = (SELECT season FROM canonical_match_identities WHERE internal_match_id = r.internal_match_id)
              AND i.competition_type = (SELECT competition_type FROM canonical_match_identities WHERE internal_match_id = r.internal_match_id))""",
        ),
    ):
        path = f"$.subject.identity_evidence.{name}"
        source = f"SELECT i.{key} AS record_id FROM {table} i WHERE {predicate}"
        evidence_checks.append(f"""
            json_type(r.artifact_json, '{path}') IS NOT 'array'
            OR json_array_length(r.artifact_json, '{path}') IS NOT (SELECT COUNT(*) FROM ({source}))
            OR EXISTS (SELECT 1 FROM ({source}) expected WHERE NOT EXISTS (
              SELECT 1 FROM json_each(r.artifact_json, '{path}') ref
              WHERE json_extract(ref.value, '$.record_id') = expected.record_id))
        """)
    guard(
        "observed_snapshot_admissions",
        "identity_evidence_insert",
        "INSERT",
        "EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.admission_id = NEW.admission_id AND ("
        + " OR ".join(evidence_checks)
        + "))",
        "observed identity evidence requires the complete current catalog selection at seal",
    )
    return result


def observed_capture_ordinal_backfill_sql_v1():
    # Legacy receipts are metadata-only bootstrap entries before any observed scope.
    # New receipts get their stable INTEGER PRIMARY KEY in the capture transaction.
    return """INSERT INTO observed_capture_ordinals (capture_receipt_id, receipt_hash, capture_row_sha256)
        SELECT c.capture_receipt_id, json_extract(c.artifact_json, '$.receipt_hash'), c.row_sha256
        FROM training_capture_receipts c
        WHERE NOT EXISTS (SELECT 1 FROM observed_capture_ordinals o WHERE o.capture_receipt_id = c.capture_receipt_id)
        ORDER BY c.rowid"""


def install_observed_training_triggers(connection):
    for name, sql in observed_training_trigger_sql_v1().items():
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        connection.exec_driver_sql(sql)
    connection.exec_driver_sql(observed_capture_ordinal_backfill_sql_v1())
