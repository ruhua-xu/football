import json
from pathlib import Path

from football_system.domain.archive import canonical_json
from football_system.domain.goal_model import PoissonGoalsConfigV1
from football_system.domain.strategy_pass_v2 import StrategyProfileV2
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.interfaces.cli import main
from scripts.market_expansion_acceptance import (
    seed_environment,
    build_fixture_analysis,
    request_for_counts,
)


def test_v2_config_examples_and_strict_schemas(capsys):
    for command, cls, path in (
        ("profile", StrategyProfileV2, "config/strategy_profile_v2.json"),
        ("poisson-config", PoissonGoalsConfigV1, "config/poisson_goals_v1.json"),
    ):
        assert main(["market-v2", command]) == 0
        assert cls.model_validate_json(
            capsys.readouterr().out
        ) == cls.model_validate_json(Path(path).read_bytes())
    for name in ("packet", "review", "distribution", "plan"):
        assert main(["market-v2", "schema", name]) == 0
        schema = json.loads(capsys.readouterr().out)
        assert schema["additionalProperties"] is False


def test_cli_plan_show_idempotency_and_no_database_output_alias(tmp_path, capsys):
    db = tmp_path / "cli.db"
    url = f"sqlite:///{db.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    sessions = create_session_factory(engine)
    _, results = seed_environment(sessions)
    service, analysis, packet, review, fusion = build_fixture_analysis(
        sessions, results
    )
    request = tmp_path / "request.json"
    request.write_text(
        canonical_json(
            dict(
                fusion_id=fusion.artifact_id,
                budget_fen=10000,
                requests=(request_for_counts((1, 2)),),
            )
        ),
        encoding="utf-8",
    )
    output = tmp_path / "plan.json"
    args = [
        "market-v2",
        "plan",
        "--database-url",
        url,
        "--input",
        str(request),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    original = output.read_bytes()
    assert main(args) == 0 and output.read_bytes() == original
    plan = json.loads(original)
    capsys.readouterr()
    assert (
        main(
            [
                "market-v2",
                "show",
                "--database-url",
                url,
                "--artifact-id",
                plan["artifact_id"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == plan
    assert (
        main(
            [
                "market-v2",
                "plan",
                "--database-url",
                url,
                "--input",
                str(request),
                "--output",
                str(db),
            ]
        )
        == 1
    )
    assert "alias" in capsys.readouterr().err
    assert (
        service.repository.load(plan["artifact_id"])
        .tickets[0]
        .candidate.expanded_atomic_bet_count
        == 2
    )
    engine.dispose()
