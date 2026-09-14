import json

import pytest

from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.interfaces.cli import main
from tests.integration.test_strategy_pass_repository import seed, store_results


@pytest.mark.parametrize("pass_type,count", [("2X1", 1), ("3X4", 4), ("4X11", 11)])
def test_strategy_cli_build_show_and_settle_persisted_synthetic_parent(
    tmp_path, capsys, pass_type, count
):
    url = f"sqlite:///{(tmp_path / 'strategy.db').as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    sessions = create_session_factory(engine)
    _, artifacts = seed(sessions)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"pass_types": [pass_type]}), encoding="utf-8")
    output = tmp_path / "plan.json"
    args = [
        "strategy-pass",
        "build",
        "--database-url",
        url,
        "--analysis-run-id",
        artifacts.analysis_run.analysis_run_id,
        "--budget-fen",
        "10000",
        "--profile",
        str(profile),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    first = output.read_bytes()
    value = json.loads(first)
    assert value["tickets"] and all(
        len(t["candidate"]["atomic_bets"]) == count for t in value["tickets"]
    )
    assert main(args) == 0 and output.read_bytes() == first
    capsys.readouterr()
    assert (
        main(
            [
                "strategy-pass",
                "show",
                "--database-url",
                url,
                "--plan-id",
                value["plan_id"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == value
    all_results, when = store_results(sessions, artifacts)
    match_ids = {
        s["match_id"] for t in value["tickets"] for s in t["candidate"]["selections"]
    }
    result_args = [
        part
        for r in all_results
        if r.match_id in match_ids
        for part in ("--result-id", r.match_result_id)
    ]
    settle = [
        "strategy-pass",
        "settle",
        "--database-url",
        url,
        "--plan-id",
        value["plan_id"],
        "--as-of",
        when.isoformat(),
        *result_args,
    ]
    assert main(settle) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["policy_version"] == "THREE_WAY_SYSTEM_PASS_BACKTEST_V1"
    assert result["gross_payout_fen"] == sum(
        t["max_payout_fen"] for t in value["tickets"]
    )
    assert (
        main(
            [
                "strategy-pass",
                "settlement-show",
                "--database-url",
                url,
                "--settlement-id",
                result["settlement_id"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == result
    engine.dispose()


def test_default_profile_and_schema_match_example(capsys):
    from pathlib import Path
    from football_system.domain.strategy_pass import StrategyProfileV1

    assert main(["strategy-pass", "profile"]) == 0
    profile = StrategyProfileV1.model_validate_json(capsys.readouterr().out)
    assert profile == StrategyProfileV1.model_validate_json(
        Path("config/strategy_profile_v1.json").read_bytes()
    )
    assert main(["strategy-pass", "profile", "--print-schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["absolute_max_tickets"]["maximum"] == 8
