import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
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
from football_system.domain.quant_integrity import (
    TrainingAdmissionPinV1,
    project_admitted_training_fact,
)
from football_system.domain.services.elo_baseline import EloThreeWayBaseline
from football_system.domain.settlement import MatchResult
from football_system.domain.training_admission import (
    MatchResultAdmissionV1,
    MatchSeasonMembershipV1,
    normalized_match_result_record_sha256,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    MatchRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
)
from football_system.infrastructure.files.training_evidence import (
    CapturedRecordReferenceV1,
    provider_record_sha256,
    training_review_input_sha256,
)
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
    RepositoryArchiveMembershipSource,
)
from tests.integration import test_training_admission_persistence as admission_tests

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
    created_at: datetime = CREATED,
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
            "created_at_utc": created_at.isoformat(),
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


@pytest.fixture
def membership_lane(tmp_path):
    yield from admission_tests.lane.__wrapped__(tmp_path)


def _prepare_verified_memberships(
    lane, *, seasons=("2024/25", "2025/26"), late_source=None
):
    """Synthetic contract evidence, NOT real-source approval or production data.

    Reuse the actual admission repository fixture, write explicit season fields in
    all three raw sources, capture those bytes, register matching identities, and
    admit separately pinned facts. No fake repository or self-seal stands in for
    reading raw evidence. All writes are confined to this test's temporary lane.
    """
    seed = admission_tests.seed_identities
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            admission_tests,
            "seed_identities",
            lambda context, count: seed(context, count=0),
        )
        admission_tests.prepare(lane, count=len(seasons))
    records = {
        role: json.loads((lane.root / f"{role}.json").read_bytes())["records"]
        for role in ("fixture", "scope", "result")
    }
    for index, season in enumerate(seasons):
        kickoff = admission_tests.SOURCE + timedelta(days=1 + 100 * index)
        result_at = kickoff + timedelta(hours=4)
        delayed = result_at + timedelta(days=1)
        for role, values in records.items():
            raw = values[index]
            raw["season_id"] = f"p-{season.replace('/', '-')}"
            if role != "scope":
                raw["kickoff_at_utc"] = kickoff.isoformat()
                if index % 2:
                    raw["home_team_id"], raw["away_team_id"] = "p-away", "p-home"
            if role == "result":
                raw["finalized_at_utc"] = (kickoff + timedelta(hours=2)).isoformat()
                raw["observed_at_utc"] = (kickoff + timedelta(hours=3)).isoformat()
                raw["available_at_utc"] = result_at.isoformat()
            if role == late_source or late_source == "mapping":
                raw["available_at_utc"] = delayed.isoformat()

    receipts = {}
    for role, values in records.items():
        admission_tests.write_evidence(
            lane.root, f"elo-{role}.json", {"records": values}
        )
        receipts[role] = lane.repo.capture_local_json(
            request_key=f"elo-capture-{role}",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference=f"elo-{role}.json",
        )

    reviewed = lane.clock()
    submissions = []
    with lane.sessions.begin() as session:
        for season in set(seasons) - {"2024/25"}:
            session.add(
                ProviderCompetitionMappingRecord(
                    mapping_id=f"competition-{season}",
                    internal_competition_id="league",
                    provider_id="provider",
                    provider_competition_id="p-league",
                    provider_competition_name="League",
                    language="en",
                    season=season,
                    competition_type="LEAGUE",
                    available_at_utc=admission_tests.SOURCE,
                )
            )
        for index, (item, season) in enumerate(
            zip(lane.submissions, seasons, strict=True)
        ):
            f, s, r = (records[role][index] for role in ("fixture", "scope", "result"))
            old = item.candidate
            kickoff = datetime.fromisoformat(f["kickoff_at_utc"])
            source = old.fixture_source.model_copy(
                update={
                    "fixture_source_archive_id": receipts["fixture"].capture_receipt_id,
                    "fixture_source_archive_payload_sha256": receipts[
                        "fixture"
                    ].payload_sha256,
                    "fixture_source_archive_created_at_utc": receipts[
                        "fixture"
                    ].archive_created_at_utc,
                    "fixture_record_sha256": provider_record_sha256(f),
                    "source_available_at_utc": datetime.fromisoformat(
                        f["available_at_utc"]
                    ),
                    "local_imported_at_utc": receipts["fixture"].local_imported_at_utc,
                    "registered_at_utc": receipts["fixture"].registered_at_utc,
                }
            )
            mapping = old.provider_mapping.model_copy(
                update={
                    "available_at_utc": source.source_available_at_utc
                    if late_source == "mapping"
                    else old.provider_mapping.available_at_utc,
                }
            )
            identity = old.canonical_identity.model_copy(
                update={
                    "season": season,
                    "kickoff_at_utc": kickoff,
                    "internal_home_team_id": f["home_team_id"].removeprefix("p-"),
                    "internal_away_team_id": f["away_team_id"].removeprefix("p-"),
                }
            )
            membership = MatchSeasonMembershipV1.freeze(
                content_payload=old.season_membership.content_payload.model_copy(
                    update={
                        "canonical_season_id": season,
                        "provider_season_id": s["season_id"],
                        "provider_season_candidate_ids": (s["season_id"],),
                        "fixture_record_sha256": source.fixture_record_sha256,
                        "provider_scope_raw_artifact_id": receipts[
                            "scope"
                        ].capture_receipt_id,
                        "provider_scope_payload_sha256": receipts[
                            "scope"
                        ].payload_sha256,
                        "provider_scope_created_at_utc": receipts[
                            "scope"
                        ].archive_created_at_utc,
                        "provider_scope_record_sha256": provider_record_sha256(s),
                        "source_available_at_utc": datetime.fromisoformat(
                            s["available_at_utc"]
                        ),
                        "local_imported_at_utc": receipts[
                            "scope"
                        ].local_imported_at_utc,
                        "registered_at_utc": receipts["scope"].registered_at_utc,
                        "reviewed_at_utc": reviewed,
                    }
                )
            )
            result = old.normalized_result.model_copy(
                update={
                    "observed_at_utc": datetime.fromisoformat(r["observed_at_utc"]),
                    "available_at_utc": datetime.fromisoformat(r["available_at_utc"]),
                    "ingested_at_utc": datetime.fromisoformat(r["available_at_utc"]),
                }
            )
            result_admission = MatchResultAdmissionV1.freeze(
                content_payload=old.match_result_admission.content_payload.model_copy(
                    update={
                        "provider_finalized_at_utc": datetime.fromisoformat(
                            r["finalized_at_utc"]
                        ),
                        "source_observed_at_utc": result.observed_at_utc,
                        "source_available_at_utc": result.available_at_utc,
                        "raw_artifact_id": receipts["result"].capture_receipt_id,
                        "raw_artifact_payload_sha256": receipts[
                            "result"
                        ].payload_sha256,
                        "raw_artifact_created_at_utc": receipts[
                            "result"
                        ].archive_created_at_utc,
                        "raw_record_sha256": provider_record_sha256(r),
                        "normalized_record_sha256": normalized_match_result_record_sha256(
                            result
                        ),
                        "local_imported_at_utc": receipts[
                            "result"
                        ].local_imported_at_utc,
                        "registered_at_utc": receipts["result"].registered_at_utc,
                        "reviewed_at_utc": reviewed,
                    }
                )
            )
            candidate = old.model_copy(
                update={
                    "fixture_source": source,
                    "provider_mapping": mapping,
                    "canonical_identity": identity,
                    "season_membership": membership,
                    "normalized_result": result,
                    "match_result_admission": result_admission,
                }
            )
            evidence = item.source_evidence.model_copy(
                update={
                    **{
                        role: CapturedRecordReferenceV1(
                            capture_receipt_id=receipt.capture_receipt_id,
                            record_pointer=f"/records/{index}",
                        )
                        for role, receipt in receipts.items()
                    },
                    "home_team_alias_id": f"alias-{identity.internal_home_team_id}",
                    "away_team_alias_id": f"alias-{identity.internal_away_team_id}",
                    "competition_mapping_id": "competition-mapping"
                    if season == "2024/25"
                    else f"competition-{season}",
                }
            )
            review = admission_tests.write_evidence(
                lane.root,
                f"elo-review-{index}.json",
                admission_tests.review_document(
                    schema="TRAINING_FACT_REVIEW_INPUT_V1",
                    digest=training_review_input_sha256(candidate, evidence),
                    at=reviewed,
                ),
            )
            submissions.append(
                item.model_copy(
                    update={
                        "candidate": candidate,
                        "source_evidence": evidence,
                        "reviewer_evidence": review,
                    }
                )
            )
            session.add(
                MatchRecord(
                    internal_match_id=identity.internal_match_id,
                    competition_id="league",
                    home_team_id=identity.internal_home_team_id,
                    away_team_id=identity.internal_away_team_id,
                    kickoff_at_utc=kickoff,
                    status="FINISHED",
                    available_at_utc=source.source_available_at_utc,
                    created_at_utc=lane.clock.value,
                )
            )
            session.flush()
            session.add(
                CanonicalMatchIdentityRecord(
                    internal_match_id=identity.internal_match_id,
                    season=season,
                    competition_type="LEAGUE",
                    available_at_utc=source.source_available_at_utc,
                )
            )
            session.add(
                ProviderMatchMappingRecord(
                    mapping_id=mapping.mapping_id,
                    provider_id="provider",
                    external_namespace=mapping.external_namespace,
                    external_match_id=mapping.external_match_id,
                    internal_match_id=mapping.internal_match_id,
                    resolution_method=mapping.resolution_method,
                    confidence=mapping.confidence,
                    available_at_utc=mapping.available_at_utc,
                )
            )

    lane.submissions = tuple(submissions)
    lane.admissions = tuple(
        admission_tests.admit(lane, submissions=(item,), key=f"elo-admit-{i}")
        for i, item in enumerate(submissions)
    )
    lane.archive_root = lane.root / "archives"
    lane.archive_root.mkdir()
    _write_membership_archives(lane)
    lane.memberships = RepositoryArchiveMembershipSource(
        lane.repo,
        tuple(
            sorted(
                (TrainingAdmissionPinV1.from_admission(a) for a in lane.admissions),
                key=lambda pin: pin.training_fact_admission_id,
            )
        ),
        lane.clock.value + timedelta(seconds=3),
    )
    return lane


def _write_membership_archives(
    lane, *, data_mode=HistoricalDataMode.SOURCE_TIME_RESEARCH
):
    fixtures = []
    for item in lane.submissions:
        candidate = item.candidate
        identity = candidate.canonical_identity
        competition = Competition(
            competition_id="league",
            canonical_key="league",
            name="League",
            country_code="TST",
        )
        home = Team(
            team_id=identity.internal_home_team_id,
            canonical_key=identity.internal_home_team_id,
            name=identity.internal_home_team_id,
        )
        away = Team(
            team_id=identity.internal_away_team_id,
            canonical_key=identity.internal_away_team_id,
            name=identity.internal_away_team_id,
        )
        fixtures.append(
            FixtureArchivePayload(
                competition=competition,
                home_team=home,
                away_team=away,
                match=Match(
                    match_id=identity.internal_match_id,
                    competition_id="league",
                    home_team_id=home.team_id,
                    away_team_id=away.team_id,
                    kickoff_at_utc=identity.kickoff_at_utc,
                    status=MatchStatus.FINISHED,
                    available_at_utc=candidate.fixture_source.source_available_at_utc,
                ),
            )
        )
    for name, kind, payloads in (
        ("fixtures", HistoricalArchiveDatasetKind.FIXTURES, tuple(reversed(fixtures))),
        (
            "mappings",
            HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
            tuple(item.candidate.provider_mapping for item in lane.submissions),
        ),
        (
            "results",
            HistoricalArchiveDatasetKind.MATCH_RESULTS,
            tuple(
                item.candidate.normalized_result for item in reversed(lane.submissions)
            ),
        ),
    ):
        _write_archive(
            lane.archive_root,
            f"{name}.json",
            kind,
            "PROVIDER",
            payloads,
            data_mode=data_mode,
            imported_at=lane.clock.value + timedelta(seconds=1)
            if data_mode.is_retrospective
            else None,
            created_at=lane.clock.value + timedelta(seconds=2),
        )


def _elo_provider(lane, **overrides):
    return HistoricalArchiveEloTrainingProvider(
        lane.archive_root,
        "PROVIDER",
        **{
            "membership_source": lane.memberships,
            "ordered_season_ids": ("2024/25", "2025/26", "2026/27"),
            **overrides,
        },
    )


def _elo_history(
    provider,
    *,
    cutoff=admission_tests.LOCAL,
    target="2025/26",
    excluded=(),
    competition="league",
):
    return asyncio.run(
        provider.fetch_elo_training_history(
            EloTrainingHistoryQuery(
                competition_id=competition,
                target_season_id=target,
                as_of_at_utc=cutoff,
                exclude_match_ids=excluded,
            )
        )
    )


def _edit_test_archive(lane, name, change):
    path = lane.archive_root / f"{name}.json"
    raw = json.loads(path.read_bytes())
    change(raw)
    raw["manifest"]["record_count"] = len(raw["records"])
    raw["manifest"]["payload_sha256"] = archive_payload_sha256(raw["records"])
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_elo_training_provider_joins_explicit_season_fixture_identity(membership_lane):
    lane = _prepare_verified_memberships(membership_lane)
    provider = _elo_provider(lane)
    before = {p: p.read_bytes() for p in lane.root.rglob("*") if p.is_file()}
    assert isinstance(provider, EloTrainingHistoryProvider)
    batch = _elo_history(provider)
    expected = tuple(
        project_admitted_training_fact(a.facts[0]) for a in lane.admissions
    )
    assert tuple(s.result for s in batch.sources) == expected
    assert tuple(s.result.season_id for s in batch.sources) == ("2024/25", "2025/26")
    assert tuple(s.result.home_team_id for s in batch.sources) == ("home", "away")
    assert batch.competition_id == "league"
    assert all(s.archive.archive_id == "results" for s in batch.sources)
    assert _elo_history(provider, competition="another-league").sources == ()
    assert _elo_history(provider, excluded=("match-0", "match-1")).sources == ()
    assert tuple(
        s.result.match_id for s in _elo_history(provider, excluded=("match-0",)).sources
    ) == ("match-1",)
    assert {p: p.read_bytes() for p in lane.root.rglob("*") if p.is_file()} == before


def test_elo_two_seasons_and_final_target_transition_leave_fixed_math_unchanged(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane)
    provider = _elo_provider(lane)
    current = _elo_history(provider)
    following = _elo_history(provider, target="2026/27")
    assert following.sources == current.sources
    elo = EloThreeWayBaseline()
    assert elo.config.season_regression_factor == Decimal("0.75")
    results = tuple(s.result for s in current.sources)
    old = elo.rebuild_state(
        results, current.as_of_at_utc, target_season_id=current.target_season_id
    )
    new = elo.rebuild_state(
        results, following.as_of_at_utc, target_season_id=following.target_season_id
    )
    assert old.training_facts == new.training_facts
    assert tuple(f.season_id for f in new.training_facts) == ("2024/25", "2025/26")
    for before, after in zip(old.teams, new.teams, strict=True):
        assert after.rating == (
            Decimal(1500) + Decimal("0.75") * (before.rating - 1500)
        ).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        assert before.prior_matches == after.prior_matches == 2
    relabeled = tuple(r.model_copy(update={"season_id": "2025/26"}) for r in results)
    flattened = elo.rebuild_state(
        relabeled, current.as_of_at_utc, target_season_id="2025/26"
    )
    assert old.teams != flattened.teams
    with pytest.raises(ArchiveValidationError, match="target season precedes"):
        _elo_history(provider, target="2024/25")


def test_elo_constructor_season_is_only_a_verified_assertion_not_a_target_override(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane, seasons=("2024/25",))
    provider = _elo_provider(lane, season_id="2024/25")
    assert (
        _elo_history(provider, target="2024/25").sources
        == _elo_history(provider, target="2025/26").sources
    )
    with pytest.raises(ArchiveValidationError, match="season_id assertion"):
        _elo_provider(lane, season_id="2025/26")
    with pytest.raises(
        MissingArchiveInputError, match="explicit verified membership_source"
    ):
        HistoricalArchiveEloTrainingProvider(
            lane.archive_root, "PROVIDER", season_id="2025/26"
        )
    with pytest.raises(
        MissingArchiveInputError, match="explicit verified membership_source"
    ):
        _elo_provider(lane, membership_source={"match-0": "2025/26"})
    with pytest.raises(
        MissingArchiveInputError, match="byte-verifying admission repository"
    ):
        _elo_provider(
            lane,
            membership_source=replace(
                lane.memberships,
                repository={
                    lane.admissions[0].training_fact_admission_id: lane.admissions[0],
                },
            ),
        )


def test_elo_obsolete_live_archive_constructor_season_call_fails_closed(tmp_path):
    store = _write_complete_archive_set(tmp_path)
    with pytest.raises(
        MissingArchiveInputError, match="constructor season labels are not evidence"
    ):
        HistoricalArchiveEloTrainingProvider(
            store,
            RESULT_PROVIDER,
            fixture_provider_code=FIXTURE_PROVIDER,
            season_id="season-1",
        )


def test_elo_missing_membership_never_falls_back_to_constructor_or_target(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane)
    source = replace(
        lane.memberships,
        admissions=(TrainingAdmissionPinV1.from_admission(lane.admissions[0]),),
    )
    provider = _elo_provider(lane, membership_source=source, season_id="2024/25")
    with pytest.raises(MissingArchiveInputError, match="no pinned verified membership"):
        _elo_history(provider)
    assert len(_elo_history(provider, excluded=("match-1",)).sources) == 1


@pytest.mark.parametrize("role", ["fixture", "scope", "result"])
def test_elo_reads_raw_bytes_again_even_after_construction(membership_lane, role):
    lane = _prepare_verified_memberships(membership_lane)
    provider = _elo_provider(lane)
    with (lane.root / f"elo-{role}.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        _elo_history(provider, excluded=("match-0", "match-1"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_result_key", "wrong-raw-result"),
        ("observed_at_utc", "2025-08-26T02:00:00Z"),
        ("available_at_utc", "2025-08-26T03:30:00Z"),
        ("home_goals", 0),
    ],
)
def test_elo_rejects_resealed_archive_result_payload_mismatch(
    membership_lane, field, value
):
    lane = _prepare_verified_memberships(membership_lane)

    def change(raw):
        result = raw["records"][0]["payload"]
        result[field] = value
        if field == "available_at_utc":
            result["ingested_at_utc"] = value
        result["payload_hash"] = match_result_payload_sha256(
            result["home_goals"], result["away_goals"]
        )

    _edit_test_archive(lane, "results", change)
    with pytest.raises(ArchiveValidationError, match="normalized result/hash"):
        _elo_history(_elo_provider(lane))


@pytest.mark.parametrize(
    "field", ["competition", "homeaway", "kickoff_at_utc", "available_at_utc"]
)
def test_elo_rejects_wrong_archive_fixture_identity_or_source_time(
    membership_lane, field
):
    lane = _prepare_verified_memberships(membership_lane)

    def change(raw):
        fixture = raw["records"][0]["payload"]
        match = fixture["match"]
        if field == "competition":
            match["competition_id"] = "another-league"
            fixture["competition"].update(
                competition_id="another-league", canonical_key="another-league"
            )
        elif field == "homeaway":
            match["home_team_id"], match["away_team_id"] = (
                match["away_team_id"],
                match["home_team_id"],
            )
            fixture["home_team"], fixture["away_team"] = (
                fixture["away_team"],
                fixture["home_team"],
            )
        else:
            match[field] = (
                datetime.fromisoformat(match[field]) + timedelta(minutes=1)
            ).isoformat()

    _edit_test_archive(lane, "fixtures", change)
    with pytest.raises(ArchiveValidationError, match="archive fixture"):
        _elo_history(_elo_provider(lane))


@pytest.mark.parametrize("role", ["fixture", "scope", "mapping", "result"])
def test_elo_cutoffs_are_per_source_not_import_time_or_rewritten_result_time(
    membership_lane, role
):
    lane = _prepare_verified_memberships(
        membership_lane, seasons=("2024/25",), late_source=role
    )
    provider = _elo_provider(lane)
    fact = lane.admissions[0].facts[0]
    binding = fact.content_payload
    boundary = max(
        binding.fixture_source.source_available_at_utc,
        binding.season_membership.content_payload.source_available_at_utc,
        binding.provider_mapping.available_at_utc,
        binding.normalized_result.available_at_utc,
    )
    assert (
        _elo_history(provider, cutoff=boundary - timedelta(microseconds=1)).sources
        == ()
    )
    visible = _elo_history(provider, cutoff=boundary).sources
    assert len(visible) == 1
    assert visible[0].result == project_admitted_training_fact(fact)
    assert boundary < binding.fixture_source.local_imported_at_utc
    if role not in {"result", "mapping"}:
        assert visible[0].result.available_at_utc < boundary
        assert visible[0].result.ingested_at_utc < boundary


def test_elo_rejects_future_pins_wrong_pins_ambiguous_pins_and_query_after_verification(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane)
    pin = lane.memberships.admissions[0]
    for source, error in (
        (
            replace(
                lane.memberships,
                verified_at_utc=pin.persisted_at_utc - timedelta(seconds=1),
            ),
            "not yet persisted",
        ),
        (replace(lane.memberships, admissions=(pin, pin)), "sorted unique"),
        (
            replace(
                lane.memberships,
                admissions=(pin.model_copy(update={"admission_hash": "0" * 64}),),
            ),
            "exact pin",
        ),
    ):
        with pytest.raises(ArchiveValidationError, match=error):
            _elo_provider(lane, membership_source=source)
    with pytest.raises(
        ArchiveValidationError, match="cannot follow membership verification"
    ):
        _elo_history(
            _elo_provider(lane),
            cutoff=lane.memberships.verified_at_utc + timedelta(seconds=1),
        )


@pytest.mark.parametrize("kind", ["ambiguous", "wrong-key", "future-time"])
def test_elo_rejects_wrong_and_ambiguous_archive_mapping(membership_lane, kind):
    lane = _prepare_verified_memberships(membership_lane)

    def change(raw):
        if kind == "ambiguous":
            other = json.loads(json.dumps(raw["records"][0]))
            other["payload"].update(
                mapping_id="other-mapping", external_match_id="other-fixture"
            )
            raw["records"].append(other)
        elif kind == "wrong-key":
            raw["records"][0]["payload"]["external_match_id"] = "another-fixture"
        else:
            raw["records"][0]["payload"]["available_at_utc"] = (
                admission_tests.LOCAL + timedelta(seconds=1)
            ).isoformat()

    _edit_test_archive(lane, "mappings", change)
    with pytest.raises(ArchiveValidationError, match="mapping is ambiguous"):
        _elo_history(_elo_provider(lane))


def test_elo_rejects_wrong_provider_and_mode_mixing_without_relabeling_live(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane)
    provider = _elo_provider(lane)
    assert provider.data_mode is HistoricalDataMode.SOURCE_TIME_RESEARCH
    assert provider.retrospective is True
    assert provider.report_data_mode == "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
    assert provider.runtime_provenance.environment.value == "research"
    with pytest.raises(
        MissingArchiveInputError, match="does not match the loaded archive store"
    ):
        HistoricalArchiveEloTrainingProvider(
            LocalArchiveStore(lane.archive_root),
            "PROVIDER",
            membership_source=lane.memberships,
            ordered_season_ids=("2024/25", "2025/26"),
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )
    _write_membership_archives(lane, data_mode=HistoricalDataMode.LIVE_STRICT)
    assert (
        LocalArchiveHistoricalDataProvider(
            lane.archive_root, "PROVIDER"
        ).report_data_mode
        == "LIVE_STRICT"
    )
    with pytest.raises(ArchiveValidationError, match="data modes cannot be mixed"):
        _elo_provider(lane)
    _write_membership_archives(lane)
    for name in ("fixtures", "results", "mappings"):

        def change(raw):
            raw["manifest"]["provider_code"] = "OTHER"
            for record in raw["records"]:
                if "provider_code" in record["payload"]:
                    record["payload"]["provider_code"] = "OTHER"

        _edit_test_archive(lane, name, change)
    with pytest.raises(ArchiveValidationError, match="wrong archive provider"):
        HistoricalArchiveEloTrainingProvider(
            lane.archive_root,
            "OTHER",
            membership_source=lane.memberships,
            ordered_season_ids=("2024/25", "2025/26"),
        )


def test_elo_rejects_interleaved_season_blocks_without_reordering_facts(
    membership_lane,
):
    lane = _prepare_verified_memberships(
        membership_lane, seasons=("2024/25", "2025/26", "2024/25")
    )
    with pytest.raises(ValueError, match="contiguous chronological blocks"):
        _elo_history(_elo_provider(lane))


def test_elo_visible_correction_requires_context_and_never_falls_back(membership_lane):
    lane = _prepare_verified_memberships(membership_lane)

    def change(raw):
        old = raw["records"][0]["payload"]
        corrected = json.loads(json.dumps(raw["records"][0]))
        result = corrected["payload"]
        later = (
            datetime.fromisoformat(old["available_at_utc"]) + timedelta(days=1)
        ).isoformat()
        result.update(
            match_result_id="correction",
            source_result_key="correction",
            supersedes_match_result_id=old["match_result_id"],
            available_at_utc=later,
            ingested_at_utc=later,
        )
        raw["records"].append(corrected)

    _edit_test_archive(lane, "results", change)
    provider = _elo_provider(lane)
    original_at = lane.submissions[1].candidate.normalized_result.available_at_utc
    assert len(_elo_history(provider, cutoff=original_at).sources) == 2
    with pytest.raises(
        ControlledTrainingCorrectionRequired, match="correction context"
    ):
        _elo_history(provider)
    assert len(_elo_history(provider, excluded=("match-1",)).sources) == 1


@pytest.mark.parametrize("trainable", [True, False])
def test_membership_reader_rejects_audit_roots_with_registered_corrections(
    membership_lane, trainable
):
    from tests.integration import test_training_corrections as correction_tests

    lane = correction_tests.corrected_lane.__wrapped__(membership_lane)
    correction_tests.record(
        lane,
        correction_tests.reviewed_intent(
            lane,
            raw=None if trainable else {"status": "CANCELLED"},
            trainable=trainable,
        ),
    )
    assert (
        lane.repo.load(lane.base_admission.training_fact_admission_id)
        == lane.base_admission
    )
    source = RepositoryArchiveMembershipSource(
        lane.repo,
        (TrainingAdmissionPinV1.from_admission(lane.base_admission),),
        lane.clock(),
    )
    with pytest.raises(
        ControlledTrainingCorrectionRequired, match="explicit correction context"
    ):
        source.load_facts()


def test_elo_rereads_normalized_archives_and_rejects_even_resealed_changes(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane)
    provider = _elo_provider(lane)
    _edit_test_archive(
        lane, "results", lambda raw: raw["manifest"].update(source_reference="changed")
    )
    with pytest.raises(ArchiveValidationError, match="archive changed"):
        _elo_history(provider)


@pytest.mark.parametrize("name", ["results", "fixtures", "mappings"])
def test_elo_missing_archive_prerequisite_cannot_silently_drop_a_pinned_fact(
    membership_lane, name
):
    lane = _prepare_verified_memberships(membership_lane)
    _edit_test_archive(lane, name, lambda raw: raw["records"].pop(0))
    with pytest.raises(
        (MissingArchiveInputError, ArchiveValidationError),
        match="missing|no archive fixture|same-provider mapping",
    ):
        _elo_history(_elo_provider(lane))


def test_elo_retiming_archive_result_into_future_cannot_hide_a_verified_fact(
    membership_lane,
):
    lane = _prepare_verified_memberships(membership_lane)
    result = lane.submissions[1].candidate.normalized_result

    def change(raw):
        later = (result.available_at_utc + timedelta(days=1)).isoformat()
        raw["records"][0]["payload"].update(
            available_at_utc=later, ingested_at_utc=later
        )

    _edit_test_archive(lane, "results", change)
    with pytest.raises(MissingArchiveInputError, match="verified result is missing"):
        _elo_history(_elo_provider(lane), cutoff=result.available_at_utc)


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
