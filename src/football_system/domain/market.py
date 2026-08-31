from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel

PROBABILITY_TOLERANCE = Decimal("0.000001")


class UnsupportedMarketError(ValueError):
    pass


class MarketType(StrEnum):
    THREE_WAY = "THREE_WAY"
    HANDICAP_THREE_WAY = "HANDICAP_THREE_WAY"
    CORRECT_SCORE = "CORRECT_SCORE"
    TOTAL_GOALS = "TOTAL_GOALS"
    HALF_FULL = "HALF_FULL"


class SelectionKey(StrEnum):
    HOME_WIN = "HOME_WIN"
    DRAW = "DRAW"
    AWAY_WIN = "AWAY_WIN"


THREE_WAY_SELECTIONS = frozenset(SelectionKey)


class MarketKey(DomainModel):
    market_type: MarketType
    handicap_value: Decimal | None = None

    @model_validator(mode="after")
    def validate_handicap(self) -> MarketKey:
        if self.market_type == MarketType.HANDICAP_THREE_WAY:
            if self.handicap_value is None or self.handicap_value == 0:
                raise ValueError("HANDICAP_THREE_WAY requires a non-zero handicap")
        elif self.handicap_value is not None:
            raise ValueError(f"{self.market_type} does not accept handicap_value")
        return self

    @property
    def canonical(self) -> str:
        if self.handicap_value is None:
            return self.market_type.value
        value = format(self.handicap_value.normalize(), "f")
        if self.handicap_value > 0:
            value = f"+{value}"
        return f"{self.market_type.value}:{value}"


class ThreeWayProbability(DomainModel):
    home_win: Decimal = Field(ge=0, le=1)
    draw: Decimal = Field(ge=0, le=1)
    away_win: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_total(self) -> ThreeWayProbability:
        total = self.home_win + self.draw + self.away_win
        if abs(total - Decimal(1)) > PROBABILITY_TOLERANCE:
            raise ValueError(f"probabilities must sum to 1, got {total}")
        return self

    def for_selection(self, selection: SelectionKey) -> Decimal:
        return {
            SelectionKey.HOME_WIN: self.home_win,
            SelectionKey.DRAW: self.draw,
            SelectionKey.AWAY_WIN: self.away_win,
        }[selection]

    def items(self) -> tuple[tuple[SelectionKey, Decimal], ...]:
        return tuple((selection, self.for_selection(selection)) for selection in SelectionKey)


class OutcomeProbability(DomainModel):
    selection: SelectionKey
    probability: Decimal = Field(ge=0, le=1)


class ProbabilityDistribution(DomainModel):
    market: MarketKey
    outcomes: tuple[OutcomeProbability, ...]

    @model_validator(mode="after")
    def validate_distribution(self) -> ProbabilityDistribution:
        selections = [outcome.selection for outcome in self.outcomes]
        if len(selections) != len(set(selections)):
            raise ValueError("probability selections must be unique")
        if self.market.market_type in {
            MarketType.THREE_WAY,
            MarketType.HANDICAP_THREE_WAY,
        } and set(selections) != THREE_WAY_SELECTIONS:
            raise ValueError("three-way markets require HOME_WIN, DRAW and AWAY_WIN")
        total = sum((outcome.probability for outcome in self.outcomes), Decimal(0))
        if abs(total - Decimal(1)) > PROBABILITY_TOLERANCE:
            raise ValueError(f"probabilities must sum to 1, got {total}")
        return self

    @classmethod
    def from_three_way(
        cls,
        market: MarketKey,
        probabilities: ThreeWayProbability,
    ) -> ProbabilityDistribution:
        return cls(
            market=market,
            outcomes=tuple(
                OutcomeProbability(selection=selection, probability=probability)
                for selection, probability in probabilities.items()
            ),
        )

    def to_three_way(self) -> ThreeWayProbability:
        values = {outcome.selection: outcome.probability for outcome in self.outcomes}
        if set(values) != THREE_WAY_SELECTIONS:
            raise ValueError("distribution is not a complete three-way market")
        return ThreeWayProbability(
            home_win=values[SelectionKey.HOME_WIN],
            draw=values[SelectionKey.DRAW],
            away_win=values[SelectionKey.AWAY_WIN],
        )


class ThreeWayMarketOdds(DomainModel):
    home_win: Decimal = Field(gt=1)
    draw: Decimal = Field(gt=1)
    away_win: Decimal = Field(gt=1)

    def for_selection(self, selection: SelectionKey) -> Decimal:
        return {
            SelectionKey.HOME_WIN: self.home_win,
            SelectionKey.DRAW: self.draw,
            SelectionKey.AWAY_WIN: self.away_win,
        }[selection]

    def items(self) -> tuple[tuple[SelectionKey, Decimal], ...]:
        return tuple((selection, self.for_selection(selection)) for selection in SelectionKey)


class ThreeWayFixedBonus(DomainModel):
    home_win: Decimal = Field(gt=1)
    draw: Decimal = Field(gt=1)
    away_win: Decimal = Field(gt=1)

    def for_selection(self, selection: SelectionKey) -> Decimal:
        return {
            SelectionKey.HOME_WIN: self.home_win,
            SelectionKey.DRAW: self.draw,
            SelectionKey.AWAY_WIN: self.away_win,
        }[selection]

    def items(self) -> tuple[tuple[SelectionKey, Decimal], ...]:
        return tuple((selection, self.for_selection(selection)) for selection in SelectionKey)
