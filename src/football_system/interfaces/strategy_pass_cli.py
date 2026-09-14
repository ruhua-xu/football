"""Opt-in strategy commands. Prices, probabilities, gates and budgets come from DB parents."""

import argparse
from datetime import datetime
from pathlib import Path
import sys

from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from football_system.application.strategy_pass import StrategyPassService
from football_system.domain.archive import canonical_json
from football_system.domain.settlement import MatchSettlementIssue
from football_system.domain.strategy_pass import StrategyProfileV1, TicketRoleRequestV1
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.database.strategy_pass_repository import (
    SqlAlchemyStrategyPassRepository,
)
from football_system.infrastructure.files.review_bridge import read_contract_file
from football_system.infrastructure.files.strategy_pass import write_strategy_file


def dispatch_strategy_pass(arguments):
    parser = argparse.ArgumentParser(
        prog="football-system strategy-pass",
        description="Versioned THREE_WAY 2X1 / 3X4 / 4X11 strategy and BACKTEST settlement",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    profile_parser = commands.add_parser(
        "profile", help="print the versioned default profile or its schema"
    )
    profile_parser.add_argument("--print-schema", action="store_true")
    profile_parser.add_argument("--output", type=Path)
    for name in ("build", "show", "settle", "settlement-show"):
        p = commands.add_parser(name)
        p.add_argument("--database-url", required=True)
        p.add_argument("--output", type=Path)
        p.add_argument("--evidence-root", type=Path)
        p.add_argument("--authority-pins", type=Path)
        p.add_argument("--operator-id")
        if name == "build":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--analysis-run-id")
            group.add_argument("--portfolio-revision-id")
            p.add_argument(
                "--budget-fen",
                required=True,
                type=int,
                help="select an existing parent budget; cannot override it",
            )
            p.add_argument("--profile", type=Path)
            p.add_argument(
                "--roles",
                type=Path,
                help="JSON array of qualified role requests; never changes eligibility",
            )
        elif name == "settlement-show":
            p.add_argument("--settlement-id", required=True)
        else:
            p.add_argument("--plan-id", required=True)
        if name == "settle":
            p.add_argument(
                "--result-id",
                action="append",
                default=[],
                help="repeat for exact persisted normalized MatchResult IDs",
            )
            p.add_argument(
                "--as-of",
                required=True,
                help="timezone-aware backtest settlement cutoff",
            )
            p.add_argument(
                "--issues",
                type=Path,
                help="JSON array; unsupported cancellation/void stays unsettled",
            )
            p.add_argument("--supersedes-settlement-id")
    args = parser.parse_args(arguments)
    engine = None
    try:
        if args.command == "profile":
            result = (
                StrategyProfileV1.model_json_schema()
                if args.print_schema
                else StrategyProfileV1()
            )
        else:
            auditor = None
            supplied = (args.evidence_root, args.authority_pins, args.operator_id)
            if any(supplied) and not all(supplied):
                raise ValueError(
                    "audit wiring requires evidence-root, authority-pins and operator-id together"
                )
            from football_system.interfaces.cli import _resource_root
            from football_system.infrastructure.database.session import (
                require_sqlite_database_url,
            )

            url = require_sqlite_database_url(args.database_url)
            if (
                args.output is not None
                and url.database
                and args.output.resolve() == Path(url.database).resolve()
            ):
                raise ValueError("output must not alias the database")
            if args.command in {"build", "settle"}:
                upgrade_database(args.database_url, _resource_root() / "alembic.ini")
            engine = create_database_engine(args.database_url)
            sessions = create_session_factory(engine)
            if all(supplied):
                from football_system.interfaces.production_quant_cli import (
                    AuthorityPinsV1,
                    LocalTrainingEvidence,
                    production_inference_context,
                )

                pins = AuthorityPinsV1.model_validate_json(
                    read_contract_file(args.authority_pins)
                )
                evidence = LocalTrainingEvidence(
                    args.evidence_root, trusted_authorities=pins.trusted_authorities
                )
                *_, auditor = production_inference_context(
                    sessions, evidence=evidence, operator_id=args.operator_id
                )
            repo = SqlAlchemyStrategyPassRepository(sessions, audit_repository=auditor)
            service = StrategyPassService(repo)
            if args.command == "build":
                profile = (
                    StrategyProfileV1.model_validate_json(
                        read_contract_file(args.profile)
                    )
                    if args.profile
                    else StrategyProfileV1()
                )
                roles = (
                    TypeAdapter(tuple[TicketRoleRequestV1, ...]).validate_json(
                        read_contract_file(args.roles)
                    )
                    if args.roles
                    else ()
                )
                result = service.create(
                    "ANALYSIS_RUN" if args.analysis_run_id else "PORTFOLIO_REVISION",
                    args.analysis_run_id or args.portfolio_revision_id,
                    args.budget_fen,
                    profile=profile,
                    role_requests=roles,
                )
            elif args.command == "show":
                result = repo.load_plan(args.plan_id)
            elif args.command == "settlement-show":
                result = repo.load_settlement(args.settlement_id)
            else:
                issues = (
                    TypeAdapter(tuple[MatchSettlementIssue, ...]).validate_json(
                        read_contract_file(args.issues)
                    )
                    if args.issues
                    else ()
                )
                result = service.settle(
                    args.plan_id,
                    tuple(args.result_id),
                    datetime.fromisoformat(args.as_of),
                    issues=issues,
                    supersedes_settlement_id=args.supersedes_settlement_id,
                )
        content = canonical_json(result)
        if args.output:
            write_strategy_file(args.output, content)
            print(f"strategy-pass {args.command}: {args.output}")
        else:
            print(content)
        return 0
    except SQLAlchemyError as error:
        print(
            f"strategy-pass database operation failed ({type(error).__name__})",
            file=sys.stderr,
        )
        return 1
    except (ValueError, KeyError, OSError) as error:
        print(f"strategy-pass failed: {error}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
