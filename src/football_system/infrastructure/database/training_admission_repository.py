"""First ADR-0008 persistence lane, using Checkpoint A and existing results.

API sequence: record(rights + authorized local attestation), capture_local_json
for each raw file, then admit(submissions with registered receipts and separate
local reviewer documents). load/load_rights/load_capture always reverify bytes
and complete relational projections. This does NOT grant production use.

Identity prerequisites: Provider, Match, CanonicalMatchIdentity, Team,
Competition, ProviderMatchMapping, and explicit provider team aliases and
competition mapping must already exist. No placeholder identities are created.
Result writes reuse the historical repository's session-scoped helper.

Capture times are actual LOCAL_FILE_IMPORT operations, never file mtime or
upstream acquisition/publication. Earlier acquisition is unknown in this lane.
Keep the evidence root and database under trusted local access control. SQLite
hashes/guards are integrity checks, not protection against a database owner who
can replace all data and trusted configuration. V1 writes still refuse corrections.
Historical reads tolerate only successors verified by the separate controlled
correction repository; V1 artifacts and normalized originals remain unchanged.

Operation times mark preparation/sealing/write events inside a transaction,
not the later SQLite commit acknowledgement. A fresh current-time grant check
after all writes and readback is the last gate before leaving a new transaction.
Exact retries are audit reads of the original request, not new authorization.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import model_validator
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.training_admission import (
    PrepareTrainingFactBindingsService,
)
from football_system.domain.archive import canonical_json
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
    utc_now,
)
from football_system.domain.training_admission import (
    SOURCE_RIGHTS_PAYLOAD_V1,
    TRAINING_FACT_REQUIRED_USES,
    LocalReviewEvidenceV1,
    LocalReviewerAttestationV1,
    Reference,
    Sha256Digest,
    SourceRightsAdmissionV1,
    SourceRightsPayloadV1,
    TrainingFactAdmissionV1,
    TrainingProviderMatchMappingV1,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database.historical_repositories import (
    _append_match_result,
    _match_result,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    MatchResultAdmissionRecord,
    MatchResultRecord,
    MatchSeasonMembershipRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    SourceRightsAdmissionRecord,
    TeamRecord,
    TrainingCaptureReceiptRecord,
    TrainingFactAdmissionRecord,
    TrainingFactBindingRecord,
    TrainingFactFixtureSourceRecord,
)
from football_system.infrastructure.files.training_evidence import (
    LocalTrainingEvidence,
    TrainingFactSubmissionV1,
    strict_json_bytes,
    sanitized_evidence_errors,
    training_review_input_sha256,
)


class TrainingCaptureReceiptV1(DomainModel):
    schema_version: Literal["TRAINING_CAPTURE_RECEIPT_V1"] = (
        "TRAINING_CAPTURE_RECEIPT_V1"
    )
    capture_receipt_id: Identifier
    receipt_hash: Sha256Digest
    request_sha256: Sha256Digest
    source_rights_admission_id: Identifier
    source_id: Identifier
    provider_code: Identifier
    evidence_reference: Reference
    payload_sha256: Sha256Digest
    capture_kind: Literal["LOCAL_FILE_IMPORT"] = "LOCAL_FILE_IMPORT"
    upstream_acquired_at_utc: None = None
    local_imported_at_utc: UtcDateTime
    archive_created_at_utc: UtcDateTime
    registered_at_utc: UtcDateTime
    imported_by: Identifier

    @classmethod
    def freeze(cls, **values: object) -> TrainingCaptureReceiptV1:
        content = {
            "schema_version": "TRAINING_CAPTURE_RECEIPT_V1",
            "capture_kind": "LOCAL_FILE_IMPORT",
            "upstream_acquired_at_utc": None,
            **values,
        }
        digest = tagged_canonical_sha256("TRAINING_CAPTURE_RECEIPT_V1", content)
        return cls(
            **content,
            capture_receipt_id=stable_id("TRAINING_CAPTURE_RECEIPT_V1", digest),
            receipt_hash=digest,
        )

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        content = self.model_dump(
            mode="python", exclude={"capture_receipt_id", "receipt_hash"}
        )
        digest = tagged_canonical_sha256(self.schema_version, content)
        if self.receipt_hash != digest or self.capture_receipt_id != stable_id(
            self.schema_version, digest
        ):
            raise ValueError("capture receipt seal mismatch")
        if (
            not self.local_imported_at_utc
            <= self.archive_created_at_utc
            <= self.registered_at_utc
        ):
            raise ValueError("capture receipt timeline mismatch")
        return self


class ControlledTrainingCorrectionRequired(ValueError):
    """No controlled predecessor/correction registration exists in this lane."""


class SqlAlchemyTrainingAdmissionRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        evidence: LocalTrainingEvidence,
        operator_id: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        _identifier(operator_id)
        self._sessions = session_factory
        self.evidence = evidence
        self.operator_id = operator_id
        self._clock = clock

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("operation clock must return aware UTC")
        return value

    @sanitized_evidence_errors
    def record(
        self,
        *,
        request_key: str,
        rights_payload: SourceRightsPayloadV1,
        reviewer_attestation: LocalReviewerAttestationV1,
    ) -> SourceRightsAdmissionV1:
        """Record rights only after reading terms, pinned authority and review bytes.

        The attestation's local review must be TRAINING_LOCAL_REVIEW_V1 binding
        SOURCE_RIGHTS_PAYLOAD_V1. recorded_at_utc is obtained here, not from a caller.
        """
        rights = SourceRightsPayloadV1.model_validate(
            rights_payload.model_dump(mode="python", warnings=False)
        )
        attestation = LocalReviewerAttestationV1.model_validate(
            reviewer_attestation.model_dump(mode="python", warnings=False)
        )
        request = _request(
            request_key,
            self.operator_id,
            rights_payload=rights,
            reviewer_attestation=attestation,
        )
        with self._sessions.begin() as session:
            _lock(session)
            previous = _prior(session, SourceRightsAdmissionRecord, request)
            if previous is not None:
                return self._rights(session, previous.source_rights_admission_id)
            started = self._now()
            self._verify_rights_evidence(rights, attestation, self.operator_id, started)
            at = self._now()
            if at < started:
                raise ValueError("rights operation clock moved backwards")
            value = SourceRightsAdmissionV1.from_recorded(
                rights_payload=rights,
                reviewer_attestation=attestation,
                recorded_at_utc=at,
            )
            row = _row(
                SourceRightsAdmissionRecord,
                **request,
                source_rights_admission_id=value.source_rights_admission_id,
                admission_hash=value.admission_hash,
                recorded_at_utc=at,
                artifact_json=canonical_json(value),
            )
            session.add(row)
            session.flush()
            stored = self._rights(session, value.source_rights_admission_id)
            self._check_current_authorization(stored, not_before=at, recording=True)
            return stored

    @sanitized_evidence_errors
    def load_rights(self, admission_id: str) -> SourceRightsAdmissionV1:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            return self._rights(session, admission_id)

    def _check_current_authorization(
        self,
        rights: SourceRightsAdmissionV1,
        *,
        not_before: datetime,
        authorities: Iterable[LocalReviewEvidenceV1] = (),
        recording: bool = False,
    ) -> None:
        attestation = rights.content_payload.reviewer_attestation.content_payload
        references = (
            LocalReviewEvidenceV1(
                evidence_reference=attestation.reviewer_authority_reference,
                evidence_sha256=attestation.authority_sha256,
            ),
            *authorities,
        )
        permissions = [
            self.evidence.load_authority(ref) for ref in dict.fromkeys(references)
        ]
        # All I/O, including authority rereads, precedes this final clock sample.
        current = self._now()
        if current < not_before:
            raise ValueError("operation clock moved backwards before transaction exit")
        if recording:
            # Recording future-effective rights is allowed; acquiring data is not.
            if current >= rights.content_payload.rights_payload.expires_at_utc:
                raise ValueError("source rights expired before transaction exit")
        else:
            rights.assert_active_for(current, TRAINING_FACT_REQUIRED_USES)
        for permission in permissions:
            permission.assert_active_for(current)

    def _verify_rights_evidence(self, rights, attestation, operator, at):
        self.evidence.read(rights.terms_reference, rights.terms_sha256)
        content = attestation.content_payload
        review = self.evidence.review(
            evidence=content.evidence,
            authority=LocalReviewEvidenceV1(
                evidence_reference=content.reviewer_authority_reference,
                evidence_sha256=content.authority_sha256,
            ),
            schema=SOURCE_RIGHTS_PAYLOAD_V1,
            digest=tagged_canonical_sha256(SOURCE_RIGHTS_PAYLOAD_V1, rights),
            source_ids=rights.source_ids,
            operator_id=operator,
            at_utc=at,
        )
        if (review.authorized_reviewer, review.reviewed_at_utc) != (
            content.authorized_reviewer,
            content.reviewed_at_utc,
        ):
            raise ValueError("rights attestation differs from local reviewer bytes")

    def _rights(self, session, admission_id):
        row = _required(session, SourceRightsAdmissionRecord, admission_id)
        _verify_row(row)
        value = SourceRightsAdmissionV1.model_validate_json(row.artifact_json)
        request = _request(
            row.request_key,
            row.operator_id,
            rights_payload=value.content_payload.rights_payload,
            reviewer_attestation=value.content_payload.reviewer_attestation,
        )
        _assert_row(
            row,
            _row(
                SourceRightsAdmissionRecord,
                **request,
                source_rights_admission_id=value.source_rights_admission_id,
                admission_hash=value.admission_hash,
                recorded_at_utc=value.content_payload.recorded_at_utc,
                artifact_json=canonical_json(value),
            ),
        )
        self._verify_rights_evidence(
            value.content_payload.rights_payload,
            value.content_payload.reviewer_attestation,
            row.operator_id,
            value.content_payload.recorded_at_utc,
        )
        return value

    @sanitized_evidence_errors
    def capture_local_json(
        self,
        *,
        request_key: str,
        source_rights_admission_id: str,
        source_id: str,
        provider_code: str,
        evidence_reference: str,
    ) -> TrainingCaptureReceiptV1:
        """Import actual local JSON bytes into an immutable SQLite receipt.

        Call only after source rights recording. This makes no claim about when the
        file was acquired upstream. Use the returned receipt ID as raw/archive ID and
        its three actual times in Checkpoint A candidates. A fixture record ID is its
        full JSON pointer (use '/' only for an actual empty-key field, not root).
        For a root object use the literal fixture_source_record_id='ROOT'.
        """
        _identifier(source_id)
        _identifier(provider_code)
        _identifier(request_key)
        with self._sessions.begin() as session:
            _lock(session)
            prior = session.scalar(
                select(TrainingCaptureReceiptRecord).where(
                    TrainingCaptureReceiptRecord.request_key == request_key
                )
            )
            metadata = dict(
                source_rights_admission_id=source_rights_admission_id,
                source_id=source_id,
                provider_code=provider_code,
                evidence_reference=evidence_reference,
            )
            if prior is not None:
                _verify_row(prior)
                expected = _request(
                    request_key,
                    self.operator_id,
                    **metadata,
                    payload_sha256=prior.payload_sha256,
                )
                if any(getattr(prior, key) != value for key, value in expected.items()):
                    raise ValueError(
                        "immutable retry request conflicts with stored content"
                    )
                # Only the original registered reference can be reread after expiry.
                return self._capture(session, prior.capture_receipt_id)[0]
            rights = self._rights(session, source_rights_admission_id)
            if source_id not in rights.content_payload.rights_payload.source_ids:
                raise ValueError("capture source is outside rights scope")
            started = self._now()
            rights.assert_active_for(started, TRAINING_FACT_REQUIRED_USES)
            payload = self.evidence.read(evidence_reference)
            strict_json_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            request = _request(
                request_key,
                self.operator_id,
                source_rights_admission_id=source_rights_admission_id,
                source_id=source_id,
                provider_code=provider_code,
                evidence_reference=evidence_reference,
                payload_sha256=digest,
            )
            imported, created, registered = self._now(), self._now(), self._now()
            if not started <= imported <= created <= registered:
                raise ValueError("capture operation clock moved backwards")
            for at in (imported, created, registered):
                rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
            value = TrainingCaptureReceiptV1.freeze(
                request_sha256=request["request_sha256"],
                source_rights_admission_id=source_rights_admission_id,
                source_id=source_id,
                provider_code=provider_code,
                evidence_reference=evidence_reference,
                payload_sha256=digest,
                local_imported_at_utc=imported,
                archive_created_at_utc=created,
                registered_at_utc=registered,
                imported_by=self.operator_id,
            )
            provider = session.scalar(
                select(ProviderRecord).where(ProviderRecord.code == provider_code)
            )
            if provider is None:
                raise ValueError("capture requires a preexisting provider")
            session.add(
                _row(
                    TrainingCaptureReceiptRecord,
                    **request,
                    capture_receipt_id=value.capture_receipt_id,
                    source_rights_admission_id=source_rights_admission_id,
                    source_id=source_id,
                    provider_id=provider.provider_id,
                    payload_sha256=digest,
                    payload_bytes=payload,
                    local_imported_at_utc=imported,
                    archive_created_at_utc=created,
                    registered_at_utc=registered,
                    artifact_json=canonical_json(value),
                )
            )
            session.flush()
            stored = self._capture(session, value.capture_receipt_id)[0]
            self._check_current_authorization(rights, not_before=registered)
            return stored

    @sanitized_evidence_errors
    def load_capture(self, receipt_id: str) -> tuple[TrainingCaptureReceiptV1, bytes]:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            return self._capture(session, receipt_id)

    def _capture(self, session, receipt_id):
        row = _required(session, TrainingCaptureReceiptRecord, receipt_id)
        _verify_row(row)
        value = TrainingCaptureReceiptV1.model_validate_json(row.artifact_json)
        provider = _required(session, ProviderRecord, row.provider_id)
        request = _request(
            row.request_key,
            row.operator_id,
            source_rights_admission_id=value.source_rights_admission_id,
            source_id=value.source_id,
            provider_code=value.provider_code,
            evidence_reference=value.evidence_reference,
            payload_sha256=value.payload_sha256,
        )
        if (
            value.request_sha256 != request["request_sha256"]
            or value.imported_by != row.operator_id
            or provider.code != value.provider_code
            or not value.local_imported_at_utc
            <= value.archive_created_at_utc
            <= value.registered_at_utc
        ):
            raise ValueError("capture receipt metadata mismatch")
        _assert_row(
            row,
            _row(
                TrainingCaptureReceiptRecord,
                **request,
                capture_receipt_id=value.capture_receipt_id,
                source_rights_admission_id=value.source_rights_admission_id,
                source_id=value.source_id,
                provider_id=provider.provider_id,
                payload_sha256=value.payload_sha256,
                payload_bytes=row.payload_bytes,
                local_imported_at_utc=value.local_imported_at_utc,
                archive_created_at_utc=value.archive_created_at_utc,
                registered_at_utc=value.registered_at_utc,
                artifact_json=canonical_json(value),
            ),
        )
        rights = self._rights(session, value.source_rights_admission_id)
        if value.source_id not in rights.content_payload.rights_payload.source_ids:
            raise ValueError("stored capture rights scope mismatch")
        for at in (
            value.local_imported_at_utc,
            value.archive_created_at_utc,
            value.registered_at_utc,
        ):
            rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
        if hashlib.sha256(row.payload_bytes).hexdigest() != value.payload_sha256:
            raise ValueError("stored capture bytes SHA-256 mismatch")
        original = self.evidence.read(value.evidence_reference, value.payload_sha256)
        if original != row.payload_bytes:
            raise ValueError("stored capture differs from local bytes")
        strict_json_bytes(row.payload_bytes)
        return value, row.payload_bytes

    @sanitized_evidence_errors
    def admit(
        self,
        *,
        request_key: str,
        source_rights_admission_id: str,
        submissions: Iterable[TrainingFactSubmissionV1],
    ) -> TrainingFactAdmissionV1:
        """Atomically exact-reuse identities, materialize results and seal all children.

        Request identity includes ordered submissions and all evidence references, not
        operation times. Exact retries preserve IDs/times, even after rights expiry;
        changed requests fail. Reordered requests are not silently treated as retries.
        """
        values = tuple(
            TrainingFactSubmissionV1.model_validate(
                item.model_dump(mode="python", warnings=False)
            )
            for item in submissions
        )
        _reject_corrections(values)
        facts = PrepareTrainingFactBindingsService().prepare(
            candidates=(item.candidate for item in values)
        )
        request = _request(
            request_key,
            self.operator_id,
            source_rights_admission_id=source_rights_admission_id,
            submissions=values,
        )
        with self._sessions.begin() as session:
            _lock(session)
            previous = _prior(session, TrainingFactAdmissionRecord, request)
            if previous is not None:
                return self._load(session, previous.training_fact_admission_id)
            from football_system.infrastructure.database.training_correction_repository import (
                assert_new_v1_roots_current,
            )

            assert_new_v1_roots_current(session, facts)
            started = self._now()
            rights = self._rights(session, source_rights_admission_id)
            rights.assert_active_for(started, TRAINING_FACT_REQUIRED_USES)
            for item in values:
                self._verify_submission(
                    session, item, rights, self.operator_id, started
                )
            for item in values:
                _append_match_result(
                    session,
                    item.candidate.normalized_result,
                    item.candidate.provider_mapping.mapping_id,
                )
            by_match = {
                item.candidate.canonical_identity.internal_match_id: item
                for item in values
            }
            # Assemble every child before sampling completion. The parent ID
            # depends on the final timestamps, so only transient rows are staged.
            children = [
                row
                for fact in facts
                for row in _children(
                    None,
                    fact,
                    by_match[fact.content_payload.canonical_identity.internal_match_id],
                )
            ]
            completed, persisted = self._now(), self._now()
            if not started <= completed <= persisted:
                raise ValueError("training fact admission timestamps are inconsistent")
            admission = TrainingFactAdmissionV1.from_persisted(
                source_rights_admission=rights,
                facts=facts,
                actual_started_at_utc=started,
                actual_completed_at_utc=completed,
                persisted_at_utc=persisted,
            )
            # Deferred parent FK, children first. Parent insertion seals the graph.
            for row in children:
                row.training_fact_admission_id = admission.training_fact_admission_id
                row.row_sha256 = _row(
                    type(row),
                    **{
                        column.name: getattr(row, column.name)
                        for column in row.__table__.columns
                        if column.name != "row_sha256"
                    },
                ).row_sha256
                session.add(row)
                session.flush()
            session.add(_admission_row(admission, request))
            session.flush()
            stored = self._load(session, admission.training_fact_admission_id)
            self._check_current_authorization(
                rights,
                not_before=persisted,
                authorities=(item.reviewer_authority for item in values),
            )
            return stored

    @sanitized_evidence_errors
    def load(self, admission_id: str) -> TrainingFactAdmissionV1:
        """Verified source-time fact graph only; not production approval."""
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            return self._load(session, admission_id)

    def _load(self, session, admission_id, *, _historical_root=False):
        row = _required(session, TrainingFactAdmissionRecord, admission_id)
        _verify_row(row)
        value = TrainingFactAdmissionV1.model_validate_json(row.artifact_json)
        request_data = strict_json_bytes(row.request_json.encode("utf-8"))
        submissions = tuple(
            TrainingFactSubmissionV1.model_validate(item)
            for item in request_data["submissions"]
        )
        request = _request(
            row.request_key,
            row.operator_id,
            source_rights_admission_id=value.content_payload.source_rights_admission_id,
            submissions=submissions,
        )
        _assert_row(row, _admission_row(value, request))
        rights = self._rights(session, value.content_payload.source_rights_admission_id)
        if rights != value.source_rights_admission:
            raise ValueError("stored admission rights graph mismatch")
        facts = PrepareTrainingFactBindingsService().prepare(
            candidates=(item.candidate for item in submissions)
        )
        if facts != value.facts:
            raise ValueError("stored admission request/fact graph mismatch")
        by_match = {
            item.candidate.canonical_identity.internal_match_id: item
            for item in submissions
        }
        expected = {
            model: []
            for model in (
                TrainingFactFixtureSourceRecord,
                MatchSeasonMembershipRecord,
                MatchResultAdmissionRecord,
                TrainingFactBindingRecord,
            )
        }
        for fact in facts:
            item = by_match[fact.content_payload.canonical_identity.internal_match_id]
            self._verify_submission(
                session,
                item,
                rights,
                row.operator_id,
                value.content_payload.actual_started_at_utc,
                historical=True,
            )
            result_row = _required(
                session,
                MatchResultRecord,
                item.candidate.normalized_result.match_result_id,
            )
            if (
                _match_result(session, result_row) != item.candidate.normalized_result
                or result_row.provider_mapping_id
                != item.candidate.provider_mapping.mapping_id
            ):
                raise ValueError("stored normalized result differs from admitted fact")
            for child in _children(admission_id, fact, item):
                expected[type(child)].append(child)
        for model, children in expected.items():
            stored = tuple(
                session.scalars(
                    select(model).where(
                        model.training_fact_admission_id == admission_id
                    )
                )
            )
            if len(stored) != len(children):
                raise ValueError("stored admission child count mismatch")
            keyed = {_primary_key(child): child for child in stored}
            for child in children:
                actual = keyed.get(_primary_key(child))
                if actual is None:
                    raise ValueError("stored admission child missing")
                _verify_row(actual)
                _assert_row(actual, child)
        if not _historical_root:
            from football_system.infrastructure.database.training_correction_repository import (
                verify_historical_result_successors,
            )

            verify_historical_result_successors(self, session, value)
        return value

    def _verify_submission(self, session, item, rights, operator, started, *, historical=False):
        candidate, evidence = item.candidate, item.source_evidence
        _reject_corrections((item,))
        fixture = candidate.fixture_source
        membership = candidate.season_membership.content_payload
        result = candidate.match_result_admission.content_payload
        receipt_specs = (
            (
                evidence.fixture,
                fixture.fixture_source_archive_id,
                fixture.fixture_source_archive_payload_sha256,
                fixture.local_imported_at_utc,
                fixture.fixture_source_archive_created_at_utc,
                fixture.registered_at_utc,
            ),
            (
                evidence.scope,
                membership.provider_scope_raw_artifact_id,
                membership.provider_scope_payload_sha256,
                membership.local_imported_at_utc,
                membership.provider_scope_created_at_utc,
                membership.registered_at_utc,
            ),
            (
                evidence.result,
                result.raw_artifact_id,
                result.raw_artifact_payload_sha256,
                result.local_imported_at_utc,
                result.raw_artifact_created_at_utc,
                result.registered_at_utc,
            ),
        )
        payloads = []
        for ref, archive_id, digest, imported, created, registered in receipt_specs:
            receipt, payload = self._capture(session, ref.capture_receipt_id)
            if (
                (
                    receipt.capture_receipt_id,
                    receipt.payload_sha256,
                    receipt.local_imported_at_utc,
                    receipt.archive_created_at_utc,
                    receipt.registered_at_utc,
                )
                != (archive_id, digest, imported, created, registered)
                or receipt.source_rights_admission_id
                != rights.source_rights_admission_id
                or (receipt.source_id, receipt.provider_code)
                != (fixture.source_id, fixture.provider_code)
                or registered > started
            ):
                raise ValueError("candidate differs from registered capture receipt")
            payloads.append(payload)
        if fixture.fixture_source_record_id != (
            evidence.fixture.record_pointer or "ROOT"
        ):
            raise ValueError("fixture record ID must identify the exact JSON pointer")
        review = self.evidence.review(
            evidence=item.reviewer_evidence,
            authority=item.reviewer_authority,
            schema="TRAINING_FACT_REVIEW_INPUT_V1",
            digest=training_review_input_sha256(candidate, evidence),
            source_ids=(fixture.source_id,),
            operator_id=operator,
            at_utc=started,
        )
        if (
            membership.reviewed_by,
            membership.reviewed_at_utc,
            result.reviewed_by,
            result.reviewed_at_utc,
        ) != (
            review.authorized_reviewer,
            review.reviewed_at_utc,
            review.authorized_reviewer,
            review.reviewed_at_utc,
        ):
            raise ValueError(
                "candidate reviewer metadata differs from local review bytes"
            )
        provider_ids = self.evidence.verify_provider_records(item, tuple(payloads))
        _verify_identities(session, item, provider_ids, historical=historical)


def _verify_identities(session, item, provider_ids, *, historical=False):
    candidate, evidence = item.candidate, item.source_evidence
    identity, mapping = candidate.canonical_identity, candidate.provider_mapping
    fixture = candidate.fixture_source
    match = _required(session, MatchRecord, identity.internal_match_id)
    canonical = _required(
        session, CanonicalMatchIdentityRecord, identity.internal_match_id
    )
    provider_mapping = _required(
        session, ProviderMatchMappingRecord, mapping.mapping_id
    )
    provider = _required(session, ProviderRecord, provider_mapping.provider_id)
    _required(session, CompetitionRecord, identity.internal_competition_id)
    if (
        match.competition_id,
        match.home_team_id,
        match.away_team_id,
        match.kickoff_at_utc,
        canonical.season,
        canonical.competition_type,
    ) != (
        identity.internal_competition_id,
        identity.internal_home_team_id,
        identity.internal_away_team_id,
        identity.kickoff_at_utc,
        identity.season,
        identity.competition_type,
    ) or max(
        match.available_at_utc, canonical.available_at_utc, mapping.available_at_utc
    ) > fixture.source_available_at_utc:
        raise ValueError("preexisting canonical identity mismatch or unavailable")
    stored_mapping = TrainingProviderMatchMappingV1(
        mapping_id=provider_mapping.mapping_id,
        provider_code=provider.code,
        external_namespace=provider_mapping.external_namespace,
        external_match_id=provider_mapping.external_match_id,
        internal_match_id=provider_mapping.internal_match_id,
        resolution_method=provider_mapping.resolution_method,
        confidence=provider_mapping.confidence,
        available_at_utc=provider_mapping.available_at_utc,
    )
    if stored_mapping != TrainingProviderMatchMappingV1.from_mapping(mapping):
        raise ValueError("preexisting provider mapping mismatch")
    if (
        provider_mapping.supersedes_mapping_id is not None
        or session.scalar(
            select(ProviderMatchMappingRecord.mapping_id).where(
                ProviderMatchMappingRecord.supersedes_mapping_id == mapping.mapping_id
            )
        )
        is not None
    ):
        raise ControlledTrainingCorrectionRequired(
            "controlled mapping correction implementation is missing"
        )
    for alias_id, raw_id, team_id in (
        (evidence.home_team_alias_id, provider_ids[0], identity.internal_home_team_id),
        (evidence.away_team_alias_id, provider_ids[1], identity.internal_away_team_id),
    ):
        team = _required(session, TeamRecord, team_id)
        alias = _required(session, ProviderTeamAliasRecord, alias_id)
        if (
            alias.provider_id,
            alias.provider_team_id,
            alias.internal_team_id,
            alias.team_type,
        ) != (
            provider.provider_id,
            raw_id,
            team_id,
            team.team_type,
        ) or alias.available_at_utc > fixture.source_available_at_utc:
            raise ValueError(
                "preexisting provider team identity mismatch or unavailable"
            )
        targets = set(
            session.scalars(
                select(ProviderTeamAliasRecord.internal_team_id).where(
                    ProviderTeamAliasRecord.provider_id == provider.provider_id,
                    ProviderTeamAliasRecord.provider_team_id == raw_id,
                    ProviderTeamAliasRecord.team_type == team.team_type,
                    ProviderTeamAliasRecord.available_at_utc
                    <= fixture.source_available_at_utc,
                )
            )
        )
        if targets != {team_id}:
            raise ValueError("ambiguous registered provider team identity")
    competition = _required(
        session, ProviderCompetitionMappingRecord, evidence.competition_mapping_id
    )
    membership = candidate.season_membership.content_payload
    if (
        competition.provider_id,
        competition.provider_competition_id,
        competition.internal_competition_id,
        competition.season,
        competition.competition_type,
    ) != (
        provider.provider_id,
        provider_ids[2],
        identity.internal_competition_id,
        identity.season,
        identity.competition_type,
    ) or competition.available_at_utc > membership.source_available_at_utc:
        raise ValueError(
            "preexisting provider competition identity mismatch or unavailable"
        )
    targets = set(
        session.scalars(
            select(ProviderCompetitionMappingRecord.internal_competition_id).where(
                ProviderCompetitionMappingRecord.provider_id == provider.provider_id,
                ProviderCompetitionMappingRecord.provider_competition_id
                == provider_ids[2],
                ProviderCompetitionMappingRecord.season == identity.season,
                ProviderCompetitionMappingRecord.competition_type
                == identity.competition_type,
                ProviderCompetitionMappingRecord.available_at_utc
                <= membership.source_available_at_utc,
            )
        )
    )
    if targets != {identity.internal_competition_id}:
        raise ValueError("ambiguous registered provider competition identity")
    others = session.scalar(
        select(MatchResultRecord.match_result_id).where(
            MatchResultRecord.provider_id == provider.provider_id,
            MatchResultRecord.internal_match_id == identity.internal_match_id,
            MatchResultRecord.match_result_id
            != candidate.normalized_result.match_result_id,
        )
    )
    if others is not None and not historical:
        raise ControlledTrainingCorrectionRequired(
            "controlled result correction implementation is missing"
        )
    # V1 can reuse exact registered prerequisites, but cannot silently establish
    # a new source/status/season version for an already-admitted mapping.
    for model, expected in (
        (TrainingFactFixtureSourceRecord, candidate.fixture_source),
        (MatchSeasonMembershipRecord, candidate.season_membership),
        (MatchResultAdmissionRecord, candidate.match_result_admission),
    ):
        for row in session.scalars(
            select(model).where(model.provider_mapping_id == mapping.mapping_id)
        ):
            _verify_row(row)
            if type(expected).model_validate_json(row.artifact_json) != expected:
                raise ControlledTrainingCorrectionRequired(
                    "controlled source/season/status correction implementation is missing"
                )


def _children(admission_id, fact, submission):
    content = fact.content_payload
    fixture, membership, result = (
        content.fixture_source,
        content.season_membership,
        content.match_result_admission,
    )
    evidence = submission.source_evidence
    fixture_id = stable_id(
        "TRAINING_FIXTURE_SOURCE_V1",
        tagged_canonical_sha256("TRAINING_FIXTURE_SOURCE_V1", fixture),
    )
    common = dict(
        training_fact_admission_id=admission_id,
        internal_match_id=content.canonical_identity.internal_match_id,
        provider_mapping_id=content.provider_mapping.mapping_id,
    )
    return (
        _row(
            TrainingFactFixtureSourceRecord,
            **common,
            fixture_source_id=fixture_id,
            capture_receipt_id=evidence.fixture.capture_receipt_id,
            artifact_json=canonical_json(fixture),
        ),
        _row(
            MatchSeasonMembershipRecord,
            **common,
            season_membership_id=membership.season_membership_id,
            fixture_source_id=fixture_id,
            capture_receipt_id=evidence.scope.capture_receipt_id,
            canonical_competition_id=membership.content_payload.canonical_competition_id,
            canonical_season_id=membership.content_payload.canonical_season_id,
            home_team_alias_id=evidence.home_team_alias_id,
            away_team_alias_id=evidence.away_team_alias_id,
            competition_mapping_id=evidence.competition_mapping_id,
            artifact_json=canonical_json(membership),
        ),
        _row(
            MatchResultAdmissionRecord,
            **common,
            match_result_admission_id=result.match_result_admission_id,
            match_result_id=content.normalized_result.match_result_id,
            capture_receipt_id=evidence.result.capture_receipt_id,
            artifact_json=canonical_json(result),
        ),
        _row(
            TrainingFactBindingRecord,
            **common,
            sequence=content.sequence,
            training_fact_binding_id=fact.training_fact_binding_id,
            fixture_source_id=fixture_id,
            season_membership_id=membership.season_membership_id,
            match_result_admission_id=result.match_result_admission_id,
            match_result_id=content.normalized_result.match_result_id,
            artifact_json=canonical_json(fact),
        ),
    )


def _admission_row(value, request):
    content = value.content_payload
    return _row(
        TrainingFactAdmissionRecord,
        **request,
        training_fact_admission_id=value.training_fact_admission_id,
        source_rights_admission_id=content.source_rights_admission_id,
        admission_hash=value.admission_hash,
        admitted_fact_count=content.admitted_fact_count,
        admitted_fact_root=content.admitted_fact_root,
        actual_started_at_utc=content.actual_started_at_utc,
        actual_completed_at_utc=content.actual_completed_at_utc,
        persisted_at_utc=content.persisted_at_utc,
        artifact_json=canonical_json(value),
    )


def _reject_corrections(values):
    if any(
        item.candidate.normalized_result.supersedes_match_result_id is not None
        or item.candidate.match_result_admission.content_payload.supersedes_match_result_id
        is not None
        for item in values
    ):
        raise ControlledTrainingCorrectionRequired(
            "controlled training correction implementation is missing"
        )


def _identifier(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 160
    ):
        raise ValueError("operation identity must be an exact nonempty identifier")


def _request(key, operator, **payload):
    _identifier(key)
    document = canonical_json({"request_key": key, "operator_id": operator, **payload})
    return dict(
        request_key=key,
        operator_id=operator,
        request_json=document,
        request_sha256=tagged_canonical_sha256(
            "TRAINING_PERSISTENCE_REQUEST_V1",
            strict_json_bytes(document.encode("utf-8")),
        ),
    )


def _lock(session):
    # Serialize SQLite retry lookup and writes before the first read. No public
    # repository calls or independent transactions are allowed inside this one.
    session.execute(text("BEGIN IMMEDIATE"))


def _prior(session, model, request):
    row = session.scalar(
        select(model).where(
            or_(
                model.request_key == request["request_key"],
                model.request_sha256 == request["request_sha256"],
            )
        )
    )
    if row is not None:
        _verify_row(row)
        if any(getattr(row, key) != value for key, value in request.items()):
            raise ValueError("immutable retry request conflicts with stored content")
    return row


def _required(session, model, key):
    value = session.get(model, key)
    if value is None:
        raise ValueError(f"missing preexisting {model.__table__.name}: {key}")
    return value


def _row(model, **values):
    values["row_sha256"] = tagged_canonical_sha256(
        f"TRAINING_SQL_ROW_V1:{model.__table__.name}",
        {key: value for key, value in values.items() if key != "payload_bytes"},
    )
    return model(**values)


def _verify_row(row):
    values = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"row_sha256", "payload_bytes"}
    }
    if (
        tagged_canonical_sha256(f"TRAINING_SQL_ROW_V1:{row.__table__.name}", values)
        != row.row_sha256
    ):
        raise ValueError(f"stored {row.__table__.name} row integrity mismatch")


def _assert_row(actual, expected):
    if any(
        getattr(actual, column.name) != getattr(expected, column.name)
        for column in expected.__table__.columns
    ):
        raise ValueError(f"stored {actual.__table__.name} projection mismatch")


def _primary_key(row):
    return tuple(
        getattr(row, column.name) for column in row.__table__.primary_key.columns
    )
