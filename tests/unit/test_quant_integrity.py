"""SYNTHETIC CONTRACT TESTS ONLY. No real Bundesliga pilot, rights or source data.

The in-memory fake emulates persisted REAL_SOURCE_DATA-shaped admission contracts;
it is NOT proof of storage, legal grants, provider evidence or real season coverage.
Every pilot artifact explicitly carries SYNTHETIC_CONTRACT_ONLY. Tiny invented
cohorts and opaque hashes below must never be used as real integrity evidence.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

import pytest
from pydantic import ValidationError

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.application.training_admission import SealSourceRightsReviewService
from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.production_release import (
    EloTrainingWindowContentV1,
    EloTrainingWindowV1,
    ProviderSeasonRefV1,
    TrainingSeasonV1,
)
from football_system.domain.quant_integrity import (
    FIXED_ELO_CONFIG_HASH,
    AdmittedFactRefV1,
    CohortCompletenessExceptionV1,
    IntegrityArtifactRefV1,
    IntegrityEvidenceUse,
    ModelBuildRecipePinV1,
    QuantIntegrityAttemptReservationContentV1,
    QuantIntegrityAttemptReservationV1,
    QuantIntegrityCohortV1,
    QuantIntegrityMetricDefinitionV1,
    QuantIntegrityPlanDefinitionV1,
    QuantIntegrityProvenanceV1,
    QuantIntegrityScopeV1,
    QuantIntegritySliceContentV1,
    QuantIntegritySliceV1,
    QuantIntegrityTargetV1,
    ReviewedProviderSeasonV1,
    TerminalProjectionDefinitionV1,
    TrainingAdmissionPinV1,
    integrity_attempt_root,
    project_admitted_training_fact,
    select_admitted_training_facts,
)
from football_system.domain.services.backtest_metrics import (
    calculate_probability_metrics,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloPredictionStatus,
    EloThreeWayBaseline,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
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
from football_system.infrastructure.providers.admitted_training import (
    AdmittedTrainingHistoryEloProvider,
)

UTC = timezone.utc
WARMUP = "synthetic-season-a"
PILOT = "synthetic-season-b"
PRODUCTION = "synthetic-season-c"
COMPETITION = "synthetic-competition-17"
WARMUP_START = datetime(2024, 8, 1, 12, tzinfo=UTC)
PILOT_START = datetime(2025, 8, 1, 12, tzinfo=UTC)
IMPORTED = datetime(2026, 9, 1, 12, tzinfo=UTC)
CREATED = IMPORTED + timedelta(minutes=1)
REGISTERED = CREATED + timedelta(minutes=1)
REVIEWED = REGISTERED + timedelta(minutes=1)
ADMITTED = REVIEWED + timedelta(minutes=3)
NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


def _hash(value):
    return tagged_canonical_sha256("SYNTHETIC_CONTRACT_ONLY", value)


def _evidence(label="scope"):
    return LocalReviewEvidenceV1(
        evidence_reference=f"synthetic-contract-test://{label}",
        evidence_sha256=_hash(label),
    )


def _fact(
    match_id,
    sequence=0,
    *,
    season=WARMUP,
    kickoff=WARMUP_START,
    home="synthetic-a",
    away="synthetic-b",
    fixture_at=None,
    membership_at=None,
    result_at=None,
    score=(2, 1),
):
    fixture_at = fixture_at or kickoff - timedelta(days=2)
    membership_at = membership_at or kickoff - timedelta(days=2)
    result_at = result_at or kickoff + timedelta(hours=2)
    result = NormalizedMatchResultRecordV1(
        match_result_id=f"result-{match_id}",
        match_id=match_id,
        provider_code="synthetic-provider",
        home_goals=score[0],
        away_goals=score[1],
        observed_at_utc=result_at - timedelta(minutes=1),
        available_at_utc=result_at,
        ingested_at_utc=result_at,
        source_result_key=f"result-key-{match_id}",
        payload_hash=match_result_payload_sha256(*score),
    )
    fixture = TrainingFixtureSourceV1(
        source_id="synthetic-source",
        provider_code="synthetic-provider",
        provider_fixture_namespace="fixture",
        provider_fixture_key=f"key-{match_id}",
        internal_match_id=match_id,
        fixture_source_archive_id="synthetic-fixtures",
        fixture_source_archive_payload_sha256=_hash("fixtures"),
        fixture_source_archive_created_at_utc=CREATED,
        fixture_source_record_id=f"record-{match_id}",
        fixture_record_sha256=_hash(match_id),
        source_available_at_utc=fixture_at,
        local_imported_at_utc=IMPORTED,
        registered_at_utc=REGISTERED,
    )
    membership = MatchSeasonMembershipV1.freeze(
        content_payload=MatchSeasonMembershipContentV1(
            source_id="synthetic-source",
            provider_code="synthetic-provider",
            provider_competition_id="synthetic-de-first",
            provider_season_id=f"provider-{season}",
            provider_season_candidate_ids=(f"provider-{season}",),
            provider_fixture_namespace="fixture",
            provider_fixture_key=f"key-{match_id}",
            provider_mapping_id=f"mapping-{match_id}",
            internal_match_id=match_id,
            fixture_source_record_id=fixture.fixture_source_record_id,
            fixture_record_sha256=fixture.fixture_record_sha256,
            provider_scope_raw_artifact_id=f"scope-{season}",
            provider_scope_payload_sha256=_hash(season),
            provider_scope_created_at_utc=CREATED,
            provider_scope_record_sha256=_hash([match_id, season]),
            provider_competition_field_path="fixture.competition.id",
            provider_season_field_path="fixture.season.id",
            provider_fixture_field_path="fixture.id",
            season_assignment_method="PROVIDER_EXPLICIT_FIELDS",
            season_mapping_version="synthetic-v1",
            canonical_competition_id=COMPETITION,
            canonical_season_id=season,
            source_available_at_utc=membership_at,
            local_imported_at_utc=IMPORTED,
            registered_at_utc=REGISTERED,
            mapping_policy_version="synthetic-v1",
            reviewed_by="synthetic-reviewer",
            reviewed_at_utc=REVIEWED,
        )
    )
    result_admission = MatchResultAdmissionV1.freeze(
        content_payload=MatchResultAdmissionContentV1(
            source_id="synthetic-source",
            internal_match_id=match_id,
            match_result_id=result.match_result_id,
            provider_code=result.provider_code,
            provider_result_key=result.source_result_key,
            provider_raw_status="FT",
            status_mapping_version="synthetic-v1",
            provider_status_category="REGULAR_TIME_FINAL",
            score_semantics="REGULAR_TIME_ONLY",
            regular_time_home_goals=score[0],
            regular_time_away_goals=score[1],
            provider_finalized_at_utc=result_at - timedelta(minutes=2),
            source_observed_at_utc=result.observed_at_utc,
            source_available_at_utc=result_at,
            raw_artifact_id="synthetic-results",
            raw_artifact_payload_sha256=_hash("results"),
            raw_artifact_created_at_utc=CREATED,
            raw_record_sha256=_hash([match_id, score]),
            normalized_record_sha256=normalized_match_result_record_sha256(result),
            local_imported_at_utc=IMPORTED,
            registered_at_utc=REGISTERED,
            adapter_name="synthetic-contract-test",
            adapter_version="1",
            reviewed_by="synthetic-reviewer",
            reviewed_at_utc=REVIEWED,
        )
    )
    return TrainingFactBindingV1.freeze(
        content_payload=TrainingFactBindingContentV1(
            sequence=sequence,
            fixture_source=fixture,
            season_membership=membership,
            match_result_admission=result_admission,
            normalized_result=result,
            provider_mapping=TrainingProviderMatchMappingV1(
                mapping_id=f"mapping-{match_id}",
                provider_code="synthetic-provider",
                external_namespace="fixture",
                external_match_id=f"key-{match_id}",
                internal_match_id=match_id,
                resolution_method="EXPLICIT_MAPPING",
                confidence=Decimal(1),
                available_at_utc=min(fixture_at, membership_at),
            ),
            canonical_identity=TrainingCanonicalMatchIdentityV1(
                internal_match_id=match_id,
                internal_competition_id=COMPETITION,
                internal_home_team_id=home,
                internal_away_team_id=away,
                season=season,
                competition_type="DOMESTIC_LEAGUE",
                kickoff_at_utc=kickoff,
            ),
        )
    )


def _admission(warmup_count=5):
    rights_payload = SourceRightsPayloadV1.freeze(
        source_owner="SYNTHETIC CONTRACT TEST ONLY",
        product_name="NOT A REAL SOURCE",
        source_ids=("synthetic-source",),
        terms_version="synthetic-test",
        terms_reference="synthetic-contract-test://terms",
        terms_sha256=_hash("terms"),
        jurisdiction="DE",
        effective_at_utc=IMPORTED - timedelta(days=10),
        expires_at_utc=NOW + timedelta(days=10),
        permitted_uses=tuple(SourceRightsPermittedUse),
        raw_retention_rule="Synthetic test only, no rights determination",
        derived_retention_rule="Synthetic test only",
        subscription_end_retention_rule="Synthetic test only",
        deletion_obligation="Synthetic test only",
        public_repository_boundary="No real source bytes",
    )
    review = SealSourceRightsReviewService().seal(
        rights_payload=rights_payload,
        authorized_reviewer="synthetic-reviewer",
        reviewer_authority_reference="synthetic-contract-test://authority",
        authority_sha256=_hash("authority"),
        reviewed_at_utc=IMPORTED - timedelta(days=9),
        evidence=_evidence("rights"),
    )
    rights = SourceRightsAdmissionV1.from_recorded(
        rights_payload=rights_payload,
        reviewer_attestation=review,
        recorded_at_utc=IMPORTED - timedelta(days=8),
    )
    facts = tuple(
        _fact(f"warmup-{i}", i, kickoff=WARMUP_START + timedelta(days=i))
        for i in range(warmup_count)
    )
    facts += (
        _fact("pilot-a", warmup_count, season=PILOT, kickoff=PILOT_START),
        _fact(
            "pilot-b",
            warmup_count + 1,
            season=PILOT,
            kickoff=PILOT_START,
            home="synthetic-promoted-c",
            away="synthetic-promoted-d",
            score=(0, 0),
        ),
        _fact(
            "pilot-c",
            warmup_count + 2,
            season=PILOT,
            kickoff=PILOT_START + timedelta(days=7),
            home="synthetic-b",
            away="synthetic-a",
            score=(0, 1),
        ),
    )
    return TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=rights,
        facts=facts,
        actual_started_at_utc=REVIEWED + timedelta(minutes=1),
        actual_completed_at_utc=REVIEWED + timedelta(minutes=2),
        persisted_at_utc=ADMITTED,
    )


def _definition(admission):
    window = EloTrainingWindowV1.freeze(
        content_payload=EloTrainingWindowContentV1(
            competition_id=COMPETITION,
            seasons=tuple(
                TrainingSeasonV1(
                    season_sequence=i,
                    season_id=s,
                    role=r,
                    provider_seasons=(
                        ProviderSeasonRefV1(
                            source_id="synthetic-source",
                            provider_code="synthetic-provider",
                            provider_competition_id="synthetic-de-first",
                            provider_season_id=f"provider-{s}",
                        ),
                    ),
                )
                for i, (s, r) in enumerate(
                    (
                        (WARMUP, "WARMUP"),
                        (PILOT, "PILOT_TARGET"),
                        (PRODUCTION, "PRODUCTION_TARGET"),
                    )
                )
            ),
        )
    )
    targets = tuple(
        QuantIntegrityTargetV1(
            identity=f.content_payload.canonical_identity,
            training_fact_admission_id=admission.training_fact_admission_id,
            fact=AdmittedFactRefV1.of(f),
            fixture_source_available_at_utc=f.content_payload.fixture_source.source_available_at_utc,
            mapping_source_available_at_utc=f.content_payload.season_membership.content_payload.source_available_at_utc,
        )
        for f in admission.facts
        if f.content_payload.canonical_identity.season == PILOT
    )
    slices = tuple(
        QuantIntegritySliceV1.freeze(
            content_payload=QuantIntegritySliceContentV1(
                sequence=i,
                decision_as_of_at_utc=group[0].identity.kickoff_at_utc
                - timedelta(hours=1),
                evaluation_as_of_at_utc=group[-1].identity.kickoff_at_utc
                + timedelta(hours=2),
                targets=group,
                exclude_match_ids=tuple(
                    sorted(("production-future", *(t.fact.match_id for t in group)))
                ),
            )
        )
        for i, group in enumerate((targets[:2], targets[2:]))
    )
    return QuantIntegrityPlanDefinitionV1(
        integrity_pilot_series_id="synthetic-contract-series",
        provenance=QuantIntegrityProvenanceV1(
            evidence_use=IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY
        ),
        scope=QuantIntegrityScopeV1(
            competition_id=COMPETITION,
            provider_seasons=tuple(
                ReviewedProviderSeasonV1(
                    source_id="synthetic-source",
                    provider_code="synthetic-provider",
                    provider_competition_id="synthetic-de-first",
                    provider_season_id=f"provider-{season}",
                    canonical_season_id=season,
                )
                for season in (WARMUP, PILOT, PRODUCTION)
            ),
            reviewed_by="synthetic-reviewer",
            authority_reference="synthetic-contract-test://authority",
            authority_sha256=_hash("authority"),
            reviewed_at_utc=REVIEWED,
            raw_scope=_evidence("raw-scope"),
            evidence=_evidence(),
        ),
        training_window=window,
        admissions=(TrainingAdmissionPinV1.from_admission(admission),),
        cohort=QuantIntegrityCohortV1(
            season_id=PILOT,
            expected_match_count=3,
            full_schedule_match_ids=("pilot-a", "pilot-b", "pilot-c"),
            cohort_match_ids=("pilot-a", "pilot-b", "pilot-c"),
            season_completed_at_utc=PILOT_START + timedelta(days=30),
            reviewed_at_utc=REVIEWED,
            reviewed_by="synthetic-reviewer",
            raw_schedule=_evidence("raw-schedule"),
            evidence=_evidence("full-synthetic-schedule"),
        ),
        slices=slices,
        excluded_match_ids=("production-future",),
        terminal_projection=TerminalProjectionDefinitionV1(
            training_cutoff_at_utc=slices[-1].content_payload.evaluation_as_of_at_utc,
            exclude_match_ids=("production-future",),
        ),
        implementation_code_revision="3a675dde1e7101d08f77e83e2c6e9ea117ff7aef+synthetic-contract-test",
        build_recipe=ModelBuildRecipePinV1(
            recipe_id="synthetic-fixed-elo-recipe",
            recipe_hash=_hash("recipe"),
            evidence=_evidence("recipe"),
        ),
    )


class SyntheticClock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        current = self.now
        self.now += timedelta(seconds=1)
        return current


class SyntheticRepository:
    """Contract fake only; intentionally no database or real evidence claims."""

    def __init__(self, admission, definition):
        self.admission = admission
        self.metadata = definition
        self.plans = {}
        self.attempts = []
        self.outputs = {}
        self.reports = {}
        self.pending = None
        self.summary = None
        self.attestation = None
        self.events = []
        self.reads = 0
        self.fail_read = None
        self.require_attempt_for_reads = True
        self.fail_completion = False

    def verify_plan_metadata(self, definition, *, at_utc):
        self.events.append("verify_metadata_without_results")
        assert (
            definition.provenance.evidence_use
            == IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY
        )
        if (
            definition.admissions != self.metadata.admissions
            or definition.scope != self.metadata.scope
            or definition.cohort != self.metadata.cohort
        ):
            raise ValueError(
                "synthetic metadata does not verify exact scope/admission/cohort"
            )
        expected = {
            t.fact.match_id: t
            for s in self.metadata.slices
            for t in s.content_payload.targets
        }
        actual = {
            t.fact.match_id: t
            for s in definition.slices
            for t in s.content_payload.targets
        }
        if actual != expected:
            raise ValueError("synthetic target metadata cannot resolve all targets")
        assert max(p.persisted_at_utc for p in definition.admissions) <= at_utc

    def seal_plan(self, plan):
        self.events.append("seal_plan")
        self.plans[plan.artifact_id] = plan

    def load_plan(self, plan_ref):
        self.events.append("load_sealed_plan")
        plan = self.plans[plan_ref.artifact_id]
        assert IntegrityArtifactRefV1.of(plan) == plan_ref
        return plan

    def reserve_attempt(self, plan_ref, *, actual_started_at_utc):
        if self.attestation is not None:
            raise ValueError("terminal series is closed")
        if self.pending is not None:
            raise ValueError("outstanding reservation requires recovery")
        plan = self.plans[plan_ref.artifact_id]
        definition = plan.content_payload.definition
        self.pending = QuantIntegrityAttemptReservationV1.freeze(
            content_payload=QuantIntegrityAttemptReservationContentV1(
                integrity_pilot_series_id=definition.integrity_pilot_series_id,
                scope_hash=definition.scope_hash,
                sequence=len(self.attempts) + 1,
                plan_ref=plan_ref,
                prior_attempt_count=len(self.attempts),
                prior_attempt_root=integrity_attempt_root(self.attempts),
                actual_started_at_utc=actual_started_at_utc,
            )
        )
        self.events.append("reserve_attempt")
        return self.pending

    def load_verified_training_admission(self, pin, *, at_utc):
        if self.require_attempt_for_reads:
            assert self.plans and self.pending is not None, (
                "result read before sealed plan/reservation"
            )
        self.events.append("read_result_bindings")
        self.reads += 1
        if self.fail_read is not None and self.reads >= self.fail_read:
            raise ValueError("synthetic missing/corrected source graph")
        return self.admission

    def complete_attempt(self, attempt, *, output, report):
        if self.fail_completion:
            raise OSError("synthetic storage unavailable")
        assert attempt.content_payload.reservation == self.pending
        assert (
            self.pending.content_payload.prior_attempt_root
            == integrity_attempt_root(self.attempts)
        )
        if attempt.content_payload.status == "COMPLETED":
            assert (
                IntegrityArtifactRefV1.of(output) == attempt.content_payload.output_ref
            )
            assert (
                IntegrityArtifactRefV1.of(report) == attempt.content_payload.report_ref
            )
            self.outputs[output.artifact_id] = output
            self.reports[report.artifact_id] = report
        else:
            assert output is None and report is None
        self.attempts.append(attempt)
        self.pending = None
        self.events.append(f"complete_{attempt.content_payload.status}")

    def list_attempts(self, integrity_pilot_series_id):
        return tuple(self.attempts)

    def load_report(self, report_ref):
        return self.reports[report_ref.artifact_id]

    def seal_terminal_attestation(self, summary, attestation):
        if self.pending is not None or summary.content_payload.attempts != tuple(
            self.attempts
        ):
            raise ValueError("terminal attempt root changed")
        assert self.attestation is None
        assert attestation.content_payload.attempt_root == integrity_attempt_root(
            self.attempts
        )
        assert (
            attestation.content_payload.report_ref
            == self.attempts[-1].content_payload.report_ref
        )
        self.summary, self.attestation = summary, attestation
        self.events.append("seal_terminal_attestation")


@pytest.fixture
def synthetic():
    admission = _admission()
    definition = _definition(admission)
    repository = SyntheticRepository(admission, definition)
    return (
        admission,
        definition,
        repository,
        QuantIntegrityPilotService(repository, SyntheticClock()),
    )


def _select(
    facts, cutoff=NOW, exclusions=(), seasons=(WARMUP, PILOT, PRODUCTION), strict=True
):
    return select_admitted_training_facts(
        facts, COMPETITION, seasons, cutoff, exclusions, strict
    )


def test_synthetic_two_season_projection_preserves_exact_source_timestamps(synthetic):
    admission, _, _, _ = synthetic
    selected = _select(tuple(reversed(admission.facts)))
    assert selected == admission.facts
    projected = tuple(project_admitted_training_fact(f) for f in selected)
    assert tuple(p.season_id for p in projected) == (WARMUP,) * 5 + (PILOT,) * 3
    for fact, result in zip(selected, projected, strict=True):
        original = fact.content_payload.normalized_result
        assert result.available_at_utc == original.available_at_utc
        assert result.ingested_at_utc == original.ingested_at_utc
        assert (
            result.ingested_at_utc
            < fact.content_payload.fixture_source.local_imported_at_utc
        )
        assert result.payload_hash == original.payload_hash
        assert result.match_result_id == original.match_result_id
    assert all(p.season_id != PRODUCTION for p in projected)


def test_synthetic_helper_excludes_all_targets_even_already_visible(synthetic):
    admission, _, _, _ = synthetic
    selected = _select(
        admission.facts, exclusions=("pilot-a", "pilot-b", "pilot-c", "warmup-0")
    )
    assert tuple(f.content_payload.normalized_result.match_id for f in selected) == (
        "warmup-1",
        "warmup-2",
        "warmup-3",
        "warmup-4",
    )
    assert tuple(f.content_payload.sequence for f in selected) == (1, 2, 3, 4)


@pytest.mark.parametrize("gate", ("fixture_at", "membership_at", "result_at"))
def test_synthetic_each_source_availability_is_individually_strict(gate):
    cutoff = WARMUP_START + timedelta(days=1)
    fact = _fact("cutoff", **{gate: cutoff})
    assert _select((fact,), cutoff=cutoff) == ()
    assert _select((fact,), cutoff=cutoff, strict=False) == (fact,)
    assert _select((fact,), cutoff=cutoff + timedelta(microseconds=1)) == (fact,)
    assert (
        _select((fact,), cutoff=cutoff - timedelta(microseconds=1), strict=False) == ()
    )


def test_synthetic_later_mapping_does_not_overwrite_original_result_time():
    fact = _fact("late-map", membership_at=WARMUP_START + timedelta(hours=4))
    selected = _select((fact,))
    projection = project_admitted_training_fact(selected[0])
    assert projection.available_at_utc == WARMUP_START + timedelta(hours=2)
    assert projection.ingested_at_utc == projection.available_at_utc


def test_synthetic_conflicting_admission_and_elo_order_fails_closed():
    a = _fact(
        "a",
        result_at=WARMUP_START + timedelta(hours=2),
        membership_at=WARMUP_START + timedelta(hours=4),
    )
    b = _fact(
        "b",
        1,
        home="synthetic-c",
        away="synthetic-d",
        result_at=WARMUP_START + timedelta(hours=3),
    )
    for facts in ((a, b), (b, a)):
        with pytest.raises(ValueError, match="UNSUPPORTED_ELO_AVAILABILITY_ORDER"):
            _select(facts)
    assert (
        a.content_payload.normalized_result.available_at_utc
        == WARMUP_START + timedelta(hours=2)
    )


@pytest.mark.parametrize(
    "seasons", ((PILOT, WARMUP, PRODUCTION), (WARMUP, PILOT, WARMUP))
)
def test_synthetic_declared_season_order_and_contiguous_blocks_required(seasons):
    facts = (
        _fact("a", season=seasons[0]),
        _fact("b", 1, season=seasons[1], kickoff=WARMUP_START + timedelta(days=1)),
        _fact("c", 2, season=seasons[2], kickoff=WARMUP_START + timedelta(days=2)),
    )
    with pytest.raises(ValueError, match="contiguous chronological blocks"):
        _select(facts)


@pytest.mark.parametrize("field", ("available_at_utc", "ingested_at_utc"))
def test_synthetic_normalized_source_timestamp_disagreement_fails_closed(field):
    fact = _fact("tampered")
    original = fact.content_payload.normalized_result
    changed = original.model_copy(
        update={field: original.available_at_utc + timedelta(seconds=1)}
    )
    tampered = fact.model_copy(
        update={
            "content_payload": fact.content_payload.model_copy(
                update={"normalized_result": changed},
            )
        }
    )
    with pytest.raises(
        ValueError, match="timestamps|preserve source time|does not match"
    ):
        _select((tampered,), exclusions=("tampered",))


def test_synthetic_hash_content_revalidated_even_for_excluded_future_facts():
    fact = _fact("tampered")
    changed = fact.content_payload.fixture_source.model_copy(
        update={"fixture_source_archive_payload_sha256": "0" * 64}
    )
    tampered = fact.model_copy(
        update={
            "content_payload": fact.content_payload.model_copy(
                update={"fixture_source": changed}
            )
        }
    )
    with pytest.raises(ValueError, match="hash is inconsistent"):
        _select((tampered,), cutoff=WARMUP_START, exclusions=("tampered",))
    with pytest.raises(ValueError, match="unique"):
        _select((fact, fact))


def test_synthetic_provider_is_offline_pinned_and_not_approved(synthetic):
    admission, definition, repository, _ = synthetic
    repository.require_attempt_for_reads = False
    provider = AdmittedTrainingHistoryEloProvider(
        repository,
        definition.admissions,
        definition.training_window,
        definition.scope,
        NOW,
    )
    results = provider.fetch_elo_results(
        cutoff_at_utc=NOW,
        target_season_id=PRODUCTION,
        exclude_match_ids=("pilot-a", "pilot-b", "pilot-c"),
    )
    assert len(results) == 5
    assert provider.data_mode.value == "SOURCE_TIME_RESEARCH"
    assert provider.retrospective and provider.offline_only
    assert provider.training_use_class == "ADMITTED_INTERNAL_RESEARCH_ONLY"
    assert not hasattr(provider, "fetch_elo_training_history")
    assert provider.config_hash == FIXED_ELO_CONFIG_HASH
    with pytest.raises(TypeError):
        AdmittedTrainingHistoryEloProvider(
            repository,
            definition.admissions,
            definition.training_window,
            definition.scope,
            NOW,
            config=EloBaselineConfig(minimum_prior_matches=0),
        )
    repository.admission = admission.model_copy(update={"admission_hash": "0" * 64})
    with pytest.raises(ValueError, match="hash is inconsistent"):
        provider.load_facts()


def test_synthetic_fixed_elo_config_window_and_roles(synthetic):
    _, definition, _, service = synthetic
    config = EloBaselineConfig()
    assert config.config_hash == FIXED_ELO_CONFIG_HASH
    assert (
        config.initial_rating,
        config.k_factor,
        config.home_advantage,
        config.season_regression_factor,
        config.draw_probability,
        config.minimum_prior_matches,
    ) == (
        Decimal(1500),
        Decimal(20),
        Decimal(100),
        Decimal("0.75"),
        Decimal("0.25"),
        5,
    )
    window = definition.training_window
    assert set(window.content_payload.model_dump()) == {
        "competition_id",
        "seasons",
        "target_exclusion",
    }
    assert window.artifact_id == stable_id(
        "ELO_TRAINING_WINDOW_V1", window.content_hash
    )
    assert "cutoff" not in canonical_json(window.content_payload)
    assert (
        window.content_payload.pilot_target_season_id
        != window.content_payload.production_target_season_id
    )
    with pytest.raises(ValidationError, match="frozen"):
        window.content_payload.competition_id = "other"
    bad = definition.model_copy(
        update={"config_hash": EloBaselineConfig(k_factor=21).config_hash}
    )
    with pytest.raises(ValueError, match=FIXED_ELO_CONFIG_HASH):
        service.seal_plan(bad)
    bad_window = window.content_payload.model_copy(
        update={
            "seasons": (
                *window.content_payload.seasons[:-1],
                window.content_payload.seasons[-1].model_copy(
                    update={"season_id": PILOT}
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="unique"):
        EloTrainingWindowV1.freeze(content_payload=bad_window)


def test_synthetic_plan_is_sealed_before_any_target_result_access(synthetic):
    _, definition, repository, service = synthetic
    plan = service.seal_plan(definition)
    assert repository.reads == 0
    assert repository.events == ["verify_metadata_without_results", "seal_plan"]
    report = service.run(IntegrityArtifactRefV1.of(plan))
    assert repository.events.index("seal_plan") < repository.events.index(
        "reserve_attempt"
    )
    assert repository.events.index("reserve_attempt") < repository.events.index(
        "read_result_bindings"
    )
    assert (
        report.content_payload.provenance.evidence_use
        == IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY
    )
    assert (
        report.content_payload.provenance.independent_out_of_sample_validation is False
    )
    assert report.content_payload.provenance.production_authorized is False


def test_synthetic_full_cohort_denominators_unavailable_reasons_and_ece(synthetic):
    _, definition, repository, service = synthetic
    report = service.run(IntegrityArtifactRefV1.of(service.seal_plan(definition)))
    content = report.content_payload
    assert content.availability_denominator == 3
    assert content.availability_count == content.probability_metrics.sample_count == 2
    assert content.availability_rate == Decimal("0.666666666667")
    assert content.available_match_ids == ("pilot-a", "pilot-c")
    assert content.calibration_observation_count == 6
    assert sum(b.count for b in content.probability_metrics.calibration_bins) == 6
    unavailable = content.unavailable_reasons[0]
    assert unavailable.count == 1
    assert unavailable.reason == "INSUFFICIENT_PRIOR_MATCHES"
    assert unavailable.slices[0].match_ids == ("pilot-b",)
    assert (
        content.benchmark.status == "UNAVAILABLE"
        and content.benchmark.blocking is False
    )
    assert content.benchmark.reason == "NO_ADMISSIBLE_POINT_IN_TIME_ODDS_ARCHIVE"
    output = repository.outputs[content.output_ref.artifact_id]
    first, second = output.content_payload.slices
    assert not {"pilot-a", "pilot-b"}.intersection(first.state.training_match_ids)
    assert {"pilot-a", "pilot-b"}.issubset(second.state.training_match_ids)
    assert "pilot-c" not in second.state.training_match_ids
    missing = first.targets[1].prediction
    assert (
        missing.status == EloPredictionStatus.UNAVAILABLE
        and missing.probabilities is None
    )
    assert missing.home_prior_matches == missing.away_prior_matches == 0
    assert first.targets[0].prediction.probabilities.draw == Decimal("0.25")
    observations = tuple(
        (t.outcome, t.prediction.probabilities)
        for s in output.content_payload.slices
        for t in s.targets
        if t.prediction.probabilities is not None
    )
    assert content.probability_metrics == calculate_probability_metrics(observations)
    totals, events, counts = [Decimal(0)] * 10, [0] * 10, [0] * 10
    for outcome, probabilities in observations:
        for selection, probability in probabilities.items():
            index = min(int(probability * 10), 9)
            totals[index] += probability
            events[index] += int(selection == outcome)
            counts[index] += 1
    with localcontext() as context:
        context.prec = 50
        expected_ece = sum(abs(totals[i] - events[i]) for i in range(10)) / 6
        assert (
            content.probability_metrics.expected_calibration_error
            == expected_ece.quantize(
                Decimal("0.000000000001"),
                rounding=ROUND_HALF_EVEN,
            )
        )
    assert "roi" not in canonical_json(content).lower()
    assert "p_market" not in canonical_json(output)
    assert content.provenance.banner == "RETROSPECTIVE_SOURCE_TIME_RESEARCH"


def test_synthetic_repeated_report_is_deterministic_and_terminal_summary_is_complete(
    synthetic,
):
    admission, definition, repository, service = synthetic
    plan = service.seal_plan(definition)
    ref = IntegrityArtifactRefV1.of(plan)
    first, second = service.run(ref), service.run(ref)
    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert len(repository.attempts) == 2
    assert repository.attempts[0].content_hash != repository.attempts[1].content_hash
    assert tuple(
        a.content_payload.reservation.content_payload.sequence
        for a in repository.attempts
    ) == (1, 2)
    assert first.content_payload.replay.matched
    assert (
        first.content_payload.replay.output_hash
        == first.content_payload.replay.replay_output_hash
    )
    assert (
        first.content_payload.replay.state_hashes
        == first.content_payload.replay.replay_state_hashes
    )
    attestation = service.seal_terminal_attestation(ref)
    assert attestation.content_payload.attempt_count == 2
    assert attestation.content_payload.attempt_root == integrity_attempt_root(
        repository.attempts
    )
    assert (
        attestation.content_payload.terminal_state_core_ref
        == first.content_payload.terminal_state_core_ref
    )
    output = repository.outputs[first.content_payload.output_ref.artifact_id]
    core = output.content_payload.terminal_state_core
    expected_state = EloThreeWayBaseline().rebuild_state(
        tuple(project_admitted_training_fact(f) for f in admission.facts),
        definition.terminal_projection.training_cutoff_at_utc,
        target_season_id=PRODUCTION,
    )
    assert core.content_payload.teams == expected_state.teams
    assert core.content_payload.training_data_hash == expected_state.training_data_hash
    assert core.content_payload.training_facts[-1].season_id == PILOT
    assert core.content_payload.production_target_season_id == PRODUCTION
    assert "state_hash" not in core.content_payload.model_dump()
    assert "release_id" not in canonical_json(core)
    with pytest.raises(ValueError, match="terminal series is closed"):
        service.run(ref)
    assert len(repository.attempts) == 2


@pytest.mark.parametrize("fail_read", (1, 2, 3))
def test_synthetic_failure_at_initial_read_replay_or_completion_is_retained(
    synthetic, fail_read
):
    _, definition, repository, service = synthetic
    plan = service.seal_plan(definition)
    repository.fail_read = fail_read
    with pytest.raises(ValueError, match="missing/corrected"):
        service.run(IntegrityArtifactRefV1.of(plan))
    assert len(repository.attempts) == 1
    failed = repository.attempts[0].content_payload
    assert failed.status == "FAILED" and failed.failure.exception_type == "ValueError"
    assert failed.report_ref is None and failed.output_ref is None
    assert repository.outputs == repository.reports == {}
    with pytest.raises(ValueError, match="last attempt"):
        service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    repository.fail_read = None
    service.run(IntegrityArtifactRefV1.of(plan))
    attestation = service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    assert attestation.content_payload.attempt_count == 2
    assert (
        repository.summary.content_payload.attempts[0].content_payload.status
        == "FAILED"
    )


def test_synthetic_missing_target_result_is_not_silently_removed(synthetic):
    _, definition, repository, service = synthetic
    first = definition.slices[0].content_payload
    original = first.targets[1]
    impossible = original.model_copy(
        update={
            "fact": original.fact.model_copy(
                update={
                    "fact_hash": _hash("missing"),
                    "training_fact_binding_id": stable_id(
                        "TRAINING_FACT_BINDING_V1", _hash("missing")
                    ),
                }
            )
        }
    )
    changed_slice = QuantIntegritySliceV1.freeze(
        content_payload=first.model_copy(
            update={"targets": (first.targets[0], impossible)},
        )
    )
    definition = definition.model_copy(
        update={"slices": (changed_slice, definition.slices[1])}
    )
    # Deliberately faulty metadata fake: the runner must independently detect it.
    repository.metadata = definition
    plan = service.seal_plan(definition)
    with pytest.raises(ValueError, match="all plan targets"):
        service.run(IntegrityArtifactRefV1.of(plan))
    assert repository.attempts[-1].content_payload.status == "FAILED"
    assert not repository.reports


def test_synthetic_target_result_after_evaluation_fails_even_if_quant_unavailable(
    synthetic,
):
    _, definition, repository, service = synthetic
    first = definition.slices[0].content_payload
    altered = QuantIntegritySliceV1.freeze(
        content_payload=first.model_copy(
            update={
                "evaluation_as_of_at_utc": first.evaluation_as_of_at_utc
                - timedelta(microseconds=1),
            }
        )
    )
    definition = definition.model_copy(
        update={"slices": (altered, definition.slices[1])}
    )
    plan = service.seal_plan(definition)
    with pytest.raises(ValueError, match="unavailable at evaluation cutoff"):
        service.run(IntegrityArtifactRefV1.of(plan))
    assert repository.attempts[-1].content_payload.status == "FAILED"


def test_synthetic_all_unavailable_has_zero_score_denominator_not_zero_cohort():
    admission = _admission(warmup_count=4)
    definition = _definition(admission)
    later = definition.slices[1].content_payload
    later = QuantIntegritySliceV1.freeze(
        content_payload=later.model_copy(
            update={
                "exclude_match_ids": (
                    "pilot-a",
                    "pilot-b",
                    "pilot-c",
                    "production-future",
                ),
            }
        )
    )
    definition = definition.model_copy(update={"slices": (definition.slices[0], later)})
    repository = SyntheticRepository(admission, definition)
    service = QuantIntegrityPilotService(repository, SyntheticClock())
    content = service.run(
        IntegrityArtifactRefV1.of(service.seal_plan(definition))
    ).content_payload
    assert content.availability_denominator == 3 and content.availability_count == 0
    assert (
        content.probability_metrics.sample_count
        == content.calibration_observation_count
        == 0
    )
    assert content.probability_metrics.multiclass_brier_score is None
    assert content.probability_metrics.multiclass_log_loss is None
    assert content.probability_metrics.expected_calibration_error is None
    assert content.unavailable_reasons[0].count == 3


def test_synthetic_plan_rejects_late_evaluation_or_local_admission_before_reads(
    synthetic,
):
    _, definition, repository, _ = synthetic
    service = QuantIntegrityPilotService(
        repository, SyntheticClock(ADMITTED - timedelta(seconds=1))
    )
    with pytest.raises(ValueError, match="precede plan seal"):
        service.seal_plan(definition)
    later = definition.model_copy(
        update={
            "terminal_projection": TerminalProjectionDefinitionV1(
                training_cutoff_at_utc=NOW + timedelta(days=1),
                exclude_match_ids=("production-future",),
            )
        }
    )
    with pytest.raises(ValueError, match="precede plan seal"):
        QuantIntegrityPilotService(repository, SyntheticClock()).seal_plan(later)
    assert repository.events == [] and repository.reads == 0


def test_synthetic_exact_target_exclusions_and_full_schedule_are_mandatory(synthetic):
    _, definition, _, service = synthetic
    first = definition.slices[0].content_payload
    with pytest.raises(ValueError, match="ALL slice targets"):
        QuantIntegritySliceV1.freeze(
            content_payload=first.model_copy(update={"exclude_match_ids": ("pilot-a",)})
        )
    incomplete = definition.cohort.model_copy(
        update={"cohort_match_ids": ("pilot-a", "pilot-c")}
    )
    with pytest.raises(ValueError, match="full schedule"):
        service.seal_plan(definition.model_copy(update={"cohort": incomplete}))
    with pytest.raises(ValueError, match="expected count"):
        QuantIntegrityCohortV1.model_validate(
            {**definition.cohort.model_dump(), "expected_match_count": 306}
        )
    exception = CohortCompletenessExceptionV1(
        match_id="pilot-b",
        provider_fixture_key="key-pilot-b",
        reason="CANCELLED",
        disposition="EXCLUDED",
        explanation="Synthetic exception test, not a real cancellation",
        evidence=_evidence("cancelled"),
    )
    explained = QuantIntegrityCohortV1.model_validate(
        {
            **incomplete.model_dump(),
            "completeness_exceptions": (exception,),
        }
    )
    assert explained.expected_match_count == 3 and len(explained.cohort_match_ids) == 2
    with pytest.raises(ValueError, match="exact full cohort"):
        service.seal_plan(definition.model_copy(update={"cohort": explained}))


def test_synthetic_scope_is_explicit_not_inferred_from_labels(synthetic):
    _, definition, repository, service = synthetic
    assert "bundesliga" not in definition.scope.competition_id
    assert all(
        "2025" not in s.canonical_season_id for s in definition.scope.provider_seasons
    )
    assert definition.scope.reviewed_competition == "BUNDESLIGA"
    with pytest.raises(ValueError, match="BUNDESLIGA"):
        service.seal_plan(
            definition.model_copy(
                update={
                    "scope": definition.scope.model_copy(
                        update={
                            "reviewed_competition": "PREMIER_LEAGUE",
                        }
                    )
                }
            )
        )
    changed = definition.scope.model_copy(
        update={"evidence": _evidence("not-reviewed")}
    )
    with pytest.raises(ValueError, match="synthetic metadata"):
        service.seal_plan(definition.model_copy(update={"scope": changed}))
    assert not repository.plans and repository.reads == 0


def test_synthetic_artifact_envelopes_are_acyclic_and_revalidate_all_hashes(synthetic):
    _, definition, repository, service = synthetic
    plan = service.seal_plan(definition)
    report = service.run(IntegrityArtifactRefV1.of(plan))
    attestation = service.seal_terminal_attestation(IntegrityArtifactRefV1.of(plan))
    output = repository.outputs[report.content_payload.output_ref.artifact_id]
    artifacts = (
        definition.training_window,
        *definition.slices,
        plan,
        output,
        output.content_payload.terminal_state_core,
        report,
        repository.attempts[0].content_payload.reservation,
        repository.attempts[0],
        repository.summary,
        attestation,
    )
    for artifact in artifacts:
        assert artifact.content_hash == tagged_canonical_sha256(
            artifact.schema_version, artifact.content_payload
        )
        assert artifact.artifact_id == stable_id(
            artifact.schema_version, artifact.content_hash
        )
        payload = artifact.content_payload.model_dump()
        assert "artifact_id" not in payload and "content_hash" not in payload
        assert artifact.artifact_id not in canonical_json(payload)
        changed = artifact.model_dump()
        changed["content_hash"] = "0" * 64
        with pytest.raises(ValueError, match="hash mismatch"):
            type(artifact).model_validate(changed)
        changed = artifact.model_dump()
        changed["artifact_id"] = "forged-id"
        with pytest.raises(ValueError, match="ID mismatch"):
            type(artifact).model_validate(changed)
    assert attestation.artifact_id not in canonical_json(report)
    assert repository.summary.artifact_id not in canonical_json(repository.attempts)


def test_synthetic_report_denominator_tampering_rejected_even_if_resealed(synthetic):
    _, definition, _, service = synthetic
    report = service.run(IntegrityArtifactRefV1.of(service.seal_plan(definition)))
    for updates in (
        {"availability_count": 1},
        {"availability_denominator": 2},
        {"availability_rate": Decimal("0.9")},
        {"calibration_observation_count": 2},
        {"available_match_ids": ("pilot-a",)},
    ):
        with pytest.raises(ValueError, match="denominator|exact cohort"):
            type(report).freeze(
                content_payload=report.content_payload.model_copy(update=updates)
            )


def test_synthetic_failed_retention_reports_both_errors_and_leaves_reservation(
    synthetic,
):
    _, definition, repository, service = synthetic
    plan = service.seal_plan(definition)
    repository.fail_read = 1
    repository.fail_completion = True
    with pytest.raises(
        ExceptionGroup, match="failure retention could not be confirmed"
    ) as error:
        service.run(IntegrityArtifactRefV1.of(plan))
    assert len(error.value.exceptions) == 2
    assert repository.pending is not None and repository.attempts == []
    assert not repository.reports


def test_synthetic_metric_definition_is_frozen_and_epsilon_is_pinned(synthetic):
    _, definition, _, _ = synthetic
    different = definition.model_copy(
        update={
            "metric_definition": QuantIntegrityMetricDefinitionV1(
                log_loss_epsilon=Decimal("0.00001"),
            )
        }
    )
    assert definition.scope_hash == different.scope_hash
    assert tagged_canonical_sha256(
        "DEFINITION_TEST", definition
    ) != tagged_canonical_sha256("DEFINITION_TEST", different)
    for updates in (
        {"calibration_definition": "TOP_CLASS_10_BINS"},
        {"log_loss_epsilon": 0},
        {"log_loss_epsilon": Decimal("NaN")},
        {"score_denominator": "ONLY_FAVORABLE_RESULTS"},
    ):
        with pytest.raises(ValueError):
            QuantIntegrityMetricDefinitionV1.model_validate(updates)


def test_synthetic_terminal_core_rejects_resealed_math_or_training_hash_tampering(
    synthetic,
):
    _, definition, repository, service = synthetic
    report = service.run(IntegrityArtifactRefV1.of(service.seal_plan(definition)))
    core = repository.outputs[
        report.content_payload.output_ref.artifact_id
    ].content_payload.terminal_state_core
    content = core.content_payload
    changed_team = content.teams[0].model_copy(
        update={"rating": content.teams[0].rating + 1}
    )
    for update in (
        {"training_data_hash": "0" * 64},
        {"teams": (changed_team, *content.teams[1:])},
    ):
        with pytest.raises(ValueError, match="fixed Elo replay"):
            type(core).freeze(content_payload=content.model_copy(update=update))


def test_synthetic_replay_mismatch_is_not_a_successful_report(synthetic, monkeypatch):
    _, definition, repository, service = synthetic
    plan = service.seal_plan(definition)
    execute = service._execute
    calls = 0

    def inconsistent_output(plan, *, at_utc):
        nonlocal calls
        calls += 1
        output = execute(plan, at_utc=at_utc)
        return (
            output
            if calls == 1
            else output.model_copy(update={"content_hash": "0" * 64})
        )

    monkeypatch.setattr(service, "_execute", inconsistent_output)
    with pytest.raises(ValueError, match="deterministic replay mismatch"):
        service.run(IntegrityArtifactRefV1.of(plan))
    assert repository.attempts[-1].content_payload.status == "FAILED"
    assert not repository.reports


def test_synthetic_metric_exception_is_retained_without_partial_report(
    synthetic, monkeypatch
):
    _, definition, repository, service = synthetic
    plan = service.seal_plan(definition)

    def unavailable_metrics(*args, **kwargs):
        raise ArithmeticError("synthetic scoring failure")

    monkeypatch.setattr(
        "football_system.application.quant_integrity.calculate_probability_metrics",
        unavailable_metrics,
    )
    with pytest.raises(ArithmeticError, match="synthetic scoring failure"):
        service.run(IntegrityArtifactRefV1.of(plan))
    assert (
        repository.attempts[-1].content_payload.failure.exception_type
        == "ArithmeticError"
    )
    assert not repository.reports and not repository.outputs


def test_synthetic_missing_failed_attempt_cannot_be_hidden_from_terminal_summary(
    synthetic, monkeypatch
):
    _, definition, repository, service = synthetic
    ref = IntegrityArtifactRefV1.of(service.seal_plan(definition))
    repository.fail_read = 1
    with pytest.raises(ValueError):
        service.run(ref)
    repository.fail_read = None
    service.run(ref)
    monkeypatch.setattr(
        repository, "list_attempts", lambda series: (repository.attempts[1],)
    )
    with pytest.raises(ValueError, match="continuous same-scope attempts"):
        service.seal_terminal_attestation(ref)
    assert repository.attestation is None


def test_synthetic_reserved_attempt_detects_incomplete_repository_history(
    synthetic, monkeypatch
):
    _, definition, repository, service = synthetic
    ref = IntegrityArtifactRefV1.of(service.seal_plan(definition))
    service.run(ref)
    reads = repository.reads
    monkeypatch.setattr(repository, "list_attempts", lambda series: ())
    with pytest.raises(ValueError, match="reservation/prior history mismatch"):
        service.run(ref)
    assert repository.reads == reads
    assert (
        len(repository.attempts) == 2
        and repository.attempts[-1].content_payload.status == "FAILED"
    )


def test_synthetic_provider_rejects_correctly_resealed_but_unpinned_admission(
    synthetic,
):
    admission, definition, repository, service = synthetic
    ref = IntegrityArtifactRefV1.of(service.seal_plan(definition))
    repository.admission = TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=admission.source_rights_admission,
        facts=admission.facts,
        actual_started_at_utc=admission.content_payload.actual_started_at_utc,
        actual_completed_at_utc=admission.content_payload.actual_completed_at_utc,
        persisted_at_utc=admission.content_payload.persisted_at_utc
        + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="exact pinned metadata"):
        service.run(ref)
    assert repository.attempts[-1].content_payload.status == "FAILED"


def test_synthetic_provider_revalidates_expired_rights_at_actual_read_time(synthetic):
    admission, definition, repository, _ = synthetic
    repository.require_attempt_for_reads = False
    provider = AdmittedTrainingHistoryEloProvider(
        repository,
        definition.admissions,
        definition.training_window,
        definition.scope,
        admission.source_rights_admission.content_payload.rights_payload.expires_at_utc,
    )
    with pytest.raises(ValueError, match="not active"):
        provider.load_facts()


def test_synthetic_provider_cannot_accept_a_different_reviewed_provider_season(
    synthetic,
):
    _, definition, repository, _ = synthetic
    repository.require_attempt_for_reads = False
    changed = definition.scope.provider_seasons[0].model_copy(
        update={"provider_season_id": "not-this-season"}
    )
    scope = definition.scope.model_copy(
        update={"provider_seasons": (changed, *definition.scope.provider_seasons[1:])}
    )
    provider = AdmittedTrainingHistoryEloProvider(
        repository,
        definition.admissions,
        definition.training_window,
        scope,
        NOW,
    )
    with pytest.raises(ValueError, match="exact reviewed provider scope"):
        provider.load_facts()


def test_synthetic_elo_regression_preserves_real_season_transition(synthetic):
    admission, _, _, _ = synthetic
    baseline = EloThreeWayBaseline()
    warmup = tuple(project_admitted_training_fact(f) for f in admission.facts[:5])
    cutoff = PILOT_START - timedelta(hours=1)
    old = baseline.rebuild_state(warmup, cutoff, target_season_id=WARMUP)
    transitioned = baseline.rebuild_state(warmup, cutoff, target_season_id=PILOT)
    for old_team, new_team in zip(old.teams, transitioned.teams, strict=True):
        assert new_team.rating == (
            Decimal(1500) + Decimal("0.75") * (old_team.rating - 1500)
        ).quantize(
            Decimal("0.000000000001"),
            rounding=ROUND_HALF_EVEN,
        )
        assert new_team.prior_matches == old_team.prior_matches == 5
    assert all(f.season_id == WARMUP for f in transitioned.training_facts)


def test_synthetic_multiple_pinned_admissions_form_one_real_order(synthetic):
    admission, definition, repository, _ = synthetic
    divided = []
    for facts in (admission.facts[:5], admission.facts[5:]):
        resequenced = tuple(
            TrainingFactBindingV1.freeze(
                content_payload=f.content_payload.model_copy(
                    update={"sequence": i},
                )
            )
            for i, f in enumerate(facts)
        )
        divided.append(
            TrainingFactAdmissionV1.from_persisted(
                source_rights_admission=admission.source_rights_admission,
                facts=resequenced,
                actual_started_at_utc=admission.content_payload.actual_started_at_utc,
                actual_completed_at_utc=admission.content_payload.actual_completed_at_utc,
                persisted_at_utc=admission.content_payload.persisted_at_utc,
            )
        )
    by_id = {a.training_fact_admission_id: a for a in divided}
    pins = tuple(
        TrainingAdmissionPinV1.from_admission(by_id[key]) for key in sorted(by_id)
    )
    repository.load_verified_training_admission = lambda pin, **kwargs: by_id[
        pin.training_fact_admission_id
    ]
    provider = AdmittedTrainingHistoryEloProvider(
        repository, pins, definition.training_window, definition.scope, NOW
    )
    assert tuple(
        f.content_payload.normalized_result.match_id for f in provider.load_facts()
    ) == tuple(f.content_payload.normalized_result.match_id for f in admission.facts)


def test_synthetic_boundary_inputs_cannot_silently_change_time_mode(synthetic):
    admission, definition, _, service = synthetic
    with pytest.raises(ValueError, match="timezone-aware"):
        _select(admission.facts, cutoff=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="explicit boolean"):
        _select(admission.facts, strict="False")
    for changes in (
        {"decision_data_mode": "LIVE_STRICT"},
        {"production_authorized": True},
        {"model_training_use_class": "APPROVED_TRAINING_HISTORY"},
    ):
        with pytest.raises(ValueError):
            service.seal_plan(
                definition.model_copy(
                    update={
                        "provenance": definition.provenance.model_copy(update=changes),
                    }
                )
            )
