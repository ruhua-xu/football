"""Durable offline pilot transactions and the schema-aware release evidence bridge.

Pre-plan reads select admission headers and opaque fact refs, never normalized
result objects or the admission's full JSON. Mixed metadata/result captures and
their payload/path aliases are rejected BEFORE opening provider documents; this
legacy capture contract requires separate metadata documents for planning.
Fixture/membership bytes, identity
rows, reviewed scope/schedule/exception documents and recipe bytes are checked
before sealing. Result bytes and the complete admission graph are verified only
after a committed reservation. SQLite/trusted local evidence are internal audit
boundaries, not protection from a database owner replacing trusted configuration.

Local descriptors below point to captured source JSON; they cannot create source
times or assert completeness without the captured schedule. Pilot reviewer files
use QuantIntegrityReviewDocumentV1 and the existing pinned ReviewerAuthorityV1.
Synthetic adapters must explicitly declare synthetic_contract_only=True and can
NEVER produce TechnicalEvidenceRefsV1. No network, acquisition or real pilot is
performed by constructing this repository.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta
from threading import Lock
from typing import Literal

from pydantic import Field
from sqlalchemy import func, select, text, true
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.quant_integrity import QuantIntegrityPilotService
from football_system.application.run_analysis import _code_revision
from football_system.domain.archive import canonical_json
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
    normalize_utc,
    utc_now,
)
from football_system.domain.production_release import (
    ReleasedStateCoreContentV1,
    ReleaseArtifactRefV1,
    TechnicalEvidenceRefsV1,
    TrainingHistoryGraphV1,
    TrainingHistoryGraphV2,
    replay_exact_facts,
)
from football_system.domain.quant_integrity import (
    AdmittedFactRefV1,
    IntegrityArtifact,
    IntegrityArtifactRefV1,
    IntegrityEvidenceUse,
    QuantIntegrityAttemptContentV1,
    QuantIntegrityAttemptReservationContentV1,
    QuantIntegrityAttemptReservationV1,
    QuantIntegrityAttemptV1,
    QuantIntegrityAttestationV1,
    QuantIntegrityFailureV1,
    QuantIntegrityOutputV1,
    QuantIntegrityPlanDefinitionV1,
    QuantIntegrityPlanDefinitionV2,
    QuantIntegrityTargetV2,
    QuantIntegrityPlanV1,
    QuantIntegrityReportV1,
    QuantIntegritySummaryContentV1,
    QuantIntegritySummaryV1,
    ReviewedProviderSeasonV1,
    TrainingAdmissionPinV1,
    integrity_attempt_root,
    revalidate_integrity_model,
)
from football_system.domain.training_admission import (
    TRAINING_FACT_REQUIRED_USES,
    LocalReviewEvidenceV1,
    MatchSeasonMembershipV1,
    Reference,
    Sha256Digest,
    TrainingCanonicalMatchIdentityV1,
    TrainingFactAdmissionContentV1,
    TrainingFactAdmissionV1,
    TrainingFixtureSourceV1,
    TrainingProviderMatchMappingV1,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    MatchResultAdmissionRecord,
    MatchSeasonMembershipRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    QuantIntegrityAttemptRecord,
    QuantIntegrityAttestationRecord,
    QuantIntegrityOutputRecord,
    QuantIntegrityPlanRecord,
    QuantIntegrityReportRecord,
    QuantIntegrityReservationRecord,
    QuantIntegritySeriesRecord,
    QuantIntegritySummaryRecord,
    TrainingCaptureReceiptRecord,
    TrainingFactAdmissionRecord,
    TrainingFactBindingRecord,
    TrainingFactFixtureSourceRecord,
)
from football_system.infrastructure.database.training_admission_repository import (
    SqlAlchemyTrainingAdmissionRepository,
    TrainingCaptureReceiptV1,
    _verify_row as verify_training_row,
)
from football_system.infrastructure.files.training_evidence import (
    LocalTrainingEvidence,
    ReviewerAuthorityV1,
    TrainingJsonAdapterV1,
    TrainingSourceEvidenceV1,
    json_pointer,
    provider_record_sha256,
    strict_json_bytes,
)
from football_system.domain.training_correction import (
    CorrectionRefV2,
    TrainingCorrectionContextV2,
)
from football_system.domain.versioned_training_history import (
    TrainingHistoryContextPinV2,
    VersionedFactRefV2,
    validate_versioned_context,
)


def correction_context_in_session(
    repository, session, pin, at, *, require_complete=True
):
    """Same-transaction version of the controlled repository's pinned reader.

    Use its byte-verifying primitives, never normalized 'latest' rows or supplied
    predecessor objects. Historical callers pass their captured actual boundary.
    """
    from football_system.infrastructure.database.training_correction_repository import (
        ADMISSIONS,
        SqlAlchemyTrainingCorrectionRepository,
    )

    repo = SqlAlchemyTrainingCorrectionRepository(repository)
    versions = []
    rights = {}
    for ref in pin.base_admissions:
        admission = repository._load(session, ref.artifact_id)
        if (ref.schema_version, ref.content_hash) != (
            admission.schema_version,
            admission.admission_hash,
        ):
            raise ValueError("correction context base admission pin mismatch")
        original_rights = admission.source_rights_admission
        rights[ref.artifact_id] = CorrectionRefV2(
            schema_version=original_rights.schema_version,
            artifact_id=original_rights.source_rights_admission_id,
            content_hash=original_rights.admission_hash,
        )
        versions.extend(
            repo._base_version(session, ref.artifact_id, f.training_fact_binding_id)
            for f in admission.facts
        )
    corrections = [
        repo._load_version(session, ref.artifact_id) for ref in pin.corrections
    ]
    if {v.reference for v in corrections} != set(pin.corrections):
        raise ValueError("correction context exact reference hash mismatch")
    for version in corrections:
        value = session.scalar(
            select(
                func.json_extract(
                    ADMISSIONS.c.artifact_json,
                    "$.content_payload.intent.source_rights_admission",
                )
            ).where(ADMISSIONS.c.correction_id == version.version_id)
        )
        if CorrectionRefV2.model_validate_json(value) != rights.get(
            version.base_admission.artifact_id
        ):
            raise ValueError(
                "correction history must preserve the exact base source rights scope"
            )
    corrections.sort(key=lambda v: (v.snapshot.stream.stream_id, v.revision_sequence))
    versions.extend(corrections)
    context = validate_versioned_context(
        TrainingCorrectionContextV2(
            actual_at_utc=at,
            versions=tuple(versions),
            corrections=tuple(e for v in corrections for e in repo._events(session, v)),
        )
    )
    if require_complete:
        assert_complete_correction_pins(session, pin, at)
    return context


def assert_complete_correction_pins(session, pin, at):
    if not session.scalar(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_correction_admissions'"
        )
    ):
        if pin.corrections:
            raise ValueError("controlled correction schema is not installed")
        return
    from football_system.infrastructure.database.training_correction_repository import (
        ADMISSIONS,
        STREAMS,
    )

    binding, mapping = TrainingFactBindingRecord, ProviderMatchMappingRecord
    scoped = (
        select(binding.sequence)
        .join(mapping, mapping.mapping_id == binding.provider_mapping_id)
        .where(
            binding.training_fact_admission_id.in_(
                tuple(p.artifact_id for p in pin.base_admissions)
            ),
            binding.internal_match_id == STREAMS.c.internal_match_id,
            mapping.provider_id == STREAMS.c.provider_id,
            func.json_extract(
                binding.artifact_json, "$.content_payload.fixture_source.source_id"
            )
            == STREAMS.c.source_id,
        )
        .exists()
    )
    rows = session.execute(
        select(ADMISSIONS.c.correction_id, ADMISSIONS.c.content_hash)
        .join(STREAMS, STREAMS.c.stream_id == ADMISSIONS.c.stream_id)
        .where(
            scoped,
            ADMISSIONS.c.registered_at_utc <= at,
            ADMISSIONS.c.source_available_at_utc <= at,
        )
    ).all()
    known = {(p.artifact_id, p.content_hash) for p in pin.corrections}
    if any(tuple(row) not in known for row in rows):
        raise ValueError(
            "unincorporated source-visible and locally registered correction; new context required"
        )


def base_context_pin(admissions):
    return TrainingHistoryContextPinV2(
        base_admissions=tuple(
            sorted(
                (
                    CorrectionRefV2(
                        schema_version=a.schema_version,
                        artifact_id=a.training_fact_admission_id,
                        content_hash=a.admission_hash,
                    )
                    for a in admissions
                ),
                key=lambda r: r.artifact_id,
            )
        ),
        corrections=(),
    )


class QuantIntegrityReviewDocumentV1(DomainModel):
    """Actor names are provenance, not a distinct-person or preparer policy.

    Review authorization comes from the exact pinned reviewer authority.
    """

    schema_version: Literal["QUANT_INTEGRITY_LOCAL_REVIEW_V1"] = (
        "QUANT_INTEGRITY_LOCAL_REVIEW_V1"
    )
    attested_schema_version: Identifier
    attested_payload_hash: Sha256Digest
    prepared_by: Identifier
    authorized_reviewer: Identifier
    reviewed_at_utc: UtcDateTime
    source_ids: tuple[Identifier, ...] = Field(min_length=1)
    evidence_use: IntegrityEvidenceUse
    accepted_for_internal_integrity: Literal[True]


class QuantIntegrityScopeRecordV1(DomainModel):
    provider: ReviewedProviderSeasonV1
    capture_receipt_id: Identifier
    record_pointer: str
    competition_id_pointer: Reference
    season_id_pointer: Reference
    competition_identity_pointer: Reference
    expected_competition_identity: Identifier
    country_pointer: Reference
    competition_type_pointer: Reference
    season_start_pointer: Reference
    season_end_pointer: Reference


class QuantIntegrityScopeDocumentV1(DomainModel):
    schema_version: Literal["QUANT_INTEGRITY_SCOPE_SOURCE_V1"] = (
        "QUANT_INTEGRITY_SCOPE_SOURCE_V1"
    )
    records: tuple[QuantIntegrityScopeRecordV1, ...] = Field(min_length=2)


class QuantIntegrityScheduleDocumentV1(DomainModel):
    schema_version: Literal["QUANT_INTEGRITY_SCHEDULE_SOURCE_V1"] = (
        "QUANT_INTEGRITY_SCHEDULE_SOURCE_V1"
    )
    provider: ReviewedProviderSeasonV1
    capture_receipt_id: Identifier
    records_pointer: str
    expected_count_pointer: Reference
    completed_at_pointer: Reference
    fixture_key_pointer: Reference
    competition_id_pointer: Reference
    season_id_pointer: Reference
    status_pointer: Reference
    # Explicit reviewed raw-to-canonical mapping, not a heuristic over labels.
    status_mapping: dict[
        str,
        Literal[
            "REGULAR_TIME_FINAL",
            "CANCELLED",
            "POSTPONED",
            "MISSING",
            "PROVIDER_SCOPE_DIFFERENCE",
        ],
    ]


class QuantIntegrityBuildRecipeV1(DomainModel):
    schema_version: Literal["QUANT_INTEGRITY_BUILD_RECIPE_V1"] = (
        "QUANT_INTEGRITY_BUILD_RECIPE_V1"
    )
    recipe_id: Identifier
    implementation_code_revision: Identifier
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1"] = "ELO_THREE_WAY_BASELINE_V1"
    model_version: Literal["1"] = "1"
    config_hash: Literal[
        "c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4"
    ]
    projection: Literal["ADMITTED_SOURCE_TIME_ELO_V1"] = "ADMITTED_SOURCE_TIME_ELO_V1"
    parameter_policy: Literal["NO_PARAMETER_TUNING"] = "NO_PARAMETER_TUNING"


class _PinnedReplayReader:
    """Only already reverified exact admissions, never a production-evidence adapter."""

    def __init__(self, admissions, context=None):
        self.admissions = {a.training_fact_admission_id: a for a in admissions}
        self.context = context

    def load_verified_correction_context(self, pin, *, at_utc):
        if self.context is None or TrainingHistoryContextPinV2.of(self.context) != pin:
            raise ValueError("replay correction context mismatch")
        return self.context

    def load_verified_training_admission(self, pin, *, at_utc):
        value = self.admissions[pin.training_fact_admission_id]
        if TrainingAdmissionPinV1.from_admission(value) != pin:
            raise ValueError("replay admission pin mismatch")
        return value


class SqlAlchemyQuantIntegrityRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        admission_repository: SqlAlchemyTrainingAdmissionRepository,
        clock: Callable[[], datetime] = utc_now,
        operator_id: str,
    ) -> None:
        if (
            not operator_id
            or operator_id != operator_id.strip()
            or len(operator_id) > 160
        ):
            raise ValueError("operator_id must be an exact nonempty identifier")
        self._sessions = session_factory
        self.admission_repository = admission_repository
        self.evidence: LocalTrainingEvidence = admission_repository.evidence
        self.operator_id = operator_id
        self._clock = clock
        self._last_clock = None
        self._clock_lock = Lock()
        self._contract_only = (
            type(admission_repository) is not SqlAlchemyTrainingAdmissionRepository
        )
        if (
            self._contract_only
            and getattr(admission_repository, "synthetic_contract_only", False)
            is not True
        ):
            raise TypeError(
                "injected admission adapters must be explicitly synthetic_contract_only"
            )

    def _now(self) -> datetime:
        with self._clock_lock:
            at = self._clock()
            if at.tzinfo is None or at.utcoffset() != timedelta(0):
                raise ValueError("repository clock must return aware UTC")
            if self._last_clock is not None and at < self._last_clock:
                raise ValueError("repository clock moved backwards")
            self._last_clock = at
            return at

    def _operation_time(self, supplied: datetime, *, recent: bool = False) -> datetime:
        now = self._now()
        if supplied.tzinfo is None or supplied.utcoffset() != timedelta(0):
            raise ValueError("supplied operation time must be aware UTC")
        if supplied > now or (recent and now - supplied > timedelta(minutes=1)):
            raise ValueError("supplied operation time disagrees with repository clock")
        return now

    def _review(
        self,
        definition,
        evidence: LocalReviewEvidenceV1,
        schema,
        payload,
        at,
        *,
        reviewer=None,
        reviewed_at=None,
    ):
        scope = definition.scope
        pinned = self.evidence.trusted_authorities.get(scope.authority_reference)
        if pinned is None or pinned != scope.authority_sha256:
            raise ValueError(
                "pilot reviewer authority is not pinned in trusted configuration"
            )
        authority = ReviewerAuthorityV1.model_validate(
            strict_json_bytes(self.evidence.read(scope.authority_reference, pinned))
        )
        review = QuantIntegrityReviewDocumentV1.model_validate(
            strict_json_bytes(
                self.evidence.read(
                    evidence.evidence_reference, evidence.evidence_sha256
                )
            )
        )
        source_ids = {s.source_id for s in scope.provider_seasons}
        if (
            review.authorized_reviewer != authority.authorized_reviewer
            or review.attested_schema_version != schema
            or schema not in authority.attested_schema_versions
            or review.attested_payload_hash != tagged_canonical_sha256(schema, payload)
            or set(review.source_ids) != source_ids
            or len(review.source_ids) != len(source_ids)
            or not source_ids <= set(authority.source_ids)
            or review.evidence_use != definition.provenance.evidence_use
            or (reviewer is not None and review.authorized_reviewer != reviewer)
            or (reviewed_at is not None and review.reviewed_at_utc != reviewed_at)
            or not authority.effective_at_utc
            <= review.reviewed_at_utc
            < authority.expires_at_utc
            or review.reviewed_at_utc > at
        ):
            raise ValueError(
                "pilot review does not bind exact content, authority, provenance and actual time"
            )

    def _recipe(self, definition):
        if definition.implementation_code_revision != _code_revision():
            raise ValueError(
                "pilot implementation_code_revision does not match current _code_revision()"
            )
        pin = definition.build_recipe
        payload = self.evidence.read(
            pin.evidence.evidence_reference, pin.evidence.evidence_sha256
        )
        if hashlib.sha256(payload).hexdigest() != pin.recipe_hash:
            raise ValueError("build recipe bytes do not match pinned hash")
        recipe = QuantIntegrityBuildRecipeV1.model_validate(strict_json_bytes(payload))
        if payload != canonical_json(recipe).encode("utf-8"):
            raise ValueError("build recipe must use the exact canonical recipe bytes")
        if (
            recipe.recipe_id != pin.recipe_id
            or recipe.implementation_code_revision
            != definition.implementation_code_revision
            or recipe.config_hash != definition.config_hash
        ):
            raise ValueError("build recipe identity/config/revision mismatch")

    def _capture(self, definition, receipt_id, at, *, session):
        if not self._contract_only:
            receipt = self._capture_metadata(session, receipt_id)
            captures = TrainingCaptureReceiptRecord
            result_ids = select(MatchResultAdmissionRecord.capture_receipt_id)
            result_link = captures.capture_receipt_id.in_(result_ids)
            if session.scalar(
                text(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_correction_admissions'"
                )
            ):
                from football_system.infrastructure.database.training_correction_repository import (
                    ADMISSIONS,
                )

                result_link |= captures.capture_receipt_id.in_(
                    select(
                        func.json_extract(
                            ADMISSIONS.c.artifact_json,
                            "$.content_payload.intent.evidence.result.capture_receipt_id",
                        )
                    )
                )
            # Inspect only sealed headers and typed result links, never the bytes
            # of a document merely labelled fixture/scope by a caller. Checking
            # hashes as well as paths also catches separately captured aliases.
            shared = session.scalar(
                select(captures.capture_receipt_id)
                .where(
                    result_link,
                    (captures.payload_sha256 == receipt.payload_sha256)
                    | (
                        func.lower(
                            func.json_extract(
                                captures.artifact_json, "$.evidence_reference"
                            )
                        )
                        == receipt.evidence_reference.lower()
                    ),
                )
                .limit(1)
            )
            if shared is not None:
                raise ValueError(
                    "pre-plan metadata capture overlaps a registered result capture; "
                    "separate metadata documents are required (receipt, payload and path aliases are not allowed)"
                )
        receipt, payload = (
            self.admission_repository.load_capture(receipt_id)
            if self._contract_only
            else self.admission_repository._capture(session, receipt_id)
        )
        receipt = revalidate_integrity_model(receipt)
        if receipt.capture_receipt_id != receipt_id or receipt.registered_at_utc > at:
            raise ValueError("source capture was not registered before operation")
        if receipt.source_rights_admission_id not in {
            p.source_rights_admission_id for p in definition.admissions
        }:
            raise ValueError("source capture rights are not pinned by the pilot")
        if (
            self.evidence.read(receipt.evidence_reference, receipt.payload_sha256)
            != payload
        ):
            raise ValueError("source capture differs from actual local bytes")
        return receipt, strict_json_bytes(payload)

    def _reviewed_sources(self, session, definition, at):
        scope, cohort = definition.scope, definition.cohort
        self._review(
            definition,
            scope.evidence,
            "QUANT_INTEGRITY_SCOPE_REVIEW_V1",
            scope.model_dump(exclude={"evidence"}),
            at,
            reviewer=scope.reviewed_by,
            reviewed_at=scope.reviewed_at_utc,
        )
        scope_doc = QuantIntegrityScopeDocumentV1.model_validate(
            strict_json_bytes(
                self.evidence.read(
                    scope.raw_scope.evidence_reference, scope.raw_scope.evidence_sha256
                )
            )
        )
        if tuple(r.provider for r in scope_doc.records) != scope.provider_seasons:
            raise ValueError(
                "reviewed raw scope must cover every exact ordered provider season"
            )
        previous_end = None
        for record in scope_doc.records:
            paths = tuple(
                value
                for key, value in record.model_dump().items()
                if key.endswith("_pointer") and key != "record_pointer"
            )
            if len(paths) != len(set(paths)):
                raise ValueError(
                    "scope semantic fields require distinct explicit source paths"
                )
            receipt, document = self._capture(
                definition, record.capture_receipt_id, at, session=session
            )
            raw = json_pointer(document, record.record_pointer)
            provider = record.provider
            if (
                (receipt.source_id, receipt.provider_code)
                != (provider.source_id, provider.provider_code)
                or json_pointer(raw, record.competition_id_pointer)
                != provider.provider_competition_id
                or json_pointer(raw, record.season_id_pointer)
                != provider.provider_season_id
                or json_pointer(raw, record.competition_identity_pointer)
                != record.expected_competition_identity
                or json_pointer(raw, record.country_pointer) != "DE"
                or json_pointer(raw, record.competition_type_pointer)
                != "DOMESTIC_LEAGUE"
            ):
                raise ValueError(
                    "raw provider scope does not verify reviewed Bundesliga mapping"
                )
            start = _source_time(json_pointer(raw, record.season_start_pointer))
            end = _source_time(json_pointer(raw, record.season_end_pointer))
            if start >= end or (previous_end is not None and start < previous_end):
                raise ValueError(
                    "raw provider seasons must form chronological nonoverlapping windows"
                )
            previous_end = end
            if (
                provider.canonical_season_id == cohort.season_id
                and cohort.season_completed_at_utc < end
            ):
                raise ValueError("cohort review predates the captured season end")
        self._review(
            definition,
            cohort.evidence,
            "QUANT_INTEGRITY_COHORT_REVIEW_V1",
            cohort.model_dump(exclude={"evidence"}),
            at,
            reviewer=cohort.reviewed_by,
            reviewed_at=cohort.reviewed_at_utc,
        )
        schedule = QuantIntegrityScheduleDocumentV1.model_validate(
            strict_json_bytes(
                self.evidence.read(
                    cohort.raw_schedule.evidence_reference,
                    cohort.raw_schedule.evidence_sha256,
                )
            )
        )
        expected_provider = scope.provider_seasons[-2]
        if schedule.provider != expected_provider:
            raise ValueError(
                "schedule provider/season is not the explicit pilot target"
            )
        receipt, document = self._capture(
            definition, schedule.capture_receipt_id, at, session=session
        )
        if (receipt.source_id, receipt.provider_code) != (
            expected_provider.source_id,
            expected_provider.provider_code,
        ):
            raise ValueError("schedule capture source/provider mismatch")
        records = json_pointer(document, schedule.records_pointer)
        count = json_pointer(document, schedule.expected_count_pointer)
        if (
            type(count) is not int
            or count != cohort.expected_match_count
            or not isinstance(records, list)
            or len(records) != count
            or _source_time(json_pointer(document, schedule.completed_at_pointer))
            != cohort.season_completed_at_utc
        ):
            raise ValueError(
                "captured full schedule does not prove expected count/completed season"
            )
        exceptions = {e.match_id: e for e in cohort.completeness_exceptions}
        found = []
        for record in records:
            fixture_key = json_pointer(record, schedule.fixture_key_pointer)
            if (
                json_pointer(record, schedule.competition_id_pointer)
                != expected_provider.provider_competition_id
                or json_pointer(record, schedule.season_id_pointer)
                != expected_provider.provider_season_id
            ):
                raise ValueError("captured schedule contains another provider season")
            if self._contract_only:
                match_id = self.admission_repository.synthetic_match_id(fixture_key)
            else:
                ids = tuple(
                    session.scalars(
                        select(ProviderMatchMappingRecord.internal_match_id)
                        .join(
                            ProviderRecord,
                            ProviderRecord.provider_id
                            == ProviderMatchMappingRecord.provider_id,
                        )
                        .where(
                            ProviderRecord.code == expected_provider.provider_code,
                            ProviderMatchMappingRecord.external_namespace == "fixture",
                            ProviderMatchMappingRecord.external_match_id == fixture_key,
                        )
                    )
                )
                if len(ids) != 1:
                    raise ValueError(
                        "full schedule fixture must have one explicit canonical mapping"
                    )
                match_id = ids[0]
            found.append(match_id)
            status = schedule.status_mapping.get(
                json_pointer(record, schedule.status_pointer)
            )
            exception = exceptions.get(match_id)
            if status is None or (status != "REGULAR_TIME_FINAL" and exception is None):
                raise ValueError(
                    "every schedule exception needs explicit reviewed completeness treatment"
                )
            if exception is not None:
                if (
                    status != exception.reason
                    or exception.provider_fixture_key != fixture_key
                ):
                    raise ValueError(
                        "completeness exception disagrees with captured provider record"
                    )
                self._review(
                    definition,
                    exception.evidence,
                    "QUANT_INTEGRITY_EXCEPTION_REVIEW_V1",
                    exception.model_dump(exclude={"evidence"}),
                    at,
                )
        if tuple(sorted(found)) != cohort.full_schedule_match_ids or len(
            set(found)
        ) != len(found):
            raise ValueError(
                "full captured schedule differs from the predeclared cohort metadata"
            )

    def verify_plan_metadata(
        self, definition: QuantIntegrityPlanDefinitionV1, *, at_utc: datetime
    ) -> None:
        definition = revalidate_integrity_model(definition)
        self._operation_time(at_utc)
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            self._series_plans(
                session, definition.integrity_pilot_series_id, expected=definition
            )
            self._metadata(session, definition, at_utc)

    def _metadata(self, session, definition, at):
        if (
            self._contract_only
            and definition.provenance.evidence_use
            != IntegrityEvidenceUse.SYNTHETIC_CONTRACT_ONLY
        ):
            raise ValueError(
                "injected contract-only admission repository cannot assert REAL_SOURCE"
            )
        self._recipe(definition)
        if self._contract_only:
            # Explicit test port only. It is never reachable by a production bridge.
            self.admission_repository.verify_synthetic_quant_metadata(
                definition, at_utc=at
            )
        else:
            self._admission_metadata(session, definition, at)
            if isinstance(definition, QuantIntegrityPlanDefinitionV2):
                self._versioned_metadata(session, definition, at)
            else:
                assert_complete_correction_pins(
                    session,
                    TrainingHistoryContextPinV2(
                        base_admissions=tuple(
                            CorrectionRefV2(
                                schema_version="TRAINING_FACT_ADMISSION_V1",
                                artifact_id=p.training_fact_admission_id,
                                content_hash=p.admission_hash,
                            )
                            for p in definition.admissions
                        ),
                        corrections=(),
                    ),
                    at,
                )
        self._reviewed_sources(session, definition, at)

    def _admission_metadata(self, session, definition, at):
        targets = {
            t.fact.match_id: t
            for s in definition.slices
            for t in s.content_payload.targets
        }
        if isinstance(definition, QuantIntegrityPlanDefinitionV2):
            targets = {}
        resolved = set()
        scopes = {s.canonical_season_id: s for s in definition.scope.provider_seasons}
        # Exact byte/hash-verified snapshots for this metadata pass only. Every
        # fact still verifies its own full raw record against these documents.
        captures = {}
        adapters = {}
        for pin in definition.admissions:
            parent = TrainingFactAdmissionRecord
            # Do not select parent.artifact_json/request_json: both embed target scores.
            header = (
                session.execute(
                    select(
                        parent.training_fact_admission_id,
                        parent.admission_hash,
                        parent.source_rights_admission_id,
                        parent.admitted_fact_count,
                        parent.admitted_fact_root,
                        parent.actual_started_at_utc,
                        parent.actual_completed_at_utc,
                        parent.persisted_at_utc,
                        func.json_extract(
                            parent.artifact_json, "$.content_payload"
                        ).label("content"),
                        func.json_extract(
                            parent.artifact_json, "$.training_fact_admission_id"
                        ).label("json_id"),
                        func.json_extract(
                            parent.artifact_json, "$.admission_hash"
                        ).label("json_hash"),
                    ).where(
                        parent.training_fact_admission_id
                        == pin.training_fact_admission_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if header is None:
                raise ValueError("pilot requires a persisted typed fact admission")
            content = TrainingFactAdmissionContentV1.model_validate_json(
                header["content"]
            )
            digest = tagged_canonical_sha256("TRAINING_FACT_ADMISSION_V1", content)
            if (
                digest != pin.admission_hash
                or digest != header["admission_hash"]
                or digest != header["json_hash"]
                or stable_id("TRAINING_FACT_ADMISSION_V1", digest)
                != pin.training_fact_admission_id
                or header["json_id"] != pin.training_fact_admission_id
                or any(
                    header[k] != getattr(content, k)
                    for k in (
                        "source_rights_admission_id",
                        "admitted_fact_count",
                        "admitted_fact_root",
                        "actual_started_at_utc",
                        "actual_completed_at_utc",
                        "persisted_at_utc",
                    )
                )
                or (
                    content.admitted_fact_count,
                    content.admitted_fact_root,
                    content.persisted_at_utc,
                    content.source_rights_admission_id,
                    content.source_rights_admission_hash,
                )
                != (
                    pin.admitted_fact_count,
                    pin.admitted_fact_root,
                    pin.persisted_at_utc,
                    pin.source_rights_admission_id,
                    pin.source_rights_admission_hash,
                )
                or content.persisted_at_utc > at
            ):
                raise ValueError(
                    "pilot admission header ID/hash/count/root/actual time mismatch"
                )
            rights = self.admission_repository._rights(
                session, pin.source_rights_admission_id
            )
            if (
                rights.admission_hash != pin.source_rights_admission_hash
                or rights.content_payload.recorded_at_utc
                != pin.source_rights_recorded_at_utc
            ):
                raise ValueError("pilot source rights pin mismatch")
            rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
            binding = TrainingFactBindingRecord
            rows = tuple(
                session.execute(
                    select(
                        binding.sequence,
                        binding.training_fact_binding_id,
                        binding.internal_match_id,
                        binding.match_result_id,
                        binding.provider_mapping_id,
                        binding.fixture_source_id,
                        binding.season_membership_id,
                        binding.match_result_admission_id,
                        func.json_extract(binding.artifact_json, "$.fact_hash").label(
                            "fact_hash"
                        ),
                        func.json_extract(
                            binding.artifact_json,
                            "$.content_payload.canonical_identity",
                        ).label("identity"),
                        func.json_extract(
                            binding.artifact_json, "$.content_payload.provider_mapping"
                        ).label("mapping"),
                    )
                    .where(
                        binding.training_fact_admission_id
                        == pin.training_fact_admission_id
                    )
                    .order_by(binding.sequence)
                ).mappings()
            )
            root = tagged_canonical_sha256(
                "TRAINING_FACT_ROOT_V1",
                {
                    "facts": [
                        {
                            "sequence": r["sequence"],
                            "training_fact_binding_id": r["training_fact_binding_id"],
                            "fact_hash": r["fact_hash"],
                        }
                        for r in rows
                    ]
                },
            )
            if (
                len(rows) != pin.admitted_fact_count
                or root != pin.admitted_fact_root
                or tuple(r["sequence"] for r in rows) != tuple(range(len(rows)))
            ):
                raise ValueError(
                    "pilot opaque fact metadata does not reproduce the admitted root"
                )
            # Project all metadata in one traversal; never fetch the sibling
            # normalized-result candidates or scan the whole request per fact.
            entries = func.json_each(parent.request_json, "$.submissions").table_valued(
                "key", "value"
            )
            projected = session.execute(
                select(
                    func.json_extract(
                        entries.c.value,
                        "$.candidate.canonical_identity.internal_match_id",
                    ),
                    func.json_extract(entries.c.value, "$.source_evidence"),
                )
                .select_from(parent)
                .join(entries, true())
                .where(
                    parent.training_fact_admission_id == pin.training_fact_admission_id,
                )
            )
            evidence_by_match = {}
            for match_id, evidence_json in projected:
                if match_id in evidence_by_match or evidence_json is None:
                    raise ValueError("ambiguous or missing submission metadata")
                evidence_by_match[match_id] = (
                    TrainingSourceEvidenceV1.model_validate_json(evidence_json)
                )
            if set(evidence_by_match) != {row["internal_match_id"] for row in rows}:
                raise ValueError(
                    "submission metadata must cover the exact admitted facts"
                )
            archives = set()
            for row in rows:
                identity = TrainingCanonicalMatchIdentityV1.model_validate_json(
                    row["identity"]
                )
                mapping = TrainingProviderMatchMappingV1.model_validate_json(
                    row["mapping"]
                )
                fx_row = _required(
                    session,
                    TrainingFactFixtureSourceRecord,
                    (pin.training_fact_admission_id, row["fixture_source_id"]),
                )
                mem_row = _required(
                    session,
                    MatchSeasonMembershipRecord,
                    (pin.training_fact_admission_id, row["season_membership_id"]),
                )
                verify_training_row(fx_row)
                verify_training_row(mem_row)
                fixture = TrainingFixtureSourceV1.model_validate_json(
                    fx_row.artifact_json
                )
                membership = MatchSeasonMembershipV1.model_validate_json(
                    mem_row.artifact_json
                )
                member = membership.content_payload
                membership.assert_matches(mapping, identity)
                if (
                    identity.internal_match_id != row["internal_match_id"]
                    or mapping.mapping_id != row["provider_mapping_id"]
                    or fx_row.internal_match_id != identity.internal_match_id
                    or mem_row.internal_match_id != identity.internal_match_id
                    or fx_row.provider_mapping_id != mapping.mapping_id
                    or mem_row.provider_mapping_id != mapping.mapping_id
                    or mem_row.fixture_source_id != fx_row.fixture_source_id
                    or fixture.internal_match_id != identity.internal_match_id
                    or fixture.fixture_source_record_id
                    != member.fixture_source_record_id
                    or fixture.fixture_record_sha256 != member.fixture_record_sha256
                    or identity.internal_competition_id
                    != definition.scope.competition_id
                    or identity.competition_type != definition.scope.competition_type
                    or member.canonical_season_id not in scopes
                ):
                    raise ValueError(
                        "typed fixture/mapping/season metadata lineage mismatch"
                    )
                expected = scopes[member.canonical_season_id]
                if (
                    member.source_id,
                    member.provider_code,
                    member.provider_competition_id,
                    member.provider_season_id,
                ) != (
                    expected.source_id,
                    expected.provider_code,
                    expected.provider_competition_id,
                    expected.provider_season_id,
                ):
                    raise ValueError(
                        "admission metadata is outside reviewed provider season scope"
                    )
                result_capture = session.scalar(
                    select(MatchResultAdmissionRecord.capture_receipt_id).where(
                        MatchResultAdmissionRecord.training_fact_admission_id
                        == pin.training_fact_admission_id,
                        MatchResultAdmissionRecord.match_result_admission_id
                        == row["match_result_admission_id"],
                        MatchResultAdmissionRecord.internal_match_id
                        == identity.internal_match_id,
                        MatchResultAdmissionRecord.match_result_id
                        == row["match_result_id"],
                        MatchResultAdmissionRecord.provider_mapping_id
                        == mapping.mapping_id,
                    )
                )
                if result_capture is None:
                    raise ValueError("target opaque result admission is not resolvable")
                for kind, receipt_id in (
                    ("FIXTURE", fx_row.capture_receipt_id),
                    ("SEASON_MEMBERSHIP", mem_row.capture_receipt_id),
                    ("RESULT", result_capture),
                ):
                    if (kind, receipt_id) not in archives:
                        self._receipt_metadata(session, pin, kind, receipt_id)
                        archives.add((kind, receipt_id))
                self._fixture_metadata(
                    session,
                    definition,
                    row,
                    fixture,
                    membership,
                    mapping,
                    identity,
                    mem_row,
                    at,
                    evidence_by_match[identity.internal_match_id],
                    captures,
                    adapters,
                )
                if identity.internal_match_id in targets:
                    target = targets[identity.internal_match_id]
                    fact_ref = AdmittedFactRefV1(
                        training_fact_binding_id=row["training_fact_binding_id"],
                        fact_hash=row["fact_hash"],
                        match_id=row["internal_match_id"],
                        match_result_id=row["match_result_id"],
                    )
                    if (
                        target.identity != identity
                        or target.fact != fact_ref
                        or target.training_fact_admission_id
                        != pin.training_fact_admission_id
                        or target.fixture_source_available_at_utc
                        != fixture.source_available_at_utc
                        or target.mapping_source_available_at_utc
                        != member.source_available_at_utc
                    ):
                        raise ValueError(
                            "target definition differs from typed metadata projection"
                        )
                    if identity.internal_match_id in resolved:
                        raise ValueError("target appears in more than one admission")
                    resolved.add(identity.internal_match_id)
            if archives != {(a.kind, a.archive_id) for a in pin.archives}:
                raise ValueError(
                    "plan must pin every exact fixture/membership/result archive"
                )
        if resolved != set(targets):
            raise ValueError("every plan target must resolve before result access")

    def _versioned_metadata(self, session, definition, at):
        """Opaque version refs plus fixture/season metadata, never target scores."""
        from football_system.infrastructure.database.training_correction_repository import (
            ADMISSIONS,
        )
        from football_system.application.training_correction import (
            TrainingCorrectionJsonAdapterV2,
        )
        from football_system.domain.training_correction import CorrectionEvidenceV2

        pin = definition.correction_context
        if definition.context_registered_at_utc > at:
            raise ValueError("context registration boundary follows operation")
        assert_complete_correction_pins(session, pin, at)
        versions = {}
        for parent in definition.admissions:
            if parent.persisted_at_utc > definition.context_registered_at_utc:
                raise ValueError("base admission follows pinned context boundary")
            b = TrainingFactBindingRecord
            for row in session.execute(
                select(
                    b.training_fact_binding_id,
                    b.internal_match_id,
                    b.match_result_id,
                    func.json_extract(b.artifact_json, "$.fact_hash").label(
                        "fact_hash"
                    ),
                    func.json_extract(
                        b.artifact_json, "$.content_payload.canonical_identity"
                    ).label("identity"),
                    func.json_extract(
                        b.artifact_json,
                        "$.content_payload.fixture_source.source_available_at_utc",
                    ).label("fixture_at"),
                    func.json_extract(
                        b.artifact_json,
                        "$.content_payload.season_membership.content_payload.source_available_at_utc",
                    ).label("mapping_at"),
                    func.json_extract(
                        b.artifact_json,
                        "$.content_payload.normalized_result.available_at_utc",
                    ).label("result_at"),
                ).where(
                    b.training_fact_admission_id == parent.training_fact_admission_id
                )
            ).mappings():
                digest = tagged_canonical_sha256(
                    "TRAINING_BASE_FACT_VERSION_V2",
                    {
                        "admission_id": parent.training_fact_admission_id,
                        "admission_hash": parent.admission_hash,
                        "binding_id": row["training_fact_binding_id"],
                        "fact_hash": row["fact_hash"],
                    },
                )
                ref = CorrectionRefV2(
                    schema_version="TRAINING_BASE_FACT_VERSION_V2",
                    artifact_id=stable_id("TRAINING_BASE_FACT_VERSION_V2", digest),
                    content_hash=digest,
                )
                versions[ref.artifact_id] = dict(
                    fact=VersionedFactRefV2(
                        version=ref,
                        base_admission=next(
                            p
                            for p in pin.base_admissions
                            if p.artifact_id == parent.training_fact_admission_id
                        ),
                        base_binding=CorrectionRefV2(
                            schema_version="TRAINING_FACT_BINDING_V1",
                            artifact_id=row["training_fact_binding_id"],
                            content_hash=row["fact_hash"],
                        ),
                        match_id=row["internal_match_id"],
                        match_result_id=row["match_result_id"],
                    ),
                    identity=TrainingCanonicalMatchIdentityV1.model_validate_json(
                        row["identity"]
                    ),
                    fixture_at=_source_time(row["fixture_at"]),
                    mapping_at=_source_time(row["mapping_at"]),
                    result_at=_source_time(row["result_at"]),
                    sequence=0,
                )
        paths = {
            "predecessor": "intent.predecessor",
            "identity": "intent.candidate.identity",
            "stream": "intent.candidate.stream",
            "fixture_at": "intent.candidate.fixture_source_available_at_utc",
            "mapping_at": "intent.candidate.mapping_source_available_at_utc",
            "result_at": "intent.candidate.result_source_available_at_utc",
            "result_id": "intent.match_result_id",
            "rights": "intent.source_rights_admission",
            "evidence": "intent.evidence",
            "provider_mapping": "intent.candidate.provider_mapping",
            **{
                key: "intent.candidate." + key
                for key in (
                    "provider_home_team_id",
                    "provider_away_team_id",
                    "provider_competition_id",
                    "provider_season_id",
                    "home_team_alias_id",
                    "away_team_alias_id",
                    "competition_mapping_id",
                    "season_mapping_version",
                    "mapping_policy_version",
                )
            },
        }
        rows = (
            session.execute(
                select(
                    ADMISSIONS.c.correction_id,
                    ADMISSIONS.c.content_hash,
                    ADMISSIONS.c.revision_sequence,
                    ADMISSIONS.c.registered_at_utc,
                    *(
                        func.json_extract(
                            ADMISSIONS.c.artifact_json, "$.content_payload." + path
                        ).label(name)
                        for name, path in paths.items()
                    ),
                )
                .where(
                    ADMISSIONS.c.correction_id.in_(
                        tuple(r.artifact_id for r in pin.corrections)
                    )
                )
                .order_by(ADMISSIONS.c.revision_sequence, ADMISSIONS.c.correction_id)
            )
            .mappings()
            .all()
        )
        if len(rows) != len(pin.corrections):
            raise ValueError("missing pinned controlled correction metadata")
        predecessors = set()
        for row in rows:
            ref = next(
                r for r in pin.corrections if r.artifact_id == row["correction_id"]
            )
            predecessor = CorrectionRefV2.model_validate_json(row["predecessor"])
            previous = versions.get(predecessor.artifact_id)
            identity = TrainingCanonicalMatchIdentityV1.model_validate_json(
                row["identity"]
            )
            if (
                previous is None
                or previous["fact"].version != predecessor
                or row["revision_sequence"] != previous["sequence"] + 1
            ):
                raise ValueError("context metadata has an incomplete predecessor chain")
            if predecessor in predecessors:
                raise ValueError("correction metadata predecessor fork")
            predecessors.add(predecessor)
            if (
                ref.content_hash != row["content_hash"]
                or ref.artifact_id != stable_id(ref.schema_version, ref.content_hash)
                or row["registered_at_utc"] > definition.context_registered_at_utc
            ):
                raise ValueError("context correction hash/registration mismatch")
            stream = strict_json_bytes(row["stream"].encode())
            if (
                stream["internal_match_id"] != identity.internal_match_id
                or identity.internal_match_id != previous["fact"].match_id
            ):
                raise ValueError(
                    "corrected identity must preserve the existing match anchor"
                )
            rights = CorrectionRefV2.model_validate_json(row["rights"])
            base_pin = next(
                p
                for p in definition.admissions
                if p.training_fact_admission_id
                == previous["fact"].base_admission.artifact_id
            )
            if (rights.artifact_id, rights.content_hash) != (
                base_pin.source_rights_admission_id,
                base_pin.source_rights_admission_hash,
            ):
                raise ValueError("correction must preserve pinned source rights scope")
            scope = next(
                (
                    s
                    for s in definition.scope.provider_seasons
                    if s.canonical_season_id == identity.season
                ),
                None,
            )
            if (
                scope is None
                or identity.internal_competition_id != definition.scope.competition_id
                or identity.competition_type != definition.scope.competition_type
                or (
                    stream["source_id"],
                    stream["provider_code"],
                    row["provider_competition_id"],
                    row["provider_season_id"],
                )
                != (
                    scope.source_id,
                    scope.provider_code,
                    scope.provider_competition_id,
                    scope.provider_season_id,
                )
            ):
                raise ValueError(
                    "corrected metadata outside exact reviewed provider season scope"
                )
            evidence = CorrectionEvidenceV2.model_validate_json(row["evidence"])
            if (
                evidence.home_team_alias_id,
                evidence.away_team_alias_id,
                evidence.competition_mapping_id,
            ) != (
                row["home_team_alias_id"],
                row["away_team_alias_id"],
                row["competition_mapping_id"],
            ):
                raise ValueError("corrected metadata reference mismatch")
            adapter = TrainingCorrectionJsonAdapterV2.model_validate(
                strict_json_bytes(
                    self.evidence.read(
                        evidence.adapter.evidence_reference,
                        evidence.adapter.evidence_sha256,
                    )
                )
            )
            if (
                adapter.provider_code,
                adapter.provider_fixture_namespace,
                adapter.season_mapping_version,
                adapter.mapping_policy_version,
            ) != (
                stream["provider_code"],
                stream["provider_fixture_namespace"],
                row["season_mapping_version"],
                row["mapping_policy_version"],
            ):
                raise ValueError("corrected adapter metadata mismatch")
            for capture_ref, paths_, source_at in (
                (evidence.fixture, adapter.fixture, row["fixture_at"]),
                (evidence.scope, adapter.scope, row["mapping_at"]),
            ):
                receipt, document = self._capture(
                    definition,
                    capture_ref.capture_receipt_id,
                    definition.context_registered_at_utc,
                    session=session,
                )
                raw = json_pointer(document, capture_ref.record_pointer)
                fields = {
                    key: json_pointer(raw, path)
                    for key, path in paths_.model_dump().items()
                }
                if (
                    (
                        receipt.receipt_hash,
                        receipt.payload_sha256,
                        receipt.source_id,
                        receipt.provider_code,
                    )
                    != (
                        capture_ref.receipt_hash,
                        capture_ref.payload_sha256,
                        stream["source_id"],
                        stream["provider_code"],
                    )
                    or provider_record_sha256(raw) != capture_ref.record_sha256
                    or (
                        fields["fixture_key"],
                        fields["competition_id"],
                        fields["season_id"],
                        _source_time(fields["available_at_utc"]),
                    )
                    != (
                        stream["provider_fixture_key"],
                        row["provider_competition_id"],
                        row["provider_season_id"],
                        _source_time(source_at),
                    )
                ):
                    raise ValueError("corrected fixture/season raw metadata mismatch")
                if capture_ref is evidence.fixture and (
                    fields["home_team_id"],
                    fields["away_team_id"],
                    _source_time(fields["kickoff_at_utc"]),
                ) != (
                    row["provider_home_team_id"],
                    row["provider_away_team_id"],
                    identity.kickoff_at_utc,
                ):
                    raise ValueError("corrected fixture identity metadata mismatch")
            mapping = TrainingProviderMatchMappingV1.model_validate_json(
                row["provider_mapping"]
            )
            if (
                mapping.provider_code,
                mapping.external_namespace,
                mapping.external_match_id,
                mapping.internal_match_id,
            ) != (
                stream["provider_code"],
                stream["provider_fixture_namespace"],
                stream["provider_fixture_key"],
                stream["internal_match_id"],
            ):
                raise ValueError(
                    "corrected provider mapping must retain the stream anchor"
                )
            stored = _required(
                session, ProviderMatchMappingRecord, evidence.provider_mapping_id
            )
            provider = _required(session, ProviderRecord, stored.provider_id)
            _required(session, MatchRecord, identity.internal_match_id)
            if mapping != TrainingProviderMatchMappingV1(
                mapping_id=stored.mapping_id,
                provider_code=provider.code,
                external_namespace=stored.external_namespace,
                external_match_id=stored.external_match_id,
                internal_match_id=stored.internal_match_id,
                resolution_method=stored.resolution_method,
                confidence=stored.confidence,
                available_at_utc=stored.available_at_utc,
            ):
                raise ValueError(
                    "corrected metadata differs from registered mapping anchor"
                )
            for alias_id, raw_team, team_id in (
                (
                    evidence.home_team_alias_id,
                    row["provider_home_team_id"],
                    identity.internal_home_team_id,
                ),
                (
                    evidence.away_team_alias_id,
                    row["provider_away_team_id"],
                    identity.internal_away_team_id,
                ),
            ):
                alias = _required(session, ProviderTeamAliasRecord, alias_id)
                if (
                    alias.provider_id,
                    alias.provider_team_id,
                    alias.internal_team_id,
                ) != (
                    stored.provider_id,
                    raw_team,
                    team_id,
                ) or alias.available_at_utc > _source_time(row["fixture_at"]):
                    raise ValueError("corrected registered team revision mismatch")
            comp = _required(
                session,
                ProviderCompetitionMappingRecord,
                evidence.competition_mapping_id,
            )
            if (
                comp.provider_id,
                comp.provider_competition_id,
                comp.internal_competition_id,
                comp.season,
                comp.competition_type,
            ) != (
                stored.provider_id,
                row["provider_competition_id"],
                identity.internal_competition_id,
                identity.season,
                identity.competition_type,
            ) or comp.available_at_utc > _source_time(row["mapping_at"]):
                raise ValueError(
                    "corrected registered competition/season revision mismatch"
                )
            versions[ref.artifact_id] = dict(
                fact=VersionedFactRefV2(
                    version=ref,
                    base_admission=previous["fact"].base_admission,
                    base_binding=previous["fact"].base_binding,
                    match_id=identity.internal_match_id,
                    match_result_id=row["result_id"],
                ),
                identity=identity,
                fixture_at=_source_time(row["fixture_at"]),
                mapping_at=_source_time(row["mapping_at"]),
                result_at=_source_time(row["result_at"]),
                sequence=row["revision_sequence"],
            )
        for item in definition.slices:
            for target in item.content_payload.targets:
                eligible = [
                    v
                    for v in versions.values()
                    if v["fact"].match_id == target.fact.match_id
                    and max(v["fixture_at"], v["mapping_at"], v["result_at"])
                    <= item.content_payload.evaluation_as_of_at_utc
                ]
                selected = max(eligible, key=lambda v: v["sequence"], default=None)
                if selected is None or target != QuantIntegrityTargetV2(
                    identity=selected["identity"],
                    fact=selected["fact"],
                    training_fact_admission_id=selected[
                        "fact"
                    ].base_admission.artifact_id,
                    fixture_source_available_at_utc=selected["fixture_at"],
                    mapping_source_available_at_utc=selected["mapping_at"],
                ):
                    raise ValueError(
                        "target differs from exact selected version metadata"
                    )

    @staticmethod
    def _capture_metadata(session, receipt_id):
        table = TrainingCaptureReceiptRecord.__table__
        # The capture's sealed header can be checked without reading result bytes.
        row = (
            session.execute(
                select(*(c for c in table.c if c.name != "payload_bytes")).where(
                    table.c.capture_receipt_id == receipt_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("missing captured archive metadata")
        values = {k: v for k, v in row.items() if k != "row_sha256"}
        if (
            tagged_canonical_sha256(
                "TRAINING_SQL_ROW_V1:training_capture_receipts", values
            )
            != row["row_sha256"]
        ):
            raise ValueError("capture metadata row hash mismatch")
        receipt = TrainingCaptureReceiptV1.model_validate_json(row["artifact_json"])
        if (
            receipt.capture_receipt_id != receipt_id
            or any(
                row[key] != getattr(receipt, key)
                for key in (
                    "request_sha256",
                    "source_rights_admission_id",
                    "source_id",
                    "payload_sha256",
                    "local_imported_at_utc",
                    "archive_created_at_utc",
                    "registered_at_utc",
                )
            )
            or _required(session, ProviderRecord, row["provider_id"]).code
            != receipt.provider_code
        ):
            raise ValueError("capture metadata typed projection mismatch")
        return receipt

    def _receipt_metadata(self, session, pin, kind, receipt_id):
        receipt = self._capture_metadata(session, receipt_id)
        expected = next(
            (a for a in pin.archives if (a.kind, a.archive_id) == (kind, receipt_id)),
            None,
        )
        if (
            expected is None
            or receipt.source_rights_admission_id != pin.source_rights_admission_id
            or (
                receipt.capture_receipt_id,
                receipt.payload_sha256,
                receipt.local_imported_at_utc,
                receipt.archive_created_at_utc,
                receipt.registered_at_utc,
            )
            != (
                expected.archive_id,
                expected.payload_hash,
                expected.local_imported_at_utc,
                expected.created_at_utc,
                expected.registered_at_utc,
            )
        ):
            raise ValueError(
                "archive pin differs from captured actual possession metadata"
            )

    def _fixture_metadata(
        self,
        session,
        definition,
        row,
        fixture,
        membership,
        mapping,
        identity,
        mem_row,
        at,
        evidence,
        captures,
        adapters,
    ):
        adapter_key = (
            evidence.adapter.evidence_reference,
            evidence.adapter.evidence_sha256,
        )
        if adapter_key not in adapters:
            adapters[adapter_key] = TrainingJsonAdapterV1.model_validate(
                strict_json_bytes(self.evidence.read(*adapter_key))
            )
        adapter = adapters[adapter_key]
        member = membership.content_payload
        if (
            evidence.fixture.capture_receipt_id != fixture.fixture_source_archive_id
            or evidence.scope.capture_receipt_id
            != member.provider_scope_raw_artifact_id
            or fixture.fixture_source_record_id
            != (evidence.fixture.record_pointer or "ROOT")
            or (
                adapter.scope.competition_id,
                adapter.scope.season_id,
                adapter.scope.fixture_key,
            )
            != (
                member.provider_competition_field_path,
                member.provider_season_field_path,
                member.provider_fixture_field_path,
            )
            or (adapter.provider_code, adapter.provider_fixture_namespace)
            != (fixture.provider_code, fixture.provider_fixture_namespace)
        ):
            raise ValueError(
                "fixture/membership adapter does not bind exact raw references"
            )
        extracted = []
        for ref, paths, digest in (
            (evidence.fixture, adapter.fixture, fixture.fixture_record_sha256),
            (evidence.scope, adapter.scope, member.provider_scope_record_sha256),
        ):
            if ref.capture_receipt_id not in captures:
                captures[ref.capture_receipt_id] = self._capture(
                    definition, ref.capture_receipt_id, at, session=session
                )
            _, document = captures[ref.capture_receipt_id]
            raw = json_pointer(document, ref.record_pointer)
            if provider_record_sha256(raw) != digest:
                raise ValueError("fixture/membership full raw record hash mismatch")
            values = {
                key: json_pointer(raw, pointer)
                for key, pointer in paths.model_dump().items()
            }
            if len(set(paths.model_dump().values())) != len(paths.model_dump()):
                raise ValueError(
                    "fixture/membership semantic fields require distinct source paths"
                )
            if (
                values["fixture_key"],
                values["competition_id"],
                values["season_id"],
            ) != (
                fixture.provider_fixture_key,
                member.provider_competition_id,
                member.provider_season_id,
            ):
                raise ValueError("raw fixture/competition/season relationship mismatch")
            extracted.append(values)
        fx, scope = extracted
        if (
            _source_time(fx["kickoff_at_utc"]) != identity.kickoff_at_utc
            or _source_time(fx["available_at_utc"]) != fixture.source_available_at_utc
            or _source_time(scope["available_at_utc"]) != member.source_available_at_utc
        ):
            raise ValueError("raw fixture/membership timestamp disagreement")
        match = _required(session, MatchRecord, identity.internal_match_id)
        canonical = _required(
            session, CanonicalMatchIdentityRecord, identity.internal_match_id
        )
        stored_mapping = _required(
            session, ProviderMatchMappingRecord, mapping.mapping_id
        )
        provider = _required(session, ProviderRecord, stored_mapping.provider_id)
        competition = _required(
            session, CompetitionRecord, identity.internal_competition_id
        )
        if (
            definition.provenance.evidence_use == IntegrityEvidenceUse.REAL_SOURCE
            and provider.provider_kind.upper() in {"TEST", "MOCK", "SYNTHETIC"}
        ):
            raise ValueError(
                "test provider cannot supply REAL_SOURCE integrity evidence"
            )
        if (
            competition.country_code != "DE"
            or (
                match.competition_id,
                match.home_team_id,
                match.away_team_id,
                match.kickoff_at_utc,
                canonical.season,
                canonical.competition_type,
            )
            != (
                identity.internal_competition_id,
                identity.internal_home_team_id,
                identity.internal_away_team_id,
                identity.kickoff_at_utc,
                identity.season,
                identity.competition_type,
            )
            or max(match.available_at_utc, canonical.available_at_utc)
            > fixture.source_available_at_utc
        ):
            raise ValueError(
                "registered canonical competition/fixture metadata mismatch"
            )
        registered_mapping = TrainingProviderMatchMappingV1(
            mapping_id=stored_mapping.mapping_id,
            provider_code=provider.code,
            external_namespace=stored_mapping.external_namespace,
            external_match_id=stored_mapping.external_match_id,
            internal_match_id=stored_mapping.internal_match_id,
            resolution_method=stored_mapping.resolution_method,
            confidence=stored_mapping.confidence,
            available_at_utc=stored_mapping.available_at_utc,
        )
        if (
            registered_mapping != mapping
            or stored_mapping.supersedes_mapping_id is not None
            or session.scalar(
                select(ProviderMatchMappingRecord.mapping_id).where(
                    ProviderMatchMappingRecord.supersedes_mapping_id
                    == mapping.mapping_id
                )
            )
            is not None
            or session.scalar(
                select(MatchResultAdmissionRecord.match_result_admission_id).where(
                    MatchResultAdmissionRecord.provider_mapping_id
                    == mapping.mapping_id,
                    MatchResultAdmissionRecord.match_result_admission_id
                    != row["match_result_admission_id"],
                )
            )
            is not None
        ):
            raise ValueError(
                "mapping/result correction requires a new controlled admission"
            )
        for alias_id, raw_team, team_id in (
            (
                mem_row.home_team_alias_id,
                fx["home_team_id"],
                identity.internal_home_team_id,
            ),
            (
                mem_row.away_team_alias_id,
                fx["away_team_id"],
                identity.internal_away_team_id,
            ),
        ):
            alias = _required(session, ProviderTeamAliasRecord, alias_id)
            if (alias.provider_id, alias.provider_team_id, alias.internal_team_id) != (
                provider.provider_id,
                raw_team,
                team_id,
            ) or alias.available_at_utc > fixture.source_available_at_utc:
                raise ValueError("raw provider team mapping mismatch")
        comp = _required(
            session, ProviderCompetitionMappingRecord, mem_row.competition_mapping_id
        )
        if (
            comp.provider_id,
            comp.provider_competition_id,
            comp.internal_competition_id,
            comp.season,
            comp.competition_type,
        ) != (
            provider.provider_id,
            member.provider_competition_id,
            definition.scope.competition_id,
            identity.season,
            identity.competition_type,
        ) or comp.available_at_utc > member.source_available_at_utc:
            raise ValueError("raw provider competition/season mapping mismatch")

    def seal_plan(self, plan: QuantIntegrityPlanV1) -> None:
        plan = revalidate_integrity_model(plan)
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            definition = plan.content_payload.definition
            self._series_plans(
                session, definition.integrity_pilot_series_id, expected=definition
            )
            previous = session.get(QuantIntegrityPlanRecord, plan.artifact_id)
            if previous is not None:
                _same_artifact(previous, plan)
                self._metadata(session, plan.content_payload.definition, self._now())
                return
            at = self._operation_time(plan.content_payload.sealed_at_utc, recent=True)
            self._metadata(session, definition, plan.content_payload.sealed_at_utc)
            series = session.get(
                QuantIntegritySeriesRecord, definition.integrity_pilot_series_id
            )
            if series is None:
                existing = tuple(
                    session.scalars(
                        select(QuantIntegritySeriesRecord)
                        .where(
                            QuantIntegritySeriesRecord.scope_hash
                            == definition.scope_hash,
                        )
                        .order_by(QuantIntegritySeriesRecord.series_sequence)
                    )
                )
                predecessor = definition.previous_terminal_attestation
                if existing:
                    last = session.scalar(
                        select(QuantIntegrityAttestationRecord).where(
                            QuantIntegrityAttestationRecord.series_id
                            == existing[-1].series_id
                        )
                    )
                    if (
                        last is None
                        or predecessor is None
                        or IntegrityArtifactRefV1.of(
                            _artifact(last, QuantIntegrityAttestationV1)
                        )
                        != predecessor
                    ):
                        raise ValueError(
                            "new pilot series must disclose the exact previous terminal attestation"
                        )
                elif predecessor is not None:
                    raise ValueError("first pilot series cannot claim a predecessor")
                series = _row(
                    QuantIntegritySeriesRecord,
                    series_id=definition.integrity_pilot_series_id,
                    scope_hash=definition.scope_hash,
                    series_sequence=len(existing) + 1,
                    evidence_use=definition.provenance.evidence_use.value,
                    previous_attestation_id=None
                    if predecessor is None
                    else predecessor.artifact_id,
                    operator_id=self.operator_id,
                    persisted_at_utc=at,
                )
                session.add(series)
                session.flush()
            _verify_row(series)
            if (
                series.scope_hash != definition.scope_hash
                or series.operator_id != self.operator_id
                or series.evidence_use != definition.provenance.evidence_use.value
            ):
                raise ValueError("pilot series scope/operator/provenance mismatch")
            predecessor = definition.previous_terminal_attestation
            if series.previous_attestation_id != (
                None if predecessor is None else predecessor.artifact_id
            ):
                raise ValueError("plan must preserve the series predecessor disclosure")
            self._recipe(definition)
            self._append(
                session,
                QuantIntegrityPlanRecord,
                plan,
                series_id=series.series_id,
                scope_hash=series.scope_hash,
                evidence_use=series.evidence_use,
                sealed_at_utc=plan.content_payload.sealed_at_utc,
            )

    def load_plan(self, plan_ref: IntegrityArtifactRefV1) -> QuantIntegrityPlanV1:
        with self._sessions.begin() as session:
            return _referenced(
                session, QuantIntegrityPlanRecord, QuantIntegrityPlanV1, plan_ref
            )

    def reserve_attempt(
        self, plan_ref: IntegrityArtifactRefV1, *, actual_started_at_utc: datetime
    ) -> QuantIntegrityAttemptReservationV1:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            plan = _referenced(
                session, QuantIntegrityPlanRecord, QuantIntegrityPlanV1, plan_ref
            )
            definition = plan.content_payload.definition
            at = self._operation_time(actual_started_at_utc, recent=True)
            self._owner(session, definition.integrity_pilot_series_id)
            self._assert_open(session, definition.integrity_pilot_series_id)
            self._series_plans(
                session, definition.integrity_pilot_series_id, expected=definition
            )
            self._recipe(definition)
            previous = self._attempts(session, definition.integrity_pilot_series_id)
            if session.scalar(
                select(func.count())
                .select_from(QuantIntegrityReservationRecord)
                .where(
                    QuantIntegrityReservationRecord.series_id
                    == definition.integrity_pilot_series_id
                )
            ) != len(previous):
                raise ValueError(
                    "outstanding reservation requires retained crash recovery"
                )
            # Source failures or code drift AFTER this reservation remain failed
            # attempts; known code/recipe incompatibility cannot enter the series.
            reservation = QuantIntegrityAttemptReservationV1.freeze(
                content_payload=QuantIntegrityAttemptReservationContentV1(
                    integrity_pilot_series_id=definition.integrity_pilot_series_id,
                    scope_hash=definition.scope_hash,
                    sequence=len(previous) + 1,
                    plan_ref=plan_ref,
                    prior_attempt_count=len(previous),
                    prior_attempt_root=integrity_attempt_root(previous),
                    actual_started_at_utc=at,
                )
            )
            self._append(
                session,
                QuantIntegrityReservationRecord,
                reservation,
                series_id=definition.integrity_pilot_series_id,
                sequence=len(previous) + 1,
                plan_id=plan.artifact_id,
                actual_started_at_utc=at,
            )
            return reservation

    def load_verified_training_admission(
        self, pin: TrainingAdmissionPinV1, *, at_utc: datetime
    ) -> TrainingFactAdmissionV1:
        pin = revalidate_integrity_model(pin)
        observed_at = self._operation_time(at_utc)
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            candidates = tuple(
                session.scalars(
                    select(QuantIntegrityReservationRecord).where(
                        ~select(QuantIntegrityAttemptRecord.artifact_id)
                        .where(
                            QuantIntegrityAttemptRecord.reservation_id
                            == QuantIntegrityReservationRecord.artifact_id,
                        )
                        .exists()
                    )
                )
            )
            authorized = False
            for row in candidates:
                reserved = _artifact(
                    row, QuantIntegrityAttemptReservationV1
                ).content_payload
                plan = _referenced(
                    session,
                    QuantIntegrityPlanRecord,
                    QuantIntegrityPlanV1,
                    reserved.plan_ref,
                )
                if (
                    row.operator_id == self.operator_id
                    and reserved.actual_started_at_utc <= at_utc
                    and pin in plan.content_payload.definition.admissions
                ):
                    authorized = True
                    break
            if not authorized:
                raise ValueError(
                    "full fact/result access requires a committed pinned pilot reservation"
                )
            return self._read_operational_admission(
                pin, at_utc, observed_at, session=session
            )

    def _read_operational_admission(self, pin, at_utc, observed_at, *, session):
        # A pending historical reservation is not present-day permission to
        # read source results. Verify rights without opening result payloads.
        self._current_research_rights((pin,), at_utc, observed_at, session=session)
        value = self._load_admission(pin, at_utc, session=session)
        assert_complete_correction_pins(
            session, base_context_pin((value,)), observed_at
        )
        value.source_rights_admission.assert_active_for(
            self._now(), TRAINING_FACT_REQUIRED_USES
        )
        return value

    def load_verified_correction_context(self, pin, *, at_utc):
        at = self._operation_time(at_utc)
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            definition = None
            for row in session.scalars(
                select(QuantIntegrityReservationRecord).where(
                    ~select(QuantIntegrityAttemptRecord.artifact_id)
                    .where(
                        QuantIntegrityAttemptRecord.reservation_id
                        == QuantIntegrityReservationRecord.artifact_id
                    )
                    .exists()
                )
            ):
                reserved = _artifact(
                    row, QuantIntegrityAttemptReservationV1
                ).content_payload
                plan = _referenced(
                    session,
                    QuantIntegrityPlanRecord,
                    QuantIntegrityPlanV1,
                    reserved.plan_ref,
                )
                candidate = plan.content_payload.definition
                if (
                    row.operator_id == self.operator_id
                    and reserved.actual_started_at_utc <= at_utc
                    and isinstance(candidate, QuantIntegrityPlanDefinitionV2)
                    and candidate.correction_context == pin
                ):
                    definition = candidate
                    break
            if definition is None:
                raise ValueError(
                    "versioned result access requires a committed pinned pilot reservation"
                )
            self._current_research_rights(
                definition.admissions, at_utc, at, session=session
            )
            context = correction_context_in_session(
                self.admission_repository, session, pin, at_utc
            )
            self._current_research_rights(
                definition.admissions, self._now(), session=session
            )
            assert_complete_correction_pins(session, pin, self._now())
            return context

    def _current_research_rights(self, pins, *boundaries, session):
        """Rights metadata only; check every distinct pin before any source read.

        The local snapshots are not authorization caches. All are checked again
        at one final clock boundary, so expiry while checking another pin fails.
        """
        checked = {}
        for pin in pins:
            key = (
                pin.source_rights_admission_id,
                pin.source_rights_admission_hash,
                pin.source_rights_recorded_at_utc,
            )
            if key in checked:
                continue
            rights = revalidate_integrity_model(
                self.admission_repository.load_rights(pin.source_rights_admission_id)
                if self._contract_only
                else self.admission_repository._rights(
                    session, pin.source_rights_admission_id
                )
            )
            if (
                rights.source_rights_admission_id != pin.source_rights_admission_id
                or rights.admission_hash != pin.source_rights_admission_hash
                or rights.content_payload.recorded_at_utc
                != pin.source_rights_recorded_at_utc
            ):
                raise ValueError("operational read source rights pin mismatch")
            for boundary in (*boundaries, self._now()):
                rights.assert_active_for(boundary, TRAINING_FACT_REQUIRED_USES)
            checked[key] = rights
        current = self._now()
        for rights in checked.values():
            rights.assert_active_for(current, TRAINING_FACT_REQUIRED_USES)

    def _series_plans(self, session, series_id, *, expected=None, cache=None):
        """The first accepted plan fixes the series code/recipe, even before runs.

        An unavailable revision needs a separately authorized lineage policy;
        accepting a different revision here would strand unreplayable attempts.
        """
        plans = tuple(
            _artifact(row, QuantIntegrityPlanV1, cache=cache)
            for row in session.scalars(
                select(QuantIntegrityPlanRecord)
                .where(QuantIntegrityPlanRecord.series_id == series_id)
                .order_by(
                    QuantIntegrityPlanRecord.persisted_at_utc,
                    QuantIntegrityPlanRecord.artifact_id,
                )
                .execution_options(populate_existing=True)
            )
        )
        if expected is None and plans:
            expected = plans[0].content_payload.definition
        for plan in plans:
            definition = plan.content_payload.definition
            if (
                definition.implementation_code_revision
                != expected.implementation_code_revision
                or definition.build_recipe != expected.build_recipe
            ):
                raise ValueError(
                    "incompatible pilot series code/recipe transition; existing series pins must be preserved"
                )
        return plans

    def _load_admission(self, pin, at, *, session):
        """Historical integrity check, not current authorization for a new read.

        Operational callers must first check current rights separately; sealed
        production evidence uses the original pilot's research authorization.
        """
        value = revalidate_integrity_model(
            self.admission_repository.load(pin.training_fact_admission_id)
            if self._contract_only
            else self.admission_repository._load(
                session, pin.training_fact_admission_id
            )
        )
        if (
            TrainingAdmissionPinV1.from_admission(value) != pin
            or pin.persisted_at_utc > at
        ):
            raise ValueError("stored source admission differs from exact pilot pin")
        value.source_rights_admission.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
        return value

    def complete_attempt(
        self,
        attempt: QuantIntegrityAttemptV1,
        *,
        output: QuantIntegrityOutputV1 | None,
        report: QuantIntegrityReportV1 | None,
    ) -> None:
        attempt = revalidate_integrity_model(attempt)
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            existing = session.scalar(
                select(QuantIntegrityAttemptRecord).where(
                    QuantIntegrityAttemptRecord.reservation_id
                    == attempt.content_payload.reservation.artifact_id
                )
            )
            if existing is not None:
                _same_artifact(existing, attempt)
                if output is not None:
                    _same_artifact(
                        _required(
                            session, QuantIntegrityOutputRecord, existing.output_id
                        ),
                        output,
                    )
                if report is not None:
                    _same_artifact(
                        _required(
                            session, QuantIntegrityReportRecord, existing.report_id
                        ),
                        report,
                    )
                if (output is None) != (existing.output_id is None) or (
                    report is None
                ) != (existing.report_id is None):
                    raise ValueError("completion retry omitted or added artifacts")
                return
            at = self._operation_time(
                attempt.content_payload.actual_completed_at_utc, recent=True
            )
            self._complete(session, attempt, output, report, at)

    def _complete(self, session, attempt, output, report, at):
        content = attempt.content_payload
        reservation = content.reservation
        reserved = reservation.content_payload
        _same_artifact(
            _required(
                session, QuantIntegrityReservationRecord, reservation.artifact_id
            ),
            reservation,
        )
        self._owner(session, reserved.integrity_pilot_series_id)
        self._assert_open(session, reserved.integrity_pilot_series_id)
        previous = self._attempts(session, reserved.integrity_pilot_series_id)
        if reserved.prior_attempt_count != len(
            previous
        ) or reserved.prior_attempt_root != integrity_attempt_root(previous):
            raise ValueError("completion cannot change or hide preceding attempts")
        plan = _referenced(
            session, QuantIntegrityPlanRecord, QuantIntegrityPlanV1, reserved.plan_ref
        )
        if content.status == "COMPLETED":
            if output is None or report is None:
                raise ValueError("successful completion requires output and report")
            output, report = (
                revalidate_integrity_model(output),
                revalidate_integrity_model(report),
            )
            if content.output_ref != IntegrityArtifactRefV1.of(
                output
            ) or content.report_ref != IntegrityArtifactRefV1.of(report):
                raise ValueError("completion output/report hash mismatch")
            self._verify_execution(session, plan, output, report, at)
            self._append(
                session, QuantIntegrityOutputRecord, output, plan_id=plan.artifact_id
            )
            self._append(
                session,
                QuantIntegrityReportRecord,
                report,
                plan_id=plan.artifact_id,
                output_id=output.artifact_id,
            )
        elif output is not None or report is not None:
            raise ValueError("failed completion cannot carry successful artifacts")
        self._append(
            session,
            QuantIntegrityAttemptRecord,
            attempt,
            series_id=reserved.integrity_pilot_series_id,
            sequence=reserved.sequence,
            plan_id=plan.artifact_id,
            reservation_id=reservation.artifact_id,
            status=content.status,
            actual_completed_at_utc=content.actual_completed_at_utc,
            output_id=None if output is None else output.artifact_id,
            report_id=None if report is None else report.artifact_id,
        )

    def recover_pending_attempt(self, reservation_id: str) -> QuantIntegrityAttemptV1:
        """Operator-triggered recovery, never automatic deletion or silent retry.

        The caller must establish the worker is stopped. Races are serialized:
        whichever completion commits first wins; the other cannot overwrite it.
        """
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            reservation = _artifact(
                _required(session, QuantIntegrityReservationRecord, reservation_id),
                QuantIntegrityAttemptReservationV1,
            )
            existing = session.scalar(
                select(QuantIntegrityAttemptRecord).where(
                    QuantIntegrityAttemptRecord.reservation_id == reservation_id
                )
            )
            if existing is not None:
                return _artifact(existing, QuantIntegrityAttemptV1)
            at = self._now()
            attempt = QuantIntegrityAttemptV1.freeze(
                content_payload=QuantIntegrityAttemptContentV1(
                    reservation=reservation,
                    actual_completed_at_utc=at,
                    status="FAILED",
                    failure=QuantIntegrityFailureV1(
                        exception_type="InterruptedPilotAttempt",
                        reason="OPERATOR_CONFIRMED_INTERRUPTED_WORKER; retained crash recovery",
                    ),
                )
            )
            self._complete(session, attempt, None, None, at)
            return attempt

    def list_attempts(
        self, integrity_pilot_series_id: str
    ) -> tuple[QuantIntegrityAttemptV1, ...]:
        with self._sessions.begin() as session:
            return self._attempts(session, integrity_pilot_series_id)

    def _attempts(
        self,
        session,
        series_id,
        *,
        replay_cache=None,
        artifact_cache=None,
        recheck_current_research=False,
    ):
        rows = tuple(
            session.scalars(
                select(QuantIntegrityAttemptRecord)
                .where(QuantIntegrityAttemptRecord.series_id == series_id)
                .order_by(QuantIntegrityAttemptRecord.sequence)
                .execution_options(populate_existing=True)
            )
        )
        values = tuple(
            _artifact(row, QuantIntegrityAttemptV1, cache=artifact_cache)
            for row in rows
        )
        for value in values:
            content = value.content_payload
            reservation = content.reservation
            _same_artifact(
                _required(
                    session, QuantIntegrityReservationRecord, reservation.artifact_id
                ),
                reservation,
            )
            reserved = reservation.content_payload
            plan = _referenced(
                session,
                QuantIntegrityPlanRecord,
                QuantIntegrityPlanV1,
                reserved.plan_ref,
                cache=artifact_cache,
            )
            if (
                plan.content_payload.definition.integrity_pilot_series_id != series_id
                or plan.content_payload.definition.scope_hash != reserved.scope_hash
            ):
                raise ValueError("attempt belongs to a different persisted plan scope")
            if content.status == "COMPLETED":
                report = _referenced(
                    session,
                    QuantIntegrityReportRecord,
                    QuantIntegrityReportV1,
                    content.report_ref,
                    cache=artifact_cache,
                )
                output = _referenced(
                    session,
                    QuantIntegrityOutputRecord,
                    QuantIntegrityOutputV1,
                    content.output_ref,
                    cache=artifact_cache,
                )
                if (
                    report.content_payload.plan_ref != reserved.plan_ref
                    or output.content_payload.plan_ref != reserved.plan_ref
                    or report.content_payload.output_ref != content.output_ref
                ):
                    raise ValueError(
                        "historical attempt report/output/plan lineage mismatch"
                    )
                if replay_cache is not None:
                    self._verify_execution(
                        session,
                        plan,
                        output,
                        report,
                        content.actual_completed_at_utc,
                        recheck_current_research=recheck_current_research,
                        replay_cache=replay_cache,
                    )
        if values:
            series = _required(session, QuantIntegritySeriesRecord, series_id)
            _verify_row(series)
            QuantIntegritySummaryContentV1(
                integrity_pilot_series_id=series_id,
                scope_hash=series.scope_hash,
                attempts=values,
                attempt_count=len(values),
                attempt_root=integrity_attempt_root(values),
                generated_at_utc=max(row.persisted_at_utc for row in rows),
            )
        return values

    def load_report(self, report_ref: IntegrityArtifactRefV1) -> QuantIntegrityReportV1:
        with self._sessions.begin() as session:
            return _referenced(
                session, QuantIntegrityReportRecord, QuantIntegrityReportV1, report_ref
            )

    def seal_terminal_attestation(
        self, summary: QuantIntegritySummaryV1, attestation: QuantIntegrityAttestationV1
    ) -> None:
        summary, attestation = (
            revalidate_integrity_model(summary),
            revalidate_integrity_model(attestation),
        )
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            existing = session.get(
                QuantIntegrityAttestationRecord, attestation.artifact_id
            )
            if existing is not None:
                _same_artifact(existing, attestation)
                _same_artifact(
                    _required(
                        session, QuantIntegritySummaryRecord, existing.summary_id
                    ),
                    summary,
                )
                self._terminal_graph(session, existing, self._now())
                return
            at = self._operation_time(
                attestation.content_payload.attested_at_utc, recent=True
            )
            content = attestation.content_payload
            self._owner(session, content.integrity_pilot_series_id)
            self._assert_open(session, content.integrity_pilot_series_id)
            self._check_terminal(session, summary, attestation, at)
            self._append(
                session,
                QuantIntegritySummaryRecord,
                summary,
                series_id=content.integrity_pilot_series_id,
                attempt_count=content.attempt_count,
                attempt_root=content.attempt_root,
                generated_at_utc=summary.content_payload.generated_at_utc,
            )
            self._append(
                session,
                QuantIntegrityAttestationRecord,
                attestation,
                series_id=content.integrity_pilot_series_id,
                attempt_count=content.attempt_count,
                attempt_root=content.attempt_root,
                summary_id=summary.artifact_id,
                plan_id=content.plan_ref.artifact_id,
                report_id=content.report_ref.artifact_id,
                evidence_use=content.provenance.evidence_use.value,
                attested_at_utc=content.attested_at_utc,
            )

    def _check_terminal(
        self, session, summary, attestation, at, *, historical_read=False
    ):
        content = attestation.content_payload
        # Never keep authorization or validation results in session.info/self.
        # Byte-identical artifacts and deterministic calculations may be reused
        # only inside this one verifying operation/transaction.
        replay_cache, artifact_cache = {}, {}
        pins = ()
        if not historical_read:
            plans = self._series_plans(
                session, content.integrity_pilot_series_id, cache=artifact_cache
            )
            pins = tuple(
                pin
                for plan in plans
                for pin in plan.content_payload.definition.admissions
            )
            # Include even unattempted/failed plans. No earlier plan's source may
            # be opened before CURRENT rights for the whole series are established.
            self._current_research_rights(pins, at, self._now(), session=session)
        attempts = self._attempts(
            session,
            content.integrity_pilot_series_id,
            replay_cache=replay_cache,
            artifact_cache=artifact_cache,
            recheck_current_research=not historical_read,
        )
        reservations = session.scalar(
            select(func.count())
            .select_from(QuantIntegrityReservationRecord)
            .where(
                QuantIntegrityReservationRecord.series_id
                == content.integrity_pilot_series_id
            )
        )
        if (
            not attempts
            or reservations != len(attempts)
            or summary.content_payload.attempts != attempts
            or IntegrityArtifactRefV1.of(summary) != content.summary_ref
            or summary.content_payload.integrity_pilot_series_id
            != content.integrity_pilot_series_id
            or content.attempt_count != len(attempts)
            or content.attempt_root != integrity_attempt_root(attempts)
            or summary.content_payload.generated_at_utc > content.attested_at_utc
        ):
            raise ValueError(
                "terminal seal must include every completed reservation without races"
            )
        last = attempts[-1].content_payload
        if (
            last.status != "COMPLETED"
            or last.reservation.content_payload.plan_ref != content.plan_ref
            or last.report_ref != content.report_ref
        ):
            raise ValueError(
                "terminal seal must use the last successful attempt, not a selected favorable attempt"
            )
        plan = _referenced(
            session,
            QuantIntegrityPlanRecord,
            QuantIntegrityPlanV1,
            content.plan_ref,
            cache=artifact_cache,
        )
        report = _referenced(
            session,
            QuantIntegrityReportRecord,
            QuantIntegrityReportV1,
            content.report_ref,
            cache=artifact_cache,
        )
        output = _referenced(
            session,
            QuantIntegrityOutputRecord,
            QuantIntegrityOutputV1,
            last.output_ref,
            cache=artifact_cache,
        )
        definition = plan.content_payload.definition
        if (
            content.scope_hash != definition.scope_hash
            or summary.content_payload.scope_hash != definition.scope_hash
            or content.input_roots != plan.content_payload.input_roots
            or content.terminal_state_core_ref
            != report.content_payload.terminal_state_core_ref
            or content.provenance != definition.provenance
            or content.build_recipe != definition.build_recipe
            or content.implementation_code_revision
            != definition.implementation_code_revision
        ):
            raise ValueError(
                "terminal plan/source/core/recipe/revision binding mismatch"
            )
        if not historical_read:
            # Each completed output was already reverified, with current read
            # guards. Finish with rights checks, not another metadata/math replay.
            self._current_research_rights(pins, at, self._now(), session=session)
        return plan, output, report, attempts

    def _verify_execution(
        self,
        session,
        plan,
        output,
        report,
        at,
        *,
        recheck_current_research=True,
        replay_cache=None,
    ):
        definition = plan.content_payload.definition
        if recheck_current_research:
            self._current_research_rights(
                definition.admissions, at, self._now(), session=session
            )
        self._metadata(session, definition, at)
        admissions = tuple(
            self._read_operational_admission(
                pin, at, self._operation_time(at), session=session
            )
            if recheck_current_research
            and not isinstance(definition, QuantIntegrityPlanDefinitionV2)
            else self._load_admission(pin, at, session=session)
            for pin in definition.admissions
        )
        context = None
        if isinstance(definition, QuantIntegrityPlanDefinitionV2):
            context = correction_context_in_session(
                self.admission_repository, session, definition.correction_context, at
            )
        else:
            assert_complete_correction_pins(session, base_context_pin(admissions), at)
        # Evidence bytes, source graphs, pins and time-specific rights above are
        # ALWAYS reverified. Cache only math whose inputs were just revalidated;
        # output/report business times are frozen by the plan, not this read time.
        key = (
            plan.content_hash,
            tuple(a.admission_hash for a in admissions),
            None
            if context is None
            else tagged_canonical_sha256(
                "VERIFIED_VERSIONED_REPLAY_V2", context.versions
            ),
        )
        calculated = None if replay_cache is None else replay_cache.get(key)
        if calculated is None:
            replay_service = QuantIntegrityPilotService(
                _PinnedReplayReader(admissions, context), self._now
            )
            expected = replay_service._execute(plan, at_utc=at)
            calculated = expected, replay_service._report(plan, expected, expected)
        if calculated != (output, report):
            raise ValueError(
                "persisted pilot output/report does not equal exact admitted deterministic replay"
            )
        if replay_cache is not None:
            replay_cache[key] = calculated
        completed = self._now()
        if recheck_current_research:
            for admission in admissions:
                admission.source_rights_admission.assert_active_for(
                    completed, TRAINING_FACT_REQUIRED_USES
                )
            assert_complete_correction_pins(
                session,
                definition.correction_context
                if context is not None
                else base_context_pin(admissions),
                completed,
            )
        self._recipe(definition)

    def _terminal_graph(self, session, row, at):
        attestation = _artifact(row, QuantIntegrityAttestationV1)
        summary = _referenced(
            session,
            QuantIntegritySummaryRecord,
            QuantIntegritySummaryV1,
            attestation.content_payload.summary_ref,
        )
        graph = self._check_terminal(
            session, summary, attestation, at, historical_read=True
        )
        return attestation, summary, *graph

    def technical_evidence(
        self,
        attestation_id: str,
        history: TrainingHistoryGraphV1,
        *,
        session: Session | None = None,
    ) -> TechnicalEvidenceRefsV1:
        """Verified bridge; terminal_state_core_hash references the PILOT core schema.

        Its hash is not renamed RELEASED_STATE_CORE_V1. Exact facts/math are compared
        to the release projection, and source/season/approved roots are recomputed
        by TrainingHistoryGraphV1, not substituted for unlike pilot admission roots.
        No synthetic path (including an injected adapter) can return this object.
        Research rights are reverified at the recorded pilot operation, not used
        as a substitute for the caller's independent current production grants.
        """
        if session is not None and (
            not session.in_transaction()
            or session.get_bind() is not self._sessions.kw["bind"]
        ):
            raise ValueError(
                "technical evidence requires an active same-database caller transaction"
            )
        own_session = session is None
        with self._sessions.begin() if own_session else nullcontext(session) as session:
            if own_session:
                session.execute(text("BEGIN"))
            row = _required(session, QuantIntegrityAttestationRecord, attestation_id)
            attestation = _artifact(row, QuantIntegrityAttestationV1)
            if (
                self._contract_only
                or attestation.content_payload.provenance.evidence_use
                != IntegrityEvidenceUse.REAL_SOURCE
            ):
                raise ValueError(
                    "SYNTHETIC_CONTRACT_ONLY is never technical production evidence"
                )
            at = self._now()
            attestation, summary, plan, output, report, attempts = self._terminal_graph(
                session, row, at
            )
            history = revalidate_integrity_model(history)
            self._bridge_history(plan, output, history)
            definition = plan.content_payload.definition
            self._recipe(definition)
            return TechnicalEvidenceRefsV1(
                integrity_pilot_scope_id=history.integrity_pilot_scope_id,
                integrity_pilot_series_id=definition.integrity_pilot_series_id,
                scope=history.scope,
                plan=_release_ref(plan),
                summary=_release_ref(summary),
                attestation=_release_ref(attestation),
                report=_release_ref(report),
                attempt_count=len(attempts),
                attempt_root=integrity_attempt_root(attempts),
                source_root=history.source_root,
                season_root=history.season_root,
                approved_facts_hash=history.approved_facts_hash,
                training_data_hash=history.training_data_hash,
                terminal_state_core_hash=output.content_payload.terminal_state_core.content_hash,
                build_recipe=ReleaseArtifactRefV1(
                    artifact_id=definition.build_recipe.recipe_id,
                    content_hash=definition.build_recipe.recipe_hash,
                ),
                code_revision=definition.implementation_code_revision,
                plan_sealed_at_utc=_required(
                    session, QuantIntegrityPlanRecord, plan.artifact_id
                ).persisted_at_utc,
                actual_started_at_utc=attempts[
                    -1
                ].content_payload.reservation.content_payload.actual_started_at_utc,
                actual_completed_at_utc=attempts[
                    -1
                ].content_payload.actual_completed_at_utc,
                attestation_persisted_at_utc=row.persisted_at_utc,
            )

    @staticmethod
    def _bridge_history(plan, output, history):
        history = revalidate_integrity_model(history)
        definition = plan.content_payload.definition
        core = output.content_payload.terminal_state_core.content_payload
        versioned = isinstance(history, TrainingHistoryGraphV2)
        if versioned != isinstance(definition, QuantIntegrityPlanDefinitionV2):
            raise ValueError("pilot/release history schema mismatch")
        if versioned and (
            history.context_pin != definition.correction_context
            or history.correction_context.actual_at_utc
            != definition.context_registered_at_utc
            or history.context_pin != core.context_pin
            or history.selected_heads != core.selected_heads
            or history.selected_versions_root != core.selected_versions_root
            or history.selection_cutoff_at_utc
            != definition.terminal_projection.training_cutoff_at_utc
            or history.exclude_match_ids
            != definition.terminal_projection.exclude_match_ids
        ):
            raise ValueError(
                "pilot bridge requires exact context and selected version roots"
            )
        if (
            history.integrity_pilot_scope_id != definition.integrity_pilot_scope_id
            or history.training_window != definition.training_window
            or tuple(
                TrainingAdmissionPinV1.from_admission(a) for a in history.admissions
            )
            != definition.admissions
            or tuple(
                VersionedFactRefV2.of(f.content_payload.version)
                if versioned
                else AdmittedFactRefV1.of(f.content_payload.binding)
                for f in history.facts
            )
            != core.admitted_fact_refs
            or tuple(f.content_payload.elo_fact for f in history.facts)
            != core.training_facts
            or history.training_data_hash != core.training_data_hash
            or history.scope.production_target_season_id
            != core.production_target_season_id
            or history.scope.training_window_hash != core.training_window_hash
        ):
            raise ValueError(
                "release evidence requires the FULL exact pilot terminal fact/admission/window graph"
            )
        state = replay_exact_facts(
            tuple(f.content_payload.elo_fact for f in history.facts),
            core.training_cutoff_at_utc,
            history.scope.production_target_season_id,
        )
        release_projection = ReleasedStateCoreContentV1.from_state(
            state=state,
            training_cutoff_at_utc=core.training_cutoff_at_utc,
            approved_facts_hash=history.approved_facts_hash,
        )
        if (
            release_projection.teams != core.teams
            or release_projection.training_data_hash != core.training_data_hash
        ):
            raise ValueError(
                "pilot/release core ratings, prior counts or training hash differ"
            )

    def _append(self, session, model, artifact, **projections):
        existing = session.get(model, artifact.artifact_id)
        if existing is not None:
            _same_artifact(existing, artifact)
            if any(
                getattr(existing, key) != value for key, value in projections.items()
            ):
                raise ValueError("immutable pilot relational projection mismatch")
            return existing
        row = _row(
            model,
            artifact_id=artifact.artifact_id,
            content_hash=artifact.content_hash,
            artifact_json=canonical_json(artifact),
            operator_id=self.operator_id,
            persisted_at_utc=self._now(),
            **projections,
        )
        session.add(row)
        session.flush()
        return row

    def _owner(self, session, series_id):
        series = _required(session, QuantIntegritySeriesRecord, series_id)
        _verify_row(series)
        if series.operator_id != self.operator_id:
            raise ValueError("pilot operation belongs to a different operator")

    @staticmethod
    def _assert_open(session, series_id):
        if (
            session.scalar(
                select(QuantIntegritySummaryRecord.artifact_id).where(
                    QuantIntegritySummaryRecord.series_id == series_id
                )
            )
            is not None
        ):
            raise ValueError("terminal pilot series is closed")


def _source_time(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("source timestamp must be an explicit UTC ISO-8601 string")
    parsed = datetime.fromisoformat(value)
    return normalize_utc(parsed)


def _required(session, model, key):
    row = session.get(model, key, populate_existing=True)
    if row is None:
        raise ValueError(f"missing persisted {model.__table__.name}: {key}")
    return row


def _row(model, **values):
    return model(
        **values,
        row_sha256=tagged_canonical_sha256(
            f"QUANT_INTEGRITY_SQL_ROW_V1:{model.__table__.name}", values
        ),
    )


def _verify_row(row):
    values = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "row_sha256"
    }
    if (
        tagged_canonical_sha256(
            f"QUANT_INTEGRITY_SQL_ROW_V1:{row.__table__.name}", values
        )
        != row.row_sha256
    ):
        raise ValueError("stored pilot row integrity mismatch")


def _artifact(row, model, *, cache=None):
    _verify_row(row)
    key = (model, row.artifact_json)
    artifact = None if cache is None else cache.get(key)
    if artifact is None:
        artifact = model.model_validate(
            strict_json_bytes(row.artifact_json.encode("utf-8"))
        )
    if (
        artifact.artifact_id != row.artifact_id
        or artifact.content_hash != row.content_hash
        or canonical_json(artifact) != row.artifact_json
    ):
        raise ValueError("stored pilot canonical artifact mismatch")
    content = artifact.content_payload
    expected = {}
    if isinstance(artifact, QuantIntegrityPlanV1):
        expected = dict(
            series_id=content.definition.integrity_pilot_series_id,
            scope_hash=content.definition.scope_hash,
            evidence_use=content.definition.provenance.evidence_use.value,
            sealed_at_utc=content.sealed_at_utc,
        )
    elif isinstance(artifact, QuantIntegrityAttemptReservationV1):
        expected = dict(
            series_id=content.integrity_pilot_series_id,
            sequence=content.sequence,
            plan_id=content.plan_ref.artifact_id,
            actual_started_at_utc=content.actual_started_at_utc,
        )
    elif isinstance(artifact, QuantIntegrityOutputV1):
        expected = dict(plan_id=content.plan_ref.artifact_id)
    elif isinstance(artifact, QuantIntegrityReportV1):
        expected = dict(
            plan_id=content.plan_ref.artifact_id,
            output_id=content.output_ref.artifact_id,
        )
    elif isinstance(artifact, QuantIntegrityAttemptV1):
        reservation = content.reservation.content_payload
        expected = dict(
            series_id=reservation.integrity_pilot_series_id,
            sequence=reservation.sequence,
            reservation_id=content.reservation.artifact_id,
            plan_id=reservation.plan_ref.artifact_id,
            actual_completed_at_utc=content.actual_completed_at_utc,
            status=content.status,
            output_id=None
            if content.output_ref is None
            else content.output_ref.artifact_id,
            report_id=None
            if content.report_ref is None
            else content.report_ref.artifact_id,
        )
    elif isinstance(artifact, QuantIntegritySummaryV1):
        expected = dict(
            series_id=content.integrity_pilot_series_id,
            attempt_count=content.attempt_count,
            attempt_root=content.attempt_root,
            generated_at_utc=content.generated_at_utc,
        )
    elif isinstance(artifact, QuantIntegrityAttestationV1):
        expected = dict(
            series_id=content.integrity_pilot_series_id,
            attempt_count=content.attempt_count,
            attempt_root=content.attempt_root,
            summary_id=content.summary_ref.artifact_id,
            plan_id=content.plan_ref.artifact_id,
            report_id=content.report_ref.artifact_id,
            evidence_use=content.provenance.evidence_use.value,
            attested_at_utc=content.attested_at_utc,
        )
    if any(getattr(row, key) != value for key, value in expected.items()):
        raise ValueError("stored pilot typed relational projection mismatch")
    if any(
        value > row.persisted_at_utc
        for value in expected.values()
        if isinstance(value, datetime)
    ):
        raise ValueError("stored pilot actual event follows persistence")
    if cache is not None:
        cache[key] = artifact
    return artifact


def _same_artifact(row, artifact):
    artifact = revalidate_integrity_model(artifact)
    if _artifact(row, type(artifact)) != artifact:
        raise ValueError(
            "immutable completion/plan retry conflicts with stored content"
        )


def _referenced(session, row_model, artifact_model, reference, *, cache=None):
    reference = revalidate_integrity_model(reference)
    artifact = _artifact(
        _required(session, row_model, reference.artifact_id),
        artifact_model,
        cache=cache,
    )
    if (artifact.schema_version, artifact.artifact_id, artifact.content_hash) != (
        reference.schema_version,
        reference.artifact_id,
        reference.content_hash,
    ):
        raise ValueError("stored pilot reference hash/schema mismatch")
    return artifact


def _release_ref(artifact: IntegrityArtifact) -> ReleaseArtifactRefV1:
    return ReleaseArtifactRefV1(
        artifact_id=artifact.artifact_id, content_hash=artifact.content_hash
    )
