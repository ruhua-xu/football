from typing import Protocol

from football_system.domain.market_v2 import MarketArtifact
from football_system.domain.strategy_pass_v2 import StrategyPassPlanV2


class ReturnDistributionRepository(Protocol):
    def load_plan(self, plan_id: str) -> StrategyPassPlanV2: ...
    def save(self, artifact: MarketArtifact) -> MarketArtifact: ...
    def load(self, artifact_id: str) -> MarketArtifact: ...
