"""Typed provider-independent evidence. Unknown claims never become confirmed facts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import Hash, MarketArtifact, MultiMarketModel, Probability


class FactCategory(StrEnum):
    LINEUP = "LINEUP"
    EXPECTED_LINEUP = "EXPECTED_LINEUP"
    INJURY = "INJURY"
    SUSPENSION = "SUSPENSION"
    SCHEDULE = "SCHEDULE"
    REST = "REST"
    FORM = "FORM"
    MOTIVATION = "MOTIVATION"
    ODDS_CONTEXT = "ODDS_CONTEXT"
    OTHER_VERIFIED_FACT = "OTHER_VERIFIED_FACT"


class AssertionClass(StrEnum):
    FACT = "FACT"
    ANALYSIS = "ANALYSIS"
    SPECULATION = "SPECULATION"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class LineupStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    EXPECTED = "EXPECTED"
    CONFIRMED = "CONFIRMED"


class EvidencePlayerV1(MultiMarketModel):
    player_id: Identifier
    team_id: Identifier
    name: str | None = Field(default=None, max_length=160)
    position: str | None = Field(default=None, max_length=80)
    jersey_number: int | None = Field(default=None, ge=0, le=999, strict=True)


class LineupFactV1(MultiMarketModel):
    category: Literal["LINEUP", "EXPECTED_LINEUP"]
    team_id: Identifier
    status: LineupStatus
    starting_xi: tuple[EvidencePlayerV1, ...] = Field(default=(), max_length=11)
    bench: tuple[EvidencePlayerV1, ...] = Field(default=(), max_length=30)
    formation: str | None = Field(default=None, max_length=80)
    confirmation_reference: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def roster(self):
        players = (*self.starting_xi, *self.bench)
        if len({p.player_id for p in players}) != len(players) or any(p.team_id != self.team_id for p in players):
            raise ValueError("LINEUP_PLAYER_TEAM_BINDING")
        if self.status == LineupStatus.UNKNOWN and (players or self.formation is not None):
            raise ValueError("UNKNOWN_LINEUP_CANNOT_ASSERT_ROSTER")
        if self.status == LineupStatus.CONFIRMED and (len(self.starting_xi) != 11 or not self.confirmation_reference or self.category != "LINEUP"):
            raise ValueError("CONFIRMED_LINEUP_REQUIRES_EXPLICIT_COMPLETE_PROOF")
        return self


class AbsenceRecordV1(MultiMarketModel):
    player: EvidencePlayerV1
    status: Literal["ACTIVE", "RESOLVED", "UNKNOWN"]
    reason_category: str | None = Field(default=None, max_length=160)
    reason: str | None = Field(default=None, max_length=1000)
    starts_at_utc: UtcDateTime | None = None
    expected_return_at_utc: UtcDateTime | None = None
    ends_at_utc: UtcDateTime | None = None
    source_timestamp_utc: UtcDateTime | None = None
    available_at_utc: UtcDateTime | None = None
    confidence: Probability

    @model_validator(mode="after")
    def interval(self):
        if self.starts_at_utc and self.ends_at_utc and self.starts_at_utc > self.ends_at_utc:
            raise ValueError("ABSENCE_INTERVAL_REVERSED")
        if self.status == "ACTIVE" and (not self.reason_category or not self.reason):
            raise ValueError("ACTIVE_ABSENCE_REQUIRES_REASON")
        if self.source_timestamp_utc and self.available_at_utc and self.source_timestamp_utc > self.available_at_utc:
            raise ValueError("ABSENCE_AVAILABILITY_BEFORE_SOURCE")
        return self


class AbsenceFactV1(MultiMarketModel):
    category: Literal["INJURY", "SUSPENSION"]
    team_id: Identifier
    knowledge_status: Literal["UNKNOWN", "RECORDS_AVAILABLE", "NONE_REPORTED"]
    as_of_at_utc: UtcDateTime
    records: tuple[AbsenceRecordV1, ...] = Field(default=(), max_length=64)
    explicit_none_reference: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def knowledge(self):
        if self.knowledge_status == "RECORDS_AVAILABLE" and not self.records:
            raise ValueError("EMPTY_RECORDS_ARE_NOT_ABSENCE_PROOF")
        if self.knowledge_status != "RECORDS_AVAILABLE" and self.records:
            raise ValueError("ABSENCE_KNOWLEDGE_CONFLICT")
        if self.knowledge_status == "NONE_REPORTED" and not self.explicit_none_reference:
            raise ValueError("EMPTY_COLLECTION_IS_NOT_NO_ABSENCES")
        if len({r.player.player_id for r in self.records}) != len(self.records) or any(r.player.team_id != self.team_id for r in self.records):
            raise ValueError("ABSENCE_PLAYER_TEAM_BINDING")
        if any((r.available_at_utc and r.available_at_utc > self.as_of_at_utc)
               or (r.source_timestamp_utc and r.source_timestamp_utc > self.as_of_at_utc)
               or (r.status == "ACTIVE" and r.starts_at_utc and r.starts_at_utc > self.as_of_at_utc) for r in self.records):
            raise ValueError("FUTURE_ABSENCE_RECORD")
        if self.category == "SUSPENSION" and any(r.status == "ACTIVE" and not re_is_ban(r.reason_category) for r in self.records):
            raise ValueError("SUSPENSION_REQUIRES_EXPLICIT_BAN_CATEGORY")
        if self.category == "INJURY" and any(re_is_ban(r.reason_category) for r in self.records):
            raise ValueError("BAN_IS_NOT_INJURY")
        return self


def re_is_ban(category):
    return category in {"SUSPENSION", "BAN", "YELLOW_CARD_BAN", "RED_CARD_BAN", "DISCIPLINARY_BAN"}


class ScheduleFixtureV1(MultiMarketModel):
    match_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    status: Literal["SCHEDULED", "FINISHED", "POSTPONED", "CANCELLED", "RESCHEDULED", "UNKNOWN"]
    known_at_utc: UtcDateTime
    previous_kickoff_at_utc: UtcDateTime | None = None
    source_version_id: Identifier | None = None

    @model_validator(mode="after")
    def identity(self):
        if self.home_team_id == self.away_team_id or (self.status == "RESCHEDULED" and self.previous_kickoff_at_utc is None):
            raise ValueError("SCHEDULE_IDENTITY_OR_RESCHEDULE_PROOF_MISSING")
        return self


class ScheduleFactV1(MultiMarketModel):
    category: Literal["SCHEDULE", "REST"]
    team_id: Identifier
    as_of_at_utc: UtcDateTime
    window_start_utc: UtcDateTime
    window_end_utc: UtcDateTime
    fixtures: tuple[ScheduleFixtureV1, ...] = Field(max_length=64)
    coverage: Literal["PROVIDER_SCOPE_ONLY", "VERIFIED_DECLARED_COMPETITIONS"]
    competition_scope: tuple[Identifier, ...] = Field(min_length=1, max_length=16)
    target_fixture_id: Identifier | None = None
    previous_fixture_id: Identifier | None = None
    next_fixture_id: Identifier | None = None
    rest_days: int | None = Field(default=None, ge=0, strict=True)
    rest_policy: Literal["KICKOFF_FULL_24H_DAYS_V1"] = "KICKOFF_FULL_24H_DAYS_V1"

    @model_validator(mode="after")
    def window(self):
        if self.window_start_utc >= self.window_end_utc or len({f.match_id for f in self.fixtures}) != len(self.fixtures):
            raise ValueError("INVALID_SCHEDULE_WINDOW")
        if any(f.known_at_utc > self.as_of_at_utc or not self.window_start_utc <= f.kickoff_at_utc <= self.window_end_utc
               or self.team_id not in {f.home_team_id, f.away_team_id} for f in self.fixtures):
            raise ValueError("FUTURE_OR_UNBOUND_SCHEDULE_FACT")
        if any(f.status == "FINISHED" and f.kickoff_at_utc >= self.as_of_at_utc for f in self.fixtures):
            raise ValueError("FUTURE_FINISHED_FIXTURE")
        if self.category == "REST":
            known = {f.match_id: f for f in self.fixtures if f.status in {"SCHEDULED", "FINISHED", "RESCHEDULED"}}
            target = known.get(self.target_fixture_id)
            if target is None:
                raise ValueError("REST_TARGET_REQUIRED")
            before = sorted((f for f in known.values() if f.kickoff_at_utc < target.kickoff_at_utc), key=lambda f: (f.kickoff_at_utc, f.match_id))
            after = sorted((f for f in known.values() if f.kickoff_at_utc > target.kickoff_at_utc), key=lambda f: (f.kickoff_at_utc, f.match_id))
            previous = before[-1] if before else None
            following = after[0] if after else None
            if (self.previous_fixture_id != (previous.match_id if previous else None)
                    or self.next_fixture_id != (following.match_id if following else None)
                    or self.rest_days != ((target.kickoff_at_utc-previous.kickoff_at_utc).days if previous else None)):
                raise ValueError("REST_DERIVATION_MISMATCH")
        return self


class FormFactV1(MultiMarketModel):
    category: Literal["FORM"] = "FORM"
    team_id: Identifier
    as_of_at_utc: UtcDateTime
    window_start_utc: UtcDateTime
    window_end_utc: UtcDateTime
    result_ids: tuple[Identifier, ...] = Field(max_length=64)
    location: Literal["ANY", "HOME", "AWAY"] = "ANY"

    @model_validator(mode="after")
    def window(self):
        if not self.window_start_utc < self.window_end_utc <= self.as_of_at_utc or len(set(self.result_ids)) != len(self.result_ids):
            raise ValueError("FORM_WINDOW_OR_RESULT_COVERAGE_INVALID")
        return self


class DescriptiveFactV1(MultiMarketModel):
    category: Literal["MOTIVATION", "ODDS_CONTEXT", "OTHER_VERIFIED_FACT"]
    statement: str = Field(min_length=1, max_length=2000)
    supporting_references: tuple[str, ...] = Field(min_length=1, max_length=16)
    inference_basis: Literal["DIRECT_EVIDENCE", "DECLARED_ANALYSIS", "DECLARED_SPECULATION"]


StructuredFactV1 = Annotated[LineupFactV1 | AbsenceFactV1 | ScheduleFactV1 | FormFactV1 | DescriptiveFactV1, Field(discriminator="category")]


class ManualVerifiedImportV1(MultiMarketModel):
    schema_version: Literal["MANUAL_VERIFIED_IMPORT_V1"] = "MANUAL_VERIFIED_IMPORT_V1"
    match_id: Identifier
    data_classification: Literal["SYNTHETIC", "REAL_SOURCE_DATA"]
    source_identity: Identifier
    source_reference: str = Field(min_length=1, max_length=1024)
    source_file: str = Field(min_length=1, max_length=512)
    source_hash: Hash
    rights_basis: Literal["SELF_OBSERVED", "REVIEWED_LICENSE"]
    rights_reference: str = Field(min_length=1, max_length=1024)
    retention_until_utc: UtcDateTime
    verified_by: Identifier
    verified_at_utc: UtcDateTime
    captured_at_utc: UtcDateTime
    published_at_utc: UtcDateTime | None = None
    available_at_utc: UtcDateTime
    fact_category: FactCategory
    assertion_class: AssertionClass
    confidence: Probability
    structured_payload: StructuredFactV1

    @model_validator(mode="after")
    def semantics(self):
        if self.structured_payload.category != self.fact_category:
            raise ValueError("FACT_CATEGORY_MISMATCH")
        if (self.published_at_utc and self.published_at_utc > self.available_at_utc) or self.available_at_utc > self.captured_at_utc or self.captured_at_utc > self.verified_at_utc:
            raise ValueError("MANUAL_SOURCE_TIMELINE_INVALID")
        if isinstance(self.structured_payload, DescriptiveFactV1):
            expected = {AssertionClass.FACT: "DIRECT_EVIDENCE", AssertionClass.ANALYSIS: "DECLARED_ANALYSIS", AssertionClass.SPECULATION: "DECLARED_SPECULATION"}
            if self.structured_payload.inference_basis != expected[self.assertion_class]:
                raise ValueError("FACT_ANALYSIS_SPECULATION_MISMATCH")
        elif self.assertion_class != AssertionClass.FACT:
            if isinstance(self.structured_payload, LineupFactV1) and self.structured_payload.status == LineupStatus.CONFIRMED:
                raise ValueError("ANALYSIS_CANNOT_CONFIRM_LINEUP")
        return self


class EvidenceSnapshotV1(MarketArtifact):
    schema_version: Literal["EVIDENCE_SNAPSHOT_V1"] = "EVIDENCE_SNAPSHOT_V1"
    source_type: Literal["MANUAL_VERIFIED_IMPORT_V1"] = "MANUAL_VERIFIED_IMPORT_V1"
    claim: ManualVerifiedImportV1
    ingested_at_utc: UtcDateTime
    clock_basis: Literal["LOCAL_SYSTEM_UTC", "SYNTHETIC_TEST_CLOCK"]
    freshness_status: FreshnessStatus
    assessed_at_utc: UtcDateTime
    freshness_limit_seconds: int = Field(ge=0, strict=True)
    source_payload_hash: Hash

    @model_validator(mode="after")
    def timing(self):
        if (self.claim.verified_at_utc > self.ingested_at_utc or self.assessed_at_utc != self.ingested_at_utc
                or self.claim.retention_until_utc <= self.ingested_at_utc
                or (self.clock_basis == "SYNTHETIC_TEST_CLOCK" and self.claim.data_classification != "SYNTHETIC")):
            raise ValueError("EVIDENCE_RECEIPT_OR_RETENTION_INVALID")
        if self.freshness_status != freshness(self.claim.published_at_utc, self.assessed_at_utc, self.freshness_limit_seconds):
            raise ValueError("FRESHNESS_NOT_SOURCE_TIME_BASED")
        return self


class ProspectiveEvidenceBindingV1(MarketArtifact):
    schema_version: Literal["PROSPECTIVE_EVIDENCE_BINDING_V1"] = "PROSPECTIVE_EVIDENCE_BINDING_V1"
    snapshot: ArtifactRefV1
    football_evidence: ArtifactRefV1
    match_id: Identifier


class EvidenceUseV1(MultiMarketModel):
    snapshot: ArtifactRefV1
    binding: ArtifactRefV1
    football_evidence: ArtifactRefV1
    match_id: Identifier
    category: FactCategory
    assertion_class: AssertionClass
    freshness: FreshnessStatus
    source_published_at_utc: UtcDateTime | None
    source_available_at_utc: UtcDateTime
    trusted_ingested_at_utc: UtcDateTime


def freshness(published_at: datetime | None, cutoff: datetime, limit_seconds: int) -> FreshnessStatus:
    if published_at is None:
        return FreshnessStatus.UNKNOWN
    if published_at > cutoff:
        raise ValueError("FUTURE_SOURCE_PUBLICATION")
    return FreshnessStatus.FRESH if cutoff-published_at <= timedelta(seconds=limit_seconds) else FreshnessStatus.STALE


class ProviderEntitlementV1(MultiMarketModel):
    provider: Identifier
    evidence_reference: str = Field(min_length=1, max_length=1024)
    evidence_sha256: Hash
    source_identity: Identifier
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    permitted_uses: tuple[Literal["PROSPECTIVE_EVIDENCE", "RESULT_OBSERVATION"], ...]

    @model_validator(mode="after")
    def period(self):
        if self.effective_at_utc >= self.expires_at_utc or len(set(self.permitted_uses)) != len(self.permitted_uses):
            raise ValueError("INVALID_ENTITLEMENT_CONTRACT")
        return self


class ProviderCapabilityContractV1(MultiMarketModel):
    schema_version: Literal["PROVIDER_CAPABILITY_CONTRACT_V1"] = "PROVIDER_CAPABILITY_CONTRACT_V1"
    provider: Identifier
    capability: FactCategory | Literal["RESULT_REVISION_CHAIN"]
    status: Literal["UNAVAILABLE", "MANUAL_IMPORT_AVAILABLE"]
    reason: Literal["PROVIDER_NOT_ACTIVATED", "ENTITLEMENT_MISSING_OR_EXPIRED", "SOURCE_REVISION_CHAIN_UNAVAILABLE", "MANUAL_VERIFIED_PATH"]
    http_sends: Literal[0] = 0
    lineup_status: LineupStatus = LineupStatus.UNKNOWN
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN


def provider_capability(provider, capability, at, entitlement=None):
    if provider == "MANUAL_VERIFIED_IMPORT_V1" and capability != "RESULT_REVISION_CHAIN":
        return ProviderCapabilityContractV1(provider=provider, capability=capability, status="MANUAL_IMPORT_AVAILABLE", reason="MANUAL_VERIFIED_PATH")
    reason = "ENTITLEMENT_MISSING_OR_EXPIRED"
    if entitlement is not None and entitlement.provider == provider and entitlement.effective_at_utc <= at < entitlement.expires_at_utc:
        reason = "SOURCE_REVISION_CHAIN_UNAVAILABLE" if capability == "RESULT_REVISION_CHAIN" else "PROVIDER_NOT_ACTIVATED"
    return ProviderCapabilityContractV1(provider=provider, capability=capability, status="UNAVAILABLE", reason=reason)
