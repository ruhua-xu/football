"""Closed relational projections of the real graph, including scalar ID references.

These are identity/storage operations only. No prediction or portfolio math lives here.
"""

import json

from football_system.domain.real_bridge import THREE_WAY


# column: (SQL type, concrete FK table/key); nullable references are explicit.
CHILDREN = {
    "rb_anchor_policies": ("REAL_EPOCH_CONFIGURATION_ANCHOR_V1", {"policy_id": "rb_policy_values.artifact_id"}),
    "rb_anchor_model_pins": ("REAL_EPOCH_CONFIGURATION_ANCHOR_V1", {"pin_id": "rb_model_pins.artifact_id"}),
    "rb_slate_members": ("REAL_SLATE_DECLARATION_V1", {"match_id": "matches.internal_match_id", "observation_id": "fixture_observations.observation_id", "market_hash": "rb_market_keys.market_hash"}),
    "rb_bucket_members": ("REAL_KICKOFF_BUCKET_V1", {"match_id": "matches.internal_match_id", "observation_id": "fixture_observations.observation_id", "market_hash": "rb_market_keys.market_hash"}),
    "rb_market_constituents": ("REAL_MARKET_SOURCE_BINDING_V1", {"ingestion_id": "live_source_ingestions.ingestion_id", "consensus_snapshot_id": "market_odds_snapshots.snapshot_id", "source_snapshot_id": "market_odds_snapshots.snapshot_id", "payload_hash": None}),
    "rb_analysis_members": ("REAL_MULTI_MARKET_ANALYSIS_V1", {"unit_id": "rb_analysis_units.artifact_id", "match_id": "matches.internal_match_id", "market_hash": "rb_market_keys.market_hash"}),
    "rb_unit_evidence": ("REAL_ANALYSIS_UNIT_V1", {"snapshot_id": "pv_evidence.artifact_id", "binding_id": "pv_evidence_bindings.artifact_id", "football_id": "mm_evidence.artifact_id"}),
    "rb_packet_units": ("ANALYSIS_PACKET_V4", {"context_id": "rb_contexts.artifact_id", "match_id": "matches.internal_match_id", "market_hash": "rb_market_keys.market_hash"}),
    "rb_correction_reasons": ("REAL_REVIEW_AUDIT_V1", {"context_id": "rb_contexts.artifact_id", "reason_json": None}),
    "rb_reason_evidence": ("REAL_REVIEW_AUDIT_V1", {"context_id": "rb_contexts.artifact_id", "snapshot_id": "pv_evidence.artifact_id"}),
    "rb_fusion_units": ("GENERIC_FUSION_RUN_V1", {"unit_id": "rb_analysis_units.artifact_id"}),
    "rb_run_inputs": ("REAL_PROSPECTIVE_RUN_V1", {"binding_id": "pv_evidence_bindings.artifact_id", "snapshot_id": "pv_evidence.artifact_id", "football_id": "mm_evidence.artifact_id"}),
    "rb_lock_frames": ("DECISION_LOCK_V2", {"unit_id": "rb_analysis_units.artifact_id", "sp_id": "rb_sp_bindings.artifact_id", "match_id": "matches.internal_match_id"}),
    "rb_lock_selected": ("DECISION_LOCK_V2", {"candidate_id": "rb_math_nodes.artifact_id", "candidate_hash": None}),
    "rb_settlement_results": ("REAL_PROSPECTIVE_SETTLEMENT_V1", {"observation_id": "rb_observations.artifact_id"}),
    "rb_report_census": ("REAL_VALIDATION_REPORT_V1", {"request_key": "rb_receipts.request_key", "run_id?": "rb_runs.artifact_id", "lock_id?": "rb_locks.artifact_id", "settlement_id?": "rb_settlements.artifact_id", "replacement_id?": "rb_prelock_replacements.artifact_id", "status": None}),
}


def child_rows(raw):
    value = json.loads(raw)
    kind, parent = value["schema_version"], value["artifact_id"]
    found = []

    def add(table, rows):
        for position, row in enumerate(rows):
            found.append(dict(table=table, row=dict(parent_id=parent, position=position, **row)))

    def ref_id(value):
        return value["artifact_id"] if value else None

    def evidence(rows):
        return [dict(snapshot_id=ref_id(u["snapshot"]),binding_id=ref_id(u["binding"]),football_id=ref_id(u["football_evidence"])) for u in rows]

    if kind == "REAL_EPOCH_CONFIGURATION_ANCHOR_V1":
        add("rb_anchor_policies", [dict(policy_id=ref_id(value["policy"]))])
        add("rb_anchor_model_pins", [dict(pin_id=ref_id(value["model_pin"]))])
    if kind in {"REAL_SLATE_DECLARATION_V1", "REAL_KICKOFF_BUCKET_V1"}:
        add("rb_slate_members" if kind == "REAL_SLATE_DECLARATION_V1" else "rb_bucket_members",
            [dict(match_id=f["match_id"], observation_id=f["observation_id"],market_hash=THREE_WAY.market_hash) for f in value["fixture_refs"]])
    if kind == "REAL_MARKET_SOURCE_BINDING_V1":
        add("rb_market_constituents",[dict(ingestion_id=value["ingestion_id"],consensus_snapshot_id=value["snapshot_id"],
            source_snapshot_id=identity,payload_hash=digest) for identity,digest in value["constituent_refs"]])
    if kind == "REAL_MULTI_MARKET_ANALYSIS_V1":
        add("rb_analysis_members",[dict(unit_id=ref_id(u),match_id=u["identity"]["match_id"],market_hash=THREE_WAY.market_hash) for u in value["units"]])
    if kind == "REAL_ANALYSIS_UNIT_V1":
        add("rb_unit_evidence",evidence(value["evidence_uses"]))
    if kind == "ANALYSIS_PACKET_V4":
        add("rb_packet_units",[dict(context_id=u["review_context_id"],match_id=u["review_context"]["identity"]["match_id"],market_hash=THREE_WAY.market_hash) for u in value["market_units"]])
    if kind == "REAL_REVIEW_AUDIT_V1":
        add("rb_correction_reasons",[dict(context_id=r["review_context_id"],reason_json=json.dumps(r,sort_keys=True,separators=(",",":"),ensure_ascii=False)) for r in value["reasons"]])
        add("rb_reason_evidence",[dict(context_id=r["review_context_id"],snapshot_id=e) for r in value["reasons"] for e in r["evidence_snapshot_ids"]])
    if kind == "GENERIC_FUSION_RUN_V1":
        add("rb_fusion_units",[dict(unit_id=u["unit_id"]) for u in value["results"]])
    if kind == "REAL_PROSPECTIVE_RUN_V1":
        add("rb_run_inputs",evidence(value["evidence"]))
    if kind == "DECISION_LOCK_V2":
        add("rb_lock_frames",[dict(unit_id=ref_id(f["unit"]),sp_id=ref_id(f["sp_snapshot"]),match_id=f["identity"]["match_id"]) for f in value["frames"]])
        add("rb_lock_selected",[dict(candidate_id=c["ticket_candidate_id"],candidate_hash=c["candidate_hash"]) for c in value["selected"]])
    if kind == "REAL_PROSPECTIVE_SETTLEMENT_V1":
        add("rb_settlement_results",[dict(observation_id=ref_id(o)) for o in value["observations"]])
    if kind == "REAL_VALIDATION_REPORT_V1":
        add("rb_report_census",[dict(request_key=c["request_key"],run_id=ref_id(c["run"]),lock_id=ref_id(c["lock"]),
            settlement_id=ref_id(c["settlement"]),replacement_id=ref_id(c["replacement"]),status=c["status"]) for c in value["census"]])
    return found


def scalar_references(body):
    """Every ID-encoded *artifact* edge, in the unchanged numerical/wire contracts.

    A missing hash here means the owner only serializes an ID. The stored edge
    still has a concrete closed schema and a full composite ID/schema/hash FK.
    Local scenario IDs and logical outcome keys are not artifact references.
    """
    kind = body["schema_version"]
    result = []

    def add(path, target, schema, digest=None):
        result.append(dict(path=path,target_id=target,target_schema=schema,target_hash=digest,
            external=schema in {"EVIDENCE_SNAPSHOT_V1","PROSPECTIVE_EVIDENCE_BINDING_V1"}))

    def selections(rows, prefix):
        for i, row in enumerate(rows):
            add(f"{prefix}[{i}].ticket_candidate_id",row["ticket_candidate_id"],"SYSTEM_TICKET_CANDIDATE_V2",row.get("candidate_hash"))
            if row.get("hedge_witness"):
                add(f"{prefix}[{i}].hedge_witness.surviving_atomic_bet_id",row["hedge_witness"]["surviving_atomic_bet_id"],"EXPANDED_ATOMIC_BET_V2")

    if kind=="SYSTEM_TICKET_V2" and body.get("hedge_witness"):
        add("hedge_witness.surviving_atomic_bet_id",body["hedge_witness"]["surviving_atomic_bet_id"],"EXPANDED_ATOMIC_BET_V2")

    if kind == "OUTCOME_CANDIDATE_V1":
        add("unit_id",body["unit_id"],"REAL_ANALYSIS_UNIT_V1")
    if kind == "GENERIC_FUSION_RUN_V1":
        for i,u in enumerate(body["results"]):
            add(f"results[{i}].unit_id",u["unit_id"],"REAL_ANALYSIS_UNIT_V1")
    if kind == "ANALYSIS_PACKET_V4":
        for i,u in enumerate(body["market_units"]):
            add(f"market_units[{i}].review_context_id",u["review_context_id"],"MARKET_REVIEW_CONTEXT_V4",u["review_context_hash"])
            for j,e in enumerate(u["evidence_ids"]):
                add(f"market_units[{i}].evidence_ids[{j}]",e,"FOOTBALL_EVIDENCE_V1")
    if kind == "IMPORTED_LLM_REVIEW_V4":
        v = body["submission"]
        add("submission.packet_id",v["packet_id"],"ANALYSIS_PACKET_V4",v["packet_hash"])
        add("submission.analysis_id",v["analysis_id"],"REAL_MULTI_MARKET_ANALYSIS_V1")
        for i,r in enumerate(v["market_reviews"]):
            prefix=f"submission.market_reviews[{i}]"
            add(prefix+".review_context_id",r["review_context_id"],"MARKET_REVIEW_CONTEXT_V4",r["review_context_hash"])
            for j,e in enumerate(r.get("evidence_refs",())):
                add(prefix+f".evidence_refs[{j}]",e,"FOOTBALL_EVIDENCE_V1")
            for group in ("scenarios","counter_scenarios"):
                for j,s in enumerate(r.get(group,())):
                    for k,e in enumerate(s["evidence_refs"]):
                        add(prefix+f".{group}[{j}].evidence_refs[{k}]",e,"FOOTBALL_EVIDENCE_V1")
    if kind == "REAL_REVIEW_AUDIT_V1":
        for i,r in enumerate(body["reasons"]):
            add(f"reasons[{i}].review_context_id",r["review_context_id"],"MARKET_REVIEW_CONTEXT_V4")
            for j,e in enumerate(r["evidence_snapshot_ids"]):
                add(f"reasons[{i}].evidence_snapshot_ids[{j}]",e,"EVIDENCE_SNAPSHOT_V1")
    if kind == "PORTFOLIO_RETURN_DISTRIBUTION_V1":
        selections(body["allocations"],"allocations")
        for i,c in enumerate(body["components"]):
            for j,target in enumerate(c["ticket_candidate_ids"]):
                add(f"components[{i}].ticket_candidate_ids[{j}]",target,"SYSTEM_TICKET_CANDIDATE_V2")
    if kind == "RETURN_EVALUATION_V1":
        selections(body["requested"],"requested")
        selections(body["selected"],"selected")
    if kind == "RETURN_OPTIMIZATION_RUN_V1":
        selections(body["steps"],"steps")
    if kind == "DECISION_LOCK_V2":
        selections(body["selected"],"selected")
    if kind in {"REAL_STRATEGY_PLAN_V1","RETURN_EVALUATION_V1"} and body.get("risk"):
        for name in ("ticket_overlap","choice_set_overlap"):
            for key in sorted(body["risk"][name]):
                for position,target in enumerate(key.split("|")[:2]):
                    add(f"risk.{name}{{{key}}}[{position}]",target,"SYSTEM_TICKET_CANDIDATE_V2")
    if kind=="REAL_VALIDATION_REPORT_V1":
        for i,row in enumerate(body["metrics"]["correction_performance"]["observations"]):
            add(f"metrics.correction_performance.observations[{i}].run_id",row["run_id"],"REAL_PROSPECTIVE_RUN_V1")
    return result
