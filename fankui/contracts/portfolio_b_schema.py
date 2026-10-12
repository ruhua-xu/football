"""Reviewed schema names now resolve to their identical runtime definitions."""
from football_system.domain.portfolio import (  # noqa: F401
    SnapshotContentV1, PredictionSourceSnapshotV1, BudgetAuthorityContentV1, PortfolioBudgetAuthorityV1,
    AssemblyContentV1, PortfolioAssemblyV1, LockContentV1, AtomicPortfolioLockV1,
    SettlementContentV1, PortfolioSettlementV1, SnapshotRequestV1, AssemblyRequestV1,
    LockRequestV1, SettlementRequestV1, SCHEMAS,
)
