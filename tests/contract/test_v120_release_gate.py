"""Independent release-only projection against the accepted OpenFootball commit."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile
import tomllib

import football_system
from football_system.domain.services.elo_baseline import EloBaselineConfig, MODEL_VERSION
from football_system.infrastructure.files.real_bridge import identities, FROZEN_IDENTITIES

ROOT = Path(__file__).resolve().parents[2]
APPROVED = "df47ba4cf34eb4f0a964c2e5ab5d0e88ea6a57f6"


def test_v120_only_exact_release_metadata_changes_in_approved_core():
    raw = subprocess.check_output(["git", "archive", APPROVED, "src", "config", "migrations", "pyproject.toml"], cwd=ROOT)
    checked = set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            expected = archive.extractfile(member).read().replace(b"\r\n", b"\n")
            actual = (ROOT / member.name).read_bytes().replace(b"\r\n", b"\n")
            if member.name in {"src/football_system/__init__.py", "src/football_system/infrastructure/database/session.py", "pyproject.toml"}:
                assert actual.count(b"1.2.0") == 1
                actual = actual.replace(b"1.2.0", b"1.1.0", 1)
            if member.name == "pyproject.toml":
                needle = b'    "tzdata==2025.2",\n'
                assert actual.count(needle) == 1
                actual = actual.replace(needle, b"", 1)
            if member.name == "src/football_system/infrastructure/files/real_bridge_frozen.py":
                assert actual.count(b"1.2.0") == 3
                actual = actual.replace(b"1.2.0", b"1.1.0")
                needle = b'        \'    "tzdata==2025.2",\\n\',\n'
                assert actual.count(needle) == 1
                actual = actual.replace(needle, b"", 1)
            assert actual == expected, member.name
            checked.add(member.name)
    current = set(subprocess.check_output(["git", "ls-files", "src", "config", "migrations", "pyproject.toml"], cwd=ROOT, text=True).splitlines())
    assert checked == current


def test_v120_version_dependency_and_frozen_model_identities():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == football_system.__version__ == "1.2.0"
    assert "tzdata==2025.2" in project["project"]["dependencies"]
    assert MODEL_VERSION == "1"
    assert EloBaselineConfig().config_hash == "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
    bridge, frozen = identities()
    assert bridge == "aa55cbe20e31b6ea1b4f4bc1f723832ae34474b85b02222c6d05e66e4bb4b11b"
    assert frozen == FROZEN_IDENTITIES and len(frozen) == 6
