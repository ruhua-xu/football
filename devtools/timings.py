"""Extract existing JUnit timings and CI step envelopes without running tests."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import xml.etree.ElementTree as ET


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module_name(classname):
    parts=classname.split(".")
    end=next((i+1 for i,p in enumerate(parts) if p.startswith("test_")),len(parts))
    return "/".join(parts[:end])+".py",parts[end:]


def junit(path):
    rows=[]
    for case in ET.parse(path).iter("testcase"):
        module,classes=module_name(case.get("classname",""))
        nodeid=module+"::"+"::".join([*classes,case.get("name","")])
        status=next((tag for tag in ("failure","error","skipped") if case.find(tag) is not None),"passed")
        rows.append(dict(nodeid=nodeid,module=module,category=module.split("/")[1] if module.startswith("tests/") else "other",
            seconds=float(case.get("time","0")),status=status))
    assert len({r["nodeid"] for r in rows})==len(rows),"duplicate JUnit node IDs"
    return rows


def aggregate(rows,key):
    groups=defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    result=[]
    for name,values in groups.items():
        durations=[r["seconds"] for r in values]
        result.append(dict(name=name,tests=len(values),seconds=round(sum(durations),3),maximum_seconds=max(durations),
            median_seconds=statistics.median(durations),passed=sum(v["status"]=="passed" for v in values),
            skipped=sum(v["status"]=="skipped" for v in values)))
    return sorted(result,key=lambda r:(-r["seconds"],r["name"]))


def instant(value):return datetime.fromisoformat(value.replace("Z","+00:00"))


def ci_steps(run):
    return [dict(job=j["name"],step=s["name"],status=s["conclusion"],
        seconds=(instant(s["completedAt"])-instant(s["startedAt"])).total_seconds()) for j in run["jobs"] for s in j["steps"]
        if s.get("startedAt") and s.get("completedAt") and not s["startedAt"].startswith("0001")]


def ci_module_envelopes(path,steps):
    """Timestamped progress boundaries are approximations, not per-test timings."""
    test_start=next(s["startedAt"] for j in steps["jobs"] for s in j["steps"] if s["name"]=="Test")
    previous=instant(test_start)
    groups=[]
    current=None
    for line in path.read_text(encoding="utf-8").splitlines():
        found=re.match(r"^(\d{4}-\d{2}-\d{2}T\S+Z)\s+(.*)$",line)
        if not found:
            continue
        at,text=instant(found[1]),found[2].strip()
        match=re.match(r"^(tests/\S+\.py)\s+[.sFxEX]+",text)
        if match:
            if current is not None:
                groups.append(current)
                previous=current["last_progress_at"]
            current=dict(module=match[1],start_envelope_at=previous,last_progress_at=at)
        elif current and re.fullmatch(r"[.sFxEX]+(?:\s+\[\s*\d+%\])?",text):
            current["last_progress_at"]=at
    if current:
        groups.append(current)
    unique={}
    for group in groups:
        elapsed=(group["last_progress_at"]-group["start_envelope_at"]).total_seconds()
        if elapsed>=0:
            value=dict(module=group["module"],approximate_envelope_seconds=round(elapsed,3))
            prior=unique.get(value["module"])
            if prior is None or elapsed<prior["approximate_envelope_seconds"]:
                unique[value["module"]]=value
    return sorted(unique.values(),key=lambda r:-r["approximate_envelope_seconds"])


def analyze(spec):
    source={k:Path(v) for k,v in spec.items() if k in {"junit","local_gates","ci_run","ci_summary","ci_log"}}
    local=json.loads(source["local_gates"].read_bytes())
    ci=json.loads(source["ci_summary"].read_bytes())
    run=json.loads(source["ci_run"].read_bytes())
    rows=junit(source["junit"])
    assert local["status"]=="PASS" and ci["status"]=="SUCCESS"
    assert local["identity"]["commit"]==ci["sha"]==spec["commit"]
    assert len(rows)==local["pytest"]["total"]
    return dict(label=spec["label"],commit=spec["commit"],identity=local["identity"],
        evidence={k:dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for k,p in source.items()},
        local_wall_seconds=local["pytest"]["elapsed_seconds"],local_workers=6,ci_pytest_summary=ci["pytest_summary"],
        junit_case_seconds_sum=round(sum(r["seconds"] for r in rows),3),test_count=len(rows),
        top100=sorted(rows,key=lambda r:(-r["seconds"],r["nodeid"]))[:100],modules=aggregate(rows,"module"),categories=aggregate(rows,"category"),
        local_steps=[dict(step=g["name"],seconds=g["elapsed_seconds"],returncode=g["returncode"]) for g in local["gates"]],
        ci_steps=ci_steps(run),ci_module_envelopes=ci_module_envelopes(source["ci_log"],run),
        limitations=["JUnit durations include setup/call/teardown; existing files have no separate phase/function spans",
            "Parallel case/module sums are worker-seconds, not wall-clock durations",
            "CI has no retained per-test JUnit; module progress timestamps are approximate envelopes",
            "Migration/replay/subprocess cost attribution needs targeted instrumentation; it cannot be inferred as exact from these logs",
            "P1-B/PF-B are distinct commits/suites/environments, not a controlled before/after optimization comparison"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    assert args.output.parent.is_dir()
    args.output.mkdir(exist_ok=False)
    results=[analyze(s) for s in json.loads(args.inputs.read_bytes())]
    for result in results:
        label=result["label"]
        (args.output/(label+".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        lines=[f"# {label}: slowest 100 recorded tests\n\n", "Seconds are JUnit setup+call+teardown worker time, not parallel wall time.\n\n",
            "| Rank | Seconds | Category | Node ID |\n|---:|---:|---|---|\n"]
        lines.extend(f"| {i} | {r['seconds']:.3f} | {r['category']} | `{r['nodeid'].replace('|','&#124;')}` |\n" for i,r in enumerate(result["top100"],1))
        (args.output/(label+"-top100.md")).write_text("".join(lines),encoding="utf-8")
        lines=[f"# {label}: module cumulative durations\n\n","| Module | Tests | Worker seconds | Maximum case seconds |\n|---|---:|---:|---:|\n"]
        lines.extend(f"| {r['name']} | {r['tests']} | {r['seconds']:.3f} | {r['maximum_seconds']:.3f} |\n" for r in result["modules"])
        (args.output/(label+"-modules.md")).write_text("".join(lines),encoding="utf-8")
    summary=[dict(label=r["label"],tests=r["test_count"],local_wall_seconds=r["local_wall_seconds"],
        worker_seconds=r["junit_case_seconds_sum"],ci_pytest=r["ci_pytest_summary"],categories=r["categories"],
        slowest_modules=r["modules"][:12],ci_steps=r["ci_steps"],local_steps=r["local_steps"]) for r in results]
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
