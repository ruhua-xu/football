import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from football_system.application.ports.data_providers import SnapshotQuery
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
    MatchIdentityResolver,
    TeamIdentity,
    UnresolvedMatchMappingError,
)
from football_system.infrastructure.providers.real.sporttery_manual import (
    SPORTTERY_MANUAL_PROVIDER_CODE,
    SportteryManualArchiveError,
    SportteryManualArchiveProvider,
)

UTC = timezone.utc
CAPTURED = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
REVIEWED = datetime(2026, 9, 1, 9, 5, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 3, 19, 0, tzinfo=UTC)
FIXTURES = Path("tests/fixtures/providers/synthetic_sporttery")


def _resolver(
    *, match_ids: tuple[str, ...] = ("manual-match",)
) -> MatchIdentityResolver:
    return MatchIdentityResolver(
        team_identities=(
            TeamIdentity(
                internal_team_id="team-harbour",
                canonical_name="Fabricated Harbour FC",
                aliases=(
                    Alias(
                        provider_code=SPORTTERY_MANUAL_PROVIDER_CODE,
                        provider_team_id="synthetic-harbour",
                        provider_team_name="Fabricated Harbour FC",
                        language="en",
                    ),
                ),
            ),
            TeamIdentity(
                internal_team_id="team-orchard",
                canonical_name="Fabricated Orchard FC",
                aliases=(
                    Alias(
                        provider_code=SPORTTERY_MANUAL_PROVIDER_CODE,
                        provider_team_id="synthetic-orchard",
                        provider_team_name="Fabricated Orchard FC",
                        language="en",
                    ),
                ),
            ),
        ),
        competition_mappings=(
            CompetitionMapping(
                internal_competition_id="synthetic-coastal",
                provider_code=SPORTTERY_MANUAL_PROVIDER_CODE,
                provider_competition_id="synthetic-coastal",
                provider_competition_name="Fabricated Coastal League",
                language="en",
                season="2026/27",
                competition_type="LEAGUE",
            ),
        ),
        canonical_matches=tuple(
            CanonicalMatchIdentity(
                internal_match_id=match_id,
                internal_competition_id="synthetic-coastal",
                internal_home_team_id="team-harbour",
                internal_away_team_id="team-orchard",
                season="2026/27",
                competition_type="LEAGUE",
                kickoff_at_utc=KICKOFF,
            )
            for match_id in match_ids
        ),
    )


def _query(*, cutoff: datetime = REVIEWED) -> SnapshotQuery:
    return SnapshotQuery(match_ids=("manual-match",), as_of_at_utc=cutoff)


def _document_data(
    artifact_name: str,
    artifact_hash: str,
    *,
    snapshot_id: str = "manual-snapshot-001",
    reviewed_at: datetime = REVIEWED,
    home_win: object = "2.11",
) -> dict[str, object]:
    return {
        "schema_version": "SPORTTERY_MANUAL_ARCHIVE_V1",
        "snapshot_id": snapshot_id,
        "captured_at_utc": CAPTURED.isoformat(),
        "source_reference": "synthetic://manual-sporttery-contract",
        "source_artifact_path": artifact_name,
        "source_artifact_sha256": artifact_hash,
        "entered_by": "synthetic-enterer",
        "reviewed_by": "synthetic-reviewer",
        "reviewed_at_utc": reviewed_at.isoformat(),
        "records": [
            {
                "sporttery_match_no": "SYN001",
                "match_number_date": "2026-09-01",
                "provider_competition_id": "synthetic-coastal",
                "provider_competition_name": "Fabricated Coastal League",
                "competition_language": "en",
                "season": "2026/27",
                "competition_type": "LEAGUE",
                "provider_home_team_id": "synthetic-harbour",
                "provider_home_team_name": "Fabricated Harbour FC",
                "home_team_language": "en",
                "provider_away_team_id": "synthetic-orchard",
                "provider_away_team_name": "Fabricated Orchard FC",
                "away_team_language": "en",
                "kickoff_at_utc": KICKOFF.isoformat(),
                "sale_status": "OPEN",
                "market_type": "THREE_WAY",
                "home_win": home_win,
                "draw": "3.16",
                "away_win": "3.57",
            }
        ],
    }


def _write_document(
    directory: Path,
    *,
    filename: str = "manual.json",
    snapshot_id: str = "manual-snapshot-001",
    reviewed_at: datetime = REVIEWED,
    home_win: object = "2.11",
) -> Path:
    artifact = directory / f"{snapshot_id}.raw"
    artifact.write_bytes(b"SYNTHETIC CONTRACT FIXTURE - NOT OFFICIAL SPORTTERY DATA\n")
    data = _document_data(
        artifact.name,
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        snapshot_id=snapshot_id,
        reviewed_at=reviewed_at,
        home_win=home_win,
    )
    document = directory / filename
    document.write_text(json.dumps(data), encoding="utf-8")
    return document


def test_json_and_csv_manual_archives_are_reviewed_exact_and_point_in_time() -> None:
    json_provider = SportteryManualArchiveProvider(
        FIXTURES / "manual.json", _resolver()
    )
    csv_provider = SportteryManualArchiveProvider(FIXTURES / "manual.csv", _resolver())

    json_batch = asyncio.run(json_provider.fetch_fixed_bonus(_query()))
    csv_batch = asyncio.run(csv_provider.fetch_fixed_bonus(_query()))
    before_review = asyncio.run(
        json_provider.fetch_fixed_bonus(_query(cutoff=CAPTURED))
    )

    assert len(json_batch.snapshots) == len(json_batch.mappings) == 1
    assert len(csv_batch.snapshots) == len(csv_batch.mappings) == 1
    assert json_batch.snapshots[0].sporttery_match_no == "SYN001"
    assert csv_batch.snapshots[0].sporttery_match_no == "SYN002"
    assert json_batch.snapshots[0].available_at_utc == REVIEWED
    assert json_batch.mappings[0].provider_code == SPORTTERY_MANUAL_PROVIDER_CODE
    assert before_review.snapshots == ()
    assert before_review.mappings == ()


@pytest.mark.parametrize(
    "change",
    (
        lambda data: data.update({"unexpected": "field"}),
        lambda data: data.update({"entered_by": "synthetic-reviewer"}),
        lambda data: data.update({"source_artifact_sha256": "0" * 64}),
        lambda data: data["records"][0].update({"home_win": 2.11}),
        lambda data: data["records"][0].update({"market_type": "HHAD"}),
        lambda data: data["records"].append(data["records"][0].copy()),
    ),
)
def test_manual_archive_rejects_unreviewed_unsafe_and_noncanonical_input(
    tmp_path: Path,
    change: object,
) -> None:
    document = _write_document(tmp_path)
    data = json.loads(document.read_text(encoding="utf-8"))
    change(data)
    document.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SportteryManualArchiveError):
        SportteryManualArchiveProvider(document, _resolver())


def test_manual_archive_corrections_are_append_only_and_choose_latest_review(
    tmp_path: Path,
) -> None:
    _write_document(
        tmp_path,
        filename="first.json",
        snapshot_id="manual-correction-one",
        home_win="2.11",
    )
    _write_document(
        tmp_path,
        filename="second.json",
        snapshot_id="manual-correction-two",
        reviewed_at=datetime(2026, 9, 1, 9, 10, tzinfo=UTC),
        home_win="2.31",
    )
    provider = SportteryManualArchiveProvider(tmp_path, _resolver())

    batch = asyncio.run(
        provider.fetch_fixed_bonus(
            _query(cutoff=datetime(2026, 9, 1, 9, 10, tzinfo=UTC))
        )
    )

    assert len(batch.snapshots) == 1
    assert batch.snapshots[0].three_way_bonus().home_win == Decimal("2.31")
    assert batch.snapshots[0].source_snapshot_key != "manual-correction-one"


def test_manual_archive_does_not_fuzzy_resolve_identity(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    with pytest.raises(UnresolvedMatchMappingError):
        SportteryManualArchiveProvider(
            document,
            MatchIdentityResolver((), (), ()),
        )
