from pathlib import Path

import pytest

from football_system.interfaces.cli import main


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_cli_displays_full_analysis_and_database_counts(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = main(
        [
            "--database-url",
            database_url(tmp_path / "cli.db"),
            "--budget-yuan",
            "100",
            "200",
            "--analysis-run-id",
            "run-cli-main",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "比赛与概率（6 场）" in output
    assert "P_market" in output
    assert "P_quant" in output
    assert "P_final" in output
    assert "Selection EV" in output
    assert "简单2串1候选（10 个）" in output
    assert "Portfolio 预算=100.00元" in output
    assert "Portfolio 预算=200.00元" in output
    assert "SQLite 持久化计数" in output


def test_cli_displays_no_bet_example(tmp_path, capsys) -> None:
    result = main(
        [
            "--database-url",
            database_url(tmp_path / "cli-no-bet.db"),
            "--budget-yuan",
            "100",
            "--analysis-run-id",
            "run-cli-no-bet",
            "--no-bet-demo",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "状态=NO_BET" in output
    assert "NO_BET 原因: NO_BET_NO_VALUE" in output


@pytest.mark.parametrize(
    ("argument", "value"),
    (("--budget-yuan", "NaN"), ("--min-selection-ev", "Infinity")),
)
def test_cli_rejects_non_finite_numbers(argument, value) -> None:
    with pytest.raises(SystemExit) as error:
        main([argument, value])
    assert error.value.code == 2
