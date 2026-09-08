"""Explicit JSON correction adapter, separate from the frozen V1 parser contract."""

from typing import Literal, Self

from pydantic import Field, model_validator

from football_system.domain.common import DomainModel, Identifier
from football_system.domain.training_admission import (
    ProviderResultStatusCategory,
    Reference,
)


class CorrectionScopePathsV2(DomainModel):
    fixture_key: Reference
    competition_id: Reference
    season_id: Reference
    available_at_utc: Reference


class CorrectionFixturePathsV2(CorrectionScopePathsV2):
    home_team_id: Reference
    away_team_id: Reference
    kickoff_at_utc: Reference


class CorrectionResultPathsV2(CorrectionFixturePathsV2):
    result_key: Reference
    status: Reference
    score_semantics: Reference
    home_goals: Reference
    away_goals: Reference
    finalized_at_utc: Reference
    observed_at_utc: Reference
    revision_id: Reference | None = None
    revision_order: Reference | None = None

    @model_validator(mode="after")
    def paired_revision(self) -> Self:
        if (self.revision_id is None) != (self.revision_order is None):
            raise ValueError("revision ID and order paths must be paired")
        return self


class CorrectionStatusRuleV2(DomainModel):
    raw_status: Identifier
    category: ProviderResultStatusCategory


class TrainingCorrectionJsonAdapterV2(DomainModel):
    schema_version: Literal["TRAINING_CORRECTION_JSON_ADAPTER_V2"] = (
        "TRAINING_CORRECTION_JSON_ADAPTER_V2"
    )
    provider_code: Identifier
    provider_fixture_namespace: Identifier
    adapter_name: Identifier
    adapter_version: Identifier
    status_mapping_version: Identifier
    season_mapping_version: Identifier
    mapping_policy_version: Identifier
    regular_time_score_semantics: Identifier
    status_rules: tuple[CorrectionStatusRuleV2, ...] = Field(min_length=1)
    fixture: CorrectionFixturePathsV2
    scope: CorrectionScopePathsV2
    result: CorrectionResultPathsV2

    @model_validator(mode="after")
    def unique_statuses(self) -> Self:
        if len({x.raw_status for x in self.status_rules}) != len(self.status_rules):
            raise ValueError("ambiguous adapter status rules")
        return self
