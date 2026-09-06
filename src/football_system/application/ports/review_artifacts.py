from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
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
    def audit_operation(self, **scope) -> AbstractContextManager: ...

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


def repository_operation(repository, **scope) -> AbstractContextManager:
    """Legacy in-memory ports have no approved persisted state to authorize.

    Concrete database repositories MUST detect approved state themselves, rather
    than relying on an optional service dependency or fields in the V3 packet.
    """
    operation = getattr(repository, "audit_operation", None)
    return nullcontext() if operation is None else operation(**scope)
