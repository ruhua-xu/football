from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
)
from football_system.domain.common import DomainModel, UtcDateTime, normalize_utc
from football_system.infrastructure.providers.historical_archive import (
    LocalArchiveStore,
)

REGISTRATION_SCOPE = "MANIFEST_PROVENANCE_ONLY"
MATERIALIZATION_POLICY = (
    "READ_ONLY_FILES; CUTOFF_SELECTED_DECISION_INPUTS_ONLY; MATCH_RESULTS_AT_EVALUATION"
)


@runtime_checkable
class HistoricalArchiveImportRepository(Protocol):
    def append_historical_archive_import(
        self,
        manifest: HistoricalArchiveManifest,
        imported_at_utc: datetime,
    ) -> object: ...

    def historical_archive_manifests(
        self,
        *,
        provider_code: str | None = None,
        dataset_kind: str | None = None,
        data_mode: str | None = None,
    ) -> tuple[HistoricalArchiveManifest, ...]: ...


@runtime_checkable
class BulkHistoricalArchiveImportRepository(
    HistoricalArchiveImportRepository, Protocol
):
    def append_historical_archive_imports(
        self,
        manifests: Sequence[HistoricalArchiveManifest],
        imported_at_utc: datetime,
    ) -> Sequence[object]: ...


class HistoricalArchiveRegistrationConflict(ValueError):
    pass


class HistoricalArchiveKindSummary(DomainModel):
    dataset_kind: HistoricalArchiveDatasetKind
    archive_count: int = Field(ge=1)
    record_count: int = Field(ge=0)
    manifests: tuple[HistoricalArchiveManifest, ...]
    checksums: tuple[str, ...]

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.archive_count != len(self.manifests):
            raise ValueError("per-kind archive count does not match manifests")
        if self.record_count != sum(
            manifest.record_count for manifest in self.manifests
        ):
            raise ValueError("per-kind record count does not match manifests")
        if any(
            manifest.dataset_kind is not self.dataset_kind
            for manifest in self.manifests
        ):
            raise ValueError("per-kind summary contains another dataset kind")
        if self.checksums != tuple(
            manifest.payload_sha256 for manifest in self.manifests
        ):
            raise ValueError("per-kind checksums do not match manifests")
        return self


class HistoricalArchiveSummary(DomainModel):
    operation: Literal["VALIDATE", "REGISTER"]
    directory: str = Field(min_length=1)
    data_mode: HistoricalDataMode
    report_label: str = Field(min_length=1)
    retrospective: bool
    archive_count: int = Field(ge=1)
    record_count: int = Field(ge=0)
    manifests: tuple[HistoricalArchiveManifest, ...]
    checksums: tuple[str, ...]
    per_kind: tuple[HistoricalArchiveKindSummary, ...]
    registration_scope: Literal["MANIFEST_PROVENANCE_ONLY"] = REGISTRATION_SCOPE
    materialization_policy: Literal[
        "READ_ONLY_FILES; CUTOFF_SELECTED_DECISION_INPUTS_ONLY; "
        "MATCH_RESULTS_AT_EVALUATION"
    ] = MATERIALIZATION_POLICY
    imported_at_utc: UtcDateTime | None = None
    registered_archive_ids: tuple[str, ...] = ()
    existing_archive_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.report_label != self.data_mode.report_label:
            raise ValueError("archive report label does not match data mode")
        if self.retrospective is not self.data_mode.is_retrospective:
            raise ValueError("archive retrospective marker does not match data mode")
        if self.archive_count != len(self.manifests):
            raise ValueError("archive count does not match manifests")
        if self.record_count != sum(
            manifest.record_count for manifest in self.manifests
        ):
            raise ValueError("record count does not match manifests")
        if self.checksums != tuple(
            manifest.payload_sha256 for manifest in self.manifests
        ):
            raise ValueError("checksums do not match manifests")
        if (
            tuple(
                manifest for summary in self.per_kind for manifest in summary.manifests
            )
            != self.manifests
        ):
            raise ValueError("per-kind manifests do not match archive manifests")
        archive_ids = tuple(manifest.archive_id for manifest in self.manifests)
        registration_ids = self.registered_archive_ids + self.existing_archive_ids
        if self.operation == "VALIDATE":
            if self.imported_at_utc is not None or registration_ids:
                raise ValueError("validation summary cannot contain registration state")
        elif self.imported_at_utc is None or set(registration_ids) != set(archive_ids):
            raise ValueError("registration summary must account for every archive")
        if len(registration_ids) != len(set(registration_ids)):
            raise ValueError("registration summary archive IDs must be unique")
        return self


class HistoricalArchiveService:
    """Validate local archives and register provenance without materializing payloads.

    Archive payload versions remain immutable, read-only files. Analysis providers later
    expose only versions legal at a decision cutoff, and the existing AnalysisRepository
    materializes only those selected decision inputs. Registration appends manifest,
    checksum, and provenance rows only; it deliberately does not populate normalized
    source tables, because immutable matches cannot represent future fixture-status
    versions without weakening point-in-time semantics. MatchResult facts are materialized
    separately during backtest evaluation persistence.

    A repository bulk append is preferred so all provenance rows share one transaction.
    Repositories exposing only the singular append API are fully preflighted before the
    deterministic append sequence, minimizing partial registration risk.
    """

    def validate(
        self,
        directory: str | Path,
        data_mode: HistoricalDataMode | str,
    ) -> HistoricalArchiveSummary:
        store = self._validated_store(directory, data_mode)
        return _build_summary(store, operation="VALIDATE")

    def register(
        self,
        directory: str | Path,
        repository: HistoricalArchiveImportRepository,
        imported_at_utc: datetime,
        data_mode: HistoricalDataMode | str,
    ) -> HistoricalArchiveSummary:
        store = self._validated_store(directory, data_mode)
        imported_at = normalize_utc(imported_at_utc)
        manifests = _sorted_manifests(store)
        if any(manifest.created_at_utc > imported_at for manifest in manifests):
            raise ValueError("archive import time cannot precede manifest creation")

        pending, existing = _preflight_registration(repository, manifests)
        if pending and isinstance(repository, BulkHistoricalArchiveImportRepository):
            stored = tuple(
                repository.append_historical_archive_imports(pending, imported_at)
            )
            if len(stored) != len(pending):
                raise RuntimeError(
                    "bulk archive registration returned an incomplete result"
                )
        else:
            for manifest in pending:
                repository.append_historical_archive_import(manifest, imported_at)

        return _build_summary(
            store,
            operation="REGISTER",
            imported_at_utc=imported_at,
            registered_archive_ids=tuple(manifest.archive_id for manifest in pending),
            existing_archive_ids=tuple(manifest.archive_id for manifest in existing),
        )

    @staticmethod
    def _validated_store(
        directory: str | Path,
        data_mode: HistoricalDataMode | str,
    ) -> LocalArchiveStore:
        try:
            expected_mode = HistoricalDataMode(data_mode)
        except ValueError as exc:
            raise ValueError(
                f"unsupported historical archive data mode: {data_mode}"
            ) from exc
        # Loading without a mode selector rejects mixed-mode directories instead of
        # silently registering only one subset of their manifests.
        store = LocalArchiveStore(directory)
        if store.data_mode is not expected_mode:
            raise ValueError(
                f"archive data mode is {store.data_mode.value}, not "
                f"{expected_mode.value}"
            )
        manifests = _sorted_manifests(store)
        archive_ids = [manifest.archive_id for manifest in manifests]
        checksums = [manifest.payload_sha256 for manifest in manifests]
        if len(archive_ids) != len(set(archive_ids)):
            raise ValueError("archive manifests must have unique archive IDs")
        if len(checksums) != len(set(checksums)):
            raise ValueError("archive manifests must have unique payload checksums")
        return store


def _sorted_manifests(
    store: LocalArchiveStore,
) -> tuple[HistoricalArchiveManifest, ...]:
    return tuple(
        archive.manifest
        for archive in sorted(
            store.archives,
            key=lambda archive: (
                archive.manifest.dataset_kind.value,
                archive.manifest.provider_code,
                archive.manifest.archive_id,
            ),
        )
    )


def _build_summary(
    store: LocalArchiveStore,
    *,
    operation: Literal["VALIDATE", "REGISTER"],
    imported_at_utc: datetime | None = None,
    registered_archive_ids: tuple[str, ...] = (),
    existing_archive_ids: tuple[str, ...] = (),
) -> HistoricalArchiveSummary:
    manifests = _sorted_manifests(store)
    by_kind: dict[HistoricalArchiveDatasetKind, list[HistoricalArchiveManifest]] = (
        defaultdict(list)
    )
    for manifest in manifests:
        by_kind[manifest.dataset_kind].append(manifest)
    per_kind = tuple(
        HistoricalArchiveKindSummary(
            dataset_kind=kind,
            archive_count=len(kind_manifests),
            record_count=sum(manifest.record_count for manifest in kind_manifests),
            manifests=tuple(kind_manifests),
            checksums=tuple(manifest.payload_sha256 for manifest in kind_manifests),
        )
        for kind, kind_manifests in sorted(
            by_kind.items(), key=lambda item: item[0].value
        )
    )
    return HistoricalArchiveSummary(
        operation=operation,
        directory=str(store.directory),
        data_mode=store.data_mode,
        report_label=store.data_mode.report_label,
        retrospective=store.data_mode.is_retrospective,
        archive_count=len(manifests),
        record_count=sum(manifest.record_count for manifest in manifests),
        manifests=manifests,
        checksums=tuple(manifest.payload_sha256 for manifest in manifests),
        per_kind=per_kind,
        imported_at_utc=imported_at_utc,
        registered_archive_ids=registered_archive_ids,
        existing_archive_ids=existing_archive_ids,
    )


def _preflight_registration(
    repository: HistoricalArchiveImportRepository,
    manifests: tuple[HistoricalArchiveManifest, ...],
) -> tuple[
    tuple[HistoricalArchiveManifest, ...],
    tuple[HistoricalArchiveManifest, ...],
]:
    stored = tuple(repository.historical_archive_manifests())
    if any(not isinstance(manifest, HistoricalArchiveManifest) for manifest in stored):
        raise TypeError("archive repository returned an invalid manifest inventory")
    stored_by_id: dict[str, HistoricalArchiveManifest] = {}
    stored_by_checksum: dict[
        tuple[str, HistoricalArchiveDatasetKind, str], HistoricalArchiveManifest
    ] = {}
    for manifest in stored:
        previous_id = stored_by_id.setdefault(manifest.archive_id, manifest)
        checksum_key = (
            manifest.provider_code,
            manifest.dataset_kind,
            manifest.payload_sha256,
        )
        previous_checksum = stored_by_checksum.setdefault(checksum_key, manifest)
        if previous_id != manifest or previous_checksum != manifest:
            raise HistoricalArchiveRegistrationConflict(
                "stored historical archive manifest inventory is inconsistent"
            )

    pending: list[HistoricalArchiveManifest] = []
    existing: list[HistoricalArchiveManifest] = []
    for manifest in manifests:
        stored_manifest = stored_by_id.get(manifest.archive_id)
        if stored_manifest is not None:
            if stored_manifest != manifest:
                raise HistoricalArchiveRegistrationConflict(
                    f"archive ID conflicts with stored provenance: {manifest.archive_id}"
                )
            existing.append(manifest)
            continue
        checksum_key = (
            manifest.provider_code,
            manifest.dataset_kind,
            manifest.payload_sha256,
        )
        checksum_manifest = stored_by_checksum.get(checksum_key)
        if checksum_manifest is not None:
            raise HistoricalArchiveRegistrationConflict(
                "archive checksum identity conflicts with stored provenance: "
                f"{manifest.payload_sha256}"
            )
        pending.append(manifest)
    return tuple(pending), tuple(existing)
