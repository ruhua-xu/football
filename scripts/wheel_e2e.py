from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.4.0"
EXPECTED_MIGRATION_HEAD = "6e4b1a9c2d73"
PROVIDER_CODE = "SYNTHETIC_ACCEPTANCE_V1"
QUANT_RUN_ID = "wheel-e2e-quant"
BLEND_RUN_ID = "wheel-e2e-blend"
WARNING_MARKERS = (
    "SYNTHETIC ACCEPTANCE DATA",
    "NOT REAL HISTORICAL PERFORMANCE",
)
REPORT_FIELD_MARKERS = (
    "- archive_schema_version: HISTORICAL_ARCHIVE_V1",
    "absolute_gap=",
    "- winning_ticket_count:",
)
RANKING_LANGUAGE = re.compile(
    r"\b(?:best|better|outperform(?:s|ed)?|ranked|ranking|winner)\b",
    re.IGNORECASE,
)

EXPECTED_RESOURCE_FILES = frozenset(
    {
        "alembic.ini",
        "config/backtest.toml",
        "config/live.toml",
        "config/mvp.toml",
        "data/fixtures/mvp_matches.json",
        "data/fixtures/historical_acceptance/acceptance_config.toml",
        "data/fixtures/historical_acceptance/fixtures.json",
        "data/fixtures/historical_acceptance/manual_quant.json",
        "data/fixtures/historical_acceptance/market_odds.json",
        "data/fixtures/historical_acceptance/match_results.json",
        "data/fixtures/historical_acceptance/provider_mappings.json",
        "data/fixtures/historical_acceptance/sporttery_bonus.json",
        (
            "data/fixtures/historical_acceptance/invalid_examples/"
            "mapping_conflict/provider_mappings.json"
        ),
        (
            "data/fixtures/historical_acceptance/invalid_examples/"
            "mapping_missing/fixtures.json"
        ),
        "fankui/architecture.md",
        "fankui/backtest_v1_contract.md",
        "fankui/betting_model.md",
        "fankui/data_model.md",
        "fankui/historical_data_backtest.md",
        "fankui/llm_review_v1_contract.md",
        "fankui/llm_review_v2_contract.md",
        "fankui/llm_review_v3_contract.md",
        "fankui/llm_strategy.md",
        "fankui/decisions/0001-market-abstraction.md",
        "fankui/decisions/0002-versioned-fusion-policies.md",
        "fankui/decisions/0003-ticket-and-atomic-bet.md",
        "fankui/decisions/0004-frozen-evidence-for-llm.md",
        "fankui/decisions/0005-sporttery-stake-unit.md",
        "fankui/decisions/0006-configurable-ticket-strategy-profile.md",
        "fankui/decisions/0007-separate-live-and-source-time-research.md",
        "migrations/env.py",
        "migrations/script.py.mako",
        "migrations/versions/1bec5f575834_create_mvp_schema.py",
        "migrations/versions/4f9b2d7c1a60_add_portfolio_risk.py",
        "migrations/versions/7a2c5e8f9b31_add_offline_review_bridge.py",
        "migrations/versions/9d4e6f1a2c70_harden_artifact_integrity.py",
        "migrations/versions/c8b7e2a4f190_add_post_review_persistence.py",
        "migrations/versions/e3754eb9a102_seal_all_analysis_artifacts.py",
        ("migrations/versions/f3a1c6d8e204_add_historical_backtest_persistence.py"),
        "migrations/versions/d2e7a4c9b615_add_identity_persistence_schema.py",
        "migrations/versions/a6c1f9e3b742_add_fixture_ingestion_capture.py",
        "migrations/versions/b7d4e9f2c631_add_quant_model_lineage.py",
        "migrations/versions/c4e8a1d7f205_add_backtest_v2_lineage.py",
        "migrations/versions/3cb19bcbdd88_add_live_source_ingestion.py",
        "migrations/versions/6e4b1a9c2d73_bind_live_analysis_preparations.py",
    }
)


class WheelE2EError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandOutput:
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install one football-system wheel into an isolated virtual environment "
            "and exercise every historical/backtest CLI path."
        )
    )
    parser.add_argument(
        "wheel",
        nargs="?",
        type=Path,
        help="Wheel to test. Defaults to the sole football_system wheel in dist/.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help=(
            "Empty output directory outside the source checkout. If omitted, a "
            "temporary directory is created and removed."
        ),
    )
    return parser


def _resolve_wheel(wheel: Path | None, project_root: Path = PROJECT_ROOT) -> Path:
    if wheel is None:
        candidates = sorted((project_root / "dist").glob("football_system-*.whl"))
        if len(candidates) != 1:
            raise WheelE2EError(
                "wheel discovery requires exactly one dist/football_system-*.whl; "
                f"found {len(candidates)}"
            )
        wheel = candidates[0]
    resolved = wheel.expanduser().resolve()
    if not resolved.is_file():
        raise WheelE2EError(f"wheel does not exist: {resolved}")
    if resolved.suffix.lower() != ".whl":
        raise WheelE2EError(f"expected a .whl file: {resolved}")
    return resolved


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WheelE2EError(message)


def _inspect_wheel(wheel: Path) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            corrupt_member = archive.testzip()
            _require(corrupt_member is None, f"corrupt wheel member: {corrupt_member}")
            names = tuple(archive.namelist())
            resource_marker = "football_system_resources/"
            resources = {
                name.split(resource_marker, 1)[1]
                for name in names
                if resource_marker in name
                and not name.endswith("/")
                and name.split(resource_marker, 1)[1]
            }
            missing = sorted(EXPECTED_RESOURCE_FILES - resources)
            unexpected = sorted(resources - EXPECTED_RESOURCE_FILES)
            _require(
                not missing and not unexpected,
                "wheel resource manifest mismatch; "
                f"missing={missing or 'NONE'}; unexpected={unexpected or 'NONE'}",
            )

            forbidden = []
            for name in names:
                parts = PurePosixPath(name).parts
                lowered = name.lower()
                if (
                    "yaoqiu" in parts
                    or "scripts" in parts
                    or lowered.endswith((".db", ".db-wal", ".db-shm", ".env"))
                ):
                    forbidden.append(name)
            _require(
                not forbidden,
                f"wheel contains forbidden source/data files: {sorted(forbidden)}",
            )

            metadata_names = [
                name for name in names if name.endswith(".dist-info/METADATA")
            ]
            _require(
                len(metadata_names) == 1,
                "wheel must contain exactly one dist-info/METADATA file",
            )
            metadata = Parser().parsestr(
                archive.read(metadata_names[0]).decode("utf-8")
            )
    except zipfile.BadZipFile as error:
        raise WheelE2EError(f"invalid wheel archive: {wheel}") from error

    normalized_name = str(metadata.get("Name", "")).lower().replace("_", "-")
    _require(
        normalized_name == "football-system",
        "wheel project name is not football-system",
    )
    _require(
        metadata.get("Version") == EXPECTED_VERSION,
        f"wheel version must remain {EXPECTED_VERSION}",
    )
    print(
        f"[wheel-e2e] wheel manifest: {len(EXPECTED_RESOURCE_FILES)} resources, "
        f"version {EXPECTED_VERSION}"
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _prepare_work_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    _require(
        not _is_within(resolved, PROJECT_ROOT),
        f"--work-dir must be outside the source checkout: {resolved}",
    )
    if resolved.exists():
        _require(resolved.is_dir(), f"--work-dir is not a directory: {resolved}")
        _require(
            not any(resolved.iterdir()),
            f"--work-dir must be empty: {resolved}",
        )
    else:
        resolved.mkdir(parents=True)
    return resolved


def _clean_environment(venv: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if venv is not None:
        bin_directory = venv / ("Scripts" if os.name == "nt" else "bin")
        environment["VIRTUAL_ENV"] = str(venv)
        environment["PATH"] = os.pathsep.join(
            (str(bin_directory), environment.get("PATH", ""))
        )
    return environment


def _display_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in command])


def _run_checked(
    label: str,
    command: Sequence[str | Path],
    *,
    cwd: Path,
    environment: dict[str, str],
    markers: Sequence[str] = (),
    timeout: int = 900,
) -> CommandOutput:
    rendered = [str(item) for item in command]
    try:
        completed = subprocess.run(
            rendered,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise WheelE2EError(
            f"{label} timed out after {timeout}s: {_display_command(rendered)}"
        ) from error
    output = CommandOutput(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        raise WheelE2EError(
            f"{label} exited {completed.returncode}: {_display_command(rendered)}\n"
            f"--- stdout ---\n{output.stdout}\n"
            f"--- stderr ---\n{output.stderr}"
        )
    for marker in markers:
        _require(marker in output.combined, f"{label} omitted output marker: {marker}")
    print(f"[wheel-e2e] PASS {label}")
    return output


def _run_json(
    label: str,
    python: Path,
    code: str,
    arguments: Sequence[str | Path],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    output = _run_checked(
        label,
        [python, "-I", "-c", code, *arguments],
        cwd=cwd,
        environment=environment,
    )
    try:
        value = json.loads(output.stdout)
    except json.JSONDecodeError as error:
        raise WheelE2EError(
            f"{label} returned invalid JSON: {output.stdout!r}"
        ) from error
    _require(isinstance(value, dict), f"{label} did not return a JSON object")
    return value


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _assert_ten_slices(label: str, output: CommandOutput) -> None:
    _require(
        "- slate_count: 10" in output.stdout,
        f"{label} did not report slate_count 10",
    )
    _require(
        output.stdout.count("### Slice ") == 10,
        f"{label} did not render exactly 10 slices",
    )


def _assert_report(path: Path, heading: str, slice_count: int | None = None) -> str:
    _require(path.is_file(), f"expected report file was not created: {path}")
    content = path.read_text(encoding="utf-8")
    _require(content.startswith(heading), f"report has an unexpected heading: {path}")
    if slice_count is not None:
        _require(
            content.count("### Slice ") == slice_count,
            f"report has an unexpected slice count: {path}",
        )
    return content


def _exercise_wheel(wheel: Path, work_dir: Path) -> None:
    venv = work_dir / "venv"
    base_environment = _clean_environment()
    _run_checked(
        "create isolated virtual environment",
        [sys.executable, "-m", "venv", venv],
        cwd=work_dir,
        environment=base_environment,
    )

    bin_directory = venv / ("Scripts" if os.name == "nt" else "bin")
    python = bin_directory / ("python.exe" if os.name == "nt" else "python")
    executable = bin_directory / (
        "football-system.exe" if os.name == "nt" else "football-system"
    )
    environment = _clean_environment(venv)
    _run_checked(
        "install wheel",
        [
            python,
            "-m",
            "pip",
            "install",
            "--isolated",
            "--disable-pip-version-check",
            wheel,
        ],
        cwd=work_dir,
        environment=environment,
    )
    _require(executable.is_file(), f"installed CLI executable is missing: {executable}")

    provenance_code = """
import importlib.metadata
import json
import sys
from pathlib import Path

import football_system
from football_system.interfaces.cli import _resource_root

origin = Path(football_system.__file__).resolve()
source_root = Path(sys.argv[1]).resolve()
if source_root == origin or source_root in origin.parents:
    raise SystemExit(f"football_system imported from source checkout: {origin}")
print(json.dumps({
    "origin": str(origin),
    "resource_root": str(_resource_root()),
    "version": importlib.metadata.version("football-system"),
}))
"""
    provenance = _run_json(
        "verify installed import provenance",
        python,
        provenance_code,
        [PROJECT_ROOT],
        cwd=work_dir,
        environment=environment,
    )
    _require(
        provenance.get("version") == EXPECTED_VERSION,
        f"installed version must remain {EXPECTED_VERSION}",
    )
    resource_root = Path(str(provenance["resource_root"])).resolve()
    archive = resource_root / "data" / "fixtures" / "historical_acceptance"
    _require(archive.is_dir(), f"installed historical archive is missing: {archive}")

    database = work_dir / "wheel-e2e.db"
    database_url = _sqlite_url(database)
    quant_report = work_dir / "quant-run.md"
    blend_report = work_dir / "blend-run.md"
    persisted_report = work_dir / "persisted-report.md"
    comparison_report = work_dir / "comparison.md"

    _run_checked(
        "live command help",
        [executable, "live", "--help"],
        cwd=work_dir,
        environment=environment,
        markers=("plan-slate", "ingest-fixtures"),
    )
    _run_checked(
        "live daily slate planning help",
        [executable, "live", "plan-slate", "--help"],
        cwd=work_dir,
        environment=environment,
        markers=("--input", "--as-of", "--output"),
    )
    _run_checked(
        "live fixture ingestion help",
        [executable, "live", "ingest-fixtures", "--help"],
        cwd=work_dir,
        environment=environment,
        markers=("--league-id", "--provider-season-id", "--team-type"),
    )

    _run_checked(
        "historical-archive validate",
        [executable, "historical-archive", "validate"],
        cwd=work_dir,
        environment=environment,
        markers=(*WARNING_MARKERS, "archive_count: 6", "MANIFEST_PROVENANCE_ONLY"),
    )
    _run_checked(
        "historical-archive import",
        [
            executable,
            "historical-archive",
            "import",
            "--database-url",
            database_url,
        ],
        cwd=work_dir,
        environment=environment,
        markers=(*WARNING_MARKERS, "imported/registered", "archive_count: 6"),
    )

    run_outputs = []
    for policy, run_id, output_path in (
        ("QUANT_ONLY_V1", QUANT_RUN_ID, quant_report),
        ("MARKET_QUANT_BLEND_V1", BLEND_RUN_ID, blend_report),
    ):
        output = _run_checked(
            f"backtest run {policy}",
            [
                executable,
                "backtest",
                "run",
                "--database-url",
                database_url,
                "--fusion-policy",
                policy,
                "--backtest-run-id",
                run_id,
                "--output",
                output_path,
            ],
            cwd=work_dir,
            environment=environment,
            markers=(
                *WARNING_MARKERS,
                *REPORT_FIELD_MARKERS,
                "# Walk-Forward Backtest Report",
                "- match_count: 60",
                "- settled_match_count: 59",
                "PARTIAL MATCH RESULT COVERAGE",
            ),
        )
        _assert_ten_slices(f"backtest run {policy}", output)
        run_outputs.append(output)

    report_output = _run_checked(
        "backtest report",
        [
            executable,
            "backtest",
            "report",
            "--database-url",
            database_url,
            "--backtest-run-id",
            QUANT_RUN_ID,
            "--output",
            persisted_report,
        ],
        cwd=work_dir,
        environment=environment,
        markers=(
            *WARNING_MARKERS,
            *REPORT_FIELD_MARKERS,
            "# Walk-Forward Backtest Report",
        ),
    )
    _assert_ten_slices("backtest report", report_output)

    comparison_output = _run_checked(
        "backtest compare",
        [
            executable,
            "backtest",
            "compare",
            "--database-url",
            database_url,
            "--left-run-id",
            QUANT_RUN_ID,
            "--right-run-id",
            BLEND_RUN_ID,
            "--output",
            comparison_report,
        ],
        cwd=work_dir,
        environment=environment,
        markers=(*WARNING_MARKERS, "# Backtest Comparison", "Brier (P_final)"),
    )

    _run_checked(
        "match-results list",
        [
            executable,
            "match-results",
            "list",
            "--database-url",
            database_url,
            "--match-id",
            "ha-20250106-01",
            "--match-id",
            "ha-20250115-06",
            "--as-of",
            "2025-01-16T03:00:00Z",
            "--provider-code",
            PROVIDER_CODE,
        ],
        cwd=work_dir,
        environment=environment,
        markers=("outcome: HOME_WIN", "missing_match_ids: ha-20250115-06"),
    )

    settlement_source_code = '''
import datetime
import json
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    row = connection.execute(
        """
        SELECT slices.parent_analysis_run_id,
               portfolios.portfolio_id,
               slices.evaluation_as_of_at_utc
        FROM backtest_slices AS slices
        JOIN portfolios
          ON portfolios.analysis_run_id = slices.parent_analysis_run_id
        WHERE slices.backtest_run_id = ?
          AND slices.slice_no = 1
          AND portfolios.status = 'RECOMMENDED'
        ORDER BY portfolios.portfolio_id
        LIMIT 1
        """,
        (sys.argv[2],),
    ).fetchone()
if row is None:
    raise SystemExit("no persisted settlement source found")
evaluation = datetime.datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
if evaluation.tzinfo is None:
    evaluation = evaluation.replace(tzinfo=datetime.timezone.utc)
print(json.dumps({
    "analysis_run_id": row[0],
    "portfolio_id": row[1],
    "evaluation_as_of": evaluation.astimezone(datetime.timezone.utc).isoformat(),
}))
'''
    settlement_source = _run_json(
        "query persisted settlement source",
        python,
        settlement_source_code,
        [database, QUANT_RUN_ID],
        cwd=work_dir,
        environment=environment,
    )
    analysis_run_id = str(settlement_source["analysis_run_id"])
    portfolio_id = str(settlement_source["portfolio_id"])
    evaluation_as_of = str(settlement_source["evaluation_as_of"])

    _run_checked(
        "settlement create",
        [
            executable,
            "settlement",
            "create",
            "--database-url",
            database_url,
            "--portfolio-id",
            portfolio_id,
            "--analysis-run-id",
            analysis_run_id,
            "--archive",
            archive,
            "--provider-code",
            PROVIDER_CODE,
            "--evaluation-as-of",
            evaluation_as_of,
        ],
        cwd=work_dir,
        environment=environment,
        markers=(*WARNING_MARKERS, "settlement_reason: SETTLED", "portfolio_capital"),
    )

    settlement_id_code = '''
import json
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    row = connection.execute(
        """
        SELECT portfolio_settlement_id
        FROM portfolio_settlements
        WHERE portfolio_id = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (sys.argv[2],),
    ).fetchone()
if row is None:
    raise SystemExit("no persisted portfolio settlement found")
print(json.dumps({"portfolio_settlement_id": row[0]}))
'''
    settlement_data = _run_json(
        "query persisted settlement ID",
        python,
        settlement_id_code,
        [database, portfolio_id],
        cwd=work_dir,
        environment=environment,
    )
    portfolio_settlement_id = str(settlement_data["portfolio_settlement_id"])

    _run_checked(
        "settlement report",
        [
            executable,
            "settlement",
            "report",
            "--database-url",
            database_url,
            "--portfolio-settlement-id",
            portfolio_settlement_id,
        ],
        cwd=work_dir,
        environment=environment,
        markers=("# Portfolio Settlement Report", "## Lineage", "## Financials"),
    )

    final_state_code = """
import json
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    head = connection.execute(
        "SELECT version_num FROM alembic_version"
    ).fetchone()
    state = {
        "migration_head": None if head is None else head[0],
        "archive_count": connection.execute(
            "SELECT COUNT(*) FROM historical_archive_imports"
        ).fetchone()[0],
        "run_count": connection.execute(
            "SELECT COUNT(*) FROM backtest_runs"
        ).fetchone()[0],
        "quant_slice_count": connection.execute(
            "SELECT COUNT(*) FROM backtest_slices WHERE backtest_run_id = ?",
            (sys.argv[2],),
        ).fetchone()[0],
        "blend_slice_count": connection.execute(
            "SELECT COUNT(*) FROM backtest_slices WHERE backtest_run_id = ?",
            (sys.argv[3],),
        ).fetchone()[0],
        "settlement_count": connection.execute(
            "SELECT COUNT(*) FROM portfolio_settlements "
            "WHERE portfolio_settlement_id = ?",
            (sys.argv[4],),
        ).fetchone()[0],
    }
print(json.dumps(state, sort_keys=True))
"""
    state = _run_json(
        "verify persisted wheel state",
        python,
        final_state_code,
        [database, QUANT_RUN_ID, BLEND_RUN_ID, portfolio_settlement_id],
        cwd=work_dir,
        environment=environment,
    )
    _require(
        state.get("migration_head") == EXPECTED_MIGRATION_HEAD,
        f"database migration head is not {EXPECTED_MIGRATION_HEAD}: {state}",
    )
    _require(state.get("archive_count") == 6, f"archive persistence mismatch: {state}")
    _require(state.get("run_count") == 2, f"backtest run persistence mismatch: {state}")
    _require(
        state.get("quant_slice_count") == 10 and state.get("blend_slice_count") == 10,
        f"backtest slice persistence mismatch: {state}",
    )
    _require(
        state.get("settlement_count") == 1,
        f"dynamic settlement ID was not persisted: {state}",
    )

    quant_content = _assert_report(
        quant_report,
        "# Walk-Forward Backtest Report",
        10,
    )
    _assert_report(blend_report, "# Walk-Forward Backtest Report", 10)
    persisted_content = _assert_report(
        persisted_report,
        "# Walk-Forward Backtest Report",
        10,
    )
    _require(
        quant_content == persisted_content,
        "in-memory quant report differs from its persisted report",
    )
    comparison_content = _assert_report(
        comparison_report,
        "# Backtest Comparison",
        20,
    )
    ranking_match = RANKING_LANGUAGE.search(
        "\n".join(
            [
                *(item.stdout for item in run_outputs),
                report_output.stdout,
                comparison_output.stdout,
                comparison_content,
            ]
        )
    )
    _require(
        ranking_match is None,
        "backtest output contains ranking language: "
        f"{ranking_match.group(0) if ranking_match else 'UNKNOWN'}",
    )

    print(
        "[wheel-e2e] SUCCESS live help + 8 historical CLI paths; "
        "2 runs x 10 slices; "
        f"migration head {EXPECTED_MIGRATION_HEAD}; reports verified"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        wheel = _resolve_wheel(args.wheel)
        _inspect_wheel(wheel)
        if args.work_dir is not None:
            work_dir = _prepare_work_dir(args.work_dir)
            _exercise_wheel(wheel, work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="football-wheel-e2e-") as temporary:
                work_dir = _prepare_work_dir(Path(temporary))
                _exercise_wheel(wheel, work_dir)
    except (OSError, sqlite3.Error, WheelE2EError) as error:
        print(f"wheel E2E failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
