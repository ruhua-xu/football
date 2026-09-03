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
    AnalysisPacketContract,
    AnalysisPacketMatchV2,
    AnalysisPacketMatchV3,
    AnalysisPacketSource,
    AnalysisPacketSourceV2,
    AnalysisPacketSourceV3,
    AnalysisPacketV2,
    AnalysisPacketV3,
    LLMReviewArtifact,
    LLMReviewFailureCode,
    LLMReviewSubmission,
    LLMReviewSubmissionContract,
    LLMReviewSubmissionV2,
    LLMReviewSubmissionV3,
    MAX_CONTRACT_FILE_BYTES,
    PacketModelQuantLineageV3,
    StoredAnalysisPacket,
    UnavailableLLMMatchReview,
    ValidLLMMatchReview,
)

VALIDATOR_VERSION = "OFFLINE_REVIEW_VALIDATOR_V1"
VALIDATOR_VERSION_V2 = "OFFLINE_REVIEW_VALIDATOR_V2"
VALIDATOR_VERSION_V3 = "OFFLINE_REVIEW_VALIDATOR_V3"
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
V3_EXACT_WIRE_FIELDS = {
    "source_kind",
    "model_name",
    "model_version",
    "calibration_label",
    "method",
    "method_version",
    "unavailable_reason",
    "handicap_value",
}


class ExportAnalysisPacketService:
    def __init__(self, repository: ReviewArtifactRepository) -> None:
        self._repository = repository

    def export(
        self,
        analysis_run_id: str,
        schema_version: str = "ANALYSIS_PACKET_V1",
    ) -> tuple[AnalysisPacketContract, str]:
        if schema_version not in {
            "ANALYSIS_PACKET_V1",
            "ANALYSIS_PACKET_V2",
            "ANALYSIS_PACKET_V3",
        }:
            raise ValueError(f"unsupported AnalysisPacket schema: {schema_version}")
        stored = self._repository.find_analysis_packet(
            analysis_run_id,
            schema_version,
        )
        if stored is not None:
            packet = _parse_analysis_packet(stored.packet_json.encode("utf-8"))
            _validate_stored_packet(stored, packet)
            return packet, stored.packet_json
        if schema_version == "ANALYSIS_PACKET_V1":
            source = self._repository.load_packet_source(analysis_run_id)
            packet = build_analysis_packet(source, utc_now())
        elif schema_version == "ANALYSIS_PACKET_V2":
            source_v2 = self._repository.load_packet_source_v2(analysis_run_id)
            packet = build_analysis_packet_v2(source_v2, utc_now())
        else:
            source_v3 = self._repository.load_packet_source_v3(analysis_run_id)
            packet = build_analysis_packet_v3(source_v3, utc_now())
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
        validator_version = _validator_version(packet.schema_version)
        artifact = LLMReviewArtifact(
            review_artifact_id=stable_id(
                "llm-review-artifact",
                packet.packet_id,
                normalized_hash,
                validator_version,
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
            validator_version=validator_version,
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


def build_analysis_packet_v2(
    source: AnalysisPacketSourceV2,
    generated_at_utc: datetime,
) -> AnalysisPacketV2:
    run = source.analysis_run
    matches: list[AnalysisPacketMatchV2] = []
    for source_match in sorted(source.matches, key=lambda item: item.match_id):
        context = source_match.review_context.model_copy(
            update={
                "evidence": tuple(
                    sorted(
                        source_match.review_context.evidence,
                        key=lambda item: item.evidence_id,
                    )
                ),
                "data_quality": source_match.review_context.data_quality.model_copy(
                    update={
                        "available_fields": tuple(
                            sorted(
                                source_match.review_context.data_quality.available_fields
                            )
                        ),
                        "missing_fields": tuple(
                            sorted(
                                source_match.review_context.data_quality.missing_fields
                            )
                        ),
                        "notes": tuple(
                            sorted(source_match.review_context.data_quality.notes)
                        ),
                    }
                ),
            }
        )
        context_hash = sha256_text(canonical_json(context.model_dump(mode="json")))
        context_id = stable_id(
            "match-review-context",
            run.analysis_run_id,
            source_match.match_id,
            context_hash,
        )
        match_payload = source_match.model_dump(
            mode="python",
            exclude={"review_context", "evidence_ids"},
        )
        matches.append(
            AnalysisPacketMatchV2(
                **match_payload,
                evidence_ids=tuple(item.evidence_id for item in context.evidence),
                review_context=context,
                review_context_id=context_id,
                review_context_hash=context_hash,
            )
        )
    packet_id = stable_id(
        "analysis-packet",
        run.analysis_run_id,
        "ANALYSIS_PACKET_V2",
        run.input_manifest_hash,
        run.code_revision,
    )
    without_hash = {
        "schema_version": "ANALYSIS_PACKET_V2",
        "packet_id": packet_id,
        "generated_at_utc": _utc_json(generated_at_utc),
        "analysis_run": run.model_dump(mode="json"),
        "matches": [match.model_dump(mode="json") for match in matches],
    }
    return AnalysisPacketV2(
        **without_hash,
        packet_hash=sha256_text(canonical_json(without_hash)),
    )


def build_analysis_packet_v3(
    source: AnalysisPacketSourceV3,
    generated_at_utc: datetime,
) -> AnalysisPacketV3:
    run = source.analysis_run
    matches: list[AnalysisPacketMatchV3] = []
    for source_match in sorted(source.matches, key=lambda item: item.match_id):
        context = source_match.review_context.model_copy(
            update={
                "evidence": tuple(
                    sorted(
                        source_match.review_context.evidence,
                        key=lambda item: item.evidence_id,
                    )
                ),
                "data_quality": source_match.review_context.data_quality.model_copy(
                    update={
                        "available_fields": tuple(
                            sorted(
                                source_match.review_context.data_quality.available_fields
                            )
                        ),
                        "missing_fields": tuple(
                            sorted(
                                source_match.review_context.data_quality.missing_fields
                            )
                        ),
                        "notes": tuple(
                            sorted(source_match.review_context.data_quality.notes)
                        ),
                    }
                ),
            }
        )
        context_hash = sha256_text(canonical_json(context.model_dump(mode="json")))
        context_id = stable_id(
            "match-review-context",
            run.analysis_run_id,
            source_match.match_id,
            context_hash,
        )
        match_payload = source_match.model_dump(
            mode="python",
            exclude={"review_context", "evidence_ids"},
        )
        matches.append(
            AnalysisPacketMatchV3(
                **match_payload,
                evidence_ids=tuple(item.evidence_id for item in context.evidence),
                review_context=context,
                review_context_id=context_id,
                review_context_hash=context_hash,
            )
        )
    model_states = tuple(
        sorted(source.quant_model_states, key=lambda item: item.quant_model_state_id)
    )
    packet_id = stable_id(
        "analysis-packet",
        run.analysis_run_id,
        "ANALYSIS_PACKET_V3",
        run.input_manifest_hash,
        run.code_revision,
    )
    without_hash = {
        "schema_version": "ANALYSIS_PACKET_V3",
        "packet_id": packet_id,
        "generated_at_utc": _utc_json(generated_at_utc),
        "analysis_run": run.model_dump(mode="json"),
        "quant_model_states": [state.model_dump(mode="json") for state in model_states],
        "matches": [match.model_dump(mode="json") for match in matches],
    }
    return AnalysisPacketV3(
        **without_hash,
        packet_hash=sha256_text(canonical_json(without_hash)),
    )


def validate_review_files(
    packet_bytes: bytes,
    review_bytes: bytes,
) -> tuple[AnalysisPacketContract, LLMReviewSubmissionContract, str]:
    try:
        packet = _parse_analysis_packet(packet_bytes)
        review_payload = strict_json_loads(review_bytes)
        if packet.schema_version == "ANALYSIS_PACKET_V1":
            submission = LLMReviewSubmission.model_validate(review_payload)
        elif packet.schema_version == "ANALYSIS_PACKET_V2":
            submission = LLMReviewSubmissionV2.model_validate(review_payload)
        else:
            submission = LLMReviewSubmissionV3.model_validate(review_payload)
    except ValidationError as error:
        raise ValueError(f"file contract validation failed: {error}") from error
    validate_review_binding(packet, submission)
    normalized = canonical_json(_normalized_review_payload(submission))
    _validate_contract_size(normalized)
    return packet, submission, normalized


def validate_review_binding(
    packet: AnalysisPacketContract,
    submission: LLMReviewSubmissionContract,
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
        if isinstance(packet, (AnalysisPacketV2, AnalysisPacketV3)):
            if (
                review.review_context_id != packet_match.review_context_id
                or review.review_context_hash != packet_match.review_context_hash
            ):
                raise ValueError(f"LLM review context mismatch for {match_id}")
        if (
            isinstance(packet, AnalysisPacketV3)
            and isinstance(packet_match.p_quant, PacketModelQuantLineageV3)
            and packet_match.p_quant.status.value == "UNAVAILABLE"
            and (
                not isinstance(review, UnavailableLLMMatchReview)
                or review.failure_code is not LLMReviewFailureCode.MODEL_UNAVAILABLE
            )
        ):
            raise ValueError(
                f"LLM review must preserve model unavailability for {match_id}"
            )
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
                raise ValueError(
                    f"LLM review references unknown evidence for {match_id}"
                )


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


def _validate_packet_hash(packet: AnalysisPacketContract) -> None:
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
    packet: AnalysisPacketContract,
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
    packet: AnalysisPacketContract,
) -> None:
    raw_review_bytes = artifact.raw_review_json.encode("utf-8")
    normalized_hash = sha256_text(artifact.normalized_review_json)
    validator_version = _validator_version(packet.schema_version)
    review_schema_version = _review_schema_version(packet.schema_version)
    expected_id = stable_id(
        "llm-review-artifact",
        packet.packet_id,
        normalized_hash,
        validator_version,
    )
    if (
        artifact.review_artifact_id != expected_id
        or artifact.parent_analysis_run_id != packet.analysis_run.analysis_run_id
        or artifact.packet_id != packet.packet_id
        or artifact.packet_hash != packet.packet_hash
        or artifact.review_schema_version != review_schema_version
        or artifact.validator_version != validator_version
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
        raise ValueError(
            "stored LLM review artifact failed contract validation"
        ) from error
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


def _validate_wire_strings(
    value: object,
    *,
    exact_fields: set[str] | None = None,
) -> None:
    required_exact_fields = EXACT_WIRE_FIELDS if exact_fields is None else exact_fields
    pending: list[tuple[str | None, object, int]] = [(None, value, 0)]
    while pending:
        field, item, depth = pending.pop()
        if depth > MAX_JSON_NESTING:
            raise ValueError("JSON nesting exceeds the contract limit")
        if isinstance(item, str):
            requires_exact_value = field is not None and (
                field in required_exact_fields
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
                    raise ValueError(
                        "JSON object keys cannot have surrounding whitespace"
                    )
                pending.append((key, child, depth + 1))
        elif isinstance(item, list):
            pending.extend((field, child, depth + 1) for child in item)


def _parse_analysis_packet(data: bytes) -> AnalysisPacketContract:
    payload = strict_json_loads(data)
    if not isinstance(payload, dict):
        raise ValueError("AnalysisPacket root must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version is None:
        raise ValueError("AnalysisPacket requires schema_version")
    if schema_version == "ANALYSIS_PACKET_V3":
        _validate_wire_strings(
            payload,
            exact_fields=EXACT_WIRE_FIELDS | V3_EXACT_WIRE_FIELDS,
        )
    if schema_version == "ANALYSIS_PACKET_V1":
        packet = AnalysisPacket.model_validate(payload)
    elif schema_version == "ANALYSIS_PACKET_V2":
        packet = AnalysisPacketV2.model_validate(payload)
    elif schema_version == "ANALYSIS_PACKET_V3":
        packet = AnalysisPacketV3.model_validate(payload)
    else:
        raise ValueError(f"unsupported AnalysisPacket schema: {schema_version}")
    _validate_packet_hash(packet)
    return packet


def _normalized_review_payload(
    submission: LLMReviewSubmissionContract,
) -> dict[str, object]:
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


def _validator_version(packet_schema_version: str) -> str:
    if packet_schema_version == "ANALYSIS_PACKET_V1":
        return VALIDATOR_VERSION
    if packet_schema_version == "ANALYSIS_PACKET_V2":
        return VALIDATOR_VERSION_V2
    if packet_schema_version == "ANALYSIS_PACKET_V3":
        return VALIDATOR_VERSION_V3
    raise ValueError(f"unsupported AnalysisPacket schema: {packet_schema_version}")


def _review_schema_version(packet_schema_version: str) -> str:
    if packet_schema_version == "ANALYSIS_PACKET_V1":
        return "LLM_REVIEW_V1"
    if packet_schema_version == "ANALYSIS_PACKET_V2":
        return "LLM_REVIEW_V2"
    if packet_schema_version == "ANALYSIS_PACKET_V3":
        return "LLM_REVIEW_V3"
    raise ValueError(f"unsupported AnalysisPacket schema: {packet_schema_version}")
