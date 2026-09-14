"""Deterministic pass generation with the existing marginal allocation economics."""

from __future__ import annotations

from decimal import Decimal
from itertools import combinations

from football_system.domain.archive import canonical_json
from football_system.domain.betting import CandidateStatus, NoBetReason
from football_system.domain.common import stable_id
from football_system.domain.services.optimizer import (
    _add_exposure,
    _allocation_is_feasible,
    _marginal_score,
)
from football_system.domain.services.payout import calculate_stake_fen
from football_system.domain.strategy_pass import (
    PASS_SHAPES,
    HedgeWitnessV1,
    OddsRefV1,
    StrategyDecisionV1,
    StrategyPassPlanV1,
    StrategyProfileV1,
    StrategySourceV1,
    StructuralRiskV1,
    SystemTicketCandidateV1,
    SystemTicketV1,
    TicketRole,
    TicketRoleRequestV1,
    fresh,
    ticket_spec,
)


def generate_candidates(source: StrategySourceV1, profile: StrategyProfileV1):
    eligible = tuple(
        s for s in source.selections if s.status == CandidateStatus.ELIGIBLE
    )
    if len({s.match_id for s in eligible}) > profile.max_input_matches:
        raise ValueError("strategy match bound exceeded; never silently truncate")
    specs = []
    for pass_type in profile.pass_types:
        n, _ = PASS_SHAPES[pass_type]
        for selections in combinations(eligible, n):
            if len({s.match_id for s in selections}) != n:
                continue
            specs.append((pass_type, selections))
            if len(specs) > profile.max_generated_candidates:
                raise ValueError(
                    "strategy candidate bound exceeded; no partial catalog"
                )
    snapshots = {x.snapshot_id: x for x in source.odds_snapshots}
    result = []
    h = source.source_hash
    for pass_type, selections in specs:
        refs = tuple(
            OddsRefV1(
                selection_id=s.candidate_id,
                snapshot_id=s.sporttery_bonus_snapshot_id,
                payload_hash=snapshots[s.sporttery_bonus_snapshot_id].payload_hash,
                fixed_bonus=s.fixed_bonus,
            )
            for s in selections
        )
        result.append(
            SystemTicketCandidateV1(
                **ticket_spec(h, selections, refs, pass_type, source.rules)
            )
        )
    return tuple(sorted(result, key=lambda c: c.candidate_id))


def structural_risk(allocations, profile):
    matches, selections, dependency, overlaps = {}, {}, {}, {}
    total = 0
    wipeout = None
    for candidate, multiplier in allocations:
        stake = calculate_stake_fen(
            candidate.atomic_bet_count, multiplier, candidate.rules
        )
        total += stake
        for key in {s.match_id for s in candidate.selections}:
            matches[key] = matches.get(key, 0) + stake
        for s in candidate.selections:
            selections[s.candidate_id] = selections.get(s.candidate_id, 0) + stake
        required = None
        for atomic in candidate.atomic_bets:
            keys = set(atomic.selection_ids)
            required = keys if required is None else required & keys
            for key in keys:
                dependency[key] = (
                    dependency.get(key, 0) + atomic.base_stake_fen * multiplier
                )
        wipeout = required if wipeout is None else wipeout & required
    for (left, _), (right, _) in combinations(allocations, 2):
        a = {s.candidate_id for s in left.selections}
        b = {s.candidate_id for s in right.selections}
        overlaps["|".join(sorted((left.candidate_id, right.candidate_id)))] = Decimal(
            len(a & b)
        ) / Decimal(min(len(a), len(b)))
    ratios = (
        {k: Decimal(v) / Decimal(total) for k, v in sorted(dependency.items())}
        if total
        else {}
    )
    concentrated = len(allocations) > 1 and (
        bool(wipeout)
        or any(x > profile.max_core_dependency_ratio for x in ratios.values())
        or any(x > profile.max_ticket_overlap for x in overlaps.values())
    )
    return StructuralRiskV1(
        match_exposure_fen=dict(sorted(matches.items())),
        selection_exposure_fen=dict(sorted(selections.items())),
        atomic_selection_dependency_fen=dict(sorted(dependency.items())),
        core_selection_dependency_ratio=ratios,
        ticket_overlap=dict(sorted(overlaps.items())),
        single_selection_wipeout_ids=tuple(sorted(wipeout or ())),
        concentrated=concentrated,
    )


def hedge_witness(candidate, primary):
    if not primary:
        return None
    used = {s.candidate_id for c in primary for s in c.selections}
    cores = None
    for c in primary:
        required = set.intersection(*(set(a.selection_ids) for a in c.atomic_bets))
        cores = required if cores is None else cores & required
    independent = tuple(sorted({s.candidate_id for s in candidate.selections} - used))
    if not independent:
        return None
    for core in sorted(cores or ()):
        for atomic in candidate.atomic_bets:
            # Atomic selections are internally consistent by construction. If the
            # core's match appears, its different eligible outcome witnesses loss
            # of the core; otherwise that match is free to take a failing outcome.
            if core not in atomic.selection_ids and set(atomic.selection_ids) & set(
                independent
            ):
                return HedgeWitnessV1(
                    failed_core_selection_id=core,
                    surviving_atomic_bet_id=atomic.atomic_bet_id,
                    independently_eligible_selection_ids=independent,
                )
    return None


def build_strategy_plan(
    source: StrategySourceV1,
    profile: StrategyProfileV1,
    role_requests: tuple[TicketRoleRequestV1, ...] = (),
) -> StrategyPassPlanV1:
    return StrategyPassPlanV1.freeze(
        **_derive_plan_content(source, profile, role_requests)
    )


def _derive_plan_content(source, profile, role_requests=()):
    source, profile = fresh(source), fresh(profile)
    role_requests = tuple(
        sorted(
            (fresh(r) for r in role_requests),
            key=lambda r: (r.pass_type.value, r.selection_ids, r.role.value),
        )
    )
    request_keys = [(r.pass_type, r.selection_ids) for r in role_requests]
    if len(set(request_keys)) != len(request_keys):
        raise ValueError("equivalent ticket cannot request multiple roles")
    ids = {s.candidate_id for s in source.selections}
    if any(not set(r.selection_ids) <= ids for r in role_requests):
        raise ValueError("role request references an unknown source selection")
    candidates = (
        generate_candidates(source, profile)
        if source.budget_fen >= source.rules.base_stake_fen
        else ()
    )
    requested = {(r.pass_type, r.selection_ids): r.role for r in role_requests}
    constraints = source.constraints.model_copy(
        update={
            "preferred_max_tickets": min(
                profile.preferred_max_tickets, source.constraints.preferred_max_tickets
            ),
            "absolute_max_tickets": min(
                profile.absolute_max_tickets, source.constraints.absolute_max_tickets
            ),
        }
    )
    selected = []
    multiples = {}
    roles = {}
    witnesses = {}
    decisions = set()
    match_exposure = {}
    selection_exposure = {}
    total = 0
    primary_count = 0
    ranked = sorted(candidates, key=lambda c: (-c.expected_roi, c.candidate_id))

    def allocation_ok(c, new_multiplier, role):
        nonlocal total
        try:
            full = calculate_stake_fen(c.atomic_bet_count, new_multiplier, source.rules)
            old = (
                calculate_stake_fen(
                    c.atomic_bet_count, multiples[c.candidate_id], source.rules
                )
                if c.candidate_id in multiples
                else 0
            )
        except ValueError:
            return False, "STAKE_OR_MULTIPLIER_LIMIT", 0
        marginal = full - old
        if not _allocation_is_feasible(
            c,
            marginal,
            total,
            source.budget_fen,
            match_exposure,
            selection_exposure,
            constraints,
        ):
            return False, "BUDGET_OR_EXPOSURE_LIMIT", marginal
        if (
            role in {TicketRole.HEDGE, TicketRole.LONGSHOT}
            and Decimal(full) > source.budget_fen * profile.max_auxiliary_budget_ratio
        ):
            return False, "AUXILIARY_SMALL_STAKE_LIMIT", marginal
        projected = [
            (
                x,
                new_multiplier
                if x.candidate_id == c.candidate_id
                else multiples[x.candidate_id],
            )
            for x in selected
        ]
        if c not in selected:
            projected.append((c, new_multiplier))
        if structural_risk(projected, profile).concentrated:
            return False, "STRUCTURAL_CONCENTRATION_LIMIT", marginal
        return True, "ACCEPTED", marginal

    while len(selected) < constraints.absolute_max_tickets:
        choices = []
        for c in ranked:
            if c.candidate_id in multiples:
                continue
            if c.expected_roi < source.min_ticket_roi:
                decisions.add((c.candidate_id, "TICKET_ROI_BELOW_THRESHOLD"))
                continue
            role = requested.get(
                (c.pass_type, tuple(s.candidate_id for s in c.selections)),
                TicketRole.PRIMARY
                if primary_count < profile.preferred_primary_max
                else TicketRole.SECONDARY,
            )
            witness = None
            if (
                role == TicketRole.PRIMARY
                and primary_count >= profile.preferred_primary_max
            ):
                decisions.add((c.candidate_id, "PRIMARY_TICKET_PREFERENCE_LIMIT"))
                continue
            if role in {TicketRole.HEDGE, TicketRole.LONGSHOT}:
                if (
                    sum(
                        r in {TicketRole.HEDGE, TicketRole.LONGSHOT}
                        for r in roles.values()
                    )
                    >= profile.max_auxiliary_tickets
                ):
                    decisions.add((c.candidate_id, "AUXILIARY_TICKET_LIMIT"))
                    continue
                if role == TicketRole.HEDGE:
                    witness = hedge_witness(
                        c,
                        [
                            x
                            for x in selected
                            if roles[x.candidate_id]
                            in {TicketRole.PRIMARY, TicketRole.SECONDARY}
                        ],
                    )
                    if witness is None:
                        decisions.add(
                            (c.candidate_id, "NO_INDEPENDENT_CORE_LOSS_WITNESS")
                        )
                        continue
                elif (
                    not selected
                    or Decimal(c.max_payout_fen) / c.base_stake_fen
                    < profile.longshot_min_max_return_multiple
                ):
                    decisions.add((c.candidate_id, "LONGSHOT_QUALIFICATION_NOT_MET"))
                    continue
            extra = len(selected) - constraints.preferred_max_tickets + 1
            if extra > 0 and (
                c.expected_roi - constraints.operational_complexity_penalty * extra
                < constraints.extra_ticket_min_roi
                or not {s.match_id for s in c.selections} - match_exposure.keys()
            ):
                decisions.add(
                    (c.candidate_id, "EXTRA_TICKET_QUALITY_OR_DIVERSITY_LIMIT")
                )
                continue
            ok, reason, marginal = allocation_ok(c, 1, role)
            if not ok:
                decisions.add((c.candidate_id, reason))
                continue
            score = _marginal_score(
                c,
                marginal,
                source.budget_fen,
                match_exposure,
                selection_exposure,
                constraints,
            )
            if score < constraints.min_marginal_score:
                decisions.add((c.candidate_id, "MARGINAL_SCORE_BELOW_LIMIT"))
                continue
            role_order = (
                0
                if role == TicketRole.PRIMARY
                and primary_count < profile.preferred_primary_min
                else 1
            )
            choices.append((c, role, witness, marginal, score, role_order))
        if not choices:
            break
        c, role, witness, marginal, _, _ = min(
            choices, key=lambda x: (-x[4], x[5], x[0].candidate_id)
        )
        selected.append(c)
        multiples[c.candidate_id] = 1
        roles[c.candidate_id] = role
        witnesses[c.candidate_id] = witness
        total += marginal
        primary_count += int(role == TicketRole.PRIMARY)
        _add_exposure(c, marginal, match_exposure, selection_exposure)
        decisions.add((c.candidate_id, "OPENED_" + role.value))
    # Same round-wise marginal increments and ROI/concentration score as 0.6.
    # Added structural/profile predicates can only constrain feasible allocations.
    while selected:
        pending = list(selected)
        allocated = False
        while pending:
            choices = []
            for c in pending:
                ok, _, marginal = allocation_ok(
                    c, multiples[c.candidate_id] + 1, roles[c.candidate_id]
                )
                if not ok:
                    continue
                score = _marginal_score(
                    c,
                    marginal,
                    source.budget_fen,
                    match_exposure,
                    selection_exposure,
                    constraints,
                )
                if score >= constraints.min_marginal_score:
                    choices.append((c, marginal, score))
            if not choices:
                break
            c, marginal, _ = min(choices, key=lambda x: (-x[2], x[0].candidate_id))
            pending.remove(c)
            multiples[c.candidate_id] += 1
            total += marginal
            allocated = True
            _add_exposure(c, marginal, match_exposure, selection_exposure)
        if not allocated:
            break
    tickets = []
    for no, c in enumerate(selected, 1):
        m = multiples[c.candidate_id]
        role = roles[c.candidate_id]
        tickets.append(
            SystemTicketV1(
                ticket_id=stable_id(
                    "SYSTEM_TICKET_V1",
                    profile.profile_hash,
                    c.candidate_id,
                    role.value,
                    m,
                ),
                ticket_no=no,
                profile_hash=profile.profile_hash,
                parent=source.parent,
                candidate=c,
                role=role,
                hedge_witness=witnesses[c.candidate_id],
                multiplier=m,
                stake_fen=calculate_stake_fen(c.atomic_bet_count, m, source.rules),
                max_payout_fen=c.max_payout_fen * m,
                gross_payout_states=tuple(
                    s.model_copy(update={"gross_payout_fen": s.gross_payout_fen * m})
                    for s in c.payout_states
                ),
                expected_gross_payout_fen=c.expected_gross_payout_fen * m,
                expected_profit_fen=c.expected_profit_fen * m,
            )
        )
    reason = None
    if not tickets:
        if source.budget_fen < source.rules.base_stake_fen or (
            candidates
            and not any(
                c.base_stake_fen
                <= min(source.budget_fen, source.rules.max_ticket_stake_fen)
                for c in candidates
            )
        ):
            reason = NoBetReason.NO_BET_NO_FEASIBLE_TICKET.value
        elif not any(c.expected_roi >= source.min_ticket_roi for c in candidates):
            reason = NoBetReason.NO_BET_NO_VALUE.value
        else:
            reason = NoBetReason.NO_BET_RISK_LIMIT.value
    return dict(
        profile=profile,
        source=source,
        role_requests=role_requests,
        candidates=candidates,
        tickets=tuple(tickets),
        decisions=tuple(
            StrategyDecisionV1(candidate_id=k, code=v) for k, v in sorted(decisions)
        ),
        risk=structural_risk(
            [(c, multiples[c.candidate_id]) for c in selected], profile
        ),
        total_stake_fen=total,
        cash_fen=source.budget_fen - total,
        status="ALLOCATED" if tickets else "NO_BET",
        no_bet_reason=reason,
    )


def validate_plan(plan):
    source, profile = plan.source, plan.profile
    expected = _derive_plan_content(source, profile, plan.role_requests)
    actual = plan.model_dump(
        mode="json", exclude={"schema_version", "plan_id", "plan_hash"}
    )
    if canonical_json(expected) != canonical_json(actual):
        raise ValueError("strategy differs from deterministic eligible-source replay")
    fresh(source)
    fresh(profile)
    catalog = {c.candidate_id: c for c in plan.candidates}
    if len(catalog) != len(plan.candidates) or len(
        {c.equivalence_hash for c in plan.candidates}
    ) != len(catalog):
        raise ValueError("duplicate equivalent ticket catalog")
    eligible = {
        s.candidate_id: s
        for s in source.selections
        if s.status == CandidateStatus.ELIGIBLE
    }
    for c in plan.candidates:
        if (
            c.source_hash != source.source_hash
            or c.pass_type not in profile.pass_types
            or c.rules != source.rules
            or any(eligible.get(s.candidate_id) != s for s in c.selections)
        ):
            raise ValueError("ticket catalog differs from eligible source/profile")
        for ref in c.odds_refs:
            snapshot = next(
                (s for s in source.odds_snapshots if s.snapshot_id == ref.snapshot_id),
                None,
            )
            if snapshot is None or snapshot.payload_hash != ref.payload_hash:
                raise ValueError("ticket odds hash reference differs from source")
    if tuple(t.ticket_no for t in plan.tickets) != tuple(
        range(1, len(plan.tickets) + 1)
    ):
        raise ValueError("ticket numbering must be contiguous")
    if len({t.candidate.equivalence_hash for t in plan.tickets}) != len(plan.tickets):
        raise ValueError("duplicate equivalent allocated ticket")
    if len(plan.tickets) > min(
        profile.absolute_max_tickets, source.constraints.absolute_max_tickets
    ):
        raise ValueError("absolute ticket count exceeded")
    aux = [t for t in plan.tickets if t.role in {TicketRole.HEDGE, TicketRole.LONGSHOT}]
    if len(aux) > 1 or any(
        Decimal(t.stake_fen) > source.budget_fen * profile.max_auxiliary_budget_ratio
        for t in aux
    ):
        raise ValueError("auxiliary ticket/stake constraint exceeded")
    primary = []
    for t in plan.tickets:
        if (
            t.parent != source.parent
            or t.profile_hash != profile.profile_hash
            or catalog.get(t.candidate.candidate_id) != t.candidate
            or t.candidate.expected_roi < source.min_ticket_roi
        ):
            raise ValueError("ticket lineage/quality mismatch")
        if t.role == TicketRole.HEDGE and t.hedge_witness != hedge_witness(
            t.candidate, primary
        ):
            raise ValueError("hedge has no exact structural witness")
        if t.role in {TicketRole.PRIMARY, TicketRole.SECONDARY}:
            primary.append(t.candidate)
    if (
        plan.total_stake_fen != sum(t.stake_fen for t in plan.tickets)
        or plan.cash_fen != source.budget_fen - plan.total_stake_fen
        or plan.cash_fen < 0
    ):
        raise ValueError("strategy budget/stake/cash mismatch")
    risk = structural_risk([(t.candidate, t.multiplier) for t in plan.tickets], profile)
    if risk != plan.risk or risk.concentrated:
        raise ValueError("structural risk mismatch or concentrated allocation")
    if any(
        Decimal(v) > source.budget_fen * source.constraints.max_match_exposure_ratio
        for v in risk.match_exposure_fen.values()
    ) or any(
        Decimal(v) > source.budget_fen * source.constraints.max_selection_exposure_ratio
        for v in risk.selection_exposure_fen.values()
    ):
        raise ValueError("existing portfolio exposure limit exceeded")
    if (plan.status == "NO_BET") != (not plan.tickets) or (
        plan.no_bet_reason is None
    ) != (bool(plan.tickets)):
        raise ValueError("strategy status/cash decision inconsistent")
