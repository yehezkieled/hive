"""Tests for ``GET /api/messages`` (Sprint 15 Phase 3).

This endpoint is intentionally unauthenticated — read access is gated by
the Tailscale-only network bind. Documented in DEPLOYMENT.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


def _store_returning(rows: list[dict]) -> MagicMock:
    s = MagicMock()
    s.get_recent = AsyncMock(return_value=rows)
    return s


def test_no_store_returns_empty() -> None:
    client = TestClient(create_app(process_manager=_bare_pm()))
    resp = client.get("/api/messages")
    assert resp.status_code == 200
    assert resp.json() == {"messages": []}


def test_returns_messages_in_store_order() -> None:
    now = datetime.now(UTC)
    rows = [
        {
            "sender": "user",
            "recipient": "dev",
            "content": "ping",
            "timestamp": now,
        },
        {
            "sender": "dev",
            "recipient": "user",
            "content": "pong",
            "timestamp": now,
        },
    ]
    client = TestClient(
        create_app(process_manager=_bare_pm(), message_store=_store_returning(rows))
    )
    resp = client.get("/api/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 2
    assert data["messages"][0]["from"] == "user"
    assert data["messages"][0]["text"] == "ping"
    assert data["messages"][1]["from"] == "dev"


def test_limit_param_is_passed_through() -> None:
    store = _store_returning([])
    client = TestClient(create_app(process_manager=_bare_pm(), message_store=store))
    client.get("/api/messages?limit=5")
    store.get_recent.assert_awaited_once_with(limit=5)


def test_open_access_no_auth_required() -> None:
    """Read endpoint is intentionally open — relies on Tailscale bind."""
    client = TestClient(create_app(process_manager=_bare_pm(), message_store=_store_returning([])))
    resp = client.get("/api/messages")
    assert resp.status_code == 200
