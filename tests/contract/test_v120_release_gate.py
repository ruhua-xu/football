"""Preserve released v1.2 mathematics and wire/lifecycle outside the reviewed source seam."""
import ast
import hashlib
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile
import tomllib

import football_system
from football_system.domain.services.elo_baseline import EloBaselineConfig, MODEL_VERSION
from football_system.infrastructure.files.real_bridge import identities, FROZEN_IDENTITIES
from tests.contract.p0_catalog_projection import project_p0
from tests.contract.release131_projection import project_release131

ROOT=Path(__file__).resolve().parents[2]
RELEASE="8b7cfb3ac2e416a5c3c8f5046150245ab076f490"
INTEGRATION_CHANGES={
    "src/football_system/domain/real_bridge.py", "src/football_system/application/real_bridge_requests.py",
    "src/football_system/infrastructure/database/real_bridge_repository.py", "src/football_system/infrastructure/database/real_bridge_sources.py",
    "src/football_system/infrastructure/database/models.py", "src/football_system/infrastructure/database/immutability.py",
    "src/football_system/infrastructure/files/real_bridge.py", "src/football_system/infrastructure/files/real_bridge_frozen.py",
    "src/football_system/interfaces/real_bridge_cli.py", "pyproject.toml",
    "src/football_system/infrastructure/database/migrations.py",
}
NEW_FILES={
    "src/football_system/domain/pinned_model_source.py", "src/football_system/infrastructure/database/openfootball_model_source.py",
    "src/football_system/infrastructure/database/ofp_real_model_pin_schema.py", "src/football_system/infrastructure/database/real_bridge_head.py",
    "migrations/versions/8ea7bcd49510_add_ofp_real_model_pin_source.py",
}
RELEASE_ONLY_VERSIONS={"src/football_system/__init__.py", "src/football_system/infrastructure/database/session.py"}


def old_text(name):
    return subprocess.check_output(["git","show",RELEASE+":"+name],cwd=ROOT).decode("utf-8").replace("\r\n","\n")


def test_v120_unmodified_math_qualification_and_legacy_source_files_are_byte_frozen():
    raw=subprocess.check_output(["git","archive",RELEASE,"src","config","migrations","pyproject.toml"],cwd=ROOT)
    checked=set()
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            expected=archive.extractfile(member).read().replace(b"\r\n",b"\n")
            actual=(ROOT/member.name).read_bytes().replace(b"\r\n",b"\n")
            actual=project_p0(member.name,project_release131(member.name,actual))
            if member.name in RELEASE_ONLY_VERSIONS:
                assert actual.count(b"1.3.0")==1
                actual=actual.replace(b"1.3.0",b"1.2.0",1)
            if member.name not in INTEGRATION_CHANGES:
                assert actual==expected,member.name
            checked.add(member.name)
    current={p.relative_to(ROOT).as_posix() for directory in ("src/football_system","config","migrations")
        for p in (ROOT/directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    current.add("pyproject.toml")
    assert current-checked==NEW_FILES
    assert checked<=current
    before=tomllib.loads(old_text("pyproject.toml"))
    after=tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))
    after["tool"]["setuptools"]["data-files"]["football_system_resources/migrations/versions"].remove(
        "migrations/versions/8ea7bcd49510_add_ofp_real_model_pin_source.py")
    assert after["project"]["version"]=="1.3.1"
    after["project"]["version"]="1.2.0"
    assert before==after  # Only the explicit release version; no dependency/packaging drift.
    migration_helper="src/football_system/infrastructure/database/migrations.py"
    expected=old_text(migration_helper).replace('config.set_main_option("sqlalchemy.url", database_url)',
        'config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))')
    assert (ROOT/migration_helper).read_text(encoding="utf-8")==expected


def test_real_bridge_configuration_and_other_lifecycle_classes_are_unchanged():
    for name,allowed in (("domain/real_bridge.py",{"RealModelPinV1"}), ("application/real_bridge_requests.py",{"ModelPinRequest"})):
        relative="src/football_system/"+name
        old=old_text(relative)
        new=(ROOT/relative).read_text(encoding="utf-8")
        old_nodes={n.name:ast.get_source_segment(old,n) for n in ast.parse(old).body if isinstance(n,ast.ClassDef)}
        new_nodes={n.name:ast.get_source_segment(new,n) for n in ast.parse(new).body if isinstance(n,ast.ClassDef)}
        assert old_nodes.keys()==new_nodes.keys()
        for key in old_nodes.keys()-allowed:
            assert old_nodes[key]==new_nodes[key],key
    relative="src/football_system/infrastructure/database/real_bridge_repository.py"
    old=old_text(relative)
    new=(ROOT/relative).read_text(encoding="utf-8")
    def operations(source):
        cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=="SqlAlchemyRealBridgeRepository")
        build=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="_build")
        return {ast.unparse(n.test):ast.get_source_segment(source,n) for n in build.body if isinstance(n,ast.If)}
    prior,actual=operations(old),operations(new)
    assert prior.keys()==actual.keys()
    for key in prior:
        if key not in {"op == 'model-pin'","op == 'anchor'"}:
            assert prior[key]==actual[key],key


def test_v120_version_dependency_and_frozen_model_identities():
    project=tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"]==football_system.__version__=="1.3.1"
    assert "tzdata==2025.2" in project["project"]["dependencies"]
    assert MODEL_VERSION=="1" and EloBaselineConfig().config_hash=="c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
    module=ast.parse(old_text("src/football_system/infrastructure/files/real_bridge.py"))
    assignments={n.targets[0].id:n.value for n in module.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)}
    names=ast.literal_eval(assignments["BRIDGE_FILES"])
    expected={kw.arg:ast.literal_eval(kw.value) for kw in assignments["FROZEN_IDENTITIES"].keywords}
    digest=hashlib.sha256(b"REAL_PROSPECTIVE_DECISION_ADAPTER_V1_LF\0")
    for name in names:
        digest.update(name.encode()+b"\0"+old_text("src/football_system/"+name).encode()+b"\0")
    assert digest.hexdigest()=="aa55cbe20e31b6ea1b4f4bc1f723832ae34474b85b02222c6d05e66e4bb4b11b"
    bridge,frozen=identities()
    assert bridge!=digest.hexdigest() and frozen==FROZEN_IDENTITIES==expected and len(frozen)==6
