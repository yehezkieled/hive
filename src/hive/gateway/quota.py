"""Plan quota for the desk's quota chip, read from ``quota-axi`` (read-only).

The chip shows the worse of the Claude plan's two account-wide windows (5-hour and
7-day) as a percent used; tapping it shows both. ``quota-axi`` runs with
``--no-credential-refresh`` so a page load never renews a login. Reads are cached and
refreshed in the background; a page waits at most ``quota_first_wait_s`` on a cold
cache, so a slow or missing ``quota-axi`` never holds a page up: the chip then says the
quota is unknown until the refresh lands.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from hive.gateway.settings import GatewaySettings

WINDOWS = (("five_hour", "5-hour window"), ("seven_day", "7-day window"))
WARN_AT = 60  # calm below, warn from here
HOT_ABOVE = 85  # hot above this
_MAX_OUTPUT = 1024 * 1024


@dataclass(frozen=True)
class Window:
    label: str
    used: int  # percent of the window used, 0-100
    resets_at: datetime | None


@dataclass(frozen=True)
class Quota:
    windows: tuple[Window, ...]  # never empty

    @property
    def worst(self) -> Window:
        return max(self.windows, key=lambda w: w.used)

    @property
    def level(self) -> str:
        used = self.worst.used
        return "hot" if used > HOT_ABOVE else "warn" if used >= WARN_AT else "ok"


def _when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_quota(raw: str) -> Quota | None:
    """The Claude row's two windows from ``quota-axi --json``; None when absent."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    providers = data.get("providers") if isinstance(data, dict) else None
    for row in providers if isinstance(providers, list) else []:
        if not isinstance(row, dict) or row.get("provider") != "claude":
            continue
        found = {
            w.get("id"): w
            for w in (row.get("windows") if isinstance(row.get("windows"), list) else [])
            if isinstance(w, dict)
        }
        windows = []
        for wid, label in WINDOWS:
            w = found.get(wid)
            left = w.get("percentRemaining") if w else None
            if isinstance(left, int | float) and not isinstance(left, bool):
                used = max(0, min(100, round(100 - left)))
                windows.append(Window(label, used, _when(w.get("resetsAt"))))
        if windows:
            return Quota(tuple(windows))
    return None


async def read_quota(settings: GatewaySettings) -> Quota | None:
    binary = settings.quota_axi
    if binary is None or not binary.is_file():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "--provider",
            "claude",
            "--json",
            "--no-credential-refresh",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(os.environ),
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), settings.quota_timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    if proc.returncode != 0 or len(out) > _MAX_OUTPUT:
        return None
    return parse_quota(out.decode("utf-8", "replace"))


class QuotaProvider:
    """Stale-while-revalidate cache: only a cold read waits, and only briefly."""

    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self._value: Quota | None = None
        self._at: float | None = None
        self._task: asyncio.Task[None] | None = None

    async def _refresh(self) -> None:
        self._value = await read_quota(self._settings)
        self._at = time.monotonic()

    async def get(self) -> Quota | None:
        if self._settings.quota_axi is None:
            return None
        if self._at is None:
            if self._task is None or self._task.done():
                self._task = asyncio.ensure_future(self._refresh())
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._task), self._settings.quota_first_wait_s
                )
            except TimeoutError:
                return None
        elif time.monotonic() - self._at > self._settings.quota_ttl_s and (
            self._task is None or self._task.done()
        ):
            self._task = asyncio.ensure_future(self._refresh())
        return self._value
