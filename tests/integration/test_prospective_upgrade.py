"""X/Y: old code creates a genuine v0.9 database; new migration is additive only."""

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

from football_system.application.prospective_requests import EpochRequestV1
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.database.prospective_schema import PROSPECTIVE_TABLES
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from scripts.market_expansion_acceptance import DECISION, KICKOFF
from tests.integration.test_market_v2_upgrade import config_for, database_snapshot

ROOT = Path(__file__).resolve().parents[2]

# Exact, reviewed adapter entry point; no old computation is excluded from the
# comparison. Keep this independently spelled out instead of ignoring a file.
APPROVED_OPENFOOTBALL_HOOK = b'\n    def run_openfootball(self, production_repository, *, request_key):\n        """Explicit versioned adapter for the OpenFootball observed source graph."""\n        return production_repository.openfootball_binding().run_pilot(request_key=request_key)\n'


def test_fresh_migration_check_and_empty_downgrade(tmp_path):
    path = tmp_path / "fresh.db"
    cfg = config_for(path)
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "4a637e9051dc")
    assert not set(PROSPECTIVE_TABLES) & set(database_snapshot(path)[0])
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_x_y_v090_upgrade_preserves_all_old_rows_definitions_and_replay(tmp_path):
    from datetime import timedelta
    old = tmp_path / "v090"
    old.mkdir()
    archive = subprocess.check_output(["git", "archive", "v0.9.0", "src", "config", "data/fixtures", "migrations",
        "scripts/market_expansion_acceptance.py", "alembic.ini", "pyproject.toml", "README.md"], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(archive)) as stream:
        stream.extractall(old, filter="data")
    path = tmp_path / "old.db"
    code = r'''
import json,sys
from pathlib import Path
import football_system
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.files.return_distribution import default_return_configuration
from football_system.application.return_distribution import ReturnDistributionService,OptimizeReturnRequestV1
from football_system.application.market_v2 import PlanRequestV2
from scripts.market_expansion_acceptance import seed_environment,build_fixture_analysis,request_for_counts
assert football_system.__version__=='0.9.0' and Path(sys.argv[2]) in Path(football_system.__file__).parents
url='sqlite:///'+Path(sys.argv[1]).as_posix()
upgrade_database(url,Path(sys.argv[2])/'alembic.ini')
engine=create_database_engine(url); sessions=create_session_factory(engine)
_,history=seed_environment(sessions)
market,analysis,packet,review,fusion=build_fixture_analysis(sessions,history)
plan=market.plan(PlanRequestV2(fusion_id=fusion.artifact_id,budget_fen=10000,requests=(request_for_counts((1,2)),)))
policy,objective=default_return_configuration()
repo=SqlAlchemyReturnDistributionRepository(sessions)
run=ReturnDistributionService(repo,policy,objective).optimize(OptimizeReturnRequestV1(plan_id=plan.artifact_id))
print(json.dumps({'plan':plan.artifact_id,'optimizer':run.artifact_id,'optimizer_json':run.model_dump_json()}))
engine.dispose()
'''
    completed = subprocess.run([sys.executable, "-B", "-c", code, str(path), str(old)], cwd=old,
        env=dict(os.environ, PYTHONPATH=str(old / "src"), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    identity = json.loads(completed.stdout.splitlines()[-1])
    before = database_snapshot(path)
    assert not set(PROSPECTIVE_TABLES) & set(before[0])
    cfg = config_for(path)
    command.upgrade(cfg, "head")
    command.check(cfg)
    after = database_snapshot(path)
    for section in range(3):
        assert all(after[section][key] == value for key, value in before[section].items())
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    sessions = create_session_factory(engine)
    rd = SqlAlchemyReturnDistributionRepository(sessions)
    optimizer = rd.load(identity["optimizer"])
    assert optimizer.model_dump_json() == identity["optimizer_json"]
    assert rd.save(optimizer) == optimizer
    clock = SyntheticProspectiveClock(DECISION+timedelta(minutes=1))
    repo = SqlAlchemyProspectiveRepository(sessions, clock=clock)
    epoch = repo.create_epoch(EpochRequestV1(request_key="upgraded-epoch", seed_plan_id=identity["plan"], name="upgrade-fixture",
        mode="SYNTHETIC", starts_at_utc=clock.now(), ends_at_utc=KICKOFF+timedelta(days=1), result_source_identity="synthetic"))
    with pytest.raises(RuntimeError, match="populated prospective ledger"):
        command.downgrade(cfg, "4a637e9051dc")
    assert repo.load(epoch.artifact_id) == epoch
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "5b748fa162ed"
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()
    final = database_snapshot(path)
    for section in range(3):
        assert all(final[section][key] == value for key, value in before[section].items())
    engine.dispose()


def test_v090_domain_application_configuration_and_migrations_are_byte_frozen():
    # All pre-existing domain/application/config/migration files are frozen;
    # the new feature is additive. CRLF checkout conversion is not a code change.
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "v0.9.0", "src/football_system/domain",
        "src/football_system/application", "config", "migrations"], cwd=ROOT, text=True).splitlines()
    assert len(names) > 119
    for name in names:
        expected = subprocess.check_output(["git", "show", "v0.9.0:"+name], cwd=ROOT)
        actual = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        if name == "src/football_system/application/quant_integrity.py":
            assert actual.count(APPROVED_OPENFOOTBALL_HOOK) == 1
            actual = actual.replace(APPROVED_OPENFOOTBALL_HOOK, b"", 1)
        assert actual == expected.replace(b"\r\n", b"\n"), name
