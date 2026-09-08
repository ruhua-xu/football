"""Frozen SQLite correction schema V2, shared by runtime and e40d183af576.

Runtime metadata and create_schema install this automatically. Existing V1
normalized tables use the same checksum-verified rebuild as Alembic; repository
reads never migrate a database. Frozen V1 factories remain unchanged.
"""

import hashlib
import json
import re

import sqlalchemy as sa
from sqlalchemy.schema import CreateTable


CORRECTION_TABLES = (
    "training_correction_schema_versions",
    "training_correction_streams",
    "training_correction_admissions",
    "training_correction_components",
    "training_correction_result_bindings",
)


def training_correction_tables_v2(metadata, datetime_type):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    def common():
        return [c("artifact_json", sa.Text()), c("row_sha256", sa.String(64))]

    # Target declarations permit standalone DDL compilation, not new tables.
    for name, keys in {
        "matches": ("internal_match_id",),
        "providers": ("provider_id",),
        "training_fact_bindings": (
            "training_fact_admission_id",
            "training_fact_binding_id",
        ),
        "match_results": ("match_result_id",),
        "provider_match_mappings": ("mapping_id",),
    }.items():
        if name not in metadata.tables:
            sa.Table(name, metadata, *(c(key, primary_key=True) for key in keys))

    def fk(names, table, targets, deferred=False):
        return sa.ForeignKeyConstraint(
            names,
            [f"{table}.{x}" for x in targets],
            ondelete="RESTRICT",
            **({"deferrable": True, "initially": "DEFERRED"} if deferred else {}),
        )

    def table(name, *columns):
        checks = []
        for col in columns:
            if isinstance(col, sa.Column) and col.name.endswith("_json"):
                checks.append(
                    sa.CheckConstraint(
                        f"json_valid({col.name}) AND json_type({col.name}) = 'object'"
                    )
                )
            if isinstance(col, sa.Column) and col.name.endswith(("_hash", "sha256")):
                checks.append(
                    sa.CheckConstraint(
                        f"length({col.name}) = 64 AND {col.name} NOT GLOB '*[^0-9a-f]*'"
                    )
                )
        return sa.Table(name, metadata, *columns, *checks)

    tables = [
        table(
            CORRECTION_TABLES[0],
            c("version", sa.Integer(), primary_key=True),
            c("preserved_result_count", sa.Integer()),
            c("preserved_results_sha256", sa.String(64)),
            c("original_result_table_sql", sa.Text()),
            *common(),
        ),
        table(
            CORRECTION_TABLES[1],
            c("stream_id", primary_key=True),
            c("source_id"),
            c("provider_id"),
            c("namespace"),
            c("fixture_key"),
            c("internal_match_id"),
            c("base_admission_id"),
            c("base_binding_id"),
            c("base_version_id", unique=True),
            *common(),
            sa.UniqueConstraint("source_id", "provider_id", "namespace", "fixture_key"),
            sa.UniqueConstraint("provider_id", "internal_match_id"),
            fk(["internal_match_id"], "matches", ["internal_match_id"]),
            fk(["provider_id"], "providers", ["provider_id"]),
            fk(
                ["base_admission_id", "base_binding_id"],
                "training_fact_bindings",
                ["training_fact_admission_id", "training_fact_binding_id"],
            ),
        ),
        table(
            CORRECTION_TABLES[2],
            c("correction_id", primary_key=True),
            c("stream_id"),
            c("predecessor_version_id", unique=True),
            c("revision_sequence", sa.Integer()),
            c("content_hash", sa.String(64), unique=True),
            c("registered_at_utc", datetime_type),
            c("source_available_at_utc", datetime_type),
            c("provider_revision_id", nullable=True),
            c("provider_revision_order", sa.Integer(), nullable=True),
            c("request_key", unique=True),
            c("request_sha256", sa.String(64), unique=True),
            c("request_json", sa.Text()),
            c("operator_id"),
            *common(),
            sa.UniqueConstraint("stream_id", "revision_sequence"),
            sa.UniqueConstraint("stream_id", "provider_revision_id"),
            sa.UniqueConstraint("stream_id", "provider_revision_order"),
            sa.CheckConstraint(
                "revision_sequence > 0 AND correction_id <> predecessor_version_id"
            ),
            fk(["stream_id"], CORRECTION_TABLES[1], ["stream_id"]),
        ),
        table(
            CORRECTION_TABLES[3],
            c("correction_id", primary_key=True),
            c("component", primary_key=True),
            c("reference_id"),
            c("reference_hash", sa.String(64)),
            *common(),
            sa.CheckConstraint(
                "component IN ('FIXTURE','MAPPING','SEASON','STATUS','RESULT')"
            ),
            fk(["correction_id"], CORRECTION_TABLES[2], ["correction_id"], True),
        ),
        table(
            CORRECTION_TABLES[4],
            c("correction_id", primary_key=True),
            c("stream_id"),
            c("match_result_id", unique=True),
            c("previous_match_result_id", unique=True),
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
            *common(),
            fk(["correction_id"], CORRECTION_TABLES[2], ["correction_id"], True),
            fk(["stream_id"], CORRECTION_TABLES[1], ["stream_id"]),
            fk(["match_result_id"], "match_results", ["match_result_id"], True),
            fk(["previous_match_result_id"], "match_results", ["match_result_id"]),
            fk(["provider_mapping_id"], "provider_match_mappings", ["mapping_id"]),
        ),
    ]
    return {t.name: t for t in tables}


def training_correction_trigger_sql_v2():
    result = {}

    def guard(table, suffix, event, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER {name} BEFORE {event} ON {table} "
            f"{('WHEN ' + condition) if condition else ''} BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    primary = {
        CORRECTION_TABLES[0]: "existing.version = NEW.version",
        CORRECTION_TABLES[
            1
        ]: "existing.stream_id = NEW.stream_id OR existing.base_version_id = NEW.base_version_id OR (existing.provider_id = NEW.provider_id AND existing.internal_match_id = NEW.internal_match_id)",
        CORRECTION_TABLES[
            2
        ]: "existing.correction_id = NEW.correction_id OR existing.predecessor_version_id = NEW.predecessor_version_id OR existing.request_key = NEW.request_key OR (existing.stream_id = NEW.stream_id AND existing.revision_sequence = NEW.revision_sequence)",
        CORRECTION_TABLES[
            3
        ]: "existing.correction_id = NEW.correction_id AND existing.component = NEW.component",
        CORRECTION_TABLES[
            4
        ]: "existing.correction_id = NEW.correction_id OR existing.match_result_id = NEW.match_result_id OR existing.previous_match_result_id = NEW.previous_match_result_id",
    }
    for table in CORRECTION_TABLES:
        for event in ("UPDATE", "DELETE"):
            guard(
                table,
                f"append_only_{event.lower()}",
                event,
                "",
                "controlled correction lineage is append-only",
            )
        guard(
            table,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {table} existing WHERE {primary[table]})",
            "immutable correction already exists",
        )
    for table in CORRECTION_TABLES[3:]:
        guard(
            table,
            "sealed_insert",
            "INSERT",
            "EXISTS (SELECT 1 FROM training_correction_admissions a WHERE a.correction_id = NEW.correction_id)",
            "sealed correction cannot gain children",
        )

    guard(
        "training_correction_components",
        "projection_insert",
        "INSERT",
        """
        json_extract(NEW.artifact_json, '$.component') IS NOT NEW.component
        OR json_extract(NEW.artifact_json, '$.reference.artifact_id') IS NOT NEW.reference_id
        OR json_extract(NEW.artifact_json, '$.reference.content_hash') IS NOT NEW.reference_hash
        """,
        "correction component reference projection mismatch",
    )
    guard(
        "training_correction_result_bindings",
        "predecessor_insert",
        "INSERT",
        """
        NOT EXISTS (SELECT 1 FROM training_correction_streams s
          JOIN match_results p ON p.match_result_id = NEW.previous_match_result_id
          JOIN provider_match_mappings m ON m.mapping_id = NEW.provider_mapping_id
          WHERE s.stream_id = NEW.stream_id AND s.provider_id = NEW.provider_id
            AND s.internal_match_id = NEW.internal_match_id AND p.provider_id = s.provider_id
            AND p.internal_match_id = s.internal_match_id AND m.provider_id = s.provider_id
            AND m.internal_match_id = s.internal_match_id AND m.external_namespace = s.namespace
            AND m.external_match_id = s.fixture_key AND p.available_at_utc <= NEW.available_at_utc
            AND p.ingested_at_utc <= NEW.ingested_at_utc AND NEW.ingested_at_utc = NEW.available_at_utc)
        OR EXISTS (SELECT 1 FROM match_results r WHERE r.supersedes_match_result_id = NEW.previous_match_result_id)
        OR NEW.match_result_id = NEW.previous_match_result_id
        """,
        "controlled result requires the same provider/match actual normalized predecessor",
    )

    guard(
        "training_correction_admissions",
        "head_insert",
        "INSERT",
        """
        NOT EXISTS (SELECT 1 FROM training_correction_streams s WHERE s.stream_id = NEW.stream_id AND (
          (NEW.revision_sequence = 1 AND NEW.predecessor_version_id = s.base_version_id)
          OR EXISTS (SELECT 1 FROM training_correction_admissions p WHERE p.stream_id = s.stream_id
            AND p.correction_id = NEW.predecessor_version_id AND p.revision_sequence + 1 = NEW.revision_sequence
            AND p.registered_at_utc <= NEW.registered_at_utc AND p.source_available_at_utc <= NEW.source_available_at_utc)))
        OR EXISTS (SELECT 1 FROM training_correction_admissions p WHERE p.stream_id = NEW.stream_id AND p.revision_sequence >= NEW.revision_sequence)
        """,
        "correction requires the actual predecessor head; no fork or cycle",
    )
    candidate_path = "$.content_payload.intent.candidate"
    guard(
        "training_correction_admissions",
        "logical_key_insert",
        "INSERT",
        f"""
        EXISTS (SELECT 1 FROM training_correction_streams owner
          JOIN training_correction_streams current ON current.stream_id = NEW.stream_id
          JOIN training_correction_admissions a ON a.stream_id = owner.stream_id
          WHERE owner.provider_id = current.provider_id AND owner.stream_id <> current.stream_id
            AND json_extract(a.artifact_json, '{candidate_path}.provider_result_key') =
                json_extract(NEW.artifact_json, '{candidate_path}.provider_result_key'))
        OR EXISTS (SELECT 1 FROM match_results r
          JOIN training_correction_streams current ON current.stream_id = NEW.stream_id
          WHERE r.provider_id = current.provider_id AND r.internal_match_id <> current.internal_match_id
            AND r.source_result_key = json_extract(NEW.artifact_json, '{candidate_path}.provider_result_key'))
        """,
        "provider logical result key is owned by a different match stream",
    )
    guard(
        "training_fact_admissions",
        "controlled_head_insert",
        "INSERT",
        """
        EXISTS (SELECT 1 FROM training_fact_bindings b
          JOIN provider_match_mappings m ON m.mapping_id = b.provider_mapping_id
          JOIN training_correction_streams s ON s.provider_id = m.provider_id AND s.internal_match_id = b.internal_match_id
          JOIN training_correction_admissions a ON a.stream_id = s.stream_id
          WHERE b.training_fact_admission_id = NEW.training_fact_admission_id)
        """,
        "new V1 admission cannot reuse a corrected stream root",
    )
    guard(
        "training_correction_admissions",
        "revision_order_insert",
        "INSERT",
        f"""
        json_extract(NEW.artifact_json, '{candidate_path}.provider_revision_id') IS NOT NEW.provider_revision_id
        OR json_extract(NEW.artifact_json, '{candidate_path}.provider_revision_order') IS NOT NEW.provider_revision_order
        OR (NEW.provider_revision_id IS NULL) <> (NEW.provider_revision_order IS NULL)
        OR EXISTS (SELECT 1 FROM training_correction_streams s
          LEFT JOIN training_correction_admissions p ON p.correction_id = NEW.predecessor_version_id
          WHERE s.stream_id = NEW.stream_id AND (
            (json_extract(NEW.artifact_json, '{candidate_path}.result_source_available_at_utc') =
             COALESCE(json_extract(p.artifact_json, '{candidate_path}.result_source_available_at_utc'),
                      json_extract(s.artifact_json, '$.snapshot.result_source_available_at_utc'))
             AND (NEW.provider_revision_order IS NULL OR NEW.provider_revision_order <= COALESCE(p.provider_revision_order, 0)))
            OR (p.provider_revision_order IS NOT NULL AND (NEW.provider_revision_order IS NULL OR NEW.provider_revision_order <= p.provider_revision_order))))
        OR EXISTS (SELECT 1 FROM training_correction_admissions established
          WHERE established.stream_id = NEW.stream_id AND established.provider_revision_order IS NOT NULL
            AND (NEW.provider_revision_order IS NULL OR NEW.provider_revision_order <= established.provider_revision_order))
        """,
        "equal source time requires an explicit advancing provider revision order",
    )
    guard(
        "training_correction_admissions",
        "complete_insert",
        "INSERT",
        """
        (SELECT COUNT(*) FROM training_correction_components c WHERE c.correction_id = NEW.correction_id) <> 5
        OR json_extract(NEW.artifact_json, '$.artifact_id') IS NOT NEW.correction_id
        OR json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.content_hash
        OR json_extract(NEW.artifact_json, '$.content_payload.intent.predecessor.artifact_id') IS NOT NEW.predecessor_version_id
        OR json_extract(NEW.artifact_json, '$.content_payload.intent.revision_sequence') IS NOT NEW.revision_sequence
        OR EXISTS (SELECT 1 FROM training_correction_components c WHERE c.correction_id = NEW.correction_id
          AND NOT EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.content_payload.components') j
            WHERE json_extract(j.value, '$.component') = c.component AND json(j.value) = json(c.artifact_json)))
        OR (json_type(NEW.artifact_json, '$.content_payload.normalized_result') = 'object') <>
          EXISTS (SELECT 1 FROM training_correction_result_bindings b JOIN match_results r ON r.match_result_id = b.match_result_id
            WHERE b.correction_id = NEW.correction_id AND b.stream_id = NEW.stream_id
            AND json_extract(NEW.artifact_json, '$.content_payload.normalized_result.match_result_id') = r.match_result_id
            AND json_extract(NEW.artifact_json, '$.content_payload.normalized_result') = json(b.artifact_json)
            AND b.previous_match_result_id = r.supersedes_match_result_id
            AND b.provider_id = r.provider_id AND b.internal_match_id = r.internal_match_id
            AND b.provider_mapping_id = r.provider_mapping_id AND b.home_goals = r.home_goals AND b.away_goals = r.away_goals
            AND b.observed_at_utc = r.observed_at_utc AND b.available_at_utc = r.available_at_utc
            AND b.ingested_at_utc = r.ingested_at_utc AND b.source_result_key = r.source_result_key AND b.payload_hash = r.payload_hash)
        """,
        "correction requires complete exact component and normalized result bindings",
    )

    # A child authorization is useful only in the transaction that seals its
    # parent (deferred FK). Ordinary writers have no such child and fail closed.
    equalities = " AND ".join(
        f"b.{x} = NEW.{x}"
        for x in (
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
        )
    )
    authorized = f"""EXISTS (SELECT 1 FROM training_correction_result_bindings b
        JOIN training_correction_streams s ON s.stream_id = b.stream_id
        WHERE {equalities} AND b.previous_match_result_id = NEW.supersedes_match_result_id
        AND s.provider_id = NEW.provider_id AND s.internal_match_id = NEW.internal_match_id
        AND NOT EXISTS (SELECT 1 FROM training_correction_admissions a WHERE a.correction_id = b.correction_id))"""
    admitted = """EXISTS (SELECT 1 FROM training_fact_bindings b JOIN provider_match_mappings m ON m.mapping_id = b.provider_mapping_id
        WHERE m.provider_id = NEW.provider_id AND b.internal_match_id = NEW.internal_match_id)"""
    guard(
        "match_results",
        "controlled_stream_insert",
        "INSERT",
        f"({admitted}) AND NOT ({authorized})",
        "admitted result stream requires a controlled correction transaction",
    )
    guard(
        "match_results",
        "logical_key_insert",
        "INSERT",
        f"""
        EXISTS (SELECT 1 FROM training_correction_admissions a
          JOIN training_correction_streams s ON s.stream_id = a.stream_id
          WHERE s.provider_id = NEW.provider_id AND s.internal_match_id <> NEW.internal_match_id
            AND json_extract(a.artifact_json, '{candidate_path}.provider_result_key') = NEW.source_result_key)
        """,
        "provider logical result key is owned by a different match stream",
    )
    guard(
        "match_results",
        "immutable_insert_existing",
        "INSERT",
        f"""
        EXISTS (SELECT 1 FROM match_results r WHERE r.match_result_id = NEW.match_result_id
          OR r.supersedes_match_result_id = NEW.supersedes_match_result_id)
        OR (EXISTS (SELECT 1 FROM match_results r WHERE r.provider_id = NEW.provider_id AND r.source_result_key = NEW.source_result_key) AND NOT ({authorized}))
        OR EXISTS (SELECT 1 FROM match_results r WHERE r.provider_id = NEW.provider_id
          AND r.source_result_key = NEW.source_result_key AND r.internal_match_id <> NEW.internal_match_id)
        """,
        "immutable result key or predecessor conflict",
    )
    guard(
        "match_results",
        "supersession_insert",
        "INSERT",
        f"""
        NEW.supersedes_match_result_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM match_results p WHERE p.match_result_id = NEW.supersedes_match_result_id
          AND p.provider_id = NEW.provider_id AND p.internal_match_id = NEW.internal_match_id
          AND p.available_at_utc <= NEW.available_at_utc
          AND (p.ingested_at_utc < NEW.ingested_at_utc OR (p.ingested_at_utc = NEW.ingested_at_utc AND ({authorized}))))
        """,
        "match result supersession requires verified provider/match chronology",
    )
    return result


def _checksum(rows):
    return hashlib.sha256(
        json.dumps(
            [tuple(r) for r in rows], separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def upgrade_training_corrections_v2(connection):
    """Caller must enter with foreign_keys OFF and no SQLite transaction.

    Never use a rename-old-table strategy: that rewrites downstream FK targets.
    Copy/drop/rename is protected by an EXCLUSIVE transaction, a byte-value
    checksum, and foreign_key_check before commit. All original indices and
    non-replaced guards are restored verbatim.
    """
    if connection.dialect.name != "sqlite":
        raise RuntimeError("controlled corrections support SQLite only")
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "correction rebuild requires foreign_keys OFF outside a transaction"
        )
    connection.exec_driver_sql("BEGIN EXCLUSIVE")
    try:
        if connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE name = 'training_correction_schema_versions'"
        ).scalar():
            raise RuntimeError("controlled correction schema is already installed")
        original = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='match_results'"
        ).scalar_one()
        before = connection.exec_driver_sql(
            "SELECT * FROM match_results ORDER BY match_result_id"
        ).all()
        objects = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE tbl_name='match_results' AND type IN ('index','trigger') AND sql IS NOT NULL"
        ).all()
        rebuilt, count = re.subn(
            r",?\s*CONSTRAINT uq_match_result_source_key UNIQUE\s*\(provider_id, source_result_key\)\s*,?",
            ",",
            original,
        )
        if count != 1:
            raise RuntimeError(
                "unrecognized normalized result schema; refusing lossy rebuild"
            )
        rebuilt = re.sub(r",\s*\)", ")", rebuilt)
        rebuilt = re.sub(
            r'CREATE TABLE "?match_results"?',
            "CREATE TABLE match_results_correction_v2",
            rebuilt,
            count=1,
        )
        connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
        connection.exec_driver_sql(rebuilt)
        connection.exec_driver_sql(
            "INSERT INTO match_results_correction_v2 SELECT * FROM match_results"
        )
        connection.exec_driver_sql("DROP TABLE match_results")
        connection.exec_driver_sql(
            "ALTER TABLE match_results_correction_v2 RENAME TO match_results"
        )
        replaced = {
            "trg_match_results_immutable_insert_existing",
            "trg_match_results_supersession_insert",
        }
        for name, sql in objects:
            if name not in replaced:
                connection.exec_driver_sql(sql)
        connection.exec_driver_sql(
            "CREATE INDEX ix_match_results_provider_source_key_versions "
            "ON match_results (provider_id, source_result_key)"
        )
        tables = training_correction_tables_v2(
            sa.MetaData(), sa.DateTime(timezone=True)
        )
        for table in tables.values():
            connection.execute(CreateTable(table))
        for sql in training_correction_trigger_sql_v2().values():
            connection.exec_driver_sql(sql)
        after = connection.exec_driver_sql(
            "SELECT * FROM match_results ORDER BY match_result_id"
        ).all()
        if before != after or _checksum(before) != _checksum(after):
            raise RuntimeError("normalized result rebuild checksum mismatch")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
            raise RuntimeError("normalized result rebuild damaged foreign key lineage")
        digest = _checksum(before)
        document = json.dumps(
            {
                "version": 2,
                "preserved_result_count": len(before),
                "preserved_results_sha256": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            tables[CORRECTION_TABLES[0]]
            .insert()
            .values(
                version=2,
                preserved_result_count=len(before),
                preserved_results_sha256=digest,
                original_result_table_sql=original,
                artifact_json=document,
                row_sha256=hashlib.sha256(document.encode()).hexdigest(),
            )
        )
        connection.exec_driver_sql("COMMIT")
    except BaseException:
        connection.exec_driver_sql("ROLLBACK")
        raise
    finally:
        connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")


def install_training_correction_schema(engine):
    """Legacy table rebuild used by create_schema before registering new tables."""
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            upgrade_training_corrections_v2(connection)
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def initialize_training_correction_schema_v2(connection):
    """Record a fresh runtime schema once; never replace a migration receipt."""
    if connection.exec_driver_sql(
        "SELECT 1 FROM training_correction_schema_versions WHERE version = 2"
    ).scalar():
        return
    rows = connection.exec_driver_sql(
        "SELECT * FROM match_results ORDER BY match_result_id"
    ).all()
    digest = _checksum(rows)
    document = json.dumps(
        {
            "version": 2,
            "preserved_result_count": len(rows),
            "preserved_results_sha256": digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    original = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='match_results'"
    ).scalar_one()
    connection.execute(
        sa.text(
            "INSERT INTO training_correction_schema_versions "
            "(version, preserved_result_count, preserved_results_sha256, original_result_table_sql, artifact_json, row_sha256) "
            "VALUES (2, :count, :digest, :original, :document, :checksum)"
        ),
        dict(
            count=len(rows),
            digest=digest,
            original=original,
            document=document,
            checksum=hashlib.sha256(document.encode()).hexdigest(),
        ),
    )


def install_training_correction_v2_triggers(connection):
    """Override only versioned guard names after installing the frozen guards."""
    for name, statement in training_correction_trigger_sql_v2().items():
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        connection.exec_driver_sql(statement)


def downgrade_training_corrections_v2(connection):
    """Restore V1 constraints only when no controlled lineage has been written."""
    if (
        connection.dialect.name != "sqlite"
        or connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
    ):
        raise RuntimeError(
            "correction downgrade requires SQLite foreign_keys OFF outside a transaction"
        )
    connection.exec_driver_sql("BEGIN EXCLUSIVE")
    try:
        if any(
            connection.exec_driver_sql(
                f"SELECT EXISTS (SELECT 1 FROM {table})"
            ).scalar()
            for table in CORRECTION_TABLES[1:]
        ):
            raise RuntimeError(
                "cannot downgrade while immutable correction lineage exists"
            )
        before = connection.exec_driver_sql(
            "SELECT * FROM match_results ORDER BY match_result_id"
        ).all()
        if connection.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM match_results r JOIN match_results p "
            "ON p.match_result_id = r.supersedes_match_result_id "
            "WHERE p.provider_id <> r.provider_id OR p.internal_match_id <> r.internal_match_id "
            "OR p.available_at_utc > r.available_at_utc OR p.ingested_at_utc >= r.ingested_at_utc)"
        ).scalar():
            raise RuntimeError("cannot restore noncontrolled V1 result chronology")
        original = connection.exec_driver_sql(
            "SELECT original_result_table_sql FROM training_correction_schema_versions WHERE version = 2"
        ).scalar_one()
        if "uq_match_result_source_key" not in original:
            # Fresh runtime databases started at V2 rather than being rebuilt.
            original = (
                original.rstrip()[:-1]
                + ", CONSTRAINT uq_match_result_source_key UNIQUE (provider_id, source_result_key))"
            )
        legacy, count = re.subn(
            r'CREATE TABLE "?match_results"?',
            "CREATE TABLE match_results_correction_downgrade",
            original,
            count=1,
        )
        if count != 1:
            raise RuntimeError("unrecognized original result table declaration")
        objects = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE tbl_name='match_results' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL"
        ).all()
        for name in training_correction_trigger_sql_v2():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        for table in reversed(CORRECTION_TABLES):
            connection.exec_driver_sql(f"DROP TABLE {table}")
        connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
        connection.exec_driver_sql(legacy)
        connection.exec_driver_sql(
            "INSERT INTO match_results_correction_downgrade SELECT * FROM match_results"
        )
        connection.exec_driver_sql("DROP TABLE match_results")
        connection.exec_driver_sql(
            "ALTER TABLE match_results_correction_downgrade RENAME TO match_results"
        )
        replaced = set(training_correction_trigger_sql_v2()) | {
            "ix_match_results_provider_source_key_versions"
        }
        for name, sql in objects:
            if name not in replaced:
                connection.exec_driver_sql(sql)
        from football_system.infrastructure.database.immutability import (
            IMMUTABLE_INSERT_KEYS,
            _install_historical_lineage_triggers,
        )

        _install_historical_lineage_triggers(connection)
        conflict = " OR ".join(
            "(" + " AND ".join(f"existing.{key} = NEW.{key}" for key in keys) + ")"
            for keys in IMMUTABLE_INSERT_KEYS["match_results"]
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_match_results_immutable_insert_existing BEFORE INSERT ON match_results "
            f"WHEN EXISTS (SELECT 1 FROM match_results existing WHERE {conflict}) "
            "BEGIN SELECT RAISE(ABORT, 'immutable record already exists'); END"
        )
        after = connection.exec_driver_sql(
            "SELECT * FROM match_results ORDER BY match_result_id"
        ).all()
        if before != after or _checksum(before) != _checksum(after):
            raise RuntimeError("normalized result downgrade checksum mismatch")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
            raise RuntimeError(
                "normalized result downgrade damaged foreign key lineage"
            )
        connection.exec_driver_sql("COMMIT")
    except BaseException:
        connection.exec_driver_sql("ROLLBACK")
        raise
    finally:
        connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
