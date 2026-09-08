"""ADR-0008 pure snapshots, not a rights reviewer or persistence authority.

All new seals hash ``schema_version + NUL + canonical(content_payload)`` and
derive their ID from (schema_version, hash). Content models explicitly enumerate
the envelope: no self ID/hash, JSON copy, or raw review/source evidence. Existing
Checkpoint A and Elo hashes are referenced unchanged.

A repository MUST validate raw evidence, normalized rows, every referenced
record, the actual pilot plan/attempt/summary/report/attestation graph, terminal
attempt closure, complete as-of correction/revocation/successor sets, and atomic
append-only persistence. Supplying these snapshots is not proof of those facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloBaselineState,
    EloRegularTimeResult,
    EloTeamState,
    EloThreeWayBaseline,
    EloTrainingFact,
)
from football_system.domain.training_admission import (
    LocalReviewerAttestationV1,
    Reference,
    RuleText,
    Sha256Digest,
    TrainingFactAdmissionV1,
    TrainingFactBindingV1,
    tagged_canonical_sha256,
)
from football_system.domain.training_correction import TrainingCorrectionContextV2, TrainingFactVersionV2
from football_system.domain.versioned_training_history import (
    TrainingHistoryContextPinV2,
    VersionedFactRefV2,
    project_versioned_training_fact,
    select_versioned_training_facts,
    select_versioned_training_heads,
    validate_versioned_context,
    versioned_selection_root,
)

PRODUCTION_CONFIG_HASH = (
    "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
)
TRAINING_HISTORY_APPROVAL_PAYLOAD_V1 = "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"
TRAINING_HISTORY_APPROVAL_PAYLOAD_V2 = "TRAINING_HISTORY_APPROVAL_PAYLOAD_V2"
TRAINING_HISTORY_APPROVAL_V2 = "TRAINING_HISTORY_APPROVAL_V2"


def _plain(value: object) -> object:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_plain(item) for item in value)
    return value


class ReleaseSnapshotV1(DomainModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def revalidate_nested_snapshots(cls, value: object) -> object:
        # Also revalidate legacy nested models, whose model_copy bypasses validation.
        return _plain(value)


def revalidate[T: BaseModel](value: T) -> T:
    return type(value).model_validate(_plain(value))


class ReleaseArtifactRefV1(ReleaseSnapshotV1):
    schema_version: Literal["RELEASE_ARTIFACT_REF_V1"] = "RELEASE_ARTIFACT_REF_V1"
    artifact_id: Identifier
    content_hash: Sha256Digest


class SealedReleaseArtifactV1[Content: ReleaseSnapshotV1](ReleaseSnapshotV1):
    """Uniform envelope; subclasses fix the schema and explicit content type."""

    schema_version: str
    artifact_id: Identifier
    content_payload: Content
    content_hash: Sha256Digest

    @classmethod
    def freeze(cls, *, content_payload: Content, **projections: object) -> Self:
        content_payload = revalidate(content_payload)
        schema = cls.model_fields["schema_version"].default
        digest = tagged_canonical_sha256(schema, content_payload)
        return cls.model_validate(
            dict(
                schema_version=schema,
                artifact_id=stable_id(schema, digest),
                content_payload=content_payload,
                content_hash=digest,
                **projections,
            )
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        digest = tagged_canonical_sha256(self.schema_version, self.content_payload)
        if self.content_hash != digest:
            raise ValueError(f"{self.schema_version} content hash mismatch")
        if self.artifact_id != stable_id(self.schema_version, digest):
            raise ValueError(f"{self.schema_version} artifact ID mismatch")
        return self

    def reference(self) -> ReleaseArtifactRefV1:
        """An inert reference, not proof that this artifact is valid or persisted."""
        return ReleaseArtifactRefV1(
            artifact_id=self.artifact_id, content_hash=self.content_hash
        )


class FixedProductionModelV1(ReleaseSnapshotV1):
    schema_version: Literal["FIXED_PRODUCTION_MODEL_V1"] = "FIXED_PRODUCTION_MODEL_V1"
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1"] = "ELO_THREE_WAY_BASELINE_V1"
    model_version: Literal["1"] = "1"
    calibration_label: Literal["BASELINE_UNCALIBRATED"] = "BASELINE_UNCALIBRATED"
    config: EloBaselineConfig = Field(default_factory=EloBaselineConfig)
    config_hash: Literal[
        "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
    ] = PRODUCTION_CONFIG_HASH

    @model_validator(mode="after")
    def fixed_config_only(self) -> Self:
        if self.config.config_hash != PRODUCTION_CONFIG_HASH:
            raise ValueError("production requires the exact fixed Elo config")
        return self


class ProviderSeasonRefV1(ReleaseSnapshotV1):
    schema_version: Literal["PROVIDER_SEASON_REF_V1"] = "PROVIDER_SEASON_REF_V1"
    source_id: Identifier
    provider_code: Identifier
    provider_competition_id: Identifier
    provider_season_id: Identifier


class TrainingSeasonV1(ReleaseSnapshotV1):
    schema_version: Literal["TRAINING_SEASON_V1"] = "TRAINING_SEASON_V1"
    season_sequence: int = Field(ge=0, strict=True)
    season_id: Identifier
    role: Literal["WARMUP", "PILOT_TARGET", "PRODUCTION_TARGET"]
    provider_seasons: tuple[ProviderSeasonRefV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_provider_scopes(self) -> Self:
        keys = tuple(_provider_season_key(item) for item in self.provider_seasons)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("provider seasons must be unique and ordered")
        return self


class EloTrainingWindowContentV1(ReleaseSnapshotV1):
    competition_id: Identifier
    seasons: tuple[TrainingSeasonV1, ...] = Field(min_length=2)
    target_exclusion: Literal["ALL_OPERATION_TARGET_IDS"] = "ALL_OPERATION_TARGET_IDS"

    @property
    def ordered_season_ids(self) -> tuple[str, ...]:
        return tuple(season.season_id for season in self.seasons)

    @property
    def pilot_target_season_id(self) -> str:
        return self.seasons[-2].season_id

    @property
    def production_target_season_id(self) -> str:
        return self.seasons[-1].season_id

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        _sequence(tuple(item.season_sequence for item in self.seasons), "season")
        _unique(tuple(item.season_id for item in self.seasons), "season")
        roles = tuple(item.role for item in self.seasons)
        if roles != ("WARMUP",) * (len(roles) - 2) + (
            "PILOT_TARGET",
            "PRODUCTION_TARGET",
        ):
            raise ValueError(
                "window must end with separate pilot and production seasons"
            )
        return self


class EloTrainingWindowV1(SealedReleaseArtifactV1[EloTrainingWindowContentV1]):
    schema_version: Literal["ELO_TRAINING_WINDOW_V1"] = "ELO_TRAINING_WINDOW_V1"


class ProductionScopeV1(ReleaseSnapshotV1):
    schema_version: Literal["PRODUCTION_SCOPE_V1"] = "PRODUCTION_SCOPE_V1"
    competition_id: Identifier
    pilot_target_season_id: Identifier
    production_target_season_id: Identifier
    training_window_hash: Sha256Digest
    model: FixedProductionModelV1 = Field(default_factory=FixedProductionModelV1)

    @model_validator(mode="after")
    def separate_target_seasons(self) -> Self:
        if self.pilot_target_season_id == self.production_target_season_id:
            raise ValueError("pilot and production target seasons must differ")
        return self


class ApprovedTrainingFactContentV1(ReleaseSnapshotV1):
    fact_sequence: int = Field(ge=0, strict=True)
    season_sequence: int = Field(ge=0, strict=True)
    integrity_pilot_scope_id: Identifier
    training_fact_admission: ReleaseArtifactRefV1
    source_rights_admission: ReleaseArtifactRefV1
    terms_sha256: Sha256Digest
    binding: TrainingFactBindingV1
    elo_fact: EloTrainingFact
    effective_source_available_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        expected = EloTrainingFact.from_result(
            sequence=self.fact_sequence, result=elo_result_from_binding(self.binding)
        )
        if self.elo_fact != expected:
            raise ValueError(
                "approved fact must exactly project its normalized binding"
            )
        if self.effective_source_available_at_utc != effective_source_time(
            self.binding
        ):
            raise ValueError("effective source availability mismatch")
        return self


class ApprovedTrainingFactV1(SealedReleaseArtifactV1[ApprovedTrainingFactContentV1]):
    schema_version: Literal["APPROVED_TRAINING_FACT_V1"] = "APPROVED_TRAINING_FACT_V1"


class HistorySourceSummaryV1(ReleaseSnapshotV1):
    schema_version: Literal["HISTORY_SOURCE_SUMMARY_V1"] = "HISTORY_SOURCE_SUMMARY_V1"
    source_sequence: int = Field(ge=0, strict=True)
    source_id: Identifier
    provider_code: Identifier
    source_rights_admission: ReleaseArtifactRefV1
    terms_sha256: Sha256Digest
    fact_count: int = Field(ge=1, strict=True)
    facts_hash: Sha256Digest
    fixture_source_count: int = Field(ge=1, strict=True)
    fixture_sources_hash: Sha256Digest
    mapping_source_count: int = Field(ge=1, strict=True)
    mapping_sources_hash: Sha256Digest
    result_source_count: int = Field(ge=1, strict=True)
    result_sources_hash: Sha256Digest


class HistorySeasonSummaryV1(ReleaseSnapshotV1):
    schema_version: Literal["HISTORY_SEASON_SUMMARY_V1"] = "HISTORY_SEASON_SUMMARY_V1"
    season: TrainingSeasonV1
    fact_count: int = Field(ge=0, strict=True)
    facts_hash: Sha256Digest


class TrainingHistoryGraphV1(ReleaseSnapshotV1):
    """Explicit full-binding projection, prepared before the pilot evidence seal.

    V1 admits the exact union of the supplied admissions, never a silent subset.
    The repository must validate provider season evidence even for zero-fact
    production seasons and resolve admission rows against their actual archives.
    """

    schema_version: Literal["TRAINING_HISTORY_GRAPH_V1"] = "TRAINING_HISTORY_GRAPH_V1"
    integrity_pilot_scope_id: Identifier
    scope: ProductionScopeV1
    training_window: EloTrainingWindowV1
    admissions: tuple[TrainingFactAdmissionV1, ...] = Field(min_length=1)
    facts: tuple[ApprovedTrainingFactV1, ...] = Field(min_length=1)
    source_summaries: tuple[HistorySourceSummaryV1, ...] = Field(min_length=1)
    season_summaries: tuple[HistorySeasonSummaryV1, ...] = Field(min_length=2)
    source_count: int = Field(ge=1, strict=True)
    source_root: Sha256Digest
    season_count: int = Field(ge=2, strict=True)
    season_root: Sha256Digest
    fact_count: int = Field(ge=1, strict=True)
    approved_facts_hash: Sha256Digest
    training_data_hash: Sha256Digest
    max_effective_source_available_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        window = self.training_window.content_payload
        expected_scope = ProductionScopeV1(
            competition_id=window.competition_id,
            pilot_target_season_id=window.seasons[-2].season_id,
            production_target_season_id=window.seasons[-1].season_id,
            training_window_hash=self.training_window.content_hash,
        )
        if self.scope != expected_scope:
            raise ValueError("history scope/window mismatch")
        admission_ids = tuple(
            item.training_fact_admission_id for item in self.admissions
        )
        if admission_ids != tuple(sorted(set(admission_ids))):
            raise ValueError("admissions must be unique and ordered by ID")
        expected = prepare_approved_facts(
            self.admissions, self.training_window, self.integrity_pilot_scope_id
        )
        if self.facts != expected:
            raise ValueError("history facts must cover exact ordered admitted bindings")
        sources, seasons = history_summaries(self.facts, self.training_window)
        if self.source_summaries != sources or self.season_summaries != seasons:
            raise ValueError("history source/season summaries mismatch")
        if (
            self.source_count != len(sources)
            or self.season_count != len(seasons)
            or self.fact_count != len(self.facts)
            or self.source_root
            != tagged_canonical_sha256("HISTORY_SOURCES_ROOT_V1", sources)
            or self.season_root
            != tagged_canonical_sha256("HISTORY_SEASONS_ROOT_V1", seasons)
            or self.approved_facts_hash != approved_facts_root(self.facts)
        ):
            raise ValueError("history counts/roots mismatch")
        maximum = max(
            item.content_payload.effective_source_available_at_utc
            for item in self.facts
        )
        if self.max_effective_source_available_at_utc != maximum:
            raise ValueError("history source maximum mismatch")
        state = replay_exact_facts(
            tuple(item.content_payload.elo_fact for item in self.facts),
            maximum,
            self.scope.production_target_season_id,
        )
        if self.training_data_hash != state.training_data_hash:
            raise ValueError("history training data hash mismatch")
        return self


class ApprovedTrainingFactContentV2(ReleaseSnapshotV1):
    fact_sequence: int = Field(ge=0, strict=True)
    season_sequence: int = Field(ge=0, strict=True)
    integrity_pilot_scope_id: Identifier
    training_fact_admission: ReleaseArtifactRefV1
    source_rights_admission: ReleaseArtifactRefV1
    terms_sha256: Sha256Digest
    version: TrainingFactVersionV2
    elo_fact: EloTrainingFact
    effective_source_available_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if (
            self.elo_fact
            != EloTrainingFact.from_result(
                sequence=self.fact_sequence,
                result=project_versioned_training_fact(self.version),
            )
            or self.effective_source_available_at_utc
            != self.version.snapshot.effective_source_available_at_utc
            or (
                self.training_fact_admission.artifact_id,
                self.training_fact_admission.content_hash,
            )
            != (
                self.version.base_admission.artifact_id,
                self.version.base_admission.content_hash,
            )
        ):
            raise ValueError(
                "approved version must exactly project its typed source version"
            )
        return self


class ApprovedTrainingFactV2(SealedReleaseArtifactV1[ApprovedTrainingFactContentV2]):
    schema_version: Literal["APPROVED_TRAINING_FACT_V2"] = "APPROVED_TRAINING_FACT_V2"


class TrainingHistoryGraphV2(TrainingHistoryGraphV1):
    """Full predecessor context and one terminal head per stream, not a V1 union."""

    schema_version: Literal["TRAINING_HISTORY_GRAPH_V2"] = "TRAINING_HISTORY_GRAPH_V2"
    context_pin: TrainingHistoryContextPinV2
    correction_context: TrainingCorrectionContextV2
    selection_cutoff_at_utc: UtcDateTime
    exclude_match_ids: tuple[Identifier, ...]
    selected_heads: tuple[VersionedFactRefV2, ...]
    selected_versions_root: Sha256Digest
    facts: tuple[ApprovedTrainingFactV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        context = validate_versioned_context(self.correction_context)
        if self.context_pin != TrainingHistoryContextPinV2.of(context):
            raise ValueError("history correction context pin mismatch")
        pins = tuple(
            (a.training_fact_admission_id, a.admission_hash) for a in self.admissions
        )
        if pins != tuple(
            (r.artifact_id, r.content_hash) for r in self.context_pin.base_admissions
        ):
            raise ValueError("history requires exact base admission context")
        bindings = {
            (a.training_fact_admission_id, f.training_fact_binding_id): f
            for a in self.admissions
            for f in a.facts
        }
        bases = [v for v in context.versions if not v.revision_sequence]
        if set(bindings) != {
            (v.base_admission.artifact_id, v.base_binding.artifact_id) for v in bases
        }:
            raise ValueError("context must cover every original admission binding")
        for v in bases:
            b = bindings[v.base_admission.artifact_id, v.base_binding.artifact_id]
            if (
                v.base_binding.content_hash,
                v.snapshot.identity,
                v.normalized_result,
            ) != (
                b.fact_hash,
                b.content_payload.canonical_identity,
                b.content_payload.normalized_result,
            ):
                raise ValueError("context base binding/identity/result mismatch")
        window = self.training_window.content_payload
        if self.scope != ProductionScopeV1(
            competition_id=window.competition_id,
            pilot_target_season_id=window.pilot_target_season_id,
            production_target_season_id=window.production_target_season_id,
            training_window_hash=self.training_window.content_hash,
        ):
            raise ValueError("history scope/window mismatch")
        if self.exclude_match_ids != tuple(sorted(set(self.exclude_match_ids))):
            raise ValueError("history exclusions must be unique and ordered")
        # Even predecessor metadata must stay inside the explicitly reviewed scope.
        seasons = {s.season_id: s for s in window.seasons}
        for v in context.versions:
            s = v.snapshot
            season = seasons.get(s.identity.season)
            provider = ProviderSeasonRefV1(
                source_id=s.stream.source_id,
                provider_code=s.stream.provider_code,
                provider_competition_id=s.provider_competition_id,
                provider_season_id=s.provider_season_id,
            )
            if (
                season is None
                or provider not in season.provider_seasons
                or s.identity.internal_competition_id != window.competition_id
            ):
                raise ValueError(
                    "version outside explicit competition/provider season window"
                )
        heads = select_versioned_training_heads(
            context,
            source_cutoffs={
                v.snapshot.stream.source_id: self.selection_cutoff_at_utc
                for v in context.versions
            },
            strict_cutoff=False,
        )
        refs = tuple(VersionedFactRefV2.of(v) for v in heads)
        expected = prepare_versioned_approved_facts(
            self.admissions,
            context,
            self.training_window,
            self.integrity_pilot_scope_id,
            self.selection_cutoff_at_utc,
            self.exclude_match_ids,
        )
        if (
            self.facts != expected
            or self.selected_heads != refs
            or self.selected_versions_root != versioned_selection_root(refs)
        ):
            raise ValueError("history must bind exact whole-version terminal selection")
        sources, summaries = versioned_history_summaries(
            self.facts, self.training_window
        )
        if (
            self.source_summaries != sources
            or self.season_summaries != summaries
            or self.source_count != len(sources)
            or self.season_count != len(summaries)
            or self.fact_count != len(self.facts)
            or self.source_root
            != tagged_canonical_sha256(
                "HISTORY_SOURCES_ROOT_V2",
                {"context": self.context_pin, "sources": sources},
            )
            or self.season_root
            != tagged_canonical_sha256("HISTORY_SEASONS_ROOT_V2", summaries)
            or self.approved_facts_hash != approved_versioned_facts_root(self.facts)
        ):
            raise ValueError("versioned history counts/roots mismatch")
        maximum = max(
            f.content_payload.effective_source_available_at_utc for f in self.facts
        )
        state = replay_exact_facts(
            tuple(f.content_payload.elo_fact for f in self.facts),
            self.selection_cutoff_at_utc,
            window.production_target_season_id,
        )
        if (
            self.max_effective_source_available_at_utc != maximum
            or self.training_data_hash != state.training_data_hash
        ):
            raise ValueError("versioned history math/source maximum mismatch")
        return self


TrainingHistoryGraph = Annotated[
    TrainingHistoryGraphV1 | TrainingHistoryGraphV2,
    Field(discriminator="schema_version"),
]
ApprovedTrainingFact = Annotated[
    ApprovedTrainingFactV1 | ApprovedTrainingFactV2,
    Field(discriminator="schema_version"),
]


class TechnicalEvidenceRefsV1(ReleaseSnapshotV1):
    """Adapter contract, deliberately independent of quant_integrity.py names.

    References are claims, not pilot verification. The repository must recompute
    continuous attempts through the supplied actual time and validate all actual
    pilot artifacts, including scope, roots, recipe, revision and terminal state.
    """

    schema_version: Literal["TECHNICAL_EVIDENCE_REFS_V1"] = "TECHNICAL_EVIDENCE_REFS_V1"
    integrity_pilot_scope_id: Identifier
    integrity_pilot_series_id: Identifier
    scope: ProductionScopeV1
    plan: ReleaseArtifactRefV1
    summary: ReleaseArtifactRefV1
    attestation: ReleaseArtifactRefV1
    report: ReleaseArtifactRefV1
    attempt_count: int = Field(ge=1, strict=True)
    attempt_root: Sha256Digest
    source_root: Sha256Digest
    season_root: Sha256Digest
    approved_facts_hash: Sha256Digest
    training_data_hash: Sha256Digest
    terminal_state_core_hash: Sha256Digest
    build_recipe: ReleaseArtifactRefV1
    code_revision: Identifier
    plan_sealed_at_utc: UtcDateTime
    actual_started_at_utc: UtcDateTime
    actual_completed_at_utc: UtcDateTime
    attestation_persisted_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_actual_times(self) -> Self:
        if not (
            self.plan_sealed_at_utc
            <= self.actual_started_at_utc
            <= self.actual_completed_at_utc
            <= self.attestation_persisted_at_utc
        ):
            raise ValueError("technical evidence actual timeline mismatch")
        return self


class TrainingHistoryManifestContentV1(ReleaseSnapshotV1):
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    source_classification: Literal["REAL_SOURCE_DATA"] = "REAL_SOURCE_DATA"
    retrospective: Literal[True] = True
    history: TrainingHistoryGraph
    technical_evidence: TechnicalEvidenceRefsV1
    created_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        graph, evidence = self.history, self.technical_evidence
        if (
            evidence.scope != graph.scope
            or evidence.integrity_pilot_scope_id != graph.integrity_pilot_scope_id
            or evidence.source_root != graph.source_root
            or evidence.season_root != graph.season_root
            or evidence.approved_facts_hash != graph.approved_facts_hash
            or evidence.training_data_hash != graph.training_data_hash
        ):
            raise ValueError("manifest technical evidence does not match history")
        if not (
            evidence.attestation_persisted_at_utc
            <= self.created_at_utc
            <= self.persisted_at_utc
        ):
            raise ValueError("manifest actual timeline mismatch")
        if any(
            item.content_payload.persisted_at_utc > evidence.plan_sealed_at_utc
            for item in graph.admissions
        ):
            raise ValueError("fact admission must precede pilot plan sealing")
        if isinstance(graph, TrainingHistoryGraphV2) and graph.correction_context.actual_at_utc > evidence.plan_sealed_at_utc:
            raise ValueError("correction context must precede pilot plan sealing")
        return self


class TrainingHistoryManifestV1(
    SealedReleaseArtifactV1[TrainingHistoryManifestContentV1]
):
    schema_version: Literal["TRAINING_HISTORY_MANIFEST_V1"] = (
        "TRAINING_HISTORY_MANIFEST_V1"
    )


class ProductionGrantKind(StrEnum):
    PRODUCTION_MODEL_TRAINING = "PRODUCTION_MODEL_TRAINING"
    PRODUCTION_MODEL_INFERENCE = "PRODUCTION_MODEL_INFERENCE"
    DERIVED_MODEL_STATE_RETENTION = "DERIVED_MODEL_STATE_RETENTION"
    AUDIT_HASH_RETENTION = "AUDIT_HASH_RETENTION"


RETENTION_GRANTS = frozenset(
    {
        ProductionGrantKind.DERIVED_MODEL_STATE_RETENTION,
        ProductionGrantKind.AUDIT_HASH_RETENTION,
    }
)


class RetentionHorizonV1(ReleaseSnapshotV1):
    schema_version: Literal["RETENTION_HORIZON_V1"] = "RETENTION_HORIZON_V1"
    indefinite: bool = Field(strict=True)
    retain_until_at_utc: UtcDateTime | None

    @model_validator(mode="after")
    def explicit_horizon(self) -> Self:
        if self.indefinite != (self.retain_until_at_utc is None):
            raise ValueError(
                "retention requires bounded retain_until or explicit indefinite"
            )
        return self


class ProductionGrantV1(ReleaseSnapshotV1):
    schema_version: Literal["PRODUCTION_GRANT_V1"] = "PRODUCTION_GRANT_V1"
    grant: ProductionGrantKind
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime | None
    retention_rule: RuleText | None
    retention: RetentionHorizonV1 | None

    @model_validator(mode="after")
    def validate_grant(self) -> Self:
        if (
            self.expires_at_utc is not None
            and self.expires_at_utc <= self.effective_at_utc
        ):
            raise ValueError("grant expiry must follow effective time")
        if self.grant in RETENTION_GRANTS:
            if self.retention is None or self.retention_rule is None:
                raise ValueError("retention grant requires rule and explicit horizon")
            until = self.retention.retain_until_at_utc
            if until is None:
                if self.expires_at_utc is not None:
                    raise ValueError(
                        "indefinite retention cannot have finite grant expiry"
                    )
            elif until < self.effective_at_utc or (
                self.expires_at_utc is not None and until > self.expires_at_utc
            ):
                raise ValueError(
                    "retain_until must lie within retention grant interval"
                )
        elif self.retention is not None or self.retention_rule is not None:
            raise ValueError("training/inference grants cannot masquerade as retention")
        return self


class SourceRightsRefV1(ReleaseSnapshotV1):
    schema_version: Literal["SOURCE_RIGHTS_REF_V1"] = "SOURCE_RIGHTS_REF_V1"
    admission: ReleaseArtifactRefV1
    terms_sha256: Sha256Digest
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class TrainingHistoryApprovalPayloadV1(ReleaseSnapshotV1):
    payload_version: Literal["TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"] = (
        TRAINING_HISTORY_APPROVAL_PAYLOAD_V1
    )
    training_use_class: Literal["APPROVED_TRAINING_HISTORY"] = (
        "APPROVED_TRAINING_HISTORY"
    )
    manifest: ReleaseArtifactRefV1
    scope: ProductionScopeV1
    source_rights: tuple[SourceRightsRefV1, ...] = Field(min_length=1)
    technical_evidence: TechnicalEvidenceRefsV1
    build_recipe: ReleaseArtifactRefV1
    code_revision: Identifier
    approver: Identifier
    authority_reference: Reference
    authority_sha256: Sha256Digest
    grants: tuple[ProductionGrantV1, ...] = Field(min_length=4, max_length=4)
    retention_compatibility: Literal["APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE"]
    approved_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime
    supersedes_approval: ReleaseArtifactRefV1 | None
    supersession_effective_at_utc: UtcDateTime | None
    superseded_grants: tuple[ProductionGrantKind, ...]

    @property
    def approval_payload_hash(self) -> str:
        return tagged_canonical_sha256(
            TRAINING_HISTORY_APPROVAL_PAYLOAD_V1, revalidate(self)
        )

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        kinds = tuple(item.grant for item in self.grants)
        if kinds != tuple(sorted(ProductionGrantKind)):
            raise ValueError(
                "all four independent production grants must be unique and sorted"
            )
        if (
            self.scope != self.technical_evidence.scope
            or self.build_recipe != self.technical_evidence.build_recipe
            or self.code_revision != self.technical_evidence.code_revision
        ):
            raise ValueError(
                "approval scope/recipe/revision differs from pilot evidence"
            )
        ids = tuple(item.admission.artifact_id for item in self.source_rights)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("approval source rights must be unique and ordered")
        for item in self.source_rights:
            if item.source_ids != tuple(sorted(set(item.source_ids))):
                raise ValueError("approval source IDs must be unique and ordered")
        if not (
            self.technical_evidence.attestation_persisted_at_utc
            <= self.approved_at_utc
            <= self.persisted_at_utc
        ):
            raise ValueError("approval actual timeline mismatch")
        if self.supersedes_approval is None:
            if self.supersession_effective_at_utc is not None or self.superseded_grants:
                raise ValueError("successor metadata requires a predecessor")
        elif (
            self.supersession_effective_at_utc is None
            or self.supersession_effective_at_utc < self.persisted_at_utc
            or not self.superseded_grants
        ):
            raise ValueError("successor must be persisted before becoming effective")
        _ordered_grants(self.superseded_grants)
        return self


class TrainingHistoryApprovalContentV1(ReleaseSnapshotV1):
    approval_payload: TrainingHistoryApprovalPayloadV1
    approval_payload_hash: Sha256Digest
    reviewer_attestation: LocalReviewerAttestationV1

    @model_validator(mode="after")
    def validate_attestation(self) -> Self:
        payload = self.approval_payload
        review = self.reviewer_attestation.content_payload
        if self.approval_payload_hash != payload.approval_payload_hash:
            raise ValueError("approval payload hash mismatch")
        if (
            review.attested_schema_version != TRAINING_HISTORY_APPROVAL_PAYLOAD_V1
            or review.attested_payload_hash != self.approval_payload_hash
            or review.authorized_reviewer != payload.approver
            or review.reviewer_authority_reference != payload.authority_reference
            or review.authority_sha256 != payload.authority_sha256
        ):
            raise ValueError(
                "reviewer attestation does not bind exact approval/authority"
            )
        if not (
            payload.technical_evidence.attestation_persisted_at_utc
            <= review.reviewed_at_utc
            <= payload.approved_at_utc
        ):
            raise ValueError("approval review timeline mismatch")
        return self


class TrainingHistoryApprovalV1(
    SealedReleaseArtifactV1[TrainingHistoryApprovalContentV1]
):
    """Reconstitutes supplied reviewer-attested content; never self-approves."""

    schema_version: Literal["TRAINING_HISTORY_APPROVAL_V1"] = (
        "TRAINING_HISTORY_APPROVAL_V1"
    )

    @model_validator(mode="after")
    def no_self_successor(self) -> Self:
        previous = self.content_payload.approval_payload.supersedes_approval
        if previous is not None and previous.artifact_id == self.artifact_id:
            raise ValueError("approval cannot supersede itself")
        return self

    @property
    def subject(self) -> TrainingHistoryApprovalPayloadV1:
        return self.content_payload.approval_payload

    @property
    def recorded_at_utc(self) -> datetime:
        return self.subject.approved_at_utc

    @property
    def persisted_at_utc(self) -> datetime:
        return self.subject.persisted_at_utc

    def matches_technical_evidence(self, evidence: TechnicalEvidenceRefsV1) -> bool:
        return self.subject.technical_evidence == evidence


def approval_technical_evidence_ref(
    evidence: TechnicalEvidenceRefsV1,
) -> ReleaseArtifactRefV1:
    """Pin the entire existing pilot graph, without copying observations into review."""
    return ReleaseArtifactRefV1(
        artifact_id=evidence.attestation.artifact_id,
        content_hash=tagged_canonical_sha256(evidence.schema_version, evidence),
    )


class TrainingHistoryApprovalPayloadV2(ReleaseSnapshotV1):
    """Human review subject. Interval/supersession times are policy, not observations.

    Manifest and pilot hashes bind their exact existing observations transitively.
    No recording timestamp, request identity, raw source or review bytes belong here.
    """

    payload_version: Literal["TRAINING_HISTORY_APPROVAL_PAYLOAD_V2"] = (
        TRAINING_HISTORY_APPROVAL_PAYLOAD_V2
    )
    training_use_class: Literal["APPROVED_TRAINING_HISTORY"] = (
        "APPROVED_TRAINING_HISTORY"
    )
    manifest: ReleaseArtifactRefV1
    scope: ProductionScopeV1
    source_rights: tuple[SourceRightsRefV1, ...] = Field(min_length=1)
    technical_evidence: ReleaseArtifactRefV1
    build_recipe: ReleaseArtifactRefV1
    code_revision: Identifier
    approver: Identifier
    authority_reference: Reference
    authority_sha256: Sha256Digest
    grants: tuple[ProductionGrantV1, ...] = Field(min_length=4, max_length=4)
    retention_compatibility: Literal["APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE"]
    supersedes_approval: ReleaseArtifactRefV1 | None
    supersession_effective_at_utc: UtcDateTime | None
    superseded_grants: tuple[ProductionGrantKind, ...]

    @property
    def approval_payload_hash(self) -> str:
        return tagged_canonical_sha256(self.payload_version, revalidate(self))

    def assert_active_intervals(self, at: datetime) -> None:
        for grant in self.grants:
            if grant.effective_at_utc > at or (
                grant.expires_at_utc is not None and grant.expires_at_utc <= at
            ):
                raise ValueError(f"{grant.grant} grant is not active at recording")
            if grant.retention is not None:
                _horizon_not_past(grant.retention, at)

    @model_validator(mode="after")
    def validate_subject(self) -> Self:
        if tuple(item.grant for item in self.grants) != tuple(
            sorted(ProductionGrantKind)
        ):
            raise ValueError(
                "all four independent production grants must be unique and sorted"
            )
        ids = tuple(item.admission.artifact_id for item in self.source_rights)
        if ids != tuple(sorted(set(ids))) or any(
            item.source_ids != tuple(sorted(set(item.source_ids)))
            for item in self.source_rights
        ):
            raise ValueError(
                "approval source rights and IDs must be unique and ordered"
            )
        if self.supersedes_approval is None:
            if self.supersession_effective_at_utc is not None or self.superseded_grants:
                raise ValueError("successor metadata requires a predecessor")
        elif self.supersession_effective_at_utc is None or not self.superseded_grants:
            raise ValueError("successor requires explicit effective policy and grants")
        _ordered_grants(self.superseded_grants)
        return self


def training_approval_request_hash(
    request_key: str,
    operator_id: str,
    approval_payload: TrainingHistoryApprovalPayloadV2,
    reviewer_attestation: LocalReviewerAttestationV1,
) -> str:
    # Persistence request V1 hashes JSON wire scalars, not typed datetime/Decimals.
    return tagged_canonical_sha256(
        "PRODUCTION_PERSISTENCE_REQUEST_V1",
        {
            "operation": "record_approval",
            "request_key": request_key,
            "operator_id": operator_id,
            "payload": {
                "approval_payload": approval_payload.model_dump(mode="json"),
                "reviewer_attestation": reviewer_attestation.model_dump(mode="json"),
            },
        },
    )


class TrainingHistoryApprovalContentV2(ReleaseSnapshotV1):
    approval_payload: TrainingHistoryApprovalPayloadV2
    approval_payload_hash: Sha256Digest
    reviewer_attestation: LocalReviewerAttestationV1
    operator_id: Identifier
    request_key: Identifier
    request_sha256: Sha256Digest
    recorded_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_recording(self) -> Self:
        payload, review = (
            self.approval_payload,
            self.reviewer_attestation.content_payload,
        )
        if self.approval_payload_hash != payload.approval_payload_hash:
            raise ValueError("approval payload hash mismatch")
        if (
            review.attested_schema_version != TRAINING_HISTORY_APPROVAL_PAYLOAD_V2
            or review.attested_payload_hash != self.approval_payload_hash
            or review.authorized_reviewer != payload.approver
            or review.reviewer_authority_reference != payload.authority_reference
            or review.authority_sha256 != payload.authority_sha256
        ):
            raise ValueError(
                "reviewer attestation does not bind exact V2 subject/authority"
            )
        if self.request_sha256 != training_approval_request_hash(
            self.request_key, self.operator_id, payload, self.reviewer_attestation
        ):
            raise ValueError("approval recording request hash mismatch")
        if not review.reviewed_at_utc <= self.recorded_at_utc <= self.persisted_at_utc:
            raise ValueError("approval actual recording timeline mismatch")
        payload.assert_active_intervals(self.recorded_at_utc)
        payload.assert_active_intervals(self.persisted_at_utc)
        if (
            payload.supersession_effective_at_utc is not None
            and payload.supersession_effective_at_utc < self.persisted_at_utc
        ):
            raise ValueError("successor must be persisted before becoming effective")
        return self


class TrainingHistoryApprovalV2(
    SealedReleaseArtifactV1[TrainingHistoryApprovalContentV2]
):
    schema_version: Literal["TRAINING_HISTORY_APPROVAL_V2"] = (
        TRAINING_HISTORY_APPROVAL_V2
    )

    @property
    def subject(self) -> TrainingHistoryApprovalPayloadV2:
        return self.content_payload.approval_payload

    @property
    def recorded_at_utc(self) -> datetime:
        return self.content_payload.recorded_at_utc

    @property
    def persisted_at_utc(self) -> datetime:
        return self.content_payload.persisted_at_utc

    def matches_technical_evidence(self, evidence: TechnicalEvidenceRefsV1) -> bool:
        return (
            self.subject.technical_evidence == approval_technical_evidence_ref(evidence)
            and self.subject.scope == evidence.scope
            and self.subject.build_recipe == evidence.build_recipe
            and self.subject.code_revision == evidence.code_revision
        )

    @model_validator(mode="after")
    def no_self_successor(self) -> Self:
        previous = self.subject.supersedes_approval
        if previous is not None and previous.artifact_id == self.artifact_id:
            raise ValueError("approval cannot supersede itself")
        return self


TrainingHistoryApproval = Annotated[
    TrainingHistoryApprovalV1 | TrainingHistoryApprovalV2,
    Field(discriminator="schema_version"),
]
TRAINING_HISTORY_APPROVAL_ADAPTER = TypeAdapter(TrainingHistoryApproval)


def parse_training_history_approval(value: object) -> TrainingHistoryApproval:
    return TRAINING_HISTORY_APPROVAL_ADAPTER.validate_python(_plain(value))


class GrantRevocationContentV1(ReleaseSnapshotV1):
    approval: ReleaseArtifactRefV1
    release: ReleaseArtifactRefV1 | None
    affected_grants: tuple[ProductionGrantKind, ...] = Field(min_length=1)
    actor: Identifier
    authority_reference: Reference
    authority_sha256: Sha256Digest
    reason: RuleText
    recorded_at_utc: UtcDateTime
    effective_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_grants(self) -> Self:
        _ordered_grants(self.affected_grants)
        return self


class GrantRevocationV1(SealedReleaseArtifactV1[GrantRevocationContentV1]):
    schema_version: Literal["TRAINING_HISTORY_REVOCATION_V1"] = (
        "TRAINING_HISTORY_REVOCATION_V1"
    )


class ApprovalSuccessorContentV1(ReleaseSnapshotV1):
    predecessor: ReleaseArtifactRefV1
    successor: ReleaseArtifactRefV1
    predecessor_scope: ProductionScopeV1
    successor_scope: ProductionScopeV1
    affected_grants: tuple[ProductionGrantKind, ...] = Field(min_length=1)
    successor_persisted_at_utc: UtcDateTime
    effective_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_successor(self) -> Self:
        if self.predecessor.artifact_id == self.successor.artifact_id:
            raise ValueError("approval cannot succeed itself")
        if self.predecessor_scope != self.successor_scope:
            raise ValueError(
                "successor must preserve exact competition/model/window scope"
            )
        if self.successor_persisted_at_utc > self.effective_at_utc:
            raise ValueError("successor cannot be backdated before persistence")
        _ordered_grants(self.affected_grants)
        return self


class ApprovalSuccessorV1(SealedReleaseArtifactV1[ApprovalSuccessorContentV1]):
    schema_version: Literal["TRAINING_APPROVAL_SUCCESSOR_V1"] = (
        "TRAINING_APPROVAL_SUCCESSOR_V1"
    )


class SourceCorrectionContentV1(ReleaseSnapshotV1):
    """Version references: fixture record, tagged mapping snapshot, membership,
    or result admission (for both STATUS and RESULT). Registration is the actual
    local time, not a source-time ingestion projection.
    """

    source_id: Identifier
    provider_code: Identifier
    match_id: Identifier
    component: Literal["FIXTURE", "MAPPING", "SEASON", "STATUS", "RESULT"]
    predecessor: ReleaseArtifactRefV1
    successor: ReleaseArtifactRefV1
    predecessor_source_available_at_utc: UtcDateTime
    source_available_at_utc: UtcDateTime
    local_imported_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime

    @model_validator(mode="after")
    def preserve_source_and_actual_clocks(self) -> Self:
        if self.predecessor.artifact_id == self.successor.artifact_id:
            raise ValueError("correction must reference a distinct successor")
        if not (
            self.predecessor_source_available_at_utc
            <= self.source_available_at_utc
            <= self.local_imported_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("correction source/actual timeline mismatch")
        return self


class SourceCorrectionV1(SealedReleaseArtifactV1[SourceCorrectionContentV1]):
    schema_version: Literal["TRAINING_SOURCE_CORRECTION_V1"] = (
        "TRAINING_SOURCE_CORRECTION_V1"
    )


class CurrentAuthorizationInputsV1(ReleaseSnapshotV1):
    """Complete repository view at an actual operation boundary.

    Controlled V2 events use SourceCorrectionV2.as_v1(); pinned components use
    the identical CorrectionRefV2.as_v1() projection, never raw ROOT pointers.
    """

    schema_version: Literal["CURRENT_AUTHORIZATION_INPUTS_V1"] = (
        "CURRENT_AUTHORIZATION_INPUTS_V1"
    )
    actual_at_utc: UtcDateTime
    technical_evidence: TechnicalEvidenceRefsV1
    corrections: tuple[SourceCorrectionV1, ...]
    revocations: tuple[GrantRevocationV1, ...]
    successors: tuple[ApprovalSuccessorV1, ...]

    @model_validator(mode="after")
    def no_forks_or_cycles(self) -> Self:
        for events in (self.corrections, self.revocations, self.successors):
            _unique(tuple(item.artifact_id for item in events), "authorization event")
        edges: dict[str, str] = {}
        for item in self.successors:
            content = item.content_payload
            key = content.predecessor.artifact_id
            if key in edges:
                raise ValueError("approval successor fork is forbidden")
            edges[key] = content.successor.artifact_id
        for start in edges:
            seen: set[str] = set()
            cursor = start
            while cursor in edges:
                if cursor in seen:
                    raise ValueError("approval successor cycle is forbidden")
                seen.add(cursor)
                cursor = edges[cursor]
        correction_keys = tuple(
            (
                item.content_payload.source_id,
                item.content_payload.component,
                item.content_payload.predecessor.artifact_id,
            )
            for item in self.corrections
        )
        _unique(correction_keys, "correction predecessor (no forks)")
        return self


def assert_authorization_progression(
    start: CurrentAuthorizationInputsV1,
    completion: CurrentAuthorizationInputsV1,
) -> None:
    """Known immutable observations cannot disappear between operation boundaries."""
    if start.actual_at_utc > completion.actual_at_utc:
        raise ValueError("actual operation completion precedes start")
    if start.technical_evidence != completion.technical_evidence:
        raise ValueError("technical evidence changed during operation")
    for name in ("corrections", "revocations", "successors"):
        later = {item.artifact_id: item for item in getattr(completion, name)}
        if any(later.get(item.artifact_id) != item for item in getattr(start, name)):
            raise ValueError(
                "known authorization observations cannot disappear or change"
            )


class BuildAuthorizationContentV1(ReleaseSnapshotV1):
    approval: ReleaseArtifactRefV1
    manifest: ReleaseArtifactRefV1
    phase: Literal["BUILD_START", "BUILD_COMPLETION"]
    current: CurrentAuthorizationInputsV1
    state_retention_horizon: RetentionHorizonV1
    audit_retention_horizon: RetentionHorizonV1


class BuildAuthorizationV1(SealedReleaseArtifactV1[BuildAuthorizationContentV1]):
    schema_version: Literal["BUILD_AUTHORIZATION_V1"] = "BUILD_AUTHORIZATION_V1"


class ReleasedStateCoreContentV1(ReleaseSnapshotV1):
    training_cutoff_at_utc: UtcDateTime
    model: FixedProductionModelV1
    production_target_season_id: Identifier
    teams: tuple[EloTeamState, ...] = Field(min_length=2)
    training_match_ids: tuple[Identifier, ...] = Field(min_length=1)
    training_result_ids: tuple[Identifier, ...] = Field(min_length=1)
    training_facts: tuple[EloTrainingFact, ...] = Field(min_length=1)
    training_data_hash: Sha256Digest
    approved_facts_hash: Sha256Digest

    @model_validator(mode="after")
    def verify_exact_math(self) -> Self:
        state = replay_exact_facts(
            self.training_facts,
            self.training_cutoff_at_utc,
            self.production_target_season_id,
        )
        if (
            self.teams != state.teams
            or self.training_match_ids != state.training_match_ids
            or self.training_result_ids != state.training_result_ids
            or self.training_data_hash != state.training_data_hash
        ):
            raise ValueError(
                "released core ratings/counts/lineage differ from exact Elo replay"
            )
        return self

    @classmethod
    def from_state(
        cls,
        *,
        state: EloBaselineState,
        training_cutoff_at_utc: datetime,
        approved_facts_hash: str,
    ) -> Self:
        state = revalidate(state)
        if state.config_hash != PRODUCTION_CONFIG_HASH or state.season_id is None:
            raise ValueError(
                "core projection requires fixed production model and season"
            )
        # Explicit projection, not a mutable state with its runtime cutoff replaced.
        return cls(
            training_cutoff_at_utc=training_cutoff_at_utc,
            model=FixedProductionModelV1(),
            production_target_season_id=state.season_id,
            teams=state.teams,
            training_match_ids=state.training_match_ids,
            training_result_ids=state.training_result_ids,
            training_facts=state.training_facts,
            training_data_hash=state.training_data_hash,
            approved_facts_hash=approved_facts_hash,
        )


class ReleasedStateCoreV1(SealedReleaseArtifactV1[ReleasedStateCoreContentV1]):
    schema_version: Literal["RELEASED_STATE_CORE_V1"] = "RELEASED_STATE_CORE_V1"


class ProductionQuantModelReleaseContentV1(ReleaseSnapshotV1):
    approval: ReleaseArtifactRefV1
    manifest: ReleaseArtifactRefV1
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    scope: ProductionScopeV1
    technical_evidence: TechnicalEvidenceRefsV1
    build_recipe: ReleaseArtifactRefV1
    code_revision: Identifier
    released_state_core: ReleasedStateCoreV1
    release_facts: tuple[ApprovedTrainingFact, ...] = Field(min_length=1)
    build_start_authorization: BuildAuthorizationV1
    build_completion_authorization: BuildAuthorizationV1
    build_started_at_utc: UtcDateTime
    build_completed_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime
    state_retention_horizon: RetentionHorizonV1
    audit_retention_horizon: RetentionHorizonV1

    @property
    def training_cutoff_at_utc(self) -> datetime:
        """Equal-value projection; the core is the sole authoritative cutoff."""
        return self.released_state_core.content_payload.training_cutoff_at_utc


class ProductionQuantModelReleaseV1(
    SealedReleaseArtifactV1[ProductionQuantModelReleaseContentV1]
):
    schema_version: Literal["PRODUCTION_QUANT_MODEL_RELEASE_V1"] = (
        "PRODUCTION_QUANT_MODEL_RELEASE_V1"
    )
    # Revalidated, hash-bound projections, not extra inputs to the release hash.
    training_manifest: TrainingHistoryManifestV1
    training_approval: TrainingHistoryApproval

    @model_validator(mode="after")
    def validate_release(self) -> Self:
        content = self.content_payload
        manifest = self.training_manifest
        approval = self.training_approval
        graph = manifest.content_payload.history
        core = content.released_state_core.content_payload
        if (
            content.manifest != manifest.reference()
            or content.approval != approval.reference()
            or content.scope != graph.scope
            or content.technical_evidence != manifest.content_payload.technical_evidence
            or content.build_recipe != content.technical_evidence.build_recipe
            or content.code_revision != content.technical_evidence.code_revision
            or content.release_facts != graph.facts
            or core.model != graph.scope.model
            or core.production_target_season_id
            != graph.scope.production_target_season_id
            or core.training_data_hash != graph.training_data_hash
            or core.approved_facts_hash != graph.approved_facts_hash
            or core.training_facts
            != tuple(item.content_payload.elo_fact for item in graph.facts)
        ):
            raise ValueError("release manifest/approval/core/fact context mismatch")
        if not (
            approval.persisted_at_utc
            <= core.training_cutoff_at_utc
            < content.build_started_at_utc
            <= content.build_completed_at_utc
            <= content.persisted_at_utc
        ):
            raise ValueError(
                "release authoritative training cutoff/actual timeline mismatch"
            )
        for item in content.release_facts:
            if isinstance(item, ApprovedTrainingFactV2):
                if item.content_payload.effective_source_available_at_utc > core.training_cutoff_at_utc:
                    raise ValueError("release contains newer versioned source facts")
                continue
            binding = item.content_payload.binding.content_payload
            for source_at in (
                binding.fixture_source.source_available_at_utc,
                binding.season_membership.content_payload.source_available_at_utc,
                binding.match_result_admission.content_payload.source_available_at_utc,
            ):
                if source_at > core.training_cutoff_at_utc:
                    raise ValueError(
                        "release contains newer fixture/mapping/result source facts"
                    )
        assert_authorization_progression(
            content.build_start_authorization.content_payload.current,
            content.build_completion_authorization.content_payload.current,
        )
        for authorization, phase, at in (
            (
                content.build_start_authorization,
                "BUILD_START",
                content.build_started_at_utc,
            ),
            (
                content.build_completion_authorization,
                "BUILD_COMPLETION",
                content.build_completed_at_utc,
            ),
        ):
            captured = authorization.content_payload
            if (
                captured.approval != content.approval
                or captured.manifest != content.manifest
                or captured.phase != phase
                or captured.current.actual_at_utc != at
                or captured.state_retention_horizon != content.state_retention_horizon
                or captured.audit_retention_horizon != content.audit_retention_horizon
            ):
                raise ValueError("captured build authorization context mismatch")
            _assert_approval_context(approval, manifest, captured.current)
            _assert_grant(
                approval,
                captured.current,
                ProductionGrantKind.PRODUCTION_MODEL_TRAINING,
            )
            assert_retention_authorized(
                approval,
                captured.current,
                content.state_retention_horizon,
                content.audit_retention_horizon,
            )
        for horizon in (
            content.state_retention_horizon,
            content.audit_retention_horizon,
        ):
            _horizon_not_past(horizon, content.persisted_at_utc)
        return self


class ProductionTargetV1(ReleaseSnapshotV1):
    schema_version: Literal["PRODUCTION_TARGET_V1"] = "PRODUCTION_TARGET_V1"
    match_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime

    @model_validator(mode="after")
    def distinct_teams(self) -> Self:
        if self.home_team_id == self.away_team_id:
            raise ValueError("target teams must differ")
        return self


class ProductionTargetAcceptancePlanContentV1(ReleaseSnapshotV1):
    release: ReleaseArtifactRefV1
    competition_id: Identifier
    production_target_season_id: Identifier
    targets: tuple[ProductionTargetV1, ...] = Field(min_length=1)
    kickoff_window_start_at_utc: UtcDateTime
    kickoff_window_end_at_utc: UtcDateTime
    decision_as_of_at_utc: UtcDateTime
    selection_rule: Literal[
        "KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1"
    ]
    minimum_prior_matches: Literal[5] = 5
    sealed_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        ids = tuple(item.match_id for item in self.targets)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("exact target IDs must be unique and ordered")
        if not self.sealed_at_utc <= self.persisted_at_utc < self.decision_as_of_at_utc:
            raise ValueError(
                "plan must be sealed/persisted before future decision cutoff"
            )
        if self.kickoff_window_start_at_utc >= self.kickoff_window_end_at_utc:
            raise ValueError("invalid target kickoff window")
        if any(
            not (
                self.kickoff_window_start_at_utc
                <= item.kickoff_at_utc
                < self.kickoff_window_end_at_utc
                and self.decision_as_of_at_utc < item.kickoff_at_utc
            )
            for item in self.targets
        ):
            raise ValueError("target kickoff outside window or not after decision")
        return self


class ProductionTargetAcceptancePlanV1(
    SealedReleaseArtifactV1[ProductionTargetAcceptancePlanContentV1]
):
    schema_version: Literal["PRODUCTION_TARGET_ACCEPTANCE_PLAN_V1"] = (
        "PRODUCTION_TARGET_ACCEPTANCE_PLAN_V1"
    )


class ApprovedTrainingHistoryAuditContentV1(ReleaseSnapshotV1):
    analysis_run_id: Identifier
    input_manifest_hash: Sha256Digest
    target_acceptance_plan: ReleaseArtifactRefV1
    decision_data_mode: Literal["LIVE_STRICT"] = "LIVE_STRICT"
    model_training_source_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    model_training_use_class: Literal["APPROVED_TRAINING_HISTORY"] = (
        "APPROVED_TRAINING_HISTORY"
    )
    retrospective_source_facts_present: Literal[True] = True
    packet: ReleaseArtifactRefV1
    quant_model_state_id: Identifier
    state_hash: Sha256Digest
    state_payload_hash: Sha256Digest
    training_data_hash: Sha256Digest
    release: ReleaseArtifactRefV1
    released_state_core_hash: Sha256Digest
    approval: ReleaseArtifactRefV1
    manifest: ReleaseArtifactRefV1
    approved_facts_hash: Sha256Digest
    technical_evidence: TechnicalEvidenceRefsV1
    source_summaries: tuple[HistorySourceSummaryV1, ...] = Field(min_length=1)
    season_summaries: tuple[HistorySeasonSummaryV1, ...] = Field(min_length=2)
    training_cutoff_at_utc: UtcDateTime
    decision_as_of_at_utc: UtcDateTime
    run_started_at_utc: UtcDateTime
    run_completed_at_utc: UtcDateTime
    run_code_revision: Identifier
    state_retention_horizon: RetentionHorizonV1
    audit_retention_horizon: RetentionHorizonV1
    generated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if not (
            self.training_cutoff_at_utc
            < self.decision_as_of_at_utc
            <= self.run_started_at_utc
            <= self.run_completed_at_utc
            <= self.generated_at_utc
        ):
            raise ValueError("audit actual timeline mismatch")
        return self


class ApprovedTrainingHistoryAuditV1(
    SealedReleaseArtifactV1[ApprovedTrainingHistoryAuditContentV1]
):
    schema_version: Literal["APPROVED_TRAINING_HISTORY_AUDIT_V1"] = (
        "APPROVED_TRAINING_HISTORY_AUDIT_V1"
    )


def elo_result_from_binding(binding: TrainingFactBindingV1) -> EloRegularTimeResult:
    content = binding.content_payload
    identity, result = content.canonical_identity, content.normalized_result
    # Availability/ingestion stay EXACTLY as admitted. Effective availability is
    # an independent eligibility check, not a replacement legacy math timestamp.
    return EloRegularTimeResult(
        match_result_id=result.match_result_id,
        match_id=result.match_id,
        season_id=identity.season,
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


def effective_source_time(binding: TrainingFactBindingV1) -> datetime:
    content = binding.content_payload
    return max(
        content.fixture_source.source_available_at_utc,
        content.season_membership.content_payload.source_available_at_utc,
        content.match_result_admission.content_payload.source_available_at_utc,
    )


def replay_exact_facts(
    facts: tuple[EloTrainingFact, ...],
    cutoff: datetime,
    target_season_id: str,
) -> EloBaselineState:
    results = tuple(
        EloRegularTimeResult(
            **item.model_dump(exclude={"sequence", "fact_hash", "source_payload_hash"}),
            payload_hash=item.source_payload_hash,
        )
        for item in facts
    )
    state = EloThreeWayBaseline().rebuild_state(
        results, cutoff, target_season_id=target_season_id
    )
    if state.training_facts != facts:
        raise ValueError(
            "exact Elo replay cannot filter, replace or reorder released facts"
        )
    return state


def approved_facts_root(facts: tuple[ApprovedTrainingFactV1, ...]) -> str:
    return tagged_canonical_sha256(
        "APPROVED_TRAINING_FACTS_ROOT_V1",
        tuple(
            {
                "fact_sequence": item.content_payload.fact_sequence,
                "approved_fact_hash": item.content_hash,
            }
            for item in facts
        ),
    )


def prepare_approved_facts(
    admissions: tuple[TrainingFactAdmissionV1, ...],
    window: EloTrainingWindowV1,
    pilot_scope_id: str,
) -> tuple[ApprovedTrainingFactV1, ...]:
    seasons = {item.season_id: item for item in window.content_payload.seasons}
    pairs = [(admission, fact) for admission in admissions for fact in admission.facts]
    pairs.sort(
        key=lambda pair: (
            pair[1].content_payload.canonical_identity.kickoff_at_utc,
            effective_source_time(pair[1]),
            pair[1].content_payload.normalized_result.ingested_at_utc,
            pair[1].content_payload.normalized_result.match_id,
            pair[1].content_payload.normalized_result.match_result_id,
        )
    )
    facts = []
    for sequence, (admission, binding) in enumerate(pairs):
        content = binding.content_payload
        identity = content.canonical_identity
        season = seasons.get(identity.season)
        membership = content.season_membership.content_payload
        provider = ProviderSeasonRefV1(
            source_id=membership.source_id,
            provider_code=membership.provider_code,
            provider_competition_id=membership.provider_competition_id,
            provider_season_id=membership.provider_season_id,
        )
        if (
            season is None
            or provider not in season.provider_seasons
            or identity.internal_competition_id != window.content_payload.competition_id
        ):
            raise ValueError(
                "admitted fact outside explicit competition/provider season window"
            )
        rights = admission.source_rights_admission
        facts.append(
            ApprovedTrainingFactV1.freeze(
                content_payload=ApprovedTrainingFactContentV1(
                    fact_sequence=sequence,
                    season_sequence=season.season_sequence,
                    integrity_pilot_scope_id=pilot_scope_id,
                    training_fact_admission=ReleaseArtifactRefV1(
                        artifact_id=admission.training_fact_admission_id,
                        content_hash=admission.admission_hash,
                    ),
                    source_rights_admission=ReleaseArtifactRefV1(
                        artifact_id=rights.source_rights_admission_id,
                        content_hash=rights.admission_hash,
                    ),
                    terms_sha256=rights.content_payload.rights_payload.terms_sha256,
                    binding=binding,
                    elo_fact=EloTrainingFact.from_result(
                        sequence=sequence, result=elo_result_from_binding(binding)
                    ),
                    effective_source_available_at_utc=effective_source_time(binding),
                )
            )
        )
    season_sequences = tuple(item.content_payload.season_sequence for item in facts)
    if season_sequences != tuple(sorted(season_sequences)):
        raise ValueError(
            "fact seasons must follow declared contiguous chronological blocks"
        )
    return tuple(facts)


def history_summaries(
    facts: tuple[ApprovedTrainingFactV1, ...],
    window: EloTrainingWindowV1,
) -> tuple[tuple[HistorySourceSummaryV1, ...], tuple[HistorySeasonSummaryV1, ...]]:
    grouped: dict[tuple[str, str, str], list[ApprovedTrainingFactV1]] = {}
    for item in facts:
        content = item.content_payload
        fixture = content.binding.content_payload.fixture_source
        grouped.setdefault(
            (
                fixture.source_id,
                fixture.provider_code,
                content.source_rights_admission.artifact_id,
            ),
            [],
        ).append(item)
    sources = []
    for sequence, ((source, provider, _), items) in enumerate(sorted(grouped.items())):
        selected = tuple(items)
        first = items[0].content_payload
        bindings = tuple(item.content_payload.binding.content_payload for item in items)
        sources.append(
            HistorySourceSummaryV1(
                source_sequence=sequence,
                source_id=source,
                provider_code=provider,
                source_rights_admission=first.source_rights_admission,
                terms_sha256=first.terms_sha256,
                fact_count=len(items),
                facts_hash=approved_facts_root(selected),
                fixture_source_count=len(items),
                fixture_sources_hash=tagged_canonical_sha256(
                    "HISTORY_FIXTURE_SOURCES_ROOT_V1",
                    tuple(item.fixture_source for item in bindings),
                ),
                mapping_source_count=len(items),
                mapping_sources_hash=tagged_canonical_sha256(
                    "HISTORY_MAPPING_SOURCES_ROOT_V1",
                    tuple(
                        {
                            "mapping": item.provider_mapping,
                            "membership": item.season_membership,
                        }
                        for item in bindings
                    ),
                ),
                result_source_count=len(items),
                result_sources_hash=tagged_canonical_sha256(
                    "HISTORY_RESULT_SOURCES_ROOT_V1",
                    tuple(
                        {
                            "admission": item.match_result_admission,
                            "normalized_result": item.normalized_result,
                        }
                        for item in bindings
                    ),
                ),
            )
        )
    seasons = []
    for season in window.content_payload.seasons:
        selected = tuple(
            item
            for item in facts
            if item.content_payload.season_sequence == season.season_sequence
        )
        if not selected and season.role != "PRODUCTION_TARGET":
            raise ValueError("historical window seasons require admitted facts")
        seasons.append(
            HistorySeasonSummaryV1(
                season=season,
                fact_count=len(selected),
                facts_hash=approved_facts_root(selected),
            )
        )
    return tuple(sources), tuple(seasons)


def approved_versioned_facts_root(facts) -> str:
    return tagged_canonical_sha256(
        "APPROVED_TRAINING_FACTS_ROOT_V2", tuple(f.reference() for f in facts)
    )


def prepare_versioned_approved_facts(
    admissions, context, window, pilot_scope_id, cutoff, excluded
):
    parents = {a.training_fact_admission_id: a for a in admissions}
    seasons = window.content_payload.ordered_season_ids
    versions = select_versioned_training_facts(
        context, window.content_payload.competition_id, seasons, cutoff, excluded, False
    )
    facts = []
    for sequence, version in enumerate(versions):
        parent = parents[version.base_admission.artifact_id]
        rights = parent.source_rights_admission
        facts.append(
            ApprovedTrainingFactV2.freeze(
                content_payload=ApprovedTrainingFactContentV2(
                    fact_sequence=sequence,
                    season_sequence=seasons.index(version.snapshot.identity.season),
                    integrity_pilot_scope_id=pilot_scope_id,
                    training_fact_admission=ReleaseArtifactRefV1(
                        artifact_id=parent.training_fact_admission_id,
                        content_hash=parent.admission_hash,
                    ),
                    source_rights_admission=ReleaseArtifactRefV1(
                        artifact_id=rights.source_rights_admission_id,
                        content_hash=rights.admission_hash,
                    ),
                    terms_sha256=rights.content_payload.rights_payload.terms_sha256,
                    version=version,
                    elo_fact=EloTrainingFact.from_result(
                        sequence=sequence,
                        result=project_versioned_training_fact(version),
                    ),
                    effective_source_available_at_utc=version.snapshot.effective_source_available_at_utc,
                )
            )
        )
    return tuple(facts)


def versioned_history_summaries(facts, window):
    grouped = {}
    for fact in facts:
        c = fact.content_payload
        stream = c.version.snapshot.stream
        grouped.setdefault(
            (
                stream.source_id,
                stream.provider_code,
                c.source_rights_admission.artifact_id,
            ),
            [],
        ).append(fact)
    sources = []
    for sequence, ((source, provider, _), values) in enumerate(sorted(grouped.items())):
        first = values[0].content_payload
        refs = tuple(VersionedFactRefV2.of(f.content_payload.version) for f in values)
        sources.append(
            HistorySourceSummaryV1(
                source_sequence=sequence,
                source_id=source,
                provider_code=provider,
                source_rights_admission=first.source_rights_admission,
                terms_sha256=first.terms_sha256,
                fact_count=len(values),
                facts_hash=approved_versioned_facts_root(values),
                fixture_source_count=len(values),
                fixture_sources_hash=tagged_canonical_sha256(
                    "HISTORY_FIXTURE_VERSIONS_ROOT_V2", refs
                ),
                mapping_source_count=len(values),
                mapping_sources_hash=tagged_canonical_sha256(
                    "HISTORY_MAPPING_VERSIONS_ROOT_V2", refs
                ),
                result_source_count=len(values),
                result_sources_hash=tagged_canonical_sha256(
                    "HISTORY_RESULT_VERSIONS_ROOT_V2", refs
                ),
            )
        )
    seasons = tuple(
        HistorySeasonSummaryV1(
            season=season,
            fact_count=sum(
                f.content_payload.season_sequence == season.season_sequence
                for f in facts
            ),
            facts_hash=approved_versioned_facts_root(
                tuple(
                    f
                    for f in facts
                    if f.content_payload.season_sequence == season.season_sequence
                )
            ),
        )
        for season in window.content_payload.seasons
    )
    return tuple(sources), seasons


def source_rights_refs(
    history: TrainingHistoryGraphV1,
) -> tuple[SourceRightsRefV1, ...]:
    refs = {}
    for admission in history.admissions:
        rights = admission.source_rights_admission
        payload = rights.content_payload.rights_payload
        ref = SourceRightsRefV1(
            admission=ReleaseArtifactRefV1(
                artifact_id=rights.source_rights_admission_id,
                content_hash=rights.admission_hash,
            ),
            terms_sha256=payload.terms_sha256,
            source_ids=payload.source_ids,
        )
        previous = refs.setdefault(ref.admission.artifact_id, ref)
        if previous != ref:
            raise ValueError("conflicting source rights references")
    return tuple(refs[key] for key in sorted(refs))


def approval_active_for_build(
    *,
    approval: TrainingHistoryApproval,
    manifest: TrainingHistoryManifestV1,
    current: CurrentAuthorizationInputsV1,
) -> Literal[True]:
    """Raise on failure; authorize training only, not inference or retention."""
    approval, manifest, current = (
        revalidate(approval),
        revalidate(manifest),
        revalidate(current),
    )
    _assert_approval_context(approval, manifest, current)
    _assert_grant(approval, current, ProductionGrantKind.PRODUCTION_MODEL_TRAINING)
    return True


def release_active_for_inference(
    *,
    release: ProductionQuantModelReleaseV1,
    plan: ProductionTargetAcceptancePlanV1,
    current: CurrentAuthorizationInputsV1,
    state_retention_horizon: RetentionHorizonV1,
    audit_retention_horizon: RetentionHorizonV1,
) -> Literal[True]:
    """Call at each actual operation start AND completion with fresh full inputs.

    Training authorization is checked at captured build times, never at the
    current inference time. A knowledge cutoff must not stand in for actual time.
    """
    release, plan, current = revalidate(release), revalidate(plan), revalidate(current)
    content = release.content_payload
    if not content.persisted_at_utc < current.actual_at_utc:
        raise ValueError("release must already be persisted before inference")
    assert_target_plan(release, plan)
    if current.actual_at_utc < plan.content_payload.decision_as_of_at_utc:
        raise ValueError("inference actual time precedes decision cutoff")
    _assert_approval_context(
        release.training_approval,
        release.training_manifest,
        current,
        require_current_source_rights=False,
    )
    _assert_grant(
        release.training_approval,
        current,
        ProductionGrantKind.PRODUCTION_MODEL_INFERENCE,
        release.reference(),
    )
    assert_retention_authorized(
        release.training_approval,
        current,
        revalidate(state_retention_horizon),
        revalidate(audit_retention_horizon),
        release.reference(),
    )
    # Existing released state/audit commitments cannot be shortened by a new run.
    assert_retention_authorized(
        release.training_approval,
        current,
        content.state_retention_horizon,
        content.audit_retention_horizon,
        release.reference(),
    )
    return True


def assert_target_plan(
    release: ProductionQuantModelReleaseV1, plan: ProductionTargetAcceptancePlanV1
) -> None:
    content, target = release.content_payload, plan.content_payload
    if (
        target.release != release.reference()
        or target.competition_id != content.scope.competition_id
        or target.production_target_season_id
        != content.scope.production_target_season_id
        or not content.persisted_at_utc < target.sealed_at_utc
    ):
        raise ValueError("target plan release/scope/sealing mismatch")
    if set(item.match_id for item in target.targets).intersection(
        content.released_state_core.content_payload.training_match_ids
    ):
        raise ValueError("target intersection with released training facts")


def assert_retention_authorized(
    approval: TrainingHistoryApproval,
    current: CurrentAuthorizationInputsV1,
    state_horizon: RetentionHorizonV1,
    audit_horizon: RetentionHorizonV1,
    release_ref: ReleaseArtifactRefV1 | None = None,
) -> None:
    for kind, horizon in (
        (ProductionGrantKind.DERIVED_MODEL_STATE_RETENTION, state_horizon),
        (ProductionGrantKind.AUDIT_HASH_RETENTION, audit_horizon),
    ):
        grant = _assert_grant(approval, current, kind, release_ref)
        _horizon_not_past(horizon, current.actual_at_utc)
        retention = grant.retention
        if retention is None or (
            not retention.indefinite
            and (
                horizon.indefinite
                or horizon.retain_until_at_utc > retention.retain_until_at_utc
            )
        ):
            raise ValueError(f"{kind} does not cover declared retention horizon")


def _assert_approval_context(
    approval, manifest, current, *, require_current_source_rights=True
) -> None:
    payload = approval.subject
    graph = manifest.content_payload.history
    if (
        payload.manifest != manifest.reference()
        or payload.scope != graph.scope
        or payload.source_rights != source_rights_refs(graph)
        or not approval.matches_technical_evidence(
            manifest.content_payload.technical_evidence
        )
        or not approval.matches_technical_evidence(current.technical_evidence)
    ):
        raise ValueError(
            "approval manifest/source/pilot terminal evidence context mismatch"
        )
    review = approval.content_payload.reviewer_attestation.content_payload
    if not (
        manifest.content_payload.persisted_at_utc
        <= review.reviewed_at_utc
        <= approval.recorded_at_utc
        <= approval.persisted_at_utc
        <= current.actual_at_utc
    ):
        raise ValueError(
            "approval/manifest not reviewed and persisted before operation"
        )
    if require_current_source_rights:
        for admission in graph.admissions:
            # Research rights gate new builds, not independently licensed inference.
            admission.source_rights_admission.assert_active_for(
                current.actual_at_utc, ()
            )
    _assert_no_visible_correction(graph, current)


def _assert_grant(approval, current, kind, release_ref=None) -> ProductionGrantV1:
    at = current.actual_at_utc
    grant = next(item for item in approval.subject.grants if item.grant == kind)
    if not grant.effective_at_utc <= at or (
        grant.expires_at_utc is not None and at >= grant.expires_at_utc
    ):
        raise ValueError(f"{kind} grant is not active")
    approval_ref = approval.reference()
    for event in current.revocations:
        item = event.content_payload
        if item.approval.artifact_id != approval_ref.artifact_id:
            continue
        if item.approval != approval_ref:
            raise ValueError("revocation approval hash mismatch")
        if item.recorded_at_utc < approval.persisted_at_utc:
            raise ValueError("revocation cannot be recorded before its approval")
        if item.release is not None:
            if (
                release_ref is None
                or item.release.artifact_id != release_ref.artifact_id
            ):
                continue
            if item.release != release_ref:
                raise ValueError("revocation release hash mismatch")
        if (
            kind in item.affected_grants
            and max(item.recorded_at_utc, item.effective_at_utc) <= at
        ):
            raise ValueError(f"{kind} revoked at actual operation time")
    for event in current.successors:
        item = event.content_payload
        if item.predecessor.artifact_id != approval_ref.artifact_id:
            continue
        if (
            item.predecessor != approval_ref
            or item.predecessor_scope != approval.subject.scope
        ):
            raise ValueError("successor approval hash/scope mismatch")
        if item.successor_persisted_at_utc < approval.persisted_at_utc:
            raise ValueError("successor cannot be persisted before its predecessor")
        if (
            kind in item.affected_grants
            and item.successor_persisted_at_utc <= item.effective_at_utc <= at
        ):
            raise ValueError(f"{kind} superseded at actual operation time")
    return grant


def _assert_no_visible_correction(graph, current) -> None:
    if isinstance(graph, TrainingHistoryGraphV2):
        # All predecessors remain in context for early slices, but only selected
        # heads are current. Ancestor events are not new invalidations.
        by_ref = {v.reference: v for v in graph.correction_context.versions}
        selected = {r.version for r in graph.selected_heads}
        incorporated = set()
        for ref in selected:
            while ref is not None:
                version = by_ref[ref]
                incorporated.update(c.reference.as_v1() for c in version.components)
                ref = version.predecessor
        scoped = {
            (
                v.snapshot.stream.source_id,
                v.snapshot.stream.provider_code,
                v.snapshot.stream.internal_match_id,
            )
            for v in graph.correction_context.versions
        }
        for event in current.corrections:
            c = event.content_payload
            if (c.source_id, c.provider_code, c.match_id) not in scoped:
                continue
            if (
                max(c.source_available_at_utc, c.registered_at_utc)
                <= current.actual_at_utc
                and c.successor not in incorporated
            ):
                raise ValueError(
                    "newer source-visible and locally registered correction"
                )
        return
    facts = {
        item.content_payload.elo_fact.match_id: item.content_payload.binding.content_payload
        for item in graph.facts
    }
    for event in current.corrections:
        item = event.content_payload
        binding = facts.get(item.match_id)
        if binding is None:
            continue
        fixture = binding.fixture_source
        if (item.source_id, item.provider_code) != (
            fixture.source_id,
            fixture.provider_code,
        ):
            raise ValueError("correction source scope mismatch")
        membership = binding.season_membership
        admission = binding.match_result_admission
        if item.component == "FIXTURE":
            original = ReleaseArtifactRefV1(
                artifact_id=fixture.fixture_source_record_id,
                content_hash=fixture.fixture_record_sha256,
            )
            source_at, registered_at = (
                fixture.source_available_at_utc,
                fixture.registered_at_utc,
            )
        elif item.component == "MAPPING":
            mapping = binding.provider_mapping
            original = ReleaseArtifactRefV1(
                artifact_id=mapping.mapping_id,
                content_hash=tagged_canonical_sha256(mapping.schema_version, mapping),
            )
            source_at = mapping.available_at_utc
            registered_at = membership.content_payload.registered_at_utc
        elif item.component == "SEASON":
            original = ReleaseArtifactRefV1(
                artifact_id=membership.season_membership_id,
                content_hash=membership.membership_hash,
            )
            source_at, registered_at = (
                membership.content_payload.source_available_at_utc,
                membership.content_payload.registered_at_utc,
            )
        else:
            original = ReleaseArtifactRefV1(
                artifact_id=admission.match_result_admission_id,
                content_hash=admission.admission_hash,
            )
            source_at, registered_at = (
                admission.content_payload.source_available_at_utc,
                admission.content_payload.registered_at_utc,
            )
        if item.predecessor.artifact_id == original.artifact_id and (
            item.predecessor != original
            or item.predecessor_source_available_at_utc != source_at
            or item.registered_at_utc < registered_at
        ):
            raise ValueError("correction predecessor hash/source/actual clock mismatch")
        if (
            item.successor.artifact_id == original.artifact_id
            and item.successor != original
        ):
            raise ValueError("correction successor hash mismatch")
        if item.successor == original:
            continue
        if (
            source_at <= item.source_available_at_utc <= current.actual_at_utc
            and registered_at <= item.registered_at_utc <= current.actual_at_utc
        ):
            raise ValueError("newer source-visible and locally registered correction")


def _horizon_not_past(horizon: RetentionHorizonV1, at: datetime) -> None:
    if horizon.retain_until_at_utc is not None and horizon.retain_until_at_utc < at:
        raise ValueError("declared retention horizon precedes actual operation")


def _provider_season_key(item: ProviderSeasonRefV1) -> tuple[str, ...]:
    return (
        item.source_id,
        item.provider_code,
        item.provider_competition_id,
        item.provider_season_id,
    )


def _ordered_grants(values: tuple[ProductionGrantKind, ...]) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError("affected grants must be unique and ordered")


def _sequence(values: tuple[int, ...], label: str) -> None:
    if values != tuple(range(len(values))):
        raise ValueError(f"{label} sequence must be contiguous")


def _unique(values: tuple, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
