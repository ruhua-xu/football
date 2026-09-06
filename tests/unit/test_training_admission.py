import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from football_system.application.training_admission import (
    PrepareTrainingFactBindingsService,
    SealSourceRightsReviewService,
    TrainingFactAdmissionCandidateV1,
)
from football_system.domain.archive import HistoricalDataMode, canonical_json
from football_system.domain.common import stable_id
from football_system.domain.identity import CanonicalMatchIdentity
from football_system.domain.match import ProviderMatchMapping
from football_system.domain.settlement import MatchResult
from football_system.domain.training_admission import (
    LOCAL_REVIEWER_ATTESTATION_V1,
    SOURCE_RIGHTS_ADMISSION_V1,
    SOURCE_RIGHTS_PAYLOAD_V1,
    TRAINING_FACT_ADMISSION_V1,
    LocalReviewEvidenceV1,
    MatchResultAdmissionContentV1,
    MatchResultAdmissionV1,
    MatchSeasonMembershipContentV1,
    MatchSeasonMembershipV1,
    ProviderResultStatusCategory,
    ResultScoreSemantics,
    SeasonAssignmentMethod,
    SourceRightsAdmissionV1,
    SourceRightsPermittedUse,
    SourceRightsPayloadV1,
    TrainingFactAdmissionV1,
    TrainingFactAdmissionContentV1,
    TrainingFactBindingV1,
    TrainingUseClass,
    TrainingFixtureSourceV1,
    normalized_match_result_record_sha256,
    tagged_canonical_sha256,
)

UTC = timezone.utc
RIGHTS_EFFECTIVE = datetime(2026, 1, 1, tzinfo=UTC)
RIGHTS_REVIEWED = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
RIGHTS_RECORDED = RIGHTS_REVIEWED + timedelta(minutes=1)
RIGHTS_EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)
SOURCE_AVAILABLE = datetime(2025, 5, 18, 17, 30, tzinfo=UTC)
LOCAL_IMPORTED = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
ARCHIVE_CREATED = LOCAL_IMPORTED + timedelta(seconds=30)
REGISTERED = LOCAL_IMPORTED + timedelta(minutes=1)
SOURCE_REVIEWED = REGISTERED + timedelta(minutes=1)
ADMISSION_STARTED = SOURCE_REVIEWED + timedelta(minutes=1)
ADMISSION_COMPLETED = ADMISSION_STARTED + timedelta(minutes=1)
ADMISSION_PERSISTED = ADMISSION_COMPLETED + timedelta(minutes=1)
ALL_USES = tuple(SourceRightsPermittedUse)


def test_training_use_class_does_not_change_historical_data_modes() -> None:
    assert TrainingUseClass.APPROVED_TRAINING_HISTORY.value == (
        "APPROVED_TRAINING_HISTORY"
    )
    assert tuple(HistoricalDataMode) == (
        HistoricalDataMode.LIVE_STRICT,
        HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )
    assert "APPROVED_TRAINING_HISTORY" not in HistoricalDataMode.__members__


def test_tagged_hash_uses_schema_nul_and_canonical_payload() -> None:
    payload = {"z": 1, "a": "value"}

    expected = hashlib.sha256(
        b"EXAMPLE_SCHEMA_V1\0" + canonical_json(payload).encode("utf-8")
    ).hexdigest()

    assert tagged_canonical_sha256("EXAMPLE_SCHEMA_V1", payload) == expected

    rights_payload = _rights().content_payload.rights_payload
    assert tagged_canonical_sha256(
        SOURCE_RIGHTS_PAYLOAD_V1,
        rights_payload,
    ) == tagged_canonical_sha256(
        SOURCE_RIGHTS_PAYLOAD_V1,
        rights_payload.model_dump(mode="python"),
    )


def test_source_rights_exact_retry_is_deterministic_and_local_only() -> None:
    first = _rights(
        source_ids=("source-secondary", "source-main"),
        uses=tuple(reversed(ALL_USES)),
    )
    repeated = _rights(
        source_ids=("source-main", "source-secondary"),
        uses=ALL_USES,
    )

    assert first == repeated
    assert first.source_rights_admission_id == stable_id(
        SOURCE_RIGHTS_ADMISSION_V1,
        first.admission_hash,
    )
    assert first.content_payload.rights_payload.source_ids == (
        "source-main",
        "source-secondary",
    )
    assert first.content_payload.rights_payload.permitted_uses == tuple(
        sorted(ALL_USES, key=lambda item: item.value)
    )
    serialized_content = canonical_json(first.content_payload)
    assert first.source_rights_admission_id not in serialized_content
    assert first.admission_hash not in serialized_content
    assert "evidence_bytes" not in serialized_content
    assert "signature" not in serialized_content
    attestation = first.content_payload.reviewer_attestation
    assert attestation.reviewer_attestation_id == stable_id(
        LOCAL_REVIEWER_ATTESTATION_V1,
        attestation.attestation_hash,
    )
    assert first.content_payload.rights_payload_hash == tagged_canonical_sha256(
        SOURCE_RIGHTS_PAYLOAD_V1,
        first.content_payload.rights_payload,
    )

    evidence_payload = LocalReviewEvidenceV1(
        evidence_reference="local://rights/review-1",
        evidence_sha256="b" * 64,
    ).model_dump(mode="python")
    evidence_payload["evidence_bytes"] = b"not allowed"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LocalReviewEvidenceV1.model_validate(evidence_payload)


def test_source_rights_recording_is_separate_and_fail_closed() -> None:
    admission = _rights()
    content = admission.content_payload
    payload = content.rights_payload
    later = SourceRightsAdmissionV1.from_recorded(
        rights_payload=payload,
        reviewer_attestation=content.reviewer_attestation,
        recorded_at_utc=RIGHTS_RECORDED + timedelta(seconds=1),
    )

    assert "recorded_at_utc" not in payload.model_dump(mode="json")
    assert content.rights_payload_hash == later.content_payload.rights_payload_hash
    assert content.reviewer_attestation == later.content_payload.reviewer_attestation
    assert admission.admission_hash != later.admission_hash

    changed_payload = payload.model_copy(update={"terms_version": "terms-v2"})
    with pytest.raises(ValidationError, match="does not bind the rights payload"):
        SourceRightsAdmissionV1.from_recorded(
            rights_payload=changed_payload,
            reviewer_attestation=content.reviewer_attestation,
            recorded_at_utc=RIGHTS_RECORDED,
        )

    late_attestation = SealSourceRightsReviewService().seal(
        rights_payload=payload,
        authorized_reviewer="reviewer-1",
        reviewer_authority_reference="local://authority/reviewer-1",
        authority_sha256="c" * 64,
        reviewed_at_utc=RIGHTS_RECORDED + timedelta(seconds=1),
        evidence=LocalReviewEvidenceV1(
            evidence_reference="local://rights/review-1",
            evidence_sha256="b" * 64,
        ),
    )
    with pytest.raises(ValidationError, match="review cannot follow"):
        SourceRightsAdmissionV1.from_recorded(
            rights_payload=payload,
            reviewer_attestation=late_attestation,
            recorded_at_utc=RIGHTS_RECORDED,
        )

    with pytest.raises(ValidationError, match="recorded before expiry"):
        SourceRightsAdmissionV1.from_recorded(
            rights_payload=payload,
            reviewer_attestation=content.reviewer_attestation,
            recorded_at_utc=RIGHTS_EXPIRES,
        )


def test_sealing_boundaries_revalidate_copied_model_instances() -> None:
    admission = _rights()
    content = admission.content_payload
    invalid_rights = content.rights_payload.model_copy(
        update={"terms_sha256": "not-a-digest"}
    )
    with pytest.raises(ValidationError, match="terms_sha256"):
        SourceRightsAdmissionV1.from_recorded(
            rights_payload=invalid_rights,
            reviewer_attestation=content.reviewer_attestation,
            recorded_at_utc=RIGHTS_RECORDED,
        )

    invalid_attestation_content = (
        content.reviewer_attestation.content_payload.model_copy(
            update={"authority_sha256": "not-a-digest"}
        )
    )
    invalid_attestation = content.reviewer_attestation.model_copy(
        update={"content_payload": invalid_attestation_content}
    )
    with pytest.raises(ValidationError, match="authority_sha256"):
        SourceRightsAdmissionV1.from_recorded(
            rights_payload=content.rights_payload,
            reviewer_attestation=invalid_attestation,
            recorded_at_utc=RIGHTS_RECORDED,
        )

    invalid_result_content = _result_admission_content().model_copy(
        update={"regular_time_home_goals": -1}
    )
    with pytest.raises(ValidationError, match="regular_time_home_goals"):
        MatchResultAdmissionV1.freeze(content_payload=invalid_result_content)

    candidate = _candidate()
    invalid_mapping = candidate.provider_mapping.model_copy(
        update={"mapping_id": "", "confidence": Decimal("2")}
    )
    with pytest.raises(ValidationError, match="mapping_id|confidence"):
        PrepareTrainingFactBindingsService().prepare(
            candidates=(candidate.model_copy(update={"provider_mapping": invalid_mapping}),)
        )

    invalid_normalized_result = candidate.normalized_result.model_copy(
        update={"home_goals": -1}
    )
    with pytest.raises(ValidationError, match="home_goals"):
        PrepareTrainingFactBindingsService().prepare(
            candidates=(
                candidate.model_copy(
                    update={"normalized_result": invalid_normalized_result}
                ),
            )
        )

    invalid_fixture = candidate.fixture_source.model_copy(
        update={"local_imported_at_utc": datetime(2026, 2, 1, 8, 0)}
    )
    with pytest.raises(ValidationError, match="timezone-aware"):
        PrepareTrainingFactBindingsService().prepare(
            candidates=(candidate.model_copy(update={"fixture_source": invalid_fixture}),)
        )


def test_all_sealed_artifacts_reject_hash_and_id_tampering() -> None:
    admission = _training_admission()
    rights = admission.source_rights_admission
    fact = admission.facts[0]
    artifacts = (
        (
            rights.content_payload.reviewer_attestation,
            "reviewer_attestation_id",
            "attestation_hash",
        ),
        (rights, "source_rights_admission_id", "admission_hash"),
        (
            fact.content_payload.season_membership,
            "season_membership_id",
            "membership_hash",
        ),
        (
            fact.content_payload.match_result_admission,
            "match_result_admission_id",
            "admission_hash",
        ),
        (fact, "training_fact_binding_id", "fact_hash"),
        (admission, "training_fact_admission_id", "admission_hash"),
    )

    for artifact, id_field, hash_field in artifacts:
        payload = artifact.model_dump(mode="python")
        payload[hash_field] = "0" * 64
        with pytest.raises(ValidationError, match="hash is inconsistent"):
            type(artifact).model_validate(payload)

        payload = artifact.model_dump(mode="python")
        payload[id_field] = "tampered-id"
        with pytest.raises(ValidationError, match="ID is inconsistent"):
            type(artifact).model_validate(payload)


def test_training_fact_exact_retry_is_order_independent_and_retrospective() -> None:
    rights = _rights()
    first = _candidate(1)
    second = _candidate(2)

    admission = _admit(rights, (second, first))
    replay = _admit(rights, (first, second))

    assert admission == replay
    assert admission.training_fact_admission_id == stable_id(
        TRAINING_FACT_ADMISSION_V1,
        admission.admission_hash,
    )
    assert tuple(
        item.content_payload.normalized_result.match_id for item in admission.facts
    ) == ("match-1", "match-2")
    assert admission.content_payload.source_data_mode is (
        HistoricalDataMode.SOURCE_TIME_RESEARCH
    )
    assert admission.content_payload.retrospective is True
    assert "training_use_class" not in admission.model_dump(mode="json")

    equivalent_confidence = first.model_copy(
        update={
            "provider_mapping": first.provider_mapping.model_copy(
                update={"confidence": Decimal("1.00")}
            )
        }
    )
    assert _admit(rights, (equivalent_confidence,)).facts[0].fact_hash == (
        _admit(rights, (first,)).facts[0].fact_hash
    )


def test_admission_v1_canonical_hashes_and_ids_remain_frozen() -> None:
    admission = _training_admission()
    rights = admission.source_rights_admission
    attestation = rights.content_payload.reviewer_attestation
    fact = admission.facts[0]
    membership = fact.content_payload.season_membership
    result = fact.content_payload.match_result_admission

    assert rights.content_payload.rights_payload_hash == (
        "0151904652e81917aa07846839e866ea3a3c1b3eaaca6ba75ec411840268c9df"
    )
    assert (attestation.reviewer_attestation_id, attestation.attestation_hash) == (
        "8fa3c044-0b12-5c7a-a457-f741eafc80cb",
        "3d8c699d1451d9945402e612ddaa9b0c972e6b7e87297212accc19de5f0b628e",
    )
    assert (rights.source_rights_admission_id, rights.admission_hash) == (
        "cea20491-48f4-5995-99fd-724241c4fb0d",
        "9fc99f27584cf328990301d3e862d31293c7cc215bd37d6020dba9f60bf0f392",
    )
    assert (result.match_result_admission_id, result.admission_hash) == (
        "9943b40c-606e-55c7-a3f1-416bcfdd9618",
        "d15ca30f000d3a72012edffd1cbae7ffed7d489f1ac040087e180d9394c3f2f8",
    )
    assert result.content_payload.normalized_record_sha256 == (
        "76f7910bbca468180ff85ec95800fccd4e8ee423058ebb6faf9a339f39aa60f5"
    )
    assert (membership.season_membership_id, membership.membership_hash) == (
        "b31b5b80-3262-5324-b394-0dccf7116800",
        "fdf7984a59582774109394a114d9f30012cee88e0802043a31e1c2324ca4ac0b",
    )
    assert (fact.training_fact_binding_id, fact.fact_hash) == (
        "c3bacbb7-157b-50f2-a367-f9701aab3dff",
        "563c1dc6597825f3d8043d930ff9828c5e44580fd45886d598620a784e3ff541",
    )
    assert admission.content_payload.admitted_fact_root == (
        "6402d012b07d4756c711e5d72817641dd4c339fd1ac8d9a672c3f6d03042a570"
    )
    assert (admission.training_fact_admission_id, admission.admission_hash) == (
        "5c53ab80-a3b3-5b11-9b7a-0c56b08e1a9b",
        "7cab97924a101e42f563499ca37ff72686dee4f83998679226d4c660f06932ad",
    )
    content_bytes = canonical_json(
        admission.content_payload.model_dump(mode="python")
    ).encode("utf-8")
    assert hashlib.sha256(content_bytes).hexdigest() == (
        "da520e5d58c9710ffe5faaf964a42bdcf4266b7a8961d28f1af910550a79fa47"
    )


def test_mapping_confidence_hash_is_independent_of_decimal_context() -> None:
    candidate = _candidate()
    confidence = Decimal("0.1234567890123456789012345678900")
    candidate = candidate.model_copy(
        update={
            "provider_mapping": candidate.provider_mapping.model_copy(
                update={"confidence": confidence}
            )
        }
    )

    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        low_precision = PrepareTrainingFactBindingsService().prepare(
            candidates=(candidate,)
        )[0]
    with localcontext() as context:
        context.prec = 50
        high_precision = PrepareTrainingFactBindingsService().prepare(
            candidates=(candidate,)
        )[0]

    assert low_precision.fact_hash == high_precision.fact_hash
    assert low_precision.content_payload.provider_mapping.confidence == Decimal(
        "0.12345678901234567890123456789"
    )


def test_training_fact_service_rejects_missing_rights() -> None:
    with pytest.raises(ValueError, match="source rights admission is required"):
        _admit(None, (_candidate(),))


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("EXPIRED", "source rights admission is not active"),
        ("USE_MISMATCH", "permitted-use mismatch"),
        ("SCOPE_MISMATCH", "not covered by source rights"),
    ),
)
def test_training_fact_service_rejects_expired_or_mismatched_rights(
    case: str,
    message: str,
) -> None:
    if case == "EXPIRED":
        rights = _rights(expires_at_utc=LOCAL_IMPORTED)
    elif case == "USE_MISMATCH":
        rights = _rights(uses=(SourceRightsPermittedUse.ACQUIRE,))
    else:
        rights = _rights(source_ids=("different-source",))
    with pytest.raises(ValidationError, match=message):
        _admit(rights, (_candidate(),))


@pytest.mark.parametrize(
    "category",
    (
        ProviderResultStatusCategory.EXTRA_TIME_ONLY_RESULT,
        ProviderResultStatusCategory.PENALTY_RESULT,
        ProviderResultStatusCategory.ABANDONED,
        ProviderResultStatusCategory.POSTPONED,
        ProviderResultStatusCategory.CANCELLED,
        ProviderResultStatusCategory.IN_PROGRESS,
        ProviderResultStatusCategory.UNKNOWN,
        ProviderResultStatusCategory.AMBIGUOUS,
    ),
)
def test_match_result_admission_rejects_every_non_final_category(
    category: ProviderResultStatusCategory,
) -> None:
    with pytest.raises(ValidationError, match="explicit regular-time-final"):
        _result_admission_content(provider_status_category=category)


def test_match_result_admission_requires_exact_normalized_status() -> None:
    payload = _result_admission_content().model_dump(mode="python")
    payload["normalized_status"] = "FINISHED"

    with pytest.raises(ValidationError, match="REGULAR_TIME_FINAL"):
        MatchResultAdmissionContentV1.model_validate(payload)


def test_match_result_correction_fails_closed_without_a_persisted_predecessor() -> None:
    content = _result_admission_content().model_copy(
        update={"supersedes_match_result_id": "prior-result"}
    )

    with pytest.raises(ValidationError, match="persisted predecessor validation"):
        MatchResultAdmissionV1.freeze(content_payload=content)


@pytest.mark.parametrize(
    "semantics",
    (
        ResultScoreSemantics.EXTRA_TIME_INCLUDED,
        ResultScoreSemantics.PENALTIES_INCLUDED,
        ResultScoreSemantics.UNKNOWN_OR_AMBIGUOUS,
    ),
)
def test_match_result_admission_rejects_wrong_score_semantics(
    semantics: ResultScoreSemantics,
) -> None:
    with pytest.raises(ValidationError, match="regular-time-only semantics"):
        _result_admission_content(score_semantics=semantics)


def test_training_fact_rejects_score_or_result_hash_mismatch() -> None:
    candidate = _candidate()
    result_payload = candidate.normalized_result.model_dump(mode="python")
    result_payload.update(home_goals=3, payload_hash="0" * 64)
    mismatched = candidate.model_copy(
        update={"normalized_result": MatchResult.model_validate(result_payload)}
    )

    with pytest.raises(ValidationError, match="does not match its result admission"):
        _admit(_rights(), (mismatched,))

    result_payload = candidate.normalized_result.model_dump(mode="python")
    result_payload["payload_hash"] = "0" * 64
    bad_hash = candidate.model_copy(
        update={"normalized_result": MatchResult.model_validate(result_payload)}
    )
    with pytest.raises(ValidationError, match="score hash is inconsistent"):
        _admit(_rights(), (bad_hash,))

    wrong_record_hash = candidate.match_result_admission.content_payload.model_copy(
        update={"normalized_record_sha256": "0" * 64}
    )
    mismatched_admission = candidate.model_copy(
        update={
            "match_result_admission": MatchResultAdmissionV1.freeze(
                content_payload=wrong_record_hash
            )
        }
    )
    with pytest.raises(ValidationError, match="record hash is inconsistent"):
        _admit(_rights(), (mismatched_admission,))


def test_training_fact_hash_seals_mapping_and_exact_match_identity() -> None:
    candidate = _candidate()
    baseline = _admit(_rights(), (candidate,)).facts[0]
    different_mapping = _admit(
        _rights(),
        (
            candidate.model_copy(
                update={
                    "provider_mapping": candidate.provider_mapping.model_copy(
                        update={"resolution_method": "REVIEWED_EXPLICIT_MAPPING"}
                    )
                }
            ),
        ),
    ).facts[0]
    different_identity = _admit(
        _rights(),
        (
            candidate.model_copy(
                update={
                    "canonical_identity": candidate.canonical_identity.model_copy(
                        update={
                            "internal_home_team_id": "away-1",
                            "internal_away_team_id": "home-1",
                        }
                    )
                }
            ),
        ),
    ).facts[0]

    assert len(
        {baseline.fact_hash, different_mapping.fact_hash, different_identity.fact_hash}
    ) == 3
    assert baseline.content_payload.provider_mapping.mapping_id == (
        candidate.provider_mapping.mapping_id
    )
    assert baseline.content_payload.canonical_identity.internal_home_team_id == (
        candidate.canonical_identity.internal_home_team_id
    )

    tampered = baseline.model_dump(mode="python")
    tampered["content_payload"]["canonical_identity"]["kickoff_at_utc"] += timedelta(
        seconds=1
    )
    with pytest.raises(ValidationError, match="training fact binding hash"):
        TrainingFactBindingV1.model_validate(tampered)


def test_season_membership_rejects_mapping_and_canonical_mismatch() -> None:
    candidate = _candidate()
    wrong_mapping = candidate.provider_mapping.model_copy(
        update={"external_match_id": "different-fixture"}
    )
    with pytest.raises(ValueError, match="does not match provider mapping"):
        _admit(
            _rights(),
            (candidate.model_copy(update={"provider_mapping": wrong_mapping}),),
        )

    wrong_identity = candidate.canonical_identity.model_copy(
        update={"season": "wrong-season"}
    )
    with pytest.raises(ValueError, match="does not match canonical identity"):
        _admit(
            _rights(),
            (candidate.model_copy(update={"canonical_identity": wrong_identity}),),
        )


def test_season_membership_rejects_non_fixture_mapping_namespace() -> None:
    candidate = _candidate()
    wrong_namespace = candidate.provider_mapping.model_copy(
        update={"external_namespace": "result"}
    )

    with pytest.raises(ValidationError, match="does not match provider mapping"):
        _admit(
            _rights(),
            (candidate.model_copy(update={"provider_mapping": wrong_namespace}),),
        )


def test_season_membership_rejects_ambiguity_and_heuristic_fields() -> None:
    payload = _season_membership_content().model_dump(mode="python")
    payload["provider_season_candidate_ids"] = (
        "provider-season-2025",
        "provider-season-2026",
    )
    with pytest.raises(ValidationError, match="explicit and unambiguous"):
        MatchSeasonMembershipContentV1.model_validate(payload)

    for forbidden in (
        "inferred_from_match_date",
        "inferred_from_directory",
        "target_season_id",
        "constructor_season_id",
    ):
        payload = _season_membership_content().model_dump(mode="python")
        payload[forbidden] = "forbidden"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            MatchSeasonMembershipContentV1.model_validate(payload)


@pytest.mark.parametrize(
    "method",
    tuple(
        method
        for method in SeasonAssignmentMethod
        if method is not SeasonAssignmentMethod.PROVIDER_EXPLICIT_FIELDS
    ),
)
def test_season_membership_rejects_non_explicit_assignment_methods(
    method: SeasonAssignmentMethod,
) -> None:
    payload = _season_membership_content().model_dump(mode="python")
    payload["season_assignment_method"] = method

    with pytest.raises(ValidationError, match="explicit provider fields"):
        MatchSeasonMembershipContentV1.model_validate(payload)


def test_season_membership_requires_distinct_provider_field_paths() -> None:
    payload = _season_membership_content().model_dump(mode="python")
    payload["provider_fixture_field_path"] = payload["provider_season_field_path"]

    with pytest.raises(ValidationError, match="field paths must be distinct"):
        MatchSeasonMembershipContentV1.model_validate(payload)


@pytest.mark.parametrize("offset", (timedelta(0), timedelta(seconds=1)))
def test_training_fact_requires_result_finalization_after_kickoff(
    offset: timedelta,
) -> None:
    candidate = _candidate()
    finalized = candidate.match_result_admission.content_payload.provider_finalized_at_utc
    identity = candidate.canonical_identity.model_copy(
        update={"kickoff_at_utc": finalized + offset}
    )

    with pytest.raises(ValidationError, match="finalized after kickoff"):
        _admit(
            _rights(),
            (candidate.model_copy(update={"canonical_identity": identity}),),
        )


def test_retrospective_archive_and_review_chronology_is_enforced() -> None:
    fixture_payload = _candidate().fixture_source.model_dump(mode="python")
    fixture_payload["fixture_source_archive_created_at_utc"] = (
        LOCAL_IMPORTED - timedelta(seconds=1)
    )
    with pytest.raises(ValidationError, match="fixture source timestamps"):
        TrainingFixtureSourceV1.model_validate(fixture_payload)

    membership_payload = _season_membership_content().model_dump(mode="python")
    membership_payload["provider_scope_created_at_utc"] = (
        LOCAL_IMPORTED - timedelta(seconds=1)
    )
    with pytest.raises(ValidationError, match="season membership timestamps"):
        MatchSeasonMembershipContentV1.model_validate(membership_payload)

    result_payload = _result_admission_content().model_dump(mode="python")
    result_payload["registered_at_utc"] = SOURCE_REVIEWED + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="result admission timestamps"):
        MatchResultAdmissionContentV1.model_validate(result_payload)


def test_child_review_must_precede_training_admission_start() -> None:
    candidate = _candidate()
    late_result_content = candidate.match_result_admission.content_payload.model_copy(
        update={"reviewed_at_utc": ADMISSION_STARTED + timedelta(seconds=1)}
    )
    late_result = candidate.model_copy(
        update={
            "match_result_admission": MatchResultAdmissionV1.freeze(
                content_payload=late_result_content
            )
        }
    )

    with pytest.raises(ValidationError, match="prerequisites cannot follow"):
        _admit(_rights(), (late_result,))

    membership_content = candidate.season_membership.content_payload.model_copy(
        update={"reviewed_at_utc": ADMISSION_STARTED + timedelta(seconds=1)}
    )
    late_membership = candidate.model_copy(
        update={
            "season_membership": MatchSeasonMembershipV1.freeze(
                content_payload=membership_content
            )
        }
    )
    with pytest.raises(ValidationError, match="prerequisites cannot follow"):
        _admit(_rights(), (late_membership,))


def test_training_admission_rejects_conflicting_source_identity_reuse() -> None:
    first = _candidate(1)
    second = _candidate(2)
    duplicate_mapping = second.provider_mapping.model_copy(
        update={"mapping_id": first.provider_mapping.mapping_id}
    )
    duplicate_mapping_membership = second.season_membership.content_payload.model_copy(
        update={"provider_mapping_id": first.provider_mapping.mapping_id}
    )
    second_with_duplicate_mapping = second.model_copy(
        update={
            "provider_mapping": duplicate_mapping,
            "season_membership": MatchSeasonMembershipV1.freeze(
                content_payload=duplicate_mapping_membership
            ),
        }
    )
    with pytest.raises(ValidationError, match="unique source lineage"):
        _admit(_rights(), (first, second_with_duplicate_mapping))

    duplicate_fixture_key = first.provider_mapping.external_match_id
    duplicate_key_mapping = second.provider_mapping.model_copy(
        update={"external_match_id": duplicate_fixture_key}
    )
    duplicate_key_fixture = second.fixture_source.model_copy(
        update={"provider_fixture_key": duplicate_fixture_key}
    )
    duplicate_key_membership_content = (
        second.season_membership.content_payload.model_copy(
            update={"provider_fixture_key": duplicate_fixture_key}
        )
    )
    second_with_duplicate_key = second.model_copy(
        update={
            "provider_mapping": duplicate_key_mapping,
            "fixture_source": duplicate_key_fixture,
            "season_membership": MatchSeasonMembershipV1.freeze(
                content_payload=duplicate_key_membership_content
            ),
        }
    )
    with pytest.raises(ValidationError, match="unique source lineage"):
        _admit(_rights(), (first, second_with_duplicate_key))


def test_training_admission_rejects_conflicting_archive_identity_metadata() -> None:
    first = _candidate(1)
    second = _candidate(2)
    conflicting_archive = second.fixture_source.model_copy(
        update={"fixture_source_archive_payload_sha256": "9" * 64}
    )

    with pytest.raises(ValidationError, match="archive identity metadata"):
        _admit(
            _rights(),
            (second.model_copy(update={"fixture_source": conflicting_archive}), first),
        )

    conflicting_possession_time = second.fixture_source.model_copy(
        update={"local_imported_at_utc": LOCAL_IMPORTED + timedelta(seconds=1)}
    )
    with pytest.raises(ValidationError, match="archive identity metadata"):
        _admit(
            _rights(),
            (
                first,
                second.model_copy(
                    update={"fixture_source": conflicting_possession_time}
                ),
            ),
        )


def test_training_admission_rejects_source_record_identity_aliases() -> None:
    first = _candidate(1)
    second = _candidate(2)
    aliased_hash = first.fixture_source.fixture_record_sha256
    aliased_fixture = second.fixture_source.model_copy(
        update={
            "fixture_source_archive_id": "fixture-archive-copy",
            "fixture_record_sha256": aliased_hash,
        }
    )
    aliased_membership_content = second.season_membership.content_payload.model_copy(
        update={"fixture_record_sha256": aliased_hash}
    )
    aliased_second = second.model_copy(
        update={
            "fixture_source": aliased_fixture,
            "season_membership": MatchSeasonMembershipV1.freeze(
                content_payload=aliased_membership_content
            ),
        }
    )

    with pytest.raises(ValidationError, match="fixture source record identity"):
        _admit(_rights(), (first, aliased_second))

    result_content = second.match_result_admission.content_payload.model_copy(
        update={
            "raw_record_sha256": (
                first.match_result_admission.content_payload.raw_record_sha256
            )
        }
    )
    result_alias = second.model_copy(
        update={
            "match_result_admission": MatchResultAdmissionV1.freeze(
                content_payload=result_content
            )
        }
    )
    with pytest.raises(ValidationError, match="result source record identity"):
        _admit(_rights(), (first, result_alias))


def test_training_admission_rejects_conflicting_provider_season_mapping() -> None:
    first = _candidate(1)
    second = _candidate(2)
    conflicting_membership_content = (
        second.season_membership.content_payload.model_copy(
            update={"canonical_season_id": "season-2026"}
        )
    )
    conflicting_second = second.model_copy(
        update={
            "canonical_identity": second.canonical_identity.model_copy(
                update={"season": "season-2026"}
            ),
            "season_membership": MatchSeasonMembershipV1.freeze(
                content_payload=conflicting_membership_content
            ),
        }
    )

    with pytest.raises(ValidationError, match="season scope mapping is inconsistent"):
        _admit(_rights(), (first, conflicting_second))


def test_training_fact_order_uses_effective_availability_before_match_id() -> None:
    first = _candidate(1)
    second = _candidate(2)
    shared_kickoff = SOURCE_AVAILABLE - timedelta(days=10)
    late_membership_content = first.season_membership.content_payload.model_copy(
        update={"source_available_at_utc": SOURCE_AVAILABLE + timedelta(hours=1)}
    )
    first = first.model_copy(
        update={
            "canonical_identity": first.canonical_identity.model_copy(
                update={"kickoff_at_utc": shared_kickoff}
            ),
            "season_membership": MatchSeasonMembershipV1.freeze(
                content_payload=late_membership_content
            ),
        }
    )
    second = second.model_copy(
        update={
            "canonical_identity": second.canonical_identity.model_copy(
                update={"kickoff_at_utc": shared_kickoff}
            )
        }
    )

    facts = PrepareTrainingFactBindingsService().prepare(candidates=(first, second))

    assert tuple(fact.content_payload.normalized_result.match_id for fact in facts) == (
        "match-2",
        "match-1",
    )
    TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=_rights(),
        facts=facts,
        actual_started_at_utc=ADMISSION_STARTED,
        actual_completed_at_utc=ADMISSION_COMPLETED,
        persisted_at_utc=ADMISSION_PERSISTED,
    )

    wrong_order = tuple(
        TrainingFactBindingV1.freeze(
            content_payload=fact.content_payload.model_copy(update={"sequence": sequence})
        )
        for sequence, fact in enumerate(reversed(facts))
    )
    with pytest.raises(ValidationError, match="canonical order"):
        TrainingFactAdmissionV1.from_persisted(
            source_rights_admission=_rights(),
            facts=wrong_order,
            actual_started_at_utc=ADMISSION_STARTED,
            actual_completed_at_utc=ADMISSION_COMPLETED,
            persisted_at_utc=ADMISSION_PERSISTED,
        )


def test_source_availability_and_local_import_are_distinct_and_ordered() -> None:
    candidate = _candidate()
    assert candidate.fixture_source.source_available_at_utc == SOURCE_AVAILABLE
    assert candidate.fixture_source.local_imported_at_utc == LOCAL_IMPORTED
    assert candidate.normalized_result.ingested_at_utc == SOURCE_AVAILABLE

    fixture_payload = candidate.fixture_source.model_dump(mode="python")
    fixture_payload["local_imported_at_utc"] = SOURCE_AVAILABLE - timedelta(minutes=1)
    with pytest.raises(ValidationError, match="fixture source timestamps"):
        TrainingFixtureSourceV1.model_validate(fixture_payload)

    result_payload = candidate.normalized_result.model_dump(mode="python")
    result_payload["ingested_at_utc"] = LOCAL_IMPORTED
    forged = candidate.model_copy(
        update={"normalized_result": MatchResult.model_validate(result_payload)}
    )
    with pytest.raises(ValidationError, match="preserve source time"):
        _admit(_rights(), (forged,))


def test_synthetic_classification_and_bare_match_result_are_not_eligible() -> None:
    admission = _training_admission()
    rights_payload = (
        admission.source_rights_admission.content_payload.rights_payload.model_dump(
            mode="python"
        )
    )
    rights_payload["source_classification"] = "SYNTHETIC_ACCEPTANCE_DATA"
    with pytest.raises(ValidationError, match="REAL_SOURCE_DATA"):
        SourceRightsPayloadV1.model_validate(rights_payload)

    content = admission.content_payload.model_dump(mode="python")
    content["source_classification"] = "SYNTHETIC_ACCEPTANCE_DATA"
    with pytest.raises(ValidationError, match="REAL_SOURCE_DATA"):
        TrainingFactAdmissionContentV1.model_validate(content)

    candidate_payload = _candidate().model_dump(mode="python")
    candidate_payload.pop("match_result_admission")
    with pytest.raises(ValidationError, match="match_result_admission"):
        TrainingFactAdmissionCandidateV1.model_validate(candidate_payload)


def test_artifact_content_payloads_exclude_their_own_identity() -> None:
    admission = _training_admission()
    artifacts = (
        (
            admission.source_rights_admission,
            "source_rights_admission_id",
            "admission_hash",
        ),
        (
            admission.facts[0].content_payload.season_membership,
            "season_membership_id",
            "membership_hash",
        ),
        (
            admission.facts[0].content_payload.match_result_admission,
            "match_result_admission_id",
            "admission_hash",
        ),
        (admission.facts[0], "training_fact_binding_id", "fact_hash"),
        (admission, "training_fact_admission_id", "admission_hash"),
    )

    for artifact, id_field, hash_field in artifacts:
        content = artifact.content_payload.model_dump(mode="python")
        assert id_field not in content
        assert hash_field not in content
        digest = tagged_canonical_sha256(artifact.schema_version, content)
        assert digest == getattr(artifact, hash_field)
        assert getattr(artifact, id_field) == stable_id(
            artifact.schema_version,
            digest,
        )


def _rights(
    *,
    source_ids: tuple[str, ...] = ("source-main",),
    uses: tuple[SourceRightsPermittedUse, ...] = ALL_USES,
    expires_at_utc: datetime = RIGHTS_EXPIRES,
):
    payload = SourceRightsPayloadV1.freeze(
        source_owner="Example Data Owner",
        product_name="Example Results Archive",
        source_ids=source_ids,
        terms_version="terms-v1",
        terms_reference="local://terms/example-v1",
        terms_sha256="a" * 64,
        jurisdiction="DE",
        effective_at_utc=RIGHTS_EFFECTIVE,
        expires_at_utc=expires_at_utc,
        permitted_uses=uses,
        raw_retention_rule="Retain raw source locally until expiry.",
        derived_retention_rule="Retain normalized facts until expiry.",
        subscription_end_retention_rule="Delete raw source at terms expiry.",
        deletion_obligation="Delete raw bytes; retain permitted audit hashes.",
        public_repository_boundary="Metadata and hashes only; no raw data.",
    )
    attestation = SealSourceRightsReviewService().seal(
        rights_payload=payload,
        authorized_reviewer="reviewer-1",
        reviewer_authority_reference="local://authority/reviewer-1",
        authority_sha256="c" * 64,
        reviewed_at_utc=RIGHTS_REVIEWED,
        evidence=LocalReviewEvidenceV1(
            evidence_reference="local://rights/review-1",
            evidence_sha256="b" * 64,
        ),
    )
    return SourceRightsAdmissionV1.from_recorded(
        rights_payload=payload,
        reviewer_attestation=attestation,
        recorded_at_utc=RIGHTS_RECORDED,
    )


def _result_admission_content(
    index: int = 1,
    *,
    provider_status_category: ProviderResultStatusCategory = (
        ProviderResultStatusCategory.REGULAR_TIME_FINAL
    ),
    score_semantics: ResultScoreSemantics = ResultScoreSemantics.REGULAR_TIME_ONLY,
) -> MatchResultAdmissionContentV1:
    return MatchResultAdmissionContentV1(
        source_id="source-main",
        internal_match_id=f"match-{index}",
        match_result_id=f"result-{index}",
        provider_code="provider-main",
        provider_result_key=f"provider-result-{index}",
        provider_raw_status="FT",
        status_mapping_version="provider-status-v1",
        provider_status_category=provider_status_category,
        normalized_status="REGULAR_TIME_FINAL",
        score_semantics=score_semantics,
        regular_time_home_goals=2,
        regular_time_away_goals=1,
        provider_finalized_at_utc=SOURCE_AVAILABLE - timedelta(minutes=2),
        source_observed_at_utc=SOURCE_AVAILABLE - timedelta(minutes=1),
        source_available_at_utc=SOURCE_AVAILABLE,
        raw_artifact_id=f"raw-result-artifact-{index}",
        raw_artifact_payload_sha256="2" * 64,
        raw_artifact_created_at_utc=ARCHIVE_CREATED,
        raw_record_sha256=_record_hash("result", index),
        normalized_record_sha256=normalized_match_result_record_sha256(
            _normalized_result(index)
        ),
        local_imported_at_utc=LOCAL_IMPORTED,
        registered_at_utc=REGISTERED,
        adapter_name="example-result-adapter",
        adapter_version="1",
        reviewed_by="result-reviewer",
        reviewed_at_utc=SOURCE_REVIEWED,
    )


def _result_admission(index: int = 1) -> MatchResultAdmissionV1:
    return MatchResultAdmissionV1.freeze(
        content_payload=_result_admission_content(index)
    )


def _season_membership_content(index: int = 1) -> MatchSeasonMembershipContentV1:
    return MatchSeasonMembershipContentV1(
        source_id="source-main",
        provider_code="provider-main",
        provider_competition_id="provider-bundesliga",
        provider_season_id="provider-season-2025",
        provider_season_candidate_ids=("provider-season-2025",),
        provider_fixture_namespace="fixture",
        provider_fixture_key=f"provider-fixture-{index}",
        provider_mapping_id=f"mapping-{index}",
        internal_match_id=f"match-{index}",
        fixture_source_record_id=f"fixture-record-{index}",
        fixture_record_sha256=_record_hash("fixture", index),
        provider_scope_raw_artifact_id="provider-scope-artifact-1",
        provider_scope_payload_sha256="3" * 64,
        provider_scope_created_at_utc=ARCHIVE_CREATED,
        provider_scope_record_sha256="1" * 64,
        provider_competition_field_path="fixture.league.id",
        provider_season_field_path="fixture.league.season.id",
        provider_fixture_field_path="fixture.id",
        season_assignment_method=SeasonAssignmentMethod.PROVIDER_EXPLICIT_FIELDS,
        season_mapping_version="season-map-v1",
        canonical_competition_id="bundesliga",
        canonical_season_id="season-2025",
        source_available_at_utc=SOURCE_AVAILABLE,
        local_imported_at_utc=LOCAL_IMPORTED,
        registered_at_utc=REGISTERED,
        mapping_policy_version="mapping-policy-v1",
        reviewed_by="season-reviewer",
        reviewed_at_utc=SOURCE_REVIEWED,
    )


def _season_membership(index: int = 1) -> MatchSeasonMembershipV1:
    return MatchSeasonMembershipV1.freeze(
        content_payload=_season_membership_content(index)
    )


def _candidate(index: int = 1) -> TrainingFactAdmissionCandidateV1:
    mapping = ProviderMatchMapping(
        mapping_id=f"mapping-{index}",
        provider_code="provider-main",
        external_namespace="fixture",
        external_match_id=f"provider-fixture-{index}",
        internal_match_id=f"match-{index}",
        resolution_method="EXPLICIT_MAPPING",
        confidence=Decimal("1"),
        available_at_utc=SOURCE_AVAILABLE,
    )
    identity = CanonicalMatchIdentity(
        internal_match_id=f"match-{index}",
        internal_competition_id="bundesliga",
        internal_home_team_id=f"home-{index}",
        internal_away_team_id=f"away-{index}",
        season="season-2025",
        competition_type="DOMESTIC_LEAGUE",
        kickoff_at_utc=SOURCE_AVAILABLE - timedelta(days=7 - index),
    )
    result = _normalized_result(index)
    return TrainingFactAdmissionCandidateV1(
        fixture_source=TrainingFixtureSourceV1(
            source_id="source-main",
            provider_code="provider-main",
            provider_fixture_namespace="fixture",
            provider_fixture_key=f"provider-fixture-{index}",
            internal_match_id=f"match-{index}",
            fixture_source_archive_id="fixture-archive-1",
            fixture_source_archive_payload_sha256="4" * 64,
            fixture_source_archive_created_at_utc=ARCHIVE_CREATED,
            fixture_source_record_id=f"fixture-record-{index}",
            fixture_record_sha256=_record_hash("fixture", index),
            source_available_at_utc=SOURCE_AVAILABLE,
            local_imported_at_utc=LOCAL_IMPORTED,
            registered_at_utc=REGISTERED,
        ),
        season_membership=_season_membership(index),
        provider_mapping=mapping,
        canonical_identity=identity,
        match_result_admission=_result_admission(index),
        normalized_result=result,
    )


def _normalized_result(index: int = 1) -> MatchResult:
    return MatchResult(
        match_result_id=f"result-{index}",
        match_id=f"match-{index}",
        provider_code="provider-main",
        home_goals=2,
        away_goals=1,
        observed_at_utc=SOURCE_AVAILABLE - timedelta(minutes=1),
        available_at_utc=SOURCE_AVAILABLE,
        ingested_at_utc=SOURCE_AVAILABLE,
        source_result_key=f"provider-result-{index}",
        payload_hash=(
            "8263e5c66ce7f77335153848f77cdc7f8eab33cbf668006ccace5b7ff3c1e933"
        ),
    )


def _record_hash(kind: str, index: int) -> str:
    return hashlib.sha256(f"{kind}-record-{index}".encode("ascii")).hexdigest()


def _admit(rights, candidates):
    facts = PrepareTrainingFactBindingsService().prepare(
        candidates=candidates,
    )
    return TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=rights,
        facts=facts,
        actual_started_at_utc=ADMISSION_STARTED,
        actual_completed_at_utc=ADMISSION_COMPLETED,
        persisted_at_utc=ADMISSION_PERSISTED,
    )


def _training_admission():
    return _admit(_rights(), (_candidate(),))
