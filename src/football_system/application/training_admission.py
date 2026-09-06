from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from football_system.domain.common import DomainModel
from football_system.domain.identity import CanonicalMatchIdentity
from football_system.domain.match import ProviderMatchMapping
from football_system.domain.settlement import MatchResult
from football_system.domain.training_admission import (
    SOURCE_RIGHTS_PAYLOAD_V1,
    LocalReviewEvidenceV1,
    LocalReviewerAttestationContentV1,
    LocalReviewerAttestationV1,
    MatchResultAdmissionV1,
    MatchSeasonMembershipV1,
    SourceRightsPayloadV1,
    NormalizedMatchResultRecordV1,
    TrainingCanonicalMatchIdentityV1,
    TrainingFactBindingContentV1,
    TrainingFactBindingV1,
    TrainingFixtureSourceV1,
    TrainingProviderMatchMappingV1,
    tagged_canonical_sha256,
    training_fact_order_key,
)


class TrainingFactAdmissionCandidateV1(DomainModel):
    fixture_source: TrainingFixtureSourceV1
    season_membership: MatchSeasonMembershipV1
    provider_mapping: ProviderMatchMapping
    canonical_identity: CanonicalMatchIdentity
    match_result_admission: MatchResultAdmissionV1
    normalized_result: MatchResult


class SealSourceRightsReviewService:
    """Seal caller-established local review evidence without performing I/O."""

    def seal(
        self,
        *,
        rights_payload: SourceRightsPayloadV1,
        authorized_reviewer: str,
        reviewer_authority_reference: str,
        authority_sha256: str,
        reviewed_at_utc: datetime,
        evidence: LocalReviewEvidenceV1,
    ) -> LocalReviewerAttestationV1:
        rights_payload = SourceRightsPayloadV1.model_validate(
            rights_payload.model_dump(mode="python")
        )
        evidence = LocalReviewEvidenceV1.model_validate(
            evidence.model_dump(mode="python")
        )
        rights_payload_hash = tagged_canonical_sha256(
            SOURCE_RIGHTS_PAYLOAD_V1,
            rights_payload,
        )
        return LocalReviewerAttestationV1.freeze(
            content_payload=LocalReviewerAttestationContentV1(
                attested_schema_version=SOURCE_RIGHTS_PAYLOAD_V1,
                attested_payload_hash=rights_payload_hash,
                authorized_reviewer=authorized_reviewer,
                reviewer_authority_reference=reviewer_authority_reference,
                authority_sha256=authority_sha256,
                reviewed_at_utc=reviewed_at_utc,
                evidence=evidence,
            )
        )


class PrepareTrainingFactBindingsService:
    """Validate and seal fact bindings for a future atomic persistence operation."""

    def prepare(
        self,
        *,
        candidates: Iterable[TrainingFactAdmissionCandidateV1],
    ) -> tuple[TrainingFactBindingV1, ...]:
        validated = tuple(
            TrainingFactAdmissionCandidateV1.model_validate(
                candidate.model_dump(mode="python")
            )
            for candidate in candidates
        )
        ordered = tuple(
            sorted(
                validated,
                key=lambda item: training_fact_order_key(
                    fixture_source=item.fixture_source,
                    season_membership=item.season_membership,
                    match_result_admission=item.match_result_admission,
                    canonical_identity=item.canonical_identity,
                    normalized_result=item.normalized_result,
                ),
            )
        )
        if not ordered:
            raise ValueError("training fact admission requires at least one fact")
        return tuple(
            TrainingFactBindingV1.freeze(
                content_payload=TrainingFactBindingContentV1(
                    sequence=sequence,
                    fixture_source=candidate.fixture_source,
                    provider_mapping=TrainingProviderMatchMappingV1.from_mapping(
                        candidate.provider_mapping
                    ),
                    canonical_identity=TrainingCanonicalMatchIdentityV1.from_identity(
                        candidate.canonical_identity
                    ),
                    season_membership=candidate.season_membership,
                    match_result_admission=candidate.match_result_admission,
                    normalized_result=NormalizedMatchResultRecordV1.from_result(
                        candidate.normalized_result
                    ),
                ),
            )
            for sequence, candidate in enumerate(ordered)
        )
