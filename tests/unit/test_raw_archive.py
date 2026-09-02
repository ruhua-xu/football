import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from football_system.domain.archive import canonical_json
from football_system.domain.raw_data import (
    ProviderRequestAudit,
    ProviderRequestFailureCode,
    ProviderRequestOutcome,
    ProviderRequestResult,
    RawArtifactMetadata,
)
from football_system.infrastructure.files.raw_archive import (
    RawArchiveCollisionError,
    RawDataArchive,
)

REQUESTED = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
RECEIVED = REQUESTED + timedelta(milliseconds=125)
PAYLOAD = b'\x00{"data":[1,2,3]}\xff'


def _metadata(payload: bytes = PAYLOAD, **updates: object) -> RawArtifactMetadata:
    values: dict[str, object] = {
        "provider": "SPORTMONKS",
        "endpoint": "/v3/football/fixtures",
        "requested_at_utc": REQUESTED,
        "received_at_utc": RECEIVED,
        "available_at_utc": REQUESTED - timedelta(minutes=1),
        "request_parameters": {"include": "participants", "page": 2},
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "http_status": 200,
        "provider_request_id": "provider-request-1",
        "duration_ms": 125,
        "outcome": ProviderRequestOutcome.SUCCESS,
        "failure_code": None,
    }
    values.update(updates)
    return RawArtifactMetadata.model_validate(values)


def test_archive_writes_hashed_payload_and_canonical_metadata_idempotently(
    tmp_path: Path,
) -> None:
    archive = RawDataArchive(tmp_path / "raw")
    metadata = _metadata()

    artifact = archive.write(PAYLOAD, metadata)
    payload_stat = artifact.payload_path.stat()
    metadata_stat = artifact.metadata_path.stat()
    repeated = archive.write(PAYLOAD, metadata)

    expected_directory = (tmp_path / "raw").resolve() / "SPORTMONKS" / "2026-09-02"
    expected_metadata = canonical_json(metadata).encode("utf-8")
    assert artifact == repeated
    assert artifact.artifact_id == hashlib.sha256(expected_metadata).hexdigest()
    assert artifact.payload_path.parent == expected_directory
    assert artifact.metadata_path.parent == expected_directory
    assert artifact.payload_path.read_bytes() == PAYLOAD
    assert artifact.metadata_path.read_bytes() == expected_metadata
    assert artifact.payload_path.stat().st_mtime_ns == payload_stat.st_mtime_ns
    assert artifact.metadata_path.stat().st_mtime_ns == metadata_stat.st_mtime_ns
    assert (
        json.loads(expected_metadata)["payload_sha256"]
        == hashlib.sha256(PAYLOAD).hexdigest()
    )
    assert not any(
        path.suffix == ".tmp" or path.suffix == ".lock"
        for path in expected_directory.iterdir()
    )


def test_archive_rejects_hash_mismatch_and_never_overwrites_collision(
    tmp_path: Path,
) -> None:
    archive = RawDataArchive(tmp_path / "raw")
    metadata = _metadata()

    with pytest.raises(ValueError, match="SHA-256"):
        archive.write(b"different", metadata)

    artifact = archive.write(PAYLOAD, metadata)
    artifact.payload_path.write_bytes(b"tampered")

    with pytest.raises(RawArchiveCollisionError, match="overwrite"):
        archive.write(PAYLOAD, metadata)
    assert artifact.payload_path.read_bytes() == b"tampered"


def test_archive_rejects_path_traversal_even_for_unvalidated_metadata(
    tmp_path: Path,
) -> None:
    archive = RawDataArchive(tmp_path / "raw")
    unsafe = _metadata().model_copy(update={"provider": "../../outside"})

    with pytest.raises(ValueError, match="safe"):
        archive.write(PAYLOAD, unsafe)

    assert not (tmp_path / "outside").exists()
    assert tuple(archive.root.iterdir()) == ()


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"payload_sha256": "A" * 64}, "payload_sha256"),
        (
            {"received_at_utc": REQUESTED - timedelta(seconds=1)},
            "requested_at_utc",
        ),
        (
            {"available_at_utc": RECEIVED + timedelta(seconds=1)},
            "available_at_utc",
        ),
        ({"http_status": 500}, "2xx"),
        (
            {
                "outcome": ProviderRequestOutcome.ERROR,
                "failure_code": None,
                "http_status": None,
                "available_at_utc": None,
            },
            "failure_code",
        ),
    ),
)
def test_raw_metadata_enforces_hash_time_and_outcome_invariants(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _metadata(**updates)


@pytest.mark.parametrize(
    "request_parameters",
    (
        {"api_key": "not-recorded"},
        {"filter": "Bearer should-never-be-recorded"},
        {"nested": {"client-secret": "not-recorded"}},
    ),
)
def test_raw_metadata_rejects_secret_like_names_and_values_without_echoing_them(
    request_parameters: dict[str, object],
) -> None:
    marker = next(
        value
        for value in ("not-recorded", "should-never-be-recorded")
        if marker_in_parameters(request_parameters, value)
    )

    with pytest.raises(ValidationError) as error:
        _metadata(request_parameters=request_parameters)

    assert marker not in str(error.value)


def test_provider_result_distinguishes_empty_success_from_network_failure() -> None:
    successful_audit = ProviderRequestAudit.model_validate(
        _metadata(b"").model_dump(mode="python", exclude={"payload_sha256"})
    )
    success = ProviderRequestResult(audit=successful_audit, payload=b"")
    timeout_audit = successful_audit.model_copy(
        update={
            "available_at_utc": None,
            "http_status": None,
            "outcome": ProviderRequestOutcome.ERROR,
            "failure_code": ProviderRequestFailureCode.TIMEOUT,
        }
    )
    failure = ProviderRequestResult(audit=timeout_audit, payload=None)

    assert success.succeeded is True
    assert (
        success.to_raw_artifact_metadata().payload_sha256
        == hashlib.sha256(b"").hexdigest()
    )
    assert failure.succeeded is False
    assert failure.payload is None
    with pytest.raises(ValueError, match="no raw artifact"):
        failure.to_raw_artifact_metadata()
    with pytest.raises(ValidationError, match="cannot expose payload"):
        ProviderRequestResult(audit=timeout_audit, payload=b"")
    with pytest.raises(ValidationError, match="frozen"):
        success.payload = b"changed"


def marker_in_parameters(value: object, marker: str) -> bool:
    if isinstance(value, dict):
        return any(
            marker_in_parameters(key, marker) or marker_in_parameters(item, marker)
            for key, item in value.items()
        )
    return isinstance(value, str) and marker in value
