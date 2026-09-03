from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.http.provider_client import (
    HttpRequest,
    HttpResponse,
)
from football_system.interfaces.cli import _ingest_live_fixtures, main

ROOT = Path(__file__).resolve().parents[2]
LIVE_CONFIG = ROOT / "config" / "live.toml"
MOCK_CONFIG = ROOT / "config" / "mvp.toml"
PAYLOAD = (
    ROOT / "tests" / "fixtures" / "providers" / "synthetic_sportmonks" / "fixtures.json"
).read_bytes()
TOKEN = "synthetic-live-cli-secret"
RECEIVED = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
KICKOFF_FROM = "2026-09-03T00:00:00Z"
KICKOFF_TO = "2026-09-03T23:59:59Z"


class ScriptedTransport:
    def __init__(self, outcomes: list[HttpResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest, *, timeout_seconds: float) -> HttpResponse:
        del timeout_seconds
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ForbiddenEnvironment(dict[str, str]):
    def get(self, key: str, default: str | None = None) -> str | None:
        del key, default
        raise AssertionError("environment must not be read before runtime preflight")


def test_live_help_is_discoverable_and_has_no_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["live", "--help"]) == 0
    assert "ingest-fixtures" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(["live", "ingest-fixtures", "--help"])
    assert error.value.code == 0
    assert "--kickoff-from" in capsys.readouterr().out
    assert not tuple(tmp_path.iterdir())

    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    assert "live ingest-fixtures" in " ".join(capsys.readouterr().out.split())


def test_live_runtime_preflight_precedes_secret_raw_and_database_io(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "must-not-exist.db"
    raw_path = tmp_path / "must-not-exist-raw"

    with pytest.raises(SystemExit) as error:
        _ingest_live_fixtures(
            _arguments(database_path, raw_path, config=MOCK_CONFIG),
            transport=ScriptedTransport([]),
            environ=ForbiddenEnvironment(),
            clock=lambda: RECEIVED,
        )

    assert error.value.code == 2
    assert not database_path.exists()
    assert not raw_path.exists()


def test_live_missing_secret_has_no_raw_or_database_side_effects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "must-not-exist.db"
    raw_path = tmp_path / "must-not-exist-raw"

    with pytest.raises(SystemExit) as error:
        _ingest_live_fixtures(
            _arguments(database_path, raw_path),
            transport=ScriptedTransport([]),
            environ={},
            clock=lambda: RECEIVED,
        )

    assert error.value.code == 2
    assert "SPORTMONKS_KEY is required" in capsys.readouterr().err
    assert not database_path.exists()
    assert not raw_path.exists()


def test_live_fixture_ingestion_archives_and_persists_scripted_capture(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "nested" / "live.db"
    raw_path = tmp_path / "raw"
    transport = ScriptedTransport(
        [HttpResponse(200, PAYLOAD, {"x-request-id": "synthetic-request-1"})]
    )

    result = _ingest_live_fixtures(
        _arguments(database_path, raw_path),
        transport=transport,
        environ={"SPORTMONKS_KEY": TOKEN},
        clock=lambda: RECEIVED,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "inserted=true" in output
    assert "competitions=1; teams=2; matches=1; observations=1" in output
    assert TOKEN not in output
    assert len(transport.requests) == 1
    assert (
        transport.requests[0].url
        == "https://api.sportmonks.com/v3/football/fixtures/between/"
        "2026-09-03/2026-09-03?"
        "include=participants%3Bleague.country%3Bseason%3Bstate&per_page=50&"
        "filters=fixtureLeagues%3Asynthetic-league-001&locale=en"
    )
    assert transport.requests[0].headers["Authorization"] == TOKEN
    assert TOKEN not in transport.requests[0].url

    metadata_files = tuple(raw_path.glob("SPORTMONKS/**/*.metadata.json"))
    payload_files = tuple(raw_path.glob("SPORTMONKS/**/*.raw"))
    assert len(metadata_files) == len(payload_files) == 1
    assert payload_files[0].read_bytes() == PAYLOAD
    assert TOKEN not in metadata_files[0].read_text(encoding="utf-8")

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    sessions = create_session_factory(engine)
    expected_counts = {
        "fixture_ingestion_captures": 1,
        "fixture_observations": 1,
        "provider_team_aliases": 2,
        "provider_competition_mappings": 1,
        "canonical_match_identities": 1,
        "provider_match_mappings": 1,
    }
    with engine.connect() as connection:
        assert {
            table: connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in expected_counts
        } == expected_counts
    catalog = SqlAlchemyMatchIdentityRepository(sessions).load_catalog(
        as_of_at_utc=RECEIVED,
        kickoff_from_utc=datetime(2026, 9, 3, tzinfo=timezone.utc),
        kickoff_to_utc=datetime(2026, 9, 4, tzinfo=timezone.utc),
        provider_codes=("SPORTMONKS",),
    )
    assert len(catalog.canonical_matches) == 1
    assert len(catalog.explicit_mappings) == 1
    assert catalog.explicit_mappings[0].external_match_id == "synthetic-fixture-001"

    with sqlite3.connect(database_path) as connection:
        assert TOKEN not in "\n".join(connection.iterdump())


def test_malformed_success_is_archived_without_opening_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "must-not-exist.db"
    raw_path = tmp_path / "raw"

    with pytest.raises(SystemExit) as error:
        _ingest_live_fixtures(
            _arguments(database_path, raw_path),
            transport=ScriptedTransport([HttpResponse(200, b'{"data":{}}')]),
            environ={"SPORTMONKS_KEY": TOKEN},
            clock=lambda: RECEIVED,
        )

    assert error.value.code == 2
    assert not database_path.exists()
    assert len(tuple(raw_path.glob("SPORTMONKS/**/*.raw"))) == 1
    assert TOKEN not in capsys.readouterr().err


def _arguments(
    database_path: Path,
    raw_path: Path,
    *,
    config: Path = LIVE_CONFIG,
) -> list[str]:
    return [
        "--config",
        str(config),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
        "--raw-archive",
        str(raw_path),
        "--kickoff-from",
        KICKOFF_FROM,
        "--kickoff-to",
        KICKOFF_TO,
        "--season",
        "2026/27",
        "--league-id",
        "synthetic-league-001",
        "--provider-season-id",
        "synthetic-season-2026",
        "--competition-type",
        "LEAGUE",
        "--team-type",
        "CLUB",
    ]
