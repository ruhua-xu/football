from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from football_system.application.identity_catalog import MatchIdentityCatalog
from football_system.domain.common import UtcDateTime
from football_system.domain.daily_slate import (
    DailySlateCandidate,
    DailySlateCandidatePlan,
    DailySlateCandidateStatus,
    DailySlateCaptureKind,
    DailySlateCapturePlan,
    DailySlateCaptureRequest,
    DailySlatePlan,
    DailySlateReconciliationTask,
    SportteryDailySlate,
)
from football_system.domain.identity import (
    MatchIdentityResolutionError,
    ProviderMatchIdentity,
    UnresolvedMatchMappingError,
)


SPORTTERY_PLANNING_PROVIDER_CODE = "SPORTTERY_MANUAL"
SPORTTERY_MATCH_NAMESPACE = "sporttery_match"
EXACT_SLATE_LABEL_RESOLUTION = "EXACT_SLATE_LABEL_KICKOFF"


class PlanSportteryDailySlateService:
    """Build capture work without creating canonical or analysis inputs."""

    def plan(
        self,
        slate: SportteryDailySlate,
        catalog: MatchIdentityCatalog,
        *,
        planned_at_utc: UtcDateTime,
    ) -> DailySlatePlan:
        if planned_at_utc < slate.provenance.reviewed_at_utc:
            raise ValueError("daily slate cannot be planned before source review")
        resolver = catalog.build_resolver(timedelta(0))
        candidate_plans: list[DailySlateCandidatePlan] = []
        tasks: list[DailySlateReconciliationTask] = []
        known_match_ids = {
            item.internal_match_id for item in catalog.canonical_matches
        }

        for candidate in slate.candidates:
            exact_candidates = _exact_canonical_candidates(candidate, catalog)
            try:
                resolution = resolver.resolve(_provider_identity(candidate))
            except MatchIdentityResolutionError as error:
                if (
                    isinstance(error, UnresolvedMatchMappingError)
                    and len(exact_candidates) == 1
                ):
                    resolved_match_id = exact_candidates[0]
                    resolution_method = EXACT_SLATE_LABEL_RESOLUTION
                else:
                    resolved_match_id = None
                    resolution_method = None
            else:
                resolved_match_id = resolution.internal_match_id
                resolution_method = resolution.resolution_method

            if resolved_match_id is None:
                canonical_candidates = tuple(sorted(set(exact_candidates)))
                statuses = [DailySlateCandidateStatus.IDENTITY_UNRESOLVED]
                if not canonical_candidates:
                    statuses.append(DailySlateCandidateStatus.FIXTURE_SOURCE_REQUIRED)
                if candidate.three_way_sp is not None:
                    statuses.append(DailySlateCandidateStatus.SPORTTERY_SP_READY)
                result = DailySlateCandidatePlan.freeze(
                    candidate=candidate,
                    statuses=tuple(statuses),
                    canonical_match_id=None,
                    resolution_method=None,
                    canonical_candidate_ids=canonical_candidates,
                )
                task = DailySlateReconciliationTask.freeze(
                    candidate_id=candidate.candidate_id,
                    provider_code=SPORTTERY_PLANNING_PROVIDER_CODE,
                    external_namespace=SPORTTERY_MATCH_NAMESPACE,
                    external_match_id=candidate.external_match_id,
                    canonical_candidate_ids=canonical_candidates,
                    fixture_source_required=not canonical_candidates,
                )
                tasks.append(task)
            else:
                if resolved_match_id not in known_match_ids:
                    raise ValueError(
                        "daily slate identity mapping references an unknown canonical match"
                    )
                statuses = [
                    DailySlateCandidateStatus.IDENTITY_RESOLVED,
                    DailySlateCandidateStatus.MARKET_ODDS_REQUIRED,
                ]
                if candidate.three_way_sp is not None:
                    statuses.extend(
                        (
                            DailySlateCandidateStatus.SPORTTERY_SP_READY,
                            DailySlateCandidateStatus.READY_FOR_CAPTURE,
                        )
                    )
                result = DailySlateCandidatePlan.freeze(
                    candidate=candidate,
                    statuses=tuple(statuses),
                    canonical_match_id=resolved_match_id,
                    resolution_method=resolution_method,
                    canonical_candidate_ids=(resolved_match_id,),
                )
            candidate_plans.append(result)

        ordered_plans = tuple(
            sorted(
                candidate_plans,
                key=lambda item: (
                    item.candidate.match_date,
                    item.candidate.sporttery_match_no,
                    item.candidate.candidate_id,
                ),
            )
        )
        resolved_match_ids = tuple(
            item.canonical_match_id
            for item in ordered_plans
            if item.canonical_match_id is not None
        )
        if len(resolved_match_ids) != len(set(resolved_match_ids)):
            raise ValueError("daily slate candidates resolve to a duplicate canonical match")

        ordered_tasks = tuple(sorted(tasks, key=lambda item: item.task_id))
        capture_plan = _capture_plan(ordered_plans, ordered_tasks)
        return DailySlatePlan.freeze(
            schema_version="DAILY_SLATE_PLAN_V1",
            status=slate.status,
            analysis_status="NO_ANALYSIS",
            planned_at_utc=planned_at_utc,
            source_slate_id=slate.slate_id,
            source_slate_hash=slate.slate_hash,
            candidates=ordered_plans,
            reconciliation_tasks=ordered_tasks,
            capture_plan=capture_plan,
        )


def _provider_identity(candidate: DailySlateCandidate) -> ProviderMatchIdentity:
    return ProviderMatchIdentity(
        provider_code=SPORTTERY_PLANNING_PROVIDER_CODE,
        provider_match_id=candidate.external_match_id,
        external_namespace=SPORTTERY_MATCH_NAMESPACE,
        provider_competition_id=candidate.competition_label,
        provider_competition_name=candidate.competition_label,
        competition_language="und",
        season="DAILY_SLATE_PLANNING",
        competition_type="DAILY_SLATE_PLANNING",
        home_team_id=candidate.home_label,
        home_team_name=candidate.home_label,
        home_team_language="und",
        away_team_id=candidate.away_label,
        away_team_name=candidate.away_label,
        away_team_language="und",
        kickoff_at_utc=candidate.kickoff_at_utc,
    )


def _exact_canonical_candidates(
    candidate: DailySlateCandidate,
    catalog: MatchIdentityCatalog,
) -> tuple[str, ...]:
    team_labels: defaultdict[str, set[str]] = defaultdict(set)
    for identity in catalog.team_identities:
        team_labels[identity.internal_team_id].add(identity.canonical_name)
        team_labels[identity.internal_team_id].update(
            alias.provider_team_name for alias in identity.aliases
        )
    competition_labels: defaultdict[str, set[str]] = defaultdict(set)
    for mapping in catalog.competition_mappings:
        competition_labels[mapping.internal_competition_id].add(
            mapping.provider_competition_name
        )
    return tuple(
        sorted(
            match.internal_match_id
            for match in catalog.canonical_matches
            if match.kickoff_at_utc == candidate.kickoff_at_utc
            and candidate.home_label in team_labels[match.internal_home_team_id]
            and candidate.away_label in team_labels[match.internal_away_team_id]
            and candidate.competition_label
            in competition_labels[match.internal_competition_id]
        )
    )


def _capture_plan(
    candidates: tuple[DailySlateCandidatePlan, ...],
    tasks: tuple[DailySlateReconciliationTask, ...],
) -> DailySlateCapturePlan:
    fixture_groups: defaultdict[str, list[DailySlateCandidatePlan]] = defaultdict(list)
    market_groups: defaultdict[str, list[DailySlateCandidatePlan]] = defaultdict(list)
    for item in candidates:
        if DailySlateCandidateStatus.FIXTURE_SOURCE_REQUIRED in item.statuses:
            fixture_groups[item.candidate.competition_label].append(item)
        if DailySlateCandidateStatus.MARKET_ODDS_REQUIRED in item.statuses:
            market_groups[item.candidate.competition_label].append(item)

    requests = [
        *_capture_requests(DailySlateCaptureKind.FIXTURE_SOURCE, fixture_groups),
        *_capture_requests(DailySlateCaptureKind.MARKET_ODDS, market_groups),
    ]
    ordered_requests = tuple(
        sorted(
            requests,
            key=lambda item: (item.kind.value, item.competition_label, item.request_id),
        )
    )
    return DailySlateCapturePlan.freeze(
        requests=ordered_requests,
        sporttery_ingestion_candidate_ids=tuple(
            sorted(
                item.candidate.candidate_id
                for item in candidates
                if DailySlateCandidateStatus.SPORTTERY_SP_READY in item.statuses
            )
        ),
        reconciliation_task_ids=tuple(sorted(item.task_id for item in tasks)),
        ready_match_ids=tuple(
            sorted(
                item.canonical_match_id
                for item in candidates
                if item.canonical_match_id is not None
                and DailySlateCandidateStatus.READY_FOR_CAPTURE in item.statuses
            )
        ),
    )


def _capture_requests(
    kind: DailySlateCaptureKind,
    groups: dict[str, list[DailySlateCandidatePlan]],
) -> tuple[DailySlateCaptureRequest, ...]:
    requests = []
    for competition_label in sorted(groups):
        items = groups[competition_label]
        requests.append(
            DailySlateCaptureRequest.freeze(
                kind=kind,
                competition_label=competition_label,
                kickoff_from_utc=min(
                    item.candidate.kickoff_at_utc for item in items
                ),
                kickoff_to_utc=max(item.candidate.kickoff_at_utc for item in items),
                candidate_ids=tuple(
                    sorted(item.candidate.candidate_id for item in items)
                ),
                canonical_match_ids=tuple(
                    sorted(
                        item.canonical_match_id
                        for item in items
                        if item.canonical_match_id is not None
                    )
                ),
            )
        )
    return tuple(requests)
