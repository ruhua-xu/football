"""Local-only explicit OpenFootball production operations, with external reviews.

No implicit migration, authority creation, review approval, target discovery,
provider HTTP, real preparation, model pin or real-bridge write command.
"""

import argparse
import json
from pathlib import Path

from sqlalchemy.engine import URL

from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_production import OpenFootballProductionArtifactV1
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.openfootball_evidence import private_evidence_directory
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.infrastructure.files.training_evidence import strict_json_bytes
from football_system.interfaces.production_quant_cli import AuthorityPinsV1, production_inference_context, write_local_json

FIELDS = {
    "prepare-data": {"policy", "mapping", "rights_payload", "directive_evidence"},
    "record-data": {"request_key", "subject", "review", "authority"},
    "cutoff": {"request_key"}, "pilot-plan": {"request_key", "targets"},
    "pilot-run": {"request_key"}, "pilot-attest": {"request_key"}, "manifest": {"request_key"},
    "approval-prepare": {"effective_at_utc", "expires_at_utc"},
    "approval-record": {"request_key", "payload", "review", "authority"},
    "release-build": {"request_key"}, "target-plan": {"request_key"}, "model-bind": {"request_key"},
    "pin-candidate": set(), "inspect": {"kind"},
}


def dispatch_openfootball_production(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=tuple(FIELDS))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--authority-pins", required=True, help="Existing operator-controlled relative pins file, not a candidate self-grant")
    parser.add_argument("--request", required=True, help="Contained relative request JSON")
    parser.add_argument("--operator", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    engine = None
    try:
        root = private_evidence_directory(args.evidence_root)
        private_evidence_directory(args.output.absolute().parent)
        if not args.database.is_file() or args.output.exists():
            raise ValueError("EXISTING_DATABASE_AND_UNUSED_OUTPUT_REQUIRED")
        reader = BridgeEvidence(root, trusted_authorities={}, max_bytes=8 * 1024 * 1024)
        pins = AuthorityPinsV1.model_validate(strict_json_bytes(reader.read(args.authority_pins)))
        request = strict_json_bytes(reader.read(args.request))
        if not isinstance(request, dict) or set(request) != FIELDS[args.command]:
            raise ValueError("EXACT_OPERATION_FIELDS_REQUIRED")
        evidence = BridgeEvidence(root, trusted_authorities=pins.trusted_authorities, max_bytes=8 * 1024 * 1024)
        engine = create_database_engine(URL.create("sqlite", database=str(args.database.absolute())).render_as_string(hide_password=False))
        sessions = create_session_factory(engine)
        _, _, production, _, _ = production_inference_context(sessions, evidence=evidence, operator_id=args.operator)
        repo = production.openfootball_binding()
        operations = {
            "prepare-data": repo.prepare_data_binding, "record-data": repo.record_data_binding,
            "cutoff": repo.seal_cutoff, "pilot-plan": repo.seal_pilot_plan, "pilot-run": repo.run_pilot,
            "pilot-attest": repo.attest_pilot, "manifest": repo.create_manifest, "approval-prepare": repo.prepare_approval,
            "approval-record": repo.record_approval, "release-build": repo.build_release,
            "target-plan": repo.create_target_plan, "model-bind": repo.bind_model_state,
            "pin-candidate": repo.load_model_pin_candidate, "inspect": repo.inspect,
        }
        result = operations[args.command](**request)
        size, digest = write_local_json(args.output, canonical_json(result))
        summary = dict(status="OPERATION_COMPLETED", command=args.command, output=str(args.output), bytes=size, sha256=digest)
        if isinstance(result, OpenFootballProductionArtifactV1):
            summary.update(artifact_id=result.artifact_id, artifact_hash=result.artifact_hash, kind=result.kind)
        print(json.dumps(summary, ensure_ascii=True))
        return 0
    except Exception as error:
        # No source records, review bytes, credentials or raw Pydantic inputs.
        print(json.dumps(dict(status="PRODUCTION_BOOTSTRAP_BLOCKED", command=args.command,
            error_type=type(error).__name__, message="Check private request/evidence and operation prerequisites; a committed operation may exist if output publication failed.")))
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(dispatch_openfootball_production())
