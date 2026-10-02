"""ASGI app: auth middleware plus two read-only pages."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from hive.gateway import pages
from hive.gateway.auth import forbidden, is_authorised, method_not_allowed
from hive.gateway.desk import build_desk
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'",
}


def create_app(
    settings: GatewaySettings | None = None, provider: SnapshotProvider | None = None
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    provider = provider or SnapshotProvider(settings)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def owner_only(request: Request, call_next) -> Response:
        if not is_authorised(request, settings):
            return forbidden()
        if request.method not in ("GET", "HEAD"):
            return method_not_allowed()
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        snap = await provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        return HTMLResponse(pages.render_home(snap, desk))

    @app.get("/p/{name}", response_class=HTMLResponse)
    async def project(name: str) -> HTMLResponse:
        snap = await provider.get()
        desk = build_desk(snap.data) if snap.data is not None else None
        proj = desk.projects.get(name) if desk else None
        status = 200 if (proj or desk is None) else 404
        return HTMLResponse(pages.render_project(name, snap, proj), status_code=status)

    return app
