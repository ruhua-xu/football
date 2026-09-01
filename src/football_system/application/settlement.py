from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol

from football_system.domain.betting import PassType, Portfolio, TicketAllocation
from football_system.domain.common import UtcDateTime, normalize_utc, stable_id
from football_system.domain.market import MarketType
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    PortfolioSettlement,
    PortfolioSettlementResult,
    Settlement,
    SettlementCoverage,
    SettlementResultReason,
    SettlementScope,
    SettlementStatus,
    TicketSettlementResult,
    UnsupportedSettlementReason,
)

SETTLEMENT_POLICY_VERSION = "THREE_WAY_2X1_BACKTEST_V1"
BACKTEST_SETTLEMENT_KIND = "BACKTEST"


class SettlementLineageError(ValueError):
    """Raised when frozen decisions, results, or corrections cross lineages."""


class AnalysisRunSettlementScope(Protocol):
    analysis_run_id: str


class PortfolioRevisionSettlementScope(Protocol):
    portfolio_revision_id: str
    parent_analysis_run_id: str
    portfolios: tuple[Portfolio, ...]


class SettlementService:
    def __init__(
        self,
        settlement_policy_version: str = SETTLEMENT_POLICY_VERSION,
    ) -> None:
        settlement_policy_version = settlement_policy_version.strip()
        if not settlement_policy_version or len(settlement_policy_version) > 80:
            raise ValueError(
                "settlement policy version must contain 1 to 80 characters"
            )
        self.settlement_policy_version = settlement_policy_version

    def settle_ticket(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        ticket: TicketAllocation,
        match_results: Iterable[MatchResult],
        settled_at_utc: UtcDateTime,
        *,
        settlement_kind: str = BACKTEST_SETTLEMENT_KIND,
        result_issues: Iterable[MatchSettlementIssue] = (),
        supersedes_settlement: Settlement | None = None,
    ) -> TicketSettlementResult:
        settled_at = normalize_utc(settled_at_utc)
        self._validate_ticket_lineage(scope, portfolio, ticket)
        expected_match_ids = tuple(leg.match_id for leg in ticket.candidate.legs)
        result_by_match, issue_by_match = self._index_inputs(
            expected_match_ids,
            match_results,
            result_issues,
            settled_at,
        )
        unsupported_reasons = self._unsupported_product_reasons(
            ticket,
            settlement_kind,
        )
        covered_match_ids = tuple(
            match_id for match_id in expected_match_ids if match_id in result_by_match
        )
        issue_matches = tuple(
            match_id for match_id in expected_match_ids if match_id in issue_by_match
        )
        missing_match_ids = tuple(
            match_id
            for match_id in expected_match_ids
            if match_id not in result_by_match and match_id not in issue_by_match
        )

        if unsupported_reasons or issue_by_match:
            return TicketSettlementResult(
                ticket_id=ticket.ticket_id,
                coverage=SettlementCoverage(
                    reason=SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE,
                    expected_match_ids=expected_match_ids,
                    covered_match_ids=covered_match_ids,
                    missing_match_ids=missing_match_ids,
                    issues=tuple(
                        issue_by_match[match_id] for match_id in issue_matches
                    ),
                    unsupported_reasons=unsupported_reasons,
                    detail="settlement semantics are not supported by this policy",
                ),
            )
        if missing_match_ids:
            return TicketSettlementResult(
                ticket_id=ticket.ticket_id,
                coverage=SettlementCoverage(
                    reason=SettlementResultReason.MISSING_RESULT,
                    expected_match_ids=expected_match_ids,
                    covered_match_ids=covered_match_ids,
                    missing_match_ids=missing_match_ids,
                    detail="one or more ticket legs have no usable final result",
                ),
            )

        ordered_results = tuple(
            result_by_match[match_id] for match_id in expected_match_ids
        )
        won = all(
            result.three_way_selection() == leg.selection
            for leg, result in zip(ticket.candidate.legs, ordered_results, strict=True)
        )
        status = SettlementStatus.WON if won else SettlementStatus.LOST
        gross_payout_fen = ticket.potential_gross_payout_fen if won else 0
        if supersedes_settlement is not None:
            changed = self._validate_ticket_correction(
                scope,
                portfolio,
                ticket,
                ordered_results,
                status,
                gross_payout_fen,
                settled_at,
                supersedes_settlement,
            )
            if not changed:
                return TicketSettlementResult(
                    ticket_id=ticket.ticket_id,
                    coverage=self._complete_coverage(expected_match_ids),
                    settlement=supersedes_settlement,
                )

        match_result_ids = tuple(result.match_result_id for result in ordered_results)
        supersedes_id = (
            supersedes_settlement.settlement_id
            if supersedes_settlement is not None
            else None
        )
        settlement = Settlement(
            settlement_id=stable_id(
                "ticket-settlement",
                settlement_kind,
                scope.scope_kind.value,
                scope.parent_analysis_run_id,
                scope.decision_scope_id,
                portfolio.portfolio_id,
                ticket.ticket_id,
                *match_result_ids,
                ticket.candidate.payout_policy_version,
                self.settlement_policy_version,
                supersedes_id or "INITIAL",
                settled_at.isoformat(),
            ),
            settlement_kind=settlement_kind,
            scope_kind=scope.scope_kind,
            parent_analysis_run_id=scope.parent_analysis_run_id,
            decision_scope_id=scope.decision_scope_id,
            portfolio_id=portfolio.portfolio_id,
            ticket_id=ticket.ticket_id,
            match_result_ids=match_result_ids,
            status=status,
            stake_fen=ticket.stake_fen,
            gross_payout_fen=gross_payout_fen,
            profit_loss_fen=gross_payout_fen - ticket.stake_fen,
            payout_policy_version=ticket.candidate.payout_policy_version,
            settlement_policy_version=self.settlement_policy_version,
            settled_at_utc=settled_at,
            supersedes_settlement_id=supersedes_id,
        )
        return TicketSettlementResult(
            ticket_id=ticket.ticket_id,
            coverage=self._complete_coverage(expected_match_ids),
            settlement=settlement,
        )

    def settle_portfolio(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        match_results: Iterable[MatchResult],
        settled_at_utc: UtcDateTime,
        *,
        settlement_kind: str = BACKTEST_SETTLEMENT_KIND,
        result_issues: Iterable[MatchSettlementIssue] = (),
        previous_ticket_settlements: Iterable[Settlement] = (),
        supersedes_portfolio_settlement: PortfolioSettlement | None = None,
    ) -> PortfolioSettlementResult:
        settled_at = normalize_utc(settled_at_utc)
        self._validate_portfolio_scope(scope, portfolio)
        expected_match_ids = tuple(
            dict.fromkeys(
                leg.match_id
                for ticket in portfolio.tickets
                for leg in ticket.candidate.legs
            )
        )
        results = tuple(match_results)
        issues = tuple(result_issues)
        self._reject_unknown_portfolio_matches(expected_match_ids, results, issues)
        result_by_match = self._unique_results(results)
        issue_by_match = self._unique_issues(issues)
        if set(result_by_match) & set(issue_by_match):
            raise SettlementLineageError(
                "a match cannot have both a final result and settlement issue"
            )
        previous_by_ticket = self._index_previous_ticket_settlements(
            portfolio,
            previous_ticket_settlements,
        )
        self._validate_previous_portfolio_settlement(
            scope,
            portfolio,
            previous_by_ticket,
            supersedes_portfolio_settlement,
            settled_at,
        )

        ticket_results = tuple(
            self.settle_ticket(
                scope,
                portfolio,
                ticket,
                (
                    result_by_match[leg.match_id]
                    for leg in ticket.candidate.legs
                    if leg.match_id in result_by_match
                ),
                settled_at,
                settlement_kind=settlement_kind,
                result_issues=(
                    issue_by_match[leg.match_id]
                    for leg in ticket.candidate.legs
                    if leg.match_id in issue_by_match
                ),
                supersedes_settlement=previous_by_ticket.get(ticket.ticket_id),
            )
            for ticket in portfolio.tickets
        )
        portfolio_unsupported_reasons = (
            (UnsupportedSettlementReason.UNSUPPORTED_SETTLEMENT_KIND,)
            if settlement_kind != BACKTEST_SETTLEMENT_KIND
            else ()
        )
        reason = (
            SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
            if portfolio_unsupported_reasons
            else self._portfolio_result_reason(ticket_results)
        )
        if reason != SettlementResultReason.SETTLED:
            return PortfolioSettlementResult(
                portfolio_id=portfolio.portfolio_id,
                reason=reason,
                ticket_results=ticket_results,
                portfolio_unsupported_reasons=portfolio_unsupported_reasons,
            )

        settlements = tuple(
            result.settlement
            for result in ticket_results
            if result.settlement is not None
        )
        portfolio_settlement = self._aggregate_portfolio(
            scope,
            portfolio,
            settlements,
            settled_at,
            settlement_kind,
            supersedes_portfolio_settlement,
        )
        return PortfolioSettlementResult(
            portfolio_id=portfolio.portfolio_id,
            reason=SettlementResultReason.SETTLED,
            ticket_results=ticket_results,
            portfolio_settlement=portfolio_settlement,
        )

    def settle_analysis_run(
        self,
        analysis_run: AnalysisRunSettlementScope,
        portfolio: Portfolio,
        match_results: Iterable[MatchResult],
        settled_at_utc: UtcDateTime,
        *,
        result_issues: Iterable[MatchSettlementIssue] = (),
    ) -> PortfolioSettlementResult:
        return self.settle_portfolio(
            SettlementScope.for_analysis_run(analysis_run.analysis_run_id),
            portfolio,
            match_results,
            settled_at_utc,
            result_issues=result_issues,
        )

    def settle_portfolio_revision(
        self,
        revision: PortfolioRevisionSettlementScope,
        portfolio: Portfolio,
        match_results: Iterable[MatchResult],
        settled_at_utc: UtcDateTime,
        *,
        result_issues: Iterable[MatchSettlementIssue] = (),
    ) -> PortfolioSettlementResult:
        if portfolio not in revision.portfolios:
            raise SettlementLineageError(
                "portfolio does not belong to the PortfolioRevision"
            )
        return self.settle_portfolio(
            SettlementScope.for_portfolio_revision(
                revision.parent_analysis_run_id,
                revision.portfolio_revision_id,
            ),
            portfolio,
            match_results,
            settled_at_utc,
            result_issues=result_issues,
        )

    def correct_ticket_settlement(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        ticket: TicketAllocation,
        match_results: Iterable[MatchResult],
        settled_at_utc: UtcDateTime,
        previous_settlement: Settlement,
    ) -> TicketSettlementResult:
        return self.settle_ticket(
            scope,
            portfolio,
            ticket,
            match_results,
            settled_at_utc,
            supersedes_settlement=previous_settlement,
        )

    def _aggregate_portfolio(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        settlements: tuple[Settlement, ...],
        settled_at: UtcDateTime,
        settlement_kind: str,
        previous: PortfolioSettlement | None,
    ) -> PortfolioSettlement:
        self._validate_aggregate_lineage(scope, portfolio, settlements)
        ticket_settlement_ids = tuple(item.settlement_id for item in settlements)
        if (
            previous is not None
            and ticket_settlement_ids == previous.ticket_settlement_ids
        ):
            return previous
        gross_ticket_payout_fen = sum(item.gross_payout_fen for item in settlements)
        ending_capital_fen = (
            portfolio.cash_position.amount_fen + gross_ticket_payout_fen
        )
        profit_loss_fen = ending_capital_fen - portfolio.budget_fen
        roi_on_budget = (
            None
            if portfolio.budget_fen == 0
            else Decimal(profit_loss_fen) / Decimal(portfolio.budget_fen)
        )
        roi_on_deployed = (
            None
            if portfolio.total_stake_fen == 0
            else Decimal(profit_loss_fen) / Decimal(portfolio.total_stake_fen)
        )
        previous_id = previous.portfolio_settlement_id if previous is not None else None
        return PortfolioSettlement(
            portfolio_settlement_id=stable_id(
                "portfolio-settlement",
                settlement_kind,
                scope.scope_kind.value,
                scope.parent_analysis_run_id,
                scope.decision_scope_id,
                portfolio.portfolio_id,
                *ticket_settlement_ids,
                self.settlement_policy_version,
                previous_id or "INITIAL",
                settled_at.isoformat(),
            ),
            settlement_kind=settlement_kind,
            scope_kind=scope.scope_kind,
            parent_analysis_run_id=scope.parent_analysis_run_id,
            decision_scope_id=scope.decision_scope_id,
            portfolio_id=portfolio.portfolio_id,
            ticket_settlement_ids=ticket_settlement_ids,
            budget_fen=portfolio.budget_fen,
            deployed_stake_fen=portfolio.total_stake_fen,
            original_cash_fen=portfolio.cash_position.amount_fen,
            gross_ticket_payout_fen=gross_ticket_payout_fen,
            ending_capital_fen=ending_capital_fen,
            profit_loss_fen=profit_loss_fen,
            roi_on_budget=roi_on_budget,
            roi_on_deployed=roi_on_deployed,
            settlement_policy_version=self.settlement_policy_version,
            settled_at_utc=settled_at,
            supersedes_portfolio_settlement_id=previous_id,
        )

    def _validate_ticket_lineage(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        ticket: TicketAllocation,
    ) -> None:
        self._validate_portfolio_scope(scope, portfolio)
        frozen_ticket = next(
            (item for item in portfolio.tickets if item.ticket_id == ticket.ticket_id),
            None,
        )
        if frozen_ticket is None or frozen_ticket != ticket:
            raise SettlementLineageError(
                "ticket is not the frozen allocation in this portfolio"
            )
        if ticket.candidate.analysis_run_id != scope.decision_scope_id:
            raise SettlementLineageError("ticket crosses its decision scope")

    @staticmethod
    def _validate_portfolio_scope(
        scope: SettlementScope,
        portfolio: Portfolio,
    ) -> None:
        if portfolio.analysis_run_id != scope.decision_scope_id:
            raise SettlementLineageError("portfolio crosses its decision scope")

    @staticmethod
    def _index_inputs(
        expected_match_ids: tuple[str, str],
        match_results: Iterable[MatchResult],
        result_issues: Iterable[MatchSettlementIssue],
        settled_at: UtcDateTime,
    ) -> tuple[dict[str, MatchResult], dict[str, MatchSettlementIssue]]:
        results = tuple(match_results)
        issues = tuple(result_issues)
        expected = set(expected_match_ids)
        if any(result.match_id not in expected for result in results) or any(
            issue.match_id not in expected for issue in issues
        ):
            raise SettlementLineageError("result references the wrong ticket match")
        result_by_match = SettlementService._unique_results(results)
        issue_by_match = SettlementService._unique_issues(issues)
        if set(result_by_match) & set(issue_by_match):
            raise SettlementLineageError(
                "a match cannot have both a final result and settlement issue"
            )
        if any(
            result.available_at_utc > settled_at or result.ingested_at_utc > settled_at
            for result in results
        ):
            raise SettlementLineageError("match result crosses settlement cutoff")
        return result_by_match, issue_by_match

    @staticmethod
    def _unique_results(results: Iterable[MatchResult]) -> dict[str, MatchResult]:
        result_by_match: dict[str, MatchResult] = {}
        result_ids: set[str] = set()
        for result in results:
            if result.match_id in result_by_match:
                raise SettlementLineageError("multiple results supplied for one match")
            if result.match_result_id in result_ids:
                raise SettlementLineageError("match result ID is reused across matches")
            result_by_match[result.match_id] = result
            result_ids.add(result.match_result_id)
        return result_by_match

    @staticmethod
    def _unique_issues(
        issues: Iterable[MatchSettlementIssue],
    ) -> dict[str, MatchSettlementIssue]:
        issue_by_match: dict[str, MatchSettlementIssue] = {}
        for issue in issues:
            if issue.match_id in issue_by_match:
                raise SettlementLineageError("multiple settlement issues for one match")
            issue_by_match[issue.match_id] = issue
        return issue_by_match

    @staticmethod
    def _reject_unknown_portfolio_matches(
        expected_match_ids: tuple[str, ...],
        results: tuple[MatchResult, ...],
        issues: tuple[MatchSettlementIssue, ...],
    ) -> None:
        expected = set(expected_match_ids)
        if any(result.match_id not in expected for result in results) or any(
            issue.match_id not in expected for issue in issues
        ):
            raise SettlementLineageError("result references the wrong portfolio match")

    @staticmethod
    def _unsupported_product_reasons(
        ticket: TicketAllocation,
        settlement_kind: str,
    ) -> tuple[UnsupportedSettlementReason, ...]:
        reasons: list[UnsupportedSettlementReason] = []
        if settlement_kind != BACKTEST_SETTLEMENT_KIND:
            reasons.append(UnsupportedSettlementReason.UNSUPPORTED_SETTLEMENT_KIND)
        if ticket.candidate.pass_type != PassType.TWO_FOLD_ONE:
            reasons.append(UnsupportedSettlementReason.UNSUPPORTED_PASS_TYPE)
        if any(
            leg.market.market_type != MarketType.THREE_WAY
            for leg in ticket.candidate.legs
        ):
            reasons.append(UnsupportedSettlementReason.UNSUPPORTED_MARKET)
        return tuple(reasons)

    @staticmethod
    def _complete_coverage(
        expected_match_ids: tuple[str, str],
    ) -> SettlementCoverage:
        return SettlementCoverage(
            reason=SettlementResultReason.SETTLED,
            expected_match_ids=expected_match_ids,
            covered_match_ids=expected_match_ids,
        )

    def _validate_ticket_correction(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        ticket: TicketAllocation,
        results: tuple[MatchResult, MatchResult],
        status: SettlementStatus,
        gross_payout_fen: int,
        settled_at: UtcDateTime,
        previous: Settlement,
    ) -> bool:
        if (
            previous.scope_kind != scope.scope_kind
            or previous.parent_analysis_run_id != scope.parent_analysis_run_id
            or previous.decision_scope_id != scope.decision_scope_id
            or previous.portfolio_id != portfolio.portfolio_id
            or previous.ticket_id != ticket.ticket_id
        ):
            raise SettlementLineageError(
                "superseded settlement crosses scope or ticket"
            )
        if (
            previous.stake_fen != ticket.stake_fen
            or previous.payout_policy_version != ticket.candidate.payout_policy_version
            or previous.settlement_policy_version != self.settlement_policy_version
            or previous.settlement_kind != BACKTEST_SETTLEMENT_KIND
        ):
            raise SettlementLineageError(
                "superseded settlement is not from this policy"
            )
        if previous.settled_at_utc > settled_at:
            raise SettlementLineageError(
                "correction predates the settlement it supersedes"
            )
        changed = False
        for previous_result_id, result in zip(
            previous.match_result_ids,
            results,
            strict=True,
        ):
            if result.match_result_id == previous_result_id:
                continue
            if result.supersedes_match_result_id != previous_result_id:
                raise SettlementLineageError(
                    "corrected result does not supersede the settled result"
                )
            changed = True
        if not changed and (
            previous.status != status
            or previous.gross_payout_fen != gross_payout_fen
            or previous.profit_loss_fen != gross_payout_fen - ticket.stake_fen
        ):
            raise SettlementLineageError("existing settlement contradicts its results")
        return changed

    @staticmethod
    def _index_previous_ticket_settlements(
        portfolio: Portfolio,
        settlements: Iterable[Settlement],
    ) -> dict[str, Settlement]:
        ticket_ids = {ticket.ticket_id for ticket in portfolio.tickets}
        result: dict[str, Settlement] = {}
        for settlement in settlements:
            if settlement.ticket_id not in ticket_ids:
                raise SettlementLineageError(
                    "previous settlement references the wrong portfolio ticket"
                )
            if settlement.ticket_id in result:
                raise SettlementLineageError(
                    "multiple previous settlements supplied for one ticket"
                )
            result[settlement.ticket_id] = settlement
        return result

    def _validate_previous_portfolio_settlement(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        previous_by_ticket: dict[str, Settlement],
        previous: PortfolioSettlement | None,
        settled_at: UtcDateTime,
    ) -> None:
        if previous is None:
            if previous_by_ticket:
                raise SettlementLineageError(
                    "ticket corrections require a prior portfolio settlement"
                )
            return
        if (
            previous.scope_kind != scope.scope_kind
            or previous.parent_analysis_run_id != scope.parent_analysis_run_id
            or previous.decision_scope_id != scope.decision_scope_id
            or previous.portfolio_id != portfolio.portfolio_id
            or previous.settlement_policy_version != self.settlement_policy_version
            or previous.settlement_kind != BACKTEST_SETTLEMENT_KIND
            or previous.budget_fen != portfolio.budget_fen
            or previous.deployed_stake_fen != portfolio.total_stake_fen
            or previous.original_cash_fen != portfolio.cash_position.amount_fen
        ):
            raise SettlementLineageError(
                "superseded portfolio settlement crosses scope"
            )
        if previous.settled_at_utc > settled_at:
            raise SettlementLineageError(
                "portfolio correction predates the settlement it supersedes"
            )
        previous_settlements = tuple(
            previous_by_ticket[ticket.ticket_id]
            for ticket in portfolio.tickets
            if ticket.ticket_id in previous_by_ticket
        )
        if previous.ticket_settlement_ids != tuple(
            item.settlement_id for item in previous_settlements
        ):
            raise SettlementLineageError(
                "previous ticket settlements do not match portfolio settlement"
            )
        self._validate_aggregate_lineage(scope, portfolio, previous_settlements)
        if previous.gross_ticket_payout_fen != sum(
            item.gross_payout_fen for item in previous_settlements
        ):
            raise SettlementLineageError(
                "previous portfolio payout does not match ticket settlements"
            )

    @staticmethod
    def _portfolio_result_reason(
        ticket_results: tuple[TicketSettlementResult, ...],
    ) -> SettlementResultReason:
        reasons = {result.reason for result in ticket_results}
        if SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE in reasons:
            return SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
        if SettlementResultReason.MISSING_RESULT in reasons:
            return SettlementResultReason.MISSING_RESULT
        return SettlementResultReason.SETTLED

    def _validate_aggregate_lineage(
        self,
        scope: SettlementScope,
        portfolio: Portfolio,
        settlements: tuple[Settlement, ...],
    ) -> None:
        if len(settlements) != len(portfolio.tickets):
            raise SettlementLineageError(
                "portfolio aggregation requires one settlement per ticket"
            )
        for ticket, settlement in zip(portfolio.tickets, settlements, strict=True):
            if (
                settlement.scope_kind != scope.scope_kind
                or settlement.parent_analysis_run_id != scope.parent_analysis_run_id
                or settlement.decision_scope_id != scope.decision_scope_id
                or settlement.portfolio_id != portfolio.portfolio_id
                or settlement.ticket_id != ticket.ticket_id
                or settlement.stake_fen != ticket.stake_fen
                or settlement.payout_policy_version
                != ticket.candidate.payout_policy_version
                or settlement.settlement_policy_version
                != self.settlement_policy_version
            ):
                raise SettlementLineageError(
                    "ticket settlement cannot be aggregated across lineages"
                )
