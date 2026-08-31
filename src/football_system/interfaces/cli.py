from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from football_system.application.models import AnalysisArtifacts
from football_system.application.run_analysis import RunAnalysisRequest, RunAnalysisService
from football_system.config import AppSettings
from football_system.domain.betting import CandidateStatus, PortfolioStatus
from football_system.domain.prediction import FusionPolicyName
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.providers.mock.dataset import MockDataset
from football_system.infrastructure.providers.mock.fixtures import MockFixtureProvider
from football_system.infrastructure.providers.mock.manual_quant import MockManualQuantProvider
from football_system.infrastructure.providers.mock.market_odds import MockMarketOddsProvider
from football_system.infrastructure.providers.mock.sporttery import MockSportteryProvider


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    parser = _build_parser()
    args = parser.parse_args(argv)
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
        fusion_policy=FusionPolicyName(args.fusion_policy or settings.analysis.fusion_policy),
        min_selection_ev=min_selection_ev,
        min_ticket_roi=min_ticket_roi,
        analysis_run_id=args.analysis_run_id,
    )
    artifacts = asyncio.run(service.run(request))
    print(format_analysis(artifacts, repository.table_counts()))
    return 0


def format_analysis(
    artifacts: AnalysisArtifacts,
    table_counts: dict[str, int],
) -> str:
    teams = {team.team_id: team.name for team in artifacts.teams}
    matches = {match.match_id: match for match in artifacts.matches}
    market_by_match = {item.match_id: item for item in artifacts.market_predictions}
    quant_by_match = {item.match_id: item for item in artifacts.quant_predictions}
    final_by_match = {item.match_id: item for item in artifacts.final_predictions}
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
    lines.extend(["", f"Selection EV（合格 {len(eligible)} / 总计 {len(artifacts.selection_candidates)}）"])
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
        if portfolio.status == PortfolioStatus.NO_BET:
            lines.append(f"  NO_BET 原因: {portfolio.no_bet_reason.value}")
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

    lines.extend(["", "SQLite 持久化计数"])
    lines.extend(f"- {name}: {count}" for name, count in sorted(table_counts.items()))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic football analysis MVP against Mock data."
    )
    parser.add_argument(
        "--config", type=Path, default=_resource_root() / "config" / "mvp.toml"
    )
    parser.add_argument("--database-url")
    parser.add_argument("--budget-yuan", nargs="+", default=("100", "200"))
    parser.add_argument("--fusion-policy", choices=[item.value for item in FusionPolicyName])
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
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "config" / "mvp.toml").is_file():
        return source_root
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


if __name__ == "__main__":
    raise SystemExit(main())
