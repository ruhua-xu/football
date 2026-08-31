from __future__ import annotations

from pydantic import model_validator

from football_system.domain.analysis import AnalysisMatchContext, AnalysisRun
from football_system.domain.betting import (
    Portfolio,
    SelectionCandidate,
    TicketCandidate,
)
from football_system.domain.common import DomainModel
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    Match,
    ProviderMatchMapping,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.prediction import (
    FinalPrediction,
    ManualQuantInput,
    MarketPrediction,
    QuantPrediction,
)
from football_system.domain.risk import PortfolioRiskReport
from football_system.domain.services.risk import analyze_portfolio_risk


class StoredInputManifest(DomainModel):
    analysis_run_id: str
    version: str
    manifest_json: str
    manifest_hash: str


class AnalysisArtifacts(DomainModel):
    competitions: tuple[Competition, ...]
    teams: tuple[Team, ...]
    matches: tuple[Match, ...]
    provider_mappings: tuple[ProviderMatchMapping, ...]
    market_odds_snapshots: tuple[MarketOddsSnapshot, ...]
    sporttery_bonus_snapshots: tuple[SportteryBonusSnapshot, ...]
    manual_quant_inputs: tuple[ManualQuantInput, ...]
    analysis_run: AnalysisRun
    match_contexts: tuple[AnalysisMatchContext, ...]
    market_predictions: tuple[MarketPrediction, ...]
    quant_predictions: tuple[QuantPrediction, ...]
    final_predictions: tuple[FinalPrediction, ...]
    selection_candidates: tuple[SelectionCandidate, ...]
    ticket_candidates: tuple[TicketCandidate, ...]
    portfolios: tuple[Portfolio, ...]
    portfolio_risk_reports: tuple[PortfolioRiskReport, ...]

    @model_validator(mode="after")
    def validate_lineage(self) -> AnalysisArtifacts:
        run_id = self.analysis_run.analysis_run_id
        match_ids = {match.match_id for match in self.matches}
        run_scoped = (
            *self.match_contexts,
            *self.market_predictions,
            *self.quant_predictions,
            *self.final_predictions,
            *self.selection_candidates,
            *self.ticket_candidates,
            *self.portfolios,
            *self.portfolio_risk_reports,
        )
        if any(item.analysis_run_id != run_id for item in run_scoped):
            raise ValueError("analysis artifacts contain a cross-run reference")
        match_scoped = (
            *self.match_contexts,
            *self.market_predictions,
            *self.quant_predictions,
            *self.final_predictions,
            *self.selection_candidates,
            *self.manual_quant_inputs,
        )
        if any(item.match_id not in match_ids for item in match_scoped):
            raise ValueError("analysis artifacts reference an unknown match")
        if (
            len(self.match_contexts) != len(match_ids)
            or {context.match_id for context in self.match_contexts} != match_ids
        ):
            raise ValueError("analysis must contain one context per match")
        context_by_match = {
            context.match_id: context for context in self.match_contexts
        }
        if any(
            mapping.internal_match_id not in match_ids
            for mapping in self.provider_mappings
        ):
            raise ValueError("provider mapping references an unknown match")

        odds_by_id = _unique_index(
            self.market_odds_snapshots, "snapshot_id", "market odds snapshot"
        )
        bonus_by_id = _unique_index(
            self.sporttery_bonus_snapshots,
            "snapshot_id",
            "Sporttery bonus snapshot",
        )
        manual_by_id = _unique_index(
            self.manual_quant_inputs, "input_id", "manual quant input"
        )
        market_by_id = _unique_index(
            self.market_predictions, "prediction_id", "market prediction"
        )
        quant_by_id = _unique_index(
            self.quant_predictions, "prediction_id", "quant prediction"
        )
        final_by_id = _unique_index(
            self.final_predictions, "prediction_id", "final prediction"
        )
        selection_by_id = _unique_index(
            self.selection_candidates, "candidate_id", "selection candidate"
        )
        ticket_by_id = _unique_index(
            self.ticket_candidates, "ticket_candidate_id", "ticket candidate"
        )
        portfolio_by_id = _unique_index(self.portfolios, "portfolio_id", "portfolio")
        risk_by_portfolio = _unique_index(
            self.portfolio_risk_reports, "portfolio_id", "portfolio risk report"
        )
        for context in self.match_contexts:
            odds = odds_by_id.get(context.market_odds_snapshot_id)
            bonus = bonus_by_id.get(context.sporttery_bonus_snapshot_id)
            if (
                odds is None
                or bonus is None
                or odds.match_id != context.match_id
                or bonus.match_id != context.match_id
                or odds.market != bonus.market
            ):
                raise ValueError("match context references inconsistent source snapshots")
        for prediction in self.market_predictions:
            snapshots = [odds_by_id.get(item) for item in prediction.input_snapshot_ids]
            context = context_by_match[prediction.match_id]
            if not snapshots or any(
                snapshot is None
                or snapshot.match_id != prediction.match_id
                or snapshot.market != prediction.market
                for snapshot in snapshots
            ) or set(prediction.input_snapshot_ids) != {
                context.market_odds_snapshot_id
            }:
                raise ValueError("market prediction has inconsistent source lineage")
        for prediction in self.quant_predictions:
            manual_input = manual_by_id.get(prediction.manual_input_id)
            context = context_by_match[prediction.match_id]
            if (
                manual_input is None
                or manual_input.match_id != prediction.match_id
                or manual_input.market != prediction.market
                or manual_input.payload_hash != prediction.input_payload_hash
                or prediction.manual_input_id != context.manual_quant_input_id
            ):
                raise ValueError("quant prediction has inconsistent source lineage")
        for prediction in self.final_predictions:
            market_prediction = market_by_id.get(prediction.market_prediction_id)
            quant_prediction = quant_by_id.get(prediction.quant_prediction_id)
            if (
                prediction.market_prediction_id is not None
                and market_prediction is None
            ) or (
                prediction.quant_prediction_id is not None
                and quant_prediction is None
            ):
                raise ValueError("final prediction references a missing input")
            upstream = (market_prediction, quant_prediction)
            if any(
                item is not None
                and (
                    item.match_id != prediction.match_id
                    or item.market != prediction.market
                )
                for item in upstream
            ):
                raise ValueError("final prediction has inconsistent source lineage")
        for candidate in self.selection_candidates:
            prediction = final_by_id.get(candidate.final_prediction_id)
            bonus = bonus_by_id.get(candidate.sporttery_bonus_snapshot_id)
            context = context_by_match[candidate.match_id]
            if (
                prediction is None
                or bonus is None
                or prediction.match_id != candidate.match_id
                or prediction.market != candidate.market
                or bonus.match_id != candidate.match_id
                or bonus.market != candidate.market
                or candidate.sporttery_bonus_snapshot_id
                != context.sporttery_bonus_snapshot_id
            ):
                raise ValueError("selection candidate has inconsistent source lineage")
        for ticket in self.ticket_candidates:
            if any(selection_by_id.get(leg.candidate_id) != leg for leg in ticket.legs):
                raise ValueError("ticket candidate has inconsistent selection lineage")
        for portfolio in self.portfolios:
            if any(
                ticket_by_id.get(ticket.candidate.ticket_candidate_id)
                != ticket.candidate
                for ticket in portfolio.tickets
            ):
                raise ValueError("portfolio has inconsistent ticket lineage")
            report = risk_by_portfolio.get(portfolio.portfolio_id)
            if report is None or (
                report.budget_fen != portfolio.budget_fen
                or report.total_stake_fen != portfolio.total_stake_fen
                or report.cash_fen != portfolio.cash_position.amount_fen
            ):
                raise ValueError("portfolio has inconsistent risk lineage")
        if set(risk_by_portfolio) != set(portfolio_by_id):
            raise ValueError("analysis must contain one risk report per portfolio")
        for report in self.portfolio_risk_reports:
            portfolio = portfolio_by_id[report.portfolio_id]
            if report != analyze_portfolio_risk(portfolio):
                raise ValueError("portfolio risk report does not match frozen portfolio")
            ticket_ids = {ticket.ticket_id for ticket in portfolio.tickets}
            exposed_ticket_ids = {
                ticket_id
                for exposure in (*report.match_exposures, *report.selection_exposures)
                for ticket_id in exposure.ticket_ids
            }
            if exposed_ticket_ids != ticket_ids:
                raise ValueError("risk exposure has inconsistent ticket lineage")
            if any(
                {item.ticket_id for item in result.ticket_results} != ticket_ids
                for result in report.stress_results
            ):
                raise ValueError("stress result has inconsistent ticket lineage")
        return self


def _unique_index(items: tuple, field: str, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        identity = getattr(item, field)
        if identity in result:
            raise ValueError(f"duplicate {label}: {identity}")
        result[identity] = item
    return result
