"""Sprint 25 — Tests for ``POST /api/vault-action/{id}/{approve|deny}``.

Covers auth, approve happy path (executed/denied/failed), deny path,
404 for unknown/already-resolved rows, and the optional reason body
field on /deny.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _pm_with(approve_row=None, deny_row=None) -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    pm.approve_vault_action = AsyncMock(return_value=approve_row)
    pm.deny_vault_action = AsyncMock(return_value=deny_row)
    return pm


class TestAuthGate:
    def test_approve_missing_header_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.post("/api/vault-action/1/approve")
        assert resp.status_code == 401

    def test_deny_wrong_token_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_pm_with()))
        resp = client.post(
            "/api/vault-action/1/deny",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401


class TestApprove:
    def test_executed_path(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        executed_at = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        row = {
            "id": 7,
            "status": "completed",
            "executed_at": executed_at,
            "denial_reason": None,
        }
        pm = _pm_with(approve_row=row)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/7/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["id"] == 7
        assert body["status"] == "completed"
        assert body["executed_at"] is not None
        pm.approve_vault_action.assert_awaited_once_with(7)

    def test_cap_exceeded_returns_denied_status(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        row = {
            "id": 8,
            "status": "denied",
            "executed_at": None,
            "denial_reason": "daily cap exceeded: $0.00 used + $5.00 requested > $1.00 cap",
        }
        pm = _pm_with(approve_row=row)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/8/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "denied"
        assert "daily cap" in body["denial_reason"]

    def test_failed_path(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        row = {
            "id": 9,
            "status": "failed",
            "executed_at": datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
            "denial_reason": "forced failure (FORCE_FAIL marker present)",
        }
        pm = _pm_with(approve_row=row)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/9/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "FORCE_FAIL" in body["denial_reason"]

    def test_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(approve_row=None)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/99/approve",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 404


class TestDeny:
    def test_happy_path_no_reason(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        row = {"id": 12, "status": "denied", "denial_reason": None}
        pm = _pm_with(deny_row=row)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/12/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "denied"
        pm.deny_vault_action.assert_awaited_once_with(12, reason=None)

    def test_happy_path_with_reason(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        row = {"id": 13, "status": "denied", "denial_reason": "not authorised"}
        pm = _pm_with(deny_row=row)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/13/deny",
            headers={"Authorization": "Bearer secret"},
            json={"reason": "not authorised"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["denial_reason"] == "not authorised"
        pm.deny_vault_action.assert_awaited_once_with(13, reason="not authorised")

    def test_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        pm = _pm_with(deny_row=None)
        client = TestClient(create_app(process_manager=pm))
        resp = client.post(
            "/api/vault-action/99/deny",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 404
