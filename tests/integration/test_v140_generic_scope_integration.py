"""IC1: actual qualified JP/KR-shaped synthetic scopes, not a five-league allowlist."""
import json
import socket

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from scripts import portfolio_acceptance_inputs as inputs
from scripts.scoped_model_acceptance_inputs import review
from football_system.domain.openfootball_scope import CompetitionScopeProfileV2


@pytest.mark.parametrize("pass_type,atoms",[("2X1",1),("3X4",4),("4X11",11)])
def test_japan_korea_shaped_qualified_models_cross_bucket_portfolio(tmp_path,monkeypatch,pass_type,atoms):
    def denied(*args,**kwargs):
        raise AssertionError("IC_SYNTHETIC_SCOPE_ACCEPTANCE_NO_NETWORK")
    monkeypatch.setattr(socket.socket,"connect",denied)
    original=inputs.source
    def regional_source(graph,label="A",team_count=4,zone="Europe/Berlin"):
        country,zone=("JP","Asia/Tokyo") if label=="A" else ("KR","Asia/Seoul")
        value=original(graph,country,team_count,zone)
        prior=value.subject
        value.subject=graph.repo.prepare_scope(profile=value.profile,country_code=country,rights=prior.rights,
            source_prefix=prior.source_prefix,acquisition=prior.acquisition,parser_policy=prior.parser_policy,
            classification="SYNTHETIC_SOFTWARE_ACCEPTANCE")
        value.review=review(graph,value.subject,value.profile.source_id,value.prefix+"/regional-qualification-review.json")
        return value
    # Only the self-authored fixture constructor changes; every qualification,
    # model/review/pin/reader/Portfolio operation is the accepted implementation.
    monkeypatch.setattr(inputs,"source",regional_source)
    graph=inputs.setup(tmp_path)
    try:
        assert {s.subject.country_code for s in graph.sources}=={"JP","KR"}
        assert {s.profile.time_policy.timezone for s in graph.sources}=={"Asia/Tokyo","Asia/Seoul"}
        assert len({s.qualification.owner.scope_id for s in graph.sources})==2
        assert len({s.release.payload.state.state_hash for s in graph.sources})==2
        assert len({s.pin.artifact_id for s in graph.sources})==2
        assert len({s.kickoff for s in graph.sources})==2
        assembly=inputs.prepare(graph,pass_type)
        plan=graph.pf.load(assembly.content_payload.strategy_plan.artifact_id)
        assert len(plan.candidates)==1 and plan.candidates[0].expanded_atomic_bet_count==atoms
        lock=graph.pf_call("lock","regional-lock",assembly_id=assembly.artifact_id)
        assert graph.pf_call("lock","regional-lock",assembly_id=assembly.artifact_id)==lock
        inputs.results(graph)
        observations=graph.pf_call("results","regional-results",portfolio_lock_id=lock.artifact_id)
        settled=graph.pf_call("settle","regional-settlement",portfolio_lock_id=lock.artifact_id,result_observation_set_id=observations.artifact_id)
        assert settled.content_payload.status=="SETTLED"
        assert settled.content_payload.ending_capital_fen==settled.content_payload.cash_fen+settled.content_payload.gross_payout_fen
        assert graph.pf.audit(settled.artifact_id)["real_prospective_samples"]==0
        with graph.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM pf_budget_claims"))==1
            assert session.scalar(text("SELECT count(*) FROM pf_shared_slots"))==4
            assert not session.execute(text("PRAGMA foreign_key_check")).all()
        # Unimplemented tournament/stage semantics remain explicit; generic
        # identifiers alone cannot silently grant continental/knockout eligibility.
        proposal=graph.sources[0].profile.model_dump(mode="python")|dict(competition_type="CONTINENTAL_CLUB_TOURNAMENT")
        with pytest.raises(ValidationError):
            CompetitionScopeProfileV2.model_validate(proposal)
        proof=dict(status="PASS_GENERIC_JP_KR_SHAPED_SCOPE_INTEGRATION",classification="SYNTHETIC_SOFTWARE_ACCEPTANCE",
            pass_type=pass_type,atomic_bets=atoms,countries=[s.subject.country_code for s in graph.sources],
            timezones=[s.profile.time_policy.timezone for s in graph.sources],scopes=[s.qualification.owner.model_dump() for s in graph.sources],
            model_states=[s.release.payload.state.state_hash for s in graph.sources],pins=[s.pin.artifact_id for s in graph.sources],
            lock=lock.artifact_id,settlement=settled.artifact_id,unsupported_tournament_profile_refused=True,
            real_competition_qualification_created=False,production_touched=False)
        (tmp_path/"generic-scope-integration-proof.json").write_text(json.dumps(proof,indent=2),encoding="utf-8")
    finally:
        graph.engine.dispose()
