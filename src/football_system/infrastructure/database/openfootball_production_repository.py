"""Append-only OpenFootball adapter on the existing production repository/session.

The external evidence boundary supplies genuine reviews/authority. This module
never manufactures approved reviews, fetches data, or creates real-bridge events.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import select, text

from football_system.application.openfootball_production import (
    facts_at_admission, facts_root, parse_utc, prepare_openfootball_binding, structural_replay,
)
from football_system.application.run_analysis import _code_revision
from football_system.domain.archive import canonical_json
from football_system.domain.common import normalize_utc, stable_id
from football_system.domain.openfootball_production import (
    BOOTSTRAP_ID, CONFIG_HASH, SOURCE_ID,
    OpenFootballCanonicalReviewV1, OpenFootballModelApprovalPayloadV1,
    OpenFootballProductionArtifactV1, OpenFootballProductionTargetV1, OpenFootballSourceRightsPayloadV1,
    canonical_id, fixed_exceptions, fixed_window, require, validate_fact_cohort,
)
from football_system.domain.openfootball_snapshot import OpenFootballAdapterPolicyV1
from football_system.domain.services.elo_baseline import EloBaselineState, EloPredictionRequest, EloThreeWayBaseline
from football_system.domain.training_admission import LocalReviewEvidenceV1, tagged_canonical_sha256
from football_system.infrastructure.database.models import (
    Base, CanonicalMatchIdentityRecord, CompetitionRecord, MatchRecord, MatchResultRecord,
    ProviderMatchMappingRecord, ProviderRecord, TeamRecord, LiveAnalysisPreparationRecord, LiveAnalysisPreparationMatchRecord,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes


class SqlAlchemyOpenFootballProductionRepository:
    """Created through SqlAlchemyProductionQuantRepository.openfootball_binding()."""

    def __init__(self, production_repository):
        self.production = production_repository
        self._sessions = production_repository._sessions
        self.evidence = production_repository.admission_repository.evidence
        self.operator_id = production_repository.operator_id
        self._clock = production_repository._now
        self._lock = RLock()
        self._last = None

    def _now(self):
        with self._lock:
            at = normalize_utc(self._clock())
            require(self._last is None or at >= self._last, "OPENFOOTBALL_CLOCK_MOVED_BACKWARDS")
            self._last = at
            return at

    @contextmanager
    def _transaction(self, write=False):
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE" if write else "BEGIN"))
            yield session

    def _table(self, name):
        return Base.metadata.tables[name]

    def _load(self, session, artifact_id, kind=None):
        table = self._table("ofp_artifacts")
        row = session.execute(select(table).where(table.c.artifact_id == artifact_id)).mappings().one()
        artifact = OpenFootballProductionArtifactV1.model_validate(strict_json_bytes(row["artifact_json"].encode()))
        require((row["artifact_id"], row["artifact_hash"], row["kind"], row["recorded_at_utc"]) ==
            (artifact.artifact_id, artifact.artifact_hash, artifact.kind, artifact.model_dump(mode="json")["recorded_at_utc"]), "ARTIFACT_COLUMN_PROJECTION_MISMATCH")
        require(kind is None or artifact.kind == kind, "WRONG_OPENFOOTBALL_ARTIFACT_KIND")
        links = self._table("ofp_parent_links")
        actual = tuple(sorted(session.execute(select(links.c.parent_id, links.c.parent_hash).where(links.c.child_id == artifact_id)).all()))
        require(actual == artifact.parents, "OPENFOOTBALL_PARENT_SET_MISMATCH")
        for parent_id, parent_hash in artifact.parents:
            parent = session.execute(select(table.c.artifact_hash, table.c.recorded_at_utc).where(table.c.artifact_id == parent_id)).one()
            require(parent[0] == parent_hash and parse_utc(parent[1]) <= artifact.recorded_at_utc, "OPENFOOTBALL_PARENT_HASH_OR_TIME_MISMATCH")
        return artifact

    def _phase(self, session, kind, required=True):
        table = self._table("ofp_phase_slots")
        key = session.scalar(select(table.c.artifact_id).where(table.c.phase == kind))
        if key is None:
            require(not required, "OPENFOOTBALL_PREREQUISITE_MISSING:" + kind)
            return None
        return self._load(session, key, kind)

    def _prior(self, session, request_key, request, kind):
        require(isinstance(request_key, str) and 0 < len(request_key) <= 160 and request_key == request_key.strip(), "REQUEST_KEY_REQUIRED")
        digest = tagged_canonical_sha256("OPENFOOTBALL_OPERATION_REQUEST_V1", [self.operator_id, kind, request])
        table = self._table("ofp_operations")
        row = session.execute(select(table).where(table.c.request_key == request_key)).mappings().one_or_none()
        if row:
            require(row["request_hash"] == digest, "EXACT_RETRY_REQUEST_CONFLICT")
            return self._load(session, row["artifact_id"], kind), digest
        require(self._phase(session, kind, False) is None, "PHASE_ALREADY_RECORDED_USE_EXACT_RETRY")
        return None, digest

    def _put(self, session, *, kind, payload, parents=(), request_key=None, request_hash=None, recorded_at=None):
        artifact = OpenFootballProductionArtifactV1.freeze(kind=kind, payload=payload,
            parents=tuple(p.reference() for p in parents), recorded_at_utc=recorded_at or self._now())
        session.execute(self._table("ofp_artifacts").insert().values(artifact_id=artifact.artifact_id,
            artifact_hash=artifact.artifact_hash, kind=kind, recorded_at_utc=artifact.model_dump(mode="json")["recorded_at_utc"],
            artifact_json=canonical_json(artifact)))
        for parent_id, parent_hash in artifact.parents:
            session.execute(self._table("ofp_parent_links").insert().values(child_id=artifact.artifact_id, parent_id=parent_id, parent_hash=parent_hash))
        session.execute(self._table("ofp_phase_slots").insert().values(phase=kind, artifact_id=artifact.artifact_id))
        if request_key is not None:
            session.execute(self._table("ofp_operations").insert().values(request_key=request_key, request_hash=request_hash, artifact_id=artifact.artifact_id))
        return self._load(session, artifact.artifact_id, kind)

    def _review(self, *, schema, digest, review, authority, at):
        review = LocalReviewEvidenceV1.model_validate(review)
        authority = LocalReviewEvidenceV1.model_validate(authority)
        result = self.evidence.review(evidence=review, authority=authority, schema=schema, digest=digest,
            source_ids=(SOURCE_ID,), operator_id=self.operator_id, at_utc=at)
        permission = self.evidence.load_authority(authority)
        final = self._now()
        require(final >= at, "REVIEW_CLOCK_MOVED_BACKWARDS")
        permission.assert_active_for(final)
        return result

    def prepare_data_binding(self, *, policy, mapping, rights_payload, directive_evidence):
        subject = prepare_openfootball_binding(evidence=self.evidence, policy=policy, mapping=mapping,
            rights_payload=rights_payload, directive_evidence=LocalReviewEvidenceV1.model_validate(directive_evidence))
        database = self._sessions.kw["bind"].url.database
        if database in (None, "", ":memory:"):
            subject["storage_binding"] = dict(kind="ISOLATED_MEMORY_TEST_CONTEXT", connection_identity=id(self._sessions.kw["bind"]))
        else:
            path = Path(database).resolve(strict=True)
            info = path.stat()
            subject["storage_binding"] = dict(kind="EXISTING_SQLITE_FILE", path=str(path), device=info.st_dev, file_identity=info.st_ino)
        return subject

    def _check_subject(self, subject):
        mapping = OpenFootballCanonicalReviewV1.model_validate(subject["mapping"])
        expected = self.prepare_data_binding(policy=OpenFootballAdapterPolicyV1.model_validate(subject["preparation_scope"]["adapter_policy"]),
            mapping=mapping, rights_payload=subject["rights_payload"], directive_evidence=subject["user_directive"])
        require(canonical_json(expected) == canonical_json(subject), "SOURCE_SCOPE_MAPPING_OR_BYTES_CHANGED")
        return mapping

    def record_data_binding(self, *, request_key, subject, review, authority):
        """Exact externally reviewed 612/13/599 subject; one atomic admission."""
        mapping = self._check_subject(subject)
        subject_hash = tagged_canonical_sha256("OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", subject)
        request = dict(subject_hash=subject_hash, review=review, authority=authority)
        with self._transaction(True) as session:
            at = self._now()
            reviewed = self._review(schema="OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", digest=subject_hash, review=review, authority=authority, at=at)
            # Technical mapping review and human source authorization are separate
            # provenance roles. The exact human review covers the entire mapping.
            require(all(e.reviewed_at <= reviewed.reviewed_at_utc for e in mapping.entries), "CANONICAL_MAPPING_REVIEW_TIMELINE_MISMATCH")
            self._assert_rights(subject, at)
            prior, digest = self._prior(session, request_key, request, "DATA_BINDING")
            if prior:
                self._data(session)
                return prior
            admitted = self._now()
            facts = facts_at_admission(subject, admitted)
            payload = dict(subject=subject, subject_hash=subject_hash, review=review, authority=authority,
                facts=[f.model_dump(mode="json") for f in facts], facts_root=facts_root(facts),
                admitted_fact_count=599, source_record_count=612, exception_count=13,
                source_data_mode="SOURCE_TIME_RESEARCH", retrospective=True, evidence_basis="CURRENT_SNAPSHOT_OBSERVED",
                provider_publication_at_utc=None, provider_finalized_at_utc=None)
            artifact = self._put(session, kind="DATA_BINDING", payload=payload, request_key=request_key, request_hash=digest, recorded_at=admitted)
            self._register_catalog(session, mapping, artifact)
            self._register_records(session, subject, facts, artifact)
            self._data(session, complete=False)
            completion = self._put(session, kind="DATA_COMPLETION", payload=dict(admitted_fact_count=599, source_record_count=612, exception_count=13), parents=(artifact,))
            self._current_review(artifact, self._now())
            require(completion.recorded_at_utc >= admitted, "ADMISSION_CLOCK_MOVED_BACKWARDS")
            return artifact

    def _assert_rights(self, subject, at):
        rights = OpenFootballSourceRightsPayloadV1.model_validate(subject["rights_payload"])
        require(rights.effective_at_utc <= at < rights.expires_at_utc, "SOURCE_RIGHTS_NOT_ACTIVE")

    def _current_review(self, binding, at):
        data = binding.payload
        self._assert_rights(data["subject"], at)
        self._review(schema="OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", digest=data["subject_hash"],
            review=data["review"], authority=data["authority"], at=at)
        self._assert_rights(data["subject"], self._now())

    def _register_catalog(self, session, mapping, artifact):
        table = self._table("ofp_canonical_entities")
        for entry in mapping.entries:
            if entry.kind == "TEAM":
                require(session.get(TeamRecord, entry.canonical_id) is None, "CANONICAL_ID_ALREADY_EXISTS_REVIEW_REQUIRED")
                session.add(TeamRecord(team_id=entry.canonical_id, canonical_key="ofp-team:" + entry.canonical_id,
                    name=entry.source_label, team_type="CLUB"))
            elif entry.kind == "COMPETITION":
                require(session.get(CompetitionRecord, entry.canonical_id) is None, "CANONICAL_COMPETITION_ALREADY_EXISTS_REVIEW_REQUIRED")
                session.add(CompetitionRecord(competition_id=entry.canonical_id, canonical_key="ofp-bundesliga:" + entry.canonical_id,
                    name="Bundesliga", country_code="DE"))
            session.execute(table.insert().values(canonical_id=entry.canonical_id, kind=entry.kind,
                source_label=entry.source_label, entry_hash=entry.content_hash, entry_json=canonical_json(entry), binding_id=artifact.artifact_id))
        provider_id = stable_id("provider", "OPENFOOTBALL")
        require(session.get(ProviderRecord, provider_id) is None, "OPENFOOTBALL_PROVIDER_ALREADY_EXISTS_REVIEW_REQUIRED")
        session.add(ProviderRecord(provider_id=provider_id, code="OPENFOOTBALL", name="OpenFootball CC0 observed snapshot", provider_kind="HISTORICAL_ARCHIVE"))
        session.flush()

    def _register_records(self, session, subject, facts, artifact):
        by_pointer = {(f.source_filename, f.record_pointer): f for f in facts}
        provider_id = stable_id("provider", "OPENFOOTBALL")
        at = artifact.recorded_at_utc
        for record in subject["source_records"]:
            row, name, pointer = record["inspection"], record["source_filename"], record["record_pointer"]
            mid = record["canonical_match_id"]
            require(session.get(MatchRecord, mid) is None, "CANONICAL_MATCH_ALREADY_EXISTS_REVIEW_REQUIRED")
            session.add(MatchRecord(internal_match_id=mid, competition_id=canonical_id("COMPETITION", "Deutsche Bundesliga"),
                home_team_id=canonical_id("TEAM", row["source_team1"]), away_team_id=canonical_id("TEAM", row["source_team2"]),
                kickoff_at_utc=parse_utc(row["kickoff_at_utc"]), status="FINISHED" if record["included"] else "UNKNOWN",
                available_at_utc=at, created_at_utc=at, fixture_ingestion_id=None))
            session.flush()
            session.add(CanonicalMatchIdentityRecord(internal_match_id=mid, season=canonical_id("SEASON", row["season"]),
                competition_type="DOMESTIC_LEAGUE", available_at_utc=at, fixture_ingestion_id=None))
            mapping_id = stable_id("OPENFOOTBALL_RECORD_MAPPING_V1", mid)
            session.add(ProviderMatchMappingRecord(mapping_id=mapping_id, provider_id=provider_id,
                external_namespace="PINNED_JSON_RECORD_POINTER", external_match_id=name + "#" + pointer,
                internal_match_id=mid, resolution_method="EXPLICIT", confidence=1, available_at_utc=at,
                fixture_ingestion_id=None, supersedes_mapping_id=None))
            session.flush()
            fact = by_pointer.get((name, pointer))
            if fact:
                result = fact.to_elo_result()
                session.add(MatchResultRecord(match_result_id=result.match_result_id, internal_match_id=mid,
                    provider_id=provider_id, provider_mapping_id=mapping_id, home_goals=result.home_goals, away_goals=result.away_goals,
                    observed_at_utc=fact.capture_at_utc, available_at_utc=at, ingested_at_utc=at,
                    source_result_key=name + "#" + pointer, payload_hash=result.payload_hash, supersedes_match_result_id=None))
            session.execute(self._table("ofp_source_records").insert().values(source_filename=name, record_pointer=pointer,
                binding_id=artifact.artifact_id, source_file_sha256=row["provenance"]["source_file_sha256"],
                record_sha256=row["provenance"]["original_record_sha256"], record_json=canonical_json(record["original_record"]),
                canonical_match_id=mid, included=record["included"], exception_reason=record["exception_reason"], fact_json=canonical_json(fact) if fact else None))
        session.flush()

    def _data(self, session, complete=True):
        binding = self._phase(session, "DATA_BINDING")
        require((binding.payload["source_record_count"], binding.payload["exception_count"], binding.payload["admitted_fact_count"]) == (612,13,599), "BINDING_COUNTS_CHANGED")
        self._current_review(binding, self._now())
        mapping = self._check_subject(binding.payload["subject"])
        expected_facts = facts_at_admission(binding.payload["subject"], binding.recorded_at_utc)
        require(canonical_json(binding.payload["facts"]) == canonical_json(expected_facts) and binding.payload["facts_root"] == facts_root(expected_facts), "OBSERVED_FACT_ROOT_MISMATCH")
        rows = session.execute(select(self._table("ofp_source_records"))).mappings().all()
        require(len(rows) == 612 and sum(r["included"] for r in rows) == 599, "PERSISTED_COHORT_INCOMPLETE")
        by_pointer = {(r["source_filename"], r["record_pointer"]): r for r in rows}
        fact_map = {(f.source_filename, f.record_pointer): f for f in expected_facts}
        for original in binding.payload["subject"]["source_records"]:
            key = original["source_filename"], original["record_pointer"]
            row, fact = by_pointer[key], fact_map.get(key)
            expected = dict(source_filename=key[0], record_pointer=key[1], binding_id=binding.artifact_id,
                source_file_sha256=original["inspection"]["provenance"]["source_file_sha256"],
                record_sha256=original["inspection"]["provenance"]["original_record_sha256"],
                record_json=canonical_json(original["original_record"]), canonical_match_id=original["canonical_match_id"],
                included=original["included"], exception_reason=original["exception_reason"], fact_json=canonical_json(fact) if fact else None)
            require(dict(row) == expected, "SOURCE_RECORD_OR_EXCEPTION_PROJECTION_MISMATCH")
            match = session.get(MatchRecord, row["canonical_match_id"])
            identity = session.get(CanonicalMatchIdentityRecord, row["canonical_match_id"])
            require(match and identity and (match.home_team_id, match.away_team_id, match.kickoff_at_utc, identity.season) ==
                (canonical_id("TEAM", original["inspection"]["source_team1"]), canonical_id("TEAM", original["inspection"]["source_team2"]),
                 parse_utc(original["inspection"]["kickoff_at_utc"]), canonical_id("SEASON", original["inspection"]["season"])), "CANONICAL_MATCH_PROJECTION_MISMATCH")
            require(match.competition_id == canonical_id("COMPETITION", "Deutsche Bundesliga") and identity.competition_type == "DOMESTIC_LEAGUE"
                and match.available_at_utc == binding.recorded_at_utc and identity.available_at_utc == binding.recorded_at_utc, "CANONICAL_SCOPE_OR_AVAILABILITY_MISMATCH")
            if fact:
                result = fact.to_elo_result()
                stored = session.get(MatchResultRecord, result.match_result_id)
                require(stored and (stored.internal_match_id, stored.home_goals, stored.away_goals, stored.available_at_utc, stored.ingested_at_utc, stored.payload_hash) ==
                    (fact.match_id, fact.home_goals, fact.away_goals, fact.admitted_at_utc, fact.admitted_at_utc, result.payload_hash), "NORMALIZED_OBSERVED_RESULT_MISMATCH")
                require((stored.provider_id, stored.provider_mapping_id, stored.source_result_key, stored.observed_at_utc, stored.supersedes_match_result_id) ==
                    (stable_id("provider", "OPENFOOTBALL"), stable_id("OPENFOOTBALL_RECORD_MAPPING_V1", fact.match_id),
                     fact.source_filename + "#" + fact.record_pointer, fact.capture_at_utc, None), "NORMALIZED_RESULT_LINEAGE_MISMATCH")
            provider_mapping = session.get(ProviderMatchMappingRecord, stable_id("OPENFOOTBALL_RECORD_MAPPING_V1", row["canonical_match_id"]))
            require(provider_mapping and (provider_mapping.provider_id, provider_mapping.external_namespace, provider_mapping.external_match_id,
                provider_mapping.internal_match_id, provider_mapping.resolution_method, provider_mapping.confidence, provider_mapping.available_at_utc) ==
                (stable_id("provider", "OPENFOOTBALL"), "PINNED_JSON_RECORD_POINTER", key[0] + "#" + key[1], row["canonical_match_id"], "EXPLICIT", 1, binding.recorded_at_utc), "SOURCE_MAPPING_PROJECTION_MISMATCH")
        entries = session.execute(select(self._table("ofp_canonical_entities"))).mappings().all()
        require(len(entries) == 24, "CANONICAL_CATALOG_INCOMPLETE")
        actual_entries = {e["canonical_id"]: e for e in entries}
        for entry in mapping.entries:
            require(dict(actual_entries[entry.canonical_id]) == dict(canonical_id=entry.canonical_id, kind=entry.kind,
                source_label=entry.source_label, entry_hash=entry.content_hash, entry_json=canonical_json(entry), binding_id=binding.artifact_id), "CANONICAL_ENTRY_REVIEW_MISMATCH")
            if entry.kind == "TEAM":
                team = session.get(TeamRecord, entry.canonical_id)
                require(team and team.name == entry.source_label and team.team_type == "CLUB", "CANONICAL_TEAM_CHANGED")
            elif entry.kind == "COMPETITION":
                competition = session.get(CompetitionRecord, entry.canonical_id)
                require(competition and competition.name == "Bundesliga" and competition.country_code == "DE", "CANONICAL_COMPETITION_CHANGED")
        result_ids = set(session.scalars(select(MatchResultRecord.match_result_id).where(MatchResultRecord.provider_id == stable_id("provider", "OPENFOOTBALL"))))
        require(result_ids == {f.to_elo_result().match_result_id for f in expected_facts}, "OPENFOOTBALL_RESULT_SET_CHANGED")
        provider = session.get(ProviderRecord, stable_id("provider", "OPENFOOTBALL"))
        require(provider and provider.code == "OPENFOOTBALL" and provider.provider_kind == "HISTORICAL_ARCHIVE", "SOURCE_PROVIDER_CHANGED")
        if complete:
            completion = self._phase(session, "DATA_COMPLETION")
            require(completion.parents == (binding.reference(),), "ADMISSION_COMPLETION_PARENT_MISMATCH")
        self._current_review(binding, self._now())
        return binding, expected_facts

    def seal_cutoff(self, request_key):
        with self._transaction(True) as session:
            binding, facts = self._data(session)
            completion = self._phase(session, "DATA_COMPLETION")
            prior, digest = self._prior(session, request_key, dict(binding=binding.reference(), completion=completion.reference()), "CUTOFF")
            if prior:
                return prior
            cutoff = self._now()
            require(completion.recorded_at_utc < cutoff and all(f.admitted_at_utc < cutoff for f in facts), "TRUSTED_CUTOFF_MUST_FOLLOW_COMPLETED_ADMISSION")
            value = self._put(session, kind="CUTOFF", payload=dict(selection_cutoff_at_utc=cutoff.isoformat(), training_cutoff_at_utc=cutoff.isoformat(),
                clock_basis="REPOSITORY_TRUSTED_UTC", admission_completed_at_utc=completion.recorded_at_utc.isoformat()),
                parents=(binding, completion), request_key=request_key, request_hash=digest, recorded_at=cutoff)
            self._current_review(binding, self._now())
            return value

    def seal_pilot_plan(self, *, request_key, targets):
        """Requires explicit future target identities before fixing exclusions."""
        targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in targets)
        require(targets, "FUTURE_TARGET_SCOPE_REQUIRED")
        require(tuple(t.match_id for t in targets) == tuple(sorted({t.match_id for t in targets})), "COMPLETE_SORTED_TARGET_SET_REQUIRED")
        with self._transaction(True) as session:
            binding, facts = self._data(session)
            cutoff_artifact = self._phase(session, "CUTOFF")
            cutoff = parse_utc(cutoff_artifact.payload["training_cutoff_at_utc"])
            self._validate_targets(session, targets, cutoff)
            excluded = tuple(t.match_id for t in targets)
            validate_fact_cohort(facts, window=fixed_window(), exclusions=excluded, cutoff=cutoff)
            payload = dict(schema_version="OPENFOOTBALL_OBSERVED_QUANT_INTEGRITY_PLAN_V1", bootstrap_id=BOOTSTRAP_ID,
                binding=binding.reference(), facts_root=binding.payload["facts_root"], fact_count=599, source_record_count=612,
                exceptions=list(fixed_exceptions()), training_window=fixed_window(), training_window_hash=fixed_window().content_hash,
                training_cutoff_at_utc=cutoff.isoformat(), targets=[t.model_dump(mode="json") for t in targets], exclude_match_ids=excluded,
                config_hash=CONFIG_HASH, model_name="ELO_THREE_WAY_BASELINE_V1", model_version="1",
                parameter_policy="NO_PARAMETER_TUNING", selection_policy="NO_ROI_MODEL_SELECTION", implementation_revision=_code_revision(),
                recipe="OPENFOOTBALL_OBSERVED_STRUCTURAL_REPLAY_V1", evidence_basis="CURRENT_SNAPSHOT_OBSERVED",
                source_data_mode="SOURCE_TIME_RESEARCH", retrospective=True)
            prior, digest = self._prior(session, request_key, payload, "PILOT_PLAN")
            if prior:
                return prior
            value = self._put(session, kind="PILOT_PLAN", payload=payload, parents=(binding, cutoff_artifact), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            return value

    def _validate_targets(self, session, targets, cutoff):
        for target in targets:
            require(target.competition_id == canonical_id("COMPETITION", "Deutsche Bundesliga") and target.season_id == canonical_id("SEASON", "2026/27")
                and target.kickoff_at_utc > cutoff and target.home_team_id != target.away_team_id, "FUTURE_BUNDESLIGA_TARGET_REQUIRED")
            match = session.get(MatchRecord, target.match_id)
            identity = session.get(CanonicalMatchIdentityRecord, target.match_id)
            require(match and identity and (match.competition_id, identity.season, match.home_team_id, match.away_team_id, match.kickoff_at_utc) ==
                (target.competition_id, target.season_id, target.home_team_id, target.away_team_id, target.kickoff_at_utc), "CANONICAL_FUTURE_TARGET_NOT_REGISTERED")
            require(match.available_at_utc <= self._now(), "FUTURE_TARGET_IDENTITY_NOT_AVAILABLE")
            require(match.status == "SCHEDULED" and not session.scalar(select(MatchResultRecord.match_result_id).where(
                MatchResultRecord.internal_match_id == target.match_id).limit(1)), "TARGET_NOT_FUTURE_UNRESULTED")

    def _plan(self, session):
        binding, facts = self._data(session)
        cutoff_artifact = self._phase(session, "CUTOFF")
        plan = self._phase(session, "PILOT_PLAN")
        p = plan.payload
        cutoff = parse_utc(p["training_cutoff_at_utc"])
        require(cutoff_artifact.payload["training_cutoff_at_utc"] == p["training_cutoff_at_utc"] and
            p["facts_root"] == binding.payload["facts_root"] and p["config_hash"] == CONFIG_HASH and
            p["implementation_revision"] == _code_revision() and p["parameter_policy"] == "NO_PARAMETER_TUNING" and
            p["selection_policy"] == "NO_ROI_MODEL_SELECTION" and p["training_window_hash"] == fixed_window().content_hash and
            canonical_json(p["exceptions"]) == canonical_json(fixed_exceptions()), "PILOT_PLAN_SCOPE_OR_IMPLEMENTATION_CHANGED")
        targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in p["targets"])
        require(targets and tuple(p["exclude_match_ids"]) == tuple(t.match_id for t in targets), "ALL_OPERATION_TARGET_IDS_MUST_BE_EXCLUDED")
        self._validate_targets(session, targets, cutoff)
        require(plan.parents == tuple(sorted((binding.reference(), cutoff_artifact.reference()))), "PILOT_PLAN_PARENT_MISMATCH")
        validate_fact_cohort(facts, window=fixed_window(), exclusions=p["exclude_match_ids"], cutoff=cutoff)
        return binding, facts, plan, cutoff

    def run_pilot(self, *, request_key):
        with self._transaction(True) as session:
            binding, facts, plan, cutoff = self._plan(session)
            prior, digest = self._prior(session, request_key, dict(plan=plan.reference()), "PILOT_REPORT")
            if prior:
                self._verify_report(prior, facts, plan, cutoff)
                return prior
            require(self._phase(session, "PILOT_RESERVATION", False) is None, "PILOT_ALREADY_RESERVED_RESOLVE_COMPLETION_NOT_NEW_ATTEMPT")
            reservation = self._put(session, kind="PILOT_RESERVATION", payload=dict(plan=plan.reference(), request_key=request_key,
                request_hash=digest, attempt_sequence=1), parents=(plan,))
            self._current_review(binding, self._now())
        try:
            result = structural_replay(facts, cutoff=cutoff, excluded_match_ids=tuple(plan.payload["exclude_match_ids"]))
            with self._transaction(True) as session:
                binding, current_facts, current_plan, current_cutoff = self._plan(session)
                require(current_plan == plan and current_facts == facts and current_cutoff == cutoff, "PILOT_SOURCE_CHANGED_DURING_RUN")
                report = self._put(session, kind="PILOT_REPORT", payload=dict(result, plan=plan.reference(), reservation=reservation.reference(),
                    facts_root=facts_root(facts), config_hash=CONFIG_HASH, training_window_hash=fixed_window().content_hash,
                    implementation_revision=_code_revision()), parents=(plan, reservation), request_key=request_key, request_hash=digest)
                self._current_review(binding, self._now())
                return report
        except Exception as error:
            with self._transaction(True) as session:
                if self._phase(session, "PILOT_REPORT", False) is None and self._phase(session, "PILOT_FAILURE", False) is None:
                    self._put(session, kind="PILOT_FAILURE", payload=dict(error_type=type(error).__name__, attempt_sequence=1), parents=(plan, reservation))
            raise

    def _verify_report(self, report, facts, plan, cutoff):
        expected = structural_replay(facts, cutoff=cutoff, excluded_match_ids=tuple(plan.payload["exclude_match_ids"]))
        require(all(canonical_json(report.payload.get(k)) == canonical_json(v) for k, v in expected.items()), "PILOT_REPORT_REPLAY_MISMATCH")
        require(report.payload["facts_root"] == facts_root(facts) and tuple(report.payload["plan"]) == plan.reference(), "PILOT_REPORT_BINDING_MISMATCH")

    def attest_pilot(self, request_key):
        with self._transaction(True) as session:
            binding, facts, plan, cutoff = self._plan(session)
            report = self._phase(session, "PILOT_REPORT")
            self._verify_report(report, facts, plan, cutoff)
            require(self._phase(session, "PILOT_FAILURE", False) is None, "FAILED_PILOT_CANNOT_BE_ATTESTED")
            payload = dict(plan=plan.reference(), report=report.reference(), attempt_count=1, facts_root=facts_root(facts),
                state_hash=report.payload["state_hash"], training_data_hash=report.payload["training_data_hash"],
                purpose="OBSERVED_STRUCTURAL_REPLAY_ONLY_NOT_APPROVAL")
            prior, digest = self._prior(session, request_key, payload, "PILOT_ATTESTATION")
            if prior:
                return prior
            result = self._put(session, kind="PILOT_ATTESTATION", payload=payload, parents=(plan, report), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            return result

    def create_manifest(self, request_key):
        with self._transaction(True) as session:
            binding, facts, plan, cutoff = self._plan(session)
            report, attestation = self._phase(session, "PILOT_REPORT"), self._phase(session, "PILOT_ATTESTATION")
            self._verify_report(report, facts, plan, cutoff)
            require(tuple(attestation.payload["report"]) == report.reference(), "ATTESTATION_REPORT_MISMATCH")
            payload = dict(schema_version="OPENFOOTBALL_TRAINING_HISTORY_MANIFEST_V1", source_binding=binding.reference(),
                source_record_count=612, exception_count=13, fact_count=599, facts_root=facts_root(facts),
                canonical_mapping_root=binding.payload["subject"]["mapping_root"],
                training_window=fixed_window(), training_window_hash=fixed_window().content_hash, training_cutoff_at_utc=cutoff.isoformat(),
                exclude_match_ids=plan.payload["exclude_match_ids"], config_hash=CONFIG_HASH, implementation_revision=_code_revision(),
                pilot_plan=plan.reference(), pilot_report=report.reference(), attestation=attestation.reference(),
                training_data_hash=report.payload["training_data_hash"], state_hash=report.payload["state_hash"],
                historical_validation=report.payload["historical_validation"], source_data_mode="SOURCE_TIME_RESEARCH", retrospective=True,
                evidence_basis="CURRENT_SNAPSHOT_OBSERVED")
            prior, digest = self._prior(session, request_key, payload, "MANIFEST")
            if prior:
                return prior
            result = self._put(session, kind="MANIFEST", payload=payload, parents=(binding, plan, report, attestation), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            return result

    def _manifest(self, session):
        binding, facts, plan, cutoff = self._plan(session)
        report, attestation, manifest = (self._phase(session, k) for k in ("PILOT_REPORT", "PILOT_ATTESTATION", "MANIFEST"))
        self._verify_report(report, facts, plan, cutoff)
        m = manifest.payload
        require((m["source_record_count"], m["exception_count"], m["fact_count"]) == (612,13,599) and
            m["facts_root"] == facts_root(facts) and m["training_window_hash"] == fixed_window().content_hash and
            m["state_hash"] == report.payload["state_hash"] and m["training_data_hash"] == report.payload["training_data_hash"] and
            tuple(m["attestation"]) == attestation.reference() and tuple(m["pilot_report"]) == report.reference() and
            manifest.parents == tuple(sorted(x.reference() for x in (binding, plan, report, attestation))), "MANIFEST_COMPLETENESS_OR_INTEGRITY_MISMATCH")
        return binding, facts, plan, report, attestation, manifest

    def prepare_approval(self, *, effective_at_utc, expires_at_utc):
        with self._transaction() as session:
            binding, _, _, report, attestation, manifest = self._manifest(session)
            m = manifest.payload
            return OpenFootballModelApprovalPayloadV1(manifest_id=manifest.artifact_id, manifest_hash=manifest.artifact_hash,
                pilot_report_id=report.artifact_id, pilot_report_hash=report.artifact_hash,
                attestation_id=attestation.artifact_id, attestation_hash=attestation.artifact_hash,
                source_binding_id=binding.artifact_id, source_binding_hash=binding.artifact_hash,
                canonical_mapping_root=m["canonical_mapping_root"], training_window_hash=m["training_window_hash"],
                training_cutoff_at_utc=m["training_cutoff_at_utc"], training_data_hash=m["training_data_hash"], state_hash=m["state_hash"],
                implementation_revision=_code_revision(), effective_at_utc=effective_at_utc, expires_at_utc=expires_at_utc,
                derived_state_retention="INDEFINITE_PRIVATE_PERSONAL_SYSTEM", audit_retention="INDEFINITE_PRIVATE_PERSONAL_SYSTEM")

    def _approval_subject(self, session, payload):
        payload = OpenFootballModelApprovalPayloadV1.model_validate(payload)
        binding, facts, plan, report, attestation, manifest = self._manifest(session)
        require((payload.manifest_id, payload.manifest_hash) == manifest.reference() and
            (payload.source_binding_id, payload.source_binding_hash) == binding.reference() and
            (payload.pilot_report_id, payload.pilot_report_hash) == report.reference() and
            (payload.attestation_id, payload.attestation_hash) == attestation.reference() and
            payload.canonical_mapping_root == manifest.payload["canonical_mapping_root"] and
            payload.training_data_hash == manifest.payload["training_data_hash"] and payload.state_hash == manifest.payload["state_hash"] and
            payload.training_cutoff_at_utc == parse_utc(manifest.payload["training_cutoff_at_utc"]) and
            payload.implementation_revision == _code_revision(), "APPROVAL_DOES_NOT_BIND_EXACT_MANIFEST")
        return payload, binding, facts, plan, report, manifest

    def record_approval(self, *, request_key, payload, review, authority):
        with self._transaction(True) as session:
            payload, binding, _, _, _, manifest = self._approval_subject(session, payload)
            at = self._now()
            require(payload.effective_at_utc <= at < payload.expires_at_utc, "MODEL_APPROVAL_NOT_ACTIVE")
            reviewed = self._review(schema=payload.schema_version, digest=payload.content_hash, review=review, authority=authority, at=at)
            require(reviewed.reviewed_at_utc >= manifest.recorded_at_utc, "MODEL_APPROVAL_MUST_FOLLOW_MANIFEST")
            body = dict(subject=payload.model_dump(mode="json"), subject_hash=payload.content_hash, review=review, authority=authority,
                reviewed_by=reviewed.authorized_reviewer, reviewed_at_utc=reviewed.reviewed_at_utc)
            prior, digest = self._prior(session, request_key, body, "APPROVAL")
            if prior:
                return prior
            value = self._put(session, kind="APPROVAL", payload=body, parents=(binding, manifest), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            self._model_authorization(value, self._now())
            return value

    def _model_authorization(self, approval, at):
        payload = OpenFootballModelApprovalPayloadV1.model_validate(approval.payload["subject"])
        require(payload.content_hash == approval.payload["subject_hash"] and payload.effective_at_utc <= at < payload.expires_at_utc, "MODEL_APPROVAL_HASH_OR_VALIDITY_MISMATCH")
        self._review(schema=payload.schema_version, digest=payload.content_hash, review=approval.payload["review"], authority=approval.payload["authority"], at=at)
        require(payload.effective_at_utc <= self._now() < payload.expires_at_utc, "MODEL_APPROVAL_EXPIRED_DURING_VERIFICATION")
        return payload

    def build_release(self, request_key):
        with self._transaction(True) as session:
            approval = self._phase(session, "APPROVAL")
            approved = self._model_authorization(approval, self._now())
            _, binding, facts, plan, report, manifest = self._approval_subject(session, approved)
            prior, digest = self._prior(session, request_key, dict(approval=approval.reference()), "RELEASE")
            if prior:
                self._release(session)
                return prior
            started = self._now()
            require(approval.recorded_at_utc <= started and approved.training_cutoff_at_utc < started, "RELEASE_BUILD_TIMELINE_INVALID")
            replay = structural_replay(facts, cutoff=approved.training_cutoff_at_utc, excluded_match_ids=tuple(plan.payload["exclude_match_ids"]))
            require(replay["state_hash"] == approved.state_hash and replay["training_data_hash"] == approved.training_data_hash, "RELEASE_STATE_REPLAY_MISMATCH")
            result = self._put(session, kind="RELEASE", payload=dict(schema_version="OPENFOOTBALL_PRODUCTION_MODEL_RELEASE_V1",
                model_name="ELO_THREE_WAY_BASELINE_V1", model_version="1", config_hash=CONFIG_HASH, state=replay["state"],
                training_data_hash=replay["training_data_hash"], state_hash=replay["state_hash"], approval=approval.reference(),
                manifest=manifest.reference(), training_cutoff_at_utc=approved.training_cutoff_at_utc.isoformat(),
                build_started_at_utc=started.isoformat(), build_completed_at_utc=self._now().isoformat(),
                raw_records_retention=approved.raw_records_retention, derived_state_retention=approved.derived_state_retention,
                audit_retention=approved.audit_retention, implementation_revision=_code_revision()),
                parents=(approval, manifest, report), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            self._model_authorization(approval, self._now())
            return result

    def _release(self, session):
        release, approval = self._phase(session, "RELEASE"), self._phase(session, "APPROVAL")
        approved = self._model_authorization(approval, self._now())
        _, binding, facts, plan, report, manifest = self._approval_subject(session, approved)
        require(release.parents == tuple(sorted(x.reference() for x in (approval, manifest, report))) and
            release.payload["state_hash"] == approved.state_hash and release.payload["training_data_hash"] == approved.training_data_hash and
            release.payload["config_hash"] == CONFIG_HASH and canonical_json(release.payload["state"]) == canonical_json(report.payload["state"]), "PRODUCTION_RELEASE_INTEGRITY_MISMATCH")
        return release, approval, approved, plan, binding

    def _live_target_proof(self, session, targets, at):
        """Verify existing complete live input bundles; never creates preparation."""
        from football_system.infrastructure.database.live_source_repositories import _preparation_from_record, _load_prepared_bundle
        from football_system.infrastructure.database.real_bridge_sources import market_values, sp_values
        refs = []
        for pid in sorted({t.live_preparation_id for t in targets}):
            stored = session.get(LiveAnalysisPreparationRecord, pid)
            require(stored is not None, "COMPLETE_LIVE_TARGET_INPUTS_REQUIRED")
            preparation = _preparation_from_record(stored)
            expected = tuple(sorted(t.match_id for t in targets if t.live_preparation_id == pid))
            require(not stored.allow_partial_inputs and stored.status == "ANALYSIS_INPUT_READY" and
                stored.match_count == stored.ready_match_count == len(expected) and
                tuple(sorted(preparation.ready_match_ids)) == expected and
                stored.competition_id == canonical_id("COMPETITION", "Deutsche Bundesliga") and
                stored.season_id == canonical_id("SEASON", "2026/27") and stored.created_at_utc <= at,
                "TARGET_INPUT_COHORT_MUST_BE_COMPLETE")
            rows = tuple(session.scalars(select(LiveAnalysisPreparationMatchRecord).where(
                LiveAnalysisPreparationMatchRecord.preparation_id == pid).order_by(LiveAnalysisPreparationMatchRecord.match_no)))
            bundle = _load_prepared_bundle(session, preparation, rows)
            require({m.match_id for m in bundle.fixtures.matches} == set(expected), "TARGET_SCOPE_MISMATCH")
            for match in bundle.fixtures.matches:
                target = next(t for t in targets if t.match_id == match.match_id)
                require(match.kickoff_at_utc == target.kickoff_at_utc and match.status.value == "SCHEDULED", "TARGET_OBSERVATION_CHANGED")
            for row in rows:
                target = next(t for t in targets if t.match_id == row.internal_match_id)
                require(row.fixture_observation_id == target.fixture_observation_id, "TARGET_FIXTURE_REF_MISMATCH")
                market = market_values(session, self._sessions, row.market_ingestion_id, row.market_consensus_snapshot_id)
                sp = sp_values(session, self._sessions, row.sporttery_ingestion_id, row.sporttery_bonus_snapshot_id)
                require(market["match_id"] == sp["match_id"] == target.match_id and
                    max(market["available_at_utc"], market["ingested_at_utc"], sp["available_at_utc"], sp["ingested_at_utc"]) <= at and
                    0 <= (at-market["captured_at_utc"]).total_seconds() <= stored.maximum_odds_age_seconds and
                    len(market["constituent_refs"]) >= stored.minimum_bookmaker_count, "TARGET_LIVE_INPUT_STALE_OR_INCOMPLETE")
            refs.append(dict(preparation_id=pid, report_hash=stored.report_hash, match_ids=expected,
                input_cutoff_at_utc=stored.decision_as_of_at_utc.isoformat()))
        return refs

    def create_target_plan(self, request_key):
        with self._transaction(True) as session:
            release, approval, approved, pilot, binding = self._release(session)
            targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in pilot.payload["targets"])
            at = self._now()
            require(release.recorded_at_utc < at < min(t.kickoff_at_utc for t in targets), "TARGET_PLAN_MUST_BE_FUTURE_AFTER_RELEASE")
            self._validate_targets(session, targets, at)
            proof = self._live_target_proof(session, targets, at)
            require(set(t.match_id for t in targets) == set(pilot.payload["exclude_match_ids"]), "ALL_TARGET_EXCLUSIONS_REQUIRED")
            payload = dict(schema_version="OPENFOOTBALL_PRODUCTION_TARGET_ACCEPTANCE_PLAN_V1", release=release.reference(),
                targets=targets, live_inputs=proof, selection_rule="KICKOFF_WINDOW_COMPLETE_LIVE_INPUTS_MINIMUM_PRIOR_MATCHES_V1",
                minimum_prior_matches=5, unavailable_target_policy="RETAIN_MODEL_UNAVAILABLE", exclude_match_ids=pilot.payload["exclude_match_ids"],
                training_cutoff_at_utc=approved.training_cutoff_at_utc.isoformat())
            prior, digest = self._prior(session, request_key, payload, "TARGET_PLAN")
            if prior:
                return prior
            value = self._put(session, kind="TARGET_PLAN", payload=payload, parents=(release, pilot), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            self._model_authorization(approval, self._now())
            return value

    def bind_model_state(self, request_key):
        """Model-only analysis: all targets retained; no P_market/P_final/strategy."""
        with self._transaction(True) as session:
            release, approval, approved, pilot, binding = self._release(session)
            plan = self._phase(session, "TARGET_PLAN")
            targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in plan.payload["targets"])
            require(tuple(plan.payload["release"]) == release.reference() and
                canonical_json(plan.payload["targets"]) == canonical_json(pilot.payload["targets"]), "TARGET_PLAN_RELEASE_SCOPE_MISMATCH")
            at = self._now()
            require(plan.recorded_at_utc < at < min(t.kickoff_at_utc for t in targets), "MODEL_BINDING_MUST_PRECEDE_ALL_KICKOFFS")
            self._validate_targets(session, targets, at)
            require(canonical_json(self._live_target_proof(session, targets, at)) == canonical_json(plan.payload["live_inputs"]), "TARGET_LIVE_INPUT_PROOF_CHANGED")
            request = dict(release=release.reference(), target_plan=plan.reference())
            prior, digest = self._prior(session, request_key, request, "STATE_BINDING")
            if prior:
                self._verify_state_binding(prior, release, plan)
                return prior
            state = EloBaselineState.model_validate(release.payload["state"])
            source_analysis_id = stable_id("OPENFOOTBALL_MODEL_ONLY_ANALYSIS_V1", release.artifact_id, plan.artifact_id)
            model_state_id = stable_id("OPENFOOTBALL_PRODUCTION_STATE_BINDING_V1", source_analysis_id, state.state_hash)
            evaluations = self._target_evaluations(state, targets)
            require(len(evaluations) == len(targets), "UNAVAILABLE_TARGET_MUST_NOT_BE_REMOVED")
            authority_hash = tagged_canonical_sha256("OPENFOOTBALL_CURRENT_AUTHORITY_V1", dict(approval=approval.reference(),
                authority=approval.payload["authority"], review=approval.payload["review"], source_binding=binding.reference()))
            payload = dict(schema_version="OPENFOOTBALL_PRODUCTION_INFERENCE_STATE_BINDING_V1", source_analysis_id=source_analysis_id,
                model_state_id=model_state_id, analysis_kind="MODEL_ONLY_NOT_REAL_PROSPECTIVE", release=release.reference(),
                target_plan=plan.reference(), target_scope=[t.match_id for t in targets], config_hash=CONFIG_HASH,
                training_data_hash=state.training_data_hash, state_hash=state.state_hash, authority_hash=authority_hash,
                state=state, evaluations=evaluations, real_model_pin_created=False, real_performance_observations=0)
            result = self._put(session, kind="STATE_BINDING", payload=payload, parents=(release, plan, approval), request_key=request_key, request_hash=digest)
            self._current_review(binding, self._now())
            self._model_authorization(approval, self._now())
            require(self._now() < min(t.kickoff_at_utc for t in targets), "MODEL_BINDING_COMPLETED_TOO_LATE")
            return result

    def _target_evaluations(self, state, targets):
        model = EloThreeWayBaseline()
        values = []
        for target in targets:
            prediction = model.predict_from_state(EloPredictionRequest(match_id=target.match_id, season_id=target.season_id,
                home_team_id=target.home_team_id, away_team_id=target.away_team_id, kickoff_at_utc=target.kickoff_at_utc,
                cutoff_at_utc=state.cutoff_at_utc), state)
            values.append(dict(match_id=target.match_id, prediction=prediction.model_dump(mode="json")))
        return values

    def _verify_state_binding(self, artifact, release, plan):
        state = EloBaselineState.model_validate(artifact.payload["state"])
        targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in plan.payload["targets"])
        analysis_id = stable_id("OPENFOOTBALL_MODEL_ONLY_ANALYSIS_V1", release.artifact_id, plan.artifact_id)
        require(tuple(artifact.payload["release"]) == release.reference() and tuple(artifact.payload["target_plan"]) == plan.reference()
            and artifact.payload["config_hash"] == CONFIG_HASH and artifact.payload["state_hash"] == state.state_hash
            and artifact.payload["training_data_hash"] == state.training_data_hash and artifact.payload["real_model_pin_created"] is False
            and artifact.payload["real_performance_observations"] == 0, "MODEL_STATE_BINDING_HEADER_MISMATCH")
        require(artifact.payload["source_analysis_id"] == analysis_id and artifact.payload["model_state_id"] ==
            stable_id("OPENFOOTBALL_PRODUCTION_STATE_BINDING_V1", analysis_id, state.state_hash) and
            canonical_json(state) == canonical_json(release.payload["state"]) and
            artifact.payload["target_scope"] == [t.match_id for t in targets] and
            canonical_json(artifact.payload["evaluations"]) == canonical_json(self._target_evaluations(state, targets)), "MODEL_STATE_BINDING_REPLAY_MISMATCH")

    def load_model_pin_candidate(self):
        with self._transaction() as session:
            release, approval, _, _, binding = self._release(session)
            plan, artifact = self._phase(session, "TARGET_PLAN"), self._phase(session, "STATE_BINDING")
            self._verify_state_binding(artifact, release, plan)
            targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in plan.payload["targets"])
            current = self._now()
            self._validate_targets(session, targets, current)
            require(current < min(t.kickoff_at_utc for t in targets), "MODEL_PIN_TARGET_WINDOW_PASSED")
            require(canonical_json(self._live_target_proof(session, targets, current)) == canonical_json(plan.payload["live_inputs"]), "MODEL_PIN_LIVE_PROOF_CHANGED")
            expected_authority = tagged_canonical_sha256("OPENFOOTBALL_CURRENT_AUTHORITY_V1", dict(approval=approval.reference(),
                authority=approval.payload["authority"], review=approval.payload["review"], source_binding=binding.reference()))
            require(artifact.payload["authority_hash"] == expected_authority, "MODEL_AUTHORITY_HASH_MISMATCH")
            self._model_authorization(approval, self._now())
            return dict(status="PRODUCTION_MODEL_PIN_READY", release_id=release.artifact_id, release_hash=release.artifact_hash,
                target_plan_id=plan.artifact_id, target_plan_hash=plan.artifact_hash,
                **{k:artifact.payload[k] for k in ("model_state_id", "source_analysis_id", "config_hash", "training_data_hash", "state_hash", "authority_hash", "target_scope")},
                binding_id=artifact.artifact_id, binding_hash=artifact.artifact_hash, real_model_pin_created=False)

    def inspect(self, kind):
        with self._transaction() as session:
            if kind == "DATA_BINDING":
                return self._data(session)[0]
            if kind == "RELEASE":
                return self._release(session)[0]
            self._data(session)
            return self._phase(session, kind)
