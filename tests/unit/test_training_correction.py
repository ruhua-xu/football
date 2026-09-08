from datetime import datetime, timezone

import pytest

from football_system.domain.training_correction import (
    CorrectionComponent,
    CorrectionRefV2,
    CorrectionStreamV2,
    SourceCorrectionV2,
    TrainingCorrectionIntentV2,
    TrainingCorrectionContextV2,
    TrainingFactVersionV2,
)


def test_typed_compatibility_references_cannot_alias_root_pointers():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def evidence(kind):
        return SourceCorrectionV2(
            transition=CorrectionRefV2(
                schema_version="CORRECTION",
                artifact_id="transition",
                content_hash="1" * 64,
            ),
            stream=CorrectionStreamV2(
                source_id="source",
                provider_code="provider",
                provider_fixture_namespace="fixture",
                provider_fixture_key="f",
                internal_match_id="match",
            ),
            revision_sequence=1,
            component=CorrectionComponent.FIXTURE,
            predecessor_version=CorrectionRefV2(
                schema_version="BASE", artifact_id="base", content_hash="2" * 64
            ),
            successor_version=CorrectionRefV2(
                schema_version="CORRECTION",
                artifact_id="transition",
                content_hash="1" * 64,
            ),
            predecessor=CorrectionRefV2(
                schema_version=kind, artifact_id="ROOT", content_hash="3" * 64
            ),
            successor=CorrectionRefV2(
                schema_version=kind, artifact_id="next", content_hash="4" * 64
            ),
            predecessor_source_available_at_utc=at,
            source_available_at_utc=at,
            local_imported_at_utc=at,
            registered_at_utc=at,
        )

    left, right = evidence("FIXTURE_A").as_v1(), evidence("FIXTURE_B").as_v1()
    assert left.content_payload.predecessor.artifact_id != "ROOT"
    assert left.content_payload.predecessor != right.content_payload.predecessor


def test_new_intent_does_not_accept_predecessor_validated_flag():
    with pytest.raises(ValueError):
        TrainingCorrectionIntentV2.model_validate({"predecessor_validated": True})


def test_core_selector_does_not_treat_partial_reference_hashes_as_snapshot_seals():
    from .test_versioned_training_history import base, context, NOW

    partial = context(base())
    with pytest.raises(ValueError, match="authoritative snapshot artifact links"):
        partial.select(source_cutoffs={"synthetic-source": NOW})


@pytest.mark.parametrize(
    "change",
    [
        {"revision_sequence": 999, "predecessor": None},
        {"components": ()},
        {"latest_match_result_id": "unregistered"},
    ],
)
def test_version_validation_revalidates_model_copy_instead_of_trusting_instances(
    change,
):
    from .test_versioned_training_history import base

    forged = base().model_copy(update=change)
    with pytest.raises(ValueError):
        TrainingFactVersionV2.model_validate(forged)


def test_context_requires_backward_only_complete_refs_and_events():
    from .test_versioned_training_history import base, successor, context

    original = base()
    revised, events = successor(original)
    value = context(original, revised, events=events)
    for changes in (
        {"versions": (revised, original)},
        {"corrections": ()},
        {"actual_at_utc": original.registered_at_utc},
    ):
        with pytest.raises(ValueError):
            TrainingCorrectionContextV2.model_validate(value.model_copy(update=changes))
