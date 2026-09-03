from __future__ import annotations

from collections.abc import Callable

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeEnvironmentGuard,
    require_provider_runtime_provenance,
)
from football_system.application.identity_catalog import (
    FixtureIngestionCapture,
    FixtureIngestionRequest,
    FixtureIngestionSummary,
)
from football_system.application.ports.data_providers import FixtureCaptureProvider
from football_system.application.ports.repositories import MatchIdentityRepository


class LiveFixtureIngestionService:
    def __init__(
        self,
        provider: FixtureCaptureProvider,
        repository_factory: Callable[[], MatchIdentityRepository],
        *,
        environment: RuntimeEnvironment | str,
    ) -> None:
        self._provider = provider
        self._repository_factory = repository_factory
        self._environment = RuntimeEnvironment(environment)

    async def ingest(
        self,
        request: FixtureIngestionRequest,
    ) -> FixtureIngestionSummary:
        provenance = require_provider_runtime_provenance(
            self._provider,
            "fixture capture",
        )
        RuntimeEnvironmentGuard(self._environment).validate_input(provenance)
        capture = FixtureIngestionCapture.model_validate(
            (
                await self._provider.capture_fixtures(
                    FixtureIngestionRequest.model_validate(
                        request.model_dump(mode="python")
                    )
                )
            ).model_dump(mode="python")
        )
        if (
            capture.request != request
            or capture.provider_code != provenance.provider_code
        ):
            raise ValueError("fixture capture conflicts with requested provider scope")
        return self._repository_factory().register_fixture_ingestion(capture)
