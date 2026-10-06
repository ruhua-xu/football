"""Explicit additive compatibility; never admit an unknown migration head."""
from sqlalchemy import text

from football_system.infrastructure.database.ofp_real_model_pin_schema import HEAD, assert_ofp_real_model_pin_schema
from football_system.infrastructure.database.real_bridge_schema import assert_real_schema

SUPPORTED_REAL_BRIDGE_HEADS = frozenset({"6c859ab273fe", "7d96abc3840f", HEAD})


def assert_real_bridge_head(session):
    heads=session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(heads) != 1 or heads[0] not in SUPPORTED_REAL_BRIDGE_HEADS:
        raise ValueError("REAL_BRIDGE_MIGRATION_HEAD_REQUIRED")
    assert_real_schema(session.connection())
    if heads[0] == HEAD:
        assert_ofp_real_model_pin_schema(session.connection())
    return heads[0]
