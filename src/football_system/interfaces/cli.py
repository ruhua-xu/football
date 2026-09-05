from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.backtest import (
    WalkForwardBacktestRequest,
    WalkForwardBacktestService,
    validate_backtest_runtime_provenance,
)
from football_system.application.backtest_reports import (
    BacktestReportComparison,
    BacktestReportData,
    expected_match_ids_from_analysis_manifest,
    load_backtest_fixture,
    render_backtest_comparison,
    render_backtest_report,
    render_historical_archive_summary,
    render_match_results,
    render_settlement_report,
    render_settlement_result,
)
from football_system.application.daily_slate import PlanSportteryDailySlateService
from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    RuntimeProvenance,
    require_provider_runtime_provenance,
)
from football_system.application.historical_archive import HistoricalArchiveService
from football_system.application.identity_catalog import (
    FixtureIngestionRequest,
    MatchIdentityCatalog,
)
from football_system.application.live_ingestion import LiveFixtureIngestionService
from football_system.application.live_sources import (
    IdentityReviewDocument,
    LiveAnalysisInputPolicy,
    LiveMarketOddsIngestionService,
    LiveSportteryIngestionService,
    NoAvailableLiveTrainingHistoryProvider,
    PrepareAnalysisRequest,
    PrepareLiveAnalysisService,
    PreparationStatus,
    PreparedLiveFixtureProvider,
    PreparedLiveMarketOddsProvider,
    PreparedLiveSportteryProvider,
    SourceIngestionSummary,
)
from football_system.application.model_analysis import (
    PreparedFixtureObservationRef,
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.models import AnalysisArtifacts
from football_system.application.ports.data_providers import (
    MatchResultQuery,
)
from football_system.application.post_review import (
    CreateFusionRunService,
    CreatePortfolioRevisionService,
)
from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
    validate_review_files,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    RunAnalysisService,
)
from football_system.application.settlement import SettlementService
from football_system.config import AppSettings
from football_system.domain.archive import (
    HistoricalArchiveDatasetKind,
    HistoricalArchiveManifest,
    HistoricalDataMode,
    canonical_json,
)
from football_system.domain.backtest import (
    BacktestArchiveProvenance,
    BacktestMetricsConfig,
    canonical_archive_provenance,
)
from football_system.domain.betting import CandidateStatus, PortfolioStatus
from football_system.domain.common import new_id, utc_now
from football_system.domain.daily_slate import (
    DailySlateCaptureRequest,
    DailySlatePlan,
)
from football_system.domain.match import TeamType
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.services.elo_baseline import EloBaselineConfig
from football_system.domain.settlement import Settlement, SettlementScope
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
    backtest_metrics_value,
)
from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.live_source_repositories import (
    SqlAlchemyLiveSourceRepository,
)
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.post_review_repositories import (
    SqlAlchemyPostReviewRepository,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.review_repositories import (
    SqlAlchemyReviewArtifactRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
    require_sqlite_database_url,
)
from football_system.infrastructure.files.daily_slate import (
    load_daily_slate_plan,
    load_sporttery_daily_slate,
)
from football_system.infrastructure.files.raw_archive import RawDataArchive
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
from football_system.infrastructure.providers.historical_archive import (
    HistoricalArchiveFixtureProvider,
    HistoricalArchiveMarketOddsProvider,
    HistoricalArchiveQuantProvider,
    HistoricalArchiveSportteryProvider,
    LocalArchiveHistoricalDataProvider,
    LocalArchiveStore,
)
from football_system.infrastructure.files.review_bridge import (
    read_contract_file,
    write_contract_file,
)
from football_system.infrastructure.http.provider_client import (
    HttpTransport,
    ProviderHttpClient,
)
from football_system.infrastructure.http.urllib_transport import UrllibTransport
from football_system.infrastructure.providers.real.sportmonks import (
    SPORTMONKS_PROVIDER_CODE,
    SportmonksFixtureProvider,
)
from football_system.infrastructure.providers.real.sporttery_manual import (
    SPORTTERY_MANUAL_PROVIDER_CODE,
    SportteryManualArchiveCaptureProvider,
)
from football_system.infrastructure.providers.real.the_odds_api import (
    THE_ODDS_API_PROVIDER_CODE,
    TheOddsApiMarketOddsProvider,
)


SPORTMONKS_BASE_URL = "https://api.sportmonks.com/v3/football/"
THE_ODDS_API_BASE_URL = "https://api.the-odds-api.com/"


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments[:1] == ["live"]:
        return _dispatch_live(arguments[1:])
    if arguments[:1] == ["historical-archive"]:
        return _dispatch_historical_archive(arguments[1:])
    if arguments[:1] == ["match-results"]:
        return _dispatch_match_results(arguments[1:])
    if arguments[:1] == ["settlement"]:
        return _dispatch_settlement(arguments[1:])
    if arguments[:1] == ["backtest"]:
        return _dispatch_backtest(arguments[1:])
    if arguments[:1] == ["analysis-packet"]:
        return _dispatch_analysis_packet(arguments[1:])
    if arguments[:1] == ["llm-review"]:
        return _dispatch_llm_review(arguments[1:])
    if arguments[:1] == ["fusion-run"]:
        return _dispatch_fusion_run(arguments[1:])
    if arguments[:1] == ["portfolio-revision"]:
        return _dispatch_portfolio_revision(arguments[1:])
    parser = _build_parser()
    args = parser.parse_args(arguments)
    settings = AppSettings.from_toml(args.config)
    database_url = _resolve_database_url(args.database_url or settings.database.url)
    if args.database_url:
        settings = settings.model_copy(
            update={
                "database": settings.database.model_copy(update={"url": database_url})
            }
        )
    budgets_fen = tuple(_yuan_to_fen(value, parser) for value in args.budget_yuan)
    if len(budgets_fen) != len(set(budgets_fen)):
        parser.error("budgets must be unique")
    min_selection_ev = (
        Decimal("10")
        if args.no_bet_demo
        else _optional_decimal(args.min_selection_ev, parser)
    )
    min_ticket_roi = _optional_decimal(args.min_ticket_roi, parser)

    fixture_path = settings.mock.fixture_path
    RuntimeEnvironmentGuard(settings.runtime.environment).validate_input(
        RuntimeProvenance(
            environment=RuntimeEnvironment.MOCK,
            provider_code="MOCK_ANALYSIS_BUNDLE",
            provenance=str(fixture_path),
            is_mock=True,
        )
    )
    if not fixture_path.is_absolute():
        fixture_path = _resource_root() / fixture_path
    dataset = MockDataset.from_json(fixture_path)
    upgrade_database(database_url, _resource_root() / "alembic.ini")
    engine = create_database_engine(database_url)
    repository = SqlAlchemyAnalysisRepository(create_session_factory(engine))
    service = RunAnalysisService(
        fixture_provider=MockFixtureProvider(dataset),
        market_odds_provider=MockMarketOddsProvider(dataset),
        sporttery_provider=MockSportteryProvider(dataset),
        manual_quant_provider=MockManualQuantProvider(dataset),
        repository=repository,
        settings=settings,
    )
    request = RunAnalysisRequest(
        as_of_at_utc=dataset.as_of_at_utc,
        kickoff_from_utc=dataset.as_of_at_utc,
        kickoff_to_utc=dataset.as_of_at_utc + timedelta(days=2),
        budgets_fen=budgets_fen,
        fusion_policy=FusionPolicyName(
            args.fusion_policy or settings.analysis.fusion_policy
        ),
        min_selection_ev=min_selection_ev,
        min_ticket_roi=min_ticket_roi,
        analysis_run_id=args.analysis_run_id,
    )
    artifacts = asyncio.run(service.run(request))
    print(format_analysis(artifacts, repository.table_counts()))
    return 0


def _dispatch_analysis_packet(arguments: Sequence[str]) -> int:
    if not arguments or arguments[0] in {"-h", "--help"}:
        print("usage: football-system analysis-packet export [options]")
        return 0
    if arguments[0] != "export":
        raise SystemExit(f"unknown analysis-packet command: {arguments[0]}")
    return _export_analysis_packet(arguments[1:])


def _dispatch_llm_review(arguments: Sequence[str]) -> int:
    if not arguments or arguments[0] in {"-h", "--help"}:
        print("usage: football-system llm-review {validate,import} [options]")
        return 0
    if arguments[0] == "validate":
        return _validate_llm_review(arguments[1:])
    if arguments[0] == "import":
        return _import_llm_review(arguments[1:])
    raise SystemExit(f"unknown llm-review command: {arguments[0]}")


def _dispatch_fusion_run(arguments: Sequence[str]) -> int:
    if not arguments or arguments[0] in {"-h", "--help"}:
        print("usage: football-system fusion-run create [options]")
        return 0
    if arguments[0] != "create":
        raise SystemExit(f"unknown fusion-run command: {arguments[0]}")
    return _create_fusion_run(arguments[1:])


def _dispatch_portfolio_revision(arguments: Sequence[str]) -> int:
    if not arguments or arguments[0] in {"-h", "--help"}:
        print("usage: football-system portfolio-revision create [options]")
        return 0
    if arguments[0] != "create":
        raise SystemExit(f"unknown portfolio-revision command: {arguments[0]}")
    return _create_portfolio_revision(arguments[1:])


def _dispatch_historical_archive(arguments: Sequence[str]) -> int:
    return _dispatch_command_group(
        "historical-archive",
        arguments,
        {
            "validate": _validate_historical_archive,
            "import": _import_historical_archive,
        },
    )


def _dispatch_live(arguments: Sequence[str]) -> int:
    return _dispatch_command_group(
        "live",
        arguments,
        {
            "plan-slate": _plan_live_slate,
            "ingest-fixtures": _ingest_live_fixtures,
            "ingest-market-odds": _ingest_live_market_odds,
            "ingest-sporttery": _ingest_live_sporttery,
            "reconcile": _reconcile_live_sources,
            "import-identity-review": _import_live_identity_review,
            "prepare-analysis": _prepare_live_analysis,
            "run-analysis": _run_live_analysis,
        },
    )


def _dispatch_match_results(arguments: Sequence[str]) -> int:
    return _dispatch_command_group(
        "match-results",
        arguments,
        {"list": _list_match_results},
    )


def _dispatch_settlement(arguments: Sequence[str]) -> int:
    return _dispatch_command_group(
        "settlement",
        arguments,
        {
            "create": _create_settlement,
            "report": _report_settlement,
        },
    )


def _dispatch_backtest(arguments: Sequence[str]) -> int:
    return _dispatch_command_group(
        "backtest",
        arguments,
        {
            "run": _run_backtest,
            "report": _report_backtest,
            "compare": _compare_backtests,
        },
    )


def _dispatch_command_group(
    group: str,
    arguments: Sequence[str],
    handlers: dict[str, Callable[[Sequence[str]], int]],
) -> int:
    parser = argparse.ArgumentParser(prog=f"football-system {group}")
    parser.add_argument("command", choices=tuple(handlers))
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    command = arguments[0]
    handler = handlers.get(command)
    if handler is None:
        parser.error(f"invalid command: {command}")
    return handler(arguments[1:])


def _plan_live_slate(
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live plan-slate",
        description=(
            "Build a provider-neutral capture plan from one reviewed Sporttery "
            "manual archive or lightweight daily slate file."
        ),
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--input", "--archive", dest="input", type=Path, required=True)
    parser.add_argument("--as-of", type=_parse_utc_datetime)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("exchange/daily_slate_plan.json"),
    )
    args = parser.parse_args(arguments)
    command_clock = clock or utc_now
    try:
        _, database_url = _live_settings(args.config, args.database_url)
        planned_at = args.as_of or command_clock()
        slate = load_sporttery_daily_slate(args.input)
        if slate.candidates:
            kickoff_from = min(item.kickoff_at_utc for item in slate.candidates)
            kickoff_to = max(item.kickoff_at_utc for item in slate.candidates)
            catalog = _open_match_identity_repository(
                database_url,
                clock=command_clock,
            ).load_catalog(
                as_of_at_utc=planned_at,
                kickoff_from_utc=kickoff_from,
                kickoff_to_utc=kickoff_to,
            )
        else:
            catalog = MatchIdentityCatalog(
                team_identities=(),
                competition_mappings=(),
                canonical_matches=(),
                explicit_mappings=(),
            )
        plan = PlanSportteryDailySlateService().plan(
            slate,
            catalog,
            planned_at_utc=planned_at,
        )
        plan_json = canonical_json(plan)
        write_contract_file(args.output, plan_json)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(plan_json)
    print(f"Daily slate plan written: {args.output}")
    return 0


def _ingest_live_fixtures(
    arguments: Sequence[str],
    *,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live ingest-fixtures",
        description=(
            "Capture current Sportmonks fixtures, archive the raw response, and "
            "atomically persist identity and observation lineage."
        ),
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--raw-archive", type=Path, default=Path("data/raw"))
    parser.add_argument("--kickoff-from", type=_parse_utc_datetime, required=True)
    parser.add_argument("--kickoff-to", type=_parse_utc_datetime, required=True)
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--provider-season-id", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--competition-type", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--team-type",
        choices=tuple(item.value for item in TeamType),
        required=True,
    )
    args = parser.parse_args(arguments)
    try:
        settings, database_url = _historical_settings(
            args.config,
            args.database_url,
        )
        require_sqlite_database_url(database_url)
        RuntimeEnvironmentGuard(settings.runtime.environment).validate_input(
            SportmonksFixtureProvider.runtime_provenance
        )
        request = FixtureIngestionRequest(
            kickoff_from_utc=args.kickoff_from,
            kickoff_to_utc=args.kickoff_to,
            provider_competition_id=args.league_id,
            provider_season_id=args.provider_season_id,
            season=args.season,
            competition_type=args.competition_type,
            language=args.language,
            team_type=args.team_type,
        )
        api_token = _required_environment_value("SPORTMONKS_KEY", environ)
        raw_archive = RawDataArchive(args.raw_archive)
        client = ProviderHttpClient(
            SPORTMONKS_PROVIDER_CODE,
            SPORTMONKS_BASE_URL,
            transport if transport is not None else UrllibTransport(),
            utc_now=clock,
        )
        provider = SportmonksFixtureProvider(client, raw_archive, api_token)
        service = LiveFixtureIngestionService(
            provider,
            lambda: _open_match_identity_repository(
                database_url,
                clock=clock or utc_now,
            ),
            environment=settings.runtime.environment,
        )
        summary = asyncio.run(service.ingest(request))
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(
        f"Fixture ingestion: {summary.ingestion_id}; "
        f"inserted={str(summary.inserted).lower()}; "
        f"raw_artifact={summary.raw_artifact_id}; "
        f"competitions={summary.competition_count}; teams={summary.team_count}; "
        f"matches={summary.match_count}; observations={summary.observation_count}"
    )
    return 0


def _ingest_live_market_odds(
    arguments: Sequence[str],
    *,
    transport: HttpTransport | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live ingest-market-odds",
        description=(
            "Capture current The Odds API h2h prices, archive the raw response, "
            "derive consensus, and atomically persist lineage."
        ),
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--raw-archive", type=Path, default=Path("data/raw"))
    parser.add_argument("--kickoff-from", type=_parse_utc_datetime)
    parser.add_argument("--kickoff-to", type=_parse_utc_datetime)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--match-id", dest="match_ids", nargs="+")
    targets.add_argument("--plan", type=Path)
    parser.add_argument("--plan-request-id")
    parser.add_argument("--sport-key", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--competition-type", required=True)
    parser.add_argument("--regions", default="uk")
    args = parser.parse_args(arguments)
    command_clock = clock or utc_now
    try:
        settings, database_url = _live_settings(args.config, args.database_url)
        if args.plan is None:
            if args.plan_request_id is not None:
                raise ValueError("--plan-request-id requires --plan")
            if args.kickoff_from is None or args.kickoff_to is None:
                raise ValueError(
                    "--kickoff-from and --kickoff-to are required with --match-id"
                )
            match_ids = tuple(args.match_ids)
            kickoff_from = args.kickoff_from
            kickoff_to = args.kickoff_to
            source_plan = None
        else:
            if args.kickoff_from is not None or args.kickoff_to is not None:
                raise ValueError("--plan supplies its exact kickoff window")
            source_plan = load_daily_slate_plan(args.plan)
            capture_request = _select_market_capture_request(
                source_plan,
                args.plan_request_id,
            )
            match_ids = capture_request.canonical_match_ids
            kickoff_from = capture_request.kickoff_from_utc
            kickoff_to = capture_request.kickoff_to_utc
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("market ingestion match IDs must be unique")
        match_ids = tuple(sorted(match_ids))
        RuntimeEnvironmentGuard(settings.runtime.environment).validate_input(
            TheOddsApiMarketOddsProvider.runtime_provenance
        )
        api_key = _required_environment_value("ODDS_API_KEY", environ)
        identity_cutoff = command_clock()
        if source_plan is not None and source_plan.planned_at_utc > identity_cutoff:
            raise ValueError("daily slate plan is not visible at the identity cutoff")
        _, identity_repository, repository = _open_live_repositories(
            database_url,
            clock=command_clock,
        )
        catalog = identity_repository.load_catalog(
            as_of_at_utc=identity_cutoff,
            kickoff_from_utc=kickoff_from,
            kickoff_to_utc=kickoff_to,
            provider_codes=(THE_ODDS_API_PROVIDER_CODE,),
        )
        visible_match_ids = {
            item.internal_match_id for item in catalog.canonical_matches
        }
        missing_match_ids = tuple(sorted(set(match_ids) - visible_match_ids))
        if missing_match_ids:
            raise ValueError(
                "requested matches are not visible at the identity cutoff: "
                + ", ".join(missing_match_ids)
            )
        resolver = catalog.build_resolver(
            timedelta(seconds=settings.runtime.kickoff_tolerance_seconds)
        )
        client = ProviderHttpClient(
            THE_ODDS_API_PROVIDER_CODE,
            THE_ODDS_API_BASE_URL,
            transport if transport is not None else UrllibTransport(),
            utc_now=command_clock,
        )
        provider = TheOddsApiMarketOddsProvider(
            client,
            RawDataArchive(args.raw_archive),
            resolver,
            api_key,
            sport_key=args.sport_key,
            season=args.season,
            competition_type=args.competition_type,
            regions=args.regions,
        )
        summary = asyncio.run(
            LiveMarketOddsIngestionService(
                provider,
                repository,
                environment=settings.runtime.environment,
                identity_cutoff_at_utc=identity_cutoff,
                clock=command_clock,
            ).ingest(match_ids)
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    _print_live_ingestion_summary(summary)
    return 0


def _ingest_live_sporttery(
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live ingest-sporttery",
        description=(
            "Normalize a reviewed SPORTTERY_MANUAL_ARCHIVE_V2 document and "
            "atomically persist source and review lineage."
        ),
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--kickoff-from", type=_parse_utc_datetime, required=True)
    parser.add_argument("--kickoff-to", type=_parse_utc_datetime, required=True)
    args = parser.parse_args(arguments)
    command_clock = clock or utc_now
    try:
        settings, database_url = _live_settings(args.config, args.database_url)
        identity_cutoff = command_clock()
        _, identity_repository, repository = _open_live_repositories(
            database_url,
            clock=command_clock,
        )
        catalog = identity_repository.load_catalog(
            as_of_at_utc=identity_cutoff,
            kickoff_from_utc=args.kickoff_from,
            kickoff_to_utc=args.kickoff_to,
            provider_codes=(SPORTTERY_MANUAL_PROVIDER_CODE,),
        )
        provider = SportteryManualArchiveCaptureProvider(
            args.archive,
            catalog.build_resolver(
                timedelta(seconds=settings.runtime.kickoff_tolerance_seconds)
            ),
            identity_cutoff_at_utc=identity_cutoff,
        )
        summary = LiveSportteryIngestionService(
            provider,
            repository,
            environment=settings.runtime.environment,
            clock=command_clock,
        ).ingest()
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    _print_live_ingestion_summary(summary)
    return 0


def _reconcile_live_sources(
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live reconcile",
        description="Render unresolved persisted live-source identity issues as JSON.",
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--ingestion-id")
    parser.add_argument("--as-of", type=_parse_utc_datetime)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(arguments)
    try:
        _, database_url = _live_settings(args.config, args.database_url)
        _, _, repository = _open_live_repositories(
            database_url,
            clock=clock or utc_now,
        )
        report = repository.reconciliation_report(
            ingestion_id=args.ingestion_id,
            generated_at_utc=args.as_of,
        )
        report_json = canonical_json(report)
        if args.output is not None:
            write_contract_file(args.output, report_json)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report_json)
    if args.output is not None:
        print(f"Reconciliation report written: {args.output}")
    return 0


def _import_live_identity_review(
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live import-identity-review",
        description="Validate and append a reviewed live-source identity mapping.",
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        _, database_url = _live_settings(args.config, args.database_url)
        review = IdentityReviewDocument.model_validate_json(
            read_contract_file(args.review)
        )
        _, _, repository = _open_live_repositories(
            database_url,
            clock=clock or utc_now,
        )
        summary = repository.import_identity_review(review)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(
        f"Identity review: {summary.review_id}; "
        f"source_ingestion={summary.source_ingestion_id}; "
        f"inserted={str(summary.inserted).lower()}; mappings={summary.mapping_count}"
    )
    return 0


def _prepare_live_analysis(
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live prepare-analysis",
        description=(
            "Freeze cutoff-clean fixture, market consensus, and Sporttery inputs "
            "using persisted data only."
        ),
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    parser.add_argument("--decision-as-of", type=_parse_utc_datetime, required=True)
    parser.add_argument("--kickoff-from", type=_parse_utc_datetime, required=True)
    parser.add_argument("--kickoff-to", type=_parse_utc_datetime, required=True)
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--season-id", required=True)
    parser.add_argument(
        "--expected-match-id",
        dest="expected_match_ids",
        action="append",
        default=[],
    )
    parser.add_argument("--allow-partial-inputs", action="store_true")
    parser.add_argument(
        "--maximum-odds-age-seconds",
        type=_parse_positive_int,
        required=True,
    )
    parser.add_argument(
        "--minimum-bookmaker-count",
        type=_parse_positive_int,
        required=True,
    )
    parser.add_argument("--preparation-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(arguments)
    try:
        _, database_url = _live_settings(args.config, args.database_url)
        expected_match_ids = tuple(args.expected_match_ids)
        if len(expected_match_ids) != len(set(expected_match_ids)):
            raise ValueError("preparation expected match IDs must be unique")
        _, _, repository = _open_live_repositories(
            database_url,
            clock=clock or utc_now,
        )
        preparation = PrepareLiveAnalysisService(repository).prepare(
            PrepareAnalysisRequest(
                decision_as_of_at_utc=args.decision_as_of,
                kickoff_from_utc=args.kickoff_from,
                kickoff_to_utc=args.kickoff_to,
                competition_id=args.competition_id,
                season_id=args.season_id,
                expected_match_ids=tuple(sorted(expected_match_ids)),
                allow_partial_inputs=args.allow_partial_inputs,
                policy=LiveAnalysisInputPolicy(
                    maximum_odds_age_seconds=args.maximum_odds_age_seconds,
                    minimum_bookmaker_count=args.minimum_bookmaker_count,
                ),
                preparation_id=args.preparation_id,
            )
        )
        preparation_json = canonical_json(preparation)
        if args.output is not None:
            write_contract_file(args.output, preparation_json)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(preparation_json)
    if args.output is not None:
        print(f"Analysis preparation written: {args.output}")
    return 0


def _run_live_analysis(
    arguments: Sequence[str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system live run-analysis",
        description=(
            "Run the fixed Elo baseline from one frozen persisted live-source "
            "preparation without network access."
        ),
    )
    _add_database_arguments(parser, default_config=_live_config_path())
    preparation_selector = parser.add_mutually_exclusive_group(required=True)
    preparation_selector.add_argument("--date", type=_parse_date)
    preparation_selector.add_argument("--preparation-id")
    parser.add_argument("--budget", nargs="+", required=True)
    parser.add_argument("--analysis-run-id")
    args = parser.parse_args(arguments)
    try:
        settings, database_url = _live_settings(args.config, args.database_url)
        budgets_fen = tuple(_yuan_to_fen(value, parser) for value in args.budget)
        if len(budgets_fen) != len(set(budgets_fen)):
            raise ValueError("budgets must be unique")
        sessions, _, live_repository = _open_live_repositories(
            database_url,
            clock=clock or utc_now,
        )
        preparation_id = args.preparation_id
        if preparation_id is None:
            preparation_ids = live_repository.find_ready_preparation_ids(args.date)
            if not preparation_ids:
                raise ValueError(
                    f"no ready live analysis preparation for UTC date {args.date}"
                )
            if len(preparation_ids) > 1:
                raise ValueError(
                    "multiple ready live analysis preparations match UTC date "
                    f"{args.date}; use --preparation-id"
                )
            preparation_id = preparation_ids[0]
        bundle = live_repository.load_prepared_sources(preparation_id)
        if bundle.preparation.status is not PreparationStatus.ANALYSIS_INPUT_READY:
            raise ValueError("live source preparation is not analysis-input ready")
        execution_time = (clock or utc_now)()
        elo_config = EloBaselineConfig()
        analysis_repository = SqlAlchemyAnalysisRepository(sessions)
        service = RunModelAnalysisService(
            fixture_provider=PreparedLiveFixtureProvider(bundle),
            market_odds_provider=PreparedLiveMarketOddsProvider(bundle),
            sporttery_provider=PreparedLiveSportteryProvider(bundle),
            training_history_provider=NoAvailableLiveTrainingHistoryProvider(),
            repository=analysis_repository,
            settings=settings,
            elo_config=elo_config,
        )
        artifacts = asyncio.run(
            service.run(
                RunModelAnalysisRequest(
                    as_of_at_utc=bundle.preparation.decision_as_of_at_utc,
                    kickoff_from_utc=bundle.preparation.kickoff_from_utc,
                    kickoff_to_utc=bundle.preparation.kickoff_to_utc,
                    budgets_fen=budgets_fen,
                    fusion_policy=FusionPolicyName(settings.analysis.fusion_policy),
                    analysis_run_id=args.analysis_run_id,
                    execution_time_utc=execution_time,
                    allow_partial_inputs=False,
                    expected_match_ids=bundle.preparation.ready_match_ids,
                    competition_id=bundle.competition_id,
                    season_id=bundle.season_id,
                    elo_config=elo_config,
                    live_source_preparation_id=preparation_id,
                    prepared_fixture_observations=tuple(
                        PreparedFixtureObservationRef(
                            match_id=item.match_id,
                            fixture_observation_id=item.fixture_observation_id,
                        )
                        for item in bundle.preparation.matches
                        if item.data_quality.ready
                        and item.fixture_observation_id is not None
                    ),
                )
            )
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(f"Live source preparation: {preparation_id}")
    print(format_analysis(artifacts, analysis_repository.table_counts()))
    return 0


def _validate_historical_archive(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system historical-archive validate",
        description="Validate immutable historical archive files without a database.",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=_historical_archive_path(),
        help=(
            "Archive directory. Defaults to bundled SYNTHETIC ACCEPTANCE DATA; "
            "NOT REAL HISTORICAL PERFORMANCE."
        ),
    )
    parser.add_argument("--config", type=Path, default=_backtest_config_path())
    _add_data_mode_argument(parser)
    args = parser.parse_args(arguments)
    try:
        settings = AppSettings.from_toml(args.config)
        data_mode = _configured_data_mode(args.data_mode, settings)
        summary = HistoricalArchiveService().validate(args.archive, data_mode)
    except (OSError, RuntimeError, ValidationError, ValueError) as error:
        parser.error(str(error))
    print(render_historical_archive_summary(summary))
    return 0


def _import_historical_archive(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system historical-archive import",
        description="Register archive manifests and provenance in the database.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument(
        "--archive",
        type=Path,
        default=_historical_archive_path(),
        help=(
            "Archive directory. Defaults to bundled SYNTHETIC ACCEPTANCE DATA; "
            "NOT REAL HISTORICAL PERFORMANCE."
        ),
    )
    _add_data_mode_argument(parser)
    args = parser.parse_args(arguments)
    try:
        settings, _, repository = _historical_repository(
            args.config,
            args.database_url,
        )
        data_mode = _configured_data_mode(args.data_mode, settings)
        summary = HistoricalArchiveService().register(
            args.archive,
            repository,
            utc_now(),
            data_mode,
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(render_historical_archive_summary(summary))
    return 0


def _list_match_results(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system match-results list",
        description="List the latest visible result for each requested match.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument(
        "--match-id",
        action="append",
        nargs="+",
        required=True,
        metavar="MATCH_ID",
    )
    parser.add_argument("--as-of", type=_parse_utc_datetime, required=True)
    parser.add_argument("--provider-code")
    args = parser.parse_args(arguments)
    match_ids = tuple(item for group in args.match_id for item in group)
    if len(match_ids) != len(set(match_ids)):
        parser.error("match IDs must be unique")
    try:
        _, _, repository = _historical_repository(
            args.config,
            args.database_url,
        )
        results = repository.latest_match_results(
            match_ids,
            args.as_of,
            args.provider_code,
        )
        report = render_match_results(
            match_ids,
            args.as_of,
            results,
            args.provider_code,
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report)
    return 0


def _create_settlement(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system settlement create",
        description="Settle one frozen base portfolio from a point-in-time archive.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument("--portfolio-id", required=True)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--provider-code", required=True)
    parser.add_argument(
        "--evaluation-as-of",
        type=_parse_utc_datetime,
        required=True,
    )
    _add_data_mode_argument(parser)
    args = parser.parse_args(arguments)
    try:
        settings, _, repository = _historical_repository(
            args.config,
            args.database_url,
        )
        data_mode = _configured_data_mode(args.data_mode, settings)
        portfolio = repository.load_base_portfolio(args.portfolio_id)
        if portfolio.analysis_run_id != args.analysis_run_id:
            raise ValueError("portfolio does not belong to the requested AnalysisRun")

        store = LocalArchiveStore(args.archive, data_mode=data_mode)
        result_provider = LocalArchiveHistoricalDataProvider(
            store,
            args.provider_code,
        )
        match_ids = tuple(
            dict.fromkeys(
                leg.match_id
                for ticket in portfolio.tickets
                for leg in ticket.candidate.legs
            )
        )
        if match_ids:
            batch = asyncio.run(
                result_provider.fetch_match_results(
                    MatchResultQuery(
                        match_ids=match_ids,
                        as_of_at_utc=args.evaluation_as_of,
                    )
                )
            )
            repository.append_match_result_batch(batch)
            match_results = batch.results
        else:
            match_results = ()

        previous_portfolios = repository.latest_portfolio_settlements(
            args.analysis_run_id,
            args.evaluation_as_of,
            (portfolio.portfolio_id,),
        )
        if len(previous_portfolios) > 1:
            raise ValueError("portfolio has multiple latest prior settlements")
        previous_portfolio = (
            repository.load_portfolio_settlement(
                previous_portfolios[0].portfolio_settlement_id
            )
            if previous_portfolios
            else None
        )
        previous_tickets = (
            tuple(
                _required_ticket_settlement(repository, settlement_id)
                for settlement_id in previous_portfolio.ticket_settlement_ids
            )
            if previous_portfolio is not None
            else ()
        )
        settlement_result = SettlementService(
            settings.settlement.policy
        ).settle_portfolio(
            SettlementScope.for_analysis_run(args.analysis_run_id),
            portfolio,
            match_results,
            args.evaluation_as_of,
            result_issues=batch.issues if match_ids else (),
            previous_ticket_settlements=previous_tickets,
            supersedes_portfolio_settlement=previous_portfolio,
        )
        for ticket_result in settlement_result.ticket_results:
            if ticket_result.settlement is not None:
                repository.append_ticket_settlement(ticket_result.settlement)
        if settlement_result.portfolio_settlement is not None:
            repository.append_portfolio_settlement(
                settlement_result.portfolio_settlement
            )
        report = render_settlement_result(
            portfolio,
            settlement_result,
            data_mode,
            store.manifests,
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report)
    return 0


def _report_settlement(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system settlement report",
        description="Render persisted settlement lineage and financials.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument("--portfolio-settlement-id", required=True)
    args = parser.parse_args(arguments)
    try:
        _, _, repository = _historical_repository(
            args.config,
            args.database_url,
        )
        portfolio = repository.load_portfolio_settlement(args.portfolio_settlement_id)
        tickets = tuple(
            _required_ticket_settlement(repository, settlement_id)
            for settlement_id in portfolio.ticket_settlement_ids
        )
        report = render_settlement_report(portfolio, tickets)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report)
    return 0


def _run_backtest(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system backtest run",
        description="Run and persist one strict walk-forward strategy.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument(
        "--archive",
        type=Path,
        default=_historical_archive_path(),
        help=(
            "Archive directory. Defaults to bundled SYNTHETIC ACCEPTANCE DATA; "
            "NOT REAL HISTORICAL PERFORMANCE."
        ),
    )
    parser.add_argument(
        "--fixture-config",
        type=Path,
        default=_historical_archive_path() / "acceptance_config.toml",
        help="Fixture plan. Defaults to the bundled synthetic acceptance plan.",
    )
    parser.add_argument(
        "--fusion-policy",
        choices=(
            FusionPolicyName.QUANT_ONLY_V1.value,
            FusionPolicyName.MARKET_QUANT_BLEND_V1.value,
        ),
        required=True,
    )
    parser.add_argument("--backtest-run-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider-code")
    _add_data_mode_argument(parser)
    parser.add_argument("--budget-fen", type=_parse_nonnegative_int)
    parser.add_argument("--min-selection-ev", type=_parse_nonnegative_decimal)
    parser.add_argument("--min-ticket-roi", type=_parse_nonnegative_decimal)
    parser.add_argument("--quant-weight", type=_parse_probability_decimal)
    args = parser.parse_args(arguments)
    try:
        base_settings, resolved_database_url = _historical_settings(
            args.config,
            args.database_url,
        )
        fixture = load_backtest_fixture(args.fixture_config)
        fixture.validate_against_settings(base_settings)
        if (
            args.data_mode is not None
            and HistoricalDataMode(args.data_mode) is not fixture.data_mode
        ):
            raise ValueError(
                "--data-mode must match fixture and config backtest data mode"
            )
        data_mode = fixture.data_mode
        policy = FusionPolicyName(args.fusion_policy)
        fixture_strategy = fixture.strategy(policy)
        quant_weight = (
            fixture_strategy.quant_weight
            if args.quant_weight is None
            else args.quant_weight
        )
        min_selection_ev = (
            fixture.min_selection_ev
            if args.min_selection_ev is None
            else args.min_selection_ev
        )
        min_ticket_roi = (
            fixture.min_ticket_roi
            if args.min_ticket_roi is None
            else args.min_ticket_roi
        )
        budget_fen = fixture.budget_fen if args.budget_fen is None else args.budget_fen
        provider_code = args.provider_code or fixture.provider_code
        settings = fixture.analysis_settings(
            base_settings,
            policy,
            quant_weight=quant_weight,
            min_selection_ev=min_selection_ev,
            min_ticket_roi=min_ticket_roi,
        )

        store = LocalArchiveStore(args.archive, data_mode=data_mode)
        archive_provenance = _backtest_archive_provenance(
            store,
            provider_code,
            data_mode,
        )
        fixture_provider = HistoricalArchiveFixtureProvider(store, provider_code)
        market_provider = HistoricalArchiveMarketOddsProvider(
            store,
            provider_code,
            bookmaker_code=fixture.market_bookmaker_code,
            require_complete=False,
        )
        sporttery_provider = HistoricalArchiveSportteryProvider(store, provider_code)
        quant_provider = HistoricalArchiveQuantProvider(store, provider_code)
        result_provider = LocalArchiveHistoricalDataProvider(store, provider_code)
        validate_backtest_runtime_provenance(
            data_mode,
            {
                role: require_provider_runtime_provenance(provider, role)
                for role, provider in {
                    "fixture": fixture_provider,
                    "market_odds": market_provider,
                    "sporttery": sporttery_provider,
                    "manual_quant": quant_provider,
                    "match_result": result_provider,
                }.items()
            },
        )
        sessions, historical = _open_historical_repository(resolved_database_url)
        HistoricalArchiveService().register(
            args.archive,
            historical,
            utc_now(),
            data_mode,
        )
        analysis = RunAnalysisService(
            fixture_provider,
            market_provider,
            sporttery_provider,
            quant_provider,
            SqlAlchemyAnalysisRepository(sessions),
            settings,
        )
        service = WalkForwardBacktestService(
            analysis,
            result_provider,
            SettlementService(settings.settlement.policy),
        )
        run_id = args.backtest_run_id or new_id()
        request = WalkForwardBacktestRequest(
            backtest_run_id=run_id,
            data_mode=data_mode,
            fusion_policy=policy,
            slates=fixture.plans,
            budget_fen=budget_fen,
            quant_weight=quant_weight,
            min_selection_ev=min_selection_ev,
            min_ticket_roi=min_ticket_roi,
            constraints=fixture.constraints,
            backtest_version=base_settings.backtest.version,
            metrics_config=BacktestMetricsConfig(
                log_loss_epsilon=base_settings.backtest.log_loss_epsilon
            ),
            archive_provenance=archive_provenance,
        )
        result = asyncio.run(service.run(request))
        fixture.validate_result_match_ids(
            tuple(
                tuple(match.match_id for match in slate.analysis_artifacts.matches)
                for slate in result.slate_results
            )
        )
        historical.save_walk_forward_backtest_result(result)
        report = render_backtest_report(
            BacktestReportData(
                backtest_run=result.backtest_run,
                slices=result.backtest_slices,
                metrics=result.metrics,
                expected_match_ids_by_slice=tuple(
                    slate.backtest_slice.expected_match_ids
                    for slate in result.slate_results
                ),
                archive_manifests=store.manifests,
            )
        )
        _write_optional_report(args.output, report)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report)
    if args.output is not None:
        print(f"Report written: {args.output}")
    return 0


def _report_backtest(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system backtest report",
        description="Render the latest persisted aggregate report for a run.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument("--backtest-run-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(arguments)
    try:
        _, sessions, repository = _historical_repository(
            args.config,
            args.database_url,
        )
        report = render_backtest_report(
            _load_backtest_report(
                repository,
                SqlAlchemyAnalysisRepository(sessions),
                args.backtest_run_id,
            )
        )
        _write_optional_report(args.output, report)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report)
    if args.output is not None:
        print(f"Report written: {args.output}")
    return 0


def _compare_backtests(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system backtest compare",
        description="Render a validated side-by-side strategy comparison.",
    )
    _add_database_arguments(parser, default_config=_backtest_config_path())
    parser.add_argument("--left-run-id", required=True)
    parser.add_argument("--right-run-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(arguments)
    if args.left_run_id == args.right_run_id:
        parser.error("left and right backtest run IDs must differ")
    try:
        _, sessions, repository = _historical_repository(
            args.config,
            args.database_url,
        )
        analysis_repository = SqlAlchemyAnalysisRepository(sessions)
        comparison = BacktestReportComparison(
            left=_load_backtest_report(
                repository,
                analysis_repository,
                args.left_run_id,
            ),
            right=_load_backtest_report(
                repository,
                analysis_repository,
                args.right_run_id,
            ),
        )
        report = render_backtest_comparison(comparison)
        _write_optional_report(args.output, report)
    except (
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    print(report)
    if args.output is not None:
        print(f"Report written: {args.output}")
    return 0


def _export_analysis_packet(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system analysis-packet export",
        description="Export a sealed analysis as an offline review packet.",
    )
    _add_database_arguments(parser)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument(
        "--schema-version",
        choices=(
            "ANALYSIS_PACKET_V1",
            "ANALYSIS_PACKET_V2",
            "ANALYSIS_PACKET_V3",
        ),
        default="ANALYSIS_PACKET_V1",
        help="Contract version; V1 remains the compatibility default.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        repository = _review_repository(args.config, args.database_url)
        packet, packet_json = ExportAnalysisPacketService(repository).export(
            args.analysis_run_id,
            args.schema_version,
        )
        write_contract_file(args.output, packet_json)
    except (KeyError, OSError, SQLAlchemyError, ValueError) as error:
        parser.error(str(error))
    print(f"AnalysisPacket {packet.packet_id} exported to {args.output}")
    return 0


def _validate_llm_review(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system llm-review validate",
        description="Validate an offline LLM review without opening a database.",
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        packet, submission, _ = validate_review_files(
            read_contract_file(args.packet),
            read_contract_file(args.review),
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(
        f"LLMReview {submission.schema_version} valid for AnalysisPacket "
        f"{packet.packet_id}"
    )
    return 0


def _import_llm_review(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system llm-review import",
        description="Validate and append an offline LLM review artifact.",
    )
    _add_database_arguments(parser)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        repository = _review_repository(args.config, args.database_url)
        artifact = ImportLLMReviewService(repository).import_review(
            read_contract_file(args.packet),
            read_contract_file(args.review),
        )
    except (KeyError, OSError, SQLAlchemyError, ValueError) as error:
        parser.error(str(error))
    print(f"LLMReviewArtifact imported: {artifact.review_artifact_id}")
    return 0


def _create_fusion_run(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system fusion-run create",
        description="Create an append-only local fusion from an imported review.",
    )
    _add_database_arguments(parser)
    parser.add_argument("--review-artifact-id", required=True)
    args = parser.parse_args(arguments)
    try:
        settings, repository = _post_review_repository(args.config, args.database_url)
        fusion_run = CreateFusionRunService(repository, settings).create(
            args.review_artifact_id
        )
    except (KeyError, OSError, SQLAlchemyError, ValueError) as error:
        parser.error(str(error))
    fallback_count = sum(item.fallback_code is not None for item in fusion_run.results)
    print(
        f"FusionRun created: {fusion_run.fusion_run_id}; "
        f"parent={fusion_run.parent_analysis_run_id}; "
        f"matches={len(fusion_run.results)}; fallbacks={fallback_count}"
    )
    return 0


def _create_portfolio_revision(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system portfolio-revision create",
        description="Recompute immutable decisions from an append-only FusionRun.",
    )
    _add_database_arguments(parser)
    parser.add_argument("--fusion-run-id", required=True)
    args = parser.parse_args(arguments)
    try:
        settings, repository = _post_review_repository(args.config, args.database_url)
        revision = CreatePortfolioRevisionService(repository, settings).create(
            args.fusion_run_id
        )
    except (KeyError, OSError, SQLAlchemyError, ValueError) as error:
        parser.error(str(error))
    print(
        f"PortfolioRevision created: {revision.portfolio_revision_id}; "
        f"parent={revision.parent_analysis_run_id}; fusion={revision.fusion_run_id}"
    )
    for portfolio in revision.portfolios:
        print(
            f"  budget={portfolio.budget_fen}; status={portfolio.status.value}; "
            f"stake={portfolio.total_stake_fen}; cash={portfolio.cash_position.amount_fen}"
        )
    return 0


def _add_database_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_config: Path | None = None,
) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config or _resource_root() / "config" / "mvp.toml",
    )
    parser.add_argument("--database-url")


def _add_data_mode_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-mode",
        choices=tuple(item.value for item in HistoricalDataMode),
        help="Defaults to backtest.data_mode in --config.",
    )


def _backtest_config_path() -> Path:
    return _resource_root() / "config" / "backtest.toml"


def _live_config_path() -> Path:
    return _resource_root() / "config" / "live.toml"


def _historical_archive_path() -> Path:
    return _resource_root() / "data" / "fixtures" / "historical_acceptance"


def _configured_data_mode(
    value: str | None,
    settings: AppSettings,
) -> HistoricalDataMode:
    return (
        HistoricalDataMode(value) if value is not None else settings.backtest.data_mode
    )


def _historical_repository(
    config_path: Path,
    database_url: str | None,
) -> tuple[
    AppSettings,
    sessionmaker[Session],
    SqlAlchemyHistoricalRepository,
]:
    settings, resolved_url = _historical_settings(config_path, database_url)
    sessions, repository = _open_historical_repository(resolved_url)
    return settings, sessions, repository


def _historical_settings(
    config_path: Path,
    database_url: str | None,
) -> tuple[AppSettings, str]:
    settings = AppSettings.from_toml(config_path)
    resolved_url = _resolve_database_url(database_url or settings.database.url)
    if database_url is not None:
        settings = settings.model_copy(
            update={
                "database": settings.database.model_copy(update={"url": resolved_url})
            }
        )
    return settings, resolved_url


def _live_settings(
    config_path: Path,
    database_url: str | None,
) -> tuple[AppSettings, str]:
    settings, resolved_url = _historical_settings(config_path, database_url)
    require_sqlite_database_url(resolved_url)
    if settings.runtime.environment is not RuntimeEnvironment.LIVE:
        raise ValueError("live commands require live runtime configuration")
    return settings, resolved_url


def _open_historical_repository(
    database_url: str,
) -> tuple[sessionmaker[Session], SqlAlchemyHistoricalRepository]:
    upgrade_database(database_url, _resource_root() / "alembic.ini")
    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    return sessions, SqlAlchemyHistoricalRepository(sessions)


def _open_match_identity_repository(
    database_url: str,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> SqlAlchemyMatchIdentityRepository:
    upgrade_database(database_url, _resource_root() / "alembic.ini")
    engine = create_database_engine(database_url)
    return SqlAlchemyMatchIdentityRepository(
        create_session_factory(engine),
        clock=clock,
    )


def _open_live_repositories(
    database_url: str,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> tuple[
    sessionmaker[Session],
    SqlAlchemyMatchIdentityRepository,
    SqlAlchemyLiveSourceRepository,
]:
    upgrade_database(database_url, _resource_root() / "alembic.ini")
    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    return (
        sessions,
        SqlAlchemyMatchIdentityRepository(sessions, clock=clock),
        SqlAlchemyLiveSourceRepository(sessions, clock=clock),
    )


def _print_live_ingestion_summary(summary: SourceIngestionSummary) -> None:
    print(
        f"{summary.source_kind.value} ingestion: {summary.ingestion_id}; "
        f"status={summary.status.value}; inserted={str(summary.inserted).lower()}; "
        f"artifacts={summary.artifact_count}; snapshots={summary.snapshot_count}; "
        f"mappings={summary.mapping_count}; issues={summary.issue_count}; "
        f"consensus={summary.consensus_count}"
    )


def _select_market_capture_request(
    plan: DailySlatePlan,
    request_id: str | None,
) -> DailySlateCaptureRequest:
    requests = plan.capture_plan.market_odds_requests
    if not requests:
        raise ValueError("daily slate plan contains no resolved market-odds targets")
    if request_id is None:
        if len(requests) != 1:
            raise ValueError(
                "daily slate plan has multiple market-odds requests; "
                "use --plan-request-id"
            )
        return requests[0]
    selected = tuple(item for item in requests if item.request_id == request_id)
    if len(selected) != 1:
        raise ValueError(f"unknown daily slate market-odds request: {request_id}")
    return selected[0]


def _required_environment_value(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environ is None else environ
    value = source.get(name)
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("\r", "\n", "\0"))
    ):
        raise ValueError(f"{name} is required and must not contain control characters")
    return value.strip()


def _required_ticket_settlement(
    repository: SqlAlchemyHistoricalRepository,
    settlement_id: str,
) -> Settlement:
    settlement = repository.find_ticket_settlement(settlement_id)
    if settlement is None:
        raise KeyError(f"unknown ticket settlement: {settlement_id}")
    return settlement


def _load_backtest_report(
    repository: SqlAlchemyHistoricalRepository,
    analysis_repository: SqlAlchemyAnalysisRepository,
    backtest_run_id: str,
) -> BacktestReportData:
    run = repository.find_backtest_run_value(backtest_run_id)
    if run is None:
        raise KeyError(f"unknown backtest run: {backtest_run_id}")
    slices = repository.backtest_slice_values(backtest_run_id)
    if not slices:
        raise ValueError("backtest run has no persisted slices")
    final_cutoff = max(item.evaluation_as_of_at_utc for item in slices)
    snapshots = repository.latest_backtest_metric_snapshots(
        backtest_run_id,
        final_cutoff,
    )
    aggregate = tuple(
        item
        for item in snapshots
        if item.metric_scope == "RUN" and item.metric_key == "AGGREGATE"
    )
    if len(aggregate) != 1:
        raise ValueError(
            "backtest run requires exactly one latest aggregate metric snapshot"
        )
    expected_match_ids_by_slice: list[tuple[str, ...]] = []
    for item in slices:
        input_manifest = analysis_repository.load_input_manifest(item.analysis_run_id)
        if input_manifest.manifest_hash != item.decision_input_manifest_hash:
            raise ValueError(
                "backtest slice decision manifest hash conflicts with AnalysisRun"
            )
        expected_match_ids_by_slice.append(
            expected_match_ids_from_analysis_manifest(
                input_manifest.manifest_json,
                item.expected_match_ids,
                item.missing_decision_match_ids,
            )
        )
    manifests: list[HistoricalArchiveManifest] = []
    for provenance in run.archive_provenance:
        manifest = repository.find_historical_archive_manifest(provenance.archive_id)
        if manifest is None:
            raise KeyError(
                f"unknown historical archive import: {provenance.archive_id}"
            )
        if BacktestArchiveProvenance.from_manifest(manifest) != provenance:
            raise ValueError(
                "persisted archive manifest conflicts with backtest provenance: "
                f"{provenance.archive_id}"
            )
        manifests.append(manifest)
    return BacktestReportData(
        backtest_run=run,
        slices=slices,
        metrics=backtest_metrics_value(aggregate[0]),
        expected_match_ids_by_slice=tuple(expected_match_ids_by_slice),
        archive_manifests=tuple(manifests),
    )


def _backtest_archive_provenance(
    store: LocalArchiveStore,
    provider_code: str,
    data_mode: HistoricalDataMode,
) -> tuple[BacktestArchiveProvenance, ...]:
    manifests = store.manifests
    if store.data_mode is not data_mode or any(
        manifest.data_mode is not data_mode for manifest in manifests
    ):
        raise ValueError("backtest archive manifests use another data mode")
    if any(manifest.provider_code != provider_code for manifest in manifests):
        raise ValueError("backtest archive manifests use another provider")
    required_kinds = set(HistoricalArchiveDatasetKind) - {
        HistoricalArchiveDatasetKind.MARKET_ODDS_ISSUES
    }
    if not required_kinds <= {manifest.dataset_kind for manifest in manifests}:
        raise ValueError("backtest archive must cover every historical dataset kind")
    return canonical_archive_provenance(
        tuple(
            BacktestArchiveProvenance.from_manifest(manifest) for manifest in manifests
        )
    )


def _write_optional_report(output: Path | None, report: str) -> None:
    if output is not None:
        output.write_text(report + "\n", encoding="utf-8")


def _parse_utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO datetime: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            f"datetime must be timezone-aware UTC: {value}"
        )
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from error


def _parse_nonnegative_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"invalid decimal: {value}") from error
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError(
            f"decimal must be finite and non-negative: {value}"
        )
    return parsed


def _parse_probability_decimal(value: str) -> Decimal:
    parsed = _parse_nonnegative_decimal(value)
    if parsed > 1:
        raise argparse.ArgumentTypeError(
            f"probability must be between zero and one: {value}"
        )
    return parsed


def _parse_nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value}") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"integer must be non-negative: {value}")
    return parsed


def _parse_positive_int(value: str) -> int:
    parsed = _parse_nonnegative_int(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError(f"integer must be positive: {value}")
    return parsed


def _review_repository(
    config_path: Path,
    database_url: str | None,
) -> SqlAlchemyReviewArtifactRepository:
    settings = AppSettings.from_toml(config_path)
    resolved_url = _resolve_database_url(database_url or settings.database.url)
    upgrade_database(resolved_url, _resource_root() / "alembic.ini")
    engine = create_database_engine(resolved_url)
    return SqlAlchemyReviewArtifactRepository(create_session_factory(engine))


def _post_review_repository(
    config_path: Path,
    database_url: str | None,
) -> tuple[AppSettings, SqlAlchemyPostReviewRepository]:
    settings = AppSettings.from_toml(config_path)
    resolved_url = _resolve_database_url(database_url or settings.database.url)
    upgrade_database(resolved_url, _resource_root() / "alembic.ini")
    engine = create_database_engine(resolved_url)
    repository = SqlAlchemyPostReviewRepository(create_session_factory(engine))
    return settings, repository


def format_analysis(
    artifacts: AnalysisArtifacts,
    table_counts: dict[str, int],
) -> str:
    teams = {team.team_id: team.name for team in artifacts.teams}
    matches = {match.match_id: match for match in artifacts.matches}
    market_by_match = {item.match_id: item for item in artifacts.market_predictions}
    quant_by_match = {item.match_id: item for item in artifacts.quant_predictions}
    final_by_match = {item.match_id: item for item in artifacts.final_predictions}
    evaluation_by_match = {
        item.match_id: item for item in artifacts.quant_model_evaluations
    }
    risk_by_portfolio = {
        item.portfolio_id: item for item in artifacts.portfolio_risk_reports
    }
    lines = [
        "Football System MVP 研究建议（非自动下注）",
        f"AnalysisRun: {artifacts.analysis_run.analysis_run_id}",
        f"As of UTC: {artifacts.analysis_run.as_of_at_utc.isoformat()}",
        f"FusionPolicy: {_analysis_fusion_policy(artifacts)}",
        "",
        f"比赛与概率（{len(artifacts.matches)} 场）",
    ]
    for match in artifacts.matches:
        p_market = market_by_match[match.match_id].probabilities
        quant = quant_by_match.get(match.match_id)
        final = final_by_match.get(match.match_id)
        lines.extend(
            [
                f"- {match.match_id}: {teams[match.home_team_id]} vs {teams[match.away_team_id]}",
                f"  P_market H/D/A: {_probabilities(p_market)}",
            ]
        )
        if quant is None:
            evaluation = evaluation_by_match.get(match.match_id)
            reason = (
                evaluation.unavailable_reason
                if evaluation is not None
                else "MODEL_UNAVAILABLE"
            )
            lines.extend(
                [
                    f"  P_quant: UNAVAILABLE ({reason})",
                    "  P_final: UNAVAILABLE (MODEL_UNAVAILABLE)",
                ]
            )
        else:
            lines.append(f"  P_quant  H/D/A: {_probabilities(quant.probabilities)}")
            lines.append(
                "  P_final  H/D/A: "
                + (
                    _probabilities(final.probabilities)
                    if final is not None
                    else "UNAVAILABLE"
                )
            )

    eligible = [
        item
        for item in artifacts.selection_candidates
        if item.status == CandidateStatus.ELIGIBLE
    ]
    lines.extend(
        [
            "",
            f"Selection EV（合格 {len(eligible)} / 总计 {len(artifacts.selection_candidates)}）",
        ]
    )
    for candidate in eligible:
        match = matches[candidate.match_id]
        lines.append(
            "- "
            f"{teams[match.home_team_id]} vs {teams[match.away_team_id]} "
            f"{candidate.selection.value}: bonus={candidate.fixed_bonus}, "
            f"p={_percent(candidate.probability)}, EV={_percent(candidate.ev)}"
        )

    lines.extend(["", f"简单2串1候选（{len(artifacts.ticket_candidates)} 个）"])
    for index, ticket in enumerate(artifacts.ticket_candidates, start=1):
        leg_text = " + ".join(
            f"{leg.match_id}/{leg.selection.value}" for leg in ticket.legs
        )
        lines.append(
            f"- #{index} {leg_text}: q={_percent(ticket.joint_probability)}, "
            f"单注毛返还={_fen(ticket.gross_payout_fen)}, "
            f"期望利润={_fen(ticket.expected_profit_fen)}, ROI={_percent(ticket.expected_roi)}"
        )

    lines.append("")
    for portfolio in artifacts.portfolios:
        lines.append(
            f"Portfolio 预算={_fen(portfolio.budget_fen)} 状态={portfolio.status.value}"
        )
        lines.append(
            "  StrategyProfile: "
            f"preferred={portfolio.constraints.preferred_max_tickets}, "
            f"absolute={portfolio.constraints.absolute_max_tickets}"
        )
        lines.append(f"  Cash Position: {_fen(portfolio.cash_position.amount_fen)}")
        if portfolio.status == PortfolioStatus.NO_BET:
            lines.append(f"  NO_BET 原因: {portfolio.no_bet_reason.value}")
            _append_risk(lines, risk_by_portfolio[portfolio.portfolio_id])
            continue
        for ticket in portfolio.tickets:
            leg_text = " + ".join(
                f"{leg.match_id}/{leg.selection.value}" for leg in ticket.candidate.legs
            )
            lines.append(
                f"  Ticket {ticket.ticket_no}: {leg_text}, {ticket.multiplier}倍, "
                f"投入={_fen(ticket.stake_fen)}, "
                f"命中毛返还={_fen(ticket.potential_gross_payout_fen)}, "
                f"期望利润={_fen(ticket.expected_profit_fen)}, "
                f"ROI={_percent(ticket.expected_roi)}"
            )
        lines.append(
            f"  总投入={_fen(portfolio.total_stake_fen)}, "
            f"未使用预算={_fen(portfolio.unused_budget_fen)}"
        )
        _append_risk(lines, risk_by_portfolio[portfolio.portfolio_id])

    lines.extend(["", "SQLite 持久化计数"])
    lines.extend(f"- {name}: {count}" for name, count in sorted(table_counts.items()))
    return "\n".join(lines)


def _analysis_fusion_policy(artifacts: AnalysisArtifacts) -> str:
    if artifacts.final_predictions:
        return artifacts.final_predictions[0].fusion_policy.value
    try:
        payload = json.loads(artifacts.analysis_run.config_json)
    except (json.JSONDecodeError, TypeError):
        return "UNAVAILABLE"
    request = payload.get("request") if isinstance(payload, dict) else None
    value = request.get("fusion_policy") if isinstance(request, dict) else None
    return value if isinstance(value, str) else "UNAVAILABLE"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic football analysis, historical archive workflows, "
            "settlement, and walk-forward backtests."
        ),
        epilog=(
            "Live commands: live ingest-fixtures; live ingest-market-odds; "
            "live ingest-sporttery; live reconcile; live import-identity-review; "
            "live prepare-analysis; live run-analysis. "
            "Historical/backtest commands: historical-archive validate; "
            "historical-archive import; match-results list; settlement create; "
            "settlement report; backtest run; backtest report; backtest compare. "
            "Offline review commands: analysis-packet export; "
            "llm-review validate/import; fusion-run create; portfolio-revision create."
        ),
    )
    parser.add_argument(
        "--config", type=Path, default=_resource_root() / "config" / "mvp.toml"
    )
    parser.add_argument("--database-url")
    parser.add_argument("--budget-yuan", nargs="+", default=("100", "200"))
    parser.add_argument(
        "--fusion-policy", choices=[item.value for item in FusionPolicyName]
    )
    parser.add_argument("--min-selection-ev")
    parser.add_argument("--min-ticket-roi")
    parser.add_argument("--analysis-run-id")
    parser.add_argument(
        "--no-bet-demo",
        action="store_true",
        help="Raise the selection threshold to produce a deterministic NO_BET example.",
    )
    return parser


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _yuan_to_fen(value: str, parser: argparse.ArgumentParser) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        parser.error(f"invalid budget: {value}")
    if not amount.is_finite():
        parser.error(f"invalid budget: {value}")
    fen = amount * Decimal(100)
    if amount < 0 or fen != fen.to_integral_value():
        parser.error(f"budget must be a non-negative amount in cents: {value}")
    return int(fen)


def _optional_decimal(
    value: str | None,
    parser: argparse.ArgumentParser,
) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        parser.error(f"invalid decimal value: {value}")
    if not amount.is_finite() or amount < 0:
        parser.error(f"decimal value must be finite and non-negative: {value}")
    return amount


def _resolve_database_url(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return database_url
    database_path = Path(database_url.removeprefix(prefix))
    if database_path.is_absolute():
        return database_url
    return f"{prefix}{(_resource_root() / database_path).resolve().as_posix()}"


def _resource_root() -> Path:
    module_path = Path(__file__).resolve()
    source_root = module_path.parents[3]
    if (source_root / "config" / "mvp.toml").is_file():
        return source_root
    adjacent_resources = module_path.parents[2] / "football_system_resources"
    if (adjacent_resources / "config" / "mvp.toml").is_file():
        return adjacent_resources
    try:
        distribution_root = Path(
            distribution("football-system").locate_file("football_system_resources")
        )
    except PackageNotFoundError as error:
        raise RuntimeError("football-system resources are not installed") from error
    candidates = (distribution_root, Path(sys.prefix) / "football_system_resources")
    for installed_root in candidates:
        if (installed_root / "config" / "mvp.toml").is_file():
            return installed_root
    raise RuntimeError("football-system package resources are incomplete")


def _probabilities(value: object) -> str:
    return "/".join(
        _percent(probability)
        for probability in (value.home_win, value.draw, value.away_win)
    )


def _percent(value: Decimal) -> str:
    return f"{value * Decimal(100):.2f}%"


def _fen(value: int | Decimal) -> str:
    return f"{Decimal(value) / Decimal(100):.2f}元"


def _append_risk(lines: list[str], report: object) -> None:
    cash_ratio = "N/A" if report.cash_ratio is None else _percent(report.cash_ratio)
    lines.append(
        "  Portfolio Risk: "
        f"cash={cash_ratio}, stake_at_risk={_fen(report.total_stake_at_risk_fen)}, "
        f"max_match={_fen(report.max_match_exposure_fen)}"
    )
    for exposure in sorted(
        report.match_exposures,
        key=lambda item: (-item.exposed_stake_fen, item.match_id),
    ):
        ratio = (
            "N/A" if exposure.budget_ratio is None else _percent(exposure.budget_ratio)
        )
        lines.append(
            f"    Exposure {exposure.match_id}: {_fen(exposure.exposed_stake_fen)} "
            f"({ratio} of budget)"
        )
    for result in report.stress_results:
        if result.is_complete:
            financial = (
                f"P/L={_fen(result.profit_loss_fen)}, "
                f"recovery={_percent(result.capital_recovery_ratio)}"
                if result.capital_recovery_ratio is not None
                else f"P/L={_fen(result.profit_loss_fen)}, recovery=N/A"
            )
        else:
            financial = (
                f"capital_range={_fen(result.minimum_ending_capital_fen)}"
                f"..{_fen(result.maximum_ending_capital_fen)}"
            )
        lines.append(
            f"    Stress {result.scenario_key}: "
            f"exposed={_fen(result.scenario_exposed_stake_fen)}, {financial}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
