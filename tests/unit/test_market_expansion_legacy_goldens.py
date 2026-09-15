from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from football_system.application.post_review import _apply_review_probability
from football_system.domain.betting import PassType
from football_system.domain.market import ThreeWayProbability
from football_system.domain.market_v2 import MarketProbabilityDistributionV1
from football_system.domain.services.review_v4 import generic_correction
from football_system.domain.services.strategy_pass import build_strategy_plan
from football_system.domain.services.strategy_settlement import settle_strategy_plan
from football_system.domain.strategy_pass import StrategyProfileV1, StrategyPassPlanV1
from football_system.domain.settlement import MatchResult
from football_system.domain.archive import match_result_payload_sha256
from tests.unit import test_review_bridge as wire
from tests.unit import test_strategy_pass as old

GOLDEN = json.loads(
    Path("data/fixtures/legacy_v070_market_goldens.json").read_text(encoding="utf-8")
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "version,source,builder,review",
    [
        ("V1", wire._source, wire.build_analysis_packet, wire._review),
        ("V2", wire._source_v2, wire.build_analysis_packet_v2, wire._review_v2),
        ("V3", wire._source_v3, wire.build_analysis_packet_v3, wire._review_v3),
    ],
)
def test_v070_packet_review_bytes_and_normalization_are_exact(
    version, source, builder, review
):
    packet = builder(source(), wire.NOW)
    packet_bytes = wire._packet_bytes(packet)
    review_bytes = wire.canonical_json(review(packet)).encode()
    _, _, normalized = wire.validate_review_files(packet_bytes, review_bytes)
    expected = GOLDEN["wire"][version]
    assert packet.packet_hash == expected["packet_hash"]
    assert sha(packet_bytes) == expected["packet_bytes_sha256"]
    assert sha(review_bytes) == expected["review_bytes_sha256"]
    assert sha(normalized.encode()) == expected["normalized_review_sha256"]


@pytest.mark.parametrize(
    "pass_type,n",
    [
        (PassType.TWO_FOLD_ONE, 2),
        (PassType.THREE_FOLD_FOUR, 3),
        (PassType.FOUR_FOLD_ELEVEN, 4),
    ],
)
def test_v070_plan_ticket_settlement_and_single_selection_math_unchanged(pass_type, n):
    source = old.source(("2", "3", "4", "5")[:n])
    plan = build_strategy_plan(source, StrategyProfileV1(pass_types=(pass_type,)))
    expected = GOLDEN["strategy"][pass_type.value]
    assert sha(plan.model_dump_json().encode()) == expected["plan_bytes_sha256"]
    assert plan.plan_hash == expected["plan_hash"]
    assert (
        sha(plan.tickets[0].model_dump_json().encode())
        == expected["ticket_bytes_sha256"]
    )
    assert plan.tickets[0].candidate.base_stake_fen == expected["unit_stake_fen"]
    assert plan.tickets[0].candidate.max_payout_fen == expected["unit_max_payout_fen"]
    assert StrategyPassPlanV1.model_validate_json(plan.model_dump_json()) == plan
    at = old.NOW + timedelta(days=3)
    scores = tuple(
        MatchResult(
            match_result_id="golden-" + s.match_id,
            match_id=s.match_id,
            provider_code="SYNTHETIC",
            home_goals=1,
            away_goals=0,
            observed_at_utc=at,
            available_at_utc=at,
            ingested_at_utc=at,
            source_result_key=s.match_id,
            payload_hash=match_result_payload_sha256(1, 0),
        )
        for s in plan.tickets[0].candidate.selections
    )
    settled = settle_strategy_plan(plan, scores, at)
    assert (
        sha(settled.model_dump_json().encode()) == expected["settlement_bytes_sha256"]
    )


@pytest.mark.parametrize(
    "confidence,quality,cap",
    [
        ("0", "1", "0.08"),
        ("1", "1", "0.08"),
        ("0.3", "0.25", "0.08"),
        ("1", "1", "0"),
        ("0.7", "0.6", "0.20"),
    ],
)
@pytest.mark.parametrize(
    "base,llm",
    [
        (("0.6", "0.25", "0.15"), ("0.1", "0.2", "0.7")),
        (("0", "1", "0"), ("0.9", "0", "0.1")),
        (("0.333333333333", "0.333333333333", "0.333333333334"), ("0.4", "0.3", "0.3")),
    ],
)
def test_threeway_generic_adapter_exactly_matches_frozen_delta_kernel(
    base, llm, confidence, quality, cap
):
    before = ThreeWayProbability(**dict(zip(("home_win", "draw", "away_win"), base)))
    proposed = ThreeWayProbability(**dict(zip(("home_win", "draw", "away_win"), llm)))
    args = tuple(map(Decimal, (confidence, quality, cap)))
    _, _, expected = _apply_review_probability(before, proposed, *args)
    actual = generic_correction(
        MarketProbabilityDistributionV1.from_three_way(before),
        MarketProbabilityDistributionV1.from_three_way(proposed),
        *args,
    )
    assert actual.to_three_way().model_dump_json() == expected.model_dump_json()
