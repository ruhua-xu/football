"""Closed additive return graph. No changes to any v0.8 table or trigger."""

import sqlalchemy as sa

# JSON path, concrete FK table, concrete FK key, nullable
TYPE_SPECS = {
    "RETURN_DISTRIBUTION_POLICY_V1": ("rd_policies", {}),
    "RETURN_OBJECTIVE_PROFILE_V1": ("rd_objectives", {}),
    "RELEVANT_MATCH_STATE_V1": ("rd_match_states", {
        "match_id": ("match_id", "matches", "internal_match_id", False),
        "unit_id": ("unit.artifact_id", "mm_analysis_units", "artifact_id", False),
        "fusion_id": ("fusion.artifact_id", "mm_fusions", "artifact_id", False),
        "sp_id": ("sp_snapshot.artifact_id", "mm_sporttery_sp", "artifact_id", False),
    }),
    "TICKET_RETURN_FUNCTION_V1": ("rd_ticket_functions", {
        "candidate_id": ("candidate.artifact_id", "mm_ticket_candidates", "artifact_id", False),
        "source_id": ("source.artifact_id", "mm_strategy_sources", "artifact_id", False),
    }),
    "PORTFOLIO_RETURN_DISTRIBUTION_V1": ("rd_distributions", {
        "plan_id": ("binding.plan.artifact_id", "mm_plans", "artifact_id", False),
        "source_id": ("binding.source.artifact_id", "mm_strategy_sources", "artifact_id", False),
        "policy_id": ("policy.artifact_id", "rd_policies", "artifact_id", False),
        "analysis_id": ("binding.analysis.artifact_id", "mm_analyses", "artifact_id", False),
        "fusion_id": ("binding.fusion.artifact_id", "mm_fusions", "artifact_id", False),
    }),
    "RETURN_DISTRIBUTION_METRICS_V1": ("rd_metrics", {
        "distribution_id": ("distribution.artifact_id", "rd_distributions", "artifact_id", False),
    }),
    "RETURN_EVALUATION_V1": ("rd_evaluations", {
        "plan_id": ("binding.plan.artifact_id", "mm_plans", "artifact_id", False),
        "source_id": ("binding.source.artifact_id", "mm_strategy_sources", "artifact_id", False),
        "analysis_id": ("binding.analysis.artifact_id", "mm_analyses", "artifact_id", False),
        "fusion_id": ("binding.fusion.artifact_id", "mm_fusions", "artifact_id", False),
        "policy_id": ("policy.artifact_id", "rd_policies", "artifact_id", False),
        "objective_id": ("objective.artifact_id", "rd_objectives", "artifact_id", False),
        "distribution_id": ("distribution.artifact_id", "rd_distributions", "artifact_id", True),
        "metrics_id": ("metrics.artifact_id", "rd_metrics", "artifact_id", True),
    }),
    "RETURN_OPTIMIZATION_RUN_V1": ("rd_runs", {
        "plan_id": ("binding.plan.artifact_id", "mm_plans", "artifact_id", False),
        "source_id": ("binding.source.artifact_id", "mm_strategy_sources", "artifact_id", False),
        "analysis_id": ("binding.analysis.artifact_id", "mm_analyses", "artifact_id", False),
        "fusion_id": ("binding.fusion.artifact_id", "mm_fusions", "artifact_id", False),
        "policy_id": ("policy.artifact_id", "rd_policies", "artifact_id", False),
        "objective_id": ("objective.artifact_id", "rd_objectives", "artifact_id", False),
        "baseline_id": ("baseline.artifact_id", "rd_evaluations", "artifact_id", False),
        "result_id": ("result.artifact_id", "rd_evaluations", "artifact_id", True),
        "legacy_id": ("legacy.artifact_id", "rd_evaluations", "artifact_id", True),
    }),
}

def REF(path, table, key="artifact_id", nullable=False):
    return path, table, key, nullable

CHILD_SPECS = (
    ("RELEVANT_MATCH_STATE_V1", "states", "rd_relevant_states", {}),
    ("TICKET_RETURN_FUNCTION_V1", "atomic_bets", "rd_function_atomics", {"atomic_id": REF("atomic.artifact_id", "mm_atomic_bets")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "matches", "rd_distribution_matches", {
        "state_id": REF("artifact_id", "rd_match_states"), "match_id": REF("match_id", "matches", "internal_match_id")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "ticket_functions", "rd_distribution_functions", {"function_id": REF("artifact_id", "rd_ticket_functions")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "allocations", "rd_distribution_allocations", {"candidate_id": REF("ticket_candidate_id", "mm_ticket_candidates")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "components", "rd_components", {}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "support", "rd_support", {}),
    ("RETURN_EVALUATION_V1", "requested", "rd_evaluation_requests", {"candidate_id": REF("ticket_candidate_id", "mm_ticket_candidates")}),
    ("RETURN_EVALUATION_V1", "selected", "rd_selected_tickets", {
        "candidate_id": REF("ticket_candidate_id", "mm_ticket_candidates"),
        "witness_atomic_id": REF("hedge_witness.surviving_atomic_bet_id", "mm_atomic_bets", nullable=True)}),
    ("RETURN_OPTIMIZATION_RUN_V1", "candidate_catalog", "rd_run_candidates", {"candidate_id": REF("artifact_id", "mm_ticket_candidates")}),
    ("RETURN_OPTIMIZATION_RUN_V1", "steps", "rd_steps", {"candidate_id": REF("ticket_candidate_id", "mm_ticket_candidates")}),
)
NESTED_SPECS = (
    ("TICKET_RETURN_FUNCTION_V1", "atomic_bets", "legs", "rd_atomic_requirements", "rd_function_atomics", {
        "outcome_candidate_id": REF("outcome_candidate.artifact_id", "mm_outcome_candidates"),
        "match_id": REF("match_id", "matches", "internal_match_id")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "components", "match_ids", "rd_component_matches", "rd_components", {"match_id": REF("", "matches", "internal_match_id")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "components", "ticket_candidate_ids", "rd_component_tickets", "rd_components", {"candidate_id": REF("", "mm_ticket_candidates")}),
    ("PORTFOLIO_RETURN_DISTRIBUTION_V1", "components", "support", "rd_component_support", "rd_components", {}),
)
SUPPORT_TABLES = {"rd_support", "rd_component_support"}
MULTIPLIER_TABLES = {"rd_distribution_allocations", "rd_evaluation_requests", "rd_selected_tickets", "rd_steps"}
RETURN_TABLES = ("rd_artifacts", *(t for t, _ in TYPE_SPECS.values()), *(s[2] for s in CHILD_SPECS), *(s[3] for s in NESTED_SPECS), "rd_seals")


def return_distribution_tables(metadata):
    def c(name, type_=sa.String(160), **kw):
        return sa.Column(name, type_, nullable=kw.pop("nullable", False), **kw)

    def fk(keys, table, targets=None, alter=False):
        return sa.ForeignKeyConstraint(keys, [f"{table}.{key}" for key in (targets or keys)],
            ondelete="RESTRICT", deferrable=True, initially="DEFERRED", use_alter=alter)

    external = {("matches", "internal_match_id"), ("mm_market_keys", "market_hash")}
    for _, fields in TYPE_SPECS.values():
        external.update((table, key) for _, table, key, _ in fields.values() if not table.startswith("rd_"))
    for spec in (*CHILD_SPECS, *NESTED_SPECS):
        external.update((table, key) for _, table, key, _ in spec[-1].values() if not table.startswith("rd_"))
    for table, key in sorted(external):
        if table not in metadata.tables:
            sa.Table(table, metadata, c(key, primary_key=True))
    if "mm_fusion_results" not in metadata.tables:
        sa.Table("mm_fusion_results", metadata, c("parent_id"), c("target_id"), sa.UniqueConstraint("parent_id", "target_id"))
    if "mm_outcome_catalog" not in metadata.tables:
        sa.Table("mm_outcome_catalog", metadata, c("market_hash", primary_key=True), c("outcome_key", primary_key=True))
    tables = {}
    def t(name, *items):
        tables[name] = sa.Table(name, metadata, *items)
        return tables[name]

    versions = ",".join(f"'{v}'" for v in TYPE_SPECS)
    t("rd_artifacts", c("artifact_id", primary_key=True), c("schema_version"),
      c("content_hash", sa.String(64), unique=True), c("artifact_json", sa.Text()),
      fk(["artifact_id"], "rd_seals", alter=True),
      sa.CheckConstraint(f"schema_version IN ({versions})"),
      sa.CheckConstraint("json_valid(artifact_json) AND json_type(artifact_json)='object' AND length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'"))
    for schema, (name, fields) in TYPE_SPECS.items():
        columns = [c("artifact_id", primary_key=True), fk(["artifact_id"], "rd_artifacts")]
        for col, (_, target, key, nullable) in fields.items():
            columns += [c(col, nullable=nullable), fk([col], target, [key])]
        if schema == "RELEVANT_MATCH_STATE_V1":
            columns += [c("market_hash"), fk(["market_hash"], "mm_market_keys"),
                        fk(["fusion_id", "unit_id"], "mm_fusion_results", ["parent_id", "target_id"])]
        t(name, *columns)

    def child_columns(name, fields, *, nested=False):
        cols = [c("parent_id", primary_key=True)]
        if nested:
            cols += [c("group_no", sa.Integer(), primary_key=True), sa.CheckConstraint("group_no>=0")]
        cols += [c("position", sa.Integer(), primary_key=True), c("item_json", sa.Text()),
                 sa.CheckConstraint("position>=0 AND json_valid(item_json)")]
        for col, (_, target, key, nullable) in fields.items():
            cols += [c(col, nullable=nullable), fk([col], target, [key])]
        group = ["parent_id", "group_no"] if nested else ["parent_id"]
        if name in SUPPORT_TABLES:
            cols += [c("gross_payout_fen", sa.BigInteger()), c("probability", sa.Text()),
                     sa.UniqueConstraint(*group, "gross_payout_fen"),
                     sa.CheckConstraint("typeof(gross_payout_fen)='integer' AND gross_payout_fen>=0")]
        if name == "rd_relevant_states":
            cols += [c("outcome_key", nullable=True), c("probability", sa.Text()), c("market_hash"),
                     sa.UniqueConstraint("parent_id", "outcome_key"),
                     fk(["market_hash", "outcome_key"], "mm_outcome_catalog")]
        if name in MULTIPLIER_TABLES:
            cols += [c("multiplier", sa.Integer()), sa.CheckConstraint("multiplier BETWEEN 1 AND 50")]
        if name == "rd_selected_tickets":
            cols += [c("role"), sa.CheckConstraint("role IN ('PRIMARY','SECONDARY','HEDGE','LONGSHOT')")]
        if name in {"rd_distribution_matches", "rd_component_matches", "rd_atomic_requirements"}:
            cols += [sa.UniqueConstraint(*group, "match_id")]
        if name in {"rd_distribution_allocations", "rd_evaluation_requests", "rd_selected_tickets", "rd_run_candidates", "rd_component_tickets"}:
            cols += [sa.UniqueConstraint(*group, "candidate_id")]
        return cols

    for schema, _, name, fields in CHILD_SPECS:
        t(name, *child_columns(name, fields), fk(["parent_id"], TYPE_SPECS[schema][0], ["artifact_id"]))
    for _, _, _, name, parent, fields in NESTED_SPECS:
        t(name, *child_columns(name, fields, nested=True), fk(["parent_id", "group_no"], parent, ["parent_id", "position"]))
    t("rd_seals", c("artifact_id", primary_key=True), fk(["artifact_id"], "rd_artifacts"))
    return tables


def return_distribution_triggers():
    statements = {}
    def trigger(name, table, condition, operation="INSERT"):
        statements[name] = f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'immutable return distribution graph violation'); END"

    for name, table in return_distribution_tables(sa.MetaData()).items():
        for op in ("UPDATE", "DELETE"):
            trigger(f"trg_{name}_{op.lower()}_v1", name, "1", op)
        match = " AND ".join(f"x.{col.name}=NEW.{col.name}" for col in table.primary_key.columns)
        trigger(f"trg_{name}_replace_v1", name, f"EXISTS(SELECT 1 FROM {name} x WHERE {match})")
    trigger("trg_rd_header_v1", "rd_artifacts",
        "json_extract(NEW.artifact_json,'$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.artifact_json,'$.content_hash') IS NOT NEW.content_hash OR json_extract(NEW.artifact_json,'$.schema_version') IS NOT NEW.schema_version")
    for schema, (name, fields) in TYPE_SPECS.items():
        conditions = [f"a.schema_version='{schema}'"] + [f"NEW.{key} IS json_extract(a.artifact_json,'$.{path}')" for key, (path, *_) in fields.items()]
        if schema == "RELEVANT_MATCH_STATE_V1":
            conditions.append("EXISTS(SELECT 1 FROM mm_market_keys k WHERE k.market_hash=NEW.market_hash AND json(k.market_json)=json(json_extract(a.artifact_json,'$.market_key')))")
        trigger(f"trg_{name}_binding_v1", name, "EXISTS(SELECT 1 FROM rd_seals WHERE artifact_id=NEW.artifact_id) OR NOT EXISTS(SELECT 1 FROM rd_artifacts a WHERE a.artifact_id=NEW.artifact_id AND " + " AND ".join(conditions) + ")")

    for spec in (*CHILD_SPECS, *NESTED_SPECS):
        if len(spec) == 4:
            schema, path, name, fields = spec
            expression = f"'$.{path}['||NEW.position||']'"
        else:
            schema, outer, inner, name, _, fields = spec
            expression = f"'$.{outer}['||NEW.group_no||'].{inner}['||NEW.position||']'"
        conditions = [f"a.schema_version='{schema}'", f"json(NEW.item_json) IS json_quote(json_extract(a.artifact_json,{expression}))"]
        conditions += [f"NEW.{col} IS json_extract(NEW.item_json,'{'$.'+path if path else '$'}')" for col, (path, *_) in fields.items()]
        if name in SUPPORT_TABLES or name == "rd_relevant_states":
            conditions += ["NEW.probability IS json_extract(NEW.item_json,'$.probability')", "json_type(NEW.item_json,'$.probability')='text'"]
        if name in SUPPORT_TABLES:
            conditions.append("NEW.gross_payout_fen IS json_extract(NEW.item_json,'$.gross_payout_fen')")
        if name == "rd_relevant_states":
            conditions.append("NEW.outcome_key IS json_extract(NEW.item_json,'$.outcome')")
            conditions.append("NEW.market_hash IS (SELECT market_hash FROM rd_match_states WHERE artifact_id=NEW.parent_id)")
        if name in MULTIPLIER_TABLES:
            conditions.append("NEW.multiplier IS json_extract(NEW.item_json,'$.multiplier')")
        if name == "rd_selected_tickets":
            conditions.append("NEW.role IS json_extract(NEW.item_json,'$.role')")
        trigger(f"trg_{name}_binding_v1", name, "EXISTS(SELECT 1 FROM rd_seals WHERE artifact_id=NEW.parent_id) OR NOT EXISTS(SELECT 1 FROM rd_artifacts a WHERE a.artifact_id=NEW.parent_id AND " + " AND ".join(conditions) + ")")
    trigger("trg_rd_single_rest_v1", "rd_relevant_states", "NEW.outcome_key IS NULL AND EXISTS(SELECT 1 FROM rd_relevant_states WHERE parent_id=NEW.parent_id AND outcome_key IS NULL)")
    cases = []
    for schema, (name, _) in TYPE_SPECS.items():
        checks = [f"a.schema_version='{schema}'", f"EXISTS(SELECT 1 FROM {name} WHERE artifact_id=a.artifact_id)"]
        checks += [f"(SELECT COUNT(*) FROM {child} WHERE parent_id=a.artifact_id)=json_array_length(a.artifact_json,'$.{path}')" for kind, path, child, _ in CHILD_SPECS if kind == schema]
        for kind, _, inner, child, parent, _ in NESTED_SPECS:
            if kind == schema:
                checks.append(f"NOT EXISTS(SELECT 1 FROM {parent} p WHERE p.parent_id=a.artifact_id AND (SELECT COUNT(*) FROM {child} c WHERE c.parent_id=p.parent_id AND c.group_no=p.position)<>json_array_length(p.item_json,'$.{inner}'))")
        cases.append("(" + " AND ".join(checks) + ")")
    trigger("trg_rd_complete_v1", "rd_seals", "NOT EXISTS(SELECT 1 FROM rd_artifacts a WHERE a.artifact_id=NEW.artifact_id AND (" + " OR ".join(cases) + "))")
    return statements


def install_return_distribution_triggers(connection):
    if connection.scalar(sa.text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='rd_artifacts'")):
        for name, sql in return_distribution_triggers().items():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            connection.exec_driver_sql(sql)
