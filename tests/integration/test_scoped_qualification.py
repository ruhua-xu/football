"""P1-B real persistence/authority paths, not P1-A inert reference acceptance."""
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import text

from tests.integration.scoped_model_fixtures import graph, source, qualify


@pytest.fixture
def g(tmp_path):
    value = graph(tmp_path)
    yield value
    value.engine.dispose()


def test_two_scopes_have_complete_independent_persisted_census_and_trusted_admission_clocks(g):
    a, b = source(g), source(g, "B", 5, "Europe/London")
    qa, qb = qualify(g, a), qualify(g, b)
    assert qa.owner != qb.owner and qa.payload.facts_root != qb.payload.facts_root
    assert len(qa.payload.facts) == 23 and len(qb.payload.facts) == 39
    assert all(f.ingested_at_utc == f.available_at_utc == qa.recorded_at_utc for f in qa.payload.facts)
    assert all(f.ingested_at_utc > a.profile.files[0].captured_at_utc for f in qa.payload.facts)
    assert {f.match_result_id for f in qa.payload.facts}.isdisjoint(f.candidate_result.match_result_id for f in a.profile.records if f.candidate_result)
    assert g.repo.inspect_scope(qa.owner.scope_id) == qa and g.repo.inspect_scope(qb.owner.scope_id) == qb
    with sqlite3.connect(g.database.as_uri() + "?mode=ro", uri=True) as native:
        assert native.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert native.execute("PRAGMA foreign_key_check").fetchall() == []
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM ofpv2_records")) == 64
        assert session.scalar(text("SELECT count(*) FROM ofpv2_records WHERE disposition='EXCEPTION'")) == 2
        assert session.scalar(text("SELECT count(*) FROM match_results")) == 0
        assert session.scalar(text("SELECT count(*) FROM ofp_source_records")) == 0
        assert not session.execute(text("PRAGMA foreign_key_check")).all()


def test_wrong_scope_authority_is_rejected_before_any_persistence(g):
    a, b = source(g), source(g, "B")
    with pytest.raises(ValueError, match="scope|payload|authorized"):
        g.repo.record_scope(request_key="qualification", subject=b.subject, review=b.review, authority=a.authority)
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM ofpv2_scopes")) == 0


def test_exact_retry_and_source_tampering_fail_closed(g):
    a = source(g)
    qa = qualify(g, a)
    assert qualify(g, a) == qa
    path = g.root / a.prefix / a.profile.files[0].path
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        g.repo.inspect_scope(qa.owner.scope_id)


def test_scope_rows_cannot_be_replaced_and_populated_migration_refuses_downgrade(g):
    from alembic import command
    from alembic.config import Config
    qa = qualify(g, source(g))
    with sqlite3.connect(g.database) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM ofpv2_records")
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + g.database.as_posix())
    with pytest.raises(RuntimeError, match="populated scoped"):
        command.downgrade(cfg, "8ea7bcd49510")
    assert g.repo.inspect_scope(qa.owner.scope_id) == qa
