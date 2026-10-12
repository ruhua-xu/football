# PF-B persisted Portfolio B — development review

Base: `4f4ec037a8173008cc07066f3ea02d036a4364f1`, the ordinary merge of the
approved PF-A contracts onto P1-B `27808d8e4228f197e4fa9f28cb651567895795bb`.
Development version: `1.4.0.dev0`; additive migration: `a0c9def6b732` after
`9fb8cde5a621`; explicit wheel resources: **90**.

## Persisted API and trusted inputs

`SqlAlchemyPortfolioRepository(bridge, budget_evidence=...)` uses the actual
`SqlAlchemyRealBridgeRepository`, its session factory and trusted clock. Budget
evidence is a private `LocalTrainingEvidence` directory with independently pinned
reviewer authorities. Commands are the closed types in `portfolio_requests.py`:

1. The real bridge `review-import` command imports the complete V4 review or
   records explicit V4 abstention, retaining the original run/packet/audit/fusion.
2. `snapshot` seals all available probability frames from one original bucket.
3. `prepare_budget_review` produces the exact allocation/census/policy subject.
   A human-approved review and independently trusted authority are needed for
   `budget-record`; preparation itself grants no budget.
4. `ticket-requests` and `assembly` build a multi-source typed numerical graph.
5. `lock` atomically claims all member heads, all prediction slots/version0, and
   the full ring-fenced allocation. Snapshot and assembly claim nothing.
6. `results` and `settle` record legal source observations and one append-only
   settlement chain. `load` verifies typed seals; `audit` replays the portfolio.

The original nine PF-A wire schemas are promoted to `domain/portfolio.py`.
`fankui/contracts/portfolio_b_schema.py` remains an import compatibility facade.
Additional closed receipt, capital declaration, approval, revalidation, result-set
and request-bundle types close the actual persistence graph.

## Money and numerical identity

A capital declaration binds the exact funding scope, allocation, limit, CNY-fen
currency and provenance, and explicitly excludes capital still authorized for
legacy use. Its bytes are pinned by the exact approved subject. A model authority
cannot approve this separate money scope. The database binds grants to approval
fields and enforces unique funding scope, allocation and capital-document hash.

The initial implementation conservatively grants **one allocation per funding
scope for its entire ledger history**, stricter than one per cycle. Changing a
cycle, grant, request or filename cannot remint the same approved capital. There
is no automatic recycling into another grant. A NO_BET lock still claims the
allocation and all member predictions once.

Member budgets/cash are never summed. `PortfolioNumericalConfigurationV1` retains
every original configuration, permits only the non-numerical result-source
identity to differ, and requires all actual numerical rules to match. The
portfolio budget must be permitted by those common rules. Strategy, pass expansion,
Return optimization and settlement call the unchanged existing kernels through
explicit Portfolio input types; no single-source run or DecisionLock is forged.

Settlement versions carry one account's financial projection. The effective view
uses the current result version rather than summing historical settlements:
`ending = cash + payout`, `P&L = payout - stake`. Missing or unsupported results
retain null payout/ending/P&L. Previously sealed versions remain replayable.

## Database ownership, backfill and atomicity

`pf_shared_heads`, `pf_shared_slots`, `pf_shared_versions` are the common authority
for legacy and Portfolio. The actual three legacy tables retain their old DDL,
rows and guards, with additive BEFORE-ABORT exclusion and AFTER-mirror triggers.
Explicit guards on both sides defeat outer `INSERT OR IGNORE` and `OR REPLACE`.
Legacy version chains remain in their original owner family; PF version0 is final.

The migration takes `BEGIN IMMEDIATE`, rejects partial preexisting PF schema and
malformed/unsealed old history, checks original FK/seals, backfills **every** old
head/slot/version, and verifies both mirror directions. The sealed migration proof
contains all old real-ledger row counts, original DDL/row hashes and claim hashes.
DDL, guards, complete backfill, proof and migration head publish in one transaction.

Typed composite FKs bind exact artifact ID/schema/hash. Every artifact and every
PF ownership/budget claim depends on a final seal, with deferred COMMIT checks.
Sealing requires the full member/frame/edge set, all claims, exact assembly money,
receipt time and concrete `PORTFOLIO_LOCK_REVALIDATION_V1` parent. Its per-member
checks replay the historical admission set; forged hashes and balanced but altered
stake/cash cannot become a committed lock.

`BEGIN IMMEDIATE` lock transactions revalidate current model qualification,
authority/rights, source admissions, fixture status, known results, market/SP age,
and complete member availability, then recheck at actual publication. Publication
must finish in 30 seconds and satisfy the strict `time + 60s < earliest kickoff`
for **all** source frames, including unselected early matches. Any failure rolls
back the full lock; an independent rejection audit cannot reserve money or slots.

An empty PF business graph can downgrade after verified legacy mirror checks;
any PF artifact, authority, rejection or claim refuses destructive downgrade.

## Audit and compatibility

`PORTFOLIO_OWNERSHIP_AUDIT_V1` includes the trusted audit time, receipt watermarks,
complete source census, `CONSUMED_BY_PORTFOLIO` ownership, prediction-slot count,
and current model/source-authority status. Post-lock invalidation remains visible
without deleting the historical financial lock or permitting another bet.
Synthetic audits report zero real prospective samples. Legacy reports that cannot
express newly visible PF ownership refuse with `PORTFOLIO_AWARE_REPORT_REQUIRED`;
their earlier historical watermarks and sealed JSON stay intact.

`tests/contract/pfb_projection.json` enumerates exact reverse hunks for eleven
existing integration/packaging files, applied before the unchanged P1-B manifest.
All other existing source, configuration, migration and operator bytes compare to
the exact merge base. All six frozen algorithm identities and Elo configuration
remain checked. Newly added files are explicitly enumerated, not wildcard-excluded.

## Reproducible acceptance matrix

| Gate | Actual proof |
|---|---|
| Models and passes | `test_portfolio_lifecycle.py`: two independently qualified scoped models, releases, states, real model-pin APIs, programs and different buckets; four future targets; 2X1/3X4/4X11 |
| Old database/binary | `test_portfolio_backfill.py`: archived v1.3.1 code creates a populated database; old rows/DDL/JSON survive; the actual old CLI refuses a0; empty downgrade/re-upgrade |
| Migration rollback | Partial backfill fails after real mirrored heads exist; malformed old seal and partial schema refuse; no new DDL or claims survive |
| Ownership | `test_portfolio_guards.py`: real distinct heads with the same slots, both winners, all three SQL conflict policies, recursive triggers OFF/ON |
| Races/crash | `test_portfolio_processes.py`: independent processes race LOCK/REPLACE against PF; exactly one winner; process death after seals and before COMMIT leaves zero claims |
| Seal and budget | Missing head/slot/version/budget/final seal, forged money/revalidation, duplicate funding cycle, exact approval mismatch, expiry and NO_BET |
| Census/time | Duplicate run and cross-program physical match; known pre-kickoff result, stale prices, lead equality and unselected early bucket |
| Results | Partial/complete/VOID/revised results; latest financial view and single allocation claim; post-lock invalidation audit |
| Installed wheel | `scripts/portfolio_acceptance.py`: isolated wheel imports, private self-authored inputs, denied network, actual two-model qualification and all three Portfolio passes |

All examples are self-authored synthetic software acceptance. Exact final commit,
wheel digest, Windows full-suite result and CI result belong in the external PF-B
review evidence. Formal production runtime migration and real source qualification
remain separate approval steps.
