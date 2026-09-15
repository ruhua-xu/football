"""Only the two V4 contract blockers; all probability/correction math is reused."""

import json
from decimal import Decimal

import pytest

from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import (
    FootballEvidenceV1,
    MarketAnalysisUnitV1,
    MultiMarketAnalysisV1,
)
from football_system.domain.review_v4 import ImportedReviewV4, LLMReviewV4
from football_system.domain.services import review_v4 as service
from football_system.domain.services.market_analysis import build_market_unit
from football_system.domain.services.poisson_goals import train_poisson
from tests.unit.test_market_v2 import goal_fixture
from tests.unit.test_review_v4 import analysis_fixture, review_bytes

ABSTENTIONS = ("INSUFFICIENT_EVIDENCE", "INVALID_CONTEXT", "SKIPPED_DISABLED")
COMMON_FIELDS = (
    "match_id",
    "market_key",
    "review_context_id",
    "review_context_hash",
    "limitations",
)


def abstention(item, code):
    return {
        **{key: item[key] for key in COMMON_FIELDS},
        "status": "UNAVAILABLE",
        "failure_code": code,
    }


def structured_submission(packet):
    data = json.loads(review_bytes(packet))
    for item, unit in zip(data["market_reviews"], packet.market_units, strict=True):
        outcomes = unit.review_context.market_key.catalog
        refs = list(unit.evidence_ids)
        item.update(
            preferred_outcomes=[outcomes[0].value],
            avoid_outcomes=[outcomes[2].value],
            risk_tags=["SYNTHETIC_FIXTURE"],
            evidence_refs=refs,
            scenarios=[
                dict(
                    scenario_id=kind.lower(),
                    scenario_type=kind,
                    description=f"Synthetic {kind} scenario.",
                    outcomes=[outcomes[i].value],
                    trigger_conditions=[f"Synthetic trigger {i}."],
                    evidence_refs=refs,
                )
                for i, kind in enumerate(("MAIN", "SECONDARY", "UPSET"))
            ],
            counter_scenarios=[
                dict(
                    if_scenario_id="main",
                    alternative_scenario_id="upset",
                    fails_outcomes=[outcomes[0].value],
                    rationale="The synthetic upset invalidates the main outcome.",
                    evidence_refs=refs,
                )
            ],
        )
    return data


def import_submission(packet, data):
    return service.import_v4_bytes(
        canonical_json(packet).encode(), canonical_json(data).encode()
    )


@pytest.fixture(scope="module")
def available_analysis():
    original = analysis_fixture()
    evidence = FootballEvidenceV1.freeze(
        match_id="target",
        facts=("Synthetic football context.",),
        source_reference="synthetic-v4-blocker-test",
        source_hash="e" * 64,
        available_at_utc=original.units[0].decision_cutoff,
        ingested_at_utc=original.units[0].decision_cutoff,
    )
    units = tuple(
        MarketAnalysisUnitV1.freeze(
            **(
                u.model_dump(exclude={"artifact_id", "content_hash"})
                | {"evidence": (evidence,)}
            )
        )
        for u in original.units
    )
    return MultiMarketAnalysisV1.freeze(
        **(
            original.model_dump(exclude={"artifact_id", "content_hash"})
            | {"units": units}
        )
    )


@pytest.fixture(scope="module")
def unavailable_analysis():
    original = analysis_fixture()
    previous = next(
        u for u in original.units if u.market_key.market_type == "TOTAL_GOALS"
    )
    cohort, request = goal_fixture(4, 4)
    unit = build_market_unit(
        identity=previous.identity,
        market_key=previous.market_key,
        decision_cutoff=previous.decision_cutoff,
        sporttery=previous.sporttery,
        consensus=previous.consensus,
        goal_state=train_poisson(cohort, request),
        base_policy="QUANT_ONLY_V1",
    )
    return MultiMarketAnalysisV1.freeze(
        **(
            original.model_dump(exclude={"artifact_id", "content_hash"})
            | {"units": (unit,)}
        )
    )


@pytest.mark.parametrize("code", ABSTENTIONS)
def test_available_quant_abstention_preserves_exact_base_without_correction(
    available_analysis, code, monkeypatch
):
    packet = service.export_packet_v4(available_analysis)
    data = json.loads(review_bytes(packet))
    data["market_reviews"] = [abstention(item, code) for item in data["market_reviews"]]
    imported = import_submission(packet, data)
    assert all(
        not hasattr(item, "p_llm") for item in imported.submission.market_reviews
    )
    monkeypatch.setattr(
        service,
        "generic_correction",
        lambda *args: pytest.fail("abstention entered correction"),
    )
    fused = service.fuse_v4(available_analysis, imported)
    for unit, result in zip(available_analysis.units, fused.results, strict=True):
        assert result.p_final.model_dump_json() == unit.p_base.model_dump_json()
        assert result.influence == Decimal(0)
        assert result.fallback_code == code
    assert service.fuse_v4(available_analysis, imported) == fused


@pytest.mark.parametrize("position", (0, 1, 2))
def test_available_quant_cannot_claim_model_unavailable(available_analysis, position):
    packet = service.export_packet_v4(available_analysis)
    data = json.loads(review_bytes(packet))
    data["market_reviews"][position] = abstention(
        data["market_reviews"][position], "MODEL_UNAVAILABLE"
    )
    with pytest.raises(
        ValueError, match="AVAILABLE quant cannot claim MODEL_UNAVAILABLE"
    ):
        import_submission(packet, data)


@pytest.mark.parametrize("code", ("MODEL_UNAVAILABLE", *ABSTENTIONS, "VALID"))
def test_unavailable_quant_requires_model_unavailable_review(
    unavailable_analysis, code
):
    packet = service.export_packet_v4(unavailable_analysis)
    context = packet.market_units[0]
    c = context.review_context
    item = dict(
        match_id=c.identity.match_id,
        market_key=c.market_key,
        review_context_id=context.review_context_id,
        review_context_hash=context.review_context_hash,
        limitations=["Synthetic unavailable model."],
        status="UNAVAILABLE",
        failure_code=code,
    )
    if code == "VALID":
        # A fabricated P_llm is not a substitute for unavailable model lineage.
        valid_unit = next(
            u for u in analysis_fixture().units if u.market_key == c.market_key
        )
        item.pop("failure_code")
        item.update(
            status="VALID",
            p_llm=valid_unit.p_quant,
            assessment_confidence="0.4",
            scenarios=[],
            preferred_outcomes=[],
            avoid_outcomes=[],
            counter_scenarios=[],
            risk_tags=[],
            reasoning_summary="Invalid synthetic review.",
            evidence_refs=[],
        )
    data = dict(
        schema_version="LLM_REVIEW_V4",
        analysis_id=packet.analysis.artifact_id,
        packet_id=packet.artifact_id,
        packet_hash=packet.content_hash,
        market_reviews=[item],
    )
    if code == "MODEL_UNAVAILABLE":
        imported = import_submission(packet, data)
        fused = service.fuse_v4(unavailable_analysis, imported)
        assert fused.results[0].p_final is None and fused.results[0].influence == 0
    else:
        with pytest.raises(ValueError, match="MODEL_UNAVAILABLE quant requires"):
            import_submission(packet, data)


def test_mixed_valid_and_abstaining_market_units_are_independent(available_analysis):
    packet = service.export_packet_v4(available_analysis)
    data = json.loads(review_bytes(packet))
    data["market_reviews"][0] = abstention(
        data["market_reviews"][0], "INSUFFICIENT_EVIDENCE"
    )
    fused = service.fuse_v4(available_analysis, import_submission(packet, data))
    assert fused.results[0].p_final == available_analysis.units[0].p_base
    assert fused.results[0].influence == 0
    assert fused.results[0].fallback_code == "INSUFFICIENT_EVIDENCE"
    assert all(r.influence > 0 and r.fallback_code is None for r in fused.results[1:])


def test_abstention_cannot_bypass_context_binding_or_supply_fake_llm(
    available_analysis,
):
    packet = service.export_packet_v4(available_analysis)
    data = json.loads(review_bytes(packet))
    llm = data["market_reviews"][0]["p_llm"]
    data["market_reviews"][0] = abstention(data["market_reviews"][0], "INVALID_CONTEXT")
    data["market_reviews"][0]["review_context_hash"] = "0" * 64
    with pytest.raises(ValueError, match="context binding"):
        import_submission(packet, data)
    data["market_reviews"][0]["review_context_hash"] = packet.market_units[
        0
    ].review_context_hash
    data["market_reviews"][0]["p_llm"] = llm
    with pytest.raises(ValueError, match="Extra inputs"):
        import_submission(packet, data)


def test_main_secondary_upset_and_typed_counters_roundtrip(available_analysis):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    raw = (" \n" + canonical_json(data) + "\n ").encode()
    imported = service.import_v4_bytes(canonical_json(packet).encode(), raw)
    assert imported.raw_review_json.encode() == raw
    assert ImportedReviewV4.model_validate_json(imported.model_dump_json()) == imported
    assert service.import_v4_bytes(canonical_json(packet).encode(), raw) == imported
    for review in imported.submission.market_reviews:
        assert tuple(s.scenario_type for s in review.scenarios) == (
            "MAIN",
            "SECONDARY",
            "UPSET",
        )
        assert tuple(s.scenario_id for s in review.scenarios) == (
            "main",
            "secondary",
            "upset",
        )
        assert all(s.trigger_conditions for s in review.scenarios)
        counter = review.counter_scenarios[0]
        assert (counter.if_scenario_id, counter.alternative_scenario_id) == (
            "main",
            "upset",
        )
        assert counter.fails_outcomes == review.scenarios[0].outcomes
    assert service.fuse_v4(available_analysis, imported) == service.fuse_v4(
        available_analysis, imported
    )


def test_preferred_and_avoid_outcomes_cannot_overlap(available_analysis):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    data["market_reviews"][0]["avoid_outcomes"] = data["market_reviews"][0][
        "preferred_outcomes"
    ]
    with pytest.raises(ValueError, match="must not overlap"):
        import_submission(packet, data)


@pytest.mark.parametrize("spacing", (False, True))
def test_duplicate_scenario_ids_are_rejected_after_string_validation(
    available_analysis, spacing
):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    data["market_reviews"][0]["scenarios"][1]["scenario_id"] = (
        " main " if spacing else "main"
    )
    with pytest.raises(ValueError, match="scenario IDs must be unique"):
        import_submission(packet, data)


@pytest.mark.parametrize("field", ("if_scenario_id", "alternative_scenario_id"))
def test_counter_scenario_references_must_exist_in_same_market_unit(
    available_analysis, field
):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    data["market_reviews"][1]["scenarios"].append(
        dict(
            scenario_id="only-other-market",
            scenario_type="SECONDARY",
            description="Other market only.",
            outcomes=[],
            trigger_conditions=[],
            evidence_refs=[],
        )
    )
    data["market_reviews"][0]["counter_scenarios"][0][field] = "only-other-market"
    with pytest.raises(ValueError, match="unknown counter-scenario reference"):
        import_submission(packet, data)


@pytest.mark.parametrize(
    "path",
    (
        ("preferred_outcomes",),
        ("avoid_outcomes",),
        ("risk_tags",),
        ("evidence_refs",),
        ("limitations",),
        ("scenarios", 0, "outcomes"),
        ("scenarios", 0, "evidence_refs"),
        ("counter_scenarios", 0, "fails_outcomes"),
        ("counter_scenarios", 0, "evidence_refs"),
    ),
)
def test_v4_review_lists_reject_duplicates(available_analysis, path):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    values = data["market_reviews"][0]
    for key in path:
        values = values[key]
    values.append(values[0])
    with pytest.raises(ValueError, match="must be unique"):
        import_submission(packet, data)


def test_unavailable_review_limitations_are_also_unique(available_analysis):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    data["market_reviews"][0] = abstention(
        data["market_reviews"][0], "SKIPPED_DISABLED"
    )
    data["market_reviews"][0]["limitations"] *= 2
    with pytest.raises(ValueError, match="limitations must be unique"):
        import_submission(packet, data)


@pytest.mark.parametrize(
    "collection,field",
    (
        ("scenarios", "scenario_id"),
        ("scenarios", "scenario_type"),
        ("scenarios", "trigger_conditions"),
        ("counter_scenarios", "if_scenario_id"),
        ("counter_scenarios", "alternative_scenario_id"),
        ("counter_scenarios", "fails_outcomes"),
        ("counter_scenarios", "rationale"),
    ),
)
def test_structured_scenario_and_counter_fields_are_required(
    available_analysis, collection, field
):
    packet = service.export_packet_v4(available_analysis)
    data = structured_submission(packet)
    del data["market_reviews"][0][collection][0][field]
    with pytest.raises(ValueError, match="Field required"):
        import_submission(packet, data)


def test_v4_schema_exposes_abstention_and_separate_scenario_contracts():
    definitions = LLMReviewV4.model_json_schema()["$defs"]
    assert set(
        definitions["UnavailableMarketReviewV4"]["properties"]["failure_code"]["enum"]
    ) == {"MODEL_UNAVAILABLE", *ABSTENTIONS}
    scenario = definitions["MarketScenarioV4"]
    counter = definitions["MarketCounterScenarioV4"]
    assert set(scenario["required"]) == {
        "scenario_id",
        "scenario_type",
        "description",
        "outcomes",
        "trigger_conditions",
        "evidence_refs",
    }
    assert set(scenario["properties"]["scenario_type"]["enum"]) == {
        "MAIN",
        "SECONDARY",
        "UPSET",
    }
    assert set(counter["required"]) == {
        "if_scenario_id",
        "alternative_scenario_id",
        "fails_outcomes",
        "rationale",
        "evidence_refs",
    }
    assert scenario["additionalProperties"] is counter["additionalProperties"] is False
