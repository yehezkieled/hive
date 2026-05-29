"""QuotaMonitor — polls Anthropic plan-quota and dispatches threshold alerts.

See `docs/adr/0002-quota-from-undocumented-oauth-endpoint.md` for the data
source decision, and `docs/plans/2026-05-20-quota-monitor.md` for the design.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive.notifications.dispatcher import Notification, NotificationDispatcher

logger = logging.getLogger(__name__)

_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_BETA_HEADER = "oauth-2025-04-20"

# Alert bands as percentages. Same set is applied to both quota windows.
BANDS: tuple[int, ...] = (80, 90, 100)

_BAND_KIND: dict[int, str] = {
    80: "quota_warn",
    90: "quota_urgent",
    100: "quota_exhausted",
}

FetchCallable = Callable[[str, dict[str, str]], Awaitable[dict]]


@dataclass(frozen=True)
class WindowReading:
    """One quota window's current utilization and next reset.

    ``resets_at`` is ``None`` when the upstream reports no reset clock for the
    window — happens at window rollover with zero usage. Treated as "not
    started" in rendered text; never fabricated.
    """

    utilization: float  # 0.0 – 100.0
    resets_at: datetime | None  # UTC, or None when upstream omits it


@dataclass(frozen=True)
class QuotaReading:
    """A snapshot of plan-quota state across both rolling windows."""

    five_hour: WindowReading
    seven_day: WindowReading
    fetched_at: datetime  # UTC, time the upstream call succeeded


class QuotaMonitor:
    """Polls Anthropic plan-quota and dispatches threshold alerts."""

    def __init__(
        self,
        credentials_path: Path,
        notifications: NotificationDispatcher,
        poll_seconds: float = 180.0,
        fetch_callable: FetchCallable | None = None,
        failure_threshold: int = 5,
    ) -> None:
        from hive.runtime.quota_state import QuotaState

        self._credentials_path = credentials_path
        self._notifications = notifications
        self._poll_seconds = poll_seconds
        self._fetch: FetchCallable = fetch_callable or _default_fetch
        self._latest: QuotaReading | None = None
        # (window_name, band) pairs already alerted in the current window cycle
        self._fired: set[tuple[str, int]] = set()
        # Last-seen resets_at per window — drives reset detection.
        # Only populated when upstream gave a real timestamp.
        self._last_resets: dict[str, datetime] = {}
        # Symmetric-debounce state machine for blind/recovered transitions.
        self._state = QuotaState(threshold=failure_threshold)
        # Background-loop task handle
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the background polling loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="quota-monitor-loop")

    async def stop(self) -> None:
        """Cancel the polling loop cleanly. Idempotent."""
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        """Background loop — poll forever until cancelled."""
        while True:
            try:
                await self.poll_once()
            except Exception:  # defense-in-depth — poll_once already catches
                logger.exception("QuotaMonitor poll cycle errored unexpectedly")
            await asyncio.sleep(self._poll_seconds)

    async def poll_once(self) -> None:
        """One poll cycle. Never raises — failures are logged and counted."""
        try:
            await self._poll_inner()
        except Exception as exc:
            await self._record_failure(exc)
        else:
            await self._record_success()

    async def _poll_inner(self) -> None:
        """The risky body — read token, fetch, parse, alert, store."""
        token = self._read_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _BETA_HEADER,
        }
        data = await self._fetch(_USAGE_URL, headers)
        five, seven = self._parse_windows(data)
        windows = (("five_hour", five), ("seven_day", seven))

        # Clear fired-bands for any window whose resets_at has advanced.
        # Null resets_at carries no signal — keep the previous timestamp until
        # upstream provides a real one again.
        for name, window in windows:
            if window.resets_at is None:
                continue
            prev = self._last_resets.get(name)
            if prev is not None and window.resets_at > prev:
                self._fired = {(n, b) for (n, b) in self._fired if n != name}
            self._last_resets[name] = window.resets_at

        # Fire alerts for any new threshold crossings.
        for name, window in windows:
            await self._check_thresholds(name, window)

        self._latest = QuotaReading(
            five_hour=five,
            seven_day=seven,
            fetched_at=datetime.now(UTC),
        )

    async def _record_success(self) -> None:
        if self._state.record_success() == "recovered":
            await self._fire_recovery_alert()

    async def _record_failure(self, exc: BaseException) -> None:
        logger.warning("QuotaMonitor poll failed: %s", exc)
        if self._state.record_failure() == "blind":
            await self._fire_blind_alert()

    async def _fire_blind_alert(self) -> None:
        from hive.runtime.quota_alerts import format_unreachable_alert

        await self._notifications.dispatch(
            Notification(
                text=format_unreachable_alert(self._latest, now=datetime.now(UTC)),
                kind="quota_monitor_blind",
            )
        )

    async def _fire_recovery_alert(self) -> None:
        from hive.runtime.quota_alerts import format_recovery_alert

        # _latest is guaranteed set: recovery only fires after N successful polls.
        assert self._latest is not None
        await self._notifications.dispatch(
            Notification(
                text=format_recovery_alert(self._latest),
                kind="quota_monitor_recovered",
            )
        )

    async def _check_thresholds(self, window_name: str, window: WindowReading) -> None:
        crossed = [b for b in BANDS if window.utilization >= b]
        newly = [b for b in crossed if (window_name, b) not in self._fired]
        if not newly:
            return
        top = max(newly)
        await self._fire_alert(window_name, top, window)
        # Mark *all* crossed bands fired so lower bands can't fire later.
        for b in crossed:
            self._fired.add((window_name, b))

    async def _fire_alert(self, window_name: str, band: int, window: WindowReading) -> None:
        from hive.runtime.quota_alerts import format_band_alert

        await self._notifications.dispatch(
            Notification(
                text=format_band_alert(window_name, band, window),
                kind=_BAND_KIND[band],
                data={
                    "window": window_name,
                    "band": band,
                    "utilization": window.utilization,
                },
            )
        )

    def get_quota(self) -> QuotaReading | None:
        """Latest successful reading, or None if none has landed yet."""
        return self._latest

    def _read_token(self) -> str:
        data = json.loads(self._credentials_path.read_text())
        return str(data["claudeAiOauth"]["accessToken"])

    @staticmethod
    def _parse_windows(data: dict) -> tuple[WindowReading, WindowReading]:
        return (
            QuotaMonitor._parse_one_window(data["five_hour"]),
            QuotaMonitor._parse_one_window(data["seven_day"]),
        )

    @staticmethod
    def _parse_one_window(raw: dict) -> WindowReading:
        resets_raw = raw["resets_at"]
        resets_at = datetime.fromisoformat(resets_raw) if resets_raw is not None else None
        return WindowReading(utilization=float(raw["utilization"]), resets_at=resets_at)


def format_quota_text(
    reading: QuotaReading | None,
    *,
    now: datetime,
    stale_after_seconds: float,
) -> str:
    """Render the on-demand `/quota` response text.

    Pure function — no I/O, no clock reads. Caller supplies `now` and the
    staleness threshold (typically 2× poll interval).
    """
    if reading is None:
        return "Hive quota — no reading yet. Try again in a moment."

    age_seconds = (now - reading.fetched_at).total_seconds()
    stale_note = ""
    if age_seconds > stale_after_seconds:
        minutes = int(age_seconds / 60)
        stale_note = f" (reading {minutes} min old — endpoint may be down)"

    def _line(label: str, window: WindowReading) -> str:
        reset_str = (
            window.resets_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            if window.resets_at is not None
            else "not started"
        )
        return f"{label}: {window.utilization:.0f}%, resets {reset_str}"

    return (
        f"Hive quota{stale_note}\n"
        f"{_line('5-hour', reading.five_hour)}\n"
        f"{_line('7-day', reading.seven_day)}"
    )


async def _default_fetch(url: str, headers: dict[str, str]) -> dict:
    """Production fetch — synchronous urllib wrapped via asyncio.to_thread."""

    def _blocking() -> dict:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - trusted URL
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.to_thread(_blocking)
