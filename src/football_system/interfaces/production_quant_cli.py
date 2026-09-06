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

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
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
    QuantIntegrityPlanV1,
    QuantIntegrityReportV1,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    LocalReviewerAttestationV1,
    Reference,
    RuleText,
    Sha256Digest,
    SourceRightsPayloadV1,
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


class ApprovalRecordRequestV1(RequestV1):
    """Describes the blocked repository API, not a satisfiable approval envelope."""

    request_key: OperationId
    manifest_id: OperationId
    grants: tuple[ProductionGrantV1, ...] = Field(min_length=1)
    review: LocalReviewEvidenceV1
    reviewer_authority: LocalReviewEvidenceV1


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
    "pilot-plan": PilotPlanRequestV1,
    "pilot-run": PilotReferenceRequestV1,
    "pilot-attest": PilotReferenceRequestV1,
    "manifest": ManifestRequestV1,
    "approval-record": ApprovalRecordRequestV1,
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
    )
}
READ_COMMANDS = frozenset({"inspect", "revoke-prepare"})


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
    elif isinstance(request, PilotPlanRequestV1):
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
    elif isinstance(request, ApprovalRecordRequestV1):
        authority(request.reviewer_authority)
        document(request.review, TrainingReviewDocumentV1)
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
    values = {name: getattr(request, name) for name in type(request).model_fields}
    if command == "rights-record":
        return admissions.record(**values)
    if command == "capture":
        return admissions.capture_local_json(**values)
    if command == "admit":
        return admissions.admit(**values)
    if command in {"pilot-plan", "pilot-run", "pilot-attest"}:
        service = QuantIntegrityPilotService(pilots, clock=utc_now)
        if command == "pilot-plan":
            return service.seal_plan(request.definition)
        if command == "pilot-run":
            return service.run(request.plan_ref)
        return service.seal_terminal_attestation(request.plan_ref)
    if command == "manifest":
        return production.create_manifest(**values)
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
            "release contracts. No provider API, authority/approval creation or raw export. "
            "approval-record is BLOCKED by the existing approval timestamp contract."
        ),
        epilog=(
            "Every operation requires --request, --database-url, --evidence-root, "
            "--authority-pins and --operator. inspect/revoke-prepare never migrate. "
            "Other writes may migrate after preflight. Paths are relative to the current "
            "directory; evidence references are contained POSIX paths. Output is local "
            "JSON, not a current inference authorization. Request/evidence: 64 MiB each; "
            "pins/config/review/each bundle member: 1 MiB; output: 256 MiB. "
            "Capture provider bytes are read only after persisted rights checks. "
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
                        "blocked_commands": ["approval-record"],
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
        request = COMMAND_MODELS[command].model_validate(
            _read_json(args.request, MAX_REQUEST_BYTES)
        )
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
        if command == "approval-record":
            # The core method unconditionally raises this conflict. Stop BEFORE migration,
            # without synthesizing a future persisted_at_utc or a reviewer attestation.
            raise ApprovalRecordingContractConflict()
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
            if command == "revoke-prepare"
            else "VERIFIED"
            if command == "inspect"
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
        elif callable(getattr(result, "reference", None)):
            response["reference"] = result.reference()
        if isinstance(result, ProductionRevocationRequestV1):
            response["review_subject"] = {
                "schema_version": result.schema_version,
                "payload_hash": result.request_hash,
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
            "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1 includes future persisted_at_utc "
            "and approved_at_utc in the reviewer-attested hash. Approval writing is "
            "stopped. Obtain explicit authorization for a new review/recording envelope "
            "and implement/review that core contract first. Do not invent timestamps, "
            "synthesize attestations or insert approval rows. No database was opened."
        )
    except ControlledTrainingCorrectionRequired:
        code, exit_code, status = "CONTROLLED_CORRECTION_REQUIRED", 3, "BLOCKED"
        message = (
            "The controlled predecessor/source-correction path is not implemented. "
            "Stop affected production work and obtain an authorized core correction "
            "implementation; do not edit old evidence or bypass immutable records."
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
                "The operation may already be persisted, but output was not confirmed. Inspect persisted artifacts before retrying; do not rerun a pilot blindly. Check the output bound, free space and a new unused local filename.",
            ),
        }[stage]
    finally:
        if engine is not None:
            engine.dispose()
    print(
        canonical_json(
            {
                "schema_version": "PRODUCTION_QUANT_CLI_ERROR_V1",
                "command": command,
                "status": status,
                "code": code,
                "message": message,
            }
        ),
        file=sys.stderr,
    )
    return exit_code
