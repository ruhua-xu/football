"""Synthetic pure version selection, not persistence or production authorization."""

from datetime import timedelta
from decimal import Decimal

import pytest

from football_system.domain.archive import match_result_payload_sha256
from football_system.domain.training_admission import ProviderResultStatusCategory
from football_system.domain.training_correction import (
    CorrectionComponent,
    CorrectionComponentBindingV2,
    CorrectionRefV2,
    CorrectionSnapshotV2,
    CorrectionStreamV2,
    SourceCorrectionV2,
    TrainingCorrectionContextV2,
    TrainingFactVersionV2,
)
from football_system.domain.versioned_training_history import (
    VersionedFactRefV2,
    project_versioned_training_fact,
    select_versioned_training_facts,
    select_versioned_training_heads,
    validate_versioned_context,
    versioned_selection_root,
)
from football_system.domain.services.elo_baseline import EloThreeWayBaseline
from tests.unit.test_quant_integrity import (
    _fact,
    NOW,
    WARMUP,
    PILOT,
    PRODUCTION,
    COMPETITION,
)


def ref(name, schema="TRAINING_BASE_FACT_VERSION_V2"):
    return CorrectionRefV2(
        schema_version=schema, artifact_id=name, content_hash="a" * 64
    )


def base(name="match", *, season=WARMUP, kickoff=None):
    kwargs = {} if kickoff is None else {"kickoff": kickoff}
    b = _fact(name, season=season, **kwargs).content_payload
    r, m, i = (
        b.normalized_result,
        b.season_membership.content_payload,
        b.canonical_identity,
    )
    snapshot = CorrectionSnapshotV2(
        stream=CorrectionStreamV2(
            source_id="synthetic-source",
            provider_code="synthetic-provider",
            provider_fixture_namespace="fixture",
            provider_fixture_key=f"key-{name}",
            internal_match_id=name,
        ),
        identity=i,
        provider_mapping=b.provider_mapping,
        provider_home_team_id="ph",
        provider_away_team_id="pa",
        provider_competition_id=m.provider_competition_id,
        provider_season_id=m.provider_season_id,
        home_team_alias_id="home-alias",
        away_team_alias_id="away-alias",
        competition_mapping_id="competition-map",
        season_mapping_version="1",
        mapping_policy_version="1",
        status_mapping_version="1",
        provider_raw_status="FT",
        provider_status_category="REGULAR_TIME_FINAL",
        raw_score_semantics="RT",
        regular_time_score_semantics="RT",
        home_goals=r.home_goals,
        away_goals=r.away_goals,
        provider_finalized_at_utc=r.observed_at_utc,
        source_observed_at_utc=r.observed_at_utc,
        fixture_source_available_at_utc=b.fixture_source.source_available_at_utc,
        mapping_source_available_at_utc=m.source_available_at_utc,
        result_source_available_at_utc=r.available_at_utc,
        provider_result_key=r.source_result_key,
    )
    return TrainingFactVersionV2(
        reference=ref(name),
        base_admission=ref("admission", "TRAINING_FACT_ADMISSION_V1"),
        base_binding=ref(name + "-binding", "TRAINING_FACT_BINDING_V1"),
        revision_sequence=0,
        predecessor=None,
        snapshot=snapshot,
        normalized_result=r,
        latest_match_result_id=r.match_result_id,
        registered_at_utc=NOW - timedelta(days=2),
        components=tuple(
            CorrectionComponentBindingV2(
                component=c,
                reference=ref(name + c),
                source_available_at_utc=snapshot.effective_source_available_at_utc,
            )
            for c in CorrectionComponent
        ),
    )


def successor(previous, *, trainable=True, same_time=False, season=None, kickoff=None):
    source = previous.snapshot.result_source_available_at_utc + (
        timedelta(0) if same_time else timedelta(days=2)
    )
    identity = previous.snapshot.identity.model_copy(
        update={
            **({"season": season} if season else {}),
            **({"kickoff_at_utc": kickoff} if kickoff else {}),
        }
    )
    snapshot = previous.snapshot.model_copy(
        update=dict(
            identity=identity,
            home_goals=0 if trainable else None,
            away_goals=3 if trainable else None,
            provider_status_category=ProviderResultStatusCategory.REGULAR_TIME_FINAL
            if trainable
            else ProviderResultStatusCategory.CANCELLED,
            provider_raw_status="FT" if trainable else "CANCELLED",
            source_observed_at_utc=source,
            result_source_available_at_utc=source,
            provider_revision_id="revision",
            provider_revision_order=previous.revision_sequence + 1,
        )
    )
    result = (
        None
        if not trainable
        else previous.normalized_result.model_copy(
            update=dict(
                match_result_id=previous.latest_match_result_id + "-next",
                home_goals=0,
                away_goals=3,
                payload_hash=match_result_payload_sha256(0, 3),
                observed_at_utc=source,
                available_at_utc=source,
                ingested_at_utc=source,
                supersedes_match_result_id=previous.latest_match_result_id,
            )
        )
    )
    reference = ref(previous.version_id + "-next", "TRAINING_CORRECTION_ADMISSION_V2")
    components = tuple(
        c.model_copy(
            update={
                "reference": ref(c.reference.artifact_id + "-next"),
                "source_available_at_utc": source,
            }
        )
        for c in previous.components
    )
    version = TrainingFactVersionV2(
        reference=reference,
        base_admission=previous.base_admission,
        base_binding=previous.base_binding,
        revision_sequence=previous.revision_sequence + 1,
        predecessor=previous.reference,
        snapshot=snapshot,
        components=components,
        normalized_result=result,
        latest_match_result_id=previous.latest_match_result_id
        if result is None
        else result.match_result_id,
        registered_at_utc=NOW - timedelta(days=1),
    )
    events = tuple(
        SourceCorrectionV2(
            transition=reference,
            stream=snapshot.stream,
            revision_sequence=version.revision_sequence,
            component=old.component,
            predecessor_version=previous.reference,
            successor_version=reference,
            predecessor=old.reference,
            successor=new.reference,
            predecessor_source_available_at_utc=old.source_available_at_utc,
            source_available_at_utc=new.source_available_at_utc,
            local_imported_at_utc=version.registered_at_utc,
            registered_at_utc=version.registered_at_utc,
        )
        for old, new in zip(previous.components, components, strict=True)
    )
    return version, events


def context(*versions, events=()):
    return TrainingCorrectionContextV2(
        actual_at_utc=NOW, versions=versions, corrections=events
    )


def select(ctx, cutoff=NOW, excluded=()):
    return select_versioned_training_facts(
        ctx, COMPETITION, (WARMUP, PILOT, PRODUCTION), cutoff, excluded, False
    )


def test_whole_version_strict_boundary_and_withdrawal_filter_order():
    original = base()
    withdrawn, events = successor(original, trainable=False)
    ctx = context(original, withdrawn, events=events)
    at = withdrawn.snapshot.effective_source_available_at_utc
    assert select(ctx, at - timedelta(seconds=1)) == (original,)
    assert select(ctx, at) == ()
    assert select_versioned_training_heads(
        ctx, source_cutoffs={"synthetic-source": at}, strict_cutoff=True
    ) == (original,)
    assert select_versioned_training_heads(
        ctx, source_cutoffs={"synthetic-source": at}, strict_cutoff=False
    ) == (withdrawn,)


def test_equal_time_uses_explicit_order_and_excludes_operation_targets():
    original = base()
    corrected, events = successor(original, same_time=True)
    ctx = context(original, corrected, events=events)
    assert select(ctx) == (corrected,)
    assert select(ctx, excluded=("match",)) == ()
    unordered = corrected.model_copy(
        update={
            "snapshot": corrected.snapshot.model_copy(
                update={"provider_revision_id": None, "provider_revision_order": None}
            )
        }
    )
    with pytest.raises(ValueError, match="explicit revision"):
        select(context(original, unordered, events=events))


def test_missing_predecessor_fork_and_registration_fail_closed():
    original = base()
    revised, events = successor(original)
    with pytest.raises(ValueError, match="predecessor"):
        select(context(revised, events=events))
    with pytest.raises(ValueError, match="duplicate"):
        select(context(original, revised, revised, events=events))
    with pytest.raises(ValueError, match="registered"):
        select(
            context(original, revised, events=events).model_copy(
                update={"actual_at_utc": original.registered_at_utc}
            )
        )
    with pytest.raises(ValueError, match="complete correction"):
        validate_versioned_context(context(original, revised))


def test_kickoff_and_season_revision_changes_actual_point_seven_five_transition():
    a = base("a")
    b = base("b", kickoff=a.snapshot.identity.kickoff_at_utc + timedelta(days=5))
    corrected, events = successor(
        b,
        season=PILOT,
        kickoff=b.snapshot.identity.kickoff_at_utc + timedelta(minutes=5),
    )
    ctx = context(a, b, corrected, events=events)
    chosen = select(ctx)
    assert tuple(v.snapshot.identity.season for v in chosen) == (WARMUP, PILOT)
    baseline = EloThreeWayBaseline()
    results = tuple(project_versioned_training_fact(v) for v in chosen)
    pilot = baseline.rebuild_state(results, NOW, target_season_id=PILOT)
    production = baseline.rebuild_state(results, NOW, target_season_id=PRODUCTION)
    for team in pilot.teams:
        expected = (
            Decimal(1500) + Decimal("0.75") * (team.rating - Decimal(1500))
        ).quantize(Decimal("0.000000000001"))
        assert production.for_team(team.team_id).rating == expected
    assert production.training_facts == pilot.training_facts
    assert len(production.training_facts) == 2
    assert versioned_selection_root(
        tuple(VersionedFactRefV2.of(v) for v in chosen)
    ) != versioned_selection_root(tuple(VersionedFactRefV2.of(v) for v in ctx.versions))


def test_season_revision_cannot_create_out_of_order_blocks():
    first = base("a", season=PILOT)
    second = base(
        "b", kickoff=first.snapshot.identity.kickoff_at_utc + timedelta(days=2)
    )
    with pytest.raises(ValueError, match="chronological blocks"):
        select(context(first, second))


def test_invalid_target_and_nontrainable_target_cannot_be_pinned():
    from football_system.domain.quant_integrity import QuantIntegrityTargetV2

    original = base()
    target = dict(
        identity=original.snapshot.identity,
        training_fact_admission_id="admission",
        fact=VersionedFactRefV2.of(original),
        fixture_source_available_at_utc=original.snapshot.fixture_source_available_at_utc,
        mapping_source_available_at_utc=original.snapshot.mapping_source_available_at_utc,
    )
    assert QuantIntegrityTargetV2(**target).fact.match_id == "match"
    with pytest.raises(ValueError, match="identity"):
        QuantIntegrityTargetV2(
            **{
                **target,
                "fact": target["fact"].model_copy(update={"match_id": "another-match"}),
            }
        )
    with pytest.raises(ValueError, match="trainable"):
        QuantIntegrityTargetV2(
            **{
                **target,
                "fact": target["fact"].model_copy(update={"match_result_id": None}),
            }
        )


def test_independent_per_source_cutoffs_never_use_registration_as_publication():
    first, second = base("first"), base("second")
    second = second.model_copy(
        update={
            "snapshot": second.snapshot.model_copy(
                update={
                    "stream": second.snapshot.stream.model_copy(
                        update={"source_id": "another-source"}
                    )
                }
            )
        }
    )
    revised_first, first_events = successor(first)
    revised_second, second_events = successor(second)
    ctx = context(
        first,
        second,
        revised_first,
        revised_second,
        events=(*first_events, *second_events),
    )
    boundary = revised_first.snapshot.result_source_available_at_utc
    cutoffs = {
        "synthetic-source": boundary - timedelta(microseconds=1),
        "another-source": boundary,
    }
    assert {
        v.version_id
        for v in select_versioned_training_heads(
            ctx, source_cutoffs=cutoffs, strict_cutoff=False
        )
    } == {first.version_id, revised_second.version_id}
    assert boundary < revised_second.registered_at_utc
    with pytest.raises(ValueError, match="per-source cutoff"):
        select_versioned_training_heads(ctx, source_cutoffs={"synthetic-source": NOW})
    with pytest.raises(ValueError, match="actual context"):
        select_versioned_training_heads(
            ctx,
            source_cutoffs={**cutoffs, "synthetic-source": NOW + timedelta(seconds=1)},
        )


@pytest.mark.parametrize(
    "component",
    [
        "fixture_source_available_at_utc",
        "mapping_source_available_at_utc",
        "provider_mapping",
    ],
)
def test_whole_version_waits_for_each_metadata_source(component):
    original = base()
    later = original.snapshot.result_source_available_at_utc + timedelta(hours=1)
    value = (
        original.snapshot.provider_mapping.model_copy(
            update={"available_at_utc": later}
        )
        if component == "provider_mapping"
        else later
    )
    original = original.model_copy(
        update={"snapshot": original.snapshot.model_copy(update={component: value})}
    )
    ctx = context(original)
    assert select(ctx, later - timedelta(microseconds=1)) == ()
    assert select(ctx, later) == (original,)
    projected = project_versioned_training_fact(original)
    assert projected.available_at_utc == original.normalized_result.available_at_utc
    assert projected.ingested_at_utc != original.registered_at_utc


@pytest.mark.parametrize(
    "corruption",
    ["type", "root-result", "future-source", "revision-order", "fork", "snapshot"],
)
def test_version_context_tampering_fails_before_elo_projection(corruption):
    original = base()
    revised, events = successor(original)
    if corruption == "type":
        revised = revised.model_copy(update={"reference": ref(revised.version_id)})
    elif corruption == "root-result":
        original = original.model_copy(update={"latest_match_result_id": "unbound"})
    elif corruption == "future-source":
        original = original.model_copy(
            update={
                "registered_at_utc": original.snapshot.result_source_available_at_utc
                - timedelta(seconds=1)
            }
        )
    elif corruption == "revision-order":
        original = original.model_copy(
            update={
                "snapshot": original.snapshot.model_copy(
                    update={
                        "provider_revision_id": "original",
                        "provider_revision_order": 2,
                    }
                )
            }
        )
    elif corruption == "fork":
        fork = revised.model_copy(
            update={"reference": ref("fork", "TRAINING_CORRECTION_ADMISSION_V2")}
        )
        with pytest.raises(ValueError, match="fork"):
            select(context(original, revised, fork, events=events))
        return
    else:
        revised = revised.model_copy(
            update={"snapshot": revised.snapshot.model_copy(update={"home_goals": 9})}
        )
    with pytest.raises(ValueError):
        select(context(original, revised, events=events))
