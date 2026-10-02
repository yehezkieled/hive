"""Web Push for the desk (ADR 0026's channel, standalone: the gateway has no Hive core).

VAPID keys and the owner's push subscriptions live under ``settings.data_dir`` (outside the
repo, files mode 0600). Keys are generated on first use. Only the owner can subscribe, since
the subscribe route sits behind the same owner/Host/Origin checks as every other route.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

log = logging.getLogger("hive.gateway.push")
MAX_SUBSCRIPTIONS = 20
_B64_RE = re.compile(r"^[A-Za-z0-9_=-]{8,256}$")


@dataclass(frozen=True)
class Alert:
    key: str  # dedupe key: one push per key
    title: str
    body: str
    url: str = "/"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def valid_subscription(sub: object) -> dict | None:
    """The subscription the browser produced, reduced to the fields we use; None if bad."""
    if not isinstance(sub, dict):
        return None
    endpoint = sub.get("endpoint")
    keys = sub.get("keys")
    if not isinstance(endpoint, str) or len(endpoint) > 1024 or not isinstance(keys, dict):
        return None
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname:
        return None
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str)):
        return None
    if not (_B64_RE.fullmatch(p256dh) and _B64_RE.fullmatch(auth)):
        return None
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


class PushService:
    def __init__(self, data_dir: Path, subject: str) -> None:
        self._dir = data_dir
        self._subject = subject
        self._lock = threading.Lock()
        self._vapid: Vapid01 | None = None

    @property
    def _key_file(self) -> Path:
        return self._dir / "vapid-private.pem"

    @property
    def _subs_file(self) -> Path:
        return self._dir / "subscriptions.json"

    def _load_vapid(self) -> Vapid01:
        with self._lock:
            if self._vapid is None:
                if not self._key_file.is_file():
                    vapid = Vapid01()
                    vapid.generate_keys()
                    pem = vapid.private_pem().decode()
                    _write_private(self._key_file, pem)
                self._vapid = Vapid01.from_file(str(self._key_file))
            return self._vapid

    def public_key(self) -> str:
        """The applicationServerKey the browser subscribes with (URL-safe base64)."""
        raw = self._load_vapid().public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def subscriptions(self) -> list[dict]:
        with self._lock:
            try:
                data = json.loads(self._subs_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
        subs = [valid_subscription(s) for s in data] if isinstance(data, list) else []
        return [s for s in subs if s]

    def _save(self, subs: list[dict]) -> None:
        with self._lock:
            _write_private(self._subs_file, json.dumps(subs))

    def add(self, sub: dict) -> bool:
        subs = [s for s in self.subscriptions() if s["endpoint"] != sub["endpoint"]]
        if len(subs) >= MAX_SUBSCRIPTIONS:
            return False
        self._save([*subs, sub])
        return True

    def remove(self, endpoint: str) -> None:
        self._save([s for s in self.subscriptions() if s["endpoint"] != endpoint])

    def _send_one(self, sub: dict, payload: str) -> None:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=self._load_vapid(),
            vapid_claims={"sub": self._subject},
            ttl=3600,
            timeout=10,
        )

    async def notify_one(self, sub: dict, title: str, body: str) -> None:
        payload = json.dumps({"title": title, "body": body, "url": "/", "tag": "hive-desk"})
        try:
            await asyncio.to_thread(self._send_one, sub, payload)
        except Exception:
            log.warning("welcome push failed")

    async def notify(self, alert: Alert) -> int:
        """Push one alert to every subscription; prune the ones the service reports gone."""
        subs = self.subscriptions()
        if not subs:
            return 0
        payload = json.dumps(
            {"title": alert.title, "body": alert.body, "url": alert.url, "tag": alert.key}
        )
        sent = 0
        for sub in subs:
            try:
                await asyncio.to_thread(self._send_one, sub, payload)
                sent += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    self.remove(sub["endpoint"])
                else:
                    log.warning("web push failed status=%s", status)
            except Exception:
                log.exception("web push errored")
        return sent
