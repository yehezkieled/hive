"""Email digest channel (Sprint 15 Phase 5).

Buffers proactive notifications and flushes either when the buffer
reaches ``buffer_size`` or when ``interval_minutes`` elapse. This avoids
spamming inboxes for chatty events while still guaranteeing they
eventually arrive.

When ``smtp_host`` is unset the channel runs in *console mode*: each
flush logs the digest instead of sending it. This keeps the channel
testable on dev machines that don't have SMTP credentials wired up.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from hive.notifications import Notification

logger = logging.getLogger(__name__)


class EmailDigest:
    """Notification channel that batches events into emailed digests."""

    def __init__(
        self,
        *,
        recipient: str,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        buffer_size: int = 20,
        interval_minutes: int = 60,
        sender: smtplib.SMTP | None = None,  # injected for tests
    ) -> None:
        self.recipient = recipient
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.buffer_size = buffer_size
        self.interval_minutes = interval_minutes
        self._sender = sender
        self._buffer: list[Notification] = []
        self._last_flush_at: datetime = datetime.now(UTC)

    @property
    def console_mode(self) -> bool:
        return not self.smtp_host

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    async def send(self, notification: Notification) -> None:
        """NotificationChannel protocol: queue the event, flush if due."""
        self._buffer.append(notification)
        if self._should_flush():
            await self.flush()

    def _should_flush(self) -> bool:
        if len(self._buffer) >= self.buffer_size:
            return True
        elapsed = datetime.now(UTC) - self._last_flush_at
        return elapsed.total_seconds() >= self.interval_minutes * 60

    async def flush(self) -> None:
        """Emit the buffered events as one digest, then reset state."""
        if not self._buffer:
            return
        body = self._render_body(self._buffer)
        subject = f"[Hive] {len(self._buffer)} event(s)"
        try:
            if self.console_mode:
                logger.info("Email digest (console mode):\n%s", body)
            else:
                self._send_smtp(subject, body)
        finally:
            self._buffer.clear()
            self._last_flush_at = datetime.now(UTC)

    def _render_body(self, events: list[Notification]) -> str:
        lines = []
        for ev in events:
            ts = ev.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"[{ts}] ({ev.kind}) {ev.text}")
        return "\n".join(lines)

    def _send_smtp(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.smtp_user or "hive@localhost"
        msg["To"] = self.recipient
        msg["Subject"] = subject
        msg.set_content(body)

        if self._sender is not None:
            self._sender.send_message(msg)
            return

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as smtp:
            smtp.starttls()
            if self.smtp_user:
                smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(msg)
