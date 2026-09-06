"""Atomic directory exchange for an unchanged V3 packet and its audit sidecar.

The low-level reader/writer validate bytes, not local grants. CLI callers should
use export_production_audit_bundle/import_production_audit_bundle, which also
invoke the full database gate. No source files are copied into the bundle.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import shutil
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import TYPE_CHECKING

from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
    _parse_analysis_packet,
    canonical_json,
    strict_json_loads,
)
from football_system.domain.production_release import ApprovedTrainingHistoryAuditV1
from football_system.domain.raw_data import (
    is_secret_like_parameter_name,
    is_secret_like_parameter_value,
)
from football_system.domain.review import (
    AnalysisPacketV3,
    LLMReviewArtifact,
    MAX_CONTRACT_FILE_BYTES,
)
from football_system.infrastructure.files.training_evidence import _open_evidence_file

if TYPE_CHECKING:
    from football_system.infrastructure.database.production_audit_repository import (
        SqlAlchemyProductionAuditRepository,
    )
    from football_system.infrastructure.database.review_repositories import (
        SqlAlchemyReviewArtifactRepository,
    )

PACKET_FILENAME = "analysis_packet_v3.json"
AUDIT_FILENAME = "approved_training_history_audit_v1.json"
MANIFEST_FILENAME = "bundle_manifest.json"
BUNDLE_SCHEMA = "PRODUCTION_AUDIT_BUNDLE_V1"


@dataclass(frozen=True)
class ProductionAuditBundle:
    packet_bytes: bytes
    audit_bytes: bytes
    packet: AnalysisPacketV3
    audit: ApprovedTrainingHistoryAuditV1


def _safe_json(data):
    try:
        payload = strict_json_loads(data)
        pending = [(payload, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 64:
                raise ValueError
            if isinstance(item, dict):
                for key, value in item.items():
                    if is_secret_like_parameter_name(key) or re.sub(
                        r"[^a-z]", "", key.lower()
                    ) in {
                        "raw",
                        "rawbytes",
                        "rawpayload",
                        "rawresponse",
                        "sourcebytes",
                        "sourcepayload",
                        "providerpayload",
                        "sourcejson",
                        "sourcehtml",
                    }:
                        raise ValueError
                    pending.append((value, depth + 1))
            elif isinstance(item, list):
                pending.extend((value, depth + 1) for value in item)
            elif isinstance(item, str) and (
                len(item) > 32768
                or is_secret_like_parameter_value(item)
                or re.search(r"<!doctype|<html|-----BEGIN|^\s*[\[{]", item, re.I)
            ):
                raise ValueError
        return payload
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid or unsafe production bundle JSON") from None


def validate_production_audit_pair(
    packet_bytes: bytes, audit_bytes: bytes
) -> tuple[AnalysisPacketV3, ApprovedTrainingHistoryAuditV1]:
    """Byte/type/hash/binding validation only; never a production authorization."""
    try:
        _safe_json(packet_bytes)
        packet = _parse_analysis_packet(packet_bytes)
        audit = ApprovedTrainingHistoryAuditV1.model_validate(_safe_json(audit_bytes))
        if not isinstance(packet, AnalysisPacketV3):
            raise ValueError
        content = audit.content_payload
        if (
            content.packet.artifact_id != packet.packet_id
            or content.packet.content_hash != packet.packet_hash
            or content.analysis_run_id != packet.analysis_run.analysis_run_id
            or content.input_manifest_hash != packet.analysis_run.input_manifest_hash
        ):
            raise ValueError
        return packet, audit
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid production packet/audit pair") from None


def read_production_audit_bundle(path: Path) -> ProductionAuditBundle:
    """Read exact named, bounded, reparse-free files and validate both byte hashes."""
    path = Path(os.path.abspath(path))
    expected_names = {PACKET_FILENAME, AUDIT_FILENAME, MANIFEST_FILENAME}
    if {item.name for item in path.iterdir()} != expected_names:
        raise ValueError(
            "production bundle requires exactly packet, sidecar and manifest"
        )
    payloads = {}
    for name in sorted(expected_names):
        with _open_evidence_file(path, (name,)) as stream:
            data = stream.read(MAX_CONTRACT_FILE_BYTES + 1)
        if len(data) > MAX_CONTRACT_FILE_BYTES:
            raise ValueError("production bundle file exceeds size limit")
        payloads[name] = data
    manifest = _safe_json(payloads[MANIFEST_FILENAME])
    if manifest != _manifest(payloads):
        raise ValueError("production bundle manifest/hash mismatch")
    packet, audit = validate_production_audit_pair(
        payloads[PACKET_FILENAME], payloads[AUDIT_FILENAME]
    )
    return ProductionAuditBundle(
        payloads[PACKET_FILENAME], payloads[AUDIT_FILENAME], packet, audit
    )


def write_production_audit_bundle(
    path: Path,
    *,
    packet_json: str,
    audit: ApprovedTrainingHistoryAuditV1,
    publication_gate: Callable[[], AbstractContextManager] = nullcontext,
) -> Path:
    """Publish a NEW directory by no-replace rename, cleaning all failed staging.

    publication_gate must check current local authorization immediately before
    publication and hold its transaction through the rename. The default is for
    byte-only tooling; the high-level export function always supplies this gate.
    """
    path = Path(os.path.abspath(path))
    if path.exists() or path.is_symlink():
        raise FileExistsError("refusing to overwrite production bundle directory")
    if not path.parent.is_dir():
        raise FileNotFoundError("production bundle parent directory does not exist")
    packet_bytes = (packet_json + "\n").encode("utf-8")
    audit_bytes = (canonical_json(audit) + "\n").encode("utf-8")
    validate_production_audit_pair(packet_bytes, audit_bytes)
    payloads = {PACKET_FILENAME: packet_bytes, AUDIT_FILENAME: audit_bytes}
    payloads[MANIFEST_FILENAME] = (canonical_json(_manifest(payloads)) + "\n").encode(
        "utf-8"
    )
    staging = Path(mkdtemp(prefix=f".{path.name}.", suffix=".staging", dir=path.parent))
    try:
        for name, data in payloads.items():
            with (staging / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        read_production_audit_bundle(staging)
        with publication_gate():
            _rename_new_directory(staging, path)
        return path
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def export_production_audit_bundle(
    path: Path,
    *,
    analysis_run_id: str,
    review_repository: SqlAlchemyReviewArtifactRepository,
    audit_repository: SqlAlchemyProductionAuditRepository,
) -> Path:
    """Commit packet and sidecar together, then publish their gated directory."""
    _require_repository_pair(review_repository, audit_repository)
    packet, _ = ExportAnalysisPacketService(review_repository).export(
        analysis_run_id, "ANALYSIS_PACKET_V3"
    )
    stored, audit = audit_repository.load_bundle(packet.packet_id)
    return write_production_audit_bundle(
        path,
        packet_json=stored.packet_json,
        audit=audit,
        publication_gate=lambda: audit_repository.publication_gate(
            stored.packet_json.encode("utf-8"), audit
        ),
    )


def import_production_audit_bundle(
    path: Path,
    review_bytes: bytes,
    *,
    review_repository: SqlAlchemyReviewArtifactRepository,
    audit_repository: SqlAlchemyProductionAuditRepository,
) -> LLMReviewArtifact:
    """Validate the supplied sidecar against local state, then import locally.

    This does not install foreign packets/audits or manufacture review content.
    Standalone historical validate_review_files remains independent of grants.
    """
    _require_repository_pair(review_repository, audit_repository)
    bundle = read_production_audit_bundle(path)
    audit_repository.validate_bundle(bundle.packet_bytes, bundle.audit)
    return ImportLLMReviewService(review_repository).import_review(
        bundle.packet_bytes, review_bytes
    )


def _require_repository_pair(review_repository, audit_repository):
    from football_system.infrastructure.database.production_audit_repository import (
        SqlAlchemyProductionAuditRepository,
    )
    from football_system.infrastructure.database.review_repositories import (
        SqlAlchemyReviewArtifactRepository,
    )

    if (
        type(audit_repository) is not SqlAlchemyProductionAuditRepository
        or type(review_repository) is not SqlAlchemyReviewArtifactRepository
        or review_repository._audit.repository is not audit_repository
    ):
        raise ValueError(
            "production bundle requires matching concrete audited repositories"
        )


def _manifest(payloads):
    return {
        "schema_version": BUNDLE_SCHEMA,
        "files": {
            name: {
                "sha256": hashlib.sha256(payloads[name]).hexdigest(),
                "size_bytes": len(payloads[name]),
            }
            for name in (PACKET_FILENAME, AUDIT_FILENAME)
        },
    }


def _rename_new_directory(source, target):
    # os.rename on POSIX can overwrite an empty target directory. Do not emulate
    # no-replace with a racy exists() check or publish an empty reservation first.
    if os.name == "nt":
        os.rename(source, target)
        return
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is not None:
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) == 0:
                return
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(
                    "refusing to overwrite production bundle directory"
                )
            raise OSError(error, "atomic production bundle publication failed")
    raise OSError(
        "atomic no-replace directory publication is unsupported on this platform"
    )
