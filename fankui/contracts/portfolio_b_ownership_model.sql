-- PF-A executable arbitration model, NOT a production migration.
-- Existing rb_* tables/guards remain. Complete artifact/clock/authority/seal
-- projections are specified in the contract and must be integrated in PF-B.
CREATE TABLE pf_lock_roots(lock_id TEXT PRIMARY KEY NOT NULL);
CREATE TABLE pf_lock_seals(lock_id TEXT PRIMARY KEY NOT NULL REFERENCES pf_lock_roots(lock_id));
CREATE TABLE pf_shared_heads(
    parent_run_id TEXT PRIMARY KEY NOT NULL REFERENCES rb_runs(artifact_id),
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('LEGACY_LOCK','LEGACY_REPLACE','PORTFOLIO_LOCK')),
    legacy_event_id TEXT REFERENCES rb_artifacts(artifact_id),
    portfolio_lock_id TEXT REFERENCES pf_lock_seals(lock_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK((owner_kind IN ('LEGACY_LOCK','LEGACY_REPLACE') AND legacy_event_id IS NOT NULL AND portfolio_lock_id IS NULL)
       OR (owner_kind='PORTFOLIO_LOCK' AND legacy_event_id IS NULL AND portfolio_lock_id IS NOT NULL))
);
CREATE TABLE pf_shared_slots(
    program_id TEXT NOT NULL REFERENCES rb_programs(artifact_id),
    match_id TEXT NOT NULL REFERENCES matches(internal_match_id),
    market_hash TEXT NOT NULL REFERENCES rb_market_keys(market_hash),
    initial_legacy_lock_id TEXT REFERENCES rb_locks(artifact_id),
    initial_portfolio_lock_id TEXT REFERENCES pf_lock_seals(lock_id) DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY(program_id,match_id,market_hash),
    CHECK((initial_legacy_lock_id IS NOT NULL AND initial_portfolio_lock_id IS NULL)
       OR (initial_legacy_lock_id IS NULL AND initial_portfolio_lock_id IS NOT NULL))
);
CREATE TABLE pf_shared_versions(
    program_id TEXT NOT NULL, match_id TEXT NOT NULL, market_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=0),
    previous_version INTEGER,
    legacy_lock_id TEXT REFERENCES rb_locks(artifact_id),
    portfolio_lock_id TEXT REFERENCES pf_lock_seals(lock_id) DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY(program_id,match_id,market_hash,version),
    UNIQUE(program_id,match_id,market_hash,previous_version),
    FOREIGN KEY(program_id,match_id,market_hash) REFERENCES pf_shared_slots(program_id,match_id,market_hash),
    FOREIGN KEY(program_id,match_id,market_hash,previous_version) REFERENCES pf_shared_versions(program_id,match_id,market_hash,version),
    CHECK((version=0 AND previous_version IS NULL) OR (version>0 AND previous_version=version-1)),
    CHECK((legacy_lock_id IS NOT NULL AND portfolio_lock_id IS NULL)
       OR (legacy_lock_id IS NULL AND portfolio_lock_id IS NOT NULL AND version=0))
);
CREATE TABLE pf_budget_grants(
    grant_id TEXT PRIMARY KEY NOT NULL, funding_scope_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL, budget_fen INTEGER NOT NULL CHECK(typeof(budget_fen)='integer' AND budget_fen>=0),
    UNIQUE(funding_scope_id,cycle_id)
);
CREATE TABLE pf_budget_claims(
    grant_id TEXT PRIMARY KEY NOT NULL REFERENCES pf_budget_grants(grant_id),
    lock_id TEXT NOT NULL UNIQUE REFERENCES pf_lock_seals(lock_id) DEFERRABLE INITIALLY DEFERRED
);

-- BEFORE RAISE is essential: an outer INSERT OR IGNORE/REPLACE must not
-- suppress an AFTER mirror's uniqueness error and silently split ownership.
CREATE TRIGGER pf_shared_head_no_reacquire BEFORE INSERT ON pf_shared_heads
WHEN EXISTS(SELECT 1 FROM pf_shared_heads WHERE parent_run_id=NEW.parent_run_id)
BEGIN SELECT RAISE(ABORT,'PF_HEAD_ALREADY_CLAIMED'); END;
CREATE TRIGGER pf_shared_slot_no_reacquire BEFORE INSERT ON pf_shared_slots
WHEN EXISTS(SELECT 1 FROM pf_shared_slots WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash)
BEGIN SELECT RAISE(ABORT,'PF_SLOT_ALREADY_CLAIMED'); END;
CREATE TRIGGER pf_shared_version_no_reacquire BEFORE INSERT ON pf_shared_versions
WHEN EXISTS(SELECT 1 FROM pf_shared_versions WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash AND version=NEW.version)
BEGIN SELECT RAISE(ABORT,'PF_VERSION_ALREADY_CLAIMED'); END;
CREATE TRIGGER pf_budget_no_reacquire BEFORE INSERT ON pf_budget_claims
WHEN EXISTS(SELECT 1 FROM pf_budget_claims WHERE grant_id=NEW.grant_id OR lock_id=NEW.lock_id)
BEGIN SELECT RAISE(ABORT,'PF_BUDGET_ALREADY_CLAIMED'); END;

CREATE TRIGGER pf_head_legacy_origin BEFORE INSERT ON pf_shared_heads
WHEN NEW.legacy_event_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM rb_head_consumptions h WHERE h.parent_run_id=NEW.parent_run_id AND h.event_id=NEW.legacy_event_id
    AND NEW.owner_kind=CASE h.action WHEN 'LOCK' THEN 'LEGACY_LOCK' WHEN 'REPLACE' THEN 'LEGACY_REPLACE' END)
BEGIN SELECT RAISE(ABORT,'PF_LEGACY_HEAD_ORIGIN_REQUIRED'); END;
CREATE TRIGGER pf_slot_legacy_origin BEFORE INSERT ON pf_shared_slots
WHEN NEW.initial_legacy_lock_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM rb_prediction_slots s WHERE s.program_id=NEW.program_id AND s.match_id=NEW.match_id
    AND s.market_hash=NEW.market_hash AND s.initial_lock_id=NEW.initial_legacy_lock_id)
BEGIN SELECT RAISE(ABORT,'PF_LEGACY_SLOT_ORIGIN_REQUIRED'); END;
CREATE TRIGGER pf_version_origin BEFORE INSERT ON pf_shared_versions
WHEN NOT EXISTS(SELECT 1 FROM pf_shared_slots s WHERE s.program_id=NEW.program_id AND s.match_id=NEW.match_id AND s.market_hash=NEW.market_hash
    AND ((NEW.legacy_lock_id IS NOT NULL AND s.initial_legacy_lock_id IS NOT NULL AND EXISTS(
        SELECT 1 FROM rb_prediction_versions v WHERE v.program_id=NEW.program_id AND v.match_id=NEW.match_id AND v.market_hash=NEW.market_hash
        AND v.version=NEW.version AND v.previous_version IS NEW.previous_version AND v.lock_id=NEW.legacy_lock_id))
    OR (NEW.portfolio_lock_id=s.initial_portfolio_lock_id AND NEW.version=0)))
BEGIN SELECT RAISE(ABORT,'PF_VERSION_OWNER_FAMILY_MISMATCH'); END;

CREATE TRIGGER pf_legacy_head_exclusion BEFORE INSERT ON rb_head_consumptions
WHEN EXISTS(SELECT 1 FROM pf_shared_heads WHERE parent_run_id=NEW.parent_run_id)
BEGIN SELECT RAISE(ABORT,'PF_HEAD_ALREADY_CLAIMED'); END;
CREATE TRIGGER pf_legacy_head_mirror AFTER INSERT ON rb_head_consumptions
BEGIN INSERT INTO pf_shared_heads(parent_run_id,owner_kind,legacy_event_id)
VALUES(NEW.parent_run_id,CASE NEW.action WHEN 'LOCK' THEN 'LEGACY_LOCK' ELSE 'LEGACY_REPLACE' END,NEW.event_id); END;
CREATE TRIGGER pf_legacy_slot_exclusion BEFORE INSERT ON rb_prediction_slots
WHEN EXISTS(SELECT 1 FROM pf_shared_slots WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash)
BEGIN SELECT RAISE(ABORT,'PF_SLOT_ALREADY_CLAIMED'); END;
CREATE TRIGGER pf_legacy_slot_mirror AFTER INSERT ON rb_prediction_slots
BEGIN INSERT INTO pf_shared_slots(program_id,match_id,market_hash,initial_legacy_lock_id)
VALUES(NEW.program_id,NEW.match_id,NEW.market_hash,NEW.initial_lock_id); END;
CREATE TRIGGER pf_legacy_version_exclusion BEFORE INSERT ON rb_prediction_versions
WHEN EXISTS(SELECT 1 FROM pf_shared_versions WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash AND version=NEW.version)
  OR NOT EXISTS(SELECT 1 FROM pf_shared_slots WHERE program_id=NEW.program_id AND match_id=NEW.match_id AND market_hash=NEW.market_hash AND initial_legacy_lock_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'PF_VERSION_OWNER_FAMILY_MISMATCH'); END;
CREATE TRIGGER pf_legacy_version_mirror AFTER INSERT ON rb_prediction_versions
BEGIN INSERT INTO pf_shared_versions(program_id,match_id,market_hash,version,previous_version,legacy_lock_id)
VALUES(NEW.program_id,NEW.match_id,NEW.market_hash,NEW.version,NEW.previous_version,NEW.lock_id); END;

-- Append-only registries cannot be freed by invalidation, UPDATE, DELETE or REPLACE.
CREATE TRIGGER pf_heads_no_update BEFORE UPDATE ON pf_shared_heads BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_heads_no_delete BEFORE DELETE ON pf_shared_heads BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_slots_no_update BEFORE UPDATE ON pf_shared_slots BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_slots_no_delete BEFORE DELETE ON pf_shared_slots BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_versions_no_update BEFORE UPDATE ON pf_shared_versions BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_versions_no_delete BEFORE DELETE ON pf_shared_versions BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_budget_no_update BEFORE UPDATE ON pf_budget_claims BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_budget_no_delete BEFORE DELETE ON pf_budget_claims BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_grants_no_update BEFORE UPDATE ON pf_budget_grants BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_grants_no_delete BEFORE DELETE ON pf_budget_grants BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_grants_no_replace BEFORE INSERT ON pf_budget_grants
WHEN EXISTS(SELECT 1 FROM pf_budget_grants WHERE grant_id=NEW.grant_id OR (funding_scope_id=NEW.funding_scope_id AND cycle_id=NEW.cycle_id))
BEGIN SELECT RAISE(ABORT,'PF_GRANT_ALREADY_EXISTS'); END;
CREATE TRIGGER pf_roots_no_update BEFORE UPDATE ON pf_lock_roots BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_roots_no_delete BEFORE DELETE ON pf_lock_roots BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_roots_no_replace BEFORE INSERT ON pf_lock_roots
WHEN EXISTS(SELECT 1 FROM pf_lock_roots WHERE lock_id=NEW.lock_id)
BEGIN SELECT RAISE(ABORT,'PF_LOCK_ALREADY_EXISTS'); END;
CREATE TRIGGER pf_seals_no_update BEFORE UPDATE ON pf_lock_seals BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_seals_no_delete BEFORE DELETE ON pf_lock_seals BEGIN SELECT RAISE(ABORT,'PF_APPEND_ONLY'); END;
CREATE TRIGGER pf_seals_no_replace BEFORE INSERT ON pf_lock_seals
WHEN EXISTS(SELECT 1 FROM pf_lock_seals WHERE lock_id=NEW.lock_id)
BEGIN SELECT RAISE(ABORT,'PF_SEAL_ALREADY_EXISTS'); END;
