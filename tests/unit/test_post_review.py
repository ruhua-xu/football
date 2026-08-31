import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from football_system.application.post_review import CreateFusionRunService
from football_system.application.review_bridge import (
    build_analysis_packet_v2,
    canonical_json,
    sha256_text,
    validate_review_files,
)
from football_system.config import AppSettings
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType, ThreeWayProbability
from football_system.domain.post_review import FusionSource
from football_system.domain.prediction import FinalPrediction, FusionPolicyName
from football_system.domain.review import (
    AnalysisPacketMatchSourceV2,
    AnalysisPacketRun,
    AnalysisPacketSourceV2,
    LLMReviewArtifact,
    MatchReviewContext,
    PacketDataQuality,
    PacketDataQualityStatus,
    PacketMarketPrediction,
    PacketQuantPrediction,
    StoredAnalysisPacket,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
BASE = ThreeWayProbability(
    home_win=Decimal("0.60"),
    draw=Decimal("0.25"),
    away_win=Decimal("0.15"),
)


class InMemoryPostReviewRepository:
    def __init__(self, sources: tuple[FusionSource, ...]) -> None:
        self.sources = {item.artifact.review_artifact_id: item for item in sources}
        self.fusion_runs = {}

    def load_fusion_source(self, review_artifact_id: str) -> FusionSource:
        return self.sources[review_artifact_id]

    def find_fusion_run(self, fusion_run_id: str):
        return self.fusion_runs.get(fusion_run_id)

    def save_fusion_run(self, fusion_run):
        existing = self.fusion_runs.setdefault(fusion_run.fusion_run_id, fusion_run)
        if existing != fusion_run:
            raise ValueError("conflicting FusionRun")
        return existing


def test_fusion_run_computes_and_clips_probability_delta_locally() -> None:
    p_llm = ThreeWayProbability(
        home_win=Decimal("0.20"),
        draw=Decimal("0.30"),
        away_win=Decimal("0.50"),
    )
    source = _fusion_source(p_llm=p_llm, confidence="1")
    repository = InMemoryPostReviewRepository((source,))
    settings = AppSettings(
        review_fusion={
            "max_probability_delta": "0.08",
            "legacy_data_quality_factor": "0.25",
        }
    )

    fusion = CreateFusionRunService(repository, settings).create(
        source.artifact.review_artifact_id
    )
    repeated = CreateFusionRunService(repository, settings).create(
        source.artifact.review_artifact_id
    )

    assert repeated == fusion
    result = fusion.results[0]
    assert result.p_base == BASE
    assert result.p_llm == p_llm
    assert result.raw_probability_delta.home_win == Decimal("-0.40")
    assert result.confidence_factor == Decimal("1.000000000000")
    assert result.data_quality_factor == Decimal("1.000000000000")
    assert max(
        abs(value) for _, value in result.applied_probability_delta.items()
    ) <= Decimal("0.08")
    assert result.p_final != p_llm
    assert result.p_final != BASE
    assert result.fallback_code is None


def test_fusion_run_falls_back_without_stopping_for_unavailable_or_invalid_review() -> (
    None
):
    unavailable = _fusion_source(unavailable=True)
    invalid_artifact = unavailable.artifact.model_copy(
        update={
            "review_artifact_id": "invalid-review-artifact",
            "raw_review_json": "{}",
            "raw_review_hash": hashlib.sha256(b"{}").hexdigest(),
        }
    )
    invalid = FusionSource(
        artifact=invalid_artifact,
        packet=unavailable.packet,
        base_predictions=unavailable.base_predictions,
    )
    repository = InMemoryPostReviewRepository((unavailable, invalid))
    settings = AppSettings()

    unavailable_run = CreateFusionRunService(repository, settings).create(
        unavailable.artifact.review_artifact_id
    )
    invalid_run = CreateFusionRunService(repository, settings).create(
        invalid.artifact.review_artifact_id
    )

    assert unavailable_run.results[0].p_final == BASE
    assert unavailable_run.results[0].fallback_code == "MODEL_UNAVAILABLE"
    assert unavailable_run.results[0].p_llm is None
    assert invalid_run.results[0].p_final == BASE
    assert invalid_run.results[0].fallback_code == "INVALID_LLM_REVIEW"


def test_same_analysis_run_accepts_multiple_append_only_fusion_runs() -> None:
    first = _fusion_source(
        p_llm=ThreeWayProbability(
            home_win=Decimal("0.58"),
            draw=Decimal("0.26"),
            away_win=Decimal("0.16"),
        )
    )
    second = _fusion_source(
        p_llm=ThreeWayProbability(
            home_win=Decimal("0.55"),
            draw=Decimal("0.27"),
            away_win=Decimal("0.18"),
        )
    )
    repository = InMemoryPostReviewRepository((first, second))
    settings = AppSettings()

    first_run = CreateFusionRunService(repository, settings).create(
        first.artifact.review_artifact_id
    )
    second_run = CreateFusionRunService(repository, settings).create(
        second.artifact.review_artifact_id
    )

    assert first_run.parent_analysis_run_id == second_run.parent_analysis_run_id
    assert first_run.fusion_run_id != second_run.fusion_run_id
    assert len(repository.fusion_runs) == 2
    assert first.base_predictions[0].probabilities == BASE
    assert second.base_predictions[0].probabilities == BASE


def _fusion_source(
    *,
    p_llm: ThreeWayProbability | None = None,
    confidence: str = "0.5",
    unavailable: bool = False,
) -> FusionSource:
    market = MarketKey(market_type=MarketType.THREE_WAY)
    run = AnalysisPacketRun(
        analysis_run_id="run-post-review-unit",
        as_of_at_utc=NOW,
        completed_at_utc=NOW,
        pipeline_version="PORTFOLIO_RISK_V1",
        code_revision="package:test",
        input_manifest_version="MVP_INPUT_MANIFEST_V2",
        input_manifest_hash="b" * 64,
    )
    source = AnalysisPacketSourceV2(
        analysis_run=run,
        matches=(
            AnalysisPacketMatchSourceV2(
                match_id="match-1",
                competition_id="competition-1",
                competition_name="League",
                home_team_id="home-1",
                home_team_name="Home",
                away_team_id="away-1",
                away_team_name="Away",
                kickoff_at_utc=NOW,
                market_key=market.canonical,
                context_hash="context-hash",
                p_market=PacketMarketPrediction(
                    prediction_id="p-market-1",
                    probabilities=BASE,
                    input_snapshot_ids=("market-snapshot-1",),
                ),
                p_quant=PacketQuantPrediction(
                    prediction_id="p-quant-1",
                    probabilities=BASE,
                    manual_input_id="manual-1",
                    input_payload_hash="manual-hash-1",
                ),
                evidence_ids=(),
                review_context=MatchReviewContext(
                    data_quality=PacketDataQuality(
                        status=PacketDataQualityStatus.COMPLETE,
                        score=Decimal(1),
                        available_fields=("sealed_unit_context",),
                    )
                ),
            ),
        ),
    )
    packet = build_analysis_packet_v2(source, NOW)
    packet_json = canonical_json(packet.model_dump(mode="json"))
    if unavailable:
        match_review = {
            "status": "UNAVAILABLE",
            "match_id": "match-1",
            "market_key": market.canonical,
            "review_context_id": packet.matches[0].review_context_id,
            "review_context_hash": packet.matches[0].review_context_hash,
            "failure_code": "MODEL_UNAVAILABLE",
            "limitations": ["Model was unavailable"],
        }
    else:
        match_review = {
            "status": "VALID",
            "match_id": "match-1",
            "market_key": market.canonical,
            "review_context_id": packet.matches[0].review_context_id,
            "review_context_hash": packet.matches[0].review_context_hash,
            "p_llm": p_llm or BASE,
            "assessment_confidence": confidence,
            "scenarios": [],
            "preferred_outcomes": [],
            "avoid_outcomes": [],
            "counter_scenarios": [],
            "risk_tags": [],
            "reasoning_summary": "Review based only on sealed packet context.",
            "limitations": [],
        }
    review_json = canonical_json(
        {
            "schema_version": "LLM_REVIEW_V2",
            "analysis_run_id": run.analysis_run_id,
            "packet_id": packet.packet_id,
            "packet_hash": packet.packet_hash,
            "match_reviews": [match_review],
        }
    )
    _, _, normalized = validate_review_files(
        packet_json.encode("utf-8"), review_json.encode("utf-8")
    )
    normalized_hash = sha256_text(normalized)
    artifact = LLMReviewArtifact(
        review_artifact_id=stable_id(
            "llm-review-artifact",
            packet.packet_id,
            normalized_hash,
            "OFFLINE_REVIEW_VALIDATOR_V2",
        ),
        parent_analysis_run_id=run.analysis_run_id,
        packet_id=packet.packet_id,
        packet_hash=packet.packet_hash,
        review_schema_version="LLM_REVIEW_V2",
        imported_at_utc=NOW,
        raw_review_json=review_json,
        raw_review_hash=hashlib.sha256(review_json.encode("utf-8")).hexdigest(),
        normalized_review_json=normalized,
        normalized_review_hash=normalized_hash,
        validator_version="OFFLINE_REVIEW_VALIDATOR_V2",
    )
    base_prediction = FinalPrediction(
        prediction_id="base-final-1",
        analysis_run_id=run.analysis_run_id,
        match_id="match-1",
        market=market,
        probabilities=BASE,
        quant_prediction_id="p-quant-1",
        fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
        fusion_config_json="{}",
        generated_at_utc=NOW,
    )
    return FusionSource(
        artifact=artifact,
        packet=StoredAnalysisPacket(
            packet_id=packet.packet_id,
            parent_analysis_run_id=run.analysis_run_id,
            schema_version=packet.schema_version,
            packet_hash=packet.packet_hash,
            packet_json=packet_json,
        ),
        base_predictions=(base_prediction,),
    )
