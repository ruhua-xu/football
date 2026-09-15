import json
from decimal import Decimal, localcontext, ROUND_DOWN

import pytest

from football_system.application.review_v4 import (
    export_packet_v4,
    import_v4_bytes,
    fuse_v4,
    generic_correction,
    parse_v4_json,
)
from football_system.domain.archive import canonical_json
from football_system.domain.betting import SportteryRules, PortfolioConstraints
from football_system.domain.market_analysis import (
    LegacyThreeWayInputV1,
    MarketMatchIdentityV1,
    MarketModelLineageV1,
    MultiMarketAnalysisV1,
)
from football_system.domain.market_v2 import (
    MarketPriceV1,
    SportteryFixedBonusSnapshotV2,
    distribution,
    market_consensus,
)
from football_system.domain.services.market_analysis import build_market_unit
from football_system.domain.services.poisson_goals import train_poisson
from tests.unit.test_market_v2 import goal_fixture, market


def analysis_fixture():
    cohort, request = goal_fixture()
    state = train_poisson(cohort, request)
    identity = MarketMatchIdentityV1(
        match_id="target",
        competition_id="league",
        season_id="2026",
        home_team_id="home",
        away_team_id="away",
        home_team_name="Synthetic Home",
        away_team_name="Synthetic Away",
        kickoff_at_utc=request.kickoff_at_utc,
    )
    at = request.generated_at_utc
    units = []
    for kind in ("THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE"):
        key = market(kind)
        sp = SportteryFixedBonusSnapshotV2.freeze(
            match_id="target",
            market_key=key,
            prices=tuple(MarketPriceV1(outcome=o, price="20.00") for o in key.catalog),
            provider_code="SYNTHETIC",
            source_identity=kind,
            source_artifact_id="raw-" + kind,
            source_artifact_hash="d" * 64,
            sale_status="OPEN",
            captured_at_utc=at,
            available_at_utc=at,
            ingested_at_utc=at,
        )
        legacy = None
        if kind == "THREE_WAY":
            p = distribution(key, [Decimal("0.6"), Decimal("0.2"), Decimal("0.2")])
            legacy = LegacyThreeWayInputV1.freeze(
                analysis_run_id="legacy-synthetic",
                analysis_run_hash="b" * 64,
                identity=identity,
                decision_cutoff=at,
                model_lineage=MarketModelLineageV1(
                    model_name="ELO_THREE_WAY_BASELINE_V1",
                    state_id="legacy-state",
                    state_hash="a" * 64,
                    config_hash="c" * 64,
                    training_data_hash="d" * 64,
                    training_cutoff_at_utc=at,
                    generated_at_utc=at,
                ),
                p_market=p,
                p_quant=p,
                p_base=p,
                unavailable_reason=None,
            )
        units.append(
            build_market_unit(
                identity=identity,
                market_key=key,
                decision_cutoff=at,
                sporttery=sp,
                consensus=None if legacy else market_consensus("target", key, (), at),
                goal_state=None if legacy else state,
                legacy_source=legacy,
                base_policy="LEGACY_FROZEN_THREE_WAY" if legacy else "QUANT_ONLY_V1",
            )
        )
    return MultiMarketAnalysisV1.freeze(
        units=tuple(
            sorted(units, key=lambda u: (u.identity.match_id, u.market_key.canonical))
        ),
        budgets_fen=(0, 10000),
        rules=SportteryRules(version="SYNTHETIC_V1"),
        constraints=PortfolioConstraints(),
        min_selection_ev="0.02",
        min_ticket_roi="0.02",
    )


def review_bytes(packet):
    return (
        " \n"
        + json.dumps(
            dict(
                schema_version="LLM_REVIEW_V4",
                analysis_id=packet.analysis.artifact_id,
                packet_id=packet.artifact_id,
                packet_hash=packet.content_hash,
                market_reviews=[
                    dict(
                        match_id=u.review_context.identity.match_id,
                        market_key=u.review_context.market_key.model_dump(mode="json"),
                        review_context_id=u.review_context_id,
                        review_context_hash=u.review_context_hash,
                        status="VALID",
                        p_llm=u.review_context.p_quant.model_dump(mode="json"),
                        assessment_confidence="0.40",
                        scenarios=[],
                        preferred_outcomes=[],
                        avoid_outcomes=[],
                        counter_scenarios=[],
                        risk_tags=[],
                        reasoning_summary="SYNTHETIC FIXED REVIEW",
                        limitations=["Not real model performance"],
                        evidence_refs=[],
                    )
                    for u in packet.market_units
                ],
            )
        )
        + "\n "
    ).encode()


def test_v4_same_match_multiple_markets_and_raw_bytes_roundtrip():
    analysis = analysis_fixture()
    packet = export_packet_v4(analysis)
    assert len(packet.market_units) == 3
    raw = review_bytes(packet)
    imported = import_v4_bytes(canonical_json(packet).encode(), raw)
    assert imported.raw_review_json.encode() == raw
    assert imported == import_v4_bytes(canonical_json(packet).encode(), raw)
    fusion = fuse_v4(analysis, imported)
    assert fusion == fuse_v4(analysis, imported)
    assert all(
        sum(o.probability for o in r.p_final.outcomes) == 1 for r in fusion.results
    )
    forbidden = {
        "p_final",
        "p_base",
        "ev",
        "ticket",
        "portfolio",
        "budget",
        "stake",
        "rules",
        "constraints",
        "quant_weight",
    }

    def walk(x):
        if isinstance(x, dict):
            assert not forbidden.intersection(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(json.loads(canonical_json(packet)))


@pytest.mark.parametrize(
    "mutation",
    [
        "float",
        "duplicate",
        "context",
        "packet",
        "extra",
        "missing",
        "market",
        "outcome",
    ],
)
def test_v4_rejects_wrong_or_forbidden_inputs(mutation):
    packet = export_packet_v4(analysis_fixture())
    data = json.loads(review_bytes(packet))
    if mutation == "float":
        data["market_reviews"][0]["assessment_confidence"] = 0.5
    elif mutation == "context":
        data["market_reviews"][0]["review_context_hash"] = "0" * 64
    elif mutation == "packet":
        data["packet_id"] = "wrong"
    elif mutation == "extra":
        data["market_reviews"][0]["p_final"] = data["market_reviews"][0]["p_llm"]
    elif mutation == "missing":
        data["market_reviews"].pop()
    elif mutation == "market":
        data["market_reviews"][0]["market_key"] = {"market_type": "THREE_WAY"}
    elif mutation == "outcome":
        data["market_reviews"][0]["p_llm"]["outcomes"].pop()
    raw = json.dumps(data).encode()
    if mutation == "duplicate":
        raw = raw.replace(
            b'"schema_version": "LLM_REVIEW_V4"',
            b'"schema_version": "LLM_REVIEW_V4", "schema_version": "LLM_REVIEW_V4"',
            1,
        )
    with pytest.raises(ValueError):
        import_v4_bytes(canonical_json(packet).encode(), raw)


@pytest.mark.parametrize("kind", ["THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE"])
def test_generic_extreme_correction_is_positive_closed_and_capped(kind):
    key = market(kind)
    n = len(key.catalog)
    base = distribution(key, [Decimal(1), *([Decimal(0)] * (n - 1))])
    llm = distribution(key, [*([Decimal(0)] * (n - 1)), Decimal(1)])
    result = generic_correction(base, llm, Decimal(1), Decimal(1), Decimal("0.08"))
    assert sum(o.probability for o in result.outcomes) == 1
    assert all(0 <= o.probability <= 1 for o in result.outcomes)
    assert all(
        abs(x.probability - y.probability) <= Decimal("0.08")
        for x, y in zip(result.outcomes, base.outcomes)
    )


def test_v4_file_size_and_nonfinite_rejection():
    with pytest.raises(ValueError):
        parse_v4_json(b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        parse_v4_json(b'{"x":NaN}')


def test_unavailable_poisson_cannot_be_reviewed_as_available():
    original = analysis_fixture()
    previous = next(
        u for u in original.units if u.market_key.market_type == "TOTAL_GOALS"
    )
    cohort, request = goal_fixture(4, 4)
    state = train_poisson(cohort, request)
    unit = build_market_unit(
        identity=previous.identity,
        market_key=previous.market_key,
        decision_cutoff=previous.decision_cutoff,
        sporttery=previous.sporttery,
        consensus=previous.consensus,
        goal_state=state,
        base_policy="QUANT_ONLY_V1",
    )
    analysis = MultiMarketAnalysisV1.freeze(
        units=(unit,),
        budgets_fen=(0,),
        rules=original.rules,
        constraints=original.constraints,
        min_selection_ev="0.02",
        min_ticket_roi="0.02",
    )
    packet = export_packet_v4(analysis)
    context = packet.market_units[0]
    item = dict(
        match_id="target",
        market_key=unit.market_key.model_dump(mode="json"),
        review_context_id=context.review_context_id,
        review_context_hash=context.review_context_hash,
        status="UNAVAILABLE",
        failure_code="MODEL_UNAVAILABLE",
        limitations=["INSUFFICIENT_LEAGUE_HISTORY"],
    )
    raw = canonical_json(
        dict(
            schema_version="LLM_REVIEW_V4",
            analysis_id=analysis.artifact_id,
            packet_id=packet.artifact_id,
            packet_hash=packet.content_hash,
            market_reviews=[item],
        )
    ).encode()
    imported = import_v4_bytes(canonical_json(packet).encode(), raw)
    assert fuse_v4(analysis, imported).results[0].p_final is None
    item.update(
        status="VALID",
        p_llm=previous.p_quant.model_dump(mode="json"),
        assessment_confidence="1",
        scenarios=[],
        preferred_outcomes=[],
        avoid_outcomes=[],
        counter_scenarios=[],
        risk_tags=[],
        reasoning_summary="invalid",
        evidence_refs=[],
    )
    del item["failure_code"]
    with pytest.raises(ValueError, match="MODEL_UNAVAILABLE"):
        import_v4_bytes(
            canonical_json(packet).encode(),
            canonical_json(
                dict(
                    schema_version="LLM_REVIEW_V4",
                    analysis_id=analysis.artifact_id,
                    packet_id=packet.artifact_id,
                    packet_hash=packet.content_hash,
                    market_reviews=[item],
                )
            ).encode(),
        )


def test_generic_fusion_replay_does_not_depend_on_ambient_decimal_context():
    analysis = analysis_fixture()
    packet = export_packet_v4(analysis)
    review = import_v4_bytes(canonical_json(packet).encode(), review_bytes(packet))
    expected = fuse_v4(analysis, review)
    with localcontext() as context:
        context.prec = 15
        context.rounding = ROUND_DOWN
        assert fuse_v4(analysis, review) == expected
