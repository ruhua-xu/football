"""CONTRACT_TEST_ONLY regressions. No vendor rights or runtime bypass is granted.

REAL_SOURCE-shaped positive cases still run the concrete persisted graph verifier
and public review recorder. Deliberately false bridges/SQL are negative inputs.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.application.production_release import (
    prepare_observed_training_history,
)
from football_system.domain.common import stable_id
from football_system.domain.production_release import (
    ReleaseArtifactRefV1,
    TechnicalEvidenceRefsV2,
    StrictWalkForwardUnavailableV1,
    revalidate,
)
from football_system.domain.quant_integrity import (
    IntegrityArtifactRefV1,
    QuantIntegrityPlanV1,
    ObservedQuantIntegrityPlanContentV1,
)
from football_system.domain.training_admission import tagged_canonical_sha256
from football_system.infrastructure.database import (
    production_quant_repository as production,
)
from football_system.infrastructure.database.models import QuantIntegrityPlanRecord
from football_system.infrastructure.database.observed_quant_schema import (
    OBSERVED_QUANT_TABLES,
)
from tests.integration import test_observed_quant_workflow as workflow
from tests.integration import test_quant_integrity_persistence as legacy

lane = workflow.lane


@pytest.fixture
def observed(lane, monkeypatch):
    return workflow.prepare_observed_lane(lane, monkeypatch, count=2)


def corrected(lane, *, status="FT", key="correction"):
    item = workflow.obs.capture(
        lane, raw=workflow.obs.raw_fixture(goals=0, status=status)
    ).model_copy(
        update={"home_team_alias_id": "sm-home", "away_team_alias_id": "sm-away"}
    )
    return workflow.obs.admit(lane, (item,), key=key)


def history_for(lane, definition):
    admissions = tuple(lane.observed.load_admission(key) for key in admission_ids(lane))
    return prepare_observed_training_history(
        admissions=admissions,
        observed_context=definition.observed_context,
        source_rights_admission=lane.recorded,
        training_window=lane.window,
        integrity_pilot_scope_id=definition.integrity_pilot_scope_id,
        selection_cutoff_at_utc=definition.terminal_projection.training_cutoff_at_utc,
        exclude_match_ids=("match",),
    )


def admission_ids(lane):
    table = workflow.obs.repository_module.ObservedSnapshotAdmissionRecord
    with lane.sessions() as session:
        return tuple(
            session.scalars(
                select(table.admission_id)
                .where(table.scope_id == lane.scope.scope_id)
                .order_by(table.admission_sequence)
            )
        )


def manifest(lane, definition, attestation_id, bridge, key="finding-manifest"):
    repo = production.SqlAlchemyProductionQuantRepository(
        lane.sessions,
        admission_repository=lane.repo,
        pilot_repository=bridge,
        clock=lane.clock,
        operator_id="operator",
    )
    return repo.create_manifest(
        key,
        admission_ids(lane),
        lane.window,
        definition.integrity_pilot_scope_id,
        attestation_id,
        observed_context=definition.observed_context,
        selection_cutoff_at_utc=definition.terminal_projection.training_cutoff_at_utc,
        exclude_match_ids=("match",),
    )


def test_echo_with_no_persisted_pilot_cannot_authorize_manifest(observed):
    lane = observed
    definition = workflow.definition_for(lane, evidence_use="REAL_SOURCE")
    history = history_for(lane, definition)

    def ref(name):
        return ReleaseArtifactRefV1(
            artifact_id="fabricated-" + name, content_hash="a" * 64
        )

    evidence = TechnicalEvidenceRefsV2(
        integrity_pilot_scope_id=history.integrity_pilot_scope_id,
        integrity_pilot_series_id=definition.integrity_pilot_series_id,
        scope=history.scope,
        plan=ref("plan"),
        summary=ref("summary"),
        report=ref("report"),
        attestation=ref("attestation"),
        attempt_count=1,
        attempt_root="b" * 64,
        source_root=history.source_root,
        season_root=history.season_root,
        approved_facts_hash=history.approved_facts_hash,
        training_data_hash=history.training_data_hash,
        terminal_state_core_hash="c" * 64,
        build_recipe=production.observed_production_build_recipe_v1(
            definition.build_recipe.recipe_id
        ),
        code_revision=lane.code_revision,
        plan_sealed_at_utc=lane.clock(),
        actual_started_at_utc=lane.clock(),
        actual_completed_at_utc=lane.clock(),
        attestation_persisted_at_utc=lane.clock(),
        strict_walk_forward=StrictWalkForwardUnavailableV1(metrics=None),
        observed_context_root=history.observed_context.base_root,
        scope_retention_deadline_utc=workflow.END,
    )
    bridge = SimpleNamespace(technical_evidence=lambda *args, **kwargs: evidence)
    with pytest.raises(ValueError, match="missing|persisted|required"):
        manifest(lane, definition, evidence.attestation.artifact_id, bridge)
    with lane.engine.connect() as c:
        assert c.scalar(text("PRAGMA foreign_keys")) == 1
        for table in (
            "production_quant_integrity_pilot_attestations",
            "training_history_manifests",
            "training_history_approval_events",
            "production_quant_model_releases",
            OBSERVED_QUANT_TABLES[7],
        ):
            assert c.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0
        targets = {
            fk["referred_table"]
            for fk in inspect(c).get_foreign_keys(OBSERVED_QUANT_TABLES[7])
        }
        assert {
            "production_quant_integrity_pilot_" + role
            for role in ("plans", "reports", "summaries", "attestations")
        } <= targets


@pytest.mark.parametrize("bad_bridge", ["noop", "false_hash", "synthetic_echo"])
def test_actual_graph_and_real_provenance_cannot_be_replaced_by_bridge_claims(
    observed, bad_bridge, monkeypatch
):
    lane = observed
    result = workflow.run_integrity(
        lane,
        evidence_use="SYNTHETIC_CONTRACT_ONLY"
        if bad_bridge == "synthetic_echo"
        else "REAL_SOURCE",
    )
    if bad_bridge == "synthetic_echo":
        bridge = workflow.ContractTestOnlyTechnicalBridge(lane.pilot)
    elif bad_bridge == "noop":
        # Even the concrete injectable instance may have a replaced method.
        bridge = lane.pilot
        monkeypatch.setattr(bridge, "technical_evidence", lambda *args, **kwargs: None)
    else:
        value = lane.pilot.technical_evidence(
            result.terminal.artifact_id, history_for(lane, result.definition)
        )
        bridge = SimpleNamespace(
            technical_evidence=lambda *args, **kwargs: value.model_copy(
                update={"attempt_root": "0" * 64}
            )
        )
    with pytest.raises(
        ValueError, match="persisted real pilot|SYNTHETIC_CONTRACT_ONLY"
    ):
        manifest(lane, result.definition, result.terminal.artifact_id, bridge)
    with lane.engine.connect() as c:
        assert c.scalar(text("SELECT COUNT(*) FROM training_history_manifests")) == 0
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []


@pytest.mark.parametrize("uses", [("VALIDATION",), ("TRAINING",)])
def test_scope_uses_are_narrower_than_generic_rights_before_bytes_or_rebuild(
    lane, monkeypatch, uses
):
    lane = workflow.prepare_observed_lane(lane, monkeypatch, uses=uses, count=2)
    with pytest.raises(ValueError, match="scoped TRAINING and VALIDATION"):
        workflow.definition_for(lane)
    from football_system.infrastructure.database.quant_integrity_repository import (
        observed_scope_preflight,
    )

    reads = []
    original = lane.repo.evidence.read

    def read(*args, **kwargs):
        reads.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(lane.repo.evidence, "read", read)
    with lane.sessions.begin() as session:
        with pytest.raises(ValueError, match="TRAINING.*VALIDATION"):
            observed_scope_preflight(session, lane.scope.scope_id, lane.clock())
    assert reads == []
    repo = production.SqlAlchemyProductionQuantRepository(
        lane.sessions,
        admission_repository=lane.repo,
        pilot_repository=lane.pilot,
        operator_id="operator",
        clock=lane.clock,
    )
    with lane.sessions.begin() as session:
        with pytest.raises(ValueError, match="TRAINING.*VALIDATION"):
            repo._observed_training_preflight(
                session, lane.scope.scope_id, (lane.base.admission_id,), lane.clock()
            )
    assert reads == []
    with lane.engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT COUNT(*) FROM production_quant_integrity_pilot_attempts")
            )
            == 0
        )
        assert (
            c.scalar(text("SELECT COUNT(*) FROM production_quant_model_releases")) == 0
        )


def test_operational_metadata_uses_actual_now_before_any_evidence_io(
    observed, monkeypatch
):
    lane = observed
    definition = workflow.definition_for(lane)
    original_at = lane.clock()
    lane.clock.value = workflow.END
    reads = []
    monkeypatch.setattr(
        lane.repo.evidence, "read", lambda *args, **kwargs: reads.append(args[0])
    )

    def no_stored_plan_data(*args, **kwargs):
        raise AssertionError("scope preflight must precede persisted plan data reads")

    monkeypatch.setattr(lane.pilot, "_series_plans", no_stored_plan_data)
    with pytest.raises(ValueError, match="ScopeRetention"):
        lane.pilot.verify_plan_metadata(definition, at_utc=original_at)
    with pytest.raises(ValueError, match="ScopeRetention"):
        lane.pilot.load_verified_observed_context(definition, at_utc=original_at)
    assert reads == []


@pytest.mark.parametrize("expiry", [None, "scope", "rights"])
@pytest.mark.parametrize("retry", [False, True], ids=["metadata", "plan-retry"])
def test_public_metadata_final_gate_follows_evidence_io_without_plan_writes(
    observed, monkeypatch, expiry, retry
):
    lane = observed
    definition = workflow.definition_for(lane)
    plan = (
        QuantIntegrityPilotService(lane.pilot, lane.clock).seal_plan(definition)
        if retry
        else None
    )
    requested_at = lane.clock()
    boundary = (
        lane.scope.subject.retention_deadline_utc
        if expiry == "scope"
        else lane.recorded.content_payload.rights_payload.expires_at_utc
        if expiry == "rights"
        else None
    )
    operations, reads = [], []
    original_read, original_clock = lane.repo.evidence.read, lane.pilot._clock

    def read(reference, *args, **kwargs):
        payload = original_read(reference, *args, **kwargs)
        reads.append(reference)
        operations.append(("evidence", reference))
        if len(reads) == 1 and boundary is not None:
            lane.clock.value = boundary
        return payload

    def clock():
        at = boundary if reads and boundary is not None else original_clock()
        operations.append(("clock", at))
        return at

    def statement(connection, cursor, sql, parameters, context, many):
        operations.append(("sql", sql))

    def verify():
        if retry:
            lane.pilot.seal_plan(plan)
        else:
            lane.pilot.verify_plan_metadata(definition, at_utc=requested_at)

    monkeypatch.setattr(lane.repo.evidence, "read", read)
    monkeypatch.setattr(lane.pilot, "_clock", clock)
    event.listen(lane.engine, "before_cursor_execute", statement)
    try:
        if expiry is None:
            verify()
        else:
            with pytest.raises(
                ValueError,
                match="ScopeRetention" if expiry == "scope" else "not active",
            ):
                verify()
    finally:
        event.remove(lane.engine, "before_cursor_execute", statement)

    assert len(reads) > 1 and "shared-native.json" in reads
    assert operations[-1][0] == "clock"
    assert operations[-1][1] > requested_at
    if boundary is not None:
        assert operations[-1][1] == boundary
    with lane.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM production_quant_integrity_pilot_plans")
        ) == int(retry)
        assert connection.scalar(
            text("SELECT COUNT(*) FROM observed_quant_plan_contexts")
        ) == int(retry)


@pytest.mark.parametrize(
    ("prior_status", "expiry"),
    [
        ("COMPLETED", None),
        ("COMPLETED", "evidence"),
        ("COMPLETED", "attestation-insert"),
        ("FAILED", "attestation-insert"),
        ("UNATTEMPTED", "attestation-insert"),
    ],
)
def test_terminal_final_gate_retains_every_observed_scope(
    observed, monkeypatch, prior_status, expiry
):
    lane = observed
    service = QuantIntegrityPilotService(lane.pilot, lane.clock)
    first = service.seal_plan(workflow.definition_for(lane, evidence_use="REAL_SOURCE"))
    if prior_status == "COMPLETED":
        service.run(IntegrityArtifactRefV1.of(first))
    elif prior_status == "FAILED":
        reservation = lane.pilot.reserve_attempt(
            IntegrityArtifactRefV1.of(first), actual_started_at_utc=lane.clock()
        )
        lane.pilot.recover_pending_attempt(reservation.artifact_id)
    first_scope = lane.scope

    # A disjoint reviewed cohort can share a pilot series, not a retention deadline.
    with lane.sessions.begin() as session:
        session.add(
            workflow.MatchRecord(
                internal_match_id="sm-match-2",
                competition_id="league",
                home_team_id="home",
                away_team_id="away",
                kickoff_at_utc=workflow.admission.SOURCE + timedelta(days=3),
                status="FINISHED",
                available_at_utc=workflow.admission.SOURCE,
                created_at_utc=workflow.admission.LOCAL,
            )
        )
        session.flush()
        session.add(
            workflow.CanonicalMatchIdentityRecord(
                internal_match_id="sm-match-2",
                season="pilot",
                competition_type="DOMESTIC_LEAGUE",
                available_at_utc=workflow.admission.SOURCE,
            )
        )
        session.add(
            workflow.ProviderMatchMappingRecord(
                mapping_id="sm-mapping-2",
                provider_id="sportmonks",
                external_namespace="fixture",
                external_match_id="102",
                internal_match_id="sm-match-2",
                resolution_method="EXPLICIT_MAPPING",
                confidence=1,
                available_at_utc=workflow.admission.SOURCE,
            )
        )
    resolution = workflow.admission.write_evidence(
        lane.root,
        "second-terms-resolution.json",
        dict(
            schema_version="CURRENT_SNAPSHOT_USER_TERMS_RESOLUTION_V1",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_rights_admission_hash=lane.recorded.admission_hash,
            source_id="source",
            terms_sha256=lane.rights.terms_sha256,
            permitted_uses=["TRAINING", "VALIDATION"],
            retention_deadline_utc="2026-12-31T01:00:00Z",
            resolution="RESOLVED_FOR_DECLARED_SNAPSHOT_SCOPE",
        ),
    )
    subject = lane.observed.prepare_scope(
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        source_id="source",
        provider_competition_id="222",
        canonical_competition_id="league",
        seasons=(
            workflow.ObservedScopeSeasonV1(
                provider_season_id="333",
                canonical_season_id="pilot",
                expected_fixture_ids=("102",),
                expected_fixture_count=1,
                exceptions=(),
            ),
        ),
        max_capture_receipts=20,
        max_snapshot_records=40,
        permitted_uses=("TRAINING", "VALIDATION"),
        retention_deadline_utc=workflow.END + timedelta(hours=1),
        user_terms_resolution=resolution,
    )
    lane.scope = lane.observed.record_scope(
        request_key="second-scope",
        subject=subject,
        reviewer_attestation=workflow.obs.review(lane, subject),
    )
    item = workflow.obs.capture(lane, index=2).model_copy(
        update={"home_team_alias_id": "sm-home", "away_team_alias_id": "sm-away"}
    )
    workflow.obs.admit(lane, (item,), key="second-admission")
    second = service.seal_plan(
        workflow.definition_for(lane, evidence_use="REAL_SOURCE")
    )
    service.run(IntegrityArtifactRefV1.of(second))

    operations, expired = [], []
    original_read, original_clock = lane.repo.evidence.read, lane.pilot._clock

    def read(reference, *args, **kwargs):
        payload = original_read(reference, *args, **kwargs)
        operations.append(("evidence", reference))
        if expiry == "evidence" and reference == "shared-native.json":
            lane.clock.value = first_scope.subject.retention_deadline_utc
            expired.append(reference)
        return payload

    def clock():
        at = lane.clock.value if expired else original_clock()
        operations.append(("clock", at))
        return at

    def statement(connection, cursor, sql, parameters, context, many):
        operations.append(("sql", sql))
        if expiry == "attestation-insert" and sql.upper().startswith(
            "INSERT INTO PRODUCTION_QUANT_INTEGRITY_PILOT_ATTESTATIONS "
        ):
            lane.clock.value = first_scope.subject.retention_deadline_utc
            expired.append(sql)

    monkeypatch.setattr(lane.repo.evidence, "read", read)
    monkeypatch.setattr(lane.pilot, "_clock", clock)
    event.listen(lane.engine, "after_cursor_execute", statement)
    try:
        if expiry is None:
            terminal = service.seal_terminal_attestation(
                IntegrityArtifactRefV1.of(second)
            )
            assert terminal.content_payload.attempt_count == 2
        else:
            with pytest.raises(ValueError, match="ScopeRetention"):
                service.seal_terminal_attestation(IntegrityArtifactRefV1.of(second))
    finally:
        event.remove(lane.engine, "after_cursor_execute", statement)
    assert operations[-1][0] == "clock"
    if expiry is not None:
        assert expired
        assert (
            first_scope.subject.retention_deadline_utc
            <= operations[-1][1]
            < lane.scope.subject.retention_deadline_utc
        )
    with lane.engine.connect() as connection:
        for table in (
            "production_quant_integrity_pilot_summaries",
            "production_quant_integrity_pilot_attestations",
        ):
            assert connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) == int(
                expiry is None
            )
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_pending_observed_reservation_does_not_block_legacy_v1_run(
    observed, monkeypatch, tmp_path
):
    lane = observed
    service = QuantIntegrityPilotService(lane.pilot, lane.clock)
    plan = service.seal_plan(workflow.definition_for(lane))
    lane.pilot.reserve_attempt(
        IntegrityArtifactRefV1.of(plan), actual_started_at_utc=lane.clock()
    )
    lane.clock.value = max(lane.clock.value, legacy.NOW) + timedelta(seconds=1)
    monkeypatch.setattr(legacy, "_code_revision", lambda: lane.code_revision)
    fixture = legacy.pilot.__wrapped__(tmp_path)
    old = next(fixture)
    try:
        monkeypatch.setattr(old.repo, "_sessions", lane.sessions)
        monkeypatch.setattr(old.repo, "_clock", lane.clock)
        service = QuantIntegrityPilotService(old.repo, lane.clock)
        legacy_plan = service.seal_plan(old.definition)
        report = service.run(IntegrityArtifactRefV1.of(legacy_plan))
        assert old.repo.load_report(IntegrityArtifactRefV1.of(report)) == report
        assert [
            a.content_payload.status
            for a in old.repo.list_attempts(old.definition.integrity_pilot_series_id)
        ] == ["COMPLETED"]
        assert (
            lane.pilot.list_attempts(
                plan.content_payload.definition.integrity_pilot_series_id
            )
            == ()
        )
    finally:
        fixture.close()
        old.engine.dispose()


def test_completed_old_attempt_and_corrected_later_attempt_close_one_series(observed):
    lane = observed
    service = QuantIntegrityPilotService(lane.pilot, lane.clock)
    first = service.seal_plan(workflow.definition_for(lane, evidence_use="REAL_SOURCE"))
    first_report = service.run(IntegrityArtifactRefV1.of(first))
    corrected(lane)
    second = service.seal_plan(
        workflow.definition_for(lane, evidence_use="REAL_SOURCE")
    )
    second_report = service.run(IntegrityArtifactRefV1.of(second))
    terminal = service.seal_terminal_attestation(IntegrityArtifactRefV1.of(second))
    assert terminal.content_payload.attempt_count == 2
    assert (
        lane.pilot.load_report(IntegrityArtifactRefV1.of(first_report)) == first_report
    )
    result = SimpleNamespace(
        definition=second.content_payload.definition, terminal=terminal
    )
    value = manifest(lane, result.definition, terminal.artifact_id, lane.pilot)
    assert value.content_payload.technical_evidence.attempt_count == 2
    assert (
        value.content_payload.technical_evidence.report.artifact_id
        == second_report.artifact_id
    )
    # Every prior completed report remains verified, not just the terminal one.
    with lane.sessions.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        session.execute(
            text(
                "DROP TRIGGER trg_production_quant_integrity_pilot_reports_append_only_update"
            )
        )
        session.execute(
            text(
                "UPDATE production_quant_integrity_pilot_reports SET artifact_json=json_set(artifact_json, '$.content_payload.capture_receipt_count', 99) WHERE artifact_id=:id"
            ),
            {"id": first_report.artifact_id},
        )
        with pytest.raises(ValueError):
            lane.pilot.technical_evidence(
                terminal.artifact_id, value.content_payload.history, session=session
            )
        session.rollback()


def test_same_timestamp_later_admission_does_not_rewrite_captured_build_prefix(
    observed, monkeypatch
):
    lane = observed
    original = workflow.run_integrity(lane, evidence_use="REAL_SOURCE")
    lane.clock.value += timedelta(minutes=1)
    with monkeypatch.context() as fixed:
        fixed.setattr(type(lane.clock), "__call__", lambda self: self.value)
        built = workflow.build(lane, original)
        release = built.release
        wire = release.model_dump_json()
        event = corrected(lane)
        assert (
            event.registered_at_utc
            == release.content_payload.build_completed_at_utc
            == release.content_payload.persisted_at_utc
        )
        assert (
            release.content_payload.build_completion_authorization.content_payload.current.observed_context.admission_prefix.admission_high_watermark
            == 0
        )
        assert built.repo.load_release(release.artifact_id).model_dump_json() == wire
        with pytest.raises(ValueError, match="correction"):
            built.repo.build_release(
                "stale-new-build",
                release.training_approval.artifact_id,
                release.content_payload.training_cutoff_at_utc,
                workflow.horizon(),
                workflow.horizon(),
            )
        inference = workflow.live_tests.SqlAlchemyProductionInferenceRepository(
            lane.sessions, production_repository=built.repo, clock=lane.clock
        )
        with pytest.raises(ValueError, match="correction"):
            inference.authorization(release.artifact_id, lane.clock())
        # A caller cannot invent a fresh release with the old, valid prefix.
        request = production._request(
            "build_release",
            "sql-stale-prefix",
            "operator",
            approval_id=release.training_approval.artifact_id,
            training_cutoff_at_utc=release.content_payload.training_cutoff_at_utc,
            state_retention_horizon=workflow.horizon(),
            audit_retention_horizon=workflow.horizon(),
        )
        stale = type(release).freeze(
            content_payload=release.content_payload.model_copy(
                update={"persisted_at_utc": lane.clock() + timedelta(seconds=1)}
            ),
            training_manifest=release.training_manifest,
            training_approval=release.training_approval,
        )
        with pytest.raises(IntegrityError, match="observed release"):
            with lane.sessions.begin() as session:
                for name, row in production._release_children(stale):
                    production._insert(session, name, row)
                production._insert(
                    session,
                    "production_quant_model_releases",
                    production._release_row(stale, request),
                )
    updated = workflow.run_integrity(
        lane,
        key="corrected",
        previous=IntegrityArtifactRefV1.of(original.terminal),
        evidence_use="REAL_SOURCE",
    )
    fresh = workflow.build(
        lane,
        updated,
        repo=built.repo,
        predecessor=release.training_approval,
        key="corrected",
    )
    current = fresh.repo.authorization(fresh.release.artifact_id, lane.clock())
    assert current.observed_context.admission_prefix.admission_high_watermark == 1
    assert fresh.repo.load_release(release.artifact_id).model_dump_json() == wire
    with lane.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def sql_plan(lane, plan, definition):
    payload = ObservedQuantIntegrityPlanContentV1.model_construct(
        definition=definition,
        input_roots=definition.input_roots,
        sealed_at_utc=lane.clock(),
    )
    digest = tagged_canonical_sha256(plan.schema_version, payload)
    forged = QuantIntegrityPlanV1.model_construct(
        artifact_id=stable_id(plan.schema_version, digest),
        content_hash=digest,
        content_payload=payload,
    )
    with lane.sessions.begin() as session:
        lane.pilot._append(
            session,
            QuantIntegrityPlanRecord,
            forged,
            series_id=definition.integrity_pilot_series_id,
            scope_hash=definition.scope_hash,
            evidence_use=definition.provenance.evidence_use.value,
            sealed_at_utc=payload.sealed_at_utc,
        )
    return forged


@pytest.mark.parametrize("microseconds", [0, 1])
def test_sql_plan_strict_cutoff_includes_microsecond_precision(observed, microseconds):
    lane = observed
    service = QuantIntegrityPilotService(lane.pilot, lane.clock)
    definition = workflow.definition_for(lane)
    plan = service.seal_plan(definition)
    cutoff = lane.base.registered_at_utc + timedelta(microseconds=microseconds)
    changed = definition.model_copy(
        update={
            "terminal_projection": definition.terminal_projection.model_copy(
                update={"training_cutoff_at_utc": cutoff}
            )
        }
    )
    if microseconds == 0:
        assert definition.observed_context.select_heads(cutoff) == ()
        with pytest.raises(IntegrityError, match="observed plan requires"):
            sql_plan(lane, plan, changed)
    else:
        value = sql_plan(lane, plan, changed)
        assert lane.pilot.load_plan(IntegrityArtifactRefV1.of(value)) == value
    with lane.engine.connect() as c:
        assert c.scalar(text("PRAGMA foreign_keys")) == 1
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_sql_plan_cannot_fall_back_behind_an_eligible_withdrawal(observed):
    lane = observed
    original = lane.base.records
    corrected(lane, status="CANCELLED")
    definition = workflow.definition_for(lane)
    plan = QuantIntegrityPilotService(lane.pilot, lane.clock).seal_plan(definition)
    changed = definition.model_copy(
        update={
            "selected_heads": tuple(workflow.ObservedFactRefV1.of(r) for r in original),
            "selected_versions_root": workflow.observed_snapshot_root(original),
        }
    )
    with pytest.raises(IntegrityError, match="observed plan requires"):
        sql_plan(lane, plan, changed)


def test_sql_release_requires_exact_microsecond_cutoff(observed):
    lane = observed
    lane.clock.value = lane.clock.value.replace(microsecond=123456)
    built = workflow.build(
        lane, workflow.run_integrity(lane, evidence_use="REAL_SOURCE")
    )
    release = built.release
    for microseconds in (-1, 1, 0):
        cutoff = release.content_payload.training_cutoff_at_utc + timedelta(
            microseconds=microseconds
        )
        core = release.content_payload.released_state_core
        core = type(core).freeze(
            content_payload=core.content_payload.model_copy(
                update={"training_cutoff_at_utc": cutoff}
            )
        )
        payload = release.content_payload.model_copy(
            update={"released_state_core": core, "persisted_at_utc": lane.clock()}
        )
        digest = tagged_canonical_sha256(release.schema_version, revalidate(payload))
        candidate = type(release).model_construct(
            artifact_id=stable_id(release.schema_version, digest),
            content_hash=digest,
            content_payload=payload,
            training_manifest=release.training_manifest,
            training_approval=release.training_approval,
        )
        request = production._request(
            "build_release",
            f"sql-microsecond-release-{microseconds}",
            "operator",
            approval_id=release.training_approval.artifact_id,
            training_cutoff_at_utc=cutoff,
            state_retention_horizon=workflow.horizon(),
            audit_retention_horizon=workflow.horizon(),
        )

        def insert():
            with lane.sessions.begin() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                assert session.scalar(text("PRAGMA foreign_keys")) == 1
                assert (
                    session.scalar(
                        text(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='trg_production_quant_model_releases_observed_complete_insert'"
                        )
                    )
                    == 1
                )
                for name, row in production._release_children(candidate):
                    production._insert(session, name, row)
                production._insert(
                    session,
                    "production_quant_model_releases",
                    production._release_row(candidate, request),
                )

        if microseconds:
            with pytest.raises(IntegrityError, match="observed release"):
                insert()
        else:
            insert()
            assert built.repo.load_release(candidate.artifact_id) == candidate
        with lane.engine.connect() as connection:
            expected = 1 + int(microseconds == 0)
            for table, children in (
                ("production_quant_model_releases", 1),
                (
                    "production_quant_model_release_observed_facts",
                    len(payload.release_facts),
                ),
                ("production_observed_release_prefixes", 2),
            ):
                assert (
                    connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
                    == expected * children
                )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
