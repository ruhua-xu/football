from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    Match,
    ProviderMatchMapping,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.prediction import ManualQuantInput
from football_system.domain.settlement import MatchResult


class FixtureQuery(DomainModel):
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    as_of_at_utc: UtcDateTime


class SnapshotQuery(DomainModel):
    match_ids: tuple[Identifier, ...]
    as_of_at_utc: UtcDateTime


class MatchResultQuery(DomainModel):
    match_ids: tuple[Identifier, ...] = Field(min_length=1)
    as_of_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_match_ids(self) -> MatchResultQuery:
        if len(self.match_ids) != len(set(self.match_ids)):
            raise ValueError("match result query IDs must be unique")
        return self


class FixtureBatch(DomainModel):
    competitions: tuple[Competition, ...]
    teams: tuple[Team, ...]
    matches: tuple[Match, ...]
    mappings: tuple[ProviderMatchMapping, ...]


class MarketOddsBatch(DomainModel):
    snapshots: tuple[MarketOddsSnapshot, ...]
    mappings: tuple[ProviderMatchMapping, ...]


class SportteryBatch(DomainModel):
    snapshots: tuple[SportteryBonusSnapshot, ...]
    mappings: tuple[ProviderMatchMapping, ...]


class ManualQuantBatch(DomainModel):
    inputs: tuple[ManualQuantInput, ...]


class MatchResultBatch(DomainModel):
    as_of_at_utc: UtcDateTime
    results: tuple[MatchResult, ...]
    mappings: tuple[ProviderMatchMapping, ...]

    @model_validator(mode="after")
    def validate_results(self) -> MatchResultBatch:
        result_ids = [result.match_result_id for result in self.results]
        match_ids = [result.match_id for result in self.results]
        mapping_ids = [mapping.mapping_id for mapping in self.mappings]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("match result IDs must be unique")
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("match result batch requires at most one result per match")
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError("match result mapping IDs must be unique")
        mapped_matches = {
            (mapping.provider_code, mapping.internal_match_id)
            for mapping in self.mappings
        }
        if any(
            (result.provider_code, result.match_id) not in mapped_matches
            for result in self.results
        ):
            raise ValueError("each match result requires a provider mapping")
        if any(
            result.available_at_utc > self.as_of_at_utc
            or result.ingested_at_utc > self.as_of_at_utc
            for result in self.results
        ) or any(
            mapping.available_at_utc > self.as_of_at_utc
            for mapping in self.mappings
        ):
            raise ValueError("match result batch crosses its knowledge cutoff")
        return self


@runtime_checkable
class FixtureProvider(Protocol):
    async def fetch_fixtures(self, query: FixtureQuery) -> FixtureBatch: ...


@runtime_checkable
class MarketOddsProvider(Protocol):
    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch: ...


@runtime_checkable
class SportteryProvider(Protocol):
    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch: ...


@runtime_checkable
class ManualQuantProvider(Protocol):
    async def fetch_manual_quant(self, query: SnapshotQuery) -> ManualQuantBatch: ...


@runtime_checkable
class HistoricalDataProvider(Protocol):
    async def fetch_match_results(self, query: MatchResultQuery) -> MatchResultBatch: ...
