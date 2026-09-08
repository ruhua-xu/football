"""SYNTHETIC_CONTRACT_TEST_ONLY, not official web review or source approval.

The isolated trusted database is seeded using the existing contract fixtures.
Its pilot port is a double; no public approval bypass or production activation.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError

from football_system.application.live_sources import (
    LiveAnalysisInputPolicy,
    PrepareAnalysisRequest,
    PreparedLiveFixtureProvider,
    PreparedLiveMarketOddsProvider,
    PreparedLiveSportteryProvider,
)
from football_system.application.model_analysis import (
    PreparedFixtureObservationRef,
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.production_release import project_release_state
from football_system.application import model_analysis
from football_system.application.run_analysis import _sporttery_rules
from football_system.config import AppSettings
from football_system.domain.archive import canonical_json
from football_system.domain.production_release import ProductionTargetV1
from football_system.domain.services.elo_baseline import EloBaselineConfig
from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.live_source_repositories import (
    SqlAlchemyLiveSourceRepository,
)
from football_system.infrastructure.database.production_inference_repository import (
    SqlAlchemyProductionInferenceRepository,
)
from football_system.infrastructure.database.production_inference_schema import (
    PRODUCTION_INFERENCE_TABLES,
    production_inference_trigger_sql_v1,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.session import create_database_engine
from tests.integration import test_production_quant_persistence as production_fixtures
from tests.integration import test_training_admission_persistence as admission_fixtures
from tests.integration.test_fixture_ingestion_persistence import _capture, KICKOFF
from tests.integration.test_live_source_persistence import (
    _market_capture,
    _sporttery_capture,
    DECISION,
    CREATED,
    MARKET_RECEIVED,
    PERSISTED,
)
from tests.unit.test_model_analysis import RepositorySpy

lane = admission_fixtures.lane
SETTINGS = AppSettings(runtime={"environment": "live"})


class PoisonResearchProvider:
    @property
    def runtime_provenance(self):
        raise AssertionError("live inference touched research provider provenance")

    async def fetch_elo_training_history(self, query):
        raise AssertionError("live inference loaded training data")


@pytest.fixture
def inference(lane, monkeypatch):
    monkeypatch.setattr(
        model_analysis, "_code_revision", lambda: "package:test-only-fixed-revision"
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            production_fixtures,
            "prepare",
            lambda lane, count: admission_fixtures.prepare(lane, count=5),
        )
        production = production_fixtures.production.__wrapped__(lane, monkeypatch)
    release = production_fixtures.build(production)
    # Reuse the captured live fixture contract with the admitted canonical teams.
    capture = _capture("pinned-inference")
    replacements = {
        "competition": "league",
        "competition-key": "league",
        "GB": "TST",
        "home-key": "home",
        "away-key": "away",
        "Home FC": "home",
        "Away FC": "away",
        "2026/27": "production",
    }

    def remap(value):
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [remap(item) for item in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    capture = type(capture).model_validate(remap(capture.model_dump(mode="python")))
    SqlAlchemyMatchIdentityRepository(
        lane.sessions, clock=lambda: MARKET_RECEIVED
    ).register_fixture_ingestion(capture)
    live = SqlAlchemyLiveSourceRepository(lane.sessions, clock=lambda: PERSISTED)
    live.save_market_odds_ingestion(_market_capture())
    live.save_sporttery_ingestion(_sporttery_capture())
    lane.clock.value = PERSISTED
    repository = SqlAlchemyProductionInferenceRepository(
        lane.sessions,
        production_repository=production.repo,
        clock=lane.clock,
    )
    context = SimpleNamespace(
        label="SYNTHETIC_CONTRACT_TEST_ONLY",
        production=production,
        lane=lane,
        release=release,
        live=live,
        repository=repository,
    )
    context.plan, context.bundle = plan_and_bundle(context, DECISION, "first")
    context.request = request_for(
        context, context.plan, context.bundle, "production-analysis"
    )
    return context


def plan_and_bundle(context, cutoff, key):
    plan = context.production.repo.seal_target_plan(
        key,
        context.release.artifact_id,
        (
            ProductionTargetV1(
                match_id="match",
                home_team_id="home",
                away_team_id="away",
                kickoff_at_utc=KICKOFF,
            ),
        ),
        KICKOFF - timedelta(hours=1),
        KICKOFF + timedelta(hours=1),
        cutoff,
        "KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1",
    )
    prepared = context.live.prepare_analysis(
        PrepareAnalysisRequest(
            decision_as_of_at_utc=cutoff,
            kickoff_from_utc=plan.content_payload.kickoff_window_start_at_utc,
            kickoff_to_utc=plan.content_payload.kickoff_window_end_at_utc,
            competition_id="league",
            season_id="production",
            expected_match_ids=("match",),
            policy=LiveAnalysisInputPolicy(
                maximum_odds_age_seconds=200_000, minimum_bookmaker_count=2
            ),
        ),
        created_at_utc=cutoff + timedelta(minutes=1),
    )
    return plan, context.live.load_prepared_sources(prepared.preparation_id)


def request_for(context, plan, bundle, run_id):
    return RunModelAnalysisRequest(
        as_of_at_utc=plan.content_payload.decision_as_of_at_utc,
        kickoff_from_utc=plan.content_payload.kickoff_window_start_at_utc,
        kickoff_to_utc=plan.content_payload.kickoff_window_end_at_utc,
        budgets_fen=(10_000,),
        fusion_policy="MARKET_QUANT_BLEND_V1",
        analysis_run_id=run_id,
        execution_time_utc=DECISION + timedelta(days=30),
        expected_match_ids=("match",),
        competition_id="league",
        season_id="production",
        elo_config=EloBaselineConfig(),
        live_source_preparation_id=bundle.preparation.preparation_id,
        prepared_fixture_observations=(
            PreparedFixtureObservationRef(
                match_id="match",
                fixture_observation_id="observation-pinned-inference",
            ),
        ),
        production_model_release_id=context.release.artifact_id,
        production_target_acceptance_plan_id=plan.artifact_id,
    )


def service(context, *, bundle=None, repository=None):
    bundle = bundle or context.bundle
    return RunModelAnalysisService(
        PreparedLiveFixtureProvider(bundle),
        PreparedLiveMarketOddsProvider(bundle),
        PreparedLiveSportteryProvider(bundle),
        PoisonResearchProvider(),
        repository
        or SqlAlchemyAnalysisRepository(
            context.lane.sessions,
            production_release_repository=context.repository,
        ),
        SETTINGS,
        production_release_repository=context.repository,
        clock=context.lane.clock,
    )


def model_counts(context):
    with context.lane.engine.connect() as connection:
        return {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM {name}"))
            for name in (
                "analysis_runs",
                "quant_model_states",
                "quant_model_evaluations",
                "market_probabilities",
                "quant_predictions",
                "final_predictions",
                "portfolios",
                *PRODUCTION_INFERENCE_TABLES,
            )
        }


def test_pinned_two_cutoffs_exact_retry_and_poison_research(inference):
    other_plan, other_bundle = plan_and_bundle(
        inference, DECISION + timedelta(hours=1), "second"
    )
    inference.lane.clock.value = CREATED
    runner = service(inference)
    assert set(
        runner.declared_provider_runtime_provenance(production_release=True)
    ) == {"fixture", "market_odds", "sporttery"}
    first = asyncio.run(runner.run_decision(inference.request))
    artifacts = first.analysis_artifacts
    assert first.training_history is None
    assert artifacts.analysis_run.started_at_utc > CREATED
    assert (
        artifacts.analysis_run.completed_at_utc < inference.request.execution_time_utc
    )
    assert (
        "model_training"
        not in json.loads(artifacts.analysis_run.config_json)["request"][
            "provider_runtime_provenance"
        ]
    )
    assert artifacts.quant_model_evaluations[0].status == "AVAILABLE"
    assert (
        json.loads(artifacts.quant_model_evaluations[0].output_json)[
            "home_prior_matches"
        ]
        == 5
    )
    assert (
        artifacts.quant_predictions[0].probabilities
        != artifacts.market_predictions[0].probabilities
    )
    before = model_counts(inference)
    calls = len(inference.lane.clock.calls)
    assert asyncio.run(runner.run(inference.request)) == artifacts
    assert len(inference.lane.clock.calls) > calls
    assert model_counts(inference) == before
    assert (
        inference.repository.load_binding("production-analysis")
        == artifacts.production_binding
    )
    inference.lane.clock.value = DECISION + timedelta(hours=1, minutes=1)
    with pytest.raises(ValueError, match="retry release/target plan"):
        asyncio.run(
            service(inference, bundle=other_bundle).run(
                request_for(inference, other_plan, other_bundle, "production-analysis")
            )
        )
    second = asyncio.run(
        service(inference, bundle=other_bundle).run(
            request_for(inference, other_plan, other_bundle, "second-run")
        )
    )
    assert (
        artifacts.quant_model_states[0].state_hash
        != second.quant_model_states[0].state_hash
    )
    assert (
        artifacts.production_binding.released_state_core_hash
        == second.production_binding.released_state_core_hash
    )
    assert (
        artifacts.quant_model_states[0].training_data_hash
        == second.quant_model_states[0].training_data_hash
    )
    assert (
        artifacts.quant_predictions[0].probabilities
        == second.quant_predictions[0].probabilities
    )
    assert (
        project_release_state(
            inference.release, DECISION, ("match",), "production"
        ).teams
        == project_release_state(
            inference.release,
            other_plan.content_payload.decision_as_of_at_utc,
            ("match",),
            "production",
        ).teams
    )
    with inference.lane.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("production_model_release_id", "missing-release"),
        ("production_target_acceptance_plan_id", "missing-plan"),
        ("season_id", "wrong-season"),
        ("competition_id", "wrong-competition"),
        ("expected_match_ids", ("unplanned",)),
    ],
)
def test_wrong_pins_and_scope_never_leave_running_state(inference, field, value):
    inference.lane.clock.value = CREATED
    with pytest.raises((ValueError, KeyError)):
        asyncio.run(
            service(inference).run(inference.request.model_copy(update={field: value}))
        )
    assert not any(model_counts(inference).values())


def test_revoked_during_provider_operation_rolls_back(inference):
    inference.lane.clock.value = CREATED
    runner = service(inference)
    original = runner._market_odds_provider.fetch_market_odds

    async def revoke_then_fetch(query):
        production_fixtures.revoke(
            inference.production,
            inference.release.training_approval,
            release=inference.release,
        )
        return await original(query)

    runner._market_odds_provider.fetch_market_odds = revoke_then_fetch
    with pytest.raises(ValueError, match="revoked"):
        asyncio.run(runner.run(inference.request))
    assert not any(model_counts(inference).values())


def test_retry_does_not_bypass_current_revocation(inference):
    inference.lane.clock.value = CREATED
    runner = service(inference)
    asyncio.run(runner.run(inference.request))
    before = model_counts(inference)
    production_fixtures.revoke(
        inference.production,
        inference.release.training_approval,
        release=inference.release,
    )
    with pytest.raises(ValueError, match="revoked"):
        asyncio.run(runner.run(inference.request))
    with pytest.raises(ValueError, match="revoked"):
        inference.repository.load_binding(inference.request.analysis_run_id)
    assert model_counts(inference) == before


def test_late_commit_failure_rolls_back_both_bindings_and_model(inference, monkeypatch):
    inference.lane.clock.value = CREATED
    original = inference.repository.validate_analysis_in_session

    def late_failure(session, artifacts, *, current=False, persisted=False):
        original(session, artifacts, current=current, persisted=persisted)
        if session.scalar(
            text("SELECT COUNT(*) FROM analysis_runs WHERE status = 'COMPLETED'")
        ):
            raise ValueError("late verification failed")

    monkeypatch.setattr(
        inference.repository, "validate_analysis_in_session", late_failure
    )
    with pytest.raises(ValueError, match="late verification"):
        asyncio.run(service(inference).run(inference.request))
    assert not any(model_counts(inference).values())


def test_revocation_becomes_effective_at_final_commit_not_request_time(
    inference, monkeypatch
):
    effective = CREATED + timedelta(minutes=10)
    production_fixtures.revoke(
        inference.production,
        inference.release.training_approval,
        release=inference.release,
        effective_at=effective,
    )
    inference.lane.clock.value = CREATED
    original = inference.repository.validate_analysis_in_session

    def advance_at_commit(session, artifacts, *, current=False, persisted=False):
        if persisted:
            inference.lane.clock.value = effective
        original(session, artifacts, current=current, persisted=persisted)

    monkeypatch.setattr(
        inference.repository, "validate_analysis_in_session", advance_at_commit
    )
    with pytest.raises(ValueError, match="revoked"):
        asyncio.run(service(inference).run(inference.request))
    assert not any(model_counts(inference).values())


def test_stripping_all_production_metadata_cannot_launder_admitted_results(
    inference,
    monkeypatch,
):
    inference.lane.clock.value = CREATED
    artifacts = asyncio.run(
        service(inference, repository=RepositorySpy([])).run(inference.request)
    )
    production_fixtures.revoke(
        inference.production,
        inference.release.training_approval,
        release=inference.release,
    )
    config = json.loads(artifacts.analysis_run.config_json)
    for key in tuple(config["request"]):
        if key.startswith("production_") or key in (
            "model_training_use_class",
            "model_training_source_mode",
            "decision_data_mode",
        ):
            del config["request"][key]
    config_json = canonical_json(config)
    stripped = artifacts.model_copy(
        update={
            "production_binding": None,
            "analysis_run": artifacts.analysis_run.model_copy(
                update={
                    "config_json": config_json,
                    "config_hash": hashlib.sha256(config_json.encode()).hexdigest(),
                }
            ),
        }
    )
    repository = SqlAlchemyAnalysisRepository(inference.lane.sessions)
    with pytest.raises(ValueError, match="admitted research training results"):
        repository.save_analysis(stripped, _sporttery_rules(SETTINGS))
    assert not any(model_counts(inference).values())
    # Isolate the direct-SQL completion guard from the application source check.
    monkeypatch.setattr(
        repository, "_assert_model_training_sources", lambda *args: None
    )
    with pytest.raises(IntegrityError, match="admitted research training results"):
        repository.save_analysis(stripped, _sporttery_rules(SETTINGS))
    assert not any(model_counts(inference).values())


@pytest.mark.parametrize("sql_only", [False, True])
@pytest.mark.parametrize("damage", ["missing-market", "missing-final", "extra-market"])
def test_incomplete_prediction_write_rolls_back_and_original_retry_succeeds(
    inference,
    monkeypatch,
    sql_only,
    damage,
):
    inference.lane.clock.value = CREATED
    artifacts = asyncio.run(
        service(inference, repository=RepositorySpy([])).run(inference.request)
    )
    repository = SqlAlchemyAnalysisRepository(
        inference.lane.sessions,
        production_release_repository=inference.repository,
    )
    persist = repository._persist_predictions
    betting = repository._persist_betting
    attempted = []

    def damaged_predictions(session, original):
        attempted.append(
            session.scalar(text("SELECT COUNT(*) FROM quant_model_states"))
        )
        updates = {}
        if damage == "missing-market":
            updates = {"market_predictions": (), "final_predictions": ()}
        elif damage == "missing-final":
            updates = {"final_predictions": ()}
        persist(session, original.model_copy(update=updates))
        if damage == "extra-market":
            session.execute(
                text("""INSERT INTO market_probabilities
                SELECT 'extra-market', analysis_run_id, internal_match_id, 'EXTRA_MARKET',
                    market_type, handicap_value, devig_method, devig_version, overround, generated_at_utc
                FROM market_probabilities""")
            )

    with monkeypatch.context() as scoped:
        scoped.setattr(repository, "_persist_predictions", damaged_predictions)
        scoped.setattr(
            repository,
            "_persist_betting",
            lambda s, a: betting(s, a.model_copy(update={"selection_candidates": ()})),
        )
        if sql_only:
            scoped.setattr(repository, "_assert_run_graph_matches", lambda *args: None)
        with pytest.raises((ValueError, IntegrityError), match="prediction|market"):
            repository.save_analysis(artifacts, _sporttery_rules(SETTINGS))
    assert attempted == [1]
    assert not any(model_counts(inference).values())
    repository.save_analysis(artifacts, _sporttery_rules(SETTINGS))
    assert (
        inference.repository.load_binding(artifacts.analysis_run.analysis_run_id)
        == artifacts.production_binding
    )


def test_binding_reader_revalidates_base_final_probabilities(inference):
    inference.lane.clock.value = CREATED
    artifacts = asyncio.run(service(inference).run(inference.request))
    with inference.lane.engine.begin() as connection:
        for name in tuple(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='final_prediction_outcomes'"
                )
            )
        ):
            connection.execute(text(f"DROP TRIGGER {name}"))
        connection.execute(
            text("""UPDATE final_prediction_outcomes
            SET probability = probability + CASE selection_key
                WHEN 'HOME_WIN' THEN 0.01 WHEN 'DRAW' THEN -0.01 ELSE 0 END""")
        )
    with pytest.raises(ValueError, match="base model/fusion"):
        inference.repository.load_binding(artifacts.analysis_run.analysis_run_id)


def test_scheduled_revocation_crossed_during_final_authorization_read_rolls_back(
    inference,
    monkeypatch,
):
    effective = CREATED + timedelta(minutes=10)
    production_fixtures.revoke(
        inference.production,
        inference.release.training_approval,
        release=inference.release,
        effective_at=effective,
    )
    inference.lane.clock.value = CREATED
    authorization = inference.production.repo.authorization_in_session
    clock = inference.repository._clock
    final_read = []
    final_observation = []

    def cross_during_load(session, release_id, at):
        assert not final_read, "expensive authorization load after final observation"
        current = authorization(session, release_id, at)
        completed = session.scalar(
            text("SELECT completed_at_utc FROM analysis_runs WHERE status='COMPLETED'")
        )
        if completed is not None and at > datetime.fromisoformat(completed).replace(
            tzinfo=timezone.utc
        ):
            assert (
                current.revocations
                and current.revocations[0].content_payload.effective_at_utc == effective
            )
            final_read.append(current)
            inference.lane.clock.value = effective
        return current

    def final_clock():
        at = clock()
        if final_read:
            final_observation.append(at)
        return at

    def no_io_after_observation(*args):
        assert not final_observation, "database I/O after final clock observation"

    monkeypatch.setattr(
        inference.production.repo, "authorization_in_session", cross_during_load
    )
    monkeypatch.setattr(inference.repository, "_clock", final_clock)
    event.listen(
        inference.lane.engine, "before_cursor_execute", no_io_after_observation
    )
    try:
        with pytest.raises(ValueError, match="revoked"):
            asyncio.run(service(inference).run(inference.request))
    finally:
        event.remove(
            inference.lane.engine, "before_cursor_execute", no_io_after_observation
        )
    assert len(final_read) == len(final_observation) == 1
    assert final_read[0].actual_at_utc < effective <= final_observation[0]
    assert not any(model_counts(inference).values())


@pytest.mark.parametrize(
    "failure", ["raw-evidence", "retrospective-successor", "elo-fact-row"]
)
def test_live_operation_revalidates_retained_evidence_and_exact_sources(
    inference, failure
):
    inference.lane.clock.value = CREATED
    runner = service(inference)
    original_fetch = runner._market_odds_provider.fetch_market_odds

    async def corrupt_then_fetch(query):
        if failure == "raw-evidence":
            (inference.lane.root / "result.json").write_bytes(
                b"changed test-only evidence"
            )
        elif failure == "retrospective-successor":
            original = inference.lane.submissions[0].candidate.normalized_result
            successor = original.model_copy(
                update={
                    "match_result_id": "unadmitted-successor",
                    "source_result_key": "unadmitted-successor",
                    "supersedes_match_result_id": original.match_result_id,
                    "ingested_at_utc": original.ingested_at_utc + timedelta(seconds=1),
                }
            )
            SqlAlchemyHistoricalRepository(inference.lane.sessions).append_match_result(
                successor
            )
        else:
            with inference.lane.engine.begin() as connection:
                connection.execute(
                    text(
                        "DROP TRIGGER trg_production_quant_model_release_facts_append_only_update"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE production_quant_model_release_facts SET artifact_json = '{}' WHERE fact_sequence = 0"
                    )
                )
        return await original_fetch(query)

    runner._market_odds_provider.fetch_market_odds = corrupt_then_fetch
    with pytest.raises((ValueError, IntegrityError)):
        asyncio.run(runner.run(inference.request))
    assert not any(model_counts(inference).values())


def test_existing_release_with_another_releases_plan_is_rejected(inference):
    other = production_fixtures.build(
        inference.production, inference.release.training_approval, key="other-release"
    )
    inference.lane.clock.value = CREATED
    request = inference.request.model_copy(
        update={"production_model_release_id": other.artifact_id}
    )
    with pytest.raises(ValueError, match="plan release"):
        asyncio.run(service(inference).run(request))
    assert not any(model_counts(inference).values())


def test_binding_fk_append_only_and_sql_completion_guards(inference):
    inference.lane.clock.value = CREATED
    artifacts = asyncio.run(service(inference).run(inference.request))
    with pytest.raises(IntegrityError, match="append-only"):
        with inference.lane.engine.begin() as connection:
            connection.execute(text("DELETE FROM analysis_run_target_acceptance_plans"))
    with pytest.raises(IntegrityError, match="immutable|lineage"):
        with inference.lane.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT OR REPLACE INTO analysis_run_target_acceptance_plans SELECT * FROM analysis_run_target_acceptance_plans"
                )
            )
    # Trusted test DDL removes only the insert guard to isolate mandatory FKs.
    with inference.lane.engine.begin() as connection:
        connection.execute(
            text("DROP TRIGGER trg_analysis_run_target_acceptance_plans_lineage_insert")
        )
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with inference.lane.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO analysis_run_target_acceptance_plans VALUES ('orphan', 'missing', 'missing', 'missing')"
                )
            )
    # A direct completion with an approved marker cannot omit companions.
    with pytest.raises(IntegrityError, match="requires exact release"):
        with inference.lane.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO analysis_runs SELECT 'orphan-run', run_kind, as_of_at_utc, 'RUNNING', started_at_utc, NULL, pipeline_version, code_revision, config_json, config_hash, input_manifest_version, input_manifest_json, input_manifest_hash, replay_of_run_id FROM analysis_runs"
                )
            )
            connection.execute(
                text(
                    "UPDATE analysis_runs SET status='COMPLETED', completed_at_utc=started_at_utc WHERE analysis_run_id='orphan-run'"
                )
            )
    assert artifacts.production_binding is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(inference.lane.engine.url))
    command.stamp(config, "b1dae507c243")
    with pytest.raises(RuntimeError, match="immutable production inference"):
        command.downgrade(config, "a0c9e4f6b132")


def test_sql_completion_rejects_extra_bindings_without_approved_marker(inference):
    inference.lane.clock.value = CREATED
    asyncio.run(service(inference).run(inference.request))
    # Trusted DDL simulates corruption; completion guards remain installed.
    with inference.lane.engine.begin() as connection:
        names = tuple(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='analysis_runs'"
                )
            )
        )
        for name in names:
            if "production_completion" not in name:
                connection.execute(text(f"DROP TRIGGER {name}"))
        connection.execute(
            text("UPDATE analysis_runs SET status='RUNNING', completed_at_utc=NULL")
        )
    with pytest.raises(IntegrityError, match="requires exact release"):
        with inference.lane.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE analysis_runs SET status='COMPLETED', completed_at_utc=started_at_utc, config_json='{}'"
                )
            )


def test_missing_binding_retry_fails_closed_even_after_trusted_ddl_tamper(inference):
    inference.lane.clock.value = CREATED
    asyncio.run(service(inference).run(inference.request))
    with inference.lane.engine.begin() as connection:
        connection.execute(
            text(
                "DROP TRIGGER trg_analysis_run_target_acceptance_plans_append_only_delete"
            )
        )
        connection.execute(text("DELETE FROM analysis_run_target_acceptance_plans"))
    with pytest.raises(ValueError, match="companion rows"):
        asyncio.run(service(inference).run(inference.request))


def test_inference_migration_runtime_offline_and_empty_downgrade(tmp_path):
    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / 'inference-migration.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "c2ebf618d354")
    engine = create_database_engine(url)
    assert set(PRODUCTION_INFERENCE_TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "c2ebf618d354"
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        names = set(
            connection.scalars(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        )
        assert set(production_inference_trigger_sql_v1()) <= names
    engine.dispose()
    command.downgrade(config, "a0c9e4f6b132")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "a0c9e4f6b132:b1dae507c243", sql=True)
    assert "production_completion_update" in output.getvalue()
    with pytest.raises(RuntimeError, match="offline"):
        command.downgrade(config, "b1dae507c243:a0c9e4f6b132", sql=True)


def test_binding_parent_reuse_preserves_fresh_read_boundaries(inference, monkeypatch):
    inference.lane.clock.value = CREATED
    artifacts = asyncio.run(service(inference).run(inference.request))
    original_load = inference.lane.repo._load
    original_authorization = inference.production.repo.authorization_in_session
    scopes, authorization_times = [], []

    def load(session, admission_id):
        scopes.append(session.info["production_verified_read"])
        return original_load(session, admission_id)

    def authorization(session, release_id, at):
        authorization_times.append(at)
        return original_authorization(session, release_id, at)

    monkeypatch.setattr(inference.lane.repo, "_load", load)
    monkeypatch.setattr(
        inference.production.repo, "authorization_in_session", authorization
    )
    for attempt in range(2):
        assert (
            inference.repository.load_binding(artifacts.analysis_run.analysis_run_id)
            == artifacts.production_binding
        )
        # Historical verification, current start, and current completion are fresh.
        assert len(scopes) == 3 * (attempt + 1)
        assert len(authorization_times) == 4 * (attempt + 1)
        times = authorization_times[4 * attempt :]
        assert times[:2] == [
            artifacts.production_binding.start_authorization.actual_at_utc,
            artifacts.production_binding.completion_authorization.actual_at_utc,
        ]
        assert times[1] < times[2] < times[3]
        assert (
            "production_verified_read"
            not in inference.production.bridge.calls[-1][2].info
        )
    assert all(scopes[i] is not scopes[j] for i in range(6) for j in range(i))


def test_same_session_verification_rechecks_evidence_and_cleans_failed_scope(inference):
    inference.lane.clock.value = CREATED
    artifacts = asyncio.run(service(inference).run(inference.request))
    evidence = inference.lane.root / "result.json"
    original = evidence.read_bytes()
    with inference.lane.sessions.begin() as session:
        session.execute(text("BEGIN"))
        arguments = (
            session,
            artifacts.production_binding,
            artifacts.analysis_run,
            artifacts.quant_model_states,
            artifacts.matches,
        )
        expected = inference.repository._verify(*arguments)
        assert "production_verified_read" not in session.info
        evidence.write_bytes(b"changed SYNTHETIC_CONTRACT_TEST_ONLY evidence")
        try:
            with pytest.raises(ValueError, match="SHA-256"):
                inference.repository._verify(*arguments)
            assert "production_verified_read" not in session.info
        finally:
            evidence.write_bytes(original)
        assert inference.repository._verify(*arguments) == expected
        assert "production_verified_read" not in session.info
