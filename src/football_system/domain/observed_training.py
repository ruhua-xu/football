"""Current local observations, not claims about historical provider knowledge.

Seals are structural integrity, not proof of persistence or permission. Only the
repository verifies local bytes, registered identities and genuine review files.
No historical mode, V1/V2 artifact, or production approval is extended here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from football_system.domain.archive import (
    HistoricalDataMode,
    match_result_payload_sha256,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    normalize_utc,
    stable_id,
)
from football_system.domain.services.elo_baseline import EloRegularTimeResult
from football_system.domain.settlement import MatchResult
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    LocalReviewerAttestationV1,
    Sha256Digest,
    TrainingCanonicalMatchIdentityV1,
    TrainingProviderMatchMappingV1,
    tagged_canonical_sha256,
)

CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1 = "CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1"
OBSERVED_SNAPSHOT_SUBJECT_V1 = "OBSERVED_SNAPSHOT_SUBJECT_V1"


def _plain(value):
    if isinstance(value, BaseModel):
        # Preserve even unvalidated model_copy extras and missing required fields.
        return _plain({**vars(value), **(value.__pydantic_extra__ or {})})
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    return value


class _ObservedModel(DomainModel):
    """Copies are not proof: every validation boundary also validates old-model children."""

    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def validate_copies(cls, value):
        return _plain(value)


class _RetrospectiveObservedModel(_ObservedModel):
    """Use provenance, not publication proof or a grant of source rights/classification."""

    source_data_mode: Literal[HistoricalDataMode.SOURCE_TIME_RESEARCH]
    retrospective: Literal[True]

    @field_validator("retrospective", mode="before")
    @classmethod
    def require_retrospective(cls, value):
        if value is not True:
            raise ValueError("observed provenance requires retrospective=true")
        return value


class EvidenceBasis(StrEnum):
    CURRENT_SNAPSHOT_OBSERVED = "CURRENT_SNAPSHOT_OBSERVED"
    VERIFIED_HISTORICAL_SOURCE_TIME = "VERIFIED_HISTORICAL_SOURCE_TIME"


class ObservedScopeExceptionV1(_ObservedModel):
    provider_fixture_key: Identifier
    reason: str = Field(min_length=1, max_length=2048)


class ObservedScopeSeasonV1(_ObservedModel):
    provider_season_id: str = Field(pattern=r"^[1-9][0-9]*$")
    canonical_season_id: Identifier
    expected_fixture_ids: tuple[Identifier, ...] = Field(min_length=1)
    expected_fixture_count: int = Field(ge=1, strict=True)
    exceptions: tuple[ObservedScopeExceptionV1, ...]

    @model_validator(mode="after")
    def cohort(self) -> Self:
        ids = self.expected_fixture_ids
        exceptions = tuple(x.provider_fixture_key for x in self.exceptions)
        if len(set(ids)) != len(ids) or len(ids) != self.expected_fixture_count:
            raise ValueError("scope requires exact unique expected cohort IDs/count")
        if len(set(exceptions)) != len(exceptions) or not set(exceptions) < set(ids):
            raise ValueError(
                "scope exceptions must be unique and leave a nonempty cohort"
            )
        return self

    @property
    def included_fixture_ids(self) -> tuple[str, ...]:
        excluded = {x.provider_fixture_key for x in self.exceptions}
        return tuple(x for x in self.expected_fixture_ids if x not in excluded)


class CurrentSnapshotCollectionScopeV1(_RetrospectiveObservedModel):
    """Time-free review subject. Limits count local receipts and admitted versions.

    This software lane makes no HTTP requests. Original HTTP metadata, if any,
    remains separate evidence and does not establish trusted operation times.
    Actual HTTP sends belong to a separate acquisition controller. Its DRAFT 20
    request budget has not been executed; receipt counts do not enforce it.
    """

    schema_version: Literal["CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1"] = (
        CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1
    )
    evidence_basis: Literal[EvidenceBasis.CURRENT_SNAPSHOT_OBSERVED] = (
        EvidenceBasis.CURRENT_SNAPSHOT_OBSERVED
    )
    source_rights_admission_id: Identifier
    source_rights_admission_hash: Sha256Digest
    source_id: Identifier
    provider_code: Literal["SPORTMONKS"] = "SPORTMONKS"
    competition: Literal["BUNDESLIGA"] = "BUNDESLIGA"
    provider_competition_id: str = Field(pattern=r"^[1-9][0-9]*$")
    canonical_competition_id: Identifier
    seasons: tuple[ObservedScopeSeasonV1, ...] = Field(min_length=1)
    max_capture_receipts: int = Field(ge=1, strict=True)
    max_snapshot_records: int = Field(ge=1, strict=True)
    permitted_uses: tuple[Literal["TRAINING", "VALIDATION"], ...] = Field(min_length=1)
    retention_deadline_utc: UtcDateTime
    user_terms_resolution: LocalReviewEvidenceV1

    @property
    def subject_hash(self) -> str:
        value = type(self).model_validate(self)
        return tagged_canonical_sha256(value.schema_version, value)

    @property
    def cohort_ids(self) -> tuple[str, ...]:
        return tuple(
            key for season in self.seasons for key in season.included_fixture_ids
        )

    @model_validator(mode="after")
    def scope(self) -> Self:
        provider = [x.provider_season_id for x in self.seasons]
        canonical = [x.canonical_season_id for x in self.seasons]
        all_ids = [
            key for season in self.seasons for key in season.expected_fixture_ids
        ]
        if len(set(provider)) != len(provider) or len(set(canonical)) != len(canonical):
            raise ValueError(
                "scope seasons must be unique in declared chronological order"
            )
        if len(set(all_ids)) != len(all_ids):
            raise ValueError(
                "scope requires nonconflicting fixture cohorts across ordered seasons"
            )
        if len(self.cohort_ids) > self.max_snapshot_records:
            raise ValueError("scope record limit cannot cover its expected cohort")
        if len(set(self.permitted_uses)) != len(self.permitted_uses):
            raise ValueError("scope uses must be unique")
        return self


def _attests(attestation, schema, digest):
    content = attestation.content_payload
    if (content.attested_schema_version, content.attested_payload_hash) != (
        schema,
        digest,
    ):
        raise ValueError(
            "new genuine review must bind the exact observed subject schema/hash"
        )


class ObservedCollectionScopeAdmissionV1(_ObservedModel):
    schema_version: Literal["OBSERVED_COLLECTION_SCOPE_ADMISSION_V1"] = (
        "OBSERVED_COLLECTION_SCOPE_ADMISSION_V1"
    )
    scope_id: Identifier
    content_hash: Sha256Digest
    subject: CurrentSnapshotCollectionScopeV1
    reviewer_attestation: LocalReviewerAttestationV1
    recorded_at_utc: UtcDateTime
    # Stable capture-ledger ordinal, not an implicit SQLite rowid or HTTP counter.
    capture_receipt_high_watermark: int = Field(ge=0, strict=True)

    @classmethod
    def freeze(cls, **values) -> Self:
        values = _plain(values)
        digest = tagged_canonical_sha256(
            cls.model_fields["schema_version"].default, values
        )
        return cls(
            scope_id=stable_id(cls.model_fields["schema_version"].default, digest),
            content_hash=digest,
            **values,
        )

    @model_validator(mode="after")
    def seal(self) -> Self:
        _attests(
            self.reviewer_attestation,
            self.subject.schema_version,
            self.subject.subject_hash,
        )
        if (
            not self.reviewer_attestation.content_payload.reviewed_at_utc
            <= self.recorded_at_utc
            < self.subject.retention_deadline_utc
        ):
            raise ValueError("scope review/recording/retention timeline mismatch")
        digest = tagged_canonical_sha256(
            self.schema_version,
            self.model_dump(
                mode="python", exclude={"schema_version", "scope_id", "content_hash"}
            ),
        )
        if self.content_hash != digest or self.scope_id != stable_id(
            self.schema_version, digest
        ):
            raise ValueError("observed scope seal mismatch")
        return self


class ObservedStreamV1(_ObservedModel):
    source_id: Identifier
    provider_code: Identifier
    provider_fixture_namespace: Identifier
    provider_fixture_key: Identifier
    internal_match_id: Identifier

    @property
    def stream_id(self) -> str:
        return stable_id(
            "OBSERVED_STREAM_V1",
            tagged_canonical_sha256(
                "OBSERVED_STREAM_V1", type(self).model_validate(self)
            ),
        )


class ObservedCapturePointerV1(_ObservedModel):
    capture_receipt_id: Identifier
    record_pointer: str = Field(
        max_length=2048, pattern=r"^/data(?:/(?:0|[1-9][0-9]*))?$"
    )


class ObservedSnapshotInputV1(_ObservedModel):
    fixture: ObservedCapturePointerV1
    season: ObservedCapturePointerV1
    result: ObservedCapturePointerV1
    provider_mapping_id: Identifier
    home_team_alias_id: Identifier
    away_team_alias_id: Identifier
    competition_mapping_id: Identifier
    original_http_metadata: LocalReviewEvidenceV1 | None = None


class ObservedCaptureEvidenceV1(ObservedCapturePointerV1):
    receipt_hash: Sha256Digest
    payload_sha256: Sha256Digest
    record_sha256: Sha256Digest
    outcome_sha256: Sha256Digest
    capture_ordinal: int = Field(ge=1, strict=True)
    capture_kind: Literal["LOCAL_FILE_IMPORT"] = "LOCAL_FILE_IMPORT"
    capture_observed_at_utc: UtcDateTime
    capture_registered_at_utc: UtcDateTime
    capture_record_count: int = Field(ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def capture(self) -> Self:
        if self.capture_observed_at_utc > self.capture_registered_at_utc:
            raise ValueError("observed capture timeline mismatch")
        if (self.record_pointer == "/data" and self.capture_record_count != 1) or (
            self.record_pointer != "/data"
            and int(self.record_pointer.rsplit("/", 1)[1]) >= self.capture_record_count
        ):
            raise ValueError(
                "observed record pointer is outside its original capture count"
            )
        return self


class ObservedFixtureV1(_ObservedModel):
    """Native FT assessment only; subject.trainable also requires resolved identity."""

    provider_fixture_key: Identifier
    provider_competition_id: Identifier
    provider_season_id: Identifier
    provider_home_team_id: Identifier
    provider_away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    provider_raw_status: Identifier | None
    trainable: bool = Field(strict=True)
    regular_time_home_goals: int | None = Field(ge=0, strict=True)
    regular_time_away_goals: int | None = Field(ge=0, strict=True)
    sporting_period_end_at_utc: UtcDateTime | None
    provider_publication_at_utc: None
    provider_finalized_at_utc: None
    provider_version_id: None
    field_evidence: dict[str, JsonValue]
    diagnostics: tuple[JsonValue, ...]

    @property
    def outcome_sha256(self) -> str:
        value = type(self).model_validate(self)
        return tagged_canonical_sha256(
            "OBSERVED_NATIVE_OUTCOME_V1",
            value.model_dump(
                mode="python",
                include={
                    "provider_raw_status",
                    "trainable",
                    "regular_time_home_goals",
                    "regular_time_away_goals",
                },
            ),
        )

    @model_validator(mode="after")
    def result(self) -> Self:
        if self.provider_home_team_id == self.provider_away_team_id:
            raise ValueError("observed provider teams must differ")
        scores = (self.regular_time_home_goals, self.regular_time_away_goals)
        if self.trainable and (
            self.provider_raw_status != "FT" or None in scores or self.diagnostics
        ):
            raise ValueError(
                "trainable observation requires explicit terminal FT scores"
            )
        if not self.trainable and scores != (None, None):
            raise ValueError(
                "withdrawn observation must not fabricate normalized scores"
            )
        return self


class ObservedIdentitySourceRefV1(_ObservedModel):
    record_id: Identifier
    record_sha256: Sha256Digest


class ObservedIdentityEvidenceV1(_ObservedModel):
    """Exact catalog selection at admission, not caller proof of its completeness."""

    match_mappings: tuple[ObservedIdentitySourceRefV1, ...] = Field(min_length=1)
    team_aliases: tuple[ObservedIdentitySourceRefV1, ...] = Field(min_length=1)
    competition_mappings: tuple[ObservedIdentitySourceRefV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def source_rows(self) -> Self:
        for name in type(self).model_fields:
            ids = [x.record_id for x in getattr(self, name)]
            if ids != sorted(set(ids)):
                raise ValueError(
                    "observed identity evidence requires sorted unique source rows"
                )
        return self


class ObservedSnapshotSubjectV1(_RetrospectiveObservedModel):
    """Keep the immutable canonical anchor beside the unmodified native findings.

    Resolved team IDs are actual alias targets, or explicit None when a successor
    cannot resolve them. Identity contradictions withdraw the head; they never
    overwrite the native FT finding, original anchor, or earlier normalized result.
    """

    schema_version: Literal["OBSERVED_SNAPSHOT_SUBJECT_V1"] = (
        OBSERVED_SNAPSHOT_SUBJECT_V1
    )
    evidence_basis: Literal[EvidenceBasis.CURRENT_SNAPSHOT_OBSERVED] = (
        EvidenceBasis.CURRENT_SNAPSHOT_OBSERVED
    )
    scope_id: Identifier
    scope_hash: Sha256Digest
    source_rights_admission_id: Identifier
    source_rights_admission_hash: Sha256Digest
    stream: ObservedStreamV1
    identity: TrainingCanonicalMatchIdentityV1
    provider_mapping: TrainingProviderMatchMappingV1
    input: ObservedSnapshotInputV1
    identity_evidence: ObservedIdentityEvidenceV1
    fixture_capture: ObservedCaptureEvidenceV1
    season_capture: ObservedCaptureEvidenceV1
    result_capture: ObservedCaptureEvidenceV1
    inspection: ObservedFixtureV1
    resolved_home_team_id: Identifier | None
    resolved_away_team_id: Identifier | None
    upstream_publication_at_utc: None
    provider_finalized_at_utc: None
    revision_sequence: int = Field(ge=0, strict=True)
    predecessor_id: Identifier | None
    predecessor_hash: Sha256Digest | None
    previous_match_result_id: Identifier | None
    version_order_basis: Literal["LOCAL_CAPTURE_AND_ADMISSION_SEQUENCE"] = (
        "LOCAL_CAPTURE_AND_ADMISSION_SEQUENCE"
    )

    @property
    def subject_hash(self) -> str:
        value = type(self).model_validate(self)
        return tagged_canonical_sha256(value.schema_version, value)

    @property
    def identity_contradictions(self) -> tuple[str, ...]:
        reasons = []
        for side in ("home", "away"):
            resolved = getattr(self, f"resolved_{side}_team_id")
            if resolved is None:
                reasons.append(f"UNRESOLVED_{side.upper()}_TEAM")
            elif resolved != getattr(self.identity, f"internal_{side}_team_id"):
                reasons.append(f"{side.upper()}_TEAM_DIFFERS_FROM_CANONICAL_ANCHOR")
        if self.inspection.kickoff_at_utc != self.identity.kickoff_at_utc:
            reasons.append("KICKOFF_DIFFERS_FROM_CANONICAL_ANCHOR")
        return tuple(reasons)

    @property
    def trainable(self) -> bool:
        return self.inspection.trainable and not self.identity_contradictions

    @property
    def capture_observed_at_utc(self) -> datetime:
        """Effective all-role cutoff gate, not the result-role observation time."""
        return max(
            x.capture_observed_at_utc
            for x in (self.fixture_capture, self.season_capture, self.result_capture)
        )

    @model_validator(mode="after")
    def bindings(self) -> Self:
        s, m, i, raw = (
            self.stream,
            self.provider_mapping,
            self.identity,
            self.inspection,
        )
        if (
            s.provider_code,
            s.provider_fixture_namespace,
            s.provider_fixture_key,
            s.internal_match_id,
        ) != (
            m.provider_code,
            m.external_namespace,
            m.external_match_id,
            m.internal_match_id,
        ):
            raise ValueError("observed stream mapping anchor mismatch")
        if (s.internal_match_id, s.provider_fixture_key) != (
            i.internal_match_id,
            raw.provider_fixture_key,
        ):
            raise ValueError("observed canonical fixture anchor mismatch")
        if i.competition_type not in {"LEAGUE", "DOMESTIC_LEAGUE"}:
            raise ValueError(
                "observed canonical classification must be a domestic league"
            )
        if not self.revision_sequence and self.identity_contradictions:
            raise ValueError("observed root requires a raw-verified canonical identity")
        if self.input.provider_mapping_id != m.mapping_id:
            raise ValueError(
                "observed input must reference its exact registered mapping"
            )
        for name, required in (
            ("match_mappings", {self.input.provider_mapping_id}),
            (
                "team_aliases",
                {self.input.home_team_alias_id, self.input.away_team_alias_id},
            ),
            ("competition_mappings", {self.input.competition_mapping_id}),
        ):
            if not required <= {
                x.record_id for x in getattr(self.identity_evidence, name)
            }:
                raise ValueError(
                    "observed identity evidence omits a referenced source row"
                )
        if bool(self.revision_sequence) != (self.predecessor_id is not None) or (
            self.predecessor_id is None
        ) != (self.predecessor_hash is None):
            raise ValueError(
                "observed local sequence requires exact predecessor reference"
            )
        if not self.revision_sequence and self.previous_match_result_id is not None:
            raise ValueError(
                "observed root cannot borrow a historical normalized predecessor"
            )
        captures, observations = {}, {}
        for role in ("fixture", "season", "result"):
            ref, evidence = getattr(self.input, role), getattr(self, f"{role}_capture")
            if (ref.capture_receipt_id, ref.record_pointer) != (
                evidence.capture_receipt_id,
                evidence.record_pointer,
            ) or evidence.capture_observed_at_utc > evidence.capture_registered_at_utc:
                raise ValueError("observed role capture pointer/timeline mismatch")
            metadata = evidence.model_dump(
                exclude={"record_pointer", "record_sha256", "outcome_sha256"}
            )
            if captures.setdefault(evidence.capture_receipt_id, metadata) != metadata:
                raise ValueError(
                    "same observed receipt cannot claim independent capture metadata"
                )
            pointer = (evidence.capture_receipt_id, evidence.record_pointer)
            if (
                observations.setdefault(pointer, evidence.record_sha256)
                != evidence.record_sha256
            ):
                raise ValueError(
                    "same observed source pointer cannot claim different record hashes"
                )
        if any(
            c.outcome_sha256 != raw.outcome_sha256
            for c in (self.fixture_capture, self.season_capture, self.result_capture)
        ):
            raise ValueError("observed full-fixture roles have contradictory outcomes")
        if (
            raw.trainable
            and raw.kickoff_at_utc >= self.result_capture.capture_observed_at_utc
        ):
            raise ValueError("terminal observation must follow kickoff")
        return self


class ObservedSnapshotSubmissionV1(_ObservedModel):
    subject: ObservedSnapshotSubjectV1
    reviewer_attestation: LocalReviewerAttestationV1

    @model_validator(mode="after")
    def review(self) -> Self:
        _attests(
            self.reviewer_attestation,
            self.subject.schema_version,
            self.subject.subject_hash,
        )
        if (
            max(
                x.capture_registered_at_utc
                for x in (
                    self.subject.fixture_capture,
                    self.subject.season_capture,
                    self.subject.result_capture,
                )
            )
            > self.reviewer_attestation.content_payload.reviewed_at_utc
        ):
            raise ValueError("snapshot review must follow registered captures")
        return self


class ObservedSnapshotRecordV1(ObservedSnapshotSubmissionV1):
    schema_version: Literal["OBSERVED_SNAPSHOT_RECORD_V1"] = (
        "OBSERVED_SNAPSHOT_RECORD_V1"
    )
    version_id: Identifier
    content_hash: Sha256Digest
    verified_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime
    normalized_result: MatchResult | None

    @classmethod
    def freeze(
        cls, *, subject, reviewer_attestation, verified_at_utc, registered_at_utc
    ) -> Self:
        submission = ObservedSnapshotSubmissionV1(
            subject=subject, reviewer_attestation=reviewer_attestation
        )
        subject, reviewer_attestation = (
            submission.subject,
            submission.reviewer_attestation,
        )
        values = dict(
            subject=subject,
            reviewer_attestation=reviewer_attestation,
            verified_at_utc=verified_at_utc,
            registered_at_utc=registered_at_utc,
        )
        tag = cls.model_fields["schema_version"].default
        digest = tagged_canonical_sha256(tag, values)
        version_id = stable_id(tag, digest)
        return cls(
            version_id=version_id,
            content_hash=digest,
            normalized_result=_normalized(subject, version_id, registered_at_utc),
            **values,
        )

    @model_validator(mode="after")
    def seal(self) -> Self:
        if (
            not self.reviewer_attestation.content_payload.reviewed_at_utc
            <= self.verified_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("observed verification/admission timeline mismatch")
        digest = tagged_canonical_sha256(
            self.schema_version,
            self.model_dump(
                mode="python",
                exclude={
                    "schema_version",
                    "version_id",
                    "content_hash",
                    "normalized_result",
                },
            ),
        )
        if digest != self.content_hash or self.version_id != stable_id(
            self.schema_version, digest
        ):
            raise ValueError("observed snapshot record seal mismatch")
        if self.normalized_result != _normalized(
            self.subject, self.version_id, self.registered_at_utc
        ):
            raise ValueError("basis-tagged observed normalized projection mismatch")
        return self

    @property
    def evidence_basis(self):
        return self.subject.evidence_basis

    @property
    def trainable(self) -> bool:
        return self.subject.trainable

    @property
    def stream(self):
        return self.subject.stream

    @property
    def identity(self):
        return self.subject.identity

    @property
    def capture_observed_at_utc(self):
        return self.subject.capture_observed_at_utc

    @property
    def revision_sequence(self):
        return self.subject.revision_sequence

    @property
    def predecessor_id(self):
        return self.subject.predecessor_id

    @property
    def source_rights_admission_id(self):
        return self.subject.source_rights_admission_id

    @property
    def source_rights_admission_hash(self):
        return self.subject.source_rights_admission_hash

    def to_elo_result(self) -> EloRegularTimeResult:
        value = ObservedSnapshotRecordV1.model_validate(self)
        result, identity = value.normalized_result, value.identity
        if result is None:
            raise ValueError("withdrawn observed head is not trainable")
        return EloRegularTimeResult(
            **result.model_dump(
                exclude={"provider_code", "observed_at_utc", "source_result_key"}
            ),
            season_id=identity.season,
            home_team_id=identity.internal_home_team_id,
            away_team_id=identity.internal_away_team_id,
            kickoff_at_utc=identity.kickoff_at_utc,
        )


def _normalized(subject, version_id, registered):
    raw = subject.inspection
    if not subject.trainable:
        return None
    return MatchResult(
        match_result_id=stable_id("OBSERVED_NORMALIZED_RESULT_V1", version_id),
        match_id=subject.stream.internal_match_id,
        provider_code=subject.stream.provider_code,
        home_goals=raw.regular_time_home_goals,
        away_goals=raw.regular_time_away_goals,
        observed_at_utc=subject.result_capture.capture_observed_at_utc,
        available_at_utc=registered,
        ingested_at_utc=registered,
        source_result_key=stable_id("OBSERVED_LOCAL_RESULT_KEY_V1", version_id),
        payload_hash=match_result_payload_sha256(
            raw.regular_time_home_goals, raw.regular_time_away_goals
        ),
        supersedes_match_result_id=subject.previous_match_result_id,
    )


def observed_snapshot_root(records) -> str:
    records = tuple(ObservedSnapshotRecordV1.model_validate(x) for x in records)
    if len({x.version_id for x in records}) != len(records):
        raise ValueError("observed root cannot contain duplicate versions")
    return tagged_canonical_sha256(
        "OBSERVED_SNAPSHOT_ROOT_V1",
        {
            "record_count": len(records),
            "records": tuple(
                {"version_id": x.version_id, "content_hash": x.content_hash}
                for x in records
            ),
        },
    )


class ObservedSnapshotAdmissionV1(_ObservedModel):
    schema_version: Literal["OBSERVED_SNAPSHOT_ADMISSION_V1"] = (
        "OBSERVED_SNAPSHOT_ADMISSION_V1"
    )
    admission_id: Identifier
    content_hash: Sha256Digest
    scope_id: Identifier
    scope_hash: Sha256Digest
    admission_sequence: int = Field(ge=0, strict=True)
    actual_started_at_utc: UtcDateTime
    verified_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime
    records: tuple[ObservedSnapshotRecordV1, ...] = Field(min_length=1)

    @classmethod
    def freeze(cls, **values) -> Self:
        values = _plain(values)
        tag = cls.model_fields["schema_version"].default
        digest = tagged_canonical_sha256(tag, values)
        return cls(admission_id=stable_id(tag, digest), content_hash=digest, **values)

    @model_validator(mode="after")
    def seal(self) -> Self:
        if (
            not self.actual_started_at_utc
            <= self.verified_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("observed admission clock moved backwards")
        keys = [x.stream.provider_fixture_key for x in self.records]
        matches = [x.stream.internal_match_id for x in self.records]
        if (
            len(set(keys)) != len(keys)
            or len(set(matches)) != len(matches)
            or keys != sorted(keys)
        ):
            raise ValueError(
                "duplicate/conflicting or unordered observed admission cohort"
            )
        for record in self.records:
            if (
                (
                    record.subject.scope_id,
                    record.subject.scope_hash,
                    record.verified_at_utc,
                    record.registered_at_utc,
                )
                != (
                    self.scope_id,
                    self.scope_hash,
                    self.verified_at_utc,
                    self.registered_at_utc,
                )
                or record.reviewer_attestation.content_payload.reviewed_at_utc
                > self.actual_started_at_utc
            ):
                raise ValueError(
                    "observed admission child scope/review/timeline mismatch"
                )
        digest = tagged_canonical_sha256(
            self.schema_version,
            self.model_dump(
                mode="python",
                exclude={"schema_version", "admission_id", "content_hash"},
            ),
        )
        if digest != self.content_hash or self.admission_id != stable_id(
            self.schema_version, digest
        ):
            raise ValueError("observed admission seal mismatch")
        return self


class ObservedSnapshotContextV1(_ObservedModel):
    schema_version: Literal["OBSERVED_SNAPSHOT_CONTEXT_V1"] = (
        "OBSERVED_SNAPSHOT_CONTEXT_V1"
    )
    records: tuple[ObservedSnapshotRecordV1, ...] = Field(min_length=1)
    scope: ObservedCollectionScopeAdmissionV1
    actual_at_utc: UtcDateTime

    @model_validator(mode="after")
    def graph(self) -> Self:
        heads, matches, fixtures, seen = {}, {}, {}, set()
        natural_anchors = {}
        captures, observations, result_observations = {}, {}, set()
        ordinals = {}
        if self.scope.recorded_at_utc > self.actual_at_utc:
            raise ValueError("observed context predates scope recording")
        for record in self.records:
            s = record.subject
            identity = s.identity.model_dump(
                mode="python", exclude={"schema_version", "internal_match_id"}
            )
            natural = tagged_canonical_sha256("OBSERVED_CANONICAL_ANCHOR_V1", identity)
            if (
                natural_anchors.setdefault(natural, s.stream.internal_match_id)
                != s.stream.internal_match_id
            ):
                raise ValueError(
                    "duplicate/conflicting observed canonical fixture cohort"
                )
            if (
                (s.scope_id, s.scope_hash)
                != (self.scope.scope_id, self.scope.content_hash)
                or record.registered_at_utc > self.actual_at_utc
                or record.version_id in seen
            ):
                raise ValueError("observed context scope/time/duplicate mismatch")
            if (
                s.stream.provider_fixture_key not in self.scope.subject.cohort_ids
                or matches.setdefault(s.stream.internal_match_id, s.stream.stream_id)
                != s.stream.stream_id
            ):
                raise ValueError(
                    "observed context has conflicting canonical cohort anchors"
                )
            if (
                fixtures.setdefault(s.stream.provider_fixture_key, s.stream.stream_id)
                != s.stream.stream_id
            ):
                raise ValueError(
                    "observed context has conflicting provider fixture anchors"
                )
            scope = self.scope.subject
            if not any(
                (season.provider_season_id, season.canonical_season_id)
                == (s.inspection.provider_season_id, s.identity.season)
                and s.stream.provider_fixture_key in season.included_fixture_ids
                for season in scope.seasons
            ):
                raise ValueError("observed context source season/cohort mismatch")
            if (
                s.source_rights_admission_id,
                s.source_rights_admission_hash,
                s.stream.source_id,
                s.stream.provider_code,
                s.identity.internal_competition_id,
                s.inspection.provider_competition_id,
            ) != (
                scope.source_rights_admission_id,
                scope.source_rights_admission_hash,
                scope.source_id,
                scope.provider_code,
                scope.canonical_competition_id,
                scope.provider_competition_id,
            ):
                raise ValueError(
                    "observed context child rights/source/competition mismatch"
                )
            result_pointer = (
                s.result_capture.capture_receipt_id,
                s.result_capture.record_pointer,
            )
            if result_pointer in result_observations:
                raise ValueError("duplicate observed result source observation")
            result_observations.add(result_pointer)
            for role in ("fixture", "season", "result"):
                capture = getattr(s, f"{role}_capture")
                metadata = capture.model_dump(
                    exclude={"record_pointer", "record_sha256", "outcome_sha256"}
                )
                if (
                    captures.setdefault(capture.capture_receipt_id, metadata)
                    != metadata
                ):
                    raise ValueError(
                        "observed receipt metadata differs between source observations"
                    )
                pointer = (capture.capture_receipt_id, capture.record_pointer)
                binding = (
                    capture.record_sha256,
                    capture.outcome_sha256,
                    s.stream.stream_id,
                )
                if observations.setdefault(pointer, binding) != binding:
                    raise ValueError(
                        "observed source pointer has conflicting record/stream claims"
                    )
                if capture.capture_observed_at_utc < self.scope.recorded_at_utc:
                    raise ValueError(
                        "observed source observation predates scope recording"
                    )
                if (
                    capture.capture_ordinal <= self.scope.capture_receipt_high_watermark
                    or ordinals.setdefault(
                        capture.capture_ordinal, capture.capture_receipt_id
                    )
                    != capture.capture_receipt_id
                ):
                    raise ValueError(
                        "observed capture ordinal is outside scope or has conflicting receipt claims"
                    )
            previous = heads.get(s.stream.stream_id)
            if previous is None:
                if s.revision_sequence or s.predecessor_id is not None:
                    raise ValueError(
                        "observed context requires complete predecessor chain"
                    )
            elif (
                (
                    s.predecessor_id,
                    s.predecessor_hash,
                    s.revision_sequence,
                    s.stream,
                    s.identity,
                    s.previous_match_result_id,
                )
                != (
                    previous.version_id,
                    previous.content_hash,
                    previous.revision_sequence + 1,
                    previous.stream,
                    previous.identity,
                    previous.normalized_result.match_result_id
                    if previous.normalized_result
                    else previous.subject.previous_match_result_id,
                )
                or record.capture_observed_at_utc < previous.capture_observed_at_utc
                or record.registered_at_utc < previous.registered_at_utc
                or s.result_capture.capture_ordinal
                <= previous.subject.result_capture.capture_ordinal
                or any(
                    getattr(s, f"{role}_capture").capture_observed_at_utc
                    < getattr(
                        previous.subject, f"{role}_capture"
                    ).capture_observed_at_utc
                    or getattr(s, f"{role}_capture").capture_ordinal
                    < getattr(previous.subject, f"{role}_capture").capture_ordinal
                    for role in ("fixture", "season", "result")
                )
            ):
                raise ValueError(
                    "observed context has a fork, cycle or backwards local version"
                )
            heads[s.stream.stream_id] = record
            seen.add(record.version_id)
        if heads and {x.stream.provider_fixture_key for x in heads.values()} != set(
            self.scope.subject.cohort_ids
        ):
            raise ValueError(
                "observed context must preserve the complete declared base cohort"
            )
        if (
            len(captures) > self.scope.subject.max_capture_receipts
            or len(self.records) > self.scope.subject.max_snapshot_records
        ):
            raise ValueError("observed context receipt/record limit exceeded")
        for receipt_id, metadata in captures.items():
            if (
                sum(key[0] == receipt_id for key in observations)
                > metadata["capture_record_count"]
            ):
                raise ValueError(
                    "observed source pointers exceed original capture record count"
                )
        return self

    @property
    def base_root(self) -> str:
        value = type(self).model_validate(self)
        return tagged_canonical_sha256(
            "OBSERVED_CONTEXT_ROOT_V1",
            {
                "scope_id": value.scope.scope_id,
                "scope_hash": value.scope.content_hash,
                "records_root": observed_snapshot_root(value.records),
            },
        )

    def select_heads(
        self, cutoff_at_utc: datetime, exclude_match_ids=()
    ) -> tuple[ObservedSnapshotRecordV1, ...]:
        """Whole eligible heads, including withdrawals. Never fall back to old FT.

        The returned tuple is a selection, not a replacement for the base context.
        Hash it separately with observed_snapshot_root for downstream manifests.
        """
        value = ObservedSnapshotContextV1.model_validate(self)
        cutoff = normalize_utc(cutoff_at_utc)
        if cutoff > value.actual_at_utc:
            raise ValueError("selection cutoff follows the actual context read")
        heads = {}
        excluded = set(exclude_match_ids)
        for record in value.records:
            if (
                record.capture_observed_at_utc < cutoff
                and record.registered_at_utc < cutoff
            ):
                heads[record.stream.stream_id] = record
        return tuple(
            sorted(
                (
                    x
                    for x in heads.values()
                    if x.stream.internal_match_id not in excluded
                ),
                key=lambda x: (x.identity.kickoff_at_utc, x.stream.internal_match_id),
            )
        )
