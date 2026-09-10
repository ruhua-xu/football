"""Offline native Sportmonks current-snapshot inspection, not training admission.

Accept only an original response with an object at /data, or a caller-selected
array record at canonical /data/N. Never guess an array selector. Evidence
pointers are RFC 6901 paths into those exact bytes, not a reserialized record.
Each scalar fact retains MISSING (no value), NULL (value=None), or VALUE (including
zero); transforms describe normalization and joins. Evidence is JSON-compatible;
only the top-level normalized time fields contain datetimes. Collection facts carry rows
instead of copying the response. The one raw SHA/record pointer applies equally
to metadata and results and does not assert independent captures.

Public v3 semantics, also used by scripts/inspect_sportmonks_sample.py: type 2 /
2ND_HALF is cumulative regular time; 48996 / 2ND_HALF_ONLY is an interval; CURRENT
can include extra time. Period type 2 ended is a sporting clock, never publication
or finalization. No other upstream time/version field has an admitted meaning
here. `trainable` means only a consistent observed FT outcome at trusted capture,
not rights, strict historical eligibility, or prediction-snapshot eligibility.
Malformed native structure/joins raise ValueError; outcome uncertainty produces
blocking diagnostics and suppresses BOTH normalized goals. Missing period ends
do not veto otherwise established FT, including a finished season with no ends.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from football_system.infrastructure.files.training_evidence import (
    json_pointer,
    strict_json_bytes,
)

_SCORE_TYPES = {1: "1ST_HALF", 2: "2ND_HALF", 1525: "CURRENT", 48996: "2ND_HALF_ONLY"}


def _require(condition: bool, code: str, pointer: str = "") -> None:
    if not condition:
        raise ValueError(f"{code}: {pointer}" if pointer else code)


def _object(value: object, pointer: str) -> dict:
    _require(isinstance(value, dict), "EXPECTED_OBJECT", pointer)
    return value


def _integer(value: object, pointer: str, minimum: int = 1) -> int:
    _require(type(value) is int and value >= minimum, "EXPECTED_STRICT_INTEGER", pointer)
    return value


def _field(obj: dict, key: str, pointer: str) -> dict:
    pointer += "/" + key.replace("~", "~0").replace("/", "~1")
    fact = {"pointer": pointer, "presence": "MISSING"}
    if key in obj:
        fact.update(presence="NULL" if obj[key] is None else "VALUE", value=obj[key])
    return fact


def _time_field(obj: dict, key: str, pointer: str) -> dict:
    fact = _field(obj, key, pointer)
    fact["transform"] = "Nonnegative integral UNIX seconds to UTC; no unit guessing"
    if fact["presence"] == "VALUE":
        seconds = _integer(fact["value"], fact["pointer"], 0)
        _require(seconds <= 253402300799, "UNIX_SECONDS_OUT_OF_RANGE", fact["pointer"])
        fact["utc"] = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return fact


def inspect_observed_fixture(
    payload: bytes,
    *,
    expected_league_id: int,
    expected_season_id: int,
    captured_at_utc: datetime,
    record_pointer: str = "/data",
) -> dict:
    """Inspect one native record without I/O, models, grants, or strict verifiers.

    `record_pointer` selects /data itself (an object) or /data/N (an array entry,
    with N=0 or a positive ASCII decimal integer without leading zeroes). Even a
    singleton array needs an explicit index. Decode the entire original payload
    strictly, then select without copying/reencoding; timezone remains /timezone
    on the original root and raw_payload_sha256 covers the entire capture.

    `provider_raw_status` is the first supplied native state token, or the decimal
    state_id when tokens are absent, never an FT inferred from IDs or scores.
    If both are absent, fail closed. `field_evidence` has
    the returned field names plus raw_payload_sha256, record_pointer, capture,
    scope, participants, state, scores, and periods. Scalar output evidence has
    source facts and a transform; missing joined rows point to their collection
    with row_presence=MISSING, not to a fabricated array index. Provider time /
    version unknowns have no source pointer. Only a consistent FT can expose a
    supplied second-half end as sporting_period_end_at_utc.
    """
    _require(type(payload) is bytes, "EXPECTED_BYTES")
    _integer(expected_league_id, "expected_league_id")
    _integer(expected_season_id, "expected_season_id")
    _require(
        isinstance(captured_at_utc, datetime)
        and captured_at_utc.utcoffset() == timedelta(0),
        "EXPECTED_UTC_CAPTURE",
    )
    root = _object(strict_json_bytes(payload), "")
    _require(isinstance(record_pointer, str) and re.fullmatch(r"/data(?:/(?:0|[1-9][0-9]*))?", record_pointer) is not None,
             "EXPECTED_NATIVE_RECORD_POINTER")
    _require(record_pointer == "/data" or isinstance(root.get("data"), list),
             "ARRAY_SELECTOR_REQUIRES_DATA_ARRAY", "/data")
    data = _object(json_pointer(root, record_pointer), record_pointer)
    _require(root.get("timezone") == "UTC", "EXPLICIT_ROOT_UTC_REQUIRED", "/timezone")

    evidence = {
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "record_pointer": record_pointer,
        "capture": {
            "source": "CALLER_TRUSTED_CAPTURE",
            "value": captured_at_utc.isoformat(),
            "transform": "Trusted repository capture clock; not supplier publication",
        },
    }
    result = {}
    for field, native in (
        ("provider_fixture_key", "id"),
        ("provider_competition_id", "league_id"),
        ("provider_season_id", "season_id"),
    ):
        fact = _field(data, native, record_pointer)
        result[field] = str(_integer(fact.get("value"), fact["pointer"]))
        evidence[field] = {**fact, "transform": "Strict positive JSON integer to decimal string"}
    fixture_id, league_id, season_id = (data[k] for k in ("id", "league_id", "season_id"))
    _require((league_id, season_id) == (expected_league_id, expected_season_id), "SCOPE_MISMATCH")
    league = _object(data.get("league"), record_pointer + "/league")
    season = _object(data.get("season"), record_pointer + "/season")
    country = _object(league.get("country"), record_pointer + "/league/country")
    scope = []
    for obj, key, suffix, expected in (
        (league, "id", "/league", league_id),
        (season, "id", "/season", season_id),
        (season, "league_id", "/season", league_id),
    ):
        fact = _field(obj, key, record_pointer + suffix)
        _require(_integer(fact.get("value"), fact["pointer"]) == expected, "NESTED_SCOPE_MISMATCH", fact["pointer"])
        scope.append(fact)
    for obj, key, suffix, expected in (
        (country, "iso2", "/league/country", "DE"),
        (league, "type", "/league", "league"),
        (league, "sub_type", "/league", "domestic"),
    ):
        fact = _field(obj, key, record_pointer + suffix)
        _require(fact.get("value") == expected, "SCOPE_SHAPE_MISMATCH", fact["pointer"])
        scope.append(fact)
    finished = _field(season, "finished", record_pointer + "/season")
    if finished["presence"] == "VALUE":
        _require(type(finished["value"]) is bool, "SEASON_FINISHED_NOT_BOOLEAN", finished["pointer"])
    evidence["scope"] = scope + [finished]

    participants = data.get("participants")
    _require(isinstance(participants, list) and len(participants) == 2, "EXACTLY_TWO_PARTICIPANTS_REQUIRED")
    joined, sides = {}, {}
    for index, participant in enumerate(participants):
        pointer = f"{record_pointer}/participants/{index}"
        participant = _object(participant, pointer)
        pid = _integer(participant.get("id"), pointer + "/id")
        meta = _object(participant.get("meta"), pointer + "/meta")
        side = meta.get("location")
        _require(isinstance(side, str) and side in ("home", "away") and pid not in joined and side not in sides,
                 "PARTICIPANT_JOIN_CONFLICT", pointer)
        for obj, base in ((data, record_pointer), (participant, pointer)):
            if "placeholder" in obj:
                _require(obj["placeholder"] is False, "PLACEHOLDER_IDENTITY_UNPROVEN", base + "/placeholder")
        joined[pid], sides[side] = side, pid
        result[f"provider_{side}_team_id"] = str(pid)
        evidence[f"provider_{side}_team_id"] = {
            **_field(participant, "id", pointer),
            "location": _field(meta, "location", pointer + "/meta"),
            "transform": "Strict positive JSON integer to decimal string; join by meta.location, not array order",
        }
    evidence["participants"] = {side: evidence[f"provider_{side}_team_id"] for side in sides}

    kickoff = _time_field(data, "starting_at_timestamp", record_pointer)
    text = _field(data, "starting_at", record_pointer)
    _require(kickoff["presence"] == "VALUE", "KICKOFF_SECONDS_REQUIRED", kickoff["pointer"])
    _require(isinstance(text.get("value"), str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", text["value"]) is not None,
             "EXPECTED_UTC_WALL_TIME", text["pointer"])
    kickoff_utc = datetime.fromisoformat(kickoff["utc"])
    _require(kickoff_utc.strftime("%Y-%m-%d %H:%M:%S") == text["value"], "KICKOFF_UTC_MISMATCH", text["pointer"])
    evidence["kickoff_at_utc"] = {**kickoff, "text": text, "timezone": _field(root, "timezone", "")}
    result["kickoff_at_utc"] = kickoff_utc
    diagnostics = []
    if captured_at_utc <= kickoff_utc:
        diagnostics.append({"code": "CAPTURE_NOT_AFTER_KICKOFF", "pointer": kickoff["pointer"]})

    state_id = _field(data, "state_id", record_pointer)
    if state_id["presence"] == "VALUE":
        _integer(state_id["value"], state_id["pointer"])
    state_fact = _field(data, "state", record_pointer)
    state = {} if state_fact["presence"] != "VALUE" else _object(data["state"], state_fact["pointer"])
    nested_id = _field(state, "id", state_fact["pointer"])
    if state:
        _integer(nested_id.get("value"), nested_id["pointer"])
        _require(state_id["presence"] != "VALUE" or state_id["value"] == nested_id["value"], "STATE_ID_MISMATCH", nested_id["pointer"])
    tokens = [_field(state, key, state_fact["pointer"]) for key in ("state", "short_name", "developer_name", "code")]
    status = _field(data, "status", record_pointer)
    aliases = []
    if status["presence"] == "VALUE" and isinstance(status["value"], dict):
        alias_id = _field(status["value"], "id", status["pointer"])
        if alias_id["presence"] == "VALUE":
            _integer(alias_id["value"], alias_id["pointer"])
            _require(alias_id["value"] == state_id.get("value"), "STATE_ID_MISMATCH", alias_id["pointer"])
        aliases = [_field(status["value"], key, status["pointer"]) for key in ("state", "short_name", "developer_name", "code")]
    elif status["presence"] != "MISSING":
        aliases = [status]
    supplied = [fact for fact in tokens + aliases if fact["presence"] != "MISSING"]
    for fact in supplied:
        if fact["presence"] == "VALUE":
            value = fact["value"]
            _require(isinstance(value, str) and bool(value) and value == value.strip(), "EXPECTED_STATE_TOKEN", fact["pointer"])
    raw = next((fact for fact in tokens + aliases if fact["presence"] == "VALUE"), state_id)
    _require(raw["presence"] == "VALUE", "RAW_STATUS_UNAVAILABLE", state_fact["pointer"])
    result["provider_raw_status"] = str(raw["value"])
    evidence["provider_raw_status"] = {
        **raw,
        "sources": [state_id, nested_id, *tokens, *aliases],
        "transform": "First supplied native state token unchanged; otherwise decimal raw state_id, never an inferred FT",
    }
    evidence["state"] = {"id": state_id, "nested_id": nested_id, "presence": state_fact["presence"], "tokens": tokens, "aliases": aliases}
    reported_ft = (
        state_id.get("value") == nested_id.get("value") == 5
        and any(fact.get("value") == "FT" for fact in tokens)
        and all(fact.get("value") == "FT" for fact in supplied)
    )
    if not reported_ft:
        diagnostics.append({"code": "REGULAR_TIME_FT_NOT_ESTABLISHED", "pointer": state_id["pointer"]})

    collections = {}
    for name in ("scores", "periods"):
        fact = _field(data, name, record_pointer)
        _require(fact["presence"] != "VALUE" or isinstance(fact["value"], list), "EXPECTED_OPTIONAL_ARRAY", fact["pointer"])
        collections[name] = {"pointer": fact["pointer"], "presence": fact["presence"], "rows": []}
        evidence[name] = collections[name]
    scores, row_ids, type_keys, description_keys = {}, set(), set(), set()
    for index, row in enumerate(data.get("scores") or []):
        pointer = f"{record_pointer}/scores/{index}"
        row = _object(row, pointer)
        rid, fid, pid, tid = (_integer(row.get(key), pointer + "/" + key) for key in ("id", "fixture_id", "participant_id", "type_id"))
        description = row.get("description")
        _require(isinstance(description, str) and bool(description) and description == description.strip(), "EXPECTED_SCORE_DESCRIPTION", pointer)
        _require(fid == fixture_id and pid in joined, "SCORE_JOIN_MISMATCH", pointer)
        _require((tid not in _SCORE_TYPES and description not in _SCORE_TYPES.values()) or _SCORE_TYPES.get(tid) == description,
                 "RESERVED_SCORE_TYPE_MISMATCH", pointer)
        _require(rid not in row_ids and (pid, tid) not in type_keys and (pid, description) not in description_keys,
                 "DUPLICATE_SCORE_CONFLICT", pointer)
        row_ids.add(rid)
        type_keys.add((pid, tid))
        description_keys.add((pid, description))
        if row.get("type") is not None:
            nested = _object(row["type"], pointer + "/type")
            _require(_integer(nested.get("id"), pointer + "/type/id") == tid, "SCORE_TYPE_MISMATCH", pointer)
            if "model_type" in nested:
                _require(nested["model_type"] == "score", "SCORE_TYPE_MISMATCH", pointer)
            if "developer_name" in nested and tid in _SCORE_TYPES:
                _require(nested["developer_name"] == description, "SCORE_TYPE_MISMATCH", pointer)
        goals = _field(row, "score", pointer)
        if goals["presence"] == "VALUE":
            score = _object(row["score"], goals["pointer"])
            _require("participant" not in score or score["participant"] == joined[pid], "SCORE_SIDE_MISMATCH", pointer)
            goals = _field(score, "goals", pointer + "/score")
            if goals["presence"] == "VALUE":
                _integer(goals["value"], goals["pointer"], 0)
        entry = {"pointer": pointer, "identity": [_field(row, key, pointer) for key in ("id", "fixture_id", "participant_id", "type_id", "description")],
                 "side": joined[pid], "goals": goals, "semantics": {
                     1: "FIRST_HALF", 2: "REGULAR_TIME_CUMULATIVE", 1525: "CURRENT_NOT_REGULAR_TIME_PROOF", 48996: "SECOND_HALF_INTERVAL_ONLY",
                 }.get(tid, "UNKNOWN_SEMANTICS")}
        collections["scores"]["rows"].append(entry)
        scores[pid, tid] = entry
        if tid not in _SCORE_TYPES:
            diagnostics.append({"code": "UNSUPPORTED_SCORE_TYPE", "pointer": pointer})

    periods, period_ids = {}, set()
    for index, row in enumerate(data.get("periods") or []):
        pointer = f"{record_pointer}/periods/{index}"
        row = _object(row, pointer)
        rid, fid, tid = (_integer(row.get(key), pointer + "/" + key) for key in ("id", "fixture_id", "type_id"))
        _require(fid == fixture_id and rid not in period_ids and tid not in periods, "PERIOD_JOIN_CONFLICT", pointer)
        period_ids.add(rid)
        if row.get("type") is not None:
            nested = _object(row["type"], pointer + "/type")
            _require(_integer(nested.get("id"), pointer + "/type/id") == tid and nested.get("model_type") == "period", "PERIOD_TYPE_MISMATCH", pointer)
            if "developer_name" in nested and tid in (1, 2):
                _require(nested["developer_name"] == _SCORE_TYPES[tid], "PERIOD_TYPE_MISMATCH", pointer)
        entry = {"pointer": pointer, "identity": [_field(row, key, pointer) for key in ("id", "fixture_id", "type_id")],
                 "started": _time_field(row, "started", pointer), "ended": _time_field(row, "ended", pointer),
                 "ticking": _field(row, "ticking", pointer), "has_timer": _field(row, "has_timer", pointer)}
        periods[tid] = entry
        collections["periods"]["rows"].append(entry)
        for flag in ("ticking", "has_timer"):
            if entry[flag]["presence"] == "VALUE":
                _require(type(entry[flag]["value"]) is bool, "PERIOD_FLAG_NOT_BOOLEAN", entry[flag]["pointer"])
        if tid not in (1, 2):
            diagnostics.append({"code": "EXTRA_OR_PENALTY_PERIOD" if tid in (3, 5) else "UNSUPPORTED_PERIOD_TYPE", "pointer": pointer})
        if entry["ticking"].get("value") is True:
            diagnostics.append({"code": "ACTIVE_PERIOD", "pointer": entry["ticking"]["pointer"]})
        start, end = (datetime.fromisoformat(entry[key]["utc"]) if "utc" in entry[key] else None for key in ("started", "ended"))
        if any(value is not None and not kickoff_utc <= value <= captured_at_utc for value in (start, end)):
            diagnostics.append({"code": "PERIOD_OUTSIDE_CAPTURE_WINDOW", "pointer": pointer})
        if start is not None and end is not None and start > end:
            diagnostics.append({"code": "PERIOD_END_BEFORE_START", "pointer": pointer})
    if 1 in periods and 2 in periods:
        first, second = periods[1], periods[2]
        first_times = [first[key]["value"] for key in ("started", "ended") if first[key]["presence"] == "VALUE"]
        second_times = [second[key]["value"] for key in ("started", "ended") if second[key]["presence"] == "VALUE"]
        if first_times and second_times and max(first_times) > min(second_times):
            diagnostics.append({"code": "HALF_INTERVAL_OVERLAP", "pointer": second["pointer"]})

    for side, pid in sides.items():
        values = [scores.get((pid, tid), {}).get("goals", {}).get("value") for tid in (1, 2, 1525, 48996)]
        half, regular, current, only = values
        for inconsistent, code in (
            (regular is None, "REGULAR_TIME_SCORE_INCOMPLETE"),
            (regular is not None and any(value is not None and value > regular for value in (half, only)), "CUMULATIVE_SCORE_DECREASE"),
            (None not in (half, regular, only) and half + only != regular, "HALF_ONLY_SUM_MISMATCH"),
            (current is not None and regular is not None and current != regular, "CURRENT_DIFFERS_FROM_REGULAR"),
        ):
            if inconsistent:
                diagnostics.append({"code": code, "side": side, "pointer": collections["scores"]["pointer"]})
        row = scores.get((pid, 2))
        evidence[f"regular_time_{side}_goals"] = {
            "sources": [row["goals"]] if row is not None else [],
            "row_pointer": row["pointer"] if row is not None else collections["scores"]["pointer"],
            "row_presence": "VALUE" if row is not None else "MISSING",
            "participant_id": pid,
            "transform": "Join fixture/participant IDs and type 2 / 2ND_HALF; cumulative regular time unchanged, only if trainable; no CURRENT or HALF_ONLY fallback",
        }

    trainable = reported_ft and not diagnostics
    result["trainable"] = trainable
    for side, pid in sides.items():
        result[f"regular_time_{side}_goals"] = scores[pid, 2]["goals"]["value"] if trainable else None
    end = periods.get(2, {}).get("ended")
    result["sporting_period_end_at_utc"] = datetime.fromisoformat(end["utc"]) if trainable and end is not None and "utc" in end else None
    evidence["sporting_period_end_at_utc"] = {
        "sources": [end] if end is not None else [],
        "row_pointer": periods[2]["pointer"] if 2 in periods else collections["periods"]["pointer"],
        "row_presence": "VALUE" if 2 in periods else "MISSING",
        "transform": "Supplied period type 2 ended UNIX seconds, only on consistent FT; no duration/clock inference or provider finalization meaning",
    }
    for field in ("provider_publication_at_utc", "provider_finalized_at_utc", "provider_version_id"):
        result[field] = None
        evidence[field] = {"sources": [], "presence": "UNKNOWN", "transform": "No documented upstream semantics admitted; never inferred from capture, headers, named fields, or sporting clocks"}
    evidence["trainable"] = {
        "source_pointers": [state_id["pointer"], state_fact["pointer"], status["pointer"], kickoff["pointer"], collections["scores"]["pointer"], collections["periods"]["pointer"]],
        "capture_source": "CALLER_TRUSTED_CAPTURE",
        "transform": "state_id=state.id=5 with supplied FT tokens, capture strictly after kickoff, both type-2 goals, no contradictory scores/status/periods; observed validation only",
    }
    result.update(field_evidence=evidence, diagnostics=diagnostics)
    return result
