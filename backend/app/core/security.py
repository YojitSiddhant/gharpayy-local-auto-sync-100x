"""Local-only access boundary for private OCR endpoints.

CORS is not authentication. Protected endpoints require all of:
1. a loopback client address during local development, or an explicitly configured
   production host/origin and server-only token,
2. an allowed Host header (DNS-rebinding guard),
3. no foreign browser Origin (direct cross-site browser calls are refused),
4. the server-only shared token sent by the Next.js proxy (constant-time compare).
"""
import hmac
from urllib.parse import urlsplit

from fastapi import Depends, Request

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ApiError

LOCAL_TOKEN_HEADER = "x-local-api-token"
MIN_TOKEN_LENGTH = 32
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1"}


def _host_name(host_header: str) -> str:
    if not host_header:
        return ""
    return (urlsplit(f"//{host_header}").hostname or "").lower()


def require_local_api_access(request: Request, settings: Settings = Depends(get_settings)) -> None:
    test_mode = settings.app_env == "test"
    client = request.client.host if request.client else ""
    remote_allowed = settings.app_env in {"production", "render"}
    if client not in _LOOPBACK_CLIENTS and not (test_mode and client == "testclient") and not remote_allowed:
        raise ApiError(403, "LOOPBACK_ONLY", "This API only accepts requests from this computer.")

    allowed_hosts = {h.strip("[]").lower() for h in settings.backend_allowed_hosts}
    if test_mode:
        allowed_hosts.add("testserver")
    if _host_name(request.headers.get("host", "")) not in allowed_hosts:
        raise ApiError(403, "HOST_NOT_ALLOWED", "Request host is not allowed.")

    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.backend_cors_origins:
        raise ApiError(403, "ORIGIN_NOT_ALLOWED", "Request origin is not allowed.")

    expected = settings.local_api_token
    if len(expected) < MIN_TOKEN_LENGTH:
        raise ApiError(503, "LOCAL_TOKEN_NOT_CONFIGURED", "Local API token is not configured on the backend.")
    supplied = request.headers.get(LOCAL_TOKEN_HEADER, "")
    if not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise ApiError(401, "LOCAL_TOKEN_REQUIRED", "A valid local API token is required.")
