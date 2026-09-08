"""Bind corrected quant history to typed versions without replacing V1 FKs."""

from alembic import op
from football_system.domain.quant_integrity import (
    QuantIntegrityPlanDefinitionV1,
    QuantIntegrityPlanV1,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes

from football_system.infrastructure.database.production_audit_schema import (
    production_audit_trigger_sql_v1,
)
from football_system.infrastructure.database.production_inference_schema import (
    production_inference_trigger_sql_v1,
)
from football_system.infrastructure.database.production_quant_schema import (
    production_quant_trigger_sql_v1,
)
from football_system.infrastructure.database.versioned_quant_schema import (
    VERSIONED_QUANT_TABLES,
    install_versioned_quant_schema_in_connection,
    versioned_quant_trigger_sql_v2,
)

revision = "f51e294b0687"
down_revision = "e40d183af576"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline versioned history upgrade cannot verify prior schema"
        )
    install_versioned_quant_schema_in_connection(op.get_bind())


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline versioned history downgrade cannot verify empty graph"
        )
    # Lock before inspecting emptiness, and roll back DDL and guards together.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for table in VERSIONED_QUANT_TABLES:
                if connection.exec_driver_sql(
                    f"SELECT EXISTS (SELECT 1 FROM {table})"
                ).scalar():
                    raise RuntimeError(
                        "cannot downgrade populated immutable versioned history graph"
                    )
            for table, path, schema in (
                (
                    "training_history_manifests",
                    "content_payload.history",
                    "TRAINING_HISTORY_GRAPH_V1",
                ),
                (
                    "production_quant_model_releases",
                    "training_manifest.content_payload.history",
                    "TRAINING_HISTORY_GRAPH_V1",
                ),
                (
                    "production_quant_integrity_pilot_outputs",
                    "content_payload.terminal_state_core",
                    "QUANT_INTEGRITY_TERMINAL_ELO_STATE_CORE_V1",
                ),
            ):
                if connection.exec_driver_sql(
                    f"SELECT EXISTS (SELECT 1 FROM {table} WHERE "
                    f"json_extract(artifact_json, '$.{path}.schema_version') IS NOT ?)",
                    (schema,),
                ).scalar():
                    raise RuntimeError(
                        "cannot downgrade populated or unknown versioned history graph"
                    )
            # V1 definitions have no schema_version. Validate their exact sealed
            # envelope instead of treating every missing discriminator as legacy.
            for artifact_id, content_hash, document in connection.exec_driver_sql(
                "SELECT artifact_id, content_hash, artifact_json "
                "FROM production_quant_integrity_pilot_plans"
            ):
                try:
                    plan = QuantIntegrityPlanV1.model_validate(
                        strict_json_bytes(document.encode("utf-8"))
                    )
                except ValueError as error:
                    raise RuntimeError(
                        "cannot downgrade unknown pilot plan graph"
                    ) from error
                if (
                    type(plan.content_payload.definition)
                    is not QuantIntegrityPlanDefinitionV1
                    or plan.artifact_id != artifact_id
                    or plan.content_hash != content_hash
                ):
                    raise RuntimeError(
                        "cannot downgrade versioned or unknown pilot plan graph"
                    )
            if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
                raise RuntimeError("cannot downgrade unknown versioned history lineage")
            previous = {
                **production_quant_trigger_sql_v1(),
                **production_inference_trigger_sql_v1(),
                **production_audit_trigger_sql_v1(),
            }
            for name in versioned_quant_trigger_sql_v2():
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
                if name in previous:
                    connection.exec_driver_sql(previous[name])
            for table in reversed(VERSIONED_QUANT_TABLES):
                connection.exec_driver_sql(f"DROP TABLE {table}")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
