from datetime import timedelta

import pytest

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.betting import PassType
from football_system.domain.services.strategy_pass import build_strategy_plan
from football_system.domain.services.strategy_settlement import settle_strategy_plan
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    UnsupportedSettlementReason,
)
from football_system.domain.strategy_pass import StrategyProfileV1
from football_system.domain.strategy_settlement import StrategySettlementV1
from tests.unit.test_strategy_pass import NOW, source

SETTLED = NOW + timedelta(days=3)


def results(plan, home_wins=None):
    matches = sorted({s.match_id for t in plan.tickets for s in t.candidate.selections})
    return tuple(
        MatchResult(
            match_result_id=f"result-{m}",
            match_id=m,
            provider_code="SYNTHETIC",
            home_goals=int(home_wins is None or m in home_wins),
            away_goals=0,
            observed_at_utc=SETTLED,
            available_at_utc=SETTLED,
            ingested_at_utc=SETTLED,
            source_result_key=m,
            payload_hash=match_result_payload_sha256(
                int(home_wins is None or m in home_wins), 0
            ),
        )
        for m in matches
    )


@pytest.mark.parametrize(
    "pass_type,n,count,payout",
    [
        (PassType.TWO_FOLD_ONE, 2, 1, 1200),
        (PassType.THREE_FOLD_FOUR, 3, 4, 10000),
        (PassType.FOUR_FOLD_ELEVEN, 4, 11, 69000),
    ],
)
@pytest.mark.parametrize("win_count", [0, 1, 2, 4])
def test_settlement_uses_exact_constituent_wins(pass_type, n, count, payout, win_count):
    p = build_strategy_plan(
        source(("2", "3", "4", "5")[:n], budget=200 * count * 2),
        StrategyProfileV1(pass_types=(pass_type,)),
    )
    r = results(p, {f"synthetic-{i}" for i in range(win_count)})
    s = settle_strategy_plan(p, r, SETTLED)
    t = s.ticket_results[0]
    assert t.stake_fen == 200 * count * 2
    assert t.gross_payout_fen == (
        payout * 2 if win_count >= n else 2400 if win_count == 2 else 0
    )
    assert len(t.atomic_settlements) == count
    assert s.ending_capital_fen == p.cash_fen + t.gross_payout_fen
    assert s.profit_loss_fen == t.gross_payout_fen - p.total_stake_fen
    assert StrategySettlementV1.model_validate_json(s.model_dump_json()) == s
    assert settle_strategy_plan(p, tuple(reversed(r)), SETTLED) == s


def test_missing_and_cancellation_do_not_invent_refund_or_loss():
    p = build_strategy_plan(
        source(("2", "3", "4")),
        StrategyProfileV1(pass_types=(PassType.THREE_FOLD_FOUR,)),
    )
    r = results(p)
    missing = settle_strategy_plan(p, r[:-1], SETTLED)
    assert missing.reason == "MISSING_RESULT" and missing.gross_payout_fen is None
    unsupported = settle_strategy_plan(
        p,
        r[:-1],
        SETTLED,
        issues=(
            MatchSettlementIssue(
                match_id=r[-1].match_id, reason=UnsupportedSettlementReason.CANCELLATION
            ),
        ),
    )
    assert (
        unsupported.reason == "UNSUPPORTED_SETTLEMENT_CASE"
        and unsupported.ending_capital_fen is None
    )
    assert not unsupported.ticket_results[0].atomic_settlements


def test_no_bet_settles_cash_without_result_or_profit():
    p = build_strategy_plan(source(budget=0), StrategyProfileV1())
    s = settle_strategy_plan(p, (), SETTLED)
    assert s.reason == "SETTLED" and s.profit_loss_fen == 0 and not s.ticket_results


def test_result_correction_is_append_only_direct_lineage():
    p = build_strategy_plan(source(("2", "3")), StrategyProfileV1())
    r = results(p)
    first = settle_strategy_plan(p, r, SETTLED)
    update = r[0].model_copy(
        update={
            "match_result_id": "corrected",
            "home_goals": 0,
            "payload_hash": match_result_payload_sha256(0, 0),
            "supersedes_match_result_id": r[0].match_result_id,
            "ingested_at_utc": SETTLED + timedelta(seconds=1),
        }
    )
    corrected = settle_strategy_plan(
        p, (update, r[1]), SETTLED + timedelta(seconds=1), previous=first
    )
    assert corrected.gross_payout_fen == 0 and first.gross_payout_fen > 0
    assert corrected.supersedes_settlement_id == first.settlement_id
    with pytest.raises(ValueError, match="directly supersede"):
        settle_strategy_plan(
            p,
            (update.model_copy(update={"supersedes_match_result_id": None}), r[1]),
            SETTLED + timedelta(seconds=1),
            previous=first,
        )
    with pytest.raises(ValueError, match="cutoff"):
        settle_strategy_plan(p, (update, r[1]), SETTLED)
    with pytest.raises(ValueError, match="lineage"):
        settle_strategy_plan(p, (r[0], r[0]), SETTLED)
