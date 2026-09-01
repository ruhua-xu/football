from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
)
from football_system.domain.backtest import (
    RETROSPECTIVE_SOURCE_TIME_RESEARCH,
    BacktestArchiveProvenance,
    BacktestDataMode,
    BacktestMatchSnapshot,
    BacktestMetricsConfig,
    BacktestRun,
    BacktestRunStatus,
    BacktestSlice,
    BacktestSlateSnapshot,
    BacktestStrategySnapshot,
    canonical_json,
    sha256_text,
)
from football_system.domain.market import SelectionKey, ThreeWayProbability
from football_system.domain.services.backtest_metrics import calculate_backtest_metrics
from football_system.domain.settlement import (
    MatchSettlementIssue,
    UnsupportedSettlementReason,
)

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)


def strategy() -> BacktestStrategySnapshot:
    return BacktestStrategySnapshot.from_config(
        "QUANT_ONLY_V1",
        {"budget_fen": 1_000, "minimum_ev": Decimal("0.02")},
    )


def run(
    data_mode: BacktestDataMode = BacktestDataMode.LIVE_STRICT,
) -> BacktestRun:
    return BacktestRun(
        backtest_run_id="backtest-1",
        backtest_version="BACKTEST_V1",
        data_mode=data_mode,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 31),
        strategy_snapshot=strategy(),
        code_revision="test-revision",
        created_at_utc=NOW,
        status=BacktestRunStatus.COMPLETED,
    )


def probabilities(home: str, draw: str, away: str) -> ThreeWayProbability:
    return ThreeWayProbability(
        home_win=Decimal(home),
        draw=Decimal(draw),
        away_win=Decimal(away),
    )


def match_snapshot(
    match_id: str,
    values: ThreeWayProbability,
    *,
    quant_values: ThreeWayProbability | None = None,
    final_values: ThreeWayProbability | None = None,
    slice_id: str = "slice-1",
    mode: BacktestDataMode = BacktestDataMode.LIVE_STRICT,
    outcome: SelectionKey = SelectionKey.HOME_WIN,
) -> BacktestMatchSnapshot:
    return BacktestMatchSnapshot(
        backtest_run_id="backtest-1",
        data_mode=mode,
        slice_id=slice_id,
        match_id=match_id,
        outcome=outcome,
        p_market=values,
        p_quant=quant_values or values,
        p_final=final_values or values,
    )


def slate(
    number: int,
    *,
    profit_loss_fen: int = 0,
    ticket_count: int = 1,
    winning_ticket_count: int = 0,
    match_count: int = 0,
    settled_match_count: int = 0,
    mode: BacktestDataMode = BacktestDataMode.LIVE_STRICT,
) -> BacktestSlateSnapshot:
    stake = ticket_count * 100
    payout = stake + profit_loss_fen
    return BacktestSlateSnapshot(
        backtest_run_id="backtest-1",
        data_mode=mode,
        slice_id=f"slice-{number}",
        decision_as_of_at_utc=NOW + timedelta(days=number),
        match_count=match_count,
        settled_match_count=settled_match_count,
        ticket_count=ticket_count,
        settled_ticket_count=ticket_count,
        winning_ticket_count=winning_ticket_count,
        budget_fen=stake,
        stake_fen=stake,
        settled_stake_fen=stake,
        cash_fen=0,
        gross_payout_fen=payout,
        profit_loss_fen=profit_loss_fen,
        is_no_bet=False,
        ticket_odds=tuple(Decimal("2.00") for _ in range(ticket_count)),
        ticket_probabilities=tuple(Decimal("0.50") for _ in range(ticket_count)),
        selection_evs=tuple(Decimal("0.10") for _ in range(ticket_count * 2)),
        max_match_exposure_fen=stake,
        max_selection_exposure_fen=stake,
    )


def no_bet_slate(
    number: int,
    *,
    match_count: int = 0,
    settled_match_count: int = 0,
    mode: BacktestDataMode = BacktestDataMode.LIVE_STRICT,
) -> BacktestSlateSnapshot:
    return BacktestSlateSnapshot(
        backtest_run_id="backtest-1",
        data_mode=mode,
        slice_id=f"slice-{number}",
        decision_as_of_at_utc=NOW + timedelta(days=number),
        match_count=match_count,
        settled_match_count=settled_match_count,
        ticket_count=0,
        settled_ticket_count=0,
        winning_ticket_count=0,
        budget_fen=100,
        stake_fen=0,
        settled_stake_fen=0,
        cash_fen=100,
        gross_payout_fen=0,
        profit_loss_fen=0,
        is_no_bet=True,
    )


def test_strategy_snapshot_requires_canonical_json_and_matching_hash() -> None:
    snapshot = strategy()

    assert snapshot.strategy_config_json == ('{"budget_fen":1000,"minimum_ev":"0.02"}')
    assert snapshot.strategy_config_hash == sha256_text(snapshot.strategy_config_json)
    assert snapshot.strategy_config_json == canonical_json(
        {"minimum_ev": Decimal("0.02"), "budget_fen": 1_000}
    )

    with pytest.raises(ValidationError, match="must be canonical"):
        BacktestStrategySnapshot(
            strategy_version="QUANT_ONLY_V1",
            strategy_config_json='{ "budget_fen": 1000 }',
            strategy_config_hash=sha256_text('{ "budget_fen": 1000 }'),
        )
    with pytest.raises(ValidationError, match="hash does not match"):
        BacktestStrategySnapshot(
            strategy_version=snapshot.strategy_version,
            strategy_config_json=snapshot.strategy_config_json,
            strategy_config_hash="0" * 64,
        )


def test_archive_provenance_is_frozen_canonical_and_identity_unique() -> None:
    manifest = HistoricalArchiveManifest(
        archive_schema_version="HISTORICAL_ARCHIVE_V1",
        archive_id="archive-b",
        provider_code="PROVIDER_B",
        dataset_kind=HistoricalArchiveDatasetKind.MATCH_RESULTS,
        created_at_utc=NOW,
        source_reference="https://example.invalid/archive-b",
        source_description="Licensed test archive",
        license_note="Test use",
        data_mode=BacktestDataMode.LIVE_STRICT,
        payload_sha256="b" * 64,
        record_count=1,
    )
    second = BacktestArchiveProvenance(
        archive_id="archive-a",
        archive_schema_version="HISTORICAL_ARCHIVE_V1",
        provider_code="PROVIDER_A",
        dataset_kind=HistoricalArchiveDatasetKind.FIXTURES,
        payload_sha256="a" * 64,
    )
    from_manifest = BacktestArchiveProvenance.from_manifest(manifest)
    payload = run().model_dump(mode="python", exclude_computed_fields=True)
    backtest_run = BacktestRun.model_validate(
        {
            **payload,
            "archive_provenance": (from_manifest, second),
            "expected_slice_ids": ("slice-1", "slice-2"),
        }
    )

    assert from_manifest.archive_id == manifest.archive_id
    assert from_manifest.payload_sha256 == manifest.payload_sha256
    assert tuple(item.archive_id for item in backtest_run.archive_provenance) == (
        "archive-a",
        "archive-b",
    )
    with pytest.raises(ValidationError, match="frozen"):
        from_manifest.archive_id = "changed"
    with pytest.raises(ValidationError, match="provenance IDs"):
        BacktestRun.model_validate({**payload, "archive_provenance": (second, second)})
    with pytest.raises(ValidationError, match="provider/kind/hash"):
        BacktestRun.model_validate(
            {
                **payload,
                "archive_provenance": (
                    second,
                    second.model_copy(update={"archive_id": "archive-c"}),
                ),
            }
        )
    with pytest.raises(ValidationError, match="slice IDs"):
        BacktestRun.model_validate(
            {**payload, "expected_slice_ids": ("slice-1", "slice-1")}
        )


def test_slice_enforces_cutoff_order_and_derives_exact_coverage() -> None:
    backtest_slice = BacktestSlice(
        slice_id="slice-1",
        backtest_run_id="backtest-1",
        data_mode=BacktestDataMode.LIVE_STRICT,
        decision_as_of_at_utc=NOW,
        kickoff_from_utc=NOW + timedelta(hours=1),
        kickoff_to_utc=NOW + timedelta(hours=2),
        evaluation_as_of_at_utc=NOW + timedelta(hours=3),
        analysis_run_id="analysis-1",
        decision_input_manifest_hash="a" * 64,
        match_result_ids=("result-1", "result-2", "result-3"),
        expected_match_ids=("match-1", "match-2", "match-3", "match-4"),
        match_count=4,
        settled_match_count=3,
        settled_ticket_count=1,
        unsettled_ticket_count=1,
    )

    assert backtest_slice.coverage == Decimal("0.5")
    assert backtest_slice.ticket_count == 2
    assert backtest_slice.match_coverage == Decimal("0.75")
    assert backtest_slice.decision_input_manifest_hash == "a" * 64
    assert backtest_slice.match_result_ids == (
        "result-1",
        "result-2",
        "result-3",
    )
    assert backtest_slice.expected_match_ids == (
        "match-1",
        "match-2",
        "match-3",
        "match-4",
    )
    payload = backtest_slice.model_dump(mode="python", exclude_computed_fields=True)
    for required_field in (
        "kickoff_from_utc",
        "kickoff_to_utc",
        "expected_match_ids",
    ):
        incomplete = dict(payload)
        incomplete.pop(required_field)
        with pytest.raises(ValidationError, match=required_field):
            BacktestSlice.model_validate(incomplete)
    with pytest.raises(ValidationError, match="frozen"):
        backtest_slice.match_count = 5
    with pytest.raises(ValidationError, match="evaluation cutoff must be after"):
        BacktestSlice(
            slice_id="slice-2",
            backtest_run_id="backtest-1",
            data_mode=BacktestDataMode.LIVE_STRICT,
            decision_as_of_at_utc=NOW,
            kickoff_from_utc=NOW,
            kickoff_to_utc=NOW,
            evaluation_as_of_at_utc=NOW,
            analysis_run_id="analysis-2",
            expected_match_ids=("match-1",),
            match_count=1,
            settled_match_count=0,
            settled_ticket_count=0,
            unsettled_ticket_count=0,
        )
    with pytest.raises(ValidationError, match="cover every settled match"):
        BacktestSlice(
            slice_id="slice-3",
            backtest_run_id="backtest-1",
            data_mode=BacktestDataMode.LIVE_STRICT,
            decision_as_of_at_utc=NOW,
            kickoff_from_utc=NOW,
            kickoff_to_utc=NOW + timedelta(minutes=30),
            evaluation_as_of_at_utc=NOW + timedelta(hours=1),
            analysis_run_id="analysis-3",
            decision_input_manifest_hash="b" * 64,
            match_result_ids=("result-1",),
            expected_match_ids=("match-1", "match-2"),
            match_count=2,
            settled_match_count=2,
            settled_ticket_count=0,
            unsettled_ticket_count=0,
        )
    with pytest.raises(ValidationError, match="result IDs must be unique"):
        BacktestSlice(
            slice_id="slice-4",
            backtest_run_id="backtest-1",
            data_mode=BacktestDataMode.LIVE_STRICT,
            decision_as_of_at_utc=NOW,
            kickoff_from_utc=NOW,
            kickoff_to_utc=NOW + timedelta(minutes=30),
            evaluation_as_of_at_utc=NOW + timedelta(hours=1),
            analysis_run_id="analysis-4",
            decision_input_manifest_hash="c" * 64,
            match_result_ids=("result-1", "result-1"),
            expected_match_ids=("match-1", "match-2"),
            match_count=2,
            settled_match_count=2,
            settled_ticket_count=0,
            unsettled_ticket_count=0,
        )


@pytest.mark.parametrize(
    ("update", "message"),
    (
        (
            {"kickoff_to_utc": NOW + timedelta(hours=3)},
            "evaluation cutoff must follow kickoff window",
        ),
        (
            {"expected_match_ids": ("match-1",)},
            "expected match count is inconsistent",
        ),
        (
            {"missing_decision_match_ids": ("match-2", "match-1")},
            "preserve expected order",
        ),
        (
            {"missing_decision_match_ids": ("outside-match",)},
            "missing decision matches must be expected",
        ),
        (
            {
                "match_result_issues": (
                    MatchSettlementIssue(
                        match_id="outside-match",
                        reason=UnsupportedSettlementReason.VOID,
                    ),
                )
            },
            "result issues must reference expected matches",
        ),
        (
            {
                "match_result_issues": (
                    MatchSettlementIssue(
                        match_id="match-2",
                        reason=UnsupportedSettlementReason.VOID,
                    ),
                    MatchSettlementIssue(
                        match_id="match-1",
                        reason=UnsupportedSettlementReason.CANCELLATION,
                    ),
                )
            },
            "result issues must preserve expected match order",
        ),
        (
            {
                "match_result_issues": (
                    MatchSettlementIssue(
                        match_id="match-1",
                        reason=UnsupportedSettlementReason.VOID,
                    ),
                ),
                "missing_decision_match_ids": ("match-1",),
            },
            "result issues cannot reference missing decision matches",
        ),
    ),
)
def test_slice_rejects_inconsistent_planned_structure(
    update: dict[str, object],
    message: str,
) -> None:
    payload = {
        "slice_id": "planned-slice",
        "backtest_run_id": "backtest-1",
        "data_mode": BacktestDataMode.LIVE_STRICT,
        "decision_as_of_at_utc": NOW,
        "kickoff_from_utc": NOW + timedelta(hours=1),
        "kickoff_to_utc": NOW + timedelta(hours=2),
        "evaluation_as_of_at_utc": NOW + timedelta(hours=3),
        "analysis_run_id": "analysis-planned",
        "expected_match_ids": ("match-1", "match-2"),
        "match_count": 2,
        "settled_match_count": 0,
        "settled_ticket_count": 0,
        "unsettled_ticket_count": 0,
    }

    with pytest.raises(ValidationError, match=message):
        BacktestSlice.model_validate({**payload, **update})


def test_multiclass_brier_has_hand_computable_outcome_components() -> None:
    metrics = calculate_backtest_metrics(
        run(),
        (
            match_snapshot(
                "match-1",
                probabilities("0.50", "0.30", "0.20"),
                quant_values=probabilities("0.60", "0.20", "0.20"),
                final_values=probabilities("0.70", "0.20", "0.10"),
            ),
        ),
        (no_bet_slate(1, match_count=1, settled_match_count=1),),
    )

    assert metrics.p_market.brier_by_outcome.home_win == Decimal("0.25")
    assert metrics.p_market.brier_by_outcome.draw == Decimal("0.09")
    assert metrics.p_market.brier_by_outcome.away_win == Decimal("0.04")
    assert metrics.p_market.multiclass_brier_score == Decimal("0.38")
    assert metrics.p_quant.multiclass_brier_score == Decimal("0.24")
    assert metrics.p_final.multiclass_brier_score == Decimal("0.14")


def test_log_loss_uses_versioned_two_sided_epsilon_clip() -> None:
    metrics = calculate_backtest_metrics(
        run(),
        (match_snapshot("match-1", probabilities("0", "0.50", "0.50")),),
        (no_bet_slate(1, match_count=1, settled_match_count=1),),
        BacktestMetricsConfig(
            log_loss_clip_version="EPSILON_CLIP_TEST_V1",
            log_loss_epsilon=Decimal("0.01"),
        ),
    )

    assert metrics.log_loss_clip_version == "EPSILON_CLIP_TEST_V1"
    assert metrics.log_loss_epsilon == Decimal("0.01")
    assert metrics.p_final.multiclass_log_loss == Decimal("4.605170185988")


def test_calibration_boundaries_are_left_closed_and_one_is_in_last_bin() -> None:
    records = (
        match_snapshot("match-1", probabilities("0.10", "0.20", "0.70")),
        match_snapshot(
            "match-2",
            probabilities("1.00", "0.00", "0.00"),
            outcome=SelectionKey.DRAW,
        ),
    )
    metrics = calculate_backtest_metrics(
        run(),
        records,
        (no_bet_slate(1, match_count=2, settled_match_count=2),),
    )
    bins = metrics.p_final.calibration_bins

    assert bins[0].label == "0.0-0.1"
    assert bins[0].count == 2
    assert bins[1].count == 1
    assert bins[1].mean_predicted_probability == Decimal("0.10")
    assert bins[2].count == 1
    assert bins[7].count == 1
    assert bins[9].count == 1
    assert bins[9].includes_upper_bound
    assert metrics.p_final.expected_calibration_error == Decimal("0.633333333333")


def test_drawdown_loss_streak_no_bet_and_ticket_aggregates_are_exact() -> None:
    slates = (
        slate(1, profit_loss_fen=100, winning_ticket_count=1),
        slate(2, profit_loss_fen=-50, ticket_count=2, winning_ticket_count=1),
        slate(3, profit_loss_fen=-100),
        no_bet_slate(4),
        slate(5, profit_loss_fen=-20, ticket_count=2, winning_ticket_count=1),
    )
    metrics = calculate_backtest_metrics(run(), (), slates)

    assert metrics.profit_loss_fen == -70
    assert metrics.max_drawdown_fen == 170
    assert metrics.max_consecutive_losing_slates == 2
    assert metrics.no_bet_count == 1
    assert metrics.no_bet_ratio == Decimal("0.20")
    assert metrics.ticket_count == 6
    assert metrics.ticket_hit_rate == Decimal("0.50")
    assert metrics.total_budget_fen == 700
    assert metrics.total_stake_fen == 600
    assert metrics.total_cash_fen == 100
    assert metrics.gross_payout_fen == 530
    assert metrics.roi_on_budget == Decimal("-0.10")
    assert metrics.roi_on_deployed == Decimal("-0.116666666667")
    assert metrics.average_ticket_odds == Decimal("2.00")
    assert metrics.average_ticket_probability == Decimal("0.50")
    assert metrics.average_selection_ev == Decimal("0.10")


def test_partial_coverage_keeps_deployed_and_settled_stake_distinct() -> None:
    partial = BacktestSlateSnapshot(
        backtest_run_id="backtest-1",
        data_mode=BacktestDataMode.LIVE_STRICT,
        slice_id="slice-1",
        decision_as_of_at_utc=NOW,
        match_count=4,
        settled_match_count=2,
        ticket_count=3,
        settled_ticket_count=1,
        winning_ticket_count=0,
        budget_fen=500,
        stake_fen=300,
        settled_stake_fen=100,
        cash_fen=200,
        gross_payout_fen=0,
        profit_loss_fen=-100,
        is_no_bet=False,
        ticket_odds=(Decimal("2"), Decimal("3"), Decimal("4")),
        ticket_probabilities=(Decimal("0.5"), Decimal("0.4"), Decimal("0.3")),
        selection_evs=(Decimal("0.1"), Decimal("0.2")),
        max_match_exposure_fen=200,
        max_selection_exposure_fen=100,
        realized_loss_when_top_exposure_failed_fen=100,
        realized_loss_when_top_two_exposure_failed_fen=100,
    )
    records = (
        match_snapshot("match-1", probabilities("0.6", "0.2", "0.2")),
        match_snapshot("match-2", probabilities("0.2", "0.3", "0.5")),
    )
    metrics = calculate_backtest_metrics(run(), records, (partial,))

    assert metrics.slate_coverage == Decimal("0")
    assert metrics.match_coverage == Decimal("0.5")
    assert metrics.ticket_coverage == Decimal("0.333333333333")
    assert metrics.total_stake_fen == 300
    assert metrics.total_settled_stake_fen == 100
    assert metrics.profit_loss_fen == -100
    assert metrics.roi_on_budget == Decimal("-0.2")
    assert metrics.roi_on_deployed == Decimal("-0.333333333333")
    assert metrics.max_match_exposure_fen == 200
    assert metrics.max_selection_exposure_fen == 100
    assert metrics.realized_loss_when_top_exposure_failed_fen == 100
    assert metrics.realized_loss_when_top_two_exposure_failed_fen == 100


def test_research_mode_is_explicit_and_cannot_mix_with_strict_snapshots() -> None:
    research_run = run(BacktestDataMode.SOURCE_TIME_RESEARCH)
    research_slate = no_bet_slate(
        1,
        mode=BacktestDataMode.SOURCE_TIME_RESEARCH,
    )
    metrics = calculate_backtest_metrics(research_run, (), (research_slate,))

    assert research_run.data_mode_label == RETROSPECTIVE_SOURCE_TIME_RESEARCH
    assert metrics.data_mode == BacktestDataMode.SOURCE_TIME_RESEARCH
    assert metrics.data_mode_label == RETROSPECTIVE_SOURCE_TIME_RESEARCH
    assert metrics.retrospective

    with pytest.raises(ValueError, match="data modes cannot be mixed"):
        calculate_backtest_metrics(research_run, (), (no_bet_slate(1),))
