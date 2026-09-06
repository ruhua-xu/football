"""SYNTHETIC_CONTRACT_ONLY: durable pilot tests, never production activation.

The injected admission adapter below is deliberately marked contract-only. It
cannot return production technical evidence. Scope/schedule/reviewer/recipe bytes
are actual controlled temporary files verified by the real pilot repository.
"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.production_release import prepare_training_history
from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.application.run_analysis import _code_revision
from football_system.domain.archive import canonical_json
from football_system.domain.market import SelectionKey
from football_system.domain.quant_integrity import (
    IntegrityArtifactRefV1,
    IntegrityEvidenceUse,
    ModelBuildRecipePinV1,
    QuantIntegrityAttemptV1,
    QuantIntegrityAttemptContentV1,
    QuantIntegrityAttemptReservationV1,
    QuantIntegrityAttestationV1,
    QuantIntegrityFailureV1,
    QuantIntegrityOutputV1,
    QuantIntegrityPlanV1,
    QuantIntegrityPlanContentV1,
    QuantIntegrityInputRootsV1,
    QuantIntegrityReportV1,
    QuantIntegritySummaryV1,
    QuantIntegritySummaryContentV1,
    AdmittedFactRefV1,
    QuantIntegritySliceContentV1,
    QuantIntegritySliceV1,
    QuantIntegrityTargetV1,
    ReviewedProviderSeasonV1,
    TrainingAdmissionPinV1,
    integrity_attempt_root,
)
from football_system.domain.training_admission import (
    TRAINING_FACT_REQUIRED_USES,
    LocalReviewEvidenceV1,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
    QuantIntegrityAttemptRecord,
    QuantIntegrityAttestationRecord,
    QuantIntegrityOutputRecord,
    QuantIntegrityPlanRecord,
    QuantIntegrityReportRecord,
    QuantIntegrityReservationRecord,
    QuantIntegritySummaryRecord,
)
from football_system.infrastructure.database.quant_integrity_repository import (
    QuantIntegrityBuildRecipeV1,
    QuantIntegrityReviewDocumentV1,
    QuantIntegrityScheduleDocumentV1,
    QuantIntegrityScopeDocumentV1,
    QuantIntegrityScopeRecordV1,
    SqlAlchemyQuantIntegrityRepository,
    _row,
)
from football_system.infrastructure.database.quant_integrity_schema import (
    QUANT_INTEGRITY_TABLES,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.database.training_admission_repository import (
    TrainingCaptureReceiptV1,
)
from football_system.infrastructure.files.training_evidence import LocalTrainingEvidence
from tests.unit.test_quant_integrity import (
    ADMITTED,
    CREATED,
    IMPORTED,
    NOW,
    PILOT_START,
    REGISTERED,
    REVIEWED,
    SyntheticClock,
    _admission,
    _definition,
)
from tests.integration import test_training_admission_persistence as admission_tests


def write(root, name, value):
    payload = (
        value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    )
    (root / name).write_bytes(payload)
    return LocalReviewEvidenceV1(
        evidence_reference=name, evidence_sha256=hashlib.sha256(payload).hexdigest()
    )


class ContractAdmissionRepository:
    synthetic_contract_only = True

    def __init__(self, value, evidence):
        self.value, self.evidence = value, evidence
        self.captures = {}
        self.result_reads = 0
        self.fail_results = False
        self.metadata = None

    def verify_synthetic_quant_metadata(self, definition, *, at_utc):
        assert (
            definition.provenance.evidence_use
            == IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY
        )
        if (
            definition.admissions
            != (TrainingAdmissionPinV1.from_admission(self.value),)
            or definition.cohort != self.metadata.cohort
            or definition.scope != self.metadata.scope
        ):
            raise ValueError("synthetic opaque metadata mismatch")
        assert ADMITTED <= at_utc

    def load(self, admission_id):
        self.result_reads += 1
        if self.fail_results:
            raise ValueError("synthetic result archive missing")
        assert self.value.training_fact_admission_id == admission_id
        return self.value

    def load_rights(self, admission_id):
        rights = self.value.source_rights_admission
        assert rights.source_rights_admission_id == admission_id
        return rights

    def load_capture(self, receipt_id):
        receipt = self.captures[receipt_id]
        return receipt, self.evidence.read(
            receipt.evidence_reference, receipt.payload_sha256
        )

    def synthetic_match_id(self, fixture_key):
        return {
            "key-pilot-a": "pilot-a",
            "key-pilot-b": "pilot-b",
            "key-pilot-c": "pilot-c",
        }[fixture_key]


def captured(adapter, root, name, document):
    evidence = write(root, name, document)
    receipt = TrainingCaptureReceiptV1.freeze(
        request_sha256=evidence.evidence_sha256,
        source_rights_admission_id=adapter.value.source_rights_admission.source_rights_admission_id,
        source_id="synthetic-source",
        provider_code="synthetic-provider",
        evidence_reference=evidence.evidence_reference,
        payload_sha256=evidence.evidence_sha256,
        local_imported_at_utc=IMPORTED,
        archive_created_at_utc=CREATED,
        registered_at_utc=REGISTERED,
        imported_by="operator",
    )
    adapter.captures[receipt.capture_receipt_id] = receipt
    return receipt


def reviewed(root, name, schema, payload):
    return write(
        root,
        name,
        QuantIntegrityReviewDocumentV1(
            attested_schema_version=schema,
            attested_payload_hash=tagged_canonical_sha256(schema, payload),
            prepared_by="operator",
            authorized_reviewer="pilot-reviewer",
            reviewed_at_utc=REVIEWED,
            source_ids=("synthetic-source",),
            evidence_use=IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY,
            accepted_for_internal_integrity=True,
        ),
    )


@pytest.fixture
def pilot(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'pilot.db').as_posix()}")
    create_schema(engine)
    sessions = create_session_factory(engine)
    clock = SyntheticClock(NOW)
    admission = _admission()
    definition = _definition(admission)
    authority = write(
        tmp_path,
        "pilot-authority.json",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by="governance",
            authorized_reviewer="pilot-reviewer",
            source_ids=["synthetic-source"],
            attested_schema_versions=[
                "QUANT_INTEGRITY_SCOPE_REVIEW_V1",
                "QUANT_INTEGRITY_COHORT_REVIEW_V1",
                "QUANT_INTEGRITY_EXCEPTION_REVIEW_V1",
            ],
            effective_at_utc=IMPORTED - timedelta(days=30),
            expires_at_utc=NOW + timedelta(days=30),
        ),
    )
    evidence = LocalTrainingEvidence(
        tmp_path,
        trusted_authorities={authority.evidence_reference: authority.evidence_sha256},
    )
    adapter = ContractAdmissionRepository(admission, evidence)
    ends = (
        PILOT_START - timedelta(days=40),
        PILOT_START + timedelta(days=30),
        PILOT_START + timedelta(days=400),
    )
    starts = (
        PILOT_START - timedelta(days=400),
        PILOT_START - timedelta(days=30),
        PILOT_START + timedelta(days=31),
    )
    scope_receipt = captured(
        adapter,
        tmp_path,
        "captured-scopes.json",
        {
            "records": [
                dict(
                    competition_id=p.provider_competition_id,
                    season_id=p.provider_season_id,
                    competition_name="Bundesliga",
                    country="DE",
                    competition_type="DOMESTIC_LEAGUE",
                    season_start=start,
                    season_end=end,
                )
                for p, start, end in zip(
                    definition.scope.provider_seasons, starts, ends, strict=True
                )
            ]
        },
    )
    scope_source = write(
        tmp_path,
        "scope-descriptor.json",
        QuantIntegrityScopeDocumentV1(
            records=tuple(
                QuantIntegrityScopeRecordV1(
                    provider=provider,
                    capture_receipt_id=scope_receipt.capture_receipt_id,
                    record_pointer=f"/records/{i}",
                    competition_id_pointer="/competition_id",
                    season_id_pointer="/season_id",
                    competition_identity_pointer="/competition_name",
                    expected_competition_identity="Bundesliga",
                    country_pointer="/country",
                    competition_type_pointer="/competition_type",
                    season_start_pointer="/season_start",
                    season_end_pointer="/season_end",
                )
                for i, provider in enumerate(definition.scope.provider_seasons)
            )
        ),
    )
    scope = definition.scope.model_copy(
        update={
            "authority_reference": authority.evidence_reference,
            "authority_sha256": authority.evidence_sha256,
            "reviewed_by": "pilot-reviewer",
            "raw_scope": scope_source,
        }
    )
    scope = scope.model_copy(
        update={
            "evidence": reviewed(
                tmp_path,
                "scope-review.json",
                "QUANT_INTEGRITY_SCOPE_REVIEW_V1",
                scope.model_dump(exclude={"evidence"}),
            )
        }
    )
    provider = definition.scope.provider_seasons[-2]
    schedule_receipt = captured(
        adapter,
        tmp_path,
        "captured-schedule.json",
        dict(
            count=3,
            completed_at=definition.cohort.season_completed_at_utc,
            records=[
                dict(
                    fixture_key=f"key-{match_id}",
                    competition_id=provider.provider_competition_id,
                    season_id=provider.provider_season_id,
                    status="FT",
                )
                for match_id in definition.cohort.full_schedule_match_ids
            ],
        ),
    )
    schedule_source = write(
        tmp_path,
        "schedule-descriptor.json",
        QuantIntegrityScheduleDocumentV1(
            provider=provider,
            capture_receipt_id=schedule_receipt.capture_receipt_id,
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
    cohort = definition.cohort.model_copy(
        update={"reviewed_by": "pilot-reviewer", "raw_schedule": schedule_source}
    )
    cohort = cohort.model_copy(
        update={
            "evidence": reviewed(
                tmp_path,
                "cohort-review.json",
                "QUANT_INTEGRITY_COHORT_REVIEW_V1",
                cohort.model_dump(exclude={"evidence"}),
            )
        }
    )
    revision = _code_revision()
    recipe = write(
        tmp_path,
        "recipe.json",
        QuantIntegrityBuildRecipeV1(
            recipe_id="synthetic-byte-verified-recipe",
            implementation_code_revision=revision,
            config_hash=definition.config_hash,
        ),
    )
    definition = definition.model_copy(
        update={
            "scope": scope,
            "cohort": cohort,
            "implementation_code_revision": revision,
            "build_recipe": ModelBuildRecipePinV1(
                recipe_id="synthetic-byte-verified-recipe",
                recipe_hash=recipe.evidence_sha256,
                evidence=recipe,
            ),
        }
    )
    adapter.metadata = definition
    repo = SqlAlchemyQuantIntegrityRepository(
        sessions, admission_repository=adapter, clock=clock, operator_id="operator"
    )
    service = QuantIntegrityPilotService(repo, clock)
    yield SimpleNamespace(
        root=tmp_path,
        engine=engine,
        sessions=sessions,
        clock=clock,
        admission=admission,
        definition=definition,
        adapter=adapter,
        repo=repo,
        service=service,
    )
    engine.dispose()


def run(pilot):
    plan = pilot.service.seal_plan(pilot.definition)
    report = pilot.service.run(IntegrityArtifactRefV1.of(plan))
    return plan, report


def candidate_plan(pilot, definition):
    return QuantIntegrityPlanV1.freeze(
        content_payload=QuantIntegrityPlanContentV1(
            definition=definition,
            input_roots=QuantIntegrityInputRootsV1.of(definition),
            sealed_at_utc=pilot.clock(),
        )
    )


def output_for(pilot, report):
    with pilot.sessions() as session:
        row = session.get(
            QuantIntegrityOutputRecord, report.content_payload.output_ref.artifact_id
        )
        return QuantIntegrityOutputV1.model_validate_json(row.artifact_json)


def test_durable_contract_plan_before_results_actual_times_and_exact_replay(pilot):
    before = pilot.clock.now
    plan = pilot.service.seal_plan(pilot.definition)
    assert pilot.adapter.result_reads == 0
    with pilot.sessions() as session:
        stored_plan = session.get(QuantIntegrityPlanRecord, plan.artifact_id)
        assert (
            before
            <= plan.content_payload.sealed_at_utc
            <= stored_plan.persisted_at_utc
            < pilot.clock.now
        )
    ref = IntegrityArtifactRefV1.of(plan)
    first = pilot.service.run(ref)
    second = pilot.service.run(ref)
    assert first == second
    assert (
        first.content_payload.availability_count == 2
        and first.content_payload.availability_denominator == 3
    )
    attempts = pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)
    assert len(attempts) == 2
    with pilot.sessions() as session:
        rows = tuple(
            session.scalars(
                select(QuantIntegrityAttemptRecord).order_by(
                    QuantIntegrityAttemptRecord.sequence
                )
            )
        )
        for row, attempt in zip(rows, attempts, strict=True):
            reservation = session.get(
                QuantIntegrityReservationRecord, row.reservation_id
            )
            assert (
                stored_plan.persisted_at_utc
                <= reservation.actual_started_at_utc
                <= reservation.persisted_at_utc
            )
            assert (
                reservation.persisted_at_utc
                <= row.actual_completed_at_utc
                <= row.persisted_at_utc
            )
            assert (
                row.actual_completed_at_utc
                == attempt.content_payload.actual_completed_at_utc
            )
        assert len(tuple(session.scalars(select(QuantIntegrityOutputRecord)))) == 1
    pilot.repo.complete_attempt(
        attempts[-1], output=output_for(pilot, second), report=second
    )
    assert (
        pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id) == attempts
    )
    attestation = pilot.service.seal_terminal_attestation(ref)
    assert attestation.content_payload.attempt_root == integrity_attempt_root(attempts)
    with pilot.sessions() as session:
        row = session.get(QuantIntegrityAttestationRecord, attestation.artifact_id)
        assert row.evidence_use == "SYNTHETIC_CONTRACT_ONLY"
        assert row.attested_at_utc <= row.persisted_at_utc < pilot.clock.now
    with pytest.raises(ValueError, match="never technical production evidence"):
        pilot.repo.technical_evidence(attestation.artifact_id, None)


def test_unsealed_results_and_self_asserted_real_source_are_blocked(pilot):
    with pytest.raises(ValueError, match="committed pinned pilot reservation"):
        pilot.repo.load_verified_training_admission(
            pilot.definition.admissions[0], at_utc=NOW
        )
    assert pilot.adapter.result_reads == 0
    real = pilot.definition.model_copy(
        update={
            "provenance": pilot.definition.provenance.model_copy(
                update={"evidence_use": IntegrityEvidenceUse.REAL_SOURCE},
            )
        }
    )
    with pytest.raises(ValueError, match="cannot assert REAL_SOURCE"):
        pilot.service.seal_plan(real)
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegrityPlanRecord.artifact_id)) is None


@pytest.mark.parametrize(
    "file",
    (
        "recipe.json",
        "scope-review.json",
        "cohort-review.json",
        "captured-scopes.json",
        "captured-schedule.json",
        "pilot-authority.json",
    ),
)
def test_wrong_actual_evidence_bytes_rollback_before_target_access(pilot, file):
    (pilot.root / file).write_bytes(b"tampered synthetic evidence")
    with pytest.raises(ValueError, match="SHA-256"):
        pilot.service.seal_plan(pilot.definition)
    assert pilot.adapter.result_reads == 0
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegrityPlanRecord.artifact_id)) is None


@pytest.mark.parametrize(
    ("reviewer", "preparer", "issuer"),
    (
        ("operator", "operator", "operator"),
        ("operator", "other-preparer", "governance"),
        ("pilot-reviewer", "pilot-reviewer", "governance"),
        ("pilot-reviewer", "other-preparer", "pilot-reviewer"),
        ("pilot-reviewer", "other-preparer", "governance"),
    ),
)
def test_review_actor_roles_are_provenance_not_separation_policy(
    pilot, reviewer, preparer, issuer
):
    authority = write(
        pilot.root,
        "role-authority.json",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by=issuer,
            authorized_reviewer=reviewer,
            source_ids=["synthetic-source"],
            attested_schema_versions=[
                "QUANT_INTEGRITY_SCOPE_REVIEW_V1",
                "QUANT_INTEGRITY_COHORT_REVIEW_V1",
            ],
            effective_at_utc=IMPORTED - timedelta(days=30),
            expires_at_utc=NOW + timedelta(days=30),
        ),
    )
    pilot.repo.evidence.trusted_authorities[authority.evidence_reference] = (
        authority.evidence_sha256
    )
    scope = pilot.definition.scope.model_copy(
        update={
            "authority_reference": authority.evidence_reference,
            "authority_sha256": authority.evidence_sha256,
            "reviewed_by": reviewer,
        }
    )
    cohort = pilot.definition.cohort.model_copy(update={"reviewed_by": reviewer})
    contents = {}
    for name, content in (("scope", scope), ("cohort", cohort)):
        schema = f"QUANT_INTEGRITY_{name.upper()}_REVIEW_V1"
        evidence = write(
            pilot.root,
            f"{name}-role-review.json",
            QuantIntegrityReviewDocumentV1(
                attested_schema_version=schema,
                attested_payload_hash=tagged_canonical_sha256(
                    schema, content.model_dump(exclude={"evidence"})
                ),
                prepared_by=preparer,
                authorized_reviewer=reviewer,
                reviewed_at_utc=content.reviewed_at_utc,
                source_ids=("synthetic-source",),
                evidence_use=IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY,
                accepted_for_internal_integrity=True,
            ),
        )
        contents[name] = content.model_copy(update={"evidence": evidence})
    definition = pilot.definition.model_copy(update=contents)
    pilot.adapter.metadata = definition
    plan = pilot.service.seal_plan(definition)
    assert plan.content_payload.definition == definition
    assert pilot.adapter.result_reads == 0


@pytest.mark.parametrize(
    "change",
    (
        {"authorized_reviewer": "not-authorized"},
        {"attested_schema_version": "WRONG_REVIEW_SCHEMA"},
        {"attested_payload_hash": "0" * 64},
        {"source_ids": ("unreviewed-source",)},
        {"source_ids": ("synthetic-source", "synthetic-source")},
        {"reviewed_at_utc": REVIEWED - timedelta(days=40)},
        {"reviewed_at_utc": NOW + timedelta(days=1)},
    ),
)
def test_review_authority_and_exact_binding_gates_remain_required(pilot, change):
    scope = pilot.definition.scope
    schema = "QUANT_INTEGRITY_SCOPE_REVIEW_V1"
    payload = scope.model_dump(exclude={"evidence"})
    review = QuantIntegrityReviewDocumentV1.model_validate(
        {
            "attested_schema_version": schema,
            "attested_payload_hash": tagged_canonical_sha256(schema, payload),
            "prepared_by": "other-preparer",
            "authorized_reviewer": scope.reviewed_by,
            "reviewed_at_utc": scope.reviewed_at_utc,
            "source_ids": ("synthetic-source",),
            "evidence_use": IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY,
            "accepted_for_internal_integrity": True,
            **change,
        }
    )
    evidence = write(pilot.root, "invalid-binding-review.json", review)
    with pytest.raises(
        ValueError, match="exact content, authority, provenance and actual time"
    ):
        pilot.repo._review(pilot.definition, evidence, schema, payload, NOW)
    assert pilot.adapter.result_reads == 0


def test_raw_schedule_count_cannot_be_replaced_by_reviewed_self_label(pilot):
    descriptor = pilot.definition.cohort.raw_schedule
    # Freshly sealed reviewer bytes still cannot make the captured count equal 306.
    cohort = pilot.definition.cohort.model_copy(update={"raw_schedule": descriptor})
    schedule = QuantIntegrityScheduleDocumentV1.model_validate_json(
        (pilot.root / descriptor.evidence_reference).read_bytes()
    )
    receipt = pilot.adapter.captures[schedule.capture_receipt_id]
    payload = {
        "count": 306,
        "completed_at": cohort.season_completed_at_utc,
        "records": [],
    }
    altered = captured(pilot.adapter, pilot.root, "different-schedule.json", payload)
    schedule = schedule.model_copy(
        update={"capture_receipt_id": altered.capture_receipt_id}
    )
    raw = write(pilot.root, "different-schedule-descriptor.json", schedule)
    cohort = cohort.model_copy(update={"raw_schedule": raw})
    cohort = cohort.model_copy(
        update={
            "evidence": reviewed(
                pilot.root,
                "different-cohort-review.json",
                "QUANT_INTEGRITY_COHORT_REVIEW_V1",
                cohort.model_dump(exclude={"evidence"}),
            )
        }
    )
    definition = pilot.definition.model_copy(update={"cohort": cohort})
    pilot.adapter.metadata = definition
    assert receipt.payload_sha256 != altered.payload_sha256
    with pytest.raises(ValueError, match="full schedule"):
        pilot.service.seal_plan(definition)


def test_continuous_reservation_one_pending_and_crash_recovery_retains_failure(pilot):
    plan = pilot.service.seal_plan(pilot.definition)
    ref = IntegrityArtifactRefV1.of(plan)
    reservation = pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
    with pytest.raises(ValueError, match="outstanding reservation"):
        pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
    recovered = pilot.repo.recover_pending_attempt(reservation.artifact_id)
    assert recovered.content_payload.status == "FAILED"
    assert recovered.content_payload.failure.exception_type == "InterruptedPilotAttempt"
    assert pilot.repo.recover_pending_attempt(reservation.artifact_id) == recovered
    pilot.service.run(ref)
    terminal = pilot.service.seal_terminal_attestation(ref)
    assert terminal.content_payload.attempt_count == 2
    assert (
        pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)[0]
        == recovered
    )


def test_failure_after_plan_is_retained_and_completion_cannot_be_overwritten(pilot):
    plan = pilot.service.seal_plan(pilot.definition)
    pilot.adapter.fail_results = True
    with pytest.raises(ValueError, match="archive missing"):
        pilot.service.run(IntegrityArtifactRefV1.of(plan))
    original = pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)[0]
    changed = QuantIntegrityAttemptV1.freeze(
        content_payload=original.content_payload.model_copy(
            update={
                "failure": QuantIntegrityFailureV1(
                    exception_type="DifferentFailure", reason="No replacement allowed"
                ),
            }
        )
    )
    with pytest.raises(ValueError, match="retry conflicts"):
        pilot.repo.complete_attempt(changed, output=None, report=None)
    assert pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id) == (
        original,
    )


def test_pending_old_reservation_cannot_read_after_current_rights_expire(pilot):
    plan = pilot.service.seal_plan(pilot.definition)
    reservation = pilot.repo.reserve_attempt(
        IntegrityArtifactRefV1.of(plan), actual_started_at_utc=pilot.clock()
    )
    requested_at = reservation.content_payload.actual_started_at_utc
    pin = pilot.definition.admissions[0]
    assert (
        pilot.repo.load_verified_training_admission(pin, at_utc=requested_at)
        == pilot.admission
    )
    reads = pilot.adapter.result_reads
    pilot.clock.now = pilot.admission.source_rights_admission.content_payload.rights_payload.expires_at_utc
    with pytest.raises(ValueError, match="not active"):
        pilot.repo.load_verified_training_admission(pin, at_utc=requested_at)
    assert (
        pilot.adapter.result_reads == reads
    )  # Rejected BEFORE opening normalized results.
    assert pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id) == ()
    assert (
        pilot.repo.recover_pending_attempt(
            reservation.artifact_id
        ).content_payload.status
        == "FAILED"
    )


def test_operational_read_checks_requested_time_and_expiry_during_read(
    pilot, monkeypatch
):
    plan = pilot.service.seal_plan(pilot.definition)
    reservation = pilot.repo.reserve_attempt(
        IntegrityArtifactRefV1.of(plan), actual_started_at_utc=pilot.clock()
    )
    pin = pilot.definition.admissions[0]
    with pytest.raises(ValueError, match="committed pinned pilot reservation"):
        pilot.repo.load_verified_training_admission(
            pin,
            at_utc=reservation.content_payload.actual_started_at_utc
            - timedelta(seconds=1),
        )
    assert pilot.adapter.result_reads == 0
    original = pilot.adapter.load

    def expires_while_reading(admission_id):
        value = original(admission_id)
        pilot.clock.now = (
            value.source_rights_admission.content_payload.rights_payload.expires_at_utc
        )
        return value

    monkeypatch.setattr(pilot.adapter, "load", expires_while_reading)
    with pytest.raises(ValueError, match="not active"):
        pilot.repo.load_verified_training_admission(
            pin, at_utc=reservation.content_payload.actual_started_at_utc
        )
    assert pilot.adapter.result_reads == 1


def test_completion_rechecks_current_rights_after_metadata_before_result_read(
    pilot, monkeypatch
):
    plan = pilot.service.seal_plan(pilot.definition)
    reservation = pilot.repo.reserve_attempt(
        IntegrityArtifactRefV1.of(plan), actual_started_at_utc=pilot.clock()
    )
    output = pilot.service._execute(
        plan, at_utc=reservation.content_payload.actual_started_at_utc
    )
    report = pilot.service._report(plan, output, output)
    attempt = QuantIntegrityAttemptV1.freeze(
        content_payload=QuantIntegrityAttemptContentV1(
            reservation=reservation,
            actual_completed_at_utc=pilot.clock(),
            status="COMPLETED",
            output_ref=IntegrityArtifactRefV1.of(output),
            report_ref=IntegrityArtifactRefV1.of(report),
        )
    )
    reads = pilot.adapter.result_reads
    original = pilot.repo._metadata

    def slow_metadata(session, definition, at):
        original(session, definition, at)
        pilot.clock.now = pilot.admission.source_rights_admission.content_payload.rights_payload.expires_at_utc

    monkeypatch.setattr(pilot.repo, "_metadata", slow_metadata)
    with pytest.raises(ValueError, match="not active"):
        pilot.repo.complete_attempt(attempt, output=output, report=report)
    assert pilot.adapter.result_reads == reads
    assert (
        pilot.repo.recover_pending_attempt(
            reservation.artifact_id
        ).content_payload.status
        == "FAILED"
    )


@pytest.mark.parametrize("last_status", ("FAILED", "COMPLETED"))
def test_direct_sql_summary_cannot_commit_without_its_attestation(pilot, last_status):
    plan = pilot.service.seal_plan(pilot.definition)
    ref = IntegrityArtifactRefV1.of(plan)
    if last_status == "FAILED":
        reservation = pilot.repo.reserve_attempt(
            ref, actual_started_at_utc=pilot.clock()
        )
        pilot.repo.recover_pending_attempt(reservation.artifact_id)
    else:
        pilot.service.run(ref)
    attempts = pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)
    summary = QuantIntegritySummaryV1.freeze(
        content_payload=QuantIntegritySummaryContentV1(
            integrity_pilot_series_id=pilot.definition.integrity_pilot_series_id,
            scope_hash=pilot.definition.scope_hash,
            attempts=attempts,
            attempt_count=1,
            attempt_root=integrity_attempt_root(attempts),
            generated_at_utc=pilot.clock(),
        )
    )
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with pilot.sessions.begin() as session:
            session.add(
                _row(
                    QuantIntegritySummaryRecord,
                    artifact_id=summary.artifact_id,
                    content_hash=summary.content_hash,
                    artifact_json=canonical_json(summary),
                    operator_id="operator",
                    persisted_at_utc=pilot.clock(),
                    series_id=pilot.definition.integrity_pilot_series_id,
                    attempt_count=1,
                    attempt_root=summary.content_payload.attempt_root,
                    generated_at_utc=summary.content_payload.generated_at_utc,
                )
            )
            session.flush()  # Deferred seal FK is checked at commit, not this flush.
            assert (
                session.get(QuantIntegritySummaryRecord, summary.artifact_id)
                is not None
            )
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegritySummaryRecord.artifact_id)) is None
    next_attempt = pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
    assert next_attempt.content_payload.sequence == 2
    assert (
        pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id) == attempts
    )


@pytest.mark.parametrize("new_plan", (False, True))
def test_forged_earlier_completed_attempt_cannot_hide_behind_honest_last(
    pilot, monkeypatch, new_plan
):
    plan = pilot.service.seal_plan(pilot.definition)
    ref = IntegrityArtifactRefV1.of(plan)
    interrupted = pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
    failed = pilot.repo.recover_pending_attempt(interrupted.artifact_id)
    pilot.service.run(
        ref
    )  # Warm the terminal verifier with an honest same-plan output.
    reservation = pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
    output = pilot.service._execute(
        plan, at_utc=reservation.content_payload.actual_started_at_utc
    )
    first_slice = output.content_payload.slices[0]
    wrong_target = first_slice.targets[0].model_copy(
        update={"outcome": SelectionKey.DRAW}
    )
    wrong_slice = first_slice.model_copy(
        update={"targets": (wrong_target, *first_slice.targets[1:])}
    )
    forged_output = QuantIntegrityOutputV1.freeze(
        content_payload=output.content_payload.model_copy(
            update={
                "slices": (wrong_slice, *output.content_payload.slices[1:]),
            }
        )
    )
    forged_report = pilot.service._report(plan, forged_output, forged_output)
    forged_attempt = QuantIntegrityAttemptV1.freeze(
        content_payload=QuantIntegrityAttemptContentV1(
            reservation=reservation,
            actual_completed_at_utc=pilot.clock(),
            status="COMPLETED",
            output_ref=IntegrityArtifactRefV1.of(forged_output),
            report_ref=IntegrityArtifactRefV1.of(forged_report),
        )
    )
    # Deliberately bypass the repository via SQL. Every seal/ref/SQL invariant is
    # consistent, but the earlier scored outcome is not the admitted result.
    with pilot.sessions.begin() as session:
        for model, artifact, fields in (
            (QuantIntegrityOutputRecord, forged_output, dict(plan_id=plan.artifact_id)),
            (
                QuantIntegrityReportRecord,
                forged_report,
                dict(plan_id=plan.artifact_id, output_id=forged_output.artifact_id),
            ),
            (
                QuantIntegrityAttemptRecord,
                forged_attempt,
                dict(
                    series_id=pilot.definition.integrity_pilot_series_id,
                    sequence=3,
                    plan_id=plan.artifact_id,
                    reservation_id=reservation.artifact_id,
                    status="COMPLETED",
                    actual_completed_at_utc=forged_attempt.content_payload.actual_completed_at_utc,
                    output_id=forged_output.artifact_id,
                    report_id=forged_report.artifact_id,
                ),
            ),
        ):
            session.add(
                _row(
                    model,
                    artifact_id=artifact.artifact_id,
                    content_hash=artifact.content_hash,
                    artifact_json=canonical_json(artifact),
                    operator_id="operator",
                    persisted_at_utc=pilot.clock(),
                    **fields,
                )
            )
            session.flush()
    if new_plan:
        definition = pilot.definition.model_copy(
            update={
                "metric_definition": pilot.definition.metric_definition.model_copy(
                    update={"log_loss_epsilon": Decimal("0.00001")}
                )
            }
        )
        plan = pilot.service.seal_plan(definition)
        ref = IntegrityArtifactRefV1.of(plan)
    honest_report = pilot.service.run(ref)
    assert honest_report.content_hash != forged_report.content_hash
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(
            pilot.repo,
            "seal_terminal_attestation",
            lambda s, a: captured.append((s, a)),
        )
        pilot.service.seal_terminal_attestation(ref)
    summary, attestation = captured[0]
    with pytest.raises(ValueError, match="exact admitted deterministic replay"):
        pilot.repo.seal_terminal_attestation(summary, attestation)
    assert (
        pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)[0]
        == failed
    )
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegritySummaryRecord.artifact_id)) is None
        assert (
            session.scalar(select(QuantIntegrityAttestationRecord.artifact_id)) is None
        )
    # Even a directly inserted terminal graph must fail the bridge's shared audit
    # path. Synthetic evidence is never relabeled REAL_SOURCE to reach this path.
    with pilot.sessions.begin() as session:
        common = dict(
            series_id=pilot.definition.integrity_pilot_series_id,
            attempt_count=4,
            attempt_root=summary.content_payload.attempt_root,
        )
        session.add(
            _row(
                QuantIntegritySummaryRecord,
                artifact_id=summary.artifact_id,
                content_hash=summary.content_hash,
                artifact_json=canonical_json(summary),
                operator_id="operator",
                persisted_at_utc=pilot.clock(),
                generated_at_utc=summary.content_payload.generated_at_utc,
                **common,
            )
        )
        session.flush()
        session.add(
            _row(
                QuantIntegrityAttestationRecord,
                artifact_id=attestation.artifact_id,
                content_hash=attestation.content_hash,
                artifact_json=canonical_json(attestation),
                operator_id="operator",
                persisted_at_utc=pilot.clock(),
                summary_id=summary.artifact_id,
                plan_id=plan.artifact_id,
                report_id=honest_report.artifact_id,
                evidence_use="SYNTHETIC_CONTRACT_ONLY",
                attested_at_utc=attestation.content_payload.attested_at_utc,
                **common,
            )
        )
    with (
        pilot.sessions.begin() as session,
        pytest.raises(ValueError, match="exact admitted deterministic replay"),
    ):
        session.execute(text("BEGIN"))
        row = session.get(QuantIntegrityAttestationRecord, attestation.artifact_id)
        pilot.repo._terminal_graph(session, row, pilot.clock())


def test_terminal_reuses_only_verified_math_within_one_verification(pilot, monkeypatch):
    plan, _ = run(pilot)
    ref = IntegrityArtifactRefV1.of(plan)
    pilot.service.run(ref)
    original_execute = QuantIntegrityPilotService._execute
    calls = []
    original_metadata = pilot.repo._metadata
    metadata_calls = []

    def counted_metadata(session, definition, at):
        metadata_calls.append(definition.integrity_pilot_series_id)
        return original_metadata(session, definition, at)

    def counted_execute(service, plan, *, at_utc):
        calls.append(plan.content_hash)
        return original_execute(service, plan, at_utc=at_utc)

    monkeypatch.setattr(QuantIntegrityPilotService, "_execute", counted_execute)
    monkeypatch.setattr(pilot.repo, "_metadata", counted_metadata)
    terminal = pilot.service.seal_terminal_attestation(ref)
    assert calls == [
        plan.content_hash
    ]  # Both completed attempts, one fixed calculation.
    assert len(metadata_calls) == 2  # No duplicate final metadata/source replay.
    with pilot.sessions.begin() as session:
        session.execute(text("BEGIN"))
        row = session.get(QuantIntegrityAttestationRecord, terminal.artifact_id)
        for count in (2, 3):
            pilot.repo._terminal_graph(session, row, pilot.clock())
            assert len(calls) == count  # No reuse across calls, even in one Session.
        (pilot.root / "captured-schedule.json").write_bytes(
            b"tampered synthetic evidence"
        )
        with pytest.raises(ValueError, match="SHA-256"):
            pilot.repo._terminal_graph(session, row, pilot.clock())
        assert len(calls) == 3


def test_success_graph_transaction_rolls_back_and_service_retains_failure(
    pilot, monkeypatch
):
    plan = pilot.service.seal_plan(pilot.definition)
    append = pilot.repo._append

    def fail_report(session, model, artifact, **values):
        if model is QuantIntegrityReportRecord:
            raise OSError("synthetic report write failure")
        return append(session, model, artifact, **values)

    monkeypatch.setattr(pilot.repo, "_append", fail_report)
    with pytest.raises(OSError, match="report write failure"):
        pilot.service.run(IntegrityArtifactRefV1.of(plan))
    assert (
        pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)[
            0
        ].content_payload.status
        == "FAILED"
    )
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegrityOutputRecord.artifact_id)) is None
        assert session.scalar(select(QuantIntegrityReportRecord.artifact_id)) is None


def test_current_code_revision_must_match_at_seal_run_and_terminal(pilot, monkeypatch):
    changed = pilot.definition.model_copy(
        update={"implementation_code_revision": "package:" + "0" * 64}
    )
    with pytest.raises(ValueError, match="_code_revision"):
        pilot.service.seal_plan(changed)
    plan, _ = run(pilot)
    monkeypatch.setattr(
        "football_system.infrastructure.database.quant_integrity_repository._code_revision",
        lambda: "package:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="_code_revision"):
        pilot.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    with pytest.raises(ValueError, match="_code_revision"):
        pilot.service.run(IntegrityArtifactRefV1.of(plan))
    attempts = pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)
    assert len(attempts) == 1 and attempts[0].content_payload.status == "COMPLETED"
    with pilot.sessions() as session:
        assert (
            len(
                tuple(
                    session.scalars(select(QuantIntegrityReservationRecord.artifact_id))
                )
            )
            == 1
        )


@pytest.mark.parametrize("transition", ("code", "recipe"))
def test_open_series_rejects_code_recipe_transition_before_plan_or_reservation(
    pilot, monkeypatch, transition
):
    plan_a, _ = run(pilot)
    revision = (
        "package:" + "b" * 64
        if transition == "code"
        else pilot.definition.implementation_code_revision
    )
    monkeypatch.setattr(
        "football_system.infrastructure.database.quant_integrity_repository._code_revision",
        lambda: revision,
    )
    recipe_id = pilot.definition.build_recipe.recipe_id + "-b"
    recipe = write(
        pilot.root,
        "recipe-b.json",
        QuantIntegrityBuildRecipeV1(
            recipe_id=recipe_id,
            implementation_code_revision=revision,
            config_hash=pilot.definition.config_hash,
        ),
    )
    definition_b = pilot.definition.model_copy(
        update={
            "implementation_code_revision": revision,
            "build_recipe": ModelBuildRecipePinV1(
                recipe_id=recipe_id, recipe_hash=recipe.evidence_sha256, evidence=recipe
            ),
        }
    )
    pilot.repo._recipe(
        definition_b
    )  # B is valid for the simulated installation, not for this A series.
    plan_b = candidate_plan(pilot, definition_b)
    metadata = pilot.repo._metadata

    def forbidden_metadata(*args, **kwargs):
        raise AssertionError("incompatible plan reached source metadata validation")

    with monkeypatch.context() as patch:
        patch.setattr(pilot.repo, "_metadata", forbidden_metadata)
        with pytest.raises(ValueError, match="incompatible pilot series code/recipe"):
            pilot.service.seal_plan(definition_b)
        with pytest.raises(ValueError, match="incompatible pilot series code/recipe"):
            pilot.repo.seal_plan(plan_b)
    assert pilot.repo._metadata == metadata
    with pytest.raises(IntegrityError, match="incompatible pilot series code/recipe"):
        with pilot.sessions.begin() as session:
            session.add(
                _row(
                    QuantIntegrityPlanRecord,
                    artifact_id=plan_b.artifact_id,
                    content_hash=plan_b.content_hash,
                    artifact_json=canonical_json(plan_b),
                    operator_id="operator",
                    persisted_at_utc=pilot.clock(),
                    series_id=definition_b.integrity_pilot_series_id,
                    scope_hash=definition_b.scope_hash,
                    evidence_use="SYNTHETIC_CONTRACT_ONLY",
                    sealed_at_utc=plan_b.content_payload.sealed_at_utc,
                )
            )
    if transition == "code":
        with pytest.raises(ValueError, match="_code_revision"):
            pilot.repo.reserve_attempt(
                IntegrityArtifactRefV1.of(plan_a), actual_started_at_utc=pilot.clock()
            )
    # There is no implicit new-series escape when A can no longer run.
    with pytest.raises(
        ValueError, match="disclose the exact previous terminal attestation"
    ):
        pilot.service.seal_plan(
            definition_b.model_copy(
                update={"integrity_pilot_series_id": "unapproved-rollover"}
            )
        )
    with pilot.sessions() as session:
        assert tuple(session.scalars(select(QuantIntegrityPlanRecord.artifact_id))) == (
            plan_a.artifact_id,
        )
        assert (
            len(
                tuple(
                    session.scalars(select(QuantIntegrityReservationRecord.artifact_id))
                )
            )
            == 1
        )
        assert (
            len(tuple(session.scalars(select(QuantIntegrityAttemptRecord.artifact_id))))
            == 1
        )
        assert (
            session.scalar(
                text("SELECT COUNT(*) FROM production_quant_integrity_pilot_series")
            )
            == 1
        )

    # Simulate a pre-fix mixed plan row, without granting it an attempt. Both the
    # repository and the SQL reservation guard must quarantine this legacy state.
    with pilot.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_production_quant_integrity_pilot_plans_code_recipe_insert"
        )
    with pilot.sessions.begin() as session:
        session.add(
            _row(
                QuantIntegrityPlanRecord,
                artifact_id=plan_b.artifact_id,
                content_hash=plan_b.content_hash,
                artifact_json=canonical_json(plan_b),
                operator_id="operator",
                persisted_at_utc=pilot.clock(),
                series_id=definition_b.integrity_pilot_series_id,
                scope_hash=definition_b.scope_hash,
                evidence_use="SYNTHETIC_CONTRACT_ONLY",
                sealed_at_utc=plan_b.content_payload.sealed_at_utc,
            )
        )
    with pytest.raises(ValueError, match="incompatible pilot series code/recipe"):
        pilot.repo.reserve_attempt(
            IntegrityArtifactRefV1.of(plan_b), actual_started_at_utc=pilot.clock()
        )
    attempts = pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)
    reserved = attempts[0].content_payload.reservation
    extra = QuantIntegrityAttemptReservationV1.freeze(
        content_payload=reserved.content_payload.model_copy(
            update={
                "sequence": 2,
                "plan_ref": IntegrityArtifactRefV1.of(plan_b),
                "prior_attempt_count": 1,
                "prior_attempt_root": integrity_attempt_root(attempts),
                "actual_started_at_utc": pilot.clock(),
            }
        )
    )
    with pytest.raises(IntegrityError, match="incompatible pilot series code/recipe"):
        with pilot.sessions.begin() as session:
            session.add(
                _row(
                    QuantIntegrityReservationRecord,
                    artifact_id=extra.artifact_id,
                    content_hash=extra.content_hash,
                    artifact_json=canonical_json(extra),
                    operator_id="operator",
                    persisted_at_utc=pilot.clock(),
                    series_id=definition_b.integrity_pilot_series_id,
                    sequence=2,
                    plan_id=plan_b.artifact_id,
                    actual_started_at_utc=extra.content_payload.actual_started_at_utc,
                )
            )
    with pilot.sessions() as session:
        assert (
            len(
                tuple(
                    session.scalars(select(QuantIntegrityReservationRecord.artifact_id))
                )
            )
            == 1
        )
        assert (
            len(tuple(session.scalars(select(QuantIntegrityAttemptRecord.artifact_id))))
            == 1
        )


def test_actual_repository_clock_rejects_future_backdated_and_naive_times(pilot):
    for clock in (lambda: NOW + timedelta(days=1), lambda: NOW - timedelta(minutes=2)):
        with pytest.raises(ValueError, match="repository clock"):
            QuantIntegrityPilotService(pilot.repo, clock).seal_plan(pilot.definition)
    broken = SqlAlchemyQuantIntegrityRepository(
        pilot.sessions,
        admission_repository=pilot.adapter,
        operator_id="operator",
        clock=lambda: NOW.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="aware UTC"):
        broken.verify_plan_metadata(pilot.definition, at_utc=NOW)
    plan = pilot.service.seal_plan(pilot.definition)
    with pytest.raises(ValueError, match="repository clock"):
        pilot.repo.reserve_attempt(
            IntegrityArtifactRefV1.of(plan),
            actual_started_at_utc=NOW + timedelta(days=1),
        )
    with pilot.sessions() as session:
        assert (
            session.scalar(select(QuantIntegrityReservationRecord.artifact_id)) is None
        )


def test_terminal_sql_guards_reject_extra_attempts_updates_deletes_and_replace(pilot):
    plan, _ = run(pilot)
    pilot.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    with pytest.raises(ValueError, match="terminal pilot series is closed"):
        pilot.service.run(IntegrityArtifactRefV1.of(plan))
    with pilot.sessions() as session:
        original = session.scalar(select(QuantIntegrityReservationRecord))
        original_artifact = QuantIntegrityAttemptReservationV1.model_validate_json(
            original.artifact_json
        )
    extra = QuantIntegrityAttemptReservationV1.freeze(
        content_payload=original_artifact.content_payload.model_copy(
            update={
                "sequence": 2,
                "prior_attempt_count": 1,
                "prior_attempt_root": integrity_attempt_root(
                    pilot.repo.list_attempts(pilot.definition.integrity_pilot_series_id)
                ),
                "actual_started_at_utc": pilot.clock(),
            }
        )
    )
    row = _row(
        QuantIntegrityReservationRecord,
        artifact_id=extra.artifact_id,
        content_hash=extra.content_hash,
        artifact_json=canonical_json(extra),
        operator_id="operator",
        persisted_at_utc=pilot.clock(),
        series_id=pilot.definition.integrity_pilot_series_id,
        sequence=2,
        plan_id=plan.artifact_id,
        actual_started_at_utc=extra.content_payload.actual_started_at_utc,
    )
    for sql in (
        "UPDATE production_quant_integrity_pilot_attempts SET status = status",
        "DELETE FROM production_quant_integrity_pilot_attestations",
        "INSERT OR REPLACE INTO production_quant_integrity_pilot_plans SELECT * FROM production_quant_integrity_pilot_plans",
    ):
        with pytest.raises(IntegrityError):
            with pilot.engine.begin() as connection:
                connection.execute(text(sql))
    with pytest.raises(IntegrityError, match="closed"):
        with pilot.sessions.begin() as session:
            session.add(row)


def test_atomic_reservation_race_has_one_winner(pilot):
    plan = pilot.service.seal_plan(pilot.definition)
    ref = IntegrityArtifactRefV1.of(plan)
    barrier = Barrier(2)

    def reserve():
        barrier.wait()
        try:
            return pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: reserve(), range(2)))
    assert sum(isinstance(value, ValueError) for value in results) == 1
    with pilot.sessions() as session:
        assert len(tuple(session.scalars(select(QuantIntegrityReservationRecord)))) == 1


def test_terminal_vs_reservation_race_cannot_close_over_a_pending_attempt(pilot):
    plan, _ = run(pilot)
    ref = IntegrityArtifactRefV1.of(plan)
    barrier = Barrier(2)

    def operation(terminal):
        barrier.wait()
        try:
            return (
                pilot.service.seal_terminal_attestation(ref)
                if terminal
                else pilot.repo.reserve_attempt(
                    ref, actual_started_at_utc=pilot.clock()
                )
            )
        except (ValueError, IntegrityError) as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(operation, (True, False)))
    with pilot.sessions() as session:
        terminal = session.scalar(select(QuantIntegrityAttestationRecord))
        reservations = tuple(session.scalars(select(QuantIntegrityReservationRecord)))
        assert (terminal is not None and len(reservations) == 1) or (
            terminal is None and len(reservations) == 2
        )
    assert any(isinstance(value, (ValueError, IntegrityError)) for value in results)


def test_bridge_requires_full_matching_history_without_relabeling_roots(pilot):
    plan, report = run(pilot)
    output = output_for(pilot, report)
    history = prepare_training_history(
        admissions=(pilot.admission,),
        training_window=pilot.definition.training_window,
        integrity_pilot_scope_id=pilot.definition.integrity_pilot_scope_id,
    )
    pilot.repo._bridge_history(plan, output, history)
    assert history.source_root != plan.content_payload.input_roots.source_root
    assert history.season_root != plan.content_payload.input_roots.season_root
    assert (
        history.approved_facts_hash
        != plan.content_payload.input_roots.admitted_facts_root
    )
    for update in (
        {"source_root": "0" * 64},
        {"season_root": "0" * 64},
        {"approved_facts_hash": "0" * 64},
        {"facts": history.facts[:-1], "fact_count": len(history.facts) - 1},
    ):
        with pytest.raises(ValueError):
            pilot.repo._bridge_history(plan, output, history.model_copy(update=update))
    different = prepare_training_history(
        admissions=(pilot.admission,),
        training_window=pilot.definition.training_window,
        integrity_pilot_scope_id="not-the-pilot-scope",
    )
    with pytest.raises(ValueError, match="FULL exact"):
        pilot.repo._bridge_history(plan, output, different)


def test_wrong_report_policy_is_rejected_and_pending_reservation_can_be_recovered(
    pilot,
):
    plan = pilot.service.seal_plan(pilot.definition)
    ref = IntegrityArtifactRefV1.of(plan)
    reservation = pilot.repo.reserve_attempt(ref, actual_started_at_utc=pilot.clock())
    output = pilot.service._execute(
        plan, at_utc=reservation.content_payload.actual_started_at_utc
    )
    report = pilot.service._report(plan, output, output)
    report = QuantIntegrityReportV1.freeze(
        content_payload=report.content_payload.model_copy(
            update={
                "metric_definition": report.content_payload.metric_definition.model_copy(
                    update={"log_loss_epsilon": Decimal("0.01")}
                ),
            }
        )
    )
    attempt = QuantIntegrityAttemptV1.freeze(
        content_payload=QuantIntegrityAttemptContentV1(
            reservation=reservation,
            actual_completed_at_utc=pilot.clock(),
            status="COMPLETED",
            output_ref=IntegrityArtifactRefV1.of(output),
            report_ref=IntegrityArtifactRefV1.of(report),
        )
    )
    with pytest.raises(ValueError, match="exact admitted deterministic replay"):
        pilot.repo.complete_attempt(attempt, output=output, report=report)
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegrityOutputRecord.artifact_id)) is None
    assert (
        pilot.repo.recover_pending_attempt(
            reservation.artifact_id
        ).content_payload.status
        == "FAILED"
    )


def test_terminal_wrong_attempt_root_is_rejected_before_persistence(pilot, monkeypatch):
    plan, _ = run(pilot)
    original_seal = pilot.repo.seal_terminal_attestation
    captured = []
    monkeypatch.setattr(
        pilot.repo,
        "seal_terminal_attestation",
        lambda summary, attest: captured.append((summary, attest)),
    )
    pilot.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    summary, attestation = captured[0]
    wrong = QuantIntegrityAttestationV1.freeze(
        content_payload=attestation.content_payload.model_copy(
            update={"attempt_root": "0" * 64}
        )
    )
    with pytest.raises(ValueError, match="every completed reservation"):
        original_seal(summary, wrong)
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegritySummaryRecord.artifact_id)) is None
    original_seal(summary, attestation)


def test_new_series_requires_explicit_prior_terminal_and_shared_session_cannot_authorize_synthetic(
    pilot,
):
    plan, _ = run(pilot)
    terminal = pilot.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    new_definition = pilot.definition.model_copy(
        update={"integrity_pilot_series_id": "synthetic-second-series"}
    )
    with pytest.raises(ValueError, match="disclose"):
        pilot.service.seal_plan(new_definition)
    new_definition = new_definition.model_copy(
        update={"previous_terminal_attestation": IntegrityArtifactRefV1.of(terminal)}
    )
    new_plan = pilot.service.seal_plan(new_definition)
    reserved = pilot.repo.reserve_attempt(
        IntegrityArtifactRefV1.of(new_plan), actual_started_at_utc=pilot.clock()
    )
    assert reserved.content_payload.sequence == 1
    with (
        pilot.sessions.begin() as session,
        pytest.raises(ValueError, match="never technical production evidence"),
    ):
        pilot.repo.technical_evidence(terminal.artifact_id, None, session=session)


@pytest.mark.parametrize("unattempted_plan", (False, True))
def test_new_terminal_checks_all_current_rights_before_any_source_read(
    pilot, monkeypatch, unattempted_plan
):
    plan, _ = run(pilot)
    original_rights = pilot.admission.source_rights_admission
    rights_by_id = {original_rights.source_rights_admission_id: original_rights}
    expires = original_rights.content_payload.rights_payload.expires_at_utc
    if unattempted_plan:
        # A second, unattempted plan has a different research grant. Its metadata
        # is sufficient to require a current-rights check; no result read is needed.
        expires = NOW + timedelta(days=1)
        payload = original_rights.content_payload.rights_payload.model_copy(
            update={"expires_at_utc": expires}
        )
        old_review = original_rights.content_payload.reviewer_attestation
        review = type(old_review).freeze(
            content_payload=old_review.content_payload.model_copy(
                update={
                    "attested_payload_hash": tagged_canonical_sha256(
                        "SOURCE_RIGHTS_PAYLOAD_V1", payload
                    ),
                }
            )
        )
        short_rights = type(original_rights).from_recorded(
            rights_payload=payload,
            reviewer_attestation=review,
            recorded_at_utc=original_rights.content_payload.recorded_at_utc,
        )
        rights_by_id[short_rights.source_rights_admission_id] = short_rights
        admission = type(pilot.admission).from_persisted(
            source_rights_admission=short_rights,
            facts=pilot.admission.facts,
            actual_started_at_utc=pilot.admission.content_payload.actual_started_at_utc,
            actual_completed_at_utc=pilot.admission.content_payload.actual_completed_at_utc,
            persisted_at_utc=pilot.admission.content_payload.persisted_at_utc,
        )
        slices = tuple(
            QuantIntegritySliceV1.freeze(
                content_payload=s.content_payload.model_copy(
                    update={
                        "targets": tuple(
                            t.model_copy(
                                update={
                                    "training_fact_admission_id": admission.training_fact_admission_id
                                }
                            )
                            for t in s.content_payload.targets
                        ),
                    }
                )
            )
            for s in pilot.definition.slices
        )
        definition = pilot.definition.model_copy(
            update={
                "admissions": (TrainingAdmissionPinV1.from_admission(admission),),
                "slices": slices,
            }
        )
        extra = candidate_plan(pilot, definition)
        with pilot.sessions.begin() as session:
            session.add(
                _row(
                    QuantIntegrityPlanRecord,
                    artifact_id=extra.artifact_id,
                    content_hash=extra.content_hash,
                    artifact_json=canonical_json(extra),
                    operator_id="operator",
                    persisted_at_utc=pilot.clock(),
                    series_id=definition.integrity_pilot_series_id,
                    scope_hash=definition.scope_hash,
                    evidence_use="SYNTHETIC_CONTRACT_ONLY",
                    sealed_at_utc=extra.content_payload.sealed_at_utc,
                )
            )
        original_rights.assert_active_for(expires, TRAINING_FACT_REQUIRED_USES)
    monkeypatch.setattr(
        pilot.adapter, "load_rights", lambda identifier: rights_by_id[identifier]
    )
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(
            pilot.repo,
            "seal_terminal_attestation",
            lambda s, a: captured.append((s, a)),
        )
        pilot.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    summary, attestation = captured[0]
    pilot.clock.now = expires
    summary = QuantIntegritySummaryV1.freeze(
        content_payload=summary.content_payload.model_copy(
            update={
                "generated_at_utc": pilot.clock(),
            }
        )
    )
    attestation = QuantIntegrityAttestationV1.freeze(
        content_payload=attestation.content_payload.model_copy(
            update={
                "summary_ref": IntegrityArtifactRefV1.of(summary),
                "attested_at_utc": pilot.clock(),
            }
        )
    )
    reads = []

    def forbidden(*args, **kwargs):
        reads.append("source/replay")
        raise AssertionError(
            "new terminal seal accessed source data before checking all current rights"
        )

    monkeypatch.setattr(pilot.repo, "_attempts", forbidden)
    monkeypatch.setattr(pilot.repo, "_metadata", forbidden)
    monkeypatch.setattr(pilot.adapter, "load", forbidden)
    monkeypatch.setattr(pilot.adapter, "load_capture", forbidden)
    monkeypatch.setattr(QuantIntegrityPilotService, "_execute", forbidden)
    with pytest.raises(ValueError, match="not active"):
        pilot.repo.seal_terminal_attestation(summary, attestation)
    assert reads == []
    with pilot.sessions() as session:
        assert session.scalar(select(QuantIntegritySummaryRecord.artifact_id)) is None
        assert (
            session.scalar(select(QuantIntegrityAttestationRecord.artifact_id)) is None
        )
        assert (
            len(tuple(session.scalars(select(QuantIntegrityAttemptRecord.artifact_id))))
            == 1
        )


def test_audit_of_sealed_pilot_does_not_replace_independent_current_grants(pilot):
    plan, _ = run(pilot)
    terminal = pilot.service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    pilot.clock.now = (
        pilot.admission.source_rights_admission.content_payload.rights_payload.expires_at_utc
        + timedelta(days=1)
    )
    with pilot.sessions.begin() as session:
        row = session.get(QuantIntegrityAttestationRecord, terminal.artifact_id)
        graph = pilot.repo._terminal_graph(session, row, pilot.clock())
        assert graph[0] == terminal
    # Historical verification is not production authorization, even after expiry.
    with pytest.raises(ValueError, match="never technical production evidence"):
        pilot.repo.technical_evidence(terminal.artifact_id, None)


def test_corrupt_stored_artifact_and_wrong_terminal_roots_are_rejected(pilot):
    plan, report = run(pilot)
    attestation = pilot.service.seal_terminal_attestation(
        IntegrityArtifactRefV1.of(plan)
    )
    with pilot.sessions() as session:
        row = session.scalar(select(QuantIntegritySummaryRecord))
        summary = QuantIntegritySummaryV1.model_validate_json(row.artifact_json)
    wrong = QuantIntegrityAttestationV1.freeze(
        content_payload=attestation.content_payload.model_copy(
            update={"attempt_root": "0" * 64}
        )
    )
    with pytest.raises(ValueError, match="closed"):
        pilot.repo.seal_terminal_attestation(summary, wrong)
    # Simulated storage corruption, NOT an authorized way to modify sealed data.
    with pilot.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_production_quant_integrity_pilot_reports_append_only_update"
        )
        connection.execute(
            text(
                "UPDATE production_quant_integrity_pilot_reports SET row_sha256 = :bad"
            ),
            {"bad": "0" * 64},
        )
    with pytest.raises(ValueError, match="row integrity"):
        pilot.repo.load_report(IntegrityArtifactRefV1.of(report))


def _pilot_signature(engine):
    inspector = inspect(engine)
    seal_fk = next(
        fk
        for fk in inspector.get_foreign_keys(
            "production_quant_integrity_pilot_summaries"
        )
        if fk["referred_table"] == "production_quant_integrity_pilot_attestations"
    )
    assert (
        seal_fk["constrained_columns"] == seal_fk["referred_columns"] == ["series_id"]
    )
    assert seal_fk["options"]["deferrable"] is True
    assert seal_fk["options"]["initially"] == "DEFERRED"
    tables = tuple(
        (
            name,
            tuple(
                (c["name"], str(c["type"]), c["nullable"])
                for c in inspector.get_columns(name)
            ),
            sorted(
                (
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    tuple(f["referred_columns"]),
                )
                for f in inspector.get_foreign_keys(name)
            ),
        )
        for name in QUANT_INTEGRITY_TABLES
    )
    with engine.connect() as connection:
        triggers = tuple(
            connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name LIKE 'production_quant_integrity_pilot_%' ORDER BY name"
                )
            )
        )
    return tables, tuple((name, " ".join(sql.split())) for name, sql in triggers)


def test_pilot_migration_fresh_and_existing_data_upgrade_parity(tmp_path):
    engines = []
    try:
        runtime = create_database_engine(
            f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
        )
        engines.append(runtime)
        create_schema(runtime)
        signatures = [_pilot_signature(runtime)]
        for name, existing in (("fresh", False), ("existing", True)):
            url = f"sqlite:///{(tmp_path / (name + '.db')).as_posix()}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", url)
            if existing:
                command.upgrade(config, "8a7c2f4e9b10")
                old = create_database_engine(url)
                with old.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO providers(provider_id,code,name,provider_kind) VALUES ('old','OLD','Existing metadata','TEST')"
                        )
                    )
                old.dispose()
            command.upgrade(config, "9b8d3e5f0a21")
            engine = create_database_engine(url)
            engines.append(engine)
            signatures.append(_pilot_signature(engine))
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == "9b8d3e5f0a21"
                )
                assert tuple(connection.execute(text("PRAGMA foreign_key_check"))) == ()
                if existing:
                    assert (
                        connection.scalar(
                            text("SELECT name FROM providers WHERE provider_id='old'")
                        )
                        == "Existing metadata"
                    )
        assert signatures[0] == signatures[1] == signatures[2]
    finally:
        for engine in engines:
            engine.dispose()


@pytest.fixture
def actual_admission(tmp_path, monkeypatch, request):
    """Actual admission repository and controlled test files, still no real source."""
    generator = admission_tests.lane.__wrapped__(tmp_path)
    lane = next(generator)

    def seed(context, count=2):
        with context.sessions.begin() as session:
            session.add(
                ProviderRecord(
                    provider_id="provider",
                    code="PROVIDER",
                    name="Contract Provider",
                    provider_kind="TEST",
                )
            )
            session.add(
                CompetitionRecord(
                    competition_id="league",
                    canonical_key="league",
                    name="Reviewed test mapping",
                    country_code="DE",
                )
            )
            for team in ("home", "away"):
                session.add(
                    TeamRecord(
                        team_id=team, canonical_key=team, name=team, team_type="CLUB"
                    )
                )
            session.flush()
            for team in ("home", "away"):
                session.add(
                    ProviderTeamAliasRecord(
                        alias_id=f"alias-{team}",
                        internal_team_id=team,
                        provider_id="provider",
                        provider_team_id=f"p-{team}",
                        provider_team_name=team,
                        language="en",
                        team_type="CLUB",
                        available_at_utc=admission_tests.SOURCE,
                    )
                )
            session.add(
                ProviderCompetitionMappingRecord(
                    mapping_id="competition-mapping",
                    internal_competition_id="league",
                    provider_id="provider",
                    provider_competition_id="p-league",
                    provider_competition_name="Explicit test scope",
                    language="en",
                    season="2024/25",
                    competition_type="DOMESTIC_LEAGUE",
                    available_at_utc=admission_tests.SOURCE,
                )
            )
            for i in range(count):
                session.add(
                    MatchRecord(
                        internal_match_id=f"match-{i}",
                        competition_id="league",
                        home_team_id="home",
                        away_team_id="away",
                        kickoff_at_utc=admission_tests.SOURCE + timedelta(days=i + 1),
                        status="FINISHED",
                        available_at_utc=admission_tests.SOURCE,
                        created_at_utc=admission_tests.LOCAL,
                    )
                )
            session.flush()
            for i in range(count):
                session.add(
                    CanonicalMatchIdentityRecord(
                        internal_match_id=f"match-{i}",
                        season="2024/25",
                        competition_type="DOMESTIC_LEAGUE",
                        available_at_utc=admission_tests.SOURCE,
                    )
                )
                session.add(
                    ProviderMatchMappingRecord(
                        mapping_id=f"mapping-{i}",
                        provider_id="provider",
                        external_namespace="fixture",
                        external_match_id=f"fixture-{i}",
                        internal_match_id=f"match-{i}",
                        resolution_method="EXPLICIT_MAPPING",
                        confidence=1,
                        available_at_utc=admission_tests.SOURCE,
                    )
                )

    monkeypatch.setattr(admission_tests, "seed_identities", seed)
    admission_tests.prepare(lane, count=getattr(request, "param", 2))
    submissions = []
    for item in lane.submissions:
        candidate = item.candidate.model_copy(
            update={
                "canonical_identity": item.candidate.canonical_identity.model_copy(
                    update={"competition_type": "DOMESTIC_LEAGUE"},
                )
            }
        )
        evidence = admission_tests.write_evidence(
            lane.root,
            item.reviewer_evidence.evidence_reference,
            admission_tests.review_document(
                schema="TRAINING_FACT_REVIEW_INPUT_V1",
                digest=admission_tests.training_review_input_sha256(
                    candidate, item.source_evidence
                ),
                at=candidate.season_membership.content_payload.reviewed_at_utc,
            ),
        )
        submissions.append(
            item.model_copy(
                update={"candidate": candidate, "reviewer_evidence": evidence}
            )
        )
    lane.submissions = tuple(submissions)
    lane.admission = admission_tests.admit(lane)
    try:
        yield lane
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


@pytest.mark.parametrize("actual_admission", (2, 12), indirect=True)
def test_real_admission_metadata_projection_never_fetches_normalized_results(
    pilot, actual_admission, monkeypatch
):
    lane = actual_admission
    value = lane.admission
    targets = tuple(
        QuantIntegrityTargetV1(
            identity=f.content_payload.canonical_identity,
            training_fact_admission_id=value.training_fact_admission_id,
            fact=AdmittedFactRefV1.of(f),
            fixture_source_available_at_utc=f.content_payload.fixture_source.source_available_at_utc,
            mapping_source_available_at_utc=f.content_payload.season_membership.content_payload.source_available_at_utc,
        )
        for f in sorted(
            value.facts, key=lambda f: f.content_payload.normalized_result.match_id
        )
    )
    scope = pilot.definition.scope.model_copy(
        update={
            "competition_id": "league",
            "provider_seasons": (
                ReviewedProviderSeasonV1(
                    source_id="source",
                    provider_code="PROVIDER",
                    provider_competition_id="p-league",
                    provider_season_id="p-2024-25",
                    canonical_season_id="2024/25",
                ),
            ),
        }
    )
    slices = (
        QuantIntegritySliceV1.freeze(
            content_payload=QuantIntegritySliceContentV1(
                sequence=0,
                targets=targets,
                exclude_match_ids=tuple(t.fact.match_id for t in targets),
                decision_as_of_at_utc=min(t.identity.kickoff_at_utc for t in targets)
                - timedelta(hours=1),
                evaluation_as_of_at_utc=max(t.identity.kickoff_at_utc for t in targets)
                + timedelta(days=1),
            )
        ),
    )
    # Exercise only the admission-metadata gate here. The separate local scope and
    # cohort reviews are covered above; this partial definition is never sealed.
    definition = pilot.definition.model_copy(
        update={
            "scope": scope,
            "slices": slices,
            "admissions": (TrainingAdmissionPinV1.from_admission(value),),
        }
    )
    repo = SqlAlchemyQuantIntegrityRepository(
        lane.sessions,
        admission_repository=lane.repo,
        operator_id="operator",
        clock=lane.clock,
    )
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append((statement, parameters))

    def forbidden(*args, **kwargs):
        raise AssertionError("normalized result admission loader called before seal")

    monkeypatch.setattr(lane.repo, "_load", forbidden)
    event.listen(lane.engine, "before_cursor_execute", capture)
    try:
        with lane.sessions.begin() as session:
            repo._admission_metadata(session, definition, lane.clock())
    finally:
        event.remove(lane.engine, "before_cursor_execute", capture)
    assert queries
    assert sum("json_each(" in statement for statement, _ in queries) == 1
    assert (
        sum(
            "training_capture_receipts.payload_bytes" in statement
            for statement, _ in queries
        )
        == 2
    )
    for statement, parameters in queries:
        assert "FROM match_results" not in statement
        assert (
            "match_results.home_goals" not in statement
            and "match_results.away_goals" not in statement
        )
        assert "$.content_payload.normalized_result" not in str(parameters)
        assert "SELECT training_fact_admissions.artifact_json" not in statement
        assert "SELECT training_fact_bindings.artifact_json" not in statement
    bad_pin = definition.admissions[0].model_copy(
        update={"admitted_fact_root": "0" * 64}
    )
    with lane.sessions.begin() as session, pytest.raises(ValueError, match="root"):
        repo._admission_metadata(
            session,
            definition.model_copy(update={"admissions": (bad_pin,)}),
            lane.clock(),
        )
    with lane.sessions.begin() as session:
        repo._admission_metadata(session, definition, lane.clock())
        (lane.root / "fixture.json").write_bytes(
            b"changed after the previous metadata pass"
        )
        with pytest.raises(ValueError, match="SHA-256"):
            repo._admission_metadata(session, definition, lane.clock())
