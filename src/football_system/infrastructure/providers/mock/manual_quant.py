from __future__ import annotations

from football_system.application.ports.data_providers import (
    ManualQuantBatch,
    ManualQuantProvider,
    SnapshotQuery,
)
from football_system.infrastructure.providers.mock.dataset import MockDataset


class MockManualQuantProvider(ManualQuantProvider):
    def __init__(self, dataset: MockDataset) -> None:
        self._dataset = dataset

    async def fetch_manual_quant(self, query: SnapshotQuery) -> ManualQuantBatch:
        return ManualQuantBatch(
            inputs=self._dataset.manual_quant_inputs(query.match_ids, query.as_of_at_utc)
        )
