from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN

from football_system.domain.betting import Portfolio, TicketAllocation
from football_system.domain.common import stable_id
from football_system.domain.market import SelectionKey
from football_system.domain.risk import (
    MatchExposure,
    PortfolioRiskReport,
    SelectionExposure,
    StressOutcome,
    StressScenarioResult,
    StressTicketResult,
    StressTicketState,
)

RATIO_QUANTUM = Decimal("0.000000000001")


def analyze_portfolio_risk(portfolio: Portfolio) -> PortfolioRiskReport:
    report_id = stable_id("portfolio-risk", portfolio.portfolio_id)
    match_tickets: dict[str, list[TicketAllocation]] = defaultdict(list)
    selection_tickets: dict[tuple[str, str, SelectionKey], list[TicketAllocation]] = (
        defaultdict(list)
    )
    market_by_key = {}
    for ticket in portfolio.tickets:
        for leg in ticket.candidate.legs:
            match_tickets[leg.match_id].append(ticket)
            key = (leg.match_id, leg.market.canonical, leg.selection)
            selection_tickets[key].append(ticket)
            market_by_key[key] = leg.market

    match_exposures = tuple(
        MatchExposure(
            exposure_id=stable_id("match-exposure", report_id, match_id),
            risk_report_id=report_id,
            match_id=match_id,
            exposed_stake_fen=sum(ticket.stake_fen for ticket in tickets),
            budget_ratio=_ratio(
                sum(ticket.stake_fen for ticket in tickets), portfolio.budget_fen
            ),
            deployed_ratio=_ratio(
                sum(ticket.stake_fen for ticket in tickets),
                portfolio.total_stake_fen,
            ),
            ticket_ids=tuple(sorted(ticket.ticket_id for ticket in tickets)),
        )
        for match_id, tickets in sorted(match_tickets.items())
    )
    selection_exposures = tuple(
        SelectionExposure(
            exposure_id=stable_id(
                "selection-exposure", report_id, match_id, market_key, selection
            ),
            risk_report_id=report_id,
            match_id=match_id,
            market=market_by_key[(match_id, market_key, selection)],
            selection=selection,
            exposed_stake_fen=sum(ticket.stake_fen for ticket in tickets),
            budget_ratio=_ratio(
                sum(ticket.stake_fen for ticket in tickets), portfolio.budget_fen
            ),
            deployed_ratio=_ratio(
                sum(ticket.stake_fen for ticket in tickets),
                portfolio.total_stake_fen,
            ),
            ticket_ids=tuple(sorted(ticket.ticket_id for ticket in tickets)),
        )
        for (match_id, market_key, selection), tickets in sorted(
            selection_tickets.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2].value),
        )
    )
    stress_results = _build_stress_results(
        portfolio,
        report_id,
        match_exposures,
        selection_exposures,
    )
    return PortfolioRiskReport(
        risk_report_id=report_id,
        analysis_run_id=portfolio.analysis_run_id,
        portfolio_id=portfolio.portfolio_id,
        budget_fen=portfolio.budget_fen,
        total_stake_fen=portfolio.total_stake_fen,
        cash_fen=portfolio.cash_position.amount_fen,
        cash_ratio=_ratio(portfolio.cash_position.amount_fen, portfolio.budget_fen),
        expected_profit_fen=sum(
            (ticket.expected_profit_fen for ticket in portfolio.tickets), Decimal(0)
        ),
        total_stake_at_risk_fen=portfolio.total_stake_fen,
        max_single_ticket_exposure_fen=max(
            (ticket.stake_fen for ticket in portfolio.tickets), default=0
        ),
        max_match_exposure_fen=max(
            (item.exposed_stake_fen for item in match_exposures), default=0
        ),
        match_exposures=match_exposures,
        selection_exposures=selection_exposures,
        stress_results=stress_results,
    )


def _build_stress_results(
    portfolio: Portfolio,
    report_id: str,
    match_exposures: tuple[MatchExposure, ...],
    selection_exposures: tuple[SelectionExposure, ...],
) -> tuple[StressScenarioResult, ...]:
    if not portfolio.tickets:
        return (_evaluate_scenario(portfolio, report_id, "CASH_BASELINE", ()),)

    selected_stake = {
        (item.match_id, item.selection): item.exposed_stake_fen
        for item in selection_exposures
    }
    ranked_matches = sorted(
        match_exposures,
        key=lambda item: (-item.exposed_stake_fen, item.match_id),
    )

    def adverse_outcome(match_id: str) -> StressOutcome:
        selection = min(
            SelectionKey,
            key=lambda item: (selected_stake.get((match_id, item), 0), item.value),
        )
        return StressOutcome(match_id=match_id, selection=selection)

    definitions = [
        (
            "TOP_EXPOSURE_MATCH_ADVERSE",
            (adverse_outcome(ranked_matches[0].match_id),),
        ),
        (
            "TOP_TWO_EXPOSURE_MATCHES_ADVERSE",
            tuple(adverse_outcome(item.match_id) for item in ranked_matches[:2]),
        ),
        (
            "ALL_EXPOSED_MATCHES_ADVERSE",
            tuple(adverse_outcome(item.match_id) for item in ranked_matches),
        ),
    ]
    seen: set[tuple[tuple[str, SelectionKey], ...]] = set()
    results = []
    for key, outcomes in definitions:
        fingerprint = tuple((item.match_id, item.selection) for item in outcomes)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        results.append(_evaluate_scenario(portfolio, report_id, key, outcomes))
    return tuple(results)


def _evaluate_scenario(
    portfolio: Portfolio,
    report_id: str,
    scenario_key: str,
    outcomes: tuple[StressOutcome, ...],
) -> StressScenarioResult:
    outcome_by_match = {item.match_id: item.selection for item in outcomes}
    ticket_results = []
    exposed_stake = 0
    won_gross = 0
    alive_max_gross = 0
    for ticket in portfolio.tickets:
        supplied = [outcome_by_match.get(leg.match_id) for leg in ticket.candidate.legs]
        if any(
            outcome is not None and outcome != leg.selection
            for outcome, leg in zip(supplied, ticket.candidate.legs, strict=True)
        ):
            state = StressTicketState.LOST
            gross = None
            exposed_stake += ticket.stake_fen
        elif all(outcome is not None for outcome in supplied):
            state = StressTicketState.WON
            gross = ticket.potential_gross_payout_fen
            won_gross += gross
        else:
            state = StressTicketState.ALIVE
            gross = None
            alive_max_gross += ticket.potential_gross_payout_fen
        ticket_results.append(
            StressTicketResult(
                ticket_id=ticket.ticket_id,
                state=state,
                gross_payout_fen=gross,
            )
        )

    minimum_capital = portfolio.cash_position.amount_fen + won_gross
    maximum_capital = minimum_capital + alive_max_gross
    complete = not any(
        result.state == StressTicketState.ALIVE for result in ticket_results
    )
    ending_capital = minimum_capital if complete else None
    scenario_id = stable_id(
        "stress-scenario",
        report_id,
        scenario_key,
        *(f"{item.match_id}:{item.selection.value}" for item in outcomes),
    )
    return StressScenarioResult(
        scenario_id=scenario_id,
        risk_report_id=report_id,
        portfolio_id=portfolio.portfolio_id,
        scenario_key=scenario_key,
        outcomes=outcomes,
        is_complete=complete,
        ticket_results=tuple(ticket_results),
        scenario_exposed_stake_fen=exposed_stake,
        scenario_exposure_ratio=_ratio(exposed_stake, portfolio.budget_fen),
        gross_payout_fen=won_gross if complete else None,
        ending_capital_fen=ending_capital,
        profit_loss_fen=(
            ending_capital - portfolio.budget_fen
            if ending_capital is not None
            else None
        ),
        capital_recovery_ratio=(
            _ratio(ending_capital, portfolio.budget_fen)
            if ending_capital is not None
            else None
        ),
        minimum_ending_capital_fen=minimum_capital,
        maximum_ending_capital_fen=maximum_capital,
    )


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATIO_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
