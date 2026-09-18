"""Pure provenance/lifecycle checks around frozen v0.9 artifacts."""

from datetime import timedelta

from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import ArtifactRefV1, FootballEvidenceV1
from football_system.domain.market_v2 import content_hash, fixed_decimal, settle_market
from football_system.domain.prospective import (
    EpochConfigurationV1, LockedLayerV1, LockedProbabilityUnitV1,
    MarketPolicyPinV1, ModelConfigurationPinV1,
)
from football_system.domain.prospective_evidence import (
    AbsenceFactV1, AssertionClass, EvidenceUseV1, FactCategory, FormFactV1,
    FreshnessStatus, LineupFactV1, LineupStatus, ScheduleFactV1, freshness,
)
from football_system.domain.services.return_distribution import payout_for_assignment
from football_system.domain.services.review_v4 import export_packet_v4


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical_unique(values):
    by_json = {canonical_json(value): value for value in values}
    return tuple(by_json[key] for key in sorted(by_json))


def configuration_for(plan, return_policy, objective):
    analysis = plan.source.analysis
    data = dict(
        models=canonical_unique(ModelConfigurationPinV1(model_name=u.model_lineage.model_name,
            model_version=u.model_lineage.model_version, config_hash=u.model_lineage.config_hash,
            training_data_hash=u.model_lineage.training_data_hash) for u in analysis.units),
        market_policies=canonical_unique(MarketPolicyPinV1(market_type=u.market_key.market_type,
            base_policy=u.base_policy, quant_weight=u.quant_weight) for u in analysis.units),
        strategy_profile=plan.profile, fusion_policy=plan.source.fusion.policy,
        rules=analysis.rules, risk=analysis.constraints, min_selection_ev=analysis.min_selection_ev,
        min_ticket_roi=analysis.min_ticket_roi, allowed_budgets_fen=analysis.budgets_fen,
        return_policy=ArtifactRefV1.of(return_policy), objective_profile=ArtifactRefV1.of(objective),
    )
    return EpochConfigurationV1(**data, configuration_hash=content_hash("EPOCH_CONFIGURATION_V1", data))


def assert_analysis_configuration(epoch, analysis, budget):
    c = epoch.configuration
    require(analysis.rules == c.rules and analysis.constraints == c.risk
            and analysis.min_selection_ev == c.min_selection_ev and analysis.min_ticket_roi == c.min_ticket_roi
            and budget in c.allowed_budgets_fen and budget in analysis.budgets_fen, "EPOCH_POLICY_OR_BUDGET_CHANGED")
    for unit in analysis.units:
        model = ModelConfigurationPinV1(model_name=unit.model_lineage.model_name, model_version=unit.model_lineage.model_version,
            config_hash=unit.model_lineage.config_hash, training_data_hash=unit.model_lineage.training_data_hash)
        policy = MarketPolicyPinV1(market_type=unit.market_key.market_type, base_policy=unit.base_policy, quant_weight=unit.quant_weight)
        require(model in c.models and policy in c.market_policies, "EPOCH_MODEL_OR_BASE_POLICY_CHANGED")


def football_projection(snapshot):
    claim = snapshot.claim
    payload = canonical_json(claim.structured_payload)
    # A bounded, explicit projection, not silent truncation. Full structured data
    # stays in EvidenceSnapshot and prepare markdown; the V4 wire remains frozen.
    detail = payload if len(payload) <= 1800 else "FULL_STRUCTURED_PAYLOAD_IN_EVIDENCE_SNAPSHOT:" + snapshot.artifact_id
    facts = (f"{claim.assertion_class.value} / {claim.fact_category.value} / source-time freshness={snapshot.freshness_status.value}", detail)
    return FootballEvidenceV1.freeze(match_id=claim.match_id, facts=facts,
        source_reference="PROSPECTIVE_EVIDENCE:"+snapshot.artifact_id, source_hash=snapshot.content_hash,
        available_at_utc=snapshot.ingested_at_utc, ingested_at_utc=snapshot.ingested_at_utc)


def evidence_use(snapshot, binding, cutoff, policy):
    claim = snapshot.claim
    require(max(claim.available_at_utc, claim.captured_at_utc, snapshot.ingested_at_utc) <= cutoff, "LOOKAHEAD_RISK_EVIDENCE_AFTER_CUTOFF")
    require(claim.retention_until_utc > cutoff, "EVIDENCE_EXPIRED_DELETE_ONLY")
    payload = claim.structured_payload
    if isinstance(payload, (ScheduleFactV1, FormFactV1, AbsenceFactV1)):
        require(payload.as_of_at_utc <= cutoff, "LOOKAHEAD_RISK_FACT_AS_OF")
    state = freshness(claim.published_at_utc, cutoff, policy.category_max_age_seconds[claim.fact_category])
    require(state != FreshnessStatus.STALE, "STALE_DECISION_EVIDENCE")
    return EvidenceUseV1(snapshot=ArtifactRefV1.of(snapshot), binding=ArtifactRefV1.of(binding),
        football_evidence=binding.football_evidence, match_id=claim.match_id, category=claim.fact_category,
        assertion_class=claim.assertion_class, freshness=state, source_published_at_utc=claim.published_at_utc,
        source_available_at_utc=claim.available_at_utc, trusted_ingested_at_utc=snapshot.ingested_at_utc)


def input_refs(analysis, packet):
    refs = [ArtifactRefV1.of(analysis)]
    if packet is not None:
        refs.append(ArtifactRefV1.of(packet))
    for unit in analysis.units:
        refs.extend(ArtifactRefV1.of(x) for x in (unit, unit.sporttery, unit.consensus, unit.goal_state, unit.legacy_source, *unit.evidence) if x is not None)
    return tuple(sorted({r.artifact_id: r for r in refs}.values(), key=lambda r: r.artifact_id))


def prepare_values(epoch, analysis, uses, at, basis, slate_key, slate_date, budget, supersedes=None):
    assert_analysis_configuration(epoch, analysis, budget)
    identities = tuple(sorted({u.identity.match_id: u.identity for u in analysis.units}.values(), key=lambda i: i.match_id))
    require(slate_date == min(i.kickoff_at_utc for i in identities).date(), "SLATE_DATE_MUST_BIND_EARLIEST_KICKOFF_UTC")
    require(epoch.starts_at_utc <= at < epoch.ends_at_utc, "EPOCH_NOT_ACTIVE")
    require(len(uses) <= epoch.policy.max_evidence_per_run, "EVIDENCE_SCOPE_TOO_LARGE")
    require(all(u.decision_cutoff <= at and u.model_lineage.generated_at_utc <= at
                and u.sporttery.ingested_at_utc <= at for u in analysis.units), "LOOKAHEAD_RISK_ANALYSIS_INPUT")
    expected = {e.artifact_id for unit in analysis.units for e in unit.evidence}
    require(expected == {u.football_evidence.artifact_id for u in uses}, "PROSPECTIVE_EVIDENCE_COVERAGE_MISMATCH")
    reason = "PRODUCTION_DECISION_ADAPTER_UNAVAILABLE" if epoch.mode == "REAL_PROSPECTIVE" else None
    if at+timedelta(seconds=epoch.policy.minimum_lock_lead_seconds) >= min(i.kickoff_at_utc for i in identities):
        reason = "LOOKAHEAD_RISK"
    packet = export_packet_v4(analysis) if reason is None else None
    refs = input_refs(analysis, packet)
    uses = tuple(sorted(uses, key=lambda u: u.snapshot.artifact_id))
    dependency = dict(epoch=ArtifactRefV1.of(epoch), inputs=refs, evidence=uses, budget_fen=budget,
                      decision_cutoff=at, source_classification=analysis.classification)
    return dict(epoch=ArtifactRefV1.of(epoch), slate_key=slate_key, slate_date=slate_date,
        decision_cutoff=at, prepared_at_utc=at, source_observed_at_utc=at, clock_basis=basis,
        data_classification="SYNTHETIC", analysis=ArtifactRefV1.of(analysis), packet=ArtifactRefV1.of(packet) if packet else None,
        identities=identities, evidence=uses, budget_fen=budget, implementation_hash=epoch.policy.implementation_hash,
        configuration_hash=epoch.configuration.configuration_hash, input_artifact_hashes=refs,
        complete_input_hash=content_hash("PROSPECTIVE_INPUTS_V1", dependency),
        status="PREPARING" if reason is None else "UNAVAILABLE", reason=reason, supersedes=ArtifactRefV1.of(supersedes) if supersedes else None)


def assert_before_kickoff(epoch, run, at):
    require(run.status == "PREPARING", run.reason or "RUN_NOT_PREPARING")
    require(epoch.starts_at_utc <= at < epoch.ends_at_utc and at >= run.prepared_at_utc, "EPOCH_OR_EVENT_TIME_INVALID")
    require(at+timedelta(seconds=epoch.policy.minimum_lock_lead_seconds) < min(i.kickoff_at_utc for i in run.identities), "LOOKAHEAD_RISK")


def correction_values(run, review, reasons, snapshots, at, basis):
    require(run.packet == ArtifactRefV1.of(review.packet), "REVIEW_NOT_BOUND_TO_PREPARED_PACKET")
    expected = tuple((u.review_context.identity.match_id, u.review_context.market_key.canonical, u.review_context_id) for u in review.packet.market_units)
    require(tuple((r.match_id, r.market_key.canonical, r.review_context_id) for r in reasons) == expected,
            "CORRECTION_REASONS_REQUIRE_EVERY_REVIEW_UNIT")
    uses = {u.snapshot.artifact_id: u for u in run.evidence}
    required_categories = {
        "CONFIRMED_LINEUP_CHANGE": {FactCategory.LINEUP}, "KEY_INJURY": {FactCategory.INJURY},
        "SUSPENSION": {FactCategory.SUSPENSION}, "ROTATION": {FactCategory.LINEUP, FactCategory.EXPECTED_LINEUP},
        "REST_ADVANTAGE": {FactCategory.REST}, "SCHEDULE_CONGESTION": {FactCategory.SCHEDULE},
        "MOTIVATION_EVIDENCE": {FactCategory.MOTIVATION}, "ODDS_CONTEXT": {FactCategory.ODDS_CONTEXT},
    }
    for reason in reasons:
        require(set(reason.evidence_snapshot_ids) <= set(uses), "CORRECTION_UNKNOWN_EVIDENCE")
        referenced = [uses[i] for i in reason.evidence_snapshot_ids]
        require(all(u.match_id == reason.match_id for u in referenced), "CORRECTION_CROSSES_MATCH")
        for category in reason.categories:
            if category not in required_categories:
                continue
            suitable = [u for u in referenced if u.category in required_categories[category] and u.freshness == FreshnessStatus.FRESH]
            require(suitable, "CORRECTION_CATEGORY_REQUIRES_FRESH_TYPED_EVIDENCE")
            if category == "CONFIRMED_LINEUP_CHANGE":
                require(reason.assertion_class == AssertionClass.FACT and any(
                    snapshots[u.snapshot.artifact_id].claim.assertion_class == AssertionClass.FACT
                    and isinstance(snapshots[u.snapshot.artifact_id].claim.structured_payload, LineupFactV1)
                    and snapshots[u.snapshot.artifact_id].claim.structured_payload.status == LineupStatus.CONFIRMED
                    for u in suitable), "UNKNOWN_OR_EXPECTED_LINEUP_IS_NOT_CONFIRMED")
            if category in {"KEY_INJURY", "SUSPENSION"}:
                kickoff = next(i.kickoff_at_utc for i in run.identities if i.match_id == reason.match_id)
                require(reason.assertion_class == AssertionClass.FACT and any(
                    snapshots[u.snapshot.artifact_id].claim.assertion_class == AssertionClass.FACT
                    and isinstance(snapshots[u.snapshot.artifact_id].claim.structured_payload, AbsenceFactV1)
                    and snapshots[u.snapshot.artifact_id].claim.structured_payload.knowledge_status == "RECORDS_AVAILABLE"
                    and any(r.status == "ACTIVE" and r.confidence > 0 and r.starts_at_utc is not None
                            and r.starts_at_utc <= kickoff and (r.ends_at_utc is None or kickoff < r.ends_at_utc)
                            and r.source_timestamp_utc is not None and r.available_at_utc is not None
                            and r.source_timestamp_utc <= r.available_at_utc <= run.decision_cutoff
                            for r in snapshots[u.snapshot.artifact_id].claim.structured_payload.records)
                    for u in suitable), "UNKNOWN_ABSENCE_IS_NOT_ACTIVE_INJURY_OR_BAN")
    return dict(run=ArtifactRefV1.of(run), review=ArtifactRefV1.of(review), raw_review_hash=review.raw_review_hash,
                imported_at_utc=at, clock_basis=basis, reasons=tuple(reasons))


def probability_frames(plan, audit):
    frames = []
    for unit, result, reviewed, reason in zip(plan.source.analysis.units, plan.source.fusion.results,
            plan.source.review.submission.market_reviews, audit.reasons, strict=True):
        market = unit.legacy_source.p_market if unit.legacy_source else unit.consensus.probabilities
        values = (market, unit.p_quant, unit.p_base, getattr(reviewed, "p_llm", None), result.p_final)
        frames.append(LockedProbabilityUnitV1(unit=ArtifactRefV1.of(unit), identity=unit.identity, market_key=unit.market_key,
            layers=tuple(LockedLayerV1(name=name, probabilities=p, distribution_hash=p.distribution_hash if p else None)
                         for name, p in zip(("P_market", "P_quant", "P_base", "P_llm", "P_final"), values, strict=True)),
            sp_snapshot=ArtifactRefV1.of(unit.sporttery), model_state_hash=unit.model_lineage.state_hash,
            review_status=reviewed.status, correction_categories=reason.categories))
    return tuple(frames)


def lock_values(epoch, run, plan, optimizer, audit, at, basis):
    assert_before_kickoff(epoch, run, at)
    assert_analysis_configuration(epoch, plan.source.analysis, run.budget_fen)
    c = epoch.configuration
    require(plan.source.analysis.artifact_id == run.analysis.artifact_id and ArtifactRefV1.of(plan.source.analysis) == run.analysis
            and plan.source.budget_fen == run.budget_fen and plan.profile == c.strategy_profile
            and plan.source.fusion.policy == c.fusion_policy, "LOCK_SOURCE_OR_FROZEN_CONFIGURATION_MISMATCH")
    require(ArtifactRefV1.of(plan.source.review) == audit.review and audit.run == ArtifactRefV1.of(run)
            and plan.source.review.packet.artifact_id == run.packet.artifact_id
            and audit.imported_at_utc <= at, "LOCK_REVIEW_LINEAGE_MISMATCH")
    require(optimizer.binding.plan == ArtifactRefV1.of(plan) and ArtifactRefV1.of(optimizer.policy) == c.return_policy
            and ArtifactRefV1.of(optimizer.objective) == c.objective_profile, "LOCK_OPTIMIZER_CONFIGURATION_MISMATCH")
    require(optimizer.status in {"OPTIMIZED", "NO_BET"} and optimizer.result is not None and optimizer.result.feasible,
            "DECISION_OPTIMIZER_UNAVAILABLE")
    frames = probability_frames(plan, audit)
    hashes = {name: content_hash("LOCK_LAYER_SET_V1", [(f.unit.artifact_id, next(p.distribution_hash for p in f.layers if p.name == name)) for f in frames])
              for name in ("P_market", "P_quant", "P_llm", "P_final")}
    result = optimizer.result
    fields = dict(run=ArtifactRefV1.of(run), epoch=ArtifactRefV1.of(epoch), locked_at_utc=at, clock_basis=basis,
        data_classification=run.data_classification, earliest_kickoff_at_utc=min(i.kickoff_at_utc for i in run.identities),
        analysis=run.analysis, packet=run.packet, review=ArtifactRefV1.of(plan.source.review), correction_audit=ArtifactRefV1.of(audit),
        fusion=ArtifactRefV1.of(plan.source.fusion), strategy_plan=ArtifactRefV1.of(plan), optimizer=ArtifactRefV1.of(optimizer),
        return_evaluation=ArtifactRefV1.of(result), frames=frames, selected=result.selected,
        budget_fen=run.budget_fen, stake_fen=result.distribution.stake_fen, cash_fen=result.distribution.cash_fen,
        p_market_hash=hashes["P_market"], p_quant_hash=hashes["P_quant"],
        p_llm_review_hash=content_hash("LOCK_REVIEW_LAYER_V1", [audit.raw_review_hash, hashes["P_llm"], audit.content_hash]),
        p_final_hash=hashes["P_final"], sp_hashes=tuple(f.sp_snapshot for f in frames),
        strategy_hash=plan.content_hash, optimizer_hash=optimizer.content_hash,
        implementation_hash=epoch.policy.implementation_hash, configuration_hash=c.configuration_hash)
    fields["complete_dependency_hash"] = content_hash("DECISION_LOCK_DEPENDENCIES_V1", fields)
    return fields


def realized_buckets(budget, cash, gross):
    ending = cash+gross
    return {"p_gross_payout_gt_zero": gross > 0, "p_break_even_or_better": ending >= budget,
            "p_2x_budget": ending >= 2*budget, "p_3x_budget": ending >= 3*budget,
            "p_loss": ending < budget, "p_deep_loss": ending <= budget//5}


@fixed_decimal(128)
def settlement_values(run, lock, evaluation, observations, at, previous=None):
    require(lock.run == ArtifactRefV1.of(run) and lock.return_evaluation == ArtifactRefV1.of(evaluation), "SETTLEMENT_LOCK_LINEAGE_MISMATCH")
    require(at >= max(i.kickoff_at_utc for i in run.identities), "SETTLEMENT_BEFORE_KICKOFF")
    expected = {i.match_id for i in run.identities}
    by_match = {o.claim.match_id: o for o in observations}
    require(len(by_match) == len(observations) and set(by_match) <= expected, "RESULT_OBSERVATION_SCOPE_MISMATCH")
    require(all(lock.locked_at_utc < o.ingested_at_utc <= at and o.claim.data_classification == run.data_classification for o in observations), "RESULT_VISIBILITY_OR_CLASSIFICATION_MISMATCH")
    missing = tuple(sorted(expected-set(by_match)))
    unsupported = tuple(sorted(mid for mid, o in by_match.items() if o.normalized_result is None))
    reason = "UNSUPPORTED_SETTLEMENT_CASE" if unsupported else "MISSING_RESULT" if missing else "SETTLED"
    if previous:
        require(previous.run == ArtifactRefV1.of(run) and previous.decision_lock == ArtifactRefV1.of(lock)
                and previous.settled_at_utc <= at, "SETTLEMENT_REVISION_CROSSES_LOCK")
    gross = None
    if reason == "SETTLED":
        assignment = {m.match_id: settle_market(m.market_key, by_match[m.match_id].normalized_result.home_goals,
                                               by_match[m.match_id].normalized_result.away_goals) for m in evaluation.distribution.matches}
        gross = payout_for_assignment(evaluation.distribution, assignment)
    return dict(run=ArtifactRefV1.of(run), decision_lock=ArtifactRefV1.of(lock), previous=ArtifactRefV1.of(previous) if previous else None,
        settled_at_utc=at, observations=tuple(ArtifactRefV1.of(o) for o in sorted(observations, key=lambda o: o.claim.match_id)),
        reason=reason, missing_match_ids=missing, unsupported_match_ids=unsupported,
        budget_fen=lock.budget_fen, stake_fen=lock.stake_fen, cash_fen=lock.cash_fen, gross_payout_fen=gross,
        ending_capital_fen=lock.cash_fen+gross if gross is not None else None,
        profit_loss_fen=lock.cash_fen+gross-lock.budget_fen if gross is not None else None,
        realized_buckets=realized_buckets(lock.budget_fen, lock.cash_fen, gross) if gross is not None else None)


def render_packet_markdown(run, packet, snapshots):
    lines = ["# Prospective analysis packet", "", f"Run: {run.artifact_id}", f"Classification: {run.data_classification}",
             f"Cutoff (trusted receipt): {run.decision_cutoff.isoformat()}", f"V4 packet hash: {packet.content_hash}",
             "", "JSON remains exact ANALYSIS_PACKET_V4. This markdown is an evidence/provenance companion.",
             "FACT / ANALYSIS / SPECULATION are distinct. UNKNOWN is not a negative fact or confirmation.",
             "No unsupported collusion inference from ownership, sponsors, region or loans.", ""]
    for unit in packet.market_units:
        c = unit.review_context
        lines += [f"## {c.identity.match_id} / {c.market_key.canonical}", f"Context hash: {c.content_hash}",
                  f"P_market: {canonical_json(c.p_market)}", f"P_quant: {canonical_json(c.p_quant)}"]
        used = [u for u in run.evidence if u.match_id == c.identity.match_id]
        if not used:
            lines.append("Dynamic context: UNKNOWN; no verified dynamic facts supplied.")
        for use in used:
            snapshot = snapshots[use.snapshot.artifact_id]
            lines += [f"### {use.category.value} / {use.assertion_class.value} / {use.freshness.value}",
                      f"Evidence snapshot: {snapshot.artifact_id}; hash: {snapshot.content_hash}",
                      f"V4 evidence ID: {use.football_evidence.artifact_id}",
                      f"Published: {snapshot.claim.published_at_utc}; available: {snapshot.claim.available_at_utc}; received: {snapshot.ingested_at_utc}",
                      "```json", canonical_json(snapshot.claim.structured_payload), "```"]
    lines += ["", "Return llm_review.json in the unchanged V4 schema plus a separate correction-reasons sidecar.",
              "Budget, tickets and objective weights are not delegated to GPT."]
    return "\n".join(lines)+"\n"
