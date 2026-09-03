from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re

from football_system.application.identity_catalog import (
    FixtureIngestionCapture,
    FixtureIngestionRequest,
    FixtureObservation,
    MatchIdentityRegistration,
    RegisteredCanonicalMatch,
    RegisteredCompetitionMapping,
    RegisteredTeamAlias,
)
from football_system.application.ports.data_providers import (
    FixtureBatch,
    FixtureCaptureProvider,
    FixtureProvider,
    FixtureQuery,
)
from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_payload_sha256,
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
    TeamType,
)
from football_system.domain.raw_data import ProviderRequestResult
from football_system.infrastructure.files.raw_archive import (
    ArchivedRawArtifact,
    RawDataArchive,
)
from football_system.infrastructure.http.provider_client import ProviderHttpClient
from football_system.infrastructure.providers.real._common import (
    ProviderPayloadError,
    archive_successful_response,
    decode_json_payload,
)

SPORTMONKS_PROVIDER_CODE = "SPORTMONKS"
_STATUS_BY_TOKEN = {
    "NS": MatchStatus.SCHEDULED,
    "TBD": MatchStatus.SCHEDULED,
    "FT": MatchStatus.FINISHED,
    "AET": MatchStatus.FINISHED,
    "FTP": MatchStatus.FINISHED,
    "FT_PEN": MatchStatus.FINISHED,
    "POSTP": MatchStatus.POSTPONED,
    "POSTPONED": MatchStatus.POSTPONED,
    "CANCL": MatchStatus.CANCELLED,
    "CANCELLED": MatchStatus.CANCELLED,
}
_SPORTMONKS_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})?$"
)


class SportmonksFixtureProvider(FixtureProvider, FixtureCaptureProvider):
    """Normalizes Sportmonks fixtures as the canonical fixture anchor."""

    provider_code = SPORTMONKS_PROVIDER_CODE
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.LIVE,
        provider_code=provider_code,
        provenance="Sportmonks current fixtures endpoint",
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )

    def __init__(
        self,
        client: ProviderHttpClient,
        raw_archive: RawDataArchive,
        api_token: str,
    ) -> None:
        if (
            not isinstance(api_token, str)
            or not api_token.strip()
            or any(character in api_token for character in ("\r", "\n", "\0"))
        ):
            raise ValueError("Sportmonks credential is invalid")
        self._client = client
        self._raw_archive = raw_archive
        self._headers = {"Authorization": api_token.strip()}
        self._last_raw_artifact: ArchivedRawArtifact | None = None

    @property
    def last_raw_artifact(self) -> ArchivedRawArtifact | None:
        return self._last_raw_artifact

    async def fetch_fixtures(self, query: FixtureQuery) -> FixtureBatch:
        result, payload = self._fetch_payload(
            query.kickoff_from_utc,
            query.kickoff_to_utc,
        )
        batch = _normalize_fixture_payload(
            payload,
            available_at_utc=result.audit.available_at_utc,
        )
        return _filter_batch(
            batch,
            kickoff_from_utc=query.kickoff_from_utc,
            kickoff_to_utc=query.kickoff_to_utc,
            as_of_at_utc=query.as_of_at_utc,
        )

    async def capture_fixtures(
        self,
        request: FixtureIngestionRequest,
    ) -> FixtureIngestionCapture:
        result, payload = self._fetch_payload(
            request.kickoff_from_utc,
            request.kickoff_to_utc,
            request=request,
        )
        available_at = result.audit.available_at_utc
        if available_at is None or self._last_raw_artifact is None:
            raise ProviderPayloadError(
                SPORTMONKS_PROVIDER_CODE,
                "response capture metadata is unavailable",
            )
        normalized = _normalize_fixture_details(
            payload,
            available_at_utc=available_at,
            expected_scope=request,
        )
        batch = _filter_batch(
            normalized.batch,
            kickoff_from_utc=request.kickoff_from_utc,
            kickoff_to_utc=request.kickoff_to_utc,
        )
        ingestion_id = stable_id(
            "fixture-ingestion",
            self.provider_code,
            self._last_raw_artifact.artifact_id,
        )
        mappings_by_match = {
            mapping.internal_match_id: mapping for mapping in batch.mappings
        }
        registration = MatchIdentityRegistration(
            created_at_utc=result.audit.received_at_utc,
            competitions=batch.competitions,
            teams=batch.teams,
            matches=batch.matches,
            team_aliases=tuple(
                RegisteredTeamAlias(
                    internal_team_id=team.team_id,
                    alias=Alias(
                        provider_code=self.provider_code,
                        provider_team_id=normalized.team_sources[team.team_id][0],
                        provider_team_name=normalized.team_sources[team.team_id][1],
                        language=request.language,
                        team_type=request.team_type,
                    ),
                    available_at_utc=available_at,
                )
                for team in batch.teams
            ),
            competition_mappings=tuple(
                RegisteredCompetitionMapping(
                    mapping=CompetitionMapping(
                        internal_competition_id=competition.competition_id,
                        provider_code=self.provider_code,
                        provider_competition_id=normalized.competition_sources[
                            competition.competition_id
                        ][0],
                        provider_competition_name=normalized.competition_sources[
                            competition.competition_id
                        ][1],
                        language=request.language,
                        season=request.season,
                        competition_type=request.competition_type,
                    ),
                    available_at_utc=available_at,
                )
                for competition in batch.competitions
            ),
            canonical_matches=tuple(
                RegisteredCanonicalMatch(
                    identity=CanonicalMatchIdentity(
                        internal_match_id=match.match_id,
                        internal_competition_id=match.competition_id,
                        internal_home_team_id=match.home_team_id,
                        internal_away_team_id=match.away_team_id,
                        season=request.season,
                        competition_type=request.competition_type,
                        kickoff_at_utc=match.kickoff_at_utc,
                    ),
                    available_at_utc=available_at,
                )
                for match in batch.matches
            ),
            explicit_mappings=batch.mappings,
        )
        observations = tuple(
            FixtureObservation(
                observation_id=stable_id(
                    "fixture-observation",
                    ingestion_id,
                    mappings_by_match[match.match_id].mapping_id,
                ),
                provider_mapping_id=mappings_by_match[match.match_id].mapping_id,
                external_match_id=mappings_by_match[match.match_id].external_match_id,
                internal_match_id=match.match_id,
                kickoff_at_utc=match.kickoff_at_utc,
                status=match.status,
                available_at_utc=available_at,
                payload_sha256=normalized.fixture_payload_hashes[
                    mappings_by_match[match.match_id].external_match_id
                ],
            )
            for match in batch.matches
        )
        return FixtureIngestionCapture(
            ingestion_id=ingestion_id,
            provider_code=self.provider_code,
            request=request,
            request_audit=result.audit,
            raw_artifact_id=self._last_raw_artifact.artifact_id,
            raw_payload_sha256=result.to_raw_artifact_metadata().payload_sha256,
            registration=registration,
            observations=observations,
        )

    def _fetch_payload(
        self,
        kickoff_from_utc: datetime,
        kickoff_to_utc: datetime,
        *,
        request: FixtureIngestionRequest | None = None,
    ) -> tuple[ProviderRequestResult, object]:
        endpoint = (
            "fixtures/between/"
            f"{kickoff_from_utc.date().isoformat()}/"
            f"{kickoff_to_utc.date().isoformat()}"
        )
        query_parameters = {
            "include": "participants;league.country;season;state",
            "per_page": 50,
        }
        if request is not None:
            query_parameters.update(
                {
                    "filters": (f"fixtureLeagues:{request.provider_competition_id}"),
                    "locale": request.language,
                }
            )
        result = self._client.get(
            endpoint,
            query_parameters=query_parameters,
            headers=self._headers,
        )
        artifact = archive_successful_response(
            self.provider_code,
            result,
            self._raw_archive,
        )
        self._last_raw_artifact = artifact
        payload = decode_json_payload(self.provider_code, result.payload or b"")
        _validate_complete_page(payload)
        return result, payload


@dataclass(frozen=True, slots=True)
class _NormalizedFixtureDetails:
    batch: FixtureBatch
    competition_sources: dict[str, tuple[str, str]]
    team_sources: dict[str, tuple[str, str]]
    fixture_payload_hashes: dict[str, str]


def _normalize_fixture_payload(
    payload: object,
    *,
    available_at_utc: datetime | None,
) -> FixtureBatch:
    return _normalize_fixture_details(
        payload,
        available_at_utc=available_at_utc,
    ).batch


def _normalize_fixture_details(
    payload: object,
    *,
    available_at_utc: datetime | None,
    expected_scope: FixtureIngestionRequest | None = None,
) -> _NormalizedFixtureDetails:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE, "payload data must be an array"
        )
    if available_at_utc is None:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "response receipt time is unavailable",
        )

    competitions: dict[str, Competition] = {}
    teams: dict[str, Team] = {}
    matches: dict[str, Match] = {}
    mappings: list[ProviderMatchMapping] = []
    competition_sources: dict[str, tuple[str, str]] = {}
    team_sources: dict[str, tuple[str, str]] = {}
    fixture_payload_hashes: dict[str, str] = {}
    for raw_fixture in payload["data"]:
        fixture = _object(raw_fixture, "fixture")
        fixture_external_id = _identifier(fixture.get("id"), "fixture id")
        if fixture_external_id in matches:
            raise ProviderPayloadError(
                SPORTMONKS_PROVIDER_CODE,
                "fixture response contains duplicate fixture IDs",
            )

        league = _object(fixture.get("league"), "fixture league")
        competition_external_id = _identifier(league.get("id"), "league id")
        if expected_scope is not None:
            _validate_fixture_scope(fixture, league, expected_scope)
        competition_name = _text(league.get("name"), "league name")
        competition = Competition(
            competition_id=stable_id("sportmonks-competition", competition_external_id),
            canonical_key=stable_id(
                "sportmonks-competition-key", competition_external_id
            ),
            name=competition_name,
            country_code=_country_code(league),
        )
        _add_unique(competitions, competition.competition_id, competition, "league")
        _add_unique(
            competition_sources,
            competition.competition_id,
            (competition_external_id, competition_name),
            "league identity",
        )

        participants = fixture.get("participants")
        if not isinstance(participants, list):
            raise ProviderPayloadError(
                SPORTMONKS_PROVIDER_CODE,
                "fixture participants must be an array",
            )
        home_raw, away_raw = _participants_by_location(participants)
        expected_team_type = (
            expected_scope.team_type if expected_scope is not None else None
        )
        home_team, home_source = _team(home_raw, expected_team_type)
        away_team, away_source = _team(away_raw, expected_team_type)
        _add_unique(teams, home_team.team_id, home_team, "team")
        _add_unique(teams, away_team.team_id, away_team, "team")
        _add_unique(
            team_sources,
            home_team.team_id,
            home_source,
            "team identity",
        )
        _add_unique(
            team_sources,
            away_team.team_id,
            away_source,
            "team identity",
        )

        kickoff_at_utc = _sportmonks_kickoff(fixture.get("starting_at"))
        match = Match(
            match_id=stable_id("sportmonks-fixture", fixture_external_id),
            competition_id=competition.competition_id,
            home_team_id=home_team.team_id,
            away_team_id=away_team.team_id,
            kickoff_at_utc=kickoff_at_utc,
            status=_status(fixture),
            # Receipt time is the only defensible availability time for live data.
            available_at_utc=available_at_utc,
        )
        matches[fixture_external_id] = match
        fixture_payload_hashes[fixture_external_id] = canonical_payload_sha256(fixture)
        mappings.append(
            ProviderMatchMapping(
                mapping_id=stable_id(
                    "provider-mapping",
                    SPORTMONKS_PROVIDER_CODE,
                    "fixture",
                    fixture_external_id,
                ),
                provider_code=SPORTMONKS_PROVIDER_CODE,
                external_namespace="fixture",
                external_match_id=fixture_external_id,
                internal_match_id=match.match_id,
                resolution_method="CANONICAL_PROVIDER_ANCHOR",
                confidence=Decimal(1),
                available_at_utc=available_at_utc,
            )
        )

    return _NormalizedFixtureDetails(
        batch=FixtureBatch(
            competitions=tuple(
                sorted(competitions.values(), key=lambda item: item.competition_id)
            ),
            teams=tuple(sorted(teams.values(), key=lambda item: item.team_id)),
            matches=tuple(sorted(matches.values(), key=lambda item: item.match_id)),
            mappings=tuple(sorted(mappings, key=lambda item: item.mapping_id)),
        ),
        competition_sources=competition_sources,
        team_sources=team_sources,
        fixture_payload_hashes=fixture_payload_hashes,
    )


def _filter_batch(
    batch: FixtureBatch,
    *,
    kickoff_from_utc: datetime,
    kickoff_to_utc: datetime,
    as_of_at_utc: datetime | None = None,
) -> FixtureBatch:
    matches = tuple(
        match
        for match in batch.matches
        if kickoff_from_utc <= match.kickoff_at_utc <= kickoff_to_utc
        and (as_of_at_utc is None or match.available_at_utc <= as_of_at_utc)
    )
    match_ids = {match.match_id for match in matches}
    competition_ids = {match.competition_id for match in matches}
    team_ids = {
        team_id
        for match in matches
        for team_id in (match.home_team_id, match.away_team_id)
    }
    return FixtureBatch(
        competitions=tuple(
            competition
            for competition in batch.competitions
            if competition.competition_id in competition_ids
        ),
        teams=tuple(team for team in batch.teams if team.team_id in team_ids),
        matches=matches,
        mappings=tuple(
            mapping
            for mapping in batch.mappings
            if mapping.internal_match_id in match_ids
        ),
    )


def _object(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            f"{field} must be an object",
        )
    return value


def _identifier(value: object, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if type(value) is int:
        return str(value)
    raise ProviderPayloadError(
        SPORTMONKS_PROVIDER_CODE,
        f"{field} must be a nonempty string or integer",
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            f"{field} must be a nonempty string",
        )
    return value.strip()


def _country_code(league: Mapping[str, object]) -> str:
    value = league.get("country_code")
    country = league.get("country")
    if value is None and isinstance(country, Mapping):
        value = country.get("iso2", country.get("iso_2", country.get("code")))
    if not isinstance(value, str):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "league country code is required",
        )
    normalized = value.strip().upper()
    if len(normalized) not in {2, 3} or not normalized.isalpha():
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "league country code is invalid",
        )
    return normalized


def _participants_by_location(
    participants: list[object],
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    by_location: dict[str, Mapping[str, object]] = {}
    for raw_participant in participants:
        participant = _object(raw_participant, "participant")
        meta = _object(participant.get("meta"), "participant meta")
        location = meta.get("location")
        if not isinstance(location, str):
            raise ProviderPayloadError(
                SPORTMONKS_PROVIDER_CODE,
                "participant location is required",
            )
        normalized = location.strip().casefold()
        if normalized not in {"home", "away"}:
            continue
        if normalized in by_location:
            raise ProviderPayloadError(
                SPORTMONKS_PROVIDER_CODE,
                "fixture has duplicate home or away participant",
            )
        by_location[normalized] = participant
    if set(by_location) != {"home", "away"}:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "fixture must include exactly one home and one away participant",
        )
    return by_location["home"], by_location["away"]


def _team(
    participant: Mapping[str, object],
    expected_team_type: TeamType | None,
) -> tuple[Team, tuple[str, str]]:
    if participant.get("placeholder") is True:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "placeholder participants cannot establish canonical identity",
        )
    external_id = _identifier(participant.get("id"), "participant id")
    name = _text(participant.get("name"), "participant name")
    team_type = _provider_team_type(participant)
    if expected_team_type is not None and team_type is not expected_team_type:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "participant type conflicts with requested identity scope",
        )
    return (
        Team(
            team_id=stable_id("sportmonks-team", external_id),
            canonical_key=stable_id("sportmonks-team-key", external_id),
            name=name,
            team_type=team_type,
        ),
        (external_id, name),
    )


def _status(fixture: Mapping[str, object]) -> MatchStatus:
    token = _status_token(fixture)
    status = _STATUS_BY_TOKEN.get(token)
    if status is None:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "fixture status is unsupported",
        )
    return status


def _status_token(fixture: Mapping[str, object]) -> str:
    for field in ("state", "status"):
        raw = fixture.get(field)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
        if isinstance(raw, Mapping):
            for key in ("short_name", "developer_name", "code", "state"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().upper()
    raise ProviderPayloadError(
        SPORTMONKS_PROVIDER_CODE,
        "fixture status is required",
    )


def _validate_complete_page(payload: object) -> None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "payload data must be an array",
        )
    pagination = _object(payload.get("pagination"), "pagination")
    count = pagination.get("count")
    per_page = pagination.get("per_page")
    current_page = pagination.get("current_page")
    has_more = pagination.get("has_more")
    if (
        type(count) is not int
        or count != len(payload["data"])
        or count > 50
        or type(per_page) is not int
        or per_page != 50
        or type(current_page) is not int
        or current_page != 1
        or "next_cursor" not in pagination
        or type(has_more) is not bool
    ):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "pagination metadata is inconsistent",
        )
    if has_more:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "fixture response requires another page; narrow the kickoff window",
        )
    if pagination["next_cursor"] is not None:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "pagination metadata is inconsistent",
        )
    rate_limit = _object(payload.get("rate_limit"), "rate_limit")
    if (
        type(rate_limit.get("resets_in_seconds")) is not int
        or rate_limit["resets_in_seconds"] < 0
        or type(rate_limit.get("remaining")) is not int
        or rate_limit["remaining"] < 0
        or rate_limit.get("requested_entity") != "Fixture"
    ):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "rate_limit metadata is inconsistent",
        )


def _validate_fixture_scope(
    fixture: Mapping[str, object],
    league: Mapping[str, object],
    request: FixtureIngestionRequest,
) -> None:
    if fixture.get("placeholder") is True:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "placeholder fixtures cannot establish canonical identity",
        )
    league_id = _identifier(fixture.get("league_id"), "fixture league_id")
    included_league_id = _identifier(league.get("id"), "league id")
    season_id = _identifier(fixture.get("season_id"), "fixture season_id")
    season = _object(fixture.get("season"), "fixture season")
    included_season_id = _identifier(season.get("id"), "season id")
    season_league_id = _identifier(season.get("league_id"), "season league_id")
    season_name = _text(season.get("name"), "season name")
    league_type = _text(league.get("type"), "league type")
    if (
        league_id != request.provider_competition_id
        or included_league_id != request.provider_competition_id
        or season_league_id != request.provider_competition_id
        or season_id != request.provider_season_id
        or included_season_id != request.provider_season_id
        or season_name != request.season
        or league_type.casefold() != request.competition_type.casefold()
    ):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "fixture response conflicts with requested league or season scope",
        )


def _provider_team_type(participant: Mapping[str, object]) -> TeamType:
    provider_type = _text(participant.get("type"), "participant type").casefold()
    gender = _text(participant.get("gender"), "participant gender").casefold()
    if gender == "female" and provider_type in {"domestic", "national"}:
        return TeamType.WOMEN
    if gender == "male" and provider_type == "national":
        return TeamType.NATIONAL
    if gender == "male" and provider_type == "domestic":
        return TeamType.CLUB
    raise ProviderPayloadError(
        SPORTMONKS_PROVIDER_CODE,
        "participant type or gender is unsupported",
    )


def _sportmonks_kickoff(value: object) -> datetime:
    if not isinstance(value, str) or not _SPORTMONKS_TIMESTAMP.fullmatch(value):
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "fixture starting_at must be a Sportmonks timestamp",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            "fixture starting_at must be a Sportmonks timestamp",
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _add_unique[T](
    values: dict[str, T],
    identifier: str,
    value: T,
    label: str,
) -> None:
    previous = values.get(identifier)
    if previous is not None and previous != value:
        raise ProviderPayloadError(
            SPORTMONKS_PROVIDER_CODE,
            f"provider response contains conflicting {label} definitions",
        )
    values[identifier] = value
