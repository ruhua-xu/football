from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from football_system.domain.betting import MAX_EXACT_STRESS_TICKETS


class SettingsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DatabaseSettings(SettingsModel):
    url: str = "sqlite:///data/football_mvp.db"


class AnalysisSettings(SettingsModel):
    pipeline_version: str = "PORTFOLIO_RISK_V1"
    fusion_policy: str = "QUANT_ONLY_V1"
    quant_weight: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)
    min_selection_ev: Decimal = Field(default=Decimal("0.02"), ge=0)
    min_ticket_roi: Decimal = Field(default=Decimal("0.02"), ge=0)


class PortfolioSettings(SettingsModel):
    preferred_max_tickets: int = Field(default=4, ge=1)
    absolute_max_tickets: int = Field(
        default=8,
        ge=1,
        le=MAX_EXACT_STRESS_TICKETS,
    )
    extra_ticket_min_roi: Decimal = Field(default=Decimal("0.20"), ge=0)
    operational_complexity_penalty: Decimal = Field(default=Decimal("0.01"), ge=0)

    @model_validator(mode="after")
    def validate_ticket_limits(self) -> PortfolioSettings:
        if self.preferred_max_tickets > self.absolute_max_tickets:
            raise ValueError("preferred_max_tickets cannot exceed absolute_max_tickets")
        return self


class SportterySettings(SettingsModel):
    rules_version: str = "SPORTTERY_MVP_V1"
    base_stake_fen: int = Field(default=200, gt=0)
    max_multiplier: int = Field(default=50, ge=1)
    max_ticket_stake_fen: int = Field(default=600_000, gt=0)

    @model_validator(mode="after")
    def validate_ticket_limit(self) -> SportterySettings:
        if self.max_ticket_stake_fen < self.base_stake_fen:
            raise ValueError("max_ticket_stake_fen must cover one base stake")
        return self


class MockSettings(SettingsModel):
    fixture_path: Path = Path("data/fixtures/mvp_matches.json")


class AppSettings(SettingsModel):
    database: DatabaseSettings = DatabaseSettings()
    analysis: AnalysisSettings = AnalysisSettings()
    portfolio: PortfolioSettings = PortfolioSettings()
    sporttery: SportterySettings = SportterySettings()
    mock: MockSettings = MockSettings()

    @model_validator(mode="after")
    def validate_strategy_thresholds(self) -> AppSettings:
        if self.portfolio.extra_ticket_min_roi <= self.analysis.min_ticket_roi:
            raise ValueError("extra_ticket_min_roi must exceed the base ticket ROI threshold")
        return self

    @classmethod
    def from_toml(cls, path: str | Path) -> AppSettings:
        config_path = Path(path)
        with config_path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))
