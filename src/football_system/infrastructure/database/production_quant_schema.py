"""Frozen SQLite production schema for revision a0c9e4f6b132.

Children precede parents in one transaction. Deferred parent FKs prevent orphan
commits; parent insertion checks exact sequence coverage and closes the graph.
SQL guards complement, never replace, repository byte/hash/authority validation.
"""

import sqlalchemy as sa


PRODUCTION_QUANT_TABLES = (
    "training_history_manifests",
    "training_history_admissions",
    "training_history_seasons",
    "training_history_sources",
    "training_history_fixture_sources",
    "training_history_mapping_sources",
    "training_history_result_sources",
    "training_history_facts",
    "training_history_approval_events",
    "training_history_approval_grants",
    "training_history_successors",
    "production_quant_model_releases",
    "production_quant_model_release_facts",
    "production_target_acceptance_plans",
    "production_target_acceptance_matches",
    "training_history_revocation_events",
    "training_source_correction_events",
)


def production_quant_tables_v1(metadata, datetime_type):
    def col(name, type_=sa.String(160), **kwargs):
        return sa.Column(name, type_, nullable=kwargs.pop("nullable", False), **kwargs)

    def fk(names, table, targets=None, *, deferred=False):
        names = (names,) if isinstance(names, str) else names
        targets = (
            names
            if targets is None
            else ((targets,) if isinstance(targets, str) else targets)
        )
        return sa.ForeignKeyConstraint(
            names,
            [f"{table}.{key}" for key in targets],
            ondelete="RESTRICT",
            **({"deferrable": True, "initially": "DEFERRED"} if deferred else {}),
        )

    def common():
        return [col("artifact_json", sa.Text()), col("row_sha256", sa.String(64))]

    def request():
        return [
            col("request_key", unique=True),
            col("request_sha256", sa.String(64), unique=True),
            col("request_json", sa.Text()),
            col("operator_id"),
        ]

    def manifest_child():
        return [
            col("manifest_id"),
            fk("manifest_id", "training_history_manifests", deferred=True),
        ]

    def source_child():
        return [
            *manifest_child(),
            col("source_sequence", sa.Integer()),
            col("season_sequence", sa.Integer()),
            col("training_fact_admission_id"),
            col("internal_match_id"),
            col("provider_mapping_id"),
            sa.PrimaryKeyConstraint("manifest_id", "source_sequence"),
            sa.UniqueConstraint("manifest_id", "internal_match_id"),
            sa.CheckConstraint("source_sequence >= 0"),
            fk(("manifest_id", "season_sequence"), "training_history_seasons"),
            fk(
                ("manifest_id", "training_fact_admission_id"),
                "training_history_admissions",
            ),
            fk("internal_match_id", "matches"),
            fk("provider_mapping_id", "provider_match_mappings", "mapping_id"),
        ]

    def table(name, *items):
        items = list(items)
        for item in tuple(items):
            if isinstance(item, sa.Column) and item.name.endswith(("sha256", "hash")):
                items.append(
                    sa.CheckConstraint(
                        f"length({item.name}) = 64 AND {item.name} NOT GLOB '*[^0-9a-f]*'"
                    )
                )
            if isinstance(item, sa.Column) and item.name.endswith("_json"):
                items.append(
                    sa.CheckConstraint(
                        f"json_valid({item.name}) AND json_type({item.name}) = 'object'"
                    )
                )
        return sa.Table(name, metadata, *items)

    tables = [
        table(
            "training_history_manifests",
            col("manifest_id", primary_key=True),
            col("manifest_hash", sa.String(64), unique=True),
            col("competition_id"),
            col("pilot_target_season_id"),
            col("production_target_season_id"),
            col("attestation_id"),
            col("attestation_hash", sa.String(64)),
            col("attempt_root", sa.String(64)),
            col("source_root", sa.String(64)),
            col("season_root", sa.String(64)),
            col("approved_facts_hash", sa.String(64)),
            col("training_data_hash", sa.String(64)),
            col("admission_count", sa.Integer()),
            col("source_count", sa.Integer()),
            col("season_count", sa.Integer()),
            col("fact_count", sa.Integer()),
            col("created_at_utc", datetime_type),
            col("persisted_at_utc", datetime_type),
            *common(),
            *request(),
            fk("competition_id", "competitions"),
            sa.CheckConstraint(
                "admission_count > 0 AND source_count > 0 AND season_count >= 2 AND fact_count > 0"
            ),
            sa.CheckConstraint(
                "pilot_target_season_id <> production_target_season_id AND created_at_utc <= persisted_at_utc"
            ),
        ),
        table(
            "training_history_admissions",
            *manifest_child(),
            col("training_fact_admission_id"),
            col("admission_hash", sa.String(64)),
            col("source_rights_admission_id"),
            *common(),
            sa.PrimaryKeyConstraint("manifest_id", "training_fact_admission_id"),
            fk("training_fact_admission_id", "training_fact_admissions"),
            fk("source_rights_admission_id", "source_rights_admissions"),
        ),
        table(
            "training_history_seasons",
            *manifest_child(),
            col("season_sequence", sa.Integer()),
            col("season_id"),
            col("role", sa.String(32)),
            col("fact_count", sa.Integer()),
            col("facts_hash", sa.String(64)),
            *common(),
            sa.PrimaryKeyConstraint("manifest_id", "season_sequence"),
            sa.UniqueConstraint("manifest_id", "season_id"),
            sa.CheckConstraint("season_sequence >= 0 AND fact_count >= 0"),
            sa.CheckConstraint(
                "role IN ('WARMUP', 'PILOT_TARGET', 'PRODUCTION_TARGET')"
            ),
        ),
        table(
            "training_history_sources",
            *manifest_child(),
            col("source_sequence", sa.Integer()),
            col("source_id"),
            col("provider_code"),
            col("source_rights_admission_id"),
            col("fact_count", sa.Integer()),
            col("facts_hash", sa.String(64)),
            *common(),
            sa.PrimaryKeyConstraint("manifest_id", "source_sequence"),
            sa.UniqueConstraint(
                "manifest_id",
                "source_id",
                "provider_code",
                "source_rights_admission_id",
            ),
            sa.CheckConstraint("source_sequence >= 0 AND fact_count > 0"),
            fk("source_rights_admission_id", "source_rights_admissions"),
        ),
        table(
            "training_history_fixture_sources",
            *source_child(),
            col("fixture_source_id"),
            col("fixture_record_hash", sa.String(64)),
            *common(),
            fk(
                ("training_fact_admission_id", "fixture_source_id"),
                "training_fact_fixture_sources",
            ),
        ),
        table(
            "training_history_mapping_sources",
            *source_child(),
            col("season_membership_id"),
            col("membership_hash", sa.String(64)),
            *common(),
            fk(
                ("training_fact_admission_id", "season_membership_id"),
                "match_season_memberships",
            ),
        ),
        table(
            "training_history_result_sources",
            *source_child(),
            col("match_result_admission_id"),
            col("admission_hash", sa.String(64)),
            col("match_result_id"),
            *common(),
            fk(
                ("training_fact_admission_id", "match_result_admission_id"),
                "match_result_admissions",
            ),
            fk(
                ("training_fact_admission_id", "match_result_id"),
                "match_result_admissions",
            ),
            fk("match_result_id", "match_results"),
        ),
        table(
            "training_history_facts",
            *manifest_child(),
            col("fact_sequence", sa.Integer()),
            col("season_sequence", sa.Integer()),
            col("season_id"),
            col("internal_match_id"),
            col("match_result_id"),
            col("training_fact_admission_id"),
            col("training_fact_binding_id"),
            col("fixture_source_sequence", sa.Integer()),
            col("mapping_source_sequence", sa.Integer()),
            col("result_source_sequence", sa.Integer()),
            col("elo_fact_hash", sa.String(64)),
            col("approved_fact_hash", sa.String(64)),
            *common(),
            sa.PrimaryKeyConstraint("manifest_id", "fact_sequence"),
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
            fk(("manifest_id", "season_sequence"), "training_history_seasons"),
            fk(
                ("training_fact_admission_id", "training_fact_binding_id"),
                "training_fact_bindings",
            ),
            fk("match_result_id", "match_results"),
            *[
                fk(
                    ("manifest_id", f"{kind}_source_sequence"),
                    f"training_history_{kind}_sources",
                    ("manifest_id", "source_sequence"),
                )
                for kind in ("fixture", "mapping", "result")
            ],
        ),
        table(
            "training_history_approval_events",
            col("approval_id", primary_key=True),
            col("approval_hash", sa.String(64), unique=True),
            col("manifest_id"),
            col("approved_at_utc", datetime_type),
            col("persisted_at_utc", datetime_type),
            col("supersedes_approval_id", nullable=True, unique=True),
            *common(),
            *request(),
            fk("manifest_id", "training_history_manifests"),
            fk(
                "supersedes_approval_id",
                "training_history_approval_events",
                "approval_id",
            ),
            sa.CheckConstraint(
                "approved_at_utc <= persisted_at_utc AND (supersedes_approval_id IS NULL OR supersedes_approval_id <> approval_id)"
            ),
        ),
        table(
            "training_history_approval_grants",
            col("approval_id"),
            col("grant", sa.String(64)),
            col("effective_at_utc", datetime_type),
            col("expires_at_utc", datetime_type, nullable=True),
            *common(),
            sa.PrimaryKeyConstraint("approval_id", "grant"),
            fk("approval_id", "training_history_approval_events", deferred=True),
            sa.CheckConstraint(
                "grant IN ('PRODUCTION_MODEL_TRAINING', 'PRODUCTION_MODEL_INFERENCE', 'DERIVED_MODEL_STATE_RETENTION', 'AUDIT_HASH_RETENTION')"
            ),
            sa.CheckConstraint(
                "expires_at_utc IS NULL OR effective_at_utc < expires_at_utc"
            ),
        ),
        table(
            "training_history_successors",
            col("predecessor_id", primary_key=True),
            col("successor_id", unique=True),
            col("successor_persisted_at_utc", datetime_type),
            col("effective_at_utc", datetime_type),
            *common(),
            fk("predecessor_id", "training_history_approval_events", "approval_id"),
            fk(
                "successor_id",
                "training_history_approval_events",
                "approval_id",
                deferred=True,
            ),
            sa.CheckConstraint(
                "predecessor_id <> successor_id AND successor_persisted_at_utc <= effective_at_utc"
            ),
        ),
        table(
            "production_quant_model_releases",
            col("release_id", primary_key=True),
            col("release_hash", sa.String(64), unique=True),
            col("approval_id"),
            col("manifest_id"),
            col("released_state_core_hash", sa.String(64)),
            col("training_cutoff_at_utc", datetime_type),
            col("fact_count", sa.Integer()),
            col("build_started_at_utc", datetime_type),
            col("build_completed_at_utc", datetime_type),
            col("persisted_at_utc", datetime_type),
            *common(),
            *request(),
            fk("approval_id", "training_history_approval_events"),
            fk("manifest_id", "training_history_manifests"),
            sa.CheckConstraint(
                "fact_count > 0 AND training_cutoff_at_utc < build_started_at_utc AND build_started_at_utc <= build_completed_at_utc AND build_completed_at_utc <= persisted_at_utc"
            ),
        ),
        table(
            "production_quant_model_release_facts",
            col("release_id"),
            col("manifest_id"),
            col("fact_sequence", sa.Integer()),
            col("match_result_id"),
            col("elo_fact_hash", sa.String(64)),
            col("approved_fact_hash", sa.String(64)),
            *common(),
            sa.PrimaryKeyConstraint("release_id", "fact_sequence"),
            sa.UniqueConstraint("release_id", "match_result_id"),
            fk("release_id", "production_quant_model_releases", deferred=True),
            fk(
                (
                    "manifest_id",
                    "fact_sequence",
                    "match_result_id",
                    "elo_fact_hash",
                    "approved_fact_hash",
                ),
                "training_history_facts",
            ),
        ),
        table(
            "production_target_acceptance_plans",
            col("plan_id", primary_key=True),
            col("plan_hash", sa.String(64), unique=True),
            col("release_id"),
            col("target_count", sa.Integer()),
            col("sealed_at_utc", datetime_type),
            col("persisted_at_utc", datetime_type),
            col("decision_as_of_at_utc", datetime_type),
            *common(),
            *request(),
            fk("release_id", "production_quant_model_releases"),
            sa.CheckConstraint(
                "target_count > 0 AND sealed_at_utc <= persisted_at_utc AND persisted_at_utc < decision_as_of_at_utc"
            ),
        ),
        table(
            "production_target_acceptance_matches",
            col("plan_id"),
            col("target_sequence", sa.Integer()),
            col("internal_match_id"),
            col("kickoff_at_utc", datetime_type),
            *common(),
            sa.PrimaryKeyConstraint("plan_id", "target_sequence"),
            sa.UniqueConstraint("plan_id", "internal_match_id"),
            fk("plan_id", "production_target_acceptance_plans", deferred=True),
            fk("internal_match_id", "matches"),
        ),
        table(
            "training_history_revocation_events",
            col("revocation_id", primary_key=True),
            col("revocation_hash", sa.String(64), unique=True),
            col("approval_id"),
            col("release_id", nullable=True),
            col("recorded_at_utc", datetime_type),
            col("effective_at_utc", datetime_type),
            *common(),
            *request(),
            fk("approval_id", "training_history_approval_events"),
            fk("release_id", "production_quant_model_releases"),
        ),
        table(
            "training_source_correction_events",
            col("correction_id", primary_key=True),
            col("correction_hash", sa.String(64), unique=True),
            col("internal_match_id"),
            col("source_id"),
            col("component", sa.String(32)),
            col("predecessor_id"),
            col("registered_at_utc", datetime_type),
            col("source_available_at_utc", datetime_type),
            *common(),
            fk("internal_match_id", "matches"),
            sa.UniqueConstraint("source_id", "component", "predecessor_id"),
            sa.CheckConstraint(
                "component IN ('FIXTURE','MAPPING','SEASON','STATUS','RESULT')"
            ),
        ),
    ]
    return {item.name: item for item in tables}


def production_quant_trigger_sql_v1():
    # Keep migration SQL independent from runtime ORM imports.
    tables = production_quant_tables_v1(sa.MetaData(), sa.DateTime(timezone=True))
    result = {}

    def trigger(table, suffix, action, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} "
            f"{('WHEN ' + condition) if condition else ''} BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    for name, table in tables.items():
        unique = [tuple(col.name for col in table.primary_key.columns)]
        unique.extend(
            tuple(col.name for col in item.columns)
            for item in table.constraints
            if isinstance(item, sa.UniqueConstraint)
        )
        conflict = " OR ".join(
            "(" + " AND ".join(f"old.{key} = NEW.{key}" for key in keys) + ")"
            for keys in sorted(set(unique))
        )
        trigger(
            name,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {name} old WHERE {conflict})",
            "immutable production record already exists",
        )
        for action in ("UPDATE", "DELETE"):
            trigger(
                name,
                f"append_only_{action.lower()}",
                action,
                "",
                "production lineage is append-only",
            )

    child_parents = {
        **{
            name: ("training_history_manifests", "manifest_id", "manifest_id")
            for name in PRODUCTION_QUANT_TABLES[1:8]
        },
        "training_history_approval_grants": (
            "training_history_approval_events",
            "approval_id",
            "approval_id",
        ),
        "training_history_successors": (
            "training_history_approval_events",
            "approval_id",
            "successor_id",
        ),
        "production_quant_model_release_facts": (
            "production_quant_model_releases",
            "release_id",
            "release_id",
        ),
        "production_target_acceptance_matches": (
            "production_target_acceptance_plans",
            "plan_id",
            "plan_id",
        ),
    }
    for child, (parent, pk, local) in child_parents.items():
        trigger(
            child,
            "sealed_insert",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {parent} p WHERE p.{pk} = NEW.{local})",
            "sealed production graph cannot gain children",
        )

    def count(table, fk, expected, sequence=None):
        where = f"{fk} = NEW.{fk}"
        check = f"(SELECT COUNT(*) FROM {table} WHERE {where}) <> {expected}"
        if sequence:
            check += f" OR (SELECT MIN({sequence}) FROM {table} WHERE {where}) IS NOT 0 OR (SELECT MAX({sequence}) FROM {table} WHERE {where}) IS NOT {expected} - 1"
        return check

    manifest_checks = [
        count("training_history_admissions", "manifest_id", "NEW.admission_count"),
        count(
            "training_history_sources",
            "manifest_id",
            "NEW.source_count",
            "source_sequence",
        ),
        count(
            "training_history_seasons",
            "manifest_id",
            "NEW.season_count",
            "season_sequence",
        ),
        *[
            count(
                f"training_history_{kind}_sources",
                "manifest_id",
                "NEW.fact_count",
                "source_sequence",
            )
            for kind in ("fixture", "mapping", "result")
        ],
        count(
            "training_history_facts", "manifest_id", "NEW.fact_count", "fact_sequence"
        ),
        "json_extract(NEW.artifact_json, '$.artifact_id') IS NOT NEW.manifest_id",
        "json_extract(NEW.artifact_json, '$.content_hash') IS NOT NEW.manifest_hash",
        "json_array_length(NEW.artifact_json, '$.content_payload.history.facts') IS NOT NEW.fact_count",
        "EXISTS (SELECT 1 FROM training_history_facts c WHERE c.manifest_id = NEW.manifest_id AND (json_extract(NEW.artifact_json, '$.content_payload.history.facts[' || c.fact_sequence || '].content_hash') IS NOT c.approved_fact_hash))",
    ]
    trigger(
        "training_history_manifests",
        "complete_insert",
        "INSERT",
        " OR ".join(manifest_checks),
        "production manifest requires exact complete children",
    )
    for kind, parent, identity in (
        ("fixture", "training_fact_fixture_sources", "fixture_source_id"),
        ("mapping", "match_season_memberships", "season_membership_id"),
        ("result", "match_result_admissions", "match_result_admission_id"),
    ):
        extra = (
            " AND p.match_result_id = NEW.match_result_id" if kind == "result" else ""
        )
        trigger(
            f"training_history_{kind}_sources",
            "lineage_insert",
            "INSERT",
            f"NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.training_fact_admission_id = NEW.training_fact_admission_id AND p.{identity} = NEW.{identity} AND p.internal_match_id = NEW.internal_match_id AND p.provider_mapping_id = NEW.provider_mapping_id{extra})",
            "production source admission lineage mismatch",
        )
    trigger(
        "training_history_facts",
        "lineage_insert",
        "INSERT",
        " OR ".join(
            [
                "NOT EXISTS (SELECT 1 FROM training_fact_bindings b WHERE b.training_fact_admission_id = NEW.training_fact_admission_id AND b.training_fact_binding_id = NEW.training_fact_binding_id AND b.internal_match_id = NEW.internal_match_id AND b.match_result_id = NEW.match_result_id)",
                "NOT EXISTS (SELECT 1 FROM training_history_seasons s WHERE s.manifest_id = NEW.manifest_id AND s.season_sequence = NEW.season_sequence AND s.season_id = NEW.season_id)",
                *[
                    f"NOT EXISTS (SELECT 1 FROM training_history_{kind}_sources c WHERE c.manifest_id = NEW.manifest_id AND c.source_sequence = NEW.{kind}_source_sequence AND c.training_fact_admission_id = NEW.training_fact_admission_id AND c.internal_match_id = NEW.internal_match_id AND c.season_sequence = NEW.season_sequence)"
                    for kind in ("fixture", "mapping", "result")
                ],
            ]
        ),
        "production fact composite lineage mismatch",
    )
    trigger(
        "training_history_approval_events",
        "complete_insert",
        "INSERT",
        " OR ".join(
            [
                count("training_history_approval_grants", "approval_id", "4"),
                "json_extract(NEW.artifact_json, '$.content_payload.approval_payload.manifest.artifact_id') IS NOT NEW.manifest_id",
                "(NEW.supersedes_approval_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM training_history_successors s WHERE s.predecessor_id = NEW.supersedes_approval_id AND s.successor_id = NEW.approval_id))",
                "(NEW.supersedes_approval_id IS NULL AND EXISTS (SELECT 1 FROM training_history_successors s WHERE s.successor_id = NEW.approval_id))",
            ]
        ),
        "approval requires exact grants and successor graph",
    )
    trigger(
        "production_quant_model_releases",
        "complete_insert",
        "INSERT",
        " OR ".join(
            [
                count(
                    "production_quant_model_release_facts",
                    "release_id",
                    "NEW.fact_count",
                    "fact_sequence",
                ),
                "NOT EXISTS (SELECT 1 FROM training_history_approval_events a JOIN training_history_manifests m ON m.manifest_id = a.manifest_id WHERE a.approval_id = NEW.approval_id AND a.manifest_id = NEW.manifest_id AND a.persisted_at_utc <= NEW.training_cutoff_at_utc AND m.fact_count = NEW.fact_count)",
                "EXISTS (SELECT 1 FROM production_quant_model_release_facts c WHERE c.release_id = NEW.release_id AND (c.manifest_id <> NEW.manifest_id OR json_extract(NEW.artifact_json, '$.content_payload.release_facts[' || c.fact_sequence || '].content_hash') IS NOT c.approved_fact_hash))",
                "json_extract(NEW.artifact_json, '$.content_payload.released_state_core.content_hash') IS NOT NEW.released_state_core_hash",
            ]
        ),
        "release requires exact approval manifest and release facts",
    )
    trigger(
        "production_target_acceptance_plans",
        "complete_insert",
        "INSERT",
        " OR ".join(
            [
                count(
                    "production_target_acceptance_matches",
                    "plan_id",
                    "NEW.target_count",
                    "target_sequence",
                ),
                "NOT EXISTS (SELECT 1 FROM production_quant_model_releases r WHERE r.release_id = NEW.release_id AND r.persisted_at_utc < NEW.sealed_at_utc)",
                "EXISTS (SELECT 1 FROM production_target_acceptance_matches t JOIN production_quant_model_releases r ON r.release_id = NEW.release_id JOIN training_history_facts f ON f.manifest_id = r.manifest_id AND f.internal_match_id = t.internal_match_id WHERE t.plan_id = NEW.plan_id)",
                "EXISTS (SELECT 1 FROM production_target_acceptance_matches t WHERE t.plan_id = NEW.plan_id AND (t.kickoff_at_utc <= NEW.decision_as_of_at_utc OR json_extract(NEW.artifact_json, '$.content_payload.targets[' || t.target_sequence || '].match_id') IS NOT t.internal_match_id))",
            ]
        ),
        "target plan requires exact future excluded targets",
    )
    trigger(
        "training_history_revocation_events",
        "lineage_insert",
        "INSERT",
        "NEW.release_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM production_quant_model_releases r WHERE r.release_id = NEW.release_id AND r.approval_id = NEW.approval_id)",
        "revocation release approval scope mismatch",
    )
    return result
