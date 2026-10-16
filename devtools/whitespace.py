"""Check a resolved Git range for each supported CI event; never accept unknown ranges."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class RangeError(ValueError):
    pass


def git(root,*args,input=None):
    return subprocess.run(["git",*args],cwd=root,input=input,capture_output=True,text=True,encoding="utf-8")


def required(value,label):
    if not value or value!=value.strip():
        raise RangeError("WHITESPACE_MISSING_OR_INVALID_"+label)
    return value


def resolve(root,value,label,*,sha=False):
    required(value,label)
    if sha and not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})",value):
        raise RangeError("WHITESPACE_INVALID_SHA_"+label)
    result=git(root,"rev-parse","--verify","--end-of-options",value+"^{commit}")
    if result.returncode:
        raise RangeError("WHITESPACE_UNRESOLVED_"+label+": fetch the required commit/ref before checking")
    return result.stdout.strip()


def empty_tree(root):
    # Only a canonical empty-tree Git object is written; no index/worktree change.
    result=git(root,"hash-object","-w","-t","tree","--stdin",input="")
    if result.returncode:
        raise RangeError("WHITESPACE_EMPTY_TREE_UNAVAILABLE")
    return result.stdout.strip()


def previous_or_empty(root,head):
    result=git(root,"rev-list","--parents","-n","1",head)
    if result.returncode:
        raise RangeError("WHITESPACE_PARENT_HISTORY_UNAVAILABLE")
    parents=result.stdout.split()[1:]
    # First parent checks the integrated change of a merge, not an empty diff
    # against origin/main when the dispatched branch is itself the default branch.
    return (parents[0],"first-parent") if parents else (empty_tree(root),"initial-empty-tree")


def merge_base(root,left,right):
    result=git(root,"merge-base","--all",left,right)
    bases=result.stdout.split()
    if result.returncode or len(bases)!=1:
        raise RangeError("WHITESPACE_NO_UNIQUE_MERGE_BASE")
    return bases[0]


def comparison(root,env):
    shallow=git(root,"rev-parse","--is-shallow-repository")
    if shallow.returncode:
        raise RangeError("WHITESPACE_GIT_CHECKOUT_REQUIRED")
    if shallow.stdout.strip()!="false":
        raise RangeError("WHITESPACE_SHALLOW_HISTORY: use actions/checkout fetch-depth: 0 and fetch required refs")
    checked_out=resolve(root,"HEAD","CHECKOUT")
    declared=resolve(root,env.get("GITHUB_SHA",""),"GITHUB_SHA",sha=True)
    if checked_out!=declared:
        raise RangeError("WHITESPACE_CHECKOUT_SHA_MISMATCH")
    event=required(env.get("EVENT_NAME",""),"EVENT_NAME")
    if event=="push":
        after=required(env.get("PUSH_AFTER_SHA",""),"PUSH_AFTER_SHA")
        if set(after)=={"0"}:
            raise RangeError("WHITESPACE_DELETED_PUSH_REF_HAS_NO_CHECKOUT_RANGE")
        head=resolve(root,after,"PUSH_AFTER_SHA",sha=True)
        if head!=checked_out:
            raise RangeError("WHITESPACE_PUSH_CHECKOUT_MISMATCH")
        before=required(env.get("PUSH_BEFORE_SHA",""),"PUSH_BEFORE_SHA")
        if before=="0"*len(head):
            base,mode=empty_tree(root),"push-initial-empty-tree"
        else:
            base,mode=resolve(root,before,"PUSH_BEFORE_SHA",sha=True),"push-before-after"
    elif event=="pull_request":
        target=resolve(root,env.get("PR_BASE_SHA",""),"PR_BASE_SHA",sha=True)
        head=resolve(root,env.get("PR_HEAD_SHA",""),"PR_HEAD_SHA",sha=True)
        base,mode=merge_base(root,target,head),"pull-request-merge-base"
    elif event=="workflow_dispatch":
        branch=required(env.get("GITHUB_REF",""),"GITHUB_REF")
        default=required(env.get("DEFAULT_BRANCH",""),"DEFAULT_BRANCH")
        if not branch.startswith("refs/heads/") or git(root,"check-ref-format",branch).returncode:
            raise RangeError("WHITESPACE_MANUAL_BRANCH_REF_REQUIRED")
        if git(root,"check-ref-format","refs/heads/"+default).returncode:
            raise RangeError("WHITESPACE_INVALID_DEFAULT_BRANCH")
        target=resolve(root,"refs/remotes/origin/"+default,"DEFAULT_BRANCH_REF")
        head=checked_out
        if branch=="refs/heads/"+default:
            if git(root,"merge-base","--is-ancestor",head,target).returncode:
                raise RangeError("WHITESPACE_DEFAULT_BRANCH_CHECKOUT_NOT_IN_REMOTE_HISTORY")
            base,kind=previous_or_empty(root,head)
            mode="manual-default-"+kind
        else:
            base=merge_base(root,target,head)
            mode="manual-feature-merge-base"
            if base==head:
                base,kind=previous_or_empty(root,head)
                mode="manual-feature-at-base-"+kind
    else:
        raise RangeError("WHITESPACE_UNSUPPORTED_EVENT:"+event)
    return dict(event=event,mode=mode,base=base,head=head,workers=env.get("CI_TEST_WORKERS"))


def main():
    root=Path.cwd()
    try:
        value=comparison(root,os.environ)
    except RangeError as error:
        print(str(error),file=sys.stderr)
        return 1
    print(json.dumps(value),flush=True)
    return subprocess.run(["git","diff","--check",value["base"],value["head"],"--"],cwd=root).returncode


if __name__=="__main__":
    raise SystemExit(main())
