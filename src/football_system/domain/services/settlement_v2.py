from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.common import normalize_utc
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import revalidate, settle_market
from football_system.domain.settlement_v2 import (
    ExpandedAtomicSettlementV2,
    StrategySettlementV2,
    TicketSettlementV2,
)


def settle_strategy_v2(plan, results, settled_at_utc, *, issues=(), previous=None):
    plan = revalidate(plan)
    at = normalize_utc(settled_at_utc)
    results = tuple(sorted((revalidate(r) for r in results), key=lambda r: r.match_id))
    issues = tuple(sorted((revalidate(i) for i in issues), key=lambda i: i.match_id))
    expected = {c.match_id for t in plan.tickets for c in t.candidate.choice_sets}
    by_match = {r.match_id: r for r in results}
    issue_ids = {i.match_id for i in issues}
    if (
        len(by_match) != len(results)
        or len({r.match_result_id for r in results}) != len(results)
        or len(issue_ids) != len(issues)
        or issue_ids & by_match.keys()
        or (set(by_match) | issue_ids) - expected
    ):
        raise ValueError("V2 settlement result coverage/identity mismatch")
    if at < max(u.decision_cutoff for u in plan.source.analysis.units) or any(
        r.ingested_at_utc > at
        or r.payload_hash != match_result_payload_sha256(r.home_goals, r.away_goals)
        for r in results
    ):
        raise ValueError("V2 normalized result hash/decision cutoff mismatch")
    if previous is not None:
        previous = revalidate(previous)
        if previous.plan != ArtifactRefV1.of(plan) or previous.settled_at_utc > at:
            raise ValueError("V2 correction crosses plan/time")
        for old in previous.results:
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
                    "V2 correction must directly supersede normalized result"
                )
    tickets = []
    for t in plan.tickets:
        matches = {c.match_id for c in t.candidate.choice_sets}
        missing = tuple(sorted(matches - set(by_match) - issue_ids))
        unsupported = tuple(sorted(matches & issue_ids))
        reason = (
            "UNSUPPORTED_SETTLEMENT_CASE"
            if unsupported
            else "MISSING_RESULT"
            if missing
            else "SETTLED"
        )
        atomic = ()
        if reason == "SETTLED":
            values = []
            for a in t.candidate.atomic_bets:
                won = all(
                    settle_market(
                        c.market_key,
                        by_match[c.match_id].home_goals,
                        by_match[c.match_id].away_goals,
                    )
                    == c.outcome
                    for c in a.legs
                )
                values.append(
                    ExpandedAtomicSettlementV2(
                        atomic_bet_id=a.artifact_id,
                        won=won,
                        stake_fen=200 * t.multiplier,
                        gross_payout_fen=a.gross_payout_fen * t.multiplier
                        if won
                        else 0,
                    )
                )
            atomic = tuple(values)
        tickets.append(
            TicketSettlementV2(
                ticket_id=t.artifact_id,
                reason=reason,
                atomic_results=atomic,
                missing_match_ids=missing,
                issue_match_ids=unsupported,
                stake_fen=t.stake_fen,
                gross_payout_fen=sum(a.gross_payout_fen for a in atomic)
                if reason == "SETTLED"
                else None,
            )
        )
    reasons = {t.reason for t in tickets}
    reason = (
        "UNSUPPORTED_SETTLEMENT_CASE"
        if "UNSUPPORTED_SETTLEMENT_CASE" in reasons
        else "MISSING_RESULT"
        if "MISSING_RESULT" in reasons
        else "SETTLED"
    )
    gross = sum(t.gross_payout_fen for t in tickets) if reason == "SETTLED" else None
    return StrategySettlementV2.freeze(
        plan=ArtifactRefV1.of(plan),
        settled_at_utc=at,
        previous=ArtifactRefV1.of(previous) if previous else None,
        results=results,
        issues=issues,
        tickets=tuple(tickets),
        reason=reason,
        budget_fen=plan.source.budget_fen,
        stake_fen=plan.total_stake_fen,
        cash_fen=plan.cash_fen,
        gross_payout_fen=gross,
        ending_capital_fen=plan.cash_fen + gross if gross is not None else None,
        profit_loss_fen=gross - plan.total_stake_fen if gross is not None else None,
    )
