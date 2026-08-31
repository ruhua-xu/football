from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from football_system.infrastructure.database.models import AnalysisRunRecord
from football_system.infrastructure.database.session import (
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
        "tickets",
    } <= tables
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_completed_analysis_run_is_immutable() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    now = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
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

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            record = session.get(AnalysisRunRecord, "run-immutable")
            assert record is not None
            record.pipeline_version = "MUTATED"


def test_alembic_upgrades_empty_sqlite_database(tmp_path) -> None:
    database_path = tmp_path / "alembic-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_database_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert "alembic_version" in tables
    assert "analysis_runs" in tables
    assert "tickets" in tables
    assert "portfolio_risk_reports" in tables
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
