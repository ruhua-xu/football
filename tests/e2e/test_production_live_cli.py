"""Pinned live CLI wiring using controlled SYNTHETIC_CONTRACT_TEST_ONLY fixtures.

The existing integration fixture seeds test approval rows and a labeled pilot
bridge. No official production activation, provider call or real review occurs.
Actual inference/transaction gates are also covered by test_production_inference.
"""

import json
from datetime import timedelta

import pytest

from football_system.application import model_analysis
from football_system.application.model_analysis import PreparedFixtureObservationRef
from football_system.interfaces import cli as entry
from football_system.interfaces import production_quant_cli as quant
from tests.e2e import test_production_quant_cli as cli_tests
from tests.integration import test_production_inference as fixtures

lane = cli_tests.lane
offline = cli_tests.offline
inference = fixtures.inference
ROOT = cli_tests.ROOT
SECRET = cli_tests.SECRET


def arguments(context):
    pins = cli_tests.write_request(
        context.lane.root,
        quant.AuthorityPinsV1(
            trusted_authorities=context.lane.repo.evidence.trusted_authorities
        ),
        "live-pins.json",
    )
    return [
        "live",
        "run-analysis",
        "--config",
        str(ROOT / "config" / "live.toml"),
        "--database-url",
        f"sqlite:///{(context.lane.root / 'lane.db').as_posix()}",
        "--preparation-id",
        context.bundle.preparation.preparation_id,
        "--budget",
        "100",
        "--analysis-run-id",
        "production-cli-analysis",
        "--production-model-release-id",
        context.release.artifact_id,
        "--production-target-acceptance-plan-id",
        context.plan.artifact_id,
        "--evidence-root",
        str(context.lane.root),
        "--authority-pins",
        str(pins),
        "--operator",
        "operator",
    ]


@pytest.fixture
def live_cli(inference, monkeypatch):
    context = inference
    context.lane.clock.value = fixtures.CREATED
    monkeypatch.setattr(entry, "utc_now", context.lane.clock)
    monkeypatch.setattr(model_analysis, "utc_now", context.lane.clock)
    # The test bridge is the only synthetic port; keep the actual inference repositories.
    monkeypatch.setattr(
        quant,
        "SqlAlchemyQuantIntegrityRepository",
        lambda *args, **kwargs: context.production.bridge,
    )

    def open_existing(database_url, *, clock):
        assert database_url == f"sqlite:///{(context.lane.root / 'lane.db').as_posix()}"
        assert clock is context.lane.clock
        return context.lane.sessions, None, context.live

    def no_research(*args, **kwargs):
        raise AssertionError(
            "pinned live CLI tried to construct a training/history fallback"
        )

    monkeypatch.setattr(entry, "_open_live_repositories", open_existing)
    monkeypatch.setattr(entry, "NoAvailableLiveTrainingHistoryProvider", no_research)
    monkeypatch.setattr(entry, "SqlAlchemyHistoricalRepository", no_research)
    return context


def test_live_pin_help_does_not_open_database_or_evidence(
    tmp_path, monkeypatch, capsys
):
    calls = cli_tests.no_database_calls(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        entry,
        "_open_live_repositories",
        lambda *args, **kwargs: pytest.fail("help opened database"),
    )
    monkeypatch.setattr(
        quant, "_read_json", lambda *args: pytest.fail("help read evidence")
    )
    with pytest.raises(SystemExit) as error:
        entry.main(["live", "run-analysis", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    for option in (
        "--production-model-release-id",
        "--production-target-acceptance-plan-id",
        "--evidence-root",
        "--authority-pins",
        "--operator",
    ):
        assert option in output
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "missing",
    [
        "--production-model-release-id",
        "--production-target-acceptance-plan-id",
        "--evidence-root",
        "--authority-pins",
        "--operator",
    ],
)
def test_live_pin_options_are_all_or_none_before_database(
    tmp_path, monkeypatch, capsys, missing
):
    options = {
        "--production-model-release-id": "test-release",
        "--production-target-acceptance-plan-id": "test-plan",
        "--evidence-root": str(tmp_path),
        "--authority-pins": str(tmp_path / "pins.json"),
        "--operator": "operator",
    }
    del options[missing]
    calls = cli_tests.no_database_calls(monkeypatch)
    monkeypatch.setattr(
        entry,
        "_open_live_repositories",
        lambda *args, **kwargs: pytest.fail("partial pins opened database"),
    )
    monkeypatch.setattr(
        quant, "_read_json", lambda *args: pytest.fail("partial pins read evidence")
    )
    with pytest.raises(SystemExit) as error:
        entry.main(
            [
                "live",
                "run-analysis",
                "--preparation-id",
                "test-preparation",
                "--budget",
                "100",
                *(value for pair in options.items() for value in pair),
            ]
        )
    assert error.value.code == 2
    assert "requires both release/target-plan IDs" in capsys.readouterr().err
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_live_malformed_authority_pins_rejected_before_database(
    lane, monkeypatch, capsys
):
    pins = lane.root / "invalid-pins.json"
    args = [
        "live",
        "run-analysis",
        "--config",
        str(ROOT / "config" / "live.toml"),
        "--database-url",
        f"sqlite:///{(lane.root / 'absent.db').as_posix()}",
        "--preparation-id",
        "test-preparation",
        "--budget",
        "100",
        "--production-model-release-id",
        "test-release",
        "--production-target-acceptance-plan-id",
        "test-plan",
        "--evidence-root",
        str(lane.root),
        "--authority-pins",
        str(pins),
        "--operator",
        "operator",
    ]
    calls = cli_tests.no_database_calls(monkeypatch)
    monkeypatch.setattr(
        entry,
        "_open_live_repositories",
        lambda *args, **kwargs: pytest.fail("invalid pins opened database"),
    )
    for payload in (
        b'{"x":NaN}',
        b'{"x":1,"x":2}',
        quant.canonical_json(
            {"trusted_authorities": {"authority.json": "0" * 64}}
        ).encode(),
        ('{"' + SECRET + '":true}').encode(),
    ):
        pins.write_bytes(payload)
        with pytest.raises(SystemExit) as error:
            entry.main(args)
        assert error.value.code == 2
        output = capsys.readouterr()
        assert "pinned production inference rejected" in output.err
        assert SECRET not in output.out + output.err
    assert calls == []
    assert not (lane.root / "absent.db").exists()


def test_live_cli_wires_exact_release_plan_and_preparation_without_training_provider(
    live_cli, monkeypatch, capsys
):
    context = live_cli
    observed = []
    contexts = []
    factory = quant.production_inference_context

    def record_context(sessions, **kwargs):
        assert sessions is context.lane.sessions
        assert kwargs["clock"] is context.lane.clock
        assert kwargs["operator_id"] == "operator"
        assert (
            kwargs["evidence"].trusted_authorities
            == context.lane.repo.evidence.trusted_authorities
        )
        result = factory(sessions, **kwargs)
        assert all(
            item._clock is context.lane.clock for item in (result[0], *result[2:])
        )
        contexts.append(result)
        return result

    class ObservedService(model_analysis.RunModelAnalysisService):
        def __init__(self, **kwargs):
            assert kwargs["training_history_provider"] is None
            assert kwargs["production_release_repository"] is contexts[-1][3]
            super().__init__(**kwargs)
            assert self._clock is context.lane.clock
            assert set(
                self.declared_provider_runtime_provenance(production_release=True)
            ) == {"fixture", "market_odds", "sporttery"}

        async def run(self, request):
            observed.append(request)
            plan, preparation = context.plan.content_payload, context.bundle.preparation
            assert request.production_model_release_id == context.release.artifact_id
            assert (
                request.production_target_acceptance_plan_id == context.plan.artifact_id
            )
            assert (
                request.as_of_at_utc
                == plan.decision_as_of_at_utc
                == preparation.decision_as_of_at_utc
            )
            assert (
                request.kickoff_from_utc
                == plan.kickoff_window_start_at_utc
                == preparation.kickoff_from_utc
            )
            assert (
                request.kickoff_to_utc
                == plan.kickoff_window_end_at_utc
                == preparation.kickoff_to_utc
            )
            assert (
                request.expected_match_ids
                == tuple(t.match_id for t in plan.targets)
                == preparation.ready_match_ids
            )
            assert request.live_source_preparation_id == preparation.preparation_id
            assert (request.competition_id, request.season_id) == (
                context.bundle.competition_id,
                context.bundle.season_id,
            )
            assert request.prepared_fixture_observations == tuple(
                PreparedFixtureObservationRef(
                    match_id=item.match_id,
                    fixture_observation_id=item.fixture_observation_id,
                )
                for item in preparation.matches
                if item.data_quality.ready
            )
            assert request.execution_time_utc is None
            assert request.allow_partial_inputs is False
            return await super().run(request)

    monkeypatch.setattr(quant, "production_inference_context", record_context)
    monkeypatch.setattr(entry, "RunModelAnalysisService", ObservedService)
    assert entry.main(arguments(context)) == 0
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert (
        "Decision mode: LIVE_STRICT; training mode: SOURCE_TIME_RESEARCH" in output.out
    )
    assert "APPROVED_TRAINING_HISTORY" in output.out
    assert len(observed) == len(contexts) == 1
    binding = contexts[0][3].load_binding("production-cli-analysis")
    assert binding is not None
    assert fixtures.model_counts(context)["analysis_runs"] == 1
    with context.lane.engine.connect() as connection:
        from sqlalchemy import text

        stored = json.loads(
            connection.scalar(text("SELECT config_json FROM analysis_runs"))
        )
        assert "model_training" not in stored["request"]["provider_runtime_provenance"]


def test_live_cli_rejects_plan_not_matching_selected_preparation(live_cli, capsys):
    context = live_cli
    context.lane.clock.value = fixtures.PERSISTED + timedelta(minutes=1)
    other_plan, _ = fixtures.plan_and_bundle(
        context, fixtures.DECISION + timedelta(hours=1), "other-cli-plan"
    )
    context.lane.clock.value = fixtures.CREATED + timedelta(hours=1)
    args = arguments(context)
    args[args.index("--production-target-acceptance-plan-id") + 1] = (
        other_plan.artifact_id
    )
    assert (
        other_plan.content_payload.decision_as_of_at_utc
        != context.bundle.preparation.decision_as_of_at_utc
    )
    with pytest.raises(SystemExit) as error:
        entry.main(args)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "No fallback is permitted" in output.err
    assert SECRET not in output.out + output.err
    assert not any(fixtures.model_counts(context).values())
