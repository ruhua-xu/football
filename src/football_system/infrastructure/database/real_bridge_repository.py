"""Independent real ledger: trusted-clock transactions, closed graph replay and RA21 commit checks."""

from datetime import datetime, timedelta
import hashlib
import json

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.market_analysis import ArtifactRefV1, MarketModelLineageV1
from football_system.domain.market_v2 import MarketArtifact, MarketProbabilityDistributionV1, content_hash, fixed_decimal
from football_system.domain.real_bridge import (
    THREE_WAY, REAL_ARTIFACT_TYPES, RealObservationProgramV1, RealBridgeImplementationIdentityV1,
    RealPolicyValuePinV1, RealConfigurationV1, RealSourceAdmissionFactV1, RealModelPinV1,
    RealEpochConfigurationAnchorV1, RealValidationEpochV1, RealSlateDeclarationV1, RealKickoffBucketV1,
    RealMarketSourceBindingV1, RealSpSourceBindingV1, RealModelAvailabilityFactV1, RealModelPinInvalidationV1, RealSourceInvalidationV1,
    RealAnalysisUnitV1, RealMultiMarketAnalysisV1, RealProspectiveRunV1, PreLockReplacementV1,
    RealPreKickoffInvalidationV1, RealProspectiveSettlementV1, RealResultObservationV1, RealCensusRowV1,
    RealValidationReportV1, RealEpochCloseV1, require,
)
from football_system.domain.services.real_bridge import context, real_packet, calculate, decision_values
from football_system.domain.services.elo_baseline import EloPredictionRequest, EloThreeWayBaseline, EloBaselineConfig
from football_system.domain.services.prospective import evidence_use, settlement_values
from football_system.domain.services.prospective_validation import report_values
from football_system.domain.settlement import MatchResult
from football_system.infrastructure.database.historical_repositories import _append_match_result, _match_result
from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository
from football_system.infrastructure.database.models import Base, MatchResultRecord, ProviderRecord, ProviderMatchMappingRecord
from football_system.infrastructure.database.real_bridge_projections import CHILDREN, child_rows
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository, micros, stamp
from football_system.infrastructure.database.real_bridge_schema import ARTIFACTS, KIND_TABLE, EXTERNAL_COLUMNS, reference_index, assert_real_schema
from football_system.infrastructure.database.real_bridge_sources import match_identity, fixture_at, fixture_ref, market_values, sp_values, model_metadata
from football_system.infrastructure.files.prospective import SystemProspectiveClock, default_prospective_policy
from football_system.infrastructure.files.real_bridge import BridgeEvidence, identities
from football_system.infrastructure.files.return_distribution import default_return_configuration, strict_return_json

MAX_BYTES = 64*1024*1024
GLOBAL_KINDS = {"PROSPECTIVE_POLICY_V1", "RETURN_DISTRIBUTION_POLICY_V1", "RETURN_OBJECTIVE_PROFILE_V1"}


def nodes(value):
    if isinstance(value, BaseModel):
        for key in type(value).model_fields:
            yield from nodes(getattr(value, key))
        if isinstance(value, MarketArtifact):
            yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from nodes(item)


def body(value):
    return value.model_dump(exclude={"schema_version","artifact_id","content_hash","data_classification","provenance","event_at_utc","clock_basis"})


class SqlAlchemyRealBridgeRepository:
    def __init__(self, sessions, *, evidence_root, clock=None, model_access=None):
        self.sessions = sessions
        self.clock = clock or SystemProspectiveClock()
        self.evidence = BridgeEvidence(evidence_root, trusted_authorities={}, max_bytes=4*1024*1024)
        self.model_access = model_access
        self.pv = SqlAlchemyProspectiveRepository(sessions)
        self.mm = SqlAlchemyMultiMarketRepository(sessions)

    @staticmethod
    def _context(s,value,at):
        return context(value,at,sequence=s.info["rb_sequence"])

    def _visible(self, session, kind, at=None, *, scope=None):
        sql = "SELECT a.artifact_id FROM rb_artifacts a JOIN rb_receipts r ON r.request_key=a.event_key WHERE a.schema_version=:kind AND r.sequence<=:watermark"
        params = dict(kind=kind, watermark=session.info.get("rb_watermark", 9223372036854775807))
        if at is not None:
            sql += " AND r.event_us<=:at"
            params["at"] = micros(at)
        if scope:
            field,identity=scope
            require(field in {"program","epoch","pin","source","run","old_lock"},"CLOSED_REAL_VISIBILITY_SCOPE_REQUIRED")
            sql+=f" AND json_extract(a.artifact_json,'$.{field}.artifact_id')=:scope_id"
            params["scope_id"]=identity
        sql += " ORDER BY r.sequence,a.artifact_id"
        return [self._load(session, i) for i in session.execute(text(sql), params).scalars().all()]

    def _ref(self, session, ref, kind):
        value = self._load(session, ref.artifact_id)
        require(value.schema_version == kind and ArtifactRefV1.of(value) == ref, "REAL_REFERENCE_TYPE_OR_HASH_MISMATCH")
        return value

    def _get(self, session, identity, kind):
        value = self._load(session, identity)
        require(value.schema_version == kind, "REAL_SOURCE_TYPE_MISMATCH")
        return value

    def _program(self, session, identity):
        return self._get(session, identity, "REAL_OBSERVATION_PROGRAM_V1")

    def _epoch(self, session, identity, at=None):
        epoch = self._get(session, identity, "REAL_VALIDATION_EPOCH_V1")
        if at is not None:
            require(epoch.starts_at_utc <= at < epoch.ends_at_utc and not self._visible(session,"REAL_EPOCH_CLOSE_V1",at,scope=("epoch",identity)), "REAL_EPOCH_NOT_ACTIVE")
        return epoch

    def _admit(self, session, reference, at):
        original = self._ref(session, reference, "REAL_SOURCE_ADMISSION_FACT_V1")
        scope = (original.program, original.source_identity, original.kind)
        current = [a for a in self._visible(session, original.schema_version, at,scope=("program",original.program.artifact_id)) if (a.program,a.source_identity,a.kind) == scope][-1]
        require(current.state == "ADMITTED" and current.effective_at_utc <= at < current.expires_at_utc, "SOURCE_ADMISSION_EXPIRED_OR_REVOKED")
        require(original.effective_at_utc <= at < original.expires_at_utc, "BOUND_SOURCE_ADMISSION_EXPIRED")
        return current

    def _proof(self, request, path, digest, session):
        if not session.info.get("rb_replaying"):
            self.evidence.read(path, digest)

    def _model_valid(self, session, pin, at):
        self._admit(session, pin.admission, at)
        require(not self._visible(session,"REAL_MODEL_PIN_INVALIDATION_V1",at,scope=("pin",pin.artifact_id)), "MODEL_INVALIDATED")
        if pin.provenance == "LIVE_OBSERVATION":
            require(self.model_access is not None, "MODEL_AUTHORITY_CONTEXT_REQUIRED")
            if session.info.get("rb_replaying"):
                watermark=session.info.pop("rb_watermark",None)
                try:
                    self._admit(session,pin.admission,self.clock.now())
                finally:
                    if watermark is not None:
                        session.info["rb_watermark"]=watermark
            # Existing authority reader gates CURRENT data/state retention even
            # for historical replay, before disclosing its retained state.
            state,release_hash,authority_hash = self.model_access.verify(session,pin,at)
            require(state.state_hash==pin.model_lineage.state_hash and release_hash==pin.release_hash
                    and authority_hash==pin.authority_hash,"PINNED_MODEL_REPLAY_MISMATCH")
            return state
        return pin.state

    def _evidence_authorized(self,s,binding_id,program,at):
        metadata=s.execute(text("SELECT json_extract(a.artifact_json,'$.claim.source_identity'),json_extract(a.artifact_json,'$.claim.retention_until_utc') FROM pv_evidence_bindings b JOIN pv_artifacts a ON a.artifact_id=b.snapshot_id WHERE b.artifact_id=:id"),{"id":binding_id}).first()
        require(metadata is not None,"EVIDENCE_BINDING_REQUIRED")
        require(at<datetime.fromisoformat(metadata[1].replace("Z","+00:00")),"EVIDENCE_RETENTION_EXPIRED")
        admissions=[a for a in self._visible(s,"REAL_SOURCE_ADMISSION_FACT_V1",at,scope=("program",program.artifact_id)) if a.kind=="EVIDENCE" and a.source_identity==metadata[0]]
        require(admissions,"EVIDENCE_SOURCE_ADMISSION_REQUIRED")
        self._admit(s,ArtifactRefV1.of(admissions[-1]),at)

    def _kickoff(self, session, bucket, at):
        require(at+timedelta(seconds=60) < bucket.kickoff_at_utc, "LOOKAHEAD_RISK")
        limits = session.info.get("rb_source_limits", {})
        for identity in bucket.identities:
            require(match_identity(session, identity.match_id, at) == identity, "CANONICAL_KICKOFF_OR_IDENTITY_CHANGED")
            require(fixture_at(session, identity.match_id, at).status == "SCHEDULED", "FIXTURE_UNAVAILABLE")
            known = session.scalar(select(MatchResultRecord.match_result_id).where(MatchResultRecord.internal_match_id==identity.match_id,
                MatchResultRecord.ingested_at_utc<=at,text("match_results.rowid<=:rb_limit")).params(rb_limit=limits.get("match_results",9223372036854775807)).limit(1))
            require(known is None, "KNOWN_RESULT_BEFORE_LOCK")

    def _head_free(self, session, run):
        row = session.execute(text("SELECT c.parent_run_id FROM rb_head_consumptions c JOIN rb_artifacts a ON a.artifact_id=c.event_id JOIN rb_receipts r ON r.request_key=a.event_key WHERE c.parent_run_id=:id AND r.sequence<=:seq"),
            dict(id=run.artifact_id,seq=session.info.get("rb_watermark",9223372036854775807))).first()
        require(run.status == "PREPARING" and row is None, "STALE_PREPARING_HEAD_OR_ALREADY_LOCKED")

    def _sources_current(self, session, analysis, anchor, at):
        pin = self._ref(session, anchor.model_pin, "REAL_MODEL_PIN_V1")
        self._model_valid(session, pin, at)
        checked = [ArtifactRefV1.of(self._admit(session,pin.admission,at))]
        for unit in analysis.units:
            for use in unit.evidence_uses:
                self._evidence_authorized(session,use.binding.artifact_id,analysis.program,at)
            for source in (unit.consensus,unit.sporttery):
                self._source_not_invalidated(session,source,at)
                checked.append(ArtifactRefV1.of(self._admit(session,source.admission,at)))
                expected = market_values(session,self.sessions,source.ingestion_id,source.snapshot_id) if source.schema_version == "REAL_MARKET_SOURCE_BINDING_V1" else sp_values(session,self.sessions,source.ingestion_id,source.snapshot_id)
                require(all(getattr(source,k) == v for k,v in expected.items()), "LOCK_SOURCE_BINDING_INVALIDATED")
            self._fresh_sources(unit.consensus,unit.sporttery,anchor,at)
            require(unit.identity.match_id in pin.scope_match_ids and unit.model.status == "AVAILABLE", "MODEL_UNAVAILABLE_AT_LOCK")
        return content_hash("REAL_LOCK_REVALIDATION_V1", dict(pin=ArtifactRefV1.of(pin),admissions=checked, analysis=ArtifactRefV1.of(analysis),at=at))

    def _source_not_invalidated(self,s,source,at):
        require(not self._visible(s,"REAL_SOURCE_INVALIDATION_V1",at,scope=("source",source.artifact_id)),"SOURCE_BINDING_INVALIDATED")

    @staticmethod
    def _fresh_sources(market, sp, anchor, at):
        policy = anchor.policy.configuration
        require(all(max(v.available_at_utc,v.ingested_at_utc)<=at and
                    at-v.captured_at_utc<=timedelta(seconds=policy.maximum_odds_age_seconds) for v in (market,sp)), "SOURCE_STALE_OR_FROM_FUTURE")
        require(len(market.constituent_refs)>=policy.minimum_bookmaker_count, "INSUFFICIENT_BOOKMAKERS")

    def execute(self, operation, request):
        cls = REQUEST_TYPES.get(operation)
        require(cls is not None and type(request) is cls, "CLOSED_REAL_COMMAND_REQUIRED")
        request = cls.model_validate(request.model_dump())
        raw = canonical_json(request)
        require(len(raw.encode()) <= 4*1024*1024, "REAL_REQUEST_TOO_LARGE")
        digest = content_hash("REAL_REQUEST_"+operation, request)
        error = None
        response = None
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            assert_real_schema(session.connection())
            old = session.execute(text("SELECT * FROM rb_receipts WHERE request_key=:key"),{"key":request.request_key}).mappings().first()
            if old:
                require((old["operation"],old["request_hash"]) == (operation,digest)
                        and (old["outcome"]=="REJECTED" or old["request_json"]==raw), "REQUEST_KEY_CONTENT_CONFLICT")
                if old["outcome"] == "REJECTED":
                    error = ValueError(old["reason"])
                else:
                    return self._load(session,old["response_id"])
            else:
                at = self.clock.now()
                latest = session.execute(text("SELECT MAX(event_us),COALESCE(MAX(sequence),0) FROM rb_receipts")).one()
                require(latest[0] is None or micros(at) >= latest[0], "TRUSTED_CLOCK_REGRESSION")
                limits = {name: session.scalar(text(f"SELECT COALESCE(MAX(rowid),0) FROM {name}")) for name in ("match_results","fixture_ingestion_captures")}
                session.info["rb_source_limits"] = limits
                sequence = latest[1]+1
                session.info["rb_sequence"] = sequence
                session.info["rb_watermark"] = sequence-1
                epoch_id = self._request_epoch(session,request)
                receipt = dict(request_key=request.request_key,operation=operation,request_hash=digest,request_json=raw,sequence=sequence,
                    event_at=stamp(at),event_us=micros(at),clock_basis=self.clock.basis,provenance="LIVE_OBSERVATION" if self.clock.basis=="LOCAL_SYSTEM_UTC" else "SYNTHETIC_SOFTWARE_ACCEPTANCE",
                    response_id=None,epoch_id=epoch_id,outcome="REJECTED",reason=None,source_limits_json=canonical_json(limits),anchor_sealed_at=None)
                try:
                    with session.begin_nested():
                        response, extras, relations = self._build(session,operation,request,at)
                        final = self.clock.now()
                        require(at <= final <= at+timedelta(seconds=30), "REAL_OPERATION_CLOCK_WINDOW_EXCEEDED")
                        if operation == "lock":
                            run = self._ref(session,response.run,"REAL_PROSPECTIVE_RUN_V1")
                            bucket = self._ref(session,run.bucket,"REAL_KICKOFF_BUCKET_V1")
                            anchor = self._ref(session,run.anchor,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
                            self._epoch(session,run.epoch.artifact_id,final)
                            self._kickoff(session,bucket,final)
                            self._model_valid(session,self._ref(session,anchor.model_pin,"REAL_MODEL_PIN_V1"),final)
                            for unit in self._ref(session,run.analysis,"REAL_MULTI_MARKET_ANALYSIS_V1").units:
                                self._admit(session,unit.consensus.admission,final)
                                self._admit(session,unit.sporttery.admission,final)
                                self._fresh_sources(unit.consensus,unit.sporttery,anchor,final)
                        elif operation in {"prepare","replace"}:
                            self._epoch(session,response.epoch.artifact_id,final)
                            self._kickoff(session,self._ref(session,response.bucket,"REAL_KICKOFF_BUCKET_V1"),final)
                        receipt.update(response_id=response.artifact_id,outcome="COMMITTED",provenance=response.provenance,
                            epoch_id=getattr(getattr(response,"epoch",None),"artifact_id",receipt["epoch_id"]),
                            anchor_sealed_at=stamp(response.sealed_at_utc) if operation=="anchor" else None)
                        session.execute(Base.metadata.tables["rb_receipts"].insert(),receipt)
                        self._store(session,(*extras,response),receipt,relations)
                        require(not any(row[0].startswith("rb_") for row in session.execute(text("PRAGMA foreign_key_check"))), "INCOMPLETE_REAL_TRANSACTION")
                        # Publication itself can be slow. A pre-insert check is
                        # insufficient: include all graph/seal writes in the clock window.
                        committed = self.clock.now()
                        require(at<=committed<=at+timedelta(seconds=30),"REAL_OPERATION_CLOCK_WINDOW_EXCEEDED")
                        if operation=="anchor":
                            require(committed<response.planned_start_at_utc,"ANCHOR_NOT_SEALED_BEFORE_START")
                        if operation=="epoch":
                            require(committed<response.starts_at_utc,"EPOCH_NOT_DECLARED_BEFORE_START")
                        if operation in {"lock","prepare","replace"}:
                            run=response if operation!="lock" else self._ref(session,response.run,"REAL_PROSPECTIVE_RUN_V1")
                            self._epoch(session,run.epoch.artifact_id,committed)
                            require(committed+timedelta(seconds=60)<min(i.kickoff_at_utc for i in run.identities),"LOOKAHEAD_RISK")
                            if operation=="lock":
                                anchor=self._ref(session,run.anchor,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
                                self._admit(session,self._ref(session,anchor.model_pin,"REAL_MODEL_PIN_V1").admission,committed)
                                for unit in self._ref(session,run.analysis,"REAL_MULTI_MARKET_ANALYSIS_V1").units:
                                    self._admit(session,unit.consensus.admission,committed)
                                    self._admit(session,unit.sporttery.admission,committed)
                                    for use in unit.evidence_uses:
                                        self._evidence_authorized(session,use.binding.artifact_id,run.program,committed)
                except (ValueError,KeyError,IntegrityError) as exception:
                    # Keep rejected attempts; never persist private exception input values.
                    code = str(exception)
                    code = code if code and len(code)<=100 and all(c.isupper() or c.isdigit() or c=="_" for c in code) else "REAL_INPUT_OR_SOURCE_REJECTED"
                    # Rejected free text/raw review is not retained. Exact retry is
                    # still bound by its full request digest, never by a new key.
                    receipt.update(response_id=None,outcome="REJECTED",reason=code,
                        request_json=canonical_json(dict(schema_version="REAL_REJECTED_REQUEST_CONTEXT_V1",operation=operation,epoch_id=epoch_id)))
                    rejected_at=self.clock.now()
                    require(rejected_at>=at,"TRUSTED_CLOCK_REGRESSION")
                    receipt.update(event_at=stamp(rejected_at),event_us=micros(rejected_at))
                    session.execute(Base.metadata.tables["rb_receipts"].insert(),receipt)
                    error = ValueError(code)
                    error.__cause__ = exception
        if error:
            raise error
        return response

    def _request_epoch(self,s,r):
        identity = getattr(r,"epoch_id",None)
        if identity:
            return identity if s.scalar(text("SELECT 1 FROM rb_epochs WHERE artifact_id=:id"),{"id":identity}) else None
        for key,kind in (("run_id","REAL_PROSPECTIVE_RUN_V1"),("slate_id","REAL_SLATE_DECLARATION_V1")):
            identity = getattr(r,key,None)
            if identity:
                epoch=s.scalar(text("SELECT e.artifact_id FROM rb_artifacts a JOIN rb_epochs e ON e.artifact_id=json_extract(a.artifact_json,'$.epoch.artifact_id') WHERE a.artifact_id=:id AND a.schema_version=:kind AND rb_valid_header_v1(a.artifact_json)=1"),dict(id=identity,kind=kind))
                if epoch:
                    return epoch
        return None

    def _build(self, s, op, r, at):
        """The same closed builder is used for source replay with a historical receipt watermark."""
        empty = ()
        if op == "program":
            require(s.info.get("rb_replaying") or (r.provenance == "SYNTHETIC_SOFTWARE_ACCEPTANCE") == (self.clock.basis == "SYNTHETIC_TEST_CLOCK"), "TEST_PROGRAM_CLOCK_MISMATCH")
            basis = "SYNTHETIC_TEST_CLOCK" if r.provenance == "SYNTHETIC_SOFTWARE_ACCEPTANCE" else "LOCAL_SYSTEM_UTC"
            return RealObservationProgramV1.freeze(provenance=r.provenance,event_at_utc=at,clock_basis=basis,
                receipt_sequence=s.info["rb_sequence"],
                observation_program_id=r.observation_program_id,competition_ids=tuple(sorted(r.competition_ids)),season_id=r.season_id,scope_reference=r.scope_reference),empty,empty
        if op == "admission":
            p = self._program(s,r.program_id)
            require(r.effective_at_utc<=at<r.expires_at_utc,"ADMISSION_TIMELINE_INVALID")
            self._proof(r,r.evidence_file,r.evidence_hash,s)
            old = self._get(s,r.previous_id,"REAL_SOURCE_ADMISSION_FACT_V1") if r.previous_id else None
            previous = [a for a in self._visible(s,"REAL_SOURCE_ADMISSION_FACT_V1",at,scope=("program",p.artifact_id)) if a.kind==r.kind and a.source_identity==r.source_identity]
            require((previous[-1] if previous else None) == old, "ADMISSION_MUST_EXTEND_CURRENT_HEAD")
            require(r.kind != "MARKET" or r.source_identity == "THE_ODDS_API", "CURRENT_H2H_PROVIDER_ONLY")
            require(r.kind != "SPORTTERY" or r.source_identity=="SPORTTERY_MANUAL","MANUAL_SP_SOURCE_REQUIRED")
            return RealSourceAdmissionFactV1.freeze(**self._context(s,p,at),program=ArtifactRefV1.of(p),source_identity=r.source_identity,kind=r.kind,state=r.state,
                effective_at_utc=r.effective_at_utc,expires_at_utc=r.expires_at_utc,rights_reference=r.rights_reference,
                evidence_hash=r.evidence_hash,verified_by=r.verified_by,credential_verified=r.credential_verified,previous=ArtifactRefV1.of(old) if old else None),empty,empty
        if op == "model-pin":
            p = self._program(s,r.program_id)
            ad = self._get(s,r.admission_id,"REAL_SOURCE_ADMISSION_FACT_V1")
            self._admit(s,ArtifactRefV1.of(ad),at)
            require(ad.program.artifact_id==p.artifact_id and ad.kind=="MODEL" and ad.source_identity==r.source_identity, "MODEL_ADMISSION_SCOPE_MISMATCH")
            if p.provenance == "SYNTHETIC_SOFTWARE_ACCEPTANCE":
                require(r.test_state is not None and r.release_id is None and r.model_state_id is None and r.source_analysis_id is None, "TEST_STATE_ONLY_IN_TEST_PROGRAM")
                state, release_hash, authority_hash = r.test_state,content_hash("TEST_MODEL_RELEASE",r.test_state),ad.content_hash
                configuration=EloBaselineConfig()
                lineage=MarketModelLineageV1(model_name=state.model_name,model_version=state.model_version,
                    state_id=stable_id("SYNTHETIC_FIXED_STATE",state.state_hash),state_hash=state.state_hash,config_hash=state.config_hash,
                    training_data_hash=state.training_data_hash,training_cutoff_at_utc=state.cutoff_at_utc,generated_at_utc=at)
                require(state.season_id==p.season_id and not set(r.scope_match_ids)&set(state.training_match_ids), "MODEL_SCOPE_OR_TRAINING_INTERSECTION")
            elif s.info.get("rb_replaying"):
                require(r.test_state is None, "TEST_STATE_NOT_LIVE")
                lineage,configuration,release_hash,authority_hash=model_metadata(s,r.model_state_id,r.source_analysis_id,r.release_id)
                state=None
            else:
                require(r.test_state is None and self.model_access is not None, "MODEL_AUTHORITY_CONTEXT_REQUIRED")
                state,release_hash,authority_hash = self.model_access.verify(s,r,at)
                require(state.season_id==p.season_id and not set(r.scope_match_ids)&set(state.training_match_ids), "MODEL_SCOPE_OR_TRAINING_INTERSECTION")
                lineage,configuration,meta_release,meta_authority=model_metadata(s,r.model_state_id,r.source_analysis_id,r.release_id)
                require((meta_release,meta_authority)==(release_hash,authority_hash),"MODEL_METADATA_MISMATCH")
                state=None
            return RealModelPinV1.freeze(**self._context(s,p,at),program=ArtifactRefV1.of(p),admission=ArtifactRefV1.of(ad),source_identity=r.source_identity,
                release_id=r.release_id,model_state_id=r.model_state_id,source_analysis_id=r.source_analysis_id,state=state,release_hash=release_hash,
                authority_hash=authority_hash,scope_match_ids=tuple(sorted(r.scope_match_ids)),configuration=configuration,model_lineage=lineage),empty,empty
        if op == "policy":
            p = self._program(s,r.program_id)
            return_policy, objective = default_return_configuration()
            _, hashes = identities()
            policy = RealPolicyValuePinV1.freeze(**self._context(s,p,at),program=ArtifactRefV1.of(p),configuration=RealConfigurationV1(
                allowed_budgets_fen=r.allowed_budgets_fen,result_source_identity=r.result_source_identity,
                maximum_odds_age_seconds=r.maximum_odds_age_seconds,minimum_bookmaker_count=r.minimum_bookmaker_count),
                prospective_policy=default_prospective_policy(),return_policy=ArtifactRefV1.of(return_policy),objective=ArtifactRefV1.of(objective),market_policy_hash=hashes["market_consensus"])
            return policy,(return_policy,objective),empty
        if op == "anchor":
            p = self._program(s,r.program_id)
            policy = self._get(s,r.policy_id,"REAL_POLICY_VALUE_PIN_V1")
            pin = self._get(s,r.model_pin_id,"REAL_MODEL_PIN_V1")
            require(pin.program == policy.program == ArtifactRefV1.of(p) and pin.provenance == policy.provenance == p.provenance, "ANCHOR_PIN_PROGRAM_MISMATCH")
            if s.info.get("rb_replaying"):
                self._admit(s,pin.admission,at)
            else:
                self._model_valid(s,pin,at)
            digest, hashes = identities()
            implementation = RealBridgeImplementationIdentityV1.freeze(**self._context(s,p,at),bridge_hash=digest,frozen_hashes=hashes)
            value = RealEpochConfigurationAnchorV1.freeze(**self._context(s,p,at),program=ArtifactRefV1.of(p),implementation=implementation,policy=policy,
                model_pin=ArtifactRefV1.of(pin),planned_start_at_utc=r.starts_at_utc,planned_end_at_utc=r.ends_at_utc,
                sealed_at_utc=s.info["rb_anchor_sealed_at"] if s.info.get("rb_replaying") else self.clock.now(),
                configuration_hash=content_hash("REAL_CONFIGURATION_V1",[policy.content_hash,ArtifactRefV1.of(pin),implementation.content_hash]))
            return value,empty,empty
        if op == "epoch":
            anchor = self._get(s,r.anchor_id,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
            pin=self._ref(s,anchor.model_pin,"REAL_MODEL_PIN_V1")
            if s.info.get("rb_replaying"):
                self._admit(s,pin.admission,at)
            else:
                self._model_valid(s,pin,at)
            old = self._epoch(s,r.previous_epoch_id) if r.previous_epoch_id else None
            if old:
                require(old.program==anchor.program and self._visible(s,"REAL_EPOCH_CLOSE_V1",at,scope=("epoch",old.artifact_id))
                        and old.ends_at_utc<=anchor.planned_start_at_utc,"CLOSED_PREDECESSOR_REQUIRED")
            require(anchor.sealed_at_utc<=at,"ANCHOR_NOT_SEALED")
            return RealValidationEpochV1.freeze(**self._context(s,anchor,at),program=anchor.program,anchor=ArtifactRefV1.of(anchor),starts_at_utc=anchor.planned_start_at_utc,
                ends_at_utc=anchor.planned_end_at_utc,configuration_hash=anchor.configuration_hash,implementation=ArtifactRefV1.of(anchor.implementation),previous=ArtifactRefV1.of(old) if old else None),empty,empty
        if op == "slate":
            epoch = self._epoch(s,r.epoch_id,at)
            p = self._ref(s,epoch.program,"REAL_OBSERVATION_PROGRAM_V1")
            self._proof(r,r.source_file,r.source_hash,s)
            require(len(set(r.match_ids))==len(r.match_ids),"DUPLICATE_SLATE_MATCH")
            matches = tuple(match_identity(s,i,at) for i in sorted(r.match_ids))
            require(all(i.competition_id in p.competition_ids and i.season_id==p.season_id for i in matches),"SLATE_OUTSIDE_PROGRAM")
            return RealSlateDeclarationV1.freeze(**self._context(s,epoch,at),epoch=ArtifactRefV1.of(epoch),program=epoch.program,source_reference=r.source_reference,
                source_hash=r.source_hash,identities=matches,fixture_refs=tuple(fixture_ref(s,i.match_id,at) for i in matches)),empty,empty
        if op == "bucket":
            slate = self._get(s,r.slate_id,"REAL_SLATE_DECLARATION_V1")
            self._epoch(s,slate.epoch.artifact_id,at)
            matches = tuple(i for i in slate.identities if i.kickoff_at_utc==r.kickoff_at_utc)
            require(matches,"EXACT_KICKOFF_NOT_IN_SLATE")
            bucket = RealKickoffBucketV1.freeze(**self._context(s,slate,at),epoch=slate.epoch,program=slate.program,slate=ArtifactRefV1.of(slate),kickoff_at_utc=r.kickoff_at_utc,identities=matches,
                fixture_refs=tuple(f for f in slate.fixture_refs if f.match_id in {i.match_id for i in matches}))
            self._kickoff(s,bucket,at)
            return bucket,empty,empty
        if op in {"market-bind","sp-bind"}:
            p = self._program(s,r.program_id)
            ad = self._get(s,r.admission_id,"REAL_SOURCE_ADMISSION_FACT_V1")
            self._admit(s,ArtifactRefV1.of(ad),at)
            require(ad.program.artifact_id==p.artifact_id and ad.kind==("MARKET" if op=="market-bind" else "SPORTTERY"),"SOURCE_ADMISSION_SCOPE_MISMATCH")
            require(op!="market-bind" or ad.credential_verified,"MARKET_CREDENTIAL_NOT_VERIFIED")
            values = market_values(s,self.sessions,r.ingestion_id,r.snapshot_id) if op=="market-bind" else sp_values(s,self.sessions,r.ingestion_id,r.snapshot_id)
            require(max(values["available_at_utc"],values["ingested_at_utc"])<=at,"SOURCE_FROM_FUTURE")
            cls = RealMarketSourceBindingV1 if op=="market-bind" else RealSpSourceBindingV1
            return cls.freeze(**self._context(s,p,at),program=ArtifactRefV1.of(p),admission=ArtifactRefV1.of(ad),**values),empty,empty
        if op in {"prepare","replace"}:
            return self._prepare(s,op,r,at)
        if op == "model-invalidate":
            pin = self._get(s,r.pin_id,"REAL_MODEL_PIN_V1")
            self._proof(r,r.evidence_file,r.evidence_hash,s)
            return RealModelPinInvalidationV1.freeze(**self._context(s,pin,at),pin=ArtifactRefV1.of(pin),reason=r.reason,evidence_hash=r.evidence_hash),empty,empty
        if op == "source-invalidate":
            source=self._load(s,r.source_id)
            require(type(source) in (RealMarketSourceBindingV1,RealSpSourceBindingV1),"REAL_MARKET_OR_SP_BINDING_REQUIRED")
            self._proof(r,r.evidence_file,r.evidence_hash,s)
            return RealSourceInvalidationV1.freeze(**self._context(s,source,at),source=ArtifactRefV1.of(source),reason=r.reason,evidence_hash=r.evidence_hash),empty,empty
        if op == "lock":
            return self._lock(s,r,at)
        if op == "result-import":
            return self._result(s,r,at),empty,empty
        if op == "settle":
            run = self._get(s,r.run_id,"REAL_PROSPECTIVE_RUN_V1")
            lock = self._lock_for(s,run,at)
            require(lock is not None and not self._invalidated(s,lock,at),"NO_EFFECTIVE_REAL_LOCK")
            epoch = self._epoch(s,run.epoch.artifact_id)
            anchor = self._ref(s,epoch.anchor,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
            observations = self._results(s,run,anchor,at)
            calc = self._ref(s,lock.calculation,"REAL_CALCULATION_BINDING_V1")
            previous = self._last_settlement(s,run,at)
            values = settlement_values(run,lock,calc.optimizer.result,observations,at,previous)
            return RealProspectiveSettlementV1.freeze(**self._context(s,run,at),**values),empty,empty
        if op in {"report","epoch-close"}:
            return self._report_or_close(s,op,r,at),empty,empty
        raise ValueError("REAL_OPERATION_NOT_IMPLEMENTED")

    @fixed_decimal(28)
    def _prepare(self,s,op,r,at):
        epoch = self._epoch(s,r.epoch_id,at)
        anchor = self._ref(s,epoch.anchor,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
        count=s.scalar(text("SELECT count(*) FROM rb_artifacts a JOIN rb_receipts r ON r.request_key=a.event_key WHERE a.schema_version='REAL_PROSPECTIVE_RUN_V1' AND json_extract(a.artifact_json,'$.epoch.artifact_id')=:id AND r.sequence<=:watermark"),dict(id=epoch.artifact_id,watermark=s.info["rb_watermark"]))
        require(count<anchor.policy.prospective_policy.max_epoch_runs,"PROSPECTIVE_REPORT_SPACE_TOO_LARGE")
        bucket = self._get(s,r.bucket_id,"REAL_KICKOFF_BUCKET_V1")
        require(bucket.epoch==ArtifactRefV1.of(epoch) and r.budget_fen in anchor.policy.configuration.allowed_budgets_fen,"BUCKET_EPOCH_OR_BUDGET_MISMATCH")
        self._kickoff(s,bucket,at)
        pin = self._ref(s,anchor.model_pin,"REAL_MODEL_PIN_V1")
        model_reason = None
        state = None
        try:
            state = self._model_valid(s,pin,at)
        except ValueError:
            model_reason = "MODEL_UNAVAILABLE"
        markets = [self._get(s,i,"REAL_MARKET_SOURCE_BINDING_V1") for i in sorted(r.market_binding_ids)]
        prices = [self._get(s,i,"REAL_SP_SOURCE_BINDING_V1") for i in sorted(r.sp_binding_ids)]
        ids = {i.match_id for i in bucket.identities}
        require(len(markets)==len(prices)==len(ids) and {m.match_id for m in markets}=={p.match_id for p in prices}==ids,"WHOLE_BUCKET_SOURCE_COVERAGE_REQUIRED")
        uses, football = [], []
        for identity in sorted(r.evidence_binding_ids):
            self._evidence_authorized(s,identity,epoch.program,at)
            binding = self.pv._load(s,identity)
            require(binding.schema_version=="PROSPECTIVE_EVIDENCE_BINDING_V1", "EVIDENCE_BINDING_REQUIRED")
            snapshot = self.pv._load(s,binding.snapshot.artifact_id)
            expected = "REAL_SOURCE_DATA" if epoch.provenance=="LIVE_OBSERVATION" else "SYNTHETIC"
            require(snapshot.claim.data_classification==expected and binding.match_id in ids,"EVIDENCE_REAL_OR_TEST_SCOPE_MISMATCH")
            uses.append(evidence_use(snapshot,binding,at,anchor.policy.prospective_policy))
            football.append(self.mm._load(s,binding.football_evidence.artifact_id))
        require(len(uses)<=128 and len({u.binding.artifact_id for u in uses})==len(uses),"EVIDENCE_LIMIT_OR_DUPLICATE")
        units=[]
        for identity in bucket.identities:
            market=next(m for m in markets if m.match_id==identity.match_id)
            sp=next(p for p in prices if p.match_id==identity.match_id)
            for item in (market,sp):
                require(item.program==epoch.program and item.provenance==epoch.provenance,"MIXED_REAL_TEST_INPUT")
                self._admit(s,item.admission,at)
                self._source_not_invalidated(s,item,at)
            self._fresh_sources(market,sp,anchor,at)
            reason = model_reason or ("MODEL_TARGET_NOT_PINNED" if identity.match_id not in pin.scope_match_ids else None)
            prediction = None
            if reason is None:
                prediction = EloThreeWayBaseline(config=pin.configuration).predict_from_state(EloPredictionRequest(match_id=identity.match_id,season_id=identity.season_id,
                    home_team_id=identity.home_team_id,away_team_id=identity.away_team_id,kickoff_at_utc=identity.kickoff_at_utc,cutoff_at_utc=state.cutoff_at_utc),state)
                if prediction.probabilities is None:
                    reason=str(prediction.reason.value)
            pq = MarketProbabilityDistributionV1.from_three_way(prediction.probabilities) if prediction is not None and prediction.probabilities is not None else None
            availability = RealModelAvailabilityFactV1.freeze(**self._context(s,epoch,at),pin=ArtifactRefV1.of(pin),match_id=identity.match_id,status="AVAILABLE" if pq else "MODEL_UNAVAILABLE",reason=reason,probabilities=pq,decision_cutoff=at)
            # QUANT_ONLY_V1 identity projection is the unchanged QuantOnlyPolicy rule.
            from football_system.domain.services.fusion import QuantOnlyPolicy
            from football_system.domain.prediction import FusionInputs,FusionConfig,ModelQuantPrediction
            from football_system.domain.market import MarketKey,MarketType
            base=None
            if pq is not None:
                scope=stable_id("real-base-input",bucket.artifact_id,identity.match_id,stamp(at))
                key=MarketKey(market_type=MarketType.THREE_WAY)
                quant=ModelQuantPrediction(prediction_id=prediction.prediction_hash,analysis_run_id=scope,match_id=identity.match_id,market=key,probabilities=prediction.probabilities,
                    quant_model_evaluation_id=availability.artifact_id,method=pin.model_lineage.model_name,method_version=pin.model_lineage.model_version,generated_at_utc=at)
                base=MarketProbabilityDistributionV1.from_three_way(QuantOnlyPolicy().fuse(FusionInputs(analysis_run_id=scope,match_id=identity.match_id,market=key,p_quant=quant),FusionConfig(),at).probabilities)
            lineage=pin.model_lineage
            units.append(RealAnalysisUnitV1.freeze(**self._context(s,epoch,at),identity=identity,fixture=next(f for f in bucket.fixture_refs if f.match_id==identity.match_id),decision_cutoff=at,consensus=market,sporttery=sp,model=availability,
                model_lineage=lineage,p_quant=pq,p_base=base,quant_status=availability.status,unavailable_reason=reason,
                evidence=tuple(sorted((e for e in football if e.match_id==identity.match_id),key=lambda e:e.artifact_id)),
                evidence_uses=tuple(u for u in uses if u.match_id==identity.match_id)))
        ready=all(u.p_base is not None for u in units)
        analysis=RealMultiMarketAnalysisV1.freeze(**self._context(s,epoch,at),program=epoch.program,epoch=ArtifactRefV1.of(epoch),anchor=epoch.anchor,bucket=ArtifactRefV1.of(bucket),
            units=tuple(units),configuration=anchor.policy.configuration,configuration_hash=epoch.configuration_hash,status="READY" if ready else "UNAVAILABLE",reason=None if ready else "BUCKET_MODEL_UNAVAILABLE")
        packet=real_packet(analysis) if ready else None
        previous=self._get(s,r.expected_head_id,"REAL_PROSPECTIVE_RUN_V1") if op=="replace" else None
        old_lock=self._get(s,r.supersedes_lock_id,"DECISION_LOCK_V2") if r.supersedes_lock_id else None
        if previous:
            self._head_free(s,previous)
            require(at>previous.decision_cutoff,"REPLACEMENT_REQUIRES_NEW_TRUSTED_CUTOFF")
            require(ready and previous.bucket==ArtifactRefV1.of(bucket) and previous.epoch==ArtifactRefV1.of(epoch)
                    and previous.budget_fen==r.budget_fen and previous.supersedes_lock==(ArtifactRefV1.of(old_lock) if old_lock else None),"REPLACEMENT_SCOPE_OR_AVAILABILITY_MISMATCH")
        if old_lock:
            require(old_lock.bucket==ArtifactRefV1.of(bucket) and old_lock.epoch==ArtifactRefV1.of(epoch) and not self._invalidated(s,old_lock,at),"POSTLOCK_REVISION_SCOPE_MISMATCH")
        deps=dict(epoch=ArtifactRefV1.of(epoch),anchor=epoch.anchor,bucket=ArtifactRefV1.of(bucket),analysis=ArtifactRefV1.of(analysis),packet=ArtifactRefV1.of(packet) if packet else None,
            evidence=uses,budget=r.budget_fen,previous=ArtifactRefV1.of(previous) if previous else None,supersedes_lock=ArtifactRefV1.of(old_lock) if old_lock else None)
        run=RealProspectiveRunV1.freeze(**self._context(s,epoch,at),program=epoch.program,epoch=deps["epoch"],anchor=epoch.anchor,bucket=deps["bucket"],analysis=deps["analysis"],packet=deps["packet"],
            identities=bucket.identities,evidence=tuple(uses),budget_fen=r.budget_fen,decision_cutoff=at,prepared_at_utc=at,implementation_hash=anchor.implementation.bridge_hash,
            configuration_hash=epoch.configuration_hash,full_dependency_hash=content_hash("REAL_RUN_DEPENDENCIES_V1",deps),status="PREPARING" if ready else "UNAVAILABLE",reason=analysis.reason,
            previous=deps["previous"],supersedes_lock=deps["supersedes_lock"],
            chain_root=(previous.chain_root or ArtifactRefV1.of(previous)) if previous else None,position=previous.position+1 if previous else 0)
        extras=[analysis,*([packet] if packet else [])]
        relations=[]
        if previous:
            event=PreLockReplacementV1.freeze(**self._context(s,epoch,at),old_run=ArtifactRefV1.of(previous),new_run=ArtifactRefV1.of(run),bucket=run.bucket,epoch=run.epoch,reason=r.reason)
            extras.append(event)
            relations.append(("rb_head_consumptions",dict(parent_run_id=previous.artifact_id,action="REPLACE",new_run_id=run.artifact_id,lock_id=None,event_id=event.artifact_id)))
        return run,tuple(extras),tuple(relations)

    def _lock(self,s,r,at):
        run=self._get(s,r.run_id,"REAL_PROSPECTIVE_RUN_V1")
        self._head_free(s,run)
        self._epoch(s,run.epoch.artifact_id,at)
        anchor=self._ref(s,run.anchor,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
        bucket=self._ref(s,run.bucket,"REAL_KICKOFF_BUCKET_V1")
        self._kickoff(s,bucket,at)
        analysis=self._ref(s,run.analysis,"REAL_MULTI_MARKET_ANALYSIS_V1")
        revalidation=self._sources_current(s,analysis,anchor,at)
        snapshots={u.snapshot.artifact_id:self.pv._load(s,u.snapshot.artifact_id) for u in run.evidence}
        policy,objective=default_return_configuration()
        plan,calc=calculate(run,analysis,anchor,r,snapshots,at,policy,objective,sequence=s.info["rb_sequence"])
        lock=decision_values(run,analysis,anchor,plan,calc,at,revalidation)
        extras=[plan,calc]
        relations=[("rb_head_consumptions",dict(parent_run_id=run.artifact_id,action="LOCK",new_run_id=None,lock_id=lock.artifact_id,event_id=lock.artifact_id))]
        old=self._ref(s,run.supersedes_lock,"DECISION_LOCK_V2") if run.supersedes_lock else None
        if old:
            require(r.invalidation_reason and not self._invalidated(s,old,at),"EXPLICIT_CURRENT_LOCK_INVALIDATION_REQUIRED")
            require(old.epoch==run.epoch and old.bucket==run.bucket,"POSTLOCK_CROSSES_EPOCH_OR_BUCKET")
            extras.append(RealPreKickoffInvalidationV1.freeze(**self._context(s,run,at),old_lock=ArtifactRefV1.of(old),new_lock=ArtifactRefV1.of(lock),reason=r.invalidation_reason))
        for identity in run.identities:
            versions=self._slot_versions(s,run.program.artifact_id,identity.match_id)
            if old:
                require(versions and versions[-1]["lock_id"]==old.artifact_id,"PREDICTION_SLOT_NOT_OWNED")
                version=versions[-1]["version"]+1
            else:
                require(not versions,"DUPLICATE_OFFICIAL_PREDICTION")
                version=0
                relations.append(("rb_prediction_slots",dict(program_id=run.program.artifact_id,match_id=identity.match_id,market_hash=THREE_WAY.market_hash,initial_lock_id=lock.artifact_id)))
            relations.append(("rb_prediction_versions",dict(program_id=run.program.artifact_id,match_id=identity.match_id,market_hash=THREE_WAY.market_hash,
                version=version,previous_version=version-1 if version else None,lock_id=lock.artifact_id)))
        return lock,tuple(extras),tuple(relations)

    def _slot_versions(self,s,program_id,match_id):
        return s.execute(text("SELECT v.* FROM rb_prediction_versions v JOIN rb_artifacts a ON a.artifact_id=v.lock_id JOIN rb_receipts r ON r.request_key=a.event_key WHERE v.program_id=:p AND v.match_id=:m AND v.market_hash=:key AND r.sequence<=:seq ORDER BY v.version"),
            dict(p=program_id,m=match_id,key=THREE_WAY.market_hash,seq=s.info.get("rb_watermark",9223372036854775807))).mappings().all()

    def _result(self,s,r,at):
        p=self._program(s,r.program_id)
        ad=self._get(s,r.admission_id,"REAL_SOURCE_ADMISSION_FACT_V1")
        self._admit(s,ArtifactRefV1.of(ad),at)
        claim=r.result
        require(ad.kind=="RESULT" and ad.source_identity==claim.source_identity and ad.program.artifact_id==p.artifact_id,"RESULT_SOURCE_ADMISSION_MISMATCH")
        require(at < claim.retention_until_utc,"RESULT_RETENTION_EXPIRED")
        self._proof(r,claim.source_file,claim.source_hash,s)
        identity=match_identity(s,claim.match_id,at)
        require(identity.competition_id in p.competition_ids and identity.kickoff_at_utc<=claim.observed_at_utc<=claim.verified_at_utc<=at,"RESULT_SCOPE_OR_TIME_INVALID")
        previous=[o for o in self._visible(s,"REAL_RESULT_OBSERVATION_V1",at,scope=("program",p.artifact_id)) if o.claim.match_id==claim.match_id and o.claim.source_identity==claim.source_identity]
        old=previous[-1] if previous else None
        require(claim.supersedes_observation_id==(old.artifact_id if old else None) and (old is None or old.ingested_at_utc<at),"RESULT_REVISION_MUST_EXTEND_HEAD")
        normalized=None
        if claim.status=="FT" and claim.regular_time_semantics=="REGULATION_ONLY":
            code="RB_"+p.provenance+"_"+hashlib.sha256(claim.source_identity.encode()).hexdigest()[:12]
            pid=stable_id("provider",code)
            mapping=stable_id("rb-result-map",pid,claim.match_id)
            earlier=old
            while earlier and earlier.normalized_result is None:
                earlier=self._ref(s,earlier.previous,"REAL_RESULT_OBSERVATION_V1") if earlier.previous else None
            key=stable_id("rb-result",r.request_key,claim.source_hash,stamp(at))
            normalized=MatchResult(match_result_id=key,match_id=claim.match_id,provider_code=code,home_goals=claim.home_goals,away_goals=claim.away_goals,
                observed_at_utc=at,available_at_utc=at,ingested_at_utc=at,source_result_key=key,payload_hash=match_result_payload_sha256(claim.home_goals,claim.away_goals),
                supersedes_match_result_id=earlier.normalized_result.match_result_id if earlier else None)
            if not s.info.get("rb_replaying"):
                if s.get(ProviderRecord,pid) is None:
                    s.add(ProviderRecord(provider_id=pid,code=code,name="Real bridge manual results",provider_kind="MANUAL"))
                    s.flush()
                if s.get(ProviderMatchMappingRecord,mapping) is None:
                    s.add(ProviderMatchMappingRecord(mapping_id=mapping,provider_id=pid,external_namespace="REAL_BRIDGE_MANUAL_RESULT",external_match_id=claim.match_id,
                        internal_match_id=claim.match_id,resolution_method="MANUAL_VERIFIED_IMPORT_V1",confidence=1,available_at_utc=at))
                    s.flush()
                _append_match_result(s,normalized,mapping)
            else:
                record=s.get(MatchResultRecord,key)
                require(record is not None and _match_result(s,record)==normalized,"NORMALIZED_REAL_RESULT_CHANGED")
        return RealResultObservationV1.freeze(**self._context(s,p,at),program=ArtifactRefV1.of(p),admission=ArtifactRefV1.of(ad),claim=claim,normalized_result=normalized,
            previous=ArtifactRefV1.of(old) if old else None,ingested_at_utc=at)

    def _lock_for(self,s,run,at):
        values=self._visible(s,"DECISION_LOCK_V2",at,scope=("run",run.artifact_id))
        return values[-1] if values else None

    def _invalidated(self,s,lock,at):
        return bool(self._visible(s,"REAL_PRE_KICKOFF_INVALIDATION_V1",at,scope=("old_lock",lock.artifact_id)))

    def _last_settlement(self,s,run,at):
        values=self._visible(s,"REAL_PROSPECTIVE_SETTLEMENT_V1",at,scope=("run",run.artifact_id))
        return values[-1] if values else None

    def _results(self,s,run,anchor,at):
        heads={}
        for o in self._visible(s,"REAL_RESULT_OBSERVATION_V1",at,scope=("program",run.program.artifact_id)):
            if o.program==run.program and o.claim.match_id in {i.match_id for i in run.identities} and o.claim.source_identity==anchor.policy.configuration.result_source_identity:
                heads[o.claim.match_id]=o
        return tuple(heads[k] for k in sorted(heads))

    def _report_or_close(self,s,op,r,at):
        epoch=self._epoch(s,r.epoch_id)
        anchor=self._ref(s,epoch.anchor,"REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
        cutoff=r.as_of_at_utc if op=="report" else at
        require(epoch.starts_at_utc<=cutoff<=at,"REPORT_ASOF_INVALID")
        if op=="epoch-close":
            require(at>=epoch.ends_at_utc,"EPOCH_NOT_FINISHED")
        census=[]
        settled=[]
        locked=[]
        replacements=self._visible(s,"PRE_LOCK_REPLACEMENT_V1",cutoff,scope=("epoch",epoch.artifact_id))
        for run in self._visible(s,"REAL_PROSPECTIVE_RUN_V1",cutoff,scope=("epoch",epoch.artifact_id)):
            if run.epoch.artifact_id!=epoch.artifact_id:
                continue
            replacement=next((v for v in replacements if v.old_run.artifact_id==run.artifact_id),None)
            lock=self._lock_for(s,run,cutoff)
            settlement=self._last_settlement(s,run,cutoff)
            status,reason=run.status,run.reason
            if replacement:
                status,reason="REPLACED_PRE_LOCK",None
            elif lock and self._invalidated(s,lock,cutoff):
                status,reason="INVALIDATED_PRE_KICKOFF",None
            elif lock:
                status,reason="LOCKED",None
                calc=self._ref(s,lock.calculation,"REAL_CALCULATION_BINDING_V1")
                plan=self._ref(s,lock.strategy_plan,"REAL_STRATEGY_PLAN_V1")
                locked.append((run,lock,calc.optimizer.result,plan))
                if settlement:
                    results=self._results(s,run,anchor,cutoff)
                    if tuple(ArtifactRefV1.of(o) for o in results)!=settlement.observations:
                        status,reason="STALE_SETTLEMENT","RESULT_REVISION_REQUIRES_SETTLEMENT"
                    elif settlement.reason=="SETTLED":
                        status="SETTLED"
                        settled.append((run,lock,settlement,calc.optimizer.result,{o.claim.match_id:o for o in results}))
                    else:
                        reason=settlement.reason
            elif cutoff>=min(i.kickoff_at_utc for i in run.identities):
                status,reason="UNAVAILABLE", "LOOKAHEAD_RISK_NO_LOCK"
            key=s.scalar(text("SELECT event_key FROM rb_artifacts WHERE artifact_id=:id"),{"id":run.artifact_id})
            census.append(RealCensusRowV1(run=ArtifactRefV1.of(run),request_key=key,status=status,reason=reason,
                lock=ArtifactRefV1.of(lock) if lock else None,settlement=ArtifactRefV1.of(settlement) if settlement else None,replacement=ArtifactRefV1.of(replacement) if replacement else None))
        watermark=s.info.get("rb_watermark",s.scalar(text("SELECT COALESCE(MAX(sequence),0) FROM rb_receipts")))
        rejected=s.execute(text("SELECT request_key,reason FROM rb_receipts WHERE epoch_id=:id AND outcome='REJECTED' AND sequence<=:seq AND event_us<=:at ORDER BY sequence"),
            dict(id=epoch.artifact_id,seq=watermark,at=micros(cutoff))).all()
        census.extend(RealCensusRowV1(run=None,request_key=key,status="REJECTED_REQUEST",reason=reason) for key,reason in rejected)
        census=tuple(census)
        digest=content_hash("REAL_COMPLETE_CENSUS_V1",census)
        if op=="epoch-close":
            return RealEpochCloseV1.freeze(**self._context(s,epoch,at),epoch=ArtifactRefV1.of(epoch),census_hash=digest,receipt_watermark=watermark)
        # Explicit typed read view for the unchanged report kernel. Test provenance
        # never exposes a LIVE mode to the real-eligibility counter.
        from football_system.application.real_bridge_views import RealReportEpochView
        metrics=report_values(RealReportEpochView(epoch,anchor),census,settled,locked,cutoff,at,epoch.clock_basis,watermark)
        for key in ("epoch","census","census_hash","created_at_utc","as_of_at_utc","clock_basis","implementation_hash","receipt_watermark"):
            metrics.pop(key,None)
        real_count=sum(len(lock.frames) for run,lock,*_ in settled if run.provenance=="LIVE_OBSERVATION")
        return RealValidationReportV1.freeze(**self._context(s,epoch,at),program=epoch.program,epoch=ArtifactRefV1.of(epoch),anchor=epoch.anchor,
            as_of_at_utc=cutoff,receipt_watermark=watermark,census=census,census_hash=digest,metrics=metrics,official_prediction_count=real_count,
            performance_evidence_status=metrics["performance_evidence_status"])

    def _store(self,s,values,receipt,relations):
        collected={}
        for value in values:
            for node in nodes(value):
                require(node.schema_version in ARTIFACTS,"RB_UNKNOWN_ARTIFACT_SCHEMA")
                require(node.artifact_id not in collected or collected[node.artifact_id]==node,"CONFLICTING_EMBEDDED_ARTIFACT")
                collected[node.artifact_id]=node
        inserted=[]
        if not s.scalar(text("SELECT 1 FROM rb_market_keys WHERE market_hash=:hash"),{"hash":THREE_WAY.market_hash}):
            s.execute(Base.metadata.tables["rb_market_keys"].insert(),dict(market_hash=THREE_WAY.market_hash,market_json=canonical_json(THREE_WAY)))
        for value in collected.values():
            raw=canonical_json(value)
            require(len(raw.encode())<=MAX_BYTES,"RB_ARTIFACT_TOO_LARGE")
            existing=s.execute(text("SELECT artifact_json FROM rb_artifacts WHERE artifact_id=:id"),{"id":value.artifact_id}).scalar()
            if existing is not None:
                require(existing==raw,"RB_ARTIFACT_ID_COLLISION")
                continue
            s.execute(Base.metadata.tables["rb_artifacts"].insert(),dict(artifact_id=value.artifact_id,schema_version=value.schema_version,content_hash=value.content_hash,artifact_json=raw,event_key=receipt["request_key"]))
            inserted.append(value)
        for value in inserted:
            raw=canonical_json(value)
            table=KIND_TABLE.get(value.schema_version,"rb_math_nodes")
            row=dict(artifact_id=value.artifact_id)
            if table=="rb_math_nodes":
                row["kind"]=value.schema_version
            for key in EXTERNAL_COLUMNS.get(table,{}):
                row[key]=getattr(value,key)
            s.execute(Base.metadata.tables[table].insert(),row)
        for value in inserted:
            raw=canonical_json(value)
            for ref in reference_index(raw):
                ref = self._resolve_reference(s,ref)
                target="rb_external_edges" if ref.pop("external") else "rb_math_edges"
                s.execute(Base.metadata.tables[target].insert(),dict(parent_id=value.artifact_id,parent_schema=value.schema_version,parent_hash=value.content_hash,**ref))
            for item in child_rows(raw):
                s.execute(Base.metadata.tables[item["table"]].insert(),item["row"])
            if value.schema_version=="REAL_PROSPECTIVE_RUN_V1":
                if value.position==0:
                    s.execute(Base.metadata.tables["rb_chains"].insert(),dict(chain_id=value.artifact_id,epoch_id=value.epoch.artifact_id,
                        bucket_id=value.bucket.artifact_id,predecessor_lock_id=value.supersedes_lock.artifact_id if value.supersedes_lock else None))
                s.execute(Base.metadata.tables["rb_chain_runs"].insert(),dict(run_id=value.artifact_id,chain_id=value.chain_root.artifact_id if value.chain_root else value.artifact_id,position=value.position))
        for table,row in relations:
            s.execute(Base.metadata.tables[table].insert(),row)
        for value in inserted:
            s.execute(Base.metadata.tables["rb_seals"].insert(),dict(artifact_id=value.artifact_id))

    @staticmethod
    def _resolve_reference(s,reference):
        reference=dict(reference)
        table="pv_artifacts" if reference["external"] else "rb_artifacts"
        row=s.execute(text(f"SELECT schema_version,content_hash FROM {table} WHERE artifact_id=:id"),{"id":reference["target_id"]}).first()
        require(row is not None and row[0]==reference["target_schema"] and reference["target_hash"] in (None,row[1]),"RB_REFERENCE_SCHEMA_OR_HASH_MISMATCH")
        reference["target_hash"]=row[1]
        return reference

    def _load(self,s,identity):
        cache=s.info.setdefault("rb_verified",{})
        receipt_numbers=s.info.setdefault("rb_verified_receipts",{})
        if identity in cache:
            require(receipt_numbers[identity]<=s.info.get("rb_watermark",9223372036854775807),"REAL_REFERENCE_FROM_FUTURE_RECEIPT")
            return cache[identity]
        active=s.info.setdefault("rb_loading",set())
        require(identity not in active,"CYCLIC_REAL_REFERENCE")
        active.add(identity)
        try:
            row=s.execute(text("SELECT * FROM rb_artifacts WHERE artifact_id=:id"),{"id":identity}).mappings().first()
            require(row is not None,"UNKNOWN_REAL_ARTIFACT")
            cls=ARTIFACTS.get(row["schema_version"])
            require(cls is not None,"UNKNOWN_REAL_SCHEMA")
            value=cls.model_validate(strict_return_json(row["artifact_json"].encode(),limit=MAX_BYTES))
            require(value.content_hash==row["content_hash"] and canonical_json(value)==row["artifact_json"],"CORRUPT_REAL_HEADER")
            for node in nodes(value):
                self._verify_projection(s,node)
            receipt=s.execute(text("SELECT * FROM rb_receipts WHERE request_key=:key"),{"key":row["event_key"]}).mappings().one()
            require(receipt["outcome"]=="COMMITTED" and receipt["sequence"]<=s.info.get("rb_watermark",9223372036854775807),"REAL_REFERENCE_FROM_FUTURE_RECEIPT")
            require(content_hash("REAL_REQUEST_"+receipt["operation"],strict_return_json(receipt["request_json"].encode()))==receipt["request_hash"],"CORRUPT_REAL_REQUEST")
            if isinstance(value,tuple(REAL_ARTIFACT_TYPES.values())):
                require(stamp(value.event_at_utc)==receipt["event_at"] and micros(value.event_at_utc)==receipt["event_us"]
                        and value.clock_basis==receipt["clock_basis"] and value.provenance==receipt["provenance"] and value.receipt_sequence==receipt["sequence"],"FORGED_REAL_EVENT_RECEIPT")
            saved={k:s.info.get(k) for k in ("rb_watermark","rb_replaying","rb_source_limits","rb_sequence","rb_anchor_sealed_at")}
            try:
                s.info.update(rb_watermark=receipt["sequence"]-1,rb_replaying=True,rb_source_limits=json.loads(receipt["source_limits_json"]),rb_sequence=receipt["sequence"])
                s.info["rb_anchor_sealed_at"]=datetime.fromisoformat(receipt["anchor_sealed_at"].replace("Z","+00:00")) if receipt["anchor_sealed_at"] else None
                request=REQUEST_TYPES[receipt["operation"]].model_validate(strict_return_json(receipt["request_json"].encode()))
                require(request.request_key==receipt["request_key"],"REQUEST_KEY_BINDING_MISMATCH")
                root,extra,relations=self._build(s,receipt["operation"],request,datetime.fromisoformat(receipt["event_at"].replace("Z","+00:00")))
                expected={n.artifact_id:canonical_json(n) for v in (*extra,root) for n in nodes(v)}
                require(expected.get(identity)==canonical_json(value),"REAL_SOURCE_OR_MATH_REPLAY_MISMATCH")
                require(root.artifact_id==receipt["response_id"],"REAL_RESPONSE_REPLAY_MISMATCH")
                for table,wanted in relations:
                    cols=Base.metadata.tables[table]
                    where=[cols.c[key]==wanted[key] for key in cols.primary_key.columns.keys()]
                    actual=s.execute(cols.select().where(*where)).mappings().one()
                    require(dict(actual)==wanted,"REAL_OWNERSHIP_REPLAY_MISMATCH")
            finally:
                for key,old in saved.items():
                    if old is None:
                        s.info.pop(key,None)
                    else:
                        s.info[key]=old
            cache[identity]=value
            receipt_numbers[identity]=receipt["sequence"]
            return value
        finally:
            active.remove(identity)

    def _verify_projection(self,s,value):
        row=s.execute(text("SELECT artifact_json,schema_version,content_hash FROM rb_artifacts WHERE artifact_id=:id"),{"id":value.artifact_id}).one()
        require(tuple(row)==(canonical_json(value),value.schema_version,value.content_hash),"CORRUPT_REAL_EMBEDDED_GRAPH")
        require(s.scalar(text("SELECT count(*) FROM rb_seals WHERE artifact_id=:id"),{"id":value.artifact_id})==1,"MISSING_REAL_COMPLETE_SEAL")
        table=KIND_TABLE.get(value.schema_version,"rb_math_nodes")
        actual=dict(s.execute(Base.metadata.tables[table].select().where(Base.metadata.tables[table].c.artifact_id==value.artifact_id)).mappings().one())
        expected=dict(artifact_id=value.artifact_id)
        if table=="rb_math_nodes":
            expected["kind"]=value.schema_version
        expected.update({k:getattr(value,k) for k in EXTERNAL_COLUMNS.get(table,{})})
        require(actual==expected,"CORRUPT_REAL_TYPED_PROJECTION")
        for ext,edge_table in ((False,"rb_math_edges"),(True,"rb_external_edges")):
            refs=[]
            for item in reference_index(canonical_json(value)):
                item=self._resolve_reference(s,item)
                if item.pop("external")==ext:
                    refs.append(dict(parent_id=value.artifact_id,parent_schema=value.schema_version,parent_hash=value.content_hash,**item))
            actual=[dict(r) for r in s.execute(text(f"SELECT * FROM {edge_table} WHERE parent_id=:id"),{"id":value.artifact_id}).mappings()]
            require(sorted(map(canonical_json,refs))==sorted(map(canonical_json,actual)),"CORRUPT_REAL_REFERENCE_GRAPH")
        projected=child_rows(canonical_json(value))
        for name,(kind,_) in CHILDREN.items():
            if value.schema_version==kind:
                actual=[dict(r) for r in s.execute(text(f"SELECT * FROM {name} WHERE parent_id=:id ORDER BY position"),{"id":value.artifact_id}).mappings()]
                require(actual==[item["row"] for item in projected if item["table"]==name],"CORRUPT_REAL_CHILD_PROJECTION")
        if value.schema_version=="REAL_PROSPECTIVE_RUN_V1":
            row=s.execute(text("SELECT * FROM rb_chain_runs WHERE run_id=:id"),{"id":value.artifact_id}).mappings().one()
            require(dict(row)==dict(run_id=value.artifact_id,chain_id=value.chain_root.artifact_id if value.chain_root else value.artifact_id,position=value.position),"CORRUPT_REAL_CHAIN")
            if value.position==0:
                row=s.execute(text("SELECT * FROM rb_chains WHERE chain_id=:id"),{"id":value.artifact_id}).mappings().one()
                require(dict(row)==dict(chain_id=value.artifact_id,epoch_id=value.epoch.artifact_id,bucket_id=value.bucket.artifact_id,
                    predecessor_lock_id=value.supersedes_lock.artifact_id if value.supersedes_lock else None),"CORRUPT_REAL_CHAIN_ROOT")

    def load(self,identity):
        with self.sessions.begin() as s:
            return self._load(s,identity)

    def packet_evidence(self,run):
        with self.sessions.begin() as s:
            run=self._get(s,run.artifact_id,"REAL_PROSPECTIVE_RUN_V1")
            for use in run.evidence:
                self._evidence_authorized(s,use.binding.artifact_id,run.program,self.clock.now())
            return {use.snapshot.artifact_id:self.pv._load(s,use.snapshot.artifact_id) for use in run.evidence}

    def audit(self,identity):
        with self.sessions.begin() as s:
            value=self._load(s,identity)
            assert_real_schema(s.connection())
            return dict(status="AUDIT_PASS",artifact=ArtifactRefV1.of(value),provenance=getattr(value,"provenance","FROZEN_MATH_VALUE"),
                verified_artifact_ids=sorted(s.info["rb_verified"]),real_provider_http=0,llm_api_http=0)
