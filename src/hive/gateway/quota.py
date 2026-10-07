"""Plan quota for the desk's quota chip (read-only).

The chip shows the busier of the Claude plan's two account-wide windows (5-hour and
7-day) as a percent used; tapping it shows both plus the Fable weekly window. Claude
Code's own rate limits are the authority for the two headline windows: they are read
from the rate-limits cache file another process writes (``rate_limits``), and the
popover shows the figures' age once they are older than ``STALE_AFTER_S``. Only when
that file is missing or unreadable do the headline windows come from ``quota-axi``
(used = 100 - percentRemaining); the Fable week always does. A window whose reset time
has passed is stale and is not shown; when no 5-hour or 7-day window is left the chip
says the quota is unknown. ``quota-axi`` runs with ``--no-credential-refresh`` so a
page load never renews a login. Its reads are cached and refreshed in the background; a
page waits at most ``quota_first_wait_s`` on a cold cache, so a slow or missing
``quota-axi`` never holds a page up.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive.gateway.settings import GatewaySettings

# (quota-axi window id, label, counts toward the chip's headline)
WINDOWS = (
    ("five_hour", "5-hour window", True),
    ("seven_day", "7-day window", True),
    ("model:fable", "Fable week", False),
)
WARN_AT = 60  # calm below, warn from here
HOT_ABOVE = 85  # hot above this
_MAX_OUTPUT = 1024 * 1024
# (Claude Code rate_limits key, label) for the rate-limits cache file
CACHE_WINDOWS = (("five_hour", "5-hour window"), ("seven_day", "7-day window"))
STALE_AFTER_S = 600  # older cache figures show their age in the popover
_MAX_CACHE = 64 * 1024


@dataclass(frozen=True)
class Window:
    label: str
    used: int  # percent of the window used, 0-100
    resets_at: datetime | None
    headline: bool = True  # counts toward the chip's busiest-window figure


@dataclass(frozen=True)
class Quota:
    windows: tuple[Window, ...]  # never empty
    as_of: datetime | None = None  # when the rate-limits cache was written; None: quota-axi

    @property
    def worst(self) -> Window:
        pool = [w for w in self.windows if w.headline] or list(self.windows)
        return max(pool, key=lambda w: w.used)

    def current(self, now: datetime) -> Quota | None:
        """The windows that have not reset by ``now``; None when no headline window is left."""
        live = tuple(w for w in self.windows if w.resets_at is None or w.resets_at > now)
        return Quota(live, self.as_of) if any(w.headline for w in live) else None

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


def _percent(value: object) -> float | None:
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    return float(value) if ok else None  # type: ignore[arg-type]


def _used(w: dict) -> float | None:
    """Percent used, from ``percentUsed``, else derived from ``percentRemaining``."""
    used = _percent(w.get("percentUsed"))
    if used is not None:
        return used
    left = _percent(w.get("percentRemaining"))
    return None if left is None else 100 - left


def _epoch(value: object) -> datetime | None:
    seconds = _percent(value)
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def parse_rate_limits(raw: str) -> Quota | None:
    """The headline windows from Claude Code's rate-limits cache; None when unreadable."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rate_limits"), dict):
        return None
    as_of = _epoch(data.get("ts"))
    if as_of is None:
        return None
    windows = []
    for key, label in CACHE_WINDOWS:
        w = data["rate_limits"].get(key)
        if not isinstance(w, dict):
            continue
        used, resets = _percent(w.get("used_percentage")), _epoch(w.get("resets_at"))
        if used is not None and math.isfinite(used) and 0 <= used <= 100 and resets:
            windows.append(Window(label, round(used), resets))
    return Quota(tuple(windows), as_of) if windows else None


def read_rate_limits(path: Path) -> Quota | None:
    try:
        if path.stat().st_size > _MAX_CACHE:
            return None
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_rate_limits(raw)


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
        for wid, label, headline in WINDOWS:
            w = found.get(wid)
            used = _used(w) if w else None
            if used is not None:
                windows.append(
                    Window(label, max(0, min(100, round(used))), _when(w.get("resetsAt")), headline)
                )
        if windows and any(w.headline for w in windows):
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
    """The rate-limits cache over ``quota-axi``.

    ``quota-axi`` is a stale-while-revalidate cache: only a cold read waits, and only
    briefly.
    """

    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self._value: Quota | None = None
        self._at: float | None = None
        self._task: asyncio.Task[None] | None = None

    async def _refresh(self) -> None:
        self._value = await read_quota(self._settings)
        self._at = time.monotonic()

    async def get(self) -> Quota | None:
        axi = await self._axi()
        path = self._settings.rate_limits
        claude = read_rate_limits(path) if path is not None else None
        if claude is None or claude.current(datetime.now(UTC)) is None:
            return axi
        fable = tuple(w for w in axi.windows if not w.headline) if axi else ()
        return Quota(claude.windows + fable, claude.as_of)

    async def _axi(self) -> Quota | None:
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
