from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from football_system.application.models import AnalysisArtifacts
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
from football_system.config import AppSettings
from football_system.domain.betting import CandidateStatus, PortfolioStatus
from football_system.domain.prediction import FusionPolicyName
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
from football_system.infrastructure.files.review_bridge import (
    read_contract_file,
    write_contract_file,
)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    arguments = list(argv) if argv is not None else sys.argv[1:]
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


def _export_analysis_packet(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="football-system analysis-packet export",
        description="Export a sealed analysis as an offline review packet.",
    )
    _add_database_arguments(parser)
    parser.add_argument("--analysis-run-id", required=True)
    parser.add_argument(
        "--schema-version",
        choices=("ANALYSIS_PACKET_V1", "ANALYSIS_PACKET_V2"),
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


def _add_database_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", type=Path, default=_resource_root() / "config" / "mvp.toml"
    )
    parser.add_argument("--database-url")


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
    risk_by_portfolio = {
        item.portfolio_id: item for item in artifacts.portfolio_risk_reports
    }
    lines = [
        "Football System MVP 研究建议（非自动下注）",
        f"AnalysisRun: {artifacts.analysis_run.analysis_run_id}",
        f"As of UTC: {artifacts.analysis_run.as_of_at_utc.isoformat()}",
        f"FusionPolicy: {artifacts.final_predictions[0].fusion_policy.value}",
        "",
        f"比赛与概率（{len(artifacts.matches)} 场）",
    ]
    for match in artifacts.matches:
        p_market = market_by_match[match.match_id].probabilities
        p_quant = quant_by_match[match.match_id].probabilities
        p_final = final_by_match[match.match_id].probabilities
        lines.extend(
            [
                f"- {match.match_id}: {teams[match.home_team_id]} vs {teams[match.away_team_id]}",
                f"  P_market H/D/A: {_probabilities(p_market)}",
                f"  P_quant  H/D/A: {_probabilities(p_quant)}",
                f"  P_final  H/D/A: {_probabilities(p_final)}",
            ]
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic football analysis against Mock data.",
        epilog=(
            "Offline commands: analysis-packet export; "
            "llm-review validate/import; fusion-run create; "
            "portfolio-revision create"
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
