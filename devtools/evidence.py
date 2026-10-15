"""Exact development-evidence identity. FULL RELEASE never reuses test passes."""
import base64
import csv
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(",",":"))


def environment_identity():
    packages={}
    unrecorded=set()
    for distribution in importlib.metadata.distributions():
        name=distribution.metadata["Name"].lower().replace("_","-")
        record=distribution.read_text("RECORD")
        if record is None:
            unrecorded.add(name)
            continue
        checked=[]
        for path,expected,size in csv.reader(record.splitlines()):
            if not expected:
                continue
            algorithm,encoded=expected.split("=",1)
            if algorithm!="sha256":
                raise ValueError("SHA256_RECORD_REQUIRED:"+name)
            raw=distribution.locate_file(path).read_bytes()
            actual=hashlib.sha256(raw).digest()
            if base64.urlsafe_b64encode(actual).decode().rstrip("=")!=encoded or (size and len(raw)!=int(size)):
                raise ValueError("INSTALLED_RECORD_MISMATCH:"+name+":"+path)
            checked.append((path,actual.hex()))
        value=dict(version=distribution.version,record_sha256=digest(record.encode()),files_sha256=digest(canonical(checked).encode()))
        if name in packages and packages[name]!=value:
            raise ValueError("AMBIGUOUS_INSTALLED_DISTRIBUTION:"+name)
        packages[name]=value
    if unrecorded-set(packages):
        raise ValueError("INSTALLED_DISTRIBUTION_RECORD_REQUIRED:"+",".join(sorted(unrecorded-set(packages))))
    return dict(python=sys.version,executable=str(Path(sys.executable).resolve()),executable_sha256=digest(Path(sys.executable).read_bytes()),
        platform=platform.platform(),machine=platform.machine(),sqlite=sqlite3.sqlite_version,packages=packages,
        utc_date=datetime.now(timezone.utc).date().isoformat(),
        controlled_environment={key:os.environ.get(key) for key in ("PYTHONHASHSEED","PYTEST_DISABLE_PLUGIN_AUTOLOAD","TZ","LANG","LC_ALL","OMP_NUM_THREADS")})


def workspace_identity(root):
    def git(*args):
        return subprocess.check_output(["git",*args],cwd=root)
    names=git("ls-files","--cached","--others","--exclude-standard","-z").decode().split("\0")
    files=[(name,digest((root/name).read_bytes()) if (root/name).is_file() else None) for name in sorted(set(names)) if name]
    return dict(commit=git("rev-parse","HEAD").decode().strip(),tree=git("rev-parse","HEAD^{tree}").decode().strip(),
        clean=not bool(git("status","--porcelain").strip()),actual_tracked_bytes_sha256=digest(canonical(files).encode()))


def identity(root,configuration):
    value=dict(schema_version="EXACT_DEVELOPMENT_EVIDENCE_V1",workspace=workspace_identity(root),
        environment=environment_identity(),configuration=configuration)
    return value,digest(canonical(value).encode())


def verified_reuse(directory,expected_identity,*,mode):
    if mode=="FULL_RELEASE" or not expected_identity["workspace"]["clean"]:
        return None
    path=Path(directory)/"receipt.json"
    if not path.is_file():
        return None
    value=json.loads(path.read_bytes())
    if value.get("status")!="PASS" or value.get("identity")!=expected_identity or not value.get("reusable_for_development"):
        return None
    if value.get("failed")!=0 or value.get("tests",0)<=0 or not value.get("steps") or any(s["returncode"] for s in value["steps"]):
        return None
    root=path.parent.resolve()
    if not value.get("artifacts"):
        return None
    for artifact in value["artifacts"]:
        target=(root/artifact["path"]).resolve()
        if not target.is_relative_to(root) or not target.is_file() or digest(target.read_bytes())!=artifact["sha256"]:
            return None
    return value
