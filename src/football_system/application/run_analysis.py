from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import Field, model_validator

from football_system.application.models import AnalysisArtifacts
from football_system.application.ports.data_providers import (
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
    ManualQuantBatch,
    ManualQuantProvider,
    MarketOddsBatch,
    MarketOddsProvider,
    SnapshotQuery,
    SportteryBatch,
    SportteryProvider,
)
from football_system.application.ports.repositories import AnalysisRepository
from football_system.config import AppSettings
from football_system.domain.analysis import (
    AnalysisMatchContext,
    AnalysisRun,
    AnalysisRunStatus,
)
from football_system.domain.betting import PortfolioConstraints, SportteryRules
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    new_id,
    stable_id,
    utc_now,
)
from football_system.domain.prediction import (
    FusionConfig,
    FusionInputs,
    FusionInputsUnavailable,
    FusionPolicyName,
    ManualQuantInput,
    MarketPrediction,
    QuantPrediction,
)
from football_system.domain.market import MarketType, UnsupportedMarketError
from football_system.domain.match import (
    Competition,
    MarketOddsSnapshot,
    Match,
    ProviderMatchMapping,
    SportteryBonusSnapshot,
    Team,
)
from football_system.domain.services.betting import (
    build_selection_candidates,
    build_two_leg_ticket_candidates,
)
from football_system.domain.services.fusion import get_fusion_policy
from football_system.domain.services.optimizer import optimize_portfolio
from football_system.domain.services.probability import normalized_inverse_probability
from football_system.domain.services.risk import analyze_portfolio_risk


class RunAnalysisRequest(DomainModel):
    as_of_at_utc: UtcDateTime
    kickoff_from_utc: UtcDateTime
    kickoff_to_utc: UtcDateTime
    budgets_fen: tuple[int, ...]
    fusion_policy: FusionPolicyName
    min_selection_ev: Decimal | None = Field(default=None, ge=0)
    min_ticket_roi: Decimal | None = Field(default=None, ge=0)
    analysis_run_id: Identifier | None = None
    execution_time_utc: UtcDateTime | None = None
    allow_partial_inputs: bool = False
    expected_match_ids: tuple[Identifier, ...] | None = None

    @model_validator(mode="after")
    def validate_request(self) -> RunAnalysisRequest:
        if self.kickoff_from_utc > self.kickoff_to_utc:
            raise ValueError("kickoff window is invalid")
        if not self.budgets_fen or any(budget < 0 for budget in self.budgets_fen):
            raise ValueError("at least one non-negative budget is required")
        if len(self.budgets_fen) != len(set(self.budgets_fen)):
            raise ValueError("budgets must be unique")
        if self.expected_match_ids is not None and len(self.expected_match_ids) != len(
            set(self.expected_match_ids)
        ):
            raise ValueError("expected analysis match IDs must be unique")
        if (
            self.execution_time_utc is not None
            and self.execution_time_utc < self.as_of_at_utc
        ):
            raise ValueError("execution time cannot precede the knowledge cutoff")
        return self


@dataclass(frozen=True)
class _SelectedInputs:
    competitions: tuple[Competition, ...]
    teams: tuple[Team, ...]
    matches: tuple[Match, ...]
    mappings: tuple[ProviderMatchMapping, ...]
    market_snapshots: tuple[MarketOddsSnapshot, ...]
    sporttery_snapshots: tuple[SportteryBonusSnapshot, ...]
    manual_inputs: tuple[ManualQuantInput, ...]


class RunAnalysisService:
    def __init__(
        self,
        fixture_provider: FixtureProvider,
        market_odds_provider: MarketOddsProvider,
        sporttery_provider: SportteryProvider,
        manual_quant_provider: ManualQuantProvider,
        repository: AnalysisRepository,
        settings: AppSettings,
    ) -> None:
        self._fixture_provider = fixture_provider
        self._market_odds_provider = market_odds_provider
        self._sporttery_provider = sporttery_provider
        self._manual_quant_provider = manual_quant_provider
        self._repository = repository
        self._settings = settings

    async def run(self, request: RunAnalysisRequest) -> AnalysisArtifacts:
        started_at = request.execution_time_utc or utc_now()
        run_id = request.analysis_run_id or new_id()
        fixture_batch = await self._fixture_provider.fetch_fixtures(
            FixtureQuery(
                kickoff_from_utc=request.kickoff_from_utc,
                kickoff_to_utc=request.kickoff_to_utc,
                as_of_at_utc=request.as_of_at_utc,
            )
        )
        fixture_batch = FixtureBatch.model_validate(
            fixture_batch.model_dump(mode="python", exclude_computed_fields=True)
        )
        query_matches = _validate_fixture_response(request, fixture_batch)
        match_ids = tuple(match.match_id for match in query_matches)
        snapshot_query = SnapshotQuery(
            match_ids=match_ids,
            as_of_at_utc=request.as_of_at_utc,
        )
        odds_batch, sporttery_batch, manual_quant_batch = await asyncio.gather(
            self._market_odds_provider.fetch_market_odds(snapshot_query),
            self._sporttery_provider.fetch_fixed_bonus(snapshot_query),
            self._manual_quant_provider.fetch_manual_quant(snapshot_query),
        )
        odds_batch = MarketOddsBatch.model_validate(
            odds_batch.model_dump(mode="python", exclude_computed_fields=True)
        )
        sporttery_batch = SportteryBatch.model_validate(
            sporttery_batch.model_dump(mode="python", exclude_computed_fields=True)
        )
        manual_quant_batch = ManualQuantBatch.model_validate(
            manual_quant_batch.model_dump(mode="python", exclude_computed_fields=True)
        )
        selected = _select_analysis_inputs(
            request,
            started_at,
            fixture_batch,
            query_matches,
            odds_batch,
            sporttery_batch,
            manual_quant_batch,
        )
        odds_by_match = _unique_by_match(selected.market_snapshots, "market odds")
        bonus_by_match = _unique_by_match(
            selected.sporttery_snapshots,
            "Sporttery bonus",
        )
        quant_by_match = _unique_by_match(selected.manual_inputs, "manual P_quant")

        market_predictions: list[MarketPrediction] = []
        quant_predictions: list[QuantPrediction] = []
        final_predictions = []
        contexts: list[AnalysisMatchContext] = []
        policy = get_fusion_policy(request.fusion_policy)
        fusion_config = FusionConfig(quant_weight=self._settings.analysis.quant_weight)

        for match in selected.matches:
            odds_snapshot = odds_by_match[match.match_id]
            bonus_snapshot = bonus_by_match[match.match_id]
            quant_input = quant_by_match[match.match_id]
            if odds_snapshot.market.market_type != MarketType.THREE_WAY:
                raise UnsupportedMarketError(
                    f"MVP probability pipeline does not support "
                    f"{odds_snapshot.market.canonical}"
                )
            if not (
                odds_snapshot.market == bonus_snapshot.market == quant_input.market
            ):
                raise ValueError(f"market inputs disagree for match {match.match_id}")
            p_market, overround = normalized_inverse_probability(
                odds_snapshot.three_way_odds()
            )
            market_prediction = MarketPrediction(
                prediction_id=stable_id(
                    "p-market", run_id, match.match_id, odds_snapshot.market.canonical
                ),
                analysis_run_id=run_id,
                match_id=match.match_id,
                market=odds_snapshot.market,
                probabilities=p_market,
                input_snapshot_ids=(odds_snapshot.snapshot_id,),
                overround=overround,
                generated_at_utc=started_at,
            )
            quant_prediction = QuantPrediction(
                prediction_id=stable_id(
                    "p-quant", run_id, match.match_id, quant_input.market.canonical
                ),
                analysis_run_id=run_id,
                match_id=match.match_id,
                market=quant_input.market,
                probabilities=quant_input.probabilities,
                manual_input_id=quant_input.input_id,
                input_payload_hash=quant_input.payload_hash,
                entered_at_utc=quant_input.available_at_utc,
            )
            fusion_inputs = FusionInputs(
                analysis_run_id=run_id,
                match_id=match.match_id,
                market=quant_input.market,
                p_market=market_prediction,
                p_quant=quant_prediction,
            )
            try:
                final_prediction = policy.fuse(
                    fusion_inputs,
                    fusion_config,
                    started_at,
                )
            except FusionInputsUnavailable:
                final_prediction = (
                    get_fusion_policy(FusionPolicyName.QUANT_ONLY_V1)
                    .fuse(fusion_inputs, fusion_config, started_at)
                    .model_copy(update={"fallback_code": "FALLBACK_TO_QUANT_ONLY"})
                )
            context_json = _canonical_json(
                {
                    "match_id": match.match_id,
                    "as_of_at_utc": request.as_of_at_utc,
                    "market_odds_snapshot_id": odds_snapshot.snapshot_id,
                    "sporttery_bonus_snapshot_id": bonus_snapshot.snapshot_id,
                    "manual_quant_input_id": quant_input.input_id,
                    "manual_quant_payload_hash": quant_input.payload_hash,
                }
            )
            contexts.append(
                AnalysisMatchContext(
                    analysis_run_id=run_id,
                    match_id=match.match_id,
                    market_odds_snapshot_id=odds_snapshot.snapshot_id,
                    sporttery_bonus_snapshot_id=bonus_snapshot.snapshot_id,
                    manual_quant_input_id=quant_input.input_id,
                    context_json=context_json,
                    context_hash=_sha256(context_json),
                )
            )
            market_predictions.append(market_prediction)
            quant_predictions.append(quant_prediction)
            final_predictions.append(final_prediction)

        min_selection_ev = (
            request.min_selection_ev
            if request.min_selection_ev is not None
            else self._settings.analysis.min_selection_ev
        )
        min_ticket_roi = (
            request.min_ticket_roi
            if request.min_ticket_roi is not None
            else self._settings.analysis.min_ticket_roi
        )
        selection_candidates = tuple(
            candidate
            for prediction in final_predictions
            for candidate in build_selection_candidates(
                prediction,
                bonus_by_match[prediction.match_id],
                min_selection_ev,
            )
        )
        rules = _sporttery_rules(self._settings)
        ticket_candidates = build_two_leg_ticket_candidates(
            selection_candidates,
            rules,
            min_ticket_roi,
        )
        constraints = _portfolio_constraints(self._settings)
        portfolios = tuple(
            optimize_portfolio(
                run_id,
                ticket_candidates,
                budget_fen,
                constraints,
                rules,
            )
            for budget_fen in request.budgets_fen
        )
        portfolio_risk_reports = tuple(
            analyze_portfolio_risk(portfolio) for portfolio in portfolios
        )
        request_config: dict[str, object] = {
            "fusion_policy": request.fusion_policy,
            "min_selection_ev": min_selection_ev,
            "min_ticket_roi": min_ticket_roi,
            "budgets_fen": request.budgets_fen,
        }
        if request.allow_partial_inputs:
            request_config.update(
                {
                    "allow_partial_inputs": True,
                    "expected_match_ids": request.expected_match_ids or (),
                }
            )
        config_json = _canonical_json(
            {
                "settings": self._settings.model_dump(mode="json"),
                "request": request_config,
            }
        )
        manifest_json = build_input_manifest_json(
            selected.competitions,
            selected.teams,
            selected.matches,
            selected.mappings,
            selected.market_snapshots,
            selected.sporttery_snapshots,
            selected.manual_inputs,
        )
        completed_at = request.execution_time_utc or utc_now()
        analysis_run = AnalysisRun(
            analysis_run_id=run_id,
            as_of_at_utc=request.as_of_at_utc,
            status=AnalysisRunStatus.COMPLETED,
            started_at_utc=started_at,
            completed_at_utc=completed_at,
            pipeline_version=self._settings.analysis.pipeline_version,
            code_revision=_code_revision(),
            config_json=config_json,
            config_hash=_sha256(config_json),
            input_manifest_hash=_sha256(manifest_json),
            input_manifest_json=manifest_json,
        )
        artifacts = AnalysisArtifacts(
            competitions=selected.competitions,
            teams=selected.teams,
            matches=selected.matches,
            provider_mappings=selected.mappings,
            market_odds_snapshots=selected.market_snapshots,
            sporttery_bonus_snapshots=selected.sporttery_snapshots,
            manual_quant_inputs=selected.manual_inputs,
            analysis_run=analysis_run,
            match_contexts=tuple(contexts),
            market_predictions=tuple(market_predictions),
            quant_predictions=tuple(quant_predictions),
            final_predictions=tuple(final_predictions),
            selection_candidates=selection_candidates,
            ticket_candidates=ticket_candidates,
            portfolios=portfolios,
            portfolio_risk_reports=portfolio_risk_reports,
        )
        self._repository.save_analysis(artifacts, rules)
        return artifacts


def default_request(
    settings: AppSettings,
    as_of_at_utc: UtcDateTime,
    budgets_fen: tuple[int, ...] = (10_000, 20_000),
) -> RunAnalysisRequest:
    return RunAnalysisRequest(
        as_of_at_utc=as_of_at_utc,
        kickoff_from_utc=as_of_at_utc,
        kickoff_to_utc=as_of_at_utc + timedelta(days=3),
        budgets_fen=budgets_fen,
        fusion_policy=FusionPolicyName(settings.analysis.fusion_policy),
    )


def _validate_fixture_response(
    request: RunAnalysisRequest,
    batch: FixtureBatch,
) -> tuple[Match, ...]:
    match_ids = tuple(match.match_id for match in batch.matches)
    expected_ids = request.expected_match_ids or ()
    if expected_ids:
        expected = set(expected_ids)
        unexpected = tuple(
            match_id for match_id in match_ids if match_id not in expected
        )
        if unexpected:
            raise ValueError(
                "fixture provider returned unexpected matches: " + ", ".join(unexpected)
            )
        if not request.allow_partial_inputs:
            missing = tuple(
                match_id for match_id in expected_ids if match_id not in match_ids
            )
            if missing:
                raise ValueError(
                    "required MVP fixtures missing for: " + ", ".join(missing)
                )
    if any(
        not (request.kickoff_from_utc <= match.kickoff_at_utc <= request.kickoff_to_utc)
        for match in batch.matches
    ):
        raise ValueError(
            "fixture provider returned a match outside the requested window"
        )
    if not request.allow_partial_inputs:
        if not batch.matches:
            raise ValueError("fixture provider returned no matches")
        return batch.matches
    return tuple(
        match
        for match in batch.matches
        if match.available_at_utc <= request.as_of_at_utc
    )


def _select_analysis_inputs(
    request: RunAnalysisRequest,
    started_at: UtcDateTime,
    fixture_batch: FixtureBatch,
    query_matches: tuple[Match, ...],
    odds_batch: MarketOddsBatch,
    sporttery_batch: SportteryBatch,
    manual_quant_batch: ManualQuantBatch,
) -> _SelectedInputs:
    match_ids = tuple(match.match_id for match in query_matches)
    match_id_set = set(match_ids)
    all_mappings = (
        fixture_batch.mappings + odds_batch.mappings + sporttery_batch.mappings
    )
    if not request.allow_partial_inputs:
        _validate_point_in_time(
            request.as_of_at_utc,
            started_at,
            query_matches,
            all_mappings,
            odds_batch.snapshots,
            sporttery_batch.snapshots,
            manual_quant_batch.inputs,
        )
        _validate_source_payloads(
            all_mappings,
            odds_batch.snapshots,
            sporttery_batch.snapshots,
            manual_quant_batch.inputs,
        )
        _validate_match_scope(
            match_ids,
            odds_batch.snapshots,
            sporttery_batch.snapshots,
            manual_quant_batch.inputs,
        )
        missing = _missing_required_inputs(
            query_matches,
            odds_batch.snapshots,
            sporttery_batch.snapshots,
            manual_quant_batch.inputs,
        )
        if missing:
            raise ValueError(f"required MVP inputs missing for: {', '.join(missing)}")
        return _SelectedInputs(
            competitions=fixture_batch.competitions,
            teams=fixture_batch.teams,
            matches=query_matches,
            mappings=all_mappings,
            market_snapshots=odds_batch.snapshots,
            sporttery_snapshots=sporttery_batch.snapshots,
            manual_inputs=manual_quant_batch.inputs,
        )

    _validate_match_scope(
        match_ids,
        odds_batch.snapshots,
        sporttery_batch.snapshots,
        manual_quant_batch.inputs,
    )
    _validate_mapping_scope(
        match_ids,
        odds_batch.mappings,
        sporttery_batch.mappings,
    )
    cutoff = request.as_of_at_utc
    fixture_mappings = tuple(
        mapping
        for mapping in fixture_batch.mappings
        if mapping.internal_match_id in match_id_set
        and mapping.available_at_utc <= cutoff
    )
    market_snapshots = tuple(
        snapshot
        for snapshot in odds_batch.snapshots
        if _snapshot_is_visible(snapshot, cutoff)
    )
    sporttery_snapshots = tuple(
        snapshot
        for snapshot in sporttery_batch.snapshots
        if _snapshot_is_visible(snapshot, cutoff)
    )
    manual_inputs = tuple(
        item for item in manual_quant_batch.inputs if item.available_at_utc <= cutoff
    )
    market_mappings = tuple(
        mapping for mapping in odds_batch.mappings if mapping.available_at_utc <= cutoff
    )
    sporttery_mappings = tuple(
        mapping
        for mapping in sporttery_batch.mappings
        if mapping.available_at_utc <= cutoff
    )
    visible_mappings = fixture_mappings + market_mappings + sporttery_mappings
    _validate_source_payloads(
        visible_mappings,
        market_snapshots,
        sporttery_snapshots,
        manual_inputs,
    )

    odds_by_match = _unique_by_match(market_snapshots, "market odds")
    bonus_by_match = _unique_by_match(sporttery_snapshots, "Sporttery bonus")
    quant_by_match = _unique_by_match(manual_inputs, "manual P_quant")
    match_by_id = {match.match_id: match for match in query_matches}
    ordered_ids = tuple(
        match_id
        for match_id in (request.expected_match_ids or match_ids)
        if match_id in match_by_id
    )
    complete_ids = tuple(
        match_id
        for match_id in ordered_ids
        if _has_complete_mvp_inputs(
            match_id,
            fixture_mappings,
            odds_by_match,
            market_mappings,
            bonus_by_match,
            sporttery_mappings,
            quant_by_match,
        )
    )
    complete = set(complete_ids)
    matches = tuple(match_by_id[match_id] for match_id in complete_ids)
    competition_ids = {match.competition_id for match in matches}
    team_ids = {
        team_id
        for match in matches
        for team_id in (match.home_team_id, match.away_team_id)
    }
    competitions = tuple(
        item
        for item in fixture_batch.competitions
        if item.competition_id in competition_ids
    )
    teams = tuple(item for item in fixture_batch.teams if item.team_id in team_ids)
    if {item.competition_id for item in competitions} != competition_ids or {
        item.team_id for item in teams
    } != team_ids:
        raise ValueError("fixture provider omitted competition or team references")

    selected_market = tuple(odds_by_match[match_id] for match_id in complete_ids)
    selected_bonus = tuple(bonus_by_match[match_id] for match_id in complete_ids)
    selected_quant = tuple(quant_by_match[match_id] for match_id in complete_ids)
    selected_fixture_mappings = tuple(
        mapping for mapping in fixture_mappings if mapping.internal_match_id in complete
    )
    selected_market_mappings = tuple(
        mapping
        for mapping in market_mappings
        if mapping.internal_match_id in complete
        and mapping.provider_code
        == odds_by_match[mapping.internal_match_id].provider_code
    )
    selected_sporttery_mappings = tuple(
        mapping
        for mapping in sporttery_mappings
        if mapping.internal_match_id in complete
        and mapping.provider_code
        == bonus_by_match[mapping.internal_match_id].provider_code
    )
    selected_mappings = _unique_mappings(
        selected_fixture_mappings,
        selected_market_mappings,
        selected_sporttery_mappings,
    )
    _validate_point_in_time(
        cutoff,
        started_at,
        matches,
        selected_mappings,
        selected_market,
        selected_bonus,
        selected_quant,
    )
    return _SelectedInputs(
        competitions=competitions,
        teams=teams,
        matches=matches,
        mappings=selected_mappings,
        market_snapshots=selected_market,
        sporttery_snapshots=selected_bonus,
        manual_inputs=selected_quant,
    )


def _missing_required_inputs(
    matches: tuple[Match, ...],
    market_snapshots: tuple[MarketOddsSnapshot, ...],
    sporttery_snapshots: tuple[SportteryBonusSnapshot, ...],
    manual_inputs: tuple[ManualQuantInput, ...],
) -> tuple[str, ...]:
    odds_by_match = _unique_by_match(market_snapshots, "market odds")
    bonus_by_match = _unique_by_match(sporttery_snapshots, "Sporttery bonus")
    quant_by_match = _unique_by_match(manual_inputs, "manual P_quant")
    return tuple(
        match.match_id
        for match in matches
        if match.match_id not in odds_by_match
        or match.match_id not in bonus_by_match
        or match.match_id not in quant_by_match
    )


def _has_complete_mvp_inputs(
    match_id: str,
    fixture_mappings: tuple[ProviderMatchMapping, ...],
    odds_by_match: dict[str, object],
    market_mappings: tuple[ProviderMatchMapping, ...],
    bonus_by_match: dict[str, object],
    sporttery_mappings: tuple[ProviderMatchMapping, ...],
    quant_by_match: dict[str, object],
) -> bool:
    odds = odds_by_match.get(match_id)
    bonus = bonus_by_match.get(match_id)
    quant = quant_by_match.get(match_id)
    if (
        odds is None
        or bonus is None
        or quant is None
        or not any(
            mapping.internal_match_id == match_id for mapping in fixture_mappings
        )
        or not _has_provider_mapping(market_mappings, match_id, odds.provider_code)
        or not _has_provider_mapping(sporttery_mappings, match_id, bonus.provider_code)
    ):
        return False
    return (
        odds.market.market_type == MarketType.THREE_WAY
        and odds.market == bonus.market == quant.market
    )


def _has_provider_mapping(
    mappings: tuple[ProviderMatchMapping, ...],
    match_id: str,
    provider_code: str,
) -> bool:
    return any(
        mapping.internal_match_id == match_id and mapping.provider_code == provider_code
        for mapping in mappings
    )


def _snapshot_is_visible(
    snapshot: MarketOddsSnapshot | SportteryBonusSnapshot,
    cutoff: UtcDateTime,
) -> bool:
    return all(
        timestamp <= cutoff
        for timestamp in (
            snapshot.captured_at_utc,
            snapshot.available_at_utc,
            snapshot.ingested_at_utc,
        )
    )


def _validate_mapping_scope(
    match_ids: tuple[str, ...],
    *mapping_groups: tuple[ProviderMatchMapping, ...],
) -> None:
    expected = set(match_ids)
    if any(
        mapping.internal_match_id not in expected
        for group in mapping_groups
        for mapping in group
    ):
        raise ValueError("provider returned a mapping for an unrequested match")


def _unique_mappings(
    *mapping_groups: tuple[ProviderMatchMapping, ...],
) -> tuple[ProviderMatchMapping, ...]:
    by_id: dict[str, ProviderMatchMapping] = {}
    for mapping in (item for group in mapping_groups for item in group):
        previous = by_id.get(mapping.mapping_id)
        if previous is not None and previous != mapping:
            raise ValueError(f"conflicting provider mapping: {mapping.mapping_id}")
        by_id[mapping.mapping_id] = mapping
    return tuple(by_id[mapping_id] for mapping_id in sorted(by_id))


def _sporttery_rules(settings: AppSettings) -> SportteryRules:
    return SportteryRules(
        version=settings.sporttery.rules_version,
        base_stake_fen=settings.sporttery.base_stake_fen,
        max_multiplier=settings.sporttery.max_multiplier,
        max_ticket_stake_fen=settings.sporttery.max_ticket_stake_fen,
    )


def _portfolio_constraints(settings: AppSettings) -> PortfolioConstraints:
    return PortfolioConstraints(
        preferred_max_tickets=settings.portfolio.preferred_max_tickets,
        absolute_max_tickets=settings.portfolio.absolute_max_tickets,
        extra_ticket_min_roi=settings.portfolio.extra_ticket_min_roi,
        operational_complexity_penalty=(
            settings.portfolio.operational_complexity_penalty
        ),
        max_match_exposure_ratio=settings.portfolio.max_match_exposure_ratio,
        max_selection_exposure_ratio=(settings.portfolio.max_selection_exposure_ratio),
        concentration_penalty=settings.portfolio.concentration_penalty,
        min_marginal_score=settings.portfolio.min_marginal_score,
    )


def _unique_by_match(items: tuple, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        if item.match_id in result:
            raise ValueError(f"multiple {label} inputs for match {item.match_id}")
        result[item.match_id] = item
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_point_in_time(
    as_of_at_utc: UtcDateTime,
    started_at_utc: UtcDateTime,
    matches: tuple,
    mappings: tuple,
    market_snapshots: tuple,
    sporttery_snapshots: tuple,
    manual_inputs: tuple,
) -> None:
    if started_at_utc < as_of_at_utc:
        raise ValueError("analysis cannot start before its knowledge cutoff")
    for item in (*matches, *mappings, *manual_inputs):
        if item.available_at_utc > as_of_at_utc:
            raise ValueError(
                f"input {getattr(item, 'match_id', getattr(item, 'mapping_id', 'unknown'))} "
                "was not available at the knowledge cutoff"
            )
    for snapshot in (*market_snapshots, *sporttery_snapshots):
        if any(
            timestamp > as_of_at_utc
            for timestamp in (
                snapshot.captured_at_utc,
                snapshot.available_at_utc,
                snapshot.ingested_at_utc,
            )
        ):
            raise ValueError(
                f"snapshot {snapshot.snapshot_id} crosses the knowledge cutoff"
            )


def _validate_match_scope(match_ids: tuple[str, ...], *item_groups: tuple) -> None:
    expected = set(match_ids)
    for item in (item for group in item_groups for item in group):
        if item.match_id not in expected:
            raise ValueError(f"provider returned an unrequested match: {item.match_id}")


def _code_revision() -> str:
    package_root = Path(__file__).resolve().parents[1]
    files = tuple(package_root.rglob("*.py"))
    if not files:
        raise RuntimeError("cannot compute code revision from installed package")
    digest = hashlib.sha256()
    for path in sorted(files, key=str):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"package:{digest.hexdigest()}"


def _validate_source_payloads(
    mappings: tuple,
    market_snapshots: tuple,
    sporttery_snapshots: tuple,
    manual_inputs: tuple,
) -> None:
    for mapping in mappings:
        _validate_decimal_storage(
            (("confidence", mapping.confidence),), 18, 12, mapping.mapping_id
        )
    for snapshot in market_snapshots:
        payload = snapshot.three_way_odds()
        _validate_market_handicap(snapshot.market, snapshot.snapshot_id)
        _validate_decimal_storage(payload.items(), 18, 6, snapshot.snapshot_id)
        _assert_payload_hash(snapshot.payload_hash, payload, snapshot.snapshot_id)
    for snapshot in sporttery_snapshots:
        payload = snapshot.three_way_bonus()
        _validate_market_handicap(snapshot.market, snapshot.snapshot_id)
        _validate_decimal_storage(payload.items(), 18, 6, snapshot.snapshot_id)
        _assert_payload_hash(snapshot.payload_hash, payload, snapshot.snapshot_id)
    for manual_input in manual_inputs:
        payload = manual_input.probabilities
        _validate_market_handicap(manual_input.market, manual_input.input_id)
        _validate_decimal_storage(payload.items(), 18, 12, manual_input.input_id)
        _assert_payload_hash(manual_input.payload_hash, payload, manual_input.input_id)


def _validate_decimal_storage(
    items: tuple,
    precision: int,
    scale: int,
    source_id: str,
) -> None:
    quantum = Decimal(1).scaleb(-scale)
    max_absolute = Decimal(10) ** (precision - scale)
    try:
        invalid = any(
            not value.is_finite()
            or abs(value) >= max_absolute
            or value != value.quantize(quantum)
            for _, value in items
        )
    except InvalidOperation:
        invalid = True
    if invalid:
        raise ValueError(f"source {source_id} exceeds database decimal precision")


def _validate_market_handicap(market: object, source_id: str) -> None:
    if market.handicap_value is not None:
        _validate_decimal_storage(
            (("handicap", market.handicap_value),), 8, 3, source_id
        )


def _assert_payload_hash(expected: str, payload: DomainModel, source_id: str) -> None:
    actual = _sha256(_canonical_json(payload.model_dump(mode="json")))
    if actual != expected:
        raise ValueError(f"source {source_id} payload hash does not match its contents")


def build_input_manifest_json(
    competitions: tuple,
    teams: tuple,
    matches: tuple,
    mappings: tuple,
    market_snapshots: tuple,
    sporttery_snapshots: tuple,
    manual_inputs: tuple,
) -> str:
    return _canonical_json(
        {
            "version": "MVP_INPUT_MANIFEST_V2",
            "competitions": _manifest_records(competitions, "competition_id"),
            "teams": _manifest_records(teams, "team_id"),
            "matches": _manifest_records(matches, "match_id"),
            "provider_mappings": _manifest_records(mappings, "mapping_id"),
            "market_odds_snapshots": _manifest_records(market_snapshots, "snapshot_id"),
            "sporttery_bonus_snapshots": _manifest_records(
                sporttery_snapshots, "snapshot_id"
            ),
            "manual_quant_inputs": _manifest_records(manual_inputs, "input_id"),
        }
    )


def _manifest_records(items: tuple, identity_field: str) -> tuple[dict, ...]:
    records = []
    for item in sorted(items, key=lambda value: getattr(value, identity_field)):
        record = item.model_dump(mode="json")
        if "quotes" in record:
            record["quotes"] = sorted(
                record["quotes"], key=lambda quote: quote["selection"]
            )
        records.append(record)
    return tuple(records)
