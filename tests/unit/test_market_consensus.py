import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from football_system.application.market_consensus import (
    MARKET_CONSENSUS_MEDIAN_V1,
    ConsensusMarketOddsProvider,
    MarketConsensusError,
    market_movement_summary,
)
from football_system.application.ports.data_providers import (
    MarketOddsBatch,
    SnapshotQuery,
)
from football_system.domain.archive import canonical_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.match import (
    MarketOddsSnapshot,
    OddsQuote,
    ProviderMatchMapping,
)
from football_system.domain.services.probability import (
    normalized_inverse_probability,
    quantize_three_way_probability,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)


class StaticMarketProvider:
    def __init__(self, batch: MarketOddsBatch) -> None:
        self._batch = batch

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        return self._batch


def _snapshot(
    *,
    provider_code: str,
    bookmaker_code: str,
    version: str,
    home: str,
    draw: str,
    away: str,
    captured_at: datetime = NOW - timedelta(minutes=5),
    available_at: datetime = NOW - timedelta(minutes=4),
    ingested_at: datetime = NOW - timedelta(minutes=3),
    match_id: str = "match-1",
    market: MarketKey = MARKET,
) -> MarketOddsSnapshot:
    odds = ThreeWayMarketOdds(
        home_win=Decimal(home),
        draw=Decimal(draw),
        away_win=Decimal(away),
    )
    payload_hash = canonical_payload_sha256(odds)
    source_key = stable_id(
        "test-source",
        provider_code,
        bookmaker_code,
        version,
        payload_hash,
    )
    return MarketOddsSnapshot(
        snapshot_id=stable_id("test-snapshot", provider_code, bookmaker_code, version),
        match_id=match_id,
        provider_code=provider_code,
        bookmaker_code=bookmaker_code,
        market=market,
        quotes=tuple(
            OddsQuote(selection=selection, odds=value)
            for selection, value in odds.items()
        ),
        captured_at_utc=captured_at,
        available_at_utc=available_at,
        ingested_at_utc=ingested_at,
        source_snapshot_key=source_key,
        payload_hash=payload_hash,
    )


def _batch(*snapshots: MarketOddsSnapshot) -> MarketOddsBatch:
    mappings: dict[tuple[str, str], ProviderMatchMapping] = {}
    for snapshot in snapshots:
        scope = (snapshot.provider_code, snapshot.match_id)
        mappings.setdefault(
            scope,
            ProviderMatchMapping(
                mapping_id=stable_id("test-mapping", *scope),
                provider_code=snapshot.provider_code,
                external_namespace="test_event",
                external_match_id=stable_id("test-external", *scope),
                internal_match_id=snapshot.match_id,
                resolution_method="TEST_EXACT",
                confidence=Decimal(1),
                available_at_utc=snapshot.available_at_utc,
            ),
        )
    return MarketOddsBatch(snapshots=snapshots, mappings=tuple(mappings.values()))


def _query() -> SnapshotQuery:
    return SnapshotQuery(match_ids=("match-1",), as_of_at_utc=NOW)


def _expected_probability(
    snapshots: tuple[MarketOddsSnapshot, ...],
) -> ThreeWayProbability:
    probabilities = [
        normalized_inverse_probability(snapshot.three_way_odds())[0]
        for snapshot in snapshots
    ]
    medians: dict[SelectionKey, Decimal] = {}
    for selection in SelectionKey:
        values = sorted(
            probability.for_selection(selection) for probability in probabilities
        )
        middle = len(values) // 2
        medians[selection] = (
            values[middle]
            if len(values) % 2
            else (values[middle - 1] + values[middle]) / Decimal(2)
        )
    total = sum(medians.values(), Decimal(0))
    return quantize_three_way_probability(
        ThreeWayProbability(
            home_win=medians[SelectionKey.HOME_WIN] / total,
            draw=medians[SelectionKey.DRAW] / total,
            away_win=medians[SelectionKey.AWAY_WIN] / total,
        )
    )


def test_consensus_uses_all_bookmakers_median_is_order_independent_and_traced() -> None:
    snapshots = (
        _snapshot(
            provider_code="FEED_A",
            bookmaker_code="alpha",
            version="1",
            home="2.10",
            draw="3.20",
            away="3.50",
        ),
        _snapshot(
            provider_code="FEED_B",
            bookmaker_code="beta",
            version="1",
            home="2.30",
            draw="3.10",
            away="3.20",
        ),
        _snapshot(
            provider_code="FEED_C",
            bookmaker_code="gamma",
            version="1",
            home="1.98",
            draw="3.45",
            away="3.85",
        ),
    )
    provider = ConsensusMarketOddsProvider((StaticMarketProvider(_batch(*snapshots)),))
    reversed_provider = ConsensusMarketOddsProvider(
        (StaticMarketProvider(_batch(*reversed(snapshots))),)
    )

    result = asyncio.run(provider.fetch_market_odds(_query()))
    reversed_result = asyncio.run(reversed_provider.fetch_market_odds(_query()))

    assert len(result.snapshots) == len(result.mappings) == 1
    assert result.snapshots == reversed_result.snapshots
    snapshot = result.snapshots[0]
    assert snapshot.provider_code == MARKET_CONSENSUS_MEDIAN_V1
    assert snapshot.bookmaker_code == MARKET_CONSENSUS_MEDIAN_V1
    assert result.mappings[0].provider_code == MARKET_CONSENSUS_MEDIAN_V1
    assert normalized_inverse_probability(snapshot.three_way_odds())[
        0
    ] == _expected_probability(snapshots)
    lineage = provider.lineage_by_snapshot_id[snapshot.snapshot_id]
    assert lineage.source_snapshot_key == snapshot.source_snapshot_key
    assert tuple(item.snapshot_id for item in lineage.constituents) == tuple(
        sorted(item.snapshot_id for item in snapshots)
    )
    assert tuple(item.payload_hash for item in lineage.constituents) == tuple(
        item.payload_hash
        for item in sorted(snapshots, key=lambda item: item.snapshot_id)
    )


def test_consensus_even_median_and_latest_bookmaker_version_are_deterministic() -> None:
    older_alpha = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="older",
        home="2.50",
        draw="3.00",
        away="3.00",
    )
    newer_alpha = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="newer",
        home="2.00",
        draw="3.30",
        away="3.70",
        captured_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        ingested_at=NOW,
    )
    beta = _snapshot(
        provider_code="FEED_B",
        bookmaker_code="beta",
        version="1",
        home="2.20",
        draw="3.10",
        away="3.40",
    )
    provider = ConsensusMarketOddsProvider(
        (StaticMarketProvider(_batch(older_alpha, newer_alpha, beta)),)
    )

    result = asyncio.run(provider.fetch_market_odds(_query()))

    assert normalized_inverse_probability(result.snapshots[0].three_way_odds())[
        0
    ] == _expected_probability((newer_alpha, beta))
    assert len(provider.lineages[0].constituents) == 2


def test_consensus_refuses_conflicting_versions_and_mappings() -> None:
    first = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="one",
        home="2.10",
        draw="3.20",
        away="3.50",
    )
    conflicting = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="two",
        home="2.20",
        draw="3.20",
        away="3.50",
    )
    provider = ConsensusMarketOddsProvider(
        (StaticMarketProvider(_batch(first, conflicting)),)
    )
    with pytest.raises(MarketConsensusError, match="conflicting duplicate bookmaker"):
        asyncio.run(provider.fetch_market_odds(_query()))

    mapping_a = _batch(first).mappings[0]
    mapping_b = mapping_a.model_copy(
        update={"mapping_id": "different", "external_match_id": "different"}
    )
    conflict_batch = MarketOddsBatch(snapshots=(first,), mappings=(mapping_b,))
    mapping_provider = ConsensusMarketOddsProvider(
        (StaticMarketProvider(_batch(first)), StaticMarketProvider(conflict_batch))
    )
    with pytest.raises(
        MarketConsensusError, match="conflicting duplicate provider mappings"
    ):
        asyncio.run(mapping_provider.fetch_market_odds(_query()))

    bad_hash = first.model_copy(update={"payload_hash": "0" * 64})
    hash_provider = ConsensusMarketOddsProvider(
        (StaticMarketProvider(_batch(bad_hash)),)
    )
    with pytest.raises(MarketConsensusError, match="payload hash"):
        asyncio.run(hash_provider.fetch_market_odds(_query()))


def test_consensus_has_no_output_for_missing_or_future_constituents() -> None:
    unsupported = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="unsupported",
        home="2.10",
        draw="3.20",
        away="3.50",
        market=MarketKey(
            market_type=MarketType.HANDICAP_THREE_WAY,
            handicap_value=Decimal("1"),
        ),
    )
    future = _snapshot(
        provider_code="FEED_B",
        bookmaker_code="beta",
        version="future",
        home="2.10",
        draw="3.20",
        away="3.50",
        captured_at=NOW + timedelta(minutes=1),
        available_at=NOW + timedelta(minutes=2),
        ingested_at=NOW + timedelta(minutes=3),
    )
    provider = ConsensusMarketOddsProvider(
        (StaticMarketProvider(_batch(unsupported, future)),)
    )

    result = asyncio.run(provider.fetch_market_odds(_query()))

    assert result.snapshots == ()
    assert result.mappings == ()
    assert provider.lineages == ()


def test_market_movement_reports_real_timepoints_and_not_single_snapshot() -> None:
    opening = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="opening",
        home="2.10",
        draw="3.20",
        away="3.50",
        captured_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=2),
        ingested_at=NOW - timedelta(hours=2),
    )
    latest = _snapshot(
        provider_code="FEED_A",
        bookmaker_code="alpha",
        version="latest",
        home="2.30",
        draw="3.10",
        away="3.20",
        captured_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(hours=1),
        ingested_at=NOW - timedelta(hours=1),
    )

    summary = market_movement_summary((opening, latest))

    assert summary is not None
    assert summary.opening_odds == opening.three_way_odds()
    assert summary.latest_odds == latest.three_way_odds()
    assert summary.absolute_change.home_win == Decimal("0.20")
    assert summary.relative_change.home_win == Decimal("0.20") / Decimal("2.10")
    assert summary.bookmaker_count == 1
    assert market_movement_summary((opening,)) is None
