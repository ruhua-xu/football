from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from football_system.domain.common import stable_id
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
    ThreeWayProbability,
)
from football_system.domain.match import (
    FixedBonusQuote,
    SaleStatus,
    SportteryBonusSnapshot,
)
from football_system.domain.post_review import (
    FusionMatchResult,
    FusionRun,
    FusionSource,
    PortfolioRevision,
    PortfolioRevisionSource,
)
from football_system.domain.prediction import FinalPrediction, FusionPolicyName
from football_system.domain.review import (
    AnalysisPacket,
    AnalysisPacketContract,
    AnalysisPacketV2,
    LLMReviewArtifact,
    LLMReviewSubmission,
    LLMReviewSubmissionV2,
    StoredAnalysisPacket,
)
from football_system.infrastructure.database.models import (
    AnalysisPacketRecord,
    AnalysisRunMatchRecord,
    AnalysisRunRecord,
    FinalPredictionOutcomeRecord,
    FinalPredictionRecord,
    FusionRunRecord,
    FusionRunResultRecord,
    LLMReviewArtifactRecord,
    PortfolioRecord,
    PortfolioRevisionRecord,
    ProviderRecord,
    SportteryBonusQuoteRecord,
    SportteryBonusSnapshotRecord,
)


class SqlAlchemyPostReviewRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_fusion_source(self, review_artifact_id: str) -> FusionSource:
        with self._session_factory() as session:
            artifact_record = session.get(LLMReviewArtifactRecord, review_artifact_id)
            if artifact_record is None:
                raise KeyError(f"unknown LLMReviewArtifact: {review_artifact_id}")
            run = _completed_run(session, artifact_record.parent_analysis_run_id)
            packet_record = session.get(AnalysisPacketRecord, artifact_record.packet_id)
            if packet_record is None:
                raise ValueError(
                    "stored LLM review artifact is missing its AnalysisPacket"
                )
            packet, packet_contract = _stored_packet(packet_record, run)
            artifact = _review_artifact(
                artifact_record,
                packet_contract,
            )
            records = tuple(
                session.scalars(
                    select(FinalPredictionRecord)
                    .where(
                        FinalPredictionRecord.analysis_run_id
                        == artifact.parent_analysis_run_id
                    )
                    .order_by(
                        FinalPredictionRecord.internal_match_id,
                        FinalPredictionRecord.market_key,
                    )
                )
            )
            predictions = tuple(
                _final_prediction(session, record) for record in records
            )
            return FusionSource(
                artifact=artifact,
                packet=packet,
                base_predictions=predictions,
            )

    def find_fusion_run(self, fusion_run_id: str) -> FusionRun | None:
        with self._session_factory() as session:
            record = session.get(FusionRunRecord, fusion_run_id)
            return _fusion_run(session, record) if record is not None else None

    def save_fusion_run(self, fusion_run: FusionRun) -> FusionRun:
        _validate_fusion_for_save(fusion_run)
        try:
            with self._session_factory.begin() as session:
                existing = _find_fusion_record(session, fusion_run)
                if existing is not None:
                    return _matching_fusion_run(session, existing, fusion_run)
                record = FusionRunRecord(
                    fusion_run_id=fusion_run.fusion_run_id,
                    parent_analysis_run_id=fusion_run.parent_analysis_run_id,
                    llm_review_artifact_id=fusion_run.llm_review_artifact_id,
                    fusion_policy=fusion_run.fusion_policy,
                    fusion_version=fusion_run.fusion_version,
                    config_json=fusion_run.config_json,
                    config_hash=fusion_run.config_hash,
                    created_at_utc=fusion_run.created_at_utc,
                )
                session.add(record)
                session.flush()
                session.add_all(
                    _fusion_result_record(result) for result in fusion_run.results
                )
                session.flush()
                return _matching_fusion_run(session, record, fusion_run)
        except IntegrityError:
            with self._session_factory() as session:
                existing = _find_fusion_record(session, fusion_run)
                if existing is not None:
                    return _matching_fusion_run(session, existing, fusion_run)
            raise

    def load_portfolio_revision_source(
        self,
        fusion_run_id: str,
    ) -> PortfolioRevisionSource:
        with self._session_factory() as session:
            record = session.get(FusionRunRecord, fusion_run_id)
            if record is None:
                raise KeyError(f"unknown FusionRun: {fusion_run_id}")
            fusion_run = _fusion_run(session, record)
            snapshots = []
            for result in sorted(fusion_run.results, key=lambda item: item.match_id):
                context = session.get(
                    AnalysisRunMatchRecord,
                    (fusion_run.parent_analysis_run_id, result.match_id),
                )
                if context is None:
                    raise ValueError(
                        "FusionRun match is missing its sealed source context"
                    )
                snapshot_record = session.get(
                    SportteryBonusSnapshotRecord,
                    context.sporttery_bonus_snapshot_id,
                )
                if snapshot_record is None:
                    raise ValueError(
                        "FusionRun match is missing its Sporttery snapshot"
                    )
                snapshot = _sporttery_snapshot(session, snapshot_record)
                if (
                    snapshot.match_id != result.match_id
                    or snapshot.market != result.market
                ):
                    raise ValueError(
                        "FusionRun and Sporttery snapshot lineage is inconsistent"
                    )
                snapshots.append(snapshot)
            budgets = tuple(
                session.scalars(
                    select(PortfolioRecord.budget_fen)
                    .where(
                        PortfolioRecord.analysis_run_id
                        == fusion_run.parent_analysis_run_id
                    )
                    .order_by(PortfolioRecord.budget_fen)
                )
            )
            return PortfolioRevisionSource(
                fusion_run=fusion_run,
                sporttery_bonus_snapshots=tuple(snapshots),
                budgets_fen=budgets,
            )

    def find_portfolio_revision(
        self,
        portfolio_revision_id: str,
    ) -> PortfolioRevision | None:
        with self._session_factory() as session:
            record = session.get(PortfolioRevisionRecord, portfolio_revision_id)
            return _portfolio_revision(session, record) if record is not None else None

    def save_portfolio_revision(
        self,
        revision: PortfolioRevision,
    ) -> PortfolioRevision:
        _validate_revision_for_save(revision)
        try:
            with self._session_factory.begin() as session:
                existing = _find_revision_record(session, revision)
                if existing is not None:
                    return _matching_revision(session, existing, revision)
                record = PortfolioRevisionRecord(
                    portfolio_revision_id=revision.portfolio_revision_id,
                    parent_analysis_run_id=revision.parent_analysis_run_id,
                    fusion_run_id=revision.fusion_run_id,
                    revision_policy=revision.revision_policy,
                    revision_version=revision.revision_version,
                    generated_at_utc=revision.generated_at_utc,
                    config_json=revision.config_json,
                    config_hash=revision.config_hash,
                    revision_json=_canonical_json(revision),
                    revision_hash=revision.revision_hash,
                )
                session.add(record)
                session.flush()
                return _matching_revision(session, record, revision)
        except IntegrityError:
            with self._session_factory() as session:
                existing = _find_revision_record(session, revision)
                if existing is not None:
                    return _matching_revision(session, existing, revision)
            raise


def _find_fusion_record(
    session: Session,
    fusion_run: FusionRun,
) -> FusionRunRecord | None:
    existing = session.get(FusionRunRecord, fusion_run.fusion_run_id)
    if existing is not None:
        return existing
    return session.scalar(
        select(FusionRunRecord).where(
            FusionRunRecord.parent_analysis_run_id == fusion_run.parent_analysis_run_id,
            FusionRunRecord.llm_review_artifact_id == fusion_run.llm_review_artifact_id,
            FusionRunRecord.fusion_policy == fusion_run.fusion_policy,
            FusionRunRecord.fusion_version == fusion_run.fusion_version,
            FusionRunRecord.config_hash == fusion_run.config_hash,
        )
    )


def _find_revision_record(
    session: Session,
    revision: PortfolioRevision,
) -> PortfolioRevisionRecord | None:
    existing = session.get(
        PortfolioRevisionRecord,
        revision.portfolio_revision_id,
    )
    if existing is not None:
        return existing
    return session.scalar(
        select(PortfolioRevisionRecord).where(
            PortfolioRevisionRecord.fusion_run_id == revision.fusion_run_id,
            PortfolioRevisionRecord.revision_policy == revision.revision_policy,
            PortfolioRevisionRecord.revision_version == revision.revision_version,
            PortfolioRevisionRecord.config_hash == revision.config_hash,
        )
    )


def _matching_fusion_run(
    session: Session,
    record: FusionRunRecord,
    expected: FusionRun,
) -> FusionRun:
    stored = _fusion_run(session, record)
    if stored != expected:
        raise ValueError("immutable FusionRun conflicts with stored data")
    return stored


def _matching_revision(
    session: Session,
    record: PortfolioRevisionRecord,
    expected: PortfolioRevision,
) -> PortfolioRevision:
    stored = _portfolio_revision(session, record)
    if stored != expected:
        raise ValueError("immutable PortfolioRevision conflicts with stored data")
    return stored


def _fusion_run(session: Session, record: FusionRunRecord) -> FusionRun:
    _validate_hashed_canonical_text(
        record.config_json,
        record.config_hash,
        "FusionRun config",
    )
    _completed_run(session, record.parent_analysis_run_id)
    artifact = session.get(
        LLMReviewArtifactRecord,
        record.llm_review_artifact_id,
    )
    if (
        artifact is None
        or artifact.parent_analysis_run_id != record.parent_analysis_run_id
    ):
        raise ValueError("stored FusionRun review lineage is inconsistent")
    if (
        _sha256(artifact.raw_review_json) != artifact.raw_review_hash
        or _sha256(artifact.normalized_review_json) != artifact.normalized_review_hash
    ):
        raise ValueError("stored FusionRun review artifact failed hash verification")
    result_records = tuple(
        session.scalars(
            select(FusionRunResultRecord)
            .where(FusionRunResultRecord.fusion_run_id == record.fusion_run_id)
            .order_by(
                FusionRunResultRecord.internal_match_id,
                FusionRunResultRecord.market_key,
            )
        )
    )
    results = tuple(
        _fusion_result(session, record, result_record)
        for result_record in result_records
    )
    base_prediction_ids = set(
        session.scalars(
            select(FinalPredictionRecord.final_prediction_id).where(
                FinalPredictionRecord.analysis_run_id == record.parent_analysis_run_id
            )
        )
    )
    if {result.base_prediction_id for result in results} != base_prediction_ids:
        raise ValueError("stored FusionRun does not cover its base predictions")
    return FusionRun(
        fusion_run_id=record.fusion_run_id,
        parent_analysis_run_id=record.parent_analysis_run_id,
        llm_review_artifact_id=record.llm_review_artifact_id,
        fusion_policy=record.fusion_policy,
        fusion_version=record.fusion_version,
        config_json=record.config_json,
        config_hash=record.config_hash,
        created_at_utc=record.created_at_utc,
        results=results,
    )


def _fusion_result(
    session: Session,
    fusion_record: FusionRunRecord,
    record: FusionRunResultRecord,
) -> FusionMatchResult:
    if _sha256(record.result_json) != record.result_hash:
        raise ValueError("stored FusionRun result failed hash verification")
    payload = _canonical_payload(record.result_json, "FusionRun result")
    result = FusionMatchResult.model_validate(payload)
    explicit_json = {
        "p_base_json": _canonical_json(result.p_base),
        "p_llm_json": (
            _canonical_json(result.p_llm) if result.p_llm is not None else None
        ),
        "raw_probability_delta_json": (
            _canonical_json(result.raw_probability_delta)
            if result.raw_probability_delta is not None
            else None
        ),
        "applied_probability_delta_json": _canonical_json(
            result.applied_probability_delta
        ),
        "p_final_json": _canonical_json(result.p_final),
    }
    if any(getattr(record, field) != value for field, value in explicit_json.items()):
        raise ValueError("stored FusionRun result canonical columns are inconsistent")
    if (
        result.fusion_result_id != record.fusion_result_id
        or result.fusion_run_id != fusion_record.fusion_run_id
        or record.fusion_run_id != fusion_record.fusion_run_id
        or result.match_id != record.internal_match_id
        or result.market.canonical != record.market_key
        or result.market.market_type.value != record.market_type
        or result.market.handicap_value != record.handicap_value
        or result.base_prediction_id != record.base_prediction_id
        or result.confidence_factor != record.confidence_factor
        or result.data_quality_factor != record.data_quality_factor
        or result.fallback_code != record.fallback_code
    ):
        raise ValueError("stored FusionRun result columns are inconsistent")
    base_record = session.get(FinalPredictionRecord, record.base_prediction_id)
    if base_record is None:
        raise ValueError("stored FusionRun result is missing its base prediction")
    base = _final_prediction(session, base_record)
    if (
        base.analysis_run_id != fusion_record.parent_analysis_run_id
        or base.match_id != result.match_id
        or base.market != result.market
        or base.probabilities != result.p_base
    ):
        raise ValueError("stored FusionRun result base lineage is inconsistent")
    return result


def _fusion_result_record(result: FusionMatchResult) -> FusionRunResultRecord:
    result_json = _canonical_json(result)
    return FusionRunResultRecord(
        fusion_result_id=result.fusion_result_id,
        fusion_run_id=result.fusion_run_id,
        internal_match_id=result.match_id,
        market_key=result.market.canonical,
        market_type=result.market.market_type.value,
        handicap_value=result.market.handicap_value,
        base_prediction_id=result.base_prediction_id,
        p_base_json=_canonical_json(result.p_base),
        p_llm_json=(
            _canonical_json(result.p_llm) if result.p_llm is not None else None
        ),
        raw_probability_delta_json=(
            _canonical_json(result.raw_probability_delta)
            if result.raw_probability_delta is not None
            else None
        ),
        applied_probability_delta_json=_canonical_json(
            result.applied_probability_delta
        ),
        confidence_factor=result.confidence_factor,
        data_quality_factor=result.data_quality_factor,
        p_final_json=_canonical_json(result.p_final),
        fallback_code=result.fallback_code,
        result_json=result_json,
        result_hash=_sha256(result_json),
    )


def _portfolio_revision(
    session: Session,
    record: PortfolioRevisionRecord,
) -> PortfolioRevision:
    payload = _canonical_payload(record.revision_json, "PortfolioRevision")
    revision = PortfolioRevision.model_validate(payload)
    _validate_revision_for_save(revision)
    if (
        revision.portfolio_revision_id != record.portfolio_revision_id
        or revision.parent_analysis_run_id != record.parent_analysis_run_id
        or revision.fusion_run_id != record.fusion_run_id
        or revision.revision_policy != record.revision_policy
        or revision.revision_version != record.revision_version
        or revision.generated_at_utc != record.generated_at_utc
        or revision.config_json != record.config_json
        or revision.config_hash != record.config_hash
        or revision.revision_hash != record.revision_hash
        or _canonical_json(revision) != record.revision_json
    ):
        raise ValueError("stored PortfolioRevision columns are inconsistent")
    fusion = session.get(FusionRunRecord, record.fusion_run_id)
    if fusion is None or fusion.parent_analysis_run_id != record.parent_analysis_run_id:
        raise ValueError("stored PortfolioRevision lineage is inconsistent")
    _completed_run(session, record.parent_analysis_run_id)
    return revision


def _validate_fusion_for_save(fusion_run: FusionRun) -> None:
    _validate_hashed_canonical_text(
        fusion_run.config_json,
        fusion_run.config_hash,
        "FusionRun config",
    )
    for result in fusion_run.results:
        _canonical_json(result)


def _validate_revision_for_save(revision: PortfolioRevision) -> None:
    _validate_hashed_canonical_text(
        revision.config_json,
        revision.config_hash,
        "PortfolioRevision config",
    )
    payload_json = _canonical_json(
        revision.model_dump(mode="python", exclude={"revision_hash"})
    )
    if _sha256(payload_json) != revision.revision_hash:
        raise ValueError("PortfolioRevision hash does not match canonical JSON")


def _completed_run(session: Session, analysis_run_id: str) -> AnalysisRunRecord:
    run = session.get(AnalysisRunRecord, analysis_run_id)
    if run is None:
        raise ValueError("post-review artifact references an unknown AnalysisRun")
    if run.status != "COMPLETED" or run.completed_at_utc is None:
        raise ValueError("post-review artifacts require a completed AnalysisRun")
    _validate_hashed_canonical_text(
        run.config_json,
        run.config_hash,
        "AnalysisRun config",
    )
    _validate_hashed_canonical_text(
        run.input_manifest_json,
        run.input_manifest_hash,
        "AnalysisRun manifest",
    )
    return run


def _stored_packet(
    record: AnalysisPacketRecord,
    run: AnalysisRunRecord,
) -> tuple[StoredAnalysisPacket, AnalysisPacketContract]:
    payload = _canonical_payload(record.packet_json, "AnalysisPacket")
    if not isinstance(payload, dict):
        raise ValueError("stored AnalysisPacket JSON must be an object")
    packet_type = {
        "ANALYSIS_PACKET_V1": AnalysisPacket,
        "ANALYSIS_PACKET_V2": AnalysisPacketV2,
    }.get(record.schema_version)
    if packet_type is None:
        raise ValueError(f"unsupported stored AnalysisPacket: {record.schema_version}")
    packet = packet_type.model_validate(payload)
    hash_payload = dict(payload)
    hash_payload.pop("packet_hash", None)
    expected_id = stable_id(
        "analysis-packet",
        run.analysis_run_id,
        packet.schema_version,
        run.input_manifest_hash,
        run.code_revision,
    )
    if (
        packet.packet_id != record.packet_id
        or packet.packet_id != expected_id
        or packet.schema_version != record.schema_version
        or packet.generated_at_utc != record.generated_at_utc
        or packet.analysis_run.analysis_run_id != record.parent_analysis_run_id
        or record.parent_analysis_run_id != run.analysis_run_id
        or packet.analysis_run.completed_at_utc != run.completed_at_utc
        or packet.analysis_run.input_manifest_hash != run.input_manifest_hash
        or packet.analysis_run.code_revision != run.code_revision
        or packet.packet_hash != record.packet_hash
        or _sha256(_canonical_json(hash_payload)) != record.packet_hash
    ):
        raise ValueError("stored AnalysisPacket failed integrity verification")
    return (
        StoredAnalysisPacket(
            packet_id=record.packet_id,
            parent_analysis_run_id=record.parent_analysis_run_id,
            schema_version=record.schema_version,
            packet_hash=record.packet_hash,
            packet_json=record.packet_json,
        ),
        packet,
    )


def _review_artifact(
    record: LLMReviewArtifactRecord,
    packet: AnalysisPacketContract,
) -> LLMReviewArtifact:
    if (
        _sha256(record.raw_review_json) != record.raw_review_hash
        or _sha256(record.normalized_review_json) != record.normalized_review_hash
    ):
        raise ValueError("stored LLM review artifact failed hash verification")
    payload = _canonical_payload(
        record.normalized_review_json,
        "normalized LLM review",
    )
    submission_type = {
        "LLM_REVIEW_V1": LLMReviewSubmission,
        "LLM_REVIEW_V2": LLMReviewSubmissionV2,
    }.get(record.review_schema_version)
    if submission_type is None:
        raise ValueError(
            f"unsupported stored LLM review: {record.review_schema_version}"
        )
    submission = submission_type.model_validate(payload)
    packet_match_ids = {item.match_id for item in packet.matches}
    expected_review_schema, expected_validator = {
        "ANALYSIS_PACKET_V1": (
            "LLM_REVIEW_V1",
            "OFFLINE_REVIEW_VALIDATOR_V1",
        ),
        "ANALYSIS_PACKET_V2": (
            "LLM_REVIEW_V2",
            "OFFLINE_REVIEW_VALIDATOR_V2",
        ),
    }[packet.schema_version]
    expected_id = stable_id(
        "llm-review-artifact",
        packet.packet_id,
        record.normalized_review_hash,
        record.validator_version,
    )
    if (
        record.review_artifact_id != expected_id
        or record.parent_analysis_run_id != packet.analysis_run.analysis_run_id
        or record.packet_id != packet.packet_id
        or record.packet_hash != packet.packet_hash
        or record.review_schema_version != expected_review_schema
        or record.validator_version != expected_validator
        or submission.schema_version != record.review_schema_version
        or submission.analysis_run_id != record.parent_analysis_run_id
        or submission.packet_id != record.packet_id
        or submission.packet_hash != record.packet_hash
        or {item.match_id for item in submission.match_reviews} != packet_match_ids
    ):
        raise ValueError("stored LLM review artifact lineage is inconsistent")
    return LLMReviewArtifact(
        review_artifact_id=record.review_artifact_id,
        parent_analysis_run_id=record.parent_analysis_run_id,
        packet_id=record.packet_id,
        packet_hash=record.packet_hash,
        review_schema_version=record.review_schema_version,
        imported_at_utc=record.imported_at_utc,
        raw_review_json=record.raw_review_json,
        raw_review_hash=record.raw_review_hash,
        normalized_review_json=record.normalized_review_json,
        normalized_review_hash=record.normalized_review_hash,
        validator_version=record.validator_version,
        source_kind=record.source_kind,
    )


def _final_prediction(
    session: Session,
    record: FinalPredictionRecord,
) -> FinalPrediction:
    market = _market_key(
        record.market_key,
        record.market_type,
        record.handicap_value,
    )
    probabilities = _final_probabilities(session, record.final_prediction_id)
    _canonical_payload(record.fusion_config_json, "FinalPrediction fusion config")
    return FinalPrediction(
        prediction_id=record.final_prediction_id,
        analysis_run_id=record.analysis_run_id,
        match_id=record.internal_match_id,
        market=market,
        probabilities=probabilities,
        market_prediction_id=record.market_probability_id,
        quant_prediction_id=record.quant_prediction_id,
        llm_assessment_id=record.llm_assessment_id,
        fusion_policy=FusionPolicyName(record.fusion_policy),
        fusion_version=record.fusion_version,
        fusion_config_json=record.fusion_config_json,
        fallback_code=record.fallback_code,
        confidence=record.confidence,
        generated_at_utc=record.generated_at_utc,
    )


def _final_probabilities(
    session: Session,
    prediction_id: str,
) -> ThreeWayProbability:
    rows = tuple(
        session.scalars(
            select(FinalPredictionOutcomeRecord).where(
                FinalPredictionOutcomeRecord.final_prediction_id == prediction_id
            )
        )
    )
    try:
        values = {SelectionKey(row.selection_key): row.probability for row in rows}
    except ValueError as error:
        raise ValueError("stored FinalPrediction has an invalid selection") from error
    if set(values) != set(SelectionKey) or len(rows) != len(SelectionKey):
        raise ValueError("stored FinalPrediction probabilities are incomplete")
    return ThreeWayProbability(
        home_win=values[SelectionKey.HOME_WIN],
        draw=values[SelectionKey.DRAW],
        away_win=values[SelectionKey.AWAY_WIN],
    )


def _sporttery_snapshot(
    session: Session,
    record: SportteryBonusSnapshotRecord,
) -> SportteryBonusSnapshot:
    provider = session.get(ProviderRecord, record.provider_id)
    if provider is None:
        raise ValueError("stored Sporttery snapshot is missing its provider")
    rows = tuple(
        session.scalars(
            select(SportteryBonusQuoteRecord).where(
                SportteryBonusQuoteRecord.snapshot_id == record.snapshot_id
            )
        )
    )
    try:
        values = {SelectionKey(row.selection_key): row.fixed_bonus for row in rows}
    except ValueError as error:
        raise ValueError(
            "stored Sporttery snapshot has an invalid selection"
        ) from error
    if set(values) != set(SelectionKey) or len(rows) != len(SelectionKey):
        raise ValueError("stored Sporttery snapshot quotes are incomplete")
    return SportteryBonusSnapshot(
        snapshot_id=record.snapshot_id,
        match_id=record.internal_match_id,
        provider_code=provider.code,
        sporttery_match_no=record.sporttery_match_no,
        market=_market_key(
            record.market_key,
            record.market_type,
            record.handicap_value,
        ),
        quotes=tuple(
            FixedBonusQuote(selection=selection, fixed_bonus=values[selection])
            for selection in SelectionKey
        ),
        sale_status=SaleStatus(record.sale_status),
        captured_at_utc=record.captured_at_utc,
        available_at_utc=record.available_at_utc,
        ingested_at_utc=record.ingested_at_utc,
        source_snapshot_key=record.source_snapshot_key,
        payload_hash=record.payload_hash,
    )


def _market_key(
    canonical: str,
    market_type: str,
    handicap_value: Decimal | None,
) -> MarketKey:
    market = MarketKey(
        market_type=MarketType(market_type),
        handicap_value=handicap_value,
    )
    if market.canonical != canonical:
        raise ValueError("stored market columns are inconsistent")
    return market


def _validate_hashed_canonical_text(
    value: str,
    expected_hash: str,
    label: str,
) -> None:
    _canonical_payload(value, label)
    if _sha256(value) != expected_hash:
        raise ValueError(f"{label} failed hash verification")


def _canonical_payload(value: str, label: str) -> object:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON number is forbidden: {constant}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(
            value,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} contains invalid JSON") from error
    if _canonical_json(payload) != value:
        raise ValueError(f"{label} is not canonical JSON")
    return payload


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal cannot be serialized")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("JSON datetime must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
