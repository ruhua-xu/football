import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tomllib

import pytest
from sqlalchemy import select, text

from football_system.application.backtest import (
    BacktestSlatePlan,
    WalkForwardBacktestRequest,
    WalkForwardBacktestService,
)
from football_system.application.ports.data_providers import (
    MatchResultBatch,
    MatchResultQuery,
)
from football_system.application.run_analysis import RunAnalysisService
from football_system.config import AppSettings
from football_system.domain.archive import HistoricalDataMode
from football_system.domain.backtest import BacktestArchiveProvenance
from football_system.domain.betting import PortfolioConstraints, PortfolioStatus
from football_system.domain.market import ThreeWayProbability
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.settlement import (
    MatchSettlementIssue,
    UnsupportedSettlementReason,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import AnalysisRunRecord
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.providers.historical_archive import (
    HistoricalArchiveFixtureProvider,
    HistoricalArchiveMarketOddsProvider,
    HistoricalArchiveQuantProvider,
    HistoricalArchiveSportteryProvider,
    LocalArchiveHistoricalDataProvider,
    LocalArchiveStore,
)


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIRECTORY = ROOT / "data" / "fixtures" / "historical_acceptance"
CONFIG_PATH = ARCHIVE_DIRECTORY / "acceptance_config.toml"


class _FailOnceFixtureProvider:
    def __init__(self, provider, fail_on_call: int) -> None:
        self._provider = provider
        self._fail_on_call = fail_on_call
        self.calls = 0

    @property
    def runtime_provenance(self):
        return self._provider.runtime_provenance

    async def fetch_fixtures(self, query):
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise RuntimeError("injected fixture provider failure")
        return await self._provider.fetch_fixtures(query)


class _IssueResultProvider:
    def __init__(self, provider) -> None:
        self._provider = provider
        self.issue: MatchSettlementIssue | None = None

    @property
    def runtime_provenance(self):
        return self._provider.runtime_provenance

    async def fetch_match_results(self, query):
        batch = await self._provider.fetch_match_results(query)
        unsupported = batch.results[-1]
        self.issue = MatchSettlementIssue(
            match_id=unsupported.match_id,
            reason=UnsupportedSettlementReason.VOID,
            detail="provider declared the match void",
        )
        return MatchResultBatch(
            as_of_at_utc=batch.as_of_at_utc,
            results=batch.results[:-1],
            mappings=batch.mappings,
            issues=(self.issue,),
        )


def test_walk_forward_retry_resumes_completed_analysis_runs() -> None:
    config = _config()
    strategy = config["strategies"][0]
    settings = _settings(config, strategy)
    provider_code = str(config["provider_code"])
    store = LocalArchiveStore(
        ARCHIVE_DIRECTORY,
        data_mode=HistoricalDataMode(str(config["data_mode"])),
    )
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyAnalysisRepository(sessions)
    fixture_provider = _FailOnceFixtureProvider(
        HistoricalArchiveFixtureProvider(store, provider_code),
        fail_on_call=3,
    )
    analysis_service = RunAnalysisService(
        fixture_provider,
        HistoricalArchiveMarketOddsProvider(store, provider_code),
        HistoricalArchiveSportteryProvider(store, provider_code),
        HistoricalArchiveQuantProvider(store, provider_code),
        repository,
        settings,
    )
    service = WalkForwardBacktestService(
        analysis_service,
        LocalArchiveHistoricalDataProvider(store, provider_code),
    )
    plans = tuple(
        BacktestSlatePlan(
            decision_as_of_at_utc=_utc(str(slate["decision_as_of_at_utc"])),
            evaluation_as_of_at_utc=_utc(str(slate["evaluation_as_of_at_utc"])),
            kickoff_from_utc=_utc(str(slate["kickoff_from_utc"])),
            kickoff_to_utc=_utc(str(slate["kickoff_to_utc"])),
        )
        for slate in config["slates"][:3]
    )
    request = WalkForwardBacktestRequest(
        backtest_run_id="persistence-resume-after-provider-failure",
        data_mode=HistoricalDataMode(str(config["data_mode"])),
        fusion_policy=FusionPolicyName(str(strategy["name"])),
        slates=plans,
        budget_fen=int(config["budget_fen"]),
        quant_weight=Decimal(str(strategy["quant_weight"])),
        min_selection_ev=Decimal(str(config["min_selection_ev"])),
        min_ticket_roi=Decimal(str(config["min_ticket_roi"])),
        constraints=PortfolioConstraints.model_validate(
            settings.portfolio.model_dump(mode="python")
        ),
        archive_provenance=tuple(
            BacktestArchiveProvenance.from_manifest(manifest)
            for manifest in store.manifests
        ),
        execution_time_utc=plans[-1].evaluation_as_of_at_utc,
    )

    with pytest.raises(RuntimeError, match="injected fixture provider failure"):
        asyncio.run(service.run(request))

    with sessions() as session:
        persisted_before_retry = set(
            session.scalars(select(AnalysisRunRecord.analysis_run_id))
        )
        statuses = set(session.scalars(select(AnalysisRunRecord.status)))
    assert len(persisted_before_retry) == 2
    assert statuses == {"COMPLETED"}

    result = asyncio.run(service.run(request))

    expected_run_ids = {
        artifacts.analysis_run.analysis_run_id
        for artifacts in result.analysis_artifacts
    }
    with sessions() as session:
        persisted_after_retry = set(
            session.scalars(select(AnalysisRunRecord.analysis_run_id))
        )
    assert persisted_before_retry < persisted_after_retry
    assert persisted_after_retry == expected_run_ids
    assert len(result.slate_results) == len(plans)
    assert repository.table_counts()["analysis_runs"] == len(plans)


def test_real_walk_forward_graph_is_atomic_idempotent_and_correction_safe() -> None:
    config = _config()
    store = LocalArchiveStore(
        ARCHIVE_DIRECTORY,
        data_mode=HistoricalDataMode(str(config["data_mode"])),
    )
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    historical = SqlAlchemyHistoricalRepository(sessions)
    historical.append_historical_archive_imports(
        store.manifests,
        max(item.created_at_utc for item in store.manifests) + timedelta(seconds=1),
    )

    quant = _run_backtest(
        sessions,
        store,
        config,
        config["strategies"][0],
    )
    tampered = _tampered_ticket_stake(quant)
    with pytest.raises(ValueError, match="ticket settlement lineage"):
        historical.save_walk_forward_backtest_result(tampered)
    for tampered in (
        _tampered_brier(quant),
        _tampered_financial_snapshot(quant),
        _tampered_drawdown(quant),
        _tampered_match_snapshot(quant),
    ):
        with pytest.raises(ValueError):
            historical.save_walk_forward_backtest_result(tampered)
    assert _counts(engine, _historical_graph_tables()) == {
        table_name: 0 for table_name in _historical_graph_tables()
    }

    assert historical.save_walk_forward_backtest_result(quant) == quant.backtest_run
    blend = _run_backtest(
        sessions,
        store,
        config,
        config["strategies"][1],
    )
    assert historical.save_walk_forward_backtest_result(blend) == blend.backtest_run

    results = (quant, blend)
    expected_result_ids = {
        item.match_result_id
        for result in results
        for batch in result.match_result_batches
        for item in batch.results
    }
    expected_ticket_settlement_ids = {
        item.settlement_id for result in results for item in result.ticket_settlements
    }
    expected_portfolio_settlement_ids = {
        item.portfolio_settlement_id
        for result in results
        for item in result.portfolio_settlements
    }
    expected_portfolio_ticket_links = sum(
        len(item.ticket_settlement_ids)
        for result in results
        for item in result.portfolio_settlements
    )
    assert len(expected_result_ids) == 59
    assert _counts(engine, _historical_graph_tables()) == {
        "match_results": 59,
        "ticket_settlements": len(expected_ticket_settlement_ids),
        "ticket_settlement_match_results": 2 * len(expected_ticket_settlement_ids),
        "portfolio_settlements": len(expected_portfolio_settlement_ids),
        "portfolio_settlement_tickets": expected_portfolio_ticket_links,
        "backtest_runs": 2,
        "backtest_slices": 20,
        "backtest_metric_snapshots": 2,
        "backtest_metric_settlements": len(expected_portfolio_settlement_ids),
        "backtest_metric_ticket_settlements": len(expected_ticket_settlement_ids),
    }

    for result in results:
        assert (
            historical.find_backtest_run_value(result.backtest_run.backtest_run_id)
            == result.backtest_run
        )
        slices = historical.backtest_slices(result.backtest_run.backtest_run_id)
        assert tuple(item.slice_no for item in slices) == tuple(range(1, 11))
        assert tuple(item.backtest_slice_id for item in slices) == tuple(
            item.slice_id for item in result.backtest_slices
        )
        assert all(
            item.created_at_utc == result.backtest_run.created_at_utc for item in slices
        )
        final_slice = slices[-1]
        assert final_slice.match_count == 6
        assert final_slice.settled_match_count == 5
        assert final_slice.coverage == result.backtest_slices[-1].coverage
        assert (
            historical.backtest_slice_values(result.backtest_run.backtest_run_id)
            == result.backtest_slices
        )
        with pytest.raises(ValueError, match="absent from its BacktestRun manifest"):
            historical.append_backtest_slice(
                result.backtest_slices[-1].model_copy(
                    update={"slice_id": f"{result.backtest_run.backtest_run_id}-extra"}
                ),
                slice_no=11,
            )

        no_bet_slate = next(
            item
            for item in result.slate_results
            if item.analysis_artifacts.portfolios[0].status is PortfolioStatus.NO_BET
        )
        assert no_bet_slate.portfolio_settlement is not None
        assert no_bet_slate.portfolio_settlement.deployed_stake_fen == 0
        for slate in result.slate_results:
            expected_count = int(slate.portfolio_settlement is not None)
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM portfolio_settlements "
                            "WHERE parent_analysis_run_id = :analysis_run_id"
                        ),
                        {
                            "analysis_run_id": (
                                slate.analysis_artifacts.analysis_run.analysis_run_id
                            )
                        },
                    )
                    == expected_count
                )

        metrics = historical.latest_backtest_metric_snapshots(
            result.backtest_run.backtest_run_id,
            result.request.slates[-1].evaluation_as_of_at_utc,
        )
        assert len(metrics) == 1
        assert metrics[0].metric_scope == "RUN"
        assert metrics[0].metric_key == "AGGREGATE"
        assert metrics[0].as_of_at_utc == (
            result.request.slates[-1].evaluation_as_of_at_utc
        )
        assert metrics[0].calculated_at_utc == result.backtest_run.created_at_utc
        with engine.connect() as connection:
            stored_lineage = set(
                connection.execute(
                    text(
                        "SELECT portfolio_settlement_id "
                        "FROM backtest_metric_settlements "
                        "WHERE metric_snapshot_id = :metric_snapshot_id"
                    ),
                    {"metric_snapshot_id": metrics[0].metric_snapshot_id},
                ).scalars()
            )
            stored_ticket_lineage = set(
                connection.execute(
                    text(
                        "SELECT settlement_id "
                        "FROM backtest_metric_ticket_settlements "
                        "WHERE metric_snapshot_id = :metric_snapshot_id"
                    ),
                    {"metric_snapshot_id": metrics[0].metric_snapshot_id},
                ).scalars()
            )
        assert stored_lineage == {
            item.portfolio_settlement_id for item in result.portfolio_settlements
        }
        assert stored_ticket_lineage == {
            item.settlement_id for item in result.ticket_settlements
        }

    counts_before_replay = _counts(engine, _historical_graph_tables())
    metric_ids_before_replay = {
        result.backtest_run.backtest_run_id: historical.latest_backtest_metric_snapshots(
            result.backtest_run.backtest_run_id,
            result.request.slates[-1].evaluation_as_of_at_utc,
        )[0].metric_snapshot_id
        for result in results
    }
    for result in results:
        historical.save_walk_forward_backtest_result(result)
    assert _counts(engine, _historical_graph_tables()) == counts_before_replay
    assert metric_ids_before_replay == {
        result.backtest_run.backtest_run_id: historical.latest_backtest_metric_snapshots(
            result.backtest_run.backtest_run_id,
            result.request.slates[-1].evaluation_as_of_at_utc,
        )[0].metric_snapshot_id
        for result in results
    }

    correction = config["special_cases"]["result_correction"]
    result_provider = LocalArchiveHistoricalDataProvider(
        store,
        str(config["provider_code"]),
    )
    correction_batch = asyncio.run(
        result_provider.fetch_match_results(
            MatchResultQuery(
                match_ids=(str(correction["match_id"]),),
                as_of_at_utc=_utc(str(correction["visible_at_utc"])),
            )
        )
    )
    assert historical.append_match_result_batch(correction_batch) == correction_batch
    initial_cutoff = next(
        slate.evaluation_as_of_at_utc
        for slate in quant.request.slates
        if str(correction["match_id"])
        in {
            match.match_id
            for match in quant.slate_results[
                quant.request.slates.index(slate)
            ].analysis_artifacts.matches
        }
    )
    initial = historical.latest_match_results(
        (str(correction["match_id"]),),
        initial_cutoff,
        str(config["provider_code"]),
    )
    corrected = historical.latest_match_results(
        (str(correction["match_id"]),),
        _utc(str(correction["visible_at_utc"])),
        str(config["provider_code"]),
    )
    assert initial[0].match_result_id == correction["initial_result_id"]
    assert corrected[0].match_result_id == correction["corrected_result_id"]
    assert corrected[0].supersedes_match_result_id == initial[0].match_result_id
    assert _counts(engine, ("match_results",))["match_results"] == 60

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_walk_forward_result_issue_round_trips_in_slice_lineage() -> None:
    config = _config()
    strategy = config["strategies"][0]
    settings = _settings(config, strategy)
    provider_code = str(config["provider_code"])
    data_mode = HistoricalDataMode(str(config["data_mode"]))
    store = LocalArchiveStore(ARCHIVE_DIRECTORY, data_mode=data_mode)
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    historical = SqlAlchemyHistoricalRepository(sessions)
    historical.append_historical_archive_imports(
        store.manifests,
        max(item.created_at_utc for item in store.manifests) + timedelta(seconds=1),
    )
    result_provider = _IssueResultProvider(
        LocalArchiveHistoricalDataProvider(store, provider_code)
    )
    service = WalkForwardBacktestService(
        RunAnalysisService(
            HistoricalArchiveFixtureProvider(store, provider_code),
            HistoricalArchiveMarketOddsProvider(store, provider_code),
            HistoricalArchiveSportteryProvider(store, provider_code),
            HistoricalArchiveQuantProvider(store, provider_code),
            SqlAlchemyAnalysisRepository(sessions),
            settings,
        ),
        result_provider,
    )
    slate = config["slates"][0]
    plan = BacktestSlatePlan(
        decision_as_of_at_utc=_utc(str(slate["decision_as_of_at_utc"])),
        evaluation_as_of_at_utc=_utc(str(slate["evaluation_as_of_at_utc"])),
        kickoff_from_utc=_utc(str(slate["kickoff_from_utc"])),
        kickoff_to_utc=_utc(str(slate["kickoff_to_utc"])),
        match_ids=tuple(str(item) for item in slate["match_ids"]),
    )
    result = asyncio.run(
        service.run(
            WalkForwardBacktestRequest(
                backtest_run_id="persistence-result-issue",
                data_mode=data_mode,
                fusion_policy=FusionPolicyName(str(strategy["name"])),
                slates=(plan,),
                budget_fen=int(config["budget_fen"]),
                quant_weight=Decimal(str(strategy["quant_weight"])),
                min_selection_ev=Decimal(str(config["min_selection_ev"])),
                min_ticket_roi=Decimal(str(config["min_ticket_roi"])),
                constraints=PortfolioConstraints.model_validate(
                    settings.portfolio.model_dump(mode="python")
                ),
                archive_provenance=tuple(
                    BacktestArchiveProvenance.from_manifest(manifest)
                    for manifest in store.manifests
                ),
                execution_time_utc=_utc("2026-09-01T10:00:00Z"),
            )
        )
    )

    assert result_provider.issue is not None
    assert result.backtest_slices[0].match_result_issues == (result_provider.issue,)
    historical.save_walk_forward_backtest_result(result)

    reopened = SqlAlchemyHistoricalRepository(sessions)
    assert reopened.backtest_slice_values(result.backtest_run.backtest_run_id) == (
        result.backtest_slices[0],
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT slice_version FROM backtest_slices "
                    "WHERE backtest_slice_id = :slice_id"
                ),
                {"slice_id": result.backtest_slices[0].slice_id},
            )
            == "BACKTEST_SLICE_RECORD_V2"
        )


def _config() -> dict[str, object]:
    with CONFIG_PATH.open("rb") as stream:
        return tomllib.load(stream)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _settings(
    config: dict[str, object],
    strategy: dict[str, object],
) -> AppSettings:
    return AppSettings.model_validate(
        {
            "analysis": {
                "pipeline_version": "HISTORICAL_PERSISTENCE_V1",
                "fusion_policy": strategy["name"],
                "quant_weight": strategy["quant_weight"],
                "min_selection_ev": config["min_selection_ev"],
                "min_ticket_roi": config["min_ticket_roi"],
            },
            "portfolio": config["portfolio"],
            "sporttery": config["sporttery"],
        }
    )


def _run_backtest(sessions, store, config, strategy):
    settings = _settings(config, strategy)
    provider_code = str(config["provider_code"])
    analysis_service = RunAnalysisService(
        HistoricalArchiveFixtureProvider(store, provider_code),
        HistoricalArchiveMarketOddsProvider(store, provider_code),
        HistoricalArchiveSportteryProvider(store, provider_code),
        HistoricalArchiveQuantProvider(store, provider_code),
        SqlAlchemyAnalysisRepository(sessions),
        settings,
    )
    service = WalkForwardBacktestService(
        analysis_service,
        LocalArchiveHistoricalDataProvider(store, provider_code),
    )
    plans = tuple(
        BacktestSlatePlan(
            decision_as_of_at_utc=_utc(str(slate["decision_as_of_at_utc"])),
            evaluation_as_of_at_utc=_utc(str(slate["evaluation_as_of_at_utc"])),
            kickoff_from_utc=_utc(str(slate["kickoff_from_utc"])),
            kickoff_to_utc=_utc(str(slate["kickoff_to_utc"])),
        )
        for slate in config["slates"]
    )
    policy = FusionPolicyName(str(strategy["name"]))
    return asyncio.run(
        service.run(
            WalkForwardBacktestRequest(
                backtest_run_id=f"persistence-{policy.value.lower()}",
                data_mode=HistoricalDataMode(str(config["data_mode"])),
                fusion_policy=policy,
                slates=plans,
                budget_fen=int(config["budget_fen"]),
                quant_weight=Decimal(str(strategy["quant_weight"])),
                min_selection_ev=Decimal(str(config["min_selection_ev"])),
                min_ticket_roi=Decimal(str(config["min_ticket_roi"])),
                constraints=PortfolioConstraints.model_validate(
                    settings.portfolio.model_dump(mode="python")
                ),
                archive_provenance=tuple(
                    BacktestArchiveProvenance.from_manifest(manifest)
                    for manifest in store.manifests
                ),
            )
        )
    )


def _tampered_ticket_stake(result):
    slate = result.slate_results[0]
    settlement = slate.ticket_settlements[0]
    tampered_settlement = settlement.model_copy(
        update={
            "stake_fen": settlement.stake_fen + 1,
            "profit_loss_fen": settlement.gross_payout_fen - settlement.stake_fen - 1,
        }
    )
    ticket_results = tuple(
        item.model_copy(update={"settlement": tampered_settlement})
        if item.ticket_id == tampered_settlement.ticket_id
        else item
        for item in slate.portfolio_settlement_result.ticket_results
    )
    settlement_result = slate.portfolio_settlement_result.model_copy(
        update={"ticket_results": ticket_results}
    )
    tampered_slate = slate.model_copy(
        update={
            "portfolio_settlement_result": settlement_result,
            "ticket_settlements": (
                tampered_settlement,
                *slate.ticket_settlements[1:],
            ),
        }
    )
    return result.model_copy(
        update={
            "slate_results": (tampered_slate, *result.slate_results[1:]),
        }
    )


def _tampered_brier(result):
    delta = Decimal("0.000000000001")
    probability = result.metrics.p_final
    components = probability.brier_by_outcome.model_copy(
        update={"home_win": probability.brier_by_outcome.home_win + delta}
    )
    probability = probability.model_copy(
        update={
            "brier_by_outcome": components,
            "multiclass_brier_score": probability.multiclass_brier_score + delta,
        }
    )
    return result.model_copy(
        update={"metrics": result.metrics.model_copy(update={"p_final": probability})}
    )


def _tampered_financial_snapshot(result):
    index = next(
        index
        for index, slate in enumerate(result.slate_results)
        if slate.slate_snapshot.winning_ticket_count > 0
    )
    slate = result.slate_results[index]
    snapshot = slate.slate_snapshot.model_copy(
        update={
            "gross_payout_fen": slate.slate_snapshot.gross_payout_fen + 1,
            "profit_loss_fen": slate.slate_snapshot.profit_loss_fen + 1,
        }
    )
    tampered = slate.model_copy(update={"slate_snapshot": snapshot})
    slates = list(result.slate_results)
    slates[index] = tampered
    return result.model_copy(update={"slate_results": tuple(slates)})


def _tampered_drawdown(result):
    return result.model_copy(
        update={
            "metrics": result.metrics.model_copy(
                update={"max_drawdown_fen": result.metrics.max_drawdown_fen + 1}
            )
        }
    )


def _tampered_match_snapshot(result):
    slate = next(item for item in result.slate_results if item.match_snapshots)
    snapshot = slate.match_snapshots[0]
    delta = Decimal("0.000000000001")
    probabilities = ThreeWayProbability(
        home_win=snapshot.p_final.home_win + delta,
        draw=snapshot.p_final.draw - delta,
        away_win=snapshot.p_final.away_win,
    )
    tampered_snapshot = snapshot.model_copy(update={"p_final": probabilities})
    tampered_slate = slate.model_copy(
        update={
            "match_snapshots": (
                tampered_snapshot,
                *slate.match_snapshots[1:],
            )
        }
    )
    slates = tuple(
        tampered_slate if item is slate else item for item in result.slate_results
    )
    return result.model_copy(update={"slate_results": slates})


def _historical_graph_tables() -> tuple[str, ...]:
    return (
        "match_results",
        "ticket_settlements",
        "ticket_settlement_match_results",
        "portfolio_settlements",
        "portfolio_settlement_tickets",
        "backtest_runs",
        "backtest_slices",
        "backtest_metric_snapshots",
        "backtest_metric_settlements",
        "backtest_metric_ticket_settlements",
    )


def _counts(engine, table_names: tuple[str, ...]) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table_name: int(
                connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) or 0
            )
            for table_name in table_names
        }
