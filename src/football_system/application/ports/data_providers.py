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
from football_system.domain.settlement import MatchResult, MatchSettlementIssue


class FixtureQuery(DomainModel):
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    as_of_at_utc: UtcDateTime


class SnapshotQuery(DomainModel):
    match_ids: tuple[Identifier, ...]
    as_of_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_match_ids(self) -> SnapshotQuery:
        if len(self.match_ids) != len(set(self.match_ids)):
            raise ValueError("snapshot query match IDs must be unique")
        return self


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

    @model_validator(mode="after")
    def validate_batch(self) -> FixtureBatch:
        _validate_unique(self.competitions, "competition_id", "competition")
        _validate_unique(self.teams, "team_id", "team")
        match_ids = _validate_unique(self.matches, "match_id", "fixture match")
        _validate_unique(self.mappings, "mapping_id", "fixture mapping")
        if any(mapping.internal_match_id not in match_ids for mapping in self.mappings):
            raise ValueError("fixture mapping references an unexpected match")
        return self


class MarketOddsBatch(DomainModel):
    snapshots: tuple[MarketOddsSnapshot, ...]
    mappings: tuple[ProviderMatchMapping, ...]

    @model_validator(mode="after")
    def validate_batch(self) -> MarketOddsBatch:
        _validate_unique(self.snapshots, "snapshot_id", "market odds snapshot")
        _validate_unique(self.mappings, "mapping_id", "market odds mapping")
        return self


class SportteryBatch(DomainModel):
    snapshots: tuple[SportteryBonusSnapshot, ...]
    mappings: tuple[ProviderMatchMapping, ...]

    @model_validator(mode="after")
    def validate_batch(self) -> SportteryBatch:
        _validate_unique(self.snapshots, "snapshot_id", "Sporttery snapshot")
        _validate_unique(self.mappings, "mapping_id", "Sporttery mapping")
        return self


class ManualQuantBatch(DomainModel):
    inputs: tuple[ManualQuantInput, ...]

    @model_validator(mode="after")
    def validate_batch(self) -> ManualQuantBatch:
        _validate_unique(self.inputs, "input_id", "manual quant input")
        return self


class MatchResultBatch(DomainModel):
    as_of_at_utc: UtcDateTime
    results: tuple[MatchResult, ...]
    mappings: tuple[ProviderMatchMapping, ...]
    issues: tuple[MatchSettlementIssue, ...] = ()

    @model_validator(mode="after")
    def validate_results(self) -> MatchResultBatch:
        result_ids = [result.match_result_id for result in self.results]
        match_ids = [result.match_id for result in self.results]
        issue_match_ids = [issue.match_id for issue in self.issues]
        mapping_ids = [mapping.mapping_id for mapping in self.mappings]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("match result IDs must be unique")
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("match result batch requires at most one result per match")
        if len(issue_match_ids) != len(set(issue_match_ids)):
            raise ValueError("match result issues must be unique by match")
        if set(match_ids) & set(issue_match_ids):
            raise ValueError("a match cannot have both a result and an issue")
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
        mapped_match_ids = {mapping.internal_match_id for mapping in self.mappings}
        if any(match_id not in mapped_match_ids for match_id in issue_match_ids):
            raise ValueError("each match result issue requires a provider mapping")
        if any(
            result.available_at_utc > self.as_of_at_utc
            or result.ingested_at_utc > self.as_of_at_utc
            for result in self.results
        ) or any(
            mapping.available_at_utc > self.as_of_at_utc for mapping in self.mappings
        ):
            raise ValueError("match result batch crosses its knowledge cutoff")
        return self


def _validate_unique(items: tuple, field: str, label: str) -> set[str]:
    identities = [getattr(item, field) for item in items]
    if len(identities) != len(set(identities)):
        raise ValueError(f"duplicate {label} IDs are not allowed")
    return set(identities)


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
    async def fetch_match_results(
        self, query: MatchResultQuery
    ) -> MatchResultBatch: ...
