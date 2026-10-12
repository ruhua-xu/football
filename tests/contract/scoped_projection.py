"""Replay exact recorded feature hunks; every other baseline byte still compares."""
import hashlib
import json
from pathlib import Path
from tests.contract.portfolio_projection import project_portfolio

MANIFEST = json.loads((Path(__file__).with_name("p1b_projection.json")).read_text(encoding="utf-8"))
SCOPED_NEW_FILES = set(MANIFEST["new_files"])


def project_scoped(relative, raw):
    raw = project_portfolio(relative, raw)
    record = MANIFEST["files"].get(relative)
    if record is None:
        return raw
    assert hashlib.sha256(raw).hexdigest() == record["after_sha256"], (relative, "UNREVIEWED_FEATURE_BYTES")
    for hunk in reversed(record["hunks"]):
        after, before = hunk["after"].encode(), hunk["before"].encode()
        assert raw.count(after) == 1, (relative, "EXACT_FEATURE_HUNK_REQUIRED")
        raw = raw.replace(after, before, 1)
    assert hashlib.sha256(raw).hexdigest() == record["before_sha256"], relative
    return raw
