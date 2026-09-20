"""Four fixed offline input adapters. No inference, decisions, network or CLI forwarding."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from football_system.application.daily_slate import PlanSportteryDailySlateService
from football_system.application.live_ingestion import LiveFixtureIngestionService
from football_system.application.live_sources import LiveSportteryIngestionService
from football_system.application.prospective_requests import EvidenceImportRequestV1
from football_system.config import AppSettings
from football_system.domain.archive import canonical_json
from football_system.infrastructure.database.identity_repositories import SqlAlchemyMatchIdentityRepository
from football_system.infrastructure.database.live_source_repositories import SqlAlchemyLiveSourceRepository
from football_system.infrastructure.database.models import MatchRecord
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.files.daily_slate import load_sporttery_daily_slate
from football_system.infrastructure.files.prospective import verified_manual_bytes
from football_system.infrastructure.files.raw_archive import RawDataArchive
from football_system.infrastructure.files.return_distribution import strict_return_json
from football_system.infrastructure.providers.real.fixture_manual import (
    ReviewedFixtureManualArchiveProvider, load_reviewed_fixture_manual_archive, reviewed_fixture_manual_request,
)
from football_system.infrastructure.providers.real.sporttery_manual import (
    SportteryManualArchiveCaptureProvider, load_verified_sporttery_manual_documents,
)


def inside(root, reference):
    name = PurePosixPath(reference)
    if name.is_absolute() or "\\" in reference or ":" in reference or any(p in {"", ".", ".."} for p in reference.split("/")):
        raise ValueError("SOURCE_MUST_BE_CONTAINED")
    path = root / reference
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("SOURCE_MUST_BE_CONTAINED")


def exact_buckets(plan):
    """Only organizational grouping, not probability/candidate generation."""
    grouped = {}
    for item in plan.candidates:
        key = item.candidate.kickoff_at_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        grouped.setdefault(key, []).append(dict(candidate_id=item.candidate.candidate_id, match_id=item.canonical_match_id))
    return [dict(kickoff_at_utc=key, members=grouped[key]) for key in sorted(grouped, key=lambda k: datetime.fromisoformat(k.replace("Z", "+00:00")))]


def validate_input(kind, root, entry, clock, classification, retention):
    """Frozen file contracts only, before an operator intent or any database write."""
    at = clock.now()
    if kind == "EVIDENCE":
        request = EvidenceImportRequestV1.model_validate(strict_return_json((root / entry).read_bytes()))
        if request.evidence.data_classification != classification or retention > request.evidence.retention_until_utc:
            raise ValueError("EVIDENCE_CLASSIFICATION_OR_RETENTION_MISMATCH")
        inside(root, request.evidence.source_file)
        verified_manual_bytes(root, request.evidence, clock)
    elif kind == "FIXTURE":
        raw = strict_return_json((root / entry).read_bytes())
        for item in raw.get("fixtures", ()):
            inside((root / entry).parent, item["source_artifact_path"])
        archive = load_reviewed_fixture_manual_archive(root / entry)
        if any(f.kickoff_at_utc <= at or f.reviewed_at_utc > at for f in archive.document.fixtures):
            raise ValueError("PREMATCH_INPUT_REQUIRED")
    elif kind == "SLATE":
        _check_document_paths(root, entry)
        slate = load_sporttery_daily_slate(root / entry)
        if any(c.kickoff_at_utc <= at for c in slate.candidates) or slate.provenance.reviewed_at_utc > at:
            raise ValueError("PREMATCH_INPUT_REQUIRED")
    elif kind == "SPORTTERY":
        _check_document_paths(root, entry)
        documents = load_verified_sporttery_manual_documents(root / entry)
        if any(d.document.schema_version != "SPORTTERY_MANUAL_ARCHIVE_V2" or d.document.reviewed_at_utc > at
               or any(r.kickoff_at_utc <= at for r in d.document.records) for d in documents):
            raise ValueError("PREMATCH_V2_SPORTTERY_REQUIRED")
    else:
        raise ValueError("INPUT_KIND_NOT_ALLOWED")


def apply_input(kind, root, entry, sessions, clock, classification, config_path, retention):
    """The caller owns path/hash/retention checks and an existing mode=rw database."""
    now = clock.now()
    identity = SqlAlchemyMatchIdentityRepository(sessions, clock=clock.now)
    if kind == "SLATE":
        # Validate relative source references before the frozen loader opens them.
        _check_document_paths(root, entry)
        slate = load_sporttery_daily_slate(root / entry)
        if any(c.kickoff_at_utc <= now for c in slate.candidates):
            raise ValueError("PREMATCH_INPUT_REQUIRED")
        catalog = identity.load_catalog(as_of_at_utc=now, kickoff_from_utc=datetime.min.replace(tzinfo=timezone.utc),
                                        kickoff_to_utc=datetime.max.replace(tzinfo=timezone.utc))
        plan = PlanSportteryDailySlateService().plan(slate, catalog, planned_at_utc=now)
        return dict(status="NO_ANALYSIS", artifact=plan, exact_kickoff_buckets=exact_buckets(plan))
    if kind == "FIXTURE":
        raw = strict_return_json((root / entry).read_bytes())
        for item in raw.get("fixtures", ()):
            inside((root / entry).parent, item["source_artifact_path"])
        archive = load_reviewed_fixture_manual_archive(root / entry)
        if any(f.kickoff_at_utc <= now or f.reviewed_at_utc > now for f in archive.document.fixtures):
            raise ValueError("PREMATCH_INPUT_REQUIRED")
        catalog = identity.load_catalog(as_of_at_utc=now, kickoff_from_utc=datetime.min.replace(tzinfo=timezone.utc),
                                        kickoff_to_utc=datetime.max.replace(tzinfo=timezone.utc))
        provider = ReviewedFixtureManualArchiveProvider(archive, catalog, RawDataArchive(root.parent / "c"))
        value = asyncio.run(LiveFixtureIngestionService(provider, lambda: identity, environment="live").ingest(reviewed_fixture_manual_request(archive)))
        return dict(status="INPUT_IMPORTED", artifact=value)
    if kind == "SPORTTERY":
        _check_document_paths(root, entry)
        documents = load_verified_sporttery_manual_documents(root / entry)
        if any(d.document.schema_version != "SPORTTERY_MANUAL_ARCHIVE_V2" or d.document.reviewed_at_utc > now
               or any(r.kickoff_at_utc <= now for r in d.document.records) for d in documents):
            raise ValueError("PREMATCH_V2_SPORTTERY_REQUIRED")
        catalog = identity.load_catalog(as_of_at_utc=now, kickoff_from_utc=datetime.min.replace(tzinfo=timezone.utc),
            kickoff_to_utc=datetime.max.replace(tzinfo=timezone.utc), provider_codes=("SPORTTERY_MANUAL",))
        settings = AppSettings.from_toml(config_path)
        provider = SportteryManualArchiveCaptureProvider(root / entry,
            catalog.build_resolver(timedelta(seconds=settings.runtime.kickoff_tolerance_seconds)), identity_cutoff_at_utc=now)
        repository = SqlAlchemyLiveSourceRepository(sessions, clock=clock.now)
        value = LiveSportteryIngestionService(provider, repository,
            environment="live", clock=clock.now).ingest()
        return dict(status="INPUT_IMPORTED" if value.status == "COMPLETED" else "INPUT_ISSUES", artifact=value,
            reconciliation=repository.reconciliation_report(ingestion_id=value.ingestion_id, generated_at_utc=now))
    if kind == "EVIDENCE":
        request = EvidenceImportRequestV1.model_validate(strict_return_json((root / entry).read_bytes()))
        claim = request.evidence
        if claim.data_classification != classification or retention > claim.retention_until_utc:
            raise ValueError("EVIDENCE_CLASSIFICATION_OR_RETENTION_MISMATCH")
        inside(root, claim.source_file)
        with sessions() as session:
            match = session.get(MatchRecord, claim.match_id)
            if match is None or match.kickoff_at_utc <= now:
                raise ValueError("CANONICAL_PREMATCH_REQUIRED")
        repository = SqlAlchemyProspectiveRepository(sessions, clock=clock)
        previous = repository.retry("EVIDENCE_IMPORT", request)
        value = previous if previous is not None else repository.import_evidence(request, verified_manual_bytes(root, claim, clock))
        return dict(status="INPUT_IMPORTED", artifact=value)
    raise ValueError("INPUT_KIND_NOT_ALLOWED")


def _check_document_paths(root, entry):
    import csv
    path = root / entry
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            documents = list(csv.DictReader(stream))
    else:
        documents = [strict_return_json(path.read_bytes())]
    for doc in documents:
        inside(path.parent, doc["source_artifact_path"])


def json_value(value):
    import json
    return json.loads(canonical_json(value))
