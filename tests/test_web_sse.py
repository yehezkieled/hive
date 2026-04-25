"""Tests for the SSE broker and ``GET /sse/notifications`` (Sprint 15 Phase 4)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hive.notifications import Notification, NotificationDispatcher
from hive.web.app import create_app
from hive.web.sse import SSEBroker, format_event


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


# ---------------------------------------------------------------------------
# SSEBroker — unit
# ---------------------------------------------------------------------------


def test_broker_starts_empty() -> None:
    assert SSEBroker().subscriber_count == 0


async def test_subscribe_returns_queue() -> None:
    b = SSEBroker()
    q = b.subscribe()
    assert isinstance(q, asyncio.Queue)
    assert b.subscriber_count == 1


async def test_unsubscribe_removes() -> None:
    b = SSEBroker()
    q = b.subscribe()
    b.unsubscribe(q)
    assert b.subscriber_count == 0


async def test_send_fans_out_to_all_subscribers() -> None:
    b = SSEBroker()
    q1, q2 = b.subscribe(), b.subscribe()
    await b.send(Notification(text="hello"))
    assert (await q1.get()).text == "hello"
    assert (await q2.get()).text == "hello"


async def test_send_with_no_subscribers_is_noop() -> None:
    b = SSEBroker()
    await b.send(Notification(text="silent"))  # must not raise


async def test_full_queue_drops_oldest() -> None:
    """A slow subscriber must not block the dispatcher."""
    b = SSEBroker(queue_size=2)
    q = b.subscribe()
    await b.send(Notification(text="a"))
    await b.send(Notification(text="b"))
    await b.send(Notification(text="c"))  # would overflow
    items = [(await q.get()).text for _ in range(2)]
    # Oldest dropped, newest preserved
    assert "c" in items


async def test_broker_implements_channel_protocol() -> None:
    """Broker should plug directly into NotificationDispatcher."""
    b = SSEBroker()
    q = b.subscribe()
    dispatcher = NotificationDispatcher()
    dispatcher.register(b)
    await dispatcher.dispatch(Notification(text="dispatch"))
    assert (await q.get()).text == "dispatch"


# ---------------------------------------------------------------------------
# format_event
# ---------------------------------------------------------------------------


def test_format_event_emits_sse_data_frame() -> None:
    n = Notification(text="hi", kind="error")
    out = format_event(n)
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    payload = json.loads(out[len("data: ") :].strip())
    assert payload["text"] == "hi"
    assert payload["kind"] == "error"
    assert "timestamp" in payload


# ---------------------------------------------------------------------------
# /sse/notifications endpoint
# ---------------------------------------------------------------------------


class TestSSEEndpoint:
    def test_requires_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_bare_pm(), sse_broker=SSEBroker()))
        resp = client.get("/sse/notifications")
        assert resp.status_code == 401

    def test_query_token_accepted_by_dependency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EventSource can't set headers — the auth dep must accept ``?token=``.

        Tested at the dependency level because TestClient's sync streaming
        response handling deadlocks on long-lived SSE streams.
        """
        from hive.web.auth import require_token

        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        # Header path
        require_token(authorization="Bearer secret", token=None)
        # Query-param path (used by EventSource)
        require_token(authorization=None, token="secret")
        # Wrong token via query param still rejects
        with pytest.raises(Exception):
            require_token(authorization=None, token="wrong")

    def test_no_broker_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("hive.web.auth.WEB_TOKEN", "secret")
        client = TestClient(create_app(process_manager=_bare_pm()))
        resp = client.get("/sse/notifications", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 503
