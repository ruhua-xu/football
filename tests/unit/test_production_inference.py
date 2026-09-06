"""Synthetic port contracts only; these objects grant no production authority."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from football_system.application.environment import RuntimeProvenance
from football_system.application.model_analysis import (
    ModelAnalysisDecision,
    PreparedFixtureObservationRef,
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.ports.data_providers import FixtureBatch
from football_system.application.ports.production_inference import (
    ProductionInferenceBindingV1,
)
from football_system.config import AppSettings
from football_system.domain.match import Competition, Match, Team
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.services.elo_baseline import EloBaselineConfig
from tests.unit import test_production_release as fixtures
from tests.unit.test_model_analysis import (
    OddsProviderStub,
    SportteryProviderStub,
    RepositorySpy,
    mapping,
    model_service,
    DECISION as STRICT_DECISION,
    KICKOFF as STRICT_KICKOFF,
)

manifest = fixtures.manifest
release = fixtures.release


class Poison:
    def __getattribute__(self, name):
        raise AssertionError(f"research provider accessed: {name}")


def live(code):
    return RuntimeProvenance(
        environment="live", provider_code=code, data_mode="LIVE_STRICT"
    )


@pytest.fixture
def runner(release):
    plan = fixtures._plan(release)
    target = plan.content_payload.targets[0]
    request = RunModelAnalysisRequest(
        as_of_at_utc=fixtures.DECISION,
        kickoff_from_utc=plan.content_payload.kickoff_window_start_at_utc,
        kickoff_to_utc=plan.content_payload.kickoff_window_end_at_utc,
        budgets_fen=(10_000,),
        fusion_policy="QUANT_ONLY_V1",
        analysis_run_id="test-only-run",
        execution_time_utc=fixtures.DECISION + timedelta(days=20),
        expected_match_ids=("target",),
        competition_id="bundesliga",
        season_id="production",
        elo_config=EloBaselineConfig(),
        live_source_preparation_id="test-preparation",
        prepared_fixture_observations=(
            PreparedFixtureObservationRef(
                match_id="target", fixture_observation_id="test-observation"
            ),
        ),
        production_model_release_id=release.artifact_id,
        production_target_acceptance_plan_id=plan.artifact_id,
    )

    class Port:
        def __init__(self):
            self.calls = []
            self.revocations = ()

        def load_release(self, key):
            assert key == release.artifact_id
            return release

        def load_target_plan(self, key):
            assert key == plan.artifact_id
            return plan

        def authorization(self, key, at):
            assert key == release.artifact_id
            self.calls.append(at)
            return fixtures._current(
                release.training_manifest, at, revocations=self.revocations
            )

        def load_binding(self, run_id):
            return None

    class CurrentFixtures:
        runtime_provenance = live("FIXTURE")

        def __init__(self):
            self.match = Match(
                match_id=target.match_id,
                competition_id="bundesliga",
                home_team_id=target.home_team_id,
                away_team_id=target.away_team_id,
                kickoff_at_utc=target.kickoff_at_utc,
                available_at_utc=fixtures.DECISION - timedelta(days=2),
            )

        async def fetch_fixtures(self, query):
            return FixtureBatch(
                competitions=(
                    Competition(
                        competition_id="bundesliga",
                        canonical_key="bundesliga",
                        name="Test",
                        country_code="DE",
                    ),
                ),
                teams=tuple(
                    Team(team_id=key, canonical_key=key, name=key)
                    for key in (target.home_team_id, target.away_team_id)
                ),
                matches=(self.match,),
                mappings=(mapping("target", "FIXTURE", self.match.available_at_utc),),
            )

    class CurrentOdds(OddsProviderStub):
        runtime_provenance = live("ODDS")

        async def fetch_market_odds(self, query):
            batch = await super().fetch_market_odds(query)
            return type(batch).model_validate(
                dict(
                    snapshots=tuple(
                        item.model_copy(update={"provider_code": "ODDS"})
                        for item in batch.snapshots
                    ),
                    mappings=(mapping("target", "ODDS", query.as_of_at_utc),),
                )
            )

    class CurrentBonus(SportteryProviderStub):
        runtime_provenance = live("SPORTTERY")

        async def fetch_fixed_bonus(self, query):
            batch = await super().fetch_fixed_bonus(query)
            return type(batch).model_validate(
                dict(
                    snapshots=tuple(
                        item.model_copy(update={"provider_code": "SPORTTERY"})
                        for item in batch.snapshots
                    ),
                    mappings=(mapping("target", "SPORTTERY", query.as_of_at_utc),),
                )
            )

    port, fixture, saved = Port(), CurrentFixtures(), RepositorySpy([])
    clock = iter(
        (
            fixtures.DECISION + timedelta(seconds=1),
            fixtures.DECISION + timedelta(seconds=2),
        )
    )
    service = RunModelAnalysisService(
        fixture,
        CurrentOdds(),
        CurrentBonus(),
        Poison(),
        saved,
        AppSettings(runtime={"environment": "live"}),
        production_release_repository=port,
        clock=lambda: next(clock),
    )
    return SimpleNamespace(
        service=service,
        request=request,
        port=port,
        fixture=fixture,
        saved=saved,
        release=release,
    )


def test_release_branch_actual_clock_and_optional_training_history(runner):
    decision = asyncio.run(runner.service.run_decision(runner.request))
    run = decision.analysis_artifacts.analysis_run
    assert runner.port.calls == [run.started_at_utc, run.completed_at_utc]
    assert run.started_at_utc == fixtures.DECISION + timedelta(seconds=1)
    assert decision.training_history is None
    assert decision.analysis_artifacts.quant_model_evaluations[0].status == "AVAILABLE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("production_model_release_id", None),
        ("production_target_acceptance_plan_id", None),
        ("live_source_preparation_id", None),
        ("allow_partial_inputs", True),
    ],
)
def test_release_request_requires_paired_pins_and_complete_live_preparation(
    runner, field, value
):
    with pytest.raises(ValueError):
        RunModelAnalysisRequest.model_validate(
            runner.request.model_dump() | {field: value}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"home_team_id": "impostor"},
        {"away_team_id": "impostor"},
        {"kickoff_at_utc": fixtures.DECISION + timedelta(days=1, seconds=1)},
        {"competition_id": "impostor"},
        {"match_id": "impostor"},
    ],
)
def test_release_requires_exact_target_identity_without_kickoff_tolerance(
    runner, changes
):
    runner.fixture.match = runner.fixture.match.model_copy(update=changes)
    with pytest.raises(ValueError):
        asyncio.run(runner.service.run(runner.request))
    assert runner.saved.saved == []


@pytest.mark.parametrize(
    "provenance",
    [
        RuntimeProvenance(
            environment="research",
            provider_code="FIXTURE",
            data_mode="SOURCE_TIME_RESEARCH",
        ),
        RuntimeProvenance(
            environment="live",
            provider_code="FIXTURE",
            data_mode="SOURCE_TIME_RESEARCH",
        ),
        RuntimeProvenance(
            environment="live",
            provider_code="FIXTURE",
            data_mode="LIVE_STRICT",
            is_mock=True,
        ),
    ],
)
def test_release_requires_live_current_providers_before_research_access(
    runner, provenance
):
    runner.fixture.runtime_provenance = provenance
    with pytest.raises(
        ValueError, match="CROSS_ENVIRONMENT|RUNTIME_DATA_MODE|MOCK_PROVENANCE"
    ):
        asyncio.run(runner.service.run(runner.request))
    assert runner.port.calls == []


def test_release_cannot_fallback_when_repository_missing(runner):
    runner.service._production_release_repository = None
    with pytest.raises(ValueError, match="release repository"):
        asyncio.run(runner.service.run(runner.request))
    assert runner.saved.saved == []


def test_release_rechecks_revocation_at_actual_completion(runner):
    original = runner.service._sporttery_provider.fetch_fixed_bonus

    async def fetch(query):
        runner.port.revocations = (
            fixtures._revocation(runner.release, fixtures.INFERENCE),
        )
        return await original(query)

    runner.service._sporttery_provider.fetch_fixed_bonus = fetch
    with pytest.raises(ValueError, match="revoked"):
        asyncio.run(runner.service.run(runner.request))
    assert len(runner.port.calls) == 2
    assert runner.saved.saved == []


def test_binding_cannot_drop_known_authorization_observations(runner):
    artifacts = asyncio.run(runner.service.run(runner.request))
    binding = artifacts.production_binding
    revoked = fixtures._revocation(runner.release, fixtures.TRAINING)
    with pytest.raises(ValueError, match="disappear"):
        ProductionInferenceBindingV1.model_validate(
            binding.model_dump()
            | {
                "start_authorization": binding.start_authorization.model_copy(
                    update={"revocations": (revoked,)}
                ),
            }
        )
    with pytest.raises(ValueError, match="companion binding"):
        type(artifacts).model_validate(
            artifacts.model_dump() | {"production_binding": None}
        )


def test_strict_training_decision_still_requires_training_history():
    service, _, _ = model_service([])
    decision = asyncio.run(
        service.run_decision(
            RunModelAnalysisRequest(
                as_of_at_utc=STRICT_DECISION,
                kickoff_from_utc=STRICT_KICKOFF,
                kickoff_to_utc=STRICT_KICKOFF + timedelta(hours=1),
                budgets_fen=(10_000,),
                fusion_policy="QUANT_ONLY_V1",
                competition_id="competition-1",
                season_id="season-1",
                elo_config=service.baseline.config,
            )
        )
    )
    with pytest.raises(ValueError, match="requires training history"):
        ModelAnalysisDecision(analysis_artifacts=decision.analysis_artifacts)


@pytest.mark.parametrize(
    "damage",
    [
        "missing-market",
        "missing-final",
        "empty-base-graph",
        "extra-market",
        "extra-quant",
        "extra-final",
        "market-probabilities",
        "final-probabilities",
        "quant-only-market-reference",
        "final-fallback",
    ],
)
def test_production_artifacts_require_exact_base_prediction_graph(runner, damage):
    artifacts = asyncio.run(runner.service.run(runner.request))
    updates = {"selection_candidates": (), "ticket_candidates": ()}
    if damage in ("missing-market", "empty-base-graph"):
        updates["market_predictions"] = ()
    if damage in ("missing-final", "empty-base-graph"):
        updates["final_predictions"] = ()
    if damage.startswith("extra-"):
        field = {
            "extra-market": "market_predictions",
            "extra-quant": "quant_predictions",
            "extra-final": "final_predictions",
        }[damage]
        values = getattr(artifacts, field)
        updates[field] = (
            *values,
            values[0].model_copy(update={"prediction_id": "extra"}),
        )
    if damage in ("market-probabilities", "final-probabilities"):
        field = (
            "market_predictions" if damage.startswith("market") else "final_predictions"
        )
        prediction = getattr(artifacts, field)[0]
        probability = prediction.probabilities
        updates[field] = (
            prediction.model_copy(
                update={
                    "probabilities": probability.model_copy(
                        update={
                            "home_win": probability.home_win + Decimal("0.01"),
                            "draw": probability.draw - Decimal("0.01"),
                        }
                    )
                }
            ),
        )
    if damage == "quant-only-market-reference":
        updates["final_predictions"] = (
            artifacts.final_predictions[0].model_copy(
                update={
                    "market_prediction_id": artifacts.market_predictions[
                        0
                    ].prediction_id,
                }
            ),
        )
    if damage == "final-fallback":
        updates["final_predictions"] = (
            artifacts.final_predictions[0].model_copy(
                update={"fallback_code": "UNAPPROVED_FALLBACK"}
            ),
        )
    with pytest.raises(ValueError, match="production"):
        type(artifacts).model_validate(
            artifacts.model_copy(update=updates).model_dump()
        )


@pytest.mark.parametrize("fusion_policy", ["QUANT_ONLY_V1", "MARKET_QUANT_BLEND_V1"])
def test_complete_production_graph_preserves_existing_fusion_semantics(
    runner, fusion_policy
):
    artifacts = asyncio.run(
        runner.service.run(
            runner.request.model_copy(
                update={"fusion_policy": FusionPolicyName(fusion_policy)}
            )
        )
    )
    assert type(artifacts).model_validate(artifacts.model_dump()) == artifacts
    assert (artifacts.final_predictions[0].market_prediction_id is None) == (
        fusion_policy == "QUANT_ONLY_V1"
    )
