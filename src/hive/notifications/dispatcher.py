"""Channel-based notification dispatcher (Sprint 15)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    """A single proactive event the orchestrator wants to surface.

    ``kind`` drives per-channel routing: the Web Push channel and the
    Telegram alert toggle both filter on it (see ``ALERT_KINDS``). SSE and
    email still receive every event. ``data`` carries optional structured
    payload — used by the web UI to render interactive bubbles (e.g.
    mode-request Allow/Deny) and by Web Push to build the deep-link.
    """

    text: str
    kind: str = "info"
    data: dict | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# The notification kinds worth pushing to a human's lock screen — the
# "actionable" set (Ticket 041, ADR 0026). The Web Push channel delivers exactly
# these, and the Telegram bridge suppresses exactly these when alerts are turned
# down (HIVE_TELEGRAM_ALERTS=false). Single source of truth so the two surfaces
# can't drift.
ALERT_KINDS = frozenset(
    {
        "decision_request",
        "mode_request",
        "vault_action_pending",
        "workflow_completed",
        "workflow_failed",
    }
)


@runtime_checkable
class NotificationChannel(Protocol):
    """Anything that can receive a notification.

    Telegram bridge, SSE broker, and email digest all implement this.
    """

    async def send(self, notification: Notification) -> None: ...


class NotificationDispatcher:
    """Fan-out registry — owns the list of channels and delivers events.

    A failing channel is logged and skipped; the others still receive
    the notification. This is important because the email digest can
    fail silently for hours without taking down Telegram alerts.
    """

    def __init__(self) -> None:
        self._channels: list[NotificationChannel] = []

    def register(self, channel: NotificationChannel) -> None:
        if channel not in self._channels:
            self._channels.append(channel)

    def unregister(self, channel: NotificationChannel) -> None:
        if channel in self._channels:
            self._channels.remove(channel)

    @property
    def channel_count(self) -> int:
        return len(self._channels)

    async def dispatch(self, notification: Notification) -> None:
        for channel in list(self._channels):
            try:
                await channel.send(notification)
            except Exception:
                logger.exception(
                    "Notification channel %s failed",
                    type(channel).__name__,
                )
