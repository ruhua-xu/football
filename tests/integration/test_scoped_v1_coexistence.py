"""V2 facts do not widen the frozen Bundesliga V1 provider-wide universe."""
from types import SimpleNamespace

from sqlalchemy import text

from tests.integration import test_openfootball_production_binding as v1
from tests.integration.ofp_real_pin_fixtures import qualify_synthetic_source
from tests.integration.scoped_model_fixtures import source, qualify
from football_system.infrastructure.database.openfootball_scoped_repository import SqlAlchemyOpenFootballScopedRepository

source_graph = v1.graph


def test_two_v2_scopes_coexist_with_complete_frozen_v1_history(source_graph, monkeypatch):
    old = qualify_synthetic_source(source_graph, monkeypatch)
    binding = old.admit()
    with old.sessions() as session:
        before = session.execute(text("SELECT artifact_id,artifact_json FROM ofp_artifacts ORDER BY artifact_id")).all()
    g = SimpleNamespace(root=old.root, clock=old.clock, sessions=old.sessions, evidence=old.evidence, production=old.production,
        repo=SqlAlchemyOpenFootballScopedRepository(old.production))
    qa, qb = qualify(g, source(g)), qualify(g, source(g, "B", 5, "Europe/London"))
    assert old.repo.inspect("DATA_BINDING") == binding
    with old.sessions() as session:
        assert session.execute(text("SELECT artifact_id,artifact_json FROM ofp_artifacts ORDER BY artifact_id")).all() == before
        assert session.scalar(text("SELECT count(*) FROM ofp_source_records")) == 612
        assert session.scalar(text("SELECT count(*) FROM ofp_source_records WHERE included=0")) == 13
        assert session.scalar(text("SELECT count(*) FROM match_results")) == 599
        assert session.scalar(text("SELECT count(*) FROM ofp_canonical_entities")) == 24
        assert session.scalar(text("SELECT count(*) FROM ofpv2_records")) == 64
        assert not session.execute(text("PRAGMA foreign_key_check")).all()
    assert qa.owner != qb.owner and binding.payload["facts_root"] not in {qa.payload.facts_root, qb.payload.facts_root}
