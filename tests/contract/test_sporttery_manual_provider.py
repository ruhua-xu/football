import asyncio
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from football_system.application.environment import RuntimeEnvironment
from football_system.application.ports.data_providers import SnapshotQuery
from football_system.domain.archive import HistoricalDataMode
from football_system.domain.common import stable_id
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
    SportteryManualReviewLevel,
    SportteryManualSnapshotProvenance,
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
    review_level: object = "INDEPENDENT_REVIEWED",
    entered_by: str = "synthetic-enterer",
    reviewed_by: str = "synthetic-reviewer",
    sporttery_match_no: str = "SYN001",
    match_number_date: str = "2026-09-01",
) -> dict[str, object]:
    return {
        "schema_version": "SPORTTERY_MANUAL_ARCHIVE_V2",
        "snapshot_id": snapshot_id,
        "captured_at_utc": CAPTURED.isoformat(),
        "source_reference": "synthetic://manual-sporttery-contract",
        "source_artifact_path": artifact_name,
        "source_artifact_sha256": artifact_hash,
        "entered_by": entered_by,
        "review_level": review_level,
        "reviewed_by": reviewed_by,
        "reviewed_at_utc": reviewed_at.isoformat(),
        "records": [
            {
                "sporttery_match_no": sporttery_match_no,
                "match_number_date": match_number_date,
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
    review_level: object = "INDEPENDENT_REVIEWED",
    entered_by: str = "synthetic-enterer",
    reviewed_by: str = "synthetic-reviewer",
    sporttery_match_no: str = "SYN001",
    match_number_date: str = "2026-09-01",
) -> Path:
    artifact = directory / f"{snapshot_id}.raw"
    artifact.write_bytes(b"SYNTHETIC CONTRACT FIXTURE - NOT OFFICIAL SPORTTERY DATA\n")
    data = _document_data(
        artifact.name,
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        snapshot_id=snapshot_id,
        reviewed_at=reviewed_at,
        home_win=home_win,
        review_level=review_level,
        entered_by=entered_by,
        reviewed_by=reviewed_by,
        sporttery_match_no=sporttery_match_no,
        match_number_date=match_number_date,
    )
    document = directory / filename
    document.write_text(json.dumps(data), encoding="utf-8")
    return document


def test_json_and_csv_manual_archives_support_both_review_levels_and_cutoff() -> None:
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
    assert json_batch.mappings[0].external_match_id == "2026-09-01:SYN001"
    assert csv_batch.mappings[0].external_match_id == "2026-09-01:SYN002"
    assert (
        json_provider.provenance_for_snapshot(
            json_batch.snapshots[0].snapshot_id
        ).review_level
        is SportteryManualReviewLevel.SELF_REVIEWED
    )
    assert (
        csv_provider.provenance_for_snapshot(
            csv_batch.snapshots[0].snapshot_id
        ).review_level
        is SportteryManualReviewLevel.INDEPENDENT_REVIEWED
    )
    assert before_review.snapshots == ()
    assert before_review.mappings == ()


def test_manual_archive_exposes_runtime_and_snapshot_provenance() -> None:
    provider = SportteryManualArchiveProvider(FIXTURES / "manual.json", _resolver())
    batch = asyncio.run(provider.fetch_fixed_bonus(_query()))
    snapshot = batch.snapshots[0]

    provenance = provider.provenance_for_snapshot(snapshot.snapshot_id)

    assert provider.runtime_provenance.environment is RuntimeEnvironment.LIVE
    assert provider.runtime_provenance.data_mode is HistoricalDataMode.LIVE_STRICT
    assert provider.runtime_provenance.provider_code == SPORTTERY_MANUAL_PROVIDER_CODE
    assert provider.runtime_provenance.is_mock is False
    assert isinstance(provenance, SportteryManualSnapshotProvenance)
    assert provenance.snapshot_id == snapshot.snapshot_id
    assert provenance.schema_version == "SPORTTERY_MANUAL_ARCHIVE_V2"
    assert provenance.source_snapshot_key == snapshot.source_snapshot_key
    assert provenance.archive_snapshot_id == "synthetic-sporttery-snapshot-001"
    assert provenance.review_level is SportteryManualReviewLevel.SELF_REVIEWED
    assert provenance.entered_by == provenance.reviewed_by == "synthetic-enterer"
    assert provenance.captured_at_utc == CAPTURED
    assert provenance.reviewed_at_utc == REVIEWED
    assert provenance.source_reference == "synthetic://sporttery-contract-fixture"
    assert provenance.source_artifact_path == "sporttery_source.raw"
    assert provenance.source_artifact_sha256 == (
        "ba38c53cd7d0b245c801d0246952714083b30fbdfde5191bd125449dd0476803"
    )
    with pytest.raises(KeyError, match="provenance is unavailable"):
        provider.provenance_for_snapshot("unknown-snapshot")


@pytest.mark.parametrize(
    "change",
    (
        lambda data: data.update({"unexpected": "field"}),
        lambda data: data.update({"entered_by": "synthetic-reviewer"}),
        lambda data: data.update({"review_level": "SELF_REVIEWED"}),
        lambda data: data.update({"review_level": "UNREVIEWED"}),
        lambda data: data.pop("review_level"),
        lambda data: data.update({"reviewed_at_utc": "2026-09-01T08:59:59+00:00"}),
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


@pytest.mark.parametrize("column_change", ("missing", "extra"))
def test_manual_csv_requires_the_exact_review_level_schema(
    tmp_path: Path,
    column_change: str,
) -> None:
    artifact = tmp_path / "sporttery_source.raw"
    artifact.write_bytes((FIXTURES / artifact.name).read_bytes())
    rows = list(
        csv.reader(io.StringIO((FIXTURES / "manual.csv").read_text(encoding="utf-8")))
    )
    if column_change == "missing":
        review_level_index = rows[0].index("review_level")
        for row in rows:
            row.pop(review_level_index)
    else:
        rows[0].append("unexpected")
        rows[1].append("field")
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    document = tmp_path / "manual.csv"
    document.write_text(output.getvalue(), encoding="utf-8")

    with pytest.raises(SportteryManualArchiveError):
        SportteryManualArchiveProvider(document, _resolver())


def test_legacy_v1_independent_review_archive_remains_replayable(
    tmp_path: Path,
) -> None:
    document = _write_document(tmp_path)
    data = json.loads(document.read_text(encoding="utf-8"))
    data["schema_version"] = "SPORTTERY_MANUAL_ARCHIVE_V1"
    data.pop("review_level")
    document.write_text(json.dumps(data), encoding="utf-8")

    provider = SportteryManualArchiveProvider(
        document,
        MatchIdentityResolver(
            (),
            (),
            (),
            explicit_mappings={
                (
                    SPORTTERY_MANUAL_PROVIDER_CODE,
                    "sporttery_match",
                    "SYN001",
                ): "manual-match"
            },
        ),
    )
    batch = asyncio.run(provider.fetch_fixed_bonus(_query()))

    assert len(batch.snapshots) == 1
    assert batch.mappings[0].external_match_id == "SYN001"
    assert batch.snapshots[0].source_snapshot_key == stable_id(
        "sporttery-manual-source",
        "manual-snapshot-001",
        "SYN001",
        "2026-09-01",
        data["source_artifact_sha256"],
    )
    assert (
        provider.provenance_for_snapshot(batch.snapshots[0].snapshot_id).review_level
        is SportteryManualReviewLevel.INDEPENDENT_REVIEWED
    )


def test_legacy_v1_cannot_declare_v2_self_review_semantics(tmp_path: Path) -> None:
    document = _write_document(
        tmp_path,
        review_level="SELF_REVIEWED",
        entered_by="synthetic-enterer",
        reviewed_by="synthetic-enterer",
    )
    data = json.loads(document.read_text(encoding="utf-8"))
    data["schema_version"] = "SPORTTERY_MANUAL_ARCHIVE_V1"
    document.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SportteryManualArchiveError):
        SportteryManualArchiveProvider(document, _resolver())

    data["schema_version"] = " SPORTTERY_MANUAL_ARCHIVE_V1 "
    document.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SportteryManualArchiveError):
        SportteryManualArchiveProvider(document, _resolver())


def test_match_number_mapping_is_date_qualified_while_display_number_is_not(
    tmp_path: Path,
) -> None:
    _write_document(
        tmp_path,
        filename="day-one.json",
        snapshot_id="manual-day-one",
        sporttery_match_no="SAME001",
        match_number_date="2026-09-01",
    )
    _write_document(
        tmp_path,
        filename="day-two.json",
        snapshot_id="manual-day-two",
        sporttery_match_no="SAME001",
        match_number_date="2026-09-02",
    )
    resolver = MatchIdentityResolver(
        (),
        (),
        (),
        explicit_mappings={
            (
                SPORTTERY_MANUAL_PROVIDER_CODE,
                "sporttery_match",
                "2026-09-01:SAME001",
            ): "day-one-match",
            (
                SPORTTERY_MANUAL_PROVIDER_CODE,
                "sporttery_match",
                "2026-09-02:SAME001",
            ): "day-two-match",
        },
    )
    provider = SportteryManualArchiveProvider(tmp_path, resolver)

    batch = asyncio.run(
        provider.fetch_fixed_bonus(
            SnapshotQuery(
                match_ids=("day-one-match", "day-two-match"),
                as_of_at_utc=REVIEWED,
            )
        )
    )

    assert tuple(item.sporttery_match_no for item in batch.snapshots) == (
        "SAME001",
        "SAME001",
    )
    assert tuple(item.external_match_id for item in batch.mappings) == (
        "2026-09-01:SAME001",
        "2026-09-02:SAME001",
    )
    assert len({item.mapping_id for item in batch.mappings}) == 2


def test_review_level_and_provenance_are_bound_into_source_identity(
    tmp_path: Path,
) -> None:
    self_reviewed = _write_document(
        tmp_path,
        filename="self.json",
        review_level="SELF_REVIEWED",
        reviewed_by="synthetic-enterer",
    )
    independent = _write_document(tmp_path, filename="independent.json")
    self_provider = SportteryManualArchiveProvider(self_reviewed, _resolver())
    independent_provider = SportteryManualArchiveProvider(independent, _resolver())
    self_snapshot = asyncio.run(self_provider.fetch_fixed_bonus(_query())).snapshots[0]
    independent_snapshot = asyncio.run(
        independent_provider.fetch_fixed_bonus(_query())
    ).snapshots[0]

    assert self_snapshot.source_snapshot_key != independent_snapshot.source_snapshot_key
    assert self_snapshot.snapshot_id != independent_snapshot.snapshot_id

    data = json.loads(independent.read_text(encoding="utf-8"))
    data["source_reference"] = "synthetic://changed-provenance"
    independent.write_text(json.dumps(data), encoding="utf-8")
    changed_provider = SportteryManualArchiveProvider(independent, _resolver())
    changed_snapshot = asyncio.run(
        changed_provider.fetch_fixed_bonus(_query())
    ).snapshots[0]

    assert (
        changed_snapshot.source_snapshot_key != independent_snapshot.source_snapshot_key
    )


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
    assert batch.mappings[0].available_at_utc == REVIEWED


def test_manual_archive_rejects_ambiguous_legacy_identity_streams(
    tmp_path: Path,
) -> None:
    first = _write_document(
        tmp_path,
        filename="first.json",
        snapshot_id="legacy-one",
        sporttery_match_no="LEGACY001",
        match_number_date="2026-09-01",
    )
    second = _write_document(
        tmp_path,
        filename="second.json",
        snapshot_id="legacy-two",
        sporttery_match_no="LEGACY001",
        match_number_date="2026-09-02",
    )
    for document in (first, second):
        data = json.loads(document.read_text(encoding="utf-8"))
        data["schema_version"] = "SPORTTERY_MANUAL_ARCHIVE_V1"
        data.pop("review_level")
        document.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SportteryManualArchiveError, match="reused across dates"):
        SportteryManualArchiveProvider(tmp_path, _resolver())


def test_manual_archive_does_not_fuzzy_resolve_identity(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    with pytest.raises(UnresolvedMatchMappingError):
        SportteryManualArchiveProvider(
            document,
            MatchIdentityResolver((), (), ()),
        )
