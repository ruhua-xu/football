"""add fixture ingestion capture

Revision ID: a6c1f9e3b742
Revises: d2e7a4c9b615
Create Date: 2026-09-02 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6c1f9e3b742"
down_revision: Union[str, None] = "d2e7a4c9b615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOURCE_TABLES = (
    "fixture_ingestion_captures",
    "fixture_observations",
)

IMMUTABLE_INSERT_KEYS = {
    "fixture_ingestion_captures": (
        ("ingestion_id",),
        ("raw_artifact_id",),
    ),
    "fixture_observations": (
        ("observation_id",),
        ("ingestion_id", "internal_match_id"),
        ("ingestion_id", "provider_mapping_id"),
    ),
}

FIXTURE_IDENTITY_ORIGIN_INDEXES = {
    "matches": "ix_matches_fixture_ingestion",
    "provider_team_aliases": "ix_provider_team_alias_fixture_ingestion",
    "provider_competition_mappings": (
        "ix_provider_competition_mapping_fixture_ingestion"
    ),
    "canonical_match_identities": ("ix_canonical_match_identity_fixture_ingestion"),
    "provider_match_mappings": "ix_provider_match_mapping_fixture_ingestion",
}

FIXTURE_IDENTITY_ORIGIN_RULES = {
    "matches": "ingestion.available_at_utc = NEW.available_at_utc",
    "provider_team_aliases": (
        "ingestion.provider_id = NEW.provider_id "
        "AND ingestion.language = NEW.language "
        "AND ingestion.team_type = NEW.team_type "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
    "provider_competition_mappings": (
        "ingestion.provider_id = NEW.provider_id "
        "AND ingestion.provider_competition_id = NEW.provider_competition_id "
        "AND ingestion.season = NEW.season "
        "AND ingestion.competition_type = NEW.competition_type "
        "AND ingestion.language = NEW.language "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
    "canonical_match_identities": (
        "ingestion.season = NEW.season "
        "AND ingestion.competition_type = NEW.competition_type "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
    "provider_match_mappings": (
        "ingestion.provider_id = NEW.provider_id "
        "AND ingestion.available_at_utc = NEW.available_at_utc"
    ),
}


def upgrade() -> None:
    _require_sqlite_dialect()
    op.create_table(
        "fixture_ingestion_captures",
        sa.Column("ingestion_id", sa.String(length=160), nullable=False),
        sa.Column("provider_id", sa.String(length=160), nullable=False),
        sa.Column("kickoff_from_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kickoff_to_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_competition_id", sa.String(length=160), nullable=False),
        sa.Column("provider_season_id", sa.String(length=160), nullable=False),
        sa.Column("season", sa.String(length=160), nullable=False),
        sa.Column("competition_type", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("team_type", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("request_parameters_json", sa.Text(), nullable=False),
        sa.Column("requested_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("provider_request_id", sa.String(length=512), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("raw_artifact_id", sa.String(length=64), nullable=False),
        sa.Column("raw_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "kickoff_from_utc <= kickoff_to_utc",
            name="ck_fixture_ingestion_kickoff_window",
        ),
        sa.CheckConstraint(
            "length(trim(endpoint)) > 0 AND length(endpoint) <= 2048",
            name="ck_fixture_ingestion_endpoint",
        ),
        sa.CheckConstraint(
            "json_valid(request_parameters_json) = 1 AND "
            "json_type(request_parameters_json) = 'object'",
            name="ck_fixture_ingestion_request_parameters_json",
        ),
        sa.CheckConstraint(
            "requested_at_utc <= received_at_utc AND "
            "available_at_utc <= received_at_utc AND "
            "received_at_utc <= ingested_at_utc",
            name="ck_fixture_ingestion_request_timeline",
        ),
        sa.CheckConstraint(
            "http_status >= 200 AND http_status <= 299",
            name="ck_fixture_ingestion_http_status",
        ),
        sa.CheckConstraint(
            "provider_request_id IS NULL OR length(provider_request_id) > 0",
            name="ck_fixture_ingestion_provider_request_id",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="ck_fixture_ingestion_duration",
        ),
        sa.CheckConstraint(
            "outcome = 'SUCCESS' AND failure_code IS NULL",
            name="ck_fixture_ingestion_success",
        ),
        sa.CheckConstraint(
            "length(trim(provider_competition_id)) > 0 AND "
            "length(trim(provider_season_id)) > 0 AND "
            "length(trim(season)) > 0 AND "
            "length(trim(competition_type)) > 0 AND "
            "length(language) >= 2 AND length(language) <= 35 AND "
            "team_type IN ('CLUB', 'NATIONAL', 'WOMEN', 'YOUTH', 'RESERVE')",
            name="ck_fixture_ingestion_scope",
        ),
        sa.CheckConstraint(
            "length(raw_artifact_id) = 64 AND raw_artifact_id NOT GLOB '*[^0-9a-f]*'",
            name="ck_fixture_ingestion_artifact_id",
        ),
        sa.CheckConstraint(
            "length(raw_payload_sha256) = 64 AND "
            "raw_payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_fixture_ingestion_payload_hash",
        ),
        sa.CheckConstraint(
            "observation_count >= 0",
            name="ck_fixture_ingestion_observation_count",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("ingestion_id"),
        sa.UniqueConstraint(
            "raw_artifact_id",
            name="uq_fixture_ingestion_raw_artifact",
        ),
    )
    op.create_index(
        "ix_fixture_ingestion_provider_available",
        "fixture_ingestion_captures",
        ["provider_id", "available_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_fixture_ingestion_scope_available",
        "fixture_ingestion_captures",
        [
            "provider_id",
            "provider_competition_id",
            "provider_season_id",
            "available_at_utc",
        ],
        unique=False,
    )

    for table_name, index_name in FIXTURE_IDENTITY_ORIGIN_INDEXES.items():
        op.add_column(
            table_name,
            sa.Column("fixture_ingestion_id", sa.String(length=160), nullable=True),
        )
        op.create_index(
            index_name,
            table_name,
            ["fixture_ingestion_id"],
            unique=False,
        )

    op.create_table(
        "fixture_observations",
        sa.Column("observation_id", sa.String(length=160), nullable=False),
        sa.Column("ingestion_id", sa.String(length=160), nullable=False),
        sa.Column("provider_mapping_id", sa.String(length=160), nullable=False),
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("kickoff_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'FINISHED', 'POSTPONED', 'CANCELLED')",
            name="ck_fixture_observation_status",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_fixture_observation_payload_hash",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["fixture_ingestion_captures.ingestion_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_mapping_id"],
            ["provider_match_mappings.mapping_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint(
            "ingestion_id",
            "internal_match_id",
            name="uq_fixture_observation_ingestion_match",
        ),
        sa.UniqueConstraint(
            "ingestion_id",
            "provider_mapping_id",
            name="uq_fixture_observation_ingestion_mapping",
        ),
    )
    op.create_index(
        "ix_fixture_observation_match_available",
        "fixture_observations",
        ["internal_match_id", "available_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_fixture_observation_mapping_available",
        "fixture_observations",
        ["provider_mapping_id", "available_at_utc"],
        unique=False,
    )
    _install_triggers()


def downgrade() -> None:
    _require_sqlite_dialect()
    _require_empty_fixture_ingestion()
    _drop_triggers()
    op.drop_index(
        "ix_fixture_observation_mapping_available",
        table_name="fixture_observations",
        if_exists=True,
    )
    op.drop_index(
        "ix_fixture_observation_match_available",
        table_name="fixture_observations",
        if_exists=True,
    )
    op.drop_table("fixture_observations", if_exists=True)
    for table_name, index_name in reversed(
        tuple(FIXTURE_IDENTITY_ORIGIN_INDEXES.items())
    ):
        op.drop_index(index_name, table_name=table_name, if_exists=True)
        op.drop_column(table_name, "fixture_ingestion_id")
    op.drop_index(
        "ix_fixture_ingestion_scope_available",
        table_name="fixture_ingestion_captures",
        if_exists=True,
    )
    op.drop_index(
        "ix_fixture_ingestion_provider_available",
        table_name="fixture_ingestion_captures",
        if_exists=True,
    )
    op.drop_table("fixture_ingestion_captures", if_exists=True)


def _install_triggers() -> None:
    for table_name, key_sets in IMMUTABLE_INSERT_KEYS.items():
        conflict_condition = " OR ".join(
            "("
            + " AND ".join(f"existing.{column} = NEW.{column}" for column in key_set)
            + ")"
            for key_set in key_sets
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable_insert_existing
            BEFORE INSERT ON {table_name}
            WHEN EXISTS (
                SELECT 1 FROM {table_name} existing
                WHERE {conflict_condition}
            )
            BEGIN
                SELECT RAISE(ABORT, 'immutable record already exists');
            END
            """
        )
    for table_name in SOURCE_TABLES:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_append_only_{action.lower()}
                BEFORE {action} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'source records are append-only');
                END
                """
            )
    for table_name, rule in FIXTURE_IDENTITY_ORIGIN_RULES.items():
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_fixture_ingestion_origin_insert
            BEFORE INSERT ON {table_name}
            WHEN NEW.fixture_ingestion_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM fixture_ingestion_captures ingestion
                WHERE ingestion.ingestion_id = NEW.fixture_ingestion_id
                AND {rule}
            )
            BEGIN
                SELECT RAISE(ABORT, 'fixture identity origin is inconsistent');
            END
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_fixture_observations_lineage_insert
        BEFORE INSERT ON fixture_observations
        WHEN NOT EXISTS (
            SELECT 1
            FROM fixture_ingestion_captures ingestion
            JOIN provider_match_mappings mapping
              ON mapping.mapping_id = NEW.provider_mapping_id
            WHERE ingestion.ingestion_id = NEW.ingestion_id
            AND mapping.provider_id = ingestion.provider_id
            AND mapping.internal_match_id = NEW.internal_match_id
            AND mapping.available_at_utc <= NEW.available_at_utc
            AND ingestion.available_at_utc = NEW.available_at_utc
        )
        BEGIN
            SELECT RAISE(ABORT, 'fixture observation lineage is inconsistent');
        END
        """
    )


def _drop_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_fixture_observations_lineage_insert")
    for table_name in FIXTURE_IDENTITY_ORIGIN_RULES:
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_fixture_ingestion_origin_insert"
        )
    for table_name in SOURCE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_insert_existing")
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only_{action}")


def _require_sqlite_dialect() -> None:
    dialect = op.get_context().dialect.name
    if dialect != "sqlite":
        raise RuntimeError(
            f"Unsupported database backend '{dialect}'; "
            "football-system v0.4.0 supports SQLite only."
        )


def _require_empty_fixture_ingestion() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "offline fixture-ingestion downgrade is unsupported because capture "
            "provenance must be checked"
        )
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM fixture_ingestion_captures)")
    ):
        raise RuntimeError(
            "cannot downgrade fixture-ingestion schema while capture data exists"
        )
