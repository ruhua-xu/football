"""Explicit offline use cases; all bookmaker naming stays in the adapter."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field

from football_system.application.ports.market_v2 import MultiMarketRepository
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.goal_model import (
    GoalPredictionRequestV1,
    PoissonGoalsConfigV1,
)
from football_system.domain.market_analysis import (
    MarketMatchIdentityV1,
    MultiMarketAnalysisV1,
)
from football_system.domain.market_v2 import (
    MarketKeyV2,
    MultiMarketModel,
    Probability,
    market_consensus,
)
from football_system.domain.offline_market_source import OfflineMarketSourceV1
from football_system.domain.review_v4 import GenericFusionPolicyV1
from football_system.domain.services.market_analysis import build_market_unit
from football_system.domain.services.poisson_goals import train_poisson
from football_system.domain.services.review_v4 import (
    export_packet_v4,
    fuse_v4,
    import_v4_bytes,
    parse_v4_json,
)
from football_system.domain.services.settlement_v2 import settle_strategy_v2
from football_system.domain.services.strategy_pass_v2 import (
    build_strategy_v2,
    strategy_source_v2,
)
from football_system.domain.settlement import MatchSettlementIssue
from football_system.domain.strategy_pass_v2 import (
    Money,
    StrategyProfileV2,
    TicketRequestV2,
)


class GoalCohortRequestV1(MultiMarketModel):
    schema_version: Literal["GOAL_COHORT_REQUEST_V1"] = "GOAL_COHORT_REQUEST_V1"
    classification: Literal["SYNTHETIC_ACCEPTANCE_DATA"] = "SYNTHETIC_ACCEPTANCE_DATA"
    source_reference: Identifier
    competition_id: Identifier
    season_id: Identifier
    result_ids: tuple[Identifier, ...] = Field(max_length=1024)
    admitted_at_utc: UtcDateTime


class TrainGoalsRequestV1(MultiMarketModel):
    cohort_id: Identifier
    prediction: GoalPredictionRequestV1
    config: PoissonGoalsConfigV1 = PoissonGoalsConfigV1()


class ConsensusRequestV2(MultiMarketModel):
    match_id: Identifier
    market_key: MarketKeyV2
    snapshot_ids: tuple[Identifier, ...] = Field(max_length=64)
    decision_cutoff: UtcDateTime


class LegacyInputRequestV1(MultiMarketModel):
    analysis_run_id: Identifier
    match_id: Identifier


class BuildMarketUnitRequestV1(MultiMarketModel):
    identity: MarketMatchIdentityV1
    market_key: MarketKeyV2
    decision_cutoff: UtcDateTime
    sporttery_id: Identifier
    consensus_id: Identifier | None = None
    goal_state_id: Identifier | None = None
    legacy_source_id: Identifier | None = None
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=32)
    base_policy: Literal[
        "QUANT_ONLY_V1", "MARKET_QUANT_BLEND_V2", "LEGACY_FROZEN_THREE_WAY"
    ] = "QUANT_ONLY_V1"
    quant_weight: Probability = "0.70"
    data_quality: Probability = "0.25"


class BuildMultiAnalysisRequestV1(MultiMarketModel):
    unit_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    budgets_fen: tuple[Money, ...] = Field(min_length=1, max_length=8)
    rules: SportteryRules
    constraints: PortfolioConstraints
    min_selection_ev: Decimal = Field(ge=0)
    min_ticket_roi: Decimal = Field(ge=0)


class PacketRequestV4(MultiMarketModel):
    analysis_id: Identifier


class FusionRequestV4(MultiMarketModel):
    analysis_id: Identifier
    review_id: Identifier
    policy: GenericFusionPolicyV1 = GenericFusionPolicyV1()


class PlanRequestV2(MultiMarketModel):
    fusion_id: Identifier
    budget_fen: Money
    profile: StrategyProfileV2 = StrategyProfileV2()
    requests: tuple[TicketRequestV2, ...] = Field(default=(), max_length=4096)


class SettleRequestV2(MultiMarketModel):
    plan_id: Identifier
    result_ids: tuple[Identifier, ...] = Field(max_length=16)
    settled_at_utc: UtcDateTime
    previous_id: Identifier | None = None
    issues: tuple[MatchSettlementIssue, ...] = Field(default=(), max_length=16)


class MarketExpansionService:
    def __init__(self, repository: MultiMarketRepository, *, exact_market_adapter=None):
        self.repository = repository
        self.adapter = exact_market_adapter

    def snapshot_import(self, raw):
        if self.adapter is None:
            raise ValueError("explicit exact-market adapter required")
        source, snapshot, reason = self.adapter(raw)
        self.repository.save(source)
        if snapshot is not None:
            self.repository.save(snapshot)
        return dict(
            source_id=source.artifact_id,
            snapshot_id=snapshot.artifact_id if snapshot else None,
            status="AVAILABLE" if snapshot else "MARKET_UNAVAILABLE",
            reason=reason,
        )

    def cohort_admit(self, raw):
        request = GoalCohortRequestV1.model_validate(parse_v4_json(raw))
        source = OfflineMarketSourceV1.freeze(
            source_reference=request.source_reference,
            raw_json=raw.decode(),
            raw_sha256=hashlib.sha256(raw).hexdigest(),
        )
        return self.repository.admit_cohort(source)

    def train_goals(self, r):
        return self.repository.save(
            train_poisson(
                self.repository.load(r.cohort_id, "GOAL_TRAINING_COHORT_V1"),
                r.prediction,
                r.config,
            )
        )

    def consensus(self, r):
        books = tuple(
            self.repository.load(i, "MARKET_ODDS_SNAPSHOT_V2") for i in r.snapshot_ids
        )
        return self.repository.save(
            market_consensus(r.match_id, r.market_key, books, r.decision_cutoff)
        )

    def legacy_input(self, r):
        return self.repository.legacy_threeway(r.analysis_run_id, r.match_id)

    def build_unit(self, r):
        get = self.repository.load
        value = build_market_unit(
            identity=r.identity,
            market_key=r.market_key,
            decision_cutoff=r.decision_cutoff,
            sporttery=get(r.sporttery_id, "SPORTTERY_FIXED_BONUS_SNAPSHOT_V2"),
            consensus=get(r.consensus_id, "MARKET_CONSENSUS_V2")
            if r.consensus_id
            else None,
            goal_state=get(r.goal_state_id, "POISSON_GOALS_STATE_V1")
            if r.goal_state_id
            else None,
            legacy_source=get(r.legacy_source_id, "LEGACY_THREE_WAY_INPUT_V1")
            if r.legacy_source_id
            else None,
            evidence=tuple(get(i, "FOOTBALL_EVIDENCE_V1") for i in r.evidence_ids),
            base_policy=r.base_policy,
            quant_weight=r.quant_weight,
            data_quality=r.data_quality,
        )
        return self.repository.save(value)

    def build_analysis(self, r):
        units = tuple(
            self.repository.load(i, "MARKET_ANALYSIS_UNIT_V1") for i in r.unit_ids
        )
        return self.repository.save(
            MultiMarketAnalysisV1.freeze(
                units=tuple(
                    sorted(
                        units,
                        key=lambda u: (u.identity.match_id, u.market_key.canonical),
                    )
                ),
                budgets_fen=r.budgets_fen,
                rules=r.rules,
                constraints=r.constraints,
                min_selection_ev=r.min_selection_ev,
                min_ticket_roi=r.min_ticket_roi,
            )
        )

    def packet(self, r):
        return self.repository.save(
            export_packet_v4(
                self.repository.load(r.analysis_id, "MULTI_MARKET_ANALYSIS_V1")
            )
        )

    def review_import(self, packet_bytes, review_bytes):
        value = import_v4_bytes(packet_bytes, review_bytes)
        if (
            self.repository.load(value.packet.artifact_id, "ANALYSIS_PACKET_V4")
            != value.packet
        ):
            raise ValueError("import requires exact stored packet")
        return self.repository.save(value)

    def fusion(self, r):
        return self.repository.save(
            fuse_v4(
                self.repository.load(r.analysis_id, "MULTI_MARKET_ANALYSIS_V1"),
                self.repository.load(r.review_id, "IMPORTED_LLM_REVIEW_V4"),
                r.policy,
            )
        )

    def plan(self, r):
        fusion = self.repository.load(r.fusion_id, "GENERIC_FUSION_RUN_V1")
        analysis = self.repository.load(
            fusion.analysis.artifact_id, "MULTI_MARKET_ANALYSIS_V1"
        )
        review = self.repository.load(
            fusion.review.artifact_id, "IMPORTED_LLM_REVIEW_V4"
        )
        source = strategy_source_v2(analysis, review, fusion, r.budget_fen)
        # All expansion guards and complete plan validation precede any write.
        return self.repository.save(build_strategy_v2(source, r.profile, r.requests))

    def settle(self, r):
        plan = self.repository.load(r.plan_id, "STRATEGY_PASS_PLAN_V2")
        previous = (
            self.repository.load(r.previous_id, "STRATEGY_SETTLEMENT_V2")
            if r.previous_id
            else None
        )
        return self.repository.save(
            settle_strategy_v2(
                plan,
                self.repository.results(r.result_ids),
                r.settled_at_utc,
                issues=r.issues,
                previous=previous,
            )
        )
