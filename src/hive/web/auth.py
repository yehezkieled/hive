"""Bearer-token auth for the Hive web write surface (Sprint 15).

Read endpoints (landing GET, htmx fragments, ``/api/messages``) are gated
by the Tailscale-only network bind. Write endpoints (``POST /api/command``
and Phase-4 SSE) require ``Authorization: Bearer <HIVE_WEB_TOKEN>``.

Security note: when ``HIVE_WEB_TOKEN`` is empty/unset the dependency
rejects *all* requests — the write surface is disabled-closed, never
disabled-open. Configure the env var to opt in.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from hive.config import WEB_TOKEN


def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: 401 unless ``Authorization`` matches WEB_TOKEN.

    Used as ``Depends(require_token)`` so SSE (Phase 4) can reuse the
    same gate without bringing middleware into the picture.
    """
    if not WEB_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Web write surface disabled: set HIVE_WEB_TOKEN",
        )
    expected = f"Bearer {WEB_TOKEN}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
