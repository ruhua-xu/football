# ADR-0010 — Versioned multi-market analysis, review and simple multiple

- Status: Accepted — overall 0.8 Architecture Review PASSED
- Baseline: v0.7.0 / `019a62fb00e60303732520e59890f4e8bfb6086d`
- Scope: offline synthetic/fixed-fixture validation; release package 0.8.0
- Accepted: 2026-09-16, user approved the overall 0.8 architecture and closed both V4 blockers.
- Accepted implementation: `e6e7fba26b0fc69506100f4c908b45b2fcd890ae`.
- V4 revision baseline: `4a0bce0b5429cb86c223d75b8288b3bea32076cb`.

## Decisions

- Add MarketKeyV2, typed ordered OutcomeKeyV1 catalogs and generic Decimal distributions.
  Keep legacy market.py, ThreeWayProbability, Elo, V1/V2/V3 wire and Strategy Pass V1 frozen.
- New markets are HANDICAP_THREE_WAY (explicit home handicap), TOTAL_GOALS (8) and
  CORRECT_SCORE (31). THREE_WAY stays the legacy Elo/fusion path. No HALF_FULL.
- Independent Poisson uses admitted regular-time facts from one competition/season cohort,
  true home/away splits, fixed history requirements and configuration-hashed numeric policy.
- Infinite-support mapping uses adaptive marginal cutoffs, full Poisson CDF/remainders and
  certified probability rounding intervals. TOTAL_GOALS 7+ is exactly the full complement
  of 0..6. OTHER includes omitted explicit scores and the correctly classified tails.
  No fixed 0..10 truncation/renormalization. Uncertifiable rounding is a hard failure.
- V4 adds per-(match,market) review units and exact context bindings. It carries generic
  P_market/P_quant and model/evidence lineage, never P_final, EV, tickets or money inputs.
- GENERIC_LLM_REVIEW_DELTA_V1 is an explicit N-outcome correction path; old
  LLM_REVIEW_DELTA_V1 stays frozen. Generic correction remains a bounded convex move,
  then deterministic probability-quantum closure; no independent clamp without closure.
- Strategy V2 first selects constituent match subsets, then expands each subset's choice
  Cartesian product. Each outcome must qualify independently. Within-match choices are OR;
  expanded cross-match legs are AND. No same-match cross-market compound within a ticket.
- Store the complete expanded AtomicBet graph plus canonical selected-outcome/REST state
  descriptor. This is deterministic reconstruction, not enumeration of 31^N raw states.
  An unselected outcome is one REST equivalence class for a ticket's payouts. State-count
  bounds are checked before allocation; no sampling or silently discarded choices.
- Add typed immutable SQLite graphs and deferred completeness seals. Legacy tables,
  triggers, rows and serialized artifacts retain their values. Populated downgrade fails.
- Offline fixture admission is explicitly SYNTHETIC_ACCEPTANCE_DATA. It is not a new
  production data mode or a grant to reuse expired/removed evidence. Real source capability
  and real model performance remain unproven; no provider/LLM HTTP is used by this work.

## Limited V4 contract revision

- Quant MODEL_UNAVAILABLE requires an UNAVAILABLE/MODEL_UNAVAILABLE review. Quant AVAILABLE
  permits VALID or abstention with INSUFFICIENT_EVIDENCE, INVALID_CONTEXT or SKIPPED_DISABLED;
  it cannot claim MODEL_UNAVAILABLE. All packet/context/evidence bindings still apply.
- Evidence/context/disabled abstention with an existing P_base preserves it exactly as P_final,
  uses zero influence and records the real failure_code. No fake P_llm or correction is used.
- Scenarios restore scenario_id, MAIN/SECONDARY/UPSET type, description, typed outcomes,
  trigger_conditions and evidence_refs. Counter-scenarios use a separate typed contract with
  if_scenario_id, alternative_scenario_id, fails_outcomes, rationale and evidence_refs;
  both references must exist within the same market review unit.
- Scenario IDs, preferred/avoid outcomes, risk_tags, limitations and each evidence_refs list
  are unique. Preferred and avoid outcomes cannot overlap. Valid evidence can be reused
  across different scenarios without duplicating an individual reference list.
- This amendment changes only V4 contracts, validation and abstention fallback dispatch.
  Generic correction math, Poisson, taxonomy, EV, Strategy/Settlement V2, database graph,
  V1/V2/V3 wire and legacy THREE_WAY behavior remain frozen.
