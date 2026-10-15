"""Two qualified persisted models -> different buckets -> Portfolio -> settlement."""
import json
import socket

import pytest
from sqlalchemy import text

from tests.integration.portfolio_fixtures import setup, prepare, results


@pytest.fixture(autouse=True)
def network_forbidden(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("PORTFOLIO_SYNTHETIC_ONLY_NO_NETWORK")
    monkeypatch.setattr(socket.socket,"connect",denied)


@pytest.mark.parametrize("pass_type,atoms", [("2X1",1),("3X4",4),("4X11",11)])
def test_cross_competition_persisted_atomic_lock_and_single_settlement(tmp_path, pass_type, atoms):
    g = setup(tmp_path)
    try:
        assembly = prepare(g, pass_type)
        plan = g.pf.load(assembly.content_payload.strategy_plan.artifact_id)
        assert len(plan.candidates) == 1 and plan.candidates[0].expanded_atomic_bet_count == atoms
        assert len({s.profile.competition_id for s in g.sources}) == 2
        assert len({s.release.payload.state.state_hash for s in g.sources}) == 2
        assert len({s.kickoff for s in g.sources}) == 2
        with g.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM pf_shared_heads")) == 0
            assert session.scalar(text("SELECT count(*) FROM pf_shared_slots")) == 0
            assert session.scalar(text("SELECT count(*) FROM pf_budget_claims")) == 0
        lock = g.pf_call("lock","lock",assembly_id=assembly.artifact_id)
        assert lock.content_payload.budget_fen == 10000
        assert lock.content_payload.stake_fen > 0 and lock.content_payload.cash_fen+lock.content_payload.stake_fen == 10000
        assert g.pf_call("lock","lock",assembly_id=assembly.artifact_id) == lock
        with g.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM pf_shared_heads WHERE owner_kind='PORTFOLIO_LOCK'")) == 2
            assert session.scalar(text("SELECT count(*) FROM pf_shared_slots")) == 4
            assert session.scalar(text("SELECT count(*) FROM pf_budget_claims")) == 1
            assert session.scalar(text("SELECT count(*) FROM rb_locks")) == 0
        results(g)
        observed = g.pf_call("results","results",portfolio_lock_id=lock.artifact_id)
        settlement = g.pf_call("settle","settle",portfolio_lock_id=lock.artifact_id,result_observation_set_id=observed.artifact_id)
        value = settlement.content_payload
        assert value.status == "SETTLED" and value.profit_loss_fen == value.gross_payout_fen-value.stake_fen
        assert value.ending_capital_fen == value.cash_fen+value.gross_payout_fen
        assert g.pf.audit(settlement.artifact_id)["status"] == "PORTFOLIO_AUDIT_PASS"
        with g.sessions() as session:
            assert not session.execute(text("PRAGMA foreign_key_check")).all()
            assert session.execute(text("PRAGMA integrity_check")).all() == [("ok",)]
        proof = dict(status="PASS_PERSISTED_TWO_MODEL_CROSS_BUCKET_PORTFOLIO", pass_type=pass_type, atomic_bets=atoms,
            source_scopes=[s.qualification.owner.model_dump() for s in g.sources], source_states=[s.release.payload.state.state_hash for s in g.sources],
            pin_ids=[s.pin.artifact_id for s in g.sources], program_ids=[s.program.artifact_id for s in g.sources],
            run_ids=[s.run.artifact_id for s in g.sources], snapshot_ids=[s.artifact_id for s in g.snapshots],
            assembly_id=assembly.artifact_id, lock_id=lock.artifact_id, settlement_id=settlement.artifact_id,
            budget_fen=value.budget_fen, stake_fen=value.stake_fen, cash_fen=value.cash_fen,
            payout_fen=value.gross_payout_fen, pnl_fen=value.profit_loss_fen, production_touched=False)
        (tmp_path/"portfolio-persisted-e2e-proof.json").write_text(json.dumps(proof,indent=2),encoding="utf-8")
    finally:
        g.engine.dispose()
