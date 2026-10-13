"""Actual ledger guards, stale inputs, atomic rollback, legacy/PF races and P&L."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import socket
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Insert

from football_system.application.portfolio_requests import REQUEST_TYPES
from tests.integration.portfolio_fixtures import setup, prepare, results, authorize_budget
from scripts.scoped_model_acceptance_inputs import review, write
from football_system.domain.portfolio import AtomicPortfolioLockV1, LockContentV1, PortfolioCapitalDeclarationV1


@pytest.fixture
def g(tmp_path, monkeypatch, request):
    def denied(*args,**kwargs):
        raise AssertionError("PF_SOFTWARE_ACCEPTANCE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket,"connect",denied)
    value=setup(tmp_path,maximum_odds_age_seconds=getattr(request,"param",3600))
    prepare(value,"2X1")
    yield value
    value.engine.dispose()


def counts(g):
    with g.sessions() as s:
        return {name:s.scalar(text("SELECT count(*) FROM "+name)) for name in
            ("pf_shared_heads","pf_shared_slots","pf_shared_versions","pf_budget_claims","pf_locks","rb_locks")}


def assert_shared_registry_is_immutable(g):
    before=counts(g)
    for conflict in ("OR IGNORE","OR REPLACE"):
        with pytest.raises(IntegrityError),g.sessions.begin() as session:
            session.execute(text("INSERT "+conflict+" INTO pf_shared_heads SELECT * FROM pf_shared_heads LIMIT 1"))
        with pytest.raises(IntegrityError),g.sessions.begin() as session:
            session.execute(text("INSERT "+conflict+" INTO pf_shared_slots SELECT * FROM pf_shared_slots LIMIT 1"))
    assert counts(g)==before


@pytest.mark.parametrize("fault",["stale_prices","kickoff","exact_lead","model_invalidated","authority_revoked","prelock_replaced"])
def test_any_member_failure_refuses_whole_portfolio_without_budget_or_prediction_claims(g,fault):
    source=g.sources[0]
    if fault=="stale_prices":
        g.clock.value+=timedelta(hours=2)
    elif fault=="kickoff":
        g.clock.value=source.kickoff+timedelta(seconds=1)
    elif fault=="exact_lead":
        g.clock.value=source.kickoff-timedelta(seconds=60)
    elif fault=="model_invalidated":
        source.call("model-invalidate","invalidate",pin_id=source.pin.artifact_id,reason="synthetic revocation",
            evidence_file=source.proof.evidence_reference,evidence_hash=source.proof.evidence_sha256)
    elif fault=="authority_revoked":
        g.evidence.trusted_authorities.pop(source.authority.evidence_reference)
    else:
        source.call("replace","replacement",**source.prepare,expected_head_id=source.run.artifact_id,reason="synthetic current-head replacement")
    before=counts(g)
    with pytest.raises(ValueError):
        g.pf_call("lock","invalid-lock",assembly_id=g.assembly.artifact_id)
    assert counts(g)==before
    assert before["pf_budget_claims"]==before["pf_locks"]==0


@pytest.mark.parametrize("fault",["first_head","after_claims","after_seal","late_publication"])
def test_mid_publication_failure_rolls_back_every_new_financial_and_ownership_row(g,monkeypatch,fault):
    before=counts(g)
    if fault=="first_head":
        original=Session.execute
        seen=0
        def execute(self,statement,*args,**kwargs):
            nonlocal seen
            if isinstance(statement,Insert) and statement.table.name=="pf_shared_heads":
                seen+=1
                if seen==2:
                    raise ValueError("SYNTHETIC_SECOND_HEAD_FAILURE")
            return original(self,statement,*args,**kwargs)
        monkeypatch.setattr(Session,"execute",execute)
    elif fault=="after_claims":
        original=g.pf._project
        def project(session,value):
            original(session,value)
            if value.schema_version=="ATOMIC_PORTFOLIO_LOCK_V1":
                raise ValueError("SYNTHETIC_AFTER_BUDGET_FAILURE")
        monkeypatch.setattr(g.pf,"_project",project)
    else:
        original=g.pf._store
        def store(session,values,key):
            original(session,values,key)
            if any(v.schema_version=="ATOMIC_PORTFOLIO_LOCK_V1" for v in values):
                if fault=="late_publication":
                    g.clock.value=min(s.kickoff for s in g.sources)-timedelta(seconds=60)
                else:
                    raise ValueError("SYNTHETIC_AFTER_SEAL_FAILURE")
        monkeypatch.setattr(g.pf,"_store",store)
    with pytest.raises(ValueError):
        g.pf_call("lock","failed-publication",assembly_id=g.assembly.artifact_id)
    assert counts(g)==before
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM pf_artifacts WHERE schema_version='ATOMIC_PORTFOLIO_LOCK_V1'"))==0
        assert not session.execute(text("PRAGMA foreign_key_check")).all()


@pytest.mark.parametrize("conflict",["OR IGNORE","OR REPLACE"])
def test_actual_legacy_writer_conflict_policy_cannot_bypass_portfolio_head_guard(g,monkeypatch,conflict):
    lock=g.pf_call("lock","pf-wins",assembly_id=g.assembly.artifact_id)
    before=counts(g)
    original=Session.execute
    def execute(self,statement,*args,**kwargs):
        if isinstance(statement,Insert) and statement.table.name in {"rb_head_consumptions","rb_prediction_slots","rb_prediction_versions"}:
            statement=statement.prefix_with(conflict)
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,"execute",execute)
    with pytest.raises(ValueError):
        g.sources[0].call("lock","legacy-after-pf",run_id=g.sources[0].run.artifact_id)
    assert counts(g)==before and g.pf.audit(lock.artifact_id)["status"]=="PORTFOLIO_AUDIT_PASS"


def test_legacy_first_blocks_portfolio_and_shared_registry_replacements(g):
    source=g.sources[0]
    source.call("lock","legacy-first",run_id=source.run.artifact_id)
    before=counts(g)
    with pytest.raises(ValueError):
        g.pf_call("lock","pf-after-legacy",assembly_id=g.assembly.artifact_id)
    assert counts(g)==before
    assert_shared_registry_is_immutable(g)


@pytest.mark.parametrize("conflict",["OR IGNORE","OR REPLACE"])
def test_actual_legacy_slot_write_is_rejected_even_if_faulty_writer_omits_head_row(g,monkeypatch,conflict):
    g.pf_call("lock","pf-slot-owner",assembly_id=g.assembly.artifact_id)
    before=counts(g)
    store=g.bridge._store
    def without_head(session,values,receipt,relations):
        return store(session,values,receipt,tuple(r for r in relations if r[0]!="rb_head_consumptions"))
    monkeypatch.setattr(g.bridge,"_store",without_head)
    original=Session.execute
    def execute(self,statement,*args,**kwargs):
        if isinstance(statement,Insert) and statement.table.name in {"rb_prediction_slots","rb_prediction_versions"}:
            statement=statement.prefix_with(conflict)
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,"execute",execute)
    with pytest.raises(ValueError) as caught:
        g.sources[0].call("lock","faulty-legacy-slot",run_id=g.sources[0].run.artifact_id)
    assert "portfolio ledger invariant" in str(caught.value.__cause__)
    assert counts(g)==before
    assert_shared_registry_is_immutable(g)


@pytest.mark.parametrize("operation",["lock","replace"])
def test_concurrent_actual_legacy_operation_and_portfolio_lock_have_one_winner(g,operation):
    barrier=Barrier(2)
    def portfolio():
        barrier.wait(timeout=10)
        try:
            g.pf_call("lock","racing-pf",assembly_id=g.assembly.artifact_id)
            return "COMMITTED"
        except (ValueError,OperationalError):
            return "REJECTED"
    def legacy():
        barrier.wait(timeout=10)
        source=g.sources[0]
        try:
            if operation=="lock":
                source.call("lock","racing-legacy",run_id=source.run.artifact_id)
            else:
                source.call("replace","racing-legacy",**source.prepare,expected_head_id=source.run.artifact_id,reason="concurrent replacement")
            return "COMMITTED"
        except (ValueError,OperationalError):
            return "REJECTED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=pool.submit(portfolio),pool.submit(legacy)
        assert sorted((first.result(),second.result()))==["COMMITTED","REJECTED"]
    actual=counts(g)
    if actual["pf_locks"]:
        assert actual["pf_budget_claims"]==1 and actual["pf_shared_heads"]==2 and actual["pf_shared_slots"]==4
    else:
        assert actual["pf_budget_claims"]==0 and actual["pf_shared_heads"]==1


def test_duplicate_snapshot_or_same_run_cannot_be_an_extra_leg(g):
    second=g.pf_call("snapshot","same-run-again",source_run_id=g.sources[0].run.artifact_id,
        imported_review_id=g.sources[0].audit.review.artifact_id,review_mode="EXPLICIT_V4_ABSTENTION")
    ticket=g.pf_call("ticket-requests","extra-ticket",requests=({"pass_type":"2X1","choices":[
        {"match_id":t.match_id,"market_key":{"market_type":"THREE_WAY"},"outcomes":["HOME_WIN"]} for t in g.sources[0].targets]},))
    with pytest.raises(ValueError):
        g.pf_call("assembly","duplicate-run",snapshot_ids=tuple(sorted((g.snapshots[0].artifact_id,second.artifact_id))),
            budget_authority_id=g.budget.artifact_id,ticket_request_bundle_id=ticket.artifact_id)
    assert counts(g)["pf_budget_claims"]==0


def test_missing_artifact_is_a_durable_idempotent_rejection_without_any_claim(g):
    before=counts(g)
    for _ in range(2):
        with pytest.raises(ValueError,match="PF_INPUT_OR_SOURCE_REJECTED"):
            g.pf_call("lock","missing-assembly",assembly_id="unavailable-assembly")
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM pf_rejections WHERE request_key='missing-assembly'"))==1
        assert session.scalar(text("SELECT count(*) FROM pf_receipts WHERE request_key='missing-assembly'"))==0
    assert counts(g)==before


def test_budget_review_cannot_be_replaced_by_model_authority_or_amount_override(g):
    subject=g.budget_subject.model_copy(update={"budget_fen":20000,"funding_limit_fen":20000})
    request=REQUEST_TYPES["budget-record"](request_key="changed-budget",subject=subject,review=g.budget_review,authority=g.budget_authority)
    with pytest.raises(ValueError):
        g.pf.execute("budget-record",request)
    request=REQUEST_TYPES["budget-record"](request_key="model-is-not-money-authority",subject=g.budget_subject,review=g.budget_review,authority=g.sources[0].authority)
    with pytest.raises(ValueError):
        g.pf.execute("budget-record",request)
    assert counts(g)["pf_budget_claims"]==0


def test_missing_results_and_revisions_keep_one_financial_ledger_and_legacy_report_fails_closed(g):
    lock=g.pf_call("lock","lock",assembly_id=g.assembly.artifact_id)
    with pytest.raises(ValueError):
        g.sources[0].call("report","old-report-cannot-express-pf",epoch_id=g.sources[0].epoch.artifact_id,as_of_at_utc=g.clock())
    report=g.pf.audit(lock.artifact_id)
    assert report["real_prospective_samples"]==0 and len(report["source_census"])==2
    results(g,only_first=True)
    first=g.pf_call("results","partial-results",portfolio_lock_id=lock.artifact_id)
    partial=g.pf_call("settle","partial-settlement",portfolio_lock_id=lock.artifact_id,result_observation_set_id=first.artifact_id)
    assert partial.content_payload.status=="MISSING_RESULT" and partial.content_payload.profit_loss_fen is None
    results(g)
    second=g.pf_call("results","complete-results",portfolio_lock_id=lock.artifact_id)
    complete=g.pf_call("settle","complete-settlement",portfolio_lock_id=lock.artifact_id,result_observation_set_id=second.artifact_id)
    assert complete.content_payload.previous.artifact_id==partial.artifact_id and complete.content_payload.status=="SETTLED"
    results(g,revised=True)
    assert g.pf.audit(complete.artifact_id)["current_result_view"]=="STALE_RESULT_VERSION"
    third=g.pf_call("results","revised-results",portfolio_lock_id=lock.artifact_id)
    revised=g.pf_call("settle","revised-settlement",portfolio_lock_id=lock.artifact_id,result_observation_set_id=third.artifact_id)
    assert revised.content_payload.previous.artifact_id==complete.artifact_id
    assert revised.content_payload.profit_loss_fen==-revised.content_payload.stake_fen
    assert counts(g)["pf_budget_claims"]==1 and counts(g)["pf_shared_slots"]==4


def test_unsupported_result_does_not_invent_zero_payout(g):
    lock=g.pf_call("lock","lock",assembly_id=g.assembly.artifact_id)
    results(g,unsupported=True)
    observed=g.pf_call("results","unsupported-results",portfolio_lock_id=lock.artifact_id)
    settled=g.pf_call("settle","unsupported-settlement",portfolio_lock_id=lock.artifact_id,result_observation_set_id=observed.artifact_id)
    assert settled.content_payload.status=="UNSUPPORTED_SETTLEMENT_CASE"
    assert settled.content_payload.gross_payout_fen is settled.content_payload.profit_loss_fen is None


def test_budget_expiry_before_lock_is_independent_of_still_fresh_prices(g):
    budget=authorize_budget(g,label="short-lived",lifetime=timedelta(seconds=30))
    assembly=g.pf_call("assembly","short-lived-assembly",snapshot_ids=tuple(sorted(s.artifact_id for s in g.snapshots)),
        budget_authority_id=budget.artifact_id,ticket_request_bundle_id=g.ticket_bundle.artifact_id)
    g.clock.value+=timedelta(minutes=1)
    with pytest.raises(ValueError,match="PF_BUDGET_SCOPE_OR_VALIDITY_MISMATCH"):
        g.pf_call("lock","expired-budget",assembly_id=assembly.artifact_id)
    assert counts(g)["pf_budget_claims"]==counts(g)["pf_shared_heads"]==0


def test_postlock_model_invalidation_preserves_money_claims_and_historical_audit(g):
    lock=g.pf_call("lock","lock",assembly_id=g.assembly.artifact_id)
    before=counts(g)
    source=g.sources[0]
    source.call("model-invalidate","after-lock-invalidation",pin_id=source.pin.artifact_id,reason="synthetic post-lock source change",
        evidence_file=source.proof.evidence_reference,evidence_hash=source.proof.evidence_sha256)
    assert counts(g)==before
    audit=g.pf.audit(lock.artifact_id)
    assert audit["status"]=="PORTFOLIO_AUDIT_PASS"
    assert any(v.get("reason")=="MODEL_INVALIDATED" for v in audit["current_sources"])
    assert g.pf_call("lock","lock",assembly_id=g.assembly.artifact_id)==lock


@pytest.mark.parametrize("missing",["pf_shared_heads","pf_shared_slots","pf_shared_versions","pf_budget_claims","pf_seals"])
def test_actual_seal_or_commit_rejects_omitted_claim_or_final_seal(g,monkeypatch,missing):
    before=counts(g)
    original=Session.execute
    skipped=False
    def execute(self,statement,*args,**kwargs):
        nonlocal skipped
        if isinstance(statement,Insert) and statement.table.name==missing and not skipped:
            is_lock=True
            if missing=="pf_seals":
                key=next(v.value for k,v in statement._values.items() if str(k)=="artifact_id")
                is_lock=self.scalar(text("SELECT count(*) FROM pf_locks WHERE lock_id=:id"),{"id":key})==1
            if is_lock:
                skipped=True
                return original(self,text("SELECT 1"))
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,"execute",execute)
    with pytest.raises(ValueError):
        g.pf_call("lock","omitted-publication-part",assembly_id=g.assembly.artifact_id)
    assert skipped and counts(g)==before


def test_validly_resealed_lock_cannot_change_assembly_stake_and_cash(g,monkeypatch):
    original=g.pf._build
    def build(session,operation,request,at,receipt):
        value,extras=original(session,operation,request,at,receipt)
        if operation=="lock":
            content=value.content_payload.model_dump(mode="python")
            content.update(stake_fen=content["stake_fen"]-200,cash_fen=content["cash_fen"]+200)
            value=AtomicPortfolioLockV1.freeze(content_payload=LockContentV1(**content))
        return value,extras
    monkeypatch.setattr(g.pf,"_build",build)
    before=counts(g)
    with pytest.raises(ValueError) as caught:
        g.pf_call("lock","forged-balanced-money",assembly_id=g.assembly.artifact_id)
    assert "portfolio ledger invariant" in str(caught.value.__cause__)
    assert counts(g)==before


def test_validly_resealed_source_revalidation_must_replay_exact_historical_admissions(g,monkeypatch):
    from football_system.domain.portfolio import PortfolioLockRevalidationV1
    original=g.pf._build
    def build(session,operation,request,at,receipt):
        value,extras=original(session,operation,request,at,receipt)
        if operation=="lock":
            proof=extras[0]
            raw=proof.model_dump(mode="python",exclude={"artifact_id","content_hash","schema_version"})
            raw["members"][0]["source_revalidation_hash"]="0"*64
            forged=PortfolioLockRevalidationV1.freeze(**raw)
            content=value.content_payload.model_dump(mode="python")|dict(revalidation_hash=forged.content_hash)
            value=AtomicPortfolioLockV1.freeze(content_payload=LockContentV1(**content))
            extras=(forged,)
        return value,extras
    monkeypatch.setattr(g.pf,"_build",build)
    before=counts(g)
    with pytest.raises(ValueError,match="PF_SOURCE_REVALIDATION_REPLAY_MISMATCH"):
        g.pf_call("lock","forged-source-check",assembly_id=g.assembly.artifact_id)
    assert counts(g)==before


def alternate_run(g,source):
    slate=source.call("slate","alternate-slate",epoch_id=source.epoch.artifact_id,match_ids=tuple(t.match_id for t in source.targets),
        source_reference="SAME_COMPLETE_SELF_AUTHORED_COHORT",source_file=source.proof.evidence_reference,source_hash=source.proof.evidence_sha256)
    bucket=source.call("bucket","alternate-bucket",slate_id=slate.artifact_id,kickoff_at_utc=source.kickoff)
    run=source.call("prepare","alternate-run",**(source.prepare|dict(bucket_id=bucket.artifact_id)))
    assert run.artifact_id!=source.run.artifact_id and run.identities==source.run.identities
    return run


@pytest.mark.parametrize("recursive",[False,True])
@pytest.mark.parametrize("first",["PORTFOLIO","LEGACY"])
def test_distinct_real_heads_same_slots_are_exclusive_for_all_sql_conflict_policies(g,monkeypatch,recursive,first):
    event.listen(g.engine,"connect",lambda db,_:db.execute("PRAGMA recursive_triggers="+str(int(recursive))))
    g.engine.dispose()
    source=g.sources[0]
    alternative=alternate_run(g,source)
    if first=="PORTFOLIO":
        g.pf_call("lock","owner",assembly_id=g.assembly.artifact_id)
    else:
        source.call("lock","alternative-owner",run_id=alternative.artifact_id)
    before=counts(g)
    for conflict in ("","OR IGNORE","OR REPLACE"):
        with monkeypatch.context() as patch:
            original=Session.execute
            def execute(self,statement,*args,**kwargs):
                if conflict and isinstance(statement,Insert) and statement.table.name in {
                    "rb_head_consumptions","rb_prediction_slots","rb_prediction_versions","pf_shared_heads","pf_shared_slots","pf_shared_versions"}:
                    statement=statement.prefix_with(conflict)
                return original(self,statement,*args,**kwargs)
            patch.setattr(Session,"execute",execute)
            with pytest.raises(ValueError):
                if first=="PORTFOLIO":
                    source.call("lock","slot-collision-"+conflict.replace(" ","-"),run_id=alternative.artifact_id)
                else:
                    g.pf_call("lock","slot-collision-"+conflict.replace(" ","-"),assembly_id=g.assembly.artifact_id)
        assert counts(g)==before


def test_new_cycle_or_renamed_grant_cannot_reallocate_an_existing_capital_scope(g):
    subject=g.budget_subject
    capital=write(g.root,"same-scope-new-allocation.json",PortfolioCapitalDeclarationV1(funding_scope_id=subject.funding_scope_id,
        allocation_id="renamed-allocation",funding_limit_fen=subject.funding_limit_fen,legacy_authorized_capital_included=False,
        owner_reference="self-authored synthetic owner",provenance=subject.provenance))
    changed=subject.model_copy(update=dict(allocation_id="renamed-allocation",decision_cycle_id="renamed-cycle",capital_evidence=capital))
    approved=review(g,changed,changed.source_id,"duplicate-capital-review.json")
    with pytest.raises(ValueError):
        g.pf_call("budget-record","same-money-new-cycle",subject=changed,review=approved,authority=g.budget_authority)
    assert counts(g)["pf_budget_claims"]==0
    renamed=subject.model_copy(update=dict(funding_scope_id="different-scope",allocation_id="different-allocation"))
    with pytest.raises(ValueError,match="PF_CAPITAL_DECLARATION_SCOPE_MISMATCH"):
        g.pf._capital(renamed)


def test_no_bet_still_consumes_exactly_one_logical_allocation_and_all_source_predictions(g):
    choices=[dict(match_id=s.targets[0].match_id,market_key={"market_type":"THREE_WAY"},outcomes=["DRAW"]) for s in g.sources]
    bundle=g.pf_call("ticket-requests","no-bet-ticket",requests=({"pass_type":"2X1","choices":choices},))
    assembly=g.pf_call("assembly","no-bet-assembly",snapshot_ids=tuple(sorted(s.artifact_id for s in g.snapshots)),
        budget_authority_id=g.budget.artifact_id,ticket_request_bundle_id=bundle.artifact_id)
    assert assembly.content_payload.status=="NO_BET" and assembly.content_payload.stake_fen==0
    g.pf_call("lock","no-bet-lock",assembly_id=assembly.artifact_id)
    before=counts(g)
    assert before["pf_budget_claims"]==1 and before["pf_shared_slots"]==4
    with pytest.raises(ValueError):
        g.pf_call("lock","try-funded-assembly-after-no-bet",assembly_id=g.assembly.artifact_id)
    assert counts(g)==before


def test_populated_portfolio_downgrade_refuses_without_removing_graph_or_authority(g):
    from alembic import command
    from tests.integration.test_portfolio_backfill import config
    from tests.integration.openfootball_upgrade_support import sqlite_snapshot
    before=sqlite_snapshot(g.database)
    with pytest.raises(RuntimeError,match="populated Portfolio B artifacts"):
        command.downgrade(config(g.database),"9fb8cde5a621")
    assert sqlite_snapshot(g.database)==before


def test_known_result_before_future_kickoff_refuses_every_portfolio_claim(g):
    from football_system.domain.settlement import MatchResult
    from football_system.domain.archive import match_result_payload_sha256
    from football_system.infrastructure.database.historical_repositories import SqlAlchemyHistoricalRepository
    target=g.sources[0].targets[0]
    at=g.clock()
    result=MatchResult(match_result_id="unexpected-early-result",match_id=target.match_id,provider_code="SYNTHETIC_BRIDGE_FIXTURE",
        home_goals=1,away_goals=0,observed_at_utc=at,available_at_utc=at,ingested_at_utc=at,
        source_result_key="self-authored-known-result",payload_hash=match_result_payload_sha256(1,0))
    SqlAlchemyHistoricalRepository(g.sessions).append_match_results((result,))
    before=counts(g)
    with pytest.raises(ValueError,match="KNOWN_RESULT_BEFORE_LOCK|SCOPED_TARGET_KNOWN_RESULT"):
        g.pf_call("lock","known-result-lock",assembly_id=g.assembly.artifact_id)
    assert counts(g)==before


@pytest.mark.parametrize("g",[86400],indirect=True)
def test_earliest_unselected_bucket_still_controls_lock_deadline(g):
    # Keep every quote independently fresh at the tested boundary. Snapshot ID
    # order varies by private evidence path and must not select the failure gate.
    late=g.sources[1]
    bundle=g.pf_call("ticket-requests","late-only-ticket",requests=({"pass_type":"2X1","choices":[
        dict(match_id=t.match_id,market_key={"market_type":"THREE_WAY"},outcomes=["HOME_WIN"]) for t in late.targets]},))
    assembly=g.pf_call("assembly","late-only-assembly",snapshot_ids=tuple(sorted(s.artifact_id for s in g.snapshots)),
        budget_authority_id=g.budget.artifact_id,ticket_request_bundle_id=bundle.artifact_id)
    assert assembly.content_payload.earliest_member_kickoff==g.sources[0].kickoff<late.kickoff
    g.clock.value=g.sources[0].kickoff-timedelta(seconds=60)
    with pytest.raises(ValueError,match="LOOKAHEAD_RISK"):
        g.pf_call("lock","early-unselected-deadline",assembly_id=assembly.artifact_id)
    assert counts(g)["pf_shared_slots"]==counts(g)["pf_budget_claims"]==0


def test_duplicate_physical_matches_across_actual_independent_programs_are_rejected(g):
    from copy import copy
    from scripts.scoped_model_acceptance_inputs import create_program_and_pin
    clone=copy(g.sources[0])
    clone.label="A-SECOND-PROGRAM"
    create_program_and_pin(g,clone)
    admissions={}
    for kind,identity in (("MARKET","THE_ODDS_API"),("SPORTTERY","SPORTTERY_MANUAL"),("RESULT","synthetic-results-A")):
        admissions[kind]=clone.call("admission",kind,program_id=clone.program.artifact_id,kind=kind,source_identity=identity,
            state="ADMITTED",effective_at_utc=g.clock(),expires_at_utc=g.clock()+timedelta(days=5),rights_reference="self-authored duplicate-program boundary",
            verified_by="synthetic-operator",evidence_file=clone.proof.evidence_reference,evidence_hash=clone.proof.evidence_sha256,credential_verified=kind=="MARKET")
    policy=clone.call("policy","policy",program_id=clone.program.artifact_id,result_source_identity="synthetic-results-A",
        maximum_odds_age_seconds=3600,minimum_bookmaker_count=2)
    anchor=clone.call("anchor","anchor",program_id=clone.program.artifact_id,policy_id=policy.artifact_id,model_pin_id=clone.pin.artifact_id,
        starts_at_utc=g.clock()+timedelta(seconds=1),ends_at_utc=clone.kickoff+timedelta(days=2))
    epoch=clone.call("epoch","epoch",anchor_id=anchor.artifact_id)
    g.clock.value+=timedelta(seconds=2)
    slate=clone.call("slate","slate",epoch_id=epoch.artifact_id,match_ids=tuple(t.match_id for t in clone.targets),source_reference="SAME_PHYSICAL_CENSUS",
        source_file=clone.proof.evidence_reference,source_hash=clone.proof.evidence_sha256)
    bucket=clone.call("bucket","bucket",slate_id=slate.artifact_id,kickoff_at_utc=clone.kickoff)
    markets,prices=[],[]
    for i,(market,sp) in enumerate(clone.captures):
        markets.append(clone.call("market-bind",f"market-{i}",program_id=clone.program.artifact_id,admission_id=admissions["MARKET"].artifact_id,
            ingestion_id=market.ingestion_id,snapshot_id=market.consensus_batch.snapshots[0].snapshot_id).artifact_id)
        prices.append(clone.call("sp-bind",f"price-{i}",program_id=clone.program.artifact_id,admission_id=admissions["SPORTTERY"].artifact_id,
            ingestion_id=sp.ingestion_id,snapshot_id=sp.batch.snapshots[0].snapshot_id).artifact_id)
    run=clone.call("prepare","prepare",epoch_id=epoch.artifact_id,bucket_id=bucket.artifact_id,market_binding_ids=tuple(markets),sp_binding_ids=tuple(prices),budget_fen=10000)
    audit=clone.call("review-import","review",run_id=run.artifact_id)
    snapshot=g.pf_call("snapshot","duplicate-physical-snapshot",source_run_id=run.artifact_id,imported_review_id=audit.review.artifact_id,review_mode="EXPLICIT_V4_ABSTENTION")
    assert clone.program.artifact_id!=g.sources[0].program.artifact_id
    with pytest.raises(ValueError,match="PF_DUPLICATE_PHYSICAL_MATCH_MARKET"):
        g.pf_call("assembly","duplicate-physical-assembly",snapshot_ids=tuple(sorted((g.snapshots[0].artifact_id,snapshot.artifact_id))),
            budget_authority_id=g.budget.artifact_id,ticket_request_bundle_id=g.ticket_bundle.artifact_id)
    assert counts(g)["pf_shared_slots"]==counts(g)["pf_budget_claims"]==0
