# Production activation v1 — implementation review guide

## Identity and review boundary

- Base: **v1.0.0 / `6d633d4425d5fd6d93918d60526f701a59020b3d`**.
- Approved bridge design: **`8b1cdf727c14510e590450406d89a9edae7ff759`**.
- Package version remains **1.0.0**. Additive migration: **`6c859ab273fe`**, parent `5b748fa162ed`.
- Default operator mode remains **INPUT_PREPARATION**, decision **UNAVAILABLE**.
- Implementation and acceptance use **REAL_PROVIDER_HTTP=0 / LLM_API_HTTP=0**. The review package's candidate snapshot and CI receipt identify the exact tested commit/tree and final gate verdicts.
- This is software implementation review. Actual source admission, legal existing model availability and production observation activation remain separate from synthetic acceptance.

## Implementation structure

| Layer | Files / responsibility |
|---|---|
| New contracts | `domain/real_bridge.py`: independently sealed real artifact family; explicit provenance, repository event time and receipt sequence |
| Closed commands | `application/real_bridge_requests.py`: no event-time, probability, external-analysis or per-run configuration override |
| Adapter / report view | `application/real_bridge.py`, `real_bridge_views.py`: explicit real-only facade and read view for frozen descriptive metrics |
| Numerical boundary | `domain/services/real_bridge.py`: validated real source/plan and `RealPreparedReturnInput`; original V4, fusion, Strategy and Return kernels |
| Input replay | `infrastructure/database/real_bridge_sources.py`: exact existing normalized live capture, mapping, membership, quote, provenance and model authority reads |
| Ledger | `real_bridge_repository.py`: one transaction per command, exact retry, scoped historical replay, head/slot arbitration, complete census |
| Relational graph | `real_bridge_schema.py`, `real_bridge_projections.py`: 64 new `rb_*` tables, 261 triggers, closed typed reference grammar, scalar-ID edges and child projections |
| Identity / filesystem | `infrastructure/files/real_bridge.py`: independent bridge identity, mandatory frozen identities and restricted-root-aware local proof reads |
| Frozen checkout proof | `infrastructure/files/real_bridge_frozen.py`: all 211 original source/config/migration/package files, with only enumerated additive registration/resource hooks projected out |
| Public interface | `interfaces/real_bridge_cli.py`: offline `football-system real-bridge` commands and V4 JSON/Markdown export |

The approved design's typed relationships are represented by concrete child tables plus closed `rb_math_edges` / `rb_external_edges`. Both ends of internal edges bind ID/schema/hash. ID-encoded V4 contexts, evidence, fusion units, outcome units, selected candidates, optimizer steps, hedge witnesses, risk-pair keys and report observations are also projected. Checksum-only optimizer distribution hashes retain their original replay-checksum meaning.

Return policy, objective and validation-policy artifacts have separate typed policy tables. They retain their original bytes/identities; they are not relabelled real source artifacts.

Additional append-only contracts needed by the boundary are `REAL_MODEL_PIN_INVALIDATION_V1`, `REAL_SOURCE_INVALIDATION_V1`, `REAL_REVIEW_AUDIT_V1` and `REAL_RESULT_OBSERVATION_V1`. Result observations remain in the new graph and bind normalized result versions; V1 observation validation is unchanged.

## Frozen value rules

The implementation calls the original functions for normalization, MEDIAN consensus, pinned Elo prediction, QUANT_ONLY base, V4 review checking/correction, candidate EV, Strategy allocation, return convolution/optimization, payout and validation metrics. It does not construct a synthetic `MultiMarketAnalysisV1` or `StrategyPassPlanV2` as an intermediate real container.

The first slice is THREE_WAY only. A run contains every match in one exact UTC kickoff bucket. The slate declaration binds its complete canonical set and original fixture observations. Different seconds or microseconds produce different buckets.

Policy declarations bind the existing default Strategy/risk/fusion/Sporttery values, the allowed integer-fen budgets, and explicitly supplied existing live-input age/bookmaker requirements. They are fixed by the epoch anchor before its window starts. There is no run-time objective/weight/threshold selection.

The bridge verifies these identities at runtime:

| Frozen identity | SHA-256 |
|---|---|
| Return algorithm | `76995ed699a8d054f3865ffbbc0063a46af2123f18ab34aa6b27a91690dc7aa6` |
| Return policy | `bd9cf8b2c3d36ce13327cb49b9513c4d6c995762387606ce26bba37ef0769b47` |
| Objective | `9bb39a771ac2390cb537030c809a7e214dcf88bcce2be17808bc7b3dcbfe7c30` |
| V1 prospective implementation | `6b2a73c14720c60eb30f9bc5d4158bccf095d397748cd1157eb53f13933ddb1f` |
| V1 prospective policy | `06f9cb5289959cf6387b324f9a8ef617d1960a68e88e23cb1423e0c15ef847f8` |
| MEDIAN/source identity wrapper | `c6c22bfb8a2c53aeb624b485c73c6e144c65f1a3cd8b6df2d7c5f32a88cc914f` |

The independent bridge hash covers its new domain, application, calculation, repository, schema, projection, source-read, filesystem and CLI modules. It is recorded separately in each configuration anchor.

## Model and authority boundary

A live pin references an existing completed approved production binding/release/state and its exact target scope. Existing production readers verify the original sealed proof and current authorization; new forecasts invoke only `predict_from_state`. The adapter does not publish a new state, obtain training history, fit parameters or expand the approved target set.

Live pin artifacts store model/configuration/hash metadata, not a duplicated state or its training facts. The embedded fixed-state seam is limited to `SYNTHETIC_SOFTWARE_ACCEPTANCE` programs and the synthetic repository clock. A missing/unauthorized live pin prevents runnable epoch creation.

After epoch creation, a unit can be `MODEL_UNAVAILABLE`. Its P_quant and P_base remain null. The whole bucket is then UNAVAILABLE with every member retained and no lockable packet. This differs from a legitimate frozen-kernel NO_BET decision.

Historical prediction replay that needs a live state still requires lawful current access through the existing authority reader. Expired/unavailable proof is not recovered from a closed capture root.

## Time, replacement and RA21

The anchor has separate repository-observed created/sealed times. Both anchor publication and epoch publication must finish before the declared start. All real artifacts bind the trusted receipt sequence; requests cannot supply it.

Replacement atomically publishes a new analysis, V4 packet, run and replacement event, and consumes the expected PREPARING head. The new cutoff must be later. `rb_head_consumptions.parent_run_id` is the database arbiter for REPLACE XOR LOCK. Losing concurrent requests do not silently follow the new head.

At lock, the repository rechecks current head, active epoch, canonical fixture/kickoff, known results, model availability, source admissions/expiry/revocation, explicit source invalidations, complete source replay, fixed freshness and all prediction slots. A newer ordinary observation alone does not withdraw the old forecast. Explicit invalidation does.

The strict lead remains `locked_at + 60 seconds < kickoff`. Clock checks include graph publication, not just computation before INSERT. A slow publication or failed multi-slot operation rolls back the entire graph. Safe rejected receipts retain the request digest and bounded context rather than failed raw review text.

Official slots are keyed by `(program_id, canonical_match_id, THREE_WAY_market_hash)`, without epoch/slate/bucket/run. A legal same-epoch post-lock revision atomically invalidates the old lock and appends the next slot version. Cross-epoch independent attempts cannot reacquire a slot.

## Replay, census and schema integrity

Reads reconstruct the closed command at its original receipt prefix. Referencing a future receipt is rejected. Original fixture/result row watermarks also prevent a same-UTC later ingestion from changing earlier decisions. Visibility is scoped before source loading so one program's history is not loaded to service another program's command.

Census includes PREPARING, REPLACED_PRE_LOCK, UNAVAILABLE, REJECTED_REQUEST, LOCKED, INVALIDATED_PRE_KICKOFF, SETTLED and STALE_SETTLEMENT. Replaced/failed attempts remain visible but do not become scored predictions. Result revisions require new settlements; older reports retain their original as-of and receipt watermark.

All new tables reject UPDATE, DELETE and replacement inserts. Complete seals require exact typed children and references. The write/audit boundary checks the full schema/trigger/index definitions; equivalent Alembic table-constraint ordering is normalized without dropping any constraint. SQLite expression indexes are verified directly because Alembic cannot reflect them. Existing SQLAlchemy legacy cyclic-FK warnings remain visible.

## CLI and packaging

`football-system real-bridge --help` lists the closed operations. Each command requires an existing migrated local database and evidence root. Live model operations additionally need the existing authority-pin configuration and operator identity. There is no provider-fetch, training, purchase, betting or force/skip command.

`packet --artifact-id <real-run-id> --output <json> --markdown <md>` exports the unchanged V4 wire plus the provenance/evidence companion. Output paths cannot alias the database, request, authority pins or the active manual proof file.

The wheel contains **83 declared resources**. `scripts/real_bridge_acceptance.py` is the external isolated-wheel scaffold: fixed synthetic sources/state, network and rebuild disabled, full replacement/result-revision/report/restart lifecycle. `scripts/wheel_e2e.py` runs it under two input orders, hash seeds and Decimal precisions and compares complete canonical artifact-graph fingerprints.

## Acceptance evidence map

| Acceptance | Automated evidence |
|---|---|
| RA01–RA02 | Closed real types, wrong market/classification/source and external-analysis injection rejection |
| RA03–RA04 | No-match/no-ticket/no-V1-seed bootstrap; missing pin, late anchor and caller event-time rejection |
| RA05–RA06 | Invalidated/unavailable model; partial unit availability retains the whole unavailable bucket |
| RA07–RA08 | Exact normalized capture/mapping/member/quote replay and same-count source corruption rejection |
| RA09–RA10 | One-second/microsecond bucket separation; strict deadline, slow publication rollback and crash retry |
| RA11 | Fresh real V4 binding, old-packet rejection, absolute P_llm/cap and UNKNOWN-lineup sidecar semantics |
| RA12–RA13 | Multi-hop retained replacement census and multi-connection replace/replace, replace/lock races |
| RA14 | Cross-slate/bucket and cross-epoch slot refusal; atomic post-lock version progression |
| RA15 / RA19 | Frozen checkout proof and unchanged original regression suite; real two-match values through original kernels |
| RA16 | Genuine `git archive v1.0.0` populated database; all old definitions/triggers/rows/artifacts preserved; empty roundtrip and populated refusal |
| RA17 | Every populated rb table mutation refused; wrong/extra edges and consistently resealed report corruption rejected |
| RA18 | Network/rebuild denial; synthetic clock cannot register a live observation program |
| RA20 | Installed-wheel full lifecycle, exact retry, restart, hash-seed/order/Decimal determinism |
| RA21 | Post-prepare expiry/revocation, model/source invalidation, canonical kickoff change, same-time known result and unchanged ordinary observation |

The final pytest summary, frozen verification, installed-wheel evidence and exact-SHA CI result in the review manifest are the gate verdicts. The fixture case catalogue and this evidence map are not substitutes for those receipts.

## Remaining real-world prerequisites

Production readiness still requires current source rights/admissions, a legally accessible suitable existing model binding, complete verified fixture/SP/market/evidence inputs and separately approved activation. Software fixture reports retain zero real observations and **INSUFFICIENT_PROSPECTIVE_SAMPLE**. No ROI, alpha or P_llm improvement claim follows from these tests.
