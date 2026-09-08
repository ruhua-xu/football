"""Synthetic V1 content seals; no source acquisition or production authority."""

from datetime import timedelta

import pytest

from football_system.domain.training_admission import (
    MatchResultAdmissionV1,
    TrainingFactBindingV1,
    tagged_canonical_sha256,
)
from football_system.domain.training_correction import (
    CorrectionRefV2,
    base_component_bindings,
    source_version_reference,
)
from .test_quant_integrity import _fact


def binding_ref(fact):
    return CorrectionRefV2(
        schema_version=fact.schema_version,
        artifact_id=fact.training_fact_binding_id,
        content_hash=fact.fact_hash,
    )


@pytest.mark.parametrize("sequence", [0, 1, 17])
def test_source_version_excludes_only_top_level_sequence_and_preserves_v1_seals(
    sequence,
):
    original = _fact("match", sequence=3)
    wire = original.model_dump_json()
    reordered = TrainingFactBindingV1.freeze(
        content_payload=original.content_payload.model_copy(
            update={"sequence": sequence}
        )
    )
    assert original.fact_hash != reordered.fact_hash
    assert original.training_fact_binding_id != reordered.training_fact_binding_id
    assert source_version_reference(original) == source_version_reference(reordered)
    assert source_version_reference(original).content_hash == tagged_canonical_sha256(
        "TRAINING_SOURCE_FACT_VERSION_V2",
        original.content_payload.model_dump(mode="python", exclude={"sequence"}),
    )
    assert base_component_bindings(
        binding_ref(original), original
    ) == base_component_bindings(binding_ref(reordered), reordered)
    assert original.model_dump_json() == wire
    assert TrainingFactBindingV1.model_validate_json(wire) == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("adapter_name", "different-adapter"),
        ("adapter_version", "2"),
        ("status_mapping_version", "different-status-mapping"),
        ("provider_raw_status", "EXPLICIT_FINAL"),
        ("reviewed_by", "different-reviewer"),
        ("raw_artifact_id", "different-capture"),
        ("raw_artifact_payload_sha256", "f" * 64),
        ("raw_record_sha256", "f" * 64),
        ("reviewed_at_utc", "later"),
    ],
)
def test_source_version_retains_true_content_hash_status_and_review_changes(
    field, value
):
    original = _fact("match", sequence=1)
    content = original.content_payload.match_result_admission.content_payload
    if value == "later":
        value = content.reviewed_at_utc + timedelta(seconds=1)
    result = MatchResultAdmissionV1.freeze(
        content_payload=content.model_copy(update={field: value})
    )
    changed = TrainingFactBindingV1.freeze(
        content_payload=original.content_payload.model_copy(
            update={"sequence": 0, "match_result_admission": result}
        )
    )
    assert (
        changed.content_payload.normalized_result
        == original.content_payload.normalized_result
    )
    assert source_version_reference(changed) != source_version_reference(original)
    assert base_component_bindings(
        binding_ref(changed), changed
    ) != base_component_bindings(binding_ref(original), original)


def test_canonical_identity_is_part_of_source_version_even_when_score_and_match_id_match():
    original = _fact("match", sequence=1)
    changed = _fact("match", sequence=0, home="different-canonical-team")
    assert (
        original.content_payload.normalized_result
        == changed.content_payload.normalized_result
    )
    assert source_version_reference(original) != source_version_reference(changed)


def test_unsealed_sequence_edit_and_caller_binding_refs_are_not_proof():
    original = _fact("match", sequence=1)
    unsealed = original.model_copy(
        update={
            "content_payload": original.content_payload.model_copy(
                update={"sequence": 0}
            )
        }
    )
    with pytest.raises(ValueError, match="hash"):
        source_version_reference(unsealed)
    wrong_ref = binding_ref(original).model_copy(update={"artifact_id": "caller-ref"})
    with pytest.raises(ValueError, match="exact V1 binding pin"):
        base_component_bindings(wrong_ref, original)
