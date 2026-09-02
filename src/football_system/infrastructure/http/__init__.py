"""Secret-safe HTTP infrastructure for real data providers."""

from football_system.infrastructure.http.provider_client import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
    ProviderHttpClient,
)

__all__ = (
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "ProviderHttpClient",
)
