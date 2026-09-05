import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from football_system.application.daily_slate import (
    EXACT_SLATE_LABEL_RESOLUTION,
    PlanSportteryDailySlateService,
)
from football_system.application.identity_catalog import MatchIdentityCatalog
from football_system.domain.daily_slate import (
    DailySlateCandidateStatus,
    DailySlateCaptureKind,
    DailySlateStatus,
)
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
    TeamIdentity,
)
from football_system.domain.match import ProviderMatchMapping
from football_system.infrastructure.files.daily_slate import (
    DailySlateFileError,
    load_sporttery_daily_slate,
)


UTC = timezone.utc
CAPTURED = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
REVIEWED = CAPTURED + timedelta(minutes=5)
PLANNED = REVIEWED + timedelta(minutes=5)
MATCH_DATE = "2026-09-05"


def test_five_candidate_plan_resolves_four_and_reconciles_one(
    tmp_path: Path,
) -> None:
    document = _write_document(tmp_path, _candidate_rows(5))
    slate = load_sporttery_daily_slate(document)

    plan = PlanSportteryDailySlateService().plan(
        slate,
        _catalog(),
        planned_at_utc=PLANNED,
    )
    replay = PlanSportteryDailySlateService().plan(
        slate,
        _catalog(),
        planned_at_utc=PLANNED,
    )

    assert plan == replay
    assert plan.status is DailySlateStatus.CANDIDATES_AVAILABLE
    assert plan.analysis_status == "NO_ANALYSIS"
    assert len(plan.candidates) == 5
    assert sum(
        DailySlateCandidateStatus.IDENTITY_RESOLVED in item.statuses
        for item in plan.candidates
    ) == 4
    assert sum(
        DailySlateCandidateStatus.IDENTITY_UNRESOLVED in item.statuses
        for item in plan.candidates
    ) == 1

    by_number = {
        item.candidate.sporttery_match_no: item for item in plan.candidates
    }
    for index in range(1, 4):
        result = by_number[f"SYN{index:03d}"]
        assert result.canonical_match_id == f"match-{index}"
        assert result.statuses == (
            DailySlateCandidateStatus.IDENTITY_RESOLVED,
            DailySlateCandidateStatus.MARKET_ODDS_REQUIRED,
            DailySlateCandidateStatus.SPORTTERY_SP_READY,
            DailySlateCandidateStatus.READY_FOR_CAPTURE,
        )

    resolved_by_exact_labels = by_number["SYN004"]
    assert resolved_by_exact_labels.canonical_candidate_ids == ("match-4",)
    assert resolved_by_exact_labels.resolution_method == EXACT_SLATE_LABEL_RESOLUTION
    assert resolved_by_exact_labels.statuses == (
        DailySlateCandidateStatus.IDENTITY_RESOLVED,
        DailySlateCandidateStatus.MARKET_ODDS_REQUIRED,
        DailySlateCandidateStatus.SPORTTERY_SP_READY,
        DailySlateCandidateStatus.READY_FOR_CAPTURE,
    )
    unresolved_without_match = by_number["SYN005"]
    assert unresolved_without_match.canonical_candidate_ids == ()
    assert unresolved_without_match.statuses == (
        DailySlateCandidateStatus.IDENTITY_UNRESOLVED,
        DailySlateCandidateStatus.FIXTURE_SOURCE_REQUIRED,
        DailySlateCandidateStatus.SPORTTERY_SP_READY,
    )

    assert len(plan.reconciliation_tasks) == 1
    number_by_candidate_id = {
        item.candidate.candidate_id: item.candidate.sporttery_match_no
        for item in plan.candidates
    }
    tasks = {
        number_by_candidate_id[task.candidate_id]: task
        for task in plan.reconciliation_tasks
    }
    assert tasks["SYN005"].canonical_candidate_ids == ()
    assert tasks["SYN005"].fixture_source_required is True

    market_request = next(
        item
        for item in plan.capture_plan.requests
        if item.kind is DailySlateCaptureKind.MARKET_ODDS
    )
    fixture_request = next(
        item
        for item in plan.capture_plan.requests
        if item.kind is DailySlateCaptureKind.FIXTURE_SOURCE
    )
    assert market_request.canonical_match_ids == (
        "match-1",
        "match-2",
        "match-3",
        "match-4",
    )
    assert fixture_request.canonical_match_ids == ()
    assert fixture_request.candidate_ids == (
        by_number["SYN005"].candidate.candidate_id,
    )
    assert len(plan.capture_plan.sporttery_ingestion_candidate_ids) == 5
    assert plan.capture_plan.ready_match_ids == (
        "match-1",
        "match-2",
        "match-3",
        "match-4",
    )


@pytest.mark.parametrize(
    "change",
    (
        lambda data: data.pop("review_level"),
        lambda data: data.update({"review_level": "UNREVIEWED"}),
        lambda data: data.update(
            {"reviewed_at_utc": (CAPTURED - timedelta(seconds=1)).isoformat()}
        ),
        lambda data: data["candidates"].append(data["candidates"][0].copy()),
    ),
)
def test_lightweight_slate_rejects_unreviewed_or_duplicate_input(
    tmp_path: Path,
    change: object,
) -> None:
    document = _write_document(tmp_path, _candidate_rows(1))
    data = json.loads(document.read_text(encoding="utf-8"))
    change(data)
    document.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DailySlateFileError):
        load_sporttery_daily_slate(document)


def test_lightweight_slate_allows_same_number_on_different_dates(
    tmp_path: Path,
) -> None:
    rows = _candidate_rows(2)
    rows[1]["sporttery_match_no"] = rows[0]["sporttery_match_no"]
    rows[1]["match_date"] = "2026-09-06"
    rows[1]["kickoff_at_utc"] = "2026-09-06T11:00:00+00:00"

    slate = load_sporttery_daily_slate(_write_document(tmp_path, rows))

    assert tuple(item.external_match_id for item in slate.candidates) == (
        "2026-09-05:SYN001",
        "2026-09-06:SYN001",
    )


def test_lightweight_slate_allows_optional_sp_and_empty_candidate_sets(
    tmp_path: Path,
) -> None:
    without_sp = _candidate_rows(1)
    without_sp[0].pop("three_way_sp")
    slate = load_sporttery_daily_slate(_write_document(tmp_path, without_sp))
    assert slate.candidates[0].three_way_sp is None

    empty_directory = tmp_path / "empty"
    empty_directory.mkdir()
    empty_slate = load_sporttery_daily_slate(_write_document(empty_directory, []))
    empty_plan = PlanSportteryDailySlateService().plan(
        empty_slate,
        MatchIdentityCatalog(
            team_identities=(),
            competition_mappings=(),
            canonical_matches=(),
            explicit_mappings=(),
        ),
        planned_at_utc=PLANNED,
    )
    assert empty_slate.status is DailySlateStatus.NO_SPORTTERY_CANDIDATES
    assert empty_plan.analysis_status == "NO_ANALYSIS"
    assert empty_plan.capture_plan.requests == ()


def test_unique_exact_slate_resolution_does_not_hide_ambiguity(tmp_path: Path) -> None:
    slate = load_sporttery_daily_slate(
        _write_document(tmp_path, _candidate_rows(4)[3:])
    )
    catalog = _catalog()
    duplicate = catalog.canonical_matches[3].model_copy(
        update={"internal_match_id": "match-4-duplicate"}
    )
    ambiguous_catalog = catalog.model_copy(
        update={"canonical_matches": (*catalog.canonical_matches, duplicate)}
    )

    plan = PlanSportteryDailySlateService().plan(
        slate,
        ambiguous_catalog,
        planned_at_utc=PLANNED,
    )

    result = plan.candidates[0]
    assert result.statuses == (
        DailySlateCandidateStatus.IDENTITY_UNRESOLVED,
        DailySlateCandidateStatus.SPORTTERY_SP_READY,
    )
    assert result.canonical_candidate_ids == ("match-4", "match-4-duplicate")
    assert plan.reconciliation_tasks[0].fixture_source_required is False


def test_reviewed_manual_archive_is_accepted_as_a_daily_slate() -> None:
    slate = load_sporttery_daily_slate(
        Path("tests/fixtures/providers/synthetic_sporttery/manual.json")
    )

    assert slate.status is DailySlateStatus.CANDIDATES_AVAILABLE
    assert len(slate.candidates) == 1
    assert slate.candidates[0].sporttery_match_no == "SYN001"
    assert slate.candidates[0].three_way_sp is not None
    assert slate.provenance.source_schema_version == "SPORTTERY_MANUAL_ARCHIVE_V2"


def _candidate_rows(count: int) -> list[dict[str, object]]:
    return [
        {
            "sporttery_match_no": f"SYN{index:03d}",
            "match_date": MATCH_DATE,
            "kickoff_at_utc": f"2026-09-05T{9 + index:02d}:00:00+00:00",
            "home_label": f"Synthetic Home {index}",
            "away_label": f"Synthetic Away {index}",
            "competition_label": "Synthetic League",
            "three_way_sp": {
                "home_win": "2.10",
                "draw": "3.20",
                "away_win": "3.40",
            },
        }
        for index in range(1, count + 1)
    ]


def _write_document(directory: Path, rows: list[dict[str, object]]) -> Path:
    artifact = directory / "daily-slate.raw"
    artifact.write_bytes(b"SYNTHETIC DAILY SLATE EVIDENCE\n")
    document = directory / "daily-slate.json"
    document.write_text(
        json.dumps(
            {
                "schema_version": "SPORTTERY_DAILY_SLATE_INPUT_V1",
                "snapshot_id": "synthetic-reviewed-daily-slate",
                "captured_at_utc": CAPTURED.isoformat(),
                "source_reference": "synthetic://daily-slate",
                "source_artifact_path": artifact.name,
                "source_artifact_sha256": hashlib.sha256(
                    artifact.read_bytes()
                ).hexdigest(),
                "entered_by": "synthetic-reviewer",
                "review_level": "SELF_REVIEWED",
                "reviewed_by": "synthetic-reviewer",
                "reviewed_at_utc": REVIEWED.isoformat(),
                "candidates": rows,
            }
        ),
        encoding="utf-8",
    )
    return document


def _catalog() -> MatchIdentityCatalog:
    team_identities = []
    canonical_matches = []
    explicit_mappings = []
    for index in range(1, 5):
        for side in ("Home", "Away"):
            team_identities.append(
                TeamIdentity(
                    internal_team_id=f"team-{side.casefold()}-{index}",
                    canonical_name=f"Synthetic {side} {index}",
                    aliases=(
                        Alias(
                            provider_code="SYNTHETIC_FIXTURE",
                            provider_team_id=f"fixture-{side.casefold()}-{index}",
                            provider_team_name=f"Synthetic {side} {index}",
                            language="en",
                        ),
                    ),
                )
            )
        canonical_matches.append(
            CanonicalMatchIdentity(
                internal_match_id=f"match-{index}",
                internal_competition_id="competition-synthetic",
                internal_home_team_id=f"team-home-{index}",
                internal_away_team_id=f"team-away-{index}",
                season="2026/27",
                competition_type="LEAGUE",
                kickoff_at_utc=datetime(
                    2026,
                    9,
                    5,
                    9 + index,
                    tzinfo=UTC,
                ),
            )
        )
        if index <= 3:
            explicit_mappings.append(
                ProviderMatchMapping(
                    mapping_id=f"sporttery-mapping-{index}",
                    provider_code="SPORTTERY_MANUAL",
                    external_namespace="sporttery_match",
                    external_match_id=f"{MATCH_DATE}:SYN{index:03d}",
                    internal_match_id=f"match-{index}",
                    resolution_method="REVIEWED_EXPLICIT",
                    confidence=Decimal("1"),
                    available_at_utc=REVIEWED,
                )
            )
    return MatchIdentityCatalog(
        team_identities=tuple(team_identities),
        competition_mappings=(
            CompetitionMapping(
                internal_competition_id="competition-synthetic",
                provider_code="SYNTHETIC_FIXTURE",
                provider_competition_id="fixture-league",
                provider_competition_name="Synthetic League",
                language="en",
                season="2026/27",
                competition_type="LEAGUE",
            ),
        ),
        canonical_matches=tuple(canonical_matches),
        explicit_mappings=tuple(explicit_mappings),
    )
