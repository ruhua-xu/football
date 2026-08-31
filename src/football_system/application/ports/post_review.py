from __future__ import annotations

from typing import Protocol

from football_system.domain.post_review import (
    FusionRun,
    FusionSource,
    PortfolioRevision,
    PortfolioRevisionSource,
)


class PostReviewRepository(Protocol):
    def load_fusion_source(self, review_artifact_id: str) -> FusionSource: ...

    def find_fusion_run(self, fusion_run_id: str) -> FusionRun | None: ...

    def save_fusion_run(self, fusion_run: FusionRun) -> FusionRun: ...

    def load_portfolio_revision_source(
        self,
        fusion_run_id: str,
    ) -> PortfolioRevisionSource: ...

    def find_portfolio_revision(
        self,
        portfolio_revision_id: str,
    ) -> PortfolioRevision | None: ...

    def save_portfolio_revision(
        self,
        revision: PortfolioRevision,
    ) -> PortfolioRevision: ...
