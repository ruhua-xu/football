from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import SelectionKey


class MatchResult(DomainModel):
    match_result_id: Identifier
    match_id: Identifier
    provider_code: Identifier
    home_goals: int = Field(ge=0)
    away_goals: int = Field(ge=0)
    observed_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    source_result_key: Identifier
    payload_hash: Identifier
    supersedes_match_result_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_timeline(self) -> MatchResult:
        if not (
            self.observed_at_utc
            <= self.available_at_utc
            <= self.ingested_at_utc
        ):
            raise ValueError(
                "match result timestamps must follow observed, available, ingested"
            )
        return self

    @model_validator(mode="after")
    def validate_supersession(self) -> MatchResult:
        if self.supersedes_match_result_id == self.match_result_id:
            raise ValueError("match result cannot supersede itself")
        return self

    def three_way_selection(self) -> SelectionKey:
        if self.home_goals > self.away_goals:
            return SelectionKey.HOME_WIN
        if self.home_goals < self.away_goals:
            return SelectionKey.AWAY_WIN
        return SelectionKey.DRAW


class SettlementScopeKind(StrEnum):
    ANALYSIS_RUN = "ANALYSIS_RUN"
    PORTFOLIO_REVISION = "PORTFOLIO_REVISION"


class SettlementStatus(StrEnum):
    WON = "WON"
    LOST = "LOST"


class Settlement(DomainModel):
    settlement_id: Identifier
    settlement_kind: Literal["BACKTEST"] = "BACKTEST"
    scope_kind: SettlementScopeKind
    parent_analysis_run_id: Identifier
    decision_scope_id: Identifier
    portfolio_id: Identifier
    ticket_id: Identifier
    match_result_ids: tuple[Identifier, Identifier]
    status: SettlementStatus
    stake_fen: int = Field(gt=0)
    gross_payout_fen: int = Field(ge=0)
    profit_loss_fen: int
    payout_policy_version: str = Field(min_length=1, max_length=80)
    settlement_policy_version: str = Field(min_length=1, max_length=80)
    settled_at_utc: UtcDateTime
    supersedes_settlement_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> Settlement:
        if (
            self.scope_kind == SettlementScopeKind.ANALYSIS_RUN
            and self.decision_scope_id != self.parent_analysis_run_id
        ):
            raise ValueError("base settlement scope must equal its AnalysisRun")
        if (
            self.scope_kind == SettlementScopeKind.PORTFOLIO_REVISION
            and self.decision_scope_id == self.parent_analysis_run_id
        ):
            raise ValueError("revision settlement requires a distinct decision scope")
        return self

    @model_validator(mode="after")
    def validate_result_lineage(self) -> Settlement:
        if len(set(self.match_result_ids)) != 2:
            raise ValueError("2X1 settlement requires two unique match results")
        if self.supersedes_settlement_id == self.settlement_id:
            raise ValueError("settlement cannot supersede itself")
        return self

    @model_validator(mode="after")
    def validate_financials(self) -> Settlement:
        if self.profit_loss_fen != self.gross_payout_fen - self.stake_fen:
            raise ValueError("settlement profit/loss is inconsistent")
        if self.status == SettlementStatus.WON and self.gross_payout_fen <= 0:
            raise ValueError("winning settlement requires a gross payout")
        if self.status == SettlementStatus.LOST and self.gross_payout_fen != 0:
            raise ValueError("losing settlement must have zero gross payout")
        return self
