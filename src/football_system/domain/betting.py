from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier
from football_system.domain.market import MarketKey, SelectionKey


MAX_EXACT_STRESS_TICKETS = 12


class CandidateStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"


class PassType(StrEnum):
    TWO_FOLD_ONE = "2X1"


class PortfolioStatus(StrEnum):
    RECOMMENDED = "RECOMMENDED"
    NO_BET = "NO_BET"


class NoBetReason(StrEnum):
    NO_BET_DATA_QUALITY = "NO_BET_DATA_QUALITY"
    NO_BET_NO_VALUE = "NO_BET_NO_VALUE"
    NO_BET_NO_FEASIBLE_TICKET = "NO_BET_NO_FEASIBLE_TICKET"
    NO_BET_RISK_LIMIT = "NO_BET_RISK_LIMIT"


class PortfolioConstraints(DomainModel):
    preferred_max_tickets: int = Field(default=4, ge=1)
    absolute_max_tickets: int = Field(
        default=8,
        ge=1,
        le=MAX_EXACT_STRESS_TICKETS,
    )
    extra_ticket_min_roi: Decimal = Field(default=Decimal("0.20"), ge=0)
    operational_complexity_penalty: Decimal = Field(default=Decimal("0.01"), ge=0)
    max_match_exposure_ratio: Decimal = Field(default=Decimal(1), ge=0, le=1)
    max_selection_exposure_ratio: Decimal = Field(default=Decimal(1), ge=0, le=1)
    concentration_penalty: Decimal = Field(default=Decimal(0), ge=0)
    min_marginal_score: Decimal = Field(default=Decimal(0), ge=0)

    @model_validator(mode="after")
    def validate_ticket_limits(self) -> PortfolioConstraints:
        if self.preferred_max_tickets > self.absolute_max_tickets:
            raise ValueError("preferred ticket limit cannot exceed absolute limit")
        return self


class SportteryRules(DomainModel):
    version: str
    base_stake_fen: int = Field(default=200, gt=0)
    max_multiplier: int = Field(default=50, ge=1)
    max_ticket_stake_fen: int = Field(default=600_000, gt=0)

    @model_validator(mode="after")
    def validate_limits(self) -> SportteryRules:
        if self.max_ticket_stake_fen < self.base_stake_fen:
            raise ValueError("max ticket stake must cover one base stake")
        return self


class SelectionCandidate(DomainModel):
    candidate_id: Identifier
    analysis_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    selection: SelectionKey
    final_prediction_id: Identifier
    sporttery_bonus_snapshot_id: Identifier
    probability: Decimal = Field(ge=0, le=1)
    fixed_bonus: Decimal = Field(gt=1)
    break_even_probability: Decimal = Field(gt=0, lt=1)
    ev: Decimal
    status: CandidateStatus
    rejection_code: str | None = None


class TicketCandidate(DomainModel):
    ticket_candidate_id: Identifier
    analysis_run_id: Identifier
    pass_type: PassType = PassType.TWO_FOLD_ONE
    legs: tuple[SelectionCandidate, SelectionCandidate]
    atomic_bet_count: int = 1
    base_stake_fen: int
    joint_probability: Decimal = Field(ge=0, le=1)
    gross_payout_fen: int = Field(gt=0)
    expected_gross_payout_fen: Decimal = Field(ge=0)
    expected_profit_fen: Decimal
    expected_roi: Decimal
    payout_policy_version: str

    @model_validator(mode="after")
    def validate_legs(self) -> TicketCandidate:
        if self.legs[0].match_id == self.legs[1].match_id:
            raise ValueError("2X1 legs must reference different matches")
        if any(leg.analysis_run_id != self.analysis_run_id for leg in self.legs):
            raise ValueError("ticket legs must belong to the same analysis run")
        return self


class TicketAllocation(DomainModel):
    ticket_id: Identifier
    ticket_no: int = Field(ge=1)
    candidate: TicketCandidate
    multiplier: int = Field(ge=1)
    stake_fen: int = Field(gt=0)
    potential_gross_payout_fen: int = Field(gt=0)
    expected_gross_payout_fen: Decimal = Field(ge=0)
    expected_profit_fen: Decimal
    expected_roi: Decimal
    probability_any_payout: Decimal = Field(ge=0, le=1)


class CashPosition(DomainModel):
    position_id: Identifier
    amount_fen: int = Field(ge=0)
    expected_profit_fen: Decimal = Decimal(0)


class Portfolio(DomainModel):
    portfolio_id: Identifier
    analysis_run_id: Identifier
    budget_fen: int = Field(ge=0)
    tickets: tuple[TicketAllocation, ...]
    total_stake_fen: int = Field(ge=0)
    unused_budget_fen: int = Field(ge=0)
    cash_position: CashPosition
    status: PortfolioStatus
    no_bet_reason: NoBetReason | None = None
    constraints: PortfolioConstraints
    strategy_version: str = "RISK_CONTROLLED_MARGINAL_V2"

    @model_validator(mode="after")
    def validate_portfolio(self) -> Portfolio:
        if len(self.tickets) > self.constraints.absolute_max_tickets:
            raise ValueError("portfolio exceeds frozen absolute ticket limit")
        if self.total_stake_fen != sum(ticket.stake_fen for ticket in self.tickets):
            raise ValueError("portfolio stake must equal ticket stakes")
        if self.total_stake_fen > self.budget_fen:
            raise ValueError("portfolio exceeds budget")
        if self.unused_budget_fen != self.budget_fen - self.total_stake_fen:
            raise ValueError("unused budget is inconsistent")
        if self.cash_position.amount_fen != self.unused_budget_fen:
            raise ValueError("cash position must equal unused budget")
        if self.cash_position.expected_profit_fen != 0:
            raise ValueError("cash position must have zero nominal profit")
        ticket_numbers = [ticket.ticket_no for ticket in self.tickets]
        if len(ticket_numbers) != len(set(ticket_numbers)):
            raise ValueError("ticket numbers must be unique within a portfolio")
        candidate_ids = [ticket.candidate.ticket_candidate_id for ticket in self.tickets]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("a portfolio cannot contain the same ticket candidate twice")
        if any(
            ticket.candidate.analysis_run_id != self.analysis_run_id
            for ticket in self.tickets
        ):
            raise ValueError("portfolio tickets must belong to the same analysis run")
        match_exposures: dict[str, int] = {}
        selection_exposures: dict[tuple[str, str, str], int] = {}
        for ticket in self.tickets:
            for leg in ticket.candidate.legs:
                match_exposures[leg.match_id] = (
                    match_exposures.get(leg.match_id, 0) + ticket.stake_fen
                )
                selection_key = (
                    leg.match_id,
                    leg.market.canonical,
                    leg.selection.value,
                )
                selection_exposures[selection_key] = (
                    selection_exposures.get(selection_key, 0) + ticket.stake_fen
                )
        if any(
            Decimal(exposure)
            > Decimal(self.budget_fen) * self.constraints.max_match_exposure_ratio
            for exposure in match_exposures.values()
        ):
            raise ValueError("portfolio exceeds maximum match exposure")
        if any(
            Decimal(exposure)
            > Decimal(self.budget_fen) * self.constraints.max_selection_exposure_ratio
            for exposure in selection_exposures.values()
        ):
            raise ValueError("portfolio exceeds maximum selection exposure")
        if self.status == PortfolioStatus.NO_BET:
            if self.tickets or self.total_stake_fen != 0 or self.no_bet_reason is None:
                raise ValueError("NO_BET portfolio must be empty and include a reason")
        elif not self.tickets or self.no_bet_reason is not None:
            raise ValueError("recommended portfolio must contain tickets and no reason")
        return self
