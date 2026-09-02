from __future__ import annotations

import socket
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from football_system.infrastructure.http.provider_client import (
    HttpRequest,
    HttpResponse,
)


class UrllibTransport:
    """Small synchronous transport that does not alter urllib's global opener."""

    def send(
        self,
        request: HttpRequest,
        *,
        timeout_seconds: float,
    ) -> HttpResponse:
        urllib_request = Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        # build_opener creates an isolated opener rather than changing urllib defaults.
        opener = build_opener()
        try:
            with opener.open(urllib_request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=response.read(),
                    headers=_headers(response.headers),
                )
        except HTTPError as error:
            return HttpResponse(
                status_code=error.code,
                body=error.read(),
                headers=_headers(error.headers),
            )
        except (TimeoutError, socket.timeout):
            raise TimeoutError("HTTP transport timed out") from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("HTTP transport timed out") from None
            # Do not expose request URLs or lower-level exception messages.
            raise OSError("HTTP transport failed") from None
        except OSError:
            raise OSError("HTTP transport failed") from None


def _headers(headers: object) -> Mapping[str, str]:
    if headers is None:
        return {}
    items = getattr(headers, "items", None)
    if not callable(items):
        return {}
    return {
        str(name): str(value)
        for name, value in items()
        if isinstance(name, str) and isinstance(value, str)
    }
