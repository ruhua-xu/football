"""CONTRACT_TEST_ONLY: invented native bytes/reviews in temporary SQLite.

Positive production cases simulate REAL_SOURCE-shaped contracts with genuine
persisted technical graphs and reviews. They grant no actual vendor rights.
The echo bridge below is adversarial test input, never production authority.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.application.run_analysis import _code_revision
from football_system.application.production_release import (
    prepare_observed_training_history,
    project_release_state,
)
from football_system.domain.archive import HistoricalDataMode, canonical_json
from football_system.domain.observed_training import (
    ObservedCapturePointerV1,
    ObservedScopeSeasonV1,
    ObservedSnapshotInputV1,
    observed_snapshot_root,
)
from football_system.domain.production_release import (
    EloTrainingWindowV1,
    EloTrainingWindowContentV1,
    TrainingSeasonV1,
    ProviderSeasonRefV1,
    ObservedFactRefV1,
    TechnicalEvidenceRefsV2,
    StrictWalkForwardUnavailableV1,
    ReleaseArtifactRefV1,
    RetentionHorizonV1,
    ProductionGrantKind,
)
from football_system.domain.quant_integrity import (
    ObservedIntegrityProvenanceV1,
    ObservedQuantIntegrityCohortV1,
    ObservedQuantIntegrityPlanDefinitionV1,
    TerminalProjectionDefinitionV1,
    ModelBuildRecipePinV1,
    IntegrityArtifactRefV1,
    integrity_attempt_root,
)
from football_system.domain.training_admission import TrainingCanonicalMatchIdentityV1
from football_system.infrastructure.database.models import (
    ProviderRecord,
    MatchRecord,
    CanonicalMatchIdentityRecord,
    ProviderTeamAliasRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    QuantIntegrityAttestationRecord,
    QuantIntegrityPlanRecord,
)
from football_system.infrastructure.database.observed_training_repository import (
    SqlAlchemyObservedTrainingRepository,
)
from football_system.infrastructure.database.quant_integrity_repository import (
    SqlAlchemyQuantIntegrityRepository,
    ObservedQuantIntegrityBuildRecipeV1,
)
from football_system.infrastructure.database.production_quant_repository import (
    SqlAlchemyProductionQuantRepository,
)
from football_system.infrastructure.database.observed_quant_schema import (
    OBSERVED_QUANT_TABLES,
)
from tests.integration import test_observed_training_admission as obs
from tests.integration import test_training_admission_persistence as admission
from tests.integration import test_production_inference as live_tests
from tests.integration import test_production_audit as audit_tests
from tests.integration.test_training_approval_v2 import record_v2

lane = admission.lane
END = datetime(2026, 12, 31, tzinfo=timezone.utc)


def horizon():
    return RetentionHorizonV1(indefinite=False, retain_until_at_utc=END)


class ContractTestOnlyTechnicalBridge:
    def __init__(self, pilot):
        self.pilot = pilot
        self.values = {}

    def technical_evidence(self, attestation_id, history, *, session):
        if attestation_id in self.values:
            expected, evidence = self.values[attestation_id]
            assert expected == history
            return evidence
        row = session.get(QuantIntegrityAttestationRecord, attestation_id)
        a, summary, plan, output, report, attempts = self.pilot._terminal_graph(
            session, row, self.pilot._now()
        )
        assert a.content_payload.provenance.evidence_use == "SYNTHETIC_CONTRACT_ONLY"
        self.pilot._bridge_history(plan, output, history)
        d = plan.content_payload.definition

        def ref(x):
            return ReleaseArtifactRefV1(
                artifact_id=x.artifact_id, content_hash=x.content_hash
            )

        evidence = TechnicalEvidenceRefsV2(
            integrity_pilot_scope_id=history.integrity_pilot_scope_id,
            integrity_pilot_series_id=d.integrity_pilot_series_id,
            scope=history.scope,
            plan=ref(plan),
            summary=ref(summary),
            attestation=ref(a),
            report=ref(report),
            attempt_count=len(attempts),
            attempt_root=integrity_attempt_root(attempts),
            source_root=history.source_root,
            season_root=history.season_root,
            approved_facts_hash=history.approved_facts_hash,
            training_data_hash=history.training_data_hash,
            terminal_state_core_hash=output.content_payload.terminal_state_core.content_hash,
            build_recipe=ReleaseArtifactRefV1(
                artifact_id=d.build_recipe.recipe_id,
                content_hash=d.build_recipe.recipe_hash,
            ),
            code_revision=d.implementation_code_revision,
            plan_sealed_at_utc=session.get(
                QuantIntegrityPlanRecord, plan.artifact_id
            ).persisted_at_utc,
            actual_started_at_utc=attempts[
                -1
            ].content_payload.reservation.content_payload.actual_started_at_utc,
            actual_completed_at_utc=attempts[
                -1
            ].content_payload.actual_completed_at_utc,
            attestation_persisted_at_utc=row.persisted_at_utc,
            strict_walk_forward=StrictWalkForwardUnavailableV1(metrics=None),
            observed_context_root=history.observed_context.base_root,
            scope_retention_deadline_utc=history.observed_context.scope.subject.retention_deadline_utc,
        )
        self.values[attestation_id] = history, evidence
        return evidence


@pytest.fixture
def observed_lane(lane, monkeypatch):
    return prepare_observed_lane(lane, monkeypatch)


def prepare_observed_lane(
    lane, monkeypatch, *, uses=("TRAINING", "VALIDATION"), count=6
):
    # Stable installed-package identity for a test running beside other agents.
    lane.code_revision = _code_revision()
    from football_system.infrastructure.database import (
        quant_integrity_repository,
        production_quant_repository,
    )

    for module in (
        quant_integrity_repository,
        production_quant_repository,
        live_tests.model_analysis,
    ):
        monkeypatch.setattr(module, "_code_revision", lambda: lane.code_revision)
    lane.clock.value = live_tests.MARKET_RECEIVED
    capture = live_tests._capture("pinned-inference")
    replacements = {
        "competition": "league",
        "competition-key": "league",
        "League": "Bundesliga",
        "GB": "DEU",
        "home-key": "home",
        "away-key": "away",
        "Home FC": "home",
        "Away FC": "away",
        "2026/27": "production",
        "LEAGUE": "DOMESTIC_LEAGUE",
    }

    def remap(value):
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [remap(v) for v in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    capture = type(capture).model_validate(remap(capture.model_dump(mode="python")))
    live_tests.SqlAlchemyMatchIdentityRepository(
        lane.sessions, clock=lane.clock
    ).register_fixture_ingestion(capture)
    lane.recorded = lane.repo.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    with lane.sessions.begin() as session:
        session.add(
            ProviderRecord(
                provider_id="sportmonks",
                code="SPORTMONKS",
                name="CONTRACT_TEST_ONLY",
                provider_kind="TEST",
            )
        )
        session.flush()
        for team, raw in (("home", "444"), ("away", "555")):
            session.add(
                ProviderTeamAliasRecord(
                    alias_id="sm-" + team,
                    internal_team_id=team,
                    provider_id="sportmonks",
                    provider_team_id=raw,
                    provider_team_name=team,
                    language="en",
                    team_type="CLUB",
                    available_at_utc=admission.SOURCE,
                )
            )
        session.add(
            ProviderCompetitionMappingRecord(
                mapping_id="sm-competition",
                internal_competition_id="league",
                provider_id="sportmonks",
                provider_competition_id="222",
                provider_competition_name="Bundesliga",
                language="en",
                season="pilot",
                competition_type="DOMESTIC_LEAGUE",
                available_at_utc=admission.SOURCE,
            )
        )
        for i in range(count):
            session.add(
                MatchRecord(
                    internal_match_id=f"sm-match-{i}",
                    competition_id="league",
                    home_team_id="home",
                    away_team_id="away",
                    kickoff_at_utc=admission.SOURCE + timedelta(days=i + 1),
                    status="FINISHED",
                    available_at_utc=admission.SOURCE,
                    created_at_utc=admission.LOCAL,
                )
            )
        session.flush()
        for i in range(count):
            session.add(
                CanonicalMatchIdentityRecord(
                    internal_match_id=f"sm-match-{i}",
                    season="pilot",
                    competition_type="DOMESTIC_LEAGUE",
                    available_at_utc=admission.SOURCE,
                )
            )
            session.add(
                ProviderMatchMappingRecord(
                    mapping_id=f"sm-mapping-{i}",
                    provider_id="sportmonks",
                    external_namespace="fixture",
                    external_match_id=str(100 + i),
                    internal_match_id=f"sm-match-{i}",
                    resolution_method="EXPLICIT_MAPPING",
                    confidence=1,
                    available_at_utc=admission.SOURCE,
                )
            )
    lane.observed = SqlAlchemyObservedTrainingRepository(lane.repo)
    permission = obs.strict_json_bytes(lane.repo.evidence.read("authority.json"))
    permission["attested_schema_versions"] = [
        obs.CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1,
        obs.OBSERVED_SNAPSHOT_SUBJECT_V1,
    ]
    lane.observed_authority = admission.write_evidence(
        lane.root, "observed-authority.json", permission
    )
    lane.repo.evidence.trusted_authorities[
        lane.observed_authority.evidence_reference
    ] = lane.observed_authority.evidence_sha256
    lane.serial = 0
    resolution = admission.write_evidence(
        lane.root,
        "observed-terms-resolution.json",
        dict(
            schema_version="CURRENT_SNAPSHOT_USER_TERMS_RESOLUTION_V1",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_rights_admission_hash=lane.recorded.admission_hash,
            source_id="source",
            terms_sha256=lane.rights.terms_sha256,
            permitted_uses=list(uses),
            retention_deadline_utc="2026-12-31T00:00:00Z",
            resolution="RESOLVED_FOR_DECLARED_SNAPSHOT_SCOPE",
        ),
    )
    subject = lane.observed.prepare_scope(
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        source_id="source",
        provider_competition_id="222",
        canonical_competition_id="league",
        seasons=(
            ObservedScopeSeasonV1(
                provider_season_id="333",
                canonical_season_id="pilot",
                expected_fixture_ids=tuple(str(100 + i) for i in range(count)),
                expected_fixture_count=count,
                exceptions=(),
            ),
        ),
        max_capture_receipts=20,
        max_snapshot_records=40,
        permitted_uses=uses,
        retention_deadline_utc=END,
        user_terms_resolution=resolution,
    )
    lane.scope = lane.observed.record_scope(
        request_key="scope",
        subject=subject,
        reviewer_attestation=obs.review(lane, subject),
    )
    raw = {
        "timezone": "UTC",
        "synthetic_notice": "CONTRACT_TEST_ONLY",
        "data": [obs.raw_fixture(i)["data"] for i in range(count)],
    }
    admission.write_evidence(lane.root, "shared-native.json", raw)
    receipt = lane.repo.capture_local_json(
        request_key="shared-native",
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        source_id="source",
        provider_code="SPORTMONKS",
        evidence_reference="shared-native.json",
    )
    inputs = []
    for i in range(count):
        ref = ObservedCapturePointerV1(
            capture_receipt_id=receipt.capture_receipt_id, record_pointer=f"/data/{i}"
        )
        inputs.append(
            ObservedSnapshotInputV1(
                fixture=ref,
                season=ref,
                result=ref,
                provider_mapping_id=f"sm-mapping-{i}",
                home_team_alias_id="sm-home",
                away_team_alias_id="sm-away",
                competition_mapping_id="sm-competition",
            )
        )
    lane.base = obs.admit(lane, tuple(inputs))
    lane.window = EloTrainingWindowV1.freeze(
        content_payload=EloTrainingWindowContentV1(
            competition_id="league",
            seasons=tuple(
                TrainingSeasonV1(
                    season_sequence=i,
                    season_id=season,
                    role=role,
                    provider_seasons=(
                        ProviderSeasonRefV1(
                            source_id="source",
                            provider_code="SPORTMONKS",
                            provider_competition_id="222",
                            provider_season_id=provider,
                        ),
                    ),
                )
                for i, (season, role, provider) in enumerate(
                    (
                        ("pilot", "PILOT_TARGET", "333"),
                        ("production", "PRODUCTION_TARGET", "334"),
                    )
                )
            ),
        )
    )
    lane.pilot = SqlAlchemyQuantIntegrityRepository(
        lane.sessions,
        admission_repository=lane.repo,
        operator_id="operator",
        clock=lane.clock,
    )
    return lane


def definition_for(
    lane, *, key="observed", previous=None, evidence_use="SYNTHETIC_CONTRACT_ONLY"
):
    context = lane.observed.load_context(lane.scope.scope_id)
    recipe = ObservedQuantIntegrityBuildRecipeV1(
        recipe_id="observed-fixed-recipe",
        implementation_code_revision=lane.code_revision,
        config_hash="c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4",
    )
    evidence = admission.write_evidence(lane.root, "observed-recipe.json", recipe)
    cutoff = context.actual_at_utc
    heads = context.select_heads(cutoff)
    return ObservedQuantIntegrityPlanDefinitionV1(
        integrity_pilot_series_id=key,
        previous_terminal_attestation=previous,
        provenance=ObservedIntegrityProvenanceV1(evidence_use=evidence_use),
        observed_context=context,
        training_window=lane.window,
        cohort=ObservedQuantIntegrityCohortV1(
            season_id="pilot",
            cohort_match_ids=tuple(sorted(r.identity.internal_match_id for r in heads)),
        ),
        targets=(
            TrainingCanonicalMatchIdentityV1(
                internal_match_id="match",
                internal_competition_id="league",
                internal_home_team_id="home",
                internal_away_team_id="away",
                kickoff_at_utc=live_tests.KICKOFF,
                season="production",
                competition_type="DOMESTIC_LEAGUE",
            ),
        ),
        terminal_projection=TerminalProjectionDefinitionV1(
            training_cutoff_at_utc=cutoff, exclude_match_ids=("match",)
        ),
        selected_heads=tuple(ObservedFactRefV1.of(r) for r in heads),
        selected_versions_root=observed_snapshot_root(heads),
        implementation_code_revision=lane.code_revision,
        build_recipe=ModelBuildRecipePinV1(
            recipe_id=recipe.recipe_id,
            recipe_hash=evidence.evidence_sha256,
            evidence=evidence,
        ),
    )


def run_integrity(
    lane, *, key="observed", previous=None, evidence_use="SYNTHETIC_CONTRACT_ONLY"
):
    definition = definition_for(
        lane, key=key, previous=previous, evidence_use=evidence_use
    )
    service = QuantIntegrityPilotService(lane.pilot, lane.clock)
    plan = service.seal_plan(definition)
    report = service.run(IntegrityArtifactRefV1.of(plan))
    terminal = service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    return SimpleNamespace(
        definition=definition,
        service=service,
        plan=plan,
        report=report,
        terminal=terminal,
    )


def build(lane, result, *, repo=None, predecessor=None, key="observed"):
    repo = repo or SqlAlchemyProductionQuantRepository(
        lane.sessions,
        admission_repository=lane.repo,
        pilot_repository=lane.pilot,
        operator_id="operator",
        clock=lane.clock,
    )
    with lane.sessions() as session:
        ids = tuple(
            session.scalars(
                select(
                    obs.repository_module.ObservedSnapshotAdmissionRecord.admission_id
                )
                .where(
                    obs.repository_module.ObservedSnapshotAdmissionRecord.scope_id
                    == lane.scope.scope_id
                )
                .order_by(
                    obs.repository_module.ObservedSnapshotAdmissionRecord.admission_sequence
                )
            )
        )
    manifest = repo.create_manifest(
        key + "-manifest",
        ids,
        lane.window,
        result.definition.integrity_pilot_scope_id,
        result.terminal.artifact_id,
        observed_context=result.definition.observed_context,
        selection_cutoff_at_utc=result.definition.terminal_projection.training_cutoff_at_utc,
        exclude_match_ids=("match",),
    )
    ctx = SimpleNamespace(repo=repo, lane=lane)
    updates = {
        kind: dict(
            expires_at_utc=END,
            **(
                dict(retention=horizon())
                if kind
                in (
                    ProductionGrantKind.DERIVED_MODEL_STATE_RETENTION,
                    ProductionGrantKind.AUDIT_HASH_RETENTION,
                )
                else {}
            ),
        )
        for kind in ProductionGrantKind
    }
    approval = record_v2(
        ctx, manifest, key=key + "-approval", updates=updates, predecessor=predecessor
    )
    release = repo.build_release(
        key + "-release",
        approval.artifact_id,
        result.definition.terminal_projection.training_cutoff_at_utc,
        horizon(),
        horizon(),
    )
    ctx.release, ctx.manifest = release, manifest
    return ctx


def test_official_observed_integrity_review_release_and_full_live_downstream(
    observed_lane, monkeypatch, tmp_path
):
    lane = observed_lane
    result = run_integrity(lane, evidence_use="REAL_SOURCE")
    c = result.report.content_payload
    assert (
        c.capture_receipt_count == 1
        and c.context_version_count == c.training_fact_count == 6
    )
    assert c.strict_walk_forward.model_dump() == {
        "status": "UNAVAILABLE",
        "reason": "UNPROVEN_HISTORICAL_VERSION_TIME",
        "metrics": None,
    }
    assert (
        c.probability_metrics
        is c.historical_model_availability
        is c.availability_denominator
        is c.calibration_observation_count
        is None
    )
    assert len(HistoricalDataMode) == 2
    assert not result.definition.observed_context.select_heads(
        admission.SOURCE + timedelta(days=100)
    )
    with pytest.raises(ValueError, match="closed"):
        result.service.run(IntegrityArtifactRefV1.of(result.plan))
    history = prepare_observed_training_history(
        admissions=(lane.base,),
        observed_context=result.definition.observed_context,
        source_rights_admission=lane.recorded,
        training_window=lane.window,
        integrity_pilot_scope_id=result.definition.integrity_pilot_scope_id,
        selection_cutoff_at_utc=result.definition.terminal_projection.training_cutoff_at_utc,
        exclude_match_ids=("match",),
    )
    assert (
        lane.pilot.technical_evidence(
            result.terminal.artifact_id, history
        ).observed_context_root
        == history.observed_context.base_root
    )
    production = build(lane, result)
    assert (
        production.repo.load_release(production.release.artifact_id)
        == production.release
    )
    live = live_tests.SqlAlchemyLiveSourceRepository(
        lane.sessions, clock=lambda: live_tests.PERSISTED
    )
    live.save_market_odds_ingestion(live_tests._market_capture())
    live.save_sporttery_ingestion(live_tests._sporttery_capture())
    lane.clock.value = live_tests.PERSISTED
    inference = SimpleNamespace(
        label="CONTRACT_TEST_ONLY",
        lane=lane,
        production=production,
        release=production.release,
        live=live,
        repository=live_tests.SqlAlchemyProductionInferenceRepository(
            lane.sessions, production_repository=production.repo, clock=lane.clock
        ),
    )
    inference.plan, inference.bundle = live_tests.plan_and_bundle(
        inference, live_tests.DECISION, "observed-targets"
    )
    inference.request = live_tests.request_for(
        inference, inference.plan, inference.bundle, "observed-live"
    )
    audited = audit_tests.audited.__wrapped__(inference, monkeypatch)
    packet, packet_json, review, fusion, revision = audit_tests.downstream(audited)
    audit = audited.auditor.gate_run(audited.run_id)
    assert (
        audit.content_payload.model_training_evidence_basis
        == "CURRENT_SNAPSHOT_OBSERVED"
    )
    assert "model_training_source_mode" not in audit.content_payload.model_dump()
    assert audit_tests.export(audited) == (packet, packet_json)
    assert audited.post.save_fusion_run(fusion) == fusion
    assert audited.post.save_portfolio_revision(revision) == revision
    path = audit_tests.export_production_audit_bundle(
        tmp_path / "observed-export",
        analysis_run_id=audited.run_id,
        review_repository=audited.review,
        audit_repository=audited.auditor,
    )
    assert (
        audit_tests.import_production_audit_bundle(
            path,
            audit_tests.review_bytes(packet),
            review_repository=audited.review,
            audit_repository=audited.auditor,
        )
        == review
    )
    first = project_release_state(
        production.release, live_tests.DECISION, ("match",), "production"
    )
    second = project_release_state(
        production.release,
        live_tests.DECISION + timedelta(hours=1),
        ("match",),
        "production",
    )
    assert (
        first.training_data_hash == second.training_data_hash
        and first.teams == second.teams
    )
    config = json.loads(audited.artifacts.analysis_run.config_json)
    assert (
        config["request"]["model_training_evidence_basis"]
        == "CURRENT_SNAPSHOT_OBSERVED"
    )
    assert "model_training_source_mode" not in config["request"]
    with lane.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    for table in OBSERVED_QUANT_TABLES:
        with pytest.raises(IntegrityError):
            with lane.engine.begin() as connection:
                connection.exec_driver_sql(f"DELETE FROM {table}")
    for damage in ("missing", "body"):
        with lane.sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            action = "delete" if damage == "missing" else "update"
            session.execute(
                text(
                    f"DROP TRIGGER trg_production_audit_observed_basis_append_only_{action}"
                )
            )
            session.execute(
                text(
                    "DELETE FROM production_audit_observed_basis"
                    if damage == "missing"
                    else "UPDATE production_audit_observed_basis SET artifact_json='{}'"
                )
            )
            with pytest.raises(ValueError, match="typed basis child"):
                audited.auditor.begin_in_session(session, audited.run_id)
            session.rollback()
    authority = obs.strict_json_bytes(lane.repo.evidence.read("authority.json"))
    authority["attested_schema_versions"] = ["PRODUCTION_REVOCATION_REQUEST_V1"]
    production.authority = admission.write_evidence(
        lane.root, "withdrawal-authority.json", authority
    )
    lane.repo.evidence.trusted_authorities[production.authority.evidence_reference] = (
        production.authority.evidence_sha256
    )
    live_tests.production_fixtures.revoke(
        production, production.release.training_approval, release=production.release
    )
    for operation in (
        lambda: audit_tests.export(audited),
        lambda: audited.post.save_fusion_run(fusion),
        lambda: audited.post.save_portfolio_revision(revision),
        lambda: asyncio.run(live_tests.service(inference).run(inference.request)),
    ):
        with pytest.raises(ValueError, match="revoked"):
            operation()


@pytest.mark.parametrize("status", ["FT", "CANCELLED"])
def test_correction_admission_clocks_old_replay_and_new_release(observed_lane, status):
    lane = observed_lane
    original = run_integrity(lane, evidence_use="REAL_SOURCE")
    production = build(lane, original)
    old = production.release
    wire = old.model_dump_json()
    previous = lane.observed.load_context(lane.scope.scope_id)
    candidate = obs.capture(
        lane, raw=obs.raw_fixture(goals=0, status=status)
    ).model_copy(
        update={"home_team_alias_id": "sm-home", "away_team_alias_id": "sm-away"}
    )
    capture_boundary = lane.clock()
    assert production.repo.authorization(old.artifact_id, capture_boundary)
    values = obs.reviewed(lane, (candidate,))
    before_admission = lane.clock()
    correction = obs.admit(lane, values=values, key="correction")
    current = lane.observed.load_context(lane.scope.scope_id)
    assert (
        current.select_heads(
            original.definition.terminal_projection.training_cutoff_at_utc
        )
        == previous.records
    )
    assert current.select_heads(correction.registered_at_utc) == previous.records
    assert (
        current.select_heads(correction.registered_at_utc + timedelta(microseconds=1))[
            0
        ]
        == correction.records[0]
    )
    with pytest.raises(ValueError):
        production.repo.authorization(old.artifact_id, before_admission)
    assert production.repo.load_release(old.artifact_id).model_dump_json() == wire
    with pytest.raises(ValueError, match="observed correction"):
        production.repo.authorization(old.artifact_id, lane.clock())
    corrected = run_integrity(
        lane,
        key="corrected",
        previous=IntegrityArtifactRefV1.of(original.terminal),
        evidence_use="REAL_SOURCE",
    )
    report = corrected.report.content_payload
    assert report.context_version_count == 7 and report.selected_head_count == 6
    assert report.withdrawn_head_count == int(status != "FT")
    assert report.training_fact_count == 6 - int(status != "FT")
    fresh = build(
        lane,
        corrected,
        repo=production.repo,
        predecessor=old.training_approval,
        key="corrected",
    )
    assert fresh.repo.load_release(fresh.release.artifact_id) == fresh.release
    assert (
        fresh.release.content_payload.released_state_core
        != old.content_payload.released_state_core
    )
    assert fresh.repo.load_release(old.artifact_id).model_dump_json() == wire
    assert (
        fresh.repo.authorization(fresh.release.artifact_id, lane.clock()).corrections
        == ()
    )
    result_ids = fresh.release.content_payload.released_state_core.content_payload.training_result_ids
    assert lane.base.records[0].normalized_result.match_result_id not in result_ids


def test_observed_plan_missing_child_tamper_and_transaction_rollback(
    observed_lane, monkeypatch
):
    lane = observed_lane
    result = run_integrity(lane)
    from football_system.infrastructure.database import observed_quant_schema

    definition = result.definition.model_copy(
        update={
            "integrity_pilot_series_id": "rollback",
            "previous_terminal_attestation": IntegrityArtifactRefV1.of(result.terminal),
        }
    )
    original = observed_quant_schema.append_observed_plan_children

    def fail_after_children(session, plan):
        original(session, plan)
        raise RuntimeError("injected after typed children")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            observed_quant_schema, "append_observed_plan_children", fail_after_children
        )
        with pytest.raises(RuntimeError, match="injected"):
            result.service.seal_plan(definition)
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM observed_quant_plan_contexts"))
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM production_quant_integrity_pilot_series")
            )
            == 1
        )
    with lane.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_observed_quant_plan_records_append_only_delete"
        )
        connection.exec_driver_sql(
            "DELETE FROM observed_quant_plan_records WHERE context_sequence=0"
        )
    with pytest.raises(ValueError, match="child count"):
        lane.pilot.load_plan(IntegrityArtifactRefV1.of(result.plan))


def test_changed_native_body_retains_failed_attempt(observed_lane):
    lane = observed_lane
    result = run_integrity(lane)
    next_definition = result.definition.model_copy(
        update={
            "integrity_pilot_series_id": "failure",
            "previous_terminal_attestation": IntegrityArtifactRefV1.of(result.terminal),
        }
    )
    plan = result.service.seal_plan(next_definition)
    admission.write_evidence(
        lane.root, "shared-native.json", {"notice": "CONTRACT_TEST_ONLY corrupt body"}
    )
    with pytest.raises(ValueError):
        result.service.run(IntegrityArtifactRefV1.of(plan))
    attempts = lane.pilot.list_attempts("failure")
    assert len(attempts) == 1 and attempts[0].content_payload.status == "FAILED"
    assert attempts[0].content_payload.failure is not None
    with pytest.raises(ValueError, match="last attempt|successful"):
        result.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))


def test_observed_result_risk_detection_survives_stripped_markers(
    observed_lane, monkeypatch
):
    lane = observed_lane
    production = build(lane, run_integrity(lane, evidence_use="REAL_SOURCE"))
    live = live_tests.SqlAlchemyLiveSourceRepository(
        lane.sessions, clock=lambda: live_tests.PERSISTED
    )
    live.save_market_odds_ingestion(live_tests._market_capture())
    live.save_sporttery_ingestion(live_tests._sporttery_capture())
    lane.clock.value = live_tests.PERSISTED
    inference = SimpleNamespace(
        lane=lane,
        production=production,
        release=production.release,
        live=live,
        repository=live_tests.SqlAlchemyProductionInferenceRepository(
            lane.sessions, production_repository=production.repo, clock=lane.clock
        ),
    )
    inference.plan, inference.bundle = live_tests.plan_and_bundle(
        inference, live_tests.DECISION, "stripped-plan"
    )
    inference.request = live_tests.request_for(
        inference, inference.plan, inference.bundle, "stripped-observed"
    )
    lane.clock.value = live_tests.CREATED
    artifacts = asyncio.run(
        live_tests.service(inference, repository=live_tests.RepositorySpy([])).run(
            inference.request
        )
    )
    config = json.loads(artifacts.analysis_run.config_json)
    for key in tuple(config["request"]):
        if (
            key.startswith(("production_", "model_training_"))
            or key == "decision_data_mode"
        ):
            del config["request"][key]
    config_json = canonical_json(config)
    stripped = artifacts.model_copy(
        update={
            "production_binding": None,
            "analysis_run": artifacts.analysis_run.model_copy(
                update={
                    "config_json": config_json,
                    "config_hash": hashlib.sha256(config_json.encode()).hexdigest(),
                }
            ),
        }
    )
    repository = live_tests.SqlAlchemyAnalysisRepository(lane.sessions)
    with pytest.raises(ValueError, match="observed/corrected"):
        repository.save_analysis(
            stripped, live_tests._sporttery_rules(live_tests.SETTINGS)
        )
    monkeypatch.setattr(
        repository, "_assert_model_training_sources", lambda *args: None
    )
    with pytest.raises(IntegrityError, match="admitted research training results"):
        repository.save_analysis(
            stripped, live_tests._sporttery_rules(live_tests.SETTINGS)
        )
    assert not any(live_tests.model_counts(inference).values())


def test_final_build_scope_expiry_rolls_back_release_and_children(observed_lane):
    lane = observed_lane
    result = run_integrity(lane, evidence_use="REAL_SOURCE")

    def expire_after_parent(connection, cursor, statement, parameters, context, many):
        if statement.upper().startswith("INSERT INTO PRODUCTION_QUANT_MODEL_RELEASES "):
            lane.clock.value = END

    event.listen(lane.engine, "after_cursor_execute", expire_after_parent)
    try:
        with pytest.raises(ValueError, match="ScopeRetention|grant is not active"):
            build(lane, result)
    finally:
        event.remove(lane.engine, "after_cursor_execute", expire_after_parent)
    with lane.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM production_quant_model_releases")
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM production_quant_model_release_observed_facts"
                )
            )
            == 0
        )


def test_observed_migration_follows_core_and_preserves_existing_rows(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from football_system.infrastructure.database.session import create_database_engine

    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "062f3a5c1798")
    engine = create_database_engine(url)
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO providers(provider_id,code,name,provider_kind) VALUES ('test','TEST','CONTRACT_TEST_ONLY','TEST')"
        )
        before = c.exec_driver_sql("SELECT * FROM providers").all()
    engine.dispose()
    command.upgrade(config, "17304b6d28a9")
    engine = create_database_engine(url)
    try:
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT * FROM providers").all() == before
            assert set(OBSERVED_QUANT_TABLES) <= set(inspect(c).get_table_names())
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()
    command.downgrade(config, "062f3a5c1798")
