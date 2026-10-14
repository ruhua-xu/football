"""Run a fixed pytest selection with hardware, phase timings and wall-clock receipts."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import psutil

ROOT=Path(__file__).resolve().parents[1]


def hardware():
    process=psutil.Process()
    affinity=process.cpu_affinity() if hasattr(process,"cpu_affinity") else list(range(os.cpu_count() or 1))
    memory=psutil.virtual_memory()
    return dict(os=platform.platform(),machine=platform.machine(),cpu_logical=psutil.cpu_count(),cpu_physical=psutil.cpu_count(logical=False),
        cpu_affinity=affinity,memory_total_bytes=memory.total,memory_available_bytes=memory.available,
        runner_image=os.environ.get("ImageOS"),runner_image_version=os.environ.get("ImageVersion"),
        python=sys.version,sqlite=__import__("sqlite3").sqlite_version)


def reasonable_workers(info):
    # Conservative memory budget until measured peak RSS supports a different cap.
    return max(1,min(4,len(info["cpu_affinity"]),int(info["memory_available_bytes"]/(3*1024**3))))


def environment():
    return {d.metadata["Name"].lower().replace("_","-"):d.version for d in importlib.metadata.distributions()}


def git(*args):return subprocess.check_output(["git",*args],cwd=ROOT).decode("utf-8").strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--temp-root",type=Path,required=True)
    parser.add_argument("--workers",default="2")
    parser.add_argument("--dist",choices=("load","worksteal"),default="load")
    parser.add_argument("--seed",type=int)
    parser.add_argument("--profile-init",action="store_true")
    parser.add_argument("--expected-count",type=int)
    parser.add_argument("--timeout",type=int,default=21600)
    parser.add_argument("targets",nargs="*")
    args=parser.parse_args()
    assert args.output.parent.is_dir() and args.temp_root.is_dir()
    assert ROOT not in args.temp_root.resolve().parents and args.temp_root.resolve()!=ROOT
    assert not any((p/".git").exists() for p in (args.temp_root.resolve(),*args.temp_root.resolve().parents)),"raw test inputs must be outside public Git"
    args.output.mkdir(exist_ok=False)
    info=hardware()
    workers=reasonable_workers(info) if args.workers=="capacity" else int(args.workers)
    assert workers>=0 and workers<=len(info["cpu_affinity"])
    sandbox=args.temp_root/("cs-"+hashlib.sha256(str(args.output.resolve()).encode()).hexdigest()[:8])
    assert not sandbox.exists(),"test sandbox must be new"
    command=[sys.executable,"-B","-m","pytest","-q","-p","no:cacheprovider","-p","no:faulthandler","-p","devtools.pytest_metrics",
        "--basetemp",str(sandbox),"--junitxml",str(args.output/"junit.xml"),"--speed-metrics-dir",str(args.output/"metrics")]
    if workers:
        command += ["-n",str(workers),"--dist",args.dist]
    if args.seed is not None:
        command += ["--speed-order-seed",str(args.seed)]
    if args.profile_init:
        command += ["--speed-profile-init"]
    command += args.targets or ["tests"]
    env=os.environ.copy()
    for key in ("PYTHONPATH","PYTHONHOME","PYTEST_ADDOPTS","PYTEST_PLUGINS"):
        env.pop(key,None)
    env.update(PYTHONUTF8="1",PYTHONDONTWRITEBYTECODE="1",PYTHONUNBUFFERED="1",PYTHONNOUSERSITE="1")
    started=datetime.now(timezone.utc).isoformat()
    receipt=dict(commit=git("rev-parse","HEAD"),tree=git("rev-parse","HEAD^{tree}"),dirty=bool(git("status","--porcelain")),
        tracked_diff_sha256=hashlib.sha256(subprocess.check_output(["git","diff","--binary","HEAD"],cwd=ROOT)).hexdigest(),
        measurement_tool_sha256={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path(__file__).with_name("pytest_metrics.py"))},
        hardware=info,packages=environment(),workers=workers,worker_request=args.workers,dist=args.dist,seed=args.seed,
        command=command,started_at_utc=started,targets=args.targets or ["tests"],profile_init=args.profile_init,
        temp_sandbox=str(sandbox),measurement_kind="TARGETED_PROFILE" if args.profile_init else "PYTEST_BENCHMARK")
    (args.output/"started.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    stop=threading.Event()
    samples=[]
    began=time.perf_counter()
    with (args.output/"pytest.log").open("xb") as stream:
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        def monitor():
            while not stop.wait(2):
                try:
                    parent=psutil.Process(process.pid)
                    processes=[parent,*parent.children(recursive=True)]
                    memory=0
                    for child in processes:
                        try:
                            memory+=child.memory_info().rss
                        except (psutil.NoSuchProcess,psutil.AccessDenied):
                            pass
                    samples.append(dict(elapsed=time.perf_counter()-began,rss_tree_bytes=memory,processes=len(processes)))
                except psutil.NoSuchProcess:
                    break
        thread=threading.Thread(target=monitor,daemon=True)
        thread.start()
        try:
            returncode=process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            parent=psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
            returncode=124
        finally:
            stop.set()
            thread.join(timeout=5)
    elapsed=time.perf_counter()-began
    junit=args.output/"junit.xml"
    cases=list(ET.parse(junit).iter("testcase")) if junit.is_file() else []
    counts=dict(total=len(cases),failed=sum(c.find("failure") is not None for c in cases),errors=sum(c.find("error") is not None for c in cases),
        skipped=sum(c.find("skipped") is not None for c in cases))
    counts["passed"]=counts["total"]-counts["failed"]-counts["errors"]-counts["skipped"]
    collection=[]
    metric_files=sorted((args.output/"metrics").glob("*.json"))
    sets=set()
    for file in metric_files:
        metric=json.loads(file.read_bytes())
        if metric["collection"]:
            collection=metric["collection"]
            sets.add(metric["collection_set_sha256"])
    assert len(sets)<=1,"workers collected different test sets"
    good=returncode==0 and not counts["failed"] and not counts["errors"] and bool(cases)
    if args.expected_count is not None:
        good &= counts["total"]==args.expected_count
    receipt.update(status="PASS" if good else "FAIL",returncode=returncode,wall_seconds=round(elapsed,3),counts=counts,
        finished_at_utc=datetime.now(timezone.utc).isoformat(),peak_tree_rss_bytes=max((s["rss_tree_bytes"] for s in samples),default=0),
        collection_set_sha256=next(iter(sets),None),collected_tests=len(collection),
        junit_sha256=hashlib.sha256(junit.read_bytes()).hexdigest() if junit.is_file() else None)
    (args.output/"resources.json").write_text(json.dumps(samples,indent=2),encoding="utf-8")
    (args.output/"receipt.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    print(json.dumps({k:receipt[k] for k in ("status","workers","dist","wall_seconds","peak_tree_rss_bytes","counts","collection_set_sha256")},indent=2))
    raise SystemExit(0 if good else 1)


if __name__=="__main__":
    main()
