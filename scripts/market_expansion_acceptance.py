"""Offline fixture scaffold shared by source tests and installed-wheel acceptance.

This file is not packaged. `python -I` can execute it with the installed package;
it imports no tests and performs no provider/LLM HTTP requests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from football_system.application.environment import (
    RuntimeEnvironment,
    RuntimeProvenance,
)
from football_system.application.model_analysis import (
    RunModelAnalysisRequest,
    RunModelAnalysisService,
)
from football_system.application.ports.data_providers import (
    EloTrainingHistoryBatch,
    EloTrainingResultSource,
)
from football_system.application.market_v2 import (
    BuildMarketUnitRequestV1,
    BuildMultiAnalysisRequestV1,
    ConsensusRequestV2,
    FusionRequestV4,
    GoalCohortRequestV1,
    MarketExpansionService,
    PacketRequestV4,
    PlanRequestV2,
    SettleRequestV2,
    TrainGoalsRequestV1,
)
from football_system.config import AppSettings
from football_system.domain.archive import (
    HistoricalDataMode,
    canonical_json,
    match_result_payload_sha256,
)
from football_system.domain.backtest import BacktestArchiveProvenance
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import stable_id
from football_system.domain.goal_model import GoalPredictionRequestV1
from football_system.domain.market import (
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.market_analysis import MarketMatchIdentityV1
from football_system.domain.market_v2 import MarketKeyV2
from football_system.domain.match import Competition, Team
from football_system.domain.prediction import FusionPolicyName
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloRegularTimeResult,
)
from football_system.domain.settlement import MatchResult
from football_system.domain.strategy_pass_v2 import (
    MatchChoiceRequestV1,
    TicketRequestV2,
)
from football_system.infrastructure.database.historical_repositories import (
    SqlAlchemyHistoricalRepository,
)
from football_system.infrastructure.database.market_v2_repository import (
    SqlAlchemyMultiMarketRepository,
)
from football_system.infrastructure.database.models import (
    CanonicalMatchIdentityRecord,
    CompetitionRecord,
    MatchRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    TeamRecord,
)
from football_system.infrastructure.database.repositories import (
    SqlAlchemyAnalysisRepository,
)
from football_system.infrastructure.providers.exact_market_fixture import (
    fixture_label,
    parse_exact_market_fixture,
)
from football_system.infrastructure.providers.mock.dataset import (
    MockDataset,
    MockMatchSeed,
)
from football_system.infrastructure.providers.mock.fixtures import MockFixtureProvider
from football_system.infrastructure.providers.mock.market_odds import (
    MockMarketOddsProvider,
)
from football_system.infrastructure.providers.mock.sporttery import (
    MockSportteryProvider,
)

DAY = datetime(2026, 1, 1, tzinfo=timezone.utc)
DECISION = DAY + timedelta(days=21)
KICKOFF = DAY + timedelta(days=24)
COMPETITION = "mm-synthetic-league"
SEASON = "2026"
LEGACY_RUN = "mm-synthetic-legacy-elo"


def seed_environment(sessions):
    competition = Competition(
        competition_id=COMPETITION,
        canonical_key=COMPETITION,
        name="Synthetic League",
        country_code="GB",
    )
    names = ("mm-home", "mm-away", *(f"mm-opponent-{i}" for i in range(10)))
    teams = tuple(Team(team_id=n, canonical_key=n, name=n) for n in names)
    provider_id = stable_id("provider", "MOCK_TRAINING")
    history = []
    with sessions.begin() as session:
        session.add(
            ProviderRecord(
                provider_id=provider_id,
                code="MOCK_TRAINING",
                name="Synthetic training",
                provider_kind="MOCK",
            )
        )
        session.add(CompetitionRecord(**competition.model_dump()))
        session.add_all(TeamRecord(**t.model_dump()) for t in teams)
        session.flush()
        for i in range(10):
            match = f"mm-history-{i:02}"
            home = "mm-home" if i < 5 else f"mm-opponent-{i}"
            away = f"mm-opponent-{i}" if i < 5 else "mm-away"
            kickoff = DAY + timedelta(days=i)
            known = kickoff - timedelta(days=1)
            h, a = (2, 1) if i < 5 else (1, 2)
            session.add(
                MatchRecord(
                    internal_match_id=match,
                    competition_id=COMPETITION,
                    home_team_id=home,
                    away_team_id=away,
                    kickoff_at_utc=kickoff,
                    status="FINISHED",
                    available_at_utc=known,
                    created_at_utc=known,
                )
            )
            session.flush()
            session.add(
                CanonicalMatchIdentityRecord(
                    internal_match_id=match,
                    season=SEASON,
                    competition_type="LEAGUE",
                    available_at_utc=known,
                )
            )
            session.add(
                ProviderMatchMappingRecord(
                    mapping_id=stable_id("fixture-mapping", match),
                    provider_id=provider_id,
                    external_namespace="SYNTHETIC",
                    external_match_id=match,
                    internal_match_id=match,
                    resolution_method="SYNTHETIC_EXACT",
                    confidence=Decimal(1),
                    available_at_utc=known,
                )
            )
            history.append(
                EloRegularTimeResult(
                    match_result_id="result-" + match,
                    match_id=match,
                    season_id=SEASON,
                    home_team_id=home,
                    away_team_id=away,
                    kickoff_at_utc=kickoff,
                    available_at_utc=kickoff + timedelta(hours=2),
                    ingested_at_utc=kickoff + timedelta(hours=2),
                    home_goals=h,
                    away_goals=a,
                    payload_hash=match_result_payload_sha256(h, a),
                )
            )
    repository = SqlAlchemyHistoricalRepository(sessions)
    results = tuple(
        MatchResult(
            match_result_id=r.match_result_id,
            match_id=r.match_id,
            provider_code="MOCK_TRAINING",
            home_goals=r.home_goals,
            away_goals=r.away_goals,
            observed_at_utc=r.available_at_utc,
            available_at_utc=r.available_at_utc,
            ingested_at_utc=r.ingested_at_utc,
            source_result_key=r.match_id,
            payload_hash=r.payload_hash,
        )
        for r in history
    )
    repository.append_match_results(results)
    known = DECISION - timedelta(hours=1)
    targets = tuple(
        MockMatchSeed(
            match_id=f"mm-target-{i}",
            fixture_external_id=f"f-{i}",
            market_external_id=f"m-{i}",
            sporttery_match_no=f"SYNTH-{i}",
            competition_id=COMPETITION,
            home_team_id="mm-home",
            away_team_id="mm-away",
            kickoff_at_utc=KICKOFF + timedelta(hours=i),
            available_at_utc=known,
            market_captured_at_utc=known,
            market_available_at_utc=known,
            sporttery_captured_at_utc=known,
            sporttery_available_at_utc=known,
            market_odds=ThreeWayMarketOdds(home_win="2.1", draw="3.2", away_win="3.8"),
            sporttery_bonus=ThreeWayFixedBonus(home_win="3", draw="1.1", away_win="4"),
            manual_quant=ThreeWayProbability(
                home_win="0.6", draw="0.2", away_win="0.2"
            ),
            manual_quant_available_at_utc=known,
        )
        for i in range(4)
    )
    dataset = MockDataset(
        as_of_at_utc=DECISION, competitions=(competition,), teams=teams, matches=targets
    )

    class History:
        runtime_provenance = RuntimeProvenance(
            environment=RuntimeEnvironment.MOCK,
            provider_code="MOCK_TRAINING",
            provenance="SYNTHETIC_FIXED_GOAL_FACTS",
            is_mock=True,
            data_mode=HistoricalDataMode.LIVE_STRICT,
        )

        async def fetch_elo_training_history(self, query):
            archive = BacktestArchiveProvenance(
                archive_id="mm-synthetic-training",
                archive_schema_version="HISTORICAL_ARCHIVE_V1",
                provider_code="MOCK_TRAINING",
                dataset_kind="MATCH_RESULTS",
                payload_sha256="a" * 64,
            )
            return EloTrainingHistoryBatch(
                competition_id=query.competition_id,
                target_season_id=query.target_season_id,
                as_of_at_utc=query.as_of_at_utc,
                sources=tuple(
                    EloTrainingResultSource(result=r, archive=archive) for r in history
                ),
            )

    settings = AppSettings()
    service = RunModelAnalysisService(
        MockFixtureProvider(dataset),
        MockMarketOddsProvider(dataset),
        MockSportteryProvider(dataset),
        History(),
        SqlAlchemyAnalysisRepository(sessions),
        settings,
    )
    artifacts = asyncio.run(
        service.run(
            RunModelAnalysisRequest(
                as_of_at_utc=DECISION,
                kickoff_from_utc=DECISION,
                kickoff_to_utc=KICKOFF + timedelta(days=1),
                budgets_fen=(0, 10000),
                fusion_policy=FusionPolicyName.QUANT_ONLY_V1,
                analysis_run_id=LEGACY_RUN,
                execution_time_utc=DECISION,
                competition_id=COMPETITION,
                season_id=SEASON,
                elo_config=EloBaselineConfig(),
            )
        )
    )
    with sessions.begin() as session:
        for match in artifacts.matches:
            if session.get(CanonicalMatchIdentityRecord, match.match_id) is None:
                session.add(
                    CanonicalMatchIdentityRecord(
                        internal_match_id=match.match_id,
                        season=SEASON,
                        competition_type="LEAGUE",
                        available_at_utc=known,
                    )
                )
    return artifacts, results


def fixture_book(
    identity, key, *, prices=None, kind="SPORTTERY", book="SYNTHETIC_BOOK"
):
    prices = prices or {}
    code = {
        "THREE_WAY": "FT_1X2",
        "HANDICAP_THREE_WAY": "FT_HOME_HANDICAP_1X2",
        "TOTAL_GOALS": "FT_EXACT_GOALS_0_7_PLUS",
        "CORRECT_SCORE": "FT_CORRECT_SCORE_31",
    }[key.market_type.value]
    return canonical_json(
        dict(
            schema_version="EXACT_MARKET_FIXTURE_V1",
            classification="SYNTHETIC_ACCEPTANCE_DATA",
            match_id=identity.match_id,
            source_identity=identity.match_id + "-" + key.canonical + "-" + book,
            source_reference="synthetic-" + identity.match_id + "-" + code + "-" + book,
            bookmaker_code=book,
            kind=kind,
            market_code=code,
            home_handicap=key.home_handicap,
            prices=[
                dict(label=fixture_label(o), price=prices.get(o.value, "1.1"))
                for o in key.catalog
            ],
            captured_at_utc=DECISION,
            available_at_utc=DECISION,
            ingested_at_utc=DECISION,
            sale_status="OPEN",
        )
    ).encode()


def build_fixture_analysis(sessions, results, *, all_below=False):
    repo = SqlAlchemyMultiMarketRepository(sessions)
    service = MarketExpansionService(
        repo, exact_market_adapter=parse_exact_market_fixture
    )
    admission = GoalCohortRequestV1(
        source_reference="synthetic-goal-cohort",
        competition_id=COMPETITION,
        season_id=SEASON,
        result_ids=tuple(r.match_result_id for r in results),
        admitted_at_utc=DAY + timedelta(days=20),
    )
    cohort = service.cohort_admit(canonical_json(admission).encode())
    units = []
    for i in range(4):
        identity = MarketMatchIdentityV1(
            match_id=f"mm-target-{i}",
            competition_id=COMPETITION,
            season_id=SEASON,
            home_team_id="mm-home",
            away_team_id="mm-away",
            home_team_name="mm-home",
            away_team_name="mm-away",
            kickoff_at_utc=KICKOFF + timedelta(hours=i),
        )
        goal = service.train_goals(
            TrainGoalsRequestV1(
                cohort_id=cohort.artifact_id,
                prediction=GoalPredictionRequestV1(
                    match_id=identity.match_id,
                    competition_id=COMPETITION,
                    season_id=SEASON,
                    home_team_id="mm-home",
                    away_team_id="mm-away",
                    kickoff_at_utc=identity.kickoff_at_utc,
                    training_cutoff_at_utc=DECISION,
                    generated_at_utc=DECISION,
                ),
            )
        )
        for kind in ("THREE_WAY", "HANDICAP_THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE"):
            key = MarketKeyV2(
                market_type=kind,
                home_handicap=-1 if kind == "HANDICAP_THREE_WAY" else None,
            )
            prices = (
                {"HOME_WIN": "4", "DRAW": "6", "AWAY_WIN": "5"}
                if kind in {"THREE_WAY", "HANDICAP_THREE_WAY"}
                else {"GOALS_2": "6", "GOALS_3": "8"}
                if kind == "TOTAL_GOALS"
                else {
                    "SCORE_3_1": "40",
                    "SCORE_4_1": "100",
                    "SCORE_5_1": "1.1",
                    "HOME_OTHER": "10000",
                    "DRAW_OTHER": "10000",
                    "AWAY_OTHER": "10000",
                }
            )
            info = service.snapshot_import(
                fixture_book(identity, key, prices={} if all_below else prices)
            )
            legacy = (
                repo.legacy_threeway(LEGACY_RUN, identity.match_id)
                if kind == "THREE_WAY"
                else None
            )
            consensus = None
            if legacy is None:
                books = []
                for no in (1, 2):
                    book = service.snapshot_import(
                        fixture_book(
                            identity,
                            key,
                            kind="BOOKMAKER",
                            book=f"SYNTHETIC_BOOK_{no}",
                            prices={
                                o.value: str((len(key.catalog) + 1) * no)
                                for o in key.catalog
                            },
                        )
                    )
                    books.append(book["snapshot_id"])
                consensus = service.consensus(
                    ConsensusRequestV2(
                        match_id=identity.match_id,
                        market_key=key,
                        snapshot_ids=tuple(books),
                        decision_cutoff=DECISION,
                    )
                )
            units.append(
                service.build_unit(
                    BuildMarketUnitRequestV1(
                        identity=identity,
                        market_key=key,
                        decision_cutoff=DECISION,
                        sporttery_id=info["snapshot_id"],
                        goal_state_id=goal.artifact_id if legacy is None else None,
                        legacy_source_id=legacy.artifact_id if legacy else None,
                        consensus_id=consensus.artifact_id if consensus else None,
                        base_policy="LEGACY_FROZEN_THREE_WAY"
                        if legacy
                        else "QUANT_ONLY_V1",
                    )
                )
            )
    analysis = service.build_analysis(
        BuildMultiAnalysisRequestV1(
            unit_ids=tuple(u.artifact_id for u in units),
            budgets_fen=(0, 10000),
            rules=SportteryRules(version="SYNTHETIC_MARKET_V2"),
            constraints=PortfolioConstraints(),
            min_selection_ev="0.02",
            min_ticket_roi="0.02",
        )
    )
    packet = service.packet(PacketRequestV4(analysis_id=analysis.artifact_id))
    reviews = []
    for u in packet.market_units:
        c = u.review_context
        reviews.append(
            dict(
                match_id=c.identity.match_id,
                market_key=c.market_key,
                review_context_id=u.review_context_id,
                review_context_hash=u.review_context_hash,
                status="VALID",
                p_llm=c.p_quant,
                assessment_confidence="0.4",
                scenarios=[],
                preferred_outcomes=[],
                avoid_outcomes=[],
                counter_scenarios=[],
                risk_tags=[],
                reasoning_summary="SYNTHETIC_FIXED_REVIEW",
                limitations=["Not real model performance"],
                evidence_refs=[],
            )
        )
    raw = canonical_json(
        dict(
            schema_version="LLM_REVIEW_V4",
            analysis_id=analysis.artifact_id,
            packet_id=packet.artifact_id,
            packet_hash=packet.content_hash,
            market_reviews=reviews,
        )
    ).encode()
    review = service.review_import(canonical_json(packet).encode(), raw)
    fusion = service.fusion(
        FusionRequestV4(analysis_id=analysis.artifact_id, review_id=review.artifact_id)
    )
    return service, analysis, packet, review, fusion


def request_for_counts(counts):
    kinds = ("THREE_WAY", "TOTAL_GOALS", "CORRECT_SCORE", "THREE_WAY")
    choices = (
        ("HOME_WIN",),
        ("GOALS_2", "GOALS_3"),
        ("SCORE_3_1", "SCORE_4_1", "SCORE_5_1"),
        ("AWAY_WIN",),
    )
    pass_type = {2: "2X1", 3: "3X4", 4: "4X11"}[len(counts)]
    return TicketRequestV2(
        pass_type=pass_type,
        choices=tuple(
            MatchChoiceRequestV1(
                match_id=f"mm-target-{i}",
                market_key=MarketKeyV2(market_type=kinds[i]),
                outcomes=choices[i][:n],
            )
            for i, n in enumerate(counts)
        ),
    )


def main():
    import argparse
    from pathlib import Path
    from sqlalchemy import text
    from football_system.interfaces.cli import main as cli, _resource_root
    from football_system.infrastructure.database.migrations import upgrade_database
    from football_system.infrastructure.database.session import (
        create_database_engine,
        create_session_factory,
    )
    from football_system.domain.market_v2 import settle_market
    from football_system.domain.services.poisson_goals import (
        score_grid,
        project_score_grid,
    )
    from football_system.domain.strategy_pass_v2 import StrategyProfileV2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(exist_ok=False)
    resources = _resource_root()
    fixture = json.loads(
        (resources / "data/fixtures/market_expansion_v1.json").read_text(
            encoding="utf-8"
        )
    )
    url = f"sqlite:///{(work / 'acceptance.db').as_posix()}"
    upgrade_database(url, resources / "alembic.ini")
    engine = create_database_engine(url)
    sessions = create_session_factory(engine)
    old, results = seed_environment(sessions)
    service, analysis, packet, review, fusion = build_fixture_analysis(
        sessions, results
    )

    def invoke(command, request, name):
        path = work / (name + "-request.json")
        output = work / (name + ".json")
        path.write_text(canonical_json(request), encoding="utf-8")
        assert (
            cli(
                [
                    "market-v2",
                    command,
                    "--database-url",
                    url,
                    "--input",
                    str(path),
                    "--output",
                    str(output),
                ]
            )
            == 0
        )
        return json.loads(output.read_text(encoding="utf-8"))

    exported = invoke(
        "packet-export", PacketRequestV4(analysis_id=analysis.artifact_id), "packet-v4"
    )
    review_path = work / "review-v4.json"
    review_path.write_text(review.raw_review_json, encoding="utf-8")
    assert (
        cli(
            [
                "market-v2",
                "review-validate",
                "--packet",
                str(work / "packet-v4.json"),
                "--review",
                str(review_path),
            ]
        )
        == 0
    )
    assert (
        cli(
            [
                "market-v2",
                "review-import",
                "--database-url",
                url,
                "--packet",
                str(work / "packet-v4.json"),
                "--review",
                str(review_path),
                "--output",
                str(work / "imported-v4.json"),
            ]
        )
        == 0
    )
    assert exported["artifact_id"] == packet.artifact_id
    assert (
        invoke(
            "fusion",
            FusionRequestV4(
                analysis_id=analysis.artifact_id, review_id=review.artifact_id
            ),
            "fusion-v4",
        )["artifact_id"]
        == fusion.artifact_id
    )
    evaluations = []
    at = KICKOFF + timedelta(days=1)
    for i, (home, away) in enumerate(((2, 0), (2, 1), (4, 1), (0, 1))):
        evaluations.append(
            MatchResult(
                match_result_id=f"mm-final-result-{i}",
                match_id=f"mm-target-{i}",
                provider_code="MOCK_FIXTURE",
                home_goals=home,
                away_goals=away,
                observed_at_utc=at,
                available_at_utc=at,
                ingested_at_utc=at,
                source_result_key=f"mm-final-source-{i}",
                payload_hash=match_result_payload_sha256(home, away),
            )
        )
    history = SqlAlchemyHistoricalRepository(sessions)
    history.append_match_results(tuple(evaluations))
    summary = {
        "classification": "SYNTHETIC_ACCEPTANCE_DATA",
        "performance_warning": "NOT REAL MODEL PERFORMANCE",
        "cases": {},
    }
    for entry in fixture["expansion_goldens"]:
        request = PlanRequestV2(
            fusion_id=fusion.artifact_id,
            budget_fen=10000,
            requests=(request_for_counts(tuple(entry["counts"])),),
        )
        parsed = invoke("plan", request, "plan-" + entry["pass_type"])
        plan = service.repository.load(parsed["artifact_id"])
        candidate = plan.candidates[0]
        assert (
            candidate.expanded_atomic_bet_count == entry["expanded"]
            and candidate.unit_stake_fen == entry["unit_stake_fen"]
        )
        assert service.plan(request) == plan and plan.tickets
        used = {c.match_id for c in candidate.choice_sets}
        settled = invoke(
            "settle",
            SettleRequestV2(
                plan_id=plan.artifact_id,
                result_ids=tuple(
                    r.match_result_id for r in evaluations if r.match_id in used
                ),
                settled_at_utc=at,
            ),
            "settle-" + entry["pass_type"],
        )
        assert settled["reason"] == "SETTLED" and settled["gross_payout_fen"] > 0
        summary["cases"][{"2X1": "F", "3X4": "G", "4X11": "H"}[entry["pass_type"]]] = (
            dict(
                expanded=entry["expanded"],
                unit_stake_fen=entry["unit_stake_fen"],
                gross_payout_fen=settled["gross_payout_fen"],
            )
        )
    for home, away, handicap, outcome in fixture["handicap_settlement"]:
        assert (
            settle_market(
                MarketKeyV2(market_type="HANDICAP_THREE_WAY", home_handicap=handicap),
                home,
                away,
            )
            == outcome
        )
    for home, away, outcome in fixture["goals_settlement"]:
        assert (
            settle_market(MarketKeyV2(market_type="TOTAL_GOALS"), home, away) == outcome
        )
    for home, away, outcome in fixture["score_settlement"]:
        assert (
            settle_market(MarketKeyV2(market_type="CORRECT_SCORE"), home, away)
            == outcome
        )
    for home, away in fixture["poisson_lambdas"]:
        grid = score_grid(home, away)
        for key in (
            MarketKeyV2(market_type="TOTAL_GOALS"),
            MarketKeyV2(market_type="CORRECT_SCORE"),
            MarketKeyV2(market_type="HANDICAP_THREE_WAY", home_handicap=-1),
        ):
            p = project_score_grid(grid, key)
            assert sum(o.probability for o in p.outcomes) == 1
    negative = service.plan(
        PlanRequestV2(
            fusion_id=fusion.artifact_id,
            budget_fen=10000,
            requests=(request_for_counts((1, 2, 3)),),
        )
    )
    assert len(negative.candidates[0].choice_sets[2].candidates) == 2
    with engine.connect() as conn:
        before = conn.scalar(text("SELECT COUNT(*) FROM mm_artifacts"))
    try:
        service.plan(
            PlanRequestV2(
                fusion_id=fusion.artifact_id,
                budget_fen=10000,
                profile=StrategyProfileV2(max_expanded_atomic_bets_per_ticket=28),
                requests=(request_for_counts((1, 2, 2, 1)),),
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expansion bound failed")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT COUNT(*) FROM mm_artifacts")) == before
    _, _, _, _, no_value = build_fixture_analysis(sessions, results, all_below=True)
    no_bet = service.plan(
        PlanRequestV2(
            fusion_id=no_value.artifact_id,
            budget_fen=10000,
            requests=(request_for_counts((1, 2)),),
        )
    )
    assert no_bet.status == "NO_BET" and no_bet.total_stake_fen == 0
    try:
        TicketRequestV2(
            pass_type="2X1",
            choices=(
                MatchChoiceRequestV1(
                    match_id="mm-target-0",
                    market_key=MarketKeyV2(market_type="THREE_WAY"),
                    outcomes=("HOME_WIN",),
                ),
                MatchChoiceRequestV1(
                    match_id="mm-target-0",
                    market_key=MarketKeyV2(market_type="TOTAL_GOALS"),
                    outcomes=("GOALS_3",),
                ),
            ),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("same-match cross-market compound accepted")
    prior = evaluations[1]
    for ordinal, (outcome, score) in enumerate(
        (("HOME_OTHER", (6, 1)), ("DRAW_OTHER", (4, 4)), ("AWAY_OTHER", (1, 6))), 1
    ):
        req = TicketRequestV2(
            pass_type="2X1",
            choices=(
                MatchChoiceRequestV1(
                    match_id="mm-target-0",
                    market_key=MarketKeyV2(market_type="THREE_WAY"),
                    outcomes=("HOME_WIN",),
                ),
                MatchChoiceRequestV1(
                    match_id="mm-target-1",
                    market_key=MarketKeyV2(market_type="CORRECT_SCORE"),
                    outcomes=(outcome,),
                ),
            ),
        )
        plan = service.plan(
            PlanRequestV2(
                fusion_id=fusion.artifact_id, budget_fen=10000, requests=(req,)
            )
        )
        changed = prior.model_copy(
            update={
                "match_result_id": "mm-other-" + outcome,
                "source_result_key": "mm-other-source-" + outcome,
                "home_goals": score[0],
                "away_goals": score[1],
                "payload_hash": match_result_payload_sha256(*score),
                "ingested_at_utc": at + timedelta(minutes=ordinal),
                "supersedes_match_result_id": prior.match_result_id,
            }
        )
        history.append_match_result(changed)
        settled = service.settle(
            SettleRequestV2(
                plan_id=plan.artifact_id,
                result_ids=(evaluations[0].match_result_id, changed.match_result_id),
                settled_at_utc=changed.ingested_at_utc,
            )
        )
        assert settled.gross_payout_fen == 200 * 4 * 10000 * plan.tickets[0].multiplier
        prior = changed
    for code in ("B", "C", "D", "E", "I", "J", "K", "M", "N", "O", "P"):
        summary["cases"][code] = "PASS"
    summary["legacy_proof"] = "A/L: immutable v0.7.0 golden and upgrade tests"
    summary["migration_head"] = "39526d8f40cb"
    (work / "acceptance-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    engine.dispose()
    print("MARKET_EXPANSION_V1_INSTALLED_ACCEPTANCE_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
