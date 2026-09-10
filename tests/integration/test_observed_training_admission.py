"""Clearly synthetic provider, terms and human-review documents in temporary DBs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_json,
    match_result_payload_sha256,
)
from football_system.domain.observed_training import (
    CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1,
    OBSERVED_SNAPSHOT_SUBJECT_V1,
    ObservedCapturePointerV1,
    ObservedCollectionScopeAdmissionV1,
    ObservedScopeExceptionV1,
    ObservedScopeSeasonV1,
    ObservedSnapshotAdmissionV1,
    ObservedSnapshotInputV1,
    ObservedSnapshotRecordV1,
    ObservedSnapshotSubmissionV1,
)
from football_system.domain.training_admission import (
    LocalReviewerAttestationContentV1,
    LocalReviewerAttestationV1,
    tagged_canonical_sha256,
)
from football_system.infrastructure.database import (
    observed_training_repository as repository_module,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    MatchResultRecord,
    ObservedResultBindingRecord,
    ProviderCompetitionMappingRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
)
from football_system.infrastructure.database.observed_training_repository import (
    SqlAlchemyObservedTrainingRepository,
)
from football_system.infrastructure.database.observed_training_schema import (
    OBSERVED_TRAINING_TABLES,
    OBSERVED_CAPTURE_ORDINALS,
    observed_training_trigger_sql_v1,
)
from football_system.infrastructure.database.session import create_database_engine
from football_system.infrastructure.files.training_evidence import strict_json_bytes

from .test_training_admission_persistence import (
    LOCAL,
    SOURCE,
    lane as admission_lane,
    review_document,
    write_evidence,
    prepare as prepare_historical,
    admit as admit_historical,
)

lane = admission_lane
UTC = timezone.utc


def seed(lane, count=2):
    with lane.sessions.begin() as session:
        session.add(
            ProviderRecord(
                provider_id="sportmonks",
                code="SPORTMONKS",
                name="Synthetic Sportmonks",
                provider_kind="TEST",
            )
        )
        session.add(
            CompetitionRecord(
                competition_id="bundesliga",
                canonical_key="synthetic-bundesliga",
                name="Bundesliga",
                country_code="DEU",
            )
        )
        for team in ("h", "a"):
            session.add(
                TeamRecord(
                    team_id=team,
                    canonical_key=team,
                    name=f"Synthetic {team}",
                    team_type="CLUB",
                )
            )
        session.flush()
        for team, raw in (("h", "444"), ("a", "555")):
            session.add(
                ProviderTeamAliasRecord(
                    alias_id=f"sm-{team}",
                    internal_team_id=team,
                    provider_id="sportmonks",
                    provider_team_id=raw,
                    provider_team_name=f"Synthetic {team}",
                    language="en",
                    team_type="CLUB",
                    available_at_utc=SOURCE,
                )
            )
        session.add(
            ProviderCompetitionMappingRecord(
                mapping_id="sm-competition",
                internal_competition_id="bundesliga",
                provider_id="sportmonks",
                provider_competition_id="222",
                provider_competition_name="Synthetic Bundesliga",
                language="en",
                season="2024/25",
                competition_type="LEAGUE",
                available_at_utc=SOURCE,
            )
        )
        for index in range(count):
            session.add(
                MatchRecord(
                    internal_match_id=f"sm-match-{index}",
                    competition_id="bundesliga",
                    home_team_id="h",
                    away_team_id="a",
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
                    internal_match_id=f"sm-match-{index}",
                    season="2024/25",
                    competition_type="LEAGUE",
                    available_at_utc=SOURCE,
                )
            )
            session.add(
                ProviderMatchMappingRecord(
                    mapping_id=f"sm-mapping-{index}",
                    provider_id="sportmonks",
                    external_namespace="fixture",
                    external_match_id=str(100 + index),
                    internal_match_id=f"sm-match-{index}",
                    resolution_method="EXPLICIT_MAPPING",
                    confidence=1,
                    available_at_utc=SOURCE,
                )
            )


def setup_observed(lane):
    lane.recorded = lane.repo.record(
        request_key="rights",
        rights_payload=lane.rights,
        reviewer_attestation=lane.attestation,
    )
    lane.observed = SqlAlchemyObservedTrainingRepository(lane.repo)
    permission = strict_json_bytes(lane.repo.evidence.read("authority.json"))
    permission["attested_schema_versions"] = [
        CURRENT_SNAPSHOT_COLLECTION_SCOPE_V1,
        OBSERVED_SNAPSHOT_SUBJECT_V1,
    ]
    lane.observed_authority = write_evidence(
        lane.root, "synthetic-observed-authority.json", permission
    )
    lane.repo.evidence.trusted_authorities[
        lane.observed_authority.evidence_reference
    ] = lane.observed_authority.evidence_sha256
    lane.serial = 0
    return lane


@pytest.fixture
def observed(lane, request):
    setup_observed(lane)
    lane.cohort_count = getattr(request, "param", 2)
    seed(lane, count=lane.cohort_count)
    return lane


def review(lane, subject, *, authority=None):
    lane.serial += 1
    at = lane.clock()
    evidence = write_evidence(
        lane.root,
        f"synthetic-review-{lane.serial}.json",
        review_document(
            schema=subject.schema_version, digest=subject.subject_hash, at=at
        ),
    )
    authority = authority or lane.observed_authority
    return LocalReviewerAttestationV1.freeze(
        content_payload=LocalReviewerAttestationContentV1(
            attested_schema_version=subject.schema_version,
            attested_payload_hash=subject.subject_hash,
            authorized_reviewer="reviewer",
            reviewer_authority_reference=authority.evidence_reference,
            authority_sha256=authority.evidence_sha256,
            reviewed_at_utc=at,
            evidence=evidence,
        )
    )


def scope_subject(lane, *, exception=False, receipts=20, records=20):
    resolution = write_evidence(
        lane.root,
        "synthetic-user-terms-resolution.json",
        dict(
            schema_version="CURRENT_SNAPSHOT_USER_TERMS_RESOLUTION_V1",
            source_rights_admission_id=lane.recorded.source_rights_admission_id,
            source_rights_admission_hash=lane.recorded.admission_hash,
            source_id="source",
            terms_sha256=lane.rights.terms_sha256,
            permitted_uses=["TRAINING", "VALIDATION"],
            retention_deadline_utc="2026-12-31T00:00:00Z",
            resolution="RESOLVED_FOR_DECLARED_SNAPSHOT_SCOPE",
        ),
    )
    return lane.observed.prepare_scope(
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        source_id="source",
        provider_competition_id="222",
        canonical_competition_id="bundesliga",
        seasons=(
            ObservedScopeSeasonV1(
                provider_season_id="333",
                canonical_season_id="2024/25",
                expected_fixture_ids=tuple(
                    str(100 + n) for n in range(lane.cohort_count)
                ),
                expected_fixture_count=lane.cohort_count,
                exceptions=(
                    ObservedScopeExceptionV1(
                        provider_fixture_key="101",
                        reason="Synthetic explicit exclusion for this software test",
                    ),
                )
                if exception
                else (),
            ),
        ),
        max_capture_receipts=receipts,
        max_snapshot_records=records,
        permitted_uses=("TRAINING", "VALIDATION"),
        retention_deadline_utc=datetime(2026, 12, 31, tzinfo=UTC),
        user_terms_resolution=resolution,
    )


def declare(lane, **kwargs):
    subject = scope_subject(lane, **kwargs)
    lane.scope = lane.observed.record_scope(
        request_key="scope", subject=subject, reviewer_attestation=review(lane, subject)
    )
    return lane.scope


def raw_fixture(index=0, goals=2, status="FT"):
    kickoff = SOURCE + timedelta(days=index + 1)
    key = 100 + index
    return {
        "timezone": "UTC",
        "synthetic_notice": "INVENTED SOFTWARE TEST ONLY",
        "data": {
            "id": key,
            "league_id": 222,
            "season_id": 333,
            "league": {
                "id": 222,
                "country": {"iso2": "DE"},
                "type": "league",
                "sub_type": "domestic",
            },
            "season": {"id": 333, "league_id": 222, "finished": True},
            "starting_at_timestamp": int(kickoff.timestamp()),
            "starting_at": kickoff.strftime("%Y-%m-%d %H:%M:%S"),
            "state_id": 5,
            "state": {"id": 5, "state": status},
            "participants": [
                {"id": 555, "meta": {"location": "away"}},
                {"id": 444, "meta": {"location": "home"}},
            ],
            "scores": [
                {
                    "id": 600 + idx,
                    "fixture_id": key,
                    "participant_id": pid,
                    "type_id": 2,
                    "description": "2ND_HALF",
                    "score": {"goals": score},
                }
                for idx, (pid, score) in enumerate(((555, 0), (444, goals)))
            ],
            "periods": [
                {
                    "id": 700,
                    "fixture_id": key,
                    "type_id": 2,
                    "ended": int((kickoff + timedelta(hours=2)).timestamp()),
                    "ticking": False,
                }
            ],
            "published_at": "1900-01-01",
            "finalized_at": "1900-01-01",
            "revision": 999,
        },
    }


def capture(lane, index=0, *, raw=None, metadata=None):
    lane.serial += 1
    name = f"synthetic-native-{lane.serial}.json"
    raw = raw_fixture(index) if raw is None else raw
    write_evidence(lane.root, name, raw)
    receipt = lane.repo.capture_local_json(
        request_key=name,
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        source_id="source",
        provider_code="SPORTMONKS",
        evidence_reference=name,
    )
    pointer = "/data/0" if isinstance(raw["data"], list) else "/data"
    ref = ObservedCapturePointerV1(
        capture_receipt_id=receipt.capture_receipt_id, record_pointer=pointer
    )
    return ObservedSnapshotInputV1(
        fixture=ref,
        season=ref,
        result=ref,
        provider_mapping_id=f"sm-mapping-{index}",
        home_team_alias_id="sm-h",
        away_team_alias_id="sm-a",
        competition_mapping_id="sm-competition",
        original_http_metadata=metadata,
    )


def reviewed(lane, inputs):
    return tuple(
        ObservedSnapshotSubmissionV1(subject=s, reviewer_attestation=review(lane, s))
        for s in lane.observed.prepare(scope_id=lane.scope.scope_id, snapshots=inputs)
    )


def admit(lane, inputs=None, *, values=None, key="admit"):
    values = reviewed(lane, inputs) if values is None else values
    return lane.observed.admit(
        request_key=key, scope_id=lane.scope.scope_id, submissions=values
    )


def counts(lane):
    with lane.engine.connect() as connection:
        return {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM {name}"))
            for name in (
                *OBSERVED_TRAINING_TABLES,
                "match_results",
                "training_capture_receipts",
                "source_rights_admissions",
            )
        }


def test_real_repository_single_capture_per_fixture_new_basis_roundtrip(observed):
    declare(observed)
    http = write_evidence(
        observed.root,
        "synthetic-original-http.json",
        {
            "received_at_utc": "2000-01-01T00:00:00Z",
            "headers": {"Date": "invented"},
            "notice": "untrusted historical metadata, not a local capture",
        },
    )
    inputs = (capture(observed, 1), capture(observed, 0, metadata=http))
    values = reviewed(observed, inputs)
    value = admit(observed, values=values)
    assert counts(observed)["training_capture_receipts"] == 2
    assert len(HistoricalDataMode) == 2
    assert observed.observed.load_admission(value.admission_id) == value
    context = observed.observed.load_context(observed.scope.scope_id)
    assert context.records == value.records
    assert (
        context.scope.subject.source_data_mode
        is HistoricalDataMode.SOURCE_TIME_RESEARCH
    )
    assert context.scope.subject.retrospective is True
    assert context.select_heads(value.registered_at_utc) == ()
    assert context.select_heads(context.actual_at_utc) == value.records
    for record in value.records:
        subject = record.subject
        assert subject.source_data_mode is HistoricalDataMode.SOURCE_TIME_RESEARCH
        assert subject.retrospective is True
        assert (
            subject.fixture_capture == subject.season_capture == subject.result_capture
        )
        assert subject.upstream_publication_at_utc is None
        assert subject.provider_finalized_at_utc is None
        assert subject.inspection.provider_version_id is None
        assert subject.inspection.provider_publication_at_utc is None
        assert subject.inspection.sporting_period_end_at_utc < LOCAL
        assert "verified_at_utc" not in type(subject).model_fields
        assert "registered_at_utc" not in type(subject).model_fields
        assert subject.capture_observed_at_utc > observed.scope.recorded_at_utc
        assert (
            record.normalized_result.observed_at_utc == subject.capture_observed_at_utc
        )
        assert (
            record.normalized_result.available_at_utc
            == record.normalized_result.ingested_at_utc
            == record.registered_at_utc
        )
        assert record.to_elo_result().home_goals == 2
        assert record.evidence_basis.value == "CURRENT_SNAPSHOT_OBSERVED"
        assert (
            SqlAlchemyHistoricalRepository(observed.sessions).find_match_result(
                record.normalized_result.match_result_id
            )
            == record.normalized_result
        )
    assert value.records[0].subject.input.original_http_metadata == http
    before = counts(observed)
    observed.clock.value = datetime(2028, 1, 1, tzinfo=UTC)
    assert admit(observed, values=values) == value
    assert observed.observed.load_admission(value.admission_id) == value
    assert counts(observed) == before
    with pytest.raises(ValueError, match="not active"):
        observed.observed.load_context(observed.scope.scope_id)
    with pytest.raises(ValueError, match="retry request"):
        admit(observed, values=reversed(values))


def test_capture_cannot_be_retroactively_upgraded_by_new_scope(observed):
    item = capture(observed)
    declare(observed, exception=True)
    with pytest.raises(ValueError, match="follow new reviewed scope"):
        reviewed(observed, (item,))
    assert counts(observed)["match_results"] == 0


def test_old_source_rights_attestation_and_old_authority_cannot_grant_scope(observed):
    subject = scope_subject(observed)
    before = counts(observed)
    with pytest.raises(ValueError, match="new genuine review"):
        observed.observed.record_scope(
            request_key="scope",
            subject=subject,
            reviewer_attestation=observed.attestation,
        )
    with pytest.raises(ValueError, match="exact payload and source scope"):
        observed.observed.record_scope(
            request_key="scope",
            subject=subject,
            reviewer_attestation=review(
                observed, subject, authority=observed.authority
            ),
        )
    assert counts(observed) == before
    assert (
        observed.repo.load_rights(observed.recorded.source_rights_admission_id)
        == observed.recorded
    )


def test_old_terms_only_document_is_not_resolved_snapshot_scope_input(observed):
    subject = scope_subject(observed)
    subject = subject.model_copy(
        update={"user_terms_resolution": observed.attestation.content_payload.evidence}
    )
    with pytest.raises(ValueError, match="user-terms-resolved"):
        observed.observed.record_scope(
            request_key="scope",
            subject=subject,
            reviewer_attestation=review(observed, subject),
        )


def test_scope_review_and_recording_have_distinct_actual_times_and_idempotency(
    observed,
):
    subject = scope_subject(observed)
    attestation = review(observed, subject)
    value = observed.observed.record_scope(
        request_key="scope", subject=subject, reviewer_attestation=attestation
    )
    assert (
        attestation.content_payload.reviewed_at_utc
        < value.recorded_at_utc
        < observed.clock.calls[-1]
    )
    assert (
        observed.observed.record_scope(
            request_key="scope", subject=subject, reviewer_attestation=attestation
        )
        == value
    )


def test_snapshot_review_schema_requires_genuine_pinned_bytes(observed):
    declare(observed, exception=True)
    subject = observed.observed.prepare(
        scope_id=observed.scope.scope_id, snapshots=(capture(observed),)
    )[0]
    forged = review(observed, subject, authority=observed.authority)
    with pytest.raises(ValueError, match="exact payload and source scope"):
        admit(
            observed,
            values=(
                ObservedSnapshotSubmissionV1(
                    subject=subject, reviewer_attestation=forged
                ),
            ),
        )
    assert counts(observed)["match_results"] == 0


@pytest.mark.parametrize(
    "status", ["AET", "PEN", "FT_PEN", "IN_PROGRESS", "CANCELLED", "AMBIGUOUS"]
)
def test_uncertain_terminal_state_is_retained_only_as_nontrainable(observed, status):
    declare(observed, exception=True)
    value = admit(observed, (capture(observed, raw=raw_fixture(status=status)),))
    record = value.records[0]
    assert record.normalized_result is None
    assert record.subject.inspection.regular_time_home_goals is None
    assert counts(observed)["match_results"] == 0
    context = observed.observed.load_context(observed.scope.scope_id)
    assert context.select_heads(context.actual_at_utc)[0] == record
    with pytest.raises(ValueError, match="not trainable"):
        record.to_elo_result()


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_join",
        "duplicate_score",
        "duplicate_fixture",
        "wrong_pointer",
        "raw_scope",
        "wrong_team",
        "wrong_mapping",
        "missing_mapping",
    ],
)
def test_bad_native_array_joins_cohort_and_registered_identity_fail_closed(
    observed, mutation
):
    declare(observed, exception=True)
    raw = raw_fixture()
    if mutation == "wrong_join":
        raw["data"]["scores"][0]["participant_id"] = 999
    if mutation == "duplicate_score":
        raw["data"]["scores"].append(raw["data"]["scores"][0].copy())
    if mutation == "duplicate_fixture":
        raw["data"] = [raw["data"], raw["data"].copy()]
    if mutation == "raw_scope":
        raw["data"]["season_id"] = 999
    item = capture(observed, raw=raw)
    if mutation == "wrong_pointer":
        item = item.model_copy(
            update={
                "result": item.result.model_copy(
                    update={"record_pointer": "/data/scores/0"}
                )
            }
        )
    if mutation == "wrong_team":
        item = item.model_copy(update={"home_team_alias_id": "sm-a"})
    if mutation in ("wrong_mapping", "missing_mapping"):
        item = item.model_copy(
            update={
                "provider_mapping_id": "sm-mapping-1"
                if mutation == "wrong_mapping"
                else "missing"
            }
        )
    before = counts(observed)
    with pytest.raises(ValueError):
        reviewed(observed, (item,))
    assert counts(observed) == before


def test_exact_cohort_duplicate_and_receipt_record_limits(observed):
    declare(observed, receipts=2, records=2)
    left, right = capture(observed), capture(observed, 1)
    with pytest.raises(ValueError, match="complete predeclared cohort"):
        reviewed(observed, (left,))
    with pytest.raises(ValueError, match="duplicate/conflicting"):
        reviewed(observed, (left, left))
    admit(observed, (left, right))
    with pytest.raises(
        ValueError, match="duplicate observed result capture|capture ordinals"
    ):
        reviewed(observed, (left,))
    with pytest.raises(ValueError, match="receipt/record limit"):
        reviewed(observed, (capture(observed, raw=raw_fixture(goals=3)),))


def test_latest_whole_captured_and_admitted_version_withdrawal_and_restoration(
    observed,
):
    declare(observed, exception=True)
    base = admit(observed, (capture(observed),))
    original_bytes = canonical_json(base)
    candidate = capture(observed, raw=raw_fixture(status="CANCELLED"))
    between = observed.clock()
    withdrawn = admit(observed, (candidate,), key="withdrawn")
    restored = admit(
        observed, (capture(observed, raw=raw_fixture(goals=3)),), key="restored"
    )
    context = observed.observed.load_context(observed.scope.scope_id)
    root = context.base_root
    assert context.select_heads(base.records[0].capture_observed_at_utc) == ()
    assert context.select_heads(between) == base.records
    assert context.select_heads(withdrawn.registered_at_utc) == base.records
    assert (
        context.select_heads(withdrawn.registered_at_utc + timedelta(microseconds=1))
        == withdrawn.records
    )
    assert context.select_heads(context.actual_at_utc) == restored.records
    assert (
        context.select_heads(context.actual_at_utc, exclude_match_ids=("sm-match-0",))
        == ()
    )
    assert context.base_root == root
    assert (
        restored.records[0].normalized_result.supersedes_match_result_id
        == base.records[0].normalized_result.match_result_id
    )
    assert (
        canonical_json(observed.observed.load_admission(base.admission_id))
        == original_bytes
    )
    assert base.admission_id not in canonical_json(restored)
    assert counts(observed)["match_results"] == 2


def test_equal_actual_clocks_use_local_sequence_not_invented_provider_order(observed):
    declare(observed, exception=True)
    fixed = observed.clock() + timedelta(seconds=10)
    observed.clock = lambda: fixed
    observed.repo._clock = observed.clock
    observed.observed._clock = observed.clock
    base = admit(observed, (capture(observed),))
    correction = admit(
        observed, (capture(observed, raw=raw_fixture(goals=3)),), key="correction"
    )
    assert base.registered_at_utc == correction.registered_at_utc
    assert (
        base.records[0].capture_observed_at_utc
        == correction.records[0].capture_observed_at_utc
    )
    assert correction.records[0].revision_sequence == 1
    assert correction.records[0].subject.inspection.provider_version_id is None
    observed.observed._clock = lambda: fixed + timedelta(seconds=1)
    context = observed.observed.load_context(observed.scope.scope_id)
    assert context.select_heads(fixed) == ()
    assert context.select_heads(context.actual_at_utc) == correction.records


def test_stale_review_cannot_fork_actual_predecessor(observed):
    declare(observed, exception=True)
    admit(observed, (capture(observed),))
    left = reviewed(observed, (capture(observed, raw=raw_fixture(goals=3)),))
    right = reviewed(observed, (capture(observed, raw=raw_fixture(goals=4)),))
    admit(observed, values=left, key="left")
    before = counts(observed)
    with pytest.raises(ValueError, match="actual predecessor"):
        admit(observed, values=right, key="right")
    assert counts(observed) == before


@pytest.mark.parametrize("withdrawn", [False, True])
def test_old_bare_writer_cannot_write_observed_successors(observed, withdrawn):
    declare(observed, exception=True)
    base = admit(observed, (capture(observed),))
    if withdrawn:
        admit(
            observed,
            (capture(observed, raw=raw_fixture(status="CANCELLED")),),
            key="withdrawal",
        )
    old = base.records[0].normalized_result
    result = old.model_copy(
        update={
            "match_result_id": "bare",
            "source_result_key": "bare",
            "supersedes_match_result_id": old.match_result_id,
            "ingested_at_utc": observed.clock(),
            "available_at_utc": observed.clock.value,
        }
    )
    with pytest.raises(IntegrityError, match="typed controlled normalization"):
        SqlAlchemyHistoricalRepository(observed.sessions).append_match_result(result)
    assert observed.observed.load_admission(base.admission_id) == base


@pytest.mark.parametrize("table", OBSERVED_TRAINING_TABLES)
@pytest.mark.parametrize("action", ["UPDATE", "DELETE", "REPLACE"])
def test_all_observed_rows_are_append_only(observed, table, action):
    declare(observed, exception=True)
    admit(observed, (capture(observed),))
    sql = {
        "UPDATE": f"UPDATE {table} SET row_sha256 = row_sha256",
        "DELETE": f"DELETE FROM {table}",
        "REPLACE": f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
    }[action]
    with pytest.raises(IntegrityError):
        with observed.engine.begin() as connection:
            connection.exec_driver_sql(sql)


@pytest.mark.parametrize("table", OBSERVED_TRAINING_TABLES)
def test_every_persisted_child_is_rehashed_on_load(observed, table):
    declare(observed, exception=True)
    value = admit(observed, (capture(observed),))
    with observed.engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER trg_{table}_append_only_update")
        connection.exec_driver_sql(f"UPDATE {table} SET artifact_json = '{{}}'")
    with pytest.raises(ValueError, match="integrity"):
        observed.observed.load_admission(value.admission_id)


@pytest.mark.parametrize(
    "target", ["raw", "review", "terms_resolution", "http", "normalized"]
)
def test_actual_bytes_and_normalized_counterpart_reverified(observed, target):
    declare(observed, exception=True)
    http = write_evidence(
        observed.root, "synthetic-http.json", {"untrusted": "historical metadata"}
    )
    item = capture(observed, metadata=http)
    values = reviewed(observed, (item,))
    value = admit(observed, values=values)
    refs = dict(
        raw=observed.repo.load_capture(item.result.capture_receipt_id)[
            0
        ].evidence_reference,
        review=values[
            0
        ].reviewer_attestation.content_payload.evidence.evidence_reference,
        terms_resolution=observed.scope.subject.user_terms_resolution.evidence_reference,
        http=http.evidence_reference,
    )
    if target == "normalized":
        with observed.engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER trg_match_results_append_only_update"
            )
            connection.exec_driver_sql(
                "UPDATE match_results SET source_result_key = 'tampered'"
            )
    else:
        write_evidence(observed.root, refs[target], {"synthetic_corruption": True})
    with pytest.raises(ValueError, match="SHA-256|normalized result"):
        observed.observed.load_admission(value.admission_id)


def test_late_failure_rolls_back_typed_binding_normalized_result_and_parent(
    observed, monkeypatch
):
    declare(observed)
    values = reviewed(observed, (capture(observed), capture(observed, 1)))
    before = counts(observed)
    original = repository_module._snapshot_row

    def broken(*args):
        row = original(*args)
        if row.fixture_key == "101":
            row.provider_mapping_id = "missing"
        return row

    monkeypatch.setattr(repository_module, "_snapshot_row", broken)
    with pytest.raises(IntegrityError):
        admit(observed, values=values)
    assert counts(observed) == before


@pytest.mark.parametrize(
    "stage", ["before_bytes", "write", "readback", "last_authority_io"]
)
def test_expired_rights_checked_before_raw_and_after_last_io(
    observed, monkeypatch, stage
):
    declare(observed, exception=True)
    values = reviewed(observed, (capture(observed),))
    before = counts(observed)
    expiry = observed.rights.expires_at_utc

    def expire():
        observed.clock.value = expiry

    if stage == "before_bytes":
        expire()
        monkeypatch.setattr(
            observed.repo,
            "_capture",
            lambda *args: pytest.fail("expired rights reached provider bytes"),
        )
    elif stage == "readback":
        original = observed.observed._load

        def after_load(*args):
            value = original(*args)
            expire()
            return value

        monkeypatch.setattr(observed.observed, "_load", after_load)
    elif stage == "last_authority_io":
        original = observed.observed._finish

        def during_finish(*args, **kwargs):
            original_authority = observed.repo.evidence.load_authority

            def after_authority(*args):
                value = original_authority(*args)
                expire()
                return value

            monkeypatch.setattr(
                observed.repo.evidence, "load_authority", after_authority
            )
            return original(*args, **kwargs)

        monkeypatch.setattr(observed.observed, "_finish", during_finish)
    else:

        def after_statement(
            connection, cursor, statement, parameters, context, executemany
        ):
            if statement.startswith("INSERT INTO observed_snapshot_records "):
                expire()

        event.listen(observed.engine, "after_cursor_execute", after_statement)
    try:
        with pytest.raises(ValueError, match="not active"):
            admit(observed, values=values)
    finally:
        if stage == "write":
            event.remove(observed.engine, "after_cursor_execute", after_statement)
    assert counts(observed) == before


def test_deferred_result_binding_rejects_actual_orphan_at_commit(observed):
    declare(observed, exception=True)
    base = admit(observed, (capture(observed),))
    with observed.sessions() as session:
        row = session.scalar(select(ObservedResultBindingRecord))
        values = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    values.update(
        version_id="orphan-version",
        match_result_id="orphan-result",
        supersedes_match_result_id=base.records[0].normalized_result.match_result_id,
    )
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with observed.sessions.begin() as session:
            session.add(ObservedResultBindingRecord(**values))
            session.flush()
    with observed.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    assert counts(observed)["observed_result_bindings"] == 1


def test_concurrent_exact_retry_is_one_atomic_admission(observed):
    declare(observed, exception=True)
    values = reviewed(observed, (capture(observed),))
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        return admit(observed, values=values)

    with ThreadPoolExecutor(max_workers=2) as executor:
        left, right = executor.submit(submit), executor.submit(submit)
        assert left.result(timeout=30) == right.result(timeout=30)
    assert counts(observed)["observed_snapshot_admissions"] == 1


def test_additive_migration_preserves_original_results_and_indexes(lane):
    prepare_historical(lane, count=1)
    original = admit_historical(lane)
    assert lane.repo.load(original.training_fact_admission_id) == original
    # Downgrade only empty observed tables in this populated synthetic database.
    url = str(lane.engine.url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.stamp(config, "head")
    command.downgrade(config, "f51e294b0687")
    engine = create_database_engine(url)
    with engine.connect() as connection:
        old_results = connection.execute(
            text("SELECT * FROM match_results ORDER BY match_result_id")
        ).all()
        assert len(old_results) == 1
        before = connection.execute(
            text(
                "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='match_results' AND type IN ('table','index') ORDER BY name"
            )
        ).all()
    engine.dispose()
    command.upgrade(config, "062f3a5c1798")
    engine = create_database_engine(url)
    assert set(OBSERVED_TRAINING_TABLES) <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(
            text(f"SELECT COUNT(*) FROM {OBSERVED_CAPTURE_ORDINALS}")
        ) == connection.scalar(text("SELECT COUNT(*) FROM training_capture_receipts"))
        assert (
            connection.execute(
                text("SELECT * FROM match_results ORDER BY match_result_id")
            ).all()
            == old_results
        )
        assert (
            connection.execute(
                text(
                    "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='match_results' AND type IN ('table','index') ORDER BY name"
                )
            ).all()
            == before
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    assert lane.repo.load(original.training_fact_admission_id) == original
    engine.dispose()
    command.downgrade(config, "f51e294b0687")
    engine = create_database_engine(url)
    assert not set(OBSERVED_TRAINING_TABLES) & set(inspect(engine).get_table_names())
    engine.dispose()


def test_no_conversion_of_preexisting_unqualified_normalized_results(observed):
    declare(observed, exception=True)
    values = reviewed(observed, (capture(observed),))
    from football_system.domain.settlement import MatchResult

    result = MatchResult(
        match_result_id="legacy",
        match_id="sm-match-0",
        provider_code="SPORTMONKS",
        home_goals=2,
        away_goals=0,
        observed_at_utc=SOURCE + timedelta(days=2),
        available_at_utc=SOURCE + timedelta(days=2),
        ingested_at_utc=SOURCE + timedelta(days=2),
        source_result_key="legacy",
        payload_hash=match_result_payload_sha256(2, 0),
    )
    SqlAlchemyHistoricalRepository(observed.sessions).append_match_result(result)
    with pytest.raises(ValueError, match="cross-basis or unqualified"):
        admit(observed, values=values)
    assert (
        SqlAlchemyHistoricalRepository(observed.sessions).find_match_result("legacy")
        == result
    )


def test_same_capture_native_bytes_fail_existing_source_time_extraction(observed):
    from football_system.infrastructure.files.training_evidence import (
        CapturedRecordReferenceV1,
        ScopeFieldPathsV1,
        provider_record_sha256,
        verified_provider_fields,
    )

    declare(observed, exception=True)
    item = capture(observed)
    value = admit(observed, (item,))
    _, payload = observed.repo.load_capture(item.result.capture_receipt_id)
    raw = strict_json_bytes(payload)["data"]
    # The exact same native receipt does not acquire historical publication
    # evidence simply because its sporting-period end is present.
    with pytest.raises(ValueError, match="missing provider field"):
        verified_provider_fields(
            payload,
            CapturedRecordReferenceV1(**item.result.model_dump()),
            ScopeFieldPathsV1(
                fixture_key="/id",
                competition_id="/league_id",
                season_id="/season_id",
                available_at_utc="/provider_publication_at_utc",
            ),
            digest=provider_record_sha256(raw),
        )
    assert observed.observed.load_admission(value.admission_id) == value


def test_tied_clocks_cannot_retroactively_apply_scope_to_prior_capture(observed):
    fixed = observed.clock() + timedelta(seconds=20)
    observed.clock = lambda: fixed
    observed.repo._clock = observed.clock
    observed.observed._clock = observed.clock
    item = capture(observed)
    declare(observed, exception=True)
    receipt, _ = observed.repo.load_capture(item.result.capture_receipt_id)
    assert receipt.local_imported_at_utc == observed.scope.recorded_at_utc
    with pytest.raises(ValueError, match="tied clocks"):
        reviewed(observed, (item,))
    assert admit(observed, (capture(observed),)).records


def test_writer_rereads_bytes_after_prepare_and_genuine_review(observed):
    declare(observed, exception=True)
    item = capture(observed)
    values = reviewed(observed, (item,))
    receipt, _ = observed.repo.load_capture(item.result.capture_receipt_id)
    before = counts(observed)
    write_evidence(observed.root, receipt.evidence_reference, raw_fixture(goals=99))
    with pytest.raises(ValueError, match="SHA-256"):
        admit(observed, values=values)
    assert counts(observed) == before


@pytest.mark.parametrize(
    "clock_kind", ["naive", "non_utc", "backwards", "retention_expired"]
)
def test_actual_clocks_and_scope_retention_fail_closed(observed, clock_kind):
    declare(observed, exception=True)
    values = reviewed(observed, (capture(observed),))
    before = counts(observed)
    current = observed.clock()
    if clock_kind == "backwards":
        ticks = iter((current, current - timedelta(seconds=1), current))
        observed.observed._clock = lambda: next(ticks)
    else:
        fixed = {
            "naive": current.replace(tzinfo=None),
            "non_utc": current.astimezone(timezone(timedelta(hours=1))),
            "retention_expired": observed.scope.subject.retention_deadline_utc,
        }[clock_kind]
        observed.observed._clock = lambda: fixed
    with pytest.raises(ValueError, match="aware UTC|backwards|retention"):
        admit(observed, values=values)
    assert counts(observed) == before


def test_missing_child_detected_and_populated_downgrade_refuses_before_ddl(observed):
    from .test_database_schema import _schema_signature, _trigger_signature

    declare(observed, exception=True)
    value = admit(observed, (capture(observed, raw=raw_fixture(status="CANCELLED")),))
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(observed.engine.url))
    command.stamp(config, "head")
    command.downgrade(config, "062f3a5c1798")
    before = _schema_signature(observed.engine), _trigger_signature(observed.engine)
    with pytest.raises(RuntimeError, match="immutable observed lineage"):
        command.downgrade(config, "f51e294b0687")
    assert (
        _schema_signature(observed.engine),
        _trigger_signature(observed.engine),
    ) == before
    with observed.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_observed_snapshot_records_append_only_delete"
        )
        connection.exec_driver_sql("DELETE FROM observed_snapshot_records")
    with pytest.raises(ValueError, match="child count"):
        observed.observed.load_admission(value.admission_id)


def test_historical_basis_guard_and_existing_admission_remain_unchanged(lane):
    from football_system.domain.observed_training import ObservedStreamV1

    prepare_historical(lane, count=1)
    original = admit_historical(lane)
    repo = SqlAlchemyObservedTrainingRepository(lane.repo)
    stream = ObservedStreamV1(
        source_id="source",
        provider_code="PROVIDER",
        provider_fixture_namespace="fixture",
        provider_fixture_key="fixture-0",
        internal_match_id="match-0",
    )
    with lane.sessions.begin() as session:
        with pytest.raises(ValueError, match="cross-basis historical stream"):
            repo._cross_basis(session, stream, "provider", "not-a-conversion")
    assert lane.repo.load(original.training_fact_admission_id) == original


def test_observed_stream_cannot_gain_a_v1_historical_binding(observed):
    from football_system.infrastructure.database.models import TrainingFactBindingRecord

    declare(observed, exception=True)
    value = admit(observed, (capture(observed),))
    with pytest.raises(IntegrityError, match="cannot automatically cross"):
        with observed.sessions.begin() as session:
            session.add(
                TrainingFactBindingRecord(
                    training_fact_admission_id="unapproved-cross-basis",
                    internal_match_id="sm-match-0",
                    provider_mapping_id="sm-mapping-0",
                    sequence=0,
                    training_fact_binding_id="unapproved-binding",
                    fixture_source_id="fixture",
                    season_membership_id="season",
                    match_result_admission_id="result",
                    match_result_id=value.records[0].normalized_result.match_result_id,
                    artifact_json="{}",
                    row_sha256="a" * 64,
                )
            )
    assert observed.observed.load_admission(value.admission_id) == value


def test_new_fixture_capture_cannot_mask_an_older_result_observation(observed):
    declare(observed, exception=True)
    older = capture(observed, raw=raw_fixture(goals=1))
    base = admit(observed, (capture(observed),))
    later = capture(observed, raw=raw_fixture(goals=3))
    mixed = later.model_copy(update={"result": older.result})
    with pytest.raises(ValueError, match="local chronology|capture ordinals"):
        reviewed(observed, (mixed,))
    assert observed.observed.load_admission(base.admission_id) == base


def batch_inputs(lane, *, goals=2):
    data = [
        raw_fixture(index, goals=goals)["data"]
        for index in reversed(range(lane.cohort_count))
    ]
    first = capture(
        lane,
        raw={
            "timezone": "UTC",
            "data": data,
            "synthetic_notice": "INVENTED BATCH; NO HTTP SENDS",
        },
    )
    inputs = []
    for position, row in enumerate(data):
        ref = first.result.model_copy(update={"record_pointer": f"/data/{position}"})
        inputs.append(
            first.model_copy(
                update={
                    "provider_mapping_id": f"sm-mapping-{row['id'] - 100}",
                    "fixture": ref,
                    "season": ref,
                    "result": ref,
                }
            )
        )
    return tuple(inputs)


@pytest.mark.parametrize("observed", [2, 50], indirect=True)
def test_whole_batch_shares_one_original_receipt_and_rooted_evidence(
    observed, monkeypatch
):
    declare(observed, receipts=1, records=observed.cohort_count)
    inputs = batch_inputs(observed)
    receipt, payload = observed.repo.load_capture(inputs[0].result.capture_receipt_id)
    original = repository_module.inspect_observed_fixture
    calls = []

    def inspect_original(actual_payload, **kwargs):
        assert actual_payload == payload
        calls.append(kwargs["record_pointer"])
        return original(actual_payload, **kwargs)

    monkeypatch.setattr(repository_module, "inspect_observed_fixture", inspect_original)
    value = admit(observed, inputs)
    assert len(value.records) == observed.cohort_count
    assert counts(observed)["training_capture_receipts"] == 1
    assert counts(observed)["match_results"] == observed.cohort_count
    assert (
        len({r.subject.result_capture.record_sha256 for r in value.records})
        == observed.cohort_count
    )
    pointers = {item.result.record_pointer for item in inputs}
    assert set(calls) == pointers
    for record in value.records:
        s = record.subject
        assert s.fixture_capture == s.season_capture == s.result_capture
        assert s.result_capture.capture_record_count == observed.cohort_count
        assert s.result_capture.payload_sha256 == receipt.payload_sha256
        assert s.result_capture.capture_observed_at_utc == receipt.local_imported_at_utc
        assert (
            s.inspection.field_evidence["raw_payload_sha256"] == receipt.payload_sha256
        )
        pointer = s.result_capture.record_pointer
        assert (
            s.inspection.field_evidence["provider_fixture_key"]["pointer"]
            == pointer + "/id"
        )
        assert (
            s.inspection.field_evidence["kickoff_at_utc"]["timezone"]["pointer"]
            == "/timezone"
        )
        assert all(
            row["pointer"].startswith(pointer + "/scores/")
            for row in s.inspection.field_evidence["scores"]["rows"]
        )
    assert observed.observed.load_admission(value.admission_id) == value


def test_receipt_and_snapshot_version_limits_are_different_units(observed):
    declare(observed, receipts=2, records=4)
    base = admit(observed, batch_inputs(observed))
    newer = admit(observed, batch_inputs(observed, goals=3), key="newer")
    assert counts(observed)["training_capture_receipts"] == 2
    assert counts(observed)["observed_snapshot_records"] == 4
    assert all(r.revision_sequence == 1 for r in newer.records)
    context = observed.observed.load_context(observed.scope.scope_id)
    assert context.select_heads(context.actual_at_utc) == newer.records
    assert observed.observed.load_admission(base.admission_id) == base
    with pytest.raises(ValueError, match="receipt/record limit"):
        reviewed(observed, batch_inputs(observed, goals=4))


@pytest.mark.parametrize("receipts,records", [(1, 4), (2, 3)])
def test_receipt_and_record_caps_are_independently_enforced(
    observed, receipts, records
):
    declare(observed, receipts=receipts, records=records)
    admit(observed, batch_inputs(observed))
    next_batch = batch_inputs(observed, goals=3)
    before = counts(observed)
    with pytest.raises(ValueError, match="receipt/record limit"):
        reviewed(observed, next_batch)
    assert counts(observed) == before
    assert before["observed_snapshot_records"] == 2


@pytest.mark.parametrize("size", [0, 51])
def test_original_capture_cardinality_cannot_be_hidden_by_a_selector(observed, size):
    declare(observed, exception=True)
    item = capture(
        observed,
        raw={
            "timezone": "UTC",
            "data": [raw_fixture(index)["data"] for index in range(size)],
        },
    )
    with pytest.raises(ValueError, match="1 to 50 original data records"):
        reviewed(observed, (item,))


@pytest.mark.parametrize(
    "contradiction", ["kickoff", "registered_team", "unresolved_team"]
)
def test_reviewed_identity_contradiction_withdraws_without_overwriting_source(
    observed, contradiction
):
    declare(observed, exception=True)
    base = admit(observed, (capture(observed),))
    before_json = canonical_json(base)
    raw = raw_fixture()
    if contradiction == "kickoff":
        kickoff = SOURCE + timedelta(days=1, minutes=5)
        raw["data"].update(
            starting_at_timestamp=int(kickoff.timestamp()),
            starting_at=kickoff.strftime("%Y-%m-%d %H:%M:%S"),
        )
    else:
        for participant in raw["data"]["participants"]:
            if participant["id"] == 444:
                participant["id"] = 666
        for score in raw["data"]["scores"]:
            if score["participant_id"] == 444:
                score["participant_id"] = 666
        if contradiction == "registered_team":
            with observed.sessions.begin() as session:
                session.add(
                    TeamRecord(
                        team_id="other",
                        canonical_key="synthetic-other",
                        name="Synthetic Other",
                        team_type="CLUB",
                    )
                )
                session.flush()
                session.add(
                    ProviderTeamAliasRecord(
                        alias_id="sm-other",
                        internal_team_id="other",
                        provider_id="sportmonks",
                        provider_team_id="666",
                        provider_team_name="Synthetic Other",
                        language="en",
                        team_type="CLUB",
                        available_at_utc=observed.clock(),
                    )
                )
    item = capture(observed, raw=raw)
    if contradiction == "registered_team":
        item = item.model_copy(update={"home_team_alias_id": "sm-other"})
    values = reviewed(observed, (item,))
    subject = values[0].subject
    assert subject.inspection.trainable is True  # Preserve the native finding.
    assert subject.trainable is False
    assert subject.identity_contradictions
    claimed = (
        subject.model_copy(
            update={
                "inspection": subject.inspection.model_copy(
                    update={"kickoff_at_utc": subject.identity.kickoff_at_utc}
                )
            }
        )
        if contradiction == "kickoff"
        else subject.model_copy(update={"resolved_home_team_id": "h"})
    )
    assert claimed.trainable
    false_review = ObservedSnapshotSubmissionV1(
        subject=claimed, reviewer_attestation=review(observed, claimed)
    )
    with pytest.raises(ValueError, match="reread bytes"):
        admit(observed, values=(false_review,), key="false-identity-qualification")
    withdrawn = admit(observed, values=values, key="identity-withdrawal")
    record = withdrawn.records[0]
    assert record.identity == base.records[0].identity
    assert record.normalized_result is None and record.trainable is False
    assert record.subject.inspection.regular_time_home_goals == 2
    if contradiction != "kickoff":
        assert record.subject.inspection.provider_home_team_id == "666"
        assert record.subject.resolved_home_team_id == (
            "other" if contradiction == "registered_team" else None
        )
    assert counts(observed)["match_results"] == 1
    context = observed.observed.load_context(observed.scope.scope_id)
    assert context.select_heads(withdrawn.registered_at_utc) == base.records
    assert context.select_heads(context.actual_at_utc) == withdrawn.records
    with pytest.raises(ValueError, match="not trainable"):
        record.to_elo_result()
    assert (
        canonical_json(observed.observed.load_admission(base.admission_id))
        == before_json
    )
    restored = admit(observed, (capture(observed),), key="restored-source")
    assert restored.records[0].trainable
    assert (
        restored.records[0].normalized_result.supersedes_match_result_id
        == base.records[0].normalized_result.match_result_id
    )


def test_actual_canonical_pipeline_domestic_league_smoke(lane):
    from football_system.application.identity_catalog import (
        MatchIdentityRegistration,
        RegisteredCanonicalMatch,
        RegisteredCompetitionMapping,
        RegisteredTeamAlias,
    )
    from football_system.domain.identity import (
        Alias,
        CanonicalMatchIdentity,
        CompetitionMapping,
    )
    from football_system.domain.match import (
        Competition,
        Match,
        ProviderMatchMapping,
        Team,
    )
    from football_system.infrastructure.database.identity_repositories import (
        SqlAlchemyMatchIdentityRepository,
    )

    setup_observed(lane)
    lane.cohort_count = 2
    kickoff = SOURCE + timedelta(days=1)
    identity = CanonicalMatchIdentity(
        internal_match_id="sm-match-0",
        internal_competition_id="bundesliga",
        internal_home_team_id="h",
        internal_away_team_id="a",
        season="2024/25",
        competition_type="DOMESTIC_LEAGUE",
        kickoff_at_utc=kickoff,
    )
    catalog = SqlAlchemyMatchIdentityRepository(lane.sessions, clock=lane.clock)
    catalog.register(
        MatchIdentityRegistration(
            created_at_utc=LOCAL,
            competitions=(
                Competition(
                    competition_id="bundesliga",
                    canonical_key="synthetic-bundesliga",
                    name="Bundesliga",
                    country_code="DE",
                ),
            ),
            teams=tuple(
                Team(team_id=key, canonical_key=key, name=f"Synthetic {key}")
                for key in ("h", "a")
            ),
            matches=(
                Match(
                    match_id="sm-match-0",
                    competition_id="bundesliga",
                    home_team_id="h",
                    away_team_id="a",
                    kickoff_at_utc=kickoff,
                    available_at_utc=SOURCE,
                ),
            ),
            team_aliases=tuple(
                RegisteredTeamAlias(
                    internal_team_id=team,
                    alias=Alias(
                        provider_code="SPORTMONKS",
                        provider_team_id=raw,
                        provider_team_name=f"Synthetic {team}",
                        language="en",
                    ),
                    available_at_utc=SOURCE,
                )
                for team, raw in (("h", "444"), ("a", "555"))
            ),
            competition_mappings=(
                RegisteredCompetitionMapping(
                    mapping=CompetitionMapping(
                        provider_code="SPORTMONKS",
                        provider_competition_id="222",
                        provider_competition_name="Bundesliga",
                        language="en",
                        season="2024/25",
                        competition_type="DOMESTIC_LEAGUE",
                        internal_competition_id="bundesliga",
                    ),
                    available_at_utc=SOURCE,
                ),
            ),
            canonical_matches=(
                RegisteredCanonicalMatch(identity=identity, available_at_utc=SOURCE),
            ),
            explicit_mappings=(
                ProviderMatchMapping(
                    mapping_id="sm-mapping-0",
                    provider_code="SPORTMONKS",
                    external_namespace="fixture",
                    external_match_id="100",
                    internal_match_id="sm-match-0",
                    resolution_method="EXPLICIT_MAPPING",
                    confidence=1,
                    available_at_utc=SOURCE,
                ),
            ),
        )
    )
    declare(lane, exception=True)
    with lane.sessions() as session:
        aliases = {
            row.provider_team_id: row.alias_id
            for row in session.scalars(select(ProviderTeamAliasRecord))
        }
        competition = session.scalar(select(ProviderCompetitionMappingRecord))
        assert (
            session.get(CanonicalMatchIdentityRecord, "sm-match-0").competition_type
            == "DOMESTIC_LEAGUE"
        )
    item = capture(lane).model_copy(
        update={
            "home_team_alias_id": aliases["444"],
            "away_team_alias_id": aliases["555"],
            "competition_mapping_id": competition.mapping_id,
        }
    )
    result = admit(lane, (item,))
    assert result.records[0].identity.competition_type == "DOMESTIC_LEAGUE"
    assert result.records[0].trainable
    assert result.records[0].to_elo_result().home_goals == 2
    assert lane.observed.load_admission(result.admission_id) == result


def test_new_ambiguous_alias_evidence_registers_only_a_reviewed_withdrawal(observed):
    declare(observed, exception=True)
    original = admit(observed, (capture(observed),))
    with observed.sessions.begin() as session:
        session.add(
            TeamRecord(
                team_id="other",
                canonical_key="synthetic-other",
                name="Synthetic Other",
                team_type="CLUB",
            )
        )
        session.flush()
        session.add(
            ProviderTeamAliasRecord(
                alias_id="ambiguous-home",
                internal_team_id="other",
                provider_id="sportmonks",
                provider_team_id="444",
                provider_team_name="Synthetic Home",
                language="en",
                team_type="CLUB",
                available_at_utc=observed.clock(),
            )
        )
    values = reviewed(observed, (capture(observed),))
    assert values[0].subject.resolved_home_team_id is None
    assert values[0].subject.inspection.trainable
    withdrawn = admit(observed, values=values, key="ambiguous-identity")
    assert withdrawn.records[0].normalized_result is None
    context = observed.observed.load_context(observed.scope.scope_id)
    assert context.select_heads(context.actual_at_utc) == withdrawn.records
    assert observed.observed.load_admission(original.admission_id) == original


@pytest.mark.parametrize("operation", ["prepare", "admit"])
def test_foreign_expired_grant_rejected_before_any_payload_sql_or_file_read(
    observed, monkeypatch, operation
):
    from football_system.application.training_admission import (
        SealSourceRightsReviewService,
    )

    declare(observed, exception=True)
    base = admit(observed, (capture(observed),))
    expiry = LOCAL + timedelta(hours=1)
    rights_b = type(observed.rights).model_validate(
        {
            **observed.rights.model_dump(mode="python"),
            "expires_at_utc": expiry,
            "terms_version": "synthetic-distinct-grant-B",
        }
    )
    reviewed_at = observed.clock()
    evidence = write_evidence(
        observed.root,
        "synthetic-rights-B-review.json",
        review_document(
            schema="SOURCE_RIGHTS_PAYLOAD_V1",
            digest=tagged_canonical_sha256("SOURCE_RIGHTS_PAYLOAD_V1", rights_b),
            at=reviewed_at,
        ),
    )
    attestation = SealSourceRightsReviewService().seal(
        rights_payload=rights_b,
        authorized_reviewer="reviewer",
        reviewer_authority_reference=observed.authority.evidence_reference,
        authority_sha256=observed.authority.evidence_sha256,
        reviewed_at_utc=reviewed_at,
        evidence=evidence,
    )
    grant_b = observed.repo.record(
        request_key="synthetic-rights-B",
        rights_payload=rights_b,
        reviewer_attestation=attestation,
    )
    filename = "synthetic-B-native.json"
    raw = {**raw_fixture(), "synthetic_notice": "B GRANT ONLY; INVENTED TEST BYTES"}
    write_evidence(observed.root, filename, raw)
    receipt = observed.repo.capture_local_json(
        request_key="synthetic-B-capture",
        source_rights_admission_id=grant_b.source_rights_admission_id,
        source_id="source",
        provider_code="SPORTMONKS",
        evidence_reference=filename,
    )
    observed.clock.value = expiry
    with observed.engine.connect() as connection:
        ordinal = connection.scalar(
            text(
                "SELECT capture_ordinal FROM observed_capture_ordinals WHERE capture_receipt_id=:id"
            ),
            {"id": receipt.capture_receipt_id},
        )
    original = base.records[0]
    ref = original.subject.input.result.model_copy(
        update={"capture_receipt_id": receipt.capture_receipt_id}
    )
    foreign_input = original.subject.input.model_copy(
        update={role: ref for role in ("fixture", "season", "result")}
    )
    foreign_capture = original.subject.result_capture.model_copy(
        update={
            "capture_receipt_id": receipt.capture_receipt_id,
            "receipt_hash": receipt.receipt_hash,
            "payload_sha256": receipt.payload_sha256,
            "capture_ordinal": ordinal,
            "capture_observed_at_utc": receipt.local_imported_at_utc,
            "capture_registered_at_utc": receipt.registered_at_utc,
        }
    )
    subject = original.subject.model_copy(
        update={
            "input": foreign_input,
            **{
                f"{role}_capture": foreign_capture
                for role in ("fixture", "season", "result")
            },
            "revision_sequence": 1,
            "predecessor_id": original.version_id,
            "predecessor_hash": original.content_hash,
            "previous_match_result_id": original.normalized_result.match_result_id,
        }
    )
    submission = ObservedSnapshotSubmissionV1(
        subject=subject, reviewer_attestation=review(observed, subject)
    )
    payload_sql, provider_files = [], []
    read = observed.repo.evidence.read

    def spy_sql(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "payload_bytes" in statement
        ):
            payload_sql.append(statement)

    def spy_read(reference, *args, **kwargs):
        if reference == filename or reference.startswith("synthetic-native-"):
            provider_files.append(reference)
        return read(reference, *args, **kwargs)

    monkeypatch.setattr(observed.repo.evidence, "read", spy_read)
    event.listen(observed.engine, "before_cursor_execute", spy_sql)
    try:
        with pytest.raises(
            ValueError, match="metadata is outside source/provider/rights scope"
        ):
            if operation == "prepare":
                observed.observed.prepare(
                    scope_id=observed.scope.scope_id, snapshots=(foreign_input,)
                )
            else:
                admit(observed, values=(submission,), key="foreign-B-under-A")
    finally:
        event.remove(observed.engine, "before_cursor_execute", spy_sql)
    assert payload_sql == []
    assert provider_files == []


def test_current_grant_rechecked_after_metadata_before_blob_read(observed, monkeypatch):
    declare(observed, exception=True)
    item = capture(observed)
    original = observed.observed._capture_metadata

    def expire_after_metadata(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.clock.value = observed.rights.expires_at_utc
        return result

    monkeypatch.setattr(observed.observed, "_capture_metadata", expire_after_metadata)
    monkeypatch.setattr(
        observed.repo,
        "_capture",
        lambda *args: pytest.fail("expired current grant reached a payload read"),
    )
    with pytest.raises(ValueError, match="not active"):
        reviewed(observed, (item,))


def test_tied_clock_older_ft_capture_cannot_restore_newer_withdrawal(observed):
    declare(observed, exception=True)
    fixed = observed.clock() + timedelta(seconds=10)
    observed.clock = lambda: fixed
    observed.repo._clock = observed.clock
    observed.observed._clock = observed.clock
    old = capture(observed)
    old_subject = observed.observed.prepare(
        scope_id=observed.scope.scope_id, snapshots=(old,)
    )[0]
    cancelled = admit(
        observed, (capture(observed, raw=raw_fixture(status="CANCELLED")),)
    )
    head = cancelled.records[0]
    assert old_subject.capture_observed_at_utc == head.capture_observed_at_utc
    assert (
        old_subject.result_capture.capture_ordinal
        < head.subject.result_capture.capture_ordinal
    )
    with pytest.raises(ValueError, match="advancing actual local capture ordinals"):
        reviewed(observed, (old,))
    forged = old_subject.model_copy(
        update={
            "revision_sequence": 1,
            "predecessor_id": head.version_id,
            "predecessor_hash": head.content_hash,
            **{
                f"{role}_capture": getattr(old_subject, f"{role}_capture").model_copy(
                    update={
                        "capture_ordinal": head.subject.result_capture.capture_ordinal
                        + 1
                    }
                )
                for role in ("fixture", "season", "result")
            },
        }
    )
    values = (
        ObservedSnapshotSubmissionV1(
            subject=forged, reviewer_attestation=review(observed, forged)
        ),
    )
    with pytest.raises(ValueError, match="capture ordinals"):
        admit(observed, values=values, key="forged-ordinal")
    fresh = admit(observed, (capture(observed),), key="fresh-restoration")
    assert fresh.records[0].normalized_result is not None
    assert (
        fresh.records[0].subject.result_capture.capture_ordinal
        > head.subject.result_capture.capture_ordinal
    )


def test_capture_ordinals_are_immutable_db_assigned_and_vacuum_stable(observed):
    from football_system.infrastructure.database.session import create_schema

    declare(observed, exception=True)
    value = admit(observed, (capture(observed),))
    with observed.engine.connect() as connection:
        before = connection.execute(
            text("SELECT * FROM observed_capture_ordinals ORDER BY capture_ordinal")
        ).all()
    for sql in (
        "UPDATE observed_capture_ordinals SET capture_ordinal = capture_ordinal + 100",
        "DELETE FROM observed_capture_ordinals",
        "INSERT OR REPLACE INTO observed_capture_ordinals SELECT * FROM observed_capture_ordinals",
    ):
        with pytest.raises(IntegrityError, match="ordinal"):
            with observed.engine.begin() as connection:
                connection.exec_driver_sql(sql)
    with observed.engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.exec_driver_sql("VACUUM")
    create_schema(observed.engine)
    with observed.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT * FROM observed_capture_ordinals ORDER BY capture_ordinal")
            ).all()
            == before
        )
    assert observed.observed.load_admission(value.admission_id) == value
    with observed.engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_observed_capture_ordinals_append_only_update"
        )
        connection.exec_driver_sql(
            "UPDATE observed_capture_ordinals SET capture_ordinal = capture_ordinal + 100"
        )
    with pytest.raises(ValueError, match="stored observed snapshot differs"):
        observed.observed.load_admission(value.admission_id)


@pytest.mark.parametrize("new_status,new_goals", [("CANCELLED", 2), ("FT", 3)])
def test_mixed_full_fixture_roles_cannot_discard_newer_outcomes(
    observed, new_status, new_goals
):
    declare(observed, exception=True)
    old = capture(observed)
    newer = capture(observed, raw=raw_fixture(status=new_status, goals=new_goals))
    mixed = newer.model_copy(update={"result": old.result})
    before = counts(observed)
    with pytest.raises(ValueError, match="contradictory outcomes"):
        reviewed(observed, (mixed,))
    assert counts(observed) == before


def test_coherent_mixed_roles_keep_result_observation_separate_from_effective_gate(
    observed,
):
    declare(observed, exception=True)
    result = capture(observed)
    newer_identity = capture(observed)
    mixed = newer_identity.model_copy(update={"result": result.result})
    admission = admit(observed, (mixed,))
    record = admission.records[0]
    assert (
        record.normalized_result.observed_at_utc
        == record.subject.result_capture.capture_observed_at_utc
    )
    assert record.normalized_result.observed_at_utc < record.capture_observed_at_utc
    assert (
        record.capture_observed_at_utc
        == record.subject.fixture_capture.capture_observed_at_utc
    )
    assert record.normalized_result.available_at_utc == record.registered_at_utc
    assert observed.observed.load_admission(admission.admission_id) == admission


def sql_admission(lane, values, *, sequence, key):
    started, verified, registered = lane.clock(), lane.clock(), lane.clock()
    records = tuple(
        ObservedSnapshotRecordV1.freeze(
            subject=x.subject,
            reviewer_attestation=x.reviewer_attestation,
            verified_at_utc=verified,
            registered_at_utc=registered,
        )
        for x in sorted(values, key=lambda x: x.subject.stream.provider_fixture_key)
    )
    admission = ObservedSnapshotAdmissionV1.freeze(
        scope_id=lane.scope.scope_id,
        scope_hash=lane.scope.content_hash,
        admission_sequence=sequence,
        actual_started_at_utc=started,
        verified_at_utc=verified,
        registered_at_utc=registered,
        records=records,
    )
    request = repository_module._request(
        key, lane.repo.operator_id, scope_id=lane.scope.scope_id, submissions=values
    )
    return admission, repository_module._admission_row(admission, request)


def insert_sql_children(session, admission):
    for record in admission.records:
        if record.normalized_result is not None:
            binding = repository_module._binding_row(record, "sportmonks")
            session.add(binding)
            session.flush()
            session.add(
                MatchResultRecord(
                    **{
                        c.name: getattr(binding, c.name)
                        for c in MatchResultRecord.__table__.columns
                    }
                )
            )
            session.flush()
        session.add(
            repository_module._snapshot_row(
                admission.admission_id, record, "sportmonks"
            )
        )
        session.flush()


def test_sql_successor_cannot_use_child_of_unsealed_later_parent_at_tied_clock(
    observed,
):
    declare(observed, exception=True)
    fixed = observed.clock() + timedelta(seconds=10)
    observed.clock = lambda: fixed
    observed.repo._clock = observed.clock
    observed.observed._clock = observed.clock
    root_values = reviewed(
        observed, (capture(observed, raw=raw_fixture(status="CANCELLED")),)
    )
    p, _ = sql_admission(observed, root_values, sequence=1, key="P-later")
    candidate = observed.observed.prepare(
        scope_id=observed.scope.scope_id,
        snapshots=(capture(observed, raw=raw_fixture(status="CANCELLED")),),
    )[0]
    successor = candidate.model_copy(
        update={
            "revision_sequence": 1,
            "predecessor_id": p.records[0].version_id,
            "predecessor_hash": p.records[0].content_hash,
        }
    )
    next_values = (
        ObservedSnapshotSubmissionV1(
            subject=successor, reviewer_attestation=review(observed, successor)
        ),
    )
    q, _ = sql_admission(observed, next_values, sequence=0, key="Q-earlier")
    before = counts(observed)
    with pytest.raises(IntegrityError, match="sealed earlier predecessor"):
        with observed.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, p)
            insert_sql_children(session, q)
    assert counts(observed) == before
    # Correct parent order in the same transaction is a readable, closed graph.
    p, p_row = sql_admission(observed, root_values, sequence=0, key="P-sealed-first")
    q, q_row = sql_admission(observed, next_values, sequence=1, key="Q-sealed-next")
    with observed.sessions.begin() as session:
        repository_module._lock(session)
        insert_sql_children(session, p)
        session.add(p_row)
        session.flush()
        insert_sql_children(session, q)
        session.add(q_row)
        session.flush()
    assert observed.observed.load_admission(q.admission_id) == q


@pytest.mark.parametrize("exception", [False, True])
def test_sql_first_seal_uses_scope_cohort_not_self_asserted_singleton_count(
    observed, exception
):
    if exception:
        subject = scope_subject(observed)
        season = subject.seasons[0].model_copy(
            update={
                "expected_fixture_ids": ("100", "101", "excluded"),
                "expected_fixture_count": 3,
                "exceptions": (
                    ObservedScopeExceptionV1(
                        provider_fixture_key="excluded",
                        reason="Synthetic predeclared exclusion",
                    ),
                ),
            }
        )
        subject = subject.model_copy(update={"seasons": (season,)})
        observed.scope = observed.observed.record_scope(
            request_key="scope",
            subject=subject,
            reviewer_attestation=review(observed, subject),
        )
    else:
        declare(observed)
    values = reviewed(observed, batch_inputs(observed))
    partial, row = sql_admission(
        observed, values[:1], sequence=0, key="partial-self-count"
    )
    assert row.record_count == len(partial.records) == 1
    assert len(observed.scope.subject.cohort_ids) == 2
    before = counts(observed)
    with pytest.raises(IntegrityError, match="exact declared scope cohort"):
        with observed.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, partial)
            session.add(row)
            session.flush()
    assert counts(observed) == before
    complete, row = sql_admission(observed, values, sequence=0, key="complete-cohort")
    with observed.sessions.begin() as session:
        repository_module._lock(session)
        insert_sql_children(session, complete)
        session.add(row)
        session.flush()
    assert observed.observed.load_admission(complete.admission_id) == complete


@pytest.mark.parametrize("observed", [3], indirect=True)
def test_sql_first_seal_checks_exact_fixture_set_even_when_count_matches(observed):
    broad = declare(observed)
    observed.cohort_count = 2
    narrow_subject = scope_subject(observed)
    narrow = observed.observed.record_scope(
        request_key="narrow",
        subject=narrow_subject,
        reviewer_attestation=review(observed, narrow_subject),
    )
    observed.cohort_count = 3
    observed.scope = broad
    values = reviewed(observed, batch_inputs(observed))
    observed.scope = narrow
    changed = tuple(
        x.subject.model_copy(
            update={"scope_id": narrow.scope_id, "scope_hash": narrow.content_hash}
        )
        for x in (values[0], values[2])
    )
    values = tuple(
        ObservedSnapshotSubmissionV1(
            subject=x, reviewer_attestation=review(observed, x)
        )
        for x in changed
    )
    admission, row = sql_admission(
        observed, values, sequence=0, key="wrong-set-same-count"
    )
    assert row.record_count == len(narrow.subject.cohort_ids) == 2
    with pytest.raises(IntegrityError, match="exact declared scope cohort"):
        with observed.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, admission)
            session.add(row)
            session.flush()


@pytest.mark.parametrize("kind", ["scope", "record", "mixed_admission"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_data_mode", "LIVE_STRICT"),
        ("source_data_mode", "MISSING"),
        ("retrospective", False),
        ("retrospective", 1),
    ],
)
def test_sql_refuses_missing_false_or_mixed_retrospective_provenance(
    observed, kind, field, value
):
    declare(observed)
    if kind == "scope":
        subject = scope_subject(observed, receipts=21)
        event = type(observed.scope).freeze(
            subject=subject,
            reviewer_attestation=review(observed, subject),
            recorded_at_utc=observed.clock(),
            capture_receipt_high_watermark=0,
        )
        request = repository_module._request(
            "invalid-provenance-scope",
            observed.repo.operator_id,
            subject=subject,
            reviewer_attestation=event.reviewer_attestation,
        )
        with observed.sessions() as session:
            row = observed.observed._scope_row(session, event, request)
        admission = None
    else:
        values = reviewed(observed, batch_inputs(observed))
        admission, row = sql_admission(
            observed, values, sequence=0, key="invalid-provenance"
        )
        if kind == "record":
            row = repository_module._snapshot_row(
                admission.admission_id, admission.records[0], "sportmonks"
            )
    document = strict_json_bytes(row.artifact_json.encode())
    subject = (
        document["records"][1]["subject"]
        if kind == "mixed_admission"
        else document["subject"]
    )
    if value == "MISSING":
        del subject[field]
    else:
        subject[field] = value
    # Rehash the SQL projection so rejection is the provenance guard, not a stale checksum.
    columns = {
        c.name: getattr(row, c.name)
        for c in row.__table__.columns
        if c.name != "row_sha256"
    }
    columns["artifact_json"] = canonical_json(document)
    bad = repository_module._row(type(row), **columns)
    before = counts(observed)
    with pytest.raises(IntegrityError, match="SOURCE_TIME_RESEARCH"):
        with observed.sessions.begin() as session:
            repository_module._lock(session)
            if kind == "mixed_admission":
                insert_sql_children(session, admission)
                # Keep the child projection consistent with the mixed envelope
                # to test provenance independently of the existing graph guard.
                child = repository_module._snapshot_row(
                    admission.admission_id, admission.records[1], "sportmonks"
                )
                changed = {
                    c.name: getattr(child, c.name)
                    for c in child.__table__.columns
                    if c.name != "row_sha256"
                }
                changed["artifact_json"] = canonical_json(document["records"][1])
                changed_child = repository_module._row(type(child), **changed)
                session.execute(
                    text(
                        "DROP TRIGGER trg_observed_snapshot_records_append_only_update"
                    )
                )
                session.execute(
                    child.__table__.update()
                    .where(child.__table__.c.version_id == child.version_id)
                    .values(
                        artifact_json=changed_child.artifact_json,
                        row_sha256=changed_child.row_sha256,
                    )
                )
            elif kind == "record":
                binding = repository_module._binding_row(
                    admission.records[0], "sportmonks"
                )
                session.add(binding)
                session.flush()
                session.add(
                    MatchResultRecord(
                        **{
                            c.name: getattr(binding, c.name)
                            for c in MatchResultRecord.__table__.columns
                        }
                    )
                )
                session.flush()
            session.add(bad)
            session.flush()
    assert counts(observed) == before


@pytest.fixture(params=("orm", "migration062"))
def source_backend(observed, request):
    if request.param == "migration062":
        from .test_database_schema import _trigger_signature

        names = observed_training_trigger_sql_v1().keys()
        before = {
            name: sql
            for name, sql in _trigger_signature(observed.engine)
            if name in names
        }
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", str(observed.engine.url))
        command.stamp(config, "head")
        command.downgrade(config, "f51e294b0687")
        command.upgrade(config, "062f3a5c1798")
        after = {
            name: sql
            for name, sql in _trigger_signature(observed.engine)
            if name in names
        }
        assert after == before
    return observed


def conflicting_home_alias(at):
    return ProviderTeamAliasRecord(
        alias_id="sm-home-conflict",
        internal_team_id="a",
        provider_id="sportmonks",
        provider_team_id="444",
        provider_team_name="Synthetic Conflicting Home",
        language="en",
        team_type="CLUB",
        available_at_utc=at,
    )


def test_mixed_role_identity_ambiguity_uses_latest_capture(source_backend):
    lane = source_backend
    declare(lane, exception=True)
    base = admit(lane, (capture(lane),))
    early_result = capture(lane, raw=raw_fixture(goals=3))
    ambiguous_at = lane.clock()
    with lane.sessions.begin() as session:
        session.add(conflicting_home_alias(ambiguous_at))
    latest = capture(lane, raw=raw_fixture(goals=3))
    all_latest = reviewed(lane, (latest,))[0].subject
    values = reviewed(
        lane, (latest.model_copy(update={"result": early_result.result}),)
    )
    subject = values[0].subject
    assert (
        subject.result_capture.capture_observed_at_utc
        < ambiguous_at
        < subject.fixture_capture.capture_observed_at_utc
    )
    assert subject.identity_evidence == all_latest.identity_evidence
    assert subject.resolved_home_team_id is all_latest.resolved_home_team_id is None
    assert subject.inspection.trainable and not subject.trainable
    withdrawn = admit(lane, values=values, key="mixed-identity-withdrawal")
    context = lane.observed.load_context(lane.scope.scope_id)
    assert context.select_heads(withdrawn.registered_at_utc) == base.records
    assert context.select_heads(context.actual_at_utc) == withdrawn.records
    assert counts(lane)["match_results"] == 1


@pytest.mark.parametrize("reference", ["team", "competition"])
def test_mixed_roles_keep_earliest_preexisting_reference_check(observed, reference):
    declare(observed, exception=True)
    early = capture(observed)
    at = observed.clock()
    with observed.sessions.begin() as session:
        model = (
            ProviderTeamAliasRecord
            if reference == "team"
            else ProviderCompetitionMappingRecord
        )
        original = session.get(
            model, "sm-h" if reference == "team" else "sm-competition"
        )
        columns = {c.name: getattr(original, c.name) for c in model.__table__.columns}
        columns.update(available_at_utc=at)
        columns["alias_id" if reference == "team" else "mapping_id"] = "late-reference"
        columns[
            "provider_team_name" if reference == "team" else "provider_competition_name"
        ] = "Synthetic Later Name"
        session.add(model(**columns))
    latest = capture(observed).model_copy(
        update={
            "home_team_alias_id"
            if reference == "team"
            else "competition_mapping_id": "late-reference",
        }
    )
    with pytest.raises(ValueError, match="preexisting.*unavailable"):
        reviewed(observed, (latest.model_copy(update={"result": early.result}),))
    assert reviewed(observed, (latest,))[0].subject.trainable


def test_tied_alias_addition_preserves_retries_but_rejects_stale_new_heads(
    source_backend,
):
    lane = source_backend
    declare(lane, exception=True)
    fixed = lane.clock() + timedelta(seconds=10)
    lane.clock = lane.repo._clock = lane.observed._clock = lambda: fixed
    original_values = reviewed(lane, (capture(lane),))
    base = admit(lane, values=original_values)
    original_json = canonical_json(base)
    candidate = capture(lane)
    stale = reviewed(lane, (candidate,))
    with lane.sessions.begin() as session:
        session.add(conflicting_home_alias(lane.clock()))
    assert (
        canonical_json(lane.observed.load_admission(base.admission_id)) == original_json
    )
    assert admit(lane, values=original_values) == base
    with pytest.raises(ValueError, match="reread bytes"):
        admit(lane, values=stale, key="stale-identity")
    withdrawn = admit(lane, (candidate,), key="fresh-identity")
    assert withdrawn.records[0].normalized_result is None
    assert withdrawn.records[0].subject.inspection.trainable
    lane.observed._clock = lambda: fixed + timedelta(seconds=1)
    context = lane.observed.load_context(lane.scope.scope_id)
    assert context.select_heads(fixed) == ()
    assert context.select_heads(context.actual_at_utc) == withdrawn.records
    assert lane.observed.load_admission(base.admission_id) == base


def test_sql_identity_evidence_cannot_omit_a_current_conflict(source_backend):
    lane = source_backend
    declare(lane, exception=True)
    base = admit(lane, (capture(lane),))
    with lane.sessions.begin() as session:
        session.add(conflicting_home_alias(lane.clock()))
    subject = reviewed(lane, (capture(lane),))[0].subject
    proof = subject.identity_evidence.model_copy(
        update={
            "team_aliases": tuple(
                x
                for x in subject.identity_evidence.team_aliases
                if x.record_id != "sm-home-conflict"
            ),
        }
    )
    claimed = subject.model_copy(
        update={"identity_evidence": proof, "resolved_home_team_id": "h"}
    )
    values = (
        ObservedSnapshotSubmissionV1(
            subject=claimed, reviewer_attestation=review(lane, claimed)
        ),
    )
    before = counts(lane)
    with pytest.raises(ValueError, match="reread bytes"):
        admit(lane, values=values, key="omitted-conflict")
    admission, row = sql_admission(lane, values, sequence=1, key="sql-omitted-conflict")
    with pytest.raises(IntegrityError, match="complete current catalog selection"):
        with lane.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, admission)
            session.add(row)
            session.flush()
    assert counts(lane) == before
    assert lane.observed.load_admission(base.admission_id) == base


def test_sql_identity_evidence_is_checked_after_last_child_insert(source_backend):
    lane = source_backend
    declare(lane, exception=True)
    values = reviewed(lane, (capture(lane),))
    admission, row = sql_admission(lane, values, sequence=0, key="interleaved-alias")
    before = counts(lane)
    with pytest.raises(IntegrityError, match="complete current catalog selection"):
        with lane.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, admission)
            session.add(
                conflicting_home_alias(values[0].subject.capture_observed_at_utc)
            )
            session.flush()
            session.add(row)
            session.flush()
    assert counts(lane) == before


@pytest.mark.parametrize(
    "collection", ["team_aliases", "competition_mappings", "match_mappings"]
)
def test_identity_source_hashes_are_reverified_not_trusted_from_sealed_record(
    observed, collection
):
    declare(observed, exception=True)
    subject = reviewed(observed, (capture(observed),))[0].subject
    refs = getattr(subject.identity_evidence, collection)
    proof = subject.identity_evidence.model_copy(
        update={
            collection: (
                refs[0].model_copy(update={"record_sha256": "0" * 64}),
                *refs[1:],
            ),
        }
    )
    claimed = subject.model_copy(update={"identity_evidence": proof})
    values = (
        ObservedSnapshotSubmissionV1(
            subject=claimed, reviewer_attestation=review(observed, claimed)
        ),
    )
    admission, row = sql_admission(
        observed, values, sequence=0, key="forged-source-hash"
    )
    # SQL proves set completeness; repository readback independently proves row hashes.
    with observed.sessions.begin() as session:
        repository_module._lock(session)
        insert_sql_children(session, admission)
        session.add(row)
        session.flush()
    with pytest.raises(ValueError, match="identity source evidence differs"):
        observed.observed.load_admission(admission.admission_id)


@pytest.mark.parametrize("existing", [False, True])
def test_sql_scope_watermark_requires_exact_actual_ledger_maximum(
    source_backend, existing
):
    lane = source_backend
    fixed = lane.clock() + timedelta(seconds=10)
    lane.clock = lane.repo._clock = lane.observed._clock = lambda: fixed
    old = capture(lane) if existing else None
    subject = scope_subject(lane, exception=True)
    attestation = review(lane, subject)
    maximum = int(existing)
    for watermark in (0, 2) if existing else (1,):
        value = ObservedCollectionScopeAdmissionV1.freeze(
            subject=subject,
            reviewer_attestation=attestation,
            recorded_at_utc=fixed,
            capture_receipt_high_watermark=watermark,
        )
        request = repository_module._request(
            f"bad-watermark-{watermark}",
            lane.repo.operator_id,
            subject=subject,
            reviewer_attestation=attestation,
        )
        with pytest.raises(
            IntegrityError, match="actual capture ledger high-watermark"
        ):
            with lane.sessions.begin() as session:
                repository_module._lock(session)
                session.add(lane.observed._scope_row(session, value, request))
                session.flush()
    value = ObservedCollectionScopeAdmissionV1.freeze(
        subject=subject,
        reviewer_attestation=attestation,
        recorded_at_utc=fixed,
        capture_receipt_high_watermark=maximum,
    )
    request = repository_module._request(
        "correct-watermark",
        lane.repo.operator_id,
        subject=subject,
        reviewer_attestation=attestation,
    )
    with lane.sessions.begin() as session:
        repository_module._lock(session)
        session.add(lane.observed._scope_row(session, value, request))
        session.flush()
    lane.scope = value
    if old is not None:
        with pytest.raises(ValueError, match="tied clocks"):
            reviewed(lane, (old,))
    admitted = admit(lane, (capture(lane),))
    assert lane.observed.load_admission(admitted.admission_id) == admitted


@pytest.mark.parametrize("receipts,records", [(1, 2), (2, 1)])
@pytest.mark.parametrize("status", ["FT", "CANCELLED"])
def test_sql_scope_limits_reject_cumulative_versions_and_receipts(
    source_backend, receipts, records, status
):
    lane = source_backend
    declare(lane, exception=True, receipts=receipts, records=records)
    base = admit(lane, (capture(lane),))
    candidate = capture(lane, raw=raw_fixture(goals=3, status=status))
    with pytest.raises(ValueError, match="receipt/record limit"):
        reviewed(lane, (candidate,))
    with lane.sessions.begin() as session:
        scope, rights = lane.observed._scope(session, lane.scope.scope_id)
        subject = lane.observed._subject(
            session,
            scope,
            rights,
            candidate,
            {r.stream.stream_id: r for r in base.records},
            lane.clock(),
        )
    values = (
        ObservedSnapshotSubmissionV1(
            subject=subject, reviewer_attestation=review(lane, subject)
        ),
    )
    admission, row = sql_admission(lane, values, sequence=1, key="over-scope-limits")
    before = counts(lane)
    with pytest.raises(IntegrityError, match="receipt/record limit"):
        with lane.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, admission)
            session.add(row)
            session.flush()
    assert counts(lane) == before
    assert lane.observed.load_admission(base.admission_id) == base


def test_sql_receipt_limit_counts_all_roles_once_and_ignores_unused_imports(
    source_backend,
):
    lane = source_backend
    declare(lane, exception=True, receipts=4, records=3)
    base = admit(lane, (capture(lane),))
    capture(lane)  # Unreferenced local import is not an admitted receipt.
    fixture, season, result = (
        capture(lane, raw=raw_fixture(goals=3)) for _ in range(3)
    )
    mixed = result.model_copy(
        update={"fixture": fixture.fixture, "season": season.season}
    )
    values = reviewed(lane, (mixed,))
    admission, row = sql_admission(lane, values, sequence=1, key="exact-role-limit")
    with lane.sessions.begin() as session:
        repository_module._lock(session)
        insert_sql_children(session, admission)
        session.add(row)
        session.flush()
    assert counts(lane)["training_capture_receipts"] == 5
    assert lane.observed.load_admission(admission.admission_id) == admission
    candidate = mixed.model_copy(
        update={"result": capture(lane, raw=raw_fixture(goals=3)).result}
    )
    with pytest.raises(ValueError, match="receipt/record limit"):
        reviewed(lane, (candidate,))
    with lane.sessions.begin() as session:
        scope, rights = lane.observed._scope(session, lane.scope.scope_id)
        subject = lane.observed._subject(
            session,
            scope,
            rights,
            candidate,
            {r.stream.stream_id: r for r in admission.records},
            lane.clock(),
        )
    values = (
        ObservedSnapshotSubmissionV1(
            subject=subject, reviewer_attestation=review(lane, subject)
        ),
    )
    over, row = sql_admission(lane, values, sequence=2, key="fifth-role-receipt")
    before = counts(lane)
    with pytest.raises(IntegrityError, match="receipt/record limit"):
        with lane.sessions.begin() as session:
            repository_module._lock(session)
            insert_sql_children(session, over)
            session.add(row)
            session.flush()
    assert counts(lane) == before
    assert lane.observed.load_admission(base.admission_id) == base
