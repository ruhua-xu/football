"""Audit history and migration files without checkout or database mutation."""
import argparse
import ast
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tarfile

RELEASE="f689ff7340598fd37f061473383461117ecb637d"
P1B="27808d8e4228f197e4fa9f28cb651567895795bb"
PFB="dcc83d1e82f585ae2b4827b0ee7727422b24942a"
SPEED="13826b7d4ced04d3545cd146427c8ccf0302c67f"
ANCHORS=dict(published_v131=RELEASE,accepted_p1b=P1B,accepted_pfb=PFB,accepted_speed_v1=SPEED)
HEAD="a0c9def6b732"
RUNTIME_PATHS=("src","config","migrations","scripts","data/fixtures","pyproject.toml","daily.cmd")


def git(root,*args):
    return subprocess.check_output(["git",*args],cwd=root).decode().strip()


def archive_files(root,commit,*paths):
    raw=subprocess.check_output(["git","-c","core.autocrlf=false","archive",commit,*paths],cwd=root)
    with tarfile.open(fileobj=BytesIO(raw)) as stream:
        return {item.name:stream.extractfile(item).read().replace(b"\r\n",b"\n") for item in stream if item.isfile()}


def parse_dag(files):
    revisions={}
    for name,raw in files.items():
        if not name.startswith("migrations/versions/") or not name.endswith(".py"):
            continue
        fields={}
        for node in ast.parse(raw.decode(),filename=name).body:
            if isinstance(node,ast.Assign):
                for target in node.targets:
                    if isinstance(target,ast.Name) and target.id in {"revision","down_revision"}:
                        fields[target.id]=ast.literal_eval(node.value)
            elif isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id in {"revision","down_revision"}:
                fields[node.target.id]=ast.literal_eval(node.value)
        if not isinstance(fields.get("revision"),str) or "down_revision" not in fields:
            raise ValueError("MIGRATION_REVISION_DECLARATION_REQUIRED:"+name)
        revision=fields["revision"]
        if revision in revisions:
            raise ValueError("DUPLICATE_MIGRATION_REVISION:"+revision)
        parent=fields["down_revision"]
        if parent is not None and not isinstance(parent,str):
            raise ValueError("UNREVIEWED_MIGRATION_BRANCH_OR_MERGE:"+revision)
        revisions[revision]=dict(parent=parent,file=name,sha256=hashlib.sha256(raw).hexdigest())
    parents={value["parent"] for value in revisions.values() if value["parent"] is not None}
    if not parents<=set(revisions):
        raise ValueError("MIGRATION_PARENT_MISSING")
    heads=set(revisions)-parents
    if heads!={HEAD}:
        raise ValueError("MIGRATION_SINGLE_APPROVED_HEAD_REQUIRED")
    chain=[]
    current=HEAD
    while current is not None:
        if current in chain:
            raise ValueError("MIGRATION_CYCLE")
        chain.append(current)
        current=revisions[current]["parent"]
    if set(chain)!=set(revisions):
        raise ValueError("DISCONNECTED_MIGRATION_DAG")
    return dict(head=HEAD,chain=list(reversed(chain)),revisions=revisions)


def audit(root):
    root=Path(root)
    if git(root,"rev-parse","v1.3.1^{}")!=RELEASE:
        raise ValueError("PUBLISHED_RELEASE_TAG_CHANGED")
    for value in ANCHORS.values():
        subprocess.run(["git","merge-base","--is-ancestor",value,"HEAD"],cwd=root,check=True,capture_output=True)
    pairs=[("release_to_p1b",RELEASE,P1B,RELEASE),("p1b_to_pfb",P1B,PFB,P1B),("pfb_to_speed",PFB,SPEED,PFB)]
    bases={}
    for name,left,right,expected in pairs:
        value=git(root,"merge-base",left,right)
        if value!=expected:
            raise ValueError("ACCEPTED_HISTORY_RELATION_CHANGED:"+name)
        bases[name]=value
    merge_proofs=[]
    for commit in git(root,"rev-list","--first-parent","--merges",RELEASE+"..HEAD").splitlines():
        parents=git(root,"rev-list","--parents","-n","1",commit).split()[1:]
        if len(parents)!=2:
            raise ValueError("TWO_PARENT_INTEGRATION_MERGE_REQUIRED")
        computed=git(root,"merge-tree","--write-tree",*parents).splitlines()[0]
        recorded=git(root,"rev-parse",commit+"^{tree}")
        if computed!=recorded:
            raise ValueError("UNREVIEWED_MANUAL_MERGE_RESOLUTION:"+commit)
        merge_proofs.append(dict(commit=commit,parents=parents,tree=recorded,recomputed_conflict_free=True))
    expected=archive_files(root,RELEASE,"migrations")
    p1=archive_files(root,P1B,"migrations")
    pf=archive_files(root,PFB,"migrations")
    additions={}
    for label,prior,current in (("P1-B",expected,p1),("PF-B",p1,pf)):
        if any(current.get(name)!=raw for name,raw in prior.items()):
            raise ValueError("ACCEPTED_FEATURE_REWRITES_PRIOR_MIGRATION:"+label)
        additions[label]=sorted(set(current)-set(prior))
    if additions["P1-B"]!=["migrations/versions/9fb8cde5a621_add_scoped_openfootball_ledger.py"] or additions["PF-B"]!=["migrations/versions/a0c9def6b732_add_portfolio_shared_ownership.py"]:
        raise ValueError("UNREVIEWED_MIGRATION_ADDITION")
    actual={p.relative_to(root).as_posix():p.read_bytes().replace(b"\r\n",b"\n") for p in (root/"migrations").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if actual!=pf:
        raise ValueError("INTEGRATED_MIGRATIONS_DIFFER_FROM_ACCEPTED_DAG")
    dag=parse_dag(actual)
    if dag["chain"][-3:]!=["8ea7bcd49510","9fb8cde5a621",HEAD]:
        raise ValueError("INTEGRATION_UPGRADE_CHAIN_CHANGED")
    runtime_changes=git(root,"diff","--name-only",PFB,"--",*RUNTIME_PATHS).splitlines()
    runtime_additions=git(root,"ls-files","--others","--exclude-standard","--",*RUNTIME_PATHS).splitlines()
    return dict(status="PASS_V140_HISTORY_AND_MIGRATION_AUDIT",current_commit=git(root,"rev-parse","HEAD"),anchors=ANCHORS,
        common_ancestors=bases,ordinary_merges=merge_proofs,migration_additions=additions,migration_dag=dag,
        accepted_runtime_unchanged=not(runtime_changes or runtime_additions),runtime_changes=runtime_changes,runtime_additions=runtime_additions,
        old_migration_bytes_unchanged=True,migration_execution_or_production_writes=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    value=audit(Path(__file__).resolve().parents[1])
    if args.output:
        with args.output.open("x",encoding="utf-8") as stream:
            json.dump(value,stream,indent=2)
    print(json.dumps(value,indent=2))


if __name__=="__main__":
    main()
