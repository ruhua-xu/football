"""v1.3.1 is the approved P0 plus explicitly enumerated release preparation."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

from tests.contract.release131_projection import project_release131

ROOT = Path(__file__).resolve().parents[2]
APPROVED = "9429bcf695527d5c1d2663b2915faa388ab4a065"


def test_release131_exactly_preserves_approved_p0_and_all_nonrelease_bytes():
    paths = ("src", "config", "migrations", "scripts", "pyproject.toml", "daily.cmd")
    raw = subprocess.check_output(["git", "archive", APPROVED, *paths], cwd=ROOT)
    checked = set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if member.isfile():
                expected = archive.extractfile(member).read().replace(b"\r\n", b"\n")
                actual = (ROOT / member.name).read_bytes().replace(b"\r\n", b"\n")
                assert project_release131(member.name, actual) == expected, member.name
                checked.add(member.name)
    current = {p.relative_to(ROOT).as_posix() for folder in ("src/football_system", "config", "migrations", "scripts")
        for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert current | {"pyproject.toml", "daily.cmd"} == checked
