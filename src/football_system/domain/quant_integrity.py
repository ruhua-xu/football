"""ADR-0008 offline integrity contracts, not production authorization or storage.

Every sealed envelope hashes only its explicitly typed content_payload with its
schema tag. Own IDs/hashes, serialized copies and raw evidence bytes are outside
that payload. References point backwards: output -> plan, report -> output,
attempt -> report, summary -> attempts, attestation -> summary. No release IDs
are needed to construct the terminal Elo core.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from enum import StrEnum
from typing import Generic, Literal, Self, TypeVar

from pydantic import Field, model_validator

from football_system.domain.backtest import RATIO_QUANTUM, BacktestProbabilityMetrics
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    normalize_utc,
    stable_id,
)
from football_system.domain.market import SelectionKey
from football_system.domain.production_release import EloTrainingWindowV1
from football_system.domain.services.elo_baseline import (
    EloBaselinePrediction,
    EloBaselineState,
    EloRegularTimeResult,
    EloTeamState,
    EloThreeWayBaseline,
    EloTrainingFact,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    Reference,
    Sha256Digest,
    TrainingCanonicalMatchIdentityV1,
    TrainingFactAdmissionV1,
    TrainingFactBindingV1,
    tagged_canonical_sha256,
    training_fact_order_key,
)

FIXED_ELO_CONFIG_HASH = (
    "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
)
RETROSPECTIVE_BANNER = "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
ModelT = TypeVar("ModelT", bound=DomainModel)
PayloadT = TypeVar("PayloadT", bound=DomainModel)


def revalidate_integrity_model(value: ModelT) -> ModelT:
    # model_copy/model_construct bypass validation, including nested seals.
    return type(value).model_validate(value.model_dump(mode="python"))


class IntegrityArtifact(DomainModel, Generic[PayloadT]):
    schema_version: Identifier
    artifact_id: Identifier
    content_payload: PayloadT
    content_hash: Sha256Digest

    @classmethod
    def freeze(cls, *, content_payload: PayloadT) -> Self:
        """Construct content, not a claim that a repository has persisted it."""
        payload = revalidate_integrity_model(content_payload)
        schema = cls.model_fields["schema_version"].default
        digest = tagged_canonical_sha256(schema, payload)
        return cls(
            artifact_id=stable_id(schema, digest),
            content_payload=payload,
            content_hash=digest,
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        payload = revalidate_integrity_model(self.content_payload)
        digest = tagged_canonical_sha256(self.schema_version, payload)
        if self.content_hash != digest:
            raise ValueError("integrity artifact content hash mismatch")
        if self.artifact_id != stable_id(self.schema_version, digest):
            raise ValueError("integrity artifact ID mismatch")
        return self


class IntegrityArtifactRefV1(DomainModel):
    schema_version: Identifier
    artifact_id: Identifier
    content_hash: Sha256Digest

    @classmethod
    def of(cls, artifact: IntegrityArtifact) -> Self:
        artifact = revalidate_integrity_model(artifact)
        return cls(
            schema_version=artifact.schema_version,
            artifact_id=artifact.artifact_id,
            content_hash=artifact.content_hash,
        )

    @model_validator(mode="after")
    def validate_id(self) -> Self:
        if self.artifact_id != stable_id(self.schema_version, self.content_hash):
            raise ValueError("integrity reference ID mismatch")
        return self


class IntegrityEvidenceUse(StrEnum):
    SYNTHETIC_CONTRACT_ONLY = "SYNTHETIC_CONTRACT_ONLY"
    REAL_SOURCE = "REAL_SOURCE"


class QuantIntegrityProvenanceV1(DomainModel):
    evidence_use: IntegrityEvidenceUse
    decision_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    model_training_source_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    model_training_use_class: Literal["ADMITTED_INTERNAL_RESEARCH_ONLY"] = (
        "ADMITTED_INTERNAL_RESEARCH_ONLY"
    )
    training_history_approval_id: None = None
    training_history_approval_hash: None = None
    production_model_release_id: None = None
    production_model_release_hash: None = None
    retrospective_source_facts_present: Literal[True] = True
    banner: Literal["RETROSPECTIVE_SOURCE_TIME_RESEARCH"] = RETROSPECTIVE_BANNER
    independent_out_of_sample_validation: Literal[False] = False
    production_authorized: Literal[False] = False


def _unique(values: tuple, label: str, *, sorted_values: bool = False) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    if sorted_values and values != tuple(sorted(values)):
        raise ValueError(f"{label} must be sorted")


def project_admitted_training_fact(fact: TrainingFactBindingV1) -> EloRegularTimeResult:
    """Project only the Elo allowlist, preserving original normalized source times."""
    binding = revalidate_integrity_model(fact).content_payload
    result = binding.normalized_result
    identity = binding.canonical_identity
    return EloRegularTimeResult(
        match_result_id=result.match_result_id,
        match_id=result.match_id,
        season_id=binding.season_membership.content_payload.canonical_season_id,
        home_team_id=identity.internal_home_team_id,
        away_team_id=identity.internal_away_team_id,
        kickoff_at_utc=identity.kickoff_at_utc,
        available_at_utc=result.available_at_utc,
        ingested_at_utc=result.ingested_at_utc,
        home_goals=result.home_goals,
        away_goals=result.away_goals,
        payload_hash=result.payload_hash,
        supersedes_match_result_id=result.supersedes_match_result_id,
    )


def select_admitted_training_facts(
    facts: Iterable[TrainingFactBindingV1],
    competition_id: str,
    ordered_season_ids: Iterable[str],
    cutoff_at_utc: datetime,
    exclude_match_ids: Iterable[str],
    strict_cutoff: bool = True,
) -> tuple[TrainingFactBindingV1, ...]:
    """Pure selection, not proof of rights or storage; callers must pin admissions.

    All input seals are revalidated, even for excluded/future facts. Input iterable
    order is irrelevant; original admission sequences and fact hashes are retained.
    Strict pilot cutoffs use <; an offline release projection may explicitly use <=.
    """
    cutoff = normalize_utc(cutoff_at_utc)
    if not isinstance(strict_cutoff, bool):
        raise ValueError("strict_cutoff must be an explicit boolean")
    seasons = tuple(ordered_season_ids)
    if not competition_id.strip() or not seasons or any(not s.strip() for s in seasons):
        raise ValueError("competition and ordered seasons are required")
    _unique(seasons, "ordered season IDs")
    excluded = frozenset(exclude_match_ids)
    validated = tuple(revalidate_integrity_model(fact) for fact in facts)
    _unique(
        tuple(f.content_payload.normalized_result.match_id for f in validated),
        "matches",
    )
    _unique(
        tuple(f.content_payload.normalized_result.match_result_id for f in validated),
        "results",
    )
    selected = []
    for fact in validated:
        binding = fact.content_payload
        identity = binding.canonical_identity
        if (
            identity.internal_competition_id != competition_id
            or identity.season not in seasons
            or identity.internal_match_id in excluded
        ):
            continue
        times = (
            binding.fixture_source.source_available_at_utc,
            binding.provider_mapping.available_at_utc,
            binding.season_membership.content_payload.source_available_at_utc,
            binding.match_result_admission.content_payload.source_available_at_utc,
            binding.normalized_result.available_at_utc,
            binding.normalized_result.ingested_at_utc,
        )
        if not all(t < cutoff if strict_cutoff else t <= cutoff for t in times):
            continue
        selected.append(fact)

    def admission_key(fact: TrainingFactBindingV1):
        binding = fact.content_payload
        return training_fact_order_key(
            fixture_source=binding.fixture_source,
            season_membership=binding.season_membership,
            match_result_admission=binding.match_result_admission,
            canonical_identity=binding.canonical_identity,
            normalized_result=binding.normalized_result,
        )

    ordered = tuple(sorted(selected, key=admission_key))
    season_indices = tuple(
        seasons.index(f.content_payload.canonical_identity.season) for f in ordered
    )
    if season_indices != tuple(sorted(season_indices)):
        raise ValueError(
            "season IDs must form declared contiguous chronological blocks"
        )
    # Elo V1 sorts by result availability, NOT max(fixture, membership, result).
    # Do not rewrite timestamps or silently change the admitted mathematical order.
    elo_order = tuple(
        sorted(
            ordered,
            key=lambda f: (
                f.content_payload.canonical_identity.kickoff_at_utc,
                f.content_payload.normalized_result.available_at_utc,
                f.content_payload.normalized_result.ingested_at_utc,
                f.content_payload.normalized_result.match_id,
                f.content_payload.normalized_result.match_result_id,
            ),
        )
    )
    if elo_order != ordered:
        raise ValueError(
            "UNSUPPORTED_ELO_AVAILABILITY_ORDER: fail closed without time mutation"
        )
    return ordered


class TrainingArchivePinV1(DomainModel):
    kind: Literal["FIXTURE", "SEASON_MEMBERSHIP", "RESULT"]
    archive_id: Identifier
    payload_hash: Sha256Digest
    created_at_utc: UtcDateTime
    local_imported_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if (
            not self.local_imported_at_utc
            <= self.created_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("archive possession times are inconsistent")
        return self


class TrainingAdmissionPinV1(DomainModel):
    training_fact_admission_id: Identifier
    admission_hash: Sha256Digest
    admitted_fact_count: int = Field(ge=1, strict=True)
    admitted_fact_root: Sha256Digest
    source_rights_admission_id: Identifier
    source_rights_admission_hash: Sha256Digest
    source_rights_recorded_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    source_classification: Literal["REAL_SOURCE_DATA"] = "REAL_SOURCE_DATA"
    archives: tuple[TrainingArchivePinV1, ...] = Field(min_length=3)

    @classmethod
    def from_admission(cls, admission: TrainingFactAdmissionV1) -> Self:
        """Projection for repository metadata creation, never a pre-plan result read."""
        admission = revalidate_integrity_model(admission)
        archives: dict[tuple[str, str], TrainingArchivePinV1] = {}
        for fact in admission.facts:
            binding = fact.content_payload
            fixture = binding.fixture_source
            membership = binding.season_membership.content_payload
            result = binding.match_result_admission.content_payload
            for kind, aid, digest, created, imported, registered in (
                (
                    "FIXTURE",
                    fixture.fixture_source_archive_id,
                    fixture.fixture_source_archive_payload_sha256,
                    fixture.fixture_source_archive_created_at_utc,
                    fixture.local_imported_at_utc,
                    fixture.registered_at_utc,
                ),
                (
                    "SEASON_MEMBERSHIP",
                    membership.provider_scope_raw_artifact_id,
                    membership.provider_scope_payload_sha256,
                    membership.provider_scope_created_at_utc,
                    membership.local_imported_at_utc,
                    membership.registered_at_utc,
                ),
                (
                    "RESULT",
                    result.raw_artifact_id,
                    result.raw_artifact_payload_sha256,
                    result.raw_artifact_created_at_utc,
                    result.local_imported_at_utc,
                    result.registered_at_utc,
                ),
            ):
                archives[kind, aid] = TrainingArchivePinV1(
                    kind=kind,
                    archive_id=aid,
                    payload_hash=digest,
                    created_at_utc=created,
                    local_imported_at_utc=imported,
                    registered_at_utc=registered,
                )
        content = admission.content_payload
        return cls(
            training_fact_admission_id=admission.training_fact_admission_id,
            admission_hash=admission.admission_hash,
            admitted_fact_count=content.admitted_fact_count,
            admitted_fact_root=content.admitted_fact_root,
            source_rights_admission_id=content.source_rights_admission_id,
            source_rights_admission_hash=content.source_rights_admission_hash,
            source_rights_recorded_at_utc=(
                admission.source_rights_admission.content_payload.recorded_at_utc
            ),
            persisted_at_utc=content.persisted_at_utc,
            archives=tuple(archives[key] for key in sorted(archives)),
        )

    @model_validator(mode="after")
    def validate_pin(self) -> Self:
        _unique(
            tuple((a.kind, a.archive_id) for a in self.archives),
            "archive pins",
            sorted_values=True,
        )
        if {a.kind for a in self.archives} != {
            "FIXTURE",
            "SEASON_MEMBERSHIP",
            "RESULT",
        }:
            raise ValueError("all three archive kinds must be pinned")
        if any(
            not self.source_rights_recorded_at_utc
            <= a.local_imported_at_utc
            <= a.registered_at_utc
            <= self.persisted_at_utc
            for a in self.archives
        ):
            raise ValueError(
                "rights/archive/admission possession chain is inconsistent"
            )
        return self


class ReviewedProviderSeasonV1(DomainModel):
    source_id: Identifier
    provider_code: Identifier
    provider_competition_id: Identifier
    provider_season_id: Identifier
    canonical_season_id: Identifier


class QuantIntegrityScopeV1(DomainModel):
    competition_id: Identifier
    reviewed_competition: Literal["BUNDESLIGA"] = "BUNDESLIGA"
    country_code: Literal["DE"] = "DE"
    competition_type: Literal["DOMESTIC_LEAGUE"] = "DOMESTIC_LEAGUE"
    provider_seasons: tuple[ReviewedProviderSeasonV1, ...] = Field(min_length=2)
    reviewed_by: Identifier
    authority_reference: Reference
    authority_sha256: Sha256Digest
    reviewed_at_utc: UtcDateTime
    raw_scope: LocalReviewEvidenceV1
    evidence: LocalReviewEvidenceV1

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        _unique(
            tuple(s.canonical_season_id for s in self.provider_seasons),
            "reviewed seasons",
        )
        _unique(
            tuple(
                (s.provider_code, s.provider_competition_id, s.provider_season_id)
                for s in self.provider_seasons
            ),
            "provider seasons",
        )
        return self


class CohortCompletenessExceptionV1(DomainModel):
    match_id: Identifier
    provider_fixture_key: Identifier
    reason: Literal["CANCELLED", "POSTPONED", "MISSING", "PROVIDER_SCOPE_DIFFERENCE"]
    disposition: Literal["EXCLUDED", "INCLUDED"]
    explanation: Reference
    evidence: LocalReviewEvidenceV1


class QuantIntegrityCohortV1(DomainModel):
    """Explicit reviewed schedule metadata, not a count inferred from season labels."""

    season_id: Identifier
    expected_match_count: int = Field(ge=1, strict=True)
    full_schedule_match_ids: tuple[Identifier, ...] = Field(min_length=1)
    cohort_match_ids: tuple[Identifier, ...] = Field(min_length=1)
    completeness_exceptions: tuple[CohortCompletenessExceptionV1, ...] = ()
    season_completed_at_utc: UtcDateTime
    reviewed_at_utc: UtcDateTime
    reviewed_by: Identifier
    raw_schedule: LocalReviewEvidenceV1
    evidence: LocalReviewEvidenceV1
    completeness_rule: Literal["FULL_REVIEWED_SCHEDULE_LESS_EXPLICIT_EXCEPTIONS"] = (
        "FULL_REVIEWED_SCHEDULE_LESS_EXPLICIT_EXCEPTIONS"
    )

    @model_validator(mode="after")
    def validate_cohort(self) -> Self:
        _unique(self.full_schedule_match_ids, "full schedule", sorted_values=True)
        _unique(self.cohort_match_ids, "cohort", sorted_values=True)
        _unique(
            tuple(e.match_id for e in self.completeness_exceptions),
            "exceptions",
            sorted_values=True,
        )
        if len(self.full_schedule_match_ids) != self.expected_match_count:
            raise ValueError("expected count must match the full explicit schedule")
        schedule = set(self.full_schedule_match_ids)
        if any(e.match_id not in schedule for e in self.completeness_exceptions):
            raise ValueError("every completeness exception requires a schedule match")
        excluded = {
            e.match_id
            for e in self.completeness_exceptions
            if e.disposition == "EXCLUDED"
        }
        if set(self.cohort_match_ids) != schedule - excluded:
            raise ValueError(
                "cohort must contain the full schedule less explicit exceptions"
            )
        if self.season_completed_at_utc > self.reviewed_at_utc:
            raise ValueError("pilot season must be completed before cohort review")
        return self


class AdmittedFactRefV1(DomainModel):
    training_fact_binding_id: Identifier
    fact_hash: Sha256Digest
    match_id: Identifier
    match_result_id: Identifier

    @model_validator(mode="after")
    def validate_id(self) -> Self:
        if self.training_fact_binding_id != stable_id(
            "TRAINING_FACT_BINDING_V1", self.fact_hash
        ):
            raise ValueError("admitted fact reference ID mismatch")
        return self

    @classmethod
    def of(cls, fact: TrainingFactBindingV1) -> Self:
        fact = revalidate_integrity_model(fact)
        result = fact.content_payload.normalized_result
        return cls(
            training_fact_binding_id=fact.training_fact_binding_id,
            fact_hash=fact.fact_hash,
            match_id=result.match_id,
            match_result_id=result.match_result_id,
        )


class QuantIntegrityTargetV1(DomainModel):
    """Metadata-only target projection; no score or result source times."""

    identity: TrainingCanonicalMatchIdentityV1
    training_fact_admission_id: Identifier
    fact: AdmittedFactRefV1
    fixture_source_available_at_utc: UtcDateTime
    mapping_source_available_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.fact.match_id != self.identity.internal_match_id:
            raise ValueError("target metadata identity mismatch")
        return self


class QuantIntegritySliceContentV1(DomainModel):
    sequence: int = Field(ge=0, strict=True)
    decision_as_of_at_utc: UtcDateTime
    evaluation_as_of_at_utc: UtcDateTime
    targets: tuple[QuantIntegrityTargetV1, ...] = Field(min_length=1)
    exclude_match_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_slice(self) -> Self:
        ids = tuple(t.fact.match_id for t in self.targets)
        _unique(ids, "slice targets", sorted_values=True)
        _unique(self.exclude_match_ids, "slice exclusions", sorted_values=True)
        if not set(ids).issubset(self.exclude_match_ids):
            raise ValueError("ALL slice targets must be excluded from training")
        for target in self.targets:
            if not (
                max(
                    target.fixture_source_available_at_utc,
                    target.mapping_source_available_at_utc,
                )
                < self.decision_as_of_at_utc
                < target.identity.kickoff_at_utc
                < self.evaluation_as_of_at_utc
            ):
                raise ValueError(
                    "target fixture/mapping/decision/kickoff/evaluation chain is invalid"
                )
        return self


class QuantIntegritySliceV1(IntegrityArtifact[QuantIntegritySliceContentV1]):
    schema_version: Literal["QUANT_INTEGRITY_SLICE_V1"] = "QUANT_INTEGRITY_SLICE_V1"


class QuantIntegrityMetricDefinitionV1(DomainModel):
    metrics_version: Literal["BACKTEST_METRICS_V1"] = "BACKTEST_METRICS_V1"
    log_loss_clip_version: Literal["EPSILON_CLIP_V1"] = "EPSILON_CLIP_V1"
    log_loss_epsilon: Decimal = Field(
        default=Decimal("0.000001"), gt=0, lt=0.5, allow_inf_nan=False
    )
    calibration_definition: Literal["THREE_CLASS_POOLED_EQUAL_WIDTH_10_BINS_V1"] = (
        "THREE_CLASS_POOLED_EQUAL_WIDTH_10_BINS_V1"
    )
    bin_rule: Literal["MIN(FLOOR(P*10),9);[LOWER,UPPER);LAST_INCLUDES_1"] = (
        "MIN(FLOOR(P*10),9);[LOWER,UPPER);LAST_INCLUDES_1"
    )
    ece_rule: Literal["SUM(BIN_COUNT/(3*N)*ABS(MEAN_P-OBSERVED_FREQUENCY))"] = (
        "SUM(BIN_COUNT/(3*N)*ABS(MEAN_P-OBSERVED_FREQUENCY))"
    )
    score_denominator: Literal["AVAILABLE_QUANT_WITH_RESOLVED_RESULT"] = (
        "AVAILABLE_QUANT_WITH_RESOLVED_RESULT"
    )
    availability_denominator: Literal["ALL_PREDECLARED_COHORT_MATCHES"] = (
        "ALL_PREDECLARED_COHORT_MATCHES"
    )


class ModelBuildRecipePinV1(DomainModel):
    recipe_id: Identifier
    recipe_hash: Sha256Digest
    evidence: LocalReviewEvidenceV1


class TerminalProjectionDefinitionV1(DomainModel):
    training_cutoff_at_utc: UtcDateTime
    exclude_match_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_exclusions(self) -> Self:
        _unique(self.exclude_match_ids, "terminal exclusions", sorted_values=True)
        return self


class QuantIntegrityPlanDefinitionV1(DomainModel):
    integrity_pilot_series_id: Identifier
    previous_terminal_attestation: IntegrityArtifactRefV1 | None = None
    provenance: QuantIntegrityProvenanceV1
    scope: QuantIntegrityScopeV1
    training_window: EloTrainingWindowV1
    admissions: tuple[TrainingAdmissionPinV1, ...] = Field(min_length=1)
    cohort: QuantIntegrityCohortV1
    slices: tuple[QuantIntegritySliceV1, ...] = Field(min_length=1)
    excluded_match_ids: tuple[Identifier, ...] = ()
    terminal_projection: TerminalProjectionDefinitionV1
    metric_definition: QuantIntegrityMetricDefinitionV1 = (
        QuantIntegrityMetricDefinitionV1()
    )
    implementation_code_revision: Identifier
    build_recipe: ModelBuildRecipePinV1
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1"] = "ELO_THREE_WAY_BASELINE_V1"
    model_version: Literal["1"] = "1"
    config_hash: Literal[FIXED_ELO_CONFIG_HASH] = FIXED_ELO_CONFIG_HASH
    calibration_label: Literal["BASELINE_UNCALIBRATED"] = "BASELINE_UNCALIBRATED"
    parameter_policy: Literal["NO_PARAMETER_TUNING"] = "NO_PARAMETER_TUNING"
    selection_policy: Literal["NO_ROI_MODEL_SELECTION"] = "NO_ROI_MODEL_SELECTION"

    @property
    def integrity_pilot_scope_id(self) -> str:
        return stable_id("QUANT_INTEGRITY_PILOT_SCOPE_V1", self.scope_hash)

    @property
    def scope_hash(self) -> str:
        # Cohort, metrics and cutoffs are deliberately NOT a way to hide attempts.
        return tagged_canonical_sha256(
            "QUANT_INTEGRITY_PILOT_SCOPE_V1",
            {
                "competition_id": self.scope.competition_id,
                "pilot_target_season_id": self.cohort.season_id,
                "evidence_use": self.provenance.evidence_use,
                "model_name": self.model_name,
            },
        )

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        window = self.training_window.content_payload
        if self.scope.competition_id != window.competition_id:
            raise ValueError("reviewed competition does not match window")
        if (
            tuple(s.canonical_season_id for s in self.scope.provider_seasons)
            != window.ordered_season_ids
        ):
            raise ValueError(
                "explicit reviewed provider season scope must match ordered window"
            )
        for season, provider in zip(
            window.seasons, self.scope.provider_seasons, strict=True
        ):
            expected = tuple(
                (
                    p.source_id,
                    p.provider_code,
                    p.provider_competition_id,
                    p.provider_season_id,
                )
                for p in season.provider_seasons
            )
            if expected != (
                (
                    provider.source_id,
                    provider.provider_code,
                    provider.provider_competition_id,
                    provider.provider_season_id,
                ),
            ):
                raise ValueError(
                    "pilot V1 requires exact single-provider season scopes"
                )
        if self.cohort.season_id != window.pilot_target_season_id:
            raise ValueError("cohort must be the distinct pilot target season")
        admission_ids = tuple(p.training_fact_admission_id for p in self.admissions)
        _unique(admission_ids, "admission pins", sorted_values=True)
        if tuple(s.content_payload.sequence for s in self.slices) != tuple(
            range(len(self.slices))
        ):
            raise ValueError("slice sequence must be contiguous")
        decisions = tuple(s.content_payload.decision_as_of_at_utc for s in self.slices)
        if decisions != tuple(sorted(decisions)):
            raise ValueError("slice decisions must be chronological")
        targets = tuple(t for s in self.slices for t in s.content_payload.targets)
        ids = tuple(t.fact.match_id for t in targets)
        _unique(ids, "plan targets")
        if tuple(sorted(ids)) != self.cohort.cohort_match_ids:
            raise ValueError("slices must cover the exact full cohort")
        for target in targets:
            if (
                target.identity.internal_competition_id != window.competition_id
                or target.identity.competition_type != self.scope.competition_type
                or target.identity.season != window.pilot_target_season_id
                or target.training_fact_admission_id not in admission_ids
                or target.identity.kickoff_at_utc >= self.cohort.season_completed_at_utc
            ):
                raise ValueError(
                    "target is outside reviewed competition/season/admission scope"
                )
        _unique(self.excluded_match_ids, "plan exclusions", sorted_values=True)
        for item in self.slices:
            if not set(self.excluded_match_ids).issubset(
                item.content_payload.exclude_match_ids
            ):
                raise ValueError("slice must include all plan exclusions")
        if not set(self.excluded_match_ids).issubset(
            self.terminal_projection.exclude_match_ids
        ):
            raise ValueError("terminal projection must include all plan exclusions")
        if self.terminal_projection.training_cutoff_at_utc < max(
            s.content_payload.evaluation_as_of_at_utc for s in self.slices
        ):
            raise ValueError(
                "terminal training cutoff cannot precede slice evaluations"
            )
        return self


class QuantIntegrityInputRootsV1(DomainModel):
    source_root: Sha256Digest
    season_root: Sha256Digest
    admitted_facts_root: Sha256Digest

    @classmethod
    def of(cls, definition: QuantIntegrityPlanDefinitionV1) -> Self:
        return cls(
            source_root=tagged_canonical_sha256(
                "QUANT_INTEGRITY_SOURCE_ROOT_V1", definition.admissions
            ),
            season_root=tagged_canonical_sha256(
                "QUANT_INTEGRITY_SEASON_ROOT_V1",
                {
                    "scope": definition.scope,
                    "window": definition.training_window,
                },
            ),
            admitted_facts_root=tagged_canonical_sha256(
                "QUANT_INTEGRITY_ADMISSION_ROOT_V1",
                [
                    {
                        "admission_hash": p.admission_hash,
                        "fact_count": p.admitted_fact_count,
                        "fact_root": p.admitted_fact_root,
                    }
                    for p in definition.admissions
                ],
            ),
        )


class QuantIntegrityPlanContentV1(DomainModel):
    definition: QuantIntegrityPlanDefinitionV1
    input_roots: QuantIntegrityInputRootsV1
    sealed_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        definition = self.definition
        if self.input_roots != QuantIntegrityInputRootsV1.of(definition):
            raise ValueError("plan input roots mismatch")
        times = (
            definition.scope.reviewed_at_utc,
            definition.cohort.reviewed_at_utc,
            definition.terminal_projection.training_cutoff_at_utc,
            *(p.persisted_at_utc for p in definition.admissions),
            *(s.content_payload.evaluation_as_of_at_utc for s in definition.slices),
        )
        if max(times) > self.sealed_at_utc:
            raise ValueError(
                "evaluation and local admission/review must precede plan seal"
            )
        return self


class QuantIntegrityPlanV1(IntegrityArtifact[QuantIntegrityPlanContentV1]):
    schema_version: Literal["PRODUCTION_QUANT_INTEGRITY_PILOT_PLAN_V1"] = (
        "PRODUCTION_QUANT_INTEGRITY_PILOT_PLAN_V1"
    )


def admitted_selection_root(facts: Iterable[AdmittedFactRefV1]) -> str:
    return tagged_canonical_sha256(
        "QUANT_INTEGRITY_SELECTED_FACT_ROOT_V1",
        {"facts": [{"sequence": i, "fact": fact} for i, fact in enumerate(facts)]},
    )


class TerminalEloStateCoreContentV1(DomainModel):
    """Training-cutoff-frozen data for a later release bridge, NOT a release.

    No plan/attempt/attestation/release IDs or run-scoped state hash enter this core.
    A release implementation must explicitly verify this schema and projection.
    """

    training_cutoff_at_utc: UtcDateTime
    production_target_season_id: Identifier
    training_window_hash: Sha256Digest
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1"] = "ELO_THREE_WAY_BASELINE_V1"
    model_version: Literal["1"] = "1"
    config_hash: Literal[FIXED_ELO_CONFIG_HASH] = FIXED_ELO_CONFIG_HASH
    calibration_label: Literal["BASELINE_UNCALIBRATED"] = "BASELINE_UNCALIBRATED"
    teams: tuple[EloTeamState, ...]
    training_facts: tuple[EloTrainingFact, ...]
    training_data_hash: Sha256Digest
    admitted_fact_refs: tuple[AdmittedFactRefV1, ...]
    admitted_facts_root: Sha256Digest

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if tuple(f.match_result_id for f in self.training_facts) != tuple(
            f.match_result_id for f in self.admitted_fact_refs
        ) or self.admitted_facts_root != admitted_selection_root(
            self.admitted_fact_refs
        ):
            raise ValueError("terminal core admitted lineage mismatch")
        rebuilt = EloThreeWayBaseline().rebuild_state(
            tuple(
                EloRegularTimeResult(
                    match_result_id=f.match_result_id,
                    match_id=f.match_id,
                    season_id=f.season_id,
                    home_team_id=f.home_team_id,
                    away_team_id=f.away_team_id,
                    kickoff_at_utc=f.kickoff_at_utc,
                    available_at_utc=f.available_at_utc,
                    ingested_at_utc=f.ingested_at_utc,
                    home_goals=f.home_goals,
                    away_goals=f.away_goals,
                    payload_hash=f.source_payload_hash,
                    supersedes_match_result_id=f.supersedes_match_result_id,
                )
                for f in self.training_facts
            ),
            self.training_cutoff_at_utc,
            target_season_id=self.production_target_season_id,
        )
        if (
            rebuilt.config_hash != self.config_hash
            or rebuilt.teams != self.teams
            or rebuilt.training_data_hash != self.training_data_hash
            or rebuilt.training_facts != self.training_facts
            or rebuilt.training_match_ids
            != tuple(f.match_id for f in self.admitted_fact_refs)
        ):
            raise ValueError("terminal core must equal the fixed Elo replay")
        return self


class TerminalEloStateCoreV1(IntegrityArtifact[TerminalEloStateCoreContentV1]):
    schema_version: Literal["QUANT_INTEGRITY_TERMINAL_ELO_STATE_CORE_V1"] = (
        "QUANT_INTEGRITY_TERMINAL_ELO_STATE_CORE_V1"
    )


class QuantIntegrityTargetOutputV1(DomainModel):
    result_fact: AdmittedFactRefV1
    outcome: SelectionKey
    prediction: EloBaselinePrediction

    @model_validator(mode="after")
    def validate_prediction(self) -> Self:
        if self.result_fact.match_id != self.prediction.match_id:
            raise ValueError("output result/prediction identity mismatch")
        if self.prediction.config_hash != FIXED_ELO_CONFIG_HASH:
            raise ValueError("output requires fixed Elo config")
        return self


class QuantIntegritySliceOutputV1(DomainModel):
    slice_ref: IntegrityArtifactRefV1
    state: EloBaselineState
    admitted_training_refs: tuple[AdmittedFactRefV1, ...]
    targets: tuple[QuantIntegrityTargetOutputV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_output(self) -> Self:
        if self.state.config_hash != FIXED_ELO_CONFIG_HASH:
            raise ValueError("slice state requires fixed Elo config")
        if self.state.training_result_ids != tuple(
            f.match_result_id for f in self.admitted_training_refs
        ):
            raise ValueError("slice state/admitted order mismatch")
        for target in self.targets:
            if (
                target.prediction.state_hash != self.state.state_hash
                or target.prediction.training_data_hash != self.state.training_data_hash
                or target.prediction.match_id in self.state.training_match_ids
            ):
                raise ValueError("slice prediction/state binding mismatch")
        return self


class QuantIntegrityOutputContentV1(DomainModel):
    plan_ref: IntegrityArtifactRefV1
    provenance: QuantIntegrityProvenanceV1
    slices: tuple[QuantIntegritySliceOutputV1, ...] = Field(min_length=1)
    terminal_state_core: TerminalEloStateCoreV1


class QuantIntegrityOutputV1(IntegrityArtifact[QuantIntegrityOutputContentV1]):
    schema_version: Literal["PRODUCTION_QUANT_INTEGRITY_PILOT_V1"] = (
        "PRODUCTION_QUANT_INTEGRITY_PILOT_V1"
    )


class QuantIntegrityReplayV1(DomainModel):
    output_hash: Sha256Digest
    replay_output_hash: Sha256Digest
    state_hashes: tuple[Sha256Digest, ...]
    replay_state_hashes: tuple[Sha256Digest, ...]
    terminal_core_hash: Sha256Digest
    replay_terminal_core_hash: Sha256Digest
    matched: Literal[True] = True

    @model_validator(mode="after")
    def validate_replay(self) -> Self:
        if (
            self.output_hash != self.replay_output_hash
            or self.state_hashes != self.replay_state_hashes
            or self.terminal_core_hash != self.replay_terminal_core_hash
        ):
            raise ValueError("deterministic replay mismatch")
        return self


class QuantUnavailableSliceV1(DomainModel):
    slice_ref: IntegrityArtifactRefV1
    match_ids: tuple[Identifier, ...] = Field(min_length=1)


class QuantUnavailableReasonV1(DomainModel):
    reason: Literal["INSUFFICIENT_PRIOR_MATCHES"] = "INSUFFICIENT_PRIOR_MATCHES"
    count: int = Field(ge=1, strict=True)
    slices: tuple[QuantUnavailableSliceV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_count(self) -> Self:
        ids = tuple(mid for s in self.slices for mid in s.match_ids)
        _unique(ids, "unavailable matches")
        if len(ids) != self.count:
            raise ValueError("unavailable reason count mismatch")
        return self


class MarketFusionBenchmarkUnavailableV1(DomainModel):
    lane: Literal["MARKET_FUSION_BENCHMARK"] = "MARKET_FUSION_BENCHMARK"
    status: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["NO_ADMISSIBLE_POINT_IN_TIME_ODDS_ARCHIVE"] = (
        "NO_ADMISSIBLE_POINT_IN_TIME_ODDS_ARCHIVE"
    )
    blocking: Literal[False] = False


class QuantIntegrityReportContentV1(DomainModel):
    plan_ref: IntegrityArtifactRefV1
    output_ref: IntegrityArtifactRefV1
    provenance: QuantIntegrityProvenanceV1
    input_roots: QuantIntegrityInputRootsV1
    metric_definition: QuantIntegrityMetricDefinitionV1
    cohort_match_ids: tuple[Identifier, ...] = Field(min_length=1)
    available_match_ids: tuple[Identifier, ...]
    availability_count: int = Field(ge=0, strict=True)
    availability_denominator: int = Field(ge=1, strict=True)
    availability_rate: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    unavailable_reasons: tuple[QuantUnavailableReasonV1, ...]
    probability_metrics: BacktestProbabilityMetrics
    calibration_observation_count: int = Field(ge=0, strict=True)
    replay: QuantIntegrityReplayV1
    terminal_state_core_ref: IntegrityArtifactRefV1
    build_recipe: ModelBuildRecipePinV1
    implementation_code_revision: Identifier
    benchmark: MarketFusionBenchmarkUnavailableV1 = MarketFusionBenchmarkUnavailableV1()

    @model_validator(mode="after")
    def validate_denominators(self) -> Self:
        _unique(self.cohort_match_ids, "report cohort", sorted_values=True)
        _unique(self.available_match_ids, "available matches", sorted_values=True)
        _unique(
            tuple(r.reason for r in self.unavailable_reasons), "unavailable reasons"
        )
        unavailable = tuple(
            mid
            for r in self.unavailable_reasons
            for s in r.slices
            for mid in s.match_ids
        )
        _unique((*self.available_match_ids, *unavailable), "report observations")
        if set((*self.available_match_ids, *unavailable)) != set(self.cohort_match_ids):
            raise ValueError("report must account for the exact cohort")
        if not (
            self.availability_count
            == len(self.available_match_ids)
            == self.probability_metrics.sample_count
        ):
            raise ValueError("probability sample denominator mismatch")
        if self.availability_denominator != len(self.cohort_match_ids):
            raise ValueError("availability denominator mismatch")
        with localcontext() as context:
            context.prec = 50
            expected_rate = (
                Decimal(self.availability_count) / self.availability_denominator
            ).quantize(
                RATIO_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
        if self.availability_rate != expected_rate:
            raise ValueError("availability rate/denominator mismatch")
        if self.calibration_observation_count != 3 * self.availability_count:
            raise ValueError("pooled calibration denominator must be 3*N")
        if self.replay.output_hash != self.output_ref.content_hash:
            raise ValueError("report replay/output binding mismatch")
        if self.replay.terminal_core_hash != self.terminal_state_core_ref.content_hash:
            raise ValueError("report terminal core binding mismatch")
        return self


class QuantIntegrityReportV1(IntegrityArtifact[QuantIntegrityReportContentV1]):
    schema_version: Literal["PRODUCTION_QUANT_INTEGRITY_PILOT_REPORT_V1"] = (
        "PRODUCTION_QUANT_INTEGRITY_PILOT_REPORT_V1"
    )


class QuantIntegrityAttemptReservationContentV1(DomainModel):
    integrity_pilot_series_id: Identifier
    scope_hash: Sha256Digest
    sequence: int = Field(ge=1, strict=True)
    plan_ref: IntegrityArtifactRefV1
    prior_attempt_count: int = Field(ge=0, strict=True)
    prior_attempt_root: Sha256Digest
    actual_started_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        if self.sequence != self.prior_attempt_count + 1:
            raise ValueError("reserved attempt sequence must be continuous")
        return self


class QuantIntegrityAttemptReservationV1(
    IntegrityArtifact[QuantIntegrityAttemptReservationContentV1]
):
    schema_version: Literal["QUANT_INTEGRITY_ATTEMPT_RESERVATION_V1"] = (
        "QUANT_INTEGRITY_ATTEMPT_RESERVATION_V1"
    )


class QuantIntegrityFailureV1(DomainModel):
    exception_type: Identifier
    reason: Reference


class QuantIntegrityAttemptContentV1(DomainModel):
    reservation: QuantIntegrityAttemptReservationV1
    actual_completed_at_utc: UtcDateTime
    status: Literal["COMPLETED", "FAILED"]
    output_ref: IntegrityArtifactRefV1 | None = None
    report_ref: IntegrityArtifactRefV1 | None = None
    failure: QuantIntegrityFailureV1 | None = None

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if (
            self.actual_completed_at_utc
            < self.reservation.content_payload.actual_started_at_utc
        ):
            raise ValueError("attempt completion cannot precede start")
        if self.status == "COMPLETED":
            if (
                self.output_ref is None
                or self.report_ref is None
                or self.failure is not None
            ):
                raise ValueError(
                    "completed attempt requires output/report and no failure"
                )
        elif (
            self.failure is None
            or self.output_ref is not None
            or self.report_ref is not None
        ):
            raise ValueError(
                "failed attempt requires a failure and no successful artifacts"
            )
        return self


class QuantIntegrityAttemptV1(IntegrityArtifact[QuantIntegrityAttemptContentV1]):
    schema_version: Literal["PRODUCTION_QUANT_INTEGRITY_PILOT_ATTEMPT_V1"] = (
        "PRODUCTION_QUANT_INTEGRITY_PILOT_ATTEMPT_V1"
    )


def integrity_attempt_root(attempts: Iterable[QuantIntegrityAttemptV1]) -> str:
    return tagged_canonical_sha256(
        "QUANT_INTEGRITY_ATTEMPT_ROOT_V1",
        [
            {
                "sequence": a.content_payload.reservation.content_payload.sequence,
                "attempt_ref": IntegrityArtifactRefV1.of(a),
            }
            for a in attempts
        ],
    )


class QuantIntegritySummaryContentV1(DomainModel):
    integrity_pilot_series_id: Identifier
    scope_hash: Sha256Digest
    attempts: tuple[QuantIntegrityAttemptV1, ...] = Field(min_length=1)
    attempt_count: int = Field(ge=1, strict=True)
    attempt_root: Sha256Digest
    generated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_attempts(self) -> Self:
        if (
            len(self.attempts) != self.attempt_count
            or integrity_attempt_root(self.attempts) != self.attempt_root
        ):
            raise ValueError("summary attempt count/root mismatch")
        previous_time = None
        for i, attempt in enumerate(self.attempts):
            reserved = attempt.content_payload.reservation.content_payload
            if (
                reserved.sequence != i + 1
                or reserved.prior_attempt_count != i
                or reserved.prior_attempt_root
                != integrity_attempt_root(self.attempts[:i])
                or reserved.integrity_pilot_series_id != self.integrity_pilot_series_id
                or reserved.scope_hash != self.scope_hash
            ):
                raise ValueError("summary requires all continuous same-scope attempts")
            if (
                previous_time is not None
                and reserved.actual_started_at_utc < previous_time
            ):
                raise ValueError("attempts must not overlap")
            previous_time = attempt.content_payload.actual_completed_at_utc
        if previous_time > self.generated_at_utc:
            raise ValueError("summary cannot precede attempt completion")
        return self


class QuantIntegritySummaryV1(IntegrityArtifact[QuantIntegritySummaryContentV1]):
    schema_version: Literal["PRODUCTION_QUANT_INTEGRITY_PILOT_SUMMARY_V1"] = (
        "PRODUCTION_QUANT_INTEGRITY_PILOT_SUMMARY_V1"
    )


class QuantIntegrityAttestationContentV1(DomainModel):
    integrity_pilot_series_id: Identifier
    scope_hash: Sha256Digest
    plan_ref: IntegrityArtifactRefV1
    summary_ref: IntegrityArtifactRefV1
    report_ref: IntegrityArtifactRefV1
    attempt_count: int = Field(ge=1, strict=True)
    attempt_root: Sha256Digest
    input_roots: QuantIntegrityInputRootsV1
    terminal_state_core_ref: IntegrityArtifactRefV1
    build_recipe: ModelBuildRecipePinV1
    implementation_code_revision: Identifier
    provenance: QuantIntegrityProvenanceV1
    attested_at_utc: UtcDateTime
    purpose: Literal["REPLAY_INTEGRITY_AND_AVAILABILITY_ONLY_NOT_APPROVAL"] = (
        "REPLAY_INTEGRITY_AND_AVAILABILITY_ONLY_NOT_APPROVAL"
    )


class QuantIntegrityAttestationV1(
    IntegrityArtifact[QuantIntegrityAttestationContentV1]
):
    schema_version: Literal["PRODUCTION_QUANT_INTEGRITY_PILOT_ATTESTATION_V1"] = (
        "PRODUCTION_QUANT_INTEGRITY_PILOT_ATTESTATION_V1"
    )
