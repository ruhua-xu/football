"""Small offline diagnostic, NOT an adapter, rights grant, or training admission.

Use an operator-controlled PRIVATE root under workspace data/research. Existing
sample retention ends 2026-09-15 UTC; inspection does not extend it. No acquisition,
network, environment/key reads, rights/DB lookup, or permission generation occurs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from football_system.domain.raw_data import RawArtifactMetadata  # noqa: E402
from football_system.infrastructure.files.training_evidence import (  # noqa: E402
    LocalTrainingEvidence,
    strict_json_bytes,
)

SCORE_TYPES = {1: "1ST_HALF", 2: "2ND_HALF", 1525: "CURRENT", 48996: "2ND_HALF_ONLY"}
PERIOD_TYPES = {1: "FIRST_HALF", 2: "SECOND_HALF", 3: "EXTRA_TIME", 5: "PENALTIES"}
LOCAL_RECEIPT = "LOCAL_RESPONSE_RECEIPT_ONLY"


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _object(value):
    _require(isinstance(value, dict), "EXPECTED_OBJECT")
    return value


def _integer(value, minimum=1):
    _require(type(value) is int and value >= minimum, "EXPECTED_STRICT_INTEGER")
    return value


def _field(obj, key, pointer):
    fact = {"pointer": f"{pointer}/{key}", "presence": "MISSING"}
    if key in obj:
        fact.update(presence="NULL" if obj[key] is None else "VALUE", value=obj[key])
    return fact


def _time_field(obj, key, pointer):
    fact = _field(obj, key, pointer)
    if fact["presence"] == "VALUE":
        seconds = _integer(fact["value"], 0)
        _require(seconds <= 253402300799, "UNIX_SECONDS_NOT_MILLISECONDS")
        fact["utc"] = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return fact


def inspect_fixture_response(
    payload: bytes, *, received_at_utc: datetime,
    expected_league_id: int, expected_season_id: int,
) -> dict:
    """Inspect exact source bytes; absent/null values stay distinct from zero."""
    _require(type(payload) is bytes, "EXPECTED_BYTES")
    _require(isinstance(received_at_utc, datetime)
             and received_at_utc.utcoffset() == timedelta(0), "EXPECTED_UTC_RECEIPT")
    root = _object(strict_json_bytes(payload))
    data = _object(root.get("data"))
    _require(root.get("timezone") == "UTC", "EXPLICIT_ROOT_UTC_REQUIRED")
    fixture_id = _integer(data.get("id"))
    league_id, season_id = _integer(data.get("league_id")), _integer(data.get("season_id"))
    _require((league_id, season_id) == (_integer(expected_league_id), _integer(expected_season_id)),
             "SCOPE_MISMATCH")
    league, season = _object(data.get("league")), _object(data.get("season"))
    _require(_integer(league.get("id")) == league_id and _integer(season.get("id")) == season_id
             and _integer(season.get("league_id")) == league_id, "NESTED_SCOPE_MISMATCH")
    _require(_object(league.get("country")).get("iso2") == "DE" and league.get("type") == "league"
             and league.get("sub_type") == "domestic" and season.get("finished") is True, "SCOPE_SHAPE_MISMATCH")
    state_id = _field(data, "state_id", "/data")
    state_object = _field(data, "state", "/data")
    state = {}
    if state_id["presence"] == "VALUE":
        _integer(state_id["value"])
    if state_object["presence"] == "VALUE":
        state = _object(data["state"])
        nested_id = _integer(state.get("id"))
        _require(state_id["presence"] != "VALUE" or nested_id == state_id["value"], "STATE_ID_MISMATCH")
        tokens = [state[key] for key in ("state", "short_name", "developer_name") if key in state]
        _require(all(isinstance(token, str) and token.strip() for token in tokens)
                 and len(set(tokens)) <= 1, "STATE_TOKEN_MISMATCH")
    reported_ft = state_id.get("value") == 5 and all(
        state.get(key) == "FT" for key in ("state", "short_name", "developer_name")
    )
    participants = data.get("participants")
    _require(isinstance(participants, list) and len(participants) == 2, "EXACTLY_TWO_PARTICIPANTS_REQUIRED")
    joined, sides = {}, {}
    for i, participant in enumerate(participants):
        pid = _integer(_object(participant).get("id"))
        side = _object(participant.get("meta")).get("location")
        _require(side in ("home", "away") and pid not in joined and side not in sides, "PARTICIPANT_JOIN_CONFLICT")
        joined[pid] = side
        sides[side] = {"id": pid, "pointer": f"/data/participants/{i}",
                       "id_pointer": f"/data/participants/{i}/id", "location_pointer": f"/data/participants/{i}/meta/location"}
    kickoff = _time_field(data, "starting_at_timestamp", "/data")
    kickoff["text"] = _field(data, "starting_at", "/data")
    kickoff["timezone_pointer"] = "/timezone"
    if kickoff["text"]["presence"] == "VALUE":
        text = kickoff["text"]["value"]
        _require(isinstance(text, str) and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", text), "EXPECTED_UTC_WALL_TIME")
        wall = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        _require(kickoff["presence"] != "VALUE" or wall.isoformat() == kickoff["utc"], "KICKOFF_UTC_MISMATCH")
    scores = {
        name: {side: {"presence": "MISSING", "pointer": "/data/scores"} for side in sides}
        for name in SCORE_TYPES.values()
    }
    extras, seen, score_ids = [], set(), set()
    for collection in ("scores", "periods"):
        _require(collection not in data or data[collection] is None
                 or isinstance(data[collection], list), "EXPECTED_OPTIONAL_ARRAY")
    for i, row in enumerate(data.get("scores") or []):
        pointer = f"/data/scores/{i}"
        row = _object(row)
        rid, fid = _integer(row.get("id")), _integer(row.get("fixture_id"))
        pid, tid = _integer(row.get("participant_id")), _integer(row.get("type_id"))
        description = row.get("description")
        _require(isinstance(description, str) and bool(description.strip()), "EXPECTED_SCORE_DESCRIPTION")
        _require(fid == fixture_id and pid in joined, "SCORE_JOIN_MISMATCH")
        _require((tid not in SCORE_TYPES and description not in SCORE_TYPES.values())
                 or SCORE_TYPES.get(tid) == description, "RESERVED_SCORE_TYPE_MISMATCH")
        key = (fid, pid, tid, description)
        _require(key not in seen and rid not in score_ids, "DUPLICATE_SCORE_CONFLICT")
        seen.add(key)
        score_ids.add(rid)
        goals = _field(row, "score", pointer)
        if goals["presence"] == "VALUE":
            score = _object(row["score"])
            _require("participant" not in score or score["participant"] == joined[pid], "SCORE_SIDE_MISMATCH")
            goals = _field(score, "goals", pointer + "/score")
            if goals["presence"] == "VALUE":
                _integer(goals["value"], 0)
        entry = {"pointer": pointer, "type_id": tid, "description": description, "side": joined[pid], "goals": goals}
        if tid in SCORE_TYPES:
            scores[description][joined[pid]] = entry
        else:
            extras.append({**entry, "semantics": "UNKNOWN_SEMANTICS"})
    periods, period_ids, diagnostics = [], set(), []
    for i, row in enumerate(data.get("periods") or []):
        pointer = f"/data/periods/{i}"
        rid = _integer(_object(row).get("id"))
        tid = _integer(row.get("type_id"))
        _require(_integer(row.get("fixture_id")) == fixture_id and rid not in period_ids, "PERIOD_JOIN_CONFLICT")
        nested = _object(row.get("type"))
        _require(_integer(nested.get("id")) == tid and nested.get("model_type") == "period", "PERIOD_TYPE_MISMATCH")
        period_ids.add(rid)
        period = {"pointer": pointer, "type_id": tid, "kind": PERIOD_TYPES.get(tid, "UNKNOWN_SEMANTICS"),
                   "started": _time_field(row, "started", pointer), "ended": _time_field(row, "ended", pointer)}
        for flag in ("ticking", "has_timer"):
            period[flag] = _field(row, flag, pointer)
            if period[flag]["presence"] == "VALUE":
                _require(type(period[flag]["value"]) is bool, "PERIOD_FLAG_NOT_BOOLEAN")
        if tid in (1, 2) and "developer_name" in nested:
            _require(nested["developer_name"] == {1: "1ST_HALF", 2: "2ND_HALF"}[tid], "PERIOD_TYPE_MISMATCH")
        periods.append(period)
        start, end = period["started"].get("value"), period["ended"].get("value")
        if start is not None and end is not None and start > end:
            diagnostics.append({"code": "PERIOD_END_BEFORE_START", "pointer": pointer})
        if end is not None and end > received_at_utc.timestamp():
            diagnostics.append({"code": "PERIOD_END_AFTER_LOCAL_RECEIPT", "pointer": pointer})
        if start is not None and kickoff.get("value") is not None and start < kickoff["value"]:
            diagnostics.append({"code": "PERIOD_START_BEFORE_KICKOFF", "pointer": pointer})
    first_ends = [p["ended"]["value"] for p in periods if p["type_id"] == 1 and p["ended"].get("value") is not None]
    for period in periods:
        if (period["type_id"] == 2 and period["started"].get("value") is not None
                and any(end > period["started"]["value"] for end in first_ends)):
            diagnostics.append({"code": "HALF_INTERVAL_OVERLAP", "pointer": period["pointer"]})
    for side in sides:
        half, regular, current, only = [
            scores[name][side].get("goals", {}).get("value")
            for name in ("1ST_HALF", "2ND_HALF", "CURRENT", "2ND_HALF_ONLY")
        ]
        # CURRENT can include extra time; HALF_ONLY is never a cumulative fallback.
        for inconsistent, code in (
            (half is not None and regular is not None and half > regular, "CUMULATIVE_SCORE_DECREASE"),
            (None not in (half, regular, only) and half + only != regular, "HALF_ONLY_SUM_MISMATCH"),
            (current is not None and regular is not None and current != regular, "CURRENT_DIFFERS_FROM_REGULAR"),
        ):
            if inconsistent:
                diagnostics.append({"code": code, "side": side})
    candidates, pending = [], [(data, "/data")]
    while pending:
        value, pointer = pending.pop()
        items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
        for key, child in items:
            path = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if isinstance(value, dict) and re.search(
                r"publish|publicat|finali[sz]|version|revision|modif|updat|correct|amend|observ|availab|effective|creat|confirm|releas",
                key, re.I,
            ):
                candidates.append({"pointer": path, "present": True, "semantics": "UNKNOWN_SEMANTICS"})
            pending.append((child, path))
    blockers = ["PROVIDER_FINALIZATION_TIME_UNPROVEN", "RESULT_VERSION_PUBLICATION_TIME_UNPROVEN",
                "RESULT_VERSION_IDENTITY_ORDER_UNPROVEN", "SHARED_METADATA_RESULT_CAPTURE_SYSTEM_LIMIT"]
    if kickoff["presence"] != "VALUE" or kickoff["text"]["presence"] != "VALUE":
        blockers.append("KICKOFF_TIME_UNPROVEN")
    if any(row.get("goals", {}).get("presence") != "VALUE" for row in scores["2ND_HALF"].values()):
        blockers.append("REGULAR_TIME_SCORE_INCOMPLETE")
    if not reported_ft:
        blockers.append("REGULAR_TIME_FINAL_STATE_NOT_ESTABLISHED")
    return {
        "fixture_id": fixture_id, "league_id": league_id, "season_id": season_id,
        "normalized_identity": {"provider_fixture_key": str(fixture_id),
                                "provider_competition_id": str(league_id), "provider_season_id": str(season_id),
                                "provider_home_team_id": str(sides["home"]["id"]), "provider_away_team_id": str(sides["away"]["id"])},
        "identity_pointers": {key: f"/data/{key}" for key in ("id", "league_id", "season_id")},
        "state": {"id": state_id, "collection_presence": state_object["presence"],
                  "tokens": {key: _field(state, key, "/data/state") for key in ("state", "short_name", "developer_name")},
                  "semantics": "PROVIDER_REPORTED_FT_AT_CAPTURE" if reported_ft else "NOT_ESTABLISHED"},
        "participants": sides, "kickoff": kickoff, "scores": scores, "extra_scores": extras,
        "score_collection_presence": _field(data, "scores", "/data")["presence"],
        "period_collection_presence": _field(data, "periods", "/data")["presence"],
        "score_semantics": {"1ST_HALF": "FIRST_HALF", "2ND_HALF": "REGULAR_TIME_CUMULATIVE",
                            "2ND_HALF_ONLY": "SECOND_HALF_INTERVAL_ONLY", "CURRENT": "CURRENT_NOT_REGULAR_TIME_PROOF"},
        "regular_time_score": scores["2ND_HALF"], "periods": periods, "diagnostics": diagnostics,
        "time_facts": {"provider_reported_sporting_period_ends": [p["ended"] for p in periods],
                       "recorded_local_receipt": {"utc": received_at_utc.isoformat(), "semantics": LOCAL_RECEIPT},
                       "provider_finalization": {"status": "UNPROVEN"},
                       "result_version_publication": {"status": "UNPROVEN"}},
        "potential_publication_version_fields": sorted(candidates, key=lambda row: row["pointer"]),
        "capture_classification": "SHARED_METADATA_RESULT_CAPTURE",
        "capture_limit_classification": "CURRENT_SYSTEM_LIMIT_NOT_PROVIDER_DISQUALIFICATION",
        "capture_limit": "PRE_PLAN_METADATA_REQUIRES_INDEPENDENT_NON_RESULT_CAPTURE",
        "strict_training_admission": False, "future_prediction_snapshot_eligibility": False,
        "blockers": blockers,
    }


def _relative(reference, *, receipt_path=False):
    # Existing Windows receipts use backslashes; do not resolve away traversal.
    if receipt_path and isinstance(reference, str):
        reference = reference.replace("\\", "/")
    _require(isinstance(reference, str) and reference and not PurePosixPath(reference).is_absolute()
             and not any(c in reference for c in "\\:\0")
             and not any(part in ("", ".", "..") for part in reference.split("/")), "CONTAINED_RELATIVE_PATH_REQUIRED")
    return PurePosixPath(reference)


def _inspect_receipt(evidence, reference, league_id, season_id):
    receipt_bytes = evidence.read(str(_relative(reference)))
    receipt = _object(strict_json_bytes(receipt_bytes))
    _require(receipt.get("available_at_semantics") == LOCAL_RECEIPT, "RECEIPT_SEMANTICS_MISMATCH")
    audit = _object(receipt.get("audit"))
    for field in ("requested_at_utc", "received_at_utc", "available_at_utc"):
        _require(isinstance(audit.get(field), str) and datetime.fromisoformat(audit[field]).utcoffset() == timedelta(0), "RECEIPT_UTC_REQUIRED")
    claimed = RawArtifactMetadata.model_validate({**audit, "payload_sha256": receipt.get("payload_sha256")})
    raw = _relative(receipt.get("raw_path"), receipt_path=True)
    metadata = _relative(receipt.get("metadata_path"), receipt_path=True)
    _require(re.fullmatch(r"[0-9a-f]{64}\.metadata\.json", metadata.name), "METADATA_FILENAME_SHA_REQUIRED")
    digest = metadata.name.removesuffix(".metadata.json")
    _require(raw.parent == metadata.parent and raw.name == digest + ".raw", "RAW_METADATA_FILENAME_MISMATCH")
    metadata_bytes = evidence.read(str(metadata), digest)
    verified = RawArtifactMetadata.model_validate(strict_json_bytes(metadata_bytes))
    _require(claimed == verified and verified.outcome == "SUCCESS"
             and verified.available_at_utc == verified.received_at_utc, "RECEIPT_METADATA_MISMATCH")
    payload = evidence.read(str(raw), verified.payload_sha256)
    _require(_integer(receipt.get("payload_bytes"), 0) == len(payload), "RECEIPT_LENGTH_MISMATCH")
    result = inspect_fixture_response(payload, received_at_utc=verified.received_at_utc,
                                      expected_league_id=league_id, expected_season_id=season_id)
    result["evidence"] = {"receipt_reference": reference, "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                          "metadata_sha256": digest, "payload_sha256": verified.payload_sha256,
                          "raw_reference": str(raw), "metadata_reference": str(metadata),
                          "available_at_semantics": LOCAL_RECEIPT}
    result["time_facts"]["recorded_local_receipt"].update(
        receipt_reference=reference, pointer="/audit/received_at_utc",
        available_at_pointer="/audit/available_at_utc",
    )
    return result


def _write_private(root, reference, payload):
    relative = _relative(reference)
    target = root.joinpath(*relative.parts)
    parent = target.parent
    _require(parent.resolve(strict=True).is_relative_to(root), "OUTPUT_OUTSIDE_PRIVATE_ROOT")
    for directory in (parent, *parent.parents):
        _require(not directory.is_symlink() and not directory.is_junction(), "OUTPUT_REPARSE_DIRECTORY")
        if directory == root:
            break
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".inspection-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)  # Atomic publication, fails even for identical existing output.
    finally:
        if temporary is not None:
            temporary.unlink()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--help"]:
        print(__doc__ + "\n--evidence-root DIR --receipt REL (1-3 times) "
              "--league-id INT --season-id INT --output-relative NEW_REL")
        return 0
    receipts, options = [], {}
    try:
        # Fixed flags avoid argparse/gettext's implicit locale environment reads.
        required = {"--evidence-root", "--league-id", "--season-id", "--output-relative"}
        _require(len(argv) % 2 == 0, "EXPECTED_FLAG_VALUE_PAIRS")
        for flag, value in zip(argv[::2], argv[1::2]):
            _require(flag in required | {"--receipt"} and flag not in options, "UNKNOWN_OR_DUPLICATE_FLAG")
            if flag == "--receipt":
                receipts.append(value)
            else:
                options[flag] = value
        _require(options.keys() == required, "REQUIRED_FLAGS_MISSING")
        _require(1 <= len(receipts) <= 3 and len(set(receipts)) == len(receipts), "ONE_TO_THREE_DISTINCT_RECEIPTS_REQUIRED")
        evidence = LocalTrainingEvidence(options["--evidence-root"], trusted_authorities={})
        _require(evidence.root.is_relative_to(WORKSPACE / "data" / "research"), "PRIVATE_RESEARCH_ROOT_REQUIRED")
        league_id, season_id = int(options["--league-id"]), int(options["--season-id"])
        results = [_inspect_receipt(evidence, ref, league_id, season_id) for ref in receipts]
        _require(len({result["fixture_id"] for result in results}) == len(results), "DUPLICATE_FIXTURE")
        report = {"status": "DIAGNOSTIC_ONLY", "http_requests_made": 0, "permission_generated": False,
                  "retention_until_utc": "2026-09-15T00:00:00Z", "fixtures": results,
                  "strict_training_admission": False, "future_prediction_snapshot_eligibility": False}
        payload = (json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        _write_private(evidence.root, options["--output-relative"], payload)
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        print(json.dumps({"status": "REJECTED", "receipt_count": len(receipts), "http_requests_made": 0}))
        return 2
    print(json.dumps({"status": "DIAGNOSTIC_ONLY", "receipt_count": len(results), "fixture_count": len(results),
                      "diagnostic_count": sum(len(r["diagnostics"]) for r in results),
                      "candidate_key_count": sum(len(r["potential_publication_version_fields"]) for r in results),
                      "strict_training_admission_count": 0, "http_requests_made": 0,
                      "output_sha256": hashlib.sha256(payload).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
