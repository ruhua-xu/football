"""Read/verify existing sealed live inputs and already pinned state; no source fetch or model builder."""

import hashlib

from sqlalchemy import select, text

from football_system.application.live_sources import LiveSourceKind
from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id
from football_system.domain.market_analysis import MarketMatchIdentityV1, MarketModelLineageV1
from football_system.domain.market_v2 import MarketPriceV1, MarketProbabilityDistributionV1, fixed_decimal
from football_system.domain.real_bridge import RealFixtureRefV1, require
from football_system.domain.services.elo_baseline import EloBaselineState, EloBaselineConfig
from football_system.domain.services.probability import normalized_inverse_probability
from football_system.infrastructure.database.live_source_repositories import (
    SqlAlchemyLiveSourceRepository, _exact_consensus_snapshot, _exact_sporttery_snapshot,
    _market_capture_from_record, _sporttery_capture_from_record, _mapping_from_record, _verify_market_consensus,
    _market_snapshot_from_record, _market_snapshot_matches_database_projection,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord, CompetitionRecord, MatchRecord, TeamRecord, MarketOddsSnapshotRecord,
    SportteryBonusSnapshotRecord, LiveSourceIngestionRecord, QuantModelStateRecord,
    FixtureObservationRecord, FixtureIngestionCaptureRecord, ProviderMatchMappingRecord,
)
from football_system.infrastructure.files.return_distribution import strict_return_json


def verify_capture_projection(session,capture):
    """Check exact original capture members and source mappings, not merely counts."""
    from football_system.application.live_sources import MarketOddsIngestionCapture
    market=isinstance(capture,MarketOddsIngestionCapture)
    artifacts=(capture.artifact,) if market else capture.artifacts
    record=session.get(LiveSourceIngestionRecord,capture.ingestion_id)
    source=capture.source_batch if market else capture.batch
    require(record.schema_version==capture.schema_version and record.provider_id==stable_id("provider",capture.provider_code)
            and record.data_mode=="LIVE_STRICT" and record.identity_cutoff_at_utc==capture.identity_cutoff_at_utc
            and record.source_ingested_at_utc==capture.ingested_at_utc and record.persisted_at_utc>=capture.ingested_at_utc
            and record.requested_match_ids_json==canonical_json(capture.requested_match_ids if market else ())
            and record.artifact_count==len(artifacts) and record.snapshot_count==len(source.snapshots)
            and record.mapping_count==len(source.mappings) and record.issue_count==len(capture.issues)
            and record.consensus_count==(len(capture.consensus_batch.snapshots) if market else 0),"SOURCE_CAPTURE_HEADER_OR_TIME_MISMATCH")
    actual=session.execute(text("SELECT artifact_id,artifact_no,role,payload_sha256,source_path,captured_at_utc,available_at_utc FROM live_source_ingestion_artifacts WHERE ingestion_id=:id ORDER BY artifact_no"),{"id":capture.ingestion_id}).mappings().all()
    require(len(actual)==len(artifacts),"SOURCE_ARTIFACT_COVERAGE_MISMATCH")
    from football_system.domain.common import normalize_utc
    from datetime import datetime
    for i,(row,item) in enumerate(zip(actual,artifacts,strict=True)):
        require((row["artifact_id"],row["artifact_no"],row["role"],row["payload_sha256"],row["source_path"])==
                (item.artifact_id,i,item.role.value,item.payload_sha256,item.source_path),"SOURCE_ARTIFACT_PROJECTION_MISMATCH")
        for key in ("captured_at_utc","available_at_utc"):
            # Legacy SQLite UTCDateTime stores naive UTC values.
            from datetime import timezone
            at=datetime.fromisoformat(row[key]).replace(tzinfo=timezone.utc)
            require(normalize_utc(at)==getattr(item,key),"SOURCE_ARTIFACT_TIME_MISMATCH")
    for role,items in (("SOURCE",source.mappings),("CONSENSUS",capture.consensus_batch.mappings if market else ())):
        rows=session.execute(text("SELECT mapping_id,mapping_no FROM live_source_ingestion_mappings WHERE ingestion_id=:id AND mapping_role=:role ORDER BY mapping_no"),dict(id=capture.ingestion_id,role=role)).all()
        require(rows==[(item.mapping_id,i) for i,item in enumerate(items)],"SOURCE_MAPPING_MEMBERSHIP_MISMATCH")
        for item in items:
            record=session.get(ProviderMatchMappingRecord,item.mapping_id)
            require(record is not None and _mapping_from_record(session,record)==item,"SOURCE_MAPPING_PROJECTION_MISMATCH")
    if market:
        _verify_market_consensus(capture)
        for role,items in (("SOURCE",source.snapshots),("CONSENSUS",capture.consensus_batch.snapshots)):
            rows=session.execute(text("SELECT snapshot_id,snapshot_no FROM live_source_ingestion_market_snapshots WHERE ingestion_id=:id AND snapshot_role=:role ORDER BY snapshot_no"),dict(id=capture.ingestion_id,role=role)).all()
            require(rows==[(item.snapshot_id,i) for i,item in enumerate(items)],"SOURCE_SNAPSHOT_MEMBERSHIP_MISMATCH")
            for item in items:
                record=session.get(MarketOddsSnapshotRecord,item.snapshot_id)
                require(record is not None and _market_snapshot_matches_database_projection(_market_snapshot_from_record(session,record),item),"SOURCE_QUOTE_PROJECTION_MISMATCH")


def match_identity(session, match_id, at=None):
    match = session.get(MatchRecord, match_id)
    require(match is not None, "CANONICAL_MATCH_REQUIRED")
    canonical = session.get(CanonicalMatchIdentityRecord, match_id)
    require(canonical is not None, "CANONICAL_SEASON_REQUIRED")
    home, away = session.get(TeamRecord, match.home_team_id), session.get(TeamRecord, match.away_team_id)
    require(session.get(CompetitionRecord, match.competition_id) and home and away, "CANONICAL_IDENTITY_INCOMPLETE")
    kickoff = match.kickoff_at_utc
    if at is not None:
        observation = fixture_at(session, match_id, at)
        kickoff = observation.kickoff_at_utc
        require(max(match.available_at_utc,match.created_at_utc,canonical.available_at_utc) <= at, "FUTURE_CANONICAL_IDENTITY")
    return MarketMatchIdentityV1(match_id=match_id, competition_id=match.competition_id, season_id=canonical.season,
        home_team_id=home.team_id, away_team_id=away.team_id, home_team_name=home.name, away_team_name=away.name,
        kickoff_at_utc=kickoff)


def fixture_at(session, match_id, at):
    """Historical append-only observation selection, also bounded by receipt sequence."""
    limit = session.info.get("rb_source_limits", {}).get("fixture_ingestion_captures",9223372036854775807)
    observation = session.scalar(select(FixtureObservationRecord).join(FixtureIngestionCaptureRecord).where(
        FixtureObservationRecord.internal_match_id==match_id, FixtureObservationRecord.available_at_utc<=at,
        FixtureIngestionCaptureRecord.ingested_at_utc<=at, text("fixture_ingestion_captures.rowid<=:rb_limit")
    ).params(rb_limit=limit).order_by(FixtureIngestionCaptureRecord.ingested_at_utc.desc(),
        FixtureObservationRecord.available_at_utc.desc(),FixtureObservationRecord.observation_id.desc()).limit(1))
    require(observation is not None, "VISIBLE_FIXTURE_OBSERVATION_REQUIRED")
    mapping = session.get(ProviderMatchMappingRecord,observation.provider_mapping_id)
    capture = session.get(FixtureIngestionCaptureRecord,observation.ingestion_id)
    require(mapping is not None and mapping.internal_match_id==match_id and mapping.provider_id==capture.provider_id,
            "FIXTURE_OBSERVATION_MAPPING_MISMATCH")
    return observation


def fixture_ref(session, match_id, at):
    observation = fixture_at(session, match_id, at)
    return RealFixtureRefV1(match_id=match_id,observation_id=observation.observation_id,
        ingestion_id=observation.ingestion_id,payload_hash=observation.payload_sha256)


@fixed_decimal(28)
def market_values(session, sessions, ingestion_id, snapshot_id):
    record = session.get(LiveSourceIngestionRecord, ingestion_id)
    require(record is not None, "LIVE_MARKET_INGESTION_REQUIRED")
    capture = _market_capture_from_record(session, ingestion_id)
    require(capture.provider_code == "THE_ODDS_API", "THE_ODDS_API_CURRENT_H2H_ONLY")
    require(capture.request_audit.request_parameters.get("markets")=="h2h" and "/historical/" not in capture.request_audit.endpoint,"CURRENT_H2H_REQUEST_REQUIRED")
    SqlAlchemyLiveSourceRepository(sessions)._verify_stored_ingestion(session, record, capture, LiveSourceKind.MARKET_ODDS)
    verify_capture_projection(session,capture)
    row = session.get(MarketOddsSnapshotRecord, snapshot_id)
    require(row is not None, "MARKET_CONSENSUS_SNAPSHOT_REQUIRED")
    snapshot = _exact_consensus_snapshot(session, ingestion_id, row)
    require(snapshot.market.market_type.value == "THREE_WAY" and snapshot.provider_code == "MARKET_CONSENSUS_MEDIAN_V1", "MEDIAN_V1_THREE_WAY_REQUIRED")
    lineage = next((line for line in capture.consensus_lineages if line.source_snapshot_key == snapshot.source_snapshot_key), None)
    require(lineage is not None, "COMPLETE_CONSENSUS_LINEAGE_REQUIRED")
    p, _ = normalized_inverse_probability(snapshot.three_way_odds())
    return dict(match_id=snapshot.match_id, ingestion_id=ingestion_id, snapshot_id=snapshot_id,
        capture_hash=record.capture_hash, payload_hash=snapshot.payload_hash,
        constituent_refs=tuple(sorted((c.snapshot_id, c.payload_hash) for c in lineage.constituents)),
        probabilities=MarketProbabilityDistributionV1.from_three_way(p), captured_at_utc=snapshot.captured_at_utc, available_at_utc=snapshot.available_at_utc,
        ingested_at_utc=max(snapshot.ingested_at_utc, record.persisted_at_utc))


@fixed_decimal(28)
def sp_values(session, sessions, ingestion_id, snapshot_id):
    record = session.get(LiveSourceIngestionRecord, ingestion_id)
    require(record is not None, "LIVE_SP_INGESTION_REQUIRED")
    capture = _sporttery_capture_from_record(session, ingestion_id)
    require(capture.provider_code == "SPORTTERY_MANUAL" and all(p.schema_version == "SPORTTERY_MANUAL_ARCHIVE_V2" for p in capture.provenance), "SPORTTERY_MANUAL_V2_ONLY")
    SqlAlchemyLiveSourceRepository(sessions)._verify_stored_ingestion(session, record, capture, LiveSourceKind.SPORTTERRY)
    verify_capture_projection(session,capture)
    row = session.get(SportteryBonusSnapshotRecord, snapshot_id)
    require(row is not None, "LIVE_SP_SNAPSHOT_REQUIRED")
    snapshot = _exact_sporttery_snapshot(session, ingestion_id, row)
    require(snapshot.market.market_type.value == "THREE_WAY", "REAL_THREE_WAY_ONLY")
    return dict(match_id=snapshot.match_id, ingestion_id=ingestion_id, snapshot_id=snapshot_id,
        capture_hash=record.capture_hash, payload_hash=snapshot.payload_hash,
        prices=tuple(MarketPriceV1(outcome=q.selection.value, price=q.fixed_bonus) for q in snapshot.quotes),
        sale_status=snapshot.sale_status.value, captured_at_utc=snapshot.captured_at_utc,
        available_at_utc=snapshot.available_at_utc, ingested_at_utc=max(snapshot.ingested_at_utc, record.persisted_at_utc))


def stored_model(session, state_id, analysis_id, release_id):
    row = session.get(QuantModelStateRecord, state_id)
    require(row is not None and row.analysis_run_id == analysis_id, "PINNED_MODEL_STATE_REQUIRED")
    require(hashlib.sha256(row.state_json.encode()).hexdigest() == row.state_payload_hash
            and hashlib.sha256(row.config_json.encode()).hexdigest() == row.config_hash, "CORRUPT_PINNED_MODEL_STATE")
    state = EloBaselineState.model_validate(strict_return_json(row.state_json.encode()))
    require(state.state_hash == row.state_hash and state.training_data_hash == row.training_data_hash and state.config_hash == row.config_hash, "MODEL_STATE_HASH_MISMATCH")
    bindings = session.execute(text("SELECT binding_json FROM quant_model_state_production_releases WHERE quant_model_state_id=:state AND analysis_run_id=:analysis AND release_id=:release"),
        dict(state=state_id, analysis=analysis_id, release=release_id)).scalars().all()
    require(len(bindings) == 1, "PINNED_PRODUCTION_BINDING_REQUIRED")
    release = session.execute(text("SELECT artifact_json FROM production_quant_model_releases WHERE release_id=:id"), {"id": release_id}).scalar_one()
    release_data = strict_return_json(release.encode(), limit=64*1024*1024)
    binding = strict_return_json(bindings[0].encode(), limit=64*1024*1024)
    return state, release_data["content_hash"], hashlib.sha256(canonical_json(binding).encode()).hexdigest(), row.generated_at_utc


def model_metadata(session,state_id,analysis_id,release_id):
    """Non-state pin metadata only: this SELECT never reads state_json/training facts."""
    row=session.execute(select(QuantModelStateRecord.model_name,QuantModelStateRecord.model_version,QuantModelStateRecord.state_hash,
        QuantModelStateRecord.config_json,QuantModelStateRecord.config_hash,QuantModelStateRecord.training_data_hash,
        QuantModelStateRecord.cutoff_at_utc,QuantModelStateRecord.generated_at_utc).where(
            QuantModelStateRecord.quant_model_state_id==state_id,QuantModelStateRecord.analysis_run_id==analysis_id)).mappings().one()
    configuration=EloBaselineConfig.model_validate(strict_return_json(row["config_json"].encode()))
    require(configuration.config_hash==row["config_hash"],"MODEL_CONFIG_HEADER_MISMATCH")
    lineage=MarketModelLineageV1(model_name=row["model_name"],model_version=row["model_version"],state_id=state_id,
        state_hash=row["state_hash"],config_hash=row["config_hash"],training_data_hash=row["training_data_hash"],
        training_cutoff_at_utc=row["cutoff_at_utc"],generated_at_utc=row["generated_at_utc"])
    binding=session.scalar(text("SELECT binding_json FROM quant_model_state_production_releases WHERE quant_model_state_id=:state AND analysis_run_id=:analysis AND release_id=:release"),
        dict(state=state_id,analysis=analysis_id,release=release_id))
    require(binding is not None,"PINNED_PRODUCTION_BINDING_REQUIRED")
    release_hash=session.scalar(text("SELECT release_hash FROM production_quant_model_releases WHERE release_id=:id"),{"id":release_id})
    require(release_hash is not None,"PINNED_MODEL_RELEASE_REQUIRED")
    return lineage,configuration,release_hash,hashlib.sha256(canonical_json(strict_return_json(binding.encode(),limit=64*1024*1024)).encode()).hexdigest()


class ExistingPinnedModelAccess:
    """Uses the existing approved-release read/authorization boundary. Never invokes a release/state writer.

    Existing integrity readers may mechanically replay their *already sealed*
    historical proof. No new training provider, fit request, state or parameter
    selection is created. Actual new forecasts use predict_from_state only.
    """
    def __init__(self, inference):
        from football_system.infrastructure.database.production_inference_repository import SqlAlchemyProductionInferenceRepository
        require(type(inference) is SqlAlchemyProductionInferenceRepository, "EXISTING_PRODUCTION_AUTHORITY_REQUIRED")
        self.inference = inference

    def verify(self, session, request, at):
        binding = self.inference.load_binding(request.source_analysis_id)
        require(binding is not None and binding.release.artifact_id == request.release_id
                and binding.quant_model_state_id == request.model_state_id, "MODEL_BINDING_SCOPE_MISMATCH")
        state, release_hash, authority_hash, generated = stored_model(session, request.model_state_id, request.source_analysis_id, request.release_id)
        require(generated <= at, "MODEL_STATE_FROM_FUTURE")
        # Exact target set is supplied by the already approved binding, not by a new fit.
        plan = self.inference.load_target_plan(binding.target_acceptance_plan.artifact_id)
        require(set(request.scope_match_ids) <= {t.match_id for t in plan.content_payload.targets}, "MODEL_TARGET_NOT_PINNED")
        return state, release_hash, authority_hash
