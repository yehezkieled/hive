"""Tests for the EmailDigest notification channel (Sprint 15 Phase 5)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from hive.notifications import EmailDigest, Notification, NotificationDispatcher

# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------


def test_console_mode_when_smtp_host_unset() -> None:
    d = EmailDigest(recipient="me@example.com")
    assert d.console_mode is True


def test_smtp_mode_when_host_set() -> None:
    d = EmailDigest(recipient="me@example.com", smtp_host="mail.example.com")
    assert d.console_mode is False


# ---------------------------------------------------------------------------
# Buffering
# ---------------------------------------------------------------------------


async def test_send_appends_to_buffer() -> None:
    d = EmailDigest(recipient="me@example.com", buffer_size=5, interval_minutes=60)
    await d.send(Notification(text="one"))
    assert d.buffered_count == 1
    await d.send(Notification(text="two"))
    assert d.buffered_count == 2


async def test_buffer_size_triggers_flush() -> None:
    d = EmailDigest(recipient="me@example.com", buffer_size=2, interval_minutes=60)
    await d.send(Notification(text="a"))
    await d.send(Notification(text="b"))  # should hit the size trigger
    assert d.buffered_count == 0


async def test_interval_triggers_flush() -> None:
    d = EmailDigest(recipient="me@example.com", buffer_size=100, interval_minutes=60)
    # Pretend the last flush happened 2 hours ago
    d._last_flush_at = datetime.now(UTC) - timedelta(hours=2)
    await d.send(Notification(text="late"))
    assert d.buffered_count == 0


async def test_flush_no_op_when_empty() -> None:
    d = EmailDigest(recipient="me@example.com")
    await d.flush()  # must not raise


# ---------------------------------------------------------------------------
# Console-mode output
# ---------------------------------------------------------------------------


async def test_console_mode_logs_digest(caplog) -> None:
    d = EmailDigest(recipient="me@example.com", buffer_size=2, interval_minutes=60)
    with caplog.at_level(logging.INFO, logger="hive.notifications.email"):
        await d.send(Notification(text="hello", kind="info"))
        await d.send(Notification(text="boom", kind="error"))
    digest_logs = [r for r in caplog.records if "Email digest" in r.message]
    assert digest_logs, "expected a digest log line"
    assert "hello" in digest_logs[0].message
    assert "boom" in digest_logs[0].message


# ---------------------------------------------------------------------------
# SMTP path (with injected sender mock)
# ---------------------------------------------------------------------------


async def test_smtp_send_uses_injected_sender() -> None:
    sender = MagicMock()
    d = EmailDigest(
        recipient="me@example.com",
        smtp_host="mail.example.com",
        smtp_user="user",
        smtp_password="pw",
        buffer_size=1,
        interval_minutes=60,
        sender=sender,
    )
    await d.send(Notification(text="urgent", kind="error"))
    sender.send_message.assert_called_once()
    msg = sender.send_message.call_args.args[0]
    assert msg["To"] == "me@example.com"
    assert "urgent" in msg.get_content()


async def test_smtp_failure_clears_buffer(caplog) -> None:
    """Even if SMTP fails, the buffer must clear so we don't grow unbounded."""
    sender = MagicMock()
    sender.send_message.side_effect = RuntimeError("smtp down")
    d = EmailDigest(
        recipient="me@example.com",
        smtp_host="mail.example.com",
        buffer_size=1,
        interval_minutes=60,
        sender=sender,
    )
    try:
        await d.send(Notification(text="x"))
    except RuntimeError:
        pass
    assert d.buffered_count == 0


# ---------------------------------------------------------------------------
# Channel-protocol integration
# ---------------------------------------------------------------------------


async def test_plugs_into_notification_dispatcher() -> None:
    d = EmailDigest(recipient="me@example.com", buffer_size=1, interval_minutes=60)
    dispatcher = NotificationDispatcher()
    dispatcher.register(d)
    await dispatcher.dispatch(Notification(text="dispatched"))
    # buffer_size=1 → first send flushes immediately
    assert d.buffered_count == 0
