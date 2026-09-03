from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.identity_catalog import (
    FixtureIngestionCapture,
    FixtureIngestionRequest,
    FixtureIngestionSummary,
    FixtureObservation,
    MatchIdentityRegistration,
    RegisteredCanonicalMatch,
    RegisteredCompetitionMapping,
    RegisteredTeamAlias,
)
from football_system.domain.common import stable_id
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
)
from football_system.domain.match import (
    Competition,
    Match,
    MatchStatus,
    ProviderMatchMapping,
    Team,
)
from football_system.domain.raw_data import ProviderRequestAudit, ProviderRequestOutcome
from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    FixtureIngestionCaptureRecord,
    FixtureObservationRecord,
    MatchRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)


UTC = timezone.utc
PROVIDER_A = "FIXTURE_CAPTURE_A"
PROVIDER_B = "FIXTURE_CAPTURE_B"
AVAILABLE = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 10, 19, 30, tzinfo=UTC)
INGESTED = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def test_fixture_ingestion_commits_identity_capture_and_observations_atomically() -> (
    None
):
    engine, sessions, repository = _repository()
    capture = _capture("atomic")

    summary = repository.register_fixture_ingestion(capture)

    assert summary == FixtureIngestionSummary(
        ingestion_id="ingestion-atomic",
        raw_artifact_id=_sha256("artifact:atomic"),
        inserted=True,
        competition_count=1,
        team_count=2,
        match_count=1,
        observation_count=1,
    )
    assert _counts(engine) == {
        "providers": 1,
        "competitions": 1,
        "teams": 2,
        "matches": 1,
        "provider_team_aliases": 2,
        "provider_competition_mappings": 1,
        "canonical_match_identities": 1,
        "provider_match_mappings": 1,
        "fixture_ingestion_captures": 1,
        "fixture_observations": 1,
    }
    with sessions() as session:
        parent = session.get(FixtureIngestionCaptureRecord, "ingestion-atomic")
        observation = session.get(FixtureObservationRecord, "observation-atomic")
        match = session.get(MatchRecord, "match")
        canonical = session.get(CanonicalMatchIdentityRecord, "match")
        provider_mapping = session.get(
            ProviderMatchMappingRecord,
            "provider-mapping",
        )
        aliases = tuple(session.scalars(select(ProviderTeamAliasRecord)))
        competition_mapping = session.scalar(select(ProviderCompetitionMappingRecord))
        assert parent is not None
        assert observation is not None
        assert match is not None
        assert canonical is not None
        assert provider_mapping is not None
        assert competition_mapping is not None
        assert parent.request_parameters_json == '{"a":{"nested":[2,1]},"z":3}'
        assert parent.provider_competition_id == "provider-league"
        assert parent.provider_season_id == "provider-season-2026-27"
        assert parent.endpoint == "https://fixtures.example.test/v1/fixtures"
        assert parent.provider_request_id == "request-atomic"
        assert parent.http_status == 200
        assert parent.duration_ms == 25
        assert parent.outcome == "SUCCESS"
        assert parent.failure_code is None
        assert parent.raw_payload_sha256 == _sha256("raw-payload:atomic")
        assert parent.observation_count == 1
        assert parent.ingested_at_utc == INGESTED
        assert observation.provider_mapping_id == "provider-mapping"
        assert observation.internal_match_id == "match"
        assert match.fixture_ingestion_id == capture.ingestion_id
        assert canonical.fixture_ingestion_id == capture.ingestion_id
        assert provider_mapping.fixture_ingestion_id == capture.ingestion_id
        assert {alias.fixture_ingestion_id for alias in aliases} == {
            capture.ingestion_id
        }
        assert competition_mapping.fixture_ingestion_id == capture.ingestion_id


def test_fixture_ingestion_exact_replay_is_a_verified_no_op() -> None:
    clock_calls: list[datetime] = []

    def clock() -> datetime:
        clock_calls.append(INGESTED)
        return INGESTED

    engine, sessions, repository = _repository(clock=clock)
    capture = _capture("replay")

    inserted = repository.register_fixture_ingestion(capture)
    replayed = repository.register_fixture_ingestion(capture)

    assert inserted.inserted is True
    assert replayed == inserted.model_copy(update={"inserted": False})
    assert clock_calls == [INGESTED]
    with sessions() as session:
        parent = session.get(FixtureIngestionCaptureRecord, capture.ingestion_id)
        assert parent is not None
        assert parent.ingested_at_utc == INGESTED
    assert _counts(engine)["fixture_ingestion_captures"] == 1
    assert _counts(engine)["fixture_observations"] == 1

    changed_scope = capture.model_copy(
        update={
            "request": capture.request.model_copy(
                update={"provider_season_id": "different-provider-season"}
            )
        }
    )
    with pytest.raises(ValueError, match="provider_season_id"):
        repository.register_fixture_ingestion(changed_scope)

    competition_mapping = capture.registration.competition_mappings[0]
    changed_competition_scope = capture.model_copy(
        update={
            "request": capture.request.model_copy(
                update={"provider_competition_id": "different-provider-league"}
            ),
            "registration": capture.registration.model_copy(
                update={
                    "competition_mappings": (
                        competition_mapping.model_copy(
                            update={
                                "mapping": competition_mapping.mapping.model_copy(
                                    update={
                                        "provider_competition_id": (
                                            "different-provider-league"
                                        )
                                    }
                                )
                            }
                        ),
                    )
                }
            ),
        }
    )
    with pytest.raises(ValueError, match="provider_competition_id"):
        repository.register_fixture_ingestion(changed_competition_scope)
    assert clock_calls == [INGESTED]


def test_fixture_ingestion_rejects_clock_before_response_receipt() -> None:
    capture = _capture("early-clock")
    early = capture.request_audit.received_at_utc - timedelta(microseconds=1)
    clock_calls: list[datetime] = []

    def clock() -> datetime:
        clock_calls.append(early)
        return early

    engine, _, repository = _repository(clock=clock)

    with pytest.raises(ValueError, match="cannot precede response receipt"):
        repository.register_fixture_ingestion(capture)

    assert clock_calls == [early]
    assert _counts(engine) == {table: 0 for table in _counts(engine)}


def test_fixture_ingestion_database_check_rejects_ingestion_before_receipt() -> None:
    _, sessions, repository = _repository()
    capture = _capture("invalid-db-timeline")
    repository.register(capture.registration)

    with pytest.raises(IntegrityError):
        _insert_parent(
            sessions,
            capture,
            ingested_at_utc=(
                capture.request_audit.received_at_utc - timedelta(microseconds=1)
            ),
        )


def test_later_capture_reuses_first_seen_identity_and_appends_observed_drift() -> None:
    _, sessions, repository = _repository()
    first = _capture("first")
    later_available = AVAILABLE + timedelta(hours=2)
    later_kickoff = KICKOFF + timedelta(minutes=20)
    later = _capture(
        "later",
        available_at=later_available,
        kickoff_at=later_kickoff,
        status=MatchStatus.POSTPONED,
    )

    repository.register_fixture_ingestion(first)
    assert repository.register_fixture_ingestion(later).inserted is True

    with sessions() as session:
        match = session.get(MatchRecord, "match")
        canonical = session.get(CanonicalMatchIdentityRecord, "match")
        mapping = session.get(ProviderMatchMappingRecord, "provider-mapping")
        aliases = tuple(session.scalars(select(ProviderTeamAliasRecord)))
        competition_mapping = session.scalar(select(ProviderCompetitionMappingRecord))
        observations = tuple(
            session.scalars(
                select(FixtureObservationRecord).order_by(
                    FixtureObservationRecord.available_at_utc
                )
            )
        )

        assert match is not None
        assert canonical is not None
        assert mapping is not None
        assert competition_mapping is not None
        assert match.kickoff_at_utc == KICKOFF
        assert match.status == MatchStatus.SCHEDULED.value
        assert match.available_at_utc == AVAILABLE
        assert canonical.available_at_utc == AVAILABLE
        assert mapping.available_at_utc == AVAILABLE
        assert competition_mapping.available_at_utc == AVAILABLE
        assert {item.available_at_utc for item in aliases} == {AVAILABLE}
        assert tuple(
            (item.kickoff_at_utc, item.status, item.available_at_utc)
            for item in observations
        ) == (
            (KICKOFF, MatchStatus.SCHEDULED.value, AVAILABLE),
            (later_kickoff, MatchStatus.POSTPONED.value, later_available),
        )

    assert repository.register_fixture_ingestion(first).inserted is False


def test_fixture_ingestion_appends_team_name_drift_but_preserves_canonical_team() -> (
    None
):
    _, sessions, repository = _repository()
    first = _capture("team-name-first")
    renamed = _capture(
        "team-name-later",
        available_at=AVAILABLE + timedelta(hours=1),
        home_name="Home FC Renamed",
        provider_home_name="Home FC Renamed",
    )
    repository.register_fixture_ingestion(first)

    with pytest.raises(ValueError, match="immutable team"):
        repository.register(renamed.registration)
    assert repository.register_fixture_ingestion(renamed).inserted is True

    with sessions() as session:
        team = session.get(TeamRecord, "home")
        aliases = tuple(
            session.scalars(
                select(ProviderTeamAliasRecord).where(
                    ProviderTeamAliasRecord.internal_team_id == "home"
                )
            )
        )
        assert team is not None
        assert team.name == "Home FC"
        assert {alias.provider_team_name for alias in aliases} == {
            "Home FC",
            "Home FC Renamed",
        }
        assert {alias.available_at_utc for alias in aliases} == {
            AVAILABLE,
            AVAILABLE + timedelta(hours=1),
        }


def test_fixture_ingestion_appends_league_name_drift_but_preserves_competition() -> (
    None
):
    _, sessions, repository = _repository()
    first = _capture("league-name-first")
    renamed = _capture(
        "league-name-later",
        available_at=AVAILABLE + timedelta(hours=1),
        competition_name="Premier League Renamed",
        provider_competition_name="Premier League Renamed",
    )
    repository.register_fixture_ingestion(first)

    with pytest.raises(ValueError, match="immutable competition"):
        repository.register(renamed.registration)
    assert repository.register_fixture_ingestion(renamed).inserted is True

    with sessions() as session:
        competition = session.get(CompetitionRecord, "competition")
        mappings = tuple(session.scalars(select(ProviderCompetitionMappingRecord)))
        assert competition is not None
        assert competition.name == "League"
        assert {mapping.provider_competition_name for mapping in mappings} == {
            "League",
            "Premier League Renamed",
        }
        assert {mapping.available_at_utc for mapping in mappings} == {
            AVAILABLE,
            AVAILABLE + timedelta(hours=1),
        }


def test_catalog_uses_latest_visible_fixture_kickoff_for_window_and_identity() -> None:
    first_ingested_at = AVAILABLE + timedelta(minutes=1)
    rescheduled_at = AVAILABLE + timedelta(hours=2)
    rescheduled_ingested_at = rescheduled_at + timedelta(minutes=30)
    rescheduled_kickoff = KICKOFF + timedelta(hours=4)
    ingestion_times = iter((first_ingested_at, rescheduled_ingested_at))
    _, _, repository = _repository(clock=lambda: next(ingestion_times))
    repository.register_fixture_ingestion(_capture("schedule-first"))
    repository.register_fixture_ingestion(
        _capture(
            "schedule-later",
            available_at=rescheduled_at,
            kickoff_at=rescheduled_kickoff,
        )
    )

    before = repository.load_catalog(
        as_of_at_utc=rescheduled_at - timedelta(microseconds=1),
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    old_window_before_ingestion = repository.load_catalog(
        as_of_at_utc=rescheduled_at,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    new_window_before_ingestion = repository.load_catalog(
        as_of_at_utc=rescheduled_ingested_at - timedelta(microseconds=1),
        kickoff_from_utc=rescheduled_kickoff,
        kickoff_to_utc=rescheduled_kickoff,
    )
    new_window_after = repository.load_catalog(
        as_of_at_utc=rescheduled_ingested_at,
        kickoff_from_utc=rescheduled_kickoff,
        kickoff_to_utc=rescheduled_kickoff,
    )

    assert tuple(item.kickoff_at_utc for item in before.canonical_matches) == (KICKOFF,)
    assert tuple(
        item.kickoff_at_utc for item in old_window_before_ingestion.canonical_matches
    ) == (KICKOFF,)
    assert new_window_before_ingestion.canonical_matches == ()
    assert tuple(
        item.kickoff_at_utc for item in new_window_after.canonical_matches
    ) == (rescheduled_kickoff,)


def test_catalog_hides_fixture_identities_until_capture_is_ingested() -> None:
    _, _, repository = _repository()
    repository.register_fixture_ingestion(_capture("delayed-ingestion"))

    before = repository.load_catalog(
        as_of_at_utc=INGESTED - timedelta(microseconds=1),
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    after = repository.load_catalog(
        as_of_at_utc=INGESTED,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )

    assert before.canonical_matches == ()
    assert before.team_identities == ()
    assert before.competition_mappings == ()
    assert before.explicit_mappings == ()
    assert len(after.canonical_matches) == 1
    assert len(after.team_identities) == 2
    assert len(after.competition_mappings) == 1
    assert len(after.explicit_mappings) == 1


def test_catalog_hides_fixture_name_drift_until_later_capture_is_ingested() -> None:
    first_ingested_at = AVAILABLE + timedelta(minutes=1)
    later_available_at = AVAILABLE + timedelta(hours=2)
    later_ingested_at = later_available_at + timedelta(minutes=30)
    ingestion_times = iter((first_ingested_at, later_ingested_at))
    _, _, repository = _repository(clock=lambda: next(ingestion_times))
    repository.register_fixture_ingestion(_capture("name-first"))
    repository.register_fixture_ingestion(
        _capture(
            "name-later",
            available_at=later_available_at,
            home_name="Home FC Renamed",
            provider_home_name="Home FC Renamed",
            competition_name="Premier League Renamed",
            provider_competition_name="Premier League Renamed",
        )
    )

    before = repository.load_catalog(
        as_of_at_utc=later_ingested_at - timedelta(microseconds=1),
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    after = repository.load_catalog(
        as_of_at_utc=later_ingested_at,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )

    assert {
        alias.provider_team_name
        for identity in before.team_identities
        for alias in identity.aliases
    } == {"Home FC", "Away FC"}
    assert {
        mapping.provider_competition_name for mapping in before.competition_mappings
    } == {"League"}
    assert {
        alias.provider_team_name
        for identity in after.team_identities
        for alias in identity.aliases
    } == {"Home FC", "Home FC Renamed", "Away FC"}
    assert {
        mapping.provider_competition_name for mapping in after.competition_mappings
    } == {"League", "Premier League Renamed"}


def test_fixture_capture_does_not_reclassify_preexisting_identity() -> None:
    _, sessions, repository = _repository()
    capture = _capture("preexisting")
    repository.register(capture.registration)
    cutoff = INGESTED - timedelta(microseconds=1)
    before = repository.load_catalog(
        as_of_at_utc=cutoff,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )

    repository.register_fixture_ingestion(capture)

    assert (
        repository.load_catalog(
            as_of_at_utc=cutoff,
            kickoff_from_utc=KICKOFF,
            kickoff_to_utc=KICKOFF,
        )
        == before
    )
    assert len(before.canonical_matches) == 1
    assert len(before.team_identities) == 2
    assert len(before.competition_mappings) == 1
    assert len(before.explicit_mappings) == 1
    with sessions() as session:
        assert session.get(MatchRecord, "match").fixture_ingestion_id is None
        assert (
            session.get(
                CanonicalMatchIdentityRecord,
                "match",
            ).fixture_ingestion_id
            is None
        )
        assert {
            item.fixture_ingestion_id
            for item in session.scalars(select(ProviderTeamAliasRecord))
        } == {None}
        assert {
            item.fixture_ingestion_id
            for item in session.scalars(select(ProviderCompetitionMappingRecord))
        } == {None}
        assert {
            item.fixture_ingestion_id
            for item in session.scalars(select(ProviderMatchMappingRecord))
        } == {None}


def test_fixture_identity_origin_trigger_rejects_missing_capture() -> None:
    _, sessions, repository = _repository()
    repository.register(_capture("origin-trigger").registration)

    with pytest.raises(IntegrityError, match="fixture identity origin is inconsistent"):
        with sessions.begin() as session:
            session.add(
                ProviderTeamAliasRecord(
                    alias_id="invalid-origin-alias",
                    internal_team_id="home",
                    provider_id=stable_id("provider", PROVIDER_A),
                    provider_team_id="invalid-origin-team",
                    provider_team_name="Invalid Origin Team",
                    language="en",
                    team_type="CLUB",
                    available_at_utc=AVAILABLE,
                    fixture_ingestion_id="missing-ingestion",
                )
            )


@pytest.mark.parametrize(
    "scope_update",
    ({"language": "fr"}, {"team_type": "NATIONAL"}),
)
def test_fixture_identity_origin_trigger_rejects_mismatched_alias_scope(
    scope_update: dict[str, str],
) -> None:
    _, sessions, repository = _repository()
    capture = _capture("origin-scope")
    repository.register_fixture_ingestion(capture)

    with pytest.raises(IntegrityError, match="fixture identity origin is inconsistent"):
        with sessions.begin() as session:
            session.add(
                ProviderTeamAliasRecord(
                    alias_id=f"invalid-origin-{next(iter(scope_update.values()))}",
                    internal_team_id="home",
                    provider_id=stable_id("provider", PROVIDER_A),
                    provider_team_id="invalid-origin-team",
                    provider_team_name="Invalid Origin Team",
                    language=scope_update.get("language", "en"),
                    team_type=scope_update.get("team_type", "CLUB"),
                    available_at_utc=AVAILABLE,
                    fixture_ingestion_id=capture.ingestion_id,
                )
            )


def test_new_backdated_capture_is_rejected_after_a_later_observation() -> None:
    engine, _, repository = _repository()
    repository.register_fixture_ingestion(_capture("first"))
    repository.register_fixture_ingestion(
        _capture("latest", available_at=AVAILABLE + timedelta(hours=2))
    )
    before = _counts(engine)

    with pytest.raises(ValueError, match="cannot predate its stored history"):
        repository.register_fixture_ingestion(
            _capture("backdated", available_at=AVAILABLE + timedelta(hours=1))
        )

    assert _counts(engine) == before


def test_late_child_conflict_rolls_back_new_identity_and_parent_rows() -> None:
    engine, _, repository = _repository()
    repository.register_fixture_ingestion(
        _capture("existing", suffix="-existing", observation_id="shared-observation")
    )
    before = _counts(engine)

    with pytest.raises(ValueError, match="fixture ingestion conflicts"):
        repository.register_fixture_ingestion(
            _capture("conflict", suffix="-new", observation_id="shared-observation")
        )

    assert _counts(engine) == before
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM competitions "
                    "WHERE competition_id = 'competition-new'"
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM fixture_ingestion_captures "
                    "WHERE ingestion_id = 'ingestion-conflict'"
                )
            )
            == 0
        )


def test_empty_fixture_ingestion_is_persisted_and_replayable() -> None:
    engine, _, repository = _repository()
    capture = _empty_capture("empty")

    inserted = repository.register_fixture_ingestion(capture)
    replayed = repository.register_fixture_ingestion(capture)

    assert inserted == FixtureIngestionSummary(
        ingestion_id="ingestion-empty",
        raw_artifact_id=_sha256("artifact:empty"),
        inserted=True,
        competition_count=0,
        team_count=0,
        match_count=0,
        observation_count=0,
    )
    assert replayed.inserted is False
    assert _counts(engine)["providers"] == 1
    assert _counts(engine)["fixture_ingestion_captures"] == 1
    assert _counts(engine)["fixture_observations"] == 0


def test_replay_rejects_an_incomplete_stored_child_set() -> None:
    engine, sessions, repository = _repository()
    capture = _capture("incomplete")
    repository.register(capture.registration)
    _insert_parent(sessions, capture)

    with pytest.raises(ValueError, match="observation set is incomplete"):
        repository.register_fixture_ingestion(capture)

    assert _counts(engine)["fixture_ingestion_captures"] == 1
    assert _counts(engine)["fixture_observations"] == 0


def test_fixture_capture_tables_are_append_only_and_reject_duplicate_inserts() -> None:
    engine, _, repository = _repository()
    repository.register_fixture_ingestion(_capture("immutable"))

    statements = (
        "UPDATE fixture_ingestion_captures SET duration_ms = 26",
        "DELETE FROM fixture_ingestion_captures",
        "UPDATE fixture_observations SET status = 'FINISHED'",
        "DELETE FROM fixture_observations",
        (
            "INSERT INTO fixture_ingestion_captures SELECT * "
            "FROM fixture_ingestion_captures"
        ),
        "INSERT INTO fixture_observations SELECT * FROM fixture_observations",
    )
    for statement in statements:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(statement))

    assert _counts(engine)["fixture_ingestion_captures"] == 1
    assert _counts(engine)["fixture_observations"] == 1


def test_fixture_observation_trigger_rejects_cross_provider_and_match_lineage() -> None:
    engine, _, repository = _repository()
    base = _capture("base")
    repository.register_fixture_ingestion(base)
    provider_b_mapping = base.registration.explicit_mappings[0].model_copy(
        update={
            "mapping_id": "provider-b-mapping",
            "provider_code": PROVIDER_B,
            "external_match_id": "provider-b-match",
        }
    )
    repository.register(
        base.registration.model_copy(
            update={
                "explicit_mappings": (
                    *base.registration.explicit_mappings,
                    provider_b_mapping,
                )
            }
        )
    )
    repository.register(_capture("other", suffix="-other").registration)
    empty = _empty_capture("lineage")
    repository.register_fixture_ingestion(empty)

    invalid_rows = (
        {
            "observation_id": "cross-provider-observation",
            "provider_mapping_id": "provider-b-mapping",
            "internal_match_id": "match",
        },
        {
            "observation_id": "cross-match-observation",
            "provider_mapping_id": "provider-mapping",
            "internal_match_id": "match-other",
        },
    )
    for invalid in invalid_rows:
        with pytest.raises(IntegrityError, match="lineage is inconsistent"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO fixture_observations (
                            observation_id, ingestion_id, provider_mapping_id,
                            internal_match_id, kickoff_at_utc, status,
                            available_at_utc, payload_sha256
                        ) VALUES (
                            :observation_id, 'ingestion-lineage',
                            :provider_mapping_id, :internal_match_id,
                            :kickoff, 'SCHEDULED', :available, :payload_sha256
                        )
                        """
                    ),
                    {
                        **invalid,
                        "kickoff": KICKOFF.strftime("%Y-%m-%d %H:%M:%S.%f"),
                        "available": AVAILABLE.strftime("%Y-%m-%d %H:%M:%S.%f"),
                        "payload_sha256": _sha256(invalid["observation_id"]),
                    },
                )

    assert _counts(engine)["fixture_observations"] == 1


def _repository(
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[
    Engine,
    sessionmaker[Session],
    SqlAlchemyMatchIdentityRepository,
]:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    return (
        engine,
        sessions,
        SqlAlchemyMatchIdentityRepository(
            sessions,
            clock=clock or (lambda: INGESTED),
        ),
    )


def _capture(
    capture_key: str,
    *,
    suffix: str = "",
    available_at: datetime = AVAILABLE,
    kickoff_at: datetime = KICKOFF,
    status: MatchStatus = MatchStatus.SCHEDULED,
    observation_id: str | None = None,
    home_name: str | None = None,
    provider_home_name: str | None = None,
    competition_name: str | None = None,
    provider_competition_name: str | None = None,
) -> FixtureIngestionCapture:
    competition_id = f"competition{suffix}"
    home_team_id = f"home{suffix}"
    away_team_id = f"away{suffix}"
    match_id = f"match{suffix}"
    received_at = available_at + timedelta(minutes=1)
    mapping_id = f"provider-mapping{suffix}"
    registration = MatchIdentityRegistration(
        created_at_utc=received_at,
        competitions=(
            Competition(
                competition_id=competition_id,
                canonical_key=f"competition-key{suffix}",
                name=competition_name or f"League{suffix}",
                country_code="GB",
            ),
        ),
        teams=(
            Team(
                team_id=home_team_id,
                canonical_key=f"home-key{suffix}",
                name=home_name or f"Home FC{suffix}",
            ),
            Team(
                team_id=away_team_id,
                canonical_key=f"away-key{suffix}",
                name=f"Away FC{suffix}",
            ),
        ),
        matches=(
            Match(
                match_id=match_id,
                competition_id=competition_id,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                kickoff_at_utc=kickoff_at,
                status=status,
                available_at_utc=available_at,
            ),
        ),
        team_aliases=(
            RegisteredTeamAlias(
                internal_team_id=home_team_id,
                alias=Alias(
                    provider_code=PROVIDER_A,
                    provider_team_id=f"provider-home{suffix}",
                    provider_team_name=provider_home_name or f"Home FC{suffix}",
                    language="en",
                ),
                available_at_utc=available_at,
            ),
            RegisteredTeamAlias(
                internal_team_id=away_team_id,
                alias=Alias(
                    provider_code=PROVIDER_A,
                    provider_team_id=f"provider-away{suffix}",
                    provider_team_name=f"Away FC{suffix}",
                    language="en",
                ),
                available_at_utc=available_at,
            ),
        ),
        competition_mappings=(
            RegisteredCompetitionMapping(
                mapping=CompetitionMapping(
                    internal_competition_id=competition_id,
                    provider_code=PROVIDER_A,
                    provider_competition_id=f"provider-league{suffix}",
                    provider_competition_name=(
                        provider_competition_name or f"League{suffix}"
                    ),
                    language="en",
                    season="2026/27",
                    competition_type="LEAGUE",
                ),
                available_at_utc=available_at,
            ),
        ),
        canonical_matches=(
            RegisteredCanonicalMatch(
                identity=CanonicalMatchIdentity(
                    internal_match_id=match_id,
                    internal_competition_id=competition_id,
                    internal_home_team_id=home_team_id,
                    internal_away_team_id=away_team_id,
                    season="2026/27",
                    competition_type="LEAGUE",
                    kickoff_at_utc=kickoff_at,
                ),
                available_at_utc=available_at,
            ),
        ),
        explicit_mappings=(
            ProviderMatchMapping(
                mapping_id=mapping_id,
                provider_code=PROVIDER_A,
                external_namespace="fixture",
                external_match_id=f"external-match{suffix}",
                internal_match_id=match_id,
                resolution_method="PROVIDER_EXACT",
                confidence=Decimal(1),
                available_at_utc=available_at,
            ),
        ),
    )
    request, audit = _request_and_audit(capture_key, available_at, suffix=suffix)
    return FixtureIngestionCapture(
        ingestion_id=f"ingestion-{capture_key}",
        provider_code=PROVIDER_A,
        request=request,
        request_audit=audit,
        raw_artifact_id=_sha256(f"artifact:{capture_key}"),
        raw_payload_sha256=_sha256(f"raw-payload:{capture_key}"),
        registration=registration,
        observations=(
            FixtureObservation(
                observation_id=observation_id or f"observation-{capture_key}",
                provider_mapping_id=mapping_id,
                external_match_id=f"external-match{suffix}",
                internal_match_id=match_id,
                kickoff_at_utc=kickoff_at,
                status=status,
                available_at_utc=available_at,
                payload_sha256=_sha256(f"observation-payload:{capture_key}"),
            ),
        ),
    )


def _empty_capture(capture_key: str) -> FixtureIngestionCapture:
    request, audit = _request_and_audit(capture_key, AVAILABLE)
    return FixtureIngestionCapture(
        ingestion_id=f"ingestion-{capture_key}",
        provider_code=PROVIDER_A,
        request=request,
        request_audit=audit,
        raw_artifact_id=_sha256(f"artifact:{capture_key}"),
        raw_payload_sha256=_sha256(f"raw-payload:{capture_key}"),
        registration=MatchIdentityRegistration(
            created_at_utc=audit.received_at_utc,
            competitions=(),
            teams=(),
            matches=(),
            team_aliases=(),
            competition_mappings=(),
            canonical_matches=(),
        ),
        observations=(),
    )


def _request_and_audit(
    capture_key: str,
    available_at: datetime,
    *,
    suffix: str = "",
) -> tuple[FixtureIngestionRequest, ProviderRequestAudit]:
    received_at = available_at + timedelta(minutes=1)
    return (
        FixtureIngestionRequest(
            kickoff_from_utc=KICKOFF - timedelta(days=1),
            kickoff_to_utc=KICKOFF + timedelta(days=1),
            provider_competition_id=f"provider-league{suffix}",
            provider_season_id=f"provider-season-2026-27{suffix}",
            season="2026/27",
            competition_type="LEAGUE",
            language="en",
            team_type="CLUB",
        ),
        ProviderRequestAudit(
            provider=PROVIDER_A,
            endpoint="https://fixtures.example.test/v1/fixtures",
            requested_at_utc=available_at + timedelta(seconds=30),
            received_at_utc=received_at,
            available_at_utc=available_at,
            request_parameters={"z": 3, "a": {"nested": [2, 1]}},
            http_status=200,
            provider_request_id=f"request-{capture_key}",
            duration_ms=25,
            outcome=ProviderRequestOutcome.SUCCESS,
        ),
    )


def _insert_parent(
    sessions: sessionmaker[Session],
    capture: FixtureIngestionCapture,
    *,
    ingested_at_utc: datetime = INGESTED,
) -> None:
    audit = capture.request_audit
    request = capture.request
    assert audit.available_at_utc is not None
    assert audit.http_status is not None
    with sessions.begin() as session:
        session.add(
            FixtureIngestionCaptureRecord(
                ingestion_id=capture.ingestion_id,
                provider_id=stable_id("provider", PROVIDER_A),
                kickoff_from_utc=request.kickoff_from_utc,
                kickoff_to_utc=request.kickoff_to_utc,
                provider_competition_id=request.provider_competition_id,
                provider_season_id=request.provider_season_id,
                season=request.season,
                competition_type=request.competition_type,
                language=request.language,
                team_type=request.team_type.value,
                endpoint=audit.endpoint,
                request_parameters_json=json.dumps(
                    audit.request_parameters,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                requested_at_utc=audit.requested_at_utc,
                received_at_utc=audit.received_at_utc,
                available_at_utc=audit.available_at_utc,
                ingested_at_utc=ingested_at_utc,
                http_status=audit.http_status,
                provider_request_id=audit.provider_request_id,
                duration_ms=audit.duration_ms,
                outcome=audit.outcome.value,
                failure_code=None,
                raw_artifact_id=capture.raw_artifact_id,
                raw_payload_sha256=capture.raw_payload_sha256,
                observation_count=len(capture.observations),
            )
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _counts(engine: Engine) -> dict[str, int]:
    tables = (
        "providers",
        "competitions",
        "teams",
        "matches",
        "provider_team_aliases",
        "provider_competition_mappings",
        "canonical_match_identities",
        "provider_match_mappings",
        "fixture_ingestion_captures",
        "fixture_observations",
    )
    with engine.connect() as connection:
        return {
            table: connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in tables
        }
