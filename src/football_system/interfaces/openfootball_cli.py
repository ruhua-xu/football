"""Offline Stage 3 qualification entry point; deliberately no training command.

Run via `python -m football_system.interfaces.openfootball_cli`. It is additive:
the frozen production-quant/real-bridge dispatch and V1 schemas are untouched.
"""

import argparse
import json
from pathlib import Path

from football_system.domain.openfootball_snapshot import OpenFootballAdapterPolicyV1
from football_system.infrastructure.files.openfootball_evidence import (
    private_evidence_directory,
    qualify_private_root,
    write_private_report,
)
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.infrastructure.files.training_evidence import strict_json_bytes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--policy", required=True, help="Contained relative policy JSON, including Europe/Berlin and TZif pins")
    parser.add_argument("--output", type=Path, required=True, help="New private preparation report; never overwritten")
    arguments = parser.parse_args(argv)
    try:
        root = private_evidence_directory(arguments.evidence_root)
        private_evidence_directory(arguments.output.absolute().parent)
        reader = BridgeEvidence(root, trusted_authorities={}, max_bytes=64 * 1024)
        policy = OpenFootballAdapterPolicyV1.model_validate(strict_json_bytes(reader.read(arguments.policy)))
        report = qualify_private_root(root, policy=policy)
        digest = write_private_report(arguments.output, report)
    except (ValueError, OSError):
        # Pydantic errors can contain input bytes. Never dump the input document.
        print(json.dumps(dict(status="STAGE3_BLOCKED", error="INVALID_OR_UNAVAILABLE_OPENFOOTBALL_PREPARATION_INPUT")))
        return 2
    print(json.dumps(dict(status=report["status"], output=str(arguments.output), sha256=digest,
        source_binding=report["source_binding"], datasets=[dict(path=d["source_filename"], quality=d["quality"]) for d in report["datasets"]],
        canonical_mapping_status=report["canonical_mapping_status"], training_calls=0), ensure_ascii=True))
    return 0  # Qualification completed; status still explicitly BLOCKED, never approval.


if __name__ == "__main__":
    raise SystemExit(main())
