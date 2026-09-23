"""Versioned, fixed-cohort production bindings. No V1 source is reclassified."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from football_system.domain.common import UtcDateTime, stable_id
from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.openfootball_snapshot import COMMIT, PINNED_FILES, Digest, Label, SnapshotModel
from football_system.domain.production_release import (
    EloTrainingWindowV1, EloTrainingWindowContentV1, ProviderSeasonRefV1, TrainingSeasonV1,
)
from football_system.domain.services.elo_baseline import EloBaselineConfig, EloRegularTimeResult
from football_system.domain.training_admission import tagged_canonical_sha256

SOURCE_ID = "OPENFOOTBALL_FOOTBALL_JSON_" + COMMIT
BOOTSTRAP_ID = "OPENFOOTBALL_PRODUCTION_BOOTSTRAP_2026_V1"
CONFIG_HASH = "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
EXCEPTIONS = {
    "2024-25/de.1.json": {"/matches/120": "ABNORMAL_MATCH_STATUS_AWARDED"},
    "2025-26/de.1.json": {f"/matches/{i}": "REPORTED_SCORE_PERIOD_UNPROVEN" for i in (8, 44, 53, 75, 99, 127, 129, 133, 153, 159, 223, 274)},
}
SEASON_ROLES = (("2024/25", "WARMUP"), ("2025/26", "PILOT_TARGET"), ("2026/27", "PRODUCTION_TARGET"))
TEAM_LABELS = (
    "1. FC Heidenheim 1846", "1. FC Köln", "1. FC Union Berlin", "1. FSV Mainz 05",
    "Bayer 04 Leverkusen", "Borussia Dortmund", "Borussia Mönchengladbach", "Eintracht Frankfurt",
    "FC Augsburg", "FC Bayern München", "FC St. Pauli 1910", "Hamburger SV", "Holstein Kiel",
    "RB Leipzig", "SC Freiburg", "SV Werder Bremen", "TSG 1899 Hoffenheim", "VfB Stuttgart",
    "VfL Bochum 1848", "VfL Wolfsburg",
)
USES = ("ACQUIRE", "STORE_LOCAL", "NORMALIZE", "INTERNAL_RESEARCH", "MODEL_TRAINING", "DERIVED_STATE_RETENTION", "AUDIT_RETENTION")


def require(value, reason):
    if not value:
        raise ValueError(reason)


def canonical_id(kind: str, label: str) -> str:
    """Allocate NEW opaque catalog IDs by an explicit, reviewed namespace rule.

    This is not fuzzy resolution of an existing catalog or an upstream team ID.
    """
    allowed = {"TEAM": set(TEAM_LABELS), "COMPETITION": {"Deutsche Bundesliga"}, "SEASON": {s for s, _ in SEASON_ROLES}}
    require(kind in allowed and label in allowed[kind], "IDENTITY_UNRESOLVED")
    return stable_id("OPENFOOTBALL_EXPLICIT_CANONICAL_V1", kind, label)


def match_id(filename: str, pointer: str) -> str:
    return stable_id("OPENFOOTBALL_CANONICAL_MATCH_V1", COMMIT, filename, pointer)


def fixed_window() -> EloTrainingWindowV1:
    return EloTrainingWindowV1.freeze(content_payload=EloTrainingWindowContentV1(
        competition_id=canonical_id("COMPETITION", "Deutsche Bundesliga"),
        seasons=tuple(TrainingSeasonV1(season_sequence=i, season_id=canonical_id("SEASON", season), role=role,
            provider_seasons=(ProviderSeasonRefV1(source_id=SOURCE_ID if i < 2 else "OPERATOR_ZERO_FACT_TARGET_DECLARATION",
                provider_code="OPENFOOTBALL" if i < 2 else "CANONICAL_CATALOG",
                provider_competition_id="de.1", provider_season_id=season),))
            for i, (season, role) in enumerate(SEASON_ROLES))))


def fixed_exceptions() -> tuple[dict, ...]:
    return tuple(dict(source_filename=name, record_pointer=pointer, reason=reason)
        for name, values in sorted(EXCEPTIONS.items()) for pointer, reason in sorted(values.items()))


class OpenFootballSourceRightsPayloadV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_SOURCE_RIGHTS_PAYLOAD_V1"] = "OPENFOOTBALL_SOURCE_RIGHTS_PAYLOAD_V1"
    source_id: Literal[SOURCE_ID] = SOURCE_ID
    provider_code: Literal["OPENFOOTBALL"] = "OPENFOOTBALL"
    repository: Literal["openfootball/football.json"] = "openfootball/football.json"
    commit: Literal[COMMIT] = COMMIT
    license: Literal["CC0-1.0"] = "CC0-1.0"
    license_sha256: Literal["36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673"] = PINNED_FILES["LICENSE.md"][1]
    allowed_uses: tuple[str, ...] = USES
    raw_retention: Literal["PERMANENT_ALL_612_SOURCE_RECORDS"] = "PERMANENT_ALL_612_SOURCE_RECORDS"
    derived_state_retention: Literal["REQUIRES_EXACT_MODEL_APPROVAL"] = "REQUIRES_EXACT_MODEL_APPROVAL"
    audit_retention: Literal["INDEFINITE_PRIVATE_PERSONAL_SYSTEM"] = "INDEFINITE_PRIVATE_PERSONAL_SYSTEM"
    model_training_rule: Literal["PILOT_ALLOWED_RELEASE_REQUIRES_EXACT_APPROVAL"] = "PILOT_ALLOWED_RELEASE_REQUIRES_EXACT_APPROVAL"
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        require(self.allowed_uses == USES, "SOURCE_RIGHTS_USES_MISMATCH")
        require(self.effective_at_utc < self.expires_at_utc, "SOURCE_RIGHTS_TIME_SCOPE_INVALID")
        return self


class OpenFootballCanonicalEntryV1(SnapshotModel):
    kind: Literal["TEAM", "COMPETITION", "SEASON"]
    source_label: Label
    canonical_id: Label
    mapping_method: Literal["EXPLICIT_NEW_CANONICAL_UUID5_V1"] = "EXPLICIT_NEW_CANONICAL_UUID5_V1"
    review_evidence_reference: Label
    review_evidence_sha256: Digest
    reviewed_by: Label
    reviewed_at: UtcDateTime

    @model_validator(mode="after")
    def mapping(self) -> Self:
        require(self.canonical_id == canonical_id(self.kind, self.source_label), "EXPLICIT_CANONICAL_MAPPING_MISMATCH")
        return self


class OpenFootballCanonicalReviewV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_CANONICAL_MAPPING_REVIEW_V1"] = "OPENFOOTBALL_CANONICAL_MAPPING_REVIEW_V1"
    entries: tuple[OpenFootballCanonicalEntryV1, ...]

    @model_validator(mode="after")
    def complete(self) -> Self:
        expected = {("TEAM", t) for t in TEAM_LABELS} | {("COMPETITION", "Deutsche Bundesliga")} | {("SEASON", s) for s, _ in SEASON_ROLES}
        keys = tuple((e.kind, e.source_label) for e in self.entries)
        require(keys == tuple(sorted(expected)), "CANONICAL_MAPPING_24_EXACT_ENTRIES_REQUIRED")
        require(len({e.canonical_id for e in self.entries}) == 24, "CANONICAL_MAPPING_ID_COLLISION")
        return self


def canonical_review(*, reviewer: str, reviewed_at: datetime, evidence_reference: str, evidence_sha256: str) -> OpenFootballCanonicalReviewV1:
    pairs = [("TEAM", label) for label in TEAM_LABELS] + [("COMPETITION", "Deutsche Bundesliga")] + [("SEASON", s) for s, _ in SEASON_ROLES]
    return OpenFootballCanonicalReviewV1(entries=tuple(OpenFootballCanonicalEntryV1(kind=kind, source_label=label,
        canonical_id=canonical_id(kind, label), reviewed_by=reviewer, reviewed_at=reviewed_at,
        review_evidence_reference=evidence_reference, review_evidence_sha256=evidence_sha256)
        for kind, label in sorted(pairs)))


class OpenFootballObservedFactV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_OBSERVED_FACT_V1"] = "OPENFOOTBALL_OBSERVED_FACT_V1"
    source_filename: Literal["2024-25/de.1.json", "2025-26/de.1.json"]
    source_file_sha256: Digest
    record_pointer: str = Field(pattern=r"^/matches/(0|[1-9][0-9]*)$")
    record_sha256: Digest
    match_id: Label
    season_id: Label
    home_team_id: Label
    away_team_id: Label
    source_team1: Label
    source_team2: Label
    kickoff_at_utc: UtcDateTime
    home_goals: int = Field(strict=True, ge=0)
    away_goals: int = Field(strict=True, ge=0)
    capture_at_utc: UtcDateTime
    admitted_at_utc: UtcDateTime
    adapter_policy_hash: Digest
    source_data_mode: Literal["SOURCE_TIME_RESEARCH"] = "SOURCE_TIME_RESEARCH"
    retrospective: Literal[True] = True
    evidence_basis: Literal["CURRENT_SNAPSHOT_OBSERVED"] = "CURRENT_SNAPSHOT_OBSERVED"
    provider_publication_at_utc: None = None
    provider_finalized_at_utc: None = None

    @model_validator(mode="after")
    def binding(self) -> Self:
        season = "2024/25" if self.source_filename.startswith("2024-") else "2025/26"
        require(self.source_file_sha256 == PINNED_FILES[self.source_filename][1], "SOURCE_FILE_HASH_MISMATCH")
        require(int(self.record_pointer.rsplit("/", 1)[1]) < 306, "SOURCE_POINTER_OUTSIDE_COHORT")
        require(self.record_pointer not in EXCEPTIONS[self.source_filename], "EXCEPTION_CANNOT_BE_TRAINING_FACT")
        require(self.match_id == match_id(self.source_filename, self.record_pointer), "MATCH_IDENTITY_MISMATCH")
        require(self.season_id == canonical_id("SEASON", season), "SEASON_MEMBERSHIP_MISMATCH")
        require((self.home_team_id, self.away_team_id) == (canonical_id("TEAM", self.source_team1), canonical_id("TEAM", self.source_team2)), "IDENTITY_UNRESOLVED")
        require(self.home_team_id != self.away_team_id, "SELF_MATCH")
        require(self.kickoff_at_utc < self.capture_at_utc <= self.admitted_at_utc, "OBSERVED_TIMELINE_MISMATCH")
        return self

    def to_elo_result(self) -> EloRegularTimeResult:
        value = type(self).model_validate(self)
        return EloRegularTimeResult(match_result_id=stable_id("OPENFOOTBALL_NORMALIZED_RESULT_V1", value.content_hash),
            match_id=value.match_id, season_id=value.season_id, home_team_id=value.home_team_id, away_team_id=value.away_team_id,
            kickoff_at_utc=value.kickoff_at_utc, home_goals=value.home_goals, away_goals=value.away_goals,
            available_at_utc=value.admitted_at_utc, ingested_at_utc=value.admitted_at_utc,
            payload_hash=match_result_payload_sha256(value.home_goals, value.away_goals))


def validate_fact_cohort(facts, *, window, exclusions=(), cutoff=None):
    values = tuple(OpenFootballObservedFactV1.model_validate(f) for f in facts)
    require(window == fixed_window(), "TRAINING_WINDOW_ROLES_OR_ZERO_FACT_TARGET_CHANGED")
    expected = {(name, f"/matches/{i}") for name in EXCEPTIONS for i in range(306) if f"/matches/{i}" not in EXCEPTIONS[name]}
    require(len(values) == 599 and {(f.source_filename, f.record_pointer) for f in values} == expected, "EXACT_599_INCLUDED_FACTS_REQUIRED")
    require(len({f.match_id for f in values}) == 599, "DUPLICATE_MATCH")
    require(not set(exclusions) & {f.match_id for f in values}, "OPERATION_TARGET_IS_TRAINING_FACT")
    if cutoff is not None:
        require(all(f.admitted_at_utc < cutoff for f in values), "ADMISSION_MUST_PRECEDE_CUTOFF")
    return tuple(sorted(values, key=lambda f: (f.kickoff_at_utc, f.admitted_at_utc, f.match_id, f.to_elo_result().match_result_id)))


class OpenFootballProductionTargetV1(SnapshotModel):
    match_id: Label
    competition_id: Label
    season_id: Label
    home_team_id: Label
    away_team_id: Label
    kickoff_at_utc: UtcDateTime
    live_preparation_id: Label
    fixture_observation_id: Label


class OpenFootballModelApprovalPayloadV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_MODEL_APPROVAL_PAYLOAD_V1"] = "OPENFOOTBALL_MODEL_APPROVAL_PAYLOAD_V1"
    bootstrap_id: Literal[BOOTSTRAP_ID] = BOOTSTRAP_ID
    manifest_id: Label
    manifest_hash: Digest
    pilot_report_id: Label
    pilot_report_hash: Digest
    attestation_id: Label
    attestation_hash: Digest
    source_binding_id: Label
    source_binding_hash: Digest
    canonical_mapping_root: Digest
    training_window_hash: Digest
    training_cutoff_at_utc: UtcDateTime
    training_data_hash: Digest
    state_hash: Digest
    implementation_revision: Label
    config_hash: Literal[CONFIG_HASH] = CONFIG_HASH
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1"] = "ELO_THREE_WAY_BASELINE_V1"
    model_version: Literal["1"] = "1"
    raw_records_retention: Literal["PERMANENT_ALL_612_SOURCE_RECORDS"] = "PERMANENT_ALL_612_SOURCE_RECORDS"
    derived_state_retention: Literal["INDEFINITE_PRIVATE_PERSONAL_SYSTEM"]
    audit_retention: Literal["INDEFINITE_PRIVATE_PERSONAL_SYSTEM"]
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    parameter_policy: Literal["NO_PARAMETER_TUNING"] = "NO_PARAMETER_TUNING"
    selection_policy: Literal["NO_ROI_MODEL_SELECTION"] = "NO_ROI_MODEL_SELECTION"

    @model_validator(mode="after")
    def window_and_clock(self) -> Self:
        require(self.training_window_hash == fixed_window().content_hash, "WINDOW_HASH_CHANGED")
        require(self.training_cutoff_at_utc < self.effective_at_utc < self.expires_at_utc, "APPROVAL_TIMELINE_INVALID")
        require(EloBaselineConfig().config_hash == self.config_hash, "FROZEN_ELO_CHANGED")
        return self


class OpenFootballProductionArtifactV1(SnapshotModel):
    schema_version: Literal["OPENFOOTBALL_PRODUCTION_ARTIFACT_V1"] = "OPENFOOTBALL_PRODUCTION_ARTIFACT_V1"
    kind: Literal["DATA_BINDING", "DATA_COMPLETION", "CUTOFF", "PILOT_PLAN", "PILOT_RESERVATION", "PILOT_FAILURE", "PILOT_REPORT", "PILOT_ATTESTATION", "MANIFEST", "APPROVAL", "RELEASE", "TARGET_PLAN", "STATE_BINDING"]
    artifact_id: Label
    artifact_hash: Digest
    parents: tuple[tuple[str, str], ...]
    recorded_at_utc: UtcDateTime
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def seal(self) -> Self:
        body = self.model_dump(mode="json", exclude={"artifact_id", "artifact_hash"})
        digest = tagged_canonical_sha256("OPENFOOTBALL_PRODUCTION_ARTIFACT_V1", body)
        require(self.artifact_hash == digest and self.artifact_id == stable_id("OPENFOOTBALL_PRODUCTION_ARTIFACT_V1", digest), "OPENFOOTBALL_ARTIFACT_SEAL_MISMATCH")
        require(self.parents == tuple(sorted(set(self.parents))), "DUPLICATE_OR_UNORDERED_PARENT")
        return self

    @classmethod
    def freeze(cls, *, kind, payload, parents, recorded_at_utc):
        from football_system.domain.archive import canonical_json
        import json
        body = dict(schema_version="OPENFOOTBALL_PRODUCTION_ARTIFACT_V1", kind=kind,
            payload=json.loads(canonical_json(payload)), parents=tuple(sorted(set(parents))),
            recorded_at_utc=recorded_at_utc.isoformat())
        # Match Pydantic's canonical UTC spelling before hashing.
        body["recorded_at_utc"] = body["recorded_at_utc"].replace("+00:00", "Z")
        digest = tagged_canonical_sha256("OPENFOOTBALL_PRODUCTION_ARTIFACT_V1", body)
        return cls(**body, artifact_hash=digest, artifact_id=stable_id("OPENFOOTBALL_PRODUCTION_ARTIFACT_V1", digest))

    def reference(self):
        return self.artifact_id, self.artifact_hash
