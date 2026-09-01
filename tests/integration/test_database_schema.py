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
)


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
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA recursive_triggers")) == 1


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
            "f3a1c6d8e204"
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
            "f3a1c6d8e204"
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
