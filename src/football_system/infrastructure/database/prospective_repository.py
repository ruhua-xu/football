"""Trusted-time, append-only prospective operations and complete source replay."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib

from pydantic import BaseModel
from sqlalchemy import select, text

from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import normalize_utc, stable_id
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import MarketArtifact, content_hash, revalidate
from football_system.domain.prospective import (
    PROSPECTIVE_ARTIFACT_TYPES, DecisionLockV1, PreKickoffInvalidationV1, ProspectiveRunV1,
    ProspectiveSettlementV1, ProspectiveValidationReportV1, ResultObservationV1,
    V4CorrectionAuditV1, ValidationCensusRowV1, ValidationEpochCloseV1, ValidationEpochV1,
)
from football_system.domain.prospective_evidence import (
    AbsenceFactV1, EvidenceSnapshotV1, FormFactV1, LineupFactV1, ProspectiveEvidenceBindingV1,
    ScheduleFactV1, freshness,
)
from football_system.domain.services.prospective import (
    assert_before_kickoff, configuration_for, correction_values, evidence_use, football_projection,
    lock_values, prepare_values, require, settlement_values,
)
from football_system.domain.services.prospective_validation import report_values
from football_system.domain.services.review_v4 import export_packet_v4
from football_system.domain.settlement import MatchResult
from football_system.infrastructure.database.historical_repositories import _append_match_result, _match_result
from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository, _one, _rows, rows_for
from football_system.infrastructure.database.models import (
    Base, FixtureIngestionCaptureRecord, FixtureObservationRecord, MatchRecord, MatchResultRecord,
    ProviderMatchMappingRecord, ProviderRecord,
)
from football_system.infrastructure.database.prospective_schema import CHILD_SPECS, NESTED_SPECS, OPERATIONS, TYPE_SPECS
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.files.prospective import SystemProspectiveClock, default_prospective_policy
from football_system.infrastructure.files.return_distribution import default_return_configuration, strict_return_json

LIMIT = 64 * 1024 * 1024
ORIGIN = datetime(1970, 1, 1, tzinfo=timezone.utc)
TIMES = {"VALIDATION_EPOCH_V1": "created_at_utc", "VALIDATION_EPOCH_CLOSE_V1": "closed_at_utc",
    "EVIDENCE_SNAPSHOT_V1": "ingested_at_utc", "PROSPECTIVE_RUN_V1": "prepared_at_utc",
    "V4_CORRECTION_AUDIT_V1": "imported_at_utc", "DECISION_LOCK_V1": "locked_at_utc",
    "PRE_KICKOFF_INVALIDATION_V1": "invalidated_at_utc", "RESULT_OBSERVATION_V1": "ingested_at_utc",
    "PROSPECTIVE_SETTLEMENT_V1": "settled_at_utc", "PROSPECTIVE_VALIDATION_REPORT_V1": "created_at_utc"}


def micros(at):
    delta = normalize_utc(at)-ORIGIN
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def stamp(at):
    return normalize_utc(at).isoformat().replace("+00:00", "Z")


def at(value, path):
    if not path:
        return value
    for key in path.split("."):
        if value is None:
            return None
        value = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
    return value


def pv_nodes(value):
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from pv_nodes(getattr(value, name))
        if isinstance(value, MarketArtifact) and value.schema_version in PROSPECTIVE_ARTIFACT_TYPES:
            yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from pv_nodes(item)


def pv_rows(value, event):
    name, fields = TYPE_SPECS[value.schema_version]
    header = dict(artifact_id=value.artifact_id, schema_version=value.schema_version, content_hash=value.content_hash,
        artifact_json=canonical_json(value), event_key=event["request_key"], recorded_at_utc=event["recorded_at_utc"],
        recorded_at_us=event["recorded_at_us"], clock_basis=event["clock_basis"])
    typed = dict(artifact_id=value.artifact_id, **{key: at(value, path) for key, (path, *_) in fields.items()})
    if name == "pv_epochs":
        typed.update(starts_us=micros(value.starts_at_utc), ends_us=micros(value.ends_at_utc), mode=value.mode)
    elif name == "pv_runs":
        typed.update(slate_key=value.slate_key, slate_date=value.slate_date.isoformat(), cutoff_us=micros(value.decision_cutoff),
                     earliest_us=micros(min(i.kickoff_at_utc for i in value.identities)), status=value.status)
    elif name == "pv_evidence":
        typed.update(category=value.claim.fact_category.value, source_identity=value.claim.source_identity, ingested_us=micros(value.ingested_at_utc))
    elif name == "pv_locks":
        typed.update(locked_us=micros(value.locked_at_utc), earliest_us=micros(value.earliest_kickoff_at_utc))
    elif name == "pv_invalidations":
        typed.update(invalidated_us=micros(value.invalidated_at_utc), deadline_us=micros(value.deadline_at_utc))
    elif name == "pv_observations":
        typed.update(source_identity=value.claim.source_identity, source_version_id=value.claim.source_version_id, ingested_us=micros(value.ingested_at_utc))
    elif name == "pv_settlements":
        typed.update(settled_us=micros(value.settled_at_utc))
    elif name == "pv_reports":
        typed.update(as_of_us=micros(value.as_of_at_utc))
    elif name == "pv_epoch_closes":
        typed.update(closed_us=micros(value.closed_at_utc))
    result = {"pv_artifacts": [header], name: [typed]}
    def child(item, index, fields, group=None, table=None):
        row = dict(parent_id=value.artifact_id, position=index, item_json=canonical_json(item),
                   **{key: at(item, path) for key, (path, *_) in fields.items()})
        if group is not None:
            row["group_no"] = group
        if table == "pv_lock_units":
            row["market_hash"] = item.market_key.market_hash
        return row
    for kind, path, table, fields in CHILD_SPECS:
        if kind == value.schema_version:
            result[table] = [child(item, i, fields, table=table) for i, item in enumerate(at(value, path) or ())]
    for kind, outer, inner, table, _, fields in NESTED_SPECS:
        if kind == value.schema_version:
            result[table] = [child(item, i, fields, group=g) for g, parent in enumerate(at(value, outer)) for i, item in enumerate(at(parent, inner))]
    result["pv_seals"] = [dict(artifact_id=value.artifact_id)]
    return result


class SqlAlchemyProspectiveRepository:
    def __init__(self, sessions, *, clock=None, policy=None):
        self._sessions = sessions
        self.clock = clock or SystemProspectiveClock()
        self.policy = policy or default_prospective_policy()
        self._mm = SqlAlchemyMultiMarketRepository(sessions)
        self._rd = SqlAlchemyReturnDistributionRepository(sessions)

    def _moment(self, session):
        value = normalize_utc(self.clock.now())
        previous = session.scalar(text("SELECT MAX(recorded_at_us) FROM pv_receipts"))
        require(previous is None or micros(value) >= previous, "TRUSTED_CLOCK_REGRESSION")
        return value

    def retry(self, operation, request):
        request = revalidate(request)
        with self._sessions.begin() as session:
            rows = _rows(session, "pv_receipts", request_key=request.request_key)
            if not rows:
                return None
            receipt = rows[0]
            require((receipt["operation"], receipt["request_hash"], receipt["request_json"]) == (
                operation, content_hash("PROSPECTIVE_REQUEST_"+operation, request), canonical_json(request)),
                "REQUEST_KEY_REUSED_WITH_DIFFERENT_CONTENT")
            return self._load(session, receipt["response_id"])

    def retry_workflow(self, request_key, workflow_hash):
        with self._sessions.begin() as session:
            rows = _rows(session, "pv_receipts", request_key=request_key)
            if not rows:
                return None
            receipt = rows[0]
            request = strict_return_json(receipt["request_json"].encode())
            require(receipt["operation"] == "LOCK" and request.get("workflow_hash") == workflow_hash,
                    "REQUEST_KEY_REUSED_WITH_DIFFERENT_CONTENT")
            return self._load(session, receipt["response_id"])

    def _execute(self, operation, request, builder):
        request = revalidate(request)
        raw = canonical_json(request)
        digest = content_hash("PROSPECTIVE_REQUEST_"+operation, request)
        with self._sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = _rows(session, "pv_receipts", request_key=request.request_key)
            if existing:
                receipt = existing[0]
                require((receipt["operation"], receipt["request_hash"], receipt["request_json"]) == (operation, digest, raw), "REQUEST_KEY_REUSED_WITH_DIFFERENT_CONTENT")
                return self._load(session, receipt["response_id"])
            event_time = []
            def moment():
                if not event_time:
                    event_time.append(self._moment(session))
                return event_time[0]
            root, extra = builder(session, moment)
            at_utc = moment()
            final = normalize_utc(self.clock.now())
            require(final >= at_utc and (final-at_utc).total_seconds() <= 30, "OPERATION_CLOCK_WINDOW_EXCEEDED")
            if operation == "LOCK":
                epoch = self._load(session, root.epoch.artifact_id)
                run = self._load(session, root.run.artifact_id)
                assert_before_kickoff(epoch, run, final)
                if extra:
                    require(final < extra[0].deadline_at_utc, "POST_KICKOFF_SUPERSESSION_FORBIDDEN")
                self._kickoff_guard(session, run, final)
            event = dict(request_key=request.request_key, operation=operation, request_hash=digest, request_json=raw,
                         sequence=self._watermark(session)+1,
                         response_id=root.artifact_id, recorded_at_utc=stamp(at_utc), recorded_at_us=micros(at_utc), clock_basis=self.clock.basis)
            values = [*extra, root]
            require(all(len(canonical_json(v).encode()) <= LIMIT for v in values), "PROSPECTIVE_ARTIFACT_SPACE_TOO_LARGE")
            session.execute(Base.metadata.tables["pv_receipts"].insert(), event)
            for value in values:
                self._put(session, value, event)
            return root

    def _put(self, session, value, event):
        for node in pv_nodes(value):
            previous = _rows(session, "pv_artifacts", artifact_id=node.artifact_id)
            if previous:
                self._verify_rows(session, node)
                continue
            for table, rows in pv_rows(node, event).items():
                if rows:
                    session.execute(Base.metadata.tables[table].insert(), rows)
            self._verify_rows(session, node)
            session.info.setdefault("pv_verified", {})[node.artifact_id] = node

    def _old(self, session, reference, schema):
        value = self._rd._load(session, reference.artifact_id) if schema.startswith(("RETURN_", "PORTFOLIO_RETURN_")) else self._mm._load(session, reference.artifact_id)
        require(value.schema_version == schema and ArtifactRefV1.of(value) == reference, "PROSPECTIVE_SOURCE_HASH_OR_TYPE_MISMATCH")
        return value

    def _verify_rows(self, session, value):
        header = _one(session, "pv_artifacts", artifact_id=value.artifact_id)
        receipt = _one(session, "pv_receipts", request_key=header["event_key"])
        require(content_hash("PROSPECTIVE_REQUEST_"+receipt["operation"], strict_return_json(receipt["request_json"].encode())) == receipt["request_hash"], "CORRUPT_PROSPECTIVE_REQUEST_RECEIPT")
        if receipt["response_id"] == value.artifact_id:
            require(OPERATIONS[receipt["operation"]] == value.schema_version, "PROSPECTIVE_OPERATION_TYPE_MISMATCH")
            self._verify_command(session, value, receipt)
        field = TIMES.get(value.schema_version)
        if field:
            require(micros(getattr(value, field)) == header["recorded_at_us"]
                    and stamp(getattr(value, field)) == header["recorded_at_utc"], "FORGED_PROSPECTIVE_EVENT_TIME")
        if hasattr(value, "clock_basis"):
            require(value.clock_basis == header["clock_basis"], "PROSPECTIVE_CLOCK_BASIS_MISMATCH")
        typed = TYPE_SPECS[value.schema_version][0]
        for name, expected in pv_rows(value, receipt).items():
            key = "artifact_id" if name in {"pv_artifacts", "pv_seals", typed} else "parent_id"
            actual = _rows(session, name, **{key: value.artifact_id})
            require(sorted(canonical_json(dict(r)) for r in actual) == sorted(canonical_json(r) for r in expected), "CORRUPT_PROSPECTIVE_GRAPH")

    def _verify_command(self, session, value, receipt):
        from football_system.application import prospective_requests as requests
        classes = {"EPOCH_CREATE": requests.EpochRequestV1, "EVIDENCE_IMPORT": requests.EvidenceImportRequestV1,
            "PREPARE": requests.PrepareProspectiveRequestV1, "REVIEW_IMPORT": requests.ReviewAuditRequestV1,
            "LOCK": requests.LockProspectiveRequestV1, "RESULT_IMPORT": requests.ResultImportRequestV1,
            "SETTLE": requests.SettleProspectiveRequestV1, "REPORT": requests.ReportProspectiveRequestV1,
            "EPOCH_CLOSE": requests.CloseEpochRequestV1}
        operation = receipt["operation"]
        request = classes[operation].model_validate(strict_return_json(receipt["request_json"].encode()))
        require(request.request_key == receipt["request_key"], "REQUEST_KEY_BINDING_MISMATCH")
        if operation == "EPOCH_CREATE":
            actual = dict(seed_plan_id=value.seed_plan.artifact_id, name=value.name, mode=value.mode,
                starts_at_utc=value.starts_at_utc, ends_at_utc=value.ends_at_utc,
                result_source_identity=value.result_source_identity, previous_epoch_id=value.previous_epoch.artifact_id if value.previous_epoch else None)
        elif operation == "EVIDENCE_IMPORT":
            actual = dict(evidence=self._load(session, value.snapshot.artifact_id).claim)
        elif operation == "PREPARE":
            actual = dict(epoch_id=value.epoch.artifact_id, analysis_id=value.analysis.artifact_id, slate_key=value.slate_key,
                slate_date=value.slate_date, budget_fen=value.budget_fen,
                supersedes_run_id=value.supersedes.artifact_id if value.supersedes else None)
            require(set(request.evidence_binding_ids) == {u.binding.artifact_id for u in value.evidence}, "REQUEST_EVIDENCE_SET_MISMATCH")
        elif operation == "REVIEW_IMPORT":
            actual = dict(run_id=value.run.artifact_id, review_id=value.review.artifact_id, reasons=value.reasons)
        elif operation == "LOCK":
            actual = dict(run_id=value.run.artifact_id, correction_audit_id=value.correction_audit.artifact_id, optimizer_run_id=value.optimizer.artifact_id)
        elif operation == "RESULT_IMPORT":
            actual = dict(result=value.claim)
        elif operation == "SETTLE":
            actual = dict(run_id=value.run.artifact_id)
        elif operation == "REPORT":
            actual = dict(epoch_id=value.epoch.artifact_id, as_of_at_utc=value.as_of_at_utc)
        else:
            actual = dict(epoch_id=value.epoch.artifact_id)
        require(all(getattr(request, key) == expected for key, expected in actual.items()), "REQUEST_RESPONSE_BINDING_MISMATCH")

    def _load(self, session, identity):
        cache = session.info.setdefault("pv_verified", {})
        if identity in cache:
            return cache[identity]
        active = session.info.setdefault("pv_loading", set())
        require(identity not in active, "CYCLIC_PROSPECTIVE_REFERENCE")
        active.add(identity)
        try:
            row = _one(session, "pv_artifacts", artifact_id=identity)
            cls = PROSPECTIVE_ARTIFACT_TYPES.get(row["schema_version"])
            require(cls is not None, "UNKNOWN_PROSPECTIVE_ARTIFACT")
            value = cls.model_validate(strict_return_json(row["artifact_json"].encode(), limit=LIMIT))
            for node in pv_nodes(value):
                self._verify_rows(session, node)
            self._replay(session, value)
            cache[identity] = value
            return value
        finally:
            active.remove(identity)

    def load(self, identity):
        with self._sessions.begin() as session:
            return self._load(session, identity)

    def _epoch(self, session, identity, at=None, *, allow_closed=False):
        epoch = self._load(session, identity)
        require(type(epoch) is ValidationEpochV1 and epoch.policy == self.policy, "EPOCH_IMPLEMENTATION_OR_POLICY_MISMATCH")
        if at is not None:
            require(epoch.starts_at_utc <= at < epoch.ends_at_utc, "EPOCH_NOT_ACTIVE")
            if not allow_closed:
                require(not _rows(session, "pv_epoch_closes", epoch_id=identity), "EPOCH_CLOSED")
        return epoch

    def _kickoff_guard(self, session, run, at_utc, watermark=None):
        for identity in run.identities:
            match = session.get(MatchRecord, identity.match_id)
            require(match is not None and match.kickoff_at_utc == identity.kickoff_at_utc, "KICKOFF_SOURCE_CHANGED")
            require(match.status not in {"FINISHED", "CANCELLED", "POSTPONED"}, "LOOKAHEAD_RISK_OR_FIXTURE_UNAVAILABLE")
            require(not session.scalar(select(MatchResultRecord.match_result_id).where(
                MatchResultRecord.internal_match_id == identity.match_id, MatchResultRecord.ingested_at_utc <= at_utc)), "RESULT_KNOWN_BEFORE_LOCK")
            visible = session.scalars(select(FixtureObservationRecord).join(FixtureIngestionCaptureRecord,
                FixtureIngestionCaptureRecord.ingestion_id == FixtureObservationRecord.ingestion_id).where(
                FixtureObservationRecord.internal_match_id == identity.match_id,
                FixtureObservationRecord.available_at_utc <= at_utc, FixtureIngestionCaptureRecord.ingested_at_utc <= at_utc))
            for observation in visible:
                require(observation.kickoff_at_utc == identity.kickoff_at_utc and observation.status == "SCHEDULED", "KICKOFF_CHANGED_PREPARE_AGAIN")
            rows = self._visible(session, "pv_evidence", "t.category IN ('SCHEDULE','REST') AND t.ingested_us<=:at", {"at": micros(at_utc)}, watermark)
            for artifact_id in rows:
                evidence = self._load(session, artifact_id)
                for fixture in evidence.claim.structured_payload.fixtures:
                    if fixture.match_id == identity.match_id:
                        require(fixture.kickoff_at_utc == identity.kickoff_at_utc and fixture.status not in {"FINISHED", "CANCELLED", "POSTPONED", "UNKNOWN"}, "KICKOFF_CHANGED_PREPARE_AGAIN")

    def _manual_payload(self, session, claim, now):
        match = session.get(MatchRecord, claim.match_id)
        require(match is not None, "MANUAL_MATCH_ID_NOT_REGISTERED")
        require(claim.verified_at_utc <= now < claim.retention_until_utc, "MANUAL_SOURCE_EXPIRED_OR_FUTURE")
        if self.clock.basis == "SYNTHETIC_TEST_CLOCK":
            require(claim.data_classification == "SYNTHETIC", "TEST_CLOCK_CANNOT_ADMIT_REAL_SOURCE")
        payload = getattr(claim, "structured_payload", None)
        if isinstance(payload, (LineupFactV1, AbsenceFactV1, ScheduleFactV1, FormFactV1)):
            require(payload.team_id in {match.home_team_id, match.away_team_id}, "MANUAL_TEAM_BINDING_MISMATCH")
        if isinstance(payload, (AbsenceFactV1, ScheduleFactV1, FormFactV1)):
            require(payload.as_of_at_utc <= claim.captured_at_utc, "FACT_AS_OF_AFTER_CAPTURE")
        if isinstance(payload, FormFactV1):
            for identity in payload.result_ids:
                record = session.get(MatchResultRecord, identity)
                require(record is not None, "FORM_RESULT_NOT_REGISTERED")
                fixture = session.get(MatchRecord, record.internal_match_id)
                require(fixture.internal_match_id != claim.match_id and payload.window_start_utc <= fixture.kickoff_at_utc < payload.window_end_utc
                        and max(record.available_at_utc, record.ingested_at_utc) <= payload.as_of_at_utc <= claim.captured_at_utc <= now,
                        "LOOKAHEAD_RISK_FORM_RESULT")
                require(payload.team_id in {fixture.home_team_id, fixture.away_team_id}, "FORM_TEAM_BINDING_MISMATCH")
                require(payload.location == "ANY" or (payload.location == "HOME" and fixture.home_team_id == payload.team_id)
                        or (payload.location == "AWAY" and fixture.away_team_id == payload.team_id), "FORM_LOCATION_MISMATCH")
                require(not session.scalar(select(MatchResultRecord.match_result_id).where(
                    MatchResultRecord.supersedes_match_result_id == identity, MatchResultRecord.ingested_at_utc <= payload.as_of_at_utc)), "FORM_SUPERSEDED_RESULT")
        if isinstance(payload, ScheduleFactV1):
            require(payload.as_of_at_utc <= claim.captured_at_utc <= now, "LOOKAHEAD_RISK_SCHEDULE")
            for fixture in payload.fixtures:
                old = session.get(MatchRecord, fixture.match_id)
                require(old is not None and (old.home_team_id, old.away_team_id) == (fixture.home_team_id, fixture.away_team_id), "SCHEDULE_CANONICAL_BINDING_REQUIRED")

    def create_epoch(self, request):
        def build(session, moment):
            seed = self._mm._load(session, request.seed_plan_id)
            require(seed.schema_version == "STRATEGY_PASS_PLAN_V2", "EPOCH_SEED_PLAN_REQUIRED")
            return_policy, objective = default_return_configuration()
            # Existing immutable rd_ configuration artifacts are persisted by their own repository before this command.
            self._old(session, ArtifactRefV1.of(return_policy), return_policy.schema_version)
            self._old(session, ArtifactRefV1.of(objective), objective.schema_version)
            previous = self._load(session, request.previous_epoch_id) if request.previous_epoch_id else None
            if previous:
                require(type(previous) is ValidationEpochV1 and _rows(session, "pv_epoch_closes", epoch_id=previous.artifact_id)
                        and request.starts_at_utc >= previous.ends_at_utc, "NEW_EPOCH_REQUIRES_CLOSED_PREDECESSOR")
            at_utc = moment()
            require(request.mode != "REAL_PROSPECTIVE" or self.clock.basis == "LOCAL_SYSTEM_UTC", "TEST_CLOCK_CANNOT_OPEN_REAL_EPOCH")
            epoch = ValidationEpochV1.freeze(name=request.name, mode=request.mode, starts_at_utc=request.starts_at_utc,
                ends_at_utc=request.ends_at_utc, created_at_utc=at_utc, clock_basis=self.clock.basis,
                seed_plan=ArtifactRefV1.of(seed), policy=self.policy, configuration=configuration_for(seed, return_policy, objective),
                result_source_identity=request.result_source_identity, previous_epoch=ArtifactRefV1.of(previous) if previous else None)
            return epoch, ()
        return self._execute("EPOCH_CREATE", request, build)

    def import_evidence(self, request, verified_hash):
        require(verified_hash == request.evidence.source_hash, "MANUAL_FILE_HASH_NOT_VERIFIED")
        def build(session, moment):
            at_utc = moment()
            claim = request.evidence
            self._manual_payload(session, claim, at_utc)
            snapshot = EvidenceSnapshotV1.freeze(claim=claim, ingested_at_utc=at_utc, assessed_at_utc=at_utc,
                clock_basis=self.clock.basis, freshness_limit_seconds=self.policy.category_max_age_seconds[claim.fact_category],
                freshness_status=freshness(claim.published_at_utc, at_utc, self.policy.category_max_age_seconds[claim.fact_category]),
                source_payload_hash=hashlib.sha256(canonical_json(claim).encode()).hexdigest())
            football = football_projection(snapshot)
            if _rows(session, "mm_artifacts", artifact_id=football.artifact_id):
                self._mm._verify_rows(session, football)
            else:
                for table, rows in rows_for(football).items():
                    if rows:
                        session.execute(Base.metadata.tables[table].insert(), rows)
            binding = ProspectiveEvidenceBindingV1.freeze(snapshot=ArtifactRefV1.of(snapshot), football_evidence=ArtifactRefV1.of(football), match_id=claim.match_id)
            return binding, (snapshot,)
        return self._execute("EVIDENCE_IMPORT", request, build)

    def _uses(self, session, ids, cutoff, epoch):
        require(len(set(ids)) == len(ids), "DUPLICATE_EVIDENCE_BINDING")
        values = []
        for identity in ids:
            binding = self._load(session, identity)
            require(type(binding) is ProspectiveEvidenceBindingV1, "EVIDENCE_BINDING_REQUIRED")
            snapshot = self._load(session, binding.snapshot.artifact_id)
            require(snapshot.claim.data_classification == "SYNTHETIC" if epoch.mode == "SYNTHETIC" else snapshot.claim.data_classification == "REAL_SOURCE_DATA", "EVIDENCE_CLASSIFICATION_MISMATCH")
            values.append(evidence_use(snapshot, binding, cutoff, epoch.policy))
        return tuple(values)

    def prepare(self, request):
        def build(session, moment):
            epoch = self._epoch(session, request.epoch_id)
            analysis = self._mm._load(session, request.analysis_id)
            require(analysis.schema_version == "MULTI_MARKET_ANALYSIS_V1", "SEALED_ANALYSIS_REQUIRED")
            previous = self._load(session, request.supersedes_run_id) if request.supersedes_run_id else None
            if previous:
                require(previous.epoch == ArtifactRefV1.of(epoch) and previous.slate_key == request.slate_key
                        and previous.slate_date == request.slate_date and _rows(session, "pv_locks", run_id=previous.artifact_id)
                        and not _rows(session, "pv_invalidations", old_run_id=previous.artifact_id), "INVALID_SUPERSEDING_RUN")
            at_utc = moment()
            self._epoch(session, request.epoch_id, at_utc)
            if previous:
                assert_before_kickoff(epoch, previous, at_utc)
            uses = self._uses(session, request.evidence_binding_ids, at_utc, epoch)
            run = ProspectiveRunV1.freeze(**prepare_values(epoch, analysis, uses, at_utc, self.clock.basis,
                request.slate_key, request.slate_date, request.budget_fen, previous))
            if run.status == "PREPARING":
                self._kickoff_guard(session, run, at_utc)
                packet = export_packet_v4(analysis)
                require(self._mm._load(session, packet.artifact_id) == packet, "EXACT_PREPARED_PACKET_MUST_BE_STORED")
            return run, ()
        return self._execute("PREPARE", request, build)

    def import_review_audit(self, request):
        def build(session, moment):
            run = self._load(session, request.run_id)
            epoch = self._epoch(session, run.epoch.artifact_id)
            review = self._mm._load(session, request.review_id)
            snapshots = {u.snapshot.artifact_id: self._load(session, u.snapshot.artifact_id) for u in run.evidence}
            at_utc = moment()
            self._epoch(session, epoch.artifact_id, at_utc)
            assert_before_kickoff(epoch, run, at_utc)
            require(not _rows(session, "pv_locks", run_id=run.artifact_id), "LOCKED_REVIEW_IMMUTABLE")
            return V4CorrectionAuditV1.freeze(**correction_values(run, review, request.reasons, snapshots, at_utc, self.clock.basis)), ()
        return self._execute("REVIEW_IMPORT", request, build)

    def lock(self, request):
        def build(session, moment):
            run = self._load(session, request.run_id)
            epoch = self._epoch(session, run.epoch.artifact_id)
            audit = self._load(session, request.correction_audit_id)
            optimizer = self._rd._load(session, request.optimizer_run_id)
            require(optimizer.schema_version == "RETURN_OPTIMIZATION_RUN_V1", "SEALED_OPTIMIZER_RUN_REQUIRED")
            plan = self._mm._load(session, optimizer.binding.plan.artifact_id)
            at_utc = moment()
            self._epoch(session, epoch.artifact_id, at_utc)
            self._kickoff_guard(session, run, at_utc)
            require(not _rows(session, "pv_locks", run_id=run.artifact_id), "RUN_ALREADY_LOCKED")
            lock = DecisionLockV1.freeze(**lock_values(epoch, run, plan, optimizer, audit, at_utc, self.clock.basis))
            invalidations = ()
            if run.supersedes:
                old = self._load(session, run.supersedes.artifact_id)
                prior_rows = _rows(session, "pv_locks", run_id=old.artifact_id)
                require(len(prior_rows) == 1 and not _rows(session, "pv_invalidations", old_run_id=old.artifact_id)
                        and bool(request.invalidation_reason), "EXPLICIT_PRE_KICKOFF_INVALIDATION_REQUIRED")
                old_lock = self._load(session, prior_rows[0]["artifact_id"])
                assert_before_kickoff(epoch, old, at_utc)
                invalidations = (PreKickoffInvalidationV1.freeze(old_run=ArtifactRefV1.of(old), old_lock=ArtifactRefV1.of(old_lock),
                    new_run=ArtifactRefV1.of(run), replacement_lock=ArtifactRefV1.of(lock), invalidated_at_utc=at_utc,
                    deadline_at_utc=min(old_lock.earliest_kickoff_at_utc, lock.earliest_kickoff_at_utc), reason=request.invalidation_reason),)
            return lock, invalidations
        return self._execute("LOCK", request, build)

    def _watermark(self, session):
        return session.scalar(text("SELECT COALESCE(MAX(sequence),0) FROM pv_receipts"))

    def _event_sequence(self, session, artifact):
        return session.scalar(text("SELECT r.sequence FROM pv_receipts r JOIN pv_artifacts a ON a.event_key=r.request_key WHERE a.artifact_id=:id"), {"id": artifact.artifact_id})

    def _visible(self, session, table, where, params, watermark, order="t.artifact_id"):
        return session.execute(text(f"SELECT t.artifact_id FROM {table} t JOIN pv_artifacts a ON a.artifact_id=t.artifact_id JOIN pv_receipts e ON e.request_key=a.event_key WHERE e.sequence<=:watermark AND {where} ORDER BY {order}"),
            dict(params, watermark=self._watermark(session) if watermark is None else watermark)).scalars().all()

    def _observation_heads(self, session, match_ids, source, as_of, watermark=None):
        values = []
        for match_id in sorted(match_ids):
            rows = self._visible(session, "pv_observations", "t.match_id=:match AND t.source_identity=:source AND t.ingested_us<=:at",
                {"match": match_id, "source": source, "at": micros(as_of)}, watermark, "t.ingested_us DESC,e.sequence DESC")
            if rows:
                values.append(self._load(session, rows[0]))
        return tuple(values)

    def import_result(self, request, verified_hash):
        require(verified_hash == request.result.source_hash, "MANUAL_FILE_HASH_NOT_VERIFIED")
        def build(session, moment):
            claim = request.result
            previous = self._load(session, claim.supersedes_observation_id) if claim.supersedes_observation_id else None
            at_utc = moment()
            self._manual_payload(session, claim, at_utc)
            match = session.get(MatchRecord, claim.match_id)
            require(match.kickoff_at_utc <= claim.observed_at_utc <= claim.verified_at_utc <= at_utc
                    and (claim.published_at_utc is None or claim.published_at_utc >= match.kickoff_at_utc), "RESULT_BEFORE_KICKOFF_OR_VERIFICATION")
            heads = self._observation_heads(session, (claim.match_id,), claim.source_identity, at_utc)
            require((not heads and previous is None) or (len(heads) == 1 and previous == heads[0]), "RESULT_REVISION_MUST_EXTEND_CURRENT_HEAD")
            if previous:
                require(previous.claim.source_identity == claim.source_identity and previous.claim.match_id == claim.match_id
                        and previous.claim.data_classification == claim.data_classification and previous.ingested_at_utc < at_utc,
                        "RESULT_REVISION_IDENTITY_OR_TIME_MISMATCH")
                if claim.previous_source_version_id is not None:
                    require(claim.previous_source_version_id == previous.claim.source_version_id, "PROVIDER_REVISION_CHAIN_MISMATCH")
            else:
                require(claim.previous_source_version_id is None, "MISSING_PROVIDER_PREDECESSOR")
            result = None
            if claim.status == "FT" and claim.regular_time_semantics == "REGULATION_ONLY":
                code = "MANUAL_PROSPECTIVE_"+hashlib.sha256(claim.source_identity.encode()).hexdigest()[:24]
                pid = stable_id("provider", code)
                if session.get(ProviderRecord, pid) is None:
                    session.add(ProviderRecord(provider_id=pid, code=code, name="Manual verified prospective results", provider_kind="MANUAL"))
                    session.flush()
                mapping_id = stable_id("prospective-manual-map", pid, claim.match_id)
                if session.get(ProviderMatchMappingRecord, mapping_id) is None:
                    session.add(ProviderMatchMappingRecord(mapping_id=mapping_id, provider_id=pid, external_namespace="MANUAL_VERIFIED_IMPORT_V1",
                        external_match_id=claim.match_id, internal_match_id=claim.match_id, resolution_method="MANUAL_VERIFIED_IMPORT_V1",
                        confidence=Decimal(1), available_at_utc=at_utc))
                    session.flush()
                earlier = previous
                while earlier and earlier.normalized_result is None:
                    earlier = self._load(session, earlier.previous.artifact_id) if earlier.previous else None
                key = stable_id("prospective-result", request.request_key, claim.source_hash, stamp(at_utc))
                result = MatchResult(match_result_id=key, match_id=claim.match_id, provider_code=code,
                    home_goals=claim.home_goals, away_goals=claim.away_goals, observed_at_utc=at_utc,
                    available_at_utc=at_utc, ingested_at_utc=at_utc, source_result_key=key,
                    payload_hash=match_result_payload_sha256(claim.home_goals, claim.away_goals),
                    supersedes_match_result_id=earlier.normalized_result.match_result_id if earlier else None)
                result = _append_match_result(session, result, mapping_id)
            observation = ResultObservationV1.freeze(claim=claim, ingested_at_utc=at_utc, clock_basis=self.clock.basis,
                source_payload_hash=hashlib.sha256(canonical_json(claim).encode()).hexdigest(), normalized_result=result,
                previous=ArtifactRefV1.of(previous) if previous else None,
                revision_capability="SOURCE_DECLARED_VERSION_CHAIN" if claim.source_version_id and (previous is None or claim.previous_source_version_id) else "SOURCE_REVISION_CHAIN_UNAVAILABLE")
            return observation, ()
        return self._execute("RESULT_IMPORT", request, build)

    def _latest_settlement(self, session, run_id, as_of, watermark=None):
        ids = self._visible(session, "pv_settlements", "t.run_id=:run AND t.settled_us<=:at",
            {"run": run_id, "at": micros(as_of)}, watermark, "t.settled_us DESC,e.sequence DESC")
        return self._load(session, ids[0]) if ids else None

    def settle(self, request):
        def build(session, moment):
            run = self._load(session, request.run_id)
            epoch = self._epoch(session, run.epoch.artifact_id)
            rows = _rows(session, "pv_locks", run_id=run.artifact_id)
            require(len(rows) == 1 and not _rows(session, "pv_invalidations", old_run_id=run.artifact_id), "RUN_NOT_ACTIVE_LOCKED")
            lock = self._load(session, rows[0]["artifact_id"])
            evaluation = self._old(session, lock.return_evaluation, "RETURN_EVALUATION_V1")
            at_utc = moment()
            observations = self._observation_heads(session, (i.match_id for i in run.identities), epoch.result_source_identity, at_utc)
            previous = self._latest_settlement(session, run.artifact_id, at_utc)
            return ProspectiveSettlementV1.freeze(**settlement_values(run, lock, evaluation, observations, at_utc, previous)), ()
        return self._execute("SETTLE", request, build)

    def _census(self, session, epoch, as_of, watermark=None):
        ids = self._visible(session, "pv_runs", "t.epoch_id=:epoch AND a.recorded_at_us<=:at",
            {"epoch": epoch.artifact_id, "at": micros(as_of)}, watermark, "t.slate_date,t.slate_key,t.artifact_id")
        require(len(ids) <= epoch.policy.max_epoch_runs, "PROSPECTIVE_REPORT_SPACE_TOO_LARGE")
        census, settled, locked = [], [], []
        for identity in ids:
            run = self._load(session, identity)
            lock_rows = self._visible(session, "pv_locks", "t.run_id=:run AND t.locked_us<=:at", {"run": identity, "at": micros(as_of)}, watermark)
            invalid_rows = self._visible(session, "pv_invalidations", "t.old_run_id=:run AND t.invalidated_us<=:at", {"run": identity, "at": micros(as_of)}, watermark)
            lock = self._load(session, lock_rows[0]) if lock_rows else None
            invalid = self._load(session, invalid_rows[0]) if invalid_rows else None
            settlement = self._latest_settlement(session, identity, as_of, watermark) if lock else None
            status, reason = run.status, run.reason
            if invalid:
                status, reason = "INVALIDATED_PRE_KICKOFF", "SUPERSEDED_BEFORE_KICKOFF"
            elif lock:
                status, reason = "LOCKED", None
                evaluation = self._old(session, lock.return_evaluation, "RETURN_EVALUATION_V1")
                plan = self._old(session, lock.strategy_plan, "STRATEGY_PASS_PLAN_V2")
                locked.append((run, lock, evaluation, plan))
                if settlement:
                    heads = self._observation_heads(session, (i.match_id for i in run.identities), epoch.result_source_identity, as_of, watermark)
                    refs = tuple(ArtifactRefV1.of(o) for o in heads)
                    if refs != settlement.observations:
                        status, reason = "STALE_SETTLEMENT", "RESULT_REVISION_REQUIRES_SETTLEMENT_REPLAY"
                    elif settlement.reason == "SETTLED":
                        status = "SETTLED"
                        settled.append((run, lock, settlement, evaluation, {o.claim.match_id: o for o in heads}))
                    else:
                        reason = settlement.reason
            elif run.status == "PREPARING" and as_of >= min(i.kickoff_at_utc for i in run.identities):
                status, reason = "UNAVAILABLE", "LOOKAHEAD_RISK_NO_PREMATCH_LOCK"
            census.append(ValidationCensusRowV1(run=ArtifactRefV1.of(run), decision_lock=ArtifactRefV1.of(lock) if lock else None,
                invalidation=ArtifactRefV1.of(invalid) if invalid else None, settlement=ArtifactRefV1.of(settlement) if settlement else None,
                status=status, reason=reason))
        return tuple(census), settled, locked

    def report(self, request):
        def build(session, moment):
            epoch = self._epoch(session, request.epoch_id)
            watermark = self._watermark(session)
            census, settled, locked = self._census(session, epoch, request.as_of_at_utc, watermark)
            at_utc = moment()
            require(epoch.starts_at_utc <= request.as_of_at_utc <= at_utc, "REPORT_AS_OF_OUTSIDE_OBSERVED_TIME")
            return ProspectiveValidationReportV1.freeze(**report_values(epoch, census, settled, locked, request.as_of_at_utc, at_utc, self.clock.basis, watermark)), ()
        return self._execute("REPORT", request, build)

    def close_epoch(self, request):
        def build(session, moment):
            epoch = self._epoch(session, request.epoch_id)
            at_utc = moment()
            require(at_utc >= epoch.ends_at_utc, "EPOCH_CANNOT_CLOSE_BEFORE_END")
            watermark = self._watermark(session)
            census, _, _ = self._census(session, epoch, at_utc, watermark)
            return ValidationEpochCloseV1.freeze(epoch=ArtifactRefV1.of(epoch), closed_at_utc=at_utc,
                receipt_watermark=watermark,
                implementation_hash=epoch.policy.implementation_hash, configuration_hash=epoch.configuration.configuration_hash,
                census_hash=content_hash("PROSPECTIVE_CENSUS_V1", census)), ()
        return self._execute("EPOCH_CLOSE", request, build)

    def _replay(self, session, value):
        kind = value.schema_version
        if kind == "PROSPECTIVE_POLICY_V1":
            require(value == self.policy, "PROSPECTIVE_IMPLEMENTATION_OR_POLICY_CHANGED")
        elif kind == "VALIDATION_EPOCH_V1":
            require(value.policy == self.policy, "EPOCH_IMPLEMENTATION_OR_POLICY_MISMATCH")
            seed = self._old(session, value.seed_plan, "STRATEGY_PASS_PLAN_V2")
            p = self._old(session, value.configuration.return_policy, "RETURN_DISTRIBUTION_POLICY_V1")
            o = self._old(session, value.configuration.objective_profile, "RETURN_OBJECTIVE_PROFILE_V1")
            require(value.configuration == configuration_for(seed, p, o), "EPOCH_CONFIGURATION_REPLAY_MISMATCH")
        elif kind == "EVIDENCE_SNAPSHOT_V1":
            require(value.source_payload_hash == hashlib.sha256(canonical_json(value.claim).encode()).hexdigest(), "MANUAL_STRUCTURED_PAYLOAD_HASH_MISMATCH")
            self._manual_payload(session, value.claim, value.ingested_at_utc)
        elif kind == "PROSPECTIVE_EVIDENCE_BINDING_V1":
            snapshot = self._load(session, value.snapshot.artifact_id)
            football = self._old(session, value.football_evidence, "FOOTBALL_EVIDENCE_V1")
            require(value.snapshot == ArtifactRefV1.of(snapshot) and value.match_id == snapshot.claim.match_id and football == football_projection(snapshot), "EVIDENCE_BRIDGE_REPLAY_MISMATCH")
        elif kind == "PROSPECTIVE_RUN_V1":
            epoch = self._epoch(session, value.epoch.artifact_id)
            analysis = self._old(session, value.analysis, "MULTI_MARKET_ANALYSIS_V1")
            uses = self._uses(session, tuple(u.binding.artifact_id for u in value.evidence), value.decision_cutoff, epoch)
            previous = self._load(session, value.supersedes.artifact_id) if value.supersedes else None
            expected = ProspectiveRunV1.freeze(**prepare_values(epoch, analysis, uses, value.prepared_at_utc, value.clock_basis,
                value.slate_key, value.slate_date, value.budget_fen, previous))
            require(expected == value, "PREPARATION_REPLAY_MISMATCH")
            if value.packet:
                require(self._old(session, value.packet, "ANALYSIS_PACKET_V4") == export_packet_v4(analysis), "PACKET_PROJECTION_MISMATCH")
        elif kind == "V4_CORRECTION_AUDIT_V1":
            run = self._load(session, value.run.artifact_id)
            review = self._old(session, value.review, "IMPORTED_LLM_REVIEW_V4")
            snapshots = {u.snapshot.artifact_id: self._load(session, u.snapshot.artifact_id) for u in run.evidence}
            expected = V4CorrectionAuditV1.freeze(**correction_values(run, review, value.reasons, snapshots, value.imported_at_utc, value.clock_basis))
            require(expected == value, "CORRECTION_AUDIT_REPLAY_MISMATCH")
        elif kind == "DECISION_LOCK_V1":
            epoch = self._epoch(session, value.epoch.artifact_id)
            run = self._load(session, value.run.artifact_id)
            plan = self._old(session, value.strategy_plan, "STRATEGY_PASS_PLAN_V2")
            optimizer = self._old(session, value.optimizer, "RETURN_OPTIMIZATION_RUN_V1")
            audit = self._load(session, value.correction_audit.artifact_id)
            require(DecisionLockV1.freeze(**lock_values(epoch, run, plan, optimizer, audit, value.locked_at_utc, value.clock_basis)) == value, "DECISION_LOCK_REPLAY_MISMATCH")
            if run.supersedes:
                invalidations = _rows(session, "pv_invalidations", new_lock_id=value.artifact_id)
                require(len(invalidations) == 1 and invalidations[0]["old_run_id"] == run.supersedes.artifact_id
                        and invalidations[0]["invalidated_us"] == micros(value.locked_at_utc), "MISSING_ATOMIC_PRE_KICKOFF_INVALIDATION")
            self._kickoff_guard(session, run, value.locked_at_utc, self._event_sequence(session, value)-1)
        elif kind == "PRE_KICKOFF_INVALIDATION_V1":
            old = self._load(session, value.old_run.artifact_id)
            old_lock = self._load(session, value.old_lock.artifact_id)
            new = self._load(session, value.new_run.artifact_id)
            # The replacement lock references the new prepare but not invalidation,
            # so this check has no reference cycle.
            replacement = self._load(session, value.replacement_lock.artifact_id)
            require(new.supersedes == value.old_run and replacement.run == value.new_run and old_lock.run == value.old_run
                    and old.epoch == new.epoch and value.deadline_at_utc == min(old_lock.earliest_kickoff_at_utc, replacement.earliest_kickoff_at_utc), "INVALIDATION_REPLAY_MISMATCH")
        elif kind == "RESULT_OBSERVATION_V1":
            require(value.source_payload_hash == hashlib.sha256(canonical_json(value.claim).encode()).hexdigest(), "RESULT_SOURCE_HASH_MISMATCH")
            self._manual_payload(session, value.claim, value.ingested_at_utc)
            match = session.get(MatchRecord, value.claim.match_id)
            require(match.kickoff_at_utc <= value.claim.observed_at_utc <= value.claim.verified_at_utc <= value.ingested_at_utc
                    and (value.claim.published_at_utc is None or value.claim.published_at_utc >= match.kickoff_at_utc), "RESULT_BEFORE_KICKOFF_OR_VERIFICATION")
            require((value.normalized_result is not None) == (value.claim.status == "FT" and value.claim.regular_time_semantics == "REGULATION_ONLY"), "RESULT_STATUS_PROJECTION_MISMATCH")
            previous = self._load(session, value.previous.artifact_id) if value.previous else None
            heads = self._observation_heads(session, (value.claim.match_id,), value.claim.source_identity, value.ingested_at_utc, self._event_sequence(session, value)-1)
            require(heads == ((previous,) if previous else ()) and value.claim.supersedes_observation_id == (previous.artifact_id if previous else None), "RESULT_REVISION_OMITS_PREDECESSOR")
            require(value.revision_capability == ("SOURCE_DECLARED_VERSION_CHAIN" if value.claim.source_version_id and (previous is None or value.claim.previous_source_version_id) else "SOURCE_REVISION_CHAIN_UNAVAILABLE"), "RESULT_REVISION_CAPABILITY_MISMATCH")
            if value.normalized_result:
                record = session.get(MatchResultRecord, value.normalized_result.match_result_id)
                require(record is not None and _match_result(session, record) == value.normalized_result, "NORMALIZED_RESULT_REPLAY_MISMATCH")
            if previous:
                require(value.previous == ArtifactRefV1.of(previous) and previous.claim.data_classification == value.claim.data_classification
                        and previous.ingested_at_utc < value.ingested_at_utc
                        and value.claim.previous_source_version_id in (None, previous.claim.source_version_id), "RESULT_REVISION_CHAIN_MISMATCH")
            else:
                require(value.claim.previous_source_version_id is None, "MISSING_PROVIDER_PREDECESSOR")
        elif kind == "PROSPECTIVE_SETTLEMENT_V1":
            run = self._load(session, value.run.artifact_id)
            lock = self._load(session, value.decision_lock.artifact_id)
            evaluation = self._old(session, lock.return_evaluation, "RETURN_EVALUATION_V1")
            observations = tuple(self._load(session, r.artifact_id) for r in value.observations)
            previous = self._load(session, value.previous.artifact_id) if value.previous else None
            epoch = self._epoch(session, run.epoch.artifact_id)
            watermark = self._event_sequence(session, value)-1
            require(observations == self._observation_heads(session, (i.match_id for i in run.identities), epoch.result_source_identity, value.settled_at_utc, watermark)
                    and previous == self._latest_settlement(session, run.artifact_id, value.settled_at_utc, watermark), "SETTLEMENT_OMITS_VISIBLE_RESULT_OR_PREDECESSOR")
            require(value == ProspectiveSettlementV1.freeze(**settlement_values(run, lock, evaluation, observations, value.settled_at_utc, previous)), "SETTLEMENT_REPLAY_MISMATCH")
        elif kind == "PROSPECTIVE_VALIDATION_REPORT_V1":
            epoch = self._epoch(session, value.epoch.artifact_id)
            require(value.receipt_watermark == self._event_sequence(session, value)-1, "REPORT_WATERMARK_MISMATCH")
            census, settled, locked = self._census(session, epoch, value.as_of_at_utc, value.receipt_watermark)
            require(value == ProspectiveValidationReportV1.freeze(**report_values(epoch, census, settled, locked, value.as_of_at_utc, value.created_at_utc, value.clock_basis, value.receipt_watermark)), "VALIDATION_CENSUS_OR_METRIC_REPLAY_MISMATCH")
        elif kind == "VALIDATION_EPOCH_CLOSE_V1":
            epoch = self._epoch(session, value.epoch.artifact_id)
            require(value.receipt_watermark == self._event_sequence(session, value)-1, "CLOSE_WATERMARK_MISMATCH")
            census, _, _ = self._census(session, epoch, value.closed_at_utc, value.receipt_watermark)
            require(value.closed_at_utc >= epoch.ends_at_utc and value.configuration_hash == epoch.configuration.configuration_hash
                    and value.implementation_hash == epoch.policy.implementation_hash and value.census_hash == content_hash("PROSPECTIVE_CENSUS_V1", census), "EPOCH_CLOSE_REPLAY_MISMATCH")
        else:
            raise ValueError("UNKNOWN_PROSPECTIVE_SCHEMA")

    def show(self, run_id):
        with self._sessions.begin() as session:
            run = self._load(session, run_id)
            require(type(run) is ProspectiveRunV1, "PROSPECTIVE_RUN_REQUIRED")
            epoch = self._epoch(session, run.epoch.artifact_id)
            census, _, _ = self._census(session, epoch, normalize_utc(self.clock.now()))
            return dict(artifact=run, current=next(row for row in census if row.run.artifact_id == run_id))

    def audit(self, identity):
        with self._sessions.begin() as session:
            value = self._load(session, identity)
            checked = sorted(session.info.get("pv_verified", {}))
            return dict(status="AUDIT_PASS", artifact=ArtifactRefV1.of(value), verified_prospective_artifact_ids=checked,
                        clock_semantics="TRUSTED_LOCAL_RECEIPT_NOT_EXTERNAL_NOTARIZATION", provider_http_sends=0,
                        historical_source_time="UNPROVEN_UNLESS_SEPARATELY_PROVEN", real_performance_claim=False)
