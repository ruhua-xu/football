from __future__ import annotations

import socket
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from football_system.infrastructure.http.provider_client import (
    HttpRequest,
    HttpResponse,
)

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class HttpResponseTooLargeError(OSError):
    pass


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: object,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


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
        # Redirects are returned as HTTP failures so credentials never cross origins.
        opener = build_opener(_RejectRedirectHandler())
        try:
            with opener.open(urllib_request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=_bounded_body(response),
                    headers=_headers(response.headers),
                )
        except HTTPError as error:
            return HttpResponse(
                status_code=error.code,
                body=_bounded_body(error),
                headers=_headers(error.headers),
            )
        except (TimeoutError, socket.timeout):
            raise TimeoutError("HTTP transport timed out") from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("HTTP transport timed out") from None
            # Do not expose request URLs or lower-level exception messages.
            raise OSError("HTTP transport failed") from None
        except HttpResponseTooLargeError:
            raise
        except OSError:
            raise OSError("HTTP transport failed") from None


def _bounded_body(response: object) -> bytes:
    read = getattr(response, "read", None)
    if not callable(read):
        raise OSError("HTTP transport returned an unreadable response")
    body = read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise HttpResponseTooLargeError("HTTP response exceeded the size limit")
    return body


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
