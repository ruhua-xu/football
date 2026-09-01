import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from football_system.application.backtest_reports import (
    BacktestReportComparison,
    BacktestReportData,
    _value,
    expected_match_ids_from_analysis_manifest,
    load_backtest_fixture,
    render_backtest_comparison,
    render_backtest_report,
    render_match_results,
)
from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestMatchSnapshot,
    BacktestRun,
    BacktestRunStatus,
    BacktestSlateSnapshot,
    BacktestSlice,
    BacktestStrategySnapshot,
)
from football_system.domain.market import SelectionKey, ThreeWayProbability
from football_system.domain.services.backtest_metrics import (
    calculate_backtest_metrics,
)
from football_system.domain.settlement import (
    MatchSettlementIssue,
    UnsupportedSettlementReason,
)


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CONFIG = (
    ROOT / "data" / "fixtures" / "historical_acceptance" / "acceptance_config.toml"
)


def test_fixture_config_is_strict_and_preserves_exact_plan_match_ids(
    tmp_path: Path,
) -> None:
    fixture = load_backtest_fixture(FIXTURE_CONFIG)

    assert fixture.market_bookmaker_code == "CONSENSUS"
    assert tuple(plan.match_ids for plan in fixture.plans) == tuple(
        slate.match_ids for slate in fixture.slates
    )
    assert all(len(plan.match_ids) == 6 for plan in fixture.plans)

    misspelled = tmp_path / "misspelled.toml"
    misspelled.write_text(
        FIXTURE_CONFIG.read_text(encoding="utf-8").replace(
            "budget_fen = 10000",
            "budegt_fen = 10000",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="budegt_fen"):
        load_backtest_fixture(misspelled)

    missing_bookmaker = tmp_path / "missing-bookmaker.toml"
    missing_bookmaker.write_text(
        FIXTURE_CONFIG.read_text(encoding="utf-8").replace(
            'market_bookmaker_code = "CONSENSUS"\n',
            "",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="market_bookmaker_code"):
        load_backtest_fixture(missing_bookmaker)


def test_formatter_includes_probability_calibration_aggregate_and_research_fields() -> (
    None
):
    report = _report("quant-report", "QUANT_ONLY_V1", "1")

    output = render_backtest_report(report)

    assert "RETROSPECTIVE_SOURCE_TIME_RESEARCH" in output
    assert "SYNTHETIC ACCEPTANCE DATA" in output
    assert "NOT REAL HISTORICAL PERFORMANCE" in output
    for stream in ("P_market", "P_quant", "P_final"):
        assert f"### {stream}" in output
    for field in (
        "multiclass_Brier",
        "Brier_H",
        "Brier_D",
        "Brier_A",
        "multiclass_LogLoss",
        "log_loss_clip_version",
        "log_loss_epsilon",
        "ECE",
        "slate_count",
        "match_count",
        "settled_match_count",
        "match_coverage",
        "ticket_count",
        "settled_ticket_count",
        "ticket_coverage",
        "total_budget",
        "total_stake",
        "total_cash",
        "gross_payout",
        "profit_loss",
        "ROI_on_budget",
        "ROI_on_deployed",
        "ticket_hit_rate",
        "NO_BET_count",
        "NO_BET_ratio",
        "max_drawdown",
        "max_consecutive_losing_slates",
        "average_ticket_odds",
        "average_ticket_probability",
        "average_selection_EV",
        "max_match_exposure",
        "max_selection_exposure",
        "realized_loss_when_top_exposure_failed",
        "realized_loss_when_top_two_exposure_failed",
        "execution_created_at_utc",
        "archive_schema_version",
        "absolute_gap",
        "winning_ticket_count",
        "kickoff_from_utc",
        "kickoff_to_utc",
        "missing_decision_match_ids",
        "decision_input_manifest_hash",
        "match_result_ids",
    ):
        assert field in output
    assert output.count("mean_probability=") == 30
    assert output.count("frequency=") == 30
    assert output.count("absolute_gap=") == 30
    assert output.count("count=") >= 30
    assert "- Brier_H: 0.25" in output
    assert "0.250000000000" not in output


def test_decimal_report_values_normalize_trailing_zeros() -> None:
    assert _value(Decimal("1.230000000000")) == "1.23"
    assert _value(Decimal("1.23")) == "1.23"
    assert _value(Decimal("0E-12")) == "0"


def test_comparison_has_both_full_reports_and_no_ranking_language() -> None:
    comparison = BacktestReportComparison(
        left=_report("quant-report", "QUANT_ONLY_V1", "1"),
        right=_report("blend-report", "MARKET_QUANT_BLEND_V1", "0.7"),
    )

    output = render_backtest_comparison(comparison)

    assert "Brier (P_final)" in output
    assert "LogLoss (P_final)" in output
    assert "ROI_on_budget" in output
    assert "Drawdown" in output
    assert "NO_BET" in output
    assert "Ticket Hit Rate" in output
    assert output.count("# Walk-Forward Backtest Report") == 2
    assert "winner" not in output.lower()
    assert "best" not in output.lower()


def test_report_renders_durable_unsupported_settlement_issue() -> None:
    issue = MatchSettlementIssue(
        match_id="match-unsupported",
        reason=UnsupportedSettlementReason.VOID,
        detail="provider declared the match void",
    )
    report = _report("issue-report", "QUANT_ONLY_V1", "1", issue=issue)

    output = render_backtest_report(report)

    assert "**UNSUPPORTED_SETTLEMENT_CASE**" in output
    assert "match-unsupported:VOID:provider declared the match void" in output
    assert "reason=VOID" in output


def test_comparison_rejects_different_result_issue_lineage() -> None:
    left_issue = MatchSettlementIssue(
        match_id="match-unsupported",
        reason=UnsupportedSettlementReason.VOID,
    )
    right_issue = left_issue.model_copy(
        update={"reason": UnsupportedSettlementReason.CANCELLATION}
    )

    with pytest.raises(ValueError, match="replay lineage"):
        BacktestReportComparison(
            left=_report("quant-issue", "QUANT_ONLY_V1", "1", issue=left_issue),
            right=_report(
                "blend-issue",
                "MARKET_QUANT_BLEND_V1",
                "0.7",
                issue=right_issue,
            ),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("decision_input_manifest_hash", "b" * 64, "replay lineage"),
        ("match_result_ids", ("another-result",), "replay lineage"),
    ),
)
def test_comparison_rejects_different_slice_lineage(
    field: str,
    value: object,
    message: str,
) -> None:
    left = _report("quant-report", "QUANT_ONLY_V1", "1")
    right = _report("blend-report", "MARKET_QUANT_BLEND_V1", "0.7")
    changed_slice = right.slices[0].model_copy(update={field: value})
    right = BacktestReportData.model_validate(
        right.model_copy(update={"slices": (changed_slice,)}).model_dump(
            mode="python",
            exclude_computed_fields=True,
        )
    )

    with pytest.raises(ValueError, match=message):
        BacktestReportComparison(left=left, right=right)


def test_comparison_rejects_different_expected_match_ids() -> None:
    issue = MatchSettlementIssue(
        match_id="match-unsupported",
        reason=UnsupportedSettlementReason.VOID,
    )
    left = _report("quant-report", "QUANT_ONLY_V1", "1", issue=issue)
    right = _report(
        "blend-report",
        "MARKET_QUANT_BLEND_V1",
        "0.7",
        issue=issue,
    )
    reversed_ids = tuple(reversed(right.slices[0].expected_match_ids))
    changed_slice = right.slices[0].model_copy(
        update={"expected_match_ids": reversed_ids}
    )
    right = BacktestReportData.model_validate(
        right.model_copy(
            update={
                "slices": (changed_slice,),
                "expected_match_ids_by_slice": (reversed_ids,),
            }
        ).model_dump(mode="python", exclude_computed_fields=True)
    )

    with pytest.raises(ValueError, match="expected match IDs"):
        BacktestReportComparison(left=left, right=right)


def test_comparison_rejects_different_persisted_kickoff_window() -> None:
    left = _report("quant-report", "QUANT_ONLY_V1", "1")
    right = _report("blend-report", "MARKET_QUANT_BLEND_V1", "0.7")
    changed_slice = right.slices[0].model_copy(
        update={
            "kickoff_from_utc": right.slices[0].kickoff_from_utc
            + timedelta(minutes=1)
        }
    )
    right = BacktestReportData.model_validate(
        right.model_copy(update={"slices": (changed_slice,)}).model_dump(
            mode="python",
            exclude_computed_fields=True,
        )
    )

    with pytest.raises(ValueError, match="expected slice IDs"):
        BacktestReportComparison(left=left, right=right)


def test_manifest_match_validation_preserves_persisted_expected_order() -> None:
    manifest_json = json.dumps(
        {
            "matches": [
                {"match_id": "match-b"},
                {"match_id": "match-c"},
            ]
        }
    )
    expected_match_ids = ("match-b", "match-a", "match-c")

    assert expected_match_ids_from_analysis_manifest(
        manifest_json,
        expected_match_ids,
        ("match-a",),
    ) == expected_match_ids

    with pytest.raises(ValueError, match="conflict with persisted slice"):
        expected_match_ids_from_analysis_manifest(
            json.dumps(
                {
                    "matches": [
                        {"match_id": "match-c"},
                        {"match_id": "match-b"},
                    ]
                }
            ),
            expected_match_ids,
            ("match-a",),
        )


def test_comparison_rejects_different_archive_provenance() -> None:
    left = _report("quant-report", "QUANT_ONLY_V1", "1")
    right = _report("blend-report", "MARKET_QUANT_BLEND_V1", "0.7")
    different_manifest = right.archive_manifests[0].model_copy(
        update={
            "archive_id": "different-report-archive",
            "payload_sha256": "f" * 64,
        }
    )
    different_run = right.backtest_run.model_copy(
        update={
            "archive_provenance": (
                BacktestArchiveProvenance.from_manifest(different_manifest),
            )
        }
    )
    right = BacktestReportData.model_validate(
        right.model_copy(
            update={
                "backtest_run": different_run,
                "archive_manifests": (different_manifest,),
            }
        ).model_dump(mode="python", exclude_computed_fields=True)
    )

    with pytest.raises(ValueError, match="archive provenance"):
        BacktestReportComparison(left=left, right=right)


def test_report_requires_exact_run_archive_provenance_and_expected_slice_ids() -> None:
    report = _report("quant-report", "QUANT_ONLY_V1", "1")
    mismatched_manifest = report.archive_manifests[0].model_copy(
        update={"payload_sha256": "f" * 64}
    )
    with pytest.raises(ValueError, match="run archive provenance"):
        BacktestReportData.model_validate(
            report.model_copy(
                update={"archive_manifests": (mismatched_manifest,)}
            ).model_dump(mode="python", exclude_computed_fields=True)
        )

    mismatched_run = report.backtest_run.model_copy(
        update={"expected_slice_ids": ("another-slice",)}
    )
    with pytest.raises(ValueError, match="expected slice IDs"):
        BacktestReportData.model_validate(
            report.model_copy(update={"backtest_run": mismatched_run}).model_dump(
                mode="python",
                exclude_computed_fields=True,
            )
        )


def test_arbitrary_archive_is_not_labeled_synthetic() -> None:
    report = _report("ordinary-report", "QUANT_ONLY_V1", "1").model_copy(
        update={"archive_manifests": (_manifest(synthetic=False),)}
    )

    output = render_backtest_report(report)

    assert "SYNTHETIC ACCEPTANCE DATA" not in output
    assert "NOT REAL HISTORICAL PERFORMANCE" not in output


def test_empty_match_result_report_is_explicit() -> None:
    output = render_match_results(
        ("missing-match",),
        datetime(2025, 1, 1, tzinfo=UTC),
        (),
        None,
    )

    assert "missing_match_ids: missing-match" in output
    assert "match_results: EMPTY" in output


def _report(
    run_id: str,
    policy: str,
    quant_weight: str,
    *,
    issue: MatchSettlementIssue | None = None,
) -> BacktestReportData:
    decision = datetime(2025, 1, 1, 9, tzinfo=UTC)
    evaluation = decision + timedelta(hours=18)
    manifest = _manifest(synthetic=True)
    slice_id = f"slice-{run_id}"
    expected_match_ids = (
        ("match-report",)
        if issue is None
        else ("match-report", issue.match_id)
    )
    run = BacktestRun(
        backtest_run_id=run_id,
        backtest_version="BACKTEST_V1",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 1),
        strategy_snapshot=BacktestStrategySnapshot.from_config(
            policy,
            {
                "budget_fen": 10_000,
                "min_selection_ev": Decimal("0.05"),
                "min_ticket_roi": Decimal("0.05"),
                "portfolio_constraints": {"absolute_max_tickets": 2},
                "quant_weight": Decimal(quant_weight),
                "slate_policy": "DAILY_FIXED_CUTOFF_V1",
            },
        ),
        code_revision="report-test-revision",
        created_at_utc=evaluation + timedelta(minutes=1),
        status=BacktestRunStatus.COMPLETED,
        archive_provenance=(BacktestArchiveProvenance.from_manifest(manifest),),
        expected_slice_ids=(slice_id,),
    )
    backtest_slice = BacktestSlice(
        slice_id=slice_id,
        backtest_run_id=run_id,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        decision_as_of_at_utc=decision,
        kickoff_from_utc=decision + timedelta(hours=1),
        kickoff_to_utc=decision + timedelta(hours=6),
        evaluation_as_of_at_utc=evaluation,
        analysis_run_id=f"analysis-{run_id}",
        decision_input_manifest_hash="a" * 64,
        match_result_ids=("result-report",),
        match_result_issues=() if issue is None else (issue,),
        expected_match_ids=expected_match_ids,
        match_count=1 if issue is None else 2,
        settled_match_count=1,
        settled_ticket_count=1,
        unsettled_ticket_count=0,
    )
    probabilities = ThreeWayProbability(
        home_win=Decimal("0.50"),
        draw=Decimal("0.30"),
        away_win=Decimal("0.20"),
    )
    match = BacktestMatchSnapshot(
        backtest_run_id=run_id,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        slice_id=slice_id,
        match_id="match-report",
        outcome=SelectionKey.HOME_WIN,
        p_market=probabilities,
        p_quant=probabilities,
        p_final=probabilities,
    )
    slate = BacktestSlateSnapshot(
        backtest_run_id=run_id,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        slice_id=slice_id,
        decision_as_of_at_utc=decision,
        match_count=1 if issue is None else 2,
        settled_match_count=1,
        ticket_count=1,
        settled_ticket_count=1,
        winning_ticket_count=0,
        budget_fen=10_000,
        stake_fen=200,
        settled_stake_fen=200,
        cash_fen=9_800,
        gross_payout_fen=0,
        profit_loss_fen=-200,
        is_no_bet=False,
        ticket_odds=(Decimal("2.50"),),
        ticket_probabilities=(Decimal("0.40"),),
        selection_evs=(Decimal("0.10"),),
        max_match_exposure_fen=200,
        max_selection_exposure_fen=200,
        realized_loss_when_top_exposure_failed_fen=200,
        realized_loss_when_top_two_exposure_failed_fen=200,
    )
    return BacktestReportData(
        backtest_run=run,
        slices=(backtest_slice,),
        metrics=calculate_backtest_metrics(run, (match,), (slate,)),
        expected_match_ids_by_slice=(expected_match_ids,),
        archive_manifests=(manifest,),
    )


def _manifest(*, synthetic: bool) -> HistoricalArchiveManifest:
    marker = (
        "Synthetic acceptance fixture"
        if synthetic
        else "Licensed historical research source"
    )
    return HistoricalArchiveManifest(
        archive_schema_version="HISTORICAL_ARCHIVE_V1",
        archive_id="report-archive",
        provider_code="REPORT_PROVIDER",
        dataset_kind=HistoricalArchiveDatasetKind.MATCH_RESULTS,
        created_at_utc=datetime(2025, 2, 1, tzinfo=UTC),
        source_reference="https://example.invalid/archive",
        source_description=marker,
        license_note=marker,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        payload_sha256="0" * 64,
        record_count=1,
    )
