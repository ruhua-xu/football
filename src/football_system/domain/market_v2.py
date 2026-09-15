"""Additive, ordered market contracts. Legacy market.py remains byte-frozen."""

from __future__ import annotations

import hashlib
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from functools import wraps
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, ValidationInfo, model_validator

from football_system.domain.archive import canonical_json
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    stable_id,
)
from football_system.domain.market import PROBABILITY_TOLERANCE, ThreeWayProbability

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
QUANTUM = Decimal("0.000000000001")
_FACTORY = object()


def fixed_decimal(precision):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def reject_float(value):
    if isinstance(value, float):
        raise ValueError("binary float is forbidden in multi-market contracts")
    if isinstance(value, DomainModel):
        reject_float(value.model_dump(mode="python"))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, bool) and (
                key.endswith("_fen") or key in {"multiplier", "max_multiplier"}
            ):
                raise ValueError("money/multiplier values cannot be booleans")
            reject_float(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_float(item)
    return value


def decimal_value(value):
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("use Decimal or a decimal string")
    return value


Probability = Annotated[
    Decimal,
    BeforeValidator(decimal_value),
    Field(ge=0, le=1, max_digits=29, decimal_places=28, allow_inf_nan=False),
]
Price = Annotated[
    Decimal,
    BeforeValidator(decimal_value),
    Field(gt=1, max_digits=18, decimal_places=6, allow_inf_nan=False),
]


def content_hash(tag, value):
    return hashlib.sha256(
        tag.encode() + b"\0" + canonical_json(value).encode()
    ).hexdigest()


class MultiMarketModel(DomainModel):
    @model_validator(mode="before")
    @classmethod
    def no_float(cls, value):
        return reject_float(value)


class MarketArtifact(MultiMarketModel):
    schema_version: str
    artifact_id: Identifier
    content_hash: Hash

    @classmethod
    def freeze(cls, **fields):
        def check(value):
            if isinstance(value, dict):
                if value.get("artifact_id") == "__SEAL__":
                    raise ValueError("nested unsealed artifact")
                for item in value.values():
                    check(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    check(item)
            elif isinstance(value, DomainModel):
                check(value.model_dump(mode="python"))

        check(fields)
        if "artifact_id" in fields or "content_hash" in fields:
            raise ValueError(
                "freeze computes identity; use model_validate for existing artifacts"
            )
        return cls.model_validate(
            dict(fields, artifact_id="__SEAL__", content_hash="0" * 64),
            context={_FACTORY: cls},
        )

    @model_validator(mode="after")
    def verify_seal(self, info: ValidationInfo) -> Self:
        digest = content_hash(
            self.schema_version,
            self.model_dump(mode="json", exclude={"artifact_id", "content_hash"}),
        )
        identity = stable_id(self.schema_version, digest)
        if (
            info.context
            and info.context.get(_FACTORY) is type(self)
            and self.artifact_id == "__SEAL__"
        ):
            object.__setattr__(self, "artifact_id", identity)
            object.__setattr__(self, "content_hash", digest)
        elif (self.artifact_id, self.content_hash) != (identity, digest):
            raise ValueError("multi-market artifact seal mismatch")
        return self


def revalidate(value):
    return type(value).model_validate(value.model_dump(mode="python"))


class MarketTypeV1(StrEnum):
    THREE_WAY = "THREE_WAY"
    HANDICAP_THREE_WAY = "HANDICAP_THREE_WAY"
    TOTAL_GOALS = "TOTAL_GOALS"
    CORRECT_SCORE = "CORRECT_SCORE"


class OutcomeKeyV1(StrEnum):
    HOME_WIN = "HOME_WIN"
    DRAW = "DRAW"
    AWAY_WIN = "AWAY_WIN"
    GOALS_0 = "GOALS_0"
    GOALS_1 = "GOALS_1"
    GOALS_2 = "GOALS_2"
    GOALS_3 = "GOALS_3"
    GOALS_4 = "GOALS_4"
    GOALS_5 = "GOALS_5"
    GOALS_6 = "GOALS_6"
    GOALS_7_PLUS = "GOALS_7_PLUS"
    SCORE_1_0 = "SCORE_1_0"
    SCORE_2_0 = "SCORE_2_0"
    SCORE_2_1 = "SCORE_2_1"
    SCORE_3_0 = "SCORE_3_0"
    SCORE_3_1 = "SCORE_3_1"
    SCORE_3_2 = "SCORE_3_2"
    SCORE_4_0 = "SCORE_4_0"
    SCORE_4_1 = "SCORE_4_1"
    SCORE_4_2 = "SCORE_4_2"
    SCORE_5_0 = "SCORE_5_0"
    SCORE_5_1 = "SCORE_5_1"
    SCORE_5_2 = "SCORE_5_2"
    HOME_OTHER = "HOME_OTHER"
    SCORE_0_0 = "SCORE_0_0"
    SCORE_1_1 = "SCORE_1_1"
    SCORE_2_2 = "SCORE_2_2"
    SCORE_3_3 = "SCORE_3_3"
    DRAW_OTHER = "DRAW_OTHER"
    SCORE_0_1 = "SCORE_0_1"
    SCORE_0_2 = "SCORE_0_2"
    SCORE_1_2 = "SCORE_1_2"
    SCORE_0_3 = "SCORE_0_3"
    SCORE_1_3 = "SCORE_1_3"
    SCORE_2_3 = "SCORE_2_3"
    SCORE_0_4 = "SCORE_0_4"
    SCORE_1_4 = "SCORE_1_4"
    SCORE_2_4 = "SCORE_2_4"
    SCORE_0_5 = "SCORE_0_5"
    SCORE_1_5 = "SCORE_1_5"
    SCORE_2_5 = "SCORE_2_5"
    AWAY_OTHER = "AWAY_OTHER"


THREE_CATALOG = tuple(OutcomeKeyV1(x) for x in ("HOME_WIN", "DRAW", "AWAY_WIN"))
GOALS_CATALOG = tuple(OutcomeKeyV1(f"GOALS_{i}") for i in range(7)) + (
    OutcomeKeyV1.GOALS_7_PLUS,
)
SCORE_CATALOG = tuple(
    o
    for o in OutcomeKeyV1
    if o.value.startswith("SCORE_") or o.value.endswith("_OTHER")
)
CATALOGS = {
    MarketTypeV1.THREE_WAY: THREE_CATALOG,
    MarketTypeV1.HANDICAP_THREE_WAY: THREE_CATALOG,
    MarketTypeV1.TOTAL_GOALS: GOALS_CATALOG,
    MarketTypeV1.CORRECT_SCORE: SCORE_CATALOG,
}


class MarketKeyV2(MultiMarketModel):
    schema_version: Literal["MARKET_KEY_V2"] = "MARKET_KEY_V2"
    market_type: MarketTypeV1
    home_handicap: int | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def parameters(self):
        if (self.market_type == MarketTypeV1.HANDICAP_THREE_WAY) != (
            self.home_handicap is not None
        ):
            raise ValueError(
                "home_handicap is required exclusively for HANDICAP_THREE_WAY"
            )
        return self

    @property
    def canonical(self):
        return self.market_type.value + (
            f":HOME{self.home_handicap:+d}" if self.home_handicap is not None else ""
        )

    @property
    def catalog(self):
        return CATALOGS[self.market_type]

    @property
    def market_hash(self):
        return content_hash(self.schema_version, self)


def outcome_label(outcome: OutcomeKeyV1) -> str:
    labels = {
        "HOME_WIN": "主胜",
        "DRAW": "平局",
        "AWAY_WIN": "客胜",
        "HOME_OTHER": "胜其他",
        "DRAW_OTHER": "平其他",
        "AWAY_OTHER": "负其他",
        "GOALS_7_PLUS": "7+球",
    }
    if outcome.value in labels:
        return labels[outcome.value]
    if outcome.value.startswith("SCORE_"):
        return outcome.value[6:].replace("_", "-")
    return outcome.value[6:] + "球"


def settle_market(
    market: MarketKeyV2, home_goals: int, away_goals: int
) -> OutcomeKeyV1:
    revalidate(market)
    if any(type(n) is not int or n < 0 for n in (home_goals, away_goals)):
        raise ValueError("regular-time goals must be nonnegative integers")
    if market.market_type == MarketTypeV1.TOTAL_GOALS:
        total = home_goals + away_goals
        return (
            OutcomeKeyV1(f"GOALS_{total}") if total < 7 else OutcomeKeyV1.GOALS_7_PLUS
        )
    if market.market_type == MarketTypeV1.CORRECT_SCORE:
        name = f"SCORE_{home_goals}_{away_goals}"
        if name in OutcomeKeyV1._value2member_map_:
            return OutcomeKeyV1(name)
        return (
            OutcomeKeyV1.HOME_OTHER
            if home_goals > away_goals
            else OutcomeKeyV1.AWAY_OTHER
            if home_goals < away_goals
            else OutcomeKeyV1.DRAW_OTHER
        )
    adjusted = home_goals + (market.home_handicap or 0)
    return (
        OutcomeKeyV1.HOME_WIN
        if adjusted > away_goals
        else OutcomeKeyV1.AWAY_WIN
        if adjusted < away_goals
        else OutcomeKeyV1.DRAW
    )


class MarketOutcomeProbabilityV1(MultiMarketModel):
    outcome: OutcomeKeyV1
    probability: Probability


class MarketProbabilityDistributionV1(MultiMarketModel):
    schema_version: Literal["MARKET_PROBABILITY_DISTRIBUTION_V1"] = (
        "MARKET_PROBABILITY_DISTRIBUTION_V1"
    )
    market_key: MarketKeyV2
    outcomes: tuple[MarketOutcomeProbabilityV1, ...] = Field(
        min_length=3, max_length=31
    )

    @model_validator(mode="after")
    def complete(self):
        if tuple(x.outcome for x in self.outcomes) != self.market_key.catalog:
            raise ValueError("distribution must equal the full ordered market catalog")
        with localcontext() as ctx:
            ctx.prec = 80
            if (
                abs(sum((x.probability for x in self.outcomes), Decimal(0)) - 1)
                > PROBABILITY_TOLERANCE
            ):
                raise ValueError("market probabilities do not sum to 1")
        return self

    def probability(self, outcome):
        return next(x.probability for x in self.outcomes if x.outcome == outcome)

    @property
    def distribution_hash(self):
        return content_hash(self.schema_version, self)

    @classmethod
    def from_three_way(cls, probabilities: ThreeWayProbability):
        revalidate(probabilities)
        return cls(
            market_key=MarketKeyV2(market_type=MarketTypeV1.THREE_WAY),
            outcomes=tuple(
                MarketOutcomeProbabilityV1(outcome=s.value, probability=p)
                for s, p in probabilities.items()
            ),
        )

    def to_three_way(self):
        if self.market_key.market_type != MarketTypeV1.THREE_WAY:
            raise ValueError("only explicit THREE_WAY adapter is supported")
        return ThreeWayProbability(
            **{o.outcome.value.lower(): o.probability for o in self.outcomes}
        )


def distribution(market, values, *, normalize=False):
    reject_float(values)
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        values = tuple(Decimal(v) for v in values)
        if len(values) != len(market.catalog) or any(
            not x.is_finite() or x < 0 for x in values
        ):
            raise ValueError("invalid generic probability vector")
        total = sum(values, Decimal(0))
        if normalize:
            if total <= 0:
                raise ValueError("empty probability mass")
            values = tuple(v / total for v in values)
        elif abs(total - 1) > PROBABILITY_TOLERANCE:
            raise ValueError("unclosed probability vector")
        rounded = [v.quantize(QUANTUM, rounding=ROUND_HALF_EVEN) for v in values]
        index = max(range(len(values)), key=lambda i: values[i])
        rounded[index] += 1 - sum(rounded, Decimal(0))
    return MarketProbabilityDistributionV1(
        market_key=market,
        outcomes=tuple(
            MarketOutcomeProbabilityV1(outcome=o, probability=p)
            for o, p in zip(market.catalog, rounded, strict=True)
        ),
    )


class MarketPriceV1(MultiMarketModel):
    outcome: OutcomeKeyV1
    price: Price


class MarketOddsSnapshotV2(MarketArtifact):
    schema_version: Literal["MARKET_ODDS_SNAPSHOT_V2"] = "MARKET_ODDS_SNAPSHOT_V2"
    match_id: Identifier
    market_key: MarketKeyV2
    prices: tuple[MarketPriceV1, ...] = Field(min_length=3, max_length=31)
    provider_code: Identifier
    source_identity: Identifier
    source_artifact_id: Identifier
    source_artifact_hash: Hash
    bookmaker_code: Identifier
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    semantics: Literal["EXACT_ORDERED_MARKET_V1"] = "EXACT_ORDERED_MARKET_V1"

    @model_validator(mode="after")
    def catalog_and_time(self):
        if tuple(p.outcome for p in self.prices) != self.market_key.catalog:
            raise ValueError("odds require the complete ordered market catalog")
        if not self.captured_at_utc <= self.available_at_utc <= self.ingested_at_utc:
            raise ValueError("invalid odds observation timeline")
        return self


class SportteryFixedBonusSnapshotV2(MarketOddsSnapshotV2):
    schema_version: Literal["SPORTTERY_FIXED_BONUS_SNAPSHOT_V2"] = (
        "SPORTTERY_FIXED_BONUS_SNAPSHOT_V2"
    )
    bookmaker_code: Literal["SPORTTERY"] = "SPORTTERY"
    sale_status: Literal["OPEN", "CLOSED", "UNKNOWN"]


class MarketConsensusV2(MarketArtifact):
    schema_version: Literal["MARKET_CONSENSUS_V2"] = "MARKET_CONSENSUS_V2"
    match_id: Identifier
    market_key: MarketKeyV2
    decision_cutoff: UtcDateTime
    snapshots: tuple[MarketOddsSnapshotV2, ...] = Field(max_length=64)
    status: Literal["AVAILABLE", "MARKET_UNAVAILABLE"]
    probabilities: MarketProbabilityDistributionV1 | None
    policy: Literal["INDEPENDENT_DEVIG_OUTCOME_MEDIAN_NORMALIZE_V2"] = (
        "INDEPENDENT_DEVIG_OUTCOME_MEDIAN_NORMALIZE_V2"
    )

    @model_validator(mode="after")
    def replay(self):
        expected = consensus_probabilities(
            self.match_id, self.market_key, self.snapshots, self.decision_cutoff
        )
        if self.probabilities != expected or self.status != (
            "AVAILABLE" if expected else "MARKET_UNAVAILABLE"
        ):
            raise ValueError("market consensus replay mismatch")
        return self


def consensus_probabilities(match_id, market, snapshots, cutoff):
    if tuple(s.artifact_id for s in snapshots) != tuple(
        sorted({s.artifact_id for s in snapshots})
    ):
        raise ValueError("consensus snapshots must be unique and ordered")
    if len({s.bookmaker_code for s in snapshots}) != len(snapshots):
        raise ValueError("consensus requires one exact snapshot per bookmaker")
    if not snapshots:
        return None
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        books = []
        for s in snapshots:
            revalidate(s)
            if (
                type(s) is not MarketOddsSnapshotV2
                or s.match_id != match_id
                or s.market_key != market
                or s.ingested_at_utc > cutoff
            ):
                raise ValueError(
                    "consensus source has different semantics/match/cutoff"
                )
            inverse = [1 / p.price for p in s.prices]
            total = sum(inverse)
            books.append([p / total for p in inverse])
        medians = []
        for i in range(len(market.catalog)):
            values = sorted(book[i] for book in books)
            n = len(values)
            medians.append(
                values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
            )
        return distribution(market, medians, normalize=True)


def market_consensus(match_id, market, snapshots, cutoff):
    snapshots = tuple(sorted(snapshots, key=lambda s: s.artifact_id))
    p = consensus_probabilities(match_id, market, snapshots, cutoff)
    return MarketConsensusV2.freeze(
        match_id=match_id,
        market_key=market,
        decision_cutoff=cutoff,
        snapshots=snapshots,
        status="AVAILABLE" if p else "MARKET_UNAVAILABLE",
        probabilities=p,
    )
