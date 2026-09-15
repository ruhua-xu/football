"""Pure strict V4 contracts and local generic correction; no I/O."""

import hashlib
import json
from decimal import Decimal, localcontext

from football_system.domain.archive import canonical_json
from football_system.domain.market_analysis import ArtifactRefV1
from football_system.domain.market_v2 import (
    QUANTUM,
    MarketTypeV1,
    distribution,
    revalidate,
    fixed_decimal,
)
from football_system.domain.review_v4 import (
    MAX_V4_BYTES,
    AnalysisPacketV4,
    GenericFusionPolicyV1,
    GenericFusionRunV1,
    GenericFusionUnitV1,
    ImportedReviewV4,
    LLMReviewV4,
    MarketReviewContextV4,
    PacketMarketUnitV4,
    UnavailableMarketReviewV4,
    ValidMarketReviewV4,
)


def parse_v4_json(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_V4_BYTES:
        raise ValueError("V4 file size limit exceeded")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate V4 JSON key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError(
            "V4 probabilities/prices must be Decimal strings, not JSON float/NaN"
        )

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_float=reject,
        parse_constant=reject,
    )


def export_packet_v4(analysis):
    analysis = revalidate(analysis)
    units = []
    for unit in analysis.units:
        context = MarketReviewContextV4.freeze(
            identity=unit.identity,
            market_key=unit.market_key,
            decision_cutoff=unit.decision_cutoff,
            p_market=unit.legacy_source.p_market
            if unit.legacy_source
            else unit.consensus.probabilities,
            p_quant=unit.p_quant,
            market_status="AVAILABLE" if unit.legacy_source else unit.consensus.status,
            quant_status=unit.quant_status,
            unavailable_reason=unit.unavailable_reason,
            data_quality=unit.data_quality,
            model_lineage=unit.model_lineage,
            evidence=unit.evidence,
        )
        units.append(
            PacketMarketUnitV4(
                review_context=context,
                review_context_id=context.artifact_id,
                review_context_hash=context.content_hash,
                evidence_ids=tuple(e.artifact_id for e in unit.evidence),
            )
        )
    packet = AnalysisPacketV4.freeze(
        analysis=ArtifactRefV1.of(analysis), market_units=tuple(units)
    )
    raw = canonical_json(packet).encode()
    if len(raw) > MAX_V4_BYTES:
        raise ValueError("V4 packet exceeds file bound")
    return packet


def check_review(packet, submission):
    if (submission.packet_id, submission.packet_hash, submission.analysis_id) != (
        packet.artifact_id,
        packet.content_hash,
        packet.analysis.artifact_id,
    ):
        raise ValueError("V4 packet identity binding mismatch")
    expected = tuple(
        (u.review_context.identity.match_id, u.review_context.market_key.canonical)
        for u in packet.market_units
    )
    if (
        tuple((r.match_id, r.market_key.canonical) for r in submission.market_reviews)
        != expected
    ):
        raise ValueError("V4 review requires exact complete market unit coverage")
    for unit, review in zip(
        packet.market_units, submission.market_reviews, strict=True
    ):
        c = unit.review_context
        if (review.review_context_id, review.review_context_hash) != (
            unit.review_context_id,
            unit.review_context_hash,
        ):
            raise ValueError("V4 review context binding mismatch")
        if c.quant_status == "MODEL_UNAVAILABLE":
            if (
                not isinstance(review, UnavailableMarketReviewV4)
                or review.failure_code != "MODEL_UNAVAILABLE"
            ):
                raise ValueError(
                    "MODEL_UNAVAILABLE quant requires UNAVAILABLE / MODEL_UNAVAILABLE"
                )
        elif (
            isinstance(review, UnavailableMarketReviewV4)
            and review.failure_code == "MODEL_UNAVAILABLE"
        ):
            raise ValueError("AVAILABLE quant cannot claim MODEL_UNAVAILABLE")
        if isinstance(review, ValidMarketReviewV4):
            refs = (
                *review.evidence_refs,
                *(
                    ref
                    for scenario in (*review.scenarios, *review.counter_scenarios)
                    for ref in scenario.evidence_refs
                ),
            )
            if not set(refs) <= set(unit.evidence_ids):
                raise ValueError("unknown V4 evidence reference")


def validate_v4_files(packet_bytes, review_bytes):
    packet_data = parse_v4_json(packet_bytes)
    review_data = parse_v4_json(review_bytes)
    if (
        packet_data.get("schema_version") != "ANALYSIS_PACKET_V4"
        or review_data.get("schema_version") != "LLM_REVIEW_V4"
    ):
        raise ValueError("explicit V4 schema versions required")
    packet = AnalysisPacketV4.model_validate(packet_data)
    submission = LLMReviewV4.model_validate(review_data)
    check_review(packet, submission)
    return packet, submission


def import_v4_bytes(packet_bytes, review_bytes):
    packet, submission = validate_v4_files(packet_bytes, review_bytes)
    return ImportedReviewV4.freeze(
        packet=packet,
        submission=submission,
        raw_review_json=review_bytes.decode("utf-8"),
        raw_review_hash=hashlib.sha256(review_bytes).hexdigest(),
    )


@fixed_decimal(28)
def generic_correction(base, llm, confidence, quality, cap):
    if base.market_key != llm.market_key:
        raise ValueError("generic fusion crosses market")
    if base.market_key.market_type == MarketTypeV1.THREE_WAY:
        # Explicit adapter only: preserve old numerical behavior exactly.
        from football_system.domain.market import ThreeWayProbability
        from football_system.domain.services.probability import (
            quantize_three_way_probability,
        )
        from football_system.domain.market_v2 import MarketProbabilityDistributionV1

        before = base.to_three_way()
        proposed = llm.to_three_way()
        influence = confidence * quality
        changes = {
            k.value.lower(): (p - before.for_selection(k)) * influence
            for k, p in proposed.items()
        }
        peak = max(abs(v) for v in changes.values())
        safe = max(Decimal(0), cap - QUANTUM * 2)
        scale = Decimal(1) if peak == 0 else min(Decimal(1), safe / peak)
        result = quantize_three_way_probability(
            ThreeWayProbability(
                **{
                    k.value.lower(): p + changes[k.value.lower()] * scale
                    for k, p in before.items()
                }
            )
        )
        if any(abs(result.for_selection(k) - p) > cap for k, p in before.items()):
            raise ValueError("legacy-adapter correction exceeds frozen cap")
        return MarketProbabilityDistributionV1.from_three_way(result)
    with localcontext() as ctx:
        ctx.prec = 80
        b = [x.probability for x in base.outcomes]
        p = [x.probability for x in llm.outcomes]
        # Close tolerated input residues before the convex move. No per-outcome clamp.
        b = list(x.probability for x in distribution(base.market_key, b).outcomes)
        p = list(x.probability for x in distribution(llm.market_key, p).outcomes)
        influence = confidence * quality
        changes = [(x - y) * influence for x, y in zip(p, b, strict=True)]
        peak = max(map(abs, changes))
        safe = max(Decimal(0), cap - QUANTUM * len(b))
        scale = Decimal(1) if peak == 0 else min(Decimal(1), safe / peak)
        values = [x + change * scale for x, change in zip(b, changes, strict=True)]
        result = distribution(base.market_key, values)
        if any(
            abs(x.probability - y) > cap
            for x, y in zip(result.outcomes, b, strict=True)
        ):
            raise ValueError("generic correction cap exceeded")
        return result


@fixed_decimal(28)
def fuse_v4(analysis, review, policy=None):
    analysis = revalidate(analysis)
    review = revalidate(review)
    policy = revalidate(policy or GenericFusionPolicyV1())
    if export_packet_v4(analysis) != review.packet:
        raise ValueError("V4 packet is not the exact sealed analysis projection")
    results = []
    for unit, item in zip(
        analysis.units, review.submission.market_reviews, strict=True
    ):
        influence = Decimal(0)
        p = unit.p_base
        fallback = unit.unavailable_reason
        if isinstance(item, ValidMarketReviewV4) and p is not None:
            with localcontext() as context:
                context.prec = (
                    28 if unit.market_key.market_type == MarketTypeV1.THREE_WAY else 80
                )
                influence = item.assessment_confidence * unit.data_quality
            p = generic_correction(
                p,
                item.p_llm,
                item.assessment_confidence,
                unit.data_quality,
                policy.max_probability_delta,
            )
        elif p is not None:
            fallback = item.failure_code
        results.append(
            GenericFusionUnitV1(
                unit_id=unit.artifact_id,
                match_id=unit.identity.match_id,
                market_key=unit.market_key,
                p_final=p,
                influence=influence,
                fallback_code=fallback,
            )
        )
    return GenericFusionRunV1.freeze(
        analysis=ArtifactRefV1.of(analysis),
        review=ArtifactRefV1.of(review),
        policy=policy,
        results=tuple(results),
    )
