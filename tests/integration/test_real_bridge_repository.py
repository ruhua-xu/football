"""Independent rb software acceptance. Fixed synthetic captures, no provider or fitting."""

from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import text

from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.domain.archive import canonical_json
from football_system.domain.prospective import ManualResultImportV1
from football_system.domain.services.elo_baseline import EloBaselineConfig, EloBaselineState, EloTeamState, _payload_sha256
from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.session import create_database_engine, create_schema, create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from tests.integration.test_fixture_ingestion_persistence import _capture, KICKOFF
from tests.integration.test_live_source_persistence import _market_capture, _sporttery_capture, PERSISTED


@pytest.fixture
def bridge(tmp_path, monkeypatch, request):
    # Test data has real contract types but never an eligible observation program.
    def forbidden(*args, **kwargs):
        raise AssertionError("PROVIDER_HTTP_AND_MODEL_TRAINING_FORBIDDEN")
    from football_system.domain.services.elo_baseline import EloThreeWayBaseline
    import socket
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(EloThreeWayBaseline, "rebuild_state", forbidden)
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'acceptance.db').as_posix()}")
    create_schema(engine)
    sessions = create_session_factory(engine)
    SqlAlchemyMatchIdentityRepository(sessions, clock=lambda: PERSISTED).register_fixture_ingestion(_capture("rb"))
    live = SqlAlchemyLiveSourceRepository(sessions, clock=lambda: PERSISTED)
    market, sp = _market_capture(), _sporttery_capture()
    variant=getattr(request,"param",1)
    count=2 if variant=="unavailable" else variant
    if count==2:
        sp=positive_sp(sp)
    live.save_market_odds_ingestion(market)
    live.save_sporttery_ingestion(sp)
    captures=[(market,sp)]
    if count==2:
        SqlAlchemyMatchIdentityRepository(sessions,clock=lambda:PERSISTED).register_fixture_ingestion(_capture("rb-b",suffix="-b"))
        second_market,second_sp=second_captures()
        second_sp=positive_sp(second_sp)
        live.save_market_odds_ingestion(second_market)
        live.save_sporttery_ingestion(second_sp)
        captures.append((second_market,second_sp))
    match_ids=("match",) if count==1 else ("match","match-b")
    clock = SyntheticProspectiveClock(PERSISTED+timedelta(hours=1))
    repo = SqlAlchemyRealBridgeRepository(sessions, evidence_root=tmp_path, clock=clock)
    proof = b"Self-authored fixed synthetic SOFTWARE ACCEPTANCE; no acquired source data."
    (tmp_path / "proof.txt").write_bytes(proof)
    p = dict(repo=repo, clock=clock, root=tmp_path, engine=engine, sessions=sessions,
             proof=hashlib.sha256(proof).hexdigest(), market=market, sp=sp, match_ids=match_ids)
    program = command(p, "program", "program", observation_program_id="rb-software-only", competition_ids=("competition",) if count==1 else ("competition","competition-b"),
        season_id="2026/27", scope_reference="FIXED_SYNTHETIC_SOFTWARE_ACCEPTANCE", provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE")
    p["program"] = program
    p["admissions"] = {}
    for kind, source in (("MODEL","fixed-model"),("MARKET","THE_ODDS_API"),("SPORTTERY","SPORTTERY_MANUAL"),("RESULT","test-result")):
        p["admissions"][kind] = command(p,"admission",kind,program_id=program.artifact_id,kind=kind,source_identity=source,
            state="ADMITTED",effective_at_utc=clock.now(),expires_at_utc=KICKOFF+timedelta(days=30),rights_reference="self-authored test fixture",
            evidence_file="proof.txt",evidence_hash=p["proof"],credential_verified=kind=="MARKET")
    values = dict(config_hash=EloBaselineConfig().config_hash,cutoff_at_utc=PERSISTED,season_id="2026/27",
        teams=tuple(EloTeamState(team_id=team,rating="1500",prior_matches=3 if variant=="unavailable" and team.endswith("-b") else 5) for team in sorted(("away","home") if count==1 else ("away","home","away-b","home-b"))),
        training_match_ids=(),training_result_ids=(),training_facts=(),training_data_hash=_payload_sha256(()),
        model_name="ELO_THREE_WAY_BASELINE_V1",model_version="1",calibration_label="BASELINE_UNCALIBRATED")
    state = EloBaselineState(**values,state_hash=_payload_sha256(values))
    p["pin"] = command(p,"model-pin","pin",program_id=program.artifact_id,admission_id=p["admissions"]["MODEL"].artifact_id,
        source_identity="fixed-model",scope_match_ids=match_ids,test_state=state)
    policy = command(p,"policy","policy",program_id=program.artifact_id,result_source_identity="test-result",
        maximum_odds_age_seconds=86400,minimum_bookmaker_count=2)
    anchor = command(p,"anchor","anchor",program_id=program.artifact_id,policy_id=policy.artifact_id,model_pin_id=p["pin"].artifact_id,
        starts_at_utc=clock.now()+timedelta(minutes=1),ends_at_utc=KICKOFF+timedelta(days=10))
    p["epoch"] = command(p,"epoch","epoch",anchor_id=anchor.artifact_id)
    clock.at += timedelta(minutes=2)
    slate = command(p,"slate","slate",epoch_id=p["epoch"].artifact_id,match_ids=match_ids,
        source_reference="synthetic declaration",source_file="proof.txt",source_hash=p["proof"])
    p["bucket"] = command(p,"bucket","bucket",slate_id=slate.artifact_id,kickoff_at_utc=KICKOFF)
    p["market_bindings"],p["sp_bindings"]=[],[]
    for i,(market,sp) in enumerate(captures):
        p["market_bindings"].append(command(p,"market-bind",f"market-{i}",program_id=program.artifact_id,admission_id=p["admissions"]["MARKET"].artifact_id,
            ingestion_id=market.ingestion_id,snapshot_id=market.consensus_batch.snapshots[0].snapshot_id))
        p["sp_bindings"].append(command(p,"sp-bind",f"sp-{i}",program_id=program.artifact_id,admission_id=p["admissions"]["SPORTTERY"].artifact_id,
            ingestion_id=sp.ingestion_id,snapshot_id=sp.batch.snapshots[0].snapshot_id))
    p["market_binding"],p["sp_binding"]=p["market_bindings"][0],p["sp_bindings"][0]
    yield p
    engine.dispose()


def command(p, op, key, **values):
    if op=="admission":
        values.setdefault("verified_by","synthetic-software-reviewer")
    return p["repo"].execute(op, REQUEST_TYPES[op](request_key=key, **values))


def prepare(p, key="prepare", **values):
    op = "replace" if "expected_head_id" in values else "prepare"
    return command(p,op,key,epoch_id=p["epoch"].artifact_id,bucket_id=p["bucket"].artifact_id,
        market_binding_ids=tuple(v.artifact_id for v in p["market_bindings"]),sp_binding_ids=tuple(v.artifact_id for v in p["sp_bindings"]),budget_fen=10000,**values)


def second_captures():
    """Another self-authored source fixture, rederived with the unchanged median kernel."""
    from football_system.application.market_consensus import derive_market_consensus
    from football_system.application.ports.data_providers import MarketOddsBatch,SportteryBatch
    from football_system.domain.common import stable_id
    from football_system.domain.match import ProviderMatchMapping
    market,sp=_market_capture(),_sporttery_capture()
    snapshots=tuple(type(s).model_validate({**s.model_dump(),"match_id":"match-b","snapshot_id":s.snapshot_id+"-b",
        "source_snapshot_key":s.source_snapshot_key+"-b"}) for s in market.source_batch.snapshots)
    mapping=ProviderMatchMapping.model_validate({**market.source_batch.mappings[0].model_dump(),"mapping_id":stable_id("provider-mapping","THE_ODDS_API","event","event-b"),
        "internal_match_id":"match-b","external_match_id":"event-b"})
    consensus,cm,lineage=derive_market_consensus("match-b",snapshots)
    market=type(market).model_validate({**market.model_dump(),"ingestion_id":"market-b","requested_match_ids":("match-b",),
        "source_batch":MarketOddsBatch(snapshots=snapshots,mappings=(mapping,),issues=()),
        "consensus_batch":MarketOddsBatch(snapshots=(consensus,),mappings=(cm,),issues=()),"consensus_lineages":(lineage,)})
    snapshot=type(sp.batch.snapshots[0]).model_validate({**sp.batch.snapshots[0].model_dump(),"match_id":"match-b",
        "snapshot_id":sp.batch.snapshots[0].snapshot_id+"-b","source_snapshot_key":sp.batch.snapshots[0].source_snapshot_key+"-b","sporttery_match_no":"SYN002"})
    mapping=ProviderMatchMapping.model_validate({**sp.batch.mappings[0].model_dump(),"mapping_id":stable_id("provider-mapping","SPORTTERY_MANUAL","sporttery_match","2026-09-03:SYN002"),
        "internal_match_id":"match-b","external_match_id":"2026-09-03:SYN002"})
    provenance=type(sp.provenance[0]).model_validate({**sp.provenance[0].model_dump(),"snapshot_id":snapshot.snapshot_id,
        "source_snapshot_key":snapshot.source_snapshot_key,"sporttery_match_no":"SYN002"})
    sp=type(sp).model_validate({**sp.model_dump(),"ingestion_id":"sp-b","batch":SportteryBatch(snapshots=(snapshot,),mappings=(mapping,)),"provenance":(provenance,)})
    return market,sp


def positive_sp(capture):
    from football_system.domain.archive import canonical_payload_sha256
    snapshot=capture.batch.snapshots[0]
    values=snapshot.model_dump()
    values["quotes"][0]["fixed_bonus"]="10"
    values["payload_hash"]=canonical_payload_sha256(dict(home_win="10",draw="3.15",away_win="3.55"))
    snapshot=type(snapshot).model_validate(values)
    return type(capture).model_validate({**capture.model_dump(),"batch":{**capture.batch.model_dump(),"snapshots":(snapshot,)}})


def test_bootstrap_prepare_lock_report_and_replay(bridge):
    p = bridge
    run = prepare(p)
    lock = command(p,"lock","lock",run_id=run.artifact_id)
    assert lock.stake_fen == 0  # One match never invents single betting.
    report = command(p,"report","report",epoch_id=p["epoch"].artifact_id,as_of_at_utc=p["clock"].now())
    assert report.official_prediction_count == 0
    assert report.metrics["coverage"] == {"LOCKED":1}
    assert report.performance_evidence_status == "INSUFFICIENT_PROSPECTIVE_SAMPLE"
    assert prepare(p) == run
    for value in (p["epoch"],run,lock,report):
        assert canonical_json(p["repo"].load(value.artifact_id)) == canonical_json(value)
        assert p["repo"].audit(value.artifact_id)["status"] == "AUDIT_PASS"
    with p["engine"].connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def report(p,key):
    return command(p,"report",key,epoch_id=p["epoch"].artifact_id,as_of_at_utc=p["clock"].now())


def result(p,key,previous=None,score=(2,1),status="FT"):
    now=p["clock"].now()
    return command(p,"result-import",key,program_id=p["program"].artifact_id,admission_id=p["admissions"]["RESULT"].artifact_id,
        result=ManualResultImportV1(match_id="match",data_classification="REAL_SOURCE_DATA",source_identity="test-result",
            source_reference="SYNTHETIC_SOFTWARE_ACCEPTANCE",source_file="proof.txt",source_hash=p["proof"],verified_by="synthetic-test",
            verified_at_utc=now,observed_at_utc=now,available_at_utc=now,published_at_utc=now,source_version_id=key,
            previous_source_version_id=previous.claim.source_version_id if previous else None,status=status,
            regular_time_semantics="REGULATION_ONLY" if status=="FT" else "UNPROVEN",home_goals=score[0] if status=="FT" else None,
            away_goals=score[1] if status=="FT" else None,supersedes_observation_id=previous.artifact_id if previous else None,
            rights_reference="self-authored synthetic software acceptance",retention_until_utc=KICKOFF+timedelta(days=30)))


def test_ra12_replacements_census_stale_head_and_original_bytes(bridge):
    p=bridge
    first=prepare(p)
    original=canonical_json(first)
    empty=report(p,"before-replacement")
    p["clock"].at+=timedelta(microseconds=1)
    second=prepare(p,"r1",expected_head_id=first.artifact_id,reason="updated verified input")
    p["clock"].at+=timedelta(microseconds=1)
    third=prepare(p,"r2",expected_head_id=second.artifact_id,reason="updated verified input")
    assert len({r.analysis.artifact_id for r in (first,second,third)})==3
    assert len({r.packet.artifact_id for r in (first,second,third)})==3
    for i,run in enumerate((first,second)):
        with pytest.raises(ValueError,match="STALE_PREPARING_HEAD"):
            command(p,"lock",f"old-head-{i}",run_id=run.artifact_id)
    command(p,"lock","lock",run_id=third.artifact_id)
    current=report(p,"census")
    assert current.metrics["coverage"]=={"REPLACED_PRE_LOCK":2,"LOCKED":1,"REJECTED_REQUEST":2}
    assert canonical_json(p["repo"].load(first.artifact_id))==original
    assert p["repo"].load(empty.artifact_id)==empty


@pytest.mark.parametrize("kind",["MARKET","SPORTTERY","MODEL"])
def test_ra21_admission_revoked_between_prepare_and_lock(bridge,kind):
    p=bridge
    run=prepare(p)
    old=p["admissions"][kind]
    p["clock"].at+=timedelta(seconds=1)
    command(p,"admission","revoke",program_id=p["program"].artifact_id,kind=kind,source_identity=old.source_identity,
        state="REVOKED",effective_at_utc=p["clock"].now(),expires_at_utc=old.expires_at_utc,
        rights_reference="explicit synthetic revocation",evidence_file="proof.txt",evidence_hash=p["proof"],previous_id=old.artifact_id)
    with pytest.raises(ValueError,match="SOURCE_ADMISSION_EXPIRED_OR_REVOKED"):
        command(p,"lock","invalid",run_id=run.artifact_id)
    assert p["repo"].load(run.artifact_id)==run
    with p["sessions"]() as s:
        assert s.scalar(text("SELECT count(*) FROM rb_head_consumptions"))==0
        assert s.scalar(text("SELECT count(*) FROM rb_prediction_slots"))==0


def test_ra21_model_invalidation_unavailable_bucket_not_no_bet(bridge):
    p=bridge
    old=prepare(p)
    p["clock"].at+=timedelta(seconds=1)
    command(p,"model-invalidate","invalidate-model",pin_id=p["pin"].artifact_id,reason="explicit synthetic model invalidation",
        evidence_file="proof.txt",evidence_hash=p["proof"])
    with pytest.raises(ValueError,match="MODEL_INVALIDATED"):
        command(p,"lock","invalid-lock",run_id=old.artifact_id)
    with pytest.raises(ValueError,match="REPLACEMENT_SCOPE_OR_AVAILABILITY"):
        prepare(p,"unavailable-replace",expected_head_id=old.artifact_id,reason="cannot replace with unavailable analysis")
    # A new predeclared cohort preserves the whole unavailable attempt without a lock.
    slate=command(p,"slate","new-slate",epoch_id=p["epoch"].artifact_id,match_ids=("match",),source_reference="test unchanged cohort",
        source_file="proof.txt",source_hash=p["proof"])
    p["bucket"]=command(p,"bucket","new-bucket",slate_id=slate.artifact_id,kickoff_at_utc=KICKOFF)
    run=prepare(p,"unavailable-run")
    assert run.status=="UNAVAILABLE" and run.packet is None and len(run.identities)==1
    analysis=p["repo"].load(run.analysis.artifact_id)
    assert analysis.units[0].p_quant is analysis.units[0].p_base is None
    assert analysis.units[0].model.status=="MODEL_UNAVAILABLE"
    with pytest.raises(ValueError):
        command(p,"lock","cannot-lock-unavailable",run_id=run.artifact_id)


def test_ra21_fixture_reschedule_and_ordinary_new_observation(bridge):
    p=bridge
    run=prepare(p)
    at=p["clock"].now()+timedelta(minutes=2)
    identity=SqlAlchemyMatchIdentityRepository(p["sessions"],clock=lambda:at)
    identity.register_fixture_ingestion(_capture("ordinary",available_at=at-timedelta(minutes=1)))
    p["clock"].at=at
    # New source information alone does not invalidate a legally unchanged forecast.
    lock=command(p,"lock","still-valid",run_id=run.artifact_id)
    p["clock"].at+=timedelta(seconds=1)
    revision=prepare(p,"revision",supersedes_lock_id=lock.artifact_id)
    at+=timedelta(minutes=2)
    SqlAlchemyMatchIdentityRepository(p["sessions"],clock=lambda:at).register_fixture_ingestion(
        _capture("rescheduled",available_at=at-timedelta(minutes=1),kickoff_at=KICKOFF+timedelta(microseconds=1)))
    p["clock"].at=at
    with pytest.raises(ValueError,match="CANONICAL_KICKOFF_OR_IDENTITY_CHANGED"):
        command(p,"lock","changed-kickoff",run_id=revision.artifact_id,invalidation_reason="change")
    assert p["repo"].load(lock.artifact_id)==lock


def test_ra14_postlock_revision_single_slot_and_duplicate_bucket(bridge):
    p=bridge
    old=prepare(p)
    old_lock=command(p,"lock","old-lock",run_id=old.artifact_id)
    p["clock"].at+=timedelta(seconds=1)
    new=prepare(p,"postlock",supersedes_lock_id=old_lock.artifact_id)
    new_lock=command(p,"lock","new-lock",run_id=new.artifact_id,invalidation_reason="explicit pre-kickoff correction")
    assert report(p,"report").metrics["coverage"]=={"INVALIDATED_PRE_KICKOFF":1,"LOCKED":1}
    with p["sessions"]() as s:
        assert s.scalar(text("SELECT count(*) FROM rb_prediction_slots"))==1
        assert s.scalars(text("SELECT version FROM rb_prediction_versions ORDER BY version")).all()==[0,1]
    p["clock"].at+=timedelta(seconds=1)
    slate=command(p,"slate","duplicate-slate",epoch_id=p["epoch"].artifact_id,match_ids=("match",),source_reference="separate naming same cohort",
        source_file="proof.txt",source_hash=p["proof"])
    p["bucket"]=command(p,"bucket","duplicate-bucket",slate_id=slate.artifact_id,kickoff_at_utc=KICKOFF)
    duplicate=prepare(p,"duplicate")
    with pytest.raises(ValueError,match="DUPLICATE_OFFICIAL_PREDICTION"):
        command(p,"lock","duplicate-lock",run_id=duplicate.artifact_id)
    assert p["repo"].load(new_lock.artifact_id)==new_lock


def test_ra20_results_revisions_full_census_and_same_time_watermark(bridge):
    p=bridge
    run=prepare(p)
    command(p,"lock","lock",run_id=run.artifact_id)
    p["clock"].at=KICKOFF+timedelta(hours=2)
    missing=command(p,"settle","missing",run_id=run.artifact_id)
    assert missing.reason=="MISSING_RESULT" and missing.profit_loss_fen is None
    first=result(p,"result")
    settled=command(p,"settle","settled",run_id=run.artifact_id)
    old=report(p,"old-report")
    assert old.metrics["coverage"]=={"SETTLED":1}
    assert old.metrics["real_run_count"]==0 and old.metrics["synthetic_run_count"]==1
    p["clock"].at+=timedelta(seconds=1)
    result(p,"result-revision",previous=first,score=(0,2))
    stale=report(p,"stale-report")
    assert stale.metrics["coverage"]=={"STALE_SETTLEMENT":1}
    revised=command(p,"settle","revised",run_id=run.artifact_id)
    assert revised.previous.artifact_id==settled.artifact_id
    current=report(p,"current-report")
    assert current.metrics["coverage"]=={"SETTLED":1}
    assert p["repo"].load(old.artifact_id)==old
    assert p["repo"].load(stale.artifact_id)==stale
    assert p["repo"].load(missing.artifact_id)==missing


@pytest.mark.parametrize("bridge",[2],indirect=True)
def test_ra19_real_two_match_math_graph_and_value_kernel_equivalence(bridge):
    from football_system.domain.strategy_pass_v2 import TicketRequestV2
    from football_system.domain.services.real_bridge import RealPreparedReturnInput
    from football_system.domain.services.return_distribution import evaluate_prepared
    from football_system.infrastructure.files.return_distribution import default_return_configuration
    p=bridge
    run=prepare(p)
    ticket=TicketRequestV2(pass_type="2X1",choices=tuple(dict(match_id=mid,market_key={"market_type":"THREE_WAY"},outcomes=("HOME_WIN",)) for mid in p["match_ids"]))
    lock=command(p,"lock","lock",run_id=run.artifact_id,strategy_requests=(ticket,))
    plan=p["repo"].load(lock.strategy_plan.artifact_id)
    assert len(plan.candidates)==1 and len(lock.frames)==2
    calculation=p["repo"].load(lock.calculation.artifact_id)
    policy,objective=default_return_configuration()
    expected=evaluate_prepared(RealPreparedReturnInput(plan),calculation.optimizer.result.requested,policy,objective)
    assert calculation.optimizer.result==expected
    from football_system.domain.return_distribution import ReturnAllocationRequestV1
    evaluation=evaluate_prepared(RealPreparedReturnInput(plan),(ReturnAllocationRequestV1(ticket_candidate_id=plan.candidates[0].artifact_id,multiplier=1),),policy,objective)
    assert evaluation.distribution.stake_fen==200
    assert [point.gross_payout_fen for point in evaluation.distribution.support]==[0,20000]
    assert p["repo"].load(lock.artifact_id)==lock
    with p["sessions"]() as s:
        assert s.scalar(text("SELECT count(*) FROM rb_prediction_slots"))==2
        assert s.scalar(text("SELECT count(*) FROM rb_math_edges WHERE path='unit_id'"))==6


@pytest.mark.parametrize("competitor",["replace","lock"])
def test_ra13_head_races_have_one_atomic_winner(bridge,competitor):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    p=bridge
    run=prepare(p)
    p["clock"].at+=timedelta(seconds=1)
    barrier=Barrier(2)
    def contender(index):
        barrier.wait(timeout=10)
        try:
            if index==1 and competitor=="lock":
                return command(p,"lock","racing-lock",run_id=run.artifact_id)
            return prepare(p,f"racing-replace-{index}",expected_head_id=run.artifact_id,reason="concurrent source refresh")
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(2) as pool:
        outcomes=list(pool.map(contender,range(2)))
    assert sum(not isinstance(v,str) for v in outcomes)==1
    assert next(v for v in outcomes if isinstance(v,str))=="STALE_PREPARING_HEAD_OR_ALREADY_LOCKED"
    with p["sessions"]() as s:
        assert s.scalar(text("SELECT count(*) FROM rb_head_consumptions WHERE parent_run_id=:id"),{"id":run.artifact_id})==1
        assert s.scalar(text("SELECT count(*) FROM rb_receipts WHERE outcome='REJECTED'"))==1


def test_ra17_append_only_every_populated_table_and_complete_seals(bridge):
    from sqlalchemy.exc import IntegrityError
    from football_system.infrastructure.database.real_bridge_schema import RB_TABLES
    from football_system.infrastructure.database.models import Base
    p=bridge
    run=prepare(p)
    command(p,"lock","lock",run_id=run.artifact_id)
    report(p,"census")
    for name in RB_TABLES:
        table=Base.metadata.tables[name]
        with p["sessions"]() as s:
            row=s.execute(table.select()).mappings().first()
        if row:
            for statement in (table.update().values({next(iter(row)):next(iter(row.values()))}),table.delete(),table.insert().prefix_with("OR REPLACE").values(dict(row))):
                with pytest.raises(IntegrityError):
                    with p["sessions"].begin() as s:
                        s.execute(statement)
    with pytest.raises(IntegrityError):
        with p["sessions"].begin() as s:
            s.execute(text("INSERT INTO rb_math_edges SELECT parent_id,'unlisted',parent_schema,parent_hash,target_id,target_schema,target_hash FROM rb_math_edges LIMIT 1"))


def test_ra21_deadline_commit_clock_and_admission_expiry(bridge,monkeypatch):
    p=bridge
    run=prepare(p)
    p["clock"].at=KICKOFF-timedelta(seconds=60)
    with pytest.raises(ValueError,match="LOOKAHEAD_RISK"):
        command(p,"lock","deadline",run_id=run.artifact_id)
    assert p["repo"].load(run.artifact_id)==run
    p["clock"].at+=timedelta(seconds=1)
    with pytest.raises(ValueError,match="LOOKAHEAD_RISK"):
        prepare(p,"late-replace",expected_head_id=run.artifact_id,reason="too late")


def test_ra01_ra02_closed_types_and_no_external_real_analysis_injection(bridge):
    from pydantic import ValidationError
    from football_system.domain.real_bridge import RealAnalysisUnitV1
    p=bridge
    run=prepare(p)
    unit=p["repo"].load(run.analysis.artifact_id).units[0]
    for changes in ({"data_classification":"SYNTHETIC_ACCEPTANCE_DATA"},{"market_key":{"market_type":"TOTAL_GOALS"}},{"consensus":None}):
        with pytest.raises(ValidationError):
            RealAnalysisUnitV1.model_validate({**unit.model_dump(),**changes})
    with pytest.raises(ValidationError):
        REQUEST_TYPES["prepare"].model_validate(dict(request_key="injected",epoch_id=p["epoch"].artifact_id,bucket_id=p["bucket"].artifact_id,
            market_binding_ids=(),sp_binding_ids=(),budget_fen=0,analysis_id=run.analysis.artifact_id))
    with pytest.raises(ValueError,match="REAL_SOURCE_TYPE_MISMATCH"):
        command(p,"lock","wrong-type",run_id=p["bucket"].artifact_id)


def test_ra04_event_overrides_late_anchor_and_missing_pin(bridge):
    from pydantic import ValidationError
    p=bridge
    with pytest.raises(ValidationError):
        REQUEST_TYPES["lock"].model_validate(dict(request_key="backdate",run_id="x",locked_at_utc=p["clock"].now()))
    anchor=p["repo"].load(p["epoch"].anchor.artifact_id)
    with pytest.raises(ValueError):
        command(p,"anchor","late-anchor",program_id=p["program"].artifact_id,policy_id=anchor.policy.artifact_id,model_pin_id=p["pin"].artifact_id,
            starts_at_utc=p["clock"].now(),ends_at_utc=KICKOFF)
    with pytest.raises(ValueError,match="UNKNOWN_REAL_ARTIFACT"):
        command(p,"anchor","missing-pin",program_id=p["program"].artifact_id,policy_id=anchor.policy.artifact_id,model_pin_id="missing",
            starts_at_utc=p["clock"].now()+timedelta(minutes=1),ends_at_utc=KICKOFF)


@pytest.mark.parametrize("kind",["MARKET","SPORTTERY"])
def test_ra07_ra08_full_capture_projection_replay_rejects_corruption(bridge,kind):
    import sqlite3
    p=bridge
    run=prepare(p)
    ingestion=p["market"].ingestion_id if kind=="MARKET" else p["sp"].ingestion_id
    with sqlite3.connect(p["root"]/"acceptance.db") as raw:
        for name, in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='live_source_ingestion_artifacts'").fetchall():
            raw.execute('DROP TRIGGER "'+name+'"')
        raw.execute("UPDATE live_source_ingestion_artifacts SET source_path='consistent-count-but-wrong-path' WHERE ingestion_id=?",(ingestion,))
    with pytest.raises(ValueError,match="SOURCE_ARTIFACT_PROJECTION_MISMATCH"):
        command(p,"lock","corrupt-lock",run_id=run.artifact_id)


@pytest.mark.parametrize("delta",[timedelta(seconds=1),timedelta(microseconds=1)])
@pytest.mark.parametrize("bridge",[2],indirect=True)
def test_ra09_exact_kickoff_buckets_never_use_tolerance(bridge,delta):
    p=bridge
    at=p["clock"].now()+timedelta(minutes=2)
    SqlAlchemyMatchIdentityRepository(p["sessions"],clock=lambda:at).register_fixture_ingestion(
        _capture("different-kickoff",suffix="-b",available_at=at-timedelta(minutes=1),kickoff_at=KICKOFF+delta))
    p["clock"].at=at
    slate=command(p,"slate","rescheduled-slate",epoch_id=p["epoch"].artifact_id,match_ids=p["match_ids"],source_reference="complete updated software cohort",
        source_file="proof.txt",source_hash=p["proof"])
    first=command(p,"bucket","first",slate_id=slate.artifact_id,kickoff_at_utc=KICKOFF)
    second=command(p,"bucket","second",slate_id=slate.artifact_id,kickoff_at_utc=KICKOFF+delta)
    assert [i.match_id for i in first.identities]==["match"]
    assert [i.match_id for i in second.identities]==["match-b"]
    assert first.kickoff_at_utc!=second.kickoff_at_utc


def test_ra10_crash_and_slow_publication_leave_no_partial_lock(bridge,monkeypatch):
    p=bridge
    run=prepare(p)
    original=p["repo"]._store
    def crash(*args,**kw):
        original(*args,**kw)
        raise RuntimeError("simulated unknown commit interruption before COMMIT")
    monkeypatch.setattr(p["repo"],"_store",crash)
    with pytest.raises(RuntimeError,match="simulated"):
        command(p,"lock","lock",run_id=run.artifact_id)
    with p["sessions"]() as s:
        assert s.scalar(text("SELECT count(*) FROM rb_locks"))==0
        assert s.scalar(text("SELECT count(*) FROM rb_head_consumptions"))==0
        assert s.scalar(text("SELECT count(*) FROM rb_receipts WHERE request_key='lock'"))==0
    def slow(*args,**kw):
        original(*args,**kw)
        p["clock"].at+=timedelta(seconds=31)
    monkeypatch.setattr(p["repo"],"_store",slow)
    with pytest.raises(ValueError,match="REAL_OPERATION_CLOCK_WINDOW_EXCEEDED"):
        command(p,"lock","slow-lock",run_id=run.artifact_id)
    monkeypatch.setattr(p["repo"],"_store",original)
    lock=command(p,"lock","lock",run_id=run.artifact_id)
    assert command(p,"lock","lock",run_id=run.artifact_id)==lock


def test_ra11_replaced_packet_review_cannot_lock_new_head(bridge):
    p=bridge
    first=prepare(p)
    packet=p["repo"].load(first.packet.artifact_id)
    p["clock"].at+=timedelta(seconds=1)
    run=prepare(p,"replace",expected_head_id=first.artifact_id,reason="new cutoff")
    unit=packet.market_units[0]
    raw=canonical_json(dict(schema_version="LLM_REVIEW_V4",analysis_id=first.analysis.artifact_id,packet_id=packet.artifact_id,packet_hash=packet.content_hash,
        market_reviews=[dict(match_id="match",market_key={"market_type":"THREE_WAY"},review_context_id=unit.review_context_id,review_context_hash=unit.review_context_hash,
            status="UNAVAILABLE",failure_code="SKIPPED_DISABLED",limitations=["old packet"])]))
    with pytest.raises(ValueError):
        command(p,"lock","old-review",run_id=run.artifact_id,raw_review=raw,reasons=())
    with p["sessions"]() as s:
        stored=s.scalar(text("SELECT request_json FROM rb_receipts WHERE request_key='old-review'"))
        assert "old packet" not in stored and "raw_review" not in stored


def test_ra17_consistently_resealed_report_still_requires_source_math(bridge):
    import sqlite3
    from football_system.domain.real_bridge import RealValidationReportV1
    p=bridge
    run=prepare(p)
    command(p,"lock","lock",run_id=run.artifact_id)
    original=report(p,"report")
    fields=original.model_dump(exclude={"artifact_id","content_hash"})
    fields["metrics"]["decision_value"]["net_profit_loss_fen"]+=1
    forged=RealValidationReportV1.freeze(**fields)
    with sqlite3.connect(p["root"]/"acceptance.db") as raw:
        for name, in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_rb_%'").fetchall():
            raw.execute('DROP TRIGGER "'+name+'"')
        raw.execute("UPDATE rb_artifacts SET artifact_json=? WHERE artifact_id=?",(canonical_json(forged),original.artifact_id))
        tables=[r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rb_%'")]
        for table in tables:
            for column in raw.execute(f'PRAGMA table_info("{table}")').fetchall():
                if column[1].endswith("json"):
                    continue
                for before,after in ((original.artifact_id,forged.artifact_id),(original.content_hash,forged.content_hash)):
                    raw.execute(f'UPDATE "{table}" SET "{column[1]}"=? WHERE "{column[1]}"=?',(after,before))
    with pytest.raises(ValueError,match="REAL_SOURCE_OR_MATH_REPLAY_MISMATCH"):
        p["repo"].load(forged.artifact_id)


def test_ra11_manual_unknown_evidence_and_absolute_v4_correction(bridge):
    from football_system.application.prospective_requests import EvidenceImportRequestV1
    from football_system.application.real_bridge import RealProspectiveDecisionAdapterV1
    from football_system.domain.prospective_evidence import ManualVerifiedImportV1
    from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
    from football_system.infrastructure.files.prospective import verified_manual_bytes
    p=bridge
    now=p["clock"].now()
    command(p,"admission","manual-evidence-admission",program_id=p["program"].artifact_id,kind="EVIDENCE",source_identity="test-evidence",state="ADMITTED",
        effective_at_utc=now,expires_at_utc=KICKOFF+timedelta(days=1),rights_reference="self-authored fixture",evidence_file="proof.txt",evidence_hash=p["proof"])
    raw=canonical_json(dict(category="LINEUP",team_id="home",status="UNKNOWN")).encode()
    (p["root"]/"lineup.json").write_bytes(raw)
    claim=ManualVerifiedImportV1(match_id="match",data_classification="SYNTHETIC",source_identity="test-evidence",source_reference="self-authored UNKNOWN lineup",
        source_file="lineup.json",source_hash=hashlib.sha256(raw).hexdigest(),rights_basis="SELF_OBSERVED",rights_reference="self-authored software input",
        retention_until_utc=KICKOFF+timedelta(days=1),verified_by="synthetic-test",verified_at_utc=now,captured_at_utc=now,published_at_utc=now,available_at_utc=now,
        fact_category="LINEUP",assertion_class="FACT",confidence="0",structured_payload=dict(category="LINEUP",team_id="home",status="UNKNOWN"))
    binding=SqlAlchemyProspectiveRepository(p["sessions"],clock=p["clock"]).import_evidence(
        EvidenceImportRequestV1(request_key="evidence",evidence=claim),verified_manual_bytes(p["root"],claim,p["clock"]))
    run=prepare(p,evidence_binding_ids=(binding.artifact_id,))
    packet=p["repo"].load(run.packet.artifact_id)
    unit=packet.market_units[0]
    from football_system.domain.market_v2 import distribution,MarketKeyV2
    proposed=distribution(MarketKeyV2(market_type="THREE_WAY"),("1","0","0"))
    submission=canonical_json(dict(schema_version="LLM_REVIEW_V4",analysis_id=run.analysis.artifact_id,packet_id=packet.artifact_id,packet_hash=packet.content_hash,
        market_reviews=[dict(match_id="match",market_key=unit.review_context.market_key,review_context_id=unit.review_context_id,review_context_hash=unit.review_context_hash,
            status="VALID",p_llm=proposed,assessment_confidence="1",scenarios=[],preferred_outcomes=[],avoid_outcomes=[],counter_scenarios=[],risk_tags=[],
            reasoning_summary="A synthetic absolute probability proposal; UNKNOWN lineup remains unknown.",evidence_refs=[binding.football_evidence.artifact_id],limitations=["software only"])]))
    reason=dict(match_id="match",market_key=unit.review_context.market_key,review_context_id=unit.review_context_id,categories=("CONFIRMED_LINEUP_CHANGE",),
        assertion_class="FACT",rationale="This invalid claim must fail",evidence_snapshot_ids=(binding.snapshot.artifact_id,))
    with pytest.raises(ValueError,match="UNKNOWN_OR_EXPECTED_LINEUP_IS_NOT_CONFIRMED"):
        command(p,"lock","false-confirmation",run_id=run.artifact_id,raw_review=submission,reasons=(reason,))
    reason.update(categories=("DATA_QUALITY_DOWNGRADE",),assertion_class="ANALYSIS",rationale="UNKNOWN is not confirmed")
    lock=command(p,"lock","valid-review",run_id=run.artifact_id,raw_review=submission,reasons=(reason,))
    layers={v.name:v.probabilities for v in lock.frames[0].layers}
    assert layers["P_llm"]==proposed
    from decimal import Decimal
    assert abs(layers["P_final"].probability("HOME_WIN")-layers["P_base"].probability("HOME_WIN"))<=Decimal("0.08")
    packet_json,markdown=RealProspectiveDecisionAdapterV1(p["repo"]).packet_files(run.artifact_id)
    assert packet_json==canonical_json(packet) and "SYNTHETIC_SOFTWARE_ACCEPTANCE" in markdown and "UNKNOWN" in markdown
    assert p["repo"].load(lock.artifact_id)==lock


@pytest.mark.parametrize("kind",["MARKET","SPORTTERY","MODEL"])
def test_ra21_current_admission_expires_after_prepare(bridge,kind):
    p=bridge
    old=p["admissions"][kind]
    now=p["clock"].now()
    command(p,"admission","short-lived",program_id=p["program"].artifact_id,kind=kind,source_identity=old.source_identity,state="ADMITTED",
        effective_at_utc=now,expires_at_utc=now+timedelta(seconds=2),rights_reference="short synthetic authorization",evidence_file="proof.txt",
        evidence_hash=p["proof"],previous_id=old.artifact_id,credential_verified=kind=="MARKET")
    run=prepare(p)
    p["clock"].at+=timedelta(seconds=3)
    with pytest.raises(ValueError,match="SOURCE_ADMISSION_EXPIRED_OR_REVOKED"):
        command(p,"lock","expired",run_id=run.artifact_id)
    assert p["repo"].load(run.artifact_id)==run


@pytest.mark.parametrize("key",["market_binding","sp_binding"])
def test_ra21_explicit_source_invalidation_blocks_old_preparing_head(bridge,key):
    p=bridge
    run=prepare(p)
    p["clock"].at+=timedelta(seconds=1)
    command(p,"source-invalidate","withdraw",source_id=p[key].artifact_id,reason="explicit withdrawal of an erroneous source binding",
        evidence_file="proof.txt",evidence_hash=p["proof"])
    with pytest.raises(ValueError,match="SOURCE_BINDING_INVALIDATED"):
        command(p,"lock","cannot-use-withdrawn",run_id=run.artifact_id)
    assert p["repo"].load(run.artifact_id)==run


def test_ra03_bootstrap_needs_no_match_ticket_or_synthetic_analysis_seed(bridge):
    import json
    p=bridge
    engine=create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    sessions=create_session_factory(engine)
    clock=SyntheticProspectiveClock(PERSISTED+timedelta(hours=1))
    repo=SqlAlchemyRealBridgeRepository(sessions,evidence_root=p["root"],clock=clock)
    with p["sessions"]() as original:
        requests=original.execute(text("SELECT operation,request_json FROM rb_receipts WHERE sequence<=(SELECT sequence FROM rb_receipts WHERE request_key='epoch') ORDER BY sequence")).all()
    for op,raw in requests:
        repo.execute(op,REQUEST_TYPES[op].model_validate(json.loads(raw)))
    with sessions() as session:
        for table in ("matches","mm_analyses","mm_strategy_plans","pv_epochs"):
            # Bootstrap only registers declarations/policy/state pins. It never
            # creates the fixtures, an analysis, a Strategy seed or a V1 epoch.
            if session.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:name"),{"name":table}):
                assert session.scalar(text(f"SELECT count(*) FROM {table}"))==0
        assert session.scalar(text("SELECT count(*) FROM rb_epochs"))==1
    engine.dispose()


@pytest.mark.parametrize("bridge",["unavailable"],indirect=True)
def test_ra05_ra06_partial_model_availability_preserves_entire_bucket(bridge):
    p=bridge
    run=prepare(p)
    analysis=p["repo"].load(run.analysis.artifact_id)
    assert run.status=="UNAVAILABLE" and run.packet is None
    assert len(run.identities)==len(analysis.units)==2
    assert analysis.units[0].p_quant is not None
    assert analysis.units[1].p_quant is analysis.units[1].p_base is None
    assert analysis.units[1].model.reason=="INSUFFICIENT_PRIOR_MATCHES"
    with pytest.raises(ValueError,match="STALE_PREPARING_HEAD_OR_ALREADY_LOCKED"):
        command(p,"lock","cannot-drop-unit",run_id=run.artifact_id)


def test_ra14_new_epoch_cannot_reacquire_program_prediction_slot(bridge):
    p=bridge
    run=prepare(p)
    lock=command(p,"lock","first-lock",run_id=run.artifact_id)
    p["clock"].at=p["epoch"].ends_at_utc
    command(p,"epoch-close","close",epoch_id=p["epoch"].artifact_id)
    old_epoch=p["epoch"]
    # Broad *declared* software fixture age permits reuse of the exact input;
    # it does not bypass a clock or slot gate or alter a numerical policy.
    policy=command(p,"policy","next-policy",program_id=p["program"].artifact_id,result_source_identity="test-result",
        maximum_odds_age_seconds=30*86400,minimum_bookmaker_count=2)
    anchor=command(p,"anchor","next-anchor",program_id=p["program"].artifact_id,policy_id=policy.artifact_id,model_pin_id=p["pin"].artifact_id,
        starts_at_utc=p["clock"].now()+timedelta(minutes=1),ends_at_utc=p["clock"].now()+timedelta(days=3))
    p["epoch"]=command(p,"epoch","next-epoch",anchor_id=anchor.artifact_id,previous_epoch_id=old_epoch.artifact_id)
    at=p["clock"].now()+timedelta(minutes=2)
    future=at+timedelta(days=1)
    captured=_capture("next-window",available_at=at-timedelta(minutes=1))
    fields=captured.model_dump()
    fields["request"].update(kickoff_from_utc=future-timedelta(days=1),kickoff_to_utc=future+timedelta(days=1))
    fields["registration"]["matches"][0]["kickoff_at_utc"]=future
    fields["registration"]["canonical_matches"][0]["identity"]["kickoff_at_utc"]=future
    fields["observations"][0]["kickoff_at_utc"]=future
    SqlAlchemyMatchIdentityRepository(p["sessions"],clock=lambda:at).register_fixture_ingestion(type(captured).model_validate(fields))
    p["clock"].at=at
    slate=command(p,"slate","next-slate",epoch_id=p["epoch"].artifact_id,match_ids=("match",),source_reference="same canonical match, explicitly new software window",
        source_file="proof.txt",source_hash=p["proof"])
    p["bucket"]=command(p,"bucket","next-bucket",slate_id=slate.artifact_id,kickoff_at_utc=future)
    next_run=prepare(p,"next-prepare")
    with pytest.raises(ValueError,match="DUPLICATE_OFFICIAL_PREDICTION"):
        command(p,"lock","cross-epoch-lock",run_id=next_run.artifact_id)
    with p["sessions"]() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_prediction_slots"))==1
        assert session.scalar(text("SELECT count(*) FROM rb_prediction_versions"))==1
    assert p["repo"].load(lock.artifact_id)==lock


def test_ra18_test_clock_cannot_register_a_real_observation_program(bridge):
    p=bridge
    with pytest.raises(ValueError,match="TEST_PROGRAM_CLOCK_MISMATCH"):
        command(p,"program","cannot-relabel",observation_program_id="fake-live",competition_ids=("competition",),season_id="2026/27",
            scope_reference="must not become real observations",provenance="LIVE_OBSERVATION")


def test_ra21_known_result_received_same_utc_after_prepare_blocks_lock(bridge):
    from football_system.domain.settlement import MatchResult
    from football_system.domain.archive import match_result_payload_sha256
    from football_system.infrastructure.database.historical_repositories import _append_match_result
    from tests.integration.test_fixture_ingestion_persistence import PROVIDER_A
    p=bridge
    run=prepare(p)
    now=p["clock"].now()
    # The canonical fixture can be wrong; any already-known result must dominate
    # its future kickoff claim. This is an explicitly contradictory software fact.
    with p["sessions"].begin() as session:
        _append_match_result(session,MatchResult(match_result_id="already-known",match_id="match",provider_code=PROVIDER_A,
            home_goals=2,away_goals=1,observed_at_utc=now,available_at_utc=now,ingested_at_utc=now,source_result_key="already-known",
            payload_hash=match_result_payload_sha256(2,1)),"provider-mapping")
    with pytest.raises(ValueError,match="KNOWN_RESULT_BEFORE_LOCK"):
        command(p,"lock","known-result",run_id=run.artifact_id)
    assert p["repo"].load(run.artifact_id)==run
