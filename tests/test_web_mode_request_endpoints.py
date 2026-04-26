"""Tests for ``POST /api/mode-request/{id}/{approve|deny}``.

Covers:
- Missing/wrong bearer token → 401
- Approve happy path → 200 with row
- Deny happy path → 200 with row
- Missing/already-resolved row → 404
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _pm_with(approve_row=None, deny_row=None) -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    pm.approve_mode_request = AsyncMock(return_value=approve_row)
    pm.deny_mode_request = AsyncMock(return_value=deny_row)
    return pm


class TestAuthGate:
    def test_approve_missing_header_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.post("/api/mode-request/1/approve")
        assert resp.status_code == 401

    def test_deny_wrong_token_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.post(
            "/api/mode-request/1/deny",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401


class TestApprove:
    def test_happy_path(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(approve_row={"id": 7, "status": "approved"})
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/mode-request/7/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": 7, "status": "approved"}
        pm.approve_mode_request.assert_awaited_once_with(7)

    def test_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(approve_row=None)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/mode-request/99/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 404


class TestDeny:
    def test_happy_path(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(deny_row={"id": 12, "status": "denied"})
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/mode-request/12/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": 12, "status": "denied"}
        pm.deny_mode_request.assert_awaited_once_with(12)

    def test_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(deny_row=None)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/mode-request/99/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 404
