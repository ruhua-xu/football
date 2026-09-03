import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
    _validate_live_provider_outputs,
    _validate_source_payloads,
)
from football_system.application.models import AnalysisArtifacts
from football_system.application.quant_model import (
    MVP_INPUT_MANIFEST_V3,
    build_model_input_manifest_json,
    freeze_elo_model_state,
)
from football_system.application.post_review import CreateFusionRunService
from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
    canonical_json,
    validate_review_files,
)
from football_system.application.environment import (
    MockProvenanceInLiveError,
    ProviderRuntimeProvenanceMismatchError,
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.ports.data_providers import (
    FixtureQuery,
    MarketOddsReconciliationIssue,
    MarketOddsReconciliationIssueReason,
    SnapshotQuery,
)
from football_system.config import AppSettings
from football_system.domain.archive import (
    HistoricalDataMode,
    match_result_payload_sha256,
)
from football_system.domain.analysis import ModelAnalysisMatchContext
from football_system.domain.betting import CandidateStatus, PortfolioStatus
from football_system.domain.common import stable_id
from football_system.domain.prediction import (
    FusionPolicyName,
    ModelQuantPrediction,
    QuantModelEvaluation,
    QuantModelEvaluationStatus,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloRegularTimeResult,
    EloThreeWayBaseline,
)
from football_system.infrastructure.database.models import (
    AnalysisPacketRecord,
    AnalysisRunRecord,
    Base,
    BetCandidateRecord,
    FinalPredictionOutcomeRecord,
    MarketProbabilityOutcomeRecord,
    TicketCandidateRecord,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.post_review_repositories import (
    SqlAlchemyPostReviewRepository,
)
from football_system.infrastructure.database.review_repositories import (
    SqlAlchemyReviewArtifactRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.providers.mock.dataset import (
    MockDataset,
    payload_hash,
)
from football_system.infrastructure.providers.mock.fixtures import MockFixtureProvider
from football_system.infrastructure.providers.mock.manual_quant import (
    MockManualQuantProvider,
)
from football_system.infrastructure.providers.mock.market_odds import (
    MockMarketOddsProvider,
)
from football_system.infrastructure.providers.mock.sporttery import (
    MockSportteryProvider,
)

EXECUTION_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


class RetrospectiveMockMarketOddsProvider(MockMarketOddsProvider):
    runtime_provenance = RuntimeProvenance(
        environment=RuntimeEnvironment.RESEARCH,
        provider_code=MockMarketOddsProvider.provider_code,
        provenance="retrospectively imported market fixture",
        is_mock=True,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )

    async def fetch_market_odds(self, query):
        batch = await super().fetch_market_odds(query)
        return batch.model_copy(
            update={
                "snapshots": tuple(
                    snapshot.model_copy(update={"ingested_at_utc": EXECUTION_TIME})
                    for snapshot in batch.snapshots
                )
            }
        )


def build_service(
    dataset: MockDataset | None = None,
    market_provider_factory=MockMarketOddsProvider,
    settings: AppSettings | None = None,
):
    settings = settings or AppSettings.from_toml("config/mvp.toml")
    dataset = dataset or MockDataset.from_json(settings.mock.fixture_path)
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyAnalysisRepository(sessions)
    service = RunAnalysisService(
        fixture_provider=MockFixtureProvider(dataset),
        market_odds_provider=market_provider_factory(dataset),
        sporttery_provider=MockSportteryProvider(dataset),
        manual_quant_provider=MockManualQuantProvider(dataset),
        repository=repository,
        settings=settings,
    )
    return service, repository, sessions, dataset, settings


def test_live_analysis_rejects_mock_providers_before_fetch_or_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _, dataset, _ = build_service(
        settings=AppSettings.from_toml("config/live.toml")
    )

    async def fail_if_fetched(*args, **kwargs):
        del args, kwargs
        raise AssertionError("provider I/O occurred before runtime isolation")

    for attribute, method_name in (
        ("_fixture_provider", "fetch_fixtures"),
        ("_market_odds_provider", "fetch_market_odds"),
        ("_sporttery_provider", "fetch_fixed_bonus"),
        ("_manual_quant_provider", "fetch_manual_quant"),
    ):
        monkeypatch.setattr(getattr(service, attribute), method_name, fail_if_fetched)

    with pytest.raises(MockProvenanceInLiveError):
        asyncio.run(service.run(request_for(dataset, "run-live-mock-rejected")))

    assert repository.table_counts()["analysis_runs"] == 0


def test_mock_analysis_does_not_trust_unvalidated_source_time_provenance() -> None:
    service, _, _, dataset, _ = build_service(
        market_provider_factory=RetrospectiveMockMarketOddsProvider
    )

    with pytest.raises(ValueError, match="knowledge cutoff"):
        asyncio.run(service.run(request_for(dataset, "run-source-time-unvalidated")))


def test_live_analysis_rejects_provider_output_that_conflicts_with_provenance() -> None:
    service, repository, _, dataset, _ = build_service(
        settings=AppSettings.from_toml("config/live.toml")
    )
    declared_codes = {
        "_fixture_provider": "DECLARED_FIXTURE",
        "_market_odds_provider": "DECLARED_MARKET",
        "_sporttery_provider": "DECLARED_SPORTTERY",
        "_manual_quant_provider": "DECLARED_QUANT",
    }
    for attribute, provider_code in declared_codes.items():
        provider = getattr(service, attribute)
        provider.runtime_provenance = RuntimeProvenance(
            environment=RuntimeEnvironment.LIVE,
            provider_code=provider_code,
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )

    with pytest.raises(ProviderRuntimeProvenanceMismatchError):
        asyncio.run(service.run(request_for(dataset, "run-live-mismatch-rejected")))

    assert repository.table_counts()["analysis_runs"] == 0


def test_live_output_validation_requires_mapping_quant_and_issue_identity() -> None:
    service, _, _, dataset, _ = build_service()

    async def fetch_batches():
        fixture_batch = await service._fixture_provider.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=dataset.as_of_at_utc,
                kickoff_to_utc=dataset.as_of_at_utc + timedelta(days=2),
                as_of_at_utc=dataset.as_of_at_utc,
            )
        )
        query = SnapshotQuery(
            match_ids=tuple(match.match_id for match in fixture_batch.matches),
            as_of_at_utc=dataset.as_of_at_utc,
        )
        market_batch, sporttery_batch, quant_batch = await asyncio.gather(
            service._market_odds_provider.fetch_market_odds(query),
            service._sporttery_provider.fetch_fixed_bonus(query),
            service._manual_quant_provider.fetch_manual_quant(query),
        )
        return fixture_batch, market_batch, sporttery_batch, quant_batch

    fixture_batch, market_batch, sporttery_batch, quant_batch = asyncio.run(
        fetch_batches()
    )
    provenance = {
        "fixture": service._fixture_provider.runtime_provenance,
        "market_odds": service._market_odds_provider.runtime_provenance,
        "sporttery": service._sporttery_provider.runtime_provenance,
        "manual_quant": service._manual_quant_provider.runtime_provenance,
    }
    issue_codes = frozenset(
        {service._market_odds_provider.runtime_provenance.provider_code}
    )

    with pytest.raises(
        ProviderRuntimeProvenanceMismatchError,
        match="without declared provider mapping",
    ):
        _validate_live_provider_outputs(
            provenance,
            fixture_batch.model_copy(update={"mappings": ()}),
            market_batch,
            sporttery_batch,
            quant_batch,
            issue_codes,
        )

    with pytest.raises(ValueError, match="exact provider mapping"):
        _validate_source_payloads(
            fixture_batch.mappings,
            market_batch.snapshots,
            sporttery_batch.snapshots,
            quant_batch.inputs,
        )

    invalid_snapshot = market_batch.snapshots[0].model_copy(
        update={
            "captured_at_utc": market_batch.snapshots[0].available_at_utc
            + timedelta(seconds=1)
        }
    )
    with pytest.raises(ValueError, match="captured, available, ingested"):
        _validate_source_payloads(
            (*fixture_batch.mappings, *market_batch.mappings),
            (invalid_snapshot,),
            (),
            (),
        )

    with pytest.raises(
        ProviderRuntimeProvenanceMismatchError,
        match="without provider identity",
    ):
        _validate_live_provider_outputs(
            provenance,
            fixture_batch,
            market_batch,
            sporttery_batch,
            quant_batch.model_copy(update={"provider_code": None}),
            issue_codes,
        )

    synthetic_issue = MarketOddsReconciliationIssue(
        issue_id="synthetic-live-issue",
        reason=MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
        provider_code="SYNTHETIC/ISSUE",
        code="SYNTHETIC_EVENT_INVALID",
        detail="synthetic issue must not cross the live provenance boundary",
    )
    with pytest.raises(
        ProviderRuntimeProvenanceMismatchError,
        match="issue outside declared provenance",
    ):
        _validate_live_provider_outputs(
            provenance,
            fixture_batch,
            market_batch.model_copy(update={"issues": (synthetic_issue,)}),
            sporttery_batch,
            quant_batch,
            issue_codes,
        )


def request_for(
    dataset: MockDataset,
    run_id: str,
    *,
    budgets_fen: tuple[int, ...] = (10_000, 20_000),
    fusion_policy: FusionPolicyName = FusionPolicyName.QUANT_ONLY_V1,
    min_selection_ev: Decimal | None = None,
) -> RunAnalysisRequest:
    return RunAnalysisRequest(
        as_of_at_utc=dataset.as_of_at_utc,
        kickoff_from_utc=dataset.as_of_at_utc,
        kickoff_to_utc=dataset.as_of_at_utc + timedelta(days=2),
        budgets_fen=budgets_fen,
        fusion_policy=fusion_policy,
        min_selection_ev=min_selection_ev,
        analysis_run_id=run_id,
        execution_time_utc=EXECUTION_TIME,
    )


def test_full_mvp_analysis_persists_replayable_artifacts() -> None:
    service, repository, sessions, dataset, settings = build_service()

    artifacts = asyncio.run(service.run(request_for(dataset, "run-e2e-main")))

    assert len(artifacts.matches) == 6
    assert len(artifacts.market_predictions) == 6
    assert len(artifacts.quant_predictions) == 6
    assert len(artifacts.final_predictions) == 6
    assert len(artifacts.selection_candidates) == 18
    assert (
        sum(
            candidate.status == CandidateStatus.ELIGIBLE
            for candidate in artifacts.selection_candidates
        )
        == 5
    )
    assert len(artifacts.ticket_candidates) == 10
    assert [portfolio.budget_fen for portfolio in artifacts.portfolios] == [
        10_000,
        20_000,
    ]
    assert all(
        portfolio.status == PortfolioStatus.RECOMMENDED
        for portfolio in artifacts.portfolios
    )
    assert all(
        len(portfolio.tickets) == settings.portfolio.preferred_max_tickets
        for portfolio in artifacts.portfolios
    )
    assert all(
        len(portfolio.tickets) <= portfolio.constraints.absolute_max_tickets
        for portfolio in artifacts.portfolios
    )
    assert [portfolio.total_stake_fen for portfolio in artifacts.portfolios] == [
        10_000,
        20_000,
    ]
    assert all(
        final.probabilities == quant.probabilities
        for final, quant in zip(
            artifacts.final_predictions,
            artifacts.quant_predictions,
            strict=True,
        )
    )

    counts = repository.table_counts()
    assert counts == {
        "matches": 6,
        "market_odds_snapshots": 6,
        "sporttery_bonus_snapshots": 6,
        "manual_quant_inputs": 6,
        "analysis_runs": 1,
        "market_probabilities": 6,
        "quant_predictions": 6,
        "final_predictions": 6,
        "bet_candidates": 18,
        "ticket_candidates": 10,
        "portfolios": 2,
        "portfolio_cash_positions": 2,
        "tickets": 8,
        "portfolio_risk_reports": 2,
        "portfolio_match_exposures": 8,
        "portfolio_selection_exposures": 8,
        "portfolio_stress_results": 6,
        "portfolio_stress_ticket_results": 24,
    }
    stored_manifest = repository.load_input_manifest("run-e2e-main")
    assert stored_manifest.version == "MVP_INPUT_MANIFEST_V2"
    assert stored_manifest.manifest_json == artifacts.analysis_run.input_manifest_json
    assert stored_manifest.manifest_hash == artifacts.analysis_run.input_manifest_hash
    with sessions() as session:
        run = session.scalar(
            select(AnalysisRunRecord).where(
                AnalysisRunRecord.analysis_run_id == "run-e2e-main"
            )
        )
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.config_hash == artifacts.analysis_run.config_hash
        assert run.code_revision.startswith("package:")
        for prediction in artifacts.market_predictions:
            stored = {
                row.selection_key: row.probability
                for row in session.scalars(
                    select(MarketProbabilityOutcomeRecord).where(
                        MarketProbabilityOutcomeRecord.market_probability_id
                        == prediction.prediction_id
                    )
                )
            }
            expected = {
                selection.value: probability
                for selection, probability in prediction.probabilities.items()
            }
            assert stored == expected
        for prediction in artifacts.final_predictions:
            stored = {
                row.selection_key: row.probability
                for row in session.scalars(
                    select(FinalPredictionOutcomeRecord).where(
                        FinalPredictionOutcomeRecord.final_prediction_id
                        == prediction.prediction_id
                    )
                )
            }
            expected = {
                selection.value: probability
                for selection, probability in prediction.probabilities.items()
            }
            assert stored == expected
        for candidate in artifacts.selection_candidates:
            stored = session.get(BetCandidateRecord, candidate.candidate_id)
            assert stored is not None
            assert stored.break_even_probability == candidate.break_even_probability
            assert stored.ev == candidate.ev
        for candidate in artifacts.ticket_candidates:
            stored = session.get(TicketCandidateRecord, candidate.ticket_candidate_id)
            assert stored is not None
            assert stored.joint_probability == candidate.joint_probability
            assert stored.expected_roi == candidate.expected_roi

    blocked_mutations = (
        "UPDATE market_probability_outcomes SET probability = 0.99",
        "DELETE FROM market_probability_inputs",
        "UPDATE quant_prediction_outcomes SET probability = 0.99",
        "UPDATE final_prediction_outcomes SET probability = 0.99",
        "DELETE FROM ticket_candidate_legs",
        "UPDATE tickets SET multiplier = 1",
        "UPDATE portfolio_cash_positions SET amount_fen = 1",
        "UPDATE portfolio_risk_reports SET cash_fen = 1",
        "DELETE FROM portfolio_stress_results",
        "DELETE FROM ticket_legs",
        "UPDATE matches SET status = 'MUTATED'",
        "INSERT OR REPLACE INTO matches ("
        "internal_match_id, competition_id, home_team_id, away_team_id, "
        "kickoff_at_utc, status, available_at_utc, created_at_utc) "
        "SELECT internal_match_id, competition_id, home_team_id, away_team_id, "
        "kickoff_at_utc, 'MUTATED', available_at_utc, created_at_utc "
        "FROM matches LIMIT 1",
        "INSERT OR REPLACE INTO analysis_runs ("
        "analysis_run_id, run_kind, as_of_at_utc, status, started_at_utc, "
        "completed_at_utc, pipeline_version, code_revision, config_json, "
        "config_hash, input_manifest_version, input_manifest_json, "
        "input_manifest_hash, replay_of_run_id) "
        "SELECT analysis_run_id, run_kind, as_of_at_utc, 'RUNNING', "
        "started_at_utc, NULL, pipeline_version, code_revision, config_json, "
        "config_hash, input_manifest_version, input_manifest_json, "
        "input_manifest_hash, replay_of_run_id FROM analysis_runs "
        "WHERE analysis_run_id = 'run-e2e-main'",
        "INSERT INTO market_odds_quotes "
        "SELECT snapshot_id, 'INJECTED', 2 FROM market_odds_snapshots LIMIT 1",
        "INSERT INTO manual_quant_input_outcomes "
        "SELECT input_id, 'INJECTED', 0.1 FROM manual_quant_inputs LIMIT 1",
    )
    for statement in blocked_mutations:
        with pytest.raises(IntegrityError):
            with sessions.begin() as session:
                session.execute(text(statement))

    with sessions.begin() as session:
        session.execute(
            text(
                """
                INSERT INTO market_odds_snapshots (
                    snapshot_id, internal_match_id, provider_id, bookmaker_id,
                    market_key, market_type, handicap_value, captured_at_utc,
                    available_at_utc, ingested_at_utc, source_snapshot_key, payload_hash
                )
                SELECT
                    'partial-source', internal_match_id, provider_id, bookmaker_id,
                    market_key, market_type, handicap_value, captured_at_utc,
                    available_at_utc, ingested_at_utc, 'partial-source', 'partial-hash'
                FROM market_odds_snapshots LIMIT 1
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO market_odds_quotes (snapshot_id, selection_key, odds)
                VALUES ('partial-source', 'HOME_WIN', 2),
                       ('partial-source', 'DRAW', 3)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO analysis_runs (
                    analysis_run_id, run_kind, as_of_at_utc, status,
                    started_at_utc, completed_at_utc, pipeline_version,
                    code_revision, config_json, config_hash,
                    input_manifest_version, input_manifest_json,
                    input_manifest_hash, replay_of_run_id
                )
                SELECT
                    'run-partial-source', run_kind, as_of_at_utc, 'RUNNING',
                    started_at_utc, NULL, pipeline_version, code_revision,
                    config_json, config_hash, input_manifest_version,
                    input_manifest_json, input_manifest_hash, NULL
                FROM analysis_runs WHERE analysis_run_id = 'run-e2e-main'
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO portfolios (
                    portfolio_id, analysis_run_id, budget_fen, total_stake_fen,
                    unused_budget_fen, status, no_bet_reason,
                    strategy_version, strategy_config_json
                ) VALUES (
                    'partial-portfolio', 'run-partial-source', 100, 0, 100,
                    'NO_BET', 'NO_BET_NO_VALUE', 'TEST', '{}'
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO portfolio_cash_positions (
                    cash_position_id, portfolio_id, amount_fen, expected_profit_fen
                ) VALUES ('partial-cash', 'partial-portfolio', 100, 0)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO portfolio_risk_reports (
                    risk_report_id, analysis_run_id, portfolio_id, policy_version,
                    budget_fen, total_stake_fen, cash_fen, cash_ratio,
                    expected_profit_fen, total_stake_at_risk_fen,
                    max_single_ticket_exposure_fen, max_match_exposure_fen
                ) VALUES (
                    'partial-risk', 'run-partial-source', 'partial-portfolio',
                    'TEST', 100, 0, 100, 1, 0, 99, 0, 0
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO portfolio_stress_results (
                    scenario_id, risk_report_id, portfolio_id, scenario_key,
                    policy_version, outcomes_json, is_complete,
                    scenario_exposed_stake_fen, scenario_exposure_ratio,
                    gross_payout_fen, ending_capital_fen, profit_loss_fen,
                    capital_recovery_ratio, minimum_ending_capital_fen,
                    maximum_ending_capital_fen
                ) VALUES (
                    'partial-stress', 'partial-risk', 'partial-portfolio',
                    'CASH_BASELINE', 'DETERMINISTIC_PORTFOLIO_STRESS_V2', '[]',
                    1, 0, 0, 0, 100, 0, 1, 100, 100
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO analysis_run_matches (
                    analysis_run_id, internal_match_id, market_odds_snapshot_id,
                    sporttery_bonus_snapshot_id, manual_quant_input_id,
                    context_json, context_hash
                )
                SELECT
                    'run-partial-source', internal_match_id, 'partial-source',
                    sporttery_bonus_snapshot_id, manual_quant_input_id,
                    context_json, context_hash
                FROM analysis_run_matches
                WHERE analysis_run_id = 'run-e2e-main' LIMIT 1
                """
            )
        )
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    """
                    UPDATE analysis_runs
                    SET status = 'COMPLETED', completed_at_utc = started_at_utc
                    WHERE analysis_run_id = 'run-partial-source'
                    """
                )
            )

    wrong_context = artifacts.match_contexts[0].model_copy(
        update={
            "market_odds_snapshot_id": artifacts.match_contexts[
                1
            ].market_odds_snapshot_id
        }
    )
    inconsistent = artifacts.model_copy(
        update={"match_contexts": (wrong_context, *artifacts.match_contexts[1:])}
    )
    with pytest.raises(ValueError, match="match context"):
        repository.save_analysis(inconsistent, _rules(settings))

    alternate_source = artifacts.market_odds_snapshots[0].model_copy(
        update={
            "snapshot_id": "alternate-same-match-odds",
            "source_snapshot_key": "alternate-same-match-odds",
        }
    )
    alternate_context = artifacts.match_contexts[0].model_copy(
        update={"market_odds_snapshot_id": alternate_source.snapshot_id}
    )
    same_match_wrong_version = artifacts.model_copy(
        update={
            "market_odds_snapshots": (
                *artifacts.market_odds_snapshots,
                alternate_source,
            ),
            "match_contexts": (alternate_context, *artifacts.match_contexts[1:]),
        }
    )
    with pytest.raises(ValueError, match="market prediction"):
        type(artifacts).model_validate(
            same_match_wrong_version.model_dump(mode="python")
        )

    changed_match = artifacts.matches[0].model_copy(
        update={
            "kickoff_at_utc": artifacts.matches[0].kickoff_at_utc + timedelta(hours=1)
        }
    )
    conflicting_catalog = artifacts.model_copy(
        update={"matches": (changed_match, *artifacts.matches[1:])}
    )
    with pytest.raises(ValueError, match="immutable match"):
        repository.save_analysis(conflicting_catalog, _rules(settings))

    source = artifacts.market_odds_snapshots[0]
    changed_quote = source.quotes[0].model_copy(
        update={"odds": source.quotes[0].odds + Decimal("0.01")}
    )
    changed_source = source.model_copy(
        update={"quotes": (changed_quote, *source.quotes[1:])}
    )
    changed_source = changed_source.model_copy(
        update={
            "payload_hash": payload_hash(
                changed_source.three_way_odds().model_dump(mode="json")
            )
        }
    )
    colliding_source = artifacts.model_copy(
        update={
            "market_odds_snapshots": (
                changed_source,
                *artifacts.market_odds_snapshots[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="snapshot ID collision"):
        repository.save_analysis(colliding_source, _rules(settings))

    bad_run = artifacts.analysis_run.model_copy(update={"config_hash": "not-a-hash"})
    with pytest.raises(ValueError, match="config hash"):
        repository.save_analysis(
            artifacts.model_copy(update={"analysis_run": bad_run}), _rules(settings)
        )
    bad_context = artifacts.match_contexts[0].model_copy(
        update={"context_hash": "not-a-hash"}
    )
    with pytest.raises(ValueError, match="context hash"):
        repository.save_analysis(
            artifacts.model_copy(
                update={"match_contexts": (bad_context, *artifacts.match_contexts[1:])}
            ),
            _rules(settings),
        )

    bad_risk = artifacts.portfolio_risk_reports[0].model_copy(
        update={"max_single_ticket_exposure_fen": 999_999}
    )
    with pytest.raises(ValueError, match="risk report"):
        repository.save_analysis(
            artifacts.model_copy(
                update={
                    "portfolio_risk_reports": (
                        bad_risk,
                        *artifacts.portfolio_risk_reports[1:],
                    )
                }
            ),
            _rules(settings),
        )


def test_model_quant_lineage_persists_without_manual_impersonation() -> None:
    service, _, _, dataset, settings = build_service()
    manual_artifacts = asyncio.run(
        service.run(
            request_for(
                dataset,
                "run-e2e-model-quant",
                min_selection_ev=Decimal("100"),
            )
        )
    )
    run = manual_artifacts.analysis_run
    baseline = EloThreeWayBaseline(EloBaselineConfig(minimum_prior_matches=0))
    history_kickoff = run.as_of_at_utc - timedelta(days=2)
    history_payload_hash = match_result_payload_sha256(2, 0)
    history_result = EloRegularTimeResult(
        match_result_id="model-history-result-v1",
        match_id="model-history-match",
        season_id="2026",
        home_team_id="model-history-home",
        away_team_id="model-history-away",
        kickoff_at_utc=history_kickoff,
        available_at_utc=history_kickoff + timedelta(hours=2),
        ingested_at_utc=run.as_of_at_utc - timedelta(days=1),
        home_goals=2,
        away_goals=0,
        payload_hash=history_payload_hash,
    )
    elo_state = baseline.rebuild_state(
        (history_result,),
        run.as_of_at_utc,
        target_season_id="2026",
    )
    model_state = freeze_elo_model_state(
        analysis_run_id=run.analysis_run_id,
        baseline=baseline,
        state=elo_state,
        generated_at_utc=run.started_at_utc,
    )
    evaluations = []
    model_predictions = []
    model_contexts = []
    manual_context_by_match = {
        context.match_id: context for context in manual_artifacts.match_contexts
    }
    unavailable_match_id = manual_artifacts.quant_predictions[0].match_id
    for manual_prediction in manual_artifacts.quant_predictions:
        probability_payload = manual_prediction.probabilities.model_dump(mode="json")
        is_available = manual_prediction.match_id != unavailable_match_id
        status = "AVAILABLE" if is_available else "UNAVAILABLE"
        unavailable_reason = None if is_available else "INSUFFICIENT_PRIOR_MATCHES"
        model_prediction_hash = hashlib.sha256(
            json.dumps(
                {
                    "match_id": manual_prediction.match_id,
                    "probabilities": probability_payload,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        output_json = json.dumps(
            {
                "match_id": manual_prediction.match_id,
                "prediction_hash": model_prediction_hash,
                "probabilities": probability_payload if is_available else None,
                "reason": unavailable_reason,
                "status": status,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        evaluation = QuantModelEvaluation(
            quant_model_evaluation_id=stable_id(
                "quant-model-evaluation",
                run.analysis_run_id,
                manual_prediction.match_id,
            ),
            analysis_run_id=run.analysis_run_id,
            quant_model_state_id=model_state.quant_model_state_id,
            match_id=manual_prediction.match_id,
            market=manual_prediction.market,
            status=QuantModelEvaluationStatus(status),
            unavailable_reason=unavailable_reason,
            probabilities=(manual_prediction.probabilities if is_available else None),
            output_json=output_json,
            output_hash=hashlib.sha256(output_json.encode("utf-8")).hexdigest(),
            model_prediction_hash=model_prediction_hash,
            evaluated_at_utc=run.started_at_utc,
        )
        evaluations.append(evaluation)
        if is_available:
            model_predictions.append(
                ModelQuantPrediction(
                    prediction_id=manual_prediction.prediction_id,
                    analysis_run_id=run.analysis_run_id,
                    match_id=manual_prediction.match_id,
                    market=manual_prediction.market,
                    probabilities=manual_prediction.probabilities,
                    quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
                    method=model_state.model_name,
                    method_version=model_state.model_version,
                    generated_at_utc=run.started_at_utc,
                )
            )
        manual_context = manual_context_by_match[manual_prediction.match_id]
        context_json = json.dumps(
            {
                "as_of_at_utc": run.as_of_at_utc.isoformat(),
                "market_odds_snapshot_id": manual_context.market_odds_snapshot_id,
                "match_id": manual_prediction.match_id,
                "quant_model_evaluation_id": evaluation.quant_model_evaluation_id,
                "sporttery_bonus_snapshot_id": (
                    manual_context.sporttery_bonus_snapshot_id
                ),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        model_contexts.append(
            ModelAnalysisMatchContext(
                analysis_run_id=run.analysis_run_id,
                match_id=manual_prediction.match_id,
                market_odds_snapshot_id=manual_context.market_odds_snapshot_id,
                sporttery_bonus_snapshot_id=(
                    manual_context.sporttery_bonus_snapshot_id
                ),
                quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
                context_json=context_json,
                context_hash=hashlib.sha256(context_json.encode("utf-8")).hexdigest(),
            )
        )
    manifest_json = build_model_input_manifest_json(
        competitions=manual_artifacts.competitions,
        teams=manual_artifacts.teams,
        matches=manual_artifacts.matches,
        mappings=manual_artifacts.provider_mappings,
        market_snapshots=manual_artifacts.market_odds_snapshots,
        sporttery_snapshots=manual_artifacts.sporttery_bonus_snapshots,
        model_states=(model_state,),
    )
    model_run = run.model_copy(
        update={
            "input_manifest_version": MVP_INPUT_MANIFEST_V3,
            "input_manifest_json": manifest_json,
            "input_manifest_hash": hashlib.sha256(
                manifest_json.encode("utf-8")
            ).hexdigest(),
        }
    )
    model_artifacts = AnalysisArtifacts(
        competitions=manual_artifacts.competitions,
        teams=manual_artifacts.teams,
        matches=manual_artifacts.matches,
        provider_mappings=manual_artifacts.provider_mappings,
        market_odds_snapshots=manual_artifacts.market_odds_snapshots,
        sporttery_bonus_snapshots=manual_artifacts.sporttery_bonus_snapshots,
        manual_quant_inputs=(),
        analysis_run=model_run,
        match_contexts=tuple(model_contexts),
        market_predictions=manual_artifacts.market_predictions,
        quant_predictions=tuple(model_predictions),
        final_predictions=tuple(
            prediction
            for prediction in manual_artifacts.final_predictions
            if prediction.match_id != unavailable_match_id
        ),
        selection_candidates=tuple(
            candidate
            for candidate in manual_artifacts.selection_candidates
            if candidate.match_id != unavailable_match_id
        ),
        ticket_candidates=manual_artifacts.ticket_candidates,
        portfolios=manual_artifacts.portfolios,
        portfolio_risk_reports=manual_artifacts.portfolio_risk_reports,
        quant_model_states=(model_state,),
        quant_model_evaluations=tuple(evaluations),
    )
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    source_time = history_kickoff.replace(tzinfo=None)
    available_time = history_result.available_at_utc.replace(tzinfo=None)
    ingested_time = history_result.ingested_at_utc.replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO competitions "
                "(competition_id, canonical_key, name, country_code) VALUES "
                "('model-history-competition', 'model-history-competition', "
                "'Model History', 'TST')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO teams (team_id, canonical_key, name, team_type) "
                "VALUES ('model-history-home', 'model-history-home', "
                "'History Home', 'CLUB'), "
                "('model-history-away', 'model-history-away', "
                "'History Away', 'CLUB')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO providers (provider_id, code, name, provider_kind) "
                "VALUES ('model-history-provider', 'MODEL_HISTORY', "
                "'Model History', 'FIXTURE')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO matches (internal_match_id, competition_id, "
                "home_team_id, away_team_id, kickoff_at_utc, status, "
                "available_at_utc, created_at_utc) VALUES "
                "('model-history-match', 'model-history-competition', "
                "'model-history-home', 'model-history-away', :kickoff, "
                "'FINISHED', :kickoff, :kickoff)"
            ),
            {"kickoff": source_time},
        )
        connection.execute(
            text(
                "INSERT INTO provider_match_mappings (mapping_id, provider_id, "
                "external_namespace, external_match_id, internal_match_id, "
                "resolution_method, confidence, available_at_utc, "
                "supersedes_mapping_id) VALUES ('model-history-mapping', "
                "'model-history-provider', 'MODEL_HISTORY', 'history-1', "
                "'model-history-match', 'EXACT_ID', 1, :kickoff, NULL)"
            ),
            {"kickoff": source_time},
        )
        connection.execute(
            text(
                "INSERT INTO match_results (match_result_id, internal_match_id, "
                "provider_id, provider_mapping_id, home_goals, away_goals, "
                "observed_at_utc, available_at_utc, ingested_at_utc, "
                "source_result_key, payload_hash, supersedes_match_result_id) "
                "VALUES ('model-history-result-v1', 'model-history-match', "
                "'model-history-provider', 'model-history-mapping', 2, 0, "
                ":available, :available, :ingested, 'history-result-v1', "
                ":payload_hash, NULL)"
            ),
            {
                "available": available_time,
                "ingested": ingested_time,
                "payload_hash": history_payload_hash,
            },
        )
    model_repository = SqlAlchemyAnalysisRepository(create_session_factory(engine))

    model_repository.save_analysis(model_artifacts, _rules(settings))
    model_repository.save_analysis(model_artifacts, _rules(settings))

    assert model_repository.quant_model_table_counts() == {
        "quant_model_states": 1,
        "quant_model_training_facts": 1,
        "quant_model_evaluations": 6,
    }
    assert model_repository.table_counts()["manual_quant_inputs"] == 0
    assert model_repository.table_counts()["quant_predictions"] == 5
    assert (
        model_repository.load_quant_model_state(model_state.quant_model_state_id)
        == model_state
    )
    assert (
        model_repository.load_quant_model_evaluation(
            evaluations[0].quant_model_evaluation_id
        )
        == evaluations[0]
    )
    assert evaluations[0].status is QuantModelEvaluationStatus.UNAVAILABLE
    review_repository = SqlAlchemyReviewArtifactRepository(
        create_session_factory(engine)
    )
    packet_service = ExportAnalysisPacketService(review_repository)
    for legacy_version in ("ANALYSIS_PACKET_V1", "ANALYSIS_PACKET_V2"):
        with pytest.raises(ValueError) as error:
            packet_service.export(run.analysis_run_id, legacy_version)
        assert str(error.value) == (
            "ANALYSIS_PACKET_V1/V2 supports manual P_quant lineage only"
        )
    packet, packet_json = packet_service.export(
        run.analysis_run_id,
        "ANALYSIS_PACKET_V3",
    )
    repeated_packet, repeated_json = packet_service.export(
        run.analysis_run_id,
        "ANALYSIS_PACKET_V3",
    )
    assert repeated_packet == packet
    assert repeated_json == packet_json
    assert len(packet.quant_model_states) == 1
    packet_state = packet.quant_model_states[0]
    assert packet_state.quant_model_state_id == model_state.quant_model_state_id
    assert packet_state.state_hash == model_state.state_hash
    assert packet_state.state_payload_hash == model_state.state_payload_hash
    assert packet_state.training_fact_count == len(model_state.training_facts)
    assert packet_state.training_match_ids == tuple(
        fact.match_id for fact in model_state.training_facts
    )
    assert packet_state.training_result_ids == tuple(
        fact.match_result_id for fact in model_state.training_facts
    )
    assert '"state_json"' not in packet_json
    assert '"config_json"' not in packet_json
    assert '"output_json"' not in packet_json
    packet_lineages = tuple(match.p_quant for match in packet.matches)
    assert all(lineage.source_kind == "MODEL" for lineage in packet_lineages)
    assert sum(lineage.prediction is None for lineage in packet_lineages) == 1
    assert sum(lineage.prediction is not None for lineage in packet_lineages) == 5
    review_payload = {
        "schema_version": "LLM_REVIEW_V3",
        "analysis_run_id": run.analysis_run_id,
        "packet_id": packet.packet_id,
        "packet_hash": packet.packet_hash,
        "match_reviews": [
            (
                {
                    "status": "UNAVAILABLE",
                    "match_id": match.match_id,
                    "market_key": match.market_key,
                    "failure_code": "MODEL_UNAVAILABLE",
                    "limitations": ["Model P_quant is unavailable."],
                    "review_context_id": match.review_context_id,
                    "review_context_hash": match.review_context_hash,
                }
                if match.p_quant.prediction is None
                else {
                    "status": "VALID",
                    "match_id": match.match_id,
                    "market_key": match.market_key,
                    "p_llm": match.p_quant.prediction.probabilities,
                    "assessment_confidence": "0.5",
                    "scenarios": [],
                    "preferred_outcomes": [],
                    "avoid_outcomes": [],
                    "counter_scenarios": [],
                    "risk_tags": [],
                    "reasoning_summary": "Review of frozen model lineage.",
                    "limitations": [],
                    "review_context_id": match.review_context_id,
                    "review_context_hash": match.review_context_hash,
                }
            )
            for match in packet.matches
        ],
    }
    review_json = canonical_json(review_payload)
    _, review_submission, _ = validate_review_files(
        packet_json.encode("utf-8"),
        review_json.encode("utf-8"),
    )
    assert (
        sum(item.status == "UNAVAILABLE" for item in review_submission.match_reviews)
        == 1
    )
    review_artifact = ImportLLMReviewService(review_repository).import_review(
        packet_json.encode("utf-8"),
        review_json.encode("utf-8"),
    )
    fusion_run = CreateFusionRunService(
        SqlAlchemyPostReviewRepository(create_session_factory(engine)),
        settings,
    ).create(review_artifact.review_artifact_id)
    assert len(fusion_run.results) == 5
    assert unavailable_match_id not in {item.match_id for item in fusion_run.results}
    with create_session_factory(engine)() as session:
        assert (
            session.scalar(select(func.count()).select_from(AnalysisPacketRecord)) == 1
        )
    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE quant_model_training_facts SET fact_hash = :hash"),
                {"hash": "0" * 64},
            )
    engine.dispose()


def test_analysis_save_is_exactly_idempotent_and_detects_graph_conflicts() -> None:
    service, repository, sessions, dataset, settings = build_service()
    artifacts = asyncio.run(service.run(request_for(dataset, "run-exact-retry")))
    rules = _rules(settings)
    counts = _all_table_counts(sessions)

    repository.save_analysis(artifacts, rules)
    repository.save_analysis(artifacts, rules)

    assert _all_table_counts(sessions) == counts

    changed_config_json = artifacts.analysis_run.config_json + " "
    changed_run = artifacts.analysis_run.model_copy(
        update={
            "config_json": changed_config_json,
            "config_hash": hashlib.sha256(
                changed_config_json.encode("utf-8")
            ).hexdigest(),
        }
    )
    run_conflict = artifacts.model_copy(update={"analysis_run": changed_run})

    manual_input = artifacts.manual_quant_inputs[0]
    changed_input = manual_input.model_copy(
        update={
            "available_at_utc": manual_input.available_at_utc - timedelta(seconds=1)
        }
    )
    input_conflict = artifacts.model_copy(
        update={
            "manual_quant_inputs": (
                changed_input,
                *artifacts.manual_quant_inputs[1:],
            )
        }
    )

    snapshot = artifacts.market_odds_snapshots[0]
    changed_quote = snapshot.quotes[0].model_copy(
        update={"odds": snapshot.quotes[0].odds + Decimal("0.01")}
    )
    changed_snapshot = snapshot.model_copy(
        update={"quotes": (changed_quote, *snapshot.quotes[1:])}
    )
    source_conflict = artifacts.model_copy(
        update={
            "market_odds_snapshots": (
                changed_snapshot,
                *artifacts.market_odds_snapshots[1:],
            )
        }
    )

    prediction = artifacts.market_predictions[0]
    changed_prediction = prediction.model_copy(
        update={"devig_version": f"{prediction.devig_version}-changed"}
    )
    prediction_conflict = artifacts.model_copy(
        update={
            "market_predictions": (
                changed_prediction,
                *artifacts.market_predictions[1:],
            )
        }
    )

    ticket_candidate = artifacts.ticket_candidates[0]
    changed_ticket_candidate = ticket_candidate.model_copy(
        update={"expected_roi": ticket_candidate.expected_roi + Decimal("0.01")}
    )
    changed_ticket_portfolios = tuple(
        portfolio.model_copy(
            update={
                "tickets": tuple(
                    ticket.model_copy(update={"candidate": changed_ticket_candidate})
                    if ticket.candidate.ticket_candidate_id
                    == changed_ticket_candidate.ticket_candidate_id
                    else ticket
                    for ticket in portfolio.tickets
                )
            }
        )
        for portfolio in artifacts.portfolios
    )
    ticket_conflict = artifacts.model_copy(
        update={
            "ticket_candidates": (
                changed_ticket_candidate,
                *artifacts.ticket_candidates[1:],
            ),
            "portfolios": changed_ticket_portfolios,
        }
    )

    portfolio = artifacts.portfolios[0]
    changed_portfolio = portfolio.model_copy(
        update={"strategy_version": f"{portfolio.strategy_version}-changed"}
    )
    portfolio_conflict = artifacts.model_copy(
        update={"portfolios": (changed_portfolio, *artifacts.portfolios[1:])}
    )

    risk = artifacts.portfolio_risk_reports[0]
    changed_risk = risk.model_copy(
        update={"policy_version": f"{risk.policy_version}-changed"}
    )
    risk_conflict = artifacts.model_copy(
        update={
            "portfolio_risk_reports": (
                changed_risk,
                *artifacts.portfolio_risk_reports[1:],
            )
        }
    )

    for conflict in (
        run_conflict,
        input_conflict,
        source_conflict,
        prediction_conflict,
        ticket_conflict,
        portfolio_conflict,
        risk_conflict,
    ):
        with pytest.raises(ValueError):
            repository.save_analysis(conflict, rules)
    assert _all_table_counts(sessions) == counts

    selection = next(iter(artifacts.market_predictions[0].probabilities.items()))[0]
    with sessions.begin() as session:
        session.execute(
            text("DROP TRIGGER trg_market_probability_outcomes_sealed_delete")
        )
        session.execute(
            text(
                "DELETE FROM market_probability_outcomes "
                "WHERE market_probability_id = :prediction_id "
                "AND selection_key = :selection_key"
            ),
            {
                "prediction_id": artifacts.market_predictions[0].prediction_id,
                "selection_key": selection.value,
            },
        )

    with pytest.raises(
        ValueError, match="market prediction outcomes conflict: missing"
    ):
        repository.save_analysis(artifacts, rules)


def test_completion_rejects_stress_outcome_without_selection() -> None:
    service, _, sessions, dataset, _ = build_service()
    tampered = False

    def tamper_before_completion(session, flush_context, instances) -> None:
        nonlocal tampered
        del flush_context, instances
        if tampered or not any(
            isinstance(record, AnalysisRunRecord) and record.status == "COMPLETED"
            for record in session.dirty
        ):
            return
        tampered = True
        connection = session.connection()
        scenario = (
            connection.execute(
                text(
                    """
                SELECT s.scenario_id, p.budget_fen, p.unused_budget_fen,
                       SUM(t.potential_gross_payout_fen) AS gross_payout_fen
                FROM portfolio_stress_results s
                JOIN portfolios p ON p.portfolio_id = s.portfolio_id
                JOIN tickets t ON t.portfolio_id = p.portfolio_id
                WHERE s.scenario_key = 'ALL_EXPOSED_MATCHES_ADVERSE'
                GROUP BY s.scenario_id, p.budget_fen, p.unused_budget_fen
                LIMIT 1
                """
                )
            )
            .mappings()
            .one()
        )
        scenario_id = scenario["scenario_id"]
        gross_payout_fen = int(scenario["gross_payout_fen"])
        ending_capital_fen = int(scenario["unused_budget_fen"]) + gross_payout_fen
        budget_fen = int(scenario["budget_fen"])
        connection.execute(
            text(
                """
                UPDATE portfolio_stress_results
                SET outcomes_json = (
                    SELECT json_group_array(json_object(
                        'match_id', json_extract(outcome.value, '$.match_id')
                    ))
                    FROM json_each(portfolio_stress_results.outcomes_json) outcome
                )
                WHERE scenario_id = :scenario_id
                """
            ),
            {"scenario_id": scenario_id},
        )
        connection.execute(
            text(
                """
                UPDATE portfolio_stress_ticket_results
                SET result_state = 'WON',
                    gross_payout_fen = (
                        SELECT t.potential_gross_payout_fen
                        FROM tickets t
                        WHERE t.ticket_id = portfolio_stress_ticket_results.ticket_id
                    )
                WHERE scenario_id = :scenario_id
                """
            ),
            {"scenario_id": scenario_id},
        )
        connection.execute(
            text(
                """
                UPDATE portfolio_stress_results
                SET is_complete = 1,
                    scenario_exposed_stake_fen = 0,
                    scenario_exposure_ratio = 0,
                    gross_payout_fen = :gross_payout_fen,
                    ending_capital_fen = :ending_capital_fen,
                    profit_loss_fen = :profit_loss_fen,
                    capital_recovery_ratio = :capital_recovery_ratio,
                    minimum_ending_capital_fen = :ending_capital_fen,
                    maximum_ending_capital_fen = :ending_capital_fen
                WHERE scenario_id = :scenario_id
                """
            ),
            {
                "scenario_id": scenario_id,
                "gross_payout_fen": gross_payout_fen,
                "ending_capital_fen": ending_capital_fen,
                "profit_loss_fen": ending_capital_fen - budget_fen,
                "capital_recovery_ratio": ending_capital_fen / budget_fen,
            },
        )

    event.listen(sessions, "before_flush", tamper_before_completion)
    try:
        with pytest.raises(IntegrityError):
            asyncio.run(
                service.run(
                    request_for(
                        dataset,
                        "run-missing-stress-selection",
                        budgets_fen=(10_000,),
                    )
                )
            )
    finally:
        event.remove(sessions, "before_flush", tamper_before_completion)


def test_no_bet_is_persisted_without_duplicating_source_snapshots() -> None:
    service, repository, _, dataset, _ = build_service()
    asyncio.run(service.run(request_for(dataset, "run-e2e-value")))

    no_bet = asyncio.run(
        service.run(
            request_for(
                dataset,
                "run-e2e-no-bet",
                budgets_fen=(10_000,),
                min_selection_ev=Decimal("10"),
            )
        )
    )

    assert no_bet.ticket_candidates == ()
    assert len(no_bet.portfolios) == 1
    assert no_bet.portfolios[0].status == PortfolioStatus.NO_BET
    assert no_bet.portfolios[0].tickets == ()
    counts = repository.table_counts()
    assert counts["analysis_runs"] == 2
    assert counts["market_odds_snapshots"] == 6
    assert counts["sporttery_bonus_snapshots"] == 6
    assert counts["portfolios"] == 3
    assert counts["tickets"] == 8


def test_market_quant_blend_runs_through_same_pipeline() -> None:
    service, _, _, dataset, _ = build_service()

    artifacts = asyncio.run(
        service.run(
            request_for(
                dataset,
                "run-e2e-blend",
                budgets_fen=(10_000,),
                fusion_policy=FusionPolicyName.MARKET_QUANT_BLEND_V1,
            )
        )
    )

    first_final = artifacts.final_predictions[0]
    assert first_final.fusion_policy == FusionPolicyName.MARKET_QUANT_BLEND_V1
    assert first_final.market_prediction_id is not None
    assert first_final.quant_prediction_id is not None
    assert first_final.probabilities != artifacts.quant_predictions[0].probabilities


class FutureDatedMarketOddsProvider(MockMarketOddsProvider):
    async def fetch_market_odds(self, query):
        batch = await super().fetch_market_odds(query)
        future = query.as_of_at_utc + timedelta(minutes=1)
        first = batch.snapshots[0].model_copy(
            update={
                "captured_at_utc": future,
                "available_at_utc": future,
                "ingested_at_utc": future,
            }
        )
        return batch.model_copy(update={"snapshots": (first, *batch.snapshots[1:])})


class TamperedMarketOddsProvider(MockMarketOddsProvider):
    async def fetch_market_odds(self, query):
        batch = await super().fetch_market_odds(query)
        first = batch.snapshots[0]
        changed_quote = first.quotes[0].model_copy(
            update={"odds": first.quotes[0].odds + Decimal("0.01")}
        )
        changed_snapshot = first.model_copy(
            update={"quotes": (changed_quote, *first.quotes[1:])}
        )
        return batch.model_copy(
            update={"snapshots": (changed_snapshot, *batch.snapshots[1:])}
        )


class OverPrecisionMarketOddsProvider(MockMarketOddsProvider):
    async def fetch_market_odds(self, query):
        batch = await super().fetch_market_odds(query)
        first = batch.snapshots[0]
        changed_quote = first.quotes[0].model_copy(
            update={"odds": Decimal("1.8200004")}
        )
        changed_snapshot = first.model_copy(
            update={"quotes": (changed_quote, *first.quotes[1:])}
        )
        changed_snapshot = changed_snapshot.model_copy(
            update={
                "payload_hash": payload_hash(
                    changed_snapshot.three_way_odds().model_dump(mode="json")
                )
            }
        )
        return batch.model_copy(
            update={"snapshots": (changed_snapshot, *batch.snapshots[1:])}
        )


class OverPrecisionMappingProvider(MockMarketOddsProvider):
    async def fetch_market_odds(self, query):
        batch = await super().fetch_market_odds(query)
        changed_mapping = batch.mappings[0].model_copy(
            update={"confidence": Decimal("0.1234567890123")}
        )
        return batch.model_copy(
            update={"mappings": (changed_mapping, *batch.mappings[1:])}
        )


def test_use_case_rejects_provider_data_after_knowledge_cutoff() -> None:
    service, _, _, dataset, _ = build_service(
        market_provider_factory=FutureDatedMarketOddsProvider
    )

    with pytest.raises(ValueError, match="crosses the knowledge cutoff"):
        asyncio.run(service.run(request_for(dataset, "run-future-input")))


def test_use_case_recomputes_provider_payload_hash() -> None:
    service, _, _, dataset, _ = build_service(
        market_provider_factory=TamperedMarketOddsProvider
    )

    with pytest.raises(ValueError, match="payload hash"):
        asyncio.run(service.run(request_for(dataset, "run-tampered-payload")))


def test_use_case_rejects_source_values_beyond_database_precision() -> None:
    service, _, _, dataset, _ = build_service(
        market_provider_factory=OverPrecisionMarketOddsProvider
    )

    with pytest.raises(ValueError, match="decimal precision"):
        asyncio.run(service.run(request_for(dataset, "run-over-precision")))

    mapping_service, _, _, _, _ = build_service(
        market_provider_factory=OverPrecisionMappingProvider
    )
    with pytest.raises(ValueError, match="decimal precision"):
        asyncio.run(
            mapping_service.run(request_for(dataset, "run-over-precision-mapping"))
        )


def test_manual_probability_change_changes_input_manifest() -> None:
    base_dataset = MockDataset.from_json("data/fixtures/mvp_matches.json")
    base_service, _, _, _, _ = build_service(base_dataset)
    base = asyncio.run(base_service.run(request_for(base_dataset, "run-manifest-base")))
    first_seed = base_dataset.matches[0]
    changed_probability = first_seed.manual_quant.model_copy(
        update={
            "home_win": Decimal("0.59"),
            "draw": Decimal("0.25"),
        }
    )
    changed_seed = first_seed.model_copy(update={"manual_quant": changed_probability})
    changed_dataset = base_dataset.model_copy(
        update={"matches": (changed_seed, *base_dataset.matches[1:])}
    )
    changed_service, _, _, _, _ = build_service(changed_dataset)
    changed = asyncio.run(
        changed_service.run(request_for(changed_dataset, "run-manifest-changed"))
    )

    assert (
        base.manual_quant_inputs[0].input_id != changed.manual_quant_inputs[0].input_id
    )
    assert (
        base.analysis_run.input_manifest_hash
        != changed.analysis_run.input_manifest_hash
    )

    reversed_dataset = base_dataset.model_copy(
        update={"matches": tuple(reversed(base_dataset.matches))}
    )
    reversed_service, _, _, _, _ = build_service(reversed_dataset)
    reordered = asyncio.run(
        reversed_service.run(request_for(reversed_dataset, "run-manifest-reordered"))
    )
    assert (
        base.analysis_run.input_manifest_hash
        == reordered.analysis_run.input_manifest_hash
    )

    kickoff_seed = first_seed.model_copy(
        update={"kickoff_at_utc": first_seed.kickoff_at_utc + timedelta(hours=1)}
    )
    kickoff_dataset = base_dataset.model_copy(
        update={"matches": (kickoff_seed, *base_dataset.matches[1:])}
    )
    kickoff_service, _, _, _, _ = build_service(kickoff_dataset)
    kickoff_changed = asyncio.run(
        kickoff_service.run(request_for(kickoff_dataset, "run-manifest-kickoff"))
    )
    assert (
        base.analysis_run.input_manifest_hash
        != kickoff_changed.analysis_run.input_manifest_hash
    )


def _all_table_counts(sessions) -> dict[str, int]:
    with sessions() as session:
        return {
            table.name: session.scalar(select(func.count()).select_from(table)) or 0
            for table in Base.metadata.tables.values()
        }


def _rules(settings: AppSettings):
    from football_system.domain.betting import SportteryRules

    return SportteryRules(
        version=settings.sporttery.rules_version,
        base_stake_fen=settings.sporttery.base_stake_fen,
        max_multiplier=settings.sporttery.max_multiplier,
        max_ticket_stake_fen=settings.sporttery.max_ticket_stake_fen,
    )
