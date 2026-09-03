from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from inspect import getattr_static
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


class ProviderRuntimeProvenanceRequiredError(RuntimeEnvironmentIsolationError):
    code = "PROVIDER_RUNTIME_PROVENANCE_REQUIRED"


class ProviderRuntimeProvenanceMismatchError(RuntimeEnvironmentIsolationError):
    code = "PROVIDER_RUNTIME_PROVENANCE_MISMATCH"


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
            and provenance.data_mode is not HistoricalDataMode.LIVE_STRICT
        ):
            raise RuntimeDataModeError(
                "live runtime requires LIVE_STRICT historical provenance"
            )
        if (
            self.environment is RuntimeEnvironment.RESEARCH
            and provenance.data_mode is not HistoricalDataMode.SOURCE_TIME_RESEARCH
        ):
            raise RuntimeDataModeError(
                "research runtime cannot treat input as LIVE_STRICT"
            )


def is_mock_provider_code(provider_code: str | None) -> bool:
    if provider_code is None:
        return False
    normalized = provider_code.strip().upper()
    return any(
        normalized == token
        or normalized.startswith(
            tuple(f"{token}{separator}" for separator in ("_", "-", "/", " "))
        )
        for token in ("MOCK", "SYNTHETIC")
    )


def provider_data_mode(provider: object) -> HistoricalDataMode:
    try:
        getattr_static(provider, "runtime_provenance")
    except AttributeError:
        return HistoricalDataMode.LIVE_STRICT
    provenance = getattr(provider, "runtime_provenance")
    if not isinstance(provenance, RuntimeProvenance):
        raise ProviderRuntimeProvenanceRequiredError(
            "provider must expose RuntimeProvenance"
        )
    if provenance.data_mode is not None:
        return provenance.data_mode
    return HistoricalDataMode.LIVE_STRICT


def require_provider_runtime_provenance(
    provider: object,
    role: str,
) -> RuntimeProvenance:
    try:
        provenance = getattr(provider, "runtime_provenance")
    except Exception:
        raise ProviderRuntimeProvenanceRequiredError(
            f"{role} provider did not expose readable runtime provenance"
        ) from None
    if not isinstance(provenance, RuntimeProvenance):
        raise ProviderRuntimeProvenanceRequiredError(
            f"{role} provider must expose RuntimeProvenance"
        )
    if provenance.provider_code is None:
        raise ProviderRuntimeProvenanceRequiredError(
            f"{role} provider provenance requires provider_code"
        )
    return provenance


def validate_analysis_provider_runtime(
    environment: RuntimeEnvironment | str,
    providers: Mapping[str, object],
) -> dict[str, RuntimeProvenance]:
    runtime = RuntimeEnvironment(environment)
    if runtime is RuntimeEnvironment.MOCK:
        return {}
    if not providers:
        raise ProviderRuntimeProvenanceRequiredError(
            f"{runtime.value} analysis requires explicit provider provenance"
        )
    provenance_by_role = {
        role: require_provider_runtime_provenance(provider, role)
        for role, provider in providers.items()
    }
    RuntimeEnvironmentGuard(runtime).validate(provenance_by_role.values())
    return provenance_by_role


def validate_runtime_environment(
    environment: RuntimeEnvironment | str,
    inputs: Iterable[RuntimeProvenance],
) -> None:
    RuntimeEnvironmentGuard(environment).validate(inputs)
