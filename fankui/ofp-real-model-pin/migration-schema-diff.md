# OFP real model pin additive schema diff

Candidate revision **8ea7bcd49510** extends **7d96abc3840f**. No production migration is performed in this task.

## Existing objects

`rb_model_pins` keeps its existing DDL and nullable legacy columns unchanged:

| Column | Existing FK, retained |
|---|---|
| artifact_id | rb_artifacts.artifact_id |
| release_id | production_quant_model_releases.release_id |
| model_state_id | quant_model_states.quant_model_state_id |
| source_analysis_id | analysis_runs.analysis_run_id |

All existing rb/ofp tables, indices and trigger definitions remain in place. OFP release/state rows are never inserted into legacy model tables.

## Added table

`ofp_real_model_pins`:

| Column | Constraint |
|---|---|
| pin_id | PK; deferred FK to rb_model_pins.artifact_id, RESTRICT |
| release_id | NOT NULL; FK to ofp_artifacts.artifact_id, RESTRICT |
| release_hash | NOT NULL; SHA256-length CHECK |
| binding_id | NOT NULL; FK to ofp_artifacts.artifact_id, RESTRICT |
| binding_hash | NOT NULL; SHA256-length CHECK |

This companion stores references only, never the model state or training facts. OFP pins use the typed OPENFOOTBALL descriptor; legacy-only columns remain NULL for this branch. NULL alone grants nothing: the typed artifact, source-specific references and mandatory companion seal must all agree.

## Five new guards

- `trg_ofp_real_pin_update`: append-only update refusal.
- `trg_ofp_real_pin_delete`: append-only delete refusal.
- `trg_ofp_real_pin_replace`: no replacement of an existing pin relation.
- `trg_ofp_real_pin_projection`: exact REAL_MODEL_PIN/OFP release/binding/target-plan/approval kind, ID, hash, source identity, lineage, target scope and parent-link checks; cannot attach an OFP relation to a legacy pin or already sealed artifact.
- `trg_ofp_real_pin_complete` on `rb_seals`: an OFP pin cannot become complete without its exact companion; a legacy pin cannot gain an OFP companion.

Existing deferred artifact/seal FKs mean an incomplete graph cannot commit. No FK is dropped or disabled. Tests intentionally omit/corrupt the writer's companion to prove these DB guards reject a bad projection even after application verification.

## Compatibility and downgrade

- 6c and 7d remain supported for existing legacy RealBridge operations; an OFP pin requires the new companion/guards.
- Fresh schema and 7d→8e upgrade/check are tested.
- Empty companion: 8e→7d removes only this extension and restores the old snapshot.
- Legacy-only populated DB: old pin/anchor JSON and hashes survive upgrade/replay and empty-extension downgrade.
- Any OFP pin/companion: downgrade refuses before dropping anything; snapshot/head remain unchanged.

The v1.1→7d tests retain their exact old revision boundary. When testing the newer head, empty 8e→7d is checked before comparing a refused populated 7d downgrade; this does not treat a multi-revision downgrade as one atomic transaction.
