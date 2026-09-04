from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database datetime must be timezone-aware")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
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


class ProviderTeamAliasRecord(Base):
    __tablename__ = "provider_team_aliases"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "internal_team_id",
            name="uq_provider_team_alias_exact_target",
        ),
        Index(
            "ix_provider_team_alias_lookup_cutoff",
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "available_at_utc",
        ),
        Index("ix_provider_team_alias_fixture_ingestion", "fixture_ingestion_id"),
    )

    alias_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    internal_team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.team_id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    provider_team_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    provider_team_name: Mapped[str] = mapped_column(IdColumn, nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    team_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fixture_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)


class ProviderCompetitionMappingRecord(Base):
    __tablename__ = "provider_competition_mappings"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "internal_competition_id",
            name="uq_provider_competition_mapping_exact_target",
        ),
        Index(
            "ix_provider_competition_mapping_lookup_cutoff",
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "available_at_utc",
        ),
        Index(
            "ix_provider_competition_mapping_fixture_ingestion",
            "fixture_ingestion_id",
        ),
    )

    mapping_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    internal_competition_id: Mapped[str] = mapped_column(
        ForeignKey("competitions.competition_id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    provider_competition_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    provider_competition_name: Mapped[str] = mapped_column(IdColumn, nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    season: Mapped[str] = mapped_column(IdColumn, nullable=False)
    competition_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fixture_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)


class MatchRecord(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint(
            "home_team_id <> away_team_id", name="ck_matches_distinct_teams"
        ),
        Index("ix_matches_competition_kickoff", "competition_id", "kickoff_at_utc"),
        Index("ix_matches_fixture_ingestion", "fixture_ingestion_id"),
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
    fixture_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)


class CanonicalMatchIdentityRecord(Base):
    __tablename__ = "canonical_match_identities"
    __table_args__ = (
        Index(
            "ix_canonical_match_identity_season_type_cutoff",
            "season",
            "competition_type",
            "available_at_utc",
        ),
        Index(
            "ix_canonical_match_identity_fixture_ingestion",
            "fixture_ingestion_id",
        ),
    )

    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    season: Mapped[str] = mapped_column(IdColumn, nullable=False)
    competition_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fixture_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)


class ProviderMatchMappingRecord(Base):
    __tablename__ = "provider_match_mappings"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "external_namespace",
            "external_match_id",
            name="uq_provider_external_match",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_mapping_confidence"
        ),
        Index("ix_provider_match_mapping_fixture_ingestion", "fixture_ingestion_id"),
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
    fixture_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    supersedes_mapping_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_match_mappings.mapping_id", ondelete="RESTRICT"),
        nullable=True,
    )


class FixtureIngestionCaptureRecord(Base):
    __tablename__ = "fixture_ingestion_captures"
    __table_args__ = (
        UniqueConstraint(
            "raw_artifact_id",
            name="uq_fixture_ingestion_raw_artifact",
        ),
        CheckConstraint(
            "kickoff_from_utc <= kickoff_to_utc",
            name="ck_fixture_ingestion_kickoff_window",
        ),
        CheckConstraint(
            "length(trim(endpoint)) > 0 AND length(endpoint) <= 2048",
            name="ck_fixture_ingestion_endpoint",
        ),
        CheckConstraint(
            "json_valid(request_parameters_json) = 1 AND "
            "json_type(request_parameters_json) = 'object'",
            name="ck_fixture_ingestion_request_parameters_json",
        ),
        CheckConstraint(
            "requested_at_utc <= received_at_utc AND "
            "available_at_utc <= received_at_utc AND "
            "received_at_utc <= ingested_at_utc",
            name="ck_fixture_ingestion_request_timeline",
        ),
        CheckConstraint(
            "http_status >= 200 AND http_status <= 299",
            name="ck_fixture_ingestion_http_status",
        ),
        CheckConstraint(
            "provider_request_id IS NULL OR length(provider_request_id) > 0",
            name="ck_fixture_ingestion_provider_request_id",
        ),
        CheckConstraint(
            "duration_ms >= 0",
            name="ck_fixture_ingestion_duration",
        ),
        CheckConstraint(
            "outcome = 'SUCCESS' AND failure_code IS NULL",
            name="ck_fixture_ingestion_success",
        ),
        CheckConstraint(
            "length(trim(provider_competition_id)) > 0 AND "
            "length(trim(provider_season_id)) > 0 AND "
            "length(trim(season)) > 0 AND "
            "length(trim(competition_type)) > 0 AND "
            "length(language) >= 2 AND length(language) <= 35 AND "
            "team_type IN ('CLUB', 'NATIONAL', 'WOMEN', 'YOUTH', 'RESERVE')",
            name="ck_fixture_ingestion_scope",
        ),
        CheckConstraint(
            "length(raw_artifact_id) = 64 AND raw_artifact_id NOT GLOB '*[^0-9a-f]*'",
            name="ck_fixture_ingestion_artifact_id",
        ),
        CheckConstraint(
            "length(raw_payload_sha256) = 64 AND "
            "raw_payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_fixture_ingestion_payload_hash",
        ),
        CheckConstraint(
            "observation_count >= 0",
            name="ck_fixture_ingestion_observation_count",
        ),
        Index(
            "ix_fixture_ingestion_provider_available",
            "provider_id",
            "available_at_utc",
        ),
        Index(
            "ix_fixture_ingestion_scope_available",
            "provider_id",
            "provider_competition_id",
            "provider_season_id",
            "available_at_utc",
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    kickoff_from_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    kickoff_to_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    provider_competition_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    provider_season_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    season: Mapped[str] = mapped_column(IdColumn, nullable=False)
    competition_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    team_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    request_parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    received_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ingested_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(EnumColumn, nullable=True)
    raw_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)


class FixtureObservationRecord(Base):
    __tablename__ = "fixture_observations"
    __table_args__ = (
        UniqueConstraint(
            "ingestion_id",
            "internal_match_id",
            name="uq_fixture_observation_ingestion_match",
        ),
        UniqueConstraint(
            "ingestion_id",
            "provider_mapping_id",
            name="uq_fixture_observation_ingestion_mapping",
        ),
        CheckConstraint(
            "status IN ('SCHEDULED', 'FINISHED', 'POSTPONED', 'CANCELLED')",
            name="ck_fixture_observation_status",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_fixture_observation_payload_hash",
        ),
        Index(
            "ix_fixture_observation_match_available",
            "internal_match_id",
            "available_at_utc",
        ),
        Index(
            "ix_fixture_observation_mapping_available",
            "provider_mapping_id",
            "available_at_utc",
        ),
    )

    observation_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("fixture_ingestion_captures.ingestion_id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_mapping_id: Mapped[str] = mapped_column(
        ForeignKey("provider_match_mappings.mapping_id", ondelete="RESTRICT"),
        nullable=False,
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    kickoff_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class MarketOddsSnapshotRecord(Base):
    __tablename__ = "market_odds_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "source_snapshot_key", name="uq_market_source_key"
        ),
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
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    odds: Mapped[Decimal] = mapped_column(PriceColumn, nullable=False)


class SportteryBonusSnapshotRecord(Base):
    __tablename__ = "sporttery_bonus_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "source_snapshot_key", name="uq_sporttery_source_key"
        ),
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
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    fixed_bonus: Mapped[Decimal] = mapped_column(PriceColumn, nullable=False)


class LiveSourceIngestionRecord(Base):
    __tablename__ = "live_source_ingestions"
    __table_args__ = (
        CheckConstraint(
            "schema_version = 'LIVE_SOURCE_INGESTION_V1'",
            name="ck_live_source_ingestion_schema",
        ),
        CheckConstraint(
            "source_kind IN ('MARKET_ODDS', 'SPORTTERRY')",
            name="ck_live_source_ingestion_kind",
        ),
        CheckConstraint(
            "data_mode = 'LIVE_STRICT'",
            name="ck_live_source_ingestion_data_mode",
        ),
        CheckConstraint(
            "status IN ('COMPLETED', 'COMPLETED_WITH_ISSUES')",
            name="ck_live_source_ingestion_status",
        ),
        CheckConstraint(
            "identity_cutoff_at_utc <= source_ingested_at_utc AND "
            "source_ingested_at_utc <= persisted_at_utc",
            name="ck_live_source_ingestion_timeline",
        ),
        CheckConstraint(
            "json_valid(requested_match_ids_json) = 1 AND "
            "json_type(requested_match_ids_json) = 'array' AND "
            "json_valid(capture_json) = 1 AND json_type(capture_json) = 'object'",
            name="ck_live_source_ingestion_json",
        ),
        CheckConstraint(
            "length(capture_hash) = 64 AND capture_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_live_source_ingestion_hash",
        ),
        CheckConstraint(
            "artifact_count > 0 AND snapshot_count >= 0 AND mapping_count >= 0 "
            "AND issue_count >= 0 AND consensus_count >= 0",
            name="ck_live_source_ingestion_counts",
        ),
        UniqueConstraint("capture_hash", name="uq_live_source_ingestion_hash"),
        Index(
            "ix_live_source_ingestion_kind_persisted",
            "source_kind",
            "persisted_at_utc",
        ),
        Index(
            "ix_live_source_ingestion_provider_persisted",
            "provider_id",
            "persisted_at_utc",
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    data_mode: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    identity_cutoff_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    source_ingested_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    persisted_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    requested_match_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_count: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False)
    consensus_count: Mapped[int] = mapped_column(Integer, nullable=False)
    capture_json: Mapped[str] = mapped_column(Text, nullable=False)
    capture_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LiveSourceArtifactRecord(Base):
    __tablename__ = "live_source_ingestion_artifacts"
    __table_args__ = (
        CheckConstraint("artifact_no >= 0", name="ck_live_source_artifact_no"),
        CheckConstraint(
            "role IN ('RAW_RESPONSE', 'MANUAL_DOCUMENT', 'SOURCE_ARTIFACT')",
            name="ck_live_source_artifact_role",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_live_source_artifact_hash",
        ),
        CheckConstraint(
            "captured_at_utc <= available_at_utc",
            name="ck_live_source_artifact_timeline",
        ),
        UniqueConstraint(
            "ingestion_id", "artifact_no", name="uq_live_source_artifact_no"
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("live_source_ingestions.ingestion_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    artifact_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    artifact_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    captured_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LiveSourceMappingRecord(Base):
    __tablename__ = "live_source_ingestion_mappings"
    __table_args__ = (
        CheckConstraint("mapping_no >= 0", name="ck_live_source_mapping_no"),
        CheckConstraint(
            "mapping_role IN ('SOURCE', 'CONSENSUS')",
            name="ck_live_source_mapping_role",
        ),
        UniqueConstraint(
            "ingestion_id",
            "mapping_role",
            "mapping_no",
            name="uq_live_source_mapping_no",
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("live_source_ingestions.ingestion_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    mapping_role: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    mapping_id: Mapped[str] = mapped_column(
        ForeignKey("provider_match_mappings.mapping_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    mapping_no: Mapped[int] = mapped_column(Integer, nullable=False)


class LiveSourceMarketSnapshotRecord(Base):
    __tablename__ = "live_source_ingestion_market_snapshots"
    __table_args__ = (
        CheckConstraint("snapshot_no >= 0", name="ck_live_source_market_no"),
        CheckConstraint(
            "snapshot_role IN ('SOURCE', 'CONSENSUS')",
            name="ck_live_source_market_role",
        ),
        UniqueConstraint(
            "ingestion_id", "snapshot_id", name="uq_live_source_market_membership"
        ),
        UniqueConstraint(
            "ingestion_id",
            "snapshot_role",
            "snapshot_no",
            name="uq_live_source_market_no",
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("live_source_ingestions.ingestion_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_role: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_no: Mapped[int] = mapped_column(Integer, nullable=False)


class LiveMarketConsensusLineageRecord(Base):
    __tablename__ = "live_market_consensus_lineages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_id", "consensus_snapshot_id"],
            [
                "live_source_ingestion_market_snapshots.ingestion_id",
                "live_source_ingestion_market_snapshots.snapshot_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "policy = 'MARKET_CONSENSUS_MEDIAN_V1'",
            name="ck_live_market_consensus_policy",
        ),
        CheckConstraint("constituent_count > 0", name="ck_live_market_consensus_count"),
    )

    ingestion_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    consensus_snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    policy: Mapped[str] = mapped_column(String(80), nullable=False)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_key: Mapped[str] = mapped_column(IdColumn, nullable=False)
    constituent_count: Mapped[int] = mapped_column(Integer, nullable=False)


class LiveMarketConsensusConstituentRecord(Base):
    __tablename__ = "live_market_consensus_constituents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_id", "consensus_snapshot_id"],
            [
                "live_market_consensus_lineages.ingestion_id",
                "live_market_consensus_lineages.consensus_snapshot_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ingestion_id", "source_snapshot_id"],
            [
                "live_source_ingestion_market_snapshots.ingestion_id",
                "live_source_ingestion_market_snapshots.snapshot_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("constituent_no >= 0", name="ck_live_market_constituent_no"),
        CheckConstraint(
            "length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_live_market_constituent_hash",
        ),
        UniqueConstraint(
            "ingestion_id",
            "consensus_snapshot_id",
            "constituent_no",
            name="uq_live_market_constituent_no",
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    consensus_snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    source_snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    constituent_no: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    bookmaker_id: Mapped[str] = mapped_column(
        ForeignKey("bookmakers.bookmaker_id", ondelete="RESTRICT"), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LiveSourceSportterySnapshotRecord(Base):
    __tablename__ = "live_source_ingestion_sporttery_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_id", "manual_document_artifact_id"],
            [
                "live_source_ingestion_artifacts.ingestion_id",
                "live_source_ingestion_artifacts.artifact_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ingestion_id", "source_artifact_id"],
            [
                "live_source_ingestion_artifacts.ingestion_id",
                "live_source_ingestion_artifacts.artifact_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("snapshot_no >= 0", name="ck_live_source_sporttery_no"),
        CheckConstraint(
            "json_valid(provenance_json) = 1 AND json_type(provenance_json) = 'object'",
            name="ck_live_source_sporttery_provenance_json",
        ),
        CheckConstraint(
            "length(provenance_hash) = 64 AND provenance_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_live_source_sporttery_provenance_hash",
        ),
        UniqueConstraint(
            "ingestion_id", "snapshot_no", name="uq_live_source_sporttery_no"
        ),
    )

    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("live_source_ingestions.ingestion_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    manual_document_artifact_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    source_artifact_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    provenance_json: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LiveSourceIssueRecord(Base):
    __tablename__ = "live_source_ingestion_issues"
    __table_args__ = (
        CheckConstraint("issue_no >= 0", name="ck_live_source_issue_no"),
        CheckConstraint(
            "source_kind IN ('MARKET_ODDS', 'SPORTTERRY')",
            name="ck_live_source_issue_kind",
        ),
        CheckConstraint(
            "(external_namespace IS NULL AND external_match_id IS NULL) OR "
            "(external_namespace IS NOT NULL AND external_match_id IS NOT NULL)",
            name="ck_live_source_issue_external_identity",
        ),
        CheckConstraint(
            "json_valid(candidates_json) = 1 AND json_type(candidates_json) = 'array'",
            name="ck_live_source_issue_candidates_json",
        ),
        UniqueConstraint("ingestion_id", "issue_no", name="uq_live_source_issue_no"),
    )

    ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("live_source_ingestions.ingestion_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    issue_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    issue_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    reason: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    external_namespace: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_match_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    requested_match_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    candidates_json: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[str] = mapped_column(String(240), nullable=False)
    provider_identity_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class LiveIdentityReviewRecord(Base):
    __tablename__ = "live_identity_reviews"
    __table_args__ = (
        CheckConstraint(
            "schema_version = 'LIVE_IDENTITY_REVIEW_V1'",
            name="ck_live_identity_review_schema",
        ),
        CheckConstraint(
            "reviewed_at_utc <= imported_at_utc",
            name="ck_live_identity_review_timeline",
        ),
        CheckConstraint(
            "mapping_count > 0", name="ck_live_identity_review_mapping_count"
        ),
        CheckConstraint(
            "json_valid(review_json) = 1 AND json_type(review_json) = 'object'",
            name="ck_live_identity_review_json",
        ),
        CheckConstraint(
            "length(review_hash) = 64 AND review_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_live_identity_review_hash",
        ),
        UniqueConstraint("review_hash", name="uq_live_identity_review_hash"),
        UniqueConstraint(
            "review_id",
            "source_ingestion_id",
            name="uq_live_identity_review_source",
        ),
    )

    review_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ingestion_id: Mapped[str] = mapped_column(
        ForeignKey("live_source_ingestions.ingestion_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_by: Mapped[str] = mapped_column(IdColumn, nullable=False)
    reviewed_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    imported_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mapping_count: Mapped[int] = mapped_column(Integer, nullable=False)
    review_json: Mapped[str] = mapped_column(Text, nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LiveIdentityReviewMappingRecord(Base):
    __tablename__ = "live_identity_review_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["review_id", "source_ingestion_id"],
            [
                "live_identity_reviews.review_id",
                "live_identity_reviews.source_ingestion_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_ingestion_id", "source_issue_id"],
            [
                "live_source_ingestion_issues.ingestion_id",
                "live_source_ingestion_issues.issue_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("mapping_no >= 0", name="ck_live_identity_review_mapping_no"),
        UniqueConstraint(
            "review_id",
            "provider_id",
            "external_namespace",
            "external_match_id",
            name="uq_live_identity_review_external",
        ),
    )

    review_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    mapping_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_ingestion_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    source_issue_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    external_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    external_match_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    provider_mapping_id: Mapped[str] = mapped_column(
        ForeignKey("provider_match_mappings.mapping_id", ondelete="RESTRICT"),
        nullable=False,
    )


class LiveAnalysisPreparationRecord(Base):
    __tablename__ = "live_analysis_preparations"
    __table_args__ = (
        CheckConstraint(
            "schema_version = 'LIVE_ANALYSIS_PREPARATION_V1'",
            name="ck_live_analysis_preparation_schema",
        ),
        CheckConstraint(
            "status IN ('ANALYSIS_INPUT_READY', 'NO_ANALYSIS_INSUFFICIENT_DATA')",
            name="ck_live_analysis_preparation_status",
        ),
        CheckConstraint(
            "decision_as_of_at_utc <= created_at_utc AND "
            "kickoff_from_utc <= kickoff_to_utc",
            name="ck_live_analysis_preparation_timeline",
        ),
        CheckConstraint(
            "policy_version = 'LIVE_ANALYSIS_INPUT_POLICY_V1' AND "
            "maximum_odds_age_seconds > 0 AND minimum_bookmaker_count > 0",
            name="ck_live_analysis_preparation_policy",
        ),
        CheckConstraint(
            "json_valid(expected_match_ids_json) = 1 AND "
            "json_type(expected_match_ids_json) = 'array' AND "
            "json_valid(preparation_json) = 1 AND "
            "json_type(preparation_json) = 'object'",
            name="ck_live_analysis_preparation_json",
        ),
        CheckConstraint(
            "match_count >= ready_match_count AND ready_match_count >= 0",
            name="ck_live_analysis_preparation_counts",
        ),
        CheckConstraint(
            "length(report_hash) = 64 AND report_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_live_analysis_preparation_hash",
        ),
        UniqueConstraint("report_hash", name="uq_live_analysis_preparation_hash"),
        Index(
            "ix_live_analysis_preparation_scope_cutoff",
            "competition_id",
            "season_id",
            "decision_as_of_at_utc",
        ),
    )

    preparation_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    decision_as_of_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    kickoff_from_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    kickoff_to_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    competition_id: Mapped[str] = mapped_column(
        ForeignKey("competitions.competition_id", ondelete="RESTRICT"), nullable=False
    )
    season_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    allow_partial_inputs: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    maximum_odds_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_bookmaker_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_match_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ready_match_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    preparation_json: Mapped[str] = mapped_column(Text, nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LiveAnalysisPreparationMatchRecord(Base):
    __tablename__ = "live_analysis_preparation_matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["market_ingestion_id", "market_consensus_snapshot_id"],
            [
                "live_source_ingestion_market_snapshots.ingestion_id",
                "live_source_ingestion_market_snapshots.snapshot_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sporttery_ingestion_id", "sporttery_bonus_snapshot_id"],
            [
                "live_source_ingestion_sporttery_snapshots.ingestion_id",
                "live_source_ingestion_sporttery_snapshots.snapshot_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("match_no >= 0", name="ck_live_analysis_match_no"),
        CheckConstraint(
            "bookmaker_count >= 0 AND "
            "(odds_age_seconds IS NULL OR odds_age_seconds >= 0)",
            name="ck_live_analysis_match_metrics",
        ),
        CheckConstraint(
            "(market_ingestion_id IS NULL AND market_consensus_snapshot_id IS NULL) "
            "OR (market_ingestion_id IS NOT NULL AND "
            "market_consensus_snapshot_id IS NOT NULL)",
            name="ck_live_analysis_match_market_pair",
        ),
        CheckConstraint(
            "(sporttery_ingestion_id IS NULL AND "
            "sporttery_bonus_snapshot_id IS NULL) OR "
            "(sporttery_ingestion_id IS NOT NULL AND "
            "sporttery_bonus_snapshot_id IS NOT NULL)",
            name="ck_live_analysis_match_sporttery_pair",
        ),
        CheckConstraint(
            "json_valid(reason_codes_json) = 1 AND "
            "json_type(reason_codes_json) = 'array' AND "
            "json_valid(data_quality_json) = 1 AND "
            "json_type(data_quality_json) = 'object'",
            name="ck_live_analysis_match_json",
        ),
        UniqueConstraint(
            "preparation_id", "match_no", name="uq_live_analysis_match_no"
        ),
    )

    preparation_id: Mapped[str] = mapped_column(
        ForeignKey("live_analysis_preparations.preparation_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    internal_match_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    match_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fixture_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("fixture_observations.observation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    market_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    market_consensus_snapshot_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )
    sporttery_ingestion_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    sporttery_bonus_snapshot_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )
    bookmaker_count: Mapped[int] = mapped_column(Integer, nullable=False)
    odds_age_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    data_quality_json: Mapped[str] = mapped_column(Text, nullable=False)


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
        ForeignKey("manual_quant_inputs.input_id", ondelete="RESTRICT"),
        primary_key=True,
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
    completed_at_utc: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
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


class LiveAnalysisRunPreparationRecord(Base):
    __tablename__ = "live_analysis_run_preparations"
    __table_args__ = (
        Index(
            "ix_live_analysis_run_preparation_preparation",
            "preparation_id",
        ),
    )

    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    preparation_id: Mapped[str] = mapped_column(
        ForeignKey("live_analysis_preparations.preparation_id", ondelete="RESTRICT"),
        nullable=False,
    )


class QuantModelStateRecord(Base):
    __tablename__ = "quant_model_states"
    __table_args__ = (
        UniqueConstraint(
            "quant_model_state_id",
            "analysis_run_id",
            name="uq_quant_model_state_run_lineage",
        ),
        UniqueConstraint(
            "analysis_run_id",
            "model_name",
            "model_version",
            "state_hash",
            name="uq_quant_model_state_version",
        ),
        CheckConstraint(
            "length(config_hash) = 64 AND config_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_state_config_hash",
        ),
        CheckConstraint(
            "length(state_hash) = 64 AND state_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_state_hash",
        ),
        CheckConstraint(
            "length(state_payload_hash) = 64 AND "
            "state_payload_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_state_payload_hash",
        ),
        CheckConstraint(
            "length(training_data_hash) = 64 AND "
            "training_data_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_training_data_hash",
        ),
        CheckConstraint(
            "training_fact_count >= 0",
            name="ck_quant_model_training_fact_count",
        ),
    )

    quant_model_state_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    calibration_label: Mapped[str] = mapped_column(String(80), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    season_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    training_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    training_fact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class QuantModelTrainingFactRecord(Base):
    __tablename__ = "quant_model_training_facts"
    __table_args__ = (
        UniqueConstraint(
            "quant_model_state_id",
            "match_result_id",
            name="uq_quant_model_training_result",
        ),
        CheckConstraint(
            "fact_sequence >= 0",
            name="ck_quant_model_training_fact_sequence",
        ),
        CheckConstraint(
            "length(source_payload_hash) = 64 AND "
            "source_payload_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_training_source_hash",
        ),
        CheckConstraint(
            "length(fact_hash) = 64 AND fact_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_training_fact_hash",
        ),
    )

    quant_model_state_id: Mapped[str] = mapped_column(
        ForeignKey("quant_model_states.quant_model_state_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    fact_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_result_id: Mapped[str] = mapped_column(
        ForeignKey("match_results.match_result_id", ondelete="RESTRICT"),
        nullable=False,
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class QuantModelEvaluationRecord(Base):
    __tablename__ = "quant_model_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "quant_model_evaluation_id",
            "analysis_run_id",
            "internal_match_id",
            name="uq_quant_model_evaluation_run_match",
        ),
        UniqueConstraint(
            "analysis_run_id",
            "internal_match_id",
            "market_key",
            name="uq_quant_model_evaluation",
        ),
        ForeignKeyConstraint(
            ["quant_model_state_id", "analysis_run_id"],
            [
                "quant_model_states.quant_model_state_id",
                "quant_model_states.analysis_run_id",
            ],
            name="fk_quant_model_evaluation_state_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_quant_model_evaluation_status",
        ),
        CheckConstraint(
            "(status = 'AVAILABLE' AND unavailable_reason IS NULL) OR "
            "(status = 'UNAVAILABLE' AND unavailable_reason IS NOT NULL "
            "AND length(trim(unavailable_reason)) > 0)",
            name="ck_quant_model_evaluation_availability",
        ),
        CheckConstraint(
            "length(output_hash) = 64 AND output_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_evaluation_output_hash",
        ),
        CheckConstraint(
            "length(model_prediction_hash) = 64 AND "
            "model_prediction_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_quant_model_prediction_hash",
        ),
    )

    quant_model_evaluation_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    quant_model_state_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    handicap_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    unavailable_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    output_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AnalysisRunMatchRecord(Base):
    __tablename__ = "analysis_run_matches"
    __table_args__ = (
        UniqueConstraint(
            "quant_model_evaluation_id",
            name="uq_analysis_run_match_model_evaluation",
        ),
        ForeignKeyConstraint(
            [
                "quant_model_evaluation_id",
                "analysis_run_id",
                "internal_match_id",
            ],
            [
                "quant_model_evaluations.quant_model_evaluation_id",
                "quant_model_evaluations.analysis_run_id",
                "quant_model_evaluations.internal_match_id",
            ],
            name="fk_analysis_run_match_model_evaluation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(manual_quant_input_id IS NOT NULL AND "
            "quant_model_evaluation_id IS NULL) OR "
            "(manual_quant_input_id IS NULL AND "
            "quant_model_evaluation_id IS NOT NULL)",
            name="ck_analysis_run_match_quant_source",
        ),
    )

    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), primary_key=True
    )
    market_odds_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sporttery_bonus_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    manual_quant_input_id: Mapped[str | None] = mapped_column(
        ForeignKey("manual_quant_inputs.input_id", ondelete="RESTRICT"), nullable=True
    )
    quant_model_evaluation_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(String(160), nullable=False)


class MarketProbabilityRecord(Base):
    __tablename__ = "market_probabilities"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "internal_match_id",
            "market_key",
            name="uq_market_probability",
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
        CheckConstraint(
            "probability >= 0 AND probability <= 1", name="ck_market_probability"
        ),
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
        ForeignKey("market_odds_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )


class QuantPredictionRecord(Base):
    __tablename__ = "quant_predictions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "internal_match_id",
            "market_key",
            name="uq_quant_prediction",
        ),
        UniqueConstraint(
            "quant_model_evaluation_id",
            name="uq_quant_prediction_model_evaluation",
        ),
        ForeignKeyConstraint(
            [
                "quant_model_evaluation_id",
                "analysis_run_id",
                "internal_match_id",
            ],
            [
                "quant_model_evaluations.quant_model_evaluation_id",
                "quant_model_evaluations.analysis_run_id",
                "quant_model_evaluations.internal_match_id",
            ],
            name="fk_quant_prediction_model_evaluation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(manual_input_id IS NOT NULL AND input_payload_hash IS NOT NULL "
            "AND quant_model_evaluation_id IS NULL AND entered_at_utc IS NOT NULL "
            "AND generated_at_utc IS NULL) OR "
            "(manual_input_id IS NULL AND input_payload_hash IS NULL "
            "AND quant_model_evaluation_id IS NOT NULL AND entered_at_utc IS NULL "
            "AND generated_at_utc IS NOT NULL)",
            name="ck_quant_prediction_source",
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
    manual_input_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "manual_quant_inputs.input_id",
            name="fk_quant_predictions_manual_input_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    input_payload_hash: Mapped[str | None] = mapped_column(String(160), nullable=True)
    quant_model_evaluation_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )
    method: Mapped[str] = mapped_column(String(80), nullable=False)
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    entered_at_utc: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    generated_at_utc: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )


class QuantPredictionOutcomeRecord(Base):
    __tablename__ = "quant_prediction_outcomes"
    __table_args__ = (
        CheckConstraint(
            "probability >= 0 AND probability <= 1", name="ck_quant_probability"
        ),
    )

    quant_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("quant_predictions.quant_prediction_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    selection_key: Mapped[str] = mapped_column(EnumColumn, primary_key=True)
    probability: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)


class FinalPredictionRecord(Base):
    __tablename__ = "final_predictions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "internal_match_id",
            "market_key",
            name="uq_final_prediction",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_final_confidence"
        ),
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
        ForeignKey("market_probabilities.market_probability_id", ondelete="RESTRICT"),
        nullable=True,
    )
    quant_prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("quant_predictions.quant_prediction_id", ondelete="RESTRICT"),
        nullable=True,
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
        CheckConstraint(
            "probability >= 0 AND probability <= 1", name="ck_final_probability"
        ),
    )

    final_prediction_id: Mapped[str] = mapped_column(
        ForeignKey("final_predictions.final_prediction_id", ondelete="RESTRICT"),
        primary_key=True,
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
        CheckConstraint(
            "probability_used >= 0 AND probability_used <= 1", name="ck_bet_probability"
        ),
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
        ForeignKey("final_predictions.final_prediction_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sporttery_bonus_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("sporttery_bonus_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    market_key: Mapped[str] = mapped_column(String(120), nullable=False)
    selection_key: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    probability_used: Mapped[Decimal] = mapped_column(ProbabilityColumn, nullable=False)
    fixed_bonus: Mapped[Decimal] = mapped_column(PriceColumn, nullable=False)
    break_even_probability: Mapped[Decimal] = mapped_column(
        ProbabilityColumn, nullable=False
    )
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
    joint_probability: Mapped[Decimal] = mapped_column(
        ProbabilityColumn, nullable=False
    )
    gross_payout_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_gross_payout_fen: Mapped[Decimal] = mapped_column(
        MetricColumn, nullable=False
    )
    expected_profit_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    expected_roi: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    payout_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)


class TicketCandidateLegRecord(Base):
    __tablename__ = "ticket_candidate_legs"
    __table_args__ = (
        UniqueConstraint(
            "ticket_candidate_id", "internal_match_id", name="uq_ticket_candidate_match"
        ),
    )

    ticket_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_candidates.ticket_candidate_id", ondelete="RESTRICT"),
        primary_key=True,
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
        UniqueConstraint(
            "analysis_run_id", "budget_fen", name="uq_portfolio_run_budget"
        ),
        CheckConstraint("budget_fen >= 0", name="ck_portfolio_budget"),
        CheckConstraint("total_stake_fen >= 0", name="ck_portfolio_stake"),
        CheckConstraint("unused_budget_fen >= 0", name="ck_portfolio_unused"),
        CheckConstraint(
            "total_stake_fen <= budget_fen", name="ck_portfolio_within_budget"
        ),
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
        ForeignKey("ticket_candidates.ticket_candidate_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ticket_no: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_type: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    role: Mapped[str | None] = mapped_column(EnumColumn, nullable=True)
    multiplier: Mapped[int] = mapped_column(Integer, nullable=False)
    atomic_bet_count: Mapped[int] = mapped_column(Integer, nullable=False)
    base_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    potential_gross_payout_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_gross_payout_fen: Mapped[Decimal] = mapped_column(
        MetricColumn, nullable=False
    )
    expected_profit_fen: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    expected_roi: Mapped[Decimal] = mapped_column(MetricColumn, nullable=False)
    probability_any_payout: Mapped[Decimal] = mapped_column(
        ProbabilityColumn, nullable=False
    )
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
    budget_ratio: Mapped[Decimal | None] = mapped_column(
        ProbabilityColumn, nullable=True
    )
    deployed_ratio: Mapped[Decimal | None] = mapped_column(
        ProbabilityColumn, nullable=True
    )
    ticket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_ids_json: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioSelectionExposureRecord(Base):
    __tablename__ = "portfolio_selection_exposures"
    __table_args__ = (
        CheckConstraint("exposed_stake_fen >= 0", name="ck_selection_exposure_stake"),
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
    budget_ratio: Mapped[Decimal | None] = mapped_column(
        ProbabilityColumn, nullable=True
    )
    deployed_ratio: Mapped[Decimal | None] = mapped_column(
        ProbabilityColumn, nullable=True
    )
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
        ForeignKey("llm_review_artifacts.review_artifact_id", ondelete="RESTRICT"),
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
    confidence_factor: Mapped[Decimal] = mapped_column(
        ProbabilityColumn, nullable=False
    )
    data_quality_factor: Mapped[Decimal] = mapped_column(
        ProbabilityColumn, nullable=False
    )
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


class HistoricalArchiveImportRecord(Base):
    __tablename__ = "historical_archive_imports"
    __table_args__ = (
        UniqueConstraint(
            "provider_code",
            "dataset_kind",
            "payload_sha256",
            name="uq_historical_archive_import_checksum_identity",
        ),
        CheckConstraint(
            "length(trim(archive_schema_version)) > 0",
            name="ck_historical_archive_import_schema_version",
        ),
        CheckConstraint(
            "length(trim(provider_code)) > 0",
            name="ck_historical_archive_import_provider_code",
        ),
        CheckConstraint(
            "dataset_kind IN ('FIXTURES', 'MARKET_ODDS', 'SPORTTERY_BONUS', "
            "'MANUAL_QUANT', 'MATCH_RESULTS', 'PROVIDER_MAPPINGS')",
            name="ck_historical_archive_import_dataset_kind",
        ),
        CheckConstraint(
            "length(trim(source_reference)) > 0",
            name="ck_historical_archive_import_source_reference",
        ),
        CheckConstraint(
            "length(trim(source_description)) > 0",
            name="ck_historical_archive_import_source_description",
        ),
        CheckConstraint(
            "length(trim(license_note)) > 0",
            name="ck_historical_archive_import_license_note",
        ),
        CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_historical_archive_import_data_mode",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_historical_archive_import_payload_hash_length",
        ),
        CheckConstraint(
            "record_count >= 0",
            name="ck_historical_archive_import_record_count",
        ),
        CheckConstraint(
            "created_at_utc <= imported_at_utc",
            name="ck_historical_archive_import_timeline",
        ),
        Index(
            "ix_historical_archive_import_provider_dataset_created",
            "provider_code",
            "dataset_kind",
            "created_at_utc",
        ),
        Index(
            "ix_historical_archive_import_mode_imported",
            "data_mode",
            "imported_at_utc",
        ),
    )

    archive_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    archive_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_description: Mapped[str] = mapped_column(Text, nullable=False)
    license_note: Mapped[str] = mapped_column(String(2048), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MatchResultRecord(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        CheckConstraint("home_goals >= 0", name="ck_match_result_home_goals"),
        CheckConstraint("away_goals >= 0", name="ck_match_result_away_goals"),
        CheckConstraint(
            "observed_at_utc <= available_at_utc AND "
            "available_at_utc <= ingested_at_utc",
            name="ck_match_result_timeline",
        ),
        CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_match_result_payload_hash_length",
        ),
        ForeignKeyConstraint(
            [
                "supersedes_match_result_id",
                "internal_match_id",
                "provider_id",
            ],
            [
                "match_results.match_result_id",
                "match_results.internal_match_id",
                "match_results.provider_id",
            ],
            ondelete="RESTRICT",
            name="fk_match_result_supersession_lineage",
        ),
        UniqueConstraint(
            "match_result_id",
            "internal_match_id",
            "provider_id",
            name="uq_match_result_lineage_binding",
        ),
        UniqueConstraint(
            "provider_id",
            "source_result_key",
            name="uq_match_result_source_key",
        ),
        UniqueConstraint(
            "supersedes_match_result_id",
            name="uq_match_result_superseded_once",
        ),
        Index(
            "ix_match_results_match_provider_cutoff",
            "internal_match_id",
            "provider_id",
            "available_at_utc",
            "ingested_at_utc",
        ),
        Index(
            "uq_match_results_provider_match_root",
            "provider_id",
            "internal_match_id",
            unique=True,
            sqlite_where=text("supersedes_match_result_id IS NULL"),
            postgresql_where=text("supersedes_match_result_id IS NULL"),
        ),
    )

    match_result_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.provider_id", ondelete="RESTRICT"), nullable=False
    )
    provider_mapping_id: Mapped[str] = mapped_column(
        ForeignKey("provider_match_mappings.mapping_id", ondelete="RESTRICT"),
        nullable=False,
    )
    home_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    away_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    available_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ingested_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_result_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_match_result_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )


class TicketSettlementRecord(Base):
    __tablename__ = "ticket_settlements"
    __table_args__ = (
        CheckConstraint(
            "settlement_kind = 'BACKTEST'",
            name="ck_ticket_settlement_kind",
        ),
        CheckConstraint(
            "scope_kind IN ('ANALYSIS_RUN', 'PORTFOLIO_REVISION')",
            name="ck_ticket_settlement_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind = 'ANALYSIS_RUN' "
            "AND decision_scope_id = parent_analysis_run_id "
            "AND portfolio_revision_id IS NULL "
            "AND base_portfolio_id IS NOT NULL "
            "AND base_ticket_id IS NOT NULL "
            "AND base_portfolio_id = portfolio_id "
            "AND base_ticket_id = ticket_id) OR "
            "(scope_kind = 'PORTFOLIO_REVISION' "
            "AND portfolio_revision_id IS NOT NULL "
            "AND decision_scope_id = portfolio_revision_id "
            "AND base_portfolio_id IS NULL "
            "AND base_ticket_id IS NULL)",
            name="ck_ticket_settlement_scope_binding",
        ),
        CheckConstraint("stake_fen > 0", name="ck_ticket_settlement_stake"),
        CheckConstraint(
            "gross_payout_fen >= 0",
            name="ck_ticket_settlement_gross_payout",
        ),
        CheckConstraint(
            "profit_loss_fen = gross_payout_fen - stake_fen",
            name="ck_ticket_settlement_profit_loss",
        ),
        CheckConstraint(
            "(status = 'WON' AND gross_payout_fen > 0) OR "
            "(status = 'LOST' AND gross_payout_fen = 0)",
            name="ck_ticket_settlement_status_payout",
        ),
        CheckConstraint(
            "length(settlement_hash) = 64",
            name="ck_ticket_settlement_hash_length",
        ),
        ForeignKeyConstraint(
            [
                "supersedes_settlement_id",
                "scope_kind",
                "parent_analysis_run_id",
                "decision_scope_id",
                "portfolio_id",
                "ticket_id",
            ],
            [
                "ticket_settlements.settlement_id",
                "ticket_settlements.scope_kind",
                "ticket_settlements.parent_analysis_run_id",
                "ticket_settlements.decision_scope_id",
                "ticket_settlements.portfolio_id",
                "ticket_settlements.ticket_id",
            ],
            ondelete="RESTRICT",
            name="fk_ticket_settlement_correction_lineage",
        ),
        UniqueConstraint(
            "settlement_id",
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            "ticket_id",
            name="uq_ticket_settlement_lineage_binding",
        ),
        UniqueConstraint("settlement_hash", name="uq_ticket_settlement_hash"),
        UniqueConstraint(
            "supersedes_settlement_id",
            name="uq_ticket_settlement_superseded_once",
        ),
        Index(
            "ix_ticket_settlements_scope_ticket_cutoff",
            "decision_scope_id",
            "ticket_id",
            "settled_at_utc",
        ),
        Index(
            "uq_ticket_settlements_logical_root",
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            "ticket_id",
            unique=True,
            sqlite_where=text("supersedes_settlement_id IS NULL"),
            postgresql_where=text("supersedes_settlement_id IS NULL"),
        ),
    )

    settlement_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    settlement_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_scope_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    portfolio_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolio_revisions.portfolio_revision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    portfolio_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    ticket_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    base_portfolio_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"), nullable=True
    )
    base_ticket_id: Mapped[str | None] = mapped_column(
        ForeignKey("tickets.ticket_id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(EnumColumn, nullable=False)
    stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_payout_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    profit_loss_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    settlement_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    settled_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    settlement_json: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_settlement_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )


class TicketSettlementMatchResultRecord(Base):
    __tablename__ = "ticket_settlement_match_results"
    __table_args__ = (
        CheckConstraint("leg_no IN (1, 2)", name="ck_ticket_settlement_result_leg_no"),
        UniqueConstraint(
            "settlement_id",
            "match_result_id",
            name="uq_ticket_settlement_match_result",
        ),
        UniqueConstraint(
            "settlement_id",
            "internal_match_id",
            name="uq_ticket_settlement_internal_match",
        ),
    )

    settlement_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_settlements.settlement_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    leg_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_result_id: Mapped[str] = mapped_column(
        ForeignKey("match_results.match_result_id", ondelete="RESTRICT"),
        nullable=False,
    )
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )


class PortfolioSettlementRecord(Base):
    __tablename__ = "portfolio_settlements"
    __table_args__ = (
        CheckConstraint(
            "settlement_kind = 'BACKTEST'",
            name="ck_portfolio_settlement_kind",
        ),
        CheckConstraint(
            "scope_kind IN ('ANALYSIS_RUN', 'PORTFOLIO_REVISION')",
            name="ck_portfolio_settlement_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind = 'ANALYSIS_RUN' "
            "AND decision_scope_id = parent_analysis_run_id "
            "AND portfolio_revision_id IS NULL "
            "AND base_portfolio_id IS NOT NULL "
            "AND base_portfolio_id = portfolio_id) OR "
            "(scope_kind = 'PORTFOLIO_REVISION' "
            "AND portfolio_revision_id IS NOT NULL "
            "AND decision_scope_id = portfolio_revision_id "
            "AND base_portfolio_id IS NULL)",
            name="ck_portfolio_settlement_scope_binding",
        ),
        CheckConstraint("budget_fen >= 0", name="ck_portfolio_settlement_budget"),
        CheckConstraint("total_stake_fen >= 0", name="ck_portfolio_settlement_stake"),
        CheckConstraint("cash_fen >= 0", name="ck_portfolio_settlement_cash"),
        CheckConstraint(
            "total_stake_fen + cash_fen = budget_fen",
            name="ck_portfolio_settlement_capital_balance",
        ),
        CheckConstraint(
            "gross_payout_fen >= 0",
            name="ck_portfolio_settlement_gross_payout",
        ),
        CheckConstraint(
            "profit_loss_fen = gross_payout_fen - total_stake_fen",
            name="ck_portfolio_settlement_profit_loss",
        ),
        CheckConstraint(
            "ticket_count >= 0", name="ck_portfolio_settlement_ticket_count"
        ),
        CheckConstraint(
            "length(settlement_hash) = 64",
            name="ck_portfolio_settlement_hash_length",
        ),
        ForeignKeyConstraint(
            [
                "supersedes_portfolio_settlement_id",
                "scope_kind",
                "parent_analysis_run_id",
                "decision_scope_id",
                "portfolio_id",
            ],
            [
                "portfolio_settlements.portfolio_settlement_id",
                "portfolio_settlements.scope_kind",
                "portfolio_settlements.parent_analysis_run_id",
                "portfolio_settlements.decision_scope_id",
                "portfolio_settlements.portfolio_id",
            ],
            ondelete="RESTRICT",
            name="fk_portfolio_settlement_correction_lineage",
        ),
        UniqueConstraint(
            "portfolio_settlement_id",
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            name="uq_portfolio_settlement_lineage_binding",
        ),
        UniqueConstraint("settlement_hash", name="uq_portfolio_settlement_hash"),
        UniqueConstraint(
            "supersedes_portfolio_settlement_id",
            name="uq_portfolio_settlement_superseded_once",
        ),
        Index(
            "ix_portfolio_settlements_scope_portfolio_cutoff",
            "decision_scope_id",
            "portfolio_id",
            "settled_at_utc",
        ),
        Index(
            "uq_portfolio_settlements_logical_root",
            "scope_kind",
            "parent_analysis_run_id",
            "decision_scope_id",
            "portfolio_id",
            unique=True,
            sqlite_where=text("supersedes_portfolio_settlement_id IS NULL"),
            postgresql_where=text("supersedes_portfolio_settlement_id IS NULL"),
        ),
    )

    portfolio_settlement_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    settlement_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_scope_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    portfolio_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolio_revisions.portfolio_revision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    portfolio_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    base_portfolio_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"), nullable=True
    )
    budget_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    total_stake_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_payout_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    profit_loss_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    settlement_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    settled_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    settlement_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_portfolio_settlement_id: Mapped[str | None] = mapped_column(
        IdColumn, nullable=True
    )


class PortfolioSettlementTicketRecord(Base):
    __tablename__ = "portfolio_settlement_tickets"
    __table_args__ = (
        CheckConstraint("settlement_no > 0", name="ck_portfolio_settlement_ticket_no"),
        UniqueConstraint(
            "portfolio_settlement_id",
            "settlement_id",
            name="uq_portfolio_settlement_ticket",
        ),
    )

    portfolio_settlement_id: Mapped[str] = mapped_column(
        ForeignKey(
            "portfolio_settlements.portfolio_settlement_id", ondelete="RESTRICT"
        ),
        primary_key=True,
    )
    settlement_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    settlement_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_settlements.settlement_id", ondelete="RESTRICT"),
        nullable=False,
    )


class BacktestRunRecord(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint(
            "backtest_mode = 'STRICT_POINT_IN_TIME'",
            name="ck_backtest_run_strict_mode",
        ),
        CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_backtest_run_data_mode",
        ),
        CheckConstraint(
            "date_from <= date_to",
            name="ck_backtest_run_date_order",
        ),
        CheckConstraint(
            "length(trim(backtest_version)) > 0",
            name="ck_backtest_run_backtest_version",
        ),
        CheckConstraint(
            "length(trim(strategy_version)) > 0",
            name="ck_backtest_run_strategy_version",
        ),
        CheckConstraint(
            "length(strategy_config_hash) = 64",
            name="ck_backtest_run_strategy_hash_length",
        ),
        CheckConstraint(
            "length(trim(code_revision)) > 0",
            name="ck_backtest_run_code_revision",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_backtest_run_status",
        ),
        CheckConstraint(
            "started_at_utc <= completed_at_utc AND completed_at_utc <= created_at_utc",
            name="ck_backtest_run_timeline",
        ),
        CheckConstraint(
            "length(config_hash) = 64", name="ck_backtest_run_config_hash_length"
        ),
        CheckConstraint(
            "length(input_manifest_hash) = 64",
            name="ck_backtest_run_manifest_hash_length",
        ),
        CheckConstraint("length(run_hash) = 64", name="ck_backtest_run_hash_length"),
        UniqueConstraint("run_hash", name="uq_backtest_run_hash"),
        Index("ix_backtest_runs_completed_at", "completed_at_utc"),
        Index(
            "ix_backtest_runs_mode_created",
            "data_mode",
            "created_at_utc",
            "backtest_run_id",
        ),
    )

    backtest_run_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    backtest_version: Mapped[str] = mapped_column(String(80), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(80), nullable=False)
    backtest_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_manifest_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_of_backtest_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("backtest_runs.backtest_run_id", ondelete="RESTRICT"),
        nullable=True,
    )


class BacktestSliceRecord(Base):
    __tablename__ = "backtest_slices"
    __table_args__ = (
        CheckConstraint("slice_no > 0", name="ck_backtest_slice_no"),
        CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_backtest_slice_data_mode",
        ),
        CheckConstraint(
            "decision_as_of_at_utc < evaluation_as_of_at_utc",
            name="ck_backtest_slice_cutoffs",
        ),
        CheckConstraint(
            "scope_kind IN ('ANALYSIS_RUN', 'PORTFOLIO_REVISION')",
            name="ck_backtest_slice_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind = 'ANALYSIS_RUN' "
            "AND decision_scope_id = parent_analysis_run_id "
            "AND portfolio_revision_id IS NULL) OR "
            "(scope_kind = 'PORTFOLIO_REVISION' "
            "AND portfolio_revision_id IS NOT NULL "
            "AND decision_scope_id = portfolio_revision_id)",
            name="ck_backtest_slice_scope_binding",
        ),
        CheckConstraint(
            "length(slice_manifest_hash) = 64",
            name="ck_backtest_slice_manifest_hash_length",
        ),
        CheckConstraint(
            "length(slice_hash) = 64", name="ck_backtest_slice_hash_length"
        ),
        CheckConstraint(
            "match_count >= 0 AND settled_match_count >= 0 AND "
            "settled_match_count <= match_count",
            name="ck_backtest_slice_match_counts",
        ),
        CheckConstraint(
            "settled_ticket_count >= 0 AND unsettled_ticket_count >= 0",
            name="ck_backtest_slice_ticket_counts",
        ),
        CheckConstraint(
            "((settled_ticket_count + unsettled_ticket_count = 0 "
            "AND coverage IS NULL) OR "
            "(settled_ticket_count + unsettled_ticket_count > 0 "
            "AND coverage IS NOT NULL AND coverage >= 0 AND coverage <= 1 "
            "AND abs(coverage * (settled_ticket_count + unsettled_ticket_count) "
            "- settled_ticket_count) <= "
            "0.000000000001 * (settled_ticket_count + unsettled_ticket_count)))",
            name="ck_backtest_slice_coverage",
        ),
        UniqueConstraint(
            "backtest_run_id", "slice_no", name="uq_backtest_slice_number"
        ),
        UniqueConstraint(
            "backtest_slice_id",
            "backtest_run_id",
            name="uq_backtest_slice_run_binding",
        ),
        UniqueConstraint("slice_hash", name="uq_backtest_slice_hash"),
        Index(
            "ix_backtest_slices_run_evaluation",
            "backtest_run_id",
            "evaluation_as_of_at_utc",
        ),
    )

    backtest_slice_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    backtest_run_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_runs.backtest_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    slice_no: Mapped[int] = mapped_column(Integer, nullable=False)
    slice_version: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    data_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_scope_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    portfolio_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolio_revisions.portfolio_revision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    decision_as_of_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    evaluation_as_of_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    slice_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    slice_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    slice_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_match_count: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_ticket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unsettled_ticket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage: Mapped[Decimal | None] = mapped_column(RatioColumn, nullable=True)


class BacktestMetricSnapshotRecord(Base):
    __tablename__ = "backtest_metric_snapshots"
    __table_args__ = (
        CheckConstraint("snapshot_no > 0", name="ck_backtest_metric_snapshot_no"),
        CheckConstraint(
            "metric_scope IN ('RUN', 'SLICE')",
            name="ck_backtest_metric_scope",
        ),
        CheckConstraint(
            "(metric_scope = 'RUN' AND backtest_slice_id IS NULL) OR "
            "(metric_scope = 'SLICE' AND backtest_slice_id IS NOT NULL)",
            name="ck_backtest_metric_scope_binding",
        ),
        CheckConstraint(
            "as_of_at_utc <= calculated_at_utc",
            name="ck_backtest_metric_timeline",
        ),
        CheckConstraint(
            "length(metrics_hash) = 64",
            name="ck_backtest_metric_payload_hash_length",
        ),
        CheckConstraint(
            "length(lineage_hash) = 64",
            name="ck_backtest_metric_lineage_hash_length",
        ),
        CheckConstraint(
            "length(snapshot_hash) = 64",
            name="ck_backtest_metric_snapshot_hash_length",
        ),
        ForeignKeyConstraint(
            ["backtest_slice_id", "backtest_run_id"],
            ["backtest_slices.backtest_slice_id", "backtest_slices.backtest_run_id"],
            ondelete="RESTRICT",
            name="fk_backtest_metric_slice_run",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "metric_scope",
            "metric_key",
            "snapshot_no",
            name="uq_backtest_metric_sequence",
        ),
        UniqueConstraint("snapshot_hash", name="uq_backtest_metric_snapshot_hash"),
        Index(
            "ix_backtest_metrics_run_key_cutoff",
            "backtest_run_id",
            "metric_scope",
            "metric_key",
            "as_of_at_utc",
        ),
    )

    metric_snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    backtest_run_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_runs.backtest_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    backtest_slice_id: Mapped[str | None] = mapped_column(IdColumn, nullable=True)
    snapshot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_scope: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(80), nullable=False)
    as_of_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    calculated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    lineage_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestMetricSettlementRecord(Base):
    __tablename__ = "backtest_metric_settlements"

    metric_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_metric_snapshots.metric_snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    portfolio_settlement_id: Mapped[str] = mapped_column(
        ForeignKey(
            "portfolio_settlements.portfolio_settlement_id", ondelete="RESTRICT"
        ),
        primary_key=True,
    )


class BacktestMetricTicketSettlementRecord(Base):
    __tablename__ = "backtest_metric_ticket_settlements"

    metric_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_metric_snapshots.metric_snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    settlement_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_settlements.settlement_id", ondelete="RESTRICT"),
        primary_key=True,
    )


class BacktestV2RunRecord(Base):
    __tablename__ = "backtest_v2_runs"
    __table_args__ = (
        CheckConstraint(
            "schema_version = 'BACKTEST_V2_RUN_RECORD_V1'",
            name="ck_backtest_v2_run_schema",
        ),
        CheckConstraint(
            "backtest_version = 'BACKTEST_V2'",
            name="ck_backtest_v2_run_version",
        ),
        CheckConstraint(
            "data_mode IN ('LIVE_STRICT', 'SOURCE_TIME_RESEARCH')",
            name="ck_backtest_v2_run_data_mode",
        ),
        CheckConstraint("date_from <= date_to", name="ck_backtest_v2_run_dates"),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED')",
            name="ck_backtest_v2_run_status",
        ),
        CheckConstraint(
            "expected_slice_count > 0",
            name="ck_backtest_v2_run_slice_count",
        ),
        CheckConstraint(
            "length(strategy_config_hash) = 64 AND length(run_hash) = 64",
            name="ck_backtest_v2_run_hashes",
        ),
        UniqueConstraint("run_hash", name="uq_backtest_v2_run_hash"),
    )

    backtest_run_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    backtest_version: Mapped[str] = mapped_column(String(80), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expected_slice_count: Mapped[int] = mapped_column(Integer, nullable=False)
    run_json: Mapped[str] = mapped_column(Text, nullable=False)
    run_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestV2RunArchiveRecord(Base):
    __tablename__ = "backtest_v2_run_archives"
    __table_args__ = (
        CheckConstraint(
            "archive_no > 0",
            name="ck_backtest_v2_run_archive_no",
        ),
        UniqueConstraint(
            "backtest_run_id",
            "archive_no",
            name="uq_backtest_v2_run_archive_no",
        ),
    )

    backtest_run_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_runs.backtest_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    archive_id: Mapped[str] = mapped_column(
        ForeignKey("historical_archive_imports.archive_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    archive_no: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestV2SliceRecord(Base):
    __tablename__ = "backtest_v2_slices"
    __table_args__ = (
        CheckConstraint(
            "slice_version = 'BACKTEST_V2_SLICE_V1'",
            name="ck_backtest_v2_slice_version",
        ),
        CheckConstraint("slice_no > 0", name="ck_backtest_v2_slice_no"),
        CheckConstraint(
            "decision_as_of_at_utc < evaluation_as_of_at_utc",
            name="ck_backtest_v2_slice_cutoffs",
        ),
        CheckConstraint(
            "planned_target_count >= decision_target_count AND "
            "decision_target_count >= result_target_count AND "
            "quant_available_count + quant_unavailable_count = decision_target_count",
            name="ck_backtest_v2_slice_counts",
        ),
        CheckConstraint(
            "length(decision_snapshot_hash) = 64 AND length(slice_hash) = 64 "
            "AND length(settlement_result_hash) = 64 "
            "AND length(slate_snapshot_hash) = 64",
            name="ck_backtest_v2_slice_hashes",
        ),
        UniqueConstraint("backtest_run_id", "slice_no", name="uq_backtest_v2_slice_no"),
        UniqueConstraint("slice_hash", name="uq_backtest_v2_slice_hash"),
        ForeignKeyConstraint(
            ["quant_model_state_id", "analysis_run_id"],
            [
                "quant_model_states.quant_model_state_id",
                "quant_model_states.analysis_run_id",
            ],
            name="fk_backtest_v2_slice_model_state",
            ondelete="RESTRICT",
        ),
    )

    backtest_slice_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    backtest_run_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_runs.backtest_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    slice_no: Mapped[int] = mapped_column(Integer, nullable=False)
    slice_version: Mapped[str] = mapped_column(String(80), nullable=False)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="RESTRICT"), nullable=False
    )
    quant_model_state_id: Mapped[str] = mapped_column(IdColumn, nullable=False)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id", ondelete="RESTRICT"), nullable=False
    )
    portfolio_settlement_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "portfolio_settlements.portfolio_settlement_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    data_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_as_of_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    evaluation_as_of_at_utc: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    created_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    planned_target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quant_available_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quant_unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    decision_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    slice_json: Mapped[str] = mapped_column(Text, nullable=False)
    slice_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    settlement_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    slate_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    slate_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestV2TrainingSourceRecord(Base):
    __tablename__ = "backtest_v2_training_sources"
    __table_args__ = (
        UniqueConstraint(
            "backtest_slice_id",
            "match_result_id",
            name="uq_backtest_v2_training_result",
        ),
        CheckConstraint(
            "training_sequence >= 0",
            name="ck_backtest_v2_training_sequence",
        ),
        CheckConstraint(
            "length(source_payload_hash) = 64 AND length(fact_hash) = 64 "
            "AND length(archive_payload_sha256) = 64",
            name="ck_backtest_v2_training_hashes",
        ),
    )

    backtest_slice_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_slices.backtest_slice_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    training_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_result_id: Mapped[str] = mapped_column(
        ForeignKey("match_results.match_result_id", ondelete="RESTRICT"), nullable=False
    )
    archive_id: Mapped[str] = mapped_column(
        ForeignKey("historical_archive_imports.archive_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestV2EvaluationRefRecord(Base):
    __tablename__ = "backtest_v2_evaluation_refs"
    __table_args__ = (
        CheckConstraint(
            "decision_no > 0",
            name="ck_backtest_v2_evaluation_no",
        ),
        CheckConstraint(
            "status IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_backtest_v2_evaluation_status",
        ),
        CheckConstraint(
            "(status = 'AVAILABLE' AND quant_prediction_id IS NOT NULL "
            "AND final_prediction_id IS NOT NULL) OR "
            "(status = 'UNAVAILABLE' AND quant_prediction_id IS NULL "
            "AND final_prediction_id IS NULL)",
            name="ck_backtest_v2_evaluation_projection",
        ),
        CheckConstraint(
            "length(output_hash) = 64 AND length(model_prediction_hash) = 64",
            name="ck_backtest_v2_evaluation_hashes",
        ),
        UniqueConstraint(
            "backtest_slice_id",
            "internal_match_id",
            name="uq_backtest_v2_evaluation_match",
        ),
    )

    backtest_slice_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_slices.backtest_slice_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    decision_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    quant_model_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "quant_model_evaluations.quant_model_evaluation_id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    market_prediction_id: Mapped[str] = mapped_column(
        ForeignKey(
            "market_probabilities.market_probability_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    quant_prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("quant_predictions.quant_prediction_id", ondelete="RESTRICT"),
        nullable=True,
    )
    final_prediction_id: Mapped[str | None] = mapped_column(
        ForeignKey("final_predictions.final_prediction_id", ondelete="RESTRICT"),
        nullable=True,
    )


class BacktestV2ResultSourceRecord(Base):
    __tablename__ = "backtest_v2_result_sources"
    __table_args__ = (
        CheckConstraint(
            "result_no > 0",
            name="ck_backtest_v2_result_no",
        ),
        CheckConstraint(
            "length(source_payload_hash) = 64 AND length(archive_payload_sha256) = 64",
            name="ck_backtest_v2_result_hashes",
        ),
        UniqueConstraint(
            "backtest_slice_id",
            "internal_match_id",
            name="uq_backtest_v2_result_match",
        ),
    )

    backtest_slice_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_slices.backtest_slice_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    result_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.internal_match_id", ondelete="RESTRICT"), nullable=False
    )
    match_result_id: Mapped[str] = mapped_column(
        ForeignKey("match_results.match_result_id", ondelete="RESTRICT"), nullable=False
    )
    archive_id: Mapped[str] = mapped_column(
        ForeignKey("historical_archive_imports.archive_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestV2SliceTicketSettlementRecord(Base):
    __tablename__ = "backtest_v2_slice_ticket_settlements"
    __table_args__ = (
        CheckConstraint(
            "settlement_no > 0",
            name="ck_backtest_v2_slice_ticket_settlement_no",
        ),
        UniqueConstraint(
            "backtest_slice_id",
            "settlement_id",
            name="uq_backtest_v2_slice_ticket_settlement",
        ),
    )

    backtest_slice_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_slices.backtest_slice_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    settlement_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    settlement_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_settlements.settlement_id", ondelete="RESTRICT"),
        nullable=False,
    )


class BacktestV2MetricSnapshotRecord(Base):
    __tablename__ = "backtest_v2_metric_snapshots"
    __table_args__ = (
        CheckConstraint(
            "metric_version = 'BACKTEST_METRICS_V2'",
            name="ck_backtest_v2_metric_version",
        ),
        CheckConstraint(
            "as_of_at_utc <= calculated_at_utc",
            name="ck_backtest_v2_metric_timeline",
        ),
        CheckConstraint(
            "length(metrics_hash) = 64 AND length(lineage_hash) = 64 "
            "AND length(snapshot_hash) = 64",
            name="ck_backtest_v2_metric_hashes",
        ),
        UniqueConstraint("snapshot_hash", name="uq_backtest_v2_metric_hash"),
    )

    metric_snapshot_id: Mapped[str] = mapped_column(IdColumn, primary_key=True)
    backtest_run_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_v2_runs.backtest_run_id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    metric_version: Mapped[str] = mapped_column(String(80), nullable=False)
    as_of_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    calculated_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lineage_json: Mapped[str] = mapped_column(Text, nullable=False)
    lineage_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
