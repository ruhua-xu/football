"""FAST / INTEGRATION / FULL_RELEASE development feedback with exact proof reuse."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from devtools.evidence import digest,identity,verified_reuse,workspace_identity  # noqa: E402
from devtools.measure import hardware,reasonable_workers  # noqa: E402

FREEZE=["tests/contract/test_p0_catalog_scope_gate.py","tests/contract/test_v120_release_gate.py",
    "tests/contract/test_v130_release_gate.py","tests/contract/test_v131_release_gate.py",
    "tests/contract/test_scoped_compatibility.py","tests/contract/test_portfolio_compatibility.py"]
PATTERNS={
    "devtools":dict(fast=["devtools/tests"],integration=["devtools/tests","tests/integration/test_database_schema.py",
        "tests/integration/test_sqlite_checkout_lifecycle.py","tests/integration/test_migration_url_encoding.py","tests/integration/test_portfolio_processes.py","tests/e2e/test_wheel_e2e_script.py"]),
    "portfolio":dict(fast=["tests/contract/test_pfa_ownership_model.py","tests/unit/test_strategy_pass*.py","tests/unit/test_return*.py"],
        integration=["tests/integration/test_portfolio*.py","tests/integration/test_real_bridge_repository.py","tests/e2e/test_wheel_e2e_script.py"]),
    "scoped":dict(fast=["tests/unit/test_openfootball_scope.py","tests/contract/test_openfootball_observed.py"],
        integration=["tests/integration/test_scoped*.py","tests/integration/test_ofp_real*.py"]),
    "observed":dict(fast=["tests/unit/test_observed*.py","tests/contract/test_sportmonks_observed.py"],
        integration=["tests/integration/test_observed*.py","tests/e2e/test_observed_training_cli.py"]),
    "correction":dict(fast=["tests/unit/test_training_correction*.py"],
        integration=["tests/integration/test_training_correction*.py","tests/integration/test_corrected_quant_workflow.py","tests/e2e/test_training_correction_cli.py"]),
    "catalog":dict(fast=["tests/unit/test_identity*.py","tests/contract/test_fixture_manual_provider.py","tests/integration/test_migration_url_encoding.py"],
        integration=["tests/integration/test_identity*.py","tests/integration/test_fixture*.py","tests/integration/test_p0*.py","tests/e2e/test_live_cli.py"]),
}


def selections(mode,area):
    if mode=="FULL_RELEASE":
        if area is not None:
            raise ValueError("FULL_RELEASE_CANNOT_BE_NARROWED_BY_AREA")
        return ["tests","devtools/tests"]
    if area not in PATTERNS:
        raise ValueError("EXPLICIT_AFFECTED_AREA_REQUIRED")
    result=set(FREEZE)
    for pattern in PATTERNS[area]["fast" if mode=="FAST" else "integration"]:
        found=list(ROOT.glob(pattern))
        if not found:
            raise ValueError("TEST_SELECTION_NOT_FOUND:"+pattern)
        result.update(p.relative_to(ROOT).as_posix() for p in found)
    return sorted(result)


def run_command(label,command,directory,env):
    started=time.perf_counter()
    with (directory/(label+".log")).open("xb") as stream:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=21600)
    return dict(step=label,command=list(map(str,command)),returncode=result.returncode,wall_seconds=round(time.perf_counter()-started,3))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=("FAST","INTEGRATION","FULL_RELEASE"))
    parser.add_argument("--area",choices=tuple(PATTERNS))
    parser.add_argument("--evidence-root",type=Path,required=True)
    parser.add_argument("--temp-root",type=Path,required=True)
    parser.add_argument("--workers",default=None)
    parser.add_argument("--dist",choices=("load","worksteal"),default=None)
    parser.add_argument("--seed",type=int)
    parser.add_argument("--boundary",type=Path)
    parser.add_argument("--reuse",action="store_true")
    parser.add_argument("--plan",action="store_true")
    args=parser.parse_args()
    if args.dist is None:
        args.dist="worksteal" if args.mode=="FULL_RELEASE" else "load"
    targets=selections(args.mode,args.area)
    if args.mode=="FULL_RELEASE" and args.reuse:
        parser.error("FULL_RELEASE always executes its complete gates; test-pass reuse is development-only")
    workers=int(args.workers) if args.workers is not None else (0 if args.mode=="FAST" else reasonable_workers(hardware()))
    if not 0<=workers<=len(hardware()["cpu_affinity"]):
        parser.error("worker count exceeds available CPU affinity")
    configuration=dict(mode=args.mode,area=args.area,targets=targets,workers=workers,dist=args.dist,seed=args.seed,
        pytest_flags=["-p no:cacheprovider","-p no:faulthandler","-p devtools.pytest_metrics"],
        boundary_sha256=digest(args.boundary.read_bytes()) if args.boundary else None,
        release_pass_reuse_allowed=False)
    if args.plan:
        print(json.dumps(dict(configuration=configuration,steps=["ruff","pytest","scope-safety"]+
            (["compile","fresh-migration","wheel-cache-verify-or-build","isolated-wheel-e2e","whitespace"] if args.mode=="FULL_RELEASE" else [])),indent=2))
        return 0
    assert args.evidence_root.is_dir() and args.temp_root.is_dir()
    assert not any((p/".git").exists() for p in (args.temp_root.resolve(),*args.temp_root.resolve().parents))
    actual,key=identity(ROOT,configuration)
    prior=args.evidence_root/key
    if args.reuse:
        proof=verified_reuse(prior,actual,mode=args.mode)
        if proof:
            print(json.dumps(dict(status="REUSED_EXACT_DEVELOPMENT_PASS",identity_key=key,original_receipt=str(prior/"receipt.json"),
                original_completed_at_utc=proof["completed_at_utc"],release_authorization=False),indent=2))
            return 0
    output=prior
    if output.exists():
        output=args.evidence_root/(key+"-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    output.mkdir(exist_ok=False)
    sandbox=args.temp_root/("gate-"+digest(str(output).encode())[:8])
    assert not sandbox.exists()
    env=os.environ.copy()
    for name in ("PYTHONPATH","PYTHONHOME","PYTEST_ADDOPTS","PYTEST_PLUGINS"):
        env.pop(name,None)
    env.update(PYTHONUTF8="1",PYTHONDONTWRITEBYTECODE="1",PYTHONUNBUFFERED="1",PYTHONNOUSERSITE="1")
    pytest=[sys.executable,"-B","-m","pytest","-q","-p","no:cacheprovider","-p","no:faulthandler","-p","devtools.pytest_metrics",
        "--basetemp",str(sandbox),"--junitxml",str(output/"junit.xml"),"--speed-metrics-dir",str(output/"metrics")]
    if workers:
        pytest += ["-n",str(workers),"--dist",args.dist]
    if args.seed is not None:
        pytest += ["--speed-order-seed",str(args.seed)]
    pytest += targets
    safety=[sys.executable,"-I","-B",str(ROOT/"devtools/safety.py")]
    if args.boundary:
        safety += ["--boundary",str(args.boundary)]
    commands=[("ruff",[sys.executable,"-I","-m","ruff","check","."]), ("pytest",pytest),("scope-safety",safety)]
    if args.mode=="FULL_RELEASE":
        commands += [
            ("compile",[sys.executable,"-I","-m","compileall","-q","src","tests","migrations","scripts","devtools"]),
            ("fresh-migration",[sys.executable,"-I","-B",str(ROOT/"devtools/release_steps.py"),"migration","--work",str(sandbox/"fresh-migration")]),
            ("wheel",[sys.executable,"-I","-B",str(ROOT/"devtools/wheel_cache.py"),"--cache",str(args.evidence_root/"wheels"),"--receipt",str(output/"wheel.json")]),
            ("isolated-wheel",[sys.executable,"-I","-B",str(ROOT/"devtools/release_steps.py"),"installed-wheel","--work",str(sandbox/"wheel"),"--wheel-receipt",str(output/"wheel.json")]),
            ("whitespace",["git","diff","--check", "dcc83d1e82f585ae2b4827b0ee7727422b24942a","HEAD"]),
        ]
    start=time.perf_counter()
    results=[]
    for name,command in commands:
        result=run_command(name,command,output,env)
        results.append(result)
        print(json.dumps(dict(step=name,returncode=result["returncode"],wall_seconds=result["wall_seconds"])),flush=True)
        if result["returncode"]:
            break
    junit=output/"junit.xml"
    cases=list(ET.parse(junit).iter("testcase")) if junit.is_file() else []
    good=len(results)==len(commands) and all(r["returncode"]==0 for r in results) and bool(cases)
    # A passing test run on a changing tree is not reusable evidence.
    unchanged=workspace_identity(ROOT)==actual["workspace"]
    good &= unchanged
    artifacts=[dict(path=p.relative_to(output).as_posix(),sha256=digest(p.read_bytes())) for p in sorted(output.rglob("*")) if p.is_file()]
    receipt=dict(status="PASS" if good else "FAIL",identity=actual,identity_key=key,steps=results,
        wall_seconds=round(time.perf_counter()-start,3),completed_at_utc=datetime.now(timezone.utc).isoformat(),
        tests=len(cases),failed=sum(c.find("failure") is not None or c.find("error") is not None for c in cases),
        skipped=sum(c.find("skipped") is not None for c in cases),workspace_unchanged_during_run=unchanged,
        reusable_for_development=good and actual["workspace"]["clean"] and args.mode!="FULL_RELEASE",artifacts=artifacts,release_authorization=False)
    (output/"receipt.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    print(json.dumps(dict(status=receipt["status"],mode=args.mode,wall_seconds=receipt["wall_seconds"],tests=len(cases),receipt=str(output/"receipt.json")),indent=2))
    return 0 if good else 1


if __name__=="__main__":
    raise SystemExit(main())
