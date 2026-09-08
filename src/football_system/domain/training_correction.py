"""Controlled correction snapshots. V1 artifacts and their hashes stay unchanged.

A reference is not evidence of persistence. The writer resolves the complete
predecessor in SQLite and re-extracts the candidate before accepting a review.
Identity here is revision metadata attached to an existing matches row, not a
second canonical match or result universe.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from football_system.domain.archive import match_result_payload_sha256

from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    NormalizedMatchResultRecordV1,
    ProviderResultStatusCategory,
    Sha256Digest,
    TrainingCanonicalMatchIdentityV1,
    TrainingProviderMatchMappingV1,
    TrainingFactAdmissionV1,
    TrainingFactBindingV1,
    tagged_canonical_sha256,
)

CORRECTION_INTENT_V2 = "TRAINING_CORRECTION_INTENT_V2"


class CorrectionProjectionModel(DomainModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def revalidate_nested_copies(cls, value):
        def plain(item):
            if isinstance(item, BaseModel):
                fields = item.model_dump(mode="python")
                fields.update(
                    {
                        name: getattr(item, name)
                        for name, field in type(item).model_fields.items()
                        if field.exclude
                    }
                )
                return plain(fields)
            if isinstance(item, dict):
                return {key: plain(child) for key, child in item.items()}
            if isinstance(item, (tuple, list)):
                return tuple(plain(child) for child in item)
            return item

        return plain(value)


class CorrectionComponent(StrEnum):
    FIXTURE = "FIXTURE"
    MAPPING = "MAPPING"
    SEASON = "SEASON"
    STATUS = "STATUS"
    RESULT = "RESULT"


class CorrectionRefV2(DomainModel):
    """Globally scoped typed reference; a JSON pointer is never an artifact ID."""

    schema_version: Identifier
    artifact_id: Identifier
    content_hash: Sha256Digest

    def as_v1(self):
        """Use this same projection for both pinned refs and V1 invalidations."""
        from football_system.domain.production_release import ReleaseArtifactRefV1

        digest = tagged_canonical_sha256("TYPED_CORRECTION_REF_V2", self)
        return ReleaseArtifactRefV1(
            artifact_id=stable_id("TYPED_CORRECTION_REF_V2", digest),
            content_hash=digest,
        )


class CorrectionStreamV2(DomainModel):
    source_id: Identifier
    provider_code: Identifier
    provider_fixture_namespace: Identifier
    provider_fixture_key: Identifier
    internal_match_id: Identifier

    @property
    def stream_id(self) -> str:
        return stable_id(
            "TRAINING_CORRECTION_STREAM_V2",
            tagged_canonical_sha256("TRAINING_CORRECTION_STREAM_V2", self),
        )


class CorrectionCaptureRefV2(DomainModel):
    capture_receipt_id: Identifier
    receipt_hash: Sha256Digest
    payload_sha256: Sha256Digest
    record_pointer: str = Field(max_length=2048)
    record_sha256: Sha256Digest


class CorrectionEvidenceV2(DomainModel):
    fixture: CorrectionCaptureRefV2
    scope: CorrectionCaptureRefV2
    result: CorrectionCaptureRefV2
    adapter: LocalReviewEvidenceV1
    home_team_alias_id: Identifier
    away_team_alias_id: Identifier
    competition_mapping_id: Identifier
    provider_mapping_id: Identifier


class CorrectionSnapshotV2(DomainModel):
    stream: CorrectionStreamV2
    identity: TrainingCanonicalMatchIdentityV1
    provider_mapping: TrainingProviderMatchMappingV1
    provider_home_team_id: Identifier
    provider_away_team_id: Identifier
    provider_competition_id: Identifier
    provider_season_id: Identifier
    home_team_alias_id: Identifier
    away_team_alias_id: Identifier
    competition_mapping_id: Identifier
    season_mapping_version: Identifier
    mapping_policy_version: Identifier
    status_mapping_version: Identifier
    provider_raw_status: Identifier
    provider_status_category: ProviderResultStatusCategory
    raw_score_semantics: Identifier | None
    regular_time_score_semantics: Identifier
    home_goals: int | None = Field(ge=0, strict=True)
    away_goals: int | None = Field(ge=0, strict=True)
    provider_finalized_at_utc: UtcDateTime | None
    source_observed_at_utc: UtcDateTime
    fixture_source_available_at_utc: UtcDateTime
    mapping_source_available_at_utc: UtcDateTime
    result_source_available_at_utc: UtcDateTime
    provider_result_key: Identifier
    provider_revision_id: Identifier | None = None
    provider_revision_order: int | None = Field(default=None, ge=0, strict=True)

    @property
    def trainable(self) -> bool:
        return (
            self.provider_status_category
            is ProviderResultStatusCategory.REGULAR_TIME_FINAL
            and self.raw_score_semantics == self.regular_time_score_semantics
        )

    @property
    def effective_source_available_at_utc(self) -> datetime:
        return max(
            self.fixture_source_available_at_utc,
            self.mapping_source_available_at_utc,
            self.result_source_available_at_utc,
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        m, s, i = self.provider_mapping, self.stream, self.identity
        if (
            m.provider_code,
            m.external_namespace,
            m.external_match_id,
            m.internal_match_id,
        ) != (
            s.provider_code,
            s.provider_fixture_namespace,
            s.provider_fixture_key,
            s.internal_match_id,
        ) or i.internal_match_id != s.internal_match_id:
            raise ValueError(
                "correction must preserve the existing match stream anchor"
            )
        if self.source_observed_at_utc > self.result_source_available_at_utc:
            raise ValueError("correction observation follows source publication")
        if (
            self.provider_finalized_at_utc is not None
            and self.provider_finalized_at_utc > self.source_observed_at_utc
        ):
            raise ValueError("correction finalization follows observation")
        if self.trainable and (
            self.home_goals is None
            or self.away_goals is None
            or self.provider_finalized_at_utc is None
            or self.provider_finalized_at_utc <= i.kickoff_at_utc
        ):
            raise ValueError(
                "trainable correction requires explicit scores and finalization after kickoff"
            )
        if (self.provider_revision_id is None) != (
            self.provider_revision_order is None
        ):
            raise ValueError(
                "provider revision ID and explicit order must be supplied together"
            )
        return self

    def component_value(self, component: CorrectionComponent) -> dict:
        fields = {
            CorrectionComponent.FIXTURE: (
                "provider_home_team_id",
                "provider_away_team_id",
            ),
            CorrectionComponent.MAPPING: (
                "provider_mapping",
                "home_team_alias_id",
                "away_team_alias_id",
                "mapping_policy_version",
            ),
            CorrectionComponent.SEASON: (
                "provider_competition_id",
                "provider_season_id",
                "competition_mapping_id",
                "season_mapping_version",
            ),
            CorrectionComponent.STATUS: (
                "provider_raw_status",
                "provider_status_category",
                "raw_score_semantics",
                "regular_time_score_semantics",
                "status_mapping_version",
            ),
            CorrectionComponent.RESULT: (
                "provider_result_key",
                "home_goals",
                "away_goals",
                "provider_finalized_at_utc",
                "source_observed_at_utc",
                "result_source_available_at_utc",
            ),
        }[component]
        value = {name: getattr(self, name) for name in fields}
        if component is CorrectionComponent.FIXTURE:
            value["kickoff_at_utc"] = self.identity.kickoff_at_utc
            value["source_available_at_utc"] = self.fixture_source_available_at_utc
        elif component is CorrectionComponent.MAPPING:
            value.update(
                home=self.identity.internal_home_team_id,
                away=self.identity.internal_away_team_id,
            )
        elif component is CorrectionComponent.SEASON:
            value.update(
                competition=self.identity.internal_competition_id,
                season=self.identity.season,
                competition_type=self.identity.competition_type,
                source_available_at_utc=self.mapping_source_available_at_utc,
            )
        return value


class CorrectionComponentBindingV2(DomainModel):
    component: CorrectionComponent
    reference: CorrectionRefV2
    source_available_at_utc: UtcDateTime


class TrainingCorrectionIntentV2(DomainModel):
    schema_version: Literal["TRAINING_CORRECTION_INTENT_V2"] = CORRECTION_INTENT_V2
    source_rights_admission: CorrectionRefV2
    predecessor: CorrectionRefV2
    predecessor_components: tuple[CorrectionComponentBindingV2, ...]
    revision_sequence: int = Field(ge=1, strict=True)
    changed_components: tuple[CorrectionComponent, ...] = Field(min_length=1)
    candidate: CorrectionSnapshotV2
    evidence: CorrectionEvidenceV2
    match_result_id: Identifier | None

    @property
    def intent_hash(self) -> str:
        return tagged_canonical_sha256(CORRECTION_INTENT_V2, self)

    @model_validator(mode="after")
    def complete_components(self) -> Self:
        if tuple(x.component for x in self.predecessor_components) != tuple(
            CorrectionComponent
        ):
            raise ValueError(
                "intent requires all five exact predecessor component refs"
            )
        if self.changed_components != tuple(
            x for x in CorrectionComponent if x in self.changed_components
        ):
            raise ValueError("changed_components must be unique and in component order")
        if self.candidate.trainable != (self.match_result_id is not None):
            raise ValueError("nontrainable correction must not fabricate a MatchResult")
        return self


class TrainingCorrectionContentV2(DomainModel):
    intent: TrainingCorrectionIntentV2
    reviewer_evidence: LocalReviewEvidenceV1
    reviewer_authority: LocalReviewEvidenceV1
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime
    actual_started_at_utc: UtcDateTime
    actual_completed_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime
    local_imported_at_utc: UtcDateTime
    components: tuple[CorrectionComponentBindingV2, ...]
    normalized_result: NormalizedMatchResultRecordV1 | None
    previous_match_result_id: Identifier

    @model_validator(mode="after")
    def timeline(self) -> Self:
        if not (
            self.local_imported_at_utc
            <= self.reviewed_at_utc
            <= self.actual_started_at_utc
            <= self.actual_completed_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("correction review/capture must precede actual recording")
        if tuple(x.component for x in self.components) != tuple(CorrectionComponent):
            raise ValueError("correction requires all five component bindings")
        if self.intent.candidate.trainable != (self.normalized_result is not None):
            raise ValueError("nontrainable head cannot contain a normalized result")
        return self


class TrainingCorrectionAdmissionV2(DomainModel):
    schema_version: Literal["TRAINING_CORRECTION_ADMISSION_V2"] = (
        "TRAINING_CORRECTION_ADMISSION_V2"
    )
    artifact_id: Identifier
    content_hash: Sha256Digest
    content_payload: TrainingCorrectionContentV2

    @classmethod
    def freeze(cls, *, content_payload: TrainingCorrectionContentV2) -> Self:
        content = TrainingCorrectionContentV2.model_validate(
            content_payload.model_dump(mode="python")
        )
        tag = cls.model_fields["schema_version"].default
        digest = tagged_canonical_sha256(tag, content)
        return cls(
            artifact_id=stable_id(tag, digest),
            content_hash=digest,
            content_payload=content,
        )

    @model_validator(mode="after")
    def seal(self) -> Self:
        digest = tagged_canonical_sha256(self.schema_version, self.content_payload)
        if digest != self.content_hash or self.artifact_id != stable_id(
            self.schema_version, digest
        ):
            raise ValueError("correction admission seal mismatch")
        return self

    def reference(self) -> CorrectionRefV2:
        return CorrectionRefV2(
            schema_version=self.schema_version,
            artifact_id=self.artifact_id,
            content_hash=self.content_hash,
        )


class TrainingFactVersionV2(CorrectionProjectionModel):
    """A typed partial projection, not the content payload of reference's seal."""

    reference: CorrectionRefV2
    base_admission: CorrectionRefV2
    base_binding: CorrectionRefV2
    revision_sequence: int = Field(ge=0, strict=True)
    predecessor: CorrectionRefV2 | None
    snapshot: CorrectionSnapshotV2
    components: tuple[CorrectionComponentBindingV2, ...]
    normalized_result: NormalizedMatchResultRecordV1 | None
    latest_match_result_id: Identifier
    registered_at_utc: UtcDateTime

    @property
    def version_id(self) -> str:
        return self.reference.artifact_id

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        snapshot, result = self.snapshot, self.normalized_result
        if (self.base_admission.schema_version, self.base_binding.schema_version) != (
            "TRAINING_FACT_ADMISSION_V1",
            "TRAINING_FACT_BINDING_V1",
        ) or self.reference.schema_version != (
            "TRAINING_CORRECTION_ADMISSION_V2"
            if self.revision_sequence
            else "TRAINING_BASE_FACT_VERSION_V2"
        ):
            raise ValueError(
                "version requires correctly typed admission and binding references"
            )
        if tuple(c.component for c in self.components) != tuple(CorrectionComponent):
            raise ValueError("version requires all five exact component bindings")
        if snapshot.trainable != (result is not None):
            raise ValueError(
                "nontrainable version must not fabricate a normalized result"
            )
        if (
            max(
                snapshot.effective_source_available_at_utc,
                snapshot.provider_mapping.available_at_utc,
                *(c.source_available_at_utc for c in self.components),
            )
            > self.registered_at_utc
        ):
            raise ValueError(
                "version source publication cannot follow actual registration"
            )
        if result is not None:
            if (
                result.match_id,
                result.provider_code,
                result.home_goals,
                result.away_goals,
                result.available_at_utc,
                result.ingested_at_utc,
                result.observed_at_utc,
                result.source_result_key,
            ) != (
                snapshot.stream.internal_match_id,
                snapshot.stream.provider_code,
                snapshot.home_goals,
                snapshot.away_goals,
                snapshot.result_source_available_at_utc,
                snapshot.result_source_available_at_utc,
                snapshot.source_observed_at_utc,
                snapshot.provider_result_key,
            ) or result.payload_hash != match_result_payload_sha256(
                result.home_goals, result.away_goals
            ):
                raise ValueError("version normalized result/snapshot mismatch")
            if self.latest_match_result_id != result.match_result_id:
                raise ValueError("version latest normalized result reference mismatch")
        if not self.revision_sequence:
            if (
                self.predecessor is not None
                or result is None
                or result.supersedes_match_result_id is not None
            ):
                raise ValueError(
                    "base version must preserve its original normalized result and have no predecessor"
                )
        elif (
            self.predecessor is None
            or self.predecessor.artifact_id == self.version_id
            or self.predecessor.schema_version
            != (
                "TRAINING_BASE_FACT_VERSION_V2"
                if self.revision_sequence == 1
                else "TRAINING_CORRECTION_ADMISSION_V2"
            )
            or (result is not None and result.supersedes_match_result_id is None)
        ):
            raise ValueError("corrected version requires an exact typed predecessor")
        return self


class SourceCorrectionV2(DomainModel):
    """Invalidation evidence generated only from a verified persisted transition."""

    schema_version: Literal["SOURCE_CORRECTION_V2"] = "SOURCE_CORRECTION_V2"
    transition: CorrectionRefV2
    stream: CorrectionStreamV2
    revision_sequence: int = Field(ge=1, strict=True)
    component: CorrectionComponent
    predecessor_version: CorrectionRefV2
    successor_version: CorrectionRefV2
    predecessor: CorrectionRefV2
    successor: CorrectionRefV2
    predecessor_source_available_at_utc: UtcDateTime
    source_available_at_utc: UtcDateTime
    local_imported_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime

    def as_v1(self):
        from football_system.domain.production_release import (
            SourceCorrectionContentV1,
            SourceCorrectionV1,
        )

        # V1 has no type field. Hash the WHOLE typed ref, never export ROOT or a
        # naked record pointer as the globally compared ID.
        return SourceCorrectionV1.freeze(
            content_payload=SourceCorrectionContentV1(
                source_id=self.stream.source_id,
                provider_code=self.stream.provider_code,
                match_id=self.stream.internal_match_id,
                component=self.component.value,
                predecessor=self.predecessor.as_v1(),
                successor=self.successor.as_v1(),
                predecessor_source_available_at_utc=self.predecessor_source_available_at_utc,
                source_available_at_utc=self.source_available_at_utc,
                local_imported_at_utc=self.local_imported_at_utc,
                registered_at_utc=self.registered_at_utc,
            )
        )


class TrainingCorrectionContextV2(CorrectionProjectionModel):
    """Closed projections, with optional flat artifacts authenticating their refs.

    The integration validator already revalidates this model, so these invariants
    also apply there. Empty artifact lists describe inert structural test inputs;
    select() requires full reference linkage, not an invented hash of a subset.
    Artifact seals do not establish DB registration, raw bytes, or reviewer rights:
    those remain the authoritative repository loader's responsibility.
    """

    actual_at_utc: UtcDateTime
    versions: tuple[TrainingFactVersionV2, ...]
    corrections: tuple[SourceCorrectionV2, ...]
    # Non-wire verification attachments. Serializing a partial context does not
    # turn its opaque refs into artifact contents; reload before core selection.
    base_artifacts: tuple[TrainingFactAdmissionV1, ...] = Field(
        default=(), exclude=True, repr=False
    )
    correction_artifacts: tuple[TrainingCorrectionAdmissionV2, ...] = Field(
        default=(), exclude=True, repr=False
    )

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        seen, heads, matches, logical_keys = {}, {}, {}, {}
        expected_events = []
        for version in self.versions:
            snapshot = version.snapshot
            if (
                version.version_id in seen
                or version.registered_at_utc > self.actual_at_utc
            ):
                raise ValueError("duplicate or not yet registered context version")
            stream_id = snapshot.stream.stream_id
            previous = heads.get(stream_id)
            if previous is None:
                if version.revision_sequence or version.predecessor is not None:
                    raise ValueError(
                        "context requires the complete exact predecessor chain"
                    )
                if snapshot.stream.internal_match_id in matches:
                    raise ValueError("ambiguous versions for one canonical match")
                matches[snapshot.stream.internal_match_id] = stream_id
            else:
                if (
                    version.predecessor != previous.reference
                    or version.revision_sequence != previous.revision_sequence + 1
                    or version.base_admission != previous.base_admission
                    or version.base_binding != previous.base_binding
                    or snapshot.stream != previous.snapshot.stream
                    or version.registered_at_utc < previous.registered_at_utc
                ):
                    raise ValueError(
                        "context has an incomplete predecessor chain or fork"
                    )
                assert_correction_chronology(previous.snapshot, snapshot)
                result = version.normalized_result
                if (
                    result is not None
                    and result.supersedes_match_result_id
                    != previous.latest_match_result_id
                    or version.latest_match_result_id
                    != (
                        result.match_result_id
                        if result
                        else previous.latest_match_result_id
                    )
                ):
                    raise ValueError("version normalized predecessor lineage mismatch")
                changed = set(changed_components(previous.snapshot, snapshot))
                for old, new in zip(
                    previous.components, version.components, strict=True
                ):
                    if old.reference == new.reference:
                        if old != new or old.component in changed:
                            raise ValueError(
                                "changed snapshot requires a new component reference"
                            )
                    else:
                        if new.source_available_at_utc < old.source_available_at_utc:
                            raise ValueError(
                                "component source chronology moved backwards"
                            )
                        expected_events.append((version, previous, old, new))
            key = (snapshot.stream.provider_code, snapshot.provider_result_key)
            if logical_keys.setdefault(key, stream_id) != stream_id:
                raise ValueError(
                    "provider logical result key belongs to multiple match streams"
                )
            seen[version.version_id] = version
            heads[stream_id] = version
        if len(expected_events) != len(self.corrections):
            raise ValueError(
                "context requires exact complete correction component events"
            )
        for event, (version, previous, old, new) in zip(
            self.corrections, expected_events, strict=True
        ):
            if (
                event.transition != version.reference
                or event.stream != version.snapshot.stream
                or event.component != old.component
                or event.predecessor != old.reference
                or event.successor != new.reference
                or event.predecessor_version != previous.reference
                or event.successor_version != version.reference
                or event.revision_sequence != version.revision_sequence
                or event.predecessor_source_available_at_utc
                != old.source_available_at_utc
                or event.source_available_at_utc != new.source_available_at_utc
                or event.registered_at_utc != version.registered_at_utc
                or not event.source_available_at_utc
                <= event.local_imported_at_utc
                <= event.registered_at_utc
            ):
                raise ValueError(
                    "context correction event scope/lineage/clocks mismatch"
                )
        if self.base_artifacts or self.correction_artifacts:
            self._assert_snapshot_links()
        return self

    def _assert_snapshot_links(self) -> None:
        """Authenticate covered projection fields against full, hash-sealed artifacts.

        In particular, a correction reference hashes the admission, NOT a partial
        TrainingFactVersionV2. Raw alias metadata still needs repository evidence.
        """
        bases = {a.training_fact_admission_id: a for a in self.base_artifacts}
        corrections = {a.artifact_id: a for a in self.correction_artifacts}
        if (
            len(bases) != len(self.base_artifacts)
            or len(corrections) != len(self.correction_artifacts)
            or set(bases) != {v.base_admission.artifact_id for v in self.versions}
            or set(corrections)
            != {v.version_id for v in self.versions if v.revision_sequence}
        ):
            raise ValueError(
                "selection requires complete authoritative snapshot artifact links"
            )
        for version in self.versions:
            parent = bases[version.base_admission.artifact_id]
            if (parent.schema_version, parent.admission_hash) != (
                version.base_admission.schema_version,
                version.base_admission.content_hash,
            ):
                raise ValueError("base admission artifact reference mismatch")
            fact = next(
                (
                    f
                    for f in parent.facts
                    if f.training_fact_binding_id == version.base_binding.artifact_id
                ),
                None,
            )
            if fact is None or (fact.schema_version, fact.fact_hash) != (
                version.base_binding.schema_version,
                version.base_binding.content_hash,
            ):
                raise ValueError("base fact artifact reference mismatch")
            if not version.revision_sequence:
                digest = tagged_canonical_sha256(
                    "TRAINING_BASE_FACT_VERSION_V2",
                    {
                        "admission_id": parent.training_fact_admission_id,
                        "admission_hash": parent.admission_hash,
                        "binding_id": fact.training_fact_binding_id,
                        "fact_hash": fact.fact_hash,
                    },
                )
                if version.reference != CorrectionRefV2(
                    schema_version="TRAINING_BASE_FACT_VERSION_V2",
                    artifact_id=stable_id("TRAINING_BASE_FACT_VERSION_V2", digest),
                    content_hash=digest,
                ):
                    raise ValueError("base snapshot reference linkage mismatch")
                binding = fact.content_payload
                season, result = (
                    binding.season_membership.content_payload,
                    binding.match_result_admission.content_payload,
                )
                snapshot = version.snapshot
                if (
                    snapshot.identity != binding.canonical_identity
                    or snapshot.provider_mapping != binding.provider_mapping
                    or version.normalized_result != binding.normalized_result
                    or version.registered_at_utc
                    != parent.content_payload.persisted_at_utc
                    or snapshot.stream.source_id != binding.fixture_source.source_id
                    or (
                        snapshot.provider_competition_id,
                        snapshot.provider_season_id,
                        snapshot.season_mapping_version,
                        snapshot.mapping_policy_version,
                        snapshot.provider_raw_status,
                        snapshot.provider_status_category,
                        snapshot.status_mapping_version,
                        snapshot.provider_finalized_at_utc,
                        snapshot.fixture_source_available_at_utc,
                        snapshot.mapping_source_available_at_utc,
                    )
                    != (
                        season.provider_competition_id,
                        season.provider_season_id,
                        season.season_mapping_version,
                        season.mapping_policy_version,
                        result.provider_raw_status,
                        result.provider_status_category,
                        result.status_mapping_version,
                        result.provider_finalized_at_utc,
                        binding.fixture_source.source_available_at_utc,
                        season.source_available_at_utc,
                    )
                    or version.components
                    != base_component_bindings(version.base_binding, fact)
                ):
                    raise ValueError(
                        "base snapshot differs from its authoritative fact artifact"
                    )
            else:
                artifact = corrections[version.version_id]
                content = artifact.content_payload
                if (
                    artifact.reference() != version.reference
                    or content.intent.predecessor != version.predecessor
                    or content.intent.revision_sequence != version.revision_sequence
                    or content.intent.candidate != version.snapshot
                    or content.components != version.components
                    or content.normalized_result != version.normalized_result
                    or content.registered_at_utc != version.registered_at_utc
                    or version.latest_match_result_id
                    != (
                        content.normalized_result.match_result_id
                        if content.normalized_result
                        else content.previous_match_result_id
                    )
                ):
                    raise ValueError(
                        "corrected snapshot differs from its authoritative admission artifact"
                    )

    def select(
        self, *, source_cutoffs: dict[str, datetime]
    ) -> tuple[TrainingFactVersionV2, ...]:
        """Select entire versions, including nontrainable heads, not mixed components.

        Callers must filter trainable AFTER selection; otherwise withdrawal would
        silently resurrect the previous score. No unpinned version is introduced.
        """
        from football_system.domain.common import normalize_utc

        validated = type(self).model_validate(self)
        validated._assert_snapshot_links()
        selected = {}
        for version in validated.versions:
            snapshot = version.snapshot
            source = snapshot.stream.source_id
            if source not in source_cutoffs:
                raise ValueError("missing explicit per-source cutoff")
            cutoff = normalize_utc(source_cutoffs[source])
            if (
                version.registered_at_utc <= validated.actual_at_utc
                and max(
                    snapshot.effective_source_available_at_utc,
                    snapshot.provider_mapping.available_at_utc,
                )
                <= cutoff
            ):
                key = snapshot.stream.stream_id
                previous = selected.get(key)
                if (
                    previous is None
                    or previous.revision_sequence < version.revision_sequence
                ):
                    selected[key] = version
        return tuple(selected[key] for key in sorted(selected))


def changed_components(
    previous: CorrectionSnapshotV2, candidate: CorrectionSnapshotV2
) -> tuple[CorrectionComponent, ...]:
    return tuple(
        c
        for c in CorrectionComponent
        if previous.component_value(c) != candidate.component_value(c)
    )


def assert_correction_chronology(
    previous: CorrectionSnapshotV2, candidate: CorrectionSnapshotV2
) -> None:
    for field in (
        "fixture_source_available_at_utc",
        "mapping_source_available_at_utc",
        "result_source_available_at_utc",
        "source_observed_at_utc",
    ):
        if getattr(candidate, field) < getattr(previous, field):
            raise ValueError(
                "correction source chronology precedes the actual predecessor"
            )
    order, old_order = (
        candidate.provider_revision_order,
        previous.provider_revision_order,
    )
    if old_order is not None and (order is None or order <= old_order):
        raise ValueError(
            "explicit provider revision order must advance and cannot disappear"
        )
    if (
        candidate.result_source_available_at_utc
        == previous.result_source_available_at_utc
    ):
        if order is None or order <= (old_order if old_order is not None else 0):
            raise ValueError(
                "equal source time requires explicit revision ID and order"
            )


def source_version_reference(fact: TrainingFactBindingV1) -> CorrectionRefV2:
    """A source version excludes only admission-local ordering, not provenance.

    Verify the complete V1 seal before projecting. The original binding ID/hash
    remain audit pins; every nested source/hash/status/review field is retained.
    """
    fact = TrainingFactBindingV1.model_validate(fact.model_dump(mode="python"))
    digest = tagged_canonical_sha256(
        "TRAINING_SOURCE_FACT_VERSION_V2",
        fact.content_payload.model_dump(mode="python", exclude={"sequence"}),
    )
    return CorrectionRefV2(
        schema_version="TRAINING_SOURCE_FACT_VERSION_V2",
        artifact_id=stable_id("TRAINING_SOURCE_FACT_VERSION_V2", digest),
        content_hash=digest,
    )


def base_component_bindings(
    base_binding: CorrectionRefV2, fact: TrainingFactBindingV1
) -> tuple[CorrectionComponentBindingV2, ...]:
    """Batch-independent components, derived from sealed content, not caller refs."""
    fact = TrainingFactBindingV1.model_validate(fact.model_dump(mode="python"))
    if base_binding != CorrectionRefV2(
        schema_version=fact.schema_version,
        artifact_id=fact.training_fact_binding_id,
        content_hash=fact.fact_hash,
    ):
        raise ValueError("component source does not match its exact V1 binding pin")
    source_version = source_version_reference(fact)
    binding = fact.content_payload
    result_at = binding.match_result_admission.content_payload.source_available_at_utc
    mapping_at = binding.season_membership.content_payload.source_available_at_utc
    objects = (
        binding.fixture_source,
        binding.provider_mapping,
        binding.season_membership,
        binding.match_result_admission,
        binding.match_result_admission,
    )
    times = (
        binding.fixture_source.source_available_at_utc,
        mapping_at,
        mapping_at,
        result_at,
        result_at,
    )
    components = []
    for kind, artifact, at in zip(CorrectionComponent, objects, times, strict=True):
        digest = tagged_canonical_sha256(
            "TRAINING_BASE_COMPONENT_REF_V2",
            {
                "source_version": source_version,
                "component": kind,
                "stored_artifact": artifact,
            },
        )
        components.append(
            CorrectionComponentBindingV2(
                component=kind,
                reference=CorrectionRefV2(
                    schema_version="TRAINING_BASE_COMPONENT_REF_V2",
                    artifact_id=stable_id("TRAINING_BASE_COMPONENT_REF_V2", digest),
                    content_hash=digest,
                ),
                source_available_at_utc=at,
            )
        )
    return tuple(components)
