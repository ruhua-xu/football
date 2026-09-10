"""Invented native fixtures only; no acquisition, accounts, or captured samples."""

import builtins
import copy
import hashlib
import inspect
import json
import os
import random
import socket
from datetime import datetime, timedelta, timezone

import pytest

from football_system.infrastructure.files import training_evidence
from football_system.infrastructure.providers.real.sportmonks_observed import (
    inspect_observed_fixture,
)
from scripts import inspect_sportmonks_sample

KICKOFF = datetime(2002, 3, 4, 12, 0, tzinfo=timezone.utc)
STAMP = int(KICKOFF.timestamp())
CAPTURE = KICKOFF + timedelta(days=100)
SCORE_TYPES = {1: "1ST_HALF", 2: "2ND_HALF", 1525: "CURRENT", 48996: "2ND_HALF_ONLY"}
UNKNOWN_FIELDS = ("provider_publication_at_utc", "provider_finalized_at_utc", "provider_version_id")
RETURN_FIELDS = {
    "provider_fixture_key", "provider_competition_id", "provider_season_id",
    "provider_home_team_id", "provider_away_team_id", "kickoff_at_utc",
    "provider_raw_status", "trainable", "regular_time_home_goals",
    "regular_time_away_goals", "sporting_period_end_at_utc",
    "field_evidence", "diagnostics", *UNKNOWN_FIELDS,
}


@pytest.fixture
def document():
    data = {
        "id": 111, "league_id": 222, "season_id": 333,
        "league": {"id": 222, "country": {"iso2": "DE"}, "type": "league", "sub_type": "domestic"},
        "season": {"id": 333, "league_id": 222, "finished": True},
        "starting_at_timestamp": STAMP, "starting_at": "2002-03-04 12:00:00",
        "state_id": 5, "state": {"id": 5, "state": "FT", "short_name": "FT", "developer_name": "FT"},
        "participants": [{"id": pid, "meta": {"location": side}} for pid, side in ((444, "home"), (555, "away"))],
        "scores": [
            {"id": 600 + index * 2 + side_index, "fixture_id": 111, "participant_id": pid,
             "type_id": tid, "description": SCORE_TYPES[tid], "score": {"participant": side, "goals": goals[side_index]}}
            for index, (tid, goals) in enumerate(((1, (1, 0)), (2, (3, 0)), (1525, (3, 0)), (48996, (2, 0))))
            for side_index, (pid, side) in enumerate(((444, "home"), (555, "away")))
        ],
        "periods": [
            {"id": 700 + index, "fixture_id": 111, "type_id": tid,
             "type": {"id": tid, "model_type": "period", "developer_name": SCORE_TYPES[tid]},
             "started": STAMP + start, "ended": STAMP + end, "ticking": False, "has_timer": True,
             "period_length": 999, "minutes": 888, "time_added": 777}
            for index, (tid, start, end) in enumerate(((1, 0, 2800), (2, 3700, 6600)))
        ],
    }
    return {"timezone": "UTC", "data": data}


@pytest.fixture
def batch_document(document):
    other = copy.deepcopy(document["data"])
    other.update(id=112, league_id=223, season_id=334,
                 starting_at_timestamp=STAMP + 86400, starting_at="2002-03-05 12:00:00")
    other["league"]["id"] = 223
    other["season"].update(id=334, league_id=223)
    teams = {"home": 666, "away": 777}
    for participant in other["participants"]:
        participant["id"] = teams[participant["meta"]["location"]]
    for row in other["scores"]:
        side = row["score"]["participant"]
        row.update(id=row["id"] + 1000, fixture_id=112, participant_id=teams[side])
        row["score"]["goals"] = {1: (0, 1), 2: (2, 4), 1525: (2, 4), 48996: (2, 3)}[row["type_id"]][side == "away"]
    for row in other["periods"]:
        row.update(id=row["id"] + 1000, fixture_id=112,
                   started=row["started"] + 86400, ended=row["ended"] + 86400)
    for collection in ("participants", "scores", "periods"):
        other[collection].reverse()
    document["data"] = [document["data"], other]
    return document


def parse(document, **kwargs):
    return inspect_observed_fixture(
        json.dumps(document).encode(),
        **{"expected_league_id": 222, "expected_season_id": 333, "captured_at_utc": CAPTURE, **kwargs},
    )


def replace(document, path, value):
    parts = path.split("/")
    for key in parts[:-1]:
        document = document[int(key)] if isinstance(document, list) else document[key]
    if value == "MISSING":
        del document[parts[-1]]
    else:
        document[parts[-1]] = value


def assert_not_trainable(result, code):
    assert result["trainable"] is False
    assert result["regular_time_home_goals"] is None
    assert result["regular_time_away_goals"] is None
    assert code in {item["code"] for item in result["diagnostics"]}


@pytest.mark.parametrize("singleton", [False, True])
@pytest.mark.parametrize("seed", range(8))
def test_full_arrays_join_by_identity_and_preserve_exact_pointers(document, singleton, seed):
    for name in ("participants", "scores", "periods"):
        random.Random(seed).shuffle(document["data"][name])
    if singleton:
        document["data"] = [document["data"]]
    payload = json.dumps(document, indent=2).encode() + b"\n"
    pointer = "/data/0" if singleton else "/data"
    result = inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333, captured_at_utc=CAPTURE, record_pointer=pointer)
    assert set(result) == RETURN_FIELDS
    assert [result[field] for field in ("provider_fixture_key", "provider_competition_id", "provider_season_id", "provider_home_team_id", "provider_away_team_id")] == ["111", "222", "333", "444", "555"]
    assert result["kickoff_at_utc"] == KICKOFF
    assert result["kickoff_at_utc"].tzinfo is timezone.utc
    assert result["provider_raw_status"] == "FT"
    assert result["trainable"] is True and result["diagnostics"] == []
    assert (result["regular_time_home_goals"], result["regular_time_away_goals"]) == (3, 0)
    assert result["sporting_period_end_at_utc"] == KICKOFF + timedelta(seconds=6600)
    evidence = result["field_evidence"]
    assert json.loads(json.dumps(evidence, allow_nan=False)) == evidence
    assert evidence["record_pointer"] == pointer
    assert evidence["raw_payload_sha256"] == hashlib.sha256(payload).hexdigest()
    for field in ("provider_fixture_key", "provider_competition_id", "provider_season_id", "provider_home_team_id", "provider_away_team_id"):
        fact = evidence[field]
        assert str(training_evidence.json_pointer(document, fact["pointer"])) == result[field]
        assert fact["transform"] and fact["presence"] == "VALUE"
    for row in evidence["scores"]["rows"]:
        assert training_evidence.json_pointer(document, row["goals"]["pointer"]) == row["goals"]["value"]
        for fact in row["identity"]:
            assert training_evidence.json_pointer(document, fact["pointer"]) == fact["value"]
    for side in ("home", "away"):
        fact = evidence[f"regular_time_{side}_goals"]
        source = training_evidence.json_pointer(document, fact["row_pointer"])
        assert source["type_id"] == 2 and source["description"] == "2ND_HALF"
        assert fact["sources"][0]["pointer"] == fact["row_pointer"] + "/score/goals"
    for field in UNKNOWN_FIELDS:
        assert result[field] is None
        assert evidence[field]["presence"] == "UNKNOWN" and evidence[field]["sources"] == []


def test_public_signature_has_optional_keyword_only_record_pointer():
    signature = inspect.signature(inspect_observed_fixture)
    assert list(signature.parameters) == ["payload", "expected_league_id", "expected_season_id", "captured_at_utc", "record_pointer"]
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in list(signature.parameters.values())[1:])
    assert signature.parameters["record_pointer"].default == "/data"


@pytest.mark.parametrize("value", [None, "MISSING", [], [None], [{}, {}], False, "fixture"])
def test_requires_explicit_single_data_record(document, value):
    replace(document, "data", value)
    with pytest.raises(ValueError):
        parse(document)


def test_multi_fixture_payload_is_not_filtered_to_expected_scope(document):
    other = copy.deepcopy(document["data"])
    other["league_id"] = 999
    document["data"] = [document["data"], other]
    with pytest.raises(ValueError, match="EXPECTED_OBJECT: /data"):
        parse(document)


def test_batch_selects_distinct_records_from_one_original_capture(batch_document):
    payload = json.dumps(batch_document, indent=2).encode() + b"\n  "
    digest = hashlib.sha256(payload).hexdigest()
    inspections = []
    for index, (league, season, home, away, home_goals, away_goals) in enumerate(
        ((222, 333, 444, 555, 3, 0), (223, 334, 666, 777, 2, 4))
    ):
        pointer = f"/data/{index}"
        result = inspect_observed_fixture(
            payload, expected_league_id=league, expected_season_id=season,
            captured_at_utc=CAPTURE, record_pointer=pointer,
        )
        inspections.append(result)
        assert result["trainable"] is True and result["diagnostics"] == []
        assert result["provider_fixture_key"] == str(111 + index)
        assert result["provider_competition_id"] == str(league)
        assert result["provider_season_id"] == str(season)
        assert (result["provider_home_team_id"], result["provider_away_team_id"]) == (str(home), str(away))
        assert (result["regular_time_home_goals"], result["regular_time_away_goals"]) == (home_goals, away_goals)
        assert result["kickoff_at_utc"] == KICKOFF + timedelta(days=index)
        assert result["sporting_period_end_at_utc"] == KICKOFF + timedelta(days=index, seconds=6600)
        evidence = result["field_evidence"]
        assert evidence["raw_payload_sha256"] == digest
        assert evidence["record_pointer"] == pointer
        assert evidence["kickoff_at_utc"]["timezone"]["pointer"] == "/timezone"
        pending = [evidence]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                paths = [path for key, path in value.items() if key in ("pointer", "record_pointer", "row_pointer")]
                paths.extend(value.get("source_pointers", []))
                assert all(path == "/timezone" or path == pointer or path.startswith(pointer + "/") for path in paths)
                if "pointer" in value and "value" in value:
                    assert training_evidence.json_pointer(batch_document, value["pointer"]) == value["value"]
                pending.extend(value.values())
    assert inspections[0]["field_evidence"]["capture"] == inspections[1]["field_evidence"]["capture"]
    assert hashlib.sha256(payload).hexdigest() == digest
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333,
                                 captured_at_utc=CAPTURE, record_pointer="/data/1")


def test_singleton_array_also_requires_an_explicit_selector(document):
    document["data"] = [document["data"]]
    with pytest.raises(ValueError, match="EXPECTED_OBJECT: /data"):
        parse(document)
    assert parse(document, record_pointer="/data/0")["trainable"] is True


@pytest.mark.parametrize("pointer", [None, True, 0, [], b"/data/0", "", "/", "data/0",
    "/other", "/data/", "/data/-", "/data/-1", "/data/-0", "/data/+0", "/data/00", "/data/01",
    "/data/1.0", "/data/1e0", "/data/ 0", "/data/0 ", "/data/0\n", "/data/\u0660",
    "/data/%30", "/data/~00", "/data//0", "/data/0/id", "/data/0/", "/data/0/../1",
    "/data~10", "$.data[0]", "/data/*", "#/data/0"])
def test_record_selector_accepts_only_canonical_native_paths(batch_document, pointer):
    with pytest.raises(ValueError, match="EXPECTED_NATIVE_RECORD_POINTER"):
        parse(batch_document, record_pointer=pointer)


@pytest.mark.parametrize("pointer", ["/data/2", "/data/10", "/data/" + "9" * 5000])
def test_missing_or_unrepresentable_array_index_is_not_guessed(batch_document, pointer):
    with pytest.raises(ValueError):
        parse(batch_document, record_pointer=pointer)


def test_canonical_multidigit_index_selects_exactly_that_record(batch_document):
    batch_document["data"] = [None] * 10 + [batch_document["data"][0]]
    result = parse(batch_document, record_pointer="/data/10")
    assert result["provider_fixture_key"] == "111" and result["trainable"] is True
    assert result["field_evidence"]["provider_fixture_key"]["pointer"] == "/data/10/id"


@pytest.mark.parametrize("selected", [None, False, [], 111, "fixture"])
def test_selected_array_entry_must_be_an_object(document, selected):
    document["data"] = [document["data"], selected]
    with pytest.raises(ValueError, match="EXPECTED_OBJECT: /data/1"):
        parse(document, record_pointer="/data/1")


def test_array_selector_cannot_address_a_numeric_object_key(document):
    document["data"] = {"0": document["data"]}
    with pytest.raises(ValueError, match="ARRAY_SELECTOR_REQUIRES_DATA_ARRAY"):
        parse(document, record_pointer="/data/0")


@pytest.mark.parametrize("timezone_value", ["MISSING", None, "Europe/Berlin"])
def test_selected_records_cannot_supply_the_original_root_timezone(batch_document, timezone_value):
    batch_document["data"][0]["timezone"] = "UTC"
    replace(batch_document, "timezone", timezone_value)
    with pytest.raises(ValueError, match="EXPLICIT_ROOT_UTC_REQUIRED: /timezone"):
        parse(batch_document, record_pointer="/data/0")


@pytest.mark.parametrize("collection", ["scores", "periods"])
def test_rows_from_other_fixture_cannot_satisfy_selected_joins(batch_document, collection):
    batch_document["data"][1][collection][0] = copy.deepcopy(batch_document["data"][0][collection][0])
    with pytest.raises(ValueError, match="SCORE_JOIN_MISMATCH|PERIOD_JOIN_CONFLICT"):
        parse(batch_document, expected_league_id=223, expected_season_id=334, record_pointer="/data/1")
    assert parse(batch_document, record_pointer="/data/0")["trainable"] is True


def test_other_fixture_cannot_supply_missing_goals_or_participants(batch_document):
    other = batch_document["data"][1]
    other["scores"] = [row for row in other["scores"] if row["type_id"] != 2]
    result = parse(batch_document, expected_league_id=223, expected_season_id=334, record_pointer="/data/1")
    assert_not_trainable(result, "REGULAR_TIME_SCORE_INCOMPLETE")
    assert result["field_evidence"]["regular_time_home_goals"]["row_pointer"] == "/data/1/scores"
    assert all(diagnostic["pointer"].startswith("/data/1/") for diagnostic in result["diagnostics"])
    other["scores"][0]["participant_id"] = 444
    with pytest.raises(ValueError, match="SCORE_JOIN_MISMATCH"):
        parse(batch_document, expected_league_id=223, expected_season_id=334, record_pointer="/data/1")


def test_unselected_record_changes_affect_only_full_capture_lineage(batch_document):
    before = parse(batch_document, record_pointer="/data/0")
    batch_document["data"][1] = {"id": False, "state_id": 1, "timezone": "Europe/Berlin"}
    after = parse(batch_document, record_pointer="/data/0")
    assert after["field_evidence"].pop("raw_payload_sha256") != before["field_evidence"].pop("raw_payload_sha256")
    assert before == after and after["trainable"] is True


@pytest.mark.parametrize("extra", [b'"number":NaN', b'"number":1e999', b'"id":1,"id":2'])
def test_strict_json_applies_to_unselected_records_too(document, extra):
    payload = b'{"timezone":"UTC","data":[' + json.dumps(document["data"]).encode() + b',{' + extra + b'}]}'
    with pytest.raises(ValueError, match="invalid local JSON evidence"):
        inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333,
                                 captured_at_utc=CAPTURE, record_pointer="/data/0")


@pytest.mark.parametrize("path", [
    "id", "league_id", "season_id", "league/id", "season/id", "season/league_id",
    "participants/0/id", "state_id", "state/id", "scores/0/id", "scores/0/fixture_id",
    "scores/0/participant_id", "scores/0/type_id", "periods/0/id", "periods/0/fixture_id",
    "periods/0/type_id", "periods/0/type/id",
])
@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.0, "1", None])
def test_native_ids_are_strict_positive_json_integers(document, path, bad):
    replace(document, "data/" + path, bad)
    if path == "state_id" and bad is None:
        assert_not_trainable(parse(document), "REGULAR_TIME_FT_NOT_ESTABLISHED")
    else:
        with pytest.raises(ValueError):
            parse(document)


@pytest.mark.parametrize("field", ["expected_league_id", "expected_season_id"])
@pytest.mark.parametrize("bad", [True, False, 0, -1, 222.0, "222", None, 999])
def test_caller_scope_is_validated_not_used_to_overwrite_source(document, field, bad):
    with pytest.raises(ValueError):
        parse(document, **{field: bad})


@pytest.mark.parametrize("path,bad", [
    ("league_id", 223), ("season_id", 334), ("league/id", 223), ("season/id", 334),
    ("season/league_id", 223), ("league/country/iso2", "GB"), ("league/type", "cup"),
    ("league/sub_type", "international"), ("season/finished", 1), ("participants", []),
    ("participants/1/id", 444), ("participants/1/meta/location", "home"),
    ("participants/0/meta/location", "HOME"), ("participants/0/meta/location", {}),
    ("scores/0/fixture_id", 112), ("scores/0/participant_id", 999),
    ("scores/0/score/participant", "away"), ("scores/0/description", "2ND_HALF"),
    ("scores/0/type_id", 999), ("scores/0/description", "OTHER"),
    ("periods/0/fixture_id", 112), ("periods/0/type/id", 2),
    ("periods/0/type/model_type", "score"), ("periods/0/type/developer_name", "EXTRA_TIME"),
    ("periods/0/ticking", 1), ("periods/0/has_timer", "false"),
    ("scores", False), ("periods", {}),
])
def test_native_scope_joins_aliases_and_shapes_fail_closed(document, path, bad):
    replace(document, "data/" + path, bad)
    with pytest.raises(ValueError):
        parse(document)


def test_extra_participant_and_placeholder_cannot_establish_identity(document):
    document["data"]["participants"].append({"id": 666, "meta": {"location": "neutral"}})
    with pytest.raises(ValueError, match="EXACTLY_TWO_PARTICIPANTS"):
        parse(document)
    document["data"]["participants"].pop()
    document["data"]["participants"][0]["placeholder"] = True
    with pytest.raises(ValueError, match="PLACEHOLDER"):
        parse(document)


@pytest.mark.parametrize("change", ["same", "different_goals", "row_id", "unknown_type_alias", "unknown_description_alias"])
def test_score_uniqueness_checks_id_type_and_description_independently(document, change):
    rows = document["data"]["scores"]
    duplicate = copy.deepcopy(rows[0])
    duplicate["id"] = 999
    if change == "different_goals":
        duplicate["score"]["goals"] = 99
    elif change == "row_id":
        duplicate = copy.deepcopy(rows[-1])
        duplicate["id"] = rows[0]["id"]
    elif change.startswith("unknown"):
        rows[0].update(type_id=900, description="SYNTHETIC_OTHER")
        duplicate.update(type_id=900, description="SYNTHETIC_OTHER")
        duplicate["type_id" if change == "unknown_type_alias" else "description"] = 901 if change == "unknown_type_alias" else "SYNTHETIC_ALIAS"
    rows.append(duplicate)
    with pytest.raises(ValueError, match="DUPLICATE_SCORE"):
        parse(document)


@pytest.mark.parametrize("same_id", [False, True])
def test_duplicate_periods_rejected_even_with_identical_clocks(document, same_id):
    duplicate = copy.deepcopy(document["data"]["periods"][1])
    if not same_id:
        duplicate["id"] = 999
    document["data"]["periods"].append(duplicate)
    with pytest.raises(ValueError, match="PERIOD_JOIN_CONFLICT"):
        parse(document)


@pytest.mark.parametrize("field,bad", [("id", True), ("id", 2), ("model_type", "period"), ("developer_name", "EXTRA_TIME")])
def test_optional_score_type_include_must_agree_with_row(document, field, bad):
    nested = {"id": 1, "model_type": "score", "developer_name": "1ST_HALF"}
    document["data"]["scores"][0]["type"] = nested
    assert parse(document)["trainable"] is True
    nested[field] = bad
    with pytest.raises(ValueError):
        parse(document)


@pytest.mark.parametrize("path", ["scores/2/score/goals", "scores/2/score", "scores/0/score/goals", "periods/1/ended"])
@pytest.mark.parametrize("value,presence", [("MISSING", "MISSING"), (None, "NULL"), (0, "VALUE")])
def test_missing_null_and_zero_remain_distinct_source_facts(document, path, value, presence):
    replace(document, "data/" + path, value)
    if path == "scores/2/score" and value == 0:
        with pytest.raises(ValueError, match="EXPECTED_OBJECT"):
            parse(document)
        return
    result = parse(document)
    collection, index = path.split("/")[:2]
    row = result["field_evidence"][collection]["rows"][int(index)]
    fact = row["goals" if collection == "scores" else "ended"]
    assert fact["pointer"] == "/data/" + path
    assert fact["presence"] == presence
    assert ("value" in fact) == (presence != "MISSING")
    if presence != "MISSING":
        assert fact["value"] == value
    if path.startswith("scores/2") and value != 0:
        assert_not_trainable(result, "REGULAR_TIME_SCORE_INCOMPLETE")
    elif path == "periods/1/ended" and value != 0:
        assert result["trainable"] is True and result["sporting_period_end_at_utc"] is None


@pytest.mark.parametrize("collection", ["scores", "periods"])
@pytest.mark.parametrize("value,presence", [("MISSING", "MISSING"), (None, "NULL"), ([], "VALUE")])
def test_collection_absence_and_null_preserved_without_end_fabrication(document, collection, value, presence):
    replace(document, "data/" + collection, value)
    result = parse(document)
    assert result["field_evidence"][collection]["presence"] == presence
    if collection == "scores":
        assert_not_trainable(result, "REGULAR_TIME_SCORE_INCOMPLETE")
        assert result["field_evidence"]["regular_time_home_goals"]["row_presence"] == "MISSING"
    else:
        assert result["trainable"] is True
        assert result["sporting_period_end_at_utc"] is None
    assert all(result[field] is None for field in UNKNOWN_FIELDS)


def test_current_and_half_only_never_substitute_for_cumulative_regular_time(document):
    document["data"]["scores"] = [row for row in document["data"]["scores"] if row["type_id"] != 2]
    result = parse(document)
    assert_not_trainable(result, "REGULAR_TIME_SCORE_INCOMPLETE")
    assert result["field_evidence"]["regular_time_home_goals"]["sources"] == []


def test_only_regular_rows_are_required_and_current_season_does_not_veto_known_ft(document):
    document["data"]["scores"] = [row for row in document["data"]["scores"] if row["type_id"] == 2]
    document["data"]["season"]["finished"] = False
    document["data"].pop("periods")
    assert parse(document)["trainable"] is True


@pytest.mark.parametrize("index,value,code", [(0, 4, "CUMULATIVE_SCORE_DECREASE"), (4, 5, "CURRENT_DIFFERS_FROM_REGULAR"), (6, 1, "HALF_ONLY_SUM_MISMATCH"), (6, 4, "CUMULATIVE_SCORE_DECREASE")])
def test_contradictory_full_score_arrays_suppress_both_goals(document, index, value, code):
    document["data"]["scores"][index]["score"]["goals"] = value
    assert_not_trainable(parse(document), code)


@pytest.mark.parametrize("kind", ["extra", "shootout", "unknown"])
@pytest.mark.parametrize("collection", ["scores", "periods"])
def test_extra_shootout_and_unknown_rows_are_never_silently_ignored(document, collection, kind):
    row = copy.deepcopy(document["data"][collection][0])
    row.update(id=999, type_id={"extra": 3, "shootout": 5, "unknown": 98765}[kind])
    if collection == "scores":
        row["description"] = {"extra": "EXTRA_TIME", "shootout": "PENALTIES", "unknown": "SYNTHETIC_OTHER"}[kind]
        row["score"]["goals"] = 0
        code = "UNSUPPORTED_SCORE_TYPE"
    else:
        row["type"] = {"id": row["type_id"], "model_type": "period"}
        code = "UNSUPPORTED_PERIOD_TYPE" if kind == "unknown" else "EXTRA_OR_PENALTY_PERIOD"
    document["data"][collection].append(row)
    random.Random(42).shuffle(document["data"][collection])
    assert_not_trainable(parse(document), code)


@pytest.mark.parametrize("state_id,token", [(1, "NS"), (4, "INPLAY_2ND_HALF"), (6, "AET"), (8, "FT_PEN"), (9, "POSTP"), (10, "CANCELLED"), (12, "ABANDONED"), (14, "WO"), (99, "WITHDRAWN")])
def test_non_ft_states_never_normalize_populated_scores(document, state_id, token):
    document["data"].update(state_id=state_id, state={"id": state_id, "state": token, "short_name": token, "developer_name": token})
    result = parse(document)
    assert result["provider_raw_status"] == token
    assert_not_trainable(result, "REGULAR_TIME_FT_NOT_ESTABLISHED")


@pytest.mark.parametrize("path,value", [("state", None), ("state", "MISSING"), ("state", {}), ("state_id", None), ("state_id", "MISSING"), ("state/short_name", "AET"), ("state/developer_name", None), ("state/state", "ft"), ("status", "CANCELLED"), ("status", {"short_name": "PEN_LIVE"})])
def test_missing_or_contradictory_ft_signals_cannot_be_repaired_by_scores(document, path, value):
    replace(document, "data/" + path, value)
    assert_not_trainable(parse(document), "REGULAR_TIME_FT_NOT_ESTABLISHED")


@pytest.mark.parametrize("path,value", [("state/id", 6), ("status", {"id": 6, "short_name": "FT"}), ("state/state", True), ("state/state", " FT ")])
def test_invalid_status_shape_and_id_aliases_raise(document, path, value):
    replace(document, "data/" + path, value)
    with pytest.raises(ValueError):
        parse(document)


def test_optional_consistent_state_aliases_and_minimal_native_state(document):
    document["data"]["state"] = {"id": 5, "short_name": "FT"}
    document["data"]["status"] = {"id": 5, "code": "FT"}
    assert parse(document)["trainable"] is True


@pytest.mark.parametrize("state", ["MISSING", None, {}])
def test_raw_state_id_is_retained_without_inventing_ft_token(document, state):
    replace(document, "data/state", state)
    result = parse(document)
    assert result["provider_raw_status"] == "5"
    assert result["field_evidence"]["provider_raw_status"]["pointer"] == "/data/state_id"
    assert_not_trainable(result, "REGULAR_TIME_FT_NOT_ESTABLISHED")
    document["data"].pop("state_id")
    with pytest.raises(ValueError, match="RAW_STATUS_UNAVAILABLE"):
        parse(document)


@pytest.mark.parametrize("path,value,code", [("periods/1/ticking", True, "ACTIVE_PERIOD"), ("periods/1/ended", STAMP + 3600, "PERIOD_END_BEFORE_START"), ("periods/0/started", STAMP - 1, "PERIOD_OUTSIDE_CAPTURE_WINDOW"), ("periods/1/ended", int(CAPTURE.timestamp()) + 1, "PERIOD_OUTSIDE_CAPTURE_WINDOW"), ("periods/0/ended", STAMP + 3800, "HALF_INTERVAL_OVERLAP"), ("periods/1/started", int(CAPTURE.timestamp()) + 1, "PERIOD_OUTSIDE_CAPTURE_WINDOW")])
def test_active_or_contradictory_periods_veto_ft(document, path, value, code):
    replace(document, "data/" + path, value)
    assert_not_trainable(parse(document), code)


@pytest.mark.parametrize("offset", [-1, 0])
def test_capture_must_be_strictly_after_kickoff(document, offset):
    assert_not_trainable(parse(document, captured_at_utc=KICKOFF + timedelta(seconds=offset)), "CAPTURE_NOT_AFTER_KICKOFF")


@pytest.mark.parametrize("capture", [None, STAMP, "2002-03-05T12:00:00Z", KICKOFF.replace(tzinfo=None), KICKOFF.astimezone(timezone(timedelta(hours=1)))])
def test_capture_is_a_trusted_explicit_utc_datetime_not_a_supplier_field(document, capture):
    with pytest.raises(ValueError, match="EXPECTED_UTC_CAPTURE"):
        parse(document, captured_at_utc=capture)


@pytest.mark.parametrize("path", ["starting_at_timestamp", "scores/0/score/goals", "periods/0/started", "periods/0/ended"])
@pytest.mark.parametrize("bad", [True, False, -1, 1.0, "1"])
def test_goals_and_unix_seconds_are_not_coerced(document, path, bad):
    replace(document, "data/" + path, bad)
    with pytest.raises(ValueError, match="EXPECTED_STRICT_INTEGER"):
        parse(document)


@pytest.mark.parametrize("path,bad", [("timezone", "Europe/Berlin"), ("timezone", None), ("timezone", "MISSING"), ("data/starting_at", "2002-03-04 13:00:00"), ("data/starting_at", "2002-03-04T12:00:00Z"), ("data/starting_at", "2002-02-31 12:00:00"), ("data/starting_at", "Mon, 04 Mar 2002 12:00:00 GMT"), ("data/starting_at", None), ("data/starting_at_timestamp", None), ("data/starting_at_timestamp", "MISSING"), ("data/starting_at_timestamp", STAMP * 1000), ("data/periods/1/ended", STAMP * 1000), ("data/periods/1/ended", 253402300800)])
def test_kickoff_requires_native_seconds_text_and_explicit_root_utc(document, path, bad):
    replace(document, path, bad)
    with pytest.raises(ValueError):
        parse(document)


@pytest.mark.parametrize("extra", [b'"number":NaN', b'"number":Infinity', b'"number":-Infinity', b'"number":1e999', b'"same":1,"same":1'])
def test_strict_json_rejects_nonfinite_and_duplicate_keys_even_in_unknown_fields(document, extra):
    payload = json.dumps(document).encode()[:-1] + b',"ignored":{' + extra + b'}}'
    with pytest.raises(ValueError, match="invalid local JSON evidence"):
        inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333, captured_at_utc=CAPTURE)


@pytest.mark.parametrize("payload", [b"\xff", b"{} trailing", b"[]", b"null", b'{"data":{},"data":{}}', "{}", bytearray(b"{}")])
def test_invalid_payload_bytes_fail_closed(payload):
    with pytest.raises(ValueError):
        inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333, captured_at_utc=CAPTURE)


def test_zero_goals_and_epoch_are_not_missing(document):
    document["data"].update(starting_at_timestamp=0, starting_at="1970-01-01 00:00:00")
    document["data"].pop("periods")
    for row in document["data"]["scores"]:
        row["score"]["goals"] = 0
    result = parse(document)
    assert result["trainable"] is True
    assert result["regular_time_home_goals"] == result["regular_time_away_goals"] == 0
    assert result["kickoff_at_utc"] == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_named_provider_times_and_version_fields_never_gain_semantics(document):
    fake = "INVENTED_UNTRUSTED_TIME_OR_VERSION"
    document["response_headers"] = {"Date": fake, "Last-Modified": fake}
    document["subscription"] = [{"updated_at": fake}]
    document["data"].update(**{field: fake for field in (*UNKNOWN_FIELDS, "published_at", "finalized_at", "version_id", "lastModified", "last_played_at", "standingsRecalculated", "standings_recalculated_at", "updated_at", "captured_at_utc")})
    document["data"]["nested/~"] = [{"published_at": fake}]
    result = parse(document)
    assert result["trainable"] is True
    assert all(result[field] is None for field in UNKNOWN_FIELDS)
    assert fake not in json.dumps(result, default=str)
    assert result["field_evidence"]["capture"]["value"] == CAPTURE.isoformat()
    document["data"]["periods"] = [document["data"]["periods"][0]]
    assert parse(document)["sporting_period_end_at_utc"] is None


def test_same_original_bytes_for_metadata_and_results_no_io_or_strict_admission(document, monkeypatch):
    payload = json.dumps(document).encode()
    def forbidden(*args, **kwargs):
        pytest.fail("observed parser must not perform I/O or strict admission")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(training_evidence, "verified_provider_fields", forbidden)
    monkeypatch.setattr(training_evidence, "LocalTrainingEvidence", forbidden)
    monkeypatch.setattr(inspect_sportmonks_sample, "inspect_fixture_response", forbidden)
    with monkeypatch.context() as offline:
        offline.setattr(builtins, "open", forbidden)
        offline.setattr(os, "getenv", forbidden)
        offline.setattr(os, "environ", None)
        first = inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333, captured_at_utc=CAPTURE)
        second = inspect_observed_fixture(payload, expected_league_id=222, expected_season_id=333, captured_at_utc=CAPTURE)
    assert first == second and first["trainable"] is True
    assert first["field_evidence"]["raw_payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert not {"strict_training_admission", "future_prediction_snapshot_eligibility"} & first.keys()


def test_existing_diagnostic_admission_flags_are_unchanged(document):
    before = inspect_sportmonks_sample.inspect_fixture_response(json.dumps(document).encode(), received_at_utc=CAPTURE, expected_league_id=222, expected_season_id=333)
    assert parse(document)["trainable"] is True
    after = inspect_sportmonks_sample.inspect_fixture_response(json.dumps(document).encode(), received_at_utc=CAPTURE, expected_league_id=222, expected_season_id=333)
    assert before == after
    assert before["strict_training_admission"] is False
    assert before["future_prediction_snapshot_eligibility"] is False
    assert "SHARED_METADATA_RESULT_CAPTURE_SYSTEM_LIMIT" in before["blockers"]
