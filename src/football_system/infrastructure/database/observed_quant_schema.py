"""Additive observed bindings and versioned guards for revision 17304b6d28a9.

Existing envelopes, row columns and hash recipes are unchanged. New content
variants use typed children, never fictitious historical admission foreign keys.
"""

import sqlalchemy as sa

OBSERVED_QUANT_TABLES = (
    "observed_quant_plan_contexts",
    "observed_quant_plan_records",
    "training_history_observed_admissions",
    "training_history_observed_context",
    "training_history_observed_facts",
    "production_quant_model_release_observed_facts",
    "production_audit_observed_basis",
    "training_history_observed_technical",
    "production_observed_release_prefixes",
)
OBSERVED_GRAPH = "OBSERVED_TRAINING_HISTORY_GRAPH_V1"
PILOT = "production_quant_integrity_pilot_"


def observed_quant_tables_v1(metadata):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    parents = {
        PILOT + "plans": ("artifact_id",),
        PILOT + "summaries": ("artifact_id",),
        PILOT + "reports": ("artifact_id",),
        PILOT + "attestations": ("artifact_id",),
        "observed_collection_scopes": ("scope_id",),
        "observed_snapshot_admissions": ("admission_id",),
        "observed_snapshot_records": ("version_id",),
        "training_history_manifests": ("manifest_id",),
        "training_history_seasons": ("manifest_id", "season_sequence"),
        "production_quant_model_releases": ("release_id",),
        "production_audit_bundles": ("packet_id",),
        "match_results": ("match_result_id",),
        "matches": ("internal_match_id",),
    }
    for name, keys in parents.items():
        if name not in metadata.tables:
            sa.Table(name, metadata, *(c(k, primary_key=True) for k in keys))

    def fk(keys, table, targets=None):
        return sa.ForeignKeyConstraint(
            keys,
            [f"{table}.{k}" for k in (targets or keys)],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )

    def table(name, *items):
        return sa.Table(
            name,
            metadata,
            *items,
            c("artifact_json", sa.Text()),
            c("row_sha256", sa.String(64)),
            *(
                sa.CheckConstraint(
                    f"length({item.name})=64 AND {item.name} NOT GLOB '*[^0-9a-f]*'"
                )
                for item in items
                if isinstance(item, sa.Column) and item.name.endswith(("hash", "root"))
            ),
            sa.CheckConstraint(
                "json_valid(artifact_json) AND json_type(artifact_json)='object'"
            ),
            sa.CheckConstraint(
                "length(row_sha256)=64 AND row_sha256 NOT GLOB '*[^0-9a-f]*'"
            ),
        )

    header = table(
        OBSERVED_QUANT_TABLES[0],
        c("plan_id", primary_key=True),
        c("scope_id"),
        c("context_root", sa.String(64)),
        c("record_count", sa.Integer()),
        fk(["plan_id"], PILOT + "plans", ["artifact_id"]),
        fk(["scope_id"], "observed_collection_scopes"),
        sa.CheckConstraint("record_count > 0"),
    )
    contexts = []
    for name, key, parent in (
        (OBSERVED_QUANT_TABLES[1], "plan_id", OBSERVED_QUANT_TABLES[0]),
        (OBSERVED_QUANT_TABLES[3], "manifest_id", "training_history_manifests"),
    ):
        contexts.append(
            table(
                name,
                c(key, primary_key=True),
                c("version_id", primary_key=True),
                c("context_sequence", sa.Integer()),
                c("scope_id"),
                c("content_hash", sa.String(64)),
                c("selected", sa.Boolean()),
                c("internal_match_id"),
                fk([key], parent),
                fk(["version_id"], "observed_snapshot_records"),
                fk(["scope_id"], "observed_collection_scopes"),
                fk(["internal_match_id"], "matches"),
                sa.UniqueConstraint(key, "context_sequence"),
                sa.UniqueConstraint(key, "version_id", "internal_match_id"),
                sa.CheckConstraint("context_sequence >= 0 AND selected IN (0,1)"),
            )
        )
    admissions = table(
        OBSERVED_QUANT_TABLES[2],
        c("manifest_id", primary_key=True),
        c("admission_id", primary_key=True),
        c("admission_sequence", sa.Integer()),
        c("content_hash", sa.String(64)),
        fk(["manifest_id"], "training_history_manifests"),
        fk(["admission_id"], "observed_snapshot_admissions"),
        sa.UniqueConstraint("manifest_id", "admission_sequence"),
        sa.CheckConstraint("admission_sequence >= 0"),
    )
    facts = table(
        OBSERVED_QUANT_TABLES[4],
        c("manifest_id", primary_key=True),
        c("fact_sequence", sa.Integer(), primary_key=True),
        c("version_id"),
        c("season_sequence", sa.Integer()),
        c("season_id"),
        c("internal_match_id"),
        c("match_result_id"),
        c("elo_fact_hash", sa.String(64)),
        c("approved_fact_hash", sa.String(64)),
        fk(["manifest_id"], "training_history_manifests"),
        fk(
            ["manifest_id", "version_id", "internal_match_id"], OBSERVED_QUANT_TABLES[3]
        ),
        fk(["manifest_id", "season_sequence"], "training_history_seasons"),
        fk(["match_result_id"], "match_results"),
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
    )
    released = table(
        OBSERVED_QUANT_TABLES[5],
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
            OBSERVED_QUANT_TABLES[4],
        ),
    )
    audit = table(
        OBSERVED_QUANT_TABLES[6],
        c("packet_id", primary_key=True),
        c("release_id"),
        c("scope_id"),
        c("evidence_basis", sa.String(64)),
        c("context_root", sa.String(64)),
        fk(["packet_id"], "production_audit_bundles"),
        fk(["release_id"], "production_quant_model_releases"),
        fk(["scope_id"], "observed_collection_scopes"),
        sa.CheckConstraint("evidence_basis='CURRENT_SNAPSHOT_OBSERVED'"),
    )
    technical = table(
        OBSERVED_QUANT_TABLES[7],
        c("manifest_id", primary_key=True),
        *(c(role + "_id") for role in ("plan", "summary", "report", "attestation")),
        *(
            c(role + "_hash", sa.String(64))
            for role in ("plan", "summary", "report", "attestation")
        ),
        fk(["manifest_id"], "training_history_manifests"),
        *(
            fk([role + "_id"], PILOT + suffix, ["artifact_id"])
            for role, suffix in (
                ("plan", "plans"),
                ("summary", "summaries"),
                ("report", "reports"),
                ("attestation", "attestations"),
            )
        ),
    )
    prefixes = table(
        OBSERVED_QUANT_TABLES[8],
        c("release_id", primary_key=True),
        c("phase", primary_key=True),
        c("scope_id"),
        c("admission_id"),
        c("admission_hash", sa.String(64)),
        c("admission_high_watermark", sa.Integer()),
        fk(["release_id"], "production_quant_model_releases"),
        fk(["scope_id"], "observed_collection_scopes"),
        fk(["admission_id"], "observed_snapshot_admissions"),
        sa.CheckConstraint(
            "phase IN ('BUILD_START','BUILD_COMPLETION') AND admission_high_watermark >= 0"
        ),
    )
    tables = {
        t.name: t
        for t in (
            header,
            *contexts,
            admissions,
            facts,
            released,
            audit,
            technical,
            prefixes,
        )
    }
    return {name: tables[name] for name in OBSERVED_QUANT_TABLES}


OBSERVED_TABLES = observed_quant_tables_v1(sa.MetaData())


def _context_rows(key, parent_id, context, heads, name):
    from football_system.domain.archive import canonical_json
    from football_system.infrastructure.database.production_quant_repository import _row

    selected = {r.version_id for r in heads}
    for sequence, r in enumerate(context.records):
        yield (
            name,
            _row(
                name,
                **{key: parent_id},
                version_id=r.version_id,
                context_sequence=sequence,
                scope_id=context.scope.scope_id,
                content_hash=r.content_hash,
                selected=r.version_id in selected,
                internal_match_id=r.identity.internal_match_id,
                artifact_json=canonical_json(r),
            ),
        )


def observed_plan_children(plan):
    from football_system.domain.archive import canonical_json
    from football_system.infrastructure.database.production_quant_repository import _row

    d = plan.content_payload.definition
    context = d.observed_context
    name = OBSERVED_QUANT_TABLES[0]
    yield (
        name,
        _row(
            name,
            plan_id=plan.artifact_id,
            scope_id=context.scope.scope_id,
            context_root=context.base_root,
            record_count=len(context.records),
            artifact_json=canonical_json(context),
        ),
    )
    yield from _context_rows(
        "plan_id", plan.artifact_id, context, d.selected_heads, OBSERVED_QUANT_TABLES[1]
    )


def append_observed_plan_children(session, plan):
    for name, row in observed_plan_children(plan):
        session.execute(OBSERVED_TABLES[name].insert().values(**row))


def verify_observed_plan_children(session, plan):
    from football_system.infrastructure.database.production_quant_repository import (
        _verify_children,
    )

    if session is None:
        raise ValueError("observed plan requires an active typed graph read")
    _verify_children(
        session,
        "plan_id",
        plan.artifact_id,
        observed_plan_children(plan),
        OBSERVED_QUANT_TABLES[:2],
    )


def observed_manifest_children(manifest):
    from football_system.domain.archive import canonical_json
    from football_system.infrastructure.database.production_quant_repository import _row

    g, mid = manifest.content_payload.history, manifest.artifact_id
    evidence = manifest.content_payload.technical_evidence
    name = OBSERVED_QUANT_TABLES[7]
    yield (
        name,
        _row(
            name,
            manifest_id=mid,
            **{
                role + "_id": getattr(evidence, role).artifact_id
                for role in ("plan", "summary", "report", "attestation")
            },
            **{
                role + "_hash": getattr(evidence, role).content_hash
                for role in ("plan", "summary", "report", "attestation")
            },
            artifact_json=canonical_json(evidence),
        ),
    )
    for a in g.admissions:
        name = OBSERVED_QUANT_TABLES[2]
        yield (
            name,
            _row(
                name,
                manifest_id=mid,
                admission_id=a.admission_id,
                admission_sequence=a.admission_sequence,
                content_hash=a.content_hash,
                artifact_json=canonical_json(a),
            ),
        )
    yield from _context_rows(
        "manifest_id",
        mid,
        g.observed_context,
        g.selected_heads,
        OBSERVED_QUANT_TABLES[3],
    )
    for f in g.facts:
        c, name = f.content_payload, OBSERVED_QUANT_TABLES[4]
        yield (
            name,
            _row(
                name,
                manifest_id=mid,
                fact_sequence=c.fact_sequence,
                version_id=c.record.version_id,
                season_sequence=c.season_sequence,
                season_id=c.elo_fact.season_id,
                internal_match_id=c.elo_fact.match_id,
                match_result_id=c.elo_fact.match_result_id,
                elo_fact_hash=c.elo_fact.fact_hash,
                approved_fact_hash=f.content_hash,
                artifact_json=canonical_json(f),
            ),
        )


def observed_audit_row(audit, release):
    from football_system.domain.archive import canonical_json
    from football_system.infrastructure.database.production_quant_repository import _row

    c, g = audit.content_payload, release.training_manifest.content_payload.history
    value = dict(
        schema_version="OBSERVED_AUDIT_BASIS_V1",
        model_training_evidence_basis=c.model_training_evidence_basis,
        observed_context_root=g.observed_context.base_root,
        strict_walk_forward=c.technical_evidence.strict_walk_forward,
    )
    return _row(
        OBSERVED_QUANT_TABLES[6],
        packet_id=c.packet.artifact_id,
        release_id=release.artifact_id,
        scope_id=g.observed_context.scope.scope_id,
        evidence_basis=c.model_training_evidence_basis,
        context_root=g.observed_context.base_root,
        artifact_json=canonical_json(value),
    )


def observed_release_prefix_children(release):
    from football_system.domain.archive import canonical_json
    from football_system.infrastructure.database.production_quant_repository import _row

    for captured in (
        release.content_payload.build_start_authorization,
        release.content_payload.build_completion_authorization,
    ):
        content = captured.content_payload
        context = content.current.observed_context
        prefix = context.admission_prefix
        name = OBSERVED_QUANT_TABLES[8]
        yield (
            name,
            _row(
                name,
                release_id=release.artifact_id,
                phase=content.phase,
                scope_id=context.scope.artifact_id,
                admission_id=prefix.admission.artifact_id,
                admission_hash=prefix.admission.content_hash,
                admission_high_watermark=prefix.admission_high_watermark,
                artifact_json=canonical_json(prefix),
            ),
        )


def admitted_training_sql_v3(run_id):
    from football_system.infrastructure.database.versioned_quant_schema import (
        admitted_training_sql_v2,
    )

    return admitted_training_sql_v2(run_id).replace(
        "SELECT match_result_id FROM training_correction_result_bindings",
        "SELECT match_result_id FROM training_correction_result_bindings UNION SELECT match_result_id FROM observed_result_bindings",
    )


def assert_no_unbound_training_results_v3(session, result_ids):
    for name in ("training_correction_result_bindings", "observed_result_bindings"):
        if session.scalar(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": name},
        ):
            t = sa.table(name, sa.column("match_result_id"))
            if session.execute(
                sa.select(t.c.match_result_id).where(
                    t.c.match_result_id.in_(result_ids)
                )
            ).first():
                raise ValueError(
                    "admitted observed/corrected training results require a production release binding"
                )


def observed_quant_trigger_sql_v1():
    from football_system.infrastructure.database.versioned_quant_schema import (
        versioned_quant_trigger_sql_v2,
    )

    result = {}
    for name, sql in versioned_quant_trigger_sql_v2().items():
        updated = sql.replace(
            "SELECT match_result_id FROM training_correction_result_bindings",
            "SELECT match_result_id FROM training_correction_result_bindings UNION SELECT match_result_id FROM observed_result_bindings",
        )
        updated = updated.replace(
            "FROM production_quant_model_release_version_facts) rf",
            "FROM production_quant_model_release_version_facts UNION ALL SELECT release_id, fact_sequence, match_result_id, elo_fact_hash FROM production_quant_model_release_observed_facts) rf",
        )
        if name in (
            "trg_training_history_manifests_complete_insert",
            "trg_training_history_manifests_versioned_schema_insert",
            "trg_production_quant_model_releases_complete_insert",
            "trg_production_quant_model_releases_versioned_schema_insert",
        ):
            path = (
                "$.content_payload.history"
                if "manifests" in name
                else "$.training_manifest.content_payload.history"
            )
            prefix, rest = updated.split("WHEN ", 1)
            condition, body = rest.split(" BEGIN ", 1)
            updated = (
                prefix
                + f"WHEN json_extract(NEW.artifact_json, '{path}.schema_version') IS NOT '{OBSERVED_GRAPH}' AND ("
                + condition
                + ") BEGIN "
                + body
            )
        if updated != sql:
            result[name] = updated

    def guard(table, suffix, event, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER {name} BEFORE {event} ON {table} "
            + (f"WHEN {condition} " if condition else "")
            + f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    for name, t in OBSERVED_TABLES.items():
        keys = [tuple(c.name for c in t.primary_key)] + [
            tuple(c.name for c in k.columns)
            for k in t.constraints
            if isinstance(k, sa.UniqueConstraint)
        ]
        conflicts = " OR ".join(
            "(" + " AND ".join(f"p.{k}=NEW.{k}" for k in keys_) + ")" for keys_ in keys
        )
        guard(
            name,
            "immutable_insert",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {name} p WHERE {conflicts})",
            "immutable observed quant identity",
        )
        for event in ("UPDATE", "DELETE"):
            guard(
                name,
                "append_only_" + event.lower(),
                event,
                "",
                "observed quant graph is append-only",
            )
        key, parent, parent_key = (
            ("plan_id", PILOT + "plans", "artifact_id")
            if name in OBSERVED_QUANT_TABLES[:2]
            else (
                ("manifest_id", "training_history_manifests", "manifest_id")
                if name in (*OBSERVED_QUANT_TABLES[2:5], OBSERVED_QUANT_TABLES[7])
                else (
                    ("release_id", "production_quant_model_releases", "release_id")
                    if name in (OBSERVED_QUANT_TABLES[5], OBSERVED_QUANT_TABLES[8])
                    else ("packet_id", "production_audit_bundles", "packet_id")
                )
            )
        )
        guard(
            name,
            "sealed_insert",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {parent} p WHERE p.{parent_key}=NEW.{key})",
            "sealed observed parent cannot gain children",
        )
    for name in (OBSERVED_QUANT_TABLES[1], OBSERVED_QUANT_TABLES[3]):
        guard(
            name,
            "lineage_insert",
            "INSERT",
            "NOT EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.version_id=NEW.version_id AND r.content_hash=NEW.content_hash AND r.scope_id=NEW.scope_id AND r.internal_match_id=NEW.internal_match_id AND r.artifact_json=NEW.artifact_json)",
            "observed context requires exact admitted record",
        )
    guard(
        OBSERVED_QUANT_TABLES[7],
        "lineage_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM {PILOT}plans p JOIN {PILOT}attestations a ON a.plan_id=p.artifact_id
          JOIN {PILOT}summaries s ON s.artifact_id=a.summary_id JOIN {PILOT}reports r ON r.artifact_id=a.report_id
          WHERE p.artifact_id=NEW.plan_id AND p.content_hash=NEW.plan_hash AND p.evidence_use='REAL_SOURCE'
          AND a.artifact_id=NEW.attestation_id AND a.content_hash=NEW.attestation_hash AND a.evidence_use='REAL_SOURCE'
          AND s.artifact_id=NEW.summary_id AND s.content_hash=NEW.summary_hash AND s.series_id=a.series_id
          AND r.artifact_id=NEW.report_id AND r.content_hash=NEW.report_hash AND r.plan_id=p.artifact_id
          AND s.attempt_count=a.attempt_count AND s.attempt_root=a.attempt_root
          AND json_extract(p.artifact_json, '$.content_payload.definition.schema_version')='OBSERVED_QUANT_INTEGRITY_PLAN_DEFINITION_V1')
        OR json_extract(NEW.artifact_json, '$.schema_version') IS NOT 'TECHNICAL_EVIDENCE_REFS_V2'
        OR """
        + " OR ".join(
            f"json_extract(NEW.artifact_json, '$.{role}.{field}') IS NOT NEW.{role}_{column}"
            for role in ("plan", "summary", "report", "attestation")
            for field, column in (("artifact_id", "id"), ("content_hash", "hash"))
        ),
        "observed technical evidence requires the exact persisted real pilot graph",
    )
    guard(
        OBSERVED_QUANT_TABLES[8],
        "lineage_insert",
        "INSERT",
        """
        NOT EXISTS (SELECT 1 FROM observed_snapshot_admissions a WHERE a.admission_id=NEW.admission_id
          AND a.content_hash=NEW.admission_hash AND a.scope_id=NEW.scope_id AND a.admission_sequence=NEW.admission_high_watermark)
        OR json_extract(NEW.artifact_json, '$.schema_version') IS NOT 'OBSERVED_ADMISSION_PREFIX_V1'
        OR json_extract(NEW.artifact_json, '$.admission.artifact_id') IS NOT NEW.admission_id
        OR json_extract(NEW.artifact_json, '$.admission.content_hash') IS NOT NEW.admission_hash
        OR json_extract(NEW.artifact_json, '$.admission_high_watermark') IS NOT NEW.admission_high_watermark
    """,
        "observed authorization prefix requires its exact admission anchor",
    )
    guard(
        OBSERVED_QUANT_TABLES[2],
        "lineage_insert",
        "INSERT",
        "NOT EXISTS (SELECT 1 FROM observed_snapshot_admissions a WHERE a.admission_id=NEW.admission_id AND a.admission_sequence=NEW.admission_sequence AND a.content_hash=NEW.content_hash AND a.artifact_json=NEW.artifact_json)",
        "observed manifest requires exact admission parent",
    )
    guard(
        OBSERVED_QUANT_TABLES[3],
        "admission_insert",
        "INSERT",
        f"NOT EXISTS (SELECT 1 FROM observed_snapshot_records r JOIN {OBSERVED_QUANT_TABLES[2]} a ON a.admission_id=r.admission_id WHERE r.version_id=NEW.version_id AND a.manifest_id=NEW.manifest_id)",
        "observed context must reference its declared admission parents",
    )
    guard(
        OBSERVED_QUANT_TABLES[4],
        "lineage_insert",
        "INSERT",
        f"""
        NOT EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[3]} c JOIN observed_result_bindings b ON b.version_id=c.version_id
          WHERE c.manifest_id=NEW.manifest_id AND c.version_id=NEW.version_id AND c.internal_match_id=NEW.internal_match_id AND c.selected=1
          AND b.match_result_id=NEW.match_result_id AND c.artifact_json=json_extract(NEW.artifact_json, '$.content_payload.record'))
        OR json_extract(NEW.artifact_json, '$.schema_version') IS NOT 'OBSERVED_APPROVED_TRAINING_FACT_V1'
        OR json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.approved_fact_hash
        OR json_extract(NEW.artifact_json, '$.content_payload.elo_fact.fact_hash') IS NOT NEW.elo_fact_hash
        OR json_extract(NEW.artifact_json, '$.content_payload.elo_fact.match_result_id') IS NOT NEW.match_result_id
        OR json_extract(NEW.artifact_json, '$.content_payload.elo_fact.match_id') IS NOT NEW.internal_match_id
        OR json_extract(NEW.artifact_json, '$.content_payload.fact_sequence') IS NOT NEW.fact_sequence
        OR json_extract(NEW.artifact_json, '$.content_payload.season_sequence') IS NOT NEW.season_sequence
        OR NOT EXISTS (SELECT 1 FROM training_history_seasons s WHERE s.manifest_id=NEW.manifest_id AND s.season_sequence=NEW.season_sequence AND s.season_id=NEW.season_id)
    """,
        "observed selected fact projection mismatch",
    )

    def count(table, key, expected):
        return f"(SELECT COUNT(*) FROM {table} c WHERE c.{key}=NEW.{key}) IS NOT {expected}"

    def utc(value):
        # julianday rounds microseconds; compare canonical UTC microsecond text.
        plain = f"replace(replace({value}, 'T', ' '), 'Z', '')"
        return f"(substr({plain},1,19) || '.' || substr(substr({plain},21) || '000000',1,6))"

    def scope_uses(scope, uses):
        return " AND ".join(
            f"EXISTS (SELECT 1 FROM observed_collection_scopes s, json_each(s.artifact_json, '$.subject.permitted_uses') u WHERE s.scope_id={scope} AND u.value='{use}')"
            for use in uses
        )

    def eligible(alias, cutoff):
        values = [f"{alias}.registered_at_utc"] + [
            f"json_extract({alias}.artifact_json, '$.subject.{role}_capture.capture_{kind}_at_utc')"
            for role in ("fixture", "season", "result")
            for kind in ("observed", "registered")
        ]
        return " AND ".join(f"{utc(value)} < {utc(cutoff)}" for value in values)

    def prefix_checks(context, scope, manifest):
        watermark = (
            f"json_extract({context}, '$.admission_prefix.admission_high_watermark')"
        )
        return [
            f"json_extract({context}, '$.schema_version') IS NOT 'OBSERVED_AUTHORIZATION_CONTEXT_V2'",
            f"json_extract({context}, '$.scope.artifact_id') IS NOT {scope}",
            f"{watermark} IS NOT (SELECT MAX(a.admission_sequence) FROM observed_snapshot_admissions a WHERE a.scope_id={scope})",
            f"{watermark} IS NOT (SELECT MAX(a.admission_sequence) FROM {OBSERVED_QUANT_TABLES[2]} a WHERE a.manifest_id={manifest})",
            f"NOT EXISTS (SELECT 1 FROM observed_snapshot_admissions a WHERE a.scope_id={scope} AND a.admission_id=json_extract({context}, '$.admission_prefix.admission.artifact_id') AND a.content_hash=json_extract({context}, '$.admission_prefix.admission.content_hash') AND a.admission_sequence={watermark})",
            f"json_array_length({context}, '$.records') IS NOT (SELECT COUNT(*) FROM observed_snapshot_records r JOIN observed_snapshot_admissions a ON a.admission_id=r.admission_id WHERE a.scope_id={scope} AND a.admission_sequence<={watermark})",
            f"EXISTS (SELECT 1 FROM observed_snapshot_records r JOIN observed_snapshot_admissions a ON a.admission_id=r.admission_id WHERE a.scope_id={scope} AND a.admission_sequence<={watermark} AND NOT EXISTS (SELECT 1 FROM json_each({context}, '$.records') j WHERE json_extract(j.value,'$.fact.version_id')=r.version_id AND json_extract(j.value,'$.fact.content_hash')=r.content_hash AND json_extract(j.value,'$.fact.match_id')=r.internal_match_id AND json_extract(j.value,'$.fact.match_result_id') IS r.match_result_id AND json_extract(j.value,'$.stream_id')=r.stream_id AND json_extract(j.value,'$.revision_sequence')=r.revision_sequence AND json_extract(j.value,'$.predecessor_id') IS r.predecessor_id))",
        ]

    def context_checks(table, key, context_path, heads_path):
        return [
            count(
                table,
                key,
                f"json_array_length(NEW.artifact_json, '{context_path}.records')",
            ),
            f"EXISTS (SELECT 1 FROM {table} c WHERE c.{key}=NEW.{key} AND (c.artifact_json IS NOT json_extract(NEW.artifact_json, '{context_path}.records[' || c.context_sequence || ']') OR c.selected IS NOT EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '{heads_path}') h WHERE json_extract(h.value, '$.version_id')=c.version_id AND json_extract(h.value, '$.content_hash')=c.content_hash)))",
            f"(SELECT COUNT(*) FROM {table} c WHERE c.{key}=NEW.{key} AND c.selected=1) IS NOT json_array_length(NEW.artifact_json, '{heads_path}')",
            f"EXISTS (SELECT 1 FROM {table} c WHERE c.{key}=NEW.{key} AND c.selected=1 GROUP BY c.internal_match_id HAVING COUNT(*)>1)",
            f"EXISTS (SELECT 1 FROM {table} c WHERE c.{key}=NEW.{key} AND c.scope_id IS NOT json_extract(NEW.artifact_json, '{context_path}.scope.scope_id'))",
        ]

    d = "$.content_payload.definition"
    observed_plan = f"json_extract(NEW.artifact_json, '{d}.schema_version') IS 'OBSERVED_QUANT_INTEGRITY_PLAN_DEFINITION_V1'"
    checks = context_checks(
        OBSERVED_QUANT_TABLES[1],
        "plan_id",
        d + ".observed_context",
        d + ".selected_heads",
    )
    checks = [s.replace("NEW.plan_id", "NEW.artifact_id") for s in checks]
    checks += [
        f"NOT EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[0]} c JOIN observed_collection_scopes s ON s.scope_id=c.scope_id WHERE c.plan_id=NEW.artifact_id AND c.artifact_json=json_extract(NEW.artifact_json, '{d}.observed_context') AND s.artifact_json=json_extract(c.artifact_json, '$.scope') AND c.record_count=json_array_length(c.artifact_json, '$.records') AND c.context_root=json_extract(NEW.artifact_json, '$.content_payload.input_roots.source_root'))"
    ]
    scope = f"json_extract(NEW.artifact_json, '{d}.observed_context.scope.scope_id')"
    cutoff = f"json_extract(NEW.artifact_json, '{d}.terminal_projection.training_cutoff_at_utc')"
    checks += [
        f"NOT ({scope_uses(scope, ('TRAINING', 'VALIDATION'))})",
        f"julianday({cutoff}) IS NULL",
        f"EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.scope_id={scope} AND NOT EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[1]} c WHERE c.plan_id=NEW.artifact_id AND c.version_id=r.version_id))",
        f"EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[1]} c JOIN observed_snapshot_records r ON r.version_id=c.version_id WHERE c.plan_id=NEW.artifact_id AND c.selected IS NOT (({eligible('r', cutoff)}) AND NOT EXISTS (SELECT 1 FROM observed_snapshot_records n WHERE n.scope_id=r.scope_id AND n.stream_id=r.stream_id AND n.revision_sequence>r.revision_sequence AND ({eligible('n', cutoff)}))))",
        f"(SELECT COUNT(*) FROM {OBSERVED_QUANT_TABLES[1]} c WHERE c.plan_id=NEW.artifact_id AND c.selected=1) IS NOT (SELECT COUNT(*) FROM observed_snapshot_records r WHERE r.scope_id={scope} AND r.revision_sequence=0)",
    ]
    guard(
        PILOT + "plans",
        "observed_complete_insert",
        "INSERT",
        observed_plan + " AND (" + " OR ".join(checks) + ")",
        "observed plan requires exact full typed context",
    )
    guard(
        PILOT + "plans",
        "observed_basis_insert",
        "INSERT",
        f"NOT ({observed_plan}) AND EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[0]} c WHERE c.plan_id=NEW.artifact_id)",
        "mixed pilot evidence basis",
    )
    for suffix, content_schema in (
        ("outputs", "OBSERVED_QUANT_INTEGRITY_OUTPUT_CONTENT_V1"),
        ("reports", "OBSERVED_QUANT_INTEGRITY_REPORT_CONTENT_V1"),
        ("attestations", "OBSERVED_QUANT_INTEGRITY_ATTESTATION_CONTENT_V1"),
    ):
        guard(
            PILOT + suffix,
            "observed_basis_insert",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {PILOT}plans p WHERE p.artifact_id=NEW.plan_id AND json_extract(p.artifact_json, '{d}.schema_version')='OBSERVED_QUANT_INTEGRITY_PLAN_DEFINITION_V1') IS NOT (json_extract(NEW.artifact_json, '$.content_payload.schema_version') IS '{content_schema}')",
            "pilot content must preserve exact evidence basis",
        )
    guard(
        PILOT + "attempts",
        "observed_current_prefix_insert",
        "INSERT",
        f"""
        NEW.status='COMPLETED' AND EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[0]} p WHERE p.plan_id=NEW.plan_id AND
          EXISTS (SELECT 1 FROM observed_snapshot_records r WHERE r.scope_id=p.scope_id AND NOT EXISTS (
            SELECT 1 FROM {OBSERVED_QUANT_TABLES[1]} c WHERE c.plan_id=NEW.plan_id AND c.version_id=r.version_id)))
    """,
        "observed completion requires the actual complete admission prefix",
    )

    g = "$.content_payload.history"
    checks = context_checks(
        OBSERVED_QUANT_TABLES[3],
        "manifest_id",
        g + ".observed_context",
        g + ".selected_heads",
    )
    checks += [
        count(OBSERVED_QUANT_TABLES[2], "manifest_id", "NEW.admission_count"),
        count(OBSERVED_QUANT_TABLES[4], "manifest_id", "NEW.fact_count"),
        count("training_history_sources", "manifest_id", "NEW.source_count"),
        count("training_history_seasons", "manifest_id", "NEW.season_count"),
        f"EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[2]} a WHERE a.manifest_id=NEW.manifest_id AND a.artifact_json IS NOT json_extract(NEW.artifact_json, '{g}.admissions[' || a.admission_sequence || ']'))",
        f"EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[4]} f WHERE f.manifest_id=NEW.manifest_id AND f.artifact_json IS NOT json_extract(NEW.artifact_json, '{g}.facts[' || f.fact_sequence || ']'))",
        f"json_array_length(NEW.artifact_json, '{g}.facts') IS NOT NEW.fact_count",
        f"json_array_length(NEW.artifact_json, '{g}.admissions') IS NOT NEW.admission_count",
        f"json_array_length(NEW.artifact_json, '{g}.source_summaries') IS NOT NEW.source_count",
        f"json_array_length(NEW.artifact_json, '{g}.season_summaries') IS NOT NEW.season_count",
        *(
            f"json_extract(NEW.artifact_json, '{g}.{key}') IS NOT NEW.{key}"
            for key in (
                "source_root",
                "season_root",
                "approved_facts_hash",
                "training_data_hash",
            )
        ),
        "json_extract(NEW.artifact_json, '$.artifact_id') IS NOT NEW.manifest_id",
        "json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.manifest_hash",
        "json_extract(NEW.artifact_json, '$.content_payload.technical_evidence.schema_version') IS NOT 'TECHNICAL_EVIDENCE_REFS_V2'",
        "json_type(NEW.artifact_json, '$.content_payload.technical_evidence.strict_walk_forward.metrics') IS NOT 'null'",
        count(OBSERVED_QUANT_TABLES[7], "manifest_id", "1"),
        f"NOT EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[7]} t WHERE t.manifest_id=NEW.manifest_id AND t.attestation_id=NEW.attestation_id AND t.attestation_hash=NEW.attestation_hash AND t.artifact_json=json_extract(NEW.artifact_json, '$.content_payload.technical_evidence'))",
        f"NOT ({scope_uses(f"json_extract(NEW.artifact_json, '{g}.observed_context.scope.scope_id')", ('TRAINING', 'VALIDATION'))})",
    ]
    for name in (
        "training_history_admissions",
        "training_history_facts",
        "training_history_fixture_sources",
        "training_history_mapping_sources",
        "training_history_result_sources",
        "training_history_version_context",
        "training_history_version_facts",
    ):
        checks.append(count(name, "manifest_id", "0"))
    guard(
        "training_history_manifests",
        "observed_complete_insert",
        "INSERT",
        f"json_extract(NEW.artifact_json, '{g}.schema_version')='{OBSERVED_GRAPH}' AND ("
        + " OR ".join(checks)
        + ")",
        "observed manifest requires exact complete unmixed children",
    )
    r = "$.training_manifest.content_payload.history"
    checks = [
        count(OBSERVED_QUANT_TABLES[5], "release_id", "NEW.fact_count"),
        count("production_quant_model_release_facts", "release_id", "0"),
        count("production_quant_model_release_version_facts", "release_id", "0"),
        "NOT EXISTS (SELECT 1 FROM training_history_approval_events a JOIN training_history_manifests m ON m.manifest_id=a.manifest_id WHERE a.approval_id=NEW.approval_id AND a.manifest_id=NEW.manifest_id AND a.persisted_at_utc <= NEW.build_started_at_utc AND json_extract(a.artifact_json, '$.schema_version')='TRAINING_HISTORY_APPROVAL_V2' AND m.fact_count=NEW.fact_count AND m.artifact_json=json_extract(NEW.artifact_json, '$.training_manifest'))",
        f"EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[5]} f WHERE f.release_id=NEW.release_id AND (f.manifest_id<>NEW.manifest_id OR f.artifact_json IS NOT json_extract(NEW.artifact_json, '$.content_payload.release_facts[' || f.fact_sequence || ']') OR NOT EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[4]} m WHERE m.manifest_id=f.manifest_id AND m.fact_sequence=f.fact_sequence AND m.artifact_json=f.artifact_json)))",
        "json_extract(NEW.artifact_json, '$.content_payload.schema_version') IS NOT 'OBSERVED_PRODUCTION_RELEASE_CONTENT_V1'",
        f"{utc(f"json_extract(NEW.artifact_json, '{r}.selection_cutoff_at_utc')")} IS NOT {utc('NEW.training_cutoff_at_utc')}",
        "json_extract(NEW.artifact_json, '$.content_payload.released_state_core.content_hash') IS NOT NEW.released_state_core_hash",
        count(OBSERVED_QUANT_TABLES[8], "release_id", "2"),
        f"EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[8]} p WHERE p.release_id=NEW.release_id AND (p.scope_id IS NOT json_extract(NEW.artifact_json, '{r}.observed_context.scope.scope_id') OR p.admission_high_watermark IS NOT (SELECT MAX(a.admission_sequence) FROM observed_snapshot_admissions a WHERE a.scope_id=p.scope_id) OR p.artifact_json IS NOT json_extract(NEW.artifact_json, '$.content_payload.' || CASE p.phase WHEN 'BUILD_START' THEN 'build_start_authorization' ELSE 'build_completion_authorization' END || '.content_payload.current.observed_context.admission_prefix')))",
    ]
    for phase in ("build_start_authorization", "build_completion_authorization"):
        context = f"json_extract(NEW.artifact_json, '$.content_payload.{phase}.content_payload.current.observed_context')"
        checks += prefix_checks(
            context,
            f"json_extract(NEW.artifact_json, '{r}.observed_context.scope.scope_id')",
            "NEW.manifest_id",
        )
    guard(
        "production_quant_model_releases",
        "observed_complete_insert",
        "INSERT",
        f"json_extract(NEW.artifact_json, '{r}.schema_version')='{OBSERVED_GRAPH}' AND ("
        + " OR ".join(checks)
        + ")",
        "observed release requires exact approval and selected facts",
    )
    scope = "json_extract(m.artifact_json, '$.content_payload.history.observed_context.scope.scope_id')"
    guard(
        "training_history_approval_events",
        "observed_scope_uses_insert",
        "INSERT",
        f"EXISTS (SELECT 1 FROM training_history_manifests m WHERE m.manifest_id=NEW.manifest_id AND json_extract(m.artifact_json, '$.content_payload.history.schema_version')='{OBSERVED_GRAPH}' AND NOT ({scope_uses(scope, ('TRAINING',))}))",
        "production grants cannot restore forbidden observed scope TRAINING",
    )
    checks = []
    for phase in ("start_authorization", "completion_authorization"):
        checks += prefix_checks(
            f"json_extract(NEW.binding_json, '$.{phase}.observed_context')",
            scope,
            "m.manifest_id",
        )
    guard(
        "quant_model_state_production_releases",
        "observed_current_prefix_insert",
        "INSERT",
        f"EXISTS (SELECT 1 FROM production_quant_model_releases r JOIN training_history_manifests m ON m.manifest_id=r.manifest_id WHERE r.release_id=NEW.release_id AND json_extract(m.artifact_json, '$.content_payload.history.schema_version')='{OBSERVED_GRAPH}' AND ("
        + " OR ".join(checks)
        + "))",
        "observed inference requires the actual complete admission prefix",
    )
    completion_checks = [
        check.replace("NEW.binding_json", "b.binding_json") for check in checks
    ]
    for action in ("INSERT", "UPDATE"):
        guard(
            "analysis_runs",
            "observed_prefix_completion_" + action.lower(),
            action,
            f"NEW.status='COMPLETED' AND EXISTS (SELECT 1 FROM quant_model_state_production_releases b JOIN production_quant_model_releases r ON r.release_id=b.release_id JOIN training_history_manifests m ON m.manifest_id=r.manifest_id WHERE b.analysis_run_id=NEW.analysis_run_id AND json_extract(m.artifact_json, '$.content_payload.history.schema_version')='{OBSERVED_GRAPH}' AND ("
            + " OR ".join(completion_checks)
            + "))",
            "completed observed inference requires the actual complete admission prefix",
        )
    for table, path, key, children in (
        (
            "training_history_manifests",
            g,
            "manifest_id",
            (*OBSERVED_QUANT_TABLES[2:5], OBSERVED_QUANT_TABLES[7]),
        ),
        (
            "production_quant_model_releases",
            r,
            "release_id",
            (OBSERVED_QUANT_TABLES[5], OBSERVED_QUANT_TABLES[8]),
        ),
    ):
        guard(
            table,
            "observed_basis_insert",
            "INSERT",
            f"json_extract(NEW.artifact_json, '{path}.schema_version') IS NOT '{OBSERVED_GRAPH}' AND ("
            + " OR ".join(count(t, key, "0") for t in children)
            + ")",
            "mixed observed/historical typed children",
        )
    guard(
        "production_target_acceptance_plans",
        "observed_exclusion_insert",
        "INSERT",
        f"EXISTS (SELECT 1 FROM production_target_acceptance_matches t JOIN production_quant_model_releases r ON r.release_id=NEW.release_id JOIN {OBSERVED_QUANT_TABLES[4]} f ON f.manifest_id=r.manifest_id AND f.internal_match_id=t.internal_match_id WHERE t.plan_id=NEW.plan_id)",
        "ALL observed production targets must be excluded",
    )
    guard(
        "production_target_acceptance_plans",
        "observed_frozen_targets_insert",
        "INSERT",
        f"EXISTS (SELECT 1 FROM production_target_acceptance_matches t JOIN production_quant_model_releases r ON r.release_id=NEW.release_id WHERE t.plan_id=NEW.plan_id AND json_extract(r.artifact_json, '$.training_manifest.content_payload.history.schema_version')='{OBSERVED_GRAPH}' AND NOT EXISTS (SELECT 1 FROM json_each(r.artifact_json, '$.training_manifest.content_payload.history.exclude_match_ids') x WHERE x.value=t.internal_match_id))",
        "observed targets require frozen explicit exclusions",
    )
    guard(
        "production_audit_bundles",
        "observed_basis_insert",
        "INSERT",
        f"""
        (EXISTS (SELECT 1 FROM production_quant_model_releases r WHERE r.release_id=NEW.release_id AND json_extract(r.artifact_json, '$.content_payload.evidence_basis')='CURRENT_SNAPSHOT_OBSERVED') AND NOT EXISTS (
          SELECT 1 FROM {OBSERVED_QUANT_TABLES[6]} b JOIN production_quant_model_releases r ON r.release_id=b.release_id
          WHERE b.packet_id=NEW.packet_id AND b.release_id=NEW.release_id
          AND b.context_root=json_extract(NEW.audit_json, '$.content_payload.technical_evidence.observed_context_root')
          AND b.context_root=json_extract(r.artifact_json, '$.content_payload.technical_evidence.observed_context_root')
          AND b.scope_id=json_extract(r.artifact_json, '$.training_manifest.content_payload.history.observed_context.scope.scope_id')
          AND json_extract(b.artifact_json, '$.schema_version')='OBSERVED_AUDIT_BASIS_V1'
          AND json_extract(b.artifact_json, '$.observed_context_root')=b.context_root
          AND json_extract(b.artifact_json, '$.model_training_evidence_basis')=b.evidence_basis
          AND json_type(b.artifact_json, '$.strict_walk_forward.metrics')='null'
          AND json_extract(NEW.audit_json, '$.content_payload.schema_version')='OBSERVED_TRAINING_HISTORY_AUDIT_CONTENT_V1'
          AND json_extract(NEW.audit_json, '$.content_payload.model_training_evidence_basis')=b.evidence_basis))
        OR (json_extract(NEW.audit_json, '$.content_payload.model_training_evidence_basis') IS NOT 'CURRENT_SNAPSHOT_OBSERVED' AND EXISTS (SELECT 1 FROM {OBSERVED_QUANT_TABLES[6]} b WHERE b.packet_id=NEW.packet_id))
    """,
        "observed audit requires explicit typed training basis",
    )
    return result


def install_observed_quant_schema_in_connection(connection):
    for table in OBSERVED_TABLES.values():
        table.create(connection, checkfirst=True)
    for name, sql in observed_quant_trigger_sql_v1().items():
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
        connection.exec_driver_sql(sql)


def install_observed_quant_schema(engine):
    with engine.begin() as connection:
        install_observed_quant_schema_in_connection(connection)
