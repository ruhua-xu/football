"""Explicit fixture-only mappings. No HTTP or guesses about real provider coverage."""

import hashlib
from typing import Literal

from pydantic import Field

from football_system.application.review_v4 import parse_v4_json
from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_v2 import (
    MarketKeyV2,
    MarketOddsSnapshotV2,
    MarketPriceV1,
    MultiMarketModel,
    Price,
    SportteryFixedBonusSnapshotV2,
)
from football_system.domain.offline_market_source import OfflineMarketSourceV1

MARKET_MAPPING = {
    "FT_1X2": "THREE_WAY",
    "FT_HOME_HANDICAP_1X2": "HANDICAP_THREE_WAY",
    "FT_EXACT_GOALS_0_7_PLUS": "TOTAL_GOALS",
    "FT_CORRECT_SCORE_31": "CORRECT_SCORE",
}


class FixturePriceV1(MultiMarketModel):
    label: str = Field(min_length=1, max_length=64)
    price: Price


class ExactMarketFixtureV1(MultiMarketModel):
    schema_version: Literal["EXACT_MARKET_FIXTURE_V1"] = "EXACT_MARKET_FIXTURE_V1"
    classification: Literal["SYNTHETIC_ACCEPTANCE_DATA"] = "SYNTHETIC_ACCEPTANCE_DATA"
    match_id: Identifier
    provider_code: Literal["SYNTHETIC_EXACT_MARKET_V1"] = "SYNTHETIC_EXACT_MARKET_V1"
    source_identity: Identifier
    source_reference: Identifier
    bookmaker_code: Identifier
    kind: Literal["BOOKMAKER", "SPORTTERY"]
    market_code: str = Field(min_length=1, max_length=80)
    home_handicap: int | None = Field(default=None, strict=True)
    prices: tuple[FixturePriceV1, ...] = Field(max_length=31)
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    sale_status: Literal["OPEN", "CLOSED", "UNKNOWN"] = "UNKNOWN"


def fixture_label(outcome):
    simple = {
        "HOME_WIN": "H",
        "DRAW": "D",
        "AWAY_WIN": "A",
        "HOME_OTHER": "H_OTHER",
        "DRAW_OTHER": "D_OTHER",
        "AWAY_OTHER": "A_OTHER",
        "GOALS_7_PLUS": "7+",
    }
    if outcome.value in simple:
        return simple[outcome.value]
    if outcome.value.startswith("SCORE_"):
        return outcome.value[6:].replace("_", ":")
    return outcome.value[6:]


def parse_exact_market_fixture(raw):
    doc = ExactMarketFixtureV1.model_validate(parse_v4_json(raw))
    source = OfflineMarketSourceV1.freeze(
        source_reference=doc.source_reference,
        raw_json=raw.decode(),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )
    kind = MARKET_MAPPING.get(doc.market_code)
    if kind is None:
        return source, None, "MARKET_UNAVAILABLE_SEMANTICS_MISMATCH"
    market = MarketKeyV2(market_type=kind, home_handicap=doc.home_handicap)
    labels = tuple(p.label for p in doc.prices)
    expected = tuple(fixture_label(o) for o in market.catalog)
    if len(set(labels)) != len(labels) or set(labels) != set(expected):
        return source, None, "MARKET_UNAVAILABLE_INCOMPLETE_CATALOG"
    prices = {p.label: p.price for p in doc.prices}
    cls = (
        SportteryFixedBonusSnapshotV2
        if doc.kind == "SPORTTERY"
        else MarketOddsSnapshotV2
    )
    extra = {"sale_status": doc.sale_status} if doc.kind == "SPORTTERY" else {}
    snapshot = cls.freeze(
        match_id=doc.match_id,
        market_key=market,
        prices=tuple(
            MarketPriceV1(outcome=o, price=prices[fixture_label(o)])
            for o in market.catalog
        ),
        provider_code=doc.provider_code,
        source_identity=doc.source_identity,
        source_artifact_id=source.artifact_id,
        source_artifact_hash=source.content_hash,
        bookmaker_code="SPORTTERY" if doc.kind == "SPORTTERY" else doc.bookmaker_code,
        captured_at_utc=doc.captured_at_utc,
        available_at_utc=doc.available_at_utc,
        ingested_at_utc=doc.ingested_at_utc,
        **extra,
    )
    return source, snapshot, None
