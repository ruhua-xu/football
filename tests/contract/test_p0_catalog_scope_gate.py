"""P0 changes one enum member and its exact freeze projection, not reader/math/lifecycle."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

from tests.contract.p0_catalog_projection import project_p0
from football_system.domain.services.elo_baseline import EloBaselineConfig
from football_system.infrastructure.files.real_bridge import FROZEN_IDENTITIES,identities

ROOT=Path(__file__).resolve().parents[2]
BASE="0f677e834d5e2b6eb092ecaf86e4c70a60e38e98"


def test_p0_exact_source_projection_preserves_released_reader_math_and_migrations():
    raw=subprocess.check_output(["git","archive",BASE,"src","config","migrations","scripts","pyproject.toml","daily.cmd"],cwd=ROOT)
    checked=set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            expected=archive.extractfile(member).read().replace(b"\r\n",b"\n")
            actual=project_p0(member.name,(ROOT/member.name).read_bytes().replace(b"\r\n",b"\n"))
            assert actual==expected,member.name
            checked.add(member.name)
    current={p.relative_to(ROOT).as_posix() for folder in ("src/football_system","config","migrations","scripts")
        for p in (ROOT/folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert current|{"pyproject.toml","daily.cmd"}==checked
    assert identities()[1]==FROZEN_IDENTITIES
    assert EloBaselineConfig().config_hash=="c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
