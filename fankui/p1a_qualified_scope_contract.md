# P1-A Qualified Competition Scope / Profile Contract

Status: development contract, independent of v1.3.1 patch release. Design baseline:
`reader-generalization-plan.md` accepted by the user. P1-A adds one pure domain
module, `domain/openfootball_scope.py`; every pre-existing production source,
configuration, migration and operator file remains byte-identical to approved P0.

## 1. Authority boundary

`QualifiedCompetitionScopeV2` declares the closed shape of a qualified scope; its
constructor is **not** a qualification authority. IDs/hashes supplied by a caller
are inert until P1-B verifies the actual immutable source/review/authority graph
and current rights in the same database transaction. JSON Schema validation, raw
byte validation and state replay do not themselves authorize training or inference.

P1-A adds no registry, migration, CLI admission, model-source dispatch or source
type to the released RealBridge pin union. That union still rejects V2 input.
Production qualification and P1-B persistence are deliberately separate work.

## 2. Schemas and invariants

| Contract | Required binding |
|---|---|
| `ScopeFileV2` | Safe relative path, role, exact bytes/SHA, capture time, full row count, historical season |
| `ScopeTimePolicyV2` | Explicit competition timezone, tzdata2025.2/IANA2025b/TZif hash, source-time review, reject DST gap/fold |
| `ScopeMappingV2` | Explicit source label → opaque canonical ID, reviewed reuse/new intent and evidence; no allocation by this module |
| `ScopeSeasonV2` | Contiguous sequence, WARMUP block → PILOT_TARGET → PRODUCTION_TARGET; distinct season IDs |
| `ScopeRecordV2` | Every file/index/raw hash has exactly one FACT_CANDIDATE or reviewed EXCEPTION; exceptions cannot carry results |
| `CompetitionScopeProfileV2` | Independent competition/source/files/parser/time/mapping/window/full census; exact frozen Elo config |
| `ScopeIdentityV2` | Tagged profile hash + deterministic scope ID + canonical competition ID |
| `QualifiedCompetitionScopeV2` | Profile identity and typed qualification/rights/authority refs all own the same scope |
| `ScopeInstanceArtifactRefV2` | Exact scope, competition, instance, artifact kind/ID/hash |
| `ScopedModelBindingV2` | Independent approval, release, state-binding, pin, program, facts/window roots, exact target set and state/training hashes |
| `LegacyBundesligaScopeV1` | Explicit inert V1 discriminator; original source ID/window/612-13-599 remain fixed |

Unknown fields/versions/kinds, boolean counts, duplicate or missing rows, repeated
match/result IDs, foreign teams/seasons, altered Elo config, arbitrary exceptions,
target-season facts and invented historical publication time fail closed.
Nested `model_copy`/`model_construct` values are revalidated at each public boundary.

Source IDs include competition plus upstream commit/source key. Profile, mapping,
window, facts and exception roots are independently tagged. History remains
CURRENT_SNAPSHOT_OBSERVED / SOURCE_TIME_RESEARCH; publication/finalization/version
time is UNKNOWN. A Git/capture time is not substituted for match publication time.

## 3. Pure APIs implemented

- `ScopeIdentityV2.of(profile)` creates an immutable **reference**, not an admission.
- `validate_scope_source_bytes(profile, raw_files, tzif_bytes)` checks the exact
  file set/bytes/hashes, duplicate JSON keys, full raw census, every row hash,
  explicit FT score and canonical mapping, exact normalized score payload and
  kickoff from the supplied pinned TZif. Gap/fold times are rejected. Exception
  rows retain their raw hash/review, never normalized to invented FT values.
- `logical_bootstrap_key(profile, targets)` excludes caller request/instance IDs,
  review-reference changes and cutoff; the same competition/source files/window/
  canonical mapping/target set derives the same key. P1-B must enforce UNIQUE and
  trusted complete target-census ownership; this pure function cannot enforce DB
  concurrency or authorize changing the target cohort to retry a failed pilot.
- `validate_scoped_model_state(scope, binding, state)` checks all ownership and
  logical-instance links, exact targets/cutoff and complete frozen Elo replay from
  only that scope's declared fact candidates. Swapping or pooling an Elo state
  fails even where configuration hashes are identical.

Refs for qualification and model stages are distinct and typed. Formal lifecycle
remains `program → MODEL admission → model-pin → anchor/epoch/run`, after exact
human approval/release/target/state-binding prerequisites. The contract does not
create any of those records or substitute references for repository verification.

## 4. V1 compatibility and P1-B obligations

The existing `OpenFootballPinnedModelReader`, V1 source/writer/ledger, model union,
JSON/hash/FK behavior and provider-wide 599 results are unchanged. Existing tests
allow exactly one added contract module and still compare every old byte. A new
boundary test separately proves no production file was edited.

P1-B will implement the accepted single-reader pipeline and V2-owned facts with
scope/instance FK/seal enforcement. Required checks include trusted evidence
resolution, reviewed expected census (not self-selected small counts), source
parser/time policy identity and TZif origin, current authority/retention/revocation,
full pilot/approval lineage, one-attempt persistence, exact future targets/live
proof and incompatibility of V1/V2 mixed graphs. P1-A does not claim these gates
already exist in a production repository.

## 5. Meaningful acceptance

Self-authored synthetic A/B inputs use different source commits/files, competition
IDs, row counts, timezones, mapping/window/facts/exception roots, state and training
hashes, program and typed approval/release/state/pin refs. The unchanged Elo builder
rebuilds each state independently. Tests reject cross-scope state and every swapped
model ref, altered raw bytes/record hashes/FT/kickoff/mapping/TZif, incomplete census,
ROI-based exception reasons, target facts, request-key retry and unknown versions.

This is contract/software acceptance, not the future fully persisted two-qualified-
model Portfolio E2E. That remains P1-B + PF-B integration work. No real competition
qualification, actual approval, model release, pin or production write occurs here.
