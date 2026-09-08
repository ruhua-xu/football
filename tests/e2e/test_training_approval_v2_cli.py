"""SYNTHETIC_CONTRACT_TEST_ONLY CLI approvals, never seeded approval rows.

BridgeDouble is the sole pilot contract substitute. All approval/release/live/
audit/review/fusion/portfolio operations use public APIs and real local files.
"""

import json

import pytest

from football_system.domain.production_release import (
    ProductionQuantModelReleaseV1,
    TrainingHistoryApprovalPayloadV2,
    TrainingHistoryApprovalV2,
)
from football_system.infrastructure.database import (
    production_quant_repository as persistence,
)
from football_system.infrastructure.files.production_audit_bundle import (
    read_production_audit_bundle,
)
from football_system.interfaces import production_quant_cli as cli
from football_system.interfaces.cli import main
from tests.e2e import test_production_quant_cli as previous
from tests.e2e import test_production_live_cli as live_tests
from tests.integration import test_production_inference as inference_tests
from tests.integration import test_production_audit as audit_tests
from tests.integration import test_training_approval_v2 as fixtures

lane = previous.lane
offline = previous.offline
production = fixtures.production


def wire_cli(context, monkeypatch):
    monkeypatch.setattr(cli, "utc_now", context.lane.clock)
    monkeypatch.setattr(audit_tests.review_bridge, "utc_now", context.lane.clock)
    monkeypatch.setattr(audit_tests.post_review, "utc_now", context.lane.clock)
    monkeypatch.setattr(
        cli,
        "SqlAlchemyQuantIntegrityRepository",
        lambda *args, **kwargs: context.bridge,
    )


def prepare_cli(context, capsys, *, values=None):
    result, response = previous.invoke(
        context.lane,
        "approval-prepare",
        values or fixtures.prepare_values(context),
        capsys,
    )
    assert result == 0, response
    assert response["status"] == "PREPARED"
    payload = TrainingHistoryApprovalPayloadV2.model_validate(response["result"])
    assert response["review_subject"] == {
        "schema_version": payload.payload_version,
        "payload_hash": payload.approval_payload_hash,
    }
    return payload


def record_request(context, payload):
    return cli.ApprovalRecordRequestV2(
        request_key="cli-v2-approval",
        approval_payload=payload,
        reviewer_attestation=fixtures.review_v2(context, payload),
    )


def test_prepare_is_readonly_and_record_keeps_exact_review_then_retry(
    production, monkeypatch, capsys
):
    wire_cli(production, monkeypatch)
    values = fixtures.prepare_values(production)
    database = production.lane.root / "lane.db"
    before = database.read_bytes()
    counts = fixtures.legacy.counts(production)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            cli, "upgrade_database", lambda *args: pytest.fail("prepare migrated")
        )
        payload = prepare_cli(production, capsys, values=values)
        assert prepare_cli(production, capsys, values=values) == payload
    assert database.read_bytes() == before
    assert fixtures.legacy.counts(production) == counts
    request = record_request(production, payload)
    result, response = previous.invoke(
        production.lane, "approval-record", request, capsys
    )
    assert result == 0, response
    assert response["status"] == "RECORDED"
    approval = TrainingHistoryApprovalV2.model_validate(response["result"])
    assert approval.content_payload.reviewer_attestation == request.reviewer_attestation
    assert approval.subject == payload
    assert production.repo.load_approval(approval.artifact_id) == approval
    assert previous.invoke(production.lane, "approval-record", request, capsys) == (
        result,
        response,
    )
    assert fixtures.legacy.counts(production)["training_history_approval_events"] == 1


def test_prepare_requires_existing_database_and_authorized_context_without_migration(
    production, monkeypatch, capsys
):
    wire_cli(production, monkeypatch)
    values = fixtures.prepare_values(production)
    monkeypatch.setattr(
        cli, "upgrade_database", lambda *args: pytest.fail("prepare migrated")
    )
    result, error = previous.invoke(
        production.lane, "approval-prepare", values, capsys, database="absent.db"
    )
    assert result == 1 and error["code"] == "DATABASE_NOT_READY"
    assert not (production.lane.root / "absent.db").exists()
    before = (production.lane.root / "lane.db").read_bytes()
    values["reviewer_authority"] = production.authority
    result, error = previous.invoke(production.lane, "approval-prepare", values, capsys)
    assert result == 1 and error["code"] == "OPERATION_REJECTED"
    assert (production.lane.root / "lane.db").read_bytes() == before


def test_postcommit_output_failure_reports_persistence_and_exact_retry_does_not_duplicate(
    production, monkeypatch, capsys
):
    wire_cli(production, monkeypatch)
    request = record_request(production, prepare_cli(production, capsys))

    def output_failed(*args):
        raise OSError(previous.SECRET)

    with monkeypatch.context() as scoped:
        scoped.setattr(cli, "write_local_json", output_failed)
        result, error = previous.invoke(
            production.lane,
            "approval-record",
            request,
            capsys,
            extra=("--output", str(production.lane.root / "failed-output.json")),
        )
    assert result == 1 and error["code"] == "OUTPUT_NOT_WRITTEN"
    assert "may already be persisted" in error["message"]
    assert "exact same request key" in error["message"]
    before = fixtures.legacy.counts(production)
    assert before["training_history_approval_events"] == 1
    result, response = previous.invoke(
        production.lane, "approval-record", request, capsys
    )
    assert result == 0, response
    assert fixtures.legacy.counts(production) == before
    changed = request.model_copy(
        update={
            "reviewer_attestation": fixtures.review_v2(
                production, request.approval_payload
            )
        }
    )
    result, error = previous.invoke(production.lane, "approval-record", changed, capsys)
    assert result == 1 and error["code"] == "OPERATION_REJECTED"
    assert fixtures.legacy.counts(production) == before


@pytest.mark.parametrize(
    "bad",
    [
        "clock-field",
        "time-option",
        "old-schema",
        "old-hash",
        "no-authority",
        "no-review",
        "no-raw",
        "code-drift",
    ],
)
def test_invalid_v2_requests_and_context_fail_closed(
    production, monkeypatch, capsys, bad
):
    wire_cli(production, monkeypatch)
    request = record_request(production, prepare_cli(production, capsys))
    document = request.model_dump(mode="json")
    extra = ()
    if bad == "clock-field":
        document["persisted_at_utc"] = "2026-01-01T00:00:00Z"
    elif bad == "time-option":
        extra = ("--recorded-at-utc", previous.SECRET)
    elif bad == "old-schema":
        document["approval_payload"]["payload_version"] = (
            "TRAINING_HISTORY_APPROVAL_PAYLOAD_V1"
        )
    elif bad == "old-hash":
        document["reviewer_attestation"]["content_payload"]["attested_payload_hash"] = (
            "0" * 64
        )
    elif bad == "no-authority":
        production.lane.repo.evidence.trusted_authorities.pop(
            request.approval_payload.authority_reference
        )
    elif bad == "no-review":
        (
            production.lane.root
            / request.reviewer_attestation.content_payload.evidence.evidence_reference
        ).unlink()
    elif bad == "no-raw":
        (production.lane.root / "result.json").unlink()
    else:
        monkeypatch.setattr(persistence, "_code_revision", lambda: "changed-package")
    before = fixtures.legacy.counts(production)
    result, error = previous.invoke(
        production.lane, "approval-record", document, capsys, extra=extra
    )
    assert result != 0
    assert error["code"] == (
        "APPROVAL_RECORDING_CONTRACT_CONFLICT"
        if bad == "old-schema"
        else "OPERATION_REJECTED"
        if bad in {"code-drift", "no-raw"}
        else "INVALID_LOCAL_INPUT"
    )
    assert fixtures.legacy.counts(production) == before


def test_cli_prepare_record_release_live_audit_review_fusion_portfolio(
    lane, monkeypatch, capsys
):
    recorded = []

    def public_cli_build(context):
        wire_cli(context, monkeypatch)
        payload = prepare_cli(context, capsys)
        request = record_request(context, payload)
        result, response = previous.invoke(
            context.lane, "approval-record", request, capsys
        )
        assert result == 0, response
        approval = TrainingHistoryApprovalV2.model_validate(response["result"])
        recorded.append(approval)
        result, response = previous.invoke(
            context.lane,
            "release-build",
            cli.ReleaseBuildRequestV1(
                request_key="cli-v2-release",
                approval_id=approval.artifact_id,
                training_cutoff_at_utc=context.lane.clock(),
                state_retention_horizon=fixtures.legacy.horizon(),
                audit_retention_horizon=fixtures.legacy.horizon(),
            ),
            capsys,
        )
        assert result == 0, response
        return ProductionQuantModelReleaseV1.model_validate(response["result"])

    with monkeypatch.context() as scoped:
        scoped.setattr(fixtures.legacy, "build", public_cli_build)
        context = inference_tests.inference.__wrapped__(lane, monkeypatch)
    assert context.release.training_approval == recorded[0]
    context = live_tests.live_cli.__wrapped__(context, monkeypatch)
    assert main(live_tests.arguments(context)) == 0
    captured = capsys.readouterr()
    assert "APPROVED_TRAINING_HISTORY" in captured.out
    assert previous.SECRET not in captured.out + captured.err
    bundle_dir = lane.root / "v2-audit-bundle"
    result, response = previous.invoke(
        lane,
        "bundle-export",
        cli.BundleExportRequestV1(
            analysis_run_id="production-cli-analysis",
            bundle_directory=bundle_dir,
        ),
        capsys,
    )
    assert result == 0, response
    bundle = read_production_audit_bundle(bundle_dir)
    assert bundle.audit.content_payload.approval == recorded[0].reference()
    review_file = previous.write_request(
        lane.root, audit_tests.review_bytes(bundle.packet), "v2-llm-review.json"
    )
    result, response = previous.invoke(
        lane,
        "review-import",
        cli.ReviewImportRequestV1(
            bundle_directory=bundle_dir,
            review_file=review_file,
        ),
        capsys,
    )
    assert result == 0, response
    review_id = response["result"]["review_artifact_id"]
    config = previous.write_request(
        lane.root, b'[runtime]\nenvironment = "live"\n', "v2-live.toml"
    )
    result, response = previous.invoke(
        lane,
        "fusion-create",
        cli.FusionCreateRequestV1(
            review_artifact_id=review_id,
            config_file=config,
        ),
        capsys,
    )
    assert result == 0, response
    result, response = previous.invoke(
        lane,
        "portfolio-revise",
        cli.PortfolioReviseRequestV1(
            fusion_run_id=response["result"]["fusion_run_id"],
            config_file=config,
        ),
        capsys,
    )
    assert result == 0, response
    assert response["result"]["portfolio_revision_id"]
    assert inference_tests.model_counts(context)["analysis_runs"] == 1
    assert (
        fixtures.legacy.counts(context.production)["training_history_approval_events"]
        == 1
    )


def test_v2_schema_discovery_never_opens_files_or_database(monkeypatch, capsys):
    previous.no_database_calls(monkeypatch)
    monkeypatch.setattr(
        cli, "_read_json", lambda *args: pytest.fail("schema read files")
    )
    for name in (
        "TrainingHistoryApprovalPayloadV2",
        "TrainingHistoryApprovalV2",
        "ApprovalPrepareRequestV2",
        "ApprovalRecordRequestV2",
    ):
        assert main(["production-quant", "--print-schema", "--type", name]) == 0
        assert (
            json.loads(capsys.readouterr().out)
            == cli.SCHEMA_TYPES[name].model_json_schema()
        )
