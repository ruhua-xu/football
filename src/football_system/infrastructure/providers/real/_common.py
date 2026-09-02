from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from football_system.domain.raw_data import (
    ProviderRequestFailureCode,
    ProviderRequestResult,
)
from football_system.infrastructure.files.raw_archive import (
    ArchivedRawArtifact,
    RawDataArchive,
)


class ProviderAdapterError(RuntimeError):
    code = "PROVIDER_ADAPTER_ERROR"

    def __init__(self, provider_code: str, detail: str) -> None:
        self.provider_code = provider_code
        super().__init__(f"{self.code}: {provider_code} {detail}")


class ProviderRequestError(ProviderAdapterError):
    code = "PROVIDER_REQUEST_FAILED"

    def __init__(
        self,
        provider_code: str,
        failure_code: ProviderRequestFailureCode | None,
        http_status: int | None,
    ) -> None:
        self.failure_code = failure_code
        self.http_status = http_status
        detail = "request failed"
        if failure_code is not None:
            detail = f"request failed with {failure_code.value}"
        if http_status is not None:
            detail = f"{detail} (HTTP {http_status})"
        super().__init__(provider_code, detail)


class ProviderPayloadError(ProviderAdapterError):
    code = "PROVIDER_PAYLOAD_INVALID"

    def __init__(self, provider_code: str, detail: str = "payload is invalid") -> None:
        super().__init__(provider_code, detail)


class ProviderArchiveError(ProviderAdapterError):
    code = "PROVIDER_RAW_ARCHIVE_FAILED"

    def __init__(self, provider_code: str) -> None:
        super().__init__(provider_code, "raw response could not be archived")


def archive_successful_response(
    provider_code: str,
    result: ProviderRequestResult,
    raw_archive: RawDataArchive,
) -> ArchivedRawArtifact:
    if not result.succeeded or result.payload is None:
        raise ProviderRequestError(
            provider_code,
            result.audit.failure_code,
            result.audit.http_status,
        )
    try:
        return raw_archive.write(result.payload, result.to_raw_artifact_metadata())
    except Exception:
        raise ProviderArchiveError(provider_code) from None


def decode_json_payload(provider_code: str, payload: bytes) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ProviderPayloadError(
            provider_code, "payload is not valid UTF-8 JSON"
        ) from None


def parse_utc_timestamp(
    provider_code: str,
    value: object,
    *,
    field: str,
) -> datetime:
    if not isinstance(value, str) or not value:
        raise ProviderPayloadError(
            provider_code, f"{field} must be an ISO-8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderPayloadError(
            provider_code, f"{field} must be an ISO-8601 timestamp"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderPayloadError(provider_code, f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON number")
