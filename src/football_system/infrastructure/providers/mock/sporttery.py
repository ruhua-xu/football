from __future__ import annotations

from decimal import Decimal

from football_system.application.ports.data_providers import (
    SnapshotQuery,
    SportteryBatch,
    SportteryProvider,
)
from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType
from football_system.domain.match import (
    FixedBonusQuote,
    ProviderMatchMapping,
    SaleStatus,
    SportteryBonusSnapshot,
)
from football_system.infrastructure.providers.mock.dataset import (
    MockDataset,
    MockMatchSeed,
    payload_hash,
)


class MockSportteryProvider(SportteryProvider):
    provider_code = "MOCK_SPORTTERY"
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.MOCK,
        provider_code=provider_code,
        provenance="bundled deterministic mock Sporttery dataset",
        is_mock=True,
    )

    def __init__(self, dataset: MockDataset) -> None:
        self._dataset = dataset

    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch:
        selected = set(query.match_ids)
        seeds = tuple(
            seed
            for seed in self._dataset.matches
            if seed.match_id in selected
            and seed.sporttery_available_at_utc <= query.as_of_at_utc
        )
        market = MarketKey(market_type=MarketType.THREE_WAY)
        snapshots = tuple(_snapshot(seed, market) for seed in seeds)
        mappings = tuple(
            ProviderMatchMapping(
                mapping_id=stable_id(
                    "mapping", self.provider_code, seed.sporttery_match_no
                ),
                provider_code=self.provider_code,
                external_namespace="sporttery_match",
                external_match_id=seed.sporttery_match_no,
                internal_match_id=seed.match_id,
                confidence=Decimal(1),
                available_at_utc=seed.sporttery_available_at_utc,
            )
            for seed in seeds
        )
        return SportteryBatch(snapshots=snapshots, mappings=mappings)


def _snapshot(seed: MockMatchSeed, market: MarketKey) -> SportteryBonusSnapshot:
    digest = payload_hash(seed.sporttery_bonus.model_dump(mode="json"))
    version_key = (
        f"{seed.sporttery_match_no}:{market.canonical}:"
        f"{seed.sporttery_captured_at_utc.isoformat()}:{digest[:16]}"
    )
    return SportteryBonusSnapshot(
        snapshot_id=stable_id(
            "sporttery", MockSportteryProvider.provider_code, version_key
        ),
        match_id=seed.match_id,
        provider_code=MockSportteryProvider.provider_code,
        sporttery_match_no=seed.sporttery_match_no,
        market=market,
        quotes=tuple(
            FixedBonusQuote(selection=selection, fixed_bonus=bonus)
            for selection, bonus in seed.sporttery_bonus.items()
        ),
        sale_status=SaleStatus.OPEN,
        captured_at_utc=seed.sporttery_captured_at_utc,
        available_at_utc=seed.sporttery_available_at_utc,
        ingested_at_utc=seed.sporttery_available_at_utc,
        source_snapshot_key=version_key,
        payload_hash=digest,
    )
