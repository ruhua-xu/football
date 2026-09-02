from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from football_system.domain.common import DomainModel, UtcDateTime

_SAFE_PROVIDER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$"
_SECRET_NAME_PARTS = (
    "accesskey",
    "apikey",
    "appkey",
    "authentication",
    "authorization",
    "authtoken",
    "clientsecret",
    "consumersecret",
    "cookie",
    "credential",
    "password",
    "passwd",
    "privatekey",
    "proxyauthorization",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessionid",
    "sessionkey",
    "setcookie",
    "signature",
    "signingkey",
    "subscriptionkey",
    "token",
    "xauth",
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?ix)(?:"
    r"\b(?:api[-_ ]?key|authorization|bearer|client[-_ ]?secret|password|"
    r"private[-_ ]?key|secret|token)\b"
    r"|-----BEGIN[ A-Z]*PRIVATE\ KEY-----"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bsk_(?:live|test)_[A-Za-z0-9]{12,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{12,}\b"
    r")"
)
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"
)
_REDACTION_MARKERS = frozenset({"***", "[redacted]", "<redacted>"})
_MAX_PARAMETER_DEPTH = 16


class ProviderRequestOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class ProviderRequestFailureCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    HTTP_CLIENT_ERROR = "HTTP_CLIENT_ERROR"
    HTTP_SERVER_ERROR = "HTTP_SERVER_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"


class ProviderRequestAudit(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    provider: str = Field(
        min_length=1,
        max_length=160,
        pattern=_SAFE_PROVIDER_PATTERN,
    )
    endpoint: str = Field(min_length=1, max_length=2048)
    requested_at_utc: UtcDateTime
    received_at_utc: UtcDateTime
    available_at_utc: UtcDateTime | None
    request_parameters: dict[str, Any] = Field(default_factory=dict)
    http_status: int | None = Field(default=None, ge=100, le=599, strict=True)
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=512)
    duration_ms: int = Field(ge=0, strict=True)
    outcome: ProviderRequestOutcome
    failure_code: ProviderRequestFailureCode | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if any(character in value for character in ("\r", "\n", "\0")):
            raise ValueError("endpoint contains control characters")
        try:
            parsed = urlsplit(value)
            query = parse_qsl(parsed.query, keep_blank_values=True)
        except ValueError:
            raise ValueError("endpoint is not structurally valid") from None
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("endpoint cannot contain user credentials")
        if parsed.fragment:
            raise ValueError("endpoint cannot contain a fragment")
        if any(
            is_secret_like_parameter_name(name)
            or is_secret_like_parameter_value(parameter_value)
            for name, parameter_value in query
        ):
            raise ValueError("endpoint cannot contain secret-like query data")
        return value

    @field_validator("request_parameters", mode="before")
    @classmethod
    def validate_request_parameters(cls, value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("request_parameters must be an object")
        return _validated_parameters(value, depth=0)

    @field_validator("provider_request_id")
    @classmethod
    def validate_provider_request_id(cls, value: str | None) -> str | None:
        if value is not None and any(
            character in value for character in ("\r", "\n", "\0")
        ):
            raise ValueError("provider_request_id contains control characters")
        if value is not None and is_secret_like_parameter_value(value):
            raise ValueError("provider_request_id cannot contain secret-like data")
        return value

    @model_validator(mode="after")
    def validate_request_lifecycle(self) -> Self:
        if self.requested_at_utc > self.received_at_utc:
            raise ValueError("requested_at_utc cannot be after received_at_utc")
        if (
            self.available_at_utc is not None
            and self.available_at_utc > self.received_at_utc
        ):
            raise ValueError("available_at_utc cannot be after received_at_utc")

        if self.outcome is ProviderRequestOutcome.SUCCESS:
            if self.http_status is None or not 200 <= self.http_status <= 299:
                raise ValueError("successful requests require a 2xx http_status")
            if self.failure_code is not None:
                raise ValueError("successful requests cannot have a failure_code")
            if self.available_at_utc is None:
                raise ValueError("successful requests require available_at_utc")
            return self

        if self.failure_code is None:
            raise ValueError("failed requests require a failure_code")
        if self.http_status is not None and 200 <= self.http_status <= 299:
            raise ValueError("failed requests cannot have a 2xx http_status")
        if (
            self.failure_code
            in {
                ProviderRequestFailureCode.TIMEOUT,
                ProviderRequestFailureCode.NETWORK_ERROR,
            }
            and self.http_status is not None
        ):
            raise ValueError("network failures cannot have an http_status")
        if (
            self.failure_code is ProviderRequestFailureCode.RATE_LIMITED
            and self.http_status != 429
        ):
            raise ValueError("RATE_LIMITED requires http_status 429")
        if self.failure_code is ProviderRequestFailureCode.HTTP_CLIENT_ERROR and (
            self.http_status is None
            or not 400 <= self.http_status <= 499
            or self.http_status == 429
        ):
            raise ValueError("HTTP_CLIENT_ERROR requires a non-429 4xx status")
        if self.failure_code is ProviderRequestFailureCode.HTTP_SERVER_ERROR and (
            self.http_status is None or not 500 <= self.http_status <= 599
        ):
            raise ValueError("HTTP_SERVER_ERROR requires a 5xx status")
        if (
            self.failure_code is ProviderRequestFailureCode.HTTP_ERROR
            and self.http_status is None
        ):
            raise ValueError("HTTP_ERROR requires an http_status")
        return self


class RawArtifactMetadata(ProviderRequestAudit):
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProviderRequestResult(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    audit: ProviderRequestAudit
    payload: bytes | None = Field(default=None, strict=True, repr=False)

    @model_validator(mode="after")
    def validate_payload_consistency(self) -> Self:
        succeeded = self.audit.outcome is ProviderRequestOutcome.SUCCESS
        if succeeded and self.payload is None:
            raise ValueError("successful provider requests require a payload")
        if not succeeded and self.payload is not None:
            raise ValueError("failed provider requests cannot expose payload data")
        return self

    @property
    def succeeded(self) -> bool:
        return self.audit.outcome is ProviderRequestOutcome.SUCCESS

    def to_raw_artifact_metadata(self) -> RawArtifactMetadata:
        if self.payload is None:
            raise ValueError("failed provider requests have no raw artifact payload")
        return RawArtifactMetadata.model_validate(
            {
                **self.audit.model_dump(mode="python"),
                "payload_sha256": hashlib.sha256(self.payload).hexdigest(),
            }
        )


def is_secret_like_parameter_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return normalized in {"auth", "key", "secret", "session", "sig"} or any(
        part in normalized for part in _SECRET_NAME_PARTS
    )


def is_secret_like_parameter_value(value: str) -> bool:
    stripped = value.strip()
    if stripped.casefold() in _REDACTION_MARKERS:
        return False
    if _SECRET_VALUE_PATTERN.search(stripped) or _JWT_PATTERN.search(stripped):
        return True
    try:
        parsed = urlsplit(stripped)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return True
    return (
        parsed.username is not None
        or parsed.password is not None
        or any(
            is_secret_like_parameter_name(name)
            or _SECRET_VALUE_PATTERN.search(parameter_value)
            for name, parameter_value in query
        )
    )


def _validated_parameters(
    value: Mapping[object, object],
    *,
    depth: int,
) -> dict[str, Any]:
    if depth > _MAX_PARAMETER_DEPTH:
        raise ValueError("request_parameters exceed the nesting limit")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 256:
            raise ValueError("request parameter names must be bounded nonempty strings")
        if any(character in key for character in ("\r", "\n", "\0")):
            raise ValueError("request parameter names contain control characters")
        if is_secret_like_parameter_name(key):
            raise ValueError("request_parameters contain a secret-like name")
        result[key] = _validated_parameter_value(item, depth=depth + 1)
    return result


def _validated_parameter_value(value: object, *, depth: int) -> object:
    if depth > _MAX_PARAMETER_DEPTH:
        raise ValueError("request_parameters exceed the nesting limit")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("request parameter numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("request parameter values exceed the size limit")
        if is_secret_like_parameter_value(value):
            raise ValueError("request_parameters contain a secret-like value")
        return value
    if isinstance(value, Mapping):
        return _validated_parameters(value, depth=depth)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(
            _validated_parameter_value(item, depth=depth + 1) for item in value
        )
    raise ValueError("request parameter values must be JSON-compatible")


ProviderFailureCode = ProviderRequestFailureCode
