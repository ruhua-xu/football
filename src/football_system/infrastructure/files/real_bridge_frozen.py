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
    "src/football_system/infrastructure/database/repositories.py": (
        '        from football_system.infrastructure.database.openfootball_production_schema import assert_no_legacy_openfootball_training\n\n        assert_no_legacy_openfootball_training(session, tuple(f.match_result_id for s in artifacts.quant_model_states for f in s.training_facts))\n',),
    "src/football_system/interfaces/production_quant_cli.py": (
        '    if list(arguments[:1]) == ["openfootball"]:\n        from football_system.interfaces.openfootball_production_cli import dispatch_openfootball_production\n\n        return dispatch_openfootball_production(arguments[1:])\n',),
    "src/football_system/infrastructure/database/production_inference_repository.py": (
        '\n    def load_openfootball_model_pin_candidate(self):\n        """Versioned model-only binding; retains unavailable targets and creates no pin."""\n        return self._production.openfootball_binding().load_model_pin_candidate()\n',),
    "src/football_system/infrastructure/database/production_quant_repository.py": (
        '\n    def openfootball_binding(self):\n        """Additive fixed-cohort observed binding; never widens the V1 readers."""\n        from football_system.infrastructure.database.openfootball_production_repository import SqlAlchemyOpenFootballProductionRepository\n\n        return SqlAlchemyOpenFootballProductionRepository(self)\n',),
    "src/football_system/application/quant_integrity.py": (
        '\n    def run_openfootball(self, production_repository, *, request_key):\n        """Explicit versioned adapter for the OpenFootball observed source graph."""\n        return production_repository.openfootball_binding().run_pilot(request_key=request_key)\n',),
    "src/football_system/infrastructure/database/models.py": (
        "\n# The real bridge is an additive graph; no legacy table or type is widened.\nfrom football_system.infrastructure.database.real_bridge_schema import real_bridge_tables  # noqa: E402\n\n_real_bridge_tables = real_bridge_tables(Base.metadata)\n",
        "\n# Independent OpenFootball bindings; existing observed V1 models are unchanged.\nfrom football_system.infrastructure.database.openfootball_production_schema import openfootball_production_tables  # noqa: E402\n\n_openfootball_production_tables = openfootball_production_tables(Base.metadata)\n",),
    "src/football_system/infrastructure/database/immutability.py": (
        "    from football_system.infrastructure.database.real_bridge_schema import install_real_bridge_triggers\n\n    install_real_bridge_triggers(connection)\n",
        "    from football_system.infrastructure.database.openfootball_production_schema import install_openfootball_production_triggers\n\n    install_openfootball_production_triggers(connection)\n",),
    "src/football_system/interfaces/cli.py": (
        "    if arguments[:1] == [\"real-bridge\"]:\n        from football_system.interfaces.real_bridge_cli import dispatch_real_bridge\n\n        return dispatch_real_bridge(arguments[1:])\n",),
    "pyproject.toml": (
        '    "tzdata==2025.2",\n',
        ', "data/fixtures/real_bridge_v1.json"',
        '    "migrations/versions/6c859ab273fe_add_real_prospective_bridge.py",\n',
        '    "migrations/versions/7d96abc3840f_add_openfootball_production_binding.py",\n',
        '    "fankui/real_prospective_activation_bridge_v1_contract.md",\n',
        '    "fankui/pre_lock_replacement_v1_contract.md",\n',
        '    "fankui/daily_operator_v1_contract.md",\n',),
}
RELEASE_VERSION_PROJECTION={
    "pyproject.toml": ('version = "1.2.0"', 'version = "1.0.0"'),
    "src/football_system/__init__.py": ('__version__ = "1.2.0"', '__version__ = "1.0.0"'),
    "src/football_system/infrastructure/database/session.py": ('football-system v1.2.0 supports SQLite only.', 'football-system v1.0.0 supports SQLite only.'),
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
            if member.name in RELEASE_VERSION_PROJECTION:
                current,baseline=RELEASE_VERSION_PROJECTION[member.name]
                if actual.count(current.encode())!=1:
                    raise ValueError("FROZEN_RELEASE_VERSION_MISMATCH")
                actual=actual.replace(current.encode(),baseline.encode(),1)
            for hook in ADDITIVE_HOOKS.get(member.name,()):
                needle=hook.encode()
                if actual.count(needle)!=1:
                    raise ValueError("ADDITIVE_REGISTRATION_HOOK_CHANGED")
                actual=actual.replace(needle,b"",1)
            if actual!=expected:
                raise ValueError("FROZEN_FILES_CHANGED:"+member.name)
            checked.append(member.name)
    return tuple(checked)
