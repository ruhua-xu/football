from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from football_system.domain.backtest import (
    RATIO_QUANTUM,
    BacktestBrierComponents,
    BacktestCalibrationBin,
    BacktestMatchSnapshot,
    BacktestMetrics,
    BacktestMetricsConfig,
    BacktestProbabilityMetrics,
    BacktestRun,
    BacktestSlateSnapshot,
)
from football_system.domain.market import SelectionKey, ThreeWayProbability


def calculate_backtest_metrics(
    run: BacktestRun,
    match_snapshots: Iterable[BacktestMatchSnapshot],
    slate_snapshots: Iterable[BacktestSlateSnapshot],
    config: BacktestMetricsConfig | None = None,
) -> BacktestMetrics:
    """Aggregate deterministic metrics without loading or mutating domain artifacts."""
    metric_config = config or BacktestMetricsConfig()
    matches = tuple(match_snapshots)
    slates = tuple(slate_snapshots)
    _validate_scope(run, matches, slates)

    match_count = sum(slate.match_count for slate in slates)
    settled_match_count = sum(slate.settled_match_count for slate in slates)
    ticket_count = sum(slate.ticket_count for slate in slates)
    settled_ticket_count = sum(slate.settled_ticket_count for slate in slates)
    settled_slate_count = sum(
        slate.settled_match_count == slate.match_count
        and slate.settled_ticket_count == slate.ticket_count
        for slate in slates
    )
    total_budget = sum(slate.budget_fen for slate in slates)
    total_stake = sum(slate.stake_fen for slate in slates)
    total_settled_stake = sum(slate.settled_stake_fen for slate in slates)
    total_cash = sum(slate.cash_fen for slate in slates)
    gross_payout = sum(slate.gross_payout_fen for slate in slates)
    profit_loss = sum(slate.profit_loss_fen for slate in slates)
    winning_ticket_count = sum(slate.winning_ticket_count for slate in slates)
    ticket_odds = tuple(value for slate in slates for value in slate.ticket_odds)
    ticket_probabilities = tuple(
        value for slate in slates for value in slate.ticket_probabilities
    )
    selection_evs = tuple(value for slate in slates for value in slate.selection_evs)
    max_drawdown, max_losing_streak = _drawdown_and_losing_streak(slates)

    return BacktestMetrics(
        backtest_run_id=run.backtest_run_id,
        backtest_version=run.backtest_version,
        data_mode=run.data_mode,
        metrics_version=metric_config.metrics_version,
        log_loss_clip_version=metric_config.log_loss_clip_version,
        log_loss_epsilon=metric_config.log_loss_epsilon,
        p_market=_probability_metrics(matches, "p_market", metric_config),
        p_quant=_probability_metrics(matches, "p_quant", metric_config),
        p_final=_probability_metrics(matches, "p_final", metric_config),
        slate_count=len(slates),
        settled_slate_count=settled_slate_count,
        slate_coverage=_ratio(settled_slate_count, len(slates)),
        match_count=match_count,
        settled_match_count=settled_match_count,
        match_coverage=_ratio(settled_match_count, match_count),
        ticket_count=ticket_count,
        settled_ticket_count=settled_ticket_count,
        ticket_coverage=_ratio(settled_ticket_count, ticket_count),
        total_budget_fen=total_budget,
        total_stake_fen=total_stake,
        total_settled_stake_fen=total_settled_stake,
        total_cash_fen=total_cash,
        gross_payout_fen=gross_payout,
        profit_loss_fen=profit_loss,
        roi_on_budget=_ratio(profit_loss, total_budget),
        roi_on_deployed=_ratio(profit_loss, total_stake),
        winning_ticket_count=winning_ticket_count,
        ticket_hit_rate=_ratio(winning_ticket_count, settled_ticket_count),
        no_bet_count=sum(slate.is_no_bet for slate in slates),
        no_bet_ratio=_ratio(sum(slate.is_no_bet for slate in slates), len(slates)),
        max_drawdown_fen=max_drawdown,
        max_consecutive_losing_slates=max_losing_streak,
        average_ticket_odds=_average(ticket_odds),
        average_ticket_probability=_average(ticket_probabilities),
        average_selection_ev=_average(selection_evs),
        max_match_exposure_fen=max(
            (slate.max_match_exposure_fen for slate in slates), default=0
        ),
        max_selection_exposure_fen=max(
            (slate.max_selection_exposure_fen for slate in slates), default=0
        ),
        realized_loss_when_top_exposure_failed_fen=sum(
            slate.realized_loss_when_top_exposure_failed_fen for slate in slates
        ),
        realized_loss_when_top_two_exposure_failed_fen=sum(
            slate.realized_loss_when_top_two_exposure_failed_fen for slate in slates
        ),
    )


compute_backtest_metrics = calculate_backtest_metrics


def _validate_scope(
    run: BacktestRun,
    matches: tuple[BacktestMatchSnapshot, ...],
    slates: tuple[BacktestSlateSnapshot, ...],
) -> None:
    records = (*matches, *slates)
    if any(record.backtest_run_id != run.backtest_run_id for record in records):
        raise ValueError("metric snapshot belongs to another backtest run")
    if any(record.data_mode != run.data_mode for record in records):
        raise ValueError("backtest data modes cannot be mixed")

    slice_ids = [slate.slice_id for slate in slates]
    if len(slice_ids) != len(set(slice_ids)):
        raise ValueError("backtest slate snapshots must have unique slice IDs")
    match_keys = [(match.slice_id, match.match_id) for match in matches]
    if len(match_keys) != len(set(match_keys)):
        raise ValueError("settled match snapshots must be unique within a slice")
    known_slices = set(slice_ids)
    if any(match.slice_id not in known_slices for match in matches):
        raise ValueError("settled match snapshot has no corresponding slate")
    matches_by_slice = Counter(match.slice_id for match in matches)
    if any(
        matches_by_slice[slate.slice_id] != slate.settled_match_count
        for slate in slates
    ):
        raise ValueError("settled match snapshots must match each slate's coverage")


def _probability_metrics(
    matches: tuple[BacktestMatchSnapshot, ...],
    probability_field: str,
    config: BacktestMetricsConfig,
) -> BacktestProbabilityMetrics:
    component_totals = {selection: Decimal(0) for selection in SelectionKey}
    bin_probability_totals = [Decimal(0) for _ in range(10)]
    bin_event_counts = [0 for _ in range(10)]
    bin_counts = [0 for _ in range(10)]
    log_loss_total = Decimal(0)

    with localcontext() as context:
        context.prec = 50
        for match in matches:
            probabilities = getattr(match, probability_field)
            if not isinstance(probabilities, ThreeWayProbability):
                raise TypeError(f"{probability_field} is not a three-way probability")
            for selection, probability in probabilities.items():
                observed = int(selection == match.outcome)
                error = probability - Decimal(observed)
                component_totals[selection] += error * error
                bin_index = min(int(probability * 10), 9)
                bin_probability_totals[bin_index] += probability
                bin_event_counts[bin_index] += observed
                bin_counts[bin_index] += 1
            true_probability = probabilities.for_selection(match.outcome)
            clipped = max(
                config.log_loss_epsilon,
                min(Decimal(1) - config.log_loss_epsilon, true_probability),
            )
            log_loss_total -= clipped.ln()

    sample_count = len(matches)
    if sample_count == 0:
        components = BacktestBrierComponents()
        log_loss = None
        ece = None
    else:
        components = BacktestBrierComponents(
            home_win=_metric(component_totals[SelectionKey.HOME_WIN] / sample_count),
            draw=_metric(component_totals[SelectionKey.DRAW] / sample_count),
            away_win=_metric(component_totals[SelectionKey.AWAY_WIN] / sample_count),
        )
        log_loss = _metric(log_loss_total / sample_count)
        total_calibration_observations = sample_count * 3
        ece_value = sum(
            (
                abs(
                    bin_probability_totals[index] / count
                    - Decimal(bin_event_counts[index]) / count
                )
                * Decimal(count)
                / Decimal(total_calibration_observations)
            )
            for index, count in enumerate(bin_counts)
            if count
        )
        ece = _metric(ece_value)

    calibration_bins = tuple(
        _calibration_bin(
            index,
            bin_counts[index],
            bin_probability_totals[index],
            bin_event_counts[index],
        )
        for index in range(10)
    )
    brier_values = (components.home_win, components.draw, components.away_win)
    brier_score = (
        _metric(sum((value for value in brier_values if value is not None), Decimal(0)))
        if sample_count
        else None
    )
    return BacktestProbabilityMetrics(
        sample_count=sample_count,
        multiclass_brier_score=brier_score,
        brier_by_outcome=components,
        multiclass_log_loss=log_loss,
        calibration_bins=calibration_bins,
        expected_calibration_error=ece,
    )


def _calibration_bin(
    index: int,
    count: int,
    probability_total: Decimal,
    event_count: int,
) -> BacktestCalibrationBin:
    lower = Decimal(index) / Decimal(10)
    upper = Decimal(index + 1) / Decimal(10)
    if count == 0:
        mean_probability = None
        observed_frequency = None
        absolute_gap = None
    else:
        mean_probability = _metric(probability_total / count)
        observed_frequency = _metric(Decimal(event_count) / count)
        absolute_gap = _metric(
            abs(probability_total / count - Decimal(event_count) / count)
        )
    return BacktestCalibrationBin(
        label=f"{lower:.1f}-{upper:.1f}",
        lower_bound=lower,
        upper_bound=upper,
        includes_upper_bound=index == 9,
        count=count,
        mean_predicted_probability=mean_probability,
        observed_frequency=observed_frequency,
        absolute_gap=absolute_gap,
    )


def _drawdown_and_losing_streak(
    slates: tuple[BacktestSlateSnapshot, ...],
) -> tuple[int, int]:
    cumulative_profit = 0
    peak_profit = 0
    max_drawdown = 0
    current_losing_streak = 0
    max_losing_streak = 0
    for slate in sorted(
        slates,
        key=lambda item: (item.decision_as_of_at_utc, item.slice_id),
    ):
        cumulative_profit += slate.profit_loss_fen
        peak_profit = max(peak_profit, cumulative_profit)
        max_drawdown = max(max_drawdown, peak_profit - cumulative_profit)
        if slate.profit_loss_fen < 0:
            current_losing_streak += 1
            max_losing_streak = max(max_losing_streak, current_losing_streak)
        else:
            current_losing_streak = 0
    return max_drawdown, max_losing_streak


def _average(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    return _metric(sum(values, Decimal(0)) / len(values))


def _metric(value: Decimal) -> Decimal:
    return value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_EVEN)


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return _metric(Decimal(numerator) / Decimal(denominator))
