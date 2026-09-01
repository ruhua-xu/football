from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar, TypeVar, cast

from pydantic import ValidationError

from football_system.application.ports.data_providers import (
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
    HistoricalDataProvider,
    ManualQuantBatch,
    ManualQuantProvider,
    MarketOddsBatch,
    MarketOddsProvider,
    MatchResultBatch,
    MatchResultQuery,
    SnapshotQuery,
    SportteryBatch,
    SportteryProvider,
)
from football_system.domain.archive import (
    FixtureArchivePayload,
    FixtureArchiveRecord,
    HistoricalArchive,
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalArchiveRecord,
    HistoricalDataMode,
    ManualQuantArchiveRecord,
    MarketOddsArchiveRecord,
    MatchResultArchiveRecord,
    ProviderMappingArchiveRecord,
    SportteryBonusArchiveRecord,
)
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    ProviderMatchMapping,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.prediction import ManualQuantInput
from football_system.domain.settlement import MatchResult

TypedArchiveRecord = (
    FixtureArchiveRecord
    | MarketOddsArchiveRecord
    | SportteryBonusArchiveRecord
    | ManualQuantArchiveRecord
    | MatchResultArchiveRecord
    | ProviderMappingArchiveRecord
)

_RECORD_TYPES: dict[HistoricalArchiveDatasetKind, type[HistoricalArchiveRecord]] = {
    HistoricalArchiveDatasetKind.FIXTURES: FixtureArchiveRecord,
    HistoricalArchiveDatasetKind.MARKET_ODDS: MarketOddsArchiveRecord,
    HistoricalArchiveDatasetKind.SPORTTERY_BONUS: SportteryBonusArchiveRecord,
    HistoricalArchiveDatasetKind.MANUAL_QUANT: ManualQuantArchiveRecord,
    HistoricalArchiveDatasetKind.MATCH_RESULTS: MatchResultArchiveRecord,
    HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS: ProviderMappingArchiveRecord,
}


class HistoricalArchiveError(ValueError):
    pass


class ArchiveValidationError(HistoricalArchiveError):
    pass


class MissingArchiveInputError(HistoricalArchiveError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedHistoricalArchive:
    path: Path
    document: HistoricalArchive
    records: tuple[TypedArchiveRecord, ...]

    @property
    def manifest(self) -> HistoricalArchiveManifest:
        return self.document.manifest


@dataclass(frozen=True, slots=True)
class _ArchiveEntry:
    archive: LoadedHistoricalArchive
    record: TypedArchiveRecord

    @property
    def provider_code(self) -> str:
        return self.archive.manifest.provider_code

    @property
    def dataset_kind(self) -> HistoricalArchiveDatasetKind:
        return self.archive.manifest.dataset_kind


def load_historical_archive(path: str | Path) -> LoadedHistoricalArchive:
    archive_path = Path(path)
    if not archive_path.is_file():
        raise MissingArchiveInputError(
            f"historical archive file does not exist: {archive_path}"
        )
    try:
        text = archive_path.read_bytes().decode("utf-8")
        raw = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_non_finite_json,
        )
        document = HistoricalArchive.model_validate(raw)
        record_type = _RECORD_TYPES[document.manifest.dataset_kind]
        records = tuple(
            cast(
                TypedArchiveRecord,
                record_type.model_validate(record.model_dump(mode="python")),
            )
            for record in document.records
        )
        records = tuple(_normalize_record(record) for record in records)
        loaded = LoadedHistoricalArchive(
            path=archive_path,
            document=document,
            records=records,
        )
        entries = tuple(_ArchiveEntry(loaded, record) for record in records)
        _validate_record_manifests(entries)
        _validate_business_keys(entries)
        _validate_result_supersession(entries, require_complete=False)
        return loaded
    except HistoricalArchiveError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise ArchiveValidationError(
            f"invalid historical archive {archive_path}: {exc}"
        ) from exc


class LocalArchiveStore:
    def __init__(
        self,
        directory: str | Path,
        *,
        data_mode: HistoricalDataMode | str | None = None,
    ) -> None:
        archive_directory = Path(directory)
        if not archive_directory.is_dir():
            raise MissingArchiveInputError(
                f"historical archive directory does not exist: {archive_directory}"
            )
        paths = tuple(
            sorted(
                (path for path in archive_directory.glob("*.json") if path.is_file()),
                key=lambda path: path.name,
            )
        )
        if not paths:
            raise MissingArchiveInputError(
                f"historical archive directory contains no JSON archives: "
                f"{archive_directory}"
            )
        archives = tuple(load_historical_archive(path) for path in paths)
        _validate_archive_ids(archives)
        modes = {archive.manifest.data_mode for archive in archives}
        selected_mode = _select_data_mode(modes, data_mode)
        selected = tuple(
            archive
            for archive in archives
            if archive.manifest.data_mode is selected_mode
        )
        selected_entries = _entries(selected)
        _validate_business_keys(selected_entries)
        _validate_result_supersession(selected_entries, require_complete=True)
        _validate_mapping_coverage(selected_entries)
        self._directory = archive_directory
        self._data_mode = selected_mode
        self._archives = selected
        self._entries_by_key: dict[
            tuple[HistoricalArchiveDatasetKind, str], tuple[_ArchiveEntry, ...]
        ] = {}
        for kind in HistoricalArchiveDatasetKind:
            for provider_code in {
                archive.manifest.provider_code for archive in selected
            }:
                matching = tuple(
                    entry
                    for entry in selected_entries
                    if entry.dataset_kind is kind
                    and entry.provider_code == provider_code
                )
                if matching:
                    self._entries_by_key[(kind, provider_code)] = matching

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        data_mode: HistoricalDataMode | str | None = None,
    ) -> LocalArchiveStore:
        return cls(directory, data_mode=data_mode)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def data_mode(self) -> HistoricalDataMode:
        return self._data_mode

    @property
    def archives(self) -> tuple[LoadedHistoricalArchive, ...]:
        return self._archives

    @property
    def manifests(self) -> tuple[HistoricalArchiveManifest, ...]:
        return tuple(archive.manifest for archive in self.archives)

    def providers_for(
        self, dataset_kind: HistoricalArchiveDatasetKind
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    archive.manifest.provider_code
                    for archive in self._archives
                    if archive.manifest.dataset_kind is dataset_kind
                }
            )
        )

    def resolve_provider(
        self,
        dataset_kind: HistoricalArchiveDatasetKind,
        provider_code: str | None,
    ) -> str:
        providers = self.providers_for(dataset_kind)
        if provider_code is None:
            if len(providers) != 1:
                available = ", ".join(providers) if providers else "none"
                raise MissingArchiveInputError(
                    f"provider_code is required for {dataset_kind.value}; "
                    f"available providers: {available}"
                )
            return providers[0]
        if provider_code not in providers:
            raise MissingArchiveInputError(
                f"missing {dataset_kind.value} archive for provider "
                f"{provider_code} in {self._data_mode.value} mode"
            )
        return provider_code

    def require_archive(
        self,
        dataset_kind: HistoricalArchiveDatasetKind,
        provider_code: str,
    ) -> None:
        if not any(
            manifest.dataset_kind is dataset_kind
            and manifest.provider_code == provider_code
            for manifest in self.manifests
        ):
            raise MissingArchiveInputError(
                f"missing {dataset_kind.value} archive for provider "
                f"{provider_code} in {self._data_mode.value} mode"
            )

    def _records(
        self,
        dataset_kind: HistoricalArchiveDatasetKind,
        provider_code: str,
    ) -> tuple[_ArchiveEntry, ...]:
        return self._entries_by_key.get((dataset_kind, provider_code), ())


class _HistoricalArchiveProvider:
    dataset_kind: ClassVar[HistoricalArchiveDatasetKind]

    def __init__(
        self,
        archive_source: str | Path | LocalArchiveStore,
        provider_code: str | None = None,
        *,
        data_mode: HistoricalDataMode | str | None = None,
    ) -> None:
        self._store = _coerce_store(archive_source, data_mode)
        self.provider_code = self._store.resolve_provider(
            self.dataset_kind, provider_code
        )
        self._store.require_archive(
            HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
            self.provider_code,
        )

    @property
    def data_mode(self) -> HistoricalDataMode:
        return self._store.data_mode

    @property
    def retrospective(self) -> bool:
        return self.data_mode.is_retrospective

    @property
    def report_data_mode(self) -> str:
        return self.data_mode.report_label


class HistoricalArchiveFixtureProvider(_HistoricalArchiveProvider, FixtureProvider):
    dataset_kind = HistoricalArchiveDatasetKind.FIXTURES

    async def fetch_fixtures(self, query: FixtureQuery) -> FixtureBatch:
        latest: dict[str, FixtureArchivePayload] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            record = cast(FixtureArchiveRecord, entry.record)
            fixture = record.payload
            match = fixture.match
            if match.available_at_utc > query.as_of_at_utc:
                continue
            current = latest.get(match.match_id)
            if (
                current is None
                or match.available_at_utc > current.match.available_at_utc
            ):
                latest[match.match_id] = fixture

        selected = tuple(
            fixture
            for fixture in sorted(latest.values(), key=lambda item: item.match.match_id)
            if query.kickoff_from_utc
            <= fixture.match.kickoff_at_utc
            <= query.kickoff_to_utc
            and _has_visible_mapping(
                self._store,
                self.provider_code,
                fixture.match.match_id,
                query.as_of_at_utc,
            )
        )
        competitions = _unique_models(
            (fixture.competition for fixture in selected),
            lambda competition: competition.competition_id,
        )
        teams = _unique_models(
            (
                team
                for fixture in selected
                for team in (fixture.home_team, fixture.away_team)
            ),
            lambda team: team.team_id,
        )
        match_ids = {fixture.match.match_id for fixture in selected}
        return FixtureBatch(
            competitions=cast(tuple[Competition, ...], competitions),
            teams=cast(tuple[Team, ...], teams),
            matches=tuple(fixture.match for fixture in selected),
            mappings=_visible_mappings(
                self._store,
                self.provider_code,
                match_ids,
                query.as_of_at_utc,
            ),
        )


class HistoricalArchiveMarketOddsProvider(
    _HistoricalArchiveProvider, MarketOddsProvider
):
    dataset_kind = HistoricalArchiveDatasetKind.MARKET_ODDS

    def __init__(
        self,
        archive_source: str | Path | LocalArchiveStore,
        provider_code: str | None = None,
        *,
        bookmaker_code: str | None = None,
        require_complete: bool = True,
        data_mode: HistoricalDataMode | str | None = None,
    ) -> None:
        super().__init__(
            archive_source,
            provider_code,
            data_mode=data_mode,
        )
        if bookmaker_code is not None:
            bookmaker_code = bookmaker_code.strip()
            if not bookmaker_code:
                raise ValueError("bookmaker_code must be nonempty when configured")
        self.bookmaker_code = bookmaker_code
        self.require_complete = require_complete

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        requested = set(query.match_ids)
        latest: dict[tuple[str, str, str], MarketOddsSnapshot] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            snapshot = cast(MarketOddsArchiveRecord, entry.record).payload
            if (
                snapshot.match_id not in requested
                or (
                    self.bookmaker_code is not None
                    and snapshot.bookmaker_code != self.bookmaker_code
                )
                or not _snapshot_visible(snapshot, query.as_of_at_utc, self.data_mode)
            ):
                continue
            stream = (
                snapshot.match_id,
                snapshot.bookmaker_code,
                snapshot.market.canonical,
            )
            current = latest.get(stream)
            if current is None or _snapshot_version(snapshot) > _snapshot_version(
                current
            ):
                latest[stream] = snapshot
        snapshots = tuple(
            snapshot
            for snapshot in sorted(
                latest.values(),
                key=lambda item: (
                    item.match_id,
                    item.bookmaker_code,
                    item.market.canonical,
                ),
            )
            if _has_visible_mapping(
                self._store,
                self.provider_code,
                snapshot.match_id,
                query.as_of_at_utc,
            )
        )
        if self.bookmaker_code is not None and self.require_complete:
            missing_match_ids = tuple(
                sorted(requested - {snapshot.match_id for snapshot in snapshots})
            )
            if missing_match_ids:
                raise MissingArchiveInputError(
                    f"configured bookmaker stream {self.bookmaker_code} has no legal "
                    f"market odds snapshot at cutoff "
                    f"{query.as_of_at_utc.isoformat()} for requested matches: "
                    f"{', '.join(missing_match_ids)}"
                )
        return MarketOddsBatch(
            snapshots=snapshots,
            mappings=_visible_mappings(
                self._store,
                self.provider_code,
                {snapshot.match_id for snapshot in snapshots},
                query.as_of_at_utc,
            ),
        )


class HistoricalArchiveSportteryProvider(_HistoricalArchiveProvider, SportteryProvider):
    dataset_kind = HistoricalArchiveDatasetKind.SPORTTERY_BONUS

    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch:
        requested = set(query.match_ids)
        latest: dict[tuple[str, str, str], SportteryBonusSnapshot] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            snapshot = cast(SportteryBonusArchiveRecord, entry.record).payload
            if snapshot.match_id not in requested or not _snapshot_visible(
                snapshot, query.as_of_at_utc, self.data_mode
            ):
                continue
            stream = (
                snapshot.match_id,
                snapshot.sporttery_match_no,
                snapshot.market.canonical,
            )
            current = latest.get(stream)
            if current is None or _snapshot_version(snapshot) > _snapshot_version(
                current
            ):
                latest[stream] = snapshot
        snapshots = tuple(
            snapshot
            for snapshot in sorted(
                latest.values(),
                key=lambda item: (
                    item.match_id,
                    item.sporttery_match_no,
                    item.market.canonical,
                ),
            )
            if _has_visible_mapping(
                self._store,
                self.provider_code,
                snapshot.match_id,
                query.as_of_at_utc,
                external_match_id=snapshot.sporttery_match_no,
            )
        )
        return SportteryBatch(
            snapshots=snapshots,
            mappings=_visible_mappings(
                self._store,
                self.provider_code,
                {snapshot.match_id for snapshot in snapshots},
                query.as_of_at_utc,
                external_match_ids={
                    snapshot.sporttery_match_no for snapshot in snapshots
                },
            ),
        )


class HistoricalArchiveQuantProvider(_HistoricalArchiveProvider, ManualQuantProvider):
    dataset_kind = HistoricalArchiveDatasetKind.MANUAL_QUANT

    async def fetch_manual_quant(self, query: SnapshotQuery) -> ManualQuantBatch:
        requested = set(query.match_ids)
        latest: dict[tuple[str, str], ManualQuantInput] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            manual_input = cast(ManualQuantArchiveRecord, entry.record).payload
            if (
                manual_input.match_id not in requested
                or manual_input.available_at_utc > query.as_of_at_utc
            ):
                continue
            stream = (manual_input.match_id, manual_input.market.canonical)
            current = latest.get(stream)
            if (
                current is None
                or manual_input.available_at_utc > current.available_at_utc
            ):
                latest[stream] = manual_input
        return ManualQuantBatch(
            inputs=tuple(
                manual_input
                for manual_input in sorted(
                    latest.values(),
                    key=lambda item: (item.match_id, item.market.canonical),
                )
                if _has_visible_mapping(
                    self._store,
                    self.provider_code,
                    manual_input.match_id,
                    query.as_of_at_utc,
                )
            )
        )


class LocalArchiveHistoricalDataProvider(
    _HistoricalArchiveProvider, HistoricalDataProvider
):
    dataset_kind = HistoricalArchiveDatasetKind.MATCH_RESULTS

    async def fetch_match_results(self, query: MatchResultQuery) -> MatchResultBatch:
        requested = set(query.match_ids)
        latest: dict[str, MatchResult] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            result = cast(MatchResultArchiveRecord, entry.record).payload
            if result.match_id not in requested or not _result_visible(
                result, query.as_of_at_utc, self.data_mode
            ):
                continue
            current = latest.get(result.match_id)
            if current is None or _result_version(result) > _result_version(current):
                latest[result.match_id] = result
        results = tuple(
            result
            for result in sorted(latest.values(), key=lambda item: item.match_id)
            if _has_visible_mapping(
                self._store,
                self.provider_code,
                result.match_id,
                query.as_of_at_utc,
            )
        )
        return MatchResultBatch(
            as_of_at_utc=query.as_of_at_utc,
            results=results,
            mappings=_visible_mappings(
                self._store,
                self.provider_code,
                {result.match_id for result in results},
                query.as_of_at_utc,
            ),
        )


def _coerce_store(
    source: str | Path | LocalArchiveStore,
    data_mode: HistoricalDataMode | str | None,
) -> LocalArchiveStore:
    if isinstance(source, LocalArchiveStore):
        if (
            data_mode is not None
            and HistoricalDataMode(data_mode) is not source.data_mode
        ):
            raise MissingArchiveInputError(
                "requested data_mode does not match the loaded archive store"
            )
        return source
    return LocalArchiveStore(source, data_mode=data_mode)


def _select_data_mode(
    modes: set[HistoricalDataMode],
    requested: HistoricalDataMode | str | None,
) -> HistoricalDataMode:
    if requested is not None:
        try:
            selected = HistoricalDataMode(requested)
        except ValueError as exc:
            raise MissingArchiveInputError(
                f"unsupported historical archive data_mode: {requested}"
            ) from exc
        if selected not in modes:
            raise MissingArchiveInputError(
                f"archive directory contains no {selected.value} files"
            )
        return selected
    if len(modes) != 1:
        available = ", ".join(sorted(mode.value for mode in modes))
        raise MissingArchiveInputError(
            "archive directory contains multiple data modes; select data_mode "
            f"explicitly ({available})"
        )
    return next(iter(modes))


def _entries(
    archives: Iterable[LoadedHistoricalArchive],
) -> tuple[_ArchiveEntry, ...]:
    return tuple(
        _ArchiveEntry(archive, record)
        for archive in archives
        for record in archive.records
    )


def _normalize_record(record: TypedArchiveRecord) -> TypedArchiveRecord:
    if isinstance(record, MarketOddsArchiveRecord):
        payload = record.payload.model_copy(
            update={
                "quotes": tuple(
                    sorted(
                        record.payload.quotes, key=lambda quote: quote.selection.value
                    )
                )
            }
        )
        return record.model_copy(update={"payload": payload})
    if isinstance(record, SportteryBonusArchiveRecord):
        payload = record.payload.model_copy(
            update={
                "quotes": tuple(
                    sorted(
                        record.payload.quotes, key=lambda quote: quote.selection.value
                    )
                )
            }
        )
        return record.model_copy(update={"payload": payload})
    return record


def _validate_archive_ids(archives: tuple[LoadedHistoricalArchive, ...]) -> None:
    seen: dict[str, Path] = {}
    for archive in archives:
        archive_id = archive.manifest.archive_id
        if archive_id in seen:
            raise ArchiveValidationError(
                f"duplicate archive_id {archive_id}: {seen[archive_id]} and "
                f"{archive.path}"
            )
        seen[archive_id] = archive.path


def _validate_record_manifests(entries: tuple[_ArchiveEntry, ...]) -> None:
    for entry in entries:
        manifest = entry.archive.manifest
        record = entry.record
        payload = record.payload
        payload_provider = getattr(payload, "provider_code", manifest.provider_code)
        if payload_provider != manifest.provider_code:
            raise ArchiveValidationError(
                f"record provider {payload_provider} does not match manifest provider "
                f"{manifest.provider_code} in {entry.archive.path}"
            )
        source_known_at = _source_known_at(record)
        if manifest.data_mode is HistoricalDataMode.LIVE_STRICT:
            if source_known_at > manifest.created_at_utc:
                raise ArchiveValidationError(
                    f"LIVE_STRICT record in {entry.archive.path} occurs after archive "
                    "creation"
                )
            continue

        imported_at = record.imported_at_utc
        if imported_at is None:
            raise ArchiveValidationError(
                f"research record in {entry.archive.path} has no import timestamp"
            )
        if source_known_at >= imported_at or imported_at > manifest.created_at_utc:
            raise ArchiveValidationError(
                f"research record in {entry.archive.path} is not retrospectively "
                "imported"
            )
        if (
            isinstance(
                payload, (MarketOddsSnapshot, SportteryBonusSnapshot, MatchResult)
            )
            and payload.ingested_at_utc != payload.available_at_utc
        ):
            raise ArchiveValidationError(
                "SOURCE_TIME_RESEARCH uses available_at_utc as its explicit "
                "source-time ingestion boundary; actual import time belongs in "
                "imported_at_utc"
            )


def _source_known_at(record: TypedArchiveRecord) -> datetime:
    payload = record.payload
    if isinstance(payload, FixtureArchivePayload):
        return payload.match.available_at_utc
    if isinstance(payload, (MarketOddsSnapshot, SportteryBonusSnapshot, MatchResult)):
        return payload.ingested_at_utc
    if isinstance(payload, (ManualQuantInput, ProviderMatchMapping)):
        return payload.available_at_utc
    raise TypeError(f"unsupported archive payload: {type(payload).__name__}")


def _validate_business_keys(entries: tuple[_ArchiveEntry, ...]) -> None:
    fixtures = _entries_of_kind(entries, HistoricalArchiveDatasetKind.FIXTURES)
    market = _entries_of_kind(entries, HistoricalArchiveDatasetKind.MARKET_ODDS)
    sporttery = _entries_of_kind(entries, HistoricalArchiveDatasetKind.SPORTTERY_BONUS)
    quant = _entries_of_kind(entries, HistoricalArchiveDatasetKind.MANUAL_QUANT)
    results = _entries_of_kind(entries, HistoricalArchiveDatasetKind.MATCH_RESULTS)
    mappings = _entries_of_kind(entries, HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS)

    _assert_unique(
        fixtures,
        lambda entry: (
            entry.provider_code,
            cast(FixtureArchiveRecord, entry.record).payload.match.match_id,
            cast(FixtureArchiveRecord, entry.record).payload.match.available_at_utc,
        ),
        "fixture version",
    )
    _assert_unique(
        market,
        lambda entry: cast(MarketOddsArchiveRecord, entry.record).payload.snapshot_id,
        "market snapshot ID",
    )
    _assert_unique(
        market,
        lambda entry: (
            entry.provider_code,
            cast(MarketOddsArchiveRecord, entry.record).payload.source_snapshot_key,
        ),
        "market source key",
    )
    _assert_unique(
        market,
        lambda entry: _market_version_key(
            cast(MarketOddsArchiveRecord, entry.record).payload
        ),
        "market odds version",
    )
    _assert_unique(
        sporttery,
        lambda entry: cast(
            SportteryBonusArchiveRecord, entry.record
        ).payload.snapshot_id,
        "Sporttery snapshot ID",
    )
    _assert_unique(
        sporttery,
        lambda entry: (
            entry.provider_code,
            cast(SportteryBonusArchiveRecord, entry.record).payload.source_snapshot_key,
        ),
        "Sporttery source key",
    )
    _assert_unique(
        sporttery,
        lambda entry: _sporttery_version_key(
            cast(SportteryBonusArchiveRecord, entry.record).payload
        ),
        "Sporttery version",
    )
    _assert_unique(
        quant,
        lambda entry: cast(ManualQuantArchiveRecord, entry.record).payload.input_id,
        "manual quant input ID",
    )
    _assert_unique(
        quant,
        lambda entry: (
            entry.provider_code,
            cast(ManualQuantArchiveRecord, entry.record).payload.match_id,
            cast(ManualQuantArchiveRecord, entry.record).payload.market.canonical,
            cast(ManualQuantArchiveRecord, entry.record).payload.available_at_utc,
        ),
        "manual quant version",
    )
    _assert_unique(
        results,
        lambda entry: cast(
            MatchResultArchiveRecord, entry.record
        ).payload.match_result_id,
        "match result ID",
    )
    _assert_unique(
        results,
        lambda entry: (
            entry.provider_code,
            cast(MatchResultArchiveRecord, entry.record).payload.source_result_key,
        ),
        "match result source key",
    )
    _assert_unique(
        results,
        lambda entry: _result_business_version_key(
            cast(MatchResultArchiveRecord, entry.record).payload
        ),
        "match result version",
    )
    _assert_unique(
        mappings,
        lambda entry: cast(
            ProviderMappingArchiveRecord, entry.record
        ).payload.mapping_id,
        "provider mapping ID",
    )
    _assert_unique(
        mappings,
        lambda entry: (
            entry.provider_code,
            cast(ProviderMappingArchiveRecord, entry.record).payload.external_namespace,
            cast(ProviderMappingArchiveRecord, entry.record).payload.external_match_id,
        ),
        "provider external match key",
    )
    _validate_fixture_identity(fixtures)


def _validate_fixture_identity(entries: tuple[_ArchiveEntry, ...]) -> None:
    fixture_records = tuple(
        cast(FixtureArchiveRecord, entry.record).payload for entry in entries
    )
    _assert_model_identity(
        (fixture.competition for fixture in fixture_records),
        lambda competition: competition.competition_id,
        "competition",
    )
    _assert_model_identity(
        (
            team
            for fixture in fixture_records
            for team in (fixture.home_team, fixture.away_team)
        ),
        lambda team: team.team_id,
        "team",
    )
    seen_matches: dict[str, tuple[str, str, str]] = {}
    for fixture in fixture_records:
        identity = (
            fixture.match.competition_id,
            fixture.match.home_team_id,
            fixture.match.away_team_id,
        )
        previous = seen_matches.setdefault(fixture.match.match_id, identity)
        if previous != identity:
            raise ArchiveValidationError(
                f"conflicting canonical fixture identity: {fixture.match.match_id}"
            )


def _validate_result_supersession(
    entries: tuple[_ArchiveEntry, ...],
    *,
    require_complete: bool,
) -> None:
    result_entries = _entries_of_kind(
        entries, HistoricalArchiveDatasetKind.MATCH_RESULTS
    )
    by_id = {
        cast(MatchResultArchiveRecord, entry.record).payload.match_result_id: entry
        for entry in result_entries
    }
    children: dict[str, str] = {}
    series: dict[tuple[str, str], list[MatchResult]] = defaultdict(list)
    for entry in result_entries:
        result = cast(MatchResultArchiveRecord, entry.record).payload
        series[(result.provider_code, result.match_id)].append(result)
        parent_id = result.supersedes_match_result_id
        if parent_id is None:
            continue
        parent_entry = by_id.get(parent_id)
        if parent_entry is None:
            if require_complete:
                raise ArchiveValidationError(
                    f"match result {result.match_result_id} supersedes missing result "
                    f"{parent_id}"
                )
            continue
        parent = cast(MatchResultArchiveRecord, parent_entry.record).payload
        if (
            parent.match_id != result.match_id
            or parent.provider_code != result.provider_code
        ):
            raise ArchiveValidationError(
                f"match result {result.match_result_id} supersedes a different "
                "match or provider"
            )
        if not (
            parent.available_at_utc <= result.available_at_utc
            and parent.ingested_at_utc < result.ingested_at_utc
        ):
            raise ArchiveValidationError(
                f"match result {result.match_result_id} must supersede an earlier "
                "version"
            )
        previous_child = children.setdefault(parent_id, result.match_result_id)
        if previous_child != result.match_result_id:
            raise ArchiveValidationError(
                f"match result correction chain forks at {parent_id}"
            )

    if not require_complete:
        return
    for key, versions in series.items():
        if len(versions) == 1:
            continue
        roots = [
            result for result in versions if result.supersedes_match_result_id is None
        ]
        if len(roots) != 1:
            raise ArchiveValidationError(
                f"match result versions for {key[0]}/{key[1]} require one "
                "supersession chain"
            )


def _validate_mapping_coverage(entries: tuple[_ArchiveEntry, ...]) -> None:
    mappings: dict[tuple[str, str], list[ProviderMatchMapping]] = defaultdict(list)
    for entry in _entries_of_kind(
        entries, HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS
    ):
        mapping = cast(ProviderMappingArchiveRecord, entry.record).payload
        mappings[(mapping.provider_code, mapping.internal_match_id)].append(mapping)

    for entry in entries:
        if entry.dataset_kind is HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS:
            continue
        match_id = _record_match_id(entry.record)
        candidates = mappings.get((entry.provider_code, match_id), [])
        if not candidates:
            raise ArchiveValidationError(
                f"{entry.dataset_kind.value} record for {match_id} has no "
                f"same-provider mapping for {entry.provider_code}"
            )
        if entry.dataset_kind is HistoricalArchiveDatasetKind.SPORTTERY_BONUS:
            sporttery_no = cast(
                SportteryBonusArchiveRecord, entry.record
            ).payload.sporttery_match_no
            if not any(
                mapping.external_match_id == sporttery_no for mapping in candidates
            ):
                raise ArchiveValidationError(
                    f"Sporttery record for {match_id} has no mapping for match "
                    f"number {sporttery_no}"
                )


def _record_match_id(record: TypedArchiveRecord) -> str:
    payload = record.payload
    if isinstance(payload, FixtureArchivePayload):
        return payload.match.match_id
    if isinstance(
        payload,
        (
            MarketOddsSnapshot,
            SportteryBonusSnapshot,
            ManualQuantInput,
            MatchResult,
        ),
    ):
        return payload.match_id
    if isinstance(payload, ProviderMatchMapping):
        return payload.internal_match_id
    raise TypeError(f"unsupported archive payload: {type(payload).__name__}")


def _entries_of_kind(
    entries: tuple[_ArchiveEntry, ...],
    dataset_kind: HistoricalArchiveDatasetKind,
) -> tuple[_ArchiveEntry, ...]:
    return tuple(entry for entry in entries if entry.dataset_kind is dataset_kind)


def _market_version_key(snapshot: MarketOddsSnapshot) -> tuple[object, ...]:
    return (
        snapshot.provider_code,
        snapshot.match_id,
        snapshot.bookmaker_code,
        snapshot.market.canonical,
        snapshot.captured_at_utc,
        snapshot.available_at_utc,
    )


def _sporttery_version_key(snapshot: SportteryBonusSnapshot) -> tuple[object, ...]:
    return (
        snapshot.provider_code,
        snapshot.match_id,
        snapshot.sporttery_match_no,
        snapshot.market.canonical,
        snapshot.captured_at_utc,
        snapshot.available_at_utc,
    )


def _result_business_version_key(result: MatchResult) -> tuple[object, ...]:
    return (
        result.provider_code,
        result.match_id,
        result.available_at_utc,
        result.ingested_at_utc,
    )


T = TypeVar("T")


def _assert_unique(
    values: Iterable[T],
    key: Callable[[T], Hashable],
    label: str,
) -> None:
    seen: set[Hashable] = set()
    for value in values:
        identity = key(value)
        if identity in seen:
            raise ArchiveValidationError(f"duplicate {label}: {identity}")
        seen.add(identity)


def _assert_model_identity(
    values: Iterable[T],
    key: Callable[[T], str],
    label: str,
) -> None:
    seen: dict[str, T] = {}
    for value in values:
        identity = key(value)
        previous = seen.setdefault(identity, value)
        if previous != value:
            raise ArchiveValidationError(f"conflicting {label} definition: {identity}")


def _unique_models(values: Iterable[T], key: Callable[[T], str]) -> tuple[T, ...]:
    by_id = {key(value): value for value in values}
    return tuple(by_id[identity] for identity in sorted(by_id))


def _snapshot_visible(
    snapshot: MarketOddsSnapshot | SportteryBonusSnapshot,
    cutoff: datetime,
    data_mode: HistoricalDataMode,
) -> bool:
    timestamps = (snapshot.captured_at_utc, snapshot.available_at_utc)
    if data_mode is HistoricalDataMode.LIVE_STRICT:
        timestamps = (*timestamps, snapshot.ingested_at_utc)
    return all(timestamp <= cutoff for timestamp in timestamps)


def _result_visible(
    result: MatchResult,
    cutoff: datetime,
    data_mode: HistoricalDataMode,
) -> bool:
    timestamps = (result.observed_at_utc, result.available_at_utc)
    if data_mode is HistoricalDataMode.LIVE_STRICT:
        timestamps = (*timestamps, result.ingested_at_utc)
    return all(timestamp <= cutoff for timestamp in timestamps)


def _snapshot_version(
    snapshot: MarketOddsSnapshot | SportteryBonusSnapshot,
) -> tuple[datetime, datetime, datetime, str]:
    return (
        snapshot.available_at_utc,
        snapshot.captured_at_utc,
        snapshot.ingested_at_utc,
        snapshot.snapshot_id,
    )


def _result_version(result: MatchResult) -> tuple[datetime, datetime, str]:
    return (
        result.available_at_utc,
        result.ingested_at_utc,
        result.match_result_id,
    )


def _mapping_payloads(
    store: LocalArchiveStore, provider_code: str
) -> tuple[ProviderMatchMapping, ...]:
    return tuple(
        cast(ProviderMappingArchiveRecord, entry.record).payload
        for entry in store._records(
            HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS, provider_code
        )
    )


def _has_visible_mapping(
    store: LocalArchiveStore,
    provider_code: str,
    match_id: str,
    cutoff: datetime,
    *,
    external_match_id: str | None = None,
) -> bool:
    return any(
        mapping.internal_match_id == match_id
        and mapping.available_at_utc <= cutoff
        and (
            external_match_id is None or mapping.external_match_id == external_match_id
        )
        for mapping in _mapping_payloads(store, provider_code)
    )


def _visible_mappings(
    store: LocalArchiveStore,
    provider_code: str,
    match_ids: set[str],
    cutoff: datetime,
    *,
    external_match_ids: set[str] | None = None,
) -> tuple[ProviderMatchMapping, ...]:
    if not match_ids:
        return ()
    return tuple(
        sorted(
            (
                mapping
                for mapping in _mapping_payloads(store, provider_code)
                if mapping.internal_match_id in match_ids
                and mapping.available_at_utc <= cutoff
                and (
                    external_match_ids is None
                    or mapping.external_match_id in external_match_ids
                )
            ),
            key=lambda mapping: mapping.mapping_id,
        )
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> object:
    raise ValueError(f"JSON numeric constant is not finite: {value}")


HistoricalArchiveManualQuantProvider = HistoricalArchiveQuantProvider
