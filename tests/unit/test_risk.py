from decimal import Decimal

import pytest

from football_system.domain.betting import (
    CandidateStatus,
    CashPosition,
    MAX_EXACT_STRESS_TICKETS,
    Portfolio,
    PortfolioConstraints,
    PortfolioStatus,
    SelectionCandidate,
    TicketAllocation,
    TicketCandidate,
)
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType, SelectionKey
from football_system.domain.risk import StressTicketState
from football_system.domain.services.risk import analyze_portfolio_risk

MARKET = MarketKey(market_type=MarketType.THREE_WAY)
CONSTRAINTS = PortfolioConstraints()


def _ticket(
    ticket_no: int,
    stake_fen: int,
    first_match: str,
    second_match: str,
    selection: SelectionKey = SelectionKey.HOME_WIN,
) -> TicketAllocation:
    legs = tuple(
        SelectionCandidate(
            candidate_id=f"candidate-{ticket_no}-{match_id}-{selection.value}",
            analysis_run_id="run-risk",
            match_id=match_id,
            market=MARKET,
            selection=selection,
            final_prediction_id=f"final-{match_id}",
            sporttery_bonus_snapshot_id=f"bonus-{match_id}",
            probability=Decimal("0.6"),
            fixed_bonus=Decimal("1.9"),
            break_even_probability=Decimal(1) / Decimal("1.9"),
            ev=Decimal("0.14"),
            status=CandidateStatus.ELIGIBLE,
        )
        for match_id in (first_match, second_match)
    )
    candidate = TicketCandidate(
        ticket_candidate_id=f"ticket-candidate-{ticket_no}",
        analysis_run_id="run-risk",
        legs=legs,
        base_stake_fen=200,
        joint_probability=Decimal("0.36"),
        gross_payout_fen=722,
        expected_gross_payout_fen=Decimal("259.92"),
        expected_profit_fen=Decimal("59.92"),
        expected_roi=Decimal("0.2996"),
        payout_policy_version="TEST_V1",
    )
    multiplier = stake_fen // 200
    return TicketAllocation(
        ticket_id=f"ticket-{ticket_no}",
        ticket_no=ticket_no,
        candidate=candidate,
        multiplier=multiplier,
        stake_fen=stake_fen,
        potential_gross_payout_fen=722 * multiplier,
        expected_gross_payout_fen=Decimal("259.92") * multiplier,
        expected_profit_fen=Decimal("59.92") * multiplier,
        expected_roi=Decimal("0.2996"),
        probability_any_payout=Decimal("0.36"),
    )


def test_domain_rejects_ticket_count_above_exact_stress_bound() -> None:
    with pytest.raises(ValueError):
        PortfolioConstraints(
            preferred_max_tickets=MAX_EXACT_STRESS_TICKETS + 1,
            absolute_max_tickets=MAX_EXACT_STRESS_TICKETS + 1,
        )


def test_risk_report_calculates_cash_and_top_level_exposure() -> None:
    tickets = (
        _ticket(1, 600, "match-1", "match-2"),
        _ticket(2, 400, "match-1", "match-3"),
    )
    portfolio = Portfolio(
        portfolio_id="portfolio-risk",
        analysis_run_id="run-risk",
        budget_fen=1_200,
        tickets=tickets,
        total_stake_fen=1_000,
        unused_budget_fen=200,
        cash_position=CashPosition(
            position_id=stable_id("cash", "portfolio-risk"), amount_fen=200
        ),
        status=PortfolioStatus.RECOMMENDED,
        constraints=CONSTRAINTS,
    )

    report = analyze_portfolio_risk(portfolio)

    assert report.cash_fen == 200
    assert report.cash_ratio == Decimal("0.166666666667")
    assert report.max_single_ticket_exposure_fen == 600
    assert report.max_match_exposure_fen == 1_000
    exposure_by_match = {item.match_id: item for item in report.match_exposures}
    assert exposure_by_match["match-1"].budget_ratio == Decimal("0.833333333333")
    assert exposure_by_match["match-1"].ticket_ids == ("ticket-1", "ticket-2")

    top_failure = report.stress_results[0]
    assert top_failure.scenario_key == "TOP_EXPOSURE_MATCH_ADVERSE"
    assert top_failure.scenario_exposed_stake_fen == 1_000
    assert top_failure.profit_loss_fen == -1_000
    assert all(
        result.state == StressTicketState.LOST
        for result in top_failure.ticket_results
    )


def test_all_cash_and_zero_budget_are_valid_risk_choices() -> None:
    for budget in (0, 10_000):
        portfolio = Portfolio(
            portfolio_id=f"portfolio-cash-{budget}",
            analysis_run_id="run-risk",
            budget_fen=budget,
            tickets=(),
            total_stake_fen=0,
            unused_budget_fen=budget,
            cash_position=CashPosition(
                position_id=stable_id("cash", budget), amount_fen=budget
            ),
            status=PortfolioStatus.NO_BET,
            no_bet_reason="NO_BET_NO_VALUE",
            constraints=CONSTRAINTS,
        )

        report = analyze_portfolio_risk(portfolio)

        assert report.match_exposures == ()
        assert report.total_stake_at_risk_fen == 0
        assert report.stress_results[0].scenario_key == "CASH_BASELINE"
        assert report.stress_results[0].profit_loss_fen == 0
        assert report.cash_ratio == (None if budget == 0 else Decimal("1.000000000000"))
        assert report.stress_results[0].capital_recovery_ratio == (
            None if budget == 0 else Decimal("1.000000000000")
        )


def test_adverse_complete_scenario_accounts_for_opposing_selections() -> None:
    tickets = (
        _ticket(1, 600, "match-1", "match-2", SelectionKey.HOME_WIN),
        _ticket(2, 200, "match-1", "match-2", SelectionKey.DRAW),
        _ticket(3, 400, "match-1", "match-2", SelectionKey.AWAY_WIN),
    )
    portfolio = Portfolio(
        portfolio_id="portfolio-opposing",
        analysis_run_id="run-risk",
        budget_fen=1_200,
        tickets=tickets,
        total_stake_fen=1_200,
        unused_budget_fen=0,
        cash_position=CashPosition(
            position_id=stable_id("cash", "portfolio-opposing"), amount_fen=0
        ),
        status=PortfolioStatus.RECOMMENDED,
        constraints=CONSTRAINTS,
    )

    report = analyze_portfolio_risk(portfolio)
    complete = next(
        result
        for result in report.stress_results
        if result.scenario_key == "ALL_EXPOSED_MATCHES_ADVERSE"
    )

    assert complete.is_complete
    assert complete.scenario_exposed_stake_fen == 1_200
    assert complete.ending_capital_fen == 0
    assert complete.profit_loss_fen == -1_200


def test_adverse_search_remains_exact_above_sixteen_matches() -> None:
    def ticket(
        number: int,
        stake: int,
        payout: int,
        first_match: str,
        first_selection: SelectionKey,
        second_match: str,
        second_selection: SelectionKey,
    ) -> TicketAllocation:
        base = _ticket(number, stake, first_match, second_match, first_selection)
        second_leg = base.candidate.legs[1].model_copy(
            update={"selection": second_selection}
        )
        return base.model_copy(
            update={
                "candidate": base.candidate.model_copy(
                    update={"legs": (base.candidate.legs[0], second_leg)}
                ),
                "potential_gross_payout_fen": payout,
            }
        )

    tickets = (
        ticket(1, 200, 400, "A", SelectionKey.DRAW, "B", SelectionKey.DRAW),
        ticket(2, 200, 400, "A", SelectionKey.DRAW, "B", SelectionKey.AWAY_WIN),
        ticket(3, 200, 1_000, "A", SelectionKey.AWAY_WIN, "B", SelectionKey.DRAW),
        ticket(4, 600, 2_166, "A", SelectionKey.HOME_WIN, "C", SelectionKey.HOME_WIN),
        ticket(5, 600, 2_166, "B", SelectionKey.HOME_WIN, "D", SelectionKey.HOME_WIN),
        *(
            ticket(
                number,
                200,
                722,
                f"X{number}",
                SelectionKey.HOME_WIN,
                f"Y{number}",
                SelectionKey.HOME_WIN,
            )
            for number in range(6, 13)
        ),
    )
    portfolio = Portfolio(
        portfolio_id="portfolio-many-matches",
        analysis_run_id="run-risk",
        budget_fen=3_200,
        tickets=tickets,
        total_stake_fen=3_200,
        unused_budget_fen=0,
        cash_position=CashPosition(position_id="cash-many-matches", amount_fen=0),
        status=PortfolioStatus.RECOMMENDED,
        constraints=PortfolioConstraints(
            preferred_max_tickets=12,
            absolute_max_tickets=12,
        ),
    )

    report = analyze_portfolio_risk(portfolio)
    complete = next(
        result
        for result in report.stress_results
        if result.scenario_key == "ALL_EXPOSED_MATCHES_ADVERSE"
    )

    assert len(complete.outcomes) == 18
    assert complete.ending_capital_fen == 0
    assert complete.profit_loss_fen == -3_200
    assert complete.policy_version == "DETERMINISTIC_PORTFOLIO_STRESS_V2"
