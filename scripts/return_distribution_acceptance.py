"""Installed-package return acceptance using already sealed synthetic V2 inputs.

No tests imports, training/inference, provider or LLM calls in this scaffold.
The existing wheel V2 scaffold supplies the source database and frozen plans.
"""

import argparse
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sqlite3

from football_system.domain.archive import canonical_json
from football_system.domain.market_v2 import settle_market
from football_system.domain.return_distribution import ReturnEvaluationV1, ReturnSupportPointV1
from football_system.domain.services.return_distribution import metric_values, payout_for_assignment
from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.interfaces.cli import main as cli, _resource_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-work-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_work_dir.resolve()
    work = args.work_dir.resolve()
    if not (source / "acceptance.db").is_file():
        raise ValueError("existing sealed synthetic V2 acceptance database required")
    work.mkdir(exist_ok=False)
    database = work / "acceptance.db"
    with sqlite3.connect((source / "acceptance.db").as_uri()+"?mode=ro", uri=True) as old, sqlite3.connect(database) as new:
        old.backup(new)
    url = "sqlite:///" + database.as_posix()
    engine = create_database_engine(url)
    sessions = create_session_factory(engine)
    repo = SqlAlchemyReturnDistributionRepository(sessions)
    old_repo = SqlAlchemyMultiMarketRepository(sessions)

    def invoke(command, request, label):
        path, output = work / (label+"-request.json"), work / (label+".json")
        with path.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(request))
        assert cli(["return-distribution", command, "--database-url", url, "--input", str(path), "--output", str(output)]) == 0
        return json.loads(output.read_bytes())

    summary = {"classification": "SYNTHETIC_ONLY", "http_sends": 0, "cases": {}}
    try:
        for pass_type in ("2X1", "3X4", "4X11"):
            source_plan = json.loads((source / ("plan-"+pass_type+".json")).read_bytes())
            plan = old_repo.load(source_plan["artifact_id"], "STRATEGY_PASS_PLAN_V2")
            request = {"plan_id": plan.artifact_id, "allocations": [
                {"ticket_candidate_id": t.candidate.artifact_id, "multiplier": t.multiplier} for t in plan.tickets]}
            evaluated = invoke("evaluate", request, "evaluate-"+pass_type)
            e = ReturnEvaluationV1.model_validate(evaluated["artifact"])
            assert e.status == "AVAILABLE" and repo.load(e.artifact_id) == e
            assert invoke("evaluate", request, "retry-"+pass_type) == evaluated
            with localcontext() as context:
                context.prec = 512
                assert sum(p.probability for p in e.distribution.support) == 1
            settlement = json.loads((source / ("settle-"+pass_type+".json")).read_bytes())
            actual = old_repo.load(settlement["artifact_id"], "STRATEGY_SETTLEMENT_V2")
            by_match = {r.match_id: r for r in actual.results}
            assignment = {m.match_id: settle_market(m.market_key, by_match[m.match_id].home_goals, by_match[m.match_id].away_goals) for m in e.distribution.matches}
            gross = payout_for_assignment(e.distribution, assignment)
            assert gross == actual.gross_payout_fen and e.distribution.cash_fen+gross == actual.ending_capital_fen
            optimized = invoke("optimize", {"plan_id": plan.artifact_id}, "optimize-"+pass_type)
            assert optimized["status"] in {"OPTIMIZED", "NO_BET"}
            assert optimized["assumptions"] == ["INDEPENDENT_MATCHES_V1", "CROSS_MARKET_SAME_MATCH_UNSUPPORTED"]
            out = work / ("show-"+pass_type+".json")
            assert cli(["return-distribution", "show", "--database-url", url, "--artifact-id", optimized["artifact_id"], "--output", str(out)]) == 0
            assert json.loads(out.read_bytes()) == optimized
            baseline = invoke("evaluate", {"plan_id": plan.artifact_id, "allocations": []}, "no-bet-"+pass_type)
            assert baseline["cash_fen"] == plan.source.budget_fen and baseline["metrics"]["expected_profit_fen"] == "0"
            summary["cases"][pass_type] = {"support_count": len(e.distribution.support), "optimizer_status": optimized["status"], "settlement_gross_fen": gross}
        fixture = json.loads((_resource_root() / "data/fixtures/return_distribution_v1.json").read_bytes())
        assert fixture["data_classification"] == "SYNTHETIC_ONLY" and set(fixture["cases"]) == set("ABCDEFGHIJKLMNOPQRST")
        case = fixture["cases"]["J"]
        metrics = metric_values(tuple(ReturnSupportPointV1(gross_payout_fen=g, probability=p) for g, p in case["ending_support"]), case["budget_fen"], case["budget_fen"], 0)
        assert all(metrics[k] == (Decimal(v) if isinstance(v, str) else v) for k, v in case["expected"].items())
        with (work / "acceptance-summary.json").open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(summary))
        print("RETURN_DISTRIBUTION_V1_INSTALLED_ACCEPTANCE_PASS")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
