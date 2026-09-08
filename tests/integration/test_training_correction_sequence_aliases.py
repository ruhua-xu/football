"""Public-writer subset/reordering aliases, with exact evidence and ref closure."""

import pytest
from sqlalchemy import select

from football_system.domain.training_admission import (
    MatchResultAdmissionV1,
    TrainingFactBindingV1,
)
from football_system.domain.training_correction import (
    CorrectionRefV2,
    base_component_bindings,
    source_version_reference,
)
from football_system.domain.versioned_training_history import validate_versioned_context
from football_system.infrastructure.database.models import TrainingFactBindingRecord
from football_system.infrastructure.database.training_correction_repository import (
    STREAMS,
)

from . import test_training_correction_findings as findings
from . import test_training_corrections as cases
from . import test_corrected_quant_workflow as workflow

lane = findings.lane
two_streams = findings.two_streams
production = workflow.production
corrected = workflow.corrected


@pytest.mark.parametrize("shape", ["subset", "reordered", "reordered-then-subset"])
def test_public_subset_and_reordering_keep_distinct_pins_but_one_source_version(
    two_streams, shape
):
    ctx = two_streams
    original = ctx.other  # Admission A's second fact, local sequence 1.
    fact_a = ctx.base_admission.facts[1]
    wire_a = ctx.base_admission.model_dump_json()
    if shape == "reordered-then-subset":
        cases.admit(
            ctx,
            key="reordered-intermediate",
            submissions=tuple(reversed(ctx.submissions)),
        )
    submissions = (
        tuple(reversed(ctx.submissions))
        if shape == "reordered"
        else (ctx.submissions[1],)
    )
    admission_b = cases.admit(ctx, key="admission-b", submissions=submissions)
    pin_b = findings.pin(admission_b)
    fact_b = next(
        f
        for f in admission_b.facts
        if f.content_payload.normalized_result.match_id
        == original.snapshot.stream.internal_match_id
    )
    assert fact_a.content_payload.sequence == 1
    assert fact_b.content_payload.sequence == (1 if shape == "reordered" else 0)
    assert (fact_a.fact_hash == fact_b.fact_hash) == (shape == "reordered")
    assert source_version_reference(fact_a) == source_version_reference(fact_b)
    second = next(
        v
        for v in ctx.corrections.load_context(
            base_admissions=(pin_b,), correction_ids=(), actual_at=ctx.clock()
        ).versions
        if v.snapshot.stream == original.snapshot.stream
    )
    assert second.reference != original.reference
    assert second.components == original.components
    wire_b = admission_b.model_dump_json()
    corrected = cases.record(
        ctx, cases.reviewed_intent(ctx, previous=second, raw={"home_goals": 5})
    )
    assert ctx.corrections.load_version(original.version_id) == original
    assert ctx.corrections.load_version(second.version_id) == second
    assert ctx.repo.load(ctx.base_pin.artifact_id).model_dump_json() == wire_a
    assert ctx.repo.load(pin_b.artifact_id).model_dump_json() == wire_b
    old_context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,), correction_ids=(), actual_at=ctx.clock()
    )
    assert old_context.versions == (ctx.base, original)
    validate_versioned_context(old_context)
    assert old_context.select(source_cutoffs={"source": old_context.actual_at_utc})
    events_a = ctx.corrections.current_invalidations(
        base_admissions=(ctx.base_pin,), actual_at=ctx.clock()
    )
    events_b = ctx.corrections.current_invalidations(
        base_admissions=(pin_b,), actual_at=ctx.clock()
    )
    assert events_a == events_b
    assert (
        events_a[0].predecessor
        == original.components[-1].reference
        == second.components[-1].reference
    )
    assert (
        events_a[0].as_v1().content_payload.predecessor
        == original.components[-1].reference.as_v1()
    )
    complete = ctx.corrections.load_context(
        base_admissions=(pin_b,),
        correction_ids=(corrected.artifact_id,),
        actual_at=ctx.clock(),
    )
    assert complete.versions[-1].predecessor == second.reference
    validate_versioned_context(complete)
    with pytest.raises(ValueError, match="complete exact predecessor"):
        ctx.corrections.load_context(
            base_admissions=(ctx.base_pin,),
            correction_ids=(corrected.artifact_id,),
            actual_at=ctx.clock(),
        )
    with pytest.raises(ValueError, match="predecessor.*head"):
        cases.reviewed_intent(ctx, previous=original)
    with ctx.sessions() as session:
        root = session.execute(select(STREAMS)).mappings().one()
        assert root["base_admission_id"] == pin_b.artifact_id
        assert root["base_binding_id"] == fact_b.training_fact_binding_id
        for parent, fact in ((ctx.base_admission, fact_a), (admission_b, fact_b)):
            stored = session.scalar(
                select(TrainingFactBindingRecord).where(
                    TrainingFactBindingRecord.training_fact_admission_id
                    == parent.training_fact_admission_id,
                    TrainingFactBindingRecord.training_fact_binding_id
                    == fact.training_fact_binding_id,
                )
            )
            assert (
                TrainingFactBindingV1.model_validate_json(stored.artifact_json) == fact
            )


def test_domain_context_checks_sequence_free_content_not_just_snapshot_or_match(
    two_streams,
):
    ctx = two_streams
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,), correction_ids=(), actual_at=ctx.clock()
    )
    original = ctx.base_admission.facts[1]
    subset = TrainingFactBindingV1.freeze(
        content_payload=original.content_payload.model_copy(update={"sequence": 0})
    )
    subset_ref = CorrectionRefV2(
        schema_version=subset.schema_version,
        artifact_id=subset.training_fact_binding_id,
        content_hash=subset.fact_hash,
    )
    alias_components = base_component_bindings(subset_ref, subset)
    equivalent = context.model_copy(
        update={
            "versions": (
                context.versions[0],
                context.versions[1].model_copy(update={"components": alias_components}),
            )
        }
    )
    assert equivalent.select(source_cutoffs={"source": ctx.clock()})
    # Adapter version is absent from the partial CorrectionSnapshotV2, but is
    # committed by the full V1 result admission and must not disappear here.
    result = MatchResultAdmissionV1.freeze(
        content_payload=subset.content_payload.match_result_admission.content_payload.model_copy(
            update={"adapter_version": "2"}
        )
    )
    changed = TrainingFactBindingV1.freeze(
        content_payload=subset.content_payload.model_copy(
            update={"match_result_admission": result}
        )
    )
    changed_ref = CorrectionRefV2(
        schema_version=changed.schema_version,
        artifact_id=changed.training_fact_binding_id,
        content_hash=changed.fact_hash,
    )
    wrong_components = base_component_bindings(changed_ref, changed)
    forged = context.model_copy(
        update={
            "versions": (
                context.versions[0],
                context.versions[1].model_copy(update={"components": wrong_components}),
            )
        }
    )
    with pytest.raises(ValueError, match="authoritative fact artifact"):
        forged.select(source_cutoffs={"source": ctx.clock()})
    # Sequence-independent source identity never substitutes for the V1 audit FK.
    with pytest.raises(ValueError, match="base fact artifact reference"):
        context.model_copy(
            update={
                "versions": (
                    context.versions[0],
                    context.versions[1].model_copy(update={"base_binding": subset_ref}),
                )
            }
        ).select(source_cutoffs={"source": ctx.clock()})


def test_repository_alias_check_retains_full_submission_review_evidence(two_streams):
    ctx = two_streams
    item = ctx.submissions[1]
    document = cases.strict_json_bytes(
        ctx.repo.evidence.read(item.reviewer_evidence.evidence_reference)
    )
    document["prepared_by"] = "another-preparer"
    changed_review = cases.write_evidence(ctx.root, "different-review.json", document)
    admission_b = cases.admit(
        ctx,
        key="different-evidence-admission",
        submissions=(item.model_copy(update={"reviewer_evidence": changed_review}),),
    )
    base_b = ctx.corrections.load_context(
        base_admissions=(findings.pin(admission_b),),
        correction_ids=(),
        actual_at=ctx.clock(),
    ).versions[0]
    assert source_version_reference(
        ctx.base_admission.facts[1]
    ) == source_version_reference(admission_b.facts[0])
    assert base_b.snapshot == ctx.other.snapshot
    with ctx.sessions.begin() as session:
        with pytest.raises(ValueError, match="registered stream fact or evidence"):
            ctx.corrections._assert_base_alias(session, ctx.other, base_b)


def test_subset_root_b_invalidates_old_release_a_and_new_corrected_release_remains_current(
    corrected,
):
    ctx, lane = corrected, corrected.lane
    old = workflow.legacy.build(ctx, workflow.record_v2(ctx))
    old_wire = old.model_dump_json()
    admission_a = ctx.admission
    old_context = lane.corrections.load_context(
        base_admissions=(lane.base_pin,), correction_ids=(), actual_at=lane.clock()
    )
    old_second = old_context.versions[1]
    admission_b = cases.admit(
        lane, key="subset-base-b", submissions=(lane.submissions[1],)
    )
    ctx.admission = admission_b
    lane.base_pin = findings.pin(admission_b)
    lane.base = lane.corrections.load_context(
        base_admissions=(lane.base_pin,), correction_ids=(), actual_at=lane.clock()
    ).versions[0]
    assert admission_a.facts[1].fact_hash != admission_b.facts[0].fact_hash
    assert old_second.components == lane.base.components
    correction = cases.record(
        lane, cases.reviewed_intent(lane, raw={"home_goals": 0, "away_goals": 4})
    )
    assert ctx.repo.load_release(old.artifact_id).model_dump_json() == old_wire
    assert lane.corrections.load_version(old_second.version_id) == old_second
    events = lane.corrections.current_invalidations(
        base_admissions=(findings.pin(admission_a),), actual_at=lane.clock()
    )
    assert events[0].predecessor == old_second.components[-1].reference
    with pytest.raises(ValueError, match="correction"):
        ctx.repo.authorization(old.artifact_id, lane.clock())
    with pytest.raises(ValueError, match="correction"):
        workflow.legacy.build(ctx, old.training_approval, key="stale-release-a")
    manifest = workflow.corrected_manifest(
        ctx, (correction.artifact_id,), key="subset-corrected-manifest"
    )
    approval = workflow.record_v2(
        ctx,
        manifest,
        key="subset-corrected-approval",
        predecessor=old.training_approval,
    )
    current = workflow.legacy.build(ctx, approval, key="subset-corrected-release")
    assert ctx.repo.load_release(current.artifact_id) == current
    assert ctx.repo.load_release(old.artifact_id).model_dump_json() == old_wire
    assert (
        current.content_payload.release_facts[0].content_payload.version.base_admission
        == lane.base_pin
    )
    assert (
        current.content_payload.release_facts[0].content_payload.version.version_id
        == correction.artifact_id
    )
    assert ctx.repo.authorization(current.artifact_id, lane.clock()).corrections
    assert lane.repo.load(admission_a.training_fact_admission_id) == admission_a
    assert lane.repo.load(admission_b.training_fact_admission_id) == admission_b
