# Resolution, version recommendation and future rollout boundary

## G1

`assert_real_bridge_head` recognizes only 6c859ab273fe, 7d96abc3840f and candidate 8ea7bcd49510. It validates the legacy rb schema, and validates the new companion schema for 8e. Unknown/multiple heads fail closed. A 7d CLI read works; 7d does not silently gain OFP pin storage.

## G2

- `ModelSourceV1` discriminates LEGACY/OPENFOOTBALL; default LEGACY is omitted from canonical serialization so published request/artifact hashes do not change.
- `ExistingPinnedModelAccess` remains the formal access boundary. It returns `VerifiedPinnedModelDescriptorV1` through a source-specific reader; the existing tuple-returning API is preserved.
- OFP reader verifies current rights/reviewer authority, exact approval, every required parent, fixed facts/window/config, mechanical state replay, canonical competition/season, complete target scope and training exclusions. Reader and writer share one transaction/database.
- Model state remains in the OFP graph. The new companion adds concrete provenance FKs and DB projection/completeness guards.
- RealBridge creates, persists, reads and audits `REAL_MODEL_PIN_V1` through normal API calls. Synthetic acceptance has `real_model_pin_created=true`, `provenance=SYNTHETIC_SOFTWARE_ACCEPTANCE`, no embedded/test state and no legacy state/training copies.
- Current authorization is rechecked when reading old pins. Legitimate historical pin/input proof is evaluated at its original event, not falsely marked fresh after kickoff.
- Published legacy anchors retain their historical bridge identity; replay accepts the explicitly known published hash or the current implementation hash, never rewrites the artifact or accepts an arbitrary implementation.

## G3

Program → MODEL admission → model-pin stays unchanged. Missing program/admission, admission source mismatch and wrong competition all reject. No production program/pin/epoch/run/DecisionLock is created by this task.

## Test evidence interpretation

The initial red baseline used the existing self-authored OFP source fixture and unmodified reader/DB implementation. Three focused failures were recorded before implementation.

Final vertical tests strengthen this to the actual four-file qualification path: only the two invented match-file byte pins are substituted in the test process. The bundled public README/LICENSE match their original SHA256s. The real acquisition manifest validator, qualifier, parser, data admission, complete fixture/market/SP capture repositories, live preparation, cutoff/pilot/attest/manifest/approval/release/state-binding and RealBridge API all execute. No model/live-proof verifier is patched to succeed. Test reviews/authorities and fixtures are self-authored software-acceptance objects, not real user approvals or source captures.

Fourteen named rejection cases plus storage-guard/migration cases cover the required negative matrix. Complete local and candidate CI results are reported in the review bundle after execution; no unfinished run is counted as green.

## Frozen identities and candidate scope

All Elo/P_market/P_quant/P_llm/P_final/EV/Strategy/Return/Settlement/prospective mathematics and six published hashes remain unchanged. The real bridge/package implementation hashes necessarily change; exact values are recorded in candidate evidence. The original v1.2.0 tag and wheel remain untouched.

The independent source worktree uses candidate operator profile `OPENFOOTBALL_PIN_CANDIDATE_V1` and head8e to test its own metadata/packaging. It is not installed or rebound in the formal runtime. Package version remains the 1.2.0 development base until a separately approved release closeout; the candidate wheel is not a replacement v1.2.0 release.

A full-regression compatibility finding required one additional non-mathematical fix: escape literal `%` when passing SQLAlchemy-rendered SQLite URLs to Alembic ConfigParser. It has its own red/green regression and an exact one-line frozen-source projection.

## Recommended version

**Recommend v1.3.0**, rather than v1.2.1: G1 alone is a patch-level compatibility correction, but the combined work adds a typed public model-source contract, a new supported model source and an additive persistence revision. Existing wire/legacy behavior stays compatible; no new prediction algorithm version is implied.

This task does not bump a published version, merge main, tag, release, replace the formal wheel or migrate/rebind production.

## Future production rollout plan — not executed

1. User reviews architecture, exact candidate commit/tree, red→green evidence, negative matrix, frozen hashes and production-untouched evidence.
2. Separately authorize a v1.3.0 release-only closeout: version/metadata/operator identity updates, full platform/migration/wheel/CI gates, followed by the approved merge/tag/release process. Do not move v1.2.0.
3. Explicit maintenance window and before backup of the existing runtime. Verify the exact installed wheel, database inodes/application IDs, current7d head and frozen 612/13/599 roots. No second active production DB.
4. Explicit in-place 7d→8e additive migration, schema/integrity/FK verification, operator identity rebind and after backup under that later authorization. Do not downgrade a populated OFP pin extension. Keep maintenance cwd isolated from stale checkout metadata.
5. Only with current official Sporttery future targets and valid rights, execute the already approved bootstrap path up to `approval-prepare`, then stop for human confirmation. Never invent targets or reuse expired old slates.
6. After exact model approval is recorded and legitimate release/target/state binding exist, separately authorized program/MODEL admission → model-pin can consume the verified OFP descriptor. Only subsequent authorization permits observation/epoch/prepare/lock.

Current stopping point is synthetic typed pin success and a review package, not real activation. REAL_PROVIDER_HTTP=0, LLM_API_HTTP=0, REAL_PROSPECTIVE_OBSERVATIONS=0, AUTO_BETTING=NO.
