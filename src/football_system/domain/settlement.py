from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime
from football_system.domain.market import SelectionKey


class MatchResult(DomainModel):
    match_result_id: Identifier
    match_id: Identifier
    provider_code: Identifier
    home_goals: int = Field(ge=0, strict=True)
    away_goals: int = Field(ge=0, strict=True)
    observed_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    source_result_key: Identifier
    payload_hash: Identifier
    supersedes_match_result_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_timeline(self) -> MatchResult:
        if not (self.observed_at_utc <= self.available_at_utc <= self.ingested_at_utc):
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


class SettlementScope(DomainModel):
    scope_kind: SettlementScopeKind
    parent_analysis_run_id: Identifier
    decision_scope_id: Identifier

    @classmethod
    def for_analysis_run(cls, analysis_run_id: str) -> SettlementScope:
        return cls(
            scope_kind=SettlementScopeKind.ANALYSIS_RUN,
            parent_analysis_run_id=analysis_run_id,
            decision_scope_id=analysis_run_id,
        )

    @classmethod
    def for_portfolio_revision(
        cls,
        parent_analysis_run_id: str,
        portfolio_revision_id: str,
    ) -> SettlementScope:
        return cls(
            scope_kind=SettlementScopeKind.PORTFOLIO_REVISION,
            parent_analysis_run_id=parent_analysis_run_id,
            decision_scope_id=portfolio_revision_id,
        )

    @model_validator(mode="after")
    def validate_scope(self) -> SettlementScope:
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


class SettlementStatus(StrEnum):
    WON = "WON"
    LOST = "LOST"


class SettlementResultReason(StrEnum):
    SETTLED = "SETTLED"
    MISSING_RESULT = "MISSING_RESULT"
    UNSUPPORTED_SETTLEMENT_CASE = "UNSUPPORTED_SETTLEMENT_CASE"


class UnsupportedSettlementReason(StrEnum):
    CANCELLATION = "CANCELLATION"
    ABANDONMENT = "ABANDONMENT"
    VOID = "VOID"
    REFUND = "REFUND"
    EXTRA_TIME = "EXTRA_TIME"
    PENALTY = "PENALTY"
    PENALTY_SHOOTOUT = "PENALTY_SHOOTOUT"
    DEGRADE = "DEGRADE"
    PARLAY_DEGRADE = "PARLAY_DEGRADE"
    UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"
    UNSUPPORTED_PASS_TYPE = "UNSUPPORTED_PASS_TYPE"
    UNSUPPORTED_SETTLEMENT_KIND = "UNSUPPORTED_SETTLEMENT_KIND"


class MatchSettlementIssue(DomainModel):
    match_id: Identifier
    reason: UnsupportedSettlementReason
    detail: str | None = Field(default=None, min_length=1, max_length=240)


class SettlementCoverage(DomainModel):
    reason: SettlementResultReason
    expected_match_ids: tuple[Identifier, Identifier]
    covered_match_ids: tuple[Identifier, ...]
    missing_match_ids: tuple[Identifier, ...] = ()
    issues: tuple[MatchSettlementIssue, ...] = ()
    unsupported_reasons: tuple[UnsupportedSettlementReason, ...] = ()
    detail: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_coverage(self) -> SettlementCoverage:
        expected = set(self.expected_match_ids)
        covered = set(self.covered_match_ids)
        missing = set(self.missing_match_ids)
        issue_matches = {issue.match_id for issue in self.issues}
        if len(expected) != 2:
            raise ValueError("2X1 coverage requires two unique matches")
        if len(covered) != len(self.covered_match_ids):
            raise ValueError("covered match IDs must be unique")
        if len(missing) != len(self.missing_match_ids):
            raise ValueError("missing match IDs must be unique")
        if len(issue_matches) != len(self.issues):
            raise ValueError("settlement issues must be unique by match")
        if len(self.unsupported_reasons) != len(set(self.unsupported_reasons)):
            raise ValueError("unsupported settlement reasons must be unique")
        if (covered | missing | issue_matches) != expected:
            raise ValueError("settlement coverage must account for every ticket match")
        if covered & missing or covered & issue_matches or missing & issue_matches:
            raise ValueError("settlement coverage categories must not overlap")
        if self.reason == SettlementResultReason.SETTLED:
            if (
                covered != expected
                or missing
                or self.issues
                or self.unsupported_reasons
            ):
                raise ValueError("settled coverage must contain both usable results")
        elif self.reason == SettlementResultReason.MISSING_RESULT:
            if not missing or self.issues or self.unsupported_reasons:
                raise ValueError("missing-result coverage requires missing matches")
        elif not self.issues and not self.unsupported_reasons:
            raise ValueError("unsupported coverage requires an explicit reason")
        return self


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

    @property
    def is_correction(self) -> bool:
        return self.supersedes_settlement_id is not None


class TicketSettlementResult(DomainModel):
    ticket_id: Identifier
    coverage: SettlementCoverage
    settlement: Settlement | None = None

    @model_validator(mode="after")
    def validate_result(self) -> TicketSettlementResult:
        if self.coverage.reason == SettlementResultReason.SETTLED:
            if self.settlement is None:
                raise ValueError("settled result requires a Settlement")
            if self.settlement.ticket_id != self.ticket_id:
                raise ValueError("ticket settlement result has inconsistent lineage")
        elif self.settlement is not None:
            raise ValueError(
                "incomplete or unsupported result cannot contain Settlement"
            )
        return self

    @property
    def reason(self) -> SettlementResultReason:
        return self.coverage.reason

    @property
    def is_settled(self) -> bool:
        return self.settlement is not None


class PortfolioSettlement(DomainModel):
    portfolio_settlement_id: Identifier
    settlement_kind: Literal["BACKTEST"] = "BACKTEST"
    scope_kind: SettlementScopeKind
    parent_analysis_run_id: Identifier
    decision_scope_id: Identifier
    portfolio_id: Identifier
    ticket_settlement_ids: tuple[Identifier, ...]
    budget_fen: int = Field(ge=0)
    deployed_stake_fen: int = Field(ge=0)
    original_cash_fen: int = Field(ge=0)
    gross_ticket_payout_fen: int = Field(ge=0)
    ending_capital_fen: int = Field(ge=0)
    profit_loss_fen: int
    roi_on_budget: Decimal | None
    roi_on_deployed: Decimal | None
    settlement_policy_version: str = Field(min_length=1, max_length=80)
    settled_at_utc: UtcDateTime
    supersedes_portfolio_settlement_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> PortfolioSettlement:
        SettlementScope(
            scope_kind=self.scope_kind,
            parent_analysis_run_id=self.parent_analysis_run_id,
            decision_scope_id=self.decision_scope_id,
        )
        if self.supersedes_portfolio_settlement_id == self.portfolio_settlement_id:
            raise ValueError("portfolio settlement cannot supersede itself")
        return self

    @model_validator(mode="after")
    def validate_financials(self) -> PortfolioSettlement:
        if len(self.ticket_settlement_ids) != len(set(self.ticket_settlement_ids)):
            raise ValueError("portfolio ticket settlement IDs must be unique")
        if self.budget_fen != self.deployed_stake_fen + self.original_cash_fen:
            raise ValueError("portfolio settlement budget is inconsistent")
        if self.deployed_stake_fen > 0 and not self.ticket_settlement_ids:
            raise ValueError("deployed stake requires ticket settlements")
        if self.deployed_stake_fen == 0 and self.ticket_settlement_ids:
            raise ValueError("all-cash portfolio cannot contain ticket settlements")
        expected_ending = self.original_cash_fen + self.gross_ticket_payout_fen
        if self.ending_capital_fen != expected_ending:
            raise ValueError("portfolio ending capital is inconsistent")
        if self.profit_loss_fen != self.ending_capital_fen - self.budget_fen:
            raise ValueError("portfolio profit/loss is inconsistent")
        expected_budget_roi = (
            None
            if self.budget_fen == 0
            else Decimal(self.profit_loss_fen) / Decimal(self.budget_fen)
        )
        expected_deployed_roi = (
            None
            if self.deployed_stake_fen == 0
            else Decimal(self.profit_loss_fen) / Decimal(self.deployed_stake_fen)
        )
        if self.roi_on_budget != expected_budget_roi:
            raise ValueError("ROI on budget is inconsistent")
        if self.roi_on_deployed != expected_deployed_roi:
            raise ValueError("ROI on deployed stake is inconsistent")
        return self

    @property
    def cash_fen(self) -> int:
        return self.original_cash_fen

    @property
    def is_correction(self) -> bool:
        return self.supersedes_portfolio_settlement_id is not None

    @property
    def supersedes_settlement_id(self) -> str | None:
        return self.supersedes_portfolio_settlement_id


class PortfolioSettlementResult(DomainModel):
    portfolio_id: Identifier
    reason: SettlementResultReason
    ticket_results: tuple[TicketSettlementResult, ...]
    portfolio_unsupported_reasons: tuple[UnsupportedSettlementReason, ...] = ()
    portfolio_settlement: PortfolioSettlement | None = None

    @model_validator(mode="after")
    def validate_result(self) -> PortfolioSettlementResult:
        ticket_ids = [result.ticket_id for result in self.ticket_results]
        if len(ticket_ids) != len(set(ticket_ids)):
            raise ValueError("portfolio settlement results require unique tickets")
        if len(self.portfolio_unsupported_reasons) != len(
            set(self.portfolio_unsupported_reasons)
        ):
            raise ValueError("portfolio unsupported reasons must be unique")
        ticket_reasons = {result.reason for result in self.ticket_results}
        expected_reason = SettlementResultReason.SETTLED
        if (
            self.portfolio_unsupported_reasons
            or SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE in ticket_reasons
        ):
            expected_reason = SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
        elif SettlementResultReason.MISSING_RESULT in ticket_reasons:
            expected_reason = SettlementResultReason.MISSING_RESULT
        if self.reason != expected_reason:
            raise ValueError("portfolio result reason is inconsistent with its tickets")
        if self.reason == SettlementResultReason.SETTLED:
            if self.portfolio_settlement is None:
                raise ValueError("settled portfolio result requires an aggregation")
            if self.portfolio_settlement.portfolio_id != self.portfolio_id:
                raise ValueError("portfolio settlement result has inconsistent lineage")
            if any(not result.is_settled for result in self.ticket_results):
                raise ValueError("settled portfolio requires every ticket settlement")
        elif self.portfolio_settlement is not None:
            raise ValueError("incomplete portfolio cannot contain an aggregation")
        return self

    @property
    def missing_match_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                match_id
                for result in self.ticket_results
                for match_id in result.coverage.missing_match_ids
            )
        )

    @property
    def unsupported_reasons(self) -> tuple[UnsupportedSettlementReason, ...]:
        reasons = list(self.portfolio_unsupported_reasons)
        for result in self.ticket_results:
            reasons.extend(result.coverage.unsupported_reasons)
            reasons.extend(issue.reason for issue in result.coverage.issues)
        return tuple(dict.fromkeys(reasons))
