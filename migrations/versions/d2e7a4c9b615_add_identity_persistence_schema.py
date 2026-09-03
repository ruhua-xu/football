"""add identity persistence schema

Revision ID: d2e7a4c9b615
Revises: f3a1c6d8e204
Create Date: 2026-09-02 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e7a4c9b615"
down_revision: Union[str, None] = "f3a1c6d8e204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOURCE_TABLES = (
    "provider_team_aliases",
    "provider_competition_mappings",
    "canonical_match_identities",
)

IMMUTABLE_INSERT_KEYS = {
    "provider_team_aliases": (
        ("alias_id",),
        (
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "internal_team_id",
        ),
    ),
    "provider_competition_mappings": (
        ("mapping_id",),
        (
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "internal_competition_id",
        ),
    ),
    "canonical_match_identities": (("internal_match_id",),),
}


def upgrade() -> None:
    _require_sqlite_dialect()
    op.create_table(
        "provider_team_aliases",
        sa.Column("alias_id", sa.String(length=160), nullable=False),
        sa.Column("internal_team_id", sa.String(length=160), nullable=False),
        sa.Column("provider_id", sa.String(length=160), nullable=False),
        sa.Column("provider_team_id", sa.String(length=160), nullable=False),
        sa.Column("provider_team_name", sa.String(length=160), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("team_type", sa.String(length=64), nullable=False),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["internal_team_id"], ["teams.team_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("alias_id"),
        sa.UniqueConstraint(
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "internal_team_id",
            name="uq_provider_team_alias_exact_target",
        ),
    )
    op.create_index(
        "ix_provider_team_alias_lookup_cutoff",
        "provider_team_aliases",
        [
            "provider_id",
            "provider_team_id",
            "provider_team_name",
            "language",
            "team_type",
            "available_at_utc",
        ],
        unique=False,
    )

    op.create_table(
        "provider_competition_mappings",
        sa.Column("mapping_id", sa.String(length=160), nullable=False),
        sa.Column("internal_competition_id", sa.String(length=160), nullable=False),
        sa.Column("provider_id", sa.String(length=160), nullable=False),
        sa.Column("provider_competition_id", sa.String(length=160), nullable=False),
        sa.Column("provider_competition_name", sa.String(length=160), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("season", sa.String(length=160), nullable=False),
        sa.Column("competition_type", sa.String(length=64), nullable=False),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["internal_competition_id"],
            ["competitions.competition_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("mapping_id"),
        sa.UniqueConstraint(
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "internal_competition_id",
            name="uq_provider_competition_mapping_exact_target",
        ),
    )
    op.create_index(
        "ix_provider_competition_mapping_lookup_cutoff",
        "provider_competition_mappings",
        [
            "provider_id",
            "provider_competition_id",
            "provider_competition_name",
            "language",
            "season",
            "competition_type",
            "available_at_utc",
        ],
        unique=False,
    )

    op.create_table(
        "canonical_match_identities",
        sa.Column("internal_match_id", sa.String(length=160), nullable=False),
        sa.Column("season", sa.String(length=160), nullable=False),
        sa.Column("competition_type", sa.String(length=64), nullable=False),
        sa.Column("available_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["internal_match_id"],
            ["matches.internal_match_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("internal_match_id"),
    )
    op.create_index(
        "ix_canonical_match_identity_season_type_cutoff",
        "canonical_match_identities",
        ["season", "competition_type", "available_at_utc"],
        unique=False,
    )
    _install_triggers()


def downgrade() -> None:
    _require_sqlite_dialect()
    _drop_triggers()
    op.drop_index(
        "ix_canonical_match_identity_season_type_cutoff",
        table_name="canonical_match_identities",
        if_exists=True,
    )
    op.drop_table("canonical_match_identities", if_exists=True)
    op.drop_index(
        "ix_provider_competition_mapping_lookup_cutoff",
        table_name="provider_competition_mappings",
        if_exists=True,
    )
    op.drop_table("provider_competition_mappings", if_exists=True)
    op.drop_index(
        "ix_provider_team_alias_lookup_cutoff",
        table_name="provider_team_aliases",
        if_exists=True,
    )
    op.drop_table("provider_team_aliases", if_exists=True)


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


def _drop_triggers() -> None:
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
