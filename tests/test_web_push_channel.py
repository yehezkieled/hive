"""Tests for WebPushChannel — the 4th NotificationChannel (Ticket 041).

No database: a FakeStore stands in for PushSubscriptionStore, and
``hive.notifications.web_push.webpush`` is monkeypatched to a MagicMock so
nothing leaves the process. ``asyncio.to_thread`` runs the patched mock in a
worker thread; assertions on the mock still hold afterwards.
"""

import json
from unittest.mock import MagicMock

from pywebpush import WebPushException

from hive.notifications.dispatcher import Notification
from hive.notifications.web_push import WebPushChannel


class FakeStore:
    """Stand-in for PushSubscriptionStore — preset subs, records deletes."""

    def __init__(self, subs: list[dict]) -> None:
        self._subs = subs
        self.deleted: list[str] = []

    async def all(self) -> list[dict]:
        return self._subs

    async def delete(self, endpoint: str) -> None:
        self.deleted.append(endpoint)


def _sub(endpoint: str = "https://push.example/ep1") -> dict:
    return {"endpoint": endpoint, "p256dh": "p256dh-key", "auth": "auth-key"}


def _channel(store: FakeStore, public: str = "PUB", private: str = "PRIV") -> WebPushChannel:
    return WebPushChannel(store, public_key=public, private_key=private, subject="mailto:x@y.z")


def _gone_exc() -> WebPushException:
    return WebPushException("gone", response=type("R", (), {"status_code": 410})())


def _payload_of(mock: MagicMock) -> dict:
    """Extract the JSON body passed to webpush via the data= kwarg."""
    return json.loads(mock.call_args.kwargs["data"])


async def test_non_actionable_kind_does_not_send(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store)

    for kind in ("workflow_started", "info"):
        await channel.send(Notification(text="x", kind=kind))

    mock.assert_not_called()


async def test_decision_request_builds_payload(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store)

    await channel.send(
        Notification(
            text="ignored",
            kind="decision_request",
            data={"entity": "otter", "question": "which db?"},
        )
    )

    mock.assert_called_once()
    payload = _payload_of(mock)
    assert payload["title"] == "otter needs your decision"
    assert payload["body"] == "which db?"
    assert payload["url"] == "/?focus=otter"


async def test_workflow_completed_builds_payload(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store)

    await channel.send(
        Notification(
            text="ignored",
            kind="workflow_completed",
            data={"entity": "otter", "run_id": "wf_9", "name": "build"},
        )
    )

    mock.assert_called_once()
    payload = _payload_of(mock)
    assert payload["title"] == "✅ otter — run finished"
    assert payload["body"] == "build"
    assert payload["url"] == "/?focus=otter&run=wf_9"


async def test_workflow_failed_builds_payload(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store)

    await channel.send(
        Notification(
            text="ignored",
            kind="workflow_failed",
            data={
                "entity": "otter",
                "run_id": "wf_9",
                "name": "build",
                "status": "interrupted",
            },
        )
    )

    mock.assert_called_once()
    payload = _payload_of(mock)
    assert payload["title"] == "❌ otter — run ended"
    assert payload["body"] == "build (interrupted)"
    assert "run=wf_9" in payload["url"]


async def test_mode_request_sends(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store)

    await channel.send(
        Notification(
            text="bypass mode?",
            kind="mode_request",
            data={"entity": "otter"},
        )
    )

    mock.assert_called_once()
    payload = _payload_of(mock)
    assert payload["title"] == "otter — approval needed"
    assert payload["body"] == "bypass mode?"
    assert payload["url"] == "/?focus=otter"


async def test_vault_action_pending_sends(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store)

    await channel.send(
        Notification(
            text="spend $5?",
            kind="vault_action_pending",
            data={"entity": "otter"},
        )
    )

    mock.assert_called_once()
    payload = _payload_of(mock)
    assert payload["title"] == "otter — vault approval"
    assert payload["body"] == "spend $5?"
    assert payload["url"] == "/?focus=otter"


async def test_inert_without_vapid_keys(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub()])
    channel = _channel(store, public="", private="")

    await channel.send(
        Notification(
            text="x",
            kind="decision_request",
            data={"entity": "otter", "question": "which db?"},
        )
    )

    mock.assert_not_called()


async def test_gone_subscription_is_deleted(monkeypatch):
    mock = MagicMock(side_effect=_gone_exc())
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore([_sub("https://push.example/ep1"), _sub("https://push.example/ep2")])
    channel = _channel(store)

    await channel.send(
        Notification(
            text="x",
            kind="decision_request",
            data={"entity": "otter", "question": "which db?"},
        )
    )

    # Loop attempted every sub despite the first one being gone.
    assert mock.call_count == 2
    assert store.deleted == ["https://push.example/ep1", "https://push.example/ep2"]


async def test_sends_to_every_subscription(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("hive.notifications.web_push.webpush", mock)
    store = FakeStore(
        [
            _sub("https://push.example/ep1"),
            _sub("https://push.example/ep2"),
            _sub("https://push.example/ep3"),
        ]
    )
    channel = _channel(store)

    await channel.send(
        Notification(
            text="x",
            kind="decision_request",
            data={"entity": "otter", "question": "which db?"},
        )
    )

    assert mock.call_count == 3
    assert store.deleted == []
