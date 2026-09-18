"""Append-only prospective lifecycle contracts; v0.9 math/wire remains frozen."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import Identifier, UtcDateTime
from football_system.domain.market_analysis import ArtifactRefV1, MarketMatchIdentityV1
from football_system.domain.market_v2 import Hash, MarketArtifact, MarketKeyV2, MarketProbabilityDistributionV1, MultiMarketModel, Probability, fixed_decimal, reject_float
from football_system.domain.prospective_evidence import (
    AssertionClass, EvidenceSnapshotV1, EvidenceUseV1, FactCategory,
    ProspectiveEvidenceBindingV1,
)
from football_system.domain.return_distribution import ReturnSelectedTicketV1
from football_system.domain.settlement import MatchResult
from football_system.domain.strategy_pass_v2 import Money, StrategyProfileV2
from football_system.domain.review_v4 import GenericFusionPolicyV1

ClockBasis = Literal["LOCAL_SYSTEM_UTC", "SYNTHETIC_TEST_CLOCK"]
DataClass = Literal["SYNTHETIC", "REAL_SOURCE_DATA"]


class ProspectivePolicyV1(MarketArtifact):
    schema_version: Literal["PROSPECTIVE_POLICY_V1"] = "PROSPECTIVE_POLICY_V1"
    implementation_hash: Hash
    frozen_math_hash: Hash
    minimum_lock_lead_seconds: int = Field(default=60, ge=1, le=3600, strict=True)
    minimum_real_observations: int = Field(default=30, ge=1, le=100000, strict=True)
    max_epoch_runs: int = Field(default=256, ge=1, le=256, strict=True)
    max_report_units: int = Field(default=4096, ge=1, le=4096, strict=True)
    max_evidence_per_run: int = Field(default=128, ge=0, le=128, strict=True)
    calibration_bins: Literal[10] = 10
    metric_precision: Literal[128] = 128
    log_loss_policy: Literal["ZERO_TRUE_PROBABILITY_REPORTED_INFINITE_V1"] = "ZERO_TRUE_PROBABILITY_REPORTED_INFINITE_V1"
    neutral_policy: Literal["EXACT_ZERO_BRIER_DELTA_V1"] = "EXACT_ZERO_BRIER_DELTA_V1"
    reject_stale_evidence: Literal[True] = True
    provider_http_enabled: Literal[False] = False
    category_max_age_seconds: dict[FactCategory, Annotated[int, Field(ge=0, le=2592000, strict=True)]] = {
        FactCategory.LINEUP: 7200, FactCategory.EXPECTED_LINEUP: 86400,
        FactCategory.INJURY: 172800, FactCategory.SUSPENSION: 172800,
        FactCategory.SCHEDULE: 604800, FactCategory.REST: 604800, FactCategory.FORM: 604800,
        FactCategory.MOTIVATION: 86400, FactCategory.ODDS_CONTEXT: 3600, FactCategory.OTHER_VERIFIED_FACT: 86400,
    }

    @model_validator(mode="after")
    def complete_categories(self):
        if set(self.category_max_age_seconds) != set(FactCategory):
            raise ValueError("FRESHNESS_POLICY_MUST_COVER_ALL_CATEGORIES")
        return self


class ModelConfigurationPinV1(MultiMarketModel):
    model_name: Identifier
    model_version: Identifier
    config_hash: Hash
    training_data_hash: Hash


class MarketPolicyPinV1(MultiMarketModel):
    market_type: Identifier
    base_policy: Identifier
    quant_weight: Probability


class EpochConfigurationV1(MultiMarketModel):
    models: tuple[ModelConfigurationPinV1, ...] = Field(min_length=1, max_length=64)
    market_policies: tuple[MarketPolicyPinV1, ...] = Field(min_length=1, max_length=64)
    strategy_profile: StrategyProfileV2
    fusion_policy: GenericFusionPolicyV1
    rules: SportteryRules
    risk: PortfolioConstraints
    min_selection_ev: Decimal = Field(ge=0, allow_inf_nan=False)
    min_ticket_roi: Decimal = Field(ge=0, allow_inf_nan=False)
    allowed_budgets_fen: tuple[Money, ...] = Field(min_length=1, max_length=8)
    return_policy: ArtifactRefV1
    objective_profile: ArtifactRefV1
    configuration_hash: Hash

    @model_validator(mode="after")
    def seal(self):
        from football_system.domain.market_v2 import content_hash
        data = self.model_dump(mode="json", exclude={"configuration_hash"})
        if content_hash("EPOCH_CONFIGURATION_V1", data) != self.configuration_hash:
            raise ValueError("EPOCH_CONFIGURATION_HASH_MISMATCH")
        return self


class ValidationEpochV1(MarketArtifact):
    schema_version: Literal["VALIDATION_EPOCH_V1"] = "VALIDATION_EPOCH_V1"
    name: Identifier
    mode: Literal["SYNTHETIC", "REAL_PROSPECTIVE"]
    starts_at_utc: UtcDateTime
    ends_at_utc: UtcDateTime
    created_at_utc: UtcDateTime
    clock_basis: ClockBasis
    seed_plan: ArtifactRefV1
    policy: ProspectivePolicyV1
    configuration: EpochConfigurationV1
    result_source_identity: Identifier
    previous_epoch: ArtifactRefV1 | None = None

    @model_validator(mode="after")
    def window(self):
        if not self.created_at_utc <= self.starts_at_utc < self.ends_at_utc:
            raise ValueError("EPOCH_MUST_BE_DECLARED_BEFORE_START")
        if self.mode == "REAL_PROSPECTIVE" and self.clock_basis != "LOCAL_SYSTEM_UTC":
            raise ValueError("TEST_CLOCK_CANNOT_OPEN_REAL_EPOCH")
        return self


class ProspectiveRunV1(MarketArtifact):
    schema_version: Literal["PROSPECTIVE_RUN_V1"] = "PROSPECTIVE_RUN_V1"
    epoch: ArtifactRefV1
    slate_key: Identifier
    slate_date: date
    decision_cutoff: UtcDateTime
    prepared_at_utc: UtcDateTime
    clock_basis: ClockBasis
    source_observed_at_utc: UtcDateTime
    source_time_basis: Literal["LOCAL_SEALED_GRAPH_OBSERVATION_NOT_HISTORICAL_PUBLICATION"] = "LOCAL_SEALED_GRAPH_OBSERVATION_NOT_HISTORICAL_PUBLICATION"
    data_classification: DataClass
    analysis: ArtifactRefV1
    packet: ArtifactRefV1 | None
    identities: tuple[MarketMatchIdentityV1, ...] = Field(min_length=1, max_length=64)
    evidence: tuple[EvidenceUseV1, ...] = Field(max_length=128)
    budget_fen: Money
    implementation_hash: Hash
    configuration_hash: Hash
    input_artifact_hashes: tuple[ArtifactRefV1, ...]
    complete_input_hash: Hash
    status: Literal["PREPARING", "UNAVAILABLE"]
    reason: str | None
    supersedes: ArtifactRefV1 | None = None

    @model_validator(mode="after")
    def boundary(self):
        ids = tuple(i.match_id for i in self.identities)
        if ids != tuple(sorted(set(ids))) or self.source_observed_at_utc != self.decision_cutoff or self.prepared_at_utc != self.decision_cutoff:
            raise ValueError("PREPARE_TRUSTED_CUTOFF_OR_IDENTITY_INVALID")
        if (self.status == "PREPARING") != (self.packet is not None and self.reason is None):
            raise ValueError("PREPARE_AVAILABILITY_CONFLICT")
        if self.clock_basis == "SYNTHETIC_TEST_CLOCK" and self.data_classification != "SYNTHETIC":
            raise ValueError("TEST_CLOCK_IS_NOT_REAL_EVIDENCE")
        return self


CorrectionCategory = Literal["CONFIRMED_LINEUP_CHANGE", "KEY_INJURY", "SUSPENSION", "ROTATION", "REST_ADVANTAGE", "SCHEDULE_CONGESTION", "MOTIVATION_EVIDENCE", "ODDS_CONTEXT", "DATA_QUALITY_DOWNGRADE", "OTHER"]


class CorrectionReasonV1(MultiMarketModel):
    match_id: Identifier
    market_key: MarketKeyV2
    review_context_id: Identifier
    categories: tuple[CorrectionCategory, ...] = Field(min_length=1, max_length=10)
    assertion_class: AssertionClass
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_snapshot_ids: tuple[Identifier, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.categories)) != len(self.categories) or self.categories != tuple(sorted(self.categories)):
            raise ValueError("CORRECTION_CATEGORIES_MUST_BE_UNIQUE_CANONICAL")
        if self.evidence_snapshot_ids != tuple(sorted(set(self.evidence_snapshot_ids))):
            raise ValueError("CORRECTION_EVIDENCE_MUST_BE_UNIQUE_CANONICAL")
        return self


class V4CorrectionAuditV1(MarketArtifact):
    schema_version: Literal["V4_CORRECTION_AUDIT_V1"] = "V4_CORRECTION_AUDIT_V1"
    run: ArtifactRefV1
    review: ArtifactRefV1
    raw_review_hash: Hash
    imported_at_utc: UtcDateTime
    clock_basis: ClockBasis
    reasons: tuple[CorrectionReasonV1, ...] = Field(min_length=1, max_length=64)


LayerName = Literal["P_market", "P_quant", "P_base", "P_llm", "P_final"]


class LockedLayerV1(MultiMarketModel):
    name: LayerName
    probabilities: MarketProbabilityDistributionV1 | None
    distribution_hash: Hash | None

    @model_validator(mode="after")
    def seal(self):
        if self.distribution_hash != (self.probabilities.distribution_hash if self.probabilities else None):
            raise ValueError("LOCKED_LAYER_HASH_MISMATCH")
        return self


class LockedProbabilityUnitV1(MultiMarketModel):
    unit: ArtifactRefV1
    identity: MarketMatchIdentityV1
    market_key: MarketKeyV2
    layers: tuple[LockedLayerV1, ...]
    sp_snapshot: ArtifactRefV1
    model_state_hash: Hash
    review_status: Literal["VALID", "UNAVAILABLE"]
    correction_categories: tuple[CorrectionCategory, ...]

    @model_validator(mode="after")
    def layers_complete(self):
        if tuple(p.name for p in self.layers) != ("P_market", "P_quant", "P_base", "P_llm", "P_final"):
            raise ValueError("LOCK_REQUIRES_ALL_LAYER_AVAILABILITY_RECORDS")
        if any(p.probabilities and p.probabilities.market_key != self.market_key for p in self.layers):
            raise ValueError("LOCK_LAYER_MARKET_MISMATCH")
        return self


class DecisionLockV1(MarketArtifact):
    schema_version: Literal["DECISION_LOCK_V1"] = "DECISION_LOCK_V1"
    run: ArtifactRefV1
    epoch: ArtifactRefV1
    locked_at_utc: UtcDateTime
    clock_basis: ClockBasis
    data_classification: DataClass
    earliest_kickoff_at_utc: UtcDateTime
    analysis: ArtifactRefV1
    packet: ArtifactRefV1
    review: ArtifactRefV1
    correction_audit: ArtifactRefV1
    fusion: ArtifactRefV1
    strategy_plan: ArtifactRefV1
    optimizer: ArtifactRefV1
    return_evaluation: ArtifactRefV1
    frames: tuple[LockedProbabilityUnitV1, ...] = Field(min_length=1, max_length=64)
    selected: tuple[ReturnSelectedTicketV1, ...] = Field(max_length=8)
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    p_market_hash: Hash
    p_quant_hash: Hash
    p_llm_review_hash: Hash
    p_final_hash: Hash
    sp_hashes: tuple[ArtifactRefV1, ...]
    strategy_hash: Hash
    optimizer_hash: Hash
    implementation_hash: Hash
    configuration_hash: Hash
    complete_dependency_hash: Hash

    @model_validator(mode="after")
    def lock(self):
        if self.locked_at_utc >= self.earliest_kickoff_at_utc:
            raise ValueError("LOOKAHEAD_RISK")
        if self.stake_fen != sum(s.stake_fen for s in self.selected) or self.cash_fen+self.stake_fen != self.budget_fen:
            raise ValueError("LOCK_MONEY_MISMATCH")
        if self.strategy_hash != self.strategy_plan.content_hash or self.optimizer_hash != self.optimizer.content_hash:
            raise ValueError("LOCK_DECISION_HASH_MISMATCH")
        return self


class PreKickoffInvalidationV1(MarketArtifact):
    schema_version: Literal["PRE_KICKOFF_INVALIDATION_V1"] = "PRE_KICKOFF_INVALIDATION_V1"
    old_run: ArtifactRefV1
    old_lock: ArtifactRefV1
    new_run: ArtifactRefV1
    replacement_lock: ArtifactRefV1
    invalidated_at_utc: UtcDateTime
    deadline_at_utc: UtcDateTime
    status: Literal["INVALIDATED_PRE_KICKOFF"] = "INVALIDATED_PRE_KICKOFF"
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def before(self):
        if self.invalidated_at_utc >= self.deadline_at_utc or self.old_run == self.new_run:
            raise ValueError("POST_KICKOFF_SUPERSESSION_FORBIDDEN")
        return self


class ManualResultImportV1(MultiMarketModel):
    source_type: Literal["MANUAL_VERIFIED_IMPORT_V1"] = "MANUAL_VERIFIED_IMPORT_V1"
    match_id: Identifier
    data_classification: DataClass
    source_identity: Identifier
    source_reference: str = Field(min_length=1, max_length=1024)
    source_file: str = Field(min_length=1, max_length=512)
    source_hash: Hash
    verified_by: Identifier
    verified_at_utc: UtcDateTime
    observed_at_utc: UtcDateTime
    available_at_utc: UtcDateTime
    published_at_utc: UtcDateTime | None = None
    source_version_id: Identifier | None = None
    previous_source_version_id: Identifier | None = None
    status: Literal["FT", "CANCELLED", "POSTPONED", "VOID", "EXTRA_TIME", "PENALTIES", "UNKNOWN"]
    regular_time_semantics: Literal["REGULATION_ONLY", "UNPROVEN"]
    home_goals: int | None = Field(default=None, ge=0, le=1000, strict=True)
    away_goals: int | None = Field(default=None, ge=0, le=1000, strict=True)
    supersedes_observation_id: Identifier | None = None
    rights_reference: str = Field(min_length=1, max_length=1024)
    retention_until_utc: UtcDateTime

    @model_validator(mode="after")
    def result(self):
        if self.observed_at_utc > self.available_at_utc or (self.published_at_utc and self.published_at_utc > self.available_at_utc):
            raise ValueError("RESULT_SOURCE_TIMELINE_INVALID")
        usable = self.status == "FT" and self.regular_time_semantics == "REGULATION_ONLY"
        if usable != (self.home_goals is not None and self.away_goals is not None):
            raise ValueError("REGULAR_TIME_RESULT_FIELDS_REQUIRED_WITHOUT_INFERENCE")
        return self


class ResultObservationV1(MarketArtifact):
    schema_version: Literal["RESULT_OBSERVATION_V1"] = "RESULT_OBSERVATION_V1"
    claim: ManualResultImportV1
    ingested_at_utc: UtcDateTime
    clock_basis: ClockBasis
    source_payload_hash: Hash
    normalized_result: MatchResult | None
    previous: ArtifactRefV1 | None
    revision_capability: Literal["SOURCE_DECLARED_VERSION_CHAIN", "SOURCE_REVISION_CHAIN_UNAVAILABLE"]
    local_revision_semantics: Literal["APPEND_ONLY_LOCAL_RECEIPT_CHAIN_NOT_HISTORICAL_PUBLICATION"] = "APPEND_ONLY_LOCAL_RECEIPT_CHAIN_NOT_HISTORICAL_PUBLICATION"

    @model_validator(mode="after")
    def timeline(self):
        if max(self.claim.verified_at_utc, self.claim.available_at_utc) > self.ingested_at_utc or self.claim.retention_until_utc <= self.ingested_at_utc:
            raise ValueError("RESULT_RECEIPT_OR_RETENTION_INVALID")
        if self.normalized_result is not None and (self.normalized_result.match_id != self.claim.match_id
                or self.normalized_result.home_goals != self.claim.home_goals or self.normalized_result.away_goals != self.claim.away_goals
                or self.normalized_result.available_at_utc != self.ingested_at_utc or self.normalized_result.ingested_at_utc != self.ingested_at_utc):
            raise ValueError("NORMALIZED_RESULT_RECEIPT_BINDING_INVALID")
        if self.clock_basis == "SYNTHETIC_TEST_CLOCK" and self.claim.data_classification != "SYNTHETIC":
            raise ValueError("TEST_CLOCK_IS_NOT_REAL_EVIDENCE")
        return self


class ProspectiveSettlementV1(MarketArtifact):
    schema_version: Literal["PROSPECTIVE_SETTLEMENT_V1"] = "PROSPECTIVE_SETTLEMENT_V1"
    run: ArtifactRefV1
    decision_lock: ArtifactRefV1
    previous: ArtifactRefV1 | None
    settled_at_utc: UtcDateTime
    observations: tuple[ArtifactRefV1, ...] = Field(max_length=64)
    reason: Literal["SETTLED", "MISSING_RESULT", "UNSUPPORTED_SETTLEMENT_CASE"]
    missing_match_ids: tuple[Identifier, ...]
    unsupported_match_ids: tuple[Identifier, ...]
    budget_fen: Money
    stake_fen: Money
    cash_fen: Money
    gross_payout_fen: Money | None
    ending_capital_fen: Money | None
    profit_loss_fen: int | None = Field(default=None, strict=True)
    realized_buckets: dict[str, bool] | None
    policy: Literal["FROZEN_RETURN_EVALUATOR_REGULAR_TIME_V1"] = "FROZEN_RETURN_EVALUATOR_REGULAR_TIME_V1"

    @model_validator(mode="after")
    def money(self):
        if self.cash_fen+self.stake_fen != self.budget_fen:
            raise ValueError("PROSPECTIVE_SETTLEMENT_CASH_MISMATCH")
        if self.reason == "SETTLED":
            if (self.missing_match_ids or self.unsupported_match_ids or self.gross_payout_fen is None
                    or self.ending_capital_fen != self.cash_fen+self.gross_payout_fen
                    or self.profit_loss_fen != self.ending_capital_fen-self.budget_fen or self.realized_buckets is None):
                raise ValueError("PROSPECTIVE_SETTLEMENT_MATH_MISMATCH")
        elif any(v is not None for v in (self.gross_payout_fen, self.ending_capital_fen, self.profit_loss_fen, self.realized_buckets)):
            raise ValueError("UNAVAILABLE_SETTLEMENT_CANNOT_INVENT_RETURN")
        return self


class ValidationCensusRowV1(MultiMarketModel):
    run: ArtifactRefV1
    decision_lock: ArtifactRefV1 | None
    invalidation: ArtifactRefV1 | None
    settlement: ArtifactRefV1 | None
    status: Literal["PREPARING", "LOCKED", "SETTLED", "INVALIDATED_PRE_KICKOFF", "UNAVAILABLE", "STALE_SETTLEMENT"]
    reason: str | None


class ProspectiveValidationReportV1(MarketArtifact):
    schema_version: Literal["PROSPECTIVE_VALIDATION_REPORT_V1"] = "PROSPECTIVE_VALIDATION_REPORT_V1"
    epoch: ArtifactRefV1
    as_of_at_utc: UtcDateTime
    created_at_utc: UtcDateTime
    clock_basis: ClockBasis
    receipt_watermark: int = Field(ge=0, strict=True)
    implementation_hash: Hash
    census: tuple[ValidationCensusRowV1, ...] = Field(max_length=256)
    census_hash: Hash
    performance_evidence_status: Literal["INSUFFICIENT_PROSPECTIVE_SAMPLE", "DESCRIPTIVE_REAL_SAMPLE_AVAILABLE"]
    real_run_count: int = Field(ge=0, strict=True)
    synthetic_run_count: int = Field(ge=0, strict=True)
    probability_quality: tuple[dict, ...]
    layer_comparisons: tuple[dict, ...]
    correction_performance: dict
    decision_value: dict
    portfolio_calibration: dict
    coverage: dict
    interpretation: Literal["DESCRIPTIVE_ONLY_NOT_MODEL_VALIDITY_ROI_ALPHA_OR_TUNING_PERMISSION"] = "DESCRIPTIVE_ONLY_NOT_MODEL_VALIDITY_ROI_ALPHA_OR_TUNING_PERMISSION"

    @field_validator("probability_quality", "layer_comparisons", "correction_performance", "decision_value", "portfolio_calibration", "coverage", mode="before")
    @classmethod
    def canonical_metric_payload(cls, value):
        # Metric payloads are closed by source replay. Keep decimal strings and
        # JSON arrays identical before/after persistence, without binary floats.
        from football_system.domain.archive import canonical_json
        return json.loads(canonical_json(reject_float(value)))

    @model_validator(mode="after")
    @fixed_decimal(128)
    def sample(self):
        if self.as_of_at_utc > self.created_at_utc or (self.clock_basis == "SYNTHETIC_TEST_CLOCK" and self.real_run_count):
            raise ValueError("REPORT_CUTOFF_OR_REAL_ELIGIBILITY_INVALID")
        if not self.real_run_count and self.performance_evidence_status != "INSUFFICIENT_PROSPECTIVE_SAMPLE":
            raise ValueError("SYNTHETIC_CANNOT_PROVE_REAL_PERFORMANCE")
        return self


class ValidationEpochCloseV1(MarketArtifact):
    schema_version: Literal["VALIDATION_EPOCH_CLOSE_V1"] = "VALIDATION_EPOCH_CLOSE_V1"
    epoch: ArtifactRefV1
    closed_at_utc: UtcDateTime
    receipt_watermark: int = Field(ge=0, strict=True)
    implementation_hash: Hash
    configuration_hash: Hash
    census_hash: Hash
    observation_policy: Literal["NO_AUTOMATIC_TUNING_NEW_VERSION_REQUIRES_NEW_FUTURE_EPOCH"] = "NO_AUTOMATIC_TUNING_NEW_VERSION_REQUIRES_NEW_FUTURE_EPOCH"


PROSPECTIVE_ARTIFACT_TYPES = {cls.model_fields["schema_version"].default: cls for cls in (
    ProspectivePolicyV1, ValidationEpochV1, ValidationEpochCloseV1,
    EvidenceSnapshotV1, ProspectiveEvidenceBindingV1, ProspectiveRunV1,
    V4CorrectionAuditV1, DecisionLockV1, PreKickoffInvalidationV1,
    ResultObservationV1, ProspectiveSettlementV1, ProspectiveValidationReportV1,
)}
