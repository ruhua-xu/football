"""Byte projection of v1.0 files with only the enumerated additive registration hooks.

Used by the local checkout operator and acceptance proofs, never for fetching code.
New bridge code has its own independent implementation identity.
"""

from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

BASE="6d633d4425d5fd6d93918d60526f701a59020b3d"
ADDITIVE_HOOKS={
    "src/football_system/infrastructure/database/models.py": (
        "\n# The real bridge is an additive graph; no legacy table or type is widened.\nfrom football_system.infrastructure.database.real_bridge_schema import real_bridge_tables  # noqa: E402\n\n_real_bridge_tables = real_bridge_tables(Base.metadata)\n",),
    "src/football_system/infrastructure/database/immutability.py": (
        "    from football_system.infrastructure.database.real_bridge_schema import install_real_bridge_triggers\n\n    install_real_bridge_triggers(connection)\n",),
    "src/football_system/interfaces/cli.py": (
        "    if arguments[:1] == [\"real-bridge\"]:\n        from football_system.interfaces.real_bridge_cli import dispatch_real_bridge\n\n        return dispatch_real_bridge(arguments[1:])\n",),
    "pyproject.toml": (
        ', "data/fixtures/real_bridge_v1.json"',
        '    "migrations/versions/6c859ab273fe_add_real_prospective_bridge.py",\n',),
}


def verify_frozen_checkout(root):
    root=Path(root)
    ref=subprocess.check_output(["git","rev-parse","v1.0.0^{}"],cwd=root,timeout=30,text=True).strip()
    if ref!=BASE:
        raise ValueError("FROZEN_BASE_REF_CHANGED")
    raw=subprocess.check_output(["git","archive",BASE,"src","config","migrations","pyproject.toml"],cwd=root,timeout=30)
    checked=[]
    with tarfile.open(fileobj=BytesIO(raw)) as stream:
        for member in stream:
            if not member.isfile():
                continue
            expected=stream.extractfile(member).read().replace(b"\r\n",b"\n")
            actual=(root/member.name).read_bytes().replace(b"\r\n",b"\n")
            for hook in ADDITIVE_HOOKS.get(member.name,()):
                needle=hook.encode()
                if actual.count(needle)!=1:
                    raise ValueError("ADDITIVE_REGISTRATION_HOOK_CHANGED")
                actual=actual.replace(needle,b"",1)
            if actual!=expected:
                raise ValueError("FROZEN_FILES_CHANGED:"+member.name)
            checked.append(member.name)
    return tuple(checked)
