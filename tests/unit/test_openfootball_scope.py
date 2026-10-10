"""Self-authored independent contract graphs; no DB admission or real qualification."""
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
import pytest

from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.openfootball_scope import (
    CompetitionScopeContract, CompetitionScopeProfileV2, LegacyBundesligaScopeV1,
    QualifiedCompetitionScopeV2, ScopeAuthorityRefV2, ScopeIdentityV2, ScopeInstanceArtifactRefV2,
    ScopedModelBindingV2, logical_bootstrap_key, validate_scope_source_bytes, validate_scoped_model_state,
)
from football_system.domain.services.elo_baseline import EloThreeWayBaseline

AT = datetime(2027, 1, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2027, 1, 2, tzinfo=timezone.utc)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def evidence(name):
    return dict(evidence_id="SYNTHETIC:" + name, evidence_sha256=digest(name.encode()))


def fixture(label="A", count=2, zone="Europe/Berlin"):
    tzif = files("tzdata.zoneinfo").joinpath(*zone.split("/")).read_bytes()
    raw = {"README.md": b"Self-authored explicit ft contract", "LICENSE.md": b"Self-authored test permission"}
    manifest, records = [], []
    window = [dict(sequence=i, season_id=f"{label}:season:{i}", source_label=f"{2024+i}/{2025+i}", role=role)
        for i, role in enumerate(("WARMUP", "PILOT_TARGET", "PRODUCTION_TARGET"))]
    for season in range(2):
        name = f"{2024+season}/test.{label}.json"
        rows = []
        for index in range(count):
            row = dict(date=f"{2024+season}-01-{index+1:02d}", time="15:00", team1="Home " + label,
                team2="Away " + label, score={"ft": [index, 1]}, round=f"Round {index+1}")
            excluded = season == 1 and index == count-1
            if excluded:
                row["score"] = [index, 1]
            rows.append(row)
            result = None if excluded else dict(match_result_id=f"{label}:result:{season}:{index}", match_id=f"{label}:match:{season}:{index}",
                season_id=window[season]["season_id"], home_team_id=label + ":home", away_team_id=label + ":away",
                kickoff_at_utc=datetime.fromisoformat(row["date"] + "T15:00").replace(tzinfo=ZoneInfo(zone)).astimezone(timezone.utc),
                available_at_utc=AT, ingested_at_utc=AT, home_goals=index, away_goals=1, payload_hash=match_result_payload_sha256(index, 1))
            records.append(dict(file=name, index=index, raw_record_sha256=digest(canonical_json(row).encode()),
                disposition="EXCEPTION" if excluded else "FACT_CANDIDATE", candidate_result=result,
                reason="REPORTED_SCORE_PERIOD_UNPROVEN" if excluded else None, review=evidence(f"{label}-{season}-{index}")))
        raw[name] = json.dumps(dict(name="Self-authored " + label, matches=rows)).encode()
        manifest.append(dict(path=name, role="MATCHES", bytes=len(raw[name]), sha256=digest(raw[name]),
            record_count=count, season_id=window[season]["season_id"], captured_at_utc=AT))
    for name, role in (("README.md", "README"), ("LICENSE.md", "LICENSE")):
        manifest.append(dict(path=name, role=role, bytes=len(raw[name]), sha256=digest(raw[name]), record_count=0, captured_at_utc=AT))
    mapping = [dict(kind=kind, source_label=source, canonical_id=canonical, method="EXPLICIT_NEW_REVIEWED", evidence=evidence(label + source))
        for kind, source, canonical in [("COMPETITION", "Toy " + label, "competition:" + label),
            ("TEAM", "Home " + label, label + ":home"), ("TEAM", "Away " + label, label + ":away"),
            *(("SEASON", w["source_label"], w["season_id"]) for w in window)]]
    profile = CompetitionScopeProfileV2(competition_id="competition:" + label, source_competition_id="toy." + label,
        source_competition_label="Toy " + label, commit=digest(label.encode())[:40], files=tuple(sorted(manifest, key=lambda f: f["path"])),
        parser_policy_hash=digest(b"SYNTHETIC-EXPLICIT-FT-POLICY"),
        time_policy=dict(timezone=zone, tzif_sha256=digest(tzif), source_time_review=evidence("time:" + label)),
        mapping=tuple(sorted(mapping, key=lambda m: (m["kind"], m["source_label"]))), window=window, records=records, cohort_review=evidence("cohort:" + label))
    owner = ScopeIdentityV2.of(profile)
    def authority(kind):
        return ScopeAuthorityRefV2(owner=owner, kind=kind, artifact_id=label + kind, artifact_hash=digest((label + kind).encode()))
    scope = QualifiedCompetitionScopeV2(identity=owner, profile=profile, qualification=authority("QUALIFICATION"),
        rights=authority("SOURCE_RIGHTS"), authority=authority("TRUSTED_AUTHORITY"))
    return scope, raw, tzif


def model(scope):
    p = scope.profile
    state = EloThreeWayBaseline().rebuild_state([r.candidate_result for r in p.records if r.candidate_result is not None],
        CUTOFF, target_season_id=p.window[-1].season_id)
    targets = (p.competition_id + ":future",)
    instance = stable_id("OFP_BOOTSTRAP_INSTANCE_V2", logical_bootstrap_key(p, targets))
    def ref(kind):
        return ScopeInstanceArtifactRefV2(owner=scope.identity, bootstrap_instance_id=instance, kind=kind,
            artifact_id=p.competition_id + kind, artifact_hash=digest((p.competition_id + kind).encode()))
    binding = ScopedModelBindingV2(owner=scope.identity, bootstrap_instance_id=instance, source_id=p.source_id,
        facts_root=p.facts_root, window_hash=p.window_hash, program_id=p.competition_id + ":program", target_match_ids=targets,
        state_hash=state.state_hash, training_data_hash=state.training_data_hash,
        approval=ref("APPROVAL"), state_binding=ref("STATE_BINDING"), release=ref("RELEASE"), pin=ref("MODEL_PIN"))
    return binding, state


def test_two_independent_profiles_raw_census_states_and_owned_lineages():
    a, ar, at = fixture()
    b, br, bt = fixture("B", 3, "Europe/London")
    for scope, raw, tzif in ((a, ar, at), (b, br, bt)):
        validate_scope_source_bytes(scope.profile, raw, tzif)
        binding, state = model(scope)
        validate_scoped_model_state(scope, binding, state)
        assert TypeAdapter(CompetitionScopeContract).validate_json(scope.model_dump_json()) == scope
    ab, ast = model(a)
    bb, bst = model(b)
    assert a.identity != b.identity and a.profile.source_id != b.profile.source_id
    assert a.profile.mapping_root != b.profile.mapping_root and a.profile.window_hash != b.profile.window_hash
    assert a.profile.facts_root != b.profile.facts_root and a.profile.exceptions_root != b.profile.exceptions_root
    assert ast.config_hash == bst.config_hash and ast.state_hash != bst.state_hash and ast.training_data_hash != bst.training_data_hash
    assert not set(ast.training_match_ids) & set(bst.training_match_ids)
    with pytest.raises(ValueError, match="SCOPE_MODEL_BINDING_MISMATCH"):
        validate_scoped_model_state(b, ab, ast)
    with pytest.raises(ValueError, match="SCOPE_INDEPENDENT_STATE_REPLAY_MISMATCH"):
        validate_scoped_model_state(b, bb, ast)


@pytest.mark.parametrize("field", ["approval", "state_binding", "release", "pin"])
def test_cross_scope_artifact_refs_cannot_be_swapped_even_via_copy(field):
    a, _, _ = fixture()
    b, _, _ = fixture("B")
    ab, _ = model(a)
    bb, state = model(b)
    forged = bb.model_copy(update={field: getattr(ab, field)})
    with pytest.raises(ValueError, match="SCOPE_MODEL_GRAPH_OWNERSHIP_MISMATCH"):
        validate_scoped_model_state(b, forged, state)


@pytest.mark.parametrize("fault", ["missing_record", "duplicate_record", "bool_count", "target_fact", "wrong_team", "changed_config",
    "roi_exception", "arbitrary_status", "path_traversal", "duplicate_mapping", "window_reordered", "publication_forged"])
def test_profile_fails_closed_for_malformed_or_widened_cohort(fault):
    scope, _, _ = fixture()
    value = scope.profile.model_dump(mode="python")
    if fault == "missing_record":
        value["records"] = value["records"][:-1]
    elif fault == "duplicate_record":
        value["records"] = (*value["records"], value["records"][0])
    elif fault == "bool_count":
        value["files"][0]["record_count"] = True
    elif fault == "target_fact":
        value["files"][0]["season_id"] = value["window"][-1]["season_id"]
    elif fault == "wrong_team":
        value["records"][0]["candidate_result"]["home_team_id"] = "outside-scope"
    elif fault == "changed_config":
        value["config_hash"] = "0" * 64
    elif fault == "roi_exception":
        value["records"][-1]["reason"] = "POOR_ROI"
    elif fault == "arbitrary_status":
        value["records"][-1]["disposition"] = "UNKNOWN_BUT_ADMIT"
    elif fault == "path_traversal":
        value["files"][0]["path"] = "../source.json"
    elif fault == "duplicate_mapping":
        value["mapping"] = (*value["mapping"], value["mapping"][0])
    elif fault == "window_reordered":
        value["window"] = tuple(reversed(value["window"]))
    else:
        value["provider_publication_at_utc"] = AT
    with pytest.raises(ValueError):
        CompetitionScopeProfileV2.model_validate(value)


@pytest.mark.parametrize("fault", ["file", "missing", "tzif", "record", "goals", "raw_array_as_fact", "utc", "mapping"])
def test_raw_evidence_is_bound_not_just_self_asserted_counts(fault):
    scope, raw, tzif = fixture()
    profile = scope.profile
    value = profile.model_dump(mode="python")
    if fault == "file":
        raw[next(iter(raw))] += b"changed"
    elif fault == "missing":
        raw.pop(next(iter(raw)))
    elif fault == "tzif":
        tzif += b"changed"
    elif fault == "record":
        value["records"][0]["raw_record_sha256"] = "0" * 64
    elif fault == "goals":
        value["records"][0]["candidate_result"]["home_goals"] = 99
    elif fault == "raw_array_as_fact":
        value["records"][-1].update(disposition="FACT_CANDIDATE", reason=None,
            candidate_result=dict(value["records"][-2]["candidate_result"], match_id="new-id", match_result_id="new-result"))
    elif fault == "utc":
        value["records"][0]["candidate_result"]["kickoff_at_utc"] = datetime(2024, 1, 1, 13, tzinfo=timezone.utc)
    else:
        value["records"][0]["candidate_result"]["home_team_id"], value["records"][0]["candidate_result"]["away_team_id"] = "A:away", "A:home"
    profile = CompetitionScopeProfileV2.model_validate(value)
    with pytest.raises(ValueError):
        validate_scope_source_bytes(profile, raw, tzif)


def test_ref_and_request_changes_cannot_reset_same_logical_attempt():
    scope, _, _ = fixture()
    value = scope.profile.model_dump(mode="python")
    value["cohort_review"] = evidence("different-review-reference")
    second = CompetitionScopeProfileV2.model_validate(value)
    assert second.content_hash != scope.profile.content_hash
    assert logical_bootstrap_key(second, ("future",)) == logical_bootstrap_key(scope.profile, ("future",))
    binding, state = model(scope)
    with pytest.raises(ValueError):
        validate_scoped_model_state(scope, binding.model_copy(update={"bootstrap_instance_id": "retry-under-new-key"}), state)


def test_v1_discriminator_remains_exact_and_v2_does_not_enter_existing_pin_union():
    from football_system.domain.pinned_model_source import ModelSourceV1
    legacy = LegacyBundesligaScopeV1()
    assert TypeAdapter(CompetitionScopeContract).validate_json(legacy.model_dump_json()) == legacy
    assert legacy.historical_counts == (612, 13, 599)
    with pytest.raises(ValidationError):
        LegacyBundesligaScopeV1(historical_counts=(760, 27, 733))
    scope, _, _ = fixture()
    with pytest.raises(ValidationError):
        TypeAdapter(ModelSourceV1).validate_python(scope.model_dump())
    with pytest.raises(ValidationError):
        TypeAdapter(CompetitionScopeContract).validate_python({"schema_version": "UNREVIEWED_V3"})


@pytest.mark.parametrize("date", ["2024-03-31", "2024-10-27"])
def test_pinned_timezone_rejects_dst_gap_and_fold(date):
    scope, raw, tzif = fixture()
    value = scope.profile.model_dump(mode="python")
    name = value["files"][0]["path"]
    document = json.loads(raw[name])
    row = document["matches"][0]
    row.update(date=date, time="02:30")
    raw[name] = json.dumps(document).encode()
    value["files"][0].update(bytes=len(raw[name]), sha256=digest(raw[name]))
    value["records"][0]["raw_record_sha256"] = digest(canonical_json(row).encode())
    profile = CompetitionScopeProfileV2.model_validate(value)
    with pytest.raises(ValueError, match="SCOPE_DST_AMBIGUOUS_OR_NONEXISTENT"):
        validate_scope_source_bytes(profile, raw, tzif)


def test_duplicate_json_keys_are_rejected_even_when_file_hash_matches():
    scope, raw, tzif = fixture()
    value = scope.profile.model_dump(mode="python")
    name = value["files"][0]["path"]
    raw[name] = raw[name].replace(b'{"name":', b'{"name":"duplicate","name":', 1)
    value["files"][0].update(bytes=len(raw[name]), sha256=digest(raw[name]))
    with pytest.raises(ValueError, match="SCOPE_DUPLICATE_JSON_KEY"):
        validate_scope_source_bytes(CompetitionScopeProfileV2.model_validate(value), raw, tzif)


@pytest.mark.parametrize("field", ["qualification", "rights", "authority"])
def test_cross_scope_authority_refs_rejected(field):
    a, _, _ = fixture()
    b, _, _ = fixture("B")
    with pytest.raises(ValueError, match="SCOPE_AUTHORITY_OWNERSHIP_MISMATCH"):
        QualifiedCompetitionScopeV2.model_validate(b.model_copy(update={field: getattr(a, field)}))
