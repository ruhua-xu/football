"""Pure release construction/projection and a separate, unchanged-V3 audit gate.

No provider, database, clock, network or self-approval service lives here. A
repository must validate the actual pilot/evidence graph and complete current
authorization inputs, then atomically persist the exact returned snapshots.
Both actual operation boundaries are mandatory for audit/downstream validation.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from football_system.application.quant_model import (
    freeze_elo_evaluation,
    freeze_elo_model_state,
    project_available_model_quant,
)
from football_system.application.review_bridge import (
    build_analysis_packet_v3,
    canonical_json,
    strict_json_loads,
)
from football_system.domain.common import normalize_utc
from football_system.domain.match import Match
from football_system.domain.prediction import QuantModelStateArtifact
from football_system.domain.production_release import (
    ApprovedTrainingHistoryAuditContentV1,
    ApprovedTrainingHistoryAuditV1,
    BuildAuthorizationContentV1,
    BuildAuthorizationV1,
    CurrentAuthorizationInputsV1,
    EloTrainingWindowV1,
    ProductionQuantModelReleaseContentV1,
    ProductionQuantModelReleaseV1,
    ProductionScopeV1,
    ProductionTargetAcceptancePlanV1,
    ReleaseArtifactRefV1,
    ReleasedStateCoreContentV1,
    ReleasedStateCoreV1,
    RetentionHorizonV1,
    TrainingHistoryApprovalV1,
    TrainingHistoryGraphV1,
    TrainingHistoryManifestV1,
    approval_active_for_build,
    approved_facts_root,
    assert_authorization_progression,
    assert_retention_authorized,
    history_summaries,
    prepare_approved_facts,
    release_active_for_inference,
    replay_exact_facts,
    revalidate,
)
from football_system.domain.review import (
    AnalysisPacketMatchSourceV3,
    AnalysisPacketSourceV3,
    AnalysisPacketV3,
    PacketModelQuantLineageV3,
    PacketQuantModelEvaluationV3,
    PacketQuantModelStateV3,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineState,
    EloPredictionRequest,
    EloThreeWayBaseline,
)
from football_system.domain.training_admission import (
    TrainingFactAdmissionV1,
    tagged_canonical_sha256,
)


def prepare_training_history(
    *,
    admissions: tuple[TrainingFactAdmissionV1, ...],
    training_window: EloTrainingWindowV1,
    integrity_pilot_scope_id: str,
) -> TrainingHistoryGraphV1:
    """Prepare full bindings/roots for a later pilot, not technical approval."""
    admissions = tuple(
        sorted(
            (revalidate(item) for item in admissions),
            key=lambda item: item.training_fact_admission_id,
        )
    )
    window = revalidate(training_window)
    facts = prepare_approved_facts(admissions, window, integrity_pilot_scope_id)
    if not facts:
        raise ValueError("training history requires admitted facts")
    sources, seasons = history_summaries(facts, window)
    maximum = max(
        item.content_payload.effective_source_available_at_utc for item in facts
    )
    state = replay_exact_facts(
        tuple(item.content_payload.elo_fact for item in facts),
        maximum,
        window.content_payload.seasons[-1].season_id,
    )
    return TrainingHistoryGraphV1(
        integrity_pilot_scope_id=integrity_pilot_scope_id,
        scope=ProductionScopeV1(
            competition_id=window.content_payload.competition_id,
            pilot_target_season_id=window.content_payload.seasons[-2].season_id,
            production_target_season_id=window.content_payload.seasons[-1].season_id,
            training_window_hash=window.content_hash,
        ),
        training_window=window,
        admissions=admissions,
        facts=facts,
        source_summaries=sources,
        season_summaries=seasons,
        source_count=len(sources),
        source_root=tagged_canonical_sha256("HISTORY_SOURCES_ROOT_V1", sources),
        season_count=len(seasons),
        season_root=tagged_canonical_sha256("HISTORY_SEASONS_ROOT_V1", seasons),
        fact_count=len(facts),
        approved_facts_hash=approved_facts_root(facts),
        training_data_hash=state.training_data_hash,
        max_effective_source_available_at_utc=maximum,
    )


def build_production_release(
    *,
    manifest: TrainingHistoryManifestV1,
    approval: TrainingHistoryApprovalV1,
    training_cutoff_at_utc: datetime,
    build_start: CurrentAuthorizationInputsV1,
    build_completion: CurrentAuthorizationInputsV1,
    persisted_at_utc: datetime,
    state_retention_horizon: RetentionHorizonV1,
    audit_retention_horizon: RetentionHorizonV1,
) -> ProductionQuantModelReleaseV1:
    """Reconstitute a caller-authorized offline build; performs no persistence."""
    manifest, approval = revalidate(manifest), revalidate(approval)
    build_start, build_completion = (
        revalidate(build_start),
        revalidate(build_completion),
    )
    state_retention_horizon, audit_retention_horizon = (
        revalidate(state_retention_horizon),
        revalidate(audit_retention_horizon),
    )
    cutoff, persisted = (
        normalize_utc(training_cutoff_at_utc),
        normalize_utc(persisted_at_utc),
    )
    history = manifest.content_payload.history
    authorizations = []
    for phase, current in (
        ("BUILD_START", build_start),
        ("BUILD_COMPLETION", build_completion),
    ):
        approval_active_for_build(approval=approval, manifest=manifest, current=current)
        assert_retention_authorized(
            approval, current, state_retention_horizon, audit_retention_horizon
        )
        authorizations.append(
            BuildAuthorizationV1.freeze(
                content_payload=BuildAuthorizationContentV1(
                    approval=approval.reference(),
                    manifest=manifest.reference(),
                    phase=phase,
                    current=current,
                    state_retention_horizon=state_retention_horizon,
                    audit_retention_horizon=audit_retention_horizon,
                )
            )
        )
    _assert_fact_cutoff(manifest, cutoff)
    state = replay_exact_facts(
        tuple(item.content_payload.elo_fact for item in history.facts),
        cutoff,
        history.scope.production_target_season_id,
    )
    core = ReleasedStateCoreV1.freeze(
        content_payload=ReleasedStateCoreContentV1.from_state(
            state=state,
            training_cutoff_at_utc=cutoff,
            approved_facts_hash=history.approved_facts_hash,
        )
    )
    evidence = manifest.content_payload.technical_evidence
    return ProductionQuantModelReleaseV1.freeze(
        content_payload=ProductionQuantModelReleaseContentV1(
            approval=approval.reference(),
            manifest=manifest.reference(),
            scope=history.scope,
            technical_evidence=evidence,
            build_recipe=evidence.build_recipe,
            code_revision=evidence.code_revision,
            released_state_core=core,
            release_facts=history.facts,
            build_start_authorization=authorizations[0],
            build_completion_authorization=authorizations[1],
            build_started_at_utc=build_start.actual_at_utc,
            build_completed_at_utc=build_completion.actual_at_utc,
            persisted_at_utc=persisted,
            state_retention_horizon=state_retention_horizon,
            audit_retention_horizon=audit_retention_horizon,
        ),
        training_manifest=manifest,
        training_approval=approval,
    )


def project_release_state(
    release: ProductionQuantModelReleaseV1,
    run_cutoff: datetime,
    target_ids: tuple[str, ...],
    target_season: str,
) -> EloBaselineState:
    """Mechanical sealed replay only; caller separately gates actual inference.

    Never query a research provider, silently drop targets, select correction
    heads, or replace source-time timestamps with local or effective time.
    """
    release = revalidate(release)
    cutoff = normalize_utc(run_cutoff)
    content = release.content_payload
    core = content.released_state_core
    if (
        not target_ids
        or len(target_ids) != len(set(target_ids))
        or any(
            not isinstance(item, str) or not item or item != item.strip()
            for item in target_ids
        )
    ):
        raise ValueError("projection requires exact unique nonempty target IDs")
    if target_season != content.scope.production_target_season_id:
        raise ValueError("target season mismatch")
    if set(target_ids).intersection(core.content_payload.training_match_ids):
        raise ValueError("target intersection with released training facts")
    if not content.persisted_at_utc < cutoff:
        raise ValueError("release must be persisted strictly before run cutoff")
    _assert_fact_cutoff(release.training_manifest, content.training_cutoff_at_utc)
    state = replay_exact_facts(
        core.content_payload.training_facts, cutoff, target_season
    )
    projected_core = ReleasedStateCoreV1.freeze(
        content_payload=ReleasedStateCoreContentV1.from_state(
            state=state,
            training_cutoff_at_utc=content.training_cutoff_at_utc,
            approved_facts_hash=core.content_payload.approved_facts_hash,
        )
    )
    if projected_core != core:
        raise ValueError("runtime state projection differs from sealed released core")
    return state


def freeze_approved_training_history_audit(
    *,
    packet: AnalysisPacketV3,
    input_manifest_json: str,
    model_state: QuantModelStateArtifact,
    release: ProductionQuantModelReleaseV1,
    plan: ProductionTargetAcceptancePlanV1,
    operation_start: CurrentAuthorizationInputsV1,
    operation_completion: CurrentAuthorizationInputsV1,
    state_retention_horizon: RetentionHorizonV1,
    audit_retention_horizon: RetentionHorizonV1,
    generated_at_utc: datetime,
) -> ApprovedTrainingHistoryAuditV1:
    """Seal an audit of a supplied real V3 packet; does not create a review."""
    content = _audit_content(
        packet=packet,
        model_state=model_state,
        release=release,
        plan=plan,
        state_retention_horizon=state_retention_horizon,
        audit_retention_horizon=audit_retention_horizon,
        generated_at_utc=generated_at_utc,
    )
    audit = ApprovedTrainingHistoryAuditV1.freeze(content_payload=content)
    validate_approved_training_history_audit(
        audit=audit,
        packet=packet,
        input_manifest_json=input_manifest_json,
        model_state=model_state,
        release=release,
        plan=plan,
        operation_start=operation_start,
        operation_completion=operation_completion,
    )
    return audit


def validate_approved_training_history_audit(
    *,
    audit: ApprovedTrainingHistoryAuditV1,
    packet: AnalysisPacketV3,
    input_manifest_json: str,
    model_state: QuantModelStateArtifact,
    release: ProductionQuantModelReleaseV1,
    plan: ProductionTargetAcceptancePlanV1,
    operation_start: CurrentAuthorizationInputsV1,
    operation_completion: CurrentAuthorizationInputsV1,
) -> None:
    """Separate fail-closed bundle gate; V3 bytes/schema/validator stay untouched.

    Repository integration must additionally validate LIVE_STRICT current input
    provenance and actual run authorization at both run boundaries. These inputs
    gate the *current* export/import/downstream operation, not a backdated run.
    """
    audit, packet, model_state = (
        revalidate(audit),
        revalidate(packet),
        revalidate(model_state),
    )
    release, plan = revalidate(release), revalidate(plan)
    start, completion = revalidate(operation_start), revalidate(operation_completion)
    assert_authorization_progression(start, completion)
    content, run = audit.content_payload, packet.analysis_run
    if not (
        run.completed_at_utc
        <= packet.generated_at_utc
        <= content.generated_at_utc
        <= completion.actual_at_utc
        and start.actual_at_utc <= completion.actual_at_utc
        and run.completed_at_utc <= start.actual_at_utc
    ):
        raise ValueError("audit/packet actual operation timeline mismatch")
    for current in (start, completion):
        release_active_for_inference(
            release=release,
            plan=plan,
            current=current,
            state_retention_horizon=content.state_retention_horizon,
            audit_retention_horizon=content.audit_retention_horizon,
        )
    expected = _audit_content(
        packet=packet,
        model_state=model_state,
        release=release,
        plan=plan,
        state_retention_horizon=content.state_retention_horizon,
        audit_retention_horizon=content.audit_retention_horizon,
        generated_at_utc=content.generated_at_utc,
    )
    if content != expected:
        raise ValueError(
            "audit packet/state/release/approval/manifest/pilot/plan mismatch"
        )
    target = plan.content_payload
    if run.as_of_at_utc != target.decision_as_of_at_utc or run.completed_at_utc >= min(
        item.kickoff_at_utc for item in target.targets
    ):
        raise ValueError("packet run cutoff/kickoff does not match target plan")
    actual_targets = tuple(
        (item.match_id, item.home_team_id, item.away_team_id, item.kickoff_at_utc)
        for item in packet.matches
    )
    expected_targets = tuple(
        (item.match_id, item.home_team_id, item.away_team_id, item.kickoff_at_utc)
        for item in target.targets
    )
    if actual_targets != expected_targets or any(
        item.competition_id != target.competition_id for item in packet.matches
    ):
        raise ValueError("packet targets differ from exact acceptance plan")
    source = AnalysisPacketSourceV3(
        analysis_run=run,
        quant_model_states=packet.quant_model_states,
        matches=tuple(
            AnalysisPacketMatchSourceV3.model_validate(
                item.model_dump(
                    exclude={"review_context_id", "review_context_hash"}, mode="python"
                )
            )
            for item in packet.matches
        ),
    )
    if (
        build_analysis_packet_v3(source, generated_at_utc=packet.generated_at_utc)
        != packet
    ):
        raise ValueError("V3 packet/context ID or hash mismatch")
    state = project_release_state(
        release,
        run.as_of_at_utc,
        tuple(item.match_id for item in target.targets),
        target.production_target_season_id,
    )
    frozen = freeze_elo_model_state(
        analysis_run_id=run.analysis_run_id,
        baseline=EloThreeWayBaseline(),
        state=state,
        generated_at_utc=model_state.generated_at_utc,
    )
    if (
        frozen != model_state
        or not run.started_at_utc
        <= model_state.generated_at_utc
        <= run.completed_at_utc
    ):
        raise ValueError("run-scoped model state does not project exact released core")
    packet_state = PacketQuantModelStateV3(
        **model_state.model_dump(
            mode="python", exclude={"config_json", "state_json", "training_facts"}
        ),
        training_fact_count=len(state.training_facts),
        training_match_ids=state.training_match_ids,
        training_result_ids=state.training_result_ids,
    )
    if packet.quant_model_states != (packet_state,) or any(
        not isinstance(item.p_quant, PacketModelQuantLineageV3)
        or item.p_quant.evaluation.quant_model_state_id
        != model_state.quant_model_state_id
        for item in packet.matches
    ):
        raise ValueError("packet model state/ordered training lineage mismatch")
    for match in packet.matches:
        evaluation = match.p_quant.evaluation
        prediction = EloThreeWayBaseline().predict_from_state(
            EloPredictionRequest(
                match_id=match.match_id,
                season_id=target.production_target_season_id,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                kickoff_at_utc=match.kickoff_at_utc,
                cutoff_at_utc=run.as_of_at_utc,
            ),
            state,
        )
        expected_evaluation = freeze_elo_evaluation(
            analysis_run_id=run.analysis_run_id,
            model_state=model_state,
            prediction=prediction,
            market=evaluation.market,
            evaluated_at_utc=evaluation.evaluated_at_utc,
        )
        expected_packet_evaluation = PacketQuantModelEvaluationV3.model_validate(
            expected_evaluation.model_dump(mode="python", exclude={"output_json"})
        )
        if (
            evaluation != expected_packet_evaluation
            or match.p_quant.prediction
            != project_available_model_quant(
                model_state=model_state, evaluation=expected_evaluation
            )
        ):
            raise ValueError(
                "packet quant evaluation does not replay from released state"
            )
    manifest = strict_json_loads(input_manifest_json.encode("utf-8"))
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "version",
            "competitions",
            "teams",
            "matches",
            "provider_mappings",
            "market_odds_snapshots",
            "sporttery_bonus_snapshots",
            "quant_model_states",
        }
        or canonical_json(manifest) != input_manifest_json
        or hashlib.sha256(input_manifest_json.encode("utf-8")).hexdigest()
        != run.input_manifest_hash
        or manifest.get("version") != "MVP_INPUT_MANIFEST_V3"
        or manifest.get("quant_model_states") != [model_state.model_dump(mode="json")]
    ):
        raise ValueError("V3 input manifest/state/hash mismatch")
    if not isinstance(manifest["matches"], list):
        raise ValueError("V3 input manifest matches must be an array")
    fixtures = tuple(Match.model_validate(item) for item in manifest["matches"])
    if tuple(
        (item.match_id, item.home_team_id, item.away_team_id, item.kickoff_at_utc)
        for item in fixtures
    ) != expected_targets or any(
        item.competition_id != target.competition_id
        or item.available_at_utc > run.as_of_at_utc
        for item in fixtures
    ):
        raise ValueError("V3 input manifest fixtures differ from sealed targets/cutoff")


def _audit_content(
    *,
    packet,
    model_state,
    release,
    plan,
    state_retention_horizon,
    audit_retention_horizon,
    generated_at_utc,
) -> ApprovedTrainingHistoryAuditContentV1:
    packet, model_state, release, plan = map(
        revalidate, (packet, model_state, release, plan)
    )
    run, content = packet.analysis_run, release.content_payload
    history = release.training_manifest.content_payload.history
    return ApprovedTrainingHistoryAuditContentV1(
        analysis_run_id=run.analysis_run_id,
        input_manifest_hash=run.input_manifest_hash,
        target_acceptance_plan=plan.reference(),
        packet=ReleaseArtifactRefV1(
            artifact_id=packet.packet_id, content_hash=packet.packet_hash
        ),
        quant_model_state_id=model_state.quant_model_state_id,
        state_hash=model_state.state_hash,
        state_payload_hash=model_state.state_payload_hash,
        training_data_hash=model_state.training_data_hash,
        release=release.reference(),
        released_state_core_hash=content.released_state_core.content_hash,
        approval=content.approval,
        manifest=content.manifest,
        approved_facts_hash=history.approved_facts_hash,
        technical_evidence=content.technical_evidence,
        source_summaries=history.source_summaries,
        season_summaries=history.season_summaries,
        training_cutoff_at_utc=content.training_cutoff_at_utc,
        decision_as_of_at_utc=run.as_of_at_utc,
        run_started_at_utc=run.started_at_utc,
        run_completed_at_utc=run.completed_at_utc,
        run_code_revision=run.code_revision,
        state_retention_horizon=state_retention_horizon,
        audit_retention_horizon=audit_retention_horizon,
        generated_at_utc=normalize_utc(generated_at_utc),
    )


def _assert_fact_cutoff(manifest: TrainingHistoryManifestV1, cutoff: datetime) -> None:
    for fact in manifest.content_payload.history.facts:
        binding = fact.content_payload.binding.content_payload
        for source_at in (
            binding.fixture_source.source_available_at_utc,
            binding.season_membership.content_payload.source_available_at_utc,
            binding.match_result_admission.content_payload.source_available_at_utc,
        ):
            if source_at > cutoff:
                raise ValueError(
                    "newer fixture/mapping/result source fact crosses training cutoff"
                )
