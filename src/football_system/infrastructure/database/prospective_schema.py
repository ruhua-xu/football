"""Additive pv_* ledger with typed dependencies and deferred event/completeness seals."""

import sqlalchemy as sa


def ref(path, table, key="artifact_id", nullable=False):
    return path, table, key, nullable


TYPE_SPECS = {
    "PROSPECTIVE_POLICY_V1": ("pv_policies", {}),
    "VALIDATION_EPOCH_V1": ("pv_epochs", {"policy_id": ref("policy.artifact_id", "pv_policies"), "seed_plan_id": ref("seed_plan.artifact_id", "mm_plans"),
        "return_policy_id": ref("configuration.return_policy.artifact_id", "rd_policies"), "objective_id": ref("configuration.objective_profile.artifact_id", "rd_objectives"),
        "previous_epoch_id": ref("previous_epoch.artifact_id", "pv_epochs", nullable=True)}),
    "VALIDATION_EPOCH_CLOSE_V1": ("pv_epoch_closes", {"epoch_id": ref("epoch.artifact_id", "pv_epochs")}),
    "EVIDENCE_SNAPSHOT_V1": ("pv_evidence", {"match_id": ref("claim.match_id", "matches", "internal_match_id")}),
    "PROSPECTIVE_EVIDENCE_BINDING_V1": ("pv_evidence_bindings", {"snapshot_id": ref("snapshot.artifact_id", "pv_evidence"),
        "football_id": ref("football_evidence.artifact_id", "mm_evidence"), "match_id": ref("match_id", "matches", "internal_match_id")}),
    "PROSPECTIVE_RUN_V1": ("pv_runs", {"epoch_id": ref("epoch.artifact_id", "pv_epochs"), "analysis_id": ref("analysis.artifact_id", "mm_analyses"),
        "packet_id": ref("packet.artifact_id", "mm_packets", nullable=True), "previous_run_id": ref("supersedes.artifact_id", "pv_runs", nullable=True)}),
    "V4_CORRECTION_AUDIT_V1": ("pv_review_audits", {"run_id": ref("run.artifact_id", "pv_runs"), "review_id": ref("review.artifact_id", "mm_reviews")}),
    "DECISION_LOCK_V1": ("pv_locks", {"run_id": ref("run.artifact_id", "pv_runs"), "epoch_id": ref("epoch.artifact_id", "pv_epochs"),
        "analysis_id": ref("analysis.artifact_id", "mm_analyses"), "packet_id": ref("packet.artifact_id", "mm_packets"),
        "review_id": ref("review.artifact_id", "mm_reviews"), "audit_id": ref("correction_audit.artifact_id", "pv_review_audits"),
        "fusion_id": ref("fusion.artifact_id", "mm_fusions"), "plan_id": ref("strategy_plan.artifact_id", "mm_plans"),
        "optimizer_id": ref("optimizer.artifact_id", "rd_runs"), "evaluation_id": ref("return_evaluation.artifact_id", "rd_evaluations")}),
    "PRE_KICKOFF_INVALIDATION_V1": ("pv_invalidations", {"old_run_id": ref("old_run.artifact_id", "pv_runs"), "old_lock_id": ref("old_lock.artifact_id", "pv_locks"),
        "new_run_id": ref("new_run.artifact_id", "pv_runs"), "new_lock_id": ref("replacement_lock.artifact_id", "pv_locks")}),
    "RESULT_OBSERVATION_V1": ("pv_observations", {"match_id": ref("claim.match_id", "matches", "internal_match_id"),
        "result_id": ref("normalized_result.match_result_id", "match_results", "match_result_id", True), "previous_id": ref("previous.artifact_id", "pv_observations", nullable=True)}),
    "PROSPECTIVE_SETTLEMENT_V1": ("pv_settlements", {"run_id": ref("run.artifact_id", "pv_runs"), "lock_id": ref("decision_lock.artifact_id", "pv_locks"),
        "previous_id": ref("previous.artifact_id", "pv_settlements", nullable=True)}),
    "PROSPECTIVE_VALIDATION_REPORT_V1": ("pv_reports", {"epoch_id": ref("epoch.artifact_id", "pv_epochs")}),
}
CHILD_SPECS = (
    ("VALIDATION_EPOCH_V1", "configuration.models", "pv_epoch_models", {}),
    ("VALIDATION_EPOCH_V1", "configuration.market_policies", "pv_epoch_market_policies", {}),
    ("EVIDENCE_SNAPSHOT_V1", "claim.structured_payload.result_ids", "pv_form_results", {"result_id": ref("", "match_results", "match_result_id")}),
    ("EVIDENCE_SNAPSHOT_V1", "claim.structured_payload.fixtures", "pv_schedule_fixtures", {"match_id": ref("match_id", "matches", "internal_match_id")}),
    ("PROSPECTIVE_RUN_V1", "identities", "pv_run_matches", {"match_id": ref("match_id", "matches", "internal_match_id")}),
    ("PROSPECTIVE_RUN_V1", "evidence", "pv_run_evidence", {"snapshot_id": ref("snapshot.artifact_id", "pv_evidence"),
        "binding_id": ref("binding.artifact_id", "pv_evidence_bindings"), "football_id": ref("football_evidence.artifact_id", "mm_evidence")}),
    ("PROSPECTIVE_RUN_V1", "input_artifact_hashes", "pv_run_inputs", {"target_id": ref("artifact_id", "mm_artifacts")}),
    ("V4_CORRECTION_AUDIT_V1", "reasons", "pv_correction_reasons", {"match_id": ref("match_id", "matches", "internal_match_id"),
        "context_id": ref("review_context_id", "mm_review_contexts")}),
    ("DECISION_LOCK_V1", "frames", "pv_lock_units", {"unit_id": ref("unit.artifact_id", "mm_analysis_units"),
        "match_id": ref("identity.match_id", "matches", "internal_match_id"), "sp_id": ref("sp_snapshot.artifact_id", "mm_sporttery_sp")}),
    ("DECISION_LOCK_V1", "selected", "pv_locked_tickets", {"candidate_id": ref("ticket_candidate_id", "mm_ticket_candidates")}),
    ("DECISION_LOCK_V1", "sp_hashes", "pv_locked_sp", {"sp_id": ref("artifact_id", "mm_sporttery_sp")}),
    ("PROSPECTIVE_SETTLEMENT_V1", "observations", "pv_settlement_observations", {"observation_id": ref("artifact_id", "pv_observations")}),
    ("PROSPECTIVE_VALIDATION_REPORT_V1", "census", "pv_report_census", {"run_id": ref("run.artifact_id", "pv_runs"),
        "lock_id": ref("decision_lock.artifact_id", "pv_locks", nullable=True), "invalidation_id": ref("invalidation.artifact_id", "pv_invalidations", nullable=True),
        "settlement_id": ref("settlement.artifact_id", "pv_settlements", nullable=True)}),
)
NESTED_SPECS = (
    ("V4_CORRECTION_AUDIT_V1", "reasons", "evidence_snapshot_ids", "pv_reason_evidence", "pv_correction_reasons", {"snapshot_id": ref("", "pv_evidence")}),
    ("DECISION_LOCK_V1", "frames", "layers", "pv_locked_layers", "pv_lock_units", {}),
)
OPERATIONS = {"EPOCH_CREATE": "VALIDATION_EPOCH_V1", "EPOCH_CLOSE": "VALIDATION_EPOCH_CLOSE_V1",
    "EVIDENCE_IMPORT": "PROSPECTIVE_EVIDENCE_BINDING_V1", "PREPARE": "PROSPECTIVE_RUN_V1",
    "REVIEW_IMPORT": "V4_CORRECTION_AUDIT_V1", "LOCK": "DECISION_LOCK_V1", "RESULT_IMPORT": "RESULT_OBSERVATION_V1",
    "SETTLE": "PROSPECTIVE_SETTLEMENT_V1", "REPORT": "PROSPECTIVE_VALIDATION_REPORT_V1"}
PROSPECTIVE_TABLES = ("pv_artifacts", *(s[0] for s in TYPE_SPECS.values()), *(s[2] for s in CHILD_SPECS),
                      *(s[3] for s in NESTED_SPECS), "pv_seals", "pv_receipts")


def prospective_tables(metadata):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)
    def fk(keys, table, targets=None, alter=False):
        return sa.ForeignKeyConstraint(keys, [f"{table}.{key}" for key in (targets or keys)], ondelete="RESTRICT", deferrable=True, initially="DEFERRED", use_alter=alter)
    external = {("mm_market_keys", "market_hash")}
    for _, fields in TYPE_SPECS.values():
        external.update((table, key) for _, table, key, _ in fields.values() if not table.startswith("pv_"))
    for spec in (*CHILD_SPECS, *NESTED_SPECS):
        external.update((table, key) for _, table, key, _ in spec[-1].values() if not table.startswith("pv_"))
    for table, key in sorted(external):
        if table not in metadata.tables:
            sa.Table(table, metadata, c(key, primary_key=True))
    tables = {}
    def t(name, *items):
        tables[name] = sa.Table(name, metadata, *items)
    versions = ",".join(f"'{kind}'" for kind in TYPE_SPECS)
    t("pv_artifacts", c("artifact_id", primary_key=True), c("schema_version"), c("content_hash", sa.String(64), unique=True), c("artifact_json", sa.Text()),
      c("event_key"), c("recorded_at_utc"), c("recorded_at_us", sa.BigInteger()), c("clock_basis"),
      fk(["artifact_id"], "pv_seals", alter=True), fk(["event_key"], "pv_receipts", ["request_key"], alter=True),
      sa.CheckConstraint(f"schema_version IN ({versions})"), sa.CheckConstraint("json_valid(artifact_json) AND length(content_hash)=64"),
      sa.CheckConstraint("clock_basis IN ('LOCAL_SYSTEM_UTC','SYNTHETIC_TEST_CLOCK')"))
    for schema, (name, fields) in TYPE_SPECS.items():
        cols = [c("artifact_id", primary_key=True), fk(["artifact_id"], "pv_artifacts")]
        for key, (_, table, target, nullable) in fields.items():
            cols += [c(key, nullable=nullable), fk([key], table, [target])]
        if name == "pv_epochs":
            cols += [c("starts_us", sa.BigInteger()), c("ends_us", sa.BigInteger()), c("mode"), sa.CheckConstraint("starts_us<ends_us")]
        if name == "pv_runs":
            cols += [c("slate_key"), c("slate_date"), c("cutoff_us", sa.BigInteger()), c("earliest_us", sa.BigInteger()), c("status"), sa.UniqueConstraint("previous_run_id")]
        if name == "pv_evidence":
            cols += [c("category"), c("source_identity"), c("ingested_us", sa.BigInteger())]
        if name == "pv_locks":
            cols += [c("locked_us", sa.BigInteger()), c("earliest_us", sa.BigInteger()), sa.UniqueConstraint("run_id"), sa.CheckConstraint("locked_us<earliest_us")]
        if name == "pv_invalidations":
            cols += [c("invalidated_us", sa.BigInteger()), c("deadline_us", sa.BigInteger()), sa.CheckConstraint("invalidated_us<deadline_us"),
                     sa.UniqueConstraint("old_run_id"), sa.UniqueConstraint("old_lock_id"), sa.UniqueConstraint("new_run_id"), sa.UniqueConstraint("new_lock_id")]
        if name == "pv_observations":
            cols += [c("source_identity"), c("source_version_id", nullable=True), c("ingested_us", sa.BigInteger()),
                     sa.UniqueConstraint("previous_id"), sa.UniqueConstraint("source_identity", "match_id", "source_version_id")]
        if name == "pv_settlements":
            cols += [c("settled_us", sa.BigInteger()), sa.UniqueConstraint("previous_id")]
        if name == "pv_reports":
            cols += [c("as_of_us", sa.BigInteger())]
        if name == "pv_epoch_closes":
            cols += [sa.UniqueConstraint("epoch_id"), c("closed_us", sa.BigInteger())]
        if name == "pv_evidence_bindings":
            cols += [sa.UniqueConstraint("snapshot_id"), sa.UniqueConstraint("football_id")]
        t(name, *cols)
    for spec in (*CHILD_SPECS, *NESTED_SPECS):
        nested = len(spec) == 6
        if nested:
            _, _, _, name, parent, fields = spec
        else:
            schema, _, name, fields = spec
            parent = TYPE_SPECS[schema][0]
        cols = [c("parent_id", primary_key=True)]
        if nested:
            cols += [c("group_no", sa.Integer(), primary_key=True)]
        cols += [c("position", sa.Integer(), primary_key=True), c("item_json", sa.Text()), sa.CheckConstraint("position>=0 AND json_valid(item_json)")]
        cols += [fk(["parent_id", "group_no"], parent, ["parent_id", "position"]) if nested else fk(["parent_id"], parent, ["artifact_id"])]
        for key, (_, table, target, nullable) in fields.items():
            cols += [c(key, nullable=nullable), fk([key], table, [target])]
        if name == "pv_lock_units":
            cols += [c("market_hash"), fk(["market_hash"], "mm_market_keys"), sa.UniqueConstraint("parent_id", "match_id", "market_hash")]
        t(name, *cols)
    t("pv_seals", c("artifact_id", primary_key=True), fk(["artifact_id"], "pv_artifacts"))
    t("pv_receipts", c("request_key", primary_key=True), c("operation"), c("request_hash", sa.String(64)), c("request_json", sa.Text()),
      c("sequence", sa.Integer(), unique=True), sa.CheckConstraint("sequence>0"),
      c("response_id"), c("recorded_at_utc"), c("recorded_at_us", sa.BigInteger()), c("clock_basis"), fk(["response_id"], "pv_artifacts", ["artifact_id"]),
      sa.CheckConstraint("operation IN ("+",".join(f"'{name}'" for name in OPERATIONS)+")"),
      sa.CheckConstraint("json_valid(request_json) AND length(request_hash)=64"),
      sa.CheckConstraint("clock_basis IN ('LOCAL_SYSTEM_UTC','SYNTHETIC_TEST_CLOCK')"))
    return tables


def prospective_triggers():
    result = {}
    def trigger(name, table, when, operation="INSERT"):
        result[name] = f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {when} BEGIN SELECT RAISE(ABORT,'immutable prospective ledger violation'); END"
    for name, table in prospective_tables(sa.MetaData()).items():
        for operation in ("UPDATE", "DELETE"):
            trigger(f"trg_{name}_{operation.lower()}_v1", name, "1", operation)
        keys = " AND ".join(f"x.{col.name}=NEW.{col.name}" for col in table.primary_key.columns)
        trigger(f"trg_{name}_replace_v1", name, f"EXISTS(SELECT 1 FROM {name} x WHERE {keys})")
    trigger("trg_pv_header_v1", "pv_artifacts", "json_extract(NEW.artifact_json,'$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.artifact_json,'$.content_hash') IS NOT NEW.content_hash OR json_extract(NEW.artifact_json,'$.schema_version') IS NOT NEW.schema_version OR NOT EXISTS(SELECT 1 FROM pv_receipts r WHERE r.request_key=NEW.event_key AND r.recorded_at_us=NEW.recorded_at_us AND r.recorded_at_utc=NEW.recorded_at_utc AND r.clock_basis=NEW.clock_basis)")
    trigger("trg_pv_clock_v1", "pv_receipts", "NEW.sequence<>COALESCE((SELECT MAX(sequence) FROM pv_receipts),0)+1 OR NEW.recorded_at_us < COALESCE((SELECT MAX(recorded_at_us) FROM pv_receipts),NEW.recorded_at_us) OR (NEW.clock_basis='LOCAL_SYSTEM_UTC' AND ABS(NEW.recorded_at_us-CAST(strftime('%s','now') AS INTEGER)*1000000)>30000000)")
    for schema, (name, fields) in TYPE_SPECS.items():
        checks = [f"a.schema_version='{schema}'"] + [f"NEW.{key} IS json_extract(a.artifact_json,'$.{path}')" for key, (path, *_) in fields.items()]
        trigger(f"trg_{name}_binding_v1", name, "EXISTS(SELECT 1 FROM pv_seals WHERE artifact_id=NEW.artifact_id) OR NOT EXISTS(SELECT 1 FROM pv_artifacts a WHERE a.artifact_id=NEW.artifact_id AND "+" AND ".join(checks)+")")
    for spec in (*CHILD_SPECS, *NESTED_SPECS):
        if len(spec) == 4:
            schema, path, name, fields = spec
            expression = f"'$.{path}['||NEW.position||']'"
        else:
            schema, outer, inner, name, _, fields = spec
            expression = f"'$.{outer}['||NEW.group_no||'].{inner}['||NEW.position||']'"
        checks = [f"a.schema_version='{schema}'", f"json(NEW.item_json) IS json_quote(json_extract(a.artifact_json,{expression}))"]
        checks += [f"NEW.{key} IS json_extract(NEW.item_json,'{'$.'+path if path else '$'}')" for key, (path, *_) in fields.items()]
        if name == "pv_lock_units":
            checks.append("EXISTS(SELECT 1 FROM mm_market_keys k WHERE k.market_hash=NEW.market_hash AND json(k.market_json)=json(json_extract(NEW.item_json,'$.market_key')))")
        trigger(f"trg_{name}_binding_v1", name, "EXISTS(SELECT 1 FROM pv_seals WHERE artifact_id=NEW.parent_id) OR NOT EXISTS(SELECT 1 FROM pv_artifacts a WHERE a.artifact_id=NEW.parent_id AND "+" AND ".join(checks)+")")
    cases = []
    for schema, (name, _) in TYPE_SPECS.items():
        checks = [f"a.schema_version='{schema}'", f"EXISTS(SELECT 1 FROM {name} WHERE artifact_id=a.artifact_id)"]
        checks += [f"(SELECT COUNT(*) FROM {child} WHERE parent_id=a.artifact_id)=COALESCE(json_array_length(a.artifact_json,'$.{path}'),0)" for kind, path, child, _ in CHILD_SPECS if kind == schema]
        for kind, _, inner, child, parent, _ in NESTED_SPECS:
            if kind == schema:
                checks.append(f"NOT EXISTS(SELECT 1 FROM {parent} p WHERE p.parent_id=a.artifact_id AND (json_type(p.item_json,'$.{inner}') IS NOT 'array' OR (SELECT COUNT(*) FROM {child} c WHERE c.parent_id=p.parent_id AND c.group_no=p.position)<>json_array_length(p.item_json,'$.{inner}')))")
        cases.append("("+" AND ".join(checks)+")")
    root_cases = " OR ".join(f"(r.operation='{op}' AND a.schema_version='{kind}')" for op, kind in OPERATIONS.items())
    trigger("trg_pv_complete_v1", "pv_seals", "NOT EXISTS(SELECT 1 FROM pv_artifacts a JOIN pv_receipts r ON r.request_key=a.event_key WHERE a.artifact_id=NEW.artifact_id AND (r.response_id<>a.artifact_id OR ("+root_cases+")) AND ("+" OR ".join(cases)+"))")
    trigger("trg_pv_run_root_v1", "pv_runs", "(NEW.previous_run_id IS NULL AND EXISTS(SELECT 1 FROM pv_runs r WHERE r.epoch_id=NEW.epoch_id AND r.slate_key=NEW.slate_key AND r.slate_date=NEW.slate_date AND r.previous_run_id IS NULL)) OR (NEW.previous_run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM pv_runs r WHERE r.artifact_id=NEW.previous_run_id AND r.epoch_id=NEW.epoch_id AND r.slate_key=NEW.slate_key AND r.slate_date=NEW.slate_date))")
    trigger("trg_pv_lock_gate_v1", "pv_locks", "NOT EXISTS(SELECT 1 FROM pv_runs r JOIN pv_epochs e ON e.artifact_id=r.epoch_id WHERE r.artifact_id=NEW.run_id AND r.epoch_id=NEW.epoch_id AND r.status='PREPARING' AND r.cutoff_us<=NEW.locked_us AND r.earliest_us=NEW.earliest_us AND e.starts_us<=NEW.locked_us AND NEW.locked_us<e.ends_us) OR EXISTS(SELECT 1 FROM pv_epoch_closes WHERE epoch_id=NEW.epoch_id) OR EXISTS(SELECT 1 FROM pv_invalidations WHERE old_run_id=NEW.run_id)")
    trigger("trg_pv_prediction_unique_v1", "pv_lock_units", "EXISTS(SELECT 1 FROM pv_lock_units u JOIN pv_locks old ON old.artifact_id=u.parent_id JOIN pv_locks current ON current.artifact_id=NEW.parent_id WHERE old.epoch_id=current.epoch_id AND u.match_id=NEW.match_id AND u.market_hash=NEW.market_hash AND NOT EXISTS(SELECT 1 FROM pv_invalidations i WHERE i.old_run_id=old.run_id))")
    trigger("trg_pv_invalidation_gate_v1", "pv_invalidations", "NOT EXISTS(SELECT 1 FROM pv_locks old JOIN pv_runs r ON r.artifact_id=NEW.new_run_id WHERE old.artifact_id=NEW.old_lock_id AND old.run_id=NEW.old_run_id AND r.previous_run_id=NEW.old_run_id AND r.epoch_id=old.epoch_id AND NEW.invalidated_us<old.earliest_us AND NEW.invalidated_us<r.earliest_us)")
    trigger("trg_pv_observation_chain_v1", "pv_observations", "(NEW.previous_id IS NULL AND EXISTS(SELECT 1 FROM pv_observations WHERE match_id=NEW.match_id AND source_identity=NEW.source_identity AND previous_id IS NULL)) OR (NEW.previous_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM pv_observations p WHERE p.artifact_id=NEW.previous_id AND p.match_id=NEW.match_id AND p.source_identity=NEW.source_identity AND p.ingested_us<NEW.ingested_us))")
    trigger("trg_pv_settlement_chain_v1", "pv_settlements", "(NEW.previous_id IS NULL AND EXISTS(SELECT 1 FROM pv_settlements WHERE run_id=NEW.run_id)) OR (NEW.previous_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM pv_settlements p WHERE p.artifact_id=NEW.previous_id AND p.run_id=NEW.run_id AND p.lock_id=NEW.lock_id AND p.settled_us<=NEW.settled_us)) OR EXISTS(SELECT 1 FROM pv_invalidations WHERE old_run_id=NEW.run_id)")
    return result


def install_prospective_triggers(connection):
    if connection.scalar(sa.text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='pv_artifacts'")):
        for name, statement in prospective_triggers().items():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            connection.exec_driver_sql(statement)
