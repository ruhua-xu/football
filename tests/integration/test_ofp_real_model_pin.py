"""Real repository pin creation from verified OFP artifacts, in synthetic storage only."""
from datetime import timedelta
import hashlib
import json
import socket

import pytest
from sqlalchemy import text

from tests.integration import test_openfootball_production_binding as synthetic_data
from tests.integration.ofp_real_pin_fixtures import through_openfootball_binding, qualify_synthetic_source
from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.domain.archive import canonical_json
from football_system.domain.openfootball_production import canonical_id
from football_system.domain.pinned_model_source import OpenFootballModelSourceV1
from football_system.infrastructure.database.real_bridge_repository import SqlAlchemyRealBridgeRepository
from football_system.infrastructure.database.real_bridge_sources import ExistingPinnedModelAccess
from tests.integration.test_openfootball_production_binding import AT, put
from football_system.infrastructure.files.training_evidence import ReviewerAuthorityV1

source_graph = synthetic_data.graph


@pytest.fixture(autouse=True)
def network_denied(monkeypatch):
    def denied(*args,**kwargs):
        raise AssertionError("SYNTHETIC_ONLY_NO_PROVIDER_OR_LLM_HTTP")
    monkeypatch.setattr(socket.socket,"connect",denied)


class BridgeClock:
    basis="SYNTHETIC_TEST_CLOCK"
    def __init__(self,clock):
        self.clock=clock
    def now(self):
        return self.clock()


def create_bridge(g, *, competition=None, admission_source=None):
    g.bridge=SqlAlchemyRealBridgeRepository(g.sessions,evidence_root=g.root,clock=BridgeClock(g.clock),model_access=ExistingPinnedModelAccess(g.inference))
    def call(op,key,**values):
        return g.bridge.execute(op,REQUEST_TYPES[op](request_key=key,**values))
    g.call=call
    g.program=call("program","rb-program",observation_program_id="SYNTHETIC_OFP_PIN_VERTICAL_ONLY",
        competition_ids=(competition or canonical_id("COMPETITION","Deutsche Bundesliga"),),season_id=canonical_id("SEASON","2026/27"),
        scope_reference="SELF_AUTHORED_SYNTHETIC_SOFTWARE_ACCEPTANCE_ONLY",provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE")
    proof=b"SELF_AUTHORED_SYNTHETIC_MODEL_ADMISSION; no real source permission granted"
    (g.root/"rb-model-source.txt").write_bytes(proof)
    g.model_admission=call("admission","rb-model-admission",program_id=g.program.artifact_id,kind="MODEL",source_identity=admission_source or g.source_identity,
        state="ADMITTED",effective_at_utc=g.clock(),expires_at_utc=g.clock()+timedelta(days=10),rights_reference="self-authored isolated software acceptance",
        verified_by="synthetic-operator",evidence_file="rb-model-source.txt",evidence_hash=hashlib.sha256(proof).hexdigest())
    return g


def pin_request(g,**changes):
    values=dict(request_key="rb-ofp-pin",program_id=g.program.artifact_id,admission_id=g.model_admission.artifact_id,
        source_identity=g.source_identity,scope_match_ids=tuple(g.candidate["target_scope"]),
        model_source=OpenFootballModelSourceV1(release_id=g.candidate["release_id"],release_hash=g.candidate["release_hash"],
            binding_id=g.candidate["binding_id"],binding_hash=g.candidate["binding_hash"]))
    values.update(changes)
    return REQUEST_TYPES["model-pin"](**values)


def test_synthetic_full_ofp_to_real_model_pin(source_graph,tmp_path,monkeypatch):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    request=pin_request(g)
    pin=g.bridge.execute("model-pin",request)
    assert pin.schema_version=="REAL_MODEL_PIN_V1" and pin.model_source.source_type=="OPENFOOTBALL"
    assert pin.provenance=="SYNTHETIC_SOFTWARE_ACCEPTANCE" and pin.clock_basis=="SYNTHETIC_TEST_CLOCK"
    assert pin.state is None and pin.release_id is pin.model_state_id is pin.source_analysis_id is None
    assert pin.model_lineage.state_hash==g.candidate["state_hash"]
    assert g.bridge.execute("model-pin",request)==pin
    assert g.bridge.load(pin.artifact_id)==pin
    assert g.bridge.audit(pin.artifact_id)["status"]=="AUDIT_PASS"
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_model_pins"))==1
        assert session.scalar(text("SELECT count(*) FROM ofp_real_model_pins"))==1
        for table in ("production_quant_model_releases","quant_model_states","analysis_runs","quant_model_training_facts","rb_epochs","rb_runs","rb_locks"):
            assert session.scalar(text('SELECT count(*) FROM "'+table+'"'))==0
        assert session.execute(text("PRAGMA integrity_check")).all()==[("ok",)]
        assert session.execute(text("PRAGMA foreign_key_check")).all()==[]
    g.clock.value+=timedelta(days=8)
    assert g.bridge.load(pin.artifact_id)==pin  # A lawful retained pin can be replayed after kickoff; current authority remains mandatory.
    proof=dict(status="PASS",schema_version=pin.schema_version,real_model_pin_created=True,pin_id=pin.artifact_id,pin_hash=pin.content_hash,
        provenance=pin.provenance,source_type=pin.model_source.source_type,ofp_release_id=g.release.artifact_id,ofp_binding_id=g.state_binding.artifact_id,
        legacy_state_or_training_rows_copied=False,program_before_admission_before_pin=True,no_test_state=True,full_qualification_verified=g.full_qualification_verified,
        provider_http_sends=0,llm_api_http_sends=0,production_sql_connections=0,real_production_pins=0,
        pin=pin.model_dump(mode="json"))
    (tmp_path/"synthetic-ofp-real-pin-proof.json").write_text(json.dumps(proof,indent=2)+"\n",encoding="utf-8")
    assert canonical_json(pin)==canonical_json(g.bridge.load(pin.artifact_id))


@pytest.mark.parametrize("fault",[
    "approval_missing","approval_payload_hash","authority_expired","wrong_competition","wrong_source_identity",
    "wrong_model_source_type","missing_parent_link","missing_parent_artifact","binding_release_mismatch",
    "target_training_intersection","missing_program","missing_model_admission","admission_source_mismatch","ofp_artifact_tampered",
])
def test_ofp_pin_refusals_fail_closed(source_graph,monkeypatch,fault):
    g=qualify_synthetic_source(source_graph,monkeypatch)
    if fault=="authority_expired":
        old=g.evidence.load_authority(g.authority)
        value=ReviewerAuthorityV1.model_validate(old.model_dump()|{"expires_at_utc":AT+timedelta(days=1)})
        g.authority=put(g.root,"short-lived-synthetic-authority.json",value)
        g.evidence.trusted_authorities[g.authority.evidence_reference]=g.authority.evidence_sha256
    g=through_openfootball_binding(g)
    g=create_bridge(g,competition="self-authored-other-competition" if fault=="wrong_competition" else None,
        admission_source="self-authored-wrong-source" if fault=="admission_source_mismatch" else None)
    changes={}
    if fault=="authority_expired":
        g.clock.value=AT+timedelta(days=2)
    elif fault=="missing_program":
        changes["program_id"]="synthetic-missing-program"
    elif fault=="missing_model_admission":
        changes["admission_id"]="synthetic-missing-admission"
    elif fault=="wrong_source_identity":
        changes["source_identity"]="synthetic-wrong-source"
    elif fault=="wrong_model_source_type":
        changes.update(model_source={"source_type":"LEGACY"},release_id=g.candidate["release_id"],
            model_state_id=g.candidate["model_state_id"],source_analysis_id=g.candidate["source_analysis_id"])
    elif fault=="target_training_intersection":
        changes["scope_match_ids"]=(g.binding.payload["facts"][0]["match_id"],)
    elif fault in {"missing_parent_artifact","binding_release_mismatch"}:
        source=pin_request(g).model_source.model_dump()
        source["binding_id" if fault=="missing_parent_artifact" else "release_id"]=(
            "synthetic-missing-ofp-artifact" if fault=="missing_parent_artifact" else g.approval.artifact_id)
        changes["model_source"]=source
    elif fault in {"approval_missing","missing_parent_link","approval_payload_hash","ofp_artifact_tampered"}:
        # Corrupt only this in-memory synthetic store to exercise read verification.
        # No FK is dropped/disabled and no REAL_MODEL_PIN row is hand-written.
        with g.engine.begin() as db:
            if fault=="approval_missing":
                db.exec_driver_sql("DROP TRIGGER trg_ofp_phase_slots_delete_immutable")
                db.execute(text("DELETE FROM ofp_phase_slots WHERE phase='APPROVAL'"))
            elif fault=="missing_parent_link":
                db.exec_driver_sql("DROP TRIGGER trg_ofp_parent_links_delete_immutable")
                db.execute(text("DELETE FROM ofp_parent_links WHERE child_id=:child AND parent_id=:parent"),
                    {"child":g.state_binding.artifact_id,"parent":g.release.artifact_id})
            else:
                db.exec_driver_sql("DROP TRIGGER trg_ofp_artifacts_update_immutable")
                identity=g.approval.artifact_id if fault=="approval_payload_hash" else g.state_binding.artifact_id
                field="$.payload.subject.manifest_hash" if fault=="approval_payload_hash" else "$.payload.state_hash"
                db.execute(text("UPDATE ofp_artifacts SET artifact_json=json_set(artifact_json,:field,:value) WHERE artifact_id=:id"),
                    {"field":field,"value":"0"*64,"id":identity})
    with pytest.raises((ValueError,KeyError)):
        g.bridge.execute("model-pin",pin_request(g,**changes))
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_model_pins"))==0
        assert session.scalar(text("SELECT count(*) FROM ofp_real_model_pins"))==0
        assert session.scalar(text("PRAGMA foreign_keys"))==1
        assert session.execute(text("PRAGMA foreign_key_check")).all()==[]


def test_source_type_and_test_state_are_not_escape_hatches(source_graph,monkeypatch):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    from scripts.real_bridge_acceptance import fixed_state
    with pytest.raises(ValueError,match="OFP_SOURCE_CANNOT_USE_LEGACY_IDS_OR_TEST_STATE"):
        pin_request(g,test_state=fixed_state())
    with pytest.raises(ValueError):
        pin_request(g,model_source={"source_type":"UNREVIEWED_OTHER"})
    with pytest.raises(ValueError):
        g.bridge.execute("model-pin",pin_request(g,
            model_source=pin_request(g).model_source.model_dump()|{"release_hash":"0"*64}))


def test_database_seal_rejects_missing_companion_even_if_writer_omits_it(source_graph,monkeypatch):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    original=g.bridge._store
    def missing_companion(session,values,receipt,relations):
        return original(session,values,receipt,tuple(r for r in relations if r[0]!="ofp_real_model_pins"))
    monkeypatch.setattr(g.bridge,"_store",missing_companion)
    with pytest.raises(ValueError):
        g.bridge.execute("model-pin",pin_request(g))
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_model_pins"))==0
        assert session.scalar(text("SELECT count(*) FROM ofp_real_model_pins"))==0
        assert session.scalar(text("PRAGMA foreign_keys"))==1
        assert session.execute(text("PRAGMA foreign_key_check")).all()==[]


def test_current_authority_expiry_blocks_existing_pin_read(source_graph,monkeypatch):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    pin=g.bridge.execute("model-pin",pin_request(g))
    g.clock.value+=timedelta(days=40)
    with pytest.raises(ValueError):
        g.bridge.load(pin.artifact_id)


@pytest.mark.parametrize("field,value",[("release_id","synthetic-missing-parent"),("binding_hash","0"*64)])
def test_database_projection_rejects_corrupted_companion_from_writer(source_graph,monkeypatch,field,value):
    g=create_bridge(through_openfootball_binding(qualify_synthetic_source(source_graph,monkeypatch)))
    original=g.bridge._store
    def corrupt_relation(session,values,receipt,relations):
        corrupted=tuple((table,dict(row,**{field:value})) if table=="ofp_real_model_pins" else (table,row) for table,row in relations)
        return original(session,values,receipt,corrupted)
    monkeypatch.setattr(g.bridge,"_store",corrupt_relation)
    with pytest.raises(ValueError):
        g.bridge.execute("model-pin",pin_request(g))
    with g.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM rb_model_pins"))==0
        assert session.scalar(text("SELECT count(*) FROM ofp_real_model_pins"))==0
        assert session.scalar(text("SELECT count(*) FROM rb_artifacts WHERE schema_version='REAL_MODEL_PIN_V1'"))==0
        assert session.scalar(text("PRAGMA foreign_keys"))==1
