"""OF01-OF20: invented preparation fixtures; never production authority/data."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from football_system.domain.openfootball_snapshot import (
    COMMIT, PINNED_FILES, TZIF_SHA256,
    OpenFootballAcquisitionManifestV1, OpenFootballAdapterPolicyV1,
    OpenFootballCanonicalMappingPlanV1, OpenFootballCurrentSnapshotScopeV1,
    OpenFootballFileCaptureV1, OpenFootballHistoricalValidationV1,
    OpenFootballMappingEntryV1, OpenFootballRecordProvenanceV1,
    OpenFootballSourceRightsCandidateV1,
)
from football_system.infrastructure.files.openfootball_evidence import (
    private_evidence_directory, qualify_openfootball_files, verify_capture_bytes, write_private_report,
)
from football_system.infrastructure.providers.real.openfootball_observed import (
    inspect_openfootball_file, local_kickoff_to_utc,
)

AT = datetime(2026, 9, 22, 9, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def policy():
    return OpenFootballAdapterPolicyV1(timezone="Europe/Berlin", tzdata_version="2025.2",
        iana_version="2025b", tzif_sha256=TZIF_SHA256)


def capture(name="2024-25/de.1.json"):
    size, digest = PINNED_FILES[name]
    return OpenFootballFileCaptureV1(url=f"https://raw.githubusercontent.com/openfootball/football.json/{COMMIT}/{name}",
        commit=COMMIT, path=name, bytes=size, sha256=digest, request_started_at_utc=AT, capture_at_utc=AT)


def manifest():
    return OpenFootballAcquisitionManifestV1(repository="openfootball/football.json", commit=COMMIT,
        authorization_basis="USER_STAGE3_LIMITED_ACQUISITION_AND_BOOTSTRAP_PREPARATION",
        files=tuple(capture(n) for n in PINNED_FILES), sends=4, errors=(), automatic_retries=0,
        production_training_authorized=False, status="COMPLETE")


def document():
    return {"name": "Deutsche Bundesliga 2024/25", "matches": [
        {"round": "Matchday 1", "date": "2024-08-23", "time": "20:30",
         "team1": "SYNTHETIC_HOME", "team2": "SYNTHETIC_AWAY", "score": {"ft": [2, 1], "ht": [1, 0]}}
    ]}


def scan(doc=None):
    return inspect_openfootball_file(json.dumps(doc or document()).encode(), filename="2024-25/de.1.json",
        capture_at_utc=AT, policy=policy())


def codes(result):
    return {d["code"] for r in result["records"] for d in r["diagnostics"]}


def test_of01_cc0_source_identity_binding():
    rights = OpenFootballSourceRightsCandidateV1()
    assert rights.license == "CC0-1.0" and rights.provider_code == "OPENFOOTBALL"
    assert rights.license_sha256 == PINNED_FILES["LICENSE.md"][1]
    assert rights.readme_sha256 != rights.license_sha256
    assert rights.status == "CANDIDATE_NOT_APPROVED" and rights.review_artifact is None
    for update in ({"license": "FREE"}, {"license_sha256": PINNED_FILES["2024-25/de.1.json"][1]},
                   {"status": "APPROVED"}, {"production_training_authorized": True}, {"trusted_authority_pins": {"self": "a" * 64}}):
        with pytest.raises(ValidationError):
            OpenFootballSourceRightsCandidateV1.model_validate(rights.model_dump() | update)


@pytest.mark.parametrize("provider", ["SPORTMONKS", "THE_ODDS_API", "openfootball", "OPENFOOTBALL "])
def test_of02_wrong_provider_rejection(provider):
    with pytest.raises(ValidationError):
        OpenFootballAcquisitionManifestV1.model_validate(manifest().model_dump() | {"provider_code": provider})
    with pytest.raises(ValidationError):
        OpenFootballAdapterPolicyV1.model_validate(policy().model_dump() | {"provider_code": provider})


@pytest.mark.parametrize("update", [
    {"commit": "a" * 40}, {"path": "2026-27/de.1.json"}, {"path": "../2024-25/de.1.json"},
    {"sha256": "a" * 64}, {"bytes": 88233}, {"url": "https://example.invalid/de.1.json"},
    {"sha256": PINNED_FILES["LICENSE.md"][1]},
])
def test_of03_exact_commit_file_hash_binding(update):
    with pytest.raises(ValidationError):
        OpenFootballFileCaptureV1.model_validate(capture().model_dump() | update)


def test_of03_file_set_and_copied_model_revalidated():
    m = manifest()
    with pytest.raises(ValidationError, match="EXACT_FOUR"):
        OpenFootballAcquisitionManifestV1.model_validate(m.model_dump() | {"files": (capture(),) * 4})
    corrupt = m.model_copy(update={"provider_code": "SPORTMONKS"})
    with pytest.raises(ValidationError):
        qualify_openfootball_files(acquisition=corrupt, source_bytes={}, policy=policy())


def test_of04_source_bytes_mutation_rejection():
    item = capture()
    with pytest.raises(ValueError, match="CAPTURE_BYTES_MISMATCH"):
        verify_capture_bytes(b"x" * item.bytes, item)
    # Rewriting the caller hash to bless mutated bytes is also refused.
    altered = item.model_copy(update={"sha256": hashlib.sha256(b"x" * item.bytes).hexdigest()})
    with pytest.raises(ValidationError, match="EXACT_FILE_HASH"):
        verify_capture_bytes(b"x" * item.bytes, altered)


@pytest.mark.parametrize("raw", [
    b'{"name":"one","name":"two","matches":[]}',
    b'{"name":"Deutsche Bundesliga 2024/25","matches":[{"score":{"ft":[0,0],"ft":[1,1]}}]}',
    b'{"name":"Deutsche Bundesliga 2024/25","matches":[NaN]}',
])
def test_of05_duplicate_json_key_rejection(raw):
    with pytest.raises(ValueError, match="invalid local JSON"):
        inspect_openfootball_file(raw, filename="2024-25/de.1.json", capture_at_utc=AT, policy=policy())


@pytest.mark.parametrize("value", [None, "", "2024-02-30", "2024-8-23", 20240823])
def test_of06_missing_date_rejection(value):
    doc = document()
    doc["matches"][0]["date"] = value
    result = scan(doc)
    assert "MALFORMED_RECORD" in codes(result)
    assert result["quality"]["date_complete_count"] == 0
    assert result["records"][0]["kickoff_at_utc"] is None
    assert result["records"][0]["regular_time_score_candidate"] is None


@pytest.mark.parametrize("value", [None, "", "24:00", "02:99", "20:30Z", "20:30+02:00", "20:30:00"])
def test_of07_missing_time_handling(value):
    doc = document()
    doc["matches"][0]["time"] = value
    result = scan(doc)
    assert "MISSING_OR_INVALID_TIME" in codes(result)
    assert result["quality"]["time_complete_count"] == 0
    assert result["records"][0]["kickoff_at_utc"] is None


@pytest.mark.parametrize("day,wall,expected", [
    ("2025-01-15", "15:30", "2025-01-15T14:30:00+00:00"),
    ("2025-08-15", "15:30", "2025-08-15T13:30:00+00:00"),
    ("2025-03-30", "01:30", "2025-03-30T00:30:00+00:00"),
    ("2025-03-30", "03:30", "2025-03-30T01:30:00+00:00"),
    ("2025-10-26", "01:30", "2025-10-25T23:30:00+00:00"),
    ("2025-10-26", "03:30", "2025-10-26T02:30:00+00:00"),
])
def test_of08_europe_berlin_dst_conversion(day, wall, expected):
    assert local_kickoff_to_utc(day, wall, policy=policy()).isoformat() == expected


@pytest.mark.parametrize("day,code", [("2025-03-30", "NONEXISTENT_LOCAL_TIME"), ("2025-10-26", "AMBIGUOUS_LOCAL_TIME")])
def test_of08_dst_gaps_and_folds_rejected(day, code):
    with pytest.raises(ValueError, match=code):
        local_kickoff_to_utc(day, "02:30", policy=policy())


def test_of09_exact_utc_policy_is_explicit_and_host_independent(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    assert local_kickoff_to_utc("2024-08-23", "20:30", policy=policy()).isoformat() == "2024-08-23T18:30:00+00:00"
    data = policy().model_dump()
    for key in ("timezone", "tzdata_version", "iana_version", "tzif_sha256"):
        invalid = dict(data)
        invalid.pop(key)
        with pytest.raises(ValidationError):
            OpenFootballAdapterPolicyV1.model_validate(invalid)
    with pytest.raises(ValidationError):
        OpenFootballAdapterPolicyV1.model_validate(data | {"timezone": "UTC"})
    import tzdata
    monkeypatch.setattr(tzdata, "__version__", "unreviewed")
    with pytest.raises(ValueError, match="TZDATA_VERSION_MISMATCH"):
        local_kickoff_to_utc("2024-08-23", "20:30", policy=policy())


def test_of09_tzif_mutation_and_unvalidated_policy_rejected(monkeypatch, tmp_path):
    from football_system.infrastructure.providers.real import openfootball_observed
    tzif = tmp_path / "zoneinfo" / "Europe"
    tzif.mkdir(parents=True)
    (tzif / "Berlin").write_bytes(b"mutated IANA bytes")
    monkeypatch.setattr(openfootball_observed, "files", lambda package: tmp_path)
    with pytest.raises(ValueError, match="TZIF_HASH_MISMATCH"):
        local_kickoff_to_utc("2024-08-23", "20:30", policy=policy())
    with pytest.raises(ValidationError):
        local_kickoff_to_utc("2024-08-23", "20:30", policy=policy().model_copy(update={"timezone": "UTC"}))


def test_of10_score_ft_semantics_and_original_provenance():
    result = scan()
    row = result["records"][0]
    assert row["score_class"] == "A_SCORE_FT" and row["regular_time_score_candidate"] == (2, 1)
    assert row["provenance"]["local_date_text"] == "2024-08-23"
    assert row["provenance"]["local_time_text"] == "20:30"
    assert row["provenance"]["original_record_pointer"] == "/matches/0"
    assert row["provenance"]["source_file_sha256"] == hashlib.sha256(json.dumps(document()).encode()).hexdigest()
    assert result["source_binding"] == "UNVERIFIED_SCAN" and not result["production_training_authorized"]


@pytest.mark.parametrize("score", [[2, 1], [], ["2", "1"], {"ft": [True, 1]}, {"ft": [-1, 1]}, {"ft": [2.0, 1]}, {"ft": [2, 1, 0]}, {"ft": [2, 1], "ht": [3, 1]}])
def test_of11_ambiguous_or_malformed_score_rejection(score):
    doc = document()
    doc["matches"][0]["score"] = score
    result = scan(doc)
    assert result["records"][0]["regular_time_score_candidate"] is None
    assert result["records"][0]["diagnostics"]
    if isinstance(score, list):
        assert "SOURCE_SCHEMA_EXCEPTION" in codes(result)


def test_of11_missing_score_separate_from_zero_zero():
    doc = document()
    del doc["matches"][0]["score"]
    assert scan(doc)["quality"]["score_classes"]["C_MISSING_SCORE"] == 1
    doc["matches"][0]["score"] = {"ft": [0, 0]}
    assert scan(doc)["records"][0]["regular_time_score_candidate"] == (0, 0)


@pytest.mark.parametrize("change", ["et", "p", "agg", "awarded", "abandoned", "cup", "wrong_league", "time_local"])
def test_of12_extra_time_cup_and_abnormal_rejection(change):
    doc = document()
    row = doc["matches"][0]
    if change in {"et", "p", "agg"}:
        row["score"][change] = [2, 1]
    elif change in {"awarded", "abandoned"}:
        row["status"] = change
    elif change == "cup":
        row["round"] = "Final"
    elif change == "time_local":
        row["time_local"] = "21:30"
    else:
        doc["name"] = "DFB Pokal 2024/25"
        with pytest.raises(ValueError, match="BUNDESLIGA_REGULAR_LEAGUE"):
            scan(doc)
        return
    assert scan(doc)["records"][0]["regular_time_score_candidate"] is None


@pytest.mark.parametrize("changed_date", [False, True])
def test_of13_duplicate_match_rejection_without_dropping_rows(changed_date):
    doc = document()
    duplicate = copy.deepcopy(doc["matches"][0])
    if changed_date:
        duplicate["date"] = "2024-08-24"
        duplicate["score"] = {"ft": [1, 1]}
    doc["matches"].append(duplicate)
    result = scan(doc)
    assert result["quality"]["raw_match_count"] == 2
    assert result["quality"]["unique_match_count"] == 1
    assert result["quality"]["duplicate_count"] == 1
    assert all(r["regular_time_score_candidate"] is None for r in result["records"])


def test_of14_unresolved_canonical_mapping_rejection():
    entry = OpenFootballMappingEntryV1(kind="TEAM", source_label="SYNTHETIC_HOME")
    mapping = OpenFootballCanonicalMappingPlanV1(scope_hash="a" * 64, entries=(entry,))
    for alias in ("SYNTHETIC_HOME", "Synthetic Home", "SYNTHETIC_AWAY"):
        with pytest.raises(ValueError, match="IDENTITY_UNRESOLVED"):
            mapping.require_explicit_identity("TEAM", alias)
    with pytest.raises(ValidationError, match="CANONICAL_IDENTITY_EVIDENCE"):
        OpenFootballMappingEntryV1(kind="TEAM", source_label="SYNTHETIC_HOME", canonical_id="team-1")
    with pytest.raises(ValidationError, match="TEAM_NAME_IS_NOT"):
        OpenFootballMappingEntryV1(kind="TEAM", source_label="SYNTHETIC_HOME", canonical_id="SYNTHETIC_HOME",
            identity_evidence_reference="synthetic-catalog.json", identity_evidence_sha256="a" * 64)


def test_of15_retrospective_provenance_preserved():
    scope = OpenFootballCurrentSnapshotScopeV1(acquisition=manifest(), adapter_policy=policy(), candidate_seasons=("2024/25", "2025/26"))
    assert scope.source_data_mode == "SOURCE_TIME_RESEARCH" and scope.retrospective is True
    assert scope.evidence_basis == "CURRENT_SNAPSHOT_OBSERVED" and scope.training_window is None
    for change in ({"source_data_mode": "LIVE_STRICT"}, {"retrospective": False}, {"retrospective": 1}, {"provider_code": "SPORTMONKS"}, {"training_window": ["2024/25", "2025/26"]}):
        with pytest.raises(ValidationError):
            OpenFootballCurrentSnapshotScopeV1.model_validate(scope.model_dump() | change)


def test_of16_publication_and_finalization_remain_unknown():
    data = scan()["records"][0]["provenance"]
    assert data["provider_publication_at_utc"] is None and data["provider_finalized_at_utc"] is None
    assert data["provider_time_status"] == "UNKNOWN"
    for key in ("provider_publication_at_utc", "provider_finalized_at_utc"):
        with pytest.raises(ValidationError):
            OpenFootballRecordProvenanceV1.model_validate(data | {key: AT})


def test_of17_no_strict_historical_metric_claims():
    data = OpenFootballHistoricalValidationV1().model_dump()
    for key in ("walk_forward_historical_availability", "historical_brier", "historical_logloss", "historical_calibration"):
        assert data[key] == "UNAVAILABLE"
        with pytest.raises(ValidationError):
            OpenFootballHistoricalValidationV1.model_validate(data | {key: "PASS"})
    with pytest.raises(ValidationError):
        OpenFootballHistoricalValidationV1(metrics={"brier": 0})
    assert data["metrics"] is None and data["reason"] == "UNPROVEN_HISTORICAL_VERSION_TIME"


def test_of18_input_order_determinism_and_byte_lineage_distinction():
    original = manifest()
    reversed_manifest = OpenFootballAcquisitionManifestV1.model_validate(original.model_dump() | {"files": tuple(reversed(original.files))})
    assert original.content_hash == reversed_manifest.content_hash
    doc = document()
    other = copy.deepcopy(doc["matches"][0])
    other.update(team1="SYNTHETIC_OTHER", date="2024-08-24")
    doc["matches"].append(other)
    first = scan(doc)
    doc["matches"].reverse()
    second = scan(doc)
    assert first["quality"] == second["quality"]
    assert first["semantic_records_hash"] == second["semantic_records_hash"]
    assert first["source_file_sha256"] != second["source_file_sha256"]  # Original-byte evidence MUST differ.
    entries = tuple(OpenFootballMappingEntryV1(kind="TEAM", source_label=n) for n in ("SYNTHETIC_HOME", "SYNTHETIC_AWAY"))
    a = OpenFootballCanonicalMappingPlanV1(scope_hash="a" * 64, entries=entries)
    b = OpenFootballCanonicalMappingPlanV1(scope_hash="a" * 64, entries=entries[::-1])
    assert a.content_hash == b.content_hash


def test_of19_pythonhashseed_determinism():
    script = """import json, runpy
n=runpy.run_path('tests/contract/test_openfootball_observed.py')
r=n['scan']()
print(json.dumps({'quality':r['quality'],'semantic':r['semantic_records_hash'],'policy':n['policy']().content_hash,'manifest':n['manifest']().content_hash},sort_keys=True))
"""
    outputs = [subprocess.check_output([sys.executable, "-B", "-c", script], cwd=ROOT,
        env=os.environ | {"PYTHONHASHSEED": seed}, timeout=30) for seed in ("0", "1", "42", "12345")]
    assert len(set(outputs)) == 1


def test_of20_old_sportmonks_v1_and_frozen_projection():
    from football_system.domain.observed_training import CurrentSnapshotCollectionScopeV1
    from football_system.infrastructure.files.real_bridge_frozen import verify_frozen_checkout
    properties = CurrentSnapshotCollectionScopeV1.model_json_schema()["properties"]
    assert properties["provider_code"]["const"] == "SPORTMONKS"
    assert len(verify_frozen_checkout(ROOT)) == 211
    for name in ("domain/observed_training.py", "infrastructure/providers/real/sportmonks_observed.py",
                 "infrastructure/files/training_evidence.py", "domain/services/elo_baseline.py"):
        relative = "src/football_system/" + name
        before = subprocess.check_output(["git", "show", "v1.1.0:" + relative], cwd=ROOT)
        assert (ROOT / relative).read_bytes().replace(b"\r\n", b"\n") == before.replace(b"\r\n", b"\n")


def test_private_output_is_no_replace_and_public_git_rejected(tmp_path):
    with pytest.raises(ValueError, match="OUTSIDE_PUBLIC_GIT"):
        private_evidence_directory(ROOT)
    output = tmp_path / "candidate.json"
    digest = write_private_report(output, {"candidate": True, "production_training_authorized": False})
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        write_private_report(output, {"candidate": False})
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest


def test_cli_has_no_training_release_or_database_command():
    from football_system.interfaces.openfootball_cli import main
    with pytest.raises(SystemExit) as error:
        main(["--train"])
    assert error.value.code == 2


def test_missing_score_future_capture_and_unknown_fields_do_not_qualify():
    doc = document()
    row = doc["matches"][0]
    row.update(date="2027-01-01", time="12:00", time_zone="UTC")
    result = scan(doc)
    assert {"CAPTURE_NOT_AFTER_KICKOFF", "SOURCE_SCHEMA_EXCEPTION"} <= codes(result)
    assert result["records"][0]["regular_time_score_candidate"] is None
