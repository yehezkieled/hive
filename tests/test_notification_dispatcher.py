"""Tests for :class:`hive.notifications.NotificationDispatcher`.

Phase 2 of Sprint 15 — replaces the bridge's single-callback pattern.
"""

from __future__ import annotations

from hive.notifications import Notification, NotificationDispatcher


class _RecordingChannel:
    def __init__(self) -> None:
        self.received: list[Notification] = []

    async def send(self, notification: Notification) -> None:
        self.received.append(notification)


class _FailingChannel:
    """Always raises — used to verify error isolation."""

    async def send(self, notification: Notification) -> None:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Notification dataclass
# ---------------------------------------------------------------------------


def test_notification_defaults_are_set() -> None:
    n = Notification(text="hello")
    assert n.text == "hello"
    assert n.kind == "info"
    assert n.timestamp is not None


def test_notification_kind_is_overridable() -> None:
    n = Notification(text="boom", kind="error")
    assert n.kind == "error"


# ---------------------------------------------------------------------------
# Dispatcher behavior
# ---------------------------------------------------------------------------


async def test_dispatcher_starts_empty() -> None:
    d = NotificationDispatcher()
    assert d.channel_count == 0


async def test_register_adds_channel() -> None:
    d = NotificationDispatcher()
    ch = _RecordingChannel()
    d.register(ch)
    assert d.channel_count == 1


async def test_register_is_idempotent() -> None:
    d = NotificationDispatcher()
    ch = _RecordingChannel()
    d.register(ch)
    d.register(ch)
    assert d.channel_count == 1


async def test_unregister_removes_channel() -> None:
    d = NotificationDispatcher()
    ch = _RecordingChannel()
    d.register(ch)
    d.unregister(ch)
    assert d.channel_count == 0


async def test_dispatch_with_no_channels_is_noop() -> None:
    d = NotificationDispatcher()
    await d.dispatch(Notification(text="silent"))  # must not raise


async def test_dispatch_fans_out_to_all_channels() -> None:
    d = NotificationDispatcher()
    a, b, c = _RecordingChannel(), _RecordingChannel(), _RecordingChannel()
    for ch in (a, b, c):
        d.register(ch)
    await d.dispatch(Notification(text="multi"))
    assert [r.text for r in a.received] == ["multi"]
    assert [r.text for r in b.received] == ["multi"]
    assert [r.text for r in c.received] == ["multi"]


async def test_failing_channel_does_not_block_others() -> None:
    """A raising channel must not prevent other channels from receiving."""
    d = NotificationDispatcher()
    bad = _FailingChannel()
    good = _RecordingChannel()
    d.register(bad)
    d.register(good)
    await d.dispatch(Notification(text="resilient"))
    assert [r.text for r in good.received] == ["resilient"]


async def test_dispatch_preserves_kind_and_text() -> None:
    d = NotificationDispatcher()
    ch = _RecordingChannel()
    d.register(ch)
    await d.dispatch(Notification(text="alert", kind="error"))
    assert ch.received[0].text == "alert"
    assert ch.received[0].kind == "error"
