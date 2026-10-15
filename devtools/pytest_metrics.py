"""Opt-in timing/collection telemetry; never caches or changes application results."""
from collections import defaultdict
import functools
import hashlib
import importlib
import json
from pathlib import Path
import random
import threading
import time

import pytest

_stats=defaultdict(lambda:dict(calls=0,inclusive_seconds=0.0,exclusive_seconds=0.0))
_stack=threading.local()
_restores=[]
_reports=[]
_worker="main"


def pytest_addoption(parser):
    group=parser.getgroup("development-speed")
    group.addoption("--speed-metrics-dir",default=None)
    group.addoption("--speed-order-seed",type=int,default=None)
    group.addoption("--speed-profile-init",action="store_true",default=False)


def wrap(owner,name,label):
    original=getattr(owner,name)
    @functools.wraps(original)
    def measured(*args,**kwargs):
        stack=getattr(_stack,"values",None)
        if stack is None:
            stack=_stack.values=[]
        frame=[time.perf_counter(),0.0]
        stack.append(frame)
        try:
            return original(*args,**kwargs)
        finally:
            elapsed=time.perf_counter()-frame[0]
            stack.pop()
            if stack:
                stack[-1][1]+=elapsed
            value=_stats[label]
            value["calls"]+=1
            value["inclusive_seconds"]+=elapsed
            value["exclusive_seconds"]+=elapsed-frame[1]
    setattr(owner,name,measured)
    _restores.append((owner,name,original))


def pytest_configure(config):
    global _worker
    _worker=getattr(config,"workerinput",{}).get("workerid","main")
    if not config.getoption("speed_metrics_dir"):
        return
    config._speed_started=time.perf_counter()
    if config.getoption("speed_profile_init"):
        for module,attribute,name in (
            ("football_system.infrastructure.database.session",None,"create_schema"),
            ("alembic.command",None,"upgrade"),
            ("football_system.infrastructure.database.observed_training_repository","SqlAlchemyObservedTrainingRepository","load_context"),
            ("football_system.infrastructure.database.quant_integrity_repository","SqlAlchemyQuantIntegrityRepository","_terminal_graph"),
            ("football_system.domain.services.elo_baseline","EloThreeWayBaseline","rebuild_state"),
            ("football_system.interfaces.cli",None,"main"),
            ("subprocess",None,"run"),
        ):
            obj=importlib.import_module(module)
            if attribute:
                obj=getattr(obj,attribute)
            wrap(obj,name,module+"."+(attribute+"." if attribute else "")+name)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config,items):
    seed=config.getoption("speed_order_seed")
    if seed is not None:
        random.Random(seed).shuffle(items)
    config._speed_nodes=[item.nodeid for item in items]


def pytest_runtest_logreport(report):
    _reports.append(dict(nodeid=report.nodeid,phase=report.when,seconds=report.duration,outcome=report.outcome,
        worker=getattr(report,"worker_id",_worker),start=getattr(report,"start",None),stop=getattr(report,"stop",None)))


def pytest_sessionfinish(session,exitstatus):
    config=session.config
    directory=config.getoption("speed_metrics_dir")
    if not directory:
        return
    worker=getattr(config,"workerinput",{}).get("workerid","main")
    nodes=getattr(config,"_speed_nodes",[])
    if not nodes and hasattr(config,"_speed_xdist_nodes"):
        nodes=config._speed_xdist_nodes
    result=dict(worker=worker,exitstatus=int(exitstatus),elapsed_seconds=time.perf_counter()-config._speed_started,
        order_seed=config.getoption("speed_order_seed"),collection=nodes,
        collection_set_sha256=hashlib.sha256(json.dumps(sorted(nodes),ensure_ascii=True).encode()).hexdigest(),
        collection_order_sha256=hashlib.sha256(json.dumps(nodes,ensure_ascii=True).encode()).hexdigest(),
        reports=_reports,operations=dict(_stats),
        attribution="inclusive operation times overlap; exclusive times subtract measured nested spans, not all unmeasured calls")
    output=Path(directory)
    output.mkdir(parents=True,exist_ok=True)
    with (output/(worker+".json")).open("x",encoding="utf-8") as f:
        json.dump(result,f,indent=2)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node,ids):
    node.config._speed_xdist_nodes=list(ids)


def pytest_unconfigure(config):
    for owner,name,value in reversed(_restores):
        setattr(owner,name,value)
