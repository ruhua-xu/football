import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.application.backtest import (
    BacktestSlatePlan,
    WalkForwardBacktestRequest,
    WalkForwardBacktestService,
    compare_backtests,
    validate_walk_forward_backtest_result,
)
from football_system.application.environment import (
    RuntimeDataModeError,
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.models import AnalysisArtifacts
from football_system.application.ports.data_providers import (
    FixtureBatch,
    FixtureQuery,
    ManualQuantBatch,
    MarketOddsBatch,
    MatchResultBatch,
    MatchResultQuery,
    SnapshotQuery,
    SportteryBatch,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.config import AppSettings, PortfolioSettings
from football_system.domain.analysis import AnalysisRunStatus
from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestDataMode,
    sha256_text,
)
from football_system.domain.betting import PortfolioConstraints, PortfolioStatus
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.match import (
    Competition,
    FixedBonusQuote,
    MarketOddsSnapshot,
    Match,
    OddsQuote,
    ProviderMatchMapping,
    SaleStatus,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.prediction import FusionPolicyName, ManualQuantInput
from football_system.domain.settlement import (
    MatchResult,
    MatchSettlementIssue,
    SettlementResultReason,
    SettlementStatus,
    UnsupportedSettlementReason,
)
from football_system.infrastructure.providers.mock.dataset import payload_hash

UTC = timezone.utc
START = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
THREE_WAY = MarketKey(market_type=MarketType.THREE_WAY)
MARKET_ODDS = ThreeWayMarketOdds(
    home_win=Decimal("2.20"),
    draw=Decimal("3.20"),
    away_win=Decimal("3.80"),
)
FUTURE_MARKET_ODDS = ThreeWayMarketOdds(
    home_win=Decimal("1.50"),
    draw=Decimal("4.20"),
    away_win=Decimal("6.00"),
)
BONUS = ThreeWayFixedBonus(
    home_win=Decimal("2.00"),
    draw=Decimal("3.20"),
    away_win=Decimal("5.00"),
)


def mock_runtime_provenance(provider_code: str) -> RuntimeProvenance:
    return RuntimeProvenance(
        environment=RuntimeEnvironment.MOCK,
        provider_code=provider_code,
        is_mock=True,
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )


QUANT = ThreeWayProbability(
    home_win=Decimal("0.70"),
    draw=Decimal("0.20"),
    away_win=Decimal("0.10"),
)


def slate_plan(
    number: int,
    *,
    match_ids: tuple[str, ...] = (),
) -> BacktestSlatePlan:
    decision = START + timedelta(days=number - 1)
    return BacktestSlatePlan(
        decision_as_of_at_utc=decision,
        kickoff_from_utc=decision + timedelta(hours=4),
        kickoff_to_utc=decision + timedelta(hours=10),
        evaluation_as_of_at_utc=decision + timedelta(hours=16),
        match_ids=match_ids,
    )


class InMemoryArchive:
    def __init__(
        self,
        plans: tuple[BacktestSlatePlan, ...],
        matches_per_slate: int,
    ) -> None:
        self.competition = Competition(
            competition_id="competition-1",
            canonical_key="test-league",
            name="Test League",
            country_code="GB",
        )
        self.teams: dict[str, Team] = {}
        self.matches: list[Match] = []
        self.market_odds: dict[str, tuple[MarketOddsSnapshot, ...]] = {}
        self.bonuses: dict[str, SportteryBonusSnapshot] = {}
        self.quant_inputs: dict[str, ManualQuantInput] = {}
        self.results: dict[str, MatchResult] = {}
        self.missing_fixtures: set[str] = set()
        self.missing_fixture_mappings: set[str] = set()
        self.missing_odds: set[str] = set()
        self.missing_bonuses: set[str] = set()
        self.missing_quant_inputs: set[str] = set()
        self.leaked_future_odds: set[str] = set()
        self.plan_match_ids: list[tuple[str, ...]] = []
        for slate_no, plan in enumerate(plans, start=1):
            match_ids = []
            for match_no in range(1, matches_per_slate + 1):
                match_id = f"s{slate_no}-match-{match_no}"
                match_ids.append(match_id)
                self._add_match(match_id, plan, match_no)
            self.plan_match_ids.append(tuple(match_ids))

    def _add_match(
        self,
        match_id: str,
        plan: BacktestSlatePlan,
        match_no: int,
    ) -> None:
        home_id = f"{match_id}-home"
        away_id = f"{match_id}-away"
        self.teams[home_id] = Team(
            team_id=home_id,
            canonical_key=home_id,
            name=f"{match_id} Home",
        )
        self.teams[away_id] = Team(
            team_id=away_id,
            canonical_key=away_id,
            name=f"{match_id} Away",
        )
        known_at = plan.decision_as_of_at_utc - timedelta(hours=2)
        kickoff = plan.kickoff_from_utc + timedelta(minutes=15 * match_no)
        self.matches.append(
            Match(
                match_id=match_id,
                competition_id=self.competition.competition_id,
                home_team_id=home_id,
                away_team_id=away_id,
                kickoff_at_utc=kickoff,
                available_at_utc=known_at,
            )
        )
        self.market_odds[match_id] = (
            odds_snapshot(
                match_id,
                "past",
                MARKET_ODDS,
                known_at,
            ),
            odds_snapshot(
                match_id,
                "future",
                FUTURE_MARKET_ODDS,
                plan.decision_as_of_at_utc + timedelta(hours=1),
            ),
        )
        self.bonuses[match_id] = bonus_snapshot(match_id, BONUS, known_at)
        self.quant_inputs[match_id] = ManualQuantInput(
            input_id=f"quant-{match_id}",
            match_id=match_id,
            market=THREE_WAY,
            probabilities=QUANT,
            available_at_utc=known_at,
            payload_hash=payload_hash(QUANT.model_dump(mode="json")),
        )
        observed = kickoff + timedelta(hours=2)
        self.results[match_id] = MatchResult(
            match_result_id=f"result-{match_id}",
            match_id=match_id,
            provider_code="RESULT_TEST",
            home_goals=0,
            away_goals=1,
            observed_at_utc=observed,
            available_at_utc=observed + timedelta(minutes=1),
            ingested_at_utc=observed + timedelta(minutes=2),
            source_result_key=f"result-source-{match_id}",
            payload_hash=f"result-payload-{match_id}",
        )


def odds_snapshot(
    match_id: str,
    version: str,
    values: ThreeWayMarketOdds,
    available_at: datetime,
) -> MarketOddsSnapshot:
    return MarketOddsSnapshot(
        snapshot_id=f"odds-{match_id}-{version}",
        match_id=match_id,
        provider_code="ODDS_TEST",
        bookmaker_code="BOOK_TEST",
        market=THREE_WAY,
        quotes=tuple(
            OddsQuote(selection=selection, odds=value)
            for selection, value in values.items()
        ),
        captured_at_utc=available_at,
        available_at_utc=available_at,
        ingested_at_utc=available_at,
        source_snapshot_key=f"odds-source-{match_id}-{version}",
        payload_hash=payload_hash(values.model_dump(mode="json")),
    )


def bonus_snapshot(
    match_id: str,
    values: ThreeWayFixedBonus,
    available_at: datetime,
) -> SportteryBonusSnapshot:
    return SportteryBonusSnapshot(
        snapshot_id=f"bonus-{match_id}",
        match_id=match_id,
        provider_code="SPORTTERY_TEST",
        sporttery_match_no=f"sporttery-{match_id}",
        market=THREE_WAY,
        quotes=tuple(
            FixedBonusQuote(selection=selection, fixed_bonus=value)
            for selection, value in values.items()
        ),
        sale_status=SaleStatus.OPEN,
        captured_at_utc=available_at,
        available_at_utc=available_at,
        ingested_at_utc=available_at,
        source_snapshot_key=f"bonus-source-{match_id}",
        payload_hash=payload_hash(values.model_dump(mode="json")),
    )


def mapping(
    match_id: str,
    provider_code: str,
    available_at: datetime,
) -> ProviderMatchMapping:
    return ProviderMatchMapping(
        mapping_id=f"mapping-{provider_code}-{match_id}",
        provider_code=provider_code,
        external_namespace=provider_code.lower(),
        external_match_id=f"external-{provider_code}-{match_id}",
        internal_match_id=match_id,
        resolution_method="TEST_EXACT",
        confidence=Decimal(1),
        available_at_utc=available_at,
    )


class FixtureStub:
    runtime_provenance = mock_runtime_provenance("FIXTURE_TEST")

    def __init__(self, archive: InMemoryArchive) -> None:
        self.archive = archive

    async def fetch_fixtures(self, query: FixtureQuery) -> FixtureBatch:
        matches = tuple(
            match
            for match in self.archive.matches
            if match.available_at_utc <= query.as_of_at_utc
            and query.kickoff_from_utc <= match.kickoff_at_utc <= query.kickoff_to_utc
            and match.match_id not in self.archive.missing_fixtures
        )
        team_ids = {
            team_id
            for match in matches
            for team_id in (match.home_team_id, match.away_team_id)
        }
        return FixtureBatch(
            competitions=(self.archive.competition,),
            teams=tuple(self.archive.teams[item] for item in sorted(team_ids)),
            matches=matches,
            mappings=tuple(
                mapping(match.match_id, "FIXTURE_TEST", match.available_at_utc)
                for match in matches
                if match.match_id not in self.archive.missing_fixture_mappings
            ),
        )


class OddsStub:
    runtime_provenance = mock_runtime_provenance("ODDS_TEST")

    def __init__(self, archive: InMemoryArchive) -> None:
        self.archive = archive
        self.queries: list[SnapshotQuery] = []

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        self.queries.append(query)
        snapshots = []
        for match_id in query.match_ids:
            if match_id in self.archive.missing_odds:
                continue
            visible = tuple(
                item
                for item in self.archive.market_odds[match_id]
                if item.captured_at_utc <= query.as_of_at_utc
                and item.available_at_utc <= query.as_of_at_utc
                and item.ingested_at_utc <= query.as_of_at_utc
            )
            candidates = (
                self.archive.market_odds[match_id]
                if match_id in self.archive.leaked_future_odds
                else visible
            )
            if candidates:
                snapshots.append(
                    max(candidates, key=lambda item: item.available_at_utc)
                )
        return MarketOddsBatch(
            snapshots=tuple(snapshots),
            mappings=tuple(
                mapping(
                    item.match_id,
                    item.provider_code,
                    item.available_at_utc,
                )
                for item in snapshots
            ),
        )


class SportteryStub:
    runtime_provenance = mock_runtime_provenance("SPORTTERY_TEST")

    def __init__(self, archive: InMemoryArchive) -> None:
        self.archive = archive

    async def fetch_fixed_bonus(self, query: SnapshotQuery) -> SportteryBatch:
        snapshots = tuple(
            self.archive.bonuses[match_id]
            for match_id in query.match_ids
            if match_id not in self.archive.missing_bonuses
            if self.archive.bonuses[match_id].available_at_utc <= query.as_of_at_utc
        )
        return SportteryBatch(
            snapshots=snapshots,
            mappings=tuple(
                mapping(
                    item.match_id,
                    item.provider_code,
                    item.available_at_utc,
                )
                for item in snapshots
            ),
        )


class QuantStub:
    runtime_provenance = mock_runtime_provenance("QUANT_TEST")

    def __init__(self, archive: InMemoryArchive) -> None:
        self.archive = archive

    async def fetch_manual_quant(self, query: SnapshotQuery) -> ManualQuantBatch:
        return ManualQuantBatch(
            provider_code="QUANT_TEST",
            inputs=tuple(
                self.archive.quant_inputs[match_id]
                for match_id in query.match_ids
                if match_id not in self.archive.missing_quant_inputs
                if self.archive.quant_inputs[match_id].available_at_utc
                <= query.as_of_at_utc
            ),
        )


class AnalysisRepositorySpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.saved: list[AnalysisArtifacts] = []

    def save_analysis(self, artifacts: AnalysisArtifacts, rules: object) -> None:
        del rules
        assert artifacts.analysis_run.status == AnalysisRunStatus.COMPLETED
        self.saved.append(artifacts)
        self.events.append(f"analysis:{artifacts.analysis_run.analysis_run_id}")


class ResultProviderSpy:
    runtime_provenance = mock_runtime_provenance("RESULT_TEST")

    def __init__(
        self,
        archive: InMemoryArchive,
        repository: AnalysisRepositorySpy,
        events: list[str],
        *,
        first_ticket_only: bool = False,
    ) -> None:
        self.archive = archive
        self.repository = repository
        self.events = events
        self.first_ticket_only = first_ticket_only
        self.issues: dict[str, MatchSettlementIssue] = {}
        self.queries: list[MatchResultQuery] = []

    async def fetch_match_results(
        self,
        query: MatchResultQuery,
    ) -> MatchResultBatch:
        assert self.repository.saved
        assert self.events[-1].startswith("analysis:")
        frozen = self.repository.saved[-1]
        assert frozen.analysis_run.status == AnalysisRunStatus.COMPLETED
        assert set(query.match_ids) == {match.match_id for match in frozen.matches}
        self.queries.append(query)
        self.events.append(f"results:{frozen.analysis_run.analysis_run_id}")
        allowed = set(query.match_ids)
        if self.first_ticket_only:
            tickets = frozen.portfolios[0].tickets
            assert len(tickets) == 2
            allowed = {leg.match_id for leg in tickets[0].candidate.legs}
        results = tuple(
            result
            for match_id in query.match_ids
            if match_id in allowed and match_id not in self.issues
            for result in (self.archive.results[match_id],)
            if result.available_at_utc <= query.as_of_at_utc
            and result.ingested_at_utc <= query.as_of_at_utc
        )
        issues = tuple(
            self.issues[match_id]
            for match_id in query.match_ids
            if match_id in allowed and match_id in self.issues
        )
        mapped_match_ids = tuple(
            dict.fromkeys(
                (
                    *(result.match_id for result in results),
                    *(issue.match_id for issue in issues),
                )
            )
        )
        return MatchResultBatch(
            as_of_at_utc=query.as_of_at_utc,
            results=results,
            mappings=tuple(
                mapping(
                    match_id,
                    self.archive.results[match_id].provider_code,
                    self.archive.results[match_id].available_at_utc - timedelta(days=1),
                )
                for match_id in mapped_match_ids
            ),
            issues=issues,
        )


def constraints_for(settings: AppSettings) -> PortfolioConstraints:
    return PortfolioConstraints.model_validate(
        settings.portfolio.model_dump(mode="python")
    )


def build_analysis_service(
    archive: InMemoryArchive,
    repository: AnalysisRepositorySpy,
    settings: AppSettings,
) -> tuple[RunAnalysisService, OddsStub]:
    odds_provider = OddsStub(archive)
    return (
        RunAnalysisService(
            fixture_provider=FixtureStub(archive),
            market_odds_provider=odds_provider,
            sporttery_provider=SportteryStub(archive),
            manual_quant_provider=QuantStub(archive),
            repository=repository,
            settings=settings,
        ),
        odds_provider,
    )


def build_service(
    plans: tuple[BacktestSlatePlan, ...],
    *,
    matches_per_slate: int = 2,
    settings: AppSettings | None = None,
    first_ticket_only: bool = False,
) -> tuple[
    WalkForwardBacktestService,
    InMemoryArchive,
    AnalysisRepositorySpy,
    ResultProviderSpy,
    OddsStub,
    AppSettings,
]:
    settings = settings or AppSettings()
    archive = InMemoryArchive(plans, matches_per_slate)
    events: list[str] = []
    repository = AnalysisRepositorySpy(events)
    analysis, odds_provider = build_analysis_service(archive, repository, settings)
    result_provider = ResultProviderSpy(
        archive,
        repository,
        events,
        first_ticket_only=first_ticket_only,
    )
    return (
        WalkForwardBacktestService(analysis, result_provider),
        archive,
        repository,
        result_provider,
        odds_provider,
        settings,
    )


def request_for(
    run_id: str,
    plans: tuple[BacktestSlatePlan, ...],
    settings: AppSettings,
    *,
    policy: FusionPolicyName = FusionPolicyName.QUANT_ONLY_V1,
    budget_fen: int = 1_000,
    min_selection_ev: Decimal = Decimal("0.02"),
    min_ticket_roi: Decimal = Decimal("0.02"),
    quant_weight: Decimal | None = None,
    archive_provenance: tuple[BacktestArchiveProvenance, ...] = (),
    execution_time_utc: datetime | None = None,
) -> WalkForwardBacktestRequest:
    return WalkForwardBacktestRequest(
        backtest_run_id=run_id,
        data_mode=BacktestDataMode.LIVE_STRICT,
        fusion_policy=policy,
        slates=plans,
        budget_fen=budget_fen,
        quant_weight=(
            settings.analysis.quant_weight if quant_weight is None else quant_weight
        ),
        min_selection_ev=min_selection_ev,
        min_ticket_roi=min_ticket_roi,
        constraints=constraints_for(settings),
        archive_provenance=archive_provenance,
        execution_time_utc=execution_time_utc,
    )


def analysis_request_for(
    run_id: str,
    plan: BacktestSlatePlan,
    *,
    allow_partial_inputs: bool,
) -> RunAnalysisRequest:
    return RunAnalysisRequest(
        as_of_at_utc=plan.decision_as_of_at_utc,
        kickoff_from_utc=plan.kickoff_from_utc,
        kickoff_to_utc=plan.kickoff_to_utc,
        budgets_fen=(1_000,),
        fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
        analysis_run_id=run_id,
        execution_time_utc=plan.decision_as_of_at_utc,
        allow_partial_inputs=allow_partial_inputs,
        expected_match_ids=plan.match_ids or None,
    )


def archive_ref(
    archive_id: str = "archive-1",
    payload_sha256: str = "a" * 64,
) -> BacktestArchiveProvenance:
    return BacktestArchiveProvenance(
        archive_id=archive_id,
        archive_schema_version="HISTORICAL_ARCHIVE_V1",
        provider_code="ARCHIVE_TEST",
        dataset_kind=HistoricalArchiveDatasetKind.MATCH_RESULTS,
        payload_sha256=payload_sha256,
    )


def test_chronological_slates_freeze_analysis_before_each_result_read() -> None:
    plans = (slate_plan(1), slate_plan(2))
    service, _, repository, result_provider, odds_provider, settings = build_service(
        plans
    )

    result = asyncio.run(
        service.run(request_for("backtest-chronological", plans, settings))
    )

    assert [item.plan for item in result.slate_results] == list(plans)
    assert [event.split(":", 1)[0] for event in repository.events] == [
        "analysis",
        "results",
        "analysis",
        "results",
    ]
    assert len(result_provider.queries) == 2
    for plan, artifacts, query in zip(
        plans,
        result.analysis_artifacts,
        result_provider.queries,
        strict=True,
    ):
        assert artifacts.analysis_run.started_at_utc == (
            result.backtest_run.created_at_utc
        )
        assert artifacts.analysis_run.completed_at_utc == (
            result.backtest_run.created_at_utc
        )
        assert query.as_of_at_utc == plan.evaluation_as_of_at_utc
    assert all(
        snapshot.snapshot_id.endswith("-past")
        for artifacts in result.analysis_artifacts
        for snapshot in artifacts.market_odds_snapshots
    )
    assert [query.as_of_at_utc for query in odds_provider.queries] == [
        plan.decision_as_of_at_utc for plan in plans
    ]


@pytest.mark.parametrize(
    "missing_attribute",
    (
        "missing_fixture_mappings",
        "missing_odds",
        "missing_bonuses",
        "missing_quant_inputs",
    ),
)
def test_partial_analysis_omits_each_incomplete_mvp_input(
    missing_attribute: str,
) -> None:
    expected_match_ids = ("s1-match-1", "s1-match-2")
    plans = (slate_plan(1, match_ids=expected_match_ids),)
    service, archive, repository, result_provider, _, settings = build_service(plans)
    getattr(archive, missing_attribute).add(expected_match_ids[0])

    result = asyncio.run(
        service.run(
            request_for(f"backtest-partial-{missing_attribute}", plans, settings)
        )
    )
    slate = result.slate_results[0]
    analyzed_match_ids = tuple(
        match.match_id for match in slate.analysis_artifacts.matches
    )

    assert analyzed_match_ids == (expected_match_ids[1],)
    assert slate.backtest_slice.missing_decision_match_ids == (expected_match_ids[0],)
    assert slate.backtest_slice.match_count == len(expected_match_ids)
    assert slate.slate_snapshot.match_count == len(expected_match_ids)
    assert result_provider.queries[0].match_ids == analyzed_match_ids
    assert repository.saved == [slate.analysis_artifacts]
    assert {
        mapping.internal_match_id
        for mapping in slate.analysis_artifacts.provider_mappings
    } == set(analyzed_match_ids)


def test_partial_analysis_uses_expected_denominator_for_mixed_missing_inputs() -> None:
    expected_match_ids = tuple(f"s1-match-{number}" for number in range(1, 6))
    plans = (slate_plan(1, match_ids=expected_match_ids),)
    service, archive, _, result_provider, _, settings = build_service(
        plans,
        matches_per_slate=5,
    )
    archive.missing_fixture_mappings.add(expected_match_ids[0])
    archive.missing_odds.add(expected_match_ids[1])
    archive.missing_bonuses.add(expected_match_ids[2])
    archive.missing_quant_inputs.add(expected_match_ids[3])

    result = asyncio.run(
        service.run(request_for("backtest-mixed-missing", plans, settings))
    )
    slate = result.slate_results[0]
    artifacts = slate.analysis_artifacts
    manifest = json.loads(artifacts.analysis_run.input_manifest_json)

    assert tuple(match.match_id for match in artifacts.matches) == (
        expected_match_ids[4],
    )
    assert slate.backtest_slice.missing_decision_match_ids == expected_match_ids[:4]
    assert slate.backtest_slice.match_count == 5
    assert slate.slate_snapshot.match_count == 5
    assert slate.backtest_slice.settled_match_count == 1
    assert result.metrics.match_coverage == Decimal("0.2")
    assert artifacts.portfolios[0].status == PortfolioStatus.NO_BET
    assert result_provider.queries[0].match_ids == (expected_match_ids[4],)
    assert [item["match_id"] for item in manifest["matches"]] == [expected_match_ids[4]]
    assert all(
        item["match_id"] == expected_match_ids[4]
        for key in (
            "market_odds_snapshots",
            "sporttery_bonus_snapshots",
            "manual_quant_inputs",
        )
        for item in manifest[key]
    )


def test_all_missing_decision_inputs_complete_without_result_query() -> None:
    expected_match_ids = tuple(f"s1-match-{number}" for number in range(1, 4))
    plans = (slate_plan(1, match_ids=expected_match_ids),)
    service, archive, repository, result_provider, _, settings = build_service(
        plans,
        matches_per_slate=3,
    )
    archive.missing_fixture_mappings.update(expected_match_ids)

    result = asyncio.run(
        service.run(request_for("backtest-all-missing", plans, settings))
    )
    slate = result.slate_results[0]
    artifacts = slate.analysis_artifacts
    manifest = json.loads(artifacts.analysis_run.input_manifest_json)

    assert artifacts.matches == ()
    assert artifacts.provider_mappings == ()
    assert artifacts.market_odds_snapshots == ()
    assert artifacts.sporttery_bonus_snapshots == ()
    assert artifacts.manual_quant_inputs == ()
    assert artifacts.portfolios[0].status == PortfolioStatus.NO_BET
    assert len(artifacts.portfolio_risk_reports) == 1
    assert slate.backtest_slice.missing_decision_match_ids == expected_match_ids
    assert slate.backtest_slice.match_count == len(expected_match_ids)
    assert slate.backtest_slice.settled_match_count == 0
    assert slate.slate_snapshot.match_count == len(expected_match_ids)
    assert slate.portfolio_settlement is not None
    assert result.metrics.match_coverage == Decimal(0)
    assert result_provider.queries == []
    assert repository.saved == [artifacts]
    assert all(not manifest[key] for key in manifest if key != "version")


def test_partial_analysis_does_not_select_future_snapshots() -> None:
    expected_match_ids = ("s1-match-1", "s1-match-2")
    plans = (slate_plan(1, match_ids=expected_match_ids),)
    service, archive, _, _, _, settings = build_service(plans)
    archive.leaked_future_odds.add(expected_match_ids[0])

    result = asyncio.run(
        service.run(request_for("backtest-future-input", plans, settings))
    )
    slate = result.slate_results[0]

    assert tuple(match.match_id for match in slate.analysis_artifacts.matches) == (
        expected_match_ids[1],
    )
    assert slate.backtest_slice.missing_decision_match_ids == (expected_match_ids[0],)
    assert all(
        snapshot.available_at_utc <= plans[0].decision_as_of_at_utc
        for snapshot in slate.analysis_artifacts.market_odds_snapshots
    )


def test_strict_analysis_still_rejects_missing_and_future_inputs() -> None:
    expected_match_ids = ("s1-match-1", "s1-match-2")
    plan = slate_plan(1, match_ids=expected_match_ids)
    archive = InMemoryArchive((plan,), matches_per_slate=2)
    repository = AnalysisRepositorySpy([])
    settings = AppSettings()
    service, _ = build_analysis_service(archive, repository, settings)
    request = analysis_request_for(
        "strict-inputs",
        plan,
        allow_partial_inputs=False,
    )
    archive.missing_odds.add(expected_match_ids[0])

    with pytest.raises(ValueError, match="required MVP inputs missing"):
        asyncio.run(service.run(request))

    archive.missing_odds.clear()
    archive.leaked_future_odds.add(expected_match_ids[0])
    with pytest.raises(ValueError, match="knowledge cutoff"):
        asyncio.run(service.run(request))


def test_settlement_issue_is_preserved_without_fabricating_a_result() -> None:
    expected_match_ids = ("s1-match-1", "s1-match-2")
    plans = (slate_plan(1, match_ids=expected_match_ids),)
    service, _, _, result_provider, _, settings = build_service(plans)
    issue = MatchSettlementIssue(
        match_id=expected_match_ids[1],
        reason=UnsupportedSettlementReason.VOID,
        detail="provider declared the second leg void",
    )
    result_provider.issues[issue.match_id] = issue

    result = asyncio.run(
        service.run(request_for("backtest-result-issue", plans, settings))
    )
    slate = result.slate_results[0]
    settlement_result = slate.portfolio_settlement_result

    assert slate.match_result_batch.issues == (issue,)
    assert tuple(item.match_id for item in slate.match_result_batch.results) == (
        expected_match_ids[0],
    )
    assert tuple(item.match_id for item in slate.match_snapshots) == (
        expected_match_ids[0],
    )
    assert settlement_result.reason == (
        SettlementResultReason.UNSUPPORTED_SETTLEMENT_CASE
    )
    assert settlement_result.ticket_results[0].coverage.issues == (issue,)
    assert settlement_result.ticket_results[0].settlement is None
    assert slate.ticket_settlements == ()
    assert slate.portfolio_settlement is None
    assert slate.backtest_slice.settled_match_count == 1
    assert slate.backtest_slice.match_result_issues == (issue,)

    tampered = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "match_result_batch": slate.match_result_batch.model_copy(
                            update={"issues": ()}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="settlement coverage"):
        validate_walk_forward_backtest_result(tampered)

    tampered_slice = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "backtest_slice": slate.backtest_slice.model_copy(
                            update={"match_result_issues": ()}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="backtest slice"):
        validate_walk_forward_backtest_result(tampered_slice)


def test_match_result_batch_rejects_overlapping_or_unmapped_issues() -> None:
    plan = slate_plan(1)
    archive = InMemoryArchive((plan,), matches_per_slate=1)
    result = archive.results[archive.plan_match_ids[0][0]]
    result_mapping = mapping(
        result.match_id,
        result.provider_code,
        result.available_at_utc - timedelta(days=1),
    )
    issue = MatchSettlementIssue(
        match_id=result.match_id,
        reason=UnsupportedSettlementReason.VOID,
    )

    with pytest.raises(ValidationError, match="both a result and an issue"):
        MatchResultBatch(
            as_of_at_utc=plan.evaluation_as_of_at_utc,
            results=(result,),
            mappings=(result_mapping,),
            issues=(issue,),
        )
    with pytest.raises(ValidationError, match="requires a provider mapping"):
        MatchResultBatch(
            as_of_at_utc=plan.evaluation_as_of_at_utc,
            results=(),
            mappings=(),
            issues=(issue,),
        )
    with pytest.raises(ValidationError, match="unique by match"):
        MatchResultBatch(
            as_of_at_utc=plan.evaluation_as_of_at_utc,
            results=(),
            mappings=(result_mapping,),
            issues=(issue, issue),
        )


def test_run_uses_actual_execution_time_and_records_all_replay_provenance() -> None:
    plans = (slate_plan(1), slate_plan(2))
    service, _, _, _, _, settings = build_service(plans)
    execution_time = plans[-1].evaluation_as_of_at_utc + timedelta(days=2)
    provenance = (archive_ref(),)

    result = asyncio.run(
        service.run(
            request_for(
                "backtest-execution-time",
                plans,
                settings,
                archive_provenance=provenance,
                execution_time_utc=execution_time,
            )
        )
    )

    assert result.backtest_run.created_at_utc == execution_time
    assert result.backtest_run.archive_provenance == provenance
    assert result.backtest_run.expected_slice_ids == tuple(
        item.backtest_slice.slice_id for item in result.slate_results
    )
    for slate in result.slate_results:
        assert slate.analysis_artifacts.analysis_run.started_at_utc == execution_time
        assert slate.analysis_artifacts.analysis_run.completed_at_utc == execution_time
        assert slate.backtest_slice.decision_input_manifest_hash == (
            slate.analysis_artifacts.analysis_run.input_manifest_hash
        )
        assert slate.backtest_slice.kickoff_from_utc == slate.plan.kickoff_from_utc
        assert slate.backtest_slice.kickoff_to_utc == slate.plan.kickoff_to_utc
        assert slate.backtest_slice.expected_match_ids == (
            slate.plan.match_ids
            or tuple(match.match_id for match in slate.analysis_artifacts.matches)
        )
        result_by_match = {
            item.match_id: item for item in slate.match_result_batch.results
        }
        assert slate.backtest_slice.match_result_ids == tuple(
            result_by_match[match.match_id].match_result_id
            for match in slate.analysis_artifacts.matches
            if match.match_id in result_by_match
        )

    with pytest.raises(ValidationError, match="final evaluation cutoff"):
        request_for(
            "backtest-too-early",
            plans,
            settings,
            execution_time_utc=plans[-1].evaluation_as_of_at_utc
            - timedelta(microseconds=1),
        )
    unchecked_request = request_for(
        "backtest-too-early-service",
        plans,
        settings,
    ).model_copy(
        update={
            "execution_time_utc": plans[-1].evaluation_as_of_at_utc
            - timedelta(microseconds=1)
        }
    )
    with pytest.raises(ValueError, match="final evaluation cutoff"):
        asyncio.run(service.run(unchecked_request))


def test_future_result_is_invisible_and_partial_coverage_is_explicit() -> None:
    plans = (slate_plan(1),)
    service, archive, _, result_provider, _, settings = build_service(plans)
    future_match = archive.plan_match_ids[0][1]
    future_time = plans[0].evaluation_as_of_at_utc + timedelta(hours=1)
    archive.results[future_match] = archive.results[future_match].model_copy(
        update={
            "observed_at_utc": future_time,
            "available_at_utc": future_time + timedelta(minutes=1),
            "ingested_at_utc": future_time + timedelta(minutes=2),
        }
    )

    result = asyncio.run(
        service.run(request_for("backtest-future-result", plans, settings))
    )
    slate = result.slate_results[0]

    assert result_provider.queries[0].as_of_at_utc == plans[0].evaluation_as_of_at_utc
    assert [item.match_id for item in slate.match_result_batch.results] == [
        archive.plan_match_ids[0][0]
    ]
    assert slate.backtest_slice.settled_match_count == 1
    assert slate.backtest_slice.settled_ticket_count == 0
    assert slate.backtest_slice.unsettled_ticket_count == 1
    assert slate.backtest_slice.coverage == Decimal(0)
    assert slate.portfolio_settlement is None
    assert result.metrics.match_coverage == Decimal("0.5")
    assert result.metrics.ticket_coverage == Decimal(0)


def test_partial_ticket_coverage_settles_every_complete_ticket_only() -> None:
    plans = (slate_plan(1),)
    settings = AppSettings(
        portfolio=PortfolioSettings(
            preferred_max_tickets=1,
            absolute_max_tickets=2,
        )
    )
    service, _, _, _, _, settings = build_service(
        plans,
        matches_per_slate=4,
        settings=settings,
        first_ticket_only=True,
    )

    result = asyncio.run(
        service.run(
            request_for(
                "backtest-partial-ticket",
                plans,
                settings,
                budget_fen=2_000,
            )
        )
    )
    slate = result.slate_results[0]

    assert slate.slate_snapshot.ticket_count == 2
    assert slate.slate_snapshot.settled_ticket_count == 1
    assert len(slate.ticket_settlements) == 1
    assert slate.ticket_settlements[0].status == SettlementStatus.LOST
    assert slate.portfolio_settlement is None
    assert slate.backtest_slice.coverage == Decimal("0.5")
    assert slate.slate_snapshot.settled_stake_fen == (
        slate.ticket_settlements[0].stake_fen
    )
    assert slate.slate_snapshot.profit_loss_fen == (
        -slate.ticket_settlements[0].stake_fen
    )
    assert (
        slate.slate_snapshot.realized_loss_when_top_exposure_failed_fen
        <= slate.slate_snapshot.settled_stake_fen
    )
    assert (
        slate.slate_snapshot.realized_loss_when_top_two_exposure_failed_fen
        <= slate.slate_snapshot.settled_stake_fen
    )


def test_no_bet_is_an_explicit_all_cash_slate() -> None:
    plans = (slate_plan(1),)
    service, _, _, _, _, settings = build_service(plans)

    result = asyncio.run(
        service.run(
            request_for(
                "backtest-no-bet",
                plans,
                settings,
                min_selection_ev=Decimal("10"),
            )
        )
    )
    slate = result.slate_results[0]

    assert slate.analysis_artifacts.portfolios[0].status == PortfolioStatus.NO_BET
    assert slate.slate_snapshot.is_no_bet
    assert slate.slate_snapshot.ticket_count == 0
    assert slate.slate_snapshot.stake_fen == 0
    assert slate.slate_snapshot.cash_fen == 1_000
    assert slate.portfolio_settlement is not None
    assert slate.portfolio_settlement.deployed_stake_fen == 0
    assert result.metrics.no_bet_count == 1
    assert result.metrics.no_bet_ratio == Decimal(1)


def test_request_rejects_out_of_order_or_overlapping_slates_and_bad_policy() -> None:
    first = slate_plan(1)
    second = slate_plan(2)
    settings = AppSettings()

    with pytest.raises(ValidationError, match="strict chronological order"):
        request_for("backtest-reversed", (second, first), settings)
    overlapping = second.model_copy(
        update={
            "decision_as_of_at_utc": first.evaluation_as_of_at_utc,
        }
    )
    with pytest.raises(ValidationError, match="strict chronological order"):
        request_for("backtest-overlap", (first, overlapping), settings)
    with pytest.raises(ValidationError, match="supports only"):
        request_for(
            "backtest-policy",
            (first,),
            settings,
            policy=FusionPolicyName.LLM_REVIEW_DELTA_V1,
        )
    with pytest.raises(ValidationError, match="provenance IDs"):
        request_for(
            "backtest-duplicate-archive",
            (first,),
            settings,
            archive_provenance=(archive_ref(), archive_ref()),
        )
    with pytest.raises(ValidationError, match="match IDs must be unique"):
        slate_plan(1, match_ids=("duplicate-match", "duplicate-match"))


def test_strategy_constraints_must_match_the_frozen_analysis() -> None:
    plans = (slate_plan(1),)
    service, _, _, _, _, settings = build_service(plans)
    request = request_for("backtest-constraints", plans, settings).model_copy(
        update={
            "constraints": PortfolioConstraints(max_match_exposure_ratio=Decimal("0.5"))
        }
    )

    with pytest.raises(ValueError, match="strategy constraints"):
        asyncio.run(service.run(request))


def test_frozen_analysis_request_config_must_exactly_match_walk_forward_request() -> (
    None
):
    match_ids = ("s1-match-1", "s1-match-2")
    plans = (slate_plan(1, match_ids=match_ids),)
    service, _, _, _, _, settings = build_service(plans)
    result = asyncio.run(
        service.run(
            request_for(
                "backtest-frozen-config",
                plans,
                settings,
                min_selection_ev=Decimal("0.03"),
                min_ticket_roi=Decimal("0.07"),
            )
        )
    )
    slate = result.slate_results[0]
    base_config = json.loads(slate.analysis_artifacts.analysis_run.config_json)
    invalid_requests = (
        {"fusion_policy": FusionPolicyName.MARKET_QUANT_BLEND_V1.value},
        {"budgets_fen": [1_000, 2_000]},
        {"min_selection_ev": "0.04"},
        {"min_ticket_roi": "0.08"},
        {"allow_partial_inputs": False},
        {"expected_match_ids": list(reversed(match_ids))},
        {"budgets_fen": ["1000"]},
        {"min_selection_ev": 0.03},
        {"allow_partial_inputs": 1},
        {"expected_match_ids": "s1-match-1"},
    )

    for request_update in invalid_requests:
        tampered_config = json.loads(json.dumps(base_config))
        tampered_config["request"].update(request_update)
        _assert_frozen_config_rejected(result, tampered_config)

    relabeled_config = json.loads(json.dumps(base_config))
    request_config = relabeled_config["request"]
    request_config["selection_ev_threshold"] = request_config.pop("min_selection_ev")
    _assert_frozen_config_rejected(result, relabeled_config)

    null_provenance_config = json.loads(json.dumps(base_config))
    null_provenance_config["request"]["provider_runtime_provenance"] = None
    _assert_frozen_config_rejected(
        result,
        null_provenance_config,
        expected_error="AnalysisRun provider runtime provenance",
    )


def test_live_provider_provenance_is_valid_frozen_backtest_config() -> None:
    plans = (slate_plan(1),)
    settings = AppSettings.from_toml("config/live.toml")
    archive = InMemoryArchive(plans, matches_per_slate=2)
    events: list[str] = []
    repository = AnalysisRepositorySpy(events)
    fixture_provider = FixtureStub(archive)
    market_provider = OddsStub(archive)
    sporttery_provider = SportteryStub(archive)
    quant_provider = QuantStub(archive)
    for provider, provider_code in (
        (fixture_provider, "FIXTURE_TEST"),
        (market_provider, "ODDS_TEST"),
        (sporttery_provider, "SPORTTERY_TEST"),
        (quant_provider, "QUANT_TEST"),
    ):
        provider.runtime_provenance = RuntimeProvenance(
            environment=RuntimeEnvironment.LIVE,
            provider_code=provider_code,
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )
    analysis = RunAnalysisService(
        fixture_provider,
        market_provider,
        sporttery_provider,
        quant_provider,
        repository,
        settings,
    )
    result_provider = ResultProviderSpy(archive, repository, events)
    result_provider.runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.LIVE,
        provider_code="RESULT_TEST",
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )
    service = WalkForwardBacktestService(analysis, result_provider)

    result = asyncio.run(
        service.run(request_for("backtest-live-provenance", plans, settings))
    )
    request_config = json.loads(result.analysis_artifacts[0].analysis_run.config_json)[
        "request"
    ]

    assert set(request_config["provider_runtime_provenance"]) == {
        "fixture",
        "market_odds",
        "sporttery",
        "manual_quant",
    }

    tampered = json.loads(result.analysis_artifacts[0].analysis_run.config_json)
    tampered["request"].pop("provider_runtime_provenance")
    _assert_frozen_config_rejected(
        result,
        tampered,
        expected_error="provider runtime provenance",
    )


def test_backtest_rejects_provider_data_mode_before_any_slate_io() -> None:
    plans = (slate_plan(1),)
    service, _, repository, result_provider, odds_provider, settings = build_service(
        plans
    )
    odds_provider.runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.RESEARCH,
        provider_code="ODDS_TEST",
        is_mock=True,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )

    with pytest.raises(RuntimeDataModeError, match="must match"):
        asyncio.run(
            service.run(request_for("backtest-mode-preflight", plans, settings))
        )

    assert repository.saved == []
    assert odds_provider.queries == []
    assert result_provider.queries == []


def _assert_frozen_config_rejected(
    result,
    config: dict[str, object],
    *,
    expected_error: str = "AnalysisRun (request|decision scope)",
) -> None:
    config_json = json.dumps(
        config, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    slate = result.slate_results[0]
    analysis_run = slate.analysis_artifacts.analysis_run.model_copy(
        update={
            "config_json": config_json,
            "config_hash": sha256_text(config_json),
        }
    )
    artifacts = slate.analysis_artifacts.model_copy(
        update={"analysis_run": analysis_run}
    )
    tampered = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(update={"analysis_artifacts": artifacts}),
            )
        }
    )

    with pytest.raises(ValueError, match=expected_error):
        validate_walk_forward_backtest_result(tampered)


def test_result_revalidation_rejects_tampered_derived_artifacts_and_metrics() -> None:
    plans = (slate_plan(1),)
    service, _, _, _, _, settings = build_service(plans)
    result = asyncio.run(service.run(request_for("backtest-tamper", plans, settings)))
    slate = result.slate_results[0]
    snapshot = slate.match_snapshots[0]

    tampered_probability = snapshot.model_copy(
        update={
            "p_final": ThreeWayProbability(
                home_win=Decimal("0.10"),
                draw=Decimal("0.20"),
                away_win=Decimal("0.70"),
            )
        }
    )
    probability_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "match_snapshots": (
                            tampered_probability,
                            *slate.match_snapshots[1:],
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="settled match snapshots"):
        validate_walk_forward_backtest_result(probability_result)

    tampered_outcome = snapshot.model_copy(update={"outcome": SelectionKey.HOME_WIN})
    outcome_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "match_snapshots": (
                            tampered_outcome,
                            *slate.match_snapshots[1:],
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="settled match snapshots"):
        validate_walk_forward_backtest_result(outcome_result)

    result_ids = list(slate.backtest_slice.match_result_ids)
    result_ids[0] = "tampered-result-id"
    result_id_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "backtest_slice": slate.backtest_slice.model_copy(
                            update={"match_result_ids": tuple(result_ids)}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="backtest slice"):
        validate_walk_forward_backtest_result(result_id_result)

    decision_coverage_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "backtest_slice": slate.backtest_slice.model_copy(
                            update={"missing_decision_match_ids": ("tampered-match",)}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="missing decision matches"):
        validate_walk_forward_backtest_result(decision_coverage_result)

    planned_structure_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "backtest_slice": slate.backtest_slice.model_copy(
                            update={
                                "kickoff_from_utc": (
                                    slate.backtest_slice.kickoff_from_utc
                                    + timedelta(minutes=1)
                                )
                            }
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="planned slate structure"):
        validate_walk_forward_backtest_result(planned_structure_result)

    manifest_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "analysis_artifacts": slate.analysis_artifacts.model_copy(
                            update={"provider_mappings": ()}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="input manifest"):
        validate_walk_forward_backtest_result(manifest_result)

    coverage_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "backtest_slice": slate.backtest_slice.model_copy(
                            update={"coverage": Decimal(0)}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="coverage"):
        validate_walk_forward_backtest_result(coverage_result)

    financial_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "slate_snapshot": slate.slate_snapshot.model_copy(
                            update={
                                "budget_fen": slate.slate_snapshot.budget_fen + 1,
                                "cash_fen": slate.slate_snapshot.cash_fen + 1,
                            }
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="slate snapshot"):
        validate_walk_forward_backtest_result(financial_result)

    assert slate.slate_snapshot.realized_loss_when_top_exposure_failed_fen > 0
    exposure_result = result.model_copy(
        update={
            "slate_results": (
                slate.model_copy(
                    update={
                        "slate_snapshot": slate.slate_snapshot.model_copy(
                            update={"realized_loss_when_top_exposure_failed_fen": 0}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="slate snapshot"):
        validate_walk_forward_backtest_result(exposure_result)

    metrics_result = result.model_copy(
        update={
            "metrics": result.metrics.model_copy(
                update={"max_drawdown_fen": result.metrics.max_drawdown_fen + 1}
            )
        }
    )
    with pytest.raises(ValueError, match="metrics"):
        validate_walk_forward_backtest_result(metrics_result)


def test_quant_and_blend_comparison_is_deterministic_and_rejects_mismatch() -> None:
    plans = (slate_plan(1),)
    service, archive, _, result_provider, _, settings = build_service(plans)
    issue = MatchSettlementIssue(
        match_id=archive.plan_match_ids[0][1],
        reason=UnsupportedSettlementReason.VOID,
        detail="provider declared the match void",
    )
    result_provider.issues[issue.match_id] = issue
    quant = asyncio.run(
        service.run(
            request_for(
                "backtest-compare-quant",
                plans,
                settings,
                policy=FusionPolicyName.QUANT_ONLY_V1,
                quant_weight=Decimal("0.10"),
                archive_provenance=(archive_ref(),),
            )
        )
    )
    blend = asyncio.run(
        service.run(
            request_for(
                "backtest-compare-blend",
                plans,
                settings,
                policy=FusionPolicyName.MARKET_QUANT_BLEND_V1,
                archive_provenance=(archive_ref(),),
            )
        )
    )

    comparison = compare_backtests(quant, blend)

    assert comparison == compare_backtests(quant, blend)
    assert comparison.left.fusion_policy == FusionPolicyName.QUANT_ONLY_V1
    assert comparison.right.fusion_policy == FusionPolicyName.MARKET_QUANT_BLEND_V1
    assert comparison.left.metrics == quant.metrics
    assert comparison.right.metrics == blend.metrics
    assert "winner" not in comparison.model_dump()

    code_revision_mismatch = blend.model_copy(
        update={
            "backtest_run": blend.backtest_run.model_copy(
                update={"code_revision": "different-code-revision"}
            )
        }
    )
    with pytest.raises(ValueError, match="code revision"):
        compare_backtests(quant, code_revision_mismatch)

    mismatched = blend.model_copy(
        update={"request": blend.request.model_copy(update={"budget_fen": 1_200})}
    )
    with pytest.raises(ValueError, match="budget"):
        compare_backtests(quant, mismatched)

    different_archive = (archive_ref("archive-2", "b" * 64),)
    archive_mismatch = blend.model_copy(
        update={
            "request": blend.request.model_copy(
                update={"archive_provenance": different_archive}
            ),
            "backtest_run": blend.backtest_run.model_copy(
                update={"archive_provenance": different_archive}
            ),
        }
    )
    with pytest.raises(ValueError, match="archive provenance"):
        compare_backtests(quant, archive_mismatch)

    blend_slate = blend.slate_results[0]
    changed_issue = issue.model_copy(update={"detail": "different issue detail"})
    issue_mismatch = blend.model_copy(
        update={
            "slate_results": (
                blend_slate.model_copy(
                    update={
                        "match_result_batch": (
                            blend_slate.match_result_batch.model_copy(
                                update={"issues": (changed_issue,)}
                            )
                        ),
                        "backtest_slice": blend_slate.backtest_slice.model_copy(
                            update={"match_result_issues": (changed_issue,)}
                        ),
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="MatchSettlementIssue lineage"):
        compare_backtests(quant, issue_mismatch)

    input_mismatch = blend.model_copy(
        update={
            "slate_results": (
                blend_slate.model_copy(
                    update={
                        "backtest_slice": blend_slate.backtest_slice.model_copy(
                            update={"decision_input_manifest_hash": "f" * 64}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="decision input manifest hashes"):
        compare_backtests(quant, input_mismatch)

    tampered_result_ids = list(blend_slate.backtest_slice.match_result_ids)
    tampered_result_ids[0] = "comparison-result-tamper"
    result_mismatch = blend.model_copy(
        update={
            "slate_results": (
                blend_slate.model_copy(
                    update={
                        "backtest_slice": blend_slate.backtest_slice.model_copy(
                            update={"match_result_ids": tuple(tampered_result_ids)}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="match result IDs"):
        compare_backtests(quant, result_mismatch)

    decision_coverage_mismatch = blend.model_copy(
        update={
            "slate_results": (
                blend_slate.model_copy(
                    update={
                        "backtest_slice": blend_slate.backtest_slice.model_copy(
                            update={"missing_decision_match_ids": ("missing-match",)}
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="missing decision match IDs"):
        compare_backtests(quant, decision_coverage_mismatch)

    slice_mismatch = blend.model_copy(
        update={
            "backtest_run": blend.backtest_run.model_copy(
                update={"expected_slice_ids": ("unexpected-slice",)}
            )
        }
    )
    with pytest.raises(ValueError, match="expected slice IDs"):
        compare_backtests(quant, slice_mismatch)
