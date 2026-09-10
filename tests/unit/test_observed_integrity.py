"""Pure observed integrity, not persisted source rights or production authority."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.domain.observed_training import (
    ObservedCollectionScopeAdmissionV1,
    ObservedCapturePointerV1,
    ObservedSnapshotContextV1,
    ObservedSnapshotRecordV1,
    observed_snapshot_root,
)
from football_system.domain.production_release import (
    EloTrainingWindowV1,
    EloTrainingWindowContentV1,
    TrainingSeasonV1,
    ProviderSeasonRefV1,
    ObservedFactRefV1,
    StrictWalkForwardUnavailableV1,
    revalidate,
)
from football_system.domain.quant_integrity import (
    FIXED_ELO_CONFIG_HASH,
    ObservedIntegrityProvenanceV1,
    ObservedQuantIntegrityCohortV1,
    ObservedQuantIntegrityPlanDefinitionV1,
    ObservedQuantIntegrityPlanContentV1,
    QuantIntegrityPlanDefinitionV1,
    QuantIntegrityPlanV1,
    QuantIntegrityReportV1,
    TerminalProjectionDefinitionV1,
    ModelBuildRecipePinV1,
)
from football_system.domain.training_admission import TrainingCanonicalMatchIdentityV1
from tests.unit.test_observed_training import graph as observed_graph, attest, AT

graph = observed_graph


@pytest.fixture
def definition(graph):
    scope, record = graph
    subject = record.subject.model_copy(
        update={
            "identity": record.identity.model_copy(
                update={"competition_type": "DOMESTIC_LEAGUE"}
            ),
            "inspection": record.subject.inspection.model_copy(
                update={
                    "field_evidence": {"native": {"arrays": [1, None, {"flag": True}]}}
                }
            ),
        }
    )
    record = ObservedSnapshotRecordV1.freeze(
        subject=subject,
        reviewer_attestation=attest(subject, AT + timedelta(seconds=3)),
        verified_at_utc=record.verified_at_utc,
        registered_at_utc=record.registered_at_utc,
    )
    context = ObservedSnapshotContextV1(
        scope=scope, records=(record,), actual_at_utc=AT + timedelta(seconds=10)
    )
    window = EloTrainingWindowV1.freeze(
        content_payload=EloTrainingWindowContentV1(
            competition_id="league",
            seasons=tuple(
                TrainingSeasonV1(
                    season_sequence=i,
                    season_id=season,
                    role=role,
                    provider_seasons=(
                        ProviderSeasonRefV1(
                            source_id="source",
                            provider_code="SPORTMONKS",
                            provider_competition_id="222",
                            provider_season_id=pid,
                        ),
                    ),
                )
                for i, (season, role, pid) in enumerate(
                    (
                        ("2024/25", "PILOT_TARGET", "333"),
                        ("future", "PRODUCTION_TARGET", "334"),
                    )
                )
            ),
        )
    )
    return ObservedQuantIntegrityPlanDefinitionV1(
        integrity_pilot_series_id="test-observed",
        previous_terminal_attestation=None,
        provenance=ObservedIntegrityProvenanceV1(
            evidence_use="SYNTHETIC_CONTRACT_ONLY"
        ),
        observed_context=context,
        training_window=window,
        cohort=ObservedQuantIntegrityCohortV1(
            season_id="2024/25", cohort_match_ids=("match",)
        ),
        targets=(
            TrainingCanonicalMatchIdentityV1(
                internal_match_id="future-target",
                internal_competition_id="league",
                internal_home_team_id="home",
                internal_away_team_id="away",
                kickoff_at_utc=AT + timedelta(days=1),
                season="future",
                competition_type="DOMESTIC_LEAGUE",
            ),
        ),
        terminal_projection=TerminalProjectionDefinitionV1(
            training_cutoff_at_utc=context.actual_at_utc,
            exclude_match_ids=("future-target",),
        ),
        selected_heads=(ObservedFactRefV1.of(record),),
        selected_versions_root=observed_snapshot_root((record,)),
        implementation_code_revision="CONTRACT_TEST_ONLY",
        build_recipe=ModelBuildRecipePinV1(
            recipe_id="fixed",
            recipe_hash="a" * 64,
            evidence=scope.subject.user_terms_resolution,
        ),
    )


def execute(definition):
    sealed = definition.observed_context.actual_at_utc + timedelta(seconds=1)
    at = sealed + timedelta(seconds=1)
    plan = QuantIntegrityPlanV1.freeze(
        content_payload=ObservedQuantIntegrityPlanContentV1(
            definition=definition,
            input_roots=definition.input_roots,
            sealed_at_utc=sealed,
        )
    )
    service = QuantIntegrityPilotService(
        SimpleNamespace(
            load_verified_observed_context=lambda d, at_utc: d.observed_context
        ),
        lambda: at,
    )
    output = service._execute(plan, at_utc=at)
    replay = service._execute(plan, at_utc=at)
    return plan, output, service._report(plan, output, replay)


def test_observed_fixed_replay_has_no_historical_metrics_and_preserves_native_json(
    definition,
):
    plan, output, report = execute(definition)
    assert QuantIntegrityPlanV1.model_validate_json(plan.model_dump_json()) == plan
    assert (
        QuantIntegrityReportV1.model_validate_json(report.model_dump_json()) == report
    )
    assert revalidate(definition.observed_context) == definition.observed_context
    core, r = (
        output.content_payload.terminal_state_core.content_payload,
        report.content_payload,
    )
    assert core.config_hash == FIXED_ELO_CONFIG_HASH
    assert (
        len(core.training_facts) == r.training_fact_count == r.selected_head_count == 1
    )
    assert r.strict_walk_forward == StrictWalkForwardUnavailableV1(metrics=None)
    assert (
        r.historical_model_availability
        is r.probability_metrics
        is r.availability_denominator
        is None
    )
    assert r.replay.state_hashes == () and r.replay.matched
    fact = core.training_facts[0]
    assert (
        fact.available_at_utc
        == fact.ingested_at_utc
        == definition.observed_context.records[0].registered_at_utc
    )
    assert "future-target" not in {f.match_id for f in core.training_facts}
    with pytest.raises(ValueError):
        QuantIntegrityPlanDefinitionV1.model_validate(definition.model_dump())


@pytest.mark.parametrize(
    "change", ["targets", "context", "selected", "config", "cohort", "cutoff"]
)
def test_observed_plan_fails_closed_before_computation(definition, change):
    body = definition.model_dump(mode="python")
    if change == "targets":
        body["terminal_projection"]["exclude_match_ids"] = ()
    elif change == "context":
        body["observed_context"]["records"] = ()
    elif change == "selected":
        body["selected_versions_root"] = "0" * 64
    elif change == "config":
        body["config_hash"] = "0" * 64
    elif change == "cohort":
        body["cohort"]["cohort_match_ids"] = ("hidden-target",)
    else:
        body["terminal_projection"]["training_cutoff_at_utc"] = AT
    with pytest.raises(ValueError):
        ObservedQuantIntegrityPlanDefinitionV1.model_validate(body)


@pytest.mark.parametrize(
    "field",
    [
        "probability_metrics",
        "availability_denominator",
        "calibration_observation_count",
        "historical_model_availability",
    ],
)
def test_unavailable_denominators_are_not_zero(definition, field):
    _, _, report = execute(definition)
    body = report.content_payload.model_dump(mode="python")
    body[field] = 0
    with pytest.raises(ValueError):
        type(report.content_payload).model_validate(body)


def test_terminal_replay_rejects_changed_ratings_or_training_hash(definition):
    _, output, _ = execute(definition)
    core = output.content_payload.terminal_state_core
    body = core.content_payload.model_dump(mode="python")
    body["training_data_hash"] = "0" * 64
    with pytest.raises(ValueError, match="replay"):
        type(core.content_payload).model_validate(body)


def test_same_kickoff_correction_preserves_head_order_but_replays_elo_order(definition):
    old = definition.observed_context
    scope_subject = old.scope.subject.model_copy(
        update={
            "seasons": (
                old.scope.subject.seasons[0].model_copy(
                    update={
                        "expected_fixture_ids": ("100", "101"),
                        "expected_fixture_count": 2,
                    }
                ),
            )
        }
    )
    scope = ObservedCollectionScopeAdmissionV1.freeze(
        subject=scope_subject,
        reviewer_attestation=attest(scope_subject, AT),
        recorded_at_utc=AT,
        capture_receipt_high_watermark=0,
    )
    subject = old.records[0].subject.model_copy(
        update={"scope_id": scope.scope_id, "scope_hash": scope.content_hash}
    )
    first = ObservedSnapshotRecordV1.freeze(
        subject=subject,
        reviewer_attestation=attest(subject, AT + timedelta(seconds=3)),
        verified_at_utc=AT + timedelta(seconds=4),
        registered_at_utc=AT + timedelta(seconds=5),
    )
    capture = subject.fixture_capture.model_copy(
        update={"capture_receipt_id": "second-capture", "capture_ordinal": 2}
    )
    pointer = ObservedCapturePointerV1(
        capture_receipt_id=capture.capture_receipt_id,
        record_pointer=capture.record_pointer,
    )
    second_subject = subject.model_copy(
        update={
            "stream": subject.stream.model_copy(
                update={"provider_fixture_key": "101", "internal_match_id": "second"}
            ),
            "identity": subject.identity.model_copy(
                update={
                    "internal_match_id": "second",
                    "internal_home_team_id": "third",
                    "internal_away_team_id": "fourth",
                }
            ),
            "provider_mapping": subject.provider_mapping.model_copy(
                update={
                    "mapping_id": "second-mapping",
                    "external_match_id": "101",
                    "internal_match_id": "second",
                }
            ),
            "input": subject.input.model_copy(
                update={
                    "provider_mapping_id": "second-mapping",
                    "home_team_alias_id": "second-home-alias",
                    "away_team_alias_id": "second-away-alias",
                    "fixture": pointer,
                    "season": pointer,
                    "result": pointer,
                }
            ),
            "identity_evidence": subject.identity_evidence.model_copy(
                update={
                    "match_mappings": (
                        subject.identity_evidence.match_mappings[0].model_copy(
                            update={"record_id": "second-mapping"}
                        ),
                    ),
                    "team_aliases": tuple(
                        alias.model_copy(
                            update={"record_id": f"second-{alias.record_id}"}
                        )
                        for alias in subject.identity_evidence.team_aliases
                    ),
                }
            ),
            "inspection": subject.inspection.model_copy(
                update={
                    "provider_fixture_key": "101",
                    "provider_home_team_id": "777",
                    "provider_away_team_id": "888",
                }
            ),
            "resolved_home_team_id": "third",
            "resolved_away_team_id": "fourth",
            "fixture_capture": capture,
            "season_capture": capture,
            "result_capture": capture,
        }
    )
    second = ObservedSnapshotRecordV1.freeze(
        subject=second_subject,
        reviewer_attestation=attest(second_subject, AT + timedelta(seconds=3)),
        verified_at_utc=AT + timedelta(seconds=4),
        registered_at_utc=AT + timedelta(seconds=5),
    )
    capture = subject.fixture_capture.model_copy(
        update={
            "capture_receipt_id": "correction-capture",
            "capture_ordinal": 3,
            "outcome_sha256": subject.inspection.model_copy(
                update={"regular_time_home_goals": 0}
            ).outcome_sha256,
            "capture_observed_at_utc": AT + timedelta(seconds=6),
            "capture_registered_at_utc": AT + timedelta(seconds=7),
        }
    )
    pointer = ObservedCapturePointerV1(
        capture_receipt_id=capture.capture_receipt_id,
        record_pointer=capture.record_pointer,
    )
    correction_subject = subject.model_copy(
        update={
            "revision_sequence": 1,
            "predecessor_id": first.version_id,
            "predecessor_hash": first.content_hash,
            "previous_match_result_id": first.normalized_result.match_result_id,
            "input": subject.input.model_copy(
                update={"fixture": pointer, "season": pointer, "result": pointer}
            ),
            "fixture_capture": capture,
            "season_capture": capture,
            "result_capture": capture,
            "inspection": subject.inspection.model_copy(
                update={"regular_time_home_goals": 0}
            ),
        }
    )
    corrected = ObservedSnapshotRecordV1.freeze(
        subject=correction_subject,
        reviewer_attestation=attest(correction_subject, AT + timedelta(seconds=8)),
        verified_at_utc=AT + timedelta(seconds=9),
        registered_at_utc=AT + timedelta(seconds=10),
    )
    context = ObservedSnapshotContextV1(
        scope=scope,
        records=(first, second, corrected),
        actual_at_utc=AT + timedelta(seconds=15),
    )
    heads = context.select_heads(context.actual_at_utc)
    updated = ObservedQuantIntegrityPlanDefinitionV1.model_validate(
        definition.model_copy(
            update={
                "observed_context": context,
                "cohort": definition.cohort.model_copy(
                    update={"cohort_match_ids": ("match", "second")}
                ),
                "terminal_projection": definition.terminal_projection.model_copy(
                    update={"training_cutoff_at_utc": context.actual_at_utc}
                ),
                "selected_heads": tuple(ObservedFactRefV1.of(r) for r in heads),
                "selected_versions_root": observed_snapshot_root(heads),
            }
        ).model_dump()
    )
    plan, output, report = execute(updated)
    assert tuple(
        r.match_id for r in plan.content_payload.definition.selected_heads
    ) == ("match", "second")
    assert tuple(
        r.match_id
        for r in output.content_payload.terminal_state_core.content_payload.admitted_fact_refs
    ) == ("second", "match")
    assert report.content_payload.training_fact_count == 2
