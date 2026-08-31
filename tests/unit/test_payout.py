from decimal import Decimal

import pytest

from football_system.domain.betting import SportteryRules
from football_system.domain.services.payout import (
    calculate_stake_fen,
    official_gross_payout_fen,
)


def rules(**overrides: int) -> SportteryRules:
    values = {
        "version": "TEST_V1",
        "base_stake_fen": 200,
        "max_multiplier": 50,
        "max_ticket_stake_fen": 600_000,
    }
    values.update(overrides)
    return SportteryRules(**values)


def test_official_rounding_uses_half_even() -> None:
    assert official_gross_payout_fen(
        (Decimal("1.65"), Decimal("1.75")), rules()
    ) == 578
    assert official_gross_payout_fen(
        (Decimal("1.25"), Decimal("1.97")), rules()
    ) == 492


def test_stake_is_derived_from_atomic_bets_and_multiplier() -> None:
    assert calculate_stake_fen(1, 1, rules()) == 200
    assert calculate_stake_fen(1, 50, rules()) == 10_000


def test_multiplier_and_ticket_stake_limits_are_enforced() -> None:
    with pytest.raises(ValueError):
        calculate_stake_fen(1, 51, rules())
    with pytest.raises(ValueError):
        calculate_stake_fen(
            4,
            4,
            rules(max_multiplier=10, max_ticket_stake_fen=3_000),
        )
    with pytest.raises(ValueError):
        calculate_stake_fen(1, True, rules())
