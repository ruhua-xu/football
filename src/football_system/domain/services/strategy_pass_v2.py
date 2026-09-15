from collections import defaultdict
from decimal import Decimal, localcontext
from functools import lru_cache
from itertools import combinations, product
from math import comb, prod

from football_system.domain.archive import canonical_json
from football_system.domain.betting import SportteryRules
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import (
    content_hash,
    revalidate,
    settle_market,
    fixed_decimal,
)
from football_system.domain.services.payout import (
    calculate_stake_fen,
    official_gross_payout_fen,
)
from football_system.domain.services.probability import (
    selection_ev,
    quantize_probability,
    quantize_metric,
)
from football_system.domain.strategy_pass import PASS_SHAPES, TicketRole
from football_system.domain.strategy_pass_v2 import (
    ExpandedAtomicBetV2,
    HedgeWitnessV2,
    MatchChoiceFailureV2,
    MatchChoiceRequestV1,
    MatchChoiceSetV1,
    OutcomeCandidateV1,
    StrategyPassPlanV2,
    StrategyProfileV2,
    StrategySourceV2,
    StructuralRiskV2,
    SystemTicketCandidateV2,
    SystemTicketV2,
    TicketRequestV2,
)


@fixed_decimal(28)
def outcome_candidates(analysis, fusion):
    values = []
    for unit, result in zip(analysis.units, fusion.results, strict=True):
        if result.unit_id != unit.artifact_id:
            raise ValueError("fusion outcome source unit mismatch")
        if result.p_final is None:
            continue
        for p, quote in zip(
            result.p_final.outcomes, unit.sporttery.prices, strict=True
        ):
            if p.outcome != quote.outcome:
                raise ValueError("outcome/SP catalog mismatch")
            ev = selection_ev(p.probability, quote.price)
            rejection = (
                "SALE_NOT_OPEN"
                if unit.sporttery.sale_status != "OPEN"
                else "EV_BELOW_THRESHOLD"
                if ev < analysis.min_selection_ev
                else None
            )
            values.append(
                OutcomeCandidateV1.freeze(
                    analysis=ArtifactRefV1.of(analysis),
                    fusion=ArtifactRefV1.of(fusion),
                    unit_id=unit.artifact_id,
                    match_id=unit.identity.match_id,
                    market_key=unit.market_key,
                    outcome=p.outcome,
                    probability=p.probability,
                    distribution_hash=result.p_final.distribution_hash,
                    fixed_bonus=quote.price,
                    snapshot=ArtifactRefV1.of(unit.sporttery),
                    minimum_ev=analysis.min_selection_ev,
                    ev=ev,
                    break_even_probability=quantize_probability(1 / quote.price),
                    status="REJECTED" if rejection else "ELIGIBLE",
                    rejection_code=rejection,
                )
            )
    return tuple(values)


def strategy_source_v2(analysis, review, fusion, budget_fen):
    return StrategySourceV2.freeze(
        analysis=analysis,
        review=review,
        fusion=fusion,
        budget_fen=budget_fen,
        selections=outcome_candidates(analysis, fusion),
    )


def expanded_count(pass_type, counts):
    n, sizes = PASS_SHAPES[pass_type]
    if len(counts) != n or any(type(c) is not int or c < 1 for c in counts):
        raise ValueError("invalid expansion arity/count")
    return sum(prod(group) for size in sizes for group in combinations(counts, size))


def preflight(pass_type, choices, profile):
    if pass_type not in profile.pass_types:
        raise ValueError("pass disabled by V2 profile")
    ids = tuple(c.match_id for c in choices)
    if ids != tuple(sorted(set(ids))):
        raise ValueError("same-match cross-market compound forbidden")
    counts = tuple(
        len(c.outcomes) if isinstance(c, MatchChoiceRequestV1) else len(c.candidates)
        for c in choices
    )
    if any(n > profile.absolute_max_outcomes_per_match for n in counts):
        raise ValueError("absolute choice bound exceeded")
    count = expanded_count(pass_type, counts)
    states = prod(
        n + int(n < len(c.market_key.catalog))
        for c, n in zip(choices, counts, strict=True)
    )
    if (
        count > profile.max_expanded_atomic_bets_per_ticket
        or states > profile.max_reconstructable_payout_states
    ):
        raise ValueError(
            "expanded atomic/state hard bound exceeded before materialization"
        )
    return count, states


@fixed_decimal(28)
def atomic_values(legs):
    with localcontext() as ctx:
        ctx.prec = 28
        gross = official_gross_payout_fen(
            (c.fixed_bonus for c in legs), SportteryRules(version="SYSTEM_MULTIPLE_V2")
        )
        probability = quantize_probability(
            prod((c.probability for c in legs), start=Decimal(1))
        )
        return dict(
            legs=legs,
            gross_payout_fen=gross,
            expected_gross_payout_fen=quantize_metric(Decimal(gross) * probability),
        )


def payout_states(choices, atomic, limit):
    options = tuple(
        tuple(c.outcome for c in choice.candidates)
        + ((None,) if len(choice.candidates) < len(choice.market_key.catalog) else ())
        for choice in choices
    )
    if prod(map(len, options)) > limit:
        raise ValueError("reconstructable payout state bound exceeded")
    states = []
    for outcomes in product(*options):
        by_match = dict(zip((c.match_id for c in choices), outcomes, strict=True))
        winning = tuple(
            a for a in atomic if all(by_match[c.match_id] == c.outcome for c in a.legs)
        )
        states.append(
            (
                outcomes,
                tuple(a.artifact_id for a in winning),
                sum(a.gross_payout_fen for a in winning),
            )
        )
    return tuple(states)


@fixed_decimal(28)
def candidate_values(source, profile, pass_type, choices):
    count, states = preflight(pass_type, choices, profile)
    _, sizes = PASS_SHAPES[pass_type]
    atomic = tuple(
        ExpandedAtomicBetV2.freeze(**atomic_values(tuple(legs)))
        for size in sizes
        for subset in combinations(choices, size)
        for legs in product(*(c.candidates for c in subset))
    )
    if len(atomic) != count or len({a.logical_key for a in atomic}) != count:
        raise ValueError("duplicate expanded atomic bet")
    stake = count * 200
    expected = sum((a.expected_gross_payout_fen for a in atomic), Decimal(0))
    maximum = max(
        x[2]
        for x in payout_states(
            choices, atomic, profile.max_reconstructable_payout_states
        )
    )
    return dict(
        source=source,
        profile=profile,
        pass_type=pass_type,
        choice_sets=choices,
        atomic_bets=atomic,
        expanded_atomic_bet_count=count,
        unit_stake_fen=stake,
        max_payout_fen=maximum,
        expected_gross_payout_fen=quantize_metric(expected),
        expected_roi=quantize_metric((expected - stake) / stake),
        reconstructable_state_count=states,
    )


def required_match_choices(candidate):
    required = set.intersection(
        *({c.match_id for c in a.legs} for a in candidate.atomic_bets)
    )
    return {c.match_id: c for c in candidate.choice_sets if c.match_id in required}


def common_choice_failures(candidates):
    if not candidates:
        return ()
    required = [required_match_choices(c) for c in candidates]
    matches = set.intersection(*(set(r) for r in required))
    failures = []
    for match in sorted(matches):
        choices = [r[match] for r in required]
        requests = tuple(
            MatchChoiceRequestV1(
                match_id=match,
                market_key=c.market_key,
                outcomes=tuple(x.outcome for x in c.candidates),
            )
            for c in choices
        )
        score = score_failure(requests)
        if score is not None:
            failures.append(
                MatchChoiceFailureV2(
                    match_id=match,
                    failed_choice_sets=requests,
                    home_goals=score[0],
                    away_goals=score[1],
                )
            )
    return tuple(failures)


@lru_cache(maxsize=256)
def score_failure(choices, winning_leg=None):
    # Finite exact representatives: totals distinguish only 0..6/7+, explicit
    # scores stop at 5, all remaining predicates are integer goal-difference
    # thresholds in [-20,20]. The 0..28 square covers every Boolean cell.
    if any(abs(c.market_key.home_handicap or 0) > 20 for c in choices) or (
        winning_leg and abs(winning_leg.market_key.home_handicap or 0) > 20
    ):
        raise ValueError("structural score representative bound exceeded")
    for total in range(57):
        for home in range(max(0, total - 28), min(28, total) + 1):
            away = total - home
            if all(
                settle_market(c.market_key, home, away) not in c.outcomes
                for c in choices
            ) and (
                winning_leg is None
                or settle_market(winning_leg.market_key, home, away)
                == winning_leg.outcome
            ):
                return home, away
    return None


@fixed_decimal(28)
def structural_risk_v2(allocations, profile):
    matches = defaultdict(int)
    outcomes = defaultdict(int)
    atoms = defaultdict(int)
    total = 0
    indispensable = []
    overlaps = {}
    choice_overlaps = {}
    for candidate, multiplier in allocations:
        total += candidate.unit_stake_fen * multiplier
        required = []
        for atomic in candidate.atomic_bets:
            keys = set()
            atoms[atomic.logical_key] += 200 * multiplier
            for c in atomic.legs:
                matches[c.match_id] += 200 * multiplier
                outcomes[c.logical_key] += 200 * multiplier
                keys.add(c.logical_key)
            required.append(keys)
        indispensable.append(set.intersection(*required))
    single = tuple(sorted(set.intersection(*indispensable))) if indispensable else ()
    failures = common_choice_failures([c for c, _ in allocations])
    for (left, _), (right, _) in combinations(allocations, 2):
        key = "|".join(sorted((left.artifact_id, right.artifact_id)))
        a = {x.logical_key for x in left.atomic_bets}
        b = {x.logical_key for x in right.atomic_bets}
        overlaps[key] = Decimal(len(a & b)) / min(len(a), len(b))
        for left_choice in left.choice_sets:
            for right_choice in right.choice_sets:
                if (left_choice.match_id, left_choice.market_key) != (
                    right_choice.match_id,
                    right_choice.market_key,
                ):
                    continue
                a = {c.outcome for c in left_choice.candidates}
                b = {c.outcome for c in right_choice.candidates}
                choice_overlaps[
                    key
                    + "|"
                    + left_choice.match_id
                    + "|"
                    + left_choice.market_key.canonical
                ] = Decimal(len(a & b)) / len(a | b)
    dependency = (
        {k: Decimal(v) / total for k, v in sorted(outcomes.items())} if total else {}
    )
    concentrated = len(allocations) > 1 and (
        bool(single)
        or bool(failures)
        or any(v > profile.max_core_dependency_ratio for v in dependency.values())
        or any(v > profile.max_ticket_overlap for v in overlaps.values())
        or any(v > profile.max_choice_set_overlap for v in choice_overlaps.values())
    )
    return StructuralRiskV2(
        match_exposure_fen=dict(sorted(matches.items())),
        outcome_exposure_fen=dict(sorted(outcomes.items())),
        atomic_dependency_fen=dict(sorted(atoms.items())),
        ticket_overlap=overlaps,
        choice_set_overlap=choice_overlaps,
        core_dependency=dependency,
        single_outcome_wipeout=single,
        single_match_choice_set_wipeout=tuple(
            content_hash("CHOICE_FAILURE_V2", f) for f in failures
        ),
        match_choice_failure_witnesses=failures,
        concentrated=concentrated,
    )


def hedge_v2(candidate, primary):
    used = {c.logical_key for t in primary for a in t.atomic_bets for c in a.legs}
    for failure in common_choice_failures(primary):
        for atomic in candidate.atomic_bets:
            independent = tuple(
                sorted(c.logical_key for c in atomic.legs if c.logical_key not in used)
            )
            if not independent:
                continue
            match_leg = next(
                (c for c in atomic.legs if c.match_id == failure.match_id), None
            )
            score = score_failure(failure.failed_choice_sets, match_leg)
            if score is not None:
                return HedgeWitnessV2(
                    failure=MatchChoiceFailureV2(
                        match_id=failure.match_id,
                        failed_choice_sets=failure.failed_choice_sets,
                        home_goals=score[0],
                        away_goals=score[1],
                    ),
                    surviving_atomic_bet_id=atomic.artifact_id,
                    independent_outcome_keys=independent,
                )
    return None


def automatic_requests(source, profile):
    groups = defaultdict(list)
    for c in source.selections:
        if c.status == "ELIGIBLE":
            groups[(c.match_id, c.market_key.canonical)].append(c)
    per_match = defaultdict(int)
    for (match, _), candidates in groups.items():
        per_match[match] += sum(
            comb(len(candidates), k)
            for k in range(
                1, min(len(candidates), profile.absolute_max_outcomes_per_match) + 1
            )
        )
    if len(per_match) > profile.max_input_matches:
        raise ValueError("input match hard bound exceeded")
    counts = list(per_match.values())
    expected = sum(
        prod(subset)
        for p in profile.pass_types
        for subset in combinations(counts, PASS_SHAPES[p][0])
    )
    if expected > profile.max_generated_ticket_candidates:
        raise ValueError(
            "generated candidate hard bound exceeded before materialization"
        )
    choices = defaultdict(list)
    for (match, _), candidates in sorted(groups.items()):
        key = candidates[0].market_key
        candidates = sorted(candidates, key=lambda c: key.catalog.index(c.outcome))
        for k in range(
            1, min(len(candidates), profile.absolute_max_outcomes_per_match) + 1
        ):
            for group in combinations(candidates, k):
                choices[match].append(
                    MatchChoiceRequestV1(
                        match_id=match,
                        market_key=key,
                        outcomes=tuple(c.outcome for c in group),
                    )
                )
    return tuple(
        TicketRequestV2(pass_type=p, choices=tuple(cs))
        for p in profile.pass_types
        for matches in combinations(sorted(choices), PASS_SHAPES[p][0])
        for cs in product(*(choices[m] for m in matches))
    )


def generate_v2_candidates(source, profile, requests):
    if (
        len(requests) > profile.max_generated_ticket_candidates
        or len({c.match_id for r in requests for c in r.choices})
        > profile.max_input_matches
    ):
        raise ValueError("request candidate/input hard bound exceeded")
    # All hard-bound checks precede materializing even the first AtomicBet.
    total = 0
    for request in requests:
        count, _ = preflight(request.pass_type, request.choices, profile)
        total += count
        if total > profile.max_total_expanded_atomic_bets:
            raise ValueError(
                "total expanded atomic hard bound exceeded before materialization"
            )
    seen = set()
    catalog = []
    decisions = []
    roles = {}
    values = {
        (c.match_id, c.market_key.canonical, c.outcome): c for c in source.selections
    }
    for request in requests:
        key = content_hash(
            "TICKET_REQUEST_IDENTITY_V2",
            dict(pass_type=request.pass_type, choices=request.choices),
        )
        if key in seen:
            raise ValueError("duplicate equivalent ticket request")
        seen.add(key)
        choices = []
        for choice in request.choices:
            candidates = []
            for outcome in choice.outcomes:
                c = values.get((choice.match_id, choice.market_key.canonical, outcome))
                if c is None or c.status != "ELIGIBLE":
                    decisions.append(
                        key
                        + ":"
                        + choice.match_id
                        + ":"
                        + outcome
                        + ":INELIGIBLE_OUTCOME_EXCLUDED"
                    )
                else:
                    candidates.append(c)
            if not candidates:
                break
            choices.append(
                MatchChoiceSetV1.freeze(
                    match_id=choice.match_id,
                    market_key=choice.market_key,
                    candidates=tuple(candidates),
                )
            )
        if len(choices) != len(request.choices):
            decisions.append(key + ":NO_QUALIFIED_CHOICE_SET")
            continue
        c = SystemTicketCandidateV2.freeze(
            **candidate_values(
                ArtifactRefV1.of(source), profile, request.pass_type, tuple(choices)
            )
        )
        if any(x.logical_key == c.logical_key for x in catalog):
            raise ValueError(
                "requests collapse to a duplicate equivalent qualified ticket"
            )
        catalog.append(c)
        roles[c.artifact_id] = request.role
    return tuple(sorted(catalog, key=lambda c: c.artifact_id)), roles, decisions


@fixed_decimal(28)
def plan_values(source, profile, requests):
    if len(canonical_json(source).encode()) > profile.max_embedded_source_bytes:
        raise ValueError("embedded strategy source byte bound exceeded")
    source = revalidate(source)
    profile = revalidate(profile)
    if tuple(requests) != tuple(sorted(requests, key=canonical_json)):
        raise ValueError("V2 requests must have canonical order")
    material = requests or automatic_requests(source, profile)
    catalog, hints, decisions = generate_v2_candidates(source, profile, material)
    rules = source.analysis.rules
    constraints = source.analysis.constraints
    budget = source.budget_fen
    chosen = []
    multipliers = {}
    roles = {}
    witnesses = {}
    preferred = min(profile.preferred_max_tickets, constraints.preferred_max_tickets)
    absolute = min(profile.absolute_max_tickets, constraints.absolute_max_tickets)

    def feasible(candidate, m, role):
        try:
            stake = calculate_stake_fen(candidate.expanded_atomic_bet_count, m, rules)
        except ValueError:
            return None
        allocation = [
            (
                c,
                m
                if c.artifact_id == candidate.artifact_id
                else multipliers[c.artifact_id],
            )
            for c in chosen
        ]
        if candidate not in chosen:
            allocation.append((candidate, m))
        deployed = sum(c.unit_stake_fen * v for c, v in allocation)
        if deployed > budget:
            return None
        if (
            role in {TicketRole.HEDGE, TicketRole.LONGSHOT}
            and stake > budget * profile.max_auxiliary_budget_ratio
        ):
            return None
        risk = structural_risk_v2(allocation, profile)
        if (
            risk.concentrated
            or any(
                Decimal(v) > budget * constraints.max_match_exposure_ratio
                for v in risk.match_exposure_fen.values()
            )
            or any(
                Decimal(v) > budget * constraints.max_selection_exposure_ratio
                for v in risk.outcome_exposure_fen.values()
            )
        ):
            return None
        peak = max(
            (*risk.match_exposure_fen.values(), *risk.outcome_exposure_fen.values()),
            default=0,
        )
        score = candidate.expected_roi - constraints.concentration_penalty * (
            Decimal(peak) / budget if budget else 0
        )
        return score if score >= constraints.min_marginal_score else None

    ranked = sorted(catalog, key=lambda c: (-c.expected_roi, c.artifact_id))
    while len(chosen) < absolute:
        options = []
        primary = sum(r == TicketRole.PRIMARY for r in roles.values())
        for c in ranked:
            if c in chosen:
                continue
            if c.expected_roi < source.analysis.min_ticket_roi:
                decisions.append(c.artifact_id + ":TICKET_ROI_BELOW_THRESHOLD")
                continue
            role = hints[c.artifact_id] or (
                TicketRole.PRIMARY
                if primary < profile.preferred_primary_max
                else TicketRole.SECONDARY
            )
            if role == TicketRole.PRIMARY and primary >= profile.preferred_primary_max:
                continue
            witness = None
            if role in {TicketRole.HEDGE, TicketRole.LONGSHOT}:
                if any(
                    r in {TicketRole.HEDGE, TicketRole.LONGSHOT} for r in roles.values()
                ):
                    continue
                if role == TicketRole.HEDGE:
                    witness = hedge_v2(
                        c,
                        [
                            x
                            for x in chosen
                            if roles[x.artifact_id]
                            in {TicketRole.PRIMARY, TicketRole.SECONDARY}
                        ],
                    )
                    if witness is None:
                        decisions.append(
                            c.artifact_id + ":NO_SURVIVING_EXPANDED_WITNESS"
                        )
                        continue
                elif (
                    not chosen
                    or Decimal(c.max_payout_fen) / c.unit_stake_fen
                    < profile.longshot_min_max_return_multiple
                ):
                    continue
            if len(chosen) >= preferred:
                used = {s.match_id for x in chosen for s in x.choice_sets}
                if (
                    c.expected_roi
                    - constraints.operational_complexity_penalty
                    * (len(chosen) - preferred + 1)
                    < constraints.extra_ticket_min_roi
                    or not {s.match_id for s in c.choice_sets} - used
                ):
                    continue
            score = feasible(c, 1, role)
            if score is None:
                decisions.append(
                    c.artifact_id + ":BANKROLL_OR_EXPANDED_STRUCTURAL_LIMIT"
                )
                continue
            complexity = sum(
                max(0, len(s.candidates) - profile.preferred_max_outcomes_per_match)
                for s in c.choice_sets
            )
            options.append((c, role, witness, score, complexity))
        if not options:
            break
        c, role, witness, _, _ = min(
            options, key=lambda x: (-x[3], x[4], x[0].artifact_id)
        )
        chosen.append(c)
        multipliers[c.artifact_id] = 1
        roles[c.artifact_id] = role
        witnesses[c.artifact_id] = witness
    while chosen:
        pending = list(chosen)
        changed = False
        while pending:
            options = [
                (c, feasible(c, multipliers[c.artifact_id] + 1, roles[c.artifact_id]))
                for c in pending
            ]
            options = [(c, s) for c, s in options if s is not None]
            if not options:
                break
            c, _ = min(options, key=lambda x: (-x[1], x[0].artifact_id))
            pending.remove(c)
            multipliers[c.artifact_id] += 1
            changed = True
        if not changed:
            break
    tickets = tuple(
        SystemTicketV2.freeze(
            candidate=c,
            ticket_no=i,
            role=roles[c.artifact_id],
            hedge_witness=witnesses[c.artifact_id],
            multiplier=multipliers[c.artifact_id],
            stake_fen=calculate_stake_fen(
                c.expanded_atomic_bet_count, multipliers[c.artifact_id], rules
            ),
            max_payout_fen=c.max_payout_fen * multipliers[c.artifact_id],
        )
        for i, c in enumerate(chosen, 1)
    )
    total = sum(t.stake_fen for t in tickets)
    risk = structural_risk_v2(
        [(c, multipliers[c.artifact_id]) for c in chosen], profile
    )
    return dict(
        source=source,
        profile=profile,
        requests=requests,
        candidates=catalog,
        decisions=tuple(sorted(set(decisions))),
        tickets=tickets,
        risk=risk,
        total_stake_fen=total,
        cash_fen=budget - total,
        status="ALLOCATED" if tickets else "NO_BET",
        reason=None if tickets else "NO_BET_NO_FEASIBLE_TICKET",
    )


def build_strategy_v2(source, profile=None, requests=()):
    profile = profile or StrategyProfileV2()
    requests = tuple(sorted((revalidate(r) for r in requests), key=canonical_json))
    return StrategyPassPlanV2.freeze(**plan_values(source, profile, requests))
