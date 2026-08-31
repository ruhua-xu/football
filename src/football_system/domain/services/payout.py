from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from functools import reduce
from operator import mul
from typing import Iterable

from football_system.domain.betting import SportteryRules


def calculate_stake_fen(
    atomic_bet_count: int,
    multiplier: int,
    rules: SportteryRules,
) -> int:
    if isinstance(multiplier, bool) or not isinstance(multiplier, int):
        raise ValueError("multiplier must be an integer")
    if not 1 <= multiplier <= rules.max_multiplier:
        raise ValueError("multiplier is outside configured limits")
    if atomic_bet_count < 1:
        raise ValueError("atomic_bet_count must be positive")
    stake_fen = atomic_bet_count * rules.base_stake_fen * multiplier
    if stake_fen > rules.max_ticket_stake_fen:
        raise ValueError("ticket stake exceeds configured limit")
    return stake_fen


def official_gross_payout_fen(
    fixed_bonuses: Iterable[Decimal],
    rules: SportteryRules,
) -> int:
    bonuses = tuple(fixed_bonuses)
    if not bonuses or any(bonus <= 1 for bonus in bonuses):
        raise ValueError("fixed bonuses must all be greater than one")
    combined_bonus = reduce(mul, bonuses, Decimal(1))
    base_stake_yuan = Decimal(rules.base_stake_fen) / Decimal(100)
    payout_yuan = (base_stake_yuan * combined_bonus).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    return int(payout_yuan * 100)
