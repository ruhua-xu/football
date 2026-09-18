import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_p_process_hash_seed_and_candidate_input_order():
    code = r'''
import hashlib,sys
from decimal import getcontext,ROUND_UP
from tests.unit.return_distribution_fixtures import fixed_plan,policies
from football_system.domain.archive import canonical_json
from football_system.domain.services.return_optimizer import optimize_portfolio
getcontext().prec=int(sys.argv[1]); getcontext().rounding=ROUND_UP
requests=[{'choices':'AB'},{'choices':'CD'}]
if int(sys.argv[1])<10: requests.reverse()
plan=fixed_plan(dict.fromkeys('ABCD','.6'),dict.fromkeys('ABCD','4'),requests)
policy,objective=policies()
value=optimize_portfolio(plan,policy=policy,objective=objective)
print(hashlib.sha256(canonical_json(value).encode()).hexdigest())
'''
    hashes = []
    for seed, precision in (("0", "28"), ("117", "6"), ("random", "192")):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", "-c", code, precision], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        hashes.append(result.stdout.strip())
    assert len(set(hashes)) == 1


@pytest.mark.parametrize("relative", [
    "domain/market_v2.py", "domain/strategy_pass_v2.py", "domain/strategy_pass.py", "domain/settlement_v2.py",
    "domain/services/strategy_pass_v2.py", "domain/services/strategy_pass.py", "domain/services/settlement_v2.py",
    "domain/services/strategy_settlement.py", "domain/services/payout.py", "domain/services/probability.py",
    "domain/services/elo_baseline.py", "domain/services/poisson_goals.py", "domain/services/review_v4.py",
    "domain/review_v4.py", "domain/review.py", "domain/goal_model.py", "domain/services/market_analysis.py",
    "infrastructure/database/market_v2_schema.py", "infrastructure/database/market_v2_repository.py",
])
def test_s_frozen_v080_math_and_artifact_bytes(relative):
    name = "src/football_system/" + relative
    old = subprocess.check_output(["git", "show", "v0.8.0:" + name], cwd=ROOT)
    actual = (ROOT / name).read_bytes()
    assert actual.replace(b"\r\n", b"\n") == old.replace(b"\r\n", b"\n")


def test_s_old_migrations_are_unchanged_and_no_real_source_refs_in_acceptance():
    files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "v0.8.0", "migrations/versions"], cwd=ROOT).decode().splitlines()
    for name in files:
        assert (ROOT / name).read_bytes().replace(b"\r\n", b"\n") == subprocess.check_output(["git", "show", "v0.8.0:" + name], cwd=ROOT).replace(b"\r\n", b"\n")
    fixture = ROOT / "data/fixtures/return_distribution_v1.json"
    doc = json.loads(fixture.read_bytes())
    assert doc["data_classification"] == "SYNTHETIC_ONLY"
    assert doc["real_source_artifact_references"] == []
