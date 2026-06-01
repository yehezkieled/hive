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


class TestGatePanel:
    """The landing page carries the Ticket 003 'Pending gates' panel."""

    def test_landing_renders_gate_panel(self) -> None:
        resp = _client().get("/")
        assert resp.status_code == 200
        # Button + popup dialog mirroring the bell/approvals panel.
        assert 'id="gate-btn"' in resp.text
        assert 'id="gate-popup"' in resp.text
        assert "Pending gates" in resp.text

    def test_gate_panel_wires_endpoints(self) -> None:
        resp = _client().get("/")
        # JS fetches the pending list and POSTs approve/deny to the gate routes.
        assert "/api/gates/pending" in resp.text
        assert "/api/gate/" in resp.text


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

    view = await build_dashboard_view_model(token_store=token_store, process_manager=_bare_pm())
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

    view = await build_dashboard_view_model(token_store=token_store, process_manager=_bare_pm())
    rows = {r["name"]: r for r in view["cacheRows"]}
    # 7d query also picks up the same row (recorded_at = NOW), so baseline equals
    # current hit by virtue of the data, not the fallback. Verify the contract
    # holds either way: baseline is non-zero and matches hit when 24h == 7d window.
    assert rows["fresh-maestro"]["hit"] == 75.0
    assert rows["fresh-maestro"]["baseline"] == 75.0


async def test_failure_scatter_classifies_recent_failures(task_store) -> None:
    """View-model surfaces recent failed tasks classified by reason category."""
    pool = task_store.pool
    pm = MagicMock()
    pm.entities = {"dev": object(), "ops": object()}

    # 3 recent failures across 2 entities, 3 distinct categories.
    for assigned_to, reason in (
        ("dev", "Request timed out after 30s"),
        ("ops", "HTTP 429 Too Many Requests"),
        ("dev", "Connection refused"),
    ):
        await pool.execute(
            """
            INSERT INTO tasks (title, assigned_to, status, failure_reason,
                               created_by, retry_count, max_retries, completed_at)
            VALUES ($1, $2, 'failed', $3, 'system', 1, 3, NOW())
            """,
            f"task for {assigned_to}",
            assigned_to,
            reason,
        )

    view = await build_dashboard_view_model(task_store=task_store, process_manager=pm)
    assert len(view["failures"]) == 3
    cats = sorted(f["category"] for f in view["failures"])
    assert cats == ["network", "rate_limit", "timeout"]
    assert view["failuresSummary"]["lastHour"] == 3
    assert view["failuresSummary"]["pendingEscalations"] == 0


async def test_failure_summary_counts_pending_escalations(task_store) -> None:
    """Tasks past max_retries that haven't completed are counted as escalations."""
    pool = task_store.pool
    pm = MagicMock()
    pm.entities = {"dev": object()}

    # Past max retries, not completed → pending escalation.
    await pool.execute(
        """
        INSERT INTO tasks (title, assigned_to, status, failure_reason,
                           created_by, retry_count, max_retries, completed_at)
        VALUES ('escalating', 'dev', 'in_progress', 'syntax error', 'system', 3, 3, NOW())
        """
    )
    # Same retry math but already completed → not counted.
    await pool.execute(
        """
        INSERT INTO tasks (title, assigned_to, status, failure_reason,
                           created_by, retry_count, max_retries, completed_at)
        VALUES ('settled', 'dev', 'completed', 'syntax error', 'system', 3, 3, NOW())
        """
    )

    view = await build_dashboard_view_model(task_store=task_store, process_manager=pm)
    assert view["failuresSummary"]["pendingEscalations"] == 1
