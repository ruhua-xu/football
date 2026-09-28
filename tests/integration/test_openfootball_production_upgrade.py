"""Additive migration checks against the genuine frozen v1.1 schema."""

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from football_system.domain.openfootball_production import OpenFootballProductionArtifactV1
from football_system.domain.archive import canonical_json
from football_system.infrastructure.database.openfootball_production_schema import OFP_TABLES
from football_system.infrastructure.database.session import create_database_engine
from tests.integration.test_market_v2_upgrade import database_snapshot
from datetime import datetime, timezone


def test_openfootball_upgrade_preserves_old_schema_and_empty_roundtrip(tmp_path):
    path = tmp_path / "upgrade.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", "sqlite:///" + path.as_posix())
    command.upgrade(config, "6c859ab273fe")
    before = database_snapshot(path)
    command.upgrade(config, "head")
    command.check(config)
    after = database_snapshot(path)
    for section in range(3):
        assert all(after[section][name] == value for name, value in before[section].items())
    assert set(OFP_TABLES) <= set(after[0])
    command.downgrade(config, "6c859ab273fe")
    assert database_snapshot(path) == before
    command.upgrade(config, "head")
    command.check(config)


def test_openfootball_populated_downgrade_refused(tmp_path):
    path = tmp_path / "populated.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", "sqlite:///" + path.as_posix())
    command.upgrade(config, "head")
    engine = create_database_engine("sqlite:///" + path.as_posix())
    artifact = OpenFootballProductionArtifactV1.freeze(kind="DATA_BINDING", payload={"classification":"SYNTHETIC_DOWNGRADE_TEST_ONLY"},
        parents=(), recorded_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO ofp_artifacts VALUES (:id,:kind,:hash,:json,:at)"),
            dict(id=artifact.artifact_id, kind=artifact.kind, hash=artifact.artifact_hash, json=canonical_json(artifact),
                 at=artifact.model_dump(mode="json")["recorded_at_utc"]))
    before = database_snapshot(path)
    with pytest.raises(RuntimeError, match="populated OpenFootball"):
        command.downgrade(config, "6c859ab273fe")
    assert database_snapshot(path) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "7d96abc3840f"
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
