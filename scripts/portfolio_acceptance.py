"""Installed-wheel, network-denied, two-qualified-model Portfolio acceptance."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import socket
import sys

import football_system
from sqlalchemy import text


def exercise(root, pass_type, atoms):
    from scripts.portfolio_acceptance_inputs import setup, prepare, results
    g = setup(root)
    try:
        assembly = prepare(g, pass_type)
        plan = g.pf.load(assembly.content_payload.strategy_plan.artifact_id)
        assert len(plan.candidates) == 1 and plan.candidates[0].expanded_atomic_bet_count == atoms
        assert len({s.release.payload.state.state_hash for s in g.sources}) == 2
        assert len({s.pin.artifact_id for s in g.sources}) == 2 and len({s.kickoff for s in g.sources}) == 2
        with g.sessions() as session:
            assert all(session.scalar(text("SELECT count(*) FROM "+table)) == 0 for table in ("pf_shared_heads","pf_shared_slots","pf_budget_claims"))
        lock = g.pf_call("lock", "installed-lock", assembly_id=assembly.artifact_id)
        assert g.pf_call("lock", "installed-lock", assembly_id=assembly.artifact_id) == lock
        with g.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM pf_shared_heads")) == 2
            assert session.scalar(text("SELECT count(*) FROM pf_shared_slots")) == 4
            assert session.scalar(text("SELECT count(*) FROM pf_budget_claims")) == 1
            assert session.scalar(text("SELECT count(*) FROM rb_locks")) == 0
        results(g)
        observed = g.pf_call("results", "installed-results", portfolio_lock_id=lock.artifact_id)
        settlement = g.pf_call("settle", "installed-settlement", portfolio_lock_id=lock.artifact_id, result_observation_set_id=observed.artifact_id)
        c = settlement.content_payload
        assert c.status == "SETTLED" and c.budget_fen == 10000 and c.stake_fen > 0
        assert c.ending_capital_fen == c.cash_fen+c.gross_payout_fen and c.profit_loss_fen == c.gross_payout_fen-c.stake_fen
        assert g.pf.audit(settlement.artifact_id)["status"] == "PORTFOLIO_AUDIT_PASS"
        with g.sessions() as session:
            assert not session.execute(text("PRAGMA foreign_key_check")).all()
            assert session.execute(text("PRAGMA integrity_check")).all() == [("ok",)]
        return dict(pass_type=pass_type, atomic_bets=atoms, source_states=[s.release.payload.state.state_hash for s in g.sources],
            pin_ids=[s.pin.artifact_id for s in g.sources], source_scopes=[s.qualification.owner.model_dump() for s in g.sources],
            lock_id=lock.artifact_id, settlement_id=settlement.artifact_id, finances=c.model_dump(mode="json"),
            prediction_slots=4, portfolio_money_ledgers=1, real_observation_count=0)
    finally:
        g.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    prefix = Path(sys.prefix).resolve()
    assert prefix in Path(football_system.__file__).resolve().parents
    assert importlib.metadata.version("football-system") == "1.4.0.dev0"
    # Only acceptance scripts are imported from the checkout. The already-loaded
    # application package and every one of its submodules must remain installed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_PORTFOLIO_NO_NETWORK")
    socket.socket.connect = denied
    args.work_dir.mkdir(parents=True, exist_ok=False)
    cases = [exercise(args.work_dir / kind, kind, atoms) for kind,atoms in (("2X1",1),("3X4",4),("4X11",11))]
    origins = {name:str(Path(module.__file__).resolve()) for name,module in sys.modules.items()
        if (name == "football_system" or name.startswith("football_system.")) and getattr(module,"__file__",None)}
    assert all(prefix in Path(path).parents for path in origins.values())
    proof = dict(status="PORTFOLIO_INSTALLED_WHEEL_ACCEPTANCE_PASS", classification="SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE",
        cases=cases, runtime_origins=origins, provider_http_sends=0, llm_http_sends=0, real_performance_claim=False, production_touched=False)
    (args.work_dir / "portfolio-installed-proof.json").write_text(json.dumps(proof,indent=2),encoding="utf-8")
    print("PORTFOLIO_INSTALLED_WHEEL_ACCEPTANCE_PASS")


if __name__ == "__main__":
    main()
