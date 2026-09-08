"""Regression coverage for independent controlled-correction integrity findings."""

import pytest
from sqlalchemy.exc import IntegrityError

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.training_admission import TrainingFactAdmissionV1
from football_system.domain.training_correction import (
    CorrectionRefV2,
    TrainingCorrectionAdmissionV2,
    TrainingCorrectionContentV2,
    TrainingCorrectionContextV2,
)
from football_system.infrastructure.database.models import MatchResultRecord
from football_system.infrastructure.database import training_admission_repository as v1
from football_system.infrastructure.database.training_correction_repository import (
    ADMISSIONS,
    COMPONENTS,
    RESULTS,
    SqlAlchemyTrainingCorrectionRepository,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes

from . import test_training_corrections as cases

lane = cases.lane
corrected_lane = cases.corrected_lane


@pytest.fixture
def two_streams(lane):
    cases.prepare(lane, count=2)
    lane.base_admission = cases.admit(lane)
    lane.corrections = SqlAlchemyTrainingCorrectionRepository(lane.repo)
    permission = strict_json_bytes(lane.repo.evidence.read("authority.json"))
    permission["attested_schema_versions"] = [cases.CORRECTION_INTENT_V2]
    lane.correction_authority = cases.write_evidence(
        lane.root, "correction-authority.json", permission
    )
    lane.repo.evidence.trusted_authorities["correction-authority.json"] = (
        lane.correction_authority.evidence_sha256
    )
    lane.base_pin = pin(lane.base_admission)
    lane.base, lane.other = lane.corrections.load_context(
        base_admissions=(lane.base_pin,), correction_ids=(), actual_at=lane.clock()
    ).versions
    lane.next_correction = 0
    return lane


def pin(admission):
    return CorrectionRefV2(
        schema_version=admission.schema_version,
        artifact_id=admission.training_fact_admission_id,
        content_hash=admission.admission_hash,
    )


def withdrawal(ctx, **kwargs):
    return cases.reviewed_intent(
        ctx,
        raw={
            "status": "CANCELLED",
            "result_key": "shared-key",
            "home_goals": None,
            "away_goals": None,
            "finalized_at_utc": None,
        },
        trainable=False,
        **kwargs,
    )


def test_withdrawal_reserves_its_logical_key_without_a_normalized_result(two_streams):
    ctx = two_streams
    first = cases.record(ctx, withdrawal(ctx))
    before = cases.counts(ctx)
    with pytest.raises(ValueError, match="logical result key"):
        cases.reviewed_intent(ctx, previous=ctx.other, raw={"result_key": "shared-key"})
    assert cases.counts(ctx) == before
    assert first.content_payload.normalized_result is None
    assert ctx.corrections.load_admission(first.artifact_id) == first
    assert ctx.corrections.load_version(first.artifact_id).normalized_result is None
    assert before["match_results"] == 2


def test_key_collision_is_rechecked_after_two_valid_previews(two_streams):
    ctx = two_streams
    first = withdrawal(ctx)
    second = cases.reviewed_intent(
        ctx, previous=ctx.other, raw={"result_key": "shared-key"}
    )
    admitted = cases.record(ctx, first)
    before = cases.counts(ctx)
    with pytest.raises(ValueError, match="logical result key"):
        cases.record(ctx, second)
    assert cases.counts(ctx) == before
    assert ctx.corrections.load_admission(admitted.artifact_id) == admitted


def test_old_withdrawal_key_stays_reserved_after_its_stream_changes_keys(two_streams):
    ctx = two_streams
    first = cases.record(ctx, withdrawal(ctx))
    old_head = ctx.corrections.load_version(first.artifact_id)
    restored = cases.record(
        ctx,
        cases.reviewed_intent(ctx, previous=old_head, raw={"result_key": "next-key"}),
    )
    with pytest.raises(ValueError, match="logical result key"):
        cases.reviewed_intent(ctx, previous=ctx.other, raw={"result_key": "shared-key"})
    assert ctx.corrections.load_admission(first.artifact_id) == first
    assert ctx.corrections.load_admission(restored.artifact_id) == restored


def test_new_v1_write_after_withdrawal_fails_but_exact_retry_and_audit_survive(
    corrected_lane,
):
    ctx = corrected_lane
    old_json = ctx.base_admission.model_dump_json()
    cases.record(ctx, withdrawal(ctx))
    before = cases.counts(ctx)
    with pytest.raises(ValueError, match="new V1 admission.*corrected stream"):
        cases.admit(ctx, key="new-request-for-old-score")
    assert cases.admit(ctx).model_dump_json() == old_json
    assert ctx.repo.load(ctx.base_pin.artifact_id).model_dump_json() == old_json
    assert ctx.corrections.load_version(ctx.base.version_id) == ctx.base
    assert cases.counts(ctx) == before


def test_established_revision_order_cannot_be_lost_before_an_equal_time_reset(
    corrected_lane,
):
    ctx = corrected_lane
    first = cases.record(ctx, cases.reviewed_intent(ctx, raw={"revision_order": 10}))
    current = ctx.corrections.load_version(first.artifact_id)
    with pytest.raises(ValueError, match="revision order.*cannot disappear"):
        cases.reviewed_intent(ctx, previous=current, revision_fields=False)
    with pytest.raises(ValueError, match="revision order"):
        cases.reviewed_intent(
            ctx, previous=current, equal_time=True, raw={"revision_order": 2}
        )
    assert ctx.corrections.load_admission(first.artifact_id) == first
    assert cases.counts(ctx)["training_correction_admissions"] == 1


def test_distinct_genuine_base_admission_pins_survive_the_registered_root_choice(
    corrected_lane,
):
    ctx = corrected_lane
    original = ctx.base
    second_admission = cases.admit(ctx, key="genuine-readmission")
    second_pin = pin(second_admission)
    second = ctx.corrections.load_context(
        base_admissions=(second_pin,), correction_ids=(), actual_at=ctx.clock()
    ).versions[0]
    assert second.base_binding == original.base_binding
    assert second.version_id != original.version_id
    assert second.components == original.components
    correction = cases.record(ctx, cases.reviewed_intent(ctx, previous=second))
    assert ctx.corrections.load_version(original.version_id) == original
    assert ctx.corrections.load_version(second.version_id) == second
    assert ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,), correction_ids=(), actual_at=ctx.clock()
    ).versions == (original,)
    assert ctx.repo.load(ctx.base_pin.artifact_id) == ctx.base_admission
    assert ctx.repo.load(second_pin.artifact_id) == second_admission
    assert (
        ctx.corrections.load_context(
            base_admissions=(second_pin,),
            correction_ids=(correction.artifact_id,),
            actual_at=ctx.clock(),
        )
        .versions[-1]
        .version_id
        == correction.artifact_id
    )
    with pytest.raises(ValueError, match="predecessor.*head"):
        cases.reviewed_intent(ctx, previous=original)
    with pytest.raises(ValueError, match="complete exact predecessor"):
        ctx.corrections.load_context(
            base_admissions=(ctx.base_pin,),
            correction_ids=(correction.artifact_id,),
            actual_at=ctx.clock(),
        )
    event = ctx.corrections.current_invalidations(
        base_admissions=(ctx.base_pin,), actual_at=ctx.clock()
    )[0]
    assert event.predecessor == original.components[-1].reference


def sealed_sql_candidate(ctx, previous, intent, review):
    """Actual fixture evidence, bypassing only the public writer to test SQL guards."""
    document = strict_json_bytes(
        ctx.repo.evidence.read(review.evidence_reference, review.evidence_sha256)
    )
    components, normalized = ctx.corrections._projections(previous, intent)
    captures = [
        ctx.repo.load_capture(ref.capture_receipt_id)[0]
        for ref in (
            intent.evidence.fixture,
            intent.evidence.scope,
            intent.evidence.result,
        )
    ]
    return TrainingCorrectionAdmissionV2.freeze(
        content_payload=TrainingCorrectionContentV2(
            intent=intent,
            reviewer_evidence=review,
            reviewer_authority=ctx.correction_authority,
            reviewed_by=document["authorized_reviewer"],
            reviewed_at_utc=document["reviewed_at_utc"],
            actual_started_at_utc=ctx.clock(),
            actual_completed_at_utc=ctx.clock(),
            registered_at_utc=ctx.clock(),
            local_imported_at_utc=max(c.local_imported_at_utc for c in captures),
            components=components,
            normalized_result=normalized,
            previous_match_result_id=previous.latest_match_result_id,
        )
    )


def insert_sql_candidate(ctx, previous, value):
    content = value.content_payload
    request = v1._request(
        "direct-sql-" + value.artifact_id,
        ctx.repo.operator_id,
        intent=content.intent,
        reviewer_evidence=content.reviewer_evidence,
        reviewer_authority=content.reviewer_authority,
    )
    with ctx.sessions.begin() as session:
        v1._lock(session)
        ctx.corrections._ensure_stream(session, previous)
        for row in ctx.corrections._component_rows(value):
            session.execute(COMPONENTS.insert().values(**row))
        if content.normalized_result is not None:
            row = ctx.corrections._result_row(session, value)
            session.execute(RESULTS.insert().values(**row))
            session.execute(
                MatchResultRecord.__table__.insert().values(
                    **{
                        c.name: row[
                            "previous_match_result_id"
                            if c.name == "supersedes_match_result_id"
                            else c.name
                        ]
                        for c in MatchResultRecord.__table__.columns
                    }
                )
            )
        session.execute(
            ADMISSIONS.insert().values(**ctx.corrections._admission_row(value, request))
        )


@pytest.mark.parametrize("trainable", [False, True])
def test_sql_ownership_guards_include_nontrainable_keys_and_all_claimants(
    two_streams, trainable
):
    ctx = two_streams
    first = withdrawal(ctx)
    second = (
        cases.reviewed_intent(ctx, previous=ctx.other, raw={"result_key": "shared-key"})
        if trainable
        else withdrawal(ctx, previous=ctx.other)
    )
    value = sealed_sql_candidate(ctx, ctx.other, *second)
    accepted = cases.record(ctx, first)
    before = cases.counts(ctx)
    with pytest.raises(IntegrityError, match="logical result key.*owned"):
        insert_sql_candidate(ctx, ctx.other, value)
    assert cases.counts(ctx) == before
    assert ctx.corrections.load_admission(accepted.artifact_id) == accepted


def test_sql_rejects_loss_of_established_revision_order_even_with_later_publication(
    corrected_lane,
):
    ctx = corrected_lane
    first = cases.record(ctx, cases.reviewed_intent(ctx, raw={"revision_order": 10}))
    previous = ctx.corrections.load_version(first.artifact_id)
    intent, _ = cases.reviewed_intent(
        ctx,
        previous=previous,
        raw={"revision_order": 11, "status": "CANCELLED"},
        trainable=False,
    )
    intent = intent.model_copy(
        update={
            "candidate": intent.candidate.model_copy(
                update={"provider_revision_id": None, "provider_revision_order": None}
            )
        }
    )
    review = cases.write_evidence(
        ctx.root,
        "sql-loss-review.json",
        cases.review_document(
            schema=cases.CORRECTION_INTENT_V2, digest=intent.intent_hash, at=ctx.clock()
        ),
    )
    value = sealed_sql_candidate(ctx, previous, intent, review)
    before = cases.counts(ctx)
    with pytest.raises(IntegrityError, match="revision order"):
        insert_sql_candidate(ctx, previous, value)
    assert cases.counts(ctx) == before


def test_sql_new_v1_parent_cannot_hide_a_withdrawal(corrected_lane):
    ctx = corrected_lane
    cases.record(ctx, withdrawal(ctx))
    value = TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=ctx.base_admission.source_rights_admission,
        facts=ctx.base_admission.facts,
        actual_started_at_utc=ctx.clock(),
        actual_completed_at_utc=ctx.clock(),
        persisted_at_utc=ctx.clock(),
    )
    request = v1._request(
        "direct-stale-v1",
        ctx.repo.operator_id,
        source_rights_admission_id=ctx.recorded.source_rights_admission_id,
        submissions=ctx.submissions,
    )
    before = cases.counts(ctx)
    with pytest.raises(IntegrityError, match="new V1 admission.*corrected stream"):
        with ctx.sessions.begin() as session:
            for child in v1._children(
                value.training_fact_admission_id, value.facts[0], ctx.submissions[0]
            ):
                session.execute(
                    child.__table__.insert().values(
                        **{
                            column.name: getattr(child, column.name)
                            for column in child.__table__.columns
                        }
                    )
                )
            parent = v1._admission_row(value, request)
            session.execute(
                parent.__table__.insert().values(
                    **{
                        column.name: getattr(parent, column.name)
                        for column in parent.__table__.columns
                    }
                )
            )
    assert cases.counts(ctx) == before
    assert ctx.repo.load(ctx.base_pin.artifact_id) == ctx.base_admission


def test_core_selector_revalidates_the_reported_copied_version_repro(corrected_lane):
    ctx = corrected_lane
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,), correction_ids=(), actual_at=ctx.clock()
    )
    forged = context.versions[0].model_copy(
        update={
            "revision_sequence": 999,
            "predecessor": None,
            "components": (),
            "normalized_result": ctx.base.normalized_result.model_copy(
                update={"home_goals": 99}
            ),
            "snapshot": ctx.base.snapshot.model_copy(update={"home_goals": 5}),
        }
    )
    with pytest.raises(ValueError):
        context.model_copy(update={"versions": (forged,)}).select(
            source_cutoffs={"source": ctx.clock()}
        )


@pytest.mark.parametrize(
    "mutation", ["future", "backward", "missing", "component", "score", "reference"]
)
def test_core_context_checks_whole_structure_before_selection(corrected_lane, mutation):
    ctx = corrected_lane
    value = cases.record(ctx, cases.reviewed_intent(ctx))
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(value.artifact_id,),
        actual_at=ctx.clock(),
    )
    root, revision = context.versions
    if mutation == "future":
        changed = context.model_copy(update={"actual_at_utc": root.registered_at_utc})
    elif mutation == "backward":
        changed = context.model_copy(update={"versions": (revision, root)})
    elif mutation == "missing":
        changed = context.model_copy(update={"corrections": ()})
    elif mutation == "component":
        changed = context.model_copy(
            update={
                "versions": (
                    root,
                    revision.model_copy(update={"components": root.components}),
                )
            }
        )
    elif mutation == "reference":
        changed = context.model_copy(
            update={
                "versions": (
                    root.model_copy(update={"base_binding": root.base_admission}),
                    revision,
                )
            }
        )
    else:
        changed = context.model_copy(
            update={
                "versions": (
                    root,
                    revision.model_copy(
                        update={
                            "normalized_result": revision.normalized_result.model_copy(
                                update={"home_goals": 99}
                            )
                        }
                    ),
                )
            }
        )
    with pytest.raises(ValueError):
        changed.select(source_cutoffs={"source": context.actual_at_utc})


@pytest.mark.parametrize("corrected", [False, True])
def test_coherent_copied_scores_cannot_reuse_an_opaque_authoritative_reference(
    corrected_lane, corrected
):
    ctx = corrected_lane
    ids = (
        (cases.record(ctx, cases.reviewed_intent(ctx)).artifact_id,)
        if corrected
        else ()
    )
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,), correction_ids=ids, actual_at=ctx.clock()
    )
    original = context.versions[-1]
    forged = original.model_copy(
        update={
            "snapshot": original.snapshot.model_copy(update={"home_goals": 99}),
            "normalized_result": original.normalized_result.model_copy(
                update={
                    "home_goals": 99,
                    "payload_hash": match_result_payload_sha256(
                        99, original.normalized_result.away_goals
                    ),
                }
            ),
        }
    )
    with pytest.raises(ValueError, match="authoritative"):
        context.model_copy(
            update={"versions": (*context.versions[:-1], forged)}
        ).select(source_cutoffs={"source": context.actual_at_utc})
    # A naked partial projection has no means of authenticating its parent hash.
    with pytest.raises(ValueError, match="authoritative snapshot artifact links"):
        context.model_copy(
            update={"base_artifacts": (), "correction_artifacts": ()}
        ).select(source_cutoffs={"source": context.actual_at_utc})
    assert (
        context.select(source_cutoffs={"source": context.actual_at_utc})[-1] == original
    )


def test_core_context_and_integration_helper_share_revision_continuity_validation(
    corrected_lane,
):
    from football_system.domain.versioned_training_history import (
        validate_versioned_context,
    )

    ctx = corrected_lane
    first = cases.record(ctx, cases.reviewed_intent(ctx, raw={"revision_order": 10}))
    previous = ctx.corrections.load_version(first.artifact_id)
    second = cases.record(
        ctx, cases.reviewed_intent(ctx, previous=previous, raw={"revision_order": 11})
    )
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(first.artifact_id, second.artifact_id),
        actual_at=ctx.clock(),
    )
    lost = context.versions[-1].model_copy(
        update={
            "snapshot": context.versions[-1].snapshot.model_copy(
                update={"provider_revision_id": None, "provider_revision_order": None}
            )
        }
    )
    copied = context.model_copy(update={"versions": (*context.versions[:-1], lost)})
    with pytest.raises(ValueError, match="cannot disappear"):
        copied.select(source_cutoffs={"source": context.actual_at_utc})
    with pytest.raises(ValueError, match="cannot disappear"):
        validate_versioned_context(copied)
    assert TrainingCorrectionContextV2.model_validate(context) == context


def test_serialized_context_is_partial_and_needs_authoritative_reload_for_core_selection(
    corrected_lane,
):
    ctx = corrected_lane
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,), correction_ids=(), actual_at=ctx.clock()
    )
    wire = context.model_dump_json()
    assert "base_artifacts" not in wire and "correction_artifacts" not in wire
    partial = TrainingCorrectionContextV2.model_validate_json(wire)
    with pytest.raises(ValueError, match="authoritative snapshot artifact links"):
        partial.select(source_cutoffs={"source": ctx.clock()})
    reloaded = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(),
        actual_at=context.actual_at_utc,
    )
    assert reloaded.model_dump_json() == wire
    assert (
        reloaded.select(source_cutoffs={"source": context.actual_at_utc})
        == context.versions
    )
