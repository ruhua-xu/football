"""Explicit P1-B seams and known added files; no mathematical file exclusions."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

from tests.contract.scoped_projection import MANIFEST, SCOPED_NEW_FILES, project_scoped
from tests.contract.portfolio_projection import PORTFOLIO_NEW_FILES
from football_system.infrastructure.files.real_bridge import identities, FROZEN_IDENTITIES
from football_system.domain.services.elo_baseline import EloBaselineConfig

ROOT = Path(__file__).resolve().parents[2]


def test_scoped_feature_preserves_all_nonintegration_baseline_bytes_and_frozen_math():
    raw = subprocess.check_output(["git", "archive", MANIFEST["base"], "src", "config", "migrations", "scripts", "pyproject.toml", "daily.cmd"], cwd=ROOT)
    checked = set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if member.isfile():
                actual = (ROOT / member.name).read_bytes().replace(b"\r\n", b"\n")
                assert project_scoped(member.name, actual) == archive.extractfile(member).read().replace(b"\r\n", b"\n"), member.name
                checked.add(member.name)
    current = {p.relative_to(ROOT).as_posix() for folder in ("src/football_system", "config", "migrations", "scripts")
        for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts} | {"pyproject.toml", "daily.cmd"}
    assert current == checked | SCOPED_NEW_FILES | PORTFOLIO_NEW_FILES
    assert not any("/services/" in name or name.startswith("config/") for name in MANIFEST["files"])
    assert identities()[1] == FROZEN_IDENTITIES
    assert EloBaselineConfig().config_hash == "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
