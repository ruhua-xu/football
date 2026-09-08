"""Public controlled writer and historical/context reader for ADR-0008.

Install the explicit V2 runtime schema (or migrate) first. Public flow:
capture_local_json (V1 rights-gated capture), capture_reference, prepare, review
the exact intent_hash, admit. prepare is an inert preview, never authorization.
Every admit resolves the DB head again under BEGIN IMMEDIATE. No caller-supplied
predecessor snapshot/flag or locally invented timestamp authorizes a transition.

Provider reassignment to another canonical match is deliberately unsupported:
the old UNIQUE provider/namespace/key mapping is the enduring match anchor.
Registered team aliases and competition mappings supply corrected identity
metadata without overwriting that anchor. Only match_results stores scores for
normalized consumers. A nontrainable version has no normalized result.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from football_system.application.training_correction import (
    TrainingCorrectionJsonAdapterV2,
)
from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import normalize_utc, stable_id
from football_system.domain.training_admission import (
    TRAINING_FACT_REQUIRED_USES,
    NormalizedMatchResultRecordV1,
    ProviderResultStatusCategory,
    TrainingCanonicalMatchIdentityV1,
    TrainingProviderMatchMappingV1,
    SourceRightsAdmissionV1,
    TrainingFactAdmissionV1,
    TrainingFactBindingV1,
    tagged_canonical_sha256,
)
from football_system.domain.training_correction import (
    CORRECTION_INTENT_V2,
    CorrectionCaptureRefV2,
    CorrectionComponent,
    CorrectionComponentBindingV2,
    CorrectionEvidenceV2,
    CorrectionRefV2,
    CorrectionSnapshotV2,
    CorrectionStreamV2,
    SourceCorrectionV2,
    TrainingCorrectionAdmissionV2,
    TrainingCorrectionContentV2,
    TrainingCorrectionContextV2,
    TrainingCorrectionIntentV2,
    TrainingFactVersionV2,
    changed_components,
    assert_correction_chronology,
    base_component_bindings,
    source_version_reference,
)
from football_system.infrastructure.database.models import (
    CompetitionRecord,
    MatchResultRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
    TrainingCaptureReceiptRecord,
    TrainingFactAdmissionRecord,
    TrainingFactBindingRecord,
    SourceRightsAdmissionRecord,
    TRAINING_CORRECTION_TABLES,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
    SqlAlchemyTrainingAdmissionRepository,
    _lock,
    _request,
    _required,
    _verify_row,
)
from football_system.infrastructure.database.training_correction_schema import (
    CORRECTION_TABLES,
)
from football_system.infrastructure.files.training_evidence import (
    TrainingFactSubmissionV1,
    TrainingJsonAdapterV1,
    json_pointer,
    provider_record_sha256,
    sanitized_evidence_errors,
    strict_json_bytes,
    verified_provider_fields,
)

TABLES = TRAINING_CORRECTION_TABLES
STREAMS, ADMISSIONS, COMPONENTS, RESULTS = (
    TABLES[name] for name in CORRECTION_TABLES[1:]
)


def _ref(tag, payload, artifact_id=None):
    digest = tagged_canonical_sha256(tag, payload)
    return CorrectionRefV2(
        schema_version=tag,
        artifact_id=artifact_id or stable_id(tag, digest),
        content_hash=digest,
    )


def _sealed_row(table, **values):
    return {
        **values,
        "row_sha256": tagged_canonical_sha256(
            f"TRAINING_CORRECTION_SQL_V2:{table.name}", values
        ),
    }


def _verified_row(table, row):
    if row is None:
        raise ValueError(f"missing registered {table.name}")
    values = dict(row)
    digest = values.pop("row_sha256")
    if _sealed_row(table, **values)["row_sha256"] != digest:
        raise ValueError(f"stored {table.name} integrity mismatch")
    return row


class SqlAlchemyTrainingCorrectionRepository:
    def __init__(self, admission_repository: SqlAlchemyTrainingAdmissionRepository):
        self.admissions = admission_repository

    @sanitized_evidence_errors
    def capture_reference(
        self, receipt_id: str, *, record_pointer: str
    ) -> CorrectionCaptureRefV2:
        receipt, payload = self.admissions.load_capture(receipt_id)
        return CorrectionCaptureRefV2(
            capture_receipt_id=receipt_id,
            receipt_hash=receipt.receipt_hash,
            payload_sha256=receipt.payload_sha256,
            record_pointer=record_pointer,
            record_sha256=provider_record_sha256(
                json_pointer(strict_json_bytes(payload), record_pointer)
            ),
        )

    @sanitized_evidence_errors
    def prepare(
        self,
        *,
        predecessor_version_id: str,
        source_rights_admission_id: str,
        evidence: CorrectionEvidenceV2,
        match_result_id: str | None,
    ) -> TrainingCorrectionIntentV2:
        """Resolve and verify the preview, then review its exact intent_hash externally."""
        evidence = CorrectionEvidenceV2.model_validate(
            evidence.model_dump(mode="python")
        )
        with self.admissions._sessions.begin() as session:
            session.execute(sa.text("BEGIN"))
            previous = self._load_version(session, predecessor_version_id)
            self._assert_head(session, previous)
            at = self.admissions._now()
            rights = self.admissions._rights(session, source_rights_admission_id)
            rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
            snapshot, _ = self._extract(session, previous, rights, evidence, at)
            return self._intent(previous, rights, evidence, snapshot, match_result_id)

    @staticmethod
    def _intent(previous, rights, evidence, snapshot, result_id):
        if previous.snapshot.stream != snapshot.stream:
            raise ValueError(
                "canonical provider reassignment to a different match is unsupported"
            )
        assert_correction_chronology(previous.snapshot, snapshot)
        changes = changed_components(previous.snapshot, snapshot)
        if not changes:
            raise ValueError("correction has no changed components")
        if result_id == previous.latest_match_result_id:
            raise ValueError("correction must use a distinct normalized revision ID")
        return TrainingCorrectionIntentV2(
            source_rights_admission=CorrectionRefV2(
                schema_version=rights.schema_version,
                artifact_id=rights.source_rights_admission_id,
                content_hash=rights.admission_hash,
            ),
            predecessor=previous.reference,
            predecessor_components=previous.components,
            revision_sequence=previous.revision_sequence + 1,
            changed_components=changes,
            candidate=snapshot,
            evidence=evidence,
            match_result_id=result_id,
        )

    def _extract(self, session, previous, rights, evidence, at):
        stream = previous.snapshot.stream
        if stream.source_id not in rights.content_payload.rights_payload.source_ids:
            raise ValueError("correction source is outside approved rights scope")
        # Check the stored capture scope BEFORE rereading any provider bytes.
        captures, payloads = [], []
        for ref in (evidence.fixture, evidence.scope, evidence.result):
            row = _required(
                session, TrainingCaptureReceiptRecord, ref.capture_receipt_id
            )
            provider = _required(session, ProviderRecord, row.provider_id)
            if (row.source_rights_admission_id, row.source_id, provider.code) != (
                rights.source_rights_admission_id,
                stream.source_id,
                stream.provider_code,
            ):
                raise ValueError(
                    "captured correction source/provider/rights scope mismatch"
                )
            receipt, payload = self.admissions._capture(session, ref.capture_receipt_id)
            if (receipt.receipt_hash, receipt.payload_sha256) != (
                ref.receipt_hash,
                ref.payload_sha256,
            ):
                raise ValueError("correction capture reference mismatch")
            if receipt.registered_at_utc > at:
                raise ValueError("correction capture follows operation/review")
            captures.append(receipt)
            payloads.append(payload)
        adapter = TrainingCorrectionJsonAdapterV2.model_validate(
            strict_json_bytes(
                self.admissions.evidence.read(
                    evidence.adapter.evidence_reference,
                    evidence.adapter.evidence_sha256,
                )
            )
        )
        if (adapter.provider_code, adapter.provider_fixture_namespace) != (
            stream.provider_code,
            stream.provider_fixture_namespace,
        ):
            raise ValueError("correction adapter stream mismatch")
        raw_result = json_pointer(
            strict_json_bytes(payloads[2]), evidence.result.record_pointer
        )
        raw_status = json_pointer(raw_result, adapter.result.status)
        category = next(
            (
                rule.category
                for rule in adapter.status_rules
                if rule.raw_status == raw_status
            ),
            ProviderResultStatusCategory.AMBIGUOUS,
        )
        absent_scores = (
            "home_goals",
            "away_goals",
            "finalized_at_utc",
            "score_semantics",
        )
        f, s, r = [
            verified_provider_fields(
                payload,
                ref,
                paths,
                digest=ref.record_sha256,
                nullable_fields=(
                    "home_goals",
                    "away_goals",
                    "finalized_at_utc",
                    "score_semantics",
                )
                if index == 2
                else (),
                missing_fields=absent_scores
                if index == 2
                and category is not ProviderResultStatusCategory.REGULAR_TIME_FINAL
                else (),
            )
            for index, (payload, ref, paths) in enumerate(
                zip(
                    payloads,
                    (evidence.fixture, evidence.scope, evidence.result),
                    (adapter.fixture, adapter.scope, adapter.result),
                    strict=True,
                )
            )
        ]
        for value, capture in zip((f, s, r), captures, strict=True):
            if value["fixture_key"] != stream.provider_fixture_key or (
                value["competition_id"],
                value["season_id"],
            ) != (s["competition_id"], s["season_id"]):
                raise ValueError(
                    "correction fixture/competition/season records disagree"
                )
            if value["available_at_utc"] > capture.local_imported_at_utc:
                raise ValueError("provider publication follows actual local capture")
        if any(
            f[key] != r[key]
            for key in ("home_team_id", "away_team_id", "kickoff_at_utc")
        ):
            raise ValueError("corrected fixture and result identity evidence disagree")
        if (
            session.scalar(
                sa.select(MatchResultRecord.match_result_id)
                .join(
                    ProviderRecord,
                    ProviderRecord.provider_id == MatchResultRecord.provider_id,
                )
                .where(
                    ProviderRecord.code == stream.provider_code,
                    MatchResultRecord.source_result_key == r["result_key"],
                    MatchResultRecord.internal_match_id != stream.internal_match_id,
                )
            )
            is not None
        ):
            raise ValueError("provider logical result key belongs to a different match")
        if (
            session.scalar(
                sa.select(ADMISSIONS.c.correction_id)
                .join(STREAMS, STREAMS.c.stream_id == ADMISSIONS.c.stream_id)
                .join(
                    ProviderRecord, ProviderRecord.provider_id == STREAMS.c.provider_id
                )
                .where(
                    ProviderRecord.code == stream.provider_code,
                    STREAMS.c.stream_id != stream.stream_id,
                    sa.func.json_extract(
                        ADMISSIONS.c.artifact_json,
                        "$.content_payload.intent.candidate.provider_result_key",
                    )
                    == r["result_key"],
                )
            )
            is not None
        ):
            raise ValueError(
                "provider logical result key is owned by a different match stream"
            )
        mapping = _required(
            session, ProviderMatchMappingRecord, evidence.provider_mapping_id
        )
        provider = _required(session, ProviderRecord, mapping.provider_id)
        if (
            provider.code,
            mapping.external_namespace,
            mapping.external_match_id,
            mapping.internal_match_id,
        ) != (
            stream.provider_code,
            stream.provider_fixture_namespace,
            stream.provider_fixture_key,
            stream.internal_match_id,
        ) or mapping.mapping_id != previous.snapshot.provider_mapping.mapping_id:
            raise ValueError(
                "canonical provider reassignment to a different match/mapping is unsupported"
            )
        if mapping.supersedes_mapping_id is not None or session.scalar(
            sa.select(ProviderMatchMappingRecord.mapping_id).where(
                ProviderMatchMappingRecord.supersedes_mapping_id == mapping.mapping_id
            )
        ):
            raise ValueError("uncontrolled canonical provider mapping successor")
        if mapping.available_at_utc > min(
            f["available_at_utc"], s["available_at_utc"], r["available_at_utc"]
        ):
            raise ValueError("registered provider mapping is not source-visible")
        teams = []
        for alias_id, raw_team in (
            (evidence.home_team_alias_id, f["home_team_id"]),
            (evidence.away_team_alias_id, f["away_team_id"]),
        ):
            alias = _required(session, ProviderTeamAliasRecord, alias_id)
            team = _required(session, TeamRecord, alias.internal_team_id)
            if (alias.provider_id, alias.provider_team_id, alias.team_type) != (
                provider.provider_id,
                raw_team,
                team.team_type,
            ) or alias.available_at_utc > f["available_at_utc"]:
                raise ValueError(
                    "registered trusted team alias mismatch or unavailable"
                )
            targets = set(
                session.scalars(
                    sa.select(ProviderTeamAliasRecord.internal_team_id).where(
                        ProviderTeamAliasRecord.provider_id == provider.provider_id,
                        ProviderTeamAliasRecord.provider_team_id == raw_team,
                        ProviderTeamAliasRecord.team_type == team.team_type,
                        ProviderTeamAliasRecord.available_at_utc
                        <= f["available_at_utc"],
                    )
                )
            )
            if targets != {team.team_id}:
                raise ValueError("ambiguous registered trusted team aliases")
            teams.append(team.team_id)
        competition = _required(
            session, ProviderCompetitionMappingRecord, evidence.competition_mapping_id
        )
        _required(session, CompetitionRecord, competition.internal_competition_id)
        if (competition.provider_id, competition.provider_competition_id) != (
            provider.provider_id,
            s["competition_id"],
        ) or competition.available_at_utc > s["available_at_utc"]:
            raise ValueError("registered canonical competition/season mapping mismatch")
        targets = set(
            session.scalars(
                sa.select(
                    ProviderCompetitionMappingRecord.internal_competition_id
                ).where(
                    ProviderCompetitionMappingRecord.provider_id
                    == provider.provider_id,
                    ProviderCompetitionMappingRecord.provider_competition_id
                    == s["competition_id"],
                    ProviderCompetitionMappingRecord.season == competition.season,
                    ProviderCompetitionMappingRecord.competition_type
                    == competition.competition_type,
                    ProviderCompetitionMappingRecord.available_at_utc
                    <= s["available_at_utc"],
                )
            )
        )
        if targets != {competition.internal_competition_id}:
            raise ValueError("ambiguous canonical competition mapping")
        # Unknown/unmapped original statuses remain nontrainable, not fake FT.
        snapshot = CorrectionSnapshotV2(
            stream=stream,
            identity=TrainingCanonicalMatchIdentityV1(
                internal_match_id=stream.internal_match_id,
                internal_competition_id=competition.internal_competition_id,
                internal_home_team_id=teams[0],
                internal_away_team_id=teams[1],
                season=competition.season,
                competition_type=competition.competition_type,
                kickoff_at_utc=f["kickoff_at_utc"],
            ),
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
            provider_home_team_id=f["home_team_id"],
            provider_away_team_id=f["away_team_id"],
            provider_competition_id=s["competition_id"],
            provider_season_id=s["season_id"],
            home_team_alias_id=evidence.home_team_alias_id,
            away_team_alias_id=evidence.away_team_alias_id,
            competition_mapping_id=evidence.competition_mapping_id,
            season_mapping_version=adapter.season_mapping_version,
            mapping_policy_version=adapter.mapping_policy_version,
            status_mapping_version=adapter.status_mapping_version,
            provider_raw_status=r["status"],
            provider_status_category=category,
            raw_score_semantics=r["score_semantics"],
            regular_time_score_semantics=adapter.regular_time_score_semantics,
            home_goals=r["home_goals"],
            away_goals=r["away_goals"],
            provider_finalized_at_utc=r["finalized_at_utc"],
            source_observed_at_utc=r["observed_at_utc"],
            fixture_source_available_at_utc=f["available_at_utc"],
            mapping_source_available_at_utc=s["available_at_utc"],
            result_source_available_at_utc=r["available_at_utc"],
            provider_result_key=r["result_key"],
            provider_revision_id=r.get("revision_id"),
            provider_revision_order=r.get("revision_order"),
        )
        return snapshot, tuple(captures)

    @sanitized_evidence_errors
    def admit(
        self,
        *,
        request_key: str,
        intent: TrainingCorrectionIntentV2,
        reviewer_evidence,
        reviewer_authority,
    ) -> TrainingCorrectionAdmissionV2:
        intent = TrainingCorrectionIntentV2.model_validate(
            intent.model_dump(mode="python")
        )
        request = _request(
            request_key,
            self.admissions.operator_id,
            intent=intent,
            reviewer_evidence=reviewer_evidence,
            reviewer_authority=reviewer_authority,
        )
        with self.admissions._sessions.begin() as session:
            _lock(session)
            prior = (
                session.execute(
                    sa.select(ADMISSIONS).where(
                        sa.or_(
                            ADMISSIONS.c.request_key == request_key,
                            ADMISSIONS.c.request_sha256 == request["request_sha256"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if prior is not None:
                _verified_row(ADMISSIONS, prior)
                if any(prior[key] != value for key, value in request.items()):
                    raise ValueError("immutable correction retry request conflicts")
                self._load_version(session, prior["correction_id"])
                return TrainingCorrectionAdmissionV2.model_validate_json(
                    prior["artifact_json"]
                )
            started = self.admissions._now()
            rights = self.admissions._rights(
                session, intent.source_rights_admission.artifact_id
            )
            rights.assert_active_for(started, TRAINING_FACT_REQUIRED_USES)
            previous = self._load_version(session, intent.predecessor.artifact_id)
            self._assert_head(session, previous)
            snapshot, captures = self._extract(
                session, previous, rights, intent.evidence, started
            )
            expected = self._intent(
                previous, rights, intent.evidence, snapshot, intent.match_result_id
            )
            if intent != expected:
                raise ValueError(
                    "reviewed intent differs from exact stored predecessor/components or verified candidate"
                )
            review = self.admissions.evidence.review(
                evidence=reviewer_evidence,
                authority=reviewer_authority,
                schema=CORRECTION_INTENT_V2,
                digest=intent.intent_hash,
                source_ids=(snapshot.stream.source_id,),
                operator_id=self.admissions.operator_id,
                at_utc=started,
            )
            permission = self.admissions.evidence.load_authority(reviewer_authority)
            permission.assert_active_for(started)
            if review.reviewed_at_utc < max(
                previous.registered_at_utc, *(x.registered_at_utc for x in captures)
            ):
                raise ValueError(
                    "correction review must follow exact predecessor and captures"
                )
            # A later correction always needs a new result capture and review,
            # including an explicit restoration after a withdrawn head.
            if captures[2].local_imported_at_utc <= previous.registered_at_utc:
                raise ValueError(
                    "correction requires a new capture after predecessor registration"
                )
            components, normalized = self._projections(previous, intent)
            completed, registered = self.admissions._now(), self.admissions._now()
            for at in (completed, registered):
                rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
                permission.assert_active_for(at)
            value = TrainingCorrectionAdmissionV2.freeze(
                content_payload=TrainingCorrectionContentV2(
                    intent=intent,
                    reviewer_evidence=reviewer_evidence,
                    reviewer_authority=reviewer_authority,
                    reviewed_by=review.authorized_reviewer,
                    reviewed_at_utc=review.reviewed_at_utc,
                    actual_started_at_utc=started,
                    actual_completed_at_utc=completed,
                    registered_at_utc=registered,
                    local_imported_at_utc=max(
                        x.local_imported_at_utc for x in captures
                    ),
                    components=components,
                    normalized_result=normalized,
                    previous_match_result_id=previous.latest_match_result_id,
                )
            )
            self._ensure_stream(session, previous)
            for row in self._component_rows(value):
                session.execute(COMPONENTS.insert().values(**row))
            if normalized is not None:
                row = self._result_row(session, value)
                session.execute(RESULTS.insert().values(**row))
                # The ordinary historical helper intentionally retains its V1
                # same-key/strict-time refusal. This controlled bridge inserts
                # the SAME normalized table with its exact deferred authorization.
                session.add(
                    MatchResultRecord(
                        match_result_id=normalized.match_result_id,
                        internal_match_id=normalized.match_id,
                        provider_id=row["provider_id"],
                        provider_mapping_id=row["provider_mapping_id"],
                        home_goals=normalized.home_goals,
                        away_goals=normalized.away_goals,
                        observed_at_utc=normalized.observed_at_utc,
                        available_at_utc=normalized.available_at_utc,
                        ingested_at_utc=normalized.ingested_at_utc,
                        source_result_key=normalized.source_result_key,
                        payload_hash=normalized.payload_hash,
                        supersedes_match_result_id=normalized.supersedes_match_result_id,
                    )
                )
                session.flush()
            session.execute(
                ADMISSIONS.insert().values(**self._admission_row(value, request))
            )
            self._load_version(session, value.artifact_id)
            self.admissions._check_current_authorization(
                rights, not_before=registered, authorities=(reviewer_authority,)
            )
            return value

    @staticmethod
    def _projections(previous, intent):
        components = []
        for old in previous.components:
            if old.component not in intent.changed_components:
                components.append(old)
                continue
            snapshot = intent.candidate
            source_time = {
                CorrectionComponent.FIXTURE: snapshot.fixture_source_available_at_utc,
                CorrectionComponent.MAPPING: max(
                    snapshot.fixture_source_available_at_utc,
                    snapshot.mapping_source_available_at_utc,
                ),
                CorrectionComponent.SEASON: snapshot.mapping_source_available_at_utc,
                CorrectionComponent.STATUS: snapshot.result_source_available_at_utc,
                CorrectionComponent.RESULT: snapshot.result_source_available_at_utc,
            }[old.component]
            ref = _ref(
                "TRAINING_CORRECTION_COMPONENT_V2",
                {
                    "component": old.component,
                    "stream": snapshot.stream,
                    "revision_sequence": intent.revision_sequence,
                    "predecessor": old.reference,
                    "value": snapshot.component_value(old.component),
                    "evidence": intent.evidence,
                    "source_available_at_utc": source_time,
                },
            )
            components.append(
                CorrectionComponentBindingV2(
                    component=old.component,
                    reference=ref,
                    source_available_at_utc=source_time,
                )
            )
        normalized = None
        s = intent.candidate
        if s.trainable:
            normalized = NormalizedMatchResultRecordV1(
                match_result_id=intent.match_result_id,
                match_id=s.stream.internal_match_id,
                provider_code=s.stream.provider_code,
                home_goals=s.home_goals,
                away_goals=s.away_goals,
                observed_at_utc=s.source_observed_at_utc,
                available_at_utc=s.result_source_available_at_utc,
                ingested_at_utc=s.result_source_available_at_utc,
                source_result_key=s.provider_result_key,
                payload_hash=match_result_payload_sha256(s.home_goals, s.away_goals),
                supersedes_match_result_id=previous.latest_match_result_id,
            )
        return tuple(components), normalized

    @staticmethod
    def _admission_row(value, request):
        c, s = value.content_payload, value.content_payload.intent.candidate
        return _sealed_row(
            ADMISSIONS,
            correction_id=value.artifact_id,
            stream_id=s.stream.stream_id,
            predecessor_version_id=c.intent.predecessor.artifact_id,
            revision_sequence=c.intent.revision_sequence,
            content_hash=value.content_hash,
            registered_at_utc=c.registered_at_utc,
            source_available_at_utc=s.effective_source_available_at_utc,
            provider_revision_id=s.provider_revision_id,
            provider_revision_order=s.provider_revision_order,
            **request,
            artifact_json=canonical_json(value),
        )

    @staticmethod
    def _component_rows(value):
        return tuple(
            _sealed_row(
                COMPONENTS,
                correction_id=value.artifact_id,
                component=c.component.value,
                reference_id=c.reference.artifact_id,
                reference_hash=c.reference.content_hash,
                artifact_json=canonical_json(c),
            )
            for c in value.content_payload.components
        )

    @staticmethod
    def _result_row(session, value):
        result, snapshot = (
            value.content_payload.normalized_result,
            value.content_payload.intent.candidate,
        )
        provider = session.scalar(
            sa.select(ProviderRecord).where(ProviderRecord.code == result.provider_code)
        )
        return _sealed_row(
            RESULTS,
            correction_id=value.artifact_id,
            stream_id=snapshot.stream.stream_id,
            match_result_id=result.match_result_id,
            previous_match_result_id=result.supersedes_match_result_id,
            provider_id=provider.provider_id,
            internal_match_id=result.match_id,
            provider_mapping_id=snapshot.provider_mapping.mapping_id,
            home_goals=result.home_goals,
            away_goals=result.away_goals,
            observed_at_utc=result.observed_at_utc,
            available_at_utc=result.available_at_utc,
            ingested_at_utc=result.ingested_at_utc,
            source_result_key=result.source_result_key,
            payload_hash=result.payload_hash,
            artifact_json=canonical_json(result),
        )

    def _ensure_stream(self, session, previous, *, create=True):
        base = (
            previous
            if previous.revision_sequence == 0
            else self._base_version(
                session,
                previous.base_admission.artifact_id,
                previous.base_binding.artifact_id,
            )
        )
        stream = base.snapshot.stream
        provider = session.scalar(
            sa.select(ProviderRecord).where(ProviderRecord.code == stream.provider_code)
        )
        expected = _sealed_row(
            STREAMS,
            stream_id=stream.stream_id,
            source_id=stream.source_id,
            provider_id=provider.provider_id,
            namespace=stream.provider_fixture_namespace,
            fixture_key=stream.provider_fixture_key,
            internal_match_id=stream.internal_match_id,
            base_admission_id=base.base_admission.artifact_id,
            base_binding_id=base.base_binding.artifact_id,
            base_version_id=base.version_id,
            artifact_json=canonical_json(base),
        )
        stored = (
            session.execute(
                sa.select(STREAMS).where(STREAMS.c.stream_id == stream.stream_id)
            )
            .mappings()
            .first()
        )
        if stored is None:
            if not create:
                raise ValueError("missing registered correction stream")
            session.execute(STREAMS.insert().values(**expected))
        elif dict(_verified_row(STREAMS, stored)) != expected:
            raise ValueError("correction stream base snapshot/projection mismatch")

    def _assert_head(self, session, previous):
        rows = (
            session.execute(
                sa.select(ADMISSIONS)
                .where(ADMISSIONS.c.stream_id == previous.snapshot.stream.stream_id)
                .order_by(ADMISSIONS.c.revision_sequence)
            )
            .mappings()
            .all()
        )
        for row in rows:
            _verified_row(ADMISSIONS, row)
        head = (
            rows[-1]["correction_id"] if rows else previous.base_admission.artifact_id
        )
        if (rows and head != previous.version_id) or (
            not rows and previous.revision_sequence != 0
        ):
            raise ValueError(
                "stale predecessor: correction must extend the actual DB head, no branch"
            )
        self._assert_results(session, previous, rows)

    def _assert_results(self, session, base, rows):
        provider = session.scalar(
            sa.select(ProviderRecord).where(
                ProviderRecord.code == base.snapshot.stream.provider_code
            )
        )
        actual = set(
            session.scalars(
                sa.select(MatchResultRecord.match_result_id).where(
                    MatchResultRecord.provider_id == provider.provider_id,
                    MatchResultRecord.internal_match_id
                    == base.snapshot.stream.internal_match_id,
                )
            )
        )
        root = (
            base
            if base.revision_sequence == 0
            else self._base_version(
                session, base.base_admission.artifact_id, base.base_binding.artifact_id
            )
        )
        expected = {root.latest_match_result_id}
        for row in rows:
            value = TrainingCorrectionAdmissionV2.model_validate_json(
                row["artifact_json"]
            )
            if value.content_payload.normalized_result is not None:
                expected.add(value.content_payload.normalized_result.match_result_id)
        if actual != expected:
            raise ControlledTrainingCorrectionRequired(
                "unregistered result successor corruption; controlled implementation is missing for this row"
            )

    @sanitized_evidence_errors
    def load_version(self, version_id: str) -> TrainingFactVersionV2:
        with self.admissions._sessions.begin() as session:
            session.execute(sa.text("BEGIN"))
            return self._load_version(session, version_id)

    @sanitized_evidence_errors
    def load_admission(self, correction_id: str) -> TrainingCorrectionAdmissionV2:
        """Full exact review, rights and capture refs for the version's audit graph."""
        with self.admissions._sessions.begin() as session:
            session.execute(sa.text("BEGIN"))
            version = self._load_version(session, correction_id)
            if not version.revision_sequence:
                raise ValueError("base version is not a correction admission")
            row = (
                session.execute(
                    sa.select(ADMISSIONS).where(
                        ADMISSIONS.c.correction_id == correction_id
                    )
                )
                .mappings()
                .one()
            )
            return TrainingCorrectionAdmissionV2.model_validate_json(
                row["artifact_json"]
            )

    def _load_version(self, session, version_id):
        # Walk references iteratively, then verify oldest to newest. No recursive
        # embedding and no supplied predecessor object is accepted as proof.
        chain, seen = [], set()
        cursor = version_id
        while True:
            if cursor in seen:
                raise ValueError("correction predecessor cycle")
            seen.add(cursor)
            row = (
                session.execute(
                    sa.select(ADMISSIONS).where(ADMISSIONS.c.correction_id == cursor)
                )
                .mappings()
                .first()
            )
            if row is None:
                break
            _verified_row(ADMISSIONS, row)
            chain.append(row)
            cursor = row["predecessor_version_id"]
        base = self._find_base(session, cursor)
        previous = base
        for row in reversed(chain):
            value = TrainingCorrectionAdmissionV2.model_validate_json(
                row["artifact_json"]
            )
            c = value.content_payload
            request_data = strict_json_bytes(row["request_json"].encode())
            expected_request = _request(
                row["request_key"],
                row["operator_id"],
                intent=c.intent,
                reviewer_evidence=c.reviewer_evidence,
                reviewer_authority=c.reviewer_authority,
            )
            if request_data != strict_json_bytes(
                expected_request["request_json"].encode()
            ) or dict(row) != self._admission_row(value, expected_request):
                raise ValueError(
                    "stored correction admission request/projection mismatch"
                )
            rights = self.admissions._rights(
                session, c.intent.source_rights_admission.artifact_id
            )
            for at in (
                c.actual_started_at_utc,
                c.actual_completed_at_utc,
                c.registered_at_utc,
            ):
                rights.assert_active_for(at, TRAINING_FACT_REQUIRED_USES)
                self.admissions.evidence.load_authority(
                    c.reviewer_authority
                ).assert_active_for(at)
            snapshot, captures = self._extract(
                session, previous, rights, c.intent.evidence, c.actual_started_at_utc
            )
            if (
                self._intent(
                    previous,
                    rights,
                    c.intent.evidence,
                    snapshot,
                    c.intent.match_result_id,
                )
                != c.intent
            ):
                raise ValueError(
                    "stored correction predecessor/component/candidate mismatch"
                )
            review = self.admissions.evidence.review(
                evidence=c.reviewer_evidence,
                authority=c.reviewer_authority,
                schema=CORRECTION_INTENT_V2,
                digest=c.intent.intent_hash,
                source_ids=(snapshot.stream.source_id,),
                operator_id=row["operator_id"],
                at_utc=c.actual_started_at_utc,
            )
            if (review.authorized_reviewer, review.reviewed_at_utc) != (
                c.reviewed_by,
                c.reviewed_at_utc,
            ):
                raise ValueError("stored correction review mismatch")
            if (
                review.reviewed_at_utc
                < max(
                    previous.registered_at_utc, *(x.registered_at_utc for x in captures)
                )
                or captures[2].local_imported_at_utc <= previous.registered_at_utc
                or c.local_imported_at_utc
                != max(x.local_imported_at_utc for x in captures)
            ):
                raise ValueError("stored correction capture/review timeline mismatch")
            components, normalized = self._projections(previous, c.intent)
            if (components, normalized, previous.latest_match_result_id) != (
                c.components,
                c.normalized_result,
                c.previous_match_result_id,
            ):
                raise ValueError("stored correction fact binding mismatch")
            stored_components = (
                session.execute(
                    sa.select(COMPONENTS).where(
                        COMPONENTS.c.correction_id == value.artifact_id
                    )
                )
                .mappings()
                .all()
            )
            expected_components = {
                r["component"]: r for r in self._component_rows(value)
            }
            if len(stored_components) != 5 or any(
                dict(_verified_row(COMPONENTS, r))
                != expected_components[r["component"]]
                for r in stored_components
            ):
                raise ValueError("stored correction component projection mismatch")
            result_row = (
                session.execute(
                    sa.select(RESULTS).where(
                        RESULTS.c.correction_id == value.artifact_id
                    )
                )
                .mappings()
                .first()
            )
            if normalized is None:
                if result_row is not None:
                    raise ValueError(
                        "nontrainable correction has a normalized row binding"
                    )
            else:
                if dict(_verified_row(RESULTS, result_row)) != self._result_row(
                    session, value
                ):
                    raise ValueError("stored controlled result binding mismatch")
                stored = _required(
                    session, MatchResultRecord, normalized.match_result_id
                )
                expected_result = self._result_row(session, value)
                if (
                    any(
                        getattr(stored, key) != expected_result[key]
                        for key in (
                            "match_result_id",
                            "internal_match_id",
                            "provider_id",
                            "provider_mapping_id",
                            "home_goals",
                            "away_goals",
                            "observed_at_utc",
                            "available_at_utc",
                            "ingested_at_utc",
                            "source_result_key",
                            "payload_hash",
                        )
                    )
                    or stored.supersedes_match_result_id
                    != normalized.supersedes_match_result_id
                ):
                    raise ValueError("stored corrected normalized result mismatch")
                predecessor_result = _required(
                    session, MatchResultRecord, previous.latest_match_result_id
                )
                if (
                    predecessor_result.provider_id != stored.provider_id
                    or predecessor_result.internal_match_id != stored.internal_match_id
                    or predecessor_result.available_at_utc > stored.available_at_utc
                    or predecessor_result.ingested_at_utc > stored.ingested_at_utc
                ):
                    raise ValueError("stored result provider/match chronology mismatch")
            previous = TrainingFactVersionV2(
                reference=value.reference(),
                base_admission=base.base_admission,
                base_binding=base.base_binding,
                revision_sequence=c.intent.revision_sequence,
                predecessor=c.intent.predecessor,
                snapshot=snapshot,
                components=components,
                normalized_result=normalized,
                latest_match_result_id=normalized.match_result_id
                if normalized
                else previous.latest_match_result_id,
                registered_at_utc=c.registered_at_utc,
            )
        if chain:
            self._ensure_stream(session, base, create=False)
        all_rows = (
            session.execute(
                sa.select(ADMISSIONS)
                .where(ADMISSIONS.c.stream_id == base.snapshot.stream.stream_id)
                .order_by(ADMISSIONS.c.revision_sequence)
            )
            .mappings()
            .all()
        )
        registered_root = (
            session.execute(
                sa.select(STREAMS).where(
                    STREAMS.c.stream_id == base.snapshot.stream.stream_id
                )
            )
            .mappings()
            .one_or_none()
        )
        stream_base = base
        if registered_root is not None:
            _verified_row(STREAMS, registered_root)
            if registered_root["base_version_id"] != base.version_id:
                stream_base = self._base_version(
                    session,
                    registered_root["base_admission_id"],
                    registered_root["base_binding_id"],
                )
                self._assert_base_alias(session, base, stream_base)
            self._ensure_stream(session, stream_base, create=False)
        ids = [stream_base.version_id]
        for index, row in enumerate(all_rows, 1):
            _verified_row(ADMISSIONS, row)
            if (
                row["revision_sequence"] != index
                or row["predecessor_version_id"] != ids[-1]
            ):
                raise ValueError(
                    "stored correction stream has a missing predecessor, fork or cycle"
                )
            ids.append(row["correction_id"])
        self._assert_results(session, stream_base, all_rows)
        return previous

    def _assert_base_alias(self, session, historical, registered):
        """Only the local V1 sequence/parent may differ; verify actual stored inputs."""
        sources, submissions = [], []
        for version in (historical, registered):
            if version.revision_sequence != 0:
                raise ValueError("only original base pins can be source aliases")
            # _base_version has already reverified the parent graph and raw bytes.
            # Fetch the full fact rather than comparing its partial snapshot.
            row = session.execute(
                sa.select(TrainingFactBindingRecord).where(
                    TrainingFactBindingRecord.training_fact_admission_id
                    == version.base_admission.artifact_id,
                    TrainingFactBindingRecord.training_fact_binding_id
                    == version.base_binding.artifact_id,
                )
            ).scalar_one()
            _verify_row(row)
            fact = TrainingFactBindingV1.model_validate_json(row.artifact_json)
            if version.base_binding != CorrectionRefV2(
                schema_version=fact.schema_version,
                artifact_id=fact.training_fact_binding_id,
                content_hash=fact.fact_hash,
            ):
                raise ValueError(
                    "historical base binding pin differs from stored content"
                )
            sources.append(source_version_reference(fact))
            parent = _required(
                session, TrainingFactAdmissionRecord, version.base_admission.artifact_id
            )
            _verify_row(parent)
            request = strict_json_bytes(parent.request_json.encode("utf-8"))
            submissions.append(
                next(
                    TrainingFactSubmissionV1.model_validate(item)
                    for item in request["submissions"]
                    if item["candidate"]["normalized_result"]["match_result_id"]
                    == fact.content_payload.normalized_result.match_result_id
                )
            )
        if (
            sources[0] != sources[1]
            or submissions[0] != submissions[1]
            or historical.snapshot != registered.snapshot
            or historical.components != registered.components
            or historical.normalized_result != registered.normalized_result
        ):
            raise ValueError(
                "historical base pin differs from the registered stream fact or evidence"
            )

    def _find_base(self, session, version_id):
        stream = (
            session.execute(
                sa.select(STREAMS).where(STREAMS.c.base_version_id == version_id)
            )
            .mappings()
            .first()
        )
        if stream is not None:
            _verified_row(STREAMS, stream)
            return self._base_version(
                session, stream["base_admission_id"], stream["base_binding_id"]
            )
        # Before a stream's first correction there is deliberately no registration
        # side effect. Base IDs are derived from the exact parent AND child pin.
        for row in session.scalars(sa.select(TrainingFactBindingRecord)):
            parent = _required(
                session, TrainingFactAdmissionRecord, row.training_fact_admission_id
            )
            binding = strict_json_bytes(row.artifact_json.encode())
            ref = _ref(
                "TRAINING_BASE_FACT_VERSION_V2",
                {
                    "admission_id": parent.training_fact_admission_id,
                    "admission_hash": parent.admission_hash,
                    "binding_id": row.training_fact_binding_id,
                    "fact_hash": binding.get("fact_hash"),
                },
            )
            if ref.artifact_id == version_id:
                return self._base_version(
                    session,
                    parent.training_fact_admission_id,
                    row.training_fact_binding_id,
                )
        raise ValueError("missing registered predecessor/base fact version")

    def _base_version(self, session, admission_id, binding_id):
        admission = self.admissions._load(session, admission_id, _historical_root=True)
        fact = next(
            (x for x in admission.facts if x.training_fact_binding_id == binding_id),
            None,
        )
        if fact is None:
            raise ValueError("base admission does not contain exact binding")
        parent_row = _required(session, TrainingFactAdmissionRecord, admission_id)
        requests = strict_json_bytes(parent_row.request_json.encode())
        submission = next(
            TrainingFactSubmissionV1.model_validate(x)
            for x in requests["submissions"]
            if x["candidate"]["normalized_result"]["match_result_id"]
            == fact.content_payload.normalized_result.match_result_id
        )
        c, e = fact.content_payload, submission.source_evidence
        f, m, r = (
            c.fixture_source,
            c.season_membership.content_payload,
            c.match_result_admission.content_payload,
        )
        adapter = TrainingJsonAdapterV1.model_validate(
            strict_json_bytes(
                self.admissions.evidence.read(
                    e.adapter.evidence_reference, e.adapter.evidence_sha256
                )
            )
        )
        payload = self.admissions._capture(session, e.fixture.capture_receipt_id)[1]
        raw = verified_provider_fields(
            payload, e.fixture, adapter.fixture, digest=f.fixture_record_sha256
        )
        snapshot = CorrectionSnapshotV2(
            stream=CorrectionStreamV2(
                source_id=f.source_id,
                provider_code=f.provider_code,
                provider_fixture_namespace=f.provider_fixture_namespace,
                provider_fixture_key=f.provider_fixture_key,
                internal_match_id=f.internal_match_id,
            ),
            identity=c.canonical_identity,
            provider_mapping=c.provider_mapping,
            provider_home_team_id=raw["home_team_id"],
            provider_away_team_id=raw["away_team_id"],
            provider_competition_id=m.provider_competition_id,
            provider_season_id=m.provider_season_id,
            home_team_alias_id=e.home_team_alias_id,
            away_team_alias_id=e.away_team_alias_id,
            competition_mapping_id=e.competition_mapping_id,
            season_mapping_version=m.season_mapping_version,
            mapping_policy_version=m.mapping_policy_version,
            status_mapping_version=r.status_mapping_version,
            provider_raw_status=r.provider_raw_status,
            provider_status_category=r.provider_status_category,
            raw_score_semantics=adapter.regular_time_score_semantics,
            regular_time_score_semantics=adapter.regular_time_score_semantics,
            home_goals=r.regular_time_home_goals,
            away_goals=r.regular_time_away_goals,
            provider_finalized_at_utc=r.provider_finalized_at_utc,
            source_observed_at_utc=r.source_observed_at_utc,
            fixture_source_available_at_utc=f.source_available_at_utc,
            mapping_source_available_at_utc=m.source_available_at_utc,
            result_source_available_at_utc=r.source_available_at_utc,
            provider_result_key=r.provider_result_key,
        )
        base_ref = _ref(
            "TRAINING_BASE_FACT_VERSION_V2",
            {
                "admission_id": admission_id,
                "admission_hash": admission.admission_hash,
                "binding_id": binding_id,
                "fact_hash": fact.fact_hash,
            },
        )
        base_binding = CorrectionRefV2(
            schema_version=fact.schema_version,
            artifact_id=binding_id,
            content_hash=fact.fact_hash,
        )
        components = base_component_bindings(base_binding, fact)
        return TrainingFactVersionV2(
            reference=base_ref,
            base_admission=CorrectionRefV2(
                schema_version=admission.schema_version,
                artifact_id=admission_id,
                content_hash=admission.admission_hash,
            ),
            base_binding=base_binding,
            revision_sequence=0,
            predecessor=None,
            snapshot=snapshot,
            components=components,
            normalized_result=c.normalized_result,
            latest_match_result_id=c.normalized_result.match_result_id,
            registered_at_utc=admission.content_payload.persisted_at_utc,
        )

    @sanitized_evidence_errors
    def load_context(
        self,
        *,
        base_admissions: tuple[CorrectionRefV2, ...],
        correction_ids: tuple[str, ...],
        actual_at: datetime,
    ) -> TrainingCorrectionContextV2:
        """Pinned closed history, never latest-by-label. Use context.select(cutoffs)."""
        actual_at = normalize_utc(actual_at)
        if not base_admissions:
            raise ValueError("context requires exact base admission pins")
        if len(set(correction_ids)) != len(correction_ids) or len(
            {p.artifact_id for p in base_admissions}
        ) != len(base_admissions):
            raise ValueError("context pins must be unique")
        with self.admissions._sessions.begin() as session:
            session.execute(sa.text("BEGIN"))
            versions, events, seen_streams = [], [], set()
            base_artifacts, correction_artifacts = [], []
            for pin in base_admissions:
                admission = self.admissions._load(session, pin.artifact_id)
                if pin != CorrectionRefV2(
                    schema_version=admission.schema_version,
                    artifact_id=admission.training_fact_admission_id,
                    content_hash=admission.admission_hash,
                ):
                    raise ValueError("context base admission pin mismatch")
                base_artifacts.append(admission)
                for fact in admission.facts:
                    base = self._base_version(
                        session, pin.artifact_id, fact.training_fact_binding_id
                    )
                    if base.snapshot.stream.stream_id in seen_streams:
                        raise ValueError(
                            "context mixes different base admission snapshots for one stream"
                        )
                    seen_streams.add(base.snapshot.stream.stream_id)
                    versions.append(base)
            loaded = [self._load_version(session, key) for key in correction_ids]
            for version in sorted(
                loaded, key=lambda v: (v.snapshot.stream.stream_id, v.revision_sequence)
            ):
                if version.revision_sequence == 0 or version.predecessor not in {
                    v.reference for v in versions
                }:
                    raise ValueError(
                        "context correction pins require the complete exact predecessor chain"
                    )
                versions.append(version)
                events.extend(self._events(session, version))
                row = (
                    session.execute(
                        sa.select(ADMISSIONS).where(
                            ADMISSIONS.c.correction_id == version.version_id
                        )
                    )
                    .mappings()
                    .one()
                )
                correction_artifacts.append(
                    TrainingCorrectionAdmissionV2.model_validate_json(
                        row["artifact_json"]
                    )
                )
            if any(v.registered_at_utc > actual_at for v in versions):
                raise ValueError(
                    "pinned fact version was not actually registered at context time"
                )
            return TrainingCorrectionContextV2(
                actual_at_utc=actual_at,
                versions=tuple(versions),
                corrections=tuple(events),
                base_artifacts=tuple(base_artifacts),
                correction_artifacts=tuple(correction_artifacts),
            )

    def _events(self, session, version):
        row = (
            session.execute(
                sa.select(ADMISSIONS).where(
                    ADMISSIONS.c.correction_id == version.version_id
                )
            )
            .mappings()
            .one()
        )
        value = TrainingCorrectionAdmissionV2.model_validate_json(row["artifact_json"])
        c = value.content_payload
        return tuple(
            SourceCorrectionV2(
                transition=value.reference(),
                stream=version.snapshot.stream,
                revision_sequence=version.revision_sequence,
                component=old.component,
                predecessor_version=c.intent.predecessor,
                successor_version=value.reference(),
                predecessor=old.reference,
                successor=new.reference,
                predecessor_source_available_at_utc=old.source_available_at_utc,
                source_available_at_utc=new.source_available_at_utc,
                local_imported_at_utc=c.local_imported_at_utc,
                registered_at_utc=c.registered_at_utc,
            )
            for old, new in zip(
                c.intent.predecessor_components, c.components, strict=True
            )
            if old.component in c.intent.changed_components
        )

    @sanitized_evidence_errors
    def invalidation_evidence(
        self, correction_id: str
    ) -> tuple[SourceCorrectionV2, ...]:
        with self.admissions._sessions.begin() as session:
            session.execute(sa.text("BEGIN"))
            version = self._load_version(session, correction_id)
            if not version.revision_sequence:
                raise ValueError("base version is not a correction")
            return self._events(session, version)

    @sanitized_evidence_errors
    def current_invalidations(
        self, *, base_admissions: tuple[CorrectionRefV2, ...], actual_at: datetime
    ) -> tuple[SourceCorrectionV2, ...]:
        """Complete DB-derived evidence for pinned facts, not a supplied source label.

        Registration and source visibility both gate an observation. Integration
        must compare these typed refs (or their as_v1 projections) with its pinned
        version component refs, not the legacy fixture pointer identifiers.
        """
        actual_at = normalize_utc(actual_at)
        if not base_admissions or len({p.artifact_id for p in base_admissions}) != len(
            base_admissions
        ):
            raise ValueError(
                "current evidence requires unique exact base admission pins"
            )
        with self.admissions._sessions.begin() as session:
            session.execute(sa.text("BEGIN"))
            streams = set()
            for pin in base_admissions:
                admission = self.admissions._load(session, pin.artifact_id)
                if (pin.schema_version, pin.content_hash) != (
                    admission.schema_version,
                    admission.admission_hash,
                ):
                    raise ValueError("current evidence base admission pin mismatch")
                if admission.content_payload.persisted_at_utc > actual_at:
                    raise ValueError(
                        "current evidence base was not registered at actual time"
                    )
                for fact in admission.facts:
                    base = self._base_version(
                        session, pin.artifact_id, fact.training_fact_binding_id
                    )
                    streams.add(base.snapshot.stream.stream_id)
            rows = (
                session.execute(
                    sa.select(ADMISSIONS)
                    .where(ADMISSIONS.c.stream_id.in_(streams))
                    .order_by(ADMISSIONS.c.stream_id, ADMISSIONS.c.revision_sequence)
                )
                .mappings()
                .all()
            )
            events = []
            for row in rows:
                _verified_row(ADMISSIONS, row)
                if row["registered_at_utc"] <= actual_at:
                    version = self._load_version(session, row["correction_id"])
                    events.extend(
                        e
                        for e in self._events(session, version)
                        if e.source_available_at_utc <= actual_at
                    )
            return tuple(events)


def assert_new_v1_roots_current(session, facts):
    """Only new V1 requests pass here; exact retries and audit loads do not."""
    if not session.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_correction_admissions'"
        )
    ).scalar():
        return
    for fact in facts:
        result = fact.content_payload.normalized_result
        if (
            session.scalar(
                sa.select(ADMISSIONS.c.correction_id)
                .join(STREAMS, STREAMS.c.stream_id == ADMISSIONS.c.stream_id)
                .join(
                    ProviderRecord, ProviderRecord.provider_id == STREAMS.c.provider_id
                )
                .where(
                    ProviderRecord.code == result.provider_code,
                    STREAMS.c.internal_match_id == result.match_id,
                )
            )
            is not None
        ):
            raise ControlledTrainingCorrectionRequired(
                "new V1 admission cannot reuse a corrected stream root"
            )


@sanitized_evidence_errors
def verify_controlled_normalized_stream(session, record) -> bool:
    """Database-only normalized audit read; not training/rights authorization.

    Verify registered version lineage instead of using source-key or timestamp
    order. Full source-byte and review verification remains load_version's job.
    This function never calls the V1 loader or _match_result, avoiding recursion
    when those readers verify the original admitted predecessor.
    """
    installed = session.info.get("training_correction_schema_present")
    if installed is None:
        installed = bool(
            session.execute(
                sa.text(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_correction_admissions'"
                )
            ).scalar()
        )
        session.info["training_correction_schema_present"] = installed
    if not installed:
        return False
    bindings = tuple(
        session.scalars(
            sa.select(TrainingFactBindingRecord)
            .join(
                ProviderMatchMappingRecord,
                ProviderMatchMappingRecord.mapping_id
                == TrainingFactBindingRecord.provider_mapping_id,
            )
            .where(
                ProviderMatchMappingRecord.provider_id == record.provider_id,
                TrainingFactBindingRecord.internal_match_id == record.internal_match_id,
            )
        )
    )
    if not bindings:
        return False
    actual = {
        row.match_result_id: row
        for row in session.scalars(
            sa.select(MatchResultRecord).where(
                MatchResultRecord.provider_id == record.provider_id,
                MatchResultRecord.internal_match_id == record.internal_match_id,
            )
        )
    }
    base_result_ids = {binding.match_result_id for binding in bindings}
    stream = (
        session.execute(
            sa.select(STREAMS).where(
                STREAMS.c.provider_id == record.provider_id,
                STREAMS.c.internal_match_id == record.internal_match_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if stream is None:
        if set(actual) != base_result_ids:
            raise ControlledTrainingCorrectionRequired(
                "unregistered result successor corruption; controlled implementation is missing for this row"
            )
        return False
    _verified_row(STREAMS, stream)
    base = TrainingFactVersionV2.model_validate_json(stream["artifact_json"])
    parent = _required(
        session, TrainingFactAdmissionRecord, stream["base_admission_id"]
    )
    _verify_row(parent)
    admission = TrainingFactAdmissionV1.model_validate_json(parent.artifact_json)
    binding = next(
        (
            b
            for b in bindings
            if (b.training_fact_admission_id, b.training_fact_binding_id)
            == (stream["base_admission_id"], stream["base_binding_id"])
        ),
        None,
    )
    if binding is None:
        raise ValueError("controlled stream is missing its exact V1 base binding")
    _verify_row(binding)
    fact = next(
        (
            f
            for f in admission.facts
            if f.training_fact_binding_id == binding.training_fact_binding_id
        ),
        None,
    )
    if fact is None or binding.artifact_json != canonical_json(fact):
        raise ValueError("controlled stream base admission/binding mismatch")
    expected_base_ref = _ref(
        "TRAINING_BASE_FACT_VERSION_V2",
        {
            "admission_id": admission.training_fact_admission_id,
            "admission_hash": admission.admission_hash,
            "binding_id": fact.training_fact_binding_id,
            "fact_hash": fact.fact_hash,
        },
    )
    if (
        base.reference != expected_base_ref
        or base.version_id != stream["base_version_id"]
        or base.revision_sequence != 0
        or base.predecessor is not None
        or base.snapshot.stream.stream_id != stream["stream_id"]
        or base.snapshot.identity != fact.content_payload.canonical_identity
        or base.snapshot.provider_mapping != fact.content_payload.provider_mapping
        or base.normalized_result != fact.content_payload.normalized_result
        or base.latest_match_result_id != base.normalized_result.match_result_id
        or base_result_ids != {base.latest_match_result_id}
    ):
        raise ValueError("controlled stream base normalized result/version mismatch")
    expected_results = {
        base.latest_match_result_id: (
            base.normalized_result,
            base.snapshot.provider_mapping.mapping_id,
        )
    }
    previous = base
    repository = SqlAlchemyTrainingCorrectionRepository
    for row in session.execute(
        sa.select(ADMISSIONS)
        .where(ADMISSIONS.c.stream_id == stream["stream_id"])
        .order_by(ADMISSIONS.c.revision_sequence)
    ).mappings():
        _verified_row(ADMISSIONS, row)
        value = TrainingCorrectionAdmissionV2.model_validate_json(row["artifact_json"])
        content = value.content_payload
        rights_row = _required(
            session,
            SourceRightsAdmissionRecord,
            content.intent.source_rights_admission.artifact_id,
        )
        _verify_row(rights_row)
        rights = SourceRightsAdmissionV1.model_validate_json(rights_row.artifact_json)
        if (
            repository._intent(
                previous,
                rights,
                content.intent.evidence,
                content.intent.candidate,
                content.intent.match_result_id,
            )
            != content.intent
        ):
            raise ValueError(
                "controlled result requires exact consecutive predecessor versions and components"
            )
        request = _request(
            row["request_key"],
            row["operator_id"],
            intent=content.intent,
            reviewer_evidence=content.reviewer_evidence,
            reviewer_authority=content.reviewer_authority,
        )
        if dict(row) != repository._admission_row(value, request):
            raise ValueError("controlled result admission projection mismatch")
        components, normalized = repository._projections(previous, content.intent)
        if (components, normalized, previous.latest_match_result_id) != (
            content.components,
            content.normalized_result,
            content.previous_match_result_id,
        ):
            raise ValueError("controlled result fact/component binding mismatch")
        expected_components = {
            c["component"]: c for c in repository._component_rows(value)
        }
        stored_components = (
            session.execute(
                sa.select(COMPONENTS).where(
                    COMPONENTS.c.correction_id == value.artifact_id
                )
            )
            .mappings()
            .all()
        )
        if len(stored_components) != 5 or any(
            dict(_verified_row(COMPONENTS, c)) != expected_components[c["component"]]
            for c in stored_components
        ):
            raise ValueError("controlled result component projection mismatch")
        result_binding = (
            session.execute(
                sa.select(RESULTS).where(RESULTS.c.correction_id == value.artifact_id)
            )
            .mappings()
            .one_or_none()
        )
        if normalized is None:
            if result_binding is not None:
                raise ValueError("nontrainable correction has a normalized result")
        else:
            if dict(_verified_row(RESULTS, result_binding)) != repository._result_row(
                session, value
            ):
                raise ValueError("controlled normalized result binding mismatch")
            expected_results[normalized.match_result_id] = (
                normalized,
                content.intent.candidate.provider_mapping.mapping_id,
            )
        previous = TrainingFactVersionV2(
            reference=value.reference(),
            base_admission=base.base_admission,
            base_binding=base.base_binding,
            revision_sequence=content.intent.revision_sequence,
            predecessor=content.intent.predecessor,
            snapshot=content.intent.candidate,
            components=components,
            normalized_result=normalized,
            latest_match_result_id=normalized.match_result_id
            if normalized
            else previous.latest_match_result_id,
            registered_at_utc=content.registered_at_utc,
        )
    if (
        set(actual) != set(expected_results)
        or record.match_result_id not in expected_results
    ):
        raise ControlledTrainingCorrectionRequired(
            "unregistered result successor corruption; controlled implementation is missing for this row"
        )
    provider = _required(session, ProviderRecord, record.provider_id)
    for result_id, (expected, mapping_id) in expected_results.items():
        row = actual[result_id]
        fields = expected.model_dump(
            exclude={"schema_version", "provider_code", "match_id"}
        )
        if (
            provider.code != expected.provider_code
            or row.internal_match_id != expected.match_id
            or row.provider_mapping_id != mapping_id
            or any(getattr(row, key) != value for key, value in fields.items())
        ):
            raise ValueError(
                "stored normalized result differs from controlled version lineage"
            )
        if expected.supersedes_match_result_id is not None:
            predecessor = actual.get(expected.supersedes_match_result_id)
            if (
                predecessor is None
                or predecessor.provider_id != row.provider_id
                or predecessor.internal_match_id != row.internal_match_id
                or predecessor.available_at_utc > row.available_at_utc
                or predecessor.ingested_at_utc > row.ingested_at_utc
            ):
                raise ValueError(
                    "controlled normalized result predecessor chronology mismatch"
                )
    return record.match_result_id != base.latest_match_result_id


def verify_historical_result_successors(admissions, session, admission):
    """V1 historical replay accepts controlled successors, never bare corruption."""
    for fact in admission.facts:
        result = fact.content_payload.normalized_result
        provider = session.scalar(
            sa.select(ProviderRecord).where(ProviderRecord.code == result.provider_code)
        )
        others = session.scalar(
            sa.select(MatchResultRecord.match_result_id).where(
                MatchResultRecord.provider_id == provider.provider_id,
                MatchResultRecord.internal_match_id == result.match_id,
                MatchResultRecord.match_result_id != result.match_result_id,
            )
        )
        if others is None:
            continue
        installed = session.execute(
            sa.text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_correction_admissions'"
            )
        ).scalar()
        if not installed:
            raise ControlledTrainingCorrectionRequired(
                "controlled result correction implementation is missing"
            )
        repo = SqlAlchemyTrainingCorrectionRepository(admissions)
        base = repo._base_version(
            session, admission.training_fact_admission_id, fact.training_fact_binding_id
        )
        head = (
            session.execute(
                sa.select(ADMISSIONS)
                .where(ADMISSIONS.c.stream_id == base.snapshot.stream.stream_id)
                .order_by(ADMISSIONS.c.revision_sequence.desc())
            )
            .mappings()
            .first()
        )
        if head is None:
            raise ControlledTrainingCorrectionRequired(
                "controlled result correction implementation is missing"
            )
        version = repo._load_version(session, head["correction_id"])
        if (
            version.base_admission != base.base_admission
            or version.base_binding != base.base_binding
        ):
            # Audit pins may differ by parent/local sequence, never by source data.
            registered_base = repo._base_version(
                session,
                version.base_admission.artifact_id,
                version.base_binding.artifact_id,
            )
            repo._assert_base_alias(session, base, registered_base)
