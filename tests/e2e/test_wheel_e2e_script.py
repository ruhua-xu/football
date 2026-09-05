from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from football_system.interfaces.cli import main as cli_main
from scripts import wheel_e2e


ROOT = Path(__file__).resolve().parents[2]


def _installed_resource_paths(data_files: dict[str, list[str]]) -> set[str]:
    resources = set()
    for destination, sources in data_files.items():
        relative_destination = Path(destination).relative_to(
            "football_system_resources"
        )
        for source in sources:
            resources.add((relative_destination / Path(source).name).as_posix())
    return resources


def test_setuptools_data_files_are_explicit_complete_and_scoped() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    data_files = project["tool"]["setuptools"]["data-files"]
    declared_sources = {
        source for sources in data_files.values() for source in sources
    }

    assert project["project"]["version"] == "0.5.0"
    assert _installed_resource_paths(data_files) == set(
        wheel_e2e.EXPECTED_RESOURCE_FILES
    )
    assert not any(set(source) & set("*?[]") for source in declared_sources)
    assert {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "migrations" / "versions").glob("*.py")
    } <= declared_sources
    assert {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "data" / "fixtures" / "historical_acceptance").glob(
            "**/*.json"
        )
    } <= declared_sources
    assert not any(
        source.startswith(("yaoqiu/", "scripts/", "data/raw/"))
        or source.endswith((".db", ".env"))
        for source in declared_sources
    )


@pytest.mark.parametrize(
    ("arguments", "optional_fragments"),
    (
        (
            ["historical-archive", "validate", "--help"],
            ("[--archive ARCHIVE]",),
        ),
        (
            ["historical-archive", "import", "--help"],
            ("[--archive ARCHIVE]",),
        ),
        (
            ["backtest", "run", "--help"],
            ("[--archive ARCHIVE]", "[--fixture-config FIXTURE_CONFIG]"),
        ),
    ),
)
def test_historical_default_paths_are_optional_and_warn_in_help(
    arguments: list[str],
    optional_fragments: tuple[str, ...],
    capsys,
) -> None:
    with pytest.raises(SystemExit) as error:
        cli_main(arguments)
    assert error.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert all(fragment in output for fragment in optional_fragments)
    assert "SYNTHETIC ACCEPTANCE DATA" in output
    assert "NOT REAL HISTORICAL PERFORMANCE" in output


def test_wheel_script_reports_missing_wheel_without_building(tmp_path, capsys) -> None:
    missing = tmp_path / "football_system-0.5.0-py3-none-any.whl"
    assert wheel_e2e.main([str(missing)]) == 1
    assert "wheel does not exist" in capsys.readouterr().err


def test_wheel_discovery_requires_exactly_one_candidate(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(wheel_e2e.WheelE2EError, match="found 0"):
        wheel_e2e._resolve_wheel(None, tmp_path)

    first = dist / "football_system-0.5.0-py3-none-any.whl"
    first.write_bytes(b"not opened by discovery")
    assert wheel_e2e._resolve_wheel(None, tmp_path) == first.resolve()

    (dist / "football_system-0.5.0-2-py3-none-any.whl").write_bytes(b"second")
    with pytest.raises(wheel_e2e.WheelE2EError, match="found 2"):
        wheel_e2e._resolve_wheel(None, tmp_path)
