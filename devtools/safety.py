"""Read-only development scope and optional actual-production boundary verification."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tarfile

BASE="dcc83d1e82f585ae2b4827b0ee7727422b24942a"
PROTECTED=("src","config","migrations","scripts","data/fixtures","pyproject.toml","daily.cmd")


def verify(root,boundary=None):
    # Performance tooling must not edit any released runtime/math/data/build contract.
    changed=subprocess.check_output(["git","diff","--name-only",BASE,"--",*PROTECTED,".github/workflows/ci.yml"],cwd=root).decode().splitlines()
    if changed:
        raise ValueError("PROTECTED_RUNTIME_OR_REQUIRED_CI_CHANGED:"+",".join(changed))
    added=subprocess.check_output(["git","ls-files","--others","--exclude-standard","--",*PROTECTED],cwd=root).decode().splitlines()
    if added:
        raise ValueError("UNTRACKED_PROTECTED_RUNTIME_FILES:"+",".join(added))
    raw=subprocess.check_output(["git","-c","core.autocrlf=false","archive",BASE,*PROTECTED,".github/workflows/ci.yml"],cwd=root)
    checked=0
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for item in archive:
            if item.isfile():
                current=(root/item.name).read_bytes().replace(b"\r\n",b"\n")
                if current!=archive.extractfile(item).read().replace(b"\r\n",b"\n"):
                    raise ValueError("PROTECTED_BYTES_CHANGED:"+item.name)
                checked+=1
    frozen=__import__("football_system.infrastructure.files.real_bridge",fromlist=["identities","FROZEN_IDENTITIES"])
    assert frozen.identities()[1]==frozen.FROZEN_IDENTITIES
    production="NOT_REQUESTED_ON_THIS_HOST"
    if boundary:
        expected=json.loads(Path(boundary).read_bytes())
        for name,record in expected["files"].items():
            path=Path(name)
            before=path.stat()
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            after=path.stat()
            assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
            assert actual==record["sha256"] and [after.st_dev,after.st_ino]==record["file_identity"],name
        production="PASS_EXACT_PROTECTED_BYTES_AND_FILE_IDENTITIES"
    return dict(status="PASS",protected_baseline=BASE,protected_files=checked,required_ci_workflow="BYTE_IDENTICAL",
        frozen_hashes=frozen.FROZEN_IDENTITIES,production_boundary=production,production_sql_connections=0,production_writes=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary",type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(Path(__file__).resolve().parents[1],args.boundary),indent=2))


if __name__=="__main__":
    main()
