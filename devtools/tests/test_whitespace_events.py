"""Execute the actual CI shell step against isolated real Git event histories."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap

import pytest

ROOT=Path(__file__).resolve().parents[2]
ENV={**os.environ,"GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":os.devnull,"GIT_AUTHOR_NAME":"Synthetic CI test",
    "GIT_COMMITTER_NAME":"Synthetic CI test","GIT_AUTHOR_EMAIL":"ci@example.invalid","GIT_COMMITTER_EMAIL":"ci@example.invalid"}


def git(root,*args):
    return subprocess.check_output(["git","-c","core.autocrlf=false",*args],cwd=root,env=ENV,stderr=subprocess.PIPE,text=True).strip()


def commit(root,text,label):
    (root/"sample.txt").write_text(text,encoding="utf-8",newline="\n")
    git(root,"add","sample.txt")
    git(root,"commit","-m",label)
    return git(root,"rev-parse","HEAD")


def repository(path):
    path.mkdir()
    git(path,"init","--initial-branch=main")
    return commit(path,"baseline\n","baseline")


def run_step(root,event,head,base,*,workers="4",branch="feature",overrides=None):
    workflow=(ROOT/".github/workflows/ci.yml").read_text(encoding="utf-8")
    script=textwrap.dedent(workflow.split("      - name: Check whitespace\n",1)[1].split("        run: |\n",1)[1])
    # Select the test interpreter only; event logic is the actual workflow/tool.
    script=script.replace("python -m devtools.whitespace",shlex.quote(Path(sys.executable).as_posix())+" -m devtools.whitespace")
    bash=Path(git(root,"--exec-path")).parents[2]/"bin/bash.exe" if os.name=="nt" else shutil.which("bash")
    assert bash
    values=dict(EVENT_NAME=event,GITHUB_SHA=head,GITHUB_REF="refs/heads/"+branch,DEFAULT_BRANCH="main",CI_TEST_WORKERS=workers,
        PR_BASE_SHA=base if event=="pull_request" else "",PR_HEAD_SHA=head if event=="pull_request" else "",
        PUSH_BEFORE_SHA=base if event=="push" else "",PUSH_AFTER_SHA=head if event=="push" else "")
    values.update(overrides or {})
    result=subprocess.run([str(bash),"--noprofile","--norc","-e","-o","pipefail","-c",script],cwd=root,
        env=ENV|values|{"PYTHONPATH":str(ROOT),"PYTHONUTF8":"1","PYTHONDONTWRITEBYTECODE":"1"},capture_output=True,text=True,encoding="utf-8",timeout=60)
    (root.parent/"whitespace-event-proof.json").write_text(json.dumps(dict(case_id=os.environ.get("PYTEST_CURRENT_TEST"),event=event,branch=branch,workers=workers,inputs=values,
        returncode=result.returncode,stdout=result.stdout,stderr=result.stderr),indent=2),encoding="utf-8")
    return result


@pytest.mark.parametrize("event,branch,workers",[("push","feature","4"),("pull_request","feature","4"),
    ("workflow_dispatch","feature","4"),("workflow_dispatch","feature","0"),
    ("workflow_dispatch","main","4"),("workflow_dispatch","main","0")])
@pytest.mark.parametrize("bad_whitespace",[False,True])
def test_real_event_matrix_enforces_whitespace(tmp_path,event,branch,workers,bad_whitespace):
    root=tmp_path/"repo"
    base=repository(root)
    git(root,"update-ref","refs/remotes/origin/main",base)
    if branch!="main":
        git(root,"switch","-c",branch)
    head=commit(root,"changed line"+(" " if bad_whitespace else "")+"\n","change")
    if branch=="main":
        git(root,"update-ref","refs/remotes/origin/main",head)
    result=run_step(root,event,head,base,workers=workers,branch=branch)
    assert result.returncode==(2 if bad_whitespace else 0),result.stdout+result.stderr
    if bad_whitespace:
        assert "trailing whitespace" in result.stdout+result.stderr


@pytest.mark.parametrize("event",["push","workflow_dispatch"])
@pytest.mark.parametrize("bad_whitespace",[False,True])
def test_initial_commit_uses_empty_tree_and_still_checks_whitespace(tmp_path,event,bad_whitespace):
    root=tmp_path/"repo"
    root.mkdir()
    git(root,"init","--initial-branch=main")
    head=commit(root,"initial"+(" " if bad_whitespace else "")+"\n","root")
    git(root,"update-ref","refs/remotes/origin/main",head)
    result=run_step(root,event,head,"0"*40,branch="main")
    assert result.returncode==(2 if bad_whitespace else 0),result.stdout+result.stderr
    if bad_whitespace:
        assert "trailing whitespace" in result.stdout+result.stderr


@pytest.mark.parametrize("fault",["missing_before","invalid_before","missing_after","deleted_after","missing_default_branch",
    "missing_default_ref","invalid_branch_ref","checkout_mismatch","unsupported_event"])
def test_missing_or_invalid_range_is_an_explicit_error(tmp_path,fault):
    root=tmp_path/"repo"
    base=repository(root)
    head=commit(root,"changed\n","change")
    git(root,"update-ref","refs/remotes/origin/main",head)
    event="push" if fault in {"missing_before","invalid_before","missing_after","deleted_after"} else "workflow_dispatch"
    values={"missing_before":{"PUSH_BEFORE_SHA":""},"invalid_before":{"PUSH_BEFORE_SHA":"1"*40},"missing_after":{"PUSH_AFTER_SHA":""},
        "deleted_after":{"PUSH_AFTER_SHA":"0"*40},"missing_default_branch":{"DEFAULT_BRANCH":""},"missing_default_ref":{"DEFAULT_BRANCH":"unavailable"},
        "invalid_branch_ref":{"GITHUB_REF":"refs/heads/../bad"},"checkout_mismatch":{"GITHUB_SHA":base},"unsupported_event":{"EVENT_NAME":"unknown"}}[fault]
    result=run_step(root,event,head,base,branch="main",overrides=values)
    assert result.returncode!=0 and "WHITESPACE_" in result.stderr,result.stdout+result.stderr


def test_shallow_history_fails_closed_with_fetch_instruction(tmp_path):
    source=tmp_path/"source"
    base=repository(source)
    head=commit(source,"changed\n","change")
    root=tmp_path/"shallow"
    git(tmp_path,"clone","--depth","1","--no-local",source.as_uri(),str(root))
    assert git(root,"rev-parse","--is-shallow-repository")=="true"
    result=run_step(root,"workflow_dispatch",head,base,branch="main")
    assert result.returncode!=0 and "WHITESPACE_SHALLOW_HISTORY" in result.stderr and "fetch-depth: 0" in result.stderr


def test_feature_equal_to_default_tip_does_not_silently_skip_last_commit(tmp_path):
    root=tmp_path/"repo"
    repository(root)
    head=commit(root,"bad line \n","bad-default-tip")
    git(root,"update-ref","refs/remotes/origin/main",head)
    git(root,"switch","-c","feature")
    result=run_step(root,"workflow_dispatch",head,head)
    assert result.returncode==2 and "trailing whitespace" in result.stdout+result.stderr


def test_manual_default_merge_checks_the_integrated_first_parent_change(tmp_path):
    root=tmp_path/"repo"
    base=repository(root)
    git(root,"switch","-c","feature")
    commit(root,"bad merge content \n","feature")
    git(root,"switch","main")
    git(root,"merge","--no-ff","feature","-m","merge feature")
    head=git(root,"rev-parse","HEAD")
    git(root,"update-ref","refs/remotes/origin/main",head)
    result=run_step(root,"workflow_dispatch",head,base,branch="main")
    assert result.returncode==2 and "trailing whitespace" in result.stdout+result.stderr


def test_manual_feature_without_common_ancestor_is_an_explicit_error(tmp_path):
    root=tmp_path/"repo"
    base=repository(root)
    git(root,"update-ref","refs/remotes/origin/main",base)
    git(root,"switch","--orphan","feature")
    head=commit(root,"unrelated\n","unrelated root")
    result=run_step(root,"workflow_dispatch",head,base)
    assert result.returncode!=0 and "WHITESPACE_NO_UNIQUE_MERGE_BASE" in result.stderr
