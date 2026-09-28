"""Exact four-file, preparation-only boundary for the approved acquisition.

This is not a production-quant admission writer. Source trust, candidate rights
and unresolved mappings stay separate from future authority/review artifacts.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_snapshot import (
    OpenFootballAcquisitionManifestV1,
    OpenFootballAdapterPolicyV1,
    OpenFootballCanonicalMappingPlanV1,
    OpenFootballCurrentSnapshotScopeV1,
    OpenFootballFileCaptureV1,
    OpenFootballHistoricalValidationV1,
    OpenFootballMappingEntryV1,
    OpenFootballSourceRightsCandidateV1,
    PINNED_FILES,
    SEASONS,
)
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.infrastructure.files.training_evidence import strict_json_bytes
from football_system.infrastructure.providers.real.openfootball_observed import inspect_openfootball_file


def private_evidence_directory(path: str | Path) -> Path:
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("REPARSE_FREE_PRIVATE_DIRECTORY_REQUIRED")
        if (part / ".git").exists():
            raise ValueError("TRAINING_EVIDENCE_MUST_BE_OUTSIDE_PUBLIC_GIT")
        if part.name.casefold() in {"bundesliga_acceptance_20260911", "bundesliga_observed_training_draft", "bundesliga_probe_20260908"} or part.name.casefold().startswith("provider_capability_"):
            raise ValueError("CLOSED_CAPTURE_ROOT_FORBIDDEN")
    if not path.is_dir():
        raise ValueError("EXISTING_PRIVATE_DIRECTORY_REQUIRED")
    return path


def verify_capture_bytes(payload: bytes, capture: OpenFootballFileCaptureV1) -> None:
    capture = OpenFootballFileCaptureV1.model_validate(capture)
    if type(payload) is not bytes or len(payload) != capture.bytes or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), capture.sha256):
        raise ValueError("OPENFOOTBALL_CAPTURE_BYTES_MISMATCH")


def qualify_openfootball_files(*, acquisition, source_bytes: dict[str, bytes], policy) -> dict:
    """Validate all four identities/bytes before parsing any dataset rows."""
    acquisition = OpenFootballAcquisitionManifestV1.model_validate(acquisition)
    policy = OpenFootballAdapterPolicyV1.model_validate(policy)
    if set(source_bytes) != set(PINNED_FILES):
        raise ValueError("EXACT_FOUR_SOURCE_FILES_REQUIRED")
    for capture in acquisition.files:
        verify_capture_bytes(source_bytes[capture.path], capture)
    # The exact pinned CC0 document, not a generic 'free' string or README hash.
    rights = OpenFootballSourceRightsCandidateV1()
    scope = OpenFootballCurrentSnapshotScopeV1(acquisition=acquisition, adapter_policy=policy,
        candidate_seasons=tuple(SEASONS.values()))
    scans = [inspect_openfootball_file(source_bytes[c.path], filename=c.path,
        capture_at_utc=c.capture_at_utc, policy=policy) for c in acquisition.files if c.path in SEASONS]
    teams = sorted({t for scan in scans for t in scan["quality"]["teams"]})
    entries = [OpenFootballMappingEntryV1(kind="TEAM", source_label=t) for t in teams]
    entries += [OpenFootballMappingEntryV1(kind="COMPETITION", source_label="Deutsche Bundesliga")]
    entries += [OpenFootballMappingEntryV1(kind="SEASON", source_label=s) for s in sorted(SEASONS.values())]
    mapping = OpenFootballCanonicalMappingPlanV1(scope_hash=scope.content_hash, entries=tuple(entries))
    old_teams, new_teams = (set(scan["quality"]["teams"]) for scan in scans)
    exceptions = [dict(source_filename=scan["source_filename"],
        record_pointer=r["provenance"]["original_record_pointer"], score_class=r["score_class"],
        diagnostics=r["diagnostics"]) for scan in scans for r in scan["records"] if r["diagnostics"]]
    return dict(schema_version="OPENFOOTBALL_BOOTSTRAP_PREPARATION_V1", status="STAGE3_BLOCKED",
        source_binding="EXACT_PINNED_FILES_VERIFIED", scope=scope.model_dump(mode="json"), scope_hash=scope.content_hash,
        rights_candidate=rights.model_dump(mode="json"), rights_candidate_hash=rights.content_hash,
        adapter_policy_hash=policy.content_hash, datasets=scans, exceptions=exceptions,
        team_set_changes=dict(retained=sorted(old_teams & new_teams), left=sorted(old_teams - new_teams), joined=sorted(new_teams - old_teams)),
        canonical_mapping_plan=mapping.model_dump(mode="json"), canonical_mapping_plan_hash=mapping.content_hash,
        canonical_mapping_status="IDENTITY_UNRESOLVED",
        training_data_validity="BLOCKED_SOURCE_EXCEPTIONS" if exceptions else "PENDING_IDENTITY_AND_REVIEW",
        historical_validation=OpenFootballHistoricalValidationV1().model_dump(mode="json"),
        training_window_status="PENDING_EXACT_BASELINE_WINDOW_REVIEW", training_window=None,
        production_training_authorized=False, training_calls=0, business_writes=0)


def qualify_private_root(root: str | Path, *, policy: OpenFootballAdapterPolicyV1) -> dict:
    root = private_evidence_directory(root)
    evidence = BridgeEvidence(root, trusted_authorities={}, max_bytes=2 * 1024 * 1024)
    acquisition = OpenFootballAcquisitionManifestV1.model_validate(strict_json_bytes(evidence.read("acquisition_manifest.json")))
    source_bytes = {c.path: evidence.read(c.path, c.sha256) for c in acquisition.files}
    return qualify_openfootball_files(acquisition=acquisition, source_bytes=source_bytes, policy=policy)


def write_private_report(output: str | Path, report: dict) -> str:
    output = Path(output).absolute()
    private_evidence_directory(output.parent)
    if output.name in {"README.md", "LICENSE.md", "de.1.json", "acquisition_manifest.json", "acquisition_request.json"}:
        raise ValueError("OUTPUT_MUST_NOT_REPLACE_EVIDENCE")
    payload = (canonical_json(report) + "\n").encode("utf-8")
    if len(payload) > 16 * 1024 * 1024:
        raise ValueError("PREPARATION_REPORT_TOO_LARGE")
    temporary = None
    try:
        with NamedTemporaryFile(dir=output.parent, prefix=".openfootball-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)  # Atomic no-replace publication, no overwrite fallback.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()
