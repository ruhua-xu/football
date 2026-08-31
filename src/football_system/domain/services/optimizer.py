from __future__ import annotations

from decimal import Decimal

from football_system.domain.betting import (
    CashPosition,
    NoBetReason,
    Portfolio,
    PortfolioConstraints,
    PortfolioStatus,
    SportteryRules,
    TicketAllocation,
    TicketCandidate,
)
from football_system.domain.common import stable_id
from football_system.domain.services.payout import calculate_stake_fen


def optimize_portfolio(
    analysis_run_id: str,
    candidates: tuple[TicketCandidate, ...],
    budget_fen: int,
    constraints: PortfolioConstraints,
    rules: SportteryRules,
) -> Portfolio:
    if any(candidate.analysis_run_id != analysis_run_id for candidate in candidates):
        raise ValueError("portfolio candidates must belong to the target analysis run")
    portfolio_id = stable_id("portfolio", analysis_run_id, budget_fen)
    if budget_fen < rules.base_stake_fen:
        return _no_bet(
            portfolio_id,
            analysis_run_id,
            budget_fen,
            NoBetReason.NO_BET_NO_FEASIBLE_TICKET,
            constraints,
        )
    ranked = sorted(candidates, key=lambda item: (-item.expected_roi, item.ticket_candidate_id))
    if not ranked:
        return _no_bet(
            portfolio_id,
            analysis_run_id,
            budget_fen,
            NoBetReason.NO_BET_NO_VALUE,
            constraints,
        )

    unit_budget = budget_fen // rules.base_stake_fen
    preferred_count = min(
        constraints.preferred_max_tickets,
        len(ranked),
        unit_budget,
    )
    selected = list(ranked[:preferred_count])
    exposed_matches = {
        leg.match_id for candidate in selected for leg in candidate.legs
    }
    for candidate in ranked[preferred_count:]:
        if (
            len(selected) >= constraints.absolute_max_tickets
            or len(selected) >= unit_budget
        ):
            break
        extra_position = len(selected) - constraints.preferred_max_tickets + 1
        adjusted_roi = candidate.expected_roi - (
            constraints.operational_complexity_penalty * Decimal(extra_position)
        )
        candidate_matches = {leg.match_id for leg in candidate.legs}
        adds_independent_exposure = bool(candidate_matches - exposed_matches)
        if (
            adjusted_roi < constraints.extra_ticket_min_roi
            or not adds_independent_exposure
        ):
            continue
        selected.append(candidate)
        exposed_matches.update(candidate_matches)
    multipliers = {candidate.ticket_candidate_id: 0 for candidate in selected}
    total_stake = 0

    while True:
        allocated = False
        for candidate in selected:
            current = multipliers[candidate.ticket_candidate_id]
            next_multiplier = current + 1
            try:
                next_stake = calculate_stake_fen(
                    candidate.atomic_bet_count, next_multiplier, rules
                )
            except ValueError:
                continue
            current_stake = (
                calculate_stake_fen(candidate.atomic_bet_count, current, rules)
                if current > 0
                else 0
            )
            marginal_stake = next_stake - current_stake
            if total_stake + marginal_stake > budget_fen:
                continue
            multipliers[candidate.ticket_candidate_id] = next_multiplier
            total_stake += marginal_stake
            allocated = True
        if not allocated:
            break

    allocations: list[TicketAllocation] = []
    for candidate in selected:
        multiplier = multipliers[candidate.ticket_candidate_id]
        if multiplier == 0:
            continue
        stake_fen = calculate_stake_fen(candidate.atomic_bet_count, multiplier, rules)
        allocations.append(
            TicketAllocation(
                ticket_id=stable_id("ticket", portfolio_id, candidate.ticket_candidate_id),
                ticket_no=len(allocations) + 1,
                candidate=candidate,
                multiplier=multiplier,
                stake_fen=stake_fen,
                potential_gross_payout_fen=candidate.gross_payout_fen * multiplier,
                expected_gross_payout_fen=(
                    candidate.expected_gross_payout_fen * Decimal(multiplier)
                ),
                expected_profit_fen=candidate.expected_profit_fen * Decimal(multiplier),
                expected_roi=candidate.expected_roi,
                probability_any_payout=candidate.joint_probability,
            )
        )

    if not allocations:
        return _no_bet(
            portfolio_id,
            analysis_run_id,
            budget_fen,
            NoBetReason.NO_BET_NO_FEASIBLE_TICKET,
            constraints,
        )
    total_stake = sum(item.stake_fen for item in allocations)
    return Portfolio(
        portfolio_id=portfolio_id,
        analysis_run_id=analysis_run_id,
        budget_fen=budget_fen,
        tickets=tuple(allocations),
        total_stake_fen=total_stake,
        unused_budget_fen=budget_fen - total_stake,
        cash_position=CashPosition(
            position_id=stable_id("cash", portfolio_id),
            amount_fen=budget_fen - total_stake,
        ),
        status=PortfolioStatus.RECOMMENDED,
        constraints=constraints,
    )


def _no_bet(
    portfolio_id: str,
    analysis_run_id: str,
    budget_fen: int,
    reason: NoBetReason,
    constraints: PortfolioConstraints,
) -> Portfolio:
    return Portfolio(
        portfolio_id=portfolio_id,
        analysis_run_id=analysis_run_id,
        budget_fen=budget_fen,
        tickets=(),
        total_stake_fen=0,
        unused_budget_fen=budget_fen,
        cash_position=CashPosition(
            position_id=stable_id("cash", portfolio_id),
            amount_fen=budget_fen,
        ),
        status=PortfolioStatus.NO_BET,
        no_bet_reason=reason,
        constraints=constraints,
    )
