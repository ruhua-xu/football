"""Additive OpenFootball preparation contracts, never a V1 admission or grant."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from football_system.domain.common import DomainModel, UtcDateTime
from football_system.domain.training_admission import tagged_canonical_sha256

COMMIT = "40b3e1b7391932d133287115106304444bf297e1"
REPOSITORY = "openfootball/football.json"
TZIF_SHA256 = "a7fd9932d785d4d690900b834c3563c1810c1cf2e01711bcc0926af6c0767cb7"
PINNED_FILES = {
    "2024-25/de.1.json": (88232, "f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3"),
    "2025-26/de.1.json": (88247, "17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672"),
    "README.md": (6330, "aadc464ae476ba4d5467785f6f2be435d1a583f589f4b2b0617fdf86fc18e29c"),
    "LICENSE.md": (6555, "36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673"),
}
SEASONS = {"2024-25/de.1.json": "2024/25", "2025-26/de.1.json": "2025/26"}
FileName = Literal["2024-25/de.1.json", "2025-26/de.1.json", "README.md", "LICENSE.md"]
Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")]


class SnapshotModel(DomainModel):
    model_config = ConfigDict(str_strip_whitespace=False, revalidate_instances="always", validate_default=True)

    @model_validator(mode="before")
    @classmethod
    def untrusted_instances(cls, value):
        # model_copy/model_construct are not validation or authority boundaries.
        return value.model_dump(mode="python") if isinstance(value, BaseModel) else value

    @field_validator("retrospective", "production_training_authorized", mode="before", check_fields=False)
    @classmethod
    def actual_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("EXPLICIT_BOOLEAN_REQUIRED")
        return value

    @property
    def content_hash(self) -> str:
        checked = type(self).model_validate(self)
        return tagged_canonical_sha256(type(self).__name__, checked)


class OpenFootballFileCaptureV1(SnapshotModel):
    url: str
    commit: Literal[COMMIT]
    path: FileName
    bytes: int = Field(strict=True, gt=0)
    sha256: Digest
    request_started_at_utc: UtcDateTime
    capture_at_utc: UtcDateTime

    @model_validator(mode="after")
    def exact_source(self) -> Self:
        if self.url != f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{self.path}":
            raise ValueError("OPENFOOTBALL_EXACT_URL_REQUIRED")
        if (self.bytes, self.sha256) != PINNED_FILES[self.path]:
            raise ValueError("OPENFOOTBALL_EXACT_FILE_HASH_REQUIRED")
        if self.request_started_at_utc > self.capture_at_utc:
            raise ValueError("CAPTURE_TIMELINE_INVALID")
        return self


class OpenFootballAcquisitionManifestV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_ACQUISITION_MANIFEST_V1"] = "OPENFOOTBALL_ACQUISITION_MANIFEST_V1"
    repository: Literal[REPOSITORY]
    commit: Literal[COMMIT]
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    authorization_basis: Literal["USER_STAGE3_LIMITED_ACQUISITION_AND_BOOTSTRAP_PREPARATION"]
    files: tuple[OpenFootballFileCaptureV1, ...]
    sends: Literal[4]
    errors: tuple[()] = ()
    automatic_retries: Literal[0]
    production_training_authorized: Literal[False]
    status: Literal["COMPLETE"]

    @field_validator("files")
    @classmethod
    def complete_files(cls, values):
        if len(values) != 4 or {f.path for f in values} != set(PINNED_FILES):
            raise ValueError("EXACT_FOUR_SOURCE_FILES_REQUIRED")
        return tuple(sorted(values, key=lambda f: f.path))


class OpenFootballAdapterPolicyV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_JSON_ADAPTER_V1"] = "OPENFOOTBALL_JSON_ADAPTER_V1"
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    competition: Literal["BUNDESLIGA"] = "BUNDESLIGA"
    competition_type: Literal["DOMESTIC_LEAGUE"] = "DOMESTIC_LEAGUE"
    stage: Literal["REGULAR_LEAGUE_MATCHES"] = "REGULAR_LEAGUE_MATCHES"
    timezone: Literal["Europe/Berlin"]  # Required, never the host/local timezone.
    tzdata_version: Literal["2025.2"]
    iana_version: Literal["2025b"]
    tzif_sha256: Literal[TZIF_SHA256]
    date_format: Literal["YYYY-MM-DD"] = "YYYY-MM-DD"
    time_format: Literal["HH:MM"] = "HH:MM"
    source_time_precision: Literal["MINUTE"] = "MINUTE"
    seconds_policy: Literal["EXPLICIT_HH_MM_TO_HH_MM_00"] = "EXPLICIT_HH_MM_TO_HH_MM_00"
    dst_fold_policy: Literal["REJECT_AMBIGUOUS"] = "REJECT_AMBIGUOUS"
    dst_gap_policy: Literal["REJECT_NONEXISTENT"] = "REJECT_NONEXISTENT"
    score_ft_policy: Literal["REGULAR_LEAGUE_FT_CANDIDATE_REQUIRES_REVIEW"] = "REGULAR_LEAGUE_FT_CANDIDATE_REQUIRES_REVIEW"
    direct_score_array_policy: Literal["SOURCE_SCHEMA_EXCEPTION"] = "SOURCE_SCHEMA_EXCEPTION"
    abnormal_status_policy: Literal["REJECT"] = "REJECT"


class OpenFootballHistoricalValidationV1(SnapshotModel):
    walk_forward_historical_availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    historical_brier: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    historical_logloss: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    historical_calibration: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["UNPROVEN_HISTORICAL_VERSION_TIME"] = "UNPROVEN_HISTORICAL_VERSION_TIME"
    metrics: None = None


class OpenFootballCurrentSnapshotScopeV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_CURRENT_SNAPSHOT_SCOPE_V1"] = "OPENFOOTBALL_CURRENT_SNAPSHOT_SCOPE_V1"
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    competition: Literal["BUNDESLIGA"] = "BUNDESLIGA"
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    retrospective: Literal[True] = True
    evidence_basis: Literal["CURRENT_SNAPSHOT_OBSERVED"] = "CURRENT_SNAPSHOT_OBSERVED"
    acquisition: OpenFootballAcquisitionManifestV1
    adapter_policy: OpenFootballAdapterPolicyV1
    candidate_seasons: tuple[Literal["2024/25", "2025/26"], ...]
    training_window: None = None
    status: Literal["CANDIDATE_NOT_ADMITTED"] = "CANDIDATE_NOT_ADMITTED"
    production_training_authorized: Literal[False] = False

    @field_validator("candidate_seasons")
    @classmethod
    def candidates_not_window(cls, values):
        if len(values) != 2 or set(values) != set(SEASONS.values()):
            raise ValueError("EXACT_CANDIDATE_SEASONS_REQUIRED")
        return tuple(sorted(values))


class OpenFootballSourceRightsCandidateV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SOURCE_RIGHTS_CANDIDATE_V1"] = "OPENFOOTBALL_SOURCE_RIGHTS_CANDIDATE_V1"
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    repository: Literal[REPOSITORY] = REPOSITORY
    commit: Literal[COMMIT] = COMMIT
    license: Literal["CC0-1.0"] = "CC0-1.0"
    license_sha256: Literal["36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673"] = PINNED_FILES["LICENSE.md"][1]
    readme_sha256: Literal["aadc464ae476ba4d5467785f6f2be435d1a583f589f4b2b0617fdf86fc18e29c"] = PINNED_FILES["README.md"][1]
    candidate_uses: tuple[str, ...] = (
        "ACQUIRE", "STORE_LOCAL", "NORMALIZE", "INTERNAL_RESEARCH", "MODEL_TRAINING",
        "DERIVED_STATE_RETENTION", "AUDIT_RETENTION",
    )
    status: Literal["CANDIDATE_NOT_APPROVED"] = "CANDIDATE_NOT_APPROVED"
    trusted_authority_pins: None = None
    review_artifact: None = None
    production_training_authorized: Literal[False] = False

    @field_validator("candidate_uses")
    @classmethod
    def exact_uses(cls, values):
        expected = cls.model_fields["candidate_uses"].default
        if len(values) != len(expected) or set(values) != set(expected):
            raise ValueError("RIGHTS_CANDIDATE_USES_MISMATCH")
        return tuple(sorted(values))


class OpenFootballMappingEntryV1(SnapshotModel):
    kind: Literal["TEAM", "COMPETITION", "SEASON"]
    source_label: Label
    canonical_id: Label | None = None
    identity_evidence_reference: Label | None = None
    identity_evidence_sha256: Digest | None = None

    @model_validator(mode="after")
    def explicit_mapping(self) -> Self:
        values = (self.canonical_id, self.identity_evidence_reference, self.identity_evidence_sha256)
        if any(v is not None for v in values) and not all(v is not None for v in values):
            raise ValueError("CANONICAL_IDENTITY_EVIDENCE_REQUIRED")
        if self.kind == "TEAM" and self.canonical_id == self.source_label:
            raise ValueError("TEAM_NAME_IS_NOT_INTERNAL_TEAM_ID")
        return self


class OpenFootballCanonicalMappingPlanV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_CANONICAL_MAPPING_PLAN_V1"] = "OPENFOOTBALL_CANONICAL_MAPPING_PLAN_V1"
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    scope_hash: Digest
    entries: tuple[OpenFootballMappingEntryV1, ...]
    status: Literal["CANDIDATE_REQUIRES_REVIEW"] = "CANDIDATE_REQUIRES_REVIEW"

    @field_validator("entries")
    @classmethod
    def stable_entries(cls, values):
        keys = [(v.kind, v.source_label) for v in values]
        if len(keys) != len(set(keys)):
            raise ValueError("DUPLICATE_MAPPING_ENTRY")
        return tuple(sorted(values, key=lambda v: (v.kind, v.source_label)))

    def require_explicit_identity(self, kind: str, source_label: str) -> str:
        checked = type(self).model_validate(self)
        for entry in checked.entries:
            if (entry.kind, entry.source_label) == (kind, source_label) and entry.canonical_id is not None:
                return entry.canonical_id
        raise ValueError("IDENTITY_UNRESOLVED")


class OpenFootballRecordProvenanceV1(SnapshotModel):
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    source_commit: Literal[COMMIT] = COMMIT
    source_filename: Literal["2024-25/de.1.json", "2025-26/de.1.json"]
    source_file_sha256: Digest
    original_record_pointer: Annotated[str, Field(pattern=r"^/matches/(0|[1-9][0-9]*)$")]
    original_record_sha256: Digest
    capture_at_utc: UtcDateTime
    local_date_text: str | None
    local_time_text: str | None
    adapter_policy_hash: Digest
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    retrospective: Literal[True] = True
    evidence_basis: Literal["CURRENT_SNAPSHOT_OBSERVED"] = "CURRENT_SNAPSHOT_OBSERVED"
    provider_publication_at_utc: None = None
    provider_finalized_at_utc: None = None
    provider_time_status: Literal["UNKNOWN"] = "UNKNOWN"
