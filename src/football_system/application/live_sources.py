from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    RuntimeProvenance,
    require_provider_runtime_provenance,
)
from football_system.application.market_consensus import (
    ConsensusLineage,
    ConsensusMarketOddsProvider,
)
from football_system.application.ports.data_providers import (
    EloTrainingHistoryBatch,
    EloTrainingHistoryProvider,
    EloTrainingHistoryQuery,
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
    MarketOddsBatch,
    MarketOddsProvider,
    SnapshotQuery,
    SportteryBatch,
    SportteryProvider,
)
from football_system.domain.archive import HistoricalDataMode, canonical_payload_sha256
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
    utc_now,
)
from football_system.domain.market_reconciliation import (
    MarketOddsReconciliationIssue,
    MarketOddsReconciliationIssueReason,
)
from football_system.domain.raw_data import ProviderRequestAudit


LIVE_SOURCE_INGESTION_V1 = "LIVE_SOURCE_INGESTION_V1"
LIVE_IDENTITY_REVIEW_V1 = "LIVE_IDENTITY_REVIEW_V1"
LIVE_RECONCILIATION_REPORT_V1 = "LIVE_RECONCILIATION_REPORT_V1"
LIVE_ANALYSIS_PREPARATION_V1 = "LIVE_ANALYSIS_PREPARATION_V1"
LIVE_ANALYSIS_INPUT_POLICY_V1 = "LIVE_ANALYSIS_INPUT_POLICY_V1"


class LiveSourceKind(StrEnum):
    MARKET_ODDS = "MARKET_ODDS"
    SPORTTERRY = "SPORTTERRY"


class LiveSourceIngestionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ISSUES = "COMPLETED_WITH_ISSUES"


class SourceArtifactRole(StrEnum):
    RAW_RESPONSE = "RAW_RESPONSE"
    MANUAL_DOCUMENT = "MANUAL_DOCUMENT"
    SOURCE_ARTIFACT = "SOURCE_ARTIFACT"


class SourceIngestionArtifact(DomainModel):
    artifact_id: Identifier
    role: SourceArtifactRole
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(min_length=1, max_length=4096)
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.captured_at_utc > self.available_at_utc:
            raise ValueError("source artifact availability cannot precede capture")
        return self


class SourceReconciliationIssue(DomainModel):
    issue_id: Identifier
    source_kind: LiveSourceKind
    reason: MarketOddsReconciliationIssueReason
    provider_code: Identifier
    external_namespace: Identifier | None = None
    external_match_id: Identifier | None = None
    requested_match_id: Identifier | None = None
    candidates: tuple[Identifier, ...] = ()
    code: Identifier
    detail: str = Field(min_length=1, max_length=240)
    provider_identity_json: str | None = None

    @model_validator(mode="after")
    def validate_issue(self) -> Self:
        if (self.external_namespace is None) != (self.external_match_id is None):
            raise ValueError(
                "source issue external namespace and match ID must be paired"
            )
        if self.candidates != tuple(sorted(set(self.candidates))):
            raise ValueError("source issue candidates must be unique and sorted")
        return self

    @classmethod
    def from_market_odds(
        cls,
        issue: MarketOddsReconciliationIssue,
    ) -> SourceReconciliationIssue:
        return cls(
            **issue.model_dump(mode="python"),
            source_kind=LiveSourceKind.MARKET_ODDS,
        )


class SportterySnapshotProvenance(DomainModel):
    schema_version: Identifier
    snapshot_id: Identifier
    source_snapshot_key: Identifier
    archive_snapshot_id: Identifier
    provider_code: Identifier
    sporttery_match_no: Identifier
    match_number_date: date
    review_level: Identifier
    entered_by: Identifier
    reviewed_by: Identifier
    captured_at_utc: UtcDateTime
    reviewed_at_utc: UtcDateTime
    source_reference: str = Field(min_length=1, max_length=2048)
    source_artifact_path: str = Field(min_length=1, max_length=4096)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manual_document_artifact_id: Identifier
    source_artifact_id: Identifier

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.captured_at_utc > self.reviewed_at_utc:
            raise ValueError("Sporttery review cannot precede source capture")
        return self


class MarketOddsIngestionCapture(DomainModel):
    schema_version: str = LIVE_SOURCE_INGESTION_V1
    ingestion_id: Identifier
    provider_code: Identifier
    requested_match_ids: tuple[Identifier, ...] = Field(min_length=1)
    identity_cutoff_at_utc: UtcDateTime
    request_audit: ProviderRequestAudit
    artifact: SourceIngestionArtifact
    ingested_at_utc: UtcDateTime
    source_batch: MarketOddsBatch
    consensus_batch: MarketOddsBatch
    consensus_lineages: tuple[ConsensusLineage, ...]

    @model_validator(mode="after")
    def validate_capture(self) -> Self:
        if self.schema_version != LIVE_SOURCE_INGESTION_V1:
            raise ValueError("unsupported live source ingestion schema")
        if self.request_audit.provider != self.provider_code:
            raise ValueError("market capture provider conflicts with request audit")
        if len(self.requested_match_ids) != len(set(self.requested_match_ids)):
            raise ValueError("market capture requested match IDs must be unique")
        if self.identity_cutoff_at_utc > self.request_audit.requested_at_utc:
            raise ValueError("market identity cutoff cannot follow request start")
        if self.artifact.role is not SourceArtifactRole.RAW_RESPONSE:
            raise ValueError("market capture requires a raw response artifact")
        if self.request_audit.available_at_utc is None:
            raise ValueError("market capture requires provider availability")
        if self.ingested_at_utc < self.request_audit.received_at_utc:
            raise ValueError("market ingestion cannot precede response receipt")
        source_ids = {item.snapshot_id for item in self.source_batch.snapshots}
        if any(
            item.match_id not in self.requested_match_ids
            for item in self.source_batch.snapshots
        ):
            raise ValueError("market capture contains an unrequested match")
        consensus_ids = {item.snapshot_id for item in self.consensus_batch.snapshots}
        if {item.source_snapshot_key for item in self.consensus_lineages} != {
            item.source_snapshot_key for item in self.consensus_batch.snapshots
        }:
            raise ValueError("market consensus lineage does not cover its snapshots")
        if any(
            constituent.snapshot_id not in source_ids
            for lineage in self.consensus_lineages
            for constituent in lineage.constituents
        ):
            raise ValueError("market consensus references an unknown source snapshot")
        if len(consensus_ids) != len(self.consensus_batch.snapshots):
            raise ValueError("market consensus snapshot IDs must be unique")
        return self

    @property
    def issues(self) -> tuple[SourceReconciliationIssue, ...]:
        return tuple(
            SourceReconciliationIssue.from_market_odds(item)
            for item in self.source_batch.issues
        )


class SportteryIngestionCapture(DomainModel):
    schema_version: str = LIVE_SOURCE_INGESTION_V1
    ingestion_id: Identifier
    provider_code: Identifier
    identity_cutoff_at_utc: UtcDateTime
    artifacts: tuple[SourceIngestionArtifact, ...] = Field(min_length=1)
    ingested_at_utc: UtcDateTime
    batch: SportteryBatch
    provenance: tuple[SportterySnapshotProvenance, ...]
    issues: tuple[SourceReconciliationIssue, ...] = ()

    @model_validator(mode="after")
    def validate_capture(self) -> Self:
        if self.schema_version != LIVE_SOURCE_INGESTION_V1:
            raise ValueError("unsupported live source ingestion schema")
        artifact_ids = tuple(item.artifact_id for item in self.artifacts)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("Sporttery capture artifacts must be unique")
        snapshots = {item.snapshot_id: item for item in self.batch.snapshots}
        artifacts = {item.artifact_id: item for item in self.artifacts}
        if set(snapshots) != {item.snapshot_id for item in self.provenance}:
            raise ValueError("Sporttery provenance must cover every snapshot")
        if any(
            item.provider_code != self.provider_code
            or snapshots[item.snapshot_id].source_snapshot_key
            != item.source_snapshot_key
            for item in self.provenance
        ):
            raise ValueError("Sporttery provenance conflicts with normalized snapshots")
        if self.identity_cutoff_at_utc > self.ingested_at_utc:
            raise ValueError("Sporttery identity cutoff cannot follow ingestion")
        if any(item.reviewed_at_utc > self.ingested_at_utc for item in self.provenance):
            raise ValueError("Sporttery ingestion cannot precede manual review")
        for item in self.provenance:
            document_artifact = artifacts.get(item.manual_document_artifact_id)
            source_artifact = artifacts.get(item.source_artifact_id)
            if (
                document_artifact is None
                or document_artifact.role is not SourceArtifactRole.MANUAL_DOCUMENT
                or source_artifact is None
                or source_artifact.role is not SourceArtifactRole.SOURCE_ARTIFACT
                or source_artifact.payload_sha256 != item.source_artifact_sha256
            ):
                raise ValueError(
                    "Sporttery provenance is not bound to capture artifacts"
                )
        if any(
            item.source_kind is not LiveSourceKind.SPORTTERRY
            or item.provider_code != self.provider_code
            for item in self.issues
        ):
            raise ValueError("Sporttery capture contains another source issue kind")
        return self


class SourceIngestionSummary(DomainModel):
    ingestion_id: Identifier
    source_kind: LiveSourceKind
    status: LiveSourceIngestionStatus
    inserted: bool
    artifact_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    mapping_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    consensus_count: int = Field(default=0, ge=0)


class ReviewedIdentityMapping(DomainModel):
    provider_code: Identifier
    external_namespace: Identifier
    external_match_id: Identifier
    internal_match_id: Identifier


class IdentityReviewDocument(DomainModel):
    schema_version: str = LIVE_IDENTITY_REVIEW_V1
    review_id: Identifier
    source_ingestion_id: Identifier
    reviewed_by: Identifier
    reviewed_at_utc: UtcDateTime
    mappings: tuple[ReviewedIdentityMapping, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.schema_version != LIVE_IDENTITY_REVIEW_V1:
            raise ValueError("unsupported live identity review schema")
        identities = tuple(
            (
                item.provider_code,
                item.external_namespace,
                item.external_match_id,
            )
            for item in self.mappings
        )
        if len(identities) != len(set(identities)):
            raise ValueError("identity review mappings must be unique")
        return self


class IdentityReviewSummary(DomainModel):
    review_id: Identifier
    source_ingestion_id: Identifier
    inserted: bool
    mapping_count: int = Field(ge=0)


class ReconciliationReport(DomainModel):
    schema_version: str = LIVE_RECONCILIATION_REPORT_V1
    generated_at_utc: UtcDateTime
    source_ingestion_ids: tuple[Identifier, ...]
    unresolved: tuple[SourceReconciliationIssue, ...]
    ambiguous: tuple[SourceReconciliationIssue, ...]
    other_issues: tuple[SourceReconciliationIssue, ...]

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.schema_version != LIVE_RECONCILIATION_REPORT_V1:
            raise ValueError("unsupported reconciliation report schema")
        if self.source_ingestion_ids != tuple(sorted(set(self.source_ingestion_ids))):
            raise ValueError("report ingestion IDs must be unique and sorted")
        groups = (self.unresolved, self.ambiguous, self.other_issues)
        if any(
            group != tuple(sorted(group, key=lambda item: item.issue_id))
            for group in groups
        ):
            raise ValueError("report issues must be sorted")
        issue_ids = tuple(item.issue_id for group in groups for item in group)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("report issue classifications must be disjoint")
        if (
            any(
                item.reason
                is not MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED
                for item in self.unresolved
            )
            or any(
                item.reason
                is not MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS
                for item in self.ambiguous
            )
            or any(
                item.reason
                in {
                    MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED,
                    MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS,
                }
                for item in self.other_issues
            )
        ):
            raise ValueError("report issue classification is inconsistent")
        return self


class LiveAnalysisInputPolicy(DomainModel):
    version: str = LIVE_ANALYSIS_INPUT_POLICY_V1
    maximum_odds_age_seconds: int = Field(gt=0, strict=True)
    minimum_bookmaker_count: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_version(self) -> Self:
        if self.version != LIVE_ANALYSIS_INPUT_POLICY_V1:
            raise ValueError("unsupported live analysis input policy")
        return self


class PreparationStatus(StrEnum):
    ANALYSIS_INPUT_READY = "ANALYSIS_INPUT_READY"
    NO_ANALYSIS_INSUFFICIENT_DATA = "NO_ANALYSIS_INSUFFICIENT_DATA"


class PreparationReasonCode(StrEnum):
    FIXTURE_NOT_VISIBLE = "FIXTURE_NOT_VISIBLE"
    IDENTITY_NOT_RESOLVED = "IDENTITY_NOT_RESOLVED"
    MARKET_CONSENSUS_NOT_VISIBLE = "MARKET_CONSENSUS_NOT_VISIBLE"
    MARKET_CONSENSUS_INVALID = "MARKET_CONSENSUS_INVALID"
    ODDS_STALE = "ODDS_STALE"
    BOOKMAKER_COVERAGE_LOW = "BOOKMAKER_COVERAGE_LOW"
    SPORTTERRY_NOT_VISIBLE = "SPORTTERRY_NOT_VISIBLE"
    SPORTTERRY_PROVENANCE_INVALID = "SPORTTERRY_PROVENANCE_INVALID"
    SOURCE_CROSSES_CUTOFF = "SOURCE_CROSSES_CUTOFF"


class PreparationDataQuality(DomainModel):
    fixture_visible: bool
    identity_resolved: bool
    market_consensus_visible: bool
    market_consensus_verified: bool
    bookmaker_coverage_sufficient: bool
    market_fresh: bool
    sporttery_visible: bool
    sporttery_provenance_verified: bool
    cutoff_clean: bool

    @property
    def ready(self) -> bool:
        return all(self.model_dump(mode="python").values())


class PreparedMatchInput(DomainModel):
    match_id: Identifier
    fixture_observation_id: Identifier | None = None
    market_consensus_snapshot_id: Identifier | None = None
    sporttery_bonus_snapshot_id: Identifier | None = None
    bookmaker_count: int = Field(default=0, ge=0)
    odds_age_seconds: int | None = Field(default=None, ge=0)
    reason_codes: tuple[PreparationReasonCode, ...]
    data_quality: PreparationDataQuality

    @model_validator(mode="after")
    def validate_readiness(self) -> Self:
        if self.reason_codes != tuple(sorted(set(self.reason_codes), key=str)):
            raise ValueError("preparation reason codes must be unique and sorted")
        if self.data_quality.ready != (not self.reason_codes):
            raise ValueError("preparation reasons conflict with data quality")
        return self


class PrepareAnalysisRequest(DomainModel):
    decision_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    competition_id: Identifier
    season_id: Identifier
    expected_match_ids: tuple[Identifier, ...] = ()
    allow_partial_inputs: bool = False
    policy: LiveAnalysisInputPolicy
    preparation_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("preparation kickoff window is invalid")
        if len(self.expected_match_ids) != len(set(self.expected_match_ids)):
            raise ValueError("preparation expected match IDs must be unique")
        return self


class LiveAnalysisPreparation(DomainModel):
    schema_version: str = LIVE_ANALYSIS_PREPARATION_V1
    preparation_id: Identifier
    status: PreparationStatus
    decision_as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    competition_id: Identifier
    season_id: Identifier
    allow_partial_inputs: bool
    policy: LiveAnalysisInputPolicy
    expected_match_ids: tuple[Identifier, ...]
    matches: tuple[PreparedMatchInput, ...]
    created_at_utc: UtcDateTime
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **values: object) -> LiveAnalysisPreparation:
        draft = cls.model_construct(**values, report_hash="0" * 64)
        payload = draft.model_dump(
            mode="python",
            exclude={"report_hash"},
            exclude_computed_fields=True,
        )
        return cls.model_validate(
            {**values, "report_hash": canonical_payload_sha256(payload)}
        )

    @model_validator(mode="after")
    def validate_preparation(self) -> Self:
        if self.schema_version != LIVE_ANALYSIS_PREPARATION_V1:
            raise ValueError("unsupported live analysis preparation schema")
        if self.decision_as_of_at_utc > self.created_at_utc:
            raise ValueError("preparation cannot be created before its cutoff")
        match_ids = tuple(item.match_id for item in self.matches)
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("preparation matches must be unique")
        ready_count = sum(item.data_quality.ready for item in self.matches)
        expected_status = (
            PreparationStatus.ANALYSIS_INPUT_READY
            if ready_count > 0
            and (self.allow_partial_inputs or ready_count == len(self.matches))
            else PreparationStatus.NO_ANALYSIS_INSUFFICIENT_DATA
        )
        if self.status is not expected_status:
            raise ValueError("preparation status conflicts with input coverage")
        payload = self.model_dump(
            mode="python",
            exclude={"report_hash"},
            exclude_computed_fields=True,
        )
        if canonical_payload_sha256(payload) != self.report_hash:
            raise ValueError("preparation report hash is inconsistent")
        return self

    @property
    def ready_match_ids(self) -> tuple[str, ...]:
        return tuple(item.match_id for item in self.matches if item.data_quality.ready)


class PreparedLiveSourceBundle(DomainModel):
    preparation: LiveAnalysisPreparation
    competition_id: Identifier
    season_id: Identifier
    fixtures: FixtureBatch
    market_odds: MarketOddsBatch
    sporttery: SportteryBatch


class PreparedLiveFixtureProvider(FixtureProvider):
    def __init__(self, bundle: PreparedLiveSourceBundle) -> None:
        self._bundle = bundle
        self.runtime_provenance = _prepared_runtime_provenance(
            "fixture",
            tuple(item.provider_code for item in bundle.fixtures.mappings),
        )

    async def fetch_fixtures(self, query: FixtureQuery) -> FixtureBatch:
        preparation = self._bundle.preparation
        if (
            query.as_of_at_utc != preparation.decision_as_of_at_utc
            or query.kickoff_from_utc != preparation.kickoff_from_utc
            or query.kickoff_to_utc != preparation.kickoff_to_utc
        ):
            raise ValueError("fixture query does not match the frozen preparation")
        return self._bundle.fixtures


class PreparedLiveMarketOddsProvider(MarketOddsProvider):
    def __init__(self, bundle: PreparedLiveSourceBundle) -> None:
        self._bundle = bundle
        self.runtime_provenance = _prepared_runtime_provenance(
            "market odds",
            tuple(
                sorted(
                    {
                        *(item.provider_code for item in bundle.market_odds.snapshots),
                        *(item.provider_code for item in bundle.market_odds.mappings),
                    }
                )
            ),
        )

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        _validate_prepared_snapshot_query(self._bundle, query, "market odds")
        return self._bundle.market_odds


class PreparedLiveSportteryProvider(SportteryProvider):
    def __init__(self, bundle: PreparedLiveSourceBundle) -> None:
        self._bundle = bundle
        self.runtime_provenance = _prepared_runtime_provenance(
            "Sporttery",
            tuple(
                sorted(
                    {
                        *(item.provider_code for item in bundle.sporttery.snapshots),
                        *(item.provider_code for item in bundle.sporttery.mappings),
                    }
                )
            ),
        )

    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch:
        _validate_prepared_snapshot_query(self._bundle, query, "Sporttery")
        return self._bundle.sporttery


class NoAvailableLiveTrainingHistoryProvider(EloTrainingHistoryProvider):
    """Represent the absence of provenance-qualified persisted training results."""

    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.LIVE,
        provider_code="PERSISTED_MATCH_RESULTS",
        provenance="No provenance-qualified persisted MatchResult history configured",
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )

    async def fetch_elo_training_history(
        self,
        query: EloTrainingHistoryQuery,
    ) -> EloTrainingHistoryBatch:
        return EloTrainingHistoryBatch(
            competition_id=query.competition_id,
            target_season_id=query.target_season_id,
            as_of_at_utc=query.as_of_at_utc,
            sources=(),
        )


@runtime_checkable
class LiveSourceRepository(Protocol):
    def save_market_odds_ingestion(
        self,
        capture: MarketOddsIngestionCapture,
    ) -> SourceIngestionSummary: ...

    def save_sporttery_ingestion(
        self,
        capture: SportteryIngestionCapture,
    ) -> SourceIngestionSummary: ...

    def import_identity_review(
        self,
        review: IdentityReviewDocument,
    ) -> IdentityReviewSummary: ...

    def reconciliation_report(
        self,
        *,
        ingestion_id: str | None = None,
        generated_at_utc: datetime | None = None,
    ) -> ReconciliationReport: ...

    def prepare_analysis(
        self,
        request: PrepareAnalysisRequest,
        *,
        created_at_utc: datetime | None = None,
    ) -> LiveAnalysisPreparation: ...

    def load_prepared_sources(
        self,
        preparation_id: str,
    ) -> PreparedLiveSourceBundle: ...

    def find_ready_preparation_ids(
        self,
        kickoff_date_utc: date,
    ) -> tuple[Identifier, ...]: ...


@runtime_checkable
class CurrentMarketOddsCaptureProvider(MarketOddsProvider, Protocol):
    runtime_provenance: RuntimeProvenance

    @property
    def last_request_audit(self) -> ProviderRequestAudit | None: ...

    @property
    def last_raw_artifact_id(self) -> str | None: ...

    @property
    def last_raw_artifact_path(self) -> str | None: ...

    @property
    def last_raw_payload_sha256(self) -> str | None: ...


@runtime_checkable
class SportteryCaptureProvider(Protocol):
    runtime_provenance: RuntimeProvenance

    def capture_sporttery(
        self,
        *,
        ingested_at_utc: datetime,
    ) -> SportteryIngestionCapture: ...


class LiveMarketOddsIngestionService:
    def __init__(
        self,
        provider: CurrentMarketOddsCaptureProvider,
        repository: LiveSourceRepository,
        *,
        environment: RuntimeEnvironment | str,
        identity_cutoff_at_utc: datetime,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._environment = RuntimeEnvironment(environment)
        self._identity_cutoff_at_utc = identity_cutoff_at_utc
        self._clock = clock

    async def ingest(
        self,
        match_ids: tuple[str, ...],
    ) -> SourceIngestionSummary:
        if not match_ids or len(match_ids) != len(set(match_ids)):
            raise ValueError("market ingestion match IDs must be nonempty and unique")
        provenance = require_provider_runtime_provenance(
            self._provider,
            "market odds capture",
        )
        RuntimeEnvironmentGuard(self._environment).validate_input(provenance)
        batch = await self._provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=match_ids,
                as_of_at_utc=datetime.max.replace(tzinfo=timezone.utc),
            )
        )
        audit = self._provider.last_request_audit
        artifact_id = self._provider.last_raw_artifact_id
        artifact_path = self._provider.last_raw_artifact_path
        payload_sha256 = self._provider.last_raw_payload_sha256
        if (
            audit is None
            or audit.available_at_utc is None
            or artifact_id is None
            or artifact_path is None
            or payload_sha256 is None
        ):
            raise ValueError("market provider did not expose a complete raw capture")
        ingested_at = self._clock()
        if ingested_at < audit.received_at_utc:
            raise ValueError("market ingestion clock precedes provider receipt")
        source_batch = MarketOddsBatch(
            snapshots=tuple(
                item.model_copy(update={"ingested_at_utc": ingested_at})
                for item in batch.snapshots
            ),
            mappings=tuple(
                item.model_copy(update={"available_at_utc": ingested_at})
                for item in batch.mappings
            ),
            issues=batch.issues,
        )
        resolved_match_ids = tuple(
            sorted({item.match_id for item in source_batch.snapshots})
        )
        if resolved_match_ids:
            consensus_provider = ConsensusMarketOddsProvider(
                (_CapturedMarketOddsProvider(source_batch, provenance),)
            )
            consensus_batch = await consensus_provider.fetch_market_odds(
                SnapshotQuery(
                    match_ids=resolved_match_ids,
                    as_of_at_utc=ingested_at,
                )
            )
            lineages = consensus_provider.lineages
        else:
            consensus_batch = MarketOddsBatch(snapshots=(), mappings=(), issues=())
            lineages = ()
        capture = MarketOddsIngestionCapture(
            ingestion_id=stable_id("live-market-ingestion", artifact_id),
            provider_code=provenance.provider_code,
            requested_match_ids=match_ids,
            identity_cutoff_at_utc=self._identity_cutoff_at_utc,
            request_audit=audit,
            artifact=SourceIngestionArtifact(
                artifact_id=artifact_id,
                role=SourceArtifactRole.RAW_RESPONSE,
                payload_sha256=payload_sha256,
                source_path=artifact_path,
                captured_at_utc=audit.received_at_utc,
                available_at_utc=audit.available_at_utc,
            ),
            ingested_at_utc=ingested_at,
            source_batch=source_batch,
            consensus_batch=consensus_batch,
            consensus_lineages=lineages,
        )
        return self._repository.save_market_odds_ingestion(capture)


class LiveSportteryIngestionService:
    def __init__(
        self,
        provider: SportteryCaptureProvider,
        repository: LiveSourceRepository,
        *,
        environment: RuntimeEnvironment | str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._environment = RuntimeEnvironment(environment)
        self._clock = clock

    def ingest(self) -> SourceIngestionSummary:
        provenance = require_provider_runtime_provenance(
            self._provider,
            "Sporttery capture",
        )
        RuntimeEnvironmentGuard(self._environment).validate_input(provenance)
        capture = self._provider.capture_sporttery(
            ingested_at_utc=self._clock(),
        )
        if capture.provider_code != provenance.provider_code:
            raise ValueError("Sporttery capture conflicts with provider provenance")
        return self._repository.save_sporttery_ingestion(capture)


class PrepareLiveAnalysisService:
    def __init__(self, repository: LiveSourceRepository) -> None:
        self._repository = repository

    def prepare(
        self,
        request: PrepareAnalysisRequest,
        *,
        created_at_utc: datetime | None = None,
    ) -> LiveAnalysisPreparation:
        return self._repository.prepare_analysis(
            request,
            created_at_utc=created_at_utc,
        )


class _CapturedMarketOddsProvider:
    def __init__(
        self,
        batch: MarketOddsBatch,
        provenance: RuntimeProvenance,
    ) -> None:
        self._batch = batch
        self.runtime_provenance = provenance

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        requested = set(query.match_ids)
        return MarketOddsBatch(
            snapshots=tuple(
                item
                for item in self._batch.snapshots
                if item.match_id in requested
                and item.captured_at_utc <= query.as_of_at_utc
                and item.available_at_utc <= query.as_of_at_utc
                and item.ingested_at_utc <= query.as_of_at_utc
            ),
            mappings=tuple(
                item
                for item in self._batch.mappings
                if item.internal_match_id in requested
                and item.available_at_utc <= query.as_of_at_utc
            ),
            issues=self._batch.issues,
        )


def _prepared_runtime_provenance(
    label: str,
    provider_codes: tuple[str, ...],
) -> RuntimeProvenance:
    codes = tuple(sorted(set(provider_codes)))
    if len(codes) != 1:
        raise ValueError(f"prepared {label} requires exactly one provider")
    return RuntimeProvenance(
        environment=RuntimeEnvironment.LIVE,
        provider_code=codes[0],
        provenance="Frozen persisted live analysis preparation",
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )


def _validate_prepared_snapshot_query(
    bundle: PreparedLiveSourceBundle,
    query: SnapshotQuery,
    label: str,
) -> None:
    expected_match_ids = tuple(item.match_id for item in bundle.fixtures.matches)
    if (
        query.as_of_at_utc != bundle.preparation.decision_as_of_at_utc
        or query.match_ids != expected_match_ids
    ):
        raise ValueError(f"{label} query does not match the frozen preparation")
