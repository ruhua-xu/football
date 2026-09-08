from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar, TypeVar, cast

from pydantic import ValidationError
from sqlalchemy import select, text

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
    is_mock_provider_code,
)
from football_system.application.ports.data_providers import (
    ArchivedMatchResultBatch,
    ArchivedMatchResultSource,
    EloTrainingHistoryBatch,
    EloTrainingHistoryProvider,
    EloTrainingHistoryQuery,
    EloTrainingResultSource,
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
    HistoricalDataProvider,
    ManualQuantBatch,
    ManualQuantProvider,
    MarketOddsBatch,
    MarketOddsProvider,
    MarketOddsReconciliationIssue,
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
    MarketOddsIssueArchivePayload,
    MarketOddsIssueArchiveRecord,
    MatchResultArchiveRecord,
    ProviderMappingArchiveRecord,
    SportteryBonusArchiveRecord,
)
from football_system.domain.backtest import BacktestArchiveProvenance
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    ProviderMatchMapping,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.common import normalize_utc, stable_id
from football_system.domain.prediction import ManualQuantInput
from football_system.domain.quant_integrity import (
    TrainingAdmissionPinV1,
    project_admitted_training_fact,
    revalidate_integrity_model,
    select_admitted_training_facts,
)
from football_system.domain.settlement import MatchResult
from football_system.domain.training_admission import (
    TRAINING_FACT_REQUIRED_USES,
    NormalizedMatchResultRecordV1,
    TrainingFactBindingV1,
    TrainingProviderMatchMappingV1,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
    SqlAlchemyTrainingAdmissionRepository,
)
from football_system.domain.training_correction import (
    TrainingCorrectionContextV2,
    TrainingFactVersionV2,
)
from football_system.domain.versioned_training_history import (
    TrainingHistoryContextPinV2,
    project_versioned_training_fact,
    select_versioned_training_facts,
    validate_versioned_context,
)
from football_system.infrastructure.database.quant_integrity_repository import (
    correction_context_in_session,
    assert_complete_correction_pins,
)

TypedArchiveRecord = (
    FixtureArchiveRecord
    | MarketOddsArchiveRecord
    | MarketOddsIssueArchiveRecord
    | SportteryBonusArchiveRecord
    | ManualQuantArchiveRecord
    | MatchResultArchiveRecord
    | ProviderMappingArchiveRecord
)

_RECORD_TYPES: dict[HistoricalArchiveDatasetKind, type[HistoricalArchiveRecord]] = {
    HistoricalArchiveDatasetKind.FIXTURES: FixtureArchiveRecord,
    HistoricalArchiveDatasetKind.MARKET_ODDS: MarketOddsArchiveRecord,
    HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES: MarketOddsIssueArchiveRecord,
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


def _parse_typed_record(
    dataset_kind: HistoricalArchiveDatasetKind,
    record: HistoricalArchiveRecord,
) -> TypedArchiveRecord:
    record_type = _RECORD_TYPES[dataset_kind]
    return cast(
        TypedArchiveRecord,
        record_type.model_validate(record.model_dump(mode="python")),
    )


def load_historical_archive(
    path: str | Path, *, correction_context=None
) -> LoadedHistoricalArchive:
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
        records = tuple(
            _parse_typed_record(document.manifest.dataset_kind, record)
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
        _validate_business_keys(entries, correction_context=correction_context)
        _validate_result_supersession(
            entries, require_complete=False, correction_context=correction_context
        )
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
        correction_context: TrainingCorrectionContextV2 | None = None,
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
        if correction_context is not None:
            correction_context = validate_versioned_context(correction_context)
        archives = tuple(
            load_historical_archive(path, correction_context=correction_context)
            for path in paths
        )
        _validate_archive_ids(archives)
        modes = {archive.manifest.data_mode for archive in archives}
        selected_mode = _select_data_mode(modes, data_mode)
        if (
            correction_context is not None
            and selected_mode is not HistoricalDataMode.SOURCE_TIME_RESEARCH
        ):
            raise ArchiveValidationError(
                "correction context cannot authorize LIVE_STRICT archives"
            )
        selected = tuple(
            archive
            for archive in archives
            if archive.manifest.data_mode is selected_mode
        )
        selected_entries = _entries(selected)
        _validate_business_keys(selected_entries, correction_context=correction_context)
        _validate_result_supersession(
            selected_entries,
            require_complete=True,
            correction_context=correction_context,
        )
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

    @property
    def runtime_provenance(self) -> RuntimeProvenance:
        environment = (
            RuntimeEnvironment.RESEARCH
            if self.data_mode is HistoricalDataMode.SOURCE_TIME_RESEARCH
            else RuntimeEnvironment.LIVE
        )
        return RuntimeProvenance(
            environment=environment,
            provider_code=self.provider_code,
            provenance=f"historical archive {self.report_data_mode}",
            is_mock=is_mock_provider_code(self.provider_code),
            data_mode=self.data_mode,
        )


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
            if not isinstance(entry.record, MarketOddsArchiveRecord):
                continue
            snapshot = entry.record.payload
            if (
                snapshot.match_id not in requested
                or (
                    self.bookmaker_code is not None
                    and snapshot.bookmaker_code != self.bookmaker_code
                )
                or not _snapshot_visible(snapshot, query.as_of_at_utc)
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
        issues_by_id: dict[str, MarketOddsReconciliationIssue] = {}
        for entry in self._store._records(
            HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES,
            self.provider_code,
        ):
            if not isinstance(entry.record, MarketOddsIssueArchiveRecord):
                raise TypeError("market odds issue archive has an invalid record type")
            issue_payload = entry.record.payload
            if issue_payload.available_at_utc > query.as_of_at_utc:
                continue
            issue = issue_payload.issue
            if (
                issue.requested_match_id is not None
                and issue.requested_match_id not in requested
                and not requested.intersection(issue.candidates)
            ):
                continue
            previous = issues_by_id.get(issue.issue_id)
            if previous is not None and previous != issue:
                raise ArchiveValidationError(
                    f"conflicting market odds issue: {issue.issue_id}"
                )
            issues_by_id[issue.issue_id] = issue
        return MarketOddsBatch(
            snapshots=snapshots,
            mappings=_visible_mappings(
                self._store,
                self.provider_code,
                {snapshot.match_id for snapshot in snapshots},
                query.as_of_at_utc,
            ),
            issues=tuple(issues_by_id[key] for key in sorted(issues_by_id)),
        )


class HistoricalArchiveSportteryProvider(_HistoricalArchiveProvider, SportteryProvider):
    dataset_kind = HistoricalArchiveDatasetKind.SPORTTERY_BONUS

    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch:
        requested = set(query.match_ids)
        latest: dict[tuple[str, str, str], SportteryBonusSnapshot] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            snapshot = cast(SportteryBonusArchiveRecord, entry.record).payload
            if snapshot.match_id not in requested or not _snapshot_visible(
                snapshot, query.as_of_at_utc
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
            provider_code=self.provider_code,
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
            ),
        )


class LocalArchiveHistoricalDataProvider(
    _HistoricalArchiveProvider, HistoricalDataProvider
):
    dataset_kind = HistoricalArchiveDatasetKind.MATCH_RESULTS

    async def fetch_match_results(self, query: MatchResultQuery) -> MatchResultBatch:
        return (await self.fetch_archived_match_results(query)).to_match_result_batch()

    async def fetch_archived_match_results(
        self,
        query: MatchResultQuery,
    ) -> ArchivedMatchResultBatch:
        requested = set(query.match_ids)
        latest: dict[str, _ArchiveEntry] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            result = cast(MatchResultArchiveRecord, entry.record).payload
            if result.match_id not in requested or not _result_visible(
                result, query.as_of_at_utc
            ):
                continue
            current = latest.get(result.match_id)
            if current is None or _result_version(result) > _result_version(
                cast(MatchResultArchiveRecord, current.record).payload
            ):
                latest[result.match_id] = entry
        selected_entries = tuple(
            entry
            for entry in sorted(
                latest.values(),
                key=lambda item: cast(
                    MatchResultArchiveRecord, item.record
                ).payload.match_id,
            )
            if _has_visible_mapping(
                self._store,
                self.provider_code,
                cast(MatchResultArchiveRecord, entry.record).payload.match_id,
                query.as_of_at_utc,
            )
        )
        results = tuple(
            cast(MatchResultArchiveRecord, entry.record).payload
            for entry in selected_entries
        )
        return ArchivedMatchResultBatch(
            as_of_at_utc=query.as_of_at_utc,
            sources=tuple(
                ArchivedMatchResultSource(
                    result=result,
                    archive=BacktestArchiveProvenance.from_manifest(
                        entry.archive.manifest
                    ),
                )
                for entry, result in zip(selected_entries, results, strict=True)
            ),
            mappings=_visible_mappings(
                self._store,
                self.provider_code,
                {result.match_id for result in results},
                query.as_of_at_utc,
            ),
        )


@dataclass(frozen=True, slots=True)
class RepositoryArchiveMembershipSource:
    """Read per-fact season evidence from exact persisted admission pins.

    ``repository`` must be the byte-verifying SqlAlchemyTrainingAdmissionRepository,
    not a dictionary, a sealed fact tuple, or a duck-typed in-memory loader.
    ``admissions`` is a nonempty, ID-sorted tuple of unique TrainingAdmissionPinV1
    values. ``verified_at_utc`` is the actual local verification boundary, distinct
    from each historical query's source-time cutoff. Pins must already exist then
    and research/storage rights must be active then. This grants no live or
    production permission.

    Each load calls the repository's read-only ``load(admission_id)``: it rereads
    captured raw files, full record hashes, adapter/reviewer evidence, registered
    identities, normalized results, memberships and persisted child/parent roots.
    Pin equality is checked after that I/O, never inferred from a constructor seal.
    Unresolved corrections fail in repository verification. Without an explicit
    ``correction_context`` pin, registered correction streams still fail closed.
    With that opt-in, each read verifies every exact predecessor and correction,
    complete current heads, and active rights at the repository's actual clock.
    Whole-version source selection is separate from that current read permission.
    Evidence files and the database remain local; this reader performs no writes.
    """

    repository: SqlAlchemyTrainingAdmissionRepository
    admissions: tuple[TrainingAdmissionPinV1, ...]
    verified_at_utc: datetime
    correction_context: TrainingHistoryContextPinV2 | None = None

    data_mode: ClassVar[HistoricalDataMode] = HistoricalDataMode.SOURCE_TIME_RESEARCH

    def load_facts(
        self,
    ) -> tuple[TrainingFactBindingV1, ...] | tuple[TrainingFactVersionV2, ...]:
        """Reverify every pinned graph, including facts later excluded by a query."""
        if self.correction_context is not None:
            return self.load_context().versions
        if not isinstance(self.repository, SqlAlchemyTrainingAdmissionRepository):
            raise MissingArchiveInputError(
                "archive memberships require the byte-verifying admission repository"
            )
        at = normalize_utc(self.verified_at_utc)
        pins = tuple(revalidate_integrity_model(pin) for pin in self.admissions)
        ids = tuple(pin.training_fact_admission_id for pin in pins)
        if not pins or ids != tuple(sorted(set(ids))):
            raise ArchiveValidationError(
                "memberships require sorted unique admission pins"
            )
        facts: list[TrainingFactBindingV1] = []
        for pin in pins:
            if pin.persisted_at_utc > at:
                raise ArchiveValidationError(
                    "membership admission is not yet persisted"
                )
            admission = revalidate_integrity_model(
                self.repository.load(pin.training_fact_admission_id)
            )
            if TrainingAdmissionPinV1.from_admission(admission) != pin:
                raise ArchiveValidationError(
                    "membership admission differs from exact pin"
                )
            admission.source_rights_admission.assert_active_for(
                at, TRAINING_FACT_REQUIRED_USES
            )
            facts.extend(admission.facts)
        for key in ("match_id", "match_result_id"):
            values = tuple(
                getattr(f.content_payload.normalized_result, key) for f in facts
            )
            if len(values) != len(set(values)):
                raise ArchiveValidationError(
                    "ambiguous membership across pinned admissions"
                )
        # Audit replay may retain a verified original after a controlled correction.
        # Refuse that stream until this consumer accepts explicit correction context.
        with self.repository._sessions.begin() as session:
            session.execute(text("BEGIN"))
            if session.scalar(
                text(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'training_correction_streams'"
                )
            ):
                for fact in facts:
                    result = fact.content_payload.normalized_result
                    if session.scalar(
                        text(
                            "SELECT 1 FROM training_correction_streams AS s "
                            "JOIN training_correction_admissions AS a "
                            "ON a.stream_id = s.stream_id "
                            "JOIN providers AS p ON p.provider_id = s.provider_id "
                            "WHERE p.code = :provider AND s.internal_match_id = :match "
                            "LIMIT 1"
                        ),
                        {"provider": result.provider_code, "match": result.match_id},
                    ):
                        raise ControlledTrainingCorrectionRequired(
                            "archive memberships require explicit correction context "
                            "for a registered correction stream"
                        )
        return tuple(facts)

    def load_context(self) -> TrainingCorrectionContextV2:
        if (
            type(self.repository) is not SqlAlchemyTrainingAdmissionRepository
            or self.correction_context is None
        ):
            raise MissingArchiveInputError(
                "versioned archive requires concrete repository and explicit context pin"
            )
        pin = revalidate_integrity_model(self.correction_context)
        admissions = tuple(revalidate_integrity_model(p) for p in self.admissions)
        if tuple(
            (p.training_fact_admission_id, p.admission_hash) for p in admissions
        ) != tuple((p.artifact_id, p.content_hash) for p in pin.base_admissions):
            raise ArchiveValidationError("archive admission/context pins mismatch")
        with self.repository._sessions.begin() as session:
            session.execute(text("BEGIN"))
            at = self.repository._now()
            if at < normalize_utc(self.verified_at_utc):
                raise ArchiveValidationError(
                    "membership verification cannot forecast actual time"
                )
            rights = []
            from football_system.infrastructure.database.models import (
                TrainingFactAdmissionRecord,
            )

            for admission in admissions:
                # Bind rights to the actual parent header before reading result bytes.
                parent = TrainingFactAdmissionRecord
                header = session.execute(
                    select(
                        parent.admission_hash,
                        parent.source_rights_admission_id,
                        parent.persisted_at_utc,
                    ).where(
                        parent.training_fact_admission_id
                        == admission.training_fact_admission_id
                    )
                ).one_or_none()
                if header is None or tuple(header) != (
                    admission.admission_hash,
                    admission.source_rights_admission_id,
                    admission.persisted_at_utc,
                ):
                    raise ArchiveValidationError(
                        "archive base admission/rights pin mismatch"
                    )
                value = self.repository._rights(
                    session, admission.source_rights_admission_id
                )
                if value.admission_hash != admission.source_rights_admission_hash:
                    raise ArchiveValidationError("archive rights pin mismatch")
                value.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
                rights.append(value)
            context = correction_context_in_session(self.repository, session, pin, at)
            for admission in admissions:
                actual = self.repository._load(
                    session, admission.training_fact_admission_id
                )
                if TrainingAdmissionPinV1.from_admission(actual) != admission:
                    raise ArchiveValidationError("archive admission differs from exact pin")
            completed = self.repository._now()
            for value in rights:
                value.assert_active_for(completed, TRAINING_FACT_REQUIRED_USES)
            assert_complete_correction_pins(session, pin, completed)
            return context


class HistoricalArchiveEloTrainingProvider(
    _HistoricalArchiveProvider,
    EloTrainingHistoryProvider,
):
    """Join local archive results to independently verified per-fact memberships.

    Public constructor::

        HistoricalArchiveEloTrainingProvider(
            archive_source, provider_code=None, *, fixture_provider_code=None,
            membership_source=None, ordered_season_ids=(), season_id=None,
            data_mode=None,
        )

    ``archive_source`` remains a path or LocalArchiveStore. ``membership_source``
    must be RepositoryArchiveMembershipSource; omitting it fails closed, including
    obsolete season-only calls. Its SOURCE_TIME_RESEARCH mode must equal the archive
    mode, and its fixture/result/mapping provider must equal both selected providers.
    Other archive adapters still support ordinary LIVE_STRICT inputs unchanged.

    ``ordered_season_ids`` declares unique chronological seasons, including any
    empty final target season. It validates order, never assigns a fact's season.
    Optional legacy ``season_id`` only asserts that ALL verified facts belong to
    that one season; it cannot replace membership evidence or restrict the target.
    Construction verifies the pins, and every fetch rereads raw evidence and checks
    archive files against the loaded envelopes. No archive or persisted run is edited.

    ``fetch_elo_training_history(query)`` uses inclusive source cutoffs independently
    for fixture, mapping, membership and result. Not-yet-visible evidence yields no
    fact; missing, ambiguous or mismatched evidence raises ValueError. All target
    match IDs supplied in ``query.exclude_match_ids`` are excluded before joining.
    Results retain real identity/season and original normalized timestamps, never
    max-source/import times. The shared admitted selector enforces chronological
    season blocks and Elo ordering. ``target_season_id`` is passed through for the
    Elo engine's final transition, never propagated into facts; a target preceding
    selected history is rejected. Corrections require explicit pinned context;
    archive fixtures/results must match the exact revision, never silently latest.
    """

    dataset_kind = HistoricalArchiveDatasetKind.MATCH_RESULTS

    def __init__(
        self,
        archive_source: str | Path | LocalArchiveStore,
        provider_code: str | None = None,
        *,
        fixture_provider_code: str | None = None,
        membership_source: RepositoryArchiveMembershipSource | None = None,
        ordered_season_ids: tuple[str, ...] = (),
        season_id: str | None = None,
        data_mode: HistoricalDataMode | str | None = None,
    ) -> None:
        if not isinstance(membership_source, RepositoryArchiveMembershipSource):
            raise MissingArchiveInputError(
                "Elo archive history requires an explicit verified membership_source; "
                "constructor season labels are not evidence"
            )
        if membership_source.correction_context is not None:
            context = membership_source.load_context()
            archive_source = LocalArchiveStore(
                archive_source.directory
                if isinstance(archive_source, LocalArchiveStore)
                else archive_source,
                data_mode=data_mode,
                correction_context=context,
            )
        super().__init__(archive_source, provider_code, data_mode=data_mode)
        if self.data_mode is not membership_source.data_mode:
            raise ArchiveValidationError(
                "repository memberships require SOURCE_TIME_RESEARCH archives; "
                "data modes cannot be mixed"
            )
        self.fixture_provider_code = self._store.resolve_provider(
            HistoricalArchiveDatasetKind.FIXTURES,
            fixture_provider_code,
        )
        if self.fixture_provider_code != self.provider_code:
            raise ArchiveValidationError(
                "verified memberships require the same fixture and result provider"
            )
        if (
            not ordered_season_ids
            or any(not s or s != s.strip() for s in ordered_season_ids)
            or len(ordered_season_ids) != len(set(ordered_season_ids))
        ):
            raise ArchiveValidationError(
                "explicit unique ordered_season_ids are required"
            )
        if season_id is not None and (not season_id or season_id != season_id.strip()):
            raise ValueError(
                "Elo archive season_id assertion must be an exact identifier"
            )
        self.membership_source = membership_source
        self.ordered_season_ids = tuple(ordered_season_ids)
        self.season_id = season_id
        self._verified_memberships()

    def _verified_memberships(self) -> tuple[TrainingFactBindingV1, ...]:
        facts = self.membership_source.load_facts()
        for fact in facts:
            if self.membership_source.correction_context is not None:
                if (
                    fact.snapshot.stream.provider_code != self.provider_code
                    or fact.snapshot.identity.season not in self.ordered_season_ids
                    or (
                        self.season_id is not None
                        and fact.snapshot.identity.season != self.season_id
                    )
                ):
                    raise ArchiveValidationError(
                        "version metadata outside exact archive provider/season scope"
                    )
                continue
            binding = fact.content_payload
            if binding.provider_mapping.provider_code != self.provider_code:
                raise ArchiveValidationError(
                    "membership has the wrong archive provider"
                )
            season = binding.season_membership.content_payload.canonical_season_id
            if season not in self.ordered_season_ids:
                raise ArchiveValidationError(
                    "membership is outside declared ordered seasons"
                )
            if self.season_id is not None and season != self.season_id:
                raise ArchiveValidationError(
                    "season_id assertion does not match verified per-fact membership"
                )
        return facts

    async def fetch_elo_training_history(
        self,
        query: EloTrainingHistoryQuery,
    ) -> EloTrainingHistoryBatch:
        query = revalidate_integrity_model(query)
        if query.target_season_id not in self.ordered_season_ids:
            raise MissingArchiveInputError(
                "target season is outside declared ordered seasons"
            )
        if self.membership_source.correction_context is not None:
            return self._fetch_versioned_history(query)
        if query.as_of_at_utc > normalize_utc(self.membership_source.verified_at_utc):
            raise ArchiveValidationError(
                "source cutoff cannot follow membership verification"
            )
        facts = self._verified_memberships()
        by_result = {
            f.content_payload.normalized_result.match_result_id: f for f in facts
        }
        for archive in self._store.archives:
            if (
                archive.manifest.provider_code == self.provider_code
                and (
                    archive.manifest.dataset_kind
                    in {
                        self.dataset_kind,
                        HistoricalArchiveDatasetKind.FIXTURES,
                        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
                    }
                )
                and load_historical_archive(archive.path).document != archive.document
            ):
                raise ArchiveValidationError("archive changed since it was loaded")

        excluded = set(query.exclude_match_ids)
        fixtures: dict[str, list[FixtureArchivePayload]] = defaultdict(list)
        for entry in self._store._records(
            HistoricalArchiveDatasetKind.FIXTURES,
            self.fixture_provider_code,
        ):
            fixture = cast(FixtureArchiveRecord, entry.record).payload
            fixtures[fixture.match.match_id].append(fixture)

        entries: dict[str, _ArchiveEntry] = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            result = cast(MatchResultArchiveRecord, entry.record).payload
            if result.match_id in excluded or not _result_visible(
                result, query.as_of_at_utc
            ):
                continue
            versions = fixtures.get(result.match_id)
            if not versions:
                raise MissingArchiveInputError(
                    "Elo result has no archive fixture identity"
                )
            match = versions[0].match
            fact = by_result.get(result.match_result_id)
            if match.competition_id != query.competition_id and (
                fact is None
                or fact.content_payload.canonical_identity.internal_competition_id
                != query.competition_id
            ):
                continue
            if result.supersedes_match_result_id is not None:
                raise ControlledTrainingCorrectionRequired(
                    "archive Elo correction requires verified correction context"
                )
            if fact is None:
                raise MissingArchiveInputError(
                    "Elo result has no pinned verified membership"
                )
            binding = fact.content_payload
            if (
                NormalizedMatchResultRecordV1.from_result(result)
                != binding.normalized_result
            ):
                raise ArchiveValidationError(
                    "archive normalized result/hash differs from verified membership"
                )
            identity = binding.canonical_identity
            if (
                match.match_id,
                match.competition_id,
                match.home_team_id,
                match.away_team_id,
            ) != (
                identity.internal_match_id,
                identity.internal_competition_id,
                identity.internal_home_team_id,
                identity.internal_away_team_id,
            ):
                raise ArchiveValidationError(
                    "archive fixture identity differs from membership"
                )
            entries[result.match_result_id] = entry

        selected = select_admitted_training_facts(
            facts,
            query.competition_id,
            self.ordered_season_ids,
            query.as_of_at_utc,
            excluded,
            strict_cutoff=False,
        )
        sources = []
        mappings = _mapping_payloads(self._store, self.provider_code)
        for fact in selected:
            binding = fact.content_payload
            identity = binding.canonical_identity
            entry = entries.get(binding.normalized_result.match_result_id)
            if entry is None:
                raise MissingArchiveInputError(
                    "verified result is missing from the archive at source cutoff"
                )
            visible_fixtures = [
                f
                for f in fixtures[identity.internal_match_id]
                if f.match.available_at_utc <= query.as_of_at_utc
            ]
            if not visible_fixtures:
                raise MissingArchiveInputError(
                    "verified fixture is missing at source cutoff"
                )
            match = max(visible_fixtures, key=lambda f: f.match.available_at_utc).match
            if (match.kickoff_at_utc, match.available_at_utc) != (
                identity.kickoff_at_utc,
                binding.fixture_source.source_available_at_utc,
            ):
                raise ArchiveValidationError(
                    "archive fixture kickoff/source availability differs from membership; "
                    "correction context is required"
                )
            matching = tuple(
                m for m in mappings if m.internal_match_id == match.match_id
            )
            if len(matching) != 1 or (
                TrainingProviderMatchMappingV1.from_mapping(matching[0])
                != binding.provider_mapping
            ):
                raise ArchiveValidationError(
                    "archive mapping is ambiguous or differs from membership"
                )
            if self.ordered_season_ids.index(
                identity.season
            ) > self.ordered_season_ids.index(query.target_season_id):
                raise ArchiveValidationError(
                    "target season precedes selected training history"
                )
            sources.append(
                EloTrainingResultSource(
                    result=project_admitted_training_fact(fact),
                    archive=BacktestArchiveProvenance.from_manifest(
                        entry.archive.manifest
                    ),
                )
            )
        return EloTrainingHistoryBatch(
            competition_id=query.competition_id,
            target_season_id=query.target_season_id,
            as_of_at_utc=query.as_of_at_utc,
            sources=tuple(sources),
        )

    def _fetch_versioned_history(self, query):
        context = self.membership_source.load_context()
        for archive in self._store.archives:
            if (
                load_historical_archive(
                    archive.path, correction_context=context
                ).document
                != archive.document
            ):
                raise ArchiveValidationError("archive changed since it was loaded")
        by_result = {
            v.normalized_result.match_result_id: v
            for v in context.versions
            if v.normalized_result is not None
        }
        entries = {}
        for entry in self._store._records(self.dataset_kind, self.provider_code):
            result = entry.record.payload
            if result.match_id in query.exclude_match_ids or not _result_visible(
                result, query.as_of_at_utc
            ):
                continue
            version = by_result.get(result.match_result_id)
            if (
                version is None
                or NormalizedMatchResultRecordV1.from_result(result)
                != version.normalized_result
            ):
                raise ArchiveValidationError(
                    "archive normalized revision is not in the exact pinned context"
                )
            entries[result.match_result_id] = entry
        fixtures = tuple(
            e.record.payload.match
            for e in self._store._records(
                HistoricalArchiveDatasetKind.FIXTURES, self.provider_code
            )
        )
        mappings = _mapping_payloads(self._store, self.provider_code)
        facts = select_versioned_training_facts(
            context,
            query.competition_id,
            self.ordered_season_ids,
            query.as_of_at_utc,
            query.exclude_match_ids,
            False,
        )
        sources = []
        for version in facts:
            s, identity = version.snapshot, version.snapshot.identity
            if (
                s.stream.provider_code != self.provider_code
                or self.ordered_season_ids.index(identity.season)
                > self.ordered_season_ids.index(query.target_season_id)
            ):
                raise ArchiveValidationError(
                    "selected archive version outside provider/target season scope"
                )
            exact = [
                f
                for f in fixtures
                if (
                    f.match_id,
                    f.competition_id,
                    f.home_team_id,
                    f.away_team_id,
                    f.kickoff_at_utc,
                    f.available_at_utc,
                )
                == (
                    identity.internal_match_id,
                    identity.internal_competition_id,
                    identity.internal_home_team_id,
                    identity.internal_away_team_id,
                    identity.kickoff_at_utc,
                    s.fixture_source_available_at_utc,
                )
            ]
            matching = tuple(
                m for m in mappings if m.internal_match_id == identity.internal_match_id
            )
            if (
                len(exact) != 1
                or len(matching) != 1
                or TrainingProviderMatchMappingV1.from_mapping(matching[0])
                != s.provider_mapping
            ):
                raise ArchiveValidationError(
                    "exact revision fixture/mapping metadata missing from archive"
                )
            entry = entries.get(version.normalized_result.match_result_id)
            if entry is None:
                raise MissingArchiveInputError(
                    "selected normalized revision missing from archive"
                )
            sources.append(
                EloTrainingResultSource(
                    result=project_versioned_training_fact(version),
                    archive=BacktestArchiveProvenance.from_manifest(
                        entry.archive.manifest
                    ),
                )
            )
        # Recheck possession, raw evidence and complete registered heads at actual end.
        end = self.membership_source.load_context()
        if end.versions != context.versions or end.corrections != context.corrections:
            raise ArchiveValidationError(
                "archive membership context changed during query"
            )
        return EloTrainingHistoryBatch(
            competition_id=query.competition_id,
            target_season_id=query.target_season_id,
            as_of_at_utc=query.as_of_at_utc,
            sources=tuple(sources),
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
        source_known_at = _source_known_at(record, manifest.data_mode)
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
                "research snapshot/result ingestion must equal source availability"
            )
        if (
            isinstance(
                payload, (MarketOddsSnapshot, SportteryBonusSnapshot, MatchResult)
            )
            and payload.ingested_at_utc > imported_at
        ):
            raise ArchiveValidationError(
                "research record ingestion cannot occur after its import timestamp"
            )


def _source_known_at(
    record: TypedArchiveRecord,
    data_mode: HistoricalDataMode,
) -> datetime:
    payload = record.payload
    if isinstance(payload, FixtureArchivePayload):
        return payload.match.available_at_utc
    if isinstance(payload, (MarketOddsSnapshot, SportteryBonusSnapshot, MatchResult)):
        if data_mode is HistoricalDataMode.SOURCE_TIME_RESEARCH:
            return payload.available_at_utc
        return payload.ingested_at_utc
    if isinstance(payload, MarketOddsIssueArchivePayload):
        return payload.available_at_utc
    if isinstance(payload, (ManualQuantInput, ProviderMatchMapping)):
        return payload.available_at_utc
    raise TypeError(f"unsupported archive payload: {type(payload).__name__}")


def _validate_business_keys(
    entries: tuple[_ArchiveEntry, ...], *, correction_context=None
) -> None:
    fixtures = _entries_of_kind(entries, HistoricalArchiveDatasetKind.FIXTURES)
    market = _entries_of_kind(entries, HistoricalArchiveDatasetKind.MARKET_ODDS)
    market_issues = _entries_of_kind(
        entries,
        HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES,
    )
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
            *(
                (
                    cast(
                        FixtureArchiveRecord, entry.record
                    ).payload.match.model_dump_json(),
                )
                if correction_context is not None
                else ()
            ),
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
        market_issues,
        lambda entry: (
            cast(MarketOddsIssueArchiveRecord, entry.record).payload.issue.issue_id,
            cast(MarketOddsIssueArchiveRecord, entry.record).payload.available_at_utc,
        ),
        "market odds issue version",
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
            *(
                (cast(MatchResultArchiveRecord, entry.record).payload.match_result_id,)
                if correction_context is not None
                else ()
            ),
        ),
        "match result source key",
    )
    _assert_unique(
        results,
        lambda entry: (
            cast(MatchResultArchiveRecord, entry.record).payload.match_result_id,
        )
        if correction_context is not None
        else _result_business_version_key(
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
    _validate_fixture_identity(fixtures, correction_context=correction_context)
    if correction_context is not None:
        expected_fixtures = {
            (
                v.snapshot.stream.provider_code,
                v.snapshot.identity.internal_match_id,
                v.snapshot.identity.internal_competition_id,
                v.snapshot.identity.internal_home_team_id,
                v.snapshot.identity.internal_away_team_id,
                v.snapshot.identity.kickoff_at_utc,
                v.snapshot.fixture_source_available_at_utc,
            )
            for v in correction_context.versions
        }
        known_matches = {key[:2] for key in expected_fixtures}
        for entry in fixtures:
            match = entry.record.payload.match
            key = (
                entry.provider_code,
                match.match_id,
                match.competition_id,
                match.home_team_id,
                match.away_team_id,
                match.kickoff_at_utc,
                match.available_at_utc,
            )
            if key[:2] in known_matches and key not in expected_fixtures:
                raise ArchiveValidationError(
                    "archive fixture differs from exact controlled revision metadata"
                )
        expected = {
            v.normalized_result.match_result_id: v.normalized_result
            for v in correction_context.versions
            if v.normalized_result is not None
        }
        for entry in results:
            value = entry.record.payload
            if expected.get(
                value.match_result_id
            ) != NormalizedMatchResultRecordV1.from_result(value):
                raise ArchiveValidationError(
                    "archive result differs from exact controlled version"
                )


def _validate_fixture_identity(
    entries: tuple[_ArchiveEntry, ...], *, correction_context=None
) -> None:
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
            if correction_context is not None:
                match = fixture.match
                if any(
                    (
                        v.snapshot.identity.internal_match_id,
                        v.snapshot.identity.internal_competition_id,
                        v.snapshot.identity.internal_home_team_id,
                        v.snapshot.identity.internal_away_team_id,
                        v.snapshot.identity.kickoff_at_utc,
                        v.snapshot.fixture_source_available_at_utc,
                    )
                    == (
                        match.match_id,
                        *identity,
                        match.kickoff_at_utc,
                        match.available_at_utc,
                    )
                    for v in correction_context.versions
                ):
                    continue
            raise ArchiveValidationError(
                f"conflicting canonical fixture identity: {fixture.match.match_id}"
            )


def _validate_result_supersession(
    entries: tuple[_ArchiveEntry, ...],
    *,
    require_complete: bool,
    correction_context=None,
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
            and (
                parent.ingested_at_utc < result.ingested_at_utc
                or (
                    correction_context is not None
                    and parent.ingested_at_utc == result.ingested_at_utc
                    and any(
                        v.normalized_result is not None
                        and v.normalized_result.match_result_id
                        == result.match_result_id
                        and v.snapshot.provider_revision_order is not None
                        for v in correction_context.versions
                    )
                )
            )
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
        if (
            entry.dataset_kind is HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS
            or entry.dataset_kind is HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES
            or isinstance(entry.record, MarketOddsIssueArchiveRecord)
        ):
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
                _sporttery_external_match_id_matches(
                    mapping.external_match_id,
                    sporttery_no,
                )
                for mapping in candidates
            ):
                raise ArchiveValidationError(
                    f"Sporttery record for {match_id} has no mapping for match "
                    f"number {sporttery_no}"
                )
        if (
            entry.dataset_kind is HistoricalArchiveDatasetKind.MARKET_ODDS
            and entry.provider_code == "THE_ODDS_API"
        ):
            snapshot = cast(MarketOddsArchiveRecord, entry.record).payload
            if not any(
                mapping.external_namespace == "event"
                and stable_id(
                    "the-odds-api-source",
                    mapping.external_match_id,
                    snapshot.bookmaker_code,
                    snapshot.captured_at_utc.isoformat(),
                    snapshot.available_at_utc.isoformat(),
                    snapshot.payload_hash,
                )
                == snapshot.source_snapshot_key
                for mapping in candidates
            ):
                raise ArchiveValidationError(
                    f"MARKET_ODDS record for {match_id} has no exact The Odds API "
                    "event mapping"
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
) -> bool:
    return all(
        timestamp <= cutoff
        for timestamp in (
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
    )


def _result_visible(
    result: MatchResult,
    cutoff: datetime,
) -> bool:
    return all(
        timestamp <= cutoff
        for timestamp in (
            result.observed_at_utc,
            result.available_at_utc,
            result.ingested_at_utc,
        )
    )


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
            external_match_id is None
            or _sporttery_external_match_id_matches(
                mapping.external_match_id,
                external_match_id,
            )
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
                    or any(
                        _sporttery_external_match_id_matches(
                            mapping.external_match_id,
                            external_match_id,
                        )
                        for external_match_id in external_match_ids
                    )
                )
            ),
            key=lambda mapping: mapping.mapping_id,
        )
    )


def _sporttery_external_match_id_matches(actual: str, match_number: str) -> bool:
    if actual == match_number:
        return True
    date_text, separator, candidate = actual.rpartition(":")
    if not separator or candidate != match_number:
        return False
    try:
        date.fromisoformat(date_text)
    except ValueError:
        return False
    return True


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
