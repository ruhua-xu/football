"""Automatic runtime schema and explicit normalized-version audit contracts."""

from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.common import stable_id
from football_system.application.ports.data_providers import MatchResultBatch
from football_system.domain.settlement import MatchResult
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import (
    Base,
    MatchResultRecord,
    ProviderRecord,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
)
from football_system.infrastructure.database.training_correction_schema import (
    CORRECTION_TABLES,
)

from . import test_training_corrections as correction_cases
from .test_database_schema import _schema_signature, _trigger_signature

lane = correction_cases.lane
corrected_lane = correction_cases.corrected_lane


@pytest.mark.parametrize("mode", ["runtime", "migration"])
def test_full_alembic_command_check_has_no_metadata_drift(tmp_path, mode):
    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / (mode + '.db')).as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    engine = create_database_engine(url)
    try:
        if mode == "migration":
            command.upgrade(config, "head")
        else:
            create_schema(engine)
            command.stamp(config, "head")
        before = _schema_signature(engine), _trigger_signature(engine)
        create_schema(engine)
        command.check(config)
        assert _schema_signature(engine) == before[0]
        for name, statement in _trigger_signature(engine):
            assert statement == dict(before[1])[name], name
        assert set(CORRECTION_TABLES) <= Base.metadata.tables.keys()
        assert (
            set(inspect(engine).get_table_names()) - {"alembic_version"}
            == Base.metadata.tables.keys()
        )
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_complete_runtime_and_migration_head_schema_and_triggers_are_identical(
    tmp_path,
):
    config = Config("alembic.ini")
    runtime = create_database_engine(
        f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    )
    migrated = create_database_engine(
        f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    )
    config.set_main_option("sqlalchemy.url", str(migrated.url))
    try:
        create_schema(runtime)
        command.upgrade(config, "head")
        assert _schema_signature(runtime) == _schema_signature(migrated)
        migrated_triggers = dict(_trigger_signature(migrated))
        assert dict(_trigger_signature(runtime)).keys() == migrated_triggers.keys()
        for name, statement in _trigger_signature(runtime):
            assert statement == migrated_triggers[name], name
    finally:
        runtime.dispose()
        migrated.dispose()


def test_create_schema_automatically_upgrades_and_preserves_legacy_v1_rows(lane):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(lane.engine.url))
    command.stamp(config, "e40d183af576")
    command.downgrade(config, "d3fc0729e465")
    correction_cases.prepare(lane, count=1)
    original = correction_cases.admit(lane)
    with lane.engine.connect() as connection:
        before = connection.execute(select(MatchResultRecord.__table__)).all()
    create_schema(lane.engine)
    create_schema(lane.engine)
    with lane.engine.connect() as connection:
        assert connection.execute(select(MatchResultRecord.__table__)).all() == before
        assert (
            connection.scalar(
                text(
                    "SELECT preserved_result_count FROM training_correction_schema_versions"
                )
            )
            == 1
        )
    assert lane.repo.load(original.training_fact_admission_id) == original
    command.stamp(config, "head")
    command.check(config)


def test_equal_time_same_key_versions_use_registered_lineage_not_first_row(
    corrected_lane,
):
    ctx = corrected_lane
    first = correction_cases.record(
        ctx, correction_cases.reviewed_intent(ctx, equal_time=True)
    )
    first_version = ctx.corrections.load_version(first.artifact_id)
    second = correction_cases.record(
        ctx,
        correction_cases.reviewed_intent(
            ctx, previous=first_version, equal_time=True, raw={"home_goals": 4}
        ),
    )
    second_version = ctx.corrections.load_version(second.artifact_id)
    historical = SqlAlchemyHistoricalRepository(ctx.sessions)
    for version in (ctx.base, first_version, second_version):
        expected = MatchResult.model_validate(
            version.normalized_result.model_dump(exclude={"schema_version"})
        )
        assert historical.find_match_result(expected.match_result_id) == expected
        assert historical.append_match_result(expected) == expected
    assert (
        historical.latest_match_results(
            ("match-0",),
            second_version.normalized_result.available_at_utc,
            provider_code="PROVIDER",
        )[0].match_result_id
        == second_version.latest_match_result_id
    )
    assert ctx.repo.load(ctx.base_pin.artifact_id) == ctx.base_admission
    unknown = MatchResult.model_validate(
        second_version.normalized_result.model_dump(exclude={"schema_version"})
    )
    with pytest.raises(ValueError, match="exact registered version ID"):
        historical.append_match_result(
            unknown.model_copy(update={"match_result_id": "not-registered"})
        )
    assert correction_cases.counts(ctx)["match_results"] == 3


@pytest.mark.parametrize("equal_time", [False, True])
def test_noncontrolled_writers_cannot_create_an_admitted_successor(
    corrected_lane, equal_time
):
    ctx = corrected_lane
    original = ctx.submissions[0].candidate.normalized_result
    delta = timedelta(0) if equal_time else timedelta(seconds=1)
    successor = original.model_copy(
        update={
            "match_result_id": "bare",
            "source_result_key": "bare",
            "home_goals": 9,
            "payload_hash": match_result_payload_sha256(9, 1),
            "supersedes_match_result_id": original.match_result_id,
            "available_at_utc": original.available_at_utc + delta,
            "ingested_at_utc": original.ingested_at_utc + delta,
        }
    )
    with pytest.raises((IntegrityError, ValueError)):
        SqlAlchemyHistoricalRepository(ctx.sessions).append_match_result(successor)
    assert correction_cases.counts(ctx)["match_results"] == 1
    assert ctx.repo.load(ctx.base_pin.artifact_id) == ctx.base_admission


def test_normalized_audit_reader_rejects_unregistered_successors_and_forged_equal_time(
    corrected_lane,
):
    ctx = corrected_lane
    original = ctx.submissions[0].candidate.normalized_result
    with ctx.sessions.begin() as session:
        session.execute(text("DROP TRIGGER trg_match_results_controlled_stream_insert"))
        session.execute(text("DROP TRIGGER trg_match_results_supersession_insert"))
        stored = session.get(MatchResultRecord, original.match_result_id)
        values = {
            column.name: getattr(stored, column.name)
            for column in stored.__table__.columns
        }
        values.update(
            match_result_id="forged",
            source_result_key="forged",
            supersedes_match_result_id=original.match_result_id,
        )
        session.execute(MatchResultRecord.__table__.insert().values(**values))
    historical = SqlAlchemyHistoricalRepository(ctx.sessions)
    for key in ("forged", original.match_result_id):
        with pytest.raises(ValueError, match="unregistered result successor"):
            historical.find_match_result(key)
    with pytest.raises(ValueError, match="unregistered result successor"):
        ctx.repo.load(ctx.base_pin.artifact_id)


def test_populated_controlled_downgrade_fails_without_modifying_v1_or_v2(
    corrected_lane,
):
    ctx = corrected_lane
    value = correction_cases.record(
        ctx, correction_cases.reviewed_intent(ctx, equal_time=True)
    )
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(ctx.engine.url))
    command.stamp(config, "e40d183af576")
    before = correction_cases.counts(ctx)
    with pytest.raises(RuntimeError, match="immutable correction lineage"):
        command.downgrade(config, "d3fc0729e465")
    assert correction_cases.counts(ctx) == before
    assert ctx.corrections.load_admission(value.artifact_id) == value
    assert ctx.repo.load(ctx.base_pin.artifact_id) == ctx.base_admission


def test_batch_retry_selects_exact_corrected_id_among_same_provider_keys(lane):
    # Seed the legacy batch writer's registered provider identity convention.
    # No admission/correction proof is mocked or bypassed.
    def standard_provider(session, flush_context, instances):
        for row in session.new:
            if isinstance(row, ProviderRecord):
                row.provider_id = stable_id("provider", "PROVIDER")
                row.provider_kind = "FIXTURE"
            elif getattr(row, "provider_id", None) == "provider":
                row.provider_id = stable_id("provider", "PROVIDER")

    event.listen(lane.sessions.class_, "before_flush", standard_provider)
    try:
        ctx = correction_cases.corrected_lane.__wrapped__(lane)
    finally:
        event.remove(lane.sessions.class_, "before_flush", standard_provider)
    value = correction_cases.record(
        ctx, correction_cases.reviewed_intent(ctx, equal_time=True)
    )
    version = ctx.corrections.load_version(value.artifact_id)
    normalized = MatchResult.model_validate(
        version.normalized_result.model_dump(exclude={"schema_version"})
    )
    batch = MatchResultBatch(
        as_of_at_utc=normalized.available_at_utc,
        results=(normalized,),
        mappings=(ctx.submissions[0].candidate.provider_mapping,),
    )
    historical = SqlAlchemyHistoricalRepository(ctx.sessions)
    assert historical.append_match_result_batch(batch) == batch
    assert correction_cases.counts(ctx)["match_results"] == 2
    with pytest.raises(ValueError, match="exact registered version ID"):
        historical.append_match_result_batch(
            batch.model_copy(
                update={
                    "results": (
                        normalized.model_copy(
                            update={"match_result_id": "unregistered-revision"}
                        ),
                    )
                }
            )
        )
