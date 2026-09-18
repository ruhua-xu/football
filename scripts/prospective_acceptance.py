"""Installed-wheel synthetic lifecycle using already sealed v0.9 source artifacts.

No training, inference, tests imports, external provider or LLM requests. The clock
injection is a synthetic scaffold seam, never a public command-line time override.
"""

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from football_system.application.market_v2 import MarketExpansionService
from football_system.application.prospective import ProspectiveService
from football_system.application.prospective_requests import (
    EpochRequestV1, EvidenceImportRequestV1, LockWorkflowRequestV1, PrepareProspectiveRequestV1,
    ReportProspectiveRequestV1, ResultImportRequestV1, SettleProspectiveRequestV1,
)
from football_system.application.return_distribution import ReturnDistributionService
from football_system.domain.archive import canonical_json
from football_system.domain.prospective import ManualResultImportV1
from football_system.domain.prospective_evidence import ManualVerifiedImportV1
from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock, verified_manual_bytes, verify_prospective_configuration
from football_system.infrastructure.files.return_distribution import verify_resource_configuration
from football_system.interfaces.cli import main as cli, _resource_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-work-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    source, work = args.source_work_dir.resolve(), args.work_dir.resolve()
    if not (source / "acceptance.db").is_file():
        raise ValueError("existing synthetic multi-market source required")
    work.mkdir(exist_ok=False)
    database = work / "acceptance.db"
    with sqlite3.connect((source / "acceptance.db").as_uri()+"?mode=ro", uri=True) as old, sqlite3.connect(database) as new:
        old.backup(new)
    url = "sqlite:///"+database.as_posix()
    engine = create_database_engine(url)
    try:
        sessions = create_session_factory(engine)
        market = MarketExpansionService(SqlAlchemyMultiMarketRepository(sessions))
        plan_id = json.loads((source / "plan-2X1.json").read_bytes())["artifact_id"]
        plan = market.repository.load(plan_id)
        analysis = plan.source.analysis
        at = max(u.decision_cutoff for u in analysis.units)+timedelta(minutes=1)
        clock = SyntheticProspectiveClock(at)
        repo = SqlAlchemyProspectiveRepository(sessions, clock=clock, policy=verify_prospective_configuration(_resource_root()))
        policy, objective = verify_resource_configuration(_resource_root())
        service = ProspectiveService(repo, market, ReturnDistributionService(SqlAlchemyReturnDistributionRepository(sessions), policy, objective),
            verify_manual=lambda claim: verified_manual_bytes(work, claim, clock))
        epoch = service.create_epoch(EpochRequestV1(request_key="wheel-epoch", seed_plan_id=plan_id, name="wheel-synthetic",
            mode="SYNTHETIC", starts_at_utc=at, ends_at_utc=at+timedelta(days=30), result_source_identity="wheel-manual"))
        first = analysis.units[0].identity
        unknown = dict(category="LINEUP", team_id=first.home_team_id, status="UNKNOWN")
        evidence = canonical_json(unknown).encode()
        (work / "manual-lineup.json").write_bytes(evidence)
        binding = service.import_evidence(EvidenceImportRequestV1(request_key="wheel-evidence", evidence=ManualVerifiedImportV1(
            match_id=first.match_id, data_classification="SYNTHETIC", source_identity="wheel-manual", source_reference="self-authored fixture",
            source_file="manual-lineup.json", source_hash=hashlib.sha256(evidence).hexdigest(), rights_basis="SELF_OBSERVED",
            rights_reference="self-authored synthetic", retention_until_utc=at+timedelta(days=60), verified_by="wheel-scaffold",
            verified_at_utc=at, captured_at_utc=at, available_at_utc=at, published_at_utc=None,
            fact_category="LINEUP", assertion_class="FACT", confidence="0", structured_payload=unknown)))
        snapshot = repo.load(binding.snapshot.artifact_id)
        assert snapshot.freshness_status == "UNKNOWN" and snapshot.claim.structured_payload.status == "UNKNOWN"
        run = service.prepare(PrepareProspectiveRequestV1(request_key="wheel-prepare", epoch_id=epoch.artifact_id,
            analysis_id=analysis.artifact_id, slate_key="wheel-slate", slate_date=min(u.identity.kickoff_at_utc for u in analysis.units).date(),
            budget_fen=plan.source.budget_fen))
        packet, markdown = service.packet_files(run)
        (work / "analysis_packet.json").write_text(packet, encoding="utf-8")
        (work / "analysis_packet.md").write_text(markdown, encoding="utf-8")
        lock_request = LockWorkflowRequestV1(request_key="wheel-lock", run_id=run.artifact_id, strategy_requests=plan.requests)
        decision = service.lock(lock_request)
        assert service.lock(lock_request) == decision
        clock.at = max(i.kickoff_at_utc for i in run.identities)+timedelta(days=1)
        for index, identity in enumerate(run.identities):
            now = clock.now()
            raw = canonical_json(dict(match_id=identity.match_id, home_goals=2, away_goals=1, classification="SYNTHETIC_ONLY")).encode()
            file = f"result-{index}.json"
            (work / file).write_bytes(raw)
            service.import_result(ResultImportRequestV1(request_key=f"wheel-result-{index}", result=ManualResultImportV1(
                match_id=identity.match_id, data_classification="SYNTHETIC", source_identity="wheel-manual", source_reference="self-authored result",
                source_file=file, source_hash=hashlib.sha256(raw).hexdigest(), verified_by="wheel-scaffold", verified_at_utc=now,
                observed_at_utc=now, available_at_utc=now, status="FT", regular_time_semantics="REGULATION_ONLY", home_goals=2, away_goals=1,
                rights_reference="self-authored synthetic", retention_until_utc=now+timedelta(days=60))))
        settled = service.settle(SettleProspectiveRequestV1(request_key="wheel-settle", run_id=run.artifact_id))
        request = ReportProspectiveRequestV1(request_key="wheel-report", epoch_id=epoch.artifact_id, as_of_at_utc=clock.now())
        report = service.report(request)
        assert report == service.report(request) and report.real_run_count == 0
        assert report.performance_evidence_status == "INSUFFICIENT_PROSPECTIVE_SAMPLE"
        assert settled.reason == "SETTLED" and report.synthetic_run_count == 1
        for artifact in (epoch, run, decision, settled, report):
            assert repo.load(artifact.artifact_id) == artifact
        for command in ("show", "audit"):
            output = work / (command+".json")
            assert cli(["prospective", command, "--database-url", url, "--artifact-id", report.artifact_id, "--output", str(output)]) == 0
        request_file = work / "cli-report-request.json"
        request_file.write_text(canonical_json(ReportProspectiveRequestV1(**{**request.model_dump(), "request_key": "wheel-cli-report"})), encoding="utf-8")
        assert cli(["prospective", "report", "--database-url", url, "--input", str(request_file), "--output", str(work / "cli-report.json")]) == 0
        assert cli(["prospective", "capability", "--provider", "SPORTMONKS", "--category", "LINEUP"]) == 0
        fixture = json.loads((_resource_root() / "data/fixtures/prospective_validation_v1.json").read_bytes())
        assert fixture["classification"] == "SYNTHETIC_ONLY" and set(fixture["cases"]) == set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        summary = dict(classification="SYNTHETIC_ONLY", provider_http_sends=0, real_performance_claim=False,
            run_id=run.artifact_id, lock_id=decision.artifact_id, settlement_id=settled.artifact_id, report_id=report.artifact_id,
            migration_head="5b748fa162ed", performance_evidence_status=report.performance_evidence_status)
        (work / "acceptance-summary.json").write_text(canonical_json(summary), encoding="utf-8")
        print("PROSPECTIVE_VALIDATION_V1_INSTALLED_ACCEPTANCE_PASS")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
