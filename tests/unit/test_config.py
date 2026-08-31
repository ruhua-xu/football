from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.application.run_analysis import _portfolio_constraints
from football_system.config import (
    AnalysisSettings,
    AppSettings,
    PortfolioSettings,
    SportterySettings,
)
from football_system.domain.betting import MAX_EXACT_STRESS_TICKETS


def test_loads_versioned_mvp_settings() -> None:
    settings = AppSettings.from_toml("config/mvp.toml")

    assert settings.analysis.pipeline_version == "PORTFOLIO_RISK_V2"
    assert settings.analysis.quant_weight == Decimal("0.70")
    assert settings.sporttery.base_stake_fen == 200
    assert settings.sporttery.max_multiplier == 50
    assert settings.sporttery.max_ticket_stake_fen == 600_000
    assert settings.portfolio.preferred_max_tickets == 4
    assert settings.portfolio.absolute_max_tickets == 8
    assert settings.portfolio.max_match_exposure_ratio == Decimal("0.60")
    assert settings.portfolio.max_selection_exposure_ratio == Decimal("0.60")
    assert settings.portfolio.concentration_penalty == Decimal("0.10")
    assert settings.portfolio.min_marginal_score == Decimal("0.10")
    assert settings.review_fusion.policy == "LLM_REVIEW_DELTA_V1"
    assert settings.review_fusion.max_probability_delta == Decimal("0.08")
    assert settings.review_fusion.legacy_data_quality_factor == Decimal("0.25")


def test_run_analysis_freezes_portfolio_risk_controls() -> None:
    settings = AppSettings.from_toml("config/mvp.toml")

    constraints = _portfolio_constraints(settings)

    assert constraints.max_match_exposure_ratio == Decimal("0.60")
    assert constraints.max_selection_exposure_ratio == Decimal("0.60")
    assert constraints.concentration_penalty == Decimal("0.10")
    assert constraints.min_marginal_score == Decimal("0.10")


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


def test_rejects_invalid_portfolio_risk_controls() -> None:
    with pytest.raises(ValidationError):
        PortfolioSettings(max_match_exposure_ratio=Decimal("1.01"))
    with pytest.raises(ValidationError):
        PortfolioSettings(max_selection_exposure_ratio=Decimal("-0.01"))
    with pytest.raises(ValidationError):
        PortfolioSettings(concentration_penalty=Decimal("-0.01"))
    with pytest.raises(ValidationError):
        PortfolioSettings(min_marginal_score=Decimal("-0.01"))
