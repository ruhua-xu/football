from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from football_system.domain.raw_data import (
    ProviderRequestFailureCode,
    ProviderRequestOutcome,
)
from football_system.infrastructure.http.provider_client import (
    MAX_RETRIES,
    HttpRequest,
    HttpResponse,
    ProviderHttpClient,
)

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
TOKEN = "top-secret-token-value"


class ScriptedTransport:
    def __init__(self, outcomes: list[HttpResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[tuple[HttpRequest, float]] = []

    def send(
        self,
        request: HttpRequest,
        *,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.requests.append((request, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TickingClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=100)
        return current


class TickingMonotonic:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.125
        return current


def _client(
    transport: ScriptedTransport,
    *,
    sleep: Callable[[float], None] | None = None,
    **settings: object,
) -> ProviderHttpClient:
    return ProviderHttpClient(
        "SPORTMONKS",
        "https://api.example.test/v3",
        transport,
        sleep=sleep or (lambda delay: None),
        utc_now=TickingClock(),
        monotonic=TickingMonotonic(),
        **settings,
    )


def test_success_returns_payload_audit_and_redacts_auth_material() -> None:
    transport = ScriptedTransport(
        [
            HttpResponse(
                200,
                b'{"data":[]}',
                {"X-Request-ID": "provider-request-7"},
            )
        ]
    )
    client = _client(transport, timeout_seconds=2.5)

    result = client.get(
        "/fixtures?include=participants&api_key=" + TOKEN,
        query_parameters={
            "league": "EPL",
            "access_token": TOKEN,
            "search": f"Bearer {TOKEN}",
        },
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-API-Key": TOKEN,
        },
    )

    sent, timeout = transport.requests[0]
    assert result.succeeded is True
    assert result.payload == b'{"data":[]}'
    assert result.audit.outcome is ProviderRequestOutcome.SUCCESS
    assert result.audit.http_status == 200
    assert result.audit.provider_request_id == "provider-request-7"
    assert result.audit.duration_ms == 125
    assert result.audit.endpoint == "/fixtures"
    assert result.audit.request_parameters == {
        "include": "participants",
        "league": "EPL",
    }
    assert timeout == 2.5
    assert TOKEN in sent.url
    assert TOKEN in sent.headers["Authorization"]
    assert TOKEN not in repr(sent)
    assert TOKEN not in repr(result)
    assert TOKEN not in result.audit.model_dump_json()
    assert result.to_raw_artifact_metadata().payload_sha256


def test_timeout_retries_with_exponential_backoff_then_returns_failure() -> None:
    transport = ScriptedTransport(
        [TimeoutError(TOKEN), TimeoutError(TOKEN), TimeoutError(TOKEN)]
    )
    delays: list[float] = []
    client = _client(
        transport,
        sleep=delays.append,
        timeout_seconds=1.5,
        max_retries=2,
        backoff_base_seconds=0.5,
        max_backoff_seconds=5.0,
    )

    result = client.get(
        "/fixtures",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert len(transport.requests) == 3
    assert [timeout for _, timeout in transport.requests] == [1.5, 1.5, 1.5]
    assert delays == [0.5, 1.0]
    assert result.succeeded is False
    assert result.payload is None
    assert result.audit.http_status is None
    assert result.audit.failure_code is ProviderRequestFailureCode.TIMEOUT
    assert TOKEN not in repr(result)


def test_429_honors_bounded_retry_after_and_reports_exhaustion() -> None:
    delays: list[float] = []
    transport = ScriptedTransport(
        [
            HttpResponse(429, b"rate limited", {"Retry-After": "9" * 1000}),
            HttpResponse(200, b"ok"),
        ]
    )
    client = _client(
        transport,
        sleep=delays.append,
        max_retries=1,
        backoff_base_seconds=0.25,
        max_retry_after_seconds=3.0,
    )

    result = client.get("/fixtures")

    assert result.succeeded is True
    assert len(transport.requests) == 2
    assert delays == [3.0]

    exhausted_transport = ScriptedTransport([HttpResponse(429, b"limited")])
    exhausted = _client(exhausted_transport, max_retries=0).get("/fixtures")
    assert exhausted.payload is None
    assert exhausted.audit.http_status == 429
    assert exhausted.audit.failure_code is ProviderRequestFailureCode.RATE_LIMITED


def test_retries_5xx_but_not_nonretryable_4xx() -> None:
    server_delays: list[float] = []
    server_transport = ScriptedTransport(
        [HttpResponse(503, b"unavailable"), HttpResponse(200, b"ok")]
    )
    server_result = _client(
        server_transport,
        sleep=server_delays.append,
        max_retries=1,
        backoff_base_seconds=0.75,
    ).get("/fixtures")

    assert server_result.succeeded is True
    assert len(server_transport.requests) == 2
    assert server_delays == [0.75]

    client_delays: list[float] = []
    client_transport = ScriptedTransport(
        [HttpResponse(404, b"missing"), HttpResponse(200, b"must not be used")]
    )
    client_result = _client(
        client_transport,
        sleep=client_delays.append,
        max_retries=3,
    ).get("/fixtures")

    assert len(client_transport.requests) == 1
    assert client_delays == []
    assert client_result.payload is None
    assert client_result.audit.http_status == 404
    assert (
        client_result.audit.failure_code is ProviderRequestFailureCode.HTTP_CLIENT_ERROR
    )


def test_non_timeout_transport_error_is_not_retried_or_exposed() -> None:
    transport = ScriptedTransport([OSError(f"socket failed using {TOKEN}")])
    result = _client(transport, max_retries=4).get(
        "/fixtures",
        query_parameters={"api_key": TOKEN},
    )

    assert len(transport.requests) == 1
    assert result.payload is None
    assert result.audit.failure_code is ProviderRequestFailureCode.NETWORK_ERROR
    assert result.audit.request_parameters == {}
    assert TOKEN not in repr(result)


@pytest.mark.parametrize(
    "settings",
    (
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 0},
        {"max_retries": MAX_RETRIES + 1},
        {"backoff_base_seconds": float("inf")},
        {"max_retry_after_seconds": 301},
    ),
)
def test_timeout_retry_and_delay_configuration_is_finitely_bounded(
    settings: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError), match="bound|positive|between"):
        _client(ScriptedTransport([]), **settings)
