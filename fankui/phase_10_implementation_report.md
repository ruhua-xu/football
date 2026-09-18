# Phase 10 — Prospective Validation / Production Closeout implementation

## Candidate identity and scope

- Base: `v0.9.0` / `9a869d42b44282551cbdc694d8efc6d0009e9dde`.
- Branch: `feature/1.0.0-prospective-validation-production-closeout`.
- Package/runtime metadata: **0.9.0**. ADR-0012: **Proposed**.
- Additive migration: `5b748fa162ed`, parent `4a637e9051dc`.
- Exact implementation SHA/tree, relative-v0.9 diff, wheel and test receipts are
  bound in the accompanying `phase10-snapshot.json` / `phase10-manifest.json`.
  They are external to this report to avoid a self-referential commit hash.
- Scope: software framework candidate. No provider/LLM HTTP, real data acquisition,
  real fitting/inference, betting, payment or subscription activation.

## Implementation

New closed contracts cover typed manual evidence, lineup/absence/schedule/rest/form,
snapshot binding, frozen epochs, preparations, V4 correction sidecars, decision
locks, pre-kickoff invalidations, result observations, settlements and reports.
The original V4 wire and all pre-existing prediction/return/settlement math remain
byte-frozen. Existing source graphs are replayed through their original repositories.

`ProspectiveService` composes those original APIs. Public `prospective` commands
include epoch/evidence-import/prepare/lock/result-import/settle/report/epoch-close,
read-only show/audit, policy and capability. Prepare writes exact V4 JSON plus a
typed-evidence Markdown companion. External GPT review stays file-based and
correction categories are a separate sidecar; absent review records abstention.

Persistence is an independent **30-table / 126-trigger** `pv_*` graph with typed FKs, deferred completeness
seals, immutable rows and receipts, BEGIN IMMEDIATE writes, exact request retries
and complete source/math/census replay. No destructive migration or populated
downgrade is allowed. A workflow interrupted before lock leaves only immutable
preparations/source/audit records; those do not implicitly become decisions.

## No-lookahead and provenance proof boundary

- All event timestamps come from the repository clock, not imported JSON/CLI flags.
- Source publication/availability, observation and local ingestion are separate.
- Lock plus frozen safety lead must be earlier than every relevant kickoff;
  known result/changed kickoff/unavailable fixture/late lock fails closed.
- Superseding lock and old-run invalidation are one transaction, before both
  deadlines; same epoch match+market duplication under another slate is rejected.
- Result chains append and require the current predecessor. Current validation
  marks old settlements stale after a result correction until re-settled.
- Reports seal the entire observed epoch census and receipt-sequence watermark.
  Later events sharing an identical UTC timestamp do not change old report bytes.
- Manual bytes are actually SHA-256 checked through contained-path file I/O.
  Expired/closed capture roots cannot be used; committed retry does not reread raw.
- Unknown lineup/absence remains unknown. Freshness uses source publication;
  missing publication cannot become FRESH through ingestion. Typed correction
  categories require the relevant fresh, explicit factual evidence.
- Local system time is not external timestamp notarization. A reviewer declaration
  is provenance, not automated proof of factual truth, license or provider history.

## Validation semantics

Quality is grouped by competition and canonical market. Brier/log-loss, ten-bin
calibration, outcome/bucket frequencies and paired layer deltas are separate from
stake/return/yield and portfolio-event calibration. Zero true probabilities remain
explicit infinite log loss. Decimal128 avoids ambient decimal context drift.
P_base/P_final correction census includes improved, worsened, neutral and abstained
records, including unsuccessful changes and NO_BET units. There is no outcome filter
or profitable-case whitelist and no feedback into weights or training parameters.

## A–Z acceptance map

| Cases | Executable evidence |
|---|---|
| A, P, R, S, U, V | `test_a_p_r_s_u_v_full_lock_settle_report_retry` |
| B | `test_b_late_lock_and_clock_regression_fail_without_receipt` |
| C, W | `test_c_w_sql_mutation_partial_graph_and_resealed_report_corruption` |
| D, E | `test_d_e_pre_kickoff_supersession_and_result_revisions` |
| F, I | `test_f_i_manual_hash_freshness_dynamic_packet_and_expired_exact_retry`; source-time/path unit tests |
| G, H | `test_g_h_sidecar_cannot_promote_unknown_to_confirmed_fact`; lineup/absence contract tests |
| J | `test_j_provider_remains_unavailable_without_activation` for all capabilities |
| K | `test_k_typed_form_and_schedule_cutoff_and_historical_lock_replay`; schedule/rest/form unit oracle |
| L, M, N | `test_l_m_n_full_correction_census_has_both_directions_and_neutral` |
| O | full lifecycle's explicit abstention census; metric oracle |
| Q | `test_q_positive_stake_exact_frozen_settlement_golden` |
| T | `test_t_empty_report` |
| X, Y | `test_x_y_v090_upgrade_preserves_all_old_rows_definitions_and_replay` |
| Z | `scripts/prospective_acceptance.py` via isolated installed-wheel E2E |

Additional tests cover unknown→FACT promotion, incomplete typed graphs, duplicate
match-market scope, request-key conflicts, clock modes, unsupported/void results,
epoch closure, CLI output immutability and forbidden event/probability overrides.

## Frozen compatibility and local gates

The upgrade test uses **git archive v0.9.0 and its original code** to create a
populated old database. It compares every previous table/trigger/row after upgrade,
replays/retries the old optimizer, adds a new epoch and proves populated downgrade
refusal. All pre-existing domain/application/config/migration file bytes are compared
with v0.9.0: **127 previous files are byte-identical**. The earlier v0.8→current
test remains unchanged and passes as well.

Targeted A–Y, corruption, CLI, fresh/check/empty downgrade-reupgrade, genuine v0.9
upgrade and frozen-byte regression passed during implementation. **Z passed** in
an isolated installed-wheel environment, together with the existing historical,
V4/Strategy and return-distribution acceptance paths. Ruff, compileall, offline
wheel build and whitespace checks passed. The wheel allowlist contains **81
resources**; it does not package private data or the acceptance scripts.

The complete clean-tree collection is **2,747 tests** (44 additive cases), divided
into **129 disjoint groups** for local execution; no legacy test is omitted. The
exact final outcome, per-group coverage and candidate CI status are in the
accompanying `phase10-pytest-summary.json` and `phase10-ci-receipt.json`. Final
review handoff requires every group and every required CI step to pass. The old
uncommitted provider diagnostics are not part of this Git-tree test collection.
Existing SQLAlchemy cyclic-FK sort warnings are retained, not suppressed.

Frozen return identities:

- Algorithm: `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6`
- Policy: `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47`
- Objective: `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30`
- Prospective implementation: `6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f`
- Prospective policy: `06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8`

## Known limitations / real evidence status

**INSUFFICIENT_PROSPECTIVE_SAMPLE**. The accepted old `MultiMarketAnalysisV1` contract
is explicitly synthetic; this candidate never relabels it. Real epoch preparation
returns `PRODUCTION_DECISION_ADAPTER_UNAVAILABLE` until a separately reviewed lawful
production decision-source route exists. Manual real evidence registration alone
does not activate that route or constitute an empirical performance observation.

Provider capability capture is permanently closed; no deleted raw is recovered.
Provider historical publication/version chains remain unproven where unavailable.
Conflicting fixture history is conservatively blocked, not guessed into a new
canonical identity. Validation is bounded (256 runs, 4096 report units, 128 evidence
per run) and rejects overflow without selective omission. Descriptive small-sample
P&L cannot establish model validity, ROI or alpha. No automatic tuning occurs.

## Review boundary

After local gates, commit/push this feature only and bind the exact candidate CI
Success receipt into the external review bundle. Then stop for the single overall
Architecture Review. No main merge, 1.0.0 version bump or release tag is included.
