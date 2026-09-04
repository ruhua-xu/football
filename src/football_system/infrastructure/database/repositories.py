from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.live_sources import (
    PreparationStatus,
    PreparedLiveSourceBundle,
)
from football_system.application.models import AnalysisArtifacts, StoredInputManifest
from football_system.domain.analysis import (
    AnalysisMatchContext,
    AnalysisRunStatus,
    ModelAnalysisMatchContext,
)
from football_system.domain.betting import SportteryRules
from football_system.domain.common import stable_id
from football_system.domain.market import MarketKey, MarketType, ThreeWayProbability
from football_system.domain.prediction import (
    ModelQuantPrediction,
    QuantModelEvaluation,
    QuantModelEvaluationStatus,
    QuantModelStateArtifact,
    QuantModelTrainingFactRef,
)
from football_system.domain.services.payout import calculate_stake_fen
from football_system.domain.services.risk import analyze_portfolio_risk
from football_system.infrastructure.database.models import (
    AnalysisRunMatchRecord,
    AnalysisRunRecord,
    BetCandidateRecord,
    BookmakerRecord,
    CompetitionRecord,
    FinalPredictionOutcomeRecord,
    FinalPredictionRecord,
    LiveAnalysisRunPreparationRecord,
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
    QuantModelEvaluationRecord,
    QuantModelStateRecord,
    QuantModelTrainingFactRecord,
    MatchResultRecord,
    SportteryBonusQuoteRecord,
    SportteryBonusSnapshotRecord,
    TeamRecord,
    TicketCandidateLegRecord,
    TicketCandidateRecord,
    TicketLegRecord,
    TicketRecord,
)
from football_system.infrastructure.database.live_source_repositories import (
    SqlAlchemyLiveSourceRepository,
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
        prepared_bundle = self._prepared_bundle(artifacts)
        with self._session_factory.begin() as session:
            run = artifacts.analysis_run
            existing_run = session.get(AnalysisRunRecord, run.analysis_run_id)
            if existing_run is not None:
                self._assert_existing_analysis_matches(
                    session,
                    existing_run,
                    artifacts,
                    rules,
                    prepared_bundle,
                )
                return

            if prepared_bundle is None:
                self._persist_sources(session, artifacts)
            self._assert_model_training_sources(session, artifacts)
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

            self._persist_quant_model_artifacts(session, artifacts)
            session.add_all(
                AnalysisRunMatchRecord(
                    analysis_run_id=context.analysis_run_id,
                    internal_match_id=context.match_id,
                    market_odds_snapshot_id=context.market_odds_snapshot_id,
                    sporttery_bonus_snapshot_id=context.sporttery_bonus_snapshot_id,
                    manual_quant_input_id=(
                        context.manual_quant_input_id
                        if isinstance(context, AnalysisMatchContext)
                        else None
                    ),
                    quant_model_evaluation_id=(
                        context.quant_model_evaluation_id
                        if not isinstance(context, AnalysisMatchContext)
                        else None
                    ),
                    context_json=context.context_json,
                    context_hash=context.context_hash,
                )
                for context in artifacts.match_contexts
            )
            session.flush()
            if prepared_bundle is not None:
                session.add(
                    LiveAnalysisRunPreparationRecord(
                        analysis_run_id=run.analysis_run_id,
                        preparation_id=prepared_bundle.preparation.preparation_id,
                    )
                )
                session.flush()
            self._persist_predictions(session, artifacts)
            self._persist_betting(session, artifacts)
            self._persist_risk(session, artifacts)
            session.flush()
            run_record.status = AnalysisRunStatus.COMPLETED.value
            run_record.completed_at_utc = run.completed_at_utc

    def _assert_existing_analysis_matches(
        self,
        session: Session,
        record: AnalysisRunRecord,
        artifacts: AnalysisArtifacts,
        rules: SportteryRules,
        prepared_bundle: PreparedLiveSourceBundle | None,
    ) -> None:
        run = artifacts.analysis_run
        if record.status != AnalysisRunStatus.COMPLETED.value:
            raise ValueError("existing AnalysisRun is not COMPLETED")
        _assert_record_fields(
            record,
            {
                "analysis_run_id": run.analysis_run_id,
                "run_kind": run.run_kind,
                "as_of_at_utc": run.as_of_at_utc,
                "status": run.status.value,
                "started_at_utc": run.started_at_utc,
                "completed_at_utc": run.completed_at_utc,
                "pipeline_version": run.pipeline_version,
                "code_revision": run.code_revision,
                "config_json": run.config_json,
                "config_hash": run.config_hash,
                "input_manifest_version": run.input_manifest_version,
                "input_manifest_json": run.input_manifest_json,
                "input_manifest_hash": run.input_manifest_hash,
                "replay_of_run_id": run.replay_of_run_id,
            },
            f"AnalysisRun {run.analysis_run_id}",
        )
        self._assert_rules_match_run(artifacts, rules)
        if prepared_bundle is None:
            self._assert_sources_match(session, artifacts)
        else:
            self._assert_model_training_sources(session, artifacts)
        self._assert_preparation_relation(session, record, prepared_bundle)
        self._assert_source_manifest_matches(artifacts)
        self._assert_run_graph_matches(session, artifacts)

    def _prepared_bundle(
        self,
        artifacts: AnalysisArtifacts,
    ) -> PreparedLiveSourceBundle | None:
        preparation_id = artifacts.live_source_preparation_id
        if preparation_id is None:
            return None
        bundle = SqlAlchemyLiveSourceRepository(
            self._session_factory
        ).load_prepared_sources(preparation_id)
        if bundle.preparation.status is not PreparationStatus.ANALYSIS_INPUT_READY:
            raise ValueError("live source preparation is not analysis-input ready")
        if artifacts.analysis_run.as_of_at_utc != (
            bundle.preparation.decision_as_of_at_utc
        ):
            raise ValueError("AnalysisRun cutoff conflicts with live source preparation")
        self._assert_source_manifest_matches(artifacts)
        _assert_domain_items_match(
            artifacts.competitions,
            bundle.fixtures.competitions,
            "competition_id",
            "prepared competitions",
        )
        _assert_domain_items_match(
            artifacts.teams,
            bundle.fixtures.teams,
            "team_id",
            "prepared teams",
        )
        _assert_domain_items_match(
            artifacts.matches,
            bundle.fixtures.matches,
            "match_id",
            "prepared matches",
        )
        _assert_domain_items_match(
            artifacts.provider_mappings,
            (
                *bundle.fixtures.mappings,
                *bundle.market_odds.mappings,
                *bundle.sporttery.mappings,
            ),
            "mapping_id",
            "prepared provider mappings",
        )
        _assert_domain_items_match(
            artifacts.market_odds_snapshots,
            bundle.market_odds.snapshots,
            "snapshot_id",
            "prepared market snapshots",
        )
        _assert_domain_items_match(
            artifacts.sporttery_bonus_snapshots,
            bundle.sporttery.snapshots,
            "snapshot_id",
            "prepared Sporttery snapshots",
        )
        prepared_by_match = {
            item.match_id: item
            for item in bundle.preparation.matches
            if item.data_quality.ready
        }
        contexts = {
            context.match_id: context for context in artifacts.match_contexts
        }
        if set(contexts) != set(prepared_by_match):
            raise ValueError("AnalysisRun matches conflict with live source preparation")
        for match_id, prepared in prepared_by_match.items():
            context = contexts[match_id]
            if not isinstance(context, ModelAnalysisMatchContext):
                raise ValueError("prepared analysis requires model match contexts")
            expected_context = {
                "match_id": match_id,
                "fixture_observation_id": prepared.fixture_observation_id,
                "market_odds_snapshot_id": prepared.market_consensus_snapshot_id,
                "sporttery_bonus_snapshot_id": prepared.sporttery_bonus_snapshot_id,
            }
            try:
                context_payload = json.loads(context.context_json)
            except json.JSONDecodeError as error:
                raise ValueError("prepared match context is not valid JSON") from error
            if (
                context.fixture_observation_id != prepared.fixture_observation_id
                or context.market_odds_snapshot_id
                != prepared.market_consensus_snapshot_id
                or context.sporttery_bonus_snapshot_id
                != prepared.sporttery_bonus_snapshot_id
                or not isinstance(context_payload, dict)
                or any(
                    context_payload.get(field) != value
                    for field, value in expected_context.items()
                )
            ):
                raise ValueError(
                    f"prepared match context conflicts with frozen sources: {match_id}"
                )
        return bundle

    @staticmethod
    def _assert_preparation_relation(
        session: Session,
        run: AnalysisRunRecord,
        prepared_bundle: PreparedLiveSourceBundle | None,
    ) -> None:
        record = session.get(LiveAnalysisRunPreparationRecord, run.analysis_run_id)
        if prepared_bundle is None:
            if record is not None:
                raise ValueError(
                    "existing AnalysisRun unexpectedly has live preparation lineage"
                )
            return
        if record is None or record.preparation_id != (
            prepared_bundle.preparation.preparation_id
        ):
            raise ValueError(
                "existing AnalysisRun conflicts with live source preparation lineage"
            )

    @staticmethod
    def _assert_source_manifest_matches(artifacts: AnalysisArtifacts) -> None:
        try:
            stored_manifest = json.loads(artifacts.analysis_run.input_manifest_json)
        except json.JSONDecodeError as error:
            raise ValueError("AnalysisRun source manifest is not valid JSON") from error
        if stored_manifest != _source_manifest_payload(artifacts):
            raise ValueError(
                "existing AnalysisRun source manifest conflicts with supplied sources"
            )

    @staticmethod
    def _assert_rules_match_run(
        artifacts: AnalysisArtifacts,
        rules: SportteryRules,
    ) -> None:
        try:
            config = json.loads(artifacts.analysis_run.config_json)
        except json.JSONDecodeError as error:
            raise ValueError("AnalysisRun config is not valid JSON") from error
        settings = config.get("settings") if isinstance(config, dict) else None
        sporttery = settings.get("sporttery") if isinstance(settings, dict) else None
        expected = {
            "rules_version": rules.version,
            "base_stake_fen": rules.base_stake_fen,
            "max_multiplier": rules.max_multiplier,
            "max_ticket_stake_fen": rules.max_ticket_stake_fen,
        }
        if not isinstance(sporttery, dict) or any(
            sporttery.get(field) != value for field, value in expected.items()
        ):
            raise ValueError(
                "existing AnalysisRun config conflicts with supplied Sporttery rules"
            )
        if any(
            candidate.base_stake_fen != rules.base_stake_fen
            or candidate.payout_policy_version != rules.version
            for candidate in artifacts.ticket_candidates
        ):
            raise ValueError(
                "existing AnalysisRun ticket candidates conflict with supplied "
                "Sporttery rules"
            )

    def _assert_sources_match(
        self,
        session: Session,
        artifacts: AnalysisArtifacts,
    ) -> None:
        self._assert_model_training_sources(session, artifacts)
        for item in artifacts.manual_quant_inputs:
            record = session.get(ManualQuantInputRecord, item.input_id)
            if record is not None:
                self._assert_manual_input_matches(session, record, item)
        for snapshot in artifacts.market_odds_snapshots:
            record = session.get(MarketOddsSnapshotRecord, snapshot.snapshot_id)
            if record is not None:
                self._assert_market_snapshot_matches(session, record, snapshot)
        for snapshot in artifacts.sporttery_bonus_snapshots:
            record = session.get(SportteryBonusSnapshotRecord, snapshot.snapshot_id)
            if record is not None:
                self._assert_sporttery_snapshot_matches(session, record, snapshot)

        competitions = _unique_source_items(
            artifacts.competitions,
            "competition_id",
            "competition",
        )
        teams = _unique_source_items(artifacts.teams, "team_id", "team")
        matches = _unique_source_items(artifacts.matches, "match_id", "match")
        provider_mappings = _unique_source_items(
            artifacts.provider_mappings,
            "mapping_id",
            "provider mapping",
        )
        provider_codes = (
            {mapping.provider_code for mapping in provider_mappings}
            | {snapshot.provider_code for snapshot in artifacts.market_odds_snapshots}
            | {
                snapshot.provider_code
                for snapshot in artifacts.sporttery_bonus_snapshots
            }
        )
        provider_ids = tuple(stable_id("provider", code) for code in provider_codes)
        _assert_exact_records(
            session.scalars(
                select(ProviderRecord).where(
                    ProviderRecord.provider_id.in_(provider_ids)
                )
            ),
            (
                {
                    "provider_id": stable_id("provider", code),
                    "code": code,
                    "name": code.replace("_", " ").title(),
                    "provider_kind": _provider_kind(code),
                }
                for code in provider_codes
            ),
            ("provider_id",),
            "providers",
        )

        bookmaker_codes = {
            snapshot.bookmaker_code for snapshot in artifacts.market_odds_snapshots
        }
        bookmaker_ids = tuple(stable_id("bookmaker", code) for code in bookmaker_codes)
        _assert_exact_records(
            session.scalars(
                select(BookmakerRecord).where(
                    BookmakerRecord.bookmaker_id.in_(bookmaker_ids)
                )
            ),
            (
                {
                    "bookmaker_id": stable_id("bookmaker", code),
                    "code": code,
                    "name": code.replace("_", " ").title(),
                }
                for code in bookmaker_codes
            ),
            ("bookmaker_id",),
            "bookmakers",
        )

        competition_ids = tuple(
            competition.competition_id for competition in competitions
        )
        _assert_exact_records(
            session.scalars(
                select(CompetitionRecord).where(
                    CompetitionRecord.competition_id.in_(competition_ids)
                )
            ),
            (
                {
                    "competition_id": competition.competition_id,
                    "canonical_key": competition.canonical_key,
                    "name": competition.name,
                    "country_code": competition.country_code,
                }
                for competition in competitions
            ),
            ("competition_id",),
            "competitions",
        )

        team_ids = tuple(team.team_id for team in teams)
        _assert_exact_records(
            session.scalars(select(TeamRecord).where(TeamRecord.team_id.in_(team_ids))),
            (
                {
                    "team_id": team.team_id,
                    "canonical_key": team.canonical_key,
                    "name": team.name,
                    "team_type": team.team_type.value,
                }
                for team in teams
            ),
            ("team_id",),
            "teams",
        )

        match_ids = tuple(match.match_id for match in matches)
        _assert_exact_records(
            session.scalars(
                select(MatchRecord).where(MatchRecord.internal_match_id.in_(match_ids))
            ),
            (
                {
                    "internal_match_id": match.match_id,
                    "competition_id": match.competition_id,
                    "home_team_id": match.home_team_id,
                    "away_team_id": match.away_team_id,
                    "kickoff_at_utc": match.kickoff_at_utc,
                    "status": match.status.value,
                    "available_at_utc": match.available_at_utc,
                }
                for match in matches
            ),
            ("internal_match_id",),
            "matches",
        )

        mapping_ids = tuple(mapping.mapping_id for mapping in provider_mappings)
        _assert_exact_records(
            session.scalars(
                select(ProviderMatchMappingRecord).where(
                    ProviderMatchMappingRecord.mapping_id.in_(mapping_ids)
                )
            ),
            (
                {
                    "mapping_id": mapping.mapping_id,
                    "provider_id": stable_id("provider", mapping.provider_code),
                    "external_namespace": mapping.external_namespace,
                    "external_match_id": mapping.external_match_id,
                    "internal_match_id": mapping.internal_match_id,
                    "resolution_method": mapping.resolution_method,
                    "confidence": mapping.confidence,
                    "available_at_utc": mapping.available_at_utc,
                    "supersedes_mapping_id": None,
                }
                for mapping in provider_mappings
            ),
            ("mapping_id",),
            "provider mappings",
        )

        manual_input_ids = tuple(
            item.input_id for item in artifacts.manual_quant_inputs
        )
        _assert_exact_records(
            session.scalars(
                select(ManualQuantInputRecord).where(
                    ManualQuantInputRecord.input_id.in_(manual_input_ids)
                )
            ),
            (
                {
                    "input_id": item.input_id,
                    "internal_match_id": item.match_id,
                    "market_key": item.market.canonical,
                    "market_type": item.market.market_type.value,
                    "handicap_value": item.market.handicap_value,
                    "available_at_utc": item.available_at_utc,
                    "payload_hash": item.payload_hash,
                }
                for item in artifacts.manual_quant_inputs
            ),
            ("input_id",),
            "manual quant inputs",
        )
        _assert_exact_records(
            session.scalars(
                select(ManualQuantInputOutcomeRecord).where(
                    ManualQuantInputOutcomeRecord.input_id.in_(manual_input_ids)
                )
            ),
            (
                {
                    "input_id": item.input_id,
                    "selection_key": selection.value,
                    "probability": probability,
                }
                for item in artifacts.manual_quant_inputs
                for selection, probability in item.probabilities.items()
            ),
            ("input_id", "selection_key"),
            "manual quant input outcomes",
        )

        market_snapshot_ids = tuple(
            snapshot.snapshot_id for snapshot in artifacts.market_odds_snapshots
        )
        _assert_exact_records(
            session.scalars(
                select(MarketOddsSnapshotRecord).where(
                    MarketOddsSnapshotRecord.snapshot_id.in_(market_snapshot_ids)
                )
            ),
            (
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "internal_match_id": snapshot.match_id,
                    "provider_id": stable_id("provider", snapshot.provider_code),
                    "bookmaker_id": stable_id("bookmaker", snapshot.bookmaker_code),
                    "market_key": snapshot.market.canonical,
                    "market_type": snapshot.market.market_type.value,
                    "handicap_value": snapshot.market.handicap_value,
                    "captured_at_utc": snapshot.captured_at_utc,
                    "available_at_utc": snapshot.available_at_utc,
                    "ingested_at_utc": snapshot.ingested_at_utc,
                    "source_snapshot_key": snapshot.source_snapshot_key,
                    "payload_hash": snapshot.payload_hash,
                }
                for snapshot in artifacts.market_odds_snapshots
            ),
            ("snapshot_id",),
            "market odds snapshots",
        )
        _assert_exact_records(
            session.scalars(
                select(MarketOddsQuoteRecord).where(
                    MarketOddsQuoteRecord.snapshot_id.in_(market_snapshot_ids)
                )
            ),
            (
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "selection_key": quote.selection.value,
                    "odds": quote.odds,
                }
                for snapshot in artifacts.market_odds_snapshots
                for quote in snapshot.quotes
            ),
            ("snapshot_id", "selection_key"),
            "market odds quotes",
        )

        sporttery_snapshot_ids = tuple(
            snapshot.snapshot_id for snapshot in artifacts.sporttery_bonus_snapshots
        )
        _assert_exact_records(
            session.scalars(
                select(SportteryBonusSnapshotRecord).where(
                    SportteryBonusSnapshotRecord.snapshot_id.in_(sporttery_snapshot_ids)
                )
            ),
            (
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "internal_match_id": snapshot.match_id,
                    "provider_id": stable_id("provider", snapshot.provider_code),
                    "sporttery_match_no": snapshot.sporttery_match_no,
                    "market_key": snapshot.market.canonical,
                    "market_type": snapshot.market.market_type.value,
                    "handicap_value": snapshot.market.handicap_value,
                    "sale_status": snapshot.sale_status.value,
                    "captured_at_utc": snapshot.captured_at_utc,
                    "available_at_utc": snapshot.available_at_utc,
                    "ingested_at_utc": snapshot.ingested_at_utc,
                    "source_snapshot_key": snapshot.source_snapshot_key,
                    "payload_hash": snapshot.payload_hash,
                }
                for snapshot in artifacts.sporttery_bonus_snapshots
            ),
            ("snapshot_id",),
            "Sporttery bonus snapshots",
        )
        _assert_exact_records(
            session.scalars(
                select(SportteryBonusQuoteRecord).where(
                    SportteryBonusQuoteRecord.snapshot_id.in_(sporttery_snapshot_ids)
                )
            ),
            (
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "selection_key": quote.selection.value,
                    "fixed_bonus": quote.fixed_bonus,
                }
                for snapshot in artifacts.sporttery_bonus_snapshots
                for quote in snapshot.quotes
            ),
            ("snapshot_id", "selection_key"),
            "Sporttery bonus quotes",
        )

    @staticmethod
    def _assert_model_training_sources(
        session: Session,
        artifacts: AnalysisArtifacts,
    ) -> None:
        for state in artifacts.quant_model_states:
            for fact in state.training_facts:
                result = session.get(MatchResultRecord, fact.match_result_id)
                if (
                    result is None
                    or result.internal_match_id != fact.match_id
                    or result.payload_hash != fact.source_payload_hash
                    or result.available_at_utc > state.cutoff_at_utc
                    or result.ingested_at_utc > state.cutoff_at_utc
                ):
                    raise ValueError(
                        "quant model training fact has unavailable source result: "
                        f"{fact.match_result_id}"
                    )
                visible_successor = session.scalar(
                    select(MatchResultRecord.match_result_id).where(
                        MatchResultRecord.supersedes_match_result_id
                        == fact.match_result_id,
                        MatchResultRecord.available_at_utc <= state.cutoff_at_utc,
                        MatchResultRecord.ingested_at_utc <= state.cutoff_at_utc,
                    )
                )
                if visible_successor is not None:
                    raise ValueError(
                        "quant model training fact is not the current result version: "
                        f"{fact.match_result_id}"
                    )

    def _assert_run_graph_matches(
        self,
        session: Session,
        artifacts: AnalysisArtifacts,
    ) -> None:
        run_id = artifacts.analysis_run.analysis_run_id
        state_ids = tuple(
            state.quant_model_state_id for state in artifacts.quant_model_states
        )
        _assert_exact_records(
            session.scalars(
                select(QuantModelStateRecord).where(
                    QuantModelStateRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "quant_model_state_id": state.quant_model_state_id,
                    "analysis_run_id": state.analysis_run_id,
                    "model_name": state.model_name,
                    "model_version": state.model_version,
                    "calibration_label": state.calibration_label,
                    "config_json": state.config_json,
                    "config_hash": state.config_hash,
                    "cutoff_at_utc": state.cutoff_at_utc,
                    "season_id": state.season_id,
                    "state_json": state.state_json,
                    "state_hash": state.state_hash,
                    "state_payload_hash": state.state_payload_hash,
                    "training_data_hash": state.training_data_hash,
                    "training_fact_count": len(state.training_facts),
                    "generated_at_utc": state.generated_at_utc,
                }
                for state in artifacts.quant_model_states
            ),
            ("quant_model_state_id",),
            "quant model states",
        )
        _assert_exact_records(
            session.scalars(
                select(QuantModelTrainingFactRecord).where(
                    QuantModelTrainingFactRecord.quant_model_state_id.in_(state_ids)
                )
            ),
            (
                {
                    "quant_model_state_id": state.quant_model_state_id,
                    "fact_sequence": fact.sequence,
                    "match_result_id": fact.match_result_id,
                    "internal_match_id": fact.match_id,
                    "source_payload_hash": fact.source_payload_hash,
                    "fact_hash": fact.fact_hash,
                }
                for state in artifacts.quant_model_states
                for fact in state.training_facts
            ),
            ("quant_model_state_id", "fact_sequence"),
            "quant model training facts",
        )
        _assert_exact_records(
            session.scalars(
                select(QuantModelEvaluationRecord).where(
                    QuantModelEvaluationRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "quant_model_evaluation_id": (
                        evaluation.quant_model_evaluation_id
                    ),
                    "analysis_run_id": evaluation.analysis_run_id,
                    "quant_model_state_id": evaluation.quant_model_state_id,
                    "internal_match_id": evaluation.match_id,
                    "market_key": evaluation.market.canonical,
                    "market_type": evaluation.market.market_type.value,
                    "handicap_value": evaluation.market.handicap_value,
                    "status": evaluation.status.value,
                    "unavailable_reason": evaluation.unavailable_reason,
                    "output_json": evaluation.output_json,
                    "output_hash": evaluation.output_hash,
                    "model_prediction_hash": evaluation.model_prediction_hash,
                    "evaluated_at_utc": evaluation.evaluated_at_utc,
                }
                for evaluation in artifacts.quant_model_evaluations
            ),
            ("quant_model_evaluation_id",),
            "quant model evaluations",
        )
        _assert_exact_records(
            session.scalars(
                select(AnalysisRunMatchRecord).where(
                    AnalysisRunMatchRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "analysis_run_id": context.analysis_run_id,
                    "internal_match_id": context.match_id,
                    "market_odds_snapshot_id": context.market_odds_snapshot_id,
                    "sporttery_bonus_snapshot_id": (
                        context.sporttery_bonus_snapshot_id
                    ),
                    "manual_quant_input_id": (
                        context.manual_quant_input_id
                        if isinstance(context, AnalysisMatchContext)
                        else None
                    ),
                    "quant_model_evaluation_id": (
                        context.quant_model_evaluation_id
                        if not isinstance(context, AnalysisMatchContext)
                        else None
                    ),
                    "context_json": context.context_json,
                    "context_hash": context.context_hash,
                }
                for context in artifacts.match_contexts
            ),
            ("analysis_run_id", "internal_match_id"),
            "match contexts",
        )

        market_prediction_ids = tuple(
            prediction.prediction_id for prediction in artifacts.market_predictions
        )
        _assert_exact_records(
            session.scalars(
                select(MarketProbabilityRecord).where(
                    MarketProbabilityRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "market_probability_id": prediction.prediction_id,
                    "analysis_run_id": prediction.analysis_run_id,
                    "internal_match_id": prediction.match_id,
                    "market_key": prediction.market.canonical,
                    "market_type": prediction.market.market_type.value,
                    "handicap_value": prediction.market.handicap_value,
                    "devig_method": prediction.devig_method,
                    "devig_version": prediction.devig_version,
                    "overround": prediction.overround,
                    "generated_at_utc": prediction.generated_at_utc,
                }
                for prediction in artifacts.market_predictions
            ),
            ("market_probability_id",),
            "market predictions",
        )
        _assert_exact_records(
            session.scalars(
                select(MarketProbabilityOutcomeRecord).where(
                    MarketProbabilityOutcomeRecord.market_probability_id.in_(
                        market_prediction_ids
                    )
                )
            ),
            (
                {
                    "market_probability_id": prediction.prediction_id,
                    "selection_key": selection.value,
                    "probability": probability,
                }
                for prediction in artifacts.market_predictions
                for selection, probability in prediction.probabilities.items()
            ),
            ("market_probability_id", "selection_key"),
            "market prediction outcomes",
        )
        _assert_exact_records(
            session.scalars(
                select(MarketProbabilityInputRecord).where(
                    MarketProbabilityInputRecord.market_probability_id.in_(
                        market_prediction_ids
                    )
                )
            ),
            (
                {
                    "market_probability_id": prediction.prediction_id,
                    "market_odds_snapshot_id": snapshot_id,
                }
                for prediction in artifacts.market_predictions
                for snapshot_id in prediction.input_snapshot_ids
            ),
            ("market_probability_id", "market_odds_snapshot_id"),
            "market prediction inputs",
        )

        quant_prediction_ids = tuple(
            prediction.prediction_id for prediction in artifacts.quant_predictions
        )
        _assert_exact_records(
            session.scalars(
                select(QuantPredictionRecord).where(
                    QuantPredictionRecord.analysis_run_id == run_id
                )
            ),
            (
                _quant_prediction_record_values(prediction)
                for prediction in artifacts.quant_predictions
            ),
            ("quant_prediction_id",),
            "quant predictions",
        )
        _assert_exact_records(
            session.scalars(
                select(QuantPredictionOutcomeRecord).where(
                    QuantPredictionOutcomeRecord.quant_prediction_id.in_(
                        quant_prediction_ids
                    )
                )
            ),
            (
                {
                    "quant_prediction_id": prediction.prediction_id,
                    "selection_key": selection.value,
                    "probability": probability,
                }
                for prediction in artifacts.quant_predictions
                for selection, probability in prediction.probabilities.items()
            ),
            ("quant_prediction_id", "selection_key"),
            "quant prediction outcomes",
        )

        final_prediction_ids = tuple(
            prediction.prediction_id for prediction in artifacts.final_predictions
        )
        _assert_exact_records(
            session.scalars(
                select(FinalPredictionRecord).where(
                    FinalPredictionRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "final_prediction_id": prediction.prediction_id,
                    "analysis_run_id": prediction.analysis_run_id,
                    "internal_match_id": prediction.match_id,
                    "market_key": prediction.market.canonical,
                    "market_type": prediction.market.market_type.value,
                    "handicap_value": prediction.market.handicap_value,
                    "market_probability_id": prediction.market_prediction_id,
                    "quant_prediction_id": prediction.quant_prediction_id,
                    "llm_assessment_id": prediction.llm_assessment_id,
                    "fusion_policy": prediction.fusion_policy.value,
                    "fusion_version": prediction.fusion_version,
                    "fusion_config_json": prediction.fusion_config_json,
                    "fallback_code": prediction.fallback_code,
                    "confidence": prediction.confidence,
                    "generated_at_utc": prediction.generated_at_utc,
                }
                for prediction in artifacts.final_predictions
            ),
            ("final_prediction_id",),
            "final predictions",
        )
        _assert_exact_records(
            session.scalars(
                select(FinalPredictionOutcomeRecord).where(
                    FinalPredictionOutcomeRecord.final_prediction_id.in_(
                        final_prediction_ids
                    )
                )
            ),
            (
                {
                    "final_prediction_id": prediction.prediction_id,
                    "selection_key": selection.value,
                    "probability": probability,
                }
                for prediction in artifacts.final_predictions
                for selection, probability in prediction.probabilities.items()
            ),
            ("final_prediction_id", "selection_key"),
            "final prediction outcomes",
        )
        self._assert_betting_graph_matches(session, artifacts)
        self._assert_risk_graph_matches(session, artifacts)

    @staticmethod
    def _assert_betting_graph_matches(
        session: Session,
        artifacts: AnalysisArtifacts,
    ) -> None:
        run_id = artifacts.analysis_run.analysis_run_id
        _assert_exact_records(
            session.scalars(
                select(BetCandidateRecord).where(
                    BetCandidateRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "candidate_id": candidate.candidate_id,
                    "analysis_run_id": candidate.analysis_run_id,
                    "internal_match_id": candidate.match_id,
                    "final_prediction_id": candidate.final_prediction_id,
                    "sporttery_bonus_snapshot_id": (
                        candidate.sporttery_bonus_snapshot_id
                    ),
                    "market_key": candidate.market.canonical,
                    "selection_key": candidate.selection.value,
                    "probability_used": candidate.probability,
                    "fixed_bonus": candidate.fixed_bonus,
                    "break_even_probability": candidate.break_even_probability,
                    "ev": candidate.ev,
                    "eligibility_status": candidate.status.value,
                    "rejection_code": candidate.rejection_code,
                }
                for candidate in artifacts.selection_candidates
            ),
            ("candidate_id",),
            "selection candidates",
        )

        ticket_candidate_ids = tuple(
            ticket.ticket_candidate_id for ticket in artifacts.ticket_candidates
        )
        _assert_exact_records(
            session.scalars(
                select(TicketCandidateRecord).where(
                    TicketCandidateRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "ticket_candidate_id": ticket.ticket_candidate_id,
                    "analysis_run_id": ticket.analysis_run_id,
                    "pass_type": ticket.pass_type.value,
                    "atomic_bet_count": ticket.atomic_bet_count,
                    "base_stake_fen": ticket.base_stake_fen,
                    "joint_probability": ticket.joint_probability,
                    "gross_payout_fen": ticket.gross_payout_fen,
                    "expected_gross_payout_fen": ticket.expected_gross_payout_fen,
                    "expected_profit_fen": ticket.expected_profit_fen,
                    "expected_roi": ticket.expected_roi,
                    "payout_policy_version": ticket.payout_policy_version,
                }
                for ticket in artifacts.ticket_candidates
            ),
            ("ticket_candidate_id",),
            "ticket candidates",
        )
        _assert_exact_records(
            session.scalars(
                select(TicketCandidateLegRecord).where(
                    TicketCandidateLegRecord.ticket_candidate_id.in_(
                        ticket_candidate_ids
                    )
                )
            ),
            (
                {
                    "ticket_candidate_id": ticket.ticket_candidate_id,
                    "leg_no": leg_no,
                    "candidate_id": leg.candidate_id,
                    "internal_match_id": leg.match_id,
                }
                for ticket in artifacts.ticket_candidates
                for leg_no, leg in enumerate(ticket.legs, start=1)
            ),
            ("ticket_candidate_id", "leg_no"),
            "ordered ticket candidate legs",
        )

        portfolio_ids = tuple(
            portfolio.portfolio_id for portfolio in artifacts.portfolios
        )
        _assert_exact_records(
            session.scalars(
                select(PortfolioRecord).where(PortfolioRecord.analysis_run_id == run_id)
            ),
            (
                {
                    "portfolio_id": portfolio.portfolio_id,
                    "analysis_run_id": portfolio.analysis_run_id,
                    "budget_fen": portfolio.budget_fen,
                    "total_stake_fen": portfolio.total_stake_fen,
                    "unused_budget_fen": portfolio.unused_budget_fen,
                    "status": portfolio.status.value,
                    "no_bet_reason": (
                        portfolio.no_bet_reason.value
                        if portfolio.no_bet_reason
                        else None
                    ),
                    "strategy_version": portfolio.strategy_version,
                    "strategy_config_json": _canonical_json(
                        portfolio.constraints.model_dump(mode="json")
                    ),
                }
                for portfolio in artifacts.portfolios
            ),
            ("portfolio_id",),
            "portfolios",
        )
        _assert_exact_records(
            session.scalars(
                select(PortfolioCashPositionRecord).where(
                    PortfolioCashPositionRecord.portfolio_id.in_(portfolio_ids)
                )
            ),
            (
                {
                    "cash_position_id": portfolio.cash_position.position_id,
                    "portfolio_id": portfolio.portfolio_id,
                    "amount_fen": portfolio.cash_position.amount_fen,
                    "expected_profit_fen": (
                        portfolio.cash_position.expected_profit_fen
                    ),
                }
                for portfolio in artifacts.portfolios
            ),
            ("cash_position_id",),
            "portfolio cash positions",
        )

        tickets = tuple(
            (portfolio, ticket)
            for portfolio in artifacts.portfolios
            for ticket in portfolio.tickets
        )
        ticket_ids = tuple(ticket.ticket_id for _, ticket in tickets)
        _assert_exact_records(
            session.scalars(
                select(TicketRecord).where(TicketRecord.portfolio_id.in_(portfolio_ids))
            ),
            (
                {
                    "ticket_id": ticket.ticket_id,
                    "portfolio_id": portfolio.portfolio_id,
                    "ticket_candidate_id": ticket.candidate.ticket_candidate_id,
                    "ticket_no": ticket.ticket_no,
                    "pass_type": ticket.candidate.pass_type.value,
                    "role": None,
                    "multiplier": ticket.multiplier,
                    "atomic_bet_count": ticket.candidate.atomic_bet_count,
                    "base_stake_fen": ticket.candidate.base_stake_fen,
                    "stake_fen": ticket.stake_fen,
                    "potential_gross_payout_fen": (ticket.potential_gross_payout_fen),
                    "expected_gross_payout_fen": ticket.expected_gross_payout_fen,
                    "expected_profit_fen": ticket.expected_profit_fen,
                    "expected_roi": ticket.expected_roi,
                    "probability_any_payout": ticket.probability_any_payout,
                    "payout_policy_version": (ticket.candidate.payout_policy_version),
                }
                for portfolio, ticket in tickets
            ),
            ("ticket_id",),
            "portfolio tickets",
        )
        _assert_exact_records(
            session.scalars(
                select(TicketLegRecord).where(TicketLegRecord.ticket_id.in_(ticket_ids))
            ),
            (
                {
                    "ticket_id": ticket.ticket_id,
                    "leg_no": leg_no,
                    "candidate_id": leg.candidate_id,
                    "internal_match_id": leg.match_id,
                }
                for _, ticket in tickets
                for leg_no, leg in enumerate(ticket.candidate.legs, start=1)
            ),
            ("ticket_id", "leg_no"),
            "ordered portfolio ticket legs",
        )

    @staticmethod
    def _assert_risk_graph_matches(
        session: Session,
        artifacts: AnalysisArtifacts,
    ) -> None:
        run_id = artifacts.analysis_run.analysis_run_id
        report_ids = tuple(
            report.risk_report_id for report in artifacts.portfolio_risk_reports
        )
        _assert_exact_records(
            session.scalars(
                select(PortfolioRiskReportRecord).where(
                    PortfolioRiskReportRecord.analysis_run_id == run_id
                )
            ),
            (
                {
                    "risk_report_id": report.risk_report_id,
                    "analysis_run_id": report.analysis_run_id,
                    "portfolio_id": report.portfolio_id,
                    "policy_version": report.policy_version,
                    "budget_fen": report.budget_fen,
                    "total_stake_fen": report.total_stake_fen,
                    "cash_fen": report.cash_fen,
                    "cash_ratio": report.cash_ratio,
                    "expected_profit_fen": report.expected_profit_fen,
                    "total_stake_at_risk_fen": report.total_stake_at_risk_fen,
                    "max_single_ticket_exposure_fen": (
                        report.max_single_ticket_exposure_fen
                    ),
                    "max_match_exposure_fen": report.max_match_exposure_fen,
                }
                for report in artifacts.portfolio_risk_reports
            ),
            ("risk_report_id",),
            "portfolio risk reports",
        )
        _assert_exact_records(
            session.scalars(
                select(PortfolioMatchExposureRecord).where(
                    PortfolioMatchExposureRecord.risk_report_id.in_(report_ids)
                )
            ),
            (
                {
                    "exposure_id": exposure.exposure_id,
                    "risk_report_id": exposure.risk_report_id,
                    "internal_match_id": exposure.match_id,
                    "exposed_stake_fen": exposure.exposed_stake_fen,
                    "budget_ratio": exposure.budget_ratio,
                    "deployed_ratio": exposure.deployed_ratio,
                    "ticket_count": len(exposure.ticket_ids),
                    "ticket_ids_json": json.dumps(
                        exposure.ticket_ids,
                        separators=(",", ":"),
                    ),
                }
                for report in artifacts.portfolio_risk_reports
                for exposure in report.match_exposures
            ),
            ("exposure_id",),
            "portfolio match exposures",
        )
        _assert_exact_records(
            session.scalars(
                select(PortfolioSelectionExposureRecord).where(
                    PortfolioSelectionExposureRecord.risk_report_id.in_(report_ids)
                )
            ),
            (
                {
                    "exposure_id": exposure.exposure_id,
                    "risk_report_id": exposure.risk_report_id,
                    "internal_match_id": exposure.match_id,
                    "market_key": exposure.market.canonical,
                    "selection_key": exposure.selection.value,
                    "exposed_stake_fen": exposure.exposed_stake_fen,
                    "budget_ratio": exposure.budget_ratio,
                    "deployed_ratio": exposure.deployed_ratio,
                    "ticket_count": len(exposure.ticket_ids),
                    "ticket_ids_json": json.dumps(
                        exposure.ticket_ids,
                        separators=(",", ":"),
                    ),
                }
                for report in artifacts.portfolio_risk_reports
                for exposure in report.selection_exposures
            ),
            ("exposure_id",),
            "portfolio selection exposures",
        )

        stress_results = tuple(
            result
            for report in artifacts.portfolio_risk_reports
            for result in report.stress_results
        )
        scenario_ids = tuple(result.scenario_id for result in stress_results)
        _assert_exact_records(
            session.scalars(
                select(PortfolioStressResultRecord).where(
                    PortfolioStressResultRecord.risk_report_id.in_(report_ids)
                )
            ),
            (
                {
                    "scenario_id": result.scenario_id,
                    "risk_report_id": result.risk_report_id,
                    "portfolio_id": result.portfolio_id,
                    "scenario_key": result.scenario_key,
                    "policy_version": result.policy_version,
                    "outcomes_json": json.dumps(
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
                    "is_complete": result.is_complete,
                    "scenario_exposed_stake_fen": (result.scenario_exposed_stake_fen),
                    "scenario_exposure_ratio": result.scenario_exposure_ratio,
                    "gross_payout_fen": result.gross_payout_fen,
                    "ending_capital_fen": result.ending_capital_fen,
                    "profit_loss_fen": result.profit_loss_fen,
                    "capital_recovery_ratio": result.capital_recovery_ratio,
                    "minimum_ending_capital_fen": (result.minimum_ending_capital_fen),
                    "maximum_ending_capital_fen": (result.maximum_ending_capital_fen),
                }
                for result in stress_results
            ),
            ("scenario_id",),
            "portfolio stress results",
        )
        _assert_exact_records(
            session.scalars(
                select(PortfolioStressTicketResultRecord).where(
                    PortfolioStressTicketResultRecord.scenario_id.in_(scenario_ids)
                )
            ),
            (
                {
                    "scenario_id": result.scenario_id,
                    "ticket_id": ticket_result.ticket_id,
                    "result_state": ticket_result.state.value,
                    "gross_payout_fen": ticket_result.gross_payout_fen,
                }
                for result in stress_results
                for ticket_result in result.ticket_results
            ),
            ("scenario_id", "ticket_id"),
            "portfolio stress ticket results",
        )

    def _persist_sources(self, session: Session, artifacts: AnalysisArtifacts) -> None:
        provider_codes = (
            {mapping.provider_code for mapping in artifacts.provider_mappings}
            | {snapshot.provider_code for snapshot in artifacts.market_odds_snapshots}
            | {
                snapshot.provider_code
                for snapshot in artifacts.sporttery_bonus_snapshots
            }
        )
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

    @staticmethod
    def _persist_quant_model_artifacts(
        session: Session,
        artifacts: AnalysisArtifacts,
    ) -> None:
        for state in artifacts.quant_model_states:
            session.add(
                QuantModelStateRecord(
                    quant_model_state_id=state.quant_model_state_id,
                    analysis_run_id=state.analysis_run_id,
                    model_name=state.model_name,
                    model_version=state.model_version,
                    calibration_label=state.calibration_label,
                    config_json=state.config_json,
                    config_hash=state.config_hash,
                    cutoff_at_utc=state.cutoff_at_utc,
                    season_id=state.season_id,
                    state_json=state.state_json,
                    state_hash=state.state_hash,
                    state_payload_hash=state.state_payload_hash,
                    training_data_hash=state.training_data_hash,
                    training_fact_count=len(state.training_facts),
                    generated_at_utc=state.generated_at_utc,
                )
            )
        session.flush()
        for state in artifacts.quant_model_states:
            session.add_all(
                QuantModelTrainingFactRecord(
                    quant_model_state_id=state.quant_model_state_id,
                    fact_sequence=fact.sequence,
                    match_result_id=fact.match_result_id,
                    internal_match_id=fact.match_id,
                    source_payload_hash=fact.source_payload_hash,
                    fact_hash=fact.fact_hash,
                )
                for fact in state.training_facts
            )
        session.flush()
        for evaluation in artifacts.quant_model_evaluations:
            session.add(
                QuantModelEvaluationRecord(
                    quant_model_evaluation_id=(
                        evaluation.quant_model_evaluation_id
                    ),
                    analysis_run_id=evaluation.analysis_run_id,
                    quant_model_state_id=evaluation.quant_model_state_id,
                    internal_match_id=evaluation.match_id,
                    market_key=evaluation.market.canonical,
                    market_type=evaluation.market.market_type.value,
                    handicap_value=evaluation.market.handicap_value,
                    status=evaluation.status.value,
                    unavailable_reason=evaluation.unavailable_reason,
                    output_json=evaluation.output_json,
                    output_hash=evaluation.output_hash,
                    model_prediction_hash=evaluation.model_prediction_hash,
                    evaluated_at_utc=evaluation.evaluated_at_utc,
                )
            )
        session.flush()

    def _persist_predictions(
        self, session: Session, artifacts: AnalysisArtifacts
    ) -> None:
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
            is_model = isinstance(prediction, ModelQuantPrediction)
            session.add(
                QuantPredictionRecord(
                    quant_prediction_id=prediction.prediction_id,
                    analysis_run_id=prediction.analysis_run_id,
                    internal_match_id=prediction.match_id,
                    market_key=prediction.market.canonical,
                    market_type=prediction.market.market_type.value,
                    handicap_value=prediction.market.handicap_value,
                    manual_input_id=(None if is_model else prediction.manual_input_id),
                    input_payload_hash=(
                        None if is_model else prediction.input_payload_hash
                    ),
                    quant_model_evaluation_id=(
                        prediction.quant_model_evaluation_id if is_model else None
                    ),
                    method=prediction.method,
                    method_version=prediction.method_version,
                    entered_at_utc=(
                        None if is_model else prediction.entered_at_utc
                    ),
                    generated_at_utc=(
                        prediction.generated_at_utc if is_model else None
                    ),
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
                        portfolio.no_bet_reason.value
                        if portfolio.no_bet_reason
                        else None
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
                        scenario_exposed_stake_fen=(result.scenario_exposed_stake_fen),
                        scenario_exposure_ratio=result.scenario_exposure_ratio,
                        gross_payout_fen=result.gross_payout_fen,
                        ending_capital_fen=result.ending_capital_fen,
                        profit_loss_fen=result.profit_loss_fen,
                        capital_recovery_ratio=result.capital_recovery_ratio,
                        minimum_ending_capital_fen=(result.minimum_ending_capital_fen),
                        maximum_ending_capital_fen=(result.maximum_ending_capital_fen),
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
                    raise ValueError(
                        "ticket stake does not match versioned Sporttery rules"
                    )

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
        for state in artifacts.quant_model_states:
            if _sha256(state.config_json) != state.config_hash:
                raise ValueError("quant model config hash does not match config JSON")
            if _sha256(state.state_json) != state.state_payload_hash:
                raise ValueError("quant model state payload hash does not match state JSON")
        for evaluation in artifacts.quant_model_evaluations:
            if _sha256(evaluation.output_json) != evaluation.output_hash:
                raise ValueError("quant model output hash does not match output JSON")

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
        if len(market_sources) != len(artifacts.market_odds_snapshots) or len(
            bonus_sources
        ) != len(artifacts.sporttery_bonus_snapshots):
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
        manual_inputs = {item.input_id: item for item in artifacts.manual_quant_inputs}
        model_states = {
            item.quant_model_state_id: item for item in artifacts.quant_model_states
        }
        model_evaluations = {
            item.quant_model_evaluation_id: item
            for item in artifacts.quant_model_evaluations
        }
        for context in artifacts.match_contexts:
            if isinstance(context, AnalysisMatchContext):
                manual_input = manual_inputs.get(context.manual_quant_input_id)
                if manual_input is None or manual_input.match_id != context.match_id:
                    raise ValueError(
                        "match context has inconsistent manual input lineage"
                    )
            else:
                evaluation = model_evaluations.get(
                    context.quant_model_evaluation_id
                )
                if evaluation is None or evaluation.match_id != context.match_id:
                    raise ValueError(
                        "match context has inconsistent model evaluation lineage"
                    )
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
            context = context_by_match.get(prediction.match_id)
            if isinstance(prediction, ModelQuantPrediction):
                evaluation = model_evaluations.get(
                    prediction.quant_model_evaluation_id
                )
                state = (
                    model_states.get(evaluation.quant_model_state_id)
                    if evaluation is not None
                    else None
                )
                if (
                    prediction.analysis_run_id != run_id
                    or context is None
                    or evaluation is None
                    or state is None
                    or evaluation.status
                    is not QuantModelEvaluationStatus.AVAILABLE
                    or evaluation.match_id != prediction.match_id
                    or evaluation.market != prediction.market
                    or evaluation.probabilities != prediction.probabilities
                    or getattr(context, "quant_model_evaluation_id", None)
                    != evaluation.quant_model_evaluation_id
                    or prediction.method != state.model_name
                    or prediction.method_version != state.model_version
                ):
                    raise ValueError(
                        "quant prediction has inconsistent model input lineage"
                    )
            else:
                manual_input = manual_inputs.get(prediction.manual_input_id)
                if (
                    prediction.analysis_run_id != run_id
                    or context is None
                    or manual_input is None
                    or manual_input.match_id != prediction.match_id
                    or manual_input.market != prediction.market
                    or manual_input.payload_hash != prediction.input_payload_hash
                    or prediction.manual_input_id
                    != getattr(context, "manual_quant_input_id", None)
                ):
                    raise ValueError(
                        "quant prediction has inconsistent manual input lineage"
                    )
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
                raise ValueError(
                    "final prediction references a missing upstream prediction"
                )
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
            report.portfolio_id: report for report in artifacts.portfolio_risk_reports
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
                or report != analyze_portfolio_risk(portfolio)
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
    def _assert_manual_input_matches(
        session: Session, record: object, value: object
    ) -> None:
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
    def _assert_market_snapshot_matches(
        session: Session, record: object, value: object
    ) -> None:
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

    def load_quant_model_state(
        self,
        quant_model_state_id: str,
    ) -> QuantModelStateArtifact:
        with self._session_factory() as session:
            record = session.get(QuantModelStateRecord, quant_model_state_id)
            if record is None:
                raise KeyError(f"unknown quant model state: {quant_model_state_id}")
            facts = tuple(
                session.scalars(
                    select(QuantModelTrainingFactRecord)
                    .where(
                        QuantModelTrainingFactRecord.quant_model_state_id
                        == quant_model_state_id
                    )
                    .order_by(QuantModelTrainingFactRecord.fact_sequence)
                )
            )
            return QuantModelStateArtifact(
                quant_model_state_id=record.quant_model_state_id,
                analysis_run_id=record.analysis_run_id,
                model_name=record.model_name,
                model_version=record.model_version,
                calibration_label=record.calibration_label,
                config_json=record.config_json,
                config_hash=record.config_hash,
                cutoff_at_utc=record.cutoff_at_utc,
                season_id=record.season_id,
                state_json=record.state_json,
                state_hash=record.state_hash,
                state_payload_hash=record.state_payload_hash,
                training_data_hash=record.training_data_hash,
                training_facts=tuple(
                    QuantModelTrainingFactRef(
                        sequence=fact.fact_sequence,
                        match_result_id=fact.match_result_id,
                        match_id=fact.internal_match_id,
                        source_payload_hash=fact.source_payload_hash,
                        fact_hash=fact.fact_hash,
                    )
                    for fact in facts
                ),
                generated_at_utc=record.generated_at_utc,
            )

    def load_quant_model_evaluation(
        self,
        quant_model_evaluation_id: str,
    ) -> QuantModelEvaluation:
        with self._session_factory() as session:
            record = session.get(
                QuantModelEvaluationRecord,
                quant_model_evaluation_id,
            )
            if record is None:
                raise KeyError(
                    "unknown quant model evaluation: "
                    f"{quant_model_evaluation_id}"
                )
            output = json.loads(record.output_json)
            status = QuantModelEvaluationStatus(record.status)
            probabilities = (
                ThreeWayProbability.model_validate(output["probabilities"])
                if status is QuantModelEvaluationStatus.AVAILABLE
                else None
            )
            return QuantModelEvaluation(
                quant_model_evaluation_id=record.quant_model_evaluation_id,
                analysis_run_id=record.analysis_run_id,
                quant_model_state_id=record.quant_model_state_id,
                match_id=record.internal_match_id,
                market=MarketKey(
                    market_type=MarketType(record.market_type),
                    handicap_value=record.handicap_value,
                ),
                status=status,
                unavailable_reason=record.unavailable_reason,
                probabilities=probabilities,
                output_json=record.output_json,
                output_hash=record.output_hash,
                model_prediction_hash=record.model_prediction_hash,
                evaluated_at_utc=record.evaluated_at_utc,
            )

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
                table.__tablename__: session.scalar(
                    select(func.count()).select_from(table)
                )
                or 0
                for table in tables
            }

    def quant_model_table_counts(self) -> dict[str, int]:
        tables: Iterable[type] = (
            QuantModelStateRecord,
            QuantModelTrainingFactRecord,
            QuantModelEvaluationRecord,
        )
        with self._session_factory() as session:
            return {
                table.__tablename__: session.scalar(
                    select(func.count()).select_from(table)
                )
                or 0
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


def _unique_source_items(
    items: Iterable[object],
    identity_field: str,
    label: str,
) -> tuple[object, ...]:
    unique: dict[object, object] = {}
    for item in items:
        identity = getattr(item, identity_field)
        existing = unique.get(identity)
        if existing is not None and existing != item:
            raise ValueError(f"conflicting duplicate {label}: {identity}")
        unique[identity] = item
    return tuple(unique.values())


def _assert_domain_items_match(
    actual: Iterable[object],
    expected: Iterable[object],
    identity_field: str,
    label: str,
) -> None:
    actual_items = _unique_source_items(actual, identity_field, label)
    expected_items = _unique_source_items(expected, identity_field, label)
    actual_by_id = {getattr(item, identity_field): item for item in actual_items}
    expected_by_id = {getattr(item, identity_field): item for item in expected_items}
    if actual_by_id != expected_by_id:
        raise ValueError(f"analysis artifacts conflict with {label}")


def _quant_prediction_record_values(prediction: object) -> dict[str, object]:
    is_model = isinstance(prediction, ModelQuantPrediction)
    return {
        "quant_prediction_id": prediction.prediction_id,
        "analysis_run_id": prediction.analysis_run_id,
        "internal_match_id": prediction.match_id,
        "market_key": prediction.market.canonical,
        "market_type": prediction.market.market_type.value,
        "handicap_value": prediction.market.handicap_value,
        "manual_input_id": None if is_model else prediction.manual_input_id,
        "input_payload_hash": None if is_model else prediction.input_payload_hash,
        "quant_model_evaluation_id": (
            prediction.quant_model_evaluation_id if is_model else None
        ),
        "method": prediction.method,
        "method_version": prediction.method_version,
        "entered_at_utc": None if is_model else prediction.entered_at_utc,
        "generated_at_utc": prediction.generated_at_utc if is_model else None,
    }


def _source_manifest_payload(artifacts: AnalysisArtifacts) -> dict[str, object]:
    payload = {
        "version": artifacts.analysis_run.input_manifest_version,
        "competitions": _manifest_records(artifacts.competitions, "competition_id"),
        "teams": _manifest_records(artifacts.teams, "team_id"),
        "matches": _manifest_records(artifacts.matches, "match_id"),
        "provider_mappings": _manifest_records(
            artifacts.provider_mappings,
            "mapping_id",
        ),
        "market_odds_snapshots": _manifest_records(
            artifacts.market_odds_snapshots,
            "snapshot_id",
        ),
        "sporttery_bonus_snapshots": _manifest_records(
            artifacts.sporttery_bonus_snapshots,
            "snapshot_id",
        ),
    }
    if artifacts.analysis_run.input_manifest_version == "MVP_INPUT_MANIFEST_V3":
        payload["quant_model_states"] = _manifest_records(
            artifacts.quant_model_states,
            "quant_model_state_id",
        )
    else:
        payload["manual_quant_inputs"] = _manifest_records(
            artifacts.manual_quant_inputs,
            "input_id",
        )
    return payload


def _manifest_records(items: Iterable[object], identity_field: str) -> list[dict]:
    records = []
    for item in sorted(items, key=lambda value: getattr(value, identity_field)):
        record = item.model_dump(mode="json")
        if "quotes" in record:
            record["quotes"] = sorted(
                record["quotes"],
                key=lambda quote: quote["selection"],
            )
        records.append(record)
    return records


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _assert_exact_records(
    records: Iterable[object],
    expected_records: Iterable[dict[str, object]],
    key_fields: tuple[str, ...],
    label: str,
) -> None:
    expected_by_key: dict[object, dict[str, object]] = {}
    for fields in expected_records:
        key = _mapping_key(fields, key_fields)
        if key in expected_by_key:
            raise ValueError(f"supplied analysis contains duplicate {label}: {key!r}")
        expected_by_key[key] = fields

    actual_by_key: dict[object, object] = {}
    for record in records:
        key = _record_key(record, key_fields)
        if key in actual_by_key:
            raise ValueError(f"stored analysis contains duplicate {label}: {key!r}")
        actual_by_key[key] = record

    missing = set(expected_by_key) - set(actual_by_key)
    extra = set(actual_by_key) - set(expected_by_key)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(repr(key) for key in missing)))
        if extra:
            details.append("extra " + ", ".join(sorted(repr(key) for key in extra)))
        raise ValueError(f"existing AnalysisRun {label} conflict: {'; '.join(details)}")

    for key, fields in expected_by_key.items():
        _assert_record_fields(actual_by_key[key], fields, f"{label} {key!r}")


def _mapping_key(
    fields: dict[str, object],
    key_fields: tuple[str, ...],
) -> object:
    values = tuple(fields[field] for field in key_fields)
    return values[0] if len(values) == 1 else values


def _record_key(record: object, key_fields: tuple[str, ...]) -> object:
    values = tuple(getattr(record, field) for field in key_fields)
    return values[0] if len(values) == 1 else values


def _provider_kind(provider_code: str) -> str:
    if "SPORTTERY" in provider_code:
        return "SPORTTERY"
    if "ODDS" in provider_code or "MARKET" in provider_code:
        return "MARKET_ODDS"
    return "FIXTURE"


def _assert_record_fields(
    record: object, expected: dict[str, object], label: str
) -> None:
    mismatched = [
        field for field, value in expected.items() if getattr(record, field) != value
    ]
    if mismatched:
        raise ValueError(
            f"immutable {label} conflicts on fields: {', '.join(mismatched)}"
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
