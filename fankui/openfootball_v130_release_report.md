# v1.3.0 OFP typed model-source release closeout

## Accepted implementation and qualified scope

- Accepted branch `feature/ofp-real-model-pin`, commit `a6952a4f9b86e1c273aa66e9f48c7930d8687a0a`, tree `31e46e3059f2b9855fe30af1591d6afd9b5b1cb4`; candidate CI #50 SUCCESS.
- OPENFOOTBALL typed model-source infrastructure is established. **The current qualified implementation scope is Bundesliga 2026/27 only.** Premier League, La Liga, Serie A and Ligue 1 require separate competition qualification and reader-scope generalization.
- This release is version/packaging/operator maintenance and documentation closeout of that approved implementation. No new observation schema, player data, competition, prediction/strategy mathematics, Sporttery logic or prospective lifecycle feature is introduced.

## Version, schema and provenance

Package/module version is **1.3.0**; head **8ea7bcd49510**, directly after **7d96abc3840f**. The wheel contains 88 explicit resources, retaining all 87 v1.2.0 resources and the approved new migration. Fixed tzdata2025.2/IANA2025b/Berlin TZif is unchanged.

The operator V2 profile is `OPENFOOTBALL_PIN_RELEASE_V1`; its release-base identity is the accepted a6952a4 commit, with actual installed package bytes and glue hash recorded separately. The previous v1.2.0 tag, target commit, wheel and release history remain preserved. RealBridge implementation identity follows actual code; it is distinct from frozen mathematical hashes.

## Correct lifecycle

After legitimate model release/target/state binding exists, the order is:

```text
program → MODEL admission → model-pin → policy/anchor → future epoch
```

Pin does not precede program. Software release/maintenance creates none of these real objects.

## Release and upgrade gates

1. Ruff, compileall, full pytest, fresh migration/check; 7d→8e; empty downgrade/re-upgrade; populated OFP downgrade refusal; wheel build and isolated installed-wheel E2E; whitespace; Windows and exact feature CI.
2. Ordinary feature→main merge preserving reviewed history, green main CI, annotated v1.3.0 tag and green tag CI, then promote the exact tested wheel as release artifact.
3. Before runtime maintenance, verify installed noneditable v1.2.0/RECORD, both original DB identities/application_ids/head7d, frozen roots and zero real activation objects; make a complete SQLite dual-DB backup.
4. Install v1.3.0 into the existing environment; migrate the original database files to8e, verify integrity/FK, exact old DDL/rows, new guards and zero pin companion, then explicit operator/head rebind. No second active production DB.
5. Menu/operator smoke checks and post-upgrade backup. Retain installation ID, runtime/backups paths and prior release journals. Failure preserves maintenance journal/lock for explicit review.

Maintenance admits only the exact published v1.2.0 V2 identity, or the retained fixed V1 legacy maintenance profile. It cannot silently reinterpret arbitrary old/candidate identities. Alembic maintenance disables cwd/src prepend so stale source metadata cannot shadow the installed wheel.

Isolated E2E verifies the genuine installed v1.3 wheel and prior installations created by pinned v1.1/v1.2 source fixtures. The old source fixture uses explicit version metadata only for its subprocess; production preflight independently verifies the actual previously installed v1.2 wheel and all RECORD entries.

## Frozen comparison

Elo config `c98d595d3afb03fe629e776fa9a0e70f24e31fcd49884be3ff11e9c979ca78e4` and published Return algorithm/policy, Objective, Prospective implementation/policy and Market consensus identities remain unchanged. No change to Elo/Poisson/P_market/P_quant/P_llm/P_final/EV/Strategy/Return/Settlement mathematics.

Production retains **612 source records / 13 explicit exceptions / 599 admitted facts**, with:

- mapping root `67a96a44b05339fa4dd5d5f11ddc50a536c78fac4f8884cb072750dd31c9b589`
- training window `0e4c4ddbaafee6600de070d05f344ce9384742b81ff4191dc98b36cef9367116`
- facts root `9440470d48857e43e9607b3aaf85581087d3d40da77d337dbe099b9853cb2d15`

## Final receipts and stop

Exact feature/main/tree/tag-object/target, CI identities, wheel SHA/resource count, backups, original/after DB identity, integrity/FK/guards, operator binding and frozen comparison are recorded in `upload/v1.3.0/` and the final v1.3.0 handoff. This checked-in specification does not claim an unfinished gate passed.

Final state must be SOFTWARE=READY, RUNTIME=READY, OPERATOR=READY, MODE=INPUT_PREPARATION. REAL_MODEL_PIN, REAL_OBSERVATION_PROGRAM, REAL_EPOCH, REAL_PROSPECTIVE_RUN and DECISION_LOCK remain zero. Stop after production upgrade.

## Next REAL BOOTSTRAP PLAN — not executed in release

Use the official Chinese Sporttery future slate before **2026-10-10** for current target discovery; preserve complete fixture/source/capture evidence and explicit unavailable/out-of-scope entries. Do not invent targets or treat unqualified competitions as supported. Bundesliga2026/27 can proceed only with complete legal identities, rights, future target exclusions and the fixed historical window.

Then, under separate bootstrap authorization: qualify/recheck sources and scope → trusted cutoff → observed pilot/attest/manifest → **approval-prepare and stop for human review**. Only exact subsequent approval permits approval-record/release-build/target acceptance/state binding, followed by separately authorized **program → MODEL admission → model-pin**. No target discovery, real model pin or observation is performed by this release closeout.
