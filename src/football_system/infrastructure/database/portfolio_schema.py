"""Portfolio graph, shared legacy/PF ownership and budget conservation constraints."""
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine

from football_system.infrastructure.database.portfolio_types import header_valid, reference_json, request_valid

HEAD = "a0c9def6b732"
TABLES = ("pf_receipts", "pf_rejections", "pf_artifacts", "pf_edges", "pf_rb_edges", "pf_seals", "pf_budget_grants", "pf_snapshots", "pf_snapshot_units",
    "pf_assemblies", "pf_assembly_members", "pf_assembly_units", "pf_locks", "pf_shared_heads", "pf_shared_slots", "pf_shared_versions",
    "pf_budget_claims", "pf_settlements", "pf_settlement_observations", "pf_backfill_proofs")


@event.listens_for(Engine, "connect")
def register_functions(connection, _record):
    if hasattr(connection, "create_function"):
        connection.create_function("pf_valid_header_v1", 1, header_valid, deterministic=True)
        connection.create_function("pf_refs_v1", 1, reference_json, deterministic=True)
        connection.create_function("pf_valid_request_v1", 4, request_valid, deterministic=True)


def portfolio_tables(metadata):
    if TABLES[0] in metadata.tables:
        return {name: metadata.tables[name] for name in TABLES}
    for name, key in (("rb_runs", "artifact_id"), ("rb_model_pins", "artifact_id"), ("rb_programs", "artifact_id"), ("rb_locks", "artifact_id"),
        ("matches", "internal_match_id"), ("rb_market_keys", "market_hash"), ("rb_seals", "artifact_id")):
        if name not in metadata.tables:
            sa.Table(name, metadata, sa.Column(key, sa.String(160), primary_key=True))
    if "rb_artifacts" not in metadata.tables:
        sa.Table("rb_artifacts", metadata, sa.Column("artifact_id", sa.String(160), primary_key=True), sa.Column("schema_version", sa.String(160)),
            sa.Column("content_hash", sa.String(64)), sa.UniqueConstraint("artifact_id", "schema_version", "content_hash"))
    result = {}
    def c(name, kind=None, **kw): return sa.Column(name, kind if kind is not None else sa.String(160), nullable=kw.pop("nullable", False), **kw)
    def fk(local, remote, deferred=False): return sa.ForeignKeyConstraint(local, remote, ondelete="RESTRICT", deferrable=deferred, initially="DEFERRED" if deferred else None)
    def t(name, *values): result[name] = sa.Table(name, metadata, *values)
    t("pf_receipts", c("request_key", primary_key=True), c("operation"), c("request_hash", sa.String(64)), c("request_json", sa.Text()),
      c("sequence", sa.Integer(), unique=True), c("event_at", sa.String(40)), c("event_us", sa.BigInteger()), c("provenance"), c("clock_basis"),
      c("receipt_id", unique=True), c("response_id", unique=True), fk(["receipt_id"], ["pf_seals.artifact_id"], True), fk(["response_id"], ["pf_seals.artifact_id"], True))
    t("pf_rejections", c("request_key", primary_key=True), c("operation"), c("request_hash", sa.String(64)), c("recorded_at", sa.String(40)), c("reason"))
    t("pf_artifacts", c("artifact_id", primary_key=True), c("schema_version"), c("content_hash", sa.String(64)), c("artifact_json", sa.Text()), c("event_key"),
      sa.UniqueConstraint("artifact_id", "schema_version", "content_hash"), fk(["event_key"], ["pf_receipts.request_key"], True),
      fk(["artifact_id"], ["pf_seals.artifact_id"], True), sa.CheckConstraint("json_valid(artifact_json) AND length(content_hash)=64", name="ck_pf_artifact"))
    for name, target in (("pf_edges", "pf_artifacts"), ("pf_rb_edges", "rb_artifacts")):
        t(name, c("parent_id", primary_key=True), c("path", primary_key=True), c("target_id"), c("target_schema"), c("target_hash", sa.String(64)),
          fk(["parent_id"], ["pf_artifacts.artifact_id"]), fk(["target_id", "target_schema", "target_hash"], [target+".artifact_id", target+".schema_version", target+".content_hash"]),
          fk(["target_id"], [("pf_seals" if target == "pf_artifacts" else "rb_seals")+".artifact_id"]))
    t("pf_seals", c("artifact_id", primary_key=True), c("published_at", sa.String(40)), c("published_us", sa.BigInteger()), fk(["artifact_id"], ["pf_artifacts.artifact_id"]))
    t("pf_budget_grants", c("grant_id", primary_key=True), c("funding_scope_id", unique=True), c("allocation_id", unique=True), c("cycle_id"),
      c("budget_fen", sa.BigInteger()), c("capital_hash", sa.String(64), unique=True), sa.UniqueConstraint("funding_scope_id", "cycle_id"), fk(["grant_id"], ["pf_artifacts.artifact_id"]),
      sa.CheckConstraint("typeof(budget_fen)='integer' AND budget_fen>=0", name="ck_pf_budget_amount"))
    t("pf_snapshots", c("snapshot_id", primary_key=True), c("run_id"), c("program_id"), c("pin_id"), c("member_count", sa.Integer()),
      fk(["snapshot_id"], ["pf_artifacts.artifact_id"]), fk(["run_id"], ["rb_runs.artifact_id"]), fk(["program_id"], ["rb_programs.artifact_id"]), fk(["pin_id"], ["rb_model_pins.artifact_id"]))
    t("pf_snapshot_units", c("snapshot_id", primary_key=True), c("match_id", primary_key=True), c("market_hash", primary_key=True), c("unit_id"), c("sp_id"),
      fk(["snapshot_id"], ["pf_snapshots.snapshot_id"]), fk(["match_id"], ["matches.internal_match_id"]), fk(["market_hash"], ["rb_market_keys.market_hash"]),
      fk(["unit_id"], ["rb_seals.artifact_id"]), fk(["sp_id"], ["rb_seals.artifact_id"]))
    t("pf_assemblies", c("assembly_id", primary_key=True), c("grant_id"), c("member_count", sa.Integer()), c("unit_count", sa.Integer()),
      fk(["assembly_id"], ["pf_artifacts.artifact_id"]), fk(["grant_id"], ["pf_budget_grants.grant_id"]))
    t("pf_assembly_members", c("assembly_id", primary_key=True), c("snapshot_id", primary_key=True), c("run_id"), c("position", sa.Integer()),
      sa.UniqueConstraint("assembly_id", "run_id"), sa.UniqueConstraint("assembly_id", "position"), fk(["assembly_id"], ["pf_assemblies.assembly_id"]),
      fk(["snapshot_id"], ["pf_snapshots.snapshot_id"]), fk(["run_id"], ["rb_runs.artifact_id"]))
    t("pf_assembly_units", c("assembly_id", primary_key=True), c("match_id", primary_key=True), c("market_hash", primary_key=True), c("snapshot_id"), c("program_id"),
      fk(["assembly_id", "snapshot_id"], ["pf_assembly_members.assembly_id", "pf_assembly_members.snapshot_id"]),
      fk(["snapshot_id", "match_id", "market_hash"], ["pf_snapshot_units.snapshot_id", "pf_snapshot_units.match_id", "pf_snapshot_units.market_hash"]), fk(["program_id"], ["rb_programs.artifact_id"]))
    t("pf_locks", c("lock_id", primary_key=True), c("assembly_id", unique=True), c("grant_id", unique=True), c("locked_us", sa.BigInteger()),
      fk(["lock_id"], ["pf_artifacts.artifact_id"]), fk(["assembly_id"], ["pf_assemblies.assembly_id"]), fk(["grant_id"], ["pf_budget_grants.grant_id"]))
    t("pf_shared_heads", c("parent_run_id", primary_key=True), c("owner_kind"), c("legacy_event_id", nullable=True), c("portfolio_lock_id", nullable=True),
      fk(["parent_run_id"], ["rb_runs.artifact_id"]), fk(["legacy_event_id"], ["rb_artifacts.artifact_id"]), fk(["portfolio_lock_id"], ["pf_seals.artifact_id"], True),
      sa.CheckConstraint("(owner_kind IN ('LEGACY_LOCK','LEGACY_REPLACE') AND legacy_event_id IS NOT NULL AND portfolio_lock_id IS NULL) OR (owner_kind='PORTFOLIO_LOCK' AND legacy_event_id IS NULL AND portfolio_lock_id IS NOT NULL)", name="ck_pf_head_owner"))
    t("pf_shared_slots", c("program_id", primary_key=True), c("match_id", primary_key=True), c("market_hash", primary_key=True),
      c("initial_legacy_lock_id", nullable=True), c("initial_portfolio_lock_id", nullable=True), fk(["program_id"], ["rb_programs.artifact_id"]),
      fk(["match_id"], ["matches.internal_match_id"]), fk(["market_hash"], ["rb_market_keys.market_hash"]), fk(["initial_legacy_lock_id"], ["rb_locks.artifact_id"]),
      fk(["initial_portfolio_lock_id"], ["pf_seals.artifact_id"], True),
      sa.CheckConstraint("(initial_legacy_lock_id IS NOT NULL AND initial_portfolio_lock_id IS NULL) OR (initial_legacy_lock_id IS NULL AND initial_portfolio_lock_id IS NOT NULL)", name="ck_pf_slot_owner"))
    t("pf_shared_versions", c("program_id", primary_key=True), c("match_id", primary_key=True), c("market_hash", primary_key=True), c("version", sa.Integer(), primary_key=True),
      c("previous_version", sa.Integer(), nullable=True), c("legacy_lock_id", nullable=True), c("portfolio_lock_id", nullable=True),
      sa.UniqueConstraint("program_id", "match_id", "market_hash", "previous_version"),
      fk(["program_id", "match_id", "market_hash"], ["pf_shared_slots.program_id", "pf_shared_slots.match_id", "pf_shared_slots.market_hash"]),
      fk(["program_id", "match_id", "market_hash", "previous_version"], ["pf_shared_versions.program_id", "pf_shared_versions.match_id", "pf_shared_versions.market_hash", "pf_shared_versions.version"]),
      fk(["legacy_lock_id"], ["rb_locks.artifact_id"]), fk(["portfolio_lock_id"], ["pf_seals.artifact_id"], True),
      sa.CheckConstraint("typeof(version)='integer' AND ((version=0 AND previous_version IS NULL) OR (version>0 AND previous_version=version-1))", name="ck_pf_version_chain"),
      sa.CheckConstraint("(legacy_lock_id IS NOT NULL AND portfolio_lock_id IS NULL) OR (legacy_lock_id IS NULL AND portfolio_lock_id IS NOT NULL AND version=0)", name="ck_pf_version_owner"))
    t("pf_budget_claims", c("grant_id", primary_key=True), c("lock_id", unique=True), fk(["grant_id"], ["pf_budget_grants.grant_id"]), fk(["lock_id"], ["pf_seals.artifact_id"], True))
    t("pf_settlements", c("settlement_id", primary_key=True), c("lock_id"), c("version", sa.Integer()), c("previous_id", nullable=True, unique=True), c("result_set_id"),
      sa.UniqueConstraint("lock_id", "version"), fk(["settlement_id"], ["pf_artifacts.artifact_id"]), fk(["lock_id"], ["pf_locks.lock_id"]),
      fk(["previous_id"], ["pf_settlements.settlement_id"]), fk(["result_set_id"], ["pf_seals.artifact_id"]),
      sa.CheckConstraint("(version=0 AND previous_id IS NULL) OR (version>0 AND previous_id IS NOT NULL)", name="ck_pf_settlement_version"))
    t("pf_settlement_observations", c("settlement_id", primary_key=True), c("match_id", primary_key=True), c("observation_id"),
      fk(["settlement_id"], ["pf_settlements.settlement_id"]), fk(["match_id"], ["matches.internal_match_id"]), fk(["observation_id"], ["rb_seals.artifact_id"]))
    t("pf_backfill_proofs", c("contract", primary_key=True), c("proof_json", sa.Text()), c("proof_hash", sa.String(64)),
      sa.CheckConstraint("json_valid(proof_json) AND length(proof_hash)=64", name="ck_pf_backfill_proof"))
    return result


def portfolio_triggers():
    result = {}
    def guard(name, table, condition, action="INSERT"):
        result[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'portfolio ledger invariant'); END"
    def after(name, table, sql):
        result[name] = f"CREATE TRIGGER {name} AFTER INSERT ON {table} BEGIN {sql}; END"
    for name, table in portfolio_tables(sa.MetaData()).items():
        for action in ("UPDATE", "DELETE"):
            guard("trg_"+name+"_"+action.lower(), name, "1", action)
        key = " AND ".join("x."+col.name+" IS NEW."+col.name for col in table.primary_key)
        guard("trg_"+name+"_replace", name, f"EXISTS(SELECT 1 FROM {name} x WHERE {key})")
    guard("trg_pf_header", "pf_artifacts", "pf_valid_header_v1(NEW.artifact_json)<>1 OR json_extract(NEW.artifact_json,'$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.artifact_json,'$.schema_version') IS NOT NEW.schema_version OR json_extract(NEW.artifact_json,'$.content_hash') IS NOT NEW.content_hash")
    guard("trg_pf_receipt", "pf_receipts", """NEW.sequence<>COALESCE((SELECT max(sequence) FROM pf_receipts),0)+1
      OR NEW.event_us IS NOT rb_us_v1(NEW.event_at) OR NEW.event_us<COALESCE((SELECT max(event_us) FROM pf_receipts),NEW.event_us)
      OR pf_valid_request_v1(NEW.operation,NEW.request_key,NEW.request_json,NEW.request_hash)<>1
      OR NEW.provenance NOT IN ('LIVE_OBSERVATION','SYNTHETIC_SOFTWARE_ACCEPTANCE')
      OR (NEW.provenance='LIVE_OBSERVATION' AND (NEW.clock_basis<>'LOCAL_SYSTEM_UTC' OR ABS(NEW.event_us-CAST(strftime('%s','now') AS INTEGER)*1000000)>30000000))
      OR (NEW.provenance='SYNTHETIC_SOFTWARE_ACCEPTANCE' AND NEW.clock_basis<>'SYNTHETIC_TEST_CLOCK')""")
    for table, ledger in (("pf_edges", "PF"), ("pf_rb_edges", "RB")):
        guard("trg_"+table+"_projection", table, f"""EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.parent_id) OR NOT EXISTS(
          SELECT 1 FROM pf_artifacts a,json_each(pf_refs_v1(a.artifact_json)) j WHERE a.artifact_id=NEW.parent_id
          AND json_extract(j.value,'$.path')=NEW.path AND json_extract(j.value,'$.ledger')='{ledger}'
          AND json_extract(j.value,'$.target_id')=NEW.target_id AND json_extract(j.value,'$.target_schema')=NEW.target_schema
          AND json_extract(j.value,'$.target_hash')=NEW.target_hash)""")
    guard("trg_pf_budget_unique", "pf_budget_grants", "EXISTS(SELECT 1 FROM pf_budget_grants WHERE funding_scope_id=NEW.funding_scope_id OR allocation_id=NEW.allocation_id OR capital_hash=NEW.capital_hash)")
    guard("trg_pf_budget_projection", "pf_budget_grants", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.grant_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a JOIN pf_artifacts r ON r.artifact_id=json_extract(a.artifact_json,'$.content_payload.exact_approval.artifact_id')
      WHERE a.artifact_id=NEW.grant_id AND a.schema_version='PORTFOLIO_BUDGET_AUTHORITY_V1'
      AND json_extract(a.artifact_json,'$.content_payload.funding_scope_id')=NEW.funding_scope_id
      AND json_extract(a.artifact_json,'$.content_payload.allocation_id')=NEW.allocation_id
      AND json_extract(a.artifact_json,'$.content_payload.decision_cycle_id')=NEW.cycle_id
      AND json_extract(a.artifact_json,'$.content_payload.budget_fen')=NEW.budget_fen
      AND r.schema_version='PORTFOLIO_BUDGET_APPROVAL_RECORD_V1'
      AND json_extract(a.artifact_json,'$.content_payload.operator_authority')=json_extract(r.artifact_json,'$.authority')
      AND NOT EXISTS(SELECT 1 FROM json_each(a.artifact_json,'$.content_payload') j
        WHERE j.key IN ('funding_scope_id','allocation_id','decision_cycle_id','currency','budget_fen','target_census_hash','policy_hash','effective_at_utc','expires_at_utc','allocation_rule')
        AND j.value IS NOT json_extract(r.artifact_json,'$.subject.'||j.key))
      AND NEW.capital_hash=json_extract(r.artifact_json,'$.subject.capital_evidence.evidence_sha256'))""")
    guard("trg_pf_snapshot_projection", "pf_snapshots", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.snapshot_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a WHERE a.artifact_id=NEW.snapshot_id AND a.schema_version='PREDICTION_SOURCE_SNAPSHOT_V1'
      AND json_extract(a.artifact_json,'$.content_payload.run.artifact_id')=NEW.run_id
      AND json_extract(a.artifact_json,'$.content_payload.program.artifact_id')=NEW.program_id
      AND json_extract(a.artifact_json,'$.content_payload.model_pin.artifact_id')=NEW.pin_id
      AND json_array_length(a.artifact_json,'$.content_payload.frames')=NEW.member_count)""")
    guard("trg_pf_snapshot_unit_projection", "pf_snapshot_units", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.snapshot_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a,json_each(a.artifact_json,'$.content_payload.frames') f,rb_market_keys k WHERE a.artifact_id=NEW.snapshot_id
      AND json_extract(f.value,'$.identity.match_id')=NEW.match_id AND json_extract(f.value,'$.unit.artifact_id')=NEW.unit_id
      AND json_extract(f.value,'$.sp_snapshot.artifact_id')=NEW.sp_id AND k.market_hash=NEW.market_hash
      AND json(k.market_json)=json_extract(f.value,'$.market_key'))""")
    guard("trg_pf_assembly_projection", "pf_assemblies", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.assembly_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a WHERE a.artifact_id=NEW.assembly_id AND a.schema_version='PORTFOLIO_ASSEMBLY_V1'
      AND json_extract(a.artifact_json,'$.content_payload.budget_authority.artifact_id')=NEW.grant_id
      AND json_array_length(a.artifact_json,'$.content_payload.snapshots')=NEW.member_count AND NEW.unit_count BETWEEN 2 AND 64)""")
    guard("trg_pf_member_projection", "pf_assembly_members", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.assembly_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a,json_each(a.artifact_json,'$.content_payload.snapshots') j,pf_snapshots s WHERE a.artifact_id=NEW.assembly_id
      AND j.key=NEW.position AND json_extract(j.value,'$.artifact_id')=NEW.snapshot_id AND s.snapshot_id=NEW.snapshot_id AND s.run_id=NEW.run_id)""")
    guard("trg_pf_unit_projection", "pf_assembly_units", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.assembly_id) OR NOT EXISTS(
      SELECT 1 FROM pf_assembly_members m,pf_snapshot_units u,pf_snapshots s WHERE m.assembly_id=NEW.assembly_id AND m.snapshot_id=NEW.snapshot_id
      AND u.snapshot_id=m.snapshot_id AND u.match_id=NEW.match_id AND u.market_hash=NEW.market_hash
      AND s.snapshot_id=m.snapshot_id AND s.program_id=NEW.program_id)""")
    guard("trg_pf_lock_unique", "pf_locks", "EXISTS(SELECT 1 FROM pf_locks WHERE assembly_id=NEW.assembly_id OR grant_id=NEW.grant_id)")
    guard("trg_pf_lock_projection", "pf_locks", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.lock_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a,pf_assemblies s WHERE a.artifact_id=NEW.lock_id AND a.schema_version='ATOMIC_PORTFOLIO_LOCK_V1'
      AND json_extract(a.artifact_json,'$.content_payload.assembly.artifact_id')=NEW.assembly_id AND s.assembly_id=NEW.assembly_id AND s.grant_id=NEW.grant_id
      AND json_extract(a.artifact_json,'$.content_payload.budget_authority.artifact_id')=NEW.grant_id
      AND rb_us_v1(json_extract(a.artifact_json,'$.content_payload.locked_at_utc'))=NEW.locked_us)""")
    guard("trg_pf_lock_financial_projection", "pf_locks", """NOT EXISTS(
      SELECT 1 FROM pf_artifacts l,pf_artifacts a,pf_artifacts p WHERE l.artifact_id=NEW.lock_id AND a.artifact_id=NEW.assembly_id
      AND p.schema_version='PORTFOLIO_LOCK_REVALIDATION_V1' AND p.content_hash=json_extract(l.artifact_json,'$.content_payload.revalidation_hash')
      AND json_extract(p.artifact_json,'$.assembly.artifact_id')=a.artifact_id
      AND json_extract(p.artifact_json,'$.budget_authority.artifact_id')=NEW.grant_id
      AND json_extract(p.artifact_json,'$.receipt')=json_extract(l.artifact_json,'$.content_payload.receipt')
      AND rb_us_v1(json_extract(p.artifact_json,'$.checked_at_utc'))=NEW.locked_us
      AND json_extract(l.artifact_json,'$.content_payload.budget_fen')=json_extract(a.artifact_json,'$.content_payload.budget_fen')
      AND json_extract(l.artifact_json,'$.content_payload.stake_fen')=json_extract(a.artifact_json,'$.content_payload.stake_fen')
      AND json_extract(l.artifact_json,'$.content_payload.cash_fen')=json_extract(a.artifact_json,'$.content_payload.cash_fen')
      AND json_extract(l.artifact_json,'$.content_payload.member_set_hash')=json_extract(a.artifact_json,'$.content_payload.member_set_hash')
      AND json_extract(l.artifact_json,'$.content_payload.common_cutoff')=json_extract(a.artifact_json,'$.content_payload.common_cutoff')
      AND json_extract(l.artifact_json,'$.content_payload.earliest_member_kickoff')=json_extract(a.artifact_json,'$.content_payload.earliest_member_kickoff')
      AND json_array_length(p.artifact_json,'$.members')=(SELECT member_count FROM pf_assemblies WHERE assembly_id=a.artifact_id)
      AND NOT EXISTS(SELECT 1 FROM json_each(p.artifact_json,'$.members') j LEFT JOIN pf_assembly_members m
        ON m.assembly_id=a.artifact_id AND m.position=j.key AND m.snapshot_id=json_extract(j.value,'$.snapshot.artifact_id')
        AND m.run_id=json_extract(j.value,'$.source_run.artifact_id') WHERE m.snapshot_id IS NULL))""")
    guard("trg_pf_head_origin", "pf_shared_heads", """(NEW.legacy_event_id IS NOT NULL AND NOT EXISTS(
      SELECT 1 FROM rb_head_consumptions h WHERE h.parent_run_id=NEW.parent_run_id AND h.event_id=NEW.legacy_event_id
      AND NEW.owner_kind=CASE h.action WHEN 'LOCK' THEN 'LEGACY_LOCK' WHEN 'REPLACE' THEN 'LEGACY_REPLACE' END))
      OR (NEW.portfolio_lock_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM pf_locks l,pf_assembly_members m
      WHERE l.lock_id=NEW.portfolio_lock_id AND m.assembly_id=l.assembly_id AND m.run_id=NEW.parent_run_id))""")
    guard("trg_pf_slot_origin", "pf_shared_slots", """(NEW.initial_legacy_lock_id IS NOT NULL AND NOT EXISTS(
      SELECT 1 FROM rb_prediction_slots s WHERE s.program_id=NEW.program_id AND s.match_id=NEW.match_id AND s.market_hash=NEW.market_hash AND s.initial_lock_id=NEW.initial_legacy_lock_id))
      OR (NEW.initial_portfolio_lock_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM pf_locks l,pf_assembly_units u WHERE l.lock_id=NEW.initial_portfolio_lock_id
      AND u.assembly_id=l.assembly_id AND u.program_id=NEW.program_id AND u.match_id=NEW.match_id AND u.market_hash=NEW.market_hash))""")
    guard("trg_pf_version_origin", "pf_shared_versions", """NOT EXISTS(SELECT 1 FROM pf_shared_slots s WHERE s.program_id=NEW.program_id AND s.match_id=NEW.match_id AND s.market_hash=NEW.market_hash
      AND ((NEW.legacy_lock_id IS NOT NULL AND s.initial_legacy_lock_id IS NOT NULL AND EXISTS(SELECT 1 FROM rb_prediction_versions v
        WHERE v.program_id=NEW.program_id AND v.match_id=NEW.match_id AND v.market_hash=NEW.market_hash AND v.version=NEW.version
        AND v.previous_version IS NEW.previous_version AND v.lock_id=NEW.legacy_lock_id))
      OR (NEW.portfolio_lock_id=s.initial_portfolio_lock_id AND NEW.version=0)))""")
    # Both sides explicitly abort before uniqueness conflict handling. OR IGNORE
    # or OR REPLACE in an old statement cannot suppress or overwrite the mirror.
    guard("trg_pf_legacy_head_exclusion", "rb_head_consumptions", "EXISTS(SELECT 1 FROM pf_shared_heads WHERE parent_run_id=NEW.parent_run_id)")
    after("trg_pf_legacy_head_mirror", "rb_head_consumptions", "INSERT INTO pf_shared_heads(parent_run_id,owner_kind,legacy_event_id) VALUES(NEW.parent_run_id,CASE NEW.action WHEN 'LOCK' THEN 'LEGACY_LOCK' ELSE 'LEGACY_REPLACE' END,NEW.event_id)")
    guard("trg_pf_legacy_slot_exclusion", "rb_prediction_slots", "EXISTS(SELECT 1 FROM pf_shared_slots WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash)")
    after("trg_pf_legacy_slot_mirror", "rb_prediction_slots", "INSERT INTO pf_shared_slots(program_id,match_id,market_hash,initial_legacy_lock_id) VALUES(NEW.program_id,NEW.match_id,NEW.market_hash,NEW.initial_lock_id)")
    guard("trg_pf_legacy_version_exclusion", "rb_prediction_versions", """EXISTS(SELECT 1 FROM pf_shared_versions WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash AND version=NEW.version)
      OR NOT EXISTS(SELECT 1 FROM pf_shared_slots WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash AND initial_legacy_lock_id IS NOT NULL)""")
    after("trg_pf_legacy_version_mirror", "rb_prediction_versions", "INSERT INTO pf_shared_versions(program_id,match_id,market_hash,version,previous_version,legacy_lock_id) VALUES(NEW.program_id,NEW.match_id,NEW.market_hash,NEW.version,NEW.previous_version,NEW.lock_id)")
    guard("trg_pf_budget_claim_unique", "pf_budget_claims", "EXISTS(SELECT 1 FROM pf_budget_claims WHERE lock_id=NEW.lock_id)")
    guard("trg_pf_budget_claim_origin", "pf_budget_claims", "NOT EXISTS(SELECT 1 FROM pf_locks l WHERE l.lock_id=NEW.lock_id AND l.grant_id=NEW.grant_id)")
    guard("trg_pf_settlement_projection", "pf_settlements", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.settlement_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a,pf_artifacts r WHERE a.artifact_id=NEW.settlement_id AND a.schema_version='PORTFOLIO_SETTLEMENT_V1'
      AND json_extract(a.artifact_json,'$.content_payload.portfolio_lock.artifact_id')=NEW.lock_id
      AND json_extract(a.artifact_json,'$.content_payload.previous.artifact_id') IS NEW.previous_id
      AND r.artifact_id=NEW.result_set_id AND r.schema_version='PORTFOLIO_RESULT_SET_V1'
      AND r.content_hash=json_extract(a.artifact_json,'$.content_payload.result_set_hash')
      AND NEW.version=COALESCE((SELECT max(version)+1 FROM pf_settlements WHERE lock_id=NEW.lock_id),0)
      AND (NEW.version=0 OR EXISTS(SELECT 1 FROM pf_settlements p WHERE p.settlement_id=NEW.previous_id AND p.lock_id=NEW.lock_id AND p.version=NEW.version-1)))""")
    guard("trg_pf_result_projection", "pf_settlement_observations", """EXISTS(SELECT 1 FROM pf_seals WHERE artifact_id=NEW.settlement_id) OR NOT EXISTS(
      SELECT 1 FROM pf_artifacts a,json_each(a.artifact_json,'$.content_payload.observations') j,rb_artifacts r WHERE a.artifact_id=NEW.settlement_id
      AND json_extract(j.value,'$.artifact_id')=NEW.observation_id AND r.artifact_id=NEW.observation_id AND r.schema_version='REAL_RESULT_OBSERVATION_V1'
      AND json_extract(r.artifact_json,'$.claim.match_id')=NEW.match_id)""")
    guard("trg_pf_legacy_report_visibility", "rb_artifacts", """NEW.schema_version IN ('REAL_VALIDATION_REPORT_V1','REAL_EPOCH_CLOSE_V1') AND EXISTS(
      SELECT 1 FROM pf_shared_heads h JOIN pf_seals z ON z.artifact_id=h.portfolio_lock_id JOIN rb_artifacts r ON r.artifact_id=h.parent_run_id
      WHERE json_extract(r.artifact_json,'$.epoch.artifact_id')=json_extract(NEW.artifact_json,'$.epoch.artifact_id')
      AND z.published_us<=rb_us_v1(CASE NEW.schema_version WHEN 'REAL_VALIDATION_REPORT_V1' THEN json_extract(NEW.artifact_json,'$.as_of_at_utc')
        ELSE json_extract(NEW.artifact_json,'$.event_at_utc') END))""")
    guard("trg_pf_seal_graph", "pf_seals", """NOT EXISTS(SELECT 1 FROM pf_artifacts a JOIN pf_receipts r ON r.request_key=a.event_key WHERE a.artifact_id=NEW.artifact_id
      AND NEW.published_us=rb_us_v1(NEW.published_at) AND NEW.published_us>=r.event_us AND NEW.published_us<=r.event_us+30000000
      AND json_array_length(pf_refs_v1(a.artifact_json))=(SELECT count(*) FROM pf_edges WHERE parent_id=a.artifact_id)+(SELECT count(*) FROM pf_rb_edges WHERE parent_id=a.artifact_id))""")
    guard("trg_pf_receipt_projection", "pf_seals", """EXISTS(SELECT 1 FROM pf_artifacts a JOIN pf_receipts r ON r.request_key=a.event_key WHERE a.artifact_id=NEW.artifact_id AND (
      (a.schema_version='PORTFOLIO_RECEIPT_V1' AND (a.artifact_id<>r.receipt_id OR json_extract(a.artifact_json,'$.request_key') IS NOT r.request_key
        OR json_extract(a.artifact_json,'$.request_hash') IS NOT r.request_hash OR json_extract(a.artifact_json,'$.operation') IS NOT r.operation
        OR json_extract(a.artifact_json,'$.sequence') IS NOT r.sequence OR rb_us_v1(json_extract(a.artifact_json,'$.event_at_utc')) IS NOT r.event_us
        OR json_extract(a.artifact_json,'$.provenance') IS NOT r.provenance OR json_extract(a.artifact_json,'$.clock_basis') IS NOT r.clock_basis))
      OR (a.artifact_id=r.response_id AND a.schema_version IS NOT CASE r.operation WHEN 'budget-record' THEN 'PORTFOLIO_BUDGET_AUTHORITY_V1'
        WHEN 'ticket-requests' THEN 'PORTFOLIO_TICKET_REQUEST_BUNDLE_V1' WHEN 'snapshot' THEN 'PREDICTION_SOURCE_SNAPSHOT_V1'
        WHEN 'assembly' THEN 'PORTFOLIO_ASSEMBLY_V1' WHEN 'lock' THEN 'ATOMIC_PORTFOLIO_LOCK_V1'
        WHEN 'results' THEN 'PORTFOLIO_RESULT_SET_V1' WHEN 'settle' THEN 'PORTFOLIO_SETTLEMENT_V1' END)
      OR (a.schema_version IN ('PREDICTION_SOURCE_SNAPSHOT_V1','ATOMIC_PORTFOLIO_LOCK_V1') AND json_extract(a.artifact_json,'$.content_payload.receipt.artifact_id') IS NOT r.receipt_id)
      OR (a.schema_version='PREDICTION_SOURCE_SNAPSHOT_V1' AND rb_us_v1(json_extract(a.artifact_json,'$.content_payload.sealed_at_utc')) IS NOT r.event_us)
      OR (a.schema_version='ATOMIC_PORTFOLIO_LOCK_V1' AND rb_us_v1(json_extract(a.artifact_json,'$.content_payload.locked_at_utc')) IS NOT r.event_us)
      OR (a.schema_version='PORTFOLIO_ASSEMBLY_V1' AND rb_us_v1(json_extract(a.artifact_json,'$.content_payload.common_cutoff')) IS NOT r.event_us)))""")
    guard("trg_pf_seal_projections", "pf_seals", """EXISTS(SELECT 1 FROM pf_artifacts a WHERE a.artifact_id=NEW.artifact_id AND (
      (a.schema_version='PORTFOLIO_BUDGET_AUTHORITY_V1' AND NOT EXISTS(SELECT 1 FROM pf_budget_grants WHERE grant_id=a.artifact_id))
      OR (a.schema_version='PREDICTION_SOURCE_SNAPSHOT_V1' AND NOT EXISTS(SELECT 1 FROM pf_snapshots s WHERE s.snapshot_id=a.artifact_id
        AND (SELECT count(*) FROM pf_snapshot_units WHERE snapshot_id=s.snapshot_id)=s.member_count))
      OR (a.schema_version='PORTFOLIO_ASSEMBLY_V1' AND NOT EXISTS(SELECT 1 FROM pf_assemblies s WHERE s.assembly_id=a.artifact_id
        AND (SELECT count(*) FROM pf_assembly_members WHERE assembly_id=s.assembly_id)=s.member_count
        AND (SELECT count(*) FROM pf_assembly_units WHERE assembly_id=s.assembly_id)=s.unit_count
        AND s.unit_count=(SELECT sum(p.member_count) FROM pf_assembly_members m JOIN pf_snapshots p ON p.snapshot_id=m.snapshot_id WHERE m.assembly_id=s.assembly_id)))
      OR (a.schema_version='ATOMIC_PORTFOLIO_LOCK_V1' AND NOT EXISTS(SELECT 1 FROM pf_locks l JOIN pf_assemblies s ON s.assembly_id=l.assembly_id
        JOIN pf_budget_grants g ON g.grant_id=l.grant_id WHERE l.lock_id=a.artifact_id
        AND (SELECT count(*) FROM pf_shared_heads WHERE portfolio_lock_id=l.lock_id)=s.member_count
        AND (SELECT count(*) FROM pf_shared_slots WHERE initial_portfolio_lock_id=l.lock_id)=s.unit_count
        AND (SELECT count(*) FROM pf_shared_versions WHERE portfolio_lock_id=l.lock_id)=s.unit_count
        AND EXISTS(SELECT 1 FROM pf_budget_claims c WHERE c.lock_id=l.lock_id AND c.grant_id=l.grant_id)
        AND json_extract(a.artifact_json,'$.content_payload.budget_fen')=g.budget_fen
        AND NEW.published_us+60000000<rb_us_v1(json_extract(a.artifact_json,'$.content_payload.earliest_member_kickoff'))))
      OR (a.schema_version='PORTFOLIO_SETTLEMENT_V1' AND NOT EXISTS(SELECT 1 FROM pf_settlements s WHERE s.settlement_id=a.artifact_id
        AND (SELECT count(*) FROM pf_settlement_observations WHERE settlement_id=s.settlement_id)=json_array_length(a.artifact_json,'$.content_payload.observations')))))""")
    return result


def install_portfolio_triggers(connection):
    if connection.scalar(sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='pf_artifacts'")):
        for statement in portfolio_triggers().values():
            connection.exec_driver_sql(statement.replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS ", 1))


def assert_portfolio_schema(connection):
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    from football_system.infrastructure.database.real_bridge_schema import schema_signature
    expected = {name: schema_signature(str(CreateTable(table).compile(dialect=dialect())).strip()) for name, table in portfolio_tables(sa.MetaData()).items()}
    expected.update({name: schema_signature(sql) for name, sql in portfolio_triggers().items()})
    rows = connection.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE name LIKE 'pf_%' OR name LIKE 'trg_pf_%'")).all()
    actual = {name: schema_signature(sql) for name, sql in rows if sql is not None}
    if expected != actual:
        raise ValueError("PORTFOLIO_SCHEMA_AND_OWNERSHIP_GUARDS_REQUIRED")


def assert_legacy_mirrors(connection):
    statements = (
      "SELECT count(*) FROM rb_head_consumptions h LEFT JOIN pf_shared_heads s ON s.parent_run_id=h.parent_run_id WHERE s.parent_run_id IS NULL OR s.legacy_event_id IS NOT h.event_id OR s.owner_kind IS NOT CASE h.action WHEN 'LOCK' THEN 'LEGACY_LOCK' ELSE 'LEGACY_REPLACE' END",
      "SELECT count(*) FROM pf_shared_heads s LEFT JOIN rb_head_consumptions h ON h.parent_run_id=s.parent_run_id WHERE s.legacy_event_id IS NOT NULL AND (h.parent_run_id IS NULL OR h.event_id IS NOT s.legacy_event_id)",
      "SELECT count(*) FROM rb_prediction_slots r LEFT JOIN pf_shared_slots s ON s.program_id=r.program_id AND s.match_id=r.match_id AND s.market_hash=r.market_hash WHERE s.program_id IS NULL OR s.initial_legacy_lock_id IS NOT r.initial_lock_id",
      "SELECT count(*) FROM pf_shared_slots s LEFT JOIN rb_prediction_slots r ON s.program_id=r.program_id AND s.match_id=r.match_id AND s.market_hash=r.market_hash WHERE s.initial_legacy_lock_id IS NOT NULL AND (r.program_id IS NULL OR r.initial_lock_id IS NOT s.initial_legacy_lock_id)",
      "SELECT count(*) FROM rb_prediction_versions r LEFT JOIN pf_shared_versions s ON s.program_id=r.program_id AND s.match_id=r.match_id AND s.market_hash=r.market_hash AND s.version=r.version WHERE s.program_id IS NULL OR s.previous_version IS NOT r.previous_version OR s.legacy_lock_id IS NOT r.lock_id",
      "SELECT count(*) FROM pf_shared_versions s LEFT JOIN rb_prediction_versions r ON s.program_id=r.program_id AND s.match_id=r.match_id AND s.market_hash=r.market_hash AND s.version=r.version WHERE s.legacy_lock_id IS NOT NULL AND (r.program_id IS NULL OR r.lock_id IS NOT s.legacy_lock_id)",
    )
    if any(connection.scalar(sa.text(statement)) for statement in statements):
        raise ValueError("PORTFOLIO_LEGACY_OWNERSHIP_MIRROR_INCOMPLETE")


def backfill_legacy_ownership(connection):
    """Same migration transaction; reject partial/malformed old graphs, never skip rows."""
    from football_system.domain.archive import canonical_json
    from football_system.domain.market_v2 import content_hash
    from football_system.infrastructure.database.real_bridge_schema import assert_real_schema
    assert_real_schema(connection)
    if connection.scalar(sa.text("SELECT count(*) FROM rb_artifacts a LEFT JOIN rb_seals s ON s.artifact_id=a.artifact_id WHERE s.artifact_id IS NULL")):
        raise ValueError("PORTFOLIO_BACKFILL_REQUIRES_COMPLETE_LEGACY_GRAPH")
    if connection.scalar(sa.text("SELECT count(*) FROM rb_artifacts WHERE rb_valid_header_v1(artifact_json)<>1")):
        raise ValueError("PORTFOLIO_BACKFILL_LEGACY_HASH_INVALID")
    if connection.execute(sa.text("PRAGMA foreign_key_check")).all():
        raise ValueError("PORTFOLIO_BACKFILL_LEGACY_FK_INVALID")
    ledger_before = legacy_ledger_identity(connection)
    before = {table: [tuple(row) for row in connection.execute(sa.text("SELECT * FROM " + table + " ORDER BY 1,2,3"))]
        for table in ("rb_head_consumptions", "rb_prediction_slots", "rb_prediction_versions")}
    connection.exec_driver_sql("INSERT INTO pf_shared_heads(parent_run_id,owner_kind,legacy_event_id) SELECT parent_run_id,CASE action WHEN 'LOCK' THEN 'LEGACY_LOCK' ELSE 'LEGACY_REPLACE' END,event_id FROM rb_head_consumptions")
    connection.exec_driver_sql("INSERT INTO pf_shared_slots(program_id,match_id,market_hash,initial_legacy_lock_id) SELECT program_id,match_id,market_hash,initial_lock_id FROM rb_prediction_slots")
    connection.exec_driver_sql("INSERT INTO pf_shared_versions(program_id,match_id,market_hash,version,previous_version,legacy_lock_id) SELECT program_id,match_id,market_hash,version,previous_version,lock_id FROM rb_prediction_versions ORDER BY version,program_id,match_id,market_hash")
    assert_legacy_mirrors(connection)
    after = {table: [tuple(row) for row in connection.execute(sa.text("SELECT * FROM " + table + " ORDER BY 1,2,3"))] for table in before}
    if before != after or ledger_before != legacy_ledger_identity(connection):
        raise ValueError("PORTFOLIO_BACKFILL_CHANGED_LEGACY_ROWS")
    proof = dict(schema_version="PORTFOLIO_OWNERSHIP_BACKFILL_V1", counts={name: len(rows) for name, rows in before.items()},
        legacy_claim_rows_hash=content_hash("PF_LEGACY_CLAIMS_V1", before), legacy_ledger=ledger_before,
        migration_head=HEAD, legacy_rows_unchanged=True, mirrors_complete=True)
    connection.execute(portfolio_tables(sa.MetaData())["pf_backfill_proofs"].insert().values(contract="PORTFOLIO_OWNERSHIP_BACKFILL_V1",
        proof_json=canonical_json(proof), proof_hash=content_hash("PF_BACKFILL_PROOF_V1", proof)))


def legacy_ledger_identity(connection):
    """Exact old ledger DDL and rows; additive PF guards are outside this identity."""
    from football_system.domain.market_v2 import content_hash
    schema = [tuple(row) for row in connection.execute(sa.text("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name LIKE 'rb_%' OR name LIKE 'trg_rb_%' ORDER BY type,name"))]
    rows = {name: [tuple(row) for row in connection.exec_driver_sql('SELECT * FROM "'+name+'" ORDER BY rowid')]
        for kind,name,_,_ in schema if kind == "table"}
    return dict(ddl_hash=content_hash("PF_OLD_LEDGER_DDL_V1",schema), rows_hash=content_hash("PF_OLD_LEDGER_ROWS_V1",rows),
        row_counts={name:len(values) for name,values in rows.items()})
