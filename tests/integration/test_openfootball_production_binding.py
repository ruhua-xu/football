"""OP01-OP20. Invented rows/reviews in isolated SQLite; NO real-data training.

Only the already tested pinned-file acquisition gateway is replaced by a test
source. Date/score parsing, canonical allocation, SQL, reviews, frozen Elo and
all subsequent lifecycle guards execute normally. No production test bypass is
added to the application.
"""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from football_system.application import openfootball_production as app
from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_production import (
    CONFIG_HASH, EXCEPTIONS, SOURCE_ID, TEAM_LABELS, OpenFootballCanonicalReviewV1,
    OpenFootballModelApprovalPayloadV1, OpenFootballObservedFactV1, OpenFootballProductionTargetV1,
    OpenFootballSourceRightsPayloadV1, canonical_id, canonical_review, fixed_exceptions, fixed_window, validate_fact_cohort,
)
from football_system.domain.openfootball_snapshot import PINNED_FILES, TZIF_SHA256, OpenFootballAdapterPolicyV1
from football_system.domain.services.elo_baseline import EloBaselineConfig
from football_system.domain.training_admission import LocalReviewEvidenceV1, tagged_canonical_sha256
from football_system.infrastructure.database.models import MatchRecord, TeamRecord, CanonicalMatchIdentityRecord
from football_system.infrastructure.database.session import create_database_engine, create_schema, create_session_factory
from football_system.infrastructure.files.real_bridge import BridgeEvidence
from football_system.infrastructure.files.training_evidence import ReviewerAuthorityV1, TrainingReviewDocumentV1
from football_system.infrastructure.providers.real.openfootball_observed import inspect_openfootball_file
from football_system.interfaces.production_quant_cli import production_inference_context

AT = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = AT

    def __call__(self):
        self.value += timedelta(microseconds=1)
        return self.value


def put(root, name, value):
    raw = (canonical_json(value) + "\n").encode()
    (root / name).write_bytes(raw)
    return LocalReviewEvidenceV1(evidence_reference=name, evidence_sha256=hashlib.sha256(raw).hexdigest())


class Graph:
    def review(self, schema, digest, name):
        return put(self.root, name, TrainingReviewDocumentV1(attested_schema_version=schema, attested_payload_hash=digest,
            prepared_by="synthetic-operator", authorized_reviewer="synthetic-operator", reviewed_at_utc=self.clock(),
            source_ids=(SOURCE_ID,), source_classification="REAL_SOURCE_DATA", approved=True, retention_compatible=True))

    def admit(self):
        digest = tagged_canonical_sha256("OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", self.subject)
        self.data_review = self.review("OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", digest, "synthetic-data-review.json")
        return self.repo.record_data_binding(request_key="data", subject=self.subject,
            review=self.data_review.model_dump(), authority=self.authority.model_dump())

    def targets(self):
        values = []
        with self.sessions.begin() as session:
            session.add(TeamRecord(team_id="synthetic-new-team", canonical_key="synthetic-new-team", name="SYNTHETIC NEW TEAM", team_type="CLUB"))
            session.flush()
            for index in range(2):
                mid = f"synthetic-future-{index}"
                home = canonical_id("TEAM", TEAM_LABELS[0]) if index == 0 else "synthetic-new-team"
                away = canonical_id("TEAM", TEAM_LABELS[3])
                kickoff = AT + timedelta(days=7)
                session.add(MatchRecord(internal_match_id=mid, competition_id=canonical_id("COMPETITION", "Deutsche Bundesliga"),
                    home_team_id=home, away_team_id=away, kickoff_at_utc=kickoff, status="SCHEDULED",
                    available_at_utc=AT, created_at_utc=AT, fixture_ingestion_id=None))
                session.flush()
                session.add(CanonicalMatchIdentityRecord(internal_match_id=mid, season=canonical_id("SEASON", "2026/27"),
                    competition_type="DOMESTIC_LEAGUE", available_at_utc=AT, fixture_ingestion_id=None))
                values.append(OpenFootballProductionTargetV1(match_id=mid, competition_id=canonical_id("COMPETITION", "Deutsche Bundesliga"),
                    season_id=canonical_id("SEASON", "2026/27"), home_team_id=home, away_team_id=away, kickoff_at_utc=kickoff,
                    live_preparation_id="synthetic-preparation", fixture_observation_id=f"synthetic-observation-{index}"))
        return values

    def through_manifest(self):
        self.binding = self.admit()
        self.cutoff = self.repo.seal_cutoff("cutoff")
        self.target_values = self.targets()
        self.plan = self.repo.seal_pilot_plan(request_key="plan", targets=self.target_values)
        self.report = self.repo.run_pilot(request_key="pilot")
        self.attestation = self.repo.attest_pilot("attest")
        self.manifest = self.repo.create_manifest("manifest")
        return self.manifest


@pytest.fixture
def graph(tmp_path, monkeypatch):
    g = Graph()
    g.root, g.clock = tmp_path, Clock()
    policy = OpenFootballAdapterPolicyV1(timezone="Europe/Berlin", tzdata_version="2025.2", iana_version="2025b", tzif_sha256=TZIF_SHA256)
    reports = []
    for number, filename in enumerate(EXCEPTIONS):
        omitted = {"1. FC Köln", "Hamburger SV"} if number == 0 else {"Holstein Kiel", "VfL Bochum 1848"}
        teams = [t for t in TEAM_LABELS if t not in omitted]
        pairs = [(h, a) for h in teams for a in teams if h != a]
        rows = []
        for index, (home, away) in enumerate(pairs):
            row = dict(round=f"Matchday {index // 9 + 1}", date=(datetime(2024 + number, 8, 1) + timedelta(days=index//9)).date().isoformat(),
                time="15:30", team1=home, team2=away, score={"ft": [index % 3, (index + 1) % 3]})
            if f"/matches/{index}" in EXCEPTIONS[filename]:
                if number == 0:
                    row["status"] = "awarded"
                else:
                    row["score"] = [0, 0]
            rows.append(row)
        raw = json.dumps(dict(name=f"Deutsche Bundesliga {2024+number}/{str(2025+number)[-2:]}", matches=rows)).encode()
        (tmp_path / filename).parent.mkdir(exist_ok=True)
        (tmp_path / filename).write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        # Test-only invented source identity; application has no override flag.
        monkeypatch.setitem(PINNED_FILES, filename, (len(raw), digest))
        reports.append(inspect_openfootball_file(raw, filename=filename, capture_at_utc=AT-timedelta(days=1), policy=policy))
    supplied = dict(scope=dict(adapter_policy=policy.model_dump(mode="json")), scope_hash="a" * 64,
        datasets=reports, rights_candidate=dict(license_sha256="36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673"))
    monkeypatch.setattr(app, "qualify_private_root", lambda root, policy: copy.deepcopy(supplied))
    g.engine = create_database_engine("sqlite://")
    create_schema(g.engine)
    g.sessions = create_session_factory(g.engine)
    authority = ReviewerAuthorityV1(issued_by="synthetic-owner", authorized_reviewer="synthetic-operator", source_ids=(SOURCE_ID,),
        attested_schema_versions=("OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", "OPENFOOTBALL_MODEL_APPROVAL_PAYLOAD_V1"),
        effective_at_utc=AT-timedelta(days=2), expires_at_utc=AT+timedelta(days=30))
    g.authority = put(tmp_path, "synthetic-authority.json", authority)
    g.evidence = BridgeEvidence(tmp_path, trusted_authorities={g.authority.evidence_reference:g.authority.evidence_sha256}, max_bytes=8*1024*1024)
    _, _, g.production, g.inference, _ = production_inference_context(g.sessions, evidence=g.evidence, operator_id="synthetic-operator", clock=g.clock)
    g.repo = g.production.openfootball_binding()
    directive = put(tmp_path, "synthetic-directive.json", dict(classification="SYNTHETIC_CONTRACT_TEST_ONLY", no_real_authority=True))
    mapping = canonical_review(reviewer="synthetic-operator", reviewed_at=g.clock(), evidence_reference=directive.evidence_reference, evidence_sha256=directive.evidence_sha256)
    rights = OpenFootballSourceRightsPayloadV1(effective_at_utc=AT-timedelta(days=1), expires_at_utc=AT+timedelta(days=20))
    g.subject = g.repo.prepare_data_binding(policy=policy, mapping=mapping, rights_payload=rights, directive_evidence=directive)
    yield g
    g.engine.dispose()


def test_op01_exact_306_306_scope(graph):
    assert graph.subject["expected_counts"] == {"2024/25":306, "2025/26":306}
    assert len(graph.subject["source_records"]) == 612


def test_op02_exact_exceptions_1_12(graph):
    assert graph.subject["exceptions"] == list(fixed_exceptions())
    assert sum(not r["included"] for r in graph.subject["source_records"] if r["source_filename"].startswith("2024")) == 1
    assert sum(not r["included"] for r in graph.subject["source_records"] if r["source_filename"].startswith("2025")) == 12


def test_op03_no_silent_exception_removal(graph):
    changed = copy.deepcopy(graph.subject)
    changed["exceptions"].pop()
    with pytest.raises(ValueError, match="SOURCE_SCOPE_MAPPING_OR_BYTES_CHANGED"):
        graph.repo._check_subject(changed)
    changed = copy.deepcopy(graph.subject)
    changed["source_records"].pop()
    with pytest.raises(ValueError):
        graph.repo._check_subject(changed)


def test_op04_all_canonical_mappings_explicit(graph):
    mapping = OpenFootballCanonicalReviewV1.model_validate(graph.subject["mapping"])
    assert len(mapping.entries) == 24
    assert len({e.canonical_id for e in mapping.entries}) == 24
    assert all(e.source_label != e.canonical_id and e.review_evidence_sha256 and e.content_hash for e in mapping.entries)
    with pytest.raises(ValueError, match="IDENTITY_UNRESOLVED"):
        canonical_id("TEAM", "fc bayern münchen")
    with pytest.raises(ValueError):
        OpenFootballCanonicalReviewV1.model_validate(mapping.model_dump() | {"entries": mapping.entries[:-1]})


def test_op05_source_hash_replay(graph):
    path = graph.root / "2024-25/de.1.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        graph.repo._check_subject(graph.subject)


def test_op06_berlin_replay(graph):
    for row in graph.subject["source_records"]:
        local = row["inspection"]["provenance"]["local_time_text"]
        utc = row["inspection"]["kickoff_at_utc"]
        assert local == "15:30" and "T13:30:00+00:00" in utc


def test_op07_unknown_provider_times(graph):
    facts = app.facts_at_admission(graph.subject, graph.clock())
    assert all(f.provider_publication_at_utc is None and f.provider_finalized_at_utc is None and f.retrospective for f in facts)
    with pytest.raises(ValueError):
        OpenFootballObservedFactV1.model_validate(facts[0].model_dump() | {"provider_publication_at_utc":AT})


def test_op08_exact_599_facts(graph):
    artifact = graph.admit()
    assert len(artifact.payload["facts"]) == 599
    with graph.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM ofp_source_records")) == 612
        assert session.scalar(text("SELECT sum(included) FROM ofp_source_records")) == 599
        assert session.scalar(text("SELECT count(*) FROM match_results")) == 599
        assert session.scalar(text("SELECT count(*) FROM ofp_canonical_entities")) == 24


def test_op09_training_window_roles_exact(graph):
    w = fixed_window()
    assert tuple(s.role for s in w.content_payload.seasons) == ("WARMUP", "PILOT_TARGET", "PRODUCTION_TARGET")
    changed = copy.deepcopy(graph.subject)
    changed["training_window"]["content_payload"]["seasons"].reverse()
    with pytest.raises(ValueError):
        graph.repo._check_subject(changed)


def test_op10_production_season_zero_fact(graph):
    facts = app.facts_at_admission(graph.subject, graph.clock())
    assert all(f.season_id != canonical_id("SEASON", "2026/27") for f in facts)
    with pytest.raises(ValueError, match="SEASON_MEMBERSHIP"):
        OpenFootballObservedFactV1.model_validate(facts[0].model_dump() | {"season_id":canonical_id("SEASON", "2026/27")})


def test_op11_all_operation_targets_excluded(graph):
    facts = app.facts_at_admission(graph.subject, graph.clock())
    with pytest.raises(ValueError, match="OPERATION_TARGET"):
        validate_fact_cohort(facts, window=fixed_window(), exclusions=(facts[0].match_id,))
    with pytest.raises(ValueError, match="FUTURE_TARGET_SCOPE_REQUIRED"):
        graph.repo.seal_pilot_plan(request_key="none", targets=[])


def test_op12_no_tuning(graph):
    assert EloBaselineConfig().config_hash == CONFIG_HASH
    changed = copy.deepcopy(graph.subject)
    changed["config_hash"] = "a" * 64
    with pytest.raises(ValueError):
        graph.repo._check_subject(changed)


def test_op13_deterministic_state_and_unavailable_accounting(graph):
    facts = app.facts_at_admission(graph.subject, graph.clock())
    cutoff = graph.clock()
    a = app.structural_replay(facts, cutoff=cutoff, excluded_match_ids=("future",))
    b = app.structural_replay(facts[::-1], cutoff=cutoff, excluded_match_ids=("future",))
    assert a == b and a["fact_count"] == 599 and a["determinism"] == "PASS"
    assert sum(a["availability_accounting"]["prior_match_counts"].values()) == 1198


def test_op14_exact_retry_and_append_only(graph):
    first = graph.admit()
    retry = graph.repo.record_data_binding(request_key="data", subject=graph.subject,
        review=graph.data_review.model_dump(), authority=graph.authority.model_dump())
    assert retry == first
    with pytest.raises(ValueError, match="PHASE_ALREADY"):
        graph.repo.record_data_binding(request_key="different", subject=graph.subject,
            review=graph.data_review.model_dump(), authority=graph.authority.model_dump())
    with pytest.raises(Exception, match="immutable"):
        with graph.sessions.begin() as session:
            session.execute(text("DELETE FROM ofp_source_records"))


def test_op15_authority_required(graph):
    graph.evidence.trusted_authorities.clear()
    with pytest.raises(ValueError, match="not pinned"):
        graph.admit()
    with graph.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM ofp_artifacts")) == 0


def test_op16_reviewer_attestation_exact_hash(graph):
    review = graph.review("OPENFOOTBALL_DATA_REVIEW_PAYLOAD_V1", "b"*64, "wrong-review.json")
    with pytest.raises(ValueError):
        graph.repo.record_data_binding(request_key="data", subject=graph.subject, review=review.model_dump(), authority=graph.authority.model_dump())


def test_op17_historical_metrics_unavailable(graph):
    facts = app.facts_at_admission(graph.subject, graph.clock())
    result = app.structural_replay(facts, cutoff=graph.clock(), excluded_match_ids=())
    validation = result["historical_validation"]
    assert validation["reason"] == "UNPROVEN_HISTORICAL_VERSION_TIME" and validation["metrics"] is None
    assert all(validation[k] == "UNAVAILABLE" for k in ("historical_brier", "historical_logloss", "historical_calibration", "walk_forward_historical_availability"))


def test_op18_manifest_completeness(graph):
    manifest = graph.through_manifest()
    assert (manifest.payload["source_record_count"], manifest.payload["exception_count"], manifest.payload["fact_count"]) == (612,13,599)
    assert len(manifest.parents) == 4
    with graph.repo._transaction() as session:
        graph.repo._manifest(session)
    assert graph.repo.run_pilot(request_key="pilot") == graph.report
    with pytest.raises(ValueError, match="PHASE_ALREADY"):
        graph.repo.run_pilot(request_key="second-pilot")


def test_op19_approval_release_integrity_and_all_targets_retained(graph, monkeypatch):
    graph.through_manifest()
    payload = graph.repo.prepare_approval(effective_at_utc=graph.clock(), expires_at_utc=AT+timedelta(days=10))
    for change in ({"manifest_hash":"0"*64}, {"state_hash":"0"*64}):
        with graph.repo._transaction() as session:
            with pytest.raises(ValueError):
                graph.repo._approval_subject(session, OpenFootballModelApprovalPayloadV1.model_validate(payload.model_dump() | change))
    with pytest.raises(ValueError, match="APPROVAL"):
        graph.repo.build_release("release-before-approval")
    review = graph.review(payload.schema_version, payload.content_hash, "synthetic-model-review.json")
    approval = graph.repo.record_approval(request_key="approval", payload=payload, review=review.model_dump(), authority=graph.authority.model_dump())
    release = graph.repo.build_release("release")
    assert tuple(release.payload["approval"]) == approval.reference()
    assert graph.repo.build_release("release") == release
    assert graph.repo.inspect("RELEASE") == release
    with pytest.raises(ValueError, match="COMPLETE_LIVE_TARGET_INPUTS_REQUIRED"):
        graph.repo.create_target_plan("target")
    # Synthetic live-source verifier fixture only. Real CLI cannot supply a verifier.
    monkeypatch.setattr(graph.repo, "_live_target_proof", lambda session, targets, at: [dict(synthetic=True, ids=[t.match_id for t in targets])])
    target = graph.repo.create_target_plan("target")
    bound = graph.repo.bind_model_state("model-binding")
    assert len(bound.payload["target_scope"]) == len(bound.payload["evaluations"]) == 2
    assert {r["prediction"]["status"] for r in bound.payload["evaluations"]} == {"AVAILABLE", "UNAVAILABLE"}
    assert tuple(bound.payload["target_plan"]) == target.reference()
    candidate = graph.repo.load_model_pin_candidate()
    assert candidate["real_model_pin_created"] is False and len(candidate["target_scope"]) == 2


def test_op20_old_v1_sportmonks_exact_regression():
    from football_system.domain.observed_training import CurrentSnapshotCollectionScopeV1
    from football_system.infrastructure.files.real_bridge_frozen import verify_frozen_checkout
    root = Path(__file__).resolve().parents[2]
    assert CurrentSnapshotCollectionScopeV1.model_json_schema()["properties"]["provider_code"]["const"] == "SPORTMONKS"
    assert len(verify_frozen_checkout(root)) == 211


def test_openfootball_normalized_rows_cannot_enter_bare_or_legacy_bound_training(graph):
    from types import SimpleNamespace
    from football_system.infrastructure.database.repositories import SqlAlchemyAnalysisRepository
    binding = graph.admit()
    fact = OpenFootballObservedFactV1.model_validate(binding.payload["facts"][0]).to_elo_result()
    with graph.sessions() as session:
        for claimed_binding in (None, object()):
            artifacts = SimpleNamespace(production_binding=claimed_binding,
                quant_model_states=(SimpleNamespace(training_facts=(SimpleNamespace(match_result_id=fact.match_result_id),)),))
            with pytest.raises(ValueError, match="OPENFOOTBALL_OBSERVED_BINDING_REQUIRED"):
                SqlAlchemyAnalysisRepository._assert_model_training_sources(None, session, artifacts)
    with pytest.raises(Exception, match="OpenFootball results require the versioned production binding"):
        with graph.sessions.begin() as session:
            session.execute(text("INSERT INTO quant_model_training_facts(match_result_id) VALUES (:result)"), {"result":fact.match_result_id})
