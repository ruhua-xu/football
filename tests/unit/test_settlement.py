from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from football_system.domain.market import SelectionKey
from football_system.domain.settlement import (
    MatchResult,
    Settlement,
    SettlementScopeKind,
    SettlementStatus,
)

OBSERVED = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
AVAILABLE = OBSERVED + timedelta(minutes=1)
INGESTED = OBSERVED + timedelta(minutes=2)


def match_result(home_goals: int = 2, away_goals: int = 1, **updates) -> MatchResult:
    values = {
        "match_result_id": "result-1",
        "match_id": "match-1",
        "provider_code": "HISTORICAL_TEST",
        "home_goals": home_goals,
        "away_goals": away_goals,
        "observed_at_utc": OBSERVED,
        "available_at_utc": AVAILABLE,
        "ingested_at_utc": INGESTED,
        "source_result_key": "source-result-1",
        "payload_hash": "result-payload-hash",
    }
    values.update(updates)
    return MatchResult(**values)


def settlement(**updates) -> Settlement:
    values = {
        "settlement_id": "settlement-1",
        "scope_kind": SettlementScopeKind.ANALYSIS_RUN,
        "parent_analysis_run_id": "run-1",
        "decision_scope_id": "run-1",
        "portfolio_id": "portfolio-1",
        "ticket_id": "ticket-1",
        "match_result_ids": ("result-1", "result-2"),
        "status": SettlementStatus.WON,
        "stake_fen": 200,
        "gross_payout_fen": 760,
        "profit_loss_fen": 560,
        "payout_policy_version": "SPORTTERY_MVP_V1",
        "settlement_policy_version": "TWO_FOLD_ONE_SETTLEMENT_V1",
        "settled_at_utc": INGESTED,
    }
    values.update(updates)
    return Settlement(**values)


@pytest.mark.parametrize(
    ("home_goals", "away_goals", "selection"),
    (
        (2, 1, SelectionKey.HOME_WIN),
        (1, 1, SelectionKey.DRAW),
        (0, 3, SelectionKey.AWAY_WIN),
    ),
)
def test_match_result_derives_regulation_time_three_way_selection(
    home_goals: int,
    away_goals: int,
    selection: SelectionKey,
) -> None:
    assert match_result(home_goals, away_goals).three_way_selection() == selection


def test_match_result_requires_monotonic_timestamps_and_supersession() -> None:
    with pytest.raises(ValidationError, match="timestamps must follow"):
        match_result(available_at_utc=INGESTED, ingested_at_utc=AVAILABLE)
    with pytest.raises(ValidationError, match="cannot supersede itself"):
        match_result(supersedes_match_result_id="result-1")


def test_settlement_enforces_won_and_lost_financials() -> None:
    won = settlement()
    lost = settlement(
        status=SettlementStatus.LOST,
        gross_payout_fen=0,
        profit_loss_fen=-200,
    )

    assert won.settlement_kind == "BACKTEST"
    assert won.profit_loss_fen == 560
    assert lost.profit_loss_fen == -200

    with pytest.raises(ValidationError, match="winning settlement"):
        settlement(gross_payout_fen=0, profit_loss_fen=-200)
    with pytest.raises(ValidationError, match="losing settlement"):
        settlement(status=SettlementStatus.LOST)
    with pytest.raises(ValidationError, match="profit/loss is inconsistent"):
        settlement(profit_loss_fen=559)


def test_settlement_requires_exactly_two_unique_match_results() -> None:
    with pytest.raises(ValidationError, match="2X1 settlement"):
        settlement(match_result_ids=("result-1", "result-1"))
    with pytest.raises(ValidationError, match="Tuple should have at most 2 items"):
        settlement(match_result_ids=("result-1", "result-2", "result-3"))


def test_settlement_distinguishes_analysis_and_revision_scopes() -> None:
    revision = settlement(
        scope_kind=SettlementScopeKind.PORTFOLIO_REVISION,
        decision_scope_id="revision-1",
    )

    assert revision.parent_analysis_run_id == "run-1"
    assert revision.decision_scope_id == "revision-1"

    with pytest.raises(ValidationError, match="base settlement scope"):
        settlement(decision_scope_id="revision-1")
    with pytest.raises(ValidationError, match="distinct decision scope"):
        settlement(scope_kind=SettlementScopeKind.PORTFOLIO_REVISION)


def test_settlement_rejects_self_supersession() -> None:
    with pytest.raises(ValidationError, match="cannot supersede itself"):
        settlement(supersedes_settlement_id="settlement-1")
