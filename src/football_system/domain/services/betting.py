from __future__ import annotations

from decimal import Decimal
from itertools import combinations

from football_system.domain.betting import (
    CandidateStatus,
    SelectionCandidate,
    SportteryRules,
    TicketCandidate,
)
from football_system.domain.common import stable_id
from football_system.domain.market import MarketType, UnsupportedMarketError
from football_system.domain.match import SaleStatus, SportteryBonusSnapshot
from football_system.domain.prediction import FinalPrediction
from football_system.domain.services.payout import official_gross_payout_fen
from football_system.domain.services.probability import (
    quantize_metric,
    quantize_probability,
    selection_ev,
)


def build_selection_candidates(
    prediction: FinalPrediction,
    bonus_snapshot: SportteryBonusSnapshot,
    min_ev: Decimal,
) -> tuple[SelectionCandidate, ...]:
    if prediction.market.market_type != MarketType.THREE_WAY:
        raise UnsupportedMarketError(
            f"MVP betting does not support {prediction.market.canonical}"
        )
    if min_ev < 0:
        raise ValueError("min_ev cannot be negative")
    if prediction.match_id != bonus_snapshot.match_id:
        raise ValueError("prediction and fixed bonus reference different matches")
    if prediction.market != bonus_snapshot.market:
        raise ValueError("prediction and fixed bonus reference different markets")
    bonus = bonus_snapshot.three_way_bonus()
    candidates: list[SelectionCandidate] = []
    for selection, probability in prediction.probabilities.items():
        fixed_bonus = bonus.for_selection(selection)
        ev = selection_ev(probability, fixed_bonus)
        rejection_code: str | None = None
        if bonus_snapshot.sale_status != SaleStatus.OPEN:
            rejection_code = "SALE_NOT_OPEN"
        elif ev < min_ev:
            rejection_code = "EV_BELOW_THRESHOLD"
        status = CandidateStatus.REJECTED if rejection_code else CandidateStatus.ELIGIBLE
        candidates.append(
            SelectionCandidate(
                candidate_id=stable_id(
                    "selection",
                    prediction.analysis_run_id,
                    prediction.match_id,
                    prediction.market.canonical,
                    selection,
                ),
                analysis_run_id=prediction.analysis_run_id,
                match_id=prediction.match_id,
                market=prediction.market,
                selection=selection,
                final_prediction_id=prediction.prediction_id,
                sporttery_bonus_snapshot_id=bonus_snapshot.snapshot_id,
                probability=probability,
                fixed_bonus=fixed_bonus,
                break_even_probability=quantize_probability(
                    Decimal(1) / fixed_bonus
                ),
                ev=ev,
                status=status,
                rejection_code=rejection_code,
            )
        )
    return tuple(candidates)


def build_two_leg_ticket_candidates(
    selection_candidates: tuple[SelectionCandidate, ...],
    rules: SportteryRules,
    min_ticket_roi: Decimal,
) -> tuple[TicketCandidate, ...]:
    if min_ticket_roi < 0:
        raise ValueError("min_ticket_roi cannot be negative")
    eligible = [
        candidate
        for candidate in selection_candidates
        if candidate.status == CandidateStatus.ELIGIBLE
    ]
    ticket_candidates: list[TicketCandidate] = []
    for left, right in combinations(eligible, 2):
        if (
            left.market.market_type != MarketType.THREE_WAY
            or right.market.market_type != MarketType.THREE_WAY
        ):
            raise UnsupportedMarketError("MVP ticket generation only supports THREE_WAY")
        if left.match_id == right.match_id:
            continue
        if left.analysis_run_id != right.analysis_run_id:
            raise ValueError("cannot combine candidates from different analysis runs")
        legs = tuple(sorted((left, right), key=lambda item: item.candidate_id))
        joint_probability = quantize_probability(
            left.probability * right.probability
        )
        gross_payout_fen = official_gross_payout_fen(
            (left.fixed_bonus, right.fixed_bonus), rules
        )
        expected_gross = quantize_metric(
            joint_probability * Decimal(gross_payout_fen)
        )
        expected_profit = quantize_metric(
            expected_gross - Decimal(rules.base_stake_fen)
        )
        expected_roi = quantize_metric(
            expected_profit / Decimal(rules.base_stake_fen)
        )
        if expected_roi < min_ticket_roi:
            continue
        ticket_candidates.append(
            TicketCandidate(
                ticket_candidate_id=stable_id(
                    "2x1", left.analysis_run_id, legs[0].candidate_id, legs[1].candidate_id
                ),
                analysis_run_id=left.analysis_run_id,
                legs=legs,
                base_stake_fen=rules.base_stake_fen,
                joint_probability=joint_probability,
                gross_payout_fen=gross_payout_fen,
                expected_gross_payout_fen=expected_gross,
                expected_profit_fen=expected_profit,
                expected_roi=expected_roi,
                payout_policy_version=rules.version,
            )
        )
    return tuple(
        sorted(
            ticket_candidates,
            key=lambda item: (-item.expected_roi, item.ticket_candidate_id),
        )
    )
