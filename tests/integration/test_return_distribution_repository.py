import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from football_system.application.market_v2 import PlanRequestV2
from football_system.application.return_distribution import EvaluateReturnRequestV1, OptimizeReturnRequestV1, ReturnDistributionService
from football_system.domain.archive import canonical_json
from football_system.domain.return_distribution import ReturnDistributionMetricsV1
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository, rows_for_return
from football_system.infrastructure.database.return_distribution_schema import return_distribution_triggers
from football_system.infrastructure.files.return_distribution import default_return_configuration
from scripts.market_expansion_acceptance import request_for_counts
from tests.integration.test_market_v2_repository import prepared  # noqa: F401


@pytest.fixture(scope="module")
def returns(prepared):  # noqa: F811 - reused pytest fixture
    _, sessions, service, _, _, _, fusion, _ = prepared
    plan = service.plan(PlanRequestV2(fusion_id=fusion.artifact_id, budget_fen=10000, requests=(request_for_counts((1, 2)),)))
    policy, objective = default_return_configuration()
    repo = SqlAlchemyReturnDistributionRepository(sessions)
    app = ReturnDistributionService(repo, policy, objective)
    evaluation = app.evaluate(EvaluateReturnRequestV1(plan_id=plan.artifact_id, allocations=(
        {"ticket_candidate_id": plan.candidates[0].artifact_id, "multiplier": 1},)))
    return repo, app, plan, evaluation


def test_full_graph_exact_retry_and_run_replay(returns):
    repo, app, plan, e = returns
    assert repo.load(e.artifact_id) == e
    assert repo.save(e) == e
    assert e.status == "AVAILABLE"
    run = app.optimize(OptimizeReturnRequestV1(plan_id=plan.artifact_id))
    assert repo.load(run.artifact_id) == run
    assert app.optimize(OptimizeReturnRequestV1(plan_id=plan.artifact_id)) == run
    with repo._sessions() as session:
        assert not session.execute(text("PRAGMA foreign_key_check")).all()
        assert session.scalar(text("SELECT COUNT(*) FROM rd_support WHERE parent_id=:id"), {"id": e.distribution.artifact_id}) == len(e.distribution.support)


@pytest.mark.parametrize("table", ["rd_artifacts", "rd_support", "rd_relevant_states", "rd_distribution_allocations", "rd_metrics", "rd_seals"])
def test_update_delete_replace_are_blocked(returns, table):
    repo, _, _, _ = returns
    from football_system.infrastructure.database.models import Base
    model = Base.metadata.tables[table]
    with repo._sessions() as session:
        row = dict(session.execute(model.select()).mappings().first())
    for statement in (model.update().values({list(row)[0]: row[list(row)[0]]}), model.delete(), model.insert().prefix_with("OR REPLACE").values(row)):
        with pytest.raises(IntegrityError):
            with repo._sessions.begin() as session:
                session.execute(statement)


def test_typed_fk_and_deferred_completeness_prevent_partial_graph(returns):
    repo, _, _, _ = returns
    from football_system.domain.return_distribution import ReturnObjectiveProfileV1
    from football_system.infrastructure.database.models import Base
    profile = ReturnObjectiveProfileV1.freeze(max_optimizer_candidates=2)
    rows = rows_for_return(profile)
    with pytest.raises(IntegrityError):
        with repo._sessions.begin() as session:
            session.execute(Base.metadata.tables["rd_artifacts"].insert(), rows["rd_artifacts"])
    with pytest.raises(IntegrityError):
        with repo._sessions.begin() as session:
            session.execute(Base.metadata.tables["rd_artifacts"].insert(), rows["rd_artifacts"])
            session.execute(Base.metadata.tables["rd_seals"].insert(), rows["rd_seals"])
    assert Base.metadata.tables["rd_distribution_allocations"].c.candidate_id.references(Base.metadata.tables["mm_ticket_candidates"].c.artifact_id)
    assert Base.metadata.tables["rd_metrics"].c.distribution_id.references(Base.metadata.tables["rd_distributions"].c.artifact_id)


@pytest.mark.parametrize("table,column,value", [
    ("rd_support", "probability", "0.999"),
    ("rd_support", "gross_payout_fen", 999999),
    ("rd_distribution_allocations", "multiplier", 49),
    ("rd_match_states", "sp_id", "wrong-source"),
])
def test_q_corruption_is_detected_on_fresh_load(returns, table, column, value):
    repo, _, _, e = returns
    from football_system.infrastructure.database.models import Base
    model = Base.metadata.tables[table]
    # This is an isolated synthetic in-memory database. Simulate offline tampering,
    # then restore the exact row and triggers before yielding it to another test.
    with repo._sessions() as session:
        row = dict(session.execute(model.select()).mappings().first())
    keys = {c.name: row[c.name] for c in model.primary_key.columns}
    where = [model.c[k] == v for k, v in keys.items()]
    statements = {name: sql for name, sql in return_distribution_triggers().items() if f" ON {table} " in sql}
    engine = repo._sessions.kw["bind"]
    raw = engine.raw_connection()
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        for name in statements:
            raw.execute(f"DROP TRIGGER {name}")
        raw.commit()
        # Use SQLAlchemy values for quoting, with the already pinned connection.
        clause = model.update().where(*where).values({column: value}).compile(dialect=engine.dialect)
        raw.execute(str(clause), tuple(clause.params[k] for k in clause.positiontup))
        raw.commit()
    finally:
        raw.close()
    try:
        with pytest.raises((ValueError, KeyError)):
            repo.load(e.artifact_id)
    finally:
        raw = engine.raw_connection()
        try:
            raw.execute("PRAGMA foreign_keys=OFF")
            clause = model.update().where(*where).values(row).compile(dialect=engine.dialect)
            raw.execute(str(clause), tuple(clause.params[k] for k in clause.positiontup))
            for sql in statements.values():
                raw.execute(sql)
            raw.commit()
        finally:
            raw.close()
    assert repo.load(e.artifact_id) == e


def test_resealed_wrong_metric_is_rejected_by_source_replay_before_insert(returns):
    repo, _, _, e = returns
    body = e.metrics.model_dump(exclude={"artifact_id", "content_hash"})
    body["median_ending_capital_fen"] += 1
    wrong = ReturnDistributionMetricsV1.freeze(**body)
    with repo._sessions() as session:
        before = session.scalar(text("SELECT COUNT(*) FROM rd_artifacts"))
    with pytest.raises(ValueError, match="metrics differ"):
        repo.save(wrong)
    with repo._sessions() as session:
        assert session.scalar(text("SELECT COUNT(*) FROM rd_artifacts")) == before


def test_cli_evaluate_optimize_show_and_forbidden_overrides(returns, tmp_path, capsys):
    # CLI database E2E uses a copied synthetic database, not the shared fixture.
    repo, _, plan, _ = returns
    engine = repo._sessions.kw["bind"]
    import sqlite3
    path = tmp_path / "cli.db"
    raw = engine.raw_connection()
    target = sqlite3.connect(path)
    raw.driver_connection.backup(target)
    target.close()
    raw.close()
    # create_schema databases have no alembic revision marker. Stamp exact current
    # runtime schema before invoking the normal CLI migration check.
    from tests.integration.test_market_v2_upgrade import config_for
    from alembic import command
    command.stamp(config_for(path), "head")
    from football_system.interfaces.cli import main
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"plan_id": plan.artifact_id}), encoding="utf-8")
    args = ["return-distribution", "optimize", "--database-url", f"sqlite:///{path.as_posix()}", "--input", str(request)]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["assumptions"] == ["INDEPENDENT_MATCHES_V1", "CROSS_MARKET_SAME_MATCH_UNSUPPORTED"]
    assert result["budget_fen"] == 10000
    assert main(["return-distribution", "show", "--database-url", f"sqlite:///{path.as_posix()}", "--artifact-id", result["artifact_id"]]) == 0
    assert json.loads(capsys.readouterr().out) == result
    request.write_text(json.dumps({"plan_id": plan.artifact_id, "budget_fen": 999999}), encoding="utf-8")
    assert main(args) == 1
    assert "budget_fen" in capsys.readouterr().err
    assert canonical_json(repo.load_plan(plan.artifact_id)) == canonical_json(plan)


def test_d_sealed_v2_same_match_cross_market_rejected_before_enumeration(prepared, monkeypatch):  # noqa: F811
    _, sessions, service, _, _, _, fusion, _ = prepared
    from football_system.domain.market_v2 import MarketKeyV2
    from football_system.domain.strategy_pass_v2 import MatchChoiceRequestV1, TicketRequestV2
    from football_system.domain.services.return_distribution import evaluate_portfolio
    from football_system.domain.services import return_distribution as kernel
    first = request_for_counts((1, 1))
    second = TicketRequestV2(pass_type="2X1", choices=(
        MatchChoiceRequestV1(match_id="mm-target-0", market_key=MarketKeyV2(market_type="CORRECT_SCORE"), outcomes=("SCORE_3_1",)),
        MatchChoiceRequestV1(match_id="mm-target-2", market_key=MarketKeyV2(market_type="CORRECT_SCORE"), outcomes=("SCORE_3_1",)),
    ))
    plan = service.plan(PlanRequestV2(fusion_id=fusion.artifact_id, budget_fen=10000, requests=(first, second)))
    assert len(plan.candidates) == 2
    monkeypatch.setattr(kernel, "distribution_values", lambda *a, **kw: pytest.fail("cross-market portfolio reached enumeration"))
    policy, objective = default_return_configuration()
    evaluated = evaluate_portfolio(plan, [{"ticket_candidate_id": c.artifact_id, "multiplier": 1} for c in plan.candidates], policy=policy, objective=objective)
    assert evaluated.status == "DISTRIBUTION_UNAVAILABLE"
    assert evaluated.reason == "CROSS_MARKET_JOINT_UNAVAILABLE" and evaluated.distribution is None
    # Saving the unavailable result replays the guard, without persisting fake probabilities.
    repo = SqlAlchemyReturnDistributionRepository(sessions)
    assert repo.save(evaluated) == evaluated
    assert repo.load(evaluated.artifact_id) == evaluated


@pytest.mark.parametrize("counts", [(1, 2), (1, 2, 2), (1, 2, 2, 1)])
def test_t_mixed_markets_all_passes_match_formal_settlement(prepared, counts):  # noqa: F811
    _, _, service, _, _, _, fusion, _ = prepared
    from football_system.domain.services.return_distribution import evaluate_portfolio, payout_for_assignment
    from football_system.domain.services.settlement_v2 import settle_strategy_v2
    from football_system.domain.market_v2 import settle_market
    from tests.unit.test_strategy_pass_v2 import match_results
    plan = service.plan(PlanRequestV2(fusion_id=fusion.artifact_id, budget_fen=10000, requests=(request_for_counts(counts),)))
    policy, objective = default_return_configuration()
    evaluated = evaluate_portfolio(plan, [{"ticket_candidate_id": t.candidate.artifact_id, "multiplier": t.multiplier} for t in plan.tickets], policy=policy, objective=objective)
    assert evaluated.status == "AVAILABLE" and plan.tickets
    for concrete in (((3, 1), (2, 1), (4, 1), (0, 2)), ((0, 1), (0, 0), (6, 1), (2, 0))):
        scores = {f"mm-target-{i}": score for i, score in enumerate(concrete[:len(counts)])}
        results, at = match_results(plan, scores)
        settled = settle_strategy_v2(plan, results, at)
        assignment = {m.match_id: settle_market(m.market_key, *scores[m.match_id]) for m in evaluated.distribution.matches}
        gross = payout_for_assignment(evaluated.distribution, assignment)
        assert gross == settled.gross_payout_fen
        assert evaluated.distribution.cash_fen + gross == settled.ending_capital_fen


@pytest.mark.parametrize("outcome,score", [("HOME_OTHER", (6, 1)), ("DRAW_OTHER", (4, 4)), ("AWAY_OTHER", (1, 6))])
def test_t_handicap_and_other_worlds_match_settlement(prepared, outcome, score):  # noqa: F811
    _, _, service, _, _, _, fusion, _ = prepared
    from football_system.domain.market_v2 import MarketKeyV2, settle_market
    from football_system.domain.strategy_pass_v2 import MatchChoiceRequestV1, TicketRequestV2
    from football_system.domain.services.return_distribution import evaluate_portfolio, payout_for_assignment
    from football_system.domain.services.settlement_v2 import settle_strategy_v2
    from tests.unit.test_strategy_pass_v2 import match_results
    request = TicketRequestV2(pass_type="2X1", choices=(
        MatchChoiceRequestV1(match_id="mm-target-0", market_key=MarketKeyV2(market_type="HANDICAP_THREE_WAY", home_handicap=-1), outcomes=("AWAY_WIN",)),
        MatchChoiceRequestV1(match_id="mm-target-1", market_key=MarketKeyV2(market_type="CORRECT_SCORE"), outcomes=(outcome,)),
    ))
    plan = service.plan(PlanRequestV2(fusion_id=fusion.artifact_id, budget_fen=10000, requests=(request,)))
    policy, objective = default_return_configuration()
    evaluated = evaluate_portfolio(plan, [{"ticket_candidate_id": t.candidate.artifact_id, "multiplier": t.multiplier} for t in plan.tickets], policy=policy, objective=objective)
    assert evaluated.status == "AVAILABLE" and plan.tickets
    scores = {"mm-target-0": (0, 0), "mm-target-1": score}
    results, at = match_results(plan, scores)
    settled = settle_strategy_v2(plan, results, at)
    assignment = {m.match_id: settle_market(m.market_key, *scores[m.match_id]) for m in evaluated.distribution.matches}
    assert payout_for_assignment(evaluated.distribution, assignment) == settled.gross_payout_fen
