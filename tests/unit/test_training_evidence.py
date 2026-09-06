import hashlib
import json
import os
from io import BytesIO
from datetime import datetime, timezone
from traceback import format_exception

import pytest

from football_system.domain.training_admission import LocalReviewEvidenceV1
from football_system.infrastructure.files import training_evidence as evidence_module
from football_system.infrastructure.files.training_evidence import (
    LocalTrainingEvidence,
    json_pointer,
    provider_record_sha256,
    strict_json_bytes,
)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"id":1,"id":2}',
        b'{"a":{"id":1,"id":2}}',
        b'{"n":NaN}',
        b'{"n":Infinity}',
        b'{"n":-Infinity}',
        b'{"n":1e999}',
        b"\xff",
        b"{broken",
    ],
)
def test_strict_json_rejects_ambiguous_bytes(payload):
    with pytest.raises((ValueError, UnicodeError)):
        strict_json_bytes(payload)


def test_json_pointer_and_full_record_hash():
    document = {"a/b": [{"~key": {"id": "x", "unmapped": "evidence"}}]}
    record = json_pointer(document, "/a~1b/0/~0key")
    assert json_pointer(document, "") == document
    assert provider_record_sha256(record) != provider_record_sha256({"id": "x"})
    assert provider_record_sha256(record) == provider_record_sha256(
        dict(reversed(list(record.items())))
    )


@pytest.mark.parametrize(
    "pointer", ["$.a", "a", "/a/01", "/a/-", "/a/2", "/a/~2", "/missing"]
)
def test_json_pointer_rejects_heuristics_and_missing_fields(pointer):
    with pytest.raises(ValueError):
        json_pointer({"a": [1]}, pointer)


@pytest.mark.parametrize(
    "reference",
    ["../outside", "/absolute", "C:/outside", "a\\b", "a//b", "./a", "a/../b", ""],
)
def test_evidence_references_are_contained(tmp_path, reference):
    evidence = LocalTrainingEvidence(tmp_path, trusted_authorities={})
    with pytest.raises(ValueError, match="relative POSIX"):
        evidence.read(reference)


def test_reads_actual_bytes_and_rechecks_hash_and_size(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_bytes(b"terms")
    evidence = LocalTrainingEvidence(tmp_path, trusted_authorities={}, max_bytes=5)
    digest = hashlib.sha256(b"terms").hexdigest()
    assert evidence.read("terms.txt", digest) == b"terms"
    path.write_bytes(b"other")
    with pytest.raises(ValueError, match="SHA-256"):
        evidence.read("terms.txt", digest)
    path.write_bytes(b"too long")
    with pytest.raises(ValueError, match="byte limit"):
        evidence.read("terms.txt")
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        evidence.read("terms.txt")


def test_evidence_reader_uses_bounded_chunks_without_lowering_limit(tmp_path, monkeypatch):
    payload = b"e" * (3 * 64 * 1024 + 7)
    reads = []

    class Stream(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= 64 * 1024
            return super().read(size)

    monkeypatch.setattr(
        evidence_module, "_open_evidence_file", lambda *args: Stream(payload)
    )
    evidence = LocalTrainingEvidence(tmp_path, trusted_authorities={})
    assert evidence.max_bytes == 64 * 1024 * 1024
    assert evidence.read("large.json", hashlib.sha256(payload).hexdigest()) == payload
    assert len(reads) >= 4


@pytest.fixture
def review_files(tmp_path):
    permission = {
        "schema_version": "TRAINING_REVIEWER_AUTHORITY_V1",
        "issued_by": "governance",
        "authorized_reviewer": "reviewer",
        "source_ids": ["source"],
        "attested_schema_versions": ["SUBJECT_V1"],
        "effective_at_utc": "2026-01-01T00:00:00Z",
        "expires_at_utc": "2027-01-01T00:00:00Z",
    }
    review = {
        "schema_version": "TRAINING_LOCAL_REVIEW_V1",
        "attested_schema_version": "SUBJECT_V1",
        "attested_payload_hash": "a" * 64,
        "prepared_by": "operator",
        "authorized_reviewer": "reviewer",
        "source_ids": ["source"],
        "source_classification": "REAL_SOURCE_DATA",
        "reviewed_at_utc": "2026-02-01T00:00:00Z",
        "approved": True,
        "retention_compatible": True,
    }

    def verify(*, review_changes=None, authority_changes=None, pinned=True):
        raw_permission = json.dumps(
            {**permission, **(authority_changes or {})}
        ).encode()
        raw_review = json.dumps({**review, **(review_changes or {})}).encode()
        (tmp_path / "authority.json").write_bytes(raw_permission)
        (tmp_path / "review.json").write_bytes(raw_review)
        authority = LocalReviewEvidenceV1(
            evidence_reference="authority.json",
            evidence_sha256=hashlib.sha256(raw_permission).hexdigest(),
        )
        evidence = LocalTrainingEvidence(
            tmp_path,
            trusted_authorities={"authority.json": authority.evidence_sha256}
            if pinned
            else {},
        )
        return evidence.review(
            evidence=LocalReviewEvidenceV1(
                evidence_reference="review.json",
                evidence_sha256=hashlib.sha256(raw_review).hexdigest(),
            ),
            authority=authority,
            schema="SUBJECT_V1",
            digest="a" * 64,
            source_ids=("source",),
            operator_id="operator",
            at_utc=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

    return verify


def test_authorized_pinned_local_review(review_files):
    assert review_files().authorized_reviewer == "reviewer"
    with pytest.raises(ValueError, match="pinned"):
        review_files(pinned=False)


@pytest.mark.parametrize(
    "changes",
    [
        {"authorized_reviewer": "operator"},
        {"attested_payload_hash": "b" * 64},
        {"attested_schema_version": "OTHER"},
        {"source_ids": ["different"]},
        {"source_ids": ["source", "source"]},
        {"source_classification": "SYNTHETIC_ACCEPTANCE_DATA"},
        {"approved": False},
        {"retention_compatible": False},
        {"reviewed_at_utc": "2026-04-01T00:00:00Z"},
        {"reviewed_at_utc": "2025-01-01T00:00:00Z"},
    ],
)
def test_local_review_fails_closed(review_files, changes):
    with pytest.raises(ValueError):
        review_files(review_changes=changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"authorized_reviewer": "other"},
        {"source_ids": ["other"]},
        {"attested_schema_versions": ["OTHER"]},
        {"expires_at_utc": "2026-02-01T00:00:00Z"},
    ],
)
def test_authority_scope_and_reviewer_mismatch_fail_closed(review_files, changes):
    with pytest.raises(ValueError):
        review_files(authority_changes=changes)


def test_pinned_operator_reviewer_preparer_and_issuer_may_be_the_same_person(
    review_files,
):
    review = review_files(
        review_changes={"authorized_reviewer": "operator"},
        authority_changes={"authorized_reviewer": "operator", "issued_by": "operator"},
    )
    assert review.authorized_reviewer == review.prepared_by == "operator"


@pytest.mark.parametrize("preparer", ["reviewer", "another-preparer"])
def test_preparer_is_preserved_not_required_to_be_operation_actor(
    review_files, preparer
):
    assert (
        review_files(review_changes={"prepared_by": preparer}).prepared_by == preparer
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows handle validation")
def test_windows_redirected_open_cannot_read_outside_bytes(tmp_path, monkeypatch):
    root = tmp_path / "controlled"
    root.mkdir()
    directory = root / "nested"
    directory.mkdir()
    (directory / "evidence.json").write_bytes(b'{"safe":true}')
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"private":"DO_NOT_READ"}')
    evidence = LocalTrainingEvidence(root, trusted_authorities={})
    native_open = evidence_module._WindowsEvidenceHandles.open
    redirects = []

    def redirected(handles, path, *, directory):
        # Deterministic model of junction redirection during CreateFileW: return
        # a real outside file handle, not a fabricated path or fake byte stream.
        if not directory:
            redirects.append(path)
            return native_open(handles, outside, directory=False)
        return native_open(handles, path, directory=True)

    def forbidden_reader(*args, **kwargs):
        pytest.fail("an uncontained handle reached the payload reader")

    monkeypatch.setattr(evidence_module._WindowsEvidenceHandles, "open", redirected)
    monkeypatch.setattr(os, "fdopen", forbidden_reader)
    with pytest.raises(ValueError, match="safely read contained"):
        evidence.read("nested/evidence.json")
    assert redirects == [root / "nested" / "evidence.json"]
    # Failed validation released every handle, including the outside handle.
    outside.unlink()
    (root / "nested" / "evidence.json").unlink()


@pytest.mark.skipif(os.name != "nt", reason="native Windows reparse-point validation")
def test_windows_directory_replaced_by_junction_before_open_is_rejected(
    tmp_path, monkeypatch
):
    import subprocess

    root = tmp_path / "controlled"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.json").write_bytes(b"DO_NOT_READ")
    evidence = LocalTrainingEvidence(root, trusted_authorities={})
    native_open = evidence_module._WindowsEvidenceHandles.open
    replaced = False

    def swap(handles, path, *, directory):
        nonlocal replaced
        if path == nested and not replaced:
            nested.rmdir()
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(nested), str(outside)],
                check=True,
                capture_output=True,
            )
            replaced = True
        return native_open(handles, path, directory=directory)

    monkeypatch.setattr(evidence_module._WindowsEvidenceHandles, "open", swap)
    try:
        with pytest.raises(ValueError, match="safely read contained"):
            evidence.read("nested/private.json")
        assert replaced
    finally:
        if replaced:
            nested.rmdir()
    assert (outside / "private.json").read_bytes() == b"DO_NOT_READ"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd/O_NOFOLLOW validation")
def test_posix_directory_swap_cannot_follow_a_new_symlink(tmp_path, monkeypatch):
    root = tmp_path / "controlled"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.json").write_bytes(b"DO_NOT_READ")
    evidence = LocalTrainingEvidence(root, trusted_authorities={})
    native_open = os.open

    def swapped(path, flags, *args, **kwargs):
        if path == "nested":
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(ValueError, match="safely read contained"):
        evidence.read("nested/private.json")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"PRIVATE_SENTINEL":1,"PRIVATE_SENTINEL":2}',
        b'{"token":"PRIVATE_SENTINEL",',
        b'{"PRIVATE_SENTINEL":NaN}',
        b"PRIVATE_SENTINEL\xff",
    ],
)
def test_untrusted_json_parse_errors_do_not_render_content(payload):
    with pytest.raises(ValueError) as error:
        strict_json_bytes(payload)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert "PRIVATE_SENTINEL" not in "".join(format_exception(error.value))
    assert error.value.__suppress_context__


@pytest.mark.parametrize("document", ["review", "authority"])
def test_untrusted_pydantic_errors_are_sanitized(review_files, document):
    changes = {
        "review_changes" if document == "review" else "authority_changes": {
            "PRIVATE_SENTINEL": "PRIVATE_SENTINEL"
        }
    }
    with pytest.raises(
        ValueError, match="invalid training evidence structure"
    ) as error:
        review_files(**changes)
    assert "PRIVATE_SENTINEL" not in "".join(format_exception(error.value))
    assert error.value.__suppress_context__
