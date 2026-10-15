"""PF-B preserves every preexisting nonintegration byte, including all kernels."""
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

from tests.contract.portfolio_projection import MANIFEST, PORTFOLIO_NEW_FILES, project_portfolio
from football_system.infrastructure.files.real_bridge import identities, FROZEN_IDENTITIES

ROOT=Path(__file__).resolve().parents[2]


def test_portfolio_exact_seams_and_all_frozen_kernel_bytes():
    raw=subprocess.check_output(["git","archive",MANIFEST["base"],"src","config","migrations","scripts","pyproject.toml","daily.cmd"],cwd=ROOT)
    checked=set()
    with tarfile.open(fileobj=BytesIO(raw)) as stream:
        for member in stream:
            if member.isfile():
                actual=(ROOT/member.name).read_bytes().replace(b"\r\n",b"\n")
                assert project_portfolio(member.name,actual)==stream.extractfile(member).read().replace(b"\r\n",b"\n"),member.name
                checked.add(member.name)
    current={p.relative_to(ROOT).as_posix() for folder in ("src/football_system","config","migrations","scripts")
        for p in (ROOT/folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}|{"pyproject.toml","daily.cmd"}
    assert current==checked|PORTFOLIO_NEW_FILES
    assert not any("/services/" in name or name.startswith("config/") for name in MANIFEST["files"])
    assert identities()[1]==FROZEN_IDENTITIES
