from __future__ import annotations

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.common import normalize_utc
from football_system.domain.settlement import SettlementResultReason
from football_system.domain.strategy_pass import fresh
from football_system.domain.strategy_settlement import (
    AtomicSettlementV1,
    StrategySettlementV1,
    SystemTicketSettlementV1,
)


def settle_strategy_plan(plan, results, settled_at_utc, *, issues=(), previous=None):
    plan = fresh(plan)
    settled_at = normalize_utc(settled_at_utc)
    if settled_at < plan.source.parent.as_of_at_utc:
        raise ValueError("settlement predates parent decision")
    results = tuple(sorted((fresh(r) for r in results), key=lambda r: r.match_id))
    if any(
        r.payload_hash != match_result_payload_sha256(r.home_goals, r.away_goals)
        for r in results
    ):
        raise ValueError("normalized result payload hash mismatch")
    issues = tuple(sorted((fresh(i) for i in issues), key=lambda i: i.match_id))
    by_match = {r.match_id: r for r in results}
    issue_matches = {i.match_id for i in issues}
    expected = {s.match_id for t in plan.tickets for s in t.candidate.selections}
    if (
        len(by_match) != len(results)
        or len({r.match_result_id for r in results}) != len(results)
        or len(issue_matches) != len(issues)
        or set(by_match) & issue_matches
        or (set(by_match) | issue_matches) - expected
    ):
        raise ValueError("duplicate/unknown/conflicting settlement match lineage")
    if any(
        r.available_at_utc > settled_at or r.ingested_at_utc > settled_at
        for r in results
    ):
        raise ValueError("result crosses settlement cutoff")
    if previous is not None:
        previous = fresh(previous)
        if (
            previous.plan_id != plan.plan_id
            or previous.plan_hash != plan.plan_hash
            or previous.parent != plan.source.parent
            or previous.settled_at_utc > settled_at
        ):
            raise ValueError("settlement correction crosses frozen plan/time")
        for old in previous.match_results:
            new = by_match.get(old.match_id)
            if new is None or (
                new != old
                and (
                    new.supersedes_match_result_id != old.match_result_id
                    or new.provider_code != old.provider_code
                    or new.ingested_at_utc < old.ingested_at_utc
                )
            ):
                raise ValueError(
                    "corrected result must directly supersede prior result"
                )
    tickets = []
    for ticket in plan.tickets:
        c = ticket.candidate
        matches = tuple(s.match_id for s in c.selections)
        missing = tuple(
            m for m in matches if m not in by_match and m not in issue_matches
        )
        unsupported = tuple(m for m in matches if m in issue_matches)
        reason = (
            SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
            if unsupported
            else SettlementResultReason.MISSING_RESULT
            if missing
            else SettlementResultReason.SETTLED
        )
        atomic = ()
        gross = None
        status = "UNSETTLED"
        if reason == SettlementResultReason.SETTLED:
            outcomes = tuple(
                by_match[s.match_id].three_way_selection() for s in c.selections
            )
            state = next(
                s for s in ticket.gross_payout_states if s.outcomes == outcomes
            )
            won = set(state.winning_atomic_bet_ids)
            atomic = tuple(
                AtomicSettlementV1(
                    atomic_bet_id=a.atomic_bet_id,
                    won=a.atomic_bet_id in won,
                    stake_fen=200 * ticket.multiplier,
                    gross_payout_fen=a.gross_payout_fen * ticket.multiplier
                    if a.atomic_bet_id in won
                    else 0,
                )
                for a in c.atomic_bets
            )
            gross = state.gross_payout_fen
            status = (
                "FULL_PAYOUT"
                if len(won) == len(atomic)
                else "PARTIAL_PAYOUT"
                if won
                else "LOST"
            )
        tickets.append(
            SystemTicketSettlementV1(
                ticket_id=ticket.ticket_id,
                reason=reason,
                status=status,
                missing_match_ids=missing,
                issue_match_ids=unsupported,
                atomic_settlements=atomic,
                stake_fen=ticket.stake_fen,
                gross_payout_fen=gross,
                profit_loss_fen=None if gross is None else gross - ticket.stake_fen,
            )
        )
    reasons = {t.reason for t in tickets}
    reason = (
        SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
        if SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE in reasons
        else SettlementResultReason.MISSING_RESULT
        if SettlementResultReason.MISSING_RESULT in reasons
        else SettlementResultReason.SETTLED
    )
    gross = (
        sum(t.gross_payout_fen for t in tickets)
        if reason == SettlementResultReason.SETTLED
        else None
    )
    return StrategySettlementV1.freeze(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        parent=plan.source.parent,
        settled_at_utc=settled_at,
        match_results=results,
        issues=issues,
        supersedes_settlement_id=None if previous is None else previous.settlement_id,
        ticket_results=tuple(tickets),
        reason=reason,
        budget_fen=plan.source.budget_fen,
        deployed_stake_fen=plan.total_stake_fen,
        cash_fen=plan.cash_fen,
        gross_payout_fen=gross,
        ending_capital_fen=None if gross is None else plan.cash_fen + gross,
        profit_loss_fen=None if gross is None else gross - plan.total_stake_fen,
    )
