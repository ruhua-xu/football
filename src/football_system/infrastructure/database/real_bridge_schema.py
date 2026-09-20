"""Additive rb ledger. Typed closed reference grammar, immutable projections and atomic ownership."""

import json
import re
from datetime import datetime
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine

from football_system.domain.real_bridge import REAL_ARTIFACT_TYPES, THREE_WAY
from football_system.domain.archive import canonical_json
from football_system.domain.market_v2 import content_hash
from football_system.infrastructure.database.real_bridge_projections import CHILDREN, child_rows, scalar_references
from football_system.domain.return_distribution import RETURN_ARTIFACT_TYPES
from football_system.domain.prospective import ProspectivePolicyV1
from football_system.domain.market_analysis import FootballEvidenceV1
from football_system.domain.review_v4 import AnalysisPacketV4, MarketReviewContextV4, ImportedReviewV4, GenericFusionRunV1
from football_system.domain.strategy_pass_v2 import OutcomeCandidateV1, MatchChoiceSetV1, ExpandedAtomicBetV2, SystemTicketCandidateV2, SystemTicketV2

ARTIFACTS = {**REAL_ARTIFACT_TYPES, **RETURN_ARTIFACT_TYPES, **{c.model_fields["schema_version"].default: c for c in (
    ProspectivePolicyV1, FootballEvidenceV1, AnalysisPacketV4, MarketReviewContextV4, ImportedReviewV4,
    GenericFusionRunV1, OutcomeCandidateV1, MatchChoiceSetV1, ExpandedAtomicBetV2, SystemTicketCandidateV2, SystemTicketV2)}}
KIND_TABLE = {kind: "rb_"+name for kind, name in (
    ("REAL_OBSERVATION_PROGRAM_V1", "programs"), ("REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1", "implementations"),
    ("REAL_POLICY_VALUE_PIN_V1", "policy_values"), ("REAL_SOURCE_ADMISSION_FACT_V1", "admissions"),
    ("REAL_MODEL_PIN_V1", "model_pins"), ("REAL_MODEL_PIN_INVALIDATION_V1", "model_invalidations"),
    ("REAL_SOURCE_INVALIDATION_V1", "source_invalidations"),
    ("REAL_EPOCH_CONFIGURATION_ANCHOR_V1", "anchors"), ("REAL_VALIDATION_EPOCH_V1", "epochs"),
    ("REAL_SLATE_DECLARATION_V1", "slates"), ("REAL_KICKOFF_BUCKET_V1", "buckets"),
    ("REAL_MARKET_SOURCE_BINDING_V1", "market_bindings"), ("REAL_SP_SOURCE_BINDING_V1", "sp_bindings"),
    ("REAL_MODEL_AVAILABILITY_FACT_V1", "model_availability"), ("REAL_ANALYSIS_UNIT_V1", "analysis_units"),
    ("REAL_MULTI_MARKET_ANALYSIS_V1", "analyses"), ("REAL_PROSPECTIVE_RUN_V1", "runs"),
    ("PRE_LOCK_REPLACEMENT_V1", "prelock_replacements"), ("REAL_REVIEW_AUDIT_V1", "review_audits"),
    ("REAL_STRATEGY_SOURCE_V1", "strategy_sources"), ("REAL_STRATEGY_PLAN_V1", "strategy_plans"),
    ("REAL_CALCULATION_BINDING_V1", "calculations"), ("DECISION_LOCK_V2", "locks"),
    ("REAL_PRE_KICKOFF_INVALIDATION_V1", "invalidations"), ("REAL_RESULT_OBSERVATION_V1", "observations"),
    ("REAL_PROSPECTIVE_SETTLEMENT_V1", "settlements"), ("REAL_VALIDATION_REPORT_V1", "reports"),
    ("REAL_EPOCH_CLOSE_V1", "epoch_closes"), ("ANALYSIS_PACKET_V4", "packets"),
    ("MARKET_REVIEW_CONTEXT_V4", "contexts"), ("IMPORTED_LLM_REVIEW_V4", "reviews"),
    ("GENERIC_FUSION_RUN_V1", "fusions"), ("FOOTBALL_EVIDENCE_V1", "football_inputs"),
    ("RETURN_DISTRIBUTION_POLICY_V1", "return_policies"), ("RETURN_OBJECTIVE_PROFILE_V1", "objective_profiles"),
    ("PROSPECTIVE_POLICY_V1", "validation_policies"))}
MATH_KINDS = set(ARTIFACTS)-set(KIND_TABLE)
# Closed type symbols; a ref at an unlisted JSON path is rejected, not ignored.
P="REAL_OBSERVATION_PROGRAM_V1"
E="REAL_VALIDATION_EPOCH_V1"
A="REAL_EPOCH_CONFIGURATION_ANCHOR_V1"
B="REAL_KICKOFF_BUCKET_V1"
R="REAL_PROSPECTIVE_RUN_V1"
N="REAL_MULTI_MARKET_ANALYSIS_V1"
U="REAL_ANALYSIS_UNIT_V1"
S="REAL_STRATEGY_SOURCE_V1"
L="REAL_STRATEGY_PLAN_V1"
F="GENERIC_FUSION_RUN_V1"
Q="IMPORTED_LLM_REVIEW_V4"
V="DECISION_LOCK_V2"
OUTCOME="OUTCOME_CANDIDATE_V1"
C="SYSTEM_TICKET_CANDIDATE_V2"
D="PORTFOLIO_RETURN_DISTRIBUTION_V1"
EV="RETURN_EVALUATION_V1"
SP="REAL_SP_SOURCE_BINDING_V1"
AD="REAL_SOURCE_ADMISSION_FACT_V1"
PIN="REAL_MODEL_PIN_V1"
POL="RETURN_DISTRIBUTION_POLICY_V1"
OBJ="RETURN_OBJECTIVE_PROFILE_V1"
RULES = {
 P:{}, "REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1":{}, "PROSPECTIVE_POLICY_V1":{}, POL:{}, OBJ:{},
  "REAL_POLICY_VALUE_PIN_V1":{"program":P,"prospective_policy":"PROSPECTIVE_POLICY_V1","return_policy":POL,"objective":OBJ},
 AD:{"program":P,"previous":AD}, PIN:{"program":P,"admission":AD},
  "REAL_MODEL_PIN_INVALIDATION_V1":{"pin":PIN},
  "REAL_SOURCE_INVALIDATION_V1":{"source":("REAL_MARKET_SOURCE_BINDING_V1",SP)},
 A:{"program":P,"implementation":"REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1","policy":"REAL_POLICY_VALUE_PIN_V1","model_pin":PIN},
  E:{"program":P,"anchor":A,"implementation":"REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1","previous":E}, "REAL_SLATE_DECLARATION_V1":{"epoch":E,"program":P},
 B:{"epoch":E,"program":P,"slate":"REAL_SLATE_DECLARATION_V1"},
 "REAL_MARKET_SOURCE_BINDING_V1":{"program":P,"admission":AD}, SP:{"program":P,"admission":AD},
 "REAL_MODEL_AVAILABILITY_FACT_V1":{"pin":PIN},
 U:{"consensus":"REAL_MARKET_SOURCE_BINDING_V1","sporttery":SP,"model":"REAL_MODEL_AVAILABILITY_FACT_V1", "evidence[*]":"FOOTBALL_EVIDENCE_V1",
    "evidence_uses[*].snapshot":"EVIDENCE_SNAPSHOT_V1","evidence_uses[*].binding":"PROSPECTIVE_EVIDENCE_BINDING_V1","evidence_uses[*].football_evidence":"FOOTBALL_EVIDENCE_V1"},
 N:{"program":P,"epoch":E,"anchor":A,"bucket":B,"units[*]":U},
  R:{"program":P,"epoch":E,"anchor":A,"bucket":B,"analysis":N,"packet":"ANALYSIS_PACKET_V4","previous":R,"chain_root":R,"supersedes_lock":V,
    "evidence[*].snapshot":"EVIDENCE_SNAPSHOT_V1","evidence[*].binding":"PROSPECTIVE_EVIDENCE_BINDING_V1","evidence[*].football_evidence":"FOOTBALL_EVIDENCE_V1"},
  "PRE_LOCK_REPLACEMENT_V1":{"old_run":R,"new_run":R,"bucket":B,"epoch":E},
 "REAL_REVIEW_AUDIT_V1":{"run":R,"review":Q},
  S:{"analysis":N,"review":Q,"fusion":F,"selections[*]":OUTCOME},
 L:{"source":S,"candidates[*]":C,"tickets[*]":"SYSTEM_TICKET_V2"},
 "REAL_CALCULATION_BINDING_V1":{"run":R,"plan":L,"optimizer":"RETURN_OPTIMIZATION_RUN_V1","review_audit":"REAL_REVIEW_AUDIT_V1"},
 V:{"program":P,"run":R,"epoch":E,"anchor":A,"bucket":B,"analysis":N,"packet":"ANALYSIS_PACKET_V4","review":Q,
    "calculation":"REAL_CALCULATION_BINDING_V1","strategy_plan":L,"return_evaluation":EV,"frames[*].unit":U,"frames[*].sp_snapshot":SP},
 "REAL_PRE_KICKOFF_INVALIDATION_V1":{"old_lock":V,"new_lock":V},
 "REAL_RESULT_OBSERVATION_V1":{"program":P,"admission":AD,"previous":"REAL_RESULT_OBSERVATION_V1"},
 "REAL_PROSPECTIVE_SETTLEMENT_V1":{"run":R,"decision_lock":V,"previous":"REAL_PROSPECTIVE_SETTLEMENT_V1","observations[*]":"REAL_RESULT_OBSERVATION_V1"},
 "REAL_VALIDATION_REPORT_V1":{"program":P,"epoch":E,"anchor":A,"census[*].run":R,"census[*].lock":V,
    "census[*].settlement":"REAL_PROSPECTIVE_SETTLEMENT_V1","census[*].replacement":"PRE_LOCK_REPLACEMENT_V1"},
 "REAL_EPOCH_CLOSE_V1":{"epoch":E},
 "FOOTBALL_EVIDENCE_V1":{},
 "ANALYSIS_PACKET_V4":{"analysis":N,"market_units[*].review_context":"MARKET_REVIEW_CONTEXT_V4"},
 "MARKET_REVIEW_CONTEXT_V4":{"evidence[*]":"FOOTBALL_EVIDENCE_V1"},
 Q:{"packet":"ANALYSIS_PACKET_V4"}, F:{"analysis":N,"review":Q},
  OUTCOME:{"analysis":N,"fusion":F,"snapshot":SP}, "MATCH_CHOICE_SET_V1":{"candidates[*]":OUTCOME},
  "EXPANDED_ATOMIC_BET_V2":{"legs[*]":OUTCOME},
 C:{"source":S,"choice_sets[*]":"MATCH_CHOICE_SET_V1","atomic_bets[*]":"EXPANDED_ATOMIC_BET_V2"},
 "SYSTEM_TICKET_V2":{"candidate":C},
 "RELEVANT_MATCH_STATE_V1":{"unit":U,"fusion":F,"sp_snapshot":SP},
  "TICKET_RETURN_FUNCTION_V1":{"candidate":C,"source":S,"atomic_bets[*].atomic":"EXPANDED_ATOMIC_BET_V2","atomic_bets[*].legs[*].outcome_candidate":OUTCOME},
 D:{"policy":POL,"matches[*]":"RELEVANT_MATCH_STATE_V1","ticket_functions[*]":"TICKET_RETURN_FUNCTION_V1"},
 "RETURN_DISTRIBUTION_METRICS_V1":{"distribution":D},
 EV:{"policy":POL,"objective":OBJ,"distribution":D,"metrics":"RETURN_DISTRIBUTION_METRICS_V1"},
 "RETURN_OPTIMIZATION_RUN_V1":{"policy":POL,"objective":OBJ,"baseline":EV,"result":EV,"legacy":EV,"candidate_catalog[*]":C},
}
for kind in (D,EV,"RETURN_OPTIMIZATION_RUN_V1"):
    RULES[kind].update({"binding.plan":L,"binding.source":S,"binding.analysis":N,"binding.fusion":F})
EXTERNAL = {"EVIDENCE_SNAPSHOT_V1":"pv_artifacts", "PROSPECTIVE_EVIDENCE_BINDING_V1":"pv_artifacts"}


def reference_index(raw):
    body = json.loads(raw)
    rules = RULES[body["schema_version"]]
    found = []
    def visit(value, path):
        if isinstance(value, dict):
            if path and {"schema_version","artifact_id","content_hash"} <= value.keys():
                pattern = re.sub(r"\[\d+\]", "[*]", path)
                allowed=rules.get(pattern)
                if value["schema_version"] not in (allowed if isinstance(allowed,tuple) else (allowed,)):
                    raise ValueError("RB_CLOSED_REFERENCE_ROLE_MISMATCH:"+pattern)
                found.append(dict(path=path, target_id=value["artifact_id"], target_schema=value["schema_version"], target_hash=value["content_hash"],
                    external=value["schema_version"] in EXTERNAL))
                return
            for key, child in value.items():
                visit(child, (path+"." if path else "")+key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, path+f"[{i}]")
    visit(body, "")
    found.extend(scalar_references(body))
    return found


def _json_refs(raw):
    return json.dumps(reference_index(raw), separators=(",",":"), sort_keys=True)


def register_functions(connection, *_):
    connection.create_function("rb_refs_v1", 1, _json_refs, deterministic=True)
    connection.create_function("rb_children_v1", 1, lambda raw: canonical_json(child_rows(raw)), deterministic=True)
    connection.create_function("rb_valid_header_v1", 1, valid_header, deterministic=True)
    connection.create_function("rb_us_v1", 1, utc_micros, deterministic=True)
    connection.create_function("rb_valid_request_v1",4,valid_request,deterministic=True)


def valid_request(operation,key,raw,digest):
    from football_system.application.real_bridge_requests import REQUEST_TYPES
    from football_system.infrastructure.files.return_distribution import strict_return_json
    try:
        value=REQUEST_TYPES[operation].model_validate(strict_return_json(raw.encode()))
        return int(value.request_key==key and canonical_json(value)==raw and content_hash("REAL_REQUEST_"+operation,value)==digest)
    except (ValueError,KeyError,TypeError):
        return 0


def utc_micros(value):
    from datetime import timezone
    at = datetime.fromisoformat(value.replace("Z","+00:00"))
    if at.tzinfo is None or at.utcoffset().total_seconds() != 0:
        raise ValueError("UTC_REQUIRED")
    delta = at-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def valid_header(raw):
    from football_system.domain.common import stable_id
    from football_system.infrastructure.files.return_distribution import strict_return_json
    value = strict_return_json(raw.encode(),limit=64*1024*1024)
    if value.get("schema_version") not in ARTIFACTS or canonical_json(value) != raw:
        return 0
    fields = {k:v for k,v in value.items() if k not in {"artifact_id","content_hash"}}
    digest = content_hash(value["schema_version"],fields)
    if value["content_hash"] != digest or value["artifact_id"] != stable_id(value["schema_version"],digest):
        return 0
    try:
        validated=ARTIFACTS[value["schema_version"]].model_validate(value)
        return int(canonical_json(validated)==raw)
    except (ValueError,TypeError,KeyError):
        return 0


event.listen(Engine, "connect", register_functions)

# Direct external facts get concrete FKs, not schema-free ID strings.
EXTERNAL_COLUMNS = {
 "rb_model_pins": {"release_id":("production_quant_model_releases","release_id"),"model_state_id":("quant_model_states","quant_model_state_id"),"source_analysis_id":("analysis_runs","analysis_run_id")},
 "rb_market_bindings": {"ingestion_id":("live_source_ingestions","ingestion_id"),"snapshot_id":("market_odds_snapshots","snapshot_id"),"match_id":("matches","internal_match_id")},
 "rb_sp_bindings": {"ingestion_id":("live_source_ingestions","ingestion_id"),"snapshot_id":("sporttery_bonus_snapshots","snapshot_id"),"match_id":("matches","internal_match_id")},
}


def real_bridge_tables(metadata):
    def col(name, typ=None, **kw):
        return sa.Column(name, typ or sa.String(160), nullable=kw.pop("nullable",False), **kw)
    def fk(columns, table, targets=None, alter=False):
        return sa.ForeignKeyConstraint(columns,[table+"."+c for c in (targets or columns)], ondelete="RESTRICT", deferrable=True, initially="DEFERRED", use_alter=alter)
    for table,key in {("matches","internal_match_id"),("pv_artifacts","artifact_id"), *(v for fields in EXTERNAL_COLUMNS.values() for v in fields.values())}:
        if table not in metadata.tables:
            sa.Table(table, metadata, col(key,primary_key=True))
    for _, fields in CHILDREN.values():
        for target in fields.values():
            if target and not target.startswith("rb_"):
                table,key = target.split(".")
                if table not in metadata.tables:
                    sa.Table(table,metadata,col(key,primary_key=True))
    for name,keys in (("live_market_consensus_lineages",("ingestion_id","consensus_snapshot_id")),
                      ("live_market_consensus_constituents",("ingestion_id","consensus_snapshot_id","source_snapshot_id")),
                      ("live_source_ingestion_sporttery_snapshots",("ingestion_id","snapshot_id"))):
        if name not in metadata.tables:
            sa.Table(name,metadata,*(col(k,primary_key=True) for k in keys))
    tables={}
    def table(name,*items):
        tables[name]=sa.Table(name,metadata,*items)
    table("rb_receipts",col("request_key",primary_key=True),col("operation"),col("request_hash"),col("request_json",sa.Text()),
        col("sequence",sa.Integer(),unique=True),col("event_at"),col("event_us",sa.BigInteger()),col("clock_basis"),col("provenance"),
        col("response_id",nullable=True),col("epoch_id",nullable=True),col("outcome"),col("reason",nullable=True),col("source_limits_json",sa.Text()),col("anchor_sealed_at",nullable=True),
        fk(["response_id"],"rb_artifacts",["artifact_id"],True),fk(["epoch_id"],"rb_epochs",["artifact_id"],True),
        sa.CheckConstraint("(outcome='COMMITTED' AND response_id IS NOT NULL AND reason IS NULL) OR (outcome='REJECTED' AND response_id IS NULL AND reason IS NOT NULL)"),
        sa.CheckConstraint("sequence>0 AND clock_basis IN ('LOCAL_SYSTEM_UTC','SYNTHETIC_TEST_CLOCK') AND provenance IN ('LIVE_OBSERVATION','SYNTHETIC_SOFTWARE_ACCEPTANCE')"))
    table("rb_artifacts",col("artifact_id",primary_key=True),col("schema_version"),col("content_hash"),col("artifact_json",sa.Text()),
        col("event_key"),fk(["event_key"],"rb_receipts",["request_key"],True),fk(["artifact_id"],"rb_seals",alter=True),
        sa.UniqueConstraint("artifact_id","schema_version","content_hash"),sa.CheckConstraint("json_valid(artifact_json)"))
    for kind,name in KIND_TABLE.items():
        columns=[col("artifact_id",primary_key=True),fk(["artifact_id"],"rb_artifacts")]
        for field,(target,key) in EXTERNAL_COLUMNS.get(name,{}).items():
            columns += [col(field,nullable=name=="rb_model_pins"),fk([field],target,[key])]
        if name=="rb_market_bindings":
            columns.append(fk(["ingestion_id","snapshot_id"],"live_market_consensus_lineages",["ingestion_id","consensus_snapshot_id"]))
        if name=="rb_sp_bindings":
            columns.append(fk(["ingestion_id","snapshot_id"],"live_source_ingestion_sporttery_snapshots"))
        table(name,*columns)
    table("rb_market_keys",col("market_hash",primary_key=True),col("market_json",sa.Text()),
        sa.CheckConstraint(sa.and_(sa.column("market_hash")==THREE_WAY.market_hash,sa.column("market_json")==canonical_json(THREE_WAY))))
    for name,(kind,fields) in CHILDREN.items():
        columns=[col("parent_id",primary_key=True),col("position",sa.Integer(),primary_key=True),
                 fk(["parent_id"],KIND_TABLE[kind],["artifact_id"]),sa.CheckConstraint("position>=0")]
        for key,target in fields.items():
            keyname=key.rstrip("?")
            columns.append(col(keyname,sa.Text() if keyname.endswith("json") else None,nullable=key.endswith("?")))
            if target:
                target_table,target_key=target.split(".")
                columns.append(fk([keyname],target_table,[target_key]))
        if name=="rb_market_constituents":
            columns.append(fk(["ingestion_id","consensus_snapshot_id","source_snapshot_id"],"live_market_consensus_constituents"))
        if "match_id" in fields or "unit_id" in fields:
            columns.append(sa.UniqueConstraint("parent_id","match_id" if "match_id" in fields else "unit_id"))
        table(name,*columns)
    table("rb_chains",col("chain_id",primary_key=True),col("epoch_id"),col("bucket_id"),col("predecessor_lock_id",nullable=True,unique=True),
        fk(["chain_id"],"rb_runs",["artifact_id"]),fk(["epoch_id"],"rb_epochs",["artifact_id"]),fk(["bucket_id"],"rb_buckets",["artifact_id"]),
        fk(["predecessor_lock_id"],"rb_locks",["artifact_id"]))
    table("rb_chain_runs",col("run_id",primary_key=True),col("chain_id"),col("position",sa.Integer()),
        fk(["run_id"],"rb_runs",["artifact_id"]),fk(["chain_id"],"rb_chains"),sa.UniqueConstraint("chain_id","position"))
    table("rb_math_nodes",col("artifact_id",primary_key=True),col("kind"),fk(["artifact_id"],"rb_artifacts"))
    table("rb_math_edges",col("parent_id",primary_key=True),col("path",primary_key=True),col("parent_schema"),col("parent_hash"),col("target_id"),col("target_schema"),col("target_hash"),
        fk(["parent_id","parent_schema","parent_hash"],"rb_artifacts",["artifact_id","schema_version","content_hash"]),fk(["target_id","target_schema","target_hash"],"rb_artifacts",["artifact_id","schema_version","content_hash"]))
    table("rb_external_edges",col("parent_id",primary_key=True),col("path",primary_key=True),col("parent_schema"),col("parent_hash"),col("target_id"),col("target_schema"),col("target_hash"),
        fk(["parent_id","parent_schema","parent_hash"],"rb_artifacts",["artifact_id","schema_version","content_hash"]),fk(["target_id"],"pv_artifacts",["artifact_id"]))
    table("rb_seals",col("artifact_id",primary_key=True),fk(["artifact_id"],"rb_artifacts"))
    table("rb_head_consumptions",col("parent_run_id",primary_key=True),col("action"),col("new_run_id",nullable=True,unique=True),col("lock_id",nullable=True,unique=True),
        col("event_id"),fk(["parent_run_id"],"rb_runs",["artifact_id"]),fk(["new_run_id"],"rb_runs",["artifact_id"]),
        fk(["lock_id"],"rb_locks",["artifact_id"]),fk(["event_id"],"rb_artifacts",["artifact_id"]),
        sa.CheckConstraint("(action='REPLACE' AND new_run_id IS NOT NULL AND lock_id IS NULL) OR (action='LOCK' AND lock_id IS NOT NULL AND new_run_id IS NULL)"))
    table("rb_prediction_slots",col("program_id",primary_key=True),col("match_id",primary_key=True),col("market_hash",primary_key=True),col("initial_lock_id"),
        fk(["program_id"],"rb_programs",["artifact_id"]),fk(["match_id"],"matches",["internal_match_id"]),fk(["initial_lock_id"],"rb_locks",["artifact_id"]),fk(["market_hash"],"rb_market_keys"))
    table("rb_prediction_versions",col("program_id",primary_key=True),col("match_id",primary_key=True),col("market_hash",primary_key=True),col("version",sa.Integer(),primary_key=True),
        col("previous_version",sa.Integer(),nullable=True),col("lock_id"),fk(["program_id","match_id","market_hash"],"rb_prediction_slots"),
        fk(["program_id","match_id","market_hash","previous_version"],"rb_prediction_versions",["program_id","match_id","market_hash","version"]),
        fk(["lock_id"],"rb_locks",["artifact_id"]),sa.UniqueConstraint("program_id","match_id","market_hash","previous_version"),
        sa.CheckConstraint("(version=0 AND previous_version IS NULL) OR (version>0 AND previous_version=version-1)"))
    return tables


def real_bridge_triggers():
    statements={}
    def trigger(name, table, predicate, op="INSERT"):
        statements[name]=f"CREATE TRIGGER {name} BEFORE {op} ON {table} WHEN {predicate} BEGIN SELECT RAISE(ABORT,'real bridge ledger invariant'); END"
    for name,table in real_bridge_tables(sa.MetaData()).items():
        for op in ("UPDATE","DELETE"):
            trigger("trg_"+name+"_"+op.lower(),name,"1",op)
        keys=" AND ".join("x."+c.name+"=NEW."+c.name for c in table.primary_key)
        trigger("trg_"+name+"_replace",name,f"EXISTS(SELECT 1 FROM {name} x WHERE {keys})")
    trigger("trg_rb_header","rb_artifacts","rb_valid_header_v1(NEW.artifact_json)<>1 OR json_extract(NEW.artifact_json,'$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.artifact_json,'$.schema_version') IS NOT NEW.schema_version OR json_extract(NEW.artifact_json,'$.content_hash') IS NOT NEW.content_hash")
    trigger("trg_rb_receipt_clock","rb_receipts","rb_us_v1(NEW.event_at) IS NOT NEW.event_us OR NEW.sequence<>COALESCE((SELECT MAX(sequence) FROM rb_receipts),0)+1 OR NEW.event_us<COALESCE((SELECT MAX(event_us) FROM rb_receipts),NEW.event_us) OR (NEW.provenance='LIVE_OBSERVATION' AND (NEW.clock_basis<>'LOCAL_SYSTEM_UTC' OR ABS(NEW.event_us-CAST(strftime('%s','now') AS INTEGER)*1000000)>30000000))")
    trigger("trg_rb_request","rb_receipts","NEW.outcome='COMMITTED' AND rb_valid_request_v1(NEW.operation,NEW.request_key,NEW.request_json,NEW.request_hash)<>1")
    for kind,name in KIND_TABLE.items():
        checks=[f"a.schema_version='{kind}'"]
        checks += [f"NEW.{key} IS json_extract(a.artifact_json,'$.{key}')" for key in EXTERNAL_COLUMNS.get(name,{})]
        trigger("trg_"+name+"_type",name,"EXISTS(SELECT 1 FROM rb_seals WHERE artifact_id=NEW.artifact_id) OR NOT EXISTS(SELECT 1 FROM rb_artifacts a WHERE a.artifact_id=NEW.artifact_id AND "+" AND ".join(checks)+")")
    math_kinds=",".join("'"+k+"'" for k in sorted(MATH_KINDS))
    trigger("trg_rb_math_type","rb_math_nodes",f"EXISTS(SELECT 1 FROM rb_seals WHERE artifact_id=NEW.artifact_id) OR NOT EXISTS(SELECT 1 FROM rb_artifacts a WHERE a.artifact_id=NEW.artifact_id AND a.schema_version=NEW.kind AND NEW.kind IN ({math_kinds}))")
    for table,external in (("rb_math_edges",0),("rb_external_edges",1)):
        trigger("trg_"+table+"_projection",table,"EXISTS(SELECT 1 FROM rb_seals WHERE artifact_id=NEW.parent_id) OR NOT EXISTS(SELECT 1 FROM rb_artifacts a,json_each(rb_refs_v1(a.artifact_json)) j WHERE a.artifact_id=NEW.parent_id AND json_extract(j.value,'$.path')=NEW.path AND json_extract(j.value,'$.target_id')=NEW.target_id AND json_extract(j.value,'$.target_schema')=NEW.target_schema AND (json_extract(j.value,'$.target_hash') IS NULL OR json_extract(j.value,'$.target_hash')=NEW.target_hash) AND json_extract(j.value,'$.external')="+str(external)+")")
    trigger("trg_rb_external_types","rb_external_edges","NOT EXISTS(SELECT 1 FROM pv_artifacts a WHERE a.artifact_id=NEW.target_id AND a.schema_version=NEW.target_schema AND a.content_hash=NEW.target_hash)")
    types=" OR ".join(f"(a.schema_version='{k}' AND EXISTS(SELECT 1 FROM {t} WHERE artifact_id=a.artifact_id))" for k,t in KIND_TABLE.items())
    types+=f" OR (a.schema_version IN ({math_kinds}) AND EXISTS(SELECT 1 FROM rb_math_nodes WHERE artifact_id=a.artifact_id))"
    trigger("trg_rb_complete","rb_seals","NOT EXISTS(SELECT 1 FROM rb_artifacts a JOIN rb_receipts r ON r.request_key=a.event_key WHERE a.artifact_id=NEW.artifact_id AND ("+types+") AND json_array_length(rb_refs_v1(a.artifact_json))=(SELECT COUNT(*) FROM rb_math_edges WHERE parent_id=a.artifact_id)+(SELECT COUNT(*) FROM rb_external_edges WHERE parent_id=a.artifact_id))")
    for name,(_,fields) in CHILDREN.items():
        checks=" AND ".join(f"json_extract(j.value,'$.row.{key.rstrip('?')}') IS NEW.{key.rstrip('?')}" for key in fields)
        trigger("trg_"+name+"_projection",name,"EXISTS(SELECT 1 FROM rb_seals WHERE artifact_id=NEW.parent_id) OR NOT EXISTS(SELECT 1 FROM rb_artifacts a,json_each(rb_children_v1(a.artifact_json)) j WHERE a.artifact_id=NEW.parent_id AND json_extract(j.value,'$.table')='"+name+"' AND json_extract(j.value,'$.row.position')=NEW.position AND "+checks+")")
    counts="+".join(f"(SELECT count(*) FROM {name} WHERE parent_id=NEW.artifact_id)" for name in CHILDREN)
    trigger("trg_rb_complete_children","rb_seals",f"(SELECT json_array_length(rb_children_v1(artifact_json)) FROM rb_artifacts WHERE artifact_id=NEW.artifact_id)<>({counts})")
    trigger("trg_rb_artifact_receipt","rb_seals","EXISTS(SELECT 1 FROM rb_artifacts a JOIN rb_receipts r ON r.request_key=a.event_key WHERE a.artifact_id=NEW.artifact_id AND (r.outcome<>'COMMITTED' OR (json_type(a.artifact_json,'$.event_at_utc') IS NOT NULL AND (rb_us_v1(json_extract(a.artifact_json,'$.event_at_utc'))<>r.event_us OR json_extract(a.artifact_json,'$.receipt_sequence')<>r.sequence OR json_extract(a.artifact_json,'$.clock_basis')<>r.clock_basis OR json_extract(a.artifact_json,'$.provenance')<>r.provenance))))")
    statements.update(ownership_triggers())
    # JSON expression indexes enforce immutable business identities without altering old tables.
    return statements


def ownership_triggers():
    """DB-side head arbitration and slot progression, not a CLI pre-check."""
    result={}
    def guard(name,table,condition):
        result[name]=f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'real bridge ownership invariant'); END"
    def j(alias,path):
        return f"json_extract({alias}.artifact_json,'$.{path}')"
    same=" AND ".join(f"{j('p',role+'.artifact_id')}={j('n',role+'.artifact_id')}" for role in ("epoch","anchor","bucket","program"))
    guard("trg_rb_chain_projection","rb_chain_runs",f"NOT EXISTS(SELECT 1 FROM rb_artifacts r WHERE r.artifact_id=NEW.run_id AND {j('r','position')}=NEW.position AND COALESCE({j('r','chain_root.artifact_id')},r.artifact_id)=NEW.chain_id)")
    guard("trg_rb_chain_root","rb_chains",f"NOT EXISTS(SELECT 1 FROM rb_artifacts r WHERE r.artifact_id=NEW.chain_id AND {j('r','position')}=0 AND {j('r','epoch.artifact_id')}=NEW.epoch_id AND {j('r','bucket.artifact_id')}=NEW.bucket_id AND {j('r','supersedes_lock.artifact_id')} IS NEW.predecessor_lock_id)")
    replace=(f"NEW.action='REPLACE' AND n.artifact_id=NEW.new_run_id AND e.artifact_id=NEW.event_id AND e.schema_version='PRE_LOCK_REPLACEMENT_V1' AND {j('e','old_run.artifact_id')}=p.artifact_id AND {j('e','new_run.artifact_id')}=n.artifact_id AND {j('n','previous.artifact_id')}=p.artifact_id AND {j('n','status')}='PREPARING' AND {j('n','position')}={j('p','position')}+1 AND {j('n','budget_fen')}={j('p','budget_fen')} AND {j('n','supersedes_lock.artifact_id')} IS {j('p','supersedes_lock.artifact_id')}")
    lock=(f"NEW.action='LOCK' AND n.artifact_id=NEW.lock_id AND e.artifact_id=n.artifact_id AND n.schema_version='DECISION_LOCK_V2' AND {j('n','run.artifact_id')}=p.artifact_id")
    guard("trg_rb_head_semantics","rb_head_consumptions",f"NOT EXISTS(SELECT 1 FROM rb_artifacts p,rb_artifacts n,rb_artifacts e,rb_artifacts b WHERE p.artifact_id=NEW.parent_run_id AND p.schema_version='REAL_PROSPECTIVE_RUN_V1' AND {j('p','status')}='PREPARING' AND {same} AND b.artifact_id={j('p','bucket.artifact_id')} AND rb_us_v1({j('n','event_at_utc')})>=rb_us_v1({j('p','event_at_utc')}) AND rb_us_v1({j('n','event_at_utc')})+60000000<rb_us_v1({j('b','kickoff_at_utc')}) AND (({replace}) OR ({lock})))")
    guard("trg_rb_slot_initial","rb_prediction_slots",f"NOT EXISTS(SELECT 1 FROM rb_artifacts l,json_each(l.artifact_json,'$.frames') f WHERE l.artifact_id=NEW.initial_lock_id AND l.schema_version='DECISION_LOCK_V2' AND {j('l','program.artifact_id')}=NEW.program_id AND json_extract(f.value,'$.identity.match_id')=NEW.match_id)")
    guard("trg_rb_version_semantics","rb_prediction_versions",f"NOT EXISTS(SELECT 1 FROM rb_prediction_slots s,rb_artifacts n,json_each(n.artifact_json,'$.frames') f WHERE s.program_id=NEW.program_id AND s.match_id=NEW.match_id AND s.market_hash=NEW.market_hash AND n.artifact_id=NEW.lock_id AND n.schema_version='DECISION_LOCK_V2' AND {j('n','program.artifact_id')}=s.program_id AND json_extract(f.value,'$.identity.match_id')=s.match_id AND ((NEW.version=0 AND NEW.lock_id=s.initial_lock_id) OR (NEW.version>0 AND EXISTS(SELECT 1 FROM rb_prediction_versions v,rb_artifacts p,rb_artifacts i WHERE v.program_id=s.program_id AND v.match_id=s.match_id AND v.market_hash=s.market_hash AND v.version=NEW.previous_version AND p.artifact_id=v.lock_id AND i.schema_version='REAL_PRE_KICKOFF_INVALIDATION_V1' AND {j('i','old_lock.artifact_id')}=p.artifact_id AND {j('i','new_lock.artifact_id')}=n.artifact_id AND {same}))))")
    guard("trg_rb_lock_complete","rb_seals",f"EXISTS(SELECT 1 FROM rb_artifacts l WHERE l.artifact_id=NEW.artifact_id AND l.schema_version='DECISION_LOCK_V2' AND ((SELECT count(*) FROM rb_prediction_versions WHERE lock_id=l.artifact_id)<>json_array_length(l.artifact_json,'$.frames') OR NOT EXISTS(SELECT 1 FROM rb_head_consumptions h WHERE h.lock_id=l.artifact_id AND h.parent_run_id={j('l','run.artifact_id')})))")
    guard("trg_rb_run_complete","rb_seals",f"EXISTS(SELECT 1 FROM rb_artifacts r WHERE r.artifact_id=NEW.artifact_id AND r.schema_version='REAL_PROSPECTIVE_RUN_V1' AND (NOT EXISTS(SELECT 1 FROM rb_chain_runs c WHERE c.run_id=r.artifact_id) OR ({j('r','position')}>0 AND NOT EXISTS(SELECT 1 FROM rb_head_consumptions h WHERE h.action='REPLACE' AND h.new_run_id=r.artifact_id))))")
    return result


INDEXES = (
 "CREATE UNIQUE INDEX ux_rb_program_name ON rb_artifacts(json_extract(artifact_json,'$.observation_program_id')) WHERE schema_version='REAL_OBSERVATION_PROGRAM_V1'",
 "CREATE UNIQUE INDEX ux_rb_epoch_anchor ON rb_artifacts(json_extract(artifact_json,'$.anchor.artifact_id')) WHERE schema_version='REAL_VALIDATION_EPOCH_V1'",
 "CREATE UNIQUE INDEX ux_rb_epoch_first ON rb_artifacts(json_extract(artifact_json,'$.program.artifact_id')) WHERE schema_version='REAL_VALIDATION_EPOCH_V1' AND json_extract(artifact_json,'$.previous') IS NULL",
 "CREATE UNIQUE INDEX ux_rb_epoch_next ON rb_artifacts(json_extract(artifact_json,'$.previous.artifact_id')) WHERE schema_version='REAL_VALIDATION_EPOCH_V1' AND json_extract(artifact_json,'$.previous') IS NOT NULL",
 "CREATE UNIQUE INDEX ux_rb_close_once ON rb_artifacts(json_extract(artifact_json,'$.epoch.artifact_id')) WHERE schema_version='REAL_EPOCH_CLOSE_V1'",
 "CREATE UNIQUE INDEX ux_rb_initial_run ON rb_artifacts(json_extract(artifact_json,'$.epoch.artifact_id'),json_extract(artifact_json,'$.bucket.artifact_id'),COALESCE(json_extract(artifact_json,'$.supersedes_lock.artifact_id'),'')) WHERE schema_version='REAL_PROSPECTIVE_RUN_V1' AND json_extract(artifact_json,'$.previous') IS NULL",
 "CREATE UNIQUE INDEX ux_rb_run_next ON rb_artifacts(json_extract(artifact_json,'$.previous.artifact_id')) WHERE schema_version='REAL_PROSPECTIVE_RUN_V1' AND json_extract(artifact_json,'$.previous') IS NOT NULL",
 "CREATE UNIQUE INDEX ux_rb_lock_once ON rb_artifacts(json_extract(artifact_json,'$.run.artifact_id')) WHERE schema_version='DECISION_LOCK_V2'",
 "CREATE UNIQUE INDEX ux_rb_invalidate_once ON rb_artifacts(json_extract(artifact_json,'$.old_lock.artifact_id')) WHERE schema_version='REAL_PRE_KICKOFF_INVALIDATION_V1'",
 "CREATE UNIQUE INDEX ux_rb_admission_next ON rb_artifacts(json_extract(artifact_json,'$.previous.artifact_id')) WHERE schema_version='REAL_SOURCE_ADMISSION_FACT_V1' AND json_extract(artifact_json,'$.previous') IS NOT NULL",
 "CREATE UNIQUE INDEX ux_rb_admission_first ON rb_artifacts(json_extract(artifact_json,'$.program.artifact_id'),json_extract(artifact_json,'$.source_identity'),json_extract(artifact_json,'$.kind')) WHERE schema_version='REAL_SOURCE_ADMISSION_FACT_V1' AND json_extract(artifact_json,'$.previous') IS NULL",
 "CREATE UNIQUE INDEX ux_rb_source_invalidation ON rb_artifacts(json_extract(artifact_json,'$.source.artifact_id')) WHERE schema_version='REAL_SOURCE_INVALIDATION_V1'",
)


def install_real_bridge_triggers(connection):
    if connection.scalar(sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='rb_artifacts'")):
        for name,statement in real_bridge_triggers().items():
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            connection.exec_driver_sql(statement)
        for statement in INDEXES:
            connection.exec_driver_sql(statement.replace("CREATE UNIQUE INDEX ","CREATE UNIQUE INDEX IF NOT EXISTS "))


RB_TABLES = tuple(real_bridge_tables(sa.MetaData()))


@lru_cache(maxsize=1)
def schema_definitions():
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    values={name:str(CreateTable(table).compile(dialect=dialect())).strip() for name,table in real_bridge_tables(sa.MetaData()).items()}
    values.update(real_bridge_triggers())
    values.update({statement.split()[3]:statement for statement in INDEXES})
    return {name:schema_signature(statement) for name,statement in values.items()}


def schema_signature(statement):
    """Alembic copies can reorder table constraints. Columns and every constraint
    remain exact; only the order of independent table-level constraints is ignored.
    """
    statement=" ".join(statement.split())
    if not statement.startswith("CREATE TABLE "):
        return statement
    begin,end=statement.index("("),statement.rindex(")")
    body=statement[begin+1:end]
    parts=[]
    quote=None
    depth=start=0
    for index,char in enumerate(body):
        if quote:
            if char==quote:
                quote=None
        elif char in "'\"":
            quote=char
        elif char=="(":
            depth+=1
        elif char==")":
            depth-=1
        elif char=="," and depth==0:
            parts.append(body[start:index].strip())
            start=index+1
    parts.append(body[start:].strip())
    keywords=("PRIMARY KEY","FOREIGN KEY","CHECK ","UNIQUE ","CONSTRAINT ")
    columns=tuple(p for p in parts if not p.startswith(keywords))
    constraints=tuple(sorted(p for p in parts if p.startswith(keywords)))
    return statement[:begin].strip(),columns,constraints,statement[end+1:]


def assert_real_schema(connection):
    expected=schema_definitions()
    rows=connection.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE name LIKE 'rb_%' OR name LIKE 'trg_rb_%' OR name LIKE 'ux_rb_%'")).all()
    actual={name:schema_signature(sql) for name,sql in rows if sql is not None}
    if actual!=expected:
        raise ValueError("REAL_LEDGER_SCHEMA_OR_GUARDS_CHANGED")
