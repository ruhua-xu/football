from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.goal_model import PoissonGoalsStateV1
from football_system.domain.market_v2 import (
    Hash,
    MarketArtifact,
    MarketConsensusV2,
    MarketKeyV2,
    MarketProbabilityDistributionV1,
    MarketTypeV1,
    MultiMarketModel,
    Probability,
    SportteryFixedBonusSnapshotV2,
    distribution,
    revalidate,
    fixed_decimal,
)


class ArtifactRefV1(MultiMarketModel):
    artifact_id: Identifier
    content_hash: Hash
    schema_version: Identifier

    @classmethod
    def of(cls, value):
        return cls(
            artifact_id=value.artifact_id,
            content_hash=value.content_hash,
            schema_version=value.schema_version,
        )


class MarketMatchIdentityV1(MultiMarketModel):
    match_id: Identifier
    competition_id: Identifier
    season_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    home_team_name: str = Field(min_length=1, max_length=160)
    away_team_name: str = Field(min_length=1, max_length=160)
    kickoff_at_utc: UtcDateTime

    @model_validator(mode="after")
    def different_teams(self):
        if self.home_team_id == self.away_team_id:
            raise ValueError("same home and away team")
        return self


class FootballEvidenceV1(MarketArtifact):
    schema_version: Literal["FOOTBALL_EVIDENCE_V1"] = "FOOTBALL_EVIDENCE_V1"
    match_id: Identifier
    facts: tuple[str, ...] = Field(min_length=1, max_length=32)
    source_reference: str = Field(min_length=1, max_length=1024)
    source_hash: Hash
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime

    @model_validator(mode="after")
    def limits(self):
        if (
            any(not f.strip() or len(f) > 2000 for f in self.facts)
            or self.available_at_utc > self.ingested_at_utc
        ):
            raise ValueError("invalid bounded football evidence")
        return self


class MarketModelLineageV1(MultiMarketModel):
    model_name: Literal["ELO_THREE_WAY_BASELINE_V1", "POISSON_GOALS_BASELINE_V1"]
    model_version: Literal["1"] = "1"
    calibration_label: Literal["BASELINE_UNCALIBRATED"] = "BASELINE_UNCALIBRATED"
    state_id: Identifier
    state_hash: Hash
    config_hash: Hash
    training_data_hash: Hash
    training_cutoff_at_utc: UtcDateTime
    generated_at_utc: UtcDateTime


class LegacyThreeWayInputV1(MarketArtifact):
    schema_version: Literal["LEGACY_THREE_WAY_INPUT_V1"] = "LEGACY_THREE_WAY_INPUT_V1"
    analysis_run_id: Identifier
    analysis_run_hash: Hash
    identity: MarketMatchIdentityV1
    decision_cutoff: UtcDateTime
    model_lineage: MarketModelLineageV1
    p_market: MarketProbabilityDistributionV1
    p_quant: MarketProbabilityDistributionV1 | None
    p_base: MarketProbabilityDistributionV1 | None
    unavailable_reason: str | None

    @model_validator(mode="after")
    def old_path(self):
        if self.model_lineage.model_name != "ELO_THREE_WAY_BASELINE_V1":
            raise ValueError("legacy THREE_WAY requires the frozen Elo model")
        if any(
            p is not None and p.market_key.market_type != MarketTypeV1.THREE_WAY
            for p in (self.p_market, self.p_quant, self.p_base)
        ):
            raise ValueError("legacy source cannot impersonate a new market")
        if (self.p_quant is None) != (self.p_base is None) or (
            self.unavailable_reason is None
        ) != (self.p_quant is not None):
            raise ValueError("legacy quant availability mismatch")
        return self


class MarketAnalysisUnitV1(MarketArtifact):
    schema_version: Literal["MARKET_ANALYSIS_UNIT_V1"] = "MARKET_ANALYSIS_UNIT_V1"
    identity: MarketMatchIdentityV1
    market_key: MarketKeyV2
    decision_cutoff: UtcDateTime
    sporttery: SportteryFixedBonusSnapshotV2
    consensus: MarketConsensusV2 | None
    goal_state: PoissonGoalsStateV1 | None
    legacy_source: LegacyThreeWayInputV1 | None
    model_lineage: MarketModelLineageV1
    p_quant: MarketProbabilityDistributionV1 | None
    p_base: MarketProbabilityDistributionV1 | None
    quant_status: Literal["AVAILABLE", "MODEL_UNAVAILABLE"]
    unavailable_reason: str | None
    base_policy: Literal[
        "QUANT_ONLY_V1", "MARKET_QUANT_BLEND_V2", "LEGACY_FROZEN_THREE_WAY"
    ]
    quant_weight: Probability = Decimal("0.70")
    data_quality: Probability
    evidence: tuple[FootballEvidenceV1, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.market_analysis import unit_values

        expected = unit_values(
            self.identity,
            self.market_key,
            self.decision_cutoff,
            self.sporttery,
            self.consensus,
            self.goal_state,
            self.legacy_source,
            self.base_policy,
            self.quant_weight,
        )
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("market analysis probability/model/source replay mismatch")
        if tuple(e.artifact_id for e in self.evidence) != tuple(
            sorted({e.artifact_id for e in self.evidence})
        ) or any(
            e.match_id != self.identity.match_id
            or e.ingested_at_utc > self.decision_cutoff
            for e in self.evidence
        ):
            raise ValueError("market evidence context mismatch or future evidence")
        return self


class MultiMarketAnalysisV1(MarketArtifact):
    schema_version: Literal["MULTI_MARKET_ANALYSIS_V1"] = "MULTI_MARKET_ANALYSIS_V1"
    units: tuple[MarketAnalysisUnitV1, ...] = Field(min_length=1, max_length=64)
    budgets_fen: tuple[Annotated[int, Field(ge=0, strict=True)], ...] = Field(
        min_length=1, max_length=8
    )
    rules: SportteryRules
    constraints: PortfolioConstraints
    min_selection_ev: Decimal = Field(ge=0)
    min_ticket_roi: Decimal = Field(ge=0)
    classification: Literal["SYNTHETIC_ACCEPTANCE_DATA"] = "SYNTHETIC_ACCEPTANCE_DATA"

    @model_validator(mode="after")
    def source(self):
        keys = tuple((u.identity.match_id, u.market_key.canonical) for u in self.units)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("analysis requires unique ordered match-market units")
        identities = {}
        for u in self.units:
            if identities.setdefault(u.identity.match_id, u.identity) != u.identity:
                raise ValueError("market units disagree on match identity")
        if any(
            type(b) is not int or b < 0 for b in self.budgets_fen
        ) or self.budgets_fen != tuple(sorted(set(self.budgets_fen))):
            raise ValueError("budgets must be ordered unique integer fen")
        revalidate(self.rules)
        revalidate(self.constraints)
        if (
            self.rules.base_stake_fen != 200
            or self.rules.max_multiplier > 50
            or self.rules.max_ticket_stake_fen > 600000
        ):
            raise ValueError("multi-market analysis exceeds frozen bankroll rules")
        return self


@fixed_decimal(80)
def base_probability(quant, market, policy, weight):
    if quant is None:
        return None
    if policy == "QUANT_ONLY_V1":
        return quant
    if policy != "MARKET_QUANT_BLEND_V2":
        raise ValueError("new market base policy mismatch")
    if market is None:
        return None
    if market.market_key != quant.market_key:
        raise ValueError("base fusion crosses market semantics")
    return distribution(
        quant.market_key,
        [
            weight * q.probability + (1 - weight) * m.probability
            for q, m in zip(quant.outcomes, market.outcomes, strict=True)
        ],
    )
