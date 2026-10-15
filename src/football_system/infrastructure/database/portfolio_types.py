"""Closed Portfolio artifact graph and explicit cross-ledger reference grammar."""
import json

from pydantic import BaseModel

from football_system.domain.archive import canonical_json
from football_system.domain.common import stable_id
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.portfolio import (
    PredictionSourceSnapshotV1, PortfolioBudgetAuthorityV1, PortfolioAssemblyV1, AtomicPortfolioLockV1,
    PortfolioSettlementV1, PortfolioReceiptV1, PortfolioAuthorityEvidenceV1, PortfolioBudgetApprovalV1, PortfolioResultSetV1, PortfolioTicketRequestBundleV1, PortfolioLockRevalidationV1,
)
from football_system.domain.portfolio_math import PortfolioAnalysisInputV1, PortfolioFusionInputV1, PortfolioStrategySourceV1, PortfolioStrategyPlanV1
from football_system.domain.real_bridge import REAL_ARTIFACT_TYPES
from football_system.domain.return_distribution import RETURN_ARTIFACT_TYPES
from football_system.domain.strategy_pass_v2 import OutcomeCandidateV1, MatchChoiceSetV1, ExpandedAtomicBetV2, SystemTicketCandidateV2, SystemTicketV2
from football_system.infrastructure.files.training_evidence import strict_json_bytes

PF_TYPES = {cls.model_fields["schema_version"].default: cls for cls in (
    PredictionSourceSnapshotV1, PortfolioBudgetAuthorityV1, PortfolioAssemblyV1, AtomicPortfolioLockV1, PortfolioSettlementV1,
    PortfolioReceiptV1, PortfolioAuthorityEvidenceV1, PortfolioBudgetApprovalV1, PortfolioResultSetV1, PortfolioTicketRequestBundleV1, PortfolioLockRevalidationV1,
    PortfolioAnalysisInputV1, PortfolioFusionInputV1, PortfolioStrategySourceV1, PortfolioStrategyPlanV1,
    OutcomeCandidateV1, MatchChoiceSetV1, ExpandedAtomicBetV2, SystemTicketCandidateV2, SystemTicketV2,
)} | RETURN_ARTIFACT_TYPES
RB_TYPES = set(REAL_ARTIFACT_TYPES) | {"IMPORTED_LLM_REVIEW_V4", "GENERIC_FUSION_RUN_V1", "ANALYSIS_PACKET_V4", "MARKET_REVIEW_CONTEXT_V4", "FOOTBALL_EVIDENCE_V1"}


def decode(raw):
    data = strict_json_bytes(raw.encode())
    cls = PF_TYPES.get(data.get("schema_version"))
    if cls is None:
        raise ValueError("PF_UNKNOWN_ARTIFACT_TYPE")
    value = cls.model_validate(data)
    if canonical_json(value) != raw:
        raise ValueError("PF_CANONICAL_ARTIFACT_BYTES_REQUIRED")
    return value


def refs(value):
    """Only typed refs and typed embedded artifacts are traversed; no ID guessing."""
    found = []
    def add(v, path):
        kind = v.schema_version
        ledger = "PF" if kind in PF_TYPES else "RB" if kind in RB_TYPES else None
        if ledger is None:
            raise ValueError("PF_REFERENCE_TYPE_NOT_REGISTERED")
        found.append(dict(path=path, ledger=ledger, target_id=v.artifact_id, target_schema=kind, target_hash=v.content_hash))
    def visit(v, path):
        if isinstance(v, ArtifactRefV1):
            add(v, path)
        elif isinstance(v, BaseModel):
            if path and all(key in type(v).model_fields for key in ("artifact_id", "content_hash", "schema_version")):
                add(v, path)
                return
            for key in type(v).model_fields:
                visit(getattr(v, key), (path + "." if path else "") + key)
        elif isinstance(v, (tuple, list)):
            for index, child in enumerate(v):
                visit(child, path + f"[{index}]")
    visit(value, "")
    if isinstance(value, AtomicPortfolioLockV1):
        # The reviewed wire contract carries the hash; bind it to the concrete
        # schema-derived identity rather than leaving it an untyped string.
        digest = value.content_payload.revalidation_hash
        kind = "PORTFOLIO_LOCK_REVALIDATION_V1"
        add(ArtifactRefV1(schema_version=kind, artifact_id=stable_id(kind, digest), content_hash=digest), "content_payload.revalidation_hash")
    return tuple(sorted(found, key=lambda r: r["path"]))


def embedded_nodes(value):
    if isinstance(value, BaseModel):
        schema = getattr(value, "schema_version", None)
        if schema in RB_TYPES:
            return
        for key in type(value).model_fields:
            yield from embedded_nodes(getattr(value, key))
        if schema in PF_TYPES and not isinstance(value, ArtifactRefV1):
            yield value
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from embedded_nodes(child)


def header_valid(raw):
    try:
        refs(decode(raw))
        return 1
    except Exception:
        return 0


def reference_json(raw):
    return json.dumps(refs(decode(raw)), sort_keys=True, separators=(",", ":"))


def request_valid(operation, key, raw, digest):
    from football_system.application.portfolio_requests import REQUEST_TYPES
    from football_system.domain.market_v2 import content_hash
    try:
        request = REQUEST_TYPES[operation].model_validate(strict_json_bytes(raw.encode()))
        return int(request.request_key == key and canonical_json(request) == raw and content_hash("PF_REQUEST_" + operation, request) == digest)
    except Exception:
        return 0
