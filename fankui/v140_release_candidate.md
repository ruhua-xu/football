# v1.4.0 release candidate scope

Accepted implementation: `ed07fb4b1a2a936aaab8b62262b0475022c53df4` (IC2 repair
independently accepted). The candidate package is `1.4.0`, but is unpublished until
separate release authorization. Its own commit/tree/wheel hash and CI identify it.

## Frozen scope

Only exact release metadata changes are admitted over accepted IC2: package/version
assertions, the operator software profile and its accepted implementation anchor.
`devtools/rc_policy.py` reverses those exact edits and compares every runtime,
configuration, migration, fixture and existing script byte to accepted IC2. The
P1-B/PF-B numerical logic, qualification, ownership and IC2 transaction/CI repairs
are retained. Older published projections remain enforced.

## Migration and actual deployment origin

Actual production is1.3.0, not1.3.1. Both have schema head8ea7bcd49510; the actual
Alembic DAG must establish `8ea7bcd49510 → 9fb8cde5a621 → a0c9def6b732`.
Production-copy verification uses a private inactive copy without an operator
installation manifest. Only the copy is migrated or fault-injected. No real model
pin/program/epoch/run/DecisionLock/PortfolioLock is created by that verification.
Genuine archived1.3.0 synthetic version0/1 DecisionLock ledgers provide the nonempty
legacy regression that the idle production copy cannot supply.

Each Alembic revision owns its transaction. A9fb failure restores8e; an a0 failure
restores9fb. A multi-revision operation can therefore stop at a fully committed
intermediate checkpoint; this is not an all-or-nothing two-revision transaction.
Retry, empty downgrade/re-upgrade and populated refusal must be verified explicitly.

## Candidate verification and future rollout

FAST/INTEGRATION precede the new candidate's complete normal CI. Real feature-branch
workflow_dispatch runs with workers4 and0 are additional requested checks. A manual
default-branch run is deferred until a separately authorized default-branch update;
the actual main branch remains the published1.3.1 software source.

The review package must include wheel contents/RECORD/dependencies and actual
installed-wheel E2E, production-copy upgrade proof, synthetic multi-competition
Portfolio/legacy evidence, frozen roots, and the unchanged active1.3.0 boundary.
Production upgrade, main merge and tag/release creation require separate approval.

The rollout plan must start with verified current installation, maintenance lock,
immutable backup and exact wheel/checkout identity. On failure preserve journals
and stop. Do not automatically downgrade a populated graph, replace an active DB,
clear an incomplete operation, or treat a test copy as a second active installation.
