from typing import Literal

from pydantic import Field, model_validator

from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import MarketArtifact, MultiMarketModel
from football_system.domain.settlement import MatchResult, MatchSettlementIssue
from football_system.domain.strategy_pass_v2 import Money


class ExpandedAtomicSettlementV2(MultiMarketModel):
    atomic_bet_id: Identifier
    won: bool
    stake_fen: Money
    gross_payout_fen: Money


class TicketSettlementV2(MultiMarketModel):
    ticket_id: Identifier
    reason: Literal["SETTLED", "MISSING_RESULT", "UNSUPPORTED_SETTLEMENT_CASE"]
    atomic_results: tuple[ExpandedAtomicSettlementV2, ...] = Field(max_length=256)
    missing_match_ids: tuple[Identifier, ...]
    issue_match_ids: tuple[Identifier, ...]
    stake_fen: Money
    gross_payout_fen: Money | None


class StrategySettlementV2(MarketArtifact):
    schema_version: Literal["STRATEGY_SETTLEMENT_V2"] = "STRATEGY_SETTLEMENT_V2"
    policy_version: Literal["REGULAR_TIME_EXPANDED_MARKET_PASS_V2"] = (
        "REGULAR_TIME_EXPANDED_MARKET_PASS_V2"
    )
    settlement_kind: Literal["BACKTEST"] = "BACKTEST"
    plan: ArtifactRefV1
    settled_at_utc: UtcDateTime
    previous: ArtifactRefV1 | None
    results: tuple[MatchResult, ...] = Field(max_length=16)
    issues: tuple[MatchSettlementIssue, ...] = Field(max_length=16)
    tickets: tuple[TicketSettlementV2, ...] = Field(max_length=8)
    reason: Literal["SETTLED", "MISSING_RESULT", "UNSUPPORTED_SETTLEMENT_CASE"]
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    gross_payout_fen: Money | None
    ending_capital_fen: Money | None
    profit_loss_fen: int | None = Field(strict=True)

    @model_validator(mode="after")
    def amounts(self):
        if (
            self.stake_fen != sum(t.stake_fen for t in self.tickets)
            or self.cash_fen != self.budget_fen - self.stake_fen
        ):
            raise ValueError("V2 settlement budget/stake mismatch")
        for t in self.tickets:
            if t.reason == "SETTLED":
                if (
                    not t.atomic_results
                    or t.missing_match_ids
                    or t.issue_match_ids
                    or sum(a.stake_fen for a in t.atomic_results) != t.stake_fen
                    or sum(a.gross_payout_fen for a in t.atomic_results)
                    != t.gross_payout_fen
                ):
                    raise ValueError("expanded settlement totals mismatch")
            elif t.gross_payout_fen is not None or t.atomic_results:
                raise ValueError("unsettled V2 ticket cannot report a payout")
        if self.reason == "SETTLED":
            if (
                any(t.reason != "SETTLED" for t in self.tickets)
                or self.gross_payout_fen
                != sum(t.gross_payout_fen for t in self.tickets)
                or self.ending_capital_fen != self.cash_fen + self.gross_payout_fen
                or self.profit_loss_fen != self.gross_payout_fen - self.stake_fen
            ):
                raise ValueError("V2 portfolio settlement mismatch")
        elif any(
            v is not None
            for v in (
                self.gross_payout_fen,
                self.ending_capital_fen,
                self.profit_loss_fen,
            )
        ):
            raise ValueError("incomplete result is not a loss or refund")
        return self
