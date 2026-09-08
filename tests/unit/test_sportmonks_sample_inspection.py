"""Invented fixtures only; no real receipts, rights, accounts, or acquisition."""

import copy
import hashlib
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from scripts import inspect_sportmonks_sample as tool

NOW = datetime(2001, 2, 3, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp())


@pytest.fixture
def document():
    data = {"id": 101, "league_id": 201, "season_id": 301,
            "league": {"id": 201, "country": {"iso2": "DE"}, "type": "league", "sub_type": "domestic"},
             "season": {"id": 301, "league_id": 201, "finished": True},
             "state_id": 5, "state": {"id": 5, "state": "FT", "short_name": "FT", "developer_name": "FT"},
            "starting_at": "2001-02-03 00:00:00", "starting_at_timestamp": STAMP,
            "participants": [{"id": pid, "meta": {"location": side}} for pid, side in ((401, "home"), (402, "away"))]}
    data["scores"] = [{"id": 501 + i * 2 + j, "fixture_id": 101, "participant_id": 401 + j,
                       "type_id": tid, "description": tool.SCORE_TYPES[tid],
                       "score": {"goals": goals[j], "participant": ("home", "away")[j]}}
                      for i, (tid, goals) in enumerate(((1, (1, 0)), (2, (3, 0)), (1525, (5, 1)), (48996, (2, 0)))) for j in range(2)]
    data["periods"] = [{"id": 601 + i, "fixture_id": 101, "type_id": tid, "started": STAMP + i * 100,
                        "ended": STAMP + i * 100 + 90, "type": {"id": tid, "model_type": "period"},
                        "period_length": 999, "minutes": 888, "time_added": 777} for i, tid in enumerate((1, 2, 3, 5))]
    return {"timezone": "UTC", "data": data, "subscription": [{"account_name": "PRIVATE_SENTINEL"}]}


def inspect(document):
    return tool.inspect_fixture_response(json.dumps(document).encode(), received_at_utc=NOW + timedelta(days=1),
                                         expected_league_id=201, expected_season_id=301)


def replace(document, path, value):
    parts = path.split("/")
    for key in parts[:-1]:
        document = document[int(key)] if isinstance(document, list) else document[key]
    if value == "MISSING":
        del document[parts[-1]]
    else:
        document[parts[-1]] = value


@pytest.mark.parametrize("reverse_participants,reverse_scores", [(False, False), (True, False), (False, True), (True, True)])
def test_joins_actual_pointers_and_distinct_score_semantics(document, reverse_participants, reverse_scores):
    for name, reverse in (("participants", reverse_participants), ("scores", reverse_scores), ("periods", True)):
        if reverse:
            document["data"][name].reverse()
    result = inspect(document)
    assert result["participants"]["home"]["id_pointer"] == f"/data/participants/{int(reverse_participants)}/id"
    assert result["normalized_identity"]["provider_fixture_key"] == "101"
    assert result["normalized_identity"]["provider_home_team_id"] == "401"
    assert result["state"]["semantics"] == "PROVIDER_REPORTED_FT_AT_CAPTURE"
    assert result["regular_time_score"]["home"]["goals"]["value"] == 3
    assert result["scores"]["CURRENT"]["home"]["goals"]["value"] == 5
    assert result["scores"]["2ND_HALF_ONLY"]["home"]["goals"]["value"] == 2
    for row in document["data"]["scores"]:
        found = result["scores"][row["description"]][row["score"]["participant"]]
        assert found["goals"]["pointer"] == f'/data/scores/{document["data"]["scores"].index(row)}/score/goals'
    assert result["periods"][0]["ended"]["pointer"] == "/data/periods/0/ended"
    assert {d["code"] for d in result["diagnostics"]} == {"CURRENT_DIFFERS_FROM_REGULAR"}


def test_no_cumulative_fallback_and_unknown_extra_types(document):
    document["data"]["scores"] = [r for r in document["data"]["scores"] if r["type_id"] != 2]
    extra = {**document["data"]["scores"][0], "id": 999, "type_id": 98765, "description": "SYNTHETIC_OTHER"}
    document["data"]["scores"].append(extra)
    result = inspect(document)
    assert result["regular_time_score"]["home"]["presence"] == "MISSING"
    assert result["extra_scores"][0]["type_id"] == 98765
    assert result["extra_scores"][0]["semantics"] == "UNKNOWN_SEMANTICS"
    assert "REGULAR_TIME_SCORE_INCOMPLETE" in result["blockers"]


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_joins_rejected(document, conflicting):
    duplicate = copy.deepcopy(document["data"]["scores"][0])
    duplicate["id"] = 999
    duplicate["score"]["goals"] += int(conflicting)
    document["data"]["scores"].append(duplicate)
    with pytest.raises(ValueError, match="DUPLICATE_SCORE"):
        inspect(document)


@pytest.mark.parametrize("path", ["id", "league_id", "season_id", "league/id", "season/id", "season/league_id",
                                 "participants/0/id", "scores/0/id", "scores/0/fixture_id", "scores/0/participant_id",
                                 "scores/0/type_id", "periods/0/id", "periods/0/fixture_id", "periods/0/type_id", "periods/0/type/id",
                                 "scores/0/score/goals", "periods/0/started", "periods/0/ended", "starting_at_timestamp"])
@pytest.mark.parametrize("bad", [True, 1.5, -1, "1"])
def test_ids_goals_and_times_are_strict_integers(document, path, bad):
    replace(document, "data/" + path, bad)
    with pytest.raises(ValueError):
        inspect(document)


@pytest.mark.parametrize("value,presence", [("MISSING", "MISSING"), (None, "NULL"), (0, "VALUE")])
def test_missing_null_zero_and_no_period_end_derivation(document, value, presence):
    for path in ("scores/0/score/goals", "periods/0/started", "periods/0/ended"):
        replace(document, "data/" + path, value)
    result = inspect(document)
    for fact in (result["scores"]["1ST_HALF"]["home"]["goals"], result["periods"][0]["started"], result["periods"][0]["ended"]):
        assert fact["presence"] == presence
        assert ("value" in fact) == (presence != "MISSING")
        if presence != "MISSING":
            assert fact["value"] == value


@pytest.mark.parametrize("path,bad", [("timezone", "Europe/Berlin"), ("data/starting_at", "2001-02-03 01:00:00"),
    ("data/starting_at", "2001-02-03T00:00:00Z"), ("data/starting_at_timestamp", STAMP * 1000),
    ("data/periods/0/ended", STAMP * 1000), ("data/scores/0/type_id", 2), ("data/scores/0/type_id", 98765),
    ("data/scores/0/description", "OTHER"), ("data/periods/0/type/id", 2), ("data/periods/0/type/model_type", "score"),
    ("data/participants/1/meta/location", "home"), ("data/participants/1/id", 401), ("data/season/finished", 1),
    ("data/league/country/iso2", "ZZ"), ("data/season/league_id", 202), ("data/scores/0/fixture_id", 102),
    ("data/scores/0/score/participant", "away"), ("data", []), ("data/scores", False), ("data/id", 0),
    ("data/participants", []), ("data/league/sub_type", "cup")])
def test_shape_scope_type_and_kickoff_disagreement_rejected(document, path, bad):
    replace(document, path, bad)
    with pytest.raises(ValueError):
        inspect(document)


def test_ends_receipt_and_suspect_keys_never_prove_publication(document):
    assert inspect(document)["potential_publication_version_fields"] == []
    document["data"].update(last_played_at="PRIVATE_SENTINEL", standings_recalculated_at="PRIVATE_SENTINEL",
                            **{"nested/~": [{"resultVersion": "PRIVATE_SENTINEL", "published_at": None}]})
    document["data"]["periods"][0]["ended"] = STAMP + 150
    result = inspect(document)
    assert result["periods"][0]["ended"]["value"] == STAMP + 150
    assert any(d["code"] == "HALF_INTERVAL_OVERLAP" for d in result["diagnostics"])
    assert result["time_facts"]["provider_finalization"] == {"status": "UNPROVEN"}
    assert result["time_facts"]["result_version_publication"] == {"status": "UNPROVEN"}
    assert result["time_facts"]["recorded_local_receipt"]["semantics"] == tool.LOCAL_RECEIPT
    assert result["potential_publication_version_fields"] == [{"pointer": "/data/nested~1~0/0/" + key, "present": True,
                                                              "semantics": "UNKNOWN_SEMANTICS"} for key in ("published_at", "resultVersion")]
    assert result["capture_classification"] == "SHARED_METADATA_RESULT_CAPTURE"
    assert result["capture_limit_classification"] == "CURRENT_SYSTEM_LIMIT_NOT_PROVIDER_DISQUALIFICATION"
    assert not result["strict_training_admission"] and not result["future_prediction_snapshot_eligibility"]
    assert len(result["blockers"]) == 4 and "PRIVATE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("path,bad", [("state_id", True), ("state/id", 8),
    ("state/developer_name", "AET"), ("periods/0/ticking", 0), ("periods/0/has_timer", "false"),
    ("periods/0/type/developer_name", "2ND_HALF")])
def test_state_and_period_flags_are_consistent(document, path, bad):
    replace(document, "data/" + path, bad)
    with pytest.raises(ValueError):
        inspect(document)


@pytest.mark.parametrize("state", [None, "MISSING"])
def test_missing_state_is_not_inferred_from_scores(document, state):
    replace(document, "data/state", state)
    result = inspect(document)
    assert result["state"]["semantics"] == "NOT_ESTABLISHED"
    assert "REGULAR_TIME_FINAL_STATE_NOT_ESTABLISHED" in result["blockers"]


def test_account_update_fields_are_not_fixture_publication_candidates(document):
    document["subscription"][0]["updated_at"] = "PRIVATE_SENTINEL"
    assert inspect(document)["potential_publication_version_fields"] == []


@pytest.fixture
def local_sample(tmp_path, monkeypatch, document):
    monkeypatch.setattr(tool, "WORKSPACE", tmp_path)
    root = tmp_path / "data" / "research" / "sample"
    (root / "raw").mkdir(parents=True)
    payload = json.dumps(document).encode()
    audit = dict(provider="SYNTHETIC", endpoint="/synthetic", requested_at_utc=NOW.isoformat(),
                 received_at_utc=NOW.isoformat(), available_at_utc=NOW.isoformat(), duration_ms=0, http_status=200, outcome="SUCCESS")
    digest = hashlib.sha256(payload).hexdigest()
    metadata = json.dumps({**audit, "payload_sha256": digest}).encode()
    stem = hashlib.sha256(metadata).hexdigest()
    (root / "raw" / (stem + ".raw")).write_bytes(payload)
    (root / "raw" / (stem + ".metadata.json")).write_bytes(metadata)
    receipt = dict(audit=audit, payload_sha256=digest, payload_bytes=len(payload), available_at_semantics=tool.LOCAL_RECEIPT,
                   raw_path="raw\\" + stem + ".raw", metadata_path="raw\\" + stem + ".metadata.json",
                   response_headers={"Date": "PRIVATE_SENTINEL", "Last-Modified": "PRIVATE_SENTINEL"})
    (root / "sample.json").write_text(json.dumps(receipt))
    return root, receipt, ["--evidence-root", str(root), "--receipt", "sample.json", "--league-id", "201", "--season-id", "301", "--output-relative", "inspection.json"]


def test_cli_offline_integrity_private_output_and_no_overwrite(local_sample, monkeypatch, capsys):
    root, receipt, args = local_sample
    def forbidden(*args, **kwargs):
        pytest.fail("network or environment access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(tool.os, "getenv", forbidden)
    originals = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with monkeypatch.context() as offline:
        offline.setattr(tool.os, "environ", None)
        status = tool.main(args)
    assert status == 0
    output = (root / "inspection.json").read_bytes()
    summary = json.loads(capsys.readouterr().out)
    assert summary["fixture_count"] == 1 and summary["http_requests_made"] == 0
    assert summary["output_sha256"] == hashlib.sha256(output).hexdigest()
    assert "PRIVATE_SENTINEL" not in output.decode() and "fixtures" not in summary
    assert json.loads(output)["fixtures"][0]["evidence"]["payload_sha256"] == receipt["payload_sha256"]
    assert tool.main(args) == 2 and json.loads(capsys.readouterr().out)["status"] == "REJECTED"
    assert (root / "inspection.json").read_bytes() == output
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert not list(root.glob(".inspection-*"))


@pytest.mark.parametrize("tamper", ["raw", "metadata", "audit", "hash", "filename", "length", "semantics", "traversal", "outside", "four"])
def test_cli_rejects_tampering_and_uncontained_paths(local_sample, capsys, tamper):
    root, receipt, args = local_sample
    if tamper in ("raw", "metadata"):
        with root.joinpath(*receipt[tamper + "_path"].replace("\\", "/").split("/")).open("ab") as stream:
            stream.write(b" ")
    elif tamper == "audit":
        receipt["audit"]["duration_ms"] = 1
    elif tamper == "four":
        args += ["--receipt", "other.json"] * 3
    elif tamper == "outside":
        args[-1] = "../outside.json"
    else:
        key, value = {"hash": ("payload_sha256", "0" * 64), "filename": ("raw_path", "wrong.raw"),
                      "length": ("payload_bytes", True), "semantics": ("available_at_semantics", "PUBLICATION"),
                      "traversal": ("raw_path", "../outside.raw")}[tamper]
        receipt[key] = value
    (root / "sample.json").write_text(json.dumps(receipt))
    assert tool.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"
    assert not (root / "inspection.json").exists()
