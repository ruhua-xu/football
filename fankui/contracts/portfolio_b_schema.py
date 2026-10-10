"""PF-A review schemas only. Not imported by production routes or migrations."""
from datetime import timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from football_system.domain.common import UtcDateTime
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.openfootball_snapshot import Digest, Label
from football_system.domain.production_release import ReleaseSnapshotV1, SealedReleaseArtifactV1
from football_system.domain.prospective import LockedProbabilityUnitV1
from football_system.domain.real_bridge import THREE_WAY
from football_system.domain.openfootball_production import require

Money = Annotated[int, Field(strict=True, ge=0)]


class SnapshotContentV1(ReleaseSnapshotV1):
    run: ArtifactRefV1
    program: ArtifactRefV1
    epoch: ArtifactRefV1
    anchor: ArtifactRefV1
    bucket: ArtifactRefV1
    model_pin: ArtifactRefV1
    model_source_type: Literal["OPENFOOTBALL", "OPENFOOTBALL_QUALIFIED_V2"]
    qualified_scope_hash: Digest
    competition_id: Label
    season_id: Label
    analysis: ArtifactRefV1
    packet: ArtifactRefV1
    review: ArtifactRefV1
    review_audit: ArtifactRefV1
    fusion: ArtifactRefV1
    review_mode: Literal["IMPORTED_V4", "EXPLICIT_V4_ABSTENTION"]
    frames: tuple[LockedProbabilityUnitV1, ...] = Field(min_length=1, max_length=64)
    source_cutoff: UtcDateTime
    sealed_at_utc: UtcDateTime
    eligibility_census_hash: Digest
    full_dependency_hash: Digest
    configuration_hash: Digest
    implementation_hash: Digest
    receipt: ArtifactRefV1
    provenance: Literal["LIVE_OBSERVATION", "SYNTHETIC_SOFTWARE_ACCEPTANCE"]

    @model_validator(mode="after")
    def source_shape(self):
        expected = {"run": "REAL_PROSPECTIVE_RUN_V1", "program": "REAL_OBSERVATION_PROGRAM_V1",
            "epoch": "REAL_VALIDATION_EPOCH_V1", "anchor": "REAL_EPOCH_CONFIGURATION_ANCHOR_V1",
            "bucket": "REAL_KICKOFF_BUCKET_V1", "model_pin": "REAL_MODEL_PIN_V1",
            "analysis": "REAL_MULTI_MARKET_ANALYSIS_V1", "packet": "ANALYSIS_PACKET_V4",
            "review": "IMPORTED_LLM_REVIEW_V4", "review_audit": "REAL_REVIEW_AUDIT_V1", "fusion": "GENERIC_FUSION_RUN_V1"}
        require(all(getattr(self, name).schema_version == kind for name, kind in expected.items()), "PF_SOURCE_REF_TYPE_MISMATCH")
        keys = tuple((f.identity.match_id, f.market_key.canonical) for f in self.frames)
        require(keys == tuple(sorted(set(keys))) and len({f.identity.kickoff_at_utc for f in self.frames}) == 1,
            "PF_COMPLETE_EXACT_BUCKET_REQUIRED")
        require(all((f.identity.competition_id, f.identity.season_id) == (self.competition_id, self.season_id) for f in self.frames),
            "PF_MODEL_COMPETITION_MISMATCH")
        require(all(f.market_key == THREE_WAY for f in self.frames) and len({f.model_state_hash for f in self.frames}) == 1,
            "PF_EXACT_MARKET_AND_MODEL_REQUIRED")
        require(self.source_cutoff <= self.sealed_at_utc < self.frames[0].identity.kickoff_at_utc, "PF_SNAPSHOT_TIME_INVALID")
        require(all(layer.probabilities is not None for f in self.frames for layer in f.layers
            if layer.name in {"P_market", "P_quant", "P_base", "P_final"}), "PF_WHOLE_BUCKET_UNAVAILABLE")
        return self


class PredictionSourceSnapshotV1(SealedReleaseArtifactV1[SnapshotContentV1]):
    schema_version: Literal["PREDICTION_SOURCE_SNAPSHOT_V1"] = "PREDICTION_SOURCE_SNAPSHOT_V1"
    content_payload: SnapshotContentV1


class BudgetAuthorityContentV1(ReleaseSnapshotV1):
    funding_scope_id: Label
    allocation_id: Label
    decision_cycle_id: Label
    currency: Literal["CNY_FEN"] = "CNY_FEN"
    budget_fen: Money
    target_census_hash: Digest
    policy_hash: Digest
    operator_authority: ArtifactRefV1
    exact_approval: ArtifactRefV1
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    allocation_rule: Literal["EXCLUSIVE_RING_FENCED_PORTFOLIO_CAPITAL"] = "EXCLUSIVE_RING_FENCED_PORTFOLIO_CAPITAL"

    @model_validator(mode="after")
    def window(self):
        require(self.effective_at_utc < self.expires_at_utc, "PF_BUDGET_AUTHORITY_TIME_INVALID")
        return self


class PortfolioBudgetAuthorityV1(SealedReleaseArtifactV1[BudgetAuthorityContentV1]):
    schema_version: Literal["PORTFOLIO_BUDGET_AUTHORITY_V1"] = "PORTFOLIO_BUDGET_AUTHORITY_V1"
    content_payload: BudgetAuthorityContentV1


class AssemblyContentV1(ReleaseSnapshotV1):
    snapshots: tuple[ArtifactRefV1, ...] = Field(min_length=2, max_length=64)
    budget_authority: ArtifactRefV1
    target_census_hash: Digest
    prepared_at_utc: UtcDateTime
    common_cutoff: UtcDateTime
    earliest_member_kickoff: UtcDateTime
    member_set_hash: Digest
    full_dependency_hash: Digest
    numerical_projection_hash: Digest
    strategy_plan: ArtifactRefV1
    optimizer: ArtifactRefV1
    return_evaluation: ArtifactRefV1
    status: Literal["ALLOCATED", "NO_BET"]
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money

    @model_validator(mode="after")
    def complete(self):
        ids = tuple(r.artifact_id for r in self.snapshots)
        require(ids == tuple(sorted(set(ids))) and all(r.schema_version == "PREDICTION_SOURCE_SNAPSHOT_V1" for r in self.snapshots),
            "PF_UNIQUE_ORDERED_SNAPSHOTS_REQUIRED")
        require(self.budget_authority.schema_version == "PORTFOLIO_BUDGET_AUTHORITY_V1", "PF_BUDGET_REF_REQUIRED")
        require(self.prepared_at_utc == self.common_cutoff < self.earliest_member_kickoff, "PF_ASSEMBLY_CLOCK_INVALID")
        require(self.stake_fen + self.cash_fen == self.budget_fen
            and (self.status != "NO_BET" or self.stake_fen == 0), "PF_MONEY_CONSERVATION_REQUIRED")
        return self


class PortfolioAssemblyV1(SealedReleaseArtifactV1[AssemblyContentV1]):
    schema_version: Literal["PORTFOLIO_ASSEMBLY_V1"] = "PORTFOLIO_ASSEMBLY_V1"
    content_payload: AssemblyContentV1


class LockContentV1(ReleaseSnapshotV1):
    assembly: ArtifactRefV1
    budget_authority: ArtifactRefV1
    member_set_hash: Digest
    ownership_manifest_hash: Digest
    revalidation_hash: Digest
    common_cutoff: UtcDateTime
    locked_at_utc: UtcDateTime
    earliest_member_kickoff: UtcDateTime
    receipt: ArtifactRefV1
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money

    @model_validator(mode="after")
    def final_boundary(self):
        require(self.assembly.schema_version == "PORTFOLIO_ASSEMBLY_V1" and self.budget_authority.schema_version == "PORTFOLIO_BUDGET_AUTHORITY_V1",
            "PF_LOCK_REF_TYPE_MISMATCH")
        require(self.common_cutoff <= self.locked_at_utc and self.locked_at_utc + timedelta(seconds=60) < self.earliest_member_kickoff,
            "PF_STRICT_EARLIEST_KICKOFF_LEAD_REQUIRED")
        require(self.stake_fen + self.cash_fen == self.budget_fen, "PF_MONEY_CONSERVATION_REQUIRED")
        return self


class AtomicPortfolioLockV1(SealedReleaseArtifactV1[LockContentV1]):
    schema_version: Literal["ATOMIC_PORTFOLIO_LOCK_V1"] = "ATOMIC_PORTFOLIO_LOCK_V1"
    content_payload: LockContentV1


class SettlementContentV1(ReleaseSnapshotV1):
    portfolio_lock: ArtifactRefV1
    previous: ArtifactRefV1 | None
    observations: tuple[ArtifactRefV1, ...]
    settled_at_utc: UtcDateTime
    result_set_hash: Digest
    status: Literal["SETTLED", "MISSING_RESULT", "UNSUPPORTED_SETTLEMENT_CASE"]
    missing_match_ids: tuple[Label, ...]
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    gross_payout_fen: Money | None
    ending_capital_fen: Money | None
    profit_loss_fen: int | None = Field(strict=True)

    @model_validator(mode="after")
    def accounting(self):
        require(self.portfolio_lock.schema_version == "ATOMIC_PORTFOLIO_LOCK_V1"
            and (self.previous is None or self.previous.schema_version == "PORTFOLIO_SETTLEMENT_V1"), "PF_SETTLEMENT_REF_TYPE_MISMATCH")
        require(self.stake_fen + self.cash_fen == self.budget_fen, "PF_MONEY_CONSERVATION_REQUIRED")
        complete = (self.gross_payout_fen, self.ending_capital_fen, self.profit_loss_fen)
        if self.status == "SETTLED":
            require(not self.missing_match_ids and all(v is not None for v in complete)
                and self.ending_capital_fen == self.cash_fen + self.gross_payout_fen
                and self.profit_loss_fen == self.gross_payout_fen - self.stake_fen, "PF_SETTLEMENT_MONEY_MISMATCH")
        else:
            require(all(v is None for v in complete), "PF_UNAVAILABLE_PNL_MUST_BE_NULL")
        return self


class PortfolioSettlementV1(SealedReleaseArtifactV1[SettlementContentV1]):
    schema_version: Literal["PORTFOLIO_SETTLEMENT_V1"] = "PORTFOLIO_SETTLEMENT_V1"
    content_payload: SettlementContentV1


class SnapshotRequestV1(ReleaseSnapshotV1):
    request_key: Label
    source_run_id: Label
    imported_review_id: Label
    review_mode: Literal["IMPORTED_V4", "EXPLICIT_V4_ABSTENTION"]


class AssemblyRequestV1(ReleaseSnapshotV1):
    request_key: Label
    snapshot_ids: tuple[Label, ...] = Field(min_length=2, max_length=64)
    budget_authority_id: Label
    ticket_request_bundle_id: Label


class LockRequestV1(ReleaseSnapshotV1):
    request_key: Label
    assembly_id: Label


class SettlementRequestV1(ReleaseSnapshotV1):
    request_key: Label
    portfolio_lock_id: Label
    result_observation_set_id: Label


SCHEMAS = (PredictionSourceSnapshotV1, PortfolioBudgetAuthorityV1, PortfolioAssemblyV1,
    AtomicPortfolioLockV1, PortfolioSettlementV1, SnapshotRequestV1, AssemblyRequestV1, LockRequestV1, SettlementRequestV1)
