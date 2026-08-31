from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database datetime must be timezone-aware")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


IdColumn = String(160)
EnumColumn = String(64)
PriceColumn = Numeric(18, 6, asdecimal=True)
ProbabilityColumn = Numeric(18, 12, asdecimal=True)
MetricColumn = Numeric(24, 8, asdecimal=True)
RatioColumn = Numeric(24, 12, asdecimal=True)


class ProviderRecord(Base):
    __tablename__ = "providers"

    provider_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_kind: Mapped[str] = mapped_column(EnumColumn, nullable=False)


class BookmakerRecord(Base):
    __tablename__ = "bookmakers"

    bookmaker_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)


class CompetitionRecord(Base):
    __tablename__ = "competitions"

    competition_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country_code: Mapped[str] = mapped_column(String(3), nullable=False)


class TeamRecord(Base):
    __tablename__ = "teams"

    team_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    team_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)


class MatchRecord(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="ck_matches_distinct_teams"),
        Index("ix_matches_competition_kickoff", "competition_id", "kickoff_at_utc"),
    )

    internal_match_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    competition_id: Mapped[str] = mapped_column(
        ForeignKey("competitions.competition_id", ondelete="RESTRICT"), nullable=False
    )
    home_team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.team_id", ondelete="RESTRICT"), nullable=False
    )
    away_team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.team_id", ondelete="RESTRICT"), nullable=False
    )
    kickoff_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProviderMatchMappingRecord(Base):
    __tablename__ = "provider_match_mappings"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "external_namespace",
            "external_match_id",
            name="uq_provider_external_match",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_mapping_confidence"),
    )

    mapping_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    external_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    external_match_id: Mapped[str] = mapped_column(String(160), nullable=False)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    resolution_method: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    supersedes_mapping_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_match_mappings.mapping_id", ondelete="RESTRICT"), nullable=True
    )


class MarketOddsSnapshotRecord(Base):
    __tablename__ = "market_odds_snapshots"
    __table_args__ = (
        UniqueConstraint("provider_id", "source_snapshot_key", name="uq_market_source_key"),
        Index(
            "ix_market_odds_match_bookmaker_time",
            "internal_match_id",
            "bookmaker_id",
            "available_at_utc",
            "captured_at_utc",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    bookmaker_id: Mapped[str] = mapped_column(
        ForeignKey("bookmakers.bookmaker_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    captured_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ingested_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_snapshot_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(160), nullable=False)


class MarketOddsQuoteRecord(Base):
    __tablename__ = "market_odds_quotes"
    __table_args__ = (
        CheckConstraint("odds > 1", name="ck_market_odds_gt_one"),
        CheckConstraint(
            "selection_key IN ('HOME_WIN', 'DRAW', 'AWAY_WIN')",
            name="ck_market_odds_selection",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    odds: Mapped[Decimal] = mapped_column(PriceColumn, nullable=False)


class SportteryBonusSnapshotRecord(Base):
    __tablename__ = "sporttery_bonus_snapshots"
    __table_args__ = (
        UniqueConstraint("provider_id", "source_snapshot_key", name="uq_sporttery_source_key"),
        Index(
            "ix_sporttery_bonus_match_time",
            "internal_match_id",
            "available_at_utc",
            "captured_at_utc",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    sporttery_match_no: Mapped[str] = mapped_column(String(80), nullable=False)
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    sale_status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    captured_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ingested_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_snapshot_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(160), nullable=False)


class SportteryBonusQuoteRecord(Base):
    __tablename__ = "sporttery_bonus_quotes"
    __table_args__ = (
        CheckConstraint("fixed_bonus > 1", name="ck_sporttery_bonus_gt_one"),
        CheckConstraint(
            "selection_key IN ('HOME_WIN', 'DRAW', 'AWAY_WIN')",
            name="ck_sporttery_bonus_selection",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    fixed_bonus: Mapped[Decimal] = mapped_column(PriceColumn, nullable=False)


class ManualQuantInputRecord(Base):
    __tablename__ = "manual_quant_inputs"
    __table_args__ = (
        UniqueConstraint(
            "internal_match_id",
            "market_key",
            "available_at_utc",
            "payload_hash",
            name="uq_manual_quant_version",
        ),
    )

    input_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(160), nullable=False)


class ManualQuantInputOutcomeRecord(Base):
    __tablename__ = "manual_quant_input_outcomes"
    __table_args__ = (
        CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_manual_quant_probability",
        ),
        CheckConstraint(
            "selection_key IN ('HOME_WIN', 'DRAW', 'AWAY_WIN')",
            name="ck_manual_quant_selection",
        ),
    )

    input_id: Mapped[str] = mapped_column(
        ForeignKey("manual_quant_inputs.input_id", ondelete="RESTRICT"), primary_key=True
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)


class AnalysisRunRecord(Base):
    __tablename__ = "analysis_runs"

    analysis_run_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    run_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    as_of_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at_utc: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String(80), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    input_manifest_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_manifest_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    replay_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=True
    )


class AnalysisRunMatchRecord(Base):
    __tablename__ = "analysis_run_matches"

    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), primary_key=True
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), primary_key=True
    )
    market_odds_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    sporttery_bonus_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    manual_quant_input_id: Mapped[str] = mapped_column(
        ForeignKey("manual_quant_inputs.input_id", ondelete="RESTRICT"), nullable=False
    )
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(String(160), nullable=False)


class MarketProbabilityRecord(Base):
    __tablename__ = "market_probabilities"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "internal_match_id", "market_key", name="uq_market_probability"
        ),
    )

    market_probability_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    devig_method: Mapped[str] = mapped_column(String(80), nullable=False)
    devig_version: Mapped[str] = mapped_column(String(40), nullable=False)
    overround: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MarketProbabilityOutcomeRecord(Base):
    __tablename__ = "market_probability_outcomes"
    __table_args__ = (
        CheckConstraint("probability >= 0 AND probability <= 1", name="ck_market_probability"),
    )

    market_probability_id: Mapped[str] = mapped_column(
        ForeignKey("market_probabilities.market_probability_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)


class MarketProbabilityInputRecord(Base):
    __tablename__ = "market_probability_inputs"

    market_probability_id: Mapped[str] = mapped_column(
        ForeignKey("market_probabilities.market_probability_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    market_odds_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )


class QuantPredictionRecord(Base):
    __tablename__ = "quant_predictions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "internal_match_id", "market_key", name="uq_quant_prediction"
        ),
    )

    quant_prediction_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    manual_input_id: Mapped[str] = mapped_column(
        ForeignKey("manual_quant_inputs.input_id", ondelete="RESTRICT"), nullable=False
    )
    input_payload_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    method: Mapped[str] = mapped_column(String(80), nullable=False)
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    entered_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class QuantPredictionOutcomeRecord(Base):
    __tablename__ = "quant_prediction_outcomes"
    __table_args__ = (
        CheckConstraint("probability >= 0 AND probability <= 1", name="ck_quant_probability"),
    )

    quant_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("quant_predictions.quant_prediction_id", ondelete="RESTRICT"), primary_key=True
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)


class FinalPredictionRecord(Base):
    __tablename__ = "final_predictions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "internal_match_id", "market_key", name="uq_final_prediction"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_final_confidence"),
    )

    final_prediction_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    market_probability_id: Mapped[str | None] = mapped_column(
        ForeignKey("market_probabilities.market_probability_id", ondelete="RESTRICT"), nullable=True
    )
    quant_prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("quant_predictions.quant_prediction_id", ondelete="RESTRICT"), nullable=True
    )
    llm_assessment_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    fusion_policy: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    fusion_version: Mapped[str] = mapped_column(String(40), nullable=False)
    fusion_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class FinalPredictionOutcomeRecord(Base):
    __tablename__ = "final_prediction_outcomes"
    __table_args__ = (
        CheckConstraint("probability >= 0 AND probability <= 1", name="ck_final_probability"),
    )

    final_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("final_predictions.final_prediction_id", ondelete="RESTRICT"), primary_key=True
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)


class BetCandidateRecord(Base):
    __tablename__ = "bet_candidates"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "internal_match_id",
            "market_key",
            "selection_key",
            name="uq_bet_candidate",
        ),
        CheckConstraint("probability_used >= 0 AND probability_used <= 1", name="ck_bet_probability"),
        CheckConstraint("fixed_bonus > 1", name="ck_bet_fixed_bonus"),
    )

    candidate_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    final_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("final_predictions.final_prediction_id", ondelete="RESTRICT"), nullable=False
    )
    sporttery_bonus_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    selection_key: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    probability_used: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    fixed_bonus: Mapped[Decimal] = mapped_column(PriceColumn, nullable=False)
    break_even_probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    ev: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    eligibility_status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    rejection_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class TicketCandidateRecord(Base):
    __tablename__ = "ticket_candidates"

    ticket_candidate_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    pass_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    atomic_bet_count: Mapped[int] = mapped_column(Integer, nullable=False)
    base_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    joint_probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    gross_payout_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_gross_payout_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    expected_profit_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    expected_roi: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    payout_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)


class TicketCandidateLegRecord(Base):
    __tablename__ = "ticket_candidate_legs"
    __table_args__ = (
        UniqueConstraint("ticket_candidate_id", "internal_match_id", name="uq_ticket_candidate_match"),
    )

    ticket_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_candidates.ticket_candidate_id", ondelete="RESTRICT"), primary_key=True
    )
    leg_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("bet_candidates.candidate_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )


class PortfolioRecord(Base):
    __tablename__ = "portfolios"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "budget_fen", name="uq_portfolio_run_budget"),
        CheckConstraint("budget_fen >= 0", name="ck_portfolio_budget"),
        CheckConstraint("total_stake_fen >= 0", name="ck_portfolio_stake"),
        CheckConstraint("unused_budget_fen >= 0", name="ck_portfolio_unused"),
        CheckConstraint("total_stake_fen <= budget_fen", name="ck_portfolio_within_budget"),
    )

    portfolio_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    budget_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    total_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    unused_budget_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    no_bet_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_config_json: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioCashPositionRecord(Base):
    __tablename__ = "portfolio_cash_positions"
    __table_args__ = (
        CheckConstraint("amount_fen >= 0", name="ck_cash_position_amount"),
    )

    cash_position_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_profit_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)


class TicketRecord(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "ticket_no", name="uq_portfolio_ticket_no"),
        CheckConstraint("ticket_no > 0", name="ck_ticket_no"),
        CheckConstraint("multiplier >= 1", name="ck_ticket_multiplier"),
        CheckConstraint("atomic_bet_count >= 1", name="ck_ticket_atomic_count"),
        CheckConstraint("base_stake_fen > 0", name="ck_ticket_base_stake"),
        CheckConstraint(
            "stake_fen = atomic_bet_count * base_stake_fen * multiplier",
            name="ck_ticket_derived_stake",
        ),
    )

    ticket_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"), nullable=False
    )
    ticket_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_candidates.ticket_candidate_id", ondelete="RESTRICT"), nullable=False
    )
    ticket_no: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    role: Mapped[str | None] = mapped_column(EnumColumn, nullable=True)
    multiplier: Mapped[int] = mapped_column(Integer, nullable=False)
    atomic_bet_count: Mapped[int] = mapped_column(Integer, nullable=False)
    base_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    potential_gross_payout_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_gross_payout_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    expected_profit_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    expected_roi: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    probability_any_payout: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    payout_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)


class TicketLegRecord(Base):
    __tablename__ = "ticket_legs"
    __table_args__ = (
        UniqueConstraint("ticket_id", "internal_match_id", name="uq_ticket_match"),
    )

    ticket_id: Mapped[str] = mapped_column(
        ForeignKey("tickets.ticket_id", ondelete="RESTRICT"), primary_key=True
    )
    leg_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("bet_candidates.candidate_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )


class PortfolioRiskReportRecord(Base):
    __tablename__ = "portfolio_risk_reports"
    __table_args__ = (
        CheckConstraint("budget_fen >= 0", name="ck_risk_budget"),
        CheckConstraint("total_stake_fen >= 0", name="ck_risk_stake"),
        CheckConstraint("cash_fen >= 0", name="ck_risk_cash"),
        CheckConstraint(
            "total_stake_fen + cash_fen = budget_fen",
            name="ck_risk_capital_balance",
        ),
        CheckConstraint(
            "total_stake_at_risk_fen >= 0",
            name="ck_risk_stake_at_risk",
        ),
        CheckConstraint(
            "max_single_ticket_exposure_fen >= 0",
            name="ck_risk_max_ticket",
        ),
        CheckConstraint(
            "max_match_exposure_fen >= 0",
            name="ck_risk_max_match",
        ),
    )

    risk_report_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    budget_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    total_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_ratio: Mapped[Decimal | None] = mapped_column(ProbabilityColumn, nullable=True)
    expected_profit_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    total_stake_at_risk_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    max_single_ticket_exposure_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    max_match_exposure_fen: Mapped[int] = mapped_column(Integer, nullable=False)


class PortfolioMatchExposureRecord(Base):
    __tablename__ = "portfolio_match_exposures"
    __table_args__ = (
        CheckConstraint("exposed_stake_fen >= 0", name="ck_match_exposure_stake"),
        CheckConstraint("ticket_count > 0", name="ck_match_exposure_tickets"),
        UniqueConstraint(
            "risk_report_id",
            "internal_match_id",
            name="uq_risk_match_exposure",
        ),
    )

    exposure_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    risk_report_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_risk_reports.risk_report_id", ondelete="RESTRICT"),
        nullable=False,
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    exposed_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_ratio: Mapped[Decimal | None] = mapped_column(ProbabilityColumn, nullable=True)
    deployed_ratio: Mapped[Decimal | None] = mapped_column(ProbabilityColumn, nullable=True)
    ticket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_ids_json: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioSelectionExposureRecord(Base):
    __tablename__ = "portfolio_selection_exposures"
    __table_args__ = (
        CheckConstraint(
            "exposed_stake_fen >= 0", name="ck_selection_exposure_stake"
        ),
        CheckConstraint("ticket_count > 0", name="ck_selection_exposure_tickets"),
        UniqueConstraint(
            "risk_report_id",
            "internal_match_id",
            "market_key",
            "selection_key",
            name="uq_risk_selection_exposure",
        ),
    )

    exposure_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    risk_report_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_risk_reports.risk_report_id", ondelete="RESTRICT"),
        nullable=False,
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    selection_key: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    exposed_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_ratio: Mapped[Decimal | None] = mapped_column(ProbabilityColumn, nullable=True)
    deployed_ratio: Mapped[Decimal | None] = mapped_column(ProbabilityColumn, nullable=True)
    ticket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_ids_json: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioStressResultRecord(Base):
    __tablename__ = "portfolio_stress_results"
    __table_args__ = (
        CheckConstraint(
            "scenario_exposed_stake_fen >= 0",
            name="ck_stress_exposed_stake",
        ),
        CheckConstraint(
            "minimum_ending_capital_fen >= 0",
            name="ck_stress_min_capital",
        ),
        CheckConstraint(
            "maximum_ending_capital_fen >= minimum_ending_capital_fen",
            name="ck_stress_capital_bounds",
        ),
        UniqueConstraint(
            "risk_report_id", "scenario_key", name="uq_risk_stress_scenario"
        ),
    )

    scenario_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    risk_report_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_risk_reports.risk_report_id", ondelete="RESTRICT"),
        nullable=False,
    )
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"), nullable=False
    )
    scenario_key: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    outcomes_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scenario_exposed_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_exposure_ratio: Mapped[Decimal | None] = mapped_column(
        ProbabilityColumn, nullable=True
    )
    gross_payout_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ending_capital_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profit_loss_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capital_recovery_ratio: Mapped[Decimal | None] = mapped_column(
        RatioColumn, nullable=True
    )
    minimum_ending_capital_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_ending_capital_fen: Mapped[int] = mapped_column(Integer, nullable=False)


class PortfolioStressTicketResultRecord(Base):
    __tablename__ = "portfolio_stress_ticket_results"

    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_stress_results.scenario_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    ticket_id: Mapped[str] = mapped_column(
        ForeignKey("tickets.ticket_id", ondelete="RESTRICT"), primary_key=True
    )
    result_state: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    gross_payout_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AnalysisPacketRecord(Base):
    __tablename__ = "analysis_packets"
    __table_args__ = (
        CheckConstraint("length(packet_hash) = 64", name="ck_packet_hash_length"),
        UniqueConstraint(
            "parent_analysis_run_id",
            "schema_version",
            name="uq_analysis_packet_run_schema",
        ),
        UniqueConstraint(
            "packet_id",
            "parent_analysis_run_id",
            "packet_hash",
            name="uq_analysis_packet_binding",
        ),
    )

    packet_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    parent_analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    packet_json: Mapped[str] = mapped_column(Text, nullable=False)
    packet_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LLMReviewArtifactRecord(Base):
    __tablename__ = "llm_review_artifacts"
    __table_args__ = (
        CheckConstraint(
            "length(packet_hash) = 64", name="ck_review_packet_hash_length"
        ),
        CheckConstraint(
            "length(raw_review_hash) = 64", name="ck_review_raw_hash_length"
        ),
        CheckConstraint(
            "length(normalized_review_hash) = 64",
            name="ck_review_normalized_hash_length",
        ),
        ForeignKeyConstraint(
            ["packet_id", "parent_analysis_run_id", "packet_hash"],
            [
                "analysis_packets.packet_id",
                "analysis_packets.parent_analysis_run_id",
                "analysis_packets.packet_hash",
            ],
            ondelete="RESTRICT",
            name="fk_review_packet_binding",
        ),
        UniqueConstraint(
            "packet_id",
            "normalized_review_hash",
            "validator_version",
            name="uq_llm_review_normalized",
        ),
    )

    review_artifact_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    parent_analysis_run_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    packet_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    packet_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    imported_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    raw_review_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_review_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)


class FusionRunRecord(Base):
    __tablename__ = "fusion_runs"
    __table_args__ = (
        CheckConstraint(
            "length(config_hash) = 64", name="ck_fusion_run_config_hash_length"
        ),
        UniqueConstraint(
            "parent_analysis_run_id",
            "llm_review_artifact_id",
            "fusion_policy",
            "fusion_version",
            "config_hash",
            name="uq_fusion_run_idempotency",
        ),
    )

    fusion_run_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    parent_analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    llm_review_artifact_id: Mapped[str] = mapped_column(
        ForeignKey(
            "llm_review_artifacts.review_artifact_id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    fusion_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    fusion_version: Mapped[str] = mapped_column(String(40), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class FusionRunResultRecord(Base):
    __tablename__ = "fusion_run_results"
    __table_args__ = (
        CheckConstraint(
            "confidence_factor >= 0 AND confidence_factor <= 1",
            name="ck_fusion_result_confidence_factor",
        ),
        CheckConstraint(
            "data_quality_factor >= 0 AND data_quality_factor <= 1",
            name="ck_fusion_result_data_quality_factor",
        ),
        CheckConstraint(
            "(p_llm_json IS NULL AND raw_probability_delta_json IS NULL) OR "
            "(p_llm_json IS NOT NULL AND raw_probability_delta_json IS NOT NULL)",
            name="ck_fusion_result_llm_delta_pair",
        ),
        CheckConstraint(
            "length(result_hash) = 64", name="ck_fusion_result_hash_length"
        ),
        UniqueConstraint(
            "fusion_run_id",
            "internal_match_id",
            name="uq_fusion_result_match",
        ),
        UniqueConstraint(
            "fusion_run_id",
            "base_prediction_id",
            name="uq_fusion_result_base_prediction",
        ),
    )

    fusion_result_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    fusion_run_id: Mapped[str] = mapped_column(
        ForeignKey("fusion_runs.fusion_run_id", ondelete="RESTRICT"), nullable=False
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    base_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("final_predictions.final_prediction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    p_base_json: Mapped[str] = mapped_column(Text, nullable=False)
    p_llm_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_probability_delta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_probability_delta_json: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_factor: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    data_quality_factor: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    p_final_json: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PortfolioRevisionRecord(Base):
    __tablename__ = "portfolio_revisions"
    __table_args__ = (
        CheckConstraint(
            "length(config_hash) = 64",
            name="ck_portfolio_revision_config_hash_length",
        ),
        CheckConstraint(
            "length(revision_hash) = 64",
            name="ck_portfolio_revision_hash_length",
        ),
        UniqueConstraint(
            "fusion_run_id",
            "revision_policy",
            "revision_version",
            "config_hash",
            name="uq_portfolio_revision_idempotency",
        ),
    )

    portfolio_revision_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    parent_analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    fusion_run_id: Mapped[str] = mapped_column(
        ForeignKey("fusion_runs.fusion_run_id", ondelete="RESTRICT"), nullable=False
    )
    revision_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    revision_version: Mapped[str] = mapped_column(String(40), nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
