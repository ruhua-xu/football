import pytest

from football_system.application.environment import (
    CrossEnvironmentInputError,
    MockProvenanceInLiveError,
    RuntimeDataModeError,
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    RuntimeProvenance,
)
from football_system.domain.archive import HistoricalDataMode


def provenance(
    environment: RuntimeEnvironment,
    provider_code: str,
    *,
    is_mock: bool = False,
    data_mode: HistoricalDataMode | None = None,
) -> RuntimeProvenance:
    return RuntimeProvenance(
        environment=environment,
        provider_code=provider_code,
        is_mock=is_mock,
        data_mode=data_mode,
    )


def test_runtime_environment_has_exact_supported_values() -> None:
    assert {environment.value for environment in RuntimeEnvironment} == {
        "mock",
        "live",
        "research",
    }


@pytest.mark.parametrize(
    "source",
    [
        provenance(RuntimeEnvironment.LIVE, "MOCK_FIXTURE"),
        provenance(RuntimeEnvironment.LIVE, "REAL_FIXTURE", is_mock=True),
    ],
)
def test_live_rejects_mock_provider_or_provenance(
    source: RuntimeProvenance,
) -> None:
    guard = RuntimeEnvironmentGuard(RuntimeEnvironment.LIVE)

    with pytest.raises(MockProvenanceInLiveError) as error:
        guard.validate_input(source)

    assert error.value.code == "MOCK_PROVENANCE_IN_LIVE"


def test_live_rejects_cross_environment_input() -> None:
    guard = RuntimeEnvironmentGuard(RuntimeEnvironment.LIVE)
    source = provenance(RuntimeEnvironment.MOCK, "REAL_FIXTURE")

    with pytest.raises(CrossEnvironmentInputError) as error:
        guard.validate((source,))

    assert error.value.code == "CROSS_ENVIRONMENT_INPUT"


def test_mock_runtime_accepts_mock_input() -> None:
    RuntimeEnvironmentGuard(RuntimeEnvironment.MOCK).validate_input(
        provenance(RuntimeEnvironment.MOCK, "MOCK_FIXTURE", is_mock=True)
    )


def test_live_accepts_live_strict_real_input() -> None:
    RuntimeEnvironmentGuard(RuntimeEnvironment.LIVE).validate_input(
        provenance(
            RuntimeEnvironment.LIVE,
            "SPORTMONKS_FIXTURE",
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )
    )


def test_research_does_not_accept_live_strict_provenance() -> None:
    guard = RuntimeEnvironmentGuard(RuntimeEnvironment.RESEARCH)

    with pytest.raises(RuntimeDataModeError) as error:
        guard.validate_input(
            provenance(
                RuntimeEnvironment.RESEARCH,
                "RESEARCH_ARCHIVE",
                data_mode=HistoricalDataMode.LIVE_STRICT,
            )
        )

    assert error.value.code == "RUNTIME_DATA_MODE_MISMATCH"
    guard.validate_input(
        provenance(
            RuntimeEnvironment.RESEARCH,
            "RESEARCH_ARCHIVE",
            data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        )
    )
