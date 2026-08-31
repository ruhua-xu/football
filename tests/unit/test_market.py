from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.domain.market import (
    MarketKey,
    MarketType,
    ProbabilityDistribution,
    ThreeWayProbability,
)


def test_market_key_distinguishes_handicap_lines() -> None:
    assert MarketKey(market_type=MarketType.THREE_WAY).canonical == "THREE_WAY"
    assert (
        MarketKey(
            market_type=MarketType.HANDICAP_THREE_WAY,
            handicap_value=Decimal("-1"),
        ).canonical
        == "HANDICAP_THREE_WAY:-1"
    )
    assert (
        MarketKey(
            market_type=MarketType.HANDICAP_THREE_WAY,
            handicap_value=Decimal("1"),
        ).canonical
        == "HANDICAP_THREE_WAY:+1"
    )


def test_handicap_market_requires_non_zero_line() -> None:
    with pytest.raises(ValidationError):
        MarketKey(market_type=MarketType.HANDICAP_THREE_WAY)
    with pytest.raises(ValidationError):
        MarketKey(
            market_type=MarketType.HANDICAP_THREE_WAY,
            handicap_value=Decimal(0),
        )


def test_probability_vector_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        ThreeWayProbability(
            home_win=Decimal("0.50"),
            draw=Decimal("0.30"),
            away_win=Decimal("0.30"),
        )


def test_three_way_distribution_round_trip() -> None:
    market = MarketKey(market_type=MarketType.THREE_WAY)
    probabilities = ThreeWayProbability(
        home_win=Decimal("0.50"),
        draw=Decimal("0.30"),
        away_win=Decimal("0.20"),
    )

    distribution = ProbabilityDistribution.from_three_way(market, probabilities)

    assert distribution.to_three_way() == probabilities
