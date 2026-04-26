"""In-process SSE broker (Sprint 15 Phase 4).

Implements the :class:`hive.notifications.NotificationChannel` protocol so
the :class:`NotificationDispatcher` can fan an event out to every
connected browser tab via Server-Sent Events.

Each subscriber owns a bounded :class:`asyncio.Queue`. If a subscriber
falls behind (slow client, dropped connection, page sleeping) the broker
drops the oldest pending event rather than blocking the dispatcher —
notifications are best-effort and we never want a stuck tab to delay
Telegram or email delivery.
"""

from __future__ import annotations

import asyncio
import json
import logging

from hive.notifications import Notification

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 100


class SSEBroker:
    """Pub/sub for proactive notifications, exposed to the browser via SSE."""

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: list[asyncio.Queue[Notification]] = []

    def subscribe(self) -> asyncio.Queue[Notification]:
        """Register a new subscriber and return its queue."""
        q: asyncio.Queue[Notification] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue[Notification]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def send(self, notification: Notification) -> None:
        """NotificationChannel protocol — fan out to every subscriber."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(notification)
            except asyncio.QueueFull:
                # Slow consumer: drop the oldest event and re-queue.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(notification)
                except asyncio.QueueFull:
                    logger.warning("SSE subscriber queue still full after drop; skipping event")


def format_event(notification: Notification) -> str:
    """Render a :class:`Notification` as a single SSE ``data:`` frame."""
    payload = json.dumps(
        {
            "text": notification.text,
            "kind": notification.kind,
            "data": notification.data,
            "timestamp": notification.timestamp.isoformat(),
        }
    )
    return f"data: {payload}\n\n"
