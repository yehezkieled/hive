"""Tests for Ticket 040 — PWA install: manifest, service worker, apple meta, icons."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hive.web.app import create_app


def _bare_pm() -> MagicMock:
    """ProcessManager mock with no entities registered."""
    pm = MagicMock()
    pm.entities = {}
    return pm


def _client(**stores) -> TestClient:
    return TestClient(create_app(process_manager=_bare_pm(), **stores))


class TestManifest:
    def test_manifest_served_with_manifest_media_type(self) -> None:
        resp = _client().get("/manifest.webmanifest")
        assert resp.status_code == 200
        assert "application/manifest+json" in resp.headers["content-type"]

    def test_manifest_has_install_fields(self) -> None:
        manifest = json.loads(_client().get("/manifest.webmanifest").text)
        assert manifest["name"] == "Hive"
        assert manifest["start_url"] == "/"
        assert manifest["scope"] == "/"
        assert manifest["display"] == "standalone"
        assert manifest["theme_color"] == "#e0a726"
        assert manifest["background_color"] == "#faf7ed"
        assert len(manifest["icons"]) >= 2


class TestServiceWorker:
    def test_served_from_root_with_js_media_type(self) -> None:
        # Root path → scope "/" so the worker controls "/" and "/dashboard".
        resp = _client().get("/service-worker.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    def test_served_no_cache(self) -> None:
        resp = _client().get("/service-worker.js")
        assert "no-cache" in resp.headers.get("cache-control", "")

    def test_has_lifecycle_and_versioned_cache(self) -> None:
        body = _client().get("/service-worker.js").text
        assert "CACHE_VERSION" in body
        for event in ("install", "activate", "fetch"):
            assert f"addEventListener('{event}'" in body or f'addEventListener("{event}"' in body


@pytest.mark.parametrize("path", ["/", "/dashboard"])
class TestHeadTags:
    """Both pages must carry the manifest link, apple meta, and SW registration."""

    def test_manifest_linked(self, path: str) -> None:
        html = _client().get(path).text
        assert 'rel="manifest"' in html
        assert "/manifest.webmanifest" in html

    def test_apple_meta_present(self, path: str) -> None:
        html = _client().get(path).text
        assert 'rel="apple-touch-icon"' in html
        assert 'name="apple-mobile-web-app-capable"' in html
        assert 'name="apple-mobile-web-app-status-bar-style"' in html

    def test_theme_color(self, path: str) -> None:
        html = _client().get(path).text
        assert 'name="theme-color"' in html
        assert "#e0a726" in html

    def test_registers_service_worker(self, path: str) -> None:
        html = _client().get(path).text
        assert "serviceWorker.register" in html
        assert "/service-worker.js" in html


class TestAssets:
    def test_icon_served_as_png(self) -> None:
        resp = _client().get("/static/icons/icon-192.png")
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    def test_apple_touch_icon_served(self) -> None:
        resp = _client().get("/static/icons/apple-touch-icon-180.png")
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    def test_favicon_served(self) -> None:
        assert _client().get("/static/icons/favicon.ico").status_code == 200

    def test_every_manifest_icon_resolves(self) -> None:
        client = _client()
        manifest = json.loads(client.get("/manifest.webmanifest").text)
        for icon in manifest["icons"]:
            assert client.get(icon["src"]).status_code == 200, icon["src"]

    def test_offline_shell_served(self) -> None:
        resp = _client().get("/static/offline.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "offline" in resp.text.lower()
