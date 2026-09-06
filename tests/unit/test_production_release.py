"""Fabricated contract fixtures only. No real source rights or approval events."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, getcontext, localcontext

import pytest
from pydantic import ValidationError

from football_system.application.production_release import (
    build_production_release,
    freeze_approved_training_history_audit,
    prepare_training_history,
    project_release_state,
    validate_approved_training_history_audit,
)
from football_system.application.quant_model import (
    build_model_input_manifest_json,
    freeze_elo_evaluation,
    freeze_elo_model_state,
    project_available_model_quant,
)
from football_system.application.review_bridge import (
    build_analysis_packet_v3,
    canonical_json,
)
from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType, ThreeWayProbability
from football_system.domain.match import Match
from football_system.domain.production_release import (
    PRODUCTION_CONFIG_HASH,
    TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
    ApprovedTrainingHistoryAuditV1,
    ApprovalSuccessorContentV1,
    ApprovalSuccessorV1,
    CurrentAuthorizationInputsV1,
    EloTrainingWindowContentV1,
    EloTrainingWindowV1,
    FixedProductionModelV1,
    GrantRevocationContentV1,
    GrantRevocationV1,
    ProductionGrantKind,
    ProductionGrantV1,
    ProductionQuantModelReleaseV1,
    ProductionTargetAcceptancePlanContentV1,
    ProductionTargetAcceptancePlanV1,
    ProductionTargetV1,
    ProviderSeasonRefV1,
    ReleaseArtifactRefV1,
    ReleasedStateCoreContentV1,
    ReleasedStateCoreV1,
    RetentionHorizonV1,
    SourceCorrectionContentV1,
    SourceCorrectionV1,
    TechnicalEvidenceRefsV1,
    TrainingHistoryApprovalContentV1,
    TrainingHistoryApprovalPayloadV1,
    TrainingHistoryApprovalV1,
    TrainingHistoryGraphV1,
    TrainingHistoryManifestContentV1,
    TrainingHistoryManifestV1,
    TrainingSeasonV1,
    approval_active_for_build,
    elo_result_from_binding,
    release_active_for_inference,
    revalidate,
    source_rights_refs,
)
from football_system.domain.review import (
    AnalysisPacketMatchSourceV3,
    AnalysisPacketRunV3,
    AnalysisPacketSourceV3,
    MatchReviewContext,
    PacketDataQuality,
    PacketMarketPrediction,
    PacketModelQuantLineageV3,
    PacketQuantModelEvaluationV3,
    PacketQuantModelStateV3,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloPredictionRequest,
    EloThreeWayBaseline,
)
from football_system.domain.training_admission import (
    SOURCE_RIGHTS_PAYLOAD_V1,
    LocalReviewEvidenceV1,
    LocalReviewerAttestationContentV1,
    LocalReviewerAttestationV1,
    MatchResultAdmissionContentV1,
    MatchResultAdmissionV1,
    MatchSeasonMembershipContentV1,
    MatchSeasonMembershipV1,
    NormalizedMatchResultRecordV1,
    SourceRightsAdmissionV1,
    SourceRightsPayloadV1,
    SourceRightsPermittedUse,
    TrainingCanonicalMatchIdentityV1,
    TrainingFactAdmissionV1,
    TrainingFactBindingContentV1,
    TrainingFactBindingV1,
    TrainingFixtureSourceV1,
    TrainingProviderMatchMappingV1,
    normalized_match_result_record_sha256,
    tagged_canonical_sha256,
)

UTC = timezone.utc
IMPORT = datetime(2026, 7, 1, tzinfo=UTC)
MANIFEST_AT = IMPORT + timedelta(days=4)
APPROVAL_AT = IMPORT + timedelta(days=5)
CUTOFF = IMPORT + timedelta(days=6)
BUILD = IMPORT + timedelta(days=7)
DECISION = IMPORT + timedelta(days=9)
END = datetime(2028, 1, 1, tzinfo=UTC)
RIGHTS_END = datetime(2030, 1, 1, tzinfo=UTC)
TRAINING = ProductionGrantKind.PRODUCTION_MODEL_TRAINING
INFERENCE = ProductionGrantKind.PRODUCTION_MODEL_INFERENCE
STATE = ProductionGrantKind.DERIVED_MODEL_STATE_RETENTION
AUDIT = ProductionGrantKind.AUDIT_HASH_RETENTION


def _ref(name):
    return ReleaseArtifactRefV1(
        artifact_id=name, content_hash=hashlib.sha256(name.encode()).hexdigest()
    )


def _horizon(until=END):
    return RetentionHorizonV1(indefinite=until is None, retain_until_at_utc=until)


def _attest(payload, tag, reviewed_at):
    return LocalReviewerAttestationV1.freeze(
        content_payload=LocalReviewerAttestationContentV1(
            attested_schema_version=tag,
            attested_payload_hash=tagged_canonical_sha256(tag, payload),
            authorized_reviewer="TEST_ONLY_REVIEWER",
            reviewer_authority_reference="local://test-only/authority",
            authority_sha256="a" * 64,
            reviewed_at_utc=reviewed_at,
            evidence=LocalReviewEvidenceV1(
                evidence_reference="local://test-only/not-real-evidence",
                evidence_sha256="b" * 64,
            ),
        )
    )


def _admission():
    payload = SourceRightsPayloadV1.freeze(
        source_owner="TEST_ONLY_NOT_REAL_RIGHTS",
        product_name="TEST_ONLY",
        source_ids=("test-source",),
        terms_version="test-1",
        terms_reference="local://test-only/terms",
        terms_sha256="c" * 64,
        jurisdiction="TEST",
        effective_at_utc=IMPORT - timedelta(days=30),
        expires_at_utc=RIGHTS_END,
        permitted_uses=tuple(SourceRightsPermittedUse),
        raw_retention_rule="Test local-only raw retention.",
        derived_retention_rule="Test append-only state retention.",
        subscription_end_retention_rule="Test explicit retention.",
        deletion_obligation="Test compatible obligations.",
        public_repository_boundary="No raw data or evidence.",
    )
    rights = SourceRightsAdmissionV1.from_recorded(
        rights_payload=payload,
        reviewer_attestation=_attest(
            payload, SOURCE_RIGHTS_PAYLOAD_V1, IMPORT - timedelta(days=29)
        ),
        recorded_at_utc=IMPORT - timedelta(days=28),
    )
    facts = []
    for index in range(6):
        season = "warmup" if index < 3 else "pilot"
        kickoff = datetime(2025 if index < 3 else 2026, 1, 1, tzinfo=UTC) + timedelta(
            days=index
        )
        available = kickoff + timedelta(hours=3)
        fixture = TrainingFixtureSourceV1(
            source_id="test-source",
            provider_code="test-provider",
            provider_fixture_namespace="fixture",
            provider_fixture_key=f"fixture-{index}",
            internal_match_id=f"history-{index}",
            fixture_source_archive_id="test-fixtures",
            fixture_source_archive_payload_sha256="d" * 64,
            fixture_source_archive_created_at_utc=IMPORT + timedelta(seconds=1),
            fixture_source_record_id=f"fixture-record-{index}",
            fixture_record_sha256=_ref(f"fixture-{index}").content_hash,
            source_available_at_utc=kickoff - timedelta(days=1),
            local_imported_at_utc=IMPORT,
            registered_at_utc=IMPORT + timedelta(seconds=2),
        )
        membership = MatchSeasonMembershipV1.freeze(
            content_payload=MatchSeasonMembershipContentV1(
                source_id="test-source",
                provider_code="test-provider",
                provider_competition_id="test-league",
                provider_season_id=season,
                provider_season_candidate_ids=(season,),
                provider_fixture_namespace="fixture",
                provider_fixture_key=f"fixture-{index}",
                provider_mapping_id=f"mapping-{index}",
                internal_match_id=f"history-{index}",
                fixture_source_record_id=fixture.fixture_source_record_id,
                fixture_record_sha256=fixture.fixture_record_sha256,
                provider_scope_raw_artifact_id=f"scope-{season}",
                provider_scope_payload_sha256="e" * 64,
                provider_scope_created_at_utc=IMPORT + timedelta(seconds=1),
                provider_scope_record_sha256=_ref(f"scope-{index}").content_hash,
                provider_competition_field_path="fixture.league",
                provider_season_field_path="fixture.season",
                provider_fixture_field_path="fixture.id",
                season_assignment_method="PROVIDER_EXPLICIT_FIELDS",
                season_mapping_version="test-map-1",
                canonical_competition_id="bundesliga",
                canonical_season_id=season,
                source_available_at_utc=available + timedelta(minutes=1),
                local_imported_at_utc=IMPORT,
                registered_at_utc=IMPORT + timedelta(seconds=2),
                mapping_policy_version="test-1",
                reviewed_by="TEST_ONLY_REVIEWER",
                reviewed_at_utc=IMPORT + timedelta(seconds=3),
            )
        )
        result = NormalizedMatchResultRecordV1(
            match_result_id=f"result-{index}",
            match_id=f"history-{index}",
            provider_code="test-provider",
            home_goals=2,
            away_goals=index % 3,
            observed_at_utc=available - timedelta(minutes=1),
            available_at_utc=available,
            ingested_at_utc=available,
            source_result_key=f"result-key-{index}",
            payload_hash=match_result_payload_sha256(2, index % 3),
        )
        result_admission = MatchResultAdmissionV1.freeze(
            content_payload=MatchResultAdmissionContentV1(
                source_id="test-source",
                internal_match_id=result.match_id,
                match_result_id=result.match_result_id,
                provider_code="test-provider",
                provider_result_key=result.source_result_key,
                provider_raw_status="FT",
                status_mapping_version="test-status-1",
                provider_status_category="REGULAR_TIME_FINAL",
                score_semantics="REGULAR_TIME_ONLY",
                regular_time_home_goals=result.home_goals,
                regular_time_away_goals=result.away_goals,
                provider_finalized_at_utc=available - timedelta(minutes=2),
                source_observed_at_utc=result.observed_at_utc,
                source_available_at_utc=result.available_at_utc,
                raw_artifact_id="test-results",
                raw_artifact_payload_sha256="f" * 64,
                raw_artifact_created_at_utc=IMPORT + timedelta(seconds=1),
                raw_record_sha256=_ref(f"result-{index}").content_hash,
                normalized_record_sha256=normalized_match_result_record_sha256(result),
                local_imported_at_utc=IMPORT,
                registered_at_utc=IMPORT + timedelta(seconds=2),
                adapter_name="test-only",
                adapter_version="1",
                reviewed_by="TEST_ONLY_REVIEWER",
                reviewed_at_utc=IMPORT + timedelta(seconds=3),
            )
        )
        facts.append(
            TrainingFactBindingV1.freeze(
                content_payload=TrainingFactBindingContentV1(
                    sequence=index,
                    fixture_source=fixture,
                    season_membership=membership,
                    provider_mapping=TrainingProviderMatchMappingV1(
                        mapping_id=f"mapping-{index}",
                        provider_code="test-provider",
                        external_namespace="fixture",
                        external_match_id=f"fixture-{index}",
                        internal_match_id=result.match_id,
                        resolution_method="EXPLICIT",
                        confidence=Decimal(1),
                        available_at_utc=kickoff - timedelta(days=1),
                    ),
                    canonical_identity=TrainingCanonicalMatchIdentityV1(
                        internal_match_id=result.match_id,
                        internal_competition_id="bundesliga",
                        internal_home_team_id="alpha",
                        internal_away_team_id="bravo",
                        season=season,
                        competition_type="DOMESTIC_LEAGUE",
                        kickoff_at_utc=kickoff,
                    ),
                    match_result_admission=result_admission,
                    normalized_result=result,
                )
            )
        )
    return TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=rights,
        facts=tuple(facts),
        actual_started_at_utc=IMPORT + timedelta(minutes=1),
        actual_completed_at_utc=IMPORT + timedelta(minutes=2),
        persisted_at_utc=IMPORT + timedelta(minutes=3),
    )


@pytest.fixture(scope="module")
def manifest():
    window = EloTrainingWindowV1.freeze(
        content_payload=EloTrainingWindowContentV1(
            competition_id="bundesliga",
            seasons=tuple(
                TrainingSeasonV1(
                    season_sequence=index,
                    season_id=season,
                    role=role,
                    provider_seasons=(
                        ProviderSeasonRefV1(
                            source_id="test-source",
                            provider_code="test-provider",
                            provider_competition_id="test-league",
                            provider_season_id=season,
                        ),
                    ),
                )
                for index, (season, role) in enumerate(
                    (
                        ("warmup", "WARMUP"),
                        ("pilot", "PILOT_TARGET"),
                        ("production", "PRODUCTION_TARGET"),
                    )
                )
            ),
        )
    )
    history = prepare_training_history(
        admissions=(_admission(),),
        training_window=window,
        integrity_pilot_scope_id="test-pilot-scope",
    )
    evidence = TechnicalEvidenceRefsV1(
        integrity_pilot_scope_id=history.integrity_pilot_scope_id,
        integrity_pilot_series_id="test-series",
        scope=history.scope,
        plan=_ref("test-plan"),
        summary=_ref("test-summary"),
        attestation=_ref("test-attestation"),
        report=_ref("test-report"),
        attempt_count=2,
        attempt_root="1" * 64,
        source_root=history.source_root,
        season_root=history.season_root,
        approved_facts_hash=history.approved_facts_hash,
        training_data_hash=history.training_data_hash,
        terminal_state_core_hash="2" * 64,
        build_recipe=_ref("test-recipe"),
        code_revision="test-code-revision",
        plan_sealed_at_utc=IMPORT + timedelta(days=1),
        actual_started_at_utc=IMPORT + timedelta(days=2),
        actual_completed_at_utc=IMPORT + timedelta(days=2, minutes=1),
        attestation_persisted_at_utc=IMPORT + timedelta(days=3),
    )
    return TrainingHistoryManifestV1.freeze(
        content_payload=TrainingHistoryManifestContentV1(
            history=history,
            technical_evidence=evidence,
            created_at_utc=MANIFEST_AT,
            persisted_at_utc=MANIFEST_AT,
        )
    )


def _approval(manifest, updates=None):
    grants = []
    for kind in sorted(ProductionGrantKind):
        values = dict(
            grant=kind,
            effective_at_utc=APPROVAL_AT,
            expires_at_utc=DECISION - timedelta(days=1)
            if kind == TRAINING
            else RIGHTS_END,
            retention_rule="Test explicit state/hash permission."
            if kind in {STATE, AUDIT}
            else None,
            retention=_horizon() if kind in {STATE, AUDIT} else None,
        )
        values.update((updates or {}).get(kind, {}))
        grants.append(ProductionGrantV1(**values))
    evidence = manifest.content_payload.technical_evidence
    payload = TrainingHistoryApprovalPayloadV1(
        manifest=manifest.reference(),
        scope=manifest.content_payload.history.scope,
        source_rights=source_rights_refs(manifest.content_payload.history),
        technical_evidence=evidence,
        build_recipe=evidence.build_recipe,
        code_revision=evidence.code_revision,
        approver="TEST_ONLY_REVIEWER",
        authority_reference="local://test-only/authority",
        authority_sha256="a" * 64,
        grants=tuple(grants),
        retention_compatibility="APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE",
        approved_at_utc=APPROVAL_AT,
        persisted_at_utc=APPROVAL_AT,
        supersedes_approval=None,
        supersession_effective_at_utc=None,
        superseded_grants=(),
    )
    return _seal_approval(payload)


def _seal_approval(payload):
    return TrainingHistoryApprovalV1.freeze(
        content_payload=TrainingHistoryApprovalContentV1(
            approval_payload=payload,
            approval_payload_hash=payload.approval_payload_hash,
            reviewer_attestation=_attest(
                payload, TRAINING_HISTORY_APPROVAL_PAYLOAD_V1, payload.approved_at_utc
            ),
        )
    )


def _current(manifest, at, **updates):
    return CurrentAuthorizationInputsV1(
        **dict(
            actual_at_utc=at,
            technical_evidence=manifest.content_payload.technical_evidence,
            corrections=(),
            revocations=(),
            successors=(),
        )
        | updates
    )


def _build(manifest, approval=None, horizon=None, **updates):
    return build_production_release(
        **dict(
            manifest=manifest,
            approval=approval or _approval(manifest),
            training_cutoff_at_utc=CUTOFF,
            build_start=_current(manifest, BUILD),
            build_completion=_current(manifest, BUILD + timedelta(minutes=1)),
            persisted_at_utc=BUILD + timedelta(minutes=2),
            state_retention_horizon=horizon or _horizon(),
            audit_retention_horizon=horizon or _horizon(),
        )
        | updates
    )


def _plan(pinned_release, **updates):
    return ProductionTargetAcceptancePlanV1.freeze(
        content_payload=ProductionTargetAcceptancePlanContentV1(
            **dict(
                release=pinned_release.reference(),
                competition_id="bundesliga",
                production_target_season_id="production",
                targets=(
                    ProductionTargetV1(
                        match_id="target",
                        home_team_id="alpha",
                        away_team_id="bravo",
                        kickoff_at_utc=DECISION + timedelta(days=1),
                    ),
                ),
                kickoff_window_start_at_utc=DECISION + timedelta(hours=12),
                kickoff_window_end_at_utc=DECISION + timedelta(days=2),
                decision_as_of_at_utc=DECISION,
                selection_rule="KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1",
                sealed_at_utc=DECISION - timedelta(days=1),
                persisted_at_utc=DECISION - timedelta(days=1),
            )
            | updates
        )
    )


@pytest.fixture(scope="module")
def release(manifest):
    return _build(manifest)


def _inference(release, current=None, horizon=None, plan=None):
    return release_active_for_inference(
        release=release,
        plan=plan or _plan(release),
        current=current or _current(release.training_manifest, DECISION),
        state_retention_horizon=horizon or _horizon(),
        audit_retention_horizon=horizon or _horizon(),
    )


def _revocation(
    release, kind, *, recorded=DECISION, effective=DECISION, release_specific=False
):
    return GrantRevocationV1.freeze(
        content_payload=GrantRevocationContentV1(
            approval=release.training_approval.reference(),
            release=release.reference() if release_specific else None,
            affected_grants=(kind,),
            actor="TEST_ONLY_REVIEWER",
            authority_reference="local://test-only/authority",
            authority_sha256="a" * 64,
            reason="Test revocation",
            recorded_at_utc=recorded,
            effective_at_utc=effective,
        )
    )


def _successor(release, kind, **updates):
    return ApprovalSuccessorV1.freeze(
        content_payload=ApprovalSuccessorContentV1(
            **dict(
                predecessor=release.training_approval.reference(),
                successor=_ref("test-successor"),
                predecessor_scope=release.content_payload.scope,
                successor_scope=release.content_payload.scope,
                affected_grants=(kind,),
                successor_persisted_at_utc=DECISION,
                effective_at_utc=DECISION,
            )
            | updates
        )
    )


def _correction(release, component, **updates):
    binding = release.content_payload.release_facts[
        0
    ].content_payload.binding.content_payload
    if component == "FIXTURE":
        predecessor = ReleaseArtifactRefV1(
            artifact_id=binding.fixture_source.fixture_source_record_id,
            content_hash=binding.fixture_source.fixture_record_sha256,
        )
        source_at = binding.fixture_source.source_available_at_utc
    elif component == "MAPPING":
        mapping = binding.provider_mapping
        predecessor = ReleaseArtifactRefV1(
            artifact_id=mapping.mapping_id,
            content_hash=tagged_canonical_sha256(mapping.schema_version, mapping),
        )
        source_at = mapping.available_at_utc
    elif component == "SEASON":
        predecessor = ReleaseArtifactRefV1(
            artifact_id=binding.season_membership.season_membership_id,
            content_hash=binding.season_membership.membership_hash,
        )
        source_at = binding.season_membership.content_payload.source_available_at_utc
    else:
        predecessor = ReleaseArtifactRefV1(
            artifact_id=binding.match_result_admission.match_result_admission_id,
            content_hash=binding.match_result_admission.admission_hash,
        )
        source_at = (
            binding.match_result_admission.content_payload.source_available_at_utc
        )
    return SourceCorrectionV1.freeze(
        content_payload=SourceCorrectionContentV1(
            **dict(
                source_id="test-source",
                provider_code="test-provider",
                match_id="history-0",
                component=component,
                predecessor=predecessor,
                successor=_ref("test-new-correction"),
                predecessor_source_available_at_utc=source_at,
                source_available_at_utc=source_at + timedelta(hours=1),
                local_imported_at_utc=DECISION,
                registered_at_utc=DECISION + timedelta(minutes=1),
            )
            | updates
        )
    )


def test_full_history_preserves_source_and_actual_times_and_distinct_roots(manifest):
    history = manifest.content_payload.history
    assert history.scope.pilot_target_season_id == "pilot"
    assert history.scope.production_target_season_id == "production"
    assert [item.fact_count for item in history.season_summaries] == [3, 3, 0]
    assert history.approved_facts_hash != history.training_data_hash
    assert history.fact_count == 6
    for fact in history.facts:
        content = fact.content_payload
        normalized = content.binding.content_payload.normalized_result
        assert content.elo_fact.available_at_utc == normalized.available_at_utc
        assert content.elo_fact.ingested_at_utc == normalized.ingested_at_utc
        assert (
            content.effective_source_available_at_utc > content.elo_fact.ingested_at_utc
        )
        assert (
            content.binding.content_payload.fixture_source.local_imported_at_utc
            == IMPORT
        )
        assert (
            content.binding.content_payload.canonical_identity.season
            == content.elo_fact.season_id
        )


def test_replay_at_two_cutoffs_keeps_core_math_and_training_hash(release):
    first = project_release_state(release, DECISION, ("target",), "production")
    second = project_release_state(
        release, DECISION + timedelta(hours=1), ("target-2",), "production"
    )
    assert first.state_hash != second.state_hash
    assert first.training_data_hash == second.training_data_hash
    assert first.teams == second.teams
    assert tuple(team.prior_matches for team in first.teams) == (6, 6)
    assert first.training_facts == second.training_facts
    direct = EloThreeWayBaseline().rebuild_state(
        tuple(
            elo_result_from_binding(item.content_payload.binding)
            for item in release.content_payload.release_facts
        ),
        DECISION,
        target_season_id="production",
    )
    assert first == direct
    core = release.content_payload.released_state_core
    assert core.content_payload == ReleasedStateCoreContentV1.from_state(
        state=second,
        training_cutoff_at_utc=CUTOFF,
        approved_facts_hash=core.content_payload.approved_facts_hash,
    )
    excluded = {
        "run_id",
        "analysis_run_id",
        "release_id",
        "cutoff_at_utc",
        "generated_at_utc",
        "state_hash",
        "content_hash",
        "artifact_id",
    }
    assert excluded.isdisjoint(core.content_payload.model_dump())


def test_training_expiry_does_not_invalidate_independent_inference(release):
    assert _inference(release) is True
    with pytest.raises(
        ValueError, match="PRODUCTION_MODEL_TRAINING grant is not active"
    ):
        approval_active_for_build(
            approval=release.training_approval,
            manifest=release.training_manifest,
            current=_current(release.training_manifest, DECISION),
        )


@pytest.mark.parametrize("kind", [INFERENCE, STATE, AUDIT])
def test_each_independent_inference_grant_expiry_fails_closed(manifest, kind):
    updates = {
        TRAINING: {"expires_at_utc": RIGHTS_END},
        kind: {"expires_at_utc": DECISION},
    }
    if kind in {STATE, AUDIT}:
        updates[kind]["retention"] = _horizon(DECISION)
    release = _build(manifest, _approval(manifest, updates), _horizon(DECISION))
    assert approval_active_for_build(
        approval=release.training_approval,
        manifest=manifest,
        current=_current(manifest, DECISION),
    )
    with pytest.raises(ValueError, match=f"{kind} grant is not active"):
        _inference(release, horizon=_horizon(DECISION))


@pytest.mark.parametrize("kind", list(ProductionGrantKind))
def test_grant_effective_boundary_is_independent(manifest, kind):
    approval = _approval(
        manifest, {kind: {"effective_at_utc": BUILD + timedelta(seconds=30)}}
    )
    if kind == INFERENCE:
        assert _build(manifest, approval)
    else:
        with pytest.raises(ValueError, match=f"{kind} grant is not active"):
            _build(manifest, approval)


@pytest.mark.parametrize("kind", list(ProductionGrantKind))
def test_revocation_is_per_grant_and_uses_recorded_and_effective_clocks(release, kind):
    event = _revocation(release, kind, recorded=DECISION + timedelta(seconds=1))
    assert _inference(
        release, _current(release.training_manifest, DECISION, revocations=(event,))
    )
    current = _current(
        release.training_manifest, DECISION + timedelta(seconds=1), revocations=(event,)
    )
    if kind == TRAINING:
        assert _inference(release, current)
    else:
        with pytest.raises(ValueError, match="revoked"):
            _inference(release, current)
    future = _revocation(release, kind, effective=DECISION + timedelta(hours=1))
    assert _inference(
        release, _current(release.training_manifest, DECISION, revocations=(future,))
    )


@pytest.mark.parametrize("kind", [INFERENCE, STATE, AUDIT])
def test_release_specific_revocation_blocks_only_affected_inference_grants(
    release, kind
):
    current = _current(
        release.training_manifest,
        DECISION,
        revocations=(_revocation(release, kind, release_specific=True),),
    )
    with pytest.raises(ValueError, match="revoked"):
        _inference(release, current)


def test_build_completion_rechecks_training_authorization(manifest, release):
    event = _revocation(
        release, TRAINING, recorded=BUILD, effective=BUILD + timedelta(minutes=1)
    )
    with pytest.raises(ValueError, match="revoked"):
        _build(
            manifest,
            build_completion=_current(
                manifest, BUILD + timedelta(minutes=1), revocations=(event,)
            ),
        )


@pytest.mark.parametrize("kind", [STATE, AUDIT])
def test_retention_must_cover_declared_horizons_not_just_be_active(manifest, kind):
    approval = _approval(
        manifest, {kind: {"retention": _horizon(END - timedelta(days=1))}}
    )
    release = _build(manifest, approval, _horizon(END - timedelta(days=1)))
    with pytest.raises(ValueError, match="does not cover declared retention horizon"):
        _inference(release, horizon=_horizon())
    with pytest.raises(ValueError, match="does not cover declared retention horizon"):
        _inference(release, horizon=_horizon(None))


def test_explicit_indefinite_retention_and_no_ambient_decimal_context_change(manifest):
    updates = {
        kind: {"retention": _horizon(None), "expires_at_utc": None}
        for kind in (STATE, AUDIT)
    }
    release = _build(manifest, _approval(manifest, updates), _horizon(None))
    assert _inference(release, horizon=_horizon(None))
    before = getcontext().copy()
    first = tagged_canonical_sha256(
        "TEST_NEW_HASH_V1", {"rating": Decimal("1500.123456789012000")}
    )
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert (
            tagged_canonical_sha256(
                "TEST_NEW_HASH_V1", {"rating": Decimal("1500.123456789012000")}
            )
            == first
        )
    assert getcontext().prec == before.prec
    assert getcontext().traps == before.traps


@pytest.mark.parametrize(
    "updates",
    [
        {"indefinite": False, "retain_until_at_utc": None},
        {"indefinite": True, "retain_until_at_utc": END},
    ],
)
def test_retention_policy_cannot_be_ambiguous(updates):
    with pytest.raises(ValidationError, match="explicit indefinite"):
        RetentionHorizonV1(**updates)


@pytest.mark.parametrize("kind", [TRAINING, INFERENCE])
def test_research_uses_cannot_supply_production_grants(manifest, kind):
    payload = _approval(manifest).content_payload.approval_payload.model_dump(
        mode="python"
    )
    payload["grants"] = tuple(
        item for item in payload["grants"] if item["grant"] != kind
    )
    with pytest.raises(ValidationError):
        TrainingHistoryApprovalPayloadV1.model_validate(payload)
    payload["grants"] = tuple(
        item.model_dump(mode="python")
        for item in _approval(manifest).content_payload.approval_payload.grants
    )
    payload["grants"][0]["grant"] = "INTERNAL_RESEARCH"
    with pytest.raises(ValidationError):
        TrainingHistoryApprovalPayloadV1.model_validate(payload)


def test_approval_requires_external_attestation_bound_to_exact_payload(manifest):
    approval = _approval(manifest)
    content = approval.content_payload
    for field, value in (("approver", "someone-else"), ("authority_sha256", "0" * 64)):
        changed = content.approval_payload.model_copy(update={field: value})
        with pytest.raises(ValidationError, match="attestation"):
            TrainingHistoryApprovalV1.freeze(
                content_payload=content.model_copy(
                    update={
                        "approval_payload": changed,
                        "approval_payload_hash": changed.approval_payload_hash,
                    }
                )
            )
    with pytest.raises(ValidationError, match="Extra inputs"):
        LocalReviewEvidenceV1.model_validate(
            {
                "evidence_reference": "local://test",
                "evidence_sha256": "0" * 64,
                "raw_evidence": b"not allowed",
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_count", 3),
        ("attempt_root", "0" * 64),
        ("code_revision", "different-code"),
        ("build_recipe", _ref("different-recipe")),
        ("attestation", _ref("different-attestation")),
    ],
)
def test_current_pilot_terminal_evidence_must_match_exactly(manifest, field, value):
    evidence = manifest.content_payload.technical_evidence.model_copy(
        update={field: value}
    )
    current = _current(manifest, BUILD, technical_evidence=evidence)
    with pytest.raises(ValueError, match="terminal evidence"):
        approval_active_for_build(
            approval=_approval(manifest), manifest=manifest, current=current
        )


@pytest.mark.parametrize(
    "component", ["FIXTURE", "MAPPING", "SEASON", "STATUS", "RESULT"]
)
def test_late_registered_correction_does_not_retroactively_change_operation(
    release, component
):
    correction = _correction(release, component)
    manifest = release.training_manifest
    assert _inference(release, _current(manifest, DECISION, corrections=(correction,)))
    with pytest.raises(ValueError, match="source-visible and locally registered"):
        _inference(
            release,
            _current(
                manifest, DECISION + timedelta(minutes=1), corrections=(correction,)
            ),
        )
    assert project_release_state(release, DECISION, ("target",), "production")


@pytest.mark.parametrize("kind", list(ProductionGrantKind))
def test_successor_is_per_grant_and_never_implicitly_replaces_release(release, kind):
    event = _successor(release, kind)
    current = _current(release.training_manifest, DECISION, successors=(event,))
    if kind == TRAINING:
        assert _inference(release, current)
    else:
        with pytest.raises(ValueError, match="superseded"):
            _inference(release, current)


def test_successors_forbid_backdating_forks_cycles_and_scope_changes(release):
    with pytest.raises(ValidationError, match="backdated"):
        _successor(release, TRAINING, effective_at_utc=DECISION - timedelta(seconds=1))
    other_scope = release.content_payload.scope.model_copy(
        update={"competition_id": "other-league"}
    )
    with pytest.raises(ValidationError, match="preserve exact"):
        _successor(release, TRAINING, successor_scope=other_scope)
    first = _successor(release, TRAINING)
    fork = _successor(release, INFERENCE, successor=_ref("fork"))
    with pytest.raises(ValidationError, match="fork"):
        _current(release.training_manifest, DECISION, successors=(first, fork))
    cycle = _successor(
        release,
        TRAINING,
        predecessor=first.content_payload.successor,
        successor=first.content_payload.predecessor,
    )
    with pytest.raises(ValidationError, match="cycle"):
        _current(release.training_manifest, DECISION, successors=(first, cycle))


@pytest.mark.parametrize(
    "targets,season,cutoff",
    [
        (("history-0",), "production", DECISION),
        (("target",), "pilot", DECISION),
        ((), "production", DECISION),
        (("target", "target"), "production", DECISION),
        (("target",), "production", BUILD),
    ],
)
def test_projection_never_filters_targets_or_changes_season(
    release, targets, season, cutoff
):
    with pytest.raises(ValueError):
        project_release_state(release, cutoff, targets, season)


@pytest.mark.parametrize(
    "field",
    ["teams", "training_facts", "training_data_hash", "production_target_season_id"],
)
def test_rehashed_tampered_core_or_release_is_rejected(release, field):
    core = release.content_payload.released_state_core
    content = core.content_payload
    if field == "teams":
        value = (
            content.teams[0].model_copy(update={"rating": Decimal("9999")}),
            *content.teams[1:],
        )
    elif field == "training_facts":
        value = tuple(reversed(content.training_facts))
    elif field == "training_data_hash":
        value = "0" * 64
    else:
        value = "wrong-season"
    with pytest.raises(ValueError):
        forged_core = ReleasedStateCoreV1.freeze(
            content_payload=content.model_copy(update={field: value})
        )
        ProductionQuantModelReleaseV1.freeze(
            content_payload=release.content_payload.model_copy(
                update={"released_state_core": forged_core}
            ),
            training_manifest=release.training_manifest,
            training_approval=release.training_approval,
        )


@pytest.mark.parametrize(
    "tamper", ["missing", "order", "nested_hash", "count", "season_root"]
)
def test_manifest_cannot_hide_or_reorder_or_forge_admitted_facts(manifest, tamper):
    history = manifest.content_payload.history
    if tamper == "missing":
        update = {"facts": history.facts[:-1]}
    elif tamper == "order":
        update = {"facts": tuple(reversed(history.facts))}
    elif tamper == "nested_hash":
        bad = history.facts[0].model_copy(update={"content_hash": "0" * 64})
        update = {"facts": (bad, *history.facts[1:])}
    elif tamper == "count":
        update = {"fact_count": 5}
    else:
        update = {"season_root": "0" * 64}
    with pytest.raises(ValueError):
        TrainingHistoryGraphV1.model_validate(history.model_copy(update=update))


def test_missing_fact_cannot_be_hidden_behind_a_valid_smaller_core(release):
    original = release.content_payload.released_state_core.content_payload
    results = tuple(
        elo_result_from_binding(item.content_payload.binding)
        for item in release.content_payload.release_facts[:-1]
    )
    state = EloThreeWayBaseline().rebuild_state(
        results, CUTOFF, target_season_id="production"
    )
    smaller = ReleasedStateCoreV1.freeze(
        content_payload=ReleasedStateCoreContentV1.from_state(
            state=state,
            training_cutoff_at_utc=CUTOFF,
            approved_facts_hash=original.approved_facts_hash,
        )
    )
    with pytest.raises(ValueError, match="context mismatch"):
        ProductionQuantModelReleaseV1.freeze(
            content_payload=release.content_payload.model_copy(
                update={"released_state_core": smaller}
            ),
            training_manifest=release.training_manifest,
            training_approval=release.training_approval,
        )


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("training_cutoff_at_utc", BUILD),
        ("training_cutoff_at_utc", APPROVAL_AT - timedelta(seconds=1)),
        ("training_cutoff_at_utc", datetime(2025, 1, 1, tzinfo=UTC)),
        ("persisted_at_utc", BUILD - timedelta(seconds=1)),
    ],
)
def test_build_rejects_newer_facts_and_invalid_authoritative_actual_times(
    manifest, parameter, value
):
    with pytest.raises(ValueError):
        _build(manifest, **{parameter: value})


@pytest.mark.parametrize(
    "boundary", ["core", "build", "current", "plan", "grant", "correction"]
)
def test_naive_times_fail_at_all_boundaries(release, boundary):
    naive = DECISION.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        if boundary == "core":
            core = release.content_payload.released_state_core
            ReleasedStateCoreV1.freeze(
                content_payload=core.content_payload.model_copy(
                    update={"training_cutoff_at_utc": naive}
                )
            )
        elif boundary == "build":
            _build(release.training_manifest, training_cutoff_at_utc=naive)
        elif boundary == "current":
            _inference(release, _current(release.training_manifest, naive))
        elif boundary == "plan":
            _plan(release, sealed_at_utc=naive)
        elif boundary == "grant":
            _approval(
                release.training_manifest, {INFERENCE: {"effective_at_utc": naive}}
            )
        else:
            _correction(release, "RESULT", registered_at_utc=naive)


def test_model_copy_nested_config_cannot_bypass_fixed_parameters():
    copied = EloBaselineConfig().model_copy(update={"k_factor": Decimal(21)})
    with pytest.raises(ValidationError, match="fixed Elo config"):
        FixedProductionModelV1(config=copied)
    with pytest.raises(ValidationError, match="minimum_prior_matches"):
        FixedProductionModelV1(
            config=copied.model_copy(update={"minimum_prior_matches": -1})
        )
    assert FixedProductionModelV1().config.config_hash == PRODUCTION_CONFIG_HASH


def test_target_plan_must_be_predeclared_and_pin_exact_release(release):
    with pytest.raises(ValueError, match="before future decision"):
        _plan(release, sealed_at_utc=DECISION, persisted_at_utc=DECISION)
    for updates in (
        {"release": _ref("wrong-release")},
        {"production_target_season_id": "pilot"},
        {"sealed_at_utc": BUILD, "persisted_at_utc": BUILD},
    ):
        with pytest.raises(ValueError, match="target plan"):
            _inference(release, plan=_plan(release, **updates))
    target = (
        _plan(release)
        .content_payload.targets[0]
        .model_copy(update={"match_id": "history-0"})
    )
    with pytest.raises(ValueError, match="target intersection"):
        _inference(release, plan=_plan(release, targets=(target,)))
    with pytest.raises(ValidationError, match="selection_rule"):
        _plan(release, selection_rule="SELECT_BY_EV")


def _packet_bundle(release):
    plan = _plan(release)
    state = project_release_state(release, DECISION, ("target",), "production")
    baseline = EloThreeWayBaseline()
    artifact = freeze_elo_model_state(
        analysis_run_id="test-run",
        baseline=baseline,
        state=state,
        generated_at_utc=DECISION + timedelta(minutes=1),
    )
    fixture = Match(
        match_id="target",
        competition_id="bundesliga",
        home_team_id="alpha",
        away_team_id="bravo",
        kickoff_at_utc=DECISION + timedelta(days=1),
        available_at_utc=DECISION - timedelta(hours=1),
    )
    manifest_json = build_model_input_manifest_json(
        competitions=(),
        teams=(),
        matches=(fixture,),
        mappings=(),
        market_snapshots=(),
        sporttery_snapshots=(),
        model_states=(artifact,),
    )
    prediction = baseline.predict_from_state(
        EloPredictionRequest(
            match_id="target",
            season_id="production",
            home_team_id="alpha",
            away_team_id="bravo",
            kickoff_at_utc=DECISION + timedelta(days=1),
            cutoff_at_utc=DECISION,
        ),
        state,
    )
    evaluation = freeze_elo_evaluation(
        analysis_run_id="test-run",
        model_state=artifact,
        prediction=prediction,
        market=MarketKey(market_type=MarketType.THREE_WAY),
        evaluated_at_utc=DECISION + timedelta(minutes=1),
    )
    quant = project_available_model_quant(model_state=artifact, evaluation=evaluation)
    packet_state = PacketQuantModelStateV3(
        **artifact.model_dump(exclude={"config_json", "state_json", "training_facts"}),
        training_fact_count=6,
        training_match_ids=state.training_match_ids,
        training_result_ids=state.training_result_ids,
    )
    source = AnalysisPacketSourceV3(
        analysis_run=AnalysisPacketRunV3(
            analysis_run_id="test-run",
            as_of_at_utc=DECISION,
            started_at_utc=DECISION + timedelta(seconds=1),
            completed_at_utc=DECISION + timedelta(minutes=2),
            pipeline_version="MVP_V1",
            code_revision="test-run-code",
            input_manifest_version="MVP_INPUT_MANIFEST_V3",
            input_manifest_hash=hashlib.sha256(manifest_json.encode()).hexdigest(),
        ),
        quant_model_states=(packet_state,),
        matches=(
            AnalysisPacketMatchSourceV3(
                match_id="target",
                competition_id="bundesliga",
                competition_name="Test League",
                home_team_id="alpha",
                home_team_name="Alpha",
                away_team_id="bravo",
                away_team_name="Bravo",
                kickoff_at_utc=DECISION + timedelta(days=1),
                market_key="THREE_WAY",
                context_hash="test-context",
                p_market=PacketMarketPrediction(
                    prediction_id="test-market",
                    input_snapshot_ids=("test-odds",),
                    probabilities=ThreeWayProbability(
                        home_win=Decimal("0.4"),
                        draw=Decimal("0.3"),
                        away_win=Decimal("0.3"),
                    ),
                ),
                p_quant=PacketModelQuantLineageV3(
                    status=evaluation.status,
                    prediction=quant,
                    evaluation=PacketQuantModelEvaluationV3.model_validate(
                        evaluation.model_dump(exclude={"output_json"})
                    ),
                ),
                review_context=MatchReviewContext(
                    data_quality=PacketDataQuality(
                        status="INSUFFICIENT", score=Decimal(0)
                    )
                ),
            ),
        ),
    )
    packet = build_analysis_packet_v3(source, DECISION + timedelta(minutes=3))
    common = dict(
        packet=packet,
        input_manifest_json=manifest_json,
        model_state=artifact,
        release=release,
        plan=plan,
        operation_start=_current(
            release.training_manifest, DECISION + timedelta(minutes=2)
        ),
        operation_completion=_current(
            release.training_manifest, DECISION + timedelta(minutes=4)
        ),
    )
    audit = freeze_approved_training_history_audit(
        **common,
        state_retention_horizon=_horizon(),
        audit_retention_horizon=_horizon(),
        generated_at_utc=DECISION + timedelta(minutes=3),
    )
    return audit, common


@pytest.fixture(scope="module")
def bundle(release):
    return _packet_bundle(release)


def test_audit_bundle_preserves_v3_and_binds_full_provenance(bundle):
    audit, common = bundle
    before = canonical_json(common["packet"].model_dump(mode="json"))
    validate_approved_training_history_audit(audit=audit, **common)
    after = canonical_json(common["packet"].model_dump(mode="json"))
    assert before == after
    assert "APPROVED_TRAINING_HISTORY" not in after
    assert audit.content_payload.decision_data_mode == "LIVE_STRICT"
    assert audit.content_payload.model_training_source_mode == "SOURCE_TIME_RESEARCH"
    assert audit.content_payload.model_training_use_class == "APPROVED_TRAINING_HISTORY"
    assert audit.content_payload.retrospective_source_facts_present is True
    assert audit.content_payload.technical_evidence.attempt_count == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("analysis_run_id", "wrong-run"),
        ("input_manifest_hash", "0" * 64),
        ("quant_model_state_id", "wrong-state"),
        ("state_hash", "0" * 64),
        ("state_payload_hash", "0" * 64),
        ("training_data_hash", "0" * 64),
        ("released_state_core_hash", "0" * 64),
        ("approved_facts_hash", "0" * 64),
        ("packet", _ref("wrong-packet")),
        ("release", _ref("wrong-release")),
        ("approval", _ref("wrong-approval")),
        ("manifest", _ref("wrong-manifest")),
        ("target_acceptance_plan", _ref("wrong-plan")),
        ("run_code_revision", "wrong-code"),
    ],
)
def test_even_resealed_audit_mismatches_are_rejected(bundle, field, value):
    audit, common = bundle
    bad = ApprovedTrainingHistoryAuditV1.freeze(
        content_payload=audit.content_payload.model_copy(update={field: value})
    )
    with pytest.raises(ValueError, match="audit .*mismatch"):
        validate_approved_training_history_audit(audit=bad, **common)


def test_audit_checks_packet_hash_manifest_bytes_and_actual_completion_revocation(
    bundle,
):
    audit, common = bundle
    packet = common["packet"].model_copy(update={"packet_hash": "0" * 64})
    adjusted = ApprovedTrainingHistoryAuditV1.freeze(
        content_payload=audit.content_payload.model_copy(
            update={
                "packet": ReleaseArtifactRefV1(
                    artifact_id=packet.packet_id, content_hash=packet.packet_hash
                ),
            }
        )
    )
    with pytest.raises(ValueError, match="V3 packet/context"):
        validate_approved_training_history_audit(
            audit=adjusted, **common | {"packet": packet}
        )
    with pytest.raises(ValueError, match="input manifest"):
        validate_approved_training_history_audit(
            audit=audit,
            **common | {"input_manifest_json": common["input_manifest_json"] + " "},
        )
    release = common["release"]
    revocation = _revocation(
        release,
        INFERENCE,
        recorded=DECISION + timedelta(minutes=3),
        effective=DECISION + timedelta(minutes=3),
    )
    completion = _current(
        release.training_manifest,
        DECISION + timedelta(minutes=4),
        revocations=(revocation,),
    )
    with pytest.raises(ValueError, match="revoked"):
        validate_approved_training_history_audit(
            audit=audit, **common | {"operation_completion": completion}
        )


def test_all_new_seals_exclude_self_identity_and_reject_hash_and_id_tampering(
    release, bundle
):
    artifacts = (
        release,
        release.training_manifest,
        release.training_approval,
        release.content_payload.released_state_core,
        release.content_payload.build_start_authorization,
        release.content_payload.build_completion_authorization,
        release.content_payload.release_facts[0],
        release.training_manifest.content_payload.history.training_window,
        _plan(release),
        bundle[0],
        _revocation(release, INFERENCE),
        _successor(release, INFERENCE),
        _correction(release, "RESULT"),
    )
    for artifact in artifacts:
        content = artifact.content_payload.model_dump(mode="python")
        assert "artifact_id" not in content and "content_hash" not in content
        assert "raw_evidence" not in content and "serialized_json" not in content
        assert artifact.content_hash == tagged_canonical_sha256(
            artifact.schema_version, artifact.content_payload
        )
        assert artifact.artifact_id == stable_id(
            artifact.schema_version, artifact.content_hash
        )
        for field, value in (("content_hash", "0" * 64), ("artifact_id", "wrong-id")):
            with pytest.raises(ValueError, match="hash mismatch|ID mismatch"):
                revalidate(artifact.model_copy(update={field: value}))


def test_hash_domain_tag_nul_and_source_time_math_hash_remain_separate():
    payload = {"b": 2, "a": "test"}
    assert (
        tagged_canonical_sha256("TEST_RELEASE_V1", payload)
        == hashlib.sha256(b'TEST_RELEASE_V1\0{"a":"test","b":2}').hexdigest()
    )


def test_observed_build_revocation_cannot_disappear_before_completion(
    manifest, release
):
    event = _revocation(release, TRAINING, recorded=BUILD, effective=DECISION)
    with pytest.raises(ValueError, match="observations cannot disappear"):
        _build(manifest, build_start=_current(manifest, BUILD, revocations=(event,)))


def _repacket(packet, **updates):
    values = packet.model_dump(
        mode="python",
        exclude={"schema_version", "packet_id", "packet_hash", "generated_at_utc"},
    )
    for match in values["matches"]:
        match.pop("review_context_id")
        match.pop("review_context_hash")
    values.update(updates)
    return build_analysis_packet_v3(
        AnalysisPacketSourceV3.model_validate(values), packet.generated_at_utc
    )


def _audit_for_packet(audit, packet):
    return ApprovedTrainingHistoryAuditV1.freeze(
        content_payload=audit.content_payload.model_copy(
            update={
                "packet": ReleaseArtifactRefV1(
                    artifact_id=packet.packet_id, content_hash=packet.packet_hash
                ),
                "input_manifest_hash": packet.analysis_run.input_manifest_hash,
            }
        )
    )


def test_rehashed_packet_with_forged_model_evaluation_is_not_an_approved_prediction(
    bundle,
):
    audit, common = bundle
    matches = common["packet"].model_dump(mode="python")["matches"]
    for match in matches:
        match.pop("review_context_id")
        match.pop("review_context_hash")
        match["p_quant"]["evaluation"]["output_hash"] = "0" * 64
    packet = _repacket(common["packet"], matches=matches)
    with pytest.raises(ValueError, match="quant evaluation does not replay"):
        validate_approved_training_history_audit(
            audit=_audit_for_packet(audit, packet), **common | {"packet": packet}
        )


@pytest.mark.parametrize("tamper", ["missing", "identity", "future", "lineage"])
def test_rehashed_input_manifest_mismatches_fail(bundle, tamper):
    audit, common = bundle
    manifest = json.loads(common["input_manifest_json"])
    if tamper == "missing":
        manifest["matches"] = []
    elif tamper == "identity":
        manifest["matches"][0]["home_team_id"] = "wrong-team"
    elif tamper == "future":
        manifest["matches"][0]["available_at_utc"] = (
            DECISION + timedelta(seconds=1)
        ).isoformat()
    else:
        manifest["quant_model_states"][0]["training_facts"][0]["match_id"] = (
            "wrong-match"
        )
    manifest_json = canonical_json(manifest)
    run = common["packet"].analysis_run.model_copy(
        update={
            "input_manifest_hash": hashlib.sha256(manifest_json.encode()).hexdigest()
        }
    )
    packet = _repacket(common["packet"], analysis_run=run)
    with pytest.raises(ValueError, match="V3 input manifest"):
        validate_approved_training_history_audit(
            audit=_audit_for_packet(audit, packet),
            **common | {"packet": packet, "input_manifest_json": manifest_json},
        )


@pytest.mark.parametrize(
    "tamper", ["phase", "time", "reference", "new-pilot", "missing-facts"]
)
def test_release_rejects_tampered_captured_build_authorization_or_facts(
    release, tamper
):
    content = release.content_payload
    capture = content.build_start_authorization
    payload = capture.content_payload
    with pytest.raises(ValueError):
        if tamper == "phase":
            payload = payload.model_copy(update={"phase": "BUILD_COMPLETION"})
        elif tamper == "time":
            payload = payload.model_copy(
                update={
                    "current": payload.current.model_copy(
                        update={"actual_at_utc": DECISION}
                    )
                }
            )
        elif tamper == "reference":
            payload = payload.model_copy(update={"approval": _ref("other-approval")})
        elif tamper == "new-pilot":
            evidence = payload.current.technical_evidence.model_copy(
                update={"attempt_count": 3}
            )
            payload = payload.model_copy(
                update={
                    "current": payload.current.model_copy(
                        update={"technical_evidence": evidence}
                    )
                }
            )
        capture = type(capture).freeze(content_payload=payload)
        updates = {"build_start_authorization": capture}
        if tamper == "missing-facts":
            updates["release_facts"] = content.release_facts[:-1]
        forged = ProductionQuantModelReleaseV1.freeze(
            content_payload=content.model_copy(update=updates),
            training_manifest=release.training_manifest,
            training_approval=release.training_approval,
        )
        project_release_state(forged, DECISION, ("target",), "production")


def test_released_core_v1_hash_and_legacy_math_golden(release):
    core = release.content_payload.released_state_core
    assert core.content_hash == (
        "024baf9c2671047b41678ba987bfdd68b2307ee18a1ff44682914cb39c825b62"
    )
    assert core.content_payload.training_data_hash == (
        "d2f08925116e3a9e0cca6f7758604002c6736847961fb5fd9d95e22211e2a639"
    )
    assert core.content_payload.approved_facts_hash == (
        "f3cc531086a5c3af01a9e1bf86afd7a25a06bf5fd27378f92f5e3d8917b77f20"
    )
    assert [
        (item.team_id, str(item.rating), item.prior_matches)
        for item in core.content_payload.teams
    ] == [
        ("alpha", "1512.914200733522", 6),
        ("bravo", "1487.085799266478", 6),
    ]


@pytest.mark.parametrize(
    "field", ["technical_evidence", "source_summaries", "season_summaries"]
)
def test_audit_pilot_and_ordered_provenance_summaries_cannot_drift(bundle, field):
    audit, common = bundle
    content = audit.content_payload
    if field == "technical_evidence":
        value = content.technical_evidence.model_copy(
            update={"attestation": _ref("other-pilot")}
        )
    elif field == "source_summaries":
        value = (
            content.source_summaries[0].model_copy(update={"terms_sha256": "0" * 64}),
        )
    else:
        value = tuple(reversed(content.season_summaries))
    bad = ApprovedTrainingHistoryAuditV1.freeze(
        content_payload=content.model_copy(update={field: value})
    )
    with pytest.raises(ValueError, match="audit .*mismatch"):
        validate_approved_training_history_audit(audit=bad, **common)


def test_audit_rejects_legacy_state_season_projection_mismatch(bundle):
    audit, common = bundle
    wrong_season = common["model_state"].model_copy(update={"season_id": "pilot"})
    with pytest.raises(ValueError, match="run-scoped model state"):
        validate_approved_training_history_audit(
            audit=audit, **common | {"model_state": wrong_season}
        )


def test_audit_rejects_resealed_packet_with_reordered_training_lineage(bundle):
    audit, common = bundle
    states = common["packet"].model_dump(mode="python")["quant_model_states"]
    states[0]["training_match_ids"] = tuple(reversed(states[0]["training_match_ids"]))
    packet = _repacket(common["packet"], quant_model_states=states)
    with pytest.raises(ValueError, match="ordered training lineage"):
        validate_approved_training_history_audit(
            audit=_audit_for_packet(audit, packet), **common | {"packet": packet}
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"retention_rule": None},
        {"retention": None},
        {"retention": _horizon(None), "expires_at_utc": END},
        {"retention": _horizon(END), "expires_at_utc": END - timedelta(days=1)},
        {"retention": _horizon(APPROVAL_AT - timedelta(days=1))},
    ],
)
def test_retention_grant_intrinsic_semantics_fail_closed(manifest, updates):
    with pytest.raises(ValueError):
        _approval(manifest, {STATE: updates})


def test_intrinsic_source_rights_and_approval_scope_must_match(manifest):
    approval = _approval(manifest)
    payload = approval.content_payload.approval_payload
    incorrect = payload.source_rights[0].model_copy(update={"terms_sha256": "0" * 64})
    changed = _seal_approval(payload.model_copy(update={"source_rights": (incorrect,)}))
    with pytest.raises(ValueError, match="manifest/source/pilot"):
        approval_active_for_build(
            approval=changed, manifest=manifest, current=_current(manifest, BUILD)
        )


def test_known_correction_predecessor_cannot_lie_about_hash_or_source_clock(release):
    event = _correction(release, "MAPPING")
    for update in (
        {
            "predecessor": event.content_payload.predecessor.model_copy(
                update={"content_hash": "0" * 64}
            )
        },
        {
            "predecessor_source_available_at_utc": event.content_payload.predecessor_source_available_at_utc
            - timedelta(hours=1)
        },
    ):
        bad = SourceCorrectionV1.freeze(
            content_payload=event.content_payload.model_copy(update=update)
        )
        with pytest.raises(ValueError, match="predecessor hash/source/actual"):
            _inference(
                release,
                _current(
                    release.training_manifest,
                    DECISION + timedelta(minutes=1),
                    corrections=(bad,),
                ),
            )
