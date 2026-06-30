"""Web Push notification channel (Ticket 041).

The 4th :class:`~hive.notifications.dispatcher.NotificationChannel`. It delivers
*actionable* events to every stored browser push subscription via the Web Push
protocol (VAPID). Inert until VAPID keys are configured, so the channel can be
registered unconditionally and simply no-op when push is not set up.
"""

from __future__ import annotations

import asyncio
import json
import logging

from pywebpush import WebPushException, webpush

from hive.notifications.dispatcher import ALERT_KINDS, Notification

logger = logging.getLogger(__name__)


class WebPushChannel:
    """Implements the NotificationChannel protocol (async ``send``)."""

    def __init__(self, store, public_key: str, private_key: str, subject: str) -> None:
        self._store = store
        self._public_key = public_key
        self._private_key = private_key
        self._subject = subject

    async def send(self, n: Notification) -> None:
        if not (self._public_key and self._private_key):
            return
        if n.kind not in ALERT_KINDS:
            return

        d = n.data or {}
        entity = d.get("entity", "")
        # "Needs you" kinds deep-link to ?reply=<entity> (opens the chat aimed at
        # the maestro, ready to reply); "Run ended" kinds use ?focus=<entity>
        # (scrolls to + highlights that entity's card). Ticket 048.
        if n.kind == "decision_request":
            title = f"{entity} needs your decision"
            body = d.get("question", "")
            url = f"/?reply={entity}"
        elif n.kind == "mode_request":
            title = f"{entity} — approval needed"
            body = n.text
            url = f"/?reply={entity}"
        elif n.kind == "vault_action_pending":
            title = f"{entity} — vault approval"
            body = n.text
            url = f"/?reply={entity}"
        elif n.kind == "workflow_completed":
            title = f"✅ {entity} — run finished"
            body = d.get("name", "")
            url = f"/?focus={entity}&run={d.get('run_id', '')}"
        elif n.kind == "workflow_failed":
            title = f"❌ {entity} — run ended"
            body = f"{d.get('name', '')} ({d.get('status', '')})"
            url = f"/?focus={entity}&run={d.get('run_id', '')}"

        payload = json.dumps({"title": title, "body": body, "url": url})
        for sub in await self._store.all():
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload,
                    vapid_private_key=self._private_key,
                    vapid_claims={"sub": self._subject},
                )
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    await self._store.delete(sub["endpoint"])
                else:
                    logger.warning("Web push failed for %s", sub["endpoint"])
            except Exception:
                logger.exception("Web push errored for %s", sub.get("endpoint"))
