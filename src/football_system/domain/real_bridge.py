"""Versioned real bridge contracts. No V1 synthetic class is widened or relabelled."""

from decimal import Decimal
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from football_system.domain.archive import canonical_json
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_analysis import ArtifactRefV1, FootballEvidenceV1, MarketMatchIdentityV1, MarketModelLineageV1
from football_system.domain.market_v2 import Hash, MarketArtifact, MarketKeyV2, MarketPriceV1, MarketProbabilityDistributionV1, MultiMarketModel, Probability, content_hash, reject_float
from football_system.domain.prospective import CorrectionReasonV1, LockedProbabilityUnitV1, ManualResultImportV1, ProspectivePolicyV1
from football_system.domain.prospective_evidence import EvidenceUseV1
from football_system.domain.review_v4 import GenericFusionPolicyV1, GenericFusionRunV1, ImportedReviewV4
from football_system.domain.return_distribution import ReturnOptimizationRunV1, ReturnSelectedTicketV1
from football_system.domain.services.elo_baseline import EloBaselineState, EloBaselineConfig
from football_system.domain.settlement import MatchResult
from football_system.domain.strategy_pass_v2 import Money, OutcomeCandidateV1, StrategyProfileV2, SystemTicketCandidateV2, SystemTicketV2, StructuralRiskV2, TicketRequestV2

Provenance = Literal["LIVE_OBSERVATION", "SYNTHETIC_SOFTWARE_ACCEPTANCE"]
ClockBasis = Literal["LOCAL_SYSTEM_UTC", "SYNTHETIC_TEST_CLOCK"]
THREE_WAY = MarketKeyV2(market_type="THREE_WAY")


def require(value, code):
    if not value:
        raise ValueError(code)


class RealArtifact(MarketArtifact):
    data_classification: Literal["REAL_SOURCE_DATA"] = "REAL_SOURCE_DATA"
    provenance: Provenance
    event_at_utc: UtcDateTime
    clock_basis: ClockBasis
    receipt_sequence: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def origin(self):
        require(self.provenance != "LIVE_OBSERVATION" or self.clock_basis == "LOCAL_SYSTEM_UTC", "TEST_CLOCK_NOT_LIVE")
        return self


class RealObservationProgramV1(RealArtifact):
    schema_version: Literal["REAL_OBSERVATION_PROGRAM_V1"] = "REAL_OBSERVATION_PROGRAM_V1"
    observation_program_id: Identifier
    competition_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    season_id: Identifier
    scope_reference: str = Field(min_length=1, max_length=2000)
    market_key: MarketKeyV2 = THREE_WAY

    @model_validator(mode="after")
    def scope(self):
        require(self.market_key == THREE_WAY and self.competition_ids == tuple(sorted(set(self.competition_ids))), "REAL_THREE_WAY_SCOPE_REQUIRED")
        return self


class RealBridgeImplementationIdentityV1(RealArtifact):
    schema_version: Literal["REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1"] = "REAL_BRIDGE_IMPLEMENTATION_IDENTITY_V1"
    bridge_hash: Hash
    frozen_hashes: dict[str, Hash]
    algorithm: Literal["REAL_PROSPECTIVE_DECISION_ADAPTER_V1"] = "REAL_PROSPECTIVE_DECISION_ADAPTER_V1"
    base_commit: Literal["6d633d4425d5fd6d93918d60526f701a59020b3d"] = "6d633d4425d5fd6d93918d60526f701a59020b3d"


class RealConfigurationV1(MultiMarketModel):
    rules: SportteryRules = SportteryRules(version="SPORTTERY_MVP_V1")
    constraints: PortfolioConstraints = PortfolioConstraints()
    strategy: StrategyProfileV2 = StrategyProfileV2()
    fusion: GenericFusionPolicyV1 = GenericFusionPolicyV1()
    base_policy: Literal["QUANT_ONLY_V1"] = "QUANT_ONLY_V1"
    min_selection_ev: Decimal = Decimal("0.02")
    min_ticket_roi: Decimal = Decimal("0.02")
    data_quality: Probability = Decimal("0.25")
    allowed_budgets_fen: tuple[Money, ...] = (0, 10000)
    bucket_policy: Literal["EXACT_KICKOFF_BUCKET_V1"] = "EXACT_KICKOFF_BUCKET_V1"
    market_policy: Literal["MARKET_CONSENSUS_MEDIAN_V1"] = "MARKET_CONSENSUS_MEDIAN_V1"
    source_provider: Literal["THE_ODDS_API"] = "THE_ODDS_API"
    result_source_identity: Identifier
    maximum_odds_age_seconds: int = Field(gt=0, strict=True)
    minimum_bookmaker_count: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def fixed(self):
        require(self.rules == SportteryRules(version="SPORTTERY_MVP_V1") and self.constraints == PortfolioConstraints()
                and self.strategy == StrategyProfileV2() and self.fusion == GenericFusionPolicyV1()
                and self.min_selection_ev == self.min_ticket_roi == Decimal("0.02") and self.data_quality == Decimal("0.25"), "FROZEN_CONFIGURATION_CHANGED")
        require(self.allowed_budgets_fen == tuple(sorted(set(self.allowed_budgets_fen)))
                and 1 <= len(self.allowed_budgets_fen) <= 8, "BUDGET_SET_INVALID")
        return self


class RealPolicyValuePinV1(RealArtifact):
    schema_version: Literal["REAL_POLICY_VALUE_PIN_V1"] = "REAL_POLICY_VALUE_PIN_V1"
    configuration: RealConfigurationV1
    prospective_policy: ProspectivePolicyV1
    return_policy: ArtifactRefV1
    objective: ArtifactRefV1
    market_policy_hash: Hash
    program: ArtifactRefV1


class RealSourceAdmissionFactV1(RealArtifact):
    schema_version: Literal["REAL_SOURCE_ADMISSION_FACT_V1"] = "REAL_SOURCE_ADMISSION_FACT_V1"
    program: ArtifactRefV1
    source_identity: Identifier
    kind: Literal["MARKET", "SPORTTERY", "MODEL", "EVIDENCE", "RESULT"]
    state: Literal["ADMITTED", "REVOKED"]
    effective_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    rights_reference: str = Field(min_length=1, max_length=2000)
    verified_by: Identifier
    evidence_hash: Hash
    credential_verified: bool = False
    retention_authorized: Literal[True] = True
    previous: ArtifactRefV1 | None = None

    @model_validator(mode="after")
    def validity(self):
        require(self.effective_at_utc <= self.event_at_utc < self.expires_at_utc, "ADMISSION_TIMELINE_INVALID")
        return self


class RealModelPinV1(RealArtifact):
    schema_version: Literal["REAL_MODEL_PIN_V1"] = "REAL_MODEL_PIN_V1"
    program: ArtifactRefV1
    admission: ArtifactRefV1
    source_identity: Identifier
    release_id: Identifier | None
    model_state_id: Identifier | None
    source_analysis_id: Identifier | None
    release_hash: Hash
    # Live state remains exclusively in its existing authorized store. Never
    # duplicate licensed training facts into an indefinite append-only ledger.
    state: EloBaselineState | None
    model_lineage: MarketModelLineageV1
    configuration: EloBaselineConfig
    scope_match_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    authority_hash: Hash

    @model_validator(mode="after")
    def pin(self):
        require(self.scope_match_ids == tuple(sorted(set(self.scope_match_ids))), "MODEL_PIN_TARGETS_INVALID")
        require(self.model_lineage.training_cutoff_at_utc <= self.model_lineage.generated_at_utc <= self.event_at_utc, "FUTURE_MODEL_STATE")
        require(self.configuration.config_hash == self.model_lineage.config_hash,"MODEL_CONFIGURATION_HASH_MISMATCH")
        require(self.model_lineage.model_name=="ELO_THREE_WAY_BASELINE_V1","PINNED_ELO_MODEL_REQUIRED")
        if self.provenance == "LIVE_OBSERVATION":
            require(self.release_id and self.model_state_id and self.source_analysis_id and self.state is None, "LIVE_MODEL_LINEAGE_REQUIRED")
        else:
            require(self.state is not None and self.state.state_hash==self.model_lineage.state_hash,"TEST_MODEL_STATE_REQUIRED")
        return self


class RealEpochConfigurationAnchorV1(RealArtifact):
    schema_version: Literal["REAL_EPOCH_CONFIGURATION_ANCHOR_V1"] = "REAL_EPOCH_CONFIGURATION_ANCHOR_V1"
    program: ArtifactRefV1
    implementation: RealBridgeImplementationIdentityV1
    policy: RealPolicyValuePinV1
    model_pin: ArtifactRefV1
    planned_start_at_utc: UtcDateTime
    planned_end_at_utc: UtcDateTime
    configuration_hash: Hash
    sealed_at_utc: UtcDateTime

    @property
    def created_at_utc(self):
        return self.event_at_utc

    @model_validator(mode="after")
    def window(self):
        require(self.created_at_utc <= self.sealed_at_utc < self.planned_start_at_utc < self.planned_end_at_utc, "ANCHOR_NOT_SEALED_BEFORE_START")
        require(self.configuration_hash == content_hash("REAL_CONFIGURATION_V1", [self.policy.content_hash, self.model_pin, self.implementation.content_hash]), "ANCHOR_CONFIG_HASH_MISMATCH")
        return self


class RealValidationEpochV1(RealArtifact):
    schema_version: Literal["REAL_VALIDATION_EPOCH_V1"] = "REAL_VALIDATION_EPOCH_V1"
    program: ArtifactRefV1
    anchor: ArtifactRefV1
    implementation: ArtifactRefV1
    mode: Literal["REAL_PROSPECTIVE"] = "REAL_PROSPECTIVE"
    starts_at_utc: UtcDateTime
    ends_at_utc: UtcDateTime
    configuration_hash: Hash
    previous: ArtifactRefV1 | None = None

    @model_validator(mode="after")
    def early(self):
        require(self.event_at_utc < self.starts_at_utc < self.ends_at_utc, "EPOCH_NOT_DECLARED_BEFORE_START")
        return self


class RealSlateDeclarationV1(RealArtifact):
    schema_version: Literal["REAL_SLATE_DECLARATION_V1"] = "REAL_SLATE_DECLARATION_V1"
    epoch: ArtifactRefV1
    program: ArtifactRefV1
    source_reference: str = Field(min_length=1, max_length=2000)
    source_hash: Hash
    identities: tuple[MarketMatchIdentityV1, ...] = Field(min_length=1, max_length=64)
    fixture_refs: tuple["RealFixtureRefV1", ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def matches(self):
        ids = tuple(i.match_id for i in self.identities)
        require(ids == tuple(sorted(set(ids))) and all(i.kickoff_at_utc > self.event_at_utc for i in self.identities), "SLATE_NOT_CANONICAL_PREMATCH")
        require(ids == tuple(f.match_id for f in self.fixture_refs), "COMPLETE_FIXTURE_REFS_REQUIRED")
        return self


class RealFixtureRefV1(MultiMarketModel):
    match_id: Identifier
    observation_id: Identifier
    ingestion_id: Identifier
    payload_hash: Hash


class RealKickoffBucketV1(RealArtifact):
    schema_version: Literal["REAL_KICKOFF_BUCKET_V1"] = "REAL_KICKOFF_BUCKET_V1"
    epoch: ArtifactRefV1
    program: ArtifactRefV1
    slate: ArtifactRefV1
    kickoff_at_utc: UtcDateTime
    identities: tuple[MarketMatchIdentityV1, ...] = Field(min_length=1, max_length=64)
    fixture_refs: tuple[RealFixtureRefV1, ...] = Field(min_length=1, max_length=64)
    market_key: MarketKeyV2 = THREE_WAY

    @model_validator(mode="after")
    def exact(self):
        ids = tuple(i.match_id for i in self.identities)
        require(self.market_key == THREE_WAY and ids == tuple(sorted(set(ids)))
                and all(i.kickoff_at_utc == self.kickoff_at_utc for i in self.identities), "EXACT_KICKOFF_BUCKET_REQUIRED")
        require(ids == tuple(f.match_id for f in self.fixture_refs), "COMPLETE_FIXTURE_REFS_REQUIRED")
        return self


class RealMarketSourceBindingV1(RealArtifact):
    schema_version: Literal["REAL_MARKET_SOURCE_BINDING_V1"] = "REAL_MARKET_SOURCE_BINDING_V1"
    program: ArtifactRefV1
    admission: ArtifactRefV1
    match_id: Identifier
    ingestion_id: Identifier
    snapshot_id: Identifier
    capture_hash: Hash
    payload_hash: Hash
    constituent_refs: tuple[tuple[Identifier, Hash], ...] = Field(min_length=1, max_length=64)
    probabilities: MarketProbabilityDistributionV1
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime
    status: Literal["AVAILABLE"] = "AVAILABLE"
    policy: Literal["MARKET_CONSENSUS_MEDIAN_V1"] = "MARKET_CONSENSUS_MEDIAN_V1"


class RealSpSourceBindingV1(RealArtifact):
    schema_version: Literal["REAL_SP_SOURCE_BINDING_V1"] = "REAL_SP_SOURCE_BINDING_V1"
    program: ArtifactRefV1
    admission: ArtifactRefV1
    match_id: Identifier
    ingestion_id: Identifier
    snapshot_id: Identifier
    capture_hash: Hash
    payload_hash: Hash
    market_key: MarketKeyV2 = THREE_WAY
    prices: tuple[MarketPriceV1, ...]
    sale_status: Literal["OPEN", "CLOSED", "UNKNOWN"]
    captured_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    ingested_at_utc: UtcDateTime

    @model_validator(mode="after")
    def catalog(self):
        require(self.market_key == THREE_WAY and tuple(p.outcome for p in self.prices) == THREE_WAY.catalog, "REAL_SP_CATALOG_MISMATCH")
        return self


class RealModelAvailabilityFactV1(RealArtifact):
    schema_version: Literal["REAL_MODEL_AVAILABILITY_FACT_V1"] = "REAL_MODEL_AVAILABILITY_FACT_V1"
    pin: ArtifactRefV1
    match_id: Identifier
    status: Literal["AVAILABLE", "MODEL_UNAVAILABLE"]
    reason: str | None
    probabilities: MarketProbabilityDistributionV1 | None
    decision_cutoff: UtcDateTime

    @model_validator(mode="after")
    def available(self):
        require((self.status=="AVAILABLE" and self.probabilities is not None and self.reason is None)
                or (self.status=="MODEL_UNAVAILABLE" and self.probabilities is None and bool(self.reason)),"MODEL_AVAILABILITY_MISMATCH")
        require(self.probabilities is None or self.probabilities.market_key==THREE_WAY,"REAL_THREE_WAY_ONLY")
        return self


class RealModelPinInvalidationV1(RealArtifact):
    schema_version: Literal["REAL_MODEL_PIN_INVALIDATION_V1"] = "REAL_MODEL_PIN_INVALIDATION_V1"
    pin: ArtifactRefV1
    reason: str = Field(min_length=1, max_length=2000)
    evidence_hash: Hash


class RealSourceInvalidationV1(RealArtifact):
    schema_version: Literal["REAL_SOURCE_INVALIDATION_V1"] = "REAL_SOURCE_INVALIDATION_V1"
    source: ArtifactRefV1
    reason: str = Field(min_length=1,max_length=2000)
    evidence_hash: Hash


class RealAnalysisUnitV1(RealArtifact):
    schema_version: Literal["REAL_ANALYSIS_UNIT_V1"] = "REAL_ANALYSIS_UNIT_V1"
    identity: MarketMatchIdentityV1
    fixture: RealFixtureRefV1
    market_key: MarketKeyV2 = THREE_WAY
    decision_cutoff: UtcDateTime
    consensus: RealMarketSourceBindingV1
    sporttery: RealSpSourceBindingV1
    model: RealModelAvailabilityFactV1
    model_lineage: MarketModelLineageV1
    p_quant: MarketProbabilityDistributionV1 | None
    p_base: MarketProbabilityDistributionV1 | None
    quant_status: Literal["AVAILABLE", "MODEL_UNAVAILABLE"]
    unavailable_reason: str | None
    data_quality: Probability = Decimal("0.25")
    evidence: tuple[FootballEvidenceV1, ...] = Field(default=(), max_length=32)
    evidence_uses: tuple[EvidenceUseV1, ...] = Field(default=(), max_length=128)

    @property
    def legacy_source(self):
        """Explicit new real type: never a fabricated LegacyThreeWayInputV1."""
        return None

    @model_validator(mode="after")
    def bound(self):
        require(self.market_key == THREE_WAY == self.consensus.probabilities.market_key == self.sporttery.market_key, "REAL_THREE_WAY_ONLY")
        require(self.identity.match_id == self.consensus.match_id == self.sporttery.match_id == self.model.match_id, "REAL_UNIT_IDENTITY_MISMATCH")
        require(self.fixture.match_id == self.identity.match_id and self.decision_cutoff == self.event_at_utc == self.model.decision_cutoff, "REAL_UNIT_RECEIPT_MISMATCH")
        require(self.p_quant == self.model.probabilities and self.quant_status == self.model.status
                and ((self.p_base is None) == (self.p_quant is None)), "QUANT_BASE_AVAILABILITY_MISMATCH")
        require(self.unavailable_reason==self.model.reason and (self.p_base is None or self.p_base.market_key==THREE_WAY),"REAL_BASE_STATUS_OR_MARKET_MISMATCH")
        require(max(self.consensus.ingested_at_utc, self.sporttery.ingested_at_utc, self.model_lineage.generated_at_utc) <= self.decision_cutoff < self.identity.kickoff_at_utc, "REAL_UNIT_LOOKAHEAD")
        require(all(e.match_id == self.identity.match_id and e.ingested_at_utc <= self.decision_cutoff for e in self.evidence), "REAL_EVIDENCE_SCOPE_OR_TIME")
        return self


class RealMultiMarketAnalysisV1(RealArtifact):
    schema_version: Literal["REAL_MULTI_MARKET_ANALYSIS_V1"] = "REAL_MULTI_MARKET_ANALYSIS_V1"
    program: ArtifactRefV1
    epoch: ArtifactRefV1
    anchor: ArtifactRefV1
    bucket: ArtifactRefV1
    units: tuple[RealAnalysisUnitV1, ...] = Field(min_length=1, max_length=64)
    configuration: RealConfigurationV1
    configuration_hash: Hash
    status: Literal["READY", "UNAVAILABLE"]
    reason: str | None

    @property
    def rules(self):
        return self.configuration.rules

    @property
    def constraints(self):
        return self.configuration.constraints

    @property
    def budgets_fen(self):
        return self.configuration.allowed_budgets_fen

    @property
    def min_selection_ev(self):
        return self.configuration.min_selection_ev

    @property
    def min_ticket_roi(self):
        return self.configuration.min_ticket_roi

    @model_validator(mode="after")
    def whole(self):
        ids = tuple(u.identity.match_id for u in self.units)
        require(ids == tuple(sorted(set(ids))) and len({u.identity.kickoff_at_utc for u in self.units}) == 1, "REAL_ANALYSIS_BUCKET_MISMATCH")
        require((self.status == "READY") == all(u.p_base is not None for u in self.units), "WHOLE_BUCKET_UNAVAILABLE_REQUIRED")
        require(all(u.provenance == self.provenance for u in self.units), "MIXED_TEST_LIVE_PROVENANCE")
        require(all(u.event_at_utc==self.event_at_utc and u.receipt_sequence==self.receipt_sequence for u in self.units),"REAL_UNIT_CUTOFF_OR_RECEIPT_MISMATCH")
        return self


class RealProspectiveRunV1(RealArtifact):
    schema_version: Literal["REAL_PROSPECTIVE_RUN_V1"] = "REAL_PROSPECTIVE_RUN_V1"
    program: ArtifactRefV1
    epoch: ArtifactRefV1
    anchor: ArtifactRefV1
    bucket: ArtifactRefV1
    analysis: ArtifactRefV1
    packet: ArtifactRefV1 | None
    identities: tuple[MarketMatchIdentityV1, ...]
    evidence: tuple[EvidenceUseV1, ...]
    budget_fen: Money
    decision_cutoff: UtcDateTime
    prepared_at_utc: UtcDateTime
    implementation_hash: Hash
    configuration_hash: Hash
    full_dependency_hash: Hash
    status: Literal["PREPARING", "UNAVAILABLE"]
    reason: str | None
    previous: ArtifactRefV1 | None = None
    supersedes_lock: ArtifactRefV1 | None = None
    chain_root: ArtifactRefV1 | None = None
    position: int = Field(default=0, ge=0, le=255, strict=True)

    @model_validator(mode="after")
    def times(self):
        require(self.prepared_at_utc == self.decision_cutoff == self.event_at_utc, "FORGED_REAL_PREPARE_TIME")
        require(len({i.kickoff_at_utc for i in self.identities}) == 1, "EXACT_KICKOFF_BUCKET_REQUIRED")
        require(self.status != "PREPARING" or self.packet is not None, "READY_PACKET_REQUIRED")
        require((self.previous is None) == (self.chain_root is None and self.position == 0), "REAL_CHAIN_POSITION_INVALID")
        return self


class PreLockReplacementV1(RealArtifact):
    schema_version: Literal["PRE_LOCK_REPLACEMENT_V1"] = "PRE_LOCK_REPLACEMENT_V1"
    old_run: ArtifactRefV1
    new_run: ArtifactRefV1
    bucket: ArtifactRefV1
    epoch: ArtifactRefV1
    reason: str = Field(min_length=1, max_length=2000)


class RealReviewAuditV1(RealArtifact):
    schema_version: Literal["REAL_REVIEW_AUDIT_V1"] = "REAL_REVIEW_AUDIT_V1"
    run: ArtifactRefV1
    review: ArtifactRefV1
    reasons: tuple[CorrectionReasonV1, ...]


class RealStrategySourceV1(RealArtifact):
    schema_version: Literal["REAL_STRATEGY_SOURCE_V1"] = "REAL_STRATEGY_SOURCE_V1"
    analysis: RealMultiMarketAnalysisV1
    review: ImportedReviewV4
    fusion: GenericFusionRunV1
    budget_fen: Money
    selections: tuple[OutcomeCandidateV1, ...] = Field(max_length=1984)

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.review_v4 import fuse_v4
        from football_system.domain.services.strategy_pass_v2 import outcome_candidates
        require(self.analysis.provenance == self.provenance and self.budget_fen in self.analysis.budgets_fen
                and self.fusion == fuse_v4(self.analysis, self.review, self.fusion.policy)
                and self.selections == outcome_candidates(self.analysis, self.fusion), "REAL_STRATEGY_SOURCE_REPLAY_MISMATCH")
        return self


class RealStrategyPlanV1(RealArtifact):
    schema_version: Literal["REAL_STRATEGY_PLAN_V1"] = "REAL_STRATEGY_PLAN_V1"
    source: RealStrategySourceV1
    profile: StrategyProfileV2
    requests: tuple[TicketRequestV2, ...]
    candidates: tuple[SystemTicketCandidateV2, ...] = Field(max_length=4096)
    decisions: tuple[str, ...]
    tickets: tuple[SystemTicketV2, ...] = Field(max_length=8)
    risk: StructuralRiskV2
    total_stake_fen: Money
    cash_fen: Money
    status: Literal["ALLOCATED", "NO_BET"]
    reason: str | None

    @model_validator(mode="after")
    def replay(self):
        from football_system.domain.services.strategy_pass_v2 import plan_values
        expected = plan_values(self.source, self.profile, self.requests)
        require(all(getattr(self, k) == v for k, v in expected.items()), "REAL_STRATEGY_PLAN_REPLAY_MISMATCH")
        return self


class RealCalculationBindingV1(RealArtifact):
    schema_version: Literal["REAL_CALCULATION_BINDING_V1"] = "REAL_CALCULATION_BINDING_V1"
    run: ArtifactRefV1
    plan: ArtifactRefV1
    optimizer: ReturnOptimizationRunV1
    review_audit: RealReviewAuditV1


class DecisionLockV2(RealArtifact):
    schema_version: Literal["DECISION_LOCK_V2"] = "DECISION_LOCK_V2"
    program: ArtifactRefV1
    run: ArtifactRefV1
    epoch: ArtifactRefV1
    anchor: ArtifactRefV1
    bucket: ArtifactRefV1
    analysis: ArtifactRefV1
    packet: ArtifactRefV1
    review: ArtifactRefV1
    calculation: ArtifactRefV1
    strategy_plan: ArtifactRefV1
    return_evaluation: ArtifactRefV1
    locked_at_utc: UtcDateTime
    earliest_kickoff_at_utc: UtcDateTime
    frames: tuple[LockedProbabilityUnitV1, ...]
    selected: tuple[ReturnSelectedTicketV1, ...]
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    configuration_hash: Hash
    implementation_hash: Hash
    full_dependency_hash: Hash
    revalidation_hash: Hash

    @model_validator(mode="after")
    def money_time(self):
        require(self.locked_at_utc == self.event_at_utc < self.earliest_kickoff_at_utc, "FORGED_REAL_LOCK_TIME")
        require(self.stake_fen == sum(s.stake_fen for s in self.selected) and self.cash_fen+self.stake_fen == self.budget_fen, "REAL_LOCK_MONEY_MISMATCH")
        return self


class RealPreKickoffInvalidationV1(RealArtifact):
    schema_version: Literal["REAL_PRE_KICKOFF_INVALIDATION_V1"] = "REAL_PRE_KICKOFF_INVALIDATION_V1"
    old_lock: ArtifactRefV1
    new_lock: ArtifactRefV1
    reason: str = Field(min_length=1, max_length=2000)


class RealProspectiveSettlementV1(RealArtifact):
    schema_version: Literal["REAL_PROSPECTIVE_SETTLEMENT_V1"] = "REAL_PROSPECTIVE_SETTLEMENT_V1"
    run: ArtifactRefV1
    decision_lock: ArtifactRefV1
    previous: ArtifactRefV1 | None
    settled_at_utc: UtcDateTime
    observations: tuple[ArtifactRefV1, ...]
    reason: Literal["SETTLED", "MISSING_RESULT", "UNSUPPORTED_SETTLEMENT_CASE"]
    missing_match_ids: tuple[Identifier, ...]
    unsupported_match_ids: tuple[Identifier, ...]
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    gross_payout_fen: Money | None
    ending_capital_fen: Money | None
    profit_loss_fen: int | None
    realized_buckets: dict[str, bool] | None


class RealResultObservationV1(RealArtifact):
    schema_version: Literal["REAL_RESULT_OBSERVATION_V1"] = "REAL_RESULT_OBSERVATION_V1"
    program: ArtifactRefV1
    admission: ArtifactRefV1
    claim: ManualResultImportV1
    normalized_result: MatchResult | None
    previous: ArtifactRefV1 | None
    ingested_at_utc: UtcDateTime

    @model_validator(mode="after")
    def result(self):
        require(self.claim.data_classification == "REAL_SOURCE_DATA" and self.ingested_at_utc == self.event_at_utc,
                "REAL_RESULT_ORIGIN_MISMATCH")
        require(max(self.claim.available_at_utc, self.claim.verified_at_utc) <= self.ingested_at_utc < self.claim.retention_until_utc,
                "REAL_RESULT_TIMELINE_INVALID")
        return self


class RealCensusRowV1(MultiMarketModel):
    run: ArtifactRefV1 | None
    request_key: Identifier
    status: Literal["PREPARING", "REPLACED_PRE_LOCK", "UNAVAILABLE", "REJECTED_REQUEST", "LOCKED", "INVALIDATED_PRE_KICKOFF", "SETTLED", "STALE_SETTLEMENT"]
    reason: str | None
    lock: ArtifactRefV1 | None = None
    settlement: ArtifactRefV1 | None = None
    replacement: ArtifactRefV1 | None = None


class RealValidationReportV1(RealArtifact):
    schema_version: Literal["REAL_VALIDATION_REPORT_V1"] = "REAL_VALIDATION_REPORT_V1"
    program: ArtifactRefV1
    epoch: ArtifactRefV1
    anchor: ArtifactRefV1
    as_of_at_utc: UtcDateTime
    receipt_watermark: int = Field(ge=0, strict=True)
    census: tuple[RealCensusRowV1, ...]
    census_hash: Hash
    metrics: dict
    official_prediction_count: int = Field(ge=0, strict=True)
    performance_evidence_status: Literal["INSUFFICIENT_PROSPECTIVE_SAMPLE", "DESCRIPTIVE_REAL_SAMPLE_AVAILABLE"]

    @field_validator("metrics", mode="before")
    @classmethod
    def canonical_metrics(cls, value):
        return json.loads(canonical_json(reject_float(value)))

    @model_validator(mode="after")
    def test_is_not_live(self):
        require(self.provenance != "SYNTHETIC_SOFTWARE_ACCEPTANCE" or
                (self.official_prediction_count == 0 and self.performance_evidence_status == "INSUFFICIENT_PROSPECTIVE_SAMPLE"), "TEST_NOT_REAL_PERFORMANCE")
        return self


class RealEpochCloseV1(RealArtifact):
    schema_version: Literal["REAL_EPOCH_CLOSE_V1"] = "REAL_EPOCH_CLOSE_V1"
    epoch: ArtifactRefV1
    census_hash: Hash
    receipt_watermark: int = Field(ge=0, strict=True)


REAL_ARTIFACT_TYPES = {cls.model_fields["schema_version"].default: cls for cls in (
    RealObservationProgramV1, RealBridgeImplementationIdentityV1, RealPolicyValuePinV1,
    RealSourceAdmissionFactV1, RealModelPinV1, RealEpochConfigurationAnchorV1, RealValidationEpochV1,
    RealSlateDeclarationV1, RealKickoffBucketV1, RealMarketSourceBindingV1, RealSpSourceBindingV1,
    RealModelAvailabilityFactV1, RealModelPinInvalidationV1, RealSourceInvalidationV1, RealAnalysisUnitV1, RealMultiMarketAnalysisV1, RealProspectiveRunV1,
    PreLockReplacementV1, RealReviewAuditV1, RealStrategySourceV1, RealStrategyPlanV1,
    RealCalculationBindingV1, DecisionLockV2, RealPreKickoffInvalidationV1, RealProspectiveSettlementV1, RealResultObservationV1,
    RealValidationReportV1, RealEpochCloseV1,
)}
