"""Ticket 041 — POST /api/push-subscribe endpoint.

Stores a browser push subscription (upsert by endpoint), behind the same
bearer-token auth as every other write endpoint. Uses a fake store so the
endpoint is exercised without a database.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hive.web.app import create_app


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


class _FakePushStore:
    def __init__(self) -> None:
        self.upserted: list[dict] = []

    async def upsert(self, sub: dict) -> None:
        self.upserted.append(sub)


_SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "kkk", "auth": "aaa"}}


def test_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    client = TestClient(
        create_app(process_manager=_bare_pm(), push_subscription_store=_FakePushStore())
    )
    resp = client.post("/api/push-subscribe", json=_SUB)
    assert resp.status_code == 401


def test_stores_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    store = _FakePushStore()
    client = TestClient(create_app(process_manager=_bare_pm(), push_subscription_store=store))
    resp = client.post("/api/push-subscribe", json=_SUB, headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(store.upserted) == 1
    saved = store.upserted[0]
    assert saved["endpoint"] == _SUB["endpoint"]
    assert saved["p256dh"] == "kkk"
    assert saved["auth"] == "aaa"


def test_no_store_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    client = TestClient(create_app(process_manager=_bare_pm()))
    resp = client.post("/api/push-subscribe", json=_SUB, headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 503


def test_malformed_body_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
    client = TestClient(
        create_app(process_manager=_bare_pm(), push_subscription_store=_FakePushStore())
    )
    resp = client.post(
        "/api/push-subscribe", json={"keys": {}}, headers={"Authorization": "Bearer secret"}
    )
    assert resp.status_code == 422
