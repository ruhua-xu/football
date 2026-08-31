from decimal import Decimal

import pytest

from football_system.application.run_analysis import _portfolio_constraints
from football_system.config import AppSettings
from football_system.domain.betting import (
    CandidateStatus,
    NoBetReason,
    PortfolioStatus,
    PortfolioConstraints,
    SelectionCandidate,
    SportteryRules,
    TicketCandidate,
)
from football_system.domain.market import MarketKey, MarketType, SelectionKey
from football_system.domain.services.optimizer import optimize_portfolio

MARKET = MarketKey(market_type=MarketType.THREE_WAY)
RULES = SportteryRules(
    version="TEST_V1",
    base_stake_fen=200,
    max_multiplier=50,
    max_ticket_stake_fen=600_000,
)
DEFAULT_CONSTRAINTS = PortfolioConstraints(
    preferred_max_tickets=4,
    absolute_max_tickets=8,
    extra_ticket_min_roi=Decimal("0.50"),
    operational_complexity_penalty=Decimal("0.01"),
)


def ticket_candidate(
    index: int,
    roi: str,
    match_ids: tuple[str, str] | None = None,
) -> TicketCandidate:
    match_ids = match_ids or (f"match-{index}-1", f"match-{index}-2")
    legs = tuple(
        SelectionCandidate(
            candidate_id=f"selection-{index}-{leg}",
            analysis_run_id="run-1",
            match_id=match_ids[leg - 1],
            market=MARKET,
            selection=SelectionKey.HOME_WIN,
            final_prediction_id=f"final-{index}-{leg}",
            sporttery_bonus_snapshot_id=f"bonus-{index}-{leg}",
            probability=Decimal("0.60"),
            fixed_bonus=Decimal("1.90"),
            break_even_probability=Decimal(1) / Decimal("1.90"),
            ev=Decimal("0.14"),
            status=CandidateStatus.ELIGIBLE,
        )
        for leg in (1, 2)
    )
    expected_roi = Decimal(roi)
    return TicketCandidate(
        ticket_candidate_id=f"candidate-{index}",
        analysis_run_id="run-1",
        legs=legs,
        base_stake_fen=200,
        joint_probability=Decimal("0.36"),
        gross_payout_fen=722,
        expected_gross_payout_fen=Decimal(200) * (Decimal(1) + expected_roi),
        expected_profit_fen=Decimal(200) * expected_roi,
        expected_roi=expected_roi,
        payout_policy_version=RULES.version,
    )


def test_optimizer_allocates_only_legal_two_yuan_units() -> None:
    candidates = tuple(
        ticket_candidate(index, str(Decimal("0.20") - Decimal(index) / Decimal(100)))
        for index in range(1, 6)
    )

    portfolio = optimize_portfolio(
        "run-1", candidates, 10_000, DEFAULT_CONSTRAINTS, RULES
    )

    assert portfolio.status == PortfolioStatus.RECOMMENDED
    assert len(portfolio.tickets) == 4
    assert portfolio.total_stake_fen == 10_000
    assert portfolio.unused_budget_fen == 0
    assert portfolio.cash_position.amount_fen == 0
    assert all(ticket.stake_fen % 200 == 0 for ticket in portfolio.tickets)
    assert all(1 <= ticket.multiplier <= 50 for ticket in portfolio.tickets)
    assert max(ticket.multiplier for ticket in portfolio.tickets) - min(
        ticket.multiplier for ticket in portfolio.tickets
    ) <= 1


def test_optimizer_supports_no_bet() -> None:
    portfolio = optimize_portfolio(
        "run-1", (), 10_000, DEFAULT_CONSTRAINTS, RULES
    )

    assert portfolio.status == PortfolioStatus.NO_BET
    assert portfolio.no_bet_reason == NoBetReason.NO_BET_NO_VALUE
    assert portfolio.total_stake_fen == 0
    assert portfolio.cash_position.amount_fen == 10_000


def test_optimizer_leaves_cash_after_multiplier_cap() -> None:
    portfolio = optimize_portfolio(
        "run-1",
        (ticket_candidate(1, "0.20"),),
        20_000,
        DEFAULT_CONSTRAINTS,
        RULES,
    )

    assert portfolio.tickets[0].multiplier == 50
    assert portfolio.total_stake_fen == 10_000
    assert portfolio.unused_budget_fen == 10_000
    assert portfolio.cash_position.amount_fen == 10_000


def test_extra_high_value_independent_ticket_can_exceed_preferred_limit() -> None:
    candidates = tuple(ticket_candidate(index, "0.30") for index in range(1, 7))
    constraints = PortfolioConstraints(
        preferred_max_tickets=2,
        absolute_max_tickets=5,
        extra_ticket_min_roi=Decimal("0.20"),
        operational_complexity_penalty=Decimal("0.01"),
    )

    portfolio = optimize_portfolio("run-1", candidates, 10_000, constraints, RULES)

    assert len(portfolio.tickets) == 5
    assert len(portfolio.tickets) > constraints.preferred_max_tickets
    assert len(portfolio.tickets) <= constraints.absolute_max_tickets


def test_optimizer_rejects_cross_run_candidate() -> None:
    candidate = ticket_candidate(1, "0.20").model_copy(
        update={"analysis_run_id": "run-2"}
    )
    with pytest.raises(ValueError, match="target analysis run"):
        optimize_portfolio(
            "run-1", (candidate,), 10_000, DEFAULT_CONSTRAINTS, RULES
        )


def test_concentration_penalty_reselects_and_hard_limits_reduce_stake() -> None:
    candidates = (
        ticket_candidate(1, "0.30", ("shared", "match-a")),
        ticket_candidate(2, "0.29", ("shared", "match-b")),
        ticket_candidate(3, "0.28", ("match-c", "match-d")),
    )
    constraints = PortfolioConstraints(
        preferred_max_tickets=2,
        absolute_max_tickets=2,
        max_match_exposure_ratio=Decimal("0.30"),
        max_selection_exposure_ratio=Decimal("0.30"),
        concentration_penalty=Decimal("0.20"),
    )

    portfolio = optimize_portfolio("run-1", candidates, 2_000, constraints, RULES)

    assert portfolio.status == PortfolioStatus.RECOMMENDED
    assert [ticket.candidate.ticket_candidate_id for ticket in portfolio.tickets] == [
        "candidate-1",
        "candidate-3",
    ]
    assert portfolio.total_stake_fen == 1_200
    assert portfolio.unused_budget_fen == 800
    assert all(ticket.stake_fen == 600 for ticket in portfolio.tickets)


def test_marginal_policy_proactively_retains_cash_before_hard_limits() -> None:
    constraints = _portfolio_constraints(AppSettings.from_toml("config/mvp.toml"))

    portfolio = optimize_portfolio(
        "run-1",
        (ticket_candidate(1, "0.12"),),
        2_000,
        constraints,
        RULES,
    )

    assert portfolio.status == PortfolioStatus.RECOMMENDED
    assert portfolio.tickets[0].multiplier == 2
    assert portfolio.total_stake_fen == 400
    assert portfolio.cash_position.amount_fen == 1_600


def test_risk_limits_can_make_every_allocation_no_bet() -> None:
    constraints = PortfolioConstraints(
        max_match_exposure_ratio=Decimal(0),
        max_selection_exposure_ratio=Decimal(0),
    )

    portfolio = optimize_portfolio(
        "run-1",
        (ticket_candidate(1, "0.20"),),
        2_000,
        constraints,
        RULES,
    )

    assert portfolio.status == PortfolioStatus.NO_BET
    assert portfolio.no_bet_reason == NoBetReason.NO_BET_RISK_LIMIT
    assert portfolio.tickets == ()
    assert portfolio.cash_position.amount_fen == 2_000


def test_recommended_portfolio_never_exceeds_selection_limit() -> None:
    candidates = tuple(
        ticket_candidate(index, "0.20", ("shared", f"match-{index}"))
        for index in range(1, 4)
    )
    constraints = PortfolioConstraints(
        preferred_max_tickets=3,
        absolute_max_tickets=3,
        max_match_exposure_ratio=Decimal(1),
        max_selection_exposure_ratio=Decimal("0.30"),
    )

    portfolio = optimize_portfolio("run-1", candidates, 2_000, constraints, RULES)

    assert portfolio.status == PortfolioStatus.RECOMMENDED
    shared_exposure = sum(
        ticket.stake_fen
        for ticket in portfolio.tickets
        if any(leg.match_id == "shared" for leg in ticket.candidate.legs)
    )
    assert shared_exposure == 600
    assert Decimal(shared_exposure) / Decimal(portfolio.budget_fen) <= (
        constraints.max_selection_exposure_ratio
    )
