"""Decimal infinite-support Poisson; tails are integrated or rounding-certified."""

from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from football_system.domain.goal_model import (
    PoissonGoalsConfigV1,
    PoissonGoalsStateV1,
    PoissonScoreGridV1,
)
from football_system.domain.market_v2 import (
    MarketTypeV1,
    OutcomeKeyV1,
    distribution,
    reject_float,
    revalidate,
    fixed_decimal,
)


def pmf(lam, cutoff):
    values = [(-lam).exp()]
    for k in range(1, cutoff + 1):
        values.append(values[-1] * lam / Decimal(k))
    return tuple(values)


def positive_tail(value, config):
    if value < 0:
        if -value > config.numerical_error_bound:
            raise ValueError("numerical error exceeds fixed bound")
        return Decimal(0)
    return value


def marginal(lam, config):
    values = [(-lam).exp()]
    total = values[0]
    for k in range(1, config.max_score + 1):
        values.append(values[-1] * lam / Decimal(k))
        total += values[-1]
        tail = positive_tail(1 - total, config)
        if k >= 6 and tail <= config.tail_epsilon:
            return tuple(values), tail
    raise ValueError("Poisson adaptive score bound exceeded")


@fixed_decimal(80)
def grid_content(home, away, config):
    reject_float((home, away))
    revalidate(config)
    home, away = Decimal(home), Decimal(away)
    if not all(x.is_finite() and 0 <= x <= config.max_lambda for x in (home, away)):
        raise ValueError("intensity outside fixed Poisson bounds")
    with localcontext() as ctx:
        ctx.prec = config.decimal_precision
        h, ht = marginal(home, config)
        a, at = marginal(away, config)
        return dict(
            config=config,
            lambda_home=home,
            lambda_away=away,
            home_pmf=h,
            away_pmf=a,
            home_tail=ht,
            away_tail=at,
            rectangular_tail=positive_tail(1 - (1 - ht) * (1 - at), config),
        )


def score_grid(home, away, config=None):
    return PoissonScoreGridV1.freeze(
        **grid_content(home, away, config or PoissonGoalsConfigV1())
    )


@fixed_decimal(80)
def training_content(cohort, request, config):
    revalidate(cohort)
    revalidate(request)
    revalidate(config)
    if (cohort.competition_id, cohort.season_id) != (
        request.competition_id,
        request.season_id,
    ):
        raise ValueError("goal cohort competition/season mismatch")
    if cohort.admitted_at_utc >= request.training_cutoff_at_utc or any(
        f.result.ingested_at_utc >= request.training_cutoff_at_utc
        or f.result.match_id == request.match_id
        for f in cohort.facts
    ):
        raise ValueError(
            "training admission/fact crosses strict training cutoff or includes target"
        )
    home = [f for f in cohort.facts if f.home_team_id == request.home_team_id]
    away = [f for f in cohort.facts if f.away_team_id == request.away_team_id]
    out = dict(
        config=config,
        cohort=cohort,
        request=request,
        training_data_hash=cohort.training_data_hash,
        status="MODEL_UNAVAILABLE",
        reason=None,
        home_count=len(home),
        away_count=len(away),
        league_count=len(cohort.facts),
        league_home_average=None,
        league_away_average=None,
        grid=None,
    )
    if len(cohort.facts) < config.minimum_league_matches:
        out["reason"] = "INSUFFICIENT_LEAGUE_HISTORY"
    elif len(home) < config.minimum_home_matches:
        out["reason"] = "INSUFFICIENT_HOME_HISTORY"
    elif len(away) < config.minimum_away_matches:
        out["reason"] = "INSUFFICIENT_AWAY_HISTORY"
    if out["reason"]:
        return out
    with localcontext() as ctx:
        ctx.prec = config.decimal_precision
        lh = Decimal(sum(f.result.home_goals for f in cohort.facts)) / len(cohort.facts)
        la = Decimal(sum(f.result.away_goals for f in cohort.facts)) / len(cohort.facts)
        out.update(league_home_average=lh, league_away_average=la)
        if lh == 0 or la == 0:
            out["reason"] = "ZERO_LEAGUE_GOAL_AVERAGE"
            return out
        home_attack = (Decimal(sum(f.result.home_goals for f in home)) / len(home)) / lh
        home_defense = (
            Decimal(sum(f.result.away_goals for f in home)) / len(home)
        ) / la
        away_attack = (Decimal(sum(f.result.away_goals for f in away)) / len(away)) / la
        away_defense = (
            Decimal(sum(f.result.home_goals for f in away)) / len(away)
        ) / lh
        h = (lh * home_attack * away_defense).quantize(
            config.lambda_quantum, rounding=ROUND_HALF_EVEN
        )
        a = (la * away_attack * home_defense).quantize(
            config.lambda_quantum, rounding=ROUND_HALF_EVEN
        )
        if h > config.max_lambda or a > config.max_lambda:
            out["reason"] = "INTENSITY_OUTSIDE_FIXED_BOUND"
            return out
        out.update(status="AVAILABLE", grid=score_grid(h, a, config))
    return out


def train_poisson(cohort, request, config=None):
    return PoissonGoalsStateV1.freeze(
        **training_content(cohort, request, config or PoissonGoalsConfigV1())
    )


def sign_intervals(home, away, handicap, n, config):
    # Integrate all away scores for every finite home row using the full CDF.
    h = pmf(home, n)
    a = pmf(away, n + abs(handicap) + 1)
    prefix = []
    running = Decimal(0)
    for p in a:
        running += p
        prefix.append(running)

    def cdf(k):
        return Decimal(0) if k < 0 else prefix[k]

    win = draw = loss = Decimal(0)
    for score, p in enumerate(h):
        boundary = score + handicap
        win += p * cdf(boundary - 1)
        draw += p * (a[boundary] if boundary >= 0 else Decimal(0))
        loss += p * positive_tail(1 - cdf(boundary), config)
    ht = positive_tail(1 - sum(h), config)
    # For H>n, the only uncertainty in assigning mass to HOME is A>=H+handicap.
    # This entire infinite double tail is bounded, not discarded/renormalized.
    uncertainty = ht * positive_tail(1 - cdf(n + handicap), config)
    return (
        (win + ht - uncertainty, win + ht),
        (draw, draw + uncertainty),
        (loss, loss + uncertainty),
    ), uncertainty


@fixed_decimal(80)
def project_score_grid(grid, market):
    revalidate(grid)
    revalidate(market)
    cfg = grid.config
    home = grid.lambda_home
    away = grid.lambda_away
    if market.market_type == MarketTypeV1.THREE_WAY:
        raise ValueError("THREE_WAY remains the independent frozen Elo path")
    if abs(market.home_handicap or 0) > cfg.max_absolute_handicap:
        raise ValueError("handicap exceeds fixed Poisson mapping bound")
    with localcontext() as ctx:
        ctx.prec = cfg.decimal_precision
        if market.market_type == MarketTypeV1.TOTAL_GOALS:
            masses = pmf(home + away, 6)
            return distribution(market, (*masses, 1 - sum(masses)))
        explicit = {}
        h = pmf(home, 5)
        a = pmf(away, 5)
        if market.market_type == MarketTypeV1.CORRECT_SCORE:
            for outcome in market.catalog:
                if outcome.value.startswith("SCORE_"):
                    x, y = map(int, outcome.value[6:].split("_"))
                    explicit[outcome] = h[x] * a[y]
        start = max(len(grid.home_pmf) - 1, abs(market.home_handicap or 0), 6)
        for n in range(start, cfg.max_score - cfg.max_absolute_handicap):
            signs, _ = sign_intervals(home, away, market.home_handicap or 0, n, cfg)
            if market.market_type == MarketTypeV1.HANDICAP_THREE_WAY:
                intervals = signs
            else:
                sums = [Decimal(0), Decimal(0), Decimal(0)]
                for outcome, mass in explicit.items():
                    x, y = map(int, outcome.value[6:].split("_"))
                    sums[0 if x > y else 2 if x < y else 1] += mass
                other = {
                    OutcomeKeyV1.HOME_OTHER: 0,
                    OutcomeKeyV1.DRAW_OTHER: 1,
                    OutcomeKeyV1.AWAY_OTHER: 2,
                }
                intervals = tuple(
                    (explicit[o], explicit[o])
                    if o in explicit
                    else (
                        signs[other[o]][0] - sums[other[o]],
                        signs[other[o]][1] - sums[other[o]],
                    )
                    for o in market.catalog
                )
            rounded = []
            for lo, hi in intervals:
                lo = max(Decimal(0), lo - cfg.numerical_error_bound)
                hi = min(Decimal(1), hi + cfg.numerical_error_bound)
                left = lo.quantize(cfg.probability_quantum, rounding=ROUND_HALF_EVEN)
                right = hi.quantize(cfg.probability_quantum, rounding=ROUND_HALF_EVEN)
                if left != right:
                    break
                rounded.append(left)
            if len(rounded) == len(market.catalog):
                # Every entry is the certified rounded infinite-support probability.
                # Only the ordinary quantization residual is closed here.
                return distribution(market, rounded)
    raise ValueError("Poisson tail cannot certify outcome rounding within hard bound")
