import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from football_system.application.backtest_v2 import (
    BacktestV2SlatePlan,
    WalkForwardBacktestV2Request,
    WalkForwardBacktestV2Service,
)
from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.model_analysis import (
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.ports.data_providers import (
    ArchivedMatchResultBatch,
    ArchivedMatchResultSource,
    EloTrainingHistoryBatch,
    EloTrainingHistoryQuery,
    EloTrainingResultSource,
    FixtureBatch,
    MarketOddsBatch,
    MatchResultBatch,
    MatchResultQuery,
    SportteryBatch,
)
from football_system.config import AppSettings
from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    HistoricalArchiveManifest,
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
    match_result_payload_sha256,
)
from football_system.domain.backtest import BacktestArchiveProvenance
from football_system.domain.betting import PortfolioConstraints, PortfolioStatus
from football_system.domain.market import (
    MarketKey,
    MarketType,
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
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
)
from football_system.domain.prediction import (
    FusionPolicyName,
    QuantModelEvaluationStatus,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloRegularTimeResult,
)
from football_system.domain.settlement import MatchResult
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import (
    CompetitionRecord,
    MatchRecord,
    TeamRecord,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.providers.mock.dataset import payload_hash

UTC = timezone.utc
DECISION = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
KICKOFF = DECISION + timedelta(hours=4)
EVALUATION = KICKOFF + timedelta(hours=4)
EXECUTION = EVALUATION + timedelta(days=1)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)
ODDS = ThreeWayMarketOdds(
    home_win=Decimal("2.20"),
    draw=Decimal("3.20"),
    away_win=Decimal("3.80"),
)
BONUS = ThreeWayFixedBonus(
    home_win=Decimal("2.10"),
    draw=Decimal("3.10"),
    away_win=Decimal("3.90"),
)


def provenance(provider_code: str) -> RuntimeProvenance:
    return RuntimeProvenance(
        environment=RuntimeEnvironment.MOCK,
        provider_code=provider_code,
        is_mock=True,
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )


def archive(
    archive_id: str,
    provider_code: str,
    kind: HistoricalArchiveDatasetKind,
    digest: str,
) -> BacktestArchiveProvenance:
    return BacktestArchiveProvenance(
        archive_id=archive_id,
        archive_schema_version="HISTORICAL_ARCHIVE_V1",
        provider_code=provider_code,
        dataset_kind=kind,
        payload_sha256=digest * 64,
    )


TRAINING_ARCHIVE = archive(
    "archive-training",
    "MOCK_TRAINING",
    HistoricalArchiveDatasetKind.MATCH_RESULTS,
    "a",
)
RESULT_ARCHIVE = archive(
    "archive-results",
    "MOCK_RESULT",
    HistoricalArchiveDatasetKind.MATCH_RESULTS,
    "b",
)


def mapping(match_id: str, provider_code: str, known_at: datetime) -> ProviderMatchMapping:
    return ProviderMatchMapping(
        mapping_id=f"mapping-{provider_code}-{match_id}",
        provider_code=provider_code,
        external_namespace=provider_code.lower(),
        external_match_id=f"external-{provider_code}-{match_id}",
        internal_match_id=match_id,
        resolution_method="TEST_EXACT",
        confidence=Decimal(1),
        available_at_utc=known_at,
    )


class FixtureProviderStub:
    runtime_provenance = provenance("MOCK_FIXTURE")

    async def fetch_fixtures(self, query) -> FixtureBatch:
        known_at = query.as_of_at_utc - timedelta(hours=1)
        matches = (
            Match(
                match_id="target-available",
                competition_id="competition-1",
                home_team_id="team-a",
                away_team_id="team-b",
                kickoff_at_utc=KICKOFF,
                available_at_utc=known_at,
            ),
            Match(
                match_id="target-unavailable",
                competition_id="competition-1",
                home_team_id="team-c",
                away_team_id="team-d",
                kickoff_at_utc=KICKOFF + timedelta(minutes=30),
                available_at_utc=known_at,
            ),
        )
        return FixtureBatch(
            competitions=(
                Competition(
                    competition_id="competition-1",
                    canonical_key="competition-1",
                    name="Competition 1",
                    country_code="GB",
                ),
            ),
            teams=tuple(
                Team(team_id=team_id, canonical_key=team_id, name=team_id)
                for team_id in ("team-a", "team-b", "team-c", "team-d")
            ),
            matches=matches,
            mappings=tuple(
                mapping(item.match_id, "MOCK_FIXTURE", known_at) for item in matches
            ),
        )


class OddsProviderStub:
    runtime_provenance = provenance("MOCK_ODDS")

    async def fetch_market_odds(self, query) -> MarketOddsBatch:
        known_at = query.as_of_at_utc - timedelta(minutes=30)
        snapshots = tuple(
            MarketOddsSnapshot(
                snapshot_id=f"odds-{match_id}",
                match_id=match_id,
                provider_code="MOCK_ODDS",
                bookmaker_code="BOOK",
                market=MARKET,
                quotes=tuple(
                    OddsQuote(selection=selection, odds=value)
                    for selection, value in ODDS.items()
                ),
                captured_at_utc=known_at,
                available_at_utc=known_at,
                ingested_at_utc=known_at,
                source_snapshot_key=f"source-odds-{match_id}",
                payload_hash=payload_hash(ODDS.model_dump(mode="json")),
            )
            for match_id in query.match_ids
        )
        return MarketOddsBatch(
            snapshots=snapshots,
            mappings=tuple(
                mapping(item.match_id, "MOCK_ODDS", known_at) for item in snapshots
            ),
        )


class SportteryProviderStub:
    runtime_provenance = provenance("MOCK_SPORTTERY")

    async def fetch_fixed_bonus(self, query) -> SportteryBatch:
        known_at = query.as_of_at_utc - timedelta(minutes=20)
        snapshots = tuple(
            SportteryBonusSnapshot(
                snapshot_id=f"bonus-{match_id}",
                match_id=match_id,
                provider_code="MOCK_SPORTTERY",
                sporttery_match_no=f"number-{match_id}",
                market=MARKET,
                quotes=tuple(
                    FixedBonusQuote(selection=selection, fixed_bonus=value)
                    for selection, value in BONUS.items()
                ),
                sale_status=SaleStatus.OPEN,
                captured_at_utc=known_at,
                available_at_utc=known_at,
                ingested_at_utc=known_at,
                source_snapshot_key=f"source-bonus-{match_id}",
                payload_hash=payload_hash(BONUS.model_dump(mode="json")),
            )
            for match_id in query.match_ids
        )
        return SportteryBatch(
            snapshots=snapshots,
            mappings=tuple(
                mapping(item.match_id, "MOCK_SPORTTERY", known_at)
                for item in snapshots
            ),
        )


def elo_result(
    result_id: str,
    match_id: str,
    home_team_id: str,
    away_team_id: str,
    home_goals: int,
    away_goals: int,
) -> EloRegularTimeResult:
    kickoff = DECISION - timedelta(days=10 if match_id != "target-available" else 20)
    available = kickoff + timedelta(hours=3)
    return EloRegularTimeResult(
        match_result_id=result_id,
        match_id=match_id,
        season_id="season-1",
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        kickoff_at_utc=kickoff,
        available_at_utc=available,
        ingested_at_utc=available,
        home_goals=home_goals,
        away_goals=away_goals,
        payload_hash=match_result_payload_sha256(home_goals, away_goals),
    )


class TrainingProviderStub:
    runtime_provenance = provenance("MOCK_TRAINING")

    def __init__(self) -> None:
        self.queries: list[EloTrainingHistoryQuery] = []
        self.results = (
            elo_result("history-a", "history-a", "team-a", "team-x", 2, 0),
            elo_result("history-b", "history-b", "team-b", "team-y", 1, 0),
            elo_result(
                "leaked-target-result",
                "target-available",
                "team-a",
                "team-b",
                9,
                0,
            ),
        )

    async def fetch_elo_training_history(
        self,
        query: EloTrainingHistoryQuery,
    ) -> EloTrainingHistoryBatch:
        self.queries.append(query)
        return EloTrainingHistoryBatch(
            competition_id=query.competition_id,
            target_season_id=query.target_season_id,
            as_of_at_utc=query.as_of_at_utc,
            sources=tuple(
                EloTrainingResultSource(result=item, archive=TRAINING_ARCHIVE)
                for item in self.results
            ),
        )


class RepositorySpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.saved = []

    def save_analysis(self, artifacts, rules) -> None:
        del rules
        self.saved.append(artifacts)
        self.events.append(f"decision:{artifacts.analysis_run.analysis_run_id}")


class PersistingRepositorySpy(RepositorySpy):
    def __init__(self, repository, events: list[str]) -> None:
        super().__init__(events)
        self.repository = repository

    def save_analysis(self, artifacts, rules) -> None:
        self.repository.save_analysis(artifacts, rules)
        super().save_analysis(artifacts, rules)


class ResultProviderSpy:
    runtime_provenance = provenance("MOCK_RESULT")

    def __init__(self, repository: RepositorySpy, events: list[str]) -> None:
        self.repository = repository
        self.events = events

    async def fetch_archived_match_results(
        self,
        query: MatchResultQuery,
    ) -> ArchivedMatchResultBatch:
        assert self.repository.saved
        assert self.events[-1].startswith("decision:")
        self.events.append("evaluation-results")
        observed = KICKOFF + timedelta(hours=2)
        scores = {
            "target-available": (2, 1),
            "target-unavailable": (0, 0),
        }
        results = tuple(
            MatchResult(
                match_result_id=f"result-{match_id}",
                match_id=match_id,
                provider_code="MOCK_RESULT",
                home_goals=scores[match_id][0],
                away_goals=scores[match_id][1],
                observed_at_utc=observed,
                available_at_utc=observed + timedelta(minutes=1),
                ingested_at_utc=observed + timedelta(minutes=1),
                source_result_key=f"source-result-{match_id}",
                payload_hash=match_result_payload_sha256(*scores[match_id]),
            )
            for match_id in query.match_ids
        )
        return ArchivedMatchResultBatch(
            as_of_at_utc=query.as_of_at_utc,
            sources=tuple(
                ArchivedMatchResultSource(result=item, archive=RESULT_ARCHIVE)
                for item in results
            ),
            mappings=tuple(
                mapping(
                    item.match_id,
                    "MOCK_RESULT",
                    item.available_at_utc - timedelta(days=1),
                )
                for item in results
            ),
        )


def model_service(events: list[str]) -> tuple[RunModelAnalysisService, TrainingProviderStub, RepositorySpy]:
    training = TrainingProviderStub()
    repository = RepositorySpy(events)
    config = EloBaselineConfig(minimum_prior_matches=1)
    return (
        RunModelAnalysisService(
            FixtureProviderStub(),
            OddsProviderStub(),
            SportteryProviderStub(),
            training,
            repository,
            AppSettings(),
            config,
        ),
        training,
        repository,
    )


def test_model_analysis_keeps_unavailable_explicit_and_excludes_targets() -> None:
    events: list[str] = []
    service, training, repository = model_service(events)
    artifacts = asyncio.run(
        service.run(
            RunModelAnalysisRequest(
                as_of_at_utc=DECISION,
                kickoff_from_utc=KICKOFF,
                kickoff_to_utc=KICKOFF + timedelta(hours=1),
                budgets_fen=(10_000,),
                fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
                analysis_run_id="model-analysis-1",
                execution_time_utc=EXECUTION,
                allow_partial_inputs=True,
                expected_match_ids=("target-available", "target-unavailable"),
                competition_id="competition-1",
                season_id="season-1",
                elo_config=service.baseline.config,
            )
        )
    )

    assert training.queries[0].exclude_match_ids == (
        "target-available",
        "target-unavailable",
    )
    assert repository.saved == [artifacts]
    assert artifacts.manual_quant_inputs == ()
    assert tuple(
        item.match_id for item in artifacts.quant_model_states[0].training_facts
    ) == (
        "history-a",
        "history-b",
    )
    assert tuple(item.status for item in artifacts.quant_model_evaluations) == (
        QuantModelEvaluationStatus.AVAILABLE,
        QuantModelEvaluationStatus.UNAVAILABLE,
    )
    assert tuple(item.match_id for item in artifacts.quant_predictions) == (
        "target-available",
    )
    assert tuple(item.match_id for item in artifacts.final_predictions) == (
        "target-available",
    )
    assert artifacts.portfolios[0].status is PortfolioStatus.NO_BET


def test_backtest_v2_freezes_decisions_before_results_and_counts_unavailable() -> None:
    events: list[str] = []
    analysis, _, repository = model_service(events)
    service = WalkForwardBacktestV2Service(
        analysis,
        ResultProviderSpy(repository, events),
    )
    constraints = PortfolioConstraints(
        preferred_max_tickets=2,
        absolute_max_tickets=3,
    )
    result = asyncio.run(
        service.run(
            WalkForwardBacktestV2Request(
                backtest_run_id="backtest-v2-1",
                data_mode=HistoricalDataMode.LIVE_STRICT,
                fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
                slates=(
                    BacktestV2SlatePlan(
                        competition_id="competition-1",
                        season_id="season-1",
                        decision_as_of_at_utc=DECISION,
                        evaluation_as_of_at_utc=EVALUATION,
                        kickoff_from_utc=KICKOFF,
                        kickoff_to_utc=KICKOFF + timedelta(hours=1),
                        match_ids=("target-available", "target-unavailable"),
                    ),
                ),
                budget_fen=10_000,
                quant_weight=Decimal("0.42"),
                min_selection_ev=Decimal("0.02"),
                min_ticket_roi=Decimal("0.02"),
                constraints=constraints,
                elo_config=analysis.baseline.config,
                archive_provenance=(TRAINING_ARCHIVE, RESULT_ARCHIVE),
                execution_time_utc=EXECUTION,
            )
        )
    )

    assert events[0].startswith("decision:")
    assert events[1] == "evaluation-results"
    assert result.metrics.planned_target_count == 2
    assert result.analysis_artifacts[0].portfolios[0].constraints == constraints
    assert '"quant_weight":"0.42"' in (
        result.analysis_artifacts[0].analysis_run.config_json
    )
    assert result.metrics.decision_target_count == 2
    assert result.metrics.result_target_count == 2
    assert result.metrics.quant_available_count == 1
    assert result.metrics.quant_unavailable_count == 1
    assert result.metrics.quant_availability == Decimal("0.5")
    assert result.metrics.p_market.sample_count == 2
    assert result.metrics.p_quant.sample_count == 1
    assert result.metrics.p_final.sample_count == 1
    unavailable = result.backtest_slices[0].match_snapshots[1]
    assert unavailable.quant_status is QuantModelEvaluationStatus.UNAVAILABLE
    assert unavailable.p_quant is None
    assert unavailable.p_final is None
    assert result.slate_results[0].slate_snapshot.is_no_bet is True
    assert result.slate_results[0].slate_snapshot.ticket_count == 0


def test_backtest_v2_persistence_round_trip_retry_and_tamper_rejection() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    historical_repository = SqlAlchemyHistoricalRepository(sessions)
    training = TrainingProviderStub()
    _seed_training_history(sessions, historical_repository, training)
    historical_repository.append_historical_archive_imports(
        (
            _archive_manifest(TRAINING_ARCHIVE),
            _archive_manifest(RESULT_ARCHIVE),
        ),
        EXECUTION + timedelta(minutes=1),
    )

    events: list[str] = []
    repository = PersistingRepositorySpy(
        SqlAlchemyAnalysisRepository(sessions),
        events,
    )
    config = EloBaselineConfig(minimum_prior_matches=0)
    analysis = RunModelAnalysisService(
        FixtureProviderStub(),
        OddsProviderStub(),
        SportteryProviderStub(),
        training,
        repository,
        AppSettings(),
        config,
    )
    service = WalkForwardBacktestV2Service(
        analysis,
        ResultProviderSpy(repository, events),
    )
    request = WalkForwardBacktestV2Request(
        backtest_run_id="backtest-v2-persistence",
        data_mode=HistoricalDataMode.LIVE_STRICT,
        fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
        slates=(
            BacktestV2SlatePlan(
                competition_id="competition-1",
                season_id="season-1",
                decision_as_of_at_utc=DECISION,
                evaluation_as_of_at_utc=EVALUATION,
                kickoff_from_utc=KICKOFF,
                kickoff_to_utc=KICKOFF + timedelta(hours=1),
                match_ids=("target-available", "target-unavailable"),
            ),
        ),
        budget_fen=10_000,
        quant_weight=Decimal("0.42"),
        min_selection_ev=Decimal(0),
        min_ticket_roi=Decimal(0),
        constraints=PortfolioConstraints(),
        elo_config=config,
        archive_provenance=(TRAINING_ARCHIVE, RESULT_ARCHIVE),
        execution_time_utc=EXECUTION,
    )
    result = asyncio.run(service.run(request))

    assert (
        historical_repository.save_walk_forward_backtest_v2_result(result)
        == result.backtest_run
    )
    assert result.metrics.settled_ticket_count > 0
    counts = historical_repository.backtest_v2_table_counts()
    assert counts == {
        "backtest_v2_runs": 1,
        "backtest_v2_run_archives": 2,
        "backtest_v2_slices": 1,
        "backtest_v2_training_sources": 2,
        "backtest_v2_evaluation_refs": 2,
        "backtest_v2_result_sources": 2,
        "backtest_v2_slice_ticket_settlements": result.metrics.settled_ticket_count,
        "backtest_v2_metric_snapshots": 1,
    }
    assert (
        historical_repository.find_backtest_v2_run_value(request.backtest_run_id)
        == result.backtest_run
    )
    assert historical_repository.backtest_v2_slice_values(
        request.backtest_run_id
    ) == result.backtest_slices
    assert (
        historical_repository.find_backtest_v2_metrics_value(
            request.backtest_run_id
        )
        == result.metrics
    )

    historical_repository.save_walk_forward_backtest_v2_result(result)
    assert historical_repository.backtest_v2_table_counts() == counts

    with pytest.raises(IntegrityError, match="immutable|append-only"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE backtest_v2_slices SET slate_snapshot_hash = :hash "
                    "WHERE backtest_run_id = :run_id"
                ),
                {"hash": "0" * 64, "run_id": request.backtest_run_id},
            )

    with engine.begin() as connection:
        connection.execute(
            text("DROP TRIGGER trg_backtest_v2_slices_append_only_update")
        )
        connection.execute(text("DROP TRIGGER trg_backtest_v2_slices_sealed_update"))
        connection.execute(
            text(
                "UPDATE backtest_v2_slices "
                "SET slate_snapshot_json = slate_snapshot_json || ' ' "
                "WHERE backtest_run_id = :run_id"
            ),
            {"run_id": request.backtest_run_id},
        )
    with pytest.raises(ValueError, match="canonical JSON"):
        historical_repository.find_backtest_v2_run_value(request.backtest_run_id)
    engine.dispose()


def _seed_training_history(sessions, repository, training: TrainingProviderStub) -> None:
    source_results = training.results[:2]
    with sessions.begin() as session:
        session.add(
            CompetitionRecord(
                competition_id="competition-1",
                canonical_key="competition-1",
                name="Competition 1",
                country_code="GB",
            )
        )
        session.add_all(
            TeamRecord(
                team_id=team_id,
                canonical_key=team_id,
                name=team_id,
                team_type="CLUB",
            )
            for team_id in ("team-a", "team-b", "team-x", "team-y")
        )
        session.flush()
        session.add_all(
            MatchRecord(
                internal_match_id=result.match_id,
                competition_id="competition-1",
                home_team_id=result.home_team_id,
                away_team_id=result.away_team_id,
                kickoff_at_utc=result.kickoff_at_utc,
                status=MatchStatus.FINISHED.value,
                available_at_utc=result.kickoff_at_utc,
                created_at_utc=result.kickoff_at_utc,
            )
            for result in source_results
        )
    match_results = tuple(
        MatchResult(
            match_result_id=result.match_result_id,
            match_id=result.match_id,
            provider_code="MOCK_TRAINING",
            home_goals=result.home_goals,
            away_goals=result.away_goals,
            observed_at_utc=result.kickoff_at_utc + timedelta(hours=2),
            available_at_utc=result.available_at_utc,
            ingested_at_utc=result.ingested_at_utc,
            source_result_key=f"source-{result.match_result_id}",
            payload_hash=result.payload_hash,
        )
        for result in source_results
    )
    repository.append_match_result_batch(
        MatchResultBatch(
            as_of_at_utc=DECISION,
            results=match_results,
            mappings=tuple(
                mapping(
                    result.match_id,
                    "MOCK_TRAINING",
                    result.kickoff_at_utc - timedelta(hours=1),
                )
                for result in source_results
            ),
        )
    )


def _archive_manifest(
    value: BacktestArchiveProvenance,
) -> HistoricalArchiveManifest:
    return HistoricalArchiveManifest(
        archive_schema_version=HISTORICAL_ARCHIVE_SCHEMA_VERSION,
        archive_id=value.archive_id,
        provider_code=value.provider_code,
        dataset_kind=value.dataset_kind,
        created_at_utc=EXECUTION,
        source_reference=f"test://{value.archive_id}",
        source_description="BACKTEST_V2 persistence test archive",
        license_note="TEST_ONLY",
        data_mode=HistoricalDataMode.LIVE_STRICT,
        payload_sha256=value.payload_sha256,
        record_count=1,
    )
