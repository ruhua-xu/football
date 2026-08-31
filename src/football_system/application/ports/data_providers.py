from __future__ import annotations

from typing import Protocol, runtime_checkable

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


class FixtureQuery(DomainModel):
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    as_of_at_utc: UtcDateTime


class SnapshotQuery(DomainModel):
    match_ids: tuple[Identifier, ...]
    as_of_at_utc: UtcDateTime


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
