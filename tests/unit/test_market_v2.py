from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from football_system.domain.goal_model import (
    GoalTrainingCohortV1,
    GoalTrainingFactV1,
    GoalPredictionRequestV1,
    PoissonGoalsConfigV1,
    PoissonGoalsStateV1,
)
from football_system.domain.market import ThreeWayProbability
from football_system.domain.market_v2 import (
    MarketKeyV2,
    MarketProbabilityDistributionV1,
    MarketOutcomeProbabilityV1,
    MarketOddsSnapshotV2,
    MarketPriceV1,
    market_consensus,
    distribution,
    settle_market,
)
from football_system.domain.services.poisson_goals import (
    score_grid,
    project_score_grid,
    train_poisson,
)
from football_system.domain.settlement import MatchResult
from football_system.domain.archive import match_result_payload_sha256

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def market(kind="THREE_WAY", handicap=None):
    return MarketKeyV2(market_type=kind, home_handicap=handicap)


@pytest.mark.parametrize(
    "kind,count",
    [
        ("THREE_WAY", 3),
        ("HANDICAP_THREE_WAY", 3),
        ("TOTAL_GOALS", 8),
        ("CORRECT_SCORE", 31),
    ],
)
def test_exact_ordered_catalog_and_decimal_adapter(kind, count):
    key = market(kind, -1 if kind == "HANDICAP_THREE_WAY" else None)
    assert len(key.catalog) == count and len(set(key.catalog)) == count
    p = distribution(key, [Decimal(1) / count] * count)
    assert sum(o.probability for o in p.outcomes) == 1
    assert p == MarketProbabilityDistributionV1.model_validate_json(p.model_dump_json())
    for values in (
        p.outcomes[:-1],
        tuple(reversed(p.outcomes)),
        (*p.outcomes, p.outcomes[0]),
    ):
        with pytest.raises(ValueError):
            MarketProbabilityDistributionV1(market_key=key, outcomes=values)


def test_legacy_threeway_explicit_adapter_preserves_values_and_bytes():
    old = ThreeWayProbability(
        home_win="0.600000000000", draw="0.250000000000", away_win="0.150000000000"
    )
    new = MarketProbabilityDistributionV1.from_three_way(old)
    assert new.to_three_way().model_dump_json() == old.model_dump_json()
    with pytest.raises(ValueError):
        market("HALF_FULL")
    with pytest.raises(ValueError):
        market("HANDICAP_THREE_WAY")
    with pytest.raises(ValueError):
        market("HANDICAP_THREE_WAY", 1.0)
    with pytest.raises(ValueError):
        MarketOutcomeProbabilityV1(outcome="HOME_WIN", probability=0.5)
    with pytest.raises(ValueError):
        MarketPriceV1(outcome="HOME_WIN", price=2.0)


@pytest.mark.parametrize(
    "kind,hcap,h,a,expected",
    [
        ("HANDICAP_THREE_WAY", -1, 2, 1, "DRAW"),
        ("HANDICAP_THREE_WAY", -1, 3, 1, "HOME_WIN"),
        ("HANDICAP_THREE_WAY", 1, 1, 2, "DRAW"),
        ("HANDICAP_THREE_WAY", 1, 1, 3, "AWAY_WIN"),
        ("TOTAL_GOALS", None, 2, 1, "GOALS_3"),
        ("TOTAL_GOALS", None, 4, 3, "GOALS_7_PLUS"),
        ("TOTAL_GOALS", None, 0, 0, "GOALS_0"),
        ("TOTAL_GOALS", None, 5, 2, "GOALS_7_PLUS"),
        ("CORRECT_SCORE", None, 3, 1, "SCORE_3_1"),
        ("CORRECT_SCORE", None, 4, 1, "SCORE_4_1"),
        ("CORRECT_SCORE", None, 5, 1, "SCORE_5_1"),
        ("CORRECT_SCORE", None, 6, 1, "HOME_OTHER"),
        ("CORRECT_SCORE", None, 4, 4, "DRAW_OTHER"),
        ("CORRECT_SCORE", None, 1, 6, "AWAY_OTHER"),
        ("CORRECT_SCORE", None, 0, 0, "SCORE_0_0"),
    ],
)
def test_b_c_d_other_settlement_goldens(kind, hcap, h, a, expected):
    assert settle_market(market(kind, hcap), h, a) == expected


@pytest.mark.parametrize(
    "home,away",
    [
        ("0.6", "0.5"),
        ("1.2", "1.0"),
        ("1.8", "1.3"),
        ("2.5", "0.8"),
        ("3.0", "2.5"),
        ("0", "0"),
        ("20", "20"),
    ],
)
def test_poisson_infinite_support_closure_and_replay(home, away):
    grid = score_grid(home, away)
    assert grid.home_tail <= Decimal("1e-18") and grid.away_tail <= Decimal("1e-18")
    assert grid == type(grid).model_validate_json(grid.model_dump_json())
    assert grid.artifact_id == score_grid(home, away).artifact_id
    for key in (
        market("HANDICAP_THREE_WAY", -1),
        market("TOTAL_GOALS"),
        market("CORRECT_SCORE"),
    ):
        p = project_score_grid(grid, key)
        assert sum(o.probability for o in p.outcomes) == 1
        assert all(o.probability >= 0 for o in p.outcomes)
        with localcontext() as context:
            context.prec = 19
            assert project_score_grid(grid, key) == p
    goals = project_score_grid(grid, market("TOTAL_GOALS"))
    with localcontext() as context:
        context.prec = 80
        lam = Decimal(home) + Decimal(away)
        masses = [(-lam).exp()]
        for n in range(1, 7):
            masses.append(masses[-1] * lam / n)
        assert abs(goals.probability("GOALS_7_PLUS") - (1 - sum(masses))) <= Decimal(
            "4e-12"
        )
    if Decimal(home) > 0 and Decimal(away) > 0:
        scores = project_score_grid(grid, market("CORRECT_SCORE"))
        assert all(
            scores.probability(k) > 0
            for k in ("HOME_OTHER", "DRAW_OTHER", "AWAY_OTHER")
        )
    with pytest.raises(ValueError):
        project_score_grid(grid, market())


@pytest.mark.parametrize("kind", ["THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE"])
def test_n_outcome_consensus_independent_devig_median(kind):
    key = market(kind)
    snapshots = tuple(
        MarketOddsSnapshotV2.freeze(
            match_id="target",
            market_key=key,
            prices=tuple(
                MarketPriceV1(
                    outcome=o,
                    price=str(
                        (2 if i == 0 else 4) * (2 if b == 2 else 1)
                        if b != 3
                        else (2 if i == 1 else 10)
                    ),
                )
                for i, o in enumerate(key.catalog)
            ),
            provider_code="SYNTHETIC",
            source_identity=f"source-{b}",
            source_artifact_id=f"raw-{b}",
            source_artifact_hash=str(b) * 64,
            bookmaker_code=f"book-{b}",
            captured_at_utc=NOW,
            available_at_utc=NOW,
            ingested_at_utc=NOW,
        )
        for b in (1, 2, 3)
    )
    result = market_consensus("target", key, snapshots, NOW)
    assert result == market_consensus("target", key, tuple(reversed(snapshots)), NOW)
    expected = distribution(
        key, [1 / p.price for p in snapshots[0].prices], normalize=True
    )
    assert result.probabilities == expected
    assert market_consensus("target", key, (), NOW).status == "MARKET_UNAVAILABLE"
    malformed = snapshots[0].model_copy(update={"prices": snapshots[0].prices[:-1]})
    with pytest.raises(ValueError):
        market_consensus("target", key, (malformed,), NOW)


def goal_fixture(home_count=5, away_count=5):
    facts = []
    for i in range(home_count + away_count):
        at = NOW + timedelta(days=i)
        h, a = (2, 1) if i < home_count else (1, 2)
        result = MatchResult(
            match_result_id=f"r{i}",
            match_id=f"m{i}",
            provider_code="SYNTHETIC",
            home_goals=h,
            away_goals=a,
            observed_at_utc=at + timedelta(hours=2),
            available_at_utc=at + timedelta(hours=2),
            ingested_at_utc=at + timedelta(hours=2),
            source_result_key=f"s{i}",
            payload_hash=match_result_payload_sha256(h, a),
        )
        facts.append(
            GoalTrainingFactV1(
                result=result,
                competition_id="league",
                season_id="2026",
                home_team_id="home" if i < home_count else f"opponent{i}",
                away_team_id=f"opponent{i}" if i < home_count else "away",
                kickoff_at_utc=at,
            )
        )
    cohort = GoalTrainingCohortV1.freeze(
        competition_id="league",
        season_id="2026",
        facts=tuple(facts),
        admitted_at_utc=NOW + timedelta(days=20),
        admission_reference="SYNTHETIC_FIXED_COHORT",
        source_artifact_hash="a" * 64,
    )
    request = GoalPredictionRequestV1(
        match_id="target",
        competition_id="league",
        season_id="2026",
        home_team_id="home",
        away_team_id="away",
        kickoff_at_utc=NOW + timedelta(days=24),
        training_cutoff_at_utc=NOW + timedelta(days=21),
        generated_at_utc=NOW + timedelta(days=21),
    )
    return cohort, request


def test_fixed_home_away_split_training_and_unavailability():
    cohort, request = goal_fixture()
    state = train_poisson(cohort, request)
    assert state.status == "AVAILABLE"
    # league home=away=1.5; H GF=2, H GA=1, A GF=2, A GA=1.
    assert (
        state.grid.lambda_home
        == state.grid.lambda_away
        == Decimal("1.333333333333333333")
    )
    assert PoissonGoalsStateV1.model_validate_json(state.model_dump_json()) == state
    assert state.config.config_hash == PoissonGoalsConfigV1().config_hash
    cohort, request = goal_fixture(4, 6)
    assert train_poisson(cohort, request).reason == "INSUFFICIENT_HOME_HISTORY"
    cohort, request = goal_fixture(6, 4)
    assert train_poisson(cohort, request).reason == "INSUFFICIENT_AWAY_HISTORY"
    cohort, request = goal_fixture(4, 4)
    assert train_poisson(cohort, request).reason == "INSUFFICIENT_LEAGUE_HISTORY"
    with pytest.raises(ValueError):
        train_poisson(
            cohort,
            request.model_copy(
                update={"training_cutoff_at_utc": cohort.admitted_at_utc}
            ),
        )


def test_zero_league_average_and_bad_normalized_hash_fail_closed():
    cohort, request = goal_fixture()
    facts = []
    for fact in cohort.facts:
        result = fact.result.model_copy(
            update={
                "home_goals": 0,
                "payload_hash": match_result_payload_sha256(0, fact.result.away_goals),
            }
        )
        facts.append(GoalTrainingFactV1(**(fact.model_dump() | {"result": result})))
    zero = GoalTrainingCohortV1.freeze(
        **(
            cohort.model_dump(exclude={"artifact_id", "content_hash"})
            | {"facts": tuple(facts)}
        )
    )
    assert train_poisson(zero, request).reason == "ZERO_LEAGUE_GOAL_AVERAGE"
    with pytest.raises(ValueError, match="payload hash"):
        GoalTrainingFactV1.model_validate(
            cohort.facts[0].model_dump()
            | {"result": cohort.facts[0].result.model_copy(update={"home_goals": 9})}
        )
