"""Actual qualified-model/run/snapshot/budget APIs, self-authored synthetic only."""
from datetime import timedelta

from football_system.application.portfolio_requests import REQUEST_TYPES
from football_system.domain.real_bridge import THREE_WAY
from football_system.domain.portfolio import PortfolioCapitalDeclarationV1
from football_system.infrastructure.files.training_evidence import ReviewerAuthorityV1
from football_system.infrastructure.database.portfolio_repository import SqlAlchemyPortfolioRepository
from scripts.scoped_model_acceptance_inputs import graph, source, through_model, create_program_and_pin, write, review


def setup(root, *, maximum_odds_age_seconds=3600):
    g = graph(root)
    g.sources = [through_model(g, source(g), kickoff_hours=4), through_model(g, source(g, "B", 5, "Europe/London"), kickoff_hours=5)]
    for s in g.sources:
        create_program_and_pin(g, s)
        s.proof = write(g.root, s.prefix + "/prospective-proof.txt", b"SELF_AUTHORED_SYNTHETIC_INPUTS_AND_RESULTS; no real betting.\n")
        s.admissions = {}
        for kind, identity in (("MARKET","THE_ODDS_API"),("SPORTTERY","SPORTTERY_MANUAL"),("RESULT","synthetic-results-"+s.label)):
            s.admissions[kind] = s.call("admission", kind, program_id=s.program.artifact_id, kind=kind, source_identity=identity,
                state="ADMITTED", effective_at_utc=g.clock(), expires_at_utc=g.clock()+timedelta(days=5), rights_reference="self-authored software acceptance",
                verified_by="synthetic-operator", evidence_file=s.proof.evidence_reference, evidence_hash=s.proof.evidence_sha256,
                credential_verified=kind=="MARKET")
        s.policy = s.call("policy", "policy", program_id=s.program.artifact_id, result_source_identity="synthetic-results-"+s.label,
            maximum_odds_age_seconds=maximum_odds_age_seconds, minimum_bookmaker_count=2)
        s.anchor = s.call("anchor", "anchor", program_id=s.program.artifact_id, policy_id=s.policy.artifact_id, model_pin_id=s.pin.artifact_id,
            starts_at_utc=g.clock()+timedelta(minutes=1), ends_at_utc=s.kickoff+timedelta(days=2))
        s.epoch = s.call("epoch", "epoch", anchor_id=s.anchor.artifact_id)
    g.clock.value += timedelta(minutes=2)
    for s in g.sources:
        s.slate = s.call("slate", "slate", epoch_id=s.epoch.artifact_id, match_ids=tuple(t.match_id for t in s.targets),
            source_reference="COMPLETE_SELF_AUTHORED_SYNTHETIC_COHORT", source_file=s.proof.evidence_reference, source_hash=s.proof.evidence_sha256)
        s.bucket = s.call("bucket", "bucket", slate_id=s.slate.artifact_id, kickoff_at_utc=s.kickoff)
        markets, prices = [], []
        for i, (market, sp) in enumerate(s.captures):
            markets.append(s.call("market-bind", f"market-{i}", program_id=s.program.artifact_id, admission_id=s.admissions["MARKET"].artifact_id,
                ingestion_id=market.ingestion_id, snapshot_id=market.consensus_batch.snapshots[0].snapshot_id).artifact_id)
            prices.append(s.call("sp-bind", f"price-{i}", program_id=s.program.artifact_id, admission_id=s.admissions["SPORTTERY"].artifact_id,
                ingestion_id=sp.ingestion_id, snapshot_id=sp.batch.snapshots[0].snapshot_id).artifact_id)
        s.prepare = dict(epoch_id=s.epoch.artifact_id, bucket_id=s.bucket.artifact_id, market_binding_ids=tuple(markets),
            sp_binding_ids=tuple(prices), budget_fen=10000)
        s.run = s.call("prepare", "prepare", **s.prepare)
        assert s.run.status == "PREPARING"
        s.audit = s.call("review-import", "review", run_id=s.run.artifact_id)
    g.pf = SqlAlchemyPortfolioRepository(g.bridge, budget_evidence=g.evidence)
    g.pf_call = lambda operation, key, **values: g.pf.execute(operation, REQUEST_TYPES[operation](request_key=key, **values))
    g.snapshots = tuple(g.pf_call("snapshot", "snapshot-"+s.label, source_run_id=s.run.artifact_id,
        imported_review_id=s.audit.review.artifact_id, review_mode="EXPLICIT_V4_ABSTENTION") for s in g.sources)
    return g


def authorize_budget(g, *, budget=10000, label="one", lifetime=timedelta(days=1)):
    evidence = write(g.root, "budget-capital-"+label+".json", PortfolioCapitalDeclarationV1(funding_scope_id="synthetic-funding-"+label,
        allocation_id="synthetic-allocation-"+label, funding_limit_fen=budget, legacy_authorized_capital_included=False,
        owner_reference="self-authored synthetic capital; no real money", provenance="SYNTHETIC_SOFTWARE_ACCEPTANCE"))
    subject = g.pf.prepare_budget_review(snapshot_ids=tuple(s.artifact_id for s in g.snapshots), funding_scope_id="synthetic-funding-"+label,
        allocation_id="synthetic-allocation-"+label, decision_cycle_id="synthetic-cycle-"+label, budget_fen=budget, funding_limit_fen=budget,
        capital_evidence=evidence, effective_at_utc=g.clock(), expires_at_utc=g.clock()+lifetime)
    authority = write(g.root, "budget-authority-"+label+".json", ReviewerAuthorityV1(issued_by="synthetic-capital-owner", authorized_reviewer="synthetic-reviewer",
        source_ids=(subject.source_id,), attested_schema_versions=(subject.schema_version,), effective_at_utc=g.clock()-timedelta(days=1),
        expires_at_utc=g.clock()+timedelta(days=2)))
    g.evidence.trusted_authorities[authority.evidence_reference] = authority.evidence_sha256
    reviewed = review(g, subject, subject.source_id, "budget-review-"+label+".json")
    value = g.pf_call("budget-record", "budget-"+label, subject=subject, review=reviewed, authority=authority)
    g.budget_subject, g.budget_authority, g.budget_review = subject, authority, reviewed
    return value


def prepare(g, pass_type="4X11", *, label="one"):
    g.budget = authorize_budget(g, label=label)
    all_ids = [t.match_id for s in g.sources for t in s.targets]
    ids = (all_ids[0], all_ids[2]) if pass_type=="2X1" else (all_ids[0],all_ids[1],all_ids[2]) if pass_type=="3X4" else tuple(all_ids)
    request = dict(pass_type=pass_type, choices=[dict(match_id=mid, market_key=THREE_WAY, outcomes=("HOME_WIN",)) for mid in sorted(ids)])
    bundle = g.pf_call("ticket-requests", "tickets-"+label, requests=(request,))
    g.ticket_bundle = bundle
    g.assembly = g.pf_call("assembly", "assembly-"+label, snapshot_ids=tuple(sorted(s.artifact_id for s in g.snapshots)),
        budget_authority_id=g.budget.artifact_id, ticket_request_bundle_id=bundle.artifact_id)
    return g.assembly


def results(g, *, only_first=False, revised=False, unsupported=False):
    from football_system.domain.prospective import ManualResultImportV1
    g.clock.value = max(g.clock.value+timedelta(seconds=1), max(s.kickoff for s in g.sources)+timedelta(hours=2, minutes=int(revised)))
    outputs = []
    for s in g.sources[:1] if only_first else g.sources:
        for i, target in enumerate(s.targets):
            previous = getattr(s,"observations", {}).get(target.match_id)
            if previous and not revised:
                outputs.append(previous)
                continue
            key = f"result-{i}"+("-revision" if revised else "")
            at = g.clock()
            unusable = unsupported and s is g.sources[0] and i == 0
            observation = s.call("result-import", key, program_id=s.program.artifact_id, admission_id=s.admissions["RESULT"].artifact_id,
                result=ManualResultImportV1(match_id=target.match_id, data_classification="REAL_SOURCE_DATA", source_identity="synthetic-results-"+s.label,
                    source_reference="SELF_AUTHORED_SYNTHETIC_RESULT", source_file=s.proof.evidence_reference, source_hash=s.proof.evidence_sha256,
                    verified_by="synthetic-operator", verified_at_utc=at, observed_at_utc=at, available_at_utc=at, published_at_utc=at,
                    source_version_id=s.label+key, previous_source_version_id=previous.claim.source_version_id if previous else None,
                    status="VOID" if unusable else "FT", regular_time_semantics="UNPROVEN" if unusable else "REGULATION_ONLY",
                    home_goals=None if unusable else 0 if revised else 2, away_goals=None if unusable else 1,
                    supersedes_observation_id=previous.artifact_id if previous else None, rights_reference="self-authored synthetic results",
                    retention_until_utc=at+timedelta(days=2)))
            if not hasattr(s,"observations"):
                s.observations = {}
            s.observations[target.match_id] = observation
            outputs.append(observation)
    return tuple(outputs)
