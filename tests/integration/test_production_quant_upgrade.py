"""SYNTHETIC_CONTRACT_TEST_ONLY: populated upgrade, not production approval.

All data is synthetic and confined to tmp_path; no local or live DB is opened.
"""

import hashlib
from collections import Counter
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, SAWarning

from football_system.application.review_bridge import (
    build_analysis_packet_v3,
    canonical_json,
)
from football_system.infrastructure.database.production_audit_schema import (
    PRODUCTION_AUDIT_TABLES,
    production_audit_trigger_sql_v1,
)
from football_system.infrastructure.database.production_inference_schema import (
    PRODUCTION_INFERENCE_TABLES,
    production_inference_trigger_sql_v1,
)
from football_system.infrastructure.database.production_quant_schema import (
    PRODUCTION_QUANT_TABLES,
    production_quant_trigger_sql_v1,
)
from football_system.infrastructure.database.quant_integrity_schema import (
    QUANT_INTEGRITY_TABLES,
    quant_integrity_trigger_sql_v1,
)
from football_system.infrastructure.database.review_repositories import (
    SqlAlchemyReviewArtifactRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.database.training_admission_schema import (
    TRAINING_ADMISSION_TABLES,
    training_admission_trigger_sql_v1,
)
from tests.integration.test_database_schema import _insert_no_bet_risk_graph


def test_production_quant_upgrade_preserves_populated_baseline(tmp_path) -> None:
    database_path = tmp_path / "synthetic-production-quant-upgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    new_tables = set().union(
        TRAINING_ADMISSION_TABLES,
        QUANT_INTEGRITY_TABLES,
        PRODUCTION_QUANT_TABLES,
        PRODUCTION_INFERENCE_TABLES,
        PRODUCTION_AUDIT_TABLES,
    )
    new_triggers = set().union(
        training_admission_trigger_sql_v1(),
        quant_integrity_trigger_sql_v1(),
        production_quant_trigger_sql_v1(),
        production_inference_trigger_sql_v1(),
        production_audit_trigger_sql_v1(),
    )
    assert not database_path.exists()
    command.upgrade(config, "6e4b1a9c2d73")
    engine = create_database_engine(database_url)
    try:
        baseline_tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert not baseline_tables & new_tables
        with engine.begin() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("6e4b1a9c2d73")
            # This SQL-only fixture creates legacy sources and a RUNNING risk graph.
            _insert_no_bet_risk_graph(connection)
            for statement in (
                """UPDATE analysis_runs
                   SET config_hash = :empty_hash, input_manifest_hash = :empty_hash
                   WHERE analysis_run_id = 'run-legacy'""",
                """UPDATE analysis_run_matches SET context_hash = :empty_hash
                   WHERE analysis_run_id = 'run-legacy'""",
                """INSERT INTO canonical_match_identities
                   (internal_match_id, season, competition_type, available_at_utc)
                   VALUES ('match-legacy', '2026', 'LEAGUE', :now)""",
                """INSERT INTO provider_team_aliases
                   (alias_id, internal_team_id, provider_id, provider_team_id,
                    provider_team_name, language, team_type, available_at_utc)
                   VALUES ('alias-home', 'home-legacy', 'provider-legacy', 'home',
                           'Home', 'en', 'CLUB', :now),
                          ('alias-away', 'away-legacy', 'provider-legacy', 'away',
                           'Away', 'en', 'CLUB', :now)""",
                """INSERT INTO provider_competition_mappings
                   (mapping_id, internal_competition_id, provider_id,
                    provider_competition_id, provider_competition_name,
                    language, season, competition_type, available_at_utc)
                   VALUES ('competition-mapping-legacy', 'competition-legacy',
                           'provider-legacy', 'league', 'Legacy', 'en', '2026',
                           'LEAGUE', :now)""",
                """INSERT INTO provider_match_mappings
                   (mapping_id, provider_id, external_namespace, external_match_id,
                    internal_match_id, resolution_method, confidence, available_at_utc)
                   VALUES ('mapping-legacy', 'provider-legacy', 'synthetic-upgrade',
                           'external-legacy', 'match-legacy', 'TEST_EXACT', 1, :now)""",
                """INSERT INTO match_results
                   (match_result_id, internal_match_id, provider_id,
                    provider_mapping_id, home_goals, away_goals, observed_at_utc,
                    available_at_utc, ingested_at_utc, source_result_key, payload_hash)
                   VALUES ('result-legacy', 'match-legacy', 'provider-legacy',
                           'mapping-legacy', 2, 1, '2026-08-31 05:00:00',
                           '2026-08-31 05:01:00', '2026-08-31 05:02:00',
                           'result-legacy', :result_hash)""",
                """INSERT INTO market_probabilities
                   (market_probability_id, analysis_run_id, internal_match_id,
                    market_key, market_type, devig_method, devig_version,
                    overround, generated_at_utc)
                   VALUES ('market-legacy', 'run-legacy', 'match-legacy',
                           'THREE_WAY', 'THREE_WAY', 'PROPORTIONAL', '1', 0, :now)""",
                """INSERT INTO market_probability_inputs
                   (market_probability_id, market_odds_snapshot_id)
                   VALUES ('market-legacy', 'odds-legacy')""",
                """INSERT INTO quant_predictions
                   (quant_prediction_id, analysis_run_id, internal_match_id,
                    market_key, market_type, manual_input_id, input_payload_hash,
                    method, method_version, entered_at_utc)
                   VALUES ('quant-legacy', 'run-legacy', 'match-legacy',
                           'THREE_WAY', 'THREE_WAY', 'manual-legacy', 'manual-hash',
                           'MANUAL', '1', :now)""",
                """INSERT INTO final_predictions
                   (final_prediction_id, analysis_run_id, internal_match_id,
                    market_key, market_type, market_probability_id,
                    quant_prediction_id, fusion_policy, fusion_version,
                    fusion_config_json, confidence, generated_at_utc)
                   VALUES ('final-legacy', 'run-legacy', 'match-legacy',
                           'THREE_WAY', 'THREE_WAY', 'market-legacy', 'quant-legacy',
                           'MARKET_QUANT_BLEND_V1', '1', '{}', 0.5, :now)""",
            ):
                connection.execute(
                    text(statement),
                    {
                        "now": "2026-08-31 03:00:00",
                        "empty_hash": hashlib.sha256(b"{}").hexdigest(),
                        "result_hash": hashlib.sha256(
                            b'{"away_goals":1,"home_goals":2}'
                        ).hexdigest(),
                    },
                )
            for table, key, value_column, parent_id, values in (
                ("market_odds_quotes", "snapshot_id", "odds", "odds-legacy", (2, 4, 4)),
                (
                    "sporttery_bonus_quotes",
                    "snapshot_id",
                    "fixed_bonus",
                    "bonus-legacy",
                    (1.8, 3.6, 3.6),
                ),
                (
                    "manual_quant_input_outcomes",
                    "input_id",
                    "probability",
                    "manual-legacy",
                    (0.5, 0.25, 0.25),
                ),
                (
                    "market_probability_outcomes",
                    "market_probability_id",
                    "probability",
                    "market-legacy",
                    (0.5, 0.25, 0.25),
                ),
                (
                    "quant_prediction_outcomes",
                    "quant_prediction_id",
                    "probability",
                    "quant-legacy",
                    (0.5, 0.25, 0.25),
                ),
                (
                    "final_prediction_outcomes",
                    "final_prediction_id",
                    "probability",
                    "final-legacy",
                    (0.5, 0.25, 0.25),
                ),
            ):
                connection.execute(
                    text(
                        f"INSERT INTO {table} ({key}, selection_key, {value_column}) "
                        "VALUES (:parent_id, :selection, :value)"
                    ),
                    [
                        {"parent_id": parent_id, "selection": selection, "value": value}
                        for selection, value in zip(
                            ("HOME_WIN", "DRAW", "AWAY_WIN"), values, strict=True
                        )
                    ],
                )
            connection.execute(
                text("""UPDATE analysis_runs
                     SET status = 'COMPLETED', completed_at_utc = started_at_utc
                     WHERE analysis_run_id = 'run-legacy'""")
            )

        # Only this legacy-only loader is used, not the public production-audit gate.
        # New tables are absent, so any accidental new-schema query fails here.
        with create_session_factory(engine)() as session:
            source = SqlAlchemyReviewArtifactRepository._load_packet_source_v3(
                session, "run-legacy"
            )
        packet = build_analysis_packet_v3(
            source, datetime(2026, 8, 31, 3, 1, tzinfo=timezone.utc)
        )
        assert packet.schema_version == "ANALYSIS_PACKET_V3"
        assert packet.matches[0].p_quant.source_kind == "MANUAL"
        assert not packet.quant_model_states
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO analysis_packets
                     (packet_id, parent_analysis_run_id, schema_version,
                      generated_at_utc, packet_json, packet_hash)
                     VALUES (:packet_id, 'run-legacy', :schema_version,
                             '2026-08-31 03:01:00', :packet_json, :packet_hash)"""),
                {
                    "packet_id": packet.packet_id,
                    "schema_version": packet.schema_version,
                    "packet_json": canonical_json(packet.model_dump(mode="json")),
                    "packet_hash": packet.packet_hash,
                },
            )
        with engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA recursive_triggers")) == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            before = _table_rows(connection, baseline_tables)
            old_triggers = dict(
                connection.execute(
                    text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
                )
                .tuples()
                .all()
            )
            assert not old_triggers.keys() & new_triggers
            assert (
                connection.scalar(
                    text(
                        "SELECT status FROM analysis_runs WHERE analysis_run_id = 'run-legacy'"
                    )
                )
                == "COMPLETED"
            )
        assert all(
            before[table][1]
            for table in (
                "providers",
                "competitions",
                "teams",
                "matches",
                "provider_team_aliases",
                "provider_competition_mappings",
                "canonical_match_identities",
                "provider_match_mappings",
                "match_results",
                "analysis_runs",
                "analysis_run_matches",
                "quant_predictions",
                "analysis_packets",
            )
        )
    finally:
        engine.dispose()

    command.upgrade(config, "c2ebf618d354")
    engine = create_database_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) == (
            baseline_tables | new_tables | {"alembic_version"}
        )
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("c2ebf618d354")
            assert _table_rows(connection, baseline_tables) == before
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA recursive_triggers")) == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            triggers = dict(
                connection.execute(
                    text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
                )
                .tuples()
                .all()
            )
            assert triggers.keys() == old_triggers.keys() | new_triggers
            assert {name: triggers[name] for name in old_triggers} == old_triggers
            # Legacy results/runs/packets must not acquire approval or audit sidecars.
            assert all(
                not rows for _, rows in _table_rows(connection, new_tables).values()
            )

        for table, (columns, rows) in before.items():
            if not rows:
                continue
            # No-op updates isolate immutable guards from FK/check failures.
            for statement in (
                f'UPDATE "{table}" SET "{columns[0]}" = "{columns[0]}"',
                f'DELETE FROM "{table}"',
            ):
                with pytest.raises(IntegrityError, match="append-only|immutable"):
                    with engine.begin() as connection:
                        connection.execute(text(statement))

        # Alembic cannot sort the known deferred-FK cycles for autogeneration.
        # Keep the explicit row/FK/trigger checks rather than relying on autogenerate.
        command.upgrade(config, "head")
        with pytest.warns(SAWarning, match="Cannot correctly sort tables;.*cycles"):
            command.check(config)
        with engine.connect() as connection:
            assert _table_rows(connection, baseline_tables) == before
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()


def _table_rows(connection, tables):
    snapshot = {}
    for table in sorted(tables):
        rows = connection.execute(text(f'SELECT * FROM "{table}"'))
        # Preserve every column and duplicate row, without relying on SELECT order.
        snapshot[table] = (tuple(rows.keys()), Counter(tuple(row) for row in rows))
    return snapshot
