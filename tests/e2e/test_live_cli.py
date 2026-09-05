from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from football_system.application.daily_slate import PlanSportteryDailySlateService
from football_system.application.live_sources import (
    IdentityReviewDocument,
    ReviewedIdentityMapping,
)
from football_system.domain.archive import canonical_json
from football_system.domain.match import ProviderMatchMapping
from football_system.infrastructure.database.identity_repositories import (
    SqlAlchemyMatchIdentityRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from football_system.infrastructure.files.daily_slate import load_sporttery_daily_slate
from football_system.infrastructure.files.review_bridge import write_contract_file
from football_system.infrastructure.http.provider_client import (
    HttpRequest,
    HttpResponse,
)
from football_system.interfaces.cli import (
    _import_live_identity_review,
    _ingest_live_fixtures,
    _ingest_live_market_odds,
    _ingest_live_sporttery,
    _plan_live_slate,
    _prepare_live_analysis,
    _reconcile_live_sources,
    _run_live_analysis,
    main,
)

ROOT = Path(__file__).resolve().parents[2]
LIVE_CONFIG = ROOT / "config" / "live.toml"
MOCK_CONFIG = ROOT / "config" / "mvp.toml"
PAYLOAD = (
    ROOT / "tests" / "fixtures" / "providers" / "synthetic_sportmonks" / "fixtures.json"
).read_bytes()
ODDS_PAYLOAD = (
    ROOT
    / "tests"
    / "fixtures"
    / "providers"
    / "synthetic_the_odds_api"
    / "current.json"
).read_bytes()
SPORTTERY_ARCHIVE = (
    ROOT / "tests" / "fixtures" / "providers" / "synthetic_sporttery" / "manual.json"
)
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
    live_help = capsys.readouterr().out
    assert {
        "plan-slate",
        "ingest-fixtures",
        "ingest-market-odds",
        "ingest-sporttery",
        "reconcile",
        "import-identity-review",
        "prepare-analysis",
    } <= set(live_help.replace("{", "").replace("}", "").replace(",", " ").split())
    with pytest.raises(SystemExit) as error:
        main(["live", "ingest-fixtures", "--help"])
    assert error.value.code == 0
    assert "--kickoff-from" in capsys.readouterr().out
    assert not tuple(tmp_path.iterdir())

    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    assert "live ingest-fixtures" in " ".join(capsys.readouterr().out.split())


def test_live_plan_slate_is_append_only_and_not_an_analysis_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "live.db"
    plan_path = tmp_path / "daily-slate-plan.json"
    arguments = [
        "--config",
        str(LIVE_CONFIG),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
        "--input",
        str(SPORTTERY_ARCHIVE),
        "--as-of",
        RECEIVED.isoformat(),
        "--output",
        str(plan_path),
    ]

    assert _plan_live_slate(arguments, clock=lambda: RECEIVED) == 0
    first_bytes = plan_path.read_bytes()
    plan = json.loads(first_bytes)
    assert plan["analysis_status"] == "NO_ANALYSIS"
    assert plan["candidates"][0]["statuses"] == [
        "IDENTITY_UNRESOLVED",
        "FIXTURE_SOURCE_REQUIRED",
        "SPORTTERY_SP_READY",
    ]
    capsys.readouterr()

    assert _plan_live_slate(arguments, clock=lambda: RECEIVED) == 0
    assert plan_path.read_bytes() == first_bytes
    capsys.readouterr()

    changed = list(arguments)
    changed[changed.index("--as-of") + 1] = (
        RECEIVED + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(SystemExit) as overwrite_error:
        _plan_live_slate(changed, clock=lambda: RECEIVED + timedelta(seconds=1))
    assert overwrite_error.value.code == 2
    assert "refusing to overwrite different file" in capsys.readouterr().err

    with pytest.raises(SystemExit) as analysis_error:
        _run_live_analysis(
            [
                "--config",
                str(LIVE_CONFIG),
                "--database-url",
                f"sqlite:///{database_path.as_posix()}",
                "--preparation-id",
                plan["plan_id"],
                "--budget",
                "100",
                "--analysis-run-id",
                "must-not-run",
            ],
            clock=lambda: RECEIVED + timedelta(minutes=1),
        )
    assert analysis_error.value.code == 2
    assert "unknown live analysis preparation" in capsys.readouterr().err

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM analysis_runs")) == 0


def test_live_market_odds_accepts_exact_targets_from_daily_slate_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "live.db"
    fixture_raw_path = tmp_path / "fixture-raw"
    odds_raw_path = tmp_path / "odds-raw"
    assert (
        _ingest_live_fixtures(
            _arguments(database_path, fixture_raw_path),
            transport=ScriptedTransport([HttpResponse(200, PAYLOAD)]),
            environ={"SPORTMONKS_KEY": TOKEN},
            clock=lambda: RECEIVED,
        )
        == 0
    )
    capsys.readouterr()

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    sessions = create_session_factory(engine)
    catalog = SqlAlchemyMatchIdentityRepository(sessions).load_catalog(
        as_of_at_utc=RECEIVED,
        kickoff_from_utc=datetime.fromisoformat(KICKOFF_FROM.replace("Z", "+00:00")),
        kickoff_to_utc=datetime.fromisoformat(KICKOFF_TO.replace("Z", "+00:00")),
    )
    match_id = catalog.canonical_matches[0].internal_match_id
    slate = load_sporttery_daily_slate(SPORTTERY_ARCHIVE)
    planning_catalog = catalog.model_copy(
        update={
            "explicit_mappings": (
                *catalog.explicit_mappings,
                ProviderMatchMapping(
                    mapping_id="synthetic-planning-mapping",
                    provider_code="SPORTTERY_MANUAL",
                    external_namespace="sporttery_match",
                    external_match_id="2026-09-01:SYN001",
                    internal_match_id=match_id,
                    resolution_method="REVIEWED_EXPLICIT",
                    confidence=Decimal("1"),
                    available_at_utc=RECEIVED,
                ),
            )
        }
    )
    plan = PlanSportteryDailySlateService().plan(
        slate,
        planning_catalog,
        planned_at_utc=RECEIVED,
    )
    assert plan.capture_plan.market_odds_requests[0].canonical_match_ids == (match_id,)
    plan_path = tmp_path / "daily-slate-plan.json"
    write_contract_file(plan_path, canonical_json(plan))

    transport = ScriptedTransport([HttpResponse(200, ODDS_PAYLOAD)])
    assert (
        _ingest_live_market_odds(
            _market_plan_arguments(database_path, odds_raw_path, plan_path),
            transport=transport,
            environ={"ODDS_API_KEY": TOKEN},
            clock=lambda: RECEIVED + timedelta(minutes=1),
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "snapshots=0" in output
    assert "issues=2" in output
    assert TOKEN not in output
    assert len(transport.requests) == 1


def test_live_market_preflight_precedes_secret_raw_and_database_io(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "must-not-exist.db"
    raw_path = tmp_path / "must-not-exist-raw"

    with pytest.raises(SystemExit) as error:
        _ingest_live_market_odds(
            _market_arguments(database_path, raw_path, config=MOCK_CONFIG),
            transport=ScriptedTransport([]),
            environ=ForbiddenEnvironment(),
            clock=lambda: RECEIVED,
        )

    assert error.value.code == 2
    assert "live commands require live runtime" in capsys.readouterr().err
    assert not database_path.exists()
    assert not raw_path.exists()

    with pytest.raises(SystemExit) as error:
        _ingest_live_market_odds(
            _market_arguments(database_path, raw_path),
            transport=ScriptedTransport([]),
            environ={},
            clock=lambda: RECEIVED,
        )

    assert error.value.code == 2
    assert "ODDS_API_KEY is required" in capsys.readouterr().err
    assert not database_path.exists()
    assert not raw_path.exists()


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


def test_live_source_cli_reconciles_reviews_and_prepares_persisted_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "live.db"
    fixture_raw_path = tmp_path / "fixture-raw"
    odds_raw_path = tmp_path / "odds-raw"

    def forbid_default_network() -> None:
        raise AssertionError("offline live commands must not construct a transport")

    monkeypatch.setattr(
        "football_system.interfaces.cli.UrllibTransport",
        forbid_default_network,
    )
    assert (
        _ingest_live_fixtures(
            _arguments(database_path, fixture_raw_path),
            transport=ScriptedTransport([HttpResponse(200, PAYLOAD)]),
            environ={"SPORTMONKS_KEY": TOKEN},
            clock=lambda: RECEIVED,
        )
        == 0
    )
    capsys.readouterr()

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    sessions = create_session_factory(engine)
    catalog = SqlAlchemyMatchIdentityRepository(sessions).load_catalog(
        as_of_at_utc=RECEIVED,
        kickoff_from_utc=datetime(2026, 9, 3, tzinfo=timezone.utc),
        kickoff_to_utc=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    canonical_match = catalog.canonical_matches[0]
    match_id = canonical_match.internal_match_id
    competition_id = canonical_match.internal_competition_id

    first_market_at = RECEIVED + timedelta(minutes=1)
    assert (
        _ingest_live_market_odds(
            _market_arguments(database_path, odds_raw_path, match_id=match_id),
            transport=ScriptedTransport([HttpResponse(200, ODDS_PAYLOAD)]),
            environ={"ODDS_API_KEY": TOKEN},
            clock=lambda: first_market_at,
        )
        == 0
    )
    market_output = capsys.readouterr().out
    assert "snapshots=0" in market_output
    assert "issues=2" in market_output
    first_market_ingestion_id = _ingestion_id_from_output(market_output)

    market_report_path = tmp_path / "market-reconciliation.json"
    assert (
        _reconcile_live_sources(
            [
                "--config",
                str(LIVE_CONFIG),
                "--database-url",
                f"sqlite:///{database_path.as_posix()}",
                "--ingestion-id",
                first_market_ingestion_id,
                "--as-of",
                first_market_at.isoformat(),
                "--output",
                str(market_report_path),
            ],
            clock=lambda: first_market_at,
        )
        == 0
    )
    capsys.readouterr()
    market_report = json.loads(market_report_path.read_text(encoding="utf-8"))
    assert len(market_report["unresolved"]) == 1
    assert len(market_report["other_issues"]) == 1

    market_reviewed_at = first_market_at + timedelta(minutes=1)
    market_review_path = tmp_path / "market-identity-review.json"
    _write_identity_review(
        market_review_path,
        review_id="market-review-001",
        ingestion_id=first_market_ingestion_id,
        reviewed_at=market_reviewed_at,
        provider_code="THE_ODDS_API",
        external_namespace="event",
        external_match_id="synthetic-odds-event-001",
        internal_match_id=match_id,
    )
    assert (
        _import_live_identity_review(
            _review_arguments(database_path, market_review_path),
            clock=lambda: market_reviewed_at + timedelta(minutes=1),
        )
        == 0
    )
    assert "inserted=true; mappings=1" in capsys.readouterr().out

    second_market_at = first_market_at + timedelta(minutes=3)
    assert (
        _ingest_live_market_odds(
            _market_arguments(database_path, odds_raw_path, match_id=match_id),
            transport=ScriptedTransport([HttpResponse(200, ODDS_PAYLOAD)]),
            environ={"ODDS_API_KEY": TOKEN},
            clock=lambda: second_market_at,
        )
        == 0
    )
    second_market_output = capsys.readouterr().out
    assert "snapshots=2" in second_market_output
    assert "issues=0" in second_market_output
    assert "consensus=1" in second_market_output

    first_sporttery_at = second_market_at + timedelta(minutes=1)
    assert (
        _ingest_live_sporttery(
            _sporttery_arguments(database_path),
            clock=lambda: first_sporttery_at,
        )
        == 0
    )
    sporttery_output = capsys.readouterr().out
    assert "snapshots=0" in sporttery_output
    assert "issues=1" in sporttery_output
    first_sporttery_ingestion_id = _ingestion_id_from_output(sporttery_output)

    sporttery_reviewed_at = first_sporttery_at + timedelta(minutes=1)
    sporttery_review_path = tmp_path / "sporttery-identity-review.json"
    _write_identity_review(
        sporttery_review_path,
        review_id="sporttery-review-001",
        ingestion_id=first_sporttery_ingestion_id,
        reviewed_at=sporttery_reviewed_at,
        provider_code="SPORTTERY_MANUAL",
        external_namespace="sporttery_match",
        external_match_id="2026-09-01:SYN001",
        internal_match_id=match_id,
    )
    assert (
        _import_live_identity_review(
            _review_arguments(database_path, sporttery_review_path),
            clock=lambda: sporttery_reviewed_at + timedelta(minutes=1),
        )
        == 0
    )
    capsys.readouterr()

    second_sporttery_at = first_sporttery_at + timedelta(minutes=3)
    assert (
        _ingest_live_sporttery(
            _sporttery_arguments(database_path),
            clock=lambda: second_sporttery_at,
        )
        == 0
    )
    second_sporttery_output = capsys.readouterr().out
    assert "snapshots=1" in second_sporttery_output
    assert "issues=0" in second_sporttery_output

    decision_at = second_sporttery_at + timedelta(minutes=1)
    preparation_path = tmp_path / "analysis-preparation.json"
    assert (
        _prepare_live_analysis(
            [
                "--config",
                str(LIVE_CONFIG),
                "--database-url",
                f"sqlite:///{database_path.as_posix()}",
                "--decision-as-of",
                decision_at.isoformat(),
                "--kickoff-from",
                KICKOFF_FROM,
                "--kickoff-to",
                KICKOFF_TO,
                "--competition-id",
                competition_id,
                "--season-id",
                "2026/27",
                "--expected-match-id",
                match_id,
                "--maximum-odds-age-seconds",
                "200000",
                "--minimum-bookmaker-count",
                "2",
                "--output",
                str(preparation_path),
            ],
            clock=lambda: decision_at + timedelta(minutes=1),
        )
        == 0
    )
    capsys.readouterr()
    preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
    assert preparation["status"] == "ANALYSIS_INPUT_READY"
    assert preparation["expected_match_ids"] == [match_id]
    assert preparation["matches"][0]["bookmaker_count"] == 2
    assert preparation["matches"][0]["reason_codes"] == []

    analysis_at = decision_at + timedelta(minutes=2)
    run_arguments = [
        "--config",
        str(LIVE_CONFIG),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
        "--date",
        "2026-09-03",
        "--budget",
        "100",
        "200",
        "--analysis-run-id",
        "live-analysis-001",
    ]
    assert _run_live_analysis(run_arguments, clock=lambda: analysis_at) == 0
    analysis_output = capsys.readouterr().out
    assert f"Live source preparation: {preparation['preparation_id']}" in analysis_output
    assert "P_quant: UNAVAILABLE (INSUFFICIENT_PRIOR_MATCHES)" in analysis_output
    assert "P_final: UNAVAILABLE (MODEL_UNAVAILABLE)" in analysis_output

    retry_arguments = list(run_arguments)
    retry_arguments[retry_arguments.index("--date") : retry_arguments.index("--date") + 2] = [
        "--preparation-id",
        preparation["preparation_id"],
    ]
    assert _run_live_analysis(retry_arguments, clock=lambda: analysis_at) == 0
    capsys.readouterr()

    packet_path = tmp_path / "live-analysis-packet-v3.json"
    assert (
        main(
            [
                "analysis-packet",
                "export",
                "--config",
                str(LIVE_CONFIG),
                "--database-url",
                f"sqlite:///{database_path.as_posix()}",
                "--analysis-run-id",
                "live-analysis-001",
                "--schema-version",
                "ANALYSIS_PACKET_V3",
                "--output",
                str(packet_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["schema_version"] == "ANALYSIS_PACKET_V3"
    assert packet["matches"][0]["p_quant"]["status"] == "UNAVAILABLE"
    assert packet["matches"][0]["p_quant"]["prediction"] is None

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM live_source_ingestions")) == 4
        assert connection.scalar(text("SELECT COUNT(*) FROM live_identity_reviews")) == 2
        assert connection.scalar(text("SELECT COUNT(*) FROM live_analysis_preparations")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM analysis_runs")) == 1
        assert connection.scalar(
            text("SELECT COUNT(*) FROM live_analysis_run_preparations")
        ) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM quant_model_evaluations")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM quant_predictions")) == 0


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


def _market_arguments(
    database_path: Path,
    raw_path: Path,
    *,
    config: Path = LIVE_CONFIG,
    match_id: str = "synthetic-match",
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
        "--match-id",
        match_id,
        "--sport-key",
        "soccer_synthetic_coastal",
        "--season",
        "2026/27",
        "--competition-type",
        "LEAGUE",
    ]


def _market_plan_arguments(
    database_path: Path,
    raw_path: Path,
    plan_path: Path,
) -> list[str]:
    return [
        "--config",
        str(LIVE_CONFIG),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
        "--raw-archive",
        str(raw_path),
        "--plan",
        str(plan_path),
        "--sport-key",
        "soccer_synthetic_coastal",
        "--season",
        "2026/27",
        "--competition-type",
        "LEAGUE",
    ]


def _sporttery_arguments(database_path: Path) -> list[str]:
    return [
        "--config",
        str(LIVE_CONFIG),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
        "--archive",
        str(SPORTTERY_ARCHIVE),
        "--kickoff-from",
        KICKOFF_FROM,
        "--kickoff-to",
        KICKOFF_TO,
    ]


def _review_arguments(database_path: Path, review_path: Path) -> list[str]:
    return [
        "--config",
        str(LIVE_CONFIG),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
        "--review",
        str(review_path),
    ]


def _write_identity_review(
    path: Path,
    *,
    review_id: str,
    ingestion_id: str,
    reviewed_at: datetime,
    provider_code: str,
    external_namespace: str,
    external_match_id: str,
    internal_match_id: str,
) -> None:
    review = IdentityReviewDocument(
        review_id=review_id,
        source_ingestion_id=ingestion_id,
        reviewed_by="synthetic-operator",
        reviewed_at_utc=reviewed_at,
        mappings=(
            ReviewedIdentityMapping(
                provider_code=provider_code,
                external_namespace=external_namespace,
                external_match_id=external_match_id,
                internal_match_id=internal_match_id,
            ),
        ),
    )
    path.write_text(canonical_json(review) + "\n", encoding="utf-8")


def _ingestion_id_from_output(output: str) -> str:
    return output.split(" ingestion: ", maxsplit=1)[1].split(";", maxsplit=1)[0]
