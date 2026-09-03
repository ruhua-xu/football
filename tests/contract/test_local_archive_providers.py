import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from football_system.application.ports.data_providers import (
    EloTrainingHistoryProvider,
    EloTrainingHistoryQuery,
    FixtureProvider,
    FixtureQuery,
    HistoricalDataProvider,
    ManualQuantProvider,
    MarketOddsProvider,
    MatchResultQuery,
    SnapshotQuery,
    SportteryProvider,
)
from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    FixtureArchivePayload,
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
    archive_payload_sha256,
    canonical_payload_sha256,
    match_result_payload_sha256,
)
from football_system.domain.market import (
    MarketKey,
    MarketType,
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.match import (
    Competition,
    FixedBonusQuote,
    MarketOddsSnapshot,
    Match,
    MatchStatus,
    OddsQuote,
    ProviderMatchMapping,
    SaleStatus,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.prediction import ManualQuantInput
from football_system.domain.settlement import MatchResult
from football_system.infrastructure.providers.historical_archive import (
    ArchiveValidationError,
    HistoricalArchiveFixtureProvider,
    HistoricalArchiveEloTrainingProvider,
    HistoricalArchiveMarketOddsProvider,
    HistoricalArchiveQuantProvider,
    HistoricalArchiveSportteryProvider,
    LocalArchiveHistoricalDataProvider,
    LocalArchiveStore,
    MissingArchiveInputError,
)

UTC = timezone.utc
BASE = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)

FIXTURE_PROVIDER = "FIXTURE_ARCHIVE"
MARKET_PROVIDER = "MARKET_ARCHIVE"
SPORTTERY_PROVIDER = "SPORTTERY_ARCHIVE"
QUANT_PROVIDER = "QUANT_ARCHIVE"
RESULT_PROVIDER = "RESULT_ARCHIVE"


def _record(
    payload: BaseModel,
    *,
    data_mode: HistoricalDataMode,
    imported_at: datetime | None,
) -> dict[str, object]:
    return {
        "retrospective": data_mode.is_retrospective,
        "imported_at_utc": imported_at.isoformat() if imported_at else None,
        "payload": payload.model_dump(mode="json"),
    }


def _write_archive(
    directory: Path,
    filename: str,
    kind: HistoricalArchiveDatasetKind,
    provider_code: str,
    payloads: tuple[BaseModel, ...],
    *,
    data_mode: HistoricalDataMode = HistoricalDataMode.LIVE_STRICT,
    imported_at: datetime | None = None,
) -> None:
    records = [
        _record(payload, data_mode=data_mode, imported_at=imported_at)
        for payload in payloads
    ]
    document = {
        "manifest": {
            "archive_schema_version": HISTORICAL_ARCHIVE_SCHEMA_VERSION,
            "archive_id": filename.removesuffix(".json"),
            "provider_code": provider_code,
            "dataset_kind": kind.value,
            "created_at_utc": CREATED.isoformat(),
            "source_reference": f"test://{filename}",
            "source_description": "Deterministic provider contract data",
            "license_note": "TEST_ONLY",
            "data_mode": data_mode.value,
            "payload_sha256": archive_payload_sha256(records),
            "record_count": len(records),
        },
        "records": records,
    }
    (directory / filename).write_text(
        json.dumps(document, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def _mapping(
    provider_code: str,
    match_id: str = "match-1",
    *,
    external_match_id: str | None = None,
) -> ProviderMatchMapping:
    return ProviderMatchMapping(
        mapping_id=f"mapping-{provider_code}-{match_id}",
        provider_code=provider_code,
        external_namespace=provider_code.lower(),
        external_match_id=external_match_id or f"external-{match_id}",
        internal_match_id=match_id,
        resolution_method="ARCHIVE_EXACT",
        confidence=Decimal(1),
        available_at_utc=BASE - timedelta(hours=1),
    )


def _fixture(*, available_at: datetime, kickoff_at: datetime) -> FixtureArchivePayload:
    competition = Competition(
        competition_id="competition-1",
        canonical_key="competition:test",
        name="Test League",
        country_code="GB",
    )
    home = Team(team_id="team-home", canonical_key="team:home", name="Home")
    away = Team(team_id="team-away", canonical_key="team:away", name="Away")
    return FixtureArchivePayload(
        competition=competition,
        home_team=home,
        away_team=away,
        match=Match(
            match_id="match-1",
            competition_id=competition.competition_id,
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            kickoff_at_utc=kickoff_at,
            status=MatchStatus.SCHEDULED,
            available_at_utc=available_at,
        ),
    )


def _odds_snapshot(
    version: int,
    *,
    captured_at: datetime,
    available_at: datetime,
    ingested_at: datetime,
) -> MarketOddsSnapshot:
    odds = ThreeWayMarketOdds(
        home_win=Decimal("2.00") + Decimal(version) / 10,
        draw=Decimal("3.20"),
        away_win=Decimal("3.40"),
    )
    return MarketOddsSnapshot(
        snapshot_id=f"market-snapshot-{version}",
        match_id="match-1",
        provider_code=MARKET_PROVIDER,
        bookmaker_code="CONSENSUS",
        market=MARKET,
        quotes=tuple(
            OddsQuote(selection=selection, odds=value)
            for selection, value in odds.items()
        ),
        captured_at_utc=captured_at,
        available_at_utc=available_at,
        ingested_at_utc=ingested_at,
        source_snapshot_key=f"market-source-{version}",
        payload_hash=canonical_payload_sha256(odds),
    )


def _sporttery_snapshot() -> SportteryBonusSnapshot:
    bonus = ThreeWayFixedBonus(
        home_win=Decimal("2.25"),
        draw=Decimal("3.10"),
        away_win=Decimal("3.30"),
    )
    return SportteryBonusSnapshot(
        snapshot_id="sporttery-snapshot-1",
        match_id="match-1",
        provider_code=SPORTTERY_PROVIDER,
        sporttery_match_no="S001",
        market=MARKET,
        quotes=tuple(
            FixedBonusQuote(selection=selection, fixed_bonus=value)
            for selection, value in bonus.items()
        ),
        sale_status=SaleStatus.OPEN,
        captured_at_utc=BASE + timedelta(minutes=20),
        available_at_utc=BASE + timedelta(minutes=30),
        ingested_at_utc=BASE + timedelta(minutes=31),
        source_snapshot_key="sporttery-source-1",
        payload_hash=canonical_payload_sha256(bonus),
    )


def _manual_quant() -> ManualQuantInput:
    probabilities = ThreeWayProbability(
        home_win=Decimal("0.45"),
        draw=Decimal("0.30"),
        away_win=Decimal("0.25"),
    )
    return ManualQuantInput(
        input_id="manual-quant-1",
        match_id="match-1",
        market=MARKET,
        probabilities=probabilities,
        available_at_utc=BASE + timedelta(minutes=40),
        payload_hash=canonical_payload_sha256(probabilities),
    )


def _result(
    result_id: str,
    *,
    match_id: str = "match-1",
    home_goals: int,
    away_goals: int,
    available_at: datetime,
    ingested_at: datetime,
    supersedes: str | None = None,
    provider_code: str = RESULT_PROVIDER,
) -> MatchResult:
    return MatchResult(
        match_result_id=result_id,
        match_id=match_id,
        provider_code=provider_code,
        home_goals=home_goals,
        away_goals=away_goals,
        observed_at_utc=KICKOFF + timedelta(hours=2),
        available_at_utc=available_at,
        ingested_at_utc=ingested_at,
        source_result_key=f"source-{result_id}",
        payload_hash=match_result_payload_sha256(home_goals, away_goals),
        supersedes_match_result_id=supersedes,
    )


def _write_complete_archive_set(directory: Path) -> LocalArchiveStore:
    fixture_v1 = _fixture(available_at=BASE, kickoff_at=KICKOFF)
    fixture_v2 = _fixture(
        available_at=BASE + timedelta(hours=2),
        kickoff_at=KICKOFF + timedelta(hours=1),
    )
    market_v1 = _odds_snapshot(
        1,
        captured_at=BASE + timedelta(minutes=5),
        available_at=BASE + timedelta(minutes=10),
        ingested_at=BASE + timedelta(minutes=11),
    )
    market_v2 = _odds_snapshot(
        2,
        captured_at=BASE + timedelta(minutes=65),
        available_at=BASE + timedelta(minutes=70),
        ingested_at=BASE + timedelta(minutes=71),
    )
    result_v1 = _result(
        "result-v1",
        home_goals=2,
        away_goals=1,
        available_at=KICKOFF + timedelta(hours=2, minutes=5),
        ingested_at=KICKOFF + timedelta(hours=2, minutes=6),
    )
    result_v2 = _result(
        "result-v2",
        home_goals=2,
        away_goals=2,
        available_at=KICKOFF + timedelta(days=1),
        ingested_at=KICKOFF + timedelta(days=1, minutes=1),
        supersedes="result-v1",
    )

    _write_archive(
        directory,
        "fixtures.json",
        HistoricalArchiveDatasetKind.FIXTURES,
        FIXTURE_PROVIDER,
        (fixture_v1, fixture_v2),
    )
    _write_archive(
        directory,
        "fixture-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        FIXTURE_PROVIDER,
        (_mapping(FIXTURE_PROVIDER),),
    )
    _write_archive(
        directory,
        "market.json",
        HistoricalArchiveDatasetKind.MARKET_ODDS,
        MARKET_PROVIDER,
        (market_v1, market_v2),
    )
    _write_archive(
        directory,
        "market-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        MARKET_PROVIDER,
        (_mapping(MARKET_PROVIDER),),
    )
    _write_archive(
        directory,
        "sporttery.json",
        HistoricalArchiveDatasetKind.SPORTTERY_BONUS,
        SPORTTERY_PROVIDER,
        (_sporttery_snapshot(),),
    )
    _write_archive(
        directory,
        "sporttery-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        SPORTTERY_PROVIDER,
        (_mapping(SPORTTERY_PROVIDER, external_match_id="2026-08-01:S001"),),
    )
    _write_archive(
        directory,
        "quant.json",
        HistoricalArchiveDatasetKind.MANUAL_QUANT,
        QUANT_PROVIDER,
        (_manual_quant(),),
    )
    _write_archive(
        directory,
        "quant-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        QUANT_PROVIDER,
        (_mapping(QUANT_PROVIDER),),
    )
    _write_archive(
        directory,
        "results.json",
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        RESULT_PROVIDER,
        (result_v1, result_v2),
    )
    _write_archive(
        directory,
        "result-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        RESULT_PROVIDER,
        (_mapping(RESULT_PROVIDER), _mapping(RESULT_PROVIDER, "match-2")),
    )
    return LocalArchiveStore(directory)


def test_local_adapters_implement_ports_and_never_select_future_versions(
    tmp_path: Path,
) -> None:
    store = _write_complete_archive_set(tmp_path)
    fixtures = HistoricalArchiveFixtureProvider(store, FIXTURE_PROVIDER)
    market = HistoricalArchiveMarketOddsProvider(store, MARKET_PROVIDER)
    sporttery = HistoricalArchiveSportteryProvider(store, SPORTTERY_PROVIDER)
    quant = HistoricalArchiveQuantProvider(store, QUANT_PROVIDER)

    assert isinstance(fixtures, FixtureProvider)
    assert isinstance(market, MarketOddsProvider)
    assert isinstance(sporttery, SportteryProvider)
    assert isinstance(quant, ManualQuantProvider)

    early_fixture = asyncio.run(
        fixtures.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=KICKOFF - timedelta(hours=1),
                kickoff_to_utc=KICKOFF + timedelta(hours=2),
                as_of_at_utc=BASE + timedelta(hours=1),
            )
        )
    )
    late_fixture = asyncio.run(
        fixtures.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=KICKOFF - timedelta(hours=1),
                kickoff_to_utc=KICKOFF + timedelta(hours=2),
                as_of_at_utc=BASE + timedelta(hours=3),
            )
        )
    )
    assert early_fixture.matches[0].kickoff_at_utc == KICKOFF
    assert late_fixture.matches[0].kickoff_at_utc == KICKOFF + timedelta(hours=1)
    assert {mapping.provider_code for mapping in early_fixture.mappings} == {
        FIXTURE_PROVIDER
    }

    unavailable = asyncio.run(
        market.fetch_market_odds(
            SnapshotQuery(
                match_ids=("match-1",),
                as_of_at_utc=BASE + timedelta(minutes=10),
            )
        )
    )
    first = asyncio.run(
        market.fetch_market_odds(
            SnapshotQuery(
                match_ids=("match-1",),
                as_of_at_utc=BASE + timedelta(minutes=11),
            )
        )
    )
    second = asyncio.run(
        market.fetch_market_odds(
            SnapshotQuery(
                match_ids=("match-1",),
                as_of_at_utc=BASE + timedelta(minutes=71),
            )
        )
    )
    assert unavailable.snapshots == ()
    assert first.snapshots[0].snapshot_id == "market-snapshot-1"
    assert second.snapshots[0].snapshot_id == "market-snapshot-2"
    assert {mapping.provider_code for mapping in second.mappings} == {MARKET_PROVIDER}

    snapshot_query = SnapshotQuery(
        match_ids=("match-1",), as_of_at_utc=BASE + timedelta(hours=2)
    )
    bonus_batch = asyncio.run(sporttery.fetch_fixed_bonus(snapshot_query))
    quant_batch = asyncio.run(quant.fetch_manual_quant(snapshot_query))
    assert bonus_batch.snapshots[0].snapshot_id == "sporttery-snapshot-1"
    assert bonus_batch.mappings[0].provider_code == SPORTTERY_PROVIDER
    assert quant_batch.inputs[0].input_id == "manual-quant-1"


def test_result_provider_supports_empty_partial_and_correction_visibility(
    tmp_path: Path,
) -> None:
    store = _write_complete_archive_set(tmp_path)
    provider = LocalArchiveHistoricalDataProvider(store, RESULT_PROVIDER)
    assert isinstance(provider, HistoricalDataProvider)

    before_result = KICKOFF + timedelta(hours=2, minutes=5)
    v1_cutoff = KICKOFF + timedelta(hours=2, minutes=6)
    before_correction = KICKOFF + timedelta(hours=23)
    v2_cutoff = KICKOFF + timedelta(days=1, minutes=1)

    empty = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(match_ids=("match-1",), as_of_at_utc=before_result)
        )
    )
    first = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(match_ids=("match-1",), as_of_at_utc=v1_cutoff)
        )
    )
    still_first = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(match_ids=("match-1",), as_of_at_utc=before_correction)
        )
    )
    corrected = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(match_ids=("match-1", "match-2"), as_of_at_utc=v2_cutoff)
        )
    )

    assert empty.results == ()
    assert empty.mappings == ()
    assert first.results[0].match_result_id == "result-v1"
    assert still_first.results[0].match_result_id == "result-v1"
    assert corrected.results[0].match_result_id == "result-v2"
    assert corrected.results[0].supersedes_match_result_id == "result-v1"
    assert tuple(result.match_id for result in corrected.results) == ("match-1",)
    assert tuple(mapping.internal_match_id for mapping in corrected.mappings) == (
        "match-1",
    )


def test_elo_training_provider_joins_explicit_season_fixture_identity(
    tmp_path: Path,
) -> None:
    store = _write_complete_archive_set(tmp_path)
    provider = HistoricalArchiveEloTrainingProvider(
        store,
        RESULT_PROVIDER,
        fixture_provider_code=FIXTURE_PROVIDER,
        season_id="season-1",
    )
    assert isinstance(provider, EloTrainingHistoryProvider)

    first = asyncio.run(
        provider.fetch_elo_training_history(
            EloTrainingHistoryQuery(
                competition_id="competition-1",
                target_season_id="season-1",
                as_of_at_utc=KICKOFF + timedelta(hours=2, minutes=6),
            )
        )
    )
    corrected = asyncio.run(
        provider.fetch_elo_training_history(
            EloTrainingHistoryQuery(
                competition_id="competition-1",
                target_season_id="season-1",
                as_of_at_utc=KICKOFF + timedelta(days=1, minutes=1),
            )
        )
    )
    excluded = asyncio.run(
        provider.fetch_elo_training_history(
            EloTrainingHistoryQuery(
                competition_id="competition-1",
                target_season_id="season-1",
                as_of_at_utc=KICKOFF + timedelta(days=1, minutes=1),
                exclude_match_ids=("match-1",),
            )
        )
    )

    assert first.sources[0].result.match_result_id == "result-v1"
    assert first.sources[0].result.season_id == "season-1"
    assert first.sources[0].result.home_team_id == "team-home"
    assert corrected.sources[0].result.match_result_id == "result-v2"
    assert corrected.sources[0].result.supersedes_match_result_id == "result-v1"
    assert corrected.sources[0].archive.archive_id == "results"
    assert excluded.sources == ()

    with pytest.raises(MissingArchiveInputError, match="configured archive season"):
        asyncio.run(
            provider.fetch_elo_training_history(
                EloTrainingHistoryQuery(
                    competition_id="competition-1",
                    target_season_id="season-2",
                    as_of_at_utc=KICKOFF + timedelta(days=1, minutes=1),
                )
            )
        )


def test_rejects_cross_provider_mapping_and_invalid_correction_lineage(
    tmp_path: Path,
) -> None:
    result_v1 = _result(
        "result-v1",
        home_goals=1,
        away_goals=0,
        available_at=KICKOFF + timedelta(hours=2, minutes=5),
        ingested_at=KICKOFF + timedelta(hours=2, minutes=6),
    )
    _write_archive(
        tmp_path,
        "results.json",
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        RESULT_PROVIDER,
        (result_v1,),
    )
    _write_archive(
        tmp_path,
        "wrong-provider-mapping.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        "OTHER_PROVIDER",
        (_mapping("OTHER_PROVIDER"),),
    )
    with pytest.raises(ArchiveValidationError, match="same-provider mapping"):
        LocalArchiveStore(tmp_path)

    for path in tmp_path.glob("*.json"):
        path.unlink()
    wrong_match_correction = _result(
        "result-v2",
        match_id="match-2",
        home_goals=1,
        away_goals=1,
        available_at=KICKOFF + timedelta(days=1),
        ingested_at=KICKOFF + timedelta(days=1, minutes=1),
        supersedes="result-v1",
    )
    _write_archive(
        tmp_path,
        "results.json",
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        RESULT_PROVIDER,
        (result_v1, wrong_match_correction),
    )
    _write_archive(
        tmp_path,
        "result-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        RESULT_PROVIDER,
        (_mapping(RESULT_PROVIDER), _mapping(RESULT_PROVIDER, "match-2")),
    )
    with pytest.raises(ArchiveValidationError, match="different match or provider"):
        LocalArchiveStore(tmp_path)


def test_rejects_duplicate_result_version_and_reports_missing_archives(
    tmp_path: Path,
) -> None:
    result_v1 = _result(
        "result-v1",
        home_goals=1,
        away_goals=0,
        available_at=KICKOFF + timedelta(hours=2, minutes=5),
        ingested_at=KICKOFF + timedelta(hours=2, minutes=6),
    )
    duplicate = result_v1.model_copy(
        update={
            "match_result_id": "result-duplicate",
            "source_result_key": "source-result-duplicate",
            "supersedes_match_result_id": "result-v1",
        }
    )
    _write_archive(
        tmp_path,
        "results.json",
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        RESULT_PROVIDER,
        (result_v1, duplicate),
    )
    _write_archive(
        tmp_path,
        "result-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        RESULT_PROVIDER,
        (_mapping(RESULT_PROVIDER),),
    )
    with pytest.raises(ArchiveValidationError, match="duplicate match result version"):
        LocalArchiveStore(tmp_path)

    for path in tmp_path.glob("*.json"):
        path.unlink()
    _write_archive(
        tmp_path,
        "empty-results.json",
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        RESULT_PROVIDER,
        (),
    )
    with pytest.raises(MissingArchiveInputError, match="PROVIDER_MAPPINGS"):
        LocalArchiveHistoricalDataProvider(tmp_path, RESULT_PROVIDER)


def test_source_time_research_is_visible_by_source_cutoff_but_marked_retrospective(
    tmp_path: Path,
) -> None:
    observed = datetime(2024, 8, 2, 20, 0, tzinfo=UTC)
    available = observed + timedelta(minutes=5)
    imported = CREATED - timedelta(hours=1)
    result = MatchResult(
        match_result_id="research-result-1",
        match_id="match-1",
        provider_code=RESULT_PROVIDER,
        home_goals=0,
        away_goals=0,
        observed_at_utc=observed,
        available_at_utc=available,
        ingested_at_utc=available,
        source_result_key="research-source-1",
        payload_hash=match_result_payload_sha256(0, 0),
    )
    mapping = _mapping(RESULT_PROVIDER).model_copy(
        update={"available_at_utc": observed - timedelta(days=1)}
    )
    _write_archive(
        tmp_path,
        "research-results.json",
        HistoricalArchiveDatasetKind.MATCH_RESULTS,
        RESULT_PROVIDER,
        (result,),
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        imported_at=imported,
    )
    _write_archive(
        tmp_path,
        "research-mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        RESULT_PROVIDER,
        (mapping,),
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
        imported_at=imported,
    )
    provider = LocalArchiveHistoricalDataProvider(
        tmp_path,
        RESULT_PROVIDER,
        data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )

    batch = asyncio.run(
        provider.fetch_match_results(
            MatchResultQuery(match_ids=("match-1",), as_of_at_utc=available)
        )
    )

    assert batch.results == (result,)
    assert provider.retrospective is True
    assert provider.report_data_mode == "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
    assert imported > batch.as_of_at_utc
