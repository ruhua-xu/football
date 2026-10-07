"""Stage-1 regression specification: intentionally red on published v1.2.0.

All storage and source bytes are synthetic, isolated and self-authored. These
tests never access a production path or insert a REAL_MODEL_PIN directly.
"""
import socket

import pytest
from alembic import command
from sqlalchemy import inspect, text

from tests.integration import test_openfootball_production_binding as synthetic_data
from tests.integration.ofp_real_pin_fixtures import through_openfootball_binding, qualify_synthetic_source
from tests.integration.test_market_v2_upgrade import config_for
from football_system.application.real_bridge_requests import ProgramRequest,ModelPinRequest
from football_system.domain.pinned_model_source import OpenFootballModelSourceV1
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.real_bridge_sources import ExistingPinnedModelAccess
from football_system.infrastructure.database.session import create_database_engine,create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock
from football_system.interfaces.real_bridge_cli import dispatch_real_bridge
from football_system.infrastructure.database.real_bridge_head import SUPPORTED_REAL_BRIDGE_HEADS
from tests.integration.test_openfootball_production_binding import AT

source_graph = synthetic_data.graph


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args,**kwargs):
        raise AssertionError("SYNTHETIC_ONLY_NO_PROVIDER_OR_LLM_HTTP")
    monkeypatch.setattr(socket.socket,"connect",denied)


@pytest.fixture
def vertical(source_graph,monkeypatch):
    return through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch))


@pytest.mark.parametrize("head",["6c859ab273fe","7d96abc3840f","8ea7bcd49510"])
def test_g1_published_7d_cli_can_show_existing_synthetic_program(tmp_path,capsys,head):
    db=tmp_path/"synthetic-head-7d.sqlite"
    command.upgrade(config_for(db),head)
    url="sqlite:///"+db.as_posix()
    engine=create_database_engine(url)
    try:
        repo=SqlAlchemyRealBridgeRepository(create_session_factory(engine),evidence_root=tmp_path,clock=SyntheticProspectiveClock(AT))
        program=repo.execute("program",ProgramRequest(request_key="synthetic-program",observation_program_id="self-authored-head-regression",
            competition_ids=("self-authored-competition",),season_id="2026/27",scope_reference="SYNTHETIC_SOFTWARE_ACCEPTANCE_ONLY",provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE"))
        result=dispatch_real_bridge(["show","--database-url",url,"--evidence-root",str(tmp_path),"--artifact-id",program.artifact_id])
        assert result==0,"G1: released 7d schema is incorrectly rejected by CLI"
        assert program.artifact_id in capsys.readouterr().out
    finally:
        engine.dispose()


def test_supported_heads_are_explicit_and_unknown_head_is_rejected(tmp_path):
    assert SUPPORTED_REAL_BRIDGE_HEADS == {"6c859ab273fe","7d96abc3840f","8ea7bcd49510"}
    db=tmp_path/"synthetic-unknown-head.sqlite"
    command.upgrade(config_for(db),"7d96abc3840f")
    engine=create_database_engine("sqlite:///"+db.as_posix())
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num='unreviewed-head'"))
    assert dispatch_real_bridge(["show","--database-url","sqlite:///"+db.as_posix(),"--evidence-root",str(tmp_path),"--artifact-id","never-read"])==1
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM rb_receipts"))==0
    engine.dispose()


def test_g2_legitimate_ofp_binding_is_readable_by_existing_model_access(vertical):
    g=vertical
    request=ModelPinRequest(request_key="source-reader",program_id="not-used-by-reader",admission_id="not-used-by-reader",
        source_identity=g.source_identity,model_source=OpenFootballModelSourceV1(
            release_id=g.candidate["release_id"],release_hash=g.candidate["release_hash"],
            binding_id=g.candidate["binding_id"],binding_hash=g.candidate["binding_hash"]),
        scope_match_ids=tuple(g.candidate["target_scope"]))
    with g.sessions() as session:
        state,release_hash,authority_hash=ExistingPinnedModelAccess(g.inference).verify(session,request,g.clock())
    assert state.state_hash==g.candidate["state_hash"] and release_hash==g.candidate["release_hash"] and authority_hash==g.candidate["authority_hash"]


def test_g3_legitimate_ofp_ids_have_constrained_bridge_storage(vertical):
    g=vertical
    foreign_keys=inspect(g.engine).get_foreign_keys("rb_model_pins")
    expected={"production_quant_model_releases","quant_model_states","analysis_runs"}
    assert expected <= {row["referred_table"] for row in foreign_keys}
    with g.sessions() as session:
        for table,key,value in (("production_quant_model_releases","release_id",g.candidate["release_id"]),
            ("quant_model_states","quant_model_state_id",g.candidate["model_state_id"]),
            ("analysis_runs","analysis_run_id",g.candidate["source_analysis_id"])):
            assert session.scalar(text(f'SELECT count(*) FROM "{table}" WHERE "{key}"=:value'),{"value":value})==0
    # Missing concrete OFP provenance storage is the FK blocker. Do not fake a
    # legacy row or hand-insert a REAL_MODEL_PIN to make this assertion pass.
    links=[name for name in inspect(g.engine).get_table_names() if name!="rb_model_pins" and
        {"rb_model_pins","ofp_artifacts"} <= {fk["referred_table"] for fk in inspect(g.engine).get_foreign_keys(name)}]
    assert links,"G3: no constrained OFP provenance relation; all three legacy FKs point at absent OFP IDs"
