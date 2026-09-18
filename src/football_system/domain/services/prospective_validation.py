"""Descriptive, full-census validation; never feeds back into prediction parameters."""

from collections import Counter, defaultdict
from decimal import Decimal

from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import content_hash, fixed_decimal, settle_market

LAYERS = ("P_market", "P_quant", "P_base", "P_llm", "P_final")
EVENTS = ("p_gross_payout_gt_zero", "p_break_even_or_better", "p_2x_budget", "p_3x_budget", "p_loss", "p_deep_loss")


@fixed_decimal(128)
def decimal(value):
    value = Decimal(value)
    return Decimal(0) if value == 0 else value.normalize()


@fixed_decimal(128)
def score_distribution(probabilities, outcome):
    if probabilities is None:
        return None
    if outcome not in probabilities.market_key.catalog:
        raise ValueError("OBSERVED_OUTCOME_OUTSIDE_MARKET")
    p = probabilities.probability(outcome)
    return {"brier": decimal(sum((x.probability-int(x.outcome == outcome))**2 for x in probabilities.outcomes)),
            "log_loss": decimal(-p.ln()) if p > 0 else None,
            "log_loss_status": "FINITE" if p > 0 else "INFINITE_ZERO_TRUE_PROBABILITY",
            "probability_mass": decimal(sum((x.probability for x in probabilities.outcomes), Decimal(0)))}


@fixed_decimal(128)
def calibration(records):
    bins = [{"bin": i, "lower": decimal(Decimal(i)/10), "upper": decimal(Decimal(i+1)/10),
             "count": 0, "predicted_sum": Decimal(0), "observed_count": 0} for i in range(10)]
    per_outcome = {}
    buckets = {name: {"count": 0, "predicted_sum": Decimal(0), "observed_count": 0} for name in ("FAVORITE", "DRAW", "UNDERDOG", "NOT_APPLICABLE")}
    for probabilities, observed, reference in records:
        three = probabilities.market_key.market_type.value in {"THREE_WAY", "HANDICAP_THREE_WAY"}
        favorite = None
        if three and reference is not None:
            teams = [x for x in reference.outcomes if x.outcome.value != "DRAW"]
            favorite = max(teams, key=lambda x: x.probability).outcome
        for value in probabilities.outcomes:
            index = min(9, int(value.probability*10))
            row = bins[index]
            row["count"] += 1
            row["predicted_sum"] += value.probability
            row["observed_count"] += int(value.outcome == observed)
            outcome_row = per_outcome.setdefault(value.outcome.value, {"outcome": value.outcome.value, "count": 0, "predicted_sum": Decimal(0), "observed_count": 0})
            outcome_row["count"] += 1
            outcome_row["predicted_sum"] += value.probability
            outcome_row["observed_count"] += int(value.outcome == observed)
            bucket = "DRAW" if three and value.outcome.value == "DRAW" else "FAVORITE" if value.outcome == favorite else "UNDERDOG" if three and favorite is not None else "NOT_APPLICABLE"
            buckets[bucket]["count"] += 1
            buckets[bucket]["predicted_sum"] += value.probability
            buckets[bucket]["observed_count"] += int(value.outcome == observed)
    def finish(row):
        n = row["count"]
        return {**row, "predicted_sum": decimal(row["predicted_sum"]),
                "mean_predicted": decimal(row["predicted_sum"]/n) if n else None,
                "observed_frequency": decimal(Decimal(row["observed_count"])/n) if n else None}
    total = sum(row["count"] for row in bins)
    ece = decimal(sum(abs(row["predicted_sum"]-row["observed_count"]) for row in bins)/total) if total else None
    return {"one_vs_rest_outcome_count": total, "bins": tuple(finish(row) for row in bins), "ece": ece,
            "predicted_vs_observed": tuple(finish(per_outcome[key]) for key in sorted(per_outcome)),
            "buckets": {key: finish(row) for key, row in buckets.items()},
            "bucket_basis": "P_BASE_ELSE_P_MARKET; TEAM_FAVORITE_AND_FIXED_DRAW_FOR_THREE_WAY_FAMILIES_ONLY"}


@fixed_decimal(128)
def layer_summary(records):
    scores = [score_distribution(p, observed) for p, observed, _ in records]
    finite = [s["log_loss"] for s in scores if s["log_loss"] is not None]
    return {"observation_count": len(scores),
            "mean_brier": decimal(sum((s["brier"] for s in scores), Decimal(0))/len(scores)) if scores else None,
            "mean_log_loss": decimal(sum(finite, Decimal(0))/len(scores)) if scores and len(finite) == len(scores) else None,
            "infinite_log_loss_count": len(scores)-len(finite),
            "calibration": calibration(records)}


def correction_outcome(base_score, final_score, review_status):
    if review_status == "UNAVAILABLE":
        return "abstained"
    if base_score is None or final_score is None:
        return "unavailable"
    change = final_score["brier"]-base_score["brier"]
    return "improved" if change < 0 else "worsened" if change > 0 else "neutral"


@fixed_decimal(128)
def report_values(epoch, census, settled, locked, as_of, created_at, basis, watermark=0):
    """Repository supplies ALL visible epoch census rows, not user-selected case IDs.

    settled rows: (run, lock, settlement, evaluation, observation-by-match)
    locked rows: (run, lock, evaluation, source-plan)
    """
    if len(census) > epoch.policy.max_epoch_runs or sum(len(lock.frames) for _, lock, *_ in settled) > epoch.policy.max_report_units:
        raise ValueError("PROSPECTIVE_REPORT_SPACE_TOO_LARGE")
    observations = defaultdict(list)
    corrected = []
    for run, lock, settlement, _, results in settled:
        for frame in lock.frames:
            result = results[frame.identity.match_id].normalized_result
            outcome = settle_market(frame.market_key, result.home_goals, result.away_goals)
            layers = {layer.name: layer.probabilities for layer in frame.layers}
            reference = layers["P_base"] or layers["P_market"]
            key = (frame.identity.competition_id, frame.market_key.canonical)
            observations[key].append((run, frame, outcome, layers, reference))
            before, after = (score_distribution(layers[name], outcome) for name in ("P_base", "P_final"))
            category = correction_outcome(before, after, frame.review_status)
            log_delta = None
            log_change = "UNAVAILABLE"
            if before and after:
                left, right = before["log_loss"], after["log_loss"]
                if left is not None and right is not None:
                    log_delta = decimal(right-left)
                    log_change = "improved" if log_delta < 0 else "worsened" if log_delta > 0 else "neutral"
                else:
                    log_change = "neutral" if left is right is None else "improved" if left is None else "worsened"
            corrected.append({"run_id": run.artifact_id, "match_id": frame.identity.match_id,
                "market_key": frame.market_key.canonical, "categories": frame.correction_categories,
                "classification": run.data_classification, "result": category,
                "brier_delta_final_minus_base": decimal(after["brier"]-before["brier"]) if before and after else None,
                "log_loss_delta_final_minus_base": log_delta, "log_loss_direction": log_change,
                "review_status": frame.review_status})
    quality, comparisons = [], []
    for (competition, market), rows in sorted(observations.items()):
        layer_records = {name: [(layers[name], observed, reference) for _, _, observed, layers, reference in rows if layers[name] is not None] for name in LAYERS}
        real = sum(run.data_classification == "REAL_SOURCE_DATA" and lock_clock_is_real(run, epoch) for run, *_ in rows)
        quality.append({"competition_id": competition, "market_key": market, "observation_count": len(rows),
            "real_observation_count": real, "sample_status": "DESCRIPTIVE_REAL_SAMPLE_AVAILABLE" if real >= epoch.policy.minimum_real_observations else "INSUFFICIENT_PROSPECTIVE_SAMPLE",
            "layers": {name: layer_summary(values) for name, values in layer_records.items()},
            "layer_unavailable_count": {name: len(rows)-len(values) for name, values in layer_records.items()}})
        for i, left in enumerate(LAYERS):
            for right in LAYERS[i+1:]:
                paired = [(layers[left], layers[right], observed, reference) for _, _, observed, layers, reference in rows if layers[left] is not None and layers[right] is not None]
                left_summary = layer_summary([(p, o, reference) for p, _, o, reference in paired])
                right_summary = layer_summary([(p, o, ref) for _, p, o, ref in paired])
                comparisons.append({"competition_id": competition, "market_key": market, "left": left, "right": right,
                    "paired_observation_count": len(paired), "delta_direction": "RIGHT_MINUS_LEFT",
                    "brier_delta": decimal(right_summary["mean_brier"]-left_summary["mean_brier"]) if paired else None,
                    "log_loss_delta": decimal(right_summary["mean_log_loss"]-left_summary["mean_log_loss"]) if left_summary["mean_log_loss"] is not None and right_summary["mean_log_loss"] is not None else None,
                    "left_infinite_log_loss_count": left_summary["infinite_log_loss_count"], "right_infinite_log_loss_count": right_summary["infinite_log_loss_count"],
                    "calibration_ece_delta": decimal(right_summary["calibration"]["ece"]-left_summary["calibration"]["ece"]) if paired else None})
    totals = Counter(r["result"] for r in corrected)
    by_category = {}
    for row in corrected:
        for category in row["categories"]:
            by_category.setdefault(category, Counter())[row["result"]] += 1
    names = ("improved", "worsened", "neutral", "abstained", "unavailable")
    correction = {"classification_basis": "BRIER_FINAL_MINUS_BASE; LOG_LOSS_DIRECTION_SEPARATE",
                  "counts": {name: totals[name] for name in names},
                  "categories": {key: {name: value[name] for name in names} for key, value in sorted(by_category.items())},
                  "categories_may_overlap": True, "observations": tuple(corrected), "case_selection": "ALL_SETTLED_ACTIVE_LOCKED_UNITS"}
    eligible, positive, quotes, no_bet = 0, 0, [], 0
    for _, lock, _, plan in locked:
        eligible += sum(s.status == "ELIGIBLE" for s in plan.source.selections)
        positive += sum(s.status == "ELIGIBLE" and s.ev > 0 for s in plan.source.selections)
        no_bet += not lock.selected
        ids = {s.ticket_candidate_id for s in lock.selected}
        selections = {leg.artifact_id: leg for candidate in plan.candidates if candidate.artifact_id in ids for atomic in candidate.atomic_bets for leg in atomic.legs}
        quotes.extend(s.fixed_bonus for s in selections.values())
    stake = sum(s.stake_fen for _, _, s, *_ in settled)
    gross = sum(s.gross_payout_fen for _, _, s, *_ in settled)
    profit = sum(s.profit_loss_fen for _, _, s, *_ in settled)
    decision = {"locked_run_count": len(locked), "settled_run_count": len(settled), "eligible_selections": eligible,
        "positive_ev_eligible_selections": positive, "no_bet_count": no_bet,
        "no_bet_frequency": decimal(Decimal(no_bet)/len(locked)) if locked else None,
        "tickets_selected": sum(len(lock.selected) for _, lock, *_ in locked),
        "average_quoted_sp": decimal(sum(quotes, Decimal(0))/len(quotes)) if quotes else None,
        "quoted_sp_basis": "UNIQUE_SELECTED_OUTCOME_PER_LOCK", "total_locked_stake_fen": sum(lock.stake_fen for _, lock, *_ in locked),
        "settled_stake_fen": stake, "gross_payout_fen": gross, "net_profit_loss_fen": profit,
        "expected_gross_payout_fen_same_settled_runs": decimal(sum((e.metrics.expected_gross_payout_fen for _, _, _, e, _ in settled), Decimal(0))),
        "expected_profit_fen_same_settled_runs": decimal(sum((e.metrics.expected_profit_fen for _, _, _, e, _ in settled), Decimal(0))),
        "expected_ending_capital_fen_same_settled_runs": decimal(sum((e.metrics.expected_ending_capital_fen for _, _, _, e, _ in settled), Decimal(0))),
        "realized_ending_capital_fen": sum(s.ending_capital_fen for _, _, s, *_ in settled),
        "yield_on_settled_stake": decimal(Decimal(profit)/stake) if stake else None,
        "meaning": "DESCRIPTIVE_REALIZED_OR_SYNTHETIC_DIAGNOSTIC_NOT_MODEL_VALIDITY"}
    portfolio = {}
    for event in EVENTS:
        predicted = [getattr(e.metrics, event) for _, _, _, e, _ in settled]
        actual = [int(s.realized_buckets[event]) for _, _, s, *_ in settled]
        n = len(predicted)
        portfolio[event] = {"observation_count": n, "mean_predicted": decimal(sum(predicted, Decimal(0))/n) if n else None,
            "observed_count": sum(actual), "observed_frequency": decimal(Decimal(sum(actual))/n) if n else None,
            "event_brier": decimal(sum((p-a)**2 for p, a in zip(predicted, actual, strict=True))/n) if n else None}
    real_runs = sum(lock_clock_is_real(run, epoch) and run.data_classification == "REAL_SOURCE_DATA" for run, *_ in settled)
    synthetic = len(settled)-real_runs
    coverage = dict(sorted(Counter(c.status for c in census).items()))
    return dict(epoch=ArtifactRefV1.of(epoch), as_of_at_utc=as_of, created_at_utc=created_at, clock_basis=basis,
        receipt_watermark=watermark,
        implementation_hash=epoch.policy.implementation_hash, census=tuple(census), census_hash=content_hash("PROSPECTIVE_CENSUS_V1", census),
        performance_evidence_status="DESCRIPTIVE_REAL_SAMPLE_AVAILABLE" if real_runs >= epoch.policy.minimum_real_observations else "INSUFFICIENT_PROSPECTIVE_SAMPLE",
        real_run_count=real_runs, synthetic_run_count=synthetic, probability_quality=tuple(quality), layer_comparisons=tuple(comparisons),
        correction_performance=correction, decision_value=decision, portfolio_calibration=portfolio, coverage=coverage)


def lock_clock_is_real(run, epoch):
    return epoch.mode == "REAL_PROSPECTIVE" and run.clock_basis == "LOCAL_SYSTEM_UTC"
