"""Exact IC2 repair projections; no baseline/manifests or numerical files are exempted."""
import hashlib

MIGRATION="migrations/versions/9fb8cde5a621_add_scoped_openfootball_ledger.py"
COMPATIBILITY_HOOK="tests/contract/portfolio_projection.py"
REPAIR_PATHS=frozenset((MIGRATION,COMPATIBILITY_HOOK))
MIGRATION_BEFORE_SHA256="2702e4ad8344c7d300a862869f6bca9dd5ac67fc2ecb8801ab036fb3c9552349"
BIND='    connection = op.get_bind()\n'
GUARDED_BIND=BIND+('    # Alembic owns commit/rollback and the head update; SQLite DDL needs BEGIN.\n'
    '    if not connection.connection.driver_connection.in_transaction:\n'
    '        connection.exec_driver_sql("BEGIN IMMEDIATE")\n')


def undo(raw,before,after,count=1):
    if raw.count(after.encode())!=count:
        raise ValueError("EXACT_IC2_REPAIR_HUNK_REQUIRED")
    return raw.replace(after.encode(),before.encode())


def project_repair(relative,raw):
    if relative==MIGRATION:
        raw=undo(raw,BIND,GUARDED_BIND,2)
        if hashlib.sha256(raw).hexdigest()!=MIGRATION_BEFORE_SHA256:
            raise ValueError("UNREVIEWED_SCOPED_MIGRATION_REPAIR_BYTES")
    elif relative==COMPATIBILITY_HOOK:
        raw=undo(raw,"from pathlib import Path\n","from pathlib import Path\nfrom devtools.repair_policy import project_repair\n")
        raw=undo(raw,"def project_portfolio(relative,raw):\n","def project_portfolio(relative,raw):\n    raw=project_repair(relative,raw)\n")
    return raw
