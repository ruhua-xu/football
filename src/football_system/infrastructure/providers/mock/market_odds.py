from __future__ import annotations

from decimal import Decimal

from football_system.application.ports.data_providers import (
    MarketOddsBatch,
    MarketOddsProvider,
    SnapshotQuery,
)
from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType
from football_system.domain.match import (
    MarketOddsSnapshot,
    OddsQuote,
    ProviderMatchMapping,
)
from football_system.infrastructure.providers.mock.dataset import (
    MockDataset,
    MockMatchSeed,
    payload_hash,
)


class MockMarketOddsProvider(MarketOddsProvider):
    provider_code = "MOCK_MARKET_ODDS"
    bookmaker_code = "MOCK_CONSENSUS"
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.MOCK,
        provider_code=provider_code,
        provenance="bundled deterministic mock market dataset",
        is_mock=True,
    )

    def __init__(self, dataset: MockDataset) -> None:
        self._dataset = dataset

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        selected = set(query.match_ids)
        seeds = tuple(
            seed
            for seed in self._dataset.matches
            if seed.match_id in selected
            and seed.market_available_at_utc <= query.as_of_at_utc
        )
        market = MarketKey(market_type=MarketType.THREE_WAY)
        snapshots = tuple(_snapshot(seed, market) for seed in seeds)
        mappings = tuple(
            ProviderMatchMapping(
                mapping_id=stable_id(
                    "mapping", self.provider_code, seed.market_external_id
                ),
                provider_code=self.provider_code,
                external_namespace="market_event",
                external_match_id=seed.market_external_id,
                internal_match_id=seed.match_id,
                confidence=Decimal(1),
                available_at_utc=seed.market_available_at_utc,
            )
            for seed in seeds
        )
        return MarketOddsBatch(snapshots=snapshots, mappings=mappings)


def _snapshot(seed: MockMatchSeed, market: MarketKey) -> MarketOddsSnapshot:
    digest = payload_hash(seed.market_odds.model_dump(mode="json"))
    version_key = (
        f"{seed.market_external_id}:{market.canonical}:"
        f"{seed.market_captured_at_utc.isoformat()}:{digest[:16]}"
    )
    return MarketOddsSnapshot(
        snapshot_id=stable_id(
            "market-odds", MockMarketOddsProvider.provider_code, version_key
        ),
        match_id=seed.match_id,
        provider_code=MockMarketOddsProvider.provider_code,
        bookmaker_code=MockMarketOddsProvider.bookmaker_code,
        market=market,
        quotes=tuple(
            OddsQuote(selection=selection, odds=odds)
            for selection, odds in seed.market_odds.items()
        ),
        captured_at_utc=seed.market_captured_at_utc,
        available_at_utc=seed.market_available_at_utc,
        ingested_at_utc=seed.market_available_at_utc,
        source_snapshot_key=version_key,
        payload_hash=digest,
    )
