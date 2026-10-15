"""Exact PF-B integration hunks, projected before the accepted P1-B baseline."""
import hashlib
import json
from pathlib import Path

MANIFEST=json.loads(Path(__file__).with_name("pfb_projection.json").read_text(encoding="utf-8"))
PORTFOLIO_NEW_FILES=set(MANIFEST["new_files"])
PORTFOLIO_RUNTIME_NEW_FILES={name for name in PORTFOLIO_NEW_FILES if not name.startswith("scripts/")}


def project_portfolio(relative,raw):
    record=MANIFEST["files"].get(relative)
    if record is None:
        return raw
    assert hashlib.sha256(raw).hexdigest()==record["after_sha256"],(relative,"UNREVIEWED_PF_B_BYTES")
    lines=raw.decode().splitlines(keepends=True)
    for hunk in reversed(record["hunks"]):
        start=hunk["after_start"]
        assert lines[start:start+len(hunk["after"])]==hunk["after"],(relative,"EXACT_PF_B_HUNK_REQUIRED")
        lines[start:start+len(hunk["after"])]=hunk["before"]
    raw="".join(lines).encode()
    assert hashlib.sha256(raw).hexdigest()==record["before_sha256"],relative
    return raw
