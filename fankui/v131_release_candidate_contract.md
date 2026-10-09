# v1.3.1 P0 Patch Release Candidate

Approved implementation: `9429bcf695527d5c1d2663b2915faa388ab4a065`.
Released baseline: v1.3.0 / `0f677e834d5e2b6eb092ecaf86e4c70a60e38e98`.

This branch prepares one patch release. The approved UNKNOWN enum/read behavior,
612/13/599 universe, existing admission restrictions and frozen numerical code
are preserved. P1-A and Portfolio development are on separate branches.

## Release-only changes

- Package/session display and exact freeze version projection become 1.3.1.
- Operator software identity names the approved P0 commit; the existing
  OPENFOOTBALL_PIN_RELEASE_V1 execution profile and 8ea7bcd49510 head remain.
- Explicit maintenance accepts only the exact published v1.3.0 software identity
  and operator hash, in addition to the existing supported v1.1/v1.2 identities.
  A same-version unreleased P0 wheel is not a published v1.3.0 identity.
- Patch confirmation/journal/backup/temporary-file namespaces are versioned
  independently of the earlier v1.3.0 release history.
- No migration is added. An existing 8e database must be byte-for-byte unchanged
  by patch rebind, with original file identity/application_id and all old rows.
- Archived old-source fixtures reproduce the exact published newline profile:
  v1.2 uses CRLF; v1.3 has five LF lines across three otherwise CRLF files, each
  checked against its published-wheel file hash. No runtime identity is widened.
  The candidate itself
  receives actual installed-wheel verification, including a v1.3.0-to-1.3.1
  synthetic exercise and preservation of the previous release journal.

All release-only edits are reversed by exact counted substitutions in the
contract tests before comparing every baseline byte. No whole-file exclusion.

## Merge and publication boundary

Feature CI and full local/installed-wheel gates precede a reviewed merge. A
merge-tree rehearsal establishes the expected main tree without changing main.
Candidate wheel SHA, Git SHA/tree, normalized code/resource comparison, actual
raw package revision, dependency versions, migration head and all frozen hashes
are recorded together. The artifact is an RC even though its metadata is 1.3.1.
No tag or public release is created as part of RC preparation.

## Production upgrade plan — explicit authorization required

1. Recheck exact current v1.3.0 installed RECORD/wheel/package/operator identity,
   original installation ID, both DB file identities/application_ids/head and
   the current state of Bundesliga target preparation. Coordinate a maintenance
   window; do not discard in-progress inbox/operations to satisfy an idle check.
2. The existing maintenance helper supports only idle INPUT_PREPARATION and an
   idle real lifecycle. If Bundesliga work has advanced beyond that supported
   state, stop and review an appropriate upgrade path; do not reset its data.
3. Verify an immutable BEFORE dual-DB backup, original manifests and authorized
   evidence. Preserve all old backups and v1.3.0 journal entries.
4. Following separate authorization, install the exact accepted 1.3.1 wheel
   non-editably and without dependency drift; switch the operator checkout to
   the exact accepted release code. A temporary identity mismatch is fail-closed.
5. Run explicit patch rebind with `UPGRADE <exact runtime path> TO 1.3.1` and the
   verified BEFORE backup. Head is already 8e: no schema migration/data rewrite.
   Rebind updates installation manifests only after file/row/schema invariants.
6. Verify original DB bytes/identities, 612/13/599 and mapping/window/facts roots,
   no lifecycle/model writes, installed RECORD/resources, operator FILES_CHECKED,
   and an independently verified AFTER backup. Resume legitimate preparation.
7. A failure after intent retains the maintenance lock/journal. Inspect before
   recovery; never replace an active DB, silently delete locks, rewind model
   history, retag v1.3.0 or overwrite its wheel. Pre-intent binary rollback to the
   original software requires verifying unchanged original manifests and DBs;
   post-intent partial-manifest recovery requires explicit maintenance review.

RC tests and this plan authorize no production action, qualification, approval,
model pin, epoch, run, DecisionLock or bet.
