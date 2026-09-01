import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.application.ports.data_providers import MatchResultBatch
from football_system.config import AppSettings
from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
    match_result_payload_sha256,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestRun,
    BacktestRunStatus,
    BacktestSlice,
    BacktestStrategySnapshot,
)
from football_system.domain.common import stable_id
from football_system.domain.match import ProviderMatchMapping
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.settlement import (
    MatchResult,
    Settlement,
    SettlementScopeKind,
    SettlementStatus,
)
from football_system.domain.services.backtest_metrics import calculate_backtest_metrics
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
    _ticket_settlement_record,
    backtest_metric_snapshot_record,
    backtest_metrics_value,
    backtest_run_record,
    backtest_slice_record,
    historical_archive_manifest,
    portfolio_settlement_hash,
)
from football_system.infrastructure.database.models import (
    AnalysisRunRecord,
    BacktestRunRecord,
    BacktestSliceRecord,
    BetCandidateRecord,
    MatchRecord,
    MatchResultRecord,
    PortfolioRecord,
    PortfolioSettlementRecord,
    PortfolioSettlementTicketRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    TicketRecord,
    TicketSettlementMatchResultRecord,
    TicketSettlementRecord,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.providers.mock.dataset import MockDataset
from football_system.infrastructure.providers.mock.fixtures import MockFixtureProvider
from football_system.infrastructure.providers.mock.manual_quant import (
    MockManualQuantProvider,
)
from football_system.infrastructure.providers.mock.market_odds import (
    MockMarketOddsProvider,
)
from football_system.infrastructure.providers.mock.sporttery import (
    MockSportteryProvider,
)


NEW_TABLES = {
    "historical_archive_imports",
    "match_results",
    "ticket_settlements",
    "ticket_settlement_match_results",
    "portfolio_settlements",
    "portfolio_settlement_tickets",
    "backtest_runs",
    "backtest_slices",
    "backtest_metric_snapshots",
    "backtest_metric_settlements",
    "backtest_metric_ticket_settlements",
}
APPEND_ONLY_TABLES = tuple(sorted(NEW_TABLES))
EXECUTION_TIME = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
PROVIDER_CODE = "HISTORICAL_TEST"


def test_fresh_schema_installs_historical_tables_and_triggers() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)

    assert NEW_TABLES <= set(inspect(engine).get_table_names())
    assert {
        "backtest_version",
        "data_mode",
        "date_from",
        "date_to",
        "strategy_version",
        "strategy_config_json",
        "strategy_config_hash",
        "code_revision",
        "created_at_utc",
        "status",
    } <= {column["name"] for column in inspect(engine).get_columns("backtest_runs")}
    assert {
        "match_count",
        "settled_match_count",
        "settled_ticket_count",
        "unsettled_ticket_count",
        "coverage",
    } <= {column["name"] for column in inspect(engine).get_columns("backtest_slices")}
    match_result_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("match_results")
    }
    assert match_result_columns["provider_mapping_id"]["nullable"] is False
    assert {
        "uq_match_results_provider_match_root",
    } <= {item["name"] for item in inspect(engine).get_indexes("match_results")}
    assert {
        "uq_ticket_settlements_logical_root",
    } <= {item["name"] for item in inspect(engine).get_indexes("ticket_settlements")}
    assert {
        "uq_portfolio_settlements_logical_root",
    } <= {item["name"] for item in inspect(engine).get_indexes("portfolio_settlements")}
    with engine.connect() as connection:
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        for table_name in APPEND_ONLY_TABLES:
            assert f"trg_{table_name}_immutable_insert_existing" in triggers
            assert f"trg_{table_name}_append_only_update" in triggers
            assert f"trg_{table_name}_append_only_delete" in triggers
        assert "trg_match_results_supersession_insert" in triggers
        assert "trg_match_results_provider_mapping_insert" in triggers
        assert "trg_ticket_settlements_base_lineage_insert" in triggers
        assert "trg_backtest_metric_settlements_lineage_insert" in triggers
        assert "trg_backtest_metric_ticket_settlements_lineage_insert" in triggers


def test_bulk_archive_import_is_ordered_idempotent_and_atomic() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    repository = SqlAlchemyHistoricalRepository(create_session_factory(engine))
    first = _archive_manifest()
    second = first.model_copy(
        update={
            "archive_id": "historical-archive-2",
            "provider_code": "HISTORICAL_TEST_2",
            "dataset_kind": HistoricalArchiveDatasetKind.FIXTURES,
            "source_reference": "test://historical-fixtures",
            "payload_sha256": "b" * 64,
            "record_count": 3,
        }
    )
    imported_at = first.created_at_utc + timedelta(minutes=1)

    stored = repository.append_historical_archive_imports(
        (second, first),
        imported_at,
    )
    assert tuple(historical_archive_manifest(item) for item in stored) == (
        second,
        first,
    )
    replayed = repository.append_historical_archive_imports(
        (first, second),
        imported_at + timedelta(hours=1),
    )
    assert tuple(historical_archive_manifest(item) for item in replayed) == (
        first,
        second,
    )
    assert all(item.imported_at_utc == imported_at for item in replayed)

    with pytest.raises(ValueError, match="archive IDs must be unique"):
        repository.append_historical_archive_imports((first, first), imported_at)
    with pytest.raises(ValueError, match="provider/kind/checksum"):
        repository.append_historical_archive_imports(
            (
                first.model_copy(update={"archive_id": "identity-copy-1"}),
                first.model_copy(update={"archive_id": "identity-copy-2"}),
            ),
            imported_at,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.append_historical_archive_imports(
            (), imported_at.replace(tzinfo=None)
        )

    pending = second.model_copy(
        update={
            "archive_id": "historical-archive-pending",
            "provider_code": "HISTORICAL_TEST_PENDING",
            "payload_sha256": "c" * 64,
        }
    )
    conflict = first.model_copy(
        update={"source_description": "conflicting stored provenance"}
    )
    with pytest.raises(ValueError, match="conflicts with stored data"):
        repository.append_historical_archive_imports(
            (pending, conflict),
            imported_at,
        )
    assert repository.find_historical_archive_import(pending.archive_id) is None
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM historical_archive_imports"))
            == 2
        )


def test_archive_and_backtest_appends_are_safe_with_expiring_sessions() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    repository = SqlAlchemyHistoricalRepository(sessionmaker(bind=engine))
    manifest = _archive_manifest()
    imported_at = manifest.created_at_utc + timedelta(minutes=1)

    stored = repository.append_historical_archive_imports((manifest,), imported_at)

    assert stored[0].archive_id == manifest.archive_id
    assert stored[0].imported_at_utc == imported_at
    run = _backtest_run("expiring-session-backtest")
    assert repository.append_backtest_run(run) == run
    record = backtest_run_record(
        run.model_copy(update={"backtest_run_id": "expiring-session-record"})
    )
    stored_record = repository.append_backtest_run(record)
    assert stored_record.backtest_run_id == "expiring-session-record"
    assert len(stored_record.run_hash) == 64


def test_backtest_run_requires_exact_registered_archive_provenance() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    repository = SqlAlchemyHistoricalRepository(create_session_factory(engine))
    manifest = _archive_manifest()
    provenance = BacktestArchiveProvenance.from_manifest(manifest)
    run = _backtest_run(
        "archive-provenance-backtest",
        archive_provenance=(provenance,),
        expected_slice_ids=("archive-provenance-slice",),
    )

    with pytest.raises(ValueError, match="unregistered archive"):
        repository.append_backtest_run(run)
    repository.append_historical_archive_import(
        manifest,
        manifest.created_at_utc + timedelta(minutes=1),
    )
    assert repository.append_backtest_run(run) == run
    assert repository.find_backtest_run_value(run.backtest_run_id) == run

    conflicting = run.model_copy(
        update={
            "backtest_run_id": "archive-provenance-conflict",
            "archive_provenance": (
                provenance.model_copy(update={"payload_sha256": "b" * 64}),
            ),
        }
    )
    with pytest.raises(ValueError, match="conflicts with its import"):
        repository.append_backtest_run(conflicting)

    wrong_mode = run.model_copy(
        update={
            "backtest_run_id": "archive-provenance-wrong-mode",
            "data_mode": HistoricalDataMode.LIVE_STRICT,
        }
    )
    with pytest.raises(ValueError, match="conflicts with its import"):
        repository.append_backtest_run(wrong_mode)


def test_strict_backtest_slice_enforces_decision_result_and_analysis_lineage() -> None:
    engine, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    ordinary_matches = artifacts.matches[:3]
    outside_match_id = "match-outside-analysis"
    with sessions.begin() as session:
        source = session.get(MatchRecord, ordinary_matches[0].match_id)
        assert source is not None
        session.add(
            MatchRecord(
                internal_match_id=outside_match_id,
                competition_id=source.competition_id,
                home_team_id=source.home_team_id,
                away_team_id=source.away_team_id,
                kickoff_at_utc=source.kickoff_at_utc,
                status=source.status,
                available_at_utc=source.available_at_utc,
                created_at_utc=source.created_at_utc,
            )
        )
    match_ids = tuple(item.match_id for item in ordinary_matches) + (outside_match_id,)
    _add_historical_provider(sessions, match_ids)
    decision_match_ids = tuple(
        item["match_id"]
        for item in json.loads(artifacts.analysis_run.input_manifest_json)["matches"]
    )
    observed = max(item.kickoff_at_utc for item in artifacts.matches) + timedelta(
        hours=2
    )
    evaluation = observed + timedelta(minutes=3)
    visible_results = tuple(
        _match_result(
            match_id,
            observed,
            observed + timedelta(minutes=1),
            observed + timedelta(minutes=2),
        )
        for match_id in (*match_ids[:2], outside_match_id)
    )
    late_result = _match_result(
        match_ids[2],
        observed,
        evaluation + timedelta(minutes=1),
        evaluation + timedelta(minutes=2),
    )
    repository.append_match_results((*visible_results, late_result))

    run = _backtest_run(
        "strict-slice-backtest",
        expected_slice_ids=("strict-slice",),
    )
    repository.append_backtest_run(run)
    valid = BacktestSlice(
        slice_id="strict-slice",
        backtest_run_id=run.backtest_run_id,
        data_mode=run.data_mode,
        decision_as_of_at_utc=artifacts.analysis_run.as_of_at_utc,
        kickoff_from_utc=min(item.kickoff_at_utc for item in artifacts.matches),
        kickoff_to_utc=max(item.kickoff_at_utc for item in artifacts.matches),
        evaluation_as_of_at_utc=evaluation,
        analysis_run_id=artifacts.analysis_run.analysis_run_id,
        decision_input_manifest_hash=artifacts.analysis_run.input_manifest_hash,
        match_result_ids=tuple(item.match_result_id for item in visible_results[:2]),
        expected_match_ids=decision_match_ids,
        match_count=len(decision_match_ids),
        settled_match_count=2,
        settled_ticket_count=0,
        unsettled_ticket_count=0,
    )

    with pytest.raises(ValueError, match="decision manifest conflicts"):
        repository.append_backtest_slice(
            valid.model_copy(update={"decision_input_manifest_hash": "f" * 64})
        )
    with pytest.raises(ValueError, match="result IDs do not cover"):
        repository.append_backtest_slice(
            valid.model_copy(update={"match_result_ids": ()})
        )
    with pytest.raises(ValueError, match="crosses its evaluation cutoff"):
        repository.append_backtest_slice(
            valid.model_copy(
                update={
                    "match_result_ids": (late_result.match_result_id,),
                    "settled_match_count": 1,
                }
            )
        )
    with pytest.raises(ValueError, match="crosses its evaluation cutoff"):
        repository.append_backtest_slice(
            valid.model_copy(
                update={
                    "match_result_ids": (visible_results[-1].match_result_id,),
                    "settled_match_count": 1,
                }
            )
        )
    assert repository.append_backtest_slice(valid) == valid
    assert repository.find_backtest_slice_value(valid.slice_id) == valid


def test_repository_backtest_slice_rejects_stale_duplicate_and_scope_drift() -> None:
    slice_ids = (
        "repository-slice-sequence",
        "repository-slice-missing",
        "repository-slice-kickoff",
        "repository-slice-duplicate",
        "repository-slice-stale",
        "repository-slice-valid",
    )
    _, repository, artifacts, base, first_v1, first_v2 = _slice_lineage_graph(
        "run-repository-slice-hardening",
        "backtest-repository-slice-hardening",
        slice_ids,
    )

    with pytest.raises(ValueError, match="expected match sequence"):
        repository.append_backtest_slice(
            base.model_copy(
                update={
                    "slice_id": slice_ids[0],
                    "expected_match_ids": tuple(reversed(base.expected_match_ids)),
                }
            ),
            slice_no=1,
        )
    with pytest.raises(ValueError, match="marks an AnalysisRun match as missing"):
        repository.append_backtest_slice(
            base.model_copy(
                update={
                    "slice_id": slice_ids[1],
                    "missing_decision_match_ids": (base.expected_match_ids[0],),
                }
            ),
            slice_no=2,
        )
    with pytest.raises(ValueError, match="kickoff lineage"):
        repository.append_backtest_slice(
            base.model_copy(
                update={
                    "slice_id": slice_ids[2],
                    "kickoff_to_utc": min(
                        item.kickoff_at_utc for item in artifacts.matches
                    ),
                }
            ),
            slice_no=3,
        )
    with pytest.raises(ValueError, match="multiple MatchResult versions"):
        repository.append_backtest_slice(
            base.model_copy(
                update={
                    "slice_id": slice_ids[3],
                    "match_result_ids": (
                        first_v2.match_result_id,
                        first_v1.match_result_id,
                    ),
                }
            ),
            slice_no=4,
        )
    with pytest.raises(ValueError, match="latest visible result"):
        repository.append_backtest_slice(
            base.model_copy(
                update={
                    "slice_id": slice_ids[4],
                    "match_result_ids": (
                        first_v1.match_result_id,
                        base.match_result_ids[1],
                    ),
                }
            ),
            slice_no=5,
        )

    missing_id = "repository-expected-but-missing-match"
    valid = base.model_copy(
        update={
            "slice_id": slice_ids[5],
            "expected_match_ids": (
                base.expected_match_ids[0],
                missing_id,
                *base.expected_match_ids[1:],
            ),
            "missing_decision_match_ids": (missing_id,),
            "match_count": base.match_count + 1,
        }
    )
    assert repository.append_backtest_slice(valid, slice_no=6) == valid


def test_sql_backtest_slice_trigger_rejects_stale_duplicate_and_scope_drift() -> None:
    engine, repository, artifacts, base, first_v1, first_v2 = _slice_lineage_graph(
        "run-sql-slice-hardening",
        "backtest-sql-slice-hardening",
        (),
    )
    invalid_values = (
        base.model_copy(
            update={
                "slice_id": "sql-slice-sequence",
                "expected_match_ids": tuple(reversed(base.expected_match_ids)),
            }
        ),
        base.model_copy(
            update={
                "slice_id": "sql-slice-missing",
                "missing_decision_match_ids": (base.expected_match_ids[0],),
            }
        ),
        base.model_copy(
            update={
                "slice_id": "sql-slice-kickoff",
                "kickoff_to_utc": min(
                    item.kickoff_at_utc for item in artifacts.matches
                ),
            }
        ),
        base.model_copy(
            update={
                "slice_id": "sql-slice-duplicate",
                "match_result_ids": (
                    first_v2.match_result_id,
                    first_v1.match_result_id,
                ),
            }
        ),
        base.model_copy(
            update={
                "slice_id": "sql-slice-stale",
                "match_result_ids": (
                    first_v1.match_result_id,
                    base.match_result_ids[1],
                ),
            }
        ),
    )
    for slice_no, value in enumerate(invalid_values, start=1):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    BacktestSliceRecord.__table__.insert(),
                    _record_values(backtest_slice_record(value, slice_no=slice_no)),
                )

    missing_id = "sql-expected-but-missing-match"
    valid = base.model_copy(
        update={
            "slice_id": "sql-slice-valid",
            "expected_match_ids": (
                base.expected_match_ids[0],
                missing_id,
                *base.expected_match_ids[1:],
            ),
            "missing_decision_match_ids": (missing_id,),
            "match_count": base.match_count + 1,
        }
    )
    with engine.begin() as connection:
        connection.execute(
            BacktestSliceRecord.__table__.insert(),
            _record_values(backtest_slice_record(valid, slice_no=6)),
        )
    assert repository.find_backtest_slice_value(valid.slice_id) == valid


def test_match_result_batch_materializes_sources_and_rejects_mapping_conflicts() -> (
    None
):
    engine, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    match = artifacts.matches[0]
    provider_code = "BATCH_RESULT_TEST"
    mapping = ProviderMatchMapping(
        mapping_id="batch-result-mapping-1",
        provider_code=provider_code,
        external_namespace="batch-result-test",
        external_match_id="external-batch-result-1",
        internal_match_id=match.match_id,
        resolution_method="TEST_EXACT",
        confidence=1,
        available_at_utc=match.available_at_utc,
    )
    observed = match.kickoff_at_utc + timedelta(hours=2)
    result = MatchResult(
        match_result_id="batch-result-1",
        match_id=match.match_id,
        provider_code=provider_code,
        home_goals=1,
        away_goals=0,
        observed_at_utc=observed,
        available_at_utc=observed + timedelta(minutes=1),
        ingested_at_utc=observed + timedelta(minutes=2),
        source_result_key="batch-result-source-1",
        payload_hash=match_result_payload_sha256(1, 0),
    )
    batch = MatchResultBatch(
        as_of_at_utc=result.ingested_at_utc,
        results=(result,),
        mappings=(mapping,),
    )

    with pytest.raises(
        ValueError, match="MatchResult payload failed hash verification"
    ):
        repository.append_match_result_batch(
            batch.model_copy(
                update={
                    "results": (result.model_copy(update={"payload_hash": "f" * 64}),)
                }
            )
        )
    assert repository.append_match_result_batch(batch) == batch
    assert repository.append_match_result_batch(batch) == batch
    provider_id = stable_id("provider", provider_code)
    with sessions() as session:
        provider = session.get(ProviderRecord, provider_id)
        assert provider is not None
        assert provider.name == "Batch Result Test"
        assert provider.provider_kind == "FIXTURE"
        stored_result = session.get(MatchResultRecord, result.match_result_id)
        assert stored_result is not None
        assert stored_result.provider_mapping_id == mapping.mapping_id

    alternate_mapping = mapping.model_copy(
        update={
            "mapping_id": "batch-result-mapping-alternate",
            "external_match_id": "external-batch-result-alternate",
        }
    )
    with pytest.raises(ValueError, match="immutable MatchResult"):
        repository.append_match_result_batch(
            MatchResultBatch(
                as_of_at_utc=result.ingested_at_utc,
                results=(result,),
                mappings=(alternate_mapping,),
            )
        )

    with pytest.raises(ValueError, match="only one root"):
        repository.append_match_result_batch(
            MatchResultBatch(
                as_of_at_utc=result.ingested_at_utc,
                results=(
                    result.model_copy(
                        update={
                            "match_result_id": "batch-result-second-root",
                            "source_result_key": "batch-result-second-root-source",
                        }
                    ),
                ),
                mappings=(mapping,),
            )
        )

    future_match = artifacts.matches[1]
    future_mapping = mapping.model_copy(
        update={
            "mapping_id": "batch-result-future-mapping",
            "external_match_id": "external-batch-result-future",
            "internal_match_id": future_match.match_id,
            "available_at_utc": observed + timedelta(minutes=10),
        }
    )
    future_result = result.model_copy(
        update={
            "match_result_id": "batch-result-before-mapping",
            "match_id": future_match.match_id,
            "source_result_key": "batch-result-before-mapping-source",
        }
    )
    with pytest.raises(ValueError, match="cannot precede its provider mapping"):
        repository.append_match_result_batch(
            MatchResultBatch(
                as_of_at_utc=future_mapping.available_at_utc,
                results=(future_result,),
                mappings=(future_mapping,),
            )
        )

    pending_mapping = mapping.model_copy(
        update={
            "mapping_id": "batch-result-mapping-pending",
            "external_match_id": "external-batch-result-pending",
        }
    )
    conflicting_mapping = mapping.model_copy(
        update={"external_match_id": "conflicting-external-id"}
    )
    conflict_batch = MatchResultBatch(
        as_of_at_utc=result.ingested_at_utc,
        results=(),
        mappings=(pending_mapping, conflicting_mapping),
    )
    with pytest.raises(ValueError, match="provider match mapping"):
        repository.append_match_result_batch(conflict_batch)
    with sessions() as session:
        assert (
            session.get(
                ProviderMatchMappingRecord,
                pending_mapping.mapping_id,
            )
            is None
        )

    external_identity_collision = mapping.model_copy(
        update={"mapping_id": "batch-result-mapping-collision"}
    )
    with pytest.raises(ValueError, match="provider match mapping"):
        repository.append_match_result_batch(
            MatchResultBatch(
                as_of_at_utc=result.ingested_at_utc,
                results=(),
                mappings=(external_identity_collision,),
            )
        )

    broken_code = "BROKEN_RESULT_PROVIDER"
    with sessions.begin() as session:
        session.add(
            ProviderRecord(
                provider_id=stable_id("provider", broken_code),
                code=broken_code,
                name="Wrong Name",
                provider_kind="MATCH_RESULTS",
            )
        )
    broken_mapping = mapping.model_copy(
        update={
            "mapping_id": "broken-provider-mapping",
            "provider_code": broken_code,
            "external_namespace": "broken-provider",
            "external_match_id": "broken-provider-external",
        }
    )
    with pytest.raises(ValueError, match="provider identity conflicts"):
        repository.append_match_result_batch(
            MatchResultBatch(
                as_of_at_utc=result.ingested_at_utc,
                results=(),
                mappings=(broken_mapping,),
            )
        )
    with sessions() as session:
        assert (
            session.get(
                ProviderMatchMappingRecord,
                broken_mapping.mapping_id,
            )
            is None
        )


def test_match_result_direct_append_and_read_verify_score_payload() -> None:
    engine, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    match = artifacts.matches[0]
    _add_historical_provider(sessions, (match.match_id,))
    observed = match.kickoff_at_utc + timedelta(hours=2)
    result = _match_result(
        match.match_id,
        observed,
        observed + timedelta(minutes=1),
        observed + timedelta(minutes=2),
    )

    with pytest.raises(
        ValueError, match="MatchResult payload failed hash verification"
    ):
        repository.append_match_result(
            result.model_copy(update={"payload_hash": "f" * 64})
        )
    assert repository.append_match_result(result) == result
    assert repository.find_match_result(result.match_result_id) == result

    duplicate_source = result.model_copy(
        update={
            "match_result_id": "result-duplicate-source",
            "supersedes_match_result_id": result.match_result_id,
            "available_at_utc": result.available_at_utc + timedelta(minutes=1),
            "ingested_at_utc": result.ingested_at_utc + timedelta(minutes=1),
        }
    )
    with pytest.raises(ValueError, match="immutable MatchResult"):
        repository.append_match_result(duplicate_source)

    _drop_trigger(engine, "trg_match_results_append_only_update")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE match_results SET home_goals = 2 "
                "WHERE match_result_id = :match_result_id"
            ),
            {"match_result_id": result.match_result_id},
        )
    with pytest.raises(
        ValueError, match="MatchResult payload failed hash verification"
    ):
        repository.find_match_result(result.match_result_id)


def test_match_result_mapping_trigger_rejects_future_mapping_and_latest_hides_bypass() -> (
    None
):
    engine, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    provider_code = "FUTURE_MAPPING_RESULTS"
    target_match, other_match = artifacts.matches[:2]
    observed = target_match.kickoff_at_utc + timedelta(hours=2)
    mapping_available = observed + timedelta(minutes=10)
    target_mapping = ProviderMatchMapping(
        mapping_id="future-result-target-mapping",
        provider_code=provider_code,
        external_namespace="future-result-test",
        external_match_id="future-result-target",
        internal_match_id=target_match.match_id,
        resolution_method="TEST_EXACT",
        confidence=1,
        available_at_utc=mapping_available,
    )
    other_mapping = target_mapping.model_copy(
        update={
            "mapping_id": "future-result-other-mapping",
            "external_match_id": "future-result-other",
            "internal_match_id": other_match.match_id,
        }
    )
    repository.append_match_result_batch(
        MatchResultBatch(
            as_of_at_utc=mapping_available,
            results=(),
            mappings=(target_mapping, other_mapping),
        )
    )
    provider_id = stable_id("provider", provider_code)
    values = {
        "match_result_id": "result-before-visible-mapping",
        "internal_match_id": target_match.match_id,
        "provider_id": provider_id,
        "provider_mapping_id": target_mapping.mapping_id,
        "home_goals": 1,
        "away_goals": 0,
        "observed_at_utc": observed,
        "available_at_utc": observed + timedelta(minutes=1),
        "ingested_at_utc": observed + timedelta(minutes=2),
        "source_result_key": "result-before-visible-mapping-source",
        "payload_hash": match_result_payload_sha256(1, 0),
        "supersedes_match_result_id": None,
    }
    statement = _insert_match_result_statement()

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                statement,
                {
                    **values,
                    "match_result_id": "result-wrong-exact-mapping",
                    "provider_mapping_id": other_mapping.mapping_id,
                    "available_at_utc": mapping_available,
                    "ingested_at_utc": mapping_available,
                    "source_result_key": "result-wrong-exact-mapping-source",
                },
            )

    _drop_trigger(engine, "trg_match_results_provider_mapping_insert")
    with engine.begin() as connection:
        connection.execute(statement, values)
    assert (
        repository.latest_match_results(
            (target_match.match_id,),
            values["ingested_at_utc"],
            provider_code,
        )
        == ()
    )
    with pytest.raises(ValueError, match="provider mapping is invalid"):
        repository.find_match_result(values["match_result_id"])


def test_load_base_portfolio_round_trips_recommended_and_no_bet_graphs() -> None:
    _, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    expected = artifacts.portfolios[0]

    loaded = repository.load_base_portfolio(expected.portfolio_id)

    assert loaded == expected
    assert tuple(ticket.ticket_no for ticket in loaded.tickets) == tuple(
        sorted(ticket.ticket_no for ticket in expected.tickets)
    )
    with pytest.raises(KeyError, match="unknown base portfolio"):
        repository.load_base_portfolio("unknown-portfolio")

    _, no_bet_sessions, no_bet_artifacts = _completed_analysis(
        min_selection_ev=Decimal("10"),
        analysis_run_id="run-historical-persistence-no-bet",
    )
    no_bet_repository = SqlAlchemyHistoricalRepository(no_bet_sessions)
    expected_no_bet = no_bet_artifacts.portfolios[0]

    assert expected_no_bet.tickets == ()
    assert (
        no_bet_repository.load_base_portfolio(expected_no_bet.portfolio_id)
        == expected_no_bet
    )


def test_load_base_portfolio_rejects_tampered_frozen_rows() -> None:
    engine, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    portfolio = artifacts.portfolios[0]
    ticket = portfolio.tickets[0]
    candidate = ticket.candidate.legs[0]

    with sessions() as session:
        portfolio_record = session.get(PortfolioRecord, portfolio.portfolio_id)
        ticket_record = session.get(TicketRecord, ticket.ticket_id)
        candidate_record = session.get(BetCandidateRecord, candidate.candidate_id)
        run_record = session.get(
            AnalysisRunRecord,
            artifacts.analysis_run.analysis_run_id,
        )
        assert portfolio_record is not None
        assert ticket_record is not None
        assert candidate_record is not None
        assert run_record is not None
        strategy_config_json = portfolio_record.strategy_config_json
        expected_roi = ticket_record.expected_roi
        candidate_ev = candidate_record.ev
        config_hash = run_record.config_hash

    _drop_trigger(engine, "trg_portfolios_sealed_update")
    with sessions.begin() as session:
        record = session.get(PortfolioRecord, portfolio.portfolio_id)
        assert record is not None
        record.strategy_config_json = json.dumps(
            portfolio.constraints.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
    with pytest.raises(ValueError, match="not canonical"):
        repository.load_base_portfolio(portfolio.portfolio_id)
    with sessions.begin() as session:
        record = session.get(PortfolioRecord, portfolio.portfolio_id)
        assert record is not None
        record.strategy_config_json = strategy_config_json
    assert repository.load_base_portfolio(portfolio.portfolio_id) == portfolio

    _drop_trigger(engine, "trg_tickets_sealed_update")
    with sessions.begin() as session:
        record = session.get(TicketRecord, ticket.ticket_id)
        assert record is not None
        record.expected_roi += Decimal(1)
    with pytest.raises(ValueError, match="Ticket redundant fields"):
        repository.load_base_portfolio(portfolio.portfolio_id)
    with sessions.begin() as session:
        record = session.get(TicketRecord, ticket.ticket_id)
        assert record is not None
        record.expected_roi = expected_roi
    assert repository.load_base_portfolio(portfolio.portfolio_id) == portfolio

    _drop_trigger(engine, "trg_bet_candidates_sealed_update")
    with sessions.begin() as session:
        record = session.get(BetCandidateRecord, candidate.candidate_id)
        assert record is not None
        record.ev += Decimal(1)
    with pytest.raises(ValueError, match="BetCandidate derived fields"):
        repository.load_base_portfolio(portfolio.portfolio_id)
    with sessions.begin() as session:
        record = session.get(BetCandidateRecord, candidate.candidate_id)
        assert record is not None
        record.ev = candidate_ev
    assert repository.load_base_portfolio(portfolio.portfolio_id) == portfolio

    _drop_trigger(engine, "trg_analysis_runs_sealed_update")
    with sessions.begin() as session:
        record = session.get(
            AnalysisRunRecord,
            artifacts.analysis_run.analysis_run_id,
        )
        assert record is not None
        record.config_hash = "f" * 64
    with pytest.raises(ValueError, match="hash verification"):
        repository.load_base_portfolio(portfolio.portfolio_id)
    with sessions.begin() as session:
        record = session.get(
            AnalysisRunRecord,
            artifacts.analysis_run.analysis_run_id,
        )
        assert record is not None
        record.config_hash = config_hash
    assert repository.load_base_portfolio(portfolio.portfolio_id) == portfolio


def test_historical_repository_round_trip_and_database_guards() -> None:
    engine, sessions, artifacts = _completed_analysis()
    repository = SqlAlchemyHistoricalRepository(sessions)
    archive_manifest = _archive_manifest()
    imported_at = archive_manifest.created_at_utc + timedelta(minutes=1)
    archive_record = repository.append_historical_archive_import(
        archive_manifest,
        imported_at,
    )
    assert historical_archive_manifest(archive_record) == archive_manifest
    assert archive_record.imported_at_utc == imported_at
    assert (
        repository.append_archive_import(
            archive_manifest,
            imported_at + timedelta(minutes=1),
        ).imported_at_utc
        == imported_at
    )
    assert (
        repository.find_historical_archive_manifest(archive_manifest.archive_id)
        == archive_manifest
    )
    assert repository.historical_archive_manifests(
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH.value
    ) == (archive_manifest,)
    repository.verify_archive_import(archive_manifest.archive_id)
    with pytest.raises(ValueError, match="conflicts with stored data"):
        repository.append_archive_import(
            archive_manifest.model_copy(
                update={"source_description": "conflicting provenance"}
            ),
            imported_at,
        )
    with pytest.raises(ValueError, match="conflicts with stored data"):
        repository.append_archive_import(
            archive_manifest.model_copy(update={"archive_id": "archive-conflict"}),
            imported_at,
        )
    portfolio = artifacts.portfolios[0]
    match_ids = tuple(
        sorted(
            {
                leg.match_id
                for ticket in portfolio.tickets
                for leg in ticket.candidate.legs
            }
        )
    )
    _add_historical_provider(sessions, match_ids)
    observed = max(
        match.kickoff_at_utc
        for match in artifacts.matches
        if match.match_id in match_ids
    ) + timedelta(hours=2)
    available = observed + timedelta(minutes=1)
    ingested = available + timedelta(minutes=1)

    results = tuple(
        _match_result(match_id, observed, available, ingested) for match_id in match_ids
    )
    assert repository.append_match_results(results) == results
    assert (
        repository.latest_match_results(
            match_ids,
            ingested,
            PROVIDER_CODE,
        )
        == results
    )

    corrected = _match_result(
        match_ids[0],
        observed,
        available + timedelta(minutes=2),
        ingested + timedelta(minutes=2),
        version=2,
        supersedes_match_result_id=f"result-{match_ids[0]}-v1",
    )
    assert repository.append_match_result(corrected) == corrected
    before_correction = repository.latest_match_results(
        match_ids,
        ingested,
        PROVIDER_CODE,
    )
    after_correction = repository.latest_match_results(
        match_ids,
        corrected.ingested_at_utc,
        PROVIDER_CODE,
    )
    assert before_correction[0].match_result_id.endswith("-v1")
    assert after_correction[0] == corrected

    with pytest.raises(ValueError, match="same match and provider"):
        repository.append_match_result(
            _match_result(
                match_ids[1],
                observed,
                available + timedelta(minutes=3),
                ingested + timedelta(minutes=3),
                version=3,
                supersedes_match_result_id=f"result-{match_ids[0]}-v1",
            )
        )
    _assert_match_result_checks(engine, match_ids[0], observed, available, ingested)

    latest_results = {
        result.match_id: result
        for result in repository.latest_match_results(
            match_ids,
            corrected.ingested_at_utc,
            PROVIDER_CODE,
        )
    }
    settled_at = corrected.ingested_at_utc + timedelta(minutes=1)
    settlements = tuple(
        _settlement(
            artifacts.analysis_run.analysis_run_id,
            portfolio,
            ticket,
            latest_results,
            settled_at,
        )
        for ticket in portfolio.tickets
    )
    false_loss = settlements[0].model_copy(
        update={
            "settlement_id": "false-loss-settlement",
            "status": SettlementStatus.LOST,
            "gross_payout_fen": 0,
            "profit_loss_fen": -settlements[0].stake_fen,
        }
    )
    with pytest.raises(ValueError, match="outcome contradicts its frozen legs"):
        repository.append_ticket_settlement(false_loss)
    for settlement in settlements:
        assert repository.append_ticket_settlement(settlement) == settlement
        assert repository.find_ticket_settlement(settlement.settlement_id) == settlement
    with pytest.raises(ValueError, match="only one root"):
        repository.append_ticket_settlement(
            settlements[0].model_copy(update={"settlement_id": "second-ticket-root"})
        )
    assert repository.latest_ticket_settlements(
        artifacts.analysis_run.analysis_run_id,
        settled_at,
    ) == tuple(sorted(settlements, key=lambda item: item.ticket_id))

    portfolio_record = PortfolioSettlementRecord(
        portfolio_settlement_id="portfolio-settlement-v1",
        settlement_kind="BACKTEST",
        scope_kind="ANALYSIS_RUN",
        parent_analysis_run_id=artifacts.analysis_run.analysis_run_id,
        decision_scope_id=artifacts.analysis_run.analysis_run_id,
        portfolio_revision_id=None,
        portfolio_id=portfolio.portfolio_id,
        base_portfolio_id=portfolio.portfolio_id,
        budget_fen=portfolio.budget_fen,
        total_stake_fen=portfolio.total_stake_fen,
        cash_fen=portfolio.unused_budget_fen,
        gross_payout_fen=sum(item.gross_payout_fen for item in settlements),
        profit_loss_fen=sum(item.profit_loss_fen for item in settlements),
        ticket_count=len(settlements),
        settlement_policy_version=settlements[0].settlement_policy_version,
        settled_at_utc=settled_at,
        settlement_hash="0" * 64,
        supersedes_portfolio_settlement_id=None,
    )
    settlement_ids = tuple(item.settlement_id for item in settlements)
    portfolio_record.settlement_hash = portfolio_settlement_hash(
        portfolio_record,
        settlement_ids,
    )
    repository.append_portfolio_settlement(portfolio_record, settlement_ids)
    stored_portfolio = repository.load_portfolio_settlement(
        portfolio_record.portfolio_settlement_id
    )
    assert stored_portfolio.ticket_settlement_ids == settlement_ids
    assert stored_portfolio.ending_capital_fen == (
        portfolio_record.cash_fen + portfolio_record.gross_payout_fen
    )
    assert repository.append_portfolio_settlement(stored_portfolio) == stored_portfolio
    with pytest.raises(ValueError, match="only one root"):
        repository.append_portfolio_settlement(
            stored_portfolio.model_copy(
                update={"portfolio_settlement_id": "second-portfolio-root"}
            )
        )
    assert (
        repository.latest_portfolio_settlements(
            artifacts.analysis_run.analysis_run_id,
            settled_at,
        )[0].portfolio_settlement_id
        == portfolio_record.portfolio_settlement_id
    )

    result_correction = _match_result(
        match_ids[0],
        observed,
        corrected.available_at_utc + timedelta(minutes=2),
        corrected.ingested_at_utc + timedelta(minutes=2),
        version=3,
        supersedes_match_result_id=corrected.match_result_id,
    ).model_copy(
        update={
            "home_goals": 0,
            "away_goals": 1,
            "payload_hash": match_result_payload_sha256(0, 1),
        }
    )
    repository.append_match_result(result_correction)
    correction_time = result_correction.ingested_at_utc + timedelta(minutes=1)
    target_ticket = next(
        ticket
        for ticket in portfolio.tickets
        if match_ids[0] in {leg.match_id for leg in ticket.candidate.legs}
    )
    previous_settlement = next(
        item for item in settlements if item.ticket_id == target_ticket.ticket_id
    )
    corrected_results = {
        result.match_id: result
        for result in repository.latest_match_results(
            match_ids,
            correction_time,
            PROVIDER_CODE,
        )
    }
    settlement_correction = _settlement(
        artifacts.analysis_run.analysis_run_id,
        portfolio,
        target_ticket,
        corrected_results,
        correction_time,
    ).model_copy(
        update={
            "settlement_id": f"{previous_settlement.settlement_id}-correction",
            "supersedes_settlement_id": previous_settlement.settlement_id,
        }
    )
    assert settlement_correction.status is SettlementStatus.LOST
    false_win = settlement_correction.model_copy(
        update={
            "settlement_id": "false-win-settlement",
            "status": SettlementStatus.WON,
            "gross_payout_fen": target_ticket.potential_gross_payout_fen,
            "profit_loss_fen": (
                target_ticket.potential_gross_payout_fen - target_ticket.stake_fen
            ),
        }
    )
    with pytest.raises(ValueError, match="outcome contradicts its frozen legs"):
        repository.append_ticket_settlement(false_win)
    repository.append_ticket_settlement(settlement_correction)
    assert repository.latest_ticket_settlements(
        artifacts.analysis_run.analysis_run_id,
        settled_at,
        (target_ticket.ticket_id,),
    ) == (previous_settlement,)
    assert repository.latest_ticket_settlements(
        artifacts.analysis_run.analysis_run_id,
        correction_time,
        (target_ticket.ticket_id,),
    ) == (settlement_correction,)
    other_settlement = next(
        item for item in settlements if item.ticket_id != target_ticket.ticket_id
    )
    with pytest.raises(ValueError, match="same scope and ticket"):
        repository.append_ticket_settlement(
            settlement_correction.model_copy(
                update={
                    "settlement_id": "cross-ticket-correction",
                    "supersedes_settlement_id": other_settlement.settlement_id,
                }
            )
        )

    processing_started = settled_at + timedelta(days=1)
    processing_completed = processing_started + timedelta(minutes=2)
    processing_created = processing_completed + timedelta(seconds=1)
    backtest = BacktestRun(
        backtest_run_id="backtest-run-1",
        backtest_version="HISTORICAL_BACKTEST_V1",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        date_from=artifacts.analysis_run.as_of_at_utc.date(),
        date_to=settled_at.date(),
        strategy_snapshot=BacktestStrategySnapshot.from_config(
            "QUANT_ONLY_V1",
            {"mode": "strict"},
        ),
        code_revision="integration-test-revision",
        created_at_utc=processing_created,
        status=BacktestRunStatus.COMPLETED,
    )
    assert backtest_run_record(backtest).data_mode == "SOURCE_TIME_RESEARCH"
    assert repository.append_backtest_run(backtest) == backtest
    run_record = repository.find_backtest_run(backtest.backtest_run_id)
    assert run_record is not None
    assert run_record.data_mode == "SOURCE_TIME_RESEARCH"
    assert run_record.backtest_mode == "STRICT_POINT_IN_TIME"
    assert run_record.strategy_config_hash == backtest.strategy_config_hash
    live_backtest = backtest.model_copy(
        update={
            "backtest_run_id": "backtest-run-live",
            "data_mode": HistoricalDataMode.LIVE_STRICT,
            "created_at_utc": processing_created + timedelta(minutes=1),
        }
    )
    assert repository.append_backtest_run(live_backtest) == live_backtest
    replay = backtest.model_copy(
        update={
            "backtest_run_id": "backtest-run-replay",
            "created_at_utc": processing_created + timedelta(minutes=2),
        }
    )
    assert (
        repository.append_backtest_run(
            replay,
            replay_of_backtest_run_id=backtest.backtest_run_id,
        )
        == replay
    )
    with pytest.raises(ValueError, match="replay lineage"):
        repository.append_backtest_run(
            replay.model_copy(
                update={
                    "backtest_run_id": "backtest-run-invalid-replay",
                    "code_revision": "different-code-revision",
                    "created_at_utc": processing_created + timedelta(minutes=3),
                }
            ),
            replay_of_backtest_run_id=backtest.backtest_run_id,
        )

    analysis_match_ids = tuple(
        item["match_id"]
        for item in json.loads(artifacts.analysis_run.input_manifest_json)["matches"]
    )
    backtest_slice = BacktestSlice(
        slice_id="backtest-slice-1",
        backtest_run_id=backtest.backtest_run_id,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        decision_as_of_at_utc=artifacts.analysis_run.as_of_at_utc,
        kickoff_from_utc=min(match.kickoff_at_utc for match in artifacts.matches),
        kickoff_to_utc=max(match.kickoff_at_utc for match in artifacts.matches),
        evaluation_as_of_at_utc=settled_at,
        analysis_run_id=artifacts.analysis_run.analysis_run_id,
        decision_input_manifest_hash=artifacts.analysis_run.input_manifest_hash,
        match_result_ids=tuple(
            latest_results[match_id].match_result_id for match_id in match_ids
        ),
        expected_match_ids=analysis_match_ids,
        match_count=len(analysis_match_ids),
        settled_match_count=len(match_ids),
        settled_ticket_count=len(settlements),
        unsettled_ticket_count=0,
    )
    assert repository.append_backtest_slice(backtest_slice) == backtest_slice
    slice_record = repository.find_backtest_slice(backtest_slice.slice_id)
    assert slice_record is not None
    assert slice_record.coverage == 1
    assert slice_record.settled_ticket_count == len(settlements)
    assert tuple(
        item.backtest_slice_id
        for item in repository.backtest_slices(backtest.backtest_run_id, settled_at)
    ) == (backtest_slice.slice_id,)

    metrics = calculate_backtest_metrics(backtest, (), ())
    metric_record = backtest_metric_snapshot_record(
        metrics,
        "metric-snapshot-1",
        settled_at,
        calculated_at_utc=processing_started + timedelta(minutes=1),
        backtest_slice_id=backtest_slice.slice_id,
        metric_key="OVERALL",
        portfolio_settlement_ids=(portfolio_record.portfolio_settlement_id,),
        ticket_settlement_ids=tuple(item.settlement_id for item in settlements),
    )
    repository.append_backtest_metric_snapshot(metric_record)
    assert backtest_metrics_value(metric_record) == metrics
    assert tuple(
        item.metric_snapshot_id
        for item in repository.latest_backtest_metric_snapshots(
            backtest.backtest_run_id,
            settled_at,
        )
    ) == (metric_record.metric_snapshot_id,)

    invalid_run = BacktestRunRecord(
        **{
            column.name: getattr(run_record, column.name)
            for column in run_record.__table__.columns
            if column.name != "run_hash"
        },
        run_hash="f" * 64,
    )
    with pytest.raises(ValueError, match="hash verification"):
        repository.append_backtest_run(invalid_run)
    _assert_backtest_slice_checks(engine, backtest_slice.slice_id)
    _assert_cross_scope_rejected(engine)
    _assert_competing_settlement_roots(engine)
    _assert_append_only(engine)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_repository_corrections_require_changed_direct_successors() -> None:
    (
        _,
        repository,
        run_id,
        portfolio,
        result_by_match,
        root_settlements,
        root_portfolio,
        settled_at,
        observed,
    ) = _correction_graph("run-repository-corrections")
    target_ticket, target_match_id = _unique_ticket_match(portfolio)
    target = next(
        item for item in root_settlements if item.ticket_id == target_ticket.ticket_id
    )

    with pytest.raises(ValueError, match="change at least one MatchResult"):
        repository.append_ticket_settlement(
            target.model_copy(
                update={
                    "settlement_id": "repository-ticket-noop",
                    "supersedes_settlement_id": target.settlement_id,
                }
            )
        )
    with pytest.raises(ValueError, match="change at least one ticket settlement"):
        repository.append_portfolio_settlement(
            _portfolio_correction_record(
                root_portfolio,
                "repository-portfolio-noop",
                root_portfolio.portfolio_settlement_id,
                tuple(item.settlement_id for item in root_settlements),
                settled_at,
            ),
            tuple(item.settlement_id for item in root_settlements),
        )

    result_v2 = _append_result_version(
        repository,
        result_by_match[target_match_id],
        observed,
        settled_at,
        version=2,
    )
    results_v2 = {**result_by_match, target_match_id: result_v2}
    correction_v1 = _settlement(
        run_id,
        portfolio,
        target_ticket,
        results_v2,
        settled_at,
    ).model_copy(
        update={
            "settlement_id": "repository-ticket-correction-v1",
            "supersedes_settlement_id": target.settlement_id,
        }
    )
    assert repository.append_ticket_settlement(correction_v1) == correction_v1

    corrected_ids = tuple(
        correction_v1.settlement_id
        if item.ticket_id == target.ticket_id
        else item.settlement_id
        for item in root_settlements
    )
    portfolio_v1 = _portfolio_correction_record(
        root_portfolio,
        "repository-portfolio-correction-v1",
        root_portfolio.portfolio_settlement_id,
        corrected_ids,
        settled_at,
    )
    repository.append_portfolio_settlement(portfolio_v1, corrected_ids)

    result_v3 = _append_result_version(
        repository,
        result_v2,
        observed,
        settled_at + timedelta(seconds=1),
        version=3,
    )
    result_v4 = _append_result_version(
        repository,
        result_v3,
        observed,
        settled_at + timedelta(seconds=2),
        version=4,
    )
    results_v4 = {**result_by_match, target_match_id: result_v4}
    skipped_ticket = _settlement(
        run_id,
        portfolio,
        target_ticket,
        results_v4,
        settled_at + timedelta(seconds=2),
    ).model_copy(
        update={
            "settlement_id": "repository-ticket-skipped",
            "supersedes_settlement_id": correction_v1.settlement_id,
        }
    )
    with pytest.raises(ValueError, match="unchanged or direct successors"):
        repository.append_ticket_settlement(skipped_ticket)

    correction_v2 = _settlement(
        run_id,
        portfolio,
        target_ticket,
        {**result_by_match, target_match_id: result_v3},
        settled_at + timedelta(seconds=1),
    ).model_copy(
        update={
            "settlement_id": "repository-ticket-correction-v2",
            "supersedes_settlement_id": correction_v1.settlement_id,
        }
    )
    correction_v3 = skipped_ticket.model_copy(
        update={
            "settlement_id": "repository-ticket-correction-v3",
            "supersedes_settlement_id": correction_v2.settlement_id,
        }
    )
    repository.append_ticket_settlement(correction_v2)
    repository.append_ticket_settlement(correction_v3)
    skipped_ids = tuple(
        correction_v3.settlement_id
        if item.ticket_id == target.ticket_id
        else item.settlement_id
        for item in root_settlements
    )
    with pytest.raises(ValueError, match="unchanged or direct successors"):
        repository.append_portfolio_settlement(
            _portfolio_correction_record(
                root_portfolio,
                "repository-portfolio-skipped",
                portfolio_v1.portfolio_settlement_id,
                skipped_ids,
                settled_at + timedelta(seconds=2),
            ),
            skipped_ids,
        )


def test_sql_correction_triggers_require_changed_direct_successors() -> None:
    (
        engine,
        repository,
        run_id,
        portfolio,
        result_by_match,
        root_settlements,
        root_portfolio,
        settled_at,
        observed,
    ) = _correction_graph("run-sql-corrections")
    target_ticket, target_match_id = _unique_ticket_match(portfolio)
    target = next(
        item for item in root_settlements if item.ticket_id == target_ticket.ticket_id
    )
    root_ids = tuple(item.settlement_id for item in root_settlements)

    no_op_ticket = target.model_copy(
        update={
            "settlement_id": "sql-ticket-noop",
            "supersedes_settlement_id": target.settlement_id,
        }
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                TicketSettlementRecord.__table__.insert(),
                _record_values(_ticket_settlement_record(no_op_ticket)),
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            no_op_portfolio = _portfolio_correction_record(
                root_portfolio,
                "sql-portfolio-noop",
                root_portfolio.portfolio_settlement_id,
                root_ids,
                settled_at,
            )
            connection.execute(
                PortfolioSettlementRecord.__table__.insert(),
                _record_values(no_op_portfolio),
            )
            connection.execute(
                PortfolioSettlementTicketRecord.__table__.insert(),
                [
                    {
                        "portfolio_settlement_id": no_op_portfolio.portfolio_settlement_id,
                        "settlement_no": number,
                        "settlement_id": settlement_id,
                    }
                    for number, settlement_id in enumerate(root_ids, start=1)
                ],
            )

    result_v2 = _append_result_version(
        repository,
        result_by_match[target_match_id],
        observed,
        settled_at,
        version=2,
    )
    correction_v1 = _settlement(
        run_id,
        portfolio,
        target_ticket,
        {**result_by_match, target_match_id: result_v2},
        settled_at,
    ).model_copy(
        update={
            "settlement_id": "sql-ticket-correction-v1",
            "supersedes_settlement_id": target.settlement_id,
        }
    )
    correction_matches = {
        result.match_result_id: result.match_id
        for result in (*result_by_match.values(), result_v2)
    }
    with engine.begin() as connection:
        connection.execute(
            TicketSettlementRecord.__table__.insert(),
            _record_values(_ticket_settlement_record(correction_v1)),
        )
        connection.execute(
            TicketSettlementMatchResultRecord.__table__.insert(),
            [
                {
                    "settlement_id": correction_v1.settlement_id,
                    "leg_no": number,
                    "match_result_id": result_id,
                    "internal_match_id": correction_matches[result_id],
                }
                for number, result_id in enumerate(
                    correction_v1.match_result_ids,
                    start=1,
                )
            ],
        )
    assert (
        repository.find_ticket_settlement(correction_v1.settlement_id) == correction_v1
    )

    corrected_ids = tuple(
        correction_v1.settlement_id
        if item.ticket_id == target.ticket_id
        else item.settlement_id
        for item in root_settlements
    )
    portfolio_v1 = _portfolio_correction_record(
        root_portfolio,
        "sql-portfolio-correction-v1",
        root_portfolio.portfolio_settlement_id,
        corrected_ids,
        settled_at,
    )
    with engine.begin() as connection:
        connection.execute(
            PortfolioSettlementRecord.__table__.insert(),
            _record_values(portfolio_v1),
        )
        connection.execute(
            PortfolioSettlementTicketRecord.__table__.insert(),
            [
                {
                    "portfolio_settlement_id": portfolio_v1.portfolio_settlement_id,
                    "settlement_no": number,
                    "settlement_id": settlement_id,
                }
                for number, settlement_id in enumerate(corrected_ids, start=1)
            ],
        )
    assert (
        repository.load_portfolio_settlement(
            portfolio_v1.portfolio_settlement_id
        ).ticket_settlement_ids
        == corrected_ids
    )

    result_v3 = _append_result_version(
        repository,
        result_v2,
        observed,
        settled_at + timedelta(seconds=1),
        version=3,
    )
    result_v4 = _append_result_version(
        repository,
        result_v3,
        observed,
        settled_at + timedelta(seconds=2),
        version=4,
    )
    skipped_ticket = _settlement(
        run_id,
        portfolio,
        target_ticket,
        {**result_by_match, target_match_id: result_v4},
        settled_at + timedelta(seconds=2),
    ).model_copy(
        update={
            "settlement_id": "sql-ticket-skipped",
            "supersedes_settlement_id": correction_v1.settlement_id,
        }
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                TicketSettlementRecord.__table__.insert(),
                _record_values(_ticket_settlement_record(skipped_ticket)),
            )

    correction_v2 = _settlement(
        run_id,
        portfolio,
        target_ticket,
        {**result_by_match, target_match_id: result_v3},
        settled_at + timedelta(seconds=1),
    ).model_copy(
        update={
            "settlement_id": "sql-ticket-correction-v2",
            "supersedes_settlement_id": correction_v1.settlement_id,
        }
    )
    correction_v3 = skipped_ticket.model_copy(
        update={
            "settlement_id": "sql-ticket-correction-v3",
            "supersedes_settlement_id": correction_v2.settlement_id,
        }
    )
    repository.append_ticket_settlement(correction_v2)
    repository.append_ticket_settlement(correction_v3)
    skipped_ids = tuple(
        correction_v3.settlement_id
        if item.ticket_id == target.ticket_id
        else item.settlement_id
        for item in root_settlements
    )
    skipped_portfolio = _portfolio_correction_record(
        root_portfolio,
        "sql-portfolio-skipped",
        portfolio_v1.portfolio_settlement_id,
        skipped_ids,
        settled_at + timedelta(seconds=2),
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                PortfolioSettlementRecord.__table__.insert(),
                _record_values(skipped_portfolio),
            )
            connection.execute(
                PortfolioSettlementTicketRecord.__table__.insert(),
                [
                    {
                        "portfolio_settlement_id": (
                            skipped_portfolio.portfolio_settlement_id
                        ),
                        "settlement_no": number,
                        "settlement_id": settlement_id,
                    }
                    for number, settlement_id in enumerate(skipped_ids, start=1)
                ],
            )


def test_historical_migration_upgrades_c8_head_and_downgrades(tmp_path) -> None:
    database_path = tmp_path / "historical-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "c8b7e2a4f190")
    engine = create_database_engine(database_url)
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    assert NEW_TABLES <= set(inspect(engine).get_table_names())
    assert {
        "provider_mapping_id",
    } <= {column["name"] for column in inspect(engine).get_columns("match_results")}
    assert {
        "uq_match_results_provider_match_root",
    } <= {item["name"] for item in inspect(engine).get_indexes("match_results")}
    assert {
        "uq_ticket_settlements_logical_root",
    } <= {item["name"] for item in inspect(engine).get_indexes("ticket_settlements")}
    assert {
        "uq_portfolio_settlements_logical_root",
    } <= {item["name"] for item in inspect(engine).get_indexes("portfolio_settlements")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "f3a1c6d8e204"
        )
        triggers = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
        )
        assert "trg_match_results_append_only_update" in triggers
        assert "trg_match_results_provider_mapping_insert" in triggers
        assert "trg_backtest_metric_snapshots_append_only_delete" in triggers
        assert "trg_backtest_metric_ticket_settlements_lineage_insert" in triggers
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
    command.check(config)

    command.downgrade(config, "c8b7e2a4f190")
    engine = create_database_engine(database_url)
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "c8b7e2a4f190"
        )
        assert not {
            name
            for name in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).scalars()
            if "backtest" in name or "settlement" in name or "match_results" in name
        }
    engine.dispose()


def _correction_graph(analysis_run_id: str):
    engine, sessions, artifacts = _completed_analysis(analysis_run_id=analysis_run_id)
    repository = SqlAlchemyHistoricalRepository(sessions)
    portfolio = artifacts.portfolios[0]
    match_ids = tuple(
        sorted(
            {
                leg.match_id
                for ticket in portfolio.tickets
                for leg in ticket.candidate.legs
            }
        )
    )
    _add_historical_provider(sessions, match_ids)
    observed = max(
        match.kickoff_at_utc
        for match in artifacts.matches
        if match.match_id in match_ids
    ) + timedelta(hours=2)
    available = observed + timedelta(minutes=1)
    ingested = available + timedelta(minutes=1)
    results = tuple(
        _match_result(match_id, observed, available, ingested) for match_id in match_ids
    )
    repository.append_match_results(results)
    result_by_match = {result.match_id: result for result in results}
    settled_at = ingested + timedelta(minutes=1)
    settlements = tuple(
        _settlement(
            artifacts.analysis_run.analysis_run_id,
            portfolio,
            ticket,
            result_by_match,
            settled_at,
        )
        for ticket in portfolio.tickets
    )
    for settlement in settlements:
        repository.append_ticket_settlement(settlement)
    settlement_ids = tuple(item.settlement_id for item in settlements)
    portfolio_record = PortfolioSettlementRecord(
        portfolio_settlement_id=f"portfolio-settlement-{analysis_run_id}",
        settlement_kind="BACKTEST",
        scope_kind="ANALYSIS_RUN",
        parent_analysis_run_id=artifacts.analysis_run.analysis_run_id,
        decision_scope_id=artifacts.analysis_run.analysis_run_id,
        portfolio_revision_id=None,
        portfolio_id=portfolio.portfolio_id,
        base_portfolio_id=portfolio.portfolio_id,
        budget_fen=portfolio.budget_fen,
        total_stake_fen=portfolio.total_stake_fen,
        cash_fen=portfolio.unused_budget_fen,
        gross_payout_fen=sum(item.gross_payout_fen for item in settlements),
        profit_loss_fen=sum(item.profit_loss_fen for item in settlements),
        ticket_count=len(settlements),
        settlement_policy_version=settlements[0].settlement_policy_version,
        settled_at_utc=settled_at,
        settlement_hash="0" * 64,
        supersedes_portfolio_settlement_id=None,
    )
    portfolio_record.settlement_hash = portfolio_settlement_hash(
        portfolio_record,
        settlement_ids,
    )
    repository.append_portfolio_settlement(portfolio_record, settlement_ids)
    return (
        engine,
        repository,
        artifacts.analysis_run.analysis_run_id,
        portfolio,
        result_by_match,
        settlements,
        portfolio_record,
        settled_at,
        observed,
    )


def _slice_lineage_graph(
    analysis_run_id: str,
    backtest_run_id: str,
    expected_slice_ids: tuple[str, ...],
):
    engine, sessions, artifacts = _completed_analysis(analysis_run_id=analysis_run_id)
    repository = SqlAlchemyHistoricalRepository(sessions)
    decision_match_ids = tuple(
        item["match_id"]
        for item in json.loads(artifacts.analysis_run.input_manifest_json)["matches"]
    )
    _add_historical_provider(sessions, decision_match_ids)
    observed = max(item.kickoff_at_utc for item in artifacts.matches) + timedelta(
        hours=2
    )
    first_v1 = _match_result(
        decision_match_ids[0],
        observed,
        observed + timedelta(minutes=1),
        observed + timedelta(minutes=2),
    )
    second_v1 = _match_result(
        decision_match_ids[1],
        observed,
        observed + timedelta(minutes=1),
        observed + timedelta(minutes=2),
    )
    repository.append_match_results((first_v1, second_v1))
    first_v2 = _append_result_version(
        repository,
        first_v1,
        observed,
        observed + timedelta(minutes=3),
        version=2,
    )
    run = _backtest_run(
        backtest_run_id,
        expected_slice_ids=expected_slice_ids,
    )
    repository.append_backtest_run(run)
    base = BacktestSlice(
        slice_id="slice-lineage-template",
        backtest_run_id=run.backtest_run_id,
        data_mode=run.data_mode,
        decision_as_of_at_utc=artifacts.analysis_run.as_of_at_utc,
        kickoff_from_utc=min(item.kickoff_at_utc for item in artifacts.matches),
        kickoff_to_utc=max(item.kickoff_at_utc for item in artifacts.matches),
        evaluation_as_of_at_utc=observed + timedelta(minutes=4),
        analysis_run_id=artifacts.analysis_run.analysis_run_id,
        decision_input_manifest_hash=artifacts.analysis_run.input_manifest_hash,
        match_result_ids=(first_v2.match_result_id, second_v1.match_result_id),
        expected_match_ids=decision_match_ids,
        match_count=len(decision_match_ids),
        settled_match_count=2,
        settled_ticket_count=0,
        unsettled_ticket_count=0,
    )
    return engine, repository, artifacts, base, first_v1, first_v2


def _unique_ticket_match(portfolio):
    ticket_count_by_match: dict[str, int] = {}
    for ticket in portfolio.tickets:
        for leg in ticket.candidate.legs:
            ticket_count_by_match[leg.match_id] = (
                ticket_count_by_match.get(leg.match_id, 0) + 1
            )
    match_id = next(
        match_id
        for match_id, ticket_count in ticket_count_by_match.items()
        if ticket_count == 1
    )
    ticket = next(
        ticket
        for ticket in portfolio.tickets
        if match_id in {leg.match_id for leg in ticket.candidate.legs}
    )
    return ticket, match_id


def _append_result_version(
    repository: SqlAlchemyHistoricalRepository,
    previous: MatchResult,
    observed: datetime,
    visible_at: datetime,
    *,
    version: int,
) -> MatchResult:
    result = _match_result(
        previous.match_id,
        observed,
        visible_at,
        visible_at,
        version=version,
        supersedes_match_result_id=previous.match_result_id,
    )
    repository.append_match_result(result)
    return result


def _portfolio_correction_record(
    root: PortfolioSettlementRecord,
    portfolio_settlement_id: str,
    supersedes_portfolio_settlement_id: str,
    settlement_ids: tuple[str, ...],
    settled_at_utc: datetime,
) -> PortfolioSettlementRecord:
    values = {
        column.name: getattr(root, column.name) for column in root.__table__.columns
    }
    values.update(
        {
            "portfolio_settlement_id": portfolio_settlement_id,
            "settled_at_utc": settled_at_utc,
            "settlement_hash": "0" * 64,
            "supersedes_portfolio_settlement_id": (supersedes_portfolio_settlement_id),
        }
    )
    record = PortfolioSettlementRecord(**values)
    record.settlement_hash = portfolio_settlement_hash(record, settlement_ids)
    return record


def _record_values(record) -> dict[str, object]:
    return {
        column.name: getattr(record, column.name) for column in record.__table__.columns
    }


def _completed_analysis(
    *,
    min_selection_ev: Decimal | None = None,
    analysis_run_id: str = "run-historical-persistence",
):
    settings = AppSettings.from_toml("config/mvp.toml")
    dataset = MockDataset.from_json(settings.mock.fixture_path)
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyAnalysisRepository(sessions)
    service = RunAnalysisService(
        fixture_provider=MockFixtureProvider(dataset),
        market_odds_provider=MockMarketOddsProvider(dataset),
        sporttery_provider=MockSportteryProvider(dataset),
        manual_quant_provider=MockManualQuantProvider(dataset),
        repository=repository,
        settings=settings,
    )
    artifacts = asyncio.run(
        service.run(
            RunAnalysisRequest(
                as_of_at_utc=dataset.as_of_at_utc,
                kickoff_from_utc=dataset.as_of_at_utc,
                kickoff_to_utc=dataset.as_of_at_utc + timedelta(days=2),
                budgets_fen=(10_000,),
                fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
                min_selection_ev=min_selection_ev,
                analysis_run_id=analysis_run_id,
                execution_time_utc=EXECUTION_TIME,
            )
        )
    )
    return engine, sessions, artifacts


def _drop_trigger(engine, trigger_name: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"DROP TRIGGER {trigger_name}"))


def _archive_manifest() -> HistoricalArchiveManifest:
    return HistoricalArchiveManifest(
        archive_schema_version=HISTORICAL_ARCHIVE_SCHEMA_VERSION,
        archive_id="historical-archive-1",
        provider_code=PROVIDER_CODE,
        dataset_kind=HistoricalArchiveDatasetKind.MATCH_RESULTS,
        created_at_utc=EXECUTION_TIME,
        source_reference="test://historical-results",
        source_description="Immutable source-time result archive for persistence tests",
        license_note="Synthetic test data only",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        payload_sha256="a" * 64,
        record_count=2,
    )


def _backtest_run(
    backtest_run_id: str,
    *,
    archive_provenance: tuple[BacktestArchiveProvenance, ...] = (),
    expected_slice_ids: tuple[str, ...] = (),
) -> BacktestRun:
    return BacktestRun(
        backtest_run_id=backtest_run_id,
        backtest_version="HISTORICAL_BACKTEST_V1",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        date_from=EXECUTION_TIME.date(),
        date_to=EXECUTION_TIME.date(),
        strategy_snapshot=BacktestStrategySnapshot.from_config(
            "QUANT_ONLY_V1",
            {"mode": "strict"},
        ),
        code_revision="integration-test-revision",
        created_at_utc=EXECUTION_TIME + timedelta(days=1),
        status=BacktestRunStatus.COMPLETED,
        archive_provenance=archive_provenance,
        expected_slice_ids=expected_slice_ids,
    )


def _add_historical_provider(sessions, match_ids: tuple[str, ...]) -> None:
    provider_id = stable_id("provider", PROVIDER_CODE)
    with sessions.begin() as session:
        session.add(
            ProviderRecord(
                provider_id=provider_id,
                code=PROVIDER_CODE,
                name="Historical Test",
                provider_kind="MATCH_RESULTS",
            )
        )
        session.flush()
        session.add_all(
            ProviderMatchMappingRecord(
                mapping_id=f"historical-mapping-{match_id}",
                provider_id=provider_id,
                external_namespace="historical-test",
                external_match_id=f"external-{match_id}",
                internal_match_id=match_id,
                resolution_method="TEST_EXACT",
                confidence=1,
                available_at_utc=EXECUTION_TIME - timedelta(days=1),
                supersedes_mapping_id=None,
            )
            for match_id in match_ids
        )


def _match_result(
    match_id: str,
    observed: datetime,
    available: datetime,
    ingested: datetime,
    *,
    version: int = 1,
    supersedes_match_result_id: str | None = None,
) -> MatchResult:
    home_goals, away_goals = (1, 1) if match_id == "match-006" else (1, 0)
    return MatchResult(
        match_result_id=f"result-{match_id}-v{version}",
        match_id=match_id,
        provider_code=PROVIDER_CODE,
        home_goals=home_goals,
        away_goals=away_goals,
        observed_at_utc=observed,
        available_at_utc=available,
        ingested_at_utc=ingested,
        source_result_key=f"source-{match_id}-v{version}",
        payload_hash=match_result_payload_sha256(home_goals, away_goals),
        supersedes_match_result_id=supersedes_match_result_id,
    )


def _settlement(run_id, portfolio, ticket, results, settled_at) -> Settlement:
    match_results = tuple(results[leg.match_id] for leg in ticket.candidate.legs)
    won = all(
        result.three_way_selection() == leg.selection
        for result, leg in zip(match_results, ticket.candidate.legs, strict=True)
    )
    gross_payout = ticket.potential_gross_payout_fen if won else 0
    return Settlement(
        settlement_id=f"settlement-{ticket.ticket_id}",
        scope_kind=SettlementScopeKind.ANALYSIS_RUN,
        parent_analysis_run_id=run_id,
        decision_scope_id=run_id,
        portfolio_id=portfolio.portfolio_id,
        ticket_id=ticket.ticket_id,
        match_result_ids=tuple(item.match_result_id for item in match_results),
        status=SettlementStatus.WON if won else SettlementStatus.LOST,
        stake_fen=ticket.stake_fen,
        gross_payout_fen=gross_payout,
        profit_loss_fen=gross_payout - ticket.stake_fen,
        payout_policy_version=ticket.candidate.payout_policy_version,
        settlement_policy_version="TWO_FOLD_ONE_SETTLEMENT_V1",
        settled_at_utc=settled_at,
    )


def _assert_match_result_checks(
    engine,
    match_id: str,
    observed: datetime,
    available: datetime,
    ingested: datetime,
) -> None:
    provider_id = stable_id("provider", PROVIDER_CODE)
    values = {
        "match_result_id": "invalid-result",
        "internal_match_id": match_id,
        "provider_id": provider_id,
        "provider_mapping_id": f"historical-mapping-{match_id}",
        "home_goals": -1,
        "away_goals": 0,
        "observed_at_utc": observed,
        "available_at_utc": available,
        "ingested_at_utc": ingested,
        "source_result_key": "invalid-source",
        "payload_hash": "a" * 64,
        "supersedes_match_result_id": None,
    }
    statement = _insert_match_result_statement()
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)
    values.update(
        match_result_id="invalid-timeline",
        home_goals=1,
        observed_at_utc=ingested,
        available_at_utc=available,
        source_result_key="invalid-timeline-source",
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)
    values.update(
        match_result_id="invalid-second-root",
        home_goals=1,
        observed_at_utc=observed,
        available_at_utc=ingested + timedelta(minutes=3),
        ingested_at_utc=ingested + timedelta(minutes=4),
        source_result_key="invalid-second-root-source",
        payload_hash=match_result_payload_sha256(1, 0),
        supersedes_match_result_id=None,
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)
    values.update(
        match_result_id="invalid-second-successor",
        source_result_key="invalid-second-successor-source",
        supersedes_match_result_id=f"result-{match_id}-v1",
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)
    values.update(
        match_result_id="invalid-duplicate-source",
        source_result_key=f"source-{match_id}-v1",
        supersedes_match_result_id=f"result-{match_id}-v2",
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(statement, values)


def _insert_match_result_statement():
    return text(
        """
        INSERT INTO match_results (
            match_result_id, internal_match_id, provider_id,
            provider_mapping_id, home_goals,
            away_goals, observed_at_utc, available_at_utc, ingested_at_utc,
            source_result_key, payload_hash, supersedes_match_result_id
        ) VALUES (
            :match_result_id, :internal_match_id, :provider_id,
            :provider_mapping_id, :home_goals,
            :away_goals, :observed_at_utc, :available_at_utc, :ingested_at_utc,
            :source_result_key, :payload_hash, :supersedes_match_result_id
        )
        """
    )


def _assert_backtest_slice_checks(engine, backtest_slice_id: str) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO backtest_slices (
                        backtest_slice_id, backtest_run_id, slice_no,
                        slice_version, parent_analysis_run_id, data_mode,
                        scope_kind, decision_scope_id, portfolio_revision_id,
                        decision_as_of_at_utc, evaluation_as_of_at_utc,
                        created_at_utc, slice_manifest_json,
                        slice_manifest_hash, slice_hash, match_count,
                        settled_match_count, settled_ticket_count,
                        unsettled_ticket_count, coverage
                    )
                    SELECT
                        'invalid-slice-coverage', backtest_run_id, slice_no + 1,
                        slice_version, parent_analysis_run_id, data_mode,
                        scope_kind, decision_scope_id, portfolio_revision_id,
                        decision_as_of_at_utc, evaluation_as_of_at_utc,
                        created_at_utc, slice_manifest_json,
                        slice_manifest_hash,
                        'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                        match_count, settled_match_count, 0, 0, coverage
                    FROM backtest_slices
                    WHERE backtest_slice_id = :backtest_slice_id
                    """
                ),
                {"backtest_slice_id": backtest_slice_id},
            )


def _assert_cross_scope_rejected(engine) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ticket_settlements (
                        settlement_id, settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, ticket_id,
                        base_portfolio_id, base_ticket_id, status, stake_fen,
                        gross_payout_fen, profit_loss_fen,
                        payout_policy_version, settlement_policy_version,
                        settled_at_utc, settlement_json, settlement_hash,
                        supersedes_settlement_id
                    )
                    SELECT
                        'cross-scope-settlement', settlement_kind,
                        'PORTFOLIO_REVISION', parent_analysis_run_id,
                        decision_scope_id, NULL, portfolio_id, ticket_id,
                        base_portfolio_id, base_ticket_id, status, stake_fen,
                        gross_payout_fen, profit_loss_fen,
                        payout_policy_version, settlement_policy_version,
                        settled_at_utc, settlement_json,
                        'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
                        NULL
                    FROM ticket_settlements LIMIT 1
                    """
                )
            )


def _assert_competing_settlement_roots(engine) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ticket_settlements (
                        settlement_id, settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, ticket_id,
                        base_portfolio_id, base_ticket_id, status, stake_fen,
                        gross_payout_fen, profit_loss_fen,
                        payout_policy_version, settlement_policy_version,
                        settled_at_utc, settlement_json, settlement_hash,
                        supersedes_settlement_id
                    )
                    SELECT
                        'direct-second-ticket-root', settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, ticket_id,
                        base_portfolio_id, base_ticket_id, status, stake_fen,
                        gross_payout_fen, profit_loss_fen,
                        payout_policy_version, settlement_policy_version,
                        settled_at_utc, settlement_json,
                        'abababababababababababababababababababababababababababababababab',
                        NULL
                    FROM ticket_settlements
                    WHERE supersedes_settlement_id IS NULL
                    LIMIT 1
                    """
                )
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ticket_settlements (
                        settlement_id, settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, ticket_id,
                        base_portfolio_id, base_ticket_id, status, stake_fen,
                        gross_payout_fen, profit_loss_fen,
                        payout_policy_version, settlement_policy_version,
                        settled_at_utc, settlement_json, settlement_hash,
                        supersedes_settlement_id
                    )
                    SELECT
                        'direct-second-ticket-successor', settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, ticket_id,
                        base_portfolio_id, base_ticket_id, status, stake_fen,
                        gross_payout_fen, profit_loss_fen,
                        payout_policy_version, settlement_policy_version,
                        datetime(settled_at_utc, '+1 minute'), settlement_json,
                        'dededededededededededededededededededededededededededededededede',
                        supersedes_settlement_id
                    FROM ticket_settlements
                    WHERE supersedes_settlement_id IS NOT NULL
                    LIMIT 1
                    """
                )
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO portfolio_settlements (
                    portfolio_settlement_id, settlement_kind, scope_kind,
                    parent_analysis_run_id, decision_scope_id,
                    portfolio_revision_id, portfolio_id, base_portfolio_id,
                    budget_fen, total_stake_fen, cash_fen,
                    gross_payout_fen, profit_loss_fen, ticket_count,
                    settlement_policy_version, settled_at_utc,
                    settlement_hash, supersedes_portfolio_settlement_id
                )
                SELECT
                    'direct-portfolio-successor', settlement_kind, scope_kind,
                    parent_analysis_run_id, decision_scope_id,
                    portfolio_revision_id, portfolio_id, base_portfolio_id,
                    budget_fen, total_stake_fen, cash_fen,
                    gross_payout_fen, profit_loss_fen, ticket_count,
                    settlement_policy_version,
                    datetime(settled_at_utc, '+1 minute'),
                    'efefefefefefefefefefefefefefefefefefefefefefefefefefefefefefefef',
                    portfolio_settlement_id
                FROM portfolio_settlements
                WHERE supersedes_portfolio_settlement_id IS NULL
                LIMIT 1
                """
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_settlements (
                        portfolio_settlement_id, settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, base_portfolio_id,
                        budget_fen, total_stake_fen, cash_fen,
                        gross_payout_fen, profit_loss_fen, ticket_count,
                        settlement_policy_version, settled_at_utc,
                        settlement_hash, supersedes_portfolio_settlement_id
                    )
                    SELECT
                        'direct-second-portfolio-successor', settlement_kind,
                        scope_kind, parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, base_portfolio_id,
                        budget_fen, total_stake_fen, cash_fen,
                        gross_payout_fen, profit_loss_fen, ticket_count,
                        settlement_policy_version,
                        datetime(settled_at_utc, '+2 minutes'),
                        '1212121212121212121212121212121212121212121212121212121212121212',
                        portfolio_settlement_id
                    FROM portfolio_settlements
                    WHERE supersedes_portfolio_settlement_id IS NULL
                    LIMIT 1
                    """
                )
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio_settlements (
                        portfolio_settlement_id, settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, base_portfolio_id,
                        budget_fen, total_stake_fen, cash_fen,
                        gross_payout_fen, profit_loss_fen, ticket_count,
                        settlement_policy_version, settled_at_utc,
                        settlement_hash, supersedes_portfolio_settlement_id
                    )
                    SELECT
                        'direct-second-portfolio-root', settlement_kind, scope_kind,
                        parent_analysis_run_id, decision_scope_id,
                        portfolio_revision_id, portfolio_id, base_portfolio_id,
                        budget_fen, total_stake_fen, cash_fen,
                        gross_payout_fen, profit_loss_fen, ticket_count,
                        settlement_policy_version, settled_at_utc,
                        'cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd',
                        NULL
                    FROM portfolio_settlements
                    WHERE supersedes_portfolio_settlement_id IS NULL
                    LIMIT 1
                    """
                )
            )


def _assert_append_only(engine) -> None:
    for table_name in APPEND_ONLY_TABLES:
        for statement in (
            f"UPDATE {table_name} SET rowid = rowid WHERE rowid = "
            f"(SELECT rowid FROM {table_name} LIMIT 1)",
            f"DELETE FROM {table_name} WHERE rowid = "
            f"(SELECT rowid FROM {table_name} LIMIT 1)",
            f"INSERT OR REPLACE INTO {table_name} SELECT * FROM {table_name} LIMIT 1",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(statement))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
