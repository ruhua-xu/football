"""Local evidence boundary for ADR-0008, not a provider or legal authority.

All references are POSIX relative paths below an operator-controlled root (keep
it outside public Git). Authority documents must also be pinned by SHA-256 in
trusted configuration, never in a candidate. The JSON formats are the models
below; unknown fields, duplicate JSON keys and non-finite numbers are rejected.

Provider paths are RFC 6901 JSON pointers relative to the selected full record.
Record hashes use TRAINING_PROVIDER_RECORD_V1 tagged canonical JSON; capture
hashes cover exact original bytes. No source timestamp is inferred from a file.
Review documents bind rights payloads or training_review_input_sha256(), not
their own bytes. Human assertions are auditable local evidence, not signatures.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import Field, ValidationError

from football_system.application.training_admission import (
    TrainingFactAdmissionCandidateV1,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    normalize_utc,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    Reference,
    Sha256Digest,
    tagged_canonical_sha256,
)


class ReviewerAuthorityV1(DomainModel):
    schema_version: Literal["TRAINING_REVIEWER_AUTHORITY_V1"] = (
        "TRAINING_REVIEWER_AUTHORITY_V1"
    )
    issued_by: Identifier
    authorized_reviewer: Identifier
    source_ids: tuple[Identifier, ...] = Field(min_length=1)
    attested_schema_versions: tuple[Identifier, ...] = Field(min_length=1)
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime

    def assert_active_for(self, at_utc: datetime) -> None:
        if not self.effective_at_utc <= normalize_utc(at_utc) < self.expires_at_utc:
            raise ValueError("reviewer authority is not active at operation time")


class TrainingReviewDocumentV1(DomainModel):
    schema_version: Literal["TRAINING_LOCAL_REVIEW_V1"] = "TRAINING_LOCAL_REVIEW_V1"
    attested_schema_version: Identifier
    attested_payload_hash: Sha256Digest
    prepared_by: Identifier
    authorized_reviewer: Identifier
    reviewed_at_utc: UtcDateTime
    source_ids: tuple[Identifier, ...] = Field(min_length=1)
    source_classification: Literal["REAL_SOURCE_DATA"]
    approved: Literal[True]
    retention_compatible: Literal[True]


class ScopeFieldPathsV1(DomainModel):
    fixture_key: Reference
    competition_id: Reference
    season_id: Reference
    available_at_utc: Reference


class FixtureFieldPathsV1(ScopeFieldPathsV1):
    home_team_id: Reference
    away_team_id: Reference
    kickoff_at_utc: Reference


class ResultFieldPathsV1(FixtureFieldPathsV1):
    result_key: Reference
    status: Reference
    score_semantics: Reference
    home_goals: Reference
    away_goals: Reference
    finalized_at_utc: Reference
    observed_at_utc: Reference


class TrainingJsonAdapterV1(DomainModel):
    """Reviewer-approved semantics, including an explicit score-semantics field.

    V1 deliberately cannot admit a source with only a score or a generic FT label
    without documented regular-time semantics and publication/finalization fields.
    """

    schema_version: Literal["TRAINING_JSON_ADAPTER_V1"] = "TRAINING_JSON_ADAPTER_V1"
    provider_code: Identifier
    provider_fixture_namespace: Identifier
    adapter_name: Identifier
    adapter_version: Identifier
    status_mapping_version: Identifier
    season_mapping_version: Identifier
    mapping_policy_version: Identifier
    regular_time_final_status: Identifier
    regular_time_score_semantics: Identifier
    fixture: FixtureFieldPathsV1
    scope: ScopeFieldPathsV1
    result: ResultFieldPathsV1


class CapturedRecordReferenceV1(DomainModel):
    capture_receipt_id: Identifier
    record_pointer: str = Field(max_length=2048)


class TrainingSourceEvidenceV1(DomainModel):
    fixture: CapturedRecordReferenceV1
    scope: CapturedRecordReferenceV1
    result: CapturedRecordReferenceV1
    adapter: LocalReviewEvidenceV1
    home_team_alias_id: Identifier
    away_team_alias_id: Identifier
    competition_mapping_id: Identifier


class TrainingFactSubmissionV1(DomainModel):
    candidate: TrainingFactAdmissionCandidateV1
    source_evidence: TrainingSourceEvidenceV1
    reviewer_evidence: LocalReviewEvidenceV1
    reviewer_authority: LocalReviewEvidenceV1


def sanitized_evidence_errors(operation):
    """Never render parser/Pydantic input values at an external evidence boundary."""

    @wraps(operation)
    def checked(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (ValidationError, UnicodeError, json.JSONDecodeError, RecursionError):
            raise ValueError("invalid training evidence structure") from None

    return checked


@sanitized_evidence_errors
def training_review_input_sha256(
    candidate: TrainingFactAdmissionCandidateV1,
    source_evidence: TrainingSourceEvidenceV1,
) -> str:
    candidate = TrainingFactAdmissionCandidateV1.model_validate(
        candidate.model_dump(mode="python", warnings=False)
    )
    source_evidence = TrainingSourceEvidenceV1.model_validate(
        source_evidence.model_dump(mode="python", warnings=False)
    )
    return tagged_canonical_sha256(
        "TRAINING_FACT_REVIEW_INPUT_V1",
        {"candidate": candidate, "source_evidence": source_evidence},
    )


def strict_json_bytes(payload: bytes) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError("non-finite JSON number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            invalid(value)
        return parsed

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid,
            parse_float=finite_float,
        )
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid local JSON evidence") from None


def json_pointer(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("expected an RFC 6901 JSON pointer")
    value = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            try:
                index = int(token)
            except ValueError:
                raise ValueError("invalid provider array field pointer") from None
            if index >= len(value):
                raise ValueError("missing provider field")
            value = value[index]
        else:
            raise ValueError("missing provider field")
    return value


@sanitized_evidence_errors
def provider_record_sha256(record: object) -> str:
    if not isinstance(record, dict):
        raise ValueError("provider record must be a full JSON object")
    return tagged_canonical_sha256("TRAINING_PROVIDER_RECORD_V1", record)


def verified_provider_fields(payload, reference, paths, *, digest, nullable_fields=(), missing_fields=()):
    """Shared strict extraction; nulls are allowed only by the new caller contract."""
    record = json_pointer(strict_json_bytes(payload), reference.record_pointer)
    if provider_record_sha256(record) != digest:
        raise ValueError("full provider record hash mismatch")
    field_paths = paths.model_dump(exclude_none=True)
    if len(set(field_paths.values())) != len(field_paths):
        raise ValueError("provider field paths must be distinct")
    extracted = {}
    for key, path in field_paths.items():
        try:
            extracted[key] = json_pointer(record, path)
        except ValueError as error:
            if key not in missing_fields or str(error) != "missing provider field":
                raise
            extracted[key] = None
    for key, value in extracted.items():
        if value is None and key in nullable_fields:
            continue
        if key in {"home_goals", "away_goals", "revision_order"}:
            if type(value) is not int or value < 0:
                raise ValueError("provider scores/order must be nonnegative JSON integers")
        elif key.endswith("_at_utc"):
            if not isinstance(value, str) or "T" not in value:
                raise ValueError("provider time must be an explicit ISO-8601 timestamp")
            try:
                extracted[key] = normalize_utc(datetime.fromisoformat(value))
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"invalid provider timestamp field: {key}") from None
        elif not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("provider identity/status fields must be explicit strings")
    return extracted


class _WindowsEvidenceHandles:
    """Win32 handles, with validation before transferring any handle to a reader."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateFileW": (
                [
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.HANDLE,
                ],
                wintypes.HANDLE,
            ),
            "GetFileInformationByHandle": (
                [wintypes.HANDLE, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            "GetFinalPathNameByHandleW": (
                [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD],
                wintypes.DWORD,
            ),
            "GetFileType": ([wintypes.HANDLE], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result

        class FileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("created", wintypes.FILETIME),
                ("accessed", wintypes.FILETIME),
                ("written", wintypes.FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            ]

        self.file_information = FileInformation

    def open(self, path: Path, *, directory: bool):
        # OPEN_REPARSE_POINT prevents following the final component. Keeping
        # every directory open without write/delete sharing also pins traversal.
        handle = self.api.CreateFileW(
            str(path),
            0x80 if directory else 0x80000000,
            0x1,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle == self.ctypes.c_void_p(-1).value:
            raise OSError("cannot safely open local evidence")
        return handle

    def validate(self, handle, expected: Path, *, directory: bool) -> None:
        info = self.file_information()
        if not self.api.GetFileInformationByHandle(handle, self.ctypes.byref(info)):
            raise OSError("cannot inspect opened evidence handle")
        if (
            info.attributes & 0x400
            or bool(info.attributes & 0x10) != directory
            or self.api.GetFileType(handle) != 1
        ):
            raise ValueError("evidence handle must be regular and reparse-free")
        length = self.api.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not length:
            raise OSError("cannot resolve opened evidence handle")
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        written = self.api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            raise OSError("cannot resolve opened evidence handle")
        final = buffer.value
        if final.startswith("\\\\?\\UNC\\"):
            final = "\\\\" + final[8:]
        elif final.startswith("\\\\?\\"):
            final = final[4:]
        if PureWindowsPath(final) != PureWindowsPath(expected):
            raise ValueError("opened evidence handle escaped its controlled path")

    def close(self, handle) -> None:
        self.api.CloseHandle(handle)


@contextmanager
def _open_evidence_file(root: Path, parts: tuple[str, ...]):
    """Containment is attached to opened objects, not a check-then-open pathname."""
    with ExitStack() as opened:
        if os.name == "nt":
            import msvcrt

            handles = _WindowsEvidenceHandles()
            path = root
            for index in range(len(parts) + 1):
                directory = index < len(parts)
                if index:
                    path = path / parts[index - 1]
                handle = handles.open(path, directory=directory)
                owner = ExitStack()
                owner.callback(handles.close, handle)
                opened.enter_context(owner)
                handles.validate(handle, path, directory=directory)
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            owner.pop_all()  # The descriptor now owns the validated file handle.
        else:
            descriptor = os.open(
                root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            opened.callback(os.close, descriptor)
            for part in (*root.parts[1:], *parts[:-1]):
                descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                opened.callback(os.close, descriptor)
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("evidence must be a regular file")
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            yield stream


class LocalTrainingEvidence:
    def __init__(
        self,
        root: str | Path,
        *,
        trusted_authorities: dict[str, str],
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or max_bytes <= 0:
            raise ValueError("evidence requires a directory and a positive byte limit")
        self.trusted_authorities = dict(trusted_authorities)
        self.max_bytes = max_bytes

    def read(self, reference: str, expected_sha256: str | None = None) -> bytes:
        if not isinstance(reference, str):
            raise ValueError("evidence reference must be a relative POSIX path")
        relative = PurePosixPath(reference)
        if (
            not reference
            or relative.is_absolute()
            or "\\" in reference
            or ":" in reference
            or any(p in {"", ".", ".."} for p in reference.split("/"))
        ):
            raise ValueError(
                "evidence reference must be a contained relative POSIX path"
            )
        try:
            with _open_evidence_file(self.root, relative.parts) as stream:
                chunks = []
                remaining = self.max_bytes + 1
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
        except (OSError, ValueError):
            raise ValueError("cannot safely read contained local evidence") from None
        if not payload or len(payload) > self.max_bytes:
            raise ValueError("evidence is empty or exceeds the byte limit")
        if expected_sha256 is not None and not hmac.compare_digest(
            hashlib.sha256(payload).hexdigest(), expected_sha256
        ):
            raise ValueError("local evidence SHA-256 mismatch")
        return payload

    @sanitized_evidence_errors
    def load_authority(self, authority: LocalReviewEvidenceV1) -> ReviewerAuthorityV1:
        pinned = self.trusted_authorities.get(authority.evidence_reference)
        if pinned is None or not hmac.compare_digest(pinned, authority.evidence_sha256):
            raise ValueError(
                "reviewer authority is not pinned in trusted configuration"
            )
        return ReviewerAuthorityV1.model_validate(
            strict_json_bytes(self.read(authority.evidence_reference, pinned))
        )

    @sanitized_evidence_errors
    def review(
        self,
        *,
        evidence: LocalReviewEvidenceV1,
        authority: LocalReviewEvidenceV1,
        schema: str,
        digest: str,
        source_ids: tuple[str, ...],
        operator_id: str,
        at_utc: datetime,
    ) -> TrainingReviewDocumentV1:
        # Actor/preparer/issuer names are provenance, not separation-of-duty policy.
        del operator_id
        permission = self.load_authority(authority)
        review = TrainingReviewDocumentV1.model_validate(
            strict_json_bytes(
                self.read(evidence.evidence_reference, evidence.evidence_sha256)
            )
        )
        if review.authorized_reviewer != permission.authorized_reviewer:
            raise ValueError("reviewer is not authorized by the pinned authority")
        if (
            review.attested_schema_version != schema
            or review.attested_payload_hash != digest
            or schema not in permission.attested_schema_versions
            or set(review.source_ids) != set(source_ids)
            or not set(source_ids) <= set(permission.source_ids)
            or len(set(review.source_ids)) != len(review.source_ids)
        ):
            raise ValueError("review does not bind the exact payload and source scope")
        if not (
            permission.effective_at_utc
            <= review.reviewed_at_utc
            < permission.expires_at_utc
            and review.reviewed_at_utc <= normalize_utc(at_utc)
        ):
            raise ValueError("review time is outside authority or follows recording")
        return review

    @sanitized_evidence_errors
    def verify_provider_records(
        self,
        submission: TrainingFactSubmissionV1,
        payloads: tuple[bytes, bytes, bytes],
    ) -> tuple[str, str, str]:
        """Verify bytes against candidate; return provider home/away/competition IDs.

        The repository must independently verify capture receipts, reviewer evidence,
        and these IDs against the explicitly referenced registered identity rows.
        """
        candidate = TrainingFactAdmissionCandidateV1.model_validate(
            submission.candidate.model_dump(mode="python", warnings=False)
        )
        evidence = submission.source_evidence
        adapter = TrainingJsonAdapterV1.model_validate(
            strict_json_bytes(
                self.read(
                    evidence.adapter.evidence_reference,
                    evidence.adapter.evidence_sha256,
                )
            )
        )
        fixture = candidate.fixture_source
        membership = candidate.season_membership.content_payload
        result = candidate.match_result_admission.content_payload
        if (
            adapter.provider_code != fixture.provider_code
            or adapter.provider_fixture_namespace != fixture.provider_fixture_namespace
            or (
                adapter.adapter_name,
                adapter.adapter_version,
                adapter.status_mapping_version,
            )
            != (
                result.adapter_name,
                result.adapter_version,
                result.status_mapping_version,
            )
            or (adapter.season_mapping_version, adapter.mapping_policy_version)
            != (membership.season_mapping_version, membership.mapping_policy_version)
            or (
                adapter.scope.competition_id,
                adapter.scope.season_id,
                adapter.scope.fixture_key,
            )
            != (
                membership.provider_competition_field_path,
                membership.provider_season_field_path,
                membership.provider_fixture_field_path,
            )
        ):
            raise ValueError("adapter evidence does not match candidate")
        refs = (evidence.fixture, evidence.scope, evidence.result)
        hashes = (
            fixture.fixture_record_sha256,
            membership.provider_scope_record_sha256,
            result.raw_record_sha256,
        )
        values = []
        for payload, ref, paths, digest in zip(
            payloads,
            refs,
            (adapter.fixture, adapter.scope, adapter.result),
            hashes,
            strict=True,
        ):
            extracted = verified_provider_fields(payload, ref, paths, digest=digest)
            if (
                extracted["fixture_key"],
                extracted["competition_id"],
                extracted["season_id"],
            ) != (
                fixture.provider_fixture_key,
                membership.provider_competition_id,
                membership.provider_season_id,
            ):
                raise ValueError(
                    "provider fixture/competition/season evidence mismatch"
                )
            values.append(extracted)
        f, s, r = values
        if (
            f["available_at_utc"],
            s["available_at_utc"],
            r["available_at_utc"],
            r["observed_at_utc"],
            r["finalized_at_utc"],
        ) != (
            fixture.source_available_at_utc,
            membership.source_available_at_utc,
            result.source_available_at_utc,
            result.source_observed_at_utc,
            result.provider_finalized_at_utc,
        ):
            raise ValueError("provider timestamp evidence mismatch")
        if (
            r["status"] != adapter.regular_time_final_status
            or r["status"] != result.provider_raw_status
            or r["score_semantics"] != adapter.regular_time_score_semantics
            or (r["home_goals"], r["away_goals"], r["result_key"])
            != (
                result.regular_time_home_goals,
                result.regular_time_away_goals,
                result.provider_result_key,
            )
        ):
            raise ValueError("provider regular-time FT/score evidence mismatch")
        if (
            f["kickoff_at_utc"] != candidate.canonical_identity.kickoff_at_utc
            or r["kickoff_at_utc"] != f["kickoff_at_utc"]
            or (r["home_team_id"], r["away_team_id"])
            != (f["home_team_id"], f["away_team_id"])
            or f["home_team_id"] == f["away_team_id"]
        ):
            raise ValueError("provider home/away/kickoff evidence mismatch")
        return f["home_team_id"], f["away_team_id"], f["competition_id"]
