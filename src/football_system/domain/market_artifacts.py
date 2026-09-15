"""Closed V4/V2 artifact registry. No plugin or provider discovery."""

from football_system.domain.goal_model import (
    GoalTrainingCohortV1,
    PoissonGoalsStateV1,
    PoissonScoreGridV1,
)
from football_system.domain.market_analysis import (
    FootballEvidenceV1,
    LegacyThreeWayInputV1,
    MarketAnalysisUnitV1,
    MultiMarketAnalysisV1,
)
from football_system.domain.market_v2 import (
    MarketConsensusV2,
    MarketOddsSnapshotV2,
    SportteryFixedBonusSnapshotV2,
)
from football_system.domain.offline_market_source import OfflineMarketSourceV1
from football_system.domain.review_v4 import (
    AnalysisPacketV4,
    GenericFusionRunV1,
    ImportedReviewV4,
    MarketReviewContextV4,
)
from football_system.domain.settlement_v2 import StrategySettlementV2
from football_system.domain.strategy_pass_v2 import (
    ExpandedAtomicBetV2,
    MatchChoiceSetV1,
    OutcomeCandidateV1,
    StrategyPassPlanV2,
    StrategySourceV2,
    SystemTicketCandidateV2,
    SystemTicketV2,
)

ARTIFACT_TYPES = {
    cls.model_fields["schema_version"].default: cls
    for cls in (
        OfflineMarketSourceV1,
        GoalTrainingCohortV1,
        PoissonScoreGridV1,
        PoissonGoalsStateV1,
        FootballEvidenceV1,
        MarketOddsSnapshotV2,
        SportteryFixedBonusSnapshotV2,
        MarketConsensusV2,
        LegacyThreeWayInputV1,
        MarketAnalysisUnitV1,
        MultiMarketAnalysisV1,
        MarketReviewContextV4,
        AnalysisPacketV4,
        ImportedReviewV4,
        GenericFusionRunV1,
        OutcomeCandidateV1,
        StrategySourceV2,
        MatchChoiceSetV1,
        ExpandedAtomicBetV2,
        SystemTicketCandidateV2,
        SystemTicketV2,
        StrategyPassPlanV2,
        StrategySettlementV2,
    )
}
