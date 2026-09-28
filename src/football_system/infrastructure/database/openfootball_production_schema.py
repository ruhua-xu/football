"""Additive binding ledger; legacy observed tables and schemas are untouched."""

import sqlalchemy as sa

OFP_TABLES = ("ofp_artifacts", "ofp_parent_links", "ofp_operations", "ofp_phase_slots", "ofp_canonical_entities", "ofp_source_records")


def openfootball_production_tables(metadata):
    if OFP_TABLES[0] in metadata.tables:
        return {n: metadata.tables[n] for n in OFP_TABLES}
    tables = {}
    tables["ofp_artifacts"] = sa.Table("ofp_artifacts", metadata,
        sa.Column("artifact_id", sa.String(160), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("artifact_json", sa.Text, nullable=False),
        sa.Column("recorded_at_utc", sa.String(40), nullable=False),
        sa.CheckConstraint("length(artifact_hash)=64 AND json_valid(artifact_json)", name="ck_ofp_artifact_shape"),
        sa.CheckConstraint("json_extract(artifact_json,'$.artifact_id')=artifact_id AND json_extract(artifact_json,'$.artifact_hash')=artifact_hash AND json_extract(artifact_json,'$.kind')=kind AND json_extract(artifact_json,'$.recorded_at_utc')=recorded_at_utc", name="ck_ofp_artifact_projection"))
    tables["ofp_parent_links"] = sa.Table("ofp_parent_links", metadata,
        sa.Column("child_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("parent_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("parent_hash", sa.String(64), nullable=False))
    tables["ofp_operations"] = sa.Table("ofp_operations", metadata,
        sa.Column("request_key", sa.String(160), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("artifact_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False))
    tables["ofp_phase_slots"] = sa.Table("ofp_phase_slots", metadata,
        sa.Column("phase", sa.String(40), primary_key=True),
        sa.Column("artifact_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False, unique=True))
    tables["ofp_canonical_entities"] = sa.Table("ofp_canonical_entities", metadata,
        sa.Column("canonical_id", sa.String(160), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_label", sa.String(200), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("entry_json", sa.Text, nullable=False),
        sa.Column("binding_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False),
        sa.UniqueConstraint("kind", "source_label", name="uq_ofp_exact_canonical_label"),
        sa.CheckConstraint("kind IN ('TEAM','COMPETITION','SEASON') AND json_valid(entry_json)", name="ck_ofp_canonical_shape"))
    tables["ofp_source_records"] = sa.Table("ofp_source_records", metadata,
        sa.Column("source_filename", sa.String(80), primary_key=True),
        sa.Column("record_pointer", sa.String(80), primary_key=True),
        sa.Column("binding_id", sa.String(160), sa.ForeignKey("ofp_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_file_sha256", sa.String(64), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("record_json", sa.Text, nullable=False),
        sa.Column("canonical_match_id", sa.String(160), sa.ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("included", sa.Boolean, nullable=False),
        sa.Column("exception_reason", sa.String(100)),
        sa.Column("fact_json", sa.Text),
        sa.CheckConstraint("json_valid(record_json) AND ((included=1 AND exception_reason IS NULL AND fact_json IS NOT NULL AND json_valid(fact_json)) OR (included=0 AND exception_reason IS NOT NULL AND fact_json IS NULL))", name="ck_ofp_record_disposition"))
    return tables


def openfootball_production_triggers():
    values = {}
    for table in OFP_TABLES:
        for action in ("UPDATE", "DELETE"):
            name = f"trg_{table}_{action.lower()}_immutable"
            values[name] = f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable OpenFootball production binding'); END"
    values["trg_ofp_parent_exact_hash"] = """CREATE TRIGGER IF NOT EXISTS trg_ofp_parent_exact_hash
      BEFORE INSERT ON ofp_parent_links WHEN NOT EXISTS
      (SELECT 1 FROM ofp_artifacts WHERE artifact_id=NEW.parent_id AND artifact_hash=NEW.parent_hash)
      BEGIN SELECT RAISE(ABORT,'OpenFootball parent hash mismatch'); END"""
    values["trg_ofp_record_scope"] = """CREATE TRIGGER IF NOT EXISTS trg_ofp_record_scope
      BEFORE INSERT ON ofp_source_records WHEN NOT EXISTS
      (SELECT 1 FROM ofp_artifacts WHERE artifact_id=NEW.binding_id AND kind='DATA_BINDING')
      BEGIN SELECT RAISE(ABORT,'OpenFootball data binding required'); END"""
    values["trg_ofp_legacy_training_forbidden"] = """CREATE TRIGGER IF NOT EXISTS trg_ofp_legacy_training_forbidden
      BEFORE INSERT ON quant_model_training_facts WHEN EXISTS
      (SELECT 1 FROM match_results r JOIN providers p ON p.provider_id=r.provider_id
       WHERE r.match_result_id=NEW.match_result_id AND p.code='OPENFOOTBALL')
      BEGIN SELECT RAISE(ABORT,'OpenFootball results require the versioned production binding'); END"""
    return values


def assert_no_legacy_openfootball_training(session, result_ids):
    """Raw normalized rows do not carry a removable LIVE_STRICT admission."""
    if not result_ids:
        return
    statement = sa.text("SELECT 1 FROM match_results r JOIN providers p ON p.provider_id=r.provider_id "
        "WHERE p.code='OPENFOOTBALL' AND r.match_result_id IN :ids LIMIT 1").bindparams(sa.bindparam("ids", expanding=True))
    if session.execute(statement, {"ids": tuple(result_ids)}).first() is not None:
        raise ValueError("OPENFOOTBALL_OBSERVED_BINDING_REQUIRED_NOT_LEGACY_TRAINING")


def install_openfootball_production_triggers(connection):
    if connection.dialect.name == "sqlite" and connection.scalar(sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='ofp_artifacts'")):
        for statement in openfootball_production_triggers().values():
            connection.exec_driver_sql(statement)
