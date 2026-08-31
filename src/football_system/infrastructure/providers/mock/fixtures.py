from __future__ import annotations

from decimal import Decimal

from football_system.application.ports.data_providers import (
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
)
from football_system.domain.common import stable_id
from football_system.domain.match import Match, ProviderMatchMapping
from football_system.infrastructure.providers.mock.dataset import MockDataset


class MockFixtureProvider(FixtureProvider):
    provider_code = "MOCK_FIXTURE"

    def __init__(self, dataset: MockDataset) -> None:
        self._dataset = dataset

    async def fetch_fixtures(self, query: FixtureQuery) -> FixtureBatch:
        seeds = tuple(
            seed
            for seed in self._dataset.matches
            if query.kickoff_from_utc <= seed.kickoff_at_utc <= query.kickoff_to_utc
            and seed.available_at_utc <= query.as_of_at_utc
        )
        competition_ids = {seed.competition_id for seed in seeds}
        team_ids = {
            team_id
            for seed in seeds
            for team_id in (seed.home_team_id, seed.away_team_id)
        }
        matches = tuple(
            Match(
                match_id=seed.match_id,
                competition_id=seed.competition_id,
                home_team_id=seed.home_team_id,
                away_team_id=seed.away_team_id,
                kickoff_at_utc=seed.kickoff_at_utc,
                available_at_utc=seed.available_at_utc,
            )
            for seed in seeds
        )
        mappings = tuple(
            ProviderMatchMapping(
                mapping_id=stable_id("mapping", self.provider_code, seed.fixture_external_id),
                provider_code=self.provider_code,
                external_namespace="fixture",
                external_match_id=seed.fixture_external_id,
                internal_match_id=seed.match_id,
                confidence=Decimal(1),
                available_at_utc=seed.available_at_utc,
            )
            for seed in seeds
        )
        return FixtureBatch(
            competitions=tuple(
                competition
                for competition in self._dataset.competitions
                if competition.competition_id in competition_ids
            ),
            teams=tuple(team for team in self._dataset.teams if team.team_id in team_ids),
            matches=matches,
            mappings=mappings,
        )
