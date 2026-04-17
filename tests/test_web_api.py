"""Tests for Hive web dashboard API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _make_app() -> TestClient:
    """Create a TestClient with mocked dependencies."""
    process_manager = MagicMock()
    process_manager.get_status.return_value = [
        {
            "name": "dev",
            "role": "maestro",
            "state": "RUNNING",
            "model": "sonnet",
            "pid": 1234,
            "uptime": 120.5,
        },
    ]
    process_manager.entities = {
        "dev": MagicMock(
            name="dev",
            role="maestro",
            state=MagicMock(value="RUNNING"),
            model="sonnet",
            teams={},
        ),
    }

    token_store = MagicMock()
    token_store.totals = AsyncMock(
        return_value={
            "call_count": 10,
            "input_tokens": 5000,
            "output_tokens": 2000,
            "cost_usd": 0.05,
        }
    )

    task_store = MagicMock()
    task_store.list = AsyncMock(return_value=[])

    audit_log = MagicMock()
    audit_log.recent = AsyncMock(return_value=[])

    app = create_app(
        process_manager=process_manager,
        token_store=token_store,
        task_store=task_store,
        audit_log=audit_log,
    )
    return TestClient(app)


class TestStatusEndpoint:
    def test_status_returns_200(self) -> None:
        client = _make_app()
        resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_status_returns_entity_list(self) -> None:
        client = _make_app()
        data = client.get("/api/status").json()
        assert isinstance(data, list)
        assert data[0]["name"] == "dev"
        assert data[0]["role"] == "maestro"


class TestOrgEndpoint:
    def test_org_returns_200(self) -> None:
        client = _make_app()
        resp = client.get("/api/org")
        assert resp.status_code == 200

    def test_org_returns_dict(self) -> None:
        client = _make_app()
        data = client.get("/api/org").json()
        assert isinstance(data, dict)
        assert "maestros" in data


class TestTasksEndpoint:
    def test_tasks_returns_200(self) -> None:
        client = _make_app()
        resp = client.get("/api/tasks")
        assert resp.status_code == 200


class TestCostEndpoint:
    def test_cost_returns_200(self) -> None:
        client = _make_app()
        resp = client.get("/api/cost")
        assert resp.status_code == 200

    def test_cost_returns_totals(self) -> None:
        client = _make_app()
        data = client.get("/api/cost").json()
        assert "call_count" in data


class TestDashboard:
    def test_dashboard_html(self) -> None:
        client = _make_app()
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Hive" in resp.text
