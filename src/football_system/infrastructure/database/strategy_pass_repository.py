"""Audited source reconstruction and append-only exact-replay strategy storage."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json

from sqlalchemy import select

from football_system.domain.archive import canonical_json
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.services.betting import build_selection_candidates
from football_system.domain.services.strategy_settlement import settle_strategy_plan
from football_system.domain.strategy_pass import (
    StrategyParentV1,
    StrategyPassPlanV1,
    StrategySourceV1,
    digest,
    fresh,
)
from football_system.domain.strategy_settlement import StrategySettlementV1
from football_system.infrastructure.database.models import (
    AnalysisRunMatchRecord,
    Base,
    BetCandidateRecord,
    FinalPredictionRecord,
    FusionRunRecord,
    MatchResultRecord,
    PortfolioRecord,
    PortfolioRevisionRecord,
    SportteryBonusSnapshotRecord,
)
from football_system.infrastructure.database.post_review_repositories import (
    _completed_run,
    _final_prediction,
    _fusion_run,
    _portfolio_revision,
    _sporttery_snapshot,
    _validate_hashed_canonical_text,
)
from football_system.infrastructure.database.historical_repositories import (
    _match_result,
)
from football_system.infrastructure.database.production_audit_repository import (
    ProductionAuditGuard,
)
from football_system.infrastructure.database.strategy_pass_schema import (
    PLAN,
    SEAL,
    SELECTION,
    TICKET,
    ATOMIC,
    LEG,
    SETTLEMENT,
    RESULT,
    RESULT_SEAL,
)


class SqlAlchemyStrategyPassRepository:
    def __init__(self, session_factory, *, audit_repository=None):
        self._audit = ProductionAuditGuard(session_factory, audit_repository)

    @contextmanager
    def operation(self, *, kind=None, source_id=None, plan_id=None, settlement_id=None):
        with self._audit.transaction() as session:
            if settlement_id is not None:
                plan_id = _one(session, SETTLEMENT, settlement_id=settlement_id)[
                    "plan_id"
                ]
            if plan_id is not None:
                run_id = _one(session, PLAN, plan_id=plan_id)["parent_analysis_run_id"]
            elif kind == "PORTFOLIO_REVISION":
                revision = session.get(PortfolioRevisionRecord, source_id)
                if revision is None:
                    raise KeyError("unknown PortfolioRevision")
                run_id = revision.parent_analysis_run_id
            elif kind == "ANALYSIS_RUN":
                run_id = source_id
            else:
                raise ValueError(
                    "strategy requires an exact AnalysisRun or PortfolioRevision source"
                )
            self._audit.require(session, run_id)
            yield session

    def load_source(self, kind, source_id, budget_fen):
        with self.operation(kind=kind, source_id=source_id) as session:
            return _source(session, kind, source_id, budget_fen)

    def save_plan(self, plan):
        plan = fresh(plan)
        parent = plan.source.parent
        with self.operation(kind=parent.kind, source_id=parent.source_id) as session:
            source = _source(
                session, parent.kind, parent.source_id, plan.source.budget_fen
            )
            if canonical_json(source) != canonical_json(plan.source):
                raise ValueError(
                    "strategy source differs from frozen parent; caller cannot change gates/budget"
                )
            rows = plan_rows(plan)
            if _rows(session, PLAN, plan_id=plan.plan_id):
                _verify_rows(session, rows, "plan_id", plan.plan_id)
                return plan
            for name, values in rows.items():
                if values:
                    session.execute(Base.metadata.tables[name].insert(), values)
            _verify_rows(session, rows, "plan_id", plan.plan_id)
            return plan

    def load_plan(self, plan_id):
        with self.operation(plan_id=plan_id) as session:
            return _load_plan(session, plan_id)

    def load_results(self, plan_id, result_ids):
        with self.operation(plan_id=plan_id) as session:
            if len(set(result_ids)) != len(result_ids):
                raise ValueError("duplicate result ID")
            return _results(session, result_ids)

    def save_settlement(self, settlement):
        settlement = fresh(settlement)
        with self.operation(plan_id=settlement.plan_id) as session:
            plan = _load_plan(session, settlement.plan_id)
            _verify_settlement(session, plan, settlement)
            rows = settlement_rows(settlement)
            if _rows(session, SETTLEMENT, settlement_id=settlement.settlement_id):
                _verify_rows(session, rows, "settlement_id", settlement.settlement_id)
                return settlement
            for name, values in rows.items():
                if values:
                    session.execute(Base.metadata.tables[name].insert(), values)
            _verify_rows(session, rows, "settlement_id", settlement.settlement_id)
            return settlement

    def load_settlement(self, settlement_id):
        with self.operation(settlement_id=settlement_id) as session:
            return _load_settlement(session, settlement_id)


def _source(session, kind, source_id, budget_fen):
    if type(budget_fen) is not int or budget_fen < 0:
        raise ValueError("budget must be an existing integer-fen parent budget")
    revision = None
    fusion = None
    if kind == "PORTFOLIO_REVISION":
        record = session.get(PortfolioRevisionRecord, source_id)
        if record is None:
            raise KeyError("unknown PortfolioRevision")
        revision = _portfolio_revision(session, record)
        run = _completed_run(session, revision.parent_analysis_run_id)
        fusion_record = session.get(FusionRunRecord, revision.fusion_run_id)
        if fusion_record is None:
            raise ValueError("revision is missing its frozen FusionRun")
        fusion = _fusion_run(session, fusion_record)
        config = json.loads(revision.config_json)
        predictions = revision.final_predictions
        result_by_id = {r.fusion_result_id: r for r in fusion.results}
        for p in predictions:
            result = result_by_id.get(p.llm_assessment_id)
            if (
                result is None
                or result.match_id != p.match_id
                or result.market != p.market
                or result.p_final != p.probabilities
            ):
                raise ValueError(
                    "revision prediction differs from frozen FusionRun P_final"
                )
        portfolios = revision.portfolios
    elif kind == "ANALYSIS_RUN":
        run = _completed_run(session, source_id)
        frozen = json.loads(run.config_json)
        config = dict(frozen["settings"])
        config["analysis"] = {
            "min_selection_ev": frozen["request"]["min_selection_ev"],
            "min_ticket_roi": frozen["request"]["min_ticket_roi"],
        }
        if budget_fen not in frozen["request"]["budgets_fen"]:
            raise ValueError("budget is absent from parent request")
        predictions = tuple(
            _final_prediction(session, p)
            for p in session.scalars(
                select(FinalPredictionRecord)
                .where(FinalPredictionRecord.analysis_run_id == source_id)
                .order_by(FinalPredictionRecord.internal_match_id)
            )
        )
        portfolios = tuple(
            session.scalars(
                select(PortfolioRecord).where(
                    PortfolioRecord.analysis_run_id == source_id
                )
            )
        )
    else:
        raise ValueError("unsupported strategy source kind")
    portfolio = next((p for p in portfolios if p.budget_fen == budget_fen), None)
    if portfolio is None:
        raise ValueError("budget is absent from frozen parent portfolios")
    constraints = PortfolioConstraints.model_validate(config["portfolio"])
    stored_constraints = (
        portfolio.constraints
        if revision
        else PortfolioConstraints.model_validate_json(portfolio.strategy_config_json)
    )
    if constraints != stored_constraints:
        raise ValueError("parent constraints disagree with frozen configuration")
    raw_rules = config["sporttery"]
    rules = SportteryRules(
        version=raw_rules["rules_version"],
        base_stake_fen=raw_rules["base_stake_fen"],
        max_multiplier=raw_rules["max_multiplier"],
        max_ticket_stake_fen=raw_rules["max_ticket_stake_fen"],
    )
    threshold = Decimal(config["analysis"]["min_selection_ev"])
    snapshots = []
    expected = []
    for p in predictions:
        context = session.get(AnalysisRunMatchRecord, (run.analysis_run_id, p.match_id))
        if context is None:
            raise ValueError("strategy prediction has no frozen match context")
        _validate_hashed_canonical_text(
            context.context_json, context.context_hash, "strategy source context"
        )
        snapshot_record = session.get(
            SportteryBonusSnapshotRecord, context.sporttery_bonus_snapshot_id
        )
        if snapshot_record is None:
            raise ValueError("strategy context is missing its frozen odds")
        snapshot = _sporttery_snapshot(session, snapshot_record)
        snapshots.append(snapshot)
        expected.extend(build_selection_candidates(p, snapshot, threshold))
    expected = tuple(sorted(expected, key=lambda c: c.candidate_id))
    if revision:
        if expected != tuple(
            sorted(revision.selection_candidates, key=lambda c: c.candidate_id)
        ):
            raise ValueError("revision candidate gate differs from sealed parent")
        # Preserve original Decimal encodings used by the immutable revision.
        selections = tuple(
            sorted(revision.selection_candidates, key=lambda c: c.candidate_id)
        )
    else:
        records = tuple(
            session.scalars(
                select(BetCandidateRecord)
                .where(BetCandidateRecord.analysis_run_id == source_id)
                .order_by(BetCandidateRecord.candidate_id)
            )
        )
        if len(records) != len(expected):
            raise ValueError("base candidate coverage differs from frozen predictions")
        for c, r in zip(expected, records, strict=True):
            pairs = (
                (c.candidate_id, r.candidate_id),
                (c.analysis_run_id, r.analysis_run_id),
                (c.match_id, r.internal_match_id),
                (c.final_prediction_id, r.final_prediction_id),
                (c.sporttery_bonus_snapshot_id, r.sporttery_bonus_snapshot_id),
                (c.market.canonical, r.market_key),
                (c.selection.value, r.selection_key),
                (c.probability, r.probability_used),
                (c.fixed_bonus, r.fixed_bonus),
                (c.break_even_probability, r.break_even_probability),
                (c.ev, r.ev),
                (c.status.value, r.eligibility_status),
                (c.rejection_code, r.rejection_code),
            )
            if any(a != b for a, b in pairs):
                raise ValueError(
                    "base candidate differs from frozen probability/odds/eligibility gate"
                )
        selections = expected
    run_hash = digest(
        "ANALYSIS_RUN_SNAPSHOT_V1",
        {c.name: getattr(run, c.name) for c in run.__table__.columns},
    )
    parent = StrategyParentV1(
        kind=kind,
        analysis_run_id=run.analysis_run_id,
        analysis_run_hash=run_hash,
        source_id=source_id,
        source_hash=revision.revision_hash if revision else run_hash,
        portfolio_revision_id=source_id if revision else None,
        fusion_run_id=fusion.fusion_run_id if fusion else None,
        fusion_run_hash=digest("FUSION_RUN_SNAPSHOT_V1", fusion) if fusion else None,
        as_of_at_utc=run.as_of_at_utc,
    )
    return StrategySourceV1(
        parent=parent,
        selections=selections,
        odds_snapshots=tuple(sorted(snapshots, key=lambda s: s.snapshot_id)),
        budget_fen=budget_fen,
        rules=rules,
        constraints=constraints,
        min_selection_ev=threshold,
        min_ticket_roi=config["analysis"]["min_ticket_roi"],
    )


def plan_rows(plan):
    parent = plan.source.parent
    result = {
        PLAN: [
            dict(
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                parent_analysis_run_id=parent.analysis_run_id,
                source_kind=parent.kind,
                source_id=parent.source_id,
                portfolio_revision_id=parent.portfolio_revision_id,
                fusion_run_id=parent.fusion_run_id,
                budget_fen=plan.source.budget_fen,
                total_stake_fen=plan.total_stake_fen,
                ticket_count=len(plan.tickets),
                artifact_json=canonical_json(plan),
            )
        ],
        SELECTION: [],
        TICKET: [],
        ATOMIC: [],
        LEG: [],
        SEAL: [dict(plan_id=plan.plan_id)],
    }
    for no, s in enumerate(plan.source.selections):
        result[SELECTION].append(
            dict(
                plan_id=plan.plan_id,
                selection_id=s.candidate_id,
                selection_no=no,
                base_candidate_id=s.candidate_id
                if parent.kind == "ANALYSIS_RUN"
                else None,
                internal_match_id=s.match_id,
                snapshot_id=s.sporttery_bonus_snapshot_id,
                status=s.status.value,
                artifact_json=canonical_json(s),
            )
        )
    for t in plan.tickets:
        c = t.candidate
        result[TICKET].append(
            dict(
                plan_id=plan.plan_id,
                ticket_id=t.ticket_id,
                ticket_no=t.ticket_no,
                candidate_id=c.candidate_id,
                equivalence_hash=c.equivalence_hash,
                pass_type=c.pass_type.value,
                role=t.role.value,
                multiplier=t.multiplier,
                stake_fen=t.stake_fen,
                max_payout_fen=t.max_payout_fen,
                atomic_count=c.atomic_bet_count,
                artifact_json=canonical_json(t),
            )
        )
        selection_by_id = {s.candidate_id: s for s in c.selections}
        for no, a in enumerate(c.atomic_bets):
            result[ATOMIC].append(
                dict(
                    plan_id=plan.plan_id,
                    ticket_id=t.ticket_id,
                    atomic_bet_id=a.atomic_bet_id,
                    atomic_no=no,
                    leg_count=len(a.selection_ids),
                    gross_payout_fen=a.gross_payout_fen,
                    artifact_json=canonical_json(a),
                )
            )
            for leg_no, sid in enumerate(a.selection_ids):
                s = selection_by_id[sid]
                result[LEG].append(
                    dict(
                        plan_id=plan.plan_id,
                        ticket_id=t.ticket_id,
                        atomic_bet_id=a.atomic_bet_id,
                        leg_no=leg_no,
                        selection_id=sid,
                        internal_match_id=s.match_id,
                        snapshot_id=s.sporttery_bonus_snapshot_id,
                        fixed_bonus=str(s.fixed_bonus),
                    )
                )
    return result


def settlement_rows(value):
    return {
        SETTLEMENT: [
            dict(
                settlement_id=value.settlement_id,
                plan_id=value.plan_id,
                settlement_hash=value.settlement_hash,
                supersedes_settlement_id=value.supersedes_settlement_id,
                artifact_json=canonical_json(value),
            )
        ],
        RESULT: [
            dict(
                settlement_id=value.settlement_id,
                match_result_id=r.match_result_id,
                result_no=no,
                internal_match_id=r.match_id,
                artifact_json=canonical_json(r),
            )
            for no, r in enumerate(value.match_results)
        ],
        RESULT_SEAL: [dict(settlement_id=value.settlement_id)],
    }


def _rows(session, name, **filters):
    table = Base.metadata.tables[name]
    return (
        session.execute(
            select(table).where(*(table.c[k] == v for k, v in filters.items()))
        )
        .mappings()
        .all()
    )


def _one(session, name, **filters):
    rows = _rows(session, name, **filters)
    if len(rows) != 1:
        raise KeyError(f"unknown or incomplete {name} artifact")
    return rows[0]


def _verify_rows(session, expected, key, value):
    for name, rows in expected.items():
        stored = _rows(session, name, **{key: value})
        if sorted(canonical_json(dict(r)) for r in stored) != sorted(
            canonical_json(r) for r in rows
        ):
            raise ValueError(f"immutable strategy graph mismatch: {name}")


def _load_plan(session, plan_id):
    record = _one(session, PLAN, plan_id=plan_id)
    plan = StrategyPassPlanV1.model_validate_json(record["artifact_json"])
    _verify_rows(session, plan_rows(plan), "plan_id", plan_id)
    parent = plan.source.parent
    if canonical_json(plan.source) != canonical_json(
        _source(session, parent.kind, parent.source_id, plan.source.budget_fen)
    ):
        raise ValueError("stored strategy no longer matches its frozen parent")
    return plan


def _results(session, ids):
    values = []
    for result_id in ids:
        row = session.get(MatchResultRecord, result_id)
        if row is None:
            raise KeyError("unknown normalized MatchResult")
        values.append(_match_result(session, row))
    return tuple(sorted(values, key=lambda r: r.match_id))


def _verify_settlement(session, plan, value):
    results = _results(session, tuple(r.match_result_id for r in value.match_results))
    if results != value.match_results:
        raise ValueError("settlement result differs from stored normalized result")
    if (
        value.supersedes_settlement_id is not None
        and _one(session, SETTLEMENT, settlement_id=value.supersedes_settlement_id)[
            "plan_id"
        ]
        != plan.plan_id
    ):
        raise ValueError("settlement correction crosses frozen plan")
    previous = (
        _load_settlement(session, value.supersedes_settlement_id)
        if value.supersedes_settlement_id
        else None
    )
    expected = settle_strategy_plan(
        plan, results, value.settled_at_utc, issues=value.issues, previous=previous
    )
    if canonical_json(expected) != canonical_json(value):
        raise ValueError("stored strategy settlement differs from exact payout replay")


def _load_settlement(session, settlement_id):
    record = _one(session, SETTLEMENT, settlement_id=settlement_id)
    value = StrategySettlementV1.model_validate_json(record["artifact_json"])
    _verify_rows(session, settlement_rows(value), "settlement_id", settlement_id)
    _verify_settlement(session, _load_plan(session, value.plan_id), value)
    return value
