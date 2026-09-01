import json
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from football_system.application.historical_archive import (
    MATERIALIZATION_POLICY,
    HistoricalArchiveImportRepository,
    HistoricalArchiveRegistrationConflict,
    HistoricalArchiveService,
)
from football_system.domain.archive import (
    HISTORICAL_ARCHIVE_SCHEMA_VERSION,
    HistoricalArchiveDatasetKind,
    HistoricalDataMode,
    archive_payload_sha256,
)
from football_system.domain.match import ProviderMatchMapping
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.session import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from football_system.infrastructure.providers.historical_archive import (
    ArchiveValidationError,
    LocalArchiveStore,
)

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_DIRECTORY = ROOT / "data" / "fixtures" / "historical_acceptance"
UTC = timezone.utc
CREATED = datetime(2025, 1, 20, 12, 0, tzinfo=UTC)
IMPORTED = CREATED + timedelta(minutes=1)


def _repository() -> tuple[object, SqlAlchemyHistoricalRepository]:
    engine = create_database_engine("sqlite:///:memory:")
    create_schema(engine)
    return engine, SqlAlchemyHistoricalRepository(create_session_factory(engine))


def test_validate_summarizes_all_six_static_acceptance_manifests() -> None:
    service = HistoricalArchiveService()

    summary = service.validate(
        ACCEPTANCE_DIRECTORY,
        HistoricalDataMode.LIVE_STRICT,
    )

    assert summary == service.validate(ACCEPTANCE_DIRECTORY, "LIVE_STRICT")
    assert summary.operation == "VALIDATE"
    assert summary.archive_count == 6
    assert summary.record_count == 365
    assert len(summary.manifests) == len(summary.checksums) == 6
    assert {item.dataset_kind for item in summary.per_kind} == set(
        HistoricalArchiveDatasetKind
    )
    assert all(item.archive_count == 1 for item in summary.per_kind)
    assert sum(item.record_count for item in summary.per_kind) == 365
    assert summary.report_label == "LIVE_STRICT"
    assert summary.retrospective is False
    assert summary.registration_scope == "MANIFEST_PROVENANCE_ONLY"
    assert summary.materialization_policy == MATERIALIZATION_POLICY
    assert summary.registered_archive_ids == summary.existing_archive_ids == ()
    assert len(LocalArchiveStore(ACCEPTANCE_DIRECTORY).archives) == 6


def test_register_is_provenance_only_and_exactly_idempotent() -> None:
    engine, repository = _repository()
    service = HistoricalArchiveService()
    assert isinstance(repository, HistoricalArchiveImportRepository)

    first = service.register(
        ACCEPTANCE_DIRECTORY,
        repository,
        IMPORTED,
        HistoricalDataMode.LIVE_STRICT,
    )
    second = service.register(
        ACCEPTANCE_DIRECTORY,
        repository,
        IMPORTED + timedelta(hours=1),
        HistoricalDataMode.LIVE_STRICT,
    )

    expected_ids = {manifest.archive_id for manifest in first.manifests}
    assert set(first.registered_archive_ids) == expected_ids
    assert first.existing_archive_ids == ()
    assert second.registered_archive_ids == ()
    assert set(second.existing_archive_ids) == expected_ids
    assert len(repository.historical_archive_manifests()) == 6

    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM historical_archive_imports"))
            == 6
        )
        for table_name in (
            "matches",
            "provider_match_mappings",
            "market_odds_snapshots",
            "sporttery_bonus_snapshots",
            "manual_quant_inputs",
            "match_results",
        ):
            assert connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) == 0


def test_tampered_archive_fails_before_any_database_change(tmp_path: Path) -> None:
    archive_directory = tmp_path / "tampered"
    shutil.copytree(ACCEPTANCE_DIRECTORY, archive_directory)
    market_path = archive_directory / "market_odds.json"
    document = json.loads(market_path.read_text(encoding="utf-8"))
    document["records"][0]["payload"]["quotes"][0]["odds"] = "9.99"
    market_path.write_text(json.dumps(document), encoding="utf-8")
    _, repository = _repository()

    with pytest.raises(ArchiveValidationError, match="checksum mismatch"):
        HistoricalArchiveService().register(
            archive_directory,
            repository,
            IMPORTED,
            HistoricalDataMode.LIVE_STRICT,
        )

    assert repository.historical_archive_manifests() == ()


def test_registration_rejects_early_or_naive_import_time_without_writes() -> None:
    _, repository = _repository()
    service = HistoricalArchiveService()

    with pytest.raises(ValueError, match="cannot precede manifest creation"):
        service.register(
            ACCEPTANCE_DIRECTORY,
            repository,
            CREATED - timedelta(seconds=1),
            HistoricalDataMode.LIVE_STRICT,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        service.register(
            ACCEPTANCE_DIRECTORY,
            repository,
            datetime(2025, 1, 20, 12, 1),
            HistoricalDataMode.LIVE_STRICT,
        )

    assert repository.historical_archive_manifests() == ()


def test_conflict_preflight_prevents_partial_provenance_registration() -> None:
    _, repository = _repository()
    service = HistoricalArchiveService()
    validation = service.validate(
        ACCEPTANCE_DIRECTORY,
        HistoricalDataMode.LIVE_STRICT,
    )
    first_manifest = validation.manifests[0]
    repository.append_historical_archive_import(
        first_manifest.model_copy(
            update={"source_description": "conflicting stored provenance"}
        ),
        IMPORTED,
    )

    with pytest.raises(HistoricalArchiveRegistrationConflict, match="archive ID"):
        service.register(
            ACCEPTANCE_DIRECTORY,
            repository,
            IMPORTED,
            HistoricalDataMode.LIVE_STRICT,
        )

    assert len(repository.historical_archive_manifests()) == 1


def test_research_validation_returns_retrospective_report_label(tmp_path: Path) -> None:
    source_available = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    retrospective_import = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    archive_created = retrospective_import + timedelta(minutes=1)
    mapping = ProviderMatchMapping(
        mapping_id="research-mapping-1",
        provider_code="RESEARCH_PROVIDER",
        external_namespace="research",
        external_match_id="external-match-1",
        internal_match_id="match-1",
        resolution_method="RESEARCH_EXACT",
        confidence=Decimal(1),
        available_at_utc=source_available,
    )
    records = [
        {
            "retrospective": True,
            "imported_at_utc": retrospective_import.isoformat(),
            "payload": mapping.model_dump(mode="json"),
        }
    ]
    document = {
        "manifest": {
            "archive_schema_version": HISTORICAL_ARCHIVE_SCHEMA_VERSION,
            "archive_id": "research-mappings-v1",
            "provider_code": "RESEARCH_PROVIDER",
            "dataset_kind": HistoricalArchiveDatasetKind.PROVIDER_MAPPINGS.value,
            "created_at_utc": archive_created.isoformat(),
            "source_reference": "test://research-mappings",
            "source_description": "Retrospective source-time research test",
            "license_note": "TEST_ONLY",
            "data_mode": HistoricalDataMode.SOURCE_TIME_RESEARCH.value,
            "payload_sha256": archive_payload_sha256(records),
            "record_count": len(records),
        },
        "records": records,
    }
    (tmp_path / "research-mappings.json").write_text(
        json.dumps(document), encoding="utf-8"
    )

    summary = HistoricalArchiveService().validate(
        tmp_path,
        HistoricalDataMode.SOURCE_TIME_RESEARCH,
    )

    assert summary.archive_count == 1
    assert summary.retrospective is True
    assert summary.report_label == "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
    assert summary.materialization_policy == MATERIALIZATION_POLICY
