"""Real immutable v0.8.0 code generates the old database; never new code at an old head."""

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
from alembic import command
from sqlalchemy import text

from football_system.infrastructure.database.return_distribution_schema import RETURN_TABLES
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.return_distribution import default_return_configuration
from tests.integration.test_market_v2_upgrade import config_for, database_snapshot

ROOT = Path(__file__).resolve().parents[2]


def test_fresh_check_empty_downgrade_reupgrade_and_populated_refusal(tmp_path):
    path = tmp_path / "fresh.db"
    cfg = config_for(path)
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "39526d8f40cb")
    assert not set(RETURN_TABLES) & set(database_snapshot(path)[0])
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    repo = SqlAlchemyReturnDistributionRepository(create_session_factory(engine))
    _, objective = default_return_configuration()
    repo.save(objective)
    with pytest.raises(RuntimeError, match="populated"):
        command.downgrade(cfg, "39526d8f40cb")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "4a637e9051dc"
        assert not connection.execute(text("PRAGMA foreign_key_check")).all()
    assert repo.load(objective.artifact_id) == objective
    engine.dispose()


def test_r_real_v080_database_upgrade_preserves_every_old_definition_row_and_operation(tmp_path):
    old = tmp_path / "old080"
    old.mkdir()
    archive = subprocess.check_output(["git", "archive", "v0.8.0", "src", "config", "data/fixtures", "migrations",
                                       "scripts/market_expansion_acceptance.py", "alembic.ini", "pyproject.toml", "README.md"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(archive)) as stream:
        stream.extractall(old, filter="data")
    path = tmp_path / "v080.db"
    env = dict(os.environ, PYTHONPATH=str(old / "src"), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    code = r'''
import json,sys
from pathlib import Path
from datetime import timedelta
import football_system
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.database.strategy_pass_repository import SqlAlchemyStrategyPassRepository
from football_system.application.strategy_pass import StrategyPassService
from football_system.domain.strategy_pass import StrategyProfileV1
from football_system.domain.betting import PassType
from football_system.application.market_v2 import PlanRequestV2,SettleRequestV2
from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.settlement import MatchResult
from football_system.infrastructure.database.historical_repositories import SqlAlchemyHistoricalRepository
from scripts.market_expansion_acceptance import seed_environment,build_fixture_analysis,request_for_counts,KICKOFF,LEGACY_RUN
assert football_system.__version__=='0.8.0' and Path(sys.argv[2]) in Path(football_system.__file__).parents
url='sqlite:///'+Path(sys.argv[1]).as_posix()
upgrade_database(url,Path(sys.argv[2])/'alembic.ini')
engine=create_database_engine(url); sessions=create_session_factory(engine)
_,history=seed_environment(sessions)
service,analysis,packet,review,fusion=build_fixture_analysis(sessions,history)
v1=StrategyPassService(SqlAlchemyStrategyPassRepository(sessions))
for kind in ('2X1','3X4','4X11'):
    previous_plan=v1.create('ANALYSIS_RUN',LEGACY_RUN,10000,profile=StrategyProfileV1(pass_types=(PassType(kind),)))
    v1.settle(previous_plan.plan_id,(),KICKOFF+timedelta(days=1))
at=KICKOFF+timedelta(days=1)
scores=((3,1),(2,1),(4,1),(0,2))
results=tuple(MatchResult(match_result_id='rd-upgrade-result-'+str(i),match_id='mm-target-'+str(i),provider_code='MOCK_FIXTURE',home_goals=h,away_goals=a,observed_at_utc=at,available_at_utc=at,ingested_at_utc=at,source_result_key='rd-upgrade-'+str(i),payload_hash=match_result_payload_sha256(h,a)) for i,(h,a) in enumerate(scores))
SqlAlchemyHistoricalRepository(sessions).append_match_results(results)
ids=[]
for counts in ((1,2),(1,2,2),(1,2,2,1)):
    plan=service.plan(PlanRequestV2(fusion_id=fusion.artifact_id,budget_fen=10000,requests=(request_for_counts(counts),)))
    service.settle(SettleRequestV2(plan_id=plan.artifact_id,result_ids=tuple(r.match_result_id for r in results[:len(counts)]),settled_at_utc=at))
    ids.append(plan.artifact_id)
print(json.dumps({'v2_plan_ids':ids}))
engine.dispose()
'''
    completed = subprocess.run([sys.executable, "-B", "-c", code, str(path), str(old)], cwd=old,
                               env=env, capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    ids = json.loads(completed.stdout.splitlines()[-1])["v2_plan_ids"]
    before = database_snapshot(path)
    assert not set(RETURN_TABLES) & set(before[0])
    cfg = config_for(path)
    command.upgrade(cfg, "head")
    command.check(cfg)
    after = database_snapshot(path)
    for section in range(3):
        assert all(after[section][key] == value for key, value in before[section].items())
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    sessions = create_session_factory(engine)
    from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository
    from football_system.domain.services.strategy_pass_v2 import build_strategy_v2
    from football_system.domain.services.settlement_v2 import settle_strategy_v2
    old_repo = SqlAlchemyMultiMarketRepository(sessions)
    for identity in ids:
        plan = old_repo.load(identity, "STRATEGY_PASS_PLAN_V2")
        assert build_strategy_v2(plan.source, plan.profile, plan.requests) == plan
        assert old_repo.save(plan) == plan
        with sessions() as session:
            settlement_id = session.scalar(text("SELECT artifact_id FROM mm_settlements WHERE plan_id=:id"), {"id": identity})
        settlement = old_repo.load(settlement_id, "STRATEGY_SETTLEMENT_V2")
        assert settle_strategy_v2(plan, settlement.results, settlement.settled_at_utc) == settlement
    from football_system.infrastructure.database.strategy_pass_repository import SqlAlchemyStrategyPassRepository
    from football_system.domain.services.strategy_pass import build_strategy_plan
    v1 = SqlAlchemyStrategyPassRepository(sessions)
    with sessions() as session:
        v1_ids = tuple(session.scalars(text("SELECT plan_id FROM strategy_pass_plans")))
    assert len(v1_ids) == 3
    for identity in v1_ids:
        plan = v1.load_plan(identity)
        assert build_strategy_plan(plan.source, plan.profile, plan.role_requests) == plan
        assert v1.save_plan(plan) == plan
    # Even the read/replay/exact retry operations preserve all old rows.
    final = database_snapshot(path)
    for section in range(3):
        assert all(final[section][key] == value for key, value in before[section].items())
    engine.dispose()
