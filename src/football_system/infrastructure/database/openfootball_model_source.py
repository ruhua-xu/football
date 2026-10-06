"""Authorized read-only adapter from the existing OFP graph to a typed model descriptor.

Current rights/authority are rechecked even during historical RealBridge replay.
Historical input proof is checked at its sealed binding/pin event, not relabelled
as fresh current odds. This module creates no state, approval, source or pin.
"""
from contextlib import nullcontext
from sqlalchemy import select

from football_system.application.openfootball_production import facts_root, parse_utc
from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id
from football_system.domain.market_analysis import MarketModelLineageV1
from football_system.domain.openfootball_production import (
    CONFIG_HASH, SOURCE_ID, OpenFootballModelApprovalPayloadV1, OpenFootballProductionTargetV1,
    canonical_id, fixed_exceptions, fixed_window, require, validate_fact_cohort,
)
from football_system.domain.pinned_model_source import OpenFootballModelSourceV1, VerifiedPinnedModelDescriptorV1
from football_system.domain.services.elo_baseline import EloBaselineConfig, EloBaselineState
from football_system.domain.training_admission import tagged_canonical_sha256
from football_system.infrastructure.database.models import MatchRecord, CanonicalMatchIdentityRecord, MatchResultRecord


class OpenFootballPinnedModelReader:
    def __init__(self, production):
        self.repo = production.openfootball_binding()

    def read(self, session, request, at, *, existing_pin=False):
        source = OpenFootballModelSourceV1.model_validate(request.model_source)
        require(request.source_identity == SOURCE_ID, "OFP_MODEL_SOURCE_IDENTITY_MISMATCH")
        repo = self.repo
        require(session.get_bind() is repo._sessions.kw["bind"], "OFP_MODEL_TRANSACTION_DATABASE_MISMATCH")
        # Share the caller's transaction/snapshot and pending pin graph. Opening
        # another BEGIN also breaks in-memory SQLite and can introduce TOCTOU.
        with nullcontext(session) as session:
            data, facts = repo._data(session)
            kinds = ("DATA_COMPLETION", "CUTOFF", "PILOT_PLAN", "PILOT_RESERVATION", "PILOT_REPORT", "PILOT_ATTESTATION",
                "MANIFEST", "APPROVAL", "RELEASE", "TARGET_PLAN", "STATE_BINDING")
            artifacts = {kind: repo._phase(session, kind) for kind in kinds}
            completion, cutoff_art, pilot, reservation, report, attest, manifest, approval, release, plan, binding = (artifacts[k] for k in kinds)
            def parents(child, *values):
                require(child.parents == tuple(sorted(a.reference() for a in values)), "OFP_MODEL_PARENT_LINEAGE_MISMATCH")
            parents(data)
            parents(completion, data)
            parents(cutoff_art, data, completion)
            parents(pilot, data, cutoff_art)
            parents(reservation, pilot)
            parents(report, pilot, reservation)
            parents(attest, pilot, report)
            parents(manifest, data, pilot, report, attest)
            parents(approval, data, manifest)
            parents(release, approval, manifest, report)
            parents(plan, release, pilot)
            parents(binding, release, plan, approval)
            require((source.release_id, source.release_hash) == release.reference()
                and (source.binding_id, source.binding_hash) == binding.reference(), "OFP_REQUESTED_SOURCE_REFERENCE_MISMATCH")
            require(repo._phase(session, "PILOT_FAILURE", False) is None, "OFP_FAILED_PILOT_NOT_PINNABLE")
            cutoff = parse_utc(cutoff_art.payload["training_cutoff_at_utc"])
            p, m, r, b = pilot.payload, manifest.payload, release.payload, binding.payload
            require(p["schema_version"] == "OPENFOOTBALL_OBSERVED_QUANT_INTEGRITY_PLAN_V1"
                and m["schema_version"] == "OPENFOOTBALL_TRAINING_HISTORY_MANIFEST_V1"
                and r["schema_version"] == "OPENFOOTBALL_PRODUCTION_MODEL_RELEASE_V1"
                and plan.payload["schema_version"] == "OPENFOOTBALL_PRODUCTION_TARGET_ACCEPTANCE_PLAN_V1"
                and b["schema_version"] == "OPENFOOTBALL_PRODUCTION_INFERENCE_STATE_BINDING_V1"
                and p["model_name"] == r["model_name"] == "ELO_THREE_WAY_BASELINE_V1"
                and p["model_version"] == r["model_version"] == "1" and r["config_hash"] == CONFIG_HASH,
                "OFP_MODEL_SOURCE_TYPE_MISMATCH")
            require(cutoff_art.payload["clock_basis"] == "REPOSITORY_TRUSTED_UTC" and completion.recorded_at_utc < cutoff
                and p["training_cutoff_at_utc"] == m["training_cutoff_at_utc"] == cutoff_art.payload["training_cutoff_at_utc"], "OFP_MODEL_CUTOFF_MISMATCH")
            targets = tuple(OpenFootballProductionTargetV1.model_validate(t) for t in p["targets"])
            target_ids = tuple(t.match_id for t in targets)
            require(target_ids and target_ids == tuple(sorted(set(target_ids)))
                and tuple(request.scope_match_ids) == target_ids == tuple(p["exclude_match_ids"]) == tuple(m["exclude_match_ids"]), "OFP_MODEL_TARGET_SCOPE_MISMATCH")
            require(tuple(p["binding"]) == data.reference() and p["facts_root"] == m["facts_root"] == facts_root(facts)
                and p["config_hash"] == m["config_hash"] == CONFIG_HASH
                and p["training_window_hash"] == m["training_window_hash"] == fixed_window().content_hash
                and canonical_json(p["training_window"]) == canonical_json(fixed_window())
                and canonical_json(m["training_window"]) == canonical_json(fixed_window())
                and canonical_json(p["exceptions"]) == canonical_json(fixed_exceptions()), "OFP_MODEL_SOURCE_SCOPE_MISMATCH")
            require((m["source_record_count"], m["exception_count"], m["fact_count"]) == (612,13,599)
                and tuple(m["source_binding"]) == data.reference() and m["canonical_mapping_root"] == data.payload["subject"]["mapping_root"], "OFP_MODEL_MANIFEST_SOURCE_MISMATCH")
            validate_fact_cohort(facts, window=fixed_window(), exclusions=target_ids, cutoff=cutoff)
            repo._verify_report(report, facts, pilot, cutoff)
            require(reservation.payload["attempt_sequence"] == attest.payload["attempt_count"] == 1
                and tuple(reservation.payload["plan"]) == pilot.reference()
                and tuple(report.payload["reservation"]) == reservation.reference()
                and tuple(attest.payload["report"]) == report.reference() and tuple(attest.payload["plan"]) == pilot.reference(), "OFP_MODEL_PILOT_LINEAGE_MISMATCH")
            require(tuple(m["pilot_plan"]) == pilot.reference() and tuple(m["pilot_report"]) == report.reference()
                and tuple(m["attestation"]) == attest.reference(), "OFP_MODEL_MANIFEST_LINEAGE_MISMATCH")
            approved = repo._model_authorization(approval, repo._now())
            approved = OpenFootballModelApprovalPayloadV1.model_validate(approved)
            require((approved.manifest_id, approved.manifest_hash) == manifest.reference()
                and (approved.source_binding_id, approved.source_binding_hash) == data.reference()
                and (approved.pilot_report_id, approved.pilot_report_hash) == report.reference()
                and (approved.attestation_id, approved.attestation_hash) == attest.reference()
                and approved.canonical_mapping_root == m["canonical_mapping_root"] and approved.training_cutoff_at_utc == cutoff,
                "OFP_MODEL_APPROVAL_SCOPE_MISMATCH")
            require(p["parameter_policy"] == "NO_PARAMETER_TUNING" and p["selection_policy"] == "NO_ROI_MODEL_SELECTION"
                and p["implementation_revision"] == report.payload["implementation_revision"] == m["implementation_revision"]
                == r["implementation_revision"] == approved.implementation_revision, "OFP_MODEL_APPROVED_IMPLEMENTATION_MISMATCH")
            state = EloBaselineState.model_validate(r["state"])
            require(state.config_hash == CONFIG_HASH and state.state_hash == approved.state_hash == r["state_hash"]
                == m["state_hash"] == report.payload["state_hash"] == attest.payload["state_hash"]
                and state.training_data_hash == approved.training_data_hash == r["training_data_hash"]
                == m["training_data_hash"] == report.payload["training_data_hash"] == attest.payload["training_data_hash"]
                and canonical_json(state) == canonical_json(report.payload["state"]), "OFP_MODEL_RELEASE_STATE_MISMATCH")
            require(tuple(r["approval"]) == approval.reference() and tuple(r["manifest"]) == manifest.reference()
                and parse_utc(r["training_cutoff_at_utc"]) == cutoff
                and approval.recorded_at_utc <= parse_utc(r["build_started_at_utc"]) <= parse_utc(r["build_completed_at_utc"]) <= release.recorded_at_utc,
                "OFP_MODEL_RELEASE_LINEAGE_MISMATCH")
            require(tuple(plan.payload["release"]) == release.reference() and canonical_json(plan.payload["targets"]) == canonical_json(p["targets"])
                and tuple(plan.payload["exclude_match_ids"]) == target_ids, "OFP_MODEL_PLAN_SCOPE_MISMATCH")
            repo._verify_state_binding(binding, release, plan)
            require(not set(target_ids) & set(state.training_match_ids), "MODEL_SCOPE_OR_TRAINING_INTERSECTION")
            comp, season = canonical_id("COMPETITION", "Deutsche Bundesliga"), canonical_id("SEASON", "2026/27")
            proof_at = request.event_at_utc if existing_pin else at
            require(state.season_id == season and release.recorded_at_utc <= plan.recorded_at_utc <= binding.recorded_at_utc <= proof_at <= at,
                "OFP_MODEL_SOURCE_FROM_FUTURE")
            for target in targets:
                match, identity = session.get(MatchRecord,target.match_id), session.get(CanonicalMatchIdentityRecord,target.match_id)
                require(target.competition_id == comp and target.season_id == season and target.kickoff_at_utc > proof_at
                    and match and identity and (match.competition_id,identity.season,match.home_team_id,match.away_team_id,match.kickoff_at_utc)
                    == (comp,season,target.home_team_id,target.away_team_id,target.kickoff_at_utc), "OFP_MODEL_CANONICAL_SCOPE_MISMATCH")
                require(not session.scalar(select(MatchResultRecord.match_result_id).where(MatchResultRecord.internal_match_id==target.match_id,
                    MatchResultRecord.ingested_at_utc<=proof_at).limit(1)), "OFP_MODEL_TARGET_RESULT_KNOWN_AT_PIN")
            # Immutable original input proof; a later prediction has its own
            # RealBridge current fixture/market/SP freshness guards.
            require(canonical_json(repo._live_target_proof(session,targets,proof_at)) == canonical_json(plan.payload["live_inputs"]), "OFP_MODEL_LIVE_PROOF_MISMATCH")
            authority_hash = tagged_canonical_sha256("OPENFOOTBALL_CURRENT_AUTHORITY_V1",dict(approval=approval.reference(),
                authority=approval.payload["authority"],review=approval.payload["review"],source_binding=data.reference()))
            require(b["authority_hash"] == authority_hash, "OFP_MODEL_AUTHORITY_HASH_MISMATCH")
            require(b["model_state_id"] == stable_id("OPENFOOTBALL_PRODUCTION_STATE_BINDING_V1", b["source_analysis_id"], state.state_hash),
                "OFP_MODEL_STATE_ID_MISMATCH")
            repo._current_review(data,repo._now())
            repo._model_authorization(approval,repo._now())
            return VerifiedPinnedModelDescriptorV1(source=source,state=state,configuration=EloBaselineConfig(),
                lineage=MarketModelLineageV1(model_name=state.model_name,model_version=state.model_version,state_id=b["model_state_id"],
                    state_hash=state.state_hash,config_hash=state.config_hash,training_data_hash=state.training_data_hash,
                    training_cutoff_at_utc=state.cutoff_at_utc,generated_at_utc=binding.recorded_at_utc),
                release_hash=release.artifact_hash,authority_hash=authority_hash,scope_match_ids=target_ids,competition_id=comp,season_id=season)
