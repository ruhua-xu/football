"""Fixed OpenFootball source projection and structural quant-integrity adapter.

Uses the existing Elo/window/replay contracts. No odds, optimizer, or historical
predictive metrics enter this observed lane. No reviewer decision is generated.
"""

from datetime import datetime

from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_production import (
    BOOTSTRAP_ID, CONFIG_HASH, EXCEPTIONS, TEAM_LABELS, USES,
    OpenFootballCanonicalReviewV1, OpenFootballObservedFactV1,
    OpenFootballSourceRightsPayloadV1,
    canonical_id, fixed_exceptions, fixed_window, match_id, require, validate_fact_cohort,
)
from football_system.domain.openfootball_snapshot import OpenFootballAdapterPolicyV1, OpenFootballHistoricalValidationV1
from football_system.domain.services.elo_baseline import EloBaselineConfig, EloThreeWayBaseline
from football_system.domain.training_admission import tagged_canonical_sha256
from football_system.infrastructure.files.openfootball_evidence import qualify_private_root
from football_system.infrastructure.files.training_evidence import json_pointer, strict_json_bytes


def prepare_openfootball_binding(*, evidence, policy, mapping, rights_payload, directive_evidence):
    """Read-only exact review subject; no approval, recording time or fit."""
    policy = OpenFootballAdapterPolicyV1.model_validate(policy)
    mapping = OpenFootballCanonicalReviewV1.model_validate(mapping)
    rights_payload = OpenFootballSourceRightsPayloadV1.model_validate(rights_payload).model_dump(mode="json")
    report = qualify_private_root(evidence.root, policy=policy)
    require({t for d in report["datasets"] for t in d["quality"]["teams"]} == set(TEAM_LABELS), "EXACT_20_TEAMS_REQUIRED")
    require(rights_payload["allowed_uses"] == list(USES), "SOURCE_RIGHTS_USES_MISMATCH")
    require(rights_payload["raw_retention"] == "PERMANENT_ALL_612_SOURCE_RECORDS", "RAW_RETENTION_MUST_BE_PERMANENT")
    require(rights_payload["license_sha256"] == report["rights_candidate"]["license_sha256"], "CC0_TERMS_HASH_MISMATCH")
    evidence.read(directive_evidence.evidence_reference, directive_evidence.evidence_sha256)
    records = []
    for dataset in report["datasets"]:
        filename = dataset["source_filename"]
        raw = strict_json_bytes(evidence.read(filename, dataset["source_file_sha256"]))
        require(dataset["quality"]["raw_match_count"] == dataset["quality"]["unique_match_count"] == 306, "EXACT_306_SOURCE_COHORT_REQUIRED")
        require({r["provenance"]["original_record_pointer"] for r in dataset["records"]} == {f"/matches/{i}" for i in range(306)}, "SOURCE_POINTER_COHORT_INCOMPLETE")
        for row in dataset["records"]:
            pointer = row["provenance"]["original_record_pointer"]
            reason = EXCEPTIONS[filename].get(pointer)
            diagnostics = {d["code"] for d in row["diagnostics"]}
            if reason == "ABNORMAL_MATCH_STATUS_AWARDED":
                require(row["source_status"] == "awarded" and diagnostics == {"ABNORMAL_MATCH_STATUS"}, "AWARDED_EXCEPTION_CHANGED")
            elif reason:
                require(row["score_class"] == "B_DIRECT_ARRAY" and diagnostics == {"SOURCE_SCHEMA_EXCEPTION"}, "REPORTED_SCORE_EXCEPTION_CHANGED")
            else:
                require(not diagnostics and row["regular_time_score_candidate"] is not None, "UNDECLARED_SOURCE_EXCEPTION")
            for key in ("source_team1", "source_team2"):
                canonical_id("TEAM", row[key])
            require(row["provenance"]["provider_publication_at_utc"] is None and row["provenance"]["provider_finalized_at_utc"] is None, "PROVIDER_TIME_FABRICATION")
            records.append(dict(source_filename=filename, record_pointer=pointer, included=reason is None,
                exception_reason=reason, original_record=json_pointer(raw, pointer), inspection=row,
                canonical_match_id=match_id(filename, pointer)))
    records.sort(key=lambda r: (r["source_filename"], int(r["record_pointer"].rsplit("/", 1)[1])))
    require(len(records) == 612 and sum(r["included"] for r in records) == 599, "EXACT_612_13_599_REQUIRED")
    payload = dict(schema_version="OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", bootstrap_id=BOOTSTRAP_ID,
        preparation_scope=report["scope"], preparation_scope_hash=report["scope_hash"],
        mapping=mapping.model_dump(mode="json"), mapping_root=mapping.content_hash,
        mapping_entry_hashes={e.canonical_id: e.content_hash for e in mapping.entries},
        training_window=fixed_window().model_dump(mode="json"), training_window_hash=fixed_window().content_hash,
        expected_counts={"2024/25":306,"2025/26":306}, included_counts={"2024/25":305,"2025/26":294},
        exceptions=list(fixed_exceptions()), source_records=records, rights_payload=rights_payload,
        user_directive=directive_evidence.model_dump(mode="json"), provider_publication_at_utc=None,
        provider_finalized_at_utc=None, source_data_mode="SOURCE_TIME_RESEARCH", retrospective=True,
        evidence_basis="CURRENT_SNAPSHOT_OBSERVED", config_hash=CONFIG_HASH)
    return payload


def facts_at_admission(prepared, admitted_at):
    values = []
    for record in prepared["source_records"]:
        if not record["included"]:
            continue
        row, p = record["inspection"], record["inspection"]["provenance"]
        goals = row["regular_time_score_candidate"]
        values.append(OpenFootballObservedFactV1(source_filename=record["source_filename"],
            source_file_sha256=p["source_file_sha256"], record_pointer=record["record_pointer"],
            record_sha256=p["original_record_sha256"], match_id=record["canonical_match_id"],
            season_id=canonical_id("SEASON", row["season"]), home_team_id=canonical_id("TEAM", row["source_team1"]),
            away_team_id=canonical_id("TEAM", row["source_team2"]), source_team1=row["source_team1"], source_team2=row["source_team2"],
            kickoff_at_utc=row["kickoff_at_utc"], home_goals=goals[0], away_goals=goals[1],
            capture_at_utc=p["capture_at_utc"], admitted_at_utc=admitted_at, adapter_policy_hash=p["adapter_policy_hash"]))
    return validate_fact_cohort(values, window=fixed_window())


def facts_root(facts):
    return tagged_canonical_sha256("OPENFOOTBALL_ADMITTED_FACTS_ROOT_V1", [f.model_dump(mode="json") for f in facts])


def structural_replay(facts, *, cutoff, excluded_match_ids):
    """One candidate computation plus exact reversed-input mechanical replay."""
    require(EloBaselineConfig().config_hash == CONFIG_HASH, "FROZEN_ELO_CONFIG_CHANGED")
    values = validate_fact_cohort(facts, window=fixed_window(), exclusions=excluded_match_ids, cutoff=cutoff)
    results = tuple(f.to_elo_result() for f in values)
    baseline = EloThreeWayBaseline()
    state = baseline.rebuild_state(results, cutoff, target_season_id=canonical_id("SEASON", "2026/27"), exclude_match_ids=excluded_match_ids)
    replay = baseline.rebuild_state(tuple(reversed(results)), cutoff, target_season_id=canonical_id("SEASON", "2026/27"), exclude_match_ids=excluded_match_ids)
    require(canonical_json(state) == canonical_json(replay), "NONDETERMINISTIC_ELO_STATE")
    require(len(state.training_facts) == 599 and not set(state.training_match_ids) & set(excluded_match_ids), "TRAINING_FACT_OR_EXCLUSION_MISMATCH")
    return dict(state=state.model_dump(mode="json"), training_data_hash=state.training_data_hash, state_hash=state.state_hash,
        structural_replay="PASS", determinism="PASS", fact_count=599, source_record_count=612, exception_count=13,
        zero_fact_production_season=True, historical_validation=OpenFootballHistoricalValidationV1().model_dump(mode="json"),
        availability_accounting=dict(historical_model_availability=None, historical_denominator=None,
            registered_training_teams=len(state.teams), prior_match_counts={t.team_id:t.prior_matches for t in state.teams},
            operation_targets_evaluated=0, reason="NO_HISTORICAL_AVAILABILITY_CLAIM"),
        mechanical_state_computations=2)


def parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
