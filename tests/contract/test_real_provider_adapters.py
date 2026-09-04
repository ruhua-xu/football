import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from football_system.application.environment import RuntimeEnvironment
from football_system.application.identity_catalog import FixtureIngestionRequest
from football_system.application.live_sources import (
    LiveMarketOddsIngestionService,
    LiveSourceIngestionStatus,
    LiveSourceKind,
    MarketOddsIngestionCapture,
    SourceIngestionSummary,
)
from football_system.application.ports.data_providers import (
    FixtureQuery,
    MarketOddsProvider,
    MarketOddsReconciliationIssueReason,
    SnapshotQuery,
)
from football_system.domain.archive import HistoricalDataMode
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
    MatchIdentityResolver,
    TeamIdentity,
)
from football_system.domain.raw_data import ProviderRequestFailureCode
from football_system.domain.match import TeamType
from football_system.infrastructure.files.raw_archive import RawDataArchive
from football_system.infrastructure.http.provider_client import (
    HttpRequest,
    HttpResponse,
    ProviderHttpClient,
)
from football_system.infrastructure.providers.real._common import (
    ProviderPayloadError,
    ProviderRequestError,
)
from football_system.infrastructure.providers.real.sportmonks import (
    SportmonksFixtureProvider,
)
from football_system.infrastructure.providers.real.the_odds_api import (
    TheOddsApiHistoricalMarketOddsImporter,
    TheOddsApiMarketOddsProvider,
    write_the_odds_api_historical_archives,
)
from football_system.infrastructure.providers.historical_archive import (
    HistoricalArchiveMarketOddsProvider,
    LocalArchiveStore,
    MissingArchiveInputError,
)

UTC = timezone.utc
RECEIVED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 3, 19, 0, tzinfo=UTC)
TOKEN = "synthetic-secret-token"
FIXTURES = Path("tests/fixtures/providers")


class ScriptedTransport:
    def __init__(self, outcomes: list[HttpResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest, *, timeout_seconds: float) -> HttpResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(
    provider_code: str,
    transport: ScriptedTransport,
    *,
    received_at: datetime = RECEIVED,
) -> ProviderHttpClient:
    return ProviderHttpClient(
        provider_code,
        "https://synthetic.provider.test",
        transport,
        max_retries=0,
        utc_now=lambda: received_at,
        monotonic=lambda: 1.0,
    )


def _fixture_query(*, cutoff: datetime = RECEIVED) -> FixtureQuery:
    return FixtureQuery(
        kickoff_from_utc=KICKOFF.replace(hour=0),
        kickoff_to_utc=KICKOFF.replace(hour=23),
        as_of_at_utc=cutoff,
    )


def _odds_resolver(
    *,
    match_ids: tuple[str, ...] = ("match-synthetic",),
    explicit_mappings: dict[tuple[str, str, str], str] | None = None,
) -> MatchIdentityResolver:
    home_name = "Fabricated Harbour FC"
    away_name = "Fabricated Orchard FC"
    return MatchIdentityResolver(
        team_identities=(
            TeamIdentity(
                internal_team_id="team-harbour",
                canonical_name=home_name,
                aliases=(
                    Alias(
                        provider_code="THE_ODDS_API",
                        provider_team_id=home_name,
                        provider_team_name=home_name,
                        language="en",
                    ),
                ),
            ),
            TeamIdentity(
                internal_team_id="team-orchard",
                canonical_name=away_name,
                aliases=(
                    Alias(
                        provider_code="THE_ODDS_API",
                        provider_team_id=away_name,
                        provider_team_name=away_name,
                        language="en",
                    ),
                ),
            ),
        ),
        competition_mappings=(
            CompetitionMapping(
                internal_competition_id="synthetic-coastal",
                provider_code="THE_ODDS_API",
                provider_competition_id="soccer_synthetic_coastal",
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
        explicit_mappings=explicit_mappings or (),
    )


def _odds_provider(
    transport: ScriptedTransport,
    archive: RawDataArchive,
    *,
    resolver: MatchIdentityResolver | None = None,
    received_at: datetime = RECEIVED,
) -> TheOddsApiMarketOddsProvider:
    return TheOddsApiMarketOddsProvider(
        _client("THE_ODDS_API", transport, received_at=received_at),
        archive,
        resolver or _odds_resolver(),
        TOKEN,
        sport_key="soccer_synthetic_coastal",
        season="2026/27",
        competition_type="LEAGUE",
    )


def _odds_importer(
    transport: ScriptedTransport,
    archive: RawDataArchive,
    *,
    resolver: MatchIdentityResolver | None = None,
    received_at: datetime = RECEIVED,
) -> TheOddsApiHistoricalMarketOddsImporter:
    identity_resolver = resolver or _odds_resolver()
    return TheOddsApiHistoricalMarketOddsImporter(
        _client("THE_ODDS_API", transport, received_at=received_at),
        archive,
        lambda _: identity_resolver,
        TOKEN,
        sport_key="soccer_synthetic_coastal",
        season="2026/27",
        competition_type="LEAGUE",
        historical_at_utc=datetime(2026, 9, 1, 11, 45, tzinfo=UTC),
    )


def test_sportmonks_normalizes_canonical_fixtures_archives_raw_and_honors_cutoff(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_sportmonks/fixtures.json").read_bytes()
    transport = ScriptedTransport([HttpResponse(200, payload)])
    provider = SportmonksFixtureProvider(
        _client("SPORTMONKS", transport),
        RawDataArchive(tmp_path / "raw"),
        TOKEN,
    )

    batch = asyncio.run(provider.fetch_fixtures(_fixture_query()))

    assert len(batch.matches) == len(batch.mappings) == 1
    assert batch.matches[0].available_at_utc == RECEIVED
    assert batch.matches[0].status.value == "SCHEDULED"
    assert batch.mappings[0].external_match_id == "synthetic-fixture-001"
    assert provider.last_raw_artifact is not None
    assert provider.last_raw_artifact.payload_path.read_bytes() == payload
    metadata = provider.last_raw_artifact.metadata_path.read_text(encoding="utf-8")
    assert TOKEN not in metadata
    assert TOKEN not in transport.requests[0].url
    assert transport.requests[0].headers["Authorization"] == TOKEN

    late_receipt = RECEIVED
    before_receipt = asyncio.run(
        SportmonksFixtureProvider(
            _client(
                "SPORTMONKS",
                ScriptedTransport([HttpResponse(200, payload)]),
                received_at=late_receipt,
            ),
            RawDataArchive(tmp_path / "second-raw"),
            TOKEN,
        ).fetch_fixtures(_fixture_query(cutoff=RECEIVED.replace(hour=11)))
    )
    assert before_receipt.matches == ()
    assert before_receipt.mappings == ()


def test_sportmonks_capture_preserves_identity_and_raw_lineage(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_sportmonks/fixtures.json").read_bytes()
    transport = ScriptedTransport([HttpResponse(200, payload)])
    client = ProviderHttpClient(
        "SPORTMONKS",
        "https://synthetic.provider.test/v3/football/",
        transport,
        max_retries=0,
        utc_now=lambda: RECEIVED,
        monotonic=lambda: 1.0,
    )
    provider = SportmonksFixtureProvider(
        client,
        RawDataArchive(tmp_path / "raw"),
        TOKEN,
    )
    request = FixtureIngestionRequest(
        kickoff_from_utc=KICKOFF.replace(hour=0),
        kickoff_to_utc=KICKOFF.replace(hour=23),
        provider_competition_id="synthetic-league-001",
        provider_season_id="synthetic-season-2026",
        season="2026/27",
        competition_type="LEAGUE",
        language="en",
        team_type=TeamType.CLUB,
    )

    capture = asyncio.run(provider.capture_fixtures(request))

    assert capture.request == request
    assert capture.provider_code == "SPORTMONKS"
    assert capture.raw_artifact_id == provider.last_raw_artifact.artifact_id
    assert len(capture.registration.competitions) == 1
    assert len(capture.registration.teams) == 2
    assert len(capture.registration.matches) == 1
    assert len(capture.registration.team_aliases) == 2
    assert len(capture.registration.competition_mappings) == 1
    assert len(capture.registration.canonical_matches) == 1
    assert len(capture.observations) == 1
    assert capture.observations[0].external_match_id == "synthetic-fixture-001"
    assert all(
        item.alias.provider_code == "SPORTMONKS"
        for item in capture.registration.team_aliases
    )
    assert (
        transport.requests[0].url
        == "https://synthetic.provider.test/v3/football/fixtures/between/"
        "2026-09-03/2026-09-03?"
        "include=participants%3Bleague.country%3Bseason%3Bstate&per_page=50&"
        "filters=fixtureLeagues%3Asynthetic-league-001&locale=en"
    )


def test_sportmonks_rejects_unsupported_status_and_malformed_payload_after_archiving(
    tmp_path: Path,
) -> None:
    raw = json.loads(
        (FIXTURES / "synthetic_sportmonks/fixtures.json").read_text(encoding="utf-8")
    )
    raw["data"][0]["state"] = {"short_name": "CANC"}
    transport = ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())])
    provider = SportmonksFixtureProvider(
        _client("SPORTMONKS", transport),
        RawDataArchive(tmp_path / "raw"),
        TOKEN,
    )

    with pytest.raises(ProviderPayloadError, match="unsupported"):
        asyncio.run(provider.fetch_fixtures(_fixture_query()))
    assert provider.last_raw_artifact is not None

    malformed = SportmonksFixtureProvider(
        _client("SPORTMONKS", ScriptedTransport([HttpResponse(200, b'{"data":{}}')])),
        RawDataArchive(tmp_path / "malformed-raw"),
        TOKEN,
    )
    with pytest.raises(ProviderPayloadError, match="data must be an array"):
        asyncio.run(malformed.fetch_fixtures(_fixture_query()))


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        ({"short_name": "FTP"}, "FINISHED"),
        (
            {"short_name": "POSTP", "developer_name": "POSTPONED"},
            "POSTPONED",
        ),
        ({"short_name": "CANCL"}, "CANCELLED"),
    ),
)
def test_sportmonks_maps_documented_terminal_states(
    tmp_path: Path,
    state: dict[str, str],
    expected: str,
) -> None:
    raw = json.loads(
        (FIXTURES / "synthetic_sportmonks/fixtures.json").read_text(encoding="utf-8")
    )
    raw["data"][0]["state"] = state
    provider = SportmonksFixtureProvider(
        _client(
            "SPORTMONKS",
            ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        ),
        RawDataArchive(tmp_path / expected),
        TOKEN,
    )

    batch = asyncio.run(provider.fetch_fixtures(_fixture_query()))

    assert batch.matches[0].status.value == expected


def test_sportmonks_rejects_in_progress_penalties_and_incomplete_pagination(
    tmp_path: Path,
) -> None:
    raw = json.loads(
        (FIXTURES / "synthetic_sportmonks/fixtures.json").read_text(encoding="utf-8")
    )
    raw["data"][0]["state"] = {"short_name": "PEN"}
    penalties = SportmonksFixtureProvider(
        _client(
            "SPORTMONKS",
            ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        ),
        RawDataArchive(tmp_path / "penalties"),
        TOKEN,
    )
    with pytest.raises(ProviderPayloadError, match="status is unsupported"):
        asyncio.run(penalties.fetch_fixtures(_fixture_query()))

    raw["data"][0]["state"] = {"short_name": "NS"}
    raw["pagination"].update({"has_more": True, "next_cursor": "synthetic-next-cursor"})
    paginated = SportmonksFixtureProvider(
        _client(
            "SPORTMONKS",
            ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        ),
        RawDataArchive(tmp_path / "paginated"),
        TOKEN,
    )
    with pytest.raises(ProviderPayloadError, match="requires another page"):
        asyncio.run(paginated.fetch_fixtures(_fixture_query()))
    assert paginated.last_raw_artifact is not None


@pytest.mark.parametrize(
    "pagination_update",
    (
        {"current_page": 2},
        {"next_cursor": "unexpected-terminal-cursor"},
        {"per_page": 49},
    ),
)
def test_sportmonks_rejects_inconsistent_terminal_pagination(
    tmp_path: Path,
    pagination_update: dict[str, object],
) -> None:
    raw = json.loads(
        (FIXTURES / "synthetic_sportmonks/fixtures.json").read_text(encoding="utf-8")
    )
    raw["pagination"].update(pagination_update)
    provider = SportmonksFixtureProvider(
        _client(
            "SPORTMONKS",
            ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        ),
        RawDataArchive(tmp_path / "pagination"),
        TOKEN,
    )

    with pytest.raises(ProviderPayloadError, match="pagination metadata"):
        asyncio.run(provider.fetch_fixtures(_fixture_query()))

    assert provider.last_raw_artifact is not None


def test_sportmonks_capture_rejects_mismatched_identity_scope_after_archiving(
    tmp_path: Path,
) -> None:
    raw = json.loads(
        (FIXTURES / "synthetic_sportmonks/fixtures.json").read_text(encoding="utf-8")
    )
    raw["data"][0]["season_id"] = "unexpected-season"
    provider = SportmonksFixtureProvider(
        _client(
            "SPORTMONKS",
            ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        ),
        RawDataArchive(tmp_path / "raw"),
        TOKEN,
    )
    request = FixtureIngestionRequest(
        kickoff_from_utc=KICKOFF.replace(hour=0),
        kickoff_to_utc=KICKOFF.replace(hour=23),
        provider_competition_id="synthetic-league-001",
        provider_season_id="synthetic-season-2026",
        season="2026/27",
        competition_type="LEAGUE",
        language="en",
        team_type=TeamType.CLUB,
    )

    with pytest.raises(ProviderPayloadError, match="league or season scope"):
        asyncio.run(provider.capture_fixtures(request))

    assert provider.last_raw_artifact is not None


@pytest.mark.parametrize(
    ("outcome", "failure"),
    (
        (TimeoutError(TOKEN), ProviderRequestFailureCode.TIMEOUT),
        (HttpResponse(429, b"limited"), ProviderRequestFailureCode.RATE_LIMITED),
    ),
)
def test_provider_failures_never_become_empty_data_or_echo_credentials(
    tmp_path: Path,
    outcome: HttpResponse | Exception,
    failure: ProviderRequestFailureCode,
) -> None:
    provider = SportmonksFixtureProvider(
        _client("SPORTMONKS", ScriptedTransport([outcome])),
        RawDataArchive(tmp_path / "raw"),
        TOKEN,
    )

    with pytest.raises(ProviderRequestError) as error:
        asyncio.run(provider.fetch_fixtures(_fixture_query()))

    assert error.value.failure_code is failure
    assert TOKEN not in str(error.value)


def test_the_odds_api_preserves_complete_bookmakers_and_archives_raw(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_the_odds_api/current.json").read_bytes()
    transport = ScriptedTransport([HttpResponse(200, payload)])
    provider = _odds_provider(transport, RawDataArchive(tmp_path / "raw"))

    batch = asyncio.run(
        provider.fetch_market_odds(
            SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
        )
    )

    assert [snapshot.bookmaker_code for snapshot in batch.snapshots] == [
        "fabricated_alpha",
        "fabricated_beta",
    ]
    assert len(batch.mappings) == 1
    assert batch.issues == ()
    assert all(snapshot.available_at_utc == RECEIVED for snapshot in batch.snapshots)
    assert all(snapshot.ingested_at_utc == RECEIVED for snapshot in batch.snapshots)
    assert provider.runtime_provenance.environment is RuntimeEnvironment.LIVE
    assert provider.runtime_provenance.data_mode is HistoricalDataMode.LIVE_STRICT
    assert provider.last_raw_artifact is not None
    assert provider.last_request_audit is not None
    assert provider.last_request_audit.received_at_utc == RECEIVED
    assert provider.last_raw_artifact_id == provider.last_raw_artifact.artifact_id
    assert provider.last_raw_artifact_path == str(
        provider.last_raw_artifact.payload_path
    )
    assert provider.last_raw_payload_sha256 == hashlib.sha256(payload).hexdigest()
    metadata = provider.last_raw_artifact.metadata_path.read_text(encoding="utf-8")
    assert TOKEN not in metadata
    assert TOKEN in transport.requests[0].url
    assert "apiKey" not in metadata
    assert "/historical/" not in transport.requests[0].url


def test_live_market_ingestion_captures_raw_source_consensus_and_lineage(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_the_odds_api/current.json").read_bytes()
    provider = _odds_provider(
        ScriptedTransport([HttpResponse(200, payload)]),
        RawDataArchive(tmp_path / "raw"),
    )

    class Repository:
        capture: MarketOddsIngestionCapture | None = None

        def save_market_odds_ingestion(
            self,
            capture: MarketOddsIngestionCapture,
        ) -> SourceIngestionSummary:
            self.capture = capture
            return SourceIngestionSummary(
                ingestion_id=capture.ingestion_id,
                source_kind=LiveSourceKind.MARKET_ODDS,
                status=LiveSourceIngestionStatus.COMPLETED,
                inserted=True,
                artifact_count=1,
                snapshot_count=len(capture.source_batch.snapshots),
                mapping_count=len(capture.source_batch.mappings),
                issue_count=len(capture.issues),
                consensus_count=len(capture.consensus_batch.snapshots),
            )

    repository = Repository()
    ingested_at = RECEIVED + timedelta(seconds=1)
    service = LiveMarketOddsIngestionService(
        provider,
        repository,
        environment=RuntimeEnvironment.LIVE,
        identity_cutoff_at_utc=RECEIVED - timedelta(seconds=1),
        clock=lambda: ingested_at,
    )

    summary = asyncio.run(service.ingest(("match-synthetic",)))

    assert summary.status is LiveSourceIngestionStatus.COMPLETED
    assert repository.capture is not None
    capture = repository.capture
    assert capture.request_audit == provider.last_request_audit
    assert capture.artifact.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert all(
        snapshot.ingested_at_utc == ingested_at
        for snapshot in capture.source_batch.snapshots
    )
    assert len(capture.source_batch.snapshots) == 2
    assert len(capture.consensus_batch.snapshots) == 1
    assert len(capture.consensus_lineages) == 1
    assert {
        item.snapshot_id for item in capture.consensus_lineages[0].constituents
    } == {item.snapshot_id for item in capture.source_batch.snapshots}


def test_the_odds_api_partially_normalizes_unrelated_reconciliation_failures(
    tmp_path: Path,
) -> None:
    current = json.loads(
        (FIXTURES / "synthetic_the_odds_api/current.json").read_text(encoding="utf-8")
    )
    unresolved = json.loads(json.dumps(current[0]))
    unresolved["id"] = "event-unresolved"
    unresolved["home_team"] = "Unknown Northern FC"
    unresolved["away_team"] = "Unknown Southern FC"
    malformed = json.loads(json.dumps(current[0]))
    malformed["id"] = "event-malformed-unrelated"
    malformed["bookmakers"][0]["markets"] = [None]
    payload_events = [unresolved, malformed, current[0]]
    resolver = _odds_resolver(
        explicit_mappings={
            (
                "THE_ODDS_API",
                "event",
                "event-malformed-unrelated",
            ): "match-unrelated"
        }
    )

    def fetch(events: list[object], raw_directory: str):
        provider = _odds_provider(
            ScriptedTransport([HttpResponse(200, json.dumps(events).encode())]),
            RawDataArchive(tmp_path / raw_directory),
            resolver=resolver,
        )
        return asyncio.run(
            provider.fetch_market_odds(
                SnapshotQuery(
                    match_ids=("match-synthetic",),
                    as_of_at_utc=RECEIVED,
                )
            )
        )

    batch = fetch(payload_events, "raw")
    reordered = fetch(list(reversed(payload_events)), "raw-reordered")

    assert len(batch.snapshots) == 2
    assert batch.mappings[0].internal_match_id == "match-synthetic"
    assert {issue.reason for issue in batch.issues} == {
        MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED,
        MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
    }
    assert batch.issues == reordered.issues
    assert [issue.issue_id for issue in batch.issues] == sorted(
        issue.issue_id for issue in batch.issues
    )
    assert all(
        issue.candidates == tuple(sorted(issue.candidates)) for issue in batch.issues
    )
    assert TOKEN not in "".join(issue.model_dump_json() for issue in batch.issues)


def test_the_odds_api_target_failures_are_explicit_and_fail_closed(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_the_odds_api/current.json").read_bytes()

    unresolved_provider = _odds_provider(
        ScriptedTransport([HttpResponse(200, payload)]),
        RawDataArchive(tmp_path / "unresolved-raw"),
        resolver=MatchIdentityResolver((), (), ()),
    )
    unresolved = asyncio.run(
        unresolved_provider.fetch_market_odds(
            SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
        )
    )
    assert unresolved.snapshots == unresolved.mappings == ()
    assert {issue.reason for issue in unresolved.issues} == {
        MarketOddsReconciliationIssueReason.IDENTITY_UNRESOLVED,
        MarketOddsReconciliationIssueReason.REQUESTED_MATCH_MISSING,
    }

    ambiguous_provider = _odds_provider(
        ScriptedTransport([HttpResponse(200, payload)]),
        RawDataArchive(tmp_path / "ambiguous-raw"),
        resolver=_odds_resolver(match_ids=("match-z", "match-a")),
    )
    ambiguous = asyncio.run(
        ambiguous_provider.fetch_market_odds(
            SnapshotQuery(match_ids=("match-a",), as_of_at_utc=RECEIVED)
        )
    )
    ambiguity = next(
        issue
        for issue in ambiguous.issues
        if issue.reason is MarketOddsReconciliationIssueReason.IDENTITY_AMBIGUOUS
    )
    assert ambiguous.snapshots == ambiguous.mappings == ()
    assert ambiguity.requested_match_id == "match-a"
    assert ambiguity.candidates == ("match-a", "match-z")
    assert any(
        issue.reason is MarketOddsReconciliationIssueReason.REQUESTED_MATCH_MISSING
        for issue in ambiguous.issues
    )

    duplicate_payload = json.loads(payload)
    duplicate_event = json.loads(json.dumps(duplicate_payload[0]))
    duplicate_event["id"] = "synthetic-odds-event-duplicate"
    duplicate_payload.append(duplicate_event)
    duplicate_provider = _odds_provider(
        ScriptedTransport([HttpResponse(200, json.dumps(duplicate_payload).encode())]),
        RawDataArchive(tmp_path / "duplicate-raw"),
    )
    duplicate = asyncio.run(
        duplicate_provider.fetch_market_odds(
            SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
        )
    )
    assert duplicate.snapshots == duplicate.mappings == ()
    assert (
        sum(
            issue.reason
            is MarketOddsReconciliationIssueReason.DUPLICATE_RESOLVED_TARGET
            for issue in duplicate.issues
        )
        == 2
    )
    assert any(
        issue.reason is MarketOddsReconciliationIssueReason.REQUESTED_MATCH_MISSING
        for issue in duplicate.issues
    )

    missing_provider = _odds_provider(
        ScriptedTransport([HttpResponse(200, b"[]")]),
        RawDataArchive(tmp_path / "missing-raw"),
    )
    missing = asyncio.run(
        missing_provider.fetch_market_odds(
            SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
        )
    )
    assert [issue.reason for issue in missing.issues] == [
        MarketOddsReconciliationIssueReason.REQUESTED_MATCH_MISSING
    ]


def test_historical_odds_import_is_research_only_and_writes_queryable_archives(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_the_odds_api/historical.json").read_bytes()
    transport = ScriptedTransport([HttpResponse(200, payload)])
    importer = _odds_importer(transport, RawDataArchive(tmp_path / "raw"))

    assert not isinstance(importer, MarketOddsProvider)
    envelope = asyncio.run(importer.import_historical_market_odds())

    source_cutoff = datetime(2026, 9, 1, 11, 45, tzinfo=UTC)
    assert envelope.data_mode is HistoricalDataMode.SOURCE_TIME_RESEARCH
    assert envelope.runtime_provenance.environment is RuntimeEnvironment.RESEARCH
    assert envelope.source_cutoff_at_utc == source_cutoff
    assert envelope.identity_cutoff_at_utc == source_cutoff
    assert envelope.imported_at_utc == RECEIVED
    assert envelope.snapshots[0].captured_at_utc == datetime(
        2026, 9, 1, 11, 30, tzinfo=UTC
    )
    assert envelope.snapshots[0].available_at_utc == source_cutoff
    assert envelope.snapshots[0].ingested_at_utc == source_cutoff
    assert importer.last_raw_artifact is not None
    assert importer.last_raw_artifact.payload_path.read_bytes() == payload
    assert TOKEN not in importer.last_raw_artifact.metadata_path.read_text(
        encoding="utf-8"
    )

    archive_directory = tmp_path / "archives"
    paths = write_the_odds_api_historical_archives(
        envelope,
        archive_directory,
        created_at_utc=RECEIVED + timedelta(minutes=1),
        source_reference="test://the-odds-api/historical",
        license_note="TEST_ONLY",
    )
    documents = tuple(json.loads(path.read_text(encoding="utf-8")) for path in paths)
    assert {document["manifest"]["dataset_kind"] for document in documents} == {
        "MARKET_ODDS",
        "MARKET_ODDS_ISSUES",
        "PROVIDER_MAPPINGS",
    }
    assert all(
        document["manifest"]["data_mode"] == "SOURCE_TIME_RESEARCH"
        for document in documents
    )
    assert all(
        record["retrospective"] is True and record["imported_at_utc"] is not None
        for document in documents
        for record in document["records"]
    )

    store = LocalArchiveStore(
        archive_directory,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )
    archive_provider = HistoricalArchiveMarketOddsProvider(
        store,
        "THE_ODDS_API",
    )
    batch = asyncio.run(
        archive_provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=("match-synthetic",),
                as_of_at_utc=source_cutoff,
            )
        )
    )
    assert len(batch.snapshots) == 1
    assert batch.snapshots[0].ingested_at_utc == source_cutoff
    assert archive_provider.retrospective is True
    assert archive_provider.report_data_mode == "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
    assert envelope.imported_at_utc > source_cutoff
    with pytest.raises(MissingArchiveInputError, match="LIVE_STRICT"):
        HistoricalArchiveMarketOddsProvider(
            archive_directory,
            "THE_ODDS_API",
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )


def test_historical_odds_archives_replay_reconciliation_issues(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (FIXTURES / "synthetic_the_odds_api/historical.json").read_text(
            encoding="utf-8"
        )
    )
    unresolved = json.loads(json.dumps(payload["data"][0]))
    unresolved.update(
        {
            "id": "historical-unresolved-event",
            "home_team": "Unknown Historical Home",
            "away_team": "Unknown Historical Away",
        }
    )
    payload["data"].append(unresolved)
    importer = _odds_importer(
        ScriptedTransport([HttpResponse(200, json.dumps(payload).encode())]),
        RawDataArchive(tmp_path / "raw"),
    )
    envelope = asyncio.run(importer.import_historical_market_odds())
    assert len(envelope.issues) == 1

    archive_directory = tmp_path / "archives"
    paths = write_the_odds_api_historical_archives(
        envelope,
        archive_directory,
        created_at_utc=RECEIVED + timedelta(minutes=1),
    )
    issue_document = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in paths
        if json.loads(path.read_text(encoding="utf-8"))["manifest"]["dataset_kind"]
        == "MARKET_ODDS_ISSUES"
    )
    assert all(
        record["payload"]["record_kind"] == "MARKET_ODDS_RECONCILIATION_ISSUE"
        for record in issue_document["records"]
    )

    provider = HistoricalArchiveMarketOddsProvider(
        archive_directory,
        "THE_ODDS_API",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )
    batch = asyncio.run(
        provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=("match-synthetic",),
                as_of_at_utc=envelope.source_available_at_utc,
            )
        )
    )
    assert batch.issues == envelope.issues


def test_historical_odds_multiple_snapshots_reuse_first_immutable_mapping(
    tmp_path: Path,
) -> None:
    first_payload = json.loads(
        (FIXTURES / "synthetic_the_odds_api/historical.json").read_text(
            encoding="utf-8"
        )
    )
    first = asyncio.run(
        _odds_importer(
            ScriptedTransport([HttpResponse(200, json.dumps(first_payload).encode())]),
            RawDataArchive(tmp_path / "raw-first"),
        ).import_historical_market_odds()
    )
    archive_directory = tmp_path / "archives"
    first_paths = write_the_odds_api_historical_archives(first, archive_directory)
    assert (
        write_the_odds_api_historical_archives(first, archive_directory) == first_paths
    )

    second_payload = json.loads(json.dumps(first_payload))
    second_cutoff = datetime(2026, 9, 1, 11, 50, tzinfo=UTC)
    second_payload["timestamp"] = second_cutoff.isoformat().replace("+00:00", "Z")
    second = asyncio.run(
        _odds_importer(
            ScriptedTransport([HttpResponse(200, json.dumps(second_payload).encode())]),
            RawDataArchive(tmp_path / "raw-second"),
            received_at=RECEIVED + timedelta(minutes=5),
        ).import_historical_market_odds()
    )
    write_the_odds_api_historical_archives(second, archive_directory)

    documents = tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in archive_directory.glob("*.json")
    )
    mapping_records = tuple(
        record
        for document in documents
        if document["manifest"]["dataset_kind"] == "PROVIDER_MAPPINGS"
        for record in document["records"]
    )
    assert len(mapping_records) == 1
    assert mapping_records[0]["payload"]["available_at_utc"] == (
        first.source_available_at_utc.isoformat().replace("+00:00", "Z")
    )

    provider = HistoricalArchiveMarketOddsProvider(
        archive_directory,
        "THE_ODDS_API",
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )
    batch = asyncio.run(
        provider.fetch_market_odds(
            SnapshotQuery(
                match_ids=("match-synthetic",),
                as_of_at_utc=second_cutoff,
            )
        )
    )
    assert len(batch.snapshots) == 1
    assert batch.snapshots[0].available_at_utc == second_cutoff


def test_the_odds_api_event_data_errors_are_issues_but_bad_structure_fails_closed(
    tmp_path: Path,
) -> None:
    raw = json.loads(
        (FIXTURES / "synthetic_the_odds_api/current.json").read_text(encoding="utf-8")
    )
    raw[0]["bookmakers"][0]["markets"][0]["outcomes"][1]["name"] = "Tie"
    malformed = _odds_provider(
        ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        RawDataArchive(tmp_path / "raw"),
    )

    batch = asyncio.run(
        malformed.fetch_market_odds(
            SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
        )
    )
    assert batch.snapshots == batch.mappings == ()
    assert {issue.reason for issue in batch.issues} == {
        MarketOddsReconciliationIssueReason.EVENT_DATA_INVALID,
        MarketOddsReconciliationIssueReason.REQUESTED_MATCH_MISSING,
    }

    raw.append(None)
    structurally_bad = _odds_provider(
        ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        RawDataArchive(tmp_path / "structurally-bad-raw"),
    )
    with pytest.raises(ProviderPayloadError, match="event entries must be objects"):
        asyncio.run(
            structurally_bad.fetch_market_odds(
                SnapshotQuery(
                    match_ids=("match-synthetic",),
                    as_of_at_utc=RECEIVED,
                )
            )
        )
    assert structurally_bad.last_raw_artifact is not None

    failed = _odds_provider(
        ScriptedTransport([TimeoutError(TOKEN)]),
        RawDataArchive(tmp_path / "failed-raw"),
    )
    with pytest.raises(ProviderRequestError) as error:
        asyncio.run(
            failed.fetch_market_odds(
                SnapshotQuery(
                    match_ids=("match-synthetic",),
                    as_of_at_utc=RECEIVED,
                )
            )
        )
    assert TOKEN not in str(error.value)
