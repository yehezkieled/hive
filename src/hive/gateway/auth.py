"""Owner-only access: Tailscale login header, loopback peer, Host/Origin allowlist.

Everything that fails a check is a bare 403, with no hint about which check failed.
Reads are GET/HEAD; the only other method is POST, and only under ``/act/``. A POST must
also carry an allowed ``Origin`` and not be marked cross-site by the browser.
"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from hive.gateway.settings import GatewaySettings

LOGIN_HEADER = "tailscale-user-login"


def _hostname(host_header: str) -> str:
    """Hostname part of a Host header value, lowercased, without port."""
    host = host_header.strip().lower()
    if host.startswith("["):  # bracketed IPv6
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_authorised(request: Request, settings: GatewaySettings) -> bool:
    peer = request.client.host if request.client else ""
    if peer not in settings.trusted_peers:
        return False
    login = request.headers.get(LOGIN_HEADER, "").strip().lower()
    if not login or not hmac.compare_digest(login, settings.owner_login):
        return False
    if _hostname(request.headers.get("host", "")) not in settings.allowed_hosts:
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        origin_host = urlsplit(origin).hostname or ""
        if origin_host.lower() not in settings.allowed_hosts:
            return False
    return True


def is_same_origin_write(request: Request) -> bool:
    """A write needs an Origin (``is_authorised`` vetted it) and no cross-site hint."""
    if request.headers.get("origin") is None:
        return False
    return request.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")


def forbidden() -> Response:
    return PlainTextResponse("forbidden", status_code=403)


def method_not_allowed() -> Response:
    return PlainTextResponse(
        "method not allowed", status_code=405, headers={"Allow": "GET, HEAD, POST"}
    )
