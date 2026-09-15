import asyncio
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.post_review import (
    CreateFusionRunService,
    CreatePortfolioRevisionService,
)
from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.application.strategy_pass import StrategyPassService
from football_system.config import AppSettings, PortfolioSettings
from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.betting import PassType
from football_system.domain.market import ThreeWayFixedBonus, ThreeWayProbability
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.services.strategy_pass import build_strategy_plan
from football_system.domain.settlement import MatchResult
from football_system.domain.strategy_pass import StrategyProfileV1
from football_system.infrastructure.database.models import Base
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
from football_system.infrastructure.database.strategy_pass_repository import (
    SqlAlchemyStrategyPassRepository,
    plan_rows,
)
from football_system.infrastructure.database.strategy_pass_schema import (
    ATOMIC,
    LEG,
    PLAN,
    STRATEGY_TABLES,
    TICKET,
    strategy_pass_trigger_sql_v1,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
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
from tests.unit.test_strategy_pass import CASES


def seed(sessions, case=CASES[2], run_id="strategy-synthetic", budgets=(10000, 20000)):
    settings = AppSettings.from_toml("config/mvp.toml")
    settings = settings.model_copy(
        update={
            "portfolio": PortfolioSettings(),
            "analysis": settings.analysis.model_copy(
                update={
                    "min_selection_ev": Decimal(case["min_selection_ev"]),
                    "min_ticket_roi": Decimal(case["min_ticket_roi"]),
                }
            ),
        }
    )
    base = MockDataset.from_json(settings.mock.fixture_path)
    matches = tuple(
        m.model_copy(
            update={
                "sporttery_bonus": ThreeWayFixedBonus(
                    home_win=price, draw="1.1", away_win="1.1"
                ),
                "manual_quant": ThreeWayProbability(
                    home_win="0.6", draw="0.2", away_win="0.2"
                ),
            }
        )
        for m, price in zip(base.matches, case["home_prices"])
    )
    dataset = base.model_copy(update={"matches": matches})
    svc = RunAnalysisService(
        MockFixtureProvider(dataset),
        MockMarketOddsProvider(dataset),
        MockSportteryProvider(dataset),
        MockManualQuantProvider(dataset),
        SqlAlchemyAnalysisRepository(sessions),
        settings,
    )
    artifacts = asyncio.run(
        svc.run(
            RunAnalysisRequest(
                as_of_at_utc=dataset.as_of_at_utc,
                kickoff_from_utc=dataset.as_of_at_utc,
                kickoff_to_utc=dataset.as_of_at_utc + timedelta(days=2),
                budgets_fen=budgets,
                fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
                analysis_run_id=run_id,
                execution_time_utc=dataset.as_of_at_utc + timedelta(days=1),
            )
        )
    )
    return settings, artifacts


@pytest.fixture
def db():
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_five_cases_round_trip_replay_and_old_graph_unchanged(db, case):
    engine, sessions = db
    _, old = seed(sessions, case)
    before = legacy_rows(engine)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    service = StrategyPassService(repo)
    plan = service.create("ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000)
    assert repo.load_plan(plan.plan_id) == plan
    assert repo.save_plan(plan) == plan
    assert (
        service.create("ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000) == plan
    )
    assert {c.pass_type.value for c in plan.candidates} == set(case["expected_passes"])
    assert legacy_rows(engine) == before
    if case["id"] == "E":
        focused = service.create(
            "ANALYSIS_RUN",
            old.analysis_run.analysis_run_id,
            10000,
            profile=StrategyProfileV1(pass_types=(PassType.TWO_FOLD_ONE,)),
        )
        assert len(focused.tickets) == 1
    if case["id"] == "D":
        assert plan.status == "NO_BET"
    with engine.connect() as conn:
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()


def legacy_rows(engine):
    with engine.connect() as conn:
        return {
            name: [tuple(row) for row in conn.execute(text(f"SELECT * FROM {name}"))]
            for name in (
                "analysis_runs",
                "bet_candidates",
                "ticket_candidates",
                "tickets",
                "portfolios",
                "portfolio_revisions",
                "fusion_runs",
            )
        }


def test_revision_parent_preserves_p_final_budget_and_legacy_bytes(db):
    engine, sessions = db
    settings, old = seed(sessions)
    review = SqlAlchemyReviewArtifactRepository(sessions)
    packet, payload = ExportAnalysisPacketService(review).export(
        old.analysis_run.analysis_run_id
    )
    raw = canonical_json(
        dict(
            schema_version="LLM_REVIEW_V1",
            analysis_run_id=old.analysis_run.analysis_run_id,
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            match_reviews=[
                dict(
                    status="UNAVAILABLE",
                    match_id=m.match_id,
                    market_key=m.market_key,
                    failure_code="MODEL_UNAVAILABLE",
                    limitations=["SYNTHETIC ONLY"],
                )
                for m in packet.matches
            ],
        )
    )
    artifact = ImportLLMReviewService(review).import_review(
        payload.encode(), raw.encode()
    )
    post = SqlAlchemyPostReviewRepository(sessions)
    fusion = CreateFusionRunService(post, settings).create(artifact.review_artifact_id)
    revision = CreatePortfolioRevisionService(post, settings).create(
        fusion.fusion_run_id
    )
    before = legacy_rows(engine)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    p = StrategyPassService(repo).create(
        "PORTFOLIO_REVISION", revision.portfolio_revision_id, 10000
    )
    assert p.source.parent.fusion_run_id == fusion.fusion_run_id
    assert p.source.parent.source_hash == revision.revision_hash
    assert p.source.selections == tuple(
        sorted(revision.selection_candidates, key=lambda s: s.candidate_id)
    )
    assert repo.load_plan(p.plan_id) == p and legacy_rows(engine) == before
    assert post.find_portfolio_revision(revision.portfolio_revision_id) == revision


def test_parent_budget_and_gate_cannot_be_overridden(db):
    _, sessions = db
    _, old = seed(sessions, budgets=(0, 10000))
    repo = SqlAlchemyStrategyPassRepository(sessions)
    with pytest.raises(ValueError, match="budget"):
        repo.load_source("ANALYSIS_RUN", old.analysis_run.analysis_run_id, 5000)
    s = repo.load_source("ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000)
    forged = build_strategy_plan(
        s.model_copy(update={"min_selection_ev": Decimal(0)}), StrategyProfileV1()
    )
    with pytest.raises(ValueError, match="frozen parent"):
        repo.save_plan(forged)
    p = StrategyPassService(repo).create(
        "ANALYSIS_RUN", old.analysis_run.analysis_run_id, 0
    )
    assert p.status == "NO_BET" and p.total_stake_fen == 0


@pytest.mark.parametrize("operation", ["update", "delete", "replace", "extend"])
def test_sql_and_orm_cannot_mutate_sealed_graph(db, operation):
    engine, sessions = db
    _, old = seed(sessions)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    p = StrategyPassService(repo).create(
        "ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            if operation == "update":
                conn.execute(Base.metadata.tables[TICKET].update().values(stake_fen=1))
            elif operation == "delete":
                conn.execute(Base.metadata.tables[LEG].delete())
            elif operation == "replace":
                conn.execute(
                    Base.metadata.tables[PLAN].insert().prefix_with("OR REPLACE"),
                    plan_rows(p)[PLAN][0],
                )
            else:
                row = plan_rows(p)[ATOMIC][0] | {
                    "atomic_bet_id": "extension",
                    "atomic_no": 10,
                }
                conn.execute(Base.metadata.tables[ATOMIC].insert(), row)
    assert repo.load_plan(p.plan_id) == p


@pytest.mark.parametrize(
    "damage",
    ["missing_leg", "wrong_match", "wrong_price", "wrong_budget", "duplicate_match"],
)
def test_unsealed_partial_or_cross_lineage_graph_cannot_commit(db, damage):
    engine, sessions = db
    _, old = seed(sessions)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    p = build_strategy_plan(
        repo.load_source("ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000),
        StrategyProfileV1(pass_types=(PassType.FOUR_FOLD_ELEVEN,)),
    )
    rows = plan_rows(p)
    if damage == "missing_leg":
        rows[LEG].pop()
    elif damage == "wrong_match":
        rows[LEG][0]["internal_match_id"] = "not-this-match"
    elif damage == "wrong_price":
        rows[LEG][0]["fixed_bonus"] = "99"
    elif damage == "wrong_budget":
        rows[PLAN][0]["budget_fen"] = 12345
    else:
        rows[LEG][1]["internal_match_id"] = rows[LEG][0]["internal_match_id"]
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            for table, values in rows.items():
                if values:
                    conn.execute(Base.metadata.tables[table].insert(), values)
    with engine.connect() as conn:
        assert conn.scalar(text(f"SELECT COUNT(*) FROM {PLAN}")) == 0


def test_deferred_header_seal_prevents_committing_omitted_graph(db):
    engine, sessions = db
    _, old = seed(sessions)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    p = build_strategy_plan(
        repo.load_source("ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000),
        StrategyProfileV1(),
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(Base.metadata.tables[PLAN].insert(), plan_rows(p)[PLAN])


def test_database_corruption_is_detected_on_read(db):
    engine, sessions = db
    _, old = seed(sessions)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    p = StrategyPassService(repo).create(
        "ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000
    )
    # Isolated corruption fixture: intentionally remove exactly one DB guard.
    with engine.begin() as conn:
        conn.exec_driver_sql(f"DROP TRIGGER trg_{LEG}_delete_v1")
        row = conn.execute(select(Base.metadata.tables[LEG])).mappings().first()
        conn.execute(
            Base.metadata.tables[LEG]
            .delete()
            .where(
                *(
                    Base.metadata.tables[LEG].c[k] == row[k]
                    for k in ("plan_id", "ticket_id", "atomic_bet_id", "leg_no")
                )
            )
        )
    with pytest.raises(ValueError, match="graph mismatch"):
        repo.load_plan(p.plan_id)


def test_fresh_and_existing_upgrade_keep_legacy_artifacts_and_guard_downgrade(tmp_path):
    path = tmp_path / "upgrade.db"
    config = Config("alembic.ini", stdout=StringIO())
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, "17304b6d28a9")
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    sessions = create_session_factory(engine)
    _, old = seed(sessions)
    before = legacy_rows(engine)
    command.upgrade(config, "head")
    command.check(config)
    assert legacy_rows(engine) == before
    assert set(STRATEGY_TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert set(strategy_pass_trigger_sql_v1()) <= set(
            conn.scalars(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
        )
        assert (
            conn.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(config).get_current_head()
        )
    p = StrategyPassService(SqlAlchemyStrategyPassRepository(sessions)).create(
        "ANALYSIS_RUN", old.analysis_run.analysis_run_id, 10000
    )
    with pytest.raises(RuntimeError, match="strategy artifacts"):
        command.downgrade(config, "17304b6d28a9")
    assert SqlAlchemyStrategyPassRepository(sessions).load_plan(p.plan_id) == p
    engine.dispose()


def store_results(sessions, old):
    when = old.analysis_run.as_of_at_utc + timedelta(days=3)
    values = tuple(
        MatchResult(
            match_result_id=f"strategy-result-{m.match_id}",
            match_id=m.match_id,
            provider_code="MOCK_FIXTURE",
            home_goals=1,
            away_goals=0,
            observed_at_utc=when,
            available_at_utc=when,
            ingested_at_utc=when,
            source_result_key=m.match_id,
            payload_hash=match_result_payload_sha256(1, 0),
        )
        for m in old.matches
    )
    historical = SqlAlchemyHistoricalRepository(sessions)
    for value in values:
        historical.append_match_result(value)
    return values, when


def test_persisted_system_settlement_and_retry(db):
    _, sessions = db
    _, old = seed(sessions)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    svc = StrategyPassService(repo)
    p = svc.create(
        "ANALYSIS_RUN",
        old.analysis_run.analysis_run_id,
        10000,
        profile=StrategyProfileV1(pass_types=(PassType.FOUR_FOLD_ELEVEN,)),
    )
    results, when = store_results(sessions, old)
    ids = tuple(r.match_result_id for r in results)
    settled = svc.settle(p.plan_id, ids, when)
    assert settled.gross_payout_fen == p.tickets[0].max_payout_fen
    assert (
        svc.settle(p.plan_id, ids, when) == settled
        and repo.load_settlement(settled.settlement_id) == settled
    )
    with pytest.raises(IntegrityError):
        svc.settle(p.plan_id, ids, when + timedelta(seconds=1))


def test_settlement_correction_persists_new_child_and_rejects_fork(db):
    _, sessions = db
    _, old = seed(sessions)
    repo = SqlAlchemyStrategyPassRepository(sessions)
    svc = StrategyPassService(repo)
    p = svc.create(
        "ANALYSIS_RUN",
        old.analysis_run.analysis_run_id,
        10000,
        profile=StrategyProfileV1(pass_types=(PassType.FOUR_FOLD_ELEVEN,)),
    )
    results, when = store_results(sessions, old)
    first = svc.settle(p.plan_id, tuple(r.match_result_id for r in results), when)
    changed = results[0].model_copy(
        update={
            "match_result_id": "strategy-correction",
            "source_result_key": "strategy-correction-source-v2",
            "home_goals": 0,
            "payload_hash": match_result_payload_sha256(0, 0),
            "supersedes_match_result_id": results[0].match_result_id,
            "ingested_at_utc": when + timedelta(seconds=1),
        }
    )
    SqlAlchemyHistoricalRepository(sessions).append_match_result(changed)
    ids = (changed.match_result_id, *(r.match_result_id for r in results[1:]))
    correction = svc.settle(
        p.plan_id,
        ids,
        when + timedelta(seconds=1),
        supersedes_settlement_id=first.settlement_id,
    )
    assert 0 < correction.gross_payout_fen < first.gross_payout_fen
    assert correction.ticket_results[0].status == "PARTIAL_PAYOUT"
    assert repo.load_settlement(first.settlement_id) == first
    assert repo.load_settlement(correction.settlement_id) == correction
    assert (
        svc.settle(
            p.plan_id,
            ids,
            when + timedelta(seconds=1),
            supersedes_settlement_id=first.settlement_id,
        )
        == correction
    )
    with pytest.raises(IntegrityError):
        svc.settle(
            p.plan_id,
            ids,
            when + timedelta(seconds=2),
            supersedes_settlement_id=first.settlement_id,
        )
