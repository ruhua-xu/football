"""RA16: a real v1.0 git archive builds the populated pre-bridge database."""

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from alembic import command
import pytest
from sqlalchemy import text

from football_system.application.real_bridge_requests import ProgramRequest
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.real_bridge_schema import RB_TABLES, INDEXES
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from scripts.market_expansion_acceptance import DECISION
from tests.integration.test_market_v2_upgrade import config_for, database_snapshot

ROOT=Path(__file__).resolve().parents[2]


def test_ra16_genuine_v100_additive_preservation_and_populated_refusal(tmp_path):
    old=tmp_path/"v100"
    old.mkdir()
    archive=subprocess.check_output(["git","archive","v1.0.0","src","config","data/fixtures","migrations","scripts/market_expansion_acceptance.py",
        "alembic.ini","pyproject.toml","README.md"],cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(archive)) as stream:
        stream.extractall(old,filter="data")
    path=tmp_path/"genuine-v100.db"
    code=r'''
import sys,json
from pathlib import Path
from datetime import timedelta
import football_system
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.files.return_distribution import default_return_configuration
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from football_system.application.return_distribution import ReturnDistributionService
from football_system.application.prospective import ProspectiveService
from football_system.application.prospective_requests import EpochRequestV1,PrepareProspectiveRequestV1,LockWorkflowRequestV1,ReportProspectiveRequestV1
from football_system.application.market_v2 import PlanRequestV2,BuildMultiAnalysisRequestV1,PacketRequestV4,FusionRequestV4
from football_system.domain.archive import canonical_json
from scripts.market_expansion_acceptance import seed_environment,build_fixture_analysis,DECISION,KICKOFF,request_for_counts
assert football_system.__version__=='1.0.0' and Path(sys.argv[2]) in Path(football_system.__file__).parents
url='sqlite:///'+Path(sys.argv[1]).as_posix()
upgrade_database(url,Path(sys.argv[2])/'alembic.ini')
engine=create_database_engine(url); sessions=create_session_factory(engine)
_,history=seed_environment(sessions)
market,original,*_=build_fixture_analysis(sessions,history)
analysis=market.build_analysis(BuildMultiAnalysisRequestV1(unit_ids=tuple(u.artifact_id for u in original.units if u.identity.match_id in {'mm-target-0','mm-target-1'} and u.market_key.market_type=='THREE_WAY'),budgets_fen=original.budgets_fen,rules=original.rules,constraints=original.constraints,min_selection_ev=original.min_selection_ev,min_ticket_roi=original.min_ticket_roi))
packet=market.packet(PacketRequestV4(analysis_id=analysis.artifact_id))
raw=canonical_json(dict(schema_version='LLM_REVIEW_V4',analysis_id=analysis.artifact_id,packet_id=packet.artifact_id,packet_hash=packet.content_hash,market_reviews=[dict(match_id=u.review_context.identity.match_id,market_key=u.review_context.market_key,review_context_id=u.review_context_id,review_context_hash=u.review_context_hash,status='UNAVAILABLE',failure_code='SKIPPED_DISABLED',limitations=['synthetic old-release fixture']) for u in packet.market_units])).encode()
review=market.review_import(canonical_json(packet).encode(),raw)
fusion=market.fusion(FusionRequestV4(analysis_id=analysis.artifact_id,review_id=review.artifact_id))
plan=market.plan(PlanRequestV2(fusion_id=fusion.artifact_id,budget_fen=10000,requests=(request_for_counts((1,1)),)))
clock=SyntheticProspectiveClock(DECISION+timedelta(minutes=1))
repo=SqlAlchemyProspectiveRepository(sessions,clock=clock)
policy,objective=default_return_configuration()
service=ProspectiveService(repo,market,ReturnDistributionService(SqlAlchemyReturnDistributionRepository(sessions),policy,objective),verify_manual=lambda _:None)
epoch=service.create_epoch(EpochRequestV1(request_key='old-epoch',seed_plan_id=plan.artifact_id,name='genuine v1.0 fixture',mode='SYNTHETIC',starts_at_utc=clock.now(),ends_at_utc=KICKOFF+timedelta(days=1),result_source_identity='test'))
run=service.prepare(PrepareProspectiveRequestV1(request_key='old-prepare',epoch_id=epoch.artifact_id,analysis_id=analysis.artifact_id,slate_key='old',slate_date=KICKOFF.date(),budget_fen=10000))
service.lock(LockWorkflowRequestV1(request_key='old-lock',run_id=run.artifact_id,strategy_requests=plan.requests))
report=service.report(ReportProspectiveRequestV1(request_key='old-report',epoch_id=epoch.artifact_id,as_of_at_utc=clock.now()))
print(json.dumps(dict(report=report.artifact_id,report_json=canonical_json(report))))
engine.dispose()
'''
    completed=subprocess.run([sys.executable,"-B","-c",code,str(path),str(old)],cwd=old,
        env=dict(os.environ,PYTHONPATH=str(old/"src"),PYTHONIOENCODING="utf-8",PYTHONDONTWRITEBYTECODE="1"),capture_output=True,text=True,encoding="utf-8",timeout=300)
    assert completed.returncode==0,completed.stdout+completed.stderr
    output=json.loads(completed.stdout.splitlines()[-1])
    before=database_snapshot(path)
    assert not set(before[0])&set(RB_TABLES)
    cfg=config_for(path)
    command.upgrade(cfg,"head")
    command.check(cfg)
    after=database_snapshot(path)
    for section in range(3):
        assert all(after[section][name]==value for name,value in before[section].items())
    engine=create_database_engine(f"sqlite:///{path.as_posix()}")
    sessions=create_session_factory(engine)
    from football_system.domain.archive import canonical_json
    assert canonical_json(SqlAlchemyProspectiveRepository(sessions).load(output["report"]))==output["report_json"]
    bridge=SqlAlchemyRealBridgeRepository(sessions,evidence_root=tmp_path,clock=SyntheticProspectiveClock(DECISION))
    bridge.execute("program",ProgramRequest(request_key="program",observation_program_id="upgrade-software-only",competition_ids=("test",),season_id="2026/27",
        scope_reference="SYNTHETIC_SOFTWARE_ACCEPTANCE",provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE"))
    full=database_snapshot(path)
    with pytest.raises(RuntimeError,match="populated real bridge"):
        command.downgrade(cfg,"5b748fa162ed")
    assert database_snapshot(path)==full
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version"))=="6c859ab273fe"
        assert connection.execute(text("PRAGMA foreign_key_check")).all()==[]
        indexes=dict(connection.execute(text("SELECT name,sql FROM sqlite_master WHERE type='index' AND name LIKE 'ux_rb_%'")).all())
        # Alembic cannot reflect SQLite expression indexes; verify every DDL exactly.
        assert set(indexes.values())==set(INDEXES)
    engine.dispose()


def test_ra16_empty_bridge_roundtrip(tmp_path):
    path=tmp_path/"fresh.db"
    cfg=config_for(path)
    command.upgrade(cfg,"5b748fa162ed")
    before=database_snapshot(path)
    command.upgrade(cfg,"head")
    command.check(cfg)
    command.downgrade(cfg,"5b748fa162ed")
    assert database_snapshot(path)==before
    command.upgrade(cfg,"head")
    command.check(cfg)
