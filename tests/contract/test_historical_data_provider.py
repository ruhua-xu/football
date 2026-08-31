import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.application.ports.data_providers import (
    HistoricalDataProvider,
    MatchResultBatch,
    MatchResultQuery,
)
from football_system.domain.common import stable_id
from football_system.domain.match import ProviderMatchMapping
from football_system.domain.settlement import MatchResult

OBSERVED = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
AVAILABLE = OBSERVED + timedelta(minutes=1)
INGESTED = OBSERVED + timedelta(minutes=2)


def match_result(match_id: str = "match-1") -> MatchResult:
    return MatchResult(
        match_result_id=f"result-{match_id}",
        match_id=match_id,
        provider_code="HISTORICAL_TEST",
        home_goals=2,
        away_goals=1,
        observed_at_utc=OBSERVED,
        available_at_utc=AVAILABLE,
        ingested_at_utc=INGESTED,
        source_result_key=f"source-{match_id}",
        payload_hash=stable_id("result-payload", match_id),
    )


def mapping(match_id: str = "match-1") -> ProviderMatchMapping:
    return ProviderMatchMapping(
        mapping_id=f"mapping-{match_id}",
        provider_code="HISTORICAL_TEST",
        external_namespace="historical-test",
        external_match_id=f"external-{match_id}",
        internal_match_id=match_id,
        resolution_method="TEST_EXACT",
        confidence=Decimal(1),
        available_at_utc=OBSERVED - timedelta(days=1),
    )


class StubHistoricalDataProvider:
    def __init__(self, results: tuple[MatchResult, ...]) -> None:
        self._results = results

    async def fetch_match_results(self, query: MatchResultQuery) -> MatchResultBatch:
        requested = set(query.match_ids)
        results = tuple(
            result
            for result in self._results
            if result.match_id in requested
            and result.available_at_utc <= query.as_of_at_utc
            and result.ingested_at_utc <= query.as_of_at_utc
        )
        return MatchResultBatch(
            as_of_at_utc=query.as_of_at_utc,
            results=results,
            mappings=tuple(mapping(result.match_id) for result in results),
        )


def test_historical_data_provider_is_runtime_checkable_and_respects_cutoff() -> None:
    provider = StubHistoricalDataProvider((match_result(),))

    assert isinstance(provider, HistoricalDataProvider)

    unavailable = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(
                match_ids=("match-1",),
                as_of_at_utc=INGESTED - timedelta(seconds=1),
            )
        )
    )
    available = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(match_ids=("match-1",), as_of_at_utc=INGESTED)
        )
    )

    assert unavailable.results == ()
    assert len(available.results) == 1
    assert available.results[0].match_id == "match-1"


def test_match_result_query_requires_nonempty_unique_match_ids() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        MatchResultQuery(match_ids=(), as_of_at_utc=INGESTED)
    with pytest.raises(ValidationError, match="must be unique"):
        MatchResultQuery(
            match_ids=("match-1", "match-1"),
            as_of_at_utc=INGESTED,
        )


def test_match_result_batch_allows_partial_unique_coverage() -> None:
    batch = MatchResultBatch(
        as_of_at_utc=INGESTED,
        results=(match_result("match-1"),),
        mappings=(mapping("match-1"), mapping("match-2")),
    )

    assert tuple(result.match_id for result in batch.results) == ("match-1",)


def test_match_result_batch_rejects_future_or_unmapped_results() -> None:
    with pytest.raises(ValidationError, match="knowledge cutoff"):
        MatchResultBatch(
            as_of_at_utc=INGESTED - timedelta(seconds=1),
            results=(match_result(),),
            mappings=(mapping(),),
        )
    with pytest.raises(ValidationError, match="requires a provider mapping"):
        MatchResultBatch(
            as_of_at_utc=INGESTED,
            results=(match_result(),),
            mappings=(),
        )
    with pytest.raises(ValidationError, match="at most one result per match"):
        MatchResultBatch(
            as_of_at_utc=INGESTED,
            results=(
                match_result(),
                match_result().model_copy(update={"match_result_id": "result-v2"}),
            ),
            mappings=(mapping(),),
        )
