"""The release cannot quietly change reviewed model/source/schema implementation."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile
from tests.contract.p0_catalog_projection import project_p0

ROOT=Path(__file__).resolve().parents[2]
ACCEPTED="a6952a4f9b86e1c273aa66e9f48c7930d8687a0a"
VERSION_FILES={"src/football_system/__init__.py", "src/football_system/infrastructure/database/session.py",
    "src/football_system/infrastructure/files/real_bridge_frozen.py", "pyproject.toml"}


def test_release_preserves_accepted_business_source_config_and_migrations():
    raw=subprocess.check_output(["git","archive",ACCEPTED,"src","config","migrations","pyproject.toml"],cwd=ROOT)
    checked=set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            expected=archive.extractfile(member).read().replace(b"\r\n",b"\n")
            actual=(ROOT/member.name).read_bytes().replace(b"\r\n",b"\n")
            actual=project_p0(member.name,actual)
            if member.name in VERSION_FILES:
                assert actual.count(b"1.3.0")==expected.count(b"1.2.0")>0
                actual=actual.replace(b"1.3.0",b"1.2.0")
            assert actual==expected,member.name
            checked.add(member.name)
    current={p.relative_to(ROOT).as_posix() for d in ("src/football_system","config","migrations")
        for p in (ROOT/d).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert current|{"pyproject.toml"}==checked|{"src/football_system/domain/openfootball_scope.py"}
