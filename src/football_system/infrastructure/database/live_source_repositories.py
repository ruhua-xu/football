from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from football_system.application.live_sources import (
    IdentityReviewDocument,
    IdentityReviewSummary,
    LiveAnalysisPreparation,
    LiveSourceIngestionStatus,
    LiveSourceKind,
    MarketOddsIngestionCapture,
    PreparationDataQuality,
    PreparationReasonCode,
    PreparationStatus,
    PrepareAnalysisRequest,
    PreparedLiveSourceBundle,
    PreparedMatchInput,
    ReconciliationReport,
    SourceIngestionSummary,
    SourceReconciliationIssue,
    SportteryIngestionCapture,
    SportterySnapshotProvenance,
)
from football_system.application.market_consensus import derive_market_consensus
from football_system.application.market_consensus import (
    ConsensusConstituent,
    ConsensusLineage,
)
from football_system.application.ports.data_providers import (
    FixtureBatch,
    MarketOddsBatch,
    SportteryBatch,
)
from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_json,
    canonical_payload_sha256,
)
from football_system.domain.common import normalize_utc, stable_id, utc_now
from football_system.domain.identity import EXPLICIT_MAPPING_RESOLUTION
from football_system.domain.market import MarketKey, MarketType, SelectionKey
from football_system.domain.market_reconciliation import (
    MarketOddsReconciliationIssueReason,
)
from football_system.domain.match import (
    Competition,
    FixedBonusQuote,
    MarketOddsSnapshot,
    Match,
    MatchStatus,
    OddsQuote,
    ProviderMatchMapping,
    SaleStatus,
    SportteryBonusSnapshot,
    Team,
    TeamType,
)
from football_system.infrastructure.database.models import (
    BookmakerRecord,
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    FixtureIngestionCaptureRecord,
    FixtureObservationRecord,
    LiveAnalysisPreparationMatchRecord,
    LiveAnalysisPreparationRecord,
    LiveIdentityReviewMappingRecord,
    LiveIdentityReviewRecord,
    LiveMarketConsensusConstituentRecord,
    LiveMarketConsensusLineageRecord,
    LiveSourceArtifactRecord,
    LiveSourceIngestionRecord,
    LiveSourceIssueRecord,
    LiveSourceMappingRecord,
    LiveSourceMarketSnapshotRecord,
    LiveSourceSportterySnapshotRecord,
    MarketOddsQuoteRecord,
    MarketOddsSnapshotRecord,
    MatchRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    SportteryBonusQuoteRecord,
    SportteryBonusSnapshotRecord,
    TeamRecord,
)


class SqlAlchemyLiveSourceRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._session_factory = session_factory
        self._clock = clock

    def save_market_odds_ingestion(
        self,
        capture: MarketOddsIngestionCapture,
    ) -> SourceIngestionSummary:
        capture = MarketOddsIngestionCapture.model_validate(
            capture.model_dump(mode="python")
        )
        _verify_market_consensus(capture)
        return self._save_ingestion(capture, LiveSourceKind.MARKET_ODDS)

    def save_sporttery_ingestion(
        self,
        capture: SportteryIngestionCapture,
    ) -> SourceIngestionSummary:
        capture = SportteryIngestionCapture.model_validate(
            capture.model_dump(mode="python")
        )
        return self._save_ingestion(capture, LiveSourceKind.SPORTTERRY)

    def _save_ingestion(
        self,
        capture: MarketOddsIngestionCapture | SportteryIngestionCapture,
        source_kind: LiveSourceKind,
    ) -> SourceIngestionSummary:
        capture_json = canonical_json(capture)
        capture_hash = canonical_payload_sha256(capture)
        persisted_at = normalize_utc(self._clock())
        if persisted_at < capture.ingested_at_utc:
            raise ValueError("live source persistence cannot predate ingestion")
        try:
            with self._session_factory.begin() as session:
                existing = session.get(LiveSourceIngestionRecord, capture.ingestion_id)
                by_hash = session.scalar(
                    select(LiveSourceIngestionRecord).where(
                        LiveSourceIngestionRecord.capture_hash == capture_hash
                    )
                )
                if existing is not None or by_hash is not None:
                    if existing is None or by_hash is not existing:
                        raise ValueError(
                            "live source capture conflicts with stored immutable identity"
                        )
                    self._verify_stored_ingestion(
                        session, existing, capture, source_kind
                    )
                    inserted = False
                else:
                    self._persist_ingestion(
                        session,
                        capture,
                        source_kind,
                        persisted_at=persisted_at,
                        capture_json=capture_json,
                        capture_hash=capture_hash,
                    )
                    inserted = True
        except IntegrityError as error:
            raise ValueError(
                "live source capture conflicts with stored immutable data"
            ) from error
        return _ingestion_summary(capture, source_kind, inserted=inserted)

    def _persist_ingestion(
        self,
        session: Session,
        capture: MarketOddsIngestionCapture | SportteryIngestionCapture,
        source_kind: LiveSourceKind,
        *,
        persisted_at: datetime,
        capture_json: str,
        capture_hash: str,
    ) -> None:
        provider_codes = {capture.provider_code}
        if isinstance(capture, MarketOddsIngestionCapture):
            requested_match_ids = capture.requested_match_ids
            artifacts = (capture.artifact,)
            source_snapshots = capture.source_batch.snapshots
            consensus_snapshots = capture.consensus_batch.snapshots
            source_mappings = capture.source_batch.mappings
            consensus_mappings = capture.consensus_batch.mappings
            issues = capture.issues
            sporttery_snapshots: tuple[SportteryBonusSnapshot, ...] = ()
            provider_codes.update(
                item.provider_code
                for item in (*source_snapshots, *consensus_snapshots, *source_mappings)
            )
            bookmaker_codes = {
                item.bookmaker_code
                for item in (*source_snapshots, *consensus_snapshots)
            }
        else:
            requested_match_ids = ()
            artifacts = capture.artifacts
            source_snapshots = ()
            consensus_snapshots = ()
            source_mappings = capture.batch.mappings
            consensus_mappings = ()
            issues = capture.issues
            sporttery_snapshots = capture.batch.snapshots
            provider_codes.update(item.provider_code for item in source_mappings)
            bookmaker_codes = set()
        provider_codes.update(item.provider_code for item in issues)
        for provider_code in sorted(provider_codes):
            _ensure_provider(session, provider_code)
        for bookmaker_code in sorted(bookmaker_codes):
            _ensure_bookmaker(session, bookmaker_code)
        session.flush()
        for mapping in (*source_mappings, *consensus_mappings):
            _ensure_mapping(session, mapping)
        for snapshot in (*source_snapshots, *consensus_snapshots):
            _ensure_market_snapshot(session, snapshot)
        for snapshot in sporttery_snapshots:
            _ensure_sporttery_snapshot(session, snapshot)
        session.flush()

        status = _ingestion_status(len(issues))
        session.add(
            LiveSourceIngestionRecord(
                ingestion_id=capture.ingestion_id,
                schema_version=capture.schema_version,
                source_kind=source_kind.value,
                provider_id=stable_id("provider", capture.provider_code),
                data_mode=HistoricalDataMode.LIVE_STRICT.value,
                status=status.value,
                identity_cutoff_at_utc=capture.identity_cutoff_at_utc,
                source_ingested_at_utc=capture.ingested_at_utc,
                persisted_at_utc=persisted_at,
                requested_match_ids_json=canonical_json(requested_match_ids),
                artifact_count=len(artifacts),
                snapshot_count=(
                    len(source_snapshots)
                    if isinstance(capture, MarketOddsIngestionCapture)
                    else len(sporttery_snapshots)
                ),
                mapping_count=len(source_mappings),
                issue_count=len(issues),
                consensus_count=len(consensus_snapshots),
                capture_json=capture_json,
                capture_hash=capture_hash,
            )
        )
        session.flush()
        session.add_all(
            LiveSourceArtifactRecord(
                ingestion_id=capture.ingestion_id,
                artifact_id=item.artifact_id,
                artifact_no=index,
                role=item.role.value,
                payload_sha256=item.payload_sha256,
                source_path=item.source_path,
                captured_at_utc=item.captured_at_utc,
                available_at_utc=item.available_at_utc,
            )
            for index, item in enumerate(artifacts)
        )
        session.add_all(
            LiveSourceMappingRecord(
                ingestion_id=capture.ingestion_id,
                mapping_role=role,
                mapping_id=item.mapping_id,
                mapping_no=index,
            )
            for role, values in (
                ("SOURCE", source_mappings),
                ("CONSENSUS", consensus_mappings),
            )
            for index, item in enumerate(values)
        )
        session.add_all(
            LiveSourceMarketSnapshotRecord(
                ingestion_id=capture.ingestion_id,
                snapshot_role=role,
                snapshot_id=item.snapshot_id,
                snapshot_no=index,
            )
            for role, values in (
                ("SOURCE", source_snapshots),
                ("CONSENSUS", consensus_snapshots),
            )
            for index, item in enumerate(values)
        )
        session.add_all(
            LiveSourceIssueRecord(
                ingestion_id=capture.ingestion_id,
                issue_id=item.issue_id,
                issue_no=index,
                source_kind=item.source_kind.value,
                reason=item.reason.value,
                provider_id=stable_id("provider", item.provider_code),
                external_namespace=item.external_namespace,
                external_match_id=item.external_match_id,
                requested_match_id=item.requested_match_id,
                candidates_json=canonical_json(item.candidates),
                code=item.code,
                detail=item.detail,
                provider_identity_json=item.provider_identity_json,
            )
            for index, item in enumerate(issues)
        )
        session.flush()
        if isinstance(capture, MarketOddsIngestionCapture):
            self._persist_market_lineage(session, capture)
        else:
            self._persist_sporttery_lineage(session, capture)
        session.flush()

    @staticmethod
    def _persist_market_lineage(
        session: Session,
        capture: MarketOddsIngestionCapture,
    ) -> None:
        snapshots_by_source_key = {
            item.source_snapshot_key: item for item in capture.consensus_batch.snapshots
        }
        for lineage in capture.consensus_lineages:
            snapshot = snapshots_by_source_key[lineage.source_snapshot_key]
            session.add(
                LiveMarketConsensusLineageRecord(
                    ingestion_id=capture.ingestion_id,
                    consensus_snapshot_id=snapshot.snapshot_id,
                    policy=lineage.policy,
                    internal_match_id=lineage.match_id,
                    source_snapshot_key=lineage.source_snapshot_key,
                    constituent_count=len(lineage.constituents),
                )
            )
            session.flush()
            session.add_all(
                LiveMarketConsensusConstituentRecord(
                    ingestion_id=capture.ingestion_id,
                    consensus_snapshot_id=snapshot.snapshot_id,
                    source_snapshot_id=item.snapshot_id,
                    constituent_no=index,
                    provider_id=stable_id("provider", item.provider_code),
                    bookmaker_id=stable_id("bookmaker", item.bookmaker_code),
                    payload_hash=item.payload_hash,
                )
                for index, item in enumerate(lineage.constituents)
            )

    @staticmethod
    def _persist_sporttery_lineage(
        session: Session,
        capture: SportteryIngestionCapture,
    ) -> None:
        provenance = {item.snapshot_id: item for item in capture.provenance}
        session.add_all(
            LiveSourceSportterySnapshotRecord(
                ingestion_id=capture.ingestion_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_no=index,
                manual_document_artifact_id=(
                    provenance[snapshot.snapshot_id].manual_document_artifact_id
                ),
                source_artifact_id=provenance[snapshot.snapshot_id].source_artifact_id,
                provenance_json=canonical_json(provenance[snapshot.snapshot_id]),
                provenance_hash=canonical_payload_sha256(
                    provenance[snapshot.snapshot_id]
                ),
            )
            for index, snapshot in enumerate(capture.batch.snapshots)
        )

    def _verify_stored_ingestion(
        self,
        session: Session,
        record: LiveSourceIngestionRecord,
        capture: MarketOddsIngestionCapture | SportteryIngestionCapture,
        source_kind: LiveSourceKind,
    ) -> None:
        if (
            record.source_kind != source_kind.value
            or record.capture_hash != canonical_payload_sha256(capture)
            or record.capture_json != canonical_json(capture)
        ):
            raise ValueError("live source ingestion ID collision")
        expected_counts = _capture_child_counts(capture)
        actual_counts = {
            "artifacts": _count_where(
                session, LiveSourceArtifactRecord, capture.ingestion_id
            ),
            "mappings": _count_where(
                session, LiveSourceMappingRecord, capture.ingestion_id
            ),
            "market_snapshots": _count_where(
                session, LiveSourceMarketSnapshotRecord, capture.ingestion_id
            ),
            "sporttery_snapshots": _count_where(
                session, LiveSourceSportterySnapshotRecord, capture.ingestion_id
            ),
            "issues": _count_where(
                session, LiveSourceIssueRecord, capture.ingestion_id
            ),
            "lineages": _count_where(
                session, LiveMarketConsensusLineageRecord, capture.ingestion_id
            ),
            "constituents": _count_where(
                session,
                LiveMarketConsensusConstituentRecord,
                capture.ingestion_id,
            ),
        }
        if actual_counts != expected_counts:
            raise ValueError("live source ingestion replay has an incomplete graph")

    def import_identity_review(
        self,
        review: IdentityReviewDocument,
    ) -> IdentityReviewSummary:
        review = IdentityReviewDocument.model_validate(review.model_dump(mode="python"))
        review_json = canonical_json(review)
        review_hash = canonical_payload_sha256(review)
        imported_at = normalize_utc(self._clock())
        if imported_at < review.reviewed_at_utc:
            raise ValueError("identity review import cannot predate review")
        try:
            with self._session_factory.begin() as session:
                existing = session.get(LiveIdentityReviewRecord, review.review_id)
                by_hash = session.scalar(
                    select(LiveIdentityReviewRecord).where(
                        LiveIdentityReviewRecord.review_hash == review_hash
                    )
                )
                if existing is not None or by_hash is not None:
                    if existing is None or by_hash is not existing:
                        raise ValueError(
                            "identity review conflicts with stored immutable identity"
                        )
                    _verify_stored_review(session, existing, review)
                    inserted = False
                else:
                    self._persist_identity_review(
                        session,
                        review,
                        imported_at=imported_at,
                        review_json=review_json,
                        review_hash=review_hash,
                    )
                    inserted = True
        except IntegrityError as error:
            raise ValueError(
                "identity review conflicts with stored immutable data"
            ) from error
        return IdentityReviewSummary(
            review_id=review.review_id,
            source_ingestion_id=review.source_ingestion_id,
            inserted=inserted,
            mapping_count=len(review.mappings),
        )

    @staticmethod
    def _persist_identity_review(
        session: Session,
        review: IdentityReviewDocument,
        *,
        imported_at: datetime,
        review_json: str,
        review_hash: str,
    ) -> None:
        ingestion = session.get(
            LiveSourceIngestionRecord,
            review.source_ingestion_id,
        )
        if ingestion is None:
            raise KeyError(
                f"unknown live source ingestion: {review.source_ingestion_id}"
            )
        if ingestion.persisted_at_utc > imported_at:
            raise ValueError("identity review cannot predate source persistence")
        pending: list[tuple[object, SourceReconciliationIssue, str]] = []
        for mapping in review.mappings:
            match = session.get(MatchRecord, mapping.internal_match_id)
            if match is None:
                raise ValueError(
                    f"identity review targets an unknown match: {mapping.internal_match_id}"
                )
            issue = _review_source_issue(session, review.source_ingestion_id, mapping)
            provider_id = _ensure_provider(session, mapping.provider_code)
            mapping_id = stable_id(
                "provider-mapping",
                mapping.provider_code,
                mapping.external_namespace,
                mapping.external_match_id,
            )
            canonical_mapping = ProviderMatchMapping(
                mapping_id=mapping_id,
                provider_code=mapping.provider_code,
                external_namespace=mapping.external_namespace,
                external_match_id=mapping.external_match_id,
                internal_match_id=mapping.internal_match_id,
                resolution_method=EXPLICIT_MAPPING_RESOLUTION,
                confidence=Decimal(1),
                available_at_utc=imported_at,
            )
            _ensure_mapping(session, canonical_mapping)
            pending.append((mapping, issue, provider_id))
        session.flush()
        session.add(
            LiveIdentityReviewRecord(
                review_id=review.review_id,
                schema_version=review.schema_version,
                source_ingestion_id=review.source_ingestion_id,
                reviewed_by=review.reviewed_by,
                reviewed_at_utc=review.reviewed_at_utc,
                imported_at_utc=imported_at,
                mapping_count=len(review.mappings),
                review_json=review_json,
                review_hash=review_hash,
            )
        )
        session.flush()
        session.add_all(
            LiveIdentityReviewMappingRecord(
                review_id=review.review_id,
                mapping_no=index,
                source_ingestion_id=review.source_ingestion_id,
                source_issue_id=issue.issue_id,
                provider_id=provider_id,
                external_namespace=mapping.external_namespace,
                external_match_id=mapping.external_match_id,
                internal_match_id=mapping.internal_match_id,
                provider_mapping_id=stable_id(
                    "provider-mapping",
                    mapping.provider_code,
                    mapping.external_namespace,
                    mapping.external_match_id,
                ),
            )
            for index, (mapping, issue, provider_id) in enumerate(pending)
        )
        session.flush()

    def reconciliation_report(
        self,
        *,
        ingestion_id: str | None = None,
        generated_at_utc: datetime | None = None,
    ) -> ReconciliationReport:
        generated_at = normalize_utc(generated_at_utc or self._clock())
        with self._session_factory() as session:
            ingestion_statement = select(LiveSourceIngestionRecord).where(
                LiveSourceIngestionRecord.persisted_at_utc <= generated_at
            )
            if ingestion_id is not None:
                known = session.get(LiveSourceIngestionRecord, ingestion_id)
                if known is None:
                    raise KeyError(f"unknown live source ingestion: {ingestion_id}")
                ingestion_statement = ingestion_statement.where(
                    LiveSourceIngestionRecord.ingestion_id == ingestion_id
                )
            ingestions = tuple(session.scalars(ingestion_statement))
            ingestion_ids = tuple(sorted(item.ingestion_id for item in ingestions))
            if not ingestion_ids:
                return ReconciliationReport(
                    generated_at_utc=generated_at,
                    source_ingestion_ids=(),
                    unresolved=(),
                    ambiguous=(),
                    other_issues=(),
                )
            reviewed_identities = set(
                session.execute(
                    select(
                        LiveIdentityReviewMappingRecord.source_ingestion_id,
                        LiveIdentityReviewMappingRecord.provider_id,
                        LiveIdentityReviewMappingRecord.external_namespace,
                        LiveIdentityReviewMappingRecord.external_match_id,
                    )
                    .join(
                        LiveIdentityReviewRecord,
                        LiveIdentityReviewRecord.review_id
                        == LiveIdentityReviewMappingRecord.review_id,
                    )
                    .where(
                        LiveIdentityReviewRecord.imported_at_utc <= generated_at,
                        LiveIdentityReviewMappingRecord.source_ingestion_id.in_(
                            ingestion_ids
                        ),
                    )
                )
            )
            provider = aliased(ProviderRecord)
            issue_rows = session.execute(
                select(LiveSourceIssueRecord, provider.code)
                .join(
                    provider, provider.provider_id == LiveSourceIssueRecord.provider_id
                )
                .where(LiveSourceIssueRecord.ingestion_id.in_(ingestion_ids))
                .order_by(
                    LiveSourceIssueRecord.issue_id,
                    LiveSourceIssueRecord.ingestion_id,
                )
            )
            by_id: dict[str, SourceReconciliationIssue] = {}
            for record, provider_code in issue_rows:
                identity = (
                    record.ingestion_id,
                    record.provider_id,
                    record.external_namespace,
                    record.external_match_id,
                )
                if identity in reviewed_identities:
                    continue
                issue = _issue_from_record(record, provider_code)
                previous = by_id.get(issue.issue_id)
                if previous is not None and previous != issue:
                    raise ValueError("stored reconciliation issue ID collision")
                by_id[issue.issue_id] = issue
        unresolved = tuple(
            item
            for item in by_id.values()
            if item.reason is MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED
        )
        ambiguous = tuple(
            item
            for item in by_id.values()
            if item.reason is MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS
        )
        other = tuple(
            item
            for item in by_id.values()
            if item.reason
            not in {
                MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED,
                MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS,
            }
        )
        return ReconciliationReport(
            generated_at_utc=generated_at,
            source_ingestion_ids=ingestion_ids,
            unresolved=tuple(sorted(unresolved, key=lambda item: item.issue_id)),
            ambiguous=tuple(sorted(ambiguous, key=lambda item: item.issue_id)),
            other_issues=tuple(sorted(other, key=lambda item: item.issue_id)),
        )

    def prepare_analysis(
        self,
        request: PrepareAnalysisRequest,
        *,
        created_at_utc: datetime | None = None,
    ) -> LiveAnalysisPreparation:
        request = PrepareAnalysisRequest.model_validate(
            request.model_dump(mode="python")
        )
        created_at = normalize_utc(created_at_utc or self._clock())
        if created_at < request.decision_as_of_at_utc:
            raise ValueError("analysis preparation cannot predate its cutoff")
        with self._session_factory.begin() as session:
            preparation, selections = _build_preparation(session, request, created_at)
            existing = session.get(
                LiveAnalysisPreparationRecord,
                preparation.preparation_id,
            )
            by_hash = session.scalar(
                select(LiveAnalysisPreparationRecord).where(
                    LiveAnalysisPreparationRecord.report_hash == preparation.report_hash
                )
            )
            if existing is not None or by_hash is not None:
                if existing is None or by_hash is not existing:
                    raise ValueError(
                        "analysis preparation conflicts with stored immutable identity"
                    )
                _verify_stored_preparation(
                    session,
                    existing,
                    preparation,
                    selections,
                )
                return preparation
            _persist_preparation(session, preparation, selections)
            session.flush()
        return preparation

    def load_prepared_sources(
        self,
        preparation_id: str,
    ) -> PreparedLiveSourceBundle:
        with self._session_factory() as session:
            record = session.get(LiveAnalysisPreparationRecord, preparation_id)
            if record is None:
                raise KeyError(f"unknown live analysis preparation: {preparation_id}")
            preparation = _preparation_from_record(record)
            rows = tuple(
                session.scalars(
                    select(LiveAnalysisPreparationMatchRecord)
                    .where(
                        LiveAnalysisPreparationMatchRecord.preparation_id
                        == preparation_id,
                        LiveAnalysisPreparationMatchRecord.ready.is_(True),
                    )
                    .order_by(LiveAnalysisPreparationMatchRecord.match_no)
                )
            )
            return _load_prepared_bundle(session, preparation, rows)

    def find_ready_preparation_ids(
        self,
        kickoff_date_utc: date,
    ) -> tuple[str, ...]:
        day_start = datetime.combine(kickoff_date_utc, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        in_day = (
            select(1)
            .select_from(LiveAnalysisPreparationMatchRecord)
            .join(
                FixtureObservationRecord,
                FixtureObservationRecord.observation_id
                == LiveAnalysisPreparationMatchRecord.fixture_observation_id,
            )
            .where(
                LiveAnalysisPreparationMatchRecord.preparation_id
                == LiveAnalysisPreparationRecord.preparation_id,
                LiveAnalysisPreparationMatchRecord.ready.is_(True),
                FixtureObservationRecord.kickoff_at_utc >= day_start,
                FixtureObservationRecord.kickoff_at_utc < day_end,
            )
            .exists()
        )
        outside_day = (
            select(1)
            .select_from(LiveAnalysisPreparationMatchRecord)
            .join(
                FixtureObservationRecord,
                FixtureObservationRecord.observation_id
                == LiveAnalysisPreparationMatchRecord.fixture_observation_id,
            )
            .where(
                LiveAnalysisPreparationMatchRecord.preparation_id
                == LiveAnalysisPreparationRecord.preparation_id,
                LiveAnalysisPreparationMatchRecord.ready.is_(True),
                (
                    (FixtureObservationRecord.kickoff_at_utc < day_start)
                    | (FixtureObservationRecord.kickoff_at_utc >= day_end)
                ),
            )
            .exists()
        )
        with self._session_factory() as session:
            return tuple(
                session.scalars(
                    select(LiveAnalysisPreparationRecord.preparation_id)
                    .where(
                        LiveAnalysisPreparationRecord.status
                        == PreparationStatus.ANALYSIS_INPUT_READY.value,
                        in_day,
                        ~outside_day,
                    )
                    .order_by(LiveAnalysisPreparationRecord.preparation_id)
                )
            )


def _ingestion_summary(
    capture: MarketOddsIngestionCapture | SportteryIngestionCapture,
    source_kind: LiveSourceKind,
    *,
    inserted: bool,
) -> SourceIngestionSummary:
    if isinstance(capture, MarketOddsIngestionCapture):
        snapshot_count = len(capture.source_batch.snapshots)
        mapping_count = len(capture.source_batch.mappings)
        issue_count = len(capture.issues)
        consensus_count = len(capture.consensus_batch.snapshots)
        artifact_count = 1
    else:
        snapshot_count = len(capture.batch.snapshots)
        mapping_count = len(capture.batch.mappings)
        issue_count = len(capture.issues)
        consensus_count = 0
        artifact_count = len(capture.artifacts)
    return SourceIngestionSummary(
        ingestion_id=capture.ingestion_id,
        source_kind=source_kind,
        status=_ingestion_status(issue_count),
        inserted=inserted,
        artifact_count=artifact_count,
        snapshot_count=snapshot_count,
        mapping_count=mapping_count,
        issue_count=issue_count,
        consensus_count=consensus_count,
    )


def _ingestion_status(issue_count: int) -> LiveSourceIngestionStatus:
    return (
        LiveSourceIngestionStatus.COMPLETED_WITH_ISSUES
        if issue_count
        else LiveSourceIngestionStatus.COMPLETED
    )


def _capture_child_counts(
    capture: MarketOddsIngestionCapture | SportteryIngestionCapture,
) -> dict[str, int]:
    if isinstance(capture, MarketOddsIngestionCapture):
        return {
            "artifacts": 1,
            "mappings": len(capture.source_batch.mappings)
            + len(capture.consensus_batch.mappings),
            "market_snapshots": len(capture.source_batch.snapshots)
            + len(capture.consensus_batch.snapshots),
            "sporttery_snapshots": 0,
            "issues": len(capture.issues),
            "lineages": len(capture.consensus_lineages),
            "constituents": sum(
                len(item.constituents) for item in capture.consensus_lineages
            ),
        }
    return {
        "artifacts": len(capture.artifacts),
        "mappings": len(capture.batch.mappings),
        "market_snapshots": 0,
        "sporttery_snapshots": len(capture.batch.snapshots),
        "issues": len(capture.issues),
        "lineages": 0,
        "constituents": 0,
    }


def _count_where(session: Session, record_type: type, ingestion_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(record_type)
            .where(record_type.ingestion_id == ingestion_id)
        )
        or 0
    )


def _verify_market_consensus(capture: MarketOddsIngestionCapture) -> None:
    source_by_id = {item.snapshot_id: item for item in capture.source_batch.snapshots}
    consensus_by_key = {
        item.source_snapshot_key: item for item in capture.consensus_batch.snapshots
    }
    mappings_by_match = {
        item.internal_match_id: item for item in capture.consensus_batch.mappings
    }
    if (
        len(consensus_by_key) != len(capture.consensus_batch.snapshots)
        or len(capture.consensus_lineages) != len(capture.consensus_batch.snapshots)
        or len(mappings_by_match) != len(capture.consensus_batch.mappings)
    ):
        raise ValueError("market consensus graph is incomplete")
    seen_keys: set[str] = set()
    for lineage in capture.consensus_lineages:
        if lineage.source_snapshot_key in seen_keys:
            raise ValueError("market consensus lineage is duplicated")
        seen_keys.add(lineage.source_snapshot_key)
        try:
            constituents = tuple(
                source_by_id[item.snapshot_id] for item in lineage.constituents
            )
            actual_snapshot = consensus_by_key[lineage.source_snapshot_key]
            actual_mapping = mappings_by_match[lineage.match_id]
        except KeyError as error:
            raise ValueError(
                "market consensus graph references unknown data"
            ) from error
        expected_snapshot, expected_mapping, expected_lineage = derive_market_consensus(
            lineage.match_id,
            constituents,
        )
        if (
            actual_snapshot != expected_snapshot
            or actual_mapping != expected_mapping
            or lineage != expected_lineage
        ):
            raise ValueError("market consensus failed deterministic recomputation")
    if seen_keys != set(consensus_by_key):
        raise ValueError("market consensus lineage does not cover every snapshot")


def _ensure_provider(session: Session, provider_code: str) -> str:
    provider_id = stable_id("provider", provider_code)
    by_id = session.get(ProviderRecord, provider_id)
    by_code = session.scalar(
        select(ProviderRecord).where(ProviderRecord.code == provider_code)
    )
    if by_id is not None and by_code is not None and by_id is not by_code:
        raise ValueError("provider identity conflicts with stored immutable data")
    values = {
        "code": provider_code,
        "name": provider_code.replace("_", " ").title(),
        "provider_kind": _provider_kind(provider_code),
    }
    existing = by_id or by_code
    if existing is None:
        session.add(ProviderRecord(provider_id=provider_id, **values))
    else:
        _assert_fields(existing, {"provider_id": provider_id, **values}, "provider")
    return provider_id


def _ensure_bookmaker(session: Session, bookmaker_code: str) -> str:
    bookmaker_id = stable_id("bookmaker", bookmaker_code)
    by_id = session.get(BookmakerRecord, bookmaker_id)
    by_code = session.scalar(
        select(BookmakerRecord).where(BookmakerRecord.code == bookmaker_code)
    )
    if by_id is not None and by_code is not None and by_id is not by_code:
        raise ValueError("bookmaker identity conflicts with stored immutable data")
    values = {
        "code": bookmaker_code,
        "name": bookmaker_code.replace("_", " ").title(),
    }
    existing = by_id or by_code
    if existing is None:
        session.add(BookmakerRecord(bookmaker_id=bookmaker_id, **values))
    else:
        _assert_fields(existing, {"bookmaker_id": bookmaker_id, **values}, "bookmaker")
    return bookmaker_id


def _ensure_mapping(session: Session, mapping: ProviderMatchMapping) -> None:
    provider_id = _ensure_provider(session, mapping.provider_code)
    session.flush()
    by_id = session.get(ProviderMatchMappingRecord, mapping.mapping_id)
    by_identity = session.scalar(
        select(ProviderMatchMappingRecord).where(
            ProviderMatchMappingRecord.provider_id == provider_id,
            ProviderMatchMappingRecord.external_namespace == mapping.external_namespace,
            ProviderMatchMappingRecord.external_match_id == mapping.external_match_id,
        )
    )
    if by_id is not None and by_identity is not None and by_id is not by_identity:
        raise ValueError("provider mapping has conflicting stored identities")
    existing = by_id or by_identity
    values = {
        "mapping_id": mapping.mapping_id,
        "provider_id": provider_id,
        "external_namespace": mapping.external_namespace,
        "external_match_id": mapping.external_match_id,
        "internal_match_id": mapping.internal_match_id,
        "resolution_method": mapping.resolution_method,
        "confidence": mapping.confidence,
        "supersedes_mapping_id": None,
    }
    if existing is None:
        session.add(
            ProviderMatchMappingRecord(
                **values,
                available_at_utc=mapping.available_at_utc,
            )
        )
        return
    _assert_fields(existing, values, f"provider mapping {mapping.mapping_id}")
    if mapping.available_at_utc < existing.available_at_utc:
        raise ValueError("provider mapping availability cannot be backdated")


def _ensure_market_snapshot(session: Session, snapshot: MarketOddsSnapshot) -> None:
    if canonical_payload_sha256(snapshot.three_way_odds()) != snapshot.payload_hash:
        raise ValueError("market snapshot payload hash is inconsistent")
    provider_id = _ensure_provider(session, snapshot.provider_code)
    bookmaker_id = _ensure_bookmaker(session, snapshot.bookmaker_code)
    session.flush()
    by_id = session.get(MarketOddsSnapshotRecord, snapshot.snapshot_id)
    by_source = session.scalar(
        select(MarketOddsSnapshotRecord).where(
            MarketOddsSnapshotRecord.provider_id == provider_id,
            MarketOddsSnapshotRecord.source_snapshot_key
            == snapshot.source_snapshot_key,
        )
    )
    if by_id is not None and by_source is not None and by_id is not by_source:
        raise ValueError("market snapshot has conflicting stored identities")
    existing = by_id or by_source
    values = {
        "snapshot_id": snapshot.snapshot_id,
        "internal_match_id": snapshot.match_id,
        "provider_id": provider_id,
        "bookmaker_id": bookmaker_id,
        "market_key": snapshot.market.canonical,
        "market_type": snapshot.market.market_type.value,
        "handicap_value": snapshot.market.handicap_value,
        "captured_at_utc": snapshot.captured_at_utc,
        "available_at_utc": snapshot.available_at_utc,
        "ingested_at_utc": snapshot.ingested_at_utc,
        "source_snapshot_key": snapshot.source_snapshot_key,
        "payload_hash": snapshot.payload_hash,
    }
    if existing is None:
        session.add(MarketOddsSnapshotRecord(**values))
        session.flush()
        session.add_all(
            MarketOddsQuoteRecord(
                snapshot_id=snapshot.snapshot_id,
                selection_key=quote.selection.value,
                odds=quote.odds,
            )
            for quote in snapshot.quotes
        )
        return
    _assert_fields(existing, values, f"market snapshot {snapshot.snapshot_id}")
    stored_quotes = {
        item.selection_key: item.odds
        for item in session.scalars(
            select(MarketOddsQuoteRecord).where(
                MarketOddsQuoteRecord.snapshot_id == snapshot.snapshot_id
            )
        )
    }
    expected_quotes = {item.selection.value: item.odds for item in snapshot.quotes}
    if stored_quotes != expected_quotes:
        raise ValueError("market snapshot quote graph conflicts with stored data")


def _ensure_sporttery_snapshot(
    session: Session,
    snapshot: SportteryBonusSnapshot,
) -> None:
    if canonical_payload_sha256(snapshot.three_way_bonus()) != snapshot.payload_hash:
        raise ValueError("Sporttery snapshot payload hash is inconsistent")
    provider_id = _ensure_provider(session, snapshot.provider_code)
    session.flush()
    by_id = session.get(SportteryBonusSnapshotRecord, snapshot.snapshot_id)
    by_source = session.scalar(
        select(SportteryBonusSnapshotRecord).where(
            SportteryBonusSnapshotRecord.provider_id == provider_id,
            SportteryBonusSnapshotRecord.source_snapshot_key
            == snapshot.source_snapshot_key,
        )
    )
    if by_id is not None and by_source is not None and by_id is not by_source:
        raise ValueError("Sporttery snapshot has conflicting stored identities")
    existing = by_id or by_source
    values = {
        "snapshot_id": snapshot.snapshot_id,
        "internal_match_id": snapshot.match_id,
        "provider_id": provider_id,
        "sporttery_match_no": snapshot.sporttery_match_no,
        "market_key": snapshot.market.canonical,
        "market_type": snapshot.market.market_type.value,
        "handicap_value": snapshot.market.handicap_value,
        "sale_status": snapshot.sale_status.value,
        "captured_at_utc": snapshot.captured_at_utc,
        "available_at_utc": snapshot.available_at_utc,
        "ingested_at_utc": snapshot.ingested_at_utc,
        "source_snapshot_key": snapshot.source_snapshot_key,
        "payload_hash": snapshot.payload_hash,
    }
    if existing is None:
        session.add(SportteryBonusSnapshotRecord(**values))
        session.flush()
        session.add_all(
            SportteryBonusQuoteRecord(
                snapshot_id=snapshot.snapshot_id,
                selection_key=quote.selection.value,
                fixed_bonus=quote.fixed_bonus,
            )
            for quote in snapshot.quotes
        )
        return
    _assert_fields(existing, values, f"Sporttery snapshot {snapshot.snapshot_id}")
    stored_quotes = {
        item.selection_key: item.fixed_bonus
        for item in session.scalars(
            select(SportteryBonusQuoteRecord).where(
                SportteryBonusQuoteRecord.snapshot_id == snapshot.snapshot_id
            )
        )
    }
    expected_quotes = {
        item.selection.value: item.fixed_bonus for item in snapshot.quotes
    }
    if stored_quotes != expected_quotes:
        raise ValueError("Sporttery snapshot quote graph conflicts with stored data")


def _review_source_issue(
    session: Session,
    ingestion_id: str,
    mapping: object,
) -> SourceReconciliationIssue:
    provider_id = stable_id("provider", mapping.provider_code)
    records = tuple(
        session.scalars(
            select(LiveSourceIssueRecord)
            .where(
                LiveSourceIssueRecord.ingestion_id == ingestion_id,
                LiveSourceIssueRecord.provider_id == provider_id,
                LiveSourceIssueRecord.external_namespace == mapping.external_namespace,
                LiveSourceIssueRecord.external_match_id == mapping.external_match_id,
                LiveSourceIssueRecord.reason.in_(
                    (
                        MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED.value,
                        MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS.value,
                    )
                ),
            )
            .order_by(LiveSourceIssueRecord.issue_id)
        )
    )
    candidates: list[SourceReconciliationIssue] = []
    for record in records:
        provider = session.get(ProviderRecord, record.provider_id)
        if provider is None:
            raise ValueError("reconciliation issue provider is missing")
        issue = _issue_from_record(record, provider.code)
        if (
            issue.reason is MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS
            and mapping.internal_match_id not in issue.candidates
        ):
            continue
        candidates.append(issue)
    if not candidates:
        raise ValueError(
            "identity review mapping has no matching unresolved or ambiguous issue"
        )
    return candidates[0]


def _issue_from_record(
    record: LiveSourceIssueRecord,
    provider_code: str,
) -> SourceReconciliationIssue:
    return SourceReconciliationIssue(
        issue_id=record.issue_id,
        source_kind=LiveSourceKind(record.source_kind),
        reason=MarketOddsReconciliationIssueReason(record.reason),
        provider_code=provider_code,
        external_namespace=record.external_namespace,
        external_match_id=record.external_match_id,
        requested_match_id=record.requested_match_id,
        candidates=tuple(json.loads(record.candidates_json)),
        code=record.code,
        detail=record.detail,
        provider_identity_json=record.provider_identity_json,
    )


def _verify_stored_review(
    session: Session,
    record: LiveIdentityReviewRecord,
    review: IdentityReviewDocument,
) -> None:
    if (
        record.review_json != canonical_json(review)
        or record.review_hash != canonical_payload_sha256(review)
        or record.source_ingestion_id != review.source_ingestion_id
        or record.mapping_count != len(review.mappings)
    ):
        raise ValueError("identity review ID collision")
    rows = tuple(
        session.scalars(
            select(LiveIdentityReviewMappingRecord)
            .where(LiveIdentityReviewMappingRecord.review_id == review.review_id)
            .order_by(LiveIdentityReviewMappingRecord.mapping_no)
        )
    )
    if len(rows) != len(review.mappings):
        raise ValueError("identity review replay has an incomplete graph")
    for row, expected in zip(rows, review.mappings, strict=True):
        provider = session.get(ProviderRecord, row.provider_id)
        if provider is None or (
            provider.code != expected.provider_code
            or row.external_namespace != expected.external_namespace
            or row.external_match_id != expected.external_match_id
            or row.internal_match_id != expected.internal_match_id
        ):
            raise ValueError("identity review replay mapping graph conflicts")


def _provider_kind(provider_code: str) -> str:
    if "SPORTTERY" in provider_code:
        return "SPORTTERY"
    if "ODDS" in provider_code or "MARKET" in provider_code:
        return "MARKET_ODDS"
    return "FIXTURE"


def _assert_fields(record: object, expected: dict[str, object], label: str) -> None:
    mismatched = [
        field for field, value in expected.items() if getattr(record, field) != value
    ]
    if mismatched:
        raise ValueError(
            f"immutable {label} conflicts on fields: {', '.join(mismatched)}"
        )


@dataclass(frozen=True, slots=True)
class _PreparationSelection:
    match: PreparedMatchInput
    market_ingestion_id: str | None
    sporttery_ingestion_id: str | None


def _build_preparation(
    session: Session,
    request: PrepareAnalysisRequest,
    created_at: datetime,
) -> tuple[LiveAnalysisPreparation, tuple[_PreparationSelection, ...]]:
    if session.get(CompetitionRecord, request.competition_id) is None:
        raise ValueError(
            f"analysis preparation competition is unknown: {request.competition_id}"
        )
    scope_rows = tuple(
        session.execute(
            select(MatchRecord, CanonicalMatchIdentityRecord)
            .join(
                CanonicalMatchIdentityRecord,
                CanonicalMatchIdentityRecord.internal_match_id
                == MatchRecord.internal_match_id,
            )
            .where(
                MatchRecord.competition_id == request.competition_id,
                CanonicalMatchIdentityRecord.season == request.season_id,
            )
            .order_by(MatchRecord.internal_match_id)
        )
    )
    scope = {
        match.internal_match_id: (match, identity) for match, identity in scope_rows
    }
    if request.expected_match_ids:
        match_ids = tuple(sorted(request.expected_match_ids))
        for match_id in match_ids:
            stored = session.get(MatchRecord, match_id)
            if stored is not None and match_id not in scope:
                raise ValueError(
                    f"expected match is outside preparation scope: {match_id}"
                )
    else:
        match_ids = tuple(sorted(scope))
    blocked_identity_ids = _open_identity_issue_match_ids(
        session,
        request.decision_as_of_at_utc,
    )
    selections: list[_PreparationSelection] = []
    for match_id in match_ids:
        stored = scope.get(match_id)
        match_record, identity_record = stored if stored is not None else (None, None)
        fixture = _latest_fixture_observation(
            session,
            match_id,
            request.decision_as_of_at_utc,
        )
        base_visible = bool(
            match_record is not None
            and identity_record is not None
            and match_record.available_at_utc <= request.decision_as_of_at_utc
            and identity_record.available_at_utc <= request.decision_as_of_at_utc
            and _fixture_origin_visible(
                session,
                match_record,
                request.decision_as_of_at_utc,
            )
        )
        fixture_visible = bool(
            base_visible
            and fixture is not None
            and fixture.status == MatchStatus.SCHEDULED.value
            and request.kickoff_from_utc
            <= fixture.kickoff_at_utc
            <= request.kickoff_to_utc
        )
        if not request.expected_match_ids and (
            fixture is None
            or not (
                request.kickoff_from_utc
                <= fixture.kickoff_at_utc
                <= request.kickoff_to_utc
            )
        ):
            continue

        market = _latest_market_candidate(
            session,
            match_id,
            request.decision_as_of_at_utc,
        )
        sporttery = _latest_sporttery_candidate(
            session,
            match_id,
            request.decision_as_of_at_utc,
        )
        market_visible = market is not None
        market_verified = bool(market and market.verified)
        bookmaker_count = market.bookmaker_count if market is not None else 0
        odds_age = market.odds_age_seconds if market is not None else None
        coverage_sufficient = bool(
            market_verified
            and bookmaker_count >= request.policy.minimum_bookmaker_count
        )
        market_fresh = bool(
            market_verified
            and odds_age is not None
            and odds_age <= request.policy.maximum_odds_age_seconds
        )
        sporttery_visible = sporttery is not None
        sporttery_verified = bool(sporttery and sporttery.verified)
        cutoff_clean = True
        identity_resolved = base_visible and match_id not in blocked_identity_ids
        quality = PreparationDataQuality(
            fixture_visible=fixture_visible,
            identity_resolved=identity_resolved,
            market_consensus_visible=market_visible,
            market_consensus_verified=market_verified,
            bookmaker_coverage_sufficient=coverage_sufficient,
            market_fresh=market_fresh,
            sporttery_visible=sporttery_visible,
            sporttery_provenance_verified=sporttery_verified,
            cutoff_clean=cutoff_clean,
        )
        reasons: list[PreparationReasonCode] = []
        if not fixture_visible:
            reasons.append(PreparationReasonCode.FIXTURE_NOT_VISIBLE)
        if not identity_resolved:
            reasons.append(PreparationReasonCode.IDENTITY_NOT_RESOLVED)
        if not market_visible:
            reasons.append(PreparationReasonCode.MARKET_CONSENSUS_NOT_VISIBLE)
        elif not market_verified:
            reasons.append(PreparationReasonCode.MARKET_CONSENSUS_INVALID)
        else:
            if not market_fresh:
                reasons.append(PreparationReasonCode.ODDS_STALE)
            if not coverage_sufficient:
                reasons.append(PreparationReasonCode.BOOKMAKER_COVERAGE_LOW)
        if not sporttery_visible:
            reasons.append(PreparationReasonCode.SPORTTERRY_NOT_VISIBLE)
        elif not sporttery_verified:
            reasons.append(PreparationReasonCode.SPORTTERRY_PROVENANCE_INVALID)
        if not cutoff_clean:
            reasons.append(PreparationReasonCode.SOURCE_CROSSES_CUTOFF)
        prepared_match = PreparedMatchInput(
            match_id=match_id,
            fixture_observation_id=(
                fixture.observation_id if fixture is not None else None
            ),
            market_consensus_snapshot_id=(
                market.snapshot.snapshot_id if market is not None else None
            ),
            sporttery_bonus_snapshot_id=(
                sporttery.snapshot.snapshot_id if sporttery is not None else None
            ),
            bookmaker_count=bookmaker_count,
            odds_age_seconds=odds_age,
            reason_codes=tuple(sorted(set(reasons), key=str)),
            data_quality=quality,
        )
        selections.append(
            _PreparationSelection(
                match=prepared_match,
                market_ingestion_id=(
                    market.ingestion_id if market is not None else None
                ),
                sporttery_ingestion_id=(
                    sporttery.ingestion_id if sporttery is not None else None
                ),
            )
        )
    match_inputs = tuple(item.match for item in selections)
    ready_count = sum(item.data_quality.ready for item in match_inputs)
    status = (
        PreparationStatus.ANALYSIS_INPUT_READY
        if ready_count > 0
        and (request.allow_partial_inputs or ready_count == len(match_inputs))
        else PreparationStatus.NO_ANALYSIS_INSUFFICIENT_DATA
    )
    preparation_id = request.preparation_id or stable_id(
        "live-analysis-preparation",
        canonical_payload_sha256(request),
        created_at,
    )
    preparation = LiveAnalysisPreparation.freeze(
        preparation_id=preparation_id,
        status=status,
        decision_as_of_at_utc=request.decision_as_of_at_utc,
        kickoff_from_utc=request.kickoff_from_utc,
        kickoff_to_utc=request.kickoff_to_utc,
        competition_id=request.competition_id,
        season_id=request.season_id,
        allow_partial_inputs=request.allow_partial_inputs,
        policy=request.policy,
        expected_match_ids=request.expected_match_ids,
        matches=match_inputs,
        created_at_utc=created_at,
    )
    return preparation, tuple(selections)


def _fixture_origin_visible(
    session: Session,
    match: MatchRecord,
    cutoff: datetime,
) -> bool:
    if match.fixture_ingestion_id is None:
        return True
    ingestion = session.get(FixtureIngestionCaptureRecord, match.fixture_ingestion_id)
    return ingestion is not None and ingestion.ingested_at_utc <= cutoff


def _latest_fixture_observation(
    session: Session,
    match_id: str,
    cutoff: datetime,
) -> FixtureObservationRecord | None:
    return session.scalar(
        select(FixtureObservationRecord)
        .join(
            FixtureIngestionCaptureRecord,
            FixtureIngestionCaptureRecord.ingestion_id
            == FixtureObservationRecord.ingestion_id,
        )
        .where(
            FixtureObservationRecord.internal_match_id == match_id,
            FixtureObservationRecord.available_at_utc <= cutoff,
            FixtureIngestionCaptureRecord.ingested_at_utc <= cutoff,
        )
        .order_by(
            FixtureObservationRecord.available_at_utc.desc(),
            FixtureIngestionCaptureRecord.ingested_at_utc.desc(),
            FixtureObservationRecord.observation_id.desc(),
        )
    )


def _open_identity_issue_match_ids(session: Session, cutoff: datetime) -> set[str]:
    reviewed_identities = set(
        session.execute(
            select(
                LiveIdentityReviewMappingRecord.source_ingestion_id,
                LiveIdentityReviewMappingRecord.provider_id,
                LiveIdentityReviewMappingRecord.external_namespace,
                LiveIdentityReviewMappingRecord.external_match_id,
            )
            .join(
                LiveIdentityReviewRecord,
                LiveIdentityReviewRecord.review_id
                == LiveIdentityReviewMappingRecord.review_id,
            )
            .where(LiveIdentityReviewRecord.imported_at_utc <= cutoff)
        )
    )
    rows = session.scalars(
        select(LiveSourceIssueRecord)
        .join(
            LiveSourceIngestionRecord,
            LiveSourceIngestionRecord.ingestion_id
            == LiveSourceIssueRecord.ingestion_id,
        )
        .where(
            LiveSourceIngestionRecord.persisted_at_utc <= cutoff,
            LiveSourceIssueRecord.reason.in_(
                (
                    MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED.value,
                    MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS.value,
                )
            ),
        )
    )
    blocked: set[str] = set()
    for row in rows:
        if (
            row.ingestion_id,
            row.provider_id,
            row.external_namespace,
            row.external_match_id,
        ) in reviewed_identities:
            continue
        if row.requested_match_id is not None:
            blocked.add(row.requested_match_id)
        blocked.update(json.loads(row.candidates_json))
    return blocked


@dataclass(frozen=True, slots=True)
class _MarketCandidate:
    ingestion_id: str
    snapshot: MarketOddsSnapshot
    verified: bool
    bookmaker_count: int
    odds_age_seconds: int | None


def _latest_market_candidate(
    session: Session,
    match_id: str,
    cutoff: datetime,
) -> _MarketCandidate | None:
    row = session.execute(
        select(
            LiveSourceMarketSnapshotRecord,
            MarketOddsSnapshotRecord,
        )
        .join(
            LiveSourceIngestionRecord,
            LiveSourceIngestionRecord.ingestion_id
            == LiveSourceMarketSnapshotRecord.ingestion_id,
        )
        .join(
            MarketOddsSnapshotRecord,
            MarketOddsSnapshotRecord.snapshot_id
            == LiveSourceMarketSnapshotRecord.snapshot_id,
        )
        .where(
            LiveSourceMarketSnapshotRecord.snapshot_role == "CONSENSUS",
            MarketOddsSnapshotRecord.internal_match_id == match_id,
            LiveSourceIngestionRecord.persisted_at_utc <= cutoff,
            LiveSourceIngestionRecord.source_ingested_at_utc <= cutoff,
            MarketOddsSnapshotRecord.captured_at_utc <= cutoff,
            MarketOddsSnapshotRecord.available_at_utc <= cutoff,
            MarketOddsSnapshotRecord.ingested_at_utc <= cutoff,
        )
        .order_by(
            MarketOddsSnapshotRecord.available_at_utc.desc(),
            MarketOddsSnapshotRecord.captured_at_utc.desc(),
            MarketOddsSnapshotRecord.ingested_at_utc.desc(),
            LiveSourceIngestionRecord.persisted_at_utc.desc(),
            MarketOddsSnapshotRecord.snapshot_id.desc(),
        )
    ).first()
    if row is None:
        return None
    edge, snapshot_record = row
    stored_snapshot = _market_snapshot_from_record(session, snapshot_record)
    verified, constituents, exact_snapshot = _verify_stored_consensus_candidate(
        session,
        edge.ingestion_id,
        stored_snapshot,
    )
    if not constituents:
        return _MarketCandidate(
            edge.ingestion_id,
            exact_snapshot or stored_snapshot,
            False,
            0,
            None,
        )
    oldest_capture = min(item.captured_at_utc for item in constituents)
    age = max(0, ceil((cutoff - oldest_capture).total_seconds()))
    return _MarketCandidate(
        ingestion_id=edge.ingestion_id,
        snapshot=exact_snapshot or stored_snapshot,
        verified=verified,
        bookmaker_count=len({item.bookmaker_code for item in constituents}),
        odds_age_seconds=age,
    )


def _verify_stored_consensus_candidate(
    session: Session,
    ingestion_id: str,
    snapshot: MarketOddsSnapshot,
) -> tuple[
    bool,
    tuple[MarketOddsSnapshot, ...],
    MarketOddsSnapshot | None,
]:
    lineage = session.get(
        LiveMarketConsensusLineageRecord,
        (ingestion_id, snapshot.snapshot_id),
    )
    if lineage is None:
        return False, (), None
    rows = tuple(
        session.scalars(
            select(LiveMarketConsensusConstituentRecord)
            .where(
                LiveMarketConsensusConstituentRecord.ingestion_id == ingestion_id,
                LiveMarketConsensusConstituentRecord.consensus_snapshot_id
                == snapshot.snapshot_id,
            )
            .order_by(LiveMarketConsensusConstituentRecord.constituent_no)
        )
    )
    constituents: list[MarketOddsSnapshot] = []
    claims: list[ConsensusConstituent] = []
    for row in rows:
        source_record = session.get(MarketOddsSnapshotRecord, row.source_snapshot_id)
        provider = session.get(ProviderRecord, row.provider_id)
        bookmaker = session.get(BookmakerRecord, row.bookmaker_id)
        if source_record is None or provider is None or bookmaker is None:
            return False, tuple(constituents), None
        constituents.append(_market_snapshot_from_record(session, source_record))
        claims.append(
            ConsensusConstituent(
                provider_code=provider.code,
                bookmaker_code=bookmaker.code,
                snapshot_id=row.source_snapshot_id,
                payload_hash=row.payload_hash,
            )
        )
    if len(rows) != lineage.constituent_count:
        return False, tuple(constituents), None
    capture = _market_capture_from_record(session, ingestion_id)
    captured_sources = {item.snapshot_id: item for item in capture.source_batch.snapshots}
    try:
        exact_constituents = tuple(
            captured_sources[row.source_snapshot_id] for row in rows
        )
        exact_snapshot = next(
            item
            for item in capture.consensus_batch.snapshots
            if item.snapshot_id == snapshot.snapshot_id
        )
        exact_mapping = next(
            item
            for item in capture.consensus_batch.mappings
            if item.internal_match_id == snapshot.match_id
        )
        exact_lineage = next(
            item
            for item in capture.consensus_lineages
            if item.source_snapshot_key == snapshot.source_snapshot_key
        )
    except (KeyError, StopIteration):
        return False, tuple(constituents), None
    expected_snapshot, expected_mapping, expected_lineage = derive_market_consensus(
        lineage.internal_match_id,
        exact_constituents,
    )
    stored_mapping = _mapping_for_ingestion_match(
        session,
        ingestion_id,
        "CONSENSUS",
        snapshot.match_id,
    )
    actual_lineage = ConsensusLineage(
        policy=lineage.policy,
        match_id=lineage.internal_match_id,
        market=snapshot.market,
        source_snapshot_key=lineage.source_snapshot_key,
        constituents=tuple(claims),
    )
    verified = bool(
        _market_snapshot_matches_database_projection(snapshot, exact_snapshot)
        and len(constituents) == len(exact_constituents)
        and all(
            _market_snapshot_matches_database_projection(stored, exact)
            for stored, exact in zip(
                constituents,
                exact_constituents,
                strict=True,
            )
        )
        and exact_snapshot == expected_snapshot
        and exact_mapping == expected_mapping
        and exact_lineage == expected_lineage
        and stored_mapping == expected_mapping
        and actual_lineage == expected_lineage
    )
    return verified, tuple(constituents), expected_snapshot


@dataclass(frozen=True, slots=True)
class _SportteryCandidate:
    ingestion_id: str
    snapshot: SportteryBonusSnapshot
    verified: bool


def _latest_sporttery_candidate(
    session: Session,
    match_id: str,
    cutoff: datetime,
) -> _SportteryCandidate | None:
    row = session.execute(
        select(
            LiveSourceSportterySnapshotRecord,
            SportteryBonusSnapshotRecord,
        )
        .join(
            LiveSourceIngestionRecord,
            LiveSourceIngestionRecord.ingestion_id
            == LiveSourceSportterySnapshotRecord.ingestion_id,
        )
        .join(
            SportteryBonusSnapshotRecord,
            SportteryBonusSnapshotRecord.snapshot_id
            == LiveSourceSportterySnapshotRecord.snapshot_id,
        )
        .where(
            SportteryBonusSnapshotRecord.internal_match_id == match_id,
            LiveSourceIngestionRecord.persisted_at_utc <= cutoff,
            LiveSourceIngestionRecord.source_ingested_at_utc <= cutoff,
            SportteryBonusSnapshotRecord.captured_at_utc <= cutoff,
            SportteryBonusSnapshotRecord.available_at_utc <= cutoff,
            SportteryBonusSnapshotRecord.ingested_at_utc <= cutoff,
        )
        .order_by(
            SportteryBonusSnapshotRecord.available_at_utc.desc(),
            SportteryBonusSnapshotRecord.captured_at_utc.desc(),
            SportteryBonusSnapshotRecord.ingested_at_utc.desc(),
            LiveSourceIngestionRecord.persisted_at_utc.desc(),
            SportteryBonusSnapshotRecord.snapshot_id.desc(),
        )
    ).first()
    if row is None:
        return None
    edge, snapshot_record = row
    stored_snapshot = _sporttery_snapshot_from_record(session, snapshot_record)
    verified, exact_snapshot = _verify_stored_sporttery_candidate(
        session,
        edge,
        stored_snapshot,
    )
    return _SportteryCandidate(
        edge.ingestion_id,
        exact_snapshot or stored_snapshot,
        verified,
    )


def _verify_stored_sporttery_candidate(
    session: Session,
    edge: LiveSourceSportterySnapshotRecord,
    snapshot: SportteryBonusSnapshot,
) -> tuple[bool, SportteryBonusSnapshot | None]:
    try:
        provenance = SportterySnapshotProvenance.model_validate_json(
            edge.provenance_json
        )
    except ValueError:
        return False, None
    capture = _sporttery_capture_from_record(session, edge.ingestion_id)
    exact_snapshot = next(
        (
            item
            for item in capture.batch.snapshots
            if item.snapshot_id == snapshot.snapshot_id
        ),
        None,
    )
    exact_provenance = next(
        (
            item
            for item in capture.provenance
            if item.snapshot_id == snapshot.snapshot_id
        ),
        None,
    )
    if exact_snapshot is None or exact_provenance is None:
        return False, None
    if (
        not _sporttery_snapshot_matches_database_projection(
            snapshot,
            exact_snapshot,
        )
        or provenance != exact_provenance
        or provenance.snapshot_id != snapshot.snapshot_id
        or provenance.source_snapshot_key != snapshot.source_snapshot_key
        or canonical_payload_sha256(provenance) != edge.provenance_hash
        or canonical_payload_sha256(exact_snapshot.three_way_bonus())
        != exact_snapshot.payload_hash
    ):
        return False, exact_snapshot
    document = session.get(
        LiveSourceArtifactRecord,
        (edge.ingestion_id, edge.manual_document_artifact_id),
    )
    source = session.get(
        LiveSourceArtifactRecord,
        (edge.ingestion_id, edge.source_artifact_id),
    )
    mapping = _mapping_for_ingestion_match(
        session,
        edge.ingestion_id,
        "SOURCE",
        snapshot.match_id,
    )
    return bool(
        document is not None
        and document.role == "MANUAL_DOCUMENT"
        and source is not None
        and source.role == "SOURCE_ARTIFACT"
        and source.payload_sha256 == provenance.source_artifact_sha256
        and mapping is not None
        and mapping.provider_code == snapshot.provider_code
    ), exact_snapshot


def _persist_preparation(
    session: Session,
    preparation: LiveAnalysisPreparation,
    selections: tuple[_PreparationSelection, ...],
) -> None:
    session.add(
        LiveAnalysisPreparationRecord(
            preparation_id=preparation.preparation_id,
            schema_version=preparation.schema_version,
            status=preparation.status.value,
            decision_as_of_at_utc=preparation.decision_as_of_at_utc,
            kickoff_from_utc=preparation.kickoff_from_utc,
            kickoff_to_utc=preparation.kickoff_to_utc,
            competition_id=preparation.competition_id,
            season_id=preparation.season_id,
            allow_partial_inputs=preparation.allow_partial_inputs,
            policy_version=preparation.policy.version,
            maximum_odds_age_seconds=(preparation.policy.maximum_odds_age_seconds),
            minimum_bookmaker_count=preparation.policy.minimum_bookmaker_count,
            expected_match_ids_json=canonical_json(preparation.expected_match_ids),
            match_count=len(preparation.matches),
            ready_match_count=len(preparation.ready_match_ids),
            created_at_utc=preparation.created_at_utc,
            preparation_json=canonical_json(preparation),
            report_hash=preparation.report_hash,
        )
    )
    session.flush()
    session.add_all(
        LiveAnalysisPreparationMatchRecord(
            preparation_id=preparation.preparation_id,
            internal_match_id=item.match.match_id,
            match_no=index,
            ready=item.match.data_quality.ready,
            fixture_observation_id=item.match.fixture_observation_id,
            market_ingestion_id=item.market_ingestion_id,
            market_consensus_snapshot_id=(item.match.market_consensus_snapshot_id),
            sporttery_ingestion_id=item.sporttery_ingestion_id,
            sporttery_bonus_snapshot_id=item.match.sporttery_bonus_snapshot_id,
            bookmaker_count=item.match.bookmaker_count,
            odds_age_seconds=item.match.odds_age_seconds,
            reason_codes_json=canonical_json(
                tuple(reason.value for reason in item.match.reason_codes)
            ),
            data_quality_json=canonical_json(item.match.data_quality),
        )
        for index, item in enumerate(selections)
    )


def _verify_stored_preparation(
    session: Session,
    record: LiveAnalysisPreparationRecord,
    preparation: LiveAnalysisPreparation,
    selections: tuple[_PreparationSelection, ...],
) -> None:
    if (
        record.preparation_json != canonical_json(preparation)
        or record.report_hash != preparation.report_hash
        or record.match_count != len(preparation.matches)
        or record.ready_match_count != len(preparation.ready_match_ids)
    ):
        raise ValueError("analysis preparation ID collision")
    rows = tuple(
        session.scalars(
            select(LiveAnalysisPreparationMatchRecord)
            .where(
                LiveAnalysisPreparationMatchRecord.preparation_id
                == preparation.preparation_id
            )
            .order_by(LiveAnalysisPreparationMatchRecord.match_no)
        )
    )
    if len(rows) != len(selections):
        raise ValueError("analysis preparation replay has an incomplete graph")
    for row, selected in zip(rows, selections, strict=True):
        expected = selected.match
        fields = {
            "internal_match_id": expected.match_id,
            "ready": expected.data_quality.ready,
            "fixture_observation_id": expected.fixture_observation_id,
            "market_ingestion_id": selected.market_ingestion_id,
            "market_consensus_snapshot_id": expected.market_consensus_snapshot_id,
            "sporttery_ingestion_id": selected.sporttery_ingestion_id,
            "sporttery_bonus_snapshot_id": expected.sporttery_bonus_snapshot_id,
            "bookmaker_count": expected.bookmaker_count,
            "odds_age_seconds": expected.odds_age_seconds,
            "reason_codes_json": canonical_json(
                tuple(reason.value for reason in expected.reason_codes)
            ),
            "data_quality_json": canonical_json(expected.data_quality),
        }
        _assert_fields(row, fields, f"preparation match {expected.match_id}")


def _preparation_from_record(
    record: LiveAnalysisPreparationRecord,
) -> LiveAnalysisPreparation:
    preparation = LiveAnalysisPreparation.model_validate_json(record.preparation_json)
    if (
        preparation.preparation_id != record.preparation_id
        or preparation.report_hash != record.report_hash
        or canonical_json(preparation) != record.preparation_json
    ):
        raise ValueError("stored analysis preparation failed verification")
    return preparation


def _load_prepared_bundle(
    session: Session,
    preparation: LiveAnalysisPreparation,
    rows: tuple[LiveAnalysisPreparationMatchRecord, ...],
) -> PreparedLiveSourceBundle:
    expected_ready = preparation.ready_match_ids
    if tuple(row.internal_match_id for row in rows) != expected_ready:
        raise ValueError("stored preparation ready-match graph is inconsistent")
    competition_record = session.get(CompetitionRecord, preparation.competition_id)
    if competition_record is None:
        raise ValueError("prepared competition is missing")
    competition = Competition(
        competition_id=competition_record.competition_id,
        canonical_key=competition_record.canonical_key,
        name=competition_record.name,
        country_code=competition_record.country_code,
    )
    matches: list[Match] = []
    fixture_mappings: dict[str, ProviderMatchMapping] = {}
    market_snapshots: list[MarketOddsSnapshot] = []
    market_mappings: dict[str, ProviderMatchMapping] = {}
    sporttery_snapshots: list[SportteryBonusSnapshot] = []
    sporttery_mappings: dict[str, ProviderMatchMapping] = {}
    team_ids: set[str] = set()
    for row in rows:
        if (
            row.fixture_observation_id is None
            or row.market_ingestion_id is None
            or row.market_consensus_snapshot_id is None
            or row.sporttery_ingestion_id is None
            or row.sporttery_bonus_snapshot_id is None
        ):
            raise ValueError("ready preparation match is missing frozen source lineage")
        match_record = session.get(MatchRecord, row.internal_match_id)
        identity = session.get(CanonicalMatchIdentityRecord, row.internal_match_id)
        observation = session.get(
            FixtureObservationRecord,
            row.fixture_observation_id,
        )
        if (
            match_record is None
            or identity is None
            or observation is None
            or match_record.competition_id != preparation.competition_id
            or identity.season != preparation.season_id
            or observation.internal_match_id != row.internal_match_id
            or not (
                preparation.kickoff_from_utc
                <= observation.kickoff_at_utc
                <= preparation.kickoff_to_utc
            )
        ):
            raise ValueError("prepared fixture lineage is inconsistent")
        matches.append(
            Match(
                match_id=match_record.internal_match_id,
                competition_id=match_record.competition_id,
                home_team_id=match_record.home_team_id,
                away_team_id=match_record.away_team_id,
                kickoff_at_utc=observation.kickoff_at_utc,
                status=MatchStatus(observation.status),
                available_at_utc=observation.available_at_utc,
            )
        )
        team_ids.update((match_record.home_team_id, match_record.away_team_id))
        fixture_mapping_record = session.get(
            ProviderMatchMappingRecord,
            observation.provider_mapping_id,
        )
        if fixture_mapping_record is None:
            raise ValueError("prepared fixture mapping is missing")
        fixture_mapping = _mapping_from_record(session, fixture_mapping_record)
        fixture_mappings[fixture_mapping.mapping_id] = fixture_mapping

        market_record = session.get(
            MarketOddsSnapshotRecord,
            row.market_consensus_snapshot_id,
        )
        if market_record is None:
            raise ValueError("prepared market snapshot is missing")
        market_snapshot = _exact_consensus_snapshot(
            session,
            row.market_ingestion_id,
            market_record,
        )
        market_mapping = _mapping_for_ingestion_match(
            session,
            row.market_ingestion_id,
            "CONSENSUS",
            row.internal_match_id,
        )
        if market_mapping is None:
            raise ValueError("prepared market mapping is missing")
        market_snapshots.append(market_snapshot)
        market_mappings[market_mapping.mapping_id] = market_mapping

        sporttery_record = session.get(
            SportteryBonusSnapshotRecord,
            row.sporttery_bonus_snapshot_id,
        )
        if sporttery_record is None:
            raise ValueError("prepared Sporttery snapshot is missing")
        sporttery_snapshot = _exact_sporttery_snapshot(
            session,
            row.sporttery_ingestion_id,
            sporttery_record,
        )
        sporttery_mapping = _mapping_for_ingestion_match(
            session,
            row.sporttery_ingestion_id,
            "SOURCE",
            row.internal_match_id,
        )
        if sporttery_mapping is None:
            raise ValueError("prepared Sporttery mapping is missing")
        sporttery_snapshots.append(sporttery_snapshot)
        sporttery_mappings[sporttery_mapping.mapping_id] = sporttery_mapping
    teams = tuple(_team_from_record(session, team_id) for team_id in sorted(team_ids))
    return PreparedLiveSourceBundle(
        preparation=preparation,
        competition_id=preparation.competition_id,
        season_id=preparation.season_id,
        fixtures=FixtureBatch(
            competitions=(competition,),
            teams=teams,
            matches=tuple(matches),
            mappings=tuple(fixture_mappings.values()),
        ),
        market_odds=MarketOddsBatch(
            snapshots=tuple(market_snapshots),
            mappings=tuple(market_mappings.values()),
            issues=(),
        ),
        sporttery=SportteryBatch(
            snapshots=tuple(sporttery_snapshots),
            mappings=tuple(sporttery_mappings.values()),
        ),
    )


def _market_snapshot_from_record(
    session: Session,
    record: MarketOddsSnapshotRecord,
) -> MarketOddsSnapshot:
    provider = session.get(ProviderRecord, record.provider_id)
    bookmaker = session.get(BookmakerRecord, record.bookmaker_id)
    if provider is None or bookmaker is None:
        raise ValueError("market snapshot provider lineage is missing")
    stored_quotes = {
        SelectionKey(item.selection_key): item.odds
        for item in session.scalars(
            select(MarketOddsQuoteRecord).where(
                MarketOddsQuoteRecord.snapshot_id == record.snapshot_id
            )
        )
    }
    quotes = tuple(
        OddsQuote(selection=selection, odds=stored_quotes[selection])
        for selection in SelectionKey
    )
    snapshot = MarketOddsSnapshot(
        snapshot_id=record.snapshot_id,
        match_id=record.internal_match_id,
        provider_code=provider.code,
        bookmaker_code=bookmaker.code,
        market=MarketKey(
            market_type=MarketType(record.market_type),
            handicap_value=record.handicap_value,
        ),
        quotes=quotes,
        captured_at_utc=record.captured_at_utc,
        available_at_utc=record.available_at_utc,
        ingested_at_utc=record.ingested_at_utc,
        source_snapshot_key=record.source_snapshot_key,
        payload_hash=record.payload_hash,
    )
    return snapshot


def _exact_consensus_snapshot(
    session: Session,
    ingestion_id: str,
    record: MarketOddsSnapshotRecord,
) -> MarketOddsSnapshot:
    stored = _market_snapshot_from_record(session, record)
    verified, _, exact = _verify_stored_consensus_candidate(
        session,
        ingestion_id,
        stored,
    )
    if not verified or exact is None:
        raise ValueError("prepared consensus snapshot failed lineage verification")
    return exact


def _market_capture_from_record(
    session: Session,
    ingestion_id: str,
) -> MarketOddsIngestionCapture:
    record = session.get(LiveSourceIngestionRecord, ingestion_id)
    if record is None or record.source_kind != LiveSourceKind.MARKET_ODDS.value:
        raise ValueError("market ingestion lineage is missing")
    capture = MarketOddsIngestionCapture.model_validate_json(record.capture_json)
    if (
        capture.ingestion_id != ingestion_id
        or canonical_json(capture) != record.capture_json
        or canonical_payload_sha256(capture) != record.capture_hash
    ):
        raise ValueError("stored market ingestion failed capture verification")
    return capture


def _sporttery_snapshot_from_record(
    session: Session,
    record: SportteryBonusSnapshotRecord,
) -> SportteryBonusSnapshot:
    provider = session.get(ProviderRecord, record.provider_id)
    if provider is None:
        raise ValueError("Sporttery snapshot provider lineage is missing")
    stored_quotes = {
        SelectionKey(item.selection_key): item.fixed_bonus
        for item in session.scalars(
            select(SportteryBonusQuoteRecord).where(
                SportteryBonusQuoteRecord.snapshot_id == record.snapshot_id
            )
        )
    }
    quotes = tuple(
        FixedBonusQuote(selection=selection, fixed_bonus=stored_quotes[selection])
        for selection in SelectionKey
    )
    snapshot = SportteryBonusSnapshot(
        snapshot_id=record.snapshot_id,
        match_id=record.internal_match_id,
        provider_code=provider.code,
        sporttery_match_no=record.sporttery_match_no,
        market=MarketKey(
            market_type=MarketType(record.market_type),
            handicap_value=record.handicap_value,
        ),
        quotes=quotes,
        sale_status=SaleStatus(record.sale_status),
        captured_at_utc=record.captured_at_utc,
        available_at_utc=record.available_at_utc,
        ingested_at_utc=record.ingested_at_utc,
        source_snapshot_key=record.source_snapshot_key,
        payload_hash=record.payload_hash,
    )
    return snapshot


def _exact_sporttery_snapshot(
    session: Session,
    ingestion_id: str,
    record: SportteryBonusSnapshotRecord,
) -> SportteryBonusSnapshot:
    stored = _sporttery_snapshot_from_record(session, record)
    edge = session.get(
        LiveSourceSportterySnapshotRecord,
        (ingestion_id, record.snapshot_id),
    )
    if edge is None:
        raise ValueError("prepared Sporttery ownership lineage is missing")
    verified, exact = _verify_stored_sporttery_candidate(session, edge, stored)
    if not verified or exact is None:
        raise ValueError("prepared Sporttery snapshot failed lineage verification")
    return exact


def _sporttery_capture_from_record(
    session: Session,
    ingestion_id: str,
) -> SportteryIngestionCapture:
    record = session.get(LiveSourceIngestionRecord, ingestion_id)
    if record is None or record.source_kind != LiveSourceKind.SPORTTERRY.value:
        raise ValueError("Sporttery ingestion lineage is missing")
    capture = SportteryIngestionCapture.model_validate_json(record.capture_json)
    if (
        capture.ingestion_id != ingestion_id
        or canonical_json(capture) != record.capture_json
        or canonical_payload_sha256(capture) != record.capture_hash
    ):
        raise ValueError("stored Sporttery ingestion failed capture verification")
    return capture


def _market_snapshot_matches_database_projection(
    stored: MarketOddsSnapshot,
    exact: MarketOddsSnapshot,
) -> bool:
    if stored.model_dump(mode="python", exclude={"quotes"}) != exact.model_dump(
        mode="python",
        exclude={"quotes"},
    ):
        return False
    scale = Decimal("0.000001")
    stored_odds = stored.three_way_odds()
    exact_odds = exact.three_way_odds()
    return all(
        stored_odds.for_selection(selection)
        == exact_odds.for_selection(selection).quantize(scale)
        for selection in SelectionKey
    )


def _sporttery_snapshot_matches_database_projection(
    stored: SportteryBonusSnapshot,
    exact: SportteryBonusSnapshot,
) -> bool:
    if stored.model_dump(mode="python", exclude={"quotes"}) != exact.model_dump(
        mode="python",
        exclude={"quotes"},
    ):
        return False
    scale = Decimal("0.000001")
    stored_bonus = stored.three_way_bonus()
    exact_bonus = exact.three_way_bonus()
    return all(
        stored_bonus.for_selection(selection)
        == exact_bonus.for_selection(selection).quantize(scale)
        for selection in SelectionKey
    )


def _mapping_for_ingestion_match(
    session: Session,
    ingestion_id: str,
    role: str,
    match_id: str,
) -> ProviderMatchMapping | None:
    records = tuple(
        session.scalars(
            select(ProviderMatchMappingRecord)
            .join(
                LiveSourceMappingRecord,
                LiveSourceMappingRecord.mapping_id
                == ProviderMatchMappingRecord.mapping_id,
            )
            .where(
                LiveSourceMappingRecord.ingestion_id == ingestion_id,
                LiveSourceMappingRecord.mapping_role == role,
                ProviderMatchMappingRecord.internal_match_id == match_id,
            )
        )
    )
    if not records:
        return None
    if len(records) != 1:
        raise ValueError("ingestion has multiple mappings for one match and role")
    return _mapping_from_record(session, records[0])


def _mapping_from_record(
    session: Session,
    record: ProviderMatchMappingRecord,
) -> ProviderMatchMapping:
    provider = session.get(ProviderRecord, record.provider_id)
    if provider is None:
        raise ValueError("provider mapping provider is missing")
    return ProviderMatchMapping(
        mapping_id=record.mapping_id,
        provider_code=provider.code,
        external_namespace=record.external_namespace,
        external_match_id=record.external_match_id,
        internal_match_id=record.internal_match_id,
        resolution_method=record.resolution_method,
        confidence=record.confidence,
        available_at_utc=record.available_at_utc,
    )


def _team_from_record(session: Session, team_id: str) -> Team:
    record = session.get(TeamRecord, team_id)
    if record is None:
        raise ValueError(f"prepared team is missing: {team_id}")
    return Team(
        team_id=record.team_id,
        canonical_key=record.canonical_key,
        name=record.name,
        team_type=TeamType(record.team_type),
    )
