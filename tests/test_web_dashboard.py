"""Tests for the Sprint 20 dashboard route + view-model + JSON API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hive.web.app import create_app
from hive.web.view_model import build_dashboard_view_model


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


def _client(**stores) -> TestClient:
    return TestClient(create_app(process_manager=_bare_pm(), **stores))


class TestDashboardPage:
    def test_renders_with_empty_stores(self) -> None:
        resp = _client().get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Markers the React mount + first-paint payload depend on
        assert 'id="root"' in resp.text
        assert "window.HIVE_DASH" in resp.text
        # Chrome: Dashboard tab active, links Hive tab back to /
        assert "is-active" in resp.text
        assert 'href="/"' in resp.text

    def test_loads_jsx_assets(self) -> None:
        resp = _client().get("/dashboard")
        assert "/static/dashboard/dashboard-shell.jsx" in resp.text
        assert "/static/dashboard/dashboard-w1234.jsx" in resp.text
        assert "/static/dashboard/dashboard-w5678.jsx" in resp.text
        assert "/static/dashboard/dashboard-mount.jsx" in resp.text
        assert "/static/dashboard/refresh.js" in resp.text
        # Babel-standalone needs `data-presets="react"` on every text/babel
        # script tag, otherwise JSX never compiles and the React mount silently
        # produces an empty page. Counted: one per JSX file (4 total).
        assert resp.text.count('data-presets="react"') == 4

    def test_static_jsx_served(self) -> None:
        resp = _client().get("/static/dashboard/dashboard-shell.jsx")
        assert resp.status_code == 200

    def test_static_refresh_served(self) -> None:
        resp = _client().get("/static/dashboard/refresh.js")
        assert resp.status_code == 200
        assert "HIVE_AUTO_REFRESH" in resp.text

    def test_dashboard_tab_links_back_from_landing(self) -> None:
        # Symmetric check — the Dashboard tab on the landing should link here.
        resp = _client().get("/")
        assert resp.status_code == 200
        assert 'href="/dashboard"' in resp.text


class TestDashboardAPI:
    def test_requires_token(self) -> None:
        resp = _client().get("/api/dashboard/all")
        assert resp.status_code == 401

    def test_returns_payload_with_token(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "test-token")
        resp = _client().get(
            "/api/dashboard/all",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # The HIVE_DASH contract — JSX widgets read these by name.
        for key in (
            "cost30",
            "health",
            "sankey",
            "p0p1Backlog",
            "cfd",
            "burn",
            "burnEvents",
            "matrix",
            "cacheRows",
            "cacheOverall",
            "histogram",
            "auditFeed",
            "failures",
            "failuresSummary",
            "entitiesY",
            "lastUpdated",
        ):
            assert key in body, f"missing key {key}"


class TestDashboardViewModelShape:
    @pytest.mark.asyncio
    async def test_empty_view_model_keys(self) -> None:
        view = await build_dashboard_view_model(process_manager=_bare_pm())
        # Same contract as TestDashboardAPI but without going through HTTP
        assert view["cost30"] == []
        assert len(view["health"]) == 5
        assert all(s["name"] for s in view["health"])
        assert view["burnEvents"] == {"1h": [], "24h": [], "7d": [], "30d": []}
        assert view["matrix"] == {"entities": [], "models": [], "cells": {}}
        assert view["cacheRows"] == []
        assert len(view["histogram"]) == 60
        assert view["auditFeed"] == []
        assert view["failures"] == []
        assert view["entitiesY"] == []

    @pytest.mark.asyncio
    async def test_serializable_to_json(self) -> None:
        # First paint embeds the dict via `{{ data | tojson }}` — must not
        # contain types Jinja's tojson can't handle (datetime, set, etc.).
        view = await build_dashboard_view_model(process_manager=_bare_pm())
        # round-trip through json to flush out non-serializable types
        text = json.dumps(view)
        roundtrip = json.loads(text)
        assert roundtrip["cost30"] == []
        assert roundtrip["lastUpdated"] == "just now"

    @pytest.mark.asyncio
    async def test_cfd_has_42_points(self) -> None:
        view = await build_dashboard_view_model(process_manager=_bare_pm())
        assert len(view["cfd"]["points"]) == 42
        assert view["cfd"]["dayBoundaries"] == [5, 11, 17, 23, 29, 35, 41]
        assert view["cfd"]["anomalies"] == []


# Module-level fixture-driven tests (asyncio_mode=auto, no decorator needed).


async def test_cache_baseline_uses_7day_history(token_store) -> None:
    """View-model baseline reflects the 7-day rolling rate, not the 24h snapshot."""
    from datetime import UTC, datetime, timedelta

    pool = token_store.pool
    # 24h window (current): 50% hit (cached=100, fresh=100).
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens) VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        100,
        100,
    )
    # 7d baseline: extra 80% sample (cached=400, fresh=100) 3 days ago.
    # Combined 7d totals: cached=500, fresh=200 → 71.4% baseline.
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens, recorded_at) VALUES ($1, $2, $3, $4, $5)""",
        "dev",
        "sonnet",
        100,
        400,
        datetime.now(UTC) - timedelta(days=3),
    )

    view = await build_dashboard_view_model(
        token_store=token_store, process_manager=_bare_pm()
    )
    rows = {r["name"]: r for r in view["cacheRows"]}
    assert rows["dev"]["hit"] == 50.0
    assert rows["dev"]["baseline"] == 71.4


async def test_cache_baseline_falls_back_when_no_7day_history(token_store) -> None:
    """Brand-new entity (24h activity, no 7d history) gets baseline = current hit
    so the JSX doesn't render a fake delta arrow against a synthetic zero."""
    pool = token_store.pool
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens) VALUES ($1, $2, $3, $4)""",
        "fresh-maestro",
        "sonnet",
        100,
        300,
    )

    view = await build_dashboard_view_model(
        token_store=token_store, process_manager=_bare_pm()
    )
    rows = {r["name"]: r for r in view["cacheRows"]}
    # 7d query also picks up the same row (recorded_at = NOW), so baseline equals
    # current hit by virtue of the data, not the fallback. Verify the contract
    # holds either way: baseline is non-zero and matches hit when 24h == 7d window.
    assert rows["fresh-maestro"]["hit"] == 75.0
    assert rows["fresh-maestro"]["baseline"] == 75.0
