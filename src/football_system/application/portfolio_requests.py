"""Closed Portfolio operations. Clock/probability/price overrides are not request fields."""
from pydantic import Field

from football_system.domain.openfootball_snapshot import Label, SnapshotModel
from football_system.domain.portfolio import (
    PortfolioBudgetReviewV1, SnapshotRequestV1, AssemblyRequestV1, LockRequestV1, SettlementRequestV1,
)
from football_system.domain.strategy_pass_v2 import TicketRequestV2
from football_system.domain.training_admission import LocalReviewEvidenceV1


class BudgetRecordRequest(SnapshotModel):
    request_key: Label
    subject: PortfolioBudgetReviewV1
    review: LocalReviewEvidenceV1
    authority: LocalReviewEvidenceV1


class TicketBundleRequest(SnapshotModel):
    request_key: Label
    requests: tuple[TicketRequestV2, ...] = Field(min_length=1, max_length=64)


class ResultSetRequest(SnapshotModel):
    request_key: Label
    portfolio_lock_id: Label


REQUEST_TYPES = {"budget-record": BudgetRecordRequest, "ticket-requests": TicketBundleRequest,
    "snapshot": SnapshotRequestV1, "assembly": AssemblyRequestV1, "lock": LockRequestV1,
    "results": ResultSetRequest, "settle": SettlementRequestV1}
