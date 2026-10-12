"""Authoritative scoped admission/lifecycle envelopes layered over the P1-A profile.

Profile facts remain review candidates. Admitted result IDs/times and their root
are a separate trusted-clock projection; neither inert refs nor caller clocks
grant qualification. Existing V1 types and numerical code are untouched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from football_system.domain.archive import canonical_json
from football_system.domain.common import UtcDateTime, stable_id
from football_system.domain.openfootball_production import CONFIG_HASH, USES, require, OpenFootballProductionTargetV1
from football_system.domain.openfootball_scope import CompetitionScopeProfileV2, ScopeIdentityV2, logical_bootstrap_key
from football_system.domain.openfootball_snapshot import Digest, Label, SnapshotModel
from football_system.domain.services.elo_baseline import EloBaselineState, EloRegularTimeResult
from football_system.domain.training_admission import LocalReviewEvidenceV1, tagged_canonical_sha256


class ScopedParserPolicyV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_PARSER_POLICY_V2"] = "OPENFOOTBALL_SCOPED_PARSER_POLICY_V2"
    score_policy: Literal["EXPLICIT_FT_REGULAR_DOMESTIC_LEAGUE_ONLY"] = "EXPLICIT_FT_REGULAR_DOMESTIC_LEAGUE_ONLY"
    direct_array_policy: Literal["PERIOD_UNPROVEN_EXCEPTION"] = "PERIOD_UNPROVEN_EXCEPTION"
    abnormal_status_policy: Literal["EXCEPTION"] = "EXCEPTION"
    historical_time_policy: Literal["CURRENT_SNAPSHOT_OBSERVED_NOT_POINT_IN_TIME"] = "CURRENT_SNAPSHOT_OBSERVED_NOT_POINT_IN_TIME"
    model_selection_policy: Literal["NO_ROI_OR_MODEL_PERFORMANCE_SELECTION"] = "NO_ROI_OR_MODEL_PERFORMANCE_SELECTION"


class ScopedSourceRightsV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_SOURCE_RIGHTS_V2"] = "OPENFOOTBALL_SCOPED_SOURCE_RIGHTS_V2"
    source_id: Label
    profile_hash: Digest
    license_sha256: Digest
    uses: tuple[str, ...] = USES
    raw_retention: Literal["PERMANENT_COMPLETE_COHORT"] = "PERMANENT_COMPLETE_COHORT"
    audit_retention: Literal["INDEFINITE_PRIVATE_AUDIT"] = "INDEFINITE_PRIVATE_AUDIT"
    model_rule: Literal["PILOT_ALLOWED_RELEASE_REQUIRES_EXACT_APPROVAL"] = "PILOT_ALLOWED_RELEASE_REQUIRES_EXACT_APPROVAL"
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime

    @model_validator(mode="after")
    def scope(self):
        require(self.uses == USES and self.effective_at_utc < self.expires_at_utc, "SCOPED_RIGHTS_SCOPE_INVALID")
        return self


class ScopedStorageBindingV2(SnapshotModel):
    kind: Literal["EXISTING_SQLITE_FILE", "ISOLATED_SYNTHETIC_MEMORY"]
    identity: tuple[str, ...] = Field(min_length=1)


class ScopedCaptureFileV2(SnapshotModel):
    path: Label
    bytes: int = Field(strict=True, gt=0)
    sha256: Digest
    captured_at_utc: UtcDateTime
    source_uri: Label


class ScopedAcquisitionV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_ACQUISITION_V2"] = "OPENFOOTBALL_SCOPED_ACQUISITION_V2"
    source_id: Label
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    classification: Literal["REAL_SOURCE_DATA", "SYNTHETIC_SOFTWARE_ACCEPTANCE"]
    files: tuple[ScopedCaptureFileV2, ...]


class ScopeQualificationSubjectV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPE_QUALIFICATION_REVIEW_V2"] = "OPENFOOTBALL_SCOPE_QUALIFICATION_REVIEW_V2"
    profile: CompetitionScopeProfileV2
    country_code: str = Field(strict=True, pattern=r"^[A-Z]{2}$")
    rights: ScopedSourceRightsV2
    source_prefix: Label
    acquisition: LocalReviewEvidenceV1
    parser_policy: LocalReviewEvidenceV1
    storage: ScopedStorageBindingV2
    catalog_context: tuple[dict[str, JsonValue], ...]
    classification: Literal["REAL_SOURCE_DATA", "SYNTHETIC_SOFTWARE_ACCEPTANCE"]

    @model_validator(mode="after")
    def bound(self):
        require(self.rights.source_id == self.profile.source_id and self.rights.profile_hash == self.profile.content_hash,
            "SCOPED_RIGHTS_PROFILE_MISMATCH")
        licenses = [f.sha256 for f in self.profile.files if f.role == "LICENSE"]
        require(licenses == [self.rights.license_sha256], "SCOPED_LICENSE_MISMATCH")
        require(self.classification == "SYNTHETIC_SOFTWARE_ACCEPTANCE" or self.storage.kind == "EXISTING_SQLITE_FILE",
            "REAL_QUALIFICATION_REQUIRES_EXISTING_FILE_DATABASE")
        return self


def admitted_results(profile: CompetitionScopeProfileV2, admitted_at: datetime) -> tuple[EloRegularTimeResult, ...]:
    """No candidate availability/ingestion clock is promoted to admitted history."""
    value = CompetitionScopeProfileV2.model_validate(profile)
    owner = ScopeIdentityV2.of(value)
    results = []
    for row in value.records:
        if row.candidate_result is not None:
            candidate = row.candidate_result
            require(candidate.kickoff_at_utc < admitted_at, "SCOPED_RESULT_NOT_OBSERVED_HISTORY")
            values = candidate.model_dump(mode="python") | dict(match_result_id=stable_id("OFP_V2_ADMITTED_RESULT", owner.scope_id,
                candidate.match_id, admitted_at.isoformat()), available_at_utc=admitted_at, ingested_at_utc=admitted_at)
            results.append(EloRegularTimeResult.model_validate(values))
    return tuple(sorted(results, key=lambda f: (f.kickoff_at_utc, f.ingested_at_utc, f.match_id, f.match_result_id)))


def scoped_facts_root(facts):
    return tagged_canonical_sha256("OFP_V2_ADMITTED_FACTS_ROOT", tuple(EloRegularTimeResult.model_validate(f) for f in facts))


class QualificationPayloadV2(SnapshotModel):
    kind: Literal["QUALIFICATION"] = "QUALIFICATION"
    subject: ScopeQualificationSubjectV2
    subject_hash: Digest
    review: LocalReviewEvidenceV1
    authority: LocalReviewEvidenceV1
    admitted_at_utc: UtcDateTime
    facts: tuple[EloRegularTimeResult, ...]
    facts_root: Digest

    @model_validator(mode="after")
    def projection(self):
        require(self.subject_hash == self.subject.content_hash and self.facts == admitted_results(self.subject.profile, self.admitted_at_utc)
            and self.facts_root == scoped_facts_root(self.facts), "SCOPED_ADMISSION_PROJECTION_MISMATCH")
        return self


class ScopedParentV2(SnapshotModel):
    artifact_id: Label
    artifact_hash: Digest
    kind: Label
    instance_id: str


class InstanceSubjectV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_TARGET_REVIEW_V2"] = "OPENFOOTBALL_SCOPED_TARGET_REVIEW_V2"
    scope: ScopeIdentityV2
    qualification: ScopedParentV2
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    targets: tuple[OpenFootballProductionTargetV1, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def census(self):
        ids = tuple(t.match_id for t in self.targets)
        require(self.qualification.kind == "QUALIFICATION" and self.qualification.instance_id == ""
            and ids == tuple(sorted(set(ids))) and self.kickoff_from_utc < self.kickoff_to_utc, "SCOPED_TARGET_CENSUS_INVALID")
        require(all(t.competition_id == self.scope.competition_id and self.kickoff_from_utc <= t.kickoff_at_utc < self.kickoff_to_utc
            for t in self.targets), "SCOPED_TARGET_COMPETITION_OR_WINDOW_MISMATCH")
        return self


class InstancePayloadV2(SnapshotModel):
    kind: Literal["INSTANCE"] = "INSTANCE"
    subject: InstanceSubjectV2
    review: LocalReviewEvidenceV1
    authority: LocalReviewEvidenceV1
    logical_key: Digest
    cutoff_at_utc: UtcDateTime
    implementation_revision: Label
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH


class ReservationPayloadV2(SnapshotModel):
    kind: Literal["PILOT_RESERVATION"] = "PILOT_RESERVATION"
    request_key: Label
    request_hash: Digest
    attempt: Literal[1] = 1


class FailurePayloadV2(SnapshotModel):
    kind: Literal["PILOT_FAILURE"] = "PILOT_FAILURE"
    error_type: Label
    attempt: Literal[1] = 1


class PilotPayloadV2(SnapshotModel):
    kind: Literal["PILOT_REPORT"] = "PILOT_REPORT"
    state: EloBaselineState
    facts_root: Digest
    window_hash: Digest
    implementation_revision: Label
    historical_validation: Literal["UNAVAILABLE_UNPROVEN_HISTORICAL_VERSION_TIME"] = "UNAVAILABLE_UNPROVEN_HISTORICAL_VERSION_TIME"


class AttestationPayloadV2(SnapshotModel):
    kind: Literal["PILOT_ATTESTATION"] = "PILOT_ATTESTATION"
    state_hash: Digest
    training_data_hash: Digest
    attempt: Literal[1] = 1
    purpose: Literal["STRUCTURAL_REPLAY_NOT_MODEL_APPROVAL"] = "STRUCTURAL_REPLAY_NOT_MODEL_APPROVAL"


class ManifestPayloadV2(SnapshotModel):
    kind: Literal["MANIFEST"] = "MANIFEST"
    facts_root: Digest
    candidate_facts_root: Digest
    mapping_root: Digest
    window_hash: Digest
    exceptions_root: Digest
    source_record_count: int = Field(strict=True, gt=0)
    fact_count: int = Field(strict=True, gt=0)
    exception_count: int = Field(strict=True, ge=0)
    cutoff_at_utc: UtcDateTime
    target_match_ids: tuple[Label, ...]
    state_hash: Digest
    training_data_hash: Digest
    implementation_revision: Label
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH


class ScopedModelApprovalV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_MODEL_APPROVAL_V2"] = "OPENFOOTBALL_SCOPED_MODEL_APPROVAL_V2"
    scope: ScopeIdentityV2
    instance_id: Label
    manifest: ScopedParentV2
    facts_root: Digest
    state_hash: Digest
    training_data_hash: Digest
    target_match_ids: tuple[Label, ...]
    cutoff_at_utc: UtcDateTime
    implementation_revision: Label
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    selection_policy: Literal["NO_ROI_MODEL_SELECTION"] = "NO_ROI_MODEL_SELECTION"
    parameter_policy: Literal["NO_PARAMETER_TUNING"] = "NO_PARAMETER_TUNING"

    @model_validator(mode="after")
    def time(self):
        require(self.manifest.kind == "MANIFEST" and self.manifest.instance_id == self.instance_id
            and self.cutoff_at_utc < self.effective_at_utc < self.expires_at_utc, "SCOPED_MODEL_APPROVAL_INVALID")
        return self


class ApprovalPayloadV2(SnapshotModel):
    kind: Literal["APPROVAL"] = "APPROVAL"
    subject: ScopedModelApprovalV2
    review: LocalReviewEvidenceV1
    authority: LocalReviewEvidenceV1


class ReleasePayloadV2(SnapshotModel):
    kind: Literal["RELEASE"] = "RELEASE"
    state: EloBaselineState
    facts_root: Digest
    implementation_revision: Label


class TargetPayloadV2(SnapshotModel):
    kind: Literal["TARGET_PLAN"] = "TARGET_PLAN"
    targets: tuple[OpenFootballProductionTargetV1, ...]
    live_input_proof: tuple[dict[str, JsonValue], ...]


class StateBindingPayloadV2(SnapshotModel):
    kind: Literal["STATE_BINDING"] = "STATE_BINDING"
    model_state_id: Label
    state_hash: Digest
    training_data_hash: Digest
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH
    authority_hash: Digest
    target_match_ids: tuple[Label, ...]
    evaluations: tuple[dict[str, JsonValue], ...]


PayloadV2 = Annotated[QualificationPayloadV2 | InstancePayloadV2 | ReservationPayloadV2 | FailurePayloadV2 | PilotPayloadV2
    | AttestationPayloadV2 | ManifestPayloadV2 | ApprovalPayloadV2 | ReleasePayloadV2 | TargetPayloadV2 | StateBindingPayloadV2,
    Field(discriminator="kind")]
PARENTS = {
    "QUALIFICATION": (), "INSTANCE": ("QUALIFICATION",),
    "PILOT_RESERVATION": ("INSTANCE", "QUALIFICATION"), "PILOT_FAILURE": ("INSTANCE", "PILOT_RESERVATION", "QUALIFICATION"),
    "PILOT_REPORT": ("INSTANCE", "PILOT_RESERVATION", "QUALIFICATION"),
    "PILOT_ATTESTATION": ("INSTANCE", "PILOT_REPORT", "PILOT_RESERVATION"),
    "MANIFEST": ("INSTANCE", "PILOT_ATTESTATION", "PILOT_REPORT", "QUALIFICATION"),
    "APPROVAL": ("INSTANCE", "MANIFEST", "QUALIFICATION"),
    "RELEASE": ("APPROVAL", "INSTANCE", "MANIFEST", "PILOT_REPORT", "QUALIFICATION"),
    "TARGET_PLAN": ("INSTANCE", "RELEASE"), "STATE_BINDING": ("APPROVAL", "INSTANCE", "QUALIFICATION", "RELEASE", "TARGET_PLAN"),
}


class ScopedArtifactV2(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SCOPED_ARTIFACT_V2"] = "OPENFOOTBALL_SCOPED_ARTIFACT_V2"
    artifact_id: Label
    artifact_hash: Digest
    owner: ScopeIdentityV2
    instance_id: str
    recorded_at_utc: UtcDateTime
    parents: tuple[ScopedParentV2, ...]
    payload: PayloadV2

    @model_validator(mode="after")
    def seal(self):
        body = self.model_dump(mode="json", exclude={"artifact_id", "artifact_hash"})
        digest = tagged_canonical_sha256(self.schema_version, body)
        require(self.artifact_hash == digest and self.artifact_id == stable_id(self.schema_version, digest), "SCOPED_ARTIFACT_SEAL_MISMATCH")
        require(tuple(p.kind for p in self.parents) == PARENTS[self.payload.kind]
            and len({p.artifact_id for p in self.parents}) == len(self.parents), "SCOPED_EXACT_TYPED_PARENTS_REQUIRED")
        require((self.instance_id == "") == (self.payload.kind == "QUALIFICATION"), "SCOPED_INSTANCE_KIND_MISMATCH")
        require(all(p.instance_id == ("" if p.kind == "QUALIFICATION" else self.instance_id) for p in self.parents), "SCOPED_PARENT_INSTANCE_MISMATCH")
        if self.payload.kind == "QUALIFICATION":
            require(self.owner == ScopeIdentityV2.of(self.payload.subject.profile)
                and self.recorded_at_utc == self.payload.admitted_at_utc, "SCOPED_QUALIFICATION_OWNER_MISMATCH")
        if self.payload.kind == "INSTANCE":
            require(self.payload.subject.scope == self.owner and self.payload.cutoff_at_utc == self.recorded_at_utc
                and self.instance_id == stable_id("OFP_BOOTSTRAP_INSTANCE_V2", self.payload.logical_key), "SCOPED_INSTANCE_IDENTITY_MISMATCH")
        return self

    @classmethod
    def freeze(cls, *, owner, instance_id, recorded_at_utc, parents, payload):
        import json
        body = dict(schema_version=cls.model_fields["schema_version"].default, owner=ScopeIdentityV2.model_validate(owner),
            instance_id=instance_id, recorded_at_utc=recorded_at_utc.isoformat().replace("+00:00", "Z"),
            parents=tuple(sorted(parents, key=lambda p: p.kind)), payload=payload)
        body = json.loads(canonical_json(body))
        digest = tagged_canonical_sha256(body["schema_version"], body)
        return cls.model_validate(body | dict(artifact_id=stable_id(body["schema_version"], digest), artifact_hash=digest))

    def reference(self):
        return ScopedParentV2(artifact_id=self.artifact_id, artifact_hash=self.artifact_hash, kind=self.payload.kind, instance_id=self.instance_id)


def instance_identity(profile, targets):
    key = logical_bootstrap_key(profile, tuple(t.match_id for t in targets))
    return stable_id("OFP_BOOTSTRAP_INSTANCE_V2", key), key
