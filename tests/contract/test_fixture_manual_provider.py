from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from football_system.application.environment import RuntimeEnvironment
from football_system.application.identity_catalog import (
    CanonicalFixtureAnchor,
    MatchIdentityCatalog,
)
from football_system.domain.archive import HistoricalDataMode, canonical_json
from football_system.domain.identity import (
    Alias,
    CanonicalMatchIdentity,
    CompetitionMapping,
    TeamIdentity,
)
from football_system.domain.match import Competition, Match, MatchStatus, Team
from football_system.infrastructure.files.raw_archive import RawDataArchive
from football_system.infrastructure.providers.real.fixture_manual import (
    REVIEWED_FIXTURE_MANUAL_ARCHIVE_SCHEMA_VERSION,
    REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE,
    ReviewedFixtureManualArchiveError,
    ReviewedFixtureManualArchiveProvider,
    ReviewedFixtureManualIssueReason,
    ReviewedFixtureManualReconciliationError,
    ReviewedFixtureManualReviewLevel,
    load_reviewed_fixture_manual_archive,
    reviewed_fixture_manual_request,
)


UTC = timezone.utc
CAPTURED = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
REVIEWED = CAPTURED + timedelta(minutes=5)
KICKOFF = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("review_level", "entered_by", "reviewed_by"),
    (
        ("SELF_REVIEWED", "fixture-operator", "fixture-operator"),
        ("INDEPENDENT_REVIEWED", "fixture-operator", "fixture-reviewer"),
    ),
)
def test_reviewed_manual_fixture_builds_an_offline_capture(
    tmp_path: Path,
    review_level: str,
    entered_by: str,
    reviewed_by: str,
) -> None:
    document = _write_archive(
        tmp_path,
        review_level=review_level,
        entered_by=entered_by,
        reviewed_by=reviewed_by,
    )
    archive = load_reviewed_fixture_manual_archive(document)
    raw_archive = RawDataArchive(tmp_path / "raw")
    provider = ReviewedFixtureManualArchiveProvider(
        archive,
        _empty_catalog(),
        raw_archive,
    )

    request = reviewed_fixture_manual_request(archive)
    capture = asyncio.run(provider.capture_fixtures(request))

    assert provider.runtime_provenance.environment is RuntimeEnvironment.LIVE
    assert provider.runtime_provenance.data_mode is HistoricalDataMode.LIVE_STRICT
    assert provider.runtime_provenance.is_mock is False
    assert capture.provider_code == REVIEWED_FIXTURE_MANUAL_PROVIDER_CODE
    assert capture.request == request
    assert capture.request_audit.available_at_utc == REVIEWED
    assert capture.request_audit.request_parameters["review_levels"] == (
        review_level,
    )
    assert capture.raw_payload_sha256 == hashlib.sha256(
        document.read_bytes()
    ).hexdigest()
    assert len(capture.registration.competitions) == 1
    assert len(capture.registration.teams) == 2
    assert len(capture.registration.matches) == 1
    assert len(capture.registration.canonical_matches) == 1
    assert len(capture.registration.explicit_mappings) == 1
    assert len(capture.observations) == 1
    assert capture.observations[0].kickoff_at_utc == KICKOFF
    assert capture.observations[0].available_at_utc == REVIEWED
    assert tuple(raw_archive.root.glob("**/*.raw"))[0].read_bytes() == document.read_bytes()
    payload = json.loads(canonical_json(archive.document))
    assert set(payload) == {"schema_version", "fixtures"}
    assert not {"odds", "prediction", "probability", "ev"} & set(
        payload["fixtures"][0]
    )
    assert (
        archive.document.fixtures[0].review_level.value
        == ReviewedFixtureManualReviewLevel(review_level).value
    )


@pytest.mark.parametrize(
    "change",
    (
        lambda data: data["fixtures"][0].pop("review_level"),
        lambda data: data["fixtures"][0].update({"review_level": "UNREVIEWED"}),
        lambda data: data["fixtures"][0].update({"prediction": "HOME"}),
        lambda data: data["fixtures"][0].update(
            {"source_artifact_sha256": "0" * 64}
        ),
        lambda data: data["fixtures"][0].update(
            {"reviewed_at_utc": (CAPTURED - timedelta(seconds=1)).isoformat()}
        ),
    ),
)
def test_reviewed_manual_fixture_rejects_unreviewed_or_unverified_input(
    tmp_path: Path,
    change: object,
) -> None:
    document = _write_archive(tmp_path)
    data = json.loads(document.read_text(encoding="utf-8"))
    change(data)
    document.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ReviewedFixtureManualArchiveError):
        load_reviewed_fixture_manual_archive(document)


def test_reviewed_manual_fixture_rejects_empty_evidence(tmp_path: Path) -> None:
    document = _write_archive(tmp_path, evidence=b"")

    with pytest.raises(ReviewedFixtureManualArchiveError, match="cannot be empty"):
        load_reviewed_fixture_manual_archive(document)


def test_reviewed_manual_fixture_does_not_fuzzy_bind_an_unknown_label(
    tmp_path: Path,
) -> None:
    exact_document = _write_archive(tmp_path / "exact")
    exact_archive = load_reviewed_fixture_manual_archive(exact_document)
    exact_capture = asyncio.run(
        ReviewedFixtureManualArchiveProvider(
            exact_archive,
            _catalog(),
            RawDataArchive(tmp_path / "exact-raw"),
        ).capture_fixtures(reviewed_fixture_manual_request(exact_archive))
    )

    near_directory = tmp_path / "near"
    near_document = _write_archive(
        near_directory,
        home_team_label="Fabricated Harbour F.C.",
    )
    near_archive = load_reviewed_fixture_manual_archive(near_document)
    near_capture = asyncio.run(
        ReviewedFixtureManualArchiveProvider(
            near_archive,
            _catalog(),
            RawDataArchive(tmp_path / "near-raw"),
        ).capture_fixtures(reviewed_fixture_manual_request(near_archive))
    )

    assert exact_capture.registration.matches[0].match_id == "canonical-match"
    assert near_capture.registration.matches[0].match_id != "canonical-match"
    assert "team-harbour" not in {
        item.team_id for item in near_capture.registration.teams
    }


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        (
            {"kickoff_at_utc": KICKOFF + timedelta(minutes=30)},
            ReviewedFixtureManualIssueReason.KICKOFF_CONFLICT,
        ),
        (
            {
                "home_team_label": "Fabricated Orchard FC",
                "away_team_label": "Fabricated Harbour FC",
            },
            ReviewedFixtureManualIssueReason.HOME_AWAY_CONFLICT,
        ),
        (
            {"competition_label": "Fabricated Other League"},
            ReviewedFixtureManualIssueReason.COMPETITION_CONFLICT,
        ),
        (
            {"season": "2027/28"},
            ReviewedFixtureManualIssueReason.SEASON_CONFLICT,
        ),
        (
            {"competition_type": "CUP"},
            ReviewedFixtureManualIssueReason.COMPETITION_TYPE_CONFLICT,
        ),
        (
            {"team_type": "NATIONAL"},
            ReviewedFixtureManualIssueReason.TEAM_TYPE_CONFLICT,
        ),
    ),
)
def test_reviewed_manual_fixture_conflicts_are_structured_and_deterministic(
    tmp_path: Path,
    updates: dict[str, object],
    reason: ReviewedFixtureManualIssueReason,
) -> None:
    document = _write_archive(tmp_path, **updates)
    archive = load_reviewed_fixture_manual_archive(document)

    with pytest.raises(ReviewedFixtureManualReconciliationError) as first:
        ReviewedFixtureManualArchiveProvider(
            archive,
            _catalog(),
            RawDataArchive(tmp_path / "raw"),
        )
    with pytest.raises(ReviewedFixtureManualReconciliationError) as replay:
        ReviewedFixtureManualArchiveProvider(
            archive,
            _catalog(),
            RawDataArchive(tmp_path / "raw"),
        )

    assert first.value.report == replay.value.report
    assert len(first.value.report.issues) == 1
    issue = first.value.report.issues[0]
    assert issue.reason is reason
    assert issue.canonical_candidate_ids == ("canonical-match",)
    assert issue.source_artifact_sha256 == archive.evidence_sha256[0]
    assert not tuple((tmp_path / "raw").glob("**/*.raw"))


def test_reviewed_manual_fixture_reports_two_labels_for_one_team(
    tmp_path: Path,
) -> None:
    document = _write_archive(
        tmp_path,
        away_team_label="Fabricated Harbour Alternate",
    )
    archive = load_reviewed_fixture_manual_archive(document)
    catalog = _catalog()
    home = catalog.team_identities[0]
    catalog = catalog.model_copy(
        update={
            "team_identities": (
                home.model_copy(
                    update={
                        "aliases": (
                            *home.aliases,
                            Alias(
                                provider_code="SYNTHETIC_FIXTURE",
                                provider_team_id="fixture-home-alternate",
                                provider_team_name="Fabricated Harbour Alternate",
                                language="en",
                            ),
                        )
                    }
                ),
                catalog.team_identities[1],
            )
        }
    )

    with pytest.raises(ReviewedFixtureManualReconciliationError) as error:
        ReviewedFixtureManualArchiveProvider(
            archive,
            catalog,
            RawDataArchive(tmp_path / "raw"),
        )

    issue = error.value.report.issues[0]
    assert issue.reason is ReviewedFixtureManualIssueReason.HOME_AWAY_CONFLICT
    assert issue.canonical_candidate_ids == ("team-harbour",)
    assert not tuple((tmp_path / "raw").glob("**/*.raw"))


def test_exact_archive_replay_survives_later_canonical_kickoff_drift(
    tmp_path: Path,
) -> None:
    document = _write_archive(tmp_path)
    archive = load_reviewed_fixture_manual_archive(document)
    catalog = _catalog()
    original_anchor = catalog.canonical_anchors[0].model_copy(
        update={
            "match": catalog.canonical_anchors[0].match.model_copy(
                update={"status": MatchStatus.FINISHED}
            )
        }
    )
    catalog = MatchIdentityCatalog(
        team_identities=catalog.team_identities,
        competition_mappings=catalog.competition_mappings,
        canonical_matches=catalog.canonical_matches,
        explicit_mappings=(),
        canonical_anchors=(original_anchor,),
    )
    first_capture = asyncio.run(
        ReviewedFixtureManualArchiveProvider(
            archive,
            catalog,
            RawDataArchive(tmp_path / "first-raw"),
        ).capture_fixtures(reviewed_fixture_manual_request(archive))
    )
    drifted_kickoff = KICKOFF + timedelta(minutes=30)
    drifted_identity = original_anchor.identity.model_copy(
        update={"kickoff_at_utc": drifted_kickoff}
    )
    drifted_anchor = original_anchor.model_copy(
        update={
            "match": original_anchor.match.model_copy(
                update={
                    "kickoff_at_utc": drifted_kickoff,
                    "status": MatchStatus.POSTPONED,
                }
            ),
            "identity": drifted_identity,
        }
    )
    drifted_catalog = MatchIdentityCatalog(
        team_identities=catalog.team_identities,
        competition_mappings=catalog.competition_mappings,
        canonical_matches=(drifted_identity,),
        explicit_mappings=first_capture.registration.explicit_mappings,
        canonical_anchors=(drifted_anchor,),
    )

    replay_capture = asyncio.run(
        ReviewedFixtureManualArchiveProvider(
            archive,
            drifted_catalog,
            RawDataArchive(tmp_path / "replay-raw"),
        ).capture_fixtures(reviewed_fixture_manual_request(archive))
    )

    assert replay_capture.ingestion_id == first_capture.ingestion_id
    assert replay_capture.registration.matches[0].match_id == "canonical-match"
    assert replay_capture.observations[0].kickoff_at_utc == KICKOFF
    assert replay_capture.observations[0].status is MatchStatus.FINISHED

    expanded_data = json.loads(document.read_text(encoding="utf-8"))
    additional = expanded_data["fixtures"][0].copy()
    additional.update(
        {
            "kickoff_at_utc": (KICKOFF + timedelta(days=1)).isoformat(),
            "home_team_label": "Fabricated Summit FC",
            "away_team_label": "Fabricated Valley FC",
        }
    )
    expanded_data["fixtures"].append(additional)
    document.write_text(json.dumps(expanded_data), encoding="utf-8")
    expanded_archive = load_reviewed_fixture_manual_archive(document)

    with pytest.raises(ReviewedFixtureManualReconciliationError) as expanded_error:
        ReviewedFixtureManualArchiveProvider(
            expanded_archive,
            drifted_catalog,
            RawDataArchive(tmp_path / "expanded-raw"),
        )
    assert expanded_error.value.report.issues[0].reason is (
        ReviewedFixtureManualIssueReason.KICKOFF_CONFLICT
    )

    finished_anchor = original_anchor.model_copy(
        update={
            "match": original_anchor.match.model_copy(
                update={"status": MatchStatus.FINISHED}
            )
        }
    )
    finished_catalog = MatchIdentityCatalog(
        team_identities=catalog.team_identities,
        competition_mappings=catalog.competition_mappings,
        canonical_matches=catalog.canonical_matches,
        explicit_mappings=first_capture.registration.explicit_mappings,
        canonical_anchors=(finished_anchor,),
    )
    expanded_capture = asyncio.run(
        ReviewedFixtureManualArchiveProvider(
            expanded_archive,
            finished_catalog,
            RawDataArchive(tmp_path / "finished-raw"),
        ).capture_fixtures(reviewed_fixture_manual_request(expanded_archive))
    )
    observations = {
        item.internal_match_id: item for item in expanded_capture.observations
    }
    assert observations["canonical-match"].status is MatchStatus.FINISHED


def test_reviewed_manual_fixture_ambiguous_labels_never_auto_bind(
    tmp_path: Path,
) -> None:
    document = _write_archive(tmp_path)
    archive = load_reviewed_fixture_manual_archive(document)
    catalog = _catalog()
    duplicate_home = catalog.team_identities[0].model_copy(
        update={"internal_team_id": "other-home-team"}
    )
    ambiguous = catalog.model_copy(
        update={"team_identities": (*catalog.team_identities, duplicate_home)}
    )

    with pytest.raises(ReviewedFixtureManualReconciliationError) as error:
        ReviewedFixtureManualArchiveProvider(
            archive,
            ambiguous,
            RawDataArchive(tmp_path / "raw"),
        )

    assert error.value.report.issues[0].reason is (
        ReviewedFixtureManualIssueReason.AMBIGUOUS_HOME_TEAM
    )
    assert error.value.report.issues[0].canonical_candidate_ids == (
        "other-home-team",
        "team-harbour",
    )


def _write_archive(
    directory: Path,
    *,
    review_level: str = "SELF_REVIEWED",
    entered_by: str = "fixture-operator",
    reviewed_by: str = "fixture-operator",
    competition_label: str = "Fabricated Coastal League",
    season: str = "2026/27",
    kickoff_at_utc: datetime = KICKOFF,
    home_team_label: str = "Fabricated Harbour FC",
    away_team_label: str = "Fabricated Orchard FC",
    competition_type: str = "LEAGUE",
    team_type: str = "CLUB",
    evidence: bytes = b"SYNTHETIC MANUAL FIXTURE EVIDENCE - NOT PROVIDER DATA\n",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    evidence_path = directory / "fixture-source.txt"
    evidence_path.write_bytes(evidence)
    document = directory / "reviewed-fixture-manual.json"
    document.write_text(
        json.dumps(
            {
                "schema_version": REVIEWED_FIXTURE_MANUAL_ARCHIVE_SCHEMA_VERSION,
                "fixtures": [
                    {
                        "competition_label": competition_label,
                        "season": season,
                        "kickoff_at_utc": kickoff_at_utc.isoformat(),
                        "home_team_label": home_team_label,
                        "away_team_label": away_team_label,
                        "competition_type": competition_type,
                        "team_type": team_type,
                        "source_reference": "synthetic://reviewed-fixture-manual",
                        "source_artifact_path": evidence_path.name,
                        "source_artifact_sha256": hashlib.sha256(
                            evidence_path.read_bytes()
                        ).hexdigest(),
                        "captured_at_utc": CAPTURED.isoformat(),
                        "entered_by": entered_by,
                        "review_level": review_level,
                        "reviewed_by": reviewed_by,
                        "reviewed_at_utc": REVIEWED.isoformat(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return document


def _empty_catalog() -> MatchIdentityCatalog:
    return MatchIdentityCatalog(
        team_identities=(),
        competition_mappings=(),
        canonical_matches=(),
        explicit_mappings=(),
    )


def _catalog() -> MatchIdentityCatalog:
    competition = Competition(
        competition_id="competition-coastal",
        canonical_key="competition-coastal-key",
        name="Fabricated Coastal League",
        country_code="GB",
    )
    home_team = Team(
        team_id="team-harbour",
        canonical_key="team-harbour-key",
        name="Fabricated Harbour FC",
    )
    away_team = Team(
        team_id="team-orchard",
        canonical_key="team-orchard-key",
        name="Fabricated Orchard FC",
    )
    match = Match(
        match_id="canonical-match",
        competition_id=competition.competition_id,
        home_team_id=home_team.team_id,
        away_team_id=away_team.team_id,
        kickoff_at_utc=KICKOFF,
        available_at_utc=CAPTURED,
    )
    identity = CanonicalMatchIdentity(
        internal_match_id=match.match_id,
        internal_competition_id=competition.competition_id,
        internal_home_team_id=home_team.team_id,
        internal_away_team_id=away_team.team_id,
        season="2026/27",
        competition_type="LEAGUE",
        kickoff_at_utc=KICKOFF,
    )
    return MatchIdentityCatalog(
        team_identities=(
            TeamIdentity(
                internal_team_id=home_team.team_id,
                canonical_name=home_team.name,
                aliases=(
                    Alias(
                        provider_code="SYNTHETIC_FIXTURE",
                        provider_team_id="fixture-home",
                        provider_team_name=home_team.name,
                        language="en",
                    ),
                ),
            ),
            TeamIdentity(
                internal_team_id=away_team.team_id,
                canonical_name=away_team.name,
                aliases=(
                    Alias(
                        provider_code="SYNTHETIC_FIXTURE",
                        provider_team_id="fixture-away",
                        provider_team_name=away_team.name,
                        language="en",
                    ),
                ),
            ),
        ),
        competition_mappings=(
            CompetitionMapping(
                internal_competition_id=competition.competition_id,
                provider_code="SYNTHETIC_FIXTURE",
                provider_competition_id="fixture-competition",
                provider_competition_name=competition.name,
                language="en",
                season="2026/27",
                competition_type="LEAGUE",
            ),
        ),
        canonical_matches=(identity,),
        explicit_mappings=(),
        canonical_anchors=(
            CanonicalFixtureAnchor(
                competition=competition,
                home_team=home_team,
                away_team=away_team,
                match=match,
                identity=identity,
            ),
        ),
    )
