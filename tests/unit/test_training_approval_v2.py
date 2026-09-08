"""SYNTHETIC_CONTRACT_TEST_ONLY. V1 pins captured from c9083e5 before V2 edits."""

import hashlib
from datetime import timedelta

import pytest

from football_system.domain.archive import canonical_json
from football_system.domain.production_release import (
    TRAINING_HISTORY_APPROVAL_PAYLOAD_V2,
    TrainingHistoryApprovalContentV2,
    TrainingHistoryApprovalPayloadV2,
    TrainingHistoryApprovalV1,
    TrainingHistoryApprovalV2,
    ProductionQuantModelReleaseV1,
    approval_technical_evidence_ref,
    parse_training_history_approval,
    revalidate,
    training_approval_request_hash,
)
from tests.unit import test_production_release as legacy


def test_frozen_c9083e5_v1_approval_bytes_hash_and_id():
    approval = legacy._approval(legacy.manifest.__wrapped__())
    encoded = canonical_json(approval).encode("utf-8")
    # Pin exact wire bytes independently of the domain's typed-content hash.
    assert len(encoded) == 6543
    assert hashlib.sha256(encoded).hexdigest() == (
        "deaa84b42d8a4b85a2a470def9c533bd57dbbed5879f93379d903c4be96c244e"
    )
    assert approval.content_payload.approval_payload_hash == (
        "7465765bb9ed323c421598148c6597d3d9c67757d9897feb2db203d28c791db2"
    )
    assert approval.content_hash == (
        "5f1bfdef22c2263f86ea896f7c00649ce509e33feda1e00c31f1ba6b4ffc47dc"
    )
    assert approval.artifact_id == "5541ad85-7193-5aac-8b0f-c4827584abae"
    parsed = TrainingHistoryApprovalV1.model_validate_json(encoded)
    assert parsed == approval
    assert canonical_json(parsed).encode("utf-8") == encoded
    from football_system.infrastructure.database.production_quant_repository import (
        _request,
    )

    request = _request(
        "record_approval",
        "frozen-c9083e5",
        "operator",
        approval_payload=approval.content_payload.approval_payload,
        reviewer_attestation=approval.content_payload.reviewer_attestation,
    )
    assert len(request["request_json"].encode()) == 6347
    assert hashlib.sha256(request["request_json"].encode()).hexdigest() == (
        "264043d86f430edabbb7ff39c7cc5b69210be6f1175ae4a27cd67caf0d3439ff"
    )
    assert request["request_sha256"] == (
        "0d596dca48eabd5409ca54b3275f3d7f0ef846221f8b2e06baf7844780b621b9"
    )


def subject_v2():
    old = legacy._approval(
        legacy.manifest.__wrapped__()
    ).content_payload.approval_payload
    return TrainingHistoryApprovalPayloadV2.model_validate(
        {
            **old.model_dump(
                exclude={
                    "payload_version",
                    "approved_at_utc",
                    "persisted_at_utc",
                    "technical_evidence",
                }
            ),
            "technical_evidence": approval_technical_evidence_ref(
                old.technical_evidence
            ),
        }
    )


def approval_v2():
    payload = subject_v2()
    review = legacy._attest(
        payload, TRAINING_HISTORY_APPROVAL_PAYLOAD_V2, legacy.APPROVAL_AT
    )
    return TrainingHistoryApprovalV2.freeze(
        content_payload=TrainingHistoryApprovalContentV2(
            approval_payload=payload,
            approval_payload_hash=payload.approval_payload_hash,
            reviewer_attestation=review,
            operator_id="TEST_ONLY_REVIEWER",
            request_key="test-record",
            request_sha256=training_approval_request_hash(
                "test-record", "TEST_ONLY_REVIEWER", payload, review
            ),
            recorded_at_utc=legacy.APPROVAL_AT + timedelta(seconds=1),
            persisted_at_utc=legacy.APPROVAL_AT + timedelta(seconds=2),
        )
    )


def test_v2_subject_has_only_policy_times_and_accessors_do_not_serialize():
    approval = approval_v2()
    wire = canonical_json(approval.subject)
    assert all(
        key not in wire
        for key in ("approved_at_utc", "recorded_at_utc", "persisted_at_utc", "actual_")
    )
    assert "effective_at_utc" in wire and "retain_until_at_utc" in wire
    assert "subject" not in approval.model_dump()
    assert "recorded_at_utc" not in approval.model_dump()
    assert approval.recorded_at_utc < approval.persisted_at_utc
    legacy_approval = legacy._approval(legacy.manifest.__wrapped__())
    for item in (legacy_approval, approval):
        assert parse_training_history_approval(item.model_dump(mode="json")) == item
        assert item.subject == item.content_payload.approval_payload


def test_scope_spelling_cannot_create_another_fixed_model_authorization():
    values = subject_v2().model_dump(mode="json")
    values["scope"]["model"]["config"]["home_advantage"] = "100.0"
    with pytest.raises(ValueError, match="exact fixed Elo config"):
        TrainingHistoryApprovalPayloadV2.model_validate(values)


@pytest.mark.parametrize(
    "field",
    [
        "approved_at_utc",
        "recorded_at_utc",
        "persisted_at_utc",
        "raw_evidence",
        "raw_source",
        "operator_id",
    ],
)
def test_subject_rejects_recording_observations_and_raw_materials(field):
    with pytest.raises(ValueError, match="Extra inputs"):
        TrainingHistoryApprovalPayloadV2.model_validate(
            subject_v2().model_dump() | {field: "not allowed"}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_payload_hash", "0" * 64),
        ("operator_id", "another-operator"),
        ("request_key", "another-key"),
        ("request_sha256", "0" * 64),
        ("recorded_at_utc", legacy.APPROVAL_AT - timedelta(seconds=1)),
        ("persisted_at_utc", legacy.APPROVAL_AT),
    ],
)
def test_v2_resealed_content_requires_exact_request_and_actual_timeline(field, value):
    approval = approval_v2()
    with pytest.raises(ValueError):
        TrainingHistoryApprovalV2.freeze(
            content_payload=approval.content_payload.model_copy(update={field: value})
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "TRAINING_HISTORY_APPROVAL_V1"),
        ("schema_version", "TRAINING_HISTORY_APPROVAL_V3"),
        ("content_hash", "0" * 64),
        ("artifact_id", "other"),
    ],
)
def test_parser_never_switches_schema_or_accepts_old_seal(field, value):
    with pytest.raises(ValueError):
        parse_training_history_approval(
            approval_v2().model_dump(mode="json") | {field: value}
        )


def test_actual_observations_change_final_seal_not_review_subject_or_evidence():
    original = approval_v2()
    changed = TrainingHistoryApprovalV2.freeze(
        content_payload=original.content_payload.model_copy(
            update={
                "recorded_at_utc": original.recorded_at_utc + timedelta(seconds=10),
                "persisted_at_utc": original.persisted_at_utc + timedelta(seconds=10),
            }
        )
    )
    assert changed.content_hash != original.content_hash
    assert changed.artifact_id != original.artifact_id
    assert (
        changed.subject.approval_payload_hash == original.subject.approval_payload_hash
    )
    assert canonical_json(
        changed.content_payload.reviewer_attestation
    ) == canonical_json(original.content_payload.reviewer_attestation)
    assert (
        changed.content_payload.request_sha256
        == original.content_payload.request_sha256
    )
    with pytest.raises(ValueError, match="content hash mismatch"):
        revalidate(
            original.model_copy(update={"content_payload": changed.content_payload})
        )


def test_v1_review_cannot_be_replayed_or_rebound_as_v2():
    original = approval_v2()
    old = legacy._approval(
        legacy.manifest.__wrapped__()
    ).content_payload.reviewer_attestation
    with pytest.raises(ValueError, match="exact V2 subject"):
        TrainingHistoryApprovalV2.freeze(
            content_payload=original.content_payload.model_copy(
                update={"reviewer_attestation": old}
            )
        )


def test_v2_release_union_and_v3_audit_use_common_accessors():
    release = legacy._build(legacy.manifest.__wrapped__(), approval=approval_v2())
    assert (
        ProductionQuantModelReleaseV1.model_validate_json(canonical_json(release))
        == release
    )
    audit, common = legacy._packet_bundle(release)
    assert audit.content_payload.approval == release.training_approval.reference()
    assert "TRAINING_HISTORY_APPROVAL_V2" not in canonical_json(common["packet"])
