"""Additive, strongly constrained OFP provenance for existing REAL_MODEL_PIN_V1.

Legacy rb_model_pins columns and FKs remain exactly as released. An OFP pin can
complete only with this typed companion; no model state/training facts are copied.
"""
import sqlalchemy as sa

from football_system.domain.openfootball_production import SOURCE_ID

TABLE = "ofp_real_model_pins"
HEAD = "8ea7bcd49510"


def ofp_real_model_pin_table(metadata):
    if TABLE in metadata.tables:
        return metadata.tables[TABLE]
    for name in ("rb_model_pins", "ofp_artifacts"):
        if name not in metadata.tables:
            sa.Table(name, metadata, sa.Column("artifact_id", sa.String(160), primary_key=True))
    return sa.Table(TABLE, metadata,
        sa.Column("pin_id", sa.String(160), sa.ForeignKey("rb_model_pins.artifact_id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"), primary_key=True),
        sa.Column("release_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("release_hash", sa.String(64), nullable=False),
        sa.Column("binding_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("length(release_hash)=64 AND length(binding_hash)=64", name="ck_ofp_real_pin_hashes"))


def ofp_real_model_pin_triggers():
    statements={}
    def guard(name, table, predicate, action="INSERT"):
        statements[name]=f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {predicate} BEGIN SELECT RAISE(ABORT,'OFP real model pin integrity'); END"
    for action in ("UPDATE", "DELETE"):
        guard("trg_ofp_real_pin_"+action.lower(), TABLE, "1", action)
    guard("trg_ofp_real_pin_replace", TABLE, f"EXISTS(SELECT 1 FROM {TABLE} WHERE pin_id=NEW.pin_id)")
    guard("trg_ofp_real_pin_projection", TABLE, """EXISTS(SELECT 1 FROM rb_seals WHERE artifact_id=NEW.pin_id) OR NOT EXISTS(
      SELECT 1 FROM rb_artifacts pin JOIN ofp_artifacts rel ON rel.artifact_id=NEW.release_id
      JOIN ofp_artifacts binding ON binding.artifact_id=NEW.binding_id
      JOIN ofp_artifacts plan ON plan.artifact_id=json_extract(binding.artifact_json,'$.payload.target_plan[0]')
      JOIN ofp_artifacts approval ON approval.artifact_id=json_extract(rel.artifact_json,'$.payload.approval[0]')
      WHERE pin.artifact_id=NEW.pin_id AND pin.schema_version='REAL_MODEL_PIN_V1' AND rb_valid_header_v1(pin.artifact_json)=1
      AND json_extract(pin.artifact_json,'$.model_source.source_type')='OPENFOOTBALL'
      AND json_extract(pin.artifact_json,'$.source_identity')='"""+SOURCE_ID+"""'
      AND json_extract(pin.artifact_json,'$.release_id') IS NULL
      AND json_extract(pin.artifact_json,'$.model_state_id') IS NULL
      AND json_extract(pin.artifact_json,'$.source_analysis_id') IS NULL
      AND json_extract(pin.artifact_json,'$.state') IS NULL
      AND json_extract(pin.artifact_json,'$.model_source.release_id')=NEW.release_id
      AND json_extract(pin.artifact_json,'$.model_source.release_hash')=NEW.release_hash
      AND json_extract(pin.artifact_json,'$.model_source.binding_id')=NEW.binding_id
      AND json_extract(pin.artifact_json,'$.model_source.binding_hash')=NEW.binding_hash
      AND rel.kind='RELEASE' AND rel.artifact_hash=NEW.release_hash
      AND binding.kind='STATE_BINDING' AND binding.artifact_hash=NEW.binding_hash
      AND plan.kind='TARGET_PLAN' AND approval.kind='APPROVAL'
      AND json_extract(binding.artifact_json,'$.payload.release[0]')=rel.artifact_id
      AND json_extract(binding.artifact_json,'$.payload.release[1]')=rel.artifact_hash
      AND json_extract(binding.artifact_json,'$.payload.target_plan[1]')=plan.artifact_hash
      AND json_extract(rel.artifact_json,'$.payload.approval[1]')=approval.artifact_hash
      AND json_extract(plan.artifact_json,'$.payload.release[0]')=rel.artifact_id
      AND json_extract(plan.artifact_json,'$.payload.release[1]')=rel.artifact_hash
      AND json_extract(pin.artifact_json,'$.release_hash')=rel.artifact_hash
      AND json_extract(pin.artifact_json,'$.authority_hash')=json_extract(binding.artifact_json,'$.payload.authority_hash')
      AND json_extract(pin.artifact_json,'$.model_lineage.state_id')=json_extract(binding.artifact_json,'$.payload.model_state_id')
      AND json_extract(pin.artifact_json,'$.model_lineage.state_hash')=json_extract(binding.artifact_json,'$.payload.state_hash')
      AND json_extract(pin.artifact_json,'$.model_lineage.state_hash')=json_extract(rel.artifact_json,'$.payload.state_hash')
      AND json_extract(pin.artifact_json,'$.model_lineage.training_data_hash')=json_extract(binding.artifact_json,'$.payload.training_data_hash')
      AND json_extract(pin.artifact_json,'$.model_lineage.config_hash')=json_extract(binding.artifact_json,'$.payload.config_hash')
      AND json_extract(pin.artifact_json,'$.scope_match_ids')=json_extract(binding.artifact_json,'$.payload.target_scope')
      AND EXISTS(SELECT 1 FROM ofp_parent_links p WHERE p.child_id=binding.artifact_id AND p.parent_id=rel.artifact_id AND p.parent_hash=rel.artifact_hash)
      AND EXISTS(SELECT 1 FROM ofp_parent_links p WHERE p.child_id=binding.artifact_id AND p.parent_id=plan.artifact_id AND p.parent_hash=plan.artifact_hash)
      AND EXISTS(SELECT 1 FROM ofp_parent_links p WHERE p.child_id=binding.artifact_id AND p.parent_id=approval.artifact_id AND p.parent_hash=approval.artifact_hash)
    )""")
    guard("trg_ofp_real_pin_complete", "rb_seals", f"""EXISTS(SELECT 1 FROM rb_artifacts a WHERE a.artifact_id=NEW.artifact_id
      AND a.schema_version='REAL_MODEL_PIN_V1' AND (
        (json_extract(a.artifact_json,'$.model_source.source_type')='OPENFOOTBALL' AND NOT EXISTS(SELECT 1 FROM {TABLE} p WHERE p.pin_id=a.artifact_id))
        OR (coalesce(json_extract(a.artifact_json,'$.model_source.source_type'),'LEGACY')<>'OPENFOOTBALL' AND EXISTS(SELECT 1 FROM {TABLE} p WHERE p.pin_id=a.artifact_id))
      ))""")
    return statements


def install_ofp_real_model_pin_triggers(connection):
    if connection.dialect.name == "sqlite" and connection.scalar(sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:name"), {"name":TABLE}):
        for statement in ofp_real_model_pin_triggers().values():
            connection.exec_driver_sql(statement.replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS ", 1))


def assert_ofp_real_model_pin_schema(connection):
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    from football_system.infrastructure.database.real_bridge_schema import schema_signature
    expected={TABLE:schema_signature(str(CreateTable(ofp_real_model_pin_table(sa.MetaData())).compile(dialect=dialect())).strip())}
    expected.update({name:schema_signature(sql) for name,sql in ofp_real_model_pin_triggers().items()})
    rows=connection.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE name=:table OR name LIKE 'trg_ofp_real_pin_%'"), {"table":TABLE}).all()
    actual={name:schema_signature(sql) for name,sql in rows if sql is not None}
    if actual != expected:
        raise ValueError("OFP_MODEL_PIN_SCHEMA_OR_GUARDS_REQUIRED")
