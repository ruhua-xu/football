from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from football_system.application.ports.data_providers import (
    MarketOddsBatch,
    MarketOddsProvider,
    SnapshotQuery,
)
from football_system.domain.archive import canonical_payload_sha256
from football_system.domain.common import stable_id
from football_system.domain.identity import MatchIdentityResolver, ProviderMatchIdentity
from football_system.domain.market import MarketKey, MarketType, ThreeWayMarketOdds
from football_system.domain.match import (
    MarketOddsSnapshot,
    OddsQuote,
    ProviderMatchMapping,
)
from football_system.infrastructure.files.raw_archive import (
    ArchivedRawArtifact,
    RawDataArchive,
)
from football_system.infrastructure.http.provider_client import ProviderHttpClient
from football_system.infrastructure.providers.real._common import (
    ProviderPayloadError,
    archive_successful_response,
    decode_json_payload,
    parse_utc_timestamp,
)

THE_ODDS_API_PROVIDER_CODE = "THE_ODDS_API"
_THREE_WAY_MARKET = MarketKey(market_type=MarketType.THREE_WAY)


class TheOddsApiMarketOddsProvider(MarketOddsProvider):
    """Fetches exact-name soccer h2h odds and retains every complete bookmaker."""

    provider_code = THE_ODDS_API_PROVIDER_CODE

    def __init__(
        self,
        client: ProviderHttpClient,
        raw_archive: RawDataArchive,
        identity_resolver: MatchIdentityResolver,
        api_key: str,
        *,
        sport_key: str,
        season: str,
        competition_type: str,
        historical_at_utc: datetime | None = None,
        regions: str = "uk",
    ) -> None:
        self._client = client
        self._raw_archive = raw_archive
        self._identity_resolver = identity_resolver
        self._api_key = _credential(api_key)
        self._sport_key = _required_text(sport_key, "sport key")
        if not self._sport_key.startswith("soccer_"):
            raise ValueError("The Odds API sport_key must identify soccer")
        self._season = _required_text(season, "season")
        self._competition_type = _required_text(
            competition_type,
            "competition type",
        )
        self._regions = _required_text(regions, "regions")
        if historical_at_utc is not None:
            if (
                historical_at_utc.tzinfo is None
                or historical_at_utc.utcoffset() is None
            ):
                raise ValueError("historical_at_utc must be timezone-aware")
            self._historical_at_utc = historical_at_utc.astimezone(timezone.utc)
        else:
            self._historical_at_utc = None
        self._last_raw_artifact: ArchivedRawArtifact | None = None

    @property
    def last_raw_artifact(self) -> ArchivedRawArtifact | None:
        return self._last_raw_artifact

    async def fetch_market_odds(self, query: SnapshotQuery) -> MarketOddsBatch:
        endpoint, parameters = self._request_details()
        result = self._client.get(endpoint, query_parameters=parameters)
        artifact = archive_successful_response(
            self.provider_code,
            result,
            self._raw_archive,
        )
        self._last_raw_artifact = artifact
        payload = decode_json_payload(self.provider_code, result.payload or b"")
        events, available_at_utc = _events_and_availability(
            payload,
            received_at_utc=result.audit.received_at_utc,
        )
        snapshots, mappings = self._normalize_events(
            events,
            available_at_utc=available_at_utc,
            ingested_at_utc=result.audit.received_at_utc,
        )
        requested = set(query.match_ids)
        visible = tuple(
            snapshot
            for snapshot in snapshots
            if snapshot.match_id in requested
            and _snapshot_is_visible(snapshot, query.as_of_at_utc)
        )
        visible_match_ids = {snapshot.match_id for snapshot in visible}
        return MarketOddsBatch(
            snapshots=visible,
            mappings=tuple(
                mapping
                for mapping in mappings
                if mapping.internal_match_id in visible_match_ids
                and mapping.available_at_utc <= query.as_of_at_utc
            ),
        )

    def _request_details(self) -> tuple[str, dict[str, str]]:
        parameters = {
            "apiKey": self._api_key,
            "regions": self._regions,
            "markets": "h2h",
            "oddsFormat": "decimal",
        }
        if self._historical_at_utc is None:
            return f"/v4/sports/{self._sport_key}/odds", parameters
        parameters["date"] = self._historical_at_utc.isoformat().replace("+00:00", "Z")
        return f"/v4/historical/sports/{self._sport_key}/odds", parameters

    def _normalize_events(
        self,
        events: tuple[Mapping[str, object], ...],
        *,
        available_at_utc: datetime,
        ingested_at_utc: datetime,
    ) -> tuple[tuple[MarketOddsSnapshot, ...], tuple[ProviderMatchMapping, ...]]:
        snapshots: list[MarketOddsSnapshot] = []
        mappings: list[ProviderMatchMapping] = []
        event_ids: set[str] = set()
        mapped_match_ids: set[str] = set()
        for event in events:
            event_id = _identifier(event.get("id"), "event id")
            if event_id in event_ids:
                raise ProviderPayloadError(
                    self.provider_code,
                    "response contains duplicate event IDs",
                )
            event_ids.add(event_id)
            identity = self._provider_identity(event, event_id)
            resolution = self._identity_resolver.resolve(identity)
            if resolution.internal_match_id in mapped_match_ids:
                raise ProviderPayloadError(
                    self.provider_code,
                    "multiple provider events resolve to one internal match",
                )
            mapped_match_ids.add(resolution.internal_match_id)
            mapping = ProviderMatchMapping(
                mapping_id=stable_id(
                    "provider-mapping",
                    self.provider_code,
                    "event",
                    event_id,
                ),
                provider_code=self.provider_code,
                external_namespace="event",
                external_match_id=event_id,
                internal_match_id=resolution.internal_match_id,
                resolution_method=resolution.resolution_method,
                confidence=resolution.confidence,
                available_at_utc=available_at_utc,
            )
            event_snapshots = _bookmaker_snapshots(
                event,
                event_id=event_id,
                match_id=resolution.internal_match_id,
                available_at_utc=available_at_utc,
                ingested_at_utc=ingested_at_utc,
            )
            if event_snapshots:
                mappings.append(mapping)
                snapshots.extend(event_snapshots)
        return (
            tuple(
                sorted(
                    snapshots,
                    key=lambda item: (
                        item.match_id,
                        item.bookmaker_code,
                        item.source_snapshot_key,
                    ),
                )
            ),
            tuple(sorted(mappings, key=lambda item: item.mapping_id)),
        )

    def _provider_identity(
        self,
        event: Mapping[str, object],
        event_id: str,
    ) -> ProviderMatchIdentity:
        sport_key = _required_text(event.get("sport_key"), "event sport_key")
        if sport_key != self._sport_key or not sport_key.startswith("soccer_"):
            raise ProviderPayloadError(
                self.provider_code,
                "event is not from the configured soccer sport",
            )
        home_team = _required_text(event.get("home_team"), "event home_team")
        away_team = _required_text(event.get("away_team"), "event away_team")
        if home_team == away_team:
            raise ProviderPayloadError(
                self.provider_code,
                "event home and away teams must differ",
            )
        return ProviderMatchIdentity(
            provider_code=self.provider_code,
            provider_match_id=event_id,
            external_namespace="event",
            provider_competition_id=sport_key,
            provider_competition_name=_required_text(
                event.get("sport_title"),
                "event sport_title",
            ),
            competition_language="en",
            season=self._season,
            competition_type=self._competition_type,
            # The Odds API does not expose team IDs, so exact raw names are IDs too.
            home_team_id=home_team,
            home_team_name=home_team,
            home_team_language="en",
            away_team_id=away_team,
            away_team_name=away_team,
            away_team_language="en",
            kickoff_at_utc=parse_utc_timestamp(
                self.provider_code,
                event.get("commence_time"),
                field="event commence_time",
            ),
        )


def _events_and_availability(
    payload: object,
    *,
    received_at_utc: datetime,
) -> tuple[tuple[Mapping[str, object], ...], datetime]:
    if isinstance(payload, list):
        return _events(payload), received_at_utc
    if not isinstance(payload, Mapping):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "payload must be an event array or historical response object",
        )
    timestamp = parse_utc_timestamp(
        THE_ODDS_API_PROVIDER_CODE,
        payload.get("timestamp"),
        field="historical timestamp",
    )
    if timestamp > received_at_utc:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "historical timestamp cannot be after local receipt time",
        )
    return _events(payload.get("data")), timestamp


def _events(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "event data must be an array",
        )
    result: list[Mapping[str, object]] = []
    for event in value:
        if not isinstance(event, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "event entries must be objects",
            )
        result.append(event)
    return tuple(result)


def _bookmaker_snapshots(
    event: Mapping[str, object],
    *,
    event_id: str,
    match_id: str,
    available_at_utc: datetime,
    ingested_at_utc: datetime,
) -> tuple[MarketOddsSnapshot, ...]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "event bookmakers must be an array",
        )
    home_team = _required_text(event.get("home_team"), "event home_team")
    away_team = _required_text(event.get("away_team"), "event away_team")
    result: list[MarketOddsSnapshot] = []
    bookmaker_codes: set[str] = set()
    for raw_bookmaker in bookmakers:
        if not isinstance(raw_bookmaker, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker entries must be objects",
            )
        bookmaker_code = _required_text(raw_bookmaker.get("key"), "bookmaker key")
        if bookmaker_code in bookmaker_codes:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "event contains duplicate bookmaker keys",
            )
        bookmaker_codes.add(bookmaker_code)
        odds = _complete_h2h_odds(raw_bookmaker, home_team, away_team)
        if odds is None:
            continue
        captured_at_utc = parse_utc_timestamp(
            THE_ODDS_API_PROVIDER_CODE,
            raw_bookmaker.get("last_update"),
            field="bookmaker last_update",
        )
        if not captured_at_utc <= available_at_utc <= ingested_at_utc:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker timestamps are not causally ordered",
            )
        payload_hash = canonical_payload_sha256(odds)
        source_snapshot_key = stable_id(
            "the-odds-api-source",
            event_id,
            bookmaker_code,
            captured_at_utc.isoformat(),
            available_at_utc.isoformat(),
            payload_hash,
        )
        result.append(
            MarketOddsSnapshot(
                snapshot_id=stable_id(
                    "market-odds",
                    THE_ODDS_API_PROVIDER_CODE,
                    source_snapshot_key,
                ),
                match_id=match_id,
                provider_code=THE_ODDS_API_PROVIDER_CODE,
                bookmaker_code=bookmaker_code,
                market=_THREE_WAY_MARKET,
                quotes=tuple(
                    OddsQuote(selection=selection, odds=value)
                    for selection, value in odds.items()
                ),
                captured_at_utc=captured_at_utc,
                available_at_utc=available_at_utc,
                ingested_at_utc=ingested_at_utc,
                source_snapshot_key=source_snapshot_key,
                payload_hash=payload_hash,
            )
        )
    return tuple(result)


def _complete_h2h_odds(
    bookmaker: Mapping[str, object],
    home_team: str,
    away_team: str,
) -> ThreeWayMarketOdds | None:
    markets = bookmaker.get("markets")
    if not isinstance(markets, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "bookmaker markets must be an array",
        )
    h2h_markets: list[Mapping[str, object]] = []
    for market in markets:
        if not isinstance(market, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker markets must contain objects",
            )
        market_key = market.get("key")
        if not isinstance(market_key, str):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "bookmaker market key must be a string",
            )
        if market_key == "h2h":
            h2h_markets.append(market)
    if not h2h_markets:
        return None
    if len(h2h_markets) != 1:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "bookmaker contains multiple h2h markets",
        )
    outcomes = h2h_markets[0].get("outcomes")
    if not isinstance(outcomes, list):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h outcomes must be an array",
        )
    by_name: dict[str, Decimal] = {}
    expected_names = {home_team, "Draw", away_team}
    for raw_outcome in outcomes:
        if not isinstance(raw_outcome, Mapping):
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "h2h outcomes must be objects",
            )
        name = raw_outcome.get("name")
        if not isinstance(name, str) or name not in expected_names:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "h2h outcome does not exactly identify home, draw, or away",
            )
        if name in by_name:
            raise ProviderPayloadError(
                THE_ODDS_API_PROVIDER_CODE,
                "h2h outcomes contain duplicates",
            )
        by_name[name] = _decimal_odds(raw_outcome.get("price"))
    if set(by_name) != expected_names:
        return None
    return ThreeWayMarketOdds(
        home_win=by_name[home_team],
        draw=by_name["Draw"],
        away_win=by_name[away_team],
    )


def _credential(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("\r", "\n", "\0"))
    ):
        raise ValueError("The Odds API credential is invalid")
    return value.strip()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            f"{field} must be a nonempty string",
        )
    return value.strip()


def _identifier(value: object, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if type(value) is int:
        return str(value)
    raise ProviderPayloadError(
        THE_ODDS_API_PROVIDER_CODE,
        f"{field} must be a nonempty string or integer",
    )


def _decimal_odds(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h decimal odds are invalid",
        )
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h decimal odds are invalid",
        ) from None
    if not decimal.is_finite() or decimal <= 1:
        raise ProviderPayloadError(
            THE_ODDS_API_PROVIDER_CODE,
            "h2h decimal odds must be finite and above one",
        )
    return decimal


def _snapshot_is_visible(snapshot: MarketOddsSnapshot, cutoff: datetime) -> bool:
    return all(
        timestamp <= cutoff
        for timestamp in (
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
    )
