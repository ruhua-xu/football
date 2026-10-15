"""P1-B closed authority, actual persistence and pin failures; no production data."""
from datetime import timedelta
import socket

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.scoped_model_fixtures import graph, source, qualify, through_model, create_program_and_pin


@pytest.fixture
def g(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SCOPED_TEST_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    value = graph(tmp_path)
    yield value
    value.engine.dispose()


@pytest.mark.parametrize("fault", ["untrusted", "expired", "review_bytes", "raw_bytes", "source_prefix"])
def test_qualification_failure_leaves_no_scope_or_canonical_entities(g, fault):
    s = source(g)
    if fault == "untrusted":
        g.evidence.trusted_authorities.clear()
    elif fault == "expired":
        g.clock.value += timedelta(days=40)
    elif fault == "review_bytes":
        (g.root / s.review.evidence_reference).write_bytes(b"changed review")
    elif fault == "raw_bytes":
        path = g.root / s.prefix / s.profile.files[0].path
        path.write_bytes(path.read_bytes() + b" ")
    else:
        s.subject = s.subject.model_copy(update={"source_prefix": "../outside"})
    with pytest.raises(ValueError):
        qualify(g, s)
    with g.sessions() as session:
        for table in ("ofpv2_scopes", "ofpv2_artifacts", "ofpv2_records", "competitions", "teams", "matches"):
            assert session.scalar(text("SELECT count(*) FROM " + table)) == 0


def test_database_seal_requires_full_census_even_when_writer_omits_children(g, monkeypatch):
    s = source(g)
    original = g.repo._put
    def incomplete(session, **values):
        if values["payload"].kind == "QUALIFICATION":
            values["project"] = None
        return original(session, **values)
    monkeypatch.setattr(g.repo, "_put", incomplete)
    with pytest.raises(IntegrityError):
        qualify(g, s)
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM ofpv2_artifacts")) == 0
        assert session.scalar(text("SELECT count(*) FROM ofpv2_scopes")) == 0
        assert session.scalar(text("SELECT count(*) FROM matches")) == 0


@pytest.mark.parametrize("fault", ["release_hash", "binding_hash", "scope_hash", "instance_id", "source_identity", "target_subset", "authority_revoked", "approval_expired"])
def test_actual_pin_rejects_invalid_or_expired_scoped_graph(g, fault):
    s = through_model(g, source(g))
    create_program_and_pin(g, s)
    request = s.pin_request.model_dump(mode="python")
    request["request_key"] = "bad-pin"
    if fault in {"release_hash", "binding_hash", "scope_hash"}:
        request["model_source"][fault] = "0" * 64
    elif fault == "instance_id":
        request["model_source"][fault] = "unknown-instance"
    elif fault == "source_identity":
        request["source_identity"] = "another-source"
    elif fault == "target_subset":
        request["scope_match_ids"] = request["scope_match_ids"][:1]
    elif fault == "authority_revoked":
        g.evidence.trusted_authorities.pop(s.authority.evidence_reference)
    else:
        g.clock.value += timedelta(days=11)
    from football_system.application.real_bridge_requests import REQUEST_TYPES
    with pytest.raises((ValueError, KeyError)):
        g.bridge.execute("model-pin", REQUEST_TYPES["model-pin"](**request))
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_model_pins")) == 1
        assert session.scalar(text("SELECT count(*) FROM ofpv2_real_model_pins")) == 1


def test_database_pin_seal_rejects_missing_scoped_companion(g, monkeypatch):
    s = through_model(g, source(g))
    create_program_and_pin(g, s)
    original = g.bridge._store
    def broken(session, values, receipt, relations):
        return original(session, values, receipt, tuple(r for r in relations if r[0] != "ofpv2_real_model_pins"))
    monkeypatch.setattr(g.bridge, "_store", broken)
    from football_system.application.real_bridge_requests import REQUEST_TYPES
    request = s.pin_request.model_dump(mode="python") | {"request_key": "missing-companion"}
    with pytest.raises(ValueError):
        g.bridge.execute("model-pin", REQUEST_TYPES["model-pin"](**request))
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_model_pins")) == 1


def test_existing_pin_reads_current_authority_but_retains_original_pre_match_proof(g):
    s = through_model(g, source(g))
    create_program_and_pin(g, s)
    g.clock.value += timedelta(days=1)
    assert g.bridge.load(s.pin.artifact_id) == s.pin
    g.evidence.trusted_authorities.pop(s.authority.evidence_reference)
    with pytest.raises(ValueError):
        g.bridge.load(s.pin.artifact_id)


def test_p1a_inert_qualified_reference_is_not_a_persisted_qualification(g):
    from tests.unit.test_openfootball_scope import fixture
    inert, _, _ = fixture()
    with pytest.raises(ValueError):
        g.repo.record_scope(request_key="inert", subject=inert, review=inert.qualification, authority=inert.authority)
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM ofpv2_scopes")) == 0
