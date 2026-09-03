from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier


class MarketOddsReconciliationIssueReason(StrEnum):
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    DUPLICATE_RESOLVED_TARGET = "DUPLICATE_RESOLVED_TARGET"
    EVENT_DATA_INVALID = "EVENT_DATA_INVALID"
    REQUESTED_MATCH_MISSING = "REQUESTED_MATCH_MISSING"


class MarketOddsReconciliationIssue(DomainModel):
    issue_id: Identifier
    reason: MarketOddsReconciliationIssueReason
    provider_code: Identifier
    external_namespace: Identifier | None = None
    external_match_id: Identifier | None = None
    requested_match_id: Identifier | None = None
    candidates: tuple[Identifier, ...] = ()
    code: Identifier
    detail: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_issue(self) -> Self:
        if (self.external_namespace is None) != (self.external_match_id is None):
            raise ValueError(
                "market odds issue external namespace and match ID must be paired"
            )
        if self.candidates != tuple(sorted(set(self.candidates))):
            raise ValueError("market odds issue candidates must be unique and sorted")
        return self
