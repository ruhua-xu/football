# P1-B Scoped Model Persistence — implementation review

Development only, version `1.4.0.dev0`, additive head `9fb8cde5a621` after released
`8ea7bcd49510`. v1.3.1 release/runtime authorization is independent. No production
qualification, model pin or migration is performed by this development work.

## Authoritative boundary

P1-A `CompetitionScopeProfileV2` remains an immutable **candidate** contract.
Its inert qualification/approval refs cannot be imported as authority. The new
`ScopeQualificationSubjectV2` binds the complete profile, exact acquisition and
parser evidence, source rights, explicit country/catalog context and actual DB
storage identity. Evidence is read safely outside public Git. Trusted authority
pins are configuration, never accepted from candidate JSON.

`LocalTrainingEvidence.review` verifies actual file hashes, reviewer identity,
source/schema/payload scope and review time. Current authority and source/model
rights are rechecked. Qualification records all raw rows, files, mappings and
exceptions atomically; direct arrays never become FT by inference or outcome.

Candidate ingestion/availability times are **not** promoted to admitted history.
New admitted result IDs/times and their root are derived from the repository's
trusted admission clock, separately sealed alongside the original candidate root.
The instance cutoff must follow completed qualification. Only this admitted
projection is replayed into an Elo state. Original V1 facts remain untouched.

## Persistence and workflow

`ofpv2_*` owns scopes, files, mappings, complete raw records, scoped artifacts,
typed parents, final seals, instances, phases, idempotent operations and pin
companions. Composite FKs bind scope/instance/kind/hash. Final seal is mandatory
at COMMIT through deferred FKs. Update/delete/replace and incomplete graph
publication are rejected. Application UDF validation lives in write triggers,
so native read-only SQLite integrity/backup tools continue to work.

The logical bootstrap key is database-unique, independent of caller request or
instance names. Pilot reservation commits once; failure remains recorded and
cannot be retried by another key. Full registered future-window census and
complete actual fixture/market/SP preparations are required, with no target
training overlap and no silent removal of unavailable targets.

Public repository entry point:

```python
repository = production_quant_repository.openfootball_scoped_binding()
```

Workflow: `prepare_scope → record_scope → prepare_instance → record_instance →
run_pilot → attest_pilot → create_manifest → prepare_approval [HUMAN REVIEW STOP]
→ record_approval → build_release → create_target_plan → bind_model_state →
model_pin_candidate`.

Qualification, target-scope and model approval require separate exact review
payloads. No API generates its own permission or creates a RealBridge pin as a
side effect. Actual pin order remains `program → MODEL admission → model-pin`.

## Shared reader and old behavior

One `OpenFootballPinnedModelReader` resolves typed V1/V2 graph adapters to the
same verified descriptor boundary. V1's fixed graph verification is preserved;
V2 verifies persisted authority, complete census, phase parents, independent Elo
replay, exact model approval/release/binding, source provenance and live proof.
Both use the caller's existing session/transaction and share final scope/state
projection checks. No per-league reader or mutable league selector is introduced.

`OPENFOOTBALL_QUALIFIED_V2` pins have a typed `ofpv2_real_model_pins` companion.
They retain no copied legacy state or training facts. Legacy JSON omits the
default source field exactly as before; V1 OFP pins keep their original companion.
Historical pin proof is evaluated at its original event, while current authority
remains mandatory. Synthetic qualification cannot authorize a LIVE program.

V2 facts never enter `match_results` or legacy training tables. Tested coexistence
retains Bundesliga V1's 612/13/599 and exact old OFP artifact JSON while two V2
scopes own 24/40 raw rows and 23/39 admitted results independently.

## Compatibility and tests

- Two independently qualified persisted synthetic competitions with actual
  source/review/authority files, separate facts/state/approval/release/pin/program
  and different kickoff buckets; all normal capture/preparation/pin APIs run.
- Wrong/expired/revoked authority, changed source/review bytes, incomplete census,
  missing pin companion and invalid scope/release/binding/target refs reject.
- P1-A inert refs are explicitly rejected as qualification input.
- Genuine archived v1.3.1 code builds a populated legacy lifecycle DB; 8e→9
  preserves old rows/DDL/constraints/artifact JSON; empty roundtrip passes and
  populated scoped downgrade refuses.
- Counted reversible integration hunks project back to the approved P1-A +
  v1.3.1 base; every remaining byte compares. No numerical service/config file
  is excluded, and all frozen mathematical identities remain exact.

The OS temporary development tree was lost during execution. Completed edit
records were recovered with exact eight-file hashes into a durable workspace;
logs, code and environments now persist on D:. Disposable self-authored raw test
evidence remains outside public Git to satisfy the unchanged privacy boundary.

Complete acceptance receipts and package identity are emitted separately. This
document is not a production approval, nor PF-B integration acceptance.
