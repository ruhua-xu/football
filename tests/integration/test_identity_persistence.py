from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from football_system.application.identity_catalog import (
    MatchIdentityRegistration,
    RegisteredCanonicalMatch,
    RegisteredCompetitionMapping,
    RegisteredTeamAlias,
)
from football_system.domain.common import stable_id
from football_system.domain.identity import (
    EXACT_IDENTITY_RESOLUTION,
    Alias,
    AmbiguousMatchMappingError,
    CanonicalMatchIdentity,
    CompetitionMapping,
    ProviderMatchIdentity,
    UnresolvedMatchMappingError,
)
from football_system.domain.match import (
    Competition,
    Match,
    ProviderMatchMapping,
    Team,
)
from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)


UTC = timezone.utc
AVAILABLE = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
FUTURE_AVAILABLE = AVAILABLE + timedelta(days=2)
CREATED = FUTURE_AVAILABLE + timedelta(days=1)
KICKOFF = datetime(2026, 9, 10, 19, 30, tzinfo=UTC)
PROVIDER_A = "FIXTURES_A"
PROVIDER_B = "MARKET_ODDS_B"
IDENTITY_TABLES = (
    "providers",
    "competitions",
    "teams",
    "matches",
    "provider_team_aliases",
    "provider_competition_mappings",
    "canonical_match_identities",
    "provider_match_mappings",
)


def test_registration_is_atomic_and_exactly_idempotent() -> None:
    engine, repository = _repository()
    registration = _registration()

    repository.register(registration)
    repository.register(registration)
    repository.register(
        registration.model_copy(update={"created_at_utc": CREATED + timedelta(hours=1)})
    )

    assert _table_counts(engine) == {
        "providers": 1,
        "competitions": 1,
        "teams": 2,
        "matches": 1,
        "provider_team_aliases": 2,
        "provider_competition_mappings": 1,
        "canonical_match_identities": 1,
        "provider_match_mappings": 0,
    }

    extra = _registration("-extra")
    conflict = MatchIdentityRegistration(
        created_at_utc=CREATED,
        competitions=(
            *extra.competitions,
            registration.competitions[0].model_copy(
                update={"name": "Conflicting immutable name"}
            ),
        ),
        teams=(*extra.teams, *registration.teams),
        matches=(*extra.matches, *registration.matches),
        team_aliases=(*extra.team_aliases, *registration.team_aliases),
        competition_mappings=(
            *extra.competition_mappings,
            *registration.competition_mappings,
        ),
        canonical_matches=(
            *extra.canonical_matches,
            *registration.canonical_matches,
        ),
    )
    before = _table_counts(engine)

    with pytest.raises(ValueError, match="immutable competition"):
        repository.register(conflict)

    assert _table_counts(engine) == before
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM competitions "
                    "WHERE competition_id = 'competition-extra'"
                )
            )
            == 0
        )

    changed_availability = registration.model_copy(
        update={
            "team_aliases": (
                registration.team_aliases[0].model_copy(
                    update={"available_at_utc": AVAILABLE + timedelta(minutes=1)}
                ),
                registration.team_aliases[1],
            )
        }
    )
    with pytest.raises(ValueError, match="immutable team alias"):
        repository.register(changed_availability)
    assert _table_counts(engine) == before


def test_round_trip_resolver_preserves_all_exact_identity_dimensions() -> None:
    _, repository = _repository()
    registration = _registration()
    current_mapping = registration.competition_mappings[0]
    repository.register(
        registration.model_copy(
            update={
                "competition_mappings": (
                    current_mapping,
                    current_mapping.model_copy(
                        update={
                            "mapping": current_mapping.mapping.model_copy(
                                update={"season": "2025/26"}
                            )
                        }
                    ),
                    current_mapping.model_copy(
                        update={
                            "mapping": current_mapping.mapping.model_copy(
                                update={"competition_type": "CUP"}
                            )
                        }
                    ),
                )
            }
        )
    )

    catalog = repository.load_catalog(
        as_of_at_utc=CREATED,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    assert catalog == repository.load_catalog(
        as_of_at_utc=CREATED,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    resolver = catalog.build_resolver(timedelta(minutes=2))
    provider_match = _provider_match()

    assert catalog.competition_mappings == (current_mapping.mapping,)
    resolution = resolver.resolve(provider_match)
    assert resolution.internal_match_id == "match"
    assert resolution.resolution_method == EXACT_IDENTITY_RESOLUTION

    invalid_updates = (
        {"home_team_name": "Home FC (similar, not exact)"},
        {"provider_competition_name": "League (similar, not exact)"},
        {"season": "2025/26"},
        {"competition_type": "CUP"},
        {"kickoff_at_utc": KICKOFF + timedelta(minutes=2, seconds=1)},
    )
    for update in invalid_updates:
        with pytest.raises(UnresolvedMatchMappingError):
            resolver.resolve(provider_match.model_copy(update=update))

    assert (
        resolver.resolve(
            provider_match.model_copy(
                update={"kickoff_at_utc": KICKOFF + timedelta(minutes=2)}
            )
        ).internal_match_id
        == "match"
    )

    with pytest.raises(ValueError, match="kickoff window"):
        repository.load_catalog(
            as_of_at_utc=CREATED,
            kickoff_from_utc=KICKOFF + timedelta(seconds=1),
            kickoff_to_utc=KICKOFF,
        )


def test_persisted_duplicate_lookup_keys_remain_ambiguous() -> None:
    _, repository = _repository()
    competitions = (
        Competition(
            competition_id="competition-one",
            canonical_key="competition-key-one",
            name="League One",
            country_code="GB",
        ),
        Competition(
            competition_id="competition-two",
            canonical_key="competition-key-two",
            name="League Two",
            country_code="GB",
        ),
    )
    teams = (
        Team(team_id="home-one", canonical_key="home-key-one", name="Home One"),
        Team(team_id="home-two", canonical_key="home-key-two", name="Home Two"),
        Team(team_id="away", canonical_key="away-key", name="Away"),
    )
    matches = (
        Match(
            match_id="ambiguous-one",
            competition_id="competition-one",
            home_team_id="home-one",
            away_team_id="away",
            kickoff_at_utc=KICKOFF,
            available_at_utc=AVAILABLE,
        ),
        Match(
            match_id="ambiguous-two",
            competition_id="competition-two",
            home_team_id="home-two",
            away_team_id="away",
            kickoff_at_utc=KICKOFF,
            available_at_utc=AVAILABLE,
        ),
    )
    shared_home = Alias(
        provider_code=PROVIDER_A,
        provider_team_id="shared-home",
        provider_team_name="Shared Home",
        language="en",
    )
    team_aliases = (
        RegisteredTeamAlias(
            internal_team_id="home-one",
            alias=shared_home,
            available_at_utc=AVAILABLE,
        ),
        RegisteredTeamAlias(
            internal_team_id="home-one",
            alias=shared_home.model_copy(
                update={
                    "provider_team_id": "unique-home-one",
                    "provider_team_name": "Home One",
                }
            ),
            available_at_utc=AVAILABLE,
        ),
        RegisteredTeamAlias(
            internal_team_id="home-two",
            alias=shared_home,
            available_at_utc=AVAILABLE,
        ),
        RegisteredTeamAlias(
            internal_team_id="home-two",
            alias=shared_home.model_copy(
                update={
                    "provider_team_id": "unique-home-two",
                    "provider_team_name": "Home Two",
                }
            ),
            available_at_utc=AVAILABLE,
        ),
        RegisteredTeamAlias(
            internal_team_id="away",
            alias=Alias(
                provider_code=PROVIDER_A,
                provider_team_id="away",
                provider_team_name="Away",
                language="en",
            ),
            available_at_utc=AVAILABLE,
        ),
    )
    shared_competition = CompetitionMapping(
        internal_competition_id="competition-one",
        provider_code=PROVIDER_A,
        provider_competition_id="shared-league",
        provider_competition_name="Shared League",
        language="en",
        season="2026/27",
        competition_type="LEAGUE",
    )
    competition_mappings = (
        RegisteredCompetitionMapping(
            mapping=shared_competition,
            available_at_utc=AVAILABLE,
        ),
        RegisteredCompetitionMapping(
            mapping=shared_competition.model_copy(
                update={"internal_competition_id": "competition-two"}
            ),
            available_at_utc=AVAILABLE,
        ),
    )
    canonical_matches = tuple(
        RegisteredCanonicalMatch(
            identity=CanonicalMatchIdentity(
                internal_match_id=match.match_id,
                internal_competition_id=match.competition_id,
                internal_home_team_id=match.home_team_id,
                internal_away_team_id=match.away_team_id,
                season="2026/27",
                competition_type="LEAGUE",
                kickoff_at_utc=match.kickoff_at_utc,
            ),
            available_at_utc=AVAILABLE,
        )
        for match in matches
    )
    repository.register(
        MatchIdentityRegistration(
            created_at_utc=CREATED,
            competitions=competitions,
            teams=teams,
            matches=matches,
            team_aliases=team_aliases,
            competition_mappings=competition_mappings,
            canonical_matches=canonical_matches,
        )
    )
    catalog = repository.load_catalog(
        as_of_at_utc=CREATED,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
    )
    resolver = catalog.build_resolver(timedelta(0))

    assert len(catalog.competition_mappings) == 2
    assert sum(len(identity.aliases) for identity in catalog.team_identities) == 5
    with pytest.raises(AmbiguousMatchMappingError) as team_error:
        resolver.resolve(
            _provider_match(
                match_id="ambiguous-provider-team",
                competition_id="shared-league",
                competition_name="Shared League",
                home_team_id="shared-home",
                home_team_name="Shared Home",
                away_team_id="away",
                away_team_name="Away",
            )
        )
    assert team_error.value.candidates == ("home-one", "home-two")

    with pytest.raises(AmbiguousMatchMappingError) as competition_error:
        resolver.resolve(
            _provider_match(
                match_id="ambiguous-provider-competition",
                competition_id="shared-league",
                competition_name="Shared League",
                home_team_id="unique-home-one",
                home_team_name="Home One",
                away_team_id="away",
                away_team_name="Away",
            )
        )
    assert competition_error.value.candidates == (
        "competition-one",
        "competition-two",
    )


def test_cutoff_excludes_future_match_metadata_and_explicit_mapping() -> None:
    _, repository = _repository()
    base = _registration()
    alternate_match = Match(
        match_id="alternate-match",
        competition_id="competition",
        home_team_id="home",
        away_team_id="away",
        kickoff_at_utc=KICKOFF + timedelta(hours=1),
        available_at_utc=FUTURE_AVAILABLE,
    )
    alternate_identity = RegisteredCanonicalMatch(
        identity=CanonicalMatchIdentity(
            internal_match_id=alternate_match.match_id,
            internal_competition_id=alternate_match.competition_id,
            internal_home_team_id=alternate_match.home_team_id,
            internal_away_team_id=alternate_match.away_team_id,
            season="2026/27",
            competition_type="LEAGUE",
            kickoff_at_utc=alternate_match.kickoff_at_utc,
        ),
        available_at_utc=FUTURE_AVAILABLE,
    )
    explicit = ProviderMatchMapping(
        mapping_id=stable_id(
            "provider-mapping", PROVIDER_A, "fixture", "explicit-event"
        ),
        provider_code=PROVIDER_A,
        external_namespace="fixture",
        external_match_id="explicit-event",
        internal_match_id=alternate_match.match_id,
        resolution_method="MANUAL_EXACT",
        confidence=Decimal("0.99"),
        available_at_utc=FUTURE_AVAILABLE,
    )
    future_alias = RegisteredTeamAlias(
        internal_team_id="home",
        alias=base.team_aliases[0].alias.model_copy(
            update={
                "provider_team_id": "future-home",
                "provider_team_name": "Future Home",
            }
        ),
        available_at_utc=FUTURE_AVAILABLE,
    )
    future_mapping = RegisteredCompetitionMapping(
        mapping=base.competition_mappings[0].mapping.model_copy(
            update={
                "provider_competition_id": "future-league",
                "provider_competition_name": "Future League",
            }
        ),
        available_at_utc=FUTURE_AVAILABLE,
    )
    repository.register(
        MatchIdentityRegistration(
            created_at_utc=CREATED,
            competitions=base.competitions,
            teams=base.teams,
            matches=(*base.matches, alternate_match),
            team_aliases=(*base.team_aliases, future_alias),
            competition_mappings=(
                *base.competition_mappings,
                future_mapping,
            ),
            canonical_matches=(*base.canonical_matches, alternate_identity),
            explicit_mappings=(explicit,),
        )
    )
    before = repository.load_catalog(
        as_of_at_utc=FUTURE_AVAILABLE - timedelta(seconds=1),
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF + timedelta(hours=2),
    )
    after = repository.load_catalog(
        as_of_at_utc=FUTURE_AVAILABLE,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF + timedelta(hours=2),
    )
    provider_match = _provider_match(match_id="explicit-event")

    before_resolution = before.build_resolver(timedelta(minutes=2)).resolve(
        provider_match
    )
    after_resolution = after.build_resolver(timedelta(minutes=2)).resolve(
        provider_match
    )
    assert before_resolution.internal_match_id == "match"
    assert before_resolution.resolution_method == EXACT_IDENTITY_RESOLUTION
    assert after_resolution.internal_match_id == "alternate-match"
    assert after_resolution.resolution_method == "MANUAL_EXACT"
    assert tuple(item.internal_match_id for item in before.canonical_matches) == (
        "match",
    )
    assert {item.internal_match_id for item in after.canonical_matches} == {
        "match",
        "alternate-match",
    }
    assert all(
        alias.provider_team_id != "future-home"
        for identity in before.team_identities
        for alias in identity.aliases
    )
    assert all(
        mapping.provider_competition_id != "future-league"
        for mapping in before.competition_mappings
    )
    assert before.explicit_mappings == ()


def test_provider_filter_applies_to_every_provider_identity_source() -> None:
    _, repository = _repository()
    base = _registration()
    provider_b_aliases = tuple(
        RegisteredTeamAlias(
            internal_team_id=item.internal_team_id,
            alias=item.alias.model_copy(update={"provider_code": PROVIDER_B}),
            available_at_utc=item.available_at_utc,
        )
        for item in base.team_aliases
    )
    provider_b_mapping = RegisteredCompetitionMapping(
        mapping=base.competition_mappings[0].mapping.model_copy(
            update={"provider_code": PROVIDER_B}
        ),
        available_at_utc=AVAILABLE,
    )
    explicit_mappings = tuple(
        ProviderMatchMapping(
            mapping_id=stable_id("provider-mapping", provider, "fixture", provider),
            provider_code=provider,
            external_namespace="fixture",
            external_match_id=provider,
            internal_match_id="match",
            resolution_method="MANUAL_EXACT",
            confidence=Decimal(1),
            available_at_utc=AVAILABLE,
        )
        for provider in (PROVIDER_A, PROVIDER_B)
    )
    repository.register(
        MatchIdentityRegistration(
            created_at_utc=CREATED,
            competitions=base.competitions,
            teams=base.teams,
            matches=base.matches,
            team_aliases=(*base.team_aliases, *provider_b_aliases),
            competition_mappings=(
                *base.competition_mappings,
                provider_b_mapping,
            ),
            canonical_matches=base.canonical_matches,
            explicit_mappings=explicit_mappings,
        )
    )

    catalog = repository.load_catalog(
        as_of_at_utc=CREATED,
        kickoff_from_utc=KICKOFF,
        kickoff_to_utc=KICKOFF,
        provider_codes=(PROVIDER_B,),
    )

    assert len(catalog.team_identities) == 2
    assert {
        alias.provider_code
        for identity in catalog.team_identities
        for alias in identity.aliases
    } == {PROVIDER_B}
    assert {item.provider_code for item in catalog.competition_mappings} == {PROVIDER_B}
    assert {item.provider_code for item in catalog.explicit_mappings} == {PROVIDER_B}
    assert tuple(item.internal_match_id for item in catalog.canonical_matches) == (
        "match",
    )

    with pytest.raises(ValueError, match="provider codes must be unique"):
        repository.load_catalog(
            as_of_at_utc=CREATED,
            kickoff_from_utc=KICKOFF,
            kickoff_to_utc=KICKOFF,
            provider_codes=(PROVIDER_B, PROVIDER_B),
        )
    with pytest.raises(ValueError, match="canonical identifiers"):
        repository.load_catalog(
            as_of_at_utc=CREATED,
            kickoff_from_utc=KICKOFF,
            kickoff_to_utc=KICKOFF,
            provider_codes=(" ",),
        )


def test_registration_rejects_missing_invalid_and_future_references() -> None:
    registration = _registration()

    with pytest.raises(ValueError, match="unknown competition"):
        _rebuild(
            registration,
            matches=(
                registration.matches[0].model_copy(
                    update={"competition_id": "missing-competition"}
                ),
            ),
        )
    with pytest.raises(ValueError, match="exactly one canonical identity"):
        _rebuild(registration, canonical_matches=())
    with pytest.raises(ValueError, match="unknown team"):
        _rebuild(
            registration,
            team_aliases=(
                registration.team_aliases[0].model_copy(
                    update={"internal_team_id": "missing-team"}
                ),
                registration.team_aliases[1],
            ),
        )
    with pytest.raises(ValueError, match="at least one mapping"):
        _rebuild(registration, competition_mappings=())
    with pytest.raises(ValueError, match="unknown match"):
        _rebuild(
            registration,
            explicit_mappings=(
                ProviderMatchMapping(
                    mapping_id="invalid-explicit",
                    provider_code=PROVIDER_A,
                    external_namespace="fixture",
                    external_match_id="missing",
                    internal_match_id="missing-match",
                    confidence=Decimal(1),
                    available_at_utc=AVAILABLE,
                ),
            ),
        )
    with pytest.raises(ValueError, match="availability timestamps"):
        _rebuild(
            registration,
            team_aliases=(
                registration.team_aliases[0].model_copy(
                    update={"available_at_utc": CREATED + timedelta(seconds=1)}
                ),
                registration.team_aliases[1],
            ),
        )
    with pytest.raises(ValueError, match="team aliases must be unique"):
        _rebuild(
            registration,
            team_aliases=(
                *registration.team_aliases,
                registration.team_aliases[0],
            ),
        )


def _repository() -> tuple[Engine, SqlAlchemyMatchIdentityRepository]:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    return engine, SqlAlchemyMatchIdentityRepository(create_session_factory(engine))


def _registration(
    suffix: str = "",
    *,
    provider_code: str = PROVIDER_A,
) -> MatchIdentityRegistration:
    competition_id = f"competition{suffix}"
    home_team_id = f"home{suffix}"
    away_team_id = f"away{suffix}"
    match_id = f"match{suffix}"
    competition = Competition(
        competition_id=competition_id,
        canonical_key=f"competition-key{suffix}",
        name=f"League{suffix}",
        country_code="GB",
    )
    home = Team(
        team_id=home_team_id,
        canonical_key=f"home-key{suffix}",
        name=f"Home FC{suffix}",
    )
    away = Team(
        team_id=away_team_id,
        canonical_key=f"away-key{suffix}",
        name=f"Away FC{suffix}",
    )
    match = Match(
        match_id=match_id,
        competition_id=competition_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        kickoff_at_utc=KICKOFF,
        available_at_utc=AVAILABLE,
    )
    aliases = (
        RegisteredTeamAlias(
            internal_team_id=home_team_id,
            alias=Alias(
                provider_code=provider_code,
                provider_team_id=f"provider-home{suffix}",
                provider_team_name=f"Home FC{suffix}",
                language="en",
            ),
            available_at_utc=AVAILABLE,
        ),
        RegisteredTeamAlias(
            internal_team_id=away_team_id,
            alias=Alias(
                provider_code=provider_code,
                provider_team_id=f"provider-away{suffix}",
                provider_team_name=f"Away FC{suffix}",
                language="en",
            ),
            available_at_utc=AVAILABLE,
        ),
    )
    competition_mapping = RegisteredCompetitionMapping(
        mapping=CompetitionMapping(
            internal_competition_id=competition_id,
            provider_code=provider_code,
            provider_competition_id=f"provider-league{suffix}",
            provider_competition_name=f"League{suffix}",
            language="en",
            season="2026/27",
            competition_type="LEAGUE",
        ),
        available_at_utc=AVAILABLE,
    )
    canonical = RegisteredCanonicalMatch(
        identity=CanonicalMatchIdentity(
            internal_match_id=match_id,
            internal_competition_id=competition_id,
            internal_home_team_id=home_team_id,
            internal_away_team_id=away_team_id,
            season="2026/27",
            competition_type="LEAGUE",
            kickoff_at_utc=KICKOFF,
        ),
        available_at_utc=AVAILABLE,
    )
    return MatchIdentityRegistration(
        created_at_utc=CREATED,
        competitions=(competition,),
        teams=(home, away),
        matches=(match,),
        team_aliases=aliases,
        competition_mappings=(competition_mapping,),
        canonical_matches=(canonical,),
    )


def _provider_match(
    *,
    match_id: str = "provider-match",
    competition_id: str = "provider-league",
    competition_name: str = "League",
    home_team_id: str = "provider-home",
    home_team_name: str = "Home FC",
    away_team_id: str = "provider-away",
    away_team_name: str = "Away FC",
) -> ProviderMatchIdentity:
    return ProviderMatchIdentity(
        provider_code=PROVIDER_A,
        provider_match_id=match_id,
        external_namespace="fixture",
        provider_competition_id=competition_id,
        provider_competition_name=competition_name,
        competition_language="en",
        season="2026/27",
        competition_type="LEAGUE",
        home_team_id=home_team_id,
        home_team_name=home_team_name,
        home_team_language="en",
        away_team_id=away_team_id,
        away_team_name=away_team_name,
        away_team_language="en",
        kickoff_at_utc=KICKOFF,
    )


def _rebuild(
    registration: MatchIdentityRegistration,
    **updates: object,
) -> MatchIdentityRegistration:
    values = registration.model_dump(mode="python")
    values.update(updates)
    return MatchIdentityRegistration.model_validate(values)


def _table_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table_name: connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}"))
            for table_name in IDENTITY_TABLES
        }
