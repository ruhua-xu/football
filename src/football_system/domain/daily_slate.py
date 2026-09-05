from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from football_system.domain.archive import canonical_payload_sha256
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.market import ThreeWayFixedBonus


SPORTTERY_DAILY_SLATE_SCHEMA_VERSION = "SPORTTERY_DAILY_SLATE_V1"
DAILY_SLATE_PLAN_SCHEMA_VERSION = "DAILY_SLATE_PLAN_V1"


class DailySlateReviewLevel(StrEnum):
    SELF_REVIEWED = "SELF_REVIEWED"
    INDEPENDENT_REVIEWED = "INDEPENDENT_REVIEWED"


class DailySlateStatus(StrEnum):
    CANDIDATES_AVAILABLE = "CANDIDATES_AVAILABLE"
    NO_SPORTTERY_CANDIDATES = "NO_SPORTTERY_CANDIDATES"


class DailySlateCandidateStatus(StrEnum):
    IDENTITY_RESOLVED = "IDENTITY_RESOLVED"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    FIXTURE_SOURCE_REQUIRED = "FIXTURE_SOURCE_REQUIRED"
    MARKET_ODDS_REQUIRED = "MARKET_ODDS_REQUIRED"
    SPORTTERY_SP_READY = "SPORTTERY_SP_READY"
    READY_FOR_CAPTURE = "READY_FOR_CAPTURE"


class DailySlateCaptureKind(StrEnum):
    FIXTURE_SOURCE = "FIXTURE_SOURCE"
    MARKET_ODDS = "MARKET_ODDS"


class DailySlateProvenance(DomainModel):
    source_schema_version: Identifier
    source_document_id: Identifier
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_reference: str = Field(min_length=1, max_length=2048)
    source_artifact_path: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entered_by: Identifier
    review_level: DailySlateReviewLevel
    reviewed_by: Identifier
    captured_at_utc: UtcDateTime
    reviewed_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_review(self) -> Self:
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
        return self


class DailySlateCandidate(DomainModel):
    candidate_id: Identifier
    sporttery_match_no: Identifier
    match_date: date
    kickoff_at_utc: UtcDateTime
    home_label: Identifier
    away_label: Identifier
    competition_label: Identifier
    three_way_sp: ThreeWayFixedBonus | None = None
    source_reference: str = Field(min_length=1, max_length=2048)
    captured_at_utc: UtcDateTime
    reviewed_at_utc: UtcDateTime
    provenance: DailySlateProvenance
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> DailySlateCandidate:
        digest = _payload_hash(values)
        return cls.model_validate(
            {
                **values,
                "candidate_id": stable_id("daily-slate-candidate", digest),
                "candidate_hash": digest,
            }
        )

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if self.home_label == self.away_label:
            raise ValueError("daily slate home and away labels must differ")
        if (
            self.source_reference != self.provenance.source_reference
            or self.captured_at_utc != self.provenance.captured_at_utc
            or self.reviewed_at_utc != self.provenance.reviewed_at_utc
        ):
            raise ValueError("daily slate candidate provenance is inconsistent")
        digest = _model_hash(self, "candidate_id", "candidate_hash")
        if digest != self.candidate_hash:
            raise ValueError("daily slate candidate hash is inconsistent")
        if self.candidate_id != stable_id("daily-slate-candidate", digest):
            raise ValueError("daily slate candidate ID is inconsistent")
        return self

    @property
    def external_match_id(self) -> str:
        return f"{self.match_date.isoformat()}:{self.sporttery_match_no}"


class SportteryDailySlate(DomainModel):
    schema_version: Literal["SPORTTERY_DAILY_SLATE_V1"] = (
        SPORTTERY_DAILY_SLATE_SCHEMA_VERSION
    )
    slate_id: Identifier
    status: DailySlateStatus
    candidates: tuple[DailySlateCandidate, ...]
    provenance: DailySlateProvenance
    slate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(
        cls,
        *,
        candidates: tuple[DailySlateCandidate, ...],
        provenance: DailySlateProvenance,
    ) -> SportteryDailySlate:
        ordered = tuple(sorted(candidates, key=_candidate_key))
        status = (
            DailySlateStatus.CANDIDATES_AVAILABLE
            if ordered
            else DailySlateStatus.NO_SPORTTERY_CANDIDATES
        )
        values = {
            "schema_version": SPORTTERY_DAILY_SLATE_SCHEMA_VERSION,
            "status": status,
            "candidates": ordered,
            "provenance": provenance,
        }
        digest = _payload_hash(values)
        return cls.model_validate(
            {
                **values,
                "slate_id": stable_id("sporttery-daily-slate", digest),
                "slate_hash": digest,
            }
        )

    @model_validator(mode="after")
    def validate_slate(self) -> Self:
        expected_status = (
            DailySlateStatus.CANDIDATES_AVAILABLE
            if self.candidates
            else DailySlateStatus.NO_SPORTTERY_CANDIDATES
        )
        if self.status is not expected_status:
            raise ValueError("daily slate status conflicts with candidate coverage")
        if self.candidates != tuple(sorted(self.candidates, key=_candidate_key)):
            raise ValueError("daily slate candidates must be canonically sorted")
        identities = tuple(
            (item.match_date, item.sporttery_match_no) for item in self.candidates
        )
        if len(identities) != len(set(identities)):
            raise ValueError("daily slate match number/date identities must be unique")
        if any(item.provenance != self.provenance for item in self.candidates):
            raise ValueError("daily slate candidates must share source provenance")
        digest = _model_hash(self, "slate_id", "slate_hash")
        if digest != self.slate_hash:
            raise ValueError("daily slate hash is inconsistent")
        if self.slate_id != stable_id("sporttery-daily-slate", digest):
            raise ValueError("daily slate ID is inconsistent")
        return self


class DailySlateCandidatePlan(DomainModel):
    candidate: DailySlateCandidate
    statuses: tuple[DailySlateCandidateStatus, ...]
    canonical_match_id: Identifier | None = None
    resolution_method: Identifier | None = None
    canonical_candidate_ids: tuple[Identifier, ...] = ()
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> DailySlateCandidatePlan:
        digest = _payload_hash(values)
        return cls.model_validate({**values, "result_hash": digest})

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.statuses != _ordered_statuses(self.statuses):
            raise ValueError("daily slate candidate statuses must be unique and ordered")
        resolved = DailySlateCandidateStatus.IDENTITY_RESOLVED in self.statuses
        unresolved = DailySlateCandidateStatus.IDENTITY_UNRESOLVED in self.statuses
        if resolved == unresolved:
            raise ValueError("daily slate candidate requires one identity status")
        if resolved != (
            self.canonical_match_id is not None and self.resolution_method is not None
        ):
            raise ValueError("daily slate identity fields conflict with its status")
        if self.canonical_candidate_ids != tuple(
            sorted(set(self.canonical_candidate_ids))
        ):
            raise ValueError("canonical candidate IDs must be unique and sorted")
        if resolved and self.canonical_candidate_ids != (self.canonical_match_id,):
            raise ValueError("resolved slate identity must name its canonical match")
        fixture_required = (
            DailySlateCandidateStatus.FIXTURE_SOURCE_REQUIRED in self.statuses
        )
        if fixture_required != (unresolved and not self.canonical_candidate_ids):
            raise ValueError("fixture-source status conflicts with identity evidence")
        market_required = DailySlateCandidateStatus.MARKET_ODDS_REQUIRED in self.statuses
        if market_required != resolved:
            raise ValueError("market-odds status requires a resolved identity")
        sp_ready = DailySlateCandidateStatus.SPORTTERY_SP_READY in self.statuses
        if sp_ready != (self.candidate.three_way_sp is not None):
            raise ValueError("Sporttery SP status conflicts with the candidate")
        ready = DailySlateCandidateStatus.READY_FOR_CAPTURE in self.statuses
        if ready != (resolved and sp_ready):
            raise ValueError("capture readiness conflicts with candidate prerequisites")
        if _model_hash(self, "result_hash") != self.result_hash:
            raise ValueError("daily slate candidate result hash is inconsistent")
        return self


class DailySlateReconciliationTask(DomainModel):
    task_id: Identifier
    candidate_id: Identifier
    provider_code: Identifier
    external_namespace: Identifier
    external_match_id: Identifier
    canonical_candidate_ids: tuple[Identifier, ...]
    fixture_source_required: bool
    task_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> DailySlateReconciliationTask:
        digest = _payload_hash(values)
        return cls.model_validate(
            {
                **values,
                "task_id": stable_id("daily-slate-reconciliation", digest),
                "task_hash": digest,
            }
        )

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        if self.canonical_candidate_ids != tuple(
            sorted(set(self.canonical_candidate_ids))
        ):
            raise ValueError("reconciliation candidates must be unique and sorted")
        if self.fixture_source_required != (not self.canonical_candidate_ids):
            raise ValueError("reconciliation fixture requirement is inconsistent")
        digest = _model_hash(self, "task_id", "task_hash")
        if digest != self.task_hash:
            raise ValueError("daily slate reconciliation hash is inconsistent")
        if self.task_id != stable_id("daily-slate-reconciliation", digest):
            raise ValueError("daily slate reconciliation ID is inconsistent")
        return self


class DailySlateCaptureRequest(DomainModel):
    request_id: Identifier
    kind: DailySlateCaptureKind
    competition_label: Identifier
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    candidate_ids: tuple[Identifier, ...] = Field(min_length=1)
    canonical_match_ids: tuple[Identifier, ...] = ()
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> DailySlateCaptureRequest:
        digest = _payload_hash(values)
        return cls.model_validate(
            {
                **values,
                "request_id": stable_id("daily-slate-capture-request", digest),
                "request_hash": digest,
            }
        )

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("daily slate capture window is invalid")
        if self.candidate_ids != tuple(sorted(set(self.candidate_ids))):
            raise ValueError("capture request candidate IDs must be unique and sorted")
        if self.canonical_match_ids != tuple(sorted(set(self.canonical_match_ids))):
            raise ValueError("capture request match IDs must be unique and sorted")
        if self.kind is DailySlateCaptureKind.FIXTURE_SOURCE:
            if self.canonical_match_ids:
                raise ValueError("fixture-source request cannot invent canonical matches")
        elif len(self.canonical_match_ids) != len(self.candidate_ids):
            raise ValueError("market-odds request requires one match per candidate")
        digest = _model_hash(self, "request_id", "request_hash")
        if digest != self.request_hash:
            raise ValueError("daily slate capture request hash is inconsistent")
        if self.request_id != stable_id("daily-slate-capture-request", digest):
            raise ValueError("daily slate capture request ID is inconsistent")
        return self


class DailySlateCapturePlan(DomainModel):
    requests: tuple[DailySlateCaptureRequest, ...]
    sporttery_ingestion_candidate_ids: tuple[Identifier, ...]
    reconciliation_task_ids: tuple[Identifier, ...]
    ready_match_ids: tuple[Identifier, ...]
    capture_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> DailySlateCapturePlan:
        digest = _payload_hash(values)
        return cls.model_validate({**values, "capture_plan_hash": digest})

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if self.requests != tuple(sorted(self.requests, key=_capture_request_key)):
            raise ValueError("daily slate capture requests must be canonically sorted")
        request_ids = tuple(item.request_id for item in self.requests)
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("daily slate capture request IDs must be unique")
        request_scopes = tuple(
            (item.kind, item.competition_label) for item in self.requests
        )
        if len(request_scopes) != len(set(request_scopes)):
            raise ValueError("daily slate capture request scopes must be unique")
        for values in (
            self.sporttery_ingestion_candidate_ids,
            self.reconciliation_task_ids,
            self.ready_match_ids,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("daily slate capture references must be unique and sorted")
        if _model_hash(self, "capture_plan_hash") != self.capture_plan_hash:
            raise ValueError("daily slate capture plan hash is inconsistent")
        return self

    @property
    def market_odds_requests(self) -> tuple[DailySlateCaptureRequest, ...]:
        return tuple(
            item for item in self.requests if item.kind is DailySlateCaptureKind.MARKET_ODDS
        )


class DailySlatePlan(DomainModel):
    schema_version: Literal["DAILY_SLATE_PLAN_V1"] = DAILY_SLATE_PLAN_SCHEMA_VERSION
    plan_id: Identifier
    status: DailySlateStatus
    analysis_status: Literal["NO_ANALYSIS"] = "NO_ANALYSIS"
    planned_at_utc: UtcDateTime
    source_slate_id: Identifier
    source_slate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: tuple[DailySlateCandidatePlan, ...]
    reconciliation_tasks: tuple[DailySlateReconciliationTask, ...]
    capture_plan: DailySlateCapturePlan
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> DailySlatePlan:
        digest = _payload_hash(values)
        return cls.model_validate(
            {
                **values,
                "plan_id": stable_id("daily-slate-plan", digest),
                "plan_hash": digest,
            }
        )

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        expected_status = (
            DailySlateStatus.CANDIDATES_AVAILABLE
            if self.candidates
            else DailySlateStatus.NO_SPORTTERY_CANDIDATES
        )
        if self.status is not expected_status:
            raise ValueError("daily slate plan status conflicts with candidate coverage")
        if self.candidates != tuple(
            sorted(self.candidates, key=lambda item: _candidate_key(item.candidate))
        ):
            raise ValueError("daily slate plan candidates must be canonically sorted")
        candidate_plans = {
            item.candidate.candidate_id: item for item in self.candidates
        }
        if len(candidate_plans) != len(self.candidates):
            raise ValueError("daily slate plan candidate IDs must be unique")
        candidate_ids = set(candidate_plans)
        if self.candidates:
            provenance = self.candidates[0].candidate.provenance
            if self.planned_at_utc < provenance.reviewed_at_utc:
                raise ValueError("daily slate cannot be planned before source review")
            source_slate = SportteryDailySlate.freeze(
                candidates=tuple(item.candidate for item in self.candidates),
                provenance=provenance,
            )
            if (
                source_slate.slate_id != self.source_slate_id
                or source_slate.slate_hash != self.source_slate_hash
                or source_slate.status is not self.status
            ):
                raise ValueError("daily slate plan source binding is inconsistent")
        if self.reconciliation_tasks != tuple(
            sorted(self.reconciliation_tasks, key=lambda item: item.task_id)
        ):
            raise ValueError("daily slate reconciliation tasks must be sorted")
        tasks = {item.task_id: item for item in self.reconciliation_tasks}
        if len(tasks) != len(self.reconciliation_tasks):
            raise ValueError("daily slate reconciliation task IDs must be unique")
        tasks_by_candidate = {
            item.candidate_id: item for item in self.reconciliation_tasks
        }
        if len(tasks_by_candidate) != len(self.reconciliation_tasks):
            raise ValueError("daily slate requires one task per unresolved candidate")
        unresolved_ids = {
            item.candidate.candidate_id
            for item in self.candidates
            if DailySlateCandidateStatus.IDENTITY_UNRESOLVED in item.statuses
        }
        if set(tasks_by_candidate) != unresolved_ids:
            raise ValueError("daily slate reconciliation tasks do not cover unresolved input")
        for candidate_id, task in tasks_by_candidate.items():
            result = candidate_plans[candidate_id]
            fixture_required = (
                DailySlateCandidateStatus.FIXTURE_SOURCE_REQUIRED in result.statuses
            )
            if (
                task.external_match_id != result.candidate.external_match_id
                or task.canonical_candidate_ids != result.canonical_candidate_ids
                or task.fixture_source_required is not fixture_required
            ):
                raise ValueError("daily slate reconciliation task is inconsistent")
        referenced_candidates = {
            candidate_id
            for request in self.capture_plan.requests
            for candidate_id in request.candidate_ids
        } | set(self.capture_plan.sporttery_ingestion_candidate_ids)
        if not referenced_candidates <= candidate_ids:
            raise ValueError("daily slate capture plan references an unknown candidate")
        if set(self.capture_plan.reconciliation_task_ids) != set(tasks):
            raise ValueError("daily slate capture plan omits reconciliation tasks")
        expected_sporttery_ids = tuple(
            sorted(
                candidate_id
                for candidate_id, result in candidate_plans.items()
                if result.candidate.three_way_sp is not None
            )
        )
        if self.capture_plan.sporttery_ingestion_candidate_ids != expected_sporttery_ids:
            raise ValueError("daily slate capture plan has inconsistent Sporttery work")
        expected_ready_ids = tuple(
            sorted(
                result.canonical_match_id
                for result in self.candidates
                if result.canonical_match_id is not None
                and DailySlateCandidateStatus.READY_FOR_CAPTURE in result.statuses
            )
        )
        if self.capture_plan.ready_match_ids != expected_ready_ids:
            raise ValueError("daily slate capture plan has inconsistent ready matches")
        _validate_capture_requests(
            self.capture_plan,
            candidate_plans,
            DailySlateCaptureKind.FIXTURE_SOURCE,
            DailySlateCandidateStatus.FIXTURE_SOURCE_REQUIRED,
        )
        _validate_capture_requests(
            self.capture_plan,
            candidate_plans,
            DailySlateCaptureKind.MARKET_ODDS,
            DailySlateCandidateStatus.MARKET_ODDS_REQUIRED,
        )
        if not self.candidates and (
            self.reconciliation_tasks
            or self.capture_plan.requests
            or self.capture_plan.sporttery_ingestion_candidate_ids
            or self.capture_plan.ready_match_ids
        ):
            raise ValueError("empty daily slate cannot contain capture work")
        digest = _model_hash(self, "plan_id", "plan_hash")
        if digest != self.plan_hash:
            raise ValueError("daily slate plan hash is inconsistent")
        if self.plan_id != stable_id("daily-slate-plan", digest):
            raise ValueError("daily slate plan ID is inconsistent")
        return self


def _validate_capture_requests(
    capture_plan: DailySlateCapturePlan,
    candidate_plans: dict[str, DailySlateCandidatePlan],
    kind: DailySlateCaptureKind,
    required_status: DailySlateCandidateStatus,
) -> None:
    expected_candidate_ids = tuple(
        sorted(
            candidate_id
            for candidate_id, result in candidate_plans.items()
            if required_status in result.statuses
        )
    )
    requested_candidate_ids: list[str] = []
    for request in capture_plan.requests:
        if request.kind is not kind:
            continue
        results = tuple(candidate_plans[item] for item in request.candidate_ids)
        if any(
            result.candidate.competition_label != request.competition_label
            for result in results
        ):
            raise ValueError("daily slate capture request competition is inconsistent")
        if request.kickoff_from_utc != min(
            result.candidate.kickoff_at_utc for result in results
        ) or request.kickoff_to_utc != max(
            result.candidate.kickoff_at_utc for result in results
        ):
            raise ValueError("daily slate capture request window is inconsistent")
        expected_match_ids = (
            tuple(
                sorted(
                    result.canonical_match_id
                    for result in results
                    if result.canonical_match_id is not None
                )
            )
            if kind is DailySlateCaptureKind.MARKET_ODDS
            else ()
        )
        if request.canonical_match_ids != expected_match_ids:
            raise ValueError("daily slate capture request matches are inconsistent")
        requested_candidate_ids.extend(request.candidate_ids)
    if (
        tuple(sorted(requested_candidate_ids)) != expected_candidate_ids
        or len(requested_candidate_ids) != len(set(requested_candidate_ids))
    ):
        raise ValueError("daily slate capture requests do not cover required candidates")


def _model_hash(model: DomainModel, *excluded: str) -> str:
    return _payload_hash(
        model.model_dump(
            mode="python",
            exclude=set(excluded),
            exclude_computed_fields=True,
        )
    )


def _payload_hash(value: object) -> str:
    return canonical_payload_sha256(_python_payload(value))


def _python_payload(value: object) -> object:
    if isinstance(value, DomainModel):
        return _python_payload(
            value.model_dump(mode="python", exclude_computed_fields=True)
        )
    if isinstance(value, Mapping):
        return {key: _python_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_python_payload(item) for item in value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


def _candidate_key(candidate: DailySlateCandidate) -> tuple[object, ...]:
    return (
        candidate.match_date,
        candidate.sporttery_match_no,
        candidate.kickoff_at_utc,
        candidate.candidate_id,
    )


def _ordered_statuses(
    statuses: tuple[DailySlateCandidateStatus, ...],
) -> tuple[DailySlateCandidateStatus, ...]:
    order = {status: index for index, status in enumerate(DailySlateCandidateStatus)}
    return tuple(sorted(set(statuses), key=order.__getitem__))


def _capture_request_key(request: DailySlateCaptureRequest) -> tuple[object, ...]:
    return (request.kind.value, request.competition_label, request.request_id)
