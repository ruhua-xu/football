from __future__ import annotations

from football_system.application.ports.data_providers import (
    ManualQuantBatch,
    ManualQuantProvider,
    SnapshotQuery,
)
from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.infrastructure.providers.mock.dataset import MockDataset


class MockManualQuantProvider(ManualQuantProvider):
    provider_code = "MOCK_MANUAL_QUANT"
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.MOCK,
        provider_code=provider_code,
        provenance="bundled deterministic mock manual quant dataset",
        is_mock=True,
    )

    def __init__(self, dataset: MockDataset) -> None:
        self._dataset = dataset

    async def fetch_manual_quant(self, query: SnapshotQuery) -> ManualQuantBatch:
        return ManualQuantBatch(
            provider_code=self.provider_code,
            inputs=self._dataset.manual_quant_inputs(
                query.match_ids, query.as_of_at_utc
            ),
        )
