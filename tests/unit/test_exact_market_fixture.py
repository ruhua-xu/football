import json

import pytest

from football_system.domain.market_analysis import MarketMatchIdentityV1
from football_system.domain.market_v2 import MarketKeyV2
from football_system.infrastructure.providers.exact_market_fixture import (
    parse_exact_market_fixture,
)
from scripts.market_expansion_acceptance import fixture_book, KICKOFF


def fixture(kind):
    identity = MarketMatchIdentityV1(
        match_id="test",
        competition_id="test-league",
        season_id="2026",
        home_team_id="h",
        away_team_id="a",
        home_team_name="H",
        away_team_name="A",
        kickoff_at_utc=KICKOFF,
    )
    key = MarketKeyV2(
        market_type=kind, home_handicap=-1 if kind == "HANDICAP_THREE_WAY" else None
    )
    return json.loads(
        fixture_book(
            identity,
            key,
            kind="BOOKMAKER",
            prices={o.value: "2.100000" for o in key.catalog},
        )
    )


@pytest.mark.parametrize(
    "kind", ["THREE_WAY", "HANDICAP_THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE"]
)
def test_exact_semantics_complete_catalog_decimal_string_roundtrip(kind):
    raw = json.dumps(fixture(kind)).encode()
    source, snapshot, reason = parse_exact_market_fixture(raw)
    assert reason is None and str(snapshot.prices[0].price) == "2.100000"
    assert source.raw_json.encode() == raw
    assert parse_exact_market_fixture(raw) == (source, snapshot, reason)
    document = fixture(kind)
    document["prices"].reverse()
    reordered = parse_exact_market_fixture(json.dumps(document).encode())[1]
    assert reordered.prices == snapshot.prices
    if kind == "HANDICAP_THREE_WAY":
        assert snapshot.market_key.home_handicap == -1


@pytest.mark.parametrize("code", ["OU_2.5", "ASIAN_SPREAD_TWO_WAY", "HALF_FULL"])
def test_adjacent_market_semantics_cannot_be_substituted(code):
    doc = fixture("TOTAL_GOALS")
    doc["market_code"] = code
    _, snapshot, reason = parse_exact_market_fixture(json.dumps(doc).encode())
    assert snapshot is None and reason == "MARKET_UNAVAILABLE_SEMANTICS_MISMATCH"


@pytest.mark.parametrize("kind", ["THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE"])
def test_partial_and_duplicate_books_are_rejected_not_completed(kind):
    for duplicate in (False, True):
        doc = fixture(kind)
        doc["prices"].pop()
        if duplicate:
            doc["prices"].append(doc["prices"][0])
        _, snapshot, reason = parse_exact_market_fixture(json.dumps(doc).encode())
        assert snapshot is None and reason == "MARKET_UNAVAILABLE_INCOMPLETE_CATALOG"
