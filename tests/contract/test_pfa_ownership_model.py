"""Executable SQL exclusion proof model, not PF-B/real-ledger implementation acceptance.

Legacy table column names/keys match the released ownership seam. Other legacy
business checks are deliberately absent: even this weaker old writer cannot win
a duplicate claim. Full actual-ledger/FK/seal integration remains a PF-B gate.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from fankui.contracts.portfolio_b_schema import LockContentV1, LockRequestV1, SettlementContentV1, SCHEMAS

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "fankui/contracts/portfolio_b_ownership_model.sql"
LEGACY = """
CREATE TABLE rb_artifacts(artifact_id TEXT PRIMARY KEY);
CREATE TABLE rb_runs(artifact_id TEXT PRIMARY KEY);
CREATE TABLE rb_locks(artifact_id TEXT PRIMARY KEY);
CREATE TABLE rb_programs(artifact_id TEXT PRIMARY KEY);
CREATE TABLE matches(internal_match_id TEXT PRIMARY KEY);
CREATE TABLE rb_market_keys(market_hash TEXT PRIMARY KEY);
CREATE TABLE rb_head_consumptions(parent_run_id TEXT PRIMARY KEY,action TEXT NOT NULL,new_run_id TEXT,lock_id TEXT,event_id TEXT NOT NULL);
CREATE TABLE rb_prediction_slots(program_id TEXT,match_id TEXT,market_hash TEXT,initial_lock_id TEXT,PRIMARY KEY(program_id,match_id,market_hash));
CREATE TABLE rb_prediction_versions(program_id TEXT,match_id TEXT,market_hash TEXT,version INTEGER,previous_version INTEGER,lock_id TEXT,PRIMARY KEY(program_id,match_id,market_hash,version));
INSERT INTO rb_artifacts VALUES('old-event');
INSERT INTO rb_runs VALUES('head'),('other'),('new-head');
INSERT INTO rb_locks VALUES('old-lock'),('old-revision');
INSERT INTO rb_programs VALUES('program');
INSERT INTO matches VALUES('match'),('other-match');
INSERT INTO rb_market_keys VALUES('THREE_WAY');
"""


def connect(path, recursive=0):
    conn = sqlite3.connect(path, timeout=3, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA recursive_triggers={recursive}")
    return conn


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "ownership-model.sqlite"
    with closing(connect(path)) as conn:
        conn.executescript(LEGACY)
        conn.executescript(SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO pf_budget_grants VALUES('grant','funding','cycle',10000)")
        conn.execute("INSERT INTO pf_budget_grants VALUES('grant2','funding','cycle2',10000)")
    return path


def legacy_sql(conn, *, head="head", match="match", action="LOCK", conflict=""):
    conn.execute(f"INSERT {conflict} INTO rb_head_consumptions VALUES(?,?,?,?,?)",
        (head, action, "new-head" if action == "REPLACE" else None, "old-lock" if action == "LOCK" else None, "old-event"))
    if action == "LOCK":
        conn.execute(f"INSERT {conflict} INTO rb_prediction_slots VALUES('program',?,'THREE_WAY','old-lock')", (match,))
        conn.execute(f"INSERT {conflict} INTO rb_prediction_versions VALUES('program',?,'THREE_WAY',0,NULL,'old-lock')", (match,))


def portfolio_sql(conn, *, head="head", match="match", lock="portfolio", grant="grant", seal=True, conflict=""):
    conn.execute("INSERT INTO pf_lock_roots VALUES(?)", (lock,))
    conn.execute(f"INSERT {conflict} INTO pf_shared_heads VALUES(?,'PORTFOLIO_LOCK',NULL,?)", (head, lock))
    conn.execute(f"INSERT {conflict} INTO pf_shared_slots VALUES('program',?,'THREE_WAY',NULL,?)", (match, lock))
    conn.execute("INSERT INTO pf_shared_versions VALUES('program',?,'THREE_WAY',0,NULL,NULL,?)", (match, lock))
    conn.execute("INSERT INTO pf_budget_claims VALUES(?,?)", (grant, lock))
    if seal:
        conn.execute("INSERT INTO pf_lock_seals VALUES(?)", (lock,))


def transaction(path, writer, *, barrier=None, recursive=0, **kwargs):
    with closing(connect(path, recursive)) as conn:
        if barrier is not None:
            barrier.wait(timeout=10)
        try:
            conn.execute("BEGIN IMMEDIATE")
            writer(conn, **kwargs)
            conn.commit()
            return "COMMITTED"
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            conn.rollback()
            return "REJECTED"


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("conflict", ["", "OR IGNORE", "OR REPLACE"])
@pytest.mark.parametrize("key", ["head", "slot"])
def test_portfolio_first_cannot_be_overwritten_by_old_sql(database, recursive, conflict, key):
    assert transaction(database, portfolio_sql) == "COMMITTED"
    assert transaction(database, legacy_sql, head="head" if key == "head" else "other", conflict=conflict, recursive=recursive) == "REJECTED"
    with closing(connect(database)) as conn:
        assert conn.execute("SELECT count(*) FROM rb_head_consumptions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM pf_shared_heads").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM pf_shared_slots").fetchone()[0] == 1


@pytest.mark.parametrize("conflict", ["", "OR IGNORE", "OR REPLACE"])
@pytest.mark.parametrize("key", ["head", "slot"])
def test_legacy_first_cannot_be_overwritten_by_new_sql(database, conflict, key):
    assert transaction(database, legacy_sql) == "COMMITTED"
    assert transaction(database, portfolio_sql, head="head" if key == "head" else "other", conflict=conflict) == "REJECTED"
    with closing(connect(database)) as conn:
        assert conn.execute("SELECT count(*) FROM pf_lock_roots").fetchone()[0] == 0
        assert conn.execute("SELECT owner_kind FROM pf_shared_heads").fetchone()[0] == "LEGACY_LOCK"


@pytest.mark.parametrize("collision", ["head", "slot", "pre-lock-replacement"])
@pytest.mark.parametrize("recursive", [0, 1])
def test_old_new_race_has_exactly_one_committed_winner(database, collision, recursive):
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(transaction, database, legacy_sql, barrier=barrier, recursive=recursive,
            action="REPLACE" if collision == "pre-lock-replacement" else "LOCK")
        new = executor.submit(transaction, database, portfolio_sql, barrier=barrier, recursive=recursive,
            head="other" if collision == "slot" else "head")
        assert sorted((old.result(), new.result())) == ["COMMITTED", "REJECTED"]
    with closing(connect(database)) as conn:
        assert conn.execute("SELECT count(*) FROM pf_shared_heads").fetchone()[0] == 1
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_missing_final_seal_or_second_budget_claim_rolls_back_all_new_claims(database):
    assert transaction(database, portfolio_sql, seal=False) == "REJECTED"
    with closing(connect(database)) as conn:
        assert all(conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
            for table in ("pf_lock_roots", "pf_shared_heads", "pf_shared_slots", "pf_shared_versions", "pf_budget_claims"))
    assert transaction(database, portfolio_sql) == "COMMITTED"
    assert transaction(database, portfolio_sql, lock="second", head="other", match="other-match") == "REJECTED"
    with closing(connect(database)) as conn:
        assert conn.execute("SELECT count(*) FROM pf_shared_heads").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM pf_budget_claims").fetchone()[0] == 1


def test_legacy_version_continues_only_legacy_owned_slot(database):
    assert transaction(database, legacy_sql) == "COMMITTED"
    with closing(connect(database)) as conn:
        conn.execute("INSERT INTO rb_prediction_versions VALUES('program','match','THREE_WAY',1,0,'old-revision')")
        assert conn.execute("SELECT version FROM pf_shared_versions ORDER BY version").fetchall() == [(0,), (1,)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT OR REPLACE INTO pf_shared_versions VALUES('program','match','THREE_WAY',0,NULL,'old-lock',NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM pf_shared_slots")


def test_historical_backfill_is_bijective_and_does_not_modify_legacy_rows(tmp_path):
    path = tmp_path / "backfill.sqlite"
    with closing(connect(path)) as conn:
        conn.executescript(LEGACY)
        legacy_sql(conn)
        tables = ("rb_head_consumptions", "rb_prediction_slots", "rb_prediction_versions")
        before = {t: conn.execute("SELECT * FROM " + t).fetchall() for t in tables}
        conn.executescript(SQL.read_text(encoding="utf-8"))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO pf_shared_heads SELECT parent_run_id,CASE action WHEN 'LOCK' THEN 'LEGACY_LOCK' ELSE 'LEGACY_REPLACE' END,event_id,NULL FROM rb_head_consumptions")
        conn.execute("INSERT INTO pf_shared_slots SELECT program_id,match_id,market_hash,initial_lock_id,NULL FROM rb_prediction_slots")
        conn.execute("INSERT INTO pf_shared_versions SELECT program_id,match_id,market_hash,version,previous_version,lock_id,NULL FROM rb_prediction_versions ORDER BY version")
        conn.commit()
        assert before == {t: conn.execute("SELECT * FROM " + t).fetchall() for t in tables}
        assert conn.execute("SELECT count(*) FROM pf_shared_versions").fetchone()[0] == 1
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert transaction(path, portfolio_sql) == "REJECTED"


def ref(kind):
    return dict(artifact_id="synthetic:" + kind, schema_version=kind, content_hash="a" * 64)


def test_schema_forbids_caller_time_and_probability_override_and_exact_lead():
    for cls in SCHEMAS:
        assert cls.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValueError):
        LockRequestV1(request_key="x", assembly_id="a", locked_at_utc="2000-01-01T00:00:00Z", p_final=[1, 0, 0])
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    value = dict(assembly=ref("PORTFOLIO_ASSEMBLY_V1"), budget_authority=ref("PORTFOLIO_BUDGET_AUTHORITY_V1"),
        member_set_hash="a" * 64, ownership_manifest_hash="b" * 64, revalidation_hash="c" * 64,
        common_cutoff=now, locked_at_utc=now, earliest_member_kickoff=now + timedelta(seconds=60),
        receipt=ref("PORTFOLIO_RECEIPT_V1"), budget_fen=10000, stake_fen=200, cash_fen=9800)
    with pytest.raises(ValueError, match="PF_STRICT_EARLIEST_KICKOFF_LEAD_REQUIRED"):
        LockContentV1(**value)
    assert LockContentV1(**dict(value, earliest_member_kickoff=now + timedelta(seconds=61))).stake_fen == 200


def test_settlement_counts_cash_and_stake_only_once_and_unavailable_is_null():
    value = dict(portfolio_lock=ref("ATOMIC_PORTFOLIO_LOCK_V1"), previous=None, observations=(),
        settled_at_utc=datetime(2030, 1, 2, tzinfo=timezone.utc), result_set_hash="d" * 64,
        status="SETTLED", missing_match_ids=(), budget_fen=10000, stake_fen=200, cash_fen=9800,
        gross_payout_fen=500, ending_capital_fen=10300, profit_loss_fen=300)
    assert SettlementContentV1(**value).profit_loss_fen == 300
    with pytest.raises(ValueError):
        SettlementContentV1(**dict(value, ending_capital_fen=10500))
    with pytest.raises(ValueError):
        SettlementContentV1(**dict(value, status="MISSING_RESULT", missing_match_ids=("m",)))
