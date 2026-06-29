"""Notification fan-out — replaces the single bridge callback.

Channels (Telegram bridge, SSE broker, email digest) implement a tiny
``send(notification)`` protocol and register with the
:class:`NotificationDispatcher`. The :class:`ProcessManager` calls
``dispatcher.dispatch(notification)`` from each fan-out point, and the
dispatcher delivers to every registered channel with per-channel error
isolation.
"""

from hive.notifications.dispatcher import (
    ALERT_KINDS,
    Notification,
    NotificationChannel,
    NotificationDispatcher,
)
from hive.notifications.email import EmailDigest
from hive.notifications.web_push import WebPushChannel

__all__ = [
    "ALERT_KINDS",
    "EmailDigest",
    "Notification",
    "NotificationChannel",
    "NotificationDispatcher",
    "WebPushChannel",
]
