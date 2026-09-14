from collections.abc import Sequence
from typing import ContextManager, Literal, Protocol

from football_system.domain.settlement import MatchResult
from football_system.domain.strategy_pass import StrategyPassPlanV1, StrategySourceV1
from football_system.domain.strategy_settlement import StrategySettlementV1

StrategySourceKind = Literal["ANALYSIS_RUN", "PORTFOLIO_REVISION"]


class StrategyPassRepository(Protocol):
    def operation(self, **scope) -> ContextManager: ...

    def load_source(
        self, kind: StrategySourceKind, source_id: str, budget_fen: int
    ) -> StrategySourceV1: ...

    def save_plan(self, plan: StrategyPassPlanV1) -> StrategyPassPlanV1: ...

    def load_plan(self, plan_id: str) -> StrategyPassPlanV1: ...

    def load_results(
        self, plan_id: str, result_ids: Sequence[str]
    ) -> tuple[MatchResult, ...]: ...

    def save_settlement(
        self, settlement: StrategySettlementV1
    ) -> StrategySettlementV1: ...

    def load_settlement(self, settlement_id: str) -> StrategySettlementV1: ...
