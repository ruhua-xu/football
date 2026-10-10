"""Only one new pure contract module; every existing production byte stays frozen."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
NEW = {"src/football_system/domain/openfootball_scope.py"}


def test_p1a_preserves_every_existing_source_configuration_migration_and_operator_byte():
    raw = subprocess.check_output(["git", "archive", "9429bcf695527d5c1d2663b2915faa388ab4a065", "src", "config", "migrations", "scripts", "pyproject.toml", "daily.cmd"], cwd=ROOT)
    checked = set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if member.isfile():
                assert (ROOT / member.name).read_bytes().replace(b"\r\n", b"\n") == archive.extractfile(member).read().replace(b"\r\n", b"\n"), member.name
                checked.add(member.name)
    current = {p.relative_to(ROOT).as_posix() for folder in ("src/football_system", "config", "migrations", "scripts")
        for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts} | {"pyproject.toml", "daily.cmd"}
    assert current - checked == NEW and checked <= current
