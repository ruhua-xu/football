"""Explicit Portfolio numerical input types; never counterfeit a single-run type."""
from typing import Literal

from pydantic import Field, model_validator

from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import MarketArtifact, MultiMarketModel
from football_system.domain.real_bridge import RealAnalysisUnitV1, RealConfigurationV1, require
from football_system.domain.review_v4 import GenericFusionUnitV1, GenericFusionPolicyV1
from football_system.domain.strategy_pass_v2 import (
    Money, OutcomeCandidateV1, StrategyProfileV2, SystemTicketCandidateV2, SystemTicketV2, StructuralRiskV2, TicketRequestV2,
)


class PortfolioNumericalConfigurationV1(MultiMarketModel):
    """Keep each source's result authority; only its non-numerical name may differ."""
    members: tuple[RealConfigurationV1, ...] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def compatible(self):
        expected = self.members[0].model_dump(mode="python", exclude={"result_source_identity"})
        require(all(c.model_dump(mode="python", exclude={"result_source_identity"}) == expected for c in self.members), "PF_NUMERICAL_POLICIES_INCOMPATIBLE")
        return self

    @property
    def rules(self): return self.members[0].rules
    @property
    def constraints(self): return self.members[0].constraints
    @property
    def strategy(self): return self.members[0].strategy
    @property
    def fusion(self): return self.members[0].fusion
    @property
    def allowed_budgets_fen(self): return self.members[0].allowed_budgets_fen
    @property
    def min_selection_ev(self): return self.members[0].min_selection_ev
    @property
    def min_ticket_roi(self): return self.members[0].min_ticket_roi


class PortfolioAnalysisInputV1(MarketArtifact):
    schema_version: Literal["PORTFOLIO_ANALYSIS_INPUT_V1"] = "PORTFOLIO_ANALYSIS_INPUT_V1"
    snapshots: tuple[ArtifactRefV1, ...]
    units: tuple[RealAnalysisUnitV1, ...] = Field(min_length=2, max_length=64)
    configuration: PortfolioNumericalConfigurationV1

    @model_validator(mode="after")
    def complete(self):
        ids = tuple(u.identity.match_id for u in self.units)
        require(ids == tuple(sorted(set(ids))) and all(u.p_quant is not None and u.p_base is not None for u in self.units),
            "PF_COMPLETE_UNIQUE_AVAILABLE_UNITS_REQUIRED")
        return self

    @property
    def rules(self): return self.configuration.rules
    @property
    def constraints(self): return self.configuration.constraints
    @property
    def budgets_fen(self): return self.configuration.allowed_budgets_fen
    @property
    def min_selection_ev(self): return self.configuration.min_selection_ev
    @property
    def min_ticket_roi(self): return self.configuration.min_ticket_roi


class PortfolioFusionInputV1(MarketArtifact):
    schema_version: Literal["PORTFOLIO_FUSION_INPUT_V1"] = "PORTFOLIO_FUSION_INPUT_V1"
    analysis: ArtifactRefV1
    source_fusions: tuple[ArtifactRefV1, ...]
    policy: GenericFusionPolicyV1
    results: tuple[GenericFusionUnitV1, ...] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def keys(self):
        keys = tuple((r.match_id, r.market_key.canonical) for r in self.results)
        require(keys == tuple(sorted(set(keys))) and all(r.p_final is not None for r in self.results), "PF_FINAL_PROBABILITIES_REQUIRED")
        return self


class PortfolioStrategySourceV1(MarketArtifact):
    schema_version: Literal["PORTFOLIO_NUMERICAL_SOURCE_V1"] = "PORTFOLIO_NUMERICAL_SOURCE_V1"
    analysis: PortfolioAnalysisInputV1
    fusion: PortfolioFusionInputV1
    budget_authority: ArtifactRefV1
    budget_fen: Money
    selections: tuple[OutcomeCandidateV1, ...]

    @model_validator(mode="after")
    def exact_inputs(self):
        from football_system.domain.services.strategy_pass_v2 import outcome_candidates
        require(self.fusion.analysis == ArtifactRefV1.of(self.analysis) and self.budget_fen in self.analysis.budgets_fen
            and self.budget_authority.schema_version == "PORTFOLIO_BUDGET_AUTHORITY_V1"
            and self.selections == outcome_candidates(self.analysis, self.fusion), "PF_NUMERICAL_SOURCE_REPLAY_MISMATCH")
        return self


class PortfolioStrategyPlanV1(MarketArtifact):
    schema_version: Literal["PORTFOLIO_STRATEGY_PLAN_V1"] = "PORTFOLIO_STRATEGY_PLAN_V1"
    source: PortfolioStrategySourceV1
    profile: StrategyProfileV2
    requests: tuple[TicketRequestV2, ...]
    candidates: tuple[SystemTicketCandidateV2, ...] = Field(max_length=4096)
    decisions: tuple[str, ...]
    tickets: tuple[SystemTicketV2, ...] = Field(max_length=8)
    risk: StructuralRiskV2
    total_stake_fen: Money
    cash_fen: Money
    status: Literal["ALLOCATED", "NO_BET"]
    reason: str | None

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.strategy_pass_v2 import plan_values
        expected = plan_values(self.source, self.profile, self.requests)
        require(all(getattr(self, name) == value for name, value in expected.items()), "PF_PLAN_REPLAY_MISMATCH")
        return self
