"""Explicit real-only value view consumed by the frozen descriptive report kernel."""

from dataclasses import dataclass

from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.real_bridge import RealValidationEpochV1, RealEpochConfigurationAnchorV1, require


@dataclass(frozen=True)
class RealReportEpochView:
    epoch: RealValidationEpochV1
    anchor: RealEpochConfigurationAnchorV1

    def __post_init__(self):
        require(type(self.epoch) is RealValidationEpochV1 and type(self.anchor) is RealEpochConfigurationAnchorV1,
                "REAL_REPORT_VIEW_REQUIRES_REAL_TYPES")
        require(self.epoch.anchor == ArtifactRefV1.of(self.anchor), "REAL_REPORT_ANCHOR_MISMATCH")

    @property
    def policy(self):
        return self.anchor.policy.prospective_policy

    @property
    def mode(self):
        return "REAL_PROSPECTIVE" if self.epoch.provenance == "LIVE_OBSERVATION" else "SYNTHETIC_SOFTWARE_ACCEPTANCE"

    @property
    def artifact_id(self):
        return self.epoch.artifact_id

    @property
    def schema_version(self):
        return self.epoch.schema_version

    @property
    def content_hash(self):
        return self.epoch.content_hash
