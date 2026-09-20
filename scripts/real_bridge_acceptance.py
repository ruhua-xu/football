"""Installed-wheel, network-denied real-type SOFTWARE acceptance. Never live activation.

All captures and state below are self-authored fixed fixtures. No tests imports,
provider transports, fit/rebuild calls, mutable clock CLI for the production app,
or real-performance samples are involved.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import socket

from sqlalchemy import text

from football_system.application.identity_catalog import FixtureIngestionCapture
from football_system.application.live_sources import MarketOddsIngestionCapture, SportteryIngestionCapture
from football_system.application.market_consensus import derive_market_consensus
from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.domain.archive import canonical_json, canonical_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.match import MarketOddsSnapshot
from football_system.domain.prospective import ManualResultImportV1
from football_system.domain.services.elo_baseline import EloBaselineConfig, EloBaselineState, EloTeamState, EloThreeWayBaseline, _payload_sha256
from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository
from football_system.infrastructure.database.migrations import upgrade_database
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from football_system.interfaces.cli import _resource_root

AT=datetime(2026,9,3,14,tzinfo=timezone.utc)
KICKOFF=datetime(2026,9,10,19,30,tzinfo=timezone.utc)
MARKET={"market_type":"THREE_WAY"}
OUTCOMES=("HOME_WIN","DRAW","AWAY_WIN")


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fixture_capture(suffix):
    mid,home,away=(f"rb-{name}-{suffix}" for name in ("match","home","away"))
    available=AT-timedelta(hours=3)
    provider="SYNTHETIC_BRIDGE_FIXTURE"
    mapping=dict(mapping_id=f"rb-fixture-map-{suffix}",provider_code=provider,external_namespace="fixture",external_match_id=mid,
        internal_match_id=mid,resolution_method="PROVIDER_EXACT",confidence="1",available_at_utc=available)
    registration=dict(created_at_utc=available+timedelta(minutes=1),
        competitions=[dict(competition_id="rb-competition",canonical_key="rb-competition",name="Software acceptance league",country_code="GB")],
        teams=[dict(team_id=team,canonical_key=team,name=team) for team in (home,away)],
        matches=[dict(match_id=mid,competition_id="rb-competition",home_team_id=home,away_team_id=away,kickoff_at_utc=KICKOFF,status="SCHEDULED",available_at_utc=available)],
        team_aliases=[dict(internal_team_id=team,alias=dict(provider_code=provider,provider_team_id=team,provider_team_name=team,language="en"),available_at_utc=available) for team in (home,away)],
        competition_mappings=[dict(mapping=dict(internal_competition_id="rb-competition",provider_code=provider,provider_competition_id="rb-league",
            provider_competition_name="Software acceptance league",language="en",season="2026/27",competition_type="LEAGUE"),available_at_utc=available)],
        canonical_matches=[dict(identity=dict(internal_match_id=mid,internal_competition_id="rb-competition",internal_home_team_id=home,internal_away_team_id=away,
            season="2026/27",competition_type="LEAGUE",kickoff_at_utc=KICKOFF),available_at_utc=available)],explicit_mappings=[mapping])
    audit=dict(provider=provider,endpoint="https://fixture.example.test/fixtures",requested_at_utc=available,received_at_utc=available+timedelta(minutes=1),
        available_at_utc=available,request_parameters={},http_status=200,duration_ms=0,outcome="SUCCESS")
    return FixtureIngestionCapture.model_validate(dict(ingestion_id="rb-fixture-"+suffix,provider_code=provider,
        request=dict(kickoff_from_utc=KICKOFF-timedelta(days=1),kickoff_to_utc=KICKOFF+timedelta(days=1),provider_competition_id="rb-league",
            provider_season_id="rb-2026-27",season="2026/27",competition_type="LEAGUE",language="en",team_type="CLUB"),
        request_audit=audit,raw_artifact_id=sha("fixture artifact "+suffix),raw_payload_sha256=sha(canonical_json(registration)),registration=registration,
        observations=[dict(observation_id="rb-observation-"+suffix,provider_mapping_id=mapping["mapping_id"],external_match_id=mid,internal_match_id=mid,
            kickoff_at_utc=KICKOFF,status="SCHEDULED",available_at_utc=available,payload_sha256=sha("software fixture "+suffix))]))


def market_capture(suffix):
    mid="rb-match-"+suffix
    snapshots=[]
    for i,prices in enumerate((("2.10","3.20","3.60"),("2.20","3.10","3.50"))):
        snapshots.append(MarketOddsSnapshot.model_validate(dict(snapshot_id=f"rb-market-{suffix}-{i}",match_id=mid,provider_code="THE_ODDS_API",
            bookmaker_code=f"synthetic-book-{i}",market=MARKET,quotes=[dict(selection=k,odds=p) for k,p in zip(OUTCOMES,prices,strict=True)],
            captured_at_utc=AT-timedelta(hours=1),available_at_utc=AT-timedelta(minutes=30),ingested_at_utc=AT,
            source_snapshot_key=f"rb-market-key-{suffix}-{i}",payload_hash=canonical_payload_sha256(dict(zip(("home_win","draw","away_win"),map(Decimal,prices),strict=True))))))
    consensus,mapping,lineage=derive_market_consensus(mid,tuple(snapshots))
    return MarketOddsIngestionCapture.model_validate(dict(ingestion_id="rb-market-ingestion-"+suffix,provider_code="THE_ODDS_API",requested_match_ids=[mid],
        identity_cutoff_at_utc=AT-timedelta(hours=1),request_audit=dict(provider="THE_ODDS_API",endpoint="https://odds.example.test/v4/sports/soccer/odds",
            requested_at_utc=AT-timedelta(minutes=31),received_at_utc=AT-timedelta(minutes=30),available_at_utc=AT-timedelta(minutes=30),
            request_parameters={"markets":"h2h"},http_status=200,duration_ms=0,outcome="SUCCESS"),
        artifact=dict(artifact_id="rb-raw-"+suffix,role="RAW_RESPONSE",payload_sha256=sha(canonical_json(snapshots)),source_path="synthetic/market-"+suffix+".json",
            captured_at_utc=AT-timedelta(minutes=30),available_at_utc=AT-timedelta(minutes=30)),ingested_at_utc=AT,
        source_batch=dict(snapshots=snapshots,mappings=[dict(mapping_id=stable_id("provider-mapping","THE_ODDS_API","event",mid),provider_code="THE_ODDS_API",
            external_namespace="event",external_match_id=mid,internal_match_id=mid,resolution_method="EXACT_TEAM_COMPETITION_KICKOFF",confidence="1",available_at_utc=AT)],issues=[]),
        consensus_batch=dict(snapshots=[consensus],mappings=[mapping],issues=[]),consensus_lineages=[lineage]))


def sp_capture(suffix):
    mid="rb-match-"+suffix
    prices=("10","3.15","3.55")
    number="SYN00"+("1" if suffix=="a" else "2")
    snapshot=dict(snapshot_id="rb-sp-"+suffix,match_id=mid,provider_code="SPORTTERY_MANUAL",sporttery_match_no=number,market=MARKET,
        quotes=[dict(selection=k,fixed_bonus=p) for k,p in zip(OUTCOMES,prices,strict=True)],sale_status="OPEN",captured_at_utc=AT-timedelta(hours=1),
        available_at_utc=AT-timedelta(minutes=30),ingested_at_utc=AT,source_snapshot_key="rb-sp-key-"+suffix,
        payload_hash=canonical_payload_sha256(dict(zip(("home_win","draw","away_win"),map(Decimal,prices),strict=True))))
    proof_hash=sha("self-authored SP "+suffix)
    return SportteryIngestionCapture.model_validate(dict(ingestion_id="rb-sp-ingestion-"+suffix,provider_code="SPORTTERY_MANUAL",identity_cutoff_at_utc=AT-timedelta(hours=1),
        artifacts=[dict(artifact_id="rb-sp-doc-"+suffix,role="MANUAL_DOCUMENT",payload_sha256=sha(canonical_json(snapshot)),source_path="synthetic/sp-doc.json",
            captured_at_utc=AT-timedelta(hours=1),available_at_utc=AT-timedelta(minutes=30)),dict(artifact_id="rb-sp-source-"+suffix,role="SOURCE_ARTIFACT",payload_sha256=proof_hash,
            source_path="synthetic/sp-source.txt",captured_at_utc=AT-timedelta(hours=1),available_at_utc=AT-timedelta(minutes=30))],ingested_at_utc=AT,
        batch=dict(snapshots=[snapshot],mappings=[dict(mapping_id=stable_id("provider-mapping","SPORTTERY_MANUAL","sporttery_match","2026-09-03:"+number),
            provider_code="SPORTTERY_MANUAL",external_namespace="sporttery_match",external_match_id="2026-09-03:"+number,internal_match_id=mid,
            resolution_method="EXACT_TEAM_COMPETITION_KICKOFF",confidence="1",available_at_utc=AT)]),
        provenance=[dict(schema_version="SPORTTERY_MANUAL_ARCHIVE_V2",snapshot_id=snapshot["snapshot_id"],source_snapshot_key=snapshot["source_snapshot_key"],
            archive_snapshot_id="rb-sp-archive-"+suffix,provider_code="SPORTTERY_MANUAL",sporttery_match_no=number,match_number_date="2026-09-03",
            review_level="SELF_REVIEWED",entered_by="software-scaffold",reviewed_by="software-scaffold",captured_at_utc=AT-timedelta(hours=1),reviewed_at_utc=AT-timedelta(minutes=30),
            source_reference="SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE",source_artifact_path="synthetic/sp-source.txt",source_artifact_sha256=proof_hash,
            manual_document_artifact_id="rb-sp-doc-"+suffix,source_artifact_id="rb-sp-source-"+suffix)]))


def fixed_state():
    fields=dict(model_name="ELO_THREE_WAY_BASELINE_V1",model_version="1",calibration_label="BASELINE_UNCALIBRATED",config_hash=EloBaselineConfig().config_hash,
        cutoff_at_utc=AT,season_id="2026/27",teams=tuple(EloTeamState(team_id=team,rating="1500",prior_matches=5)
            for team in sorted(f"rb-{kind}-{suffix}" for kind in ("home","away") for suffix in ("a","b"))),
        training_match_ids=(),training_result_ids=(),training_facts=(),training_data_hash=_payload_sha256(()))
    return EloBaselineState(**fields,state_hash=_payload_sha256(fields))


def exercise(work,*,precision=28,reverse=False):
    work=Path(work).resolve()
    work.mkdir(exist_ok=False)
    url="sqlite:///"+(work/"acceptance.db").as_posix()
    upgrade_database(url,_resource_root()/"alembic.ini")
    engine=create_database_engine(url)
    old_precision=getcontext().prec
    try:
        sessions=create_session_factory(engine)
        inputs=[]
        getcontext().prec=28  # Construct the same fixed normalized input, before testing ambient precision.
        for suffix in ("a","b"):
            SqlAlchemyMatchIdentityRepository(sessions,clock=lambda:AT).register_fixture_ingestion(fixture_capture(suffix))
            source=SqlAlchemyLiveSourceRepository(sessions,clock=lambda:AT)
            market,sp=market_capture(suffix),sp_capture(suffix)
            source.save_market_odds_ingestion(market)
            source.save_sporttery_ingestion(sp)
            inputs.append((market,sp))
        state=fixed_state()
        getcontext().prec=precision
        clock=SyntheticProspectiveClock(AT+timedelta(minutes=1))
        repo=SqlAlchemyRealBridgeRepository(sessions,evidence_root=work,clock=clock)
        proof=b"SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE; no licensed provider acquisition."
        (work/"proof.txt").write_bytes(proof)
        digest=hashlib.sha256(proof).hexdigest()
        def call(op,key,**values):
            return repo.execute(op,REQUEST_TYPES[op](request_key=key,**values))
        def ordered(values):
            return tuple(reversed(values)) if reverse else tuple(values)
        program=call("program","program",observation_program_id="wheel-software-only",competition_ids=("rb-competition",),season_id="2026/27",
            scope_reference="SYNTHETIC_SOFTWARE_ACCEPTANCE_ONLY",provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE")
        admissions={}
        for kind,source in (("MODEL","fixed-state"),("MARKET","THE_ODDS_API"),("SPORTTERY","SPORTTERY_MANUAL"),("RESULT","wheel-results")):
            admissions[kind]=call("admission",kind,program_id=program.artifact_id,kind=kind,source_identity=source,state="ADMITTED",effective_at_utc=clock.now(),
                expires_at_utc=KICKOFF+timedelta(days=30),rights_reference="self-authored fixture",verified_by="software-scaffold",evidence_file="proof.txt",
                evidence_hash=digest,credential_verified=kind=="MARKET")
        mids=("rb-match-a","rb-match-b")
        pin=call("model-pin","pin",program_id=program.artifact_id,admission_id=admissions["MODEL"].artifact_id,source_identity="fixed-state",scope_match_ids=ordered(mids),test_state=state)
        policy=call("policy","policy",program_id=program.artifact_id,result_source_identity="wheel-results",maximum_odds_age_seconds=86400,minimum_bookmaker_count=2)
        anchor=call("anchor","anchor",program_id=program.artifact_id,policy_id=policy.artifact_id,model_pin_id=pin.artifact_id,
            starts_at_utc=clock.now()+timedelta(minutes=1),ends_at_utc=KICKOFF+timedelta(days=3))
        epoch=call("epoch","epoch",anchor_id=anchor.artifact_id)
        clock.at+=timedelta(minutes=2)
        slate=call("slate","slate",epoch_id=epoch.artifact_id,match_ids=ordered(mids),source_reference="self-authored complete cohort",source_file="proof.txt",source_hash=digest)
        bucket=call("bucket","bucket",slate_id=slate.artifact_id,kickoff_at_utc=KICKOFF)
        markets,prices=[],[]
        for i,(market,sp) in enumerate(inputs):
            markets.append(call("market-bind",f"market-{i}",program_id=program.artifact_id,admission_id=admissions["MARKET"].artifact_id,
                ingestion_id=market.ingestion_id,snapshot_id=market.consensus_batch.snapshots[0].snapshot_id).artifact_id)
            prices.append(call("sp-bind",f"sp-{i}",program_id=program.artifact_id,admission_id=admissions["SPORTTERY"].artifact_id,
                ingestion_id=sp.ingestion_id,snapshot_id=sp.batch.snapshots[0].snapshot_id).artifact_id)
        prepare=dict(epoch_id=epoch.artifact_id,bucket_id=bucket.artifact_id,market_binding_ids=ordered(markets),sp_binding_ids=ordered(prices),budget_fen=10000)
        first=call("prepare","prepare",**prepare)
        (work/"first-packet.json").write_text(canonical_json(repo.load(first.packet.artifact_id)),encoding="utf-8")
        clock.at+=timedelta(seconds=1)
        run=call("replace","replace",**prepare,expected_head_id=first.artifact_id,reason="fixed software replacement")
        lock=call("lock","lock",run_id=run.artifact_id,strategy_requests=[dict(pass_type="2X1",choices=[dict(match_id=mid,market_key=MARKET,outcomes=["HOME_WIN"]) for mid in mids])])
        assert first.analysis!=run.analysis and first.packet!=run.packet
        clock.at=KICKOFF+timedelta(hours=2)
        def result(mid,key,previous=None):
            return call("result-import",key,program_id=program.artifact_id,admission_id=admissions["RESULT"].artifact_id,result=ManualResultImportV1(
                match_id=mid,data_classification="REAL_SOURCE_DATA",source_identity="wheel-results",source_reference="SYNTHETIC_SOFTWARE_ACCEPTANCE",source_file="proof.txt",
                source_hash=digest,verified_by="software-scaffold",verified_at_utc=clock.now(),observed_at_utc=clock.now(),available_at_utc=clock.now(),
                published_at_utc=clock.now(),source_version_id=key,previous_source_version_id=previous.claim.source_version_id if previous else None,
                status="FT",regular_time_semantics="REGULATION_ONLY",home_goals=0 if previous else 2,away_goals=1,
                supersedes_observation_id=previous.artifact_id if previous else None,rights_reference="self-authored fixture",retention_until_utc=KICKOFF+timedelta(days=30)))
        observations=[result(mid,f"result-{i}") for i,mid in enumerate(mids)]
        settlement=call("settle","settle",run_id=run.artifact_id)
        old_report=call("report","old-report",epoch_id=epoch.artifact_id,as_of_at_utc=clock.now())
        clock.at+=timedelta(seconds=1)
        result(mids[0],"revised-result",observations[0])
        stale=call("report","stale",epoch_id=epoch.artifact_id,as_of_at_utc=clock.now())
        assert stale.metrics["coverage"]=={"REPLACED_PRE_LOCK":1,"STALE_SETTLEMENT":1}
        revised=call("settle","resettle",run_id=run.artifact_id)
        report=call("report","report",epoch_id=epoch.artifact_id,as_of_at_utc=clock.now())
        assert report.official_prediction_count==0 and report.performance_evidence_status=="INSUFFICIENT_PROSPECTIVE_SAMPLE"
        assert report.metrics["coverage"]=={"REPLACED_PRE_LOCK":1,"SETTLED":1}
        assert revised.previous.artifact_id==settlement.artifact_id
        # A new repository/connection after restart must not need any in-memory cache.
        engine.dispose()
        repo=SqlAlchemyRealBridgeRepository(sessions,evidence_root=work,clock=clock)
        assert call("prepare","prepare",**prepare)==first
        for artifact in (anchor,epoch,first,run,lock,settlement,old_report,stale,revised,report):
            assert repo.load(artifact.artifact_id)==artifact
            assert repo.audit(artifact.artifact_id)["status"]=="AUDIT_PASS"
        with sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM rb_prediction_slots"))==2
            rows=session.execute(text("SELECT artifact_id,artifact_json FROM rb_artifacts ORDER BY artifact_id")).all()
            fingerprint=sha(canonical_json([tuple(row) for row in rows]))
        summary=dict(provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE",provider_http_sends=0,llm_api_http_sends=0,real_observation_count=0,
            real_performance_claim=False,migration_head="6c859ab273fe",artifact_count=len(rows),artifact_graph_hash=fingerprint,
            run_id=run.artifact_id,lock_id=lock.artifact_id,report_id=report.artifact_id,performance_evidence_status=report.performance_evidence_status)
        (work/"acceptance-summary.json").write_text(canonical_json(summary),encoding="utf-8")
        return summary
    finally:
        getcontext().prec=old_precision
        engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir",type=Path,required=True)
    parser.add_argument("--decimal-precision",type=int,default=28)
    parser.add_argument("--reverse-input-order",action="store_true")
    args=parser.parse_args()
    def forbidden(*_,**__):
        raise AssertionError("PROVIDER_HTTP_LLM_HTTP_AND_TRAINING_FORBIDDEN")
    socket.socket.connect=forbidden
    EloThreeWayBaseline.rebuild_state=forbidden
    print(json.dumps(exercise(args.work_dir,precision=args.decimal_precision,reverse=args.reverse_input_order),sort_keys=True))
    print("REAL_BRIDGE_V1_INSTALLED_ACCEPTANCE_PASS")


if __name__=="__main__":
    main()
