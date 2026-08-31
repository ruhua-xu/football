from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier
from football_system.domain.market import MarketKey, SelectionKey


class StressTicketState(StrEnum):
    LOST = "LOST"
    WON = "WON"
    ALIVE = "ALIVE"


class MatchExposure(DomainModel):
    exposure_id: Identifier
    risk_report_id: Identifier
    match_id: Identifier
    exposed_stake_fen: int = Field(ge=0)
    budget_ratio: Decimal | None = Field(default=None, ge=0)
    deployed_ratio: Decimal | None = Field(default=None, ge=0)
    ticket_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def validate_tickets(self) -> MatchExposure:
        if not self.ticket_ids or len(self.ticket_ids) != len(set(self.ticket_ids)):
            raise ValueError("match exposure must reference unique tickets")
        return self


class SelectionExposure(DomainModel):
    exposure_id: Identifier
    risk_report_id: Identifier
    match_id: Identifier
    market: MarketKey
    selection: SelectionKey
    exposed_stake_fen: int = Field(ge=0)
    budget_ratio: Decimal | None = Field(default=None, ge=0)
    deployed_ratio: Decimal | None = Field(default=None, ge=0)
    ticket_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def validate_tickets(self) -> SelectionExposure:
        if not self.ticket_ids or len(self.ticket_ids) != len(set(self.ticket_ids)):
            raise ValueError("selection exposure must reference unique tickets")
        return self


class StressOutcome(DomainModel):
    match_id: Identifier
    selection: SelectionKey


class StressTicketResult(DomainModel):
    ticket_id: Identifier
    state: StressTicketState
    gross_payout_fen: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_payout(self) -> StressTicketResult:
        if self.state == StressTicketState.WON:
            if self.gross_payout_fen is None or self.gross_payout_fen <= 0:
                raise ValueError("winning stress ticket requires a gross payout")
        elif self.gross_payout_fen is not None:
            raise ValueError("non-winning stress ticket cannot have a gross payout")
        return self


class StressScenarioResult(DomainModel):
    scenario_id: Identifier
    risk_report_id: Identifier
    portfolio_id: Identifier
    scenario_key: str
    policy_version: str = "DETERMINISTIC_PORTFOLIO_STRESS_V2"
    outcomes: tuple[StressOutcome, ...]
    is_complete: bool
    ticket_results: tuple[StressTicketResult, ...]
    scenario_exposed_stake_fen: int = Field(ge=0)
    scenario_exposure_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    gross_payout_fen: int | None = Field(default=None, ge=0)
    ending_capital_fen: int | None = Field(default=None, ge=0)
    profit_loss_fen: int | None = None
    capital_recovery_ratio: Decimal | None = Field(default=None, ge=0)
    minimum_ending_capital_fen: int = Field(ge=0)
    maximum_ending_capital_fen: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_result(self) -> StressScenarioResult:
        match_ids = [outcome.match_id for outcome in self.outcomes]
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("stress scenario match outcomes must be unique")
        ticket_ids = [result.ticket_id for result in self.ticket_results]
        if len(ticket_ids) != len(set(ticket_ids)):
            raise ValueError("stress scenario ticket results must be unique")
        if self.minimum_ending_capital_fen > self.maximum_ending_capital_fen:
            raise ValueError("stress scenario capital bounds are invalid")
        exact_values = (
            self.gross_payout_fen,
            self.ending_capital_fen,
            self.profit_loss_fen,
        )
        if self.is_complete:
            if any(value is None for value in exact_values):
                raise ValueError("complete stress scenario requires exact financial results")
            if any(
                result.state == StressTicketState.ALIVE for result in self.ticket_results
            ):
                raise ValueError("complete stress scenario cannot contain an alive ticket")
            if self.gross_payout_fen != sum(
                result.gross_payout_fen or 0 for result in self.ticket_results
            ):
                raise ValueError("complete stress payout is inconsistent")
            if self.ending_capital_fen != self.minimum_ending_capital_fen:
                raise ValueError("complete scenario must have exact capital bounds")
            if self.minimum_ending_capital_fen != self.maximum_ending_capital_fen:
                raise ValueError("complete scenario must have exact capital bounds")
        else:
            if not any(
                result.state == StressTicketState.ALIVE for result in self.ticket_results
            ):
                raise ValueError("partial stress scenario requires an alive ticket")
            if any(value is not None for value in exact_values) or (
                self.capital_recovery_ratio is not None
            ):
                raise ValueError("partial stress scenario cannot claim exact financial results")
        return self


class PortfolioRiskReport(DomainModel):
    risk_report_id: Identifier
    analysis_run_id: Identifier
    portfolio_id: Identifier
    policy_version: str = "TOP_LEVEL_STAKE_EXPOSURE_V1"
    budget_fen: int = Field(ge=0)
    total_stake_fen: int = Field(ge=0)
    cash_fen: int = Field(ge=0)
    cash_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    expected_profit_fen: Decimal
    total_stake_at_risk_fen: int = Field(ge=0)
    max_single_ticket_exposure_fen: int = Field(ge=0)
    max_match_exposure_fen: int = Field(ge=0)
    match_exposures: tuple[MatchExposure, ...]
    selection_exposures: tuple[SelectionExposure, ...]
    stress_results: tuple[StressScenarioResult, ...]

    @model_validator(mode="after")
    def validate_report(self) -> PortfolioRiskReport:
        if self.total_stake_fen + self.cash_fen != self.budget_fen:
            raise ValueError("risk report cash and stake must equal portfolio budget")
        if self.total_stake_at_risk_fen != self.total_stake_fen:
            raise ValueError("top-level ticket stake is the V1 stake at risk")
        if self.max_match_exposure_fen != max(
            (item.exposed_stake_fen for item in self.match_exposures), default=0
        ):
            raise ValueError("maximum match exposure is inconsistent")
        if any(item.risk_report_id != self.risk_report_id for item in self.match_exposures):
            raise ValueError("match exposure belongs to another risk report")
        if any(
            item.risk_report_id != self.risk_report_id
            for item in self.selection_exposures
        ):
            raise ValueError("selection exposure belongs to another risk report")
        if any(
            item.risk_report_id != self.risk_report_id
            or item.portfolio_id != self.portfolio_id
            for item in self.stress_results
        ):
            raise ValueError("stress result belongs to another risk report")
        if not self.stress_results:
            raise ValueError("risk report requires at least one stress result")
        return self
