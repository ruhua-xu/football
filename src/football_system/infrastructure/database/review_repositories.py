from __future__ import annotations

import hashlib
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from football_system.domain.common import stable_id
from football_system.domain.market import (
    SelectionKey,
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.review import (
    AnalysisPacketContract,
    AnalysisPacketMatch,
    AnalysisPacketMatchSourceV2,
    AnalysisPacketRun,
    AnalysisPacketSource,
    AnalysisPacketSourceV2,
    LLMReviewArtifact,
    MatchReviewContext,
    PacketDataQuality,
    PacketDataQualityStatus,
    PacketEvidence,
    PacketInternationalOdds,
    PacketMarketPrediction,
    PacketQuantPrediction,
    PacketSportteryOdds,
    StoredAnalysisPacket,
)
from football_system.infrastructure.database.models import (
    AnalysisPacketRecord,
    AnalysisRunMatchRecord,
    AnalysisRunRecord,
    BookmakerRecord,
    CompetitionRecord,
    LLMReviewArtifactRecord,
    ManualQuantInputRecord,
    MarketOddsQuoteRecord,
    MarketOddsSnapshotRecord,
    MarketProbabilityInputRecord,
    MarketProbabilityOutcomeRecord,
    MarketProbabilityRecord,
    MatchRecord,
    ProviderRecord,
    QuantPredictionOutcomeRecord,
    QuantPredictionRecord,
    SportteryBonusSnapshotRecord,
    SportteryBonusQuoteRecord,
    TeamRecord,
)


class SqlAlchemyReviewArtifactRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_packet_source(self, analysis_run_id: str) -> AnalysisPacketSource:
        with self._session_factory() as session:
            run = session.get(AnalysisRunRecord, analysis_run_id)
            if run is None:
                raise KeyError(f"unknown AnalysisRun: {analysis_run_id}")
            if run.status != "COMPLETED" or run.completed_at_utc is None:
                raise ValueError("AnalysisPacket requires a completed AnalysisRun")
            if _sha256(run.config_json) != run.config_hash:
                raise ValueError("stored AnalysisRun config failed hash verification")
            if _sha256(run.input_manifest_json) != run.input_manifest_hash:
                raise ValueError("stored AnalysisRun manifest failed hash verification")
            contexts = tuple(
                session.scalars(
                    select(AnalysisRunMatchRecord)
                    .where(AnalysisRunMatchRecord.analysis_run_id == analysis_run_id)
                    .order_by(AnalysisRunMatchRecord.internal_match_id)
                )
            )
            matches = tuple(
                self._load_match(session, run, context) for context in contexts
            )
            return AnalysisPacketSource(
                analysis_run=AnalysisPacketRun(
                    analysis_run_id=run.analysis_run_id,
                    as_of_at_utc=run.as_of_at_utc,
                    completed_at_utc=run.completed_at_utc,
                    pipeline_version=run.pipeline_version,
                    code_revision=run.code_revision,
                    input_manifest_version=run.input_manifest_version,
                    input_manifest_hash=run.input_manifest_hash,
                ),
                matches=matches,
            )

    def load_packet_source_v2(self, analysis_run_id: str) -> AnalysisPacketSourceV2:
        with self._session_factory() as session:
            run = session.get(AnalysisRunRecord, analysis_run_id)
            if run is None:
                raise KeyError(f"unknown AnalysisRun: {analysis_run_id}")
            if run.status != "COMPLETED" or run.completed_at_utc is None:
                raise ValueError("AnalysisPacket requires a completed AnalysisRun")
            if _sha256(run.config_json) != run.config_hash:
                raise ValueError("stored AnalysisRun config failed hash verification")
            if _sha256(run.input_manifest_json) != run.input_manifest_hash:
                raise ValueError("stored AnalysisRun manifest failed hash verification")
            contexts = tuple(
                session.scalars(
                    select(AnalysisRunMatchRecord)
                    .where(AnalysisRunMatchRecord.analysis_run_id == analysis_run_id)
                    .order_by(AnalysisRunMatchRecord.internal_match_id)
                )
            )
            matches = tuple(
                self._load_match_v2(session, run, context) for context in contexts
            )
            return AnalysisPacketSourceV2(
                analysis_run=AnalysisPacketRun(
                    analysis_run_id=run.analysis_run_id,
                    as_of_at_utc=run.as_of_at_utc,
                    completed_at_utc=run.completed_at_utc,
                    pipeline_version=run.pipeline_version,
                    code_revision=run.code_revision,
                    input_manifest_version=run.input_manifest_version,
                    input_manifest_hash=run.input_manifest_hash,
                ),
                matches=matches,
            )

    def find_analysis_packet(
        self,
        analysis_run_id: str,
        schema_version: str,
    ) -> StoredAnalysisPacket | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(AnalysisPacketRecord).where(
                    AnalysisPacketRecord.parent_analysis_run_id == analysis_run_id,
                    AnalysisPacketRecord.schema_version == schema_version,
                )
            )
            return _stored_packet(record) if record is not None else None

    def save_analysis_packet(
        self,
        packet: AnalysisPacketContract,
        packet_json: str,
    ) -> StoredAnalysisPacket:
        try:
            with self._session_factory.begin() as session:
                existing = _find_packet_record(session, packet)
                if existing is not None:
                    return _stored_packet(existing)
                record = AnalysisPacketRecord(
                    packet_id=packet.packet_id,
                    parent_analysis_run_id=packet.analysis_run.analysis_run_id,
                    schema_version=packet.schema_version,
                    generated_at_utc=packet.generated_at_utc,
                    packet_json=packet_json,
                    packet_hash=packet.packet_hash,
                )
                session.add(record)
                session.flush()
                return _stored_packet(record)
        except IntegrityError:
            with self._session_factory() as session:
                existing = _find_packet_record(session, packet)
                if existing is not None:
                    return _stored_packet(existing)
            raise

    def load_analysis_packet(self, packet_id: str) -> StoredAnalysisPacket:
        with self._session_factory() as session:
            record = session.get(AnalysisPacketRecord, packet_id)
            if record is None:
                raise KeyError(f"unknown AnalysisPacket: {packet_id}")
            return _stored_packet(record)

    def save_llm_review(self, artifact: LLMReviewArtifact) -> LLMReviewArtifact:
        try:
            with self._session_factory.begin() as session:
                existing = _find_review_record(session, artifact)
                if existing is not None:
                    return _review_artifact(existing)
                record = LLMReviewArtifactRecord(
                    review_artifact_id=artifact.review_artifact_id,
                    parent_analysis_run_id=artifact.parent_analysis_run_id,
                    packet_id=artifact.packet_id,
                    packet_hash=artifact.packet_hash,
                    review_schema_version=artifact.review_schema_version,
                    imported_at_utc=artifact.imported_at_utc,
                    raw_review_json=artifact.raw_review_json,
                    raw_review_hash=artifact.raw_review_hash,
                    normalized_review_json=artifact.normalized_review_json,
                    normalized_review_hash=artifact.normalized_review_hash,
                    validator_version=artifact.validator_version,
                    source_kind=artifact.source_kind,
                )
                session.add(record)
                session.flush()
                return _review_artifact(record)
        except IntegrityError:
            with self._session_factory() as session:
                existing = _find_review_record(session, artifact)
                if existing is not None:
                    return _review_artifact(existing)
            raise

    @staticmethod
    def _load_match(
        session: Session,
        run: AnalysisRunRecord,
        context: AnalysisRunMatchRecord,
    ) -> AnalysisPacketMatch:
        if _sha256(context.context_json) != context.context_hash:
            raise ValueError("stored match context failed hash verification")
        match = _required(session.get(MatchRecord, context.internal_match_id), "match")
        competition = _required(
            session.get(CompetitionRecord, match.competition_id), "competition"
        )
        home_team = _required(session.get(TeamRecord, match.home_team_id), "home team")
        away_team = _required(session.get(TeamRecord, match.away_team_id), "away team")
        odds_snapshot = _required(
            session.get(MarketOddsSnapshotRecord, context.market_odds_snapshot_id),
            "market odds snapshot",
        )
        bonus_snapshot = _required(
            session.get(
                SportteryBonusSnapshotRecord,
                context.sporttery_bonus_snapshot_id,
            ),
            "Sporttery bonus snapshot",
        )
        manual_input = _required(
            session.get(ManualQuantInputRecord, context.manual_quant_input_id),
            "manual quant input",
        )
        if any(
            source.internal_match_id != match.internal_match_id
            for source in (odds_snapshot, bonus_snapshot, manual_input)
        ) or not (
            odds_snapshot.market_key
            == bonus_snapshot.market_key
            == manual_input.market_key
        ):
            raise ValueError("stored packet context has inconsistent source lineage")
        market_prediction = _required_one(
            session,
            select(MarketProbabilityRecord).where(
                MarketProbabilityRecord.analysis_run_id == run.analysis_run_id,
                MarketProbabilityRecord.internal_match_id == match.internal_match_id,
                MarketProbabilityRecord.market_key == odds_snapshot.market_key,
            ),
            "market prediction",
        )
        quant_prediction = _required_one(
            session,
            select(QuantPredictionRecord).where(
                QuantPredictionRecord.analysis_run_id == run.analysis_run_id,
                QuantPredictionRecord.internal_match_id == match.internal_match_id,
                QuantPredictionRecord.market_key == odds_snapshot.market_key,
            ),
            "quant prediction",
        )
        input_snapshot_ids = tuple(
            session.scalars(
                select(MarketProbabilityInputRecord.market_odds_snapshot_id)
                .where(
                    MarketProbabilityInputRecord.market_probability_id
                    == market_prediction.market_probability_id
                )
                .order_by(MarketProbabilityInputRecord.market_odds_snapshot_id)
            )
        )
        if input_snapshot_ids != (context.market_odds_snapshot_id,) or (
            quant_prediction.manual_input_id != context.manual_quant_input_id
            or quant_prediction.input_payload_hash != manual_input.payload_hash
        ):
            raise ValueError(
                "stored packet predictions have inconsistent source lineage"
            )
        return AnalysisPacketMatch(
            match_id=match.internal_match_id,
            competition_id=competition.competition_id,
            competition_name=competition.name,
            home_team_id=home_team.team_id,
            home_team_name=home_team.name,
            away_team_id=away_team.team_id,
            away_team_name=away_team.name,
            kickoff_at_utc=match.kickoff_at_utc,
            market_key=market_prediction.market_key,
            context_hash=context.context_hash,
            p_market=PacketMarketPrediction(
                prediction_id=market_prediction.market_probability_id,
                probabilities=_three_way_probabilities(
                    session,
                    MarketProbabilityOutcomeRecord,
                    "market_probability_id",
                    market_prediction.market_probability_id,
                ),
                input_snapshot_ids=input_snapshot_ids,
            ),
            p_quant=PacketQuantPrediction(
                prediction_id=quant_prediction.quant_prediction_id,
                probabilities=_three_way_probabilities(
                    session,
                    QuantPredictionOutcomeRecord,
                    "quant_prediction_id",
                    quant_prediction.quant_prediction_id,
                ),
                manual_input_id=quant_prediction.manual_input_id,
                input_payload_hash=quant_prediction.input_payload_hash,
            ),
        )

    @classmethod
    def _load_match_v2(
        cls,
        session: Session,
        run: AnalysisRunRecord,
        context: AnalysisRunMatchRecord,
    ) -> AnalysisPacketMatchSourceV2:
        base = cls._load_match(session, run, context)
        market_snapshot = _required(
            session.get(MarketOddsSnapshotRecord, context.market_odds_snapshot_id),
            "market odds snapshot",
        )
        bonus_snapshot = _required(
            session.get(
                SportteryBonusSnapshotRecord,
                context.sporttery_bonus_snapshot_id,
            ),
            "Sporttery bonus snapshot",
        )
        provider = _required(
            session.get(ProviderRecord, market_snapshot.provider_id),
            "market odds provider",
        )
        bookmaker = _required(
            session.get(BookmakerRecord, market_snapshot.bookmaker_id),
            "bookmaker",
        )
        sporttery_provider = _required(
            session.get(ProviderRecord, bonus_snapshot.provider_id),
            "Sporttery provider",
        )
        international_odds = PacketInternationalOdds(
            snapshot_id=market_snapshot.snapshot_id,
            provider_id=provider.provider_id,
            provider_name=provider.name,
            bookmaker_id=bookmaker.bookmaker_id,
            bookmaker_name=bookmaker.name,
            captured_at_utc=market_snapshot.captured_at_utc,
            available_at_utc=market_snapshot.available_at_utc,
            payload_hash=market_snapshot.payload_hash,
            odds=_three_way_market_odds(session, market_snapshot.snapshot_id),
        )
        sporttery_odds = PacketSportteryOdds(
            snapshot_id=bonus_snapshot.snapshot_id,
            provider_id=sporttery_provider.provider_id,
            provider_name=sporttery_provider.name,
            sporttery_match_no=bonus_snapshot.sporttery_match_no,
            sale_status=bonus_snapshot.sale_status,
            captured_at_utc=bonus_snapshot.captured_at_utc,
            available_at_utc=bonus_snapshot.available_at_utc,
            payload_hash=bonus_snapshot.payload_hash,
            odds=_three_way_fixed_bonus(session, bonus_snapshot.snapshot_id),
        )
        market_evidence = PacketEvidence(
            evidence_id=stable_id(
                "review-evidence",
                market_snapshot.snapshot_id,
                market_snapshot.payload_hash,
            ),
            category="INTERNATIONAL_ODDS",
            body=_odds_body(
                "International odds",
                base.market_key,
                international_odds.odds.items(),
            ),
            source_kind="SEALED_DATABASE_SNAPSHOT",
            source_name=f"{provider.name} / {bookmaker.name}",
            source_reference=f"market_odds_snapshots/{market_snapshot.snapshot_id}",
            source_record_id=market_snapshot.snapshot_id,
            source_payload_hash=market_snapshot.payload_hash,
            observed_at_utc=market_snapshot.captured_at_utc,
            available_at_utc=market_snapshot.available_at_utc,
        )
        sporttery_evidence = PacketEvidence(
            evidence_id=stable_id(
                "review-evidence",
                bonus_snapshot.snapshot_id,
                bonus_snapshot.payload_hash,
            ),
            category="SPORTTERY_ODDS",
            body=_odds_body(
                "Sporttery fixed bonus",
                base.market_key,
                sporttery_odds.odds.items(),
            ),
            source_kind="SEALED_DATABASE_SNAPSHOT",
            source_name=sporttery_provider.name,
            source_reference=(
                f"sporttery_bonus_snapshots/{bonus_snapshot.snapshot_id}"
            ),
            source_record_id=bonus_snapshot.snapshot_id,
            source_payload_hash=bonus_snapshot.payload_hash,
            observed_at_utc=bonus_snapshot.captured_at_utc,
            available_at_utc=bonus_snapshot.available_at_utc,
        )
        evidence = tuple(
            sorted(
                (market_evidence, sporttery_evidence), key=lambda item: item.evidence_id
            )
        )
        review_context = MatchReviewContext(
            sporttery_odds=sporttery_odds,
            international_odds=international_odds,
            evidence=evidence,
            data_quality=PacketDataQuality(
                status=PacketDataQualityStatus.PARTIAL,
                score=Decimal("0.25"),
                available_fields=(
                    "evidence",
                    "international_odds",
                    "sporttery_odds",
                ),
                missing_fields=(
                    "confirmed_lineup",
                    "expected_lineup",
                    "home_away_form",
                    "injuries",
                    "odds_movement_summary",
                    "recent_form",
                    "rest_days",
                    "schedule_context",
                    "suspensions",
                ),
                notes=(
                    "Mock fixture supplies sealed odds snapshots only; contextual football data is unavailable.",
                ),
            ),
        )
        return AnalysisPacketMatchSourceV2(
            **base.model_dump(mode="python", exclude={"evidence_ids"}),
            evidence_ids=tuple(item.evidence_id for item in evidence),
            review_context=review_context,
        )


def _three_way_probabilities(
    session: Session,
    record_type: type,
    identity_field: str,
    identity: str,
) -> ThreeWayProbability:
    rows = session.scalars(
        select(record_type).where(getattr(record_type, identity_field) == identity)
    )
    values = {SelectionKey(row.selection_key): row.probability for row in rows}
    try:
        return ThreeWayProbability(
            home_win=values[SelectionKey.HOME_WIN],
            draw=values[SelectionKey.DRAW],
            away_win=values[SelectionKey.AWAY_WIN],
        )
    except KeyError as error:
        raise ValueError("stored packet prediction is incomplete") from error


def _three_way_market_odds(session: Session, snapshot_id: str) -> ThreeWayMarketOdds:
    values = {
        SelectionKey(row.selection_key): row.odds
        for row in session.scalars(
            select(MarketOddsQuoteRecord).where(
                MarketOddsQuoteRecord.snapshot_id == snapshot_id
            )
        )
    }
    try:
        return ThreeWayMarketOdds(
            home_win=values[SelectionKey.HOME_WIN],
            draw=values[SelectionKey.DRAW],
            away_win=values[SelectionKey.AWAY_WIN],
        )
    except KeyError as error:
        raise ValueError("stored market odds snapshot is incomplete") from error


def _three_way_fixed_bonus(session: Session, snapshot_id: str) -> ThreeWayFixedBonus:
    values = {
        SelectionKey(row.selection_key): row.fixed_bonus
        for row in session.scalars(
            select(SportteryBonusQuoteRecord).where(
                SportteryBonusQuoteRecord.snapshot_id == snapshot_id
            )
        )
    }
    try:
        return ThreeWayFixedBonus(
            home_win=values[SelectionKey.HOME_WIN],
            draw=values[SelectionKey.DRAW],
            away_win=values[SelectionKey.AWAY_WIN],
        )
    except KeyError as error:
        raise ValueError("stored Sporttery bonus snapshot is incomplete") from error


def _odds_body(
    label: str,
    market_key: str,
    values: tuple[tuple[SelectionKey, Decimal], ...],
) -> str:
    prices = ", ".join(
        f"{selection.value}={format(value.normalize(), 'f')}"
        for selection, value in values
    )
    return f"{label} for {market_key}: {prices}."


def _stored_packet(record: AnalysisPacketRecord) -> StoredAnalysisPacket:
    return StoredAnalysisPacket(
        packet_id=record.packet_id,
        parent_analysis_run_id=record.parent_analysis_run_id,
        schema_version=record.schema_version,
        packet_hash=record.packet_hash,
        packet_json=record.packet_json,
    )


def _find_packet_record(
    session: Session,
    packet: AnalysisPacketContract,
) -> AnalysisPacketRecord | None:
    existing = session.get(AnalysisPacketRecord, packet.packet_id)
    if existing is not None:
        return existing
    return session.scalar(
        select(AnalysisPacketRecord).where(
            AnalysisPacketRecord.parent_analysis_run_id
            == packet.analysis_run.analysis_run_id,
            AnalysisPacketRecord.schema_version == packet.schema_version,
        )
    )


def _find_review_record(
    session: Session,
    artifact: LLMReviewArtifact,
) -> LLMReviewArtifactRecord | None:
    existing = session.get(LLMReviewArtifactRecord, artifact.review_artifact_id)
    if existing is not None:
        return existing
    return session.scalar(
        select(LLMReviewArtifactRecord).where(
            LLMReviewArtifactRecord.packet_id == artifact.packet_id,
            LLMReviewArtifactRecord.normalized_review_hash
            == artifact.normalized_review_hash,
            LLMReviewArtifactRecord.validator_version == artifact.validator_version,
        )
    )


def _review_artifact(record: LLMReviewArtifactRecord) -> LLMReviewArtifact:
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


def _required(value: object | None, label: str) -> object:
    if value is None:
        raise ValueError(f"stored AnalysisRun is missing {label}")
    return value


def _required_one(session: Session, statement: object, label: str) -> object:
    values = tuple(session.scalars(statement))
    if len(values) != 1:
        raise ValueError(f"stored AnalysisRun requires exactly one {label}")
    return values[0]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
