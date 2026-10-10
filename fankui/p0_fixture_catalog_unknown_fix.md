# P0 Fixture Catalog UNKNOWN — independent candidate

Base: v1.3.0 / `0f677e834d5e2b6eb092ecaf86e4c70a60e38e98`.
Feature: `feature/p0-fixture-catalog-unknown`.

## Scope and minimum change

The OFP V1 writer already persists 13 reviewed exception matches as `UNKNOWN`.
The formal `SqlAlchemyMatchIdentityRepository.load_catalog` reconstructs `Match`
objects via `MatchStatus(status)` and previously failed on those existing rows.

The behavior change is exactly **`MatchStatus.UNKNOWN = "UNKNOWN"`**. The reader
query, row coverage, historical records, OFP writer, source admission rules,
fixture SQL schema, model/Strategy/Return/prospective mathematics and RealBridge
lifecycle are unchanged. No migration is needed: the value is already stored.

The frozen-source checker and its contract tests gain exact one-line P0
projections. They still compare all remaining bytes to released code; no
whole-file exclusion or weakening of mathematical identities is introduced.

## Red → green evidence

Before implementation changes, three independent vertical regression cases
failed with `ValueError: 'UNKNOWN' is not a valid MatchStatus`, with zero setup
errors. They cover the complete 612-row directory, provider-filtered metadata
without dropping matches, and the real manual-fixture provider/repository path.
The same three cases passed after the one-member enum extension.

Tests use self-authored, separately qualified/admitted synthetic 612/13/599
source graphs and normal persistence APIs. They do not fit models, use network,
modify production or inject an alternative catalog to hide the exceptions.

The source-backed fixture E2E has a legitimately registered source fixture and
canonical context. It reads the entire history plus that future fixture,
resolves the reviewed manual input to the same canonical IDs and appends its
observation through the formal ingestion service. Historical rows, artifact
bytes, facts root, mapping root and window hash remain identical.

## UNKNOWN does not grant availability

- UNKNOWN remains UNKNOWN, distinct from FINISHED and SCHEDULED.
- Unsupported arbitrary status strings remain rejected, rather than mapped to UNKNOWN.
- The unchanged `ck_fixture_observation_status` still refuses UNKNOWN as a
  recorded live observation; failed ingestion rolls back its entire graph.
- A historical UNKNOWN identity has no admitted result and cannot produce a
  READY live preparation or the required RealBridge fixture observation ref.
- No exception is converted to FT/0:0 or included in the 599 facts.

## Genuine snapshot check

A separately labelled, read-only diagnostic snapshot of the released DB is
used for local evidence only; no operator installation points to it. Its OFP
storage-binding metadata is not rewritten. Released code reproduces the failure;
candidate code reads all 599 FINISHED + 13 UNKNOWN anchors without changing the
snapshot bytes or the genuine frozen roots.

The existing source-backed real fixture document also validates and reaches
the provider constructor. Its missing current-season mappings/aliases remain
an operational input prerequisite: a preview that would create different
competition/team IDs is not persisted. P0 does not claim production target,
HAD, model approval or complete canonical mapping readiness.

## Acceptance and deployment boundary

Exact red/green/negative/full-suite, lint/compile, fresh migration/check,
candidate wheel/isolated-wheel, CI, snapshot and frozen-boundary receipts are
delivered under `upload/p0-fixture-catalog-fix-20261008/`. Only actual receipts
can declare a gate passed. Candidate package metadata remains the 1.3.0 source
base; the candidate wheel is not the published 1.3.0 artifact.

No main merge, release, tag move, production wheel install, runtime migration,
rebind, real approval/pin/program/epoch/run/DecisionLock is part of this P0 task.
P1 and Portfolio work remain plans pending user confirmation.
