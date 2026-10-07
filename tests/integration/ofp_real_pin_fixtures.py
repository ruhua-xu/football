"""Self-authored OFP/live-input fixture using normal persistence/verification APIs.

No production paths, provider transport, real targets, test_state pins, or patched
live/model/approval verifiers. The existing OFP graph supplies its synthetic
acquisition boundary; all source parsing/599 admission and subsequent APIs run.
"""
from datetime import datetime, timedelta
import hashlib

from scripts import real_bridge_acceptance as inputs
from football_system.application.identity_catalog import FixtureIngestionCapture
from football_system.application.live_sources import (
    MarketOddsIngestionCapture, SportteryIngestionCapture, PrepareAnalysisRequest,
    PrepareLiveAnalysisService, LiveAnalysisInputPolicy,
)
from football_system.application.market_consensus import derive_market_consensus
from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id
from football_system.domain.match import MarketOddsSnapshot
from football_system.domain.openfootball_production import SOURCE_ID, TEAM_LABELS, canonical_id, OpenFootballProductionTargetV1
from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository


def qualify_synthetic_source(g, monkeypatch):
    """Replace only invented match-byte pins, not the qualification verifier.

    The source_graph generator owns the two invented 306-row payloads/pins.
    Public pinned README/LICENSE bytes are exact. The real acquisition manifest,
    four-file verifier, native parser, rights/mapping scope and data API execute.
    """
    from tests.fixtures.openfootball_public_documents import README, LICENSE
    from football_system.application import openfootball_production as app
    from football_system.domain.openfootball_snapshot import (
        COMMIT, PINNED_FILES, OpenFootballAcquisitionManifestV1, OpenFootballFileCaptureV1, OpenFootballAdapterPolicyV1,
    )
    from football_system.domain.training_admission import LocalReviewEvidenceV1
    from football_system.infrastructure.files.openfootball_evidence import qualify_private_root
    from tests.integration.test_openfootball_production_binding import AT
    from football_system.infrastructure.database.migrations import upgrade_database
    from football_system.infrastructure.database.session import create_database_engine, create_session_factory
    from football_system.interfaces.production_quant_cli import production_inference_context
    from pathlib import Path

    for name, value in (("README.md",README),("LICENSE.md",LICENSE)):
        raw=value.encode("utf-8")
        assert (len(raw),hashlib.sha256(raw).hexdigest())==PINNED_FILES[name]
        (g.root/name).write_bytes(raw)
    captures=[]
    for name,(size,digest) in PINNED_FILES.items():
        captures.append(OpenFootballFileCaptureV1(url=f"https://raw.githubusercontent.com/openfootball/football.json/{COMMIT}/{name}",
            commit=COMMIT,path=name,bytes=size,sha256=digest,request_started_at_utc=AT-timedelta(days=1,seconds=1),capture_at_utc=AT-timedelta(days=1)))
    acquisition=OpenFootballAcquisitionManifestV1(repository="openfootball/football.json",commit=COMMIT,
        authorization_basis="USER_STAGE3_LIMITED_ACQUISITION_AND_BOOTSTRAP_PREPARATION",files=tuple(captures),sends=4,
        errors=(),automatic_retries=0,production_training_authorized=False,status="COMPLETE")
    (g.root/"acquisition_manifest.json").write_text(canonical_json(acquisition),encoding="utf-8")
    monkeypatch.setattr(app,"qualify_private_root",qualify_private_root)
    original=g.subject
    # Exercise the actual migration path as well, in a unique test-owned file.
    g.engine.dispose()
    g.database=g.root/"synthetic-ofp-pin.sqlite"
    url="sqlite:///"+g.database.as_posix()
    upgrade_database(url,Path(__file__).resolve().parents[2]/"alembic.ini")
    g.engine=create_database_engine(url)
    g.sessions=create_session_factory(g.engine)
    _,_,g.production,g.inference,_=production_inference_context(g.sessions,evidence=g.evidence,operator_id="synthetic-operator",clock=g.clock)
    g.repo=g.production.openfootball_binding()
    g.subject=g.repo.prepare_data_binding(policy=OpenFootballAdapterPolicyV1.model_validate(original["preparation_scope"]["adapter_policy"]),
        mapping=original["mapping"],rights_payload=original["rights_payload"],directive_evidence=LocalReviewEvidenceV1.model_validate(original["user_directive"]))
    g.full_qualification_verified=True
    return g


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def add_complete_live_inputs(g):
    """Real capture repositories and live preparation; no _live_target_proof stub."""
    g.clock.value += timedelta(hours=4)
    at = g.clock()
    shift = at-inputs.AT
    mid = "self-authored-ofp-future-match"
    comp = canonical_id("COMPETITION", "Deutsche Bundesliga")
    season = canonical_id("SEASON", "2026/27")
    home, away = (canonical_id("TEAM", TEAM_LABELS[i]) for i in (0, 3))
    names = {"rb-match-a":mid,"rb-home-a":home,"rb-away-a":away,"rb-competition":comp,
        "2026/27":season,"Software acceptance league":"Bundesliga"}
    def remap(value):
        if isinstance(value, datetime):
            if value == inputs.AT-timedelta(hours=1):
                return at-timedelta(minutes=45)
            return value+shift
        if isinstance(value, str):
            return names.get(value, value)
        if isinstance(value, dict):
            return {k:remap(v) for k,v in value.items()}
        if isinstance(value, (tuple,list)):
            return [remap(v) for v in value]
        return value
    raw = remap(inputs.fixture_capture("a").model_dump())
    raw["registration"]["competitions"][0].update(canonical_key="ofp-bundesliga:"+comp,country_code="DE")
    for item,label in zip(raw["registration"]["teams"],(TEAM_LABELS[0],TEAM_LABELS[3]),strict=True):
        item.update(canonical_key="ofp-team:"+item["team_id"],name=label)
    raw["registration"]["canonical_matches"][0]["identity"]["competition_type"]="DOMESTIC_LEAGUE"
    raw["registration"]["competition_mappings"][0]["mapping"]["competition_type"]="DOMESTIC_LEAGUE"
    raw["request"]["competition_type"]="DOMESTIC_LEAGUE"
    raw["raw_payload_sha256"]=_digest(raw["registration"])
    fixture = FixtureIngestionCapture.model_validate(raw)
    SqlAlchemyMatchIdentityRepository(g.sessions,clock=g.clock).register_fixture_ingestion(fixture)
    raw = remap(inputs.market_capture("a").model_dump())
    snapshots=tuple(MarketOddsSnapshot.model_validate(v) for v in raw["source_batch"]["snapshots"])
    consensus,mapping,lineage=derive_market_consensus(mid,snapshots)
    original=raw["source_batch"]["mappings"][0]
    original["mapping_id"]=stable_id("provider-mapping",original["provider_code"],original["external_namespace"],original["external_match_id"])
    raw["artifact"]["payload_sha256"]=_digest(snapshots)
    raw["consensus_batch"]=dict(snapshots=(consensus,),mappings=(mapping,),issues=())
    raw["consensus_lineages"]=(lineage,)
    market=MarketOddsIngestionCapture.model_validate(raw)
    raw=remap(inputs.sp_capture("a").model_dump())
    number=raw["batch"]["snapshots"][0]["sporttery_match_no"]
    original=raw["batch"]["mappings"][0]
    original["external_match_id"]=at.date().isoformat()+":"+number
    original["mapping_id"]=stable_id("provider-mapping","SPORTTERY_MANUAL","sporttery_match",original["external_match_id"])
    raw["provenance"][0]["match_number_date"]=at.date()
    raw["artifacts"][0]["payload_sha256"]=_digest(raw["batch"]["snapshots"])
    sporttery=SportteryIngestionCapture.model_validate(raw)
    live=SqlAlchemyLiveSourceRepository(g.sessions,clock=g.clock)
    live.save_market_odds_ingestion(market)
    live.save_sporttery_ingestion(sporttery)
    kickoff=fixture.observations[0].kickoff_at_utc
    preparation=PrepareLiveAnalysisService(live).prepare(PrepareAnalysisRequest(
        decision_as_of_at_utc=g.clock(),kickoff_from_utc=kickoff-timedelta(seconds=1),kickoff_to_utc=kickoff+timedelta(seconds=1),
        competition_id=comp,season_id=season,expected_match_ids=(mid,),allow_partial_inputs=False,
        policy=LiveAnalysisInputPolicy(maximum_odds_age_seconds=3600,minimum_bookmaker_count=2)))
    assert preparation.status.value=="ANALYSIS_INPUT_READY", canonical_json(preparation)
    target=OpenFootballProductionTargetV1(match_id=mid,competition_id=comp,season_id=season,home_team_id=home,away_team_id=away,
        kickoff_at_utc=kickoff,live_preparation_id=preparation.preparation_id,fixture_observation_id=fixture.observations[0].observation_id)
    return (target,)


def through_openfootball_binding(g):
    g.binding=g.admit()
    g.target_values=add_complete_live_inputs(g)
    g.cutoff=g.repo.seal_cutoff("cutoff")
    g.plan=g.repo.seal_pilot_plan(request_key="plan",targets=g.target_values)
    g.report=g.repo.run_pilot(request_key="pilot")
    g.attestation=g.repo.attest_pilot("attest")
    g.manifest=g.repo.create_manifest("manifest")
    payload=g.repo.prepare_approval(effective_at_utc=g.clock(),expires_at_utc=g.clock()+timedelta(days=10))
    review=g.review(payload.schema_version,payload.content_hash,"self-authored-model-review.json")
    g.approval=g.repo.record_approval(request_key="approval",payload=payload,review=review.model_dump(),authority=g.authority.model_dump())
    g.release=g.repo.build_release("release")
    g.target_plan=g.repo.create_target_plan("target")
    g.state_binding=g.repo.bind_model_state("binding")
    g.candidate=g.inference.load_openfootball_model_pin_candidate()
    assert g.candidate["real_model_pin_created"] is False
    assert g.candidate["release_id"]==g.release.artifact_id
    g.source_identity=SOURCE_ID
    return g
