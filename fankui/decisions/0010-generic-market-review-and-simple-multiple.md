# ADR-0010 — Versioned multi-market analysis, review and simple multiple

- Status: Proposed — one overall 0.8 Architecture Review after complete software acceptance
- Baseline: v0.7.0 / `019a62fb00e60303732520e59890f4e8bfb6086d`
- Scope: offline synthetic/fixed-fixture software candidate, package remains 0.7.0

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
