"""Closed real bridge requests; trusted event timestamps are never caller fields."""

from typing import Literal
from pydantic import Field, model_validator
from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_v2 import Hash, MultiMarketModel
from football_system.domain.prospective import CorrectionReasonV1, ManualResultImportV1
from football_system.domain.real_bridge import Provenance
from football_system.domain.services.elo_baseline import EloBaselineState
from football_system.domain.strategy_pass_v2 import Money, TicketRequestV2


class Request(MultiMarketModel):
    request_key: str = Field(min_length=1, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def canonical_sets(cls,value):
        if isinstance(value,dict):
            value=dict(value)
            for name in ("competition_ids","scope_match_ids","match_ids","market_binding_ids","sp_binding_ids","evidence_binding_ids","allowed_budgets_fen"):
                if name in value and isinstance(value[name],(tuple,list)):
                    if len(value[name])!=len(set(value[name])):
                        raise ValueError("DUPLICATE_REAL_REQUEST_MEMBER")
                    value[name]=tuple(sorted(value[name]))
        return value


class ProgramRequest(Request):
    observation_program_id: Identifier
    competition_ids: tuple[Identifier, ...]
    season_id: Identifier
    scope_reference: str = Field(min_length=1, max_length=2000)
    provenance: Provenance


class AdmissionRequest(Request):
    program_id: Identifier
    kind: Literal["MARKET", "SPORTTERY", "MODEL", "EVIDENCE", "RESULT"]
    source_identity: Identifier
    state: Literal["ADMITTED", "REVOKED"]
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    rights_reference: str = Field(min_length=1, max_length=2000)
    verified_by: Identifier
    evidence_file: str
    evidence_hash: Hash
    credential_verified: bool = False
    previous_id: Identifier | None = None


class ModelPinRequest(Request):
    program_id: Identifier
    admission_id: Identifier
    source_identity: Identifier
    release_id: Identifier | None = None
    model_state_id: Identifier | None = None
    source_analysis_id: Identifier | None = None
    scope_match_ids: tuple[Identifier, ...]
    test_state: EloBaselineState | None = None


class PolicyRequest(Request):
    program_id: Identifier
    allowed_budgets_fen: tuple[Money, ...] = (0, 10000)
    result_source_identity: Identifier
    maximum_odds_age_seconds: int = Field(gt=0, strict=True)
    minimum_bookmaker_count: int = Field(gt=0, strict=True)


class AnchorRequest(Request):
    program_id: Identifier
    policy_id: Identifier
    model_pin_id: Identifier
    starts_at_utc: UtcDateTime
    ends_at_utc: UtcDateTime


class EpochRequest(Request):
    anchor_id: Identifier
    previous_epoch_id: Identifier | None = None


class SlateRequest(Request):
    epoch_id: Identifier
    match_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    source_reference: str = Field(min_length=1, max_length=2000)
    source_file: str
    source_hash: Hash


class BucketRequest(Request):
    slate_id: Identifier
    kickoff_at_utc: UtcDateTime


class SourceRequest(Request):
    program_id: Identifier
    admission_id: Identifier
    ingestion_id: Identifier
    snapshot_id: Identifier


class RealProspectivePrepareV1(Request):
    schema_version: Literal["REAL_PROSPECTIVE_PREPARE_V1"] = "REAL_PROSPECTIVE_PREPARE_V1"
    epoch_id: Identifier
    bucket_id: Identifier
    market_binding_ids: tuple[Identifier, ...] = Field(max_length=64)
    sp_binding_ids: tuple[Identifier, ...] = Field(max_length=64)
    evidence_binding_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    budget_fen: Money
    supersedes_lock_id: Identifier | None = None


class ReplaceRequest(RealProspectivePrepareV1):
    expected_head_id: Identifier
    reason: str = Field(min_length=1, max_length=2000)


class LockRequest(Request):
    run_id: Identifier
    raw_review: str | None = Field(default=None, max_length=4*1024*1024)
    reasons: tuple[CorrectionReasonV1, ...] | None = None
    strategy_requests: tuple[TicketRequestV2, ...] = Field(default=(), max_length=4096)
    invalidation_reason: str | None = Field(default=None, min_length=1, max_length=2000)


class ModelInvalidationRequest(Request):
    pin_id: Identifier
    reason: str = Field(min_length=1, max_length=2000)
    evidence_file: str
    evidence_hash: Hash


class SourceInvalidationRequest(Request):
    source_id: Identifier
    reason: str = Field(min_length=1,max_length=2000)
    evidence_file: str
    evidence_hash: Hash


class ResultRequest(Request):
    program_id: Identifier
    admission_id: Identifier
    result: ManualResultImportV1


class SettleRequest(Request):
    run_id: Identifier


class ReportRequest(Request):
    epoch_id: Identifier
    as_of_at_utc: UtcDateTime


class CloseRequest(Request):
    epoch_id: Identifier


REQUEST_TYPES = {"program": ProgramRequest, "admission": AdmissionRequest, "model-pin": ModelPinRequest,
    "policy": PolicyRequest, "anchor": AnchorRequest, "epoch": EpochRequest, "slate": SlateRequest,
    "bucket": BucketRequest, "market-bind": SourceRequest, "sp-bind": SourceRequest,
    "prepare": RealProspectivePrepareV1, "replace": ReplaceRequest, "lock": LockRequest,
    "model-invalidate": ModelInvalidationRequest, "source-invalidate": SourceInvalidationRequest, "result-import": ResultRequest, "settle": SettleRequest,
    "report": ReportRequest, "epoch-close": CloseRequest}
