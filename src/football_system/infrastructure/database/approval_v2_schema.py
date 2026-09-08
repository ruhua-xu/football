"""Frozen replacement approval guards for d3fc0729e465. No row/column rewrite.

SQL checks projections and closure, not cryptographic hashes or local authority.
Those remain mandatory at the repository boundary for both wire versions.
"""


def approval_v2_trigger_sql():
    def extract(path, document="NEW.artifact_json"):
        return f"json_extract({document}, '$.{path}')"

    def utc(value):
        # SQLAlchemy SQLite DateTime stores six fractional digits, including zero.
        # Preserve microseconds; julianday/strftime alone silently lose precision.
        return (
            f"(CASE WHEN substr({value}, -1) <> 'Z' OR julianday({value}) IS NULL THEN NULL "
            f"WHEN length({value}) = 20 THEN replace(substr({value}, 1, 19), 'T', ' ') || '.000000' "
            f"ELSE replace(substr({value}, 1, length({value}) - 1), 'T', ' ') END)"
        )

    content = "content_payload"
    subject = f"{content}.approval_payload"
    review = f"{content}.reviewer_attestation.content_payload"
    schema = extract("schema_version")
    v2 = f"{schema} = 'TRAINING_HISTORY_APPROVAL_V2'"
    recorded = (
        f"CASE WHEN {v2} THEN {extract(content + '.recorded_at_utc')} "
        f"ELSE {extract(subject + '.approved_at_utc')} END"
    )
    persisted = (
        f"CASE WHEN {v2} THEN {extract(content + '.persisted_at_utc')} "
        f"ELSE {extract(subject + '.persisted_at_utc')} END"
    )
    checks = [
        f"{schema} IS NULL OR {schema} NOT IN ('TRAINING_HISTORY_APPROVAL_V1', 'TRAINING_HISTORY_APPROVAL_V2')",
        f"{extract(subject + '.payload_version')} IS NOT CASE WHEN {v2} THEN 'TRAINING_HISTORY_APPROVAL_PAYLOAD_V2' ELSE 'TRAINING_HISTORY_APPROVAL_PAYLOAD_V1' END",
        f"{extract('artifact_id')} IS NOT NEW.approval_id",
        f"{extract('content_hash')} IS NOT NEW.approval_hash",
        f"{extract(subject + '.manifest.artifact_id')} IS NOT NEW.manifest_id",
        f"NOT EXISTS (SELECT 1 FROM training_history_manifests m WHERE m.manifest_id = NEW.manifest_id AND m.manifest_hash = {extract(subject + '.manifest.content_hash')} AND m.persisted_at_utc <= {utc(extract(review + '.reviewed_at_utc'))})",
        f"{utc(recorded)} IS NOT NEW.approved_at_utc",
        f"{utc(persisted)} IS NOT NEW.persisted_at_utc",
        f"{utc(extract(review + '.reviewed_at_utc'))} IS NULL OR {utc(extract(review + '.reviewed_at_utc'))} > NEW.approved_at_utc",
        f"{extract(review + '.attested_schema_version')} IS NOT {extract(subject + '.payload_version')}",
        f"{extract(review + '.attested_payload_hash')} IS NOT {extract(content + '.approval_payload_hash')}",
        f"{extract(review + '.authorized_reviewer')} IS NOT {extract(subject + '.approver')}",
        f"{extract(review + '.reviewer_authority_reference')} IS NOT {extract(subject + '.authority_reference')}",
        f"{extract(review + '.authority_sha256')} IS NOT {extract(subject + '.authority_sha256')}",
        f"{extract(subject + '.supersedes_approval.artifact_id')} IS NOT NEW.supersedes_approval_id",
        "(SELECT COUNT(*) FROM training_history_approval_grants g WHERE g.approval_id = NEW.approval_id) <> 4",
        f"json_array_length(NEW.artifact_json, '$.{subject}.grants') IS NOT 4",
        f"EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.{subject}.grants') j WHERE NOT EXISTS (SELECT 1 FROM training_history_approval_grants g WHERE g.approval_id = NEW.approval_id AND g.grant = json_extract(j.value, '$.grant') AND g.artifact_json = j.value))",
        f"EXISTS (SELECT 1 FROM training_history_approval_grants g WHERE g.approval_id = NEW.approval_id AND NOT EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.{subject}.grants') j WHERE g.artifact_json = j.value))",
        "(NEW.supersedes_approval_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM training_history_successors s WHERE s.predecessor_id = NEW.supersedes_approval_id AND s.successor_id = NEW.approval_id))",
        "(NEW.supersedes_approval_id IS NULL AND EXISTS (SELECT 1 FROM training_history_successors s WHERE s.successor_id = NEW.approval_id))",
        f"EXISTS (SELECT 1 FROM training_history_successors s WHERE s.successor_id = NEW.approval_id AND (s.successor_persisted_at_utc IS NOT NEW.persisted_at_utc OR s.effective_at_utc IS NOT {utc(extract(subject + '.supersession_effective_at_utc'))}))",
        f"{extract('operation', 'NEW.request_json')} IS NOT 'record_approval'",
        f"{extract('request_key', 'NEW.request_json')} IS NOT NEW.request_key",
        f"{extract('operator_id', 'NEW.request_json')} IS NOT NEW.operator_id",
        f"{extract('payload.approval_payload', 'NEW.request_json')} IS NOT {extract(subject)}",
        f"{extract('payload.reviewer_attestation', 'NEW.request_json')} IS NOT {extract(content + '.reviewer_attestation')}",
    ]
    retention_until = utc("json_extract(j.value, '$.retention.retain_until_at_utc')")
    v2_checks = (
        [
            f"json_type(NEW.artifact_json, '$.{subject}.{key}') IS NOT NULL"
            for key in ("approved_at_utc", "recorded_at_utc", "persisted_at_utc")
        ]
        + [
            f"{extract(content + '.' + key)} IS NOT NEW.{key}"
            for key in ("operator_id", "request_key", "request_sha256")
        ]
        + [
            f"{extract(subject + '.retention_compatibility')} IS NOT 'APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE'",
            "EXISTS (SELECT 1 FROM training_history_approval_grants g WHERE g.approval_id = NEW.approval_id AND (g.effective_at_utc > NEW.approved_at_utc OR (g.expires_at_utc IS NOT NULL AND g.expires_at_utc <= NEW.persisted_at_utc)))",
            f"EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.{subject}.grants') j "
            "WHERE json_extract(j.value, '$.retention.indefinite') = 0 "
            f"AND ({retention_until} IS NULL OR {retention_until} < NEW.persisted_at_utc))",
        ]
    )
    checks.append(f"({v2} AND ({' OR '.join(v2_checks)}))")
    result = {
        "trg_training_history_approval_events_complete_insert": "CREATE TRIGGER trg_training_history_approval_events_complete_insert "
        "BEFORE INSERT ON training_history_approval_events WHEN "
        + " OR ".join(checks)
        + " BEGIN SELECT RAISE(ABORT, 'approval requires exact V1/V2 schema time grants and successor graph'); END"
    }
    grant = extract("grant")
    retention_grant = (
        f"{grant} IN ('AUDIT_HASH_RETENTION', 'DERIVED_MODEL_STATE_RETENTION')"
    )
    until = utc(extract("retention.retain_until_at_utc"))
    retention_checks = [
        f"{extract('retention.schema_version')} IS NOT 'RETENTION_HORIZON_V1'",
        "json_type(NEW.artifact_json, '$.retention_rule') IS NOT 'text'",
        f"length(trim({extract('retention_rule')})) = 0",
        "(json_type(NEW.artifact_json, '$.retention.indefinite') IS NOT 'true' AND "
        "json_type(NEW.artifact_json, '$.retention.indefinite') IS NOT 'false')",
        f"({extract('retention.indefinite')} = 1 AND "
        "(json_type(NEW.artifact_json, '$.retention.retain_until_at_utc') IS NOT 'null' OR NEW.expires_at_utc IS NOT NULL))",
        f"({extract('retention.indefinite')} = 0 AND ({until} IS NULL "
        f"OR {until} < NEW.effective_at_utc OR (NEW.expires_at_utc IS NOT NULL AND {until} > NEW.expires_at_utc)))",
    ]
    grant_checks = [
        f"{extract('schema_version')} IS NOT 'PRODUCTION_GRANT_V1'",
        f"{grant} IS NOT NEW.grant",
        f"{utc(extract('effective_at_utc'))} IS NOT NEW.effective_at_utc",
        f"{utc(extract('expires_at_utc'))} IS NOT NEW.expires_at_utc",
        f"({retention_grant} AND ({' OR '.join(retention_checks)}))",
        f"(NOT ({retention_grant}) AND (json_type(NEW.artifact_json, '$.retention') IS NOT 'null' "
        "OR json_type(NEW.artifact_json, '$.retention_rule') IS NOT 'null'))",
    ]
    result["trg_training_history_approval_grants_json_insert"] = (
        "CREATE TRIGGER trg_training_history_approval_grants_json_insert "
        "BEFORE INSERT ON training_history_approval_grants WHEN "
        + " OR ".join(grant_checks)
        + " BEGIN SELECT RAISE(ABORT, 'approval grant JSON/time/retention projection mismatch'); END"
    )
    # The config hash pins the exact fixed model. Scope identity is independent
    # of the JSON encoding and of reviewer/evidence reference names.
    same_scope = " AND ".join(
        f"{extract(subject + '.scope.' + key, 'a.artifact_json')} = {extract(subject + '.scope.' + key)}"
        for key in (
            "competition_id",
            "pilot_target_season_id",
            "production_target_season_id",
            "training_window_hash",
            "model.model_name",
            "model.model_version",
            "model.config_hash",
        )
    )
    reviewed_at = utc(extract(review + ".reviewed_at_utc"))
    reused_review = (
        "EXISTS (SELECT 1 FROM training_history_approval_events a WHERE "
        f"{extract('schema_version', 'a.artifact_json')} = 'TRAINING_HISTORY_APPROVAL_V2' AND "
        f"{extract(content + '.approval_payload_hash', 'a.artifact_json')} = {extract(content + '.approval_payload_hash')} AND ("
        f"{extract(content + '.reviewer_attestation.attestation_hash', 'a.artifact_json')} = {extract(content + '.reviewer_attestation.attestation_hash')} OR "
        f"{extract(review + '.evidence.evidence_sha256', 'a.artifact_json')} = {extract(review + '.evidence.evidence_sha256')}))"
    )
    reset_scope = (
        "EXISTS (SELECT 1 FROM training_history_approval_events a WHERE "
        f"{same_scope} AND NOT EXISTS (SELECT 1 FROM training_history_approval_events child "
        "WHERE child.supersedes_approval_id = a.approval_id) "
        "AND a.approval_id IS NOT NEW.supersedes_approval_id)"
    )
    invalid_predecessor = (
        "(NEW.supersedes_approval_id IS NOT NULL AND NOT EXISTS "
        f"(SELECT 1 FROM training_history_approval_events a WHERE {same_scope} "
        "AND a.approval_id = NEW.supersedes_approval_id "
        f"AND a.approval_hash = {extract(subject + '.supersedes_approval.content_hash')} "
        f"AND a.persisted_at_utc <= {reviewed_at}))"
    )
    unacknowledged_withdrawal = (
        "EXISTS (SELECT 1 FROM training_history_revocation_events r "
        "JOIN training_history_approval_events a ON a.approval_id = r.approval_id "
        f"WHERE {same_scope} AND (r.recorded_at_utc > {reviewed_at} OR EXISTS "
        "(SELECT 1 FROM json_each(r.artifact_json, '$.content_payload.affected_grants') withdrawn "
        f"WHERE NOT EXISTS (SELECT 1 FROM json_each(NEW.artifact_json, '$.{subject}.superseded_grants') renewed "
        "WHERE renewed.value = withdrawn.value))))"
    )
    result["trg_training_history_approval_events_review_insert"] = (
        "CREATE TRIGGER trg_training_history_approval_events_review_insert "
        f"BEFORE INSERT ON training_history_approval_events WHEN {v2} AND ("
        + " OR ".join(
            (reused_review, reset_scope, invalid_predecessor, unacknowledged_withdrawal)
        )
        + ") BEGIN SELECT RAISE(ABORT, 'approval review replay or missing explicit fresh successor policy'); END"
    )
    return result


def install_approval_v2_triggers(connection):
    if connection.dialect.name == "sqlite":
        for name, statement in approval_v2_trigger_sql().items():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            connection.exec_driver_sql(statement)
