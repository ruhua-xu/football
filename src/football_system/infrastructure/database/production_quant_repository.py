"""Durable ADR-0008 graphs, with no reviewer impersonation or correction bypass.

Approval WRITING is intentionally blocked: the current pure V1 reviewer hash
includes the future repository persistence timestamp. A prior request approval
cannot honestly be rebound to that hash. Reads verify independently supplied
existing V1 approvals; no public API imports or fabricates an approved row.

Withdrawal is deliberately separate from activation: it verifies persisted target
seals/scope and fresh revocation review bytes, not the installed model, historical
source availability, or current permission to use the artifact being withdrawn.

The pilot technical bridge must verify actual persisted terminal evidence and
reject SYNTHETIC_CONTRACT_TEST_ONLY, not echo supplied history roots. Its optional
``session`` keyword is required here to participate in the same SQLite snapshot.
Admission verification uses its existing session-scoped loader unchanged.

SQLite write transactions are serialized before reads. Persistence timestamps
are actual UTC clock observations at transaction sealing, not predicted commit
times. A final authorization check runs immediately before commit. The database,
evidence root, clock and authority configuration remain trusted local boundaries.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from threading import Lock
from typing import Literal, Protocol

from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.production_release import (
    build_production_release,
    prepare_training_history,
)
from football_system.application.run_analysis import _code_revision
from football_system.domain.archive import canonical_json
from football_system.domain.common import Identifier, UtcDateTime, stable_id, utc_now
from football_system.domain.production_release import (
    PRODUCTION_CONFIG_HASH,
    TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
    ApprovalSuccessorContentV1,
    ApprovalSuccessorV1,
    CurrentAuthorizationInputsV1,
    EloTrainingWindowV1,
    GrantRevocationContentV1,
    GrantRevocationV1,
    HistorySourceSummaryV1,
    ProductionGrantKind,
    ProductionGrantV1,
    ProductionQuantModelReleaseV1,
    ProductionTargetAcceptancePlanContentV1,
    ProductionTargetAcceptancePlanV1,
    ProductionTargetV1,
    ReleaseArtifactRefV1,
    ReleaseSnapshotV1,
    RetentionHorizonV1,
    SourceCorrectionV1,
    TechnicalEvidenceRefsV1,
    TrainingHistoryApprovalV1,
    TrainingHistoryGraphV1,
    TrainingHistoryManifestContentV1,
    TrainingHistoryManifestV1,
    approval_active_for_build,
    assert_retention_authorized,
    assert_target_plan,
    revalidate,
    source_rights_refs,
)
from football_system.domain.training_admission import (
    LocalReviewEvidenceV1,
    RuleText,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database.models import (
    Base,
    CanonicalMatchIdentityRecord,
    MatchRecord,
    MatchResultRecord,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
    SqlAlchemyTrainingAdmissionRepository,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes


class ProductionPilotRepository(Protocol):
    def technical_evidence(
        self,
        attestation_id: str,
        history: TrainingHistoryGraphV1,
        *,
        session: Session | None = None,
    ) -> TechnicalEvidenceRefsV1: ...


class ApprovalRecordingContractConflict(ValueError):
    """Approval persistence needs a new reviewer/recording envelope contract."""


@contextmanager
def _verification_scope(session):
    """One read or operation boundary, never a transaction-wide evidence cache."""
    root = "production_verified_read" not in session.info
    if root:
        session.info["production_verified_read"] = {}
    try:
        yield session.info["production_verified_read"]
    finally:
        if root:
            del session.info["production_verified_read"]


def _verified_read(method):
    @wraps(method)
    def checked(self, session, *args, **kwargs):
        if (
            not session.in_transaction()
            or session.get_bind() is not self._sessions.kw["bind"]
        ):
            raise ValueError(
                "production reads require an active same-database caller transaction"
            )
        # Reuse verified immutable parents only within ONE public read. A later
        # same-session call starts fresh and sees uncommitted changes/evidence.
        key = (id(self), method.__name__, args, tuple(kwargs.items()))
        with _verification_scope(session) as cache:
            if key not in cache:
                cache[key] = method(self, session, *args, **kwargs)
            return cache[key]

    return checked


def production_build_recipe_v1(recipe_id: str) -> ReleaseArtifactRefV1:
    """Pin canonical bytes of the existing pilot recipe for the installed package.

    This SHA-256 is the pilot's external recipe BYTES hash, not a new content seal.
    Production requires canonical JSON bytes of QUANT_INTEGRITY_BUILD_RECIPE_V1.
    """
    _identifier(recipe_id)
    return _production_build_recipe_v1(recipe_id, _code_revision())


def _production_build_recipe_v1(
    recipe_id: str, code_revision: str
) -> ReleaseArtifactRefV1:
    _identifier(recipe_id)
    payload = {
        "schema_version": "QUANT_INTEGRITY_BUILD_RECIPE_V1",
        "recipe_id": recipe_id,
        "implementation_code_revision": code_revision,
        "model_name": "ELO_THREE_WAY_BASELINE_V1",
        "model_version": "1",
        "config_hash": PRODUCTION_CONFIG_HASH,
        "projection": "ADMITTED_SOURCE_TIME_ELO_V1",
        "parameter_policy": "NO_PARAMETER_TUNING",
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ReleaseArtifactRefV1(artifact_id=recipe_id, content_hash=digest)


class ProductionRevocationRequestV1(ReleaseSnapshotV1):
    """Reviewer approves exact scope and effect policy, not future recording time."""

    schema_version: Literal["PRODUCTION_REVOCATION_REQUEST_V1"] = (
        "PRODUCTION_REVOCATION_REQUEST_V1"
    )
    approval: ReleaseArtifactRefV1
    release: ReleaseArtifactRefV1 | None
    source_ids: tuple[Identifier, ...] = Field(min_length=1)
    affected_grants: tuple[ProductionGrantKind, ...] = Field(min_length=1)
    actor: Identifier
    reviewer_authority: LocalReviewEvidenceV1
    reason: RuleText
    effective_at_utc: UtcDateTime | None

    @property
    def request_hash(self):
        return tagged_canonical_sha256(self.schema_version, revalidate(self))


@dataclass(frozen=True, slots=True)
class _RevocationTarget:
    """Identity for withdrawal only; this is not an activation-capable release."""

    approval: TrainingHistoryApprovalV1
    release: ReleaseArtifactRefV1 | None
    source_ids: tuple[str, ...]
    persisted_at_utc: datetime


class SqlAlchemyProductionQuantRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        admission_repository: SqlAlchemyTrainingAdmissionRepository,
        pilot_repository: ProductionPilotRepository,
        clock: Callable[[], datetime] = utc_now,
        operator_id: str,
    ):
        _identifier(operator_id)
        self._sessions = session_factory
        self.admission_repository = admission_repository
        self.pilot_repository = pilot_repository
        self.operator_id = operator_id
        self._clock = clock
        self._last_clock = None
        self._clock_lock = Lock()

    def _now(self):
        with self._clock_lock:
            at = _utc(self._clock())
            if self._last_clock is not None and at < self._last_clock:
                raise ValueError("production operation clock moved backwards")
            self._last_clock = at
            return at

    @contextmanager
    def _transaction(self, *, write=False):
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE" if write else "BEGIN"))
            yield session

    def create_manifest(
        self,
        request_key: str,
        admission_ids: tuple[str, ...],
        training_window: EloTrainingWindowV1,
        integrity_pilot_scope_id: str,
        attestation_id: str,
    ) -> TrainingHistoryManifestV1:
        window = revalidate(training_window)
        ids = tuple(admission_ids)
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("manifest requires exact nonempty unique admission IDs")
        for key in (*ids, integrity_pilot_scope_id, attestation_id):
            _identifier(key)
        request = _request(
            "create_manifest",
            request_key,
            self.operator_id,
            admission_ids=ids,
            training_window=window,
            integrity_pilot_scope_id=integrity_pilot_scope_id,
            attestation_id=attestation_id,
        )
        with self._transaction(write=True) as session:
            previous = _prior(session, "training_history_manifests", request)
            if previous:
                return self.load_manifest_in_session(session, previous["manifest_id"])
            history = prepare_training_history(
                admissions=tuple(
                    self.admission_repository._load(session, key) for key in ids
                ),
                training_window=window,
                integrity_pilot_scope_id=integrity_pilot_scope_id,
            )
            evidence = self._technical(session, attestation_id, history)
            created, persisted = self._now(), self._now()
            manifest = TrainingHistoryManifestV1.freeze(
                content_payload=TrainingHistoryManifestContentV1(
                    history=history,
                    technical_evidence=evidence,
                    created_at_utc=created,
                    persisted_at_utc=persisted,
                )
            )
            for table, values in _manifest_children(manifest):
                _insert(session, table, values)
            _insert(
                session, "training_history_manifests", _manifest_row(manifest, request)
            )
            result = self.load_manifest_in_session(session, manifest.artifact_id)
            self._assert_clean_sources(session, history, self._now())
            return result

    def record_approval(
        self,
        request_key: str,
        manifest_id: str,
        *,
        grants: tuple[ProductionGrantV1, ...],
        review: LocalReviewEvidenceV1,
        reviewer_authority: LocalReviewEvidenceV1,
    ) -> TrainingHistoryApprovalV1:
        del request_key, manifest_id, grants, review, reviewer_authority
        raise ApprovalRecordingContractConflict(
            "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1 hashes approved_at_utc and persisted_at_utc; "
            "LocalReviewerAttestationV1 must bind that exact final payload hash. A review of a "
            "timestamp-free operation request cannot satisfy it. Approval writing is stopped "
            "until an explicit new review/recording envelope is authorized; no attestation is synthesized."
        )

    def load_manifest(self, manifest_id: str) -> TrainingHistoryManifestV1:
        with self._transaction() as session:
            return self.load_manifest_in_session(session, manifest_id)

    @_verified_read
    def load_manifest_in_session(
        self, session: Session, manifest_id: str
    ) -> TrainingHistoryManifestV1:
        row = _required(session, "training_history_manifests", manifest_id)
        manifest = TrainingHistoryManifestV1.model_validate_json(row["artifact_json"])
        request = _stored_request(row, "create_manifest")
        payload = strict_json_bytes(request["request_json"].encode())["payload"]
        history = manifest.content_payload.history
        if (
            tuple(sorted(payload["admission_ids"]))
            != tuple(item.training_fact_admission_id for item in history.admissions)
            or EloTrainingWindowV1.model_validate(payload["training_window"])
            != history.training_window
            or payload["integrity_pilot_scope_id"] != history.integrity_pilot_scope_id
            or payload["attestation_id"]
            != manifest.content_payload.technical_evidence.attestation.artifact_id
        ):
            raise ValueError("manifest immutable request differs from graph")
        _assert_row(row, _manifest_row(manifest, request))
        expected = prepare_training_history(
            admissions=tuple(
                self.admission_repository._load(
                    session, item.training_fact_admission_id
                )
                for item in history.admissions
            ),
            training_window=history.training_window,
            integrity_pilot_scope_id=history.integrity_pilot_scope_id,
        )
        if expected != history:
            raise ValueError("manifest differs from actual admitted evidence")
        if (
            self._technical(session, payload["attestation_id"], expected)
            != manifest.content_payload.technical_evidence
        ):
            raise ValueError("manifest actual terminal pilot graph mismatch")
        _verify_children(
            session,
            "manifest_id",
            manifest_id,
            _manifest_children(manifest),
            (
                "training_history_admissions",
                "training_history_seasons",
                "training_history_sources",
                "training_history_fixture_sources",
                "training_history_mapping_sources",
                "training_history_result_sources",
                "training_history_facts",
            ),
        )
        return manifest

    def load_approval(self, approval_id: str) -> TrainingHistoryApprovalV1:
        with self._transaction() as session:
            return self.load_approval_in_session(session, approval_id)

    @_verified_read
    def load_approval_in_session(
        self, session: Session, approval_id: str
    ) -> TrainingHistoryApprovalV1:
        return self._approval(session, approval_id, frozenset())

    def _approval(self, session, approval_id, visited):
        if approval_id in visited:
            raise ValueError("approval predecessor cycle")
        row = _required(session, "training_history_approval_events", approval_id)
        approval = TrainingHistoryApprovalV1.model_validate_json(row["artifact_json"])
        payload = approval.content_payload.approval_payload
        manifest = self.load_manifest_in_session(session, payload.manifest.artifact_id)
        request = _stored_request(row, "record_approval")
        expected_request = _request(
            "record_approval",
            row["request_key"],
            row["operator_id"],
            approval_payload=payload,
            reviewer_attestation=approval.content_payload.reviewer_attestation,
        )
        if request != expected_request:
            raise ValueError("approval recorded request mismatch")
        _assert_row(row, _approval_row(approval, request))
        if (
            payload.manifest != manifest.reference()
            or payload.scope != manifest.content_payload.history.scope
            or payload.source_rights
            != source_rights_refs(manifest.content_payload.history)
            or payload.technical_evidence != manifest.content_payload.technical_evidence
            or manifest.content_payload.persisted_at_utc
            > approval.content_payload.reviewer_attestation.content_payload.reviewed_at_utc
        ):
            raise ValueError("approval exact manifest/source/pilot scope mismatch")
        attestation = approval.content_payload.reviewer_attestation.content_payload
        review = self.admission_repository.evidence.review(
            evidence=attestation.evidence,
            authority=LocalReviewEvidenceV1(
                evidence_reference=attestation.reviewer_authority_reference,
                evidence_sha256=attestation.authority_sha256,
            ),
            schema=TRAINING_HISTORY_APPROVAL_PAYLOAD_V1,
            digest=payload.approval_payload_hash,
            source_ids=_source_ids(manifest),
            operator_id=row["operator_id"],
            at_utc=payload.approved_at_utc,
        )
        if (review.authorized_reviewer, review.reviewed_at_utc) != (
            attestation.authorized_reviewer,
            attestation.reviewed_at_utc,
        ):
            raise ValueError(
                "approval attestation differs from authorized local review bytes"
            )
        _verify_children(
            session,
            "approval_id",
            approval_id,
            _approval_grants(approval),
            ("training_history_approval_grants",),
        )
        edges = _select(
            session, "training_history_successors", successor_id=approval_id
        )
        if payload.supersedes_approval is None:
            if edges:
                raise ValueError("unexpected approval successor projection")
        else:
            predecessor = self._approval(
                session,
                payload.supersedes_approval.artifact_id,
                visited | {approval_id},
            )
            if predecessor.reference() != payload.supersedes_approval:
                raise ValueError("approval predecessor reference mismatch")
            successor = _successor(predecessor, approval)
            if len(edges) != 1:
                raise ValueError("missing or forked approval successor projection")
            _assert_row(edges[0], _successor_row(successor))
        return approval

    def build_release(
        self,
        request_key: str,
        approval_id: str,
        training_cutoff_at_utc: datetime,
        state_retention_horizon: RetentionHorizonV1,
        audit_retention_horizon: RetentionHorizonV1,
    ) -> ProductionQuantModelReleaseV1:
        cutoff = _utc(training_cutoff_at_utc)
        state_horizon, audit_horizon = (
            revalidate(state_retention_horizon),
            revalidate(audit_retention_horizon),
        )
        request = _request(
            "build_release",
            request_key,
            self.operator_id,
            approval_id=approval_id,
            training_cutoff_at_utc=cutoff,
            state_retention_horizon=state_horizon,
            audit_retention_horizon=audit_horizon,
        )
        with self._transaction(write=True) as session:
            previous = _prior(session, "production_quant_model_releases", request)
            if previous:
                return self.load_release_in_session(session, previous["release_id"])
            started = self._now()
            with _verification_scope(session):
                approval = self.load_approval_in_session(session, approval_id)
                manifest = self.load_manifest_in_session(
                    session,
                    approval.content_payload.approval_payload.manifest.artifact_id,
                )
                start = self._authorization(session, approval, manifest, started)
                approval_active_for_build(
                    approval=approval, manifest=manifest, current=start
                )
                assert_retention_authorized(
                    approval, start, state_horizon, audit_horizon
                )
            # Perform exact math before observing build completion. The pure builder
            # below replays again and verifies both captured authorization boundaries.
            from football_system.domain.production_release import replay_exact_facts

            replay_exact_facts(
                tuple(
                    item.content_payload.elo_fact
                    for item in manifest.content_payload.history.facts
                ),
                cutoff,
                manifest.content_payload.history.scope.production_target_season_id,
            )
            completed = self._now()
            with _verification_scope(session):
                completed_approval = self.load_approval_in_session(session, approval_id)
                completed_manifest = self.load_manifest_in_session(
                    session, manifest.artifact_id
                )
                if completed_approval != approval or completed_manifest != manifest:
                    raise ValueError(
                        "pinned build approval/manifest changed during operation"
                    )
                completion = self._authorization(
                    session, completed_approval, completed_manifest, completed
                )
                approval_active_for_build(
                    approval=completed_approval,
                    manifest=completed_manifest,
                    current=completion,
                )
                assert_retention_authorized(
                    completed_approval, completion, state_horizon, audit_horizon
                )
            persisted = self._now()
            release = build_production_release(
                manifest=manifest,
                approval=approval,
                training_cutoff_at_utc=cutoff,
                build_start=start,
                build_completion=completion,
                persisted_at_utc=persisted,
                state_retention_horizon=state_horizon,
                audit_retention_horizon=audit_horizon,
            )
            for table, values in _release_children(release):
                _insert(session, table, values)
            _insert(
                session,
                "production_quant_model_releases",
                _release_row(release, request),
            )
            committing = self._now()
            with _verification_scope(session):
                result = self.load_release_in_session(session, release.artifact_id)
                commit = self._authorization(
                    session,
                    result.training_approval,
                    result.training_manifest,
                    committing,
                )
                if commit.actual_at_utc < persisted:
                    raise ValueError(
                        "production operation clock moved backwards before commit"
                    )
                approval_active_for_build(
                    approval=result.training_approval,
                    manifest=result.training_manifest,
                    current=commit,
                )
                assert_retention_authorized(
                    result.training_approval,
                    commit,
                    state_horizon,
                    audit_horizon,
                    release.reference(),
                )
                # Revalidation can take time. Re-read the visible authorization
                # events and effective intervals at its end, not only its start.
                commit = self._authorization(
                    session,
                    result.training_approval,
                    result.training_manifest,
                    self._now(),
                )
                approval_active_for_build(
                    approval=result.training_approval,
                    manifest=result.training_manifest,
                    current=commit,
                )
                assert_retention_authorized(
                    result.training_approval,
                    commit,
                    state_horizon,
                    audit_horizon,
                    release.reference(),
                )
            return result

    def load_release(self, release_id: str) -> ProductionQuantModelReleaseV1:
        with self._transaction() as session:
            return self.load_release_in_session(session, release_id)

    @_verified_read
    def load_release_in_session(
        self, session: Session, release_id: str
    ) -> ProductionQuantModelReleaseV1:
        row = _required(session, "production_quant_model_releases", release_id)
        release = ProductionQuantModelReleaseV1.model_validate_json(
            row["artifact_json"]
        )
        content = release.content_payload
        request = _stored_request(row, "build_release")
        if request != _request(
            "build_release",
            row["request_key"],
            row["operator_id"],
            approval_id=content.approval.artifact_id,
            training_cutoff_at_utc=content.training_cutoff_at_utc,
            state_retention_horizon=content.state_retention_horizon,
            audit_retention_horizon=content.audit_retention_horizon,
        ):
            raise ValueError("release immutable request mismatch")
        _assert_row(row, _release_row(release, request))
        approval = self.load_approval_in_session(session, content.approval.artifact_id)
        manifest = self.load_manifest_in_session(session, content.manifest.artifact_id)
        if (
            approval != release.training_approval
            or manifest != release.training_manifest
        ):
            raise ValueError("release differs from actual approval/manifest graph")
        _verify_children(
            session,
            "release_id",
            release_id,
            _release_children(release),
            ("production_quant_model_release_facts",),
        )
        for captured in (
            content.build_start_authorization,
            content.build_completion_authorization,
        ):
            actual = self._authorization(
                session,
                approval,
                manifest,
                captured.content_payload.current.actual_at_utc,
            )
            if actual != captured.content_payload.current:
                raise ValueError(
                    "captured build authorization differs from persisted as-of evidence"
                )
        return release

    def authorization(
        self, release_id: str, at_utc: datetime
    ) -> CurrentAuthorizationInputsV1:
        with self._transaction() as session:
            return self.authorization_in_session(session, release_id, at_utc)

    @_verified_read
    def authorization_in_session(
        self, session: Session, release_id: str, at_utc: datetime
    ) -> CurrentAuthorizationInputsV1:
        """Fresh complete view in the caller's transaction; does not authorize alone."""
        at = _utc(at_utc)
        if at > self._now():
            raise ValueError(
                "authorization cannot forecast a future actual operation time"
            )
        release = self.load_release_in_session(session, release_id)
        return self._authorization(
            session, release.training_approval, release.training_manifest, at
        )

    def _authorization(self, session, approval, manifest, at):
        at = _utc(at)
        self._assert_clean_sources(session, manifest.content_payload.history, at)
        corrections = []
        match_ids = tuple(
            item.content_payload.elo_fact.match_id
            for item in manifest.content_payload.history.facts
        )
        table = _table("training_source_correction_events")
        for row in session.execute(
            select(table).where(table.c.internal_match_id.in_(match_ids))
        ).mappings():
            _verify_row(table.name, row)
            correction = SourceCorrectionV1.model_validate_json(row["artifact_json"])
            _assert_row(row, _correction_row(correction))
            if correction.content_payload.registered_at_utc <= at:
                corrections.append(correction)
        revocations = []
        for row in _select(
            session,
            "training_history_revocation_events",
            approval_id=approval.artifact_id,
        ):
            event = self._revocation(session, row)
            if event.content_payload.approval != approval.reference():
                raise ValueError("revocation differs from authorized approval scope")
            if event.content_payload.recorded_at_utc <= at:
                revocations.append(event)
        successors = []
        for row in _select(
            session, "training_history_successors", predecessor_id=approval.artifact_id
        ):
            successor = self.load_approval_in_session(session, row["successor_id"])
            event = _successor(approval, successor)
            _assert_row(row, _successor_row(event))
            if event.content_payload.successor_persisted_at_utc <= at:
                successors.append(event)
        return CurrentAuthorizationInputsV1(
            actual_at_utc=at,
            technical_evidence=self._technical(
                session,
                manifest.content_payload.technical_evidence.attestation.artifact_id,
                manifest.content_payload.history,
            ),
            corrections=tuple(sorted(corrections, key=lambda item: item.artifact_id)),
            revocations=tuple(sorted(revocations, key=lambda item: item.artifact_id)),
            successors=tuple(sorted(successors, key=lambda item: item.artifact_id)),
        )

    def seal_target_plan(
        self,
        request_key: str,
        release_id: str,
        targets: tuple[ProductionTargetV1, ...],
        kickoff_window_start_at_utc: datetime,
        kickoff_window_end_at_utc: datetime,
        decision_as_of_at_utc: datetime,
        selection_rule: str,
    ) -> ProductionTargetAcceptancePlanV1:
        targets = tuple(revalidate(item) for item in targets)
        request = _request(
            "seal_target_plan",
            request_key,
            self.operator_id,
            release_id=release_id,
            targets=targets,
            kickoff_window_start_at_utc=_utc(kickoff_window_start_at_utc),
            kickoff_window_end_at_utc=_utc(kickoff_window_end_at_utc),
            decision_as_of_at_utc=_utc(decision_as_of_at_utc),
            selection_rule=selection_rule,
        )
        with self._transaction(write=True) as session:
            previous = _prior(session, "production_target_acceptance_plans", request)
            if previous:
                return self.load_target_plan_in_session(session, previous["plan_id"])
            release = self.load_release_in_session(session, release_id)
            sealed, persisted = self._now(), self._now()
            plan = ProductionTargetAcceptancePlanV1.freeze(
                content_payload=ProductionTargetAcceptancePlanContentV1(
                    release=release.reference(),
                    competition_id=release.content_payload.scope.competition_id,
                    production_target_season_id=release.content_payload.scope.production_target_season_id,
                    targets=targets,
                    kickoff_window_start_at_utc=kickoff_window_start_at_utc,
                    kickoff_window_end_at_utc=kickoff_window_end_at_utc,
                    decision_as_of_at_utc=decision_as_of_at_utc,
                    selection_rule=selection_rule,
                    sealed_at_utc=sealed,
                    persisted_at_utc=persisted,
                )
            )
            self._verify_targets(session, release, plan)
            for table, values in _target_children(plan):
                _insert(session, table, values)
            _insert(
                session, "production_target_acceptance_plans", _plan_row(plan, request)
            )
            return self.load_target_plan_in_session(session, plan.artifact_id)

    def load_target_plan(self, plan_id: str) -> ProductionTargetAcceptancePlanV1:
        with self._transaction() as session:
            return self.load_target_plan_in_session(session, plan_id)

    @_verified_read
    def load_target_plan_in_session(
        self, session: Session, plan_id: str
    ) -> ProductionTargetAcceptancePlanV1:
        row = _required(session, "production_target_acceptance_plans", plan_id)
        plan = ProductionTargetAcceptancePlanV1.model_validate_json(
            row["artifact_json"]
        )
        content = plan.content_payload
        request = _stored_request(row, "seal_target_plan")
        if request != _request(
            "seal_target_plan",
            row["request_key"],
            row["operator_id"],
            release_id=content.release.artifact_id,
            targets=content.targets,
            kickoff_window_start_at_utc=content.kickoff_window_start_at_utc,
            kickoff_window_end_at_utc=content.kickoff_window_end_at_utc,
            decision_as_of_at_utc=content.decision_as_of_at_utc,
            selection_rule=content.selection_rule,
        ):
            raise ValueError("target plan immutable request mismatch")
        _assert_row(row, _plan_row(plan, request))
        release = self.load_release_in_session(session, content.release.artifact_id)
        self._verify_targets(session, release, plan)
        _verify_children(
            session,
            "plan_id",
            plan_id,
            _target_children(plan),
            ("production_target_acceptance_matches",),
        )
        return plan

    def _verify_targets(self, session, release, plan):
        assert_target_plan(release, plan)
        content = plan.content_payload
        for target in content.targets:
            fixture = (
                session.execute(
                    select(MatchRecord.__table__).where(
                        MatchRecord.internal_match_id == target.match_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            identity = (
                session.execute(
                    select(CanonicalMatchIdentityRecord.__table__).where(
                        CanonicalMatchIdentityRecord.internal_match_id
                        == target.match_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                fixture is None
                or identity is None
                or (
                    fixture["competition_id"],
                    fixture["home_team_id"],
                    fixture["away_team_id"],
                    fixture["kickoff_at_utc"],
                    identity["season"],
                )
                != (
                    content.competition_id,
                    target.home_team_id,
                    target.away_team_id,
                    target.kickoff_at_utc,
                    content.production_target_season_id,
                )
            ):
                raise ValueError(
                    "target differs from preexisting canonical fixture/season"
                )
            if (
                max(
                    fixture["available_at_utc"],
                    fixture["created_at_utc"],
                    identity["available_at_utc"],
                )
                > content.sealed_at_utc
            ):
                raise ValueError("target fixture was not possessed before plan sealing")

    def prepare_revocation_request(
        self,
        approval_id: str,
        *,
        release_id: str | None,
        affected_grants: tuple[ProductionGrantKind, ...],
        actor: str,
        reviewer_authority: LocalReviewEvidenceV1,
        reason: str,
        effective_at_utc: datetime | None = None,
    ) -> ProductionRevocationRequestV1:
        with self._transaction() as session:
            target = self._revocation_target(session, approval_id, release_id)
            return ProductionRevocationRequestV1(
                approval=target.approval.reference(),
                release=target.release,
                source_ids=target.source_ids,
                affected_grants=affected_grants,
                actor=actor,
                reviewer_authority=reviewer_authority,
                reason=reason,
                effective_at_utc=None
                if effective_at_utc is None
                else _utc(effective_at_utc),
            )

    def record_revocation(
        self,
        request_key: str,
        *,
        revocation_request: ProductionRevocationRequestV1,
        review: LocalReviewEvidenceV1,
    ) -> GrantRevocationV1:
        intent, review = revalidate(revocation_request), revalidate(review)
        request = _request(
            "record_revocation",
            request_key,
            self.operator_id,
            revocation_request=intent,
            review=review,
        )
        with self._transaction(write=True) as session:
            previous = _prior(session, "training_history_revocation_events", request)
            target = self._revocation_target(
                session,
                intent.approval.artifact_id,
                None if intent.release is None else intent.release.artifact_id,
            )
            if previous:
                return self._revocation(session, previous, target=target)
            at = self._now()
            self._verify_revocation_review(intent, review, target, self.operator_id, at)
            recorded = self._now()
            if recorded < at:
                raise ValueError("revocation clock moved backwards")
            event = GrantRevocationV1.freeze(
                content_payload=GrantRevocationContentV1(
                    approval=intent.approval,
                    release=intent.release,
                    affected_grants=intent.affected_grants,
                    actor=intent.actor,
                    authority_reference=intent.reviewer_authority.evidence_reference,
                    authority_sha256=intent.reviewer_authority.evidence_sha256,
                    reason=intent.reason,
                    recorded_at_utc=recorded,
                    effective_at_utc=recorded
                    if intent.effective_at_utc is None
                    else intent.effective_at_utc,
                )
            )
            values = _revocation_row(event, request)
            _insert(session, "training_history_revocation_events", values)
            return self._revocation(
                session,
                _required(
                    session, "training_history_revocation_events", event.artifact_id
                ),
                target=target,
            )

    def _revocation_target(self, session, approval_id, release_id):
        """Check immutable withdrawal identity, never historical/live eligibility.

        The approval's local attestation *seal* remains mandatory; its old evidence
        files do not. Fresh independent withdrawal authority is checked separately.
        Manifest/release envelopes are hashed without invoking today's Elo replay.
        """
        row = _required(session, "training_history_approval_events", approval_id)
        approval = TrainingHistoryApprovalV1.model_validate_json(row["artifact_json"])
        payload = approval.content_payload.approval_payload
        request = _stored_request(row, "record_approval")
        if request != _request(
            "record_approval",
            row["request_key"],
            row["operator_id"],
            approval_payload=payload,
            reviewer_attestation=approval.content_payload.reviewer_attestation,
        ):
            raise ValueError("revocation target approval request mismatch")
        _assert_row(row, _approval_row(approval, request))

        manifest_row = _required(
            session, "training_history_manifests", payload.manifest.artifact_id
        )
        manifest = _revocation_envelope_v1(
            manifest_row, "TRAINING_HISTORY_MANIFEST_V1", "manifest_id", "manifest_hash"
        )
        content, history = (
            manifest["content_payload"],
            manifest["content_payload"]["history"],
        )
        if (
            manifest["content_hash"] != payload.manifest.content_hash
            or history["scope"] != payload.scope.model_dump(mode="json")
            or content["technical_evidence"]
            != payload.technical_evidence.model_dump(mode="json")
            or manifest_row["competition_id"] != payload.scope.competition_id
            or manifest_row["pilot_target_season_id"]
            != payload.scope.pilot_target_season_id
            or manifest_row["production_target_season_id"]
            != payload.scope.production_target_season_id
            or manifest_row["persisted_at_utc"]
            != _utc(datetime.fromisoformat(content["persisted_at_utc"]))
        ):
            raise ValueError("revocation target manifest/source scope mismatch")
        sources = tuple(
            HistorySourceSummaryV1.model_validate(item)
            for item in history["source_summaries"]
        )
        rights = {item.admission.artifact_id: item for item in payload.source_rights}
        if not sources or tuple(item.source_sequence for item in sources) != tuple(
            range(len(sources))
        ):
            raise ValueError("revocation target source scope is incomplete")
        for source in sources:
            right = rights.get(source.source_rights_admission.artifact_id)
            if (
                right is None
                or right.admission != source.source_rights_admission
                or right.terms_sha256 != source.terms_sha256
                or source.source_id not in right.source_ids
            ):
                raise ValueError("revocation target source/rights binding mismatch")

        release_ref, persisted = None, payload.persisted_at_utc
        if release_id is not None:
            row = _required(session, "production_quant_model_releases", release_id)
            release = _revocation_envelope_v1(
                row, "PRODUCTION_QUANT_MODEL_RELEASE_V1", "release_id", "release_hash"
            )
            content = release["content_payload"]
            if (
                content["approval"] != approval.reference().model_dump(mode="json")
                or content["manifest"] != payload.manifest.model_dump(mode="json")
                or content["scope"] != payload.scope.model_dump(mode="json")
                or content["technical_evidence"]
                != payload.technical_evidence.model_dump(mode="json")
                or content["build_recipe"]
                != payload.build_recipe.model_dump(mode="json")
                or content["code_revision"] != payload.code_revision
                or row["approval_id"] != approval_id
                or row["manifest_id"] != payload.manifest.artifact_id
                or row["persisted_at_utc"]
                != _utc(datetime.fromisoformat(content["persisted_at_utc"]))
            ):
                raise ValueError("revocation target release/approval scope mismatch")
            release_ref = ReleaseArtifactRefV1(
                artifact_id=release_id, content_hash=release["content_hash"]
            )
            persisted = max(persisted, row["persisted_at_utc"])
        return _RevocationTarget(
            approval,
            release_ref,
            tuple(sorted({item.source_id for item in sources})),
            persisted,
        )

    def _revocation(self, session, row, *, target=None):
        event = GrantRevocationV1.model_validate_json(row["artifact_json"])
        request = _stored_request(row, "record_revocation")
        payload = strict_json_bytes(request["request_json"].encode())["payload"]
        intent = ProductionRevocationRequestV1.model_validate(
            payload["revocation_request"]
        )
        review = LocalReviewEvidenceV1.model_validate(payload["review"])
        content = event.content_payload
        if target is None:
            target = self._revocation_target(
                session,
                intent.approval.artifact_id,
                None if intent.release is None else intent.release.artifact_id,
            )
        self._verify_revocation_review(
            intent,
            review,
            target,
            row["operator_id"],
            content.recorded_at_utc,
        )
        expected = GrantRevocationV1.freeze(
            content_payload=GrantRevocationContentV1(
                approval=intent.approval,
                release=intent.release,
                affected_grants=intent.affected_grants,
                actor=intent.actor,
                authority_reference=intent.reviewer_authority.evidence_reference,
                authority_sha256=intent.reviewer_authority.evidence_sha256,
                reason=intent.reason,
                recorded_at_utc=content.recorded_at_utc,
                effective_at_utc=content.recorded_at_utc
                if intent.effective_at_utc is None
                else intent.effective_at_utc,
            )
        )
        if expected != event:
            raise ValueError("revocation differs from exact reviewer-approved request")
        _assert_row(row, _revocation_row(event, request))
        return event

    def _verify_revocation_review(self, intent, review, target, operator, at):
        if (
            intent.approval != target.approval.reference()
            or intent.release != target.release
            or intent.source_ids != target.source_ids
        ):
            raise ValueError("revocation exact approval/source scope mismatch")
        if at < target.persisted_at_utc:
            raise ValueError("revocation cannot precede target persistence")
        granted = {
            item.grant
            for item in target.approval.content_payload.approval_payload.grants
        }
        if not set(
            intent.affected_grants
        ) <= granted or intent.affected_grants != tuple(
            sorted(set(intent.affected_grants))
        ):
            raise ValueError("revocation must name exact unique granted permissions")
        document = self.admission_repository.evidence.review(
            evidence=review,
            authority=intent.reviewer_authority,
            schema=intent.schema_version,
            digest=intent.request_hash,
            source_ids=intent.source_ids,
            operator_id=operator,
            at_utc=at,
        )
        if document.authorized_reviewer != intent.actor:
            raise ValueError("revocation actor is not the authorized local reviewer")

    def _technical(self, session, attestation_id, history):
        cache = session.info.get("production_verified_read", {})
        key = (
            id(self),
            "technical",
            attestation_id,
            history.scope.training_window_hash,
            history.approved_facts_hash,
        )
        evidence = cache.get(key)
        if evidence is None:
            evidence = revalidate(
                self.pilot_repository.technical_evidence(
                    attestation_id, history, session=session
                )
            )
        if evidence.attestation.artifact_id != attestation_id:
            raise ValueError("pilot bridge returned an unrequested attestation")
        code_revision = _code_revision()
        if (
            evidence.code_revision != code_revision
            or evidence.build_recipe
            != _production_build_recipe_v1(
                evidence.build_recipe.artifact_id, code_revision
            )
        ):
            raise ValueError(
                "current package code revision/build recipe differs from pilot evidence"
            )
        cache[key] = evidence
        return evidence

    def _assert_clean_sources(self, session, history, at):
        # No actual registration clock exists on bare normalized successors. Do
        # not manufacture one or silently ignore them using source-time ingestion.
        results = {
            item.content_payload.elo_fact.match_id: item.content_payload.elo_fact.match_result_id
            for item in history.facts
        }
        for row in session.execute(
            select(MatchResultRecord.__table__).where(
                MatchResultRecord.internal_match_id.in_(tuple(results))
            )
        ).mappings():
            if (
                results[row["internal_match_id"]] != row["match_result_id"]
                or row["supersedes_match_result_id"] is not None
            ):
                raise ControlledTrainingCorrectionRequired(
                    "unadmitted normalized successor requires full controlled predecessor path"
                )
        correction_table = _table("training_source_correction_events")
        if (
            session.execute(
                select(correction_table.c.correction_id).where(
                    correction_table.c.internal_match_id.in_(tuple(results)),
                    correction_table.c.registered_at_utc <= at,
                )
            ).first()
            is not None
        ):
            raise ControlledTrainingCorrectionRequired(
                "registered correction event blocks production until full controlled predecessor path exists"
            )


def _manifest_children(manifest):
    mid, graph = manifest.artifact_id, manifest.content_payload.history
    for admission in graph.admissions:
        yield (
            "training_history_admissions",
            _row(
                "training_history_admissions",
                manifest_id=mid,
                training_fact_admission_id=admission.training_fact_admission_id,
                admission_hash=admission.admission_hash,
                source_rights_admission_id=admission.content_payload.source_rights_admission_id,
                artifact_json=canonical_json(admission),
            ),
        )
    for summary in graph.season_summaries:
        yield (
            "training_history_seasons",
            _row(
                "training_history_seasons",
                manifest_id=mid,
                season_sequence=summary.season.season_sequence,
                season_id=summary.season.season_id,
                role=summary.season.role,
                fact_count=summary.fact_count,
                facts_hash=summary.facts_hash,
                artifact_json=canonical_json(summary),
            ),
        )
    for summary in graph.source_summaries:
        yield (
            "training_history_sources",
            _row(
                "training_history_sources",
                manifest_id=mid,
                source_sequence=summary.source_sequence,
                source_id=summary.source_id,
                provider_code=summary.provider_code,
                source_rights_admission_id=summary.source_rights_admission.artifact_id,
                fact_count=summary.fact_count,
                facts_hash=summary.facts_hash,
                artifact_json=canonical_json(summary),
            ),
        )
    for fact in graph.facts:
        content, binding = (
            fact.content_payload,
            fact.content_payload.binding.content_payload,
        )
        fixture, membership, result = (
            binding.fixture_source,
            binding.season_membership,
            binding.match_result_admission,
        )
        fixture_id = stable_id(
            "TRAINING_FIXTURE_SOURCE_V1",
            tagged_canonical_sha256("TRAINING_FIXTURE_SOURCE_V1", fixture),
        )
        common = dict(
            manifest_id=mid,
            source_sequence=content.fact_sequence,
            season_sequence=content.season_sequence,
            training_fact_admission_id=content.training_fact_admission.artifact_id,
            internal_match_id=content.elo_fact.match_id,
            provider_mapping_id=binding.provider_mapping.mapping_id,
        )
        yield (
            "training_history_fixture_sources",
            _row(
                "training_history_fixture_sources",
                **common,
                fixture_source_id=fixture_id,
                fixture_record_hash=fixture.fixture_record_sha256,
                artifact_json=canonical_json(fixture),
            ),
        )
        yield (
            "training_history_mapping_sources",
            _row(
                "training_history_mapping_sources",
                **common,
                season_membership_id=membership.season_membership_id,
                membership_hash=membership.membership_hash,
                artifact_json=canonical_json(
                    {
                        "provider_mapping": binding.provider_mapping,
                        "season_membership": membership,
                    }
                ),
            ),
        )
        yield (
            "training_history_result_sources",
            _row(
                "training_history_result_sources",
                **common,
                match_result_admission_id=result.match_result_admission_id,
                admission_hash=result.admission_hash,
                match_result_id=content.elo_fact.match_result_id,
                artifact_json=canonical_json(
                    {
                        "admission": result,
                        "normalized_result": binding.normalized_result,
                    }
                ),
            ),
        )
        yield (
            "training_history_facts",
            _row(
                "training_history_facts",
                manifest_id=mid,
                fact_sequence=content.fact_sequence,
                season_sequence=content.season_sequence,
                season_id=content.elo_fact.season_id,
                internal_match_id=content.elo_fact.match_id,
                match_result_id=content.elo_fact.match_result_id,
                training_fact_admission_id=content.training_fact_admission.artifact_id,
                training_fact_binding_id=content.binding.training_fact_binding_id,
                fixture_source_sequence=content.fact_sequence,
                mapping_source_sequence=content.fact_sequence,
                result_source_sequence=content.fact_sequence,
                elo_fact_hash=content.elo_fact.fact_hash,
                approved_fact_hash=fact.content_hash,
                artifact_json=canonical_json(fact),
            ),
        )


def _manifest_row(manifest, request):
    content, history = manifest.content_payload, manifest.content_payload.history
    return _row(
        "training_history_manifests",
        **request,
        manifest_id=manifest.artifact_id,
        manifest_hash=manifest.content_hash,
        competition_id=history.scope.competition_id,
        pilot_target_season_id=history.scope.pilot_target_season_id,
        production_target_season_id=history.scope.production_target_season_id,
        attestation_id=content.technical_evidence.attestation.artifact_id,
        attestation_hash=content.technical_evidence.attestation.content_hash,
        attempt_root=content.technical_evidence.attempt_root,
        source_root=history.source_root,
        season_root=history.season_root,
        approved_facts_hash=history.approved_facts_hash,
        training_data_hash=history.training_data_hash,
        admission_count=len(history.admissions),
        source_count=history.source_count,
        season_count=history.season_count,
        fact_count=history.fact_count,
        created_at_utc=content.created_at_utc,
        persisted_at_utc=content.persisted_at_utc,
        artifact_json=canonical_json(manifest),
    )


def _approval_row(approval, request):
    content = approval.content_payload.approval_payload
    return _row(
        "training_history_approval_events",
        **request,
        approval_id=approval.artifact_id,
        approval_hash=approval.content_hash,
        manifest_id=content.manifest.artifact_id,
        approved_at_utc=content.approved_at_utc,
        persisted_at_utc=content.persisted_at_utc,
        supersedes_approval_id=None
        if content.supersedes_approval is None
        else content.supersedes_approval.artifact_id,
        artifact_json=canonical_json(approval),
    )


def _approval_grants(approval):
    for grant in approval.content_payload.approval_payload.grants:
        yield (
            "training_history_approval_grants",
            _row(
                "training_history_approval_grants",
                approval_id=approval.artifact_id,
                grant=grant.grant.value,
                effective_at_utc=grant.effective_at_utc,
                expires_at_utc=grant.expires_at_utc,
                artifact_json=canonical_json(grant),
            ),
        )


def _successor(predecessor, successor):
    payload = successor.content_payload.approval_payload
    if payload.supersedes_approval != predecessor.reference():
        raise ValueError("successor does not bind actual predecessor")
    return ApprovalSuccessorV1.freeze(
        content_payload=ApprovalSuccessorContentV1(
            predecessor=predecessor.reference(),
            successor=successor.reference(),
            predecessor_scope=predecessor.content_payload.approval_payload.scope,
            successor_scope=payload.scope,
            affected_grants=payload.superseded_grants,
            successor_persisted_at_utc=payload.persisted_at_utc,
            effective_at_utc=payload.supersession_effective_at_utc,
        )
    )


def _successor_row(event):
    content = event.content_payload
    return _row(
        "training_history_successors",
        predecessor_id=content.predecessor.artifact_id,
        successor_id=content.successor.artifact_id,
        successor_persisted_at_utc=content.successor_persisted_at_utc,
        effective_at_utc=content.effective_at_utc,
        artifact_json=canonical_json(event),
    )


def _release_row(release, request):
    content = release.content_payload
    return _row(
        "production_quant_model_releases",
        **request,
        release_id=release.artifact_id,
        release_hash=release.content_hash,
        approval_id=content.approval.artifact_id,
        manifest_id=content.manifest.artifact_id,
        released_state_core_hash=content.released_state_core.content_hash,
        training_cutoff_at_utc=content.training_cutoff_at_utc,
        fact_count=len(content.release_facts),
        build_started_at_utc=content.build_started_at_utc,
        build_completed_at_utc=content.build_completed_at_utc,
        persisted_at_utc=content.persisted_at_utc,
        artifact_json=canonical_json(release),
    )


def _release_children(release):
    for fact in release.content_payload.release_facts:
        content = fact.content_payload
        yield (
            "production_quant_model_release_facts",
            _row(
                "production_quant_model_release_facts",
                release_id=release.artifact_id,
                manifest_id=release.content_payload.manifest.artifact_id,
                fact_sequence=content.fact_sequence,
                match_result_id=content.elo_fact.match_result_id,
                elo_fact_hash=content.elo_fact.fact_hash,
                approved_fact_hash=fact.content_hash,
                artifact_json=canonical_json(fact),
            ),
        )


def _plan_row(plan, request):
    content = plan.content_payload
    return _row(
        "production_target_acceptance_plans",
        **request,
        plan_id=plan.artifact_id,
        plan_hash=plan.content_hash,
        release_id=content.release.artifact_id,
        target_count=len(content.targets),
        sealed_at_utc=content.sealed_at_utc,
        persisted_at_utc=content.persisted_at_utc,
        decision_as_of_at_utc=content.decision_as_of_at_utc,
        artifact_json=canonical_json(plan),
    )


def _target_children(plan):
    for sequence, target in enumerate(plan.content_payload.targets):
        yield (
            "production_target_acceptance_matches",
            _row(
                "production_target_acceptance_matches",
                plan_id=plan.artifact_id,
                target_sequence=sequence,
                internal_match_id=target.match_id,
                kickoff_at_utc=target.kickoff_at_utc,
                artifact_json=canonical_json(target),
            ),
        )


def _revocation_row(event, request):
    content = event.content_payload
    return _row(
        "training_history_revocation_events",
        **request,
        revocation_id=event.artifact_id,
        revocation_hash=event.content_hash,
        approval_id=content.approval.artifact_id,
        release_id=None if content.release is None else content.release.artifact_id,
        recorded_at_utc=content.recorded_at_utc,
        effective_at_utc=content.effective_at_utc,
        artifact_json=canonical_json(event),
    )


def _correction_row(event):
    content = event.content_payload
    return _row(
        "training_source_correction_events",
        correction_id=event.artifact_id,
        correction_hash=event.content_hash,
        internal_match_id=content.match_id,
        source_id=content.source_id,
        component=content.component,
        predecessor_id=content.predecessor.artifact_id,
        registered_at_utc=content.registered_at_utc,
        source_available_at_utc=content.source_available_at_utc,
        artifact_json=canonical_json(event),
    )


def _source_ids(manifest):
    return tuple(
        sorted(
            {
                item.source_id
                for item in manifest.content_payload.history.source_summaries
            }
        )
    )


def _revocation_envelope_v1(row, schema, id_column, hash_column):
    """Verify a persisted V1 seal, not the usability or replay of its contents."""
    document = strict_json_bytes(row["artifact_json"].encode("utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != schema:
        raise ValueError("revocation target envelope schema mismatch")
    content = document.get("content_payload")
    if not isinstance(content, dict):
        raise ValueError("revocation target envelope requires content")
    digest = tagged_canonical_sha256(schema, _revocation_hash_payload_v1(content))
    if (
        canonical_json(document) != row["artifact_json"]
        or document.get("artifact_id") != row[id_column]
        or document.get("content_hash") != row[hash_column]
        or digest != row[hash_column]
        or stable_id(schema, digest) != row[id_column]
    ):
        raise ValueError("revocation target envelope ID/hash mismatch")
    return document


def _revocation_hash_payload_v1(value):
    # Frozen V1 wire-to-hash scalar types. All V1 datetimes have *_at_utc keys;
    # these are its exact Decimal fields (Elo config/ratings, mapping confidence).
    # Restore serialization types only: never run current Elo/admission validators
    # to decide whether an old, possibly unusable artifact may be withdrawn.
    decimal_fields = {
        "initial_rating",
        "k_factor",
        "home_advantage",
        "season_regression_factor",
        "draw_probability",
        "rating",
        "confidence",
    }
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if item is not None and key.endswith("_at_utc"):
                if not isinstance(item, str):
                    raise ValueError("revocation target V1 time must be serialized UTC")
                at = _utc(datetime.fromisoformat(item))
                if item != at.isoformat().replace("+00:00", "Z"):
                    raise ValueError("revocation target V1 time is not canonical UTC")
                result[key] = at
            elif item is not None and key in decimal_fields:
                if not isinstance(item, str):
                    raise ValueError(
                        "revocation target V1 Decimal must be serialized text"
                    )
                try:
                    result[key] = Decimal(item)
                except InvalidOperation as error:
                    raise ValueError("invalid revocation target V1 Decimal") from error
            else:
                result[key] = _revocation_hash_payload_v1(item)
        return result
    if isinstance(value, list):
        return [_revocation_hash_payload_v1(item) for item in value]
    return value


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("production operation clock/time must be aware UTC")
    return value


def _identifier(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 160
    ):
        raise ValueError(
            "production operation identity must be an exact nonempty identifier"
        )


def _table(name):
    return Base.metadata.tables[name]


def _request(operation, key, operator, **payload):
    _identifier(key)
    _identifier(operator)
    document = canonical_json(
        {
            "operation": operation,
            "request_key": key,
            "operator_id": operator,
            "payload": payload,
        }
    )
    return dict(
        request_key=key,
        operator_id=operator,
        request_json=document,
        request_sha256=tagged_canonical_sha256(
            "PRODUCTION_PERSISTENCE_REQUEST_V1", strict_json_bytes(document.encode())
        ),
    )


def _stored_request(row, operation):
    document = strict_json_bytes(row["request_json"].encode())
    if document.get("operation") != operation:
        raise ValueError("production request operation mismatch")
    request = _request(
        operation, row["request_key"], row["operator_id"], **document["payload"]
    )
    if any(row[key] != value for key, value in request.items()):
        raise ValueError("stored production request integrity mismatch")
    return request


def _prior(session, name, request):
    rows = _select(session, name, request_key=request["request_key"])
    if not rows:
        return None
    row = rows[0]
    if any(row[key] != value for key, value in request.items()):
        raise ValueError(
            "immutable production retry request conflicts with stored content"
        )
    return row


def _required(session, name, key):
    table = _table(name)
    _identifier(key)
    pk = tuple(table.primary_key.columns)[0]
    row = session.execute(select(table).where(pk == key)).mappings().one_or_none()
    if row is None:
        raise ValueError(f"missing persisted {name}: {key}")
    _verify_row(name, row)
    return row


def _select(session, name, **filters):
    table = _table(name)
    rows = tuple(
        session.execute(
            select(table).where(
                *(table.c[key] == value for key, value in filters.items())
            )
        ).mappings()
    )
    for row in rows:
        _verify_row(name, row)
    return rows


def _row(name, **values):
    expected = {column.name for column in _table(name).columns} - {"row_sha256"}
    if set(values) != expected:
        raise ValueError(f"incomplete {name} projection: {expected ^ set(values)}")
    return dict(
        **values,
        row_sha256=tagged_canonical_sha256(f"PRODUCTION_SQL_ROW_V1:{name}", values),
    )


def _verify_row(name, row):
    if row["row_sha256"] != tagged_canonical_sha256(
        f"PRODUCTION_SQL_ROW_V1:{name}",
        {key: value for key, value in row.items() if key != "row_sha256"},
    ):
        raise ValueError(f"stored {name} row integrity mismatch")


def _assert_row(actual, expected):
    if dict(actual) != expected:
        raise ValueError("stored production child/parent projection mismatch")


def _insert(session, name, values):
    session.execute(_table(name).insert().values(**values))


def _verify_children(session, parent_key, parent_id, expected, names):
    groups = {name: {} for name in names}
    for name, row in expected:
        pk = tuple(row[column.name] for column in _table(name).primary_key.columns)
        groups[name][pk] = row
    for name, children in groups.items():
        actual = _select(session, name, **{parent_key: parent_id})
        if len(actual) != len(children):
            raise ValueError(f"stored {name} child count mismatch")
        for row in actual:
            pk = tuple(row[column.name] for column in _table(name).primary_key.columns)
            if pk not in children:
                raise ValueError(f"unexpected {name} child identity")
            _assert_row(row, children[pk])
