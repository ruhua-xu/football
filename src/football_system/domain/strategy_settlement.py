"""Explicit BACKTEST settlement for constituent-based system tickets."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    SettlementResultReason,
)
from football_system.domain.strategy_pass import StrategyParentV1, digest


class AtomicSettlementV1(DomainModel):
    atomic_bet_id: Identifier
    won: bool
    stake_fen: int = Field(gt=0, strict=True)
    gross_payout_fen: int = Field(ge=0, strict=True)


class SystemTicketSettlementV1(DomainModel):
    ticket_id: Identifier
    reason: SettlementResultReason
    status: Literal["FULL_PAYOUT", "PARTIAL_PAYOUT", "LOST", "UNSETTLED"]
    missing_match_ids: tuple[Identifier, ...]
    issue_match_ids: tuple[Identifier, ...]
    atomic_settlements: tuple[AtomicSettlementV1, ...]
    stake_fen: int = Field(gt=0, strict=True)
    gross_payout_fen: int | None = Field(ge=0, strict=True)
    profit_loss_fen: int | None = Field(strict=True)

    @model_validator(mode="after")
    def totals(self) -> Self:
        complete = self.reason == SettlementResultReason.SETTLED
        if complete:
            if (
                not self.atomic_settlements
                or self.missing_match_ids
                or self.issue_match_ids
            ):
                raise ValueError("complete system settlement requires all constituents")
            if (
                self.stake_fen != sum(a.stake_fen for a in self.atomic_settlements)
                or self.gross_payout_fen
                != sum(a.gross_payout_fen for a in self.atomic_settlements)
                or self.profit_loss_fen != self.gross_payout_fen - self.stake_fen
            ):
                raise ValueError("system settlement constituent totals mismatch")
        elif (
            self.atomic_settlements
            or self.gross_payout_fen is not None
            or self.profit_loss_fen is not None
            or self.status != "UNSETTLED"
        ):
            raise ValueError("missing/unsupported result must not invent a payout")
        return self


class StrategySettlementContentV1(DomainModel):
    schema_version: Literal["STRATEGY_SETTLEMENT_V1"] = "STRATEGY_SETTLEMENT_V1"
    policy_version: Literal["THREE_WAY_SYSTEM_PASS_BACKTEST_V1"] = (
        "THREE_WAY_SYSTEM_PASS_BACKTEST_V1"
    )
    settlement_kind: Literal["BACKTEST"] = "BACKTEST"
    plan_id: Identifier
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent: StrategyParentV1
    settled_at_utc: UtcDateTime
    match_results: tuple[MatchResult, ...]
    issues: tuple[MatchSettlementIssue, ...]
    supersedes_settlement_id: Identifier | None
    ticket_results: tuple[SystemTicketSettlementV1, ...]
    reason: SettlementResultReason
    budget_fen: int = Field(ge=0, strict=True)
    deployed_stake_fen: int = Field(ge=0, strict=True)
    cash_fen: int = Field(ge=0, strict=True)
    gross_payout_fen: int | None = Field(ge=0, strict=True)
    ending_capital_fen: int | None = Field(ge=0, strict=True)
    profit_loss_fen: int | None = Field(strict=True)


class StrategySettlementV1(StrategySettlementContentV1):
    settlement_id: Identifier
    settlement_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **content):
        payload = StrategySettlementContentV1.model_validate(content)
        h = digest(
            "STRATEGY_SETTLEMENT_V1",
            payload.model_dump(mode="json", exclude={"schema_version"}),
        )
        return cls(
            settlement_id=stable_id("STRATEGY_SETTLEMENT_V1", h),
            settlement_hash=h,
            **payload.model_dump(mode="python"),
        )

    @model_validator(mode="after")
    def seal(self) -> Self:
        h = digest(
            self.schema_version,
            self.model_dump(
                mode="json",
                exclude={"schema_version", "settlement_id", "settlement_hash"},
            ),
        )
        if self.settlement_hash != h or self.settlement_id != stable_id(
            self.schema_version, h
        ):
            raise ValueError("strategy settlement seal mismatch")
        if (
            self.deployed_stake_fen != sum(t.stake_fen for t in self.ticket_results)
            or self.cash_fen != self.budget_fen - self.deployed_stake_fen
        ):
            raise ValueError("settlement capital/stake mismatch")
        if self.reason == SettlementResultReason.SETTLED:
            if (
                any(t.reason != self.reason for t in self.ticket_results)
                or self.gross_payout_fen
                != sum(t.gross_payout_fen for t in self.ticket_results)
                or self.ending_capital_fen != self.cash_fen + self.gross_payout_fen
                or self.profit_loss_fen != self.ending_capital_fen - self.budget_fen
            ):
                raise ValueError("settlement aggregate mismatch")
        elif any(
            x is not None
            for x in (
                self.gross_payout_fen,
                self.ending_capital_fen,
                self.profit_loss_fen,
            )
        ):
            raise ValueError("incomplete settlement cannot report aggregate return")
        return self
