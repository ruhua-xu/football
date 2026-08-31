from datetime import datetime, timezone
from decimal import Decimal

import pytest

from football_system.domain.betting import CandidateStatus, SportteryRules
from football_system.domain.common import stable_id
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayProbability,
    UnsupportedMarketError,
)
from football_system.domain.match import (
    FixedBonusQuote,
    SaleStatus,
    SportteryBonusSnapshot,
)
from football_system.domain.prediction import FinalPrediction, FusionPolicyName
from football_system.domain.services.betting import (
    build_selection_candidates,
    build_two_leg_ticket_candidates,
)

NOW = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)
RULES = SportteryRules(
    version="TEST_V1",
    base_stake_fen=200,
    max_multiplier=50,
    max_ticket_stake_fen=600_000,
)


def final_prediction(match_id: str) -> FinalPrediction:
    return FinalPrediction(
        prediction_id=f"final-{match_id}",
        analysis_run_id="run-1",
        match_id=match_id,
        market=MARKET,
        probabilities=ThreeWayProbability(
            home_win=Decimal("0.60"),
            draw=Decimal("0.25"),
            away_win=Decimal("0.15"),
        ),
        quant_prediction_id=f"quant-{match_id}",
        fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
        fusion_config_json="{}",
        generated_at_utc=NOW,
    )


def bonus_snapshot(match_id: str) -> SportteryBonusSnapshot:
    return SportteryBonusSnapshot(
        snapshot_id=f"bonus-{match_id}",
        match_id=match_id,
        provider_code="MOCK_SPORTTERY",
        sporttery_match_no=match_id,
        market=MARKET,
        quotes=(
            FixedBonusQuote(selection=SelectionKey.HOME_WIN, fixed_bonus=Decimal("1.90")),
            FixedBonusQuote(selection=SelectionKey.DRAW, fixed_bonus=Decimal("3.40")),
            FixedBonusQuote(selection=SelectionKey.AWAY_WIN, fixed_bonus=Decimal("5.20")),
        ),
        sale_status=SaleStatus.OPEN,
        captured_at_utc=NOW,
        available_at_utc=NOW,
        ingested_at_utc=NOW,
        source_snapshot_key=f"source-{match_id}",
        payload_hash=stable_id("payload", match_id),
    )


def test_selection_candidates_include_rejection_reasons() -> None:
    candidates = build_selection_candidates(
        final_prediction("match-1"), bonus_snapshot("match-1"), Decimal("0.02")
    )

    assert len(candidates) == 3
    assert candidates[0].selection == SelectionKey.HOME_WIN
    assert candidates[0].ev == Decimal("0.1400")
    assert candidates[0].status == CandidateStatus.ELIGIBLE
    assert all(candidate.rejection_code == "EV_BELOW_THRESHOLD" for candidate in candidates[1:])


def test_three_matches_generate_three_unique_two_leg_candidates() -> None:
    selections = tuple(
        build_selection_candidates(
            final_prediction(f"match-{index}"),
            bonus_snapshot(f"match-{index}"),
            Decimal("0.02"),
        )[0]
        for index in range(1, 4)
    )

    tickets = build_two_leg_ticket_candidates(selections, RULES, Decimal("0.02"))

    assert len(tickets) == 3
    assert len({ticket.ticket_candidate_id for ticket in tickets}) == 3
    assert all(ticket.legs[0].match_id != ticket.legs[1].match_id for ticket in tickets)


def test_betting_rejects_unsupported_market_and_negative_threshold() -> None:
    unsupported = MarketKey(
        market_type=MarketType.HANDICAP_THREE_WAY,
        handicap_value=Decimal("-1"),
    )
    with pytest.raises(UnsupportedMarketError):
        build_selection_candidates(
            final_prediction("match-1").model_copy(update={"market": unsupported}),
            bonus_snapshot("match-1").model_copy(update={"market": unsupported}),
            Decimal("0.02"),
        )
    with pytest.raises(ValueError, match="negative"):
        build_selection_candidates(
            final_prediction("match-1"), bonus_snapshot("match-1"), Decimal("-0.01")
        )
