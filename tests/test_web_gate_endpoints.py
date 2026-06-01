"""Tests for the gate web surface (Ticket 003, slice #24).

Covers the three gate endpoints, mirroring
``test_web_mode_request_endpoints.py``:
- ``GET  /api/gates/pending``           — pending gate rows (kind="gate")
- ``POST /api/gate/{id}/approve``       — approve a parked gate
- ``POST /api/gate/{id}/deny``          — deny a parked gate

Each is guarded by the bearer-token check and returns the same row
shape the mode-request endpoints use.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _pm_with(approve_row=None, deny_row=None) -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    pm.approve_gate = AsyncMock(return_value=approve_row)
    pm.deny_gate = AsyncMock(return_value=deny_row)
    return pm


def _store_with(pending=None) -> MagicMock:
    store = MagicMock()
    store.list_pending = AsyncMock(return_value=pending or [])
    return store


class TestPendingList:
    def test_requires_token(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.get("/api/gates/pending")
        assert resp.status_code == 401

    def test_empty_when_no_store(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.get(
            "/api/gates/pending",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"gates": []}

    def test_lists_pending_gates(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        rows = [
            {
                "id": 3,
                "requester": "dev",
                "requested_mode": "plan",
                "reason": "Here is my plan. Proceed?",
                "status": "pending",
            }
        ]
        store = _store_with(pending=rows)
        client = TestClient(
            create_app(
                process_manager=_pm_with(),
                mode_request_store=store,
                default_maestro="otter",
            )
        )
        resp = client.get(
            "/api/gates/pending",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"gates": rows}
        # Scoped to gate rows for the default maestro.
        store.list_pending.assert_awaited_once_with("otter", kind="gate")


class TestApprove:
    def test_requires_token(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.post("/api/gate/1/approve")
        assert resp.status_code == 401

    def test_happy_path(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(approve_row={"id": 7, "status": "approved"})
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/gate/7/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": 7, "status": "approved"}
        pm.approve_gate.assert_awaited_once_with(7)

    def test_status_defaults_to_approved(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(approve_row={"id": 7})
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/gate/7/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": 7, "status": "approved"}

    def test_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(approve_row=None)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/gate/99/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 404


class TestDeny:
    def test_requires_token(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.post(
            "/api/gate/1/deny",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_happy_path(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(deny_row={"id": 12, "status": "denied"})
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/gate/12/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": 12, "status": "denied"}
        pm.deny_gate.assert_awaited_once_with(12)

    def test_status_defaults_to_denied(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(deny_row={"id": 12})
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/gate/12/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": 12, "status": "denied"}

    def test_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(deny_row=None)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/gate/99/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 404
