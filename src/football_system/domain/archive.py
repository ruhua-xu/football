from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market_reconciliation import MarketOddsReconciliationIssue
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    Match,
    ProviderMatchMapping,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.prediction import ManualQuantInput
from football_system.domain.settlement import MatchResult

HISTORICAL_ARCHIVE_SCHEMA_VERSION = "HISTORICAL_ARCHIVE_V1"
RETROSPECTIVE_RESEARCH_LABEL = "RETROSPECTIVE_SOURCE_TIME_RESEARCH"


class HistoricalArchiveDatasetKind(StrEnum):
    FIXTURES = "FIXTURES"
    MARKET_ODDS = "MARKET_ODDS"
    MARKET_ODDS_ISSUES = "MARKET_ODDS_ISSUES"
    SPORTTERY_BONUS = "SPORTTERY_BONUS"
    MANUAL_QUANT = "MANUAL_QUANT"
    MATCH_RESULTS = "MATCH_RESULTS"
    PROVIDER_MAPPINGS = "PROVIDER_MAPPINGS"


class HistoricalDataMode(StrEnum):
    LIVE_STRICT = "LIVE_STRICT"
    SOURCE_TIME_RESEARCH = "SOURCE_TIME_RESEARCH"

    @property
    def is_retrospective(self) -> bool:
        return self is HistoricalDataMode.SOURCE_TIME_RESEARCH

    @property
    def report_label(self) -> str:
        if self.is_retrospective:
            return RETROSPECTIVE_RESEARCH_LABEL
        return self.value


class HistoricalArchiveManifest(DomainModel):
    archive_schema_version: Literal[HISTORICAL_ARCHIVE_SCHEMA_VERSION]
    archive_id: Identifier
    provider_code: Identifier
    dataset_kind: HistoricalArchiveDatasetKind
    created_at_utc: UtcDateTime
    source_reference: str = Field(min_length=1, max_length=2048)
    source_description: str = Field(min_length=1, max_length=4000)
    license_note: str = Field(min_length=1, max_length=2048)
    data_mode: HistoricalDataMode
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=0, strict=True)


class HistoricalArchiveRecord(DomainModel):
    retrospective: bool = Field(strict=True)
    imported_at_utc: UtcDateTime | None = None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_finite_values(self) -> Self:
        if _contains_non_finite(self.payload):
            raise ValueError("archive records cannot contain NaN or Infinity")
        return self


class FixtureArchivePayload(DomainModel):
    competition: Competition
    home_team: Team
    away_team: Team
    match: Match

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.match.competition_id != self.competition.competition_id:
            raise ValueError("fixture competition does not match the match reference")
        if self.match.home_team_id != self.home_team.team_id:
            raise ValueError("fixture home team does not match the match reference")
        if self.match.away_team_id != self.away_team.team_id:
            raise ValueError("fixture away team does not match the match reference")
        return self


class FixtureArchiveRecord(HistoricalArchiveRecord):
    payload: FixtureArchivePayload


class MarketOddsArchiveRecord(HistoricalArchiveRecord):
    payload: MarketOddsSnapshot

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        snapshot = self.payload
        if not (
            snapshot.captured_at_utc
            <= snapshot.available_at_utc
            <= snapshot.ingested_at_utc
        ):
            raise ValueError(
                "market odds timestamps must follow captured, available, ingested"
            )
        _assert_payload_hash(
            snapshot.payload_hash,
            snapshot.three_way_odds(),
            snapshot.snapshot_id,
        )
        return self


class MarketOddsIssueArchivePayload(DomainModel):
    record_kind: Literal["MARKET_ODDS_RECONCILIATION_ISSUE"] = (
        "MARKET_ODDS_RECONCILIATION_ISSUE"
    )
    provider_code: Identifier
    available_at_utc: UtcDateTime
    issue: MarketOddsReconciliationIssue

    @model_validator(mode="after")
    def validate_provider(self) -> Self:
        if self.issue.provider_code != self.provider_code:
            raise ValueError("market odds issue archive provider is inconsistent")
        return self


class MarketOddsIssueArchiveRecord(HistoricalArchiveRecord):
    payload: MarketOddsIssueArchivePayload


class SportteryBonusArchiveRecord(HistoricalArchiveRecord):
    payload: SportteryBonusSnapshot

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        snapshot = self.payload
        if not (
            snapshot.captured_at_utc
            <= snapshot.available_at_utc
            <= snapshot.ingested_at_utc
        ):
            raise ValueError(
                "Sporttery timestamps must follow captured, available, ingested"
            )
        _assert_payload_hash(
            snapshot.payload_hash,
            snapshot.three_way_bonus(),
            snapshot.snapshot_id,
        )
        return self


class ManualQuantArchiveRecord(HistoricalArchiveRecord):
    payload: ManualQuantInput

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        _assert_payload_hash(
            self.payload.payload_hash,
            self.payload.probabilities,
            self.payload.input_id,
        )
        return self


class MatchResultArchiveRecord(HistoricalArchiveRecord):
    payload: MatchResult

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        result = self.payload
        _assert_payload_hash(
            result.payload_hash,
            match_result_payload(result),
            result.match_result_id,
        )
        return self


class ProviderMappingArchiveRecord(HistoricalArchiveRecord):
    payload: ProviderMatchMapping


class HistoricalArchive(DomainModel):
    manifest: HistoricalArchiveManifest
    records: tuple[HistoricalArchiveRecord, ...]

    @model_validator(mode="before")
    @classmethod
    def validate_payload_integrity(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        manifest = value.get("manifest")
        records = value.get("records")
        if not isinstance(manifest, (Mapping, HistoricalArchiveManifest)):
            raise ValueError("archive manifest must be an object")
        if not isinstance(records, (list, tuple)):
            raise ValueError("archive records must be an array")
        if isinstance(manifest, HistoricalArchiveManifest):
            expected_count = manifest.record_count
            expected_hash = manifest.payload_sha256
        else:
            expected_count = manifest.get("record_count")
            expected_hash = manifest.get("payload_sha256")
        if type(expected_count) is not int or expected_count != len(records):
            raise ValueError("archive record_count does not match records")
        if not isinstance(expected_hash, str):
            raise ValueError("archive payload_sha256 must be a string")
        if archive_payload_sha256(records) != expected_hash:
            raise ValueError("archive payload checksum mismatch")
        return value

    @model_validator(mode="after")
    def validate_provenance_mode(self) -> Self:
        retrospective = self.manifest.data_mode.is_retrospective
        for record in self.records:
            if record.retrospective is not retrospective:
                raise ValueError(
                    "record retrospective marker does not match archive data_mode"
                )
            if retrospective and record.imported_at_utc is None:
                raise ValueError("SOURCE_TIME_RESEARCH records require imported_at_utc")
            if not retrospective and record.imported_at_utc is not None:
                raise ValueError(
                    "LIVE_STRICT records cannot carry retrospective import time"
                )
        return self


def canonical_json(value: object) -> str:
    return json.dumps(
        _json_compatible(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def archive_payload_sha256(records: Sequence[object]) -> str:
    normalized: list[object] = []
    for record in records:
        if isinstance(record, Mapping):
            record = HistoricalArchiveRecord.model_validate(record)
        if isinstance(record, HistoricalArchiveRecord):
            record = record.model_dump(mode="json")
        normalized.append(record)
    return canonical_payload_sha256(normalized)


def match_result_payload(result: MatchResult) -> dict[str, int]:
    return {
        "away_goals": result.away_goals,
        "home_goals": result.home_goals,
    }


def match_result_payload_sha256(home_goals: int, away_goals: int) -> str:
    return canonical_payload_sha256(
        {"away_goals": away_goals, "home_goals": home_goals}
    )


def _assert_payload_hash(expected: str, payload: object, source_id: str) -> None:
    if canonical_payload_sha256(payload) != expected:
        raise ValueError(f"source {source_id} payload hash does not match its contents")


def _json_compatible(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical JSON cannot contain NaN or Infinity")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON cannot contain NaN or Infinity")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical JSON datetime must be timezone-aware")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _contains_non_finite(value: object) -> bool:
    if isinstance(value, Decimal):
        return not value.is_finite()
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, BaseModel):
        return _contains_non_finite(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_non_finite(item) for item in value)
    return False


ArchiveDatasetKind = HistoricalArchiveDatasetKind
HistoricalArchiveDocument = HistoricalArchive
