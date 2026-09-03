from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Self

from pydantic import ValidationError, model_validator

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.ports.data_providers import (
    MarketOddsBatch,
    MarketOddsProvider,
    MarketOddsReconciliationIssue,
    MarketOddsReconciliationIssueReason,
    SnapshotQuery,
)
from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    HistoricalArchive,
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalArchiveRecord,
    HistoricalDataMode,
    MarketOddsArchiveRecord,
    MarketOddsIssueArchivePayload,
    MarketOddsIssueArchiveRecord,
    ProviderMappingArchiveRecord,
    archive_payload_sha256,
    canonical_json,
    canonical_payload_sha256,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.identity import (
    AmbiguousMatchMappingError,
    MatchIdentityResolutionError,
    MatchIdentityResolver,
    ProviderMatchIdentity,
)
from football_system.domain.market import MarketKey, MarketType, ThreeWayMarketOdds
from football_system.domain.match import (
    MarketOddsSnapshot,
    OddsQuote,
    ProviderMatchMapping,
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

THE_ODDS_API_PROVIDER_CODE = "THE_ODDS_API"
_THREE_WAY_MARKET = MarketKey(market_type=MarketType.THREE_WAY)
_EVENT_NAMESPACE = "event"


@dataclass(frozen=True, slots=True)
class _NormalizedEvents:
    snapshots: tuple[MarketOddsSnapshot, ...]
    mappings: tuple[ProviderMatchMapping, ...]
    issues: tuple[MarketOddsReconciliationIssue, ...]


class TheOddsApiHistoricalImportEnvelope(DomainModel):
    provider_code: Literal["THE_ODDS_API"] = THE_ODDS_API_PROVIDER_CODE
    data_mode: HistoricalDataMode = HistoricalDataMode.SOURCE_TIME_RESEARCH
    runtime_provenance: RuntimeProvenance
    source_available_at_utc: UtcDateTime
    identity_cutoff_at_utc: UtcDateTime
    imported_at_utc: UtcDateTime
    raw_artifact_id: Identifier
    snapshots: tuple[MarketOddsSnapshot, ...]
    mappings: tuple[ProviderMatchMapping, ...]
    issues: tuple[MarketOddsReconciliationIssue, ...] = ()

    @property
    def source_cutoff_at_utc(self) -> datetime:
        return self.source_available_at_utc

    @model_validator(mode="after")
    def validate_import(self) -> Self:
        if self.data_mode is not HistoricalDataMode.SOURCE_TIME_RESEARCH:
            raise ValueError(
                "The Odds API historical imports require SOURCE_TIME_RESEARCH"
            )
        if (
            self.runtime_provenance.environment is not RuntimeEnvironment.RESEARCH
            or self.runtime_provenance.data_mode
            is not HistoricalDataMode.SOURCE_TIME_RESEARCH
            or self.runtime_provenance.provider_code != self.provider_code
            or self.runtime_provenance.is_mock
        ):
            raise ValueError("historical import runtime provenance must be research")
        if self.source_available_at_utc >= self.imported_at_utc:
            raise ValueError(
                "historical source availability must precede local import receipt"
            )
        if self.identity_cutoff_at_utc != self.source_available_at_utc:
            raise ValueError(
                "historical reconciliation must use the exact source-time identity cutoff"
            )
        if any(
            snapshot.provider_code != self.provider_code
            or snapshot.available_at_utc != self.source_available_at_utc
            or snapshot.ingested_at_utc != self.source_available_at_utc
            or snapshot.captured_at_utc > self.source_available_at_utc
            for snapshot in self.snapshots
        ):
            raise ValueError("historical import snapshot provenance is inconsistent")
        if any(
            mapping.provider_code != self.provider_code
            or mapping.available_at_utc != self.source_available_at_utc
            for mapping in self.mappings
        ):
            raise ValueError("historical import mapping provenance is inconsistent")
        if any(
            not _snapshot_has_exact_event_mapping(snapshot, self.mappings)
            for snapshot in self.snapshots
        ):
            raise ValueError("historical import snapshot has no exact event mapping")
        if any(issue.provider_code != self.provider_code for issue in self.issues):
            raise ValueError("historical import issue has the wrong provider")
        _require_unique_ids(self.snapshots, "snapshot_id", "snapshot")
        _require_unique_ids(self.mappings, "mapping_id", "mapping")
        _require_unique_ids(self.issues, "issue_id", "issue")
        return self


class TheOddsApiMarketOddsProvider(MarketOddsProvider):
    """Fetch current soccer h2h odds without retrospective endpoint access."""

    provider_code = THE_ODDS_API_PROVIDER_CODE
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.LIVE,
        provider_code=THE_ODDS_API_PROVIDER_CODE,
        provenance="The Odds API current odds endpoint",
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )

    def __init__(
        self,
        client: ProviderHttpClient,
        raw_archive: RawDataArchive,
        identity_resolver: MatchIdentityResolver,
        api_key: str,
        *,
        sport_key: str,
        season: str,
        competition_type: str,
        regions: str = "uk",
    ) -> None:
        self._client = client
        self._raw_archive = raw_archive
        self._identity_resolver = identity_resolver
        self._api_key = _credential(api_key)
        self._sport_key = _soccer_sport_key(sport_key)
        self._season = _required_text(season, "season")
        self._competition_type = _required_text(
            competition_type,
            "competition type",
        )
        self._regions = _required_text(regions, "regions")
        self._last_raw_artifact: ArchivedRawArtifact | None = None

    @property
    def last_raw_artifact(self) -> ArchivedRawArtifact | None:
        return self._last_raw_artifact

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        result = self._client.get(
            f"/v4/sports/{self._sport_key}/odds",
            query_parameters={
                "apiKey": self._api_key,
                "regions": self._regions,
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
        )
        artifact = archive_successful_response(
            self.provider_code,
            result,
            self._raw_archive,
        )
        self._last_raw_artifact = artifact
        payload = decode_json_payload(self.provider_code, result.payload or b"")
        events = _current_events(payload)
        requested = set(query.match_ids)
        normalized = _normalize_events(
            events,
            identity_resolver=self._identity_resolver,
            sport_key=self._sport_key,
            season=self._season,
            competition_type=self._competition_type,
            available_at_utc=result.audit.received_at_utc,
            ingested_at_utc=result.audit.received_at_utc,
            target_scope=requested,
        )
        visible = tuple(
            snapshot
            for snapshot in normalized.snapshots
            if snapshot.match_id in requested
            and _snapshot_is_visible(snapshot, query.as_of_at_utc)
        )
        visible_match_ids = {snapshot.match_id for snapshot in visible}
        response_is_visible = result.audit.received_at_utc <= query.as_of_at_utc
        issues = _ordered_issues(
            (
                *(normalized.issues if response_is_visible else ()),
                *(
                    _requested_missing_issue(match_id)
                    for match_id in sorted(requested - visible_match_ids)
                ),
            )
        )
        return MarketOddsBatch(
            snapshots=visible,
            mappings=tuple(
                mapping
                for mapping in normalized.mappings
                if mapping.internal_match_id in visible_match_ids
                and mapping.available_at_utc <= query.as_of_at_utc
            ),
            issues=issues,
        )


class TheOddsApiHistoricalMarketOddsImporter:
    """Download retrospective snapshots for explicit source-time research import."""

    provider_code = THE_ODDS_API_PROVIDER_CODE
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.RESEARCH,
        provider_code=THE_ODDS_API_PROVIDER_CODE,
        provenance="The Odds API retrospective historical odds download",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )

    def __init__(
        self,
        client: ProviderHttpClient,
        raw_archive: RawDataArchive,
        identity_resolver_factory: Callable[[datetime], MatchIdentityResolver],
        api_key: str,
        *,
        sport_key: str,
        season: str,
        competition_type: str,
        historical_at_utc: datetime,
        regions: str = "uk",
    ) -> None:
        self._client = client
        self._raw_archive = raw_archive
        if not callable(identity_resolver_factory):
            raise TypeError("identity_resolver_factory must be callable")
        self._identity_resolver_factory = identity_resolver_factory
        self._api_key = _credential(api_key)
        self._sport_key = _soccer_sport_key(sport_key)
        self._season = _required_text(season, "season")
        self._competition_type = _required_text(
            competition_type,
            "competition type",
        )
        self._historical_at_utc = _aware_utc(
            historical_at_utc,
            "historical_at_utc",
        )
        self._regions = _required_text(regions, "regions")
        self._last_raw_artifact: ArchivedRawArtifact | None = None

    @property
    def last_raw_artifact(self) -> ArchivedRawArtifact | None:
        return self._last_raw_artifact

    async def import_historical_market_odds(
        self,
    ) -> TheOddsApiHistoricalImportEnvelope:
        result = self._client.get(
            f"/v4/historical/sports/{self._sport_key}/odds",
            query_parameters={
                "apiKey": self._api_key,
                "regions": self._regions,
                "markets": "h2h",
                "oddsFormat": "decimal",
                "date": self._historical_at_utc.isoformat().replace("+00:00", "Z"),
            },
        )
        artifact = archive_successful_response(
            self.provider_code,
            result,
            self._raw_archive,
        )
        self._last_raw_artifact = artifact
        payload = decode_json_payload(self.provider_code, result.payload or b"")
        events, source_available_at_utc = _historical_events_and_availability(
            payload,
            received_at_utc=result.audit.received_at_utc,
        )
        identity_resolver = self._identity_resolver_factory(source_available_at_utc)
        if not isinstance(identity_resolver, MatchIdentityResolver):
            raise TypeError(
                "identity_resolver_factory must return MatchIdentityResolver"
            )
        normalized = _normalize_events(
            events,
            identity_resolver=identity_resolver,
            sport_key=self._sport_key,
            season=self._season,
            competition_type=self._competition_type,
            available_at_utc=source_available_at_utc,
            ingested_at_utc=source_available_at_utc,
            target_scope=None,
        )
        return TheOddsApiHistoricalImportEnvelope(
            runtime_provenance=self.runtime_provenance,
            source_available_at_utc=source_available_at_utc,
            identity_cutoff_at_utc=source_available_at_utc,
            imported_at_utc=result.audit.received_at_utc,
            raw_artifact_id=artifact.artifact_id,
            snapshots=normalized.snapshots,
            mappings=normalized.mappings,
            issues=normalized.issues,
        )


def write_the_odds_api_historical_archives(
    envelope: TheOddsApiHistoricalImportEnvelope,
    output_directory: str | Path,
    *,
    source_reference: str | None = None,
    source_description: str = (
        "The Odds API historical odds imported for source-time research"
    ),
    license_note: str = "Archive operator must verify source license terms",
    created_at_utc: datetime | None = None,
) -> tuple[Path, Path, Path]:
    """Write odds, reconciliation issue, and mapping research archives."""

    if not isinstance(envelope, TheOddsApiHistoricalImportEnvelope):
        raise TypeError("envelope must be a TheOddsApiHistoricalImportEnvelope")
    created_at = _aware_utc(
        created_at_utc or envelope.imported_at_utc,
        "created_at_utc",
    )
    if created_at < envelope.imported_at_utc:
        raise ValueError("archive creation cannot precede the local import receipt")
    directory = Path(output_directory)
    if directory.exists() and not directory.is_dir():
        raise NotADirectoryError(f"archive output is not a directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    reference = source_reference or f"raw-archive:{envelope.raw_artifact_id}"

    market_records: tuple[HistoricalArchiveRecord, ...] = tuple(
        MarketOddsArchiveRecord(
            retrospective=True,
            imported_at_utc=envelope.imported_at_utc,
            payload=snapshot,
        )
        for snapshot in envelope.snapshots
    )
    issue_records: tuple[HistoricalArchiveRecord, ...] = tuple(
        MarketOddsIssueArchiveRecord(
            retrospective=True,
            imported_at_utc=envelope.imported_at_utc,
            payload=MarketOddsIssueArchivePayload(
                provider_code=envelope.provider_code,
                available_at_utc=envelope.source_available_at_utc,
                issue=issue,
            ),
        )
        for issue in envelope.issues
    )
    all_mapping_records: tuple[HistoricalArchiveRecord, ...] = tuple(
        ProviderMappingArchiveRecord(
            retrospective=True,
            imported_at_utc=envelope.imported_at_utc,
            payload=mapping,
        )
        for mapping in envelope.mappings
    )
    market_document = _historical_archive_document(
        envelope,
        HistoricalArchiveDatasetKind.MARKET_ODDS,
        market_records,
        created_at_utc=created_at,
        source_reference=reference,
        source_description=source_description,
        license_note=license_note,
    )
    issue_document = _historical_archive_document(
        envelope,
        HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES,
        issue_records,
        created_at_utc=created_at,
        source_reference=reference,
        source_description=source_description,
        license_note=license_note,
    )
    unfiltered_mapping_document = _historical_archive_document(
        envelope,
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        all_mapping_records,
        created_at_utc=created_at,
        source_reference=reference,
        source_description=source_description,
        license_note=license_note,
    )
    market_path = directory / f"{market_document.manifest.archive_id}.json"
    issue_path = directory / f"{issue_document.manifest.archive_id}.json"
    mapping_path = directory / f"{unfiltered_mapping_document.manifest.archive_id}.json"
    mapping_records = _new_mapping_records(
        envelope,
        directory,
        exclude_path=mapping_path,
    )
    mapping_document = _historical_archive_document(
        envelope,
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        mapping_records,
        created_at_utc=created_at,
        source_reference=reference,
        source_description=source_description,
        license_note=license_note,
    )
    for path, document in (
        (mapping_path, mapping_document),
        (issue_path, issue_document),
        (market_path, market_document),
    ):
        _preflight_new_canonical_json(path, document)
    _write_new_canonical_json(mapping_path, mapping_document)
    _write_new_canonical_json(issue_path, issue_document)
    _write_new_canonical_json(market_path, market_document)
    return market_path, mapping_path, issue_path


def _normalize_events(
    events: tuple[Mapping[str, object], ...],
    *,
    identity_resolver: MatchIdentityResolver,
    sport_key: str,
    season: str,
    competition_type: str,
    available_at_utc: datetime,
    ingested_at_utc: datetime,
    target_scope: set[str] | None,
) -> _NormalizedEvents:
    issues: list[MarketOddsReconciliationIssue] = []
    normalized_by_target: defaultdict[
        str,
        list[tuple[ProviderMatchMapping, tuple[MarketOddsSnapshot, ...]]],
    ] = defaultdict(list)
    resolved_event_ids: defaultdict[str, list[str]] = defaultdict(list)
    blocked_targets: set[str] = set()

    for event in events:
        try:
            event_id = _identifier(event.get("id"), "event id")
        except ProviderPayloadError:
            issues.append(
                _reconciliation_issue(
                    MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
                    code="EVENT_ID_INVALID",
                    detail="provider event has no usable external event identity",
                    identity_fingerprint=canonical_payload_sha256(event),
                )
            )
            continue

        try:
            identity = _provider_identity(
                event,
                event_id,
                sport_key=sport_key,
                season=season,
                competition_type=competition_type,
            )
        except (ProviderPayloadError, ValidationError):
            issues.append(
                _reconciliation_issue(
                    MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
                    external_match_id=event_id,
                    code="EVENT_IDENTITY_INVALID",
                    detail="provider event identity fields could not be normalized",
                )
            )
            continue

        try:
            resolution = identity_resolver.resolve(identity)
        except MatchIdentityResolutionError as error:
            candidates = tuple(sorted(set(error.candidates)))
            intersections = (
                set(candidates)
                if target_scope is None
                else set(candidates) & target_scope
            )
            blocked_targets.update(intersections)
            reason = (
                MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS
                if isinstance(error, AmbiguousMatchMappingError)
                else MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED
            )
            detail = (
                "provider event identity matched multiple canonical candidates"
                if reason is MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS
                else "provider event identity could not be resolved"
            )
            requested_targets = tuple(sorted(intersections))
            if not requested_targets:
                requested_targets = (None,)
            issues.extend(
                _reconciliation_issue(
                    reason,
                    external_match_id=event_id,
                    requested_match_id=requested_match_id,
                    candidates=candidates,
                    code=error.code,
                    detail=detail,
                )
                for requested_match_id in requested_targets
            )
            continue

        match_id = resolution.internal_match_id
        resolved_event_ids[match_id].append(event_id)
        requested_match_id = (
            match_id if target_scope is not None and match_id in target_scope else None
        )
        try:
            mapping = ProviderMatchMapping(
                mapping_id=stable_id(
                    "provider-mapping",
                    THE_ODDS_API_PROVIDER_CODE,
                    _EVENT_NAMESPACE,
                    event_id,
                ),
                provider_code=THE_ODDS_API_PROVIDER_CODE,
                external_namespace=_EVENT_NAMESPACE,
                external_match_id=event_id,
                internal_match_id=match_id,
                resolution_method=resolution.resolution_method,
                confidence=resolution.confidence,
                available_at_utc=available_at_utc,
            )
            event_snapshots = _bookmaker_snapshots(
                event,
                event_id=event_id,
                match_id=match_id,
                available_at_utc=available_at_utc,
                ingested_at_utc=ingested_at_utc,
            )
        except (ProviderPayloadError, ValidationError):
            issues.append(
                _reconciliation_issue(
                    MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
                    external_match_id=event_id,
                    requested_match_id=requested_match_id,
                    code="EVENT_MARKET_DATA_INVALID",
                    detail="provider event market data could not be normalized",
                )
            )
            continue
        if not event_snapshots:
            issues.append(
                _reconciliation_issue(
                    MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
                    external_match_id=event_id,
                    requested_match_id=requested_match_id,
                    code="NO_COMPLETE_H2H_SNAPSHOT",
                    detail="provider event has no complete three-way odds snapshot",
                )
            )
            continue
        normalized_by_target[match_id].append((mapping, event_snapshots))

    for match_id in sorted(resolved_event_ids):
        event_ids = tuple(sorted(resolved_event_ids[match_id]))
        if len(event_ids) < 2:
            continue
        blocked_targets.add(match_id)
        requested_match_id = (
            match_id if target_scope is not None and match_id in target_scope else None
        )
        issues.extend(
            _reconciliation_issue(
                MarketOddsReconciliationIssueReason.DUPLICATE_RESOLVED_TARGET,
                external_match_id=event_id,
                requested_match_id=requested_match_id,
                candidates=(match_id,),
                code="DUPLICATE_RESOLVED_EVENT_TARGET",
                detail="multiple provider events resolved to one canonical match",
            )
            for event_id in event_ids
        )

    snapshots: list[MarketOddsSnapshot] = []
    mappings: list[ProviderMatchMapping] = []
    for match_id in sorted(normalized_by_target):
        if match_id in blocked_targets:
            continue
        for mapping, event_snapshots in normalized_by_target[match_id]:
            mappings.append(mapping)
            snapshots.extend(event_snapshots)
    return _NormalizedEvents(
        snapshots=tuple(
            sorted(
                snapshots,
                key=lambda item: (
                    item.match_id,
                    item.bookmaker_code,
                    item.source_snapshot_key,
                ),
            )
        ),
        mappings=tuple(sorted(mappings, key=lambda item: item.mapping_id)),
        issues=_ordered_issues(issues),
    )


def _provider_identity(
    event: Mapping[str, object],
    event_id: str,
    *,
    sport_key: str,
    season: str,
    competition_type: str,
) -> ProviderMatchIdentity:
    event_sport_key = _required_text(event.get("sport_key"), "event sport_key")
    if event_sport_key != sport_key or not event_sport_key.startswith("soccer_"):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "event is not from the configured soccer sport",
        )
    home_team = _required_text(event.get("home_team"), "event home_team")
    away_team = _required_text(event.get("away_team"), "event away_team")
    if home_team == away_team:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "event home and away teams must differ",
        )
    return ProviderMatchIdentity(
        provider_code=THE_ODDS_API_PROVIDER_CODE,
        provider_match_id=event_id,
        external_namespace=_EVENT_NAMESPACE,
        provider_competition_id=event_sport_key,
        provider_competition_name=_required_text(
            event.get("sport_title"),
            "event sport_title",
        ),
        competition_language="en",
        season=season,
        competition_type=competition_type,
        # The Odds API does not expose team IDs, so exact raw names are IDs too.
        home_team_id=home_team,
        home_team_name=home_team,
        home_team_language="en",
        away_team_id=away_team,
        away_team_name=away_team,
        away_team_language="en",
        kickoff_at_utc=parse_utc_timestamp(
            THE_ODDS_API_PROVIDER_CODE,
            event.get("commence_time"),
            field="event commence_time",
        ),
    )


def _current_events(payload: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(payload, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "current odds payload must be an event array",
        )
    return _events(payload)


def _historical_events_and_availability(
    payload: object,
    *,
    received_at_utc: datetime,
) -> tuple[tuple[Mapping[str, object], ...], datetime]:
    if not isinstance(payload, Mapping):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "historical payload must be a response object",
        )
    timestamp = parse_utc_timestamp(
        THE_ODDS_API_PROVIDER_CODE,
        payload.get("timestamp"),
        field="historical timestamp",
    )
    if timestamp >= received_at_utc:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "historical timestamp must precede local receipt time",
        )
    return _events(payload.get("data")), timestamp


def _events(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "event data must be an array",
        )
    result: list[Mapping[str, object]] = []
    for event in value:
        if not isinstance(event, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "event entries must be objects",
            )
        result.append(event)
    events = tuple(result)
    _validate_unique_event_ids(events)
    return events


def _validate_unique_event_ids(
    events: tuple[Mapping[str, object], ...],
) -> None:
    event_ids: set[str] = set()
    for event in events:
        try:
            event_id = _identifier(event.get("id"), "event id")
        except ProviderPayloadError:
            continue
        if event_id in event_ids:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "response contains duplicate event IDs",
            )
        event_ids.add(event_id)


def _bookmaker_snapshots(
    event: Mapping[str, object],
    *,
    event_id: str,
    match_id: str,
    available_at_utc: datetime,
    ingested_at_utc: datetime,
) -> tuple[MarketOddsSnapshot, ...]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "event bookmakers must be an array",
        )
    home_team = _required_text(event.get("home_team"), "event home_team")
    away_team = _required_text(event.get("away_team"), "event away_team")
    result: list[MarketOddsSnapshot] = []
    bookmaker_codes: set[str] = set()
    for raw_bookmaker in bookmakers:
        if not isinstance(raw_bookmaker, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker entries must be objects",
            )
        bookmaker_code = _required_text(raw_bookmaker.get("key"), "bookmaker key")
        if bookmaker_code in bookmaker_codes:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "event contains duplicate bookmaker keys",
            )
        bookmaker_codes.add(bookmaker_code)
        odds = _complete_h2h_odds(raw_bookmaker, home_team, away_team)
        if odds is None:
            continue
        captured_at_utc = parse_utc_timestamp(
            THE_ODDS_API_PROVIDER_CODE,
            raw_bookmaker.get("last_update"),
            field="bookmaker last_update",
        )
        if not captured_at_utc <= available_at_utc <= ingested_at_utc:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker timestamps are not causally ordered",
            )
        payload_hash = canonical_payload_sha256(odds)
        source_snapshot_key = _the_odds_api_source_snapshot_key(
            event_id,
            bookmaker_code,
            captured_at_utc,
            available_at_utc,
            payload_hash,
        )
        result.append(
            MarketOddsSnapshot(
                snapshot_id=stable_id(
                    "market-odds",
                    THE_ODDS_API_PROVIDER_CODE,
                    source_snapshot_key,
                ),
                match_id=match_id,
                provider_code=THE_ODDS_API_PROVIDER_CODE,
                bookmaker_code=bookmaker_code,
                market=_THREE_WAY_MARKET,
                quotes=tuple(
                    OddsQuote(selection=selection, odds=value)
                    for selection, value in odds.items()
                ),
                captured_at_utc=captured_at_utc,
                available_at_utc=available_at_utc,
                ingested_at_utc=ingested_at_utc,
                source_snapshot_key=source_snapshot_key,
                payload_hash=payload_hash,
            )
        )
    return tuple(result)


def _complete_h2h_odds(
    bookmaker: Mapping[str, object],
    home_team: str,
    away_team: str,
) -> ThreeWayMarketOdds | None:
    markets = bookmaker.get("markets")
    if not isinstance(markets, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "bookmaker markets must be an array",
        )
    h2h_markets: list[Mapping[str, object]] = []
    for market in markets:
        if not isinstance(market, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker markets must contain objects",
            )
        market_key = market.get("key")
        if not isinstance(market_key, str):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker market key must be a string",
            )
        if market_key == "h2h":
            h2h_markets.append(market)
    if not h2h_markets:
        return None
    if len(h2h_markets) != 1:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "bookmaker contains multiple h2h markets",
        )
    outcomes = h2h_markets[0].get("outcomes")
    if not isinstance(outcomes, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h outcomes must be an array",
        )
    by_name: dict[str, Decimal] = {}
    expected_names = {home_team, "Draw", away_team}
    for raw_outcome in outcomes:
        if not isinstance(raw_outcome, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "h2h outcomes must be objects",
            )
        name = raw_outcome.get("name")
        if not isinstance(name, str) or name not in expected_names:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "h2h outcome does not exactly identify home, draw, or away",
            )
        if name in by_name:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "h2h outcomes contain duplicates",
            )
        by_name[name] = _decimal_odds(raw_outcome.get("price"))
    if set(by_name) != expected_names:
        return None
    return ThreeWayMarketOdds(
        home_win=by_name[home_team],
        draw=by_name["Draw"],
        away_win=by_name[away_team],
    )


def _reconciliation_issue(
    reason: MarketOddsReconciliationIssueReason,
    *,
    code: str,
    detail: str,
    external_match_id: str | None = None,
    requested_match_id: str | None = None,
    candidates: tuple[str, ...] = (),
    identity_fingerprint: str | None = None,
) -> MarketOddsReconciliationIssue:
    ordered_candidates = tuple(sorted(set(candidates)))
    return MarketOddsReconciliationIssue(
        issue_id=stable_id(
            "market-odds-reconciliation-issue",
            canonical_json(
                {
                    "candidates": ordered_candidates,
                    "code": code,
                    "external_match_id": external_match_id,
                    "identity_fingerprint": identity_fingerprint,
                    "provider_code": THE_ODDS_API_PROVIDER_CODE,
                    "reason": reason,
                    "requested_match_id": requested_match_id,
                }
            ),
        ),
        reason=reason,
        provider_code=THE_ODDS_API_PROVIDER_CODE,
        external_namespace=(
            _EVENT_NAMESPACE if external_match_id is not None else None
        ),
        external_match_id=external_match_id,
        requested_match_id=requested_match_id,
        candidates=ordered_candidates,
        code=code,
        detail=detail,
    )


def _requested_missing_issue(match_id: str) -> MarketOddsReconciliationIssue:
    return _reconciliation_issue(
        MarketOddsReconciliationIssueReason.REQUESTED_MATCH_MISSING,
        requested_match_id=match_id,
        code="REQUESTED_MARKET_ODDS_MISSING",
        detail="requested match has no complete visible reconciled odds snapshot",
    )


def _ordered_issues(
    issues: Iterable[MarketOddsReconciliationIssue],
) -> tuple[MarketOddsReconciliationIssue, ...]:
    by_id = {issue.issue_id: issue for issue in issues}
    return tuple(by_id[issue_id] for issue_id in sorted(by_id))


def _historical_archive_document(
    envelope: TheOddsApiHistoricalImportEnvelope,
    dataset_kind: HistoricalArchiveDatasetKind,
    records: tuple[HistoricalArchiveRecord, ...],
    *,
    created_at_utc: datetime,
    source_reference: str,
    source_description: str,
    license_note: str,
) -> HistoricalArchive:
    manifest = HistoricalArchiveManifest(
        archive_schema_version=HISTORICAL_ARCHIVE_SCHEMA_VERSION,
        archive_id=stable_id(
            "historical-archive",
            envelope.provider_code,
            dataset_kind.value,
            envelope.raw_artifact_id,
            created_at_utc.isoformat(),
        ),
        provider_code=envelope.provider_code,
        dataset_kind=dataset_kind,
        created_at_utc=created_at_utc,
        source_reference=source_reference,
        source_description=source_description,
        license_note=license_note,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        payload_sha256=archive_payload_sha256(records),
        record_count=len(records),
    )
    return HistoricalArchive.model_validate(
        {
            "manifest": manifest.model_dump(mode="json"),
            "records": tuple(record.model_dump(mode="json") for record in records),
        }
    )


def _new_mapping_records(
    envelope: TheOddsApiHistoricalImportEnvelope,
    directory: Path,
    *,
    exclude_path: Path,
) -> tuple[HistoricalArchiveRecord, ...]:
    from football_system.infrastructure.providers.historical_archive import (
        load_historical_archive,
    )

    existing_by_id: dict[str, ProviderMatchMapping] = {}
    existing_by_external: dict[tuple[str, str, str], ProviderMatchMapping] = {}
    excluded = exclude_path.resolve()
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        if not path.is_file() or path.resolve() == excluded:
            continue
        archive = load_historical_archive(path)
        manifest = archive.manifest
        if (
            manifest.data_mode is not HistoricalDataMode.SOURCE_TIME_RESEARCH
            or manifest.dataset_kind
            is not HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS
            or manifest.provider_code != envelope.provider_code
        ):
            continue
        for record in archive.records:
            if not isinstance(record, ProviderMappingArchiveRecord):
                raise TypeError("provider mapping archive has an invalid record type")
            mapping = record.payload
            external_key = _mapping_external_key(mapping)
            previous_id = existing_by_id.setdefault(mapping.mapping_id, mapping)
            previous_external = existing_by_external.setdefault(external_key, mapping)
            if previous_id != mapping or previous_external != mapping:
                raise ValueError(
                    "existing historical provider mappings contain immutable conflicts"
                )

    records: list[HistoricalArchiveRecord] = []
    pending_by_id: dict[str, ProviderMatchMapping] = {}
    pending_by_external: dict[tuple[str, str, str], ProviderMatchMapping] = {}
    for mapping in envelope.mappings:
        external_key = _mapping_external_key(mapping)
        pending_id = pending_by_id.setdefault(mapping.mapping_id, mapping)
        pending_external = pending_by_external.setdefault(external_key, mapping)
        if pending_id != mapping or pending_external != mapping:
            raise ValueError("historical import has conflicting provider mappings")

        by_id = existing_by_id.get(mapping.mapping_id)
        by_external = existing_by_external.get(external_key)
        if by_id is not None and by_external is not None and by_id != by_external:
            raise ValueError("historical provider mapping identities conflict")
        existing = by_id or by_external
        if existing is not None:
            _validate_reused_mapping(existing, mapping)
            continue
        records.append(
            ProviderMappingArchiveRecord(
                retrospective=True,
                imported_at_utc=envelope.imported_at_utc,
                payload=mapping,
            )
        )
    return tuple(records)


def _mapping_external_key(mapping: ProviderMatchMapping) -> tuple[str, str, str]:
    return (
        mapping.provider_code,
        mapping.external_namespace,
        mapping.external_match_id,
    )


def _validate_reused_mapping(
    existing: ProviderMatchMapping,
    incoming: ProviderMatchMapping,
) -> None:
    immutable_fields = (
        "mapping_id",
        "provider_code",
        "external_namespace",
        "external_match_id",
        "internal_match_id",
        "resolution_method",
        "confidence",
    )
    if any(
        getattr(existing, field) != getattr(incoming, field)
        for field in immutable_fields
    ):
        raise ValueError("historical provider mapping conflicts with stored identity")
    if incoming.available_at_utc < existing.available_at_utc:
        raise ValueError(
            "historical snapshots must be imported in source-time order so immutable "
            "mapping availability is not backdated"
        )


def _snapshot_has_exact_event_mapping(
    snapshot: MarketOddsSnapshot,
    mappings: tuple[ProviderMatchMapping, ...],
) -> bool:
    return any(
        mapping.provider_code == snapshot.provider_code
        and mapping.external_namespace == _EVENT_NAMESPACE
        and mapping.internal_match_id == snapshot.match_id
        and _the_odds_api_source_snapshot_key(
            mapping.external_match_id,
            snapshot.bookmaker_code,
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.payload_hash,
        )
        == snapshot.source_snapshot_key
        for mapping in mappings
    )


def _the_odds_api_source_snapshot_key(
    event_id: str,
    bookmaker_code: str,
    captured_at_utc: datetime,
    available_at_utc: datetime,
    payload_hash: str,
) -> str:
    return stable_id(
        "the-odds-api-source",
        event_id,
        bookmaker_code,
        captured_at_utc.isoformat(),
        available_at_utc.isoformat(),
        payload_hash,
    )


def _preflight_new_canonical_json(path: Path, document: HistoricalArchive) -> None:
    if not path.exists():
        return
    content = _canonical_document_bytes(document)
    if path.is_file() and not path.is_symlink() and path.read_bytes() == content:
        return
    raise FileExistsError(f"refusing to overwrite archive document: {path}")


def _write_new_canonical_json(path: Path, document: HistoricalArchive) -> None:
    content = _canonical_document_bytes(document)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.is_file() and not path.is_symlink() and path.read_bytes() == content:
            return
        raise FileExistsError(
            f"refusing to overwrite archive document: {path}"
        ) from None


def _canonical_document_bytes(document: HistoricalArchive) -> bytes:
    return (canonical_json(document) + "\n").encode("utf-8")


def _require_unique_ids(items: tuple[object, ...], field: str, label: str) -> None:
    identities = [getattr(item, field) for item in items]
    if len(identities) != len(set(identities)):
        raise ValueError(f"historical import {label} IDs must be unique")


def _soccer_sport_key(value: object) -> str:
    sport_key = _required_text(value, "sport key")
    if not sport_key.startswith("soccer_"):
        raise ValueError("The Odds API sport_key must identify soccer")
    return sport_key


def _credential(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("\r", "\n", "\0"))
    ):
        raise ValueError("The Odds API credential is invalid")
    return value.strip()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            f"{field} must be a nonempty string",
        )
    return value.strip()


def _identifier(value: object, field: str) -> str:
    if isinstance(value, str) and value.strip():
        identifier = value.strip()
    elif type(value) is int:
        identifier = str(value)
    else:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            f"{field} must be a nonempty string or integer",
        )
    if len(identifier) > 160:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            f"{field} is too long",
        )
    return identifier


def _decimal_odds(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h decimal odds are invalid",
        )
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h decimal odds are invalid",
        ) from None
    if not decimal.is_finite() or decimal <= 1:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h decimal odds must be finite and above one",
        )
    return decimal


def _aware_utc(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _snapshot_is_visible(snapshot: MarketOddsSnapshot, cutoff: datetime) -> bool:
    return all(
        timestamp <= cutoff
        for timestamp in (
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
    )
