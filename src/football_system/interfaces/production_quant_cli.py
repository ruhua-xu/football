"""Local-only production quant operations. Never acquire data or create authority.

Request/schema discovery is independent of the database and evidence filesystem.
Only explicitly supplied files are preflighted here; database-resolved evidence
remains subject to repository verification (including the pilot reservation gate).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    RootModel,
    Tag,
    TypeAdapter,
    model_validator,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.application.post_review import (
    CreateFusionRunService,
    CreatePortfolioRevisionService,
)
from football_system.application.review_bridge import validate_review_files
from football_system.application.training_admission import (
    PrepareTrainingFactBindingsService,
)
from football_system.application.training_correction import (
    TrainingCorrectionJsonAdapterV2,
)
from football_system.domain.archive import canonical_json
from football_system.domain.common import UtcDateTime, utc_now
from football_system.config import AppSettings
from football_system.domain.production_release import (
    ApprovedTrainingHistoryAuditV1,
    EloTrainingWindowV1,
    ProductionGrantKind,
    ProductionGrantV1,
    ProductionTargetV1,
    ReleaseSnapshotV1,
    RetentionHorizonV1,
    ReleaseArtifactRefV1,
    TrainingHistoryApprovalPayloadV2,
    TrainingHistoryApprovalV2,
    revalidate,
)
from football_system.domain.review import (
    AnalysisPacketV3,
    LLMReviewSubmissionV3,
    MAX_CONTRACT_FILE_BYTES,
)
from football_system.domain.quant_integrity import (
    IntegrityArtifact,
    IntegrityArtifactRefV1,
    QuantIntegrityPlanDefinitionV1,
    QuantIntegrityPlanDefinitionV2,
    QuantIntegrityPlanV1,
    QuantIntegrityReportV1,
    QuantIntegrityTargetV2,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    LocalReviewerAttestationV1,
    Reference,
    RuleText,
    Sha256Digest,
    SourceRightsPayloadV1,
    TrainingFactAdmissionV1,
)
from football_system.domain.training_correction import (
    CorrectionCaptureRefV2,
    CorrectionEvidenceV2,
    CorrectionRefV2,
    TrainingCorrectionAdmissionV2,
    TrainingCorrectionIntentV2,
    TrainingFactVersionV2,
)
from football_system.domain.versioned_training_history import (
    TrainingHistoryContextPinV2,
    VersionedFactRefV2,
    select_versioned_training_heads,
)
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.production_quant_repository import (
    ApprovalRecordingContractConflict,
    ProductionRevocationRequestV1,
    SqlAlchemyProductionQuantRepository,
)
from football_system.infrastructure.database.quant_integrity_repository import (
    QuantIntegrityBuildRecipeV1,
    QuantIntegrityReviewDocumentV1,
    QuantIntegrityScheduleDocumentV1,
    QuantIntegrityScopeDocumentV1,
    SqlAlchemyQuantIntegrityRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
    require_sqlite_database_url,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
    SqlAlchemyTrainingAdmissionRepository,
)
from football_system.infrastructure.database.training_correction_repository import (
    SqlAlchemyTrainingCorrectionRepository,
)
from football_system.infrastructure.files.training_evidence import (
    LocalTrainingEvidence,
    ReviewerAuthorityV1,
    TrainingFactSubmissionV1,
    TrainingJsonAdapterV1,
    TrainingReviewDocumentV1,
    strict_json_bytes,
)

MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_PINS_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
OperationId = Annotated[
    str, Field(strict=True, min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")
]


class RequestV1(ReleaseSnapshotV1):
    # ReleaseSnapshotV1 also revalidates nested model_copy/model_construct inputs.
    model_config = ConfigDict(str_strip_whitespace=False)


class AuthorityPinsV1(RequestV1):
    """Previously approved trust configuration, not an authority-creation request."""

    schema_version: Literal["PRODUCTION_QUANT_AUTHORITY_PINS_V1"] = (
        "PRODUCTION_QUANT_AUTHORITY_PINS_V1"
    )
    trusted_authorities: dict[Reference, Sha256Digest] = Field(min_length=1)


class RightsRecordRequestV1(RequestV1):
    request_key: OperationId
    rights_payload: SourceRightsPayloadV1
    reviewer_attestation: LocalReviewerAttestationV1


class CaptureRequestV1(RequestV1):
    request_key: OperationId
    source_rights_admission_id: OperationId
    source_id: OperationId
    provider_code: OperationId
    evidence_reference: Reference

    @model_validator(mode="after")
    def validate_reference_syntax(self) -> Self:
        reference = self.evidence_reference
        if (
            PurePosixPath(reference).is_absolute()
            or any(character in reference for character in ("\\", ":", "\x00"))
            or any(part in {"", ".", ".."} for part in reference.split("/"))
        ):
            raise ValueError("capture requires a contained relative POSIX reference")
        return self


class AdmitRequestV1(RequestV1):
    request_key: OperationId
    source_rights_admission_id: OperationId
    submissions: tuple[TrainingFactSubmissionV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        PrepareTrainingFactBindingsService().prepare(
            candidates=(item.candidate for item in self.submissions)
        )
        ids = [
            s.candidate.canonical_identity.internal_match_id for s in self.submissions
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("admission requires unique matches")
        return self


class PilotPlanRequestV1(RequestV1):
    definition: QuantIntegrityPlanDefinitionV1

    @model_validator(mode="after")
    def validate_version(self) -> Self:
        if any(
            isinstance(t, QuantIntegrityTargetV2)
            for s in self.definition.slices
            for t in s.content_payload.targets
        ):
            raise ValueError("versioned targets require an explicit V2 plan context")
        return self


class PilotPlanRequestV2(RequestV1):
    definition: Annotated[
        QuantIntegrityPlanDefinitionV2, Field(discriminator="schema_version")
    ]

    @model_validator(mode="before")
    @classmethod
    def validate_explicit_versions(cls, value):
        value = cls.revalidate_nested_snapshots(value)
        if isinstance(value, dict) and isinstance(value.get("definition"), dict):
            definition = value["definition"]
            if definition.get("schema_version") != "QUANT_INTEGRITY_PLAN_DEFINITION_V2":
                raise ValueError("corrected pilot requires an explicit V2 definition")
            context = definition.get("correction_context")
            if not isinstance(context, dict) or context.get("schema_version") != (
                "TRAINING_HISTORY_CONTEXT_PIN_V2"
            ):
                raise ValueError("corrected pilot requires an explicit V2 context pin")
            for item in definition.get("slices", ()):
                for target in item["content_payload"]["targets"]:
                    if target.get("schema_version") != "QUANT_INTEGRITY_TARGET_V2" or (
                        target.get("fact", {}).get("schema_version")
                        != "VERSIONED_FACT_REF_V2"
                    ):
                        raise ValueError("corrected pilot requires explicit V2 targets")
        return value


def _request_version(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        subject = value.get("definition", value)
        if isinstance(subject, BaseModel):
            subject = subject.model_dump(mode="python")
        if isinstance(subject, dict):
            return subject.get("schema_version", "LEGACY_V1")
    return None


class PilotPlanRequest(
    RootModel[
        Annotated[
            Annotated[PilotPlanRequestV1, Tag("LEGACY_V1")]
            | Annotated[PilotPlanRequestV2, Tag("QUANT_INTEGRITY_PLAN_DEFINITION_V2")],
            Discriminator(_request_version),
        ]
    ]
):
    """Untagged legacy V1 or explicitly tagged V2, never inferred from fields."""

    model_config = ConfigDict(frozen=True)


class PilotReferenceRequestV1(RequestV1):
    plan_ref: IntegrityArtifactRefV1

    @model_validator(mode="after")
    def validate_plan_type(self) -> Self:
        if (
            self.plan_ref.schema_version
            != QuantIntegrityPlanV1.model_fields["schema_version"].default
        ):
            raise ValueError("pilot requires a plan reference")
        return self


class ManifestRequestV1(RequestV1):
    request_key: OperationId
    admission_ids: tuple[OperationId, ...] = Field(min_length=1)
    training_window: EloTrainingWindowV1
    integrity_pilot_scope_id: OperationId
    attestation_id: OperationId

    @model_validator(mode="after")
    def validate_admissions(self) -> Self:
        if len(self.admission_ids) != len(set(self.admission_ids)):
            raise ValueError("manifest requires unique admissions")
        return self


class ManifestRequestV2(ManifestRequestV1):
    schema_version: Literal["PRODUCTION_QUANT_MANIFEST_REQUEST_V2"]
    correction_context: Annotated[
        TrainingHistoryContextPinV2, Field(discriminator="schema_version")
    ]
    context_registered_at_utc: UtcDateTime
    selection_cutoff_at_utc: UtcDateTime
    exclude_match_ids: tuple[OperationId, ...] = ()

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.admission_ids != tuple(
            p.artifact_id for p in self.correction_context.base_admissions
        ):
            raise ValueError("manifest requires the exact ordered context admissions")
        if self.selection_cutoff_at_utc > self.context_registered_at_utc:
            raise ValueError("selection cutoff follows actual context verification")
        if self.exclude_match_ids != tuple(sorted(set(self.exclude_match_ids))):
            raise ValueError("manifest exclusions must be ordered and unique")
        return self


class ManifestRequest(
    RootModel[
        Annotated[
            Annotated[ManifestRequestV1, Tag("LEGACY_V1")]
            | Annotated[ManifestRequestV2, Tag("PRODUCTION_QUANT_MANIFEST_REQUEST_V2")],
            Discriminator(_request_version),
        ]
    ]
):
    """Legacy V1 requests retain their exact repository request identity."""

    model_config = ConfigDict(frozen=True)


class CorrectionReferenceRequestV2(RequestV1):
    receipt_id: OperationId
    record_pointer: str = Field(strict=True, max_length=2048, pattern=r"^(?:/.*)?$")


class CorrectionPrepareRequestV2(RequestV1):
    predecessor_version_id: OperationId
    source_rights_admission_id: OperationId
    evidence: CorrectionEvidenceV2
    match_result_id: OperationId | None


class CorrectionAdmitRequestV2(RequestV1):
    request_key: OperationId
    intent: Annotated[TrainingCorrectionIntentV2, Field(discriminator="schema_version")]
    reviewer_evidence: LocalReviewEvidenceV1
    reviewer_authority: LocalReviewEvidenceV1


class CorrectionContextRequestV2(RequestV1):
    base_admissions: tuple[CorrectionRefV2, ...] = Field(min_length=1)
    correction_ids: tuple[OperationId, ...]
    source_cutoffs: dict[OperationId, UtcDateTime] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pins(self) -> Self:
        TrainingHistoryContextPinV2(
            base_admissions=self.base_admissions, corrections=()
        )
        if len(self.correction_ids) != len(set(self.correction_ids)):
            raise ValueError("context requires unique correction IDs")
        return self


class ApprovalRecordRequestV1(RequestV1):
    """Unsupported legacy request; never automatically converted to V2."""

    request_key: OperationId
    manifest_id: OperationId
    grants: tuple[ProductionGrantV1, ...] = Field(min_length=1)
    review: LocalReviewEvidenceV1
    reviewer_authority: LocalReviewEvidenceV1


class ApprovalPrepareRequestV2(RequestV1):
    manifest_id: OperationId
    grants: tuple[ProductionGrantV1, ...] = Field(min_length=4, max_length=4)
    approver: OperationId
    reviewer_authority: LocalReviewEvidenceV1
    retention_compatibility: Literal["APPEND_ONLY_STATE_AND_AUDIT_COMPATIBLE"]
    supersedes_approval: ReleaseArtifactRefV1 | None = None
    supersession_effective_at_utc: UtcDateTime | None = None
    superseded_grants: tuple[ProductionGrantKind, ...] = ()


class ApprovalRecordRequestV2(RequestV1):
    request_key: OperationId
    approval_payload: TrainingHistoryApprovalPayloadV2
    reviewer_attestation: LocalReviewerAttestationV1


class ReleaseBuildRequestV1(RequestV1):
    request_key: OperationId
    approval_id: OperationId
    training_cutoff_at_utc: UtcDateTime
    state_retention_horizon: RetentionHorizonV1
    audit_retention_horizon: RetentionHorizonV1


class TargetPlanRequestV1(RequestV1):
    request_key: OperationId
    release_id: OperationId
    targets: tuple[ProductionTargetV1, ...] = Field(min_length=1)
    kickoff_window_start_at_utc: UtcDateTime
    kickoff_window_end_at_utc: UtcDateTime
    decision_as_of_at_utc: UtcDateTime
    selection_rule: Literal[
        "KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1"
    ]

    @model_validator(mode="after")
    def validate_targets(self) -> Self:
        ids = tuple(t.match_id for t in self.targets)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("target IDs must be unique and ordered")
        if self.kickoff_window_start_at_utc >= self.kickoff_window_end_at_utc or any(
            not (
                self.kickoff_window_start_at_utc
                <= target.kickoff_at_utc
                < self.kickoff_window_end_at_utc
                and self.decision_as_of_at_utc < target.kickoff_at_utc
            )
            for target in self.targets
        ):
            raise ValueError("invalid target decision/kickoff window")
        return self


class RevokePrepareRequestV1(RequestV1):
    approval_id: OperationId
    release_id: OperationId | None
    affected_grants: tuple[ProductionGrantKind, ...] = Field(min_length=1)
    actor: OperationId
    reviewer_authority: LocalReviewEvidenceV1
    reason: RuleText
    effective_at_utc: UtcDateTime | None = None


class RevokeRequestV1(RequestV1):
    request_key: OperationId
    revocation_request: ProductionRevocationRequestV1
    review: LocalReviewEvidenceV1


class InspectStoredV1(RequestV1):
    kind: Literal[
        "rights",
        "capture",
        "admission",
        "manifest",
        "approval",
        "release",
        "target-plan",
        "correction",
        "correction-version",
    ]
    artifact_id: OperationId


class InspectPilotV1(RequestV1):
    kind: Literal["pilot-plan", "pilot-report"]
    reference: IntegrityArtifactRefV1

    @model_validator(mode="after")
    def validate_reference_type(self) -> Self:
        expected = {
            "pilot-plan": QuantIntegrityPlanV1.model_fields["schema_version"].default,
            "pilot-report": QuantIntegrityReportV1.model_fields[
                "schema_version"
            ].default,
        }[self.kind]
        if self.reference.schema_version != expected:
            raise ValueError("inspection reference type mismatch")
        return self


class InspectAttemptsV1(RequestV1):
    kind: Literal["pilot-attempts"]
    series_id: OperationId


class InspectRequestV1(RequestV1):
    target: Annotated[
        InspectStoredV1 | InspectPilotV1 | InspectAttemptsV1,
        Field(discriminator="kind"),
    ]


class BundleExportRequestV1(RequestV1):
    analysis_run_id: OperationId
    bundle_directory: Path


class ReviewImportRequestV1(RequestV1):
    bundle_directory: Path
    review_file: Path


class FusionCreateRequestV1(RequestV1):
    review_artifact_id: OperationId
    config_file: Path


class PortfolioReviseRequestV1(RequestV1):
    fusion_run_id: OperationId
    config_file: Path


COMMAND_MODELS = {
    "rights-record": RightsRecordRequestV1,
    "capture": CaptureRequestV1,
    "admit": AdmitRequestV1,
    "correction-reference": CorrectionReferenceRequestV2,
    "correction-prepare": CorrectionPrepareRequestV2,
    "correction-admit": CorrectionAdmitRequestV2,
    "correction-context": CorrectionContextRequestV2,
    "pilot-plan": PilotPlanRequest,
    "pilot-run": PilotReferenceRequestV1,
    "pilot-attest": PilotReferenceRequestV1,
    "manifest": ManifestRequest,
    "approval-prepare": ApprovalPrepareRequestV2,
    "approval-record": ApprovalRecordRequestV2,
    "release-build": ReleaseBuildRequestV1,
    "target-plan": TargetPlanRequestV1,
    "revoke-prepare": RevokePrepareRequestV1,
    "revoke": RevokeRequestV1,
    "inspect": InspectRequestV1,
    "bundle-export": BundleExportRequestV1,
    "review-import": ReviewImportRequestV1,
    "fusion-create": FusionCreateRequestV1,
    "portfolio-revise": PortfolioReviseRequestV1,
}
SCHEMA_TYPES = {
    model.__name__: model
    for model in (
        *COMMAND_MODELS.values(),
        PilotPlanRequestV1,
        PilotPlanRequestV2,
        ManifestRequestV1,
        ManifestRequestV2,
        QuantIntegrityPlanDefinitionV1,
        QuantIntegrityPlanDefinitionV2,
        QuantIntegrityTargetV2,
        TrainingCorrectionJsonAdapterV2,
        CorrectionCaptureRefV2,
        CorrectionEvidenceV2,
        TrainingCorrectionIntentV2,
        TrainingCorrectionAdmissionV2,
        TrainingFactVersionV2,
        TrainingHistoryContextPinV2,
        VersionedFactRefV2,
        AuthorityPinsV1,
        ReviewerAuthorityV1,
        TrainingReviewDocumentV1,
        TrainingJsonAdapterV1,
        TrainingFactSubmissionV1,
        QuantIntegrityReviewDocumentV1,
        QuantIntegrityScopeDocumentV1,
        QuantIntegrityScheduleDocumentV1,
        QuantIntegrityBuildRecipeV1,
        ProductionRevocationRequestV1,
        AppSettings,
        AnalysisPacketV3,
        LLMReviewSubmissionV3,
        ApprovedTrainingHistoryAuditV1,
        TrainingHistoryApprovalPayloadV2,
        TrainingHistoryApprovalV2,
    )
}
READ_COMMANDS = frozenset(
    {
        "inspect",
        "revoke-prepare",
        "approval-prepare",
        "correction-reference",
        "correction-prepare",
        "correction-context",
    }
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally echoes unknown arguments and their potentially secret values.
        raise ValueError("invalid command arguments; consult --help")


def _read_local_bytes(path: Path, limit: int) -> bytes:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("input must be a regular local file")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if not payload or len(payload) > limit:
        raise ValueError("input is empty or exceeds the byte limit")
    return payload


def _read_json(path: Path, limit: int) -> object:
    return strict_json_bytes(_read_local_bytes(path, limit))


def _read_settings(path: Path) -> AppSettings:
    return AppSettings.model_validate(
        tomllib.loads(_read_local_bytes(path, MAX_CONFIG_BYTES).decode("utf-8"))
    )


def _preflight_correction(request, evidence, document, authority) -> None:
    # Only supplied descriptors/reviews, never captured provider bytes. Persisted
    # rights and historical receipt authorization remain repository checks.
    if isinstance(request, (CorrectionPrepareRequestV2, CorrectionAdmitRequestV2)):
        source = (
            request.evidence
            if isinstance(request, CorrectionPrepareRequestV2)
            else request.intent.evidence
        )
        raw = strict_json_bytes(
            evidence.read(
                source.adapter.evidence_reference, source.adapter.evidence_sha256
            )
        )
        if not isinstance(raw, dict) or raw.get("schema_version") != (
            "TRAINING_CORRECTION_JSON_ADAPTER_V2"
        ):
            raise ValueError("correction requires an explicitly versioned V2 adapter")
        TrainingCorrectionJsonAdapterV2.model_validate(raw)
    if isinstance(request, CorrectionAdmitRequestV2):
        authority(request.reviewer_authority)
        review = document(request.reviewer_evidence, TrainingReviewDocumentV1)
        if (review.attested_schema_version, review.attested_payload_hash) != (
            request.intent.schema_version,
            request.intent.intent_hash,
        ):
            raise ValueError("review must bind the exact time-free correction intent")


def _preflight_evidence(request: RequestV1, evidence: LocalTrainingEvidence) -> None:
    def document(ref: LocalReviewEvidenceV1, model: type[BaseModel]) -> BaseModel:
        return model.model_validate(
            strict_json_bytes(
                evidence.read(ref.evidence_reference, ref.evidence_sha256)
            )
        )

    def authority(ref: LocalReviewEvidenceV1) -> None:
        if (
            evidence.trusted_authorities.get(ref.evidence_reference)
            != ref.evidence_sha256
        ):
            raise ValueError("authority must match independently approved pins")

    for name, digest in evidence.trusted_authorities.items():
        document(
            LocalReviewEvidenceV1(evidence_reference=name, evidence_sha256=digest),
            ReviewerAuthorityV1,
        )
    if isinstance(request, RightsRecordRequestV1):
        rights = request.rights_payload
        evidence.read(rights.terms_reference, rights.terms_sha256)
        review = request.reviewer_attestation.content_payload
        authority(
            LocalReviewEvidenceV1(
                evidence_reference=review.reviewer_authority_reference,
                evidence_sha256=review.authority_sha256,
            )
        )
        document(review.evidence, TrainingReviewDocumentV1)
    elif isinstance(request, CaptureRequestV1):
        # Provider bytes may only be read after the repository's rights-before-read gate.
        pass
    elif isinstance(request, AdmitRequestV1):
        for item in request.submissions:
            authority(item.reviewer_authority)
            document(item.reviewer_evidence, TrainingReviewDocumentV1)
            document(item.source_evidence.adapter, TrainingJsonAdapterV1)
            if item.candidate.normalized_result.supersedes_match_result_id is not None:
                raise ControlledTrainingCorrectionRequired()
    elif isinstance(
        request,
        (CorrectionPrepareRequestV2, CorrectionAdmitRequestV2),
    ):
        _preflight_correction(request, evidence, document, authority)
    elif isinstance(request, (PilotPlanRequestV1, PilotPlanRequestV2)):
        definition = request.definition
        scope, cohort = definition.scope, definition.cohort
        authority(
            LocalReviewEvidenceV1(
                evidence_reference=scope.authority_reference,
                evidence_sha256=scope.authority_sha256,
            )
        )
        document(scope.raw_scope, QuantIntegrityScopeDocumentV1)
        document(cohort.raw_schedule, QuantIntegrityScheduleDocumentV1)
        pin = definition.build_recipe
        recipe = document(pin.evidence, QuantIntegrityBuildRecipeV1)
        if (
            pin.recipe_hash != pin.evidence.evidence_sha256
            or hashlib.sha256(canonical_json(recipe).encode("utf-8")).hexdigest()
            != pin.recipe_hash
            or (
                recipe.recipe_id,
                recipe.implementation_code_revision,
                recipe.config_hash,
            )
            != (
                pin.recipe_id,
                definition.implementation_code_revision,
                definition.config_hash,
            )
        ):
            raise ValueError(
                "recipe requires exact canonical bytes and matching plan pins"
            )
        for review in (
            scope.evidence,
            cohort.evidence,
            *(e.evidence for e in cohort.completeness_exceptions),
        ):
            document(review, QuantIntegrityReviewDocumentV1)
    elif isinstance(request, ApprovalPrepareRequestV2):
        authority(request.reviewer_authority)
    elif isinstance(request, ApprovalRecordRequestV2):
        review = request.reviewer_attestation.content_payload
        authority(
            LocalReviewEvidenceV1(
                evidence_reference=review.reviewer_authority_reference,
                evidence_sha256=review.authority_sha256,
            )
        )
        document(review.evidence, TrainingReviewDocumentV1)
    elif isinstance(request, RevokePrepareRequestV1):
        authority(request.reviewer_authority)
    elif isinstance(request, RevokeRequestV1):
        authority(request.revocation_request.reviewer_authority)
        document(request.review, TrainingReviewDocumentV1)
    elif isinstance(request, BundleExportRequestV1):
        _output_path(request.bundle_directory)
    elif isinstance(request, ReviewImportRequestV1):
        from football_system.infrastructure.files.production_audit_bundle import (
            read_production_audit_bundle,
        )

        bundle = read_production_audit_bundle(request.bundle_directory)
        validate_review_files(
            bundle.packet_bytes,
            _read_local_bytes(request.review_file, MAX_CONTRACT_FILE_BYTES),
        )
    elif isinstance(request, (FusionCreateRequestV1, PortfolioReviseRequestV1)):
        _read_settings(request.config_file)


def production_inference_context(sessions, *, evidence, operator_id, clock=None):
    """Wire the same local verification boundary for live and downstream commands."""
    from football_system.infrastructure.database.production_audit_repository import (
        SqlAlchemyProductionAuditRepository,
    )
    from football_system.infrastructure.database.production_inference_repository import (
        SqlAlchemyProductionInferenceRepository,
    )

    if clock is None:
        clock = utc_now
    admissions = SqlAlchemyTrainingAdmissionRepository(
        sessions, evidence=evidence, operator_id=operator_id, clock=clock
    )
    pilots = SqlAlchemyQuantIntegrityRepository(
        sessions, admission_repository=admissions, operator_id=operator_id, clock=clock
    )
    production = SqlAlchemyProductionQuantRepository(
        sessions,
        admission_repository=admissions,
        pilot_repository=pilots,
        operator_id=operator_id,
        clock=clock,
    )
    inference = SqlAlchemyProductionInferenceRepository(
        sessions, production_repository=production, clock=clock
    )
    auditor = SqlAlchemyProductionAuditRepository(
        sessions,
        production_repository=production,
        inference_repository=inference,
        clock=clock,
    )
    return admissions, pilots, production, inference, auditor


def _output_path(path: Path) -> Path:
    parent = path.parent.resolve(strict=True)
    destination = parent / path.name
    if (
        not parent.is_dir()
        or destination.exists()
        or destination.is_symlink()
        or destination.is_junction()
    ):
        raise FileExistsError(
            "output requires an existing directory and an unused name"
        )
    return destination


def write_local_json(path: Path, content: str) -> tuple[int, str]:
    """Publish bounded canonical JSON bytes atomically, never replacing any file."""
    encoded = (content + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("output exceeds the byte limit")
    path = _output_path(path)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".quant-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _execute(
    command, request, admissions, pilots, production, *, sessions=None, auditor=None
):
    # Keep validated nested models, rather than converting repository arguments to dicts.
    values = {
        name: getattr(request, name)
        for name in type(request).model_fields
        if name != "schema_version"
    }
    if command == "rights-record":
        return admissions.record(**values)
    if command == "capture":
        return admissions.capture_local_json(**values)
    if command == "admit":
        return admissions.admit(**values)
    if command.startswith("correction-"):
        corrections = SqlAlchemyTrainingCorrectionRepository(admissions)
        if command == "correction-reference":
            return corrections.capture_reference(**values)
        if command == "correction-prepare":
            return corrections.prepare(**values)
        if command == "correction-admit":
            return corrections.admit(**values)
        context = corrections.load_context(
            base_admissions=request.base_admissions,
            correction_ids=request.correction_ids,
            actual_at=admissions._now(),
        )
        if set(request.source_cutoffs) != {
            v.snapshot.stream.source_id for v in context.versions
        }:
            raise ValueError("context requires the exact set of per-source cutoffs")
        selected = select_versioned_training_heads(
            context, source_cutoffs=request.source_cutoffs, strict_cutoff=False
        )
        pin = TrainingHistoryContextPinV2.of(context)
        return {
            "context": context,
            "correction_context": pin,
            "context_registered_at_utc": context.actual_at_utc,
            "context_root": pin.context_root,
            "base_root": pin.base_root,
            "source_cutoffs": request.source_cutoffs,
            "selected_versions": selected,
            "selected_heads": tuple(VersionedFactRefV2.of(v) for v in selected),
        }
    if command in {"pilot-plan", "pilot-run", "pilot-attest"}:
        service = QuantIntegrityPilotService(pilots, clock=utc_now)
        if command == "pilot-plan":
            return service.seal_plan(request.definition)
        if command == "pilot-run":
            return service.run(request.plan_ref)
        return service.seal_terminal_attestation(request.plan_ref)
    if command == "manifest":
        return production.create_manifest(**values)
    if command == "approval-prepare":
        return production.prepare_approval(**values)
    if command == "approval-record":
        return production.record_approval(**values)
    if command == "release-build":
        return production.build_release(**values)
    if command == "target-plan":
        return production.seal_target_plan(**values)
    if command == "revoke-prepare":
        return production.prepare_revocation_request(**values)
    if command == "revoke":
        return production.record_revocation(**values)
    if command in {
        "bundle-export",
        "review-import",
        "fusion-create",
        "portfolio-revise",
    }:
        from football_system.infrastructure.database.review_repositories import (
            SqlAlchemyReviewArtifactRepository,
        )
        from football_system.infrastructure.database.post_review_repositories import (
            SqlAlchemyPostReviewRepository,
        )
        from football_system.infrastructure.files.production_audit_bundle import (
            export_production_audit_bundle,
            import_production_audit_bundle,
        )

        review_repository = SqlAlchemyReviewArtifactRepository(
            sessions, audit_repository=auditor
        )
        if command == "bundle-export":
            export_production_audit_bundle(
                request.bundle_directory,
                analysis_run_id=request.analysis_run_id,
                review_repository=review_repository,
                audit_repository=auditor,
            )
            return {
                "analysis_run_id": request.analysis_run_id,
                "bundle_published": True,
            }
        if command == "review-import":
            return import_production_audit_bundle(
                request.bundle_directory,
                _read_local_bytes(request.review_file, MAX_CONTRACT_FILE_BYTES),
                review_repository=review_repository,
                audit_repository=auditor,
            )
        post_repository = SqlAlchemyPostReviewRepository(
            sessions, audit_repository=auditor
        )
        settings = _read_settings(request.config_file)
        if command == "fusion-create":
            return CreateFusionRunService(post_repository, settings).create(
                request.review_artifact_id
            )
        return CreatePortfolioRevisionService(post_repository, settings).create(
            request.fusion_run_id
        )
    target = request.target
    if target.kind == "capture":
        return admissions.load_capture(target.artifact_id)[0]  # Never export raw bytes.
    if isinstance(target, InspectStoredV1):
        if target.kind in {"correction", "correction-version"}:
            corrections = SqlAlchemyTrainingCorrectionRepository(admissions)
            return (
                corrections.load_admission
                if target.kind == "correction"
                else corrections.load_version
            )(target.artifact_id)
        return {
            "rights": admissions.load_rights,
            "admission": admissions.load,
            "manifest": production.load_manifest,
            "approval": production.load_approval,
            "release": production.load_release,
            "target-plan": production.load_target_plan,
        }[target.kind](target.artifact_id)
    if isinstance(target, InspectPilotV1):
        return (
            pilots.load_plan if target.kind == "pilot-plan" else pilots.load_report
        )(target.reference)
    return pilots.list_attempts(target.series_id)


def dispatch_production_quant(arguments: Sequence[str]) -> int:
    parser = _SafeParser(
        prog="football-system production-quant",
        description=(
            "Local-only admitted history, retrospective integrity pilots and production "
            "release contracts. No provider API, authority creation or raw export. "
            "approval-prepare and correction-prepare are read-only previews, not reviews "
            "or authorization. Obtain an external authorized review of the exact returned "
            "review_subject before approval-record or correction-admit. V1 approval writing is unsupported."
        ),
        epilog=(
            "Every operation requires --request, --database-url, --evidence-root, "
            "--authority-pins and --operator. inspect, *-prepare, correction-reference "
            "and correction-context open SQLite mode=ro and never migrate. "
            "Other writes may migrate after preflight. Paths are relative to the current "
            "directory; evidence references are contained POSIX paths. Output is local "
            "JSON, not a current inference authorization. Request/evidence: 64 MiB each; "
            "pins/config/review/each bundle member: 1 MiB; output: 256 MiB. "
            "Capture provider bytes are read only after persisted rights checks. "
            "correction-reference audits an existing receipt, even after rights expiry; "
            "new capture/prepare/admit require current rights. correction-context uses "
            "the repository's actual clock, not a caller time; source_cutoffs select "
            "whole historical versions inclusively, including nontrainable withdrawals. "
            "Use its correction_context pin and context_registered_at_utc in an explicit "
            "V2 pilot definition or PRODUCTION_QUANT_MANIFEST_REQUEST_V2. Untagged V1 "
            "requests are preserved, never converted. Historical source cutoffs and "
            "the context knowledge time are distinct. "
            "Output parents must exist; no overwrite."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("command", nargs="?", choices=tuple(COMMAND_MODELS))
    parser.add_argument("--request", type=Path, help="Strict UTF-8 JSON request file.")
    parser.add_argument(
        "--database-url", help="Explicit local file-backed SQLite URL; no URI options."
    )
    parser.add_argument(
        "--evidence-root", type=Path, help="Existing private local evidence directory."
    )
    parser.add_argument(
        "--authority-pins",
        type=Path,
        help="Approved AuthorityPinsV1 JSON; never generated.",
    )
    parser.add_argument(
        "--operator",
        help="Exact recording operator ID; reviewer authority is checked separately.",
    )
    parser.add_argument(
        "--output", type=Path, help="Optional new local JSON file; otherwise stdout."
    )
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print exact request schema, or the type catalog, without I/O.",
    )
    parser.add_argument(
        "--type",
        choices=tuple(sorted(SCHEMA_TYPES)),
        help="Schema type for --print-schema (including external evidence documents).",
    )
    stage = "input"
    command = None
    engine = None
    try:
        if not arguments or list(arguments) in (["--help"], ["-h"]):
            parser.print_help()
            return 0
        args = parser.parse_args(arguments)
        command = args.command
        if args.print_schema:
            model = (
                SCHEMA_TYPES.get(args.type)
                if args.type
                else COMMAND_MODELS.get(command)
            )
            print(
                canonical_json(
                    model.model_json_schema()
                    if model
                    else {
                        "commands": {
                            key: value.__name__ for key, value in COMMAND_MODELS.items()
                        },
                        "types": sorted(SCHEMA_TYPES),
                        "limits_bytes": {
                            "request": MAX_REQUEST_BYTES,
                            "evidence": MAX_EVIDENCE_BYTES,
                            "authority_pins": MAX_PINS_BYTES,
                            "output": MAX_OUTPUT_BYTES,
                            "config": MAX_CONFIG_BYTES,
                            "review_and_bundle_member": MAX_CONTRACT_FILE_BYTES,
                        },
                        "blocked_commands": [],
                        "unsupported_approval_payload_versions": [
                            "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"
                        ],
                    }
                )
            )
            return 0
        if (
            args.type
            or command is None
            or any(
                value is None
                for value in (
                    args.request,
                    args.database_url,
                    args.evidence_root,
                    args.authority_pins,
                    args.operator,
                )
            )
        ):
            raise ValueError("explicit operation arguments are required")
        operator = TypeAdapter(OperationId).validate_python(args.operator)
        document = _read_json(args.request, MAX_REQUEST_BYTES)
        if (
            command == "approval-record"
            and isinstance(document, dict)
            and (
                "manifest_id" in document
                or isinstance(document.get("approval_payload"), dict)
                and document["approval_payload"].get("payload_version")
                == "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"
            )
        ):
            raise ApprovalRecordingContractConflict()
        request = COMMAND_MODELS[command].model_validate(document)
        if isinstance(request, RootModel):
            request = request.root
        request = revalidate(request)
        pins = AuthorityPinsV1.model_validate(
            _read_json(args.authority_pins, MAX_PINS_BYTES)
        )
        evidence = LocalTrainingEvidence(
            args.evidence_root,
            trusted_authorities=pins.trusted_authorities,
            max_bytes=MAX_EVIDENCE_BYTES,
        )
        _preflight_evidence(request, evidence)
        output = None if args.output is None else _output_path(args.output)
        url = require_sqlite_database_url(args.database_url)
        if (
            url.drivername not in {"sqlite", "sqlite+pysqlite"}
            or url.database in {None, "", ":memory:"}
            or any((url.username, url.password, url.host, url.port, url.query))
            or url.database.startswith("file:")
        ):
            raise ValueError("a plain local file-backed SQLite URL is required")
        database = Path(url.database).resolve()
        if database.drive.startswith("\\\\") or not database.parent.is_dir():
            raise ValueError("database requires an existing local parent directory")
        if output == database:
            raise ValueError("output must not be the database file")
        if isinstance(
            request, BundleExportRequestV1
        ) and request.bundle_directory.resolve() in {database, output}:
            raise ValueError("bundle directory must not alias database or output")
        stage = "database"
        if command in READ_COMMANDS:
            if not database.is_file():
                raise ValueError("read commands require an existing database")
            engine = create_engine(
                URL.create(
                    "sqlite+pysqlite",
                    database=database.as_uri(),
                    query={"mode": "ro", "uri": "true"},
                )
            )
        else:
            from football_system.interfaces.cli import _resource_root

            database_url = URL.create(
                "sqlite+pysqlite", database=str(database)
            ).render_as_string()
            upgrade_database(database_url, _resource_root() / "alembic.ini")
            engine = create_database_engine(database_url)
        sessions = create_session_factory(engine)
        admissions, pilots, production, _, auditor = production_inference_context(
            sessions, evidence=evidence, operator_id=operator
        )
        stage = "operation"
        result = _execute(
            command,
            request,
            admissions,
            pilots,
            production,
            sessions=sessions,
            auditor=auditor,
        )
        stage = "output"
        status = (
            "PREPARED"
            if command.endswith("-prepare")
            else "VERIFIED"
            if command in READ_COMMANDS
            else "RECORDED"
        )
        response = {
            "schema_version": "PRODUCTION_QUANT_CLI_RESULT_V1",
            "command": command,
            "status": status,
            "result": result,
        }
        if isinstance(result, IntegrityArtifact):
            response["reference"] = IntegrityArtifactRefV1.of(result)
        elif isinstance(result, TrainingFactAdmissionV1):
            response["reference"] = CorrectionRefV2(
                schema_version=result.schema_version,
                artifact_id=result.training_fact_admission_id,
                content_hash=result.admission_hash,
            )
        elif isinstance(result, TrainingFactVersionV2):
            response["reference"] = result.reference
            response["fact_reference"] = VersionedFactRefV2.of(result)
        elif callable(getattr(result, "reference", None)):
            response["reference"] = result.reference()
        if isinstance(result, ProductionRevocationRequestV1):
            response["review_subject"] = {
                "schema_version": result.schema_version,
                "payload_hash": result.request_hash,
            }
        if isinstance(result, TrainingHistoryApprovalPayloadV2):
            response["review_subject"] = {
                "schema_version": result.payload_version,
                "payload_hash": result.approval_payload_hash,
            }
        if isinstance(result, TrainingCorrectionIntentV2):
            response["review_subject"] = {
                "schema_version": result.schema_version,
                "payload_hash": result.intent_hash,
            }
        if isinstance(result, QuantIntegrityPlanV1):
            response["integrity_pilot_scope_id"] = (
                result.content_payload.definition.integrity_pilot_scope_id
            )
        content = canonical_json(response)
        if len((content + "\n").encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("output exceeds the byte limit")
        if output is not None:
            size, digest = write_local_json(output, content)
            receipt = {key: value for key, value in response.items() if key != "result"}
            receipt.update(output_bytes=size, output_sha256=digest)
            print(canonical_json(receipt))
        else:
            print(content)
        return 0
    except ApprovalRecordingContractConflict:
        code, exit_code, status = "APPROVAL_RECORDING_CONTRACT_CONFLICT", 3, "BLOCKED"
        message = (
            "V1 approval writing is unsupported and is not automatically converted. "
            "Use approval-prepare and obtain a new local review of the exact "
            "TRAINING_HISTORY_APPROVAL_PAYLOAD_V2 subject. Do not reuse or rebind V1 "
            "attestations or invent recording timestamps. No database was opened."
        )
    except ControlledTrainingCorrectionRequired:
        code, exit_code, status = "CONTROLLED_CORRECTION_REQUIRED", 3, "BLOCKED"
        message = (
            "Use the controlled correction-reference, correction-prepare and "
            "correction-admit path with an externally authorized review of the exact "
            "V2 intent. An unregistered successor or unsupported identity reassignment "
            "still blocks affected work; do not edit old evidence or bypass immutable records."
        )
    except Exception:
        # Never print exception text, Pydantic input/locations, SQL parameters, paths or URLs.
        code, exit_code, status, message = {
            "input": (
                "INVALID_LOCAL_INPUT",
                2,
                "REJECTED",
                "Check explicit arguments, --print-schema/--type, file bounds, strict JSON, seals, contained evidence references and approved authority pins. Output must have an existing parent and an unused name. No database was opened.",
            ),
            "database": (
                "DATABASE_NOT_READY",
                1,
                "REJECTED",
                "Check the local SQLite file and schema. Read commands never create or migrate it; arrange an explicit authorized migration for an older schema. A write migration may have run.",
            ),
            "operation": (
                "OPERATION_REJECTED",
                1,
                "REJECTED",
                "Repository verification failed. Check exact persisted IDs/hashes, rights, authorized reviews, source timestamps, identity mappings and prerequisites. For pilot-run, inspect all pilot-attempts; a failed attempt or pending reservation may remain. Do not blindly retry or erase attempts.",
            ),
            "output": (
                "OUTPUT_NOT_WRITTEN",
                1,
                "REJECTED",
                "The operation may already be persisted, but output was not confirmed. An approval-record retry must use the exact same request key, subject, attestation and operator; it will not duplicate the approval. Inspect persisted artifacts; do not rerun a pilot blindly. Check the output bound, free space and a new unused local filename.",
            ),
        }[stage]
    finally:
        if engine is not None:
            engine.dispose()
    persistence = (
        "NO_DATABASE_OPENED"
        if stage == "input"
        else "NO_NEW_ROWS"
        if command in READ_COMMANDS
        else "MIGRATION_MAY_HAVE_RUN"
        if stage == "database"
        else "ATOMIC_NO_NEW_ROWS"
        if command == "correction-admit" and stage == "operation"
        else "MAY_HAVE_PERSISTED"
    )
    if stage == "operation" and command == "correction-admit":
        message = (
            "Correction admission failed atomically; no new correction or normalized "
            "result rows were committed by this attempt. Previously committed rows may "
            "exist. Check exact predecessor, current rights, original review and raw "
            "evidence. A retry must use the exact same request key, intent, review, "
            "authority and operator; changed requests cannot reuse the key."
        )
    elif stage == "database" and command in READ_COMMANDS:
        message = (
            "Read-only commands require an existing local SQLite database. No database "
            "was created or migrated. Check the file and arrange an explicit authorized "
            "migration if its schema is older."
        )
    elif stage == "operation" and command in READ_COMMANDS:
        message = (
            "Read-only verification failed; no new database rows were written. Check "
            "the schema, exact persisted pins, complete predecessor chain, source "
            "cutoffs, retained raw evidence, rights and externally authorized reviews."
        )
    elif stage == "output" and command in READ_COMMANDS:
        message = (
            "Read-only verification or preparation completed without new database rows, "
            "but output was not confirmed. No review or authorization was created. "
            "Check the output bound, free space and a new unused local filename."
        )
    elif stage == "output" and command == "correction-admit":
        message = (
            "The correction may already be persisted, but output was not confirmed. "
            "Inspect the correction or retry with the exact same request key, intent, "
            "review, authority and operator; an exact retry will not duplicate rows. "
            "Use a new unused output filename; do not create a replacement review."
        )
    print(
        canonical_json(
            {
                "schema_version": "PRODUCTION_QUANT_CLI_ERROR_V1",
                "command": command,
                "status": status,
                "code": code,
                "message": message,
                "persistence": persistence,
            }
        ),
        file=sys.stderr,
    )
    return exit_code
