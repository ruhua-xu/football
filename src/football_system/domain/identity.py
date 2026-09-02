from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar, Self

from pydantic import AliasChoices, Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.match import ProviderMatchMapping, TeamType


EXPLICIT_MAPPING_RESOLUTION = "EXPLICIT_MAPPING"
EXACT_IDENTITY_RESOLUTION = "EXACT_TEAM_COMPETITION_KICKOFF"


class Alias(DomainModel):
    provider_code: Identifier = Field(
        validation_alias=AliasChoices("provider_code", "provider_id")
    )
    provider_team_id: Identifier
    provider_team_name: Identifier = Field(
        validation_alias=AliasChoices("provider_team_name", "alias", "name")
    )
    language: str = Field(default="und", min_length=2, max_length=35)
    team_type: TeamType = TeamType.CLUB

    @property
    def provider_id(self) -> str:
        return self.provider_code

    @property
    def alias(self) -> str:
        return self.provider_team_name


TeamAlias = Alias


class TeamIdentity(DomainModel):
    internal_team_id: Identifier
    canonical_name: Identifier
    team_type: TeamType = TeamType.CLUB
    aliases: tuple[Alias, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_alias_types(self) -> Self:
        if any(alias.team_type is not self.team_type for alias in self.aliases):
            raise ValueError("team aliases must preserve the canonical team type")
        return self


class CompetitionMapping(DomainModel):
    internal_competition_id: Identifier = Field(
        validation_alias=AliasChoices(
            "internal_competition_id",
            "competition_id",
        )
    )
    provider_code: Identifier = Field(
        validation_alias=AliasChoices("provider_code", "provider_id")
    )
    provider_competition_id: Identifier
    provider_competition_name: Identifier = Field(
        validation_alias=AliasChoices(
            "provider_competition_name",
            "competition_name",
            "provider_name",
        )
    )
    language: str = Field(default="und", min_length=2, max_length=35)
    season: Identifier
    competition_type: Identifier

    @property
    def competition_id(self) -> str:
        return self.internal_competition_id

    @property
    def provider_id(self) -> str:
        return self.provider_code


class ProviderMatchIdentity(DomainModel):
    provider_code: Identifier = Field(
        validation_alias=AliasChoices("provider_code", "provider_id")
    )
    provider_match_id: Identifier = Field(
        validation_alias=AliasChoices("provider_match_id", "external_match_id")
    )
    external_namespace: Identifier = "match"
    provider_competition_id: Identifier
    provider_competition_name: Identifier
    competition_language: str = Field(default="und", min_length=2, max_length=35)
    season: Identifier
    competition_type: Identifier
    home_team_id: Identifier = Field(
        validation_alias=AliasChoices(
            "home_team_id",
            "provider_home_team_id",
            "home_provider_team_id",
        )
    )
    home_team_name: Identifier = Field(
        validation_alias=AliasChoices(
            "home_team_name",
            "provider_home_team_name",
            "home_provider_team_name",
        )
    )
    home_team_language: str = Field(default="und", min_length=2, max_length=35)
    home_team_type: TeamType = TeamType.CLUB
    away_team_id: Identifier = Field(
        validation_alias=AliasChoices(
            "away_team_id",
            "provider_away_team_id",
            "away_provider_team_id",
        )
    )
    away_team_name: Identifier = Field(
        validation_alias=AliasChoices(
            "away_team_name",
            "provider_away_team_name",
            "away_provider_team_name",
        )
    )
    away_team_language: str = Field(default="und", min_length=2, max_length=35)
    away_team_type: TeamType = TeamType.CLUB
    kickoff_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_teams(self) -> Self:
        home = (self.home_team_id, self.home_team_type)
        away = (self.away_team_id, self.away_team_type)
        if home == away:
            raise ValueError("provider home and away teams must differ")
        return self

    @property
    def provider_id(self) -> str:
        return self.provider_code

    @property
    def external_match_id(self) -> str:
        return self.provider_match_id


class CanonicalMatchIdentity(DomainModel):
    internal_match_id: Identifier = Field(
        validation_alias=AliasChoices("internal_match_id", "match_id")
    )
    internal_competition_id: Identifier = Field(
        validation_alias=AliasChoices(
            "internal_competition_id",
            "competition_id",
        )
    )
    internal_home_team_id: Identifier = Field(
        validation_alias=AliasChoices("internal_home_team_id", "home_team_id")
    )
    internal_away_team_id: Identifier = Field(
        validation_alias=AliasChoices("internal_away_team_id", "away_team_id")
    )
    season: Identifier
    competition_type: Identifier
    kickoff_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_teams(self) -> Self:
        if self.internal_home_team_id == self.internal_away_team_id:
            raise ValueError("canonical home and away teams must differ")
        return self

    @property
    def match_id(self) -> str:
        return self.internal_match_id

    @property
    def competition_id(self) -> str:
        return self.internal_competition_id

    @property
    def home_team_id(self) -> str:
        return self.internal_home_team_id

    @property
    def away_team_id(self) -> str:
        return self.internal_away_team_id


class ExplicitMatchMapping(DomainModel):
    provider_code: Identifier
    provider_match_id: Identifier = Field(
        validation_alias=AliasChoices("provider_match_id", "external_match_id")
    )
    external_namespace: Identifier = "match"
    internal_match_id: Identifier
    confidence: Decimal = Field(default=Decimal(1), ge=0, le=1, allow_inf_nan=False)
    resolution_method: Identifier = EXPLICIT_MAPPING_RESOLUTION


class MatchResolution(DomainModel):
    internal_match_id: Identifier
    confidence: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    resolution_method: Identifier


class MatchIdentityResolutionError(ValueError):
    code: ClassVar[str] = "MATCH_IDENTITY_RESOLUTION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        provider_match: ProviderMatchIdentity,
        candidates: Iterable[str] = (),
    ) -> None:
        self.provider_match = provider_match
        self.candidates = tuple(sorted(set(candidates)))
        super().__init__(f"{self.code}: {message}")


class AmbiguousMatchMappingError(MatchIdentityResolutionError):
    code = "AMBIGUOUS_MATCH_MAPPING"


class UnresolvedMatchMappingError(MatchIdentityResolutionError):
    code = "UNRESOLVED_MATCH_MAPPING"


AmbiguousMatchMapping = AmbiguousMatchMappingError
UnresolvedMatchMapping = UnresolvedMatchMappingError


class MatchIdentityResolver:
    def __init__(
        self,
        team_identities: Iterable[TeamIdentity],
        competition_mappings: Iterable[CompetitionMapping],
        canonical_matches: Iterable[CanonicalMatchIdentity],
        *,
        explicit_mappings: (
            Iterable[ExplicitMatchMapping | ProviderMatchMapping]
            | Mapping[tuple[str, str] | tuple[str, str, str], str]
        ) = (),
        kickoff_tolerance: timedelta = timedelta(minutes=5),
    ) -> None:
        if not isinstance(kickoff_tolerance, timedelta):
            raise TypeError("kickoff_tolerance must be a timedelta")
        if kickoff_tolerance < timedelta(0):
            raise ValueError("kickoff_tolerance must be nonnegative")
        self.kickoff_tolerance = kickoff_tolerance

        self._teams: defaultdict[tuple[object, ...], set[str]] = defaultdict(set)
        for identity in team_identities:
            for alias in identity.aliases:
                self._teams[_alias_key(alias)].add(identity.internal_team_id)

        self._competitions: defaultdict[tuple[object, ...], set[str]] = defaultdict(
            set
        )
        for mapping in competition_mappings:
            self._competitions[_competition_key(mapping)].add(
                mapping.internal_competition_id
            )

        matches_by_id: dict[str, CanonicalMatchIdentity] = {}
        for match in canonical_matches:
            previous = matches_by_id.get(match.internal_match_id)
            if previous is not None and previous != match:
                raise ValueError(
                    "canonical match ID is assigned to conflicting identities: "
                    f"{match.internal_match_id}"
                )
            matches_by_id[match.internal_match_id] = match
        self._canonical_matches = tuple(matches_by_id.values())
        self._explicit = _index_explicit_mappings(explicit_mappings)

    def resolve(self, provider_match: ProviderMatchIdentity) -> MatchResolution:
        explicit = self._explicit.get(
            (
                provider_match.provider_code,
                provider_match.external_namespace,
                provider_match.provider_match_id,
            ),
            (),
        )
        if explicit:
            targets = {resolution.internal_match_id for resolution in explicit}
            if len(targets) != 1:
                raise AmbiguousMatchMappingError(
                    "explicit provider match mappings have conflicting targets",
                    provider_match=provider_match,
                    candidates=targets,
                )
            return explicit[0]

        home_team_ids = self._teams.get(_provider_home_team_key(provider_match), set())
        away_team_ids = self._teams.get(_provider_away_team_key(provider_match), set())
        competition_ids = self._competitions.get(
            _provider_competition_key(provider_match), set()
        )

        self._require_single_mapping(provider_match, "home team", home_team_ids)
        self._require_single_mapping(provider_match, "away team", away_team_ids)
        self._require_single_mapping(provider_match, "competition", competition_ids)

        home_team_id = next(iter(home_team_ids))
        away_team_id = next(iter(away_team_ids))
        competition_id = next(iter(competition_ids))
        candidates = tuple(
            match
            for match in self._canonical_matches
            if match.internal_competition_id == competition_id
            and match.internal_home_team_id == home_team_id
            and match.internal_away_team_id == away_team_id
            and match.season == provider_match.season
            and match.competition_type == provider_match.competition_type
            and abs(match.kickoff_at_utc - provider_match.kickoff_at_utc)
            <= self.kickoff_tolerance
        )
        if not candidates:
            raise UnresolvedMatchMappingError(
                "no canonical match satisfies the exact mappings and kickoff tolerance",
                provider_match=provider_match,
            )
        candidate_ids = {match.internal_match_id for match in candidates}
        if len(candidate_ids) != 1:
            raise AmbiguousMatchMappingError(
                "multiple canonical matches satisfy the exact identity mapping",
                provider_match=provider_match,
                candidates=candidate_ids,
            )
        return MatchResolution(
            internal_match_id=candidates[0].internal_match_id,
            confidence=Decimal(1),
            resolution_method=EXACT_IDENTITY_RESOLUTION,
        )

    @staticmethod
    def _require_single_mapping(
        provider_match: ProviderMatchIdentity,
        label: str,
        internal_ids: set[str],
    ) -> None:
        if not internal_ids:
            raise UnresolvedMatchMappingError(
                f"no exact {label} mapping exists",
                provider_match=provider_match,
            )
        if len(internal_ids) > 1:
            raise AmbiguousMatchMappingError(
                f"exact {label} mapping has multiple canonical targets",
                provider_match=provider_match,
                candidates=internal_ids,
            )


def _alias_key(alias: Alias) -> tuple[object, ...]:
    return (
        alias.provider_code,
        alias.provider_team_id,
        alias.provider_team_name,
        alias.language,
        alias.team_type,
    )


def _provider_home_team_key(match: ProviderMatchIdentity) -> tuple[object, ...]:
    return (
        match.provider_code,
        match.home_team_id,
        match.home_team_name,
        match.home_team_language,
        match.home_team_type,
    )


def _provider_away_team_key(match: ProviderMatchIdentity) -> tuple[object, ...]:
    return (
        match.provider_code,
        match.away_team_id,
        match.away_team_name,
        match.away_team_language,
        match.away_team_type,
    )


def _competition_key(mapping: CompetitionMapping) -> tuple[object, ...]:
    return (
        mapping.provider_code,
        mapping.provider_competition_id,
        mapping.provider_competition_name,
        mapping.language,
        mapping.season,
        mapping.competition_type,
    )


def _provider_competition_key(match: ProviderMatchIdentity) -> tuple[object, ...]:
    return (
        match.provider_code,
        match.provider_competition_id,
        match.provider_competition_name,
        match.competition_language,
        match.season,
        match.competition_type,
    )


def _index_explicit_mappings(
    mappings: (
        Iterable[ExplicitMatchMapping | ProviderMatchMapping]
        | Mapping[tuple[str, str] | tuple[str, str, str], str]
    ),
) -> dict[tuple[str, str, str], tuple[MatchResolution, ...]]:
    indexed: defaultdict[tuple[str, str, str], list[MatchResolution]] = defaultdict(
        list
    )
    if isinstance(mappings, Mapping):
        for raw_key, internal_match_id in mappings.items():
            if len(raw_key) == 2:
                provider_code, provider_match_id = raw_key
                namespace = "match"
            elif len(raw_key) == 3:
                provider_code, namespace, provider_match_id = raw_key
            else:
                raise ValueError("explicit mapping keys must contain two or three items")
            indexed[(provider_code, namespace, provider_match_id)].append(
                MatchResolution(
                    internal_match_id=internal_match_id,
                    confidence=Decimal(1),
                    resolution_method=EXPLICIT_MAPPING_RESOLUTION,
                )
            )
        return {key: tuple(values) for key, values in indexed.items()}

    for raw_mapping in mappings:
        mapping = _coerce_explicit_mapping(raw_mapping)
        indexed[
            (
                mapping.provider_code,
                mapping.external_namespace,
                mapping.provider_match_id,
            )
        ].append(
            MatchResolution(
                internal_match_id=mapping.internal_match_id,
                confidence=mapping.confidence,
                resolution_method=mapping.resolution_method,
            )
        )
    return {key: tuple(values) for key, values in indexed.items()}


def _coerce_explicit_mapping(
    value: ExplicitMatchMapping | ProviderMatchMapping,
) -> ExplicitMatchMapping:
    if isinstance(value, ExplicitMatchMapping):
        return value
    return ExplicitMatchMapping(
        provider_code=value.provider_code,
        provider_match_id=value.external_match_id,
        external_namespace=value.external_namespace,
        internal_match_id=value.internal_match_id,
        confidence=value.confidence,
        resolution_method=value.resolution_method,
    )
