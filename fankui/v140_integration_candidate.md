# v1.4.0 Integration Candidate IC2

This is an **unpublished integration candidate**, package version `1.4.0.dev0`.
It is not a production upgrade or the final v1.4.0 release/tag. Candidate version
promotion remains part of the subsequent reviewed release process.

## Integration lineage

- Published software baseline: v1.3.1, `f689ff7340598fd37f061473383461117ecb637d`.
- Accepted P1-B: `27808d8e4228f197e4fa9f28cb651567895795bb`.
- Accepted PF-B: `dcc83d1e82f585ae2b4827b0ee7727422b24942a`.
- Reviewed DEV_SPEED_V1: `13826b7d4ced04d3545cd146427c8ccf0302c67f`.

The independent branch uses ordinary merges from the published baseline. The
accepted P1-B/PF-B runtime, numerical code, migrations, configurations and existing
acceptance scripts are retained byte-for-byte (normal Git newline representation
aside). No redesign of qualification or Portfolio is included.

`devtools/integration_history.py` checks the tag target, all four accepted
ancestors, their common ancestors, and recomputes the three ordinary merge trees
to reject conflicts or unreviewed manual resolutions. It checks every earlier
migration byte and both accepted additive migrations, duplicate revisions,
missing parents, branches/cycles and disconnected revisions. The sole 32-revision
chain ends in `8ea7bcd49510 → 9fb8cde5a621 → a0c9def6b732`.

## Layered safety policy

`devtools/safety.py` now uses `V140_LAYERED_FREEZE`, replacing the performance-only
rule that required the entire runtime tree to equal PF-B:

1. Published v1.3.1 domain contracts/numerical kernels, numerical application
   entry points, configuration and historical fixtures remain byte-frozen.
2. Explicitly accepted P1-B/PF-B registration, qualification, Portfolio financial
   contracts and their exact projection manifests retain their accepted bytes.
3. Earlier migration history and the two accepted additive revisions retain
   their bytes and one verified DAG/head.
4. Normal `CI / test` must project back to the published workflow with every
   original final gate; the six algorithm hashes and Elo configuration stay fixed.
5. On the operator host, the separate read-only boundary manifest checks actual
   production file bytes and physical identities.

New integration/tool/test modules are not blanket-rejected for differing from an
old Phase B tree. They still require ordinary review, the unchanged historical
compatibility tests and full acceptance. A new adapter cannot exempt or replace a
kernel, financial contract, old migration, fixture, projection or CI gate. Focused
positive/negative tests exercise those distinctions. Runtime implementation is
unchanged in this candidate; the history audit reports that fact independently.

## Acceptance matrix

| Requirement | Actual acceptance path |
|---|---|
| Independent competition models / generic qualified scope | Existing scoped qualification/model lifecycle and reader tests; additional JP/KR-shaped synthetic scopes with actual private evidence/review/qualification/release/state/pin APIs |
| Different kickoff buckets / immutable snapshots | Portfolio lifecycle/guard tests retain all source frames and exact packet/V4 review/fusion references |
| Assembly / atomic lock / dedicated capital | Existing actual PF ledger, shared legacy ownership, typed seal/revalidation, budget and concurrent-process/crash tests |
| Cross-competition2X1/3X4/4X11 | Existing persisted and installed-wheel paths, plus all three pass types over JP/KR-shaped independent synthetic models |
| Settlement | One money ledger, missing/unsupported/revised results, exact payout/cash/P&L replay |
| Legacy compatibility | Original V1 wire/math and genuine archived-v1.3.1 database/old-binary proofs; a released legacy ledger upgraded into the same database as new qualified sources, with a successful independent Portfolio settlement and exact legacy money/ownership preservation |
| Migration upgrade/downgrade |8e→9fb→a0, full backfill including legacy version0/1, empty roundtrip, injected downgrade rollback, populated refusal |
| Frozen mathematics | Original source-byte projections, six frozen algorithm identities and Elo configuration hash |
| Production safety | Existing protected runtime/databases/inputs remain untouched; actual runtime is still v1.3.0 |

All model/Portfolio examples are self-authored synthetic software acceptance.
They do not qualify real Japanese, Korean or any other competition or authorize bets.

## Competition coverage is capability-based

There is no five-major-league allowlist in the scoped or Portfolio identity model.
Competition/source IDs, country, timezone, team mappings, complete cohorts,
seasons, permissions and model pools are explicit per-scope inputs. The additional
JP/Asia-Tokyo and KR/Asia-Seoul acceptance verifies that the existing regular-league
path does not depend on DE/GB or a five-league enumeration. Other European domestic
leagues can use the same reviewed capability after their own source qualification.

The current accepted V2 profile intentionally admits **regular domestic-league
regulation-time** evidence. Champions League, Europa League, AFC Champions League and other
tournament formats require reviewed stage/group/knockout and regulation/extra-time/
penalty semantics. Their competition IDs and future qualified-model adapters can
feed the same program/pin/snapshot/Portfolio ownership interfaces; eligibility
cannot be granted merely by relabeling an existing domestic profile. This candidate
documents that extension boundary and verifies unsupported tournament profiles
fail closed. It does not claim every event model is implemented.

## Normal CI and fallback

The required workflow identity remains **CI**, job key **test**. Push and PR
triggers and all original release steps remain; only the explicitly reviewed
parallel invocation, configurable inputs and evidence retention are added.
Recommended target is `pytest -n 4 --dist worksteal`. Resource-based `capacity`
resolves up to4 workers; repository variables `CI_TEST_WORKERS` / `CI_TEST_SCHEDULER`
or workflow-dispatch inputs can override it. Set workers to `0` for the full serial
fallback; the collection and downstream migration/wheel/legacy gates do not shrink.
No branch-protection or required-status rule is edited.

The DEV_SPEED experiment workflow is manual-only after closeout; normal pushes do
not launch another performance matrix. FAST/INTEGRATION/FULL_RELEASE remain. Old
25/136-test evidence keeps its original exact identity; it is not relabeled as a
fresh execution on this candidate. New CI-policy and integration checks run on the
new candidate, followed by one necessary complete acceptance cycle.
