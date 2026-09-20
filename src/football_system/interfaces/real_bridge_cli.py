"""Offline real bridge entry point. Trusted event clocks have no CLI override."""

import argparse
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.application.real_bridge import RealProspectiveDecisionAdapterV1
from football_system.domain.archive import canonical_json
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory, require_sqlite_database_url
from football_system.infrastructure.files.return_distribution import strict_return_json
from football_system.infrastructure.files.strategy_pass import write_strategy_file
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.infrastructure.database.real_bridge_sources import ExistingPinnedModelAccess


def dispatch_real_bridge(arguments):
    parser = argparse.ArgumentParser(prog="football-system real-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (*REQUEST_TYPES, "show", "audit", "packet"):
        command = commands.add_parser(name)
        command.add_argument("--database-url", required=True)
        command.add_argument("--evidence-root", type=Path, required=True)
        command.add_argument("--output", type=Path)
        command.add_argument("--authority-pins",type=Path)
        command.add_argument("--operator-id")
        if name=="packet":
            command.add_argument("--markdown",type=Path)
        if name in REQUEST_TYPES:
            command.add_argument("--input", type=Path, required=True)
        else:
            command.add_argument("--artifact-id", required=True)
    args = parser.parse_args(arguments)
    engine = None
    try:
        url = require_sqlite_database_url(args.database_url)
        if url.drivername not in {"sqlite","sqlite+pysqlite"} or url.query or any((url.username,url.password,url.host,url.port)):
            raise ValueError("PLAIN_LOCAL_SQLITE_FILE_REQUIRED")
        database = url.database
        if not database or not Path(database).is_file():
            raise ValueError("REAL_BRIDGE_REQUIRES_EXISTING_MIGRATED_DATABASE")
        protected = {Path(database).resolve()}
        if getattr(args, "input", None):
            protected.add(args.input.resolve())
        if args.authority_pins:
            protected.add(args.authority_pins.resolve())
        if args.output and args.output.resolve() in protected:
            raise ValueError("OUTPUT_ALIASES_SOURCE_OR_DATABASE")
        engine = create_database_engine(args.database_url)
        sessions=create_session_factory(engine)
        with sessions() as session:
            if session.scalar(text("SELECT version_num FROM alembic_version"))!="6c859ab273fe":
                raise ValueError("REAL_BRIDGE_MIGRATION_HEAD_REQUIRED")
        access=None
        if args.authority_pins:
            from football_system.interfaces.production_quant_cli import AuthorityPinsV1, production_inference_context
            if not args.operator_id:
                raise ValueError("OPERATOR_ID_REQUIRED_FOR_MODEL_AUTHORITY")
            with args.authority_pins.open("rb") as stream:
                pins=AuthorityPinsV1.model_validate(strict_return_json(stream.read(4*1024*1024+1)))
            evidence=BridgeEvidence(args.evidence_root,trusted_authorities=pins.trusted_authorities,max_bytes=4*1024*1024)
            *_,inference,_auditor=production_inference_context(sessions,evidence=evidence,operator_id=args.operator_id)
            access=ExistingPinnedModelAccess(inference)
        repository = SqlAlchemyRealBridgeRepository(sessions, evidence_root=args.evidence_root,model_access=access)
        adapter=RealProspectiveDecisionAdapterV1(repository)
        if args.command == "audit":
            result = repository.audit(args.artifact_id)
        elif args.command in {"show", "packet"}:
            result = repository.load(args.artifact_id)
            if args.command == "packet":
                if result.schema_version != "REAL_PROSPECTIVE_RUN_V1" or result.packet is None:
                    raise ValueError("READY_REAL_RUN_REQUIRED")
                raw,markdown=adapter.packet_files(result.artifact_id)
                result = strict_return_json(raw.encode())
                if args.markdown:
                    if args.markdown.resolve() in protected or (args.output and args.markdown.resolve()==args.output.resolve()):
                        raise ValueError("OUTPUT_ALIASES_SOURCE_OR_DATABASE")
                    write_strategy_file(args.markdown,markdown)
        else:
            with args.input.open("rb") as stream:
                raw = stream.read(4*1024*1024+1)
            request = REQUEST_TYPES[args.command].model_validate(strict_return_json(raw))
            proof=getattr(request,"evidence_file",getattr(request,"source_file",None))
            if args.command=="result-import":
                proof=request.result.source_file
            if proof and args.output and args.output.resolve()==(args.evidence_root/proof).resolve():
                raise ValueError("OUTPUT_ALIASES_PROOF_FILE")
            result = adapter.execute(args.command, request)
        if args.output:
            write_strategy_file(args.output, canonical_json(result))
        else:
            print(canonical_json(result))
        return 0
    except (SQLAlchemyError, ValueError, KeyError, OSError, TypeError) as error:
        # ValidationError can embed source input; do not print arbitrary request bytes.
        print(f"real bridge gate failed ({type(error).__name__})", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
