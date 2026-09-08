"""Verified pinned reads and transaction-scoped analysis binding helpers.

No source/approval writer or training provider is exposed here. The production
repository may verify retained raw research evidence while checking integrity.
"""

from collections.abc import Callable
from datetime import datetime
import hashlib
from threading import Lock

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from football_system.application.environment import (
    RuntimeEnvironmentGuard,
    RuntimeProvenance,
)
from football_system.application.models import (
    AnalysisArtifacts,
    validate_production_predictions,
)
from football_system.application.ports.production_inference import (
    ProductionInferenceBindingV1,
)
from football_system.application.production_release import project_release_state
from football_system.application.quant_model import (
    freeze_elo_evaluation,
    freeze_elo_model_state,
)
from football_system.domain.analysis import AnalysisRun, ModelAnalysisMatchContext
from football_system.domain.archive import canonical_json
from football_system.domain.common import normalize_utc, utc_now
from football_system.domain.market import MarketKey, MarketType, ThreeWayProbability
from football_system.domain.match import Match, MarketOddsSnapshot
from football_system.domain.prediction import (
    FinalPrediction,
    MarketPrediction,
    ModelQuantPrediction,
    QuantModelStateArtifact,
)
from football_system.domain.production_release import (
    CurrentAuthorizationInputsV1,
    ProductionQuantModelReleaseV1,
    ProductionTargetAcceptancePlanV1,
    assert_authorization_progression,
    release_active_for_inference,
    revalidate,
)
from football_system.domain.services.elo_baseline import (
    EloPredictionRequest,
    EloThreeWayBaseline,
)
from football_system.infrastructure.database.models import Base
from football_system.infrastructure.database.production_quant_repository import (
    SqlAlchemyProductionQuantRepository,
    _verification_scope,
)
from football_system.infrastructure.files.training_evidence import strict_json_bytes


class SqlAlchemyProductionInferenceRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        production_repository: SqlAlchemyProductionQuantRepository,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = session_factory
        self._production = production_repository
        self._clock = clock
        self._last_clock = None
        self._clock_lock = Lock()

    def _now(self):
        with self._clock_lock:
            at = normalize_utc(self._clock())
            if self._last_clock is not None and at < self._last_clock:
                raise ValueError("production inference clock moved backwards")
            self._last_clock = at
            return at

    def load_release(self, release_id: str) -> ProductionQuantModelReleaseV1:
        return self._production.load_release(release_id)

    def load_target_plan(self, plan_id: str) -> ProductionTargetAcceptancePlanV1:
        return self._production.load_target_plan(plan_id)

    def authorization(
        self, release_id: str, at_utc: datetime
    ) -> CurrentAuthorizationInputsV1:
        return self._production.authorization(release_id, at_utc)

    def load_binding(self, analysis_run_id: str) -> ProductionInferenceBindingV1 | None:
        """Verified completed binding, including fresh read-operation authorization.

        None means no run exists, never an unapproved/missing-companion fallback.
        The inference service must still gate its own invocation start/completion.
        """
        with self._sessions.begin() as session:
            session.execute(text("BEGIN"))
            at = self._now()
            rows = _rows(session, "analysis_runs", analysis_run_id=analysis_run_id)
            if not rows:
                self.assert_bindings_in_session(session, analysis_run_id, None)
                return None
            run = AnalysisRun.model_validate(dict(rows[0]))
            if run.status != "COMPLETED":
                raise ValueError("production binding read requires a COMPLETED run")
            bindings = _rows(
                session,
                "quant_model_state_production_releases",
                analysis_run_id=analysis_run_id,
            )
            if len(bindings) != 1:
                raise ValueError(
                    "existing production run is missing exact companion binding"
                )
            binding = ProductionInferenceBindingV1.model_validate_json(
                bindings[0]["binding_json"]
            )
            self.assert_bindings_in_session(session, analysis_run_id, binding)
            manifest = strict_json_bytes(run.input_manifest_json.encode("utf-8"))
            states = tuple(
                QuantModelStateArtifact.model_validate(item)
                for item in manifest["quant_model_states"]
            )
            matches = tuple(Match.model_validate(item) for item in manifest["matches"])
            release, plan, state = self._verify(session, binding, run, states, matches)
            self._assert_persisted_model(
                session, binding, run, states[0], state, matches
            )
            start = self._authorize(session, binding, release, plan, at)
            completion = self._authorize(
                session, binding, release, plan, self._now(), finish=True
            )
            assert_authorization_progression(start, completion)
            return binding

    def validate_analysis_in_session(
        self,
        session: Session,
        artifacts: AnalysisArtifacts,
        *,
        current: bool = False,
        persisted: bool = False,
    ) -> None:
        """Exact admitted branch; never selects legacy source-time successor heads."""
        artifacts = revalidate(artifacts)
        binding = artifacts.production_binding
        if binding is None:
            raise ValueError("production analysis requires a companion binding")
        release, plan, state = self._verify(
            session,
            binding,
            artifacts.analysis_run,
            artifacts.quant_model_states,
            artifacts.matches,
        )
        baseline = EloThreeWayBaseline()
        matches = {item.match_id: item for item in artifacts.matches}
        if len(artifacts.quant_model_evaluations) != len(matches):
            raise ValueError(
                "production inference requires exactly one evaluation per target"
            )
        for evaluation in artifacts.quant_model_evaluations:
            match = matches[evaluation.match_id]
            expected = freeze_elo_evaluation(
                analysis_run_id=binding.analysis_run_id,
                model_state=artifacts.quant_model_states[0],
                prediction=baseline.predict_from_state(
                    EloPredictionRequest(
                        match_id=match.match_id,
                        season_id=state.season_id,
                        home_team_id=match.home_team_id,
                        away_team_id=match.away_team_id,
                        kickoff_at_utc=match.kickoff_at_utc,
                        cutoff_at_utc=state.cutoff_at_utc,
                    ),
                    state,
                ),
                market=evaluation.market,
                evaluated_at_utc=artifacts.analysis_run.started_at_utc,
            )
            if evaluation != expected or evaluation.status != "AVAILABLE":
                raise ValueError(
                    "production inference differs from exact fixed baseline evaluation"
                )
        if persisted:
            self.assert_bindings_in_session(session, binding.analysis_run_id, binding)
            self._assert_persisted_model(
                session,
                binding,
                artifacts.analysis_run,
                artifacts.quant_model_states[0],
                state,
                artifacts.matches,
            )
        if current:
            completion = self._authorize(
                session, binding, release, plan, self._now(), finish=True
            )
            if completion.actual_at_utc >= min(
                item.kickoff_at_utc for item in plan.content_payload.targets
            ):
                raise ValueError(
                    "production analysis commit must precede planned kickoff"
                )

    def save_binding_in_session(
        self, session: Session, binding: ProductionInferenceBindingV1
    ) -> None:
        """Insert both companions after model/context rows, before COMPLETED."""
        self._session(session)
        for name, row in _binding_rows(revalidate(binding)):
            session.execute(Base.metadata.tables[name].insert().values(**row))

    def assert_bindings_in_session(
        self,
        session: Session,
        analysis_run_id: str,
        binding: ProductionInferenceBindingV1 | None,
    ) -> None:
        self._session(session)
        expected = {} if binding is None else dict(_binding_rows(revalidate(binding)))
        for name in (
            "quant_model_state_production_releases",
            "analysis_run_target_acceptance_plans",
        ):
            actual = [
                dict(row)
                for row in _rows(session, name, analysis_run_id=analysis_run_id)
            ]
            if actual != ([] if binding is None else [expected[name]]):
                raise ValueError("production companion rows differ from exact binding")

    def _session(self, session):
        if (
            not session.in_transaction()
            or session.get_bind() is not self._sessions.kw["bind"]
        ):
            raise ValueError(
                "production inference requires an active same-database transaction"
            )

    def _authorize(self, session, binding, release, plan, at, *, finish=False):
        current = self._production.authorization_in_session(
            session, release.artifact_id, at
        )
        if current.actual_at_utc != at:
            raise ValueError("production authorization actual time mismatch")
        if finish:
            # The repository verifies all event rows before applying its as-of
            # filter. Require the returned snapshot to cover EVERY scoped row,
            # including recorded revocations whose effective time is still future.
            correction_ids = {
                item.artifact_id
                for item in self._production.correction_events_in_session(
                    session, release.training_manifest.content_payload.history
                )
            }
            revocation_ids = {
                row["revocation_id"]
                for row in _rows(
                    session,
                    "training_history_revocation_events",
                    approval_id=release.content_payload.approval.artifact_id,
                )
            }
            successor_ids = {
                row["successor_id"]
                for row in _rows(
                    session,
                    "training_history_successors",
                    predecessor_id=release.content_payload.approval.artifact_id,
                )
            }
            if (
                correction_ids != {item.artifact_id for item in current.corrections}
                or revocation_ids != {item.artifact_id for item in current.revocations}
                or successor_ids
                != {
                    item.content_payload.successor.artifact_id
                    for item in current.successors
                }
            ):
                raise ValueError(
                    "final inference authorization requires complete transaction event snapshots"
                )
            # All I/O is finished. Re-evaluate the complete, immutable transaction
            # snapshot at an observed final time, never a forecast or another load.
            completed_at = self._now()
            if completed_at < current.actual_at_utc:
                raise ValueError(
                    "production inference completion clock moved backwards"
                )
            current = CurrentAuthorizationInputsV1(
                actual_at_utc=completed_at,
                technical_evidence=current.technical_evidence,
                corrections=current.corrections,
                revocations=current.revocations,
                successors=current.successors,
            )
        assert_authorization_progression(binding.completion_authorization, current)
        release_active_for_inference(
            release=release,
            plan=plan,
            current=current,
            state_retention_horizon=binding.state_retention_horizon,
            audit_retention_horizon=binding.audit_retention_horizon,
        )
        return current

    def _verify(self, session, binding, run, states, matches):
        self._session(session)
        binding, run = revalidate(binding), revalidate(run)
        # Reuse parents only for this historical verification, not current gates.
        with _verification_scope(session):
            release = self._production.load_release_in_session(
                session, binding.release.artifact_id
            )
            plan = self._production.load_target_plan_in_session(
                session, binding.target_acceptance_plan.artifact_id
            )
            content, target = release.content_payload, plan.content_payload
            core = content.released_state_core
            if (
                binding.release != release.reference()
                or binding.target_acceptance_plan != plan.reference()
                or binding.released_state_core_hash != core.content_hash
                or binding.training_cutoff_at_utc != content.training_cutoff_at_utc
                or binding.training_data_hash != core.content_payload.training_data_hash
                or binding.approved_facts_hash
                != core.content_payload.approved_facts_hash
                or binding.state_retention_horizon != content.state_retention_horizon
                or binding.audit_retention_horizon != content.audit_retention_horizon
                or binding.analysis_run_id != run.analysis_run_id
                or run.code_revision != content.code_revision
                or binding.start_authorization.actual_at_utc != run.started_at_utc
                or binding.completion_authorization.actual_at_utc
                != run.completed_at_utc
                or run.as_of_at_utc != target.decision_as_of_at_utc
                or run.completed_at_utc
                >= min(item.kickoff_at_utc for item in target.targets)
            ):
                raise ValueError("production binding release/core/plan/run mismatch")
            config = strict_json_bytes(run.config_json.encode("utf-8"))
            if (
                hashlib.sha256(run.config_json.encode("utf-8")).hexdigest()
                != run.config_hash
                or hashlib.sha256(run.input_manifest_json.encode("utf-8")).hexdigest()
                != run.input_manifest_hash
            ):
                raise ValueError("production run config/manifest hash mismatch")
            request = config["request"]
            provenance = request.get("provider_runtime_provenance", {})
            if (
                config["settings"]["runtime"]["environment"] != "live"
                or request.get("model_training_use_class")
                != "APPROVED_TRAINING_HISTORY"
                or request.get("model_training_source_mode") != "SOURCE_TIME_RESEARCH"
                or request.get("decision_data_mode") != "LIVE_STRICT"
                or request.get("production_model_release_id") != release.artifact_id
                or request.get("production_target_acceptance_plan_id")
                != plan.artifact_id
                or request.get("competition_id") != target.competition_id
                or request.get("season_id") != target.production_target_season_id
                or not request.get("live_source_preparation_id")
                or set(provenance) != {"fixture", "market_odds", "sporttery"}
            ):
                raise ValueError(
                    "production analysis requires exact LIVE_STRICT config/provenance"
                )
            RuntimeEnvironmentGuard("live").validate(
                RuntimeProvenance.model_validate(item) for item in provenance.values()
            )
            if tuple(
                (
                    item.match_id,
                    item.home_team_id,
                    item.away_team_id,
                    item.kickoff_at_utc,
                )
                for item in sorted(matches, key=lambda item: item.match_id)
            ) != tuple(
                (
                    item.match_id,
                    item.home_team_id,
                    item.away_team_id,
                    item.kickoff_at_utc,
                )
                for item in target.targets
            ) or any(item.competition_id != target.competition_id for item in matches):
                raise ValueError("production analysis targets differ from exact plan")
            state = project_release_state(
                release,
                run.as_of_at_utc,
                tuple(item.match_id for item in target.targets),
                target.production_target_season_id,
            )
            expected = freeze_elo_model_state(
                analysis_run_id=run.analysis_run_id,
                baseline=EloThreeWayBaseline(),
                state=state,
                generated_at_utc=run.started_at_utc,
            )
            if (
                states != (expected,)
                or binding.quant_model_state_id != expected.quant_model_state_id
            ):
                raise ValueError(
                    "production model state/facts differ from exact released core"
                )
            # Complete actual correction/revocation sets, not source-time head selection.
            for captured in (
                binding.start_authorization,
                binding.completion_authorization,
            ):
                actual = self._production.authorization_in_session(
                    session, release.artifact_id, captured.actual_at_utc
                )
                if actual != captured:
                    raise ValueError(
                        "captured inference authorization differs from durable evidence"
                    )
                release_active_for_inference(
                    release=release,
                    plan=plan,
                    current=actual,
                    state_retention_horizon=binding.state_retention_horizon,
                    audit_retention_horizon=binding.audit_retention_horizon,
                )
            return release, plan, state

    @staticmethod
    def _assert_persisted_model(session, binding, run, model_state, state, matches):
        expected = model_state.model_dump(mode="python", exclude={"training_facts"})
        expected["training_fact_count"] = len(model_state.training_facts)
        rows = _rows(session, "quant_model_states", analysis_run_id=run.analysis_run_id)
        if [dict(row) for row in rows] != [expected]:
            raise ValueError("persisted production model state mismatch")
        facts = _rows(
            session,
            "quant_model_training_facts",
            quant_model_state_id=binding.quant_model_state_id,
        )
        expected_facts = [
            dict(
                quant_model_state_id=binding.quant_model_state_id,
                fact_sequence=fact.sequence,
                match_result_id=fact.match_result_id,
                internal_match_id=fact.match_id,
                source_payload_hash=fact.source_payload_hash,
                fact_hash=fact.fact_hash,
            )
            for fact in model_state.training_facts
        ]
        if (
            sorted((dict(row) for row in facts), key=lambda item: item["fact_sequence"])
            != expected_facts
        ):
            raise ValueError("persisted production training fact/hash mismatch")
        evaluations = _rows(
            session, "quant_model_evaluations", analysis_run_id=run.analysis_run_id
        )
        by_match = {item.match_id: item for item in matches}
        if len(evaluations) != len(matches) or {
            row["internal_match_id"] for row in evaluations
        } != set(by_match):
            raise ValueError("persisted production target evaluation mismatch")
        expected_evaluations = []
        for row in evaluations:
            match = by_match[row["internal_match_id"]]
            market = MarketKey(
                market_type=MarketType(row["market_type"]),
                handicap_value=row["handicap_value"],
            )
            evaluation = freeze_elo_evaluation(
                analysis_run_id=run.analysis_run_id,
                model_state=model_state,
                prediction=EloThreeWayBaseline().predict_from_state(
                    EloPredictionRequest(
                        match_id=match.match_id,
                        season_id=state.season_id,
                        home_team_id=match.home_team_id,
                        away_team_id=match.away_team_id,
                        kickoff_at_utc=match.kickoff_at_utc,
                        cutoff_at_utc=state.cutoff_at_utc,
                    ),
                    state,
                ),
                market=market,
                evaluated_at_utc=run.started_at_utc,
            )
            expected = evaluation.model_dump(
                mode="python", exclude={"market", "probabilities", "match_id"}
            )
            expected.update(
                internal_match_id=match.match_id,
                market_key=market.canonical,
                market_type=market.market_type.value,
                handicap_value=market.handicap_value,
            )
            if dict(row) != expected or evaluation.status != "AVAILABLE":
                raise ValueError(
                    "persisted production inference differs from released state"
                )
            expected_evaluations.append(evaluation)

        def probabilities(table, key, identity):
            rows = _rows(session, table, **{key: identity})
            if {row["selection_key"] for row in rows} != {
                "HOME_WIN",
                "DRAW",
                "AWAY_WIN",
            }:
                raise ValueError(
                    "persisted production prediction requires exact three-way outcomes"
                )
            return ThreeWayProbability(
                **{row["selection_key"].lower(): row["probability"] for row in rows}
            )

        contexts = tuple(
            ModelAnalysisMatchContext(
                analysis_run_id=row["analysis_run_id"],
                match_id=row["internal_match_id"],
                fixture_observation_id=strict_json_bytes(
                    row["context_json"].encode()
                ).get("fixture_observation_id"),
                market_odds_snapshot_id=row["market_odds_snapshot_id"],
                sporttery_bonus_snapshot_id=row["sporttery_bonus_snapshot_id"],
                quant_model_evaluation_id=row["quant_model_evaluation_id"],
                context_json=row["context_json"],
                context_hash=row["context_hash"],
            )
            for row in _rows(
                session, "analysis_run_matches", analysis_run_id=run.analysis_run_id
            )
        )
        market_predictions = tuple(
            MarketPrediction(
                prediction_id=row["market_probability_id"],
                analysis_run_id=row["analysis_run_id"],
                match_id=row["internal_match_id"],
                market=MarketKey(
                    market_type=row["market_type"], handicap_value=row["handicap_value"]
                ),
                probabilities=probabilities(
                    "market_probability_outcomes",
                    "market_probability_id",
                    row["market_probability_id"],
                ),
                input_snapshot_ids=tuple(
                    item["market_odds_snapshot_id"]
                    for item in _rows(
                        session,
                        "market_probability_inputs",
                        market_probability_id=row["market_probability_id"],
                    )
                ),
                devig_method=row["devig_method"],
                devig_version=row["devig_version"],
                overround=row["overround"],
                generated_at_utc=row["generated_at_utc"],
            )
            for row in _rows(
                session, "market_probabilities", analysis_run_id=run.analysis_run_id
            )
        )
        quant_predictions = tuple(
            ModelQuantPrediction(
                prediction_id=row["quant_prediction_id"],
                analysis_run_id=row["analysis_run_id"],
                match_id=row["internal_match_id"],
                market=MarketKey(
                    market_type=row["market_type"], handicap_value=row["handicap_value"]
                ),
                probabilities=probabilities(
                    "quant_prediction_outcomes",
                    "quant_prediction_id",
                    row["quant_prediction_id"],
                ),
                quant_model_evaluation_id=row["quant_model_evaluation_id"],
                method=row["method"],
                method_version=row["method_version"],
                generated_at_utc=row["generated_at_utc"],
            )
            for row in _rows(
                session, "quant_predictions", analysis_run_id=run.analysis_run_id
            )
        )
        final_predictions = tuple(
            FinalPrediction(
                prediction_id=row["final_prediction_id"],
                analysis_run_id=row["analysis_run_id"],
                match_id=row["internal_match_id"],
                market=MarketKey(
                    market_type=row["market_type"], handicap_value=row["handicap_value"]
                ),
                probabilities=probabilities(
                    "final_prediction_outcomes",
                    "final_prediction_id",
                    row["final_prediction_id"],
                ),
                market_prediction_id=row["market_probability_id"],
                quant_prediction_id=row["quant_prediction_id"],
                llm_assessment_id=row["llm_assessment_id"],
                fusion_policy=row["fusion_policy"],
                fusion_version=row["fusion_version"],
                fusion_config_json=row["fusion_config_json"],
                fallback_code=row["fallback_code"],
                confidence=row["confidence"],
                generated_at_utc=row["generated_at_utc"],
            )
            for row in _rows(
                session, "final_predictions", analysis_run_id=run.analysis_run_id
            )
        )
        manifest = strict_json_bytes(run.input_manifest_json.encode())
        validate_production_predictions(
            run=run,
            contexts=contexts,
            model_states=(model_state,),
            evaluations=tuple(expected_evaluations),
            market_snapshots=tuple(
                MarketOddsSnapshot.model_validate(item)
                for item in manifest["market_odds_snapshots"]
            ),
            market_predictions=market_predictions,
            quant_predictions=quant_predictions,
            final_predictions=final_predictions,
        )


def _rows(session, name, **filters):
    table = Base.metadata.tables[name]
    return (
        session.execute(
            select(table).where(
                *(table.c[key] == value for key, value in filters.items())
            )
        )
        .mappings()
        .all()
    )


def _binding_rows(binding):
    keys = dict(
        analysis_run_id=binding.analysis_run_id,
        quant_model_state_id=binding.quant_model_state_id,
        release_id=binding.release.artifact_id,
        plan_id=binding.target_acceptance_plan.artifact_id,
    )
    yield (
        "quant_model_state_production_releases",
        dict(
            **keys,
            released_state_core_hash=binding.released_state_core_hash,
            training_cutoff_at_utc=binding.training_cutoff_at_utc,
            training_data_hash=binding.training_data_hash,
            approved_facts_hash=binding.approved_facts_hash,
            binding_json=canonical_json(binding),
            binding_hash=binding.binding_hash,
        ),
    )
    yield "analysis_run_target_acceptance_plans", keys
