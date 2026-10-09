# P1 and Portfolio Dependencies / Synthetic E2E Plan

**Plan only. P0 independent acceptance comes first; user confirmation is required
before implementing these larger architecture changes.**

## 1. Workstreams and dependency order

| Unit | Work | Depends on | Acceptance output |
|---|---|---|---|
| P0 | Preserve UNKNOWN in full catalog reading | v1.3.0 baseline | Red/green, full regression, immutable history/roots and no production change |
| P1-A | Versioned qualified scope/profile and per-instance contracts | Approved reader-generalization plan | Closed schemas, source/authority/mapping/window/exception invariants |
| P1-B | Scope-aware repository/ledger + one shared OFP reader | P1-A | Two independent qualified scopes, no state/fact/approval cross-contamination |
| P1-C | Premier League qualification first; La Liga/Serie A/Ligue 1 evidence in parallel | P1-A; source review | Per-competition exact reviewed inputs, never JSON-presence-as-qualification |
| PF-A | Final B source snapshot/shared ownership/Portfolio Lock contract | User review of A/B decision | Ownership, time, budget, invalidation/report/settlement specification |
| PF-B | Additive registries and typed portfolio adapters | PF-A | Legacy/new concurrency arbitration and unchanged numerical replay |
| INT | Cross-scope, cross-run, cross-bucket synthetic vertical E2E | P1-B + PF-B | Entire acceptance matrix below |
| REAL | Separately authorized real bootstrap/deployment | Full review/gates and explicit authorization | Never implied by synthetic success |

P1-C data review and PF-A design can proceed in parallel. Neither may overwrite
Bundesliga V1, reuse its 599 facts/root as another competition, or block its
legitimate existing human-review workflow. Missing canonical current-season
mappings/aliases remain explicit input preparation, not a reason to invent IDs.

## 2. P1 minimum implementation scope

Use the accepted `reader-generalization-plan.md` as the design baseline:

- A single OpenFootballPinnedModelReader verification pipeline, with a frozen
  V1 Bundesliga scope adapter and an explicit qualified-scope version.
- Separate source manifests/pins, canonical mapping, time policy/TZif, window,
  raw records, exceptions, facts, approvals, state/release and pin per scope.
- Scoped bootstrap instance/phase/request uniqueness and one-attempt pilot;
  changing an instance/key cannot restart a failed logical attempt.
- Typed scope-aware FK/seal relationships; no cross-scope parents or silently
  widened legacy results/training path.
- Preserve V1 global 612/13/599/catalog/root checks; V2-owned facts do not alter
  the old provider-wide 599-result set without a separately reviewed compatible
  migration of that boundary.
- Reuse frozen Elo code/configuration. Each state is built from only its own
  qualified competition facts; matching config hashes do not permit shared state.
- Current rights/authority, full graph/state/approval replay, future targets and
  exact exclusions remain mandatory. History stays retrospective observed data,
  not fabricated point-in-time historical performance.

Production source qualification remains pending. The prior assessment found:

| Competition | Raw rows (two seasons) | Review-required rows | Immediate issue |
|---|---:|---:|---|
| Premier League | 760 | 27 | direct score arrays with unproven period |
| La Liga | 760 | 25 | 10 missing FT plus 15 direct arrays |
| Serie A | 760 | 46 | 10 missing FT plus 36 direct arrays |
| Ligue 1 | 612 | 25 | awarded, direct arrays and canceled/missing score |

These are inventory counts, not admitted facts or approved exceptions. Premier
League is the first qualification target; the other three prepare evidence,
mapping/window and rights reviews independently. No real qualification, training
or approval is performed by this planning document.

## 3. Required independent-model synthetic fixture

The final E2E uses at least two genuinely independent qualified source graphs:

- competition A and B have different source IDs/file manifests/mapping roots,
  separate rights/reviewer scope, separate bootstrap IDs and non-overlapping
  canonical training matches/results.
- Each cohort is fully parsed/qualified/admitted by the proposed formal APIs;
  test-only source bytes/rights are self-authored and explicitly synthetic.
  No patched qualification/approval/model verifier returns unconditional PASS.
- The frozen Elo builder independently produces state A and state B. Assert
  distinct training_data_hash/state_hash/release/pin IDs and correct competition
  binding. Do not copy a state, pool A+B facts, or use one test_state for both.
- Independent programs/anchors/epochs contain at least four eligible future
  matches total. Use different exact UTC kickoff buckets and separate runs,
  sufficient to exercise 2X1, 3X4 and 4X11 across competition boundaries.
- Real input contract shapes use self-authored fixture/market/SP evidence with
  actual normal repository capture/replay, fixed synthetic clocks and no HTTP.
- This proves software behavior only: never marks synthetic results as real
  forecasts, approvals, returns or actual bets.

## 4. Final synthetic E2E acceptance matrix

| ID | Required case | Required evidence |
|---|---|---|
| Q01 | Independent A/B qualification → state → exact approval → release → pin | Full graph IDs/hashes; disjoint training facts and state ownership |
| Q02 | Swap A state/pin/approval into B | Scope/type/hash/authority rejection, no partial artifact |
| Q03 | Missing/expired/revoked/illegal model pin | MODEL_UNAVAILABLE or closed rejection; no fabricated probabilities |
| S01 | At least two programs and distinct kickoff runs → immutable snapshots | Original run/bucket identities preserved; snapshot has no claims/stake |
| P01 | Cross-competition 2X1 | Complete source refs, 1 atomic bet, exact original kernel/payout replay |
| P02 | Cross-competition 3X4 | Complete source refs, 4 atomic bets, consistent common portfolio budget |
| P03 | Cross-competition 4X11 | Complete source refs, 11 atomic bets, exact distribution/optimizer replay |
| P04 | Portfolio Lock | Trusted cutoff/publication, complete graph/seal, all claims atomically owned |
| B01 | Unified budget/stake/cash | No member-budget sum; no duplicate budget claim; exposure/bounds unchanged |
| B02 | Snapshot/assembly fails before lock | No prediction-slot/head/budget consumption |
| T01 | Market or SP expires after snapshot | Lock rejected at current time; original snapshot bytes preserved |
| T02 | Earliest kickoff reached or lead boundary exactly equal | Reject; no backdating or borrowing later bucket deadline |
| T03 | Source evidence appears after common cutoff | Reject; capture/available/ingested and review timelines distinct |
| U01 | Duplicate member/run or same canonical match from different runs/programs | Reject duplicate physical prediction/leg; no extra samples |
| U02 | Same program prediction slot through another epoch/bucket | Shared authority rejects reacquisition |
| A01 | Legacy lock vs Portfolio Lock race on a source head/slot | Exactly one commit winner; loser has no partial graph or claims |
| A02 | PRE_LOCK replacement vs Portfolio Lock race | Same ownership invariant; stale snapshot cannot lock |
| I01 | Model/source invalidation before final lock | Entire portfolio rejected; no stale member borrowing |
| I02 | Invalidation after lock | Original financial/claim history retained; no automatic refund/rebet |
| R01 | Settlement across different kickoff/result times | Missing results remain explicit; one financial settlement, no double budget/sample count |
| R02 | Result revision/void/unsupported settlement case | Append-only corrected financial view; frozen rules unchanged |
| L01 | Complete legacy regression | Genuine v1.3 catalogs, pins/locks/reports/bytes and frozen policies remain compatible |
| M01 | Additive migration and verified legacy claim backfill | Old rows/DDL unchanged where specified; no unmirrored ownership path |
| M02 | Empty downgrade/re-upgrade, populated downgrade refusal | No destruction of scoped models or portfolio claims |

Additional statistical/availability rule: a pin alone is insufficient. Respect
the existing whole-bucket UNAVAILABLE behavior; keep missing models/inputs in
the census and do not silently remove them to force a ticket.

## 5. Release-grade checks after future implementation

Ruff, compileall, full pytest, fresh/upgrade/roundtrip/refusal migrations,
isolated installed-wheel E2E, Windows and exact candidate CI, frozen mathematics
and v1.3 compatibility comparison. Every real production operation remains out
of scope until separately approved.

The approved next action after this document is a **user decision on P1/PF-A
implementation scope**, not an automatic production rollout or real approval.
