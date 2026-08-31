from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN

from football_system.domain.market import ThreeWayMarketOdds, ThreeWayProbability

PROBABILITY_QUANTUM = Decimal("0.000000000001")
METRIC_QUANTUM = Decimal("0.00000001")


def normalized_inverse_probability(
    odds: ThreeWayMarketOdds,
) -> tuple[ThreeWayProbability, Decimal]:
    inverse_home = Decimal(1) / odds.home_win
    inverse_draw = Decimal(1) / odds.draw
    inverse_away = Decimal(1) / odds.away_win
    raw_overround = inverse_home + inverse_draw + inverse_away
    overround = quantize_probability(raw_overround)
    return (
        quantize_three_way_probability(
            ThreeWayProbability(
                home_win=inverse_home / raw_overround,
                draw=inverse_draw / raw_overround,
                away_win=inverse_away / raw_overround,
            )
        ),
        overround,
    )


def selection_ev(probability: Decimal, fixed_bonus: Decimal) -> Decimal:
    return quantize_metric(probability * fixed_bonus - Decimal(1))


def quantize_probability(value: Decimal) -> Decimal:
    return value.quantize(PROBABILITY_QUANTUM, rounding=ROUND_HALF_EVEN)


def quantize_metric(value: Decimal) -> Decimal:
    return value.quantize(METRIC_QUANTUM, rounding=ROUND_HALF_EVEN)


def quantize_three_way_probability(
    probabilities: ThreeWayProbability,
) -> ThreeWayProbability:
    values = {
        "home_win": quantize_probability(probabilities.home_win),
        "draw": quantize_probability(probabilities.draw),
        "away_win": quantize_probability(probabilities.away_win),
    }
    residual = Decimal(1) - sum(values.values(), Decimal(0))
    largest = max(values, key=values.__getitem__)
    values[largest] += residual
    return ThreeWayProbability(**values)
