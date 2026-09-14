"""Synthetic admitted-history fixture; no real sources or approval artifacts."""

from types import SimpleNamespace

import pytest

from football_system.application.strategy_pass import StrategyPassService
from football_system.infrastructure.database.strategy_pass_repository import (
    SqlAlchemyStrategyPassRepository,
)
from tests.integration import test_production_audit as audit_fixtures
from tests.integration import test_production_inference as inference_fixtures
from tests.integration import test_production_quant_persistence as production_fixtures

lane = inference_fixtures.lane
inference = inference_fixtures.inference
audited = audit_fixtures.audited


def test_strategy_direct_retry_read_and_settlement_keep_mandatory_current_audit(
    audited,
):
    audit_fixtures.downstream(audited)
    repo = SqlAlchemyStrategyPassRepository(
        audited.lane.sessions, audit_repository=audited.auditor
    )
    budget = audited.artifacts.portfolios[0].budget_fen
    plan = StrategyPassService(repo).create("ANALYSIS_RUN", audited.run_id, budget)
    assert repo.load_plan(plan.plan_id) == plan
    for dependency in (None, SimpleNamespace(gate_run=lambda *_: None)):
        blocked = SqlAlchemyStrategyPassRepository(
            audited.lane.sessions, audit_repository=dependency
        )
        for operation in (
            lambda: blocked.load_source("ANALYSIS_RUN", audited.run_id, budget),
            lambda: blocked.save_plan(plan),
            lambda: blocked.load_plan(plan.plan_id),
            lambda: blocked.load_results(plan.plan_id, ()),
            lambda: StrategyPassService(blocked).create(
                "ANALYSIS_RUN", audited.run_id, budget
            ),
        ):
            with pytest.raises(ValueError, match="concrete production audit"):
                operation()
    audited.lane.clock.value = production_fixtures.END
    for operation in (
        lambda: repo.load_plan(plan.plan_id),
        lambda: repo.save_plan(plan),
        lambda: repo.load_results(plan.plan_id, ()),
    ):
        with pytest.raises(ValueError):
            operation()
