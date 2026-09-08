"""Formal public writer contracts. All provider/review bytes are test-only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.training_correction import (
    CORRECTION_INTENT_V2,
    CorrectionComponent,
    CorrectionEvidenceV2,
    CorrectionRefV2,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.models import (
    MatchRecord,
    MatchResultRecord,
    ProviderCompetitionMappingRecord,
    ProviderTeamAliasRecord,
    TeamRecord,
)
from football_system.infrastructure.database.session import create_database_engine
from football_system.infrastructure.database.training_correction_repository import (
    ADMISSIONS,
    COMPONENTS,
    RESULTS,
    SqlAlchemyTrainingCorrectionRepository,
)
from football_system.infrastructure.database.training_correction_schema import (
    CORRECTION_TABLES,
)
from football_system.infrastructure.files.training_evidence import (
    json_pointer,
    strict_json_bytes,
)

from .test_training_admission_persistence import (
    SOURCE,
    admit,
    lane as admission_lane,
    prepare,
    review_document,
    write_evidence,
)

lane = admission_lane


@pytest.fixture
def corrected_lane(lane):
    prepare(lane, count=1)
    lane.base_admission = admit(lane)
    lane.corrections = SqlAlchemyTrainingCorrectionRepository(lane.repo)
    authority = strict_json_bytes(lane.repo.evidence.read("authority.json"))
    authority["attested_schema_versions"] = [CORRECTION_INTENT_V2]
    lane.correction_authority = write_evidence(
        lane.root, "correction-authority.json", authority
    )
    lane.repo.evidence.trusted_authorities["correction-authority.json"] = (
        lane.correction_authority.evidence_sha256
    )
    lane.base_pin = CorrectionRefV2(
        schema_version=lane.base_admission.schema_version,
        artifact_id=lane.base_admission.training_fact_admission_id,
        content_hash=lane.base_admission.admission_hash,
    )
    lane.base = lane.corrections.load_context(
        base_admissions=(lane.base_pin,), correction_ids=(), actual_at=lane.clock()
    ).versions[0]
    lane.next_correction = 0
    return lane


def reviewed_intent(
    lane,
    *,
    previous=None,
    raw=None,
    fixture=None,
    scope=None,
    evidence_changes=None,
    equal_time=False,
    trainable=True,
    omit_result_fields=(),
    revision_fields=True,
):
    previous = previous or lane.base
    submission = next(
        item
        for item in lane.submissions
        if item.candidate.normalized_result.match_id
        == previous.snapshot.stream.internal_match_id
    )
    lane.next_correction += 1
    n = lane.next_correction
    base_raw = {
        role: json_pointer(
            strict_json_bytes(lane.repo.evidence.read(f"{role}.json")),
            getattr(submission.source_evidence, role).record_pointer,
        )
        for role in ("fixture", "scope", "result")
    }
    adapter = strict_json_bytes(lane.repo.evidence.read("adapter.json"))
    adapter.update(
        schema_version="TRAINING_CORRECTION_JSON_ADAPTER_V2",
        adapter_version="2",
        status_rules=[
            {"raw_status": "FT", "category": "REGULAR_TIME_FINAL"},
            {"raw_status": "CANCELLED", "category": "CANCELLED"},
        ],
    )
    del adapter["regular_time_final_status"]
    if revision_fields:
        adapter["result"].update(
            revision_id="/revision_id", revision_order="/revision_order"
        )
    adapter_ref = write_evidence(lane.root, f"correction-adapter-{n}.json", adapter)
    base_raw["result"].update(
        home_goals=3, revision_id=f"provider-revision-{n}", revision_order=n
    )
    if equal_time:
        base_raw["result"].update(
            available_at_utc=previous.snapshot.result_source_available_at_utc.isoformat(),
            observed_at_utc=previous.snapshot.source_observed_at_utc.isoformat(),
        )
    else:
        at = previous.snapshot.result_source_available_at_utc + timedelta(days=1)
        base_raw["result"].update(
            available_at_utc=at.isoformat(), observed_at_utc=at.isoformat()
        )
    for role, updates in (("fixture", fixture), ("scope", scope), ("result", raw)):
        if updates:
            base_raw[role].update(updates)
    for key in omit_result_fields:
        base_raw["result"].pop(key)
    refs = {}
    for role in ("fixture", "scope", "result"):
        if role != "result" and not {"fixture": fixture, "scope": scope}[role]:
            refs[role] = lane.corrections.capture_reference(
                lane.receipts[role].capture_receipt_id,
                record_pointer=getattr(submission.source_evidence, role).record_pointer,
            )
        else:
            name = f"correction-{role}-{n}.json"
            write_evidence(lane.root, name, base_raw[role])
            receipt = lane.repo.capture_local_json(
                request_key=name,
                source_rights_admission_id=lane.recorded.source_rights_admission_id,
                source_id="source",
                provider_code="PROVIDER",
                evidence_reference=name,
            )
            refs[role] = lane.corrections.capture_reference(
                receipt.capture_receipt_id, record_pointer=""
            )
    evidence = CorrectionEvidenceV2(
        **refs,
        adapter=adapter_ref,
        **{
            "provider_mapping_id": submission.candidate.provider_mapping.mapping_id,
            "home_team_alias_id": submission.source_evidence.home_team_alias_id,
            "away_team_alias_id": submission.source_evidence.away_team_alias_id,
            "competition_mapping_id": submission.source_evidence.competition_mapping_id,
            **(evidence_changes or {}),
        },
    )
    intent = lane.corrections.prepare(
        predecessor_version_id=previous.version_id,
        source_rights_admission_id=lane.recorded.source_rights_admission_id,
        evidence=evidence,
        match_result_id=f"corrected-{n}" if trainable else None,
    )
    review = write_evidence(
        lane.root,
        f"correction-review-{n}.json",
        review_document(
            schema=CORRECTION_INTENT_V2, digest=intent.intent_hash, at=lane.clock()
        ),
    )
    return intent, review


def record(lane, pair, key=None):
    intent, review = pair
    return lane.corrections.admit(
        request_key=key or f"correction-{intent.match_result_id or intent.intent_hash}",
        intent=intent,
        reviewer_evidence=review,
        reviewer_authority=lane.correction_authority,
    )


def counts(lane):
    with lane.engine.connect() as c:
        return {
            name: c.scalar(text(f"SELECT COUNT(*) FROM {name}"))
            for name in (*CORRECTION_TABLES, "match_results")
        }


@pytest.mark.parametrize("same_key", [False, True])
def test_public_result_correction_materializes_existing_results_and_retains_v1(
    corrected_lane, same_key
):
    ctx = corrected_lane
    before = ctx.base_admission.model_dump_json()
    pair = reviewed_intent(
        ctx, raw={} if same_key else {"result_key": "new-provider-result-key"}
    )
    assert pair[0].changed_components == (CorrectionComponent.RESULT,)
    value = record(ctx, pair)
    version = ctx.corrections.load_version(value.artifact_id)
    assert version.revision_sequence == 1
    assert version.predecessor == ctx.base.reference
    assert version.normalized_result.home_goals == 3
    assert version.normalized_result.supersedes_match_result_id == "normalized-0"
    assert (
        version.normalized_result.ingested_at_utc
        == version.snapshot.result_source_available_at_utc
    )
    assert (
        ctx.repo.load(ctx.base_admission.training_fact_admission_id).model_dump_json()
        == before
    )
    assert ctx.corrections.load_version(ctx.base.version_id) == ctx.base
    historical = SqlAlchemyHistoricalRepository(ctx.sessions)
    assert historical.find_match_result("corrected-1").home_goals == 3
    assert historical.find_match_result("normalized-0").home_goals == 2
    assert record(ctx, pair) == value
    with ctx.sessions() as session:
        assert session.get(MatchRecord, "match-0").home_team_id == "home"
    assert counts(ctx)["match_results"] == 2


def test_equal_source_time_keeps_real_time_and_explicit_revision_order(corrected_lane):
    ctx = corrected_lane
    pair = reviewed_intent(ctx, equal_time=True)
    value = record(ctx, pair)
    result = value.content_payload.normalized_result
    assert result.available_at_utc == ctx.base.normalized_result.available_at_utc
    assert result.ingested_at_utc == ctx.base.normalized_result.ingested_at_utc
    assert value.content_payload.intent.candidate.provider_revision_order == 1
    assert result.source_result_key == ctx.base.normalized_result.source_result_key


def test_context_preserves_source_cutoffs_and_actual_registration(corrected_lane):
    ctx = corrected_lane
    value = record(ctx, reviewed_intent(ctx))
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(value.artifact_id,),
        actual_at=ctx.clock(),
    )
    assert context.select(
        source_cutoffs={"source": ctx.base.snapshot.result_source_available_at_utc}
    ) == (ctx.base,)
    assert (
        context.select(
            source_cutoffs={
                "source": value.content_payload.intent.candidate.result_source_available_at_utc
            }
        )[0].version_id
        == value.artifact_id
    )
    old = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(),
        actual_at=ctx.base.registered_at_utc,
    )
    assert old.versions == (ctx.base,)
    with pytest.raises(ValueError, match="actually registered"):
        ctx.corrections.load_context(
            base_admissions=(ctx.base_pin,),
            correction_ids=(value.artifact_id,),
            actual_at=ctx.base.registered_at_utc,
        )
    with pytest.raises(ValueError, match="per-source"):
        context.select(source_cutoffs={})
    events = ctx.corrections.invalidation_evidence(value.artifact_id)
    assert events == context.corrections
    assert len(events) == 1 and events[0].component is CorrectionComponent.RESULT
    v1 = events[0].as_v1()
    assert v1.content_payload.predecessor.artifact_id != "ROOT"
    assert v1.content_payload.successor.artifact_id != "ROOT"
    assert (
        ctx.corrections.current_invalidations(
            base_admissions=(ctx.base_pin,), actual_at=ctx.base.registered_at_utc
        )
        == ()
    )
    assert (
        ctx.corrections.current_invalidations(
            base_admissions=(ctx.base_pin,), actual_at=ctx.clock()
        )
        == events
    )


@pytest.mark.parametrize("status", ["CANCELLED", "WITHDRAWN", "UNCLEAR"])
def test_raw_nontrainable_head_and_explicit_fresh_restoration(corrected_lane, status):
    ctx = corrected_lane
    withdrawal = record(
        ctx,
        reviewed_intent(
            ctx,
            raw={
                "status": status,
                "home_goals": None,
                "away_goals": None,
                "finalized_at_utc": None,
                "score_semantics": None,
            },
            trainable=False,
        ),
    )
    head = ctx.corrections.load_version(withdrawal.artifact_id)
    assert head.snapshot.provider_raw_status == status
    assert not head.snapshot.trainable and head.normalized_result is None
    assert counts(ctx)["match_results"] == 1
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(withdrawal.artifact_id,),
        actual_at=ctx.clock(),
    )
    assert context.select(
        source_cutoffs={"source": head.snapshot.result_source_available_at_utc}
    ) == (head,)
    restore = record(ctx, reviewed_intent(ctx, previous=head))
    assert (
        restore.content_payload.normalized_result.supersedes_match_result_id
        == "normalized-0"
    )
    assert restore.content_payload.intent.revision_sequence == 2
    with pytest.raises(ValueError, match="complete exact predecessor"):
        ctx.corrections.load_context(
            base_admissions=(ctx.base_pin,),
            correction_ids=(restore.artifact_id,),
            actual_at=ctx.clock(),
        )
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(withdrawal.artifact_id, restore.artifact_id),
        actual_at=ctx.clock(),
    )
    assert context.select(
        source_cutoffs={
            "source": restore.content_payload.intent.candidate.result_source_available_at_utc
        }
    )[0].snapshot.trainable


def test_fixture_team_kickoff_and_season_metadata_never_overwrites_anchor(
    corrected_lane,
):
    ctx = corrected_lane
    with ctx.sessions.begin() as session:
        session.add(
            TeamRecord(
                team_id="other", canonical_key="other", name="Other", team_type="CLUB"
            )
        )
        session.flush()
        session.add(
            ProviderTeamAliasRecord(
                alias_id="alias-other",
                internal_team_id="other",
                provider_id="provider",
                provider_team_id="p-other",
                provider_team_name="Other",
                language="en",
                team_type="CLUB",
                available_at_utc=SOURCE,
            )
        )
        session.add(
            ProviderCompetitionMappingRecord(
                mapping_id="next-season",
                internal_competition_id="league",
                provider_id="provider",
                provider_competition_id="p-league",
                provider_competition_name="League",
                language="en",
                season="2025/26",
                competition_type="LEAGUE",
                available_at_utc=SOURCE,
            )
        )
    shared = {
        "season_id": "p-2025-26",
        "home_team_id": "p-other",
        "kickoff_at_utc": (
            ctx.base.snapshot.identity.kickoff_at_utc + timedelta(minutes=10)
        ).isoformat(),
    }
    pair = reviewed_intent(
        ctx,
        fixture=shared,
        scope={"season_id": "p-2025-26"},
        raw=shared,
        evidence_changes={
            "home_team_alias_id": "alias-other",
            "competition_mapping_id": "next-season",
        },
    )
    assert pair[0].changed_components == (
        CorrectionComponent.FIXTURE,
        CorrectionComponent.MAPPING,
        CorrectionComponent.SEASON,
        CorrectionComponent.RESULT,
    )
    value = record(ctx, pair)
    version = ctx.corrections.load_version(value.artifact_id)
    assert version.snapshot.identity.internal_home_team_id == "other"
    assert version.snapshot.identity.season == "2025/26"
    with ctx.sessions() as session:
        anchor = session.get(MatchRecord, "match-0")
        assert anchor.home_team_id == "home"
        assert anchor.kickoff_at_utc == ctx.base.snapshot.identity.kickoff_at_utc
        assert len(tuple(session.scalars(select(MatchRecord)))) == 1
    assert (
        ctx.repo.load(ctx.base_admission.training_fact_admission_id)
        == ctx.base_admission
    )


@pytest.mark.parametrize(
    "mutation", ["components", "predecessor_hash", "score", "result_id", "sequence"]
)
def test_reviewed_intent_cannot_supply_predecessor_proof_or_mix_components(
    corrected_lane, mutation
):
    ctx = corrected_lane
    intent, review = reviewed_intent(ctx)
    updates = {
        "components": {
            "changed_components": (
                CorrectionComponent.FIXTURE,
                CorrectionComponent.RESULT,
            )
        },
        "predecessor_hash": {
            "predecessor": intent.predecessor.model_copy(
                update={"content_hash": "f" * 64}
            )
        },
        "score": {"candidate": intent.candidate.model_copy(update={"home_goals": 8})},
        "result_id": {"match_result_id": "normalized-0"},
        "sequence": {"revision_sequence": 3},
    }[mutation]
    changed = intent.model_copy(update=updates)
    changed_review = write_evidence(
        ctx.root,
        "tampered-review.json",
        review_document(
            schema=CORRECTION_INTENT_V2, digest=changed.intent_hash, at=ctx.clock()
        ),
    )
    before = counts(ctx)
    with pytest.raises(ValueError):
        record(ctx, (changed, changed_review))
    assert counts(ctx) == before


def test_competing_reviews_are_serialized_one_successor_and_no_partial_rows(
    corrected_lane,
):
    ctx = corrected_lane
    left, right = reviewed_intent(ctx), reviewed_intent(ctx, raw={"home_goals": 4})
    barrier = Barrier(2)

    def submit(pair):
        barrier.wait(timeout=10)
        try:
            return record(ctx, pair)
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = executor.submit(submit, left), executor.submit(submit, right)
        results = [a.result(timeout=120), b.result(timeout=120)]
    assert sum(isinstance(x, ValueError) for x in results) == 1
    assert "head" in str(next(x for x in results if isinstance(x, ValueError)))
    assert counts(ctx)[ADMISSIONS.name] == 1
    assert counts(ctx)[COMPONENTS.name] == 5
    assert counts(ctx)[RESULTS.name] == 1
    assert counts(ctx)["match_results"] == 2


@pytest.mark.parametrize(
    "filename",
    [
        "correction-result-1.json",
        "correction-review-1.json",
        "correction-adapter-1.json",
    ],
)
def test_missing_or_changed_actual_bytes_refuse_write_and_replay(
    corrected_lane, filename
):
    ctx = corrected_lane
    pair = reviewed_intent(ctx)
    value = record(ctx, pair)
    (ctx.root / filename).unlink()
    with pytest.raises(ValueError, match="read"):
        ctx.corrections.load_version(value.artifact_id)
    with pytest.raises(ValueError, match="read"):
        record(ctx, pair)


@pytest.mark.parametrize("future", [False, True])
def test_review_cannot_predate_capture_or_follow_operation_start(
    corrected_lane, future
):
    ctx = corrected_lane
    intent, _ = reviewed_intent(ctx)
    at = ctx.clock.value + timedelta(days=1) if future else ctx.base.registered_at_utc
    review = write_evidence(
        ctx.root,
        "bad-time-review.json",
        review_document(schema=CORRECTION_INTENT_V2, digest=intent.intent_hash, at=at),
    )
    before = counts(ctx)
    with pytest.raises(ValueError, match="review"):
        record(ctx, (intent, review))
    assert counts(ctx) == before


@pytest.mark.parametrize("phase", ["write", "readback"])
def test_expiry_during_transaction_rolls_back_correction_and_normalized_rows(
    corrected_lane, monkeypatch, phase
):
    ctx = corrected_lane
    pair = reviewed_intent(ctx)
    before = counts(ctx)
    expiry = ctx.rights.expires_at_utc

    def after_statement(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith("INSERT INTO training_correction_admissions "):
            ctx.clock.value = expiry

    if phase == "write":
        event.listen(ctx.engine, "after_cursor_execute", after_statement)
    else:
        original = ctx.corrections._load_version

        def loaded(session, version_id):
            value = original(session, version_id)
            if value.revision_sequence:
                ctx.clock.value = expiry
            return value

        monkeypatch.setattr(ctx.corrections, "_load_version", loaded)
    try:
        with pytest.raises(ValueError, match="active"):
            record(ctx, pair)
    finally:
        if phase == "write":
            event.remove(ctx.engine, "after_cursor_execute", after_statement)
    assert counts(ctx) == before


def test_bare_public_and_sql_result_writers_cannot_extend_admitted_stream(
    corrected_lane,
):
    ctx = corrected_lane
    original = ctx.submissions[0].candidate.normalized_result
    corrected = original.model_copy(
        update={
            "match_result_id": "bare",
            "source_result_key": "bare",
            "supersedes_match_result_id": original.match_result_id,
            "home_goals": 9,
            "payload_hash": match_result_payload_sha256(9, 1),
            "ingested_at_utc": original.ingested_at_utc + timedelta(seconds=1),
        }
    )
    with pytest.raises(IntegrityError, match="controlled"):
        SqlAlchemyHistoricalRepository(ctx.sessions).append_match_result(corrected)
    with pytest.raises(IntegrityError, match="controlled"):
        with ctx.sessions.begin() as session:
            row = session.get(MatchResultRecord, original.match_result_id)
            values = {col.name: getattr(row, col.name) for col in row.__table__.columns}
            values.update(
                match_result_id="bare-sql",
                source_result_key="bare-sql",
                supersedes_match_result_id=original.match_result_id,
                available_at_utc=original.available_at_utc + timedelta(days=1),
                ingested_at_utc=original.ingested_at_utc + timedelta(days=1),
            )
            session.execute(MatchResultRecord.__table__.insert().values(**values))
    assert counts(ctx)["match_results"] == 1


def test_v1_loader_still_refuses_unregistered_successor_corruption(corrected_lane):
    ctx = corrected_lane
    with ctx.engine.begin() as c:
        c.exec_driver_sql("DROP TRIGGER trg_match_results_controlled_stream_insert")
    original = ctx.submissions[0].candidate.normalized_result
    corrected = original.model_copy(
        update={
            "match_result_id": "corruption",
            "source_result_key": "corruption",
            "supersedes_match_result_id": original.match_result_id,
            "ingested_at_utc": original.ingested_at_utc + timedelta(seconds=1),
        }
    )
    with ctx.sessions.begin() as session:
        row = session.get(MatchResultRecord, original.match_result_id)
        values = {
            column.name: getattr(row, column.name) for column in row.__table__.columns
        }
        values.update(
            match_result_id=corrected.match_result_id,
            source_result_key=corrected.source_result_key,
            ingested_at_utc=corrected.ingested_at_utc,
            supersedes_match_result_id=original.match_result_id,
        )
        session.execute(MatchResultRecord.__table__.insert().values(**values))
    with pytest.raises(ValueError, match="controlled.*missing"):
        ctx.repo.load(ctx.base_admission.training_fact_admission_id)


@pytest.mark.parametrize("table", CORRECTION_TABLES)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "REPLACE"])
def test_correction_rows_are_append_only(corrected_lane, table, operation):
    ctx = corrected_lane
    record(ctx, reviewed_intent(ctx))
    sql = {
        "UPDATE": f"UPDATE {table} SET row_sha256=row_sha256",
        "DELETE": f"DELETE FROM {table}",
        "REPLACE": f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
    }[operation]
    with pytest.raises(IntegrityError):
        with ctx.engine.begin() as c:
            c.exec_driver_sql(sql)


def test_runtime_rebuild_preserves_all_old_rows_and_checksum(corrected_lane):
    ctx = corrected_lane
    with ctx.engine.connect() as c:
        meta = (
            c.execute(text("SELECT * FROM training_correction_schema_versions"))
            .mappings()
            .one()
        )
        assert meta["preserved_result_count"] == 0
        assert len(meta["preserved_results_sha256"]) == 64
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert (
        ctx.repo.load(ctx.base_admission.training_fact_admission_id)
        == ctx.base_admission
    )


def test_new_migration_and_runtime_schema_guard_parity(corrected_lane, tmp_path):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "d3fc0729e465")
    command.upgrade(config, "e40d183af576")
    engine = create_database_engine(url)
    try:

        def schema(engine):
            with engine.connect() as c:
                return {
                    row[0]: row[1]
                    for row in c.exec_driver_sql(
                        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
                    )
                    if row[0].startswith("training_correction")
                    or row[0].startswith("trg_training_correction")
                    or row[0]
                    in {
                        "trg_match_results_supersession_insert",
                        "trg_match_results_immutable_insert_existing",
                        "trg_match_results_controlled_stream_insert",
                    }
                }

        assert schema(engine) == schema(corrected_lane.engine)
        assert set(CORRECTION_TABLES) <= set(inspect(engine).get_table_names())
        with engine.connect() as c:
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert (
                c.scalar(text("SELECT version_num FROM alembic_version"))
                == "e40d183af576"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "raw",
    [
        {"fixture_key": "another-fixture"},
        {"home_team_id": "wrong-team"},
        {"season_id": "wrong-season"},
        {"home_goals": True},
        {"available_at_utc": "2025-05-18T00:00:00Z"},
        {"observed_at_utc": "2030-01-01T00:00:00Z"},
        {"finalized_at_utc": "2025-05-18T04:00:00"},
        {"revision_order": "1"},
    ],
)
def test_public_prepare_rejects_invalid_explicit_provider_fields(corrected_lane, raw):
    with pytest.raises(ValueError):
        reviewed_intent(corrected_lane, raw=raw)
    assert counts(corrected_lane)[ADMISSIONS.name] == 0
    assert counts(corrected_lane)["match_results"] == 1


def test_equal_source_time_requires_order_even_if_both_paths_are_removed(
    corrected_lane,
):
    ctx = corrected_lane
    intent, _ = reviewed_intent(ctx, equal_time=True)
    adapter = strict_json_bytes(
        ctx.repo.evidence.read(intent.evidence.adapter.evidence_reference)
    )
    adapter["result"].pop("revision_id")
    adapter["result"].pop("revision_order")
    ref = write_evidence(ctx.root, "unordered-adapter.json", adapter)
    with pytest.raises(ValueError, match="equal source time"):
        ctx.corrections.prepare(
            predecessor_version_id=ctx.base.version_id,
            source_rights_admission_id=ctx.recorded.source_rights_admission_id,
            evidence=intent.evidence.model_copy(update={"adapter": ref}),
            match_result_id="unordered",
        )


@pytest.mark.parametrize(
    "change",
    [
        {"home_team_alias_id": "missing"},
        {"home_team_alias_id": "alias-away"},
        {"provider_mapping_id": "missing"},
        {"competition_mapping_id": "missing"},
    ],
)
def test_public_prepare_requires_exact_registered_identity_refs(corrected_lane, change):
    with pytest.raises(ValueError):
        reviewed_intent(corrected_lane, evidence_changes=change)
    assert counts(corrected_lane)[ADMISSIONS.name] == 0


@pytest.mark.parametrize(
    "failure", ["bytes", "review", "hash", "predecessor", "component"]
)
def test_write_failure_never_leaves_partial_result_or_correction(
    corrected_lane, failure
):
    ctx = corrected_lane
    intent, review = reviewed_intent(ctx)
    if failure == "bytes":
        (ctx.root / "correction-result-1.json").unlink()
    elif failure == "review":
        (ctx.root / review.evidence_reference).unlink()
    elif failure == "hash":
        review = ctx.submissions[0].reviewer_evidence
    elif failure == "predecessor":
        intent = intent.model_copy(
            update={
                "predecessor": intent.predecessor.model_copy(
                    update={"artifact_id": "unknown-version"}
                )
            }
        )
    else:
        components = list(intent.predecessor_components)
        components[0] = components[0].model_copy(
            update={"reference": components[1].reference}
        )
        intent = intent.model_copy(update={"predecessor_components": tuple(components)})
    before = counts(ctx)
    with pytest.raises(ValueError):
        record(ctx, (intent, review))
    assert counts(ctx) == before


def test_expired_retry_is_original_audit_read_but_new_operation_fails(corrected_lane):
    ctx = corrected_lane
    pair = reviewed_intent(ctx)
    value = record(ctx, pair)
    ctx.clock.value = ctx.rights.expires_at_utc
    calls = len(ctx.clock.calls)
    assert record(ctx, pair) == value
    assert len(ctx.clock.calls) == calls
    with pytest.raises(ValueError, match="active"):
        record(ctx, pair, key="new-operation-after-expiry")


def test_rehashed_cycle_and_missing_component_corruption_fail_closed(corrected_lane):
    from football_system.infrastructure.database.training_correction_repository import (
        _sealed_row,
    )

    ctx = corrected_lane
    value = record(ctx, reviewed_intent(ctx))
    with ctx.engine.begin() as c:
        c.exec_driver_sql(
            "DROP TRIGGER trg_training_correction_admissions_append_only_update"
        )
        row = dict(c.execute(select(ADMISSIONS)).mappings().one())
        row.pop("row_sha256")
        row["predecessor_version_id"] = value.artifact_id
        c.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        c.execute(ADMISSIONS.update().values(**_sealed_row(ADMISSIONS, **row)))
    with pytest.raises(ValueError, match="cycle"):
        ctx.corrections.load_version(value.artifact_id)


def test_missing_controlled_component_is_detected_by_historical_v1_replay(
    corrected_lane,
):
    ctx = corrected_lane
    value = record(ctx, reviewed_intent(ctx))
    with ctx.engine.begin() as c:
        c.exec_driver_sql(
            "DROP TRIGGER trg_training_correction_components_append_only_delete"
        )
        c.execute(COMPONENTS.delete().where(COMPONENTS.c.component == "SEASON"))
    with pytest.raises(ValueError, match="component"):
        ctx.corrections.load_version(value.artifact_id)
    with pytest.raises(ValueError, match="component"):
        ctx.repo.load(ctx.base_admission.training_fact_admission_id)


def test_migration_preserves_populated_v1_admission_children_and_result_lineage(lane):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(lane.engine.url))
    command.stamp(config, "e40d183af576")
    command.downgrade(config, "d3fc0729e465")
    prepare(lane, count=1)
    value = admit(lane)
    with lane.engine.connect() as c:
        before = c.exec_driver_sql(
            "SELECT * FROM match_results ORDER BY match_result_id"
        ).all()
        children = c.exec_driver_sql(
            "SELECT row_sha256 FROM training_fact_bindings"
        ).all()
    command.upgrade(config, "e40d183af576")
    with lane.engine.connect() as c:
        assert (
            c.exec_driver_sql(
                "SELECT * FROM match_results ORDER BY match_result_id"
            ).all()
            == before
        )
        assert (
            c.exec_driver_sql("SELECT row_sha256 FROM training_fact_bindings").all()
            == children
        )
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert lane.repo.load(value.training_fact_admission_id) == value


def test_successive_same_key_corrections_keep_exact_version_and_component_chain(
    corrected_lane,
):
    ctx = corrected_lane
    first = record(ctx, reviewed_intent(ctx, equal_time=True))
    previous = ctx.corrections.load_version(first.artifact_id)
    second = record(ctx, reviewed_intent(ctx, previous=previous, raw={"home_goals": 4}))
    assert ctx.corrections.load_admission(first.artifact_id) == first
    assert ctx.corrections.load_admission(second.artifact_id) == second
    latest = ctx.corrections.load_version(second.artifact_id)
    assert (
        latest.normalized_result.supersedes_match_result_id
        == previous.normalized_result.match_result_id
    )
    assert latest.revision_sequence == 2
    assert (
        latest.components[:-1] == previous.components[:-1] == ctx.base.components[:-1]
    )
    assert latest.components[-1] != previous.components[-1]
    context = ctx.corrections.load_context(
        base_admissions=(ctx.base_pin,),
        correction_ids=(first.artifact_id, second.artifact_id),
        actual_at=ctx.clock(),
    )
    assert context.select(
        source_cutoffs={"source": previous.snapshot.result_source_available_at_utc}
    ) == (previous,)
    assert ctx.repo.load(ctx.base_pin.artifact_id) == ctx.base_admission


def test_late_parent_seal_failure_rolls_back_authorization_and_result(
    corrected_lane, monkeypatch
):
    ctx = corrected_lane
    pair = reviewed_intent(ctx)
    original = ctx.corrections._component_rows
    monkeypatch.setattr(
        ctx.corrections, "_component_rows", lambda value: original(value)[:-1]
    )
    before = counts(ctx)
    with pytest.raises(IntegrityError, match="complete exact"):
        record(ctx, pair)
    assert counts(ctx) == before


def test_canonical_provider_reassignment_boundary_is_explicit(corrected_lane):
    from football_system.infrastructure.database.models import (
        ProviderMatchMappingRecord,
    )

    ctx = corrected_lane
    with ctx.sessions.begin() as session:
        session.add(
            MatchRecord(
                internal_match_id="other-match",
                competition_id="league",
                home_team_id="home",
                away_team_id="away",
                kickoff_at_utc=SOURCE + timedelta(days=2),
                status="FINISHED",
                available_at_utc=SOURCE,
                created_at_utc=ctx.clock(),
            )
        )
        session.flush()
        session.add(
            ProviderMatchMappingRecord(
                mapping_id="other-mapping",
                provider_id="provider",
                external_namespace="fixture",
                external_match_id="other-fixture",
                internal_match_id="other-match",
                resolution_method="EXPLICIT_MAPPING",
                confidence=1,
                available_at_utc=SOURCE,
            )
        )
    with pytest.raises(ValueError, match="canonical provider reassignment"):
        reviewed_intent(ctx, evidence_changes={"provider_mapping_id": "other-mapping"})
    assert counts(ctx)[ADMISSIONS.name] == 0


@pytest.mark.parametrize("status", ["CANCELLED", "UNCLEAR", "FT"])
def test_absent_scores_only_allowed_for_nontrainable_original_status(
    corrected_lane, status
):
    ctx = corrected_lane
    fields = ("home_goals", "away_goals", "finalized_at_utc", "score_semantics")
    if status == "FT":
        with pytest.raises(ValueError, match="missing provider field"):
            reviewed_intent(ctx, omit_result_fields=fields)
        return
    value = record(
        ctx,
        reviewed_intent(
            ctx, raw={"status": status}, omit_result_fields=fields, trainable=False
        ),
    )
    head = ctx.corrections.load_version(value.artifact_id)
    assert head.normalized_result is None
    assert (
        head.snapshot.home_goals is None
        and head.snapshot.provider_finalized_at_utc is None
    )
    assert head.snapshot.provider_raw_status == status
    assert counts(ctx)["match_results"] == 1
