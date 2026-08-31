import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.config import AppSettings
from football_system.domain.betting import CandidateStatus, PortfolioStatus
from football_system.domain.prediction import FusionPolicyName
from football_system.infrastructure.database.models import (
    AnalysisRunRecord,
    BetCandidateRecord,
    FinalPredictionOutcomeRecord,
    MarketProbabilityOutcomeRecord,
    TicketCandidateRecord,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.providers.mock.dataset import MockDataset, payload_hash
from football_system.infrastructure.providers.mock.fixtures import MockFixtureProvider
from football_system.infrastructure.providers.mock.manual_quant import MockManualQuantProvider
from football_system.infrastructure.providers.mock.market_odds import MockMarketOddsProvider
from football_system.infrastructure.providers.mock.sporttery import MockSportteryProvider

EXECUTION_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def build_service(
    dataset: MockDataset | None = None,
    market_provider_factory=MockMarketOddsProvider,
):
    settings = AppSettings.from_toml("config/mvp.toml")
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
    assert sum(
        candidate.status == CandidateStatus.ELIGIBLE
        for candidate in artifacts.selection_candidates
    ) == 5
    assert len(artifacts.ticket_candidates) == 10
    assert [portfolio.budget_fen for portfolio in artifacts.portfolios] == [10_000, 20_000]
    assert all(portfolio.status == PortfolioStatus.RECOMMENDED for portfolio in artifacts.portfolios)
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
        "tickets": 8,
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
        "DELETE FROM ticket_legs",
        "UPDATE matches SET status = 'MUTATED'",
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
        session.execute(
            text(
                """
                UPDATE analysis_runs
                SET status = 'COMPLETED', completed_at_utc = started_at_utc
                WHERE analysis_run_id = 'run-partial-source'
                """
            )
        )
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO market_odds_quotes (snapshot_id, selection_key, odds)
                    VALUES ('partial-source', 'AWAY_WIN', 4)
                    """
                )
            )

    wrong_context = artifacts.match_contexts[0].model_copy(
        update={
            "market_odds_snapshot_id": artifacts.match_contexts[1].market_odds_snapshot_id
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
        update={"kickoff_at_utc": artifacts.matches[0].kickoff_at_utc + timedelta(hours=1)}
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
    base = asyncio.run(
        base_service.run(request_for(base_dataset, "run-manifest-base"))
    )
    first_seed = base_dataset.matches[0]
    changed_probability = first_seed.manual_quant.model_copy(
        update={
            "home_win": Decimal("0.59"),
            "draw": Decimal("0.25"),
        }
    )
    changed_seed = first_seed.model_copy(
        update={"manual_quant": changed_probability}
    )
    changed_dataset = base_dataset.model_copy(
        update={"matches": (changed_seed, *base_dataset.matches[1:])}
    )
    changed_service, _, _, _, _ = build_service(changed_dataset)
    changed = asyncio.run(
        changed_service.run(request_for(changed_dataset, "run-manifest-changed"))
    )

    assert (
        base.manual_quant_inputs[0].input_id
        != changed.manual_quant_inputs[0].input_id
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


def _rules(settings: AppSettings):
    from football_system.domain.betting import SportteryRules

    return SportteryRules(
        version=settings.sporttery.rules_version,
        base_stake_fen=settings.sporttery.base_stake_fen,
        max_multiplier=settings.sporttery.max_multiplier,
        max_ticket_stake_fen=settings.sporttery.max_ticket_stake_fen,
    )
