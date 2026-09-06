"""Frozen additive SQLite schema V1 shared by runtime and revision 8a7c2f4e9b10.

Do not extend this factory for future revisions; add a new version instead.
Deferred parent FKs allow children first, followed by the sealed admission in
one transaction. Inserting the parent closes the graph permanently.
"""

import sqlalchemy as sa


TRAINING_ADMISSION_TABLES = (
    "source_rights_admissions",
    "training_capture_receipts",
    "training_fact_admissions",
    "training_fact_fixture_sources",
    "match_season_memberships",
    "match_result_admissions",
    "training_fact_bindings",
)


def training_admission_tables_v1(
    metadata: sa.MetaData, datetime_type: sa.types.TypeEngine
) -> dict[str, sa.Table]:
    def column(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=False, **kw)

    def fk(name, target, *, deferred=False):
        return sa.ForeignKeyConstraint(
            [name],
            [target],
            ondelete="RESTRICT",
            **({"deferrable": True, "initially": "DEFERRED"} if deferred else {}),
        )

    def common():
        return [column("artifact_json", sa.Text()), column("row_sha256", sa.String(64))]

    def request():
        return [
            column("request_key", unique=True),
            column("request_sha256", sa.String(64), unique=True),
            column("request_json", sa.Text()),
            column("operator_id"),
        ]

    def child():
        return [
            column("training_fact_admission_id"),
            column("internal_match_id"),
            column("provider_mapping_id"),
            fk(
                "training_fact_admission_id",
                "training_fact_admissions.training_fact_admission_id",
                deferred=True,
            ),
            fk("internal_match_id", "matches.internal_match_id"),
            fk("provider_mapping_id", "provider_match_mappings.mapping_id"),
        ]

    def table(name, *items):
        items = list(items)
        for item in list(items):
            if isinstance(item, sa.Column) and item.name.endswith(("sha256", "hash")):
                items.append(
                    sa.CheckConstraint(
                        f"length({item.name}) = 64 AND {item.name} NOT GLOB '*[^0-9a-f]*'",
                        name=f"ck_{name}_{item.name}",
                    )
                )
            if isinstance(item, sa.Column) and item.name.endswith("_json"):
                items.append(
                    sa.CheckConstraint(
                        f"json_valid({item.name}) = 1 AND json_type({item.name}) = 'object'",
                        name=f"ck_{name}_{item.name}",
                    )
                )
        return sa.Table(name, metadata, *items)

    tables = [
        table(
            "source_rights_admissions",
            column("source_rights_admission_id", primary_key=True),
            column("admission_hash", sa.String(64), unique=True),
            column("recorded_at_utc", datetime_type),
            *common(),
            *request(),
        ),
        table(
            "training_capture_receipts",
            column("capture_receipt_id", primary_key=True),
            column("source_rights_admission_id"),
            column("provider_id"),
            column("source_id"),
            column("payload_sha256", sa.String(64)),
            column("payload_bytes", sa.LargeBinary()),
            column("local_imported_at_utc", datetime_type),
            column("archive_created_at_utc", datetime_type),
            column("registered_at_utc", datetime_type),
            *common(),
            *request(),
            fk(
                "source_rights_admission_id",
                "source_rights_admissions.source_rights_admission_id",
            ),
            fk("provider_id", "providers.provider_id"),
            sa.CheckConstraint(
                "local_imported_at_utc <= archive_created_at_utc AND archive_created_at_utc <= registered_at_utc",
                name="ck_training_capture_timeline",
            ),
        ),
        table(
            "training_fact_admissions",
            column("training_fact_admission_id", primary_key=True),
            column("source_rights_admission_id"),
            column("admission_hash", sa.String(64), unique=True),
            column("admitted_fact_count", sa.Integer()),
            column("admitted_fact_root", sa.String(64)),
            column("actual_started_at_utc", datetime_type),
            column("actual_completed_at_utc", datetime_type),
            column("persisted_at_utc", datetime_type),
            *common(),
            *request(),
            fk(
                "source_rights_admission_id",
                "source_rights_admissions.source_rights_admission_id",
            ),
            sa.CheckConstraint(
                "admitted_fact_count > 0", name="ck_training_admission_count"
            ),
            sa.CheckConstraint(
                "actual_started_at_utc <= actual_completed_at_utc AND actual_completed_at_utc <= persisted_at_utc",
                name="ck_training_admission_timeline",
            ),
        ),
        table(
            "training_fact_fixture_sources",
            *child(),
            column("fixture_source_id"),
            column("capture_receipt_id"),
            *common(),
            sa.PrimaryKeyConstraint("training_fact_admission_id", "fixture_source_id"),
            sa.UniqueConstraint("training_fact_admission_id", "internal_match_id"),
            fk("capture_receipt_id", "training_capture_receipts.capture_receipt_id"),
        ),
        table(
            "match_season_memberships",
            *child(),
            column("season_membership_id"),
            column("fixture_source_id"),
            column("capture_receipt_id"),
            column("canonical_competition_id"),
            column("canonical_season_id"),
            column("home_team_alias_id"),
            column("away_team_alias_id"),
            column("competition_mapping_id"),
            *common(),
            sa.PrimaryKeyConstraint(
                "training_fact_admission_id", "season_membership_id"
            ),
            sa.UniqueConstraint("training_fact_admission_id", "internal_match_id"),
            fk("capture_receipt_id", "training_capture_receipts.capture_receipt_id"),
            fk("canonical_competition_id", "competitions.competition_id"),
            fk("home_team_alias_id", "provider_team_aliases.alias_id"),
            fk("away_team_alias_id", "provider_team_aliases.alias_id"),
            fk("competition_mapping_id", "provider_competition_mappings.mapping_id"),
            sa.ForeignKeyConstraint(
                ["training_fact_admission_id", "fixture_source_id"],
                [
                    "training_fact_fixture_sources.training_fact_admission_id",
                    "training_fact_fixture_sources.fixture_source_id",
                ],
                ondelete="RESTRICT",
            ),
        ),
        table(
            "match_result_admissions",
            *child(),
            column("match_result_admission_id"),
            column("match_result_id"),
            column("capture_receipt_id"),
            *common(),
            sa.PrimaryKeyConstraint(
                "training_fact_admission_id", "match_result_admission_id"
            ),
            sa.UniqueConstraint("training_fact_admission_id", "internal_match_id"),
            sa.UniqueConstraint("training_fact_admission_id", "match_result_id"),
            fk("match_result_id", "match_results.match_result_id"),
            fk("capture_receipt_id", "training_capture_receipts.capture_receipt_id"),
        ),
        table(
            "training_fact_bindings",
            *child(),
            column("sequence", sa.Integer()),
            column("training_fact_binding_id"),
            column("fixture_source_id"),
            column("season_membership_id"),
            column("match_result_admission_id"),
            column("match_result_id"),
            *common(),
            sa.PrimaryKeyConstraint("training_fact_admission_id", "sequence"),
            sa.UniqueConstraint(
                "training_fact_admission_id", "training_fact_binding_id"
            ),
            sa.UniqueConstraint("training_fact_admission_id", "internal_match_id"),
            sa.UniqueConstraint("training_fact_admission_id", "match_result_id"),
            sa.CheckConstraint("sequence >= 0", name="ck_training_fact_sequence"),
            fk("match_result_id", "match_results.match_result_id"),
            *[
                sa.ForeignKeyConstraint(
                    ["training_fact_admission_id", key],
                    [f"{target}.training_fact_admission_id", f"{target}.{key}"],
                    ondelete="RESTRICT",
                )
                for key, target in (
                    ("fixture_source_id", "training_fact_fixture_sources"),
                    ("season_membership_id", "match_season_memberships"),
                    ("match_result_admission_id", "match_result_admissions"),
                )
            ],
        ),
    ]
    return {item.name: item for item in tables}


def training_admission_trigger_sql_v1() -> dict[str, str]:
    keys = {
        "source_rights_admissions": (
            ("source_rights_admission_id",),
            ("admission_hash",),
            ("request_key",),
            ("request_sha256",),
        ),
        "training_capture_receipts": (
            ("capture_receipt_id",),
            ("request_key",),
            ("request_sha256",),
        ),
        "training_fact_admissions": (
            ("training_fact_admission_id",),
            ("admission_hash",),
            ("request_key",),
            ("request_sha256",),
        ),
        "training_fact_fixture_sources": (
            ("training_fact_admission_id", "fixture_source_id"),
            ("training_fact_admission_id", "internal_match_id"),
        ),
        "match_season_memberships": (
            ("training_fact_admission_id", "season_membership_id"),
            ("training_fact_admission_id", "internal_match_id"),
        ),
        "match_result_admissions": (
            ("training_fact_admission_id", "match_result_admission_id"),
            ("training_fact_admission_id", "internal_match_id"),
            ("training_fact_admission_id", "match_result_id"),
        ),
        "training_fact_bindings": tuple(
            ("training_fact_admission_id", key)
            for key in (
                "sequence",
                "training_fact_binding_id",
                "internal_match_id",
                "match_result_id",
            )
        ),
    }
    result = {}

    def trigger(table, suffix, event, condition, message):
        name = f"trg_{table}_{suffix}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {event} ON {table} "
            f"{('WHEN ' + condition) if condition else ''} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )

    for table, key_sets in keys.items():
        conflict = " OR ".join(
            "(" + " AND ".join(f"existing.{key} = NEW.{key}" for key in group) + ")"
            for group in key_sets
        )
        trigger(
            table,
            "immutable_insert_existing",
            "INSERT",
            f"EXISTS (SELECT 1 FROM {table} existing WHERE {conflict})",
            "immutable training record already exists",
        )
        for action in ("UPDATE", "DELETE"):
            trigger(
                table,
                f"append_only_{action.lower()}",
                action,
                "",
                "training lineage is append-only",
            )
        if table in TRAINING_ADMISSION_TABLES[3:]:
            trigger(
                table,
                "sealed_insert",
                "INSERT",
                "EXISTS (SELECT 1 FROM training_fact_admissions p WHERE p.training_fact_admission_id = NEW.training_fact_admission_id)",
                "sealed training admission cannot gain children",
            )
            trigger(
                table,
                "identity_insert",
                "INSERT",
                "NOT EXISTS (SELECT 1 FROM provider_match_mappings m WHERE m.mapping_id = NEW.provider_mapping_id AND m.internal_match_id = NEW.internal_match_id)",
                "training identity lineage mismatch",
            )
    trigger(
        "match_result_admissions",
        "result_insert",
        "INSERT",
        "NOT EXISTS (SELECT 1 FROM match_results r WHERE r.match_result_id = NEW.match_result_id AND r.internal_match_id = NEW.internal_match_id AND r.provider_mapping_id = NEW.provider_mapping_id AND r.supersedes_match_result_id IS NULL AND r.ingested_at_utc = r.available_at_utc)",
        "training result lineage mismatch or controlled correction unsupported",
    )
    capture_fields = {
        "training_fact_fixture_sources": (
            "$",
            "fixture_source_archive_id",
            "fixture_source_archive_payload_sha256",
            "fixture_source_archive_created_at_utc",
        ),
        "match_season_memberships": (
            "$.content_payload",
            "provider_scope_raw_artifact_id",
            "provider_scope_payload_sha256",
            "provider_scope_created_at_utc",
        ),
        "match_result_admissions": (
            "$.content_payload",
            "raw_artifact_id",
            "raw_artifact_payload_sha256",
            "raw_artifact_created_at_utc",
        ),
    }
    for table, (root, artifact_id, digest, created) in capture_fields.items():
        checks = [
            f"json_extract(NEW.artifact_json, '{root}.{artifact_id}') = cap.capture_receipt_id",
            f"json_extract(NEW.artifact_json, '{root}.{digest}') = cap.payload_sha256",
            f"json_extract(NEW.artifact_json, '{root}.source_id') = cap.source_id",
            f"json_extract(NEW.artifact_json, '{root}.provider_code') = provider.code",
            f"json_extract(NEW.artifact_json, '{root}.internal_match_id') = NEW.internal_match_id",
            "mapping.provider_id = cap.provider_id",
            "json_extract(cap.artifact_json, '$.capture_receipt_id') = cap.capture_receipt_id",
            "json_extract(cap.artifact_json, '$.source_id') = cap.source_id",
            "json_extract(cap.artifact_json, '$.provider_code') = provider.code",
            "json_extract(cap.artifact_json, '$.source_rights_admission_id') = cap.source_rights_admission_id",
            "json_extract(cap.artifact_json, '$.payload_sha256') = cap.payload_sha256",
            "EXISTS (SELECT 1 FROM json_each(rights.artifact_json, '$.content_payload.rights_payload.source_ids') scope WHERE scope.value = cap.source_id)",
            *[
                f"json_extract(NEW.artifact_json, '{root}.{child_field}') = json_extract(cap.artifact_json, '$.{receipt_field}')"
                for child_field, receipt_field in (
                    (created, "archive_created_at_utc"),
                    ("local_imported_at_utc", "local_imported_at_utc"),
                    ("registered_at_utc", "registered_at_utc"),
                )
            ],
        ]
        trigger(
            table,
            "capture_insert",
            "INSERT",
            "NOT EXISTS (SELECT 1 FROM training_capture_receipts cap "
            "JOIN providers provider ON provider.provider_id = cap.provider_id "
            "JOIN provider_match_mappings mapping ON mapping.mapping_id = NEW.provider_mapping_id "
            "JOIN source_rights_admissions rights ON rights.source_rights_admission_id = cap.source_rights_admission_id "
            "WHERE cap.capture_receipt_id = NEW.capture_receipt_id AND "
            + " AND ".join(checks)
            + ")",
            "training child capture declaration or scope mismatch",
        )
    links = (
        ("training_fact_fixture_sources", "fixture_source_id"),
        ("match_season_memberships", "season_membership_id"),
        ("match_result_admissions", "match_result_admission_id"),
    )
    trigger(
        "training_fact_bindings",
        "lineage_insert",
        "INSERT",
        " OR ".join(
            f"NOT EXISTS (SELECT 1 FROM {table} c "
            "JOIN training_capture_receipts cap ON cap.capture_receipt_id = c.capture_receipt_id "
            "JOIN training_fact_fixture_sources fixture ON fixture.training_fact_admission_id = NEW.training_fact_admission_id AND fixture.fixture_source_id = NEW.fixture_source_id "
            "JOIN training_capture_receipts base ON base.capture_receipt_id = fixture.capture_receipt_id "
            f"WHERE c.training_fact_admission_id = NEW.training_fact_admission_id AND c.{key} = NEW.{key} AND c.internal_match_id = NEW.internal_match_id AND c.provider_mapping_id = NEW.provider_mapping_id "
            "AND cap.source_id = base.source_id AND cap.provider_id = base.provider_id AND cap.source_rights_admission_id = base.source_rights_admission_id"
            + (
                " AND c.match_result_id = NEW.match_result_id"
                if table == "match_result_admissions"
                else ""
            )
            + (
                " AND c.fixture_source_id = NEW.fixture_source_id"
                if table == "match_season_memberships"
                else ""
            )
            + ")"
            for table, key in links
        ),
        "training fact child lineage mismatch",
    )
    count_checks = [
        f"(SELECT COUNT(*) FROM {table} c WHERE c.training_fact_admission_id = NEW.training_fact_admission_id) <> NEW.admitted_fact_count"
        for table in TRAINING_ADMISSION_TABLES[3:]
    ]
    trigger(
        "training_fact_admissions",
        "complete_insert",
        "INSERT",
        " OR ".join(
            [
                *count_checks,
                "(SELECT MIN(sequence) FROM training_fact_bindings WHERE training_fact_admission_id = NEW.training_fact_admission_id) <> 0",
                "(SELECT MAX(sequence) FROM training_fact_bindings WHERE training_fact_admission_id = NEW.training_fact_admission_id) <> NEW.admitted_fact_count - 1",
                "json_array_length(NEW.artifact_json, '$.facts') IS NOT NEW.admitted_fact_count",
                "json_extract(NEW.artifact_json, '$.content_payload.admitted_fact_root') IS NOT NEW.admitted_fact_root",
                "EXISTS (SELECT 1 FROM training_fact_bindings c WHERE c.training_fact_admission_id = NEW.training_fact_admission_id AND json_extract(NEW.artifact_json, '$.facts[' || c.sequence || '].training_fact_binding_id') IS NOT c.training_fact_binding_id)",
            ]
        ),
        "training admission requires a complete sealed graph",
    )
    trigger(
        "training_fact_admissions",
        "capture_seal",
        "INSERT",
        " OR ".join(
            [
                "json_extract(NEW.artifact_json, '$.content_payload.source_rights_admission_id') IS NOT NEW.source_rights_admission_id",
                "NOT EXISTS (SELECT 1 FROM source_rights_admissions rights WHERE rights.source_rights_admission_id = NEW.source_rights_admission_id AND json_extract(NEW.artifact_json, '$.source_rights_admission') = json_extract(rights.artifact_json, '$'))",
                *[
                    f"EXISTS (SELECT 1 FROM {table} child JOIN training_capture_receipts cap ON cap.capture_receipt_id = child.capture_receipt_id WHERE child.training_fact_admission_id = NEW.training_fact_admission_id AND cap.source_rights_admission_id IS NOT NEW.source_rights_admission_id)"
                    for table in capture_fields
                ],
                "EXISTS (SELECT 1 FROM training_fact_bindings binding "
                "JOIN training_fact_fixture_sources fixture ON fixture.training_fact_admission_id = binding.training_fact_admission_id AND fixture.fixture_source_id = binding.fixture_source_id "
                "JOIN match_season_memberships season ON season.training_fact_admission_id = binding.training_fact_admission_id AND season.season_membership_id = binding.season_membership_id "
                "JOIN match_result_admissions result ON result.training_fact_admission_id = binding.training_fact_admission_id AND result.match_result_admission_id = binding.match_result_admission_id "
                "WHERE binding.training_fact_admission_id = NEW.training_fact_admission_id AND ("
                "json_extract(NEW.artifact_json, '$.facts[' || binding.sequence || ']') IS NOT json_extract(binding.artifact_json, '$') OR "
                "json_extract(binding.artifact_json, '$.content_payload.fixture_source') IS NOT json_extract(fixture.artifact_json, '$') OR "
                "json_extract(binding.artifact_json, '$.content_payload.season_membership') IS NOT json_extract(season.artifact_json, '$') OR "
                "json_extract(binding.artifact_json, '$.content_payload.match_result_admission') IS NOT json_extract(result.artifact_json, '$')))",
            ]
        ),
        "training admission capture rights or artifact projection mismatch",
    )
    return result
