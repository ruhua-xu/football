"""Actual scoped qualification/model persistence, using the trusted evidence boundary.

Candidate JSON and P1-A refs never constitute authority. All rights/reviews are
read from pinned local evidence; all admission/publication clocks are repository
clocks. V2 normalized facts never enter legacy match_results/training tables.
"""
from contextlib import contextmanager
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from threading import RLock

from sqlalchemy import select, text

from football_system.application.run_analysis import _code_revision
from football_system.domain.archive import canonical_json
from football_system.domain.common import normalize_utc, stable_id
from football_system.domain.openfootball_production import CONFIG_HASH, require
from football_system.domain.openfootball_scope import CompetitionScopeProfileV2, ScopeIdentityV2, validate_scope_source_bytes
from football_system.domain import openfootball_scoped as d
from football_system.domain.training_admission import LocalReviewEvidenceV1, tagged_canonical_sha256
from football_system.infrastructure.database.models import Base, CompetitionRecord, TeamRecord, MatchRecord, CanonicalMatchIdentityRecord, MatchResultRecord
from football_system.infrastructure.database.openfootball_scoped_schema import assert_scoped_head
from football_system.infrastructure.files.training_evidence import strict_json_bytes


class SqlAlchemyOpenFootballScopedRepository:
    def __init__(self, production):
        self.production = production
        self._sessions = production._sessions
        self.evidence = production.admission_repository.evidence
        self.operator_id = production.operator_id
        self._clock = production._now
        self._last = None
        self._lock = RLock()

    def _now(self):
        with self._lock:
            at = normalize_utc(self._clock())
            require(self._last is None or at >= self._last, "SCOPED_CLOCK_REGRESSION")
            self._last = at
            return at

    @contextmanager
    def _transaction(self, write=False):
        with self._sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE" if write else "BEGIN"))
            assert_scoped_head(session)
            yield session

    @staticmethod
    def _table(name):
        return Base.metadata.tables[name]

    def _storage(self, session, classification):
        database = self._sessions.kw["bind"].url.database
        if database in (None, "", ":memory:"):
            require(classification == "SYNTHETIC_SOFTWARE_ACCEPTANCE", "REAL_SCOPE_REQUIRES_FILE_DATABASE")
            return d.ScopedStorageBindingV2(kind="ISOLATED_SYNTHETIC_MEMORY", identity=(str(id(self._sessions.kw["bind"])),))
        path = Path(database).resolve(strict=True)
        stat = path.stat()
        return d.ScopedStorageBindingV2(kind="EXISTING_SQLITE_FILE", identity=(str(path), str(stat.st_dev), str(stat.st_ino),
            str(session.scalar(text("PRAGMA application_id")))))

    def _review(self, subject, review, authority, source_id, at):
        review, authority = LocalReviewEvidenceV1.model_validate(review), LocalReviewEvidenceV1.model_validate(authority)
        permission = self.evidence.load_authority(authority)
        permission.assert_active_for(at)
        result = self.evidence.review(evidence=review, authority=authority, schema=subject.schema_version,
            digest=subject.content_hash, source_ids=(source_id,), operator_id=self.operator_id, at_utc=at)
        self.evidence.load_authority(authority).assert_active_for(self._now())
        return result

    def _read_ref(self, ref):
        return self.evidence.read(ref.evidence_reference, ref.evidence_sha256)

    @staticmethod
    def _rights(subject, at):
        require(subject.rights.effective_at_utc <= at < subject.rights.expires_at_utc, "SCOPED_SOURCE_RIGHTS_NOT_ACTIVE")

    def _inputs(self, subject, at):
        from football_system.infrastructure.files.openfootball_evidence import private_evidence_directory
        private_evidence_directory(self.evidence.root)
        subject = d.ScopeQualificationSubjectV2.model_validate(subject)
        self._rights(subject, at)
        profile = subject.profile
        policy = d.ScopedParserPolicyV2.model_validate(strict_json_bytes(self._read_ref(subject.parser_policy)))
        require(policy.content_hash == profile.parser_policy_hash, "SCOPED_PARSER_POLICY_NOT_VERIFIED")
        acquisition = d.ScopedAcquisitionV2.model_validate(strict_json_bytes(self._read_ref(subject.acquisition)))
        require(acquisition.source_id == profile.source_id and acquisition.commit == profile.commit
            and acquisition.classification == subject.classification, "SCOPED_ACQUISITION_SCOPE_MISMATCH")
        require(tuple(f.path for f in acquisition.files) == tuple(f.path for f in profile.files), "SCOPED_COMPLETE_CAPTURE_MANIFEST_REQUIRED")
        raw_files = {}
        for capture, file in zip(acquisition.files, profile.files, strict=True):
            require((capture.bytes, capture.sha256, capture.captured_at_utc) == (file.bytes, file.sha256, file.captured_at_utc)
                and capture.captured_at_utc <= at, "SCOPED_CAPTURE_TIME_OR_HASH_MISMATCH")
            uri = f"https://raw.githubusercontent.com/{profile.repository}/{profile.commit}/{file.path}"
            if subject.classification == "SYNTHETIC_SOFTWARE_ACCEPTANCE":
                uri = f"synthetic://{profile.source_id}/{file.path}"
            require(capture.source_uri == uri, "SCOPED_CAPTURE_URI_MISMATCH")
            raw_files[file.path] = self.evidence.read(subject.source_prefix + "/" + file.path, file.sha256)
        import tzdata
        require(tzdata.__version__ == profile.time_policy.tzdata_version and tzdata.IANA_VERSION == profile.time_policy.iana_version,
            "SCOPED_PINNED_TZDATA_REQUIRED")
        zone_parts = profile.time_policy.timezone.split("/")
        require(all(p and p not in {".", ".."} and "\\" not in p and ":" not in p for p in zone_parts), "SCOPED_TIMEZONE_PATH_INVALID")
        tzif = files("tzdata.zoneinfo").joinpath(*zone_parts).read_bytes()
        validate_scope_source_bytes(profile, raw_files, tzif)
        refs = [profile.cohort_review, profile.time_policy.source_time_review, *(m.evidence for m in profile.mapping), *(r.review for r in profile.records)]
        for ref in set((r.evidence_id, r.evidence_sha256) for r in refs):
            self.evidence.read(*ref)
        rows = {}
        seasons = {s.season_id: s for s in profile.window}
        for file in profile.files:
            if file.role == "MATCHES":
                source = strict_json_bytes(raw_files[file.path])
                require(source["name"] == profile.source_competition_label + " " + seasons[file.season_id].source_label,
                    "SCOPED_SOURCE_COMPETITION_SEASON_TITLE_MISMATCH")
                rows.update({(file.path, i): row for i, row in enumerate(source["matches"])})
        for record in profile.records:
            row = rows[(record.file, record.index)]
            score = row.get("score")
            if row.get("status") not in (None, "finished"):
                reason = "ABNORMAL_MATCH_STATUS"
            elif isinstance(score, list):
                reason = "REPORTED_SCORE_PERIOD_UNPROVEN"
            elif not (isinstance(score, dict) and set(score) <= {"ft", "ht"} and isinstance(score.get("ft"), list)
                and len(score["ft"]) == 2 and all(type(goal) is int and goal >= 0 for goal in score["ft"])):
                reason = "MISSING_OR_INVALID_SCORE"
            else:
                reason = None
            require(record.reason == reason and (record.candidate_result is not None) == (reason is None), "SCOPED_UNJUSTIFIED_EXCEPTION_OR_FACT")
            if record.candidate_result is not None:
                require(record.candidate_result.ingested_at_utc <= at, "SCOPED_CANDIDATE_CLOCK_FROM_FUTURE")
        ordered = sorted((r.candidate_result for r in profile.records if r.candidate_result is not None), key=lambda f: (f.kickoff_at_utc, f.match_id))
        positions = {s.season_id: s.sequence for s in profile.window}
        order = [positions[f.season_id] for f in ordered]
        require(order == sorted(order), "SCOPED_FACT_SEASON_CHRONOLOGY_MISMATCH")
        self._rights(subject, self._now())
        return rows

    def _catalog_context(self, session, profile):
        result = []
        for entry in profile.mapping:
            require(len(entry.canonical_id) <= 160, "SCOPED_CANONICAL_IDENTIFIER_TOO_LONG")
            record = session.get(TeamRecord if entry.kind == "TEAM" else CompetitionRecord, entry.canonical_id) if entry.kind != "SEASON" else None
            if record is None:
                require(entry.method == "EXPLICIT_NEW_REVIEWED" or entry.kind == "SEASON", "SCOPED_REUSE_ENTITY_MISSING")
                existing = None
            elif entry.kind == "TEAM":
                require(entry.method == "REUSE_EXISTING_REVIEWED", "SCOPED_NEW_ENTITY_ALREADY_EXISTS")
                existing = dict(name=record.name, canonical_key=record.canonical_key, team_type=record.team_type)
            else:
                require(entry.method == "REUSE_EXISTING_REVIEWED", "SCOPED_NEW_ENTITY_ALREADY_EXISTS")
                existing = dict(name=record.name, canonical_key=record.canonical_key, country_code=record.country_code)
            result.append(dict(kind=entry.kind, canonical_id=entry.canonical_id, source_label=entry.source_label, existing=existing))
        return tuple(result)

    def prepare_scope(self, *, profile, country_code, rights, source_prefix, acquisition, parser_policy, classification):
        profile = CompetitionScopeProfileV2.model_validate(profile)
        with self._transaction() as session:
            subject = d.ScopeQualificationSubjectV2(profile=profile, country_code=country_code, rights=rights, source_prefix=source_prefix, acquisition=acquisition,
                parser_policy=parser_policy, classification=classification, storage=self._storage(session, classification),
                catalog_context=self._catalog_context(session, profile))
            self._inputs(subject, self._now())
            return subject

    def _load(self, session, artifact_id, kind=None):
        table = self._table("ofpv2_artifacts")
        row = session.execute(select(table).where(table.c.artifact_id == artifact_id)).mappings().one()
        value = d.ScopedArtifactV2.model_validate(strict_json_bytes(row["artifact_json"].encode()))
        require(kind is None or value.payload.kind == kind, "SCOPED_ARTIFACT_KIND_MISMATCH")
        require(dict(row) == self._artifact_row(value), "SCOPED_ARTIFACT_COLUMN_MISMATCH")
        require(session.scalar(select(self._table("ofpv2_seals").c.artifact_id).where(self._table("ofpv2_seals").c.artifact_id == artifact_id)) is not None,
            "SCOPED_COMPLETE_SEAL_REQUIRED")
        links = self._table("ofpv2_parents")
        actual = session.execute(select(links).where(links.c.child_id == artifact_id).order_by(links.c.parent_kind)).mappings().all()
        expected = [dict(child_id=artifact_id, parent_kind=p.kind, scope_id=value.owner.scope_id, parent_id=p.artifact_id,
            parent_hash=p.artifact_hash, parent_instance_id=p.instance_id) for p in value.parents]
        require([dict(r) for r in actual] == expected, "SCOPED_COMPLETE_PARENT_SET_REQUIRED")
        for parent in value.parents:
            row = session.execute(text("SELECT a.scope_id,a.instance_id,a.kind,a.artifact_hash,z.completed_at_utc FROM ofpv2_artifacts a JOIN ofpv2_seals z ON z.artifact_id=a.artifact_id WHERE a.artifact_id=:id"), {"id": parent.artifact_id}).one_or_none()
            require(row is not None and tuple(row[:4]) == (value.owner.scope_id, parent.instance_id, parent.kind, parent.artifact_hash)
                and datetime.fromisoformat(row[4]) <= value.recorded_at_utc, "SCOPED_PARENT_OWNER_HASH_OR_TIME_CHANGED")
        return value

    @staticmethod
    def _artifact_row(value):
        return dict(artifact_id=value.artifact_id, artifact_hash=value.artifact_hash, scope_id=value.owner.scope_id, scope_hash=value.owner.scope_hash,
            instance_id=value.instance_id, kind=value.payload.kind, recorded_at_utc=value.model_dump(mode="json")["recorded_at_utc"], artifact_json=canonical_json(value))

    def _phase(self, session, scope_id, instance_id, kind, required=True):
        t = self._table("ofpv2_phases")
        key = session.scalar(select(t.c.artifact_id).where(t.c.scope_id == scope_id, t.c.instance_id == instance_id, t.c.kind == kind))
        require(key is not None or not required, "SCOPED_PREREQUISITE_MISSING:" + kind)
        return self._load(session, key, kind) if key is not None else None

    def _prior(self, session, owner, instance_id, kind, request_key, request):
        require(isinstance(request_key, str) and 0 < len(request_key) <= 160 and request_key == request_key.strip(), "SCOPED_REQUEST_KEY_REQUIRED")
        digest = tagged_canonical_sha256("OFP_V2_REQUEST", dict(operator=self.operator_id, scope=owner, instance=instance_id, kind=kind, request=request))
        t = self._table("ofpv2_operations")
        row = session.execute(select(t).where(t.c.scope_id == owner.scope_id, t.c.instance_id == instance_id, t.c.request_key == request_key)).mappings().one_or_none()
        if row:
            require(row["request_hash"] == digest, "SCOPED_EXACT_RETRY_CONFLICT")
            value = self._load(session, row["artifact_id"], kind)
            require(value.owner == owner and value.instance_id == instance_id, "SCOPED_OPERATION_OWNER_MISMATCH")
            return value, digest
        require(self._phase(session, owner.scope_id, instance_id, kind, False) is None, "SCOPED_PHASE_ALREADY_RECORDED")
        return None, digest

    def _put(self, session, *, owner, instance_id, payload, parents=(), at=None, request_key=None, request_hash=None, project=None):
        value = d.ScopedArtifactV2.freeze(owner=owner, instance_id=instance_id, recorded_at_utc=at or self._now(),
            payload=payload, parents=tuple(p.reference() for p in parents))
        session.execute(self._table("ofpv2_artifacts").insert().values(**self._artifact_row(value)))
        for parent in value.parents:
            session.execute(self._table("ofpv2_parents").insert().values(child_id=value.artifact_id, parent_kind=parent.kind,
                scope_id=owner.scope_id, parent_id=parent.artifact_id, parent_hash=parent.artifact_hash, parent_instance_id=parent.instance_id))
        session.execute(self._table("ofpv2_phases").insert().values(scope_id=owner.scope_id, instance_id=instance_id, kind=payload.kind,
            artifact_id=value.artifact_id, artifact_hash=value.artifact_hash))
        if project is not None:
            project(value)
        if request_key is not None:
            session.execute(self._table("ofpv2_operations").insert().values(scope_id=owner.scope_id, instance_id=instance_id,
                request_key=request_key, request_hash=request_hash, artifact_id=value.artifact_id))
        session.execute(self._table("ofpv2_seals").insert().values(artifact_id=value.artifact_id, completed_at_utc=self._now().isoformat().replace("+00:00", "Z")))
        return self._load(session, value.artifact_id, payload.kind)

    def _register_catalog(self, session, subject, at):
        for context in subject.catalog_context:
            kind, cid = context["kind"], context["canonical_id"]
            if kind == "SEASON":
                continue
            cls = TeamRecord if kind == "TEAM" else CompetitionRecord
            existing = session.get(cls, cid)
            if context["existing"] is None:
                require(existing is None, "SCOPED_CATALOG_CHANGED_AFTER_REVIEW")
                values = dict(canonical_key="ofpv2:" + kind.lower() + ":" + cid, name=context["source_label"])
                values.update(dict(team_id=cid, team_type="CLUB") if kind == "TEAM" else dict(competition_id=cid, country_code=subject.country_code))
                session.add(cls(**values))
            else:
                require(existing is not None and all(getattr(existing, k) == v for k, v in context["existing"].items()), "SCOPED_REUSED_ENTITY_CHANGED")
                if kind == "COMPETITION":
                    require(existing.country_code == subject.country_code, "SCOPED_COUNTRY_REVIEW_MISMATCH")
        session.flush()
        for row in subject.profile.records:
            fact = row.candidate_result
            if fact is None:
                continue
            match, identity = session.get(MatchRecord, fact.match_id), session.get(CanonicalMatchIdentityRecord, fact.match_id)
            if match is None:
                session.add(MatchRecord(internal_match_id=fact.match_id, competition_id=subject.profile.competition_id, home_team_id=fact.home_team_id,
                    away_team_id=fact.away_team_id, kickoff_at_utc=fact.kickoff_at_utc, status="FINISHED", available_at_utc=at, created_at_utc=at, fixture_ingestion_id=None))
                session.flush()
                session.add(CanonicalMatchIdentityRecord(internal_match_id=fact.match_id, season=fact.season_id, competition_type="DOMESTIC_LEAGUE",
                    available_at_utc=at, fixture_ingestion_id=None))
            else:
                require(identity is not None and (match.competition_id, identity.season, match.home_team_id, match.away_team_id, match.kickoff_at_utc)
                    == (subject.profile.competition_id, fact.season_id, fact.home_team_id, fact.away_team_id, fact.kickoff_at_utc), "SCOPED_MATCH_IDENTITY_COLLISION")
        session.flush()

    def record_scope(self, *, request_key, subject, review, authority):
        subject = d.ScopeQualificationSubjectV2.model_validate(subject)
        owner = ScopeIdentityV2.of(subject.profile)
        with self._transaction(True) as session:
            reviewed = self._review(subject, review, authority, subject.profile.source_id, self._now())
            require(max(f.captured_at_utc for f in subject.profile.files) <= reviewed.reviewed_at_utc, "SCOPED_REVIEW_PRECEDES_ACQUISITION")
            raw_rows = self._inputs(subject, self._now())
            require(subject.storage == self._storage(session, subject.classification), "SCOPED_STORAGE_BINDING_CHANGED")
            prior, digest = self._prior(session, owner, "", "QUALIFICATION", request_key, dict(subject=subject, review=review, authority=authority))
            if prior is not None:
                self._data(session, owner.scope_id)
                return prior
            require(subject.catalog_context == self._catalog_context(session, subject.profile), "SCOPED_CATALOG_CHANGED_AFTER_REVIEW")
            at = self._now()
            self._register_catalog(session, subject, at)
            facts = d.admitted_results(subject.profile, at)
            payload = d.QualificationPayloadV2(subject=subject, subject_hash=subject.content_hash, review=review, authority=authority,
                admitted_at_utc=at, facts=facts, facts_root=d.scoped_facts_root(facts))
            planned = d.ScopedArtifactV2.freeze(owner=owner, instance_id="", recorded_at_utc=at, parents=(), payload=payload)
            session.execute(self._table("ofpv2_scopes").insert().values(scope_id=owner.scope_id, scope_hash=owner.scope_hash,
                source_id=subject.profile.source_id, competition_id=owner.competition_id, qualification_id=planned.artifact_id,
                profile_json=canonical_json(subject.profile), classification=subject.classification))
            def project(_value):
                for file in subject.profile.files:
                    session.execute(self._table("ofpv2_files").insert().values(scope_id=owner.scope_id, path=file.path,
                        file_json=canonical_json(file), record_count=file.record_count, sha256=file.sha256))
                for entry in subject.profile.mapping:
                    session.execute(self._table("ofpv2_mappings").insert().values(scope_id=owner.scope_id, kind=entry.kind,
                        source_label=entry.source_label, canonical_id=entry.canonical_id, entry_json=canonical_json(entry)))
                by_match = {f.match_id: f for f in facts}
                for row in subject.profile.records:
                    fact = by_match[row.candidate_result.match_id] if row.candidate_result is not None else None
                    session.execute(self._table("ofpv2_records").insert().values(scope_id=owner.scope_id, file=row.file, position=row.index,
                        raw_hash=row.raw_record_sha256, raw_json=canonical_json(raw_rows[(row.file, row.index)]), disposition=row.disposition,
                        reason=row.reason, match_id=fact.match_id if fact else None, result_id=fact.match_result_id if fact else None,
                        fact_json=canonical_json(fact) if fact else None))
            value = self._put(session, owner=owner, instance_id="", payload=payload, at=at, project=project, request_key=request_key, request_hash=digest)
            self._data(session, owner.scope_id)
            return value

    def _data(self, session, scope_id):
        value = self._phase(session, scope_id, "", "QUALIFICATION")
        payload, profile = value.payload, value.payload.subject.profile
        self._review(payload.subject, payload.review, payload.authority, profile.source_id, self._now())
        raw = self._inputs(payload.subject, self._now())
        require(payload.subject.storage == self._storage(session, payload.subject.classification), "SCOPED_STORAGE_BINDING_CHANGED")
        registry = session.execute(select(self._table("ofpv2_scopes")).where(self._table("ofpv2_scopes").c.scope_id == scope_id)).mappings().one()
        require(dict(registry) == dict(scope_id=scope_id, scope_hash=value.owner.scope_hash, source_id=profile.source_id,
            competition_id=profile.competition_id, qualification_id=value.artifact_id, profile_json=canonical_json(profile),
            classification=payload.subject.classification), "SCOPED_REGISTRY_PROJECTION_CHANGED")
        for name, expected in (
            ("ofpv2_files", [dict(scope_id=scope_id, path=f.path, file_json=canonical_json(f), record_count=f.record_count, sha256=f.sha256) for f in profile.files]),
            ("ofpv2_mappings", [dict(scope_id=scope_id, kind=m.kind, source_label=m.source_label, canonical_id=m.canonical_id, entry_json=canonical_json(m)) for m in profile.mapping]),
        ):
            table = self._table(name)
            actual = [dict(r) for r in session.execute(select(table).where(table.c.scope_id == scope_id)).mappings()]
            require(sorted(map(canonical_json, actual)) == sorted(map(canonical_json, expected)), "SCOPED_SOURCE_OR_MAPPING_CENSUS_CHANGED")
        for context in payload.subject.catalog_context:
            if context["kind"] == "SEASON":
                continue
            cls = TeamRecord if context["kind"] == "TEAM" else CompetitionRecord
            record = session.get(cls, context["canonical_id"])
            expected = context["existing"]
            if expected is None:
                expected = dict(name=context["source_label"], canonical_key="ofpv2:" + context["kind"].lower() + ":" + context["canonical_id"])
                expected.update(dict(team_type="CLUB") if context["kind"] == "TEAM" else dict(country_code=payload.subject.country_code))
            require(record is not None and all(getattr(record, k) == v for k, v in expected.items()), "SCOPED_CANONICAL_ENTITY_CHANGED")
        t = self._table("ofpv2_records")
        stored = session.execute(select(t).where(t.c.scope_id == scope_id).order_by(t.c.file, t.c.position)).mappings().all()
        require(len(stored) == len(profile.records), "SCOPED_PERSISTED_RAW_CENSUS_INCOMPLETE")
        facts = {f.match_id: f for f in payload.facts}
        for row, record in zip(stored, profile.records, strict=True):
            fact = facts[record.candidate_result.match_id] if record.candidate_result is not None else None
            require(dict(row) == dict(scope_id=scope_id, file=record.file, position=record.index, raw_hash=record.raw_record_sha256,
                raw_json=canonical_json(raw[(record.file, record.index)]), disposition=record.disposition, reason=record.reason,
                match_id=fact.match_id if fact else None, result_id=fact.match_result_id if fact else None,
                fact_json=canonical_json(fact) if fact else None), "SCOPED_PERSISTED_RECORD_CHANGED")
            if fact is not None:
                match, identity = session.get(MatchRecord, fact.match_id), session.get(CanonicalMatchIdentityRecord, fact.match_id)
                require(match and identity and (match.competition_id, identity.season, match.home_team_id, match.away_team_id, match.kickoff_at_utc)
                    == (profile.competition_id, fact.season_id, fact.home_team_id, fact.away_team_id, fact.kickoff_at_utc), "SCOPED_CANONICAL_FACT_CHANGED")
                require(session.get(MatchResultRecord, fact.match_result_id) is None, "SCOPED_FACT_LEAKED_INTO_LEGACY_RESULTS")
        return value

    def inspect_scope(self, scope_id):
        with self._transaction() as session:
            return self._data(session, scope_id)

    @staticmethod
    def _parents(value, *parents):
        require(value.parents == tuple(sorted((p.reference() for p in parents), key=lambda p: p.kind)), "SCOPED_PHASE_PARENT_GRAPH_MISMATCH")

    def _validate_targets(self, session, qualification, subject, at, *, historical=False):
        profile = qualification.payload.subject.profile
        targets = subject.targets
        excluded = {t.match_id for t in targets}
        require(not excluded & {f.match_id for f in qualification.payload.facts}, "SCOPED_TARGET_TRAINING_INTERSECTION")
        for target in targets:
            match, identity = session.get(MatchRecord, target.match_id), session.get(CanonicalMatchIdentityRecord, target.match_id)
            require(match and identity and (match.competition_id, identity.season, match.home_team_id, match.away_team_id, match.kickoff_at_utc)
                == (profile.competition_id, profile.window[-1].season_id, target.home_team_id, target.away_team_id, target.kickoff_at_utc),
                "SCOPED_TARGET_CANONICAL_OWNERSHIP_MISMATCH")
            require(match.available_at_utc <= at < target.kickoff_at_utc and (historical or match.status == "SCHEDULED"), "SCOPED_TARGET_NOT_FUTURE_VISIBLE")
            query = select(MatchResultRecord.match_result_id).where(MatchResultRecord.internal_match_id == target.match_id)
            if historical:
                query = query.where(MatchResultRecord.ingested_at_utc <= at)
            require(session.scalar(query.limit(1)) is None, "SCOPED_TARGET_KNOWN_RESULT")
        visible = set(session.scalars(select(MatchRecord.internal_match_id).join(CanonicalMatchIdentityRecord,
            CanonicalMatchIdentityRecord.internal_match_id == MatchRecord.internal_match_id).where(MatchRecord.competition_id == profile.competition_id,
            CanonicalMatchIdentityRecord.season == profile.window[-1].season_id, MatchRecord.available_at_utc <= at,
            MatchRecord.kickoff_at_utc >= subject.kickoff_from_utc, MatchRecord.kickoff_at_utc < subject.kickoff_to_utc)))
        require(visible == excluded, "SCOPED_FULL_TARGET_WINDOW_CENSUS_REQUIRED")

    def _live_proof(self, session, qualification, targets, at):
        from football_system.infrastructure.database.models import LiveAnalysisPreparationRecord, LiveAnalysisPreparationMatchRecord
        from football_system.infrastructure.database.live_source_repositories import _preparation_from_record, _load_prepared_bundle
        from football_system.infrastructure.database.real_bridge_sources import market_values, sp_values
        profile = qualification.payload.subject.profile
        values = []
        for pid in sorted({t.live_preparation_id for t in targets}):
            stored = session.get(LiveAnalysisPreparationRecord, pid)
            require(stored is not None, "SCOPED_COMPLETE_LIVE_INPUTS_REQUIRED")
            preparation = _preparation_from_record(stored)
            expected = tuple(sorted(t.match_id for t in targets if t.live_preparation_id == pid))
            require(not stored.allow_partial_inputs and stored.status == "ANALYSIS_INPUT_READY" and stored.created_at_utc <= at
                and stored.match_count == stored.ready_match_count == len(expected) and tuple(sorted(preparation.ready_match_ids)) == expected
                and stored.competition_id == profile.competition_id and stored.season_id == profile.window[-1].season_id, "SCOPED_LIVE_COHORT_NOT_COMPLETE")
            rows = tuple(session.scalars(select(LiveAnalysisPreparationMatchRecord).where(LiveAnalysisPreparationMatchRecord.preparation_id == pid)
                .order_by(LiveAnalysisPreparationMatchRecord.match_no)))
            bundle = _load_prepared_bundle(session, preparation, rows)
            require({m.match_id for m in bundle.fixtures.matches} == set(expected), "SCOPED_LIVE_TARGET_SET_MISMATCH")
            for row in rows:
                target = next(t for t in targets if t.match_id == row.internal_match_id)
                fixture = next(m for m in bundle.fixtures.matches if m.match_id == target.match_id)
                require(row.fixture_observation_id == target.fixture_observation_id and fixture.kickoff_at_utc == target.kickoff_at_utc
                    and fixture.status.value == "SCHEDULED", "SCOPED_LIVE_FIXTURE_REF_CHANGED")
                market = market_values(session, self._sessions, row.market_ingestion_id, row.market_consensus_snapshot_id)
                sporttery = sp_values(session, self._sessions, row.sporttery_ingestion_id, row.sporttery_bonus_snapshot_id)
                require(market["match_id"] == sporttery["match_id"] == target.match_id and sporttery["sale_status"] == "OPEN"
                    and max(market["available_at_utc"], market["ingested_at_utc"], sporttery["available_at_utc"], sporttery["ingested_at_utc"]) <= at
                    and all(0 <= (at-v["captured_at_utc"]).total_seconds() <= stored.maximum_odds_age_seconds for v in (market, sporttery))
                    and len(market["constituent_refs"]) >= stored.minimum_bookmaker_count, "SCOPED_LIVE_INPUT_EXPIRED_OR_UNAVAILABLE")
            values.append(dict(preparation_id=pid, report_hash=stored.report_hash, match_ids=list(expected),
                input_cutoff_at_utc=stored.decision_as_of_at_utc.isoformat()))
        return tuple(values)

    def prepare_instance(self, *, scope_id, targets, kickoff_from_utc, kickoff_to_utc):
        with self._transaction() as session:
            qualification = self._data(session, scope_id)
            subject = d.InstanceSubjectV2(scope=qualification.owner, qualification=qualification.reference(), targets=targets,
                kickoff_from_utc=kickoff_from_utc, kickoff_to_utc=kickoff_to_utc)
            self._validate_targets(session, qualification, subject, self._now())
            self._live_proof(session, qualification, subject.targets, self._now())
            return subject

    def record_instance(self, *, request_key, subject, review, authority):
        subject = d.InstanceSubjectV2.model_validate(subject)
        with self._transaction(True) as session:
            q = self._data(session, subject.scope.scope_id)
            require(subject.scope == q.owner and subject.qualification == q.reference(), "SCOPED_INSTANCE_QUALIFICATION_CHANGED")
            self._review(subject, review, authority, q.payload.subject.profile.source_id, self._now())
            iid, key = d.instance_identity(q.payload.subject.profile, subject.targets)
            prior, digest = self._prior(session, q.owner, iid, "INSTANCE", request_key, dict(subject=subject, review=review, authority=authority))
            if prior is not None:
                self._instance(session, q.owner.scope_id, iid)
                return prior
            require(session.scalar(select(self._table("ofpv2_instances").c.instance_id).where(self._table("ofpv2_instances").c.logical_key == key)) is None,
                "SCOPED_LOGICAL_BOOTSTRAP_ALREADY_EXISTS")
            cutoff = self._now()
            completed = session.scalar(select(self._table("ofpv2_seals").c.completed_at_utc).where(self._table("ofpv2_seals").c.artifact_id == q.artifact_id))
            require(datetime.fromisoformat(completed) < cutoff and all(f.ingested_at_utc < cutoff for f in q.payload.facts), "SCOPED_TRUSTED_CUTOFF_AFTER_ADMISSION_REQUIRED")
            self._validate_targets(session, q, subject, cutoff)
            self._live_proof(session, q, subject.targets, cutoff)
            payload = d.InstancePayloadV2(subject=subject, review=review, authority=authority, logical_key=key, cutoff_at_utc=cutoff, implementation_revision=_code_revision())
            def project(value):
                session.execute(self._table("ofpv2_instances").insert().values(instance_id=iid, scope_id=q.owner.scope_id, logical_key=key, artifact_id=value.artifact_id))
            value = self._put(session, owner=q.owner, instance_id=iid, payload=payload, parents=(q,), at=cutoff, project=project,
                request_key=request_key, request_hash=digest)
            self._data(session, q.owner.scope_id)
            return value

    def _instance(self, session, scope_id, iid):
        q = self._data(session, scope_id)
        instance = self._phase(session, scope_id, iid, "INSTANCE")
        self._parents(instance, q)
        p = instance.payload
        require(p.subject.scope == q.owner and p.subject.qualification == q.reference()
            and d.instance_identity(q.payload.subject.profile, p.subject.targets) == (iid, p.logical_key)
            and p.implementation_revision == _code_revision(), "SCOPED_INSTANCE_IDENTITY_OR_IMPLEMENTATION_CHANGED")
        self._review(p.subject, p.review, p.authority, q.payload.subject.profile.source_id, self._now())
        self._validate_targets(session, q, p.subject, p.cutoff_at_utc, historical=True)
        require(all(f.ingested_at_utc < p.cutoff_at_utc for f in q.payload.facts), "SCOPED_CUTOFF_PRECEDES_ADMISSION")
        return q, instance

    def _state(self, qualification, instance):
        from football_system.domain.services.elo_baseline import EloThreeWayBaseline
        return EloThreeWayBaseline().rebuild_state(qualification.payload.facts, instance.payload.cutoff_at_utc,
            target_season_id=qualification.payload.subject.profile.window[-1].season_id,
            exclude_match_ids=tuple(t.match_id for t in instance.payload.subject.targets))

    def run_pilot(self, *, scope_id, instance_id, request_key):
        with self._transaction(True) as session:
            q, instance = self._instance(session, scope_id, instance_id)
            prior, digest = self._prior(session, q.owner, instance_id, "PILOT_REPORT", request_key, instance.reference())
            if prior is not None:
                self._pilot(session, scope_id, instance_id)
                return prior
            require(self._phase(session, scope_id, instance_id, "PILOT_RESERVATION", False) is None, "SCOPED_PILOT_ALREADY_RESERVED_NO_NEW_ATTEMPT")
            reservation = self._put(session, owner=q.owner, instance_id=instance_id,
                payload=d.ReservationPayloadV2(request_key=request_key, request_hash=digest), parents=(q, instance))
        try:
            state = self._state(q, instance)
            with self._transaction(True) as session:
                current_q, current_instance = self._instance(session, scope_id, instance_id)
                require((current_q, current_instance) == (q, instance), "SCOPED_PILOT_INPUT_CHANGED")
                value = self._put(session, owner=q.owner, instance_id=instance_id, payload=d.PilotPayloadV2(state=state,
                    facts_root=q.payload.facts_root, window_hash=q.payload.subject.profile.window_hash, implementation_revision=_code_revision()),
                    parents=(q, instance, reservation), request_key=request_key, request_hash=digest)
                self._data(session, scope_id)
                return value
        except Exception as error:
            with self._transaction(True) as session:
                if self._phase(session, scope_id, instance_id, "PILOT_REPORT", False) is None and self._phase(session, scope_id, instance_id, "PILOT_FAILURE", False) is None:
                    self._put(session, owner=q.owner, instance_id=instance_id, payload=d.FailurePayloadV2(error_type=type(error).__name__),
                        parents=(q, instance, reservation))
            raise

    def _pilot(self, session, scope_id, iid):
        q, instance = self._instance(session, scope_id, iid)
        reservation = self._phase(session, scope_id, iid, "PILOT_RESERVATION")
        report = self._phase(session, scope_id, iid, "PILOT_REPORT")
        require(self._phase(session, scope_id, iid, "PILOT_FAILURE", False) is None, "SCOPED_FAILED_PILOT_NOT_RELEASABLE")
        self._parents(reservation, q, instance)
        self._parents(report, q, instance, reservation)
        require(report.payload.state == self._state(q, instance) and report.payload.facts_root == q.payload.facts_root
            and report.payload.window_hash == q.payload.subject.profile.window_hash and report.payload.implementation_revision == _code_revision(),
            "SCOPED_INDEPENDENT_ELO_REPLAY_MISMATCH")
        return q, instance, reservation, report

    def attest_pilot(self, *, scope_id, instance_id, request_key):
        with self._transaction(True) as session:
            q, instance, reservation, report = self._pilot(session, scope_id, instance_id)
            payload = d.AttestationPayloadV2(state_hash=report.payload.state.state_hash, training_data_hash=report.payload.state.training_data_hash)
            prior, digest = self._prior(session, q.owner, instance_id, payload.kind, request_key, payload)
            value = prior or self._put(session, owner=q.owner, instance_id=instance_id, payload=payload, parents=(instance, reservation, report),
                request_key=request_key, request_hash=digest)
            self._data(session, scope_id)
            return value

    def create_manifest(self, *, scope_id, instance_id, request_key):
        with self._transaction(True) as session:
            q, instance, _, report = self._pilot(session, scope_id, instance_id)
            attest = self._phase(session, scope_id, instance_id, "PILOT_ATTESTATION")
            self._parents(attest, instance, self._phase(session, scope_id, instance_id, "PILOT_RESERVATION"), report)
            require((attest.payload.state_hash, attest.payload.training_data_hash) == (report.payload.state.state_hash, report.payload.state.training_data_hash), "SCOPED_ATTESTATION_STATE_MISMATCH")
            p = q.payload.subject.profile
            payload = d.ManifestPayloadV2(facts_root=q.payload.facts_root, candidate_facts_root=p.facts_root, mapping_root=p.mapping_root,
                window_hash=p.window_hash, exceptions_root=p.exceptions_root, source_record_count=len(p.records), fact_count=len(q.payload.facts),
                exception_count=len(p.records)-len(q.payload.facts), cutoff_at_utc=instance.payload.cutoff_at_utc,
                target_match_ids=tuple(t.match_id for t in instance.payload.subject.targets), state_hash=report.payload.state.state_hash,
                training_data_hash=report.payload.state.training_data_hash, implementation_revision=_code_revision())
            prior, digest = self._prior(session, q.owner, instance_id, payload.kind, request_key, payload)
            value = prior or self._put(session, owner=q.owner, instance_id=instance_id, payload=payload, parents=(q, instance, report, attest),
                request_key=request_key, request_hash=digest)
            self._data(session, scope_id)
            return value

    def _manifest(self, session, scope_id, iid):
        q, instance, reservation, report = self._pilot(session, scope_id, iid)
        attest = self._phase(session, scope_id, iid, "PILOT_ATTESTATION")
        self._parents(attest, instance, reservation, report)
        require((attest.payload.state_hash, attest.payload.training_data_hash) == (report.payload.state.state_hash, report.payload.state.training_data_hash),
            "SCOPED_ATTESTATION_STATE_MISMATCH")
        manifest = self._phase(session, scope_id, iid, "MANIFEST")
        self._parents(manifest, q, instance, report, attest)
        p, m = q.payload.subject.profile, manifest.payload
        require((m.facts_root, m.candidate_facts_root, m.mapping_root, m.window_hash, m.exceptions_root)
            == (q.payload.facts_root, p.facts_root, p.mapping_root, p.window_hash, p.exceptions_root)
            and (m.source_record_count, m.fact_count, m.exception_count) == (len(p.records), len(q.payload.facts), len(p.records)-len(q.payload.facts))
            and (m.state_hash, m.training_data_hash) == (report.payload.state.state_hash, report.payload.state.training_data_hash)
            and m.target_match_ids == tuple(t.match_id for t in instance.payload.subject.targets)
            and m.cutoff_at_utc == instance.payload.cutoff_at_utc and m.implementation_revision == _code_revision(), "SCOPED_MANIFEST_GRAPH_MISMATCH")
        return q, instance, report, manifest

    def prepare_approval(self, *, scope_id, instance_id, effective_at_utc, expires_at_utc):
        with self._transaction() as session:
            q, instance, report, manifest = self._manifest(session, scope_id, instance_id)
            return d.ScopedModelApprovalV2(scope=q.owner, instance_id=instance_id, manifest=manifest.reference(), facts_root=q.payload.facts_root,
                state_hash=report.payload.state.state_hash, training_data_hash=report.payload.state.training_data_hash,
                target_match_ids=manifest.payload.target_match_ids, cutoff_at_utc=instance.payload.cutoff_at_utc, implementation_revision=_code_revision(),
                effective_at_utc=effective_at_utc, expires_at_utc=expires_at_utc)

    def _approval_matches(self, subject, q, instance, report, manifest):
        require(subject.scope == q.owner and subject.instance_id == instance.instance_id and subject.manifest == manifest.reference()
            and subject.facts_root == q.payload.facts_root and subject.state_hash == report.payload.state.state_hash
            and subject.training_data_hash == report.payload.state.training_data_hash and subject.target_match_ids == manifest.payload.target_match_ids
            and subject.cutoff_at_utc == instance.payload.cutoff_at_utc and subject.implementation_revision == _code_revision(), "SCOPED_EXACT_MODEL_APPROVAL_REQUIRED")

    def _model_authority(self, q, approval):
        subject = approval.payload.subject
        at = self._now()
        require(subject.effective_at_utc <= at < subject.expires_at_utc, "SCOPED_MODEL_APPROVAL_EXPIRED_OR_INACTIVE")
        result = self._review(subject, approval.payload.review, approval.payload.authority, q.payload.subject.profile.source_id, at)
        require(subject.effective_at_utc <= self._now() < subject.expires_at_utc, "SCOPED_MODEL_APPROVAL_EXPIRED_DURING_CHECK")
        return result

    def record_approval(self, *, scope_id, instance_id, request_key, subject, review, authority):
        subject = d.ScopedModelApprovalV2.model_validate(subject)
        with self._transaction(True) as session:
            q, instance, report, manifest = self._manifest(session, scope_id, instance_id)
            self._approval_matches(subject, q, instance, report, manifest)
            at = self._now()
            self._validate_targets(session, q, instance.payload.subject, at)
            require(subject.effective_at_utc <= at < subject.expires_at_utc, "SCOPED_MODEL_APPROVAL_NOT_ACTIVE")
            reviewed = self._review(subject, review, authority, q.payload.subject.profile.source_id, at)
            completion = session.scalar(select(self._table("ofpv2_seals").c.completed_at_utc).where(self._table("ofpv2_seals").c.artifact_id == manifest.artifact_id))
            require(reviewed.reviewed_at_utc >= datetime.fromisoformat(completion), "SCOPED_MODEL_REVIEW_MUST_FOLLOW_MANIFEST")
            payload = d.ApprovalPayloadV2(subject=subject, review=review, authority=authority)
            prior, digest = self._prior(session, q.owner, instance_id, payload.kind, request_key, payload)
            value = prior or self._put(session, owner=q.owner, instance_id=instance_id, payload=payload, parents=(q, instance, manifest),
                request_key=request_key, request_hash=digest)
            self._model_authority(q, value)
            self._data(session, scope_id)
            return value

    def build_release(self, *, scope_id, instance_id, request_key):
        with self._transaction(True) as session:
            q, instance, report, manifest = self._manifest(session, scope_id, instance_id)
            approval = self._phase(session, scope_id, instance_id, "APPROVAL")
            self._parents(approval, q, instance, manifest)
            self._model_authority(q, approval)
            self._approval_matches(approval.payload.subject, q, instance, report, manifest)
            self._validate_targets(session, q, instance.payload.subject, self._now())
            payload = d.ReleasePayloadV2(state=self._state(q, instance), facts_root=q.payload.facts_root, implementation_revision=_code_revision())
            prior, digest = self._prior(session, q.owner, instance_id, payload.kind, request_key, approval.reference())
            value = prior or self._put(session, owner=q.owner, instance_id=instance_id, payload=payload, parents=(q, instance, report, manifest, approval),
                request_key=request_key, request_hash=digest)
            self._model_authority(q, approval)
            self._data(session, scope_id)
            return value

    def _release(self, session, scope_id, iid):
        q, instance, report, manifest = self._manifest(session, scope_id, iid)
        approval, release = self._phase(session, scope_id, iid, "APPROVAL"), self._phase(session, scope_id, iid, "RELEASE")
        self._parents(approval, q, instance, manifest)
        self._parents(release, q, instance, report, manifest, approval)
        self._model_authority(q, approval)
        self._approval_matches(approval.payload.subject, q, instance, report, manifest)
        require(release.payload.state == report.payload.state and release.payload.facts_root == q.payload.facts_root
            and release.payload.implementation_revision == _code_revision(), "SCOPED_RELEASE_STATE_REPLAY_MISMATCH")
        return q, instance, approval, release

    def create_target_plan(self, *, scope_id, instance_id, request_key):
        with self._transaction(True) as session:
            q, instance, approval, release = self._release(session, scope_id, instance_id)
            at = self._now()
            self._validate_targets(session, q, instance.payload.subject, at)
            proof = self._live_proof(session, q, instance.payload.subject.targets, at)
            payload = d.TargetPayloadV2(targets=instance.payload.subject.targets, live_input_proof=proof)
            prior, digest = self._prior(session, q.owner, instance_id, payload.kind, request_key, payload)
            value = prior or self._put(session, owner=q.owner, instance_id=instance_id, payload=payload, parents=(instance, release),
                request_key=request_key, request_hash=digest)
            self._model_authority(q, approval)
            self._data(session, scope_id)
            return value

    @staticmethod
    def _evaluations(state, targets):
        from football_system.domain.services.elo_baseline import EloPredictionRequest, EloThreeWayBaseline
        return tuple(dict(match_id=t.match_id, prediction=EloThreeWayBaseline().predict_from_state(EloPredictionRequest(match_id=t.match_id,
            season_id=t.season_id, home_team_id=t.home_team_id, away_team_id=t.away_team_id, kickoff_at_utc=t.kickoff_at_utc,
            cutoff_at_utc=state.cutoff_at_utc), state).model_dump(mode="json")) for t in targets)

    @staticmethod
    def _authority_hash(q, approval):
        return tagged_canonical_sha256("OFP_V2_CURRENT_MODEL_AUTHORITY", dict(qualification=q.reference(), source_review=q.payload.review,
            source_authority=q.payload.authority, approval=approval.reference(), review=approval.payload.review, authority=approval.payload.authority))

    def bind_model_state(self, *, scope_id, instance_id, request_key):
        with self._transaction(True) as session:
            q, instance, approval, release = self._release(session, scope_id, instance_id)
            plan = self._phase(session, scope_id, instance_id, "TARGET_PLAN")
            self._parents(plan, instance, release)
            require(plan.payload.targets == instance.payload.subject.targets, "SCOPED_TARGET_PLAN_COHORT_CHANGED")
            at = self._now()
            self._validate_targets(session, q, instance.payload.subject, at)
            require(canonical_json(self._live_proof(session, q, plan.payload.targets, at)) == canonical_json(plan.payload.live_input_proof), "SCOPED_LIVE_PROOF_CHANGED")
            state = release.payload.state
            payload = d.StateBindingPayloadV2(model_state_id=stable_id("OFP_V2_QUALIFIED_MODEL_STATE", release.artifact_id, state.state_hash),
                state_hash=state.state_hash, training_data_hash=state.training_data_hash, authority_hash=self._authority_hash(q, approval),
                target_match_ids=tuple(t.match_id for t in plan.payload.targets), evaluations=self._evaluations(state, plan.payload.targets))
            prior, digest = self._prior(session, q.owner, instance_id, payload.kind, request_key, dict(release=release.reference(), plan=plan.reference()))
            value = prior or self._put(session, owner=q.owner, instance_id=instance_id, payload=payload, parents=(q, instance, approval, release, plan),
                request_key=request_key, request_hash=digest)
            self._model_authority(q, approval)
            self._data(session, scope_id)
            require(self._now() < min(t.kickoff_at_utc for t in plan.payload.targets), "SCOPED_BINDING_COMPLETED_AFTER_KICKOFF")
            return value

    def _binding(self, session, scope_id, iid):
        q, instance, approval, release = self._release(session, scope_id, iid)
        plan = self._phase(session, scope_id, iid, "TARGET_PLAN")
        binding = self._phase(session, scope_id, iid, "STATE_BINDING")
        self._parents(plan, instance, release)
        self._parents(binding, q, instance, approval, release, plan)
        state = release.payload.state
        require(plan.payload.targets == instance.payload.subject.targets
            and binding.payload.target_match_ids == tuple(t.match_id for t in plan.payload.targets)
            and binding.payload.model_state_id == stable_id("OFP_V2_QUALIFIED_MODEL_STATE", release.artifact_id, state.state_hash)
            and binding.payload.config_hash == state.config_hash == CONFIG_HASH
            and binding.payload.state_hash == state.state_hash and binding.payload.training_data_hash == state.training_data_hash
            and binding.payload.authority_hash == self._authority_hash(q, approval)
            and canonical_json(binding.payload.evaluations) == canonical_json(self._evaluations(state, plan.payload.targets)),
            "SCOPED_MODEL_BINDING_REPLAY_MISMATCH")
        return q, instance, approval, release, plan, binding

    def model_pin_candidate(self, *, scope_id, instance_id):
        from football_system.domain.pinned_model_source import OpenFootballQualifiedModelSourceV2
        with self._transaction() as session:
            q, instance, approval, release, plan, binding = self._binding(session, scope_id, instance_id)
            at = self._now()
            self._validate_targets(session, q, instance.payload.subject, at)
            require(canonical_json(self._live_proof(session, q, plan.payload.targets, at)) == canonical_json(plan.payload.live_input_proof), "SCOPED_LIVE_PROOF_CHANGED")
            self._model_authority(q, approval)
            source = OpenFootballQualifiedModelSourceV2(scope_id=scope_id, scope_hash=q.owner.scope_hash, instance_id=instance_id,
                release_id=release.artifact_id, release_hash=release.artifact_hash, binding_id=binding.artifact_id, binding_hash=binding.artifact_hash)
            return dict(status="SCOPED_MODEL_PIN_READY", model_source=source.model_dump(mode="json"), source_identity=q.payload.subject.profile.source_id,
                scope_match_ids=binding.payload.target_match_ids, state_hash=binding.payload.state_hash,
                training_data_hash=binding.payload.training_data_hash, real_model_pin_created=False)

    def read_model(self, session, request, at, *, existing_pin=False):
        """V2 graph adapter called by the single OpenFootballPinnedModelReader."""
        from football_system.domain.pinned_model_source import OpenFootballQualifiedModelSourceV2, VerifiedPinnedModelDescriptorV1
        from football_system.domain.market_analysis import MarketModelLineageV1
        from football_system.domain.real_bridge import RealObservationProgramV1
        from football_system.domain.services.elo_baseline import EloBaselineConfig
        require(session.get_bind() is self._sessions.kw["bind"], "SCOPED_SAME_TRANSACTION_REQUIRED")
        assert_scoped_head(session)
        source = OpenFootballQualifiedModelSourceV2.model_validate(request.model_source)
        q, instance, approval, release, plan, binding = self._binding(session, source.scope_id, source.instance_id)
        require(source.scope_hash == q.owner.scope_hash and (source.release_id, source.release_hash) == (release.artifact_id, release.artifact_hash)
            and (source.binding_id, source.binding_hash) == (binding.artifact_id, binding.artifact_hash)
            and request.source_identity == q.payload.subject.profile.source_id, "SCOPED_PIN_SOURCE_REFERENCE_MISMATCH")
        require(tuple(request.scope_match_ids) == binding.payload.target_match_ids, "SCOPED_PIN_COMPLETE_TARGET_SET_REQUIRED")
        program_id = request.program.artifact_id if existing_pin else request.program_id
        raw = session.execute(text("SELECT a.artifact_json FROM rb_artifacts a JOIN rb_seals z ON z.artifact_id=a.artifact_id WHERE a.artifact_id=:id AND a.schema_version='REAL_OBSERVATION_PROGRAM_V1'"), {"id": program_id}).scalar_one()
        program = RealObservationProgramV1.model_validate(strict_json_bytes(raw.encode()))
        expected = "SYNTHETIC_SOFTWARE_ACCEPTANCE" if q.payload.subject.classification == "SYNTHETIC_SOFTWARE_ACCEPTANCE" else "LIVE_OBSERVATION"
        require(program.provenance == expected, "SCOPED_SYNTHETIC_QUALIFICATION_NOT_LIVE")
        proof_at = request.event_at_utc if existing_pin else at
        require(binding.recorded_at_utc <= proof_at <= at, "SCOPED_PIN_STATE_FROM_FUTURE")
        self._validate_targets(session, q, instance.payload.subject, proof_at, historical=existing_pin)
        require(canonical_json(self._live_proof(session, q, plan.payload.targets, proof_at)) == canonical_json(plan.payload.live_input_proof), "SCOPED_PIN_LIVE_PROOF_CHANGED")
        self._model_authority(q, approval)
        state = release.payload.state
        return VerifiedPinnedModelDescriptorV1(source=source, state=state, configuration=EloBaselineConfig(),
            lineage=MarketModelLineageV1(model_name=state.model_name, model_version=state.model_version, state_id=binding.payload.model_state_id,
                state_hash=state.state_hash, config_hash=state.config_hash, training_data_hash=state.training_data_hash,
                training_cutoff_at_utc=state.cutoff_at_utc, generated_at_utc=binding.recorded_at_utc),
            release_hash=release.artifact_hash, authority_hash=binding.payload.authority_hash, scope_match_ids=binding.payload.target_match_ids,
            competition_id=q.owner.competition_id, season_id=q.payload.subject.profile.window[-1].season_id)
