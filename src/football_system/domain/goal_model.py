"""Independent fixed Poisson baseline, separate from the frozen Elo model."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.market_v2 import (
    Hash,
    MarketArtifact,
    MultiMarketModel,
    content_hash,
    decimal_value,
    revalidate,
)
from football_system.domain.settlement import MatchResult


class PoissonGoalsConfigV1(MultiMarketModel):
    model_name: Literal["POISSON_GOALS_BASELINE_V1"] = "POISSON_GOALS_BASELINE_V1"
    model_version: Literal["1"] = "1"
    calibration_label: Literal["BASELINE_UNCALIBRATED"] = "BASELINE_UNCALIBRATED"
    formula_version: Literal["HOME_AWAY_SPLIT_RATIOS_V1"] = "HOME_AWAY_SPLIT_RATIOS_V1"
    minimum_home_matches: Literal[5] = 5
    minimum_away_matches: Literal[5] = 5
    minimum_league_matches: Literal[10] = 10
    maximum_training_facts: Literal[1024] = 1024
    decimal_precision: Literal[80] = 80
    tail_epsilon: Literal[Decimal("1e-18")] = Decimal("1e-18")
    numerical_error_bound: Literal[Decimal("1e-60")] = Decimal("1e-60")
    probability_quantum: Literal[Decimal("1e-12")] = Decimal("1e-12")
    lambda_quantum: Literal[Decimal("1e-18")] = Decimal("1e-18")
    max_lambda: Literal[Decimal(20)] = Decimal(20)
    max_score: Literal[256] = 256
    max_absolute_handicap: Literal[20] = 20
    tail_policy: Literal["INFINITE_CDF_CERTIFIED_ROUNDING_V1"] = (
        "INFINITE_CDF_CERTIFIED_ROUNDING_V1"
    )
    closure_policy: Literal["ROUND_HALF_EVEN_LARGEST_CANONICAL_RESIDUAL_V1"] = (
        "ROUND_HALF_EVEN_LARGEST_CANONICAL_RESIDUAL_V1"
    )

    @field_validator(
        "tail_epsilon",
        "numerical_error_bound",
        "probability_quantum",
        "lambda_quantum",
        "max_lambda",
        mode="before",
    )
    @classmethod
    def fixed_decimal(cls, value):
        return Decimal(decimal_value(value))

    @property
    def config_hash(self):
        return content_hash("POISSON_GOALS_CONFIG_V1", self)


class GoalTrainingFactV1(MultiMarketModel):
    result: MatchResult
    competition_id: Identifier
    season_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    score_semantics: Literal["REGULAR_TIME_FINAL"] = "REGULAR_TIME_FINAL"

    @model_validator(mode="after")
    def identity(self):
        revalidate(self.result)
        if self.result.payload_hash != match_result_payload_sha256(
            self.result.home_goals, self.result.away_goals
        ):
            raise ValueError("normalized training score payload hash mismatch")
        if (
            self.home_team_id == self.away_team_id
            or self.kickoff_at_utc >= self.result.available_at_utc
        ):
            raise ValueError("training fact identity/timeline mismatch")
        return self

    @property
    def fact_hash(self):
        return content_hash("GOAL_TRAINING_FACT_V1", self)


class GoalTrainingCohortV1(MarketArtifact):
    schema_version: Literal["GOAL_TRAINING_COHORT_V1"] = "GOAL_TRAINING_COHORT_V1"
    competition_id: Identifier
    season_id: Identifier
    facts: tuple[GoalTrainingFactV1, ...] = Field(max_length=1024)
    admitted_at_utc: UtcDateTime
    admission_reference: Identifier
    source_artifact_hash: Hash
    classification: Literal["SYNTHETIC_ACCEPTANCE_DATA"] = "SYNTHETIC_ACCEPTANCE_DATA"

    @model_validator(mode="after")
    def scope(self):
        keys = tuple(
            (f.kickoff_at_utc, f.result.match_id, f.result.match_result_id)
            for f in self.facts
        )
        if keys != tuple(sorted(set(keys))) or len(
            {f.result.match_id for f in self.facts}
        ) != len(self.facts):
            raise ValueError("cohort needs one ordered admitted version per match")
        if len({f.result.match_result_id for f in self.facts}) != len(self.facts):
            raise ValueError("cohort result ID reused across facts")
        if any(
            f.competition_id != self.competition_id
            or f.season_id != self.season_id
            or f.result.ingested_at_utc > self.admitted_at_utc
            for f in self.facts
        ):
            raise ValueError("cohort crosses scope/admission time")
        return self

    @property
    def training_data_hash(self):
        return content_hash("GOAL_TRAINING_DATA_V1", self.facts)


class GoalPredictionRequestV1(MultiMarketModel):
    match_id: Identifier
    competition_id: Identifier
    season_id: Identifier
    home_team_id: Identifier
    away_team_id: Identifier
    kickoff_at_utc: UtcDateTime
    training_cutoff_at_utc: UtcDateTime
    generated_at_utc: UtcDateTime

    @model_validator(mode="after")
    def timeline(self):
        if (
            self.home_team_id == self.away_team_id
            or not self.training_cutoff_at_utc
            <= self.generated_at_utc
            < self.kickoff_at_utc
        ):
            raise ValueError("invalid goal prediction identity/timeline")
        return self


class PoissonScoreGridV1(MarketArtifact):
    schema_version: Literal["POISSON_SCORE_GRID_V1"] = "POISSON_SCORE_GRID_V1"
    config: PoissonGoalsConfigV1
    lambda_home: Decimal = Field(ge=0, le=20, decimal_places=18)
    lambda_away: Decimal = Field(ge=0, le=20, decimal_places=18)
    home_pmf: tuple[Decimal, ...] = Field(min_length=7, max_length=257)
    away_pmf: tuple[Decimal, ...] = Field(min_length=7, max_length=257)
    home_tail: Decimal = Field(ge=0, le=1)
    away_tail: Decimal = Field(ge=0, le=1)
    rectangular_tail: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.poisson_goals import grid_content

        expected = grid_content(self.lambda_home, self.lambda_away, self.config)
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("Poisson score grid replay mismatch")
        return self


class PoissonGoalsStateV1(MarketArtifact):
    schema_version: Literal["POISSON_GOALS_STATE_V1"] = "POISSON_GOALS_STATE_V1"
    config: PoissonGoalsConfigV1
    cohort: GoalTrainingCohortV1
    request: GoalPredictionRequestV1
    training_data_hash: Hash
    status: Literal["AVAILABLE", "MODEL_UNAVAILABLE"]
    reason: str | None
    home_count: int = Field(ge=0, strict=True)
    away_count: int = Field(ge=0, strict=True)
    league_count: int = Field(ge=0, strict=True)
    league_home_average: Decimal | None
    league_away_average: Decimal | None
    grid: PoissonScoreGridV1 | None

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.poisson_goals import training_content

        expected = training_content(self.cohort, self.request, self.config)
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ValueError("Poisson trained state replay mismatch")
        return self
