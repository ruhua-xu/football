"""Probability-only ADR-0008 orchestration through an injectable repository port."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Protocol

from football_system.domain.backtest import RATIO_QUANTUM, BacktestMetricsConfig
from football_system.domain.common import normalize_utc
from football_system.domain.market import SelectionKey
from football_system.domain.quant_integrity import (
    FIXED_ELO_CONFIG_HASH,
    AdmittedFactRefV1,
    IntegrityArtifactRefV1,
    QuantIntegrityAttemptContentV1,
    QuantIntegrityAttemptReservationV1,
    QuantIntegrityAttemptV1,
    QuantIntegrityAttestationContentV1,
    QuantIntegrityAttestationV1,
    QuantIntegrityFailureV1,
    QuantIntegrityInputRootsV1,
    QuantIntegrityOutputContentV1,
    QuantIntegrityOutputV1,
    QuantIntegrityPlanContentV1,
    QuantIntegrityPlanDefinitionV1,
    QuantIntegrityPlanV1,
    QuantIntegrityReplayV1,
    QuantIntegrityReportContentV1,
    QuantIntegrityReportV1,
    QuantIntegritySliceOutputV1,
    QuantIntegritySummaryContentV1,
    QuantIntegritySummaryV1,
    QuantIntegrityTargetOutputV1,
    QuantUnavailableReasonV1,
    QuantUnavailableSliceV1,
    TerminalEloStateCoreContentV1,
    TerminalEloStateCoreV1,
    admitted_selection_root,
    integrity_attempt_root,
    project_admitted_training_fact,
    revalidate_integrity_model,
    select_admitted_training_facts,
)
from football_system.domain.services.backtest_metrics import (
    calculate_probability_metrics,
)
from football_system.domain.services.elo_baseline import (
    EloPredictionRequest,
    EloPredictionStatus,
    EloThreeWayBaseline,
)
from football_system.infrastructure.providers.admitted_training import (
    AdmittedTrainingHistoryEloProvider,
    AdmittedTrainingRepository,
)


class QuantIntegrityPilotRepository(AdmittedTrainingRepository, Protocol):
    """Required append-only transactions; these pure classes implement NO storage.

    A durable adapter must enforce exact retries, stable IDs, no overwrite/delete,
    one outstanding reservation per series, and no attempts after terminal seal.
    Scope continuity must not be bypassed by changing cohort/cutoffs/provider or
    by silently opening another series. New post-terminal series require explicit
    lineage/disclosure. Reservations survive crashes and must be completed FAILED
    by adapter recovery, never removed from sequence/root accounting.
    """

    def verify_plan_metadata(
        self,
        definition: QuantIntegrityPlanDefinitionV1,
        *,
        at_utc: datetime,
    ) -> None:
        """Verify pre-existing metadata ONLY; do not fetch target scores/results.

        Prove exact rights/admission/archive pins, raw-reviewed Bundesliga/provider
        season scope, actual admission times, full schedule and every exception.
        Resolve every target's fixture/mapping and opaque admitted binding ref from
        metadata projections. Check active rights and correction heads. Synthetic
        evidence is test-only and must NEVER be represented as a real pilot.
        """
        ...

    def seal_plan(self, plan: QuantIntegrityPlanV1) -> None:
        """Atomically recheck metadata prerequisites and append plan before result access."""
        ...

    def load_plan(self, plan_ref: IntegrityArtifactRefV1) -> QuantIntegrityPlanV1: ...

    def reserve_attempt(
        self,
        plan_ref: IntegrityArtifactRefV1,
        *,
        actual_started_at_utc: datetime,
    ) -> QuantIntegrityAttemptReservationV1:
        """Atomically reserve next continuous sequence with full prior count/root.

        Require a sealed plan, actual start >= seal, no pending reservation, no
        terminal attestation, and same scope for this series. Persist reservation
        before returning, before ANY fact/result read or computation.
        """
        ...

    def complete_attempt(
        self,
        attempt: QuantIntegrityAttemptV1,
        *,
        output: QuantIntegrityOutputV1 | None,
        report: QuantIntegrityReportV1 | None,
    ) -> None:
        """Atomically append completion and exact output/report, or retained failure.

        Verify reservation ownership, hashes, plan/cohort/output/report bindings,
        monotone actual times and unchanged prior root. Never replace a completion.
        Exact retry is idempotent; uncertain commits must be resolved by the adapter.
        """
        ...

    def list_attempts(
        self, integrity_pilot_series_id: str
    ) -> tuple[QuantIntegrityAttemptV1, ...]:
        """Return ALL completed attempts in sequence, including failures/unfavorable runs."""
        ...

    def load_report(
        self, report_ref: IntegrityArtifactRefV1
    ) -> QuantIntegrityReportV1: ...

    def seal_terminal_attestation(
        self,
        summary: QuantIntegritySummaryV1,
        attestation: QuantIntegrityAttestationV1,
    ) -> None:
        """Atomically verify current complete attempt list/root and last success/report.

        Reject outstanding reservations or later attempts; persist both artifacts
        and permanently close the series to attempts in the same transaction.
        Verify all plan/report/source/core/recipe/revision bindings. No approval is
        granted. A synthetic terminal attestation is never production evidence.
        """
        ...


class QuantIntegrityPilotService:
    def __init__(
        self,
        repository: QuantIntegrityPilotRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self.repository = repository
        self.clock = clock

    def seal_plan(
        self, definition: QuantIntegrityPlanDefinitionV1
    ) -> QuantIntegrityPlanV1:
        definition = revalidate_integrity_model(definition)
        at = normalize_utc(self.clock())
        plan = QuantIntegrityPlanV1.freeze(
            content_payload=QuantIntegrityPlanContentV1(
                definition=definition,
                input_roots=QuantIntegrityInputRootsV1.of(definition),
                sealed_at_utc=at,
            )
        )
        self.repository.verify_plan_metadata(definition, at_utc=at)
        self.repository.seal_plan(plan)
        return plan

    def run(self, plan_ref: IntegrityArtifactRefV1) -> QuantIntegrityReportV1:
        plan_ref = revalidate_integrity_model(plan_ref)
        plan = revalidate_integrity_model(self.repository.load_plan(plan_ref))
        if IntegrityArtifactRefV1.of(plan) != plan_ref:
            raise ValueError("repository returned a different plan")
        definition = plan.content_payload.definition
        started = normalize_utc(self.clock())
        if started < plan.content_payload.sealed_at_utc:
            raise ValueError("actual start cannot precede sealed plan")
        reservation = revalidate_integrity_model(
            self.repository.reserve_attempt(
                plan_ref,
                actual_started_at_utc=started,
            )
        )
        try:
            reserved = reservation.content_payload
            requested_start = started
            started = reserved.actual_started_at_utc
            previous = tuple(
                revalidate_integrity_model(a)
                for a in self.repository.list_attempts(
                    definition.integrity_pilot_series_id
                )
            )
            if (
                reserved.plan_ref != plan_ref
                or reserved.integrity_pilot_series_id
                != definition.integrity_pilot_series_id
                or reserved.scope_hash != definition.scope_hash
                or started < requested_start
                or reserved.prior_attempt_count != len(previous)
                or reserved.prior_attempt_root != integrity_attempt_root(previous)
            ):
                raise ValueError("attempt reservation/prior history mismatch")
            if previous:
                QuantIntegritySummaryContentV1(
                    integrity_pilot_series_id=definition.integrity_pilot_series_id,
                    scope_hash=definition.scope_hash,
                    attempts=previous,
                    attempt_count=len(previous),
                    attempt_root=integrity_attempt_root(previous),
                    generated_at_utc=started,
                )
            self.repository.verify_plan_metadata(definition, at_utc=started)
            output = self._execute(plan, at_utc=started)
            replay = self._execute(plan, at_utc=started)
            report = self._report(plan, output, replay)
            completed = normalize_utc(self.clock())
            if completed < started:
                raise ValueError("actual clock moved backwards")
            # Rights or locally registered corrections can invalidate work mid-attempt.
            self._provider(plan, at_utc=completed).load_facts()
            self.repository.verify_plan_metadata(definition, at_utc=completed)
            attempt = QuantIntegrityAttemptV1.freeze(
                content_payload=QuantIntegrityAttemptContentV1(
                    reservation=reservation,
                    actual_completed_at_utc=completed,
                    status="COMPLETED",
                    output_ref=IntegrityArtifactRefV1.of(output),
                    report_ref=IntegrityArtifactRefV1.of(report),
                )
            )
            self.repository.complete_attempt(attempt, output=output, report=report)
            return report
        except Exception as exc:
            try:
                failed = QuantIntegrityAttemptV1.freeze(
                    content_payload=QuantIntegrityAttemptContentV1(
                        reservation=reservation,
                        actual_completed_at_utc=normalize_utc(self.clock()),
                        status="FAILED",
                        failure=QuantIntegrityFailureV1(
                            exception_type=type(exc).__name__,
                            # Do not persist arbitrary exception text containing source bytes/secrets.
                            reason="INTEGRITY_EXECUTION_FAILED; inspect controlled local error context",
                        ),
                    )
                )
                self.repository.complete_attempt(failed, output=None, report=None)
            except Exception as retention_error:
                raise ExceptionGroup(
                    "pilot failed and failure retention could not be confirmed; reservation requires recovery",
                    [exc, retention_error],
                ) from exc
            raise

    def seal_terminal_attestation(
        self,
        plan_ref: IntegrityArtifactRefV1,
    ) -> QuantIntegrityAttestationV1:
        plan_ref = revalidate_integrity_model(plan_ref)
        plan = revalidate_integrity_model(self.repository.load_plan(plan_ref))
        if IntegrityArtifactRefV1.of(plan) != plan_ref:
            raise ValueError("terminal plan reference mismatch")
        definition = plan.content_payload.definition
        at = normalize_utc(self.clock())
        attempts = tuple(
            revalidate_integrity_model(a)
            for a in self.repository.list_attempts(definition.integrity_pilot_series_id)
        )
        summary = QuantIntegritySummaryV1.freeze(
            content_payload=QuantIntegritySummaryContentV1(
                integrity_pilot_series_id=definition.integrity_pilot_series_id,
                scope_hash=definition.scope_hash,
                attempts=attempts,
                attempt_count=len(attempts),
                attempt_root=integrity_attempt_root(attempts),
                generated_at_utc=at,
            )
        )
        last = attempts[-1].content_payload
        if (
            last.status != "COMPLETED"
            or last.report_ref is None
            or last.reservation.content_payload.plan_ref != plan_ref
        ):
            raise ValueError(
                "terminal attestation requires the last attempt's successful plan/report"
            )
        report = revalidate_integrity_model(
            self.repository.load_report(last.report_ref)
        )
        content = report.content_payload
        if (
            IntegrityArtifactRefV1.of(report) != last.report_ref
            or content.output_ref != last.output_ref
            or content.plan_ref != plan_ref
            or content.input_roots != plan.content_payload.input_roots
            or content.provenance != definition.provenance
            or content.cohort_match_ids != definition.cohort.cohort_match_ids
            or content.metric_definition != definition.metric_definition
            or content.build_recipe != definition.build_recipe
            or content.implementation_code_revision
            != definition.implementation_code_revision
        ):
            raise ValueError("terminal report does not bind the exact plan")
        attestation = QuantIntegrityAttestationV1.freeze(
            content_payload=QuantIntegrityAttestationContentV1(
                integrity_pilot_series_id=definition.integrity_pilot_series_id,
                scope_hash=definition.scope_hash,
                plan_ref=plan_ref,
                summary_ref=IntegrityArtifactRefV1.of(summary),
                report_ref=last.report_ref,
                attempt_count=len(attempts),
                attempt_root=summary.content_payload.attempt_root,
                input_roots=plan.content_payload.input_roots,
                terminal_state_core_ref=content.terminal_state_core_ref,
                build_recipe=definition.build_recipe,
                implementation_code_revision=definition.implementation_code_revision,
                provenance=definition.provenance,
                attested_at_utc=at,
            )
        )
        self.repository.seal_terminal_attestation(summary, attestation)
        return attestation

    def _provider(self, plan: QuantIntegrityPlanV1, *, at_utc: datetime):
        definition = plan.content_payload.definition
        return AdmittedTrainingHistoryEloProvider(
            repository=self.repository,
            admissions=definition.admissions,
            training_window=definition.training_window,
            scope=definition.scope,
            verified_at_utc=at_utc,
        )

    def _execute(
        self, plan: QuantIntegrityPlanV1, *, at_utc: datetime
    ) -> QuantIntegrityOutputV1:
        definition = plan.content_payload.definition
        window = definition.training_window.content_payload
        facts = self._provider(plan, at_utc=at_utc).load_facts()
        by_match = {f.content_payload.normalized_result.match_id: f for f in facts}
        baseline = EloThreeWayBaseline()
        if baseline.config_hash != FIXED_ELO_CONFIG_HASH:
            raise ValueError("fixed Elo baseline config has drifted")
        # Resolve EVERY result, including MODEL_UNAVAILABLE targets, before scoring.
        # A missing/late result is an attempt failure, never a denominator reduction.
        for item in definition.slices:
            slice_definition = item.content_payload
            for target in slice_definition.targets:
                fact = by_match.get(target.fact.match_id)
                if fact is None or AdmittedFactRefV1.of(fact) != target.fact:
                    raise ValueError(
                        "all plan targets require exact resolvable admitted results"
                    )
                binding = fact.content_payload
                if (
                    binding.canonical_identity != target.identity
                    or binding.fixture_source.source_available_at_utc
                    != target.fixture_source_available_at_utc
                    or binding.season_membership.content_payload.source_available_at_utc
                    != target.mapping_source_available_at_utc
                ):
                    raise ValueError("target metadata disagrees with admitted source")
                result = binding.normalized_result
                if not (
                    target.identity.kickoff_at_utc
                    < result.available_at_utc
                    <= slice_definition.evaluation_as_of_at_utc
                    <= at_utc
                ):
                    raise ValueError(
                        "target result is unavailable at evaluation cutoff"
                    )
        slices = []
        for item in definition.slices:
            selected = item.content_payload
            training = select_admitted_training_facts(
                facts,
                window.competition_id,
                window.ordered_season_ids,
                selected.decision_as_of_at_utc,
                selected.exclude_match_ids,
            )
            if any(
                window.ordered_season_ids.index(
                    f.content_payload.canonical_identity.season
                )
                > window.ordered_season_ids.index(window.pilot_target_season_id)
                for f in training
            ):
                raise ValueError(
                    "pilot training cannot contain a later production season"
                )
            state = baseline.rebuild_state(
                tuple(project_admitted_training_fact(f) for f in training),
                selected.decision_as_of_at_utc,
                target_season_id=window.pilot_target_season_id,
                exclude_match_ids=selected.exclude_match_ids,
            )
            predictions = []
            for target in selected.targets:
                identity = target.identity
                prediction = baseline.predict_from_state(
                    EloPredictionRequest(
                        match_id=target.fact.match_id,
                        season_id=identity.season,
                        home_team_id=identity.internal_home_team_id,
                        away_team_id=identity.internal_away_team_id,
                        kickoff_at_utc=identity.kickoff_at_utc,
                        cutoff_at_utc=selected.decision_as_of_at_utc,
                    ),
                    state,
                )
                result = by_match[
                    target.fact.match_id
                ].content_payload.normalized_result
                outcome = (
                    SelectionKey.HOME_WIN
                    if result.home_goals > result.away_goals
                    else SelectionKey.AWAY_WIN
                    if result.home_goals < result.away_goals
                    else SelectionKey.DRAW
                )
                predictions.append(
                    QuantIntegrityTargetOutputV1(
                        result_fact=target.fact,
                        outcome=outcome,
                        prediction=prediction,
                    )
                )
            slices.append(
                QuantIntegritySliceOutputV1(
                    slice_ref=IntegrityArtifactRefV1.of(item),
                    state=state,
                    admitted_training_refs=tuple(
                        AdmittedFactRefV1.of(f) for f in training
                    ),
                    targets=tuple(predictions),
                )
            )
        terminal_definition = definition.terminal_projection
        terminal_facts = select_admitted_training_facts(
            facts,
            window.competition_id,
            window.ordered_season_ids,
            terminal_definition.training_cutoff_at_utc,
            terminal_definition.exclude_match_ids,
            strict_cutoff=False,
        )
        state = baseline.rebuild_state(
            tuple(project_admitted_training_fact(f) for f in terminal_facts),
            terminal_definition.training_cutoff_at_utc,
            target_season_id=window.production_target_season_id,
            exclude_match_ids=terminal_definition.exclude_match_ids,
        )
        refs = tuple(AdmittedFactRefV1.of(f) for f in terminal_facts)
        core = TerminalEloStateCoreV1.freeze(
            content_payload=TerminalEloStateCoreContentV1(
                training_cutoff_at_utc=terminal_definition.training_cutoff_at_utc,
                production_target_season_id=window.production_target_season_id,
                training_window_hash=definition.training_window.content_hash,
                teams=state.teams,
                training_facts=state.training_facts,
                training_data_hash=state.training_data_hash,
                admitted_fact_refs=refs,
                admitted_facts_root=admitted_selection_root(refs),
            )
        )
        return QuantIntegrityOutputV1.freeze(
            content_payload=QuantIntegrityOutputContentV1(
                plan_ref=IntegrityArtifactRefV1.of(plan),
                provenance=definition.provenance,
                slices=tuple(slices),
                terminal_state_core=core,
            )
        )

    def _report(
        self,
        plan: QuantIntegrityPlanV1,
        output: QuantIntegrityOutputV1,
        replay_output: QuantIntegrityOutputV1,
    ) -> QuantIntegrityReportV1:
        definition = plan.content_payload.definition
        replay = QuantIntegrityReplayV1(
            output_hash=output.content_hash,
            replay_output_hash=replay_output.content_hash,
            state_hashes=tuple(
                s.state.state_hash for s in output.content_payload.slices
            ),
            replay_state_hashes=tuple(
                s.state.state_hash for s in replay_output.content_payload.slices
            ),
            terminal_core_hash=output.content_payload.terminal_state_core.content_hash,
            replay_terminal_core_hash=replay_output.content_payload.terminal_state_core.content_hash,
        )
        available = []
        observations = []
        unavailable = []
        for item in output.content_payload.slices:
            missing = []
            for target in item.targets:
                if target.prediction.status == EloPredictionStatus.AVAILABLE:
                    available.append(target.prediction.match_id)
                    observations.append(
                        (target.outcome, target.prediction.probabilities)
                    )
                else:
                    missing.append(target.prediction.match_id)
            if missing:
                unavailable.append(
                    QuantUnavailableSliceV1(
                        slice_ref=item.slice_ref,
                        match_ids=tuple(sorted(missing)),
                    )
                )
        metrics_config = BacktestMetricsConfig(
            **definition.metric_definition.model_dump(
                include={
                    "metrics_version",
                    "log_loss_clip_version",
                    "log_loss_epsilon",
                }
            )
        )
        with localcontext() as context:
            context.prec = 50
            metrics = calculate_probability_metrics(observations, metrics_config)
            rate = (
                Decimal(len(available)) / len(definition.cohort.cohort_match_ids)
            ).quantize(
                RATIO_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
        reasons = (
            ()
            if not unavailable
            else (
                QuantUnavailableReasonV1(
                    count=sum(len(s.match_ids) for s in unavailable),
                    slices=tuple(unavailable),
                ),
            )
        )
        return QuantIntegrityReportV1.freeze(
            content_payload=QuantIntegrityReportContentV1(
                plan_ref=IntegrityArtifactRefV1.of(plan),
                output_ref=IntegrityArtifactRefV1.of(output),
                provenance=definition.provenance,
                input_roots=plan.content_payload.input_roots,
                metric_definition=definition.metric_definition,
                cohort_match_ids=definition.cohort.cohort_match_ids,
                available_match_ids=tuple(sorted(available)),
                availability_count=len(available),
                availability_denominator=len(definition.cohort.cohort_match_ids),
                availability_rate=rate,
                unavailable_reasons=reasons,
                probability_metrics=metrics,
                calibration_observation_count=3 * len(available),
                replay=replay,
                terminal_state_core_ref=IntegrityArtifactRefV1.of(
                    output.content_payload.terminal_state_core
                ),
                build_recipe=definition.build_recipe,
                implementation_code_revision=definition.implementation_code_revision,
            )
        )
