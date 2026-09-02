from __future__ import annotations

import io
import socket
import urllib.request
from urllib.error import HTTPError, URLError

import pytest

from football_system.infrastructure.http.provider_client import HttpRequest
from football_system.infrastructure.http.urllib_transport import UrllibTransport


class FakeHeaders:
    def items(self) -> list[tuple[str, str]]:
        return [("X-Request-ID", "synthetic-request")]


class FakeResponse:
    status = 200
    headers = FakeHeaders()

    def read(self) -> bytes:
        return b"synthetic"

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
        lambda: opener,
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
        lambda: FakeOpener(error),
    )

    response = UrllibTransport().send(_request(), timeout_seconds=2.5)

    assert response.status_code == 429
    assert response.body == b"limited"

    monkeypatch.setattr(
        "football_system.infrastructure.http.urllib_transport.build_opener",
        lambda: FakeOpener(URLError(socket.timeout("synthetic-secret-token"))),
    )
    with pytest.raises(TimeoutError) as timeout:
        UrllibTransport().send(_request(), timeout_seconds=2.5)
    assert "synthetic-secret-token" not in str(timeout.value)
