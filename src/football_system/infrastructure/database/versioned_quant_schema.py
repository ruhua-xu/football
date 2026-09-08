"""Additive f51e294b0687 projection. Never changes correction-owned tables.

V1 fact/source FKs remain intact. V2 selected facts reference a closed version
context whose roots retain the original composite binding FK and whose revisions
have an explicit correction-admission FK, not arbitrary polymorphic identifiers.
"""

import sqlalchemy as sa

VERSIONED_QUANT_TABLES = (
    "training_history_version_context",
    "training_history_version_facts",
    "production_quant_model_release_version_facts",
)


def versioned_quant_tables_v2(metadata):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    for name, keys in {
        "training_history_manifests": ("manifest_id",),
        "training_history_admissions": ("manifest_id", "training_fact_admission_id"),
        "training_history_seasons": ("manifest_id", "season_sequence"),
        "training_fact_bindings": (
            "training_fact_admission_id",
            "training_fact_binding_id",
        ),
        "training_correction_admissions": ("correction_id",),
        "production_quant_model_releases": ("release_id",),
        "match_results": ("match_result_id",),
        "matches": ("internal_match_id",),
    }.items():
        if name not in metadata.tables:
            sa.Table(name, metadata, *(c(key, primary_key=True) for key in keys))

    def fk(names, table, targets=None):
        return sa.ForeignKeyConstraint(
            names,
            [f"{table}.{key}" for key in (targets or names)],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )

    def table(name, *columns):
        return sa.Table(
            name,
            metadata,
            *columns,
            c("artifact_json", sa.Text()),
            c("row_sha256", sa.String(64)),
            sa.CheckConstraint(
                "json_valid(artifact_json) AND json_type(artifact_json) = 'object'"
            ),
            sa.CheckConstraint(
                "length(row_sha256) = 64 AND row_sha256 NOT GLOB '*[^0-9a-f]*'"
            ),
        )

    context = table(
        VERSIONED_QUANT_TABLES[0],
        c("manifest_id", primary_key=True),
        c("version_id", primary_key=True),
        c("context_sequence", sa.Integer()),
        c("revision_sequence", sa.Integer()),
        c("base_admission_id"),
        c("base_binding_id"),
        c("internal_match_id"),
        c("correction_id", nullable=True),
        c("predecessor_version_id", nullable=True),
        c("selected", sa.Boolean()),
        sa.UniqueConstraint("manifest_id", "context_sequence"),
        sa.UniqueConstraint("manifest_id", "version_id", "internal_match_id"),
        sa.UniqueConstraint("manifest_id", "predecessor_version_id"),
        sa.CheckConstraint(
            "context_sequence >= 0 AND revision_sequence >= 0 AND selected IN (0,1)"
        ),
        sa.CheckConstraint(
            "(revision_sequence = 0 AND correction_id IS NULL AND predecessor_version_id IS NULL) OR (revision_sequence > 0 AND correction_id IS NOT NULL AND correction_id = version_id AND predecessor_version_id IS NOT NULL)"
        ),
        fk(["manifest_id"], "training_history_manifests"),
        fk(
            ["manifest_id", "base_admission_id"],
            "training_history_admissions",
            ["manifest_id", "training_fact_admission_id"],
        ),
        fk(
            ["base_admission_id", "base_binding_id"],
            "training_fact_bindings",
            ["training_fact_admission_id", "training_fact_binding_id"],
        ),
        fk(["correction_id"], "training_correction_admissions"),
        fk(["internal_match_id"], "matches"),
        fk(
            ["manifest_id", "predecessor_version_id"],
            VERSIONED_QUANT_TABLES[0],
            ["manifest_id", "version_id"],
        ),
    )
    facts = table(
        VERSIONED_QUANT_TABLES[1],
        c("manifest_id", primary_key=True),
        c("fact_sequence", sa.Integer(), primary_key=True),
        c("version_id"),
        c("season_sequence", sa.Integer()),
        c("season_id"),
        c("internal_match_id"),
        c("match_result_id"),
        c("elo_fact_hash", sa.String(64)),
        c("approved_fact_hash", sa.String(64)),
        sa.UniqueConstraint("manifest_id", "internal_match_id"),
        sa.UniqueConstraint("manifest_id", "match_result_id"),
        sa.UniqueConstraint(
            "manifest_id",
            "fact_sequence",
            "match_result_id",
            "elo_fact_hash",
            "approved_fact_hash",
        ),
        sa.CheckConstraint("fact_sequence >= 0"),
        fk(["manifest_id"], "training_history_manifests"),
        fk(
            ["manifest_id", "version_id", "internal_match_id"],
            VERSIONED_QUANT_TABLES[0],
        ),
        fk(["manifest_id", "season_sequence"], "training_history_seasons"),
        fk(["match_result_id"], "match_results"),
    )
    released = table(
        VERSIONED_QUANT_TABLES[2],
        c("release_id", primary_key=True),
        c("fact_sequence", sa.Integer(), primary_key=True),
        c("manifest_id"),
        c("match_result_id"),
        c("elo_fact_hash", sa.String(64)),
        c("approved_fact_hash", sa.String(64)),
        fk(["release_id"], "production_quant_model_releases"),
        fk(
            [
                "manifest_id",
                "fact_sequence",
                "match_result_id",
                "elo_fact_hash",
                "approved_fact_hash",
            ],
            VERSIONED_QUANT_TABLES[1],
        ),
    )
    return {t.name: t for t in (context, facts, released)}


def versioned_quant_trigger_sql_v2():
    from football_system.infrastructure.database.production_quant_schema import (
        production_quant_trigger_sql_v1,
    )
    from football_system.infrastructure.database.production_inference_schema import (
        production_inference_trigger_sql_v1,
    )
    from football_system.infrastructure.database.production_audit_schema import (
        production_audit_trigger_sql_v1,
    )

    result = {}
    # Extend companion guards, not normalized/V3 tables or the old typed FKs.
    for name, sql in {
        **production_inference_trigger_sql_v1(),
        **production_audit_trigger_sql_v1(),
    }.items():
        updated = sql.replace(
            "FROM production_quant_model_release_facts rf",
            "FROM (SELECT release_id, fact_sequence, match_result_id, elo_fact_hash FROM production_quant_model_release_facts UNION ALL SELECT release_id, fact_sequence, match_result_id, elo_fact_hash FROM production_quant_model_release_version_facts) rf",
        )
        for alias in ("a", "admission"):
            updated = updated.replace(
                f"JOIN match_result_admissions {alias} ON",
                f"JOIN (SELECT match_result_id FROM match_result_admissions UNION SELECT match_result_id FROM training_correction_result_bindings) {alias} ON",
            )
        if updated != sql:
            result[name] = updated
    for name, path in (
        (
            "trg_training_history_manifests_complete_insert",
            "$.content_payload.history.schema_version",
        ),
        (
            "trg_production_quant_model_releases_complete_insert",
            "$.training_manifest.content_payload.history.schema_version",
        ),
    ):
        old = production_quant_trigger_sql_v1()[name]
        # Preserve the original guard verbatim for original history graphs.
        prefix, rest = old.split("WHEN ", 1)
        condition, body = rest.split(" BEGIN ", 1)
        result[name] = (
            prefix
            + f"WHEN json_extract(NEW.artifact_json, '{path}') IS NOT 'TRAINING_HISTORY_GRAPH_V2' AND ("
            + condition
            + ") BEGIN "
            + body
        )

    def guard(table, suffix, action, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} "
            + (f"WHEN {condition} " if condition else "")
            + f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    tables = versioned_quant_tables_v2(sa.MetaData())
    for name, table in tables.items():
        keys = [tuple(c.name for c in table.primary_key.columns)] + [
            tuple(c.name for c in k.columns)
            for k in table.constraints
            if isinstance(k, sa.UniqueConstraint)
        ]
        conflict = " OR ".join(
            "(" + " AND ".join(f"old.{k}=NEW.{k}" for k in key) + ")"
            for key in sorted(set(keys))
        )
        guard(
            name,
            "immutable_insert",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {name} old WHERE {conflict})",
            "immutable versioned history already exists",
        )
        for action in ("UPDATE", "DELETE"):
            guard(
                name,
                "append_only_" + action.lower(),
                action,
                "",
                "versioned history is append-only",
            )
        parent, key = (
            ("production_quant_model_releases", "release_id")
            if name == VERSIONED_QUANT_TABLES[2]
            else ("training_history_manifests", "manifest_id")
        )
        guard(
            name,
            "sealed_insert",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {parent} p WHERE p.{key}=NEW.{key})",
            "sealed graph cannot gain version children",
        )
    guard(
        VERSIONED_QUANT_TABLES[0],
        "lineage_insert",
        "INSERT",
        " OR ".join(
            (
                "json_extract(NEW.artifact_json, '$.reference.artifact_id') IS NOT NEW.version_id",
                "json_extract(NEW.artifact_json, '$.base_binding.artifact_id') IS NOT NEW.base_binding_id",
                "json_extract(NEW.artifact_json, '$.base_admission.artifact_id') IS NOT NEW.base_admission_id",
                "json_extract(NEW.artifact_json, '$.base_binding.schema_version') IS NOT 'TRAINING_FACT_BINDING_V1'",
                "json_extract(NEW.artifact_json, '$.base_admission.schema_version') IS NOT 'TRAINING_FACT_ADMISSION_V1'",
                "json_extract(NEW.artifact_json, '$.reference.schema_version') IS NOT CASE WHEN NEW.revision_sequence=0 THEN 'TRAINING_BASE_FACT_VERSION_V2' ELSE 'TRAINING_CORRECTION_ADMISSION_V2' END",
                "json_extract(NEW.artifact_json, '$.revision_sequence') IS NOT NEW.revision_sequence",
                "json_extract(NEW.artifact_json, '$.predecessor.artifact_id') IS NOT NEW.predecessor_version_id",
                "json_extract(NEW.artifact_json, '$.snapshot.stream.internal_match_id') IS NOT NEW.internal_match_id",
                "NEW.revision_sequence > 0 AND NEW.correction_id IS NOT NEW.version_id",
                "NOT EXISTS (SELECT 1 FROM training_fact_bindings b WHERE b.training_fact_admission_id=NEW.base_admission_id AND b.training_fact_binding_id=NEW.base_binding_id AND b.internal_match_id=NEW.internal_match_id AND json_extract(b.artifact_json, '$.fact_hash')=json_extract(NEW.artifact_json, '$.base_binding.content_hash') AND (NEW.revision_sequence>0 OR json_extract(NEW.artifact_json, '$.normalized_result')=json_extract(b.artifact_json, '$.content_payload.normalized_result')))",
                "NEW.revision_sequence > 0 AND NOT EXISTS (SELECT 1 FROM training_correction_admissions a JOIN training_correction_streams s ON s.stream_id=a.stream_id JOIN training_history_version_context p ON p.manifest_id=NEW.manifest_id AND p.version_id=NEW.predecessor_version_id WHERE a.correction_id=NEW.correction_id AND a.content_hash=json_extract(NEW.artifact_json, '$.reference.content_hash') AND a.revision_sequence=NEW.revision_sequence AND a.predecessor_version_id=p.version_id AND p.revision_sequence+1=NEW.revision_sequence AND p.base_binding_id=NEW.base_binding_id AND p.base_admission_id=NEW.base_admission_id AND s.base_admission_id=NEW.base_admission_id AND s.base_binding_id=NEW.base_binding_id AND s.internal_match_id=NEW.internal_match_id)",
                "NEW.revision_sequence > 0 AND NOT EXISTS (SELECT 1 FROM training_correction_admissions a WHERE a.correction_id=NEW.correction_id AND json_extract(NEW.artifact_json, '$.snapshot')=json_extract(a.artifact_json, '$.content_payload.intent.candidate') AND json_extract(NEW.artifact_json, '$.components')=json_extract(a.artifact_json, '$.content_payload.components') AND json_extract(NEW.artifact_json, '$.normalized_result') IS json_extract(a.artifact_json, '$.content_payload.normalized_result'))",
            )
        ),
        "version context typed lineage mismatch",
    )
    guard(
        VERSIONED_QUANT_TABLES[1],
        "lineage_insert",
        "INSERT",
        " OR ".join(
            (
                "NOT EXISTS (SELECT 1 FROM training_history_version_context c WHERE c.manifest_id=NEW.manifest_id AND c.version_id=NEW.version_id AND c.internal_match_id=NEW.internal_match_id AND c.selected=1 AND json_extract(c.artifact_json, '$.normalized_result.match_result_id')=NEW.match_result_id AND c.artifact_json=json_extract(NEW.artifact_json, '$.content_payload.version'))",
                "NOT EXISTS (SELECT 1 FROM training_history_seasons s WHERE s.manifest_id=NEW.manifest_id AND s.season_sequence=NEW.season_sequence AND s.season_id=NEW.season_id)",
                "json_extract(NEW.artifact_json, '$.content_payload.version.reference.artifact_id') IS NOT NEW.version_id",
                "json_extract(NEW.artifact_json, '$.schema_version') IS NOT 'APPROVED_TRAINING_FACT_V2'",
                "json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.approved_fact_hash",
                *(
                    f"json_extract(NEW.artifact_json, '$.content_payload.{path}') IS NOT NEW.{column}"
                    for path, column in (
                        ("fact_sequence", "fact_sequence"),
                        ("season_sequence", "season_sequence"),
                        ("elo_fact.fact_hash", "elo_fact_hash"),
                        ("elo_fact.match_id", "internal_match_id"),
                        ("elo_fact.match_result_id", "match_result_id"),
                        ("elo_fact.season_id", "season_id"),
                    )
                ),
            )
        ),
        "selected version fact lineage mismatch",
    )

    def count(table, key, amount):
        return f"(SELECT COUNT(*) FROM {table} WHERE {key}=NEW.{key}) IS NOT {amount}"

    graph = "$.content_payload.history"
    checks = [
        count("training_history_admissions", "manifest_id", "NEW.admission_count"),
        count("training_history_sources", "manifest_id", "NEW.source_count"),
        count("training_history_seasons", "manifest_id", "NEW.season_count"),
        count(
            VERSIONED_QUANT_TABLES[0],
            "manifest_id",
            f"json_array_length(NEW.artifact_json, '{graph}.correction_context.versions')",
        ),
        count(VERSIONED_QUANT_TABLES[1], "manifest_id", "NEW.fact_count"),
        *(
            count(table, "manifest_id", "0")
            for table in (
                "training_history_facts",
                "training_history_fixture_sources",
                "training_history_mapping_sources",
                "training_history_result_sources",
            )
        ),
        "json_extract(NEW.artifact_json, '$.artifact_id') IS NOT NEW.manifest_id",
        "json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.manifest_hash",
        f"json_array_length(NEW.artifact_json, '{graph}.facts') IS NOT NEW.fact_count",
        f"EXISTS (SELECT 1 FROM {VERSIONED_QUANT_TABLES[0]} c WHERE c.manifest_id=NEW.manifest_id AND (json_extract(NEW.artifact_json, '{graph}.correction_context.versions[' || c.context_sequence || ']') IS NOT c.artifact_json OR c.selected IS NOT EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '{graph}.selected_heads') h WHERE json_extract(h.value, '$.version')=json_extract(c.artifact_json, '$.reference') AND json_extract(h.value, '$.match_id')=c.internal_match_id AND json_extract(h.value, '$.match_result_id') IS json_extract(c.artifact_json, '$.normalized_result.match_result_id'))))",
        f"(SELECT COUNT(*) FROM {VERSIONED_QUANT_TABLES[0]} c WHERE c.manifest_id=NEW.manifest_id AND c.selected=1) IS NOT json_array_length(NEW.artifact_json, '{graph}.selected_heads')",
        f"EXISTS (SELECT 1 FROM {VERSIONED_QUANT_TABLES[0]} c WHERE c.manifest_id=NEW.manifest_id AND c.selected=1 GROUP BY c.internal_match_id HAVING COUNT(*)>1)",
        f"EXISTS (SELECT 1 FROM {VERSIONED_QUANT_TABLES[1]} f WHERE f.manifest_id=NEW.manifest_id AND json_extract(NEW.artifact_json, '{graph}.facts[' || f.fact_sequence || ']') IS NOT f.artifact_json)",
    ]
    guard(
        "training_history_manifests",
        "versioned_complete_insert",
        "INSERT",
        f"json_extract(NEW.artifact_json, '{graph}.schema_version')='TRAINING_HISTORY_GRAPH_V2' AND ("
        + " OR ".join(checks)
        + ")",
        "versioned manifest requires exact complete children",
    )
    checks = [
        count(VERSIONED_QUANT_TABLES[2], "release_id", "NEW.fact_count"),
        count("production_quant_model_release_facts", "release_id", "0"),
        "NOT EXISTS (SELECT 1 FROM training_history_approval_events a JOIN training_history_manifests m ON m.manifest_id=a.manifest_id WHERE a.approval_id=NEW.approval_id AND a.manifest_id=NEW.manifest_id AND a.persisted_at_utc <= NEW.training_cutoff_at_utc AND m.fact_count=NEW.fact_count)",
        f"EXISTS (SELECT 1 FROM {VERSIONED_QUANT_TABLES[2]} f WHERE f.release_id=NEW.release_id AND (f.manifest_id <> NEW.manifest_id OR json_extract(NEW.artifact_json, '$.content_payload.release_facts[' || f.fact_sequence || ']') IS NOT f.artifact_json OR NOT EXISTS (SELECT 1 FROM {VERSIONED_QUANT_TABLES[1]} m WHERE m.manifest_id=NEW.manifest_id AND m.fact_sequence=f.fact_sequence AND m.artifact_json=f.artifact_json)))",
        "json_extract(NEW.artifact_json, '$.content_payload.released_state_core.content_hash') IS NOT NEW.released_state_core_hash",
        "NOT EXISTS (SELECT 1 FROM training_history_manifests m WHERE m.manifest_id=NEW.manifest_id AND m.artifact_json=json_extract(NEW.artifact_json, '$.training_manifest'))",
    ]
    guard(
        "production_quant_model_releases",
        "versioned_complete_insert",
        "INSERT",
        "json_extract(NEW.artifact_json, '$.training_manifest.content_payload.history.schema_version')='TRAINING_HISTORY_GRAPH_V2' AND ("
        + " OR ".join(checks)
        + ")",
        "versioned release requires exact complete facts",
    )
    for table, path, key, children in (
        (
            "training_history_manifests",
            graph,
            "manifest_id",
            VERSIONED_QUANT_TABLES[:2],
        ),
        (
            "production_quant_model_releases",
            "$.training_manifest.content_payload.history",
            "release_id",
            VERSIONED_QUANT_TABLES[2:],
        ),
    ):
        schema = f"json_extract(NEW.artifact_json, '{path}.schema_version')"
        mixed = " OR ".join(count(child, key, "0") for child in children)
        guard(
            table,
            "versioned_schema_insert",
            "INSERT",
            f"({schema} IS NOT 'TRAINING_HISTORY_GRAPH_V1' AND {schema} IS NOT 'TRAINING_HISTORY_GRAPH_V2') OR ({schema}='TRAINING_HISTORY_GRAPH_V1' AND ({mixed}))",
            "history schema must match its typed fact graph",
        )
    guard(
        "production_target_acceptance_plans",
        "versioned_exclusion_insert",
        "INSERT",
        "EXISTS (SELECT 1 FROM production_target_acceptance_matches t JOIN production_quant_model_releases r ON r.release_id=NEW.release_id JOIN training_history_version_facts f ON f.manifest_id=r.manifest_id AND f.internal_match_id=t.internal_match_id WHERE t.plan_id=NEW.plan_id)",
        "target intersects versioned training history",
    )
    return result


def install_versioned_quant_schema(engine):
    """Maintenance installer; normal create_schema installs this graph automatically."""
    with engine.begin() as connection:
        install_versioned_quant_schema_in_connection(connection)


def install_versioned_quant_schema_in_connection(connection):
    tables = versioned_quant_tables_v2(sa.MetaData())
    if "training_correction_admissions" not in sa.inspect(connection).get_table_names():
        raise ValueError(
            "install controlled correction schema before versioned history"
        )
    for table in tables.values():
        table.create(connection, checkfirst=True)
    for name, sql in versioned_quant_trigger_sql_v2().items():
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        connection.exec_driver_sql(sql)


def admitted_training_sql_v2(run_id):
    from football_system.infrastructure.database.production_audit_schema import (
        admitted_training_sql_v1,
    )

    return admitted_training_sql_v1(run_id).replace(
        "JOIN match_result_admissions a ON",
        "JOIN (SELECT match_result_id FROM match_result_admissions UNION SELECT match_result_id FROM training_correction_result_bindings) a ON",
    )
