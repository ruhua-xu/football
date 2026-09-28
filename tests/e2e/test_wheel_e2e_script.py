from __future__ import annotations

import tomllib
import subprocess
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

    assert project["project"]["version"] == "1.2.0"
    assert "tzdata==2025.2" in project["project"]["dependencies"]
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


def test_candidate_resources_preserve_exact_v110_manifest_and_add_one_migration():
    baseline = tomllib.loads(subprocess.check_output(
        ["git", "show", "5ed940a8af8077be80549603a2da38aea77fc1bf:pyproject.toml"], cwd=ROOT
    ).decode("utf-8"))
    old = _installed_resource_paths(baseline["tool"]["setuptools"]["data-files"])
    new = set(wheel_e2e.EXPECTED_RESOURCE_FILES)
    # The actual v1.1.0 baseline has 86 resources, rather than the earlier 83 count.
    assert len(old) == 86 and len(new) == 87
    assert old <= new
    assert new - old == {"migrations/versions/7d96abc3840f_add_openfootball_production_binding.py"}
    assert wheel_e2e.EXPECTED_MIGRATION_HEAD == "7d96abc3840f"


def test_ci_and_isolated_wheel_install_the_same_real_pinned_timezone_dependency():
    requirements = (ROOT / "config/openfootball_snapshot_requirements.txt").read_text(encoding="utf-8")
    packages = [line.strip() for line in requirements.splitlines() if line.strip() and not line.startswith("#")]
    assert packages == [wheel_e2e.PINNED_TIMEZONE_DEPENDENCY] == ["tzdata==2025.2"]
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "-r config/openfootball_snapshot_requirements.txt" in workflow


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
    missing = tmp_path / "football_system-1.2.0-py3-none-any.whl"
    assert wheel_e2e.main([str(missing)]) == 1
    assert "wheel does not exist" in capsys.readouterr().err


def test_wheel_discovery_requires_exactly_one_candidate(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(wheel_e2e.WheelE2EError, match="found 0"):
        wheel_e2e._resolve_wheel(None, tmp_path)

    first = dist / "football_system-1.2.0-py3-none-any.whl"
    first.write_bytes(b"not opened by discovery")
    assert wheel_e2e._resolve_wheel(None, tmp_path) == first.resolve()

    (dist / "football_system-1.2.0-2-py3-none-any.whl").write_bytes(b"second")
    with pytest.raises(wheel_e2e.WheelE2EError, match="found 2"):
        wheel_e2e._resolve_wheel(None, tmp_path)
