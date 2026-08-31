import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from football_system.application.review_bridge import (
    ExportAnalysisPacketService,
    ImportLLMReviewService,
    build_analysis_packet,
    build_analysis_packet_v2,
    canonical_json,
    strict_json_loads,
    validate_review_files,
)
from football_system.infrastructure.files.review_bridge import (
    read_contract_file,
    write_contract_file,
)
from football_system.domain.market import (
    ThreeWayFixedBonus,
    ThreeWayMarketOdds,
    ThreeWayProbability,
)
from football_system.domain.review import (
    AnalysisPacketMatch,
    AnalysisPacketMatchSourceV2,
    AnalysisPacketRun,
    AnalysisPacketSource,
    AnalysisPacketSourceV2,
    MatchReviewContext,
    PacketDataQuality,
    PacketDataQualityStatus,
    PacketEvidence,
    PacketInternationalOdds,
    PacketMarketPrediction,
    PacketQuantPrediction,
    PacketSportteryOdds,
    StoredAnalysisPacket,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
PROBABILITIES = ThreeWayProbability(
    home_win=Decimal("0.6"),
    draw=Decimal("0.25"),
    away_win=Decimal("0.15"),
)


def _source() -> AnalysisPacketSource:
    return AnalysisPacketSource(
        analysis_run=AnalysisPacketRun(
            analysis_run_id="run-review",
            as_of_at_utc=NOW,
            completed_at_utc=NOW,
            pipeline_version="MVP_V1",
            code_revision="package:test",
            input_manifest_version="MVP_INPUT_MANIFEST_V2",
            input_manifest_hash="b" * 64,
        ),
        matches=(
            AnalysisPacketMatch(
                match_id="match-1",
                competition_id="competition-1",
                competition_name="League",
                home_team_id="team-home",
                home_team_name="Home",
                away_team_id="team-away",
                away_team_name="Away",
                kickoff_at_utc=NOW,
                market_key="THREE_WAY",
                context_hash="context-hash",
                p_market=PacketMarketPrediction(
                    prediction_id="p-market-1",
                    probabilities=PROBABILITIES,
                    input_snapshot_ids=("snapshot-1",),
                ),
                p_quant=PacketQuantPrediction(
                    prediction_id="p-quant-1",
                    probabilities=PROBABILITIES,
                    manual_input_id="manual-1",
                    input_payload_hash="payload-1",
                ),
            ),
        ),
    )


def _review(packet, **match_updates) -> dict:
    match_review = {
        "status": "VALID",
        "match_id": "match-1",
        "market_key": "THREE_WAY",
        "p_llm": {
            "home_win": "0.58",
            "draw": "0.26",
            "away_win": "0.16",
        },
        "assessment_confidence": "0.5",
        "scenarios": [],
        "preferred_outcomes": [],
        "avoid_outcomes": [],
        "counter_scenarios": [],
        "risk_tags": ["LINEUP_UNCERTAINTY"],
        "reasoning_summary": "Offline review based only on the supplied packet.",
        "limitations": ["No frozen external evidence supplied"],
    }
    match_review.update(match_updates)
    return {
        "schema_version": "LLM_REVIEW_V1",
        "analysis_run_id": packet.analysis_run.analysis_run_id,
        "packet_id": packet.packet_id,
        "packet_hash": packet.packet_hash,
        "match_reviews": [match_review],
    }


def _source_v2() -> AnalysisPacketSourceV2:
    base = _source()
    evidence = (
        PacketEvidence(
            evidence_id="evidence-market-1",
            category="INTERNATIONAL_ODDS",
            body="International odds for THREE_WAY: HOME_WIN=2, DRAW=3, AWAY_WIN=4.",
            source_kind="SEALED_DATABASE_SNAPSHOT",
            source_name="Mock Provider / Mock Bookmaker",
            source_reference="market_odds_snapshots/snapshot-1",
            source_record_id="snapshot-1",
            source_payload_hash="payload-market-1",
            observed_at_utc=NOW,
            available_at_utc=NOW,
        ),
        PacketEvidence(
            evidence_id="evidence-sporttery-1",
            category="SPORTTERY_ODDS",
            body="Sporttery fixed bonus for THREE_WAY: HOME_WIN=2.1, DRAW=3.1, AWAY_WIN=3.5.",
            source_kind="SEALED_DATABASE_SNAPSHOT",
            source_name="Mock Sporttery",
            source_reference="sporttery_bonus_snapshots/bonus-1",
            source_record_id="bonus-1",
            source_payload_hash="payload-sporttery-1",
            observed_at_utc=NOW,
            available_at_utc=NOW,
        ),
    )
    context = MatchReviewContext(
        sporttery_odds=PacketSportteryOdds(
            snapshot_id="bonus-1",
            provider_id="provider-sporttery",
            provider_name="Mock Sporttery",
            sporttery_match_no="M001",
            sale_status="OPEN",
            captured_at_utc=NOW,
            available_at_utc=NOW,
            payload_hash="payload-sporttery-1",
            odds=ThreeWayFixedBonus(
                home_win=Decimal("2.1"),
                draw=Decimal("3.1"),
                away_win=Decimal("3.5"),
            ),
        ),
        international_odds=PacketInternationalOdds(
            snapshot_id="snapshot-1",
            provider_id="provider-market",
            provider_name="Mock Provider",
            bookmaker_id="bookmaker-1",
            bookmaker_name="Mock Bookmaker",
            captured_at_utc=NOW,
            available_at_utc=NOW,
            payload_hash="payload-market-1",
            odds=ThreeWayMarketOdds(
                home_win=Decimal("2"),
                draw=Decimal("3"),
                away_win=Decimal("4"),
            ),
        ),
        evidence=evidence,
        data_quality=PacketDataQuality(
            status=PacketDataQualityStatus.PARTIAL,
            score=Decimal("0.25"),
            available_fields=("evidence", "international_odds", "sporttery_odds"),
            missing_fields=(
                "confirmed_lineup",
                "expected_lineup",
                "home_away_form",
                "injuries",
                "odds_movement_summary",
                "recent_form",
                "rest_days",
                "schedule_context",
                "suspensions",
            ),
            notes=("Mock contextual data is unavailable.",),
        ),
    )
    return AnalysisPacketSourceV2(
        analysis_run=base.analysis_run,
        matches=(
            AnalysisPacketMatchSourceV2(
                **base.matches[0].model_dump(mode="python", exclude={"evidence_ids"}),
                evidence_ids=tuple(item.evidence_id for item in evidence),
                review_context=context,
            ),
        ),
    )


def _review_v2(packet, **match_updates) -> dict:
    review = _review(packet, **match_updates)
    review["schema_version"] = "LLM_REVIEW_V2"
    review["match_reviews"][0]["review_context_id"] = packet.matches[
        0
    ].review_context_id
    review["match_reviews"][0]["review_context_hash"] = packet.matches[
        0
    ].review_context_hash
    return review


def _packet_bytes(packet) -> bytes:
    return canonical_json(packet.model_dump(mode="json")).encode("utf-8")


def test_analysis_packet_is_deterministic_and_excludes_betting_outputs() -> None:
    packet = build_analysis_packet(_source(), NOW)
    repeated = build_analysis_packet(_source(), NOW)

    assert packet == repeated
    serialized = canonical_json(packet.model_dump(mode="json"))
    assert packet.packet_hash in serialized
    for forbidden in ("p_final", "ev", "budget", "stake", "ticket", "portfolio"):
        assert f'"{forbidden}"' not in serialized.lower()
    assert '"config_hash"' not in serialized


def test_analysis_packet_v2_contains_auditable_context_and_strict_whitelist() -> None:
    packet = build_analysis_packet_v2(_source_v2(), NOW)
    repeated = build_analysis_packet_v2(_source_v2(), NOW)

    assert packet == repeated
    serialized = canonical_json(packet.model_dump(mode="json"))
    context = packet.matches[0].review_context
    assert context.evidence[0].body
    assert context.evidence[0].source_reference
    assert context.data_quality.status == PacketDataQualityStatus.PARTIAL
    for field in (
        "sporttery_odds",
        "international_odds",
        "odds_movement_summary",
        "recent_form",
        "home_away_form",
        "rest_days",
        "schedule_context",
        "injuries",
        "suspensions",
        "expected_lineup",
        "confirmed_lineup",
        "evidence",
        "data_quality",
    ):
        assert f'"{field}"' in serialized
    for forbidden in (
        "p_final",
        "ev",
        "ranking",
        "ticket",
        "portfolio",
        "budget",
        "stake",
        "fusion_weight",
        "strategy_profile",
    ):
        assert f'"{forbidden}"' not in serialized.lower()


def test_review_v2_binds_the_exact_review_context_and_rejects_mixed_versions() -> None:
    packet = build_analysis_packet_v2(_source_v2(), NOW)
    packet_bytes = _packet_bytes(packet)

    _, submission, _ = validate_review_files(
        packet_bytes,
        canonical_json(_review_v2(packet)).encode("utf-8"),
    )
    assert submission.schema_version == "LLM_REVIEW_V2"

    tampered = _review_v2(packet)
    tampered["match_reviews"][0]["review_context_hash"] = "0" * 64
    with pytest.raises(ValueError, match="context mismatch"):
        validate_review_files(packet_bytes, canonical_json(tampered).encode("utf-8"))

    with pytest.raises(ValueError, match="file contract validation"):
        validate_review_files(
            packet_bytes,
            canonical_json(_review(packet)).encode("utf-8"),
        )


def test_offline_review_contract_validates_binding_and_absolute_probability() -> None:
    packet = build_analysis_packet(_source(), NOW)
    review_bytes = canonical_json(_review(packet)).encode("utf-8")

    validated_packet, submission, normalized = validate_review_files(
        _packet_bytes(packet), review_bytes
    )

    assert validated_packet == packet
    assert submission.match_reviews[0].p_llm.home_win == Decimal("0.58")
    assert json.loads(normalized)["packet_hash"] == packet.packet_hash


@pytest.mark.parametrize(
    "mutation",
    (
        lambda packet, review: review.update({"packet_hash": "0" * 64}),
        lambda packet, review: review["match_reviews"][0].update(
            {"probability_delta": {"home_win": "0.01"}}
        ),
        lambda packet, review: review["match_reviews"][0].update(
            {"p_final": {"home_win": "0.58"}}
        ),
        lambda packet, review: review["match_reviews"][0].update(
            {
                "p_llm": {
                    "home_win": "0.9",
                    "draw": "0.2",
                    "away_win": "0.1",
                }
            }
        ),
    ),
)
def test_offline_review_rejects_wrong_binding_and_forbidden_fields(mutation) -> None:
    packet = build_analysis_packet(_source(), NOW)
    review = _review(packet)
    mutation(packet, review)

    with pytest.raises(ValueError):
        validate_review_files(
            _packet_bytes(packet), canonical_json(review).encode("utf-8")
        )


def test_strict_json_rejects_duplicate_keys_and_tampered_packet() -> None:
    packet = build_analysis_packet(_source(), NOW)
    duplicate = b'{"schema_version":"LLM_REVIEW_V1","schema_version":"LLM_REVIEW_V1"}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        validate_review_files(_packet_bytes(packet), duplicate)

    tampered = packet.model_copy(update={"packet_hash": "0" * 64})
    with pytest.raises(ValueError, match="hash verification"):
        validate_review_files(
            _packet_bytes(tampered),
            canonical_json(_review(tampered)).encode("utf-8"),
        )


def test_review_normalization_is_stable_for_decimal_scale() -> None:
    packet = build_analysis_packet(_source(), NOW)
    first = _review(packet)
    second = _review(
        packet,
        assessment_confidence="0.50",
        p_llm={
            "home_win": "0.580",
            "draw": "0.260",
            "away_win": "0.160",
        },
    )

    _, _, first_normalized = validate_review_files(
        _packet_bytes(packet), canonical_json(first).encode("utf-8")
    )
    _, _, second_normalized = validate_review_files(
        _packet_bytes(packet), canonical_json(second).encode("utf-8")
    )

    assert first_normalized == second_normalized


def test_review_normalization_is_stable_for_semantic_collection_order() -> None:
    source = _source().model_copy(
        update={
            "matches": (
                _source()
                .matches[0]
                .model_copy(update={"evidence_ids": ("evidence-1", "evidence-2")}),
            )
        }
    )
    packet = build_analysis_packet(source, NOW)
    scenarios = [
        {
            "scenario_id": "scenario-1",
            "scenario_type": "MAIN",
            "market_key": "THREE_WAY",
            "outcome": "HOME_WIN",
            "summary": "Home controls the match.",
            "trigger_conditions": ["condition-b", "condition-a"],
            "evidence_ids": ["evidence-2", "evidence-1"],
        },
        {
            "scenario_id": "scenario-2",
            "scenario_type": "UPSET",
            "market_key": "THREE_WAY",
            "outcome": "AWAY_WIN",
            "summary": "Away exploits transitions.",
            "trigger_conditions": [],
            "evidence_ids": ["evidence-1"],
        },
    ]
    opinions = [
        {
            "market_key": "THREE_WAY",
            "outcome": "HOME_WIN",
            "strength": "MEDIUM",
            "rationale": "Supported by the main scenario.",
            "evidence_ids": ["evidence-2", "evidence-1"],
        },
        {
            "market_key": "THREE_WAY",
            "outcome": "DRAW",
            "strength": "LOW",
            "rationale": "A secondary possibility.",
            "evidence_ids": [],
        },
    ]
    first = _review(
        packet,
        scenarios=scenarios,
        preferred_outcomes=opinions,
        risk_tags=["TAG_B", "TAG_A"],
        limitations=["LIMIT_B", "LIMIT_A"],
    )
    second = _review(
        packet,
        scenarios=[
            scenarios[1],
            {
                **scenarios[0],
                "trigger_conditions": list(
                    reversed(scenarios[0]["trigger_conditions"])
                ),
                "evidence_ids": list(reversed(scenarios[0]["evidence_ids"])),
            },
        ],
        preferred_outcomes=[
            opinions[1],
            {
                **opinions[0],
                "evidence_ids": list(reversed(opinions[0]["evidence_ids"])),
            },
        ],
        risk_tags=["TAG_A", "TAG_B"],
        limitations=["LIMIT_A", "LIMIT_B"],
    )

    _, _, first_normalized = validate_review_files(
        _packet_bytes(packet), canonical_json(first).encode("utf-8")
    )
    _, _, second_normalized = validate_review_files(
        _packet_bytes(packet), canonical_json(second).encode("utf-8")
    )

    assert first_normalized == second_normalized


def test_review_rejects_duplicate_logical_opinions() -> None:
    packet = build_analysis_packet(_source(), NOW)
    opinion = {
        "market_key": "THREE_WAY",
        "outcome": "HOME_WIN",
        "strength": "MEDIUM",
        "rationale": "Same logical opinion.",
        "evidence_ids": [],
    }
    review = _review(packet, preferred_outcomes=[opinion, opinion])

    with pytest.raises(ValueError, match="preferred outcome opinions"):
        validate_review_files(
            _packet_bytes(packet), canonical_json(review).encode("utf-8")
        )


def test_packet_rejects_more_matches_than_a_review_can_cover() -> None:
    source = _source().model_dump(mode="python")
    source["matches"] = [
        {**source["matches"][0], "match_id": f"match-{index}"} for index in range(257)
    ]

    with pytest.raises(ValueError, match="256"):
        AnalysisPacketSource.model_validate(source)


def test_export_rejects_an_inconsistently_bound_stored_packet() -> None:
    packet = build_analysis_packet(_source(), NOW)

    class Repository:
        def find_analysis_packet(self, analysis_run_id: str, schema_version: str):
            assert analysis_run_id == "run-review"
            assert schema_version == "ANALYSIS_PACKET_V1"
            return StoredAnalysisPacket(
                packet_id=packet.packet_id,
                parent_analysis_run_id="another-run",
                schema_version=packet.schema_version,
                packet_hash=packet.packet_hash,
                packet_json=canonical_json(packet.model_dump(mode="json")),
            )

    with pytest.raises(ValueError, match="stored packet"):
        ExportAnalysisPacketService(Repository()).export("run-review")


def test_import_revalidates_an_existing_review_artifact() -> None:
    packet = build_analysis_packet(_source(), NOW)
    packet_json = canonical_json(packet.model_dump(mode="json"))

    class Repository:
        def load_analysis_packet(self, packet_id: str):
            assert packet_id == packet.packet_id
            return StoredAnalysisPacket(
                packet_id=packet.packet_id,
                parent_analysis_run_id=packet.analysis_run.analysis_run_id,
                schema_version=packet.schema_version,
                packet_hash=packet.packet_hash,
                packet_json=packet_json,
            )

        def save_llm_review(self, artifact):
            return artifact.model_copy(update={"raw_review_hash": "0" * 64})

    with pytest.raises(ValueError, match="integrity validation"):
        ImportLLMReviewService(Repository()).import_review(
            packet_json.encode("utf-8"),
            canonical_json(_review(packet)).encode("utf-8"),
        )


def test_import_preserves_raw_review_bytes_with_a_trailing_newline() -> None:
    packet = build_analysis_packet(_source(), NOW)
    packet_json = canonical_json(packet.model_dump(mode="json"))

    class Repository:
        def load_analysis_packet(self, packet_id: str):
            return StoredAnalysisPacket(
                packet_id=packet_id,
                parent_analysis_run_id=packet.analysis_run.analysis_run_id,
                schema_version=packet.schema_version,
                packet_hash=packet.packet_hash,
                packet_json=packet_json,
            )

        def save_llm_review(self, artifact):
            return artifact

    review_bytes = canonical_json(_review(packet)).encode("utf-8") + b"\n"
    artifact = ImportLLMReviewService(Repository()).import_review(
        packet_json.encode("utf-8"), review_bytes
    )

    assert artifact.raw_review_json.endswith("\n")
    assert artifact.raw_review_hash == hashlib.sha256(review_bytes).hexdigest()


def test_contract_requires_explicit_schema_versions() -> None:
    packet = build_analysis_packet(_source(), NOW)
    packet_without_schema = packet.model_dump(mode="json")
    packet_without_schema.pop("schema_version")
    with pytest.raises(ValueError, match="schema_version"):
        validate_review_files(
            canonical_json(packet_without_schema).encode("utf-8"),
            canonical_json(_review(packet)).encode("utf-8"),
        )

    review_without_schema = _review(packet)
    review_without_schema.pop("schema_version")
    with pytest.raises(ValueError, match="schema_version"):
        validate_review_files(
            _packet_bytes(packet),
            canonical_json(review_without_schema).encode("utf-8"),
        )


def test_contract_rejects_surrounding_whitespace_in_wire_strings() -> None:
    packet = build_analysis_packet(_source(), NOW)
    review = _review(packet)
    review["analysis_run_id"] = f"{review['analysis_run_id']} "

    with pytest.raises(ValueError, match="surrounding whitespace"):
        validate_review_files(
            _packet_bytes(packet), canonical_json(review).encode("utf-8")
        )


def test_contract_allows_domain_normalization_for_free_text() -> None:
    packet = build_analysis_packet(_source(), NOW)
    review = _review(packet, reasoning_summary="  Free text is trimmed.  ")

    _, submission, _ = validate_review_files(
        _packet_bytes(packet), canonical_json(review).encode("utf-8")
    )

    assert submission.match_reviews[0].reasoning_summary == "Free text is trimmed."


def test_contract_rejects_excessive_json_nesting_without_recursion_error() -> None:
    nested = b"[" * 129 + b"0" + b"]" * 129

    with pytest.raises(ValueError, match="nesting"):
        strict_json_loads(nested)


def test_contract_rejects_decimal_exponent_amplification() -> None:
    packet = build_analysis_packet(_source(), NOW)
    review = _review(packet, assessment_confidence="1e-1000000")
    compact = json.dumps(review, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ValueError, match="decimal precision"):
        validate_review_files(_packet_bytes(packet), compact)


def test_contract_file_size_is_checked_before_unbounded_read(tmp_path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * 1_000_001)

    with pytest.raises(ValueError, match="size limit"):
        read_contract_file(oversized)


def test_contract_writer_counts_its_trailing_newline_in_size_limit(tmp_path) -> None:
    output = tmp_path / "boundary.json"

    with pytest.raises(ValueError, match="size limit"):
        write_contract_file(output, "x" * 1_000_000)

    assert not output.exists()
