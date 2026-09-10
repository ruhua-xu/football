"""Mandatory local authorization for approved review and post-review artifacts.

V3 remains a historical byte contract. These gates instead verify its actual
database graph, normalized model facts, and current rights. SQLite IMMEDIATE
transactions serialize publication with revocation writers; clocks and already
recorded future revocations are checked again at completion, even on retries.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.ports.production_inference import (
    ProductionInferenceBindingV1,
)
from football_system.application.production_release import (
    freeze_approved_training_history_audit,
    validate_approved_training_history_audit,
)
from football_system.application.review_bridge import (
    _parse_analysis_packet,
    build_analysis_packet_v3,
    canonical_json,
    strict_json_loads,
)
from football_system.domain.analysis import AnalysisRun
from football_system.domain.common import normalize_utc, utc_now
from football_system.domain.match import Match
from football_system.domain.production_release import (
    ApprovalSuccessorV1,
    ApprovedTrainingHistoryAuditV1,
    ObservedTrainingHistoryGraphV1,
    authorization_at,
    CurrentAuthorizationInputsV1,
    GrantRevocationV1,
    assert_authorization_progression,
    release_active_for_inference,
)
from football_system.domain.review import AnalysisPacketV3, StoredAnalysisPacket
from football_system.infrastructure.database.models import Base
from football_system.infrastructure.database.production_audit_schema import (
    admitted_training_sql_v1,
)
from football_system.infrastructure.database.versioned_quant_schema import (
    admitted_training_sql_v2,
)
from football_system.infrastructure.database.production_inference_repository import (
    SqlAlchemyProductionInferenceRepository,
)
from football_system.infrastructure.database.production_quant_repository import (
    SqlAlchemyProductionQuantRepository,
    _verification_scope,
    _verified_read,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes


@dataclass(frozen=True)
class ProductionAuditOperation:
    session: Session
    transaction: object
    analysis_run_id: str
    run: AnalysisRun
    binding: ProductionInferenceBindingV1
    start: CurrentAuthorizationInputsV1
    require_sidecar: bool


class SqlAlchemyProductionAuditRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        production_repository: SqlAlchemyProductionQuantRepository,
        inference_repository: SqlAlchemyProductionInferenceRepository,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if (
            type(inference_repository) is not SqlAlchemyProductionInferenceRepository
            or type(production_repository) is not SqlAlchemyProductionQuantRepository
            or inference_repository._production is not production_repository
            or any(
                repository._sessions.kw["bind"] is not session_factory.kw["bind"]
                for repository in (production_repository, inference_repository)
            )
        ):
            raise ValueError("production audit requires matching concrete repositories")
        self._sessions = session_factory
        self._production = production_repository
        self._inference = inference_repository
        self._clock = clock
        self._last_clock = None
        self._clock_lock = Lock()

    def _now(self):
        with self._clock_lock:
            at = normalize_utc(self._clock())
            if self._last_clock is not None and at < self._last_clock:
                raise ValueError("production audit clock moved backwards")
            self._last_clock = at
            return at

    def _session(self, session):
        if (
            not session.in_transaction()
            or session.get_bind() is not self._sessions.kw["bind"]
            or session.get_bind().dialect.name != "sqlite"
        ):
            raise ValueError(
                "production audit requires an active same-database transaction"
            )

    def gate_run(self, analysis_run_id: str) -> ApprovedTrainingHistoryAuditV1:
        """Require a stored packet/sidecar and fresh authorization, never create one."""
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            operation = self.begin_in_session(session, analysis_run_id)
            return self.finish_in_session(session, operation)

    @_verified_read
    def begin_in_session(
        self, session: Session, analysis_run_id: str, *, require_sidecar: bool = True
    ) -> ProductionAuditOperation:
        self._session(session)
        at = self._now()
        context = self._context(session, analysis_run_id)
        binding, run, state, release, plan = context
        current = self._authorize(session, binding, release, plan, at)
        operation = ProductionAuditOperation(
            session,
            session.get_transaction(),
            run.analysis_run_id,
            run,
            binding,
            current,
            require_sidecar,
        )
        self._validate_sidecar(session, operation, context, current)
        return operation

    def finish_in_session(
        self, session: Session, operation: ProductionAuditOperation
    ) -> ApprovedTrainingHistoryAuditV1 | None:
        return SqlAlchemyProductionAuditRepository.revalidate_in_session(
            self, session, operation
        )

    def revalidate_in_session(
        self, session: Session, operation: ProductionAuditOperation
    ) -> ApprovedTrainingHistoryAuditV1 | None:
        self._session(session)
        if (
            type(operation) is not ProductionAuditOperation
            or operation.session is not session
            or operation.transaction is not session.get_transaction()
        ):
            raise ValueError(
                "production audit operation belongs to another transaction"
            )
        session.expire_all()
        with _verification_scope(session):
            context = self._context(session, operation.analysis_run_id)
            binding, run, state, release, plan = context
            if binding != operation.binding:
                raise ValueError("production binding changed during audit operation")
            completion = self._authorize(session, binding, release, plan, self._now())
            assert_authorization_progression(operation.start, completion)
            audit = self._validate_sidecar(session, operation, context, completion)
        # The verified reader visits all these immutable event rows but filters
        # its returned view by recording time. Capture the complete transaction
        # snapshot first, including future-recorded/future-effective withdrawals.
        events = self._authorization_events_in_session(session, release)
        verified = self._authorize(session, binding, release, plan, self._now())
        # No SQL, evidence read, or other authorization loader may follow this
        # actual-time observation. Only pure gates remain before commit/rename.
        final = authorization_at(verified, self._now(), **events)
        assert_authorization_progression(verified, final)
        assert_authorization_progression(completion, final)
        release_active_for_inference(
            release=release,
            plan=plan,
            current=final,
            state_retention_horizon=binding.state_retention_horizon,
            audit_retention_horizon=binding.audit_retention_horizon,
        )
        return audit

    def _authorization_events_in_session(self, session, release):
        approval_id = release.training_approval.artifact_id
        tables = Base.metadata.tables
        filters = (
            (
                "revocations",
                GrantRevocationV1,
                "training_history_revocation_events",
                tables["training_history_revocation_events"].c.approval_id
                == approval_id,
            ),
            (
                "successors",
                ApprovalSuccessorV1,
                "training_history_successors",
                tables["training_history_successors"].c.predecessor_id == approval_id,
            ),
        )
        return {
            "observed_records": self._production.observed_records_in_session(
                session, release.training_manifest.content_payload.history
            ),
            "corrections": self._production.correction_events_in_session(
                session, release.training_manifest.content_payload.history
            ),
            **{
                name: tuple(
                    sorted(
                        (
                            model.model_validate_json(row["artifact_json"])
                            for row in session.execute(
                                select(tables[table]).where(condition)
                            ).mappings()
                        ),
                        key=lambda item: item.artifact_id,
                    )
                )
                for name, model, table, condition in filters
            },
        }

    def create_in_session(
        self, session: Session, packet_id: str, operation: ProductionAuditOperation
    ) -> ApprovedTrainingHistoryAuditV1:
        """Called only after inserting a new packet, in its still-open transaction."""
        self._session(session)
        if (
            type(operation) is not ProductionAuditOperation
            or operation.session is not session
            or operation.transaction is not session.get_transaction()
            or operation.require_sidecar
        ):
            raise ValueError("audit creation requires the packet creation transaction")
        context = self._context(session, operation.analysis_run_id)
        binding, run, state, release, plan = context
        stored, packet = self._packet(session, run)
        if stored.packet_id != packet_id or binding != operation.binding:
            raise ValueError("audit creation packet/binding mismatch")
        if _rows(session, "production_audit_bundles", packet_id=packet_id):
            return self.revalidate_in_session(session, operation)
        audit = freeze_approved_training_history_audit(
            packet=packet,
            input_manifest_json=run.input_manifest_json,
            model_state=state,
            release=release,
            plan=plan,
            operation_start=operation.start,
            operation_completion=self._authorize(
                session, binding, release, plan, self._now()
            ),
            state_retention_horizon=binding.state_retention_horizon,
            audit_retention_horizon=binding.audit_retention_horizon,
            generated_at_utc=packet.generated_at_utc,
        )
        # The sidecar contains only typed summaries/hashes, never source evidence.
        from football_system.infrastructure.files.production_audit_bundle import (
            validate_production_audit_pair,
        )

        validate_production_audit_pair(
            stored.packet_json.encode("utf-8"), canonical_json(audit).encode("utf-8")
        )
        if isinstance(
            release.training_manifest.content_payload.history,
            ObservedTrainingHistoryGraphV1,
        ):
            from football_system.infrastructure.database.observed_quant_schema import (
                OBSERVED_TABLES,
                OBSERVED_QUANT_TABLES,
                observed_audit_row,
            )

            session.execute(
                OBSERVED_TABLES[OBSERVED_QUANT_TABLES[6]]
                .insert()
                .values(**observed_audit_row(audit, release))
            )
        session.execute(
            Base.metadata.tables["production_audit_bundles"]
            .insert()
            .values(**_audit_row(audit))
        )
        return self.revalidate_in_session(session, operation)

    def load_bundle(
        self, packet_id: str
    ) -> tuple[StoredAnalysisPacket, ApprovedTrainingHistoryAuditV1]:
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            rows = _rows(session, "analysis_packets", packet_id=packet_id)
            if not rows:
                raise KeyError("unknown production AnalysisPacket")
            operation = self.begin_in_session(
                session, rows[0]["parent_analysis_run_id"]
            )
            stored, _ = self._packet(session, operation.run)
            return stored, self.finish_in_session(session, operation)

    def load_audit(self, packet_id: str) -> ApprovedTrainingHistoryAuditV1:
        return self.load_bundle(packet_id)[1]

    @contextmanager
    def publication_gate(
        self, packet_bytes: bytes, audit: ApprovedTrainingHistoryAuditV1
    ):
        """Validate the supplied pair, hold the revocation lock through publication.

        The final gate runs BEFORE the caller's atomic rename, not after files
        become visible. This context never writes a packet or accepts an audit.
        """
        from football_system.infrastructure.files.production_audit_bundle import (
            validate_production_audit_pair,
        )

        packet, audit = validate_production_audit_pair(
            packet_bytes, canonical_json(audit).encode("utf-8")
        )
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            operation = self.begin_in_session(
                session, packet.analysis_run.analysis_run_id
            )
            _, expected_packet = self._packet(session, operation.run)
            expected_audit = self.finish_in_session(session, operation)
            if packet != expected_packet or audit != expected_audit:
                raise ValueError("supplied production bundle differs from stored pair")
            yield

    def validate_bundle(
        self, packet_bytes: bytes, audit: ApprovedTrainingHistoryAuditV1
    ) -> None:
        with self.publication_gate(packet_bytes, audit):
            pass

    @_verified_read
    def _context(self, session, analysis_run_id):
        from football_system.infrastructure.database.review_repositories import (
            _load_model_state,
        )

        rows = _rows(session, "analysis_runs", analysis_run_id=analysis_run_id)
        if not rows:
            raise KeyError("unknown production AnalysisRun")
        run = AnalysisRun.model_validate(dict(rows[0]))
        if run.status != "COMPLETED":
            raise ValueError("production audit requires a COMPLETED run")
        rows = _rows(
            session,
            "quant_model_state_production_releases",
            analysis_run_id=analysis_run_id,
        )
        if len(rows) != 1:
            raise ValueError("production audit requires exact inference binding")
        binding = ProductionInferenceBindingV1.model_validate(
            strict_json_bytes(rows[0]["binding_json"].encode("utf-8"))
        )
        self._inference.assert_bindings_in_session(session, analysis_run_id, binding)
        state = _load_model_state(session, binding.quant_model_state_id)
        manifest = strict_json_bytes(run.input_manifest_json.encode("utf-8"))
        matches = tuple(Match.model_validate(item) for item in manifest["matches"])
        release, plan, projected = self._inference._verify(
            session, binding, run, (state,), matches
        )
        self._inference._assert_persisted_model(
            session, binding, run, state, projected, matches
        )
        return binding, run, state, release, plan

    def _authorize(self, session, binding, release, plan, at):
        current = self._production.authorization_in_session(
            session, release.artifact_id, at
        )
        if current.actual_at_utc != at:
            raise ValueError("production audit authorization actual time mismatch")
        assert_authorization_progression(binding.completion_authorization, current)
        release_active_for_inference(
            release=release,
            plan=plan,
            current=current,
            state_retention_horizon=binding.state_retention_horizon,
            audit_retention_horizon=binding.audit_retention_horizon,
        )
        return current

    def _packet(self, session, run):
        from football_system.infrastructure.database.review_repositories import (
            SqlAlchemyReviewArtifactRepository,
        )

        rows = _rows(
            session, "analysis_packets", parent_analysis_run_id=run.analysis_run_id
        )
        if len(rows) != 1:
            raise ValueError("production audit requires exactly one stored V3 packet")
        row = rows[0]
        packet = _parse_analysis_packet(row["packet_json"].encode("utf-8"))
        if not isinstance(packet, AnalysisPacketV3):
            raise ValueError("production audit requires ANALYSIS_PACKET_V3")
        source = SqlAlchemyReviewArtifactRepository._load_packet_source_v3(
            session, run.analysis_run_id
        )
        if (
            build_analysis_packet_v3(source, packet.generated_at_utc) != packet
            or canonical_json(packet.model_dump(mode="json")) != row["packet_json"]
            or row["packet_id"] != packet.packet_id
            or row["packet_hash"] != packet.packet_hash
            or row["generated_at_utc"] != packet.generated_at_utc
            or row["schema_version"] != packet.schema_version
        ):
            raise ValueError("production packet differs from full persisted source")
        return StoredAnalysisPacket(
            **{key: row[key] for key in StoredAnalysisPacket.model_fields}
        ), packet

    def _validate_sidecar(self, session, operation, context, completion):
        binding, run, state, release, plan = context
        packets = _rows(
            session, "analysis_packets", parent_analysis_run_id=run.analysis_run_id
        )
        rows = _rows(
            session, "production_audit_bundles", analysis_run_id=run.analysis_run_id
        )
        if not packets and not rows and not operation.require_sidecar:
            return None
        if len(rows) != 1:
            raise ValueError(
                "approved operation requires stored production audit sidecar"
            )
        stored, packet = self._packet(session, run)
        from football_system.infrastructure.files.production_audit_bundle import (
            validate_production_audit_pair,
        )

        _, audit = validate_production_audit_pair(
            stored.packet_json.encode("utf-8"), rows[0]["audit_json"].encode("utf-8")
        )
        if dict(rows[0]) != _audit_row(audit) or _rows(
            session, "production_audit_packet_requirements", packet_id=stored.packet_id
        ) != [{"packet_id": stored.packet_id}]:
            raise ValueError("production audit normalized columns/obligation mismatch")
        if isinstance(
            release.training_manifest.content_payload.history,
            ObservedTrainingHistoryGraphV1,
        ):
            from football_system.infrastructure.database.observed_quant_schema import (
                OBSERVED_TABLES,
                OBSERVED_QUANT_TABLES,
                observed_audit_row,
            )

            table = OBSERVED_TABLES[OBSERVED_QUANT_TABLES[6]]
            children = (
                session.execute(
                    select(table).where(table.c.packet_id == stored.packet_id)
                )
                .mappings()
                .all()
            )
            if [dict(row) for row in children] != [observed_audit_row(audit, release)]:
                raise ValueError("observed audit typed basis child missing or changed")
        if (
            audit.content_payload.state_retention_horizon
            != binding.state_retention_horizon
            or audit.content_payload.audit_retention_horizon
            != binding.audit_retention_horizon
        ):
            raise ValueError(
                "production audit retention differs from inference binding"
            )
        validate_approved_training_history_audit(
            audit=audit,
            packet=packet,
            input_manifest_json=run.input_manifest_json,
            model_state=state,
            release=release,
            plan=plan,
            operation_start=operation.start,
            operation_completion=completion,
        )
        return audit


class ProductionAuditGuard:
    """Shared transaction scope for concrete review repositories, including retries.

    Detection is independent of the optional dependency. Duck-typed/no-op auditors
    are not a trust boundary; approved runs require the concrete implementation.
    """

    def __init__(self, sessions, audit_repository):
        self._sessions = sessions
        self.repository = audit_repository
        self._active = ContextVar(
            f"production_audit_transaction_{id(self)}", default=None
        )

    @contextmanager
    def transaction(self):
        active = self._active.get()
        if active is not None:
            yield active[0]
            return
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            operations = {}
            token = self._active.set((session, operations))
            try:
                yield session
                session.flush()
                for operation in operations.values():
                    # Use the concrete implementation, not injected no-op overrides.
                    SqlAlchemyProductionAuditRepository.finish_in_session(
                        self.repository, session, operation
                    )
            finally:
                self._active.reset(token)

    def require(self, session, analysis_run_id, *, require_sidecar=True):
        rows = _rows(session, "analysis_runs", analysis_run_id=analysis_run_id)
        config = (
            strict_json_loads(rows[0]["config_json"].encode("utf-8")) if rows else {}
        )
        if not isinstance(config, dict) or not isinstance(
            config.get("request", {}), dict
        ):
            raise ValueError("invalid stored production operation config")
        request = config.get("request", {})
        training_sql = admitted_training_sql_v1
        if session.scalar(
            text(
                "SELECT 1 FROM sqlite_master WHERE name='training_correction_result_bindings'"
            )
        ):
            training_sql = admitted_training_sql_v2
        if session.scalar(
            text("SELECT 1 FROM sqlite_master WHERE name='observed_result_bindings'")
        ):
            from football_system.infrastructure.database.observed_quant_schema import (
                admitted_training_sql_v3,
            )

            training_sql = admitted_training_sql_v3
        requires_audit = (
            request.get("model_training_use_class") == "APPROVED_TRAINING_HISTORY"
            or request.get("production_model_release_id") is not None
            or request.get("production_target_acceptance_plan_id") is not None
            or any(
                _rows(session, table, analysis_run_id=analysis_run_id)
                for table in (
                    "quant_model_state_production_releases",
                    "analysis_run_target_acceptance_plans",
                    "production_audit_bundles",
                )
            )
            or session.scalar(
                text(f"SELECT {training_sql(':analysis_run_id')}"),
                {"analysis_run_id": analysis_run_id},
            )
        )
        if not requires_audit:
            return None
        if type(self.repository) is not SqlAlchemyProductionAuditRepository:
            raise ValueError(
                "approved run requires concrete production audit repository"
            )
        operations = self._active.get()[1]
        existing = operations.get(analysis_run_id)
        if existing is not None:
            # Nested calls share the outer start/finish gate and cannot return
            # outside its transaction. Do not turn that into a rights cache.
            if require_sidecar and not existing.require_sidecar:
                existing = replace(existing, require_sidecar=True)
                operations[analysis_run_id] = existing
            return existing
        if operations:
            # Otherwise a later run's expensive final read could invalidate the
            # earlier run's clock observation before their shared commit.
            raise ValueError(
                "production audit transaction requires a single approved run"
            )
        operation = SqlAlchemyProductionAuditRepository.begin_in_session(
            self.repository, session, analysis_run_id, require_sidecar=require_sidecar
        )
        operations[analysis_run_id] = operation
        return operation

    @contextmanager
    def operation(
        self,
        *,
        analysis_run_id=None,
        packet_id=None,
        review_artifact_id=None,
        fusion_run_id=None,
        require_sidecar=True,
    ):
        with self.transaction() as session:
            for table, key, value in (
                ("analysis_packets", "packet_id", packet_id),
                ("llm_review_artifacts", "review_artifact_id", review_artifact_id),
                ("fusion_runs", "fusion_run_id", fusion_run_id),
            ):
                if value is not None:
                    rows = _rows(session, table, **{key: value})
                    if len(rows) != 1:
                        raise KeyError(f"unknown {table} artifact")
                    resolved = rows[0]["parent_analysis_run_id"]
                    if analysis_run_id is not None and analysis_run_id != resolved:
                        raise ValueError("production operation lineage mismatch")
                    analysis_run_id = resolved
            if analysis_run_id is None:
                raise ValueError("production operation requires a run identity")
            self.require(session, analysis_run_id, require_sidecar=require_sidecar)
            yield session


def _rows(session, table, **filters):
    table = Base.metadata.tables[table]
    return (
        session.execute(
            select(table).where(
                *(table.c[key] == value for key, value in filters.items())
            )
        )
        .mappings()
        .all()
    )


def _audit_row(audit):
    content = audit.content_payload
    return dict(
        packet_id=content.packet.artifact_id,
        analysis_run_id=content.analysis_run_id,
        quant_model_state_id=content.quant_model_state_id,
        release_id=content.release.artifact_id,
        plan_id=content.target_acceptance_plan.artifact_id,
        audit_id=audit.artifact_id,
        audit_hash=audit.content_hash,
        packet_hash=content.packet.content_hash,
        audit_json=canonical_json(audit),
        generated_at_utc=content.generated_at_utc,
    )
