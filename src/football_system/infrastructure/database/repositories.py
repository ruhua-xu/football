from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.models import AnalysisArtifacts, StoredInputManifest
from football_system.domain.analysis import AnalysisRunStatus
from football_system.domain.betting import SportteryRules
from football_system.domain.common import stable_id
from football_system.domain.services.payout import calculate_stake_fen
from football_system.infrastructure.database.models import (
    AnalysisRunMatchRecord,
    AnalysisRunRecord,
    BetCandidateRecord,
    BookmakerRecord,
    CompetitionRecord,
    FinalPredictionOutcomeRecord,
    FinalPredictionRecord,
    ManualQuantInputOutcomeRecord,
    ManualQuantInputRecord,
    MarketOddsQuoteRecord,
    MarketOddsSnapshotRecord,
    MarketProbabilityInputRecord,
    MarketProbabilityOutcomeRecord,
    MarketProbabilityRecord,
    MatchRecord,
    PortfolioRecord,
    PortfolioCashPositionRecord,
    PortfolioMatchExposureRecord,
    PortfolioRiskReportRecord,
    PortfolioSelectionExposureRecord,
    PortfolioStressResultRecord,
    PortfolioStressTicketResultRecord,
    ProviderMatchMappingRecord,
    ProviderRecord,
    QuantPredictionOutcomeRecord,
    QuantPredictionRecord,
    SportteryBonusQuoteRecord,
    SportteryBonusSnapshotRecord,
    TeamRecord,
    TicketCandidateLegRecord,
    TicketCandidateRecord,
    TicketLegRecord,
    TicketRecord,
)


class SqlAlchemyAnalysisRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_analysis(
        self,
        artifacts: AnalysisArtifacts,
        rules: SportteryRules,
    ) -> None:
        if artifacts.analysis_run.status != AnalysisRunStatus.COMPLETED:
            raise ValueError("only completed AnalysisRun artifacts can be persisted")
        self._validate_hashes(artifacts)
        self._validate_lineage(artifacts)
        self._validate_ticket_rules(artifacts, rules)
        with self._session_factory.begin() as session:
            self._persist_sources(session, artifacts)
            run = artifacts.analysis_run
            run_record = AnalysisRunRecord(
                analysis_run_id=run.analysis_run_id,
                run_kind=run.run_kind,
                as_of_at_utc=run.as_of_at_utc,
                status="RUNNING",
                started_at_utc=run.started_at_utc,
                completed_at_utc=None,
                pipeline_version=run.pipeline_version,
                code_revision=run.code_revision,
                config_json=run.config_json,
                config_hash=run.config_hash,
                input_manifest_version=run.input_manifest_version,
                input_manifest_json=run.input_manifest_json,
                input_manifest_hash=run.input_manifest_hash,
                replay_of_run_id=run.replay_of_run_id,
            )
            session.add(run_record)
            session.flush()

            session.add_all(
                AnalysisRunMatchRecord(
                    analysis_run_id=context.analysis_run_id,
                    internal_match_id=context.match_id,
                    market_odds_snapshot_id=context.market_odds_snapshot_id,
                    sporttery_bonus_snapshot_id=context.sporttery_bonus_snapshot_id,
                    manual_quant_input_id=context.manual_quant_input_id,
                    context_json=context.context_json,
                    context_hash=context.context_hash,
                )
                for context in artifacts.match_contexts
            )
            self._persist_predictions(session, artifacts)
            self._persist_betting(session, artifacts)
            self._persist_risk(session, artifacts)
            session.flush()
            run_record.status = AnalysisRunStatus.COMPLETED.value
            run_record.completed_at_utc = run.completed_at_utc

    def _persist_sources(self, session: Session, artifacts: AnalysisArtifacts) -> None:
        provider_codes = {
            mapping.provider_code for mapping in artifacts.provider_mappings
        } | {
            snapshot.provider_code for snapshot in artifacts.market_odds_snapshots
        } | {
            snapshot.provider_code for snapshot in artifacts.sporttery_bonus_snapshots
        }
        for provider_code in sorted(provider_codes):
            provider_id = stable_id("provider", provider_code)
            existing = session.get(ProviderRecord, provider_id)
            values = {
                "code": provider_code,
                "name": provider_code.replace("_", " ").title(),
                "provider_kind": _provider_kind(provider_code),
            }
            if existing is None:
                session.add(
                    ProviderRecord(
                        provider_id=provider_id,
                        **values,
                    )
                )
            else:
                _assert_record_fields(existing, values, f"provider {provider_id}")
        bookmaker_codes = {
            snapshot.bookmaker_code for snapshot in artifacts.market_odds_snapshots
        }
        for bookmaker_code in sorted(bookmaker_codes):
            bookmaker_id = stable_id("bookmaker", bookmaker_code)
            existing = session.get(BookmakerRecord, bookmaker_id)
            values = {
                "code": bookmaker_code,
                "name": bookmaker_code.replace("_", " ").title(),
            }
            if existing is None:
                session.add(
                    BookmakerRecord(
                        bookmaker_id=bookmaker_id,
                        **values,
                    )
                )
            else:
                _assert_record_fields(existing, values, f"bookmaker {bookmaker_id}")
        for competition in artifacts.competitions:
            existing = session.get(CompetitionRecord, competition.competition_id)
            values = {
                "canonical_key": competition.canonical_key,
                "name": competition.name,
                "country_code": competition.country_code,
            }
            if existing is None:
                session.add(
                    CompetitionRecord(
                        competition_id=competition.competition_id,
                        **values,
                    )
                )
            else:
                _assert_record_fields(
                    existing, values, f"competition {competition.competition_id}"
                )
        for team in artifacts.teams:
            existing = session.get(TeamRecord, team.team_id)
            values = {
                "canonical_key": team.canonical_key,
                "name": team.name,
                "team_type": team.team_type.value,
            }
            if existing is None:
                session.add(
                    TeamRecord(
                        team_id=team.team_id,
                        **values,
                    )
                )
            else:
                _assert_record_fields(existing, values, f"team {team.team_id}")
        session.flush()
        created_at = artifacts.analysis_run.started_at_utc
        for match in artifacts.matches:
            existing = session.get(MatchRecord, match.match_id)
            values = {
                "competition_id": match.competition_id,
                "home_team_id": match.home_team_id,
                "away_team_id": match.away_team_id,
                "kickoff_at_utc": match.kickoff_at_utc,
                "status": match.status.value,
                "available_at_utc": match.available_at_utc,
            }
            if existing is None:
                session.add(
                    MatchRecord(
                        internal_match_id=match.match_id,
                        **values,
                        created_at_utc=created_at,
                    )
                )
            else:
                _assert_record_fields(existing, values, f"match {match.match_id}")
        session.flush()
        for manual_input in artifacts.manual_quant_inputs:
            existing = session.get(ManualQuantInputRecord, manual_input.input_id)
            if existing is not None:
                self._assert_manual_input_matches(session, existing, manual_input)
                continue
            session.add(
                ManualQuantInputRecord(
                    input_id=manual_input.input_id,
                    internal_match_id=manual_input.match_id,
                    market_key=manual_input.market.canonical,
                    market_type=manual_input.market.market_type.value,
                    handicap_value=manual_input.market.handicap_value,
                    available_at_utc=manual_input.available_at_utc,
                    payload_hash=manual_input.payload_hash,
                )
            )
            session.flush()
            session.add_all(
                ManualQuantInputOutcomeRecord(
                    input_id=manual_input.input_id,
                    selection_key=selection.value,
                    probability=probability,
                )
                for selection, probability in manual_input.probabilities.items()
            )
        for mapping in artifacts.provider_mappings:
            existing = session.get(ProviderMatchMappingRecord, mapping.mapping_id)
            values = {
                "provider_id": stable_id("provider", mapping.provider_code),
                "external_namespace": mapping.external_namespace,
                "external_match_id": mapping.external_match_id,
                "internal_match_id": mapping.internal_match_id,
                "resolution_method": mapping.resolution_method,
                "confidence": mapping.confidence,
                "available_at_utc": mapping.available_at_utc,
                "supersedes_mapping_id": None,
            }
            if existing is None:
                session.add(
                    ProviderMatchMappingRecord(
                        mapping_id=mapping.mapping_id,
                        **values,
                    )
                )
            else:
                _assert_record_fields(existing, values, f"mapping {mapping.mapping_id}")
        for snapshot in artifacts.market_odds_snapshots:
            existing = session.get(MarketOddsSnapshotRecord, snapshot.snapshot_id)
            if existing is not None:
                self._assert_market_snapshot_matches(session, existing, snapshot)
                continue
            session.add(
                MarketOddsSnapshotRecord(
                    snapshot_id=snapshot.snapshot_id,
                    internal_match_id=snapshot.match_id,
                    provider_id=stable_id("provider", snapshot.provider_code),
                    bookmaker_id=stable_id("bookmaker", snapshot.bookmaker_code),
                    market_key=snapshot.market.canonical,
                    market_type=snapshot.market.market_type.value,
                    handicap_value=snapshot.market.handicap_value,
                    captured_at_utc=snapshot.captured_at_utc,
                    available_at_utc=snapshot.available_at_utc,
                    ingested_at_utc=snapshot.ingested_at_utc,
                    source_snapshot_key=snapshot.source_snapshot_key,
                    payload_hash=snapshot.payload_hash,
                )
            )
            session.flush()
            session.add_all(
                MarketOddsQuoteRecord(
                    snapshot_id=snapshot.snapshot_id,
                    selection_key=quote.selection.value,
                    odds=quote.odds,
                )
                for quote in snapshot.quotes
            )
        for snapshot in artifacts.sporttery_bonus_snapshots:
            existing = session.get(SportteryBonusSnapshotRecord, snapshot.snapshot_id)
            if existing is not None:
                self._assert_sporttery_snapshot_matches(session, existing, snapshot)
                continue
            session.add(
                SportteryBonusSnapshotRecord(
                    snapshot_id=snapshot.snapshot_id,
                    internal_match_id=snapshot.match_id,
                    provider_id=stable_id("provider", snapshot.provider_code),
                    sporttery_match_no=snapshot.sporttery_match_no,
                    market_key=snapshot.market.canonical,
                    market_type=snapshot.market.market_type.value,
                    handicap_value=snapshot.market.handicap_value,
                    sale_status=snapshot.sale_status.value,
                    captured_at_utc=snapshot.captured_at_utc,
                    available_at_utc=snapshot.available_at_utc,
                    ingested_at_utc=snapshot.ingested_at_utc,
                    source_snapshot_key=snapshot.source_snapshot_key,
                    payload_hash=snapshot.payload_hash,
                )
            )
            session.flush()
            session.add_all(
                SportteryBonusQuoteRecord(
                    snapshot_id=snapshot.snapshot_id,
                    selection_key=quote.selection.value,
                    fixed_bonus=quote.fixed_bonus,
                )
                for quote in snapshot.quotes
            )
        session.flush()

    def _persist_predictions(self, session: Session, artifacts: AnalysisArtifacts) -> None:
        for prediction in artifacts.market_predictions:
            session.add(
                MarketProbabilityRecord(
                    market_probability_id=prediction.prediction_id,
                    analysis_run_id=prediction.analysis_run_id,
                    internal_match_id=prediction.match_id,
                    market_key=prediction.market.canonical,
                    market_type=prediction.market.market_type.value,
                    handicap_value=prediction.market.handicap_value,
                    devig_method=prediction.devig_method,
                    devig_version=prediction.devig_version,
                    overround=prediction.overround,
                    generated_at_utc=prediction.generated_at_utc,
                )
            )
            session.flush()
            session.add_all(
                MarketProbabilityOutcomeRecord(
                    market_probability_id=prediction.prediction_id,
                    selection_key=selection.value,
                    probability=probability,
                )
                for selection, probability in prediction.probabilities.items()
            )
            session.add_all(
                MarketProbabilityInputRecord(
                    market_probability_id=prediction.prediction_id,
                    market_odds_snapshot_id=snapshot_id,
                )
                for snapshot_id in prediction.input_snapshot_ids
            )
        for prediction in artifacts.quant_predictions:
            session.add(
                QuantPredictionRecord(
                    quant_prediction_id=prediction.prediction_id,
                    analysis_run_id=prediction.analysis_run_id,
                    internal_match_id=prediction.match_id,
                    market_key=prediction.market.canonical,
                    market_type=prediction.market.market_type.value,
                    handicap_value=prediction.market.handicap_value,
                    manual_input_id=prediction.manual_input_id,
                    input_payload_hash=prediction.input_payload_hash,
                    method=prediction.method,
                    method_version=prediction.method_version,
                    entered_at_utc=prediction.entered_at_utc,
                )
            )
            session.flush()
            session.add_all(
                QuantPredictionOutcomeRecord(
                    quant_prediction_id=prediction.prediction_id,
                    selection_key=selection.value,
                    probability=probability,
                )
                for selection, probability in prediction.probabilities.items()
            )
        for prediction in artifacts.final_predictions:
            session.add(
                FinalPredictionRecord(
                    final_prediction_id=prediction.prediction_id,
                    analysis_run_id=prediction.analysis_run_id,
                    internal_match_id=prediction.match_id,
                    market_key=prediction.market.canonical,
                    market_type=prediction.market.market_type.value,
                    handicap_value=prediction.market.handicap_value,
                    market_probability_id=prediction.market_prediction_id,
                    quant_prediction_id=prediction.quant_prediction_id,
                    llm_assessment_id=prediction.llm_assessment_id,
                    fusion_policy=prediction.fusion_policy.value,
                    fusion_version=prediction.fusion_version,
                    fusion_config_json=prediction.fusion_config_json,
                    fallback_code=prediction.fallback_code,
                    confidence=prediction.confidence,
                    generated_at_utc=prediction.generated_at_utc,
                )
            )
            session.flush()
            session.add_all(
                FinalPredictionOutcomeRecord(
                    final_prediction_id=prediction.prediction_id,
                    selection_key=selection.value,
                    probability=probability,
                )
                for selection, probability in prediction.probabilities.items()
            )

    def _persist_betting(self, session: Session, artifacts: AnalysisArtifacts) -> None:
        for candidate in artifacts.selection_candidates:
            session.add(
                BetCandidateRecord(
                    candidate_id=candidate.candidate_id,
                    analysis_run_id=candidate.analysis_run_id,
                    internal_match_id=candidate.match_id,
                    final_prediction_id=candidate.final_prediction_id,
                    sporttery_bonus_snapshot_id=candidate.sporttery_bonus_snapshot_id,
                    market_key=candidate.market.canonical,
                    selection_key=candidate.selection.value,
                    probability_used=candidate.probability,
                    fixed_bonus=candidate.fixed_bonus,
                    break_even_probability=candidate.break_even_probability,
                    ev=candidate.ev,
                    eligibility_status=candidate.status.value,
                    rejection_code=candidate.rejection_code,
                )
            )
        session.flush()
        for ticket in artifacts.ticket_candidates:
            session.add(
                TicketCandidateRecord(
                    ticket_candidate_id=ticket.ticket_candidate_id,
                    analysis_run_id=ticket.analysis_run_id,
                    pass_type=ticket.pass_type.value,
                    atomic_bet_count=ticket.atomic_bet_count,
                    base_stake_fen=ticket.base_stake_fen,
                    joint_probability=ticket.joint_probability,
                    gross_payout_fen=ticket.gross_payout_fen,
                    expected_gross_payout_fen=ticket.expected_gross_payout_fen,
                    expected_profit_fen=ticket.expected_profit_fen,
                    expected_roi=ticket.expected_roi,
                    payout_policy_version=ticket.payout_policy_version,
                )
            )
            session.flush()
            session.add_all(
                TicketCandidateLegRecord(
                    ticket_candidate_id=ticket.ticket_candidate_id,
                    leg_no=index,
                    candidate_id=leg.candidate_id,
                    internal_match_id=leg.match_id,
                )
                for index, leg in enumerate(ticket.legs, start=1)
            )
        session.flush()
        for portfolio in artifacts.portfolios:
            session.add(
                PortfolioRecord(
                    portfolio_id=portfolio.portfolio_id,
                    analysis_run_id=portfolio.analysis_run_id,
                    budget_fen=portfolio.budget_fen,
                    total_stake_fen=portfolio.total_stake_fen,
                    unused_budget_fen=portfolio.unused_budget_fen,
                    status=portfolio.status.value,
                    no_bet_reason=(
                        portfolio.no_bet_reason.value if portfolio.no_bet_reason else None
                    ),
                    strategy_version=portfolio.strategy_version,
                    strategy_config_json=json.dumps(
                        portfolio.constraints.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            session.flush()
            session.add(
                PortfolioCashPositionRecord(
                    cash_position_id=portfolio.cash_position.position_id,
                    portfolio_id=portfolio.portfolio_id,
                    amount_fen=portfolio.cash_position.amount_fen,
                    expected_profit_fen=portfolio.cash_position.expected_profit_fen,
                )
            )
            for ticket in portfolio.tickets:
                candidate = ticket.candidate
                session.add(
                    TicketRecord(
                        ticket_id=ticket.ticket_id,
                        portfolio_id=portfolio.portfolio_id,
                        ticket_candidate_id=candidate.ticket_candidate_id,
                        ticket_no=ticket.ticket_no,
                        pass_type=candidate.pass_type.value,
                        role=None,
                        multiplier=ticket.multiplier,
                        atomic_bet_count=candidate.atomic_bet_count,
                        base_stake_fen=candidate.base_stake_fen,
                        stake_fen=ticket.stake_fen,
                        potential_gross_payout_fen=ticket.potential_gross_payout_fen,
                        expected_gross_payout_fen=ticket.expected_gross_payout_fen,
                        expected_profit_fen=ticket.expected_profit_fen,
                        expected_roi=ticket.expected_roi,
                        probability_any_payout=ticket.probability_any_payout,
                        payout_policy_version=candidate.payout_policy_version,
                    )
                )
                session.flush()
                session.add_all(
                    TicketLegRecord(
                        ticket_id=ticket.ticket_id,
                        leg_no=index,
                        candidate_id=leg.candidate_id,
                        internal_match_id=leg.match_id,
                    )
                    for index, leg in enumerate(candidate.legs, start=1)
                )

    def _persist_risk(self, session: Session, artifacts: AnalysisArtifacts) -> None:
        for report in artifacts.portfolio_risk_reports:
            session.add(
                PortfolioRiskReportRecord(
                    risk_report_id=report.risk_report_id,
                    analysis_run_id=report.analysis_run_id,
                    portfolio_id=report.portfolio_id,
                    policy_version=report.policy_version,
                    budget_fen=report.budget_fen,
                    total_stake_fen=report.total_stake_fen,
                    cash_fen=report.cash_fen,
                    cash_ratio=report.cash_ratio,
                    expected_profit_fen=report.expected_profit_fen,
                    total_stake_at_risk_fen=report.total_stake_at_risk_fen,
                    max_single_ticket_exposure_fen=(
                        report.max_single_ticket_exposure_fen
                    ),
                    max_match_exposure_fen=report.max_match_exposure_fen,
                )
            )
            session.flush()
            session.add_all(
                PortfolioMatchExposureRecord(
                    exposure_id=exposure.exposure_id,
                    risk_report_id=exposure.risk_report_id,
                    internal_match_id=exposure.match_id,
                    exposed_stake_fen=exposure.exposed_stake_fen,
                    budget_ratio=exposure.budget_ratio,
                    deployed_ratio=exposure.deployed_ratio,
                    ticket_count=len(exposure.ticket_ids),
                    ticket_ids_json=json.dumps(
                        exposure.ticket_ids, separators=(",", ":")
                    ),
                )
                for exposure in report.match_exposures
            )
            session.add_all(
                PortfolioSelectionExposureRecord(
                    exposure_id=exposure.exposure_id,
                    risk_report_id=exposure.risk_report_id,
                    internal_match_id=exposure.match_id,
                    market_key=exposure.market.canonical,
                    selection_key=exposure.selection.value,
                    exposed_stake_fen=exposure.exposed_stake_fen,
                    budget_ratio=exposure.budget_ratio,
                    deployed_ratio=exposure.deployed_ratio,
                    ticket_count=len(exposure.ticket_ids),
                    ticket_ids_json=json.dumps(
                        exposure.ticket_ids, separators=(",", ":")
                    ),
                )
                for exposure in report.selection_exposures
            )
            for result in report.stress_results:
                session.add(
                    PortfolioStressResultRecord(
                        scenario_id=result.scenario_id,
                        risk_report_id=result.risk_report_id,
                        portfolio_id=result.portfolio_id,
                        scenario_key=result.scenario_key,
                        policy_version=result.policy_version,
                        outcomes_json=json.dumps(
                            [
                                {
                                    "match_id": outcome.match_id,
                                    "selection": outcome.selection.value,
                                }
                                for outcome in result.outcomes
                            ],
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        is_complete=result.is_complete,
                        scenario_exposed_stake_fen=(
                            result.scenario_exposed_stake_fen
                        ),
                        scenario_exposure_ratio=result.scenario_exposure_ratio,
                        gross_payout_fen=result.gross_payout_fen,
                        ending_capital_fen=result.ending_capital_fen,
                        profit_loss_fen=result.profit_loss_fen,
                        capital_recovery_ratio=result.capital_recovery_ratio,
                        minimum_ending_capital_fen=(
                            result.minimum_ending_capital_fen
                        ),
                        maximum_ending_capital_fen=(
                            result.maximum_ending_capital_fen
                        ),
                    )
                )
                session.flush()
                session.add_all(
                    PortfolioStressTicketResultRecord(
                        scenario_id=result.scenario_id,
                        ticket_id=ticket_result.ticket_id,
                        result_state=ticket_result.state.value,
                        gross_payout_fen=ticket_result.gross_payout_fen,
                    )
                    for ticket_result in result.ticket_results
                )

    def _validate_ticket_rules(
        self,
        artifacts: AnalysisArtifacts,
        rules: SportteryRules,
    ) -> None:
        for portfolio in artifacts.portfolios:
            for ticket in portfolio.tickets:
                expected_stake = calculate_stake_fen(
                    ticket.candidate.atomic_bet_count,
                    ticket.multiplier,
                    rules,
                )
                if expected_stake != ticket.stake_fen:
                    raise ValueError("ticket stake does not match versioned Sporttery rules")

    def _validate_hashes(self, artifacts: AnalysisArtifacts) -> None:
        run = artifacts.analysis_run
        if _sha256(run.config_json) != run.config_hash:
            raise ValueError("AnalysisRun config hash does not match config JSON")
        if _sha256(run.input_manifest_json) != run.input_manifest_hash:
            raise ValueError("AnalysisRun manifest hash does not match manifest JSON")
        for context in artifacts.match_contexts:
            if _sha256(context.context_json) != context.context_hash:
                raise ValueError(
                    f"match context hash does not match JSON: {context.match_id}"
                )

    def _validate_lineage(self, artifacts: AnalysisArtifacts) -> None:
        run_id = artifacts.analysis_run.analysis_run_id
        match_ids = {match.match_id for match in artifacts.matches}
        market_sources = {
            snapshot.snapshot_id: snapshot
            for snapshot in artifacts.market_odds_snapshots
        }
        bonus_sources = {
            snapshot.snapshot_id: snapshot
            for snapshot in artifacts.sporttery_bonus_snapshots
        }
        if (
            len(market_sources) != len(artifacts.market_odds_snapshots)
            or len(bonus_sources) != len(artifacts.sporttery_bonus_snapshots)
        ):
            raise ValueError("source snapshot IDs must be unique")
        if any(
            mapping.internal_match_id not in match_ids
            for mapping in artifacts.provider_mappings
        ):
            raise ValueError("provider mapping references an unknown match")
        for context in artifacts.match_contexts:
            odds = market_sources.get(context.market_odds_snapshot_id)
            bonus = bonus_sources.get(context.sporttery_bonus_snapshot_id)
            if (
                context.analysis_run_id != run_id
                or odds is None
                or bonus is None
                or odds.match_id != context.match_id
                or bonus.match_id != context.match_id
            ):
                raise ValueError("match context has inconsistent source lineage")
        context_by_match = {
            context.match_id: context for context in artifacts.match_contexts
        }
        market_predictions = {
            prediction.prediction_id: prediction
            for prediction in artifacts.market_predictions
        }
        quant_predictions = {
            prediction.prediction_id: prediction
            for prediction in artifacts.quant_predictions
        }
        final_predictions = {
            prediction.prediction_id: prediction
            for prediction in artifacts.final_predictions
        }
        selections = {
            candidate.candidate_id: candidate
            for candidate in artifacts.selection_candidates
        }
        manual_inputs = {
            item.input_id: item for item in artifacts.manual_quant_inputs
        }
        for context in artifacts.match_contexts:
            manual_input = manual_inputs.get(context.manual_quant_input_id)
            if manual_input is None or manual_input.match_id != context.match_id:
                raise ValueError("match context has inconsistent manual input lineage")
        for prediction in artifacts.market_predictions:
            sources = [
                market_sources.get(snapshot_id)
                for snapshot_id in prediction.input_snapshot_ids
            ]
            context = context_by_match.get(prediction.match_id)
            if (
                prediction.analysis_run_id != run_id
                or context is None
                or not sources
                or any(
                    source is None
                    or source.match_id != prediction.match_id
                    or source.market != prediction.market
                    for source in sources
                )
                or set(prediction.input_snapshot_ids)
                != {context.market_odds_snapshot_id}
            ):
                raise ValueError("market prediction has inconsistent input lineage")
        ticket_candidates = {
            candidate.ticket_candidate_id: candidate
            for candidate in artifacts.ticket_candidates
        }
        for prediction in artifacts.quant_predictions:
            manual_input = manual_inputs.get(prediction.manual_input_id)
            context = context_by_match.get(prediction.match_id)
            if (
                prediction.analysis_run_id != run_id
                or context is None
                or manual_input is None
                or manual_input.match_id != prediction.match_id
                or manual_input.market != prediction.market
                or manual_input.payload_hash != prediction.input_payload_hash
                or prediction.manual_input_id
                != context.manual_quant_input_id
            ):
                raise ValueError("quant prediction has inconsistent input lineage")
        for prediction in artifacts.final_predictions:
            market_prediction = market_predictions.get(prediction.market_prediction_id)
            quant_prediction = quant_predictions.get(prediction.quant_prediction_id)
            if (
                prediction.analysis_run_id != run_id
                or (
                    prediction.market_prediction_id is not None
                    and market_prediction is None
                )
                or (
                    prediction.quant_prediction_id is not None
                    and quant_prediction is None
                )
            ):
                raise ValueError("final prediction references a missing upstream prediction")
            upstream = (market_prediction, quant_prediction)
            if any(
                item is not None
                and (
                    item.analysis_run_id != run_id
                    or item.match_id != prediction.match_id
                    or item.market != prediction.market
                )
                for item in upstream
            ):
                raise ValueError("final prediction has inconsistent upstream lineage")
        for candidate in artifacts.selection_candidates:
            prediction = final_predictions.get(candidate.final_prediction_id)
            bonus = bonus_sources.get(candidate.sporttery_bonus_snapshot_id)
            context = context_by_match.get(candidate.match_id)
            if (
                candidate.analysis_run_id != run_id
                or context is None
                or prediction is None
                or bonus is None
                or prediction.match_id != candidate.match_id
                or prediction.market != candidate.market
                or bonus.match_id != candidate.match_id
                or bonus.market != candidate.market
                or candidate.sporttery_bonus_snapshot_id
                != context.sporttery_bonus_snapshot_id
            ):
                raise ValueError("selection candidate has inconsistent lineage")
        for ticket in artifacts.ticket_candidates:
            if ticket.analysis_run_id != run_id or any(
                selections.get(leg.candidate_id) != leg for leg in ticket.legs
            ):
                raise ValueError("ticket candidate has inconsistent lineage")
        for portfolio in artifacts.portfolios:
            if portfolio.analysis_run_id != run_id or any(
                ticket_candidates.get(ticket.candidate.ticket_candidate_id)
                != ticket.candidate
                for ticket in portfolio.tickets
            ):
                raise ValueError("portfolio has inconsistent candidate lineage")
        portfolios = {
            portfolio.portfolio_id: portfolio for portfolio in artifacts.portfolios
        }
        reports = {
            report.portfolio_id: report
            for report in artifacts.portfolio_risk_reports
        }
        if len(reports) != len(artifacts.portfolio_risk_reports) or set(reports) != set(
            portfolios
        ):
            raise ValueError("analysis must contain one risk report per portfolio")
        for portfolio_id, report in reports.items():
            portfolio = portfolios[portfolio_id]
            ticket_ids = {ticket.ticket_id for ticket in portfolio.tickets}
            if (
                report.analysis_run_id != run_id
                or report.budget_fen != portfolio.budget_fen
                or report.total_stake_fen != portfolio.total_stake_fen
                or report.cash_fen != portfolio.cash_position.amount_fen
                or any(
                    set(exposure.ticket_ids) - ticket_ids
                    for exposure in (
                        *report.match_exposures,
                        *report.selection_exposures,
                    )
                )
                or any(
                    {item.ticket_id for item in result.ticket_results} != ticket_ids
                    for result in report.stress_results
                )
            ):
                raise ValueError("portfolio risk report has inconsistent lineage")

    @staticmethod
    def _assert_manual_input_matches(session: Session, record: object, value: object) -> None:
        stored = {
            row.selection_key: row.probability
            for row in session.scalars(
                select(ManualQuantInputOutcomeRecord).where(
                    ManualQuantInputOutcomeRecord.input_id == record.input_id
                )
            )
        }
        expected = {
            selection.value: probability
            for selection, probability in value.probabilities.items()
        }
        if (
            record.internal_match_id != value.match_id
            or record.market_key != value.market.canonical
            or record.market_type != value.market.market_type.value
            or record.handicap_value != value.market.handicap_value
            or record.available_at_utc != value.available_at_utc
            or record.payload_hash != value.payload_hash
            or stored != expected
        ):
            raise ValueError(f"manual quant input ID collision: {value.input_id}")

    @staticmethod
    def _assert_market_snapshot_matches(session: Session, record: object, value: object) -> None:
        stored = {
            row.selection_key: row.odds
            for row in session.scalars(
                select(MarketOddsQuoteRecord).where(
                    MarketOddsQuoteRecord.snapshot_id == record.snapshot_id
                )
            )
        }
        expected = {quote.selection.value: quote.odds for quote in value.quotes}
        if (
            record.internal_match_id != value.match_id
            or record.provider_id != stable_id("provider", value.provider_code)
            or record.bookmaker_id != stable_id("bookmaker", value.bookmaker_code)
            or record.market_key != value.market.canonical
            or record.market_type != value.market.market_type.value
            or record.handicap_value != value.market.handicap_value
            or record.captured_at_utc != value.captured_at_utc
            or record.available_at_utc != value.available_at_utc
            or record.ingested_at_utc != value.ingested_at_utc
            or record.source_snapshot_key != value.source_snapshot_key
            or record.payload_hash != value.payload_hash
            or stored != expected
        ):
            raise ValueError(f"market snapshot ID collision: {value.snapshot_id}")

    @staticmethod
    def _assert_sporttery_snapshot_matches(
        session: Session, record: object, value: object
    ) -> None:
        stored = {
            row.selection_key: row.fixed_bonus
            for row in session.scalars(
                select(SportteryBonusQuoteRecord).where(
                    SportteryBonusQuoteRecord.snapshot_id == record.snapshot_id
                )
            )
        }
        expected = {quote.selection.value: quote.fixed_bonus for quote in value.quotes}
        if (
            record.internal_match_id != value.match_id
            or record.provider_id != stable_id("provider", value.provider_code)
            or record.sporttery_match_no != value.sporttery_match_no
            or record.market_key != value.market.canonical
            or record.market_type != value.market.market_type.value
            or record.handicap_value != value.market.handicap_value
            or record.sale_status != value.sale_status.value
            or record.captured_at_utc != value.captured_at_utc
            or record.available_at_utc != value.available_at_utc
            or record.ingested_at_utc != value.ingested_at_utc
            or record.source_snapshot_key != value.source_snapshot_key
            or record.payload_hash != value.payload_hash
            or stored != expected
        ):
            raise ValueError(f"Sporttery snapshot ID collision: {value.snapshot_id}")

    def table_counts(self) -> dict[str, int]:
        tables: Iterable[type] = (
            MatchRecord,
            MarketOddsSnapshotRecord,
            SportteryBonusSnapshotRecord,
            ManualQuantInputRecord,
            AnalysisRunRecord,
            MarketProbabilityRecord,
            QuantPredictionRecord,
            FinalPredictionRecord,
            BetCandidateRecord,
            TicketCandidateRecord,
            PortfolioRecord,
            PortfolioCashPositionRecord,
            TicketRecord,
            PortfolioRiskReportRecord,
            PortfolioMatchExposureRecord,
            PortfolioSelectionExposureRecord,
            PortfolioStressResultRecord,
            PortfolioStressTicketResultRecord,
        )
        with self._session_factory() as session:
            return {
                table.__tablename__: session.scalar(select(func.count()).select_from(table)) or 0
                for table in tables
            }

    def load_input_manifest(self, analysis_run_id: str) -> StoredInputManifest:
        with self._session_factory() as session:
            run = session.get(AnalysisRunRecord, analysis_run_id)
            if run is None:
                raise KeyError(f"unknown AnalysisRun: {analysis_run_id}")
            actual_hash = _sha256(run.input_manifest_json)
            if actual_hash != run.input_manifest_hash:
                raise ValueError("stored AnalysisRun manifest failed hash verification")
            return StoredInputManifest(
                analysis_run_id=run.analysis_run_id,
                version=run.input_manifest_version,
                manifest_json=run.input_manifest_json,
                manifest_hash=run.input_manifest_hash,
            )


def _provider_kind(provider_code: str) -> str:
    if "SPORTTERY" in provider_code:
        return "SPORTTERY"
    if "ODDS" in provider_code or "MARKET" in provider_code:
        return "MARKET_ODDS"
    return "FIXTURE"


def _assert_record_fields(record: object, expected: dict[str, object], label: str) -> None:
    mismatched = [
        field
        for field, value in expected.items()
        if getattr(record, field) != value
    ]
    if mismatched:
        raise ValueError(
            f"immutable {label} conflicts on fields: {', '.join(mismatched)}"
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
