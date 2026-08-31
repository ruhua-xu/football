from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ValidationError

from football_system.application.ports.review_artifacts import (
    ReviewArtifactRepository,
)
from football_system.domain.common import stable_id, utc_now
from football_system.domain.review import (
    AnalysisPacket,
    AnalysisPacketSource,
    LLMReviewArtifact,
    LLMReviewSubmission,
    MAX_CONTRACT_FILE_BYTES,
    StoredAnalysisPacket,
    ValidLLMMatchReview,
)

VALIDATOR_VERSION = "OFFLINE_REVIEW_VALIDATOR_V1"
MAX_JSON_NESTING = 128
EXACT_WIRE_FIELDS = {
    "schema_version",
    "status",
    "market_key",
    "outcome",
    "fails_outcome",
    "scenario_type",
    "strength",
    "failure_code",
    "pipeline_version",
    "code_revision",
    "input_manifest_version",
    "risk_tags",
    "home_win",
    "draw",
    "away_win",
    "assessment_confidence",
    "as_of_at_utc",
    "completed_at_utc",
    "generated_at_utc",
    "kickoff_at_utc",
}


class ExportAnalysisPacketService:
    def __init__(self, repository: ReviewArtifactRepository) -> None:
        self._repository = repository

    def export(self, analysis_run_id: str) -> tuple[AnalysisPacket, str]:
        stored = self._repository.find_analysis_packet(
            analysis_run_id,
            "ANALYSIS_PACKET_V1",
        )
        if stored is not None:
            packet = _parse_analysis_packet(stored.packet_json.encode("utf-8"))
            _validate_stored_packet(stored, packet)
            return packet, stored.packet_json
        source = self._repository.load_packet_source(analysis_run_id)
        packet = build_analysis_packet(source, utc_now())
        packet_json = canonical_json(packet.model_dump(mode="json"))
        _validate_contract_size(packet_json, include_trailing_newline=True)
        stored = self._repository.save_analysis_packet(packet, packet_json)
        stored_packet = _parse_analysis_packet(stored.packet_json.encode("utf-8"))
        _validate_stored_packet(stored, stored_packet)
        return stored_packet, stored.packet_json


class ImportLLMReviewService:
    def __init__(self, repository: ReviewArtifactRepository) -> None:
        self._repository = repository

    def import_review(
        self,
        packet_bytes: bytes,
        review_bytes: bytes,
    ) -> LLMReviewArtifact:
        packet, submission, normalized_review_json = validate_review_files(
            packet_bytes,
            review_bytes,
        )
        stored = self._repository.load_analysis_packet(packet.packet_id)
        _validate_stored_packet(stored, packet)
        normalized_hash = sha256_text(normalized_review_json)
        artifact = LLMReviewArtifact(
            review_artifact_id=stable_id(
                "llm-review-artifact",
                packet.packet_id,
                normalized_hash,
                VALIDATOR_VERSION,
            ),
            parent_analysis_run_id=packet.analysis_run.analysis_run_id,
            packet_id=packet.packet_id,
            packet_hash=packet.packet_hash,
            review_schema_version=submission.schema_version,
            imported_at_utc=utc_now(),
            raw_review_json=_decode_json_bytes(review_bytes),
            raw_review_hash=hashlib.sha256(review_bytes).hexdigest(),
            normalized_review_json=normalized_review_json,
            normalized_review_hash=normalized_hash,
        )
        stored_artifact = self._repository.save_llm_review(artifact)
        _validate_stored_review_artifact(stored_artifact, packet)
        return stored_artifact


def build_analysis_packet(
    source: AnalysisPacketSource,
    generated_at_utc: datetime,
) -> AnalysisPacket:
    run = source.analysis_run
    matches = tuple(sorted(source.matches, key=lambda item: item.match_id))
    packet_id = stable_id(
        "analysis-packet",
        run.analysis_run_id,
        "ANALYSIS_PACKET_V1",
        run.input_manifest_hash,
        run.code_revision,
    )
    run_json = run.model_dump(mode="json")
    without_hash = {
        "schema_version": "ANALYSIS_PACKET_V1",
        "packet_id": packet_id,
        "generated_at_utc": _utc_json(generated_at_utc),
        "analysis_run": run_json,
        "matches": [match.model_dump(mode="json") for match in matches],
    }
    return AnalysisPacket(
        **without_hash,
        packet_hash=sha256_text(canonical_json(without_hash)),
    )


def validate_review_files(
    packet_bytes: bytes,
    review_bytes: bytes,
) -> tuple[AnalysisPacket, LLMReviewSubmission, str]:
    try:
        packet = _parse_analysis_packet(packet_bytes)
        submission = LLMReviewSubmission.model_validate(
            strict_json_loads(review_bytes)
        )
    except ValidationError as error:
        raise ValueError(f"file contract validation failed: {error}") from error
    validate_review_binding(packet, submission)
    normalized = canonical_json(_normalized_review_payload(submission))
    _validate_contract_size(normalized)
    return packet, submission, normalized


def validate_review_binding(
    packet: AnalysisPacket,
    submission: LLMReviewSubmission,
) -> None:
    if (
        submission.analysis_run_id != packet.analysis_run.analysis_run_id
        or submission.packet_id != packet.packet_id
        or submission.packet_hash != packet.packet_hash
    ):
        raise ValueError("LLM review does not match the AnalysisPacket binding")
    packet_matches = {match.match_id: match for match in packet.matches}
    reviews = {review.match_id: review for review in submission.match_reviews}
    if set(reviews) != set(packet_matches):
        raise ValueError("LLM review must cover the exact AnalysisPacket match set")
    for match_id, review in reviews.items():
        packet_match = packet_matches[match_id]
        if review.market_key != packet_match.market_key:
            raise ValueError(f"LLM review market mismatch for {match_id}")
        if isinstance(review, ValidLLMMatchReview):
            nested_markets = {
                item.market_key
                for item in (
                    *review.scenarios,
                    *review.preferred_outcomes,
                    *review.avoid_outcomes,
                )
            }
            if nested_markets - {packet_match.market_key}:
                raise ValueError(f"LLM review nested market mismatch for {match_id}")
            evidence_ids = {
                evidence_id
                for item in (
                    *review.scenarios,
                    *review.preferred_outcomes,
                    *review.avoid_outcomes,
                    *review.counter_scenarios,
                )
                for evidence_id in item.evidence_ids
            }
            if evidence_ids - set(packet_match.evidence_ids):
                raise ValueError(f"LLM review references unknown evidence for {match_id}")


def strict_json_loads(data: bytes) -> object:
    if not data or len(data) > MAX_CONTRACT_FILE_BYTES:
        raise ValueError("JSON file is empty or exceeds the size limit")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is forbidden: {value}")

    try:
        value = json.loads(
            _decode_json_bytes(data),
            parse_float=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error.msg}") from error
    except RecursionError as error:
        raise ValueError("JSON nesting exceeds the contract limit") from error
    _validate_wire_strings(value)
    return value


def canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_packet_hash(packet: AnalysisPacket) -> None:
    payload = packet.model_dump(mode="json", exclude={"packet_hash"})
    expected_hash = sha256_text(canonical_json(payload))
    if packet.packet_hash != expected_hash:
        raise ValueError("AnalysisPacket hash verification failed")
    expected_id = stable_id(
        "analysis-packet",
        packet.analysis_run.analysis_run_id,
        packet.schema_version,
        packet.analysis_run.input_manifest_hash,
        packet.analysis_run.code_revision,
    )
    if packet.packet_id != expected_id:
        raise ValueError("AnalysisPacket identity verification failed")


def _validate_stored_packet(
    stored: StoredAnalysisPacket,
    packet: AnalysisPacket,
) -> None:
    if (
        stored.packet_id != packet.packet_id
        or stored.parent_analysis_run_id != packet.analysis_run.analysis_run_id
        or stored.schema_version != packet.schema_version
        or stored.packet_hash != packet.packet_hash
        or stored.packet_json != canonical_json(packet.model_dump(mode="json"))
    ):
        raise ValueError("supplied AnalysisPacket does not match the stored packet")


def _validate_stored_review_artifact(
    artifact: LLMReviewArtifact,
    packet: AnalysisPacket,
) -> None:
    raw_review_bytes = artifact.raw_review_json.encode("utf-8")
    normalized_hash = sha256_text(artifact.normalized_review_json)
    expected_id = stable_id(
        "llm-review-artifact",
        packet.packet_id,
        normalized_hash,
        VALIDATOR_VERSION,
    )
    if (
        artifact.review_artifact_id != expected_id
        or artifact.parent_analysis_run_id != packet.analysis_run.analysis_run_id
        or artifact.packet_id != packet.packet_id
        or artifact.packet_hash != packet.packet_hash
        or artifact.review_schema_version != "LLM_REVIEW_V1"
        or artifact.validator_version != VALIDATOR_VERSION
        or artifact.raw_review_hash != hashlib.sha256(raw_review_bytes).hexdigest()
        or artifact.normalized_review_hash != normalized_hash
    ):
        raise ValueError("stored LLM review artifact failed integrity validation")
    try:
        _, submission, normalized = validate_review_files(
            canonical_json(packet.model_dump(mode="json")).encode("utf-8"),
            raw_review_bytes,
        )
    except ValueError as error:
        raise ValueError("stored LLM review artifact failed contract validation") from error
    if (
        submission.schema_version != artifact.review_schema_version
        or normalized != artifact.normalized_review_json
    ):
        raise ValueError("stored LLM review artifact failed normalization validation")


def _decode_json_bytes(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("JSON files must use UTF-8") from error
    if text.startswith("\ufeff"):
        raise ValueError("UTF-8 BOM is not allowed")
    return text


def _validate_wire_strings(value: object) -> None:
    pending: list[tuple[str | None, object, int]] = [(None, value, 0)]
    while pending:
        field, item, depth = pending.pop()
        if depth > MAX_JSON_NESTING:
            raise ValueError("JSON nesting exceeds the contract limit")
        if isinstance(item, str):
            requires_exact_value = field is not None and (
                field in EXACT_WIRE_FIELDS
                or field.endswith("_id")
                or field.endswith("_ids")
                or field.endswith("_hash")
            )
            if requires_exact_value and item != item.strip():
                raise ValueError(
                    f"JSON field {field} cannot have surrounding whitespace"
                )
        elif isinstance(item, dict):
            for key, child in item.items():
                if key != key.strip():
                    raise ValueError("JSON object keys cannot have surrounding whitespace")
                pending.append((key, child, depth + 1))
        elif isinstance(item, list):
            pending.extend((field, child, depth + 1) for child in item)


def _parse_analysis_packet(data: bytes) -> AnalysisPacket:
    packet = AnalysisPacket.model_validate(strict_json_loads(data))
    _validate_packet_hash(packet)
    return packet


def _normalized_review_payload(submission: LLMReviewSubmission) -> dict[str, object]:
    payload = submission.model_dump(mode="python", exclude={"match_reviews"})
    reviews = []
    for review in sorted(submission.match_reviews, key=lambda item: item.match_id):
        item = review.model_dump(mode="python")
        for scenario in item.get("scenarios", ()):
            scenario["evidence_ids"] = sorted(scenario["evidence_ids"])
            scenario["trigger_conditions"] = sorted(scenario["trigger_conditions"])
        for field in ("preferred_outcomes", "avoid_outcomes"):
            for opinion in item.get(field, ()):
                opinion["evidence_ids"] = sorted(opinion["evidence_ids"])
        for counter in item.get("counter_scenarios", ()):
            counter["evidence_ids"] = sorted(counter["evidence_ids"])
        for field in (
            "scenarios",
            "preferred_outcomes",
            "avoid_outcomes",
            "counter_scenarios",
        ):
            if field in item:
                item[field] = sorted(item[field], key=canonical_json)
        for field in ("risk_tags", "limitations"):
            if field in item:
                item[field] = sorted(item[field])
        reviews.append(item)
    payload["match_reviews"] = reviews
    return payload


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal cannot be serialized")
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return _utc_json(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _utc_json(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("JSON datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_contract_size(
    content: str,
    *,
    include_trailing_newline: bool = False,
) -> None:
    serialized = content + ("\n" if include_trailing_newline else "")
    if len(serialized.encode("utf-8")) > MAX_CONTRACT_FILE_BYTES:
        raise ValueError("contract JSON exceeds the size limit")
