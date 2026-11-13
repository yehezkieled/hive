"""The desk's own static files: the PWA manifest and service worker, its icons and its fonts.
All served from this desk, never a CDN, and only by a pinned name."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import PlainTextResponse, Response

ICONS_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "icons"
FONTS_DIR = Path(__file__).resolve().parent / "static" / "fonts"
FONT_FILES = frozenset(
    {
        "ibm-plex-mono-400.woff2",
        "ibm-plex-mono-500.woff2",
        "ibm-plex-mono-600.woff2",
        "ibm-plex-mono-700.woff2",
        "familjen-grotesk.woff2",
    }
)
ICON_FILES = {
    "icon-192.png": "icon-192.png",
    "icon-512.png": "icon-512.png",
    "apple-touch-icon-180.png": "apple-touch-icon-180.png",
}
MANIFEST = {
    "name": "Hive desk",
    "short_name": "Hive desk",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f4efe5",  # the Comb page ground and top bar, light theme
    "theme_color": "#fffcf6",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}
SERVICE_WORKER = """
self.addEventListener('install', function(){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('push', function(e){
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {}
  var url = typeof d.url === 'string' && d.url.charAt(0) === '/' ? d.url : '/';
  e.waitUntil(self.registration.showNotification(String(d.title || 'Hive desk'), {
    body: String(d.body || ''), tag: d.tag ? String(d.tag) : undefined,
    icon: '/icons/icon-192.png', data: { url: url }
  }));
});
self.addEventListener('notificationclick', function(e){
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function(list){
    for (var i = 0; i < list.length; i++) {
      if ('focus' in list[i]) { list[i].navigate(url); return list[i].focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
"""


def add_asset_routes(app: FastAPI) -> None:
    """The service worker, the manifest, the icons and the fonts."""

    @app.get("/sw.js")
    async def service_worker() -> Response:
        return Response(
            SERVICE_WORKER,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.webmanifest")
    async def manifest() -> Response:
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/icons/{name}")
    async def icon(name: str) -> Response:
        fname = ICON_FILES.get(name)
        if fname is None or not (ICONS_DIR / fname).is_file():
            return PlainTextResponse("not found", status_code=404)
        return Response((ICONS_DIR / fname).read_bytes(), media_type="image/png")

    @app.get("/fonts/{name}")
    async def font(name: str) -> Response:
        if name not in FONT_FILES or not (FONTS_DIR / name).is_file():
            return PlainTextResponse("not found", status_code=404)
        return Response((FONTS_DIR / name).read_bytes(), media_type="font/woff2")
