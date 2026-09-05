from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from football_system.application.daily_slate import PlanSportteryDailySlateService
from football_system.application.identity_catalog import MatchIdentityCatalog
from football_system.domain.daily_slate import (
    DailySlateCandidate,
    DailySlateProvenance,
    DailySlateReviewLevel,
    DailySlateStatus,
    SportteryDailySlate,
)
from football_system.domain.market import ThreeWayFixedBonus


UTC = timezone.utc
CAPTURED = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
REVIEWED = CAPTURED + timedelta(minutes=5)


def test_daily_slate_hashes_are_deterministic_and_models_are_frozen() -> None:
    provenance = _provenance()
    first = _candidate("SYN001", date(2026, 9, 5), 10, provenance)
    second = _candidate("SYN002", date(2026, 9, 5), 12, provenance)

    slate = SportteryDailySlate.freeze(
        candidates=(second, first),
        provenance=provenance,
    )
    replay = SportteryDailySlate.freeze(
        candidates=(first, second),
        provenance=provenance,
    )

    assert slate == replay
    assert slate.slate_id == replay.slate_id
    assert slate.slate_hash == replay.slate_hash
    assert tuple(item.sporttery_match_no for item in slate.candidates) == (
        "SYN001",
        "SYN002",
    )
    with pytest.raises(ValidationError, match="frozen"):
        slate.status = DailySlateStatus.NO_SPORTTERY_CANDIDATES


def test_daily_slate_rejects_duplicate_number_on_same_date() -> None:
    provenance = _provenance()
    first = _candidate("SAME001", date(2026, 9, 5), 10, provenance)
    duplicate = _candidate("SAME001", date(2026, 9, 5), 11, provenance)

    with pytest.raises(ValidationError, match="number/date identities must be unique"):
        SportteryDailySlate.freeze(
            candidates=(first, duplicate),
            provenance=provenance,
        )


def test_daily_slate_allows_same_number_on_different_dates() -> None:
    provenance = _provenance()

    slate = SportteryDailySlate.freeze(
        candidates=(
            _candidate("SAME001", date(2026, 9, 5), 10, provenance),
            _candidate("SAME001", date(2026, 9, 6), 10, provenance),
        ),
        provenance=provenance,
    )

    assert tuple(item.external_match_id for item in slate.candidates) == (
        "2026-09-05:SAME001",
        "2026-09-06:SAME001",
    )


def test_empty_daily_slate_is_a_deterministic_no_analysis_plan() -> None:
    slate = SportteryDailySlate.freeze(candidates=(), provenance=_provenance())
    catalog = MatchIdentityCatalog(
        team_identities=(),
        competition_mappings=(),
        canonical_matches=(),
        explicit_mappings=(),
    )

    plan = PlanSportteryDailySlateService().plan(
        slate,
        catalog,
        planned_at_utc=REVIEWED,
    )
    replay = PlanSportteryDailySlateService().plan(
        slate,
        catalog,
        planned_at_utc=REVIEWED,
    )

    assert plan == replay
    assert plan.status is DailySlateStatus.NO_SPORTTERY_CANDIDATES
    assert plan.analysis_status == "NO_ANALYSIS"
    assert plan.candidates == ()
    assert plan.reconciliation_tasks == ()
    assert plan.capture_plan.requests == ()
    assert plan.capture_plan.sporttery_ingestion_candidate_ids == ()
    assert plan.capture_plan.ready_match_ids == ()


def _provenance() -> DailySlateProvenance:
    return DailySlateProvenance(
        source_schema_version="SPORTTERY_DAILY_SLATE_INPUT_V1",
        source_document_id="synthetic-reviewed-slate",
        source_document_sha256="1" * 64,
        source_reference="synthetic://daily-slate",
        source_artifact_path="daily-slate.raw",
        source_artifact_sha256="2" * 64,
        entered_by="synthetic-reviewer",
        review_level=DailySlateReviewLevel.SELF_REVIEWED,
        reviewed_by="synthetic-reviewer",
        captured_at_utc=CAPTURED,
        reviewed_at_utc=REVIEWED,
    )


def _candidate(
    match_no: str,
    match_date: date,
    kickoff_hour: int,
    provenance: DailySlateProvenance,
) -> DailySlateCandidate:
    return DailySlateCandidate.freeze(
        sporttery_match_no=match_no,
        match_date=match_date,
        kickoff_at_utc=datetime(
            match_date.year,
            match_date.month,
            match_date.day,
            kickoff_hour,
            tzinfo=UTC,
        ),
        home_label=f"Synthetic Home {match_date.isoformat()} {kickoff_hour}",
        away_label=f"Synthetic Away {match_date.isoformat()} {kickoff_hour}",
        competition_label="Synthetic League",
        three_way_sp=ThreeWayFixedBonus(
            home_win=Decimal("2.10"),
            draw=Decimal("3.20"),
            away_win=Decimal("3.40"),
        ),
        source_reference=provenance.source_reference,
        captured_at_utc=provenance.captured_at_utc,
        reviewed_at_utc=provenance.reviewed_at_utc,
        provenance=provenance,
    )
