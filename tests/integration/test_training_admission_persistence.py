"""Contract-only local fixtures; none of these bytes are production evidence."""

import hashlib
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from threading import Barrier
from traceback import format_exception

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.application.training_admission import (
    PrepareTrainingFactBindingsService,
    SealSourceRightsReviewService,
    TrainingFactAdmissionCandidateV1,
)
from football_system.domain.archive import canonical_json, match_result_payload_sha256
from football_system.domain.identity import CanonicalMatchIdentity
from football_system.domain.match import ProviderMatchMapping
from football_system.domain.settlement import MatchResult
from football_system.domain.training_admission import (
    SOURCE_RIGHTS_PAYLOAD_V1,
    LocalReviewEvidenceV1,
    MatchResultAdmissionContentV1,
    MatchResultAdmissionV1,
    MatchSeasonMembershipContentV1,
    MatchSeasonMembershipV1,
    SourceRightsPayloadV1,
    SourceRightsPermittedUse,
    TrainingFactAdmissionV1,
    TrainingFixtureSourceV1,
    normalized_match_result_record_sha256,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database import (
    training_admission_repository as repository_module,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
    TrainingFactBindingRecord,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.database.training_admission_repository import (
    ControlledTrainingCorrectionRequired,
    SqlAlchemyTrainingAdmissionRepository,
    TrainingCaptureReceiptV1,
)
from football_system.infrastructure.database.training_admission_schema import (
    TRAINING_ADMISSION_TABLES,
)
from football_system.infrastructure.files.training_evidence import (
    CapturedRecordReferenceV1,
    LocalTrainingEvidence,
    TrainingFactSubmissionV1,
    TrainingJsonAdapterV1,
    TrainingSourceEvidenceV1,
    provider_record_sha256,
    strict_json_bytes,
    training_review_input_sha256,
)

UTC = timezone.utc
SOURCE = datetime(2025, 5, 17, tzinfo=UTC)
LOCAL = datetime(2026, 1, 2, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.value = LOCAL
        self.calls = []

    def __call__(self):
        self.value += timedelta(seconds=1)
        self.calls.append(self.value)
        return self.value


def write_evidence(root, name, value):
    payload = (
        value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    )
    (root / name).write_bytes(payload)
    return LocalReviewEvidenceV1(
        evidence_reference=name, evidence_sha256=hashlib.sha256(payload).hexdigest()
    )


def review_document(*, schema, digest, at):
    return dict(
        schema_version="TRAINING_LOCAL_REVIEW_V1",
        attested_schema_version=schema,
        attested_payload_hash=digest,
        prepared_by="operator",
        authorized_reviewer="reviewer",
        reviewed_at_utc=at,
        source_ids=["source"],
        source_classification="REAL_SOURCE_DATA",
        approved=True,
        retention_compatible=True,
    )


@pytest.fixture
def lane(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'lane.db').as_posix()}")
    create_schema(engine)
    sessions = create_session_factory(engine)
    clock = Clock()
    authority = write_evidence(
        tmp_path,
        "authority.json",
        dict(
            schema_version="TRAINING_REVIEWER_AUTHORITY_V1",
            issued_by="governance",
            authorized_reviewer="reviewer",
            source_ids=["source"],
            attested_schema_versions=[
                SOURCE_RIGHTS_PAYLOAD_V1,
                "TRAINING_FACT_REVIEW_INPUT_V1",
            ],
            effective_at_utc="2026-01-01T00:00:00Z",
            expires_at_utc="2027-01-01T00:00:00Z",
        ),
    )
    terms = write_evidence(
        tmp_path,
        "terms.txt",
        b"Test-only terms; immutable local retention is permitted.",
    )
    rights = SourceRightsPayloadV1.freeze(
        source_ids=("source",),
        permitted_uses=tuple(SourceRightsPermittedUse),
        source_owner="owner",
        product_name="contract-fixture",
        terms_version="v1",
        terms_reference=terms.evidence_reference,
        terms_sha256=terms.evidence_sha256,
        jurisdiction="TEST",
        effective_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at_utc=datetime(2027, 1, 1, tzinfo=UTC),
        raw_retention_rule="retain locally",
        derived_retention_rule="retain locally",
        subscription_end_retention_rule="retain locally",
        deletion_obligation="none",
        public_repository_boundary="no raw/evidence in public Git",
    )
    reviewed = datetime(2026, 1, 1, 12, tzinfo=UTC)
    review = write_evidence(
        tmp_path,
        "rights-review.json",
        review_document(
            schema=SOURCE_RIGHTS_PAYLOAD_V1,
            digest=tagged_canonical_sha256(SOURCE_RIGHTS_PAYLOAD_V1, rights),
            at=reviewed,
        ),
    )
    attestation = SealSourceRightsReviewService().seal(
        rights_payload=rights,
        authorized_reviewer="reviewer",
        reviewer_authority_reference=authority.evidence_reference,
        authority_sha256=authority.evidence_sha256,
        reviewed_at_utc=reviewed,
        evidence=review,
    )
    evidence = LocalTrainingEvidence(
        tmp_path,
        trusted_authorities={authority.evidence_reference: authority.evidence_sha256},
    )
    repo = SqlAlchemyTrainingAdmissionRepository(
        sessions, evidence=evidence, operator_id="operator", clock=clock
    )
    context = SimpleNamespace(
        root=tmp_path,
        engine=engine,
        sessions=sessions,
        clock=clock,
        rights=rights,
        attestation=attestation,
        authority=authority,
        repo=repo,
    )
    yield context
    engine.dispose()


def seed_identities(lane, count=2):
    with lane.sessions.begin() as session:
        session.add(
            ProviderRecord(
                provider_id="provider",
                code="PROVIDER",
                name="Provider",
                provider_kind="TEST",
            )
        )
        session.add(
            CompetitionRecord(
                competition_id="league",
                canonical_key="league",
                name="League",
                country_code="TST",
            )
        )
        for team in ("home", "away"):
            session.add(
                TeamRecord(
                    team_id=team, canonical_key=team, name=team, team_type="CLUB"
                )
            )
        session.flush()
        for team in ("home", "away"):
            session.add(
                ProviderTeamAliasRecord(
                    alias_id=f"alias-{team}",
                    internal_team_id=team,
                    provider_id="provider",
                    provider_team_id=f"p-{team}",
                    provider_team_name=team,
                    language="en",
                    team_type="CLUB",
                    available_at_utc=SOURCE,
                )
            )
        session.add(
            ProviderCompetitionMappingRecord(
                mapping_id="competition-mapping",
                internal_competition_id="league",
                provider_id="provider",
                provider_competition_id="p-league",
                provider_competition_name="League",
                language="en",
                season="2024/25",
                competition_type="LEAGUE",
                available_at_utc=SOURCE,
            )
        )
        for index in range(count):
            session.add(
                MatchRecord(
                    internal_match_id=f"match-{index}",
                    competition_id="league",
                    home_team_id="home",
                    away_team_id="away",
                    kickoff_at_utc=SOURCE + timedelta(days=index + 1),
                    status="FINISHED",
                    available_at_utc=SOURCE,
                    created_at_utc=LOCAL,
                )
            )
        session.flush()
        for index in range(count):
            session.add(
                CanonicalMatchIdentityRecord(
                    internal_match_id=f"match-{index}",
                    season="2024/25",
                    competition_type="LEAGUE",
                    available_at_utc=SOURCE,
                )
            )
            session.add(
                ProviderMatchMappingRecord(
                    mapping_id=f"mapping-{index}",
                    provider_id="provider",
                    external_namespace="fixture",
                    external_match_id=f"fixture-{index}",
                    internal_match_id=f"match-{index}",
                    resolution_method="EXPLICIT_MAPPING",
                    confidence=1,
                    available_at_utc=SOURCE,
                )
            )


def prepare(lane, *, count=2, raw_change=None):
    lane.recorded = lane.repo.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    seed_identities(lane, count)
    records = {role: [] for role in ("fixture", "scope", "result")}
    scope_paths = {
        key: f"/{key}"
        for key in ("fixture_key", "competition_id", "season_id", "available_at_utc")
    }
    fixture_paths = {
        **scope_paths,
        **{
            key: f"/{key}" for key in ("home_team_id", "away_team_id", "kickoff_at_utc")
        },
    }
    result_paths = {
        **fixture_paths,
        **{
            key: f"/{key}"
            for key in (
                "result_key",
                "status",
                "score_semantics",
                "home_goals",
                "away_goals",
                "finalized_at_utc",
                "observed_at_utc",
            )
        },
    }
    adapter = TrainingJsonAdapterV1(
        provider_code="PROVIDER",
        provider_fixture_namespace="fixture",
        adapter_name="contract-json",
        adapter_version="1",
        status_mapping_version="status-v1",
        season_mapping_version="season-v1",
        mapping_policy_version="mapping-v1",
        regular_time_final_status="FT",
        regular_time_score_semantics="90_MINUTES_PLUS_STOPPAGE",
        fixture=fixture_paths,
        scope=scope_paths,
        result=result_paths,
    )
    lane.adapter = write_evidence(lane.root, "adapter.json", adapter)
    for index in range(count):
        kickoff = SOURCE + timedelta(days=index + 1)
        scope = dict(
            fixture_key=f"fixture-{index}",
            competition_id="p-league",
            season_id="p-2024-25",
            available_at_utc=SOURCE.isoformat(),
        )
        fixture = dict(
            **scope,
            home_team_id="p-home",
            away_team_id="p-away",
            kickoff_at_utc=kickoff.isoformat(),
        )
        result = dict(
            **fixture,
            result_key=f"result-{index}",
            status="FT",
            score_semantics="90_MINUTES_PLUS_STOPPAGE",
            home_goals=2,
            away_goals=1,
            finalized_at_utc=(kickoff + timedelta(hours=2)).isoformat(),
            observed_at_utc=(kickoff + timedelta(hours=3)).isoformat(),
        )
        result["available_at_utc"] = (kickoff + timedelta(hours=4)).isoformat()
        for role, record in (
            ("fixture", fixture),
            ("scope", scope),
            ("result", result),
        ):
            record["unmapped_audit_field"] = "must be included in full record hash"
            if raw_change and raw_change[0] == role and index == 0:
                if raw_change[2] is None:
                    record.pop(raw_change[1])
                else:
                    record[raw_change[1]] = raw_change[2]
            records[role].append(record)
    receipts = {}
    for role, values in records.items():
        write_evidence(lane.root, f"{role}.json", {"records": values})
        receipts[role] = lane.repo.capture_local_json(
            request_key=f"capture-{role}",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference=f"{role}.json",
        )
    reviewed = lane.clock()
    submissions = []
    for index in range(count):
        kickoff = SOURCE + timedelta(days=index + 1)
        f, s, r = (receipts[role] for role in ("fixture", "scope", "result"))
        source = TrainingFixtureSourceV1(
            source_id="source",
            provider_code="PROVIDER",
            provider_fixture_namespace="fixture",
            provider_fixture_key=f"fixture-{index}",
            internal_match_id=f"match-{index}",
            fixture_source_archive_id=f.capture_receipt_id,
            fixture_source_archive_payload_sha256=f.payload_sha256,
            fixture_source_archive_created_at_utc=f.archive_created_at_utc,
            fixture_source_record_id=f"/records/{index}",
            fixture_record_sha256=provider_record_sha256(records["fixture"][index]),
            source_available_at_utc=SOURCE,
            local_imported_at_utc=f.local_imported_at_utc,
            registered_at_utc=f.registered_at_utc,
        )
        mapping = ProviderMatchMapping(
            mapping_id=f"mapping-{index}",
            provider_code="PROVIDER",
            external_namespace="fixture",
            external_match_id=f"fixture-{index}",
            internal_match_id=f"match-{index}",
            resolution_method="EXPLICIT_MAPPING",
            confidence=1,
            available_at_utc=SOURCE,
        )
        identity = CanonicalMatchIdentity(
            internal_match_id=f"match-{index}",
            internal_competition_id="league",
            internal_home_team_id="home",
            internal_away_team_id="away",
            season="2024/25",
            competition_type="LEAGUE",
            kickoff_at_utc=kickoff,
        )
        membership = MatchSeasonMembershipV1.freeze(
            content_payload=MatchSeasonMembershipContentV1(
                source_id="source",
                provider_code="PROVIDER",
                provider_competition_id="p-league",
                provider_season_id="p-2024-25",
                provider_season_candidate_ids=("p-2024-25",),
                provider_fixture_namespace="fixture",
                provider_fixture_key=f"fixture-{index}",
                provider_mapping_id=mapping.mapping_id,
                internal_match_id=f"match-{index}",
                fixture_source_record_id=source.fixture_source_record_id,
                fixture_record_sha256=source.fixture_record_sha256,
                provider_scope_raw_artifact_id=s.capture_receipt_id,
                provider_scope_payload_sha256=s.payload_sha256,
                provider_scope_created_at_utc=s.archive_created_at_utc,
                provider_scope_record_sha256=provider_record_sha256(
                    records["scope"][index]
                ),
                provider_competition_field_path="/competition_id",
                provider_season_field_path="/season_id",
                provider_fixture_field_path="/fixture_key",
                season_assignment_method="PROVIDER_EXPLICIT_FIELDS",
                season_mapping_version="season-v1",
                canonical_competition_id="league",
                canonical_season_id="2024/25",
                source_available_at_utc=SOURCE,
                local_imported_at_utc=s.local_imported_at_utc,
                registered_at_utc=s.registered_at_utc,
                mapping_policy_version="mapping-v1",
                reviewed_by="reviewer",
                reviewed_at_utc=reviewed,
            )
        )
        normalized = MatchResult(
            match_result_id=f"normalized-{index}",
            match_id=f"match-{index}",
            provider_code="PROVIDER",
            home_goals=2,
            away_goals=1,
            observed_at_utc=kickoff + timedelta(hours=3),
            available_at_utc=kickoff + timedelta(hours=4),
            ingested_at_utc=kickoff + timedelta(hours=4),
            source_result_key=f"result-{index}",
            payload_hash=match_result_payload_sha256(2, 1),
        )
        result_admission = MatchResultAdmissionV1.freeze(
            content_payload=MatchResultAdmissionContentV1(
                source_id="source",
                internal_match_id=f"match-{index}",
                match_result_id=normalized.match_result_id,
                provider_code="PROVIDER",
                provider_result_key=f"result-{index}",
                provider_raw_status="FT",
                status_mapping_version="status-v1",
                provider_status_category="REGULAR_TIME_FINAL",
                score_semantics="REGULAR_TIME_ONLY",
                regular_time_home_goals=2,
                regular_time_away_goals=1,
                provider_finalized_at_utc=kickoff + timedelta(hours=2),
                source_observed_at_utc=normalized.observed_at_utc,
                source_available_at_utc=normalized.available_at_utc,
                raw_artifact_id=r.capture_receipt_id,
                raw_artifact_payload_sha256=r.payload_sha256,
                raw_artifact_created_at_utc=r.archive_created_at_utc,
                raw_record_sha256=provider_record_sha256(records["result"][index]),
                normalized_record_sha256=normalized_match_result_record_sha256(
                    normalized
                ),
                local_imported_at_utc=r.local_imported_at_utc,
                registered_at_utc=r.registered_at_utc,
                adapter_name="contract-json",
                adapter_version="1",
                reviewed_by="reviewer",
                reviewed_at_utc=reviewed,
            )
        )
        candidate = TrainingFactAdmissionCandidateV1(
            fixture_source=source,
            season_membership=membership,
            provider_mapping=mapping,
            canonical_identity=identity,
            match_result_admission=result_admission,
            normalized_result=normalized,
        )
        evidence = TrainingSourceEvidenceV1(
            **{
                role: CapturedRecordReferenceV1(
                    capture_receipt_id=receipts[role].capture_receipt_id,
                    record_pointer=f"/records/{index}",
                )
                for role in receipts
            },
            adapter=lane.adapter,
            home_team_alias_id="alias-home",
            away_team_alias_id="alias-away",
            competition_mapping_id="competition-mapping",
        )
        review = write_evidence(
            lane.root,
            f"review-{index}.json",
            review_document(
                schema="TRAINING_FACT_REVIEW_INPUT_V1",
                digest=training_review_input_sha256(candidate, evidence),
                at=reviewed,
            ),
        )
        submissions.append(
            TrainingFactSubmissionV1(
                candidate=candidate,
                source_evidence=evidence,
                reviewer_evidence=review,
                reviewer_authority=lane.authority,
            )
        )
    lane.receipts, lane.submissions = receipts, tuple(submissions)
    return lane.submissions


def admit(lane, *, submissions=None, key="admit"):
    return lane.repo.admit(
        request_key=key,
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        submissions=lane.submissions if submissions is None else submissions,
    )


def counts(lane):
    with lane.engine.connect() as connection:
        return {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM {name}"))
            for name in (*TRAINING_ADMISSION_TABLES, "match_results")
        }


def test_atomic_materialization_round_trip_and_exact_retry(lane):
    prepare(lane)
    initial = len(lane.clock.calls)
    value = admit(lane, submissions=reversed(lane.submissions))
    assert value.content_payload.admitted_fact_count == 2
    assert value.content_payload.source_data_mode.value == "SOURCE_TIME_RESEARCH"
    assert value.content_payload.retrospective is True
    assert [fact.content_payload.sequence for fact in value.facts] == [0, 1]
    assert (
        value.content_payload.actual_started_at_utc,
        value.content_payload.actual_completed_at_utc,
        value.content_payload.persisted_at_utc,
    ) == tuple(lane.clock.calls[initial : initial + 3])
    assert lane.clock.calls[-1] > value.content_payload.persisted_at_utc
    assert lane.repo.load(value.training_fact_admission_id) == value
    historical = SqlAlchemyHistoricalRepository(lane.sessions)
    for submission in lane.submissions:
        assert (
            historical.find_match_result(
                submission.candidate.normalized_result.match_result_id
            )
            == submission.candidate.normalized_result
        )
    before = counts(lane)
    lane.clock.value = datetime(2028, 1, 1, tzinfo=UTC)
    calls = len(lane.clock.calls)
    assert admit(lane, submissions=reversed(lane.submissions)) == value
    assert len(lane.clock.calls) == calls
    assert counts(lane) == before
    with pytest.raises(ValueError, match="retry request"):
        admit(lane)


def test_exact_existing_result_reuse(lane):
    prepare(lane)
    historical = SqlAlchemyHistoricalRepository(lane.sessions)
    for item in lane.submissions:
        historical.append_match_result(item.candidate.normalized_result)
    assert admit(lane).content_payload.admitted_fact_count == 2
    assert counts(lane)["match_results"] == 2


def test_rights_actual_time_and_retry_identity(lane):
    value = lane.repo.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    assert value.content_payload.recorded_at_utc == lane.clock.calls[-2]
    assert lane.clock.calls[-1] > value.content_payload.recorded_at_utc
    assert (
        value.content_payload.recorded_at_utc
        != lane.attestation.content_payload.reviewed_at_utc
    )
    lane.clock.value = datetime(2028, 1, 1, tzinfo=UTC)
    assert (
        lane.repo.record(
            request_key="rights",
            rights_payload=lane.rights,
            reviewer_attestation=lane.attestation,
        )
        == value
    )
    changed = lane.rights.model_copy(update={"terms_version": "changed"})
    with pytest.raises(ValueError, match="retry request"):
        lane.repo.record(
            request_key="rights",
            rights_payload=changed,
            reviewer_attestation=lane.attestation,
        )


@pytest.mark.parametrize(
    "filename", ["terms.txt", "authority.json", "rights-review.json"]
)
@pytest.mark.parametrize("before_record", [True, False])
def test_rights_reads_actual_evidence_and_rejects_changed_bytes(
    lane, filename, before_record
):
    if not before_record:
        lane.repo.record(
            request_key="rights",
            rights_payload=lane.rights,
            reviewer_attestation=lane.attestation,
        )
    (lane.root / filename).write_bytes(b"changed evidence")
    with pytest.raises(ValueError, match="SHA-256"):
        lane.repo.record(
            request_key="rights",
            rights_payload=lane.rights,
            reviewer_attestation=lane.attestation,
        )
    assert counts(lane)["source_rights_admissions"] == (0 if before_record else 1)


def test_capture_uses_real_import_time_not_file_mtime_and_retries(lane):
    prepare(lane)
    receipt = lane.receipts["fixture"]
    os.utime(lane.root / "fixture.json", (0, 0))
    loaded, payload = lane.repo.load_capture(receipt.capture_receipt_id)
    assert loaded == receipt
    assert receipt.capture_kind == "LOCAL_FILE_IMPORT"
    assert receipt.upstream_acquired_at_utc is None
    assert receipt.local_imported_at_utc > LOCAL > SOURCE
    assert hashlib.sha256(payload).hexdigest() == receipt.payload_sha256
    lane.clock.value = datetime(2028, 1, 1, tzinfo=UTC)
    assert (
        lane.repo.capture_local_json(
            request_key="capture-fixture",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference="fixture.json",
        )
        == receipt
    )
    (lane.root / "fixture.json").write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError, match="SHA-256"):
        lane.repo.capture_local_json(
            request_key="capture-fixture",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference="fixture.json",
        )


@pytest.mark.parametrize(
    "role,field,value",
    [
        ("fixture", "fixture_key", "wrong"),
        ("fixture", "competition_id", "wrong"),
        ("fixture", "season_id", "wrong"),
        ("fixture", "home_team_id", "p-away"),
        ("fixture", "kickoff_at_utc", "2025-05-18T01:00:00Z"),
        ("fixture", "available_at_utc", "2025-05-17T01:00:00Z"),
        ("scope", "fixture_key", "wrong"),
        ("scope", "competition_id", "wrong"),
        ("scope", "season_id", None),
        ("scope", "season_id", ["p-2024-25", "other"]),
        ("scope", "available_at_utc", "2025-05-17T01:00:00Z"),
        ("result", "fixture_key", "wrong"),
        ("result", "competition_id", "wrong"),
        ("result", "season_id", "wrong"),
        ("result", "status", "AET"),
        ("result", "status", "PEN"),
        ("result", "status", "POSTPONED"),
        ("result", "status", "ABANDONED"),
        ("result", "status", "IN_PROGRESS"),
        ("result", "status", "UNKNOWN"),
        ("result", "score_semantics", "EXTRA_TIME_INCLUDED"),
        ("result", "score_semantics", None),
        ("result", "home_goals", 1),
        ("result", "away_goals", 2),
        ("result", "home_goals", True),
        ("result", "home_goals", "2"),
        ("result", "home_goals", 2.0),
        ("result", "result_key", "wrong"),
        ("result", "home_team_id", "p-away"),
        ("result", "away_team_id", "p-home"),
        ("result", "finalized_at_utc", None),
        ("result", "finalized_at_utc", "2025-05-18T02:30:00Z"),
        ("result", "observed_at_utc", "2025-05-18T03:30:00Z"),
        ("result", "available_at_utc", None),
        ("result", "available_at_utc", "2025-05-18T04:30:00Z"),
        ("result", "available_at_utc", "2025-05-18T04:00:00"),
    ],
)
def test_rehashed_reviewed_raw_bytes_must_actually_support_candidate(
    lane, role, field, value
):
    prepare(lane, count=1, raw_change=(role, field, value))
    before = counts(lane)
    with pytest.raises(ValueError):
        admit(lane)
    assert counts(lane) == before


@pytest.mark.parametrize(
    "filename",
    ["fixture.json", "scope.json", "result.json", "adapter.json", "review-0.json"],
)
def test_load_and_retry_reverify_source_adapter_and_reviewer_bytes(lane, filename):
    prepare(lane)
    value = admit(lane)
    before = counts(lane)
    (lane.root / filename).write_bytes(b'{"changed":true}')
    with pytest.raises(ValueError, match="SHA-256"):
        lane.repo.load(value.training_fact_admission_id)
    with pytest.raises(ValueError, match="SHA-256"):
        admit(lane)
    assert counts(lane) == before


def test_unregistered_receipt_and_invented_candidate_times_rejected(lane):
    prepare(lane, count=1)
    item = lane.submissions[0]
    changed = item.candidate.fixture_source.model_copy(
        update={"local_imported_at_utc": LOCAL}
    )
    item = item.model_copy(
        update={
            "candidate": item.candidate.model_copy(update={"fixture_source": changed})
        }
    )
    with pytest.raises(ValueError, match="registered capture"):
        admit(lane, submissions=(item,))
    ref = lane.submissions[0].source_evidence.fixture.model_copy(
        update={"capture_receipt_id": "not-registered"}
    )
    item = lane.submissions[0].model_copy(
        update={
            "source_evidence": lane.submissions[0].source_evidence.model_copy(
                update={"fixture": ref}
            )
        }
    )
    with pytest.raises(ValueError, match="missing preexisting training_capture"):
        admit(lane, submissions=(item,))
    assert counts(lane)["match_results"] == 0


@pytest.mark.parametrize(
    "model,column,value",
    [
        (MatchRecord, "home_team_id", "away"),
        (CanonicalMatchIdentityRecord, "season", "other"),
        (ProviderMatchMappingRecord, "resolution_method", "other"),
        (ProviderTeamAliasRecord, "provider_team_id", "other"),
        (ProviderCompetitionMappingRecord, "season", "other"),
    ],
)
def test_all_preexisting_identity_projections_are_verified(lane, model, column, value):
    prepare(lane, count=1)
    table = model.__table__.name
    with lane.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table}_append_only_update")
        if table == "matches":
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            text(f"UPDATE {table} SET {column} = :value"), {"value": value}
        )
    before = counts(lane)
    with pytest.raises(ValueError, match="identity|mapping"):
        admit(lane)
    assert counts(lane) == before


def test_late_child_failure_rolls_back_results_and_entire_admission(lane, monkeypatch):
    prepare(lane)
    before = counts(lane)
    original = repository_module._children

    def broken(*args):
        children = original(*args)
        if children[-1].sequence == 1:
            children[-1].match_result_id = "missing-result"
        return children

    monkeypatch.setattr(repository_module, "_children", broken)
    with pytest.raises(IntegrityError):
        admit(lane)
    assert counts(lane) == before
    monkeypatch.setattr(repository_module, "_children", original)
    assert admit(lane).content_payload.admitted_fact_count == 2


@pytest.mark.parametrize("table", TRAINING_ADMISSION_TABLES)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "REPLACE"])
def test_all_lane_rows_are_append_only_including_duplicate_replace(
    lane, table, operation
):
    prepare(lane, count=1)
    admit(lane)
    sql = {
        "UPDATE": f"UPDATE {table} SET row_sha256 = row_sha256",
        "DELETE": f"DELETE FROM {table}",
        "REPLACE": f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
    }[operation]
    with pytest.raises(IntegrityError):
        with lane.engine.begin() as connection:
            connection.execute(text(sql))


@pytest.mark.parametrize("table", TRAINING_ADMISSION_TABLES)
def test_read_verifies_every_parent_child_row_even_if_update_guard_was_removed(
    lane, table
):
    prepare(lane, count=1)
    value = admit(lane)
    with lane.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table}_append_only_update")
        connection.execute(text(f"UPDATE {table} SET artifact_json = '{{}}'"))
    with pytest.raises(ValueError, match="integrity"):
        lane.repo.load(value.training_fact_admission_id)


def test_read_detects_corrupted_stored_capture_blob(lane):
    prepare(lane, count=1)
    value = admit(lane)
    with lane.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_training_capture_receipts_append_only_update"
        )
        connection.execute(
            text("UPDATE training_capture_receipts SET payload_bytes = :payload"),
            {"payload": b"corrupted"},
        )
    with pytest.raises(ValueError, match="capture bytes SHA-256"):
        lane.repo.load(value.training_fact_admission_id)


def test_read_detects_missing_children_and_rejects_new_children(lane):
    prepare(lane, count=1)
    value = admit(lane)
    with lane.sessions() as session:
        row = session.scalar(select(TrainingFactBindingRecord))
        values = {
            column.name: getattr(row, column.name) for column in row.__table__.columns
        }
    values.update(sequence=1, training_fact_binding_id="new-binding")
    with pytest.raises(IntegrityError, match="sealed training"):
        with lane.sessions.begin() as session:
            session.add(TrainingFactBindingRecord(**values))
    with lane.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_training_fact_bindings_append_only_delete"
        )
        connection.execute(text("DELETE FROM training_fact_bindings"))
    with pytest.raises(ValueError, match="child count"):
        lane.repo.load(value.training_fact_admission_id)


def test_new_correction_invalidates_reads_and_new_admissions(lane):
    prepare(lane, count=1)
    value = admit(lane)
    original = lane.submissions[0].candidate.normalized_result
    correction = original.model_copy(
        update={
            "match_result_id": "corrected",
            "source_result_key": "corrected",
            "home_goals": 3,
            "payload_hash": match_result_payload_sha256(3, 1),
            "supersedes_match_result_id": original.match_result_id,
            "ingested_at_utc": original.ingested_at_utc + timedelta(seconds=1),
        }
    )
    SqlAlchemyHistoricalRepository(lane.sessions).append_match_result(correction)
    with pytest.raises(
        ControlledTrainingCorrectionRequired, match="implementation is missing"
    ):
        lane.repo.load(value.training_fact_admission_id)
    with pytest.raises(ControlledTrainingCorrectionRequired):
        admit(lane, key="new-admission")


def test_expired_rights_and_backward_clocks_roll_back(lane):
    prepare(lane, count=1)
    before = counts(lane)
    lane.clock.value = datetime(2027, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="not active"):
        admit(lane)
    assert counts(lane) == before
    times = iter(
        [
            LOCAL + timedelta(hours=1),
            LOCAL + timedelta(minutes=59),
            LOCAL + timedelta(hours=2),
        ]
    )
    lane.repo._clock = lambda: next(times)
    with pytest.raises(ValueError, match="timestamps"):
        admit(lane)
    assert counts(lane) == before


def test_naive_operation_clock_rejected(lane):
    lane.repo._clock = lambda: datetime(2026, 1, 2)
    with pytest.raises(ValueError, match="aware UTC"):
        lane.repo.record(
            request_key="rights",
            rights_payload=lane.rights,
            reviewer_attestation=lane.attestation,
        )
    assert counts(lane)["source_rights_admissions"] == 0


def test_migration_upgrade_empty_downgrade_and_populated_refusal(tmp_path):
    config = Config("alembic.ini")
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "6e4b1a9c2d73")
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    assert set(TRAINING_ADMISSION_TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "c2ebf618d354"
        )
    engine.dispose()
    command.downgrade(config, "6e4b1a9c2d73")
    engine = create_database_engine(url)
    assert not set(TRAINING_ADMISSION_TABLES) & set(inspect(engine).get_table_names())
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_rights_admissions (source_rights_admission_id, admission_hash, recorded_at_utc, artifact_json, row_sha256, request_key, request_sha256, request_json, operator_id) VALUES ('rights', :hash, '2026-01-01', '{}', :hash, 'retry', :hash, '{}', 'operator')"
            ),
            {"hash": "a" * 64},
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="immutable lineage exists"):
        command.downgrade(config, "6e4b1a9c2d73")
    engine = create_database_engine(url)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "8a7c2f4e9b10"
        )
    engine.dispose()


def test_training_migration_offline_upgrade_and_fail_closed_downgrade(tmp_path):
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{(tmp_path / 'never-created.db').as_posix()}"
    )
    command.upgrade(config, "6e4b1a9c2d73:8a7c2f4e9b10", sql=True)
    assert "CREATE TABLE training_fact_bindings" in output.getvalue()
    assert "DEFERRABLE INITIALLY DEFERRED" in output.getvalue()
    with pytest.raises(RuntimeError, match="offline training downgrade"):
        command.downgrade(config, "8a7c2f4e9b10:6e4b1a9c2d73", sql=True)
    assert not (tmp_path / "never-created.db").exists()


def test_capture_receipt_seal_covers_all_actual_times(lane):
    prepare(lane, count=1)
    receipt = lane.receipts["fixture"]
    document = receipt.model_dump(mode="python")
    document["local_imported_at_utc"] -= timedelta(seconds=1)
    with pytest.raises(ValueError, match="receipt seal"):
        TrainingCaptureReceiptV1.model_validate(document)
    values = {
        key: value
        for key, value in document.items()
        if key not in {"capture_receipt_id", "receipt_hash"}
    }
    altered = TrainingCaptureReceiptV1.freeze(**values)
    assert altered.capture_receipt_id != receipt.capture_receipt_id
    assert altered.receipt_hash != receipt.receipt_hash


def test_concurrent_exact_retry_serializes_one_operation(lane):
    prepare(lane, count=1)
    calls = len(lane.clock.calls)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        return admit(lane)

    with ThreadPoolExecutor(max_workers=2) as executor:
        left, right = (executor.submit(submit) for _ in range(2))
        assert left.result(timeout=30) == right.result(timeout=30)
    assert len(lane.clock.calls) == calls + 4
    assert counts(lane)["match_results"] == 1
    assert counts(lane)["training_fact_admissions"] == 1


def test_changed_registered_source_lineage_is_an_unsupported_correction(lane):
    prepare(lane, count=1)
    admit(lane)
    item = lane.submissions[0]
    old = item.candidate.match_result_admission
    changed = MatchResultAdmissionV1.freeze(
        content_payload=old.content_payload.model_copy(update={"adapter_version": "2"})
    )
    candidate = item.candidate.model_copy(update={"match_result_admission": changed})
    adapter_payload = lane.repo.evidence.read("adapter.json")
    adapter = strict_json_bytes(adapter_payload)
    adapter["adapter_version"] = "2"
    evidence = item.source_evidence.model_copy(
        update={"adapter": write_evidence(lane.root, "adapter-v2.json", adapter)}
    )
    reviewer = write_evidence(
        lane.root,
        "review-v2.json",
        review_document(
            schema="TRAINING_FACT_REVIEW_INPUT_V1",
            digest=training_review_input_sha256(candidate, evidence),
            at=old.content_payload.reviewed_at_utc,
        ),
    )
    changed_item = item.model_copy(
        update={
            "candidate": candidate,
            "source_evidence": evidence,
            "reviewer_evidence": reviewer,
        }
    )
    before = counts(lane)
    with pytest.raises(
        ControlledTrainingCorrectionRequired, match="source/season/status"
    ):
        admit(lane, submissions=(changed_item,), key="changed-source")
    assert counts(lane) == before


def test_capture_requires_recorded_rights_before_reading_provider_file(lane):
    with pytest.raises(ValueError, match="source_rights_admissions"):
        lane.repo.capture_local_json(
            request_key="no-rights",
            source_rights_admission_id="unknown",
            source_id="source",
            provider_code="PROVIDER",
            evidence_reference="not-even-present.json",
        )
    assert counts(lane)["training_capture_receipts"] == 0


def test_legitimately_pinned_reviewer_may_also_record_rights(lane):
    other = SqlAlchemyTrainingAdmissionRepository(
        lane.sessions,
        evidence=lane.repo.evidence,
        operator_id="reviewer",
        clock=lane.clock,
    )
    value = other.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    assert value.content_payload.reviewer_attestation == lane.attestation
    assert other.load_rights(value.source_rights_admission_id) == value
    assert counts(lane)["source_rights_admissions"] == 1


def test_deferred_parent_foreign_key_rejects_orphan_graph(lane):
    prepare(lane, count=1)
    value = admit(lane)
    before = counts(lane)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with lane.engine.begin() as connection:
            columns = [
                column["name"]
                for column in inspect(connection).get_columns(
                    "training_fact_fixture_sources"
                )
            ]
            selection = [
                "'orphan-parent'" if name == "training_fact_admission_id" else name
                for name in columns
            ]
            connection.execute(
                text(
                    f"INSERT INTO training_fact_fixture_sources ({', '.join(columns)}) SELECT {', '.join(selection)} FROM training_fact_fixture_sources"
                )
            )
    assert counts(lane) == before
    assert lane.repo.load(value.training_fact_admission_id) == value


def test_current_normalized_result_full_record_is_reverified(lane):
    prepare(lane, count=1)
    value = admit(lane)
    with lane.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER trg_match_results_append_only_update")
        connection.execute(
            text("UPDATE match_results SET source_result_key = 'changed-key'")
        )
    with pytest.raises(ValueError, match="normalized result"):
        lane.repo.load(value.training_fact_admission_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_reference", "never-authorized.json"),
        ("source_id", "other-source"),
        ("provider_code", "OTHER_PROVIDER"),
        ("source_rights_admission_id", "other-rights"),
        ("operator_id", "other-operator"),
    ],
)
def test_expired_capture_retry_rejects_metadata_changes_before_any_file_read(
    lane, monkeypatch, field, value
):
    prepare(lane, count=1)
    lane.clock.value = datetime(2028, 1, 1, tzinfo=UTC)
    arguments = dict(
        request_key="capture-fixture",
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        source_id="source",
        provider_code="PROVIDER",
        evidence_reference="fixture.json",
    )
    if field == "operator_id":
        lane.repo.operator_id = value
    else:
        arguments[field] = value

    def forbidden_read(*args, **kwargs):
        pytest.fail("changed retry metadata reached an evidence read")

    monkeypatch.setattr(lane.repo.evidence, "read", forbidden_read)
    with pytest.raises(ValueError, match="retry request"):
        lane.repo.capture_local_json(**arguments)


def _shorten_test_authority(lane, expiry):
    permission = strict_json_bytes(lane.repo.evidence.read("authority.json"))
    permission["expires_at_utc"] = expiry.isoformat()
    lane.authority = write_evidence(lane.root, "authority.json", permission)
    lane.repo.evidence.trusted_authorities["authority.json"] = (
        lane.authority.evidence_sha256
    )
    content = lane.attestation.content_payload
    lane.attestation = SealSourceRightsReviewService().seal(
        rights_payload=lane.rights,
        authorized_reviewer=content.authorized_reviewer,
        reviewer_authority_reference="authority.json",
        authority_sha256=lane.authority.evidence_sha256,
        reviewed_at_utc=content.reviewed_at_utc,
        evidence=content.evidence,
    )


@pytest.mark.parametrize("operation", ["record", "capture", "admit"])
@pytest.mark.parametrize("phase", ["write", "readback"])
@pytest.mark.parametrize("grant", ["rights", "authority"])
def test_expiry_during_final_work_rolls_back_all_new_rows(
    lane, monkeypatch, operation, phase, grant
):
    expiry = (
        datetime(2026, 7, 1, tzinfo=UTC)
        if grant == "authority"
        else lane.rights.expires_at_utc
    )
    if grant == "authority":
        _shorten_test_authority(lane, expiry)
    if operation != "record":
        prepare(lane, count=1)
    before = counts(lane)
    table, method = {
        "record": ("source_rights_admissions", "_rights"),
        "capture": ("training_capture_receipts", "_capture"),
        "admit": ("training_fact_bindings", "_load"),
    }[operation]
    crossed = []

    def advance():
        crossed.append(phase)
        lane.clock.value = expiry

    def after_statement(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith(f"INSERT INTO {table} "):
            advance()

    if phase == "readback":
        original = getattr(lane.repo, method)

        def after_readback(*args, **kwargs):
            result = original(*args, **kwargs)
            advance()
            return result

        monkeypatch.setattr(lane.repo, method, after_readback)
    else:
        event.listen(lane.engine, "after_cursor_execute", after_statement)
    try:
        with pytest.raises(
            ValueError, match="rights.*(active|expired)|authority.*active"
        ):
            if operation == "record":
                lane.repo.record(
                    request_key="rights",
                    rights_payload=lane.rights,
                    reviewer_attestation=lane.attestation,
                )
            elif operation == "capture":
                lane.repo.capture_local_json(
                    request_key="new-capture",
                    source_rights_admission_id=lane.recorded.source_rights_admission_id,
                    source_id="source",
                    provider_code="PROVIDER",
                    evidence_reference="fixture.json",
                )
            else:
                admit(lane)
    finally:
        if phase == "write":
            event.remove(lane.engine, "after_cursor_execute", after_statement)
    assert crossed
    assert counts(lane) == before


def test_separate_fact_reviewer_authority_is_checked_at_transaction_exit(
    lane, monkeypatch
):
    prepare(lane, count=1)
    permission = strict_json_bytes(lane.repo.evidence.read("authority.json"))
    expiry = datetime(2026, 7, 1, tzinfo=UTC)
    permission["expires_at_utc"] = expiry.isoformat()
    authority = write_evidence(lane.root, "fact-authority.json", permission)
    lane.repo.evidence.trusted_authorities["fact-authority.json"] = (
        authority.evidence_sha256
    )
    item = lane.submissions[0].model_copy(update={"reviewer_authority": authority})
    original = lane.repo._load

    def after_load(*args):
        result = original(*args)
        lane.clock.value = expiry
        return result

    before = counts(lane)
    monkeypatch.setattr(lane.repo, "_load", after_load)
    with pytest.raises(ValueError, match="authority.*active"):
        admit(lane, submissions=(item,))
    assert counts(lane) == before


def test_child_preparation_precedes_completion_and_current_gate_follows_readback(
    lane, monkeypatch
):
    prepare(lane, count=1)
    original_children, original_load = repository_module._children, lane.repo._load
    initial = len(lane.clock.calls)
    phases = []

    def children(parent, *args):
        if parent is None:
            phases.append(("children", len(lane.clock.calls)))
        return original_children(parent, *args)

    def loaded(*args):
        value = original_load(*args)
        phases.append(("readback", len(lane.clock.calls)))
        return value

    monkeypatch.setattr(repository_module, "_children", children)
    monkeypatch.setattr(lane.repo, "_load", loaded)
    value = admit(lane)
    assert phases == [("children", initial + 1), ("readback", initial + 3)]
    assert len(lane.clock.calls) == initial + 4
    assert value.content_payload.persisted_at_utc < lane.clock.calls[-1]


def test_different_authorized_operation_actor_preserves_capture_and_review_provenance(
    lane,
):
    prepare(lane, count=1)
    lane.repo.operator_id = "reviewer"
    value = admit(lane)
    assert (
        value.facts[
            0
        ].content_payload.match_result_admission.content_payload.reviewed_by
        == "reviewer"
    )
    assert (
        lane.repo.load_capture(lane.receipts["fixture"].capture_receipt_id)[
            0
        ].imported_by
        == "operator"
    )
    assert (
        strict_json_bytes(lane.repo.evidence.read("review-0.json"))["prepared_by"]
        == "operator"
    )


def _direct_sql_graph(lane, mutate):
    facts = PrepareTrainingFactBindingsService().prepare(
        candidates=(item.candidate for item in lane.submissions)
    )
    at = lane.clock()
    admission = TrainingFactAdmissionV1.from_persisted(
        source_rights_admission=lane.recorded,
        facts=facts,
        actual_started_at_utc=at,
        actual_completed_at_utc=at,
        persisted_at_utc=at,
    )
    request = repository_module._request(
        "direct-sql",
        "operator",
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        submissions=lane.submissions,
    )
    parent = repository_module._admission_row(admission, request)
    children = list(
        repository_module._children(
            admission.training_fact_admission_id, facts[0], lane.submissions[0]
        )
    )
    mutate(parent, children)
    with lane.sessions.begin() as session:
        item = lane.submissions[0]
        repository_module._append_match_result(
            session,
            item.candidate.normalized_result,
            item.candidate.provider_mapping.mapping_id,
        )
        # Execute raw INSERTs: no repository validation, no ORM insert hooks.
        for row in [*children, parent]:
            session.execute(
                row.__table__.insert().values(
                    **{
                        column.name: getattr(row, column.name)
                        for column in row.__table__.columns
                    }
                )
            )
    return admission


@pytest.mark.parametrize("child_index", [0, 1, 2])
def test_direct_sql_rejects_child_pointing_to_a_different_declared_capture(
    lane, child_index
):
    prepare(lane, count=1)
    before = counts(lane)

    def change(parent, children):
        children[child_index].capture_receipt_id = lane.receipts[
            "result" if child_index != 2 else "fixture"
        ].capture_receipt_id

    with pytest.raises(IntegrityError, match="capture declaration or scope"):
        _direct_sql_graph(lane, change)
    assert counts(lane) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "other-source"),
        ("provider_code", "OTHER_PROVIDER"),
        ("internal_match_id", "other-match"),
        ("fixture_source_archive_payload_sha256", "a" * 64),
        ("registered_at_utc", "2026-01-01T00:00:00Z"),
    ],
)
def test_direct_sql_checks_declared_capture_scope_hash_and_timeline(lane, field, value):
    prepare(lane, count=1)

    def change(parent, children):
        document = strict_json_bytes(children[0].artifact_json.encode())
        document[field] = value
        children[0].artifact_json = canonical_json(document)

    with pytest.raises(IntegrityError, match="capture declaration or scope"):
        _direct_sql_graph(lane, change)
    assert counts(lane)["training_fact_admissions"] == 0
    assert counts(lane)["match_results"] == 0


def test_direct_sql_parent_seal_requires_exact_capture_rights(lane):
    prepare(lane, count=1)
    other = lane.repo.record(
        request_key="other-rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )

    def change(parent, children):
        parent.source_rights_admission_id = other.source_rights_admission_id
        document = strict_json_bytes(parent.artifact_json.encode())
        document["content_payload"]["source_rights_admission_id"] = (
            other.source_rights_admission_id
        )
        document["content_payload"]["source_rights_admission_hash"] = (
            other.admission_hash
        )
        document["source_rights_admission"] = other.model_dump(mode="json")
        parent.artifact_json = canonical_json(document)

    with pytest.raises(IntegrityError, match="capture rights"):
        _direct_sql_graph(lane, change)
    assert counts(lane)["match_results"] == 0


def test_direct_sql_parent_seal_requires_matching_embedded_source_graph(lane):
    prepare(lane, count=1)

    def change(parent, children):
        document = strict_json_bytes(parent.artifact_json.encode())
        document["facts"][0]["content_payload"]["fixture_source"][
            "fixture_source_archive_id"
        ] = lane.receipts["result"].capture_receipt_id
        parent.artifact_json = canonical_json(document)

    with pytest.raises(IntegrityError, match="artifact projection"):
        _direct_sql_graph(lane, change)
    assert counts(lane)["match_results"] == 0


@pytest.mark.parametrize("mismatch", ["provider", "rights"])
def test_direct_sql_rejects_actual_capture_mapping_or_binding_scope_mismatch(
    lane, mismatch
):
    prepare(lane, count=1)
    rights = lane.recorded
    provider_code = "PROVIDER"
    if mismatch == "provider":
        provider_code = "OTHER"
        with lane.sessions.begin() as session:
            session.add(
                ProviderRecord(
                    provider_id="other-provider",
                    code="OTHER",
                    name="Other",
                    provider_kind="TEST",
                )
            )
    else:
        rights = lane.repo.record(
            request_key="other-rights",
            rights_payload=lane.rights,
            reviewer_attestation=lane.attestation,
        )
    receipt = lane.repo.capture_local_json(
        request_key="different-scope",
        source_rights_admission_id=rights.source_rights_admission_id,
        source_id="source",
        provider_code=provider_code,
        evidence_reference="fixture.json",
    )
    before = counts(lane)

    def change(parent, children):
        child = children[0]
        document = strict_json_bytes(child.artifact_json.encode())
        document.update(
            provider_code=provider_code,
            fixture_source_archive_id=receipt.capture_receipt_id,
            fixture_source_archive_payload_sha256=receipt.payload_sha256,
            fixture_source_archive_created_at_utc=receipt.archive_created_at_utc,
            local_imported_at_utc=receipt.local_imported_at_utc,
            registered_at_utc=receipt.registered_at_utc,
        )
        # Use the model's canonical UTC representation, as a valid declared
        # capture must pass its own JSON equality checks before the scope guard.
        child.artifact_json = canonical_json(
            TrainingFixtureSourceV1.model_validate(document)
        )
        child.capture_receipt_id = receipt.capture_receipt_id

    with pytest.raises(
        IntegrityError, match="capture declaration or scope|child lineage"
    ):
        _direct_sql_graph(lane, change)
    assert counts(lane) == before


def test_direct_sql_valid_graph_still_seals_and_reads(lane):
    prepare(lane, count=1)
    value = _direct_sql_graph(lane, lambda parent, children: None)
    assert lane.repo.load(value.training_fact_admission_id) == value


@pytest.mark.parametrize(
    "field", ["finalized_at_utc", "observed_at_utc", "available_at_utc"]
)
def test_provider_timestamp_errors_never_render_private_raw_values(lane, field):
    prepare(lane, count=1, raw_change=("result", field, "TPRIVATE_SENTINEL_TIMESTAMP"))
    with pytest.raises(ValueError, match="invalid provider timestamp field") as error:
        admit(lane)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert "PRIVATE_SENTINEL" not in "".join(format_exception(error.value))
    assert error.value.__suppress_context__
    assert counts(lane)["match_results"] == 0


def test_candidate_validation_errors_do_not_render_untrusted_fields(lane):
    prepare(lane, count=1)
    item = lane.submissions[0]
    result = item.candidate.normalized_result.model_copy(
        update={"home_goals": "PRIVATE_SENTINEL"}
    )
    changed = item.model_copy(
        update={
            "candidate": item.candidate.model_copy(update={"normalized_result": result})
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(
            ValueError, match="invalid training evidence structure"
        ) as error:
            admit(lane, submissions=(changed,))
    assert "PRIVATE_SENTINEL" not in "".join(format_exception(error.value))
    assert counts(lane)["match_results"] == 0
