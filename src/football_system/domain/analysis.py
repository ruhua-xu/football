from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from football_system.domain.common import DomainModel, Identifier, UtcDateTime


class AnalysisRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AnalysisRun(DomainModel):
    analysis_run_id: Identifier
    run_kind: str = "MVP_ANALYSIS"
    as_of_at_utc: UtcDateTime
    status: AnalysisRunStatus
    started_at_utc: UtcDateTime
    completed_at_utc: UtcDateTime
    pipeline_version: str
    code_revision: str
    config_json: str
    config_hash: Identifier
    input_manifest_version: str = "MVP_INPUT_MANIFEST_V2"
    input_manifest_json: str
    input_manifest_hash: Identifier
    replay_of_run_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_timeline(self) -> AnalysisRun:
        if self.as_of_at_utc > self.started_at_utc:
            raise ValueError("analysis cannot start before its knowledge cutoff")
        if self.started_at_utc > self.completed_at_utc:
            raise ValueError("analysis cannot complete before it starts")
        return self


class AnalysisMatchContext(DomainModel):
    analysis_run_id: Identifier
    match_id: Identifier
    market_odds_snapshot_id: Identifier
    sporttery_bonus_snapshot_id: Identifier
    manual_quant_input_id: Identifier
    context_json: str
    context_hash: Identifier


class ModelAnalysisMatchContext(DomainModel):
    analysis_run_id: Identifier
    match_id: Identifier
    market_odds_snapshot_id: Identifier
    sporttery_bonus_snapshot_id: Identifier
    quant_model_evaluation_id: Identifier
    context_json: str
    context_hash: Identifier
