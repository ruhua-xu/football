from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN

import pytest
from pydantic import ValidationError

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.services.elo_baseline import (
    BASELINE_UNCALIBRATED,
    ELO_THREE_WAY_BASELINE_V1,
    EloBaselineConfig,
    EloBaselineState,
    EloPredictionRequest,
    EloPredictionStatus,
    EloRegularTimeResult,
    EloThreeWayBaseline,
    EloUnavailableReason,
)

UTC = timezone.utc
START = datetime(2024, 8, 1, 12, 0, tzinfo=UTC)


def result(
    number: int,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    *,
    season: str = "2024",
    available_delay: timedelta = timedelta(hours=2),
    ingested_delay: timedelta | None = None,
    version: str = "v1",
    supersedes_match_result_id: str | None = None,
) -> EloRegularTimeResult:
    kickoff = START + timedelta(days=number)
    ingested_delay = available_delay if ingested_delay is None else ingested_delay
    return EloRegularTimeResult(
        match_result_id=f"result-{number}-{version}",
        match_id=f"match-{number}",
        season_id=season,
        home_team_id=home,
        away_team_id=away,
        kickoff_at_utc=kickoff,
        available_at_utc=kickoff + available_delay,
        ingested_at_utc=kickoff + ingested_delay,
        home_goals=home_goals,
        away_goals=away_goals,
        payload_hash=match_result_payload_sha256(home_goals, away_goals),
        supersedes_match_result_id=supersedes_match_result_id,
    )


def request(
    home: str,
    away: str,
    cutoff: datetime,
    *,
    season: str = "2024",
    match_id: str = "target-match",
) -> EloPredictionRequest:
    return EloPredictionRequest(
        match_id=match_id,
        season_id=season,
        home_team_id=home,
        away_team_id=away,
        cutoff_at_utc=cutoff,
        kickoff_at_utc=cutoff + timedelta(days=1),
    )


def team(state: EloBaselineState, team_id: str):
    value = state.for_team(team_id)
    assert value is not None
    return value


def test_rebuild_is_deterministic_and_has_stable_lineage() -> None:
    matches = (
        result(1, "alpha", "bravo", 2, 0),
        result(2, "charlie", "alpha", 1, 1),
        result(3, "bravo", "charlie", 0, 1),
    )
    cutoff = START + timedelta(days=5)
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=0))

    chronological = baseline.rebuild_state(
        matches,
        cutoff,
        target_season_id="2024",
    )
    shuffled = baseline.rebuild_state(
        (matches[2], matches[0], matches[1]),
        cutoff,
        target_season_id="2024",
    )

    assert chronological == shuffled
    assert chronological.training_match_ids == ("match-1", "match-2", "match-3")
    assert tuple(item.team_id for item in chronological.teams) == (
        "alpha",
        "bravo",
        "charlie",
    )
    assert chronological.model_name == ELO_THREE_WAY_BASELINE_V1
    assert chronological.model_version == "1"
    assert chronological.calibration_label == BASELINE_UNCALIBRATED
    assert chronological.config_hash == baseline.config_hash
    assert len(chronological.config_hash) == 64
    assert chronological.training_result_ids == (
        "result-1-v1",
        "result-2-v1",
        "result-3-v1",
    )
    assert len(chronological.training_data_hash) == 64
    assert len(chronological.state_hash) == 64
    assert tuple(fact.sequence for fact in chronological.training_facts) == (0, 1, 2)


def test_prediction_cutoff_excludes_future_results() -> None:
    past = result(1, "alpha", "bravo", 2, 0)
    delayed = result(
        2,
        "bravo",
        "alpha",
        3,
        0,
        available_delay=timedelta(days=3),
    )
    cutoff = START + timedelta(days=3)
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=1))
    prediction_request = request("alpha", "bravo", cutoff)

    with_future_input = baseline.predict(prediction_request, (delayed, past))
    past_only = baseline.predict(prediction_request, (past,))

    assert with_future_input == past_only
    assert with_future_input.training_match_ids == (past.match_id,)
    assert delayed.kickoff_at_utc < cutoff < delayed.available_at_utc


def test_season_transition_regresses_known_ratings_toward_initial() -> None:
    baseline = EloThreeWayBaseline(
        EloBaselineConfig(
            home_advantage=Decimal(0),
            season_regression_factor=Decimal("0.5"),
            minimum_prior_matches=0,
        )
    )
    matches = (result(1, "alpha", "bravo", 3, 0),)
    cutoff = START + timedelta(days=3)
    old_season = baseline.rebuild_state(
        matches,
        cutoff,
        target_season_id="2024",
    )
    new_season = baseline.rebuild_state(
        matches,
        cutoff,
        target_season_id="2025",
    )

    old_alpha = team(old_season, "alpha")
    new_alpha = team(new_season, "alpha")
    expected = (
        baseline.config.initial_rating
        + Decimal("0.5") * (old_alpha.rating - baseline.config.initial_rating)
    ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

    assert new_alpha.rating == expected
    assert abs(new_alpha.rating - baseline.config.initial_rating) < abs(
        old_alpha.rating - baseline.config.initial_rating
    )
    assert new_alpha.prior_matches == old_alpha.prior_matches
    assert new_season.training_match_ids == old_season.training_match_ids


def test_promoted_team_starts_at_initial_rating() -> None:
    baseline = EloThreeWayBaseline(
        EloBaselineConfig(
            home_advantage=Decimal(0),
            minimum_prior_matches=0,
        )
    )
    matches = (result(1, "incumbent", "other", 2, 0),)
    cutoff = START + timedelta(days=3)

    prediction = baseline.predict(
        request("promoted", "incumbent", cutoff, season="2025"),
        matches,
    )

    assert prediction.status == EloPredictionStatus.AVAILABLE
    assert prediction.home_rating == baseline.config.initial_rating
    assert prediction.home_prior_matches == 0
    assert prediction.away_prior_matches == 1


def test_insufficient_history_is_explicitly_unavailable() -> None:
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=2))
    prediction = baseline.predict(
        request("alpha", "bravo", START + timedelta(days=3)),
        (result(1, "alpha", "bravo", 2, 0),),
    )

    assert prediction.status == EloPredictionStatus.UNAVAILABLE
    assert prediction.reason == EloUnavailableReason.INSUFFICIENT_PRIOR_MATCHES
    assert prediction.probabilities is None
    assert prediction.insufficient_history_team_ids == ("alpha", "bravo")
    assert "p_market" not in prediction.model_dump()


def test_duplicate_result_versions_and_ambiguous_corrections_are_rejected() -> None:
    baseline = EloThreeWayBaseline()
    original = result(1, "alpha", "bravo", 1, 0)
    cutoff = START + timedelta(days=3)

    with pytest.raises(ValueError, match="source result IDs must be unique"):
        baseline.rebuild_state((original, original), cutoff)

    conflict = result(1, "alpha", "bravo", 1, 2, version="v2")
    with pytest.raises(ValueError, match="one supersession chain"):
        baseline.rebuild_state((original, conflict), cutoff)


def test_ingestion_cutoff_excludes_a_known_but_not_yet_ingested_result() -> None:
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=1))
    delayed_ingestion = result(
        1,
        "alpha",
        "bravo",
        2,
        0,
        ingested_delay=timedelta(days=4),
    )
    cutoff = START + timedelta(days=3)

    prediction = baseline.predict(
        request("alpha", "bravo", cutoff),
        (delayed_ingestion,),
    )

    assert delayed_ingestion.available_at_utc < cutoff
    assert delayed_ingestion.ingested_at_utc > cutoff
    assert prediction.status == EloPredictionStatus.UNAVAILABLE
    assert prediction.training_match_ids == ()
    assert prediction.training_result_ids == ()


def test_visible_correction_replaces_prior_version_and_changes_state_hashes() -> None:
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=0))
    original = result(1, "alpha", "bravo", 1, 0)
    corrected = result(
        1,
        "alpha",
        "bravo",
        1,
        2,
        available_delay=timedelta(hours=4),
        ingested_delay=timedelta(days=3),
        version="v2",
        supersedes_match_result_id=original.match_result_id,
    )

    before = baseline.rebuild_state(
        (corrected, original),
        START + timedelta(days=2),
        target_season_id="2024",
    )
    after = baseline.rebuild_state(
        (original, corrected),
        START + timedelta(days=5),
        target_season_id="2024",
    )

    assert before.training_result_ids == (original.match_result_id,)
    assert after.training_result_ids == (corrected.match_result_id,)
    assert before.training_data_hash != after.training_data_hash
    assert before.state_hash != after.state_hash
    assert team(before, "alpha").rating > team(after, "alpha").rating


def test_training_fact_and_state_hashes_reject_tampering() -> None:
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=0))
    state = baseline.rebuild_state(
        (result(1, "alpha", "bravo", 2, 0),),
        START + timedelta(days=3),
        target_season_id="2024",
    )
    fact_payload = state.training_facts[0].model_dump(mode="python")
    fact_payload["home_goals"] = 0

    with pytest.raises(ValidationError, match="training fact hash"):
        type(state.training_facts[0]).model_validate(fact_payload)

    state_payload = state.model_dump(mode="python")
    state_payload["teams"][0]["rating"] = Decimal("999")
    with pytest.raises(ValidationError, match="state hash"):
        EloBaselineState.model_validate(state_payload)


def test_target_result_is_excluded_even_when_visible() -> None:
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=0))
    target_result = result(1, "alpha", "bravo", 2, 0)
    cutoff = START + timedelta(days=3)
    target_request = request(
        "alpha",
        "bravo",
        cutoff,
        match_id=target_result.match_id,
    )

    prediction = baseline.predict(target_request, (target_result,))

    assert prediction.training_match_ids == ()
    assert prediction.training_result_ids == ()
    assert prediction.home_rating == baseline.config.initial_rating
    assert prediction.away_rating == baseline.config.initial_rating


def test_three_way_probability_sums_exactly_to_one() -> None:
    baseline = EloThreeWayBaseline(
        EloBaselineConfig(
            home_advantage=Decimal("80"),
            draw_probability=Decimal("0.27"),
            minimum_prior_matches=0,
        )
    )
    prediction = baseline.predict(
        request("alpha", "bravo", START + timedelta(days=1)),
        (),
    )

    assert prediction.probabilities is not None
    assert prediction.probabilities.draw == Decimal("0.27")
    assert sum(
        prediction.probabilities.model_dump().values(),
        Decimal(0),
    ) == Decimal(1)


def test_results_drive_directionality_not_team_ids() -> None:
    baseline = EloThreeWayBaseline(
        EloBaselineConfig(
            home_advantage=Decimal(0),
            minimum_prior_matches=3,
        )
    )
    matches = (
        result(1, "zzz-strong", "aaa-weak", 2, 0),
        result(2, "aaa-weak", "zzz-strong", 0, 1),
        result(3, "zzz-strong", "aaa-weak", 3, 1),
    )
    cutoff = START + timedelta(days=5)

    strong_home = baseline.predict(
        request("zzz-strong", "aaa-weak", cutoff, match_id="strong-home"),
        matches,
    )
    weak_home = baseline.predict(
        request("aaa-weak", "zzz-strong", cutoff, match_id="weak-home"),
        matches,
    )

    assert strong_home.probabilities is not None
    assert weak_home.probabilities is not None
    assert strong_home.home_rating > strong_home.away_rating
    assert strong_home.probabilities.home_win > strong_home.probabilities.away_win
    assert strong_home.probabilities.home_win == weak_home.probabilities.away_win
    assert strong_home.probabilities.away_win == weak_home.probabilities.home_win

    no_history = EloThreeWayBaseline(
        EloBaselineConfig(
            home_advantage=Decimal(0),
            minimum_prior_matches=0,
        )
    )
    arbitrary_ids = no_history.predict(
        request("zzz", "aaa", cutoff, match_id="arbitrary-ids"),
        (),
    )
    assert arbitrary_ids.probabilities is not None
    assert arbitrary_ids.probabilities.home_win == arbitrary_ids.probabilities.away_win
