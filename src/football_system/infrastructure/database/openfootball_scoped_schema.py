"""Additive scoped ledger; old OFP/RB tables, foreign keys and rows stay intact."""
import hashlib

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine

from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_scope import CompetitionScopeProfileV2
from football_system.domain.openfootball_scoped import ScopedArtifactV2

HEAD = "9fb8cde5a621"
SUPPORTED_HEADS = frozenset({HEAD})
TABLES = ("ofpv2_scopes", "ofpv2_artifacts", "ofpv2_parents", "ofpv2_seals", "ofpv2_files", "ofpv2_mappings",
          "ofpv2_records", "ofpv2_instances", "ofpv2_phases", "ofpv2_operations", "ofpv2_real_model_pins")


def _header(raw):
    try:
        from football_system.infrastructure.files.training_evidence import strict_json_bytes
        ScopedArtifactV2.model_validate(strict_json_bytes(raw.encode()))
        return 1
    except Exception:
        return 0


def _profile_hash(raw):
    try:
        from football_system.infrastructure.files.training_evidence import strict_json_bytes
        return CompetitionScopeProfileV2.model_validate(strict_json_bytes(raw.encode())).content_hash
    except Exception:
        return "INVALID"


def _record_hash(raw):
    try:
        from football_system.infrastructure.files.training_evidence import strict_json_bytes
        return hashlib.sha256(canonical_json(strict_json_bytes(raw.encode())).encode()).hexdigest()
    except Exception:
        return "INVALID"


@event.listens_for(Engine, "connect")
def register_scoped_functions(connection, _record):
    if hasattr(connection, "create_function"):
        connection.create_function("ofpv2_header_valid", 1, _header, deterministic=True)
        connection.create_function("ofpv2_profile_hash", 1, _profile_hash, deterministic=True)
        connection.create_function("ofpv2_record_hash", 1, _record_hash, deterministic=True)


def scoped_tables(metadata):
    if TABLES[0] in metadata.tables:
        return {name: metadata.tables[name] for name in TABLES}
    for table, key in (("competitions", "competition_id"), ("matches", "internal_match_id"), ("rb_model_pins", "artifact_id")):
        if table not in metadata.tables:
            sa.Table(table, metadata, sa.Column(key, sa.String(160), primary_key=True))
    result = {}
    def col(name, kind=None, **kw):
        return sa.Column(name, kind if kind is not None else sa.String(160), nullable=kw.pop("nullable", False), **kw)
    def table(name, *columns):
        result[name] = sa.Table(name, metadata, *columns)
    def fk(local, remote, *, deferred=False):
        return sa.ForeignKeyConstraint(local, remote, ondelete="RESTRICT", deferrable=deferred, initially="DEFERRED" if deferred else None)
    table("ofpv2_scopes", col("scope_id", primary_key=True), col("scope_hash", sa.String(64)), col("source_id"), col("competition_id"),
        col("qualification_id", unique=True), col("profile_json", sa.Text()), col("classification"),
        sa.UniqueConstraint("scope_id", "scope_hash"), fk(["competition_id"], ["competitions.competition_id"]),
        fk(["qualification_id", "scope_id", "scope_hash"], ["ofpv2_artifacts.artifact_id", "ofpv2_artifacts.scope_id", "ofpv2_artifacts.scope_hash"], deferred=True),
        sa.CheckConstraint("json_valid(profile_json) AND length(scope_hash)=64 AND classification IN ('REAL_SOURCE_DATA','SYNTHETIC_SOFTWARE_ACCEPTANCE')", name="ck_ofpv2_scope"))
    table("ofpv2_artifacts", col("artifact_id", primary_key=True), col("artifact_hash", sa.String(64), unique=True),
        col("scope_id"), col("scope_hash", sa.String(64)), col("instance_id"), col("kind"), col("recorded_at_utc", sa.String(40)), col("artifact_json", sa.Text()),
        sa.UniqueConstraint("artifact_id", "scope_id", "scope_hash"),
        sa.UniqueConstraint("artifact_id", "scope_id", "instance_id"),
        sa.UniqueConstraint("artifact_id", "scope_id", "instance_id", "kind", "artifact_hash"),
        fk(["scope_id", "scope_hash"], ["ofpv2_scopes.scope_id", "ofpv2_scopes.scope_hash"]),
        fk(["artifact_id"], ["ofpv2_seals.artifact_id"], deferred=True),
        sa.CheckConstraint("json_valid(artifact_json) AND length(artifact_hash)=64", name="ck_ofpv2_header"))
    table("ofpv2_parents", col("child_id", primary_key=True), col("parent_kind", primary_key=True), col("scope_id"), col("parent_id"),
        col("parent_hash", sa.String(64)), col("parent_instance_id"),
        fk(["child_id"], ["ofpv2_artifacts.artifact_id"]),
        fk(["parent_id", "scope_id", "parent_instance_id", "parent_kind", "parent_hash"],
            ["ofpv2_artifacts.artifact_id", "ofpv2_artifacts.scope_id", "ofpv2_artifacts.instance_id", "ofpv2_artifacts.kind", "ofpv2_artifacts.artifact_hash"]),
        fk(["parent_id"], ["ofpv2_seals.artifact_id"]))
    table("ofpv2_seals", col("artifact_id", primary_key=True), col("completed_at_utc", sa.String(40)), fk(["artifact_id"], ["ofpv2_artifacts.artifact_id"]))
    table("ofpv2_files", col("scope_id", primary_key=True), col("path", sa.String(200), primary_key=True), col("file_json", sa.Text()),
        col("record_count", sa.Integer()), col("sha256", sa.String(64)), fk(["scope_id"], ["ofpv2_scopes.scope_id"]),
        sa.CheckConstraint("record_count>=0 AND json_valid(file_json)", name="ck_ofpv2_file"))
    table("ofpv2_mappings", col("scope_id", primary_key=True), col("kind", primary_key=True), col("source_label", sa.String(200), primary_key=True),
        col("canonical_id"), col("entry_json", sa.Text()), fk(["scope_id"], ["ofpv2_scopes.scope_id"]),
        sa.CheckConstraint("json_valid(entry_json)", name="ck_ofpv2_mapping"))
    table("ofpv2_records", col("scope_id", primary_key=True), col("file", sa.String(200), primary_key=True), col("position", sa.Integer(), primary_key=True),
        col("raw_hash", sa.String(64)), col("raw_json", sa.Text()), col("disposition"), col("reason", nullable=True),
        col("match_id", nullable=True), col("result_id", nullable=True, unique=True), col("fact_json", sa.Text(), nullable=True),
        fk(["scope_id", "file"], ["ofpv2_files.scope_id", "ofpv2_files.path"]), fk(["match_id"], ["matches.internal_match_id"]),
        sa.CheckConstraint("position>=0 AND json_valid(raw_json) AND length(raw_hash)=64 AND ((disposition='FACT_CANDIDATE' AND reason IS NULL AND match_id IS NOT NULL AND result_id IS NOT NULL AND fact_json IS NOT NULL AND json_valid(fact_json)) OR (disposition='EXCEPTION' AND reason IS NOT NULL AND match_id IS NULL AND result_id IS NULL AND fact_json IS NULL))", name="ck_ofpv2_record"))
    table("ofpv2_instances", col("instance_id", primary_key=True), col("scope_id"), col("logical_key", sa.String(64), unique=True), col("artifact_id", unique=True),
        fk(["scope_id"], ["ofpv2_scopes.scope_id"]), fk(["artifact_id"], ["ofpv2_seals.artifact_id"], deferred=True))
    table("ofpv2_phases", col("scope_id", primary_key=True), col("instance_id", primary_key=True), col("kind", primary_key=True),
        col("artifact_id", unique=True), col("artifact_hash", sa.String(64)),
        fk(["artifact_id", "scope_id", "instance_id", "kind", "artifact_hash"],
            ["ofpv2_artifacts.artifact_id", "ofpv2_artifacts.scope_id", "ofpv2_artifacts.instance_id", "ofpv2_artifacts.kind", "ofpv2_artifacts.artifact_hash"]))
    table("ofpv2_operations", col("scope_id", primary_key=True), col("instance_id", primary_key=True), col("request_key", primary_key=True),
        col("request_hash", sa.String(64)), col("artifact_id", unique=True), fk(["artifact_id"], ["ofpv2_seals.artifact_id"], deferred=True),
        fk(["artifact_id", "scope_id", "instance_id"], ["ofpv2_artifacts.artifact_id", "ofpv2_artifacts.scope_id", "ofpv2_artifacts.instance_id"]),
        fk(["scope_id"], ["ofpv2_scopes.scope_id"]))
    table("ofpv2_real_model_pins", col("pin_id", primary_key=True), col("scope_id"), col("scope_hash", sa.String(64)), col("instance_id"),
        col("release_id"), col("release_hash", sa.String(64)), col("binding_id"), col("binding_hash", sa.String(64)),
        fk(["pin_id"], ["rb_model_pins.artifact_id"], deferred=True), fk(["scope_id", "scope_hash"], ["ofpv2_scopes.scope_id", "ofpv2_scopes.scope_hash"]),
        fk(["instance_id"], ["ofpv2_instances.instance_id"]), fk(["release_id"], ["ofpv2_seals.artifact_id"]), fk(["binding_id"], ["ofpv2_seals.artifact_id"]))
    return result


def scoped_triggers():
    values = {}
    def guard(name, table, condition, action="INSERT"):
        values[name] = f"CREATE TRIGGER {name} BEFORE {action} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'scoped OFP ledger invariant'); END"
    definitions = scoped_tables(sa.MetaData())
    for name, table in definitions.items():
        for action in ("UPDATE", "DELETE"):
            guard("trg_" + name + "_" + action.lower(), name, "1", action)
        key = " AND ".join("x." + c.name + " IS NEW." + c.name for c in table.primary_key)
        guard("trg_" + name + "_replace", name, f"EXISTS(SELECT 1 FROM {name} x WHERE {key})")
    # Keep application UDFs in write guards, not CHECK constraints: native
    # read-only SQLite integrity/backup tools must work without app callbacks.
    guard("trg_ofpv2_scope_hash", "ofpv2_scopes", "ofpv2_profile_hash(NEW.profile_json)<>NEW.scope_hash")
    guard("trg_ofpv2_header_valid", "ofpv2_artifacts", "ofpv2_header_valid(NEW.artifact_json)<>1")
    guard("trg_ofpv2_record_hash", "ofpv2_records", "ofpv2_record_hash(NEW.raw_json)<>NEW.raw_hash")
    guard("trg_ofpv2_header_projection", "ofpv2_artifacts", " OR ".join(
        f"json_extract(NEW.artifact_json,'$.{path}') IS NOT NEW.{column}" for path, column in (
            ("artifact_id", "artifact_id"), ("artifact_hash", "artifact_hash"), ("owner.scope_id", "scope_id"),
            ("owner.scope_hash", "scope_hash"), ("instance_id", "instance_id"), ("payload.kind", "kind"), ("recorded_at_utc", "recorded_at_utc"))))
    guard("trg_ofpv2_header_time", "ofpv2_artifacts", """EXISTS(SELECT 1 FROM ofpv2_scopes s WHERE s.scope_id=NEW.scope_id
      AND (json_extract(NEW.artifact_json,'$.owner.competition_id') IS NOT s.competition_id
      OR (s.classification='REAL_SOURCE_DATA' AND ABS((julianday(NEW.recorded_at_utc)-julianday('now'))*86400)>30)))""")
    guard("trg_ofpv2_pilot_terminal", "ofpv2_artifacts", """NEW.kind IN ('PILOT_REPORT','PILOT_FAILURE') AND EXISTS(
      SELECT 1 FROM ofpv2_artifacts a WHERE a.scope_id=NEW.scope_id AND a.instance_id=NEW.instance_id
      AND a.kind IN ('PILOT_REPORT','PILOT_FAILURE'))""")
    guard("trg_ofpv2_parent_projection", "ofpv2_parents", """EXISTS(SELECT 1 FROM ofpv2_seals WHERE artifact_id=NEW.child_id) OR NOT EXISTS(
      SELECT 1 FROM ofpv2_artifacts c,ofpv2_artifacts p,ofpv2_seals z,json_each(c.artifact_json,'$.parents') j
      WHERE c.artifact_id=NEW.child_id AND c.scope_id=NEW.scope_id AND p.artifact_id=NEW.parent_id
      AND z.artifact_id=p.artifact_id AND rb_us_v1(z.completed_at_utc)<=rb_us_v1(c.recorded_at_utc) AND json_extract(j.value,'$.artifact_id')=NEW.parent_id
      AND json_extract(j.value,'$.artifact_hash')=NEW.parent_hash AND json_extract(j.value,'$.kind')=NEW.parent_kind
      AND json_extract(j.value,'$.instance_id')=NEW.parent_instance_id)""")
    for name, path, predicates in (
        ("ofpv2_files", "files", "json_extract(j.value,'$.path')=NEW.path AND json_extract(j.value,'$.record_count')=NEW.record_count AND json_extract(j.value,'$.sha256')=NEW.sha256 AND json(j.value)=json(NEW.file_json)"),
        ("ofpv2_mappings", "mapping", "json_extract(j.value,'$.kind')=NEW.kind AND json_extract(j.value,'$.source_label')=NEW.source_label AND json_extract(j.value,'$.canonical_id')=NEW.canonical_id AND json(j.value)=json(NEW.entry_json)"),
    ):
        guard("trg_" + name + "_projection", name, f"EXISTS(SELECT 1 FROM ofpv2_scopes s JOIN ofpv2_seals z ON z.artifact_id=s.qualification_id WHERE s.scope_id=NEW.scope_id) OR NOT EXISTS(SELECT 1 FROM ofpv2_scopes s,json_each(s.profile_json,'$.{path}') j WHERE s.scope_id=NEW.scope_id AND {predicates})")
    guard("trg_ofpv2_record_projection", "ofpv2_records", """EXISTS(SELECT 1 FROM ofpv2_scopes s JOIN ofpv2_seals z ON z.artifact_id=s.qualification_id WHERE s.scope_id=NEW.scope_id) OR NOT EXISTS(
      SELECT 1 FROM ofpv2_scopes s JOIN ofpv2_artifacts q ON q.artifact_id=s.qualification_id,json_each(s.profile_json,'$.records') j
      WHERE s.scope_id=NEW.scope_id AND json_extract(j.value,'$.file')=NEW.file AND json_extract(j.value,'$.index')=NEW.position
      AND json_extract(j.value,'$.raw_record_sha256')=NEW.raw_hash AND json_extract(j.value,'$.disposition')=NEW.disposition
      AND json_extract(j.value,'$.reason') IS NEW.reason AND (NEW.disposition='EXCEPTION' OR EXISTS(
        SELECT 1 FROM json_each(q.artifact_json,'$.payload.facts') f WHERE json_extract(f.value,'$.match_id')=NEW.match_id
        AND json_extract(f.value,'$.match_result_id')=NEW.result_id AND json(f.value)=json(NEW.fact_json)
        AND NEW.match_id=json_extract(j.value,'$.candidate_result.match_id'))))""")
    guard("trg_ofpv2_instance_projection", "ofpv2_instances", """NOT EXISTS(SELECT 1 FROM ofpv2_artifacts a WHERE a.artifact_id=NEW.artifact_id
      AND a.kind='INSTANCE' AND a.scope_id=NEW.scope_id AND a.instance_id=NEW.instance_id
      AND json_extract(a.artifact_json,'$.payload.logical_key')=NEW.logical_key)""")
    guard("trg_ofpv2_seal_complete", "ofpv2_seals", """NOT EXISTS(SELECT 1 FROM ofpv2_artifacts a JOIN ofpv2_phases p ON p.artifact_id=a.artifact_id
      WHERE a.artifact_id=NEW.artifact_id AND rb_us_v1(NEW.completed_at_utc)>=rb_us_v1(a.recorded_at_utc)
      AND json_array_length(a.artifact_json,'$.parents')=(SELECT count(*) FROM ofpv2_parents WHERE child_id=a.artifact_id)
      AND (a.kind='QUALIFICATION' OR EXISTS(SELECT 1 FROM ofpv2_instances i WHERE i.instance_id=a.instance_id AND i.scope_id=a.scope_id))
      AND (a.kind<>'QUALIFICATION' OR EXISTS(SELECT 1 FROM ofpv2_scopes s WHERE s.qualification_id=a.artifact_id
        AND json(s.profile_json)=json_extract(a.artifact_json,'$.payload.subject.profile')
        AND s.classification=json_extract(a.artifact_json,'$.payload.subject.classification')
        AND (SELECT count(*) FROM ofpv2_files WHERE scope_id=s.scope_id)=json_array_length(s.profile_json,'$.files')
        AND (SELECT count(*) FROM ofpv2_mappings WHERE scope_id=s.scope_id)=json_array_length(s.profile_json,'$.mapping')
        AND (SELECT count(*) FROM ofpv2_records WHERE scope_id=s.scope_id)=json_array_length(s.profile_json,'$.records')
        AND (SELECT count(*) FROM ofpv2_records WHERE scope_id=s.scope_id AND disposition='FACT_CANDIDATE')=json_array_length(a.artifact_json,'$.payload.facts'))))""")
    guard("trg_ofpv2_pin_projection", "ofpv2_real_model_pins", """EXISTS(SELECT 1 FROM rb_seals WHERE artifact_id=NEW.pin_id) OR NOT EXISTS(
      SELECT 1 FROM rb_artifacts p,ofpv2_artifacts r,ofpv2_artifacts b,ofpv2_scopes s,rb_artifacts program WHERE p.artifact_id=NEW.pin_id
      AND p.schema_version='REAL_MODEL_PIN_V1' AND json_extract(p.artifact_json,'$.model_source.source_type')='OPENFOOTBALL_QUALIFIED_V2'
      AND rb_valid_header_v1(p.artifact_json)=1 AND s.scope_id=NEW.scope_id
      AND json_extract(p.artifact_json,'$.source_identity')=s.source_id
      AND json_extract(p.artifact_json,'$.release_id') IS NULL AND json_extract(p.artifact_json,'$.state') IS NULL
      AND json_extract(p.artifact_json,'$.source_analysis_id') IS NULL AND json_extract(p.artifact_json,'$.model_state_id') IS NULL
      AND program.artifact_id=json_extract(p.artifact_json,'$.program.artifact_id')
      AND program.schema_version='REAL_OBSERVATION_PROGRAM_V1'
      AND EXISTS(SELECT 1 FROM json_each(program.artifact_json,'$.competition_ids') c WHERE c.value=s.competition_id)
      AND json_extract(p.artifact_json,'$.model_source.scope_id')=NEW.scope_id AND json_extract(p.artifact_json,'$.model_source.scope_hash')=NEW.scope_hash
      AND json_extract(p.artifact_json,'$.model_source.instance_id')=NEW.instance_id
      AND json_extract(p.artifact_json,'$.model_source.release_id')=NEW.release_id AND json_extract(p.artifact_json,'$.model_source.release_hash')=NEW.release_hash
      AND json_extract(p.artifact_json,'$.model_source.binding_id')=NEW.binding_id AND json_extract(p.artifact_json,'$.model_source.binding_hash')=NEW.binding_hash
      AND r.artifact_id=NEW.release_id AND r.kind='RELEASE' AND r.artifact_hash=NEW.release_hash
      AND b.artifact_id=NEW.binding_id AND b.kind='STATE_BINDING' AND b.artifact_hash=NEW.binding_hash
      AND r.scope_id=b.scope_id AND r.scope_id=NEW.scope_id AND r.instance_id=b.instance_id AND r.instance_id=NEW.instance_id
      AND json_extract(p.artifact_json,'$.release_hash')=r.artifact_hash
      AND json_extract(p.artifact_json,'$.model_lineage.state_hash')=json_extract(b.artifact_json,'$.payload.state_hash')
      AND json_extract(p.artifact_json,'$.model_lineage.state_hash')=json_extract(r.artifact_json,'$.payload.state.state_hash')
      AND json_extract(p.artifact_json,'$.model_lineage.state_id')=json_extract(b.artifact_json,'$.payload.model_state_id')
      AND json_extract(p.artifact_json,'$.model_lineage.training_data_hash')=json_extract(b.artifact_json,'$.payload.training_data_hash')
      AND json_extract(p.artifact_json,'$.model_lineage.training_data_hash')=json_extract(r.artifact_json,'$.payload.state.training_data_hash')
      AND json_extract(p.artifact_json,'$.model_lineage.config_hash')=json_extract(b.artifact_json,'$.payload.config_hash')
      AND json_extract(p.artifact_json,'$.model_lineage.config_hash')=json_extract(r.artifact_json,'$.payload.state.config_hash')
      AND json_extract(p.artifact_json,'$.authority_hash')=json_extract(b.artifact_json,'$.payload.authority_hash')
      AND json_extract(p.artifact_json,'$.scope_match_ids')=json_extract(b.artifact_json,'$.payload.target_match_ids'))""")
    guard("trg_ofpv2_pin_complete", "rb_seals", """EXISTS(SELECT 1 FROM rb_artifacts a WHERE a.artifact_id=NEW.artifact_id AND a.schema_version='REAL_MODEL_PIN_V1'
      AND ((json_extract(a.artifact_json,'$.model_source.source_type')='OPENFOOTBALL_QUALIFIED_V2' AND NOT EXISTS(SELECT 1 FROM ofpv2_real_model_pins p WHERE p.pin_id=a.artifact_id))
      OR (coalesce(json_extract(a.artifact_json,'$.model_source.source_type'),'LEGACY')<>'OPENFOOTBALL_QUALIFIED_V2' AND EXISTS(SELECT 1 FROM ofpv2_real_model_pins p WHERE p.pin_id=a.artifact_id))))""")
    return values


def install_scoped_triggers(connection):
    if connection.scalar(sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='ofpv2_scopes'")):
        for statement in scoped_triggers().values():
            connection.exec_driver_sql(statement.replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS ", 1))


def assert_scoped_schema(connection):
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    from football_system.infrastructure.database.real_bridge_schema import schema_signature
    expected = {name: schema_signature(str(CreateTable(table).compile(dialect=dialect())).strip())
        for name, table in scoped_tables(sa.MetaData()).items()}
    expected.update({name: schema_signature(sql) for name, sql in scoped_triggers().items()})
    rows = connection.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE name LIKE 'ofpv2_%' OR name LIKE 'trg_ofpv2_%'")).all()
    actual = {name: schema_signature(sql) for name, sql in rows if sql is not None}
    if actual != expected:
        raise ValueError("SCOPED_OFP_SCHEMA_AND_GUARDS_REQUIRED")


def assert_scoped_head(session):
    heads = session.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(heads) != 1 or heads[0] not in SUPPORTED_HEADS:
        raise ValueError("SCOPED_OFP_MIGRATION_HEAD_REQUIRED")
    assert_scoped_schema(session.connection())
