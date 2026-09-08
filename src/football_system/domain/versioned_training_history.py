"""Explicit backward-only correction pins and whole-version Elo selection.

These projections do not confer persistence or rights. The repository must reload
the exact context, including predecessors, and check the complete current heads.
No V1 admission is manufactured and no normalized score hash is reinterpreted.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier, normalize_utc
from football_system.domain.services.elo_baseline import EloRegularTimeResult
from football_system.domain.training_admission import tagged_canonical_sha256
from football_system.domain.training_correction import (
    CorrectionComponent,
    CorrectionRefV2,
    TrainingCorrectionContextV2,
    TrainingFactVersionV2,
)


class TrainingHistoryContextPinV2(DomainModel):
    schema_version: Literal["TRAINING_HISTORY_CONTEXT_PIN_V2"] = (
        "TRAINING_HISTORY_CONTEXT_PIN_V2"
    )
    base_admissions: tuple[CorrectionRefV2, ...] = Field(min_length=1)
    corrections: tuple[CorrectionRefV2, ...]

    @property
    def base_root(self) -> str:
        return tagged_canonical_sha256(
            "TRAINING_HISTORY_BASE_ROOT_V2", self.base_admissions
        )

    @property
    def context_root(self) -> str:
        return tagged_canonical_sha256(self.schema_version, self)

    @classmethod
    def of(cls, context: TrainingCorrectionContextV2) -> Self:
        return cls(
            base_admissions=tuple(
                sorted(
                    {v.base_admission for v in context.versions},
                    key=lambda r: r.artifact_id,
                )
            ),
            corrections=tuple(
                sorted(
                    (v.reference for v in context.versions if v.revision_sequence),
                    key=lambda r: r.artifact_id,
                )
            ),
        )

    @model_validator(mode="after")
    def validate_pins(self) -> Self:
        for refs, schema in (
            (self.base_admissions, "TRAINING_FACT_ADMISSION_V1"),
            (self.corrections, "TRAINING_CORRECTION_ADMISSION_V2"),
        ):
            ids = tuple(r.artifact_id for r in refs)
            if ids != tuple(sorted(set(ids))) or any(
                r.schema_version != schema for r in refs
            ):
                raise ValueError("context requires ordered unique typed admission pins")
        return self


class VersionedFactRefV2(DomainModel):
    schema_version: Literal["VERSIONED_FACT_REF_V2"] = "VERSIONED_FACT_REF_V2"
    version: CorrectionRefV2
    base_admission: CorrectionRefV2
    base_binding: CorrectionRefV2
    match_id: Identifier
    match_result_id: Identifier | None

    @model_validator(mode="after")
    def typed_lineage(self) -> Self:
        if self.version.schema_version not in {
            "TRAINING_BASE_FACT_VERSION_V2",
            "TRAINING_CORRECTION_ADMISSION_V2",
        } or (self.base_admission.schema_version, self.base_binding.schema_version) != (
            "TRAINING_FACT_ADMISSION_V1",
            "TRAINING_FACT_BINDING_V1",
        ):
            raise ValueError(
                "version reference requires typed correction and original binding lineage"
            )
        return self

    @classmethod
    def of(cls, value: TrainingFactVersionV2) -> Self:
        return cls(
            version=value.reference,
            base_admission=value.base_admission,
            base_binding=value.base_binding,
            match_id=value.snapshot.stream.internal_match_id,
            match_result_id=None
            if value.normalized_result is None
            else value.normalized_result.match_result_id,
        )


def versioned_selection_root(refs) -> str:
    return tagged_canonical_sha256("SELECTED_TRAINING_VERSIONS_ROOT_V2", tuple(refs))


def validate_versioned_context(
    context: TrainingCorrectionContextV2,
) -> TrainingCorrectionContextV2:
    context = TrainingCorrectionContextV2.model_validate(
        context.model_dump(mode="python")
    )
    seen, streams, matches, ids = {}, {}, {}, set()
    for version in context.versions:
        s = version.snapshot
        if (
            version.version_id in ids
            or version.registered_at_utc > context.actual_at_utc
        ):
            raise ValueError("duplicate or not yet registered context version")
        VersionedFactRefV2.of(version)
        if version.reference.schema_version != (
            "TRAINING_CORRECTION_ADMISSION_V2"
            if version.revision_sequence
            else "TRAINING_BASE_FACT_VERSION_V2"
        ):
            raise ValueError(
                "context requires correctly typed base and correction versions"
            )
        if (
            max(
                s.effective_source_available_at_utc, s.provider_mapping.available_at_utc
            )
            > version.registered_at_utc
        ):
            raise ValueError(
                "version source publication cannot follow actual registration"
            )
        if tuple(c.component for c in version.components) != tuple(CorrectionComponent):
            raise ValueError("version requires all five exact component bindings")
        if s.trainable != (version.normalized_result is not None):
            raise ValueError("withdrawn version must not fabricate a normalized result")
        result = version.normalized_result
        if result is not None and (
            (
                result.match_id,
                result.provider_code,
                result.home_goals,
                result.away_goals,
                result.available_at_utc,
                result.ingested_at_utc,
                result.observed_at_utc,
                result.source_result_key,
            )
            != (
                s.stream.internal_match_id,
                s.stream.provider_code,
                s.home_goals,
                s.away_goals,
                s.result_source_available_at_utc,
                s.result_source_available_at_utc,
                s.source_observed_at_utc,
                s.provider_result_key,
            )
        ):
            raise ValueError("version normalized result/snapshot mismatch")
        previous = streams.get(s.stream.stream_id)
        if previous is None:
            if version.revision_sequence != 0 or version.predecessor is not None:
                raise ValueError(
                    "context requires the complete exact predecessor chain"
                )
            if s.stream.internal_match_id in matches:
                raise ValueError("ambiguous versions for one canonical match")
            if (
                result is None
                or result.supersedes_match_result_id is not None
                or version.latest_match_result_id != result.match_result_id
            ):
                raise ValueError(
                    "base version must preserve its original normalized result"
                )
            matches[s.stream.internal_match_id] = s.stream.stream_id
        elif (
            version.predecessor != previous.reference
            or version.revision_sequence != previous.revision_sequence + 1
            or version.base_admission != previous.base_admission
            or version.base_binding != previous.base_binding
            or s.stream != previous.snapshot.stream
            or version.registered_at_utc < previous.registered_at_utc
            or any(
                getattr(s, key) < getattr(previous.snapshot, key)
                for key in (
                    "fixture_source_available_at_utc",
                    "mapping_source_available_at_utc",
                    "result_source_available_at_utc",
                    "source_observed_at_utc",
                )
            )
        ):
            raise ValueError("context has an incomplete predecessor chain or fork")
        if previous is not None and (
            (
                result is not None
                and result.supersedes_match_result_id != previous.latest_match_result_id
            )
            or version.latest_match_result_id
            != (
                result.match_result_id
                if result is not None
                else previous.latest_match_result_id
            )
        ):
            raise ValueError("version normalized predecessor lineage mismatch")
        if previous is not None and (
            s.result_source_available_at_utc
            == previous.snapshot.result_source_available_at_utc
            or (
                s.provider_revision_order is not None
                and previous.snapshot.provider_revision_order is not None
            )
        ):
            if s.provider_revision_order is None or s.provider_revision_order <= (
                previous.snapshot.provider_revision_order or 0
            ):
                raise ValueError(
                    "versions require verified advancing explicit revision order"
                )
        ids.add(version.version_id)
        seen[version.reference] = version
        streams[s.stream.stream_id] = version
    expected = []
    for version in context.versions:
        if version.predecessor is None:
            continue
        previous = seen[version.predecessor]
        for old, new in zip(previous.components, version.components, strict=True):
            if old != new:
                expected.append(
                    (version.reference, old.component, old.reference, new.reference)
                )
    actual = [
        (e.transition, e.component, e.predecessor, e.successor)
        for e in context.corrections
    ]
    if actual != expected:
        raise ValueError("context requires exact complete correction component events")
    for event in context.corrections:
        version = seen[event.transition]
        previous = seen[version.predecessor]
        old = next(c for c in previous.components if c.component == event.component)
        new = next(c for c in version.components if c.component == event.component)
        if (
            event.stream != version.snapshot.stream
            or event.predecessor_version != previous.reference
            or event.successor_version != version.reference
            or event.revision_sequence != version.revision_sequence
            or event.predecessor_source_available_at_utc != old.source_available_at_utc
            or event.source_available_at_utc != new.source_available_at_utc
            or event.registered_at_utc != version.registered_at_utc
            or not event.source_available_at_utc
            <= event.local_imported_at_utc
            <= event.registered_at_utc
        ):
            raise ValueError("context correction event scope/lineage/clocks mismatch")
    return context


def select_versioned_training_heads(
    context: TrainingCorrectionContextV2,
    *,
    source_cutoffs: dict[str, datetime],
    strict_cutoff: bool = True,
) -> tuple[TrainingFactVersionV2, ...]:
    context = validate_versioned_context(context)
    if type(strict_cutoff) is not bool:
        raise ValueError("strict_cutoff must be an explicit boolean")
    cutoffs = {key: normalize_utc(value) for key, value in source_cutoffs.items()}
    selected = {}
    for version in context.versions:
        s = version.snapshot
        if s.stream.source_id not in cutoffs:
            raise ValueError("missing explicit per-source cutoff")
        cutoff = cutoffs[s.stream.source_id]
        if cutoff > context.actual_at_utc:
            raise ValueError("source cutoff cannot follow actual context verification")
        available = max(
            s.effective_source_available_at_utc, s.provider_mapping.available_at_utc
        )
        if available < cutoff if strict_cutoff else available <= cutoff:
            selected[s.stream.stream_id] = version
    return tuple(selected[key] for key in sorted(selected))


def project_versioned_training_fact(
    version: TrainingFactVersionV2,
) -> EloRegularTimeResult:
    result, identity = version.normalized_result, version.snapshot.identity
    if result is None or not version.snapshot.trainable:
        raise ValueError("nontrainable selected head has no Elo result")
    return EloRegularTimeResult(
        **result.model_dump(
            exclude={
                "schema_version",
                "provider_code",
                "observed_at_utc",
                "source_result_key",
            }
        ),
        season_id=identity.season,
        home_team_id=identity.internal_home_team_id,
        away_team_id=identity.internal_away_team_id,
        kickoff_at_utc=identity.kickoff_at_utc,
    )


def select_versioned_training_facts(
    context: TrainingCorrectionContextV2,
    competition_id: str,
    ordered_season_ids,
    cutoff_at_utc: datetime,
    exclude_match_ids,
    strict_cutoff: bool = True,
) -> tuple[TrainingFactVersionV2, ...]:
    seasons = tuple(ordered_season_ids)
    if not seasons or len(set(seasons)) != len(seasons):
        raise ValueError("ordered seasons must be explicit and unique")
    heads = select_versioned_training_heads(
        context,
        source_cutoffs={
            v.snapshot.stream.source_id: cutoff_at_utc for v in context.versions
        },
        strict_cutoff=strict_cutoff,
    )
    excluded = set(exclude_match_ids)
    facts = [
        v
        for v in heads
        if v.snapshot.trainable
        and v.snapshot.identity.internal_competition_id == competition_id
        and v.snapshot.identity.season in seasons
        and v.snapshot.stream.internal_match_id not in excluded
    ]
    facts.sort(
        key=lambda v: (
            v.snapshot.identity.kickoff_at_utc,
            v.normalized_result.available_at_utc,
            v.normalized_result.ingested_at_utc,
            v.snapshot.stream.internal_match_id,
            v.normalized_result.match_result_id,
        )
    )
    order = tuple(seasons.index(v.snapshot.identity.season) for v in facts)
    if order != tuple(sorted(order)):
        raise ValueError(
            "selected version seasons must form ordered chronological blocks"
        )
    return tuple(facts)
