"""Normal integration CI keeps the CI/test identity and every original final gate."""
from pathlib import Path
import subprocess

import pytest

from devtools.ci_policy import project_ci

ROOT=Path(__file__).resolve().parents[2]
BASE="dcc83d1e82f585ae2b4827b0ee7727422b24942a"


def test_only_reviewed_parallel_inputs_and_artifact_capture_change_existing_ci():
    original=subprocess.check_output(["git","show",BASE+":.github/workflows/ci.yml"],cwd=ROOT).replace(b"\r\n",b"\n")
    current=(ROOT/".github/workflows/ci.yml").read_bytes()
    assert project_ci(current)==original
    assert b"name: CI\n" in original and b"  test:\n" in original


@pytest.mark.parametrize("before,after",[(b"name: CI",b"name: New check"),(b"  test:\n",b"  fast:\n"),
    (b'command.check(config)',b'pass'),(b'python scripts/wheel_e2e.py',b'echo skipped')])
def test_required_gate_or_check_identity_drift_is_not_an_approved_projection(before,after):
    current=(ROOT/".github/workflows/ci.yml").read_bytes().replace(b"\r\n",b"\n")
    original=subprocess.check_output(["git","show",BASE+":.github/workflows/ci.yml"],cwd=ROOT).replace(b"\r\n",b"\n")
    assert before in current
    altered=current.replace(before,after,1)
    try:
        projected=project_ci(altered)
    except ValueError:
        return
    assert projected!=original


def test_performance_experiments_are_not_triggered_by_normal_push_or_pr():
    value=(ROOT/".github/workflows/development-speed.yml").read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in value and "  push:" not in value and "  pull_request:" not in value
