"""Layered v1.4 integration freeze and optional actual-production boundary checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from devtools.ci_policy import project_ci  # noqa: E402
from devtools.integration_history import PFB, RELEASE, archive_files, audit  # noqa: E402
from devtools.repair_policy import project_repair  # noqa: E402
from devtools.rc_policy import project_rc, verify_rc_scope  # noqa: E402

PUBLISHED_ROOTS=("src/football_system/domain/services","config","data/fixtures")
PUBLISHED_FILES=("daily.cmd","alembic.ini",
    "src/football_system/application/market_consensus.py","src/football_system/application/return_distribution.py",
    "src/football_system/application/strategy_pass.py","src/football_system/application/settlement.py",
    "src/football_system/application/prospective.py","src/football_system/application/quant_model.py",
    "src/football_system/infrastructure/files/prospective.py","src/football_system/infrastructure/files/return_distribution.py",
    "src/football_system/infrastructure/providers/real/the_odds_api.py")
PROJECTION_FILES=("tests/contract/p1b_projection.json","tests/contract/pfb_projection.json",
    "tests/contract/scoped_projection.py","tests/contract/portfolio_projection.py")
ELO_CONFIG_HASH="c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"


def freeze_layers(root):
    """Published kernels/data and explicitly accepted seams have distinct anchors.

    The original compatibility tests still apply their exact reverse projections.
    New integration/test/tool modules are not banned merely for differing from PF-B;
    changes to a frozen layer require explicit review of that layer's contract.
    """
    projections=archive_files(root,PFB,*PROJECTION_FILES)
    manifests=[json.loads(projections[name]) for name in PROJECTION_FILES if name.endswith(".json")]
    seams={name for manifest in manifests for name in (*manifest["files"],*manifest["new_files"])}
    seams.add("src/football_system/domain/openfootball_scope.py")  # Accepted P1-A contract.
    if any(any(name==prefix or name.startswith(prefix+"/") for prefix in PUBLISHED_ROOTS) for name in seams):
        raise ValueError("INTEGRATION_SEAM_CANNOT_EXEMPT_FROZEN_KERNEL_OR_DATA")
    published=archive_files(root,RELEASE,"src/football_system/domain",*PUBLISHED_ROOTS[1:],*PUBLISHED_FILES)
    published={name:raw for name,raw in published.items() if name not in seams}
    return [dict(name="published_v131_kernels_contracts_config_and_fixtures",baseline=RELEASE,
            files=published,exclusive_roots=PUBLISHED_ROOTS),
        dict(name="accepted_p1b_pfb_registration_and_financial_contracts",baseline=PFB,
            files=archive_files(root,PFB,*sorted(seams)),exclusive_roots=()),
        dict(name="accepted_exact_projection_contracts",baseline=PFB,files=projections,exclusive_roots=())]


def verify_layers(root,layers):
    results=[]
    for layer in layers:
        expected=layer["files"]
        for name,raw in expected.items():
            path=root/name
            if not path.is_file() or project_repair(name,project_rc(name,path.read_bytes().replace(b"\r\n",b"\n")))!=raw:
                raise ValueError("FROZEN_LAYER_BYTES_CHANGED:"+layer["name"]+":"+name)
        for folder in layer["exclusive_roots"]:
            actual={p.relative_to(root).as_posix() for p in (root/folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
            names={name for name in expected if name.startswith(folder+"/")}
            if actual!=names:
                raise ValueError("FROZEN_LAYER_FILE_SET_CHANGED:"+folder)
        hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in sorted(expected.items())}
        results.append(dict(layer=layer["name"],baseline=layer["baseline"],files=len(expected),
            manifest_sha256=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()))
    return results


def verify(root,boundary=None):
    root=Path(root)
    layers=verify_layers(root,freeze_layers(root))
    rc_scope=verify_rc_scope(root)
    history=audit(root)
    ci_name=".github/workflows/ci.yml"
    ci_base=archive_files(root,RELEASE,ci_name)[ci_name]
    if project_ci((root/ci_name).read_bytes())!=ci_base:
        raise ValueError("PUBLISHED_REQUIRED_CI_GATES_CHANGED")
    frozen=__import__("football_system.infrastructure.files.real_bridge",fromlist=["identities","FROZEN_IDENTITIES"])
    from football_system.domain.services.elo_baseline import EloBaselineConfig
    if frozen.identities()[1]!=frozen.FROZEN_IDENTITIES or EloBaselineConfig().config_hash!=ELO_CONFIG_HASH:
        raise ValueError("FROZEN_MATHEMATICAL_IDENTITY_CHANGED")
    production="NOT_REQUESTED_ON_THIS_HOST"
    if boundary:
        expected=json.loads(Path(boundary).read_bytes())
        for name,record in expected["files"].items():
            path=Path(name)
            before=path.stat()
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            after=path.stat()
            assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
            assert actual==record["sha256"] and [after.st_dev,after.st_ino]==record["file_identity"],name
        production="PASS_EXACT_PROTECTED_BYTES_AND_FILE_IDENTITIES"
    return dict(status="PASS",policy="V140_LAYERED_FREEZE",layers=layers,history_and_migrations=history,rc_scope=rc_scope,
        required_ci_workflow="EXACT_APPROVED_PARALLEL_PROJECTION_ORIGINAL_GATES_RETAINED",
        frozen_hashes=frozen.FROZEN_IDENTITIES,elo_config_hash=ELO_CONFIG_HASH,
        production_boundary=production,production_sql_connections=0,production_writes=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    value=verify(Path(__file__).resolve().parents[1],args.boundary)
    if args.output:
        with args.output.open("x",encoding="utf-8") as stream:
            json.dump(value,stream,indent=2)
    print(json.dumps(value,indent=2))


if __name__=="__main__":
    main()
