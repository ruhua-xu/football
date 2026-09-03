from __future__ import annotations

from typing import Protocol

from football_system.domain.review import (
    AnalysisPacketContract,
    AnalysisPacketSource,
    AnalysisPacketSourceV2,
    AnalysisPacketSourceV3,
    LLMReviewArtifact,
    StoredAnalysisPacket,
)


class ReviewArtifactRepository(Protocol):
    def load_packet_source(self, analysis_run_id: str) -> AnalysisPacketSource: ...

    def load_packet_source_v2(self, analysis_run_id: str) -> AnalysisPacketSourceV2: ...

    def load_packet_source_v3(self, analysis_run_id: str) -> AnalysisPacketSourceV3: ...

    def find_analysis_packet(
        self,
        analysis_run_id: str,
        schema_version: str,
    ) -> StoredAnalysisPacket | None: ...

    def save_analysis_packet(
        self,
        packet: AnalysisPacketContract,
        packet_json: str,
    ) -> StoredAnalysisPacket: ...

    def load_analysis_packet(self, packet_id: str) -> StoredAnalysisPacket: ...

    def save_llm_review(self, artifact: LLMReviewArtifact) -> LLMReviewArtifact: ...
