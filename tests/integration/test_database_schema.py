import re
from io import StringIO
from datetime import datetime, timezone
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from football_system.infrastructure.database.models import (
    AnalysisRunRecord,
    PortfolioRecord,
    PortfolioRiskReportRecord,
)
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import (
    configure_sqlite_engine,
    create_database_engine,
    create_schema,
    create_session_factory,
    require_sqlite_database_url,
)


IDENTITY_MIGRATION_REVISION = "d2e7a4c9b615"
CURRENT_MIGRATION_HEAD = "6e4b1a9c2d73"
QUANT_MODEL_MIGRATION_REVISION = "b7d4e9f2c631"
BACKTEST_V2_MIGRATION_REVISION = "c4e8a1d7f205"
IDENTITY_TABLES = {
    "provider_team_aliases",
    "provider_competition_mappings",
    "canonical_match_identities",
}
FIXTURE_INGESTION_TABLES = {
    "fixture_ingestion_captures",
    "fixture_observations",
}
QUANT_MODEL_TABLES = {
    "quant_model_states",
    "quant_model_training_facts",
    "quant_model_evaluations",
}
BACKTEST_V2_TABLES = {
    "backtest_v2_runs",
    "backtest_v2_run_archives",
    "backtest_v2_slices",
    "backtest_v2_training_sources",
    "backtest_v2_evaluation_refs",
    "backtest_v2_result_sources",
    "backtest_v2_slice_ticket_settlements",
    "backtest_v2_metric_snapshots",
}
LIVE_SOURCE_TABLES = {
    "live_source_ingestions",
    "live_source_ingestion_artifacts",
    "live_source_ingestion_mappings",
    "live_source_ingestion_market_snapshots",
    "live_market_consensus_lineages",
    "live_market_consensus_constituents",
    "live_source_ingestion_sporttery_snapshots",
    "live_source_ingestion_issues",
    "live_identity_reviews",
    "live_identity_review_mappings",
    "live_analysis_preparations",
    "live_analysis_preparation_matches",
    "live_analysis_run_preparations",
}
FIXTURE_IDENTITY_ORIGIN_INDEXES = {
    "matches": "ix_matches_fixture_ingestion",
    "provider_team_aliases": "ix_provider_team_alias_fixture_ingestion",
    "provider_competition_mappings": (
        "ix_provider_competition_mapping_fixture_ingestion"
    ),
    "canonical_match_identities": ("ix_canonical_match_identity_fixture_ingestion"),
    "provider_match_mappings": "ix_provider_match_mapping_fixture_ingestion",
}


def test_schema_contains_mvp_tables_and_enables_foreign_keys() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)

    tables = set(inspect(engine).get_table_names())
    assert {
        "matches",
        "market_odds_snapshots",
        "market_odds_quotes",
        "sporttery_bonus_snapshots",
        "sporttery_bonus_quotes",
        "manual_quant_inputs",
        "manual_quant_input_outcomes",
        "fixture_ingestion_captures",
        "fixture_observations",
        "analysis_runs",
        "market_probabilities",
        "quant_predictions",
        "final_predictions",
        "bet_candidates",
        "ticket_candidates",
        "portfolios",
        "portfolio_cash_positions",
        "portfolio_risk_reports",
        "portfolio_match_exposures",
        "portfolio_selection_exposures",
        "portfolio_stress_results",
        "portfolio_stress_ticket_results",
        "analysis_packets",
        "llm_review_artifacts",
        "fusion_runs",
        "fusion_run_results",
        "portfolio_revisions",
        "tickets",
    } <= tables
    assert QUANT_MODEL_TABLES <= tables
    assert BACKTEST_V2_TABLES <= tables
    assert LIVE_SOURCE_TABLES <= tables
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA recursive_triggers")) == 1


def test_quant_model_schema_has_exclusive_lineage_and_sealing_triggers() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    inspector = inspect(engine)

    context_columns = {
        column["name"]: column
        for column in inspector.get_columns("analysis_run_matches")
    }
    prediction_columns = {
        column["name"]: column for column in inspector.get_columns("quant_predictions")
    }
    assert context_columns["manual_quant_input_id"]["nullable"] is True
    assert context_columns["quant_model_evaluation_id"]["nullable"] is True
    assert prediction_columns["manual_input_id"]["nullable"] is True
    assert prediction_columns["input_payload_hash"]["nullable"] is True
    assert prediction_columns["entered_at_utc"]["nullable"] is True
    assert prediction_columns["generated_at_utc"]["nullable"] is True
    assert {
        item["name"] for item in inspector.get_check_constraints("quant_predictions")
    } >= {"ck_quant_prediction_source"}
    assert {
        item["name"] for item in inspector.get_check_constraints("analysis_run_matches")
    } >= {"ck_analysis_run_match_quant_source"}
    assert {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
        )
        for item in inspector.get_foreign_keys("quant_model_training_facts")
    } >= {
        (("match_result_id",), "match_results", ("match_result_id",)),
        (("internal_match_id",), "matches", ("internal_match_id",)),
    }
    with engine.connect() as connection:
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
    assert "trg_analysis_runs_completion_quant_model_graph" in triggers
    for table_name in QUANT_MODEL_TABLES:
        assert f"trg_{table_name}_immutable_insert_existing" in triggers
        assert f"trg_{table_name}_append_only_update" in triggers
        assert f"trg_{table_name}_append_only_delete" in triggers
        assert f"trg_{table_name}_sealed_insert" in triggers
    engine.dispose()


def test_live_source_migration_upgrades_backtest_head_and_empty_downgrade(
    tmp_path,
) -> None:
    database_path = tmp_path / "live-source-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, BACKTEST_V2_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert not LIVE_SOURCE_TABLES & set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, CURRENT_MIGRATION_HEAD)
    engine = create_database_engine(database_url)
    assert LIVE_SOURCE_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
    for table_name in LIVE_SOURCE_TABLES:
        assert f"trg_{table_name}_immutable_insert_existing" in triggers
        assert f"trg_{table_name}_append_only_update" in triggers
        assert f"trg_{table_name}_append_only_delete" in triggers
    assert "trg_analysis_runs_completion_live_preparation" in triggers
    engine.dispose()

    command.downgrade(config, BACKTEST_V2_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert not LIVE_SOURCE_TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            BACKTEST_V2_MIGRATION_REVISION
        )
    engine.dispose()


def test_live_source_migration_rejects_populated_downgrade(tmp_path) -> None:
    database_path = tmp_path / "live-source-populated.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, CURRENT_MIGRATION_HEAD)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO providers "
                "(provider_id, code, name, provider_kind) "
                "VALUES ('live-provider', 'LIVE_PROVIDER', "
                "'Live Provider', 'MARKET_ODDS')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO live_source_ingestions "
                "(ingestion_id, schema_version, source_kind, provider_id, "
                "data_mode, status, identity_cutoff_at_utc, "
                "source_ingested_at_utc, persisted_at_utc, "
                "requested_match_ids_json, artifact_count, snapshot_count, "
                "mapping_count, issue_count, consensus_count, capture_json, "
                "capture_hash) VALUES "
                "('live-ingestion', 'LIVE_SOURCE_INGESTION_V1', "
                "'MARKET_ODDS', 'live-provider', 'LIVE_STRICT', 'COMPLETED', "
                "'2026-09-01 00:00:00', '2026-09-01 00:00:01', "
                "'2026-09-01 00:00:02', '[\"match\"]', 1, 0, 0, 0, 0, "
                "'{}', :capture_hash)"
            ),
            {"capture_hash": "a" * 64},
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="immutable lineage exists"):
        command.downgrade(config, BACKTEST_V2_MIGRATION_REVISION)


def test_identity_schema_contains_foreign_keys_unique_lookups_and_triggers() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    inspector = inspect(engine)

    assert IDENTITY_TABLES <= set(inspector.get_table_names())
    assert {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
            item["options"].get("ondelete"),
        )
        for table_name in IDENTITY_TABLES
        for item in inspector.get_foreign_keys(table_name)
    } == {
        (("internal_team_id",), "teams", ("team_id",), "RESTRICT"),
        (("provider_id",), "providers", ("provider_id",), "RESTRICT"),
        (
            ("internal_competition_id",),
            "competitions",
            ("competition_id",),
            "RESTRICT",
        ),
        (("internal_match_id",), "matches", ("internal_match_id",), "RESTRICT"),
    }
    team_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("provider_team_aliases")
    }
    assert (
        "provider_id",
        "provider_team_id",
        "provider_team_name",
        "language",
        "team_type",
        "internal_team_id",
    ) in team_unique
    competition_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("provider_competition_mappings")
    }
    assert (
        "provider_id",
        "provider_competition_id",
        "provider_competition_name",
        "language",
        "season",
        "competition_type",
        "internal_competition_id",
    ) in competition_unique
    assert {
        item["name"]: tuple(item["column_names"])
        for table_name in IDENTITY_TABLES
        for item in inspector.get_indexes(table_name)
    } == {
        "ix_provider_team_alias_lookup_cutoff": (
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "available_at_utc",
        ),
        "ix_provider_competition_mapping_lookup_cutoff": (
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "available_at_utc",
        ),
        "ix_canonical_match_identity_season_type_cutoff": (
            "season",
            "competition_type",
            "available_at_utc",
        ),
        "ix_provider_team_alias_fixture_ingestion": ("fixture_ingestion_id",),
        "ix_provider_competition_mapping_fixture_ingestion": ("fixture_ingestion_id",),
        "ix_canonical_match_identity_fixture_ingestion": ("fixture_ingestion_id",),
    }
    with engine.connect() as connection:
        trigger_sql = {
            name: sql
            for name, sql in connection.execute(
                text(
                    "SELECT name, lower(sql) FROM sqlite_master WHERE type = 'trigger'"
                )
            )
        }
    for table_name in IDENTITY_TABLES:
        assert f"trg_{table_name}_immutable_insert_existing" in trigger_sql
        assert f"trg_{table_name}_append_only_update" in trigger_sql
        assert f"trg_{table_name}_append_only_delete" in trigger_sql
    immutable_columns = {
        "provider_team_aliases": (
            "alias_id",
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "internal_team_id",
        ),
        "provider_competition_mappings": (
            "mapping_id",
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "internal_competition_id",
        ),
        "canonical_match_identities": ("internal_match_id",),
    }
    for table_name, columns in immutable_columns.items():
        sql = trigger_sql[f"trg_{table_name}_immutable_insert_existing"]
        for column in columns:
            assert f"existing.{column} = new.{column}" in sql


def test_identity_lookup_keys_allow_multiple_canonical_targets() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO providers (provider_id, code, name, provider_kind) "
                "VALUES ('provider-identity', 'IDENTITY', 'Identity', 'TEST')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO teams (team_id, canonical_key, name, team_type) "
                "VALUES ('team-a', 'team-a', 'Team A', 'CLUB'), "
                "('team-b', 'team-b', 'Team B', 'CLUB')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO competitions "
                "(competition_id, canonical_key, name, country_code) "
                "VALUES ('competition-a', 'competition-a', 'Competition A', 'TST'), "
                "('competition-b', 'competition-b', 'Competition B', 'TST')"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_team_aliases (
                    alias_id, internal_team_id, provider_id, provider_team_id,
                    provider_team_name, language, team_type, available_at_utc
                ) VALUES
                    ('alias-a', 'team-a', 'provider-identity', 'provider-team',
                     'Provider Team', 'en', 'CLUB', '2026-09-02 10:00:00'),
                    ('alias-b', 'team-b', 'provider-identity', 'provider-team',
                     'Provider Team', 'en', 'CLUB', '2026-09-02 10:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_competition_mappings (
                    mapping_id, internal_competition_id, provider_id,
                    provider_competition_id, provider_competition_name,
                    language, season, competition_type, available_at_utc
                ) VALUES
                    ('mapping-a', 'competition-a', 'provider-identity',
                     'provider-competition', 'Provider Competition', 'en',
                     '2026', 'LEAGUE', '2026-09-02 10:00:00'),
                    ('mapping-b', 'competition-b', 'provider-identity',
                     'provider-competition', 'Provider Competition', 'en',
                     '2026', 'LEAGUE', '2026-09-02 10:00:00')
                """
            )
        )
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM provider_team_aliases")) == 2
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM provider_competition_mappings")
            )
            == 2
        )


def test_fixture_ingestion_schema_has_strict_lineage_constraints_and_indexes() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    inspector = inspect(engine)

    assert FIXTURE_INGESTION_TABLES <= set(inspector.get_table_names())
    parent_columns = {
        column["name"]: column
        for column in inspector.get_columns("fixture_ingestion_captures")
    }
    assert {
        "provider_id",
        "kickoff_from_utc",
        "kickoff_to_utc",
        "provider_competition_id",
        "provider_season_id",
        "season",
        "competition_type",
        "language",
        "team_type",
        "endpoint",
        "request_parameters_json",
        "requested_at_utc",
        "received_at_utc",
        "available_at_utc",
        "ingested_at_utc",
        "http_status",
        "provider_request_id",
        "duration_ms",
        "outcome",
        "failure_code",
        "raw_artifact_id",
        "raw_payload_sha256",
        "observation_count",
    } <= set(parent_columns)
    assert all(
        parent_columns[column]["nullable"] is False
        for column in (
            "provider_competition_id",
            "provider_season_id",
            "ingested_at_utc",
        )
    )
    parent_checks = {
        item["name"]: _normalize_sql(item["sqltext"])
        for item in inspector.get_check_constraints("fixture_ingestion_captures")
    }
    assert (
        "length(trim(provider_competition_id))>0"
        in parent_checks["ck_fixture_ingestion_scope"]
    )
    assert (
        "length(trim(provider_season_id))>0"
        in parent_checks["ck_fixture_ingestion_scope"]
    )
    assert (
        "received_at_utc<=ingested_at_utc"
        in parent_checks["ck_fixture_ingestion_request_timeline"]
    )
    assert {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
            item["options"].get("ondelete"),
        )
        for table_name in FIXTURE_INGESTION_TABLES
        for item in inspector.get_foreign_keys(table_name)
    } == {
        (("provider_id",), "providers", ("provider_id",), "RESTRICT"),
        (
            ("ingestion_id",),
            "fixture_ingestion_captures",
            ("ingestion_id",),
            "RESTRICT",
        ),
        (
            ("provider_mapping_id",),
            "provider_match_mappings",
            ("mapping_id",),
            "RESTRICT",
        ),
        (("internal_match_id",), "matches", ("internal_match_id",), "RESTRICT"),
    }
    assert {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("fixture_ingestion_captures")
    } == {("raw_artifact_id",)}
    assert {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("fixture_observations")
    } == {
        ("ingestion_id", "internal_match_id"),
        ("ingestion_id", "provider_mapping_id"),
    }
    assert {
        item["name"]: tuple(item["column_names"])
        for table_name in FIXTURE_INGESTION_TABLES
        for item in inspector.get_indexes(table_name)
    } == {
        "ix_fixture_ingestion_provider_available": (
            "provider_id",
            "available_at_utc",
        ),
        "ix_fixture_ingestion_scope_available": (
            "provider_id",
            "provider_competition_id",
            "provider_season_id",
            "available_at_utc",
        ),
        "ix_fixture_observation_match_available": (
            "internal_match_id",
            "available_at_utc",
        ),
        "ix_fixture_observation_mapping_available": (
            "provider_mapping_id",
            "available_at_utc",
        ),
    }
    for table_name, index_name in FIXTURE_IDENTITY_ORIGIN_INDEXES.items():
        columns = {
            column["name"]: column for column in inspector.get_columns(table_name)
        }
        assert columns["fixture_ingestion_id"]["nullable"] is True
        indexes = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_indexes(table_name)
        }
        assert indexes[index_name] == ("fixture_ingestion_id",)
    with engine.connect() as connection:
        trigger_sql = {
            name: sql.lower()
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    for table_name in FIXTURE_INGESTION_TABLES:
        assert f"trg_{table_name}_immutable_insert_existing" in trigger_sql
        assert f"trg_{table_name}_append_only_update" in trigger_sql
        assert f"trg_{table_name}_append_only_delete" in trigger_sql
    lineage_sql = trigger_sql["trg_fixture_observations_lineage_insert"]
    assert "mapping.provider_id = ingestion.provider_id" in lineage_sql
    assert "mapping.internal_match_id = new.internal_match_id" in lineage_sql
    for table_name in FIXTURE_IDENTITY_ORIGIN_INDEXES:
        origin_sql = trigger_sql[f"trg_{table_name}_fixture_ingestion_origin_insert"]
        assert "new.fixture_ingestion_id is not null" in origin_sql
        assert "fixture identity origin is inconsistent" in origin_sql


@pytest.mark.parametrize(
    "accept_engine",
    (configure_sqlite_engine, create_schema, create_session_factory),
)
def test_external_sqlite_engines_restore_pragmas_on_every_checkout(
    accept_engine,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    accept_engine(engine)

    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA recursive_triggers")) == 1
        connection.rollback()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        assert connection.scalar(text("PRAGMA foreign_keys")) == 0
        assert connection.scalar(text("PRAGMA recursive_triggers")) == 0

    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA recursive_triggers")) == 1

    engine.dispose()


@pytest.mark.parametrize(
    "operation",
    (
        lambda: create_database_engine(
            "postgresql+psycopg://user:secret@example.invalid/football"
        ),
        lambda: upgrade_database(
            "postgresql+psycopg://user:secret@example.invalid/football"
        ),
    ),
)
def test_database_entry_points_reject_non_sqlite_before_driver_loading(
    operation,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"Unsupported database backend 'postgresql'.*supports SQLite only",
    ) as error:
        operation()
    assert "secret" not in str(error.value)


def test_upgrade_database_creates_missing_nested_sqlite_parent_after_validation(
    tmp_path,
) -> None:
    database_path = tmp_path / "missing" / "nested" / "football.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    require_sqlite_database_url(database_url)
    assert not database_path.parent.exists()

    upgrade_database(database_url)

    assert database_path.is_file()
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            CURRENT_MIGRATION_HEAD
        )
    engine.dispose()


@pytest.mark.parametrize("operation", (create_schema, create_session_factory))
def test_external_entry_points_reject_non_sqlite_engine(operation) -> None:
    class _PostgresDialect:
        name = "postgresql"

    class _PostgresEngine:
        dialect = _PostgresDialect()

    with pytest.raises(ValueError, match="supports SQLite only"):
        operation(cast(Engine, _PostgresEngine()))


def test_direct_alembic_rejects_non_sqlite_before_driver_loading() -> None:
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://user:secret@example.invalid/football",
    )

    with pytest.raises(ValueError, match="supports SQLite only") as error:
        command.upgrade(config, "head")
    assert "secret" not in str(error.value)


def test_direct_alembic_offline_rejects_non_sqlite_without_credentials() -> None:
    config = Config("alembic.ini", output_buffer=StringIO())
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://user:secret@example.invalid/football",
    )

    with pytest.raises(ValueError, match="supports SQLite only") as error:
        command.upgrade(config, "head", sql=True)
    assert "secret" not in str(error.value)


def test_historical_migration_generates_offline_trigger_sql(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'offline-never-created.db').as_posix()}"
    upgrade_output = StringIO()
    upgrade_config = Config("alembic.ini", output_buffer=upgrade_output)
    upgrade_config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(
        upgrade_config,
        "c8b7e2a4f190:f3a1c6d8e204",
        sql=True,
    )

    upgrade_sql = upgrade_output.getvalue()
    assert "CREATE TRIGGER trg_ticket_settlements_correction_insert" in upgrade_sql
    assert "CREATE TRIGGER trg_backtest_slices_lineage_insert" in upgrade_sql
    assert "expected_match_ids" in upgrade_sql

    downgrade_output = StringIO()
    downgrade_config = Config("alembic.ini", output_buffer=downgrade_output)
    downgrade_config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(
        downgrade_config,
        "f3a1c6d8e204:c8b7e2a4f190",
        sql=True,
    )

    downgrade_sql = downgrade_output.getvalue()
    assert "DROP TRIGGER IF EXISTS trg_ticket_settlements_correction_insert" in (
        downgrade_sql
    )
    assert "DROP TRIGGER IF EXISTS trg_backtest_slices_lineage_insert" in downgrade_sql
    assert not (tmp_path / "offline-never-created.db").exists()


def test_runtime_schema_matches_alembic_head(tmp_path) -> None:
    runtime_url = f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    migration_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    runtime_engine = create_database_engine(runtime_url)
    create_schema(runtime_engine)
    upgrade_database(migration_url)
    migration_engine = create_database_engine(migration_url)

    assert _schema_signature(runtime_engine) == _schema_signature(migration_engine)
    assert _trigger_signature(runtime_engine) == _trigger_signature(migration_engine)

    runtime_engine.dispose()
    migration_engine.dispose()


def test_quant_model_migration_preserves_legacy_rows_and_empty_downgrade(
    tmp_path,
) -> None:
    database_path = tmp_path / "quant-model-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "a6c1f9e3b742")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        _insert_no_bet_risk_graph(connection, completed=True)
    engine.dispose()

    command.upgrade(config, QUANT_MODEL_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert QUANT_MODEL_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        context = connection.execute(
            text(
                "SELECT manual_quant_input_id, quant_model_evaluation_id "
                "FROM analysis_run_matches WHERE analysis_run_id = 'run-legacy'"
            )
        ).one()
        assert context == ("manual-legacy", None)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()

    command.downgrade(config, "a6c1f9e3b742")
    engine = create_database_engine(database_url)
    assert not QUANT_MODEL_TABLES & set(inspect(engine).get_table_names())
    assert "quant_model_evaluation_id" not in {
        column["name"] for column in inspect(engine).get_columns("analysis_run_matches")
    }
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT manual_quant_input_id FROM analysis_run_matches "
                    "WHERE analysis_run_id = 'run-legacy'"
                )
            )
            == "manual-legacy"
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_quant_model_migration_rejects_populated_downgrade(tmp_path) -> None:
    database_path = tmp_path / "populated-quant-model.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        _insert_no_bet_risk_graph(connection, completed=False)
        connection.execute(
            text(
                "UPDATE analysis_runs SET input_manifest_version = "
                "'MVP_INPUT_MANIFEST_V3' WHERE analysis_run_id = 'run-legacy'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO quant_model_states (
                    quant_model_state_id, analysis_run_id, model_name,
                    model_version, calibration_label, config_json, config_hash,
                    cutoff_at_utc, season_id, state_json, state_hash,
                    state_payload_hash, training_data_hash, training_fact_count,
                    generated_at_utc
                ) VALUES (
                    'state-populated', 'run-legacy', 'MODEL', '1',
                    'BASELINE_UNCALIBRATED', '{}', :config_hash,
                    '2026-08-31 03:00:00', '2026', :state_json, :state_hash,
                    :state_payload_hash, :training_data_hash, 0,
                    '2026-08-31 03:00:00'
                )
                """
            ),
            {
                "config_hash": "a" * 64,
                "state_hash": "b" * 64,
                "state_payload_hash": "c" * 64,
                "training_data_hash": "d" * 64,
                "state_json": (
                    '{"config_hash":"'
                    + "a" * 64
                    + '","state_hash":"'
                    + "b" * 64
                    + '","training_data_hash":"'
                    + "d" * 64
                    + '"}'
                ),
            },
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="while model lineage exists"):
        command.downgrade(config, "a6c1f9e3b742")

    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            QUANT_MODEL_MIGRATION_REVISION
        )
        assert (
            connection.scalar(
                text(
                    "SELECT state_hash FROM quant_model_states "
                    "WHERE quant_model_state_id = 'state-populated'"
                )
            )
            == "b" * 64
        )
    engine.dispose()


def test_backtest_v2_migration_upgrades_quant_head_and_empty_downgrade(
    tmp_path,
) -> None:
    database_path = tmp_path / "backtest-v2-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, QUANT_MODEL_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert not BACKTEST_V2_TABLES & set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, BACKTEST_V2_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert BACKTEST_V2_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        triggers = set(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'trg_backtest_v2_%'"
                )
            ).scalars()
        )
        assert {
            "trg_backtest_v2_runs_insert_running",
            "trg_backtest_v2_runs_completion",
            "trg_backtest_v2_slices_lineage_insert",
            "trg_backtest_v2_metrics_lineage_insert",
        } <= triggers
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()

    command.downgrade(config, QUANT_MODEL_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert not BACKTEST_V2_TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'trg_backtest_v2_%'"
                )
            )
            == 0
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_backtest_v2_migration_rejects_populated_downgrade(tmp_path) -> None:
    database_path = tmp_path / "populated-backtest-v2.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO backtest_v2_runs (
                    backtest_run_id, schema_version, backtest_version, data_mode,
                    date_from, date_to, strategy_version, strategy_config_json,
                    strategy_config_hash, code_revision, status, created_at_utc,
                    expected_slice_count, run_json, run_hash
                ) VALUES (
                    'backtest-v2-populated', 'BACKTEST_V2_RUN_RECORD_V1',
                    'BACKTEST_V2', 'LIVE_STRICT', '2026-08-31', '2026-08-31',
                    'strategy-v1', '{}', :strategy_config_hash, 'revision',
                    'RUNNING', '2026-08-31 03:00:00', 1, :run_json, :run_hash
                )
                """
            ),
            {
                "strategy_config_hash": "a" * 64,
                "run_hash": "b" * 64,
                "run_json": (
                    '{"archive_provenance":[],"backtest_run_id":'
                    '"backtest-v2-populated","backtest_version":"BACKTEST_V2",'
                    '"expected_slice_ids":["slice-1"],"status":"COMPLETED"}'
                ),
            },
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="while backtest lineage exists"):
        command.downgrade(config, QUANT_MODEL_MIGRATION_REVISION)

    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            BACKTEST_V2_MIGRATION_REVISION
        )
        assert (
            connection.scalar(
                text(
                    "SELECT run_hash FROM backtest_v2_runs "
                    "WHERE backtest_run_id = 'backtest-v2-populated'"
                )
            )
            == "b" * 64
        )
    engine.dispose()


def test_identity_migration_upgrades_f3_head_and_downgrades_to_f3(tmp_path) -> None:
    database_path = tmp_path / "identity-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "f3a1c6d8e204")
    engine = create_database_engine(database_url)
    assert not IDENTITY_TABLES & set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, IDENTITY_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert IDENTITY_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            IDENTITY_MIGRATION_REVISION
        )
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        for table_name in IDENTITY_TABLES:
            assert f"trg_{table_name}_immutable_insert_existing" in triggers
            assert f"trg_{table_name}_append_only_update" in triggers
            assert f"trg_{table_name}_append_only_delete" in triggers
    engine.dispose()

    command.downgrade(config, "f3a1c6d8e204")
    engine = create_database_engine(database_url)
    assert not IDENTITY_TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "f3a1c6d8e204"
        )
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert not {
            trigger
            for trigger in triggers
            if any(table_name in trigger for table_name in IDENTITY_TABLES)
        }
    engine.dispose()


def test_fixture_ingestion_migration_upgrades_identity_head_and_downgrades(
    tmp_path,
) -> None:
    database_path = tmp_path / "fixture-ingestion-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, IDENTITY_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert not FIXTURE_INGESTION_TABLES & set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    assert FIXTURE_INGESTION_TABLES <= set(inspect(engine).get_table_names())
    parent_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("fixture_ingestion_captures")
    }
    assert all(
        parent_columns[column]["nullable"] is False
        for column in (
            "provider_competition_id",
            "provider_season_id",
            "ingested_at_utc",
        )
    )
    assert all(
        "fixture_ingestion_id"
        in {column["name"] for column in inspect(engine).get_columns(table_name)}
        for table_name in FIXTURE_IDENTITY_ORIGIN_INDEXES
    )
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            CURRENT_MIGRATION_HEAD
        )
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_fixture_observations_lineage_insert" in triggers
        for table_name in FIXTURE_INGESTION_TABLES:
            assert f"trg_{table_name}_immutable_insert_existing" in triggers
            assert f"trg_{table_name}_append_only_update" in triggers
            assert f"trg_{table_name}_append_only_delete" in triggers
    engine.dispose()

    command.downgrade(config, IDENTITY_MIGRATION_REVISION)
    engine = create_database_engine(database_url)
    assert not FIXTURE_INGESTION_TABLES & set(inspect(engine).get_table_names())
    assert all(
        "fixture_ingestion_id"
        not in {column["name"] for column in inspect(engine).get_columns(table_name)}
        for table_name in FIXTURE_IDENTITY_ORIGIN_INDEXES
    )
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            IDENTITY_MIGRATION_REVISION
        )
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert not {
            trigger
            for trigger in triggers
            if "fixture_ingestion" in trigger or "fixture_observations" in trigger
        }
    engine.dispose()


def test_fixture_ingestion_migration_rejects_populated_downgrade(tmp_path) -> None:
    database_path = tmp_path / "populated-fixture-ingestion.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO providers (provider_id, code, name, provider_kind)
                VALUES ('fixture-provider', 'FIXTURE_PROVIDER',
                        'Fixture Provider', 'FIXTURE')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO fixture_ingestion_captures (
                    ingestion_id, provider_id, kickoff_from_utc,
                    kickoff_to_utc, provider_competition_id,
                    provider_season_id, season, competition_type, language,
                    team_type, endpoint, request_parameters_json,
                    requested_at_utc, received_at_utc, available_at_utc,
                    ingested_at_utc, http_status, provider_request_id,
                    duration_ms, outcome, failure_code, raw_artifact_id,
                    raw_payload_sha256, observation_count
                ) VALUES (
                    'populated-ingestion', 'fixture-provider',
                    '2026-09-03 00:00:00', '2026-09-03 23:59:59',
                    'league', 'season-id', '2026/27', 'LEAGUE', 'en',
                    'CLUB', 'https://provider.invalid/fixtures', '{}',
                    '2026-09-02 10:00:00', '2026-09-02 10:00:01',
                    '2026-09-02 10:00:01', '2026-09-02 10:00:02',
                    200, NULL, 1, 'SUCCESS', NULL,
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    0
                )
                """
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="while capture data exists"):
        command.downgrade(config, IDENTITY_MIGRATION_REVISION)

    engine = create_database_engine(database_url)
    assert FIXTURE_INGESTION_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "a6c1f9e3b742"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT raw_artifact_id FROM fixture_ingestion_captures "
                    "WHERE ingestion_id = 'populated-ingestion'"
                )
            )
            == "a" * 64
        )
    engine.dispose()


def test_completed_analysis_run_requires_a_validated_transition() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    now = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                AnalysisRunRecord(
                    analysis_run_id="run-immutable",
                    run_kind="TEST",
                    as_of_at_utc=now,
                    status="COMPLETED",
                    started_at_utc=now,
                    completed_at_utc=now,
                    pipeline_version="TEST",
                    code_revision="test",
                    config_json="{}",
                    config_hash="hash-config",
                    input_manifest_version="TEST_V1",
                    input_manifest_json="{}",
                    input_manifest_hash="hash-input",
                    replay_of_run_id=None,
                )
            )


def test_completion_rejects_cash_drift_after_child_insert() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    with engine.begin() as connection:
        _insert_no_bet_risk_graph(connection)
        connection.execute(
            text(
                """
                UPDATE portfolios
                SET budget_fen = 200, unused_budget_fen = 200
                WHERE portfolio_id = 'portfolio-legacy'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE portfolio_risk_reports
                SET budget_fen = 200, cash_fen = 200, cash_ratio = 1
                WHERE risk_report_id = 'risk-legacy'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE portfolio_stress_results
                SET ending_capital_fen = 200,
                    minimum_ending_capital_fen = 200,
                    maximum_ending_capital_fen = 200
                WHERE scenario_id = 'stress-legacy'
                """
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE analysis_runs
                    SET status = 'COMPLETED', completed_at_utc = started_at_utc
                    WHERE analysis_run_id = 'run-legacy'
                    """
                )
            )


def test_completion_rejects_recommended_stake_without_tickets() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    with engine.begin() as connection:
        _insert_no_bet_risk_graph(connection)
        connection.execute(
            text(
                """
                UPDATE portfolios
                SET budget_fen = 200,
                    total_stake_fen = 100,
                    status = 'RECOMMENDED',
                    no_bet_reason = NULL
                WHERE portfolio_id = 'portfolio-legacy'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE portfolio_risk_reports
                SET budget_fen = 200,
                    total_stake_fen = 100,
                    cash_ratio = 0.5,
                    total_stake_at_risk_fen = 100
                WHERE risk_report_id = 'risk-legacy'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE portfolio_stress_results
                SET profit_loss_fen = -100, capital_recovery_ratio = 0.5
                WHERE scenario_id = 'stress-legacy'
                """
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE analysis_runs
                    SET status = 'COMPLETED', completed_at_utc = started_at_utc
                    WHERE analysis_run_id = 'run-legacy'
                    """
                )
            )


def test_risk_report_rejects_cross_run_portfolio_lineage() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    now = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
    with sessions.begin() as session:
        for run_id in ("run-a", "run-b"):
            session.add(
                AnalysisRunRecord(
                    analysis_run_id=run_id,
                    run_kind="TEST",
                    as_of_at_utc=now,
                    status="RUNNING",
                    started_at_utc=now,
                    completed_at_utc=None,
                    pipeline_version="TEST",
                    code_revision="test",
                    config_json="{}",
                    config_hash="hash-config",
                    input_manifest_version="TEST_V1",
                    input_manifest_json="{}",
                    input_manifest_hash="hash-input",
                    replay_of_run_id=None,
                )
            )
        session.add(
            PortfolioRecord(
                portfolio_id="portfolio-a",
                analysis_run_id="run-a",
                budget_fen=100,
                total_stake_fen=0,
                unused_budget_fen=100,
                status="NO_BET",
                no_bet_reason="NO_BET_NO_VALUE",
                strategy_version="TEST",
                strategy_config_json="{}",
            )
        )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                PortfolioRiskReportRecord(
                    risk_report_id="cross-run-risk",
                    analysis_run_id="run-b",
                    portfolio_id="portfolio-a",
                    policy_version="TEST",
                    budget_fen=100,
                    total_stake_fen=0,
                    cash_fen=100,
                    cash_ratio=1,
                    expected_profit_fen=0,
                    total_stake_at_risk_fen=0,
                    max_single_ticket_exposure_fen=0,
                    max_match_exposure_fen=0,
                )
            )


def test_alembic_upgrades_empty_sqlite_database(tmp_path) -> None:
    database_path = tmp_path / "alembic-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "7a2c5e8f9b31")

    pre_hardening_engine = create_database_engine(database_url)
    pre_hardening_columns = {
        column["name"]: column
        for column in inspect(pre_hardening_engine).get_columns(
            "portfolio_stress_results"
        )
    }
    assert pre_hardening_columns["capital_recovery_ratio"]["type"].scale == 8
    pre_hardening_engine.dispose()

    command.upgrade(config, "head")

    engine = create_database_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert "alembic_version" in tables
    assert "analysis_runs" in tables
    assert "tickets" in tables
    assert "portfolio_risk_reports" in tables
    assert "analysis_packets" in tables
    assert "llm_review_artifacts" in tables
    assert "fusion_runs" in tables
    assert "fusion_run_results" in tables
    assert "portfolio_revisions" in tables
    stress_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("portfolio_stress_results")
    }
    assert stress_columns["capital_recovery_ratio"]["type"].scale == 12
    with engine.connect() as connection:
        triggers = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        ).scalars()
        assert "trg_analysis_runs_sealed_update" in set(triggers)
        assert "trg_market_probability_outcomes_sealed_update" in set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_portfolio_risk_reports_sealed_update" in set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_analysis_packets_append_only_update" in set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_fusion_runs_append_only_insert_existing" in set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_fusion_run_results_completed_parent_insert" in set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_portfolio_revisions_append_only_delete" in set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )


def test_post_review_migration_upgrades_0_2_head_and_downgrades(tmp_path) -> None:
    database_path = tmp_path / "post-review-upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "9d4e6f1a2c70")
    engine = create_database_engine(database_url)
    assert "fusion_runs" not in set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    assert {
        "fusion_runs",
        "fusion_run_results",
        "portfolio_revisions",
    } <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            CURRENT_MIGRATION_HEAD
        )
    engine.dispose()

    command.downgrade(config, "9d4e6f1a2c70")
    engine = create_database_engine(database_url)
    assert not {
        "fusion_runs",
        "fusion_run_results",
        "portfolio_revisions",
    } & set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "9d4e6f1a2c70"
        )
    engine.dispose()


def test_hardening_migration_rejects_existing_cross_run_risk_lineage(tmp_path) -> None:
    database_path = tmp_path / "cross-run-upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "7a2c5e8f9b31")
    engine = create_database_engine(database_url)
    now = "2026-08-31 03:00:00"
    with engine.begin() as connection:
        for run_id in ("run-a", "run-b"):
            connection.execute(
                text(
                    """
                    INSERT INTO analysis_runs (
                        analysis_run_id, run_kind, as_of_at_utc, status,
                        started_at_utc, completed_at_utc, pipeline_version,
                        code_revision, config_json, config_hash,
                        input_manifest_version, input_manifest_json,
                        input_manifest_hash, replay_of_run_id
                    ) VALUES (
                        :run_id, 'TEST', :now, 'RUNNING', :now, NULL,
                        'TEST', 'test', '{}', 'config-hash', 'TEST_V1',
                        '{}', 'manifest-hash', NULL
                    )
                    """
                ),
                {"run_id": run_id, "now": now},
            )
        connection.execute(
            text(
                """
                INSERT INTO portfolios (
                    portfolio_id, analysis_run_id, budget_fen, total_stake_fen,
                    unused_budget_fen, status, no_bet_reason,
                    strategy_version, strategy_config_json
                ) VALUES (
                    'portfolio-a', 'run-a', 100, 0, 100, 'NO_BET',
                    'NO_BET_NO_VALUE', 'TEST', '{}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO portfolio_risk_reports (
                    risk_report_id, analysis_run_id, portfolio_id, policy_version,
                    budget_fen, total_stake_fen, cash_fen, cash_ratio,
                    expected_profit_fen, total_stake_at_risk_fen,
                    max_single_ticket_exposure_fen, max_match_exposure_fen
                ) VALUES (
                    'cross-run-risk', 'run-b', 'portfolio-a', 'TEST',
                    100, 0, 100, 1, 0, 0, 0, 0
                )
                """
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="cross-run portfolio risk"):
        command.upgrade(config, "head")


def test_hardening_migration_rejects_invalid_completed_risk_graph(tmp_path) -> None:
    database_path = tmp_path / "invalid-completed-risk-upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "7a2c5e8f9b31")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        _insert_no_bet_risk_graph(
            connection,
            cash_amount_fen=99,
            completed=True,
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="completed portfolio risk graphs"):
        command.upgrade(config, "head")

    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "7a2c5e8f9b31"
        )
        assert (
            connection.scalar(
                text(
                    """
                SELECT status FROM analysis_runs
                WHERE analysis_run_id = 'run-legacy'
                """
                )
            )
            == "COMPLETED"
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE portfolio_cash_positions
                    SET amount_fen = 100
                    WHERE cash_position_id = 'cash-legacy'
                    """
                )
            )
    engine.dispose()


def test_hardening_migration_accepts_valid_completed_risk_graph(tmp_path) -> None:
    database_path = tmp_path / "valid-completed-risk-upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "7a2c5e8f9b31")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        _insert_no_bet_risk_graph(connection, completed=True)
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            CURRENT_MIGRATION_HEAD
        )
        assert (
            connection.scalar(
                text(
                    """
                SELECT status FROM analysis_runs
                WHERE analysis_run_id = 'run-legacy'
                """
                )
            )
            == "COMPLETED"
        )
    engine.dispose()


def _insert_no_bet_risk_graph(
    connection,
    *,
    cash_amount_fen: int = 100,
    completed: bool = False,
) -> None:
    now = "2026-08-31 03:00:00"
    connection.execute(
        text(
            """
            INSERT INTO competitions (competition_id, canonical_key, name, country_code)
            VALUES ('competition-legacy', 'competition-legacy', 'Legacy', 'TST')
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO teams (team_id, canonical_key, name, team_type)
            VALUES ('home-legacy', 'home-legacy', 'Home', 'CLUB'),
                   ('away-legacy', 'away-legacy', 'Away', 'CLUB')
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO providers (provider_id, code, name, provider_kind)
            VALUES ('provider-legacy', 'LEGACY', 'Legacy', 'TEST')
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO bookmakers (bookmaker_id, code, name)
            VALUES ('bookmaker-legacy', 'LEGACY', 'Legacy')
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO matches (
                internal_match_id, competition_id, home_team_id, away_team_id,
                kickoff_at_utc, status, available_at_utc, created_at_utc
            ) VALUES (
                'match-legacy', 'competition-legacy', 'home-legacy', 'away-legacy',
                :now, 'SCHEDULED', :now, :now
            )
            """
        ),
        {"now": now},
    )
    connection.execute(
        text(
            """
            INSERT INTO market_odds_snapshots (
                snapshot_id, internal_match_id, provider_id, bookmaker_id,
                market_key, market_type, handicap_value, captured_at_utc,
                available_at_utc, ingested_at_utc, source_snapshot_key, payload_hash
            ) VALUES (
                'odds-legacy', 'match-legacy', 'provider-legacy',
                'bookmaker-legacy', 'THREE_WAY', 'THREE_WAY', NULL,
                :now, :now, :now, 'odds-legacy', 'odds-hash'
            )
            """
        ),
        {"now": now},
    )
    connection.execute(
        text(
            """
            INSERT INTO sporttery_bonus_snapshots (
                snapshot_id, internal_match_id, provider_id, sporttery_match_no,
                market_key, market_type, handicap_value, sale_status,
                captured_at_utc, available_at_utc, ingested_at_utc,
                source_snapshot_key, payload_hash
            ) VALUES (
                'bonus-legacy', 'match-legacy', 'provider-legacy', '001',
                'THREE_WAY', 'THREE_WAY', NULL, 'OPEN',
                :now, :now, :now, 'bonus-legacy', 'bonus-hash'
            )
            """
        ),
        {"now": now},
    )
    connection.execute(
        text(
            """
            INSERT INTO manual_quant_inputs (
                input_id, internal_match_id, market_key, market_type,
                handicap_value, available_at_utc, payload_hash
            ) VALUES (
                'manual-legacy', 'match-legacy', 'THREE_WAY', 'THREE_WAY',
                NULL, :now, 'manual-hash'
            )
            """
        ),
        {"now": now},
    )
    connection.execute(
        text(
            """
            INSERT INTO analysis_runs (
                analysis_run_id, run_kind, as_of_at_utc, status,
                started_at_utc, completed_at_utc, pipeline_version,
                code_revision, config_json, config_hash,
                input_manifest_version, input_manifest_json,
                input_manifest_hash, replay_of_run_id
            ) VALUES (
                'run-legacy', 'TEST', :now, 'RUNNING', :now, NULL,
                'TEST', 'test', '{}', 'config-hash', 'TEST_V1',
                '{}', 'manifest-hash', NULL
            )
            """
        ),
        {"now": now},
    )
    connection.execute(
        text(
            """
            INSERT INTO analysis_run_matches (
                analysis_run_id, internal_match_id, market_odds_snapshot_id,
                sporttery_bonus_snapshot_id, manual_quant_input_id,
                context_json, context_hash
            ) VALUES (
                'run-legacy', 'match-legacy', 'odds-legacy', 'bonus-legacy',
                'manual-legacy', '{}', 'context-hash'
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO portfolios (
                portfolio_id, analysis_run_id, budget_fen, total_stake_fen,
                unused_budget_fen, status, no_bet_reason,
                strategy_version, strategy_config_json
            ) VALUES (
                'portfolio-legacy', 'run-legacy', 100, 0, 100, 'NO_BET',
                'NO_BET_NO_VALUE', 'TEST', '{}'
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO portfolio_cash_positions (
                cash_position_id, portfolio_id, amount_fen, expected_profit_fen
            ) VALUES ('cash-legacy', 'portfolio-legacy', :cash_amount_fen, 0)
            """
        ),
        {"cash_amount_fen": cash_amount_fen},
    )
    connection.execute(
        text(
            """
            INSERT INTO portfolio_risk_reports (
                risk_report_id, analysis_run_id, portfolio_id, policy_version,
                budget_fen, total_stake_fen, cash_fen, cash_ratio,
                expected_profit_fen, total_stake_at_risk_fen,
                max_single_ticket_exposure_fen, max_match_exposure_fen
            ) VALUES (
                'risk-legacy', 'run-legacy', 'portfolio-legacy', 'TEST',
                100, 0, 100, 1, 0, 0, 0, 0
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO portfolio_stress_results (
                scenario_id, risk_report_id, portfolio_id, scenario_key,
                policy_version, outcomes_json, is_complete,
                scenario_exposed_stake_fen, scenario_exposure_ratio,
                gross_payout_fen, ending_capital_fen, profit_loss_fen,
                capital_recovery_ratio, minimum_ending_capital_fen,
                maximum_ending_capital_fen
            ) VALUES (
                'stress-legacy', 'risk-legacy', 'portfolio-legacy',
                'CASH_BASELINE', 'TEST', '[]', 1, 0, 0,
                0, 100, 0, 1, 100, 100
            )
            """
        )
    )
    if completed:
        connection.execute(
            text(
                """
                UPDATE analysis_runs
                SET status = 'COMPLETED', completed_at_utc = started_at_utc
                WHERE analysis_run_id = 'run-legacy'
                """
            )
        )


def _schema_signature(engine: Engine) -> dict[str, object]:
    inspector = inspect(engine)
    tables = sorted(set(inspector.get_table_names()) - {"alembic_version"})
    return {
        table: {
            "columns": tuple(
                sorted(
                    (
                        column["name"],
                        str(column["type"]).upper(),
                        column["nullable"],
                        _normalize_sql(column.get("default")),
                        column.get("primary_key", 0),
                    )
                    for column in inspector.get_columns(table)
                )
            ),
            "primary_key": _constraint_signature(inspector.get_pk_constraint(table)),
            "foreign_keys": tuple(
                sorted(
                    (
                        item.get("name") or "",
                        tuple(item.get("constrained_columns") or ()),
                        item.get("referred_table") or "",
                        tuple(item.get("referred_columns") or ()),
                        tuple(
                            sorted(
                                (key, str(value))
                                for key, value in (item.get("options") or {}).items()
                            )
                        ),
                    )
                    for item in inspector.get_foreign_keys(table)
                )
            ),
            "unique_constraints": tuple(
                sorted(
                    _constraint_signature(item)
                    for item in inspector.get_unique_constraints(table)
                )
            ),
            "check_constraints": tuple(
                sorted(
                    (
                        item.get("name") or "",
                        _normalize_sql(item.get("sqltext")),
                    )
                    for item in inspector.get_check_constraints(table)
                )
            ),
            "indexes": tuple(
                sorted(
                    (
                        item.get("name") or "",
                        tuple(item.get("column_names") or ()),
                        bool(item.get("unique")),
                        tuple(
                            sorted(
                                (key, _normalize_sql(value))
                                for key, value in (
                                    item.get("dialect_options") or {}
                                ).items()
                            )
                        ),
                    )
                    for item in inspector.get_indexes(table)
                )
            ),
        }
        for table in tables
    }


def _trigger_signature(engine: Engine) -> tuple[tuple[str, str], ...]:
    with engine.connect() as connection:
        triggers = connection.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' ORDER BY name"
            )
        )
        return tuple(
            (
                name,
                _normalize_sql(sql).replace(
                    "create trigger if not exists",
                    "create trigger",
                    1,
                ),
            )
            for name, sql in triggers
        )


def _constraint_signature(item: dict[str, object]) -> tuple[object, ...]:
    return (
        item.get("name") or "",
        tuple(item.get("constrained_columns") or ()),
    )


def _normalize_sql(value: object) -> str:
    if value is None:
        return ""
    normalized = re.sub(r"\s+", " ", str(value).strip()).lower()
    return re.sub(r"\s*([(),=<>])\s*", r"\1", normalized)
