"""RC preparation changes metadata, not accepted application or migration semantics."""
from pathlib import Path

import pytest

from devtools.integration_history import archive_files
from devtools.rc_policy import ACCEPTED, HUNKS, project_rc, verify_rc_scope

ROOT=Path(__file__).resolve().parents[2]


def test_all_runtime_bytes_match_accepted_ic2_except_exact_release_metadata():
    value=verify_rc_scope(ROOT)
    assert value["package_version"]=="1.4.0" and value["business_numerics_migrations_unchanged"]
    assert len(value["metadata_promotions"])==7


@pytest.mark.parametrize("name",sorted(HUNKS))
def test_rc_metadata_reverse_projection_is_exact(name):
    raw=(ROOT/name).read_bytes().replace(b"\r\n",b"\n")
    expected=archive_files(ROOT,ACCEPTED,name)[name]
    assert project_rc(name,raw)==expected
    # A business/code edit cannot hide behind the release-version projection.
    assert project_rc(name,raw+b"\n# UNREVIEWED\n")!=expected
