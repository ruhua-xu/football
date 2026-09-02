from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from football_system.domain.archive import HistoricalDataMode


class RuntimeEnvironment(StrEnum):
    MOCK = "mock"
    LIVE = "live"
    RESEARCH = "research"


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: RuntimeEnvironment = RuntimeEnvironment.MOCK
    kickoff_tolerance_seconds: int = Field(default=300, ge=0, strict=True)


class RuntimeProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    environment: RuntimeEnvironment
    provider_code: str | None = Field(default=None, min_length=1, max_length=160)
    provenance: str | None = Field(default=None, min_length=1, max_length=2048)
    is_mock: bool = Field(default=False, strict=True)
    data_mode: HistoricalDataMode | None = None


class RuntimeEnvironmentIsolationError(ValueError):
    code: ClassVar[str] = "RUNTIME_ENVIRONMENT_ISOLATION_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class MockProvenanceInLiveError(RuntimeEnvironmentIsolationError):
    code = "MOCK_PROVENANCE_IN_LIVE"


class CrossEnvironmentInputError(RuntimeEnvironmentIsolationError):
    code = "CROSS_ENVIRONMENT_INPUT"


class RuntimeDataModeError(RuntimeEnvironmentIsolationError):
    code = "RUNTIME_DATA_MODE_MISMATCH"


class RuntimeEnvironmentGuard:
    def __init__(self, environment: RuntimeEnvironment | str) -> None:
        self.environment = RuntimeEnvironment(environment)

    def validate(self, inputs: Iterable[RuntimeProvenance]) -> None:
        for provenance in inputs:
            self.validate_input(provenance)

    def validate_input(self, provenance: RuntimeProvenance) -> None:
        if self.environment is RuntimeEnvironment.LIVE and (
            provenance.is_mock or is_mock_provider_code(provenance.provider_code)
        ):
            raise MockProvenanceInLiveError(
                "live runtime cannot consume mock provenance or providers"
            )
        if provenance.environment is not self.environment:
            raise CrossEnvironmentInputError(
                f"{self.environment.value} runtime cannot consume "
                f"{provenance.environment.value} input"
            )
        if (
            self.environment is RuntimeEnvironment.LIVE
            and provenance.data_mode is not None
            and provenance.data_mode is not HistoricalDataMode.LIVE_STRICT
        ):
            raise RuntimeDataModeError(
                "live runtime requires LIVE_STRICT historical provenance"
            )
        if (
            self.environment is RuntimeEnvironment.RESEARCH
            and provenance.data_mode is not None
            and provenance.data_mode is not HistoricalDataMode.SOURCE_TIME_RESEARCH
        ):
            raise RuntimeDataModeError(
                "research runtime cannot treat input as LIVE_STRICT"
            )


def is_mock_provider_code(provider_code: str | None) -> bool:
    if provider_code is None:
        return False
    normalized = provider_code.strip().upper()
    return normalized == "MOCK" or normalized.startswith(("MOCK_", "MOCK-", "MOCK."))


def validate_runtime_environment(
    environment: RuntimeEnvironment | str,
    inputs: Iterable[RuntimeProvenance],
) -> None:
    RuntimeEnvironmentGuard(environment).validate(inputs)
