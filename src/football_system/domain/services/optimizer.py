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

    minimum_stakes: dict[str, int] = {}
    for candidate in ranked:
        try:
            minimum_stake = calculate_stake_fen(candidate.atomic_bet_count, 1, rules)
        except ValueError:
            continue
        if minimum_stake <= budget_fen:
            minimum_stakes[candidate.ticket_candidate_id] = minimum_stake
    if not minimum_stakes:
        return _no_bet(
            portfolio_id,
            analysis_run_id,
            budget_fen,
            NoBetReason.NO_BET_NO_FEASIBLE_TICKET,
            constraints,
        )

    selected: list[TicketCandidate] = []
    multipliers: dict[str, int] = {}
    match_exposures: dict[str, int] = {}
    selection_exposures: dict[tuple[str, str, str], int] = {}
    total_stake = 0

    while (
        len(selected) < constraints.absolute_max_tickets
        and len(selected) < len(minimum_stakes)
    ):
        is_extra = len(selected) >= constraints.preferred_max_tickets
        extra_position = len(selected) - constraints.preferred_max_tickets + 1
        opening_choices: list[tuple[TicketCandidate, int, Decimal]] = []
        for candidate in ranked:
            if candidate.ticket_candidate_id in multipliers:
                continue
            minimum_stake = minimum_stakes.get(candidate.ticket_candidate_id)
            if minimum_stake is None:
                continue
            if is_extra:
                adjusted_roi = candidate.expected_roi - (
                    constraints.operational_complexity_penalty
                    * Decimal(extra_position)
                )
                candidate_matches = {leg.match_id for leg in candidate.legs}
                if (
                    adjusted_roi < constraints.extra_ticket_min_roi
                    or not candidate_matches - match_exposures.keys()
                ):
                    continue
            if not _allocation_is_feasible(
                candidate,
                minimum_stake,
                total_stake,
                budget_fen,
                match_exposures,
                selection_exposures,
                constraints,
            ):
                continue
            score = _marginal_score(
                candidate,
                minimum_stake,
                budget_fen,
                match_exposures,
                selection_exposures,
                constraints,
            )
            if score >= constraints.min_marginal_score:
                opening_choices.append((candidate, minimum_stake, score))
        if not opening_choices:
            break
        candidate, minimum_stake, _ = min(
            opening_choices,
            key=lambda item: (-item[2], item[0].ticket_candidate_id),
        )
        selected.append(candidate)
        multipliers[candidate.ticket_candidate_id] = 1
        total_stake += minimum_stake
        _add_exposure(
            candidate,
            minimum_stake,
            match_exposures,
            selection_exposures,
        )

    if not selected:
        return _no_bet(
            portfolio_id,
            analysis_run_id,
            budget_fen,
            NoBetReason.NO_BET_RISK_LIMIT,
            constraints,
        )

    while True:
        pending = list(selected)
        allocated = False
        while pending:
            allocation_choices: list[tuple[TicketCandidate, int, Decimal]] = []
            for candidate in pending:
                current = multipliers[candidate.ticket_candidate_id]
                next_multiplier = current + 1
                try:
                    next_stake = calculate_stake_fen(
                        candidate.atomic_bet_count, next_multiplier, rules
                    )
                    current_stake = calculate_stake_fen(
                        candidate.atomic_bet_count, current, rules
                    )
                except ValueError:
                    continue
                marginal_stake = next_stake - current_stake
                if not _allocation_is_feasible(
                    candidate,
                    marginal_stake,
                    total_stake,
                    budget_fen,
                    match_exposures,
                    selection_exposures,
                    constraints,
                ):
                    continue
                score = _marginal_score(
                    candidate,
                    marginal_stake,
                    budget_fen,
                    match_exposures,
                    selection_exposures,
                    constraints,
                )
                if score >= constraints.min_marginal_score:
                    allocation_choices.append((candidate, marginal_stake, score))
            if not allocation_choices:
                break
            candidate, marginal_stake, _ = min(
                allocation_choices,
                key=lambda item: (-item[2], item[0].ticket_candidate_id),
            )
            pending.remove(candidate)
            multipliers[candidate.ticket_candidate_id] += 1
            total_stake += marginal_stake
            _add_exposure(
                candidate,
                marginal_stake,
                match_exposures,
                selection_exposures,
            )
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


def _allocation_is_feasible(
    candidate: TicketCandidate,
    marginal_stake: int,
    total_stake: int,
    budget_fen: int,
    match_exposures: dict[str, int],
    selection_exposures: dict[tuple[str, str, str], int],
    constraints: PortfolioConstraints,
) -> bool:
    if total_stake + marginal_stake > budget_fen:
        return False
    match_limit = Decimal(budget_fen) * constraints.max_match_exposure_ratio
    selection_limit = Decimal(budget_fen) * constraints.max_selection_exposure_ratio
    if any(
        Decimal(match_exposures.get(match_id, 0) + marginal_stake) > match_limit
        for match_id in {leg.match_id for leg in candidate.legs}
    ):
        return False
    return not any(
        Decimal(selection_exposures.get(key, 0) + marginal_stake) > selection_limit
        for key in _selection_keys(candidate)
    )


def _marginal_score(
    candidate: TicketCandidate,
    marginal_stake: int,
    budget_fen: int,
    match_exposures: dict[str, int],
    selection_exposures: dict[tuple[str, str, str], int],
    constraints: PortfolioConstraints,
) -> Decimal:
    projected_exposures = tuple(
        match_exposures.get(match_id, 0) + marginal_stake
        for match_id in {leg.match_id for leg in candidate.legs}
    ) + tuple(
        selection_exposures.get(key, 0) + marginal_stake
        for key in _selection_keys(candidate)
    )
    projected_exposure = max(projected_exposures)
    projected_ratio = Decimal(projected_exposure) / Decimal(budget_fen)
    return candidate.expected_roi - (
        constraints.concentration_penalty * projected_ratio
    )


def _add_exposure(
    candidate: TicketCandidate,
    marginal_stake: int,
    match_exposures: dict[str, int],
    selection_exposures: dict[tuple[str, str, str], int],
) -> None:
    for match_id in {leg.match_id for leg in candidate.legs}:
        match_exposures[match_id] = match_exposures.get(match_id, 0) + marginal_stake
    for key in _selection_keys(candidate):
        selection_exposures[key] = selection_exposures.get(key, 0) + marginal_stake


def _selection_keys(candidate: TicketCandidate) -> set[tuple[str, str, str]]:
    return {
        (leg.match_id, leg.market.canonical, leg.selection.value)
        for leg in candidate.legs
    }


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
