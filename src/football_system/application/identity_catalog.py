from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Self

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
    MatchIdentityResolver,
    TeamIdentity,
)
from football_system.domain.match import (
    Competition,
    Match,
    MatchStatus,
    ProviderMatchMapping,
    Team,
    TeamType,
)
from football_system.domain.raw_data import (
    ProviderRequestAudit,
    ProviderRequestOutcome,
)


class RegisteredTeamAlias(DomainModel):
    internal_team_id: Identifier
    alias: Alias
    available_at_utc: UtcDateTime


class RegisteredCompetitionMapping(DomainModel):
    mapping: CompetitionMapping
    available_at_utc: UtcDateTime


class RegisteredCanonicalMatch(DomainModel):
    identity: CanonicalMatchIdentity
    available_at_utc: UtcDateTime


class MatchIdentityRegistration(DomainModel):
    created_at_utc: UtcDateTime
    competitions: tuple[Competition, ...]
    teams: tuple[Team, ...]
    matches: tuple[Match, ...]
    team_aliases: tuple[RegisteredTeamAlias, ...]
    competition_mappings: tuple[RegisteredCompetitionMapping, ...]
    canonical_matches: tuple[RegisteredCanonicalMatch, ...]
    explicit_mappings: tuple[ProviderMatchMapping, ...] = ()

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        competitions = _unique_index(
            self.competitions,
            lambda item: item.competition_id,
            "competition IDs",
        )
        _reject_duplicate_keys(
            self.competitions,
            lambda item: item.canonical_key,
            "competition canonical keys",
        )
        teams = _unique_index(
            self.teams,
            lambda item: item.team_id,
            "team IDs",
        )
        _reject_duplicate_keys(
            self.teams,
            lambda item: item.canonical_key,
            "team canonical keys",
        )
        matches = _unique_index(
            self.matches,
            lambda item: item.match_id,
            "match IDs",
        )
        canonical_matches = _unique_index(
            self.canonical_matches,
            lambda item: item.identity.internal_match_id,
            "canonical match IDs",
        )
        _reject_duplicate_keys(
            self.team_aliases,
            _team_alias_key,
            "team aliases",
        )
        _reject_duplicate_keys(
            self.competition_mappings,
            _competition_mapping_key,
            "competition mappings",
        )
        _reject_duplicate_keys(
            self.explicit_mappings,
            lambda item: item.mapping_id,
            "explicit mapping IDs",
        )
        _reject_duplicate_keys(
            self.explicit_mappings,
            lambda item: (
                item.provider_code,
                item.external_namespace,
                item.external_match_id,
            ),
            "explicit provider match identities",
        )

        competition_ids = set(competitions)
        team_ids = set(teams)
        match_ids = set(matches)
        for match in self.matches:
            if match.competition_id not in competition_ids:
                raise ValueError(
                    f"match references an unknown competition: {match.match_id}"
                )
            if match.home_team_id not in team_ids or match.away_team_id not in team_ids:
                raise ValueError(f"match references an unknown team: {match.match_id}")

        if set(canonical_matches) != match_ids:
            raise ValueError(
                "every registered match must have exactly one canonical identity"
            )
        for match_id, registered in canonical_matches.items():
            match = matches[match_id]
            identity = registered.identity
            if (
                identity.internal_competition_id not in competition_ids
                or identity.internal_home_team_id not in team_ids
                or identity.internal_away_team_id not in team_ids
            ):
                raise ValueError(
                    f"canonical match references an unknown identity: {match_id}"
                )
            if (
                identity.internal_competition_id != match.competition_id
                or identity.internal_home_team_id != match.home_team_id
                or identity.internal_away_team_id != match.away_team_id
                or identity.kickoff_at_utc != match.kickoff_at_utc
            ):
                raise ValueError(
                    "canonical match identity conflicts with its registered match: "
                    f"{match_id}"
                )

        aliased_team_ids: set[str] = set()
        for registered in self.team_aliases:
            team = teams.get(registered.internal_team_id)
            if team is None:
                raise ValueError(
                    "team alias references an unknown team: "
                    f"{registered.internal_team_id}"
                )
            if registered.alias.team_type is not team.team_type:
                raise ValueError(
                    "team alias type conflicts with its registered team: "
                    f"{registered.internal_team_id}"
                )
            aliased_team_ids.add(registered.internal_team_id)
        if aliased_team_ids != team_ids:
            raise ValueError("every registered team must have at least one alias")

        mapped_competition_ids: set[str] = set()
        for registered in self.competition_mappings:
            competition_id = registered.mapping.internal_competition_id
            if competition_id not in competition_ids:
                raise ValueError(
                    "competition mapping references an unknown competition: "
                    f"{competition_id}"
                )
            mapped_competition_ids.add(competition_id)
        if mapped_competition_ids != competition_ids:
            raise ValueError(
                "every registered competition must have at least one mapping"
            )

        for mapping in self.explicit_mappings:
            if mapping.internal_match_id not in match_ids:
                raise ValueError(
                    "explicit mapping references an unknown match: "
                    f"{mapping.internal_match_id}"
                )

        available_values = (
            *(match.available_at_utc for match in self.matches),
            *(item.available_at_utc for item in self.team_aliases),
            *(item.available_at_utc for item in self.competition_mappings),
            *(item.available_at_utc for item in self.canonical_matches),
            *(item.available_at_utc for item in self.explicit_mappings),
        )
        if any(value > self.created_at_utc for value in available_values):
            raise ValueError(
                "identity availability timestamps cannot follow registration creation"
            )
        return self


class FixtureIngestionRequest(DomainModel):
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    provider_competition_id: Identifier
    provider_season_id: Identifier
    season: Identifier
    competition_type: Identifier
    language: str = Field(default="en", min_length=2, max_length=35)
    team_type: TeamType

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("kickoff window start cannot follow its end")
        if self.kickoff_to_utc - self.kickoff_from_utc > timedelta(days=100):
            raise ValueError("Sportmonks fixture windows cannot exceed 100 days")
        if self.team_type not in {
            TeamType.CLUB,
            TeamType.NATIONAL,
            TeamType.WOMEN,
        }:
            raise ValueError("Sportmonks fixture ingestion cannot infer this team type")
        return self


class FixtureObservation(DomainModel):
    observation_id: Identifier
    provider_mapping_id: Identifier
    external_match_id: Identifier
    internal_match_id: Identifier
    kickoff_at_utc: UtcDateTime
    status: MatchStatus
    available_at_utc: UtcDateTime
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FixtureIngestionCapture(DomainModel):
    ingestion_id: Identifier
    provider_code: Identifier
    request: FixtureIngestionRequest
    request_audit: ProviderRequestAudit
    raw_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registration: MatchIdentityRegistration
    observations: tuple[FixtureObservation, ...]

    @model_validator(mode="after")
    def validate_capture(self) -> Self:
        if self.request_audit.outcome is not ProviderRequestOutcome.SUCCESS:
            raise ValueError("fixture ingestion requires a successful provider request")
        if self.request_audit.provider != self.provider_code:
            raise ValueError("fixture ingestion provider conflicts with request audit")
        available_at = self.request_audit.available_at_utc
        if available_at is None:
            raise ValueError("fixture ingestion requires provider availability time")
        if self.registration.created_at_utc != self.request_audit.received_at_utc:
            raise ValueError("identity registration must be created at response receipt")

        matches = _unique_index(
            self.registration.matches,
            lambda item: item.match_id,
            "fixture ingestion match IDs",
        )
        observations = _unique_index(
            self.observations,
            lambda item: item.internal_match_id,
            "fixture observation match IDs",
        )
        _reject_duplicate_keys(
            self.observations,
            lambda item: item.observation_id,
            "fixture observation IDs",
        )
        if set(observations) != set(matches):
            raise ValueError("fixture ingestion requires one observation per match")

        mappings = {
            mapping.mapping_id: mapping
            for mapping in self.registration.explicit_mappings
            if mapping.provider_code == self.provider_code
        }
        if len(mappings) != len(self.registration.explicit_mappings):
            raise ValueError("fixture mappings must use the capture provider")
        if set(mappings) != {
            observation.provider_mapping_id for observation in self.observations
        }:
            raise ValueError("fixture ingestion requires one mapping per observation")
        for observation in self.observations:
            match = matches[observation.internal_match_id]
            mapping = mappings.get(observation.provider_mapping_id)
            if mapping is None or (
                mapping.internal_match_id != observation.internal_match_id
                or mapping.external_match_id != observation.external_match_id
            ):
                raise ValueError(
                    "fixture observation requires its exact provider match mapping"
                )
            if (
                observation.kickoff_at_utc != match.kickoff_at_utc
                or observation.status is not match.status
                or observation.available_at_utc != available_at
                or not (
                    self.request.kickoff_from_utc
                    <= observation.kickoff_at_utc
                    <= self.request.kickoff_to_utc
                )
            ):
                raise ValueError("fixture observation conflicts with normalized match")

        availability_values = (
            *(match.available_at_utc for match in self.registration.matches),
            *(item.available_at_utc for item in self.registration.team_aliases),
            *(
                item.available_at_utc
                for item in self.registration.competition_mappings
            ),
            *(item.available_at_utc for item in self.registration.canonical_matches),
            *(item.available_at_utc for item in self.registration.explicit_mappings),
        )
        if any(value != available_at for value in availability_values):
            raise ValueError("fixture identities must use response availability time")
        if any(
            item.alias.provider_code != self.provider_code
            or item.alias.language != self.request.language
            or item.alias.team_type is not self.request.team_type
            for item in self.registration.team_aliases
        ):
            raise ValueError("fixture team aliases conflict with ingestion scope")
        if any(
            item.mapping.provider_code != self.provider_code
            or item.mapping.provider_competition_id
            != self.request.provider_competition_id
            or item.mapping.language != self.request.language
            or item.mapping.season != self.request.season
            or item.mapping.competition_type != self.request.competition_type
            for item in self.registration.competition_mappings
        ) or any(
            item.identity.season != self.request.season
            or item.identity.competition_type != self.request.competition_type
            for item in self.registration.canonical_matches
        ):
            raise ValueError("fixture competition identities conflict with ingestion scope")
        return self


class FixtureIngestionSummary(DomainModel):
    ingestion_id: Identifier
    raw_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    inserted: bool
    competition_count: int = Field(ge=0, strict=True)
    team_count: int = Field(ge=0, strict=True)
    match_count: int = Field(ge=0, strict=True)
    observation_count: int = Field(ge=0, strict=True)


class MatchIdentityCatalog(DomainModel):
    team_identities: tuple[TeamIdentity, ...]
    competition_mappings: tuple[CompetitionMapping, ...]
    canonical_matches: tuple[CanonicalMatchIdentity, ...]
    explicit_mappings: tuple[ProviderMatchMapping, ...]

    def build_resolver(self, kickoff_tolerance: timedelta) -> MatchIdentityResolver:
        return MatchIdentityResolver(
            team_identities=self.team_identities,
            competition_mappings=self.competition_mappings,
            canonical_matches=self.canonical_matches,
            explicit_mappings=self.explicit_mappings,
            kickoff_tolerance=kickoff_tolerance,
        )


def _unique_index[Item, Key](
    items: tuple[Item, ...],
    key: Callable[[Item], Key],
    label: str,
) -> dict[Key, Item]:
    indexed: dict[Key, Item] = {}
    for item in items:
        value = key(item)
        if value in indexed:
            raise ValueError(f"{label} must be unique: {value!r}")
        indexed[value] = item
    return indexed


def _reject_duplicate_keys[Item, Key](
    items: tuple[Item, ...],
    key: Callable[[Item], Key],
    label: str,
) -> None:
    _unique_index(items, key, label)


def _team_alias_key(item: RegisteredTeamAlias) -> tuple[object, ...]:
    alias = item.alias
    return (
        item.internal_team_id,
        alias.provider_code,
        alias.provider_team_id,
        alias.provider_team_name,
        alias.language,
        alias.team_type,
    )


def _competition_mapping_key(
    item: RegisteredCompetitionMapping,
) -> tuple[object, ...]:
    mapping = item.mapping
    return (
        mapping.internal_competition_id,
        mapping.provider_code,
        mapping.provider_competition_id,
        mapping.provider_competition_name,
        mapping.language,
        mapping.season,
        mapping.competition_type,
    )
