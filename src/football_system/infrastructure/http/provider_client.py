from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from football_system.domain.raw_data import (
    ProviderRequestAudit,
    ProviderRequestFailureCode,
    ProviderRequestOutcome,
    ProviderRequestResult,
    is_secret_like_parameter_name,
    is_secret_like_parameter_value,
)

MAX_TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 8
MAX_DELAY_SECONDS = 300.0
_METHOD_PATTERN = re.compile(r"^[A-Z]+$")
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-correlation-id",
    "cf-ray",
)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        method = self.method.upper()
        if not _METHOD_PATTERN.fullmatch(method):
            raise ValueError("HTTP method must contain only ASCII letters")
        if not isinstance(self.url, str) or not self.url:
            raise ValueError("HTTP request URL must be a nonempty string")
        if self.body is not None and not isinstance(self.body, bytes):
            raise TypeError("HTTP request body must be bytes")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP response status_code must be between 100 and 599")
        if not isinstance(self.body, bytes):
            raise TypeError("HTTP response body must be bytes")
        normalized_headers: dict[str, str] = {}
        for name, value in self.headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("HTTP response headers must contain strings")
            normalized_headers[name] = value
        object.__setattr__(self, "headers", MappingProxyType(normalized_headers))


class HttpTransport(Protocol):
    def send(
        self,
        request: HttpRequest,
        *,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class ProviderHttpClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        transport: HttpTransport,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.25,
        max_backoff_seconds: float = 10.0,
        max_retry_after_seconds: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        utc_now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._base_url = _validated_base_url(base_url)
        self._transport = transport
        self._timeout_seconds = _bounded_float(
            "timeout_seconds",
            timeout_seconds,
            minimum_exclusive=0.0,
            maximum=MAX_TIMEOUT_SECONDS,
        )
        if type(max_retries) is not int or not 0 <= max_retries <= MAX_RETRIES:
            raise ValueError(f"max_retries must be between 0 and {MAX_RETRIES}")
        self._max_retries = max_retries
        self._backoff_base_seconds = _bounded_float(
            "backoff_base_seconds",
            backoff_base_seconds,
            minimum=0.0,
            maximum=MAX_DELAY_SECONDS,
        )
        self._max_backoff_seconds = _bounded_float(
            "max_backoff_seconds",
            max_backoff_seconds,
            minimum=0.0,
            maximum=MAX_DELAY_SECONDS,
        )
        self._max_retry_after_seconds = _bounded_float(
            "max_retry_after_seconds",
            max_retry_after_seconds,
            minimum=0.0,
            maximum=MAX_DELAY_SECONDS,
        )
        self._sleep = sleep
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic

    def get(
        self,
        endpoint: str,
        *,
        query_parameters: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        available_at_utc: datetime | None = None,
    ) -> ProviderRequestResult:
        return self.request(
            "GET",
            endpoint,
            query_parameters=query_parameters,
            headers=headers,
            available_at_utc=available_at_utc,
        )

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        query_parameters: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        available_at_utc: datetime | None = None,
    ) -> ProviderRequestResult:
        request, audit_endpoint, sanitized_parameters = self._prepare_request(
            method,
            endpoint,
            query_parameters,
            headers,
            body,
        )
        requested_at = _clock_utc(self._utc_now)
        started_at = self._monotonic()
        if not math.isfinite(started_at):
            raise ValueError("monotonic clock must return a finite value")

        for attempt_index in range(self._max_retries + 1):
            try:
                response = self._transport.send(
                    request,
                    timeout_seconds=self._timeout_seconds,
                )
            except TimeoutError:
                received_at = _clock_utc(self._utc_now)
                if attempt_index < self._max_retries:
                    self._sleep(self._backoff_delay(attempt_index))
                    continue
                return self._failure_result(
                    audit_endpoint,
                    sanitized_parameters,
                    requested_at,
                    received_at,
                    started_at,
                    ProviderRequestFailureCode.TIMEOUT,
                )
            except Exception:
                received_at = _clock_utc(self._utc_now)
                return self._failure_result(
                    audit_endpoint,
                    sanitized_parameters,
                    requested_at,
                    received_at,
                    started_at,
                    ProviderRequestFailureCode.NETWORK_ERROR,
                )

            received_at = _clock_utc(self._utc_now)
            status = response.status_code
            if 200 <= status <= 299:
                audit = ProviderRequestAudit(
                    provider=self._provider,
                    endpoint=audit_endpoint,
                    requested_at_utc=requested_at,
                    received_at_utc=received_at,
                    available_at_utc=available_at_utc or received_at,
                    request_parameters=sanitized_parameters,
                    http_status=status,
                    provider_request_id=_provider_request_id(response.headers),
                    duration_ms=self._duration_ms(started_at),
                    outcome=ProviderRequestOutcome.SUCCESS,
                    failure_code=None,
                )
                return ProviderRequestResult(audit=audit, payload=response.body)

            retryable = status == 429 or 500 <= status <= 599
            if retryable and attempt_index < self._max_retries:
                self._sleep(
                    self._backoff_delay(
                        attempt_index,
                        retry_after=_retry_after_seconds(response.headers, received_at),
                    )
                )
                continue
            return self._failure_result(
                audit_endpoint,
                sanitized_parameters,
                requested_at,
                received_at,
                started_at,
                _http_failure_code(status),
                http_status=status,
                provider_request_id=_provider_request_id(response.headers),
            )

        raise AssertionError("bounded provider request loop terminated unexpectedly")

    def _prepare_request(
        self,
        method: str,
        endpoint: str,
        query_parameters: Mapping[str, object] | None,
        headers: Mapping[str, str] | None,
        body: bytes | None,
    ) -> tuple[HttpRequest, str, dict[str, object]]:
        try:
            url, audit_endpoint, endpoint_parameters = _resolve_endpoint(
                self._base_url,
                endpoint,
            )
            supplied_items, supplied_parameters = _query_items(query_parameters or {})
            parsed = urlsplit(url)
            existing_items = parse_qsl(parsed.query, keep_blank_values=True)
            query = urlencode((*existing_items, *supplied_items), doseq=True)
            request_url = urlunsplit(parsed._replace(query=query))
            request_headers = _validated_headers(headers or {})
            sanitized = _merge_sanitized_parameters(
                endpoint_parameters,
                supplied_parameters,
            )
            request = HttpRequest(
                method=method,
                url=request_url,
                headers=request_headers,
                body=body,
            )
        except (TypeError, ValueError):
            raise ValueError(
                "provider HTTP request is not structurally valid"
            ) from None
        return request, audit_endpoint, sanitized

    def _failure_result(
        self,
        endpoint: str,
        request_parameters: dict[str, object],
        requested_at: datetime,
        received_at: datetime,
        started_at: float,
        failure_code: ProviderRequestFailureCode,
        *,
        http_status: int | None = None,
        provider_request_id: str | None = None,
    ) -> ProviderRequestResult:
        audit = ProviderRequestAudit(
            provider=self._provider,
            endpoint=endpoint,
            requested_at_utc=requested_at,
            received_at_utc=received_at,
            available_at_utc=None,
            request_parameters=request_parameters,
            http_status=http_status,
            provider_request_id=provider_request_id,
            duration_ms=self._duration_ms(started_at),
            outcome=ProviderRequestOutcome.ERROR,
            failure_code=failure_code,
        )
        return ProviderRequestResult(audit=audit, payload=None)

    def _duration_ms(self, started_at: float) -> int:
        finished_at = self._monotonic()
        if not math.isfinite(finished_at):
            raise ValueError("monotonic clock must return a finite value")
        return max(0, round((finished_at - started_at) * 1000))

    def _backoff_delay(
        self,
        attempt_index: int,
        *,
        retry_after: float | None = None,
    ) -> float:
        exponential = min(
            self._backoff_base_seconds * (2**attempt_index),
            self._max_backoff_seconds,
        )
        if retry_after is None:
            return exponential
        bounded_retry_after = min(retry_after, self._max_retry_after_seconds)
        return max(exponential, bounded_retry_after)


def _validated_base_url(base_url: str) -> str:
    if not isinstance(base_url, str):
        raise TypeError("base_url must be a string")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise ValueError("base_url is not structurally valid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ValueError("base_url must be an HTTP(S) origin without credentials")
    return base_url.rstrip("/") + "/"


def _resolve_endpoint(
    base_url: str,
    endpoint: str,
) -> tuple[str, str, dict[str, object]]:
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("endpoint must be a nonempty string")
    url = urljoin(base_url, endpoint)
    base = urlsplit(base_url)
    parsed = urlsplit(url)
    if (
        parsed.scheme != base.scheme
        or parsed.netloc != base.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("endpoint must remain within the provider origin")
    endpoint_parameters = _sanitized_pairs(
        parse_qsl(parsed.query, keep_blank_values=True)
    )
    return url, parsed.path or "/", endpoint_parameters


def _query_items(
    parameters: Mapping[str, object],
) -> tuple[list[tuple[str, str]], dict[str, object]]:
    if not isinstance(parameters, Mapping):
        raise TypeError("query parameters must be a mapping")
    items: list[tuple[str, str]] = []
    sanitized: dict[str, object] = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or not name:
            raise ValueError("query parameter names must be nonempty strings")
        values = (
            tuple(value)
            if isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            else (value,)
        )
        normalized = tuple(_query_scalar(item) for item in values)
        items.extend((name, item) for item in normalized)
        if is_secret_like_parameter_name(name) or any(
            is_secret_like_parameter_value(item) for item in normalized
        ):
            continue
        sanitized[name] = normalized[0] if len(normalized) == 1 else normalized
    return items, sanitized


def _query_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("query parameter numbers must be finite")
        return str(value)
    if isinstance(value, str):
        if any(character in value for character in ("\r", "\n", "\0")):
            raise ValueError("query parameter values contain control characters")
        return value
    raise TypeError("query parameter values must be scalar")


def _validated_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise TypeError("headers must be a mapping")
    result: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("headers must contain strings")
        if not name or any(
            character in name + value for character in ("\r", "\n", "\0")
        ):
            raise ValueError("headers contain invalid control characters")
        result[name] = value
    return result


def _sanitized_pairs(items: Sequence[tuple[str, str]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in items:
        if is_secret_like_parameter_name(name) or is_secret_like_parameter_value(value):
            continue
        previous = result.get(name)
        if previous is None:
            result[name] = value
        elif isinstance(previous, tuple):
            result[name] = (*previous, value)
        else:
            result[name] = (previous, value)
    return result


def _merge_sanitized_parameters(
    endpoint_parameters: dict[str, object],
    supplied_parameters: dict[str, object],
) -> dict[str, object]:
    return {**endpoint_parameters, **supplied_parameters}


def _http_failure_code(status: int) -> ProviderRequestFailureCode:
    if status == 429:
        return ProviderRequestFailureCode.RATE_LIMITED
    if 400 <= status <= 499:
        return ProviderRequestFailureCode.HTTP_CLIENT_ERROR
    if 500 <= status <= 599:
        return ProviderRequestFailureCode.HTTP_SERVER_ERROR
    return ProviderRequestFailureCode.HTTP_ERROR


def _provider_request_id(headers: Mapping[str, str]) -> str | None:
    by_name = {name.casefold(): value for name, value in headers.items()}
    for header in _REQUEST_ID_HEADERS:
        value = by_name.get(header)
        if value is None:
            continue
        stripped = value.strip()
        if (
            stripped
            and len(stripped) <= 512
            and "\r" not in stripped
            and "\n" not in stripped
            and not is_secret_like_parameter_value(stripped)
        ):
            return stripped
    return None


def _retry_after_seconds(
    headers: Mapping[str, str],
    received_at: datetime,
) -> float | None:
    value = next(
        (
            header_value
            for name, header_value in headers.items()
            if name.casefold() == "retry-after"
        ),
        None,
    )
    if value is None:
        return None
    stripped = value.strip()
    try:
        integer_seconds = int(stripped, 10)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None or retry_at.utcoffset() is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = max(
            0.0, (retry_at.astimezone(timezone.utc) - received_at).total_seconds()
        )
        return min(seconds, MAX_DELAY_SECONDS)
    return float(max(0, min(integer_seconds, int(MAX_DELAY_SECONDS))))


def _clock_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("UTC clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _bounded_float(
    name: str,
    value: float,
    *,
    minimum: float | None = None,
    minimum_exclusive: float | None = None,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result > maximum:
        raise ValueError(f"{name} exceeds its finite bound")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} is below its minimum")
    if minimum_exclusive is not None and result <= minimum_exclusive:
        raise ValueError(f"{name} must be positive")
    return result
