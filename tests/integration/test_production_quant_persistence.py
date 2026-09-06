"""Contract fixtures only, never real source rights or production activation.

Admission bytes/identities are genuinely persisted through the admission lane.
The isolated bridge double is explicitly NOT a real integrity pilot. Approval
rows are seeded only to test reads/build persistence while the public approval
writer remains blocked by the documented V1 timestamp-attestation conflict.
"""

from contextlib import contextmanager
from datetime import timedelta, datetime, timezone
from io import StringIO
import json
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from . import test_training_admission_persistence as admission_fixtures
from .test_training_admission_persistence import (
    admit,
    prepare,
    review_document,
    write_evidence,
)

from football_system.application.production_release import (
    prepare_training_history,
    project_release_state,
)
from football_system.domain.production_release import (
    TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
    EloTrainingWindowContentV1,
    EloTrainingWindowV1,
    GrantRevocationContentV1,
    GrantRevocationV1,
    ProductionGrantKind,
    ProductionGrantV1,
    ProductionTargetV1,
    ProviderSeasonRefV1,
    ReleaseArtifactRefV1,
    RetentionHorizonV1,
    SourceCorrectionContentV1,
    SourceCorrectionV1,
    TechnicalEvidenceRefsV1,
    TrainingHistoryApprovalContentV1,
    TrainingHistoryApprovalPayloadV1,
    TrainingHistoryApprovalV1,
    TrainingSeasonV1,
    release_active_for_inference,
    source_rights_refs,
)
from football_system.domain.training_admission import (
    LocalReviewerAttestationContentV1,
    LocalReviewerAttestationV1,
    tagged_canonical_sha256,
)
from football_system.domain.archive import canonical_json
from football_system.infrastructure.database import (
    production_quant_repository as persistence,
)
from football_system.infrastructure.database.models import (
    Base,
    MatchRecord,
    CanonicalMatchIdentityRecord,
)
from football_system.infrastructure.database.production_quant_repository import (
    ApprovalRecordingContractConflict,
    SqlAlchemyProductionQuantRepository,
    production_build_recipe_v1,
)
from football_system.infrastructure.database.production_quant_schema import (
    PRODUCTION_QUANT_TABLES,
    production_quant_trigger_sql_v1,
)
from football_system.infrastructure.database.session import create_database_engine
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
)

UTC = timezone.utc
TRAINING = ProductionGrantKind.PRODUCTION_MODEL_TRAINING
INFERENCE = ProductionGrantKind.PRODUCTION_MODEL_INFERENCE
STATE = ProductionGrantKind.DERIVED_MODEL_STATE_RETENTION
AUDIT = ProductionGrantKind.AUDIT_HASH_RETENTION
END = datetime(2029, 1, 1, tzinfo=UTC)
lane = admission_fixtures.lane


def ref(name):
    return ReleaseArtifactRefV1(
        artifact_id=name,
        content_hash=tagged_canonical_sha256("TEST_ONLY_REFERENCE_V1", {"name": name}),
    )


def horizon(at=END):
    return RetentionHorizonV1(indefinite=at is None, retain_until_at_utc=at)


class BridgeDouble:
    """Trusted-port contract double; never presented as actual pilot evidence."""

    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []
        self.synthetic = False
        self.fail = False

    def technical_evidence(self, attestation_id, history, *, session=None):
        assert session is not None
        assert session.in_transaction()
        self.calls.append((attestation_id, history, session))
        if self.synthetic:
            raise ValueError(
                "synthetic pilot cannot supply production technical evidence"
            )
        if self.fail:
            raise ValueError("actual terminal graph verification failed")
        assert (
            session.scalar(text("SELECT COUNT(*) FROM training_fact_admissions")) == 1
        )
        return self.evidence


@pytest.fixture
def production(lane, monkeypatch):
    # Other agents edit installed package files concurrently. Isolate this port
    # fixture's revision; dedicated tests still assert changed revisions fail.
    monkeypatch.setattr(
        persistence, "_code_revision", lambda: "package:test-only-fixed-revision"
    )
    prepare(lane, count=2)
    admission = admit(lane)
    window = EloTrainingWindowV1.freeze(
        content_payload=EloTrainingWindowContentV1(
            competition_id="league",
            seasons=tuple(
                TrainingSeasonV1(
                    season_sequence=index,
                    season_id=season,
                    role=role,
                    provider_seasons=(
                        ProviderSeasonRefV1(
                            source_id="source",
                            provider_code="PROVIDER",
                            provider_competition_id="p-league",
                            provider_season_id=provider_season,
                        ),
                    ),
                )
                for index, (season, provider_season, role) in enumerate(
                    (
                        ("2024/25", "p-2024-25", "PILOT_TARGET"),
                        ("production", "p-production", "PRODUCTION_TARGET"),
                    )
                )
            ),
        )
    )
    history = prepare_training_history(
        admissions=(admission,),
        training_window=window,
        integrity_pilot_scope_id="test-scope",
    )
    evidence = TechnicalEvidenceRefsV1(
        integrity_pilot_scope_id="test-scope",
        integrity_pilot_series_id="test-series",
        scope=history.scope,
        plan=ref("test-plan"),
        summary=ref("test-summary"),
        attestation=ref("test-attestation"),
        report=ref("test-report"),
        attempt_count=2,
        attempt_root="a" * 64,
        source_root=history.source_root,
        season_root=history.season_root,
        approved_facts_hash=history.approved_facts_hash,
        training_data_hash=history.training_data_hash,
        terminal_state_core_hash="b" * 64,
        build_recipe=production_build_recipe_v1("test-recipe"),
        code_revision=persistence._code_revision(),
        plan_sealed_at_utc=lane.clock(),
        actual_started_at_utc=lane.clock(),
        actual_completed_at_utc=lane.clock(),
        attestation_persisted_at_utc=lane.clock(),
    )
    bridge = BridgeDouble(evidence)
    repo = SqlAlchemyProductionQuantRepository(
        lane.sessions,
        admission_repository=lane.repo,
        pilot_repository=bridge,
        clock=lane.clock,
        operator_id="operator",
    )
    authority = write_evidence(
        lane.root,
        "production-authority.json",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by="governance",
            authorized_reviewer="reviewer",
            source_ids=["source"],
            attested_schema_versions=[
                TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
                "PRODUCTION_REVOCATION_REQUEST_V1",
            ],
            effective_at_utc="2026-01-01T00:00:00Z",
            expires_at_utc="2031-01-01T00:00:00Z",
        ),
    )
    lane.repo.evidence.trusted_authorities[authority.evidence_reference] = (
        authority.evidence_sha256
    )
    return SimpleNamespace(
        lane=lane,
        admission=admission,
        window=window,
        history=history,
        bridge=bridge,
        repo=repo,
        authority=authority,
    )


def manifest(context, key="manifest"):
    return context.repo.create_manifest(
        key,
        (context.admission.training_fact_admission_id,),
        context.window,
        "test-scope",
        "test-attestation",
    )


def seed_approval_fixture(context, value=None, *, updates=None, predecessor=None):
    """Test-only existing-row fixture, NOT a usable approval-writing workflow."""
    value = value or manifest(context)
    approved, persisted = context.lane.clock(), context.lane.clock()
    grants = []
    for kind in sorted(ProductionGrantKind):
        settings = dict(
            grant=kind,
            effective_at_utc=approved,
            expires_at_utc=END,
            retention_rule="test retention" if kind in (STATE, AUDIT) else None,
            retention=horizon() if kind in (STATE, AUDIT) else None,
        )
        settings.update((updates or {}).get(kind, {}))
        grants.append(ProductionGrantV1(**settings))
    payload = TrainingHistoryApprovalPayloadV1(
        manifest=value.reference(),
        scope=value.content_payload.history.scope,
        source_rights=source_rights_refs(value.content_payload.history),
        technical_evidence=value.content_payload.technical_evidence,
        build_recipe=value.content_payload.technical_evidence.build_recipe,
        code_revision=value.content_payload.technical_evidence.code_revision,
        approver="reviewer",
        authority_reference=context.authority.evidence_reference,
        authority_sha256=context.authority.evidence_sha256,
        grants=tuple(grants),
        retention_compatibility="APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE",
        approved_at_utc=approved,
        persisted_at_utc=persisted,
        supersedes_approval=None if predecessor is None else predecessor.reference(),
        supersession_effective_at_utc=None if predecessor is None else persisted,
        superseded_grants=() if predecessor is None else (TRAINING,),
    )
    review = write_evidence(
        context.lane.root,
        f"approval-{persisted.timestamp()}.json",
        review_document(
            schema=TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
            digest=payload.approval_payload_hash,
            at=approved,
        ),
    )
    attestation = LocalReviewerAttestationV1.freeze(
        content_payload=LocalReviewerAttestationContentV1(
            attested_schema_version=TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
            attested_payload_hash=payload.approval_payload_hash,
            authorized_reviewer="reviewer",
            reviewer_authority_reference=context.authority.evidence_reference,
            authority_sha256=context.authority.evidence_sha256,
            reviewed_at_utc=approved,
            evidence=review,
        )
    )
    approval = TrainingHistoryApprovalV1.freeze(
        content_payload=TrainingHistoryApprovalContentV1(
            approval_payload=payload,
            approval_payload_hash=payload.approval_payload_hash,
            reviewer_attestation=attestation,
        )
    )
    request = persistence._request(
        "record_approval",
        f"seed-{persisted.timestamp()}",
        "operator",
        approval_payload=payload,
        reviewer_attestation=attestation,
    )
    with context.lane.sessions.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        for name, row in persistence._approval_grants(approval):
            persistence._insert(session, name, row)
        if predecessor is not None:
            persistence._insert(
                session,
                "training_history_successors",
                persistence._successor_row(
                    persistence._successor(predecessor, approval)
                ),
            )
        persistence._insert(
            session,
            "training_history_approval_events",
            persistence._approval_row(approval, request),
        )
    return approval


def build(context, approval=None, *, key="release", retention=None):
    approval = approval or seed_approval_fixture(context)
    cutoff = context.lane.clock()
    return context.repo.build_release(
        key,
        approval.artifact_id,
        cutoff,
        retention or horizon(),
        retention or horizon(),
    )


def target_plan(context, release):
    at = context.lane.clock()
    decision, kickoff = at + timedelta(days=1), at + timedelta(days=2)
    with context.lane.sessions.begin() as session:
        session.add(
            MatchRecord(
                internal_match_id="future",
                competition_id="league",
                home_team_id="home",
                away_team_id="away",
                kickoff_at_utc=kickoff,
                status="SCHEDULED",
                available_at_utc=at,
                created_at_utc=at,
            )
        )
        session.flush()
        session.add(
            CanonicalMatchIdentityRecord(
                internal_match_id="future",
                season="production",
                competition_type="LEAGUE",
                available_at_utc=at,
            )
        )
    targets = (
        ProductionTargetV1(
            match_id="future",
            home_team_id="home",
            away_team_id="away",
            kickoff_at_utc=kickoff,
        ),
    )
    return context.repo.seal_target_plan(
        "target",
        release.artifact_id,
        targets,
        kickoff - timedelta(hours=1),
        kickoff + timedelta(hours=1),
        decision,
        "KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1",
    )


def revoke(
    context,
    approval,
    *,
    release=None,
    kind=INFERENCE,
    effective_at=None,
    key="revocation",
):
    intent = context.repo.prepare_revocation_request(
        approval.artifact_id,
        release_id=None if release is None else release.artifact_id,
        affected_grants=(kind,),
        actor="reviewer",
        reviewer_authority=context.authority,
        reason="Test-only revocation.",
        effective_at_utc=effective_at,
    )
    review = write_evidence(
        context.lane.root,
        f"{key}.json",
        review_document(
            schema=intent.schema_version,
            digest=intent.request_hash,
            at=context.lane.clock(),
        ),
    )
    event = context.repo.record_revocation(
        key, revocation_request=intent, review=review
    )
    return event, intent, review


def counts(context):
    with context.lane.engine.connect() as connection:
        return {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM {name}"))
            for name in PRODUCTION_QUANT_TABLES
        }


def test_manifest_actual_admission_graph_and_exact_retry_without_new_timestamps(
    production,
):
    before = len(production.lane.clock.calls)
    value = manifest(production)
    assert value.content_payload.created_at_utc == production.lane.clock.calls[before]
    assert (
        value.content_payload.persisted_at_utc
        == production.lane.clock.calls[before + 1]
    )
    assert value.content_payload.history.facts == production.history.facts
    assert production.repo.load_manifest(value.artifact_id) == value
    calls = len(production.lane.clock.calls)
    assert manifest(production) == value
    assert len(production.lane.clock.calls) == calls
    assert counts(production)["training_history_facts"] == 2
    assert production.bridge.calls
    with pytest.raises(ValueError, match="retry request"):
        production.repo.create_manifest(
            "manifest",
            (production.admission.training_fact_admission_id,),
            production.window,
            "changed-scope",
            "test-attestation",
        )


def test_approval_writer_stops_instead_of_rebinding_a_prior_review(production):
    value = manifest(production)
    before = counts(production)
    with pytest.raises(
        ApprovalRecordingContractConflict, match="future|persisted_at_utc"
    ):
        production.repo.record_approval(
            "approval",
            value.artifact_id,
            grants=(),
            review=production.authority,
            reviewer_authority=production.authority,
        )
    assert counts(production) == before
    assert counts(production)["training_history_approval_events"] == 0


def test_seeded_reviewed_approval_build_and_projection_round_trip(production):
    approval = seed_approval_fixture(production)
    assert production.repo.load_approval(approval.artifact_id) == approval
    initial = len(production.lane.clock.calls)
    release = build(production, approval)
    times = production.lane.clock.calls[initial:]
    content = release.content_payload
    assert content.training_cutoff_at_utc == times[0]
    assert content.build_started_at_utc == times[1]
    assert content.build_completed_at_utc == times[2]
    assert content.persisted_at_utc == times[3]
    assert times[4] > content.persisted_at_utc
    assert production.repo.load_release(release.artifact_id) == release
    calls = len(production.lane.clock.calls)
    assert (
        production.repo.build_release(
            "release",
            approval.artifact_id,
            content.training_cutoff_at_utc,
            horizon(),
            horizon(),
        )
        == release
    )
    assert len(production.lane.clock.calls) == calls
    with pytest.raises(ValueError, match="retry request"):
        production.repo.build_release(
            "release",
            approval.artifact_id,
            content.training_cutoff_at_utc + timedelta(seconds=1),
            horizon(),
            horizon(),
        )
    plan = target_plan(production, release)
    assert production.repo.load_target_plan(plan.artifact_id) == plan
    state = project_release_state(
        release, plan.content_payload.decision_as_of_at_utc, ("future",), "production"
    )
    assert tuple(item.prior_matches for item in state.teams) == (2, 2)


def test_target_exact_retry_preserves_all_times(production):
    release = build(production)
    plan = target_plan(production, release)
    content = plan.content_payload
    calls = len(production.lane.clock.calls)
    assert (
        production.repo.seal_target_plan(
            "target",
            release.artifact_id,
            content.targets,
            content.kickoff_window_start_at_utc,
            content.kickoff_window_end_at_utc,
            content.decision_as_of_at_utc,
            content.selection_rule,
        )
        == plan
    )
    assert len(production.lane.clock.calls) == calls
    with pytest.raises(ValueError, match="retry request"):
        production.repo.seal_target_plan(
            "target",
            release.artifact_id,
            content.targets,
            content.kickoff_window_start_at_utc,
            content.kickoff_window_end_at_utc,
            content.decision_as_of_at_utc + timedelta(seconds=1),
            content.selection_rule,
        )


@pytest.mark.parametrize(
    "filename",
    [
        "fixture.json",
        "result.json",
        "scope.json",
        "rights-review.json",
        "terms.txt",
        "review-0.json",
    ],
)
def test_load_manifest_and_retry_read_actual_local_evidence_again(production, filename):
    value = manifest(production)
    (production.lane.root / filename).write_bytes(b"changed bytes")
    with pytest.raises(ValueError):
        production.repo.load_manifest(value.artifact_id)
    with pytest.raises(ValueError):
        manifest(production)


def test_all_load_paths_require_real_terminal_pilot_bridge_and_current_recipe(
    production, monkeypatch
):
    release = build(production)
    plan = target_plan(production, release)
    production.bridge.fail = True
    for load, key in (
        (production.repo.load_manifest, release.content_payload.manifest.artifact_id),
        (production.repo.load_approval, release.content_payload.approval.artifact_id),
        (production.repo.load_release, release.artifact_id),
        (production.repo.load_target_plan, plan.artifact_id),
    ):
        with pytest.raises(ValueError, match="terminal graph"):
            load(key)
    production.bridge.fail = False
    monkeypatch.setattr(persistence, "_code_revision", lambda: "package:changed")
    with pytest.raises(ValueError, match="code revision/build recipe"):
        production.repo.load_release(release.artifact_id)


def test_synthetic_pilot_cannot_create_a_production_manifest(production):
    production.bridge.synthetic = True
    with pytest.raises(ValueError, match="synthetic pilot"):
        manifest(production)
    assert not any(counts(production).values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_count", 3),
        ("attempt_root", "0" * 64),
        ("build_recipe", ref("wrong-recipe")),
    ],
)
def test_changed_terminal_claims_are_not_accepted_as_existing_graph(
    production, field, value
):
    original = manifest(production)
    production.bridge.evidence = production.bridge.evidence.model_copy(
        update={field: value}
    )
    with pytest.raises(ValueError, match="terminal pilot|recipe"):
        production.repo.load_manifest(original.artifact_id)


def test_late_manifest_child_failure_rolls_back_every_production_row(
    production, monkeypatch
):
    original = persistence._manifest_children

    def broken(value):
        for name, row in original(value):
            if name == "training_history_facts" and row["fact_sequence"] == 1:
                row["match_result_id"] = "missing-result"
            yield name, row

    monkeypatch.setattr(persistence, "_manifest_children", broken)
    with pytest.raises(IntegrityError):
        manifest(production)
    assert not any(counts(production).values())


def test_build_failure_inside_commit_leaves_no_partial_release(production, monkeypatch):
    approval = seed_approval_fixture(production)
    original = production.repo._authorization
    calls = 0

    def fail_last(session, a, m, at):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise ValueError("commit boundary evidence changed")
        return original(session, a, m, at)

    monkeypatch.setattr(production.repo, "_authorization", fail_last)
    with pytest.raises(ValueError, match="commit boundary"):
        build(production, approval)
    assert counts(production)["production_quant_model_releases"] == 0
    assert counts(production)["production_quant_model_release_facts"] == 0


@pytest.mark.parametrize("boundary", ["completion", "commit"])
def test_training_expiring_during_build_fails_without_partial_release(
    production, boundary
):
    end = production.lane.clock.value + timedelta(minutes=1)
    approval = seed_approval_fixture(
        production, updates={TRAINING: {"expires_at_utc": end}}
    )
    cutoff = production.lane.clock()
    start = end - timedelta(seconds=2)
    values = iter(
        [
            start,
            end if boundary == "completion" else start + timedelta(seconds=1),
            end,
            end + timedelta(seconds=1),
        ]
    )
    production.repo._clock = lambda: next(values)
    with pytest.raises(
        ValueError, match="PRODUCTION_MODEL_TRAINING grant is not active"
    ):
        production.repo.build_release(
            "expiry", approval.artifact_id, cutoff, horizon(), horizon()
        )
    assert counts(production)["production_quant_model_releases"] == 0
    assert counts(production)["production_quant_model_release_facts"] == 0


def test_inference_does_not_require_current_research_or_training_rights(production):
    approval = seed_approval_fixture(
        production,
        updates={TRAINING: {"expires_at_utc": datetime(2027, 1, 1, tzinfo=UTC)}},
    )
    release = build(production, approval)
    production.lane.clock.value = datetime(2028, 1, 1, tzinfo=UTC)
    plan = target_plan(production, release)
    production.lane.clock.value = plan.content_payload.decision_as_of_at_utc
    current = production.repo.authorization(
        release.artifact_id, plan.content_payload.decision_as_of_at_utc
    )
    assert release_active_for_inference(
        release=release,
        plan=plan,
        current=current,
        state_retention_horizon=horizon(),
        audit_retention_horizon=horizon(),
    )
    with pytest.raises(ValueError, match="not active"):
        build(production, approval, key="expired-new-build")


@pytest.mark.parametrize("kind", [TRAINING, INFERENCE, STATE, AUDIT])
def test_revocations_have_independent_actual_scope_and_retry(production, kind):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    plan = target_plan(production, release)
    event, intent, review = revoke(production, approval, release=release, kind=kind)
    calls = len(production.lane.clock.calls)
    assert (
        production.repo.record_revocation(
            "revocation", revocation_request=intent, review=review
        )
        == event
    )
    assert len(production.lane.clock.calls) == calls
    production.lane.clock.value = plan.content_payload.decision_as_of_at_utc
    current = production.repo.authorization(
        release.artifact_id, plan.content_payload.decision_as_of_at_utc
    )
    assert current.revocations == (event,)
    kwargs = dict(
        release=release,
        plan=plan,
        current=current,
        state_retention_horizon=horizon(),
        audit_retention_horizon=horizon(),
    )
    if kind == TRAINING:
        assert release_active_for_inference(**kwargs)
    else:
        with pytest.raises(ValueError, match="revoked"):
            release_active_for_inference(**kwargs)
    with pytest.raises(ValueError, match="retry request"):
        production.repo.record_revocation(
            "revocation",
            revocation_request=intent.model_copy(update={"reason": "changed"}),
            review=review,
        )


def test_revocation_requires_pinned_independent_local_review_bytes(production):
    approval = seed_approval_fixture(production)
    event, intent, review = revoke(production, approval)
    (production.lane.root / review.evidence_reference).write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        production.repo.record_revocation(
            "revocation", revocation_request=intent, review=review
        )
    assert counts(production)["training_history_revocation_events"] == 1
    assert (
        event.content_payload.recorded_at_utc
        >= approval.content_payload.approval_payload.persisted_at_utc
    )


def test_reviewed_request_cannot_change_grant_scope(production):
    approval = seed_approval_fixture(production)
    _, intent, review = revoke(production, approval)
    with pytest.raises(ValueError, match="exact payload"):
        production.repo.record_revocation(
            "different-request",
            revocation_request=intent.model_copy(
                update={"affected_grants": (TRAINING,)}
            ),
            review=review,
        )


def test_same_session_authorization_sees_uncommitted_revocation_and_never_commits(
    production,
):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    event, intent, review = revoke(production, approval)
    at = production.lane.clock()
    extra = GrantRevocationV1.freeze(
        content_payload=event.content_payload.model_copy(
            update={
                "recorded_at_utc": at,
                "effective_at_utc": at,
            }
        )
    )
    request = persistence._request(
        "record_revocation",
        "not-committed",
        "operator",
        revocation_request=intent,
        review=review,
    )
    with production.lane.sessions.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        persistence._insert(
            session,
            "training_history_revocation_events",
            persistence._revocation_row(extra, request),
        )
        loaded = production.repo.load_release_in_session(session, release.artifact_id)
        current = production.repo.authorization_in_session(
            session, release.artifact_id, at
        )
        assert loaded == release and set(
            item.artifact_id for item in current.revocations
        ) == {event.artifact_id, extra.artifact_id}
        assert session.in_transaction()
        assert production.bridge.calls[-1][2] is session
        session.rollback()
    assert counts(production)["training_history_revocation_events"] == 1


@pytest.mark.parametrize(
    "table",
    [
        "training_history_manifests",
        "training_history_admissions",
        "training_history_seasons",
        "training_history_sources",
        "training_history_fixture_sources",
        "training_history_mapping_sources",
        "training_history_result_sources",
        "training_history_facts",
        "training_history_approval_events",
        "training_history_approval_grants",
        "production_quant_model_releases",
        "production_quant_model_release_facts",
        "production_target_acceptance_plans",
        "production_target_acceptance_matches",
    ],
)
def test_all_load_projections_detect_tampering_after_update_trigger_removal(
    production, table
):
    release = build(production)
    plan = target_plan(production, release)
    with production.lane.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table}_append_only_update")
        connection.execute(text(f"UPDATE {table} SET artifact_json = '{{}}'"))
    with pytest.raises(ValueError, match="integrity"):
        production.repo.load_target_plan(plan.artifact_id)


@pytest.mark.parametrize("action", ["UPDATE", "DELETE", "REPLACE"])
def test_append_only_guards_cover_every_populated_production_table(production, action):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    target_plan(production, release)
    revoke(production, approval)
    for table, count in counts(production).items():
        if not count:
            continue
        sql = {
            "UPDATE": f"UPDATE {table} SET row_sha256 = row_sha256",
            "DELETE": f"DELETE FROM {table}",
            "REPLACE": f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
        }[action]
        with pytest.raises(IntegrityError):
            with production.lane.engine.begin() as connection:
                connection.execute(text(sql))


def test_composite_fk_prevents_cross_fact_release_projection(production):
    release = build(production)
    table = Base.metadata.tables["production_quant_model_release_facts"]
    with production.lane.sessions() as session:
        row = dict(session.execute(select(table)).mappings().first())
    row.update(release_id="unsealed-new-release", match_result_id="normalized-1")
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with production.lane.sessions.begin() as session:
            session.execute(table.insert().values(**row))
    assert production.repo.load_release(release.artifact_id) == release


def test_parent_last_guards_reject_orphans_and_late_children(production):
    value = manifest(production)
    table = Base.metadata.tables["training_history_seasons"]
    with production.lane.sessions() as session:
        row = dict(session.execute(select(table)).mappings().first())
    row.update(season_sequence=7, season_id="late")
    with pytest.raises(IntegrityError, match="sealed production"):
        with production.lane.sessions.begin() as session:
            session.execute(table.insert().values(**row))
    row.update(manifest_id="orphan-manifest")
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with production.lane.sessions.begin() as session:
            session.execute(table.insert().values(**row))
    assert production.repo.load_manifest(value.artifact_id) == value


def test_successor_no_forks_and_full_predecessor_load(production):
    first = seed_approval_fixture(production)
    release = build(production, first)
    second = seed_approval_fixture(production, predecessor=first)
    assert production.repo.load_approval(second.artifact_id) == second
    current = production.repo.authorization(
        release.artifact_id, production.lane.clock()
    )
    assert current.successors[0].content_payload.successor == second.reference()
    with pytest.raises(IntegrityError):
        seed_approval_fixture(production, predecessor=first)


def test_unadmitted_normalized_successor_blocks_reads_and_authorization(production):
    release = build(production)
    original = production.lane.submissions[0].candidate.normalized_result
    successor = original.model_copy(
        update={
            "match_result_id": "unadmitted",
            "source_result_key": "unadmitted",
            "supersedes_match_result_id": original.match_result_id,
            "ingested_at_utc": original.ingested_at_utc + timedelta(seconds=1),
        }
    )
    SqlAlchemyHistoricalRepository(production.lane.sessions).append_match_result(
        successor
    )
    with pytest.raises(ControlledTrainingCorrectionRequired):
        production.repo.authorization(release.artifact_id, production.lane.clock())


def test_registered_correction_ledger_is_a_blocker_not_an_admission_bypass(production):
    release = build(production)
    binding = release.content_payload.release_facts[
        0
    ].content_payload.binding.content_payload
    result = binding.match_result_admission
    registered = production.lane.clock()
    correction = SourceCorrectionV1.freeze(
        content_payload=SourceCorrectionContentV1(
            source_id="source",
            provider_code="PROVIDER",
            match_id="match-0",
            component="RESULT",
            predecessor=ReleaseArtifactRefV1(
                artifact_id=result.match_result_admission_id,
                content_hash=result.admission_hash,
            ),
            successor=ref("blocked-correction"),
            predecessor_source_available_at_utc=result.content_payload.source_available_at_utc,
            source_available_at_utc=result.content_payload.source_available_at_utc
            + timedelta(seconds=1),
            local_imported_at_utc=registered,
            registered_at_utc=registered,
        )
    )
    with production.lane.sessions.begin() as session:
        persistence._insert(
            session,
            "training_source_correction_events",
            persistence._correction_row(correction),
        )
    with pytest.raises(
        ControlledTrainingCorrectionRequired, match="registered correction"
    ):
        production.repo.authorization(release.artifact_id, registered)


def test_naive_clock_and_time_are_rejected_without_rows(production):
    production.repo._clock = lambda: datetime(2026, 1, 2)
    with pytest.raises(ValueError, match="aware UTC"):
        manifest(production)
    assert not any(counts(production).values())


def test_migration_upgrade_and_empty_downgrade(tmp_path):
    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / 'production-migration.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "9b8d3e5f0a21")
    command.upgrade(config, "a0c9e4f6b132")
    engine = create_database_engine(url)
    assert set(PRODUCTION_QUANT_TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
    command.downgrade(config, "9b8d3e5f0a21")
    engine = create_database_engine(url)
    assert not set(PRODUCTION_QUANT_TABLES) & set(inspect(engine).get_table_names())
    engine.dispose()


def test_migration_offline_upgrade_and_fail_closed_downgrade(tmp_path):
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{(tmp_path / 'not-created.db').as_posix()}"
    )
    command.upgrade(config, "9b8d3e5f0a21:a0c9e4f6b132", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE production_quant_model_releases" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert all(name in sql for name in production_quant_trigger_sql_v1())
    with pytest.raises(RuntimeError, match="offline production downgrade"):
        command.downgrade(config, "a0c9e4f6b132:9b8d3e5f0a21", sql=True)
    assert not (tmp_path / "not-created.db").exists()


def test_rehashed_child_projection_still_cannot_forge_exact_manifest(production):
    value = manifest(production)
    table = Base.metadata.tables["training_history_facts"]
    with production.lane.sessions() as session:
        row = dict(
            session.execute(select(table).where(table.c.fact_sequence == 0))
            .mappings()
            .one()
        )
    row["elo_fact_hash"] = "0" * 64
    changed = persistence._row(
        table.name, **{key: val for key, val in row.items() if key != "row_sha256"}
    )
    with production.lane.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table.name}_append_only_update")
        connection.execute(
            table.update().where(table.c.fact_sequence == 0).values(**changed)
        )
    with pytest.raises(ValueError, match="projection mismatch"):
        production.repo.load_manifest(value.artifact_id)


def test_missing_sealed_child_rejected_on_all_release_reads(production):
    release = build(production)
    with production.lane.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_production_quant_model_release_facts_append_only_delete"
        )
        connection.execute(
            text(
                "DELETE FROM production_quant_model_release_facts WHERE fact_sequence = 1"
            )
        )
    with pytest.raises(ValueError, match="child count"):
        production.repo.load_release(release.artifact_id)


def test_populated_production_downgrade_refuses_lineage_deletion(production):
    manifest(production)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(production.lane.engine.url))
    command.stamp(config, "a0c9e4f6b132")
    with pytest.raises(RuntimeError, match="immutable production lineage"):
        command.downgrade(config, "9b8d3e5f0a21")
    with production.lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "a0c9e4f6b132"
        )


def test_session_helpers_require_active_matching_transaction(production):
    value = manifest(production)
    with (
        production.lane.sessions() as session,
        pytest.raises(ValueError, match="active same-database"),
    ):
        production.repo.load_manifest_in_session(session, value.artifact_id)


def test_authorization_rejects_future_actual_times(production):
    release = build(production)
    with pytest.raises(ValueError, match="cannot forecast"):
        production.repo.authorization(
            release.artifact_id, production.lane.clock.value + timedelta(days=1)
        )


def test_actual_persisted_pilot_bridge_rejects_synthetic_production_evidence(
    tmp_path, monkeypatch
):
    from . import test_quant_integrity_persistence as pilot_fixtures
    from football_system.domain.quant_integrity import IntegrityArtifactRefV1
    from football_system.infrastructure.database import (
        quant_integrity_repository as pilot_module,
    )

    revision = persistence._code_revision()
    monkeypatch.setattr(pilot_fixtures, "_code_revision", lambda: revision)
    monkeypatch.setattr(pilot_module, "_code_revision", lambda: revision)
    generator = pilot_fixtures.pilot.__wrapped__(tmp_path)
    pilot = next(generator)
    try:
        plan, _ = pilot_fixtures.run(pilot)
        attestation = pilot.service.seal_terminal_attestation(
            IntegrityArtifactRefV1.of(plan)
        )
        history = prepare_training_history(
            admissions=(pilot.admission,),
            training_window=pilot.definition.training_window,
            integrity_pilot_scope_id=pilot.definition.integrity_pilot_scope_id,
        )
        repository = SqlAlchemyProductionQuantRepository(
            pilot.sessions,
            admission_repository=pilot.adapter,
            pilot_repository=pilot.repo,
            clock=pilot.clock,
            operator_id="operator",
        )
        with pilot.sessions.begin() as session:
            session.execute(text("BEGIN"))
            with pytest.raises(ValueError, match="SYNTHETIC_CONTRACT_ONLY"):
                repository._technical(session, attestation.artifact_id, history)
            assert (
                session.scalar(
                    text(
                        "SELECT COUNT(*) FROM production_quant_integrity_pilot_attestations"
                    )
                )
                == 1
            )
            assert (
                session.scalar(text("SELECT COUNT(*) FROM training_history_manifests"))
                == 0
            )
            assert (
                session.scalar(
                    text("SELECT COUNT(*) FROM training_history_approval_events")
                )
                == 0
            )
            assert (
                session.scalar(
                    text("SELECT COUNT(*) FROM production_quant_model_releases")
                )
                == 0
            )
    finally:
        generator.close()


def withdrawal_authority(context):
    authority = write_evidence(
        context.lane.root,
        "withdrawal-authority.json",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by="governance",
            authorized_reviewer="withdrawal-reviewer",
            source_ids=["source"],
            attested_schema_versions=["PRODUCTION_REVOCATION_REQUEST_V1"],
            effective_at_utc="2026-01-01T00:00:00Z",
            expires_at_utc="2031-01-01T00:00:00Z",
        ),
    )
    context.lane.repo.evidence.trusted_authorities[authority.evidence_reference] = (
        authority.evidence_sha256
    )
    return authority


def withdrawal_request(context, approval, release, authority):
    return context.repo.prepare_revocation_request(
        approval.artifact_id,
        release_id=None if release is None else release.artifact_id,
        affected_grants=(INFERENCE,),
        actor="withdrawal-reviewer",
        reviewer_authority=authority,
        reason="Withdraw an unusable historical artifact.",
    )


def withdrawal_review(context, intent):
    document = review_document(
        schema=intent.schema_version,
        digest=intent.request_hash,
        at=context.lane.clock(),
    )
    document["authorized_reviewer"] = "withdrawal-reviewer"
    return write_evidence(context.lane.root, "withdrawal-review.json", document)


@contextmanager
def degraded_history(context, approval, damage, monkeypatch):
    """Change real files or the installed-code comparison, never the stored seals."""
    saved = {}
    with monkeypatch.context() as patch:
        if damage == "code":
            from football_system.application.run_analysis import _code_revision

            assert _code_revision() != context.bridge.evidence.code_revision
            patch.setattr(persistence, "_code_revision", _code_revision)
        elif damage == "recipe":
            patch.setattr(
                persistence,
                "production_build_recipe_v1",
                lambda recipe_id: ref("changed-recipe"),
            )
            patch.setattr(
                persistence,
                "_production_build_recipe_v1",
                lambda recipe_id, code_revision: ref("changed-recipe"),
            )
        elif damage == "replay":
            from football_system.domain import production_release

            def unavailable(*args, **kwargs):
                raise ValueError("current Elo replay is unavailable")

            patch.setattr(production_release, "replay_exact_facts", unavailable)
        else:
            files = {
                "source": ("fixture.json", "scope.json", "result.json"),
                "old-review": (
                    approval.content_payload.reviewer_attestation.content_payload.evidence.evidence_reference,
                ),
                "old-authority": (context.authority.evidence_reference,),
            }[damage]
            for filename in files:
                path = context.lane.root / filename
                saved[path] = path.read_bytes()
                path.unlink()
        try:
            yield
        finally:
            for path, data in saved.items():
                path.write_bytes(data)


@pytest.mark.parametrize("release_specific", [False, True])
@pytest.mark.parametrize(
    "damage", ["code", "recipe", "source", "old-review", "old-authority", "replay"]
)
def test_withdrawal_survives_code_drift_and_missing_historical_evidence(
    production, monkeypatch, damage, release_specific
):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    plan = target_plan(production, release)
    authority = withdrawal_authority(production)
    selected = release if release_specific else None
    intent = withdrawal_request(production, approval, selected, authority)
    with degraded_history(production, approval, damage, monkeypatch):
        with pytest.raises((ValueError, OSError)):
            production.repo.load_release(release.artifact_id)
        assert withdrawal_request(production, approval, selected, authority) == intent
        review = withdrawal_review(production, intent)
        event = production.repo.record_revocation(
            "withdraw-unusable", revocation_request=intent, review=review
        )
        assert event.content_payload.approval == approval.reference()
        assert event.content_payload.release == (
            release.reference() if release_specific else None
        )
        calls = len(production.lane.clock.calls)
        assert (
            production.repo.record_revocation(
                "withdraw-unusable", revocation_request=intent, review=review
            )
            == event
        )
        assert len(production.lane.clock.calls) == calls
        assert counts(production)["training_history_revocation_events"] == 1
    # Restoring the old package/files must not erase withdrawal or reactivate it.
    assert production.repo.load_release(release.artifact_id) == release
    production.lane.clock.value = plan.content_payload.decision_as_of_at_utc
    current = production.repo.authorization(
        release.artifact_id, production.lane.clock.value
    )
    assert current.revocations == (event,)
    with pytest.raises(ValueError, match="revoked"):
        release_active_for_inference(
            release=release,
            plan=plan,
            current=current,
            state_retention_horizon=horizon(),
            audit_retention_horizon=horizon(),
        )


@pytest.mark.parametrize(
    "fault",
    ["reviewer", "authority", "request", "approval-hash", "release-hash", "sources"],
)
def test_degraded_history_never_weakens_fresh_withdrawal_review(
    production, monkeypatch, fault
):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    authority = withdrawal_authority(production)
    with degraded_history(production, approval, "source", monkeypatch):
        intent = withdrawal_request(production, approval, release, authority)
        if fault == "approval-hash":
            intent = intent.model_copy(
                update={
                    "approval": intent.approval.model_copy(
                        update={"content_hash": "0" * 64}
                    )
                }
            )
        elif fault == "release-hash":
            intent = intent.model_copy(
                update={
                    "release": intent.release.model_copy(
                        update={"content_hash": "0" * 64}
                    )
                }
            )
        elif fault == "sources":
            intent = intent.model_copy(update={"source_ids": ("other-source",)})
        if fault == "reviewer":
            document = review_document(
                schema=intent.schema_version,
                digest=intent.request_hash,
                at=production.lane.clock(),
            )
            document["authorized_reviewer"] = "operator"
            review = write_evidence(
                production.lane.root, "withdrawal-review.json", document
            )
        else:
            review = withdrawal_review(production, intent)
        if fault == "authority":
            production.lane.repo.evidence.trusted_authorities.pop(
                authority.evidence_reference
            )
        elif fault == "request":
            intent = intent.model_copy(update={"affected_grants": (TRAINING,)})
        with pytest.raises(
            ValueError, match="scope mismatch|not authorized|not pinned|exact payload"
        ):
            production.repo.record_revocation(
                "bad-withdrawal", revocation_request=intent, review=review
            )
    assert counts(production)["training_history_revocation_events"] == 0


def test_withdrawal_does_not_call_activation_or_replay_loaders(production, monkeypatch):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    authority = withdrawal_authority(production)
    from football_system.domain.services.elo_baseline import EloThreeWayBaseline

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "withdrawal must not activate or replay historical evidence"
        )

    for name in (
        "load_approval_in_session",
        "load_manifest_in_session",
        "load_release_in_session",
    ):
        monkeypatch.setattr(production.repo, name, forbidden)
    monkeypatch.setattr(production.lane.repo, "_load", forbidden)
    monkeypatch.setattr(production.bridge, "technical_evidence", forbidden)
    monkeypatch.setattr(persistence, "_code_revision", forbidden)
    monkeypatch.setattr(EloThreeWayBaseline, "rebuild_state", forbidden)
    # Even expired production grants are valid targets for an authorized withdrawal.
    production.lane.clock.value = END + timedelta(days=1)
    intent = withdrawal_request(production, approval, release, authority)
    review = withdrawal_review(production, intent)
    assert production.repo.record_revocation(
        "expired-target", revocation_request=intent, review=review
    )


@pytest.mark.parametrize(
    "artifact", ["approval-grants", "approval-authority", "manifest", "release"]
)
def test_withdrawal_still_requires_the_exact_persisted_target_seals(
    production, artifact
):
    approval = seed_approval_fixture(production)
    release = build(production, approval)
    authority = withdrawal_authority(production)
    table_name, key, value = {
        "approval-grants": (
            "training_history_approval_events",
            "approval_id",
            approval.artifact_id,
        ),
        "approval-authority": (
            "training_history_approval_events",
            "approval_id",
            approval.artifact_id,
        ),
        "manifest": (
            "training_history_manifests",
            "manifest_id",
            release.content_payload.manifest.artifact_id,
        ),
        "release": (
            "production_quant_model_releases",
            "release_id",
            release.artifact_id,
        ),
    }[artifact]
    table = Base.metadata.tables[table_name]
    with production.lane.sessions() as session:
        row = dict(
            session.execute(select(table).where(table.c[key] == value)).mappings().one()
        )
    document = json.loads(row["artifact_json"])
    if artifact == "approval-grants":
        document["content_payload"]["approval_payload"]["grants"][0][
            "expires_at_utc"
        ] = "2035-01-01T00:00:00Z"
    elif artifact == "approval-authority":
        document["content_payload"]["approval_payload"]["authority_sha256"] = "0" * 64
    elif artifact == "manifest":
        document["content_payload"]["history"]["source_summaries"][0]["source_id"] = (
            "other-source"
        )
    else:
        document["content_payload"]["scope"]["production_target_season_id"] = (
            "other-season"
        )
    row["artifact_json"] = canonical_json(document)
    # A forged SQL row checksum must not substitute for the artifact's own seal.
    row = persistence._row(
        table_name, **{name: item for name, item in row.items() if name != "row_sha256"}
    )
    with production.lane.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table_name}_append_only_update")
        connection.execute(table.update().where(table.c[key] == value).values(**row))
    with pytest.raises(ValueError, match="hash mismatch|payload hash"):
        withdrawal_request(production, approval, release, authority)
    assert counts(production)["training_history_revocation_events"] == 0


def test_build_reuses_verification_only_within_each_fresh_boundary(
    production, monkeypatch
):
    approval = seed_approval_fixture(production)
    original = production.bridge.technical_evidence
    scopes = []

    def observed(attestation_id, history, *, session=None):
        scopes.append(session.info["production_verified_read"])
        return original(attestation_id, history, session=session)

    monkeypatch.setattr(production.bridge, "technical_evidence", observed)
    build(production, approval)
    assert len(scopes) == 3  # Start, completion, pre-commit, not every nested load.
    assert all(scopes[i] is not scopes[j] for i in range(3) for j in range(i))
    assert "production_verified_read" not in production.bridge.calls[-1][2].info


def test_training_expiry_during_final_verification_is_not_cached(production):
    end = production.lane.clock.value + timedelta(minutes=1)
    approval = seed_approval_fixture(
        production, updates={TRAINING: {"expires_at_utc": end}}
    )
    cutoff = production.lane.clock()
    times = iter(end - timedelta(seconds=offset) for offset in (4, 3, 2, 1, 0))
    production.repo._clock = lambda: next(times)
    with pytest.raises(
        ValueError, match="PRODUCTION_MODEL_TRAINING grant is not active"
    ):
        production.repo.build_release(
            "expiry-in-verification", approval.artifact_id, cutoff, horizon(), horizon()
        )
    assert counts(production)["production_quant_model_releases"] == 0
    assert counts(production)["production_quant_model_release_facts"] == 0


@pytest.mark.parametrize("boundary", ["completion", "commit"])
@pytest.mark.parametrize("change", ["source", "approval-review", "code", "revocation"])
def test_build_never_reuses_stale_evidence_or_authorization_between_boundaries(
    production, monkeypatch, boundary, change
):
    approval = seed_approval_fixture(production)
    intent = production.repo.prepare_revocation_request(
        approval.artifact_id,
        release_id=None,
        affected_grants=(TRAINING,),
        actor="reviewer",
        reviewer_authority=production.authority,
        reason="Withdraw during test build.",
    )
    review = write_evidence(
        production.lane.root,
        "boundary-review.json",
        review_document(
            schema=intent.schema_version,
            digest=intent.request_hash,
            at=production.lane.clock(),
        ),
    )
    cutoff = production.lane.clock()
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        at = production.lane.clock()
        if calls == (2 if boundary == "completion" else 4):
            if change == "source":
                (production.lane.root / "result.json").unlink()
            elif change == "approval-review":
                old = approval.content_payload.reviewer_attestation.content_payload.evidence.evidence_reference
                (production.lane.root / old).unlink()
            elif change == "code":
                monkeypatch.setattr(
                    persistence, "_code_revision", lambda: "package:changed-at-boundary"
                )
            else:
                session = production.bridge.calls[-1][2]
                event = GrantRevocationV1.freeze(
                    content_payload=GrantRevocationContentV1(
                        approval=approval.reference(),
                        release=None,
                        affected_grants=(TRAINING,),
                        actor=intent.actor,
                        authority_reference=production.authority.evidence_reference,
                        authority_sha256=production.authority.evidence_sha256,
                        reason=intent.reason,
                        recorded_at_utc=at,
                        effective_at_utc=at,
                    )
                )
                request = persistence._request(
                    "record_revocation",
                    "boundary-withdrawal",
                    "operator",
                    revocation_request=intent,
                    review=review,
                )
                persistence._insert(
                    session,
                    "training_history_revocation_events",
                    persistence._revocation_row(event, request),
                )
        return at

    production.repo._clock = clock
    with pytest.raises((ValueError, OSError)):
        production.repo.build_release(
            "changed-boundary", approval.artifact_id, cutoff, horizon(), horizon()
        )
    assert counts(production)["production_quant_model_releases"] == 0
    assert counts(production)["production_quant_model_release_facts"] == 0
    assert "production_verified_read" not in production.bridge.calls[-1][2].info


def test_technical_recipe_uses_one_fresh_revision_even_on_scope_cache_hits(
    production, monkeypatch
):
    evidence = production.bridge.evidence
    revision = evidence.code_revision
    scans = []

    def observe_revision():
        scans.append(revision)
        return revision

    monkeypatch.setattr(persistence, "_code_revision", observe_revision)
    with production.lane.sessions.begin() as session:
        session.execute(text("BEGIN"))
        with persistence._verification_scope(session):
            for attempt in range(2):
                assert (
                    production.repo._technical(
                        session, evidence.attestation.artifact_id, production.history
                    )
                    == evidence
                )
                assert scans == [revision] * (attempt + 1)
            assert len(production.bridge.calls) == 1
            revision = "package:changed-during-verification"
            with pytest.raises(ValueError, match="code revision/build recipe"):
                production.repo._technical(
                    session, evidence.attestation.artifact_id, production.history
                )
            assert scans == [evidence.code_revision, evidence.code_revision, revision]
        assert "production_verified_read" not in session.info
        revision = evidence.code_revision
        assert (
            production.repo._technical(
                session, evidence.attestation.artifact_id, production.history
            )
            == evidence
        )
        assert len(production.bridge.calls) == 2
        assert len(scans) == 4


def test_public_recipe_observes_current_revision_on_every_call(monkeypatch):
    import hashlib

    scans = []

    def revision():
        value = f"package:synthetic-revision-{len(scans)}"
        scans.append(value)
        return value

    monkeypatch.setattr(persistence, "_code_revision", revision)
    recipes = [production_build_recipe_v1("test-recipe") for _ in range(2)]
    assert len(scans) == 2
    assert recipes[0] != recipes[1]
    for recipe, observed in zip(recipes, scans, strict=True):
        expected = {
            "schema_version": "QUANT_INTEGRITY_BUILD_RECIPE_V1",
            "recipe_id": "test-recipe",
            "implementation_code_revision": observed,
            "model_name": "ELO_THREE_WAY_BASELINE_V1",
            "model_version": "1",
            "config_hash": persistence.PRODUCTION_CONFIG_HASH,
            "projection": "ADMITTED_SOURCE_TIME_ELO_V1",
            "parameter_policy": "NO_PARAMETER_TUNING",
        }
        assert recipe == ReleaseArtifactRefV1(
            artifact_id="test-recipe",
            content_hash=hashlib.sha256(canonical_json(expected).encode()).hexdigest(),
        )
    with pytest.raises(ValueError):
        production_build_recipe_v1("")
    assert len(scans) == 2
