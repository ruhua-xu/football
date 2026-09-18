"""Thin offline CLI over sealed V2 source IDs; no probability/price/budget overrides."""

import argparse
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError

from football_system.application.return_distribution import (
    EvaluateReturnRequestV1, OptimizeReturnRequestV1, ReturnDistributionService, return_report,
)
from football_system.domain.archive import canonical_json
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory, require_sqlite_database_url
from football_system.infrastructure.files.return_distribution import strict_return_json, verify_resource_configuration
from football_system.infrastructure.files.strategy_pass import write_strategy_file


def dispatch_return_distribution(arguments):
    parser = argparse.ArgumentParser(prog="football-system return-distribution")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("evaluate", "optimize", "show"):
        p = subs.add_parser(name)
        p.add_argument("--database-url", required=True)
        p.add_argument("--output", type=Path)
        if name == "show":
            p.add_argument("--artifact-id", required=True)
        else:
            p.add_argument("--input", type=Path, required=True)
    for name in ("policy", "profile"):
        p = subs.add_parser(name)
        p.add_argument("--output", type=Path)
    args = parser.parse_args(arguments)
    engine = None
    try:
        from football_system.interfaces.cli import _resource_root
        root = _resource_root()
        policy, profile = verify_resource_configuration(root)
        if args.command in {"policy", "profile"}:
            result = policy if args.command == "policy" else profile
        else:
            database = require_sqlite_database_url(args.database_url).database
            if args.output is not None and database and args.output.resolve() == Path(database).resolve():
                raise ValueError("output must not alias the database")
            request = None
            if args.command != "show":
                with args.input.open("rb") as stream:
                    raw = stream.read(4*1024*1024+1)
                cls = EvaluateReturnRequestV1 if args.command == "evaluate" else OptimizeReturnRequestV1
                request = cls.model_validate(strict_return_json(raw))
                upgrade_database(args.database_url, root / "alembic.ini")
            engine = create_database_engine(args.database_url)
            repo = SqlAlchemyReturnDistributionRepository(create_session_factory(engine))
            service = ReturnDistributionService(repo, policy, profile)
            artifact = repo.load(args.artifact_id) if args.command == "show" else getattr(service, args.command)(request)
            result = return_report(artifact)
        content = canonical_json(result)
        if args.output is not None:
            write_strategy_file(args.output, content)
            print(f"return-distribution {args.command}: {args.output}")
        else:
            print(content)
        return 0
    except SQLAlchemyError as error:
        print(f"return-distribution database gate failed ({type(error).__name__})", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError) as error:
        print(f"return-distribution failed: {error}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
