"""SYNTHETIC_CONTRACT_TEST_ONLY: byte exchange, not source approval/web review."""

import hashlib
import json
from contextlib import contextmanager

import pytest

from football_system.application.review_bridge import canonical_json
from football_system.domain.review import MAX_CONTRACT_FILE_BYTES
from football_system.infrastructure.files import production_audit_bundle as files
from tests.unit import test_production_release as fixtures

manifest = fixtures.manifest
release = fixtures.release
bundle = fixtures.bundle


def write(tmp_path, bundle, **kwargs):
    audit, common = bundle
    return files.write_production_audit_bundle(
        tmp_path / "new-bundle",
        audit=audit,
        packet_json=canonical_json(common["packet"].model_dump(mode="json")),
        **kwargs,
    )


def test_pair_round_trip_preserves_exact_legacy_v3_bytes(tmp_path, bundle):
    audit, common = bundle
    before = canonical_json(common["packet"].model_dump(mode="json"))
    path = write(tmp_path, bundle)
    loaded = files.read_production_audit_bundle(path)
    assert loaded.packet_bytes == (before + "\n").encode("utf-8")
    assert loaded.packet == common["packet"]
    assert loaded.audit == audit
    assert "APPROVED_TRAINING_HISTORY" not in before
    assert {item.name for item in path.iterdir()} == {
        files.PACKET_FILENAME,
        files.AUDIT_FILENAME,
        files.MANIFEST_FILENAME,
    }
    manifest = json.loads((path / files.MANIFEST_FILENAME).read_bytes())
    for name, content in (
        (files.PACKET_FILENAME, loaded.packet_bytes),
        (files.AUDIT_FILENAME, loaded.audit_bytes),
    ):
        assert manifest["files"][name] == {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    with pytest.raises(FileExistsError):
        write(tmp_path, bundle)


@pytest.mark.parametrize(
    "name", [files.PACKET_FILENAME, files.AUDIT_FILENAME, files.MANIFEST_FILENAME]
)
def test_missing_or_modified_bundle_member_is_rejected(tmp_path, bundle, name):
    path = write(tmp_path, bundle)
    member = path / name
    original = member.read_bytes()
    member.write_bytes(original + b" ")
    if name != files.MANIFEST_FILENAME:
        with pytest.raises(ValueError, match="hash mismatch"):
            files.read_production_audit_bundle(path)
    member.unlink()
    with pytest.raises(ValueError, match="requires exactly"):
        files.read_production_audit_bundle(path)


@pytest.mark.parametrize("failure", ["fsync", "authorization", "rename", "collision"])
def test_failed_staging_never_publishes_partial_pair(
    tmp_path, bundle, monkeypatch, failure
):
    target = tmp_path / "new-bundle"

    @contextmanager
    def gate():
        assert not target.exists()
        (staging,) = tmp_path.iterdir()
        assert len(tuple(staging.iterdir())) == 3
        if failure == "authorization":
            raise ValueError("test current authorization revoked")
        if failure == "collision":
            target.mkdir()
        yield

    def fail(*args):
        raise OSError("test publication failure")

    if failure == "fsync":
        monkeypatch.setattr(files.os, "fsync", fail)
    elif failure == "rename":
        monkeypatch.setattr(files, "_rename_new_directory", fail)
    with pytest.raises((OSError, ValueError)):
        write(tmp_path, bundle, publication_gate=gate)
    assert tuple(tmp_path.glob("*.staging")) == ()
    if failure == "collision":
        assert tuple(target.iterdir()) == ()
    else:
        assert not target.exists()


@pytest.mark.parametrize(
    "unsafe",
    [
        b'{"secret":"test-sensitive-value"}',
        b'{"raw_payload":"test-sensitive-value"}',
        b'{"schema_version":"one","schema_version":"two"}',
        b'{"number":NaN}',
        b"[" * 150 + b"]" * 150,
        b" " * (MAX_CONTRACT_FILE_BYTES + 1),
    ],
    ids=["secret", "raw", "duplicate", "nonfinite", "nesting", "oversized"],
)
def test_bounded_strict_sanitized_json_boundary(bundle, unsafe):
    _, common = bundle
    packet_bytes = canonical_json(common["packet"].model_dump(mode="json")).encode()
    with pytest.raises(ValueError) as error:
        files.validate_production_audit_pair(packet_bytes, unsafe)
    assert "test-sensitive-value" not in str(error.value)


def test_rehashed_manifest_does_not_allow_untyped_or_unbound_sidecar(tmp_path, bundle):
    path = write(tmp_path, bundle)
    audit_path = path / files.AUDIT_FILENAME
    payload = json.loads(audit_path.read_bytes())
    payload["content_payload"]["release"]["artifact_id"] = "test-tampered-release"
    audit_path.write_text(canonical_json(payload), encoding="utf-8")
    payloads = {
        name: (path / name).read_bytes()
        for name in (files.PACKET_FILENAME, files.AUDIT_FILENAME)
    }
    (path / files.MANIFEST_FILENAME).write_text(
        canonical_json(files._manifest(payloads)), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="packet/audit pair"):
        files.read_production_audit_bundle(path)
