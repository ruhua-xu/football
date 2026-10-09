"""P0 vertical regression: full 612-row source graph, genuine APIs, synthetic data.

No production data or network; the 599 facts and 13 UNKNOWN exception records
are produced by the unchanged qualification/admission path, never hand-relabelled.
"""
import asyncio
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json

import pytest
from sqlalchemy import text

from football_system.application.environment import RuntimeEnvironment
from football_system.application.live_ingestion import LiveFixtureIngestionService
from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_production import TEAM_LABELS,canonical_id
from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
from football_system.infrastructure.files.raw_archive import RawDataArchive
from football_system.infrastructure.providers.real.fixture_manual import (
    ReviewedFixtureManualArchiveProvider,load_reviewed_fixture_manual_archive,reviewed_fixture_manual_request,
)
from tests.integration import test_openfootball_production_binding as synthetic_source
from tests.integration.ofp_real_pin_fixtures import qualify_synthetic_source,add_complete_live_inputs
from tests.integration.openfootball_upgrade_support import sqlite_snapshot

source_graph=synthetic_source.graph

@pytest.fixture
def admitted(source_graph,monkeypatch):
    from football_system.domain.services.elo_baseline import EloThreeWayBaseline
    def forbidden(*args,**kwargs):
        raise AssertionError("P0_CATALOG_TEST_MUST_NOT_TRAIN_OR_FETCH")
    monkeypatch.setattr(EloThreeWayBaseline,"rebuild_state",forbidden)
    from football_system.infrastructure.http.urllib_transport import UrllibTransport
    monkeypatch.setattr(UrllibTransport,"send",forbidden)
    g=qualify_synthetic_source(source_graph,monkeypatch)
    g.binding=g.admit()
    with g.sessions() as s:
        assert s.execute(text("SELECT status,count(*) FROM matches GROUP BY status ORDER BY status")).all()==[("FINISHED",599),("UNKNOWN",13)]
    return g


def full_catalog(g,provider_codes=()):
    return SqlAlchemyMatchIdentityRepository(g.sessions,clock=g.clock).load_catalog(as_of_at_utc=g.clock(),
        kickoff_from_utc=datetime.min.replace(tzinfo=timezone.utc),kickoff_to_utc=datetime.max.replace(tzinfo=timezone.utc),provider_codes=provider_codes)


def history(g):
    snapshot=sqlite_snapshot(g.database)
    protected=("ofp_artifacts","ofp_parent_links","ofp_operations","ofp_phase_slots","ofp_canonical_entities","ofp_source_records","match_results")
    with g.sessions() as s:
        old_matches=s.execute(text("SELECT m.* FROM matches m JOIN ofp_source_records r ON r.canonical_match_id=m.internal_match_id ORDER BY m.internal_match_id")).all()
        old_identities=s.execute(text("SELECT i.* FROM canonical_match_identities i JOIN ofp_source_records r ON r.canonical_match_id=i.internal_match_id ORDER BY i.internal_match_id")).all()
    return dict(tables={name:snapshot["rows"][name] for name in protected},ddl={name:value for name,value in snapshot["schema"].items()},
        matches=[tuple(row) for row in old_matches],identities=[tuple(row) for row in old_identities],binding_json=canonical_json(g.repo.inspect("DATA_BINDING")))


@pytest.mark.parametrize("provider_codes",[(),("OPENFOOTBALL",)])
def test_complete_catalog_retains_all_599_finished_and_13_unknown(admitted,provider_codes):
    g=admitted
    before=sqlite_snapshot(g.database)
    catalog=full_catalog(g,provider_codes)
    assert len(catalog.canonical_matches)==len(catalog.canonical_anchors)==612
    assert Counter(a.match.status.value for a in catalog.canonical_anchors)=={"FINISHED":599,"UNKNOWN":13}
    assert all(a.match.status.value!="SCHEDULED" for a in catalog.canonical_anchors)
    assert sqlite_snapshot(g.database)==before
    binding=g.repo.inspect("DATA_BINDING")
    assert binding==g.binding and len(binding.payload["facts"])==599
    assert binding.payload["facts_root"]==g.binding.payload["facts_root"]


def test_source_backed_manual_fixture_preparation_through_full_catalog_preserves_history(admitted):
    g=admitted
    target,=add_complete_live_inputs(g)  # Formal self-authored source captures, no catalogue injection.
    frozen=history(g)
    before_binding=g.repo.inspect("DATA_BINDING")
    captured=g.clock()
    reviewed=g.clock()
    evidence=b"SELF_AUTHORED_SYNTHETIC_FIXTURE_IDENTITY_ACCEPTANCE_ONLY"
    (g.root/"fixture-source.txt").write_bytes(evidence)
    document=dict(schema_version="REVIEWED_FIXTURE_MANUAL_ARCHIVE_V1",fixtures=[dict(
        competition_label="Bundesliga",season=canonical_id("SEASON","2026/27"),kickoff_at_utc=target.kickoff_at_utc.isoformat(),
        home_team_label=TEAM_LABELS[0],away_team_label=TEAM_LABELS[3],competition_type="DOMESTIC_LEAGUE",team_type="CLUB",
        source_reference="synthetic://p0/source-backed-fixture",source_artifact_path="fixture-source.txt",source_artifact_sha256=hashlib.sha256(evidence).hexdigest(),
        captured_at_utc=captured.isoformat(),entered_by="synthetic-operator",review_level="SELF_REVIEWED",reviewed_by="synthetic-operator",reviewed_at_utc=reviewed.isoformat())])
    path=g.root/"reviewed-fixtures.json"
    path.write_text(json.dumps(document),encoding="utf-8")
    archive=load_reviewed_fixture_manual_archive(path)
    identity=SqlAlchemyMatchIdentityRepository(g.sessions,clock=g.clock)
    catalog=full_catalog(g)  # Exactly the formal CLI's complete catalogue path.
    assert len(catalog.canonical_anchors)==613
    provider=ReviewedFixtureManualArchiveProvider(archive,catalog,RawDataArchive(g.root/"manual-raw"))
    assert provider.runtime_provenance.environment is RuntimeEnvironment.LIVE and not provider.runtime_provenance.is_mock
    summary=asyncio.run(LiveFixtureIngestionService(provider,lambda:identity,environment=RuntimeEnvironment.LIVE).ingest(reviewed_fixture_manual_request(archive)))
    assert summary.inserted and summary.match_count==summary.observation_count==1
    with g.sessions() as s:
        observations=s.execute(text("SELECT internal_match_id,status FROM fixture_observations WHERE ingestion_id=:id"),{"id":summary.ingestion_id}).all()
        assert observations==[(target.match_id,"SCHEDULED")]
        assert s.scalar(text("SELECT count(*) FROM competitions"))==1
        assert s.scalar(text("SELECT count(*) FROM teams"))==20
        assert s.scalar(text("SELECT count(*) FROM matches"))==613
        assert s.scalar(text("SELECT count(*) FROM match_results"))==599
        assert s.scalar(text("SELECT count(*) FROM ofp_source_records WHERE included=0"))==13
        assert s.execute(text("PRAGMA foreign_key_check")).all()==[]
    assert history(g)==frozen
    assert g.repo.inspect("DATA_BINDING")==before_binding
    refreshed=full_catalog(g)
    assert Counter(a.match.status.value for a in refreshed.canonical_anchors)=={"FINISHED":599,"UNKNOWN":13,"SCHEDULED":1}
    proof=dict(status="PASS",provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE_ONLY",full_catalog_count=613,
        historical_counts=[612,13,599],historical_rows_and_artifact_bytes_unchanged=True,source_binding_hash=before_binding.artifact_hash,
        facts_root=before_binding.payload["facts_root"],mapping_root=before_binding.payload["subject"]["mapping_root"],
        training_window_hash=before_binding.payload["subject"]["training_window_hash"],fixture_ingestion_id=summary.ingestion_id,
        canonical_match_id=target.match_id,unknown_promoted_to_scheduled_or_finished=False,model_training_calls=0,production_touched=False)
    (g.root/"p0-fixture-preparation-proof.json").write_text(json.dumps(proof,indent=2),encoding="utf-8")


def test_unknown_historical_identity_is_not_a_result_or_ready_live_fixture(admitted):
    from football_system.application.live_sources import PrepareAnalysisRequest,PrepareLiveAnalysisService,LiveAnalysisInputPolicy
    from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository
    from football_system.infrastructure.database.real_bridge_sources import fixture_ref
    g=admitted
    frozen=history(g)
    anchor=next(a for a in full_catalog(g).canonical_anchors if a.match.status.value=="UNKNOWN")
    at=g.clock()
    preparation=PrepareLiveAnalysisService(SqlAlchemyLiveSourceRepository(g.sessions,clock=g.clock)).prepare(PrepareAnalysisRequest(
        decision_as_of_at_utc=at,kickoff_from_utc=anchor.match.kickoff_at_utc,kickoff_to_utc=anchor.match.kickoff_at_utc,
        competition_id=anchor.competition.competition_id,season_id=anchor.identity.season,expected_match_ids=(anchor.match.match_id,),
        allow_partial_inputs=False,policy=LiveAnalysisInputPolicy(maximum_odds_age_seconds=3600,minimum_bookmaker_count=2)))
    assert preparation.status.value=="NO_ANALYSIS_INSUFFICIENT_DATA"
    assert len(preparation.matches)==1 and preparation.matches[0].match_id==anchor.match.match_id
    assert preparation.matches[0].data_quality.identity_resolved
    assert not preparation.matches[0].data_quality.fixture_visible
    assert "FIXTURE_NOT_VISIBLE" in preparation.matches[0].reason_codes
    with g.sessions() as s:
        assert s.scalar(text("SELECT count(*) FROM match_results WHERE internal_match_id=:id"),{"id":anchor.match.match_id})==0
        with pytest.raises(ValueError,match="VISIBLE_FIXTURE_OBSERVATION_REQUIRED"):
            fixture_ref(s,anchor.match.match_id,at)
    assert history(g)==frozen
