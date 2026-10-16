"""Guard the integration boundary without reinstating the Phase B whole-tree ban."""
import json
from pathlib import Path

import pytest

from devtools import integration_history, safety
from devtools.repair_policy import MIGRATION, REPAIR_PATHS

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def layers():
    return safety.freeze_layers(ROOT)


def materialize(root,layer):
    for name,raw in layer["files"].items():
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((ROOT/name).read_bytes() if name in REPAIR_PATHS else raw)


def test_actual_candidate_preserves_layered_freeze_and_accepted_history(tmp_path):
    value=safety.verify(ROOT)
    assert value["policy"]=="V140_LAYERED_FREEZE"
    history=value["history_and_migrations"]
    assert history["accepted_runtime_preserved_except_reviewed_repair"]
    assert history["unreviewed_runtime_changes"]==[]
    assert history["approved_migration_repair"]["file"]==MIGRATION
    assert len(history["ordinary_merges"])==3
    assert len(history["migration_dag"]["revisions"])==32
    assert history["migration_dag"]["chain"][-3:]==["8ea7bcd49510","9fb8cde5a621","a0c9def6b732"]
    assert value["production_sql_connections"]==value["production_writes"]==0
    (tmp_path/"v140-layered-safety-proof.json").write_text(json.dumps(value,indent=2),encoding="utf-8")


def test_new_integration_module_is_not_an_old_phase_b_whole_tree_violation(tmp_path,layers):
    for layer in layers:
        materialize(tmp_path,layer)
    extra=tmp_path/"src/football_system/application/new_integration_adapter.py"
    extra.parent.mkdir(parents=True,exist_ok=True)
    extra.write_text('"""New adapter still requires normal review and compatibility tests."""\n',encoding="utf-8")
    assert len(safety.verify_layers(tmp_path,layers))==3
    # Approved V2 pin/Portfolio seams differ from the release and are admitted by
    # their own accepted contract, rather than exempting the numerical kernels.
    name="src/football_system/domain/pinned_model_source.py"
    assert (tmp_path/name).read_bytes()!=integration_history.archive_files(ROOT,integration_history.RELEASE,name)[name]


@pytest.mark.parametrize("name",[
    "src/football_system/domain/services/elo_baseline.py",
    "src/football_system/domain/services/probability.py",
    "src/football_system/domain/betting.py",
    "src/football_system/application/return_distribution.py",
    "config/return_distribution_policy_v1.json",
    "data/fixtures/real_bridge_v1.json",
    "src/football_system/infrastructure/database/portfolio_repository.py",
    "src/football_system/infrastructure/database/openfootball_scoped_repository.py",
    "tests/contract/pfb_projection.json",
])
def test_numerical_historical_financial_and_projection_drift_is_rejected(tmp_path,layers,name):
    layer=next(layer for layer in layers if name in layer["files"])
    materialize(tmp_path,layer)
    path=tmp_path/name
    path.write_bytes(path.read_bytes()+b"\n# unreviewed drift\n")
    with pytest.raises(ValueError,match="FROZEN_LAYER_BYTES_CHANGED"):
        safety.verify_layers(tmp_path,[layer])


def test_new_kernel_cannot_be_hidden_as_a_permitted_integration_addition(tmp_path,layers):
    layer=layers[0]
    materialize(tmp_path,layer)
    (tmp_path/"src/football_system/domain/services/unreviewed_probability.py").write_bytes(b"pass\n")
    with pytest.raises(ValueError,match="FROZEN_LAYER_FILE_SET_CHANGED"):
        safety.verify_layers(tmp_path,[layer])


def migration(revision,parent):
    return f"revision: str = {revision!r}\ndown_revision = {parent!r}\n".encode()


@pytest.mark.parametrize("fault",["duplicate","missing_parent","two_heads","cycle","disconnected"])
def test_migration_revision_conflicts_and_disconnected_history_fail_closed(fault):
    files={"migrations/versions/old.py":migration("old",None),
        "migrations/versions/head.py":migration(integration_history.HEAD,"old")}
    assert integration_history.parse_dag(files)["chain"]==["old",integration_history.HEAD]
    if fault=="duplicate":
        files["migrations/versions/duplicate.py"]=files["migrations/versions/head.py"]
    elif fault=="missing_parent":
        files["migrations/versions/old.py"]=migration("old","missing")
    elif fault=="two_heads":
        files["migrations/versions/branch.py"]=migration("branch","old")
    elif fault=="cycle":
        files["migrations/versions/old.py"]=migration("old",integration_history.HEAD)
    else:
        files["migrations/versions/x.py"]=migration("x","y")
        files["migrations/versions/y.py"]=migration("y","x")
    with pytest.raises(ValueError,match="MIGRATION"):
        integration_history.parse_dag(files)
