from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from football_system.application.identity_catalog import (
    FixtureIngestionCapture,
    FixtureIngestionSummary,
    MatchIdentityCatalog,
    MatchIdentityRegistration,
    RegisteredCompetitionMapping,
    RegisteredTeamAlias,
)
from football_system.domain.common import normalize_utc, stable_id, utc_now
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
    TeamIdentity,
)
from football_system.domain.match import ProviderMatchMapping, TeamType
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    FixtureIngestionCaptureRecord,
    FixtureObservationRecord,
    MatchRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
)


class SqlAlchemyMatchIdentityRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._session_factory = session_factory
        self._clock = clock

    def register(self, registration: MatchIdentityRegistration) -> None:
        registration = MatchIdentityRegistration.model_validate(
            registration.model_dump(mode="python")
        )
        try:
            with self._session_factory.begin() as session:
                pending = _preflight_registration(session, registration)
                _persist_pending_registration(session, pending)
        except IntegrityError as error:
            raise ValueError(
                "identity registration conflicts with stored immutable data"
            ) from error

    def register_fixture_ingestion(
        self,
        capture: FixtureIngestionCapture,
    ) -> FixtureIngestionSummary:
        capture = FixtureIngestionCapture.model_validate(
            capture.model_dump(mode="python")
        )
        available_at = _capture_available_at(capture)
        provider_id = stable_id("provider", capture.provider_code)
        parent_fields = _fixture_ingestion_fields(capture, provider_id)
        try:
            with self._session_factory.begin() as session:
                existing = _find_fixture_ingestion(session, capture)
                pending = _preflight_registration(
                    session,
                    capture.registration,
                    fixture_available_at=available_at,
                    required_provider_codes=(capture.provider_code,),
                )
                if existing is not None:
                    _assert_record_fields(
                        existing,
                        parent_fields,
                        f"fixture ingestion {capture.ingestion_id}",
                    )
                    if pending.base_records or pending.identity_records:
                        raise ValueError(
                            "fixture ingestion replay has an incomplete identity graph"
                        )
                    _verify_fixture_observations(session, capture)
                    inserted = False
                else:
                    _assert_observations_not_backdated(session, capture)
                    ingested_at = _new_ingested_at_utc(self._clock, capture)
                    _mark_fixture_registration_origin(
                        pending,
                        capture.ingestion_id,
                    )
                    _persist_fixture_registration(
                        session,
                        pending,
                        FixtureIngestionCaptureRecord(
                            **parent_fields, ingested_at_utc=ingested_at
                        ),
                    )
                    session.add_all(
                        FixtureObservationRecord(
                            observation_id=observation.observation_id,
                            ingestion_id=capture.ingestion_id,
                            provider_mapping_id=observation.provider_mapping_id,
                            internal_match_id=observation.internal_match_id,
                            kickoff_at_utc=observation.kickoff_at_utc,
                            status=observation.status.value,
                            available_at_utc=observation.available_at_utc,
                            payload_sha256=observation.payload_sha256,
                        )
                        for observation in capture.observations
                    )
                    session.flush()
                    inserted = True
        except IntegrityError as error:
            raise ValueError(
                "fixture ingestion conflicts with stored immutable data"
            ) from error

        return FixtureIngestionSummary(
            ingestion_id=capture.ingestion_id,
            raw_artifact_id=capture.raw_artifact_id,
            inserted=inserted,
            competition_count=len(capture.registration.competitions),
            team_count=len(capture.registration.teams),
            match_count=len(capture.registration.matches),
            observation_count=len(capture.observations),
        )

    def load_catalog(
        self,
        *,
        as_of_at_utc: datetime,
        kickoff_from_utc: datetime,
        kickoff_to_utc: datetime,
        provider_codes: tuple[str, ...] = (),
    ) -> MatchIdentityCatalog:
        cutoff = normalize_utc(as_of_at_utc)
        kickoff_from = normalize_utc(kickoff_from_utc)
        kickoff_to = normalize_utc(kickoff_to_utc)
        if kickoff_from > kickoff_to:
            raise ValueError("kickoff window start cannot follow its end")
        requested_providers = tuple(provider_codes)
        if len(requested_providers) != len(set(requested_providers)) or any(
            not isinstance(code, str)
            or not code.strip()
            or code != code.strip()
            or len(code) > 160
            for code in requested_providers
        ):
            raise ValueError("provider codes must be unique canonical identifiers")

        home_team = aliased(TeamRecord)
        away_team = aliased(TeamRecord)
        visible_fixture_ingestion_ids = select(
            FixtureIngestionCaptureRecord.ingestion_id
        ).where(FixtureIngestionCaptureRecord.ingested_at_utc <= cutoff)
        latest_observed_kickoff = (
            select(FixtureObservationRecord.kickoff_at_utc)
            .join(
                FixtureIngestionCaptureRecord,
                FixtureIngestionCaptureRecord.ingestion_id
                == FixtureObservationRecord.ingestion_id,
            )
            .where(
                FixtureObservationRecord.internal_match_id
                == MatchRecord.internal_match_id,
                FixtureObservationRecord.available_at_utc <= cutoff,
                FixtureIngestionCaptureRecord.ingested_at_utc <= cutoff,
            )
            .order_by(
                FixtureObservationRecord.available_at_utc.desc(),
                FixtureIngestionCaptureRecord.ingested_at_utc.desc(),
                FixtureObservationRecord.observation_id.desc(),
            )
            .limit(1)
            .correlate(MatchRecord)
            .scalar_subquery()
        )
        effective_kickoff = func.coalesce(
            latest_observed_kickoff,
            MatchRecord.kickoff_at_utc,
        )
        match_statement = (
            select(
                MatchRecord,
                CanonicalMatchIdentityRecord,
                CompetitionRecord,
                home_team,
                away_team,
                effective_kickoff.label("effective_kickoff_at_utc"),
            )
            .join(
                CanonicalMatchIdentityRecord,
                CanonicalMatchIdentityRecord.internal_match_id
                == MatchRecord.internal_match_id,
            )
            .join(
                CompetitionRecord,
                CompetitionRecord.competition_id == MatchRecord.competition_id,
            )
            .join(home_team, home_team.team_id == MatchRecord.home_team_id)
            .join(away_team, away_team.team_id == MatchRecord.away_team_id)
            .where(
                MatchRecord.available_at_utc <= cutoff,
                CanonicalMatchIdentityRecord.available_at_utc <= cutoff,
                or_(
                    MatchRecord.fixture_ingestion_id.is_(None),
                    MatchRecord.fixture_ingestion_id.in_(visible_fixture_ingestion_ids),
                ),
                or_(
                    CanonicalMatchIdentityRecord.fixture_ingestion_id.is_(None),
                    CanonicalMatchIdentityRecord.fixture_ingestion_id.in_(
                        visible_fixture_ingestion_ids
                    ),
                ),
                effective_kickoff >= kickoff_from,
                effective_kickoff <= kickoff_to,
            )
            .order_by(effective_kickoff, MatchRecord.internal_match_id)
        )
        with self._session_factory() as session:
            match_rows = tuple(session.execute(match_statement))
            if not match_rows:
                return MatchIdentityCatalog(
                    team_identities=(),
                    competition_mappings=(),
                    canonical_matches=(),
                    explicit_mappings=(),
                )

            team_records: dict[str, TeamRecord] = {}
            competition_ids: set[str] = set()
            competition_scopes: set[tuple[str, str, str]] = set()
            match_ids: list[str] = []
            canonical_matches: list[CanonicalMatchIdentity] = []
            for match, metadata, competition, home, away, kickoff_at in match_rows:
                team_records[home.team_id] = home
                team_records[away.team_id] = away
                competition_ids.add(competition.competition_id)
                competition_scopes.add(
                    (
                        competition.competition_id,
                        metadata.season,
                        metadata.competition_type,
                    )
                )
                match_ids.append(match.internal_match_id)
                canonical_matches.append(
                    CanonicalMatchIdentity(
                        internal_match_id=match.internal_match_id,
                        internal_competition_id=competition.competition_id,
                        internal_home_team_id=home.team_id,
                        internal_away_team_id=away.team_id,
                        season=metadata.season,
                        competition_type=metadata.competition_type,
                        kickoff_at_utc=kickoff_at,
                    )
                )

            alias_statement = (
                select(ProviderTeamAliasRecord, ProviderRecord)
                .join(
                    ProviderRecord,
                    ProviderRecord.provider_id == ProviderTeamAliasRecord.provider_id,
                )
                .where(
                    ProviderTeamAliasRecord.internal_team_id.in_(team_records),
                    ProviderTeamAliasRecord.available_at_utc <= cutoff,
                    or_(
                        ProviderTeamAliasRecord.fixture_ingestion_id.is_(None),
                        ProviderTeamAliasRecord.fixture_ingestion_id.in_(
                            visible_fixture_ingestion_ids
                        ),
                    ),
                )
                .order_by(
                    ProviderTeamAliasRecord.internal_team_id,
                    ProviderRecord.code,
                    ProviderTeamAliasRecord.provider_team_id,
                    ProviderTeamAliasRecord.provider_team_name,
                    ProviderTeamAliasRecord.language,
                    ProviderTeamAliasRecord.team_type,
                    ProviderTeamAliasRecord.alias_id,
                )
            )
            competition_statement = (
                select(ProviderCompetitionMappingRecord, ProviderRecord)
                .join(
                    ProviderRecord,
                    ProviderRecord.provider_id
                    == ProviderCompetitionMappingRecord.provider_id,
                )
                .where(
                    ProviderCompetitionMappingRecord.internal_competition_id.in_(
                        competition_ids
                    ),
                    ProviderCompetitionMappingRecord.available_at_utc <= cutoff,
                    or_(
                        ProviderCompetitionMappingRecord.fixture_ingestion_id.is_(None),
                        ProviderCompetitionMappingRecord.fixture_ingestion_id.in_(
                            visible_fixture_ingestion_ids
                        ),
                    ),
                )
                .order_by(
                    ProviderCompetitionMappingRecord.internal_competition_id,
                    ProviderRecord.code,
                    ProviderCompetitionMappingRecord.provider_competition_id,
                    ProviderCompetitionMappingRecord.provider_competition_name,
                    ProviderCompetitionMappingRecord.language,
                    ProviderCompetitionMappingRecord.season,
                    ProviderCompetitionMappingRecord.competition_type,
                    ProviderCompetitionMappingRecord.mapping_id,
                )
            )
            explicit_statement = (
                select(ProviderMatchMappingRecord, ProviderRecord)
                .join(
                    ProviderRecord,
                    ProviderRecord.provider_id
                    == ProviderMatchMappingRecord.provider_id,
                )
                .where(
                    ProviderMatchMappingRecord.internal_match_id.in_(match_ids),
                    ProviderMatchMappingRecord.available_at_utc <= cutoff,
                    or_(
                        ProviderMatchMappingRecord.fixture_ingestion_id.is_(None),
                        ProviderMatchMappingRecord.fixture_ingestion_id.in_(
                            visible_fixture_ingestion_ids
                        ),
                    ),
                )
                .order_by(
                    ProviderRecord.code,
                    ProviderMatchMappingRecord.external_namespace,
                    ProviderMatchMappingRecord.external_match_id,
                    ProviderMatchMappingRecord.internal_match_id,
                    ProviderMatchMappingRecord.mapping_id,
                )
            )
            if requested_providers:
                alias_statement = alias_statement.where(
                    ProviderRecord.code.in_(requested_providers)
                )
                competition_statement = competition_statement.where(
                    ProviderRecord.code.in_(requested_providers)
                )
                explicit_statement = explicit_statement.where(
                    ProviderRecord.code.in_(requested_providers)
                )

            aliases_by_team: defaultdict[str, list[Alias]] = defaultdict(list)
            for record, provider in session.execute(alias_statement):
                aliases_by_team[record.internal_team_id].append(
                    Alias(
                        provider_code=provider.code,
                        provider_team_id=record.provider_team_id,
                        provider_team_name=record.provider_team_name,
                        language=record.language,
                        team_type=TeamType(record.team_type),
                    )
                )
            team_identities = tuple(
                TeamIdentity(
                    internal_team_id=team_id,
                    canonical_name=team_records[team_id].name,
                    team_type=TeamType(team_records[team_id].team_type),
                    aliases=tuple(aliases_by_team[team_id]),
                )
                for team_id in sorted(team_records)
                if aliases_by_team[team_id]
            )
            competition_mappings = tuple(
                CompetitionMapping(
                    internal_competition_id=record.internal_competition_id,
                    provider_code=provider.code,
                    provider_competition_id=record.provider_competition_id,
                    provider_competition_name=record.provider_competition_name,
                    language=record.language,
                    season=record.season,
                    competition_type=record.competition_type,
                )
                for record, provider in session.execute(competition_statement)
                if (
                    record.internal_competition_id,
                    record.season,
                    record.competition_type,
                )
                in competition_scopes
            )
            explicit_mappings = tuple(
                ProviderMatchMapping(
                    mapping_id=record.mapping_id,
                    provider_code=provider.code,
                    external_namespace=record.external_namespace,
                    external_match_id=record.external_match_id,
                    internal_match_id=record.internal_match_id,
                    resolution_method=record.resolution_method,
                    confidence=record.confidence,
                    available_at_utc=record.available_at_utc,
                )
                for record, provider in session.execute(explicit_statement)
            )

        return MatchIdentityCatalog(
            team_identities=team_identities,
            competition_mappings=competition_mappings,
            canonical_matches=tuple(canonical_matches),
            explicit_mappings=explicit_mappings,
        )


class _PendingRegistration:
    def __init__(
        self,
        base_records: list[object],
        identity_records: list[object],
    ) -> None:
        self.base_records = base_records
        self.identity_records = identity_records


def _persist_pending_registration(
    session: Session,
    pending: _PendingRegistration,
) -> None:
    session.add_all(
        record for record in pending.base_records if not isinstance(record, MatchRecord)
    )
    session.flush()
    session.add_all(
        record for record in pending.base_records if isinstance(record, MatchRecord)
    )
    session.flush()
    session.add_all(pending.identity_records)
    session.flush()


def _mark_fixture_registration_origin(
    pending: _PendingRegistration,
    ingestion_id: str,
) -> None:
    fixture_identity_types = (
        MatchRecord,
        ProviderTeamAliasRecord,
        ProviderCompetitionMappingRecord,
        CanonicalMatchIdentityRecord,
        ProviderMatchMappingRecord,
    )
    for record in (*pending.base_records, *pending.identity_records):
        if isinstance(record, fixture_identity_types):
            record.fixture_ingestion_id = ingestion_id


def _persist_fixture_registration(
    session: Session,
    pending: _PendingRegistration,
    ingestion: FixtureIngestionCaptureRecord,
) -> None:
    session.add_all(
        record for record in pending.base_records if not isinstance(record, MatchRecord)
    )
    session.flush()
    session.add(ingestion)
    session.flush()
    session.add_all(
        record for record in pending.base_records if isinstance(record, MatchRecord)
    )
    session.flush()
    session.add_all(pending.identity_records)
    session.flush()


def _capture_available_at(capture: FixtureIngestionCapture) -> datetime:
    available_at = capture.request_audit.available_at_utc
    if available_at is None:
        raise ValueError("fixture ingestion requires provider availability time")
    return available_at


def _fixture_ingestion_fields(
    capture: FixtureIngestionCapture,
    provider_id: str,
) -> dict[str, object]:
    audit = capture.request_audit
    request = capture.request
    return {
        "ingestion_id": capture.ingestion_id,
        "provider_id": provider_id,
        "kickoff_from_utc": request.kickoff_from_utc,
        "kickoff_to_utc": request.kickoff_to_utc,
        "provider_competition_id": request.provider_competition_id,
        "provider_season_id": request.provider_season_id,
        "season": request.season,
        "competition_type": request.competition_type,
        "language": request.language,
        "team_type": request.team_type.value,
        "endpoint": audit.endpoint,
        "request_parameters_json": json.dumps(
            audit.request_parameters,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "requested_at_utc": audit.requested_at_utc,
        "received_at_utc": audit.received_at_utc,
        "available_at_utc": _capture_available_at(capture),
        "http_status": audit.http_status,
        "provider_request_id": audit.provider_request_id,
        "duration_ms": audit.duration_ms,
        "outcome": audit.outcome.value,
        "failure_code": (
            audit.failure_code.value if audit.failure_code is not None else None
        ),
        "raw_artifact_id": capture.raw_artifact_id,
        "raw_payload_sha256": capture.raw_payload_sha256,
        "observation_count": len(capture.observations),
    }


def _new_ingested_at_utc(
    clock: Callable[[], datetime],
    capture: FixtureIngestionCapture,
) -> datetime:
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("clock must return a datetime")
    ingested_at = normalize_utc(value)
    if ingested_at < capture.request_audit.received_at_utc:
        raise ValueError("fixture ingestion time cannot precede response receipt")
    return ingested_at


def _find_fixture_ingestion(
    session: Session,
    capture: FixtureIngestionCapture,
) -> FixtureIngestionCaptureRecord | None:
    by_id = session.get(FixtureIngestionCaptureRecord, capture.ingestion_id)
    by_artifact = session.scalar(
        select(FixtureIngestionCaptureRecord).where(
            FixtureIngestionCaptureRecord.raw_artifact_id == capture.raw_artifact_id
        )
    )
    if by_id is not None and by_artifact is not None and by_id is not by_artifact:
        raise ValueError("fixture ingestion has conflicting stored identities")
    return by_id if by_id is not None else by_artifact


def _verify_fixture_observations(
    session: Session,
    capture: FixtureIngestionCapture,
) -> None:
    stored = tuple(
        session.scalars(
            select(FixtureObservationRecord).where(
                FixtureObservationRecord.ingestion_id == capture.ingestion_id
            )
        )
    )
    expected_ids = {item.observation_id for item in capture.observations}
    stored_by_id = {item.observation_id: item for item in stored}
    if len(stored) != len(capture.observations) or set(stored_by_id) != expected_ids:
        raise ValueError(
            "fixture ingestion observation set is incomplete or conflicting"
        )
    for observation in capture.observations:
        _assert_record_fields(
            stored_by_id[observation.observation_id],
            {
                "observation_id": observation.observation_id,
                "ingestion_id": capture.ingestion_id,
                "provider_mapping_id": observation.provider_mapping_id,
                "internal_match_id": observation.internal_match_id,
                "kickoff_at_utc": observation.kickoff_at_utc,
                "status": observation.status.value,
                "available_at_utc": observation.available_at_utc,
                "payload_sha256": observation.payload_sha256,
            },
            f"fixture observation {observation.observation_id}",
        )


def _assert_observations_not_backdated(
    session: Session,
    capture: FixtureIngestionCapture,
) -> None:
    provider_id = stable_id("provider", capture.provider_code)
    for observation in capture.observations:
        latest = session.scalar(
            select(FixtureObservationRecord)
            .join(
                FixtureIngestionCaptureRecord,
                FixtureIngestionCaptureRecord.ingestion_id
                == FixtureObservationRecord.ingestion_id,
            )
            .where(
                FixtureIngestionCaptureRecord.provider_id == provider_id,
                FixtureObservationRecord.internal_match_id
                == observation.internal_match_id,
            )
            .order_by(
                FixtureObservationRecord.available_at_utc.desc(),
                FixtureObservationRecord.observation_id.desc(),
            )
            .limit(1)
        )
        if (
            latest is not None
            and observation.available_at_utc < latest.available_at_utc
        ):
            raise ValueError(
                "fixture observation availability cannot predate its stored history: "
                f"{observation.internal_match_id}"
            )


def _preflight_registration(
    session: Session,
    registration: MatchIdentityRegistration,
    *,
    fixture_available_at: datetime | None = None,
    required_provider_codes: tuple[str, ...] = (),
) -> _PendingRegistration:
    base_records: list[object] = []
    identity_records: list[object] = []
    provider_codes = (
        {item.alias.provider_code for item in registration.team_aliases}
        | {item.mapping.provider_code for item in registration.competition_mappings}
        | {item.provider_code for item in registration.explicit_mappings}
        | set(required_provider_codes)
    )

    for provider_code in sorted(provider_codes):
        provider_id = stable_id("provider", provider_code)
        expected = {
            "provider_id": provider_id,
            "code": provider_code,
            "name": provider_code.replace("_", " ").title(),
            "provider_kind": _provider_kind(provider_code),
        }
        pending = _preflight_immutable_record(
            session,
            ProviderRecord,
            provider_id,
            expected,
            f"provider {provider_code}",
            select(ProviderRecord).where(ProviderRecord.code == provider_code),
        )
        if pending is not None:
            base_records.append(pending)

    for competition in registration.competitions:
        expected = {
            "competition_id": competition.competition_id,
            "canonical_key": competition.canonical_key,
            "name": competition.name,
            "country_code": competition.country_code,
        }
        pending = _preflight_immutable_record(
            session,
            CompetitionRecord,
            competition.competition_id,
            expected,
            f"competition {competition.competition_id}",
            select(CompetitionRecord).where(
                CompetitionRecord.canonical_key == competition.canonical_key
            ),
            ignored_fields=("name",) if fixture_available_at is not None else (),
        )
        if pending is not None:
            base_records.append(pending)

    for team in registration.teams:
        expected = {
            "team_id": team.team_id,
            "canonical_key": team.canonical_key,
            "name": team.name,
            "team_type": team.team_type.value,
        }
        pending = _preflight_immutable_record(
            session,
            TeamRecord,
            team.team_id,
            expected,
            f"team {team.team_id}",
            select(TeamRecord).where(TeamRecord.canonical_key == team.canonical_key),
            ignored_fields=("name",) if fixture_available_at is not None else (),
        )
        if pending is not None:
            base_records.append(pending)

    for match in registration.matches:
        immutable_fields = {
            "internal_match_id": match.match_id,
            "competition_id": match.competition_id,
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
            "kickoff_at_utc": match.kickoff_at_utc,
            "status": match.status.value,
            "available_at_utc": match.available_at_utc,
        }
        existing = session.get(MatchRecord, match.match_id)
        if existing is not None:
            if fixture_available_at is None:
                _assert_record_fields(
                    existing,
                    immutable_fields,
                    f"match {match.match_id}",
                )
            else:
                _assert_record_fields(
                    existing,
                    {
                        field: value
                        for field, value in immutable_fields.items()
                        if field not in {"kickoff_at_utc", "status", "available_at_utc"}
                    },
                    f"match {match.match_id}",
                )
                _assert_not_backdated(
                    match.available_at_utc,
                    existing.available_at_utc,
                    f"match {match.match_id}",
                )
            if existing.created_at_utc > registration.created_at_utc:
                raise ValueError(
                    "identity registration creation cannot predate stored match: "
                    f"{match.match_id}"
                )
            continue
        base_records.append(
            MatchRecord(
                **immutable_fields,
                created_at_utc=registration.created_at_utc,
            )
        )

    for registered in registration.team_aliases:
        alias_id = _team_alias_id(registered)
        alias = registered.alias
        provider_id = stable_id("provider", alias.provider_code)
        expected = {
            "alias_id": alias_id,
            "internal_team_id": registered.internal_team_id,
            "provider_id": provider_id,
            "provider_team_id": alias.provider_team_id,
            "provider_team_name": alias.provider_team_name,
            "language": alias.language,
            "team_type": alias.team_type.value,
            "available_at_utc": registered.available_at_utc,
        }
        pending = _preflight_immutable_record(
            session,
            ProviderTeamAliasRecord,
            alias_id,
            expected,
            f"team alias {alias_id}",
            select(ProviderTeamAliasRecord).where(
                ProviderTeamAliasRecord.provider_id == provider_id,
                ProviderTeamAliasRecord.provider_team_id == alias.provider_team_id,
                ProviderTeamAliasRecord.provider_team_name == alias.provider_team_name,
                ProviderTeamAliasRecord.language == alias.language,
                ProviderTeamAliasRecord.team_type == alias.team_type.value,
                ProviderTeamAliasRecord.internal_team_id == registered.internal_team_id,
            ),
            allow_later_availability=fixture_available_at is not None,
        )
        if pending is not None:
            identity_records.append(pending)

    for registered in registration.competition_mappings:
        mapping_id = _competition_mapping_id(registered)
        mapping = registered.mapping
        provider_id = stable_id("provider", mapping.provider_code)
        expected = {
            "mapping_id": mapping_id,
            "internal_competition_id": mapping.internal_competition_id,
            "provider_id": provider_id,
            "provider_competition_id": mapping.provider_competition_id,
            "provider_competition_name": mapping.provider_competition_name,
            "language": mapping.language,
            "season": mapping.season,
            "competition_type": mapping.competition_type,
            "available_at_utc": registered.available_at_utc,
        }
        pending = _preflight_immutable_record(
            session,
            ProviderCompetitionMappingRecord,
            mapping_id,
            expected,
            f"competition mapping {mapping_id}",
            select(ProviderCompetitionMappingRecord).where(
                ProviderCompetitionMappingRecord.provider_id == provider_id,
                ProviderCompetitionMappingRecord.provider_competition_id
                == mapping.provider_competition_id,
                ProviderCompetitionMappingRecord.provider_competition_name
                == mapping.provider_competition_name,
                ProviderCompetitionMappingRecord.language == mapping.language,
                ProviderCompetitionMappingRecord.season == mapping.season,
                ProviderCompetitionMappingRecord.competition_type
                == mapping.competition_type,
                ProviderCompetitionMappingRecord.internal_competition_id
                == mapping.internal_competition_id,
            ),
            allow_later_availability=fixture_available_at is not None,
        )
        if pending is not None:
            identity_records.append(pending)

    for registered in registration.canonical_matches:
        identity = registered.identity
        expected = {
            "internal_match_id": identity.internal_match_id,
            "season": identity.season,
            "competition_type": identity.competition_type,
            "available_at_utc": registered.available_at_utc,
        }
        pending = _preflight_immutable_record(
            session,
            CanonicalMatchIdentityRecord,
            identity.internal_match_id,
            expected,
            f"canonical match identity {identity.internal_match_id}",
            allow_later_availability=fixture_available_at is not None,
        )
        if pending is not None:
            identity_records.append(pending)

    for mapping in registration.explicit_mappings:
        provider_id = stable_id("provider", mapping.provider_code)
        expected = {
            "mapping_id": mapping.mapping_id,
            "provider_id": provider_id,
            "external_namespace": mapping.external_namespace,
            "external_match_id": mapping.external_match_id,
            "internal_match_id": mapping.internal_match_id,
            "resolution_method": mapping.resolution_method,
            "confidence": mapping.confidence,
            "available_at_utc": mapping.available_at_utc,
            "supersedes_mapping_id": None,
        }
        pending = _preflight_immutable_record(
            session,
            ProviderMatchMappingRecord,
            mapping.mapping_id,
            expected,
            f"explicit match mapping {mapping.mapping_id}",
            select(ProviderMatchMappingRecord).where(
                ProviderMatchMappingRecord.provider_id == provider_id,
                ProviderMatchMappingRecord.external_namespace
                == mapping.external_namespace,
                ProviderMatchMappingRecord.external_match_id
                == mapping.external_match_id,
            ),
            allow_later_availability=fixture_available_at is not None,
        )
        if pending is not None:
            identity_records.append(pending)

    return _PendingRegistration(base_records, identity_records)


def _preflight_immutable_record(
    session: Session,
    record_type: type[object],
    primary_key: str,
    expected: dict[str, object],
    label: str,
    identity_statement: object | None = None,
    *,
    allow_later_availability: bool = False,
    ignored_fields: tuple[str, ...] = (),
) -> object | None:
    by_id = session.get(record_type, primary_key)
    by_identity = (
        session.scalar(identity_statement) if identity_statement is not None else None
    )
    if by_id is not None and by_identity is not None and by_id is not by_identity:
        raise ValueError(f"immutable {label} has conflicting stored identities")
    existing = by_id if by_id is not None else by_identity
    if existing is not None:
        excluded_fields = set(ignored_fields)
        if allow_later_availability:
            excluded_fields.add("available_at_utc")
        _assert_record_fields(
            existing,
            {
                field: value
                for field, value in expected.items()
                if field not in excluded_fields
            },
            label,
        )
        if allow_later_availability:
            candidate_available_at = expected["available_at_utc"]
            if not isinstance(candidate_available_at, datetime):
                raise TypeError("identity availability must be a datetime")
            _assert_not_backdated(
                candidate_available_at,
                getattr(existing, "available_at_utc"),
                label,
            )
        return None
    return record_type(**expected)


def _assert_not_backdated(
    candidate: datetime,
    stored: datetime,
    label: str,
) -> None:
    if candidate < stored:
        raise ValueError(
            f"fixture ingestion availability cannot predate stored {label}"
        )


def _assert_record_fields(
    record: object,
    expected: dict[str, object],
    label: str,
) -> None:
    mismatched = [
        field for field, value in expected.items() if getattr(record, field) != value
    ]
    if mismatched:
        raise ValueError(
            f"immutable {label} conflicts on fields: {', '.join(mismatched)}"
        )


def _team_alias_id(registered: RegisteredTeamAlias) -> str:
    alias = registered.alias
    return stable_id(
        "provider-team-alias",
        alias.provider_code,
        alias.provider_team_id,
        alias.provider_team_name,
        alias.language,
        alias.team_type.value,
        registered.internal_team_id,
    )


def _competition_mapping_id(registered: RegisteredCompetitionMapping) -> str:
    mapping = registered.mapping
    return stable_id(
        "provider-competition-mapping",
        mapping.provider_code,
        mapping.provider_competition_id,
        mapping.provider_competition_name,
        mapping.language,
        mapping.season,
        mapping.competition_type,
        mapping.internal_competition_id,
    )


def _provider_kind(provider_code: str) -> str:
    if "SPORTTERY" in provider_code:
        return "SPORTTERY"
    if "ODDS" in provider_code or "MARKET" in provider_code:
        return "MARKET_ODDS"
    return "FIXTURE"
