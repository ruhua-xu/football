"""Controlled writer and history integration; bridge doubles are SYNTHETIC ONLY."""

from datetime import timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from football_system.application.production_release import (
    prepare_versioned_training_history,
    project_release_state,
)
from football_system.domain.production_release import (
    TechnicalEvidenceRefsV1,
    release_active_for_inference,
)
from football_system.domain.quant_integrity import TrainingAdmissionPinV1
from football_system.domain.services.elo_baseline import EloThreeWayBaseline
from football_system.domain.training_correction import (
    CORRECTION_INTENT_V2,
    CorrectionRefV2,
)
from football_system.domain.versioned_training_history import (
    TrainingHistoryContextPinV2,
    project_versioned_training_fact,
    select_versioned_training_facts,
)
from football_system.infrastructure.database.training_correction_repository import (
    SqlAlchemyTrainingCorrectionRepository,
)
from football_system.infrastructure.database.versioned_quant_schema import (
    VERSIONED_QUANT_TABLES,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes
from football_system.infrastructure.providers.historical_archive import (
    RepositoryArchiveMembershipSource,
    HistoricalArchiveEloTrainingProvider,
)
from tests.integration import test_production_quant_persistence as legacy
from tests.integration.test_training_approval_v2 import record_v2
from tests.integration.test_training_corrections import reviewed_intent, record
from tests.integration import test_quant_integrity_persistence as pilot_tests

lane = legacy.lane
production = legacy.production
actual_admission = pilot_tests.actual_admission


class SyntheticBridgeRegistry:
    """Explicit test double; never installed by any public production API."""

    def __init__(self, context):
        evidence = context.bridge.evidence
        self.values = {evidence.attestation.artifact_id: (context.history, evidence)}

    def technical_evidence(self, attestation_id, history, *, session=None):
        assert session is not None and session.in_transaction()
        expected, evidence = self.values[attestation_id]
        assert history == expected
        return evidence


@pytest.fixture
def corrected(production):
    ctx, lane = production, production.lane
    enable_corrections(lane, ctx.admission)
    ctx.bridges = SyntheticBridgeRegistry(ctx)
    ctx.repo.pilot_repository = ctx.bridges
    return ctx


def enable_corrections(lane, admission):
    lane.base_admission = admission
    assert inspect(lane.engine).has_table("training_correction_admissions")
    assert all(inspect(lane.engine).has_table(name) for name in VERSIONED_QUANT_TABLES)
    lane.corrections = SqlAlchemyTrainingCorrectionRepository(lane.repo)
    authority = strict_json_bytes(lane.repo.evidence.read("authority.json"))
    authority["attested_schema_versions"] = [CORRECTION_INTENT_V2]
    lane.correction_authority = legacy.write_evidence(
        lane.root, "correction-authority.json", authority
    )
    lane.repo.evidence.trusted_authorities["correction-authority.json"] = (
        lane.correction_authority.evidence_sha256
    )
    lane.base_pin = CorrectionRefV2(
        schema_version=admission.schema_version,
        artifact_id=admission.training_fact_admission_id,
        content_hash=admission.admission_hash,
    )
    lane.base = lane.corrections.load_context(
        base_admissions=(lane.base_pin,), correction_ids=(), actual_at=lane.clock()
    ).versions[0]
    lane.next_correction = 0


def load_context(ctx, ids=()):
    return ctx.lane.corrections.load_context(
        base_admissions=(ctx.lane.base_pin,),
        correction_ids=ids,
        actual_at=ctx.lane.clock(),
    )


def selected(ctx, context, cutoff):
    return select_versioned_training_facts(
        context,
        "league",
        ctx.window.content_payload.ordered_season_ids,
        cutoff,
        (),
        False,
    )


def corrected_manifest(ctx, ids, *, key="corrected-manifest"):
    context = load_context(ctx, ids)
    cutoff = context.actual_at_utc
    graph = prepare_versioned_training_history(
        admissions=(ctx.admission,),
        correction_context=context,
        training_window=ctx.window,
        integrity_pilot_scope_id="test-scope",
        selection_cutoff_at_utc=cutoff,
    )
    old = ctx.bridge.evidence
    evidence = TechnicalEvidenceRefsV1(
        **{
            **old.model_dump(),
            "plan": legacy.ref(key + "-plan"),
            "attestation": legacy.ref(key + "-attestation"),
            "source_root": graph.source_root,
            "season_root": graph.season_root,
            "approved_facts_hash": graph.approved_facts_hash,
            "training_data_hash": graph.training_data_hash,
            "plan_sealed_at_utc": ctx.lane.clock(),
            "actual_started_at_utc": ctx.lane.clock(),
            "actual_completed_at_utc": ctx.lane.clock(),
            "attestation_persisted_at_utc": ctx.lane.clock(),
        }
    )
    ctx.bridges.values[evidence.attestation.artifact_id] = graph, evidence
    return ctx.repo.create_manifest(
        key,
        (ctx.admission.training_fact_admission_id,),
        ctx.window,
        "test-scope",
        evidence.attestation.artifact_id,
        correction_context=TrainingHistoryContextPinV2.of(context),
        context_registered_at_utc=context.actual_at_utc,
        selection_cutoff_at_utc=cutoff,
    )


def test_result_correction_changes_later_slice_not_earlier_and_preserves_score_hash(
    corrected,
):
    ctx = corrected
    original = load_context(ctx)
    correction = record(
        ctx.lane, reviewed_intent(ctx.lane, raw={"home_goals": 0, "away_goals": 4})
    )
    context = load_context(ctx, (correction.artifact_id,))
    boundary = (
        correction.content_payload.intent.candidate.result_source_available_at_utc
    )
    assert selected(ctx, context, boundary - timedelta(seconds=1)) == selected(
        ctx, original, boundary - timedelta(seconds=1)
    )
    later = selected(ctx, context, boundary + timedelta(days=2))
    assert later[0].version_id == correction.artifact_id
    assert len(later) == len(original.versions)
    state = EloThreeWayBaseline().rebuild_state(
        tuple(project_versioned_training_fact(v) for v in later),
        context.actual_at_utc,
        target_season_id="production",
    )
    assert state.teams != project_original(ctx).teams
    assert (
        state.training_facts[0].source_payload_hash
        == correction.content_payload.normalized_result.payload_hash
    )
    assert ctx.lane.repo.load(ctx.admission.training_fact_admission_id) == ctx.admission


def project_original(ctx):
    from football_system.domain.production_release import replay_exact_facts

    return replay_exact_facts(
        tuple(f.content_payload.elo_fact for f in ctx.history.facts),
        ctx.lane.clock(),
        "production",
    )


def test_late_registration_preserves_old_release_audit_but_blocks_new_use_and_corrected_release_works(
    corrected,
):
    ctx = corrected
    old = legacy.build(ctx, record_v2(ctx))
    old_wire = old.model_dump_json()
    correction = record(ctx.lane, reviewed_intent(ctx.lane, raw={"home_goals": 0}))
    assert ctx.repo.load_release(old.artifact_id).model_dump_json() == old_wire
    with pytest.raises(ValueError, match="correction"):
        ctx.repo.authorization(old.artifact_id, ctx.lane.clock())
    with pytest.raises(ValueError, match="correction"):
        legacy.build(ctx, old.training_approval, key="old-build-again")
    manifest = corrected_manifest(ctx, (correction.artifact_id,))
    graph = manifest.content_payload.history
    assert graph.context_pin.context_root != graph.selected_versions_root
    assert len(graph.correction_context.versions) == 3 and len(graph.facts) == 2
    approval = record_v2(
        ctx, manifest, key="corrected-approval", predecessor=old.training_approval
    )
    release = legacy.build(ctx, approval, key="corrected-release")
    assert ctx.repo.load_release(release.artifact_id) == release
    assert ctx.repo.load_release(old.artifact_id).model_dump_json() == old_wire
    plan = legacy.target_plan(ctx, release)
    at = plan.content_payload.decision_as_of_at_utc
    ctx.lane.clock.value = at
    current = ctx.repo.authorization(release.artifact_id, ctx.lane.clock())
    assert current.corrections and all(
        e.content_payload.predecessor.artifact_id != "ROOT" for e in current.corrections
    )
    assert release_active_for_inference(
        release=release,
        plan=plan,
        current=current,
        state_retention_horizon=legacy.horizon(),
        audit_retention_horizon=legacy.horizon(),
    )
    state = project_release_state(
        release,
        at,
        tuple(t.match_id for t in plan.content_payload.targets),
        "production",
    )
    assert (
        "corrected-1" in state.training_result_ids
        and "normalized-0" not in state.training_result_ids
    )
    with ctx.lane.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_ancestor_corrections_do_not_stale_current_head(corrected):
    ctx = corrected
    first = record(ctx.lane, reviewed_intent(ctx.lane, raw={"home_goals": 0}))
    previous = ctx.lane.corrections.load_version(first.artifact_id)
    second = record(
        ctx.lane, reviewed_intent(ctx.lane, previous=previous, raw={"home_goals": 4})
    )
    ancestor = ctx.lane.corrections.invalidation_evidence(first.artifact_id)[0].as_v1()
    with ctx.lane.sessions.begin() as session:
        legacy.persistence._insert(
            session,
            "training_source_correction_events",
            legacy.persistence._correction_row(ancestor),
        )
    manifest = corrected_manifest(ctx, (first.artifact_id, second.artifact_id))
    release = legacy.build(ctx, record_v2(ctx, manifest))
    assert (
        len(ctx.repo.authorization(release.artifact_id, ctx.lane.clock()).corrections)
        == 2
    )
    assert (
        release.content_payload.release_facts[0].content_payload.version.version_id
        == second.artifact_id
    )


def test_withdrawal_does_not_resurrect_previous_fact_and_restoration_requires_full_context(
    corrected,
):
    ctx = corrected
    withdrawn = record(
        ctx.lane,
        reviewed_intent(
            ctx.lane,
            trainable=False,
            raw={
                "status": "CANCELLED",
                "home_goals": None,
                "away_goals": None,
                "finalized_at_utc": None,
                "score_semantics": None,
            },
        ),
    )
    context = load_context(ctx, (withdrawn.artifact_id,))
    assert [
        v.snapshot.stream.internal_match_id
        for v in selected(ctx, context, ctx.lane.clock.value - timedelta(seconds=1))
    ] == ["match-1"]
    manifest = corrected_manifest(ctx, (withdrawn.artifact_id,))
    assert manifest.content_payload.history.fact_count == 1
    release = legacy.build(ctx, record_v2(ctx, manifest))
    assert ctx.repo.authorization(release.artifact_id, ctx.lane.clock()).corrections
    head = ctx.lane.corrections.load_version(withdrawn.artifact_id)
    restored = record(ctx.lane, reviewed_intent(ctx.lane, previous=head))
    with pytest.raises(ValueError, match="predecessor"):
        load_context(ctx, (restored.artifact_id,))
    with pytest.raises(ValueError, match="correction"):
        ctx.repo.authorization(release.artifact_id, ctx.lane.clock())
    context = load_context(ctx, (withdrawn.artifact_id, restored.artifact_id))
    assert len(selected(ctx, context, context.actual_at_utc)) == 2


def test_versioned_projection_is_typed_append_only(corrected):
    ctx = corrected
    value = record(ctx.lane, reviewed_intent(ctx.lane))
    manifest = corrected_manifest(ctx, (value.artifact_id,))
    legacy.build(ctx, record_v2(ctx, manifest))
    for table in VERSIONED_QUANT_TABLES:
        for sql in (
            f"UPDATE {table} SET row_sha256=row_sha256",
            f"DELETE FROM {table}",
            f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
        ):
            with pytest.raises(IntegrityError):
                with ctx.lane.engine.begin() as c:
                    c.exec_driver_sql(sql)


def test_archive_uses_pinned_equal_time_version_and_rechecks_actual_rights(corrected):
    from tests.contract.test_local_archive_providers import (
        _write_membership_archives,
        _edit_test_archive,
        _elo_history,
    )

    ctx, lane = corrected, corrected.lane
    value = record(lane, reviewed_intent(lane, equal_time=True))
    context = load_context(ctx, (value.artifact_id,))
    lane.archive_root = lane.root / "archives"
    lane.archive_root.mkdir()
    _write_membership_archives(lane)

    def append(raw):
        original = next(
            r
            for r in raw["records"]
            if r["payload"]["match_result_id"] == "normalized-0"
        )
        from copy import deepcopy

        revision = deepcopy(original)
        revision["payload"].update(
            value.content_payload.normalized_result.model_dump(
                mode="json", exclude={"schema_version"}
            )
        )
        raw["records"].append(revision)

    _edit_test_archive(lane, "results", append)
    source = RepositoryArchiveMembershipSource(
        lane.repo,
        (TrainingAdmissionPinV1.from_admission(ctx.admission),),
        lane.clock(),
        TrainingHistoryContextPinV2.of(context),
    )
    provider = HistoricalArchiveEloTrainingProvider(
        lane.archive_root,
        "PROVIDER",
        membership_source=source,
        ordered_season_ids=ctx.window.content_payload.ordered_season_ids,
    )
    batch = _elo_history(provider, target="production")
    assert {s.result.match_result_id for s in batch.sources} == {
        "corrected-1",
        "normalized-1",
    }
    lane.clock.value = lane.rights.expires_at_utc
    with pytest.raises(ValueError, match="active"):
        _elo_history(provider, target="production")


@pytest.mark.parametrize("field", ["source_rights_admission_id", "persisted_at_utc"])
def test_archive_version_context_binds_rights_to_actual_base_before_result_reads(
    corrected, monkeypatch, field
):
    lane = corrected.lane
    value = record(lane, reviewed_intent(lane))
    context = load_context(corrected, (value.artifact_id,))
    admission = TrainingAdmissionPinV1.from_admission(corrected.admission)
    forged = admission.model_copy(
        update={
            field: "unrelated-active-grant"
            if field == "source_rights_admission_id"
            else admission.persisted_at_utc + timedelta(seconds=1)
        }
    )
    source = RepositoryArchiveMembershipSource(
        lane.repo, (forged,), lane.clock(), TrainingHistoryContextPinV2.of(context)
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            lane.repo,
            "_load",
            lambda *a, **kw: pytest.fail("unbound archive result read"),
        )
        scoped.setattr(
            lane.repo,
            "_rights",
            lambda *a, **kw: pytest.fail("caller substituted archive rights"),
        )
        with pytest.raises(ValueError, match="base admission/rights pin"):
            source.load_context()


def pilot_definition(lane, context):
    from football_system.domain.production_release import (
        EloTrainingWindowV1,
        EloTrainingWindowContentV1,
        TrainingSeasonV1,
        ProviderSeasonRefV1,
    )
    from football_system.domain.quant_integrity import (
        QuantIntegrityPlanDefinitionV2,
        QuantIntegrityScopeV1,
        QuantIntegrityCohortV1,
        QuantIntegrityProvenanceV1,
        ReviewedProviderSeasonV1,
        QuantIntegrityTargetV2,
        QuantIntegritySliceV1,
        QuantIntegritySliceContentV1,
        TerminalProjectionDefinitionV1,
        ModelBuildRecipePinV1,
    )
    from football_system.domain.versioned_training_history import VersionedFactRefV2
    from football_system.infrastructure.database import quant_integrity_repository as q
    from tests.integration.test_training_admission_persistence import SOURCE
    from football_system.domain.training_admission import tagged_canonical_sha256

    pin = TrainingHistoryContextPinV2.of(context)
    prefix = "pilot-" + pin.context_root[:10]

    def write(name, value):
        return legacy.write_evidence(lane.root, prefix + name + ".json", value)

    def capture(name, value):
        evidence = write(name, value)
        return lane.repo.capture_local_json(
            request_key=prefix + name,
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference=evidence.evidence_reference,
        )

    authority = write(
        "authority",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by="test-governance",
            authorized_reviewer="reviewer",
            source_ids=["source"],
            attested_schema_versions=[
                "QUANT_INTEGRITY_SCOPE_REVIEW_V1",
                "QUANT_INTEGRITY_COHORT_REVIEW_V1",
            ],
            effective_at_utc=lane.rights.effective_at_utc,
            expires_at_utc=lane.rights.expires_at_utc,
        ),
    )
    lane.repo.evidence.trusted_authorities[authority.evidence_reference] = (
        authority.evidence_sha256
    )
    scopes = tuple(
        ReviewedProviderSeasonV1(
            source_id="source",
            provider_code="PROVIDER",
            provider_competition_id="p-league",
            provider_season_id=provider_season,
            canonical_season_id=season,
        )
        for season, provider_season in (
            ("2024/25", "p-2024-25"),
            ("production", "p-production"),
        )
    )
    window = EloTrainingWindowV1.freeze(
        content_payload=EloTrainingWindowContentV1(
            competition_id="league",
            seasons=tuple(
                TrainingSeasonV1(
                    season_sequence=i,
                    season_id=s.canonical_season_id,
                    role="PILOT_TARGET" if i == 0 else "PRODUCTION_TARGET",
                    provider_seasons=(
                        ProviderSeasonRefV1(
                            **s.model_dump(exclude={"canonical_season_id"})
                        ),
                    ),
                )
                for i, s in enumerate(scopes)
            ),
        )
    )
    receipt = capture(
        "scopes",
        {
            "records": [
                dict(
                    competition_id=s.provider_competition_id,
                    season_id=s.provider_season_id,
                    name="Bundesliga",
                    country="DE",
                    type="DOMESTIC_LEAGUE",
                    start=SOURCE + timedelta(days=start),
                    end=SOURCE + timedelta(days=end),
                )
                for s, start, end in ((scopes[0], -300, 30), (scopes[1], 31, 400))
            ]
        },
    )
    source = write(
        "scope-descriptor",
        q.QuantIntegrityScopeDocumentV1(
            records=tuple(
                q.QuantIntegrityScopeRecordV1(
                    provider=s,
                    capture_receipt_id=receipt.capture_receipt_id,
                    record_pointer=f"/records/{i}",
                    competition_id_pointer="/competition_id",
                    season_id_pointer="/season_id",
                    competition_identity_pointer="/name",
                    expected_competition_identity="Bundesliga",
                    country_pointer="/country",
                    competition_type_pointer="/type",
                    season_start_pointer="/start",
                    season_end_pointer="/end",
                )
                for i, s in enumerate(scopes)
            )
        ),
    )

    def review(model, schema, name):
        evidence = write(
            name,
            q.QuantIntegrityReviewDocumentV1(
                attested_schema_version=schema,
                attested_payload_hash=tagged_canonical_sha256(
                    schema, model.model_dump(exclude={"evidence"})
                ),
                prepared_by="operator",
                authorized_reviewer="reviewer",
                reviewed_at_utc=model.reviewed_at_utc,
                source_ids=("source",),
                evidence_use="SYNTHETIC_CONTRACT_ONLY",
                accepted_for_internal_integrity=True,
            ),
        )
        return model.model_copy(update={"evidence": evidence})

    scope = review(
        QuantIntegrityScopeV1(
            competition_id="league",
            provider_seasons=scopes,
            reviewed_by="reviewer",
            authority_reference=authority.evidence_reference,
            authority_sha256=authority.evidence_sha256,
            reviewed_at_utc=lane.clock(),
            raw_scope=source,
            evidence=source,
        ),
        "QUANT_INTEGRITY_SCOPE_REVIEW_V1",
        "scope-review",
    )
    selected = context.select(source_cutoffs={"source": context.actual_at_utc})
    ids = tuple(sorted(v.snapshot.stream.internal_match_id for v in selected))
    completed = SOURCE + timedelta(days=30)
    schedule = capture(
        "schedule",
        dict(
            count=len(ids),
            completed_at=completed,
            records=[
                dict(
                    fixture_key=v.snapshot.stream.provider_fixture_key,
                    competition_id="p-league",
                    season_id="p-2024-25",
                    status="FT",
                )
                for v in selected
            ],
        ),
    )
    source = write(
        "schedule-descriptor",
        q.QuantIntegrityScheduleDocumentV1(
            provider=scopes[0],
            capture_receipt_id=schedule.capture_receipt_id,
            records_pointer="/records",
            expected_count_pointer="/count",
            completed_at_pointer="/completed_at",
            fixture_key_pointer="/fixture_key",
            competition_id_pointer="/competition_id",
            season_id_pointer="/season_id",
            status_pointer="/status",
            status_mapping={"FT": "REGULAR_TIME_FINAL"},
        ),
    )
    cohort = review(
        QuantIntegrityCohortV1(
            season_id="2024/25",
            expected_match_count=len(ids),
            full_schedule_match_ids=ids,
            cohort_match_ids=ids,
            season_completed_at_utc=completed,
            reviewed_at_utc=lane.clock(),
            reviewed_by="reviewer",
            raw_schedule=source,
            evidence=source,
        ),
        "QUANT_INTEGRITY_COHORT_REVIEW_V1",
        "cohort-review",
    )
    slices = tuple(
        QuantIntegritySliceV1.freeze(
            content_payload=QuantIntegritySliceContentV1(
                sequence=i,
                decision_as_of_at_utc=v.snapshot.identity.kickoff_at_utc
                - timedelta(hours=1),
                evaluation_as_of_at_utc=completed,
                exclude_match_ids=(v.snapshot.stream.internal_match_id,),
                targets=(
                    QuantIntegrityTargetV2(
                        identity=v.snapshot.identity,
                        training_fact_admission_id=lane.admission.training_fact_admission_id,
                        fact=VersionedFactRefV2.of(v),
                        fixture_source_available_at_utc=v.snapshot.fixture_source_available_at_utc,
                        mapping_source_available_at_utc=v.snapshot.mapping_source_available_at_utc,
                    ),
                ),
            )
        )
        for i, v in enumerate(
            sorted(selected, key=lambda v: v.snapshot.identity.kickoff_at_utc)
        )
    )
    recipe = q.QuantIntegrityBuildRecipeV1(
        recipe_id=prefix,
        implementation_code_revision=q._code_revision(),
        config_hash=legacy.persistence.PRODUCTION_CONFIG_HASH,
    )
    recipe_ref = write("recipe", recipe)
    return QuantIntegrityPlanDefinitionV2(
        integrity_pilot_series_id=prefix,
        provenance=QuantIntegrityProvenanceV1(evidence_use="SYNTHETIC_CONTRACT_ONLY"),
        scope=scope,
        training_window=window,
        admissions=(TrainingAdmissionPinV1.from_admission(lane.admission),),
        cohort=cohort,
        slices=slices,
        terminal_projection=TerminalProjectionDefinitionV1(
            training_cutoff_at_utc=completed
        ),
        implementation_code_revision=q._code_revision(),
        build_recipe=ModelBuildRecipePinV1(
            recipe_id=prefix,
            recipe_hash=recipe_ref.evidence_sha256,
            evidence=recipe_ref,
        ),
        correction_context=pin,
        context_registered_at_utc=context.actual_at_utc,
    )


def test_actual_corrected_pilot_metadata_reservation_replay_and_exact_bridge(
    actual_admission, monkeypatch
):
    from football_system.application.quant_integrity import QuantIntegrityPilotService
    from football_system.domain.quant_integrity import IntegrityArtifactRefV1
    from football_system.infrastructure.database import quant_integrity_repository as q
    from football_system.infrastructure.database.models import (
        QuantIntegrityOutputRecord,
    )
    from sqlalchemy import select

    lane = actual_admission
    monkeypatch.setattr(
        q, "_code_revision", lambda: "package:controlled-pilot-test-only"
    )
    enable_corrections(lane, lane.admission)
    correction = record(lane, reviewed_intent(lane, raw={"home_goals": 0}))
    context = lane.corrections.load_context(
        base_admissions=(lane.base_pin,),
        correction_ids=(correction.artifact_id,),
        actual_at=lane.clock(),
    )
    definition = pilot_definition(lane, context)
    repo = q.SqlAlchemyQuantIntegrityRepository(
        lane.sessions,
        admission_repository=lane.repo,
        clock=lane.clock,
        operator_id="operator",
    )
    with pytest.raises(ValueError, match="reservation"):
        repo.load_verified_correction_context(
            definition.correction_context, at_utc=lane.clock()
        )
    service = QuantIntegrityPilotService(repo, lane.clock)
    with monkeypatch.context() as guarded:
        guarded.setattr(
            lane.repo,
            "_load",
            lambda *a, **k: pytest.fail("full target result read before reservation"),
        )
        plan = service.seal_plan(definition)
    report = service.run(IntegrityArtifactRefV1.of(plan))
    terminal = service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    with lane.sessions() as session:
        row = session.scalar(
            select(QuantIntegrityOutputRecord).where(
                QuantIntegrityOutputRecord.plan_id == plan.artifact_id
            )
        )
        output = q._artifact(row, q.QuantIntegrityOutputV1)
    assert output.content_payload.slices[1].state.training_result_ids == (
        "normalized-0",
    )
    assert (
        output.content_payload.terminal_state_core.content_payload.training_facts[
            0
        ].match_result_id
        == "corrected-1"
    )
    graph = prepare_versioned_training_history(
        admissions=(lane.admission,),
        correction_context=context,
        training_window=definition.training_window,
        integrity_pilot_scope_id=definition.integrity_pilot_scope_id,
        selection_cutoff_at_utc=definition.terminal_projection.training_cutoff_at_utc,
    )
    repo._bridge_history(plan, output, graph)
    with pytest.raises(ValueError, match="SYNTHETIC"):
        repo.technical_evidence(terminal.artifact_id, graph)
    assert repo.load_report(IntegrityArtifactRefV1.of(report)) == report
    wrong = (
        definition.slices[0]
        .content_payload.targets[0]
        .fact.model_copy(
            update={
                "version": definition.slices[0]
                .content_payload.targets[0]
                .fact.version.model_copy(update={"content_hash": "f" * 64})
            }
        )
    )
    target = (
        definition.slices[0]
        .content_payload.targets[0]
        .model_copy(update={"fact": wrong})
    )
    bad_slice = definition.slices[0].model_copy(
        update={
            "content_payload": definition.slices[0].content_payload.model_copy(
                update={"targets": (target,)}
            )
        }
    )
    with lane.sessions.begin() as session, pytest.raises(ValueError, match="target"):
        repo._versioned_metadata(
            session,
            definition.model_copy(
                update={"slices": (bad_slice, *definition.slices[1:])}
            ),
            lane.clock(),
        )
    report_wire = report.model_dump_json()
    previous = lane.corrections.load_version(correction.artifact_id)
    later = record(
        lane, reviewed_intent(lane, previous=previous, raw={"home_goals": 4})
    )
    assert later.content_payload.registered_at_utc > context.actual_at_utc
    assert (
        repo.load_report(IntegrityArtifactRefV1.of(report)).model_dump_json()
        == report_wire
    )


def test_controlled_season_and_kickoff_revision_preserves_anchor_and_changes_transition(
    corrected,
):
    from football_system.infrastructure.database.models import (
        ProviderCompetitionMappingRecord,
        MatchRecord,
    )
    from tests.integration.test_training_admission_persistence import SOURCE
    from decimal import Decimal

    ctx, lane = corrected, corrected.lane
    original = load_context(ctx)
    with lane.sessions.begin() as session:
        session.add(
            ProviderCompetitionMappingRecord(
                mapping_id="production-season-map",
                internal_competition_id="league",
                provider_id="provider",
                provider_competition_id="p-league",
                provider_competition_name="League",
                language="en",
                season="production",
                competition_type="LEAGUE",
                available_at_utc=SOURCE,
            )
        )
    kickoff = SOURCE + timedelta(days=3)
    source = SOURCE + timedelta(days=5)
    shared = {"season_id": "p-production", "kickoff_at_utc": kickoff.isoformat()}
    value = record(
        lane,
        reviewed_intent(
            lane,
            fixture=shared,
            scope={"season_id": "p-production"},
            raw={
                **shared,
                "available_at_utc": source.isoformat(),
                "observed_at_utc": source.isoformat(),
                "finalized_at_utc": (kickoff + timedelta(hours=3)).isoformat(),
            },
            evidence_changes={"competition_mapping_id": "production-season-map"},
        ),
    )
    context = load_context(ctx, (value.artifact_id,))
    assert selected(ctx, context, source - timedelta(seconds=1)) == selected(
        ctx, original, source - timedelta(seconds=1)
    )
    facts = selected(ctx, context, source)
    assert [
        (f.snapshot.stream.internal_match_id, f.snapshot.identity.season) for f in facts
    ] == [("match-1", "2024/25"), ("match-0", "production")]
    baseline = EloThreeWayBaseline()
    results = tuple(project_versioned_training_fact(v) for v in facts)
    before = baseline.rebuild_state(results[:1], source, target_season_id="2024/25")
    transition = baseline.rebuild_state(
        results[:1], source, target_season_id="production"
    )
    for team in before.teams:
        assert transition.for_team(team.team_id).rating == (
            Decimal(1500) + Decimal("0.75") * (team.rating - Decimal(1500))
        ).quantize(Decimal("0.000000000001"))
    with lane.sessions() as session:
        assert (
            session.get(MatchRecord, "match-0").kickoff_at_utc
            == lane.base.snapshot.identity.kickoff_at_utc
        )
    assert baseline.rebuild_state(
        results, source, target_season_id="production"
    ).training_result_ids == ("normalized-1", "corrected-1")
    from tests.contract.test_local_archive_providers import (
        _write_membership_archives,
        _edit_test_archive,
        _elo_history,
    )
    from copy import deepcopy

    lane.archive_root = lane.root / "archives"
    lane.archive_root.mkdir()
    _write_membership_archives(lane)

    def append_fixture(raw):
        revision = deepcopy(
            next(
                r
                for r in raw["records"]
                if r["payload"]["match"]["match_id"] == "match-0"
            )
        )
        revision["payload"]["match"]["kickoff_at_utc"] = kickoff.isoformat()
        raw["records"].append(revision)

    def append_result(raw):
        revision = deepcopy(
            next(
                r
                for r in raw["records"]
                if r["payload"]["match_result_id"] == "normalized-0"
            )
        )
        revision["payload"].update(
            value.content_payload.normalized_result.model_dump(
                mode="json", exclude={"schema_version"}
            )
        )
        raw["records"].append(revision)

    _edit_test_archive(lane, "fixtures", append_fixture)
    _edit_test_archive(lane, "results", append_result)
    memberships = RepositoryArchiveMembershipSource(
        lane.repo,
        (TrainingAdmissionPinV1.from_admission(ctx.admission),),
        lane.clock(),
        TrainingHistoryContextPinV2.of(context),
    )
    provider = HistoricalArchiveEloTrainingProvider(
        lane.archive_root,
        "PROVIDER",
        membership_source=memberships,
        ordered_season_ids=ctx.window.content_payload.ordered_season_ids,
    )
    early = _elo_history(
        provider, cutoff=source - timedelta(seconds=1), target="production"
    )
    late = _elo_history(provider, cutoff=source, target="production")
    assert tuple(s.result.match_result_id for s in early.sources) == (
        "normalized-0",
        "normalized-1",
    )
    assert tuple(s.result.match_result_id for s in late.sources) == (
        "normalized-1",
        "corrected-1",
    )
    assert late.sources[-1].result.kickoff_at_utc == kickoff


def test_f51_migration_matches_automatic_runtime_schema(corrected, tmp_path):
    from alembic import command
    from alembic.config import Config
    from football_system.infrastructure.database.session import create_database_engine
    from football_system.infrastructure.database.versioned_quant_schema import (
        versioned_quant_trigger_sql_v2,
    )

    url = f"sqlite:///{(tmp_path / 'f51.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "f51e294b0687")
    engine = create_database_engine(url)
    names = {*VERSIONED_QUANT_TABLES, *versioned_quant_trigger_sql_v2()}

    def signature(engine):
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            return {
                name: sql
                for name, sql in connection.exec_driver_sql(
                    "SELECT name,sql FROM sqlite_master"
                )
                if name in names
            }

    try:
        assert signature(engine) == signature(corrected.lane.engine)
        assert any(
            fk["referred_table"] == "training_fact_bindings"
            for fk in inspect(engine).get_foreign_keys(
                "training_history_version_context"
            )
        )
        assert any(
            fk["referred_table"] == "training_correction_admissions"
            for fk in inspect(engine).get_foreign_keys(
                "training_history_version_context"
            )
        )
        assert any(
            fk["referred_table"] == "training_fact_bindings"
            for fk in inspect(engine).get_foreign_keys("training_history_facts")
        )
    finally:
        engine.dispose()


def test_corrected_release_runs_live_audit_and_later_registration_preserves_old_run(
    lane, monkeypatch
):
    from tests.integration import test_production_inference as inf
    from tests.integration import test_production_audit as audits

    original_build = legacy.build

    def build_corrected(ctx):
        enable_corrections(ctx.lane, ctx.admission)
        ctx.bridges = SyntheticBridgeRegistry(ctx)
        ctx.repo.pilot_repository = ctx.bridges
        correction = record(ctx.lane, reviewed_intent(ctx.lane, raw={"home_goals": 0}))
        manifest = corrected_manifest(ctx, (correction.artifact_id,))
        return original_build(ctx, record_v2(ctx, manifest))

    with monkeypatch.context() as scoped:
        scoped.setattr(legacy, "build", build_corrected)
        ctx = inf.inference.__wrapped__(lane, monkeypatch)
    audited = audits.audited.__wrapped__(ctx, monkeypatch)
    artifacts = audited.artifacts
    assert artifacts.quant_model_evaluations[0].status == "AVAILABLE"
    assert (
        ctx.release.training_approval.schema_version == "TRAINING_HISTORY_APPROVAL_V2"
    )
    packet, _ = audits.export(audited)
    audit = audited.auditor.gate_run(audited.run_id)
    assert audit.content_payload.packet.artifact_id == packet.packet_id
    assert (
        audit.content_payload.approved_facts_hash
        == ctx.release.training_manifest.content_payload.history.approved_facts_hash
    )
    assert audits.counts(audited)["production_audit_bundles"] == 1
    before = artifacts.quant_model_states[0].model_dump_json()
    previous = ctx.release.content_payload.release_facts[0].content_payload.version
    record(lane, reviewed_intent(lane, previous=previous, raw={"home_goals": 4}))
    with pytest.raises(ValueError, match="correction"):
        audited.auditor.gate_run(audited.run_id)
    from football_system.application.ports.production_inference import (
        ProductionInferenceBindingV1,
    )
    from sqlalchemy import text

    release = ctx.production.repo.load_release(ctx.release.artifact_id)
    assert release == ctx.release
    with lane.sessions.begin() as session:
        session.execute(text("BEGIN"))
        assert session.scalar(
            text(
                "SELECT audit_json FROM production_audit_bundles WHERE analysis_run_id=:id"
            ),
            {"id": audited.run_id},
        ) == legacy.persistence.canonical_json(audit)
        assert (
            session.scalar(
                text(
                    "SELECT COUNT(*) FROM match_result_admissions WHERE match_result_id LIKE 'corrected-%'"
                )
            )
            == 0
        )
        wire = session.scalar(
            text(
                "SELECT binding_json FROM quant_model_state_production_releases WHERE analysis_run_id=:id"
            ),
            {"id": ctx.request.analysis_run_id},
        )
        assert (
            ProductionInferenceBindingV1.model_validate_json(wire)
            == artifacts.production_binding
        )
        with pytest.raises(ValueError, match="correction"):
            ctx.production.repo._assert_clean_sources(
                session, release.training_manifest.content_payload.history, lane.clock()
            )
    state = project_release_state(
        release, artifacts.analysis_run.as_of_at_utc, ("match",), "production"
    )
    assert (
        state.training_data_hash == artifacts.quant_model_states[0].training_data_hash
    )
    assert artifacts.quant_model_states[0].model_dump_json() == before


def test_f51_empty_downgrade_restores_prior_schema_guards_and_reupgrades(tmp_path):
    from alembic import command
    from alembic.config import Config
    from football_system.infrastructure.database.session import create_database_engine
    from tests.integration.test_database_schema import (
        _schema_signature,
        _trigger_signature,
    )

    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / 'empty-versioned.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    engine = create_database_engine(url)
    try:
        command.upgrade(config, "e40d183af576")
        prior = _schema_signature(engine), _trigger_signature(engine)
        command.upgrade(config, "head")
        head = _schema_signature(engine), _trigger_signature(engine)
        command.downgrade(config, "e40d183af576")
        assert (_schema_signature(engine), _trigger_signature(engine)) == prior
        command.upgrade(config, "head")
        assert (_schema_signature(engine), _trigger_signature(engine)) == head
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_f51_empty_version_graph_downgrade_preserves_populated_legacy_release(
    production,
):
    from alembic import command
    from alembic.config import Config

    release = legacy.build(production, record_v2(production))
    before = release.model_dump_json()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(production.lane.engine.url))
    command.stamp(config, "head")
    command.downgrade(config, "e40d183af576")
    assert production.repo.load_release(release.artifact_id).model_dump_json() == before
    command.upgrade(config, "head")
    assert production.repo.load_release(release.artifact_id).model_dump_json() == before


def test_f51_populated_downgrade_refusal_leaves_graph_and_guards_intact(corrected):
    from alembic import command
    from alembic.config import Config
    from tests.integration.test_database_schema import (
        _schema_signature,
        _trigger_signature,
    )

    value = record(corrected.lane, reviewed_intent(corrected.lane))
    manifest = corrected_manifest(corrected, (value.artifact_id,))
    release = legacy.build(corrected, record_v2(corrected, manifest))
    engine = corrected.lane.engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.stamp(config, "head")
    before = _schema_signature(engine), _trigger_signature(engine)
    with pytest.raises(RuntimeError, match="populated immutable versioned history"):
        command.downgrade(config, "e40d183af576")
    assert (_schema_signature(engine), _trigger_signature(engine)) == before
    assert corrected.repo.load_release(release.artifact_id) == release
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == "f51e294b0687"
        )


def test_f51_offline_unknown_downgrade_refused_before_ddl(tmp_path):
    from io import StringIO
    from alembic import command
    from alembic.config import Config

    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    path = tmp_path / "offline-unknown.db"
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    with pytest.raises(RuntimeError, match="offline.*empty graph"):
        command.downgrade(config, "f51e294b0687:e40d183af576", sql=True)
    assert "DROP " not in output.getvalue()
    assert not path.exists()


@pytest.mark.parametrize(
    "corruption",
    [
        "base-result",
        "correction-snapshot",
        "fact-hash",
        "selected-head",
        "missing-fact",
    ],
)
def test_sql_version_graph_rejects_inconsistent_children_before_repository_replay(
    corrected, monkeypatch, corruption
):
    import json

    value = record(corrected.lane, reviewed_intent(corrected.lane))
    insert = legacy.persistence._insert

    def corrupt(session, table, row):
        row = dict(row)
        document = json.loads(row["artifact_json"])
        if table == VERSIONED_QUANT_TABLES[0]:
            if corruption == "base-result" and row["revision_sequence"] == 0:
                document["normalized_result"]["match_result_id"] = "unbound-result"
            if corruption == "correction-snapshot" and row["revision_sequence"] > 0:
                document["snapshot"]["home_goals"] = 9
        elif table == VERSIONED_QUANT_TABLES[1] and row["fact_sequence"] == 0:
            if corruption == "missing-fact":
                return
            if corruption == "fact-hash":
                row["elo_fact_hash"] = "f" * 64
        elif table == "training_history_manifests" and corruption == "selected-head":
            document["content_payload"]["history"]["selected_heads"][0]["version"][
                "content_hash"
            ] = "f" * 64
        row["artifact_json"] = legacy.persistence.canonical_json(document)
        insert(session, table, row)

    with monkeypatch.context() as scoped:
        scoped.setattr(legacy.persistence, "_insert", corrupt)
        with pytest.raises(IntegrityError):
            corrected_manifest(corrected, (value.artifact_id,))
    with corrected.lane.engine.connect() as connection:
        assert all(
            connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
            == 0
            for table in VERSIONED_QUANT_TABLES
        )
