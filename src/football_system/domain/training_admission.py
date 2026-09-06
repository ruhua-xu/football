from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias, TypeVar

from pydantic import AfterValidator, BaseModel, Field, model_validator

from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_json,
    match_result_payload_sha256,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    normalize_utc,
    stable_id,
)
from football_system.domain.identity import CanonicalMatchIdentity
from football_system.domain.match import ProviderMatchMapping
from football_system.domain.settlement import MatchResult

SOURCE_RIGHTS_PAYLOAD_V1 = "SOURCE_RIGHTS_PAYLOAD_V1"
SOURCE_RIGHTS_ADMISSION_V1 = "SOURCE_RIGHTS_ADMISSION_V1"
LOCAL_REVIEW_EVIDENCE_V1 = "LOCAL_REVIEW_EVIDENCE_V1"
LOCAL_REVIEWER_ATTESTATION_V1 = "LOCAL_REVIEWER_ATTESTATION_V1"
MATCH_RESULT_ADMISSION_V1 = "MATCH_RESULT_ADMISSION_V1"
NORMALIZED_MATCH_RESULT_RECORD_V1 = "NORMALIZED_MATCH_RESULT_RECORD_V1"
MATCH_SEASON_MEMBERSHIP_V1 = "MATCH_SEASON_MEMBERSHIP_V1"
TRAINING_PROVIDER_MATCH_MAPPING_V1 = "TRAINING_PROVIDER_MATCH_MAPPING_V1"
TRAINING_CANONICAL_MATCH_IDENTITY_V1 = "TRAINING_CANONICAL_MATCH_IDENTITY_V1"
TRAINING_FIXTURE_SOURCE_V1 = "TRAINING_FIXTURE_SOURCE_V1"
TRAINING_FACT_BINDING_V1 = "TRAINING_FACT_BINDING_V1"
TRAINING_FACT_ROOT_V1 = "TRAINING_FACT_ROOT_V1"
TRAINING_FACT_ADMISSION_V1 = "TRAINING_FACT_ADMISSION_V1"
REAL_SOURCE_DATA = "REAL_SOURCE_DATA"
REGULAR_TIME_FINAL = "REGULAR_TIME_FINAL"

Sha256Digest: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Reference: TypeAlias = Annotated[str, Field(min_length=1, max_length=2048)]
RuleText: TypeAlias = Annotated[str, Field(min_length=1, max_length=2048)]
ModelT = TypeVar("ModelT", bound=BaseModel)


def _normalize_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("canonical Decimal must be finite")
    if value == 0:
        return Decimal(0)
    sign, digits, exponent = value.as_tuple()
    canonical_digits = list(digits)
    while canonical_digits[-1] == 0:
        canonical_digits.pop()
        exponent += 1
    return Decimal((sign, tuple(canonical_digits), exponent))


CanonicalConfidence: TypeAlias = Annotated[
    Decimal,
    Field(ge=0, le=1, allow_inf_nan=False),
    AfterValidator(_normalize_decimal),
]


class TrainingUseClass(StrEnum):
    APPROVED_TRAINING_HISTORY = "APPROVED_TRAINING_HISTORY"


class SourceRightsPermittedUse(StrEnum):
    ACQUIRE = "ACQUIRE"
    STORE_LOCAL = "STORE_LOCAL"
    NORMALIZE = "NORMALIZE"
    INTERNAL_RESEARCH = "INTERNAL_RESEARCH"


TRAINING_FACT_REQUIRED_USES = frozenset(SourceRightsPermittedUse)


class ProviderResultStatusCategory(StrEnum):
    REGULAR_TIME_FINAL = "REGULAR_TIME_FINAL"
    EXTRA_TIME_ONLY_RESULT = "EXTRA_TIME_ONLY_RESULT"
    PENALTY_RESULT = "PENALTY_RESULT"
    ABANDONED = "ABANDONED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    IN_PROGRESS = "IN_PROGRESS"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class ResultScoreSemantics(StrEnum):
    REGULAR_TIME_ONLY = "REGULAR_TIME_ONLY"
    EXTRA_TIME_INCLUDED = "EXTRA_TIME_INCLUDED"
    PENALTIES_INCLUDED = "PENALTIES_INCLUDED"
    UNKNOWN_OR_AMBIGUOUS = "UNKNOWN_OR_AMBIGUOUS"


class SeasonAssignmentMethod(StrEnum):
    PROVIDER_EXPLICIT_FIELDS = "PROVIDER_EXPLICIT_FIELDS"
    MATCH_DATE_HEURISTIC = "MATCH_DATE_HEURISTIC"
    DIRECTORY_NAME_HEURISTIC = "DIRECTORY_NAME_HEURISTIC"
    TARGET_SEASON_OVERRIDE = "TARGET_SEASON_OVERRIDE"
    CONSTRUCTOR_SEASON_ID = "CONSTRUCTOR_SEASON_ID"
    UNKNOWN_OR_AMBIGUOUS = "UNKNOWN_OR_AMBIGUOUS"


class TrainingProviderMatchMappingV1(DomainModel):
    schema_version: Literal["TRAINING_PROVIDER_MATCH_MAPPING_V1"] = (
        TRAINING_PROVIDER_MATCH_MAPPING_V1
    )
    mapping_id: Identifier
    provider_code: Identifier
    external_namespace: Identifier
    external_match_id: Identifier
    internal_match_id: Identifier
    resolution_method: Identifier
    confidence: CanonicalConfidence
    available_at_utc: UtcDateTime

    @classmethod
    def from_mapping(
        cls,
        mapping: ProviderMatchMapping,
    ) -> TrainingProviderMatchMappingV1:
        mapping = _revalidate_model(mapping)
        return cls(
            mapping_id=mapping.mapping_id,
            provider_code=mapping.provider_code,
            external_namespace=mapping.external_namespace,
            external_match_id=mapping.external_match_id,
            internal_match_id=mapping.internal_match_id,
            resolution_method=mapping.resolution_method,
            confidence=mapping.confidence,
            available_at_utc=mapping.available_at_utc,
        )


class TrainingCanonicalMatchIdentityV1(DomainModel):
    schema_version: Literal["TRAINING_CANONICAL_MATCH_IDENTITY_V1"] = (
        TRAINING_CANONICAL_MATCH_IDENTITY_V1
    )
    internal_match_id: Identifier
    internal_competition_id: Identifier
    internal_home_team_id: Identifier
    internal_away_team_id: Identifier
    season: Identifier
    competition_type: Identifier
    kickoff_at_utc: UtcDateTime

    @classmethod
    def from_identity(
        cls,
        identity: CanonicalMatchIdentity,
    ) -> TrainingCanonicalMatchIdentityV1:
        identity = _revalidate_model(identity)
        return cls(
            internal_match_id=identity.internal_match_id,
            internal_competition_id=identity.internal_competition_id,
            internal_home_team_id=identity.internal_home_team_id,
            internal_away_team_id=identity.internal_away_team_id,
            season=identity.season,
            competition_type=identity.competition_type,
            kickoff_at_utc=identity.kickoff_at_utc,
        )

    @model_validator(mode="after")
    def validate_teams(self) -> Self:
        if self.internal_home_team_id == self.internal_away_team_id:
            raise ValueError("canonical home and away teams must differ")
        return self


class NormalizedMatchResultRecordV1(DomainModel):
    schema_version: Literal["NORMALIZED_MATCH_RESULT_RECORD_V1"] = (
        NORMALIZED_MATCH_RESULT_RECORD_V1
    )
    match_result_id: Identifier
    match_id: Identifier
    provider_code: Identifier
    home_goals: int = Field(ge=0, strict=True)
    away_goals: int = Field(ge=0, strict=True)
    observed_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    source_result_key: Identifier
    payload_hash: Sha256Digest
    supersedes_match_result_id: Identifier | None = None

    @classmethod
    def from_result(cls, result: MatchResult) -> NormalizedMatchResultRecordV1:
        result = _revalidate_model(result)
        return cls(
            match_result_id=result.match_result_id,
            match_id=result.match_id,
            provider_code=result.provider_code,
            home_goals=result.home_goals,
            away_goals=result.away_goals,
            observed_at_utc=result.observed_at_utc,
            available_at_utc=result.available_at_utc,
            ingested_at_utc=result.ingested_at_utc,
            source_result_key=result.source_result_key,
            payload_hash=result.payload_hash,
            supersedes_match_result_id=result.supersedes_match_result_id,
        )

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if not (self.observed_at_utc <= self.available_at_utc <= self.ingested_at_utc):
            raise ValueError("normalized result timestamps are inconsistent")
        if self.supersedes_match_result_id == self.match_result_id:
            raise ValueError("normalized result cannot supersede itself")
        return self


class LocalReviewEvidenceV1(DomainModel):
    schema_version: Literal["LOCAL_REVIEW_EVIDENCE_V1"] = LOCAL_REVIEW_EVIDENCE_V1
    evidence_reference: Reference
    evidence_sha256: Sha256Digest


class LocalReviewerAttestationContentV1(DomainModel):
    attested_schema_version: Identifier
    attested_payload_hash: Sha256Digest
    authorized_reviewer: Identifier
    reviewer_authority_reference: Reference
    authority_sha256: Sha256Digest
    reviewed_at_utc: UtcDateTime
    evidence: LocalReviewEvidenceV1


class LocalReviewerAttestationV1(DomainModel):
    schema_version: Literal["LOCAL_REVIEWER_ATTESTATION_V1"] = (
        LOCAL_REVIEWER_ATTESTATION_V1
    )
    reviewer_attestation_id: Identifier
    content_payload: LocalReviewerAttestationContentV1
    attestation_hash: Sha256Digest

    @classmethod
    def freeze(
        cls,
        *,
        content_payload: LocalReviewerAttestationContentV1,
    ) -> LocalReviewerAttestationV1:
        content_payload = _revalidate_model(content_payload)
        artifact_id, digest = _artifact_identity(
            LOCAL_REVIEWER_ATTESTATION_V1,
            content_payload,
        )
        return cls(
            reviewer_attestation_id=artifact_id,
            content_payload=content_payload,
            attestation_hash=digest,
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        _assert_artifact_seal(
            self.schema_version,
            self.content_payload,
            self.reviewer_attestation_id,
            self.attestation_hash,
            "reviewer attestation",
        )
        return self


class SourceRightsPayloadV1(DomainModel):
    payload_version: Literal["SOURCE_RIGHTS_PAYLOAD_V1"] = SOURCE_RIGHTS_PAYLOAD_V1
    source_classification: Literal["REAL_SOURCE_DATA"] = REAL_SOURCE_DATA
    source_owner: Identifier
    product_name: Identifier
    source_ids: tuple[Identifier, ...] = Field(min_length=1)
    terms_version: Identifier
    terms_reference: Reference
    terms_sha256: Sha256Digest
    jurisdiction: Identifier
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    permitted_uses: tuple[SourceRightsPermittedUse, ...] = Field(min_length=1)
    raw_retention_rule: RuleText
    derived_retention_rule: RuleText
    subscription_end_retention_rule: RuleText
    deletion_obligation: RuleText
    public_repository_boundary: RuleText

    @classmethod
    def freeze(
        cls,
        *,
        source_ids: Iterable[str],
        permitted_uses: Iterable[SourceRightsPermittedUse | str],
        **values: object,
    ) -> SourceRightsPayloadV1:
        return cls.model_validate(
            {
                **values,
                "source_ids": _canonical_identifiers(source_ids, "source IDs"),
                "permitted_uses": _canonical_uses(permitted_uses),
            }
        )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.source_ids != tuple(sorted(self.source_ids)) or len(
            self.source_ids
        ) != len(set(self.source_ids)):
            raise ValueError("source rights source IDs must be unique and sorted")
        if self.permitted_uses != tuple(
            sorted(self.permitted_uses, key=lambda item: item.value)
        ) or len(self.permitted_uses) != len(set(self.permitted_uses)):
            raise ValueError("source rights permitted uses must be unique and sorted")
        if self.effective_at_utc >= self.expires_at_utc:
            raise ValueError("source rights must have a valid effective interval")
        return self


class SourceRightsAdmissionContentV1(DomainModel):
    rights_payload: SourceRightsPayloadV1
    rights_payload_hash: Sha256Digest
    reviewer_attestation: LocalReviewerAttestationV1
    recorded_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        expected_hash = tagged_canonical_sha256(
            SOURCE_RIGHTS_PAYLOAD_V1,
            self.rights_payload,
        )
        if not hmac.compare_digest(self.rights_payload_hash, expected_hash):
            raise ValueError("source rights payload hash is inconsistent")
        attestation = self.reviewer_attestation.content_payload
        if (
            attestation.attested_schema_version != SOURCE_RIGHTS_PAYLOAD_V1
            or not hmac.compare_digest(
                attestation.attested_payload_hash,
                self.rights_payload_hash,
            )
        ):
            raise ValueError("reviewer attestation does not bind the rights payload")
        if attestation.reviewed_at_utc > self.recorded_at_utc:
            raise ValueError("rights review cannot follow admission recording")
        if self.recorded_at_utc >= self.rights_payload.expires_at_utc:
            raise ValueError("source rights must be recorded before expiry")
        return self


class SourceRightsAdmissionV1(DomainModel):
    schema_version: Literal["SOURCE_RIGHTS_ADMISSION_V1"] = (
        SOURCE_RIGHTS_ADMISSION_V1
    )
    source_rights_admission_id: Identifier
    content_payload: SourceRightsAdmissionContentV1
    admission_hash: Sha256Digest

    @classmethod
    def from_recorded(
        cls,
        *,
        rights_payload: SourceRightsPayloadV1,
        reviewer_attestation: LocalReviewerAttestationV1,
        recorded_at_utc: datetime,
    ) -> SourceRightsAdmissionV1:
        """Reconstitute a caller-confirmed recorded event; this performs no I/O."""
        rights_payload = _revalidate_model(rights_payload)
        reviewer_attestation = _revalidate_model(reviewer_attestation)
        rights_payload_hash = tagged_canonical_sha256(
            SOURCE_RIGHTS_PAYLOAD_V1,
            rights_payload,
        )
        content_payload = SourceRightsAdmissionContentV1(
            rights_payload=rights_payload,
            rights_payload_hash=rights_payload_hash,
            reviewer_attestation=reviewer_attestation,
            recorded_at_utc=recorded_at_utc,
        )
        artifact_id, digest = _artifact_identity(
            SOURCE_RIGHTS_ADMISSION_V1,
            content_payload,
        )
        return cls(
            source_rights_admission_id=artifact_id,
            content_payload=content_payload,
            admission_hash=digest,
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        _assert_artifact_seal(
            self.schema_version,
            self.content_payload,
            self.source_rights_admission_id,
            self.admission_hash,
            "source rights admission",
        )
        return self

    def assert_active_for(
        self,
        at_utc: datetime,
        required_uses: Iterable[SourceRightsPermittedUse],
    ) -> None:
        validated = _revalidate_model(self)
        at = normalize_utc(at_utc)
        content = validated.content_payload
        rights = content.rights_payload
        if content.recorded_at_utc > at:
            raise ValueError("source rights admission is not yet recorded")
        if not (rights.effective_at_utc <= at < rights.expires_at_utc):
            raise ValueError("source rights admission is not active")
        missing = set(required_uses) - set(rights.permitted_uses)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"source rights permitted-use mismatch: {names}")


class MatchResultAdmissionContentV1(DomainModel):
    source_id: Identifier
    internal_match_id: Identifier
    match_result_id: Identifier
    provider_code: Identifier
    provider_result_key: Identifier
    provider_raw_status: Identifier
    status_mapping_version: Identifier
    provider_status_category: ProviderResultStatusCategory
    normalized_status: Literal["REGULAR_TIME_FINAL"] = REGULAR_TIME_FINAL
    score_semantics: ResultScoreSemantics
    regular_time_home_goals: int = Field(ge=0, strict=True)
    regular_time_away_goals: int = Field(ge=0, strict=True)
    provider_finalized_at_utc: UtcDateTime
    source_observed_at_utc: UtcDateTime
    source_available_at_utc: UtcDateTime
    raw_artifact_id: Identifier
    raw_artifact_payload_sha256: Sha256Digest
    raw_artifact_created_at_utc: UtcDateTime
    raw_record_sha256: Sha256Digest
    normalized_record_sha256: Sha256Digest
    local_imported_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime
    adapter_name: Identifier
    adapter_version: Identifier
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime
    supersedes_match_result_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_result_semantics(self) -> Self:
        if self.provider_status_category is not ProviderResultStatusCategory.REGULAR_TIME_FINAL:
            raise ValueError("only an explicit regular-time-final result is admissible")
        if self.score_semantics is not ResultScoreSemantics.REGULAR_TIME_ONLY:
            raise ValueError("result score must have explicit regular-time-only semantics")
        if not (
            self.provider_finalized_at_utc
            <= self.source_observed_at_utc
            <= self.source_available_at_utc
            <= self.local_imported_at_utc
            <= self.raw_artifact_created_at_utc
            <= self.registered_at_utc
            <= self.reviewed_at_utc
        ):
            raise ValueError("result admission timestamps are inconsistent")
        if self.supersedes_match_result_id is not None:
            raise ValueError(
                "result correction requires persisted predecessor validation"
            )
        return self


class MatchResultAdmissionV1(DomainModel):
    schema_version: Literal["MATCH_RESULT_ADMISSION_V1"] = MATCH_RESULT_ADMISSION_V1
    match_result_admission_id: Identifier
    content_payload: MatchResultAdmissionContentV1
    admission_hash: Sha256Digest

    @classmethod
    def freeze(
        cls,
        *,
        content_payload: MatchResultAdmissionContentV1,
    ) -> MatchResultAdmissionV1:
        content_payload = _revalidate_model(content_payload)
        artifact_id, digest = _artifact_identity(
            MATCH_RESULT_ADMISSION_V1,
            content_payload,
        )
        return cls(
            match_result_admission_id=artifact_id,
            content_payload=content_payload,
            admission_hash=digest,
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        _assert_artifact_seal(
            self.schema_version,
            self.content_payload,
            self.match_result_admission_id,
            self.admission_hash,
            "match result admission",
        )
        return self

    def assert_matches(
        self,
        result: MatchResult | NormalizedMatchResultRecordV1,
    ) -> None:
        validated = _revalidate_model(self)
        if isinstance(result, MatchResult):
            result = NormalizedMatchResultRecordV1.from_result(result)
        else:
            result = _revalidate_model(result)
        content = validated.content_payload
        expected = (
            content.match_result_id,
            content.internal_match_id,
            content.provider_code,
            content.provider_result_key,
            content.regular_time_home_goals,
            content.regular_time_away_goals,
            content.source_observed_at_utc,
            content.source_available_at_utc,
            content.supersedes_match_result_id,
        )
        actual = (
            result.match_result_id,
            result.match_id,
            result.provider_code,
            result.source_result_key,
            result.home_goals,
            result.away_goals,
            result.observed_at_utc,
            result.available_at_utc,
            result.supersedes_match_result_id,
        )
        if expected != actual:
            raise ValueError("normalized MatchResult does not match its result admission")
        expected_score_hash = match_result_payload_sha256(
            content.regular_time_home_goals,
            content.regular_time_away_goals,
        )
        if not hmac.compare_digest(result.payload_hash, expected_score_hash):
            raise ValueError("normalized MatchResult score hash is inconsistent")
        expected_record_hash = normalized_match_result_record_sha256(result)
        if not hmac.compare_digest(
            content.normalized_record_sha256,
            expected_record_hash,
        ):
            raise ValueError("normalized MatchResult record hash is inconsistent")


class MatchSeasonMembershipContentV1(DomainModel):
    source_id: Identifier
    provider_code: Identifier
    provider_competition_id: Identifier
    provider_season_id: Identifier
    provider_season_candidate_ids: tuple[Identifier, ...] = Field(min_length=1)
    provider_fixture_namespace: Identifier
    provider_fixture_key: Identifier
    provider_mapping_id: Identifier
    internal_match_id: Identifier
    fixture_source_record_id: Identifier
    fixture_record_sha256: Sha256Digest
    provider_scope_raw_artifact_id: Identifier
    provider_scope_payload_sha256: Sha256Digest
    provider_scope_created_at_utc: UtcDateTime
    provider_scope_record_sha256: Sha256Digest
    provider_competition_field_path: Reference
    provider_season_field_path: Reference
    provider_fixture_field_path: Reference
    season_assignment_method: SeasonAssignmentMethod
    season_mapping_version: Identifier
    canonical_competition_id: Identifier
    canonical_season_id: Identifier
    relationship_evidence_kind: Literal[
        "PROVIDER_EXPLICIT_COMPETITION_SEASON_FIXTURE"
    ] = "PROVIDER_EXPLICIT_COMPETITION_SEASON_FIXTURE"
    source_available_at_utc: UtcDateTime
    local_imported_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime
    mapping_policy_version: Identifier
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_membership(self) -> Self:
        if (
            len(self.provider_season_candidate_ids) != 1
            or self.provider_season_candidate_ids[0] != self.provider_season_id
        ):
            raise ValueError("provider season evidence must be explicit and unambiguous")
        if self.season_assignment_method is not SeasonAssignmentMethod.PROVIDER_EXPLICIT_FIELDS:
            raise ValueError("season assignment must use explicit provider fields")
        field_paths = (
            self.provider_competition_field_path,
            self.provider_season_field_path,
            self.provider_fixture_field_path,
        )
        if len(set(field_paths)) != len(field_paths):
            raise ValueError("provider relationship field paths must be distinct")
        if not (
            self.source_available_at_utc
            <= self.local_imported_at_utc
            <= self.provider_scope_created_at_utc
            <= self.registered_at_utc
            <= self.reviewed_at_utc
        ):
            raise ValueError("season membership timestamps are inconsistent")
        return self


class MatchSeasonMembershipV1(DomainModel):
    schema_version: Literal["MATCH_SEASON_MEMBERSHIP_V1"] = (
        MATCH_SEASON_MEMBERSHIP_V1
    )
    season_membership_id: Identifier
    content_payload: MatchSeasonMembershipContentV1
    membership_hash: Sha256Digest

    @classmethod
    def freeze(
        cls,
        *,
        content_payload: MatchSeasonMembershipContentV1,
    ) -> MatchSeasonMembershipV1:
        content_payload = _revalidate_model(content_payload)
        artifact_id, digest = _artifact_identity(
            MATCH_SEASON_MEMBERSHIP_V1,
            content_payload,
        )
        return cls(
            season_membership_id=artifact_id,
            content_payload=content_payload,
            membership_hash=digest,
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        _assert_artifact_seal(
            self.schema_version,
            self.content_payload,
            self.season_membership_id,
            self.membership_hash,
            "match season membership",
        )
        return self

    def assert_matches(
        self,
        provider_mapping: TrainingProviderMatchMappingV1,
        canonical_identity: TrainingCanonicalMatchIdentityV1,
    ) -> None:
        validated = _revalidate_model(self)
        provider_mapping = _revalidate_model(provider_mapping)
        canonical_identity = _revalidate_model(canonical_identity)
        content = validated.content_payload
        mapping_expected = (
            content.provider_mapping_id,
            content.provider_code,
            content.provider_fixture_namespace,
            content.provider_fixture_key,
            content.internal_match_id,
        )
        mapping_actual = (
            provider_mapping.mapping_id,
            provider_mapping.provider_code,
            provider_mapping.external_namespace,
            provider_mapping.external_match_id,
            provider_mapping.internal_match_id,
        )
        if mapping_expected != mapping_actual:
            raise ValueError("season membership does not match provider mapping")
        if provider_mapping.available_at_utc > content.source_available_at_utc:
            raise ValueError("season membership predates its provider mapping")
        canonical_expected = (
            content.internal_match_id,
            content.canonical_competition_id,
            content.canonical_season_id,
        )
        canonical_actual = (
            canonical_identity.internal_match_id,
            canonical_identity.internal_competition_id,
            canonical_identity.season,
        )
        if canonical_expected != canonical_actual:
            raise ValueError("season membership does not match canonical identity")


class TrainingFixtureSourceV1(DomainModel):
    schema_version: Literal["TRAINING_FIXTURE_SOURCE_V1"] = (
        TRAINING_FIXTURE_SOURCE_V1
    )
    source_id: Identifier
    provider_code: Identifier
    provider_fixture_namespace: Identifier
    provider_fixture_key: Identifier
    internal_match_id: Identifier
    fixture_source_archive_id: Identifier
    fixture_source_archive_payload_sha256: Sha256Digest
    fixture_source_archive_created_at_utc: UtcDateTime
    fixture_source_record_id: Identifier
    fixture_record_sha256: Sha256Digest
    source_available_at_utc: UtcDateTime
    local_imported_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if not (
            self.source_available_at_utc
            <= self.local_imported_at_utc
            <= self.fixture_source_archive_created_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("fixture source timestamps are inconsistent")
        return self


class TrainingFactBindingContentV1(DomainModel):
    sequence: int = Field(ge=0, strict=True)
    fixture_source: TrainingFixtureSourceV1
    provider_mapping: TrainingProviderMatchMappingV1
    canonical_identity: TrainingCanonicalMatchIdentityV1
    season_membership: MatchSeasonMembershipV1
    match_result_admission: MatchResultAdmissionV1
    normalized_result: NormalizedMatchResultRecordV1

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        fixture = self.fixture_source
        mapping = self.provider_mapping
        identity = self.canonical_identity
        membership = self.season_membership.content_payload
        result_admission = self.match_result_admission.content_payload
        if len({fixture.source_id, membership.source_id, result_admission.source_id}) != 1:
            raise ValueError("training fact sources are outside one rights scope")
        if len(
            {fixture.provider_code, membership.provider_code, result_admission.provider_code}
        ) != 1:
            raise ValueError("training fact provider lineage is inconsistent")
        if (
            fixture.internal_match_id != membership.internal_match_id
            or fixture.provider_fixture_namespace
            != membership.provider_fixture_namespace
            or fixture.internal_match_id != result_admission.internal_match_id
            or fixture.provider_fixture_key != membership.provider_fixture_key
            or fixture.fixture_source_record_id
            != membership.fixture_source_record_id
            or fixture.fixture_record_sha256 != membership.fixture_record_sha256
        ):
            raise ValueError("training fact fixture and admission lineage is inconsistent")
        self.season_membership.assert_matches(mapping, identity)
        if self.normalized_result.ingested_at_utc != self.normalized_result.available_at_utc:
            raise ValueError(
                "SOURCE_TIME_RESEARCH result ingestion must preserve source time"
            )
        self.match_result_admission.assert_matches(self.normalized_result)
        if identity.kickoff_at_utc >= result_admission.provider_finalized_at_utc:
            raise ValueError("training fact result must be finalized after kickoff")
        return self


class TrainingFactBindingV1(DomainModel):
    schema_version: Literal["TRAINING_FACT_BINDING_V1"] = TRAINING_FACT_BINDING_V1
    training_fact_binding_id: Identifier
    content_payload: TrainingFactBindingContentV1
    fact_hash: Sha256Digest

    @classmethod
    def freeze(
        cls,
        *,
        content_payload: TrainingFactBindingContentV1,
    ) -> TrainingFactBindingV1:
        content_payload = _revalidate_model(content_payload)
        artifact_id, digest = _artifact_identity(
            TRAINING_FACT_BINDING_V1,
            content_payload,
        )
        return cls(
            training_fact_binding_id=artifact_id,
            content_payload=content_payload,
            fact_hash=digest,
        )

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        _assert_artifact_seal(
            self.schema_version,
            self.content_payload,
            self.training_fact_binding_id,
            self.fact_hash,
            "training fact binding",
        )
        return self


class TrainingFactAdmissionContentV1(DomainModel):
    source_rights_admission_id: Identifier
    source_rights_admission_hash: Sha256Digest
    source_data_mode: HistoricalDataMode
    source_classification: Literal["REAL_SOURCE_DATA"] = REAL_SOURCE_DATA
    retrospective: Literal[True] = True
    actual_started_at_utc: UtcDateTime
    actual_completed_at_utc: UtcDateTime
    persisted_at_utc: UtcDateTime
    admitted_fact_count: int = Field(ge=1, strict=True)
    admitted_fact_root: Sha256Digest

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.source_data_mode is not HistoricalDataMode.SOURCE_TIME_RESEARCH:
            raise ValueError(
                "approved training fact admission requires SOURCE_TIME_RESEARCH"
            )
        if not (
            self.actual_started_at_utc
            <= self.actual_completed_at_utc
            <= self.persisted_at_utc
        ):
            raise ValueError("training fact admission timestamps are inconsistent")
        return self


class TrainingFactAdmissionV1(DomainModel):
    schema_version: Literal["TRAINING_FACT_ADMISSION_V1"] = (
        TRAINING_FACT_ADMISSION_V1
    )
    training_fact_admission_id: Identifier
    content_payload: TrainingFactAdmissionContentV1
    source_rights_admission: SourceRightsAdmissionV1
    facts: tuple[TrainingFactBindingV1, ...] = Field(min_length=1)
    admission_hash: Sha256Digest

    @classmethod
    def from_persisted(
        cls,
        *,
        source_rights_admission: SourceRightsAdmissionV1 | None,
        facts: tuple[TrainingFactBindingV1, ...],
        actual_started_at_utc: datetime,
        actual_completed_at_utc: datetime,
        persisted_at_utc: datetime,
    ) -> TrainingFactAdmissionV1:
        """Reconstitute a caller-confirmed atomic write; this performs no I/O."""
        if source_rights_admission is None:
            raise ValueError("source rights admission is required")
        source_rights_admission = _revalidate_model(source_rights_admission)
        facts = tuple(_revalidate_model(item) for item in facts)
        fact_root = training_fact_root(facts)
        content_payload = TrainingFactAdmissionContentV1(
            source_rights_admission_id=(
                source_rights_admission.source_rights_admission_id
            ),
            source_rights_admission_hash=source_rights_admission.admission_hash,
            source_data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
            source_classification=REAL_SOURCE_DATA,
            retrospective=True,
            actual_started_at_utc=actual_started_at_utc,
            actual_completed_at_utc=actual_completed_at_utc,
            persisted_at_utc=persisted_at_utc,
            admitted_fact_count=len(facts),
            admitted_fact_root=fact_root,
        )
        artifact_id, digest = _artifact_identity(
            TRAINING_FACT_ADMISSION_V1,
            content_payload,
        )
        return cls(
            training_fact_admission_id=artifact_id,
            content_payload=content_payload,
            source_rights_admission=source_rights_admission,
            facts=facts,
            admission_hash=digest,
        )

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        content = self.content_payload
        rights = _revalidate_model(self.source_rights_admission)
        facts = tuple(_revalidate_model(item) for item in self.facts)
        if (
            content.source_rights_admission_id
            != rights.source_rights_admission_id
            or not hmac.compare_digest(
                content.source_rights_admission_hash,
                rights.admission_hash,
            )
        ):
            raise ValueError("training fact admission rights binding is inconsistent")
        if content.admitted_fact_count != len(facts):
            raise ValueError("training fact admission count is inconsistent")
        if tuple(item.content_payload.sequence for item in facts) != tuple(
            range(len(facts))
        ):
            raise ValueError("training fact sequence must be contiguous")
        match_ids = tuple(
            item.content_payload.normalized_result.match_id for item in facts
        )
        result_ids = tuple(
            item.content_payload.normalized_result.match_result_id for item in facts
        )
        if len(match_ids) != len(set(match_ids)) or len(result_ids) != len(
            set(result_ids)
        ):
            raise ValueError("training facts must have unique match and result IDs")
        order_keys = tuple(
            training_fact_order_key(
                fixture_source=item.content_payload.fixture_source,
                season_membership=item.content_payload.season_membership,
                match_result_admission=item.content_payload.match_result_admission,
                canonical_identity=item.content_payload.canonical_identity,
                normalized_result=item.content_payload.normalized_result,
            )
            for item in facts
        )
        if order_keys != tuple(sorted(order_keys)):
            raise ValueError("training facts are not in canonical order")
        expected_root = training_fact_root(facts)
        if not hmac.compare_digest(content.admitted_fact_root, expected_root):
            raise ValueError("training fact root is inconsistent")
        _assert_artifact_seal(
            self.schema_version,
            content,
            self.training_fact_admission_id,
            self.admission_hash,
            "training fact admission",
        )
        rights_scope = set(rights.content_payload.rights_payload.source_ids)
        operation_times = [
            content.actual_started_at_utc,
            content.actual_completed_at_utc,
            content.persisted_at_utc,
        ]
        mapping_ids: set[str] = set()
        fixture_keys: set[tuple[str, str, str]] = set()
        result_keys: set[tuple[str, str]] = set()
        fixture_records: set[tuple[str, str]] = set()
        season_scope_mappings: dict[
            tuple[str, str, str, str],
            tuple[str, str, str, str],
        ] = {}
        archive_metadata: dict[
            str,
            tuple[str, str, str, datetime, datetime, datetime],
        ] = {}
        fixture_record_identities: dict[
            tuple[str, str, str],
            tuple[str, str, str],
        ] = {}
        result_record_identities: dict[
            tuple[str, str, str],
            tuple[str, str, str, str],
        ] = {}
        for item in facts:
            binding = item.content_payload
            source_id = binding.fixture_source.source_id
            if source_id not in rights_scope:
                raise ValueError("training fact source is not covered by source rights")
            source_times = (
                binding.fixture_source.local_imported_at_utc,
                binding.fixture_source.fixture_source_archive_created_at_utc,
                binding.fixture_source.registered_at_utc,
                binding.season_membership.content_payload.local_imported_at_utc,
                binding.season_membership.content_payload.provider_scope_created_at_utc,
                binding.season_membership.content_payload.registered_at_utc,
                binding.season_membership.content_payload.reviewed_at_utc,
                binding.match_result_admission.content_payload.local_imported_at_utc,
                binding.match_result_admission.content_payload.raw_artifact_created_at_utc,
                binding.match_result_admission.content_payload.registered_at_utc,
                binding.match_result_admission.content_payload.reviewed_at_utc,
            )
            operation_times.extend(source_times)
            if max(source_times) > content.actual_started_at_utc:
                raise ValueError(
                    "training fact prerequisites cannot follow admission start"
                )
            mapping = binding.provider_mapping
            membership = binding.season_membership.content_payload
            result = binding.match_result_admission.content_payload
            mapping_ids.add(mapping.mapping_id)
            fixture_keys.add(
                (
                    mapping.provider_code,
                    mapping.external_namespace,
                    mapping.external_match_id,
                )
            )
            result_keys.add((result.provider_code, result.provider_result_key))
            fixture_records.add(
                (
                    binding.fixture_source.fixture_source_archive_id,
                    binding.fixture_source.fixture_source_record_id,
                )
            )
            season_scope = (
                source_id,
                membership.provider_code,
                membership.provider_competition_id,
                membership.provider_season_id,
            )
            canonical_scope = (
                membership.canonical_competition_id,
                membership.canonical_season_id,
                membership.season_mapping_version,
                membership.mapping_policy_version,
            )
            previous_scope = season_scope_mappings.setdefault(
                season_scope,
                canonical_scope,
            )
            if previous_scope != canonical_scope:
                raise ValueError("provider season scope mapping is inconsistent")
            fixture_record_reference = (
                source_id,
                mapping.provider_code,
                binding.fixture_source.fixture_record_sha256,
            )
            fixture_identity = (
                binding.fixture_source.provider_fixture_namespace,
                binding.fixture_source.provider_fixture_key,
                binding.fixture_source.internal_match_id,
            )
            previous_fixture_identity = fixture_record_identities.setdefault(
                fixture_record_reference,
                fixture_identity,
            )
            if previous_fixture_identity != fixture_identity:
                raise ValueError("fixture source record identity is inconsistent")
            result_record_reference = (
                source_id,
                result.provider_code,
                result.raw_record_sha256,
            )
            result_identity = (
                result.provider_code,
                result.provider_result_key,
                result.internal_match_id,
                result.match_result_id,
            )
            previous_result_identity = result_record_identities.setdefault(
                result_record_reference,
                result_identity,
            )
            if previous_result_identity != result_identity:
                raise ValueError("result source record identity is inconsistent")
            archive_references = (
                (
                    binding.fixture_source.fixture_source_archive_id,
                    binding.fixture_source.fixture_source_archive_payload_sha256,
                    binding.fixture_source.fixture_source_archive_created_at_utc,
                    binding.fixture_source.local_imported_at_utc,
                    binding.fixture_source.registered_at_utc,
                ),
                (
                    membership.provider_scope_raw_artifact_id,
                    membership.provider_scope_payload_sha256,
                    membership.provider_scope_created_at_utc,
                    membership.local_imported_at_utc,
                    membership.registered_at_utc,
                ),
                (
                    result.raw_artifact_id,
                    result.raw_artifact_payload_sha256,
                    result.raw_artifact_created_at_utc,
                    result.local_imported_at_utc,
                    result.registered_at_utc,
                ),
            )
            for (
                archive_id,
                payload_hash,
                created_at_utc,
                imported_at_utc,
                registered_at_utc,
            ) in archive_references:
                metadata = (
                    source_id,
                    mapping.provider_code,
                    payload_hash,
                    created_at_utc,
                    imported_at_utc,
                    registered_at_utc,
                )
                previous = archive_metadata.setdefault(archive_id, metadata)
                if previous != metadata:
                    raise ValueError("source archive identity metadata is inconsistent")
        fact_count = len(facts)
        if not (
            len(mapping_ids)
            == len(fixture_keys)
            == len(result_keys)
            == len(fixture_records)
            == fact_count
        ):
            raise ValueError("training facts must have unique source lineage")
        for at_utc in operation_times:
            rights.assert_active_for(at_utc, TRAINING_FACT_REQUIRED_USES)
        return self


def tagged_canonical_sha256(schema_tag: str, content_payload: object) -> str:
    if not schema_tag or "\0" in schema_tag:
        raise ValueError("schema tag must be a non-empty NUL-free string")
    encoded = (
        schema_tag.encode("utf-8")
        + b"\0"
        + canonical_json(_canonical_hash_value(content_payload)).encode("utf-8")
    )
    return hashlib.sha256(encoded).hexdigest()


def normalized_match_result_record_sha256(
    result: MatchResult | NormalizedMatchResultRecordV1,
) -> str:
    if isinstance(result, MatchResult):
        result = NormalizedMatchResultRecordV1.from_result(result)
    else:
        result = _revalidate_model(result)
    return tagged_canonical_sha256(
        NORMALIZED_MATCH_RESULT_RECORD_V1,
        result,
    )


def training_fact_order_key(
    *,
    fixture_source: TrainingFixtureSourceV1,
    season_membership: MatchSeasonMembershipV1,
    match_result_admission: MatchResultAdmissionV1,
    canonical_identity: CanonicalMatchIdentity | TrainingCanonicalMatchIdentityV1,
    normalized_result: MatchResult | NormalizedMatchResultRecordV1,
) -> tuple[datetime, datetime, datetime, str, str]:
    effective_source_available_at_utc = max(
        fixture_source.source_available_at_utc,
        season_membership.content_payload.source_available_at_utc,
        match_result_admission.content_payload.source_available_at_utc,
    )
    return (
        canonical_identity.kickoff_at_utc,
        effective_source_available_at_utc,
        normalized_result.ingested_at_utc,
        normalized_result.match_id,
        normalized_result.match_result_id,
    )


def training_fact_root(facts: Iterable[TrainingFactBindingV1]) -> str:
    ordered = tuple(_revalidate_model(item) for item in facts)
    payload = {
        "facts": [
            {
                "sequence": item.content_payload.sequence,
                "training_fact_binding_id": item.training_fact_binding_id,
                "fact_hash": item.fact_hash,
            }
            for item in ordered
        ]
    }
    return tagged_canonical_sha256(TRAINING_FACT_ROOT_V1, payload)


def _artifact_identity(schema_version: str, content_payload: object) -> tuple[str, str]:
    if isinstance(content_payload, BaseModel):
        content_payload = _revalidate_model(content_payload)
    digest = tagged_canonical_sha256(schema_version, content_payload)
    return stable_id(schema_version, digest), digest


def _assert_artifact_seal(
    schema_version: str,
    content_payload: object,
    artifact_id: str,
    content_hash: str,
    label: str,
) -> None:
    if isinstance(content_payload, BaseModel):
        content_payload = _revalidate_model(content_payload)
    expected_id, expected_hash = _artifact_identity(schema_version, content_payload)
    if not hmac.compare_digest(content_hash, expected_hash):
        raise ValueError(f"{label} hash is inconsistent")
    if artifact_id != expected_id:
        raise ValueError(f"{label} ID is inconsistent")


def _canonical_identifiers(values: Iterable[str], label: str) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if not normalized or any(not value for value in normalized):
        raise ValueError(f"{label} must be non-empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")
    return tuple(sorted(normalized))


def _canonical_uses(
    values: Iterable[SourceRightsPermittedUse | str],
) -> tuple[SourceRightsPermittedUse, ...]:
    normalized = tuple(SourceRightsPermittedUse(value) for value in values)
    if not normalized:
        raise ValueError("source rights permitted uses must be non-empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError("source rights permitted uses must be unique")
    return tuple(sorted(normalized, key=lambda item: item.value))


def _canonical_hash_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_hash_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {key: _canonical_hash_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_hash_value(item) for item in value]
    if isinstance(value, Decimal):
        return _normalize_decimal(value)
    return value


def _revalidate_model(value: ModelT) -> ModelT:
    return type(value).model_validate(value.model_dump(mode="python"))
