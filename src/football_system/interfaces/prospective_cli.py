"""Offline, trusted-local-time prospective commands. No event-time overrides."""

import argparse
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError

from football_system.application.market_v2 import MarketExpansionService
from football_system.application.prospective import ProspectiveService
from football_system.application.prospective_requests import (
    CloseEpochRequestV1, EpochRequestV1, EvidenceImportRequestV1, LockWorkflowRequestV1,
    PrepareProspectiveRequestV1, ReportProspectiveRequestV1, ResultImportRequestV1, SettleProspectiveRequestV1,
)
from football_system.application.return_distribution import ReturnDistributionService
from football_system.domain.archive import canonical_json
from football_system.domain.prospective_evidence import provider_capability
from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory, require_sqlite_database_url
from football_system.infrastructure.files.prospective import SystemProspectiveClock, verified_manual_bytes, verify_prospective_configuration
from football_system.infrastructure.files.return_distribution import strict_return_json, verify_resource_configuration
from football_system.infrastructure.files.strategy_pass import write_strategy_file

COMMANDS = {"epoch": (EpochRequestV1, "create_epoch"), "evidence-import": (EvidenceImportRequestV1, "import_evidence"),
    "prepare": (PrepareProspectiveRequestV1, "prepare"), "lock": (LockWorkflowRequestV1, "lock"),
    "result-import": (ResultImportRequestV1, "import_result"), "settle": (SettleProspectiveRequestV1, "settle"),
    "report": (ReportProspectiveRequestV1, "report"), "epoch-close": (CloseEpochRequestV1, "close_epoch")}


def read_request(path):
    with path.open("rb") as stream:
        raw = stream.read(4*1024*1024+1)
    return strict_return_json(raw)


def dispatch_prospective(arguments):
    parser = argparse.ArgumentParser(prog="football-system prospective")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in (*COMMANDS, "show", "audit"):
        p = subs.add_parser(name)
        p.add_argument("--database-url", required=True)
        p.add_argument("--output", type=Path)
        if name in COMMANDS:
            p.add_argument("--input", type=Path, required=True)
        else:
            p.add_argument("--artifact-id", required=True)
        if name in {"evidence-import", "result-import"}:
            p.add_argument("--evidence-root", type=Path, required=True)
        if name == "prepare":
            p.add_argument("--packet-dir", type=Path, required=True)
        if name == "lock":
            p.add_argument("--review", type=Path)
            p.add_argument("--reasons", type=Path)
    subs.add_parser("policy")
    p = subs.add_parser("capability")
    p.add_argument("--provider", required=True)
    p.add_argument("--category", required=True)
    args = parser.parse_args(arguments)
    engine = None
    try:
        from football_system.interfaces.cli import _resource_root
        root = _resource_root()
        policy, objective = verify_resource_configuration(root)
        prospective_policy = verify_prospective_configuration(root)
        clock = SystemProspectiveClock()
        if args.command == "policy":
            result = prospective_policy
        elif args.command == "capability":
            result = provider_capability(args.provider, args.category, clock.now())
        else:
            database = require_sqlite_database_url(args.database_url).database
            protected = {Path(database).resolve()} if database else set()
            for key in ("input", "review", "reasons"):
                path = getattr(args, key, None)
                if path:
                    protected.add(path.resolve())
            outputs = [args.output] if args.output else []
            if args.command == "prepare":
                outputs += [args.packet_dir / "analysis_packet.json", args.packet_dir / "analysis_packet.md"]
            if len({p.resolve() for p in outputs}) != len(outputs) or any(p.resolve() in protected for p in outputs):
                raise ValueError("output must not alias database, input or another output")
            request = None
            if args.command in COMMANDS:
                request = COMMANDS[args.command][0].model_validate(read_request(args.input))
                if args.command in {"evidence-import", "result-import"}:
                    claim = request.evidence if args.command == "evidence-import" else request.result
                    if args.output and args.output.resolve() == (args.evidence_root / claim.source_file).resolve():
                        raise ValueError("output must not alias manual source")
                upgrade_database(args.database_url, root / "alembic.ini")
            engine = create_database_engine(args.database_url)
            sessions = create_session_factory(engine)
            repo = SqlAlchemyProspectiveRepository(sessions, clock=clock, policy=prospective_policy)
            service = ProspectiveService(repo, MarketExpansionService(SqlAlchemyMultiMarketRepository(sessions)),
                ReturnDistributionService(SqlAlchemyReturnDistributionRepository(sessions), policy, objective),
                verify_manual=lambda claim: verified_manual_bytes(args.evidence_root, claim, clock))
            if args.command == "show":
                artifact = repo.load(args.artifact_id)
                result = repo.show(args.artifact_id) if artifact.schema_version == "PROSPECTIVE_RUN_V1" else artifact
            elif args.command == "audit":
                result = repo.audit(args.artifact_id)
            elif args.command == "lock":
                if bool(args.review) != bool(args.reasons):
                    raise ValueError("external review and correction-reasons sidecar must be supplied together")
                review_bytes = None
                if args.review:
                    with args.review.open("rb") as stream:
                        review_bytes = stream.read(4*1024*1024+1)
                result = service.lock(request, review_bytes=review_bytes, reasons=read_request(args.reasons) if args.reasons else None)
            else:
                result = getattr(service, COMMANDS[args.command][1])(request)
                if args.command == "prepare" and result.packet is not None:
                    packet, markdown = service.packet_files(result)
                    write_strategy_file(args.packet_dir / "analysis_packet.json", packet)
                    write_strategy_file(args.packet_dir / "analysis_packet.md", markdown)
            if args.output:
                write_strategy_file(args.output, canonical_json(result))
                print(f"prospective {args.command}: {args.output}")
                return 0
        print(canonical_json(result))
        return 0
    except SQLAlchemyError as error:
        print(f"prospective database gate failed ({type(error).__name__})", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(f"prospective failed: {error}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
