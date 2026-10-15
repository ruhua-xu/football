"""Persisted Portfolio B: verified snapshots, dedicated budget, atomic claims and P&L."""
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, NoResultFound

from football_system.application.portfolio_requests import REQUEST_TYPES
from football_system.application.portfolio_math import calculate_portfolio, source_frames, settle_portfolio
from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import content_hash
from football_system.domain.portfolio_math import PortfolioNumericalConfigurationV1
from football_system.domain import portfolio as p
from football_system.domain.real_bridge import THREE_WAY, require
from football_system.infrastructure.database.models import Base
from football_system.infrastructure.database.portfolio_schema import HEAD, assert_portfolio_schema, assert_legacy_mirrors
from football_system.infrastructure.database.portfolio_types import decode, refs, embedded_nodes, RB_TYPES
from football_system.infrastructure.database.real_bridge_schema import ARTIFACTS as RB_ARTIFACTS
from football_system.infrastructure.database.prospective_repository import micros, stamp
from football_system.infrastructure.files.return_distribution import default_return_configuration
from football_system.infrastructure.files.training_evidence import LocalTrainingEvidence, strict_json_bytes
from football_system.infrastructure.files.openfootball_evidence import private_evidence_directory


class SqlAlchemyPortfolioRepository:
    def __init__(self, bridge, *, budget_evidence):
        from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
        require(type(bridge) is SqlAlchemyRealBridgeRepository and isinstance(budget_evidence, LocalTrainingEvidence), "PF_TRUSTED_REPOSITORY_CONTEXT_REQUIRED")
        private_evidence_directory(budget_evidence.root)
        self.bridge, self.sessions, self.clock, self.budget_evidence = bridge, bridge.sessions, bridge.clock, budget_evidence

    @staticmethod
    def _table(name): return Base.metadata.tables[name]

    @staticmethod
    def _check(session):
        require(session.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [HEAD], "PF_MIGRATION_HEAD_REQUIRED")
        assert_portfolio_schema(session.connection())
        assert_legacy_mirrors(session.connection())
        proof = session.execute(text("SELECT proof_json,proof_hash FROM pf_backfill_proofs WHERE contract='PORTFOLIO_OWNERSHIP_BACKFILL_V1'")).one_or_none()
        require(proof is not None and content_hash("PF_BACKFILL_PROOF_V1", strict_json_bytes(proof[0].encode())) == proof[1], "PF_COMPLETE_BACKFILL_PROOF_REQUIRED")

    def _load(self, session, identity, kind=None):
        row = session.execute(text("SELECT a.*,s.published_us FROM pf_artifacts a JOIN pf_seals s ON s.artifact_id=a.artifact_id WHERE a.artifact_id=:id"), {"id":identity}).mappings().one()
        value = decode(row["artifact_json"])
        require((value.artifact_id, value.schema_version, value.content_hash) == (row["artifact_id"], row["schema_version"], row["content_hash"])
            and (kind is None or value.schema_version == kind), "PF_ARTIFACT_IDENTITY_MISMATCH")
        expected = refs(value)
        actual = []
        for table, ledger in (("pf_edges", "PF"), ("pf_rb_edges", "RB")):
            actual += [dict(path=r["path"], ledger=ledger, target_id=r["target_id"], target_schema=r["target_schema"], target_hash=r["target_hash"])
                for r in session.execute(text("SELECT * FROM "+table+" WHERE parent_id=:id"), {"id":identity}).mappings()]
        require(tuple(sorted(actual, key=lambda r:r["path"])) == expected, "PF_COMPLETE_EDGE_SET_REQUIRED")
        for ref in expected:
            target_table = "pf_artifacts" if ref["ledger"] == "PF" else "rb_artifacts"
            seal_table = "pf_seals" if ref["ledger"] == "PF" else "rb_seals"
            target = session.execute(text(f"SELECT a.schema_version,a.content_hash FROM {target_table} a JOIN {seal_table} s ON s.artifact_id=a.artifact_id WHERE a.artifact_id=:id"), {"id":ref["target_id"]}).one_or_none()
            require(target is not None and tuple(target) == (ref["target_schema"], ref["target_hash"]), "PF_TYPED_PARENT_HASH_OR_SEAL_CHANGED")
        return value

    def _ref(self, session, reference, kind=None):
        value = self._load(session, reference.artifact_id, kind or reference.schema_version)
        require(ArtifactRefV1.of(value) == reference, "PF_REFERENCE_HASH_MISMATCH")
        return value

    def _rb_static(self, session, reference):
        require(reference.schema_version in RB_TYPES, "PF_RB_REFERENCE_TYPE_REQUIRED")
        row = session.execute(text("SELECT a.schema_version,a.content_hash,a.artifact_json FROM rb_artifacts a JOIN rb_seals s ON s.artifact_id=a.artifact_id WHERE a.artifact_id=:id"), {"id":reference.artifact_id}).one()
        require(tuple(row[:2]) == (reference.schema_version, reference.content_hash), "PF_RB_REFERENCE_HASH_MISMATCH")
        value = RB_ARTIFACTS[reference.schema_version].model_validate(strict_json_bytes(row[2].encode()))
        require(ArtifactRefV1.of(value) == reference and canonical_json(value) == row[2], "PF_RB_REFERENCE_CANONICAL_BYTES_CHANGED")
        return value

    def _current_budget(self, session, identity, at):
        budget = self._load(session, identity, "PORTFOLIO_BUDGET_AUTHORITY_V1")
        content = budget.content_payload
        approval = self._ref(session, content.exact_approval, "PORTFOLIO_BUDGET_APPROVAL_RECORD_V1")
        authority = self._ref(session, content.operator_authority, "PORTFOLIO_AUTHORITY_EVIDENCE_V1")
        subject = approval.subject
        expected = subject.model_dump(mode="python", exclude={"schema_version","funding_limit_fen","capital_evidence","provenance"})
        require(all(getattr(content,k) == value for k,value in expected.items()) and approval.authority == content.operator_authority
            and authority.source_id == subject.source_id and subject.effective_at_utc <= at < subject.expires_at_utc, "PF_BUDGET_SCOPE_OR_VALIDITY_MISMATCH")
        self.budget_evidence.load_authority(authority.authority).assert_active_for(at)
        self.budget_evidence.review(evidence=approval.review, authority=authority.authority, schema=subject.schema_version,
            digest=subject.content_hash, source_ids=(subject.source_id,), operator_id="portfolio-operator", at_utc=at)
        self._capital(subject)
        return budget, subject.provenance

    def _capital(self, subject):
        raw = self.budget_evidence.read(subject.capital_evidence.evidence_reference, subject.capital_evidence.evidence_sha256)
        capital = p.PortfolioCapitalDeclarationV1.model_validate(strict_json_bytes(raw))
        require(all(getattr(capital, key) == getattr(subject, key) for key in
            ("funding_scope_id", "allocation_id", "funding_limit_fen", "currency", "allocation_rule", "provenance")), "PF_CAPITAL_DECLARATION_SCOPE_MISMATCH")
        return capital

    def _available_run(self, session, run_id, at, *, own_lock_id=None):
        run = self.bridge._get(session, run_id, "REAL_PROSPECTIVE_RUN_V1")
        require(run.status == "PREPARING" and run.supersedes_lock is None, "PF_ACTIVE_INITIAL_PREPARING_RUN_REQUIRED")
        owner = session.execute(text("SELECT owner_kind,portfolio_lock_id FROM pf_shared_heads WHERE parent_run_id=:id"), {"id":run_id}).one_or_none()
        require(owner is None or (own_lock_id is not None and tuple(owner) == ("PORTFOLIO_LOCK", own_lock_id)), "PF_SOURCE_HEAD_ALREADY_CONSUMED")
        self.bridge._head_free(session, run)
        self.bridge._epoch(session, run.epoch.artifact_id, at)
        bucket = self.bridge._ref(session, run.bucket, "REAL_KICKOFF_BUCKET_V1")
        self.bridge._kickoff(session, bucket, at)
        anchor = self.bridge._ref(session, run.anchor, "REAL_EPOCH_CONFIGURATION_ANCHOR_V1")
        analysis = self.bridge._ref(session, run.analysis, "REAL_MULTI_MARKET_ANALYSIS_V1")
        require(analysis.status == "READY", "PF_WHOLE_BUCKET_UNAVAILABLE")
        pin = self.bridge._ref(session, anchor.model_pin, "REAL_MODEL_PIN_V1")
        require(pin.model_source.source_type in {"OPENFOOTBALL", "OPENFOOTBALL_QUALIFIED_V2"}, "PF_QUALIFIED_COMPETITION_MODEL_REQUIRED")
        revalidation = self.bridge._sources_current(session, analysis, anchor, at)
        for identity in run.identities:
            slot = session.execute(text("SELECT initial_portfolio_lock_id FROM pf_shared_slots WHERE program_id=:p AND match_id=:m AND market_hash=:k"),
                {"p":run.program.artifact_id,"m":identity.match_id,"k":THREE_WAY.market_hash}).one_or_none()
            require(slot is None or (own_lock_id is not None and slot[0] == own_lock_id), "PF_PREDICTION_SLOT_ALREADY_OWNED")
        return run, anchor, analysis, pin, revalidation

    @staticmethod
    def _snapshot_dependency(content):
        return content_hash("PF_SNAPSHOT_DEPENDENCIES_V1", content.model_dump(mode="python", exclude={"full_dependency_hash"}))

    def _snapshot_parts(self, session, snapshot, *, current_at=None):
        value = snapshot.content_payload
        if current_at is not None:
            run, anchor, analysis, pin, _ = self._available_run(session, value.run.artifact_id, current_at)
        else:
            run = self._rb_static(session, value.run)
            anchor = self._rb_static(session, value.anchor)
            analysis = self._rb_static(session, value.analysis)
            pin = self._rb_static(session, value.model_pin)
        require((run.program, run.epoch, run.anchor, run.bucket, run.analysis, run.packet) ==
            (value.program, value.epoch, value.anchor, value.bucket, value.analysis, value.packet)
            and ArtifactRefV1.of(pin) == value.model_pin and pin.model_source.source_type == value.model_source_type,
            "PF_SNAPSHOT_SOURCE_GRAPH_CHANGED")
        review = self._rb_static(session, value.review)
        audit = self._rb_static(session, value.review_audit)
        fusion = self._rb_static(session, value.fusion)
        scope_hash = pin.model_source.scope_hash if pin.model_source.source_type == "OPENFOOTBALL_QUALIFIED_V2" else session.execute(
            text("SELECT a.artifact_hash FROM ofp_phase_slots s JOIN ofp_artifacts a ON a.artifact_id=s.artifact_id WHERE s.phase='DATA_BINDING'")).scalar_one()
        require(audit.run == value.run and audit.event_at_utc <= value.sealed_at_utc and audit.review == value.review
            and value.source_cutoff == run.decision_cutoff and value.frames == source_frames(analysis, review, fusion, audit)
            and value.qualified_scope_hash == scope_hash and value.configuration_hash == run.configuration_hash
            and value.implementation_hash == run.implementation_hash and value.eligibility_census_hash == content_hash("PF_SOURCE_CENSUS_V1", run.identities)
            and value.full_dependency_hash == self._snapshot_dependency(value), "PF_SNAPSHOT_REPLAY_MISMATCH")
        return run, anchor, analysis, pin, review, audit, fusion

    @staticmethod
    def _census(snapshots):
        members = [(s.content_payload.program.artifact_id, frame.identity.match_id, frame.market_key.market_hash)
            for s in snapshots for frame in s.content_payload.frames]
        physical = [(mid, market) for _, mid, market in members]
        require(len(physical) == len(set(physical)), "PF_DUPLICATE_PHYSICAL_MATCH_MARKET")
        return content_hash("PF_TARGET_CENSUS_V1", tuple(sorted(members)))

    def prepare_budget_review(self, *, snapshot_ids, funding_scope_id, allocation_id, decision_cycle_id, budget_fen,
            funding_limit_fen, capital_evidence, effective_at_utc, expires_at_utc):
        with self.sessions.begin() as session:
            session.execute(text("BEGIN"))
            self._check(session)
            snapshots = tuple(self._load(session, key, "PREDICTION_SOURCE_SNAPSHOT_V1") for key in sorted(snapshot_ids))
            parts = [self._snapshot_parts(session, s, current_at=self.clock.now()) for s in snapshots]
            require(len({s.content_payload.provenance for s in snapshots}) == 1, "PF_MIXED_PROVENANCE")
            configuration = PortfolioNumericalConfigurationV1(members=tuple(item[2].configuration for item in parts))
            return p.PortfolioBudgetReviewV1(funding_scope_id=funding_scope_id, allocation_id=allocation_id, decision_cycle_id=decision_cycle_id,
                budget_fen=budget_fen, funding_limit_fen=funding_limit_fen, capital_evidence=capital_evidence, target_census_hash=self._census(snapshots),
                policy_hash=content_hash("PF_POLICY_V1", configuration), effective_at_utc=effective_at_utc, expires_at_utc=expires_at_utc,
                provenance=snapshots[0].content_payload.provenance)

    def _assembly_parts(self, session, assembly, *, current_at=None):
        c = assembly.content_payload
        snapshots = tuple(self._ref(session, r, "PREDICTION_SOURCE_SNAPSHOT_V1") for r in c.snapshots)
        require(len({s.content_payload.run.artifact_id for s in snapshots}) == len(snapshots), "PF_DUPLICATE_SOURCE_RUN")
        require(len({s.content_payload.provenance for s in snapshots}) == 1, "PF_MIXED_PROVENANCE")
        parts = [self._snapshot_parts(session, s, current_at=current_at) for s in snapshots]
        budget = self._ref(session, c.budget_authority, "PORTFOLIO_BUDGET_AUTHORITY_V1")
        if current_at is not None:
            checked, provenance = self._current_budget(session, budget.artifact_id, current_at)
            require(checked == budget and provenance == snapshots[0].content_payload.provenance, "PF_BUDGET_PROVENANCE_MISMATCH")
        require(self._census(snapshots) == c.target_census_hash == budget.content_payload.target_census_hash
            and budget.content_payload.policy_hash == content_hash("PF_POLICY_V1", PortfolioNumericalConfigurationV1(members=tuple(item[2].configuration for item in parts))), "PF_BUDGET_CENSUS_OR_POLICY_MISMATCH")
        plan = self._ref(session, c.strategy_plan, "PORTFOLIO_STRATEGY_PLAN_V1")
        optimizer = self._ref(session, c.optimizer, "RETURN_OPTIMIZATION_RUN_V1")
        policy, objective = default_return_configuration()
        expected_plan, expected_optimizer = calculate_portfolio(snapshots, [x[2] for x in parts], [x[6] for x in parts], budget, plan.requests, policy, objective)
        require(plan == expected_plan and optimizer == expected_optimizer and c.return_evaluation == ArtifactRefV1.of(optimizer.result), "PF_ASSEMBLY_NUMERICAL_REPLAY_MISMATCH")
        require(c.earliest_member_kickoff == min(f.identity.kickoff_at_utc for s in snapshots for f in s.content_payload.frames)
            and c.budget_fen == budget.content_payload.budget_fen and c.stake_fen == optimizer.result.distribution.stake_fen
            and c.cash_fen == optimizer.result.distribution.cash_fen, "PF_ASSEMBLY_MONEY_OR_TIME_MISMATCH")
        projection = content_hash("PF_NUMERICAL_PROJECTION_V1", (ArtifactRefV1.of(plan.source.analysis), ArtifactRefV1.of(plan.source.fusion),
            ArtifactRefV1.of(plan.source), ArtifactRefV1.of(plan), ArtifactRefV1.of(optimizer)))
        members = content_hash("PF_MEMBER_SET_V1", tuple(ArtifactRefV1.of(s) for s in snapshots))
        dependency = content_hash("PF_ASSEMBLY_DEPENDENCIES_V1", dict(members=members, budget=ArtifactRefV1.of(budget),
            census=self._census(snapshots), projection=projection, cutoff=c.common_cutoff, earliest=c.earliest_member_kickoff))
        require((c.member_set_hash,c.numerical_projection_hash,c.full_dependency_hash) == (members,projection,dependency), "PF_ASSEMBLY_DEPENDENCY_HASH_MISMATCH")
        require(all(session.scalar(text("SELECT published_us FROM pf_seals WHERE artifact_id=:id"), {"id":s.artifact_id}) <= micros(c.common_cutoff)
            for s in snapshots), "PF_SNAPSHOT_PUBLISHED_AFTER_COMMON_CUTOFF")
        return snapshots, parts, budget, plan, optimizer

    def execute(self, operation, request):
        cls = REQUEST_TYPES.get(operation)
        require(cls is not None and type(request) is cls, "PF_CLOSED_REQUEST_REQUIRED")
        request = cls.model_validate(request.model_dump(mode="python"))
        digest = content_hash("PF_REQUEST_"+operation, request)
        checked = False
        try:
            with self.sessions.begin() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._check(session)
                checked = True
                old = session.execute(text("SELECT * FROM pf_receipts WHERE request_key=:k"), {"k":request.request_key}).mappings().one_or_none()
                if old:
                    require((old["operation"], old["request_hash"]) == (operation, digest), "PF_REQUEST_KEY_CONTENT_CONFLICT")
                    return self._load(session, old["response_id"])
                rejected = session.execute(text("SELECT * FROM pf_rejections WHERE request_key=:k"), {"k":request.request_key}).mappings().one_or_none()
                if rejected:
                    require((rejected["operation"], rejected["request_hash"]) == (operation, digest), "PF_REQUEST_KEY_CONTENT_CONFLICT")
                    raise ValueError(rejected["reason"])
                at = self.clock.now()
                sequence = session.scalar(text("SELECT COALESCE(max(sequence),0)+1 FROM pf_receipts"))
                provenance = "LIVE_OBSERVATION" if self.clock.basis == "LOCAL_SYSTEM_UTC" else "SYNTHETIC_SOFTWARE_ACCEPTANCE"
                receipt = p.PortfolioReceiptV1.freeze(request_key=request.request_key, operation=operation, request_hash=digest, sequence=sequence,
                    event_at_utc=at, clock_basis=self.clock.basis, provenance=provenance)
                response, extra = self._build(session, operation, request, at, receipt)
                session.execute(self._table("pf_receipts").insert().values(request_key=request.request_key, operation=operation, request_hash=digest,
                    request_json=canonical_json(request), sequence=sequence, event_at=stamp(at), event_us=micros(at), provenance=provenance,
                    clock_basis=self.clock.basis, receipt_id=receipt.artifact_id, response_id=response.artifact_id))
                self._store(session, (receipt, *extra, response), request.request_key)
                final = self.clock.now()
                require(at <= final <= at+timedelta(seconds=30), "PF_PUBLICATION_CLOCK_WINDOW_EXCEEDED")
                if operation == "lock":
                    assembly = self._ref(session, response.content_payload.assembly)
                    # Claims are now ours; current member validation uses source guards
                    # without allowing a second owner or recomputing later evidence.
                    self._lock_final_check(session, response, assembly, final)
                elif operation == "budget-record":
                    self._current_budget(session,response.artifact_id,final)
                elif operation == "snapshot":
                    self._snapshot_parts(session,response,current_at=final)
                elif operation == "assembly":
                    self._current_budget(session,response.content_payload.budget_authority.artifact_id,final)
                    for snapshot_ref in response.content_payload.snapshots:
                        snapshot=self._ref(session,snapshot_ref)
                        self._available_run(session,snapshot.content_payload.run.artifact_id,final)
                completed=self.clock.now()
                require(final<=completed<=at+timedelta(seconds=30), "PF_PUBLICATION_CLOCK_WINDOW_EXCEEDED")
                if operation=="lock":
                    require(completed+timedelta(seconds=60)<response.content_payload.earliest_member_kickoff, "PF_FINAL_PUBLICATION_TOO_LATE")
                require(not session.execute(text("PRAGMA foreign_key_check")).all(), "PF_INCOMPLETE_TRANSACTION")
                return response
        except (ValueError, KeyError, IntegrityError, NoResultFound) as error:
            reason = str(error)
            reason = reason if 0 < len(reason) <= 120 and all(c.isupper() or c.isdigit() or c == "_" for c in reason) else "PF_INPUT_OR_SOURCE_REJECTED"
            if not checked:
                raise ValueError(reason) from error
            with self.sessions.begin() as session:
                if not session.scalar(text("SELECT count(*) FROM pf_receipts WHERE request_key=:k"), {"k":request.request_key}) and not session.scalar(text("SELECT count(*) FROM pf_rejections WHERE request_key=:k"), {"k":request.request_key}):
                    session.execute(self._table("pf_rejections").insert().values(request_key=request.request_key, operation=operation,
                        request_hash=digest, recorded_at=stamp(self.clock.now()), reason=reason))
            raise ValueError(reason) from error

    def _store(self, session, values, key):
        seen = set()
        for value in values:
            for node in embedded_nodes(value):
                if node.artifact_id in seen:
                    continue
                seen.add(node.artifact_id)
                raw = canonical_json(node)
                prior = session.scalar(text("SELECT artifact_json FROM pf_artifacts WHERE artifact_id=:id"), {"id":node.artifact_id})
                if prior is not None:
                    require(prior == raw, "PF_ARTIFACT_COLLISION")
                    continue
                session.execute(self._table("pf_artifacts").insert().values(artifact_id=node.artifact_id, schema_version=node.schema_version,
                    content_hash=node.content_hash, artifact_json=raw, event_key=key))
                for edge in refs(node):
                    table = "pf_edges" if edge["ledger"] == "PF" else "pf_rb_edges"
                    session.execute(self._table(table).insert().values(parent_id=node.artifact_id,
                        **{k:v for k,v in edge.items() if k != "ledger"}))
                self._project(session, node)
                published = self.clock.now()
                session.execute(self._table("pf_seals").insert().values(artifact_id=node.artifact_id, published_at=stamp(published), published_us=micros(published)))

    def _project(self, session, value):
        schema, aid = value.schema_version, value.artifact_id
        c = getattr(value, "content_payload", None)
        if schema == "PORTFOLIO_BUDGET_AUTHORITY_V1":
            approval = self._ref(session, c.exact_approval, "PORTFOLIO_BUDGET_APPROVAL_RECORD_V1")
            session.execute(self._table("pf_budget_grants").insert().values(grant_id=aid, funding_scope_id=c.funding_scope_id, allocation_id=c.allocation_id,
                cycle_id=c.decision_cycle_id, budget_fen=c.budget_fen, capital_hash=approval.subject.capital_evidence.evidence_sha256))
        elif schema == "PREDICTION_SOURCE_SNAPSHOT_V1":
            session.execute(self._table("pf_snapshots").insert().values(snapshot_id=aid, run_id=c.run.artifact_id, program_id=c.program.artifact_id,
                pin_id=c.model_pin.artifact_id, member_count=len(c.frames)))
            for frame in c.frames:
                session.execute(self._table("pf_snapshot_units").insert().values(snapshot_id=aid, match_id=frame.identity.match_id,
                    market_hash=frame.market_key.market_hash, unit_id=frame.unit.artifact_id, sp_id=frame.sp_snapshot.artifact_id))
        elif schema == "PORTFOLIO_ASSEMBLY_V1":
            snapshots = tuple(self._ref(session, r) for r in c.snapshots)
            session.execute(self._table("pf_assemblies").insert().values(assembly_id=aid, grant_id=c.budget_authority.artifact_id,
                member_count=len(snapshots), unit_count=sum(len(s.content_payload.frames) for s in snapshots)))
            for position, snapshot in enumerate(snapshots):
                session.execute(self._table("pf_assembly_members").insert().values(assembly_id=aid, snapshot_id=snapshot.artifact_id,
                    run_id=snapshot.content_payload.run.artifact_id, position=position))
                for frame in snapshot.content_payload.frames:
                    session.execute(self._table("pf_assembly_units").insert().values(assembly_id=aid, snapshot_id=snapshot.artifact_id,
                        match_id=frame.identity.match_id, market_hash=frame.market_key.market_hash, program_id=snapshot.content_payload.program.artifact_id))
        elif schema == "ATOMIC_PORTFOLIO_LOCK_V1":
            session.execute(self._table("pf_locks").insert().values(lock_id=aid, assembly_id=c.assembly.artifact_id,
                grant_id=c.budget_authority.artifact_id, locked_us=micros(c.locked_at_utc)))
            members = session.execute(text("SELECT * FROM pf_assembly_members WHERE assembly_id=:id ORDER BY position"), {"id":c.assembly.artifact_id}).mappings().all()
            units = session.execute(text("SELECT * FROM pf_assembly_units WHERE assembly_id=:id ORDER BY match_id"), {"id":c.assembly.artifact_id}).mappings().all()
            for member in members:
                session.execute(self._table("pf_shared_heads").insert().values(parent_run_id=member["run_id"], owner_kind="PORTFOLIO_LOCK", legacy_event_id=None, portfolio_lock_id=aid))
            for unit in units:
                slot = {k:unit[k] for k in ("program_id","match_id","market_hash")}
                session.execute(self._table("pf_shared_slots").insert().values(**slot, initial_legacy_lock_id=None, initial_portfolio_lock_id=aid))
                session.execute(self._table("pf_shared_versions").insert().values(**slot, version=0, previous_version=None, legacy_lock_id=None, portfolio_lock_id=aid))
            session.execute(self._table("pf_budget_claims").insert().values(grant_id=c.budget_authority.artifact_id, lock_id=aid))
        elif schema == "PORTFOLIO_SETTLEMENT_V1":
            result_set = session.execute(text("SELECT artifact_id FROM pf_artifacts WHERE schema_version='PORTFOLIO_RESULT_SET_V1' AND content_hash=:hash"), {"hash":c.result_set_hash}).scalar_one()
            version = session.scalar(text("SELECT COALESCE(max(version),-1)+1 FROM pf_settlements WHERE lock_id=:id"), {"id":c.portfolio_lock.artifact_id})
            session.execute(self._table("pf_settlements").insert().values(settlement_id=aid, lock_id=c.portfolio_lock.artifact_id, version=version,
                previous_id=c.previous.artifact_id if c.previous else None, result_set_id=result_set))
            for ref in c.observations:
                observation = self._rb_static(session, ref)
                session.execute(self._table("pf_settlement_observations").insert().values(settlement_id=aid, match_id=observation.claim.match_id,
                    observation_id=observation.artifact_id))

    def load(self, identity):
        with self.sessions.begin() as session:
            session.execute(text("BEGIN"))
            self._check(session)
            return self._load(session, identity)

    def _build(self, session, operation, request, at, receipt):
        if operation == "budget-record":
            subject = p.PortfolioBudgetReviewV1.model_validate(request.subject)
            require(subject.provenance == receipt.provenance and subject.effective_at_utc <= at < subject.expires_at_utc,
                "PF_BUDGET_PROVENANCE_OR_TIME_INVALID")
            self.budget_evidence.load_authority(request.authority).assert_active_for(at)
            reviewed = self.budget_evidence.review(evidence=request.review, authority=request.authority, schema=subject.schema_version,
                digest=subject.content_hash, source_ids=(subject.source_id,), operator_id="portfolio-operator", at_utc=at)
            self._capital(subject)
            authority = p.PortfolioAuthorityEvidenceV1.freeze(source_id=subject.source_id, authority=request.authority)
            approval = p.PortfolioBudgetApprovalV1.freeze(subject=subject, subject_hash=subject.content_hash, review=request.review,
                authority=ArtifactRefV1.of(authority), reviewed_at_utc=reviewed.reviewed_at_utc)
            fields = subject.model_dump(mode="python", exclude={"schema_version","funding_limit_fen","capital_evidence","provenance"})
            value = p.PortfolioBudgetAuthorityV1.freeze(content_payload=p.BudgetAuthorityContentV1(**fields,
                operator_authority=ArtifactRefV1.of(authority), exact_approval=ArtifactRefV1.of(approval)))
            return value, (authority, approval)
        if operation == "ticket-requests":
            requests = tuple(sorted(request.requests, key=canonical_json))
            return p.PortfolioTicketRequestBundleV1.freeze(requests=requests), ()
        if operation == "snapshot":
            from football_system.domain.services.review_v4 import fuse_v4
            run, anchor, analysis, pin, _ = self._available_run(session, request.source_run_id, at)
            require(run.provenance == receipt.provenance, "PF_MIXED_PROVENANCE")
            review = self.bridge._get(session, request.imported_review_id, "IMPORTED_LLM_REVIEW_V4")
            audits = session.execute(text("SELECT a.artifact_id FROM rb_artifacts a JOIN rb_seals z ON z.artifact_id=a.artifact_id WHERE a.schema_version='REAL_REVIEW_AUDIT_V1' AND json_extract(a.artifact_json,'$.run.artifact_id')=:run AND json_extract(a.artifact_json,'$.review.artifact_id')=:review"),
                {"run":run.artifact_id,"review":review.artifact_id}).scalars().all()
            require(len(audits) == 1, "PF_UNIQUE_SOURCE_REVIEW_AUDIT_REQUIRED")
            audit = self.bridge._get(session, audits[0], "REAL_REVIEW_AUDIT_V1")
            require(audit.event_at_utc <= at, "PF_REVIEW_RECEIPT_AFTER_CUTOFF")
            if request.review_mode == "EXPLICIT_V4_ABSTENTION":
                require(all(r.status == "UNAVAILABLE" and r.failure_code == "SKIPPED_DISABLED" for r in review.submission.market_reviews),
                    "PF_EXPLICIT_ABSTENTION_RECORD_REQUIRED")
            expected_fusion = fuse_v4(analysis, review, anchor.policy.configuration.fusion)
            fusion = self.bridge._get(session, expected_fusion.artifact_id, "GENERIC_FUSION_RUN_V1")
            require(fusion == expected_fusion, "PF_ORIGINAL_FUSION_REQUIRED")
            if pin.model_source.source_type == "OPENFOOTBALL_QUALIFIED_V2":
                scope_hash = pin.model_source.scope_hash
            else:
                scope_hash = session.execute(text("SELECT a.artifact_hash FROM ofp_phase_slots s JOIN ofp_artifacts a ON a.artifact_id=s.artifact_id WHERE s.phase='DATA_BINDING'")).scalar_one()
            content = p.SnapshotContentV1(run=ArtifactRefV1.of(run), program=run.program, epoch=run.epoch, anchor=run.anchor, bucket=run.bucket,
                model_pin=anchor.model_pin, model_source_type=pin.model_source.source_type, qualified_scope_hash=scope_hash,
                competition_id=run.identities[0].competition_id, season_id=run.identities[0].season_id, analysis=run.analysis, packet=run.packet,
                review=ArtifactRefV1.of(review), review_audit=ArtifactRefV1.of(audit), fusion=ArtifactRefV1.of(fusion), review_mode=request.review_mode,
                frames=source_frames(analysis, review, fusion, audit), source_cutoff=run.decision_cutoff, sealed_at_utc=at,
                eligibility_census_hash=content_hash("PF_SOURCE_CENSUS_V1", run.identities), full_dependency_hash="0"*64,
                configuration_hash=run.configuration_hash, implementation_hash=run.implementation_hash,
                receipt=ArtifactRefV1.of(receipt), provenance=run.provenance)
            content = p.SnapshotContentV1.model_validate(content.model_dump(mode="python") | {"full_dependency_hash":self._snapshot_dependency(content)})
            return p.PredictionSourceSnapshotV1.freeze(content_payload=content), ()
        if operation == "assembly":
            ids = tuple(request.snapshot_ids)
            require(ids == tuple(sorted(set(ids))) and len(ids) >= 2, "PF_ORDERED_UNIQUE_SNAPSHOT_SET_REQUIRED")
            snapshots = tuple(self._load(session, key, "PREDICTION_SOURCE_SNAPSHOT_V1") for key in ids)
            require(len({s.content_payload.run.artifact_id for s in snapshots}) == len(snapshots)
                and all(s.content_payload.provenance == receipt.provenance for s in snapshots), "PF_DUPLICATE_RUN_OR_MIXED_PROVENANCE")
            parts = [self._snapshot_parts(session, snapshot, current_at=at) for snapshot in snapshots]
            require(all(s.content_payload.sealed_at_utc <= at for s in snapshots), "PF_SNAPSHOT_FROM_FUTURE")
            budget, provenance = self._current_budget(session, request.budget_authority_id, at)
            require(provenance == receipt.provenance and self._census(snapshots) == budget.content_payload.target_census_hash,
                "PF_BUDGET_COMPLETE_CENSUS_REQUIRED")
            require(budget.content_payload.policy_hash == content_hash("PF_POLICY_V1", PortfolioNumericalConfigurationV1(members=tuple(item[2].configuration for item in parts))), "PF_BUDGET_POLICY_MISMATCH")
            bundle = self._load(session, request.ticket_request_bundle_id, "PORTFOLIO_TICKET_REQUEST_BUNDLE_V1")
            policy, objective = default_return_configuration()
            plan, optimizer = calculate_portfolio(snapshots, [v[2] for v in parts], [v[6] for v in parts], budget, bundle.requests, policy, objective)
            projection_hash = content_hash("PF_NUMERICAL_PROJECTION_V1", (ArtifactRefV1.of(plan.source.analysis), ArtifactRefV1.of(plan.source.fusion),
                ArtifactRefV1.of(plan.source), ArtifactRefV1.of(plan), ArtifactRefV1.of(optimizer)))
            members_hash = content_hash("PF_MEMBER_SET_V1", tuple(ArtifactRefV1.of(s) for s in snapshots))
            earliest = min(f.identity.kickoff_at_utc for s in snapshots for f in s.content_payload.frames)
            dependency = content_hash("PF_ASSEMBLY_DEPENDENCIES_V1", dict(members=members_hash, budget=ArtifactRefV1.of(budget),
                census=self._census(snapshots), projection=projection_hash, cutoff=at, earliest=earliest))
            value = p.PortfolioAssemblyV1.freeze(content_payload=p.AssemblyContentV1(snapshots=tuple(ArtifactRefV1.of(s) for s in snapshots),
                budget_authority=ArtifactRefV1.of(budget), target_census_hash=self._census(snapshots), prepared_at_utc=at, common_cutoff=at,
                earliest_member_kickoff=earliest, member_set_hash=members_hash, full_dependency_hash=dependency,
                numerical_projection_hash=projection_hash, strategy_plan=ArtifactRefV1.of(plan), optimizer=ArtifactRefV1.of(optimizer),
                return_evaluation=ArtifactRefV1.of(optimizer.result), status="ALLOCATED" if optimizer.result.selected else "NO_BET",
                budget_fen=budget.content_payload.budget_fen, stake_fen=optimizer.result.distribution.stake_fen, cash_fen=optimizer.result.distribution.cash_fen))
            return value, (plan, optimizer)
        if operation == "lock":
            assembly = self._load(session, request.assembly_id, "PORTFOLIO_ASSEMBLY_V1")
            snapshots, _, budget, _, _ = self._assembly_parts(session, assembly, current_at=at)
            require(all(s.content_payload.provenance == receipt.provenance for s in snapshots), "PF_MIXED_PROVENANCE")
            require(session.scalar(text("SELECT count(*) FROM pf_budget_claims WHERE grant_id=:id"), {"id":budget.artifact_id}) == 0, "PF_BUDGET_ALREADY_CLAIMED")
            ownership = self._ownership_manifest(snapshots, budget)
            c = assembly.content_payload
            checks = tuple(p.PortfolioMemberRevalidationV1(snapshot=ArtifactRefV1.of(snapshot), source_run=snapshot.content_payload.run,
                source_revalidation_hash=self._available_run(session, snapshot.content_payload.run.artifact_id, at)[4]) for snapshot in snapshots)
            revalidation = p.PortfolioLockRevalidationV1.freeze(assembly=ArtifactRefV1.of(assembly), budget_authority=ArtifactRefV1.of(budget),
                members=checks, checked_at_utc=at, receipt=ArtifactRefV1.of(receipt))
            value = p.AtomicPortfolioLockV1.freeze(content_payload=p.LockContentV1(assembly=ArtifactRefV1.of(assembly), budget_authority=ArtifactRefV1.of(budget),
                member_set_hash=c.member_set_hash, ownership_manifest_hash=ownership, revalidation_hash=revalidation.content_hash,
                common_cutoff=c.common_cutoff, locked_at_utc=at,
                earliest_member_kickoff=c.earliest_member_kickoff, receipt=ArtifactRefV1.of(receipt), budget_fen=c.budget_fen, stake_fen=c.stake_fen, cash_fen=c.cash_fen))
            return value, (revalidation,)
        if operation == "results":
            lock = self._load(session, request.portfolio_lock_id, "ATOMIC_PORTFOLIO_LOCK_V1")
            assembly = self._ref(session, lock.content_payload.assembly, "PORTFOLIO_ASSEMBLY_V1")
            snapshots, parts, _, _, optimizer = self._assembly_parts(session, assembly)
            self._assert_lock_ownership(session,lock,snapshots)
            observations = self._result_heads(session, parts, at)
            identities = tuple(f.identity for s in snapshots for f in s.content_payload.frames)
            values = settle_portfolio(assembly, lock, optimizer.result, identities, observations, at)
            return p.PortfolioResultSetV1.freeze(portfolio_lock=ArtifactRefV1.of(lock), observations=values["observations"],
                missing_match_ids=values["missing_match_ids"], unsupported_match_ids=values["unsupported_match_ids"], as_of_at_utc=at), ()
        if operation == "settle":
            lock = self._load(session, request.portfolio_lock_id, "ATOMIC_PORTFOLIO_LOCK_V1")
            assembly = self._ref(session, lock.content_payload.assembly, "PORTFOLIO_ASSEMBLY_V1")
            snapshots, parts, _, _, optimizer = self._assembly_parts(session, assembly)
            self._assert_lock_ownership(session,lock,snapshots)
            result_set = self._load(session, request.result_observation_set_id, "PORTFOLIO_RESULT_SET_V1")
            require(result_set.portfolio_lock == ArtifactRefV1.of(lock) and result_set.as_of_at_utc <= at, "PF_RESULT_SET_SCOPE_OR_TIME_MISMATCH")
            observations = self._result_heads(session, parts, at)
            require(tuple(ArtifactRefV1.of(o) for o in observations) == result_set.observations, "PF_RESULT_SET_NOT_CURRENT")
            previous_id = session.execute(text("SELECT settlement_id FROM pf_settlements WHERE lock_id=:id ORDER BY version DESC LIMIT 1"), {"id":lock.artifact_id}).scalar_one_or_none()
            previous = self._load(session, previous_id, "PORTFOLIO_SETTLEMENT_V1") if previous_id else None
            if previous:
                require(previous.content_payload.settled_at_utc < at and previous.content_payload.observations != result_set.observations,
                    "PF_SETTLEMENT_REQUIRES_NEW_RESULT_VERSION")
            identities = tuple(f.identity for s in snapshots for f in s.content_payload.frames)
            values = settle_portfolio(assembly, lock, optimizer.result, identities, observations, at)
            value = p.PortfolioSettlementV1.freeze(content_payload=p.SettlementContentV1(portfolio_lock=ArtifactRefV1.of(lock),
                previous=ArtifactRefV1.of(previous) if previous else None, observations=values["observations"], settled_at_utc=at,
                result_set_hash=result_set.content_hash, status=values["reason"], missing_match_ids=values["missing_match_ids"],
                budget_fen=values["budget_fen"], stake_fen=values["stake_fen"], cash_fen=values["cash_fen"], gross_payout_fen=values["gross_payout_fen"],
                ending_capital_fen=values["ending_capital_fen"], profit_loss_fen=values["profit_loss_fen"]))
            return value, ()
        raise ValueError("PF_UNKNOWN_OPERATION")

    @staticmethod
    def _ownership_manifest(snapshots, budget):
        return content_hash("PF_OWNERSHIP_MANIFEST_V1", dict(budget=ArtifactRefV1.of(budget),
            heads=tuple(sorted(s.content_payload.run.artifact_id for s in snapshots)),
            slots=tuple(sorted((s.content_payload.program.artifact_id, f.identity.match_id, f.market_key.market_hash)
                for s in snapshots for f in s.content_payload.frames))))

    def _lock_final_check(self, session, lock, assembly, at):
        c = lock.content_payload
        require(c.common_cutoff <= c.locked_at_utc <= at and at+timedelta(seconds=60) < c.earliest_member_kickoff, "PF_FINAL_PUBLICATION_TOO_LATE")
        budget, _ = self._current_budget(session, c.budget_authority.artifact_id, at)
        snapshots = tuple(self._ref(session, r) for r in assembly.content_payload.snapshots)
        require(c.member_set_hash == assembly.content_payload.member_set_hash and c.ownership_manifest_hash == self._ownership_manifest(snapshots,budget),
            "PF_LOCK_MEMBER_OR_OWNERSHIP_CHANGED")
        for snapshot in snapshots:
            self._available_run(session, snapshot.content_payload.run.artifact_id, at, own_lock_id=lock.artifact_id)
        self._assert_lock_ownership(session,lock,snapshots)

    def _assert_lock_ownership(self,session,lock,snapshots):
        c = lock.content_payload
        assembly = self._ref(session, c.assembly, "PORTFOLIO_ASSEMBLY_V1")
        proof = self._load(session, stable_id("PORTFOLIO_LOCK_REVALIDATION_V1", c.revalidation_hash), "PORTFOLIO_LOCK_REVALIDATION_V1")
        require(proof.content_hash == c.revalidation_hash and proof.assembly == c.assembly and proof.budget_authority == c.budget_authority
            and proof.receipt == c.receipt and proof.checked_at_utc == c.locked_at_utc
            and tuple((m.snapshot,m.source_run) for m in proof.members) == tuple((ArtifactRefV1.of(s),s.content_payload.run) for s in snapshots)
            and all(getattr(c,key)==getattr(assembly.content_payload,key) for key in
                ("budget_authority","member_set_hash","common_cutoff","earliest_member_kickoff","budget_fen","stake_fen","cash_fen")), "PF_LOCK_REVALIDATION_OR_FINANCIAL_GRAPH_MISMATCH")
        for member,snapshot in zip(proof.members,snapshots,strict=True):
            analysis=self._rb_static(session,snapshot.content_payload.analysis)
            pin=self._rb_static(session,snapshot.content_payload.model_pin)
            admissions=[ArtifactRefV1.of(self.bridge._admit(session,pin.admission,proof.checked_at_utc))]
            admissions.extend(ArtifactRefV1.of(self.bridge._admit(session,binding.admission,proof.checked_at_utc))
                for unit in analysis.units for binding in (unit.consensus,unit.sporttery))
            expected_hash=content_hash("REAL_LOCK_REVALIDATION_V1",dict(pin=ArtifactRefV1.of(pin),admissions=admissions,
                analysis=ArtifactRefV1.of(analysis),at=proof.checked_at_utc))
            require(member.source_revalidation_hash==expected_hash,"PF_SOURCE_REVALIDATION_REPLAY_MISMATCH")
        heads=set(session.scalars(text("SELECT parent_run_id FROM pf_shared_heads WHERE portfolio_lock_id=:id AND owner_kind='PORTFOLIO_LOCK'"),{"id":lock.artifact_id}))
        slots={tuple(row) for row in session.execute(text("SELECT program_id,match_id,market_hash FROM pf_shared_slots WHERE initial_portfolio_lock_id=:id"),{"id":lock.artifact_id})}
        versions={tuple(row) for row in session.execute(text("SELECT program_id,match_id,market_hash FROM pf_shared_versions WHERE portfolio_lock_id=:id AND version=0 AND previous_version IS NULL"),{"id":lock.artifact_id})}
        expected={(s.content_payload.program.artifact_id,f.identity.match_id,f.market_key.market_hash) for s in snapshots for f in s.content_payload.frames}
        require(heads=={s.content_payload.run.artifact_id for s in snapshots} and slots==versions==expected
            and session.scalar(text("SELECT count(*) FROM pf_budget_claims WHERE lock_id=:lock AND grant_id=:grant"),
                {"lock":lock.artifact_id,"grant":lock.content_payload.budget_authority.artifact_id})==1, "PF_COMMITTED_OWNERSHIP_GRAPH_INCOMPLETE")

    def _result_heads(self, session, parts, at):
        values = [observation for run, anchor, *_ in parts for observation in self.bridge._results(session, run, anchor, at)]
        require(len({v.claim.match_id for v in values}) == len(values), "PF_DUPLICATE_RESULT_OBSERVATION")
        return tuple(sorted(values, key=lambda v:v.claim.match_id))

    def audit(self, identity):
        with self.sessions.begin() as session:
            session.execute(text("BEGIN"))
            self._check(session)
            value = self._load(session, identity)
            at = self.clock.now()
            extra = dict(as_of_at_utc=stamp(at), receipt_watermarks={name: session.scalar(text("SELECT COALESCE(max(sequence),0) FROM "+name)) for name in ("rb_receipts","pf_receipts")})
            if value.schema_version == "PREDICTION_SOURCE_SNAPSHOT_V1":
                self._snapshot_parts(session,value)
            elif value.schema_version == "PORTFOLIO_ASSEMBLY_V1":
                self._assembly_parts(session,value)
            elif value.schema_version in {"ATOMIC_PORTFOLIO_LOCK_V1","PORTFOLIO_SETTLEMENT_V1"}:
                lock = value if value.schema_version == "ATOMIC_PORTFOLIO_LOCK_V1" else self._ref(session,value.content_payload.portfolio_lock)
                assembly = self._ref(session,lock.content_payload.assembly)
                snapshots, parts, budget, _, optimizer = self._assembly_parts(session,assembly)
                self._assert_lock_ownership(session,lock,snapshots)
                require(lock.content_payload.ownership_manifest_hash == self._ownership_manifest(snapshots,budget), "PF_AUDIT_OWNERSHIP_MISMATCH")
                current_sources = []
                for _, _, analysis, pin, *_ in parts:
                    try:
                        self.bridge._model_valid(session,pin,at)
                        for unit in analysis.units:
                            for binding in (unit.consensus,unit.sporttery):
                                self.bridge._source_not_invalidated(session,binding,at)
                                self.bridge._admit(session,binding.admission,at)
                        current_sources.append(dict(model_pin_id=pin.artifact_id,status="CURRENT_AUTHORITY_VALID"))
                    except ValueError as error:
                        current_sources.append(dict(model_pin_id=pin.artifact_id,status="CURRENT_AUTHORITY_UNAVAILABLE",reason=str(error)))
                extra.update(report_schema="PORTFOLIO_OWNERSHIP_AUDIT_V1", source_census=[dict(run_id=s.content_payload.run.artifact_id,
                    program_id=s.content_payload.program.artifact_id, model_pin_id=s.content_payload.model_pin.artifact_id,
                    state="CONSUMED_BY_PORTFOLIO", portfolio_lock_id=lock.artifact_id,
                    match_ids=[f.identity.match_id for f in s.content_payload.frames]) for s in snapshots],
                    current_sources=current_sources, committed_prediction_slots=sum(len(s.content_payload.frames) for s in snapshots),
                    real_prospective_samples=0 if snapshots[0].content_payload.provenance=="SYNTHETIC_SOFTWARE_ACCEPTANCE" else None)
                if value.schema_version == "PORTFOLIO_SETTLEMENT_V1":
                    observations = tuple(self._rb_static(session,r) for r in value.content_payload.observations)
                    identities = tuple(f.identity for s in snapshots for f in s.content_payload.frames)
                    expected = settle_portfolio(assembly,lock,optimizer.result,identities,observations,value.content_payload.settled_at_utc)
                    require((value.content_payload.gross_payout_fen,value.content_payload.profit_loss_fen,value.content_payload.status)
                        == (expected["gross_payout_fen"],expected["profit_loss_fen"],expected["reason"]), "PF_SETTLEMENT_REPLAY_MISMATCH")
                    current = tuple(ArtifactRefV1.of(o) for o in self._result_heads(session,parts,at))
                    extra["current_result_view"] = "CURRENT" if current==value.content_payload.observations else "STALE_RESULT_VERSION"
            return dict(status="PORTFOLIO_AUDIT_PASS",artifact_id=value.artifact_id,schema_version=value.schema_version,content_hash=value.content_hash,**extra)
