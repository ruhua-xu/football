"""Collect (never execute) the unchanged application suite and verify its node set."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--baseline-junit",type=Path)
    args=parser.parse_args()
    assert args.output.parent.is_dir()
    args.output.mkdir(exist_ok=False)
    process=subprocess.run([sys.executable,"-B","-m","pytest","--collect-only","-q","-p","no:cacheprovider",
        "-p","devtools.pytest_metrics","--speed-metrics-dir",str(args.output/"metrics"),"tests"],cwd=ROOT,capture_output=True,timeout=120)
    (args.output/"collection.log").write_bytes(process.stdout+process.stderr)
    assert process.returncode==0
    result=json.loads((args.output/"metrics/main.json").read_bytes())
    baseline_match=None
    if args.baseline_junit:
        nodes=[]
        for case in ET.parse(args.baseline_junit).iter("testcase"):
            parts=case.attrib["classname"].split(".")
            end=next(i+1 for i,p in enumerate(parts) if p.startswith("test_"))
            nodes.append("/".join(parts[:end])+".py::"+"::".join([*parts[end:],case.attrib["name"]]))
        assert sorted(nodes)==sorted(result["collection"]),"APPLICATION_TEST_COLLECTION_CHANGED"
        baseline_match=True
    summary=dict(status="PASS_COLLECTION_ONLY",tests=len(result["collection"]),collection_set_sha256=result["collection_set_sha256"],
        baseline_node_set_identical=baseline_match,baseline_junit_sha256=hashlib.sha256(args.baseline_junit.read_bytes()).hexdigest() if args.baseline_junit else None)
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
