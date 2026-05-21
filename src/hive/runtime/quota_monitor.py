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
_BAND_LABEL: dict[int, str] = {
    80: "Crossed 80%",
    90: "Crossed 90%",
    100: "EXHAUSTED (100%)",
}
_WINDOW_LABEL: dict[str, str] = {
    "five_hour": "5-hour window",
    "seven_day": "7-day window",
}

FetchCallable = Callable[[str, dict[str, str]], Awaitable[dict]]


@dataclass(frozen=True)
class WindowReading:
    """One quota window's current utilization and next reset."""

    utilization: float  # 0.0 – 100.0
    resets_at: datetime  # UTC


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
        self._credentials_path = credentials_path
        self._notifications = notifications
        self._poll_seconds = poll_seconds
        self._fetch: FetchCallable = fetch_callable or _default_fetch
        self._failure_threshold = failure_threshold
        self._latest: QuotaReading | None = None
        # (window_name, band) pairs already alerted in the current window cycle
        self._fired: set[tuple[str, int]] = set()
        # Last-seen resets_at per window — drives reset detection
        self._last_resets: dict[str, datetime] = {}
        # Failure tracking for the "monitor blind" meta-alert
        self._consecutive_failures: int = 0
        self._monitor_blind_alerted: bool = False
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
        for name, window in windows:
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
        if self._monitor_blind_alerted:
            await self._fire_recovery_alert()
            self._monitor_blind_alerted = False
        self._consecutive_failures = 0

    async def _record_failure(self, exc: BaseException) -> None:
        logger.warning("QuotaMonitor poll failed: %s", exc)
        self._consecutive_failures += 1
        if (
            self._consecutive_failures >= self._failure_threshold
            and not self._monitor_blind_alerted
        ):
            await self._fire_blind_alert()
            self._monitor_blind_alerted = True

    async def _fire_blind_alert(self) -> None:
        await self._notifications.dispatch(
            Notification(
                text=(
                    "QuotaMonitor — endpoint unreachable\n"
                    "Quota polling has failed continuously. "
                    "Quota alerts are offline until it recovers."
                ),
                kind="quota_monitor_blind",
            )
        )

    async def _fire_recovery_alert(self) -> None:
        await self._notifications.dispatch(
            Notification(
                text=(
                    "QuotaMonitor — back online\n"
                    "Quota polling has recovered. Alerts are active again."
                ),
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
        label = _BAND_LABEL[band]
        window_label = _WINDOW_LABEL[window_name]
        reset_str = window.resets_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        text = f"Hive quota — {window_label}\n{label}. Resets at {reset_str}."
        await self._notifications.dispatch(
            Notification(
                text=text,
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
        five_raw = data["five_hour"]
        seven_raw = data["seven_day"]
        return (
            WindowReading(
                utilization=float(five_raw["utilization"]),
                resets_at=datetime.fromisoformat(five_raw["resets_at"]),
            ),
            WindowReading(
                utilization=float(seven_raw["utilization"]),
                resets_at=datetime.fromisoformat(seven_raw["resets_at"]),
            ),
        )


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
        reset_str = window.resets_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
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
