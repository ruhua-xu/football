"""Reuse a byte-verified wheel only for identical packaging inputs and build environment."""
import argparse
from datetime import datetime,timezone
from email.parser import BytesParser
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from devtools.evidence import canonical,digest,environment_identity  # noqa: E402


def packaging_inputs(root):
    project=tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8"))
    # This repository's wheel uses explicit src packages and explicit data files.
    # Refuse a packaging layout change rather than silently accepting a stale key.
    assert project["tool"]["setuptools"]["packages"]["find"]["where"]==["src"]
    assert not (root/"setup.py").exists() and not (root/"setup.cfg").exists() and not (root/"MANIFEST.in").exists()
    members={p.relative_to(root).as_posix() for p in (root/"src/football_system").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    members|={"pyproject.toml","README.md"}
    for values in project["tool"]["setuptools"]["data-files"].values():
        for name in values:
            assert not set(name)&set("*?[]")
            members.add(name)
    for pattern in ("LICENSE*","COPYING*","AUTHORS*","NOTICE*"):
        members.update(p.name for p in root.glob(pattern) if p.is_file())
    return project,{name:digest((root/name).read_bytes()) for name in sorted(members)}


def verify_wheel(path,root,project):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        expected={p.relative_to(root/"src").as_posix():p.read_bytes() for p in (root/"src/football_system").rglob("*.py")}
        for name,raw in expected.items():
            assert archive.read(name)==raw,name
        actual_python={name for name in archive.namelist() if name.startswith("football_system/") and name.endswith(".py")}
        assert actual_python==set(expected)
        resources={name.split("football_system_resources/",1)[1]:name for name in archive.namelist() if "football_system_resources/" in name and not name.endswith("/")}
        wanted={}
        for directory,names in project["tool"]["setuptools"]["data-files"].items():
            for name in names:
                relative=(Path(directory)/Path(name).name).as_posix().split("football_system_resources/",1)[-1]
                if directory=="football_system_resources":
                    relative=Path(name).name
                wanted[relative]=(root/name).read_bytes()
        assert set(resources)==set(wanted)
        for name,raw in wanted.items():
            assert archive.read(resources[name])==raw,name
        metadata=[name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        assert len(metadata)==1
        header=BytesParser().parsebytes(archive.read(metadata[0]))
        assert header["Version"]==project["project"]["version"]
        assert header["Name"].lower().replace("_","-")==project["project"]["name"].lower().replace("_","-")


def ensure_wheel(root,cache,*,builder=None):
    project,inputs=packaging_inputs(root)
    env=environment_identity()
    env.pop("utc_date")  # Build inputs/backend, not a calendar boundary, identify a wheel.
    context=dict(schema_version="EXACT_WHEEL_BUILD_INPUTS_V1",inputs=inputs,environment=env,
        build_command=["python","-I","-m","build","--wheel","--no-isolation"])
    key=digest(canonical(context).encode())
    folder=Path(cache)/key
    record=folder/"build.json"
    if record.is_file():
        previous=json.loads(record.read_bytes())
        path=folder/previous["wheel_name"]
        assert previous["context"]==context and digest(path.read_bytes())==previous["wheel_sha256"]
        verify_wheel(path,root,project)
        return dict(status="REUSED_EXACT_WHEEL",wheel=str(path.resolve()),wheel_sha256=previous["wheel_sha256"],build_key=key)
    if folder.exists():
        raise ValueError("PARTIAL_WHEEL_CACHE_REQUIRES_INSPECTION")
    folder.mkdir(parents=True,exist_ok=False)
    (folder/"inputs.json").write_text(json.dumps(context,indent=2),encoding="utf-8")
    if builder is None:
        with (folder/"build.log").open("xb") as stream:
            subprocess.run([sys.executable,"-I","-m","build","--wheel","--no-isolation","--outdir",str(folder)],cwd=root,
                stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=900)
    else:
        builder(folder,project)
    wheels=list(folder.glob("*.whl"))
    assert len(wheels)==1
    path=wheels[0]
    verify_wheel(path,root,project)
    assert packaging_inputs(root)[1]==inputs,"packaging inputs changed during build"
    value=dict(context=context,wheel_name=path.name,wheel_sha256=digest(path.read_bytes()),created_at_utc=datetime.now(timezone.utc).isoformat())
    record.write_text(json.dumps(value,indent=2),encoding="utf-8")
    return dict(status="BUILT_EXACT_WHEEL",wheel=str(path.resolve()),wheel_sha256=value["wheel_sha256"],build_key=key)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache",type=Path,required=True)
    parser.add_argument("--receipt",type=Path,required=True)
    args=parser.parse_args()
    assert args.cache.parent.is_dir() and args.receipt.parent.is_dir()
    result=ensure_wheel(ROOT,args.cache)
    with args.receipt.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
