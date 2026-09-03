from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.ports.data_providers import (
    SnapshotQuery,
    SportteryBatch,
    SportteryProvider,
)
from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_payload_sha256,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.identity import MatchIdentityResolver, ProviderMatchIdentity
from football_system.domain.market import MarketKey, MarketType, ThreeWayFixedBonus
from football_system.domain.match import (
    FixedBonusQuote,
    ProviderMatchMapping,
    SaleStatus,
    SportteryBonusSnapshot,
)

SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1 = "SPORTTERY_MANUAL_ARCHIVE_V1"
SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION = "SPORTTERY_MANUAL_ARCHIVE_V2"
_SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1, SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION}
)
SPORTTERY_MANUAL_PROVIDER_CODE = "SPORTTERY_MANUAL"
_THREE_WAY_MARKET = MarketKey(market_type=MarketType.THREE_WAY)
_DECIMAL_STRING = re.compile(r"^\d+(?:\.\d+)?$")


class SportteryManualArchiveError(ValueError):
    code = "SPORTTERY_MANUAL_ARCHIVE_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


class SportteryManualReviewLevel(StrEnum):
    SELF_REVIEWED = "SELF_REVIEWED"
    INDEPENDENT_REVIEWED = "INDEPENDENT_REVIEWED"


class SportteryManualRecord(DomainModel):
    sporttery_match_no: Identifier
    match_number_date: date
    provider_competition_id: Identifier
    provider_competition_name: Identifier
    competition_language: str = Field(default="und", min_length=2, max_length=35)
    season: Identifier
    competition_type: Identifier
    provider_home_team_id: Identifier
    provider_home_team_name: Identifier
    home_team_language: str = Field(default="und", min_length=2, max_length=35)
    provider_away_team_id: Identifier
    provider_away_team_name: Identifier
    away_team_language: str = Field(default="und", min_length=2, max_length=35)
    kickoff_at_utc: UtcDateTime
    sale_status: SaleStatus
    market_type: str
    home_win: Decimal
    draw: Decimal
    away_win: Decimal

    @field_validator("match_number_date", mode="before")
    @classmethod
    def validate_match_number_date(cls, value: object) -> date:
        if not isinstance(value, str):
            raise ValueError("match_number_date must be an ISO date string")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("match_number_date must be an ISO date string") from None

    @field_validator("home_win", "draw", "away_win", mode="before")
    @classmethod
    def validate_decimal_string(cls, value: object) -> Decimal:
        # JSON numbers (and Python floats) are rejected to preserve entered odds exactly.
        if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value.strip()):
            raise ValueError("fixed bonuses must be decimal strings")
        try:
            decimal = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("fixed bonuses must be decimal strings") from None
        if not decimal.is_finite() or decimal <= 1:
            raise ValueError("fixed bonuses must be finite and above one")
        return decimal

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.market_type != MarketType.THREE_WAY.value:
            raise ValueError("manual Sporttery supports only THREE_WAY")
        if self.provider_home_team_id == self.provider_away_team_id:
            raise ValueError("manual Sporttery home and away teams must differ")
        return self


class SportteryManualDocument(DomainModel):
    schema_version: str
    snapshot_id: Identifier
    captured_at_utc: UtcDateTime
    source_reference: str = Field(min_length=1, max_length=2048)
    source_artifact_path: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entered_by: Identifier
    review_level: SportteryManualReviewLevel
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime
    records: tuple[SportteryManualRecord, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def upgrade_v1_review_level(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        raw_version = value.get("schema_version")
        normalized_version = (
            raw_version.strip() if isinstance(raw_version, str) else raw_version
        )
        if normalized_version != SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1:
            return value
        if "review_level" in value:
            raise ValueError("V1 manual Sporttery archives cannot declare review_level")
        return {
            **value,
            "schema_version": normalized_version,
            "review_level": SportteryManualReviewLevel.INDEPENDENT_REVIEWED,
        }

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("manual Sporttery schema version is unsupported")
        return value

    @model_validator(mode="after")
    def validate_review(self) -> Self:
        same_reviewer = self.entered_by.casefold() == self.reviewed_by.casefold()
        if (
            self.review_level is SportteryManualReviewLevel.SELF_REVIEWED
            and not same_reviewer
        ):
            raise ValueError(
                "self-reviewed manual Sporttery entry requires the same reviewer"
            )
        if (
            self.review_level is SportteryManualReviewLevel.INDEPENDENT_REVIEWED
            and same_reviewer
        ):
            raise ValueError(
                "independently reviewed manual Sporttery entry requires a distinct "
                "reviewer"
            )
        if self.reviewed_at_utc < self.captured_at_utc:
            raise ValueError("manual Sporttery review cannot predate capture")
        seen: set[tuple[str, date]] = set()
        for record in self.records:
            identity = (record.sporttery_match_no, record.match_number_date)
            if identity in seen:
                raise ValueError(
                    "manual Sporttery document has duplicate match number/date"
                )
            seen.add(identity)
            if (
                self.schema_version == SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION
                and len(_date_qualified_match_id(record)) > 160
            ):
                raise ValueError("date-qualified Sporttery match identity is too long")
        return self


_DOCUMENT_COLUMNS = (
    "schema_version",
    "snapshot_id",
    "captured_at_utc",
    "source_reference",
    "source_artifact_path",
    "source_artifact_sha256",
    "entered_by",
    "review_level",
    "reviewed_by",
    "reviewed_at_utc",
)
_V1_DOCUMENT_COLUMNS = tuple(
    column for column in _DOCUMENT_COLUMNS if column != "review_level"
)
_RECORD_COLUMNS = (
    "sporttery_match_no",
    "match_number_date",
    "provider_competition_id",
    "provider_competition_name",
    "competition_language",
    "season",
    "competition_type",
    "provider_home_team_id",
    "provider_home_team_name",
    "home_team_language",
    "provider_away_team_id",
    "provider_away_team_name",
    "away_team_language",
    "kickoff_at_utc",
    "sale_status",
    "market_type",
    "home_win",
    "draw",
    "away_win",
)
_CSV_COLUMNS = _DOCUMENT_COLUMNS + _RECORD_COLUMNS
_V1_CSV_COLUMNS = _V1_DOCUMENT_COLUMNS + _RECORD_COLUMNS


class SportteryManualSnapshotProvenance(DomainModel):
    schema_version: Identifier
    snapshot_id: Identifier
    source_snapshot_key: Identifier
    archive_snapshot_id: Identifier
    provider_code: Identifier
    sporttery_match_no: Identifier
    match_number_date: date
    review_level: SportteryManualReviewLevel
    entered_by: Identifier
    reviewed_by: Identifier
    captured_at_utc: UtcDateTime
    reviewed_at_utc: UtcDateTime
    source_reference: str = Field(min_length=1, max_length=2048)
    source_artifact_path: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _LoadedSnapshot:
    snapshot: SportteryBonusSnapshot
    mapping: ProviderMatchMapping
    provenance: SportteryManualSnapshotProvenance


class SportteryManualArchiveProvider(SportteryProvider):
    """Strict, reviewed manual fixed-bonus source for offline Sporttery imports."""

    def __init__(
        self,
        archive_source: str | Path,
        identity_resolver: MatchIdentityResolver,
        *,
        provider_code: str = SPORTTERY_MANUAL_PROVIDER_CODE,
    ) -> None:
        if not isinstance(provider_code, str) or not provider_code.strip():
            raise ValueError("manual Sporttery provider_code must be nonempty")
        self.provider_code = provider_code.strip()
        self._loaded = _load_documents(
            archive_source,
            identity_resolver,
            provider_code=self.provider_code,
        )
        self._provenance_by_snapshot_id = {
            item.snapshot.snapshot_id: item.provenance for item in self._loaded
        }
        self._runtime_provenance = RuntimeProvenance(
            environment=RuntimeEnvironment.LIVE,
            provider_code=self.provider_code,
            provenance=",".join(
                sorted({item.provenance.schema_version for item in self._loaded})
            ),
            is_mock=False,
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )

    @property
    def runtime_provenance(self) -> RuntimeProvenance:
        return self._runtime_provenance

    def provenance_for_snapshot(
        self,
        snapshot_id: str,
    ) -> SportteryManualSnapshotProvenance:
        try:
            return self._provenance_by_snapshot_id[snapshot_id]
        except KeyError:
            raise KeyError(
                f"manual Sporttery snapshot provenance is unavailable: {snapshot_id}"
            ) from None

    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch:
        selected = set(query.match_ids)
        latest: dict[tuple[str, str], _LoadedSnapshot] = {}
        for loaded in self._loaded:
            snapshot = loaded.snapshot
            if snapshot.match_id not in selected or not _snapshot_is_visible(
                snapshot,
                query,
            ):
                continue
            stream = (snapshot.match_id, loaded.mapping.external_match_id)
            previous = latest.get(stream)
            if previous is None or _snapshot_version(snapshot) > _snapshot_version(
                previous.snapshot
            ):
                latest[stream] = loaded
        values = tuple(
            sorted(
                latest.values(),
                key=lambda item: (
                    item.snapshot.match_id,
                    item.mapping.external_match_id,
                    item.snapshot.snapshot_id,
                ),
            )
        )
        return SportteryBatch(
            snapshots=tuple(item.snapshot for item in values),
            mappings=tuple(item.mapping for item in values),
        )


def _load_documents(
    archive_source: str | Path,
    identity_resolver: MatchIdentityResolver,
    *,
    provider_code: str,
) -> tuple[_LoadedSnapshot, ...]:
    paths = _document_paths(archive_source)
    loaded: list[_LoadedSnapshot] = []
    snapshot_ids: set[str] = set()
    loaded_snapshot_ids: set[str] = set()
    for path in paths:
        document = _read_document(path)
        if document.snapshot_id in snapshot_ids:
            raise SportteryManualArchiveError("duplicate manual snapshot_id")
        snapshot_ids.add(document.snapshot_id)
        artifact_hash = _verified_artifact_hash(path, document)
        for record in document.records:
            external_match_id = _external_match_id(document, record)
            provider_match = ProviderMatchIdentity(
                provider_code=provider_code,
                provider_match_id=external_match_id,
                external_namespace="sporttery_match",
                provider_competition_id=record.provider_competition_id,
                provider_competition_name=record.provider_competition_name,
                competition_language=record.competition_language,
                season=record.season,
                competition_type=record.competition_type,
                home_team_id=record.provider_home_team_id,
                home_team_name=record.provider_home_team_name,
                home_team_language=record.home_team_language,
                away_team_id=record.provider_away_team_id,
                away_team_name=record.provider_away_team_name,
                away_team_language=record.away_team_language,
                kickoff_at_utc=record.kickoff_at_utc,
            )
            resolution = identity_resolver.resolve(provider_match)
            bonus = ThreeWayFixedBonus(
                home_win=record.home_win,
                draw=record.draw,
                away_win=record.away_win,
            )
            payload_hash = canonical_payload_sha256(bonus)
            source_snapshot_key = _source_snapshot_key(
                document,
                record,
                provider_code,
                artifact_hash,
            )
            snapshot = SportteryBonusSnapshot(
                snapshot_id=stable_id(
                    "sporttery-manual-snapshot",
                    provider_code,
                    source_snapshot_key,
                    payload_hash,
                ),
                match_id=resolution.internal_match_id,
                provider_code=provider_code,
                sporttery_match_no=record.sporttery_match_no,
                market=_THREE_WAY_MARKET,
                quotes=tuple(
                    FixedBonusQuote(selection=selection, fixed_bonus=value)
                    for selection, value in bonus.items()
                ),
                sale_status=record.sale_status,
                captured_at_utc=document.captured_at_utc,
                available_at_utc=document.reviewed_at_utc,
                ingested_at_utc=document.reviewed_at_utc,
                source_snapshot_key=source_snapshot_key,
                payload_hash=payload_hash,
            )
            if snapshot.snapshot_id in loaded_snapshot_ids:
                raise SportteryManualArchiveError(
                    "duplicate manual fixed-bonus snapshot"
                )
            loaded_snapshot_ids.add(snapshot.snapshot_id)
            provenance = SportteryManualSnapshotProvenance(
                schema_version=document.schema_version,
                snapshot_id=snapshot.snapshot_id,
                source_snapshot_key=source_snapshot_key,
                archive_snapshot_id=document.snapshot_id,
                provider_code=provider_code,
                sporttery_match_no=record.sporttery_match_no,
                match_number_date=record.match_number_date,
                review_level=document.review_level,
                entered_by=document.entered_by,
                reviewed_by=document.reviewed_by,
                captured_at_utc=document.captured_at_utc,
                reviewed_at_utc=document.reviewed_at_utc,
                source_reference=document.source_reference,
                source_artifact_path=document.source_artifact_path,
                source_artifact_sha256=artifact_hash,
            )
            loaded.append(
                _LoadedSnapshot(
                    snapshot=snapshot,
                    mapping=ProviderMatchMapping(
                        mapping_id=stable_id(
                            "provider-mapping",
                            provider_code,
                            "sporttery_match",
                            external_match_id,
                        ),
                        provider_code=provider_code,
                        external_namespace="sporttery_match",
                        external_match_id=external_match_id,
                        internal_match_id=resolution.internal_match_id,
                        resolution_method=resolution.resolution_method,
                        confidence=resolution.confidence,
                        available_at_utc=document.reviewed_at_utc,
                    ),
                    provenance=provenance,
                )
            )
    return _canonicalize_loaded_mappings(tuple(loaded))


def _external_match_id(
    document: SportteryManualDocument,
    record: SportteryManualRecord,
) -> str:
    if document.schema_version == SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1:
        return record.sporttery_match_no
    return _date_qualified_match_id(record)


def _date_qualified_match_id(record: SportteryManualRecord) -> str:
    return f"{record.match_number_date.isoformat()}:{record.sporttery_match_no}"


def _canonicalize_loaded_mappings(
    loaded: tuple[_LoadedSnapshot, ...],
) -> tuple[_LoadedSnapshot, ...]:
    versions_by_match_number: dict[tuple[date, str], str] = {}
    v1_dates_by_match_number: dict[str, set[date]] = {}
    mappings_by_external_id: dict[str, ProviderMatchMapping] = {}

    for item in loaded:
        provenance = item.provenance
        identity = (provenance.match_number_date, provenance.sporttery_match_no)
        previous_version = versions_by_match_number.setdefault(
            identity,
            provenance.schema_version,
        )
        if previous_version != provenance.schema_version:
            raise SportteryManualArchiveError(
                "the same Sporttery match number/date cannot mix V1 and V2 identity"
            )
        if provenance.schema_version == SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1:
            dates = v1_dates_by_match_number.setdefault(
                provenance.sporttery_match_no,
                set(),
            )
            dates.add(provenance.match_number_date)
            if len(dates) != 1:
                raise SportteryManualArchiveError(
                    "V1 Sporttery match numbers cannot be reused across dates"
                )

        mapping = item.mapping
        previous = mappings_by_external_id.get(mapping.external_match_id)
        if previous is None:
            mappings_by_external_id[mapping.external_match_id] = mapping
            continue
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
            getattr(previous, field) != getattr(mapping, field)
            for field in immutable_fields
        ):
            raise SportteryManualArchiveError(
                "Sporttery correction conflicts with its immutable match mapping"
            )
        if mapping.available_at_utc < previous.available_at_utc:
            mappings_by_external_id[mapping.external_match_id] = mapping

    return tuple(
        replace(
            item,
            mapping=item.mapping.model_copy(
                update={
                    "available_at_utc": mappings_by_external_id[
                        item.mapping.external_match_id
                    ].available_at_utc
                }
            ),
        )
        for item in loaded
    )


def _source_snapshot_key(
    document: SportteryManualDocument,
    record: SportteryManualRecord,
    provider_code: str,
    artifact_hash: str,
) -> str:
    if document.schema_version == SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1:
        return stable_id(
            "sporttery-manual-source",
            document.snapshot_id,
            record.sporttery_match_no,
            record.match_number_date.isoformat(),
            artifact_hash,
        )
    return stable_id(
        "sporttery-manual-source",
        canonical_payload_sha256(
            {
                "schema_version": document.schema_version,
                "archive_snapshot_id": document.snapshot_id,
                "provider_code": provider_code,
                "sporttery_match_no": record.sporttery_match_no,
                "match_number_date": record.match_number_date.isoformat(),
                "captured_at_utc": document.captured_at_utc,
                "source_reference": document.source_reference,
                "source_artifact_path": document.source_artifact_path,
                "source_artifact_sha256": artifact_hash,
                "entered_by": document.entered_by,
                "review_level": document.review_level,
                "reviewed_by": document.reviewed_by,
                "reviewed_at_utc": document.reviewed_at_utc,
            }
        ),
    )


def _document_paths(archive_source: str | Path) -> tuple[Path, ...]:
    path = Path(archive_source)
    if path.is_file():
        if path.suffix.casefold() not in {".json", ".csv"}:
            raise SportteryManualArchiveError("manual archive must be JSON or CSV")
        return (path.resolve(),)
    if not path.is_dir():
        raise FileNotFoundError(
            f"manual Sporttery archive source does not exist: {path}"
        )
    paths = tuple(
        sorted(
            (
                *path.glob("*.json"),
                *path.glob("*.csv"),
            ),
            key=lambda item: item.name,
        )
    )
    if not paths:
        raise SportteryManualArchiveError(
            "manual archive directory contains no JSON or CSV"
        )
    return tuple(item.resolve() for item in paths)


def _read_document(path: Path) -> SportteryManualDocument:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise SportteryManualArchiveError(
            "manual archive must be readable UTF-8"
        ) from None
    try:
        if path.suffix.casefold() == ".json":
            payload = json.loads(
                text,
                object_pairs_hook=_no_duplicate_json_keys,
                parse_constant=_reject_json_constant,
            )
        else:
            payload = _csv_document(text)
        return SportteryManualDocument.model_validate(payload)
    except (csv.Error, json.JSONDecodeError, ValueError):
        raise SportteryManualArchiveError("manual archive schema is invalid") from None


def _csv_document(text: str) -> dict[str, object]:
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if reader.fieldnames is None:
        raise SportteryManualArchiveError("manual CSV requires a header")
    headers = tuple(reader.fieldnames)
    if len(headers) != len(set(headers)) or set(headers) not in {
        frozenset(_CSV_COLUMNS),
        frozenset(_V1_CSV_COLUMNS),
    }:
        raise SportteryManualArchiveError("manual CSV columns do not match the schema")
    rows = list(reader)
    if not rows:
        raise SportteryManualArchiveError("manual CSV requires at least one record")
    normalized_rows: list[dict[str, object]] = []
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise SportteryManualArchiveError(
                "manual CSV row has the wrong column count"
            )
        normalized_rows.append(
            {name: None if value == r"\N" else value for name, value in row.items()}
        )
    document_columns = (
        _DOCUMENT_COLUMNS if "review_level" in headers else _V1_DOCUMENT_COLUMNS
    )
    provenance = {name: normalized_rows[0][name] for name in document_columns}
    if any(
        any(row[name] != provenance[name] for name in document_columns)
        for row in normalized_rows[1:]
    ):
        raise SportteryManualArchiveError(
            "manual CSV provenance must be identical on every row"
        )
    schema_version = provenance["schema_version"]
    expected_schema = (
        SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION
        if "review_level" in headers
        else SPORTTERY_MANUAL_ARCHIVE_SCHEMA_V1
    )
    if schema_version != expected_schema:
        raise SportteryManualArchiveError(
            "manual CSV columns do not match the schema version"
        )
    return {
        **provenance,
        "records": [
            {name: row[name] for name in _RECORD_COLUMNS} for row in normalized_rows
        ],
    }


def _no_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON number")


def _verified_artifact_hash(path: Path, document: SportteryManualDocument) -> str:
    base = path.parent.resolve()
    requested = Path(document.source_artifact_path)
    if requested.is_absolute():
        raise SportteryManualArchiveError(
            "manual source artifact path must be relative"
        )
    unresolved_artifact = base / requested
    if unresolved_artifact.is_symlink():
        raise SportteryManualArchiveError("manual source artifact path is invalid")
    artifact = unresolved_artifact.resolve()
    if (
        not artifact.is_relative_to(base)
        or artifact == path.resolve()
        or not artifact.is_file()
    ):
        raise SportteryManualArchiveError("manual source artifact path is invalid")
    try:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    except OSError:
        raise SportteryManualArchiveError(
            "manual source artifact cannot be read"
        ) from None
    if not hmac.compare_digest(digest, document.source_artifact_sha256):
        raise SportteryManualArchiveError(
            "manual source artifact SHA-256 does not match"
        )
    return digest


def _snapshot_is_visible(
    snapshot: SportteryBonusSnapshot,
    query: SnapshotQuery,
) -> bool:
    return all(
        timestamp <= query.as_of_at_utc
        for timestamp in (
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
    )


def _snapshot_version(snapshot: SportteryBonusSnapshot) -> tuple[object, ...]:
    return (
        snapshot.available_at_utc,
        snapshot.captured_at_utc,
        snapshot.ingested_at_utc,
        snapshot.snapshot_id,
    )
