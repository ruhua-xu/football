from __future__ import annotations

import hashlib
from decimal import Decimal

from football_system.application.ports.post_review import PostReviewRepository
from football_system.application.review_bridge import (
    canonical_json,
    sha256_text,
    validate_review_files,
)
from football_system.config import AppSettings
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import stable_id, utc_now
from football_system.domain.market import ThreeWayProbability
from football_system.domain.post_review import (
    FusionMatchResult,
    FusionRun,
    FusionSource,
    PortfolioRevision,
    ThreeWayProbabilityDelta,
)
from football_system.domain.prediction import FinalPrediction, FusionPolicyName
from football_system.domain.review import AnalysisPacketV2, ValidLLMMatchReview
from football_system.domain.services.betting import (
    build_selection_candidates,
    build_two_leg_ticket_candidates,
)
from football_system.domain.services.optimizer import optimize_portfolio
from football_system.domain.services.probability import (
    PROBABILITY_QUANTUM,
    quantize_probability,
    quantize_three_way_probability,
)
from football_system.domain.services.risk import analyze_portfolio_risk

INVALID_REVIEW_FALLBACK = "INVALID_LLM_REVIEW"
ZERO_INFLUENCE_FALLBACK = "ZERO_REVIEW_INFLUENCE"
REVISION_POLICY = "PORTFOLIO_RECOMPUTE_V1"
REVISION_VERSION = "1"


class CreateFusionRunService:
    def __init__(
        self,
        repository: PostReviewRepository,
        settings: AppSettings,
    ) -> None:
        self._repository = repository
        self._settings = settings

    def create(self, review_artifact_id: str) -> FusionRun:
        source = self._repository.load_fusion_source(review_artifact_id)
        fusion_settings = self._settings.review_fusion
        policy = FusionPolicyName(fusion_settings.policy)
        if policy != FusionPolicyName.LLM_REVIEW_DELTA_V1:
            raise ValueError(f"unsupported review fusion policy: {policy}")
        config_json = canonical_json(fusion_settings.model_dump(mode="json"))
        config_hash = sha256_text(config_json)
        fusion_run_id = stable_id(
            "fusion-run",
            source.artifact.parent_analysis_run_id,
            source.artifact.review_artifact_id,
            policy.value,
            fusion_settings.version,
            config_hash,
        )
        existing = self._repository.find_fusion_run(fusion_run_id)
        if existing is not None:
            return existing

        review_by_match, quality_by_match, invalid = _validated_review_inputs(
            source,
            fusion_settings.legacy_data_quality_factor,
        )
        results = []
        for base in sorted(source.base_predictions, key=lambda item: item.match_id):
            review = review_by_match.get(base.match_id)
            quality = quality_by_match.get(base.match_id, Decimal(0))
            result_id = stable_id(
                "fusion-result",
                fusion_run_id,
                base.match_id,
                base.market.canonical,
            )
            if invalid or review is None:
                results.append(
                    _fallback_result(
                        result_id,
                        fusion_run_id,
                        base,
                        INVALID_REVIEW_FALLBACK,
                        quality,
                    )
                )
            elif not isinstance(review, ValidLLMMatchReview):
                results.append(
                    _fallback_result(
                        result_id,
                        fusion_run_id,
                        base,
                        review.failure_code.value,
                        quality,
                    )
                )
            else:
                confidence = quantize_probability(review.assessment_confidence)
                quality = quantize_probability(quality)
                raw_delta, applied_delta, p_final = _apply_review_probability(
                    base.probabilities,
                    review.p_llm,
                    confidence,
                    quality,
                    fusion_settings.max_probability_delta,
                )
                fallback_code = (
                    ZERO_INFLUENCE_FALLBACK if p_final == base.probabilities else None
                )
                results.append(
                    FusionMatchResult(
                        fusion_result_id=result_id,
                        fusion_run_id=fusion_run_id,
                        match_id=base.match_id,
                        market=base.market,
                        base_prediction_id=base.prediction_id,
                        p_base=base.probabilities,
                        p_llm=review.p_llm,
                        raw_probability_delta=raw_delta,
                        applied_probability_delta=applied_delta,
                        confidence_factor=confidence,
                        data_quality_factor=quality,
                        p_final=p_final,
                        fallback_code=fallback_code,
                    )
                )
        fusion_run = FusionRun(
            fusion_run_id=fusion_run_id,
            parent_analysis_run_id=source.artifact.parent_analysis_run_id,
            llm_review_artifact_id=source.artifact.review_artifact_id,
            fusion_policy=policy.value,
            fusion_version=fusion_settings.version,
            config_json=config_json,
            config_hash=config_hash,
            created_at_utc=utc_now(),
            results=tuple(results),
        )
        return self._repository.save_fusion_run(fusion_run)


class CreatePortfolioRevisionService:
    def __init__(
        self,
        repository: PostReviewRepository,
        settings: AppSettings,
    ) -> None:
        self._repository = repository
        self._settings = settings

    def create(self, fusion_run_id: str) -> PortfolioRevision:
        source = self._repository.load_portfolio_revision_source(fusion_run_id)
        config_json = canonical_json(
            {
                "analysis": {
                    "min_selection_ev": self._settings.analysis.min_selection_ev,
                    "min_ticket_roi": self._settings.analysis.min_ticket_roi,
                },
                "portfolio": self._settings.portfolio.model_dump(mode="json"),
                "sporttery": self._settings.sporttery.model_dump(mode="json"),
                "budgets_fen": source.budgets_fen,
            }
        )
        config_hash = sha256_text(config_json)
        revision_id = stable_id(
            "portfolio-revision",
            source.fusion_run.parent_analysis_run_id,
            source.fusion_run.fusion_run_id,
            REVISION_POLICY,
            REVISION_VERSION,
            config_hash,
        )
        existing = self._repository.find_portfolio_revision(revision_id)
        if existing is not None:
            return existing

        generated_at = utc_now()
        final_predictions = tuple(
            FinalPrediction(
                prediction_id=stable_id(
                    "revision-final",
                    revision_id,
                    result.match_id,
                    result.market.canonical,
                ),
                analysis_run_id=revision_id,
                match_id=result.match_id,
                market=result.market,
                probabilities=result.p_final,
                llm_assessment_id=result.fusion_result_id,
                fusion_policy=FusionPolicyName.LLM_REVIEW_DELTA_V1,
                fusion_version=source.fusion_run.fusion_version,
                fusion_config_json=canonical_json(
                    {
                        "fusion_run_id": source.fusion_run.fusion_run_id,
                        "fusion_result_id": result.fusion_result_id,
                    }
                ),
                fallback_code=result.fallback_code,
                confidence=quantize_probability(
                    result.confidence_factor * result.data_quality_factor
                ),
                generated_at_utc=generated_at,
            )
            for result in sorted(
                source.fusion_run.results, key=lambda item: item.match_id
            )
        )
        bonus_by_match = {
            item.match_id: item for item in source.sporttery_bonus_snapshots
        }
        selection_candidates = tuple(
            candidate
            for prediction in final_predictions
            for candidate in build_selection_candidates(
                prediction,
                bonus_by_match[prediction.match_id],
                self._settings.analysis.min_selection_ev,
            )
        )
        rules = _sporttery_rules(self._settings)
        ticket_candidates = build_two_leg_ticket_candidates(
            selection_candidates,
            rules,
            self._settings.analysis.min_ticket_roi,
        )
        constraints = _portfolio_constraints(self._settings)
        portfolios = tuple(
            optimize_portfolio(
                revision_id,
                ticket_candidates,
                budget_fen,
                constraints,
                rules,
            )
            for budget_fen in source.budgets_fen
        )
        risk_reports = tuple(analyze_portfolio_risk(item) for item in portfolios)
        payload = {
            "portfolio_revision_id": revision_id,
            "parent_analysis_run_id": source.fusion_run.parent_analysis_run_id,
            "fusion_run_id": source.fusion_run.fusion_run_id,
            "revision_policy": REVISION_POLICY,
            "revision_version": REVISION_VERSION,
            "generated_at_utc": generated_at,
            "config_json": config_json,
            "config_hash": config_hash,
            "final_predictions": final_predictions,
            "selection_candidates": selection_candidates,
            "ticket_candidates": ticket_candidates,
            "portfolios": portfolios,
            "portfolio_risk_reports": risk_reports,
        }
        revision_hash = sha256_text(canonical_json(payload))
        revision = PortfolioRevision(**payload, revision_hash=revision_hash)
        return self._repository.save_portfolio_revision(revision)


def _validated_review_inputs(
    source: FusionSource,
    legacy_quality: Decimal,
) -> tuple[dict[str, object], dict[str, Decimal], bool]:
    try:
        raw_bytes = source.artifact.raw_review_json.encode("utf-8")
        packet, submission, normalized = validate_review_files(
            source.packet.packet_json.encode("utf-8"),
            raw_bytes,
        )
        if (
            hashlib.sha256(raw_bytes).hexdigest() != source.artifact.raw_review_hash
            or sha256_text(normalized) != source.artifact.normalized_review_hash
            or normalized != source.artifact.normalized_review_json
            or submission.schema_version != source.artifact.review_schema_version
        ):
            raise ValueError("stored LLM review artifact failed integrity validation")
    except ValueError:
        return {}, {}, True
    reviews = {item.match_id: item for item in submission.match_reviews}
    if isinstance(packet, AnalysisPacketV2):
        quality = {
            item.match_id: item.review_context.data_quality.score
            for item in packet.matches
        }
    else:
        quality = {item.match_id: legacy_quality for item in packet.matches}
    return reviews, quality, False


def _fallback_result(
    result_id: str,
    fusion_run_id: str,
    base: FinalPrediction,
    fallback_code: str,
    quality: Decimal,
) -> FusionMatchResult:
    return FusionMatchResult(
        fusion_result_id=result_id,
        fusion_run_id=fusion_run_id,
        match_id=base.match_id,
        market=base.market,
        base_prediction_id=base.prediction_id,
        p_base=base.probabilities,
        p_llm=None,
        raw_probability_delta=None,
        applied_probability_delta=_zero_delta(),
        confidence_factor=Decimal(0),
        data_quality_factor=quantize_probability(quality),
        p_final=base.probabilities,
        fallback_code=fallback_code,
    )


def _apply_review_probability(
    p_base: ThreeWayProbability,
    p_llm: ThreeWayProbability,
    confidence_factor: Decimal,
    data_quality_factor: Decimal,
    max_probability_delta: Decimal,
) -> tuple[ThreeWayProbabilityDelta, ThreeWayProbabilityDelta, ThreeWayProbability]:
    raw = ThreeWayProbabilityDelta(
        home_win=p_llm.home_win - p_base.home_win,
        draw=p_llm.draw - p_base.draw,
        away_win=p_llm.away_win - p_base.away_win,
    )
    influence = confidence_factor * data_quality_factor
    scaled = {name: value * influence for name, value in raw.items()}
    peak = max(abs(value) for value in scaled.values())
    safe_cap = max(Decimal(0), max_probability_delta - PROBABILITY_QUANTUM * 2)
    scale = Decimal(1) if peak == 0 else min(Decimal(1), safe_cap / peak)
    provisional = ThreeWayProbability(
        home_win=p_base.home_win + scaled["home_win"] * scale,
        draw=p_base.draw + scaled["draw"] * scale,
        away_win=p_base.away_win + scaled["away_win"] * scale,
    )
    p_final = quantize_three_way_probability(provisional)
    applied = ThreeWayProbabilityDelta(
        home_win=p_final.home_win - p_base.home_win,
        draw=p_final.draw - p_base.draw,
        away_win=p_final.away_win - p_base.away_win,
    )
    if any(abs(value) > max_probability_delta for _, value in applied.items()):
        raise ValueError("locally computed probability delta exceeds configured cap")
    return raw, applied, p_final


def _zero_delta() -> ThreeWayProbabilityDelta:
    return ThreeWayProbabilityDelta(home_win=0, draw=0, away_win=0)


def _sporttery_rules(settings: AppSettings) -> SportteryRules:
    return SportteryRules(
        version=settings.sporttery.rules_version,
        base_stake_fen=settings.sporttery.base_stake_fen,
        max_multiplier=settings.sporttery.max_multiplier,
        max_ticket_stake_fen=settings.sporttery.max_ticket_stake_fen,
    )


def _portfolio_constraints(settings: AppSettings) -> PortfolioConstraints:
    return PortfolioConstraints(
        preferred_max_tickets=settings.portfolio.preferred_max_tickets,
        absolute_max_tickets=settings.portfolio.absolute_max_tickets,
        extra_ticket_min_roi=settings.portfolio.extra_ticket_min_roi,
        operational_complexity_penalty=(
            settings.portfolio.operational_complexity_penalty
        ),
        max_match_exposure_ratio=settings.portfolio.max_match_exposure_ratio,
        max_selection_exposure_ratio=(settings.portfolio.max_selection_exposure_ratio),
        concentration_penalty=settings.portfolio.concentration_penalty,
        min_marginal_score=settings.portfolio.min_marginal_score,
    )
