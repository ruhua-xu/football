import asyncio
from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from football_system.application.post_review import (
    CreateFusionRunService,
    CreatePortfolioRevisionService,
)
from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
    canonical_json,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.config import AppSettings
from football_system.domain.prediction import FusionPolicyName
from football_system.infrastructure.database.post_review_repositories import (
    SqlAlchemyPostReviewRepository,
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
from football_system.infrastructure.providers.mock.dataset import MockDataset
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


EXECUTION_TIME = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def test_post_review_repository_round_trip_and_database_guards() -> None:
    settings = AppSettings.from_toml("config/mvp.toml")
    dataset = MockDataset.from_json(settings.mock.fixture_path)
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    analysis_repository = SqlAlchemyAnalysisRepository(sessions)
    artifacts_a = _run_analysis(
        analysis_repository,
        settings,
        dataset,
        "run-post-review-a",
    )
    artifacts_b = _run_analysis(
        analysis_repository,
        settings,
        dataset,
        "run-post-review-b",
    )

    review_repository = SqlAlchemyReviewArtifactRepository(sessions)
    packet, packet_json = ExportAnalysisPacketService(review_repository).export(
        artifacts_a.analysis_run.analysis_run_id
    )
    review_json = canonical_json(
        {
            "schema_version": "LLM_REVIEW_V1",
            "analysis_run_id": packet.analysis_run.analysis_run_id,
            "packet_id": packet.packet_id,
            "packet_hash": packet.packet_hash,
            "match_reviews": [
                {
                    "status": "VALID",
                    "match_id": match.match_id,
                    "market_key": match.market_key,
                    "p_llm": match.p_quant.probabilities,
                    "assessment_confidence": "0.5",
                    "scenarios": [],
                    "preferred_outcomes": [],
                    "avoid_outcomes": [],
                    "counter_scenarios": [],
                    "risk_tags": [],
                    "reasoning_summary": "Stored integration review.",
                    "limitations": [],
                }
                for match in packet.matches
            ],
        }
    )
    artifact = ImportLLMReviewService(review_repository).import_review(
        packet_json.encode("utf-8"),
        review_json.encode("utf-8"),
    )
    repository = SqlAlchemyPostReviewRepository(sessions)

    source = repository.load_fusion_source(artifact.review_artifact_id)
    assert source.artifact == artifact
    assert source.packet.packet_json == packet_json
    assert source.base_predictions == tuple(
        sorted(artifacts_a.final_predictions, key=lambda item: item.match_id)
    )

    fusion = CreateFusionRunService(repository, settings).create(
        artifact.review_artifact_id
    )
    assert repository.find_fusion_run(fusion.fusion_run_id) == fusion
    assert repository.save_fusion_run(fusion) == fusion
    with pytest.raises(ValueError, match="conflicts with stored data"):
        repository.save_fusion_run(
            fusion.model_copy(update={"fusion_version": "conflicting-version"})
        )

    revision_source = repository.load_portfolio_revision_source(fusion.fusion_run_id)
    assert revision_source.sporttery_bonus_snapshots == tuple(
        sorted(artifacts_a.sporttery_bonus_snapshots, key=lambda item: item.match_id)
    )
    assert revision_source.budgets_fen == tuple(
        sorted(portfolio.budget_fen for portfolio in artifacts_a.portfolios)
    )
    revision = CreatePortfolioRevisionService(repository, settings).create(
        fusion.fusion_run_id
    )
    assert repository.find_portfolio_revision(revision.portfolio_revision_id) == (
        revision
    )
    assert repository.save_portfolio_revision(revision) == revision
    with pytest.raises(ValueError):
        repository.save_portfolio_revision(
            revision.model_copy(update={"revision_version": "conflicting-version"})
        )

    _assert_cross_run_lineage_guards(
        sessions,
        artifacts_a,
        artifacts_b,
        artifact.review_artifact_id,
        fusion.fusion_run_id,
    )
    _assert_append_only_guards(
        sessions,
        fusion.fusion_run_id,
        revision.portfolio_revision_id,
    )


def _run_analysis(repository, settings, dataset, run_id):
    service = RunAnalysisService(
        fixture_provider=MockFixtureProvider(dataset),
        market_odds_provider=MockMarketOddsProvider(dataset),
        sporttery_provider=MockSportteryProvider(dataset),
        manual_quant_provider=MockManualQuantProvider(dataset),
        repository=repository,
        settings=settings,
    )
    return asyncio.run(
        service.run(
            RunAnalysisRequest(
                as_of_at_utc=dataset.as_of_at_utc,
                kickoff_from_utc=dataset.as_of_at_utc,
                kickoff_to_utc=dataset.as_of_at_utc + timedelta(days=2),
                budgets_fen=(10_000, 20_000),
                fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
                analysis_run_id=run_id,
                execution_time_utc=EXECUTION_TIME,
            )
        )
    )


def _assert_cross_run_lineage_guards(
    sessions,
    artifacts_a,
    artifacts_b,
    review_artifact_id: str,
    fusion_run_id: str,
) -> None:
    config_json = "{}"
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    now = EXECUTION_TIME.isoformat()
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO fusion_runs (
                        fusion_run_id, parent_analysis_run_id,
                        llm_review_artifact_id, fusion_policy, fusion_version,
                        config_json, config_hash, created_at_utc
                    ) VALUES (
                        'cross-run-fusion', :parent_run_id, :review_artifact_id,
                        'CROSS_RUN_TEST', '1', :config_json, :config_hash, :now
                    )
                    """
                ),
                {
                    "parent_run_id": artifacts_b.analysis_run.analysis_run_id,
                    "review_artifact_id": review_artifact_id,
                    "config_json": config_json,
                    "config_hash": config_hash,
                    "now": now,
                },
            )

    with sessions.begin() as session:
        session.execute(
            text(
                """
                INSERT INTO fusion_runs (
                    fusion_run_id, parent_analysis_run_id,
                    llm_review_artifact_id, fusion_policy, fusion_version,
                    config_json, config_hash, created_at_utc
                ) VALUES (
                    'fusion-for-result-lineage', :parent_run_id,
                    :review_artifact_id, 'RESULT_LINEAGE_TEST', '1',
                    :config_json, :config_hash, :now
                )
                """
            ),
            {
                "parent_run_id": artifacts_a.analysis_run.analysis_run_id,
                "review_artifact_id": review_artifact_id,
                "config_json": config_json,
                "config_hash": config_hash,
                "now": now,
            },
        )
    base_b_by_match = {
        item.match_id: item.prediction_id for item in artifacts_b.final_predictions
    }
    first_a = min(artifacts_a.final_predictions, key=lambda item: item.match_id)
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO fusion_run_results (
                        fusion_result_id, fusion_run_id, internal_match_id,
                        market_key, market_type, handicap_value,
                        base_prediction_id, p_base_json, p_llm_json,
                        raw_probability_delta_json,
                        applied_probability_delta_json, confidence_factor,
                        data_quality_factor, p_final_json, fallback_code,
                        result_json, result_hash
                    )
                    SELECT
                        'cross-run-result', 'fusion-for-result-lineage',
                        internal_match_id, market_key, market_type,
                        handicap_value, :base_prediction_id, p_base_json,
                        p_llm_json, raw_probability_delta_json,
                        applied_probability_delta_json, confidence_factor,
                        data_quality_factor, p_final_json, fallback_code,
                        result_json, result_hash
                    FROM fusion_run_results
                    WHERE fusion_run_id = :fusion_run_id
                      AND internal_match_id = :match_id
                    """
                ),
                {
                    "base_prediction_id": base_b_by_match[first_a.match_id],
                    "fusion_run_id": fusion_run_id,
                    "match_id": first_a.match_id,
                },
            )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO portfolio_revisions (
                        portfolio_revision_id, parent_analysis_run_id,
                        fusion_run_id, revision_policy, revision_version,
                        generated_at_utc, config_json, config_hash,
                        revision_json, revision_hash
                    ) VALUES (
                        'cross-run-revision', :parent_run_id, :fusion_run_id,
                        'CROSS_RUN_TEST', '1', :now, :config_json,
                        :config_hash, '{}', :config_hash
                    )
                    """
                ),
                {
                    "parent_run_id": artifacts_b.analysis_run.analysis_run_id,
                    "fusion_run_id": fusion_run_id,
                    "now": now,
                    "config_json": config_json,
                    "config_hash": config_hash,
                },
            )


def _assert_append_only_guards(
    sessions,
    fusion_run_id: str,
    revision_id: str,
) -> None:
    statements = (
        (
            "UPDATE fusion_runs SET fusion_version = 'tampered' "
            "WHERE fusion_run_id = :fusion_run_id",
            {"fusion_run_id": fusion_run_id},
        ),
        (
            "DELETE FROM fusion_runs WHERE fusion_run_id = :fusion_run_id",
            {"fusion_run_id": fusion_run_id},
        ),
        (
            """
            INSERT OR REPLACE INTO fusion_runs (
                fusion_run_id, parent_analysis_run_id, llm_review_artifact_id,
                fusion_policy, fusion_version, config_json, config_hash,
                created_at_utc
            )
            SELECT fusion_run_id, parent_analysis_run_id,
                   llm_review_artifact_id, fusion_policy, 'tampered',
                   config_json, config_hash, created_at_utc
            FROM fusion_runs WHERE fusion_run_id = :fusion_run_id
            """,
            {"fusion_run_id": fusion_run_id},
        ),
        (
            "UPDATE fusion_run_results SET result_json = 'tampered' "
            "WHERE fusion_run_id = :fusion_run_id",
            {"fusion_run_id": fusion_run_id},
        ),
        (
            "DELETE FROM fusion_run_results WHERE fusion_run_id = :fusion_run_id",
            {"fusion_run_id": fusion_run_id},
        ),
        (
            """
            INSERT OR REPLACE INTO fusion_run_results (
                fusion_result_id, fusion_run_id, internal_match_id,
                market_key, market_type, handicap_value, base_prediction_id,
                p_base_json, p_llm_json, raw_probability_delta_json,
                applied_probability_delta_json, confidence_factor,
                data_quality_factor, p_final_json, fallback_code,
                result_json, result_hash
            )
            SELECT fusion_result_id, fusion_run_id, internal_match_id,
                   market_key, market_type, handicap_value, base_prediction_id,
                   p_base_json, p_llm_json, raw_probability_delta_json,
                   applied_probability_delta_json, confidence_factor,
                   data_quality_factor, p_final_json, fallback_code,
                   'tampered', result_hash
            FROM fusion_run_results WHERE fusion_run_id = :fusion_run_id
            LIMIT 1
            """,
            {"fusion_run_id": fusion_run_id},
        ),
        (
            "UPDATE portfolio_revisions SET revision_json = 'tampered' "
            "WHERE portfolio_revision_id = :revision_id",
            {"revision_id": revision_id},
        ),
        (
            "DELETE FROM portfolio_revisions "
            "WHERE portfolio_revision_id = :revision_id",
            {"revision_id": revision_id},
        ),
        (
            """
            INSERT OR REPLACE INTO portfolio_revisions (
                portfolio_revision_id, parent_analysis_run_id, fusion_run_id,
                revision_policy, revision_version, generated_at_utc,
                config_json, config_hash, revision_json, revision_hash
            )
            SELECT portfolio_revision_id, parent_analysis_run_id,
                   fusion_run_id, revision_policy, revision_version,
                   generated_at_utc, config_json, config_hash,
                   'tampered', revision_hash
            FROM portfolio_revisions
            WHERE portfolio_revision_id = :revision_id
            """,
            {"revision_id": revision_id},
        ),
    )
    for statement, parameters in statements:
        with pytest.raises(IntegrityError):
            with sessions.begin() as session:
                session.execute(text(statement), parameters)
