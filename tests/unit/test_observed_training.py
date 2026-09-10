"""Pure structural contracts; invented seals are NOT repository admissions."""

from datetime import datetime, timedelta, timezone

import pytest

from football_system.domain.archive import HistoricalDataMode, canonical_json
from football_system.domain.observed_training import (
    CurrentSnapshotCollectionScopeV1,
    EvidenceBasis,
    ObservedCaptureEvidenceV1,
    ObservedCapturePointerV1,
    ObservedCollectionScopeAdmissionV1,
    ObservedFixtureV1,
    ObservedIdentityEvidenceV1,
    ObservedIdentitySourceRefV1,
    ObservedScopeSeasonV1,
    ObservedSnapshotAdmissionV1,
    ObservedSnapshotContextV1,
    ObservedSnapshotInputV1,
    ObservedSnapshotRecordV1,
    ObservedSnapshotSubjectV1,
    ObservedStreamV1,
    observed_snapshot_root,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    LocalReviewerAttestationContentV1,
    LocalReviewerAttestationV1,
    TrainingCanonicalMatchIdentityV1,
    TrainingProviderMatchMappingV1,
    tagged_canonical_sha256,
)

AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def attest(subject, at):
    return LocalReviewerAttestationV1.freeze(
        content_payload=LocalReviewerAttestationContentV1(
            attested_schema_version=subject.schema_version,
            attested_payload_hash=subject.subject_hash,
            authorized_reviewer="synthetic-reviewer",
            reviewer_authority_reference="synthetic-authority.json",
            authority_sha256="a" * 64,
            reviewed_at_utc=at,
            evidence=LocalReviewEvidenceV1(
                evidence_reference="synthetic-review.json", evidence_sha256="b" * 64
            ),
        )
    )


@pytest.fixture
def graph():
    scope_subject = CurrentSnapshotCollectionScopeV1(
        source_data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        retrospective=True,
        source_rights_admission_id="rights",
        source_rights_admission_hash="a" * 64,
        source_id="source",
        provider_competition_id="222",
        canonical_competition_id="league",
        seasons=(
            ObservedScopeSeasonV1(
                provider_season_id="333",
                canonical_season_id="2024/25",
                expected_fixture_ids=("100",),
                expected_fixture_count=1,
                exceptions=(),
            ),
        ),
        max_capture_receipts=10,
        max_snapshot_records=10,
        permitted_uses=("TRAINING", "VALIDATION"),
        retention_deadline_utc=AT + timedelta(days=300),
        user_terms_resolution=LocalReviewEvidenceV1(
            evidence_reference="synthetic-resolved.json", evidence_sha256="c" * 64
        ),
    )
    scope = ObservedCollectionScopeAdmissionV1.freeze(
        subject=scope_subject,
        reviewer_attestation=attest(scope_subject, AT),
        recorded_at_utc=AT,
        capture_receipt_high_watermark=0,
    )
    pointer = ObservedCapturePointerV1(
        capture_receipt_id="capture", record_pointer="/data"
    )
    capture = ObservedCaptureEvidenceV1(
        **pointer.model_dump(),
        receipt_hash="a" * 64,
        payload_sha256="b" * 64,
        record_sha256="c" * 64,
        outcome_sha256=tagged_canonical_sha256(
            "OBSERVED_NATIVE_OUTCOME_V1",
            {
                "provider_raw_status": "FT",
                "trainable": True,
                "regular_time_home_goals": 2,
                "regular_time_away_goals": 0,
            },
        ),
        capture_ordinal=1,
        capture_observed_at_utc=AT + timedelta(seconds=1),
        capture_registered_at_utc=AT + timedelta(seconds=2),
        capture_record_count=1,
    )
    subject = ObservedSnapshotSubjectV1(
        source_data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        retrospective=True,
        scope_id=scope.scope_id,
        scope_hash=scope.content_hash,
        source_rights_admission_id="rights",
        source_rights_admission_hash="a" * 64,
        stream=ObservedStreamV1(
            source_id="source",
            provider_code="SPORTMONKS",
            provider_fixture_namespace="fixture",
            provider_fixture_key="100",
            internal_match_id="match",
        ),
        identity=TrainingCanonicalMatchIdentityV1(
            internal_match_id="match",
            internal_competition_id="league",
            internal_home_team_id="home",
            internal_away_team_id="away",
            season="2024/25",
            competition_type="LEAGUE",
            kickoff_at_utc=AT - timedelta(days=1),
        ),
        provider_mapping=TrainingProviderMatchMappingV1(
            mapping_id="mapping",
            provider_code="SPORTMONKS",
            external_namespace="fixture",
            external_match_id="100",
            internal_match_id="match",
            resolution_method="EXPLICIT",
            confidence=1,
            available_at_utc=AT,
        ),
        input=ObservedSnapshotInputV1(
            fixture=pointer,
            season=pointer,
            result=pointer,
            provider_mapping_id="mapping",
            home_team_alias_id="home-alias",
            away_team_alias_id="away-alias",
            competition_mapping_id="competition",
        ),
        identity_evidence=ObservedIdentityEvidenceV1(
            match_mappings=(
                ObservedIdentitySourceRefV1(
                    record_id="mapping", record_sha256="a" * 64
                ),
            ),
            team_aliases=tuple(
                ObservedIdentitySourceRefV1(record_id=key, record_sha256="b" * 64)
                for key in ("away-alias", "home-alias")
            ),
            competition_mappings=(
                ObservedIdentitySourceRefV1(
                    record_id="competition", record_sha256="c" * 64
                ),
            ),
        ),
        fixture_capture=capture,
        season_capture=capture,
        result_capture=capture,
        inspection=ObservedFixtureV1(
            provider_fixture_key="100",
            provider_competition_id="222",
            provider_season_id="333",
            provider_home_team_id="444",
            provider_away_team_id="555",
            kickoff_at_utc=AT - timedelta(days=1),
            provider_raw_status="FT",
            trainable=True,
            regular_time_home_goals=2,
            regular_time_away_goals=0,
            sporting_period_end_at_utc=None,
            provider_publication_at_utc=None,
            provider_finalized_at_utc=None,
            provider_version_id=None,
            field_evidence={"synthetic": "structural test only"},
            diagnostics=(),
        ),
        resolved_home_team_id="home",
        resolved_away_team_id="away",
        upstream_publication_at_utc=None,
        provider_finalized_at_utc=None,
        revision_sequence=0,
        predecessor_id=None,
        predecessor_hash=None,
        previous_match_result_id=None,
    )
    record = ObservedSnapshotRecordV1.freeze(
        subject=subject,
        reviewer_attestation=attest(subject, AT + timedelta(seconds=3)),
        verified_at_utc=AT + timedelta(seconds=4),
        registered_at_utc=AT + timedelta(seconds=5),
    )
    return scope, record


def test_new_evidence_axis_does_not_extend_historical_modes():
    assert {x.value for x in HistoricalDataMode} == {
        "LIVE_STRICT",
        "SOURCE_TIME_RESEARCH",
    }
    assert {x.value for x in EvidenceBasis} == {
        "CURRENT_SNAPSHOT_OBSERVED",
        "VERIFIED_HISTORICAL_SOURCE_TIME",
    }
    with pytest.raises(ValueError):
        HistoricalDataMode("CURRENT_SNAPSHOT_OBSERVED")


@pytest.mark.parametrize(
    "field", ["upstream_publication_at_utc", "provider_finalized_at_utc"]
)
def test_unknown_times_are_required_null_not_omitted_or_backfilled(graph, field):
    _, record = graph
    document = record.subject.model_dump(mode="python")
    del document[field]
    with pytest.raises(ValueError):
        ObservedSnapshotSubjectV1.model_validate(document)
    document[field] = AT
    with pytest.raises(ValueError):
        ObservedSnapshotSubjectV1.model_validate(document)


def test_record_projection_clocks_and_local_ids_are_sealed(graph):
    _, record = graph
    assert (
        ObservedSnapshotRecordV1.model_validate_json(record.model_dump_json()) == record
    )
    result = record.normalized_result
    assert result.observed_at_utc == record.capture_observed_at_utc
    assert result.available_at_utc == result.ingested_at_utc == record.registered_at_utc
    assert (
        record.version_id
        != result.match_result_id
        != record.stream.provider_fixture_key
    )
    assert record.to_elo_result().available_at_utc == record.registered_at_utc
    for field in ("verified_at_utc", "registered_at_utc"):
        bad = record.model_dump(mode="python")
        bad[field] += timedelta(microseconds=1)
        with pytest.raises(ValueError):
            ObservedSnapshotRecordV1.model_validate(bad)
    bad = record.model_dump(mode="python")
    bad["normalized_result"]["observed_at_utc"] = AT
    with pytest.raises(ValueError, match="projection"):
        ObservedSnapshotRecordV1.model_validate(bad)


def test_cutoff_is_strict_and_selection_does_not_replace_base_context(graph):
    scope, record = graph
    context = ObservedSnapshotContextV1(
        scope=scope, records=(record,), actual_at_utc=AT + timedelta(seconds=10)
    )
    assert context.select_heads(record.capture_observed_at_utc) == ()
    assert context.select_heads(record.registered_at_utc) == ()
    root = context.base_root
    assert context.select_heads(context.actual_at_utc) == (record,)
    assert context.select_heads(context.actual_at_utc, ("match",)) == ()
    assert context.base_root == root
    assert observed_snapshot_root(()) != observed_snapshot_root(context.records)
    with pytest.raises(ValueError, match="follows"):
        context.select_heads(context.actual_at_utc + timedelta(seconds=1))


def test_scope_requires_new_review_and_consistent_cohort(graph):
    scope, _ = graph
    bad = scope.model_dump(mode="python")
    bad["reviewer_attestation"]["content_payload"]["attested_schema_version"] = (
        "SOURCE_RIGHTS_PAYLOAD_V1"
    )
    with pytest.raises(ValueError):
        ObservedCollectionScopeAdmissionV1.model_validate(bad)
    season = scope.subject.seasons[0].model_dump(mode="python")
    season["expected_fixture_count"] = 3
    with pytest.raises(ValueError, match="IDs/count"):
        ObservedScopeSeasonV1.model_validate(season)


def test_incomplete_or_duplicate_context_fails_closed(graph):
    scope, record = graph
    with pytest.raises(ValueError, match="duplicate|fork"):
        ObservedSnapshotContextV1(
            scope=scope,
            records=(record, record),
            actual_at_utc=AT + timedelta(seconds=10),
        )
    subject = record.subject.model_copy(
        update={
            "predecessor_id": record.version_id,
            "predecessor_hash": record.content_hash,
            "revision_sequence": 1,
            "previous_match_result_id": record.normalized_result.match_result_id,
        }
    )
    successor = ObservedSnapshotRecordV1.freeze(
        subject=subject,
        reviewer_attestation=attest(subject, AT + timedelta(seconds=3)),
        verified_at_utc=record.verified_at_utc,
        registered_at_utc=record.registered_at_utc,
    )
    with pytest.raises(ValueError, match="complete predecessor"):
        ObservedSnapshotContextV1(
            scope=scope, records=(successor,), actual_at_utc=AT + timedelta(seconds=10)
        )


@pytest.mark.parametrize(
    "field",
    ["provider_publication_at_utc", "provider_finalized_at_utc", "provider_version_id"],
)
@pytest.mark.parametrize("invalid", ["missing", "value"])
def test_factory_rejects_nested_constructed_missing_or_filled_upstream_unknowns(
    graph, field, invalid
):
    _, record = graph
    fields = record.subject.inspection.model_dump(mode="python")
    if invalid == "missing":
        del fields[field]
    else:
        fields[field] = "invented"
    bad = record.subject.model_copy(
        update={"inspection": ObservedFixtureV1.model_construct(**fields)}
    )
    with pytest.raises(ValueError):
        ObservedSnapshotRecordV1.freeze(
            subject=bad,
            reviewer_attestation=record.reviewer_attestation,
            verified_at_utc=record.verified_at_utc,
            registered_at_utc=record.registered_at_utc,
        )
    with pytest.raises(ValueError):
        ObservedSnapshotSubjectV1.model_validate(bad)


@pytest.mark.parametrize(
    "part",
    [
        "season",
        "authority",
        "capture",
        "foreign_identity",
        "foreign_result",
        "extra_proof",
    ],
)
def test_deep_model_copies_cannot_be_resealed_or_selected(graph, part):
    scope, record = graph
    if part == "season":
        subject = scope.subject.model_copy(
            update={
                "seasons": (
                    scope.subject.seasons[0].model_copy(
                        update={"expected_fixture_count": 999}
                    ),
                )
            }
        )
        with pytest.raises(ValueError):
            ObservedCollectionScopeAdmissionV1.freeze(
                subject=subject,
                reviewer_attestation=scope.reviewer_attestation,
                recorded_at_utc=scope.recorded_at_utc,
                capture_receipt_high_watermark=0,
            )
        with pytest.raises(ValueError):
            _ = subject.subject_hash
        return
    if part == "authority":
        attestation = record.reviewer_attestation.model_copy(
            update={"attestation_hash": "0" * 64}
        )
        bad = record.model_copy(update={"reviewer_attestation": attestation})
    elif part == "capture":
        capture = record.subject.result_capture.model_copy(
            update={"capture_record_count": True}
        )
        bad = record.model_copy(
            update={
                "subject": record.subject.model_copy(update={"result_capture": capture})
            }
        )
    elif part == "foreign_identity":
        identity = record.identity.model_copy(
            update={"internal_home_team_id": record.identity.internal_away_team_id}
        )
        bad = record.model_copy(
            update={"subject": record.subject.model_copy(update={"identity": identity})}
        )
    elif part == "foreign_result":
        bad = record.model_copy(
            update={
                "normalized_result": record.normalized_result.model_copy(
                    update={"home_goals": True}
                )
            }
        )
    else:
        bad = record.model_copy(update={"caller_verified": True})
    with pytest.raises(ValueError):
        ObservedSnapshotRecordV1.model_validate(bad)
    with pytest.raises(ValueError):
        bad.to_elo_result()
    with pytest.raises(ValueError):
        observed_snapshot_root((bad,))
    with pytest.raises(ValueError):
        ObservedSnapshotAdmissionV1.freeze(
            scope_id=scope.scope_id,
            scope_hash=scope.content_hash,
            admission_sequence=0,
            actual_started_at_utc=record.reviewer_attestation.content_payload.reviewed_at_utc,
            verified_at_utc=record.verified_at_utc,
            registered_at_utc=record.registered_at_utc,
            records=(bad,),
        )
    context = ObservedSnapshotContextV1(
        scope=scope, records=(record,), actual_at_utc=AT + timedelta(seconds=10)
    ).model_copy(update={"records": (bad,)})
    with pytest.raises(ValueError):
        context.select_heads(context.actual_at_utc)
    with pytest.raises(ValueError):
        _ = context.base_root


def test_root_counts_versions_and_rejects_duplicate_source_observations(graph):
    scope, record = graph
    assert observed_snapshot_root((record,)) == tagged_canonical_sha256(
        "OBSERVED_SNAPSHOT_ROOT_V1",
        {
            "record_count": 1,
            "records": (
                {"version_id": record.version_id, "content_hash": record.content_hash},
            ),
        },
    )
    with pytest.raises(ValueError, match="duplicate"):
        observed_snapshot_root((record, record))
    with pytest.raises(ValueError):
        ObservedSnapshotContextV1(
            scope=scope, records=(), actual_at_utc=AT + timedelta(seconds=10)
        )
    subject = record.subject.model_copy(
        update={
            "revision_sequence": 1,
            "predecessor_id": record.version_id,
            "predecessor_hash": record.content_hash,
            "previous_match_result_id": record.normalized_result.match_result_id,
        }
    )
    repeat = ObservedSnapshotRecordV1.freeze(
        subject=subject,
        reviewer_attestation=attest(subject, AT + timedelta(seconds=3)),
        verified_at_utc=record.verified_at_utc,
        registered_at_utc=record.registered_at_utc,
    )
    with pytest.raises(ValueError, match="source observation"):
        ObservedSnapshotContextV1(
            scope=scope,
            records=(record, repeat),
            actual_at_utc=AT + timedelta(seconds=10),
        )


def test_same_receipt_cannot_claim_independent_role_hashes_or_times(graph):
    _, record = graph
    for field, value in (
        ("payload_sha256", "d" * 64),
        ("record_sha256", "d" * 64),
        ("capture_record_count", 2),
        ("capture_registered_at_utc", AT + timedelta(seconds=3)),
    ):
        bad = record.subject.model_copy(
            update={
                "season_capture": record.subject.season_capture.model_copy(
                    update={field: value}
                )
            }
        )
        with pytest.raises(ValueError):
            ObservedSnapshotSubjectV1.model_validate(bad)


def test_receipt_unit_rename_has_no_legacy_alias_and_capture_count_is_bounded(graph):
    scope, record = graph
    old = scope.subject.model_dump(mode="python")
    old["max_capture_requests"] = old.pop("max_capture_receipts")
    with pytest.raises(ValueError):
        CurrentSnapshotCollectionScopeV1.model_validate(old)
    for invalid in (0, True, 1.0, -1):
        with pytest.raises(ValueError):
            CurrentSnapshotCollectionScopeV1.model_validate(
                scope.subject.model_copy(update={"max_capture_receipts": invalid})
            )
    fields = record.subject.result_capture.model_dump(mode="python")
    assert (
        ObservedCaptureEvidenceV1(
            **{**fields, "capture_record_count": 50, "record_pointer": "/data/49"}
        ).capture_record_count
        == 50
    )
    for count, pointer in (
        (51, "/data/0"),
        (0, "/data/0"),
        (2, "/data/2"),
        (2, "/data"),
    ):
        with pytest.raises(ValueError):
            ObservedCaptureEvidenceV1(
                **{**fields, "capture_record_count": count, "record_pointer": pointer}
            )


def test_retrospective_provenance_is_explicit_and_transitively_sealed(graph):
    scope, record = graph
    for subject in (scope.subject, record.subject):
        wire = subject.model_dump(mode="json")
        assert wire["source_data_mode"] == "SOURCE_TIME_RESEARCH"
        assert wire["retrospective"] is True
        assert wire["evidence_basis"] == "CURRENT_SNAPSHOT_OBSERVED"
        assert "source_classification" not in wire
        assert type(subject).model_fields["source_data_mode"].is_required()
        assert type(subject).model_fields["retrospective"].is_required()
        assert type(subject).model_validate_json(subject.model_dump_json()) == subject
    assert "source_data_mode" not in record.model_dump(exclude={"subject"})
    assert (
        ObservedSnapshotRecordV1.model_validate_json(record.model_dump_json()) == record
    )
    altered = record.model_dump(
        mode="python",
        exclude={"schema_version", "version_id", "content_hash", "normalized_result"},
    )
    altered["subject"]["source_data_mode"] = "LIVE_STRICT"
    assert (
        tagged_canonical_sha256(record.schema_version, altered) != record.content_hash
    )


@pytest.mark.parametrize("kind", ["scope", "subject"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_data_mode", "LIVE_STRICT"),
        ("source_data_mode", "CURRENT_SNAPSHOT_OBSERVED"),
        ("source_data_mode", None),
        ("source_data_mode", "MISSING"),
        ("retrospective", False),
        ("retrospective", 1),
        ("retrospective", "true"),
        ("retrospective", "MISSING"),
    ],
)
def test_provenance_cannot_be_omitted_relabelled_or_laundered_by_copy(
    graph, kind, field, value
):
    scope, record = graph
    original = scope.subject if kind == "scope" else record.subject
    data = original.model_dump(mode="python")
    if value == "MISSING":
        del data[field]
        copied = type(original).model_construct(**data)
    else:
        data[field] = value
        copied = original.model_copy(update={field: value})
    with pytest.raises(ValueError):
        type(original).model_validate_json(canonical_json(data))
    with pytest.raises(ValueError):
        type(original).model_validate(copied)
    with pytest.raises(ValueError):
        if kind == "scope":
            ObservedCollectionScopeAdmissionV1.freeze(
                subject=copied,
                reviewer_attestation=scope.reviewer_attestation,
                recorded_at_utc=scope.recorded_at_utc,
                capture_receipt_high_watermark=scope.capture_receipt_high_watermark,
            )
        else:
            ObservedSnapshotRecordV1.freeze(
                subject=copied,
                reviewer_attestation=record.reviewer_attestation,
                verified_at_utc=record.verified_at_utc,
                registered_at_utc=record.registered_at_utc,
            )


def test_mixed_mode_context_copy_is_not_selectable_or_hashable(graph):
    scope, record = graph
    live = record.model_copy(
        update={
            "subject": record.subject.model_copy(
                update={"source_data_mode": HistoricalDataMode.LIVE_STRICT}
            )
        }
    )
    context = ObservedSnapshotContextV1(
        scope=scope, records=(record,), actual_at_utc=AT + timedelta(seconds=10)
    )
    mixed = context.model_copy(update={"records": (record, live)})
    with pytest.raises(ValueError):
        mixed.select_heads(mixed.actual_at_utc)
    with pytest.raises(ValueError):
        _ = mixed.base_root
    with pytest.raises(ValueError):
        observed_snapshot_root(mixed.records)


@pytest.mark.parametrize(
    "mutation", ["missing", "empty", "duplicate", "unordered", "hash", "reference"]
)
def test_identity_evidence_is_required_and_deeply_sealed(graph, mutation):
    _, record = graph
    document = record.subject.model_dump(mode="python")
    proof = document["identity_evidence"]
    if mutation == "missing":
        del document["identity_evidence"]
    elif mutation == "empty":
        proof["team_aliases"] = ()
    elif mutation == "duplicate":
        proof["team_aliases"] += proof["team_aliases"][:1]
    elif mutation == "unordered":
        proof["team_aliases"] = tuple(reversed(proof["team_aliases"]))
    elif mutation == "hash":
        proof["team_aliases"][0]["record_sha256"] = "not-a-sha256"
    else:
        proof["team_aliases"] = proof["team_aliases"][:1]
    with pytest.raises(ValueError):
        ObservedSnapshotSubjectV1.model_validate(document)
    copied = (
        ObservedSnapshotSubjectV1.model_construct(**document)
        if mutation == "missing"
        else record.subject.model_copy(update=document)
    )
    with pytest.raises(ValueError):
        _ = copied.subject_hash


def test_valid_identity_proof_hash_changes_invalidate_record_and_review(graph):
    _, record = graph
    document = record.model_dump(mode="python")
    document["subject"]["identity_evidence"]["team_aliases"][0]["record_sha256"] = (
        "d" * 64
    )
    with pytest.raises(ValueError):
        ObservedSnapshotRecordV1.model_validate(document)
