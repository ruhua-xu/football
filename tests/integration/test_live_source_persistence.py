from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.live_sources import (
    IdentityReviewDocument,
    LiveAnalysisInputPolicy,
    LiveSourceIngestionStatus,
    LiveSourceKind,
    MarketOddsIngestionCapture,
    PrepareAnalysisRequest,
    PreparedLiveFixtureProvider,
    PreparedLiveMarketOddsProvider,
    PreparedLiveSportteryProvider,
    PreparationStatus,
    NoAvailableLiveTrainingHistoryProvider,
    ReviewedIdentityMapping,
    SourceArtifactRole,
    SourceIngestionArtifact,
    SourceReconciliationIssue,
    SportteryIngestionCapture,
    SportterySnapshotProvenance,
)
from football_system.application.model_analysis import (
    PreparedFixtureObservationRef,
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.market_consensus import derive_market_consensus
from football_system.application.ports.data_providers import (
    MarketOddsBatch,
    SportteryBatch,
)
from football_system.domain.archive import canonical_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType, SelectionKey
from football_system.domain.market_reconciliation import (
    MarketOddsReconciliationIssueReason,
)
from football_system.domain.match import (
    FixedBonusQuote,
    MarketOddsSnapshot,
    OddsQuote,
    ProviderMatchMapping,
    SaleStatus,
    SportteryBonusSnapshot,
)
from football_system.domain.prediction import FusionPolicyName, QuantModelEvaluationStatus
from football_system.domain.services.elo_baseline import EloBaselineConfig
from football_system.config import AppSettings
from football_system.domain.raw_data import (
    ProviderRequestAudit,
    ProviderRequestOutcome,
)
from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.live_source_repositories import (
    SqlAlchemyLiveSourceRepository,
)
from football_system.infrastructure.database.models import (
    LiveAnalysisRunPreparationRecord,
    LiveSourceIngestionRecord,
    MatchRecord,
    ProviderMatchMappingRecord,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.review_repositories import (
    SqlAlchemyReviewArtifactRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from tests.integration.test_fixture_ingestion_persistence import (
    _capture as fixture_capture,
)

UTC = timezone.utc
MARKET_RECEIVED = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
MARKET_INGESTED = MARKET_RECEIVED + timedelta(minutes=1)
SPORTTERY_INGESTED = MARKET_RECEIVED + timedelta(minutes=2)
PERSISTED = MARKET_RECEIVED + timedelta(hours=1)
DECISION = datetime(2026, 9, 4, 13, 0, tzinfo=UTC)
CREATED = DECISION + timedelta(minutes=1)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)


def test_live_ingestions_are_append_only_idempotent_and_prepare_frozen_sources() -> (
    None
):
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    SqlAlchemyMatchIdentityRepository(
        sessions,
        clock=lambda: MARKET_RECEIVED,
    ).register_fixture_ingestion(fixture_capture("live-source"))
    clock = [PERSISTED]
    repository = SqlAlchemyLiveSourceRepository(sessions, clock=lambda: clock[0])
    market_capture = _market_capture()
    sporttery_capture = _sporttery_capture()

    market_inserted = repository.save_market_odds_ingestion(market_capture)
    market_replayed = repository.save_market_odds_ingestion(market_capture)
    sporttery_inserted = repository.save_sporttery_ingestion(sporttery_capture)

    assert market_inserted.status is LiveSourceIngestionStatus.COMPLETED
    assert market_inserted.inserted is True
    assert market_replayed.inserted is False
    assert market_inserted.consensus_count == 1
    assert sporttery_inserted.inserted is True
    with sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(LiveSourceIngestionRecord))
            == 2
        )

    request = PrepareAnalysisRequest(
        decision_as_of_at_utc=DECISION,
        kickoff_from_utc=datetime(2026, 9, 9, tzinfo=UTC),
        kickoff_to_utc=datetime(2026, 9, 11, 23, 59, tzinfo=UTC),
        competition_id="competition",
        season_id="2026/27",
        expected_match_ids=("match",),
        policy=LiveAnalysisInputPolicy(
            maximum_odds_age_seconds=200_000,
            minimum_bookmaker_count=2,
        ),
    )
    preparation = repository.prepare_analysis(request, created_at_utc=CREATED)
    replayed = repository.prepare_analysis(request, created_at_utc=CREATED)
    bundle = repository.load_prepared_sources(preparation.preparation_id)

    assert preparation.status is PreparationStatus.ANALYSIS_INPUT_READY
    assert replayed == preparation
    assert preparation.ready_match_ids == ("match",)
    assert preparation.matches[0].bookmaker_count == 2
    assert preparation.matches[0].fixture_observation_id == (
        "observation-live-source"
    )
    assert bundle.preparation == preparation
    assert bundle.fixtures.matches[0].kickoff_at_utc == fixture_capture(
        "live-source"
    ).observations[0].kickoff_at_utc
    assert len(bundle.market_odds.snapshots) == 1
    assert len(bundle.sporttery.snapshots) == 1
    assert repository.find_ready_preparation_ids(date(2026, 9, 10)) == (
        preparation.preparation_id,
    )
    assert repository.find_ready_preparation_ids(date(2026, 9, 9)) == ()

    with engine.begin() as connection:
        try:
            connection.execute(
                text(
                    "UPDATE live_source_ingestions SET status = 'COMPLETED' "
                    "WHERE ingestion_id = :ingestion_id"
                ),
                {"ingestion_id": market_capture.ingestion_id},
            )
        except Exception as error:
            assert "append-only" in str(error)
        else:
            raise AssertionError("live source ingestion update unexpectedly succeeded")


def test_identity_review_closes_visible_issue_and_persists_explicit_mapping() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    SqlAlchemyMatchIdentityRepository(
        sessions,
        clock=lambda: MARKET_RECEIVED,
    ).register_fixture_ingestion(fixture_capture("review"))
    clock = [PERSISTED]
    repository = SqlAlchemyLiveSourceRepository(sessions, clock=lambda: clock[0])
    unresolved = _unresolved_sporttery_capture()
    repository.save_sporttery_ingestion(unresolved)

    before = repository.reconciliation_report(
        ingestion_id=unresolved.ingestion_id,
        generated_at_utc=PERSISTED,
    )
    assert len(before.unresolved) == 1

    review = IdentityReviewDocument(
        review_id="review-sporttery-001",
        source_ingestion_id=unresolved.ingestion_id,
        reviewed_by="operator-a",
        reviewed_at_utc=PERSISTED + timedelta(minutes=1),
        mappings=(
            ReviewedIdentityMapping(
                provider_code="SPORTTERY_MANUAL",
                external_namespace="sporttery_match",
                external_match_id="2026-09-03:SYN001",
                internal_match_id="match",
            ),
        ),
    )
    clock[0] = PERSISTED + timedelta(minutes=2)
    inserted = repository.import_identity_review(review)
    replayed = repository.import_identity_review(review)

    assert inserted.inserted is True
    assert replayed.inserted is False
    at_review = repository.reconciliation_report(
        ingestion_id=unresolved.ingestion_id,
        generated_at_utc=review.reviewed_at_utc,
    )
    after = repository.reconciliation_report(
        ingestion_id=unresolved.ingestion_id,
        generated_at_utc=clock[0],
    )
    assert len(at_review.unresolved) == 1
    assert after.unresolved == ()
    mapping_id = stable_id(
        "provider-mapping",
        "SPORTTERY_MANUAL",
        "sporttery_match",
        "2026-09-03:SYN001",
    )
    with sessions() as session:
        mapping = session.get(ProviderMatchMappingRecord, mapping_id)
        assert mapping is not None
        assert mapping.internal_match_id == "match"
        assert mapping.resolution_method == "EXPLICIT_MAPPING"


def test_prepared_model_analysis_uses_rescheduled_fixture_and_seals_relation() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    first_fixture = fixture_capture("prepared-model-first")
    SqlAlchemyMatchIdentityRepository(
        sessions,
        clock=lambda: MARKET_RECEIVED,
    ).register_fixture_ingestion(first_fixture)
    rescheduled_kickoff = first_fixture.observations[0].kickoff_at_utc + timedelta(
        hours=2
    )
    rescheduled_available = DECISION - timedelta(hours=1)
    SqlAlchemyMatchIdentityRepository(
        sessions,
        clock=lambda: rescheduled_available + timedelta(minutes=2),
    ).register_fixture_ingestion(
        fixture_capture(
            "prepared-model-rescheduled",
            available_at=rescheduled_available,
            kickoff_at=rescheduled_kickoff,
            observation_id="observation-prepared-model-rescheduled",
        )
    )
    live_repository = SqlAlchemyLiveSourceRepository(
        sessions,
        clock=lambda: PERSISTED,
    )
    live_repository.save_market_odds_ingestion(_market_capture())
    live_repository.save_sporttery_ingestion(_sporttery_capture())
    preparation = live_repository.prepare_analysis(
        PrepareAnalysisRequest(
            decision_as_of_at_utc=DECISION,
            kickoff_from_utc=datetime(2026, 9, 9, tzinfo=UTC),
            kickoff_to_utc=datetime(2026, 9, 11, 23, 59, tzinfo=UTC),
            competition_id="competition",
            season_id="2026/27",
            expected_match_ids=("match",),
            policy=LiveAnalysisInputPolicy(
                maximum_odds_age_seconds=200_000,
                minimum_bookmaker_count=2,
            ),
        ),
        created_at_utc=CREATED,
    )
    bundle = live_repository.load_prepared_sources(preparation.preparation_id)
    analysis_repository = SqlAlchemyAnalysisRepository(sessions)
    elo_config = EloBaselineConfig()
    service = RunModelAnalysisService(
        fixture_provider=PreparedLiveFixtureProvider(bundle),
        market_odds_provider=PreparedLiveMarketOddsProvider(bundle),
        sporttery_provider=PreparedLiveSportteryProvider(bundle),
        training_history_provider=NoAvailableLiveTrainingHistoryProvider(),
        repository=analysis_repository,
        settings=AppSettings(runtime={"environment": "live"}),
        elo_config=elo_config,
    )
    request = RunModelAnalysisRequest(
        as_of_at_utc=DECISION,
        kickoff_from_utc=preparation.kickoff_from_utc,
        kickoff_to_utc=preparation.kickoff_to_utc,
        budgets_fen=(10_000,),
        fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
        analysis_run_id="prepared-model-run",
        execution_time_utc=CREATED,
        expected_match_ids=preparation.ready_match_ids,
        competition_id="competition",
        season_id="2026/27",
        elo_config=elo_config,
        live_source_preparation_id=preparation.preparation_id,
        prepared_fixture_observations=(
            PreparedFixtureObservationRef(
                match_id="match",
                fixture_observation_id="observation-prepared-model-rescheduled",
            ),
        ),
    )

    artifacts = asyncio.run(service.run(request))
    replayed = asyncio.run(service.run(request))
    packet_source = SqlAlchemyReviewArtifactRepository(
        sessions
    ).load_packet_source_v3("prepared-model-run")

    assert replayed == artifacts
    assert artifacts.matches[0].kickoff_at_utc == rescheduled_kickoff
    assert artifacts.quant_model_evaluations[0].status is (
        QuantModelEvaluationStatus.UNAVAILABLE
    )
    assert artifacts.quant_predictions == ()
    assert packet_source.matches[0].kickoff_at_utc == rescheduled_kickoff
    with sessions() as session:
        base_match = session.get(MatchRecord, "match")
        relation = session.get(
            LiveAnalysisRunPreparationRecord,
            "prepared-model-run",
        )
        assert base_match is not None
        assert base_match.kickoff_at_utc == first_fixture.observations[0].kickoff_at_utc
        assert relation is not None
        assert relation.preparation_id == preparation.preparation_id
    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE live_analysis_run_preparations "
                    "SET preparation_id = preparation_id "
                    "WHERE analysis_run_id = 'prepared-model-run'"
                )
            )


def _market_capture() -> MarketOddsIngestionCapture:
    snapshots = tuple(
        _market_snapshot(
            bookmaker,
            odds,
            captured_at=MARKET_RECEIVED - timedelta(minutes=index + 5),
        )
        for index, (bookmaker, odds) in enumerate(
            (
                ("bookmaker-a", ("2.10", "3.20", "3.60")),
                ("bookmaker-b", ("2.20", "3.10", "3.50")),
            )
        )
    )
    source_mapping = ProviderMatchMapping(
        mapping_id=stable_id("provider-mapping", "THE_ODDS_API", "event", "event-1"),
        provider_code="THE_ODDS_API",
        external_namespace="event",
        external_match_id="event-1",
        internal_match_id="match",
        resolution_method="EXACT_TEAM_COMPETITION_KICKOFF",
        confidence=Decimal(1),
        available_at_utc=MARKET_INGESTED,
    )
    consensus_snapshot, consensus_mapping, lineage = derive_market_consensus(
        "match", snapshots
    )
    audit = ProviderRequestAudit(
        provider="THE_ODDS_API",
        endpoint="https://synthetic.provider.test/v4/sports/soccer/odds",
        requested_at_utc=MARKET_RECEIVED - timedelta(seconds=1),
        received_at_utc=MARKET_RECEIVED,
        available_at_utc=MARKET_RECEIVED,
        request_parameters={"markets": "h2h"},
        http_status=200,
        duration_ms=1000,
        outcome=ProviderRequestOutcome.SUCCESS,
    )
    return MarketOddsIngestionCapture(
        ingestion_id="market-ingestion-001",
        provider_code="THE_ODDS_API",
        requested_match_ids=("match",),
        identity_cutoff_at_utc=MARKET_RECEIVED - timedelta(minutes=1),
        request_audit=audit,
        artifact=SourceIngestionArtifact(
            artifact_id="raw-artifact-001",
            role=SourceArtifactRole.RAW_RESPONSE,
            payload_sha256="a" * 64,
            source_path="synthetic/raw-market.json",
            captured_at_utc=MARKET_RECEIVED,
            available_at_utc=MARKET_RECEIVED,
        ),
        ingested_at_utc=MARKET_INGESTED,
        source_batch=MarketOddsBatch(
            snapshots=snapshots,
            mappings=(source_mapping,),
            issues=(),
        ),
        consensus_batch=MarketOddsBatch(
            snapshots=(consensus_snapshot,),
            mappings=(consensus_mapping,),
            issues=(),
        ),
        consensus_lineages=(lineage,),
    )


def _market_snapshot(
    bookmaker: str,
    odds: tuple[str, str, str],
    *,
    captured_at: datetime,
) -> MarketOddsSnapshot:
    quotes = tuple(
        OddsQuote(selection=selection, odds=Decimal(value))
        for selection, value in zip(SelectionKey, odds, strict=True)
    )
    snapshot_key = stable_id(
        "test-market-source",
        bookmaker,
        captured_at,
    )
    snapshot = MarketOddsSnapshot(
        snapshot_id=stable_id("test-market-snapshot", snapshot_key),
        match_id="match",
        provider_code="THE_ODDS_API",
        bookmaker_code=bookmaker,
        market=MARKET,
        quotes=quotes,
        captured_at_utc=captured_at,
        available_at_utc=MARKET_RECEIVED,
        ingested_at_utc=MARKET_INGESTED,
        source_snapshot_key=snapshot_key,
        payload_hash=canonical_payload_sha256(
            {
                "home_win": Decimal(odds[0]),
                "draw": Decimal(odds[1]),
                "away_win": Decimal(odds[2]),
            }
        ),
    )
    return snapshot


def _sporttery_capture() -> SportteryIngestionCapture:
    source_artifact_id = "sporttery-source-artifact"
    document_artifact_id = "sporttery-document-artifact"
    snapshot_key = stable_id("test-sporttery-source", SPORTTERY_INGESTED)
    bonus_values = {
        SelectionKey.HOME_WIN: Decimal("2.05"),
        SelectionKey.DRAW: Decimal("3.15"),
        SelectionKey.AWAY_WIN: Decimal("3.55"),
    }
    snapshot = SportteryBonusSnapshot(
        snapshot_id=stable_id("test-sporttery-snapshot", snapshot_key),
        match_id="match",
        provider_code="SPORTTERY_MANUAL",
        sporttery_match_no="SYN001",
        market=MARKET,
        quotes=tuple(
            FixedBonusQuote(selection=selection, fixed_bonus=value)
            for selection, value in bonus_values.items()
        ),
        sale_status=SaleStatus.OPEN,
        captured_at_utc=MARKET_RECEIVED - timedelta(hours=2),
        available_at_utc=MARKET_RECEIVED - timedelta(hours=1),
        ingested_at_utc=SPORTTERY_INGESTED,
        source_snapshot_key=snapshot_key,
        payload_hash=canonical_payload_sha256(
            {
                "home_win": bonus_values[SelectionKey.HOME_WIN],
                "draw": bonus_values[SelectionKey.DRAW],
                "away_win": bonus_values[SelectionKey.AWAY_WIN],
            }
        ),
    )
    provenance = SportterySnapshotProvenance(
        schema_version="SPORTTERY_MANUAL_ARCHIVE_V2",
        snapshot_id=snapshot.snapshot_id,
        source_snapshot_key=snapshot.source_snapshot_key,
        archive_snapshot_id="archive-snapshot-001",
        provider_code="SPORTTERY_MANUAL",
        sporttery_match_no="SYN001",
        match_number_date=date(2026, 9, 3),
        review_level="SELF_REVIEWED",
        entered_by="operator-a",
        reviewed_by="operator-a",
        captured_at_utc=snapshot.captured_at_utc,
        reviewed_at_utc=snapshot.available_at_utc,
        source_reference="synthetic://sporttery",
        source_artifact_path="source.raw",
        source_artifact_sha256="b" * 64,
        manual_document_artifact_id=document_artifact_id,
        source_artifact_id=source_artifact_id,
    )
    mapping = ProviderMatchMapping(
        mapping_id=stable_id(
            "provider-mapping",
            "SPORTTERY_MANUAL",
            "sporttery_match",
            "2026-09-03:SYN001",
        ),
        provider_code="SPORTTERY_MANUAL",
        external_namespace="sporttery_match",
        external_match_id="2026-09-03:SYN001",
        internal_match_id="match",
        resolution_method="EXACT_TEAM_COMPETITION_KICKOFF",
        confidence=Decimal(1),
        available_at_utc=SPORTTERY_INGESTED,
    )
    return SportteryIngestionCapture(
        ingestion_id="sporttery-ingestion-001",
        provider_code="SPORTTERY_MANUAL",
        identity_cutoff_at_utc=MARKET_RECEIVED,
        artifacts=(
            SourceIngestionArtifact(
                artifact_id=document_artifact_id,
                role=SourceArtifactRole.MANUAL_DOCUMENT,
                payload_sha256="c" * 64,
                source_path="synthetic/manual.json",
                captured_at_utc=snapshot.captured_at_utc,
                available_at_utc=snapshot.available_at_utc,
            ),
            SourceIngestionArtifact(
                artifact_id=source_artifact_id,
                role=SourceArtifactRole.SOURCE_ARTIFACT,
                payload_sha256="b" * 64,
                source_path="synthetic/source.raw",
                captured_at_utc=snapshot.captured_at_utc,
                available_at_utc=snapshot.available_at_utc,
            ),
        ),
        ingested_at_utc=SPORTTERY_INGESTED,
        batch=SportteryBatch(snapshots=(snapshot,), mappings=(mapping,)),
        provenance=(provenance,),
    )


def _unresolved_sporttery_capture() -> SportteryIngestionCapture:
    resolved = _sporttery_capture()
    issue = SourceReconciliationIssue(
        issue_id="sporttery-unresolved-001",
        source_kind=LiveSourceKind.SPORTTERRY,
        reason=MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED,
        provider_code="SPORTTERY_MANUAL",
        external_namespace="sporttery_match",
        external_match_id="2026-09-03:SYN001",
        candidates=(),
        code="UNRESOLVED_MATCH_MAPPING",
        detail="manual Sporttery identity could not be resolved",
        provider_identity_json="{}",
    )
    return resolved.model_copy(
        update={
            "ingestion_id": "sporttery-unresolved-ingestion",
            "batch": SportteryBatch(snapshots=(), mappings=()),
            "provenance": (),
            "issues": (issue,),
        }
    )
