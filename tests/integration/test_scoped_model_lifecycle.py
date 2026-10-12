"""Two independently qualified persisted models, real pin API, no test_state shortcut."""
import json
import socket

import pytest
from sqlalchemy import text

from tests.integration.scoped_model_fixtures import graph, source, through_model, create_program_and_pin


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_MODEL_E2E_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_two_competitions_persist_qualified_model_state_release_and_real_type_pin(tmp_path):
    g = graph(tmp_path)
    try:
        a = through_model(g, source(g), kickoff_hours=4)
        b = through_model(g, source(g, "B", 5, "Europe/London"), kickoff_hours=5)
        pa, pb = create_program_and_pin(g, a), create_program_and_pin(g, b)
        assert pa.state is pb.state is None
        assert pa.model_source.source_type == pb.model_source.source_type == "OPENFOOTBALL_QUALIFIED_V2"
        assert pa.model_source.scope_id != pb.model_source.scope_id and pa.program != pb.program
        assert pa.model_lineage.state_hash != pb.model_lineage.state_hash
        assert pa.model_lineage.training_data_hash != pb.model_lineage.training_data_hash
        assert pa.configuration == pb.configuration and a.kickoff != b.kickoff
        assert all(row["prediction"]["status"] == "AVAILABLE" for s in (a, b) for row in s.binding.payload.evaluations)
        for s in (a, b):
            assert g.bridge.execute("model-pin", s.pin_request) == s.pin
            assert g.bridge.load(s.pin.artifact_id) == s.pin
            assert g.bridge.audit(s.pin.artifact_id)["status"] == "AUDIT_PASS"
        with g.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM ofpv2_real_model_pins")) == 2
            assert session.scalar(text("SELECT count(*) FROM ofp_real_model_pins")) == 0
            assert session.scalar(text("SELECT count(*) FROM match_results")) == 0
            assert session.scalar(text("SELECT count(*) FROM quant_model_states")) == 0
            assert not session.execute(text("PRAGMA foreign_key_check")).all()
        proof = dict(status="PASS_TWO_PERSISTED_QUALIFIED_SYNTHETIC_MODELS", real_production_pins=0,
            models=[dict(scope=s.qualification.owner.model_dump(), qualification_id=s.qualification.artifact_id,
                instance_id=s.instance.instance_id, manifest_id=s.manifest.artifact_id, approval_id=s.approval.artifact_id,
                release_id=s.release.artifact_id, binding_id=s.binding.artifact_id, program_id=s.program.artifact_id,
                pin_id=s.pin.artifact_id, state_hash=s.release.payload.state.state_hash,
                training_data_hash=s.release.payload.state.training_data_hash, facts_root=s.qualification.payload.facts_root,
                target_match_ids=list(s.pin.scope_match_ids), kickoff=s.kickoff.isoformat()) for s in (a, b)])
        (tmp_path / "two-scoped-model-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    finally:
        g.engine.dispose()
