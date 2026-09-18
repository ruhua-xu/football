"""Prospective A-Z acceptance on isolated, clearly synthetic frozen-v0.9 graphs."""

from datetime import timedelta
import hashlib
import json
import sqlite3

import pytest
from sqlalchemy import text

from football_system.application.market_v2 import BuildMultiAnalysisRequestV1, FusionRequestV4, MarketExpansionService, PacketRequestV4, PlanRequestV2
from football_system.application.prospective import ProspectiveService
from football_system.application.prospective_requests import (
    EpochRequestV1, LockWorkflowRequestV1, PrepareProspectiveRequestV1, ReportProspectiveRequestV1,
    ResultImportRequestV1, SettleProspectiveRequestV1,
)
from football_system.application.return_distribution import ReturnDistributionService
from football_system.domain.prospective import ManualResultImportV1
from football_system.domain.archive import canonical_json
from football_system.domain.strategy_pass_v2 import TicketRequestV2
from football_system.infrastructure.database.market_v2_repository import SqlAlchemyMultiMarketRepository
from football_system.infrastructure.database.prospective_repository import SqlAlchemyProspectiveRepository
from football_system.infrastructure.database.return_distribution_repository import SqlAlchemyReturnDistributionRepository
from football_system.infrastructure.database.session import create_database_engine, create_session_factory
from football_system.infrastructure.files.prospective import SyntheticProspectiveClock, verified_manual_bytes
from football_system.infrastructure.files.return_distribution import default_return_configuration
from scripts.market_expansion_acceptance import DECISION, KICKOFF
from tests.integration.test_market_v2_repository import prepared  # noqa: F401


@pytest.fixture(scope="module")
def prospective_base(prepared):  # noqa: F811
    engine, _, market, old, *_ = prepared
    analysis = market.build_analysis(BuildMultiAnalysisRequestV1(
        unit_ids=tuple(u.artifact_id for u in old.units if u.identity.match_id in {"mm-target-0", "mm-target-1"}
                       and u.market_key.market_type in {"THREE_WAY", "TOTAL_GOALS"}),
        budgets_fen=old.budgets_fen, rules=old.rules, constraints=old.constraints,
        min_selection_ev=old.min_selection_ev, min_ticket_roi=old.min_ticket_roi))
    packet = market.packet(PacketRequestV4(analysis_id=analysis.artifact_id))
    raw = canonical_json(dict(schema_version="LLM_REVIEW_V4", analysis_id=analysis.artifact_id,
        packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=[dict(
            match_id=u.review_context.identity.match_id, market_key=u.review_context.market_key,
            review_context_id=u.review_context_id, review_context_hash=u.review_context_hash,
            status="UNAVAILABLE", failure_code="SKIPPED_DISABLED", limitations=["Synthetic fixture only"])
            for u in packet.market_units])).encode()
    review = market.review_import(canonical_json(packet).encode(), raw)
    fusion = market.fusion(FusionRequestV4(analysis_id=analysis.artifact_id, review_id=review.artifact_id))
    seed = market.plan(PlanRequestV2(fusion_id=fusion.artifact_id, budget_fen=10000, requests=home_request()))
    return engine, analysis, seed


def home_request():
    return (TicketRequestV2(pass_type="2X1", choices=tuple(dict(match_id="mm-target-"+str(i),
        market_key={"market_type": "THREE_WAY"}, outcomes=("HOME_WIN",)) for i in (0, 1))),)


@pytest.fixture
def prospective(prospective_base, tmp_path):
    source, analysis, seed = prospective_base
    path = tmp_path / "prospective.db"
    raw = source.raw_connection()
    target = sqlite3.connect(path)
    raw.driver_connection.backup(target)
    target.close()
    raw.close()
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    sessions = create_session_factory(engine)
    market = MarketExpansionService(SqlAlchemyMultiMarketRepository(sessions))
    clock = SyntheticProspectiveClock(DECISION+timedelta(minutes=1))
    repo = SqlAlchemyProspectiveRepository(sessions, clock=clock)
    policy, objective = default_return_configuration()
    service = ProspectiveService(repo, market, ReturnDistributionService(SqlAlchemyReturnDistributionRepository(sessions), policy, objective),
        verify_manual=lambda c: verified_manual_bytes(tmp_path, c, clock))
    epoch_request = EpochRequestV1(request_key="epoch", seed_plan_id=seed.artifact_id, name="synthetic-future-epoch",
        mode="SYNTHETIC", starts_at_utc=clock.now(), ends_at_utc=KICKOFF+timedelta(days=10), result_source_identity="synthetic-manual")
    epoch = service.create_epoch(epoch_request)
    yield dict(engine=engine, sessions=sessions, market=market, clock=clock, repo=repo, service=service,
               epoch=epoch, epoch_request=epoch_request, analysis=analysis, root=tmp_path, path=path)
    engine.dispose()


def prepare(p, key="prepare", **values):
    return p["service"].prepare(PrepareProspectiveRequestV1(request_key=key, epoch_id=p["epoch"].artifact_id,
        analysis_id=p["analysis"].artifact_id, slate_key="slate", slate_date=KICKOFF.date(), budget_fen=10000, **values))


def lock(p, run, key="lock", **values):
    return p["service"].lock(LockWorkflowRequestV1(request_key=key, run_id=run.artifact_id,
        strategy_requests=home_request(), **values))


def result(p, match, *, key=None, previous=None, score=(2, 1), status="FT"):
    now = p["clock"].now()
    key = key or "result-"+match
    data = json.dumps(dict(match=match, score=score, status=status, revision=key)).encode()
    file = key+".json"
    (p["root"] / file).write_bytes(data)
    claim = ManualResultImportV1(match_id=match, data_classification="SYNTHETIC", source_identity="synthetic-manual",
        source_reference="self-authored-synthetic-result", source_file=file, source_hash=hashlib.sha256(data).hexdigest(),
        verified_by="synthetic-test", verified_at_utc=now, observed_at_utc=now, available_at_utc=now,
        published_at_utc=now, source_version_id=key, previous_source_version_id=previous.claim.source_version_id if previous else None,
        status=status, regular_time_semantics="REGULATION_ONLY" if status == "FT" else "UNPROVEN",
        home_goals=score[0] if status == "FT" else None, away_goals=score[1] if status == "FT" else None,
        supersedes_observation_id=previous.artifact_id if previous else None, rights_reference="self-authored fixture",
        retention_until_utc=now+timedelta(days=30))
    return p["service"].import_result(ResultImportRequestV1(request_key=key, result=claim))


def settle_all(p, run):
    p["clock"].at = KICKOFF+timedelta(days=1)
    results = tuple(result(p, i.match_id) for i in run.identities)
    settlement = p["service"].settle(SettleProspectiveRequestV1(request_key="settle", run_id=run.artifact_id))
    return settlement, results


def report(p, key="report", at=None):
    return p["service"].report(ReportProspectiveRequestV1(request_key=key, epoch_id=p["epoch"].artifact_id,
        as_of_at_utc=at or p["clock"].now()))


def test_a_p_r_s_u_v_full_lock_settle_report_retry(prospective):
    p = prospective
    empty = report(p, "empty-before-prepare")
    run = prepare(p)
    decision = lock(p, run)
    assert decision.locked_at_utc < decision.earliest_kickoff_at_utc
    assert decision.stake_fen == 0  # Frozen marginal utility legitimately chooses NO_BET.
    assert lock(p, run) == decision
    assert prepare(p) == run
    settlement, _ = settle_all(p, run)
    assert settlement.reason == "SETTLED"
    assert settlement.ending_capital_fen == settlement.cash_fen+settlement.gross_payout_fen
    assert set(settlement.realized_buckets) == {"p_gross_payout_gt_zero", "p_break_even_or_better", "p_2x_budget", "p_3x_budget", "p_loss", "p_deep_loss"}
    validation = report(p)
    assert validation == report(p)
    assert validation.performance_evidence_status == "INSUFFICIENT_PROSPECTIVE_SAMPLE"
    assert validation.real_run_count == 0 and validation.synthetic_run_count == 1
    assert len(validation.probability_quality) == 2
    assert validation.correction_performance["counts"]["abstained"] == 4
    for artifact in (run, decision, settlement, validation):
        assert p["repo"].load(artifact.artifact_id) == artifact
        assert p["repo"].audit(artifact.artifact_id)["status"] == "AUDIT_PASS"
    assert p["repo"].load(empty.artifact_id) == empty  # Same UTC instant, later receipt sequence.
    with p["engine"].connect() as conn:
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()


def test_t_empty_report(prospective):
    validation = report(prospective)
    assert validation.census == () and validation.probability_quality == ()
    assert validation.decision_value["yield_on_settled_stake"] is None
    assert validation.performance_evidence_status == "INSUFFICIENT_PROSPECTIVE_SAMPLE"


def test_b_late_lock_and_clock_regression_fail_without_receipt(prospective):
    p = prospective
    run = prepare(p)
    p["clock"].at = KICKOFF
    with pytest.raises(ValueError, match="LOOKAHEAD_RISK"):
        lock(p, run)
    late = p["service"].prepare(PrepareProspectiveRequestV1(request_key="late", epoch_id=p["epoch"].artifact_id,
        analysis_id=p["analysis"].artifact_id, slate_key="late", slate_date=KICKOFF.date(), budget_fen=10000))
    assert late.status == "UNAVAILABLE" and late.reason == "LOOKAHEAD_RISK"
    p["clock"].at = DECISION
    with pytest.raises(ValueError, match="TRUSTED_CLOCK_REGRESSION"):
        report(p)
    with p["sessions"]() as session:
        assert session.scalar(text("SELECT count(*) FROM pv_locks")) == 0
        assert session.scalar(text("SELECT count(*) FROM pv_receipts WHERE request_key='lock'")) == 0


def test_d_e_pre_kickoff_supersession_and_result_revisions(prospective):
    p = prospective
    old = prepare(p)
    old_lock = lock(p, old)
    p["clock"].at += timedelta(minutes=1)
    new = prepare(p, "replacement", supersedes_run_id=old.artifact_id)
    replacement = lock(p, new, "replacement-lock", invalidation_reason="Explicit synthetic pre-kickoff source correction")
    assert p["repo"].show(old.artifact_id)["current"].status == "INVALIDATED_PRE_KICKOFF"
    assert p["repo"].show(new.artifact_id)["current"].status == "LOCKED"
    with pytest.raises(ValueError, match="RUN_NOT_ACTIVE_LOCKED"):
        p["service"].settle(SettleProspectiveRequestV1(request_key="invalidated-settle", run_id=old.artifact_id))
    settlement, observations = settle_all(p, new)
    old_report = report(p)
    p["clock"].at += timedelta(seconds=1)
    revision = result(p, observations[0].claim.match_id, key="revision", previous=observations[0], score=(0, 2))
    assert revision.previous.artifact_id == observations[0].artifact_id
    assert revision.normalized_result.supersedes_match_result_id == observations[0].normalized_result.match_result_id
    assert report(p, "stale").coverage == {"INVALIDATED_PRE_KICKOFF": 1, "STALE_SETTLEMENT": 1}
    corrected = p["service"].settle(SettleProspectiveRequestV1(request_key="resettle", run_id=new.artifact_id))
    assert corrected.previous.artifact_id == settlement.artifact_id
    assert report(p, "corrected").coverage == {"INVALIDATED_PRE_KICKOFF": 1, "SETTLED": 1}
    for value in (old_lock, replacement, settlement, observations[0], old_report):
        assert p["repo"].load(value.artifact_id) == value
    with pytest.raises(ValueError, match="LOOKAHEAD_RISK"):
        prepare(p, "illegal-after-kickoff", supersedes_run_id=new.artifact_id)
    with pytest.raises(ValueError, match="RESULT_REVISION_MUST_EXTEND_CURRENT_HEAD"):
        result(p, observations[0].claim.match_id, key="fork", previous=observations[0])


def test_missing_unsupported_and_void_revision_are_not_losses(prospective):
    p = prospective
    run = prepare(p)
    lock(p, run)
    p["clock"].at = KICKOFF+timedelta(days=1)
    missing = p["service"].settle(SettleProspectiveRequestV1(request_key="missing", run_id=run.artifact_id))
    assert missing.reason == "MISSING_RESULT" and missing.gross_payout_fen is None
    first = result(p, run.identities[0].match_id, status="VOID")
    result(p, run.identities[1].match_id)
    void = p["service"].settle(SettleProspectiveRequestV1(request_key="void", run_id=run.artifact_id))
    assert void.reason == "UNSUPPORTED_SETTLEMENT_CASE" and void.profit_loss_fen is None
    p["clock"].at += timedelta(seconds=1)
    result(p, first.claim.match_id, key="void-corrected", previous=first)
    fixed = p["service"].settle(SettleProspectiveRequestV1(request_key="fixed", run_id=run.artifact_id))
    assert fixed.reason == "SETTLED"
    assert p["repo"].load(void.artifact_id) == void


def test_c_w_sql_mutation_partial_graph_and_resealed_report_corruption(prospective):
    from sqlalchemy.exc import IntegrityError
    from football_system.domain.prospective import ProspectiveValidationReportV1
    from football_system.infrastructure.database.models import Base
    from football_system.infrastructure.database.prospective_schema import PROSPECTIVE_TABLES, prospective_triggers
    p = prospective
    run = prepare(p)
    lock(p, run)
    settle_all(p, run)
    validation = report(p)
    for name in PROSPECTIVE_TABLES:
        table = Base.metadata.tables[name]
        with p["sessions"]() as session:
            row = session.execute(table.select()).mappings().first()
        if row:
            row = dict(row)
            for statement in (table.update().values({next(iter(row)): next(iter(row.values()))}),
                              table.delete(), table.insert().prefix_with("OR REPLACE").values(row)):
                with pytest.raises(IntegrityError):
                    with p["sessions"].begin() as session:
                        session.execute(statement)
    fields = validation.model_dump(exclude={"artifact_id", "content_hash"})
    fields["decision_value"]["net_profit_loss_fen"] += 1
    wrong = ProspectiveValidationReportV1.freeze(**fields)
    # Privileged offline corruption is outside the write API. Even when every
    # projection and hash is resealed consistently, source replay rejects it.
    raw = sqlite3.connect(p["path"])
    try:
        for name in prospective_triggers():
            raw.execute(f"DROP TRIGGER {name}")
        old_id, new_id = validation.artifact_id, wrong.artifact_id
        raw.execute("UPDATE pv_artifacts SET artifact_id=?,content_hash=?,artifact_json=? WHERE artifact_id=?",
                    (new_id, wrong.content_hash, canonical_json(wrong), old_id))
        for table, field in (("pv_reports", "artifact_id"), ("pv_report_census", "parent_id"),
                             ("pv_seals", "artifact_id"), ("pv_receipts", "response_id")):
            raw.execute(f"UPDATE {table} SET {field}=? WHERE {field}=?", (new_id, old_id))
        raw.commit()
    finally:
        raw.close()
    with pytest.raises(ValueError, match="VALIDATION_CENSUS_OR_METRIC_REPLAY_MISMATCH"):
        p["repo"].load(wrong.artifact_id)


def rebuild_analysis(p, *, positive_prices=False, evidence=None):
    from football_system.application.market_v2 import BuildMarketUnitRequestV1, ConsensusRequestV2
    from football_system.infrastructure.providers.exact_market_fixture import parse_exact_market_fixture
    from scripts.market_expansion_acceptance import fixture_book
    p["market"].adapter = parse_exact_market_fixture
    ids = []
    for unit in p["analysis"].units:
        sp_id = unit.sporttery.artifact_id
        if positive_prices and unit.market_key.market_type == "THREE_WAY":
            payload = json.loads(fixture_book(unit.identity, unit.market_key, prices={"HOME_WIN": "10"}, book="SYNTHETIC_PROSPECTIVE_POSITIVE"))
            sp_id = p["market"].snapshot_import(canonical_json(payload).encode())["snapshot_id"]
        dynamic = (evidence.football_evidence.artifact_id,) if evidence and evidence.match_id == unit.identity.match_id and unit.legacy_source is None else ()
        consensus = unit.consensus
        cutoff = unit.decision_cutoff
        if dynamic:
            cutoff = p["clock"].now()
            consensus = p["market"].consensus(ConsensusRequestV2(match_id=unit.identity.match_id, market_key=unit.market_key,
                snapshot_ids=tuple(s.artifact_id for s in consensus.snapshots), decision_cutoff=cutoff))
        rebuilt = p["market"].build_unit(BuildMarketUnitRequestV1(identity=unit.identity, market_key=unit.market_key,
            decision_cutoff=cutoff, sporttery_id=sp_id, consensus_id=consensus.artifact_id if consensus else None,
            goal_state_id=unit.goal_state.artifact_id if unit.goal_state else None,
            legacy_source_id=unit.legacy_source.artifact_id if unit.legacy_source else None,
            evidence_ids=dynamic, base_policy=unit.base_policy, quant_weight=unit.quant_weight, data_quality=unit.data_quality))
        ids.append(rebuilt.artifact_id)
    old = p["analysis"]
    p["analysis"] = p["market"].build_analysis(BuildMultiAnalysisRequestV1(unit_ids=tuple(ids), budgets_fen=old.budgets_fen,
        rules=old.rules, constraints=old.constraints, min_selection_ev=old.min_selection_ev, min_ticket_roi=old.min_ticket_roi))


def test_q_positive_stake_exact_frozen_settlement_golden(prospective):
    from decimal import Decimal
    from football_system.domain.services.settlement_v2 import settle_strategy_v2
    from football_system.domain.services.return_distribution import evaluate_portfolio
    from football_system.domain.return_distribution import ReturnAllocationRequestV1
    p = prospective
    rebuild_analysis(p, positive_prices=True)
    run = prepare(p)
    decision = lock(p, run)
    assert decision.stake_fen > 0
    settled, observations = settle_all(p, run)
    plan = p["market"].repository.load(decision.strategy_plan.artifact_id)
    # V2's allocator may select other multipliers. Its frozen per-ticket payouts
    # are reconciled to the locked optimizer allocation via a separate oracle.
    old_settlement = settle_strategy_v2(plan, tuple(o.normalized_result for o in observations), p["clock"].now())
    assert old_settlement is not None
    evaluation = evaluate_portfolio(plan, tuple(ReturnAllocationRequestV1(ticket_candidate_id=s.ticket_candidate_id,
        multiplier=s.multiplier) for s in decision.selected), policy=p["service"].returns.policy, objective=p["service"].returns.objective)
    assert settled.gross_payout_fen in {point.gross_payout_fen for point in evaluation.distribution.support}
    assert settled.gross_payout_fen == sum(s.multiplier*sum(a.gross_payout_fen for a in next(c for c in plan.candidates if c.artifact_id == s.ticket_candidate_id).atomic_bets) for s in decision.selected)
    validation = report(p)
    assert Decimal(validation.decision_value["average_quoted_sp"]) == 10
    assert validation.decision_value["settled_stake_fen"] == decision.stake_fen


def test_request_reuse_clock_kind_and_epoch_closure(prospective):
    from football_system.application.prospective_requests import CloseEpochRequestV1
    p = prospective
    prepare(p)
    with pytest.raises(ValueError, match="REQUEST_KEY_REUSED"):
        p["service"].prepare(PrepareProspectiveRequestV1(request_key="prepare", epoch_id=p["epoch"].artifact_id,
            analysis_id=p["analysis"].artifact_id, slate_key="changed", slate_date=KICKOFF.date(), budget_fen=0))
    with pytest.raises(ValueError, match="TEST_CLOCK_CANNOT_OPEN_REAL_EPOCH"):
        p["service"].create_epoch(EpochRequestV1(**{**p["epoch_request"].model_dump(), "request_key": "real", "mode": "REAL_PROSPECTIVE"}))
    with pytest.raises(ValueError, match="EPOCH_CANNOT_CLOSE_BEFORE_END"):
        p["service"].close_epoch(CloseEpochRequestV1(request_key="early-close", epoch_id=p["epoch"].artifact_id))
    p["clock"].at = p["epoch"].ends_at_utc
    closed = p["service"].close_epoch(CloseEpochRequestV1(request_key="close", epoch_id=p["epoch"].artifact_id))
    next_epoch = p["service"].create_epoch(EpochRequestV1(**{**p["epoch_request"].model_dump(), "request_key": "next",
        "name": "next", "starts_at_utc": p["clock"].now(), "ends_at_utc": p["clock"].now()+timedelta(days=10), "previous_epoch_id": p["epoch"].artifact_id}))
    assert next_epoch.previous_epoch.artifact_id == p["epoch"].artifact_id
    assert p["repo"].load(closed.artifact_id) == closed


def manual_evidence(p, *, category="OTHER_VERIFIED_FACT", payload=None, published=None, key="evidence"):
    from football_system.application.prospective_requests import EvidenceImportRequestV1
    from football_system.domain.prospective_evidence import ManualVerifiedImportV1
    at = p["clock"].now()
    payload = payload or dict(category=category, statement="Self-authored synthetic fact", supporting_references=["synthetic-fixture"], inference_basis="DIRECT_EVIDENCE")
    raw = canonical_json(payload).encode()
    (p["root"] / (key+".json")).write_bytes(raw)
    claim = ManualVerifiedImportV1(match_id="mm-target-0", data_classification="SYNTHETIC", source_identity="synthetic-facts",
        source_reference="synthetic-fixture", source_file=key+".json", source_hash=hashlib.sha256(raw).hexdigest(),
        rights_basis="SELF_OBSERVED", rights_reference="self-authored-synthetic", retention_until_utc=at+timedelta(days=30),
        verified_by="synthetic-test", verified_at_utc=at, captured_at_utc=at, available_at_utc=published or at,
        published_at_utc=published or at, fact_category=category, assertion_class="FACT", confidence="1", structured_payload=payload)
    return EvidenceImportRequestV1(request_key=key, evidence=claim)


def test_f_i_manual_hash_freshness_dynamic_packet_and_expired_exact_retry(prospective):
    p = prospective
    request = manual_evidence(p)
    (p["root"] / request.evidence.source_file).write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        p["service"].import_evidence(request)
    request = manual_evidence(p)
    binding = p["service"].import_evidence(request)
    rebuild_analysis(p, evidence=binding)
    run = prepare(p, evidence_binding_ids=(binding.artifact_id,))
    packet, markdown = p["service"].packet_files(run)
    assert json.loads(packet)["schema_version"] == "ANALYSIS_PACKET_V4"
    assert "FACT" in markdown and "Published:" in markdown and binding.snapshot.artifact_id in markdown
    stale = p["service"].import_evidence(manual_evidence(p, key="stale", published=p["clock"].now()-timedelta(days=2)))
    with pytest.raises(ValueError, match="STALE_DECISION_EVIDENCE"):
        prepare(p, "stale-prepare", evidence_binding_ids=(stale.artifact_id,))
    p["clock"].at = request.evidence.retention_until_utc+timedelta(seconds=1)
    (p["root"] / request.evidence.source_file).unlink()
    assert p["service"].import_evidence(request) == binding  # No forbidden expired reread.


def test_l_m_n_full_correction_census_has_both_directions_and_neutral(prospective):
    from decimal import Decimal
    p = prospective
    run = prepare(p)
    packet = p["market"].repository.load(run.packet.artifact_id)
    reviews, reasons = [], []
    for unit in packet.market_units:
        context = unit.review_context
        probabilities = context.p_quant.model_dump(mode="json")
        if context.market_key.market_type == "THREE_WAY":
            delta = Decimal("0.1") if context.identity.match_id == "mm-target-0" else Decimal("-0.1")
            for row in probabilities["outcomes"]:
                if row["outcome"] == "HOME_WIN":
                    row["probability"] = str(Decimal(row["probability"])+delta)
                elif row["outcome"] == "DRAW":
                    row["probability"] = str(Decimal(row["probability"])-delta)
        common = dict(match_id=context.identity.match_id, market_key=context.market_key, review_context_id=unit.review_context_id)
        reviews.append(dict(**common, review_context_hash=unit.review_context_hash, status="VALID", p_llm=probabilities,
            assessment_confidence="1", scenarios=[], preferred_outcomes=[], avoid_outcomes=[], counter_scenarios=[],
            risk_tags=[], reasoning_summary="Synthetic deterministic correction test", limitations=["Not real performance"], evidence_refs=[]))
        reasons.append(dict(**common, categories=["OTHER"], assertion_class="ANALYSIS", rationale="Synthetic acceptance correction"))
    raw = canonical_json(dict(schema_version="LLM_REVIEW_V4", analysis_id=run.analysis.artifact_id,
        packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=reviews)).encode()
    p["service"].lock(LockWorkflowRequestV1(request_key="external-lock", run_id=run.artifact_id, strategy_requests=home_request()), review_bytes=raw, reasons=reasons)
    settle_all(p, run)
    validation = report(p)
    counts = validation.correction_performance["counts"]
    assert counts == {"improved": 1, "worsened": 1, "neutral": 2, "abstained": 0, "unavailable": 0}
    assert all(row["paired_observation_count"] == 2 for row in validation.layer_comparisons)
    assert len(validation.correction_performance["observations"]) == 4


def test_cli_daily_workflow_readonly_audit_and_forbidden_time_override(prospective, monkeypatch, capsys):
    from alembic import command
    from football_system.interfaces.cli import main
    from tests.integration.test_market_v2_upgrade import config_for
    p = prospective
    command.stamp(config_for(p["path"]), "head")
    # Injection is test-only; the public parser exposes no clock override.
    monkeypatch.setattr("football_system.interfaces.prospective_cli.SystemProspectiveClock", lambda: p["clock"])
    url = f"sqlite:///{p['path'].as_posix()}"
    def invoke(name, data, *extra):
        label = name+"-"+data.request_key
        path, out = p["root"] / (label+"-request.json"), p["root"] / (label+"-output.json")
        path.write_text(canonical_json(data), encoding="utf-8")
        assert main(["prospective", name, "--database-url", url, "--input", str(path), "--output", str(out), *extra]) == 0
        return json.loads(out.read_bytes())
    run = invoke("prepare", PrepareProspectiveRequestV1(request_key="cli-prepare", epoch_id=p["epoch"].artifact_id,
        analysis_id=p["analysis"].artifact_id, slate_key="cli", slate_date=KICKOFF.date(), budget_fen=10000),
        "--packet-dir", str(p["root"] / "packet"))
    assert (p["root"] / "packet/analysis_packet.md").is_file()
    decision = invoke("lock", LockWorkflowRequestV1(request_key="cli-lock", run_id=run["artifact_id"], strategy_requests=home_request()))
    assert decision["locked_at_utc"] < decision["earliest_kickoff_at_utc"]
    p["clock"].at = KICKOFF+timedelta(days=1)
    for identity in p["repo"].load(run["artifact_id"]).identities:
        observation = result(p, identity.match_id)
        # Retry through the public CLI checks the exact manual claim and avoids reread.
        invoke("result-import", ResultImportRequestV1(request_key="result-"+identity.match_id, result=observation.claim), "--evidence-root", str(p["root"]))
    invoke("settle", SettleProspectiveRequestV1(request_key="cli-settle", run_id=run["artifact_id"]))
    validation = invoke("report", ReportProspectiveRequestV1(request_key="cli-report", epoch_id=p["epoch"].artifact_id, as_of_at_utc=p["clock"].now()))
    assert main(["prospective", "audit", "--database-url", url, "--artifact-id", validation["artifact_id"]]) == 0
    assert main(["prospective", "show", "--database-url", url, "--artifact-id", run["artifact_id"]]) == 0
    bad = p["root"] / "bad.json"
    bad.write_text(canonical_json(dict(request_key="bad", run_id=run["artifact_id"], locked_at_utc=DECISION)), encoding="utf-8")
    assert main(["prospective", "lock", "--database-url", url, "--input", str(bad)]) == 1
    assert "locked_at_utc" in capsys.readouterr().err


def test_k_typed_form_and_schedule_cutoff_and_historical_lock_replay(prospective):
    p = prospective
    # Typed result FK and the domain cutoff/location gate are both exercised.
    now = p["clock"].now()
    form = dict(category="FORM", team_id="mm-home", as_of_at_utc=now, window_start_utc=DECISION-timedelta(days=30),
        window_end_utc=DECISION, result_ids=["result-mm-history-00"], location="HOME")
    binding = p["service"].import_evidence(manual_evidence(p, category="FORM", payload=form, key="form"))
    assert p["repo"].load(binding.artifact_id) == binding
    with pytest.raises(ValueError, match="FORM_LOCATION_MISMATCH"):
        p["service"].import_evidence(manual_evidence(p, category="FORM", payload=dict(form, location="AWAY"), key="wrong-form"))
    run = prepare(p)
    decision = lock(p, run)
    fixture = dict(match_id="mm-target-0", home_team_id="mm-home", away_team_id="mm-away",
        kickoff_at_utc=KICKOFF, status="CANCELLED", known_at_utc=now)
    schedule = dict(category="SCHEDULE", team_id="mm-home", as_of_at_utc=now, window_start_utc=DECISION,
        window_end_utc=KICKOFF+timedelta(days=1), fixtures=[fixture], coverage="PROVIDER_SCOPE_ONLY", competition_scope=["mm-synthetic-league"])
    p["service"].import_evidence(manual_evidence(p, category="SCHEDULE", payload=schedule, key="cancelled"))
    assert p["repo"].load(decision.artifact_id) == decision  # Equal UTC, later receipt cannot change history.
    with pytest.raises(ValueError, match="KICKOFF_CHANGED_PREPARE_AGAIN"):
        prepare(p, "cancelled-replacement", supersedes_run_id=run.artifact_id)


@pytest.mark.parametrize("category,correction,expected", [
    ("LINEUP", "CONFIRMED_LINEUP_CHANGE", "UNKNOWN_OR_EXPECTED_LINEUP_IS_NOT_CONFIRMED"),
    ("INJURY", "KEY_INJURY", "UNKNOWN_ABSENCE_IS_NOT_ACTIVE_INJURY_OR_BAN"),
])
def test_g_h_sidecar_cannot_promote_unknown_to_confirmed_fact(prospective, category, correction, expected):
    p = prospective
    payload = dict(category=category, team_id="mm-home", **({"status": "UNKNOWN"} if category == "LINEUP" else
        {"knowledge_status": "UNKNOWN", "as_of_at_utc": p["clock"].now()}))
    binding = p["service"].import_evidence(manual_evidence(p, category=category, payload=payload))
    rebuild_analysis(p, evidence=binding)
    run = prepare(p, evidence_binding_ids=(binding.artifact_id,))
    packet = p["market"].repository.load(run.packet.artifact_id)
    reviews, reasons = [], []
    for unit in packet.market_units:
        common = dict(match_id=unit.review_context.identity.match_id, market_key=unit.review_context.market_key, review_context_id=unit.review_context_id)
        reviews.append(dict(**common, review_context_hash=unit.review_context_hash, status="UNAVAILABLE", failure_code="SKIPPED_DISABLED", limitations=["synthetic"]))
        use = common["match_id"] == "mm-target-0" and common["market_key"].market_type == "TOTAL_GOALS"
        reasons.append(dict(**common, categories=[correction if use else "OTHER"], assertion_class="FACT", rationale="Synthetic false promotion attempt",
            evidence_snapshot_ids=[binding.snapshot.artifact_id] if use else []))
    raw = canonical_json(dict(schema_version="LLM_REVIEW_V4", analysis_id=run.analysis.artifact_id,
        packet_id=packet.artifact_id, packet_hash=packet.content_hash, market_reviews=reviews)).encode()
    with pytest.raises(ValueError, match=expected):
        p["service"].lock(LockWorkflowRequestV1(request_key="illegal-promotion", run_id=run.artifact_id, strategy_requests=home_request()), review_bytes=raw, reasons=reasons)
    with p["sessions"]() as session:
        assert session.scalar(text("SELECT count(*) FROM pv_locks")) == 0


def test_typed_fk_deferred_receipt_and_duplicate_observation_scope(prospective):
    from sqlalchemy.exc import IntegrityError
    from football_system.infrastructure.database.models import Base
    p = prospective
    with p["sessions"]() as session:
        receipt = dict(session.execute(text("SELECT * FROM pv_receipts ORDER BY sequence DESC LIMIT 1")).mappings().one())
    receipt.update(request_key="partial", response_id="missing-root", sequence=receipt["sequence"]+1)
    with pytest.raises(IntegrityError):
        with p["sessions"].begin() as session:
            session.execute(Base.metadata.tables["pv_receipts"].insert(), receipt)
    assert Base.metadata.tables["pv_locks"].c.optimizer_id.references(Base.metadata.tables["rd_runs"].c.artifact_id)
    assert Base.metadata.tables["pv_reason_evidence"].c.snapshot_id.references(Base.metadata.tables["pv_evidence"].c.artifact_id)
    run = prepare(p)
    decision = lock(p, run)
    other = p["service"].prepare(PrepareProspectiveRequestV1(request_key="duplicate-slate", epoch_id=p["epoch"].artifact_id,
        analysis_id=p["analysis"].artifact_id, slate_key="cannot-duplicate-samples", slate_date=KICKOFF.date(), budget_fen=10000))
    with pytest.raises(IntegrityError):
        lock(p, other, "duplicate-lock")
    assert p["repo"].load(decision.artifact_id) == decision
    with p["sessions"]() as session:
        assert session.scalar(text("SELECT count(*) FROM pv_locks")) == 1
        assert session.scalar(text("SELECT count(*) FROM pv_receipts WHERE request_key IN ('partial','duplicate-lock')")) == 0
