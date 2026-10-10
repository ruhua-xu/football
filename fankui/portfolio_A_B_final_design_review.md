# Portfolio A/B Final Design Review — proposed decision B

**Design only. Neither Portfolio path is implemented or deployed by P0.**

## 1. Decision and premises

Recommend **B: immutable prediction-source snapshots + a Portfolio Lock** as
the primary architecture for one cross-run spending decision. A remains a
legitimate alternative when separately locked, zero-budget component forecasts
are intentionally official decisions before portfolio assembly; it is not
silently adopted as the default.

The decision prioritizes: (1) no premature prediction/head claims for an assembly
that never locks; (2) one owner of the combined budget/tickets; (3) atomic
qualification of every member at the common portfolio decision boundary;
(4) preserving independent competition models and exact-kickoff runs.

B costs more compatibility work than A. It is acceptable only after a shared,
database-enforced head/slot arbitration contract is reviewed and verified. Two
independent ownership tables with no mutual exclusion are not an implementation
of B. If that proof is missing, Portfolio remains BLOCKED.

## 2. Side-by-side comparison

| Topic | A: zero-budget component DecisionLock + Portfolio Lock | B: immutable prediction snapshot + Portfolio Lock |
|---|---|---|
| Source readiness | Existing valid DecisionLockV2 per member; already passed original lock guards | Validated snapshot from an active PREPARING run; snapshot alone is not an official decision |
| Prediction slots | Component locks consume slots before Portfolio Lock | Snapshot consumes none; Portfolio Lock claims all member prediction slots atomically |
| Head consumption | Components already consume their run heads | Snapshot consumes none; Portfolio Lock consumes all current source heads atomically |
| Failed assembly | Official component predictions remain even if portfolio fails | No financial lock, slot or head claim from the failed portfolio |
| Odds refresh before final lock | Locked component needs existing legal post-lock revision; cascades into dependent portfolio | Existing PRE_LOCK replacement invalidates old snapshots; snapshot rebuilt from the new head |
| Independent budget | Requires explicit portfolio budget; component budget/stake must be zero in strict MVP | Portfolio is the only new spending owner; source snapshot has no stake or cash allocation |
| Duplicate bets | Must not reuse nonzero component tickets or sum component cash/budgets | Unique budget/decision-cycle claim plus match dedup; no component tickets to double-spend |
| Atomicity | Portfolio final transaction can be atomic, but component locks were earlier transactions | All new claims and portfolio financial lock are one transaction |
| Source invalidation | Revalidate all component locks/current source legality; invalidation cascades | Revalidate snapshot refs, source heads, pins and permissions; invalid snapshot never authorizes a lock |
| Settlement | Distinguish zero-stake component forecast statistics from portfolio financial settlement | One portfolio financial settlement; prediction accounting references original program/match/market owners |
| Legacy compatibility | Smallest bridge change; existing V2 slots/heads continue to own component decisions | Requires explicit shared arbitration and versioned reporting; old artifacts stay byte-identical |
| Migration | Mainly new portfolio graph/budget tables | Additive registries, immutable snapshot/portfolio graph, cross-path guards and verified legacy backfill |

Existing isolated acceptance has already demonstrated A's key behavior: a
zero-budget V2 lock stores complete probability frames and consumes its head
and prediction slots with stake=0. This is evidence for A's feasibility **and**
its early-claim semantics, not evidence that Portfolio Assembly already exists.

## 3. Proposed B state and source contract

```text
qualified model A → pin/program/run A (one exact kickoff)
qualified model B → pin/program/run B (another exact kickoff)
→ immutable source snapshots, each bound to its own current run
→ Portfolio Assembly / verified candidate plan
→ atomic Portfolio Lock
→ portfolio settlement and audit
```

No old runs are merged, relabelled or represented as a fabricated single run.
Each snapshot retains its source program/epoch/anchor/run/bucket identity.

Proposed `PREDICTION_SOURCE_SNAPSHOT_V1` includes closed refs/hash for the source
run/analysis/packet, the exact V4 review or explicit abstention and review audit,
frozen fusion output/P_final, model pin/source scope/state lineage, fixture,
market/SP ingestion snapshots, configuration/implementation identities,
eligibility census, original source cutoffs and trusted snapshot receipt.

Use unchanged V4 import/fusion and deterministic source verification. Caller
supplied probabilities, substituted odds, `model_construct` shortcuts or fake
source refs are rejected. A snapshot seals evidence; it grants no official
prediction slot, betting authority, budget or eligibility after invalidation.

Each competition uses its own genuinely qualified cohort, Elo state, approval,
release and pin. Matching config hashes do not make states interchangeable.

## 4. One shared head/slot authority — required for B

Additive, append-only arbitration registries must be authoritative for **both**
legacy DecisionLockV2 and Portfolio Lock paths:

- Head claim key: `source_run_id`. Owner kind/ref is closed and typed (legacy
  replace/lock or Portfolio Lock). Only one successful consuming event per head.
- Prediction claim key preserves the existing
  `(program_id, canonical_match_id, THREE_WAY_market_hash)` semantics; versions
  append and retain the exact typed lock owner.
- Portfolio member dedup is additionally global within the assembly on
  `(canonical_match_id, market_key)`, so different programs cannot turn the same
  physical prediction into independent legs/samples.
- Budget claim key: approved portfolio budget scope + logical decision cycle.
  Cash is not obtained by summing run budgets; stake+cash equals this one budget.

Existing rows are backfilled into new registries from validated original
events, without updating old rows/hashes. Additive database guards must force
legacy `rb_head_consumptions`, `rb_prediction_slots` and version inserts to
participate in the same authority. New Portfolio writes must participate too.
An old application writer cannot win a second claim just because it does not
know a new table. Backfill completeness and cross-path exclusion are startup
and migration acceptance requirements.

Reader/status/report changes must be explicit and versioned: old historical
artifacts replay under their original schema/watermark. A source run consumed
by Portfolio is represented by its new ownership event, not by rewriting the
immutable run or pretending the Portfolio Lock is DecisionLockV2. Where an old
report cannot represent new ownership, fail explicitly or use the reviewed
portfolio-aware report version, never return a misleading PREPARING census.

## 5. Constructive atomicity argument

The following is a proof obligation/pseudocode, not implemented code:

```text
BEGIN IMMEDIATE
  load exact snapshot/member set and approved budget
  require every source run is the current active PREPARING head
  require all source/pin/authority/fixture/price/eligibility proofs valid
  require no head/prediction/budget claim conflicts in shared registries
  verify/reconstruct frozen Strategy/Pass/Return candidate result
  obtain actual final publication clock; recheck source expiry and earliest kickoff
  insert every head + prediction + budget claim and Portfolio Lock/complete seal
COMMIT
```

With a unique shared claim key, two legacy/Portfolio competitors cannot both
commit ownership. With one transaction, failure on any member/budget/clock
check rolls back all new claims and financial artifacts. Snapshot creation
has no claim operation, so it cannot consume a slot merely by being exported.

This argument depends on actual FK/check/trigger and reader enforcement; the
synthetic race and crash tests in the test plan must prove the implementation.
It is not a substitute for those tests or permission to weaken legacy guards.

## 6. Common cutoff, prices and lead time

Every snapshot preserves its real source decision/capture/availability/ingestion
times. The assembly has one trusted evaluation cutoff, and the final lock must
satisfy the existing strict lead relative to the earliest selected kickoff.

```text
all used information/receipts <= portfolio cutoff <= actual lock publication
actual publication + required lead < earliest member kickoff
```

The final transaction checks current model/data rights, active epochs, current
heads, invalidations, known results and fixture identity/kickoff. Market/SP
snapshots must match the sealed refs and compatible freshness/bookmaker policy
at both preparation and publication. No quote after cutoff is used; no stale
quote is relabelled as fresh. Updated prices/source data require a legitimate
replacement/new source snapshot, not silent mutation of an old one.

Different competition or kickoff buckets do not relax time rules. A match that
already started cannot be combined with future legs as a pre-match portfolio.

## 7. Post-lock behavior and settlement

Strict first B release supports immutable Portfolio Lock plus explicit
invalidation/audit; it does **not** silently invent cross-run post-lock
replacement. Any future replacement/refunding design requires its own exact
same-cohort/claim-version/budget rules. Source invalidation after a lock keeps
the original lock and money history; it does not automatically cancel a real
ticket, refund budget, erase slots or authorize another bet.

Settlement is owned once by the Portfolio Lock and its selected tickets. Reuse
the unchanged regular-time settlement/payout and Return accounting functions,
preserve void/unsupported/missing-result handling, wait for all required legal
results, and append result revisions. Forecast metrics count the underlying
program/match/market predictions once; portfolio financial outcomes are not
summed again with source-run money.

The legacy standalone route stays available. Initial B rejects already locked
legacy member runs; it cannot re-consume their heads/slots. Supporting a mixed
locked/unlocked source route later would be a separate contract decision.

## 8. Frozen numerical boundary

Neither A nor B changes Elo, Poisson, P_market/base/fusion, EV, Strategy/Pass,
Return, objectives, bankroll formulas, Decimal rounding or payout mathematics.
New typed validated projections provide one verified portfolio source to the
unchanged kernels. Old single-run/source checks are not removed or bypassed.

NO_BET remains a valid computed outcome; MODEL_UNAVAILABLE is not NO_BET.
Whole-bucket unavailability remains as released unless a separate reviewed
availability/cohort contract explicitly changes it. No dropping unavailable
members to manufacture a qualifying portfolio.

**Choice B is conditional on the shared-ownership proof and full compatibility
acceptance. Larger implementation awaits user confirmation after P0.**
