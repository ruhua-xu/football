from __future__ import annotations

import io
import socket
import urllib.request
from urllib.error import HTTPError, URLError

import pytest

from football_system.infrastructure.http.provider_client import HttpRequest
from football_system.infrastructure.http.urllib_transport import (
    MAX_RESPONSE_BYTES,
    UrllibTransport,
    _RejectRedirectHandler,
)


class FakeHeaders:
    def items(self) -> list[tuple[str, str]]:
        return [("X-Request-ID", "synthetic-request")]


class FakeResponse:
    status = 200
    headers = FakeHeaders()

    def read(self, amount: int = -1) -> bytes:
        return b"synthetic"[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: float) -> object:
        self.requests.append((request, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _request() -> HttpRequest:
    return HttpRequest(
        method="GET",
        url="https://synthetic.provider.test/odds?apiKey=synthetic-secret-token",
        headers={"Authorization": "synthetic-secret-token"},
    )


def test_transport_uses_an_isolated_opener_without_global_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = FakeOpener(FakeResponse())
    before = urllib.request._opener
    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.build_opener",
        lambda *handlers: opener,
    )

    response = UrllibTransport().send(_request(), timeout_seconds=2.5)

    assert response.status_code == 200
    assert response.body == b"synthetic"
    assert response.headers["X-Request-ID"] == "synthetic-request"
    assert urllib.request._opener is before
    assert opener.requests[0][1] == 2.5


def test_transport_returns_http_errors_and_hides_network_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = HTTPError(
        "https://synthetic.provider.test/?apiKey=synthetic-secret-token",
        429,
        "limited",
        FakeHeaders(),
        io.BytesIO(b"limited"),
    )
    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.build_opener",
        lambda *handlers: FakeOpener(error),
    )

    response = UrllibTransport().send(_request(), timeout_seconds=2.5)

    assert response.status_code == 429
    assert response.body == b"limited"

    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.build_opener",
        lambda *handlers: FakeOpener(
            URLError(socket.timeout("synthetic-secret-token"))
        ),
    )
    with pytest.raises(TimeoutError) as timeout:
        UrllibTransport().send(_request(), timeout_seconds=2.5)
    assert "synthetic-secret-token" not in str(timeout.value)


def test_transport_rejects_redirects_before_credentials_can_be_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: list[object] = []

    def opener_factory(*values: object) -> FakeOpener:
        handlers.extend(values)
        return FakeOpener(FakeResponse())

    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.build_opener",
        opener_factory,
    )

    UrllibTransport().send(_request(), timeout_seconds=2.5)

    assert len(handlers) == 1
    redirect_handler = handlers[0]
    assert isinstance(redirect_handler, _RejectRedirectHandler)
    assert (
        redirect_handler.redirect_request(
            object(),
            object(),
            302,
            "redirect",
            object(),
            "https://attacker.invalid/",
        )
        is None
    )


def test_transport_rejects_oversized_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedResponse(FakeResponse):
        def read(self, amount: int = -1) -> bytes:
            return b"x" * min(amount, MAX_RESPONSE_BYTES + 1)

    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.build_opener",
        lambda *handlers: FakeOpener(OversizedResponse()),
    )

    with pytest.raises(OSError, match="size limit"):
        UrllibTransport().send(_request(), timeout_seconds=2.5)
