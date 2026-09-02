from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from football_system.domain.identity import (
    EXACT_IDENTITY_RESOLUTION,
    EXPLICIT_MAPPING_RESOLUTION,
    Alias,
    AmbiguousMatchMappingError,
    CanonicalMatchIdentity,
    CompetitionMapping,
    ExplicitMatchMapping,
    MatchIdentityResolver,
    ProviderMatchIdentity,
    TeamIdentity,
    UnresolvedMatchMappingError,
)
from football_system.domain.match import ProviderMatchMapping, TeamType


KICKOFF = datetime(2026, 9, 2, 19, 30, tzinfo=timezone.utc)


def alias(
    team_id: str,
    name: str,
    *,
    language: str = "en",
    team_type: TeamType = TeamType.CLUB,
) -> Alias:
    return Alias(
        provider_code="FIXTURES",
        provider_team_id=team_id,
        provider_team_name=name,
        language=language,
        team_type=team_type,
    )


def team(
    internal_id: str,
    canonical_name: str,
    *aliases: Alias,
    team_type: TeamType = TeamType.CLUB,
) -> TeamIdentity:
    return TeamIdentity(
        internal_team_id=internal_id,
        canonical_name=canonical_name,
        team_type=team_type,
        aliases=aliases,
    )


def competition(
    internal_id: str = "premier-league",
    *,
    season: str = "2026/27",
    competition_type: str = "LEAGUE",
    provider_id: str = "8",
    provider_name: str = "Premier League",
) -> CompetitionMapping:
    return CompetitionMapping(
        internal_competition_id=internal_id,
        provider_code="FIXTURES",
        provider_competition_id=provider_id,
        provider_competition_name=provider_name,
        language="en",
        season=season,
        competition_type=competition_type,
    )


def canonical_match(
    match_id: str = "match-1",
    *,
    competition_id: str = "premier-league",
    home_team_id: str = "team-home",
    away_team_id: str = "team-away",
    kickoff: datetime = KICKOFF,
    season: str = "2026/27",
    competition_type: str = "LEAGUE",
) -> CanonicalMatchIdentity:
    return CanonicalMatchIdentity(
        internal_match_id=match_id,
        internal_competition_id=competition_id,
        internal_home_team_id=home_team_id,
        internal_away_team_id=away_team_id,
        kickoff_at_utc=kickoff,
        season=season,
        competition_type=competition_type,
    )


def provider_match(
    *,
    match_id: str = "provider-match-1",
    competition_id: str = "8",
    competition_name: str = "Premier League",
    home_team_id: str = "100",
    home_team_name: str = "Manchester United",
    home_language: str = "en",
    home_team_type: TeamType = TeamType.CLUB,
    away_team_id: str = "200",
    away_team_name: str = "Liverpool",
    away_language: str = "en",
    kickoff: datetime = KICKOFF,
    season: str = "2026/27",
    competition_type: str = "LEAGUE",
) -> ProviderMatchIdentity:
    return ProviderMatchIdentity(
        provider_code="FIXTURES",
        provider_match_id=match_id,
        provider_competition_id=competition_id,
        provider_competition_name=competition_name,
        competition_language="en",
        season=season,
        competition_type=competition_type,
        home_team_id=home_team_id,
        home_team_name=home_team_name,
        home_team_language=home_language,
        home_team_type=home_team_type,
        away_team_id=away_team_id,
        away_team_name=away_team_name,
        away_team_language=away_language,
        kickoff_at_utc=kickoff,
    )


def resolver(
    *matches: CanonicalMatchIdentity,
    home_aliases: tuple[Alias, ...] | None = None,
    competition_mappings: tuple[CompetitionMapping, ...] | None = None,
    kickoff_tolerance: timedelta = timedelta(minutes=5),
) -> MatchIdentityResolver:
    return MatchIdentityResolver(
        team_identities=(
            team(
                "team-home",
                "Manchester United",
                *(home_aliases or (alias("100", "Manchester United"),)),
            ),
            team("team-away", "Liverpool", alias("200", "Liverpool")),
        ),
        competition_mappings=competition_mappings or (competition(),),
        canonical_matches=matches or (canonical_match(),),
        kickoff_tolerance=kickoff_tolerance,
    )


def test_resolves_exact_chinese_and_english_aliases() -> None:
    aliases = (
        alias("100", "Manchester United", language="en"),
        alias("100", "曼彻斯特联", language="zh-CN"),
    )
    identity_resolver = resolver(home_aliases=aliases)

    english = identity_resolver.resolve(provider_match())
    chinese = identity_resolver.resolve(
        provider_match(home_team_name="曼彻斯特联", home_language="zh-CN")
    )

    assert english.internal_match_id == "match-1"
    assert chinese.internal_match_id == "match-1"
    assert chinese.resolution_method == EXACT_IDENTITY_RESOLUTION


def test_same_name_teams_are_kept_distinct_by_provider_id_and_team_type() -> None:
    identity_resolver = MatchIdentityResolver(
        team_identities=(
            team("club-united", "United", alias("10", "United")),
            team(
                "youth-united",
                "United U21",
                alias("11", "United", team_type=TeamType.YOUTH),
                team_type=TeamType.YOUTH,
            ),
            team("opponent", "Opponent", alias("20", "Opponent")),
        ),
        competition_mappings=(competition(),),
        canonical_matches=(
            canonical_match(
                home_team_id="youth-united",
                away_team_id="opponent",
            ),
        ),
    )

    result = identity_resolver.resolve(
        provider_match(
            home_team_id="11",
            home_team_name="United",
            home_team_type=TeamType.YOUTH,
            away_team_id="20",
            away_team_name="Opponent",
        )
    )

    assert result.internal_match_id == "match-1"


def test_competition_season_and_type_are_part_of_exact_identity() -> None:
    mappings = (
        competition(),
        competition(
            "fa-cup",
            provider_id="8",
            provider_name="Premier League",
            competition_type="CUP",
        ),
        competition(
            "premier-league-2025",
            season="2025/26",
        ),
    )
    identity_resolver = resolver(
        canonical_match(),
        canonical_match(
            "cup-match",
            competition_id="fa-cup",
            competition_type="CUP",
        ),
        canonical_match(
            "prior-season-match",
            competition_id="premier-league-2025",
            season="2025/26",
        ),
        competition_mappings=mappings,
    )

    assert identity_resolver.resolve(provider_match()).internal_match_id == "match-1"
    assert (
        identity_resolver.resolve(provider_match(competition_type="CUP")).internal_match_id
        == "cup-match"
    )
    assert (
        identity_resolver.resolve(provider_match(season="2025/26")).internal_match_id
        == "prior-season-match"
    )


def test_kickoff_tolerance_is_inclusive_and_configurable() -> None:
    identity_resolver = resolver(kickoff_tolerance=timedelta(minutes=2))

    result = identity_resolver.resolve(
        provider_match(kickoff=KICKOFF + timedelta(minutes=2))
    )
    assert result.internal_match_id == "match-1"

    with pytest.raises(UnresolvedMatchMappingError) as error:
        identity_resolver.resolve(
            provider_match(kickoff=KICKOFF + timedelta(minutes=2, seconds=1))
        )
    assert error.value.code == "UNRESOLVED_MATCH_MAPPING"


def test_negative_kickoff_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        resolver(kickoff_tolerance=timedelta(seconds=-1))


def test_ambiguous_match_refuses_binding_with_typed_code() -> None:
    identity_resolver = resolver(
        canonical_match("match-1", kickoff=KICKOFF - timedelta(minutes=1)),
        canonical_match("match-2", kickoff=KICKOFF + timedelta(minutes=1)),
    )

    with pytest.raises(AmbiguousMatchMappingError) as error:
        identity_resolver.resolve(provider_match())

    assert error.value.code == "AMBIGUOUS_MATCH_MAPPING"
    assert error.value.candidates == ("match-1", "match-2")


def test_explicit_mapping_has_priority_over_derived_identity() -> None:
    identity_resolver = MatchIdentityResolver(
        team_identities=(),
        competition_mappings=(),
        canonical_matches=(),
        explicit_mappings=(
            ExplicitMatchMapping(
                provider_code="FIXTURES",
                provider_match_id="provider-match-1",
                internal_match_id="explicit-match",
            ),
        ),
    )

    result = identity_resolver.resolve(provider_match(home_team_name="not mapped"))

    assert result.internal_match_id == "explicit-match"
    assert result.resolution_method == EXPLICIT_MAPPING_RESOLUTION


def test_conflicting_explicit_mappings_are_ambiguous() -> None:
    identity_resolver = MatchIdentityResolver(
        team_identities=(),
        competition_mappings=(),
        canonical_matches=(),
        explicit_mappings=(
            ExplicitMatchMapping(
                provider_code="FIXTURES",
                provider_match_id="provider-match-1",
                internal_match_id="one",
            ),
            ExplicitMatchMapping(
                provider_code="FIXTURES",
                provider_match_id="provider-match-1",
                internal_match_id="two",
            ),
        ),
    )

    with pytest.raises(AmbiguousMatchMappingError) as error:
        identity_resolver.resolve(provider_match())

    assert error.value.code == "AMBIGUOUS_MATCH_MAPPING"


def test_existing_provider_mapping_is_accepted_as_explicit_mapping() -> None:
    identity_resolver = MatchIdentityResolver(
        team_identities=(),
        competition_mappings=(),
        canonical_matches=(),
        explicit_mappings=(
            ProviderMatchMapping(
                mapping_id="mapping-1",
                provider_code="FIXTURES",
                external_namespace="fixture",
                external_match_id="provider-match-1",
                internal_match_id="existing-match",
                resolution_method="MANUAL_EXACT",
                confidence=Decimal("0.99"),
                available_at_utc=KICKOFF,
            ),
        ),
    )

    result = identity_resolver.resolve(
        provider_match().model_copy(update={"external_namespace": "fixture"})
    )

    assert result.internal_match_id == "existing-match"
    assert result.confidence == Decimal("0.99")
    assert result.resolution_method == "MANUAL_EXACT"


def test_similar_but_not_exact_name_is_unresolved_without_fuzzy_matching() -> None:
    with pytest.raises(UnresolvedMatchMappingError):
        resolver().resolve(provider_match(home_team_name="Manchester Utd"))
