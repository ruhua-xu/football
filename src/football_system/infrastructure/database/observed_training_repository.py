"""Synthetic/offline-capable current observation admission, no acquisition client.

API: prepare_scope -> genuine review -> record_scope -> existing repository's
capture_local_json -> prepare -> genuine reviews -> admit. prepare methods read
but do not write or manufacture reviews. load_admission is an immutable audit
read; load_context is a current authorized-use read. All new writes are one
business transaction with a fresh grant check after the last I/O/readback.

Each native receipt contains one /data fixture or up to 50 /data/N records. All
roles and fixtures may share that receipt, with no reserialization or copies.
max_capture_receipts counts distinct LOCAL_FILE_IMPORT receipts referenced by
admissions, not HTTP sends. A separate acquisition controller owns the DRAFT 20
HTTP-request budget, which has not been executed or enforced by this lane.
Unknown upstream clocks/order remain null. Recorded times mark transaction-local
events, not a subsequent SQLite commit acknowledgement.
Receipt metadata is checked without BLOB access before any provider bytes. Local
capture ordinals come from the DB capture ledger, never from the caller/upstream.
All full-fixture roles must agree on outcome; normalized observation time is the
result-role capture time, while capture_observed_at_utc remains the all-role gate.

The registered canonical identity remains the immutable match anchor. A reviewed
successor with contradictory kickoff/team evidence is a nontrainable withdrawal,
even if the native score is FT. Resolved team IDs come only from actual aliases;
None explicitly denotes a predecessor alias that no longer resolves the source.
Identity assessments retain the complete catalog selection checked at admission;
audit reads rehash those source rows rather than reinterpret later tied-clock additions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from sqlalchemy import or_, select, text

from football_system.domain.archive import HistoricalDataMode, canonical_json
from football_system.domain.observed_training import (
    CurrentSnapshotCollectionScopeV1,
    ObservedCaptureEvidenceV1,
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
    ObservedSnapshotSubmissionV1,
    ObservedStreamV1,
)
from football_system.domain.training_admission import (
    TRAINING_FACT_REQUIRED_USES,
    LocalReviewEvidenceV1,
    LocalReviewerAttestationV1,
    TrainingCanonicalMatchIdentityV1,
    TrainingProviderMatchMappingV1,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    MatchResultRecord,
    ObservedCollectionScopeRecord,
    ObservedResultBindingRecord,
    ObservedSnapshotAdmissionRecord,
    ObservedSnapshotRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
    TrainingCaptureReceiptRecord,
    TrainingFactBindingRecord,
)
from football_system.infrastructure.database.training_admission_repository import (
    SqlAlchemyTrainingAdmissionRepository,
    TrainingCaptureReceiptV1,
    _assert_row,
    _lock,
    _prior,
    _request,
    _required,
    _row,
    _verify_row,
)
from football_system.infrastructure.database.observed_training_schema import (
    OBSERVED_CAPTURE_ORDINALS,
)
from football_system.infrastructure.files.training_evidence import (
    json_pointer,
    provider_record_sha256,
    sanitized_evidence_errors,
    strict_json_bytes,
)
from football_system.infrastructure.providers.real.sportmonks_observed import (
    inspect_observed_fixture,
)


class SqlAlchemyObservedTrainingRepository:
    def __init__(
        self,
        admission_repository: SqlAlchemyTrainingAdmissionRepository,
        clock: Callable[[], datetime] | None = None,
    ):
        self.admission_repository = admission_repository
        self._sessions = admission_repository._sessions
        self.evidence = admission_repository.evidence
        self._clock = clock if clock is not None else admission_repository._clock

    def _now(self):
        at = self._clock()
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("observed operation clock must return aware UTC")
        return at

    @property
    def operator_id(self):
        return self.admission_repository.operator_id

    @sanitized_evidence_errors
    def prepare_scope(
        self,
        *,
        source_rights_admission_id: str,
        source_id: str,
        provider_competition_id: str,
        canonical_competition_id: str,
        seasons: Iterable[ObservedScopeSeasonV1],
        max_capture_receipts: int,
        max_snapshot_records: int,
        permitted_uses: Iterable[str],
        retention_deadline_utc: datetime,
        user_terms_resolution: LocalReviewEvidenceV1,
    ) -> CurrentSnapshotCollectionScopeV1:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            rights = self.admission_repository._rights(
                session, source_rights_admission_id
            )
            at = self._now()
            rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
            subject = CurrentSnapshotCollectionScopeV1(
                source_data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
                retrospective=True,
                source_rights_admission_id=source_rights_admission_id,
                source_rights_admission_hash=rights.admission_hash,
                source_id=source_id,
                provider_competition_id=provider_competition_id,
                canonical_competition_id=canonical_competition_id,
                seasons=tuple(seasons),
                max_capture_receipts=max_capture_receipts,
                max_snapshot_records=max_snapshot_records,
                permitted_uses=tuple(permitted_uses),
                retention_deadline_utc=retention_deadline_utc,
                user_terms_resolution=user_terms_resolution,
            )
            self._scope_subject(session, subject, rights, at)
            self._finish(rights, subject, not_before=at)
            return subject

    @sanitized_evidence_errors
    def record_scope(
        self,
        *,
        request_key: str,
        subject: CurrentSnapshotCollectionScopeV1,
        reviewer_attestation: LocalReviewerAttestationV1,
    ) -> ObservedCollectionScopeAdmissionV1:
        subject = CurrentSnapshotCollectionScopeV1.model_validate(subject)
        attestation = LocalReviewerAttestationV1.model_validate(
            reviewer_attestation.model_dump(mode="python")
        )
        request = _request(
            request_key,
            self.operator_id,
            subject=subject,
            reviewer_attestation=attestation,
        )
        with self._sessions.begin() as session:
            _lock(session)
            prior = _prior(session, ObservedCollectionScopeRecord, request)
            if prior is not None:
                return self._scope(session, prior.scope_id)[0]
            rights = self.admission_repository._rights(
                session, subject.source_rights_admission_id
            )
            started = self._now()
            self._scope_subject(session, subject, rights, started)
            self._review(
                attestation,
                subject.schema_version,
                subject.subject_hash,
                subject.source_id,
                started,
            )
            high_watermark = session.scalar(
                text(
                    "SELECT COALESCE(MAX(capture_ordinal), 0) FROM observed_capture_ordinals"
                )
            )
            recorded = self._now()
            if recorded < started:
                raise ValueError("observed scope clock moved backwards")
            value = ObservedCollectionScopeAdmissionV1.freeze(
                subject=subject,
                reviewer_attestation=attestation,
                recorded_at_utc=recorded,
                capture_receipt_high_watermark=high_watermark,
            )
            session.add(self._scope_row(session, value, request))
            session.flush()
            stored, rights = self._scope(session, value.scope_id)
            self._finish(
                rights, subject, not_before=recorded, attestations=(attestation,)
            )
            return stored

    def _scope_subject(self, session, subject, rights, at):
        rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
        if (
            subject.source_rights_admission_id != rights.source_rights_admission_id
            or subject.source_rights_admission_hash != rights.admission_hash
            or subject.source_id not in rights.content_payload.rights_payload.source_ids
        ):
            raise ValueError("observed scope is outside the existing source rights")
        if (
            not at
            < subject.retention_deadline_utc
            <= rights.content_payload.rights_payload.expires_at_utc
        ):
            raise ValueError(
                "observed scope retention deadline is expired or outside rights"
            )
        self._provider(session, subject.provider_code)
        competition = _required(
            session, CompetitionRecord, subject.canonical_competition_id
        )
        if (
            competition.name.casefold() != "bundesliga"
            or competition.country_code not in ("DE", "DEU", "GER")
        ):
            raise ValueError(
                "observed scope requires the existing German Bundesliga competition"
            )
        ref = subject.user_terms_resolution
        resolved = strict_json_bytes(
            self.evidence.read(ref.evidence_reference, ref.evidence_sha256)
        )
        expected = dict(
            schema_version="CURRENT_SNAPSHOT_USER_TERMS_RESOLUTION_V1",
            source_rights_admission_id=rights.source_rights_admission_id,
            source_rights_admission_hash=rights.admission_hash,
            source_id=subject.source_id,
            terms_sha256=rights.content_payload.rights_payload.terms_sha256,
            permitted_uses=list(subject.permitted_uses),
            retention_deadline_utc=subject.model_dump(mode="json")[
                "retention_deadline_utc"
            ],
            resolution="RESOLVED_FOR_DECLARED_SNAPSHOT_SCOPE",
        )
        if not isinstance(resolved, dict) or resolved != expected:
            raise ValueError(
                "new scope requires exact user-terms-resolved input evidence; old license is not a scope upgrade"
            )

    def _scope(self, session, scope_id):
        row = _required(session, ObservedCollectionScopeRecord, scope_id)
        _verify_row(row)
        value = ObservedCollectionScopeAdmissionV1.model_validate(
            strict_json_bytes(row.artifact_json.encode())
        )
        request = _request(
            row.request_key,
            row.operator_id,
            subject=value.subject,
            reviewer_attestation=value.reviewer_attestation,
        )
        _assert_row(row, self._scope_row(session, value, request))
        rights = self.admission_repository._rights(
            session, value.subject.source_rights_admission_id
        )
        self._scope_subject(session, value.subject, rights, value.recorded_at_utc)
        self._review(
            value.reviewer_attestation,
            value.subject.schema_version,
            value.subject.subject_hash,
            value.subject.source_id,
            value.recorded_at_utc,
        )
        return value, rights

    def _scope_row(self, session, value, request):
        return _row(
            ObservedCollectionScopeRecord,
            **request,
            scope_id=value.scope_id,
            subject_hash=value.subject.subject_hash,
            content_hash=value.content_hash,
            source_rights_admission_id=value.subject.source_rights_admission_id,
            provider_id=self._provider(
                session, value.subject.provider_code
            ).provider_id,
            canonical_competition_id=value.subject.canonical_competition_id,
            recorded_at_utc=value.recorded_at_utc,
            capture_receipt_high_watermark=value.capture_receipt_high_watermark,
            artifact_json=canonical_json(value),
        )

    def _review(self, attestation, schema, digest, source_id, at):
        content = attestation.content_payload
        if (content.attested_schema_version, content.attested_payload_hash) != (
            schema,
            digest,
        ):
            raise ValueError(
                "new genuine review must bind the observed subject schema/hash"
            )
        review = self.evidence.review(
            evidence=content.evidence,
            authority=_authority(attestation),
            schema=schema,
            digest=digest,
            source_ids=(source_id,),
            operator_id=self.operator_id,
            at_utc=at,
        )
        if (review.authorized_reviewer, review.reviewed_at_utc) != (
            content.authorized_reviewer,
            content.reviewed_at_utc,
        ):
            raise ValueError(
                "observed attestation differs from genuine local review bytes"
            )

    def _finish(self, rights, subject, *, not_before, attestations=()):
        references = {
            _authority(rights.content_payload.reviewer_attestation),
            *(_authority(x) for x in attestations),
        }
        permissions = [self.evidence.load_authority(ref) for ref in references]
        # Nothing that reads or writes follows this fresh final authorization time.
        current = self._now()
        if current < not_before:
            raise ValueError(
                "observed operation clock moved backwards before transaction exit"
            )
        rights.assert_active_for(current, TRAINING_FACT_REQUIRED_USES)
        if current >= subject.retention_deadline_utc:
            raise ValueError("observed scope retention deadline expired")
        for permission in permissions:
            permission.assert_active_for(current)
        return current

    def _capture_metadata(self, session, scope, rights, inputs, at):
        """Authorize sealed metadata for every role before selecting any payload BLOB."""
        rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
        if at >= scope.subject.retention_deadline_utc:
            raise ValueError("observed scope retention deadline expired")
        provider = self._provider(session, scope.subject.provider_code)
        table = TrainingCaptureReceiptRecord.__table__
        ordinals = table.metadata.tables[OBSERVED_CAPTURE_ORDINALS]
        columns = tuple(c for c in table.c if c.name != "payload_bytes")
        captures = {}
        for item in inputs:
            for role in ("fixture", "season", "result"):
                key = getattr(item, role).capture_receipt_id
                if key in captures:
                    continue
                metadata = (
                    session.execute(
                        select(
                            *columns,
                            ordinals.c.capture_ordinal,
                            ordinals.c.receipt_hash.label("ordinal_receipt_hash"),
                            ordinals.c.capture_row_sha256,
                        )
                        .select_from(
                            table.outerjoin(
                                ordinals,
                                ordinals.c.capture_receipt_id
                                == table.c.capture_receipt_id,
                            )
                        )
                        .where(table.c.capture_receipt_id == key)
                    )
                    .mappings()
                    .one_or_none()
                )
                if metadata is None:
                    raise ValueError(
                        "missing preexisting observed capture receipt metadata"
                    )
                # Transient metadata-only projections cannot lazy-load payload_bytes.
                row = TrainingCaptureReceiptRecord(
                    **{c.name: metadata[c.name] for c in columns}
                )
                _verify_row(row)
                receipt = TrainingCaptureReceiptV1.model_validate(
                    strict_json_bytes(row.artifact_json.encode())
                )
                if (
                    row.source_rights_admission_id,
                    row.source_id,
                    row.provider_id,
                    receipt.source_rights_admission_id,
                    receipt.source_id,
                    receipt.provider_code,
                ) != (
                    rights.source_rights_admission_id,
                    scope.subject.source_id,
                    provider.provider_id,
                    rights.source_rights_admission_id,
                    scope.subject.source_id,
                    scope.subject.provider_code,
                ):
                    raise ValueError(
                        "observed capture metadata is outside source/provider/rights scope"
                    )
                if (
                    not scope.recorded_at_utc
                    <= receipt.local_imported_at_utc
                    <= receipt.archive_created_at_utc
                    <= receipt.registered_at_utc
                    <= at
                ):
                    raise ValueError(
                        "observed capture must follow new reviewed scope recording and precede admission"
                    )
                request = _request(
                    row.request_key,
                    row.operator_id,
                    source_rights_admission_id=receipt.source_rights_admission_id,
                    source_id=receipt.source_id,
                    provider_code=receipt.provider_code,
                    evidence_reference=receipt.evidence_reference,
                    payload_sha256=receipt.payload_sha256,
                )
                if (
                    receipt.request_sha256 != request["request_sha256"]
                    or receipt.imported_by != row.operator_id
                ):
                    raise ValueError(
                        "observed capture receipt request metadata mismatch"
                    )
                _assert_row(
                    row,
                    _row(
                        TrainingCaptureReceiptRecord,
                        **request,
                        capture_receipt_id=receipt.capture_receipt_id,
                        source_rights_admission_id=receipt.source_rights_admission_id,
                        source_id=receipt.source_id,
                        provider_id=provider.provider_id,
                        payload_sha256=receipt.payload_sha256,
                        payload_bytes=None,
                        local_imported_at_utc=receipt.local_imported_at_utc,
                        archive_created_at_utc=receipt.archive_created_at_utc,
                        registered_at_utc=receipt.registered_at_utc,
                        artifact_json=canonical_json(receipt),
                    ),
                )
                ordinal = metadata["capture_ordinal"]
                if ordinal is None or (
                    metadata["ordinal_receipt_hash"],
                    metadata["capture_row_sha256"],
                ) != (receipt.receipt_hash, row.row_sha256):
                    raise ValueError(
                        "observed capture ordinal metadata binding mismatch"
                    )
                if ordinal <= scope.capture_receipt_high_watermark:
                    raise ValueError(
                        "observed capture must follow new reviewed scope recording, including tied clocks"
                    )
                captures[key] = (receipt, ordinal)
        return captures

    @sanitized_evidence_errors
    def prepare(
        self, *, scope_id: str, snapshots: Iterable[ObservedSnapshotInputV1]
    ) -> tuple[ObservedSnapshotSubjectV1, ...]:
        inputs = tuple(ObservedSnapshotInputV1.model_validate(x) for x in snapshots)
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            scope, rights = self._scope(session, scope_id)
            started = self._now()
            rights.assert_active_for(started, TRAINING_FACT_REQUIRED_USES)
            if started >= scope.subject.retention_deadline_utc:
                raise ValueError("observed scope retention deadline expired")
            metadata = self._capture_metadata(session, scope, rights, inputs, started)
            current = self._now()
            rights.assert_active_for(current, TRAINING_FACT_REQUIRED_USES)
            if not started <= current < scope.subject.retention_deadline_utc:
                raise ValueError(
                    "observed capture preflight clock moved backwards or retention expired"
                )
            admissions, records = self._history(session, scope, rights)
            if admissions and admissions[-1].registered_at_utc > started:
                raise ValueError(
                    "observed operation clock moved backwards across admissions"
                )
            heads = {x.stream.stream_id: x for x in records}
            subjects = tuple(
                sorted(
                    (
                        self._subject(
                            session, scope, rights, x, heads, started, metadata=metadata
                        )
                        for x in inputs
                    ),
                    key=lambda x: x.stream.provider_fixture_key,
                )
            )
            self._cohort(scope, records, subjects, first=not admissions)
            self._finish(
                rights,
                scope.subject,
                not_before=current,
                attestations=(scope.reviewer_attestation,),
            )
            return subjects

    @sanitized_evidence_errors
    def admit(
        self,
        *,
        request_key: str,
        scope_id: str,
        submissions: Iterable[ObservedSnapshotSubmissionV1],
    ) -> ObservedSnapshotAdmissionV1:
        values = tuple(
            ObservedSnapshotSubmissionV1.model_validate(x) for x in submissions
        )
        request = _request(
            request_key, self.operator_id, scope_id=scope_id, submissions=values
        )
        with self._sessions.begin() as session:
            _lock(session)
            prior = _prior(session, ObservedSnapshotAdmissionRecord, request)
            if prior is not None:
                return self._load(session, prior.admission_id)
            scope, rights = self._scope(session, scope_id)
            started = self._now()
            rights.assert_active_for(started, TRAINING_FACT_REQUIRED_USES)
            if started >= scope.subject.retention_deadline_utc:
                raise ValueError("observed scope retention deadline expired")
            metadata = self._capture_metadata(
                session, scope, rights, tuple(x.subject.input for x in values), started
            )
            current = self._now()
            rights.assert_active_for(current, TRAINING_FACT_REQUIRED_USES)
            if not started <= current < scope.subject.retention_deadline_utc:
                raise ValueError(
                    "observed capture preflight clock moved backwards or retention expired"
                )
            admissions, previous = self._history(session, scope, rights)
            if admissions and admissions[-1].registered_at_utc > started:
                raise ValueError(
                    "observed operation clock moved backwards across admissions"
                )
            heads = {x.stream.stream_id: x for x in previous}
            for value in values:
                actual = self._subject(
                    session,
                    scope,
                    rights,
                    value.subject.input,
                    heads,
                    started,
                    metadata=metadata,
                )
                if actual != value.subject:
                    raise ValueError(
                        "reviewed snapshot differs from reread bytes or actual predecessor; stale/fork request"
                    )
                self._review(
                    value.reviewer_attestation,
                    actual.schema_version,
                    actual.subject_hash,
                    actual.stream.source_id,
                    started,
                )
            self._cohort(
                scope, previous, tuple(x.subject for x in values), first=not admissions
            )
            verified, registered = self._now(), self._now()
            if not current <= verified <= registered:
                raise ValueError("observed admission clock moved backwards")
            records = tuple(
                ObservedSnapshotRecordV1.freeze(
                    subject=x.subject,
                    reviewer_attestation=x.reviewer_attestation,
                    verified_at_utc=verified,
                    registered_at_utc=registered,
                )
                for x in sorted(
                    values, key=lambda x: x.subject.stream.provider_fixture_key
                )
            )
            admission = ObservedSnapshotAdmissionV1.freeze(
                scope_id=scope_id,
                scope_hash=scope.content_hash,
                admission_sequence=len(admissions),
                actual_started_at_utc=started,
                verified_at_utc=verified,
                registered_at_utc=registered,
                records=records,
            )
            for record in records:
                provider = self._provider(session, record.stream.provider_code)
                if record.normalized_result is not None:
                    binding = _binding_row(record, provider.provider_id)
                    session.add(binding)
                    session.flush()
                    # The old bare writer intentionally cannot authorize this lane.
                    # Use the SAME normalized table under an exact deferred binding.
                    session.add(
                        MatchResultRecord(
                            **{
                                column.name: getattr(binding, column.name)
                                for column in MatchResultRecord.__table__.columns
                            }
                        )
                    )
                    session.flush()
                session.add(
                    _snapshot_row(admission.admission_id, record, provider.provider_id)
                )
                session.flush()
            session.add(_admission_row(admission, request))
            session.flush()
            stored = self._load(session, admission.admission_id)
            self._finish(
                rights,
                scope.subject,
                not_before=registered,
                attestations=(
                    scope.reviewer_attestation,
                    *(x.reviewer_attestation for x in values),
                ),
            )
            return stored

    def _subject(
        self,
        session,
        scope,
        rights,
        item,
        heads,
        at,
        *,
        metadata=None,
        identity_evidence: ObservedIdentityEvidenceV1 | None = None,
    ):
        rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
        mapping = _required(
            session, ProviderMatchMappingRecord, item.provider_mapping_id
        )
        provider = _required(session, ProviderRecord, mapping.provider_id)
        match = _required(session, MatchRecord, mapping.internal_match_id)
        canonical = _required(
            session, CanonicalMatchIdentityRecord, match.internal_match_id
        )
        seasons = [
            x
            for x in scope.subject.seasons
            if x.canonical_season_id == canonical.season
        ]
        if len(seasons) != 1 or provider.code != scope.subject.provider_code:
            raise ValueError("observed registered provider/season is outside scope")
        season = seasons[0]
        stream = ObservedStreamV1(
            source_id=scope.subject.source_id,
            provider_code=provider.code,
            provider_fixture_namespace=mapping.external_namespace,
            provider_fixture_key=mapping.external_match_id,
            internal_match_id=match.internal_match_id,
        )
        self._cross_basis(session, stream, provider.provider_id, scope.scope_id)
        predecessor = heads.get(stream.stream_id)
        if metadata is None:
            metadata = self._capture_metadata(session, scope, rights, (item,), at)
        if predecessor is not None and (
            metadata[item.result.capture_receipt_id][1]
            <= predecessor.subject.result_capture.capture_ordinal
            or any(
                metadata[getattr(item, role).capture_receipt_id][1]
                < getattr(predecessor.subject, f"{role}_capture").capture_ordinal
                for role in ("fixture", "season", "result")
            )
        ):
            raise ValueError(
                "observed correction requires advancing actual local capture ordinals"
            )
        receipts, inspected, captures = {}, {}, {}
        for role in ("fixture", "season", "result"):
            ref = getattr(item, role)
            if ref.capture_receipt_id not in receipts:
                receipts[ref.capture_receipt_id] = self.admission_repository._capture(
                    session, ref.capture_receipt_id
                )
            receipt, payload = receipts[ref.capture_receipt_id]
            if receipt != metadata[ref.capture_receipt_id][0]:
                raise ValueError(
                    "observed capture differs from authorized metadata preflight"
                )
            document = strict_json_bytes(payload)
            data = document.get("data") if isinstance(document, dict) else None
            record_count = (
                len(data) if isinstance(data, list) else int(isinstance(data, dict))
            )
            if not 1 <= record_count <= 50:
                raise ValueError(
                    "observed capture requires 1 to 50 original data records"
                )
            if isinstance(data, list):
                keys = [
                    row.get("id") if isinstance(row, dict) else None for row in data
                ]
                if any(type(key) is not int or key <= 0 for key in keys) or len(
                    set(keys)
                ) != len(keys):
                    raise ValueError(
                        "observed capture requires unique original fixture IDs; duplicate/conflicting source rows"
                    )
            native = inspect_observed_fixture(
                payload,
                expected_league_id=int(scope.subject.provider_competition_id),
                expected_season_id=int(season.provider_season_id),
                captured_at_utc=receipt.local_imported_at_utc,
                record_pointer=ref.record_pointer,
            )
            if (
                ref.record_pointer != native["field_evidence"]["record_pointer"]
                or native["field_evidence"]["raw_payload_sha256"]
                != receipt.payload_sha256
            ):
                raise ValueError(
                    "observed role requires the exact native record pointer and original bytes"
                )
            inspected[role] = ObservedFixtureV1.model_validate(native)
            captures[role] = ObservedCaptureEvidenceV1(
                **ref.model_dump(),
                receipt_hash=receipt.receipt_hash,
                payload_sha256=receipt.payload_sha256,
                record_sha256=provider_record_sha256(
                    json_pointer(document, ref.record_pointer)
                ),
                outcome_sha256=inspected[role].outcome_sha256,
                capture_ordinal=metadata[ref.capture_receipt_id][1],
                capture_observed_at_utc=receipt.local_imported_at_utc,
                capture_registered_at_utc=receipt.registered_at_utc,
                capture_record_count=record_count,
            )
        raw = inspected["result"]
        identity_fields = (
            "provider_fixture_key",
            "provider_competition_id",
            "provider_season_id",
            "provider_home_team_id",
            "provider_away_team_id",
            "kickoff_at_utc",
        )
        if (
            any(
                tuple(getattr(x, name) for name in identity_fields)
                != tuple(getattr(raw, name) for name in identity_fields)
                for x in inspected.values()
            )
            or raw.provider_fixture_key not in season.included_fixture_ids
        ):
            raise ValueError("observed role join conflicts with exact declared cohort")
        if any(x.outcome_sha256 != raw.outcome_sha256 for x in inspected.values()):
            raise ValueError("observed full-fixture roles have contradictory outcomes")
        if item.original_http_metadata is not None:
            self.evidence.read(
                item.original_http_metadata.evidence_reference,
                item.original_http_metadata.evidence_sha256,
            )
        capture_at = max(x.capture_observed_at_utc for x in captures.values())
        resolved_home, resolved_away, identity_evidence = self._identities(
            session,
            item,
            raw,
            mapping,
            provider,
            match,
            canonical,
            scope,
            min(x.capture_observed_at_utc for x in captures.values()),
            capture_at,
            predecessor,
            identity_evidence,
        )
        identity = TrainingCanonicalMatchIdentityV1(
            internal_match_id=match.internal_match_id,
            internal_competition_id=match.competition_id,
            internal_home_team_id=match.home_team_id,
            internal_away_team_id=match.away_team_id,
            season=canonical.season,
            competition_type=canonical.competition_type,
            kickoff_at_utc=match.kickoff_at_utc,
        )
        if predecessor is not None and (
            predecessor.identity != identity
            or predecessor.capture_observed_at_utc > capture_at
            or predecessor.registered_at_utc > at
            or any(
                captures[role].capture_observed_at_utc
                < getattr(
                    predecessor.subject, f"{role}_capture"
                ).capture_observed_at_utc
                for role in captures
            )
        ):
            raise ValueError(
                "observed correction must preserve canonical anchor and advance local chronology"
            )
        return ObservedSnapshotSubjectV1(
            source_data_mode=scope.subject.source_data_mode,
            retrospective=scope.subject.retrospective,
            scope_id=scope.scope_id,
            scope_hash=scope.content_hash,
            source_rights_admission_id=rights.source_rights_admission_id,
            source_rights_admission_hash=rights.admission_hash,
            stream=stream,
            identity=identity,
            input=item,
            identity_evidence=identity_evidence,
            provider_mapping=TrainingProviderMatchMappingV1(
                mapping_id=mapping.mapping_id,
                provider_code=provider.code,
                external_namespace=mapping.external_namespace,
                external_match_id=mapping.external_match_id,
                internal_match_id=mapping.internal_match_id,
                resolution_method=mapping.resolution_method,
                confidence=mapping.confidence,
                available_at_utc=mapping.available_at_utc,
            ),
            fixture_capture=captures["fixture"],
            season_capture=captures["season"],
            result_capture=captures["result"],
            inspection=raw,
            resolved_home_team_id=resolved_home,
            resolved_away_team_id=resolved_away,
            upstream_publication_at_utc=None,
            provider_finalized_at_utc=None,
            revision_sequence=predecessor.revision_sequence + 1 if predecessor else 0,
            predecessor_id=predecessor.version_id if predecessor else None,
            predecessor_hash=predecessor.content_hash if predecessor else None,
            previous_match_result_id=(
                predecessor.normalized_result.match_result_id
                if predecessor.normalized_result
                else predecessor.subject.previous_match_result_id
            )
            if predecessor
            else None,
        )

    def _identities(
        self,
        session,
        item,
        raw,
        mapping,
        provider,
        match,
        canonical,
        scope,
        capture_at,
        latest_capture_at,
        predecessor,
        evidence,
    ):
        if (
            (
                mapping.external_match_id,
                match.competition_id,
            )
            != (
                raw.provider_fixture_key,
                scope.subject.canonical_competition_id,
            )
            or canonical.competition_type not in {"LEAGUE", "DOMESTIC_LEAGUE"}
            or max(
                mapping.available_at_utc,
                match.available_at_utc,
                match.created_at_utc,
                canonical.available_at_utc,
            )
            > capture_at
        ):
            raise ValueError(
                "observed preexisting canonical identity mismatch or unavailable"
            )
        aliases = tuple(
            _required(session, ProviderTeamAliasRecord, key)
            for key in (item.home_team_alias_id, item.away_team_alias_id)
        )
        competition = _required(
            session, ProviderCompetitionMappingRecord, item.competition_mapping_id
        )
        # New heads query the complete current selection. Replay uses sealed source
        # references, but every referenced row must still match its predicate/hash.
        selections, references = {}, {}
        for name, model, predicate in (
            (
                "match_mappings",
                ProviderMatchMappingRecord,
                or_(
                    ProviderMatchMappingRecord.mapping_id == mapping.mapping_id,
                    ProviderMatchMappingRecord.supersedes_mapping_id
                    == mapping.mapping_id,
                ),
            ),
            (
                "team_aliases",
                ProviderTeamAliasRecord,
                or_(
                    ProviderTeamAliasRecord.alias_id.in_(
                        (item.home_team_alias_id, item.away_team_alias_id)
                    ),
                    (ProviderTeamAliasRecord.provider_id == provider.provider_id)
                    & (ProviderTeamAliasRecord.available_at_utc <= latest_capture_at)
                    & or_(
                        *(
                            (ProviderTeamAliasRecord.provider_team_id == raw_id)
                            & (ProviderTeamAliasRecord.team_type == alias.team_type)
                            for alias, raw_id in zip(
                                aliases,
                                (raw.provider_home_team_id, raw.provider_away_team_id),
                                strict=True,
                            )
                        )
                    ),
                ),
            ),
            (
                "competition_mappings",
                ProviderCompetitionMappingRecord,
                or_(
                    ProviderCompetitionMappingRecord.mapping_id
                    == item.competition_mapping_id,
                    (
                        ProviderCompetitionMappingRecord.provider_id
                        == provider.provider_id
                    )
                    & (
                        ProviderCompetitionMappingRecord.provider_competition_id
                        == raw.provider_competition_id
                    )
                    & (ProviderCompetitionMappingRecord.season == canonical.season)
                    & (
                        ProviderCompetitionMappingRecord.competition_type
                        == canonical.competition_type
                    )
                    & (
                        ProviderCompetitionMappingRecord.available_at_utc
                        <= latest_capture_at
                    ),
                ),
            ),
        ):
            primary_key = next(iter(model.__table__.primary_key.columns))
            query = select(model).where(predicate).order_by(primary_key)
            if evidence is not None:
                query = query.where(
                    primary_key.in_(x.record_id for x in getattr(evidence, name))
                )
            rows = tuple(session.scalars(query))
            references[name] = tuple(
                ObservedIdentitySourceRefV1(
                    record_id=getattr(row, primary_key.name),
                    record_sha256=tagged_canonical_sha256(
                        f"OBSERVED_IDENTITY_SOURCE_V1:{model.__table__.name}",
                        {c.name: getattr(row, c.name) for c in model.__table__.columns},
                    ),
                )
                for row in rows
            )
            if evidence is not None and references[name] != getattr(evidence, name):
                raise ValueError(
                    "observed identity source evidence differs from stored catalog rows"
                )
            selections[name] = rows
        evidence = ObservedIdentityEvidenceV1(**references)
        if (
            mapping.supersedes_mapping_id is not None
            or len(selections["match_mappings"]) != 1
        ):
            raise ValueError(
                "observed mapping correction needs an explicit reviewed identity path"
            )
        resolved = []
        for side, alias, raw_id in (
            ("home", aliases[0], raw.provider_home_team_id),
            ("away", aliases[1], raw.provider_away_team_id),
        ):
            team = _required(session, TeamRecord, alias.internal_team_id)
            if (
                alias.provider_id,
                alias.team_type,
            ) != (
                provider.provider_id,
                team.team_type,
            ) or alias.available_at_utc > capture_at:
                raise ValueError(
                    "observed preexisting provider team alias mismatch or unavailable"
                )
            if alias.provider_team_id != raw_id:
                if predecessor is not None and alias.alias_id == getattr(
                    predecessor.subject.input, f"{side}_team_alias_id"
                ):
                    # Preserve the changed native ID, but claim no new resolution.
                    # Only a reviewed withdrawal can use this predecessor reference.
                    resolved.append(None)
                    continue
                raise ValueError("observed preexisting provider team alias mismatch")
            targets = {
                row.internal_team_id
                for row in selections["team_aliases"]
                if row.provider_id == provider.provider_id
                and row.provider_team_id == raw_id
                and row.team_type == team.team_type
                and row.available_at_utc <= latest_capture_at
            }
            if targets != {alias.internal_team_id}:
                if predecessor is None:
                    raise ValueError("ambiguous observed provider team identity")
                resolved.append(None)
                continue
            resolved.append(alias.internal_team_id)
        if (
            competition.provider_id,
            competition.provider_competition_id,
            competition.internal_competition_id,
            competition.season,
            competition.competition_type,
        ) != (
            provider.provider_id,
            raw.provider_competition_id,
            match.competition_id,
            canonical.season,
            canonical.competition_type,
        ) or competition.available_at_utc > capture_at:
            raise ValueError(
                "observed preexisting provider competition mapping mismatch or unavailable"
            )
        targets = {
            row.internal_competition_id for row in selections["competition_mappings"]
        }
        if targets != {match.competition_id}:
            raise ValueError("ambiguous observed provider competition identity")
        return *resolved, evidence

    def _cross_basis(self, session, stream, provider_id, scope_id):
        bindings = session.scalar(
            select(TrainingFactBindingRecord.training_fact_binding_id)
            .join(
                ProviderMatchMappingRecord,
                ProviderMatchMappingRecord.mapping_id
                == TrainingFactBindingRecord.provider_mapping_id,
            )
            .where(
                ProviderMatchMappingRecord.provider_id == provider_id,
                or_(
                    TrainingFactBindingRecord.internal_match_id
                    == stream.internal_match_id,
                    (
                        ProviderMatchMappingRecord.external_namespace
                        == stream.provider_fixture_namespace
                    )
                    & (
                        ProviderMatchMappingRecord.external_match_id
                        == stream.provider_fixture_key
                    ),
                ),
            )
        )
        if bindings is not None:
            raise ValueError(
                "cross-basis historical stream requires an explicit reviewed path"
            )
        for row in session.scalars(
            select(ObservedSnapshotRecord).where(
                ObservedSnapshotRecord.provider_id == provider_id,
                or_(
                    ObservedSnapshotRecord.internal_match_id
                    == stream.internal_match_id,
                    (
                        ObservedSnapshotRecord.namespace
                        == stream.provider_fixture_namespace
                    )
                    & (
                        ObservedSnapshotRecord.fixture_key
                        == stream.provider_fixture_key
                    ),
                ),
            )
        ):
            _verify_row(row)
            if row.stream_id != stream.stream_id or row.scope_id != scope_id:
                raise ValueError(
                    "observed cohort stream already belongs to another source/scope/anchor"
                )
        for row in session.scalars(
            select(MatchResultRecord).where(
                MatchResultRecord.provider_id == provider_id,
                MatchResultRecord.internal_match_id == stream.internal_match_id,
            )
        ):
            binding = session.scalar(
                select(ObservedResultBindingRecord).where(
                    ObservedResultBindingRecord.match_result_id == row.match_result_id
                )
            )
            if binding is None:
                raise ValueError(
                    "cross-basis or unqualified normalized result stream cannot be converted"
                )
            _verify_row(binding)

    def _cohort(self, scope, previous, subjects, *, first):
        keys = [x.stream.provider_fixture_key for x in subjects]
        matches = [x.stream.internal_match_id for x in subjects]
        if not keys or len(set(keys)) != len(keys) or len(set(matches)) != len(matches):
            raise ValueError("duplicate/conflicting observed admission cohort")
        if not set(keys) <= set(scope.subject.cohort_ids) or (
            first and set(keys) != set(scope.subject.cohort_ids)
        ):
            raise ValueError(
                "first observed admission requires exact complete predeclared cohort with exceptions"
            )
        prior_results = {
            (
                x.subject.result_capture.capture_receipt_id,
                x.subject.result_capture.record_pointer,
            )
            for x in previous
        }
        if any(
            (x.result_capture.capture_receipt_id, x.result_capture.record_pointer)
            in prior_results
            for x in subjects
        ):
            raise ValueError(
                "duplicate observed result capture cannot manufacture a new local version"
            )
        all_subjects = [x.subject for x in previous] + list(subjects)
        anchors = {}
        for subject in all_subjects:
            identity = subject.identity
            anchor = (
                identity.internal_competition_id,
                identity.season,
                identity.internal_home_team_id,
                identity.internal_away_team_id,
                identity.kickoff_at_utc,
            )
            if (
                anchors.setdefault(anchor, identity.internal_match_id)
                != identity.internal_match_id
            ):
                raise ValueError(
                    "duplicate/conflicting observed canonical fixture cohort"
                )
        captures = {
            getattr(x, f"{role}_capture").capture_receipt_id
            for x in all_subjects
            for role in ("fixture", "season", "result")
        }
        if (
            len(captures) > scope.subject.max_capture_receipts
            or len(all_subjects) > scope.subject.max_snapshot_records
        ):
            raise ValueError("observed scope receipt/record limit exceeded")

    @sanitized_evidence_errors
    def load_admission(self, admission_id: str) -> ObservedSnapshotAdmissionV1:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            return self._load(session, admission_id)

    def _load(self, session, admission_id):
        row = _required(session, ObservedSnapshotAdmissionRecord, admission_id)
        _verify_row(row)
        scope, rights = self._scope(session, row.scope_id)
        admissions, _ = self._history(
            session, scope, rights, through_sequence=row.admission_sequence
        )
        if not admissions or admissions[-1].admission_id != admission_id:
            raise ValueError("observed admission missing from complete history")
        return admissions[-1]

    @sanitized_evidence_errors
    def load_context(self, scope_id: str) -> ObservedSnapshotContextV1:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            scope, rights = self._scope(session, scope_id)
            started = self._now()
            rights.assert_active_for(started, TRAINING_FACT_REQUIRED_USES)
            if started >= scope.subject.retention_deadline_utc:
                raise ValueError("observed scope retention deadline expired")
            _, records = self._history(session, scope, rights)
            # Validate the graph before the final I/O/clock gate. This projection is
            # not a caller-supplied proof flag and is never used to authorize writes.
            context = ObservedSnapshotContextV1(
                records=records, scope=scope, actual_at_utc=started
            )
            actual = self._finish(
                rights,
                scope.subject,
                not_before=started,
                attestations=(
                    scope.reviewer_attestation,
                    *(x.reviewer_attestation for x in records),
                ),
            )
            return ObservedSnapshotContextV1(
                records=context.records, scope=scope, actual_at_utc=actual
            )

    def _history(self, session, scope, rights, *, through_sequence=None):
        query = select(ObservedSnapshotAdmissionRecord).where(
            ObservedSnapshotAdmissionRecord.scope_id == scope.scope_id
        )
        if through_sequence is not None:
            query = query.where(
                ObservedSnapshotAdmissionRecord.admission_sequence <= through_sequence
            )
        admissions, records, heads = [], [], {}
        for row in session.scalars(
            query.order_by(ObservedSnapshotAdmissionRecord.admission_sequence)
        ):
            _verify_row(row)
            value = ObservedSnapshotAdmissionV1.model_validate(
                strict_json_bytes(row.artifact_json.encode())
            )
            if (
                value.admission_sequence != len(admissions)
                or value.scope_hash != scope.content_hash
                or (
                    admissions
                    and value.actual_started_at_utc < admissions[-1].registered_at_utc
                )
            ):
                raise ValueError("observed history admission sequence/scope mismatch")
            request_data = strict_json_bytes(row.request_json.encode())
            values = tuple(
                ObservedSnapshotSubmissionV1.model_validate(x)
                for x in request_data["submissions"]
            )
            request = _request(
                row.request_key,
                row.operator_id,
                scope_id=scope.scope_id,
                submissions=values,
            )
            _assert_row(row, _admission_row(value, request))
            if tuple(
                (x.subject, x.reviewer_attestation) for x in value.records
            ) != tuple(
                (x.subject, x.reviewer_attestation)
                for x in sorted(
                    values, key=lambda x: x.subject.stream.provider_fixture_key
                )
            ):
                raise ValueError("observed request/child graph mismatch")
            children = tuple(
                session.scalars(
                    select(ObservedSnapshotRecord).where(
                        ObservedSnapshotRecord.admission_id == value.admission_id
                    )
                )
            )
            if len(children) != len(value.records):
                raise ValueError("observed admission child count mismatch")
            keyed = {x.version_id: x for x in children}
            for record in value.records:
                actual = self._subject(
                    session,
                    scope,
                    rights,
                    record.subject.input,
                    heads,
                    value.actual_started_at_utc,
                    identity_evidence=record.subject.identity_evidence,
                )
                if actual != record.subject:
                    raise ValueError(
                        "stored observed snapshot differs from bytes, mappings or historical predecessor"
                    )
                self._review(
                    record.reviewer_attestation,
                    actual.schema_version,
                    actual.subject_hash,
                    actual.stream.source_id,
                    value.actual_started_at_utc,
                )
                for at in (value.verified_at_utc, value.registered_at_utc):
                    rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
                    if at >= scope.subject.retention_deadline_utc:
                        raise ValueError(
                            "stored observed admission exceeded retention deadline"
                        )
                provider = self._provider(session, record.stream.provider_code)
                child = keyed.get(record.version_id)
                if child is None:
                    raise ValueError("observed admission child missing")
                _verify_row(child)
                _assert_row(
                    child,
                    _snapshot_row(value.admission_id, record, provider.provider_id),
                )
                bindings = tuple(
                    session.scalars(
                        select(ObservedResultBindingRecord).where(
                            ObservedResultBindingRecord.version_id == record.version_id
                        )
                    )
                )
                if len(bindings) != int(record.normalized_result is not None):
                    raise ValueError("observed normalized child count mismatch")
                if bindings:
                    _verify_row(bindings[0])
                    _assert_row(bindings[0], _binding_row(record, provider.provider_id))
                    normalized = _required(
                        session,
                        MatchResultRecord,
                        record.normalized_result.match_result_id,
                    )
                    if any(
                        getattr(normalized, column.name)
                        != getattr(bindings[0], column.name)
                        for column in MatchResultRecord.__table__.columns
                    ):
                        raise ValueError(
                            "observed normalized result differs from admitted projection"
                        )
            self._cohort(
                scope,
                records,
                tuple(x.subject for x in value.records),
                first=not admissions,
            )
            records.extend(value.records)
            heads.update((x.stream.stream_id, x) for x in value.records)
            admissions.append(value)
        if records:
            ObservedSnapshotContextV1(
                records=tuple(records),
                scope=scope,
                actual_at_utc=max(x.registered_at_utc for x in records),
            )
        return tuple(admissions), tuple(records)

    def _provider(self, session, code):
        provider = session.scalar(
            select(ProviderRecord).where(ProviderRecord.code == code)
        )
        if provider is None:
            raise ValueError("observed lane requires a preexisting provider")
        return provider


def _authority(attestation):
    content = attestation.content_payload
    return LocalReviewEvidenceV1(
        evidence_reference=content.reviewer_authority_reference,
        evidence_sha256=content.authority_sha256,
    )


def _admission_row(value, request):
    return _row(
        ObservedSnapshotAdmissionRecord,
        **request,
        admission_id=value.admission_id,
        scope_id=value.scope_id,
        admission_sequence=value.admission_sequence,
        content_hash=value.content_hash,
        record_count=len(value.records),
        actual_started_at_utc=value.actual_started_at_utc,
        verified_at_utc=value.verified_at_utc,
        registered_at_utc=value.registered_at_utc,
        artifact_json=canonical_json(value),
    )


def _snapshot_row(admission_id, value, provider_id):
    s, i = value.subject, value.subject.input
    return _row(
        ObservedSnapshotRecord,
        admission_id=admission_id,
        scope_id=s.scope_id,
        version_id=value.version_id,
        content_hash=value.content_hash,
        subject_hash=s.subject_hash,
        stream_id=value.stream.stream_id,
        source_id=value.stream.source_id,
        source_rights_admission_id=value.source_rights_admission_id,
        provider_id=provider_id,
        namespace=value.stream.provider_fixture_namespace,
        fixture_key=value.stream.provider_fixture_key,
        internal_match_id=value.stream.internal_match_id,
        provider_mapping_id=i.provider_mapping_id,
        home_team_alias_id=i.home_team_alias_id,
        away_team_alias_id=i.away_team_alias_id,
        competition_mapping_id=i.competition_mapping_id,
        fixture_capture_id=s.fixture_capture.capture_receipt_id,
        season_capture_id=s.season_capture.capture_receipt_id,
        result_capture_id=s.result_capture.capture_receipt_id,
        revision_sequence=value.revision_sequence,
        predecessor_id=value.predecessor_id,
        capture_observed_at_utc=value.capture_observed_at_utc,
        verified_at_utc=value.verified_at_utc,
        registered_at_utc=value.registered_at_utc,
        evidence_basis=value.evidence_basis.value,
        trainable=s.trainable,
        upstream_publication_at_utc=s.upstream_publication_at_utc,
        provider_finalized_at_utc=s.provider_finalized_at_utc,
        match_result_id=value.normalized_result.match_result_id
        if value.normalized_result
        else None,
        artifact_json=canonical_json(value),
    )


def _binding_row(value, provider_id):
    result = value.normalized_result
    return _row(
        ObservedResultBindingRecord,
        version_id=value.version_id,
        stream_id=value.stream.stream_id,
        evidence_basis=value.evidence_basis.value,
        provider_id=provider_id,
        internal_match_id=result.match_id,
        provider_mapping_id=value.subject.input.provider_mapping_id,
        **result.model_dump(mode="python", exclude={"provider_code", "match_id"}),
        artifact_json=canonical_json(result),
    )
