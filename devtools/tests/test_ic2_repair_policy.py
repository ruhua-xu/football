"""The permitted migration repair is exactly two anchored guards, never a broad exclusion."""
from pathlib import Path

import pytest

from devtools.integration_history import PFB, archive_files
from devtools.repair_policy import BIND, GUARDED_BIND, MIGRATION, COMPATIBILITY_HOOK, project_repair
from devtools.rc_policy import project_rc

ROOT=Path(__file__).resolve().parents[2]


def test_migration_and_compatibility_hook_project_exactly_to_the_accepted_bytes():
    old=archive_files(ROOT,PFB,MIGRATION,COMPATIBILITY_HOOK)
    for name,expected in old.items():
        assert project_repair(name,project_rc(name,(ROOT/name).read_bytes().replace(b"\r\n",b"\n")))==expected


@pytest.mark.parametrize("fault",["missing_guard","unconditional_begin","moved_guard","changed_head","changed_body"])
def test_the_repair_exception_rejects_other_migration_changes(fault):
    raw=(ROOT/MIGRATION).read_bytes().replace(b"\r\n",b"\n")
    if fault=="missing_guard":
        raw=raw.replace(GUARDED_BIND.encode(),BIND.encode(),1)
    elif fault=="unconditional_begin":
        raw=raw.replace(b"if not connection.connection.driver_connection.in_transaction:",b"if True:",1)
    elif fault=="moved_guard":
        raw=raw.replace(GUARDED_BIND.encode(),BIND.encode(),1)+(GUARDED_BIND[len(BIND):]).encode()
    elif fault=="changed_head":
        raw=raw.replace(b'"8ea7bcd49510"',b'"wrong-parent"',1)
    else:
        raw=raw.replace(b"checkfirst=True",b"checkfirst=False",1)
    with pytest.raises(ValueError,match="IC2_REPAIR|MIGRATION_REPAIR"):
        project_repair(MIGRATION,raw)
