from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, ROUND_HALF_EVEN

from football_system.domain.backtest import (
    RATIO_QUANTUM,
    BacktestMetricsConfig,
    BacktestRun,
    BacktestSlateSnapshot,
)
from football_system.domain.backtest_v2 import (
    BACKTEST_V2,
    BacktestV2Metrics,
    BacktestV2Slice,
)
from football_system.domain.prediction import QuantModelEvaluationStatus
from football_system.domain.services.backtest_metrics import (
    calculate_probability_metrics,
)


def calculate_backtest_v2_metrics(
    run: BacktestRun,
    slices: Iterable[BacktestV2Slice],
    slate_snapshots: Iterable[BacktestSlateSnapshot],
    config: BacktestMetricsConfig | None = None,
) -> BacktestV2Metrics:
    metric_config = config or BacktestMetricsConfig()
    slice_values = tuple(slices)
    slates = tuple(slate_snapshots)
    if run.backtest_version != BACKTEST_V2:
        raise ValueError("BACKTEST_V2 metrics require a BACKTEST_V2 run")
    if len(slice_values) != len(slates):
        raise ValueError("BACKTEST_V2 requires one financial snapshot per slice")
    if any(
        item.backtest_run_id != run.backtest_run_id or item.data_mode != run.data_mode
        for item in (*slice_values, *slates)
    ):
        raise ValueError("BACKTEST_V2 metric inputs have inconsistent run scope")
    slice_ids = tuple(item.slice_id for item in slice_values)
    if len(slice_ids) != len(set(slice_ids)):
        raise ValueError("BACKTEST_V2 metric slices must be unique")
    slates_by_id = {item.slice_id: item for item in slates}
    if set(slates_by_id) != set(slice_ids):
        raise ValueError("BACKTEST_V2 financial snapshots do not cover its slices")

    matches = tuple(
        snapshot for backtest_slice in slice_values for snapshot in backtest_slice.match_snapshots
    )
    evaluations = tuple(
        evaluation
        for backtest_slice in slice_values
        for evaluation in backtest_slice.decision_snapshot.evaluations
    )
    quant_matches = tuple(
        item
        for item in matches
        if item.quant_status is QuantModelEvaluationStatus.AVAILABLE
    )
    planned_count = sum(len(item.expected_match_ids) for item in slice_values)
    decision_count = len(evaluations)
    available_count = sum(
        item.status is QuantModelEvaluationStatus.AVAILABLE for item in evaluations
    )
    unavailable_count = decision_count - available_count
    ordered_slates = tuple(slates_by_id[slice_id] for slice_id in slice_ids)
    total_budget = sum(item.budget_fen for item in ordered_slates)
    total_stake = sum(item.stake_fen for item in ordered_slates)
    settled_stake = sum(item.settled_stake_fen for item in ordered_slates)
    gross_payout = sum(item.gross_payout_fen for item in ordered_slates)
    profit_loss = gross_payout - settled_stake
    max_drawdown, max_losing_streak = _drawdown_and_losing_streak(ordered_slates)

    return BacktestV2Metrics(
        backtest_run_id=run.backtest_run_id,
        data_mode=run.data_mode,
        log_loss_clip_version=metric_config.log_loss_clip_version,
        log_loss_epsilon=metric_config.log_loss_epsilon,
        p_market=calculate_probability_metrics(
            ((item.outcome, item.p_market) for item in matches),
            metric_config,
        ),
        p_quant=calculate_probability_metrics(
            ((item.outcome, item.p_quant) for item in quant_matches if item.p_quant),
            metric_config,
        ),
        p_final=calculate_probability_metrics(
            ((item.outcome, item.p_final) for item in quant_matches if item.p_final),
            metric_config,
        ),
        slate_count=len(slice_values),
        planned_target_count=planned_count,
        decision_target_count=decision_count,
        result_target_count=len(matches),
        quant_available_count=available_count,
        quant_unavailable_count=unavailable_count,
        quant_scored_count=len(quant_matches),
        decision_coverage=_ratio(decision_count, planned_count),
        result_coverage=_ratio(len(matches), planned_count),
        quant_availability=_ratio(available_count, decision_count),
        quant_scored_coverage=_ratio(len(quant_matches), planned_count),
        ticket_count=sum(item.ticket_count for item in ordered_slates),
        settled_ticket_count=sum(item.settled_ticket_count for item in ordered_slates),
        total_budget_fen=total_budget,
        total_stake_fen=total_stake,
        total_settled_stake_fen=settled_stake,
        gross_payout_fen=gross_payout,
        profit_loss_fen=profit_loss,
        roi_on_budget=_ratio(profit_loss, total_budget),
        roi_on_deployed=_ratio(profit_loss, total_stake),
        no_bet_count=sum(item.is_no_bet for item in ordered_slates),
        max_drawdown_fen=max_drawdown,
        max_consecutive_losing_slates=max_losing_streak,
    )


def _drawdown_and_losing_streak(
    slates: tuple[BacktestSlateSnapshot, ...],
) -> tuple[int, int]:
    cumulative_profit = 0
    peak_profit = 0
    max_drawdown = 0
    losing_streak = 0
    max_losing_streak = 0
    for slate in sorted(
        slates,
        key=lambda item: (item.decision_as_of_at_utc, item.slice_id),
    ):
        cumulative_profit += slate.profit_loss_fen
        peak_profit = max(peak_profit, cumulative_profit)
        max_drawdown = max(max_drawdown, peak_profit - cumulative_profit)
        if slate.profit_loss_fen < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return max_drawdown, max_losing_streak


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATIO_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
