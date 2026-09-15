from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.market_v2 import PlanRequestV2, SettleRequestV2
from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.settlement import MatchResult
from football_system.domain.strategy_pass_v2 import StrategyProfileV2
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.market_v2_repository import (
    SqlAlchemyMultiMarketRepository,
    rows_for,
)
from football_system.infrastructure.database.models import Base
from football_system.domain.offline_market_source import OfflineMarketSourceV1
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from scripts.market_expansion_acceptance import (
    seed_environment,
    build_fixture_analysis,
    request_for_counts,
    KICKOFF,
)


@pytest.fixture(scope="module")
def prepared():
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    old, results = seed_environment(sessions)
    service, analysis, packet, review, fusion = build_fixture_analysis(
        sessions, results
    )
    service.plan(
        PlanRequestV2(
            fusion_id=fusion.artifact_id,
            budget_fen=10000,
            requests=(request_for_counts((1, 2)),),
        )
    )
    yield engine, sessions, service, analysis, packet, review, fusion, old
    engine.dispose()


def test_full_poisson_legacy_v4_fusion_graph_roundtrip(prepared):
    engine, sessions, service, analysis, packet, review, fusion, old = prepared
    repo = service.repository
    for artifact in (analysis, packet, review, fusion):
        assert repo.load(artifact.artifact_id) == artifact
        assert repo.save(artifact) == artifact
    legacy = next(u for u in analysis.units if u.market_key.market_type == "THREE_WAY")
    expected = next(
        p for p in old.final_predictions if p.match_id == legacy.identity.match_id
    )
    assert legacy.p_base.to_three_way() == expected.probabilities
    from football_system.infrastructure.database.models import FinalPredictionRecord
    from football_system.infrastructure.database.post_review_repositories import (
        _final_prediction,
    )

    with sessions() as session:
        persisted = _final_prediction(
            session, session.get(FinalPredictionRecord, expected.prediction_id)
        )
    assert (
        legacy.p_base.to_three_way().model_dump_json()
        == persisted.probabilities.model_dump_json()
    )
    assert legacy.model_lineage.model_name == "ELO_THREE_WAY_BASELINE_V1"
    assert len(packet.market_units) == 16
    with engine.connect() as conn:
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()


@pytest.mark.parametrize(
    "counts,expected", [((1, 2), 2), ((1, 2, 2), 12), ((1, 2, 2, 1), 29)]
)
def test_expanded_pass_persistence_counts_and_retry(prepared, counts, expected):
    engine, sessions, service, analysis, packet, review, fusion, old = prepared
    request = PlanRequestV2(
        fusion_id=fusion.artifact_id,
        budget_fen=10000,
        requests=(request_for_counts(counts),),
    )
    plan = service.plan(request)
    assert plan.candidates[0].expanded_atomic_bet_count == expected
    assert service.plan(request) == plan
    assert service.repository.load(plan.artifact_id) == plan
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT COUNT(*) FROM mm_candidate_atomics WHERE parent_id=:id"),
                {"id": plan.candidates[0].artifact_id},
            )
            == expected
        )


def test_explosion_has_no_partial_persistence(prepared):
    engine, sessions, service, analysis, packet, review, fusion, old = prepared
    with engine.connect() as conn:
        before = conn.scalar(text("SELECT COUNT(*) FROM mm_artifacts"))
    request = PlanRequestV2(
        fusion_id=fusion.artifact_id,
        budget_fen=10000,
        profile=StrategyProfileV2(max_expanded_atomic_bets_per_ticket=28),
        requests=(request_for_counts((1, 2, 2, 1)),),
    )
    with pytest.raises(ValueError, match="before materialization"):
        service.plan(request)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT COUNT(*) FROM mm_artifacts")) == before


@pytest.mark.parametrize(
    "table", ["mm_artifacts", "mm_analysis_units", "mm_sp_prices", "mm_atomic_legs"]
)
def test_sql_update_delete_replace_are_blocked(prepared, table):
    engine, *_ = prepared
    model = Base.metadata.tables[table]
    with engine.connect() as conn:
        row = conn.execute(select(model)).mappings().first()
    assert row is not None
    for statement in (
        model.delete(),
        model.update().values({list(row)[0]: row[list(row)[0]]}),
        model.insert().prefix_with("OR REPLACE").values(dict(row)),
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(statement)


def test_settlement_uses_persisted_regular_time_scores_and_keeps_correction(prepared):
    engine, sessions, service, analysis, packet, review, fusion, old = prepared
    plan = service.plan(
        PlanRequestV2(
            fusion_id=fusion.artifact_id,
            budget_fen=10000,
            requests=(request_for_counts((1, 2)),),
        )
    )
    at = KICKOFF + timedelta(days=1)
    results = tuple(
        MatchResult(
            match_result_id=f"mm-evaluation-{i}",
            match_id=f"mm-target-{i}",
            provider_code="MOCK_FIXTURE",
            home_goals=2,
            away_goals=1,
            observed_at_utc=at,
            available_at_utc=at,
            ingested_at_utc=at,
            source_result_key=f"mm-final-{i}",
            payload_hash=match_result_payload_sha256(2, 1),
        )
        for i in range(2)
    )
    history = SqlAlchemyHistoricalRepository(sessions)
    history.append_match_results(results)
    request = SettleRequestV2(
        plan_id=plan.artifact_id,
        result_ids=tuple(r.match_result_id for r in results),
        settled_at_utc=at,
    )
    settlement = service.settle(request)
    assert settlement.gross_payout_fen == 200 * 4 * 8 * plan.tickets[0].multiplier
    assert (
        service.repository.load(settlement.artifact_id) == settlement
        and service.settle(request) == settlement
    )
    new = results[1].model_copy(
        update={
            "match_result_id": "mm-corrected-result",
            "source_result_key": "mm-corrected-key",
            "home_goals": 0,
            "away_goals": 0,
            "payload_hash": match_result_payload_sha256(0, 0),
            "ingested_at_utc": at + timedelta(seconds=1),
            "supersedes_match_result_id": results[1].match_result_id,
        }
    )
    history.append_match_result(new)
    correction = service.settle(
        SettleRequestV2(
            plan_id=plan.artifact_id,
            result_ids=(results[0].match_result_id, new.match_result_id),
            settled_at_utc=at + timedelta(seconds=1),
            previous_id=settlement.artifact_id,
        )
    )
    assert correction.gross_payout_fen == 0
    assert service.repository.load(settlement.artifact_id) == settlement


def test_deferred_seal_refuses_partial_header_commit(prepared):
    engine, *_ = prepared
    source = OfflineMarketSourceV1.freeze(
        source_reference="partial-source",
        raw_json="{}",
        raw_sha256=hashlib.sha256(b"{}").hexdigest(),
    )
    values = rows_for(source)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                Base.metadata.tables["mm_artifacts"].insert(), values["mm_artifacts"]
            )
            conn.execute(
                Base.metadata.tables["mm_sources"].insert(), values["mm_sources"]
            )


def test_corrupt_stored_expanded_graph_is_rejected_on_read(prepared, tmp_path):
    engine, sessions, service, analysis, packet, review, fusion, old = prepared
    plan = service.plan(
        PlanRequestV2(
            fusion_id=fusion.artifact_id,
            budget_fen=10000,
            requests=(request_for_counts((1, 2, 2)),),
        )
    )
    clone = create_database_engine(f"sqlite:///{(tmp_path / 'corrupt.db').as_posix()}")
    raw = engine.raw_connection()
    dest = clone.raw_connection()
    try:
        raw.driver_connection.backup(dest.driver_connection)
    finally:
        raw.close()
        dest.close()
    with clone.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER trg_mm_atomic_legs_delete_v1")
        conn.execute(
            text("DELETE FROM mm_atomic_legs WHERE parent_id=:id AND position=0"),
            {"id": plan.candidates[0].atomic_bets[0].artifact_id},
        )
    repo = SqlAlchemyMultiMarketRepository(create_session_factory(clone))
    with pytest.raises(ValueError, match="corrupt sealed"):
        repo.load(plan.artifact_id)
    clone.dispose()
