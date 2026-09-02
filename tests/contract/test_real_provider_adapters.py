import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from football_system.application.ports.data_providers import FixtureQuery, SnapshotQuery
from football_system.domain.identity import (
    Alias,
    AmbiguousMatchMappingError,
    CanonicalMatchIdentity,
    CompetitionMapping,
    MatchIdentityResolver,
    TeamIdentity,
    UnresolvedMatchMappingError,
)
from football_system.domain.raw_data import ProviderRequestFailureCode
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
    TheOddsApiMarketOddsProvider,
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
    *, match_ids: tuple[str, ...] = ("match-synthetic",)
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
    )


def _odds_provider(
    transport: ScriptedTransport,
    archive: RawDataArchive,
    *,
    historical_at_utc: datetime | None = None,
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
        historical_at_utc=historical_at_utc,
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
    assert all(snapshot.available_at_utc == RECEIVED for snapshot in batch.snapshots)
    assert all(snapshot.ingested_at_utc == RECEIVED for snapshot in batch.snapshots)
    assert provider.last_raw_artifact is not None
    metadata = provider.last_raw_artifact.metadata_path.read_text(encoding="utf-8")
    assert TOKEN not in metadata
    assert TOKEN in transport.requests[0].url
    assert "apiKey" not in metadata


def test_the_odds_api_uses_historical_wrapper_time_and_exact_identity(
    tmp_path: Path,
) -> None:
    payload = (FIXTURES / "synthetic_the_odds_api/historical.json").read_bytes()
    provider = _odds_provider(
        ScriptedTransport([HttpResponse(200, payload)]),
        RawDataArchive(tmp_path / "raw"),
        historical_at_utc=datetime(2026, 9, 1, 11, 45, tzinfo=UTC),
    )

    batch = asyncio.run(
        provider.fetch_market_odds(
            SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
        )
    )

    assert len(batch.snapshots) == 1
    assert batch.snapshots[0].captured_at_utc == datetime(
        2026, 9, 1, 11, 30, tzinfo=UTC
    )
    assert batch.snapshots[0].available_at_utc == datetime(
        2026, 9, 1, 11, 45, tzinfo=UTC
    )
    assert batch.snapshots[0].ingested_at_utc == RECEIVED

    unresolved = _odds_provider(
        ScriptedTransport([HttpResponse(200, payload)]),
        RawDataArchive(tmp_path / "unresolved-raw"),
        resolver=MatchIdentityResolver((), (), ()),
    )
    with pytest.raises(UnresolvedMatchMappingError):
        asyncio.run(
            unresolved.fetch_market_odds(
                SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
            )
        )

    ambiguous = _odds_provider(
        ScriptedTransport([HttpResponse(200, payload)]),
        RawDataArchive(tmp_path / "ambiguous-raw"),
        resolver=_odds_resolver(match_ids=("match-one", "match-two")),
    )
    with pytest.raises(AmbiguousMatchMappingError):
        asyncio.run(
            ambiguous.fetch_market_odds(
                SnapshotQuery(match_ids=("match-one",), as_of_at_utc=RECEIVED)
            )
        )


def test_the_odds_api_rejects_nonexact_h2h_outcome_and_failed_request(
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
    with pytest.raises(ProviderPayloadError, match="exactly"):
        asyncio.run(
            malformed.fetch_market_odds(
                SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
            )
        )

    raw[0]["bookmakers"][0]["markets"] = [None]
    structurally_bad = _odds_provider(
        ScriptedTransport([HttpResponse(200, json.dumps(raw).encode())]),
        RawDataArchive(tmp_path / "structurally-bad-raw"),
    )
    with pytest.raises(ProviderPayloadError, match="contain objects"):
        asyncio.run(
            structurally_bad.fetch_market_odds(
                SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
            )
        )

    failed = _odds_provider(
        ScriptedTransport([TimeoutError(TOKEN)]),
        RawDataArchive(tmp_path / "failed-raw"),
    )
    with pytest.raises(ProviderRequestError) as error:
        asyncio.run(
            failed.fetch_market_odds(
                SnapshotQuery(match_ids=("match-synthetic",), as_of_at_utc=RECEIVED)
            )
        )
    assert TOKEN not in str(error.value)
