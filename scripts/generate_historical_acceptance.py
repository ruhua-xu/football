from __future__ import annotations

# ruff: noqa: E402

import hashlib
import json
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
    archive_payload_sha256,
    canonical_payload_sha256,
    match_result_payload_sha256,
)
from football_system.domain.market import (
    MarketKey,
    MarketType,
    SelectionKey,
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
    LocalArchiveStore,
)

UTC = timezone.utc
OUTPUT_DIRECTORY = ROOT / "data" / "fixtures" / "historical_acceptance"

PROVIDER_CODE = "SYNTHETIC_ACCEPTANCE_V1"
MARKET_BOOKMAKER_CODE = "CONSENSUS"
CLASSIFICATION = "SYNTHETIC_ACCEPTANCE_DATA"
PERFORMANCE_WARNING = "NOT REAL HISTORICAL PERFORMANCE"
MANIFEST_LABEL = f"{CLASSIFICATION} / {PERFORMANCE_WARNING}"
CREATED_AT_UTC = datetime(2025, 1, 20, 12, 0, tzinfo=UTC)
MARKET = MarketKey(market_type=MarketType.THREE_WAY)

SLATE_COUNT = 10
MATCHES_PER_SLATE = 6
BUDGET_FEN = 10_000
MIN_SELECTION_EV = Decimal("0.05")
MIN_TICKET_ROI = Decimal("0.05")
NO_BET_SLATE_INDEX = 6

KICKOFF_TIMES = (
    time(13, 0),
    time(14, 45),
    time(16, 30),
    time(18, 15),
    time(20, 0),
    time(21, 45),
)
OUTCOMES = (
    SelectionKey.HOME_WIN,
    SelectionKey.DRAW,
    SelectionKey.AWAY_WIN,
)
SCORES = {
    SelectionKey.HOME_WIN: (2, 1),
    SelectionKey.DRAW: (1, 1),
    SelectionKey.AWAY_WIN: (0, 2),
}


@dataclass(frozen=True, slots=True)
class SlatePlan:
    slate_id: str
    slate_date: date
    decision_as_of_at_utc: datetime
    evaluation_as_of_at_utc: datetime
    kickoff_from_utc: datetime
    kickoff_to_utc: datetime
    match_ids: tuple[str, ...]
    evaluation_outcomes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchiveInventory:
    filename: str
    dataset_kind: HistoricalArchiveDatasetKind
    record_count: int
    payload_sha256: str
    file_sha256: str


def _decimal_triplet(values: tuple[str, str, str]) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) for value in values)


VALUE_PROFILES = (
    (
        _decimal_triplet(("1.85", "3.60", "4.60")),
        _decimal_triplet(("1.90", "3.30", "4.30")),
        _decimal_triplet(("0.60", "0.24", "0.16")),
    ),
    (
        _decimal_triplet(("2.65", "3.15", "2.90")),
        _decimal_triplet(("2.55", "3.00", "3.20")),
        _decimal_triplet(("0.34", "0.40", "0.26")),
    ),
    (
        _decimal_triplet(("3.25", "3.45", "2.15")),
        _decimal_triplet(("3.10", "3.20", "2.20")),
        _decimal_triplet(("0.24", "0.25", "0.51")),
    ),
    (
        _decimal_triplet(("2.05", "3.35", "3.80")),
        _decimal_triplet(("2.05", "3.15", "3.55")),
        _decimal_triplet(("0.56", "0.26", "0.18")),
    ),
    (
        _decimal_triplet(("2.80", "3.00", "2.85")),
        _decimal_triplet(("2.70", "2.90", "3.00")),
        _decimal_triplet(("0.31", "0.42", "0.27")),
    ),
    (
        _decimal_triplet(("3.55", "3.25", "2.10")),
        _decimal_triplet(("3.25", "3.05", "2.20")),
        _decimal_triplet(("0.22", "0.28", "0.50")),
    ),
)

NO_BET_PROFILES = (
    (
        _decimal_triplet(("2.10", "3.25", "3.75")),
        _decimal_triplet(("2.05", "3.10", "3.55")),
        _decimal_triplet(("0.46", "0.29", "0.25")),
    ),
    (
        _decimal_triplet(("2.65", "2.95", "3.25")),
        _decimal_triplet(("2.55", "2.85", "3.10")),
        _decimal_triplet(("0.36", "0.34", "0.30")),
    ),
    (
        _decimal_triplet(("3.30", "3.30", "2.25")),
        _decimal_triplet(("3.15", "3.15", "2.30")),
        _decimal_triplet(("0.29", "0.29", "0.42")),
    ),
)


def _three_way_probability(values: tuple[Decimal, ...]) -> ThreeWayProbability:
    return ThreeWayProbability(
        home_win=values[0],
        draw=values[1],
        away_win=values[2],
    )


def _three_way_odds(values: tuple[Decimal, ...]) -> ThreeWayMarketOdds:
    return ThreeWayMarketOdds(
        home_win=values[0],
        draw=values[1],
        away_win=values[2],
    )


def _three_way_bonus(values: tuple[Decimal, ...]) -> ThreeWayFixedBonus:
    return ThreeWayFixedBonus(
        home_win=values[0],
        draw=values[1],
        away_win=values[2],
    )


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _record(payload: BaseModel) -> dict[str, object]:
    return {
        "retrospective": False,
        "imported_at_utc": None,
        "payload": payload.model_dump(mode="json"),
    }


def _write_json(path: Path, value: object) -> tuple[str, str]:
    text = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_archive(
    directory: Path,
    filename: str,
    dataset_kind: HistoricalArchiveDatasetKind,
    payloads: tuple[BaseModel, ...],
    *,
    archive_suffix: str = "v1",
) -> ArchiveInventory:
    records = [_record(payload) for payload in payloads]
    payload_sha256 = archive_payload_sha256(records)
    document = {
        "manifest": {
            "archive_schema_version": HISTORICAL_ARCHIVE_SCHEMA_VERSION,
            "archive_id": (
                f"historical-acceptance-{Path(filename).stem}-{archive_suffix}"
            ),
            "provider_code": PROVIDER_CODE,
            "dataset_kind": dataset_kind.value,
            "created_at_utc": _timestamp(CREATED_AT_UTC),
            "source_reference": f"synthetic-acceptance://{filename}",
            "source_description": (
                f"{MANIFEST_LABEL}. Deterministic static acceptance fixture."
            ),
            "license_note": MANIFEST_LABEL,
            "data_mode": HistoricalDataMode.LIVE_STRICT.value,
            "payload_sha256": payload_sha256,
            "record_count": len(records),
        },
        "records": records,
    }
    _, file_sha256 = _write_json(directory / filename, document)
    return ArchiveInventory(
        filename=filename,
        dataset_kind=dataset_kind,
        record_count=len(records),
        payload_sha256=payload_sha256,
        file_sha256=file_sha256,
    )


def _market_snapshot(
    match_id: str,
    snapshot_id: str,
    values: tuple[Decimal, ...],
    *,
    bookmaker_code: str,
    captured_at_utc: datetime,
    available_at_utc: datetime,
    ingested_at_utc: datetime,
) -> MarketOddsSnapshot:
    odds = _three_way_odds(values)
    return MarketOddsSnapshot(
        snapshot_id=snapshot_id,
        match_id=match_id,
        provider_code=PROVIDER_CODE,
        bookmaker_code=bookmaker_code,
        market=MARKET,
        quotes=tuple(
            OddsQuote(selection=selection, odds=value)
            for selection, value in odds.items()
        ),
        captured_at_utc=captured_at_utc,
        available_at_utc=available_at_utc,
        ingested_at_utc=ingested_at_utc,
        source_snapshot_key=f"source-{snapshot_id}",
        payload_hash=canonical_payload_sha256(odds),
    )


def _sporttery_snapshot(
    match_id: str,
    snapshot_id: str,
    sporttery_match_no: str,
    values: tuple[Decimal, ...],
    *,
    captured_at_utc: datetime,
    available_at_utc: datetime,
    ingested_at_utc: datetime,
) -> SportteryBonusSnapshot:
    bonus = _three_way_bonus(values)
    return SportteryBonusSnapshot(
        snapshot_id=snapshot_id,
        match_id=match_id,
        provider_code=PROVIDER_CODE,
        sporttery_match_no=sporttery_match_no,
        market=MARKET,
        quotes=tuple(
            FixedBonusQuote(selection=selection, fixed_bonus=value)
            for selection, value in bonus.items()
        ),
        sale_status=SaleStatus.OPEN,
        captured_at_utc=captured_at_utc,
        available_at_utc=available_at_utc,
        ingested_at_utc=ingested_at_utc,
        source_snapshot_key=f"source-{snapshot_id}",
        payload_hash=canonical_payload_sha256(bonus),
    )


def _manual_quant_input(
    match_id: str,
    input_id: str,
    values: tuple[Decimal, ...],
    available_at_utc: datetime,
) -> ManualQuantInput:
    probabilities = _three_way_probability(values)
    return ManualQuantInput(
        input_id=input_id,
        match_id=match_id,
        market=MARKET,
        probabilities=probabilities,
        available_at_utc=available_at_utc,
        payload_hash=canonical_payload_sha256(probabilities),
    )


def _special_ids(slates: tuple[SlatePlan, ...]) -> dict[str, str]:
    return {
        "market_future_version_match_id": slates[0].match_ids[0],
        "sporttery_future_version_match_id": slates[0].match_ids[1],
        "quant_future_version_match_id": slates[0].match_ids[2],
        "fixture_future_version_match_id": slates[0].match_ids[3],
        "odds_exactly_at_cutoff_match_id": slates[3].match_ids[0],
        "odds_after_cutoff_match_id": slates[4].match_ids[5],
        "result_correction_match_id": slates[2].match_ids[1],
        "missing_result_match_id": slates[9].match_ids[5],
    }


def _build_dataset() -> tuple[
    tuple[SlatePlan, ...],
    dict[HistoricalArchiveDatasetKind, tuple[BaseModel, ...]],
    dict[str, object],
]:
    competition = Competition(
        competition_id="ha-competition-1",
        canonical_key="competition:synthetic-acceptance",
        name="Synthetic Acceptance League",
        country_code="ZZ",
    )
    teams = tuple(
        Team(
            team_id=f"ha-team-{number:02d}",
            canonical_key=f"team:synthetic-acceptance:{number:02d}",
            name=f"Acceptance Team {number:02d}",
        )
        for number in range(1, 13)
    )

    fixture_payloads: list[BaseModel] = []
    market_payloads: list[BaseModel] = []
    sporttery_payloads: list[BaseModel] = []
    quant_payloads: list[BaseModel] = []
    result_payloads: list[BaseModel] = []
    mapping_payloads: list[BaseModel] = []
    raw_slates: list[dict[str, object]] = []
    matches_by_id: dict[str, Match] = {}
    profile_by_match: dict[
        str, tuple[tuple[Decimal, ...], tuple[Decimal, ...], tuple[Decimal, ...]]
    ] = {}

    first_day = date(2025, 1, 6)
    for slate_index in range(SLATE_COUNT):
        slate_date = first_day + timedelta(days=slate_index)
        day_start = datetime.combine(slate_date, time.min, tzinfo=UTC)
        decision = day_start + timedelta(hours=9)
        evaluation = day_start + timedelta(days=1, hours=3)
        match_ids: list[str] = []

        for match_index, kickoff_time in enumerate(KICKOFF_TIMES):
            match_id = f"ha-{slate_date:%Y%m%d}-{match_index + 1:02d}"
            match_ids.append(match_id)
            home_index = (slate_index + match_index * 2) % len(teams)
            away_index = (home_index + 1 + slate_index % 3) % len(teams)
            kickoff = datetime.combine(slate_date, kickoff_time, tzinfo=UTC)
            match = Match(
                match_id=match_id,
                competition_id=competition.competition_id,
                home_team_id=teams[home_index].team_id,
                away_team_id=teams[away_index].team_id,
                kickoff_at_utc=kickoff,
                status=MatchStatus.SCHEDULED,
                available_at_utc=decision - timedelta(days=2, hours=1),
            )
            matches_by_id[match_id] = match
            fixture_payloads.append(
                _fixture_payload(
                    competition,
                    teams[home_index],
                    teams[away_index],
                    match,
                )
            )

            sporttery_match_no = f"A{slate_date:%y%m%d}{match_index + 1:02d}"
            mapping_payloads.append(
                ProviderMatchMapping(
                    mapping_id=f"ha-mapping-{match_id}",
                    provider_code=PROVIDER_CODE,
                    external_namespace="synthetic-acceptance-sporttery",
                    external_match_id=sporttery_match_no,
                    internal_match_id=match_id,
                    resolution_method="SYNTHETIC_EXACT",
                    confidence=Decimal("1.000000000000"),
                    available_at_utc=decision - timedelta(days=3),
                )
            )

            if slate_index == NO_BET_SLATE_INDEX:
                profile = NO_BET_PROFILES[match_index % len(NO_BET_PROFILES)]
            else:
                profile = VALUE_PROFILES[match_index]
            if slate_index == 1 and match_index == 0:
                profile = (
                    _decimal_triplet(("1.28", "5.80", "9.50")),
                    _decimal_triplet(("1.34", "5.50", "8.80")),
                    _decimal_triplet(("0.82", "0.12", "0.06")),
                )
            elif slate_index == 3 and match_index == 5:
                profile = (
                    _decimal_triplet(("6.80", "4.60", "1.42")),
                    _decimal_triplet(("6.20", "4.20", "1.50")),
                    _decimal_triplet(("0.10", "0.12", "0.78")),
                )
            profile_by_match[match_id] = profile

        raw_slates.append(
            {
                "slate_id": f"ha-slate-{slate_index + 1:02d}",
                "slate_date": slate_date,
                "decision": decision,
                "evaluation": evaluation,
                "match_ids": tuple(match_ids),
            }
        )

    provisional_slates = tuple(
        SlatePlan(
            slate_id=str(item["slate_id"]),
            slate_date=item["slate_date"],
            decision_as_of_at_utc=item["decision"],
            evaluation_as_of_at_utc=item["evaluation"],
            kickoff_from_utc=datetime.combine(
                item["slate_date"], time(12, 0), tzinfo=UTC
            ),
            kickoff_to_utc=datetime.combine(
                item["slate_date"], time(23, 0), tzinfo=UTC
            ),
            match_ids=item["match_ids"],
            evaluation_outcomes=(),
        )
        for item in raw_slates
    )
    special_ids = _special_ids(provisional_slates)

    correction_visible_at: datetime | None = None
    future_visibility: dict[str, datetime] = {}
    final_slates: list[SlatePlan] = []
    global_match_index = 0
    for slate_index, slate in enumerate(provisional_slates):
        evaluation_outcomes: list[str] = []
        for match_index, match_id in enumerate(slate.match_ids):
            decision = slate.decision_as_of_at_utc
            match = matches_by_id[match_id]
            odds_values, bonus_values, quant_values = profile_by_match[match_id]

            captured = decision - timedelta(minutes=70 - match_index * 3)
            available = captured + timedelta(minutes=5)
            ingested = available + timedelta(minutes=1)
            if match_id == special_ids["odds_exactly_at_cutoff_match_id"]:
                captured = decision - timedelta(minutes=5)
                available = decision - timedelta(minutes=1)
                ingested = decision
            market_payloads.append(
                _market_snapshot(
                    match_id,
                    f"ha-market-{match_id}-v1",
                    odds_values,
                    bookmaker_code=MARKET_BOOKMAKER_CODE,
                    captured_at_utc=captured,
                    available_at_utc=available,
                    ingested_at_utc=ingested,
                )
            )

            sporttery_captured = decision - timedelta(minutes=50 - match_index * 2)
            sporttery_available = sporttery_captured + timedelta(minutes=4)
            sporttery_ingested = sporttery_available + timedelta(minutes=1)
            sporttery_match_no = f"A{slate.slate_date:%y%m%d}{match_index + 1:02d}"
            sporttery_payloads.append(
                _sporttery_snapshot(
                    match_id,
                    f"ha-sporttery-{match_id}-v1",
                    sporttery_match_no,
                    bonus_values,
                    captured_at_utc=sporttery_captured,
                    available_at_utc=sporttery_available,
                    ingested_at_utc=sporttery_ingested,
                )
            )
            quant_payloads.append(
                _manual_quant_input(
                    match_id,
                    f"ha-quant-{match_id}-v1",
                    quant_values,
                    decision - timedelta(minutes=20 - match_index),
                )
            )

            final_outcome = OUTCOMES[global_match_index % len(OUTCOMES)]
            evaluation_outcome = final_outcome
            if match_id == special_ids["missing_result_match_id"]:
                evaluation_outcomes.append("MISSING_RESULT")
            else:
                if match_id == special_ids["result_correction_match_id"]:
                    evaluation_outcome = SelectionKey.HOME_WIN
                home_goals, away_goals = SCORES[evaluation_outcome]
                observed = match.kickoff_at_utc + timedelta(hours=1, minutes=55)
                result_available = observed + timedelta(minutes=10)
                result_ingested = result_available + timedelta(minutes=1)
                result_id = f"ha-result-{match_id}-v1"
                result_payloads.append(
                    MatchResult(
                        match_result_id=result_id,
                        match_id=match_id,
                        provider_code=PROVIDER_CODE,
                        home_goals=home_goals,
                        away_goals=away_goals,
                        observed_at_utc=observed,
                        available_at_utc=result_available,
                        ingested_at_utc=result_ingested,
                        source_result_key=f"source-{result_id}",
                        payload_hash=match_result_payload_sha256(
                            home_goals, away_goals
                        ),
                    )
                )
                evaluation_outcomes.append(evaluation_outcome.value)

                if match_id == special_ids["result_correction_match_id"]:
                    corrected_home, corrected_away = SCORES[final_outcome]
                    corrected_available = slate.evaluation_as_of_at_utc + timedelta(
                        hours=6
                    )
                    corrected_ingested = corrected_available + timedelta(minutes=1)
                    corrected_id = f"ha-result-{match_id}-v2"
                    result_payloads.append(
                        MatchResult(
                            match_result_id=corrected_id,
                            match_id=match_id,
                            provider_code=PROVIDER_CODE,
                            home_goals=corrected_home,
                            away_goals=corrected_away,
                            observed_at_utc=observed,
                            available_at_utc=corrected_available,
                            ingested_at_utc=corrected_ingested,
                            source_result_key=f"source-{corrected_id}",
                            payload_hash=match_result_payload_sha256(
                                corrected_home, corrected_away
                            ),
                            supersedes_match_result_id=result_id,
                        )
                    )
                    correction_visible_at = corrected_ingested
            global_match_index += 1

        final_slates.append(
            SlatePlan(
                slate_id=slate.slate_id,
                slate_date=slate.slate_date,
                decision_as_of_at_utc=slate.decision_as_of_at_utc,
                evaluation_as_of_at_utc=slate.evaluation_as_of_at_utc,
                kickoff_from_utc=slate.kickoff_from_utc,
                kickoff_to_utc=slate.kickoff_to_utc,
                match_ids=slate.match_ids,
                evaluation_outcomes=tuple(evaluation_outcomes),
            )
        )

    slates = tuple(final_slates)
    first_decision = slates[0].decision_as_of_at_utc

    market_future_match = special_ids["market_future_version_match_id"]
    market_future_visible = first_decision + timedelta(minutes=6)
    market_payloads.append(
        _market_snapshot(
            market_future_match,
            f"ha-market-{market_future_match}-v2",
            _decimal_triplet(("1.78", "3.75", "4.90")),
            bookmaker_code=MARKET_BOOKMAKER_CODE,
            captured_at_utc=first_decision + timedelta(minutes=4),
            available_at_utc=first_decision + timedelta(minutes=5),
            ingested_at_utc=market_future_visible,
        )
    )
    future_visibility["market_future_version_visible_at_utc"] = market_future_visible

    late_only_match = special_ids["odds_after_cutoff_match_id"]
    late_decision = slates[4].decision_as_of_at_utc
    late_visible = late_decision + timedelta(minutes=3)
    market_payloads.append(
        _market_snapshot(
            late_only_match,
            f"ha-market-{late_only_match}-late-book-v1",
            _decimal_triplet(("2.20", "3.25", "3.40")),
            bookmaker_code="LATE_BOOK",
            captured_at_utc=late_decision + timedelta(minutes=1),
            available_at_utc=late_decision + timedelta(minutes=2),
            ingested_at_utc=late_visible,
        )
    )
    future_visibility["odds_after_cutoff_visible_at_utc"] = late_visible

    sporttery_future_match = special_ids["sporttery_future_version_match_id"]
    sporttery_future_visible = first_decision + timedelta(minutes=9)
    sporttery_no = "A25010602"
    sporttery_payloads.append(
        _sporttery_snapshot(
            sporttery_future_match,
            f"ha-sporttery-{sporttery_future_match}-v2",
            sporttery_no,
            _decimal_triplet(("2.60", "3.05", "3.15")),
            captured_at_utc=first_decision + timedelta(minutes=7),
            available_at_utc=first_decision + timedelta(minutes=8),
            ingested_at_utc=sporttery_future_visible,
        )
    )
    future_visibility["sporttery_future_version_visible_at_utc"] = (
        sporttery_future_visible
    )

    quant_future_match = special_ids["quant_future_version_match_id"]
    quant_future_visible = first_decision + timedelta(minutes=12)
    quant_payloads.append(
        _manual_quant_input(
            quant_future_match,
            f"ha-quant-{quant_future_match}-v2",
            _decimal_triplet(("0.22", "0.24", "0.54")),
            quant_future_visible,
        )
    )
    future_visibility["quant_future_version_visible_at_utc"] = quant_future_visible

    fixture_future_match = special_ids["fixture_future_version_match_id"]
    original_fixture = matches_by_id[fixture_future_match]
    fixture_future_visible = original_fixture.kickoff_at_utc + timedelta(hours=3)
    updated_fixture = original_fixture.model_copy(
        update={
            "status": MatchStatus.FINISHED,
            "available_at_utc": fixture_future_visible,
        }
    )
    fixture_payloads.append(
        _fixture_payload(
            competition,
            next(
                team for team in teams if team.team_id == updated_fixture.home_team_id
            ),
            next(
                team for team in teams if team.team_id == updated_fixture.away_team_id
            ),
            updated_fixture,
        )
    )
    future_visibility["fixture_future_version_visible_at_utc"] = fixture_future_visible

    if correction_visible_at is None:
        raise AssertionError("result correction timestamp was not generated")

    fixture_payloads.sort(key=lambda item: item.match.available_at_utc)
    market_payloads.sort(key=lambda item: item.ingested_at_utc)
    sporttery_payloads.sort(key=lambda item: item.ingested_at_utc)
    quant_payloads.sort(key=lambda item: item.available_at_utc)
    result_payloads.sort(key=lambda item: item.ingested_at_utc)
    mapping_payloads.sort(key=lambda item: item.available_at_utc)

    payloads = {
        HistoricalArchiveDatasetKind.FIXTURES: tuple(fixture_payloads),
        HistoricalArchiveDatasetKind.MARKET_ODDS: tuple(market_payloads),
        HistoricalArchiveDatasetKind.SPORTTERY_BONUS: tuple(sporttery_payloads),
        HistoricalArchiveDatasetKind.MANUAL_QUANT: tuple(quant_payloads),
        HistoricalArchiveDatasetKind.MATCH_RESULTS: tuple(result_payloads),
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS: tuple(mapping_payloads),
    }
    metadata: dict[str, object] = {
        **special_ids,
        **future_visibility,
        "result_correction_visible_at_utc": correction_visible_at,
    }
    return slates, payloads, metadata


def _fixture_payload(
    competition: Competition,
    home_team: Team,
    away_team: Team,
    match: Match,
) -> BaseModel:
    from football_system.domain.archive import FixtureArchivePayload

    return FixtureArchivePayload(
        competition=competition,
        home_team=home_team,
        away_team=away_team,
        match=match,
    )


def _toml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _toml_array(values: tuple[str, ...] | list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _render_config(
    slates: tuple[SlatePlan, ...],
    inventories: tuple[ArchiveInventory, ...],
    metadata: dict[str, object],
) -> str:
    lines = [
        f"dataset_id = {_toml_string('historical-acceptance-v1')}",
        f"classification = {_toml_string(CLASSIFICATION)}",
        f"performance_warning = {_toml_string(PERFORMANCE_WARNING)}",
        f"manifest_label = {_toml_string(MANIFEST_LABEL)}",
        f"archive_schema_version = {_toml_string(HISTORICAL_ARCHIVE_SCHEMA_VERSION)}",
        f"data_mode = {_toml_string(HistoricalDataMode.LIVE_STRICT.value)}",
        f"provider_code = {_toml_string(PROVIDER_CODE)}",
        f"market_bookmaker_code = {_toml_string(MARKET_BOOKMAKER_CODE)}",
        f"slate_policy = {_toml_string('DAILY_FIXED_CUTOFF_V1')}",
        f"slate_count = {SLATE_COUNT}",
        f"matches_per_slate = {MATCHES_PER_SLATE}",
        f"expected_match_count = {SLATE_COUNT * MATCHES_PER_SLATE}",
        f"budget_fen = {BUDGET_FEN}",
        f"min_selection_ev = {_toml_string(MIN_SELECTION_EV)}",
        f"min_ticket_roi = {_toml_string(MIN_TICKET_ROI)}",
        "",
        "[portfolio]",
        "preferred_max_tickets = 2",
        "absolute_max_tickets = 2",
        f"extra_ticket_min_roi = {_toml_string('0.20')}",
        f"operational_complexity_penalty = {_toml_string('0.01')}",
        f"max_match_exposure_ratio = {_toml_string('0.40')}",
        f"max_selection_exposure_ratio = {_toml_string('0.40')}",
        f"concentration_penalty = {_toml_string('0.00')}",
        f"min_marginal_score = {_toml_string('0.00')}",
        "",
        "[sporttery]",
        f"rules_version = {_toml_string('SPORTTERY_ACCEPTANCE_V1')}",
        "base_stake_fen = 200",
        "max_multiplier = 3",
        "max_ticket_stake_fen = 600",
        "",
        "[expected_final_outcome_counts]",
        "HOME_WIN = 20",
        "DRAW = 20",
        "AWAY_WIN = 19",
        "MISSING_RESULT = 1",
        "",
        "[invalid_examples]",
        (
            "mapping_conflict_directory = "
            f"{_toml_string('invalid_examples/mapping_conflict')}"
        ),
        (
            "mapping_missing_directory = "
            f"{_toml_string('invalid_examples/mapping_missing')}"
        ),
    ]

    for inventory in inventories:
        lines.extend(
            (
                "",
                "[[archives]]",
                f"filename = {_toml_string(inventory.filename)}",
                f"dataset_kind = {_toml_string(inventory.dataset_kind.value)}",
                f"record_count = {inventory.record_count}",
                f"payload_sha256 = {_toml_string(inventory.payload_sha256)}",
                f"file_sha256 = {_toml_string(inventory.file_sha256)}",
            )
        )

    lines.extend(
        (
            "",
            "[[strategies]]",
            f"name = {_toml_string('QUANT_ONLY_V1')}",
            f"quant_weight = {_toml_string('1.00')}",
            f"expected_no_bet_slate_ids = {_toml_array(['ha-slate-07'])}",
            "",
            "[[strategies]]",
            f"name = {_toml_string('MARKET_QUANT_BLEND_V1')}",
            f"quant_weight = {_toml_string('0.70')}",
            f"expected_no_bet_slate_ids = {_toml_array(['ha-slate-07'])}",
            "",
            "[special_cases.odds_exactly_at_cutoff]",
            (f"match_id = {_toml_string(metadata['odds_exactly_at_cutoff_match_id'])}"),
            (
                "snapshot_id = "
                f"{_toml_string('ha-market-' + str(metadata['odds_exactly_at_cutoff_match_id']) + '-v1')}"
            ),
            'legal_behavior = "included_at_decision_cutoff"',
            "",
            "[special_cases.odds_after_cutoff]",
            f"match_id = {_toml_string(metadata['odds_after_cutoff_match_id'])}",
            (
                "snapshot_id = "
                f"{_toml_string('ha-market-' + str(metadata['odds_after_cutoff_match_id']) + '-late-book-v1')}"
            ),
            (
                "visible_at_utc = "
                f"{_toml_string(_timestamp(metadata['odds_after_cutoff_visible_at_utc']))}"
            ),
            'legal_behavior = "excluded_at_decision_included_after_ingestion"',
            "",
            "[special_cases.market_future_version]",
            (f"match_id = {_toml_string(metadata['market_future_version_match_id'])}"),
            (
                "decision_snapshot_id = "
                f"{_toml_string('ha-market-' + str(metadata['market_future_version_match_id']) + '-v1')}"
            ),
            (
                "future_snapshot_id = "
                f"{_toml_string('ha-market-' + str(metadata['market_future_version_match_id']) + '-v2')}"
            ),
            (
                "visible_at_utc = "
                f"{_toml_string(_timestamp(metadata['market_future_version_visible_at_utc']))}"
            ),
            "",
            "[special_cases.sporttery_future_version]",
            (
                "match_id = "
                f"{_toml_string(metadata['sporttery_future_version_match_id'])}"
            ),
            (
                "decision_snapshot_id = "
                f"{_toml_string('ha-sporttery-' + str(metadata['sporttery_future_version_match_id']) + '-v1')}"
            ),
            (
                "future_snapshot_id = "
                f"{_toml_string('ha-sporttery-' + str(metadata['sporttery_future_version_match_id']) + '-v2')}"
            ),
            (
                "visible_at_utc = "
                f"{_toml_string(_timestamp(metadata['sporttery_future_version_visible_at_utc']))}"
            ),
            "",
            "[special_cases.quant_future_version]",
            (f"match_id = {_toml_string(metadata['quant_future_version_match_id'])}"),
            (
                "decision_input_id = "
                f"{_toml_string('ha-quant-' + str(metadata['quant_future_version_match_id']) + '-v1')}"
            ),
            (
                "future_input_id = "
                f"{_toml_string('ha-quant-' + str(metadata['quant_future_version_match_id']) + '-v2')}"
            ),
            (
                "visible_at_utc = "
                f"{_toml_string(_timestamp(metadata['quant_future_version_visible_at_utc']))}"
            ),
            "",
            "[special_cases.fixture_future_version]",
            (f"match_id = {_toml_string(metadata['fixture_future_version_match_id'])}"),
            (
                "visible_at_utc = "
                f"{_toml_string(_timestamp(metadata['fixture_future_version_visible_at_utc']))}"
            ),
            "",
            "[special_cases.result_correction]",
            (f"match_id = {_toml_string(metadata['result_correction_match_id'])}"),
            (
                "initial_result_id = "
                f"{_toml_string('ha-result-' + str(metadata['result_correction_match_id']) + '-v1')}"
            ),
            (
                "corrected_result_id = "
                f"{_toml_string('ha-result-' + str(metadata['result_correction_match_id']) + '-v2')}"
            ),
            'initial_outcome = "HOME_WIN"',
            'corrected_outcome = "DRAW"',
            (
                "visible_at_utc = "
                f"{_toml_string(_timestamp(metadata['result_correction_visible_at_utc']))}"
            ),
            "",
            "[special_cases.missing_result]",
            f"match_id = {_toml_string(metadata['missing_result_match_id'])}",
            'expected_behavior = "partial_coverage_not_silent_drop"',
        )
    )

    for index, slate in enumerate(slates):
        expected_status = "NO_BET" if index == NO_BET_SLATE_INDEX else "RECOMMENDED"
        expected_ticket_count = 0 if index == NO_BET_SLATE_INDEX else 2
        expected_cash_fen = BUDGET_FEN if index == NO_BET_SLATE_INDEX else 8_800
        expected_settled = MATCHES_PER_SLATE - (1 if index == 9 else 0)
        lines.extend(
            (
                "",
                "[[slates]]",
                f"slate_id = {_toml_string(slate.slate_id)}",
                f"date = {_toml_string(slate.slate_date.isoformat())}",
                (
                    "decision_as_of_at_utc = "
                    f"{_toml_string(_timestamp(slate.decision_as_of_at_utc))}"
                ),
                (
                    "evaluation_as_of_at_utc = "
                    f"{_toml_string(_timestamp(slate.evaluation_as_of_at_utc))}"
                ),
                (
                    "kickoff_from_utc = "
                    f"{_toml_string(_timestamp(slate.kickoff_from_utc))}"
                ),
                (f"kickoff_to_utc = {_toml_string(_timestamp(slate.kickoff_to_utc))}"),
                f"match_ids = {_toml_array(list(slate.match_ids))}",
                (
                    "evaluation_outcomes = "
                    f"{_toml_array(list(slate.evaluation_outcomes))}"
                ),
                f"expected_settled_match_count = {expected_settled}",
                f"quant_only_status = {_toml_string(expected_status)}",
                f"quant_only_ticket_count = {expected_ticket_count}",
                f"quant_only_cash_fen = {expected_cash_fen}",
                f"market_quant_blend_status = {_toml_string(expected_status)}",
                f"market_quant_blend_ticket_count = {expected_ticket_count}",
                f"market_quant_blend_cash_fen = {expected_cash_fen}",
            )
        )
    return "\n".join(lines) + "\n"


def _write_invalid_examples(directory: Path, first_fixture: BaseModel) -> None:
    conflict_directory = directory / "invalid_examples" / "mapping_conflict"
    missing_directory = directory / "invalid_examples" / "mapping_missing"
    conflict_directory.mkdir(parents=True)
    missing_directory.mkdir(parents=True)

    conflict_available = datetime(2025, 1, 1, 8, 0, tzinfo=UTC)
    conflicting_mappings = (
        ProviderMatchMapping(
            mapping_id="ha-invalid-conflict-a",
            provider_code=PROVIDER_CODE,
            external_namespace="synthetic-conflict",
            external_match_id="DUPLICATE-001",
            internal_match_id="ha-invalid-match-a",
            resolution_method="SYNTHETIC_CONFLICT_EXAMPLE",
            confidence=Decimal("1.000000000000"),
            available_at_utc=conflict_available,
        ),
        ProviderMatchMapping(
            mapping_id="ha-invalid-conflict-b",
            provider_code=PROVIDER_CODE,
            external_namespace="synthetic-conflict",
            external_match_id="DUPLICATE-001",
            internal_match_id="ha-invalid-match-b",
            resolution_method="SYNTHETIC_CONFLICT_EXAMPLE",
            confidence=Decimal("1.000000000000"),
            available_at_utc=conflict_available + timedelta(minutes=1),
        ),
    )
    _write_archive(
        conflict_directory,
        "provider_mappings.json",
        HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS,
        conflicting_mappings,
        archive_suffix="invalid-conflict-v1",
    )
    _write_archive(
        missing_directory,
        "fixtures.json",
        HistoricalArchiveDatasetKind.FIXTURES,
        (first_fixture,),
        archive_suffix="invalid-missing-mapping-v1",
    )


def _generate_corpus(directory: Path) -> tuple[ArchiveInventory, ...]:
    slates, payloads, metadata = _build_dataset()
    archive_specs = (
        ("fixtures.json", HistoricalArchiveDatasetKind.FIXTURES),
        ("market_odds.json", HistoricalArchiveDatasetKind.MARKET_ODDS),
        ("sporttery_bonus.json", HistoricalArchiveDatasetKind.SPORTTERY_BONUS),
        ("manual_quant.json", HistoricalArchiveDatasetKind.MANUAL_QUANT),
        ("match_results.json", HistoricalArchiveDatasetKind.MATCH_RESULTS),
        ("provider_mappings.json", HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS),
    )
    inventories = tuple(
        _write_archive(
            directory,
            filename,
            dataset_kind,
            payloads[dataset_kind],
        )
        for filename, dataset_kind in archive_specs
    )
    _write_invalid_examples(
        directory,
        payloads[HistoricalArchiveDatasetKind.FIXTURES][0],
    )

    config_text = _render_config(slates, inventories, metadata)
    with (directory / "acceptance_config.toml").open(
        "w", encoding="utf-8", newline="\n"
    ) as stream:
        stream.write(config_text)
    config = tomllib.loads(config_text)
    if config["expected_match_count"] != 60 or len(config["slates"]) != 10:
        raise AssertionError("generated acceptance plan has incorrect dimensions")

    store = LocalArchiveStore(
        directory,
        data_mode=HistoricalDataMode.LIVE_STRICT,
    )
    if len(store.manifests) != len(HistoricalArchiveDatasetKind):
        raise AssertionError("main acceptance store does not contain six archives")
    invalid_directories = (
        directory / "invalid_examples" / "mapping_conflict",
        directory / "invalid_examples" / "mapping_missing",
    )
    for invalid_directory in invalid_directories:
        try:
            LocalArchiveStore(invalid_directory)
        except ArchiveValidationError:
            continue
        raise AssertionError(
            f"invalid example unexpectedly loaded: {invalid_directory}"
        )
    return inventories


def _publish_corpus(staged_directory: Path, output_directory: Path) -> None:
    backup_directory = staged_directory.with_name(f"{staged_directory.name}.previous")
    if backup_directory.exists():
        raise FileExistsError(f"backup path already exists: {backup_directory}")

    had_output = output_directory.exists()
    output_moved = False
    try:
        if had_output:
            if not output_directory.is_dir():
                raise NotADirectoryError(output_directory)
            output_directory.rename(backup_directory)
            output_moved = True
        staged_directory.rename(output_directory)
    except BaseException:
        if output_moved:
            try:
                backup_directory.rename(output_directory)
            except BaseException as restore_error:
                raise RuntimeError(
                    "failed to publish or restore the acceptance corpus; "
                    f"the previous corpus remains at {backup_directory}"
                ) from restore_error
        raise

    if output_moved:
        shutil.rmtree(backup_directory)


def main() -> None:
    OUTPUT_DIRECTORY.parent.mkdir(parents=True, exist_ok=True)
    staged_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{OUTPUT_DIRECTORY.name}.staging-",
            dir=OUTPUT_DIRECTORY.parent,
        )
    )
    try:
        inventories = _generate_corpus(staged_directory)
        # The fixed corpus is fully generated; carrying stale files forward is unsafe.
        _publish_corpus(staged_directory, OUTPUT_DIRECTORY)
    finally:
        if staged_directory.exists():
            shutil.rmtree(staged_directory)

    counts = ", ".join(
        f"{item.dataset_kind.value}={item.record_count}" for item in inventories
    )
    print(
        f"generated {SLATE_COUNT} slates / {SLATE_COUNT * MATCHES_PER_SLATE} "
        f"matches in {OUTPUT_DIRECTORY} ({counts})"
    )


if __name__ == "__main__":
    main()
