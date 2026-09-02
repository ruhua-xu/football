from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from football_system.application.ports.data_providers import (
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
)
from football_system.domain.common import stable_id
from football_system.domain.match import (
    Competition,
    Match,
    MatchStatus,
    ProviderMatchMapping,
    Team,
)
from football_system.infrastructure.files.raw_archive import (
    ArchivedRawArtifact,
    RawDataArchive,
)
from football_system.infrastructure.http.provider_client import ProviderHttpClient
from football_system.infrastructure.providers.real._common import (
    ProviderPayloadError,
    archive_successful_response,
    decode_json_payload,
    parse_utc_timestamp,
)

SPORTMONKS_PROVIDER_CODE = "SPORTMONKS"
_STATUS_BY_TOKEN = {
    "NS": MatchStatus.SCHEDULED,
    "TBD": MatchStatus.SCHEDULED,
    "FT": MatchStatus.FINISHED,
    "AET": MatchStatus.FINISHED,
    "PEN": MatchStatus.FINISHED,
}


class SportmonksFixtureProvider(FixtureProvider):
    """Normalizes Sportmonks fixtures as the canonical fixture anchor."""

    provider_code = SPORTMONKS_PROVIDER_CODE

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
        endpoint = (
            "/fixtures/between/"
            f"{query.kickoff_from_utc.date().isoformat()}/"
            f"{query.kickoff_to_utc.date().isoformat()}"
        )
        result = self._client.get(
            endpoint,
            query_parameters={
                "include": "participants;league;league.country;state",
            },
            headers=self._headers,
        )
        artifact = archive_successful_response(
            self.provider_code,
            result,
            self._raw_archive,
        )
        self._last_raw_artifact = artifact
        payload = decode_json_payload(self.provider_code, result.payload or b"")
        batch = _normalize_fixture_payload(
            payload,
            available_at_utc=result.audit.available_at_utc,
        )
        matches = tuple(
            match
            for match in batch.matches
            if query.kickoff_from_utc <= match.kickoff_at_utc <= query.kickoff_to_utc
            and match.available_at_utc <= query.as_of_at_utc
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


def _normalize_fixture_payload(
    payload: object,
    *,
    available_at_utc: datetime | None,
) -> FixtureBatch:
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
        competition = Competition(
            competition_id=stable_id("sportmonks-competition", competition_external_id),
            canonical_key=stable_id(
                "sportmonks-competition-key", competition_external_id
            ),
            name=_text(league.get("name"), "league name"),
            country_code=_country_code(league),
        )
        _add_unique(competitions, competition.competition_id, competition, "league")

        participants = fixture.get("participants")
        if not isinstance(participants, list):
            raise ProviderPayloadError(
                SPORTMONKS_PROVIDER_CODE,
                "fixture participants must be an array",
            )
        home_raw, away_raw = _participants_by_location(participants)
        home_team = _team(home_raw)
        away_team = _team(away_raw)
        _add_unique(teams, home_team.team_id, home_team, "team")
        _add_unique(teams, away_team.team_id, away_team, "team")

        kickoff_at_utc = parse_utc_timestamp(
            SPORTMONKS_PROVIDER_CODE,
            fixture.get("starting_at"),
            field="fixture starting_at",
        )
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

    return FixtureBatch(
        competitions=tuple(
            sorted(competitions.values(), key=lambda item: item.competition_id)
        ),
        teams=tuple(sorted(teams.values(), key=lambda item: item.team_id)),
        matches=tuple(sorted(matches.values(), key=lambda item: item.match_id)),
        mappings=tuple(sorted(mappings, key=lambda item: item.mapping_id)),
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


def _team(participant: Mapping[str, object]) -> Team:
    external_id = _identifier(participant.get("id"), "participant id")
    return Team(
        team_id=stable_id("sportmonks-team", external_id),
        canonical_key=stable_id("sportmonks-team-key", external_id),
        name=_text(participant.get("name"), "participant name"),
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
            for key in ("short_name", "code", "state"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().upper()
    raise ProviderPayloadError(
        SPORTMONKS_PROVIDER_CODE,
        "fixture status is required",
    )


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
