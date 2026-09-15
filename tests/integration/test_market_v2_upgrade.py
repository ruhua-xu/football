from io import BytesIO, StringIO
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from football_system.domain.services.strategy_pass import build_strategy_plan
from football_system.infrastructure.database.market_v2_schema import (
    MARKET_V2_TABLES,
    market_v2_triggers,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.database.strategy_pass_repository import (
    SqlAlchemyStrategyPassRepository,
)

ROOT = Path(__file__).resolve().parents[2]


def database_snapshot(path):
    with sqlite3.connect(path) as c:
        tables = dict(
            c.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'alembic_version'"
            )
        )
        triggers = dict(
            c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")
        )
        rows = {
            name: sorted(
                json.dumps(tuple(row), ensure_ascii=False)
                for row in c.execute(f'SELECT * FROM "{name}"')
            )
            for name in tables
        }
    return tables, triggers, rows


def config_for(path):
    cfg = Config(str(ROOT / "alembic.ini"), stdout=StringIO())
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    return cfg


def test_fresh_upgrade_empty_downgrade_reupgrade_and_populated_refusal(tmp_path):
    db = tmp_path / "fresh.db"
    cfg = config_for(db)
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "28415c7e39ba")
    command.upgrade(cfg, "head")
    command.check(cfg)
    engine = create_database_engine(f"sqlite:///{db.as_posix()}")
    from football_system.domain.market_v2 import MarketKeyV2
    from football_system.infrastructure.database.market_v2_repository import (
        SqlAlchemyMultiMarketRepository,
    )

    repo = SqlAlchemyMultiMarketRepository(create_session_factory(engine))
    with repo._sessions.begin() as session:
        repo._market(session, MarketKeyV2(market_type="CORRECT_SCORE"))
    with pytest.raises(RuntimeError, match="populated"):
        command.downgrade(cfg, "28415c7e39ba")
    with engine.connect() as c:
        assert (
            c.scalar(text("SELECT version_num FROM alembic_version")) == "39526d8f40cb"
        )
        assert not c.execute(text("PRAGMA foreign_key_check")).all()
        assert set(market_v2_triggers()) <= set(
            c.scalars(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
        )
    engine.dispose()


def test_real_v070_code_database_upgrade_preserves_all_old_values_and_v1_operations(
    tmp_path,
):
    # Git's immutable release tree, not the new models pointed at an old head.
    old = tmp_path / "old"
    old.mkdir()
    archive = subprocess.check_output(
        [
            "git",
            "archive",
            "v0.7.0",
            "src",
            "config",
            "data/fixtures",
            "migrations",
            "alembic.ini",
            "pyproject.toml",
            "README.md",
        ],
        cwd=ROOT,
    )
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    db = tmp_path / "old.db"
    env = dict(os.environ, PYTHONPATH=str(old / "src"), PYTHONIOENCODING="utf-8")
    code = r"""
import asyncio,json,sys
from pathlib import Path
from datetime import timedelta
from football_system.interfaces.cli import main
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.database.strategy_pass_repository import SqlAlchemyStrategyPassRepository
from football_system.application.strategy_pass import StrategyPassService
from football_system.domain.strategy_pass import StrategyProfileV1
from football_system.domain.betting import PassType
import football_system
assert football_system.__version__=="0.7.0" and Path(sys.argv[2]) in Path(football_system.__file__).parents
url="sqlite:///"+Path(sys.argv[1]).as_posix()
assert main(["--database-url",url,"--analysis-run-id","upgrade-v070","--budget-yuan","100"])==0
engine=create_database_engine(url); repo=SqlAlchemyStrategyPassRepository(create_session_factory(engine)); service=StrategyPassService(repo)
for value in ("2X1","3X4","4X11"):
    plan=service.create("ANALYSIS_RUN","upgrade-v070",10000,profile=StrategyProfileV1(pass_types=(PassType(value),)))
    assert repo.load_plan(plan.plan_id)==plan
engine.dispose()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(db), str(old)],
        cwd=old,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before = database_snapshot(db)
    assert not set(MARKET_V2_TABLES) & set(before[0])
    cfg = config_for(db)
    command.upgrade(cfg, "head")
    command.check(cfg)
    after = database_snapshot(db)
    for index in range(3):
        assert all(after[index][key] == value for key, value in before[index].items())
    engine = create_database_engine(f"sqlite:///{db.as_posix()}")
    repo = SqlAlchemyStrategyPassRepository(create_session_factory(engine))
    with engine.connect() as c:
        ids = tuple(c.scalars(text("SELECT plan_id FROM strategy_pass_plans")))
    assert len(ids) == 3
    for identity in ids:
        plan = repo.load_plan(identity)
        assert (
            build_strategy_plan(plan.source, plan.profile, plan.role_requests) == plan
        )
        assert repo.save_plan(plan) == plan
    from football_system.application.strategy_pass import StrategyPassService
    from football_system.interfaces.cli import main

    for identity in ids:
        assert (
            main(
                [
                    "strategy-pass",
                    "show",
                    "--database-url",
                    f"sqlite:///{db.as_posix()}",
                    "--plan-id",
                    identity,
                ]
            )
            == 0
        )
        # Missing-result V1 semantics still work and retain original graph.
        plan = repo.load_plan(identity)
        settled = StrategyPassService(repo).settle(
            identity, (), plan.source.parent.as_of_at_utc + timedelta(days=4)
        )
        assert settled.reason in {"MISSING_RESULT", "SETTLED"}
    engine.dispose()
