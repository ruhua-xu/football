from decimal import Decimal

from football_system.domain.market import ThreeWayMarketOdds
from football_system.domain.services.probability import (
    normalized_inverse_probability,
    selection_ev,
)


def test_normalized_inverse_probability_removes_overround() -> None:
    probabilities, overround = normalized_inverse_probability(
        ThreeWayMarketOdds(
            home_win=Decimal("2.00"),
            draw=Decimal("3.00"),
            away_win=Decimal("4.00"),
        )
    )

    assert overround == Decimal("1.083333333333")
    assert abs(probabilities.home_win - Decimal(6) / Decimal(13)) < Decimal("1e-12")
    assert abs(probabilities.draw - Decimal(4) / Decimal(13)) < Decimal("1e-12")
    assert abs(probabilities.away_win - Decimal(3) / Decimal(13)) < Decimal("1e-12")
    assert sum(probabilities.model_dump().values(), Decimal(0)) == Decimal(1)


def test_selection_ev_is_net_return_per_unit_stake() -> None:
    assert selection_ev(Decimal("0.60"), Decimal("1.80")) == Decimal("0.0800")
