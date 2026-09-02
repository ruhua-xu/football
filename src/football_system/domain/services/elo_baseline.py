from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from enum import StrEnum
from typing import ClassVar, Self

from pydantic import Field, field_validator, model_validator

from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    normalize_utc,
)
from football_system.domain.market import ThreeWayProbability

ELO_THREE_WAY_BASELINE_V1 = "ELO_THREE_WAY_BASELINE_V1"
BASELINE_UNCALIBRATED = "BASELINE_UNCALIBRATED"
MODEL_VERSION = "1"

ELO_RATING_SCALE = Decimal("400")
RATING_QUANTUM = Decimal("0.000000000001")
PROBABILITY_QUANTUM = Decimal("0.000000000001")


class EloBaselineConfig(DomainModel):
    """Fixed, user-supplied Elo parameters; no fitting occurs in this service."""

    initial_rating: Decimal = Field(default=Decimal("1500"), gt=0)
    k_factor: Decimal = Field(default=Decimal("20"), gt=0)
    home_advantage: Decimal = Field(default=Decimal("100"), ge=0)
    season_regression_factor: Decimal = Field(
        default=Decimal("0.75"),
        ge=0,
        le=1,
        description="Share of the prior rating deviation retained next season.",
    )
    draw_probability: Decimal = Field(default=Decimal("0.25"), ge=0, lt=1)
    minimum_prior_matches: int = Field(default=5, ge=0, strict=True)

    @field_validator(
        "initial_rating",
        "k_factor",
        "home_advantage",
        "season_regression_factor",
        "draw_probability",
    )
    @classmethod
    def validate_finite_decimals(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Elo configuration values must be finite")
        return value

    @property
    def config_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EloRegularTimeResult(DomainModel):
    """A final regular-time score and the time at which it became usable."""

    match_id: Identifier
    season_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    home_goals: int = Field(ge=0, strict=True)
    away_goals: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.home_team_id == self.away_team_id:
            raise ValueError("home and away teams must differ")
        if self.available_at_utc < self.kickoff_at_utc:
            raise ValueError("result cannot be available before kickoff")
        return self


class EloPredictionRequest(DomainModel):
    match_id: Identifier
    season_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    cutoff_at_utc: UtcDateTime

    @model_validator(mode="after")
    def validate_prediction_timeline(self) -> Self:
        if self.home_team_id == self.away_team_id:
            raise ValueError("home and away teams must differ")
        if self.cutoff_at_utc > self.kickoff_at_utc:
            raise ValueError("prediction cutoff cannot be after kickoff")
        return self


class EloTeamState(DomainModel):
    team_id: Identifier
    rating: Decimal
    prior_matches: int = Field(ge=0, strict=True)

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Elo rating must be finite")
        return value


class EloBaselineState(DomainModel):
    model_name: str = ELO_THREE_WAY_BASELINE_V1
    model_version: str = MODEL_VERSION
    calibration_label: str = BASELINE_UNCALIBRATED
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_at_utc: UtcDateTime
    season_id: Identifier | None = None
    teams: tuple[EloTeamState, ...] = ()
    training_match_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.model_name != ELO_THREE_WAY_BASELINE_V1:
            raise ValueError("unexpected Elo model name")
        if self.model_version != MODEL_VERSION:
            raise ValueError("unexpected Elo model version")
        if self.calibration_label != BASELINE_UNCALIBRATED:
            raise ValueError("unexpected Elo calibration label")
        team_ids = tuple(team.team_id for team in self.teams)
        if len(team_ids) != len(set(team_ids)):
            raise ValueError("Elo team state IDs must be unique")
        if team_ids != tuple(sorted(team_ids)):
            raise ValueError("Elo team states must use stable team-ID ordering")
        if len(self.training_match_ids) != len(set(self.training_match_ids)):
            raise ValueError("Elo training match IDs must be unique")
        return self

    def for_team(self, team_id: str) -> EloTeamState | None:
        return next((team for team in self.teams if team.team_id == team_id), None)


class EloPredictionStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class EloUnavailableReason(StrEnum):
    INSUFFICIENT_PRIOR_MATCHES = "INSUFFICIENT_PRIOR_MATCHES"


class EloBaselinePrediction(DomainModel):
    model_name: str = ELO_THREE_WAY_BASELINE_V1
    model_version: str = MODEL_VERSION
    calibration_label: str = BASELINE_UNCALIBRATED
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    match_id: Identifier
    season_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    cutoff_at_utc: UtcDateTime
    status: EloPredictionStatus
    reason: EloUnavailableReason | None = None
    probabilities: ThreeWayProbability | None = None
    insufficient_history_team_ids: tuple[Identifier, ...] = ()
    home_rating: Decimal
    away_rating: Decimal
    home_prior_matches: int = Field(ge=0, strict=True)
    away_prior_matches: int = Field(ge=0, strict=True)
    training_match_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_prediction(self) -> Self:
        if self.model_name != ELO_THREE_WAY_BASELINE_V1:
            raise ValueError("unexpected Elo model name")
        if self.model_version != MODEL_VERSION:
            raise ValueError("unexpected Elo model version")
        if self.calibration_label != BASELINE_UNCALIBRATED:
            raise ValueError("unexpected Elo calibration label")
        if len(self.training_match_ids) != len(set(self.training_match_ids)):
            raise ValueError("Elo training match IDs must be unique")
        insufficient = self.insufficient_history_team_ids
        if len(insufficient) != len(set(insufficient)):
            raise ValueError("insufficient-history team IDs must be unique")
        if not set(insufficient).issubset({self.home_team_id, self.away_team_id}):
            raise ValueError("insufficient-history teams must belong to the prediction")
        if self.status == EloPredictionStatus.AVAILABLE:
            if self.probabilities is None:
                raise ValueError("available Elo prediction requires probabilities")
            if self.reason is not None or insufficient:
                raise ValueError("available Elo prediction cannot have a failure reason")
        else:
            if self.probabilities is not None:
                raise ValueError("unavailable Elo prediction cannot have probabilities")
            if self.reason is None or not insufficient:
                raise ValueError("unavailable Elo prediction requires an explicit reason")
        return self


@dataclass(frozen=True, slots=True)
class EloThreeWayBaseline:
    config: EloBaselineConfig = field(default_factory=EloBaselineConfig)

    model_name: ClassVar[str] = ELO_THREE_WAY_BASELINE_V1
    model_version: ClassVar[str] = MODEL_VERSION
    calibration_label: ClassVar[str] = BASELINE_UNCALIBRATED

    @property
    def config_hash(self) -> str:
        return self.config.config_hash

    def rebuild_state(
        self,
        historical_results: Iterable[EloRegularTimeResult],
        cutoff_at_utc: datetime,
        *,
        target_season_id: str | None = None,
    ) -> EloBaselineState:
        cutoff = normalize_utc(cutoff_at_utc)
        if target_season_id is not None and not target_season_id.strip():
            raise ValueError("target season ID must not be blank")
        ordered_results = _visible_results(historical_results, cutoff)
        ratings: dict[str, Decimal] = {}
        prior_matches: dict[str, int] = {}
        training_match_ids: list[str] = []
        current_season_id: str | None = None
        seen_season_ids: set[str] = set()

        for result in ordered_results:
            if current_season_id is None:
                current_season_id = result.season_id
                seen_season_ids.add(result.season_id)
            elif result.season_id != current_season_id:
                if result.season_id in seen_season_ids:
                    raise ValueError(
                        "season IDs must form contiguous chronological blocks"
                    )
                _regress_ratings(ratings, self.config)
                current_season_id = result.season_id
                seen_season_ids.add(result.season_id)

            _apply_result(ratings, prior_matches, result, self.config)
            training_match_ids.append(result.match_id)

        if target_season_id is not None:
            if current_season_id is None:
                current_season_id = target_season_id
            elif target_season_id != current_season_id:
                if target_season_id in seen_season_ids:
                    raise ValueError("target season precedes the rebuilt Elo state")
                _regress_ratings(ratings, self.config)
                current_season_id = target_season_id

        return EloBaselineState(
            config_hash=self.config_hash,
            cutoff_at_utc=cutoff,
            season_id=current_season_id,
            teams=tuple(
                EloTeamState(
                    team_id=team_id,
                    rating=ratings[team_id],
                    prior_matches=prior_matches[team_id],
                )
                for team_id in sorted(ratings)
            ),
            training_match_ids=tuple(training_match_ids),
        )

    def predict(
        self,
        request: EloPredictionRequest,
        historical_results: Iterable[EloRegularTimeResult],
    ) -> EloBaselinePrediction:
        state = self.rebuild_state(
            historical_results,
            request.cutoff_at_utc,
            target_season_id=request.season_id,
        )
        return self.predict_from_state(request, state)

    def predict_from_state(
        self,
        request: EloPredictionRequest,
        state: EloBaselineState,
    ) -> EloBaselinePrediction:
        if state.config_hash != self.config_hash:
            raise ValueError("Elo state config hash does not match the service config")
        if state.cutoff_at_utc != request.cutoff_at_utc:
            raise ValueError("Elo state cutoff does not match the prediction cutoff")
        if state.season_id != request.season_id:
            raise ValueError("Elo state season does not match the prediction season")
        if request.match_id in state.training_match_ids:
            raise ValueError("prediction match cannot appear in its training lineage")

        home_state = state.for_team(request.home_team_id)
        away_state = state.for_team(request.away_team_id)
        home_rating = (
            self.config.initial_rating if home_state is None else home_state.rating
        )
        away_rating = (
            self.config.initial_rating if away_state is None else away_state.rating
        )
        home_prior_matches = 0 if home_state is None else home_state.prior_matches
        away_prior_matches = 0 if away_state is None else away_state.prior_matches
        counts = {
            request.home_team_id: home_prior_matches,
            request.away_team_id: away_prior_matches,
        }
        insufficient = tuple(
            team_id
            for team_id in (request.home_team_id, request.away_team_id)
            if counts[team_id] < self.config.minimum_prior_matches
        )
        common = {
            "config_hash": self.config_hash,
            "match_id": request.match_id,
            "season_id": request.season_id,
            "home_team_id": request.home_team_id,
            "away_team_id": request.away_team_id,
            "cutoff_at_utc": request.cutoff_at_utc,
            "home_rating": home_rating,
            "away_rating": away_rating,
            "home_prior_matches": home_prior_matches,
            "away_prior_matches": away_prior_matches,
            "training_match_ids": state.training_match_ids,
        }
        if insufficient:
            return EloBaselinePrediction(
                **common,
                status=EloPredictionStatus.UNAVAILABLE,
                reason=EloUnavailableReason.INSUFFICIENT_PRIOR_MATCHES,
                insufficient_history_team_ids=insufficient,
            )
        return EloBaselinePrediction(
            **common,
            status=EloPredictionStatus.AVAILABLE,
            probabilities=_three_way_probabilities(
                home_rating,
                away_rating,
                self.config,
            ),
        )


def _visible_results(
    historical_results: Iterable[EloRegularTimeResult],
    cutoff_at_utc: datetime,
) -> tuple[EloRegularTimeResult, ...]:
    visible = tuple(
        result
        for result in historical_results
        if result.available_at_utc <= cutoff_at_utc
    )
    by_match_id: dict[str, EloRegularTimeResult] = {}
    by_fixture: dict[
        tuple[str, str, str, datetime], EloRegularTimeResult
    ] = {}
    for result in visible:
        previous = by_match_id.get(result.match_id)
        if previous is not None:
            kind = "duplicate" if previous == result else "conflicting"
            raise ValueError(
                f"{kind} historical result for match ID {result.match_id!r}"
            )
        fixture_key = (
            result.season_id,
            result.home_team_id,
            result.away_team_id,
            result.kickoff_at_utc,
        )
        previous = by_fixture.get(fixture_key)
        if previous is not None:
            same_score = (
                previous.home_goals == result.home_goals
                and previous.away_goals == result.away_goals
            )
            kind = "duplicate" if same_score else "conflicting"
            raise ValueError(
                f"{kind} historical fixture with match IDs "
                f"{previous.match_id!r} and {result.match_id!r}"
            )
        by_match_id[result.match_id] = result
        by_fixture[fixture_key] = result
    return tuple(
        sorted(
            visible,
            key=lambda result: (
                result.kickoff_at_utc,
                result.available_at_utc,
                result.match_id,
            ),
        )
    )


def _apply_result(
    ratings: dict[str, Decimal],
    prior_matches: dict[str, int],
    result: EloRegularTimeResult,
    config: EloBaselineConfig,
) -> None:
    home_rating = ratings.get(result.home_team_id, config.initial_rating)
    away_rating = ratings.get(result.away_team_id, config.initial_rating)
    expected_home = _expected_home_score(home_rating, away_rating, config.home_advantage)
    if result.home_goals > result.away_goals:
        actual_home = Decimal(1)
    elif result.home_goals < result.away_goals:
        actual_home = Decimal(0)
    else:
        actual_home = Decimal("0.5")
    with localcontext() as context:
        context.prec = 50
        adjustment = config.k_factor * (actual_home - expected_home)
        ratings[result.home_team_id] = _quantize_rating(home_rating + adjustment)
        ratings[result.away_team_id] = _quantize_rating(away_rating - adjustment)
    prior_matches[result.home_team_id] = prior_matches.get(result.home_team_id, 0) + 1
    prior_matches[result.away_team_id] = prior_matches.get(result.away_team_id, 0) + 1


def _regress_ratings(
    ratings: dict[str, Decimal],
    config: EloBaselineConfig,
) -> None:
    with localcontext() as context:
        context.prec = 50
        for team_id in sorted(ratings):
            deviation = ratings[team_id] - config.initial_rating
            ratings[team_id] = _quantize_rating(
                config.initial_rating + config.season_regression_factor * deviation
            )


def _expected_home_score(
    home_rating: Decimal,
    away_rating: Decimal,
    home_advantage: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        exponent = (
            (away_rating - home_rating - home_advantage)
            / ELO_RATING_SCALE
            * Decimal(10).ln()
        )
        return Decimal(1) / (Decimal(1) + exponent.exp())


def _three_way_probabilities(
    home_rating: Decimal,
    away_rating: Decimal,
    config: EloBaselineConfig,
) -> ThreeWayProbability:
    expected_home = _expected_home_score(
        home_rating,
        away_rating,
        config.home_advantage,
    )
    with localcontext() as context:
        context.prec = 50
        non_draw_probability = Decimal(1) - config.draw_probability
        home_win = (non_draw_probability * expected_home).quantize(
            PROBABILITY_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        home_win = min(non_draw_probability, max(Decimal(0), home_win))
        away_win = Decimal(1) - config.draw_probability - home_win
    return ThreeWayProbability(
        home_win=home_win,
        draw=config.draw_probability,
        away_win=away_win,
    )


def _quantize_rating(value: Decimal) -> Decimal:
    return value.quantize(RATING_QUANTUM, rounding=ROUND_HALF_EVEN)
