"""Versioned system tickets; legacy 0.6 Ticket/Portfolio bytes remain separate.

Payout states are amounts, not a modeled return distribution. Expected payout
uses the existing independent-leg product convention and linearity across bets.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from enum import StrEnum
from itertools import combinations, product
from math import prod
from typing import Literal, Self

from pydantic import Field, model_validator

from football_system.domain.archive import canonical_json
from football_system.domain.betting import (
    CandidateStatus,
    PassType,
    PortfolioConstraints,
    SelectionCandidate,
    SportteryRules,
)
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.market import SelectionKey
from football_system.domain.match import SaleStatus, SportteryBonusSnapshot
from football_system.domain.services.payout import (
    calculate_stake_fen,
    official_gross_payout_fen,
)
from football_system.domain.services.probability import (
    quantize_metric,
    quantize_probability,
    selection_ev,
)

OUTCOMES = (SelectionKey.HOME_WIN, SelectionKey.DRAW, SelectionKey.AWAY_WIN)
PASS_SHAPES = {
    PassType.TWO_FOLD_ONE: (2, (2,)),
    PassType.THREE_FOLD_FOUR: (3, (2, 3)),
    PassType.FOUR_FOLD_ELEVEN: (4, (2, 3, 4)),
}


def digest(tag: str, value: object) -> str:
    return hashlib.sha256(
        tag.encode() + b"\0" + canonical_json(value).encode()
    ).hexdigest()


def fresh(value):
    return type(value).model_validate(value.model_dump(mode="python"))


class TicketRole(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    HEDGE = "HEDGE"
    LONGSHOT = "LONGSHOT"


class StrategyProfileV1(DomainModel):
    schema_version: Literal["STRATEGY_PROFILE_V1"] = "STRATEGY_PROFILE_V1"
    name: Identifier = "FOCUSED_PRIMARY_WITH_QUALIFIED_DIVERSIFIER"
    version: Literal["1"] = "1"
    pass_types: tuple[PassType, ...] = tuple(PASS_SHAPES)
    preferred_max_tickets: int = Field(default=4, ge=1, le=8, strict=True)
    absolute_max_tickets: int = Field(default=8, ge=1, le=8, strict=True)
    preferred_primary_min: int = Field(default=2, ge=1, le=3, strict=True)
    preferred_primary_max: int = Field(default=3, ge=1, le=3, strict=True)
    allow_no_bet: Literal[True] = True
    max_auxiliary_tickets: Literal[1] = 1
    max_auxiliary_budget_ratio: Decimal = Field(
        default=Decimal("0.10"), gt=0, le=Decimal("0.25")
    )
    max_ticket_overlap: Decimal = Field(default=Decimal("0.75"), ge=0, le=1)
    max_core_dependency_ratio: Decimal = Field(default=Decimal("0.75"), gt=0, le=1)
    reject_multi_ticket_single_selection_wipeout: Literal[True] = True
    longshot_min_max_return_multiple: Decimal = Field(default=Decimal(6), gt=1)
    protect_core_judgment_not_principal: Literal[True] = True
    no_low_odds_principal_recovery: Literal[True] = True
    max_generated_candidates: int = Field(default=4096, ge=1, le=10000, strict=True)
    max_input_matches: int = Field(default=12, ge=2, le=16, strict=True)

    @model_validator(mode="after")
    def limits(self) -> Self:
        if not self.pass_types or len(set(self.pass_types)) != len(self.pass_types):
            raise ValueError("profile requires unique supported pass types")
        if self.pass_types != tuple(p for p in PASS_SHAPES if p in self.pass_types):
            raise ValueError("profile pass types require canonical order")
        if (
            not self.preferred_primary_min
            <= self.preferred_primary_max
            <= self.preferred_max_tickets
            <= self.absolute_max_tickets
        ):
            raise ValueError("invalid focused ticket preferences")
        return self

    @property
    def profile_hash(self) -> str:
        return digest(self.schema_version, self)

    @property
    def profile_id(self) -> str:
        return stable_id(self.schema_version, self.profile_hash)


class StrategyParentV1(DomainModel):
    kind: Literal["ANALYSIS_RUN", "PORTFOLIO_REVISION"]
    analysis_run_id: Identifier
    analysis_run_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: Identifier
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    fusion_run_id: Identifier | None = None
    fusion_run_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    portfolio_revision_id: Identifier | None = None
    as_of_at_utc: UtcDateTime

    @model_validator(mode="after")
    def lineage(self) -> Self:
        if self.kind == "ANALYSIS_RUN":
            if (
                self.source_id != self.analysis_run_id
                or self.source_hash != self.analysis_run_hash
                or any(
                    (
                        self.fusion_run_id,
                        self.fusion_run_hash,
                        self.portfolio_revision_id,
                    )
                )
            ):
                raise ValueError("base strategy source must be its exact AnalysisRun")
        elif not (
            self.source_id == self.portfolio_revision_id
            and self.fusion_run_id
            and self.fusion_run_hash
        ):
            raise ValueError(
                "revision strategy source requires complete parent lineage"
            )
        return self


class TicketRoleRequestV1(DomainModel):
    pass_type: PassType
    selection_ids: tuple[Identifier, ...]
    role: TicketRole

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if (
            self.selection_ids != tuple(sorted(set(self.selection_ids)))
            or len(self.selection_ids) != PASS_SHAPES[self.pass_type][0]
        ):
            raise ValueError("role request requires exact sorted selection IDs")
        return self


class StrategySourceV1(DomainModel):
    schema_version: Literal["STRATEGY_SOURCE_V1"] = "STRATEGY_SOURCE_V1"
    parent: StrategyParentV1
    selections: tuple[SelectionCandidate, ...]
    odds_snapshots: tuple[SportteryBonusSnapshot, ...]
    budget_fen: int = Field(ge=0, strict=True)
    rules: SportteryRules
    constraints: PortfolioConstraints
    min_selection_ev: Decimal = Field(ge=0)
    min_ticket_roi: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def input_gate(self) -> Self:
        fresh(self.rules)
        fresh(self.constraints)
        if any(
            type(v) is not int
            for v in (
                self.rules.base_stake_fen,
                self.rules.max_multiplier,
                self.rules.max_ticket_stake_fen,
            )
        ):
            raise ValueError("money and multipliers must be integers")
        ids = tuple(x.candidate_id for x in self.selections)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("source selections require sorted unique IDs")
        snapshots = {x.snapshot_id: x for x in self.odds_snapshots}
        if tuple(snapshots) != tuple(sorted(set(snapshots))) or len(snapshots) != len(
            self.odds_snapshots
        ):
            raise ValueError("odds snapshots require unique canonical order")
        logical = set()
        for item in self.selections:
            fresh(item)
            if item.status == CandidateStatus.REJECTED and not item.rejection_code:
                raise ValueError(
                    "rejected source selection requires its original reason"
                )
            snapshot = snapshots.get(item.sporttery_bonus_snapshot_id)
            key = (item.match_id, item.market.canonical, item.selection)
            if key in logical:
                raise ValueError("duplicate equivalent source selection")
            logical.add(key)
            if (
                item.analysis_run_id != self.parent.source_id
                or item.market.canonical != "THREE_WAY"
            ):
                raise ValueError(
                    "strategy selections must retain exact source and THREE_WAY"
                )
            if (
                snapshot is None
                or snapshot.match_id != item.match_id
                or snapshot.market != item.market
            ):
                raise ValueError("selection odds source mismatch")
            fresh(snapshot)
            if (
                max(
                    snapshot.captured_at_utc,
                    snapshot.available_at_utc,
                    snapshot.ingested_at_utc,
                )
                > self.parent.as_of_at_utc
            ):
                raise ValueError("strategy odds source crosses parent decision cutoff")
            if item.fixed_bonus != snapshot.three_way_bonus().for_selection(
                item.selection
            ):
                raise ValueError("selection price differs from frozen odds")
            if item.ev != selection_ev(
                item.probability, item.fixed_bonus
            ) or item.break_even_probability != quantize_probability(
                Decimal(1) / item.fixed_bonus
            ):
                raise ValueError("selection EV/probability gate evidence changed")
            if item.status == CandidateStatus.ELIGIBLE and (
                item.rejection_code is not None
                or item.ev < self.min_selection_ev
                or snapshot.sale_status != SaleStatus.OPEN
            ):
                raise ValueError(
                    "invalid eligible selection; strategy cannot weaken the gate"
                )
        if (
            self.rules.base_stake_fen != 200
            or self.rules.max_multiplier > 50
            or self.rules.max_ticket_stake_fen > 600000
        ):
            raise ValueError("system pass rules exceed the existing money limits")
        return self

    @property
    def source_hash(self) -> str:
        return digest(self.schema_version, self)


class OddsRefV1(DomainModel):
    selection_id: Identifier
    snapshot_id: Identifier
    payload_hash: Identifier
    fixed_bonus: Decimal = Field(gt=1)


class AtomicBetV1(DomainModel):
    atomic_bet_id: Identifier
    parent_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_ids: tuple[Identifier, ...]
    match_ids: tuple[Identifier, ...]
    fixed_bonuses: tuple[Decimal, ...]
    base_stake_fen: Literal[200] = 200
    gross_payout_fen: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def combinations(self) -> Self:
        if (
            not 2 <= len(self.selection_ids) <= 4
            or len(set(self.match_ids)) != len(self.selection_ids)
            or len(self.fixed_bonuses) != len(self.selection_ids)
        ):
            raise ValueError("atomic bet needs distinct matches and exact odds")
        if len(set(self.selection_ids)) != len(self.selection_ids):
            raise ValueError("duplicate atomic selection")
        if self.gross_payout_fen != official_gross_payout_fen(
            self.fixed_bonuses, SportteryRules(version="SPORTTERY_MVP_V1")
        ):
            raise ValueError("atomic payout is not the existing rounded product")
        if self.atomic_bet_id != stable_id(
            "ATOMIC_BET_V1",
            digest("ATOMIC_ID_V1", [self.parent_signature, self.selection_ids]),
        ):
            raise ValueError("atomic bet identity mismatch")
        return self


class GrossPayoutStateV1(DomainModel):
    outcomes: tuple[SelectionKey, ...]
    winning_atomic_bet_ids: tuple[Identifier, ...]
    gross_payout_fen: int = Field(ge=0, strict=True)


def ticket_spec(source_hash, selections, odds_refs, pass_type, rules):
    n, sizes = PASS_SHAPES[pass_type]
    if len(selections) != n or len({s.match_id for s in selections}) != n:
        raise ValueError("pass type requires distinct matches, one outcome per match")
    if tuple(s.candidate_id for s in selections) != tuple(
        sorted(s.candidate_id for s in selections)
    ):
        raise ValueError("ticket selections must have canonical order")
    if any(
        s.status != CandidateStatus.ELIGIBLE
        or s.ev < 0
        or s.rejection_code
        or s.market.canonical != "THREE_WAY"
        for s in selections
    ):
        raise ValueError("ineligible selection cannot enter a pass or hedge")
    if tuple(
        (r.selection_id, r.snapshot_id, r.fixed_bonus) for r in odds_refs
    ) != tuple(
        (s.candidate_id, s.sporttery_bonus_snapshot_id, s.fixed_bonus)
        for s in selections
    ):
        raise ValueError("ticket odds references mismatch")
    signature = digest(
        "SYSTEM_TICKET_EQUIVALENCE_V1",
        {
            "pass_type": pass_type,
            "selections": sorted(
                (s.match_id, s.market.canonical, s.selection.value) for s in selections
            ),
        },
    )
    atomic = tuple(
        AtomicBetV1(
            atomic_bet_id=stable_id(
                "ATOMIC_BET_V1",
                digest(
                    "ATOMIC_ID_V1", [signature, tuple(s.candidate_id for s in combo)]
                ),
            ),
            parent_signature=signature,
            selection_ids=tuple(s.candidate_id for s in combo),
            match_ids=tuple(s.match_id for s in combo),
            fixed_bonuses=tuple(s.fixed_bonus for s in combo),
            gross_payout_fen=official_gross_payout_fen(
                (s.fixed_bonus for s in combo), rules
            ),
        )
        for size in sizes
        for combo in combinations(selections, size)
    )
    states = []
    for outcomes in product(OUTCOMES, repeat=n):
        won = {
            s.candidate_id
            for s, o in zip(selections, outcomes, strict=True)
            if s.selection == o
        }
        winning = tuple(a for a in atomic if set(a.selection_ids) <= won)
        states.append(
            GrossPayoutStateV1(
                outcomes=outcomes,
                winning_atomic_bet_ids=tuple(a.atomic_bet_id for a in winning),
                gross_payout_fen=sum(a.gross_payout_fen for a in winning),
            )
        )
    probabilities = {s.candidate_id: s.probability for s in selections}
    expected = sum(
        (
            quantize_metric(
                Decimal(a.gross_payout_fen)
                * quantize_probability(
                    prod((probabilities[k] for k in a.selection_ids), start=Decimal(1))
                )
            )
            for a in atomic
        ),
        Decimal(0),
    )
    stake = len(atomic) * rules.base_stake_fen
    return dict(
        candidate_id=stable_id("SYSTEM_TICKET_CANDIDATE_V1", source_hash, signature),
        equivalence_hash=signature,
        source_hash=source_hash,
        pass_type=pass_type,
        selections=selections,
        odds_refs=odds_refs,
        rules=rules,
        atomic_bets=atomic,
        payout_states=tuple(states),
        base_stake_fen=stake,
        max_payout_fen=sum(a.gross_payout_fen for a in atomic),
        expected_gross_payout_fen=quantize_metric(expected),
        expected_profit_fen=quantize_metric(expected - Decimal(stake)),
        expected_roi=quantize_metric((expected - Decimal(stake)) / Decimal(stake)),
    )


class SystemTicketCandidateV1(DomainModel):
    schema_version: Literal["SYSTEM_TICKET_CANDIDATE_V1"] = "SYSTEM_TICKET_CANDIDATE_V1"
    candidate_id: Identifier
    equivalence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pass_type: PassType
    selections: tuple[SelectionCandidate, ...]
    odds_refs: tuple[OddsRefV1, ...]
    rules: SportteryRules
    atomic_bets: tuple[AtomicBetV1, ...]
    payout_states: tuple[GrossPayoutStateV1, ...]
    base_stake_fen: int = Field(gt=0, strict=True)
    max_payout_fen: int = Field(gt=0, strict=True)
    expected_gross_payout_fen: Decimal
    expected_profit_fen: Decimal
    expected_roi: Decimal
    payout_policy_version: Literal["CONSTITUENT_ROUND_HALF_EVEN_THEN_MULTIPLY_V1"] = (
        "CONSTITUENT_ROUND_HALF_EVEN_THEN_MULTIPLY_V1"
    )
    expected_value_assumption: Literal["EXISTING_INDEPENDENT_LEGS_LINEARITY_V1"] = (
        "EXISTING_INDEPENDENT_LEGS_LINEARITY_V1"
    )

    @model_validator(mode="after")
    def exact_math(self) -> Self:
        expected = ticket_spec(
            self.source_hash,
            self.selections,
            self.odds_refs,
            self.pass_type,
            self.rules,
        )
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("system ticket decomposition/payout/identity mismatch")
        return self

    @property
    def legs(self):
        return self.selections

    @property
    def ticket_candidate_id(self):
        return self.candidate_id

    @property
    def atomic_bet_count(self):
        return len(self.atomic_bets)


class HedgeWitnessV1(DomainModel):
    failed_core_selection_id: Identifier
    surviving_atomic_bet_id: Identifier
    independently_eligible_selection_ids: tuple[Identifier, ...]
    explanation: Literal["QUALIFIED_NEW_SELECTIONS_CAN_PAY_WHEN_PRIMARY_CORE_FAILS"] = (
        "QUALIFIED_NEW_SELECTIONS_CAN_PAY_WHEN_PRIMARY_CORE_FAILS"
    )


class SystemTicketV1(DomainModel):
    schema_version: Literal["SYSTEM_TICKET_V1"] = "SYSTEM_TICKET_V1"
    ticket_id: Identifier
    ticket_no: int = Field(ge=1, strict=True)
    profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent: StrategyParentV1
    candidate: SystemTicketCandidateV1
    role: TicketRole
    hedge_witness: HedgeWitnessV1 | None = None
    multiplier: int = Field(ge=1, le=50, strict=True)
    stake_fen: int = Field(gt=0, strict=True)
    max_payout_fen: int = Field(gt=0, strict=True)
    gross_payout_states: tuple[GrossPayoutStateV1, ...]
    expected_gross_payout_fen: Decimal
    expected_profit_fen: Decimal

    @model_validator(mode="after")
    def allocation(self) -> Self:
        c = self.candidate
        if self.ticket_id != stable_id(
            "SYSTEM_TICKET_V1",
            self.profile_hash,
            c.candidate_id,
            self.role.value,
            self.multiplier,
        ):
            raise ValueError("system ticket ID does not bind role/stake")
        if (
            self.stake_fen
            != calculate_stake_fen(c.atomic_bet_count, self.multiplier, c.rules)
            or self.max_payout_fen != c.max_payout_fen * self.multiplier
            or self.expected_gross_payout_fen
            != c.expected_gross_payout_fen * self.multiplier
            or self.expected_profit_fen != c.expected_profit_fen * self.multiplier
        ):
            raise ValueError("system ticket stake or payout scaling mismatch")
        expected = tuple(
            s.model_copy(
                update={"gross_payout_fen": s.gross_payout_fen * self.multiplier}
            )
            for s in c.payout_states
        )
        if self.gross_payout_states != expected or (
            (self.role == TicketRole.HEDGE) != (self.hedge_witness is not None)
        ):
            raise ValueError("system ticket states/role evidence mismatch")
        return self


class StrategyDecisionV1(DomainModel):
    candidate_id: Identifier
    code: Identifier


class StructuralRiskV1(DomainModel):
    match_exposure_fen: dict[str, int]
    selection_exposure_fen: dict[str, int]
    atomic_selection_dependency_fen: dict[str, int]
    core_selection_dependency_ratio: dict[str, Decimal]
    ticket_overlap: dict[str, Decimal]
    single_selection_wipeout_ids: tuple[Identifier, ...]
    concentrated: bool
    basis: Literal["STRUCTURAL_NOT_STATISTICAL_CORRELATION"] = (
        "STRUCTURAL_NOT_STATISTICAL_CORRELATION"
    )


class StrategyPassContentV1(DomainModel):
    schema_version: Literal["STRATEGY_PASS_PLAN_V1"] = "STRATEGY_PASS_PLAN_V1"
    profile: StrategyProfileV1
    source: StrategySourceV1
    role_requests: tuple[TicketRoleRequestV1, ...]
    candidates: tuple[SystemTicketCandidateV1, ...]
    tickets: tuple[SystemTicketV1, ...]
    decisions: tuple[StrategyDecisionV1, ...]
    risk: StructuralRiskV1
    total_stake_fen: int = Field(ge=0, strict=True)
    cash_fen: int = Field(ge=0, strict=True)
    status: Literal["ALLOCATED", "NO_BET"]
    no_bet_reason: str | None


class StrategyPassPlanV1(StrategyPassContentV1):
    plan_id: Identifier
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def freeze(cls, **content):
        payload = StrategyPassContentV1.model_validate(content)
        h = digest(
            "STRATEGY_PASS_PLAN_V1",
            payload.model_dump(mode="json", exclude={"schema_version"}),
        )
        return cls(
            plan_id=stable_id("STRATEGY_PASS_PLAN_V1", h),
            plan_hash=h,
            **payload.model_dump(mode="python"),
        )

    @model_validator(mode="after")
    def seal(self) -> Self:
        content = self.model_dump(
            mode="json", exclude={"schema_version", "plan_id", "plan_hash"}
        )
        h = digest(self.schema_version, content)
        if self.plan_hash != h or self.plan_id != stable_id(self.schema_version, h):
            raise ValueError("strategy plan seal mismatch")
        from football_system.domain.services.strategy_pass import validate_plan

        validate_plan(self)
        return self
