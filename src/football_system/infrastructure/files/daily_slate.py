from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.daily_slate import (
    DailySlateCandidate,
    DailySlatePlan,
    DailySlateProvenance,
    DailySlateReviewLevel,
    SportteryDailySlate,
)
from football_system.domain.market import ThreeWayFixedBonus
from football_system.infrastructure.files.review_bridge import read_contract_file
from football_system.infrastructure.providers.real.sporttery_manual import (
    SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION,
    load_verified_sporttery_manual_documents,
)


SPORTTERY_DAILY_SLATE_INPUT_SCHEMA_VERSION = "SPORTTERY_DAILY_SLATE_INPUT_V1"
_DECIMAL_STRING = re.compile(r"^\d+(?:\.\d+)?$")


class DailySlateFileError(ValueError):
    code = "DAILY_SLATE_FILE_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


class ReviewedDailySlateThreeWaySp(DomainModel):
    home_win: Decimal
    draw: Decimal
    away_win: Decimal

    @field_validator("home_win", "draw", "away_win", mode="before")
    @classmethod
    def validate_decimal_string(cls, value: object) -> Decimal:
        if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value.strip()):
            raise ValueError("daily slate SP values must be decimal strings")
        try:
            decimal = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("daily slate SP values must be decimal strings") from None
        if not decimal.is_finite() or decimal <= 1:
            raise ValueError("daily slate SP values must be finite and above one")
        return decimal

    def to_bonus(self) -> ThreeWayFixedBonus:
        return ThreeWayFixedBonus.model_validate(self.model_dump(mode="python"))


class ReviewedDailySlateCandidate(DomainModel):
    sporttery_match_no: Identifier
    match_date: date
    kickoff_at_utc: UtcDateTime
    home_label: Identifier
    away_label: Identifier
    competition_label: Identifier
    three_way_sp: ReviewedDailySlateThreeWaySp | None = None

    @field_validator("match_date", mode="before")
    @classmethod
    def validate_match_date(cls, value: object) -> date:
        if not isinstance(value, str):
            raise ValueError("daily slate match_date must be an ISO date string")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(
                "daily slate match_date must be an ISO date string"
            ) from None

    @model_validator(mode="after")
    def validate_teams(self) -> Self:
        if self.home_label == self.away_label:
            raise ValueError("daily slate home and away labels must differ")
        return self


class ReviewedDailySlateDocument(DomainModel):
    schema_version: Literal["SPORTTERY_DAILY_SLATE_INPUT_V1"]
    snapshot_id: Identifier
    captured_at_utc: UtcDateTime
    source_reference: str = Field(min_length=1, max_length=2048)
    source_artifact_path: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entered_by: Identifier
    review_level: DailySlateReviewLevel
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime
    candidates: tuple[ReviewedDailySlateCandidate, ...] = ()

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.reviewed_at_utc < self.captured_at_utc:
            raise ValueError("daily slate review cannot predate capture")
        same_reviewer = self.entered_by.casefold() == self.reviewed_by.casefold()
        if self.review_level is DailySlateReviewLevel.SELF_REVIEWED and not same_reviewer:
            raise ValueError("self-reviewed daily slate requires the same reviewer")
        if (
            self.review_level is DailySlateReviewLevel.INDEPENDENT_REVIEWED
            and same_reviewer
        ):
            raise ValueError(
                "independently reviewed daily slate requires a distinct reviewer"
            )
        identities = tuple(
            (item.match_date, item.sporttery_match_no) for item in self.candidates
        )
        if len(identities) != len(set(identities)):
            raise ValueError("daily slate match number/date identities must be unique")
        return self


def load_sporttery_daily_slate(path: str | Path) -> SportteryDailySlate:
    source_path = Path(path)
    if not source_path.is_file():
        raise DailySlateFileError(f"daily slate input does not exist: {source_path}")
    try:
        if source_path.suffix.casefold() == ".csv":
            return _from_manual_archive(source_path)
        payload, raw = _read_json_object(source_path)
        schema_version = payload.get("schema_version")
        if schema_version == SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION:
            return _from_manual_archive(source_path)
        if schema_version != SPORTTERY_DAILY_SLATE_INPUT_SCHEMA_VERSION:
            raise DailySlateFileError("daily slate input schema is unsupported")
        document = ReviewedDailySlateDocument.model_validate(payload)
        artifact_hash = _verified_relative_artifact(source_path, document)
        provenance = DailySlateProvenance(
            source_schema_version=document.schema_version,
            source_document_id=document.snapshot_id,
            source_document_sha256=hashlib.sha256(raw).hexdigest(),
            source_reference=document.source_reference,
            source_artifact_path=document.source_artifact_path,
            source_artifact_sha256=artifact_hash,
            entered_by=document.entered_by,
            review_level=document.review_level,
            reviewed_by=document.reviewed_by,
            captured_at_utc=document.captured_at_utc,
            reviewed_at_utc=document.reviewed_at_utc,
        )
        candidates = tuple(
            DailySlateCandidate.freeze(
                sporttery_match_no=item.sporttery_match_no,
                match_date=item.match_date,
                kickoff_at_utc=item.kickoff_at_utc,
                home_label=item.home_label,
                away_label=item.away_label,
                competition_label=item.competition_label,
                three_way_sp=(
                    item.three_way_sp.to_bonus()
                    if item.three_way_sp is not None
                    else None
                ),
                source_reference=document.source_reference,
                captured_at_utc=document.captured_at_utc,
                reviewed_at_utc=document.reviewed_at_utc,
                provenance=provenance,
            )
            for item in document.candidates
        )
        return SportteryDailySlate.freeze(
            candidates=candidates,
            provenance=provenance,
        )
    except DailySlateFileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise DailySlateFileError("reviewed daily slate input is invalid") from None


def load_daily_slate_plan(path: str | Path) -> DailySlatePlan:
    plan_path = Path(path)
    if not plan_path.is_file():
        raise DailySlateFileError(f"daily slate plan does not exist: {plan_path}")
    try:
        payload, _ = _read_json_object(plan_path)
        return DailySlatePlan.model_validate(payload)
    except DailySlateFileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise DailySlateFileError("daily slate plan is invalid") from None


def _from_manual_archive(path: Path) -> SportteryDailySlate:
    verified = load_verified_sporttery_manual_documents(path)
    if len(verified) != 1:
        raise DailySlateFileError("daily slate planning requires one manual snapshot")
    source = verified[0]
    document = source.document
    if document.schema_version != SPORTTERY_MANUAL_ARCHIVE_SCHEMA_VERSION:
        raise DailySlateFileError(
            "daily slate planning requires SPORTTERY_MANUAL_ARCHIVE_V2"
        )
    provenance = DailySlateProvenance(
        source_schema_version=document.schema_version,
        source_document_id=document.snapshot_id,
        source_document_sha256=source.document_sha256,
        source_reference=document.source_reference,
        source_artifact_path=document.source_artifact_path,
        source_artifact_sha256=source.source_artifact_sha256,
        entered_by=document.entered_by,
        review_level=DailySlateReviewLevel(document.review_level.value),
        reviewed_by=document.reviewed_by,
        captured_at_utc=document.captured_at_utc,
        reviewed_at_utc=document.reviewed_at_utc,
    )
    candidates = tuple(
        DailySlateCandidate.freeze(
            sporttery_match_no=record.sporttery_match_no,
            match_date=record.match_number_date,
            kickoff_at_utc=record.kickoff_at_utc,
            home_label=record.provider_home_team_name,
            away_label=record.provider_away_team_name,
            competition_label=record.provider_competition_name,
            three_way_sp=ThreeWayFixedBonus(
                home_win=record.home_win,
                draw=record.draw,
                away_win=record.away_win,
            ),
            source_reference=document.source_reference,
            captured_at_utc=document.captured_at_utc,
            reviewed_at_utc=document.reviewed_at_utc,
            provenance=provenance,
        )
        for record in document.records
    )
    return SportteryDailySlate.freeze(candidates=candidates, provenance=provenance)


def _read_json_object(path: Path) -> tuple[dict[str, object], bytes]:
    raw = read_contract_file(path)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise DailySlateFileError("daily slate JSON must be strict UTF-8") from None
    if not isinstance(payload, dict):
        raise DailySlateFileError("daily slate JSON must contain one object")
    return payload, raw


def _verified_relative_artifact(
    document_path: Path,
    document: ReviewedDailySlateDocument,
) -> str:
    base = document_path.parent.resolve()
    requested = Path(document.source_artifact_path)
    if requested.is_absolute():
        raise DailySlateFileError("daily slate source artifact path must be relative")
    unresolved = base / requested
    if unresolved.is_symlink():
        raise DailySlateFileError("daily slate source artifact path is invalid")
    artifact = unresolved.resolve()
    if (
        not artifact.is_relative_to(base)
        or artifact == document_path.resolve()
        or not artifact.is_file()
    ):
        raise DailySlateFileError("daily slate source artifact path is invalid")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if not hmac.compare_digest(digest, document.source_artifact_sha256):
        raise DailySlateFileError("daily slate source artifact SHA-256 does not match")
    return digest


def _no_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON number")
