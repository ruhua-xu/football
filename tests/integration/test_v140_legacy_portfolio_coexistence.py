"""A genuine released DecisionLock ledger and new Portfolio coexist in one DB."""
from collections import Counter
import hashlib
import json
import shutil
import socket

from sqlalchemy import text

from scripts import portfolio_acceptance_inputs as inputs
from tests.integration.openfootball_upgrade_support import sqlite_snapshot
from tests.integration.test_portfolio_backfill import genuine_database


def test_published_legacy_ledger_and_new_qualified_portfolio_keep_separate_money(tmp_path,monkeypatch):
    def denied(*args,**kwargs):
        raise AssertionError("INTEGRATION_COEXISTENCE_SYNTHETIC_ONLY_NO_NETWORK")
    monkeypatch.setattr(socket.socket,"connect",denied)
    # This helper executes the archived v1.3.1 binary, not current code imitating
    # an old ledger. Reuse its synthetic evidence root and upgrade a private copy.
    original,legacy,_=genuine_database(tmp_path)
    before=sqlite_snapshot(original)
    upgraded=original.parent/"isolated-scoped.sqlite"
    shutil.copy2(original,upgraded)
    g=inputs.setup(original.parent)
    try:
        assert g.database==upgraded
        assembly=inputs.prepare(g,"4X11")
        lock=g.pf_call("lock","coexisting-lock",assembly_id=assembly.artifact_id)
        assert lock.content_payload.budget_fen==10000  # Dedicated grant, not sum of old and source budgets.
        inputs.results(g)
        result=g.pf_call("results","coexisting-results",portfolio_lock_id=lock.artifact_id)
        settled=g.pf_call("settle","coexisting-settlement",portfolio_lock_id=lock.artifact_id,result_observation_set_id=result.artifact_id)
        value=settled.content_payload
        assert value.status=="SETTLED"
        assert value.ending_capital_fen==value.cash_fen+value.gross_payout_fen
        assert value.profit_loss_fen==value.gross_payout_fen-value.stake_fen
        assert g.pf.audit(settled.artifact_id)["status"]=="PORTFOLIO_AUDIT_PASS"
        assert g.bridge.audit(legacy["report_id"])["status"]=="AUDIT_PASS"
        after=sqlite_snapshot(upgraded)
        for part in ("schema","layout"):
            assert all(after[part][key]==old for key,old in before[part].items()),part
        for part in ("rows","artifacts"):
            assert all(not(Counter(old)-Counter(after[part][key])) for key,old in before[part].items()),part
        # All original financial/ownership rows remain exactly equal, not merely
        # a subset. The new qualified sources use disjoint matches/programs.
        for table in ("rb_locks","rb_prediction_slots","rb_prediction_versions","rb_head_consumptions"):
            assert after["rows"][table]==before["rows"][table]
        with g.sessions() as session:
            legacy_slots=session.scalar(text("SELECT count(*) FROM pf_shared_slots WHERE initial_legacy_lock_id IS NOT NULL"))
            pf_slots=session.scalar(text("SELECT count(*) FROM pf_shared_slots WHERE initial_portfolio_lock_id IS NOT NULL"))
            assert legacy_slots==len(before["rows"]["rb_prediction_slots"])>0
            assert pf_slots==4
            assert session.scalar(text("SELECT count(*) FROM pf_budget_claims"))==1
            assert session.scalar(text("SELECT sum(budget_fen) FROM pf_budget_grants"))==10000
        proof=dict(status="PASS_GENUINE_LEGACY_AND_QUALIFIED_PORTFOLIO_COEXISTENCE",archived_commit="f689ff7340598fd37f061473383461117ecb637d",
            classification="SYNTHETIC_SOFTWARE_ACCEPTANCE",migration_head=after["head"],legacy_report_id=legacy["report_id"],
            legacy_artifact_json_sha256=hashlib.sha256(json.dumps(before["artifacts"],sort_keys=True).encode()).hexdigest(),
            original_ddl_rows_artifact_json_preserved=True,legacy_financial_and_ownership_rows_exactly_unchanged=True,
            legacy_slots=legacy_slots,portfolio_slots=pf_slots,portfolio_lock=lock.artifact_id,portfolio_settlement=settled.artifact_id,
            separate_portfolio_grants=1,portfolio_budget_fen=value.budget_fen,stake_fen=value.stake_fen,cash_fen=value.cash_fen,
            payout_fen=value.gross_payout_fen,pnl_fen=value.profit_loss_fen,production_touched=False)
        (tmp_path/"legacy-portfolio-coexistence-proof.json").write_text(json.dumps(proof,indent=2),encoding="utf-8")
    finally:
        g.engine.dispose()
