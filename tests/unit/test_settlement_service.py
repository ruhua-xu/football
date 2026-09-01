from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from football_system.application.settlement import (
    SettlementLineageError,
    SettlementService,
)
from football_system.domain.analysis import AnalysisRun, AnalysisRunStatus
from football_system.domain.betting import (
    CandidateStatus,
    CashPosition,
    NoBetReason,
    Portfolio,
    PortfolioConstraints,
    PortfolioStatus,
    SelectionCandidate,
    TicketAllocation,
    TicketCandidate,
)
from football_system.domain.market import MarketKey, MarketType, SelectionKey
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    SettlementResultReason,
    SettlementScope,
    SettlementStatus,
    UnsupportedSettlementReason,
)

OBSERVED = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
AVAILABLE = OBSERVED + timedelta(minutes=1)
INGESTED = OBSERVED + timedelta(minutes=2)
SETTLED_AT = OBSERVED + timedelta(hours=1)
THREE_WAY = MarketKey(market_type=MarketType.THREE_WAY)
CONSTRAINTS = PortfolioConstraints()


def _ticket(
    ticket_no: int = 1,
    *,
    scope_id: str = "run-1",
    match_ids: tuple[str, str] = ("match-home", "match-draw"),
    selections: tuple[SelectionKey, SelectionKey] = (
        SelectionKey.HOME_WIN,
        SelectionKey.DRAW,
    ),
    market: MarketKey = THREE_WAY,
    potential_gross_payout_fen: int = 12_160,
) -> TicketAllocation:
    bonuses = (Decimal("1.90"), Decimal("3.20"))
    legs = tuple(
        SelectionCandidate(
            candidate_id=f"selection-{ticket_no}-{index}",
            analysis_run_id=scope_id,
            match_id=match_id,
            market=market,
            selection=selection,
            final_prediction_id=f"prediction-{ticket_no}-{index}",
            sporttery_bonus_snapshot_id=f"bonus-{ticket_no}-{index}",
            probability=Decimal("0.5"),
            fixed_bonus=bonuses[index],
            break_even_probability=Decimal(1) / bonuses[index],
            ev=Decimal("0.1"),
            status=CandidateStatus.ELIGIBLE,
        )
        for index, (match_id, selection) in enumerate(
            zip(match_ids, selections, strict=True)
        )
    )
    candidate = TicketCandidate(
        ticket_candidate_id=f"ticket-candidate-{ticket_no}",
        analysis_run_id=scope_id,
        legs=legs,
        base_stake_fen=200,
        joint_probability=Decimal("0.25"),
        gross_payout_fen=1_216,
        expected_gross_payout_fen=Decimal("304"),
        expected_profit_fen=Decimal("104"),
        expected_roi=Decimal("0.52"),
        payout_policy_version="FROZEN_PAYOUT_V1",
    )
    return TicketAllocation(
        ticket_id=f"ticket-{ticket_no}",
        ticket_no=ticket_no,
        candidate=candidate,
        multiplier=10,
        stake_fen=2_000,
        potential_gross_payout_fen=potential_gross_payout_fen,
        expected_gross_payout_fen=Decimal("3040"),
        expected_profit_fen=Decimal("1040"),
        expected_roi=Decimal("0.52"),
        probability_any_payout=Decimal("0.25"),
    )


def _portfolio(
    tickets: tuple[TicketAllocation, ...],
    *,
    scope_id: str = "run-1",
    budget_fen: int = 6_000,
    portfolio_id: str = "portfolio-1",
) -> Portfolio:
    deployed = sum(ticket.stake_fen for ticket in tickets)
    cash = budget_fen - deployed
    return Portfolio(
        portfolio_id=portfolio_id,
        analysis_run_id=scope_id,
        budget_fen=budget_fen,
        tickets=tickets,
        total_stake_fen=deployed,
        unused_budget_fen=cash,
        cash_position=CashPosition(position_id=f"cash-{portfolio_id}", amount_fen=cash),
        status=PortfolioStatus.RECOMMENDED if tickets else PortfolioStatus.NO_BET,
        no_bet_reason=None if tickets else NoBetReason.NO_BET_NO_VALUE,
        constraints=CONSTRAINTS,
    )


def _result(
    match_id: str,
    home_goals: int,
    away_goals: int,
    *,
    version: int = 1,
    supersedes: str | None = None,
) -> MatchResult:
    return MatchResult(
        match_result_id=f"result-{match_id}-v{version}",
        match_id=match_id,
        provider_code="HISTORICAL_TEST",
        home_goals=home_goals,
        away_goals=away_goals,
        observed_at_utc=OBSERVED,
        available_at_utc=AVAILABLE,
        ingested_at_utc=INGESTED,
        source_result_key=f"source-{match_id}-v{version}",
        payload_hash=f"payload-{match_id}-v{version}",
        supersedes_match_result_id=supersedes,
    )


def test_golden_two_fold_ticket_uses_frozen_stake_payout_and_policy() -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))
    scope = SettlementScope.for_analysis_run("run-1")
    results = (
        _result("match-home", 2, 1),
        _result("match-draw", 1, 1),
    )

    outcome = SettlementService().settle_ticket(
        scope,
        portfolio,
        ticket,
        results,
        SETTLED_AT,
    )
    repeated = SettlementService().settle_ticket(
        scope,
        portfolio,
        ticket,
        results,
        SETTLED_AT,
    )

    assert outcome.reason == SettlementResultReason.SETTLED
    assert outcome.settlement is not None
    assert outcome.settlement.status == SettlementStatus.WON
    assert outcome.settlement.stake_fen == 2_000
    assert outcome.settlement.gross_payout_fen == 12_160
    assert outcome.settlement.profit_loss_fen == 10_160
    assert outcome.settlement.payout_policy_version == "FROZEN_PAYOUT_V1"
    assert repeated == outcome


def test_any_failed_leg_loses_the_whole_frozen_ticket() -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))

    outcome = SettlementService().settle_ticket(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        ticket,
        (_result("match-home", 2, 1), _result("match-draw", 0, 1)),
        SETTLED_AT,
    )

    assert outcome.settlement is not None
    assert outcome.settlement.status == SettlementStatus.LOST
    assert outcome.settlement.gross_payout_fen == 0
    assert outcome.settlement.profit_loss_fen == -2_000


def test_missing_and_unsupported_results_do_not_fabricate_settlement() -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))
    service = SettlementService()
    scope = SettlementScope.for_analysis_run("run-1")

    missing = service.settle_ticket(
        scope,
        portfolio,
        ticket,
        (_result("match-home", 2, 1),),
        SETTLED_AT,
    )
    cancelled = service.settle_ticket(
        scope,
        portfolio,
        ticket,
        (_result("match-home", 2, 1),),
        SETTLED_AT,
        result_issues=(
            MatchSettlementIssue(
                match_id="match-draw",
                reason=UnsupportedSettlementReason.CANCELLATION,
            ),
        ),
    )

    assert missing.reason == SettlementResultReason.MISSING_RESULT
    assert missing.coverage.missing_match_ids == ("match-draw",)
    assert missing.settlement is None
    assert cancelled.reason == SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
    assert cancelled.coverage.issues[0].reason == (
        UnsupportedSettlementReason.CANCELLATION
    )
    assert cancelled.settlement is None


@pytest.mark.parametrize(
    "unsupported_reason",
    (
        UnsupportedSettlementReason.CANCELLATION,
        UnsupportedSettlementReason.ABANDONMENT,
        UnsupportedSettlementReason.VOID,
        UnsupportedSettlementReason.REFUND,
        UnsupportedSettlementReason.EXTRA_TIME,
        UnsupportedSettlementReason.PENALTY,
        UnsupportedSettlementReason.DEGRADE,
    ),
)
def test_unsupported_match_semantics_return_the_conservative_result(
    unsupported_reason: UnsupportedSettlementReason,
) -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))

    outcome = SettlementService().settle_ticket(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        ticket,
        (_result("match-home", 2, 1),),
        SETTLED_AT,
        result_issues=(
            MatchSettlementIssue(
                match_id="match-draw",
                reason=unsupported_reason,
            ),
        ),
    )

    assert outcome.reason == SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
    assert outcome.settlement is None


def test_non_three_way_or_non_backtest_case_is_explicitly_unsupported() -> None:
    handicap = MarketKey(
        market_type=MarketType.HANDICAP_THREE_WAY,
        handicap_value=Decimal("1"),
    )
    ticket = _ticket(market=handicap)
    portfolio = _portfolio((ticket,))
    results = (_result("match-home", 2, 1), _result("match-draw", 1, 1))
    service = SettlementService()

    market_outcome = service.settle_ticket(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        ticket,
        results,
        SETTLED_AT,
    )
    three_way_ticket = _ticket()
    three_way_portfolio = _portfolio((three_way_ticket,))
    live_outcome = service.settle_ticket(
        SettlementScope.for_analysis_run("run-1"),
        three_way_portfolio,
        three_way_ticket,
        results,
        SETTLED_AT,
        settlement_kind="LIVE",
    )

    assert market_outcome.reason == (SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE)
    assert market_outcome.coverage.unsupported_reasons == (
        UnsupportedSettlementReason.UNSUPPORTED_MARKET,
    )
    assert live_outcome.coverage.unsupported_reasons == (
        UnsupportedSettlementReason.UNSUPPORTED_SETTLEMENT_KIND,
    )


def test_two_ticket_portfolio_with_forty_yuan_cash_is_manually_reconciled() -> None:
    winner = _ticket(1)
    loser = _ticket(
        2,
        match_ids=("match-home-2", "match-draw-2"),
        potential_gross_payout_fen=9_000,
    )
    portfolio = _portfolio((winner, loser), budget_fen=8_000)
    results = (
        _result("match-home", 2, 1),
        _result("match-draw", 1, 1),
        _result("match-home-2", 0, 1),
        _result("match-draw-2", 1, 1),
    )

    outcome = SettlementService().settle_portfolio(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        results,
        SETTLED_AT,
    )

    assert outcome.reason == SettlementResultReason.SETTLED
    aggregate = outcome.portfolio_settlement
    assert aggregate is not None
    assert aggregate.budget_fen == 8_000
    assert aggregate.deployed_stake_fen == 4_000
    assert aggregate.original_cash_fen == 4_000
    assert aggregate.gross_ticket_payout_fen == 12_160
    assert aggregate.ending_capital_fen == 16_160
    assert aggregate.profit_loss_fen == 8_160
    assert aggregate.roi_on_budget == Decimal("1.02")
    assert aggregate.roi_on_deployed == Decimal("2.04")


def test_incomplete_portfolio_reports_coverage_without_partial_aggregation() -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))

    outcome = SettlementService().settle_portfolio(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        (_result("match-home", 2, 1),),
        SETTLED_AT,
    )

    assert outcome.reason == SettlementResultReason.MISSING_RESULT
    assert outcome.missing_match_ids == ("match-draw",)
    assert outcome.portfolio_settlement is None


@pytest.mark.parametrize(
    ("budget_fen", "roi_on_budget"),
    ((0, None), (4_000, Decimal(0))),
)
def test_all_cash_portfolio_handles_zero_roi_denominators_explicitly(
    budget_fen: int,
    roi_on_budget: Decimal | None,
) -> None:
    portfolio = _portfolio((), budget_fen=budget_fen)

    outcome = SettlementService().settle_portfolio(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        (),
        SETTLED_AT,
    )

    aggregate = outcome.portfolio_settlement
    assert aggregate is not None
    assert aggregate.roi_on_budget == roi_on_budget
    assert aggregate.roi_on_deployed is None
    assert aggregate.ending_capital_fen == budget_fen
    assert aggregate.profit_loss_fen == 0


def test_all_cash_portfolio_reports_unsupported_kind_without_an_aggregate() -> None:
    portfolio = _portfolio((), budget_fen=4_000)

    outcome = SettlementService().settle_portfolio(
        SettlementScope.for_analysis_run("run-1"),
        portfolio,
        (),
        SETTLED_AT,
        settlement_kind="LIVE",
    )

    assert outcome.reason == SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
    assert outcome.unsupported_reasons == (
        UnsupportedSettlementReason.UNSUPPORTED_SETTLEMENT_KIND,
    )
    assert outcome.portfolio_settlement is None


def test_result_correction_appends_v2_without_mutating_v1() -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))
    scope = SettlementScope.for_analysis_run("run-1")
    service = SettlementService()
    home = _result("match-home", 2, 1)
    draw_v1 = _result("match-draw", 1, 1)
    v1 = service.settle_ticket(
        scope,
        portfolio,
        ticket,
        (home, draw_v1),
        SETTLED_AT,
    ).settlement
    assert v1 is not None
    draw_v2 = _result(
        "match-draw",
        1,
        2,
        version=2,
        supersedes=draw_v1.match_result_id,
    )

    corrected = service.correct_ticket_settlement(
        scope,
        portfolio,
        ticket,
        (home, draw_v2),
        SETTLED_AT + timedelta(minutes=1),
        v1,
    )

    assert corrected.settlement is not None
    assert corrected.settlement.settlement_id != v1.settlement_id
    assert corrected.settlement.supersedes_settlement_id == v1.settlement_id
    assert corrected.settlement.status == SettlementStatus.LOST
    assert corrected.settlement.gross_payout_fen == 0
    assert v1.status == SettlementStatus.WON
    assert v1.supersedes_settlement_id is None


def test_wrong_scope_match_and_result_correction_lineage_are_rejected() -> None:
    ticket = _ticket()
    portfolio = _portfolio((ticket,))
    scope = SettlementScope.for_analysis_run("run-1")
    service = SettlementService()
    home = _result("match-home", 2, 1)
    draw = _result("match-draw", 1, 1)

    with pytest.raises(SettlementLineageError, match="decision scope"):
        service.settle_ticket(
            SettlementScope.for_analysis_run("run-2"),
            portfolio,
            ticket,
            (home, draw),
            SETTLED_AT,
        )
    with pytest.raises(SettlementLineageError, match="wrong ticket match"):
        service.settle_ticket(
            scope,
            portfolio,
            ticket,
            (home, _result("wrong-match", 1, 1)),
            SETTLED_AT,
        )

    v1 = service.settle_ticket(
        scope,
        portfolio,
        ticket,
        (home, draw),
        SETTLED_AT,
    ).settlement
    assert v1 is not None
    unrelated_v2 = _result("match-draw", 0, 1, version=2)
    with pytest.raises(SettlementLineageError, match="does not supersede"):
        service.correct_ticket_settlement(
            scope,
            portfolio,
            ticket,
            (home, unrelated_v2),
            SETTLED_AT + timedelta(minutes=1),
            v1,
        )


def test_portfolio_correction_supersedes_ticket_and_portfolio_versions() -> None:
    first = _ticket(1)
    second = _ticket(2, match_ids=("match-home-2", "match-draw-2"))
    portfolio = _portfolio((first, second), budget_fen=8_000)
    scope = SettlementScope.for_analysis_run("run-1")
    service = SettlementService()
    draw_v1 = _result("match-draw", 1, 1)
    initial_results = (
        _result("match-home", 2, 1),
        draw_v1,
        _result("match-home-2", 2, 1),
        _result("match-draw-2", 1, 1),
    )
    initial = service.settle_portfolio(
        scope,
        portfolio,
        initial_results,
        SETTLED_AT,
    )
    assert initial.portfolio_settlement is not None
    previous_tickets = tuple(
        result.settlement
        for result in initial.ticket_results
        if result.settlement is not None
    )
    draw_v2 = _result(
        "match-draw",
        0,
        1,
        version=2,
        supersedes=draw_v1.match_result_id,
    )
    corrected_results = (initial_results[0], draw_v2, *initial_results[2:])

    corrected = service.settle_portfolio(
        scope,
        portfolio,
        corrected_results,
        SETTLED_AT + timedelta(minutes=1),
        previous_ticket_settlements=previous_tickets,
        supersedes_portfolio_settlement=initial.portfolio_settlement,
    )

    assert corrected.portfolio_settlement is not None
    assert corrected.portfolio_settlement.supersedes_portfolio_settlement_id == (
        initial.portfolio_settlement.portfolio_settlement_id
    )
    assert corrected.ticket_results[0].settlement is not None
    assert corrected.ticket_results[0].settlement.supersedes_settlement_id == (
        previous_tickets[0].settlement_id
    )
    assert corrected.ticket_results[1].settlement == previous_tickets[1]


@dataclass(frozen=True)
class _RevisionScope:
    portfolio_revision_id: str
    parent_analysis_run_id: str
    portfolios: tuple[Portfolio, ...]


def test_analysis_run_and_portfolio_revision_scope_interfaces() -> None:
    run_ticket = _ticket()
    run_portfolio = _portfolio((run_ticket,))
    run = AnalysisRun(
        analysis_run_id="run-1",
        as_of_at_utc=OBSERVED - timedelta(hours=1),
        status=AnalysisRunStatus.COMPLETED,
        started_at_utc=OBSERVED,
        completed_at_utc=OBSERVED,
        pipeline_version="test",
        code_revision="test",
        config_json="{}",
        config_hash="config-hash",
        input_manifest_json="{}",
        input_manifest_hash="manifest-hash",
    )
    results = (_result("match-home", 2, 1), _result("match-draw", 1, 1))
    service = SettlementService()

    base = service.settle_analysis_run(run, run_portfolio, results, SETTLED_AT)

    revision_ticket = _ticket(scope_id="revision-1")
    revision_portfolio = _portfolio(
        (revision_ticket,),
        scope_id="revision-1",
        portfolio_id="revision-portfolio",
    )
    revision = _RevisionScope("revision-1", "run-1", (revision_portfolio,))
    revised = service.settle_portfolio_revision(
        revision,
        revision_portfolio,
        results,
        SETTLED_AT,
    )

    assert base.portfolio_settlement is not None
    assert base.portfolio_settlement.decision_scope_id == "run-1"
    assert revised.portfolio_settlement is not None
    assert revised.portfolio_settlement.parent_analysis_run_id == "run-1"
    assert revised.portfolio_settlement.decision_scope_id == "revision-1"
