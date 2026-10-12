"""Separate-process transaction adversary, self-authored test databases only."""
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys

from sqlalchemy.exc import OperationalError
from football_system.application.portfolio_requests import REQUEST_TYPES as PF_REQUESTS
from football_system.application.real_bridge_requests import REQUEST_TYPES as RB_REQUESTS
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.real_bridge_sources import ExistingPinnedModelAccess
from football_system.infrastructure.database.portfolio_repository import SqlAlchemyPortfolioRepository
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.interfaces.production_quant_cli import production_inference_context
from scripts.scoped_model_acceptance_inputs import Clock, BridgeClock


def main():
    job=json.loads(sys.stdin.readline())
    assert job["classification"]=="SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE"
    def denied(*args,**kwargs):
        raise AssertionError("PROCESS_ACCEPTANCE_NETWORK_FORBIDDEN")
    socket.socket.connect=denied
    root=Path(job["root"])
    assert (root/"source-A/evidence.json").is_file()
    clock=Clock(datetime.fromisoformat(job["clock"]))
    engine=create_database_engine("sqlite:///"+(root/"isolated-scoped.sqlite").as_posix())
    sessions=create_session_factory(engine)
    evidence=BridgeEvidence(root,trusted_authorities=job["authorities"])
    _,_,_,inference,_=production_inference_context(sessions,evidence=evidence,operator_id="synthetic-operator",clock=clock)
    bridge=SqlAlchemyRealBridgeRepository(sessions,evidence_root=root,clock=BridgeClock(clock),model_access=ExistingPinnedModelAccess(inference))
    repository=SqlAlchemyPortfolioRepository(bridge,budget_evidence=evidence) if job["kind"]=="PF" else bridge
    if job.get("crash"):
        original=repository._store
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            os._exit(73)  # Deliberate process death after seals, before COMMIT.
        repository._store=crash
    print("READY",flush=True)
    assert sys.stdin.readline().strip()=="START"
    types=PF_REQUESTS if job["kind"]=="PF" else RB_REQUESTS
    try:
        result=repository.execute(job["operation"],types[job["operation"]].model_validate(job["request"]))
        print(json.dumps(dict(outcome="COMMITTED",artifact_id=result.artifact_id)),flush=True)
    except (ValueError,OperationalError) as error:
        print(json.dumps(dict(outcome="REJECTED",reason=str(error))),flush=True)
    finally:
        engine.dispose()


if __name__=="__main__":
    main()
