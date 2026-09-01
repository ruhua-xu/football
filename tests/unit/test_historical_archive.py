import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    FixtureArchiveRecord,
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
    archive_payload_sha256,
    canonical_payload_sha256,
    match_result_payload_sha256,
)
from football_system.domain.market import MarketKey, MarketType, SelectionKey
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    Match,
    OddsQuote,
    Team,
)
from football_system.infrastructure.providers.historical_archive import (
    ArchiveValidationError,
    load_historical_archive,
)

UTC = timezone.utc
SOURCE_TIME = datetime(2024, 8, 1, 10, 0, tzinfo=UTC)
CREATED = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _fixture_payload(*, available_at: datetime = SOURCE_TIME) -> dict[str, object]:
    competition = Competition(
        competition_id="competition-1",
        canonical_key="competition:test",
        name="Test League",
        country_code="GB",
    )
    home = Team(team_id="team-home", canonical_key="team:home", name="Home")
    away = Team(team_id="team-away", canonical_key="team:away", name="Away")
    match = Match(
        match_id="match-1",
        competition_id=competition.competition_id,
        home_team_id=home.team_id,
        away_team_id=away.team_id,
        kickoff_at_utc=SOURCE_TIME + timedelta(days=1),
        available_at_utc=available_at,
    )
    return {
        "competition": competition.model_dump(mode="json"),
        "home_team": home.model_dump(mode="json"),
        "away_team": away.model_dump(mode="json"),
        "match": match.model_dump(mode="json"),
    }


def _market_payload(*, payload_hash: str | None = None) -> dict[str, object]:
    odds = {"home_win": "2.10", "draw": "3.20", "away_win": "3.40"}
    snapshot = MarketOddsSnapshot(
        snapshot_id="odds-1",
        match_id="match-1",
        provider_code="MARKET_TEST",
        bookmaker_code="CONSENSUS",
        market=MarketKey(market_type=MarketType.THREE_WAY),
        quotes=(
            OddsQuote(selection=SelectionKey.HOME_WIN, odds=Decimal("2.10")),
            OddsQuote(selection=SelectionKey.DRAW, odds=Decimal("3.20")),
            OddsQuote(selection=SelectionKey.AWAY_WIN, odds=Decimal("3.40")),
        ),
        captured_at_utc=SOURCE_TIME,
        available_at_utc=SOURCE_TIME + timedelta(minutes=1),
        ingested_at_utc=SOURCE_TIME + timedelta(minutes=2),
        source_snapshot_key="market-source-1",
        payload_hash=payload_hash or canonical_payload_sha256(odds),
    )
    return snapshot.model_dump(mode="json")


def _record(
    payload: dict[str, object],
    *,
    retrospective: bool = False,
    imported_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "retrospective": retrospective,
        "imported_at_utc": imported_at.isoformat() if imported_at else None,
        "payload": payload,
    }


def _document(
    kind: HistoricalArchiveDatasetKind,
    provider_code: str,
    records: list[dict[str, object]],
    *,
    data_mode: HistoricalDataMode = HistoricalDataMode.LIVE_STRICT,
    archive_id: str = "archive-1",
) -> dict[str, object]:
    return {
        "manifest": {
            "archive_schema_version": HISTORICAL_ARCHIVE_SCHEMA_VERSION,
            "archive_id": archive_id,
            "provider_code": provider_code,
            "dataset_kind": kind.value,
            "created_at_utc": CREATED.isoformat(),
            "source_reference": "test://historical-archive",
            "source_description": "Deterministic test archive",
            "license_note": "TEST_ONLY",
            "data_mode": data_mode.value,
            "payload_sha256": archive_payload_sha256(records),
            "record_count": len(records),
        },
        "records": records,
    }


def _write(path: Path, document: dict[str, object]) -> Path:
    path.write_text(
        json.dumps(document, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )
    return path


def test_loads_versioned_utf8_archive_and_validates_all_records(
    tmp_path: Path,
) -> None:
    record = _record(_fixture_payload())
    path = _write(
        tmp_path / "fixtures.json",
        _document(
            HistoricalArchiveDatasetKind.FIXTURES,
            "FIXTURE_TEST",
            [record],
        ),
    )

    loaded = load_historical_archive(path)

    assert loaded.manifest.archive_schema_version == HISTORICAL_ARCHIVE_SCHEMA_VERSION
    assert loaded.manifest.record_count == 1
    assert isinstance(loaded.records[0], FixtureArchiveRecord)
    assert loaded.records[0].payload.match.match_id == "match-1"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("checksum", "checksum mismatch"),
        ("schema", "archive_schema_version"),
        ("naive_time", "timezone-aware"),
    ),
)
def test_rejects_wrong_checksum_schema_and_naive_times(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    records = [_record(_fixture_payload())]
    document = _document(
        HistoricalArchiveDatasetKind.FIXTURES,
        "FIXTURE_TEST",
        records,
    )
    manifest = document["manifest"]
    assert isinstance(manifest, dict)
    if mutation == "checksum":
        manifest["payload_sha256"] = "0" * 64
    elif mutation == "schema":
        manifest["archive_schema_version"] = "HISTORICAL_ARCHIVE_V0"
    else:
        payload = records[0]["payload"]
        assert isinstance(payload, dict)
        match = payload["match"]
        assert isinstance(match, dict)
        match["available_at_utc"] = "2024-08-01T10:00:00"
        manifest["payload_sha256"] = archive_payload_sha256(records)

    with pytest.raises(ArchiveValidationError, match=message):
        load_historical_archive(_write(tmp_path / f"{mutation}.json", document))


@pytest.mark.parametrize("constant", (float("nan"), float("inf"), -float("inf")))
def test_rejects_non_finite_json_numbers(tmp_path: Path, constant: float) -> None:
    records = [
        _record(
            {
                "mapping_id": "mapping-1",
                "provider_code": "FIXTURE_TEST",
                "external_namespace": "fixture",
                "external_match_id": "external-1",
                "internal_match_id": "match-1",
                "confidence": constant,
                "available_at_utc": SOURCE_TIME.isoformat(),
            }
        )
    ]
    document = _document(
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        "FIXTURE_TEST",
        [],
    )
    document["records"] = records

    with pytest.raises(ArchiveValidationError, match="not finite"):
        load_historical_archive(_write(tmp_path / "non-finite.json", document))


def test_rejects_duplicate_business_versions_and_bad_canonical_hash(
    tmp_path: Path,
) -> None:
    fixture = _record(_fixture_payload())
    duplicate_path = _write(
        tmp_path / "duplicate.json",
        _document(
            HistoricalArchiveDatasetKind.FIXTURES,
            "FIXTURE_TEST",
            [fixture, fixture],
        ),
    )
    bad_hash_path = _write(
        tmp_path / "bad-hash.json",
        _document(
            HistoricalArchiveDatasetKind.MARKET_ODDS,
            "MARKET_TEST",
            [_record(_market_payload(payload_hash="0" * 64))],
            archive_id="archive-market",
        ),
    )

    with pytest.raises(ArchiveValidationError, match="duplicate fixture version"):
        load_historical_archive(duplicate_path)
    with pytest.raises(ArchiveValidationError, match="payload hash"):
        load_historical_archive(bad_hash_path)


def test_rejects_match_result_score_encoded_as_json_string(tmp_path: Path) -> None:
    records = [
        _record(
            {
                "match_result_id": "result-1",
                "match_id": "match-1",
                "provider_code": "RESULT_TEST",
                "home_goals": "2",
                "away_goals": 1,
                "observed_at_utc": SOURCE_TIME.isoformat(),
                "available_at_utc": (SOURCE_TIME + timedelta(minutes=1)).isoformat(),
                "ingested_at_utc": (SOURCE_TIME + timedelta(minutes=2)).isoformat(),
                "source_result_key": "source-result-1",
                "payload_hash": match_result_payload_sha256(2, 1),
                "supersedes_match_result_id": None,
            }
        )
    ]
    document = _document(
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        "RESULT_TEST",
        records,
    )

    with pytest.raises(ArchiveValidationError, match="valid integer"):
        load_historical_archive(_write(tmp_path / "string-score.json", document))


def test_research_mode_requires_explicit_retrospective_import_provenance(
    tmp_path: Path,
) -> None:
    valid_record = _record(
        _fixture_payload(), retrospective=True, imported_at=CREATED - timedelta(hours=1)
    )
    valid = _document(
        HistoricalArchiveDatasetKind.FIXTURES,
        "FIXTURE_TEST",
        [valid_record],
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )
    loaded = load_historical_archive(_write(tmp_path / "research.json", valid))
    round_tripped = load_historical_archive(
        _write(
            tmp_path / "research-round-trip.json",
            loaded.document.model_dump(mode="json"),
        )
    )

    assert loaded.records[0].retrospective is True
    assert round_tripped.document == loaded.document
    assert loaded.manifest.data_mode.report_label == (
        "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
    )

    invalid_record = _record(
        _fixture_payload(), retrospective=False, imported_at=CREATED
    )
    invalid = _document(
        HistoricalArchiveDatasetKind.FIXTURES,
        "FIXTURE_TEST",
        [invalid_record],
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        archive_id="archive-invalid-research",
    )
    with pytest.raises(ArchiveValidationError, match="retrospective marker"):
        load_historical_archive(_write(tmp_path / "invalid-research.json", invalid))


def test_research_import_must_be_later_than_source_time(tmp_path: Path) -> None:
    record = _record(
        _fixture_payload(),
        retrospective=True,
        imported_at=SOURCE_TIME - timedelta(seconds=1),
    )
    document = _document(
        HistoricalArchiveDatasetKind.FIXTURES,
        "FIXTURE_TEST",
        [record],
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )

    with pytest.raises(ArchiveValidationError, match="not retrospectively imported"):
        load_historical_archive(_write(tmp_path / "early-import.json", document))
