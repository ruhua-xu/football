from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.config import (
    AnalysisSettings,
    AppSettings,
    PortfolioSettings,
    SportterySettings,
)
from football_system.domain.betting import MAX_EXACT_STRESS_TICKETS


def test_loads_versioned_mvp_settings() -> None:
    settings = AppSettings.from_toml("config/mvp.toml")

    assert settings.analysis.quant_weight == Decimal("0.70")
    assert settings.sporttery.base_stake_fen == 200
    assert settings.sporttery.max_multiplier == 50
    assert settings.sporttery.max_ticket_stake_fen == 600_000
    assert settings.portfolio.preferred_max_tickets == 4
    assert settings.portfolio.absolute_max_tickets == 8


def test_rejects_ticket_limit_below_base_stake() -> None:
    with pytest.raises(ValidationError):
        SportterySettings(base_stake_fen=200, max_ticket_stake_fen=199)


def test_rejects_negative_or_non_strict_value_thresholds() -> None:
    with pytest.raises(ValidationError):
        AnalysisSettings(min_selection_ev=Decimal("-0.01"))
    with pytest.raises(ValidationError, match="extra_ticket_min_roi"):
        AppSettings(
            analysis={"min_ticket_roi": "0.20"},
            portfolio={"extra_ticket_min_roi": "0.20"},
        )


def test_rejects_ticket_count_above_exact_stress_bound() -> None:
    with pytest.raises(ValidationError):
        PortfolioSettings(
            preferred_max_tickets=MAX_EXACT_STRESS_TICKETS + 1,
            absolute_max_tickets=MAX_EXACT_STRESS_TICKETS + 1,
        )
