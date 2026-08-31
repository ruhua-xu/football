from __future__ import annotations

from typing import Protocol

from football_system.application.models import AnalysisArtifacts, StoredInputManifest
from football_system.domain.betting import SportteryRules


class AnalysisRepository(Protocol):
    def save_analysis(
        self,
        artifacts: AnalysisArtifacts,
        rules: SportteryRules,
    ) -> None: ...

    def table_counts(self) -> dict[str, int]: ...

    def load_input_manifest(self, analysis_run_id: str) -> StoredInputManifest: ...
