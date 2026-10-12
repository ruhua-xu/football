"""PF-A introduces review documents/models/tests, not runtime or migration changes."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile
from tests.contract.scoped_projection import SCOPED_NEW_FILES, project_scoped
from tests.contract.release131_projection import project_release131

ROOT = Path(__file__).resolve().parents[2]


def test_pfa_preserves_all_existing_runtime_and_migration_bytes():
    raw = subprocess.check_output(["git", "archive", "9429bcf695527d5c1d2663b2915faa388ab4a065",
        "src", "config", "migrations", "scripts", "pyproject.toml", "daily.cmd"], cwd=ROOT)
    checked = set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if member.isfile():
                actual = project_release131(member.name, project_scoped(member.name, (ROOT / member.name).read_bytes().replace(b"\r\n", b"\n")))
                assert actual == archive.extractfile(member).read().replace(b"\r\n", b"\n"), member.name
                checked.add(member.name)
    current = {p.relative_to(ROOT).as_posix() for folder in ("src/football_system", "config", "migrations", "scripts")
        for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts} | {"pyproject.toml", "daily.cmd"}
    assert current == checked | SCOPED_NEW_FILES | {"src/football_system/domain/openfootball_scope.py"}
