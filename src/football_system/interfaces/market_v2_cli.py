"""Versioned offline market use cases; no market/settlement rules in CLI."""

import argparse
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError

from football_system.application.market_v2 import (
    BuildMarketUnitRequestV1,
    BuildMultiAnalysisRequestV1,
    ConsensusRequestV2,
    FusionRequestV4,
    GoalCohortRequestV1,
    LegacyInputRequestV1,
    MarketExpansionService,
    PacketRequestV4,
    PlanRequestV2,
    SettleRequestV2,
    TrainGoalsRequestV1,
)
from football_system.domain.archive import canonical_json
from football_system.domain.goal_model import PoissonGoalsConfigV1
from football_system.domain.market_v2 import MarketProbabilityDistributionV1
from football_system.domain.review_v4 import AnalysisPacketV4, LLMReviewV4, MAX_V4_BYTES
from football_system.domain.services.review_v4 import parse_v4_json, validate_v4_files
from football_system.domain.strategy_pass_v2 import StrategyProfileV2
from football_system.infrastructure.database.market_v2_repository import (
    SqlAlchemyMultiMarketRepository,
)
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.files.strategy_pass import write_strategy_file
from football_system.infrastructure.providers.exact_market_fixture import (
    ExactMarketFixtureV1,
    parse_exact_market_fixture,
)

REQUESTS = {
    "snapshot-import": ExactMarketFixtureV1,
    "cohort-admit": GoalCohortRequestV1,
    "train-goals": TrainGoalsRequestV1,
    "consensus": ConsensusRequestV2,
    "legacy-input": LegacyInputRequestV1,
    "build-unit": BuildMarketUnitRequestV1,
    "build-analysis": BuildMultiAnalysisRequestV1,
    "packet-export": PacketRequestV4,
    "fusion": FusionRequestV4,
    "plan": PlanRequestV2,
    "settle": SettleRequestV2,
}
METHODS = {
    "train-goals": "train_goals",
    "legacy-input": "legacy_input",
    "build-unit": "build_unit",
    "build-analysis": "build_analysis",
    "packet-export": "packet",
}
SCHEMAS = {
    "packet": AnalysisPacketV4,
    "review": LLMReviewV4,
    "distribution": MarketProbabilityDistributionV1,
    "profile": StrategyProfileV2,
    "poisson-config": PoissonGoalsConfigV1,
    **REQUESTS,
}


def read_input(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_V4_BYTES + 1)
    if len(raw) > MAX_V4_BYTES:
        raise ValueError("input exceeds 4 MiB bound")
    return raw


def dispatch_market_v2(arguments):
    parser = argparse.ArgumentParser(
        prog="football-system market-v2",
        description="Offline multi-market / V4 / simple multiple candidate",
    )
    subs = parser.add_subparsers(dest="command", required=True)
    schema = subs.add_parser("schema")
    schema.add_argument("kind", choices=SCHEMAS)
    for name in ("profile", "poisson-config"):
        p = subs.add_parser(name)
        p.add_argument("--output", type=Path)
    for name in (*REQUESTS, "show", "review-validate", "review-import"):
        p = subs.add_parser(name)
        if name != "review-validate":
            p.add_argument("--database-url", required=True)
        p.add_argument("--output", type=Path)
        if name in {"review-validate", "review-import"}:
            p.add_argument("--packet", type=Path, required=True)
            p.add_argument("--review", type=Path, required=True)
        elif name == "show":
            p.add_argument("--artifact-id", required=True)
        else:
            p.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(arguments)
    engine = None
    try:
        if args.command == "schema":
            result = SCHEMAS[args.kind].model_json_schema()
        elif args.command in {"profile", "poisson-config"}:
            result = SCHEMAS[args.command]()
        elif args.command == "review-validate":
            packet, review = validate_v4_files(
                read_input(args.packet), read_input(args.review)
            )
            result = dict(
                status="VALID",
                packet_id=packet.artifact_id,
                market_review_count=len(review.market_reviews),
            )
        else:
            from football_system.interfaces.cli import _resource_root
            from football_system.infrastructure.database.session import (
                require_sqlite_database_url,
            )

            database = require_sqlite_database_url(args.database_url).database
            if (
                args.output is not None
                and database
                and args.output.resolve() == Path(database).resolve()
            ):
                raise ValueError("output must not alias the database")

            request = None
            raw = None
            if args.command in REQUESTS:
                raw = read_input(args.input)
                request = REQUESTS[args.command].model_validate(parse_v4_json(raw))
            if args.command != "show":
                upgrade_database(args.database_url, _resource_root() / "alembic.ini")
            engine = create_database_engine(args.database_url)
            repo = SqlAlchemyMultiMarketRepository(create_session_factory(engine))
            service = MarketExpansionService(
                repo, exact_market_adapter=parse_exact_market_fixture
            )
            if args.command == "show":
                result = repo.load(args.artifact_id)
            elif args.command == "review-import":
                result = service.review_import(
                    read_input(args.packet), read_input(args.review)
                )
            elif args.command == "snapshot-import":
                result = service.snapshot_import(raw)
            elif args.command == "cohort-admit":
                result = service.cohort_admit(raw)
            else:
                result = getattr(service, METHODS.get(args.command, args.command))(
                    request
                )
        content = canonical_json(result)
        if getattr(args, "output", None):
            write_strategy_file(args.output, content)
            print(f"market-v2 {args.command}: {args.output}")
        else:
            print(content)
        return 0
    except SQLAlchemyError as error:
        print(
            f"multi-market database gate failed ({type(error).__name__})",
            file=sys.stderr,
        )
        return 1
    except (ValueError, KeyError, OSError) as error:
        print(f"market-v2 failed: {error}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
