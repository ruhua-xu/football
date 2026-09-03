from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from football_system.application.identity_catalog import (
    FixtureIngestionCapture,
    FixtureIngestionSummary,
    MatchIdentityCatalog,
    MatchIdentityRegistration,
)
from football_system.application.models import AnalysisArtifacts, StoredInputManifest
from football_system.domain.betting import SportteryRules
from football_system.domain.prediction import (
    QuantModelEvaluation,
    QuantModelStateArtifact,
)

if TYPE_CHECKING:
    from football_system.application.backtest_v2 import WalkForwardBacktestV2Result
    from football_system.domain.backtest import BacktestRun
    from football_system.domain.backtest_v2 import BacktestV2Metrics, BacktestV2Slice


class AnalysisRepository(Protocol):
    def save_analysis(
        self,
        artifacts: AnalysisArtifacts,
        rules: SportteryRules,
    ) -> None: ...

    def table_counts(self) -> dict[str, int]: ...

    def quant_model_table_counts(self) -> dict[str, int]: ...

    def load_input_manifest(self, analysis_run_id: str) -> StoredInputManifest: ...

    def load_quant_model_state(
        self,
        quant_model_state_id: str,
    ) -> QuantModelStateArtifact: ...

    def load_quant_model_evaluation(
        self,
        quant_model_evaluation_id: str,
    ) -> QuantModelEvaluation: ...


class MatchIdentityRepository(Protocol):
    def register(self, registration: MatchIdentityRegistration) -> None: ...

    def register_fixture_ingestion(
        self,
        capture: FixtureIngestionCapture,
    ) -> FixtureIngestionSummary: ...

    def load_catalog(
        self,
        *,
        as_of_at_utc: datetime,
        kickoff_from_utc: datetime,
        kickoff_to_utc: datetime,
        provider_codes: tuple[str, ...] = (),
    ) -> MatchIdentityCatalog: ...


class BacktestV2Repository(Protocol):
    def save_walk_forward_backtest_v2_result(
        self,
        result: WalkForwardBacktestV2Result,
        *,
        calculated_at_utc: datetime | None = None,
    ) -> BacktestRun: ...

    def find_backtest_v2_run_value(
        self,
        backtest_run_id: str,
    ) -> BacktestRun | None: ...

    def backtest_v2_slice_values(
        self,
        backtest_run_id: str,
    ) -> tuple[BacktestV2Slice, ...]: ...

    def find_backtest_v2_metrics_value(
        self,
        backtest_run_id: str,
    ) -> BacktestV2Metrics | None: ...

    def backtest_v2_table_counts(self) -> dict[str, int]: ...
