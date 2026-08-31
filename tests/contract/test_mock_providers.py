import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from football_system.application.ports.data_providers import (
    FixtureProvider,
    FixtureQuery,
    ManualQuantProvider,
    MarketOddsProvider,
    SnapshotQuery,
    SportteryProvider,
)
from football_system.domain.market import MarketType
from football_system.infrastructure.providers.mock.dataset import MockDataset
from football_system.infrastructure.providers.mock.fixtures import MockFixtureProvider
from football_system.infrastructure.providers.mock.market_odds import MockMarketOddsProvider
from football_system.infrastructure.providers.mock.manual_quant import MockManualQuantProvider
from football_system.infrastructure.providers.mock.sporttery import MockSportteryProvider


def load_dataset() -> MockDataset:
    return MockDataset.from_json("data/fixtures/mvp_matches.json")


def test_fixture_provider_returns_six_normalized_matches() -> None:
    dataset = load_dataset()
    provider = MockFixtureProvider(dataset)
    assert isinstance(provider, FixtureProvider)

    batch = asyncio.run(
        provider.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=datetime(2026, 8, 2, tzinfo=timezone.utc),
                kickoff_to_utc=datetime(2026, 8, 3, tzinfo=timezone.utc),
                as_of_at_utc=dataset.as_of_at_utc,
            )
        )
    )

    assert len(batch.matches) == 6
    assert len(batch.teams) == 12
    assert len(batch.competitions) == 1
    assert len(batch.mappings) == 6
    assert len({match.match_id for match in batch.matches}) == 6
    assert all(match.kickoff_at_utc.tzinfo == timezone.utc for match in batch.matches)


def test_market_and_sporttery_providers_are_type_separated() -> None:
    dataset = load_dataset()
    odds_provider = MockMarketOddsProvider(dataset)
    sporttery_provider = MockSportteryProvider(dataset)
    assert isinstance(odds_provider, MarketOddsProvider)
    assert isinstance(sporttery_provider, SportteryProvider)
    match_ids = tuple(seed.match_id for seed in dataset.matches)
    query = SnapshotQuery(match_ids=match_ids, as_of_at_utc=dataset.as_of_at_utc)

    odds_batch = asyncio.run(odds_provider.fetch_market_odds(query))
    sporttery_batch = asyncio.run(sporttery_provider.fetch_fixed_bonus(query))

    assert len(odds_batch.snapshots) == 6
    assert len(sporttery_batch.snapshots) == 6
    assert len(odds_batch.mappings) == 6
    assert len(sporttery_batch.mappings) == 6
    assert all(
        snapshot.market.market_type == MarketType.THREE_WAY
        for snapshot in odds_batch.snapshots + sporttery_batch.snapshots
    )
    assert odds_batch.snapshots[0].three_way_odds().home_win > 1
    assert sporttery_batch.snapshots[0].three_way_bonus().home_win > 1
    assert odds_batch.snapshots[0].provider_code != sporttery_batch.snapshots[0].provider_code


def test_provider_respects_point_in_time_availability() -> None:
    dataset = load_dataset()
    provider = MockMarketOddsProvider(dataset)
    query = SnapshotQuery(
        match_ids=tuple(seed.match_id for seed in dataset.matches),
        as_of_at_utc=datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc),
    )

    batch = asyncio.run(provider.fetch_market_odds(query))

    assert batch.snapshots == ()


def test_manual_quant_inputs_are_frozen_at_cutoff() -> None:
    dataset = load_dataset()
    provider = MockManualQuantProvider(dataset)
    assert isinstance(provider, ManualQuantProvider)
    match_ids = tuple(seed.match_id for seed in dataset.matches)

    available = asyncio.run(
        provider.fetch_manual_quant(
            SnapshotQuery(match_ids=match_ids, as_of_at_utc=dataset.as_of_at_utc)
        )
    ).inputs
    unavailable = asyncio.run(
        provider.fetch_manual_quant(
            SnapshotQuery(
                match_ids=match_ids,
                as_of_at_utc=datetime(2026, 8, 1, 8, 59, tzinfo=timezone.utc),
            )
        )
    ).inputs

    assert len(available) == 6
    assert unavailable == ()
    assert all(item.market.market_type == MarketType.THREE_WAY for item in available)
    assert all(item.input_id and item.payload_hash for item in available)


def test_snapshot_identity_changes_when_payload_changes() -> None:
    dataset = load_dataset()
    first_seed = dataset.matches[0]
    changed_seed = first_seed.model_copy(
        update={
            "market_odds": first_seed.market_odds.model_copy(
                update={"home_win": Decimal("1.83")}
            )
        }
    )
    changed_dataset = dataset.model_copy(
        update={"matches": (changed_seed, *dataset.matches[1:])}
    )
    query = SnapshotQuery(
        match_ids=(first_seed.match_id,), as_of_at_utc=dataset.as_of_at_utc
    )
    original = asyncio.run(
        MockMarketOddsProvider(dataset).fetch_market_odds(query)
    ).snapshots[0]
    changed = asyncio.run(
        MockMarketOddsProvider(changed_dataset).fetch_market_odds(query)
    ).snapshots[0]

    assert original.snapshot_id != changed.snapshot_id
    assert original.source_snapshot_key != changed.source_snapshot_key
    assert original.payload_hash != changed.payload_hash
