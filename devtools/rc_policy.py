"""Exact RC metadata promotion over accepted ed07fb4; all business bytes stay frozen."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tarfile

ACCEPTED="ed07fb4b1a2a936aaab8b62262b0475022c53df4"
RUNTIME_PATHS=("src","config","migrations","scripts","data/fixtures","pyproject.toml","daily.cmd")
HUNKS={
    "pyproject.toml":(('version = "1.4.0.dev0"','version = "1.4.0"',1),),
    "src/football_system/__init__.py":(('__version__ = "1.4.0.dev0"','__version__ = "1.4.0"',1),),
    "src/football_system/infrastructure/database/session.py":(("football-system v1.4.0.dev0 supports SQLite only.","football-system v1.4.0 supports SQLite only.",1),),
    "src/football_system/infrastructure/files/real_bridge_frozen.py":(("1.4.0.dev0","1.4.0",3),),
    "scripts/daily_operator.py":(
        ('RELEASE_VERSION = "1.4.0.dev0"','RELEASE_VERSION = "1.4.0"',1),
        ('APPROVED_IMPLEMENTATION = "4f4ec037a8173008cc07066f3ea02d036a4364f1"','APPROVED_IMPLEMENTATION = "'+ACCEPTED+'"',1),
        ("Explicit isolated development identity; qualification is always scope-specific.","Explicit reviewed release-candidate identity; qualification remains scope-specific.",1),
        ("PORTFOLIO_SCOPED_DEVELOPMENT_V1","PORTFOLIO_SCOPED_RELEASE_V1",2)),
    "scripts/wheel_e2e.py":(('EXPECTED_VERSION = "1.4.0.dev0"','EXPECTED_VERSION = "1.4.0"',1),),
    "scripts/portfolio_acceptance.py":(('importlib.metadata.version("football-system") == "1.4.0.dev0"','importlib.metadata.version("football-system") == "1.4.0"',1),),
    "tests/contract/portfolio_projection.py":(
        ("from devtools.repair_policy import project_repair\n","from devtools.repair_policy import project_repair\nfrom devtools.rc_policy import project_rc\n",1),
        ("    raw=project_repair(relative,raw)\n","    raw=project_repair(relative,project_rc(relative,raw))\n",1)),
}
RUNTIME_METADATA=frozenset(name for name in HUNKS if not name.startswith("tests/"))


def project_rc(relative,raw):
    for before,after,count in reversed(HUNKS.get(relative,())):
        if raw.count(after.encode())!=count:
            raise ValueError("EXACT_RC_METADATA_HUNK_REQUIRED:"+relative)
        raw=raw.replace(after.encode(),before.encode())
    return raw


def verify_rc_scope(root):
    root=Path(root)
    subprocess.run(["git","merge-base","--is-ancestor",ACCEPTED,"HEAD"],cwd=root,check=True,capture_output=True)
    raw=subprocess.check_output(["git","-c","core.autocrlf=false","archive",ACCEPTED,*RUNTIME_PATHS],cwd=root)
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        expected={item.name:archive.extractfile(item).read().replace(b"\r\n",b"\n") for item in archive if item.isfile()}
    names=subprocess.check_output(["git","ls-files","--cached","--others","--exclude-standard","-z","--",*RUNTIME_PATHS],cwd=root).decode().split("\0")
    if set(filter(None,names))!=set(expected):
        raise ValueError("RC_RUNTIME_FILE_SET_CHANGED")
    promotions=[]
    for name,before in expected.items():
        after=(root/name).read_bytes().replace(b"\r\n",b"\n")
        if project_rc(name,after)!=before:
            raise ValueError("RC_BUSINESS_OR_UNREVIEWED_METADATA_CHANGED:"+name)
        if before!=after:
            promotions.append(dict(file=name,before_sha256=hashlib.sha256(before).hexdigest(),after_sha256=hashlib.sha256(after).hexdigest()))
    if {item["file"] for item in promotions}!=RUNTIME_METADATA:
        raise ValueError("RC_COMPLETE_METADATA_PROMOTION_REQUIRED")
    manifest={name:hashlib.sha256(value).hexdigest() for name,value in sorted(expected.items())}
    return dict(status="PASS_RC_SCOPE_FROZEN_TO_ACCEPTED_IC2",accepted_commit=ACCEPTED,files=len(expected),
        baseline_manifest_sha256=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),metadata_promotions=promotions,
        package_version="1.4.0",business_numerics_migrations_unchanged=True)
