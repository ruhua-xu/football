"""Self-authored two-competition acceptance inputs; real qualification APIs execute."""
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.resources import files
from io import BytesIO
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.openfootball_scope import CompetitionScopeProfileV2
from football_system.domain.openfootball_scoped import ScopedParserPolicyV2, ScopedSourceRightsV2, ScopedAcquisitionV2
from football_system.domain.training_admission import LocalReviewEvidenceV1
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.infrastructure.files.training_evidence import ReviewerAuthorityV1, TrainingReviewDocumentV1
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.interfaces.production_quant_cli import production_inference_context
from football_system.interfaces.cli import _resource_root


class Clock:
    def __init__(self, at=None):
        self.value = at or datetime(2030, 1, 1, 9, tzinfo=timezone.utc)

    def __call__(self):
        self.value += timedelta(microseconds=1)
        return self.value


def write(root, name, value):
    raw = value if isinstance(value, bytes) else (canonical_json(value) + "\n").encode()
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return LocalReviewEvidenceV1(evidence_reference=name, evidence_sha256=hashlib.sha256(raw).hexdigest())


def review(g, subject, source_id, name):
    return write(g.root, name, TrainingReviewDocumentV1(attested_schema_version=subject.schema_version,
        attested_payload_hash=subject.content_hash, prepared_by="synthetic-operator", authorized_reviewer="synthetic-reviewer",
        reviewed_at_utc=g.clock(), source_ids=(source_id,), source_classification="REAL_SOURCE_DATA", approved=True, retention_compatible=True))


def graph(root):
    root.mkdir(parents=True, exist_ok=True)
    g = SimpleNamespace(root=root, clock=Clock())
    g.database = root / "isolated-scoped.sqlite"
    url = "sqlite:///" + g.database.as_posix()
    upgrade_database(url, _resource_root() / "alembic.ini")
    g.engine = create_database_engine(url)
    g.sessions = create_session_factory(g.engine)
    g.evidence = BridgeEvidence(root, trusted_authorities={})
    _, _, g.production, g.inference, _ = production_inference_context(g.sessions, evidence=g.evidence,
        operator_id="synthetic-operator", clock=g.clock)
    g.repo = g.production.openfootball_scoped_binding()
    return g


def source(g, label="A", team_count=4, zone="Europe/Berlin"):
    prefix = "source-" + label
    at = g.clock.value
    captured = at - timedelta(days=1)
    comp = "synthetic-competition-" + label
    teams = ["SYNTHETIC " + label + " TEAM " + str(i) for i in range(team_count)]
    ids = {team: stable_id("SCOPED_SYNTHETIC_TEAM", label, team) for team in teams}
    policy = ScopedParserPolicyV2()
    parser = write(g.root, prefix + "/parser.json", policy)
    notes = write(g.root, prefix + "/evidence.json", dict(classification="SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE_ONLY",
        competition=comp, teams=teams, time_semantics="SELF_AUTHORED_LOCAL_WALL_TIME", timezone=zone,
        score_semantics="EXPLICIT_FT_FOR_NORMAL_ROWS_DIRECT_ARRAY_UNPROVEN", no_real_qualification=True))
    evidence = dict(evidence_id=notes.evidence_reference, evidence_sha256=notes.evidence_sha256)
    tzif = files("tzdata.zoneinfo").joinpath(*zone.split("/")).read_bytes()
    timezone_object = ZoneInfo.from_file(BytesIO(tzif), key=zone)
    manifest, records = [], []
    window = [dict(sequence=i, season_id=f"synthetic-{label}-season-{i}", source_label=season, role=role)
        for i, (season, role) in enumerate((("2024/25", "WARMUP"), ("2025/26", "PILOT_TARGET"), ("2030/31", "PRODUCTION_TARGET")))]
    for season in range(2):
        filename = f"{2024+season}-{str(2025+season)[-2:]}/test.{label}.json"
        rows = []
        pairs = [(h, a) for h in teams for a in teams if h != a]
        for i, (home, away) in enumerate(pairs):
            local = datetime(2024+season, 8, 1, 15, 30) + timedelta(days=i)
            row = dict(round=f"Matchday {i+1}", date=local.date().isoformat(), time="15:30", team1=home, team2=away,
                score={"ft": [i % 3, (i+1) % 3]})
            exception = season == 1 and i == len(pairs)-1
            if exception:
                row["score"] = [0, 0]
            rows.append(row)
            fact = None if exception else dict(match_result_id=f"declared-{label}-{season}-{i}", match_id=f"history-{label}-{season}-{i}",
                season_id=window[season]["season_id"], home_team_id=ids[home], away_team_id=ids[away],
                kickoff_at_utc=local.replace(tzinfo=timezone_object).astimezone(timezone.utc), available_at_utc=captured,
                ingested_at_utc=captured, home_goals=i % 3, away_goals=(i+1) % 3, payload_hash=match_result_payload_sha256(i % 3, (i+1) % 3))
            records.append(dict(file=filename, index=i, raw_record_sha256=hashlib.sha256(canonical_json(row).encode()).hexdigest(),
                disposition="EXCEPTION" if exception else "FACT_CANDIDATE", candidate_result=fact,
                reason="REPORTED_SCORE_PERIOD_UNPROVEN" if exception else None, review=evidence))
        raw = (canonical_json(dict(name="Synthetic league " + label + " " + window[season]["source_label"], matches=rows)) + "\n").encode()
        write(g.root, prefix + "/" + filename, raw)
        manifest.append(dict(path=filename, role="MATCHES", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
            record_count=len(rows), season_id=window[season]["season_id"], captured_at_utc=captured))
    for name, role, raw in (("README.md", "README", b"Self-authored synthetic explicit ft regular-time records.\n"),
        ("LICENSE.md", "LICENSE", b"Test owner permits this synthetic local software acceptance evidence.\n")):
        write(g.root, prefix + "/" + name, raw)
        manifest.append(dict(path=name, role=role, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), record_count=0, captured_at_utc=captured))
    mapping = [dict(kind=kind, source_label=source_label, canonical_id=cid, method="EXPLICIT_NEW_REVIEWED", evidence=evidence)
        for kind, source_label, cid in [("COMPETITION", "Synthetic league " + label, comp),
            *(("TEAM", team, ids[team]) for team in teams), *(("SEASON", w["source_label"], w["season_id"]) for w in window)]]
    p = CompetitionScopeProfileV2(competition_id=comp, source_competition_id="test." + label, source_competition_label="Synthetic league " + label,
        commit=hashlib.sha256(("self-authored-" + label).encode()).hexdigest()[:40], files=sorted(manifest, key=lambda f: f["path"]),
        parser_policy_hash=policy.content_hash, time_policy=dict(timezone=zone, tzif_sha256=hashlib.sha256(tzif).hexdigest(), source_time_review=evidence),
        mapping=sorted(mapping, key=lambda m: (m["kind"], m["source_label"])), window=window, records=records, cohort_review=evidence)
    acquisition = write(g.root, prefix + "/acquisition.json", ScopedAcquisitionV2(source_id=p.source_id, commit=p.commit,
        classification="SYNTHETIC_SOFTWARE_ACCEPTANCE", files=tuple(dict(path=f.path, bytes=f.bytes, sha256=f.sha256,
            captured_at_utc=f.captured_at_utc, source_uri=f"synthetic://{p.source_id}/{f.path}") for f in p.files)))
    rights = ScopedSourceRightsV2(source_id=p.source_id, profile_hash=p.content_hash,
        license_sha256=next(f.sha256 for f in p.files if f.role == "LICENSE"), effective_at_utc=at-timedelta(days=2), expires_at_utc=at+timedelta(days=30))
    authority = write(g.root, prefix + "/authority.json", ReviewerAuthorityV1(issued_by="synthetic-owner", authorized_reviewer="synthetic-reviewer",
        source_ids=(p.source_id,), attested_schema_versions=("OPENFOOTBALL_SCOPE_QUALIFICATION_REVIEW_V2", "OPENFOOTBALL_SCOPED_TARGET_REVIEW_V2",
            "OPENFOOTBALL_SCOPED_MODEL_APPROVAL_V2"), effective_at_utc=at-timedelta(days=3), expires_at_utc=at+timedelta(days=31)))
    g.evidence.trusted_authorities[authority.evidence_reference] = authority.evidence_sha256
    subject = g.repo.prepare_scope(profile=p, country_code="DE" if label == "A" else "GB", rights=rights, source_prefix=prefix, acquisition=acquisition,
        parser_policy=parser, classification="SYNTHETIC_SOFTWARE_ACCEPTANCE")
    reviewed = review(g, subject, p.source_id, prefix + "/qualification-review.json")
    return SimpleNamespace(profile=p, subject=subject, review=reviewed, authority=authority, prefix=prefix, label=label, team_ids=ids)


def qualify(g, s):
    return g.repo.record_scope(request_key="qualification", subject=s.subject, review=s.review, authority=s.authority)


def live_inputs(g, s, *, kickoff_hours=4):
    """Four distinct canonical teams, normal fixture/market/SP/preparation APIs."""
    from scripts import real_bridge_acceptance as inputs
    from football_system.application.identity_catalog import FixtureIngestionCapture
    from football_system.application.live_sources import MarketOddsIngestionCapture, SportteryIngestionCapture, PrepareAnalysisRequest, PrepareLiveAnalysisService, LiveAnalysisInputPolicy
    from football_system.application.market_consensus import derive_market_consensus
    from football_system.domain.match import MarketOddsSnapshot
    from football_system.domain.openfootball_production import OpenFootballProductionTargetV1
    from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
    from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository
    at = g.clock()
    kickoff = at + timedelta(hours=kickoff_hours)
    shift = at - inputs.AT
    season = s.profile.window[-1].season_id
    names_by_id = {cid: name for name, cid in s.team_ids.items()}
    team_ids = tuple(s.team_ids.values())
    mids, captures, fixture_rows = [], [], []
    def digest(value):
        return hashlib.sha256(canonical_json(value).encode()).hexdigest()
    for index in range(2):
        suffix = s.label + str(index)
        mid = "scoped-future-" + suffix
        home, away = team_ids[index*2:index*2+2]
        replacements = {f"rb-match-{suffix}": mid, f"rb-home-{suffix}": home, f"rb-away-{suffix}": away,
            "rb-competition": s.profile.competition_id, "rb-league": "scoped-fixture-league-" + s.label,
            "2026/27": season, "Software acceptance league": s.profile.source_competition_label}
        def remap(value):
            if isinstance(value, datetime):
                if value == inputs.KICKOFF:
                    return kickoff
                if value == inputs.AT - timedelta(hours=1):
                    return at - timedelta(minutes=45)
                return value + shift
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, dict):
                return {k: remap(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [remap(v) for v in value]
            return value
        raw = remap(inputs.fixture_capture(suffix).model_dump())
        raw["registration"]["competitions"][0].update(canonical_key="ofpv2:competition:" + s.profile.competition_id,
            country_code=s.subject.country_code)
        for team in raw["registration"]["teams"]:
            team.update(canonical_key="ofpv2:team:" + team["team_id"], name=names_by_id[team["team_id"]])
        for alias in raw["registration"]["team_aliases"]:
            alias["alias"]["provider_team_name"] = names_by_id[alias["internal_team_id"]]
        raw["registration"]["canonical_matches"][0]["identity"]["competition_type"] = "DOMESTIC_LEAGUE"
        raw["registration"]["competition_mappings"][0]["mapping"]["competition_type"] = "DOMESTIC_LEAGUE"
        raw["request"]["competition_type"] = "DOMESTIC_LEAGUE"
        raw["request"]["kickoff_from_utc"] = kickoff - timedelta(seconds=1)
        raw["request"]["kickoff_to_utc"] = kickoff + timedelta(seconds=1)
        raw["raw_payload_sha256"] = digest(raw["registration"])
        fixture = FixtureIngestionCapture.model_validate(raw)
        SqlAlchemyMatchIdentityRepository(g.sessions, clock=g.clock).register_fixture_ingestion(fixture)
        raw = remap(inputs.market_capture(suffix).model_dump())
        snapshots = tuple(MarketOddsSnapshot.model_validate(v) for v in raw["source_batch"]["snapshots"])
        consensus, mapping, lineage = derive_market_consensus(mid, snapshots)
        original = raw["source_batch"]["mappings"][0]
        original["mapping_id"] = stable_id("provider-mapping", original["provider_code"], original["external_namespace"], original["external_match_id"])
        raw["artifact"]["payload_sha256"] = digest(snapshots)
        raw["consensus_batch"] = dict(snapshots=(consensus,), mappings=(mapping,), issues=())
        raw["consensus_lineages"] = (lineage,)
        market = MarketOddsIngestionCapture.model_validate(raw)
        raw = remap(inputs.sp_capture(suffix).model_dump())
        number = "SYN" + s.label + str(index+1).zfill(3)
        raw["batch"]["snapshots"][0]["sporttery_match_no"] = number
        original = raw["batch"]["mappings"][0]
        original["external_match_id"] = at.date().isoformat() + ":" + number
        original["mapping_id"] = stable_id("provider-mapping", "SPORTTERY_MANUAL", "sporttery_match", original["external_match_id"])
        raw["provenance"][0].update(match_number_date=at.date(), sporttery_match_no=number)
        raw["artifacts"][0]["payload_sha256"] = digest(raw["batch"]["snapshots"])
        sporttery = SportteryIngestionCapture.model_validate(raw)
        repository = SqlAlchemyLiveSourceRepository(g.sessions, clock=g.clock)
        repository.save_market_odds_ingestion(market)
        repository.save_sporttery_ingestion(sporttery)
        mids.append(mid)
        captures.append((market, sporttery))
        fixture_rows.append((mid, home, away, fixture.observations[0].observation_id))
    live = SqlAlchemyLiveSourceRepository(g.sessions, clock=g.clock)
    prep = PrepareLiveAnalysisService(live).prepare(PrepareAnalysisRequest(decision_as_of_at_utc=g.clock(),
        kickoff_from_utc=kickoff-timedelta(seconds=1), kickoff_to_utc=kickoff+timedelta(seconds=1), competition_id=s.profile.competition_id,
        season_id=season, expected_match_ids=tuple(mids), allow_partial_inputs=False,
        policy=LiveAnalysisInputPolicy(maximum_odds_age_seconds=3600, minimum_bookmaker_count=2)))
    assert prep.status.value == "ANALYSIS_INPUT_READY", canonical_json(prep)
    s.targets = tuple(OpenFootballProductionTargetV1(match_id=mid, competition_id=s.profile.competition_id, season_id=season,
        home_team_id=home, away_team_id=away, kickoff_at_utc=kickoff, live_preparation_id=prep.preparation_id,
        fixture_observation_id=observation) for mid, home, away, observation in fixture_rows)
    s.kickoff, s.captures = kickoff, tuple(captures)
    return s.targets


def through_model(g, s, *, kickoff_hours=4):
    s.qualification = qualify(g, s)
    live_inputs(g, s, kickoff_hours=kickoff_hours)
    subject = g.repo.prepare_instance(scope_id=s.qualification.owner.scope_id, targets=s.targets,
        kickoff_from_utc=s.kickoff-timedelta(seconds=1), kickoff_to_utc=s.kickoff+timedelta(seconds=1))
    reviewed = review(g, subject, s.profile.source_id, s.prefix + "/target-review.json")
    s.instance = g.repo.record_instance(request_key="instance", subject=subject, review=reviewed, authority=s.authority)
    context = dict(scope_id=s.qualification.owner.scope_id, instance_id=s.instance.instance_id)
    s.pilot = g.repo.run_pilot(**context, request_key="pilot")
    s.attestation = g.repo.attest_pilot(**context, request_key="attestation")
    s.manifest = g.repo.create_manifest(**context, request_key="manifest")
    approval = g.repo.prepare_approval(**context, effective_at_utc=g.clock(), expires_at_utc=g.clock()+timedelta(days=10))
    reviewed = review(g, approval, s.profile.source_id, s.prefix + "/model-review.json")
    s.approval = g.repo.record_approval(**context, request_key="approval", subject=approval, review=reviewed, authority=s.authority)
    s.release = g.repo.build_release(**context, request_key="release")
    s.plan = g.repo.create_target_plan(**context, request_key="target-plan")
    s.binding = g.repo.bind_model_state(**context, request_key="state-binding")
    s.candidate = g.repo.model_pin_candidate(**context)
    return s


class BridgeClock:
    basis = "SYNTHETIC_TEST_CLOCK"
    def __init__(self, clock):
        self.clock = clock
    def now(self):
        return self.clock()


def create_program_and_pin(g, s):
    from football_system.application.real_bridge_requests import REQUEST_TYPES
    from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
    from football_system.infrastructure.database.real_bridge_sources import ExistingPinnedModelAccess
    if not hasattr(g, "bridge"):
        g.bridge = SqlAlchemyRealBridgeRepository(g.sessions, evidence_root=g.root, clock=BridgeClock(g.clock),
            model_access=ExistingPinnedModelAccess(g.inference))
    def call(operation, key, **values):
        return g.bridge.execute(operation, REQUEST_TYPES[operation](request_key=s.label + ":" + key, **values))
    s.call = call
    s.program = call("program", "program", observation_program_id="SCOPED_SYNTHETIC_" + s.label,
        competition_ids=(s.profile.competition_id,), season_id=s.profile.window[-1].season_id,
        scope_reference="SELF_AUTHORED_SYNTHETIC_PERSISTED_QUALIFICATION", provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE")
    proof = write(g.root, s.prefix + "/model-admission.txt", b"SYNTHETIC MODEL ADMISSION; no real production permission.\n")
    s.admission = call("admission", "model-admission", program_id=s.program.artifact_id, kind="MODEL", source_identity=s.profile.source_id,
        state="ADMITTED", effective_at_utc=g.clock(), expires_at_utc=g.clock()+timedelta(days=9), rights_reference="self-authored synthetic acceptance",
        verified_by="synthetic-operator", evidence_file=proof.evidence_reference, evidence_hash=proof.evidence_sha256)
    s.pin_request = REQUEST_TYPES["model-pin"](request_key=s.label + ":pin", program_id=s.program.artifact_id, admission_id=s.admission.artifact_id,
        source_identity=s.profile.source_id, scope_match_ids=s.candidate["scope_match_ids"], model_source=s.candidate["model_source"])
    s.pin = g.bridge.execute("model-pin", s.pin_request)
    return s.pin
