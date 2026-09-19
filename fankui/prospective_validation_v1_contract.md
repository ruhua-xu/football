# Prospective Validation V1 contracts — accepted software framework

Status: **Accepted**. Base: v0.9.0. ADR: ADR-0012 Accepted. Package version: **1.0.0**. Architecture Review approved candidate `da4e9c518492ebe24319b871deb1872ef91fb0c9`; technical contracts remain frozen. This is software completion, not real production activation or performance evidence.

## Scope and classification

This additive ledger wraps the frozen Elo/Poisson, market/base/V4/fusion, EV,
Strategy Pass V1/V2, Return Distribution V1, objective weights and settlement.
No provider or LLM HTTP adapter is activated. Sportmonks capability captures are
permanently closed; restricted/deleted raw is never a source for these commands.

The existing `MultiMarketAnalysisV1` wire declares
`SYNTHETIC_ACCEPTANCE_DATA`. This bridge preserves that fact. `REAL_PROSPECTIVE`
preparation therefore returns `PRODUCTION_DECISION_ADAPTER_UNAVAILABLE`; it does
not relabel the old graph. Legally reviewed manual real facts can be registered
independently. Real decision-source activation requires a compatible separately
reviewed production adapter. Software acceptance is not production activation.
No real observations have been acquired by this implementation.

## Time and event boundary

Source publication, availability, observation/capture, reviewer verification and
trusted local receipt are different fields. Only the repository clock supplies
receipt, prepare/cutoff, review import, lock, invalidation, settlement, report and
epoch-close timestamps. Public requests reject overrides. The local clock is
**not an external trusted-time notarization**. Clock rollback is rejected, SQL
system-time receipts must be within 30s of SQLite now, and an operation cannot
cross a 30s construction/commit window. Every receipt has an append-only sequence.

The safety gate is `lock + minimum_lock_lead_seconds < earliest kickoff` (default
60 seconds). All run matches, including NO_BET and unselected markets, participate.
Known results, unavailable fixture states and inconsistent kickoff evidence block
locking. Conflicting kickoff history is conservatively unavailable: this version
does not invent a revised canonical fixture identity or assume a cancelled match
has resumed. A new valid future source requires a new preparation.

Reports bind both `as_of_at_utc` and the complete receipt-sequence watermark. A
later event at the same UTC instant cannot alter a historical report. This is a
local append-only ledger, not proof of source publication in historical time.

## Evidence / manual verified import

`MANUAL_VERIFIED_IMPORT_V1` includes canonical match identity, source identity and
reference, contained relative source file, actual SHA-256, reviewer and time,
rights basis/reference, retention deadline, source timestamps, confidence,
classification, assertion class, category and a typed payload. The application
verifies bytes using the contained local evidence reader before persistence;
matching bytes is provenance, not an automated truth or license attestation.
Raw file bytes are not copied into the ledger. Structured assertions and hashes
are retained. An already committed exact retry returns its sealed artifact without
opening an expired or deleted source file.

Categories: LINEUP, EXPECTED_LINEUP, INJURY, SUSPENSION, SCHEDULE, REST, FORM,
MOTIVATION, ODDS_CONTEXT, OTHER_VERIFIED_FACT. FACT / ANALYSIS / SPECULATION remain
explicit. Ownership, sponsorship, region or loans are not evidence of collusion.

* Lineups: UNKNOWN, EXPECTED, CONFIRMED. Confirmation requires exactly eleven
  distinct team-bound starters and a confirmation reference. UNKNOWN has no roster.
* Absences: UNKNOWN, RECORDS_AVAILABLE, NONE_REPORTED. Empty provider lists are
  not NONE_REPORTED; the latter requires an explicit reference. Injury and bans
  are separate, with player/team, status, reason, time interval, source/availability
  and confidence. KEY_INJURY/SUSPENSION sidecars require fresh factual records,
  known source/availability/start, active status and an interval covering kickoff.
* Freshness: FRESH / STALE / UNKNOWN uses source publication and the frozen
  category limit, never just ingestion. Stale decision evidence is rejected;
  unknown publication remains UNKNOWN and cannot justify freshness-dependent
  correction categories. Limits are versioned in `ProspectivePolicyV1` and the
  shipped config; the CLI verifies that fixed resource against the implementation.
* Schedule/rest: explicit window, known-at, team, competition scope, fixture
  states and optional reschedule predecessor. Rest is full 24h kickoff days from
  the declared available scope, not invented all-competition coverage.
* Form: explicit window/location and typed MatchResult references; target/future,
  not-yet-received and visibly superseded results are rejected.

`EvidenceSnapshotV1` seals the claim and receipt/freshness/payload hash.
`ProspectiveEvidenceBindingV1` deterministically projects it to unchanged
`FootballEvidenceV1`. Large structured payloads get an explicit snapshot reference
in V4 and the full payload in the Markdown companion, rather than silent truncation.
New evidence must be attached when constructing the sealed analysis; `prepare`
does not mutate or retrofit an existing analysis. The legacy THREE_WAY bridge
requires its original cutoff, so newly received dynamic evidence requires a new
valid source analysis (other supported units may use a newly sealed consensus).

## Epoch, prepare, review, lock

`ValidationEpochV1` must be declared before its start and pins implementation and
frozen-code hashes, model/version/config/training data, base policies, fusion,
strategy profile, risk/rules, EV thresholds, budget set, return policy and objective.
Model state may evolve only within those already pinned configuration/history
identities; this release provides no re-fitting workflow. Close is a new
`ValidationEpochCloseV1` artifact. A linked next epoch requires a closed predecessor
and a future non-overlapping window. No automatic tuning or reuse as validation
of a subsequently tuned configuration occurs.

`ProspectiveRunV1` seals the full input graph, UTC slate identity/date, snapshots,
budget and cutoff. Its immutable status is PREPARING or UNAVAILABLE. Current
LOCKED, SETTLED, INVALIDATED_PRE_KICKOFF, UNAVAILABLE and STALE_SETTLEMENT are
derived event projections, not updates to the original run row.

`V4CorrectionAuditV1` is a **separate sidecar**, not a V4 schema change. It covers
every unit in exact packet order, binds raw review hash and lists canonical
categories plus snapshot references. Categories: CONFIRMED_LINEUP_CHANGE,
KEY_INJURY, SUSPENSION, ROTATION, REST_ADVANTAGE, SCHEDULE_CONGESTION,
MOTIVATION_EVIDENCE, ODDS_CONTEXT, DATA_QUALITY_DOWNGRADE, OTHER. Unknown/expected
lineups cannot justify confirmation, and unknown absences cannot justify active
injury/bans. Optional missing review follows unchanged V4 abstention/model-unavailable
rules and records an explicit audit for every unit.

`DecisionLockV1` seals all available/unavailable P_market/P_quant/P_base/P_llm/P_final
layers, SP snapshots, review/audit/fusion/plan/optimizer/evaluation, selected tickets,
multipliers/roles/stake/cash, implementation/configuration and dependency hashes.
Same epoch match+market cannot be counted twice under another slate key. A
replacement preparation references the old run, and its lock plus the old run's
`PreKickoffInvalidationV1` are committed atomically, before both deadlines. No
post-kickoff prediction supersession, branch or money/probability mutation exists.

## Result / settlement

`ManualResultImportV1` has source bytes/hash/provenance, trusted verification,
observation/publication/availability, status, regular-time semantics, optional
source versions and a local predecessor. `ResultObservationV1` is always appended.
Only FT with proven REGULATION_ONLY semantics creates a normalized MatchResult.
The normalized record uses local receipt for local availability; original source
times remain in the claim. Unknown provider revision capability is explicitly
SOURCE_REVISION_CHAIN_UNAVAILABLE. A manual declaration is not a fetched historical
provider version proof. Revisions must extend the current source/match head.

`ProspectiveSettlementV1` uses all run match heads from the epoch's fixed source,
the locked allocation, frozen market settlement and frozen return payout evaluator.
Missing/unsupported/cancelled/void/unproven results produce null money metrics,
not zero payouts or assumed losses. New revisions append new settlements referencing
the previous one. A current report flags an old settlement STALE_SETTLEMENT until
re-settled, while old sealed reports and settlements still replay exactly.

## Full-census descriptive validation

`ProspectiveValidationReportV1` has no caller-supplied case IDs or outcome filter.
All visible epoch runs, invalidations, locks, missing/unsupported/stale results and
settlements appear in its sealed census. Probability observations come from every
settled active locked unit, not just selected tickets or winning corrections.

* Quality grouped by competition and canonical market key, never mixed catalogs.
  Multiclass Brier is the **sum** of squared outcome errors; log-loss is `-ln(p_true)`.
  A zero true probability is explicit infinite log loss (null aggregate and an
  infinite counter), not epsilon smoothing. Arithmetic uses Decimal128.
* Ten [0,.1), …, [.9,1] one-vs-rest bins, per-outcome prediction/observed frequencies,
  ECE and probability buckets. FAVORITE/UNDERDOG use P_base else P_market among
  the two team outcomes; DRAW is fixed. Other markets use NOT_APPLICABLE.
* Layer deltas use the paired observation intersection and RIGHT_MINUS_LEFT.
  P_base→P_final includes every improved/worsened/neutral/abstained/unavailable
  case with category counts; the direction is Brier, with log-loss separately shown.
* Decision/value: eligible and positive-EV selections, NO_BET, tickets, unique
  selected quoted SP, locked/settled stake, expected and realized gross/ending/profit,
  net P&L and yield on actual settled stake (null for zero stake).
* Portfolio calibration compares frozen predicted probabilities with realized
  gross>0, break-even, 2x, 3x, loss, deep-loss frequencies and event Brier scores.

Synthetic diagnostics cannot satisfy the default 30-real-observation threshold.
Every empty/insufficient real cohort remains INSUFFICIENT_PROSPECTIVE_SAMPLE.
Small-sample gains/losses, descriptive coverage and software tests do not establish
model validity, ROI, alpha or permission to tune on the same validation period.

## Persistence, bounds and operational recovery

Migration `5b748fa162ed` adds only `pv_*` tables, typed source/child FKs, deferred
completeness seals and append-only triggers. Old tables/triggers/rows remain
unchanged. Request key plus canonical request content gives exact retries;
reuse with different bytes/meaning fails. External review hash is part of the
workflow retry identity. Decision writes use BEGIN IMMEDIATE and a final time check.
Preparation/review/source artifacts may remain if a later workflow step fails;
they never become an implicit lock. Retry with the same inputs is the recovery path.

Artifacts max64MiB; local evidence/request/review max4MiB; run evidence128,
epoch runs256, report units4096. Limits reject rather than select profitable cases.
Loads revalidate hashes, typed projections, request/event time/sequence and complete
source/math/census replay. SQLite is the supported ledger backend. Privileged
offline database modification is detected on replay, not cryptographically prevented.
Populated downgrade is refused; only an empty additive ledger can be removed.

## CLI workflow

All commands are under `football-system prospective`. Mutation commands use
`--database-url sqlite:///... --input request.json [--output artifact.json]`.

1. `epoch`: EpochRequestV1 with an existing sealed seed plan, name/mode/window/result source.
2. `evidence-import --evidence-root <directory>`: EvidenceImportRequestV1. Build a new
   analysis incorporating the returned football evidence IDs using the existing market API.
3. `prepare --packet-dir <directory>`: PrepareProspectiveRequestV1. Writes exact
   `analysis_packet.json` and an evidence-aware `analysis_packet.md` when available.
4. `lock [--review llm_review.json --reasons correction_reasons.json]`:
   LockWorkflowRequestV1 (`request_key`, `run_id`, optional strategy_requests and
   pre-kickoff invalidation_reason). Reasons file is an array of CorrectionReasonV1
   in packet order. The optional review is produced externally, never by an HTTP call here.
5. `result-import --evidence-root <directory>`: ResultImportRequestV1.
6. `settle`: SettleProspectiveRequestV1; `report`: ReportProspectiveRequestV1 with
   epoch ID and an observed `as_of_at_utc`; `epoch-close`: CloseEpochRequestV1.
7. `show --artifact-id ...` and `audit --artifact-id ...` are read-only and do not
   migrate a database. `policy` prints the fixed sealed policy;
   `capability --provider ... --category ...` reports offline capability status.

Event times, probability/price/objective overrides and binary-float JSON numbers
are not accepted. Output paths cannot alias the database, inputs or manual source.
The CLI has no synthetic-clock flag; only isolated tests/scaffolds inject that clock.
