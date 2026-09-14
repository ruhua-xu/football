from football_system.application.ports.strategy_pass import StrategyPassRepository
from football_system.domain.services.strategy_pass import build_strategy_plan
from football_system.domain.services.strategy_settlement import settle_strategy_plan
from football_system.domain.strategy_pass import StrategyProfileV1


class StrategyPassService:
    def __init__(self, repository: StrategyPassRepository):
        self._repository = repository

    def create(self, kind, source_id, budget_fen, *, profile=None, role_requests=()):
        with self._repository.operation(kind=kind, source_id=source_id):
            source = self._repository.load_source(kind, source_id, budget_fen)
            plan = build_strategy_plan(
                source, profile or StrategyProfileV1(), role_requests
            )
            return self._repository.save_plan(plan)

    def settle(
        self,
        plan_id,
        result_ids,
        settled_at_utc,
        *,
        issues=(),
        supersedes_settlement_id=None,
    ):
        with self._repository.operation(plan_id=plan_id):
            plan = self._repository.load_plan(plan_id)
            results = self._repository.load_results(plan_id, result_ids)
            previous = (
                self._repository.load_settlement(supersedes_settlement_id)
                if supersedes_settlement_id
                else None
            )
            settlement = settle_strategy_plan(
                plan, results, settled_at_utc, issues=issues, previous=previous
            )
            return self._repository.save_settlement(settlement)
