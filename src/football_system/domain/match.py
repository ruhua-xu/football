from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import (
    MarketKey,
    SelectionKey,
    THREE_WAY_SELECTIONS,
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
)


class TeamType(StrEnum):
    CLUB = "CLUB"
    NATIONAL = "NATIONAL"
    WOMEN = "WOMEN"
    YOUTH = "YOUTH"
    RESERVE = "RESERVE"


class MatchStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"


class SaleStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    SUSPENDED = "SUSPENDED"


class Competition(DomainModel):
    competition_id: Identifier
    canonical_key: Identifier
    name: Identifier
    country_code: str = Field(min_length=2, max_length=3)


class Team(DomainModel):
    team_id: Identifier
    canonical_key: Identifier
    name: Identifier
    team_type: TeamType = TeamType.CLUB


class Match(DomainModel):
    match_id: Identifier
    competition_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    status: MatchStatus = MatchStatus.SCHEDULED
    available_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_teams(self) -> Match:
        if self.home_team_id == self.away_team_id:
            raise ValueError("home and away teams must differ")
        return self


class ProviderMatchMapping(DomainModel):
    mapping_id: Identifier
    provider_code: Identifier
    external_namespace: Identifier
    external_match_id: Identifier
    internal_match_id: Identifier
    resolution_method: str = "MOCK_EXACT"
    confidence: Decimal = Field(ge=0, le=1)
    available_at_utc: UtcDateTime


class OddsQuote(DomainModel):
    selection: SelectionKey
    odds: Decimal = Field(gt=1)


class FixedBonusQuote(DomainModel):
    selection: SelectionKey
    fixed_bonus: Decimal = Field(gt=1)


class MarketOddsSnapshot(DomainModel):
    snapshot_id: Identifier
    match_id: Identifier
    provider_code: Identifier
    bookmaker_code: Identifier
    market: MarketKey
    quotes: tuple[OddsQuote, ...]
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    source_snapshot_key: Identifier
    payload_hash: Identifier

    @model_validator(mode="after")
    def validate_quotes(self) -> MarketOddsSnapshot:
        selections = [quote.selection for quote in self.quotes]
        if len(selections) != len(set(selections)):
            raise ValueError("odds selections must be unique")
        if set(selections) != THREE_WAY_SELECTIONS:
            raise ValueError("MVP odds snapshot requires all three selections")
        return self

    def three_way_odds(self) -> ThreeWayMarketOdds:
        values = {quote.selection: quote.odds for quote in self.quotes}
        return ThreeWayMarketOdds(
            home_win=values[SelectionKey.HOME_WIN],
            draw=values[SelectionKey.DRAW],
            away_win=values[SelectionKey.AWAY_WIN],
        )


class SportteryBonusSnapshot(DomainModel):
    snapshot_id: Identifier
    match_id: Identifier
    provider_code: Identifier
    sporttery_match_no: Identifier
    market: MarketKey
    quotes: tuple[FixedBonusQuote, ...]
    sale_status: SaleStatus
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    source_snapshot_key: Identifier
    payload_hash: Identifier

    @model_validator(mode="after")
    def validate_quotes(self) -> SportteryBonusSnapshot:
        selections = [quote.selection for quote in self.quotes]
        if len(selections) != len(set(selections)):
            raise ValueError("fixed bonus selections must be unique")
        if set(selections) != THREE_WAY_SELECTIONS:
            raise ValueError("MVP fixed bonus snapshot requires all three selections")
        return self

    def three_way_bonus(self) -> ThreeWayFixedBonus:
        values = {quote.selection: quote.fixed_bonus for quote in self.quotes}
        return ThreeWayFixedBonus(
            home_win=values[SelectionKey.HOME_WIN],
            draw=values[SelectionKey.DRAW],
            away_win=values[SelectionKey.AWAY_WIN],
        )
