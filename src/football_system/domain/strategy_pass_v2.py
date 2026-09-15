"""Simple same-market choice sets and explicit expanded system tickets."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from football_system.domain.betting import PassType
from football_system.domain.common import Identifier
from football_system.domain.market_analysis import ArtifactRefV1, MultiMarketAnalysisV1
from football_system.domain.market_v2 import (
    Hash,
    MarketArtifact,
    MarketKeyV2,
    MultiMarketModel,
    OutcomeKeyV1,
    Probability,
    Price,
    content_hash,
    fixed_decimal,
)
from football_system.domain.review_v4 import GenericFusionRunV1, ImportedReviewV4
from football_system.domain.strategy_pass import PASS_SHAPES, TicketRole

Money = Annotated[int, Field(ge=0, strict=True)]


class StrategyProfileV2(MultiMarketModel):
    schema_version: Literal["STRATEGY_PROFILE_V2"] = "STRATEGY_PROFILE_V2"
    name: Literal["FOCUSED_SIMPLE_MULTIPLE_V2"] = "FOCUSED_SIMPLE_MULTIPLE_V2"
    pass_types: tuple[PassType, ...] = tuple(PASS_SHAPES)
    preferred_max_outcomes_per_match: int = Field(default=2, ge=1, le=3, strict=True)
    absolute_max_outcomes_per_match: int = Field(default=3, ge=1, le=3, strict=True)
    max_expanded_atomic_bets_per_ticket: int = Field(
        default=96, ge=1, le=256, strict=True
    )
    max_generated_ticket_candidates: int = Field(
        default=512, ge=1, le=4096, strict=True
    )
    max_input_matches: int = Field(default=12, ge=2, le=16, strict=True)
    max_market_outcomes: Literal[31] = 31
    max_total_expanded_atomic_bets: int = Field(
        default=4096, ge=1, le=4096, strict=True
    )
    max_embedded_source_bytes: Literal[8388608] = 8388608
    max_reconstructable_payout_states: int = Field(
        default=256, ge=1, le=256, strict=True
    )
    preferred_max_tickets: int = Field(default=4, ge=1, le=8, strict=True)
    absolute_max_tickets: int = Field(default=8, ge=1, le=8, strict=True)
    preferred_primary_min: int = Field(default=2, ge=1, le=3, strict=True)
    preferred_primary_max: int = Field(default=3, ge=1, le=3, strict=True)
    max_auxiliary_tickets: Literal[1] = 1
    max_auxiliary_budget_ratio: Probability = Decimal("0.10")
    max_ticket_overlap: Probability = Decimal("0.75")
    max_choice_set_overlap: Probability = Decimal("0.75")
    max_core_dependency_ratio: Probability = Decimal("0.75")
    longshot_min_max_return_multiple: Decimal = Field(default=Decimal(6), gt=1)
    allow_no_bet: Literal[True] = True
    reject_common_wipeout: Literal[True] = True

    @model_validator(mode="after")
    def bounds(self):
        if (
            self.pass_types != tuple(p for p in PASS_SHAPES if p in self.pass_types)
            or not self.pass_types
        ):
            raise ValueError("ordered unique supported passes required")
        if (
            not self.preferred_max_outcomes_per_match
            <= self.absolute_max_outcomes_per_match
            or not self.preferred_primary_min
            <= self.preferred_primary_max
            <= self.preferred_max_tickets
            <= self.absolute_max_tickets
        ):
            raise ValueError("invalid V2 profile limits")
        if (
            not 0 < self.max_auxiliary_budget_ratio <= Decimal("0.25")
            or self.max_core_dependency_ratio <= 0
        ):
            raise ValueError("invalid auxiliary/core risk limit")
        return self

    @property
    def profile_hash(self):
        return content_hash(self.schema_version, self)


class OutcomeCandidateV1(MarketArtifact):
    schema_version: Literal["OUTCOME_CANDIDATE_V1"] = "OUTCOME_CANDIDATE_V1"
    analysis: ArtifactRefV1
    fusion: ArtifactRefV1
    unit_id: Identifier
    match_id: Identifier
    market_key: MarketKeyV2
    outcome: OutcomeKeyV1
    probability: Probability
    distribution_hash: Hash
    fixed_bonus: Price
    snapshot: ArtifactRefV1
    minimum_ev: Decimal = Field(ge=0)
    ev: Decimal
    break_even_probability: Probability
    status: Literal["ELIGIBLE", "REJECTED"]
    rejection_code: Literal["EV_BELOW_THRESHOLD", "SALE_NOT_OPEN"] | None

    @model_validator(mode="after")
    @fixed_decimal(28)
    def economics(self):
        from football_system.domain.services.probability import (
            selection_ev,
            quantize_probability,
        )

        if (
            self.outcome not in self.market_key.catalog
            or self.ev != selection_ev(self.probability, self.fixed_bonus)
            or self.break_even_probability != quantize_probability(1 / self.fixed_bonus)
        ):
            raise ValueError("outcome EV/price math or taxonomy mismatch")
        if (self.status == "ELIGIBLE") != (self.rejection_code is None) or (
            self.status == "ELIGIBLE" and self.ev < self.minimum_ev
        ):
            raise ValueError("outcome eligibility evidence mismatch")
        return self

    @property
    def logical_key(self):
        return content_hash(
            "OUTCOME_IDENTITY_V1",
            [self.match_id, self.market_key.canonical, self.outcome.value],
        )


class StrategySourceV2(MarketArtifact):
    schema_version: Literal["STRATEGY_SOURCE_V2"] = "STRATEGY_SOURCE_V2"
    analysis: MultiMarketAnalysisV1
    review: ImportedReviewV4
    fusion: GenericFusionRunV1
    budget_fen: Money
    selections: tuple[OutcomeCandidateV1, ...] = Field(max_length=1984)

    @model_validator(mode="after")
    def lineage(self):
        from football_system.domain.services.review_v4 import fuse_v4
        from football_system.domain.services.strategy_pass_v2 import outcome_candidates

        if self.budget_fen not in self.analysis.budgets_fen or self.fusion != fuse_v4(
            self.analysis, self.review, self.fusion.policy
        ):
            raise ValueError("V2 source must retain original budget/fusion lineage")
        if self.selections != outcome_candidates(self.analysis, self.fusion):
            raise ValueError("V2 selection source differs from original EV gate")
        return self


class MatchChoiceRequestV1(MultiMarketModel):
    match_id: Identifier
    market_key: MarketKeyV2
    outcomes: tuple[OutcomeKeyV1, ...] = Field(min_length=1, max_length=31)

    @model_validator(mode="after")
    def catalog(self):
        if self.outcomes != tuple(
            o for o in self.market_key.catalog if o in self.outcomes
        ):
            raise ValueError("choice outcomes must be unique canonical members")
        return self


class TicketRequestV2(MultiMarketModel):
    pass_type: PassType
    choices: tuple[MatchChoiceRequestV1, ...] = Field(min_length=2, max_length=4)
    role: TicketRole | None = None

    @model_validator(mode="after")
    def matches(self):
        ids = tuple(c.match_id for c in self.choices)
        if len(ids) != PASS_SHAPES[self.pass_type][0] or ids != tuple(sorted(set(ids))):
            raise ValueError(
                "one choice set per distinct ordered match; same-match cross-market compound forbidden"
            )
        return self


class MatchChoiceSetV1(MarketArtifact):
    schema_version: Literal["MATCH_CHOICE_SET_V1"] = "MATCH_CHOICE_SET_V1"
    match_id: Identifier
    market_key: MarketKeyV2
    candidates: tuple[OutcomeCandidateV1, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def qualified(self):
        outcomes = tuple(c.outcome for c in self.candidates)
        if outcomes != tuple(
            o for o in self.market_key.catalog if o in outcomes
        ) or any(
            c.match_id != self.match_id
            or c.market_key != self.market_key
            or c.status != "ELIGIBLE"
            or c.ev < 0
            for c in self.candidates
        ):
            raise ValueError(
                "choice set requires independently eligible same-market outcomes"
            )
        return self

    @property
    def logical_key(self):
        return content_hash(
            "MATCH_CHOICE_IDENTITY_V1",
            [
                self.match_id,
                self.market_key.canonical,
                [c.outcome.value for c in self.candidates],
            ],
        )


class ExpandedAtomicBetV2(MarketArtifact):
    schema_version: Literal["EXPANDED_ATOMIC_BET_V2"] = "EXPANDED_ATOMIC_BET_V2"
    legs: tuple[OutcomeCandidateV1, ...] = Field(min_length=2, max_length=4)
    base_stake_fen: Literal[200] = 200
    gross_payout_fen: Money
    expected_gross_payout_fen: Decimal

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.strategy_pass_v2 import atomic_values

        if len({c.match_id for c in self.legs}) != len(self.legs) or any(
            c.status != "ELIGIBLE" or c.ev < 0 for c in self.legs
        ):
            raise ValueError(
                "expanded atomic legs must be qualified and from distinct matches"
            )
        expected = atomic_values(self.legs)
        if (
            self.gross_payout_fen != expected["gross_payout_fen"]
            or self.expected_gross_payout_fen != expected["expected_gross_payout_fen"]
        ):
            raise ValueError(
                "expanded atomic payout differs from frozen 200-fen policy"
            )
        return self

    @property
    def logical_key(self):
        return content_hash("ATOMIC_IDENTITY_V2", [c.logical_key for c in self.legs])


class SystemTicketCandidateV2(MarketArtifact):
    schema_version: Literal["SYSTEM_TICKET_CANDIDATE_V2"] = "SYSTEM_TICKET_CANDIDATE_V2"
    source: ArtifactRefV1
    profile: StrategyProfileV2
    pass_type: PassType
    choice_sets: tuple[MatchChoiceSetV1, ...] = Field(min_length=2, max_length=4)
    atomic_bets: tuple[ExpandedAtomicBetV2, ...] = Field(min_length=1, max_length=256)
    expanded_atomic_bet_count: int = Field(ge=1, le=256, strict=True)
    unit_stake_fen: Money
    max_payout_fen: Money
    expected_gross_payout_fen: Decimal
    expected_roi: Decimal
    reconstructable_state_count: int = Field(ge=1, le=256, strict=True)
    payout_state_policy: Literal["EXCLUSIVE_CHOICE_OR_REST_CARTESIAN_V1"] = (
        "EXCLUSIVE_CHOICE_OR_REST_CARTESIAN_V1"
    )
    expected_value_assumption: Literal["EXISTING_INDEPENDENT_LEGS_LINEARITY_V1"] = (
        "EXISTING_INDEPENDENT_LEGS_LINEARITY_V1"
    )

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.strategy_pass_v2 import candidate_values

        expected = candidate_values(
            self.source, self.profile, self.pass_type, self.choice_sets
        )
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("V2 ticket decomposition/payout replay mismatch")
        return self

    @property
    def logical_key(self):
        return content_hash(
            "SYSTEM_TICKET_IDENTITY_V2",
            [self.pass_type, [c.logical_key for c in self.choice_sets]],
        )


class MatchChoiceFailureV2(MultiMarketModel):
    match_id: Identifier
    failed_choice_sets: tuple[MatchChoiceRequestV1, ...]
    home_goals: int = Field(ge=0, le=28, strict=True)
    away_goals: int = Field(ge=0, le=28, strict=True)


class HedgeWitnessV2(MultiMarketModel):
    failure: MatchChoiceFailureV2
    surviving_atomic_bet_id: Identifier
    independent_outcome_keys: tuple[str, ...]


class SystemTicketV2(MarketArtifact):
    schema_version: Literal["SYSTEM_TICKET_V2"] = "SYSTEM_TICKET_V2"
    candidate: SystemTicketCandidateV2
    ticket_no: int = Field(ge=1, le=8, strict=True)
    role: TicketRole
    hedge_witness: HedgeWitnessV2 | None
    multiplier: int = Field(ge=1, le=50, strict=True)
    stake_fen: Money
    max_payout_fen: Money

    @model_validator(mode="after")
    def scaling(self):
        c = self.candidate
        if (
            self.stake_fen != c.unit_stake_fen * self.multiplier
            or self.max_payout_fen != c.max_payout_fen * self.multiplier
            or self.stake_fen > 600000
        ):
            raise ValueError("expanded stake/payout scaling mismatch")
        if (self.role == TicketRole.HEDGE) != (self.hedge_witness is not None):
            raise ValueError("V2 hedge requires witness")
        return self


class StructuralRiskV2(MultiMarketModel):
    basis: Literal["EXPANDED_AND_OR_GRAPH_NOT_STATISTICAL_CORRELATION"] = (
        "EXPANDED_AND_OR_GRAPH_NOT_STATISTICAL_CORRELATION"
    )
    match_exposure_fen: dict[str, int]
    outcome_exposure_fen: dict[str, int]
    atomic_dependency_fen: dict[str, int]
    ticket_overlap: dict[str, Decimal]
    choice_set_overlap: dict[str, Decimal]
    core_dependency: dict[str, Decimal]
    single_outcome_wipeout: tuple[str, ...]
    single_match_choice_set_wipeout: tuple[str, ...]
    match_choice_failure_witnesses: tuple[MatchChoiceFailureV2, ...]
    concentrated: bool


class StrategyPassPlanV2(MarketArtifact):
    schema_version: Literal["STRATEGY_PASS_PLAN_V2"] = "STRATEGY_PASS_PLAN_V2"
    source: StrategySourceV2
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
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("V2 plan differs from deterministic source replay")
        return self
