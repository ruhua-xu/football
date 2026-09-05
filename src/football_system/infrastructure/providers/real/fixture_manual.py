from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.identity_catalog import (
    CanonicalFixtureAnchor,
    FixtureIngestionCapture,
    FixtureIngestionRequest,
    FixtureObservation,
    MatchIdentityCatalog,
    MatchIdentityRegistration,
    RegisteredCanonicalMatch,
    RegisteredCompetitionMapping,
    RegisteredTeamAlias,
)
from football_system.application.ports.data_providers import FixtureCaptureProvider
from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_json,
    canonical_payload_sha256,
)
from football_system.domain.common import DomainModel, Identifier, UtcDateTime, stable_id
from football_system.domain.identity import Alias, CanonicalMatchIdentity, CompetitionMapping
from football_system.domain.match import (
    Competition,
    Match,
    MatchStatus,
    ProviderMatchMapping,
    Team,
    TeamType,
)
from football_system.domain.raw_data import (
    ProviderRequestAudit,
    ProviderRequestOutcome,
    ProviderRequestResult,
)
from football_system.infrastructure.files.raw_archive import RawDataArchive
from football_system.infrastructure.files.review_bridge import read_contract_file


REVIEWED_FIXTURE_MANUAL_ARCHIVE_SCHEMA_VERSION = (
    "REVIEWED_FIXTURE_MANUAL_ARCHIVE_V1"
)
REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE = "REVIEWED_FIXTURE_MANUAL"
REVIEWED_FIXTURE_MANUAL_RECONCILIATION_SCHEMA_VERSION = (
    "REVIEWED_FIXTURE_MANUAL_RECONCILIATION_V1"
)
_PROVIDER_NAMESPACE = "fixture"
_LANGUAGE = "und"
_EVIDENCE_SUFFIXES = frozenset(
    {".htm", ".html", ".jpeg", ".jpg", ".pdf", ".png", ".txt", ".webp"}
)
_MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
_EVIDENCE_CHUNK_BYTES = 1024 * 1024


class ReviewedFixtureManualArchiveError(ValueError):
    code = "REVIEWED_FIXTURE_MANUAL_ARCHIVE_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


class ReviewedFixtureManualReviewLevel(StrEnum):
    SELF_REVIEWED = "SELF_REVIEWED"
    INDEPENDENT_REVIEWED = "INDEPENDENT_REVIEWED"


class ReviewedFixtureManualRecord(DomainModel):
    competition_label: Identifier
    season: Identifier
    kickoff_at_utc: UtcDateTime
    home_team_label: Identifier
    away_team_label: Identifier
    competition_type: Identifier
    team_type: TeamType
    source_reference: str = Field(min_length=1, max_length=2048)
    source_artifact_path: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at_utc: UtcDateTime
    entered_by: Identifier
    review_level: ReviewedFixtureManualReviewLevel
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.home_team_label == self.away_team_label:
            raise ValueError("reviewed manual fixture teams must differ")
        if self.reviewed_at_utc < self.captured_at_utc:
            raise ValueError("reviewed manual fixture review cannot predate capture")
        same_reviewer = self.entered_by.casefold() == self.reviewed_by.casefold()
        if (
            self.review_level is ReviewedFixtureManualReviewLevel.SELF_REVIEWED
            and not same_reviewer
        ):
            raise ValueError("self-reviewed manual fixture requires the same reviewer")
        if (
            self.review_level
            is ReviewedFixtureManualReviewLevel.INDEPENDENT_REVIEWED
            and same_reviewer
        ):
            raise ValueError(
                "independently reviewed manual fixture requires a distinct reviewer"
            )
        if self.team_type not in {
            TeamType.CLUB,
            TeamType.NATIONAL,
            TeamType.WOMEN,
        }:
            raise ValueError("reviewed manual fixture team type is unsupported")
        return self


class ReviewedFixtureManualDocument(DomainModel):
    schema_version: Literal["REVIEWED_FIXTURE_MANUAL_ARCHIVE_V1"]
    fixtures: tuple[ReviewedFixtureManualRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        first = self.fixtures[0]
        scope = (
            first.competition_label,
            first.season,
            first.competition_type,
            first.team_type,
        )
        if any(
            (
                item.competition_label,
                item.season,
                item.competition_type,
                item.team_type,
            )
            != scope
            for item in self.fixtures[1:]
        ):
            raise ValueError("reviewed manual fixture archive requires one exact scope")
        identities = tuple(_record_identity(item) for item in self.fixtures)
        if len(identities) != len(set(identities)):
            raise ValueError("reviewed manual fixture archive contains duplicate fixtures")
        if max(item.kickoff_at_utc for item in self.fixtures) - min(
            item.kickoff_at_utc for item in self.fixtures
        ) > timedelta(days=100):
            raise ValueError("reviewed manual fixture windows cannot exceed 100 days")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedReviewedFixtureManualArchive:
    path: Path
    raw: bytes
    document: ReviewedFixtureManualDocument
    document_sha256: str
    evidence_sha256: tuple[str, ...]


class ReviewedFixtureManualIssueReason(StrEnum):
    AMBIGUOUS_COMPETITION = "AMBIGUOUS_COMPETITION"
    AMBIGUOUS_HOME_TEAM = "AMBIGUOUS_HOME_TEAM"
    AMBIGUOUS_AWAY_TEAM = "AMBIGUOUS_AWAY_TEAM"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    CANONICAL_REFERENCE_UNAVAILABLE = "CANONICAL_REFERENCE_UNAVAILABLE"
    HOME_AWAY_CONFLICT = "HOME_AWAY_CONFLICT"
    KICKOFF_CONFLICT = "KICKOFF_CONFLICT"
    COMPETITION_CONFLICT = "COMPETITION_CONFLICT"
    SEASON_CONFLICT = "SEASON_CONFLICT"
    COMPETITION_TYPE_CONFLICT = "COMPETITION_TYPE_CONFLICT"
    TEAM_TYPE_CONFLICT = "TEAM_TYPE_CONFLICT"


class ReviewedFixtureManualReconciliationIssue(DomainModel):
    issue_id: Identifier
    reason: ReviewedFixtureManualIssueReason
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_reference: str = Field(min_length=1, max_length=2048)
    competition_label: Identifier
    season: Identifier
    kickoff_at_utc: UtcDateTime
    home_team_label: Identifier
    away_team_label: Identifier
    competition_type: Identifier
    team_type: TeamType
    source_artifact_path: str = Field(min_length=1, max_length=2048)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_candidate_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        if self.canonical_candidate_ids != tuple(
            sorted(set(self.canonical_candidate_ids))
        ):
            raise ValueError("manual fixture candidates must be unique and sorted")
        return self


class ReviewedFixtureManualReconciliationReport(DomainModel):
    schema_version: Literal["REVIEWED_FIXTURE_MANUAL_RECONCILIATION_V1"] = (
        REVIEWED_FIXTURE_MANUAL_RECONCILIATION_SCHEMA_VERSION
    )
    report_id: Identifier
    archive_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issues: tuple[ReviewedFixtureManualReconciliationIssue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.issues != tuple(sorted(self.issues, key=lambda item: item.issue_id)):
            raise ValueError("manual fixture reconciliation issues must be sorted")
        if len(self.issues) != len({item.issue_id for item in self.issues}):
            raise ValueError("manual fixture reconciliation issue IDs must be unique")
        expected_id = stable_id(
            "reviewed-fixture-manual-reconciliation",
            self.archive_document_sha256,
            *(item.issue_id for item in self.issues),
        )
        if self.report_id != expected_id:
            raise ValueError("manual fixture reconciliation report ID is inconsistent")
        return self


class ReviewedFixtureManualReconciliationError(ValueError):
    code = "REVIEWED_FIXTURE_MANUAL_RECONCILIATION_REQUIRED"

    def __init__(self, report: ReviewedFixtureManualReconciliationReport) -> None:
        self.report = report
        super().__init__(f"{self.code}: {canonical_json(report)}")


@dataclass(frozen=True, slots=True)
class _ResolvedFixture:
    record: ReviewedFixtureManualRecord
    competition: Competition
    home_team: Team
    away_team: Team
    match_id: str
    status: MatchStatus


class ReviewedFixtureManualArchiveProvider(FixtureCaptureProvider):
    provider_code = REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.LIVE,
        provider_code=provider_code,
        provenance=REVIEWED_FIXTURE_MANUAL_ARCHIVE_SCHEMA_VERSION,
        is_mock=False,
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )

    def __init__(
        self,
        archive: VerifiedReviewedFixtureManualArchive,
        identity_catalog: MatchIdentityCatalog,
        raw_archive: RawDataArchive,
    ) -> None:
        if not isinstance(archive, VerifiedReviewedFixtureManualArchive):
            raise TypeError("reviewed manual fixture archive must be verified")
        if not isinstance(identity_catalog, MatchIdentityCatalog):
            raise TypeError("reviewed manual fixture identity catalog is invalid")
        if not isinstance(raw_archive, RawDataArchive):
            raise TypeError("reviewed manual fixture raw archive is invalid")
        self._archive = archive
        self._request = reviewed_fixture_manual_request(archive)
        self._resolved = _resolve_fixtures(archive, identity_catalog)
        self._raw_archive = raw_archive

    async def capture_fixtures(
        self,
        request: FixtureIngestionRequest,
    ) -> FixtureIngestionCapture:
        if request != self._request:
            raise ValueError("reviewed manual fixture request conflicts with its archive")
        available_at = max(
            item.reviewed_at_utc for item in self._archive.document.fixtures
        )
        result = ProviderRequestResult(
            audit=ProviderRequestAudit(
                provider=self.provider_code,
                endpoint="offline://reviewed-fixture-manual/archive/v1",
                requested_at_utc=available_at,
                received_at_utc=available_at,
                available_at_utc=available_at,
                request_parameters={
                    "archive_schema_version": self._archive.document.schema_version,
                    "document_sha256": self._archive.document_sha256,
                    "evidence_sha256": self._archive.evidence_sha256,
                    "fixture_count": len(self._archive.document.fixtures),
                    "review_levels": tuple(
                        sorted(
                            {
                                item.review_level.value
                                for item in self._archive.document.fixtures
                            }
                        )
                    ),
                },
                http_status=200,
                provider_request_id=stable_id(
                    "reviewed-fixture-manual-document",
                    self._archive.document_sha256,
                ),
                duration_ms=0,
                outcome=ProviderRequestOutcome.SUCCESS,
            ),
            payload=self._archive.raw,
        )
        archived = self._raw_archive.write(
            self._archive.raw,
            result.to_raw_artifact_metadata(),
        )
        ingestion_id = stable_id(
            "fixture-ingestion",
            self.provider_code,
            archived.artifact_id,
        )
        registration, mappings_by_match = _registration(
            self._resolved,
            request,
            ingestion_id,
            available_at,
            self._archive.document_sha256,
        )
        observations = tuple(
            FixtureObservation(
                observation_id=stable_id(
                    "fixture-observation",
                    ingestion_id,
                    mappings_by_match[item.match_id].mapping_id,
                ),
                provider_mapping_id=mappings_by_match[item.match_id].mapping_id,
                external_match_id=mappings_by_match[item.match_id].external_match_id,
                internal_match_id=item.match_id,
                kickoff_at_utc=item.record.kickoff_at_utc,
                status=item.status,
                available_at_utc=available_at,
                payload_sha256=canonical_payload_sha256(item.record),
            )
            for item in self._resolved
        )
        return FixtureIngestionCapture(
            ingestion_id=ingestion_id,
            provider_code=self.provider_code,
            request=request,
            request_audit=result.audit,
            raw_artifact_id=archived.artifact_id,
            raw_payload_sha256=self._archive.document_sha256,
            registration=registration,
            observations=observations,
        )


def load_reviewed_fixture_manual_archive(
    path: str | Path,
) -> VerifiedReviewedFixtureManualArchive:
    archive_path = Path(path)
    if not archive_path.is_file() or archive_path.suffix.casefold() != ".json":
        raise ReviewedFixtureManualArchiveError(
            f"reviewed manual fixture archive must be a JSON file: {archive_path}"
        )
    try:
        raw = read_contract_file(archive_path)
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("archive must contain one object")
        document = ReviewedFixtureManualDocument.model_validate(payload)
        evidence_cache: dict[Path, str] = {}
        evidence_hashes = tuple(
            sorted(
                {
                    _verified_evidence_hash(archive_path, item, evidence_cache)
                    for item in document.fixtures
                }
            )
        )
        return VerifiedReviewedFixtureManualArchive(
            path=archive_path.resolve(),
            raw=raw,
            document=document,
            document_sha256=hashlib.sha256(raw).hexdigest(),
            evidence_sha256=evidence_hashes,
        )
    except ReviewedFixtureManualArchiveError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ReviewedFixtureManualArchiveError(
            "reviewed manual fixture archive is invalid"
        ) from None


def reviewed_fixture_manual_request(
    archive: VerifiedReviewedFixtureManualArchive,
) -> FixtureIngestionRequest:
    first = archive.document.fixtures[0]
    return FixtureIngestionRequest(
        kickoff_from_utc=min(
            item.kickoff_at_utc for item in archive.document.fixtures
        ),
        kickoff_to_utc=max(item.kickoff_at_utc for item in archive.document.fixtures),
        provider_competition_id=_competition_external_id(first),
        provider_season_id=stable_id(
            "reviewed-fixture-manual-season",
            first.competition_label,
            first.season,
        ),
        season=first.season,
        competition_type=first.competition_type,
        language=_LANGUAGE,
        team_type=first.team_type,
    )


def _resolve_fixtures(
    archive: VerifiedReviewedFixtureManualArchive,
    catalog: MatchIdentityCatalog,
) -> tuple[_ResolvedFixture, ...]:
    team_labels: defaultdict[tuple[str, TeamType], set[str]] = defaultdict(set)
    for identity in catalog.team_identities:
        team_labels[(identity.canonical_name, identity.team_type)].add(
            identity.internal_team_id
        )
        for alias in identity.aliases:
            team_labels[(alias.provider_team_name, alias.team_type)].add(
                identity.internal_team_id
            )
    competition_labels: defaultdict[tuple[str, str, str], set[str]] = defaultdict(
        set
    )
    competition_labels_any: defaultdict[str, set[str]] = defaultdict(set)
    for mapping in catalog.competition_mappings:
        competition_labels[
            (
                mapping.provider_competition_name,
                mapping.season,
                mapping.competition_type,
            )
        ].add(mapping.internal_competition_id)
        competition_labels_any[mapping.provider_competition_name].add(
            mapping.internal_competition_id
        )
    for anchor in catalog.canonical_anchors:
        competition_labels[
            (
                anchor.competition.name,
                anchor.identity.season,
                anchor.identity.competition_type,
            )
        ].add(anchor.competition.competition_id)
        competition_labels_any[anchor.competition.name].add(
            anchor.competition.competition_id
        )

    team_labels_any: defaultdict[str, set[str]] = defaultdict(set)
    for identity in catalog.team_identities:
        team_labels_any[identity.canonical_name].add(identity.internal_team_id)
        for alias in identity.aliases:
            team_labels_any[alias.provider_team_name].add(identity.internal_team_id)

    teams_by_id: dict[str, Team] = {}
    competitions_by_id: dict[str, Competition] = {}
    for anchor in catalog.canonical_anchors:
        _add_exact(teams_by_id, anchor.home_team.team_id, anchor.home_team, "team")
        _add_exact(teams_by_id, anchor.away_team.team_id, anchor.away_team, "team")
        _add_exact(
            competitions_by_id,
            anchor.competition.competition_id,
            anchor.competition,
            "competition",
        )
    anchors_by_match = {
        anchor.match.match_id: anchor for anchor in catalog.canonical_anchors
    }
    manual_mappings: defaultdict[str, set[str]] = defaultdict(set)
    for mapping in catalog.explicit_mappings:
        if (
            mapping.provider_code == REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE
            and mapping.external_namespace == _PROVIDER_NAMESPACE
        ):
            manual_mappings[mapping.external_match_id].add(mapping.internal_match_id)

    issues: list[ReviewedFixtureManualReconciliationIssue] = []
    resolved: list[_ResolvedFixture] = []
    for record in archive.document.fixtures:
        mapped_matches = tuple(
            (status, match_id)
            for status in MatchStatus
            for match_id in manual_mappings[
                _external_match_id(archive.document_sha256, record, status)
            ]
        )
        if len(mapped_matches) > 1:
            issues.append(
                _issue(
                    archive.document_sha256,
                    record,
                    ReviewedFixtureManualIssueReason.AMBIGUOUS_MATCH,
                    tuple(match_id for _, match_id in mapped_matches),
                )
            )
            continue
        if mapped_matches:
            status, match_id = mapped_matches[0]
            anchor = anchors_by_match.get(match_id)
            if anchor is None:
                issues.append(
                    _issue(
                        archive.document_sha256,
                        record,
                        ReviewedFixtureManualIssueReason.CANONICAL_REFERENCE_UNAVAILABLE,
                        (match_id,),
                    )
                )
                continue
            resolved.append(
                _ResolvedFixture(
                    record=record,
                    competition=anchor.competition,
                    home_team=anchor.home_team,
                    away_team=anchor.away_team,
                    match_id=match_id,
                    status=status,
                )
            )
            continue

        scope_conflict = _scope_conflict(
            record,
            competition_labels_any[record.competition_label],
            team_labels_any[record.home_team_label],
            team_labels_any[record.away_team_label],
            catalog.canonical_anchors,
        )
        if scope_conflict is not None:
            reason, candidate_ids = scope_conflict
            issues.append(
                _issue(
                    archive.document_sha256,
                    record,
                    reason,
                    candidate_ids,
                )
            )
            continue

        competition_candidates = competition_labels[
            (record.competition_label, record.season, record.competition_type)
        ]
        home_candidates = team_labels[(record.home_team_label, record.team_type)]
        away_candidates = team_labels[(record.away_team_label, record.team_type)]
        competition_id = _single_candidate(
            record,
            competition_candidates,
            ReviewedFixtureManualIssueReason.AMBIGUOUS_COMPETITION,
            archive.document_sha256,
            issues,
        )
        home_team_id = _single_candidate(
            record,
            home_candidates,
            ReviewedFixtureManualIssueReason.AMBIGUOUS_HOME_TEAM,
            archive.document_sha256,
            issues,
        )
        away_team_id = _single_candidate(
            record,
            away_candidates,
            ReviewedFixtureManualIssueReason.AMBIGUOUS_AWAY_TEAM,
            archive.document_sha256,
            issues,
        )
        if competition_id is None or home_team_id is None or away_team_id is None:
            continue
        if home_team_id == away_team_id:
            issues.append(
                _issue(
                    archive.document_sha256,
                    record,
                    ReviewedFixtureManualIssueReason.HOME_AWAY_CONFLICT,
                    (home_team_id,),
                )
            )
            continue

        competition = competitions_by_id.get(competition_id)
        home_team = teams_by_id.get(home_team_id)
        away_team = teams_by_id.get(away_team_id)
        unavailable_ids = tuple(
            value
            for value, candidates, item in (
                (competition_id, competition_candidates, competition),
                (home_team_id, home_candidates, home_team),
                (away_team_id, away_candidates, away_team),
            )
            if candidates and item is None
        )
        if unavailable_ids:
            issues.append(
                _issue(
                    archive.document_sha256,
                    record,
                    ReviewedFixtureManualIssueReason.CANONICAL_REFERENCE_UNAVAILABLE,
                    unavailable_ids,
                )
            )
            continue
        competition = competition or _new_competition(record)
        home_team = home_team or _new_team(record.home_team_label, record.team_type)
        away_team = away_team or _new_team(record.away_team_label, record.team_type)

        exact = tuple(
            anchor
            for anchor in catalog.canonical_anchors
            if _same_scope(anchor, record)
            and anchor.match.competition_id == competition.competition_id
            and anchor.match.home_team_id == home_team.team_id
            and anchor.match.away_team_id == away_team.team_id
            and anchor.match.kickoff_at_utc == record.kickoff_at_utc
        )
        if len(exact) > 1:
            issues.append(
                _issue(
                    archive.document_sha256,
                    record,
                    ReviewedFixtureManualIssueReason.AMBIGUOUS_MATCH,
                    tuple(item.match.match_id for item in exact),
                )
            )
            continue
        if exact:
            match_id = exact[0].match.match_id
            status = exact[0].match.status
        else:
            conflict = _stored_conflict(
                record,
                competition.competition_id,
                home_team.team_id,
                away_team.team_id,
                catalog.canonical_anchors,
            )
            if conflict is not None:
                reason, candidate_ids = conflict
                issues.append(
                    _issue(
                        archive.document_sha256,
                        record,
                        reason,
                        candidate_ids,
                    )
                )
                continue
            archive_conflict = _archive_conflict(
                record,
                competition.competition_id,
                home_team.team_id,
                away_team.team_id,
                resolved,
            )
            if archive_conflict is not None:
                reason, candidate_ids = archive_conflict
                issues.append(
                    _issue(
                        archive.document_sha256,
                        record,
                        reason,
                        candidate_ids,
                    )
                )
                continue
            match_id = stable_id(
                "reviewed-fixture-manual-match",
                competition.competition_id,
                record.season,
                record.competition_type,
                home_team.team_id,
                away_team.team_id,
                record.kickoff_at_utc.isoformat(),
            )
            status = MatchStatus.SCHEDULED
        if any(item.match_id == match_id for item in resolved):
            issues.append(
                _issue(
                    archive.document_sha256,
                    record,
                    ReviewedFixtureManualIssueReason.AMBIGUOUS_MATCH,
                    (match_id,),
                )
            )
            continue
        resolved.append(
            _ResolvedFixture(
                record=record,
                competition=competition,
                home_team=home_team,
                away_team=away_team,
                match_id=match_id,
                status=status,
            )
        )

    if issues:
        ordered = tuple(sorted(issues, key=lambda item: item.issue_id))
        raise ReviewedFixtureManualReconciliationError(
            ReviewedFixtureManualReconciliationReport(
                report_id=stable_id(
                    "reviewed-fixture-manual-reconciliation",
                    archive.document_sha256,
                    *(item.issue_id for item in ordered),
                ),
                archive_document_sha256=archive.document_sha256,
                issues=ordered,
            )
        )
    return tuple(sorted(resolved, key=lambda item: item.match_id))


def _single_candidate(
    record: ReviewedFixtureManualRecord,
    candidates: set[str],
    reason: ReviewedFixtureManualIssueReason,
    document_sha256: str,
    issues: list[ReviewedFixtureManualReconciliationIssue],
) -> str | None:
    if len(candidates) > 1:
        issues.append(_issue(document_sha256, record, reason, tuple(candidates)))
        return None
    if candidates:
        return next(iter(candidates))
    if reason is ReviewedFixtureManualIssueReason.AMBIGUOUS_COMPETITION:
        return _new_competition(record).competition_id
    label = (
        record.home_team_label
        if reason is ReviewedFixtureManualIssueReason.AMBIGUOUS_HOME_TEAM
        else record.away_team_label
    )
    return _new_team(label, record.team_type).team_id


def _stored_conflict(
    record: ReviewedFixtureManualRecord,
    competition_id: str,
    home_team_id: str,
    away_team_id: str,
    anchors: tuple[CanonicalFixtureAnchor, ...],
) -> tuple[ReviewedFixtureManualIssueReason, tuple[str, ...]] | None:
    same_scope = tuple(item for item in anchors if _same_scope(item, record))
    kickoff_conflicts = tuple(
        item.match.match_id
        for item in same_scope
        if item.match.competition_id == competition_id
        and item.match.home_team_id == home_team_id
        and item.match.away_team_id == away_team_id
        and item.match.kickoff_at_utc != record.kickoff_at_utc
    )
    if kickoff_conflicts:
        return ReviewedFixtureManualIssueReason.KICKOFF_CONFLICT, kickoff_conflicts
    home_away_conflicts = tuple(
        item.match.match_id
        for item in same_scope
        if item.match.competition_id == competition_id
        and item.match.home_team_id == away_team_id
        and item.match.away_team_id == home_team_id
        and item.match.kickoff_at_utc == record.kickoff_at_utc
    )
    if home_away_conflicts:
        return ReviewedFixtureManualIssueReason.HOME_AWAY_CONFLICT, home_away_conflicts
    competition_conflicts = tuple(
        item.match.match_id
        for item in same_scope
        if item.match.competition_id != competition_id
        and item.match.home_team_id == home_team_id
        and item.match.away_team_id == away_team_id
        and item.match.kickoff_at_utc == record.kickoff_at_utc
    )
    if competition_conflicts:
        return ReviewedFixtureManualIssueReason.COMPETITION_CONFLICT, competition_conflicts
    return None


def _scope_conflict(
    record: ReviewedFixtureManualRecord,
    competition_ids: set[str],
    home_team_ids: set[str],
    away_team_ids: set[str],
    anchors: tuple[CanonicalFixtureAnchor, ...],
) -> tuple[ReviewedFixtureManualIssueReason, tuple[str, ...]] | None:
    candidates = tuple(
        item
        for item in anchors
        if item.match.competition_id in competition_ids
        and item.match.home_team_id in home_team_ids
        and item.match.away_team_id in away_team_ids
        and item.match.kickoff_at_utc == record.kickoff_at_utc
    )
    if not candidates:
        return None
    if any(
        item.home_team.team_type is record.team_type
        and item.away_team.team_type is record.team_type
        and item.identity.season == record.season
        and item.identity.competition_type == record.competition_type
        for item in candidates
    ):
        return None
    candidate_ids = tuple(item.match.match_id for item in candidates)
    if any(
        item.home_team.team_type is not record.team_type
        or item.away_team.team_type is not record.team_type
        for item in candidates
    ):
        return ReviewedFixtureManualIssueReason.TEAM_TYPE_CONFLICT, candidate_ids
    if any(item.identity.season != record.season for item in candidates):
        return ReviewedFixtureManualIssueReason.SEASON_CONFLICT, candidate_ids
    if any(
        item.identity.competition_type != record.competition_type
        for item in candidates
    ):
        return ReviewedFixtureManualIssueReason.COMPETITION_TYPE_CONFLICT, candidate_ids
    return None


def _archive_conflict(
    record: ReviewedFixtureManualRecord,
    competition_id: str,
    home_team_id: str,
    away_team_id: str,
    resolved: list[_ResolvedFixture],
) -> tuple[ReviewedFixtureManualIssueReason, tuple[str, ...]] | None:
    for item in resolved:
        if (
            item.record.season != record.season
            or item.record.competition_type != record.competition_type
        ):
            continue
        if (
            item.competition.competition_id == competition_id
            and item.home_team.team_id == home_team_id
            and item.away_team.team_id == away_team_id
            and item.record.kickoff_at_utc != record.kickoff_at_utc
        ):
            return ReviewedFixtureManualIssueReason.KICKOFF_CONFLICT, (item.match_id,)
        if (
            item.competition.competition_id == competition_id
            and item.home_team.team_id == away_team_id
            and item.away_team.team_id == home_team_id
            and item.record.kickoff_at_utc == record.kickoff_at_utc
        ):
            return ReviewedFixtureManualIssueReason.HOME_AWAY_CONFLICT, (item.match_id,)
        if (
            item.competition.competition_id != competition_id
            and item.home_team.team_id == home_team_id
            and item.away_team.team_id == away_team_id
            and item.record.kickoff_at_utc == record.kickoff_at_utc
        ):
            return ReviewedFixtureManualIssueReason.COMPETITION_CONFLICT, (item.match_id,)
    return None


def _registration(
    resolved: tuple[_ResolvedFixture, ...],
    request: FixtureIngestionRequest,
    ingestion_id: str,
    available_at: UtcDateTime,
    document_sha256: str,
) -> tuple[MatchIdentityRegistration, dict[str, ProviderMatchMapping]]:
    competitions: dict[str, Competition] = {}
    teams: dict[str, Team] = {}
    matches: dict[str, Match] = {}
    team_aliases: dict[tuple[str, str], RegisteredTeamAlias] = {}
    mappings: dict[str, ProviderMatchMapping] = {}
    for item in resolved:
        _add_exact(
            competitions,
            item.competition.competition_id,
            item.competition,
            "competition",
        )
        _add_exact(teams, item.home_team.team_id, item.home_team, "team")
        _add_exact(teams, item.away_team.team_id, item.away_team, "team")
        match = Match(
            match_id=item.match_id,
            competition_id=item.competition.competition_id,
            home_team_id=item.home_team.team_id,
            away_team_id=item.away_team.team_id,
            kickoff_at_utc=item.record.kickoff_at_utc,
            status=item.status,
            available_at_utc=available_at,
        )
        _add_exact(matches, match.match_id, match, "match")
        for team, label in (
            (item.home_team, item.record.home_team_label),
            (item.away_team, item.record.away_team_label),
        ):
            alias = RegisteredTeamAlias(
                internal_team_id=team.team_id,
                alias=Alias(
                    provider_code=REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE,
                    provider_team_id=stable_id(
                        "reviewed-fixture-manual-team-source",
                        item.record.team_type.value,
                        label,
                    ),
                    provider_team_name=label,
                    language=request.language,
                    team_type=item.record.team_type,
                ),
                available_at_utc=available_at,
            )
            team_aliases[(team.team_id, label)] = alias
        external_match_id = _external_match_id(
            document_sha256,
            item.record,
            item.status,
        )
        mapping = ProviderMatchMapping(
            mapping_id=stable_id(
                "provider-mapping",
                REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE,
                _PROVIDER_NAMESPACE,
                external_match_id,
            ),
            provider_code=REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE,
            external_namespace=_PROVIDER_NAMESPACE,
            external_match_id=external_match_id,
            internal_match_id=item.match_id,
            resolution_method="REVIEWED_MANUAL_FIXTURE",
            confidence=Decimal(1),
            available_at_utc=available_at,
        )
        mappings[item.match_id] = mapping

    competition = resolved[0].competition
    registration = MatchIdentityRegistration(
        created_at_utc=available_at,
        competitions=tuple(sorted(competitions.values(), key=lambda item: item.competition_id)),
        teams=tuple(sorted(teams.values(), key=lambda item: item.team_id)),
        matches=tuple(sorted(matches.values(), key=lambda item: item.match_id)),
        team_aliases=tuple(
            sorted(
                team_aliases.values(),
                key=lambda item: (
                    item.internal_team_id,
                    item.alias.provider_team_name,
                ),
            )
        ),
        competition_mappings=(
            RegisteredCompetitionMapping(
                mapping=CompetitionMapping(
                    internal_competition_id=competition.competition_id,
                    provider_code=REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE,
                    provider_competition_id=request.provider_competition_id,
                    provider_competition_name=resolved[0].record.competition_label,
                    language=request.language,
                    season=request.season,
                    competition_type=request.competition_type,
                ),
                available_at_utc=available_at,
            ),
        ),
        canonical_matches=tuple(
            RegisteredCanonicalMatch(
                identity=CanonicalMatchIdentity(
                    internal_match_id=match.match_id,
                    internal_competition_id=match.competition_id,
                    internal_home_team_id=match.home_team_id,
                    internal_away_team_id=match.away_team_id,
                    season=request.season,
                    competition_type=request.competition_type,
                    kickoff_at_utc=match.kickoff_at_utc,
                ),
                available_at_utc=available_at,
            )
            for match in sorted(matches.values(), key=lambda item: item.match_id)
        ),
        explicit_mappings=tuple(
            sorted(mappings.values(), key=lambda item: item.mapping_id)
        ),
    )
    return registration, mappings


def _new_competition(record: ReviewedFixtureManualRecord) -> Competition:
    return Competition(
        competition_id=stable_id(
            "reviewed-fixture-manual-competition",
            record.competition_label,
        ),
        canonical_key=stable_id(
            "reviewed-fixture-manual-competition-key",
            record.competition_label,
        ),
        name=record.competition_label,
        country_code="ZZ",
    )


def _new_team(label: str, team_type: TeamType) -> Team:
    return Team(
        team_id=stable_id("reviewed-fixture-manual-team", team_type.value, label),
        canonical_key=stable_id(
            "reviewed-fixture-manual-team-key",
            team_type.value,
            label,
        ),
        name=label,
        team_type=team_type,
    )


def _same_scope(
    anchor: CanonicalFixtureAnchor,
    record: ReviewedFixtureManualRecord,
) -> bool:
    return (
        anchor.identity.season == record.season
        and anchor.identity.competition_type == record.competition_type
        and anchor.home_team.team_type is record.team_type
        and anchor.away_team.team_type is record.team_type
    )


def _issue(
    document_sha256: str,
    record: ReviewedFixtureManualRecord,
    reason: ReviewedFixtureManualIssueReason,
    candidate_ids: tuple[str, ...],
) -> ReviewedFixtureManualReconciliationIssue:
    record_sha256 = canonical_payload_sha256(record)
    candidates = tuple(sorted(set(candidate_ids)))
    return ReviewedFixtureManualReconciliationIssue(
        issue_id=stable_id(
            "reviewed-fixture-manual-issue",
            document_sha256,
            record_sha256,
            reason.value,
            *candidates,
        ),
        reason=reason,
        record_sha256=record_sha256,
        source_reference=record.source_reference,
        competition_label=record.competition_label,
        season=record.season,
        kickoff_at_utc=record.kickoff_at_utc,
        home_team_label=record.home_team_label,
        away_team_label=record.away_team_label,
        competition_type=record.competition_type,
        team_type=record.team_type,
        source_artifact_path=record.source_artifact_path,
        source_artifact_sha256=record.source_artifact_sha256,
        canonical_candidate_ids=candidates,
    )


def _record_identity(record: ReviewedFixtureManualRecord) -> tuple[object, ...]:
    return (
        record.competition_label,
        record.season,
        record.competition_type,
        record.team_type,
        record.home_team_label,
        record.away_team_label,
        record.kickoff_at_utc,
    )


def _competition_external_id(record: ReviewedFixtureManualRecord) -> str:
    return stable_id(
        "reviewed-fixture-manual-competition-source",
        record.competition_label,
    )


def _external_match_id(
    document_sha256: str,
    record: ReviewedFixtureManualRecord,
    status: MatchStatus,
) -> str:
    return stable_id(
        "reviewed-fixture-manual-fixture-source",
        document_sha256,
        canonical_payload_sha256(record),
        status.value,
    )


def _verified_evidence_hash(
    archive_path: Path,
    record: ReviewedFixtureManualRecord,
    cache: dict[Path, str],
) -> str:
    base = archive_path.parent.resolve()
    requested = Path(record.source_artifact_path)
    if requested.is_absolute() or requested.suffix.casefold() not in _EVIDENCE_SUFFIXES:
        raise ReviewedFixtureManualArchiveError(
            "reviewed manual fixture evidence must be a relative screenshot, PDF, "
            "HTML, or text path"
        )
    unresolved = base / requested
    if unresolved.is_symlink():
        raise ReviewedFixtureManualArchiveError(
            "reviewed manual fixture evidence path is invalid"
        )
    evidence = unresolved.resolve()
    if (
        not evidence.is_relative_to(base)
        or evidence == archive_path.resolve()
        or not evidence.is_file()
    ):
        raise ReviewedFixtureManualArchiveError(
            "reviewed manual fixture evidence path is invalid"
        )
    try:
        evidence_size = evidence.stat().st_size
        if evidence_size == 0:
            raise ReviewedFixtureManualArchiveError(
                "reviewed manual fixture evidence cannot be empty"
            )
        if evidence_size > _MAX_EVIDENCE_BYTES:
            raise ReviewedFixtureManualArchiveError(
                "reviewed manual fixture evidence exceeds the size limit"
            )
        digest = cache.get(evidence)
        if digest is None:
            hasher = hashlib.sha256()
            total = 0
            with evidence.open("rb") as stream:
                while chunk := stream.read(_EVIDENCE_CHUNK_BYTES):
                    total += len(chunk)
                    if total > _MAX_EVIDENCE_BYTES:
                        raise ReviewedFixtureManualArchiveError(
                            "reviewed manual fixture evidence exceeds the size limit"
                        )
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            cache[evidence] = digest
    except ReviewedFixtureManualArchiveError:
        raise
    except OSError:
        raise ReviewedFixtureManualArchiveError(
            "reviewed manual fixture evidence cannot be read"
        ) from None
    if not hmac.compare_digest(digest, record.source_artifact_sha256):
        raise ReviewedFixtureManualArchiveError(
            "reviewed manual fixture evidence SHA-256 does not match"
        )
    return digest


def _add_exact[Item](
    values: dict[str, Item],
    key: str,
    item: Item,
    label: str,
) -> None:
    previous = values.get(key)
    if previous is not None and previous != item:
        raise ValueError(f"reviewed manual fixture has conflicting {label}: {key}")
    values[key] = item


def _no_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON number")
