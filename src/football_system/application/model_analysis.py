from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast

from pydantic import Field, model_validator

from football_system.application.environment import (
    ProviderRuntimeProvenanceMismatchError,
    RuntimeEnvironment,
    RuntimeProvenance,
    require_provider_runtime_provenance,
    validate_analysis_provider_runtime,
)
from football_system.application.models import AnalysisArtifacts
from football_system.application.ports.data_providers import (
    EloTrainingHistoryBatch,
    EloTrainingHistoryProvider,
    EloTrainingHistoryQuery,
    FixtureBatch,
    FixtureProvider,
    FixtureQuery,
    MarketOddsBatch,
    MarketOddsProvider,
    SnapshotQuery,
    SportteryBatch,
    SportteryProvider,
)
from football_system.application.ports.repositories import AnalysisRepository
from football_system.application.ports.production_inference import (
    ProductionInferenceBindingV1,
    ProductionInferenceRepository,
)
from football_system.application.production_release import project_release_state
from football_system.application.quant_model import (
    MVP_INPUT_MANIFEST_V3,
    build_model_input_manifest_json,
    freeze_elo_evaluation,
    freeze_elo_model_state,
    project_available_model_quant,
)
from football_system.application.run_analysis import (
    RunAnalysisRequest,
    _canonical_json,
    _code_revision,
    _portfolio_constraints,
    _sha256,
    _sporttery_rules,
    _validate_fixture_response,
    _validate_source_payloads,
)
from football_system.config import AppSettings
from football_system.domain.analysis import (
    AnalysisRun,
    AnalysisRunStatus,
    ModelAnalysisMatchContext,
)
from football_system.domain.betting import PortfolioConstraints
from football_system.domain.common import (
    DomainModel,
    Identifier,
    UtcDateTime,
    new_id,
    normalize_utc,
    stable_id,
    utc_now,
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
from football_system.domain.prediction import (
    FusionConfig,
    FusionInputs,
    MarketPrediction,
    ModelQuantPrediction,
    QuantModelEvaluation,
)
from football_system.domain.production_release import (
    assert_authorization_progression,
    release_active_for_inference,
)
from football_system.domain.services.betting import (
    build_selection_candidates,
    build_two_leg_ticket_candidates,
)
from football_system.domain.services.elo_baseline import (
    EloBaselineConfig,
    EloPredictionRequest,
    EloThreeWayBaseline,
)
from football_system.domain.services.fusion import get_fusion_policy
from football_system.domain.services.optimizer import optimize_portfolio
from football_system.domain.services.probability import normalized_inverse_probability
from football_system.domain.services.risk import analyze_portfolio_risk


class PreparedFixtureObservationRef(DomainModel):
    match_id: Identifier
    fixture_observation_id: Identifier


class RunModelAnalysisRequest(RunAnalysisRequest):
    competition_id: str
    season_id: str
    elo_config: EloBaselineConfig
    quant_weight: Decimal | None = Field(default=None, ge=0, le=1)
    constraints: PortfolioConstraints | None = None
    live_source_preparation_id: Identifier | None = None
    prepared_fixture_observations: tuple[PreparedFixtureObservationRef, ...] = ()
    production_model_release_id: Identifier | None = None
    production_target_acceptance_plan_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_prepared_lineage(self) -> RunModelAnalysisRequest:
        if (self.production_model_release_id is None) != (
            self.production_target_acceptance_plan_id is None
        ):
            raise ValueError(
                "production release and target plan must be supplied together"
            )
        if self.production_model_release_id is not None and (
            self.live_source_preparation_id is None or self.allow_partial_inputs
        ):
            raise ValueError(
                "production inference requires complete prepared live inputs"
            )
        references = tuple(item.match_id for item in self.prepared_fixture_observations)
        if len(references) != len(set(references)):
            raise ValueError("prepared fixture observation matches must be unique")
        if (self.live_source_preparation_id is None) != (not references):
            raise ValueError(
                "live source preparation and fixture observations must be supplied together"
            )
        if self.expected_match_ids is not None and any(
            match_id not in self.expected_match_ids for match_id in references
        ):
            raise ValueError("prepared fixture observation is outside expected matches")
        return self


class ModelAnalysisDecision(DomainModel):
    analysis_artifacts: AnalysisArtifacts
    training_history: EloTrainingHistoryBatch | None = None

    @model_validator(mode="after")
    def validate_cutoff(self) -> ModelAnalysisDecision:
        if self.analysis_artifacts.production_binding is not None:
            if self.training_history is not None:
                raise ValueError(
                    "production inference cannot contain provider training history"
                )
            return self
        if self.training_history is None:
            raise ValueError("strict training decision requires training history")
        if (
            self.training_history.as_of_at_utc
            != self.analysis_artifacts.analysis_run.as_of_at_utc
        ):
            raise ValueError("model decision training cutoff is inconsistent")
        return self


@dataclass(frozen=True)
class _SelectedModelInputs:
    competitions: tuple[Competition, ...]
    teams: tuple[Team, ...]
    matches: tuple[Match, ...]
    mappings: tuple[ProviderMatchMapping, ...]
    market_snapshots: tuple[MarketOddsSnapshot, ...]
    sporttery_snapshots: tuple[SportteryBonusSnapshot, ...]


class RunModelAnalysisService:
    """Run the existing decision pipeline with an immutable Elo P_quant source."""

    def __init__(
        self,
        fixture_provider: FixtureProvider,
        market_odds_provider: MarketOddsProvider,
        sporttery_provider: SportteryProvider,
        training_history_provider: EloTrainingHistoryProvider | None,
        repository: AnalysisRepository,
        settings: AppSettings,
        elo_config: EloBaselineConfig | None = None,
        production_release_repository: ProductionInferenceRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fixture_provider = fixture_provider
        self._market_odds_provider = market_odds_provider
        self._sporttery_provider = sporttery_provider
        self._training_history_provider = training_history_provider
        self._repository = repository
        self._settings = settings
        self._baseline = EloThreeWayBaseline(elo_config or EloBaselineConfig())
        self._production_release_repository = production_release_repository
        self._clock = clock or utc_now

    @property
    def baseline(self) -> EloThreeWayBaseline:
        return self._baseline

    def declared_provider_runtime_provenance(
        self, *, production_release: bool = False
    ) -> dict[str, RuntimeProvenance]:
        return {
            role: require_provider_runtime_provenance(provider, role)
            for role, provider in {
                "fixture": self._fixture_provider,
                "market_odds": self._market_odds_provider,
                "sporttery": self._sporttery_provider,
                **(
                    {}
                    if production_release
                    else {"model_training": self._training_history_provider}
                ),
            }.items()
        }

    async def run(self, request: RunModelAnalysisRequest) -> AnalysisArtifacts:
        return (await self.run_decision(request)).analysis_artifacts

    async def run_decision(
        self,
        request: RunModelAnalysisRequest,
    ) -> ModelAnalysisDecision:
        request = RunModelAnalysisRequest.model_validate(
            request.model_dump(mode="python", exclude_computed_fields=True)
        )
        if request.elo_config != self._baseline.config:
            raise ValueError("model analysis Elo config does not match its runner")
        pinned = request.production_model_release_id is not None
        production = self._production_release_repository
        if pinned and (
            production is None
            or self._settings.runtime.environment is not RuntimeEnvironment.LIVE
        ):
            raise ValueError(
                "production inference requires live runtime and release repository"
            )
        if not pinned and self._training_history_provider is None:
            raise ValueError(
                "strict model analysis requires a training history provider"
            )
        runtime_provenance = validate_analysis_provider_runtime(
            self._settings.runtime.environment,
            {
                "fixture": self._fixture_provider,
                "market_odds": self._market_odds_provider,
                "sporttery": self._sporttery_provider,
                **(
                    {}
                    if pinned
                    else {"model_training": self._training_history_provider}
                ),
            },
        )
        started_at = (
            normalize_utc(self._clock())
            if pinned
            else request.execution_time_utc or self._clock()
        )
        run_id = request.analysis_run_id or new_id()
        release = plan = prior_binding = start_authorization = None
        if pinned:
            release = production.load_release(request.production_model_release_id)
            plan = production.load_target_plan(
                request.production_target_acceptance_plan_id
            )
            target = plan.content_payload
            if (
                release.artifact_id != request.production_model_release_id
                or plan.artifact_id != request.production_target_acceptance_plan_id
                or request.competition_id != target.competition_id
                or request.season_id != target.production_target_season_id
                or request.as_of_at_utc != target.decision_as_of_at_utc
                or request.kickoff_from_utc != target.kickoff_window_start_at_utc
                or request.kickoff_to_utc != target.kickoff_window_end_at_utc
                or (
                    request.expected_match_ids is not None
                    and request.expected_match_ids
                    != tuple(item.match_id for item in target.targets)
                )
                or request.elo_config != release.content_payload.scope.model.config
            ):
                raise ValueError(
                    "production request differs from exact release/target plan"
                )
            if started_at >= min(item.kickoff_at_utc for item in target.targets):
                raise ValueError(
                    "production inference must start before planned kickoff"
                )
            start_authorization = production.authorization(
                release.artifact_id, started_at
            )
            if start_authorization.actual_at_utc != started_at:
                raise ValueError(
                    "production authorization returned the wrong actual time"
                )
            release_active_for_inference(
                release=release,
                plan=plan,
                current=start_authorization,
                state_retention_horizon=release.content_payload.state_retention_horizon,
                audit_retention_horizon=release.content_payload.audit_retention_horizon,
            )
            prior_binding = production.load_binding(run_id)
            if prior_binding is not None:
                if (
                    prior_binding.release != release.reference()
                    or prior_binding.target_acceptance_plan != plan.reference()
                ):
                    raise ValueError("production retry release/target plan mismatch")
                started_at = prior_binding.start_authorization.actual_at_utc

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
        if any(
            match.competition_id != request.competition_id for match in query_matches
        ):
            raise ValueError("model analysis fixtures cross the requested competition")
        if pinned and tuple(
            (item.match_id, item.home_team_id, item.away_team_id, item.kickoff_at_utc)
            for item in sorted(query_matches, key=lambda item: item.match_id)
        ) != tuple(
            (item.match_id, item.home_team_id, item.away_team_id, item.kickoff_at_utc)
            for item in plan.content_payload.targets
        ):
            raise ValueError(
                "production fixtures differ from exact planned targets/kickoff"
            )
        fixture_observations = {
            item.match_id: item.fixture_observation_id
            for item in request.prepared_fixture_observations
        }
        if fixture_observations and set(fixture_observations) != {
            match.match_id for match in query_matches
        }:
            raise ValueError(
                "prepared fixture observations do not match provider fixtures"
            )
        target_match_ids = request.expected_match_ids or tuple(
            match.match_id for match in query_matches
        )
        snapshot_query = SnapshotQuery(
            match_ids=tuple(match.match_id for match in query_matches),
            as_of_at_utc=request.as_of_at_utc,
        )
        training_batch = None
        if pinned:
            target_match_ids = tuple(
                item.match_id for item in plan.content_payload.targets
            )
            query_matches = tuple(sorted(query_matches, key=lambda item: item.match_id))
            odds_batch, sporttery_batch = await asyncio.gather(
                self._market_odds_provider.fetch_market_odds(snapshot_query),
                self._sporttery_provider.fetch_fixed_bonus(snapshot_query),
            )
        else:
            training_query = EloTrainingHistoryQuery(
                competition_id=request.competition_id,
                target_season_id=request.season_id,
                as_of_at_utc=request.as_of_at_utc,
                exclude_match_ids=target_match_ids,
            )
            odds_batch, sporttery_batch, training_batch = await asyncio.gather(
                self._market_odds_provider.fetch_market_odds(snapshot_query),
                self._sporttery_provider.fetch_fixed_bonus(snapshot_query),
                self._training_history_provider.fetch_elo_training_history(
                    training_query
                ),
            )
            training_batch = EloTrainingHistoryBatch.model_validate(
                training_batch.model_dump(mode="python", exclude_computed_fields=True)
            )
            _validate_training_batch(training_query, training_batch)
        odds_batch = MarketOddsBatch.model_validate(
            odds_batch.model_dump(mode="python", exclude_computed_fields=True)
        )
        sporttery_batch = SportteryBatch.model_validate(
            sporttery_batch.model_dump(mode="python", exclude_computed_fields=True)
        )
        _validate_runtime_outputs(
            runtime_provenance,
            fixture_batch,
            odds_batch,
            sporttery_batch,
            training_batch,
        )
        selected = _select_model_inputs(
            request,
            started_at,
            fixture_batch,
            query_matches,
            odds_batch,
            sporttery_batch,
        )

        state = (
            project_release_state(
                release, request.as_of_at_utc, target_match_ids, request.season_id
            )
            if pinned
            else self._baseline.rebuild_state(
                (source.result for source in training_batch.sources),
                request.as_of_at_utc,
                target_season_id=request.season_id,
                exclude_match_ids=target_match_ids,
            )
        )
        model_state = freeze_elo_model_state(
            analysis_run_id=run_id,
            baseline=self._baseline,
            state=state,
            generated_at_utc=started_at,
        )
        odds_by_match = _by_match(selected.market_snapshots, "market odds")
        bonus_by_match = _by_match(selected.sporttery_snapshots, "Sporttery bonus")
        policy = get_fusion_policy(request.fusion_policy)
        quant_weight = (
            request.quant_weight
            if request.quant_weight is not None
            else self._settings.analysis.quant_weight
        )
        fusion_config = FusionConfig(quant_weight=quant_weight)
        market_predictions: list[MarketPrediction] = []
        model_evaluations: list[QuantModelEvaluation] = []
        quant_predictions: list[ModelQuantPrediction] = []
        final_predictions = []
        contexts: list[ModelAnalysisMatchContext] = []

        for match in selected.matches:
            odds_snapshot = odds_by_match[match.match_id]
            bonus_snapshot = bonus_by_match[match.match_id]
            if odds_snapshot.market.market_type is not MarketType.THREE_WAY:
                raise UnsupportedMarketError(
                    "MVP model pipeline supports only THREE_WAY market odds"
                )
            if odds_snapshot.market != bonus_snapshot.market:
                raise ValueError(f"market inputs disagree for match {match.match_id}")
            p_market, overround = normalized_inverse_probability(
                odds_snapshot.three_way_odds()
            )
            market_prediction = MarketPrediction(
                prediction_id=stable_id(
                    "p-market",
                    run_id,
                    match.match_id,
                    odds_snapshot.market.canonical,
                ),
                analysis_run_id=run_id,
                match_id=match.match_id,
                market=odds_snapshot.market,
                probabilities=p_market,
                input_snapshot_ids=(odds_snapshot.snapshot_id,),
                overround=overround,
                generated_at_utc=started_at,
            )
            elo_prediction = self._baseline.predict_from_state(
                EloPredictionRequest(
                    match_id=match.match_id,
                    season_id=request.season_id,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    kickoff_at_utc=match.kickoff_at_utc,
                    cutoff_at_utc=request.as_of_at_utc,
                ),
                state,
            )
            evaluation = freeze_elo_evaluation(
                analysis_run_id=run_id,
                model_state=model_state,
                prediction=elo_prediction,
                market=odds_snapshot.market,
                evaluated_at_utc=started_at,
            )
            quant_prediction = project_available_model_quant(
                model_state=model_state,
                evaluation=evaluation,
            )
            if pinned and quant_prediction is None:
                raise ValueError(
                    "planned production target requires five prior matches"
                )
            if quant_prediction is not None:
                final_predictions.append(
                    policy.fuse(
                        FusionInputs(
                            analysis_run_id=run_id,
                            match_id=match.match_id,
                            market=odds_snapshot.market,
                            p_market=market_prediction,
                            p_quant=quant_prediction,
                        ),
                        fusion_config,
                        started_at,
                    )
                )
                quant_predictions.append(quant_prediction)
            context_payload = {
                "match_id": match.match_id,
                "as_of_at_utc": request.as_of_at_utc,
                "market_odds_snapshot_id": odds_snapshot.snapshot_id,
                "sporttery_bonus_snapshot_id": bonus_snapshot.snapshot_id,
                "quant_model_evaluation_id": evaluation.quant_model_evaluation_id,
            }
            fixture_observation_id = fixture_observations.get(match.match_id)
            if fixture_observation_id is not None:
                context_payload["fixture_observation_id"] = fixture_observation_id
            context_json = _canonical_json(context_payload)
            contexts.append(
                ModelAnalysisMatchContext(
                    analysis_run_id=run_id,
                    match_id=match.match_id,
                    fixture_observation_id=fixture_observation_id,
                    market_odds_snapshot_id=odds_snapshot.snapshot_id,
                    sporttery_bonus_snapshot_id=bonus_snapshot.snapshot_id,
                    quant_model_evaluation_id=evaluation.quant_model_evaluation_id,
                    context_json=context_json,
                    context_hash=_sha256(context_json),
                )
            )
            market_predictions.append(market_prediction)
            model_evaluations.append(evaluation)

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
        constraints = request.constraints or _portfolio_constraints(self._settings)
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
        risk_reports = tuple(analyze_portfolio_risk(item) for item in portfolios)
        request_config: dict[str, object] = {
            "fusion_policy": request.fusion_policy,
            "min_selection_ev": min_selection_ev,
            "min_ticket_roi": min_ticket_roi,
            "budgets_fen": request.budgets_fen,
            "quant_weight": quant_weight,
            "portfolio_constraints": constraints,
            "competition_id": request.competition_id,
            "season_id": request.season_id,
            "allow_partial_inputs": request.allow_partial_inputs,
            "expected_match_ids": request.expected_match_ids or (),
            "quant_source": "MODEL",
            "model_name": state.model_name,
            "model_version": state.model_version,
            "calibration_label": state.calibration_label,
            "model_config_hash": state.config_hash,
        }
        if pinned:
            request_config.update(
                production_model_release_id=release.artifact_id,
                production_target_acceptance_plan_id=plan.artifact_id,
                model_training_use_class="APPROVED_TRAINING_HISTORY",
                model_training_source_mode="SOURCE_TIME_RESEARCH",
                decision_data_mode="LIVE_STRICT",
                kickoff_from_utc=request.kickoff_from_utc,
                kickoff_to_utc=request.kickoff_to_utc,
            )
            from football_system.domain.production_release import ObservedTrainingHistoryGraphV1

            if isinstance(release.training_manifest.content_payload.history, ObservedTrainingHistoryGraphV1):
                del request_config["model_training_source_mode"]
                request_config["model_training_evidence_basis"] = "CURRENT_SNAPSHOT_OBSERVED"
        if runtime_provenance:
            request_config["provider_runtime_provenance"] = {
                role: provenance.model_dump(mode="json")
                for role, provenance in sorted(runtime_provenance.items())
            }
        if request.live_source_preparation_id is not None:
            request_config.update(
                {
                    "live_source_preparation_id": request.live_source_preparation_id,
                    "prepared_fixture_observations": {
                        match_id: fixture_observations[match_id]
                        for match_id in sorted(fixture_observations)
                    },
                }
            )
        config_json = _canonical_json(
            {
                "settings": self._settings.model_dump(mode="json"),
                "request": request_config,
            }
        )
        manifest_json = build_model_input_manifest_json(
            competitions=selected.competitions,
            teams=selected.teams,
            matches=selected.matches,
            mappings=selected.mappings,
            market_snapshots=selected.market_snapshots,
            sporttery_snapshots=selected.sporttery_snapshots,
            model_states=(model_state,),
        )
        completed_at = (
            normalize_utc(self._clock())
            if pinned
            else request.execution_time_utc or self._clock()
        )
        binding = None
        if pinned:
            completion = production.authorization(release.artifact_id, completed_at)
            if completion.actual_at_utc != completed_at:
                raise ValueError(
                    "production authorization returned the wrong actual time"
                )
            assert_authorization_progression(start_authorization, completion)
            release_active_for_inference(
                release=release,
                plan=plan,
                current=completion,
                state_retention_horizon=release.content_payload.state_retention_horizon,
                audit_retention_horizon=release.content_payload.audit_retention_horizon,
            )
            if completed_at >= min(
                item.kickoff_at_utc for item in plan.content_payload.targets
            ):
                raise ValueError(
                    "production inference must complete before planned kickoff"
                )
            core = release.content_payload.released_state_core
            binding = prior_binding or ProductionInferenceBindingV1(
                analysis_run_id=run_id,
                quant_model_state_id=model_state.quant_model_state_id,
                release=release.reference(),
                target_acceptance_plan=plan.reference(),
                released_state_core_hash=core.content_hash,
                training_cutoff_at_utc=release.content_payload.training_cutoff_at_utc,
                training_data_hash=core.content_payload.training_data_hash,
                approved_facts_hash=core.content_payload.approved_facts_hash,
                start_authorization=start_authorization,
                completion_authorization=completion,
                state_retention_horizon=release.content_payload.state_retention_horizon,
                audit_retention_horizon=release.content_payload.audit_retention_horizon,
            )
            completed_at = binding.completion_authorization.actual_at_utc
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
            input_manifest_version=MVP_INPUT_MANIFEST_V3,
            input_manifest_json=manifest_json,
            input_manifest_hash=_sha256(manifest_json),
        )
        artifacts = AnalysisArtifacts(
            competitions=selected.competitions,
            teams=selected.teams,
            matches=selected.matches,
            provider_mappings=selected.mappings,
            market_odds_snapshots=selected.market_snapshots,
            sporttery_bonus_snapshots=selected.sporttery_snapshots,
            manual_quant_inputs=(),
            analysis_run=analysis_run,
            match_contexts=tuple(contexts),
            market_predictions=tuple(market_predictions),
            quant_predictions=tuple(quant_predictions),
            final_predictions=tuple(final_predictions),
            selection_candidates=selection_candidates,
            ticket_candidates=ticket_candidates,
            portfolios=portfolios,
            portfolio_risk_reports=risk_reports,
            quant_model_states=(model_state,),
            quant_model_evaluations=tuple(model_evaluations),
            live_source_preparation_id=request.live_source_preparation_id,
            production_binding=binding,
        )
        self._repository.save_analysis(artifacts, rules)
        return ModelAnalysisDecision(
            analysis_artifacts=artifacts,
            training_history=training_batch,
        )


def _validate_training_batch(
    query: EloTrainingHistoryQuery,
    batch: EloTrainingHistoryBatch,
) -> None:
    if (
        batch.competition_id != query.competition_id
        or batch.target_season_id != query.target_season_id
        or batch.as_of_at_utc != query.as_of_at_utc
    ):
        raise ValueError("Elo training history does not match its query")


def _select_model_inputs(
    request: RunModelAnalysisRequest,
    started_at: UtcDateTime,
    fixture_batch: FixtureBatch,
    query_matches: tuple[Match, ...],
    odds_batch: MarketOddsBatch,
    sporttery_batch: SportteryBatch,
) -> _SelectedModelInputs:
    if started_at < request.as_of_at_utc:
        raise ValueError("analysis cannot start before its knowledge cutoff")
    requested = {match.match_id for match in query_matches}
    for match in query_matches:
        if match.available_at_utc > request.as_of_at_utc:
            raise ValueError(f"fixture {match.match_id} crosses the knowledge cutoff")
    for group in (odds_batch.snapshots, sporttery_batch.snapshots):
        for snapshot in group:
            if snapshot.match_id not in requested:
                raise ValueError(
                    "provider returned an unrequested model-analysis match"
                )
            if any(
                timestamp > request.as_of_at_utc
                for timestamp in (
                    snapshot.captured_at_utc,
                    snapshot.available_at_utc,
                    snapshot.ingested_at_utc,
                )
            ):
                raise ValueError(f"snapshot {snapshot.snapshot_id} crosses the cutoff")
    odds_by_match = _by_match(odds_batch.snapshots, "market odds")
    bonus_by_match = _by_match(sporttery_batch.snapshots, "Sporttery bonus")
    ordered_ids = request.expected_match_ids or tuple(
        match.match_id for match in query_matches
    )
    match_by_id = {match.match_id: match for match in query_matches}
    complete_ids = tuple(
        match_id
        for match_id in ordered_ids
        if match_id in match_by_id
        and match_id in odds_by_match
        and match_id in bonus_by_match
    )
    if not request.allow_partial_inputs and len(complete_ids) != len(ordered_ids):
        missing = tuple(
            match_id for match_id in ordered_ids if match_id not in complete_ids
        )
        raise ValueError(
            "required model analysis inputs missing for: " + ", ".join(missing)
        )
    selected_ids = set(complete_ids)
    matches = tuple(match_by_id[match_id] for match_id in complete_ids)
    market_snapshots = tuple(
        cast(MarketOddsSnapshot, odds_by_match[match_id]) for match_id in complete_ids
    )
    sporttery_snapshots = tuple(
        cast(SportteryBonusSnapshot, bonus_by_match[match_id])
        for match_id in complete_ids
    )
    mappings_by_id: dict[str, ProviderMatchMapping] = {}
    for mapping in (
        *fixture_batch.mappings,
        *odds_batch.mappings,
        *sporttery_batch.mappings,
    ):
        if mapping.internal_match_id not in requested:
            raise ValueError("provider returned a mapping for an unrequested match")
        if mapping.available_at_utc > request.as_of_at_utc:
            continue
        if mapping.internal_match_id not in selected_ids:
            continue
        previous = mappings_by_id.get(mapping.mapping_id)
        if previous is not None and previous != mapping:
            raise ValueError(f"conflicting provider mapping: {mapping.mapping_id}")
        mappings_by_id[mapping.mapping_id] = mapping
    mappings = tuple(mappings_by_id[key] for key in sorted(mappings_by_id))
    _validate_source_payloads(mappings, market_snapshots, sporttery_snapshots, ())
    fixture_mapped = {mapping.internal_match_id for mapping in fixture_batch.mappings}
    if any(match_id not in fixture_mapped for match_id in complete_ids):
        raise ValueError("model analysis fixture is missing its provider mapping")
    competition_ids = {match.competition_id for match in matches}
    competitions = tuple(
        item
        for item in fixture_batch.competitions
        if item.competition_id in competition_ids
    )
    if {item.competition_id for item in competitions} != competition_ids:
        raise ValueError("model analysis fixture is missing its competition")
    team_ids = {
        team_id
        for match in matches
        for team_id in (match.home_team_id, match.away_team_id)
    }
    teams = tuple(item for item in fixture_batch.teams if item.team_id in team_ids)
    if {item.team_id for item in teams} != team_ids:
        raise ValueError("model analysis fixture is missing a team")
    return _SelectedModelInputs(
        competitions=competitions,
        teams=teams,
        matches=matches,
        mappings=mappings,
        market_snapshots=market_snapshots,
        sporttery_snapshots=sporttery_snapshots,
    )


def _validate_runtime_outputs(
    provenance: dict[str, RuntimeProvenance],
    fixture_batch: FixtureBatch,
    odds_batch: MarketOddsBatch,
    sporttery_batch: SportteryBatch,
    training_batch: EloTrainingHistoryBatch | None,
) -> None:
    if not provenance:
        return
    actual_codes = {
        "fixture": {item.provider_code for item in fixture_batch.mappings},
        "market_odds": {
            *(item.provider_code for item in odds_batch.snapshots),
            *(item.provider_code for item in odds_batch.mappings),
        },
        "sporttery": {
            *(item.provider_code for item in sporttery_batch.snapshots),
            *(item.provider_code for item in sporttery_batch.mappings),
        },
    }
    if training_batch is not None:
        actual_codes["model_training"] = {
            source.archive.provider_code for source in training_batch.sources
        }
    for role, codes in actual_codes.items():
        if any(code != provenance[role].provider_code for code in codes):
            raise ProviderRuntimeProvenanceMismatchError(
                f"{role} provider emitted data outside declared provenance"
            )


def _by_match(items: tuple, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        if item.match_id in result:
            raise ValueError(f"multiple {label} inputs for match {item.match_id}")
        result[item.match_id] = item
    return result
