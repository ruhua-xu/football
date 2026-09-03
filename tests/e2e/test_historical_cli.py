from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    canonical_archive_provenance,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import (
    BacktestMetricSnapshotRecord,
    BacktestRunRecord,
    BacktestSliceRecord,
    HistoricalArchiveImportRecord,
    MatchResultRecord,
    PortfolioRecord,
    PortfolioSettlementRecord,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.providers.historical_archive import (
    LocalArchiveStore,
)
from football_system.interfaces.cli import main


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data" / "fixtures" / "historical_acceptance"
FIXTURE_CONFIG = ARCHIVE / "acceptance_config.toml"
BACKTEST_CONFIG = ROOT / "config" / "backtest.toml"
PROVIDER_CODE = "SYNTHETIC_ACCEPTANCE_V1"
UTC = timezone.utc


def test_top_level_help_discovers_historical_and_backtest_paths(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    for command in (
        "historical-archive validate",
        "historical-archive import",
        "match-results list",
        "settlement create",
        "settlement report",
        "backtest run",
        "backtest report",
        "backtest compare",
    ):
        assert command in output


@pytest.mark.parametrize(
    "arguments",
    (
        ["historical-archive", "unknown"],
        ["backtest", "run"],
        ["match-results", "list", "--as-of", "not-a-date"],
    ),
)
def test_historical_cli_parse_errors_exit_two(arguments) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_backtest_preflight_failure_does_not_create_database(tmp_path: Path) -> None:
    database_path = tmp_path / "must-not-exist.db"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "backtest",
                "run",
                "--archive",
                str(ARCHIVE),
                "--fixture-config",
                str(FIXTURE_CONFIG),
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                f"sqlite:///{database_path.as_posix()}",
                "--fusion-policy",
                "QUANT_ONLY_V1",
                "--data-mode",
                "SOURCE_TIME_RESEARCH",
            ]
        )

    assert error.value.code == 2
    assert not database_path.exists()


def test_historical_cli_runs_all_public_paths(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{(tmp_path / 'historical-cli.db').as_posix()}"
    quant_output = tmp_path / "quant.md"
    blend_output = tmp_path / "blend.md"
    report_output = tmp_path / "report.md"
    comparison_output = tmp_path / "comparison.md"

    assert (
        main(
            [
                "historical-archive",
                "validate",
                "--archive",
                str(ARCHIVE),
                "--config",
                str(BACKTEST_CONFIG),
            ]
        )
        == 0
    )
    validation_output = capsys.readouterr().out
    assert "archive_count: 6" in validation_output
    assert "MANIFEST_PROVENANCE_ONLY" in validation_output

    assert (
        main(
            [
                "historical-archive",
                "import",
                "--archive",
                str(ARCHIVE),
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    import_output = capsys.readouterr().out
    assert "imported/registered" in import_output
    assert "MANIFEST_PROVENANCE_ONLY" in import_output

    for policy, run_id, output_path in (
        ("QUANT_ONLY_V1", "cli-quant", quant_output),
        ("MARKET_QUANT_BLEND_V1", "cli-blend", blend_output),
    ):
        assert (
            main(
                [
                    "backtest",
                    "run",
                    "--archive",
                    str(ARCHIVE),
                    "--fixture-config",
                    str(FIXTURE_CONFIG),
                    "--config",
                    str(BACKTEST_CONFIG),
                    "--database-url",
                    database_url,
                    "--fusion-policy",
                    policy,
                    "--backtest-run-id",
                    run_id,
                    "--output",
                    str(output_path),
                ]
            )
            == 0
        )
        run_output = capsys.readouterr().out
        assert "slate_count: 10" in run_output
        assert "match_count: 60" in run_output
        assert "settled_match_count: 59" in run_output
        assert "SYNTHETIC ACCEPTANCE DATA" in run_output
        assert "NOT REAL HISTORICAL PERFORMANCE" in run_output
        assert "archive_schema_version: HISTORICAL_ARCHIVE_V1" in run_output
        assert "absolute_gap=" in run_output
        assert "winning_ticket_count:" in run_output
        assert "kickoff_from_utc:" in run_output
        assert "kickoff_to_utc:" in run_output
        assert output_path.read_text(encoding="utf-8").startswith(
            "# Walk-Forward Backtest Report"
        )

    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    historical = SqlAlchemyHistoricalRepository(sessions)
    expected_provenance = canonical_archive_provenance(
        tuple(
            BacktestArchiveProvenance.from_manifest(manifest)
            for manifest in LocalArchiveStore(ARCHIVE).manifests
        )
    )
    for run_id in ("cli-quant", "cli-blend"):
        run = historical.find_backtest_run_value(run_id)
        assert run is not None
        slices = historical.backtest_slice_values(run_id)
        assert run.created_at_utc > max(item.evaluation_as_of_at_utc for item in slices)
        assert run.archive_provenance == expected_provenance
        assert len(run.archive_provenance) == 6
        assert tuple(item.slice_id for item in slices) == run.expected_slice_ids
        assert all(item.match_count == 6 for item in slices)

    assert (
        main(
            [
                "backtest",
                "report",
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
                "--backtest-run-id",
                "cli-quant",
                "--output",
                str(report_output),
            ]
        )
        == 0
    )
    report_stdout = capsys.readouterr().out
    assert "match_coverage: 0.983333333333" in report_stdout
    assert "execution_created_at_utc:" in report_stdout
    assert "missing_decision_match_ids: NONE" in report_stdout
    assert "decision_input_manifest_hash:" in report_stdout
    assert "match_result_ids:" in report_stdout
    assert "PARTIAL MATCH RESULT COVERAGE" in report_stdout
    assert report_stdout.count("- archive_id:") == 6
    assert report_output.is_file()
    assert report_output.read_bytes() == quant_output.read_bytes()

    historical.append_historical_archive_import(
        HistoricalArchiveManifest(
            archive_schema_version="HISTORICAL_ARCHIVE_V1",
            archive_id="unrelated-later-market-archive",
            provider_code=PROVIDER_CODE,
            dataset_kind=HistoricalArchiveDatasetKind.MARKET_ODDS,
            created_at_utc=datetime(2025, 2, 1, tzinfo=UTC),
            source_reference="https://example.invalid/unrelated-later-archive",
            source_description="Unrelated later archive",
            license_note="Test-only unrelated provenance",
            data_mode=HistoricalDataMode.LIVE_STRICT,
            payload_sha256="f" * 64,
            record_count=0,
        ),
        datetime.now(UTC),
    )
    assert (
        main(
            [
                "backtest",
                "report",
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
                "--backtest-run-id",
                "cli-quant",
            ]
        )
        == 0
    )
    unchanged_report = capsys.readouterr().out
    assert unchanged_report == report_output.read_text(encoding="utf-8")

    assert (
        main(
            [
                "backtest",
                "compare",
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
                "--left-run-id",
                "cli-quant",
                "--right-run-id",
                "cli-blend",
                "--output",
                str(comparison_output),
            ]
        )
        == 0
    )
    comparison_stdout = capsys.readouterr().out
    assert "Brier (P_final)" in comparison_stdout
    assert comparison_stdout.count("# Walk-Forward Backtest Report") == 2
    assert "winner" not in comparison_stdout.lower()
    assert "best" not in comparison_stdout.lower()
    assert comparison_output.is_file()

    assert (
        main(
            [
                "match-results",
                "list",
                "--config",
                str(BACKTEST_CONFIG),
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
            ]
        )
        == 0
    )
    result_output = capsys.readouterr().out
    assert "outcome: HOME_WIN" in result_output
    assert "missing_match_ids: ha-20250115-06" in result_output

    with sessions() as session:
        settlement_source = session.execute(
            select(
                BacktestSliceRecord.parent_analysis_run_id,
                BacktestSliceRecord.evaluation_as_of_at_utc,
                PortfolioRecord.portfolio_id,
            )
            .join(
                PortfolioRecord,
                PortfolioRecord.analysis_run_id
                == BacktestSliceRecord.parent_analysis_run_id,
            )
            .where(
                BacktestSliceRecord.backtest_run_id == "cli-quant",
                BacktestSliceRecord.slice_no == 1,
            )
        ).one()
    analysis_run_id, evaluation_as_of, portfolio_id = settlement_source

    assert (
        main(
            [
                "settlement",
                "create",
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
                "--portfolio-id",
                portfolio_id,
                "--analysis-run-id",
                analysis_run_id,
                "--archive",
                str(ARCHIVE),
                "--provider-code",
                PROVIDER_CODE,
                "--evaluation-as-of",
                evaluation_as_of.isoformat(),
            ]
        )
        == 0
    )
    settlement_output = capsys.readouterr().out
    assert "settlement_reason: SETTLED" in settlement_output
    assert "status: WON" in settlement_output or "status: LOST" in settlement_output
    assert "frozen_gross_payout" in settlement_output
    assert "portfolio_capital" in settlement_output

    with sessions() as session:
        portfolio_settlement_id = session.scalar(
            select(PortfolioSettlementRecord.portfolio_settlement_id).where(
                PortfolioSettlementRecord.portfolio_id == portfolio_id
            )
        )
    assert portfolio_settlement_id is not None
    assert (
        main(
            [
                "settlement",
                "report",
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
                "--portfolio-settlement-id",
                portfolio_settlement_id,
            ]
        )
        == 0
    )
    settlement_report = capsys.readouterr().out
    assert "## Lineage" in settlement_report
    assert "## Financials" in settlement_report

    with sessions() as session:
        no_bet_source = session.execute(
            select(
                BacktestSliceRecord.parent_analysis_run_id,
                BacktestSliceRecord.evaluation_as_of_at_utc,
                PortfolioRecord.portfolio_id,
            )
            .join(
                PortfolioRecord,
                PortfolioRecord.analysis_run_id
                == BacktestSliceRecord.parent_analysis_run_id,
            )
            .where(
                BacktestSliceRecord.backtest_run_id == "cli-quant",
                BacktestSliceRecord.slice_no == 7,
                PortfolioRecord.status == "NO_BET",
            )
        ).one()
    no_bet_run_id, no_bet_evaluation, no_bet_portfolio_id = no_bet_source
    assert (
        main(
            [
                "settlement",
                "create",
                "--config",
                str(BACKTEST_CONFIG),
                "--database-url",
                database_url,
                "--portfolio-id",
                no_bet_portfolio_id,
                "--analysis-run-id",
                no_bet_run_id,
                "--archive",
                str(ARCHIVE),
                "--provider-code",
                PROVIDER_CODE,
                "--evaluation-as-of",
                no_bet_evaluation.isoformat(),
            ]
        )
        == 0
    )
    no_bet_output = capsys.readouterr().out
    assert "portfolio_status: NO_BET" in no_bet_output
    assert "settlement_reason: SETTLED" in no_bet_output
    assert "portfolio_capital" in no_bet_output

    with sessions() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(HistoricalArchiveImportRecord)
            )
            == 7
        )
        assert session.scalar(select(func.count()).select_from(MatchResultRecord)) == 59
        assert session.scalar(select(func.count()).select_from(BacktestRunRecord)) == 2
        assert (
            session.scalar(select(func.count()).select_from(BacktestSliceRecord)) == 20
        )
        assert (
            session.scalar(
                select(func.count()).select_from(BacktestMetricSnapshotRecord)
            )
            == 2
        )
        for run_id in ("cli-quant", "cli-blend"):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(BacktestSliceRecord)
                    .where(BacktestSliceRecord.backtest_run_id == run_id)
                )
                == 10
            )
