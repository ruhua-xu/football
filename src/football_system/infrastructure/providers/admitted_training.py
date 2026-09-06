"""Pinned, offline admitted history. This provider grants no production rights."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol

from football_system.domain.archive import HistoricalDataMode
from football_system.domain.common import normalize_utc
from football_system.domain.production_release import EloTrainingWindowV1
from football_system.domain.quant_integrity import (
    FIXED_ELO_CONFIG_HASH,
    QuantIntegrityScopeV1,
    TrainingAdmissionPinV1,
    project_admitted_training_fact,
    revalidate_integrity_model,
    select_admitted_training_facts,
)
from football_system.domain.services.elo_baseline import EloRegularTimeResult
from football_system.domain.training_admission import (
    TRAINING_FACT_REQUIRED_USES,
    TrainingFactAdmissionV1,
    TrainingFactBindingV1,
)


class AdmittedTrainingRepository(Protocol):
    def load_verified_training_admission(
        self,
        pin: TrainingAdmissionPinV1,
        *,
        at_utc: datetime,
    ) -> TrainingFactAdmissionV1:
        """Load this exact persisted graph, never 'latest' or bare MatchResult rows.

        Verify stored raw archive/record bytes and hashes, rights/reviewer evidence,
        identity, provider scope, normalized rows and child/parent roots. Reject
        stale/corrected graphs when a correction is both source-visible and locally
        registered by at_utc. Enforce active research/storage rights at at_utc.
        Unknown evidence/timestamps/heads fail closed. No acquisition or live I/O.
        """
        ...


@dataclass(frozen=True, slots=True)
class AdmittedTrainingHistoryEloProvider:
    """Not an AnalysisRun provider and deliberately not the live provider Protocol.

    Caller supplies exact admissions and an actual verification time. The
    repository must prove persistence/raw evidence; typed objects alone cannot.
    No APPROVED_TRAINING_HISTORY assertion is made before independent grants.
    """

    repository: AdmittedTrainingRepository
    admissions: tuple[TrainingAdmissionPinV1, ...]
    training_window: EloTrainingWindowV1
    scope: QuantIntegrityScopeV1
    verified_at_utc: datetime

    data_mode: ClassVar[HistoricalDataMode] = HistoricalDataMode.SOURCE_TIME_RESEARCH
    retrospective: ClassVar[bool] = True
    offline_only: ClassVar[bool] = True
    report_data_mode: ClassVar[str] = "RETROSPECTIVE_SOURCE_TIME_RESEARCH"
    training_use_class: ClassVar[str] = "ADMITTED_INTERNAL_RESEARCH_ONLY"
    config_hash: ClassVar[str] = FIXED_ELO_CONFIG_HASH

    def load_facts(self) -> tuple[TrainingFactBindingV1, ...]:
        at = normalize_utc(self.verified_at_utc)
        window = revalidate_integrity_model(self.training_window).content_payload
        scope = revalidate_integrity_model(self.scope)
        pins = tuple(revalidate_integrity_model(p) for p in self.admissions)
        ids = tuple(p.training_fact_admission_id for p in pins)
        if not pins or len(ids) != len(set(ids)) or ids != tuple(sorted(ids)):
            raise ValueError("provider requires sorted unique pinned admissions")
        if (
            scope.competition_id != window.competition_id
            or tuple(s.canonical_season_id for s in scope.provider_seasons)
            != window.ordered_season_ids
            or scope.reviewed_at_utc > at
        ):
            raise ValueError(
                "provider requires explicit reviewed competition/season scope"
            )
        admitted: list[TrainingFactBindingV1] = []
        scopes = {s.canonical_season_id: s for s in scope.provider_seasons}
        for season in window.seasons:
            reviewed = scopes[season.season_id]
            providers = tuple(
                (
                    p.source_id,
                    p.provider_code,
                    p.provider_competition_id,
                    p.provider_season_id,
                )
                for p in season.provider_seasons
            )
            if providers != (
                (
                    reviewed.source_id,
                    reviewed.provider_code,
                    reviewed.provider_competition_id,
                    reviewed.provider_season_id,
                ),
            ):
                raise ValueError(
                    "training window does not pin the exact reviewed provider scope"
                )
        for pin in pins:
            if pin.persisted_at_utc > at:
                raise ValueError("admission was not yet locally persisted")
            admission = revalidate_integrity_model(
                self.repository.load_verified_training_admission(pin, at_utc=at)
            )
            if TrainingAdmissionPinV1.from_admission(admission) != pin:
                raise ValueError(
                    "repository admission does not match exact pinned metadata"
                )
            admission.source_rights_admission.assert_active_for(
                at, TRAINING_FACT_REQUIRED_USES
            )
            for fact in admission.facts:
                binding = fact.content_payload
                membership = binding.season_membership.content_payload
                expected = scopes.get(membership.canonical_season_id)
                if (
                    expected is None
                    or membership.canonical_competition_id != scope.competition_id
                    or binding.canonical_identity.competition_type
                    != scope.competition_type
                    or (
                        membership.source_id,
                        membership.provider_code,
                        membership.provider_competition_id,
                        membership.provider_season_id,
                    )
                    != (
                        expected.source_id,
                        expected.provider_code,
                        expected.provider_competition_id,
                        expected.provider_season_id,
                    )
                ):
                    raise ValueError(
                        "fact is outside the exact reviewed provider scope"
                    )
                admitted.append(fact)
        ordered = select_admitted_training_facts(
            admitted,
            window.competition_id,
            window.ordered_season_ids,
            at,
            (),
            strict_cutoff=False,
        )
        if len(ordered) != len(admitted):
            raise ValueError(
                "admitted source facts cannot follow actual verification time"
            )
        return ordered

    def fetch_elo_results(
        self,
        *,
        cutoff_at_utc: datetime,
        target_season_id: str,
        exclude_match_ids: Iterable[str],
        strict_cutoff: bool = True,
    ) -> tuple[EloRegularTimeResult, ...]:
        window = revalidate_integrity_model(self.training_window).content_payload
        if target_season_id not in window.ordered_season_ids:
            raise ValueError("target season is outside the pinned training window")
        cutoff = normalize_utc(cutoff_at_utc)
        if cutoff > normalize_utc(self.verified_at_utc):
            raise ValueError("knowledge cutoff cannot follow actual verification")
        excluded = set(exclude_match_ids)
        facts = select_admitted_training_facts(
            self.load_facts(),
            window.competition_id,
            window.ordered_season_ids,
            cutoff,
            excluded,
            strict_cutoff=strict_cutoff,
        )
        if any(
            window.ordered_season_ids.index(f.content_payload.canonical_identity.season)
            > window.ordered_season_ids.index(target_season_id)
            for f in facts
        ):
            raise ValueError("training season cannot follow requested target season")
        return tuple(project_admitted_training_fact(fact) for fact in facts)
