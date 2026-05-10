"""Tests for ``POST /api/command`` (Sprint 15 Phase 3).

Covers:
- Missing/invalid bearer token → 401
- Empty configured token → 401 even with a header
- Valid token + dispatcher → returns dispatcher's text
- No dispatcher wired → friendly placeholder, still requires auth
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from hive.commands.dispatch import CommandResult
from hive.web.app import create_app


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


def _dispatcher_returning(text: str) -> MagicMock:
    d = MagicMock()
    d.dispatch = AsyncMock(return_value=CommandResult(text=text))
    return d


class TestAuthGate:
    def test_missing_header_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(
            create_app(
                process_manager=_bare_pm(),
                command_dispatcher=_dispatcher_returning("ok"),
            )
        )
        resp = client.post("/api/command", json={"text": "/help"})
        assert resp.status_code == 401

    def test_wrong_token_is_unauthorized(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(
            create_app(
                process_manager=_bare_pm(),
                command_dispatcher=_dispatcher_returning("ok"),
            )
        )
        resp = client.post(
            "/api/command",
            json={"text": "/help"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_empty_token_rejects_unconditionally(self, monkeypatch) -> None:
        """If HIVE_WEB_TOKEN is empty, ALL requests must 401 — never match-anything."""
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "")
        client = TestClient(
            create_app(
                process_manager=_bare_pm(),
                command_dispatcher=_dispatcher_returning("ok"),
            )
        )
        # Even with a "Bearer " prefix matching the empty value, must reject
        resp = client.post(
            "/api/command",
            json={"text": "/help"},
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

        # And without any header
        resp2 = client.post("/api/command", json={"text": "/help"})
        assert resp2.status_code == 401


class TestSuccessPath:
    def test_valid_token_dispatches(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        dispatcher = _dispatcher_returning("hello back")
        client = TestClient(
            create_app(
                process_manager=_bare_pm(),
                command_dispatcher=dispatcher,
            )
        )
        resp = client.post(
            "/api/command",
            json={"text": "/help"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "hello back"
        dispatcher.dispatch.assert_awaited_once_with("/help", actor="web:user")

    def test_no_dispatcher_returns_placeholder(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_bare_pm()))
        resp = client.post(
            "/api/command",
            json={"text": "/help"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert "not configured" in resp.json()["text"].lower()


class TestDualWriteSuppression:
    """The dispatcher already logs entity round-trips through the bus router.

    When ``CommandResult.routed`` is True, the web endpoint must NOT call
    ``log_message`` again — otherwise the chat shows a duplicate
    ``user→hive`` / ``hive→user`` pair shadowing the real entity exchange.
    """

    def _store(self) -> MagicMock:
        store = MagicMock()
        store.log_message = AsyncMock()
        return store

    def test_routed_result_skips_log_message(self, monkeypatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=CommandResult(text="hi from dev", routed=True))
        store = self._store()
        client = TestClient(
            create_app(
                process_manager=_bare_pm(),
                command_dispatcher=dispatcher,
                message_store=store,
            )
        )
        resp = client.post(
            "/api/command",
            json={"text": "/m:dev hello"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        store.log_message.assert_not_awaited()

    def test_unrouted_result_still_logs(self, monkeypatch) -> None:
        """Non-routing commands like /help have no entity round-trip — still log."""
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=CommandResult(text="help text", routed=False))
        store = self._store()
        client = TestClient(
            create_app(
                process_manager=_bare_pm(),
                command_dispatcher=dispatcher,
                message_store=store,
            )
        )
        resp = client.post(
            "/api/command",
            json={"text": "/help"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        assert store.log_message.await_count == 2


class TestEntityResolution:
    """POST /api/command returns the correct ``entity`` field for each routing case."""

    @pytest.mark.asyncio
    async def test_plain_text_resolves_to_otter(self, monkeypatch) -> None:
        """Plain text 'hello' routes to the default maestro (otter)."""
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=CommandResult(text="hi", entity="otter"))
        app = create_app(process_manager=_bare_pm(), command_dispatcher=dispatcher)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/command",
                json={"text": "hello"},
                headers={"Authorization": "Bearer secret"},
            )
        assert resp.status_code == 200
        assert resp.json()["entity"] == "otter"

    @pytest.mark.asyncio
    async def test_m_dev_resolves_to_dev(self, monkeypatch) -> None:
        """/m:dev x routes to entity 'dev'."""
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(return_value=CommandResult(text="ok", entity="dev"))
        app = create_app(process_manager=_bare_pm(), command_dispatcher=dispatcher)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/command",
                json={"text": "/m:dev x"},
                headers={"Authorization": "Bearer secret"},
            )
        assert resp.status_code == 200
        assert resp.json()["entity"] == "dev"

    @pytest.mark.asyncio
    async def test_full_address_resolves_to_entity(self, monkeypatch) -> None:
        """A dotted address like hive_dev.backend resolves entity to hive_dev.backend."""
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock(
            return_value=CommandResult(text="ok", entity="hive_dev.backend")
        )
        app = create_app(process_manager=_bare_pm(), command_dispatcher=dispatcher)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/command",
                json={"text": "/m:hive_dev.backend check status"},
                headers={"Authorization": "Bearer secret"},
            )
        assert resp.status_code == 200
        assert resp.json()["entity"] == "hive_dev.backend"
