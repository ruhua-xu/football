import pytest

from football_system.application.environment import (
    CrossEnvironmentInputError,
    MockProvenanceInLiveError,
    ProviderRuntimeProvenanceRequiredError,
    RuntimeDataModeError,
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    RuntimeProvenance,
    provider_data_mode,
    is_mock_provider_code,
    validate_analysis_provider_runtime,
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
        provenance(RuntimeEnvironment.LIVE, "SYNTHETIC/ACCEPTANCE"),
        provenance(RuntimeEnvironment.LIVE, "SYNTHETIC ACCEPTANCE"),
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


@pytest.mark.parametrize("provider_code", ("MOCKINGBIRD_ODDS", "SYNTHETICALLY_REAL"))
def test_mock_provider_detection_requires_a_token_boundary(provider_code: str) -> None:
    assert is_mock_provider_code(provider_code) is False


def test_live_accepts_live_strict_real_input() -> None:
    RuntimeEnvironmentGuard(RuntimeEnvironment.LIVE).validate_input(
        provenance(
            RuntimeEnvironment.LIVE,
            "SPORTMONKS_FIXTURE",
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )
    )


@pytest.mark.parametrize(
    "environment",
    (RuntimeEnvironment.LIVE, RuntimeEnvironment.RESEARCH),
)
def test_real_runtime_requires_explicit_data_mode(
    environment: RuntimeEnvironment,
) -> None:
    with pytest.raises(RuntimeDataModeError):
        RuntimeEnvironmentGuard(environment).validate_input(
            provenance(environment, "REAL_FIXTURE")
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


def test_live_analysis_requires_provenance_on_every_provider_role() -> None:
    with pytest.raises(ProviderRuntimeProvenanceRequiredError) as error:
        validate_analysis_provider_runtime(
            RuntimeEnvironment.LIVE,
            {"fixture": object()},
        )

    assert error.value.code == "PROVIDER_RUNTIME_PROVENANCE_REQUIRED"


def test_mock_analysis_does_not_require_real_provider_provenance() -> None:
    assert (
        validate_analysis_provider_runtime(
            RuntimeEnvironment.MOCK,
            {"fixture": object()},
        )
        == {}
    )


def test_research_analysis_requires_matching_provider_provenance() -> None:
    with pytest.raises(ProviderRuntimeProvenanceRequiredError):
        validate_analysis_provider_runtime(
            RuntimeEnvironment.RESEARCH,
            {"fixture": object()},
        )

    provider = type(
        "ResearchProvider",
        (),
        {
            "runtime_provenance": provenance(
                RuntimeEnvironment.RESEARCH,
                "RESEARCH_FIXTURE",
                data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
            )
        },
    )()
    assert validate_analysis_provider_runtime(
        RuntimeEnvironment.RESEARCH,
        {"fixture": provider},
    ) == {"fixture": provider.runtime_provenance}

    provider.runtime_provenance = provenance(
        RuntimeEnvironment.LIVE,
        "LIVE_FIXTURE",
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )
    with pytest.raises(CrossEnvironmentInputError):
        validate_analysis_provider_runtime(
            RuntimeEnvironment.RESEARCH,
            {"fixture": provider},
        )


def test_provider_data_mode_does_not_hide_provenance_errors() -> None:
    class BrokenProvider:
        @property
        def runtime_provenance(self) -> RuntimeProvenance:
            raise RuntimeDataModeError("broken provenance")

    with pytest.raises(RuntimeDataModeError, match="broken provenance"):
        provider_data_mode(BrokenProvider())
