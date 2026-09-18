"""Closed local command requests. Trusted event times are deliberately absent."""

from datetime import date
from typing import Literal

from pydantic import Field

from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_v2 import Hash, MultiMarketModel
from football_system.domain.prospective import CorrectionReasonV1, ManualResultImportV1
from football_system.domain.prospective_evidence import ManualVerifiedImportV1
from football_system.domain.strategy_pass_v2 import Money, TicketRequestV2


class ProspectiveRequestV1(MultiMarketModel):
    request_key: str = Field(min_length=1, max_length=100)


class EpochRequestV1(ProspectiveRequestV1):
    seed_plan_id: Identifier
    name: Identifier
    mode: Literal["SYNTHETIC", "REAL_PROSPECTIVE"]
    starts_at_utc: UtcDateTime
    ends_at_utc: UtcDateTime
    result_source_identity: Identifier
    previous_epoch_id: Identifier | None = None


class EvidenceImportRequestV1(ProspectiveRequestV1):
    evidence: ManualVerifiedImportV1


class PrepareProspectiveRequestV1(ProspectiveRequestV1):
    epoch_id: Identifier
    analysis_id: Identifier
    slate_key: Identifier
    slate_date: date
    budget_fen: Money
    evidence_binding_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    supersedes_run_id: Identifier | None = None


class ReviewAuditRequestV1(ProspectiveRequestV1):
    run_id: Identifier
    review_id: Identifier
    reasons: tuple[CorrectionReasonV1, ...] = Field(min_length=1, max_length=64)


class LockProspectiveRequestV1(ProspectiveRequestV1):
    run_id: Identifier
    correction_audit_id: Identifier
    optimizer_run_id: Identifier
    invalidation_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    workflow_hash: Hash | None = None


class LockWorkflowRequestV1(ProspectiveRequestV1):
    run_id: Identifier
    strategy_requests: tuple[TicketRequestV2, ...] = Field(default=(), max_length=4096)
    invalidation_reason: str | None = Field(default=None, min_length=1, max_length=2000)


class ResultImportRequestV1(ProspectiveRequestV1):
    result: ManualResultImportV1


class SettleProspectiveRequestV1(ProspectiveRequestV1):
    run_id: Identifier


class ReportProspectiveRequestV1(ProspectiveRequestV1):
    epoch_id: Identifier
    as_of_at_utc: UtcDateTime


class CloseEpochRequestV1(ProspectiveRequestV1):
    epoch_id: Identifier
