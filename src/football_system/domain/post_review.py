from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from football_system.domain.betting import (
    Portfolio,
    SelectionCandidate,
    TicketCandidate,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.market import (
    PROBABILITY_TOLERANCE,
    MarketKey,
    ThreeWayProbability,
)
from football_system.domain.match import SportteryBonusSnapshot
from football_system.domain.prediction import FinalPrediction
from football_system.domain.review import (
    LLMReviewArtifact,
    SHA256_PATTERN,
    StoredAnalysisPacket,
)
from football_system.domain.risk import PortfolioRiskReport
from football_system.domain.services.risk import analyze_portfolio_risk

DELTA_TOLERANCE = PROBABILITY_TOLERANCE


class ThreeWayProbabilityDelta(DomainModel):
    home_win: Decimal = Field(ge=-1, le=1)
    draw: Decimal = Field(ge=-1, le=1)
    away_win: Decimal = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def validate_total(self) -> ThreeWayProbabilityDelta:
        if abs(self.home_win + self.draw + self.away_win) > DELTA_TOLERANCE:
            raise ValueError("three-way probability deltas must sum to zero")
        return self

    def items(self) -> tuple[tuple[str, Decimal], ...]:
        return (
            ("home_win", self.home_win),
            ("draw", self.draw),
            ("away_win", self.away_win),
        )


class FusionMatchResult(DomainModel):
    fusion_result_id: Identifier
    fusion_run_id: Identifier
    match_id: Identifier
    market: MarketKey
    base_prediction_id: Identifier
    p_base: ThreeWayProbability
    p_llm: ThreeWayProbability | None = None
    raw_probability_delta: ThreeWayProbabilityDelta | None = None
    applied_probability_delta: ThreeWayProbabilityDelta
    confidence_factor: Decimal = Field(ge=0, le=1)
    data_quality_factor: Decimal = Field(ge=0, le=1)
    p_final: ThreeWayProbability
    fallback_code: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_probability_lineage(self) -> FusionMatchResult:
        if (self.p_llm is None) != (self.raw_probability_delta is None):
            raise ValueError("P_llm and raw probability delta must be present together")
        if self.p_llm is None and self.fallback_code is None:
            raise ValueError("missing P_llm requires an explicit fallback code")
        if self.p_llm is not None:
            expected_raw = _delta(self.p_llm, self.p_base)
            if not _delta_equal(expected_raw, self.raw_probability_delta):
                raise ValueError(
                    "raw probability delta does not match P_llm and P_base"
                )
        expected_applied = _delta(self.p_final, self.p_base)
        if not _delta_equal(expected_applied, self.applied_probability_delta):
            raise ValueError(
                "applied probability delta does not match P_final and P_base"
            )
        return self


class FusionRun(DomainModel):
    fusion_run_id: Identifier
    parent_analysis_run_id: Identifier
    llm_review_artifact_id: Identifier
    fusion_policy: str = Field(min_length=1, max_length=80)
    fusion_version: str = Field(min_length=1, max_length=40)
    config_json: str = Field(min_length=2)
    config_hash: str = Field(pattern=SHA256_PATTERN)
    created_at_utc: UtcDateTime
    results: tuple[FusionMatchResult, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_results(self) -> FusionRun:
        match_ids = [item.match_id for item in self.results]
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("FusionRun requires one result per match")
        if any(item.fusion_run_id != self.fusion_run_id for item in self.results):
            raise ValueError("FusionRun results reference another fusion run")
        if any(
            item.fusion_result_id
            != stable_id(
                "fusion-result",
                self.fusion_run_id,
                item.match_id,
                item.market.canonical,
            )
            for item in self.results
        ):
            raise ValueError("FusionRun result identity is inconsistent")
        return self


class FusionSource(DomainModel):
    artifact: LLMReviewArtifact
    packet: StoredAnalysisPacket
    base_predictions: tuple[FinalPrediction, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_lineage(self) -> FusionSource:
        run_id = self.artifact.parent_analysis_run_id
        if (
            self.packet.packet_id != self.artifact.packet_id
            or self.packet.parent_analysis_run_id != run_id
            or self.packet.packet_hash != self.artifact.packet_hash
        ):
            raise ValueError("fusion source packet and review lineage is inconsistent")
        match_ids = [item.match_id for item in self.base_predictions]
        if len(match_ids) != len(set(match_ids)) or any(
            item.analysis_run_id != run_id for item in self.base_predictions
        ):
            raise ValueError("fusion source base predictions are inconsistent")
        return self


class PortfolioRevision(DomainModel):
    portfolio_revision_id: Identifier
    parent_analysis_run_id: Identifier
    fusion_run_id: Identifier
    revision_policy: str = Field(min_length=1, max_length=80)
    revision_version: str = Field(min_length=1, max_length=40)
    generated_at_utc: UtcDateTime
    config_json: str = Field(min_length=2)
    config_hash: str = Field(pattern=SHA256_PATTERN)
    revision_hash: str = Field(pattern=SHA256_PATTERN)
    final_predictions: tuple[FinalPrediction, ...] = Field(min_length=1, max_length=256)
    selection_candidates: tuple[SelectionCandidate, ...]
    ticket_candidates: tuple[TicketCandidate, ...]
    portfolios: tuple[Portfolio, ...] = Field(min_length=1)
    portfolio_risk_reports: tuple[PortfolioRiskReport, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lineage(self) -> PortfolioRevision:
        scope_id = self.portfolio_revision_id
        scoped = (
            *self.final_predictions,
            *self.selection_candidates,
            *self.ticket_candidates,
            *self.portfolios,
            *self.portfolio_risk_reports,
        )
        if any(item.analysis_run_id != scope_id for item in scoped):
            raise ValueError("PortfolioRevision children must use the revision scope")
        final_by_id = _unique_index(
            self.final_predictions, "prediction_id", "revision final prediction"
        )
        selection_by_id = _unique_index(
            self.selection_candidates, "candidate_id", "revision selection candidate"
        )
        ticket_by_id = _unique_index(
            self.ticket_candidates,
            "ticket_candidate_id",
            "revision ticket candidate",
        )
        portfolio_by_id = _unique_index(
            self.portfolios, "portfolio_id", "revision portfolio"
        )
        risk_by_portfolio = _unique_index(
            self.portfolio_risk_reports,
            "portfolio_id",
            "revision portfolio risk report",
        )
        if any(
            candidate.final_prediction_id not in final_by_id
            for candidate in self.selection_candidates
        ):
            raise ValueError("revision selection references an unknown prediction")
        if any(
            any(leg.candidate_id not in selection_by_id for leg in ticket.legs)
            for ticket in self.ticket_candidates
        ):
            raise ValueError("revision ticket references an unknown selection")
        if any(
            any(
                allocation.candidate.ticket_candidate_id not in ticket_by_id
                for allocation in portfolio.tickets
            )
            for portfolio in self.portfolios
        ):
            raise ValueError(
                "revision portfolio references an unknown ticket candidate"
            )
        if set(risk_by_portfolio) != set(portfolio_by_id):
            raise ValueError("revision requires one risk report per portfolio")
        if any(
            risk_by_portfolio[portfolio.portfolio_id]
            != analyze_portfolio_risk(portfolio)
            for portfolio in self.portfolios
        ):
            raise ValueError("revision risk report does not match its portfolio")
        return self


class PortfolioRevisionSource(DomainModel):
    fusion_run: FusionRun
    sporttery_bonus_snapshots: tuple[SportteryBonusSnapshot, ...] = Field(
        min_length=1, max_length=256
    )
    budgets_fen: tuple[int, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source(self) -> PortfolioRevisionSource:
        match_ids = [item.match_id for item in self.sporttery_bonus_snapshots]
        if len(match_ids) != len(set(match_ids)):
            raise ValueError("revision source requires one bonus snapshot per match")
        if set(match_ids) != {item.match_id for item in self.fusion_run.results}:
            raise ValueError(
                "revision source bonus snapshots do not cover FusionRun matches"
            )
        if any(budget < 0 for budget in self.budgets_fen) or len(
            self.budgets_fen
        ) != len(set(self.budgets_fen)):
            raise ValueError("revision budgets must be unique and non-negative")
        return self


def _delta(
    target: ThreeWayProbability,
    base: ThreeWayProbability,
) -> ThreeWayProbabilityDelta:
    return ThreeWayProbabilityDelta(
        home_win=target.home_win - base.home_win,
        draw=target.draw - base.draw,
        away_win=target.away_win - base.away_win,
    )


def _delta_equal(
    left: ThreeWayProbabilityDelta,
    right: ThreeWayProbabilityDelta | None,
) -> bool:
    return right is not None and all(
        abs(left_value - right_value) <= DELTA_TOLERANCE
        for (_, left_value), (_, right_value) in zip(
            left.items(), right.items(), strict=True
        )
    )


def _unique_index(items: tuple, field: str, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        identity = getattr(item, field)
        if identity in result:
            raise ValueError(f"duplicate {label}: {identity}")
        result[identity] = item
    return result
