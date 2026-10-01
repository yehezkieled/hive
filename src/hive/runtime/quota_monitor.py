"""QuotaMonitor — polls Anthropic plan-quota and dispatches threshold alerts.

See `docs/adr/0002-quota-from-undocumented-oauth-endpoint.md` for the data
source decision, and `docs/plans/2026-05-20-quota-monitor.md` for the design.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import urllib.error
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

# OAuth refresh — same undocumented surface as the usage endpoint (ADR 0002).
# [UNSURE] endpoint and client id are the ones Claude Code itself uses, taken
# from community sources, not an Anthropic doc.
_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
# Refresh this long before expiry so a poll never races the cutoff.
_REFRESH_SKEW_SECONDS = 300.0

FetchCallable = Callable[[str, dict[str, str]], Awaitable[dict]]
# (refresh_token) -> {"access_token", "refresh_token"?, "expires_in"?}
RefreshCallable = Callable[[str], Awaitable[dict]]


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
        refresh_callable: RefreshCallable | None = None,
    ) -> None:
        from hive.runtime.quota_state import QuotaState

        self._credentials_path = credentials_path
        self._notifications = notifications
        self._poll_seconds = poll_seconds
        self._fetch: FetchCallable = fetch_callable or _default_fetch
        self._refresh: RefreshCallable = refresh_callable or _default_refresh
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
        token = await self._fresh_token()
        try:
            data = await self._fetch(_USAGE_URL, self._headers(token))
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            # Token rejected despite looking valid (revoked / clock skew):
            # force one refresh and retry once.
            token = await self._fresh_token(force=True)
            data = await self._fetch(_USAGE_URL, self._headers(token))
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

    def exhausted_until(self, now: datetime | None = None) -> datetime | None:
        """Reset time of the quota wall if one is up right now, else None.

        A wall is a window at 100% whose ``resets_at`` is still in the future.
        With both windows spent, the later reset is when turns can resume. A
        stale 100% reading self-clears once ``resets_at`` passes, so a paused
        caller resumes on its own without waiting for the next poll.
        """
        if self._latest is None:
            return None
        now = now or datetime.now(UTC)
        walls = [
            w.resets_at
            for w in (self._latest.five_hour, self._latest.seven_day)
            if w.utilization >= 100 and w.resets_at is not None and w.resets_at > now
        ]
        return max(walls) if walls else None

    async def wall_after_timeout(self) -> datetime | None:
        """Re-poll, then report the wall — for a turn that just timed out.

        The cached reading can be up to a poll interval old, so a wall that
        went up mid-turn would be missed without a fresh read.
        """
        await self.poll_once()
        return self.exhausted_until()

    def get_quota(self) -> QuotaReading | None:
        """Latest successful reading, or None if none has landed yet."""
        return self._latest

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "anthropic-beta": _BETA_HEADER}

    def _read_oauth(self) -> dict:
        return json.loads(self._credentials_path.read_text())["claudeAiOauth"]

    async def _fresh_token(self, *, force: bool = False) -> str:
        """Access token, refreshed first when expired / near expiry (or forced).

        Re-reads the credentials file right before refreshing: Claude Code
        refreshes the same file while sessions run, and refresh tokens rotate,
        so if it already did, use its token rather than burning ours.
        """
        oauth = self._read_oauth()
        expires_ms = oauth.get("expiresAt")
        near_expiry = (
            expires_ms is not None
            and expires_ms / 1000.0 - datetime.now(UTC).timestamp() < _REFRESH_SKEW_SECONDS
        )
        if not (force or near_expiry):
            return str(oauth["accessToken"])
        refresh_token = oauth.get("refreshToken")
        if not refresh_token:
            if force:
                raise RuntimeError("OAuth token rejected and no refresh token on file")
            return str(oauth["accessToken"])  # let the fetch decide
        granted = await self._refresh(str(refresh_token))
        self._write_oauth(granted)
        logger.info("QuotaMonitor refreshed the OAuth access token")
        return str(granted["access_token"])

    def _write_oauth(self, granted: dict) -> None:
        """Persist a refresh grant into the credentials file atomically."""
        creds = json.loads(self._credentials_path.read_text())
        oauth = creds["claudeAiOauth"]
        oauth["accessToken"] = granted["access_token"]
        if granted.get("refresh_token"):
            oauth["refreshToken"] = granted["refresh_token"]
        if granted.get("expires_in") is not None:
            expires_at = datetime.now(UTC).timestamp() + float(granted["expires_in"])
            oauth["expiresAt"] = int(expires_at * 1000)
        fd, tmp = tempfile.mkstemp(dir=self._credentials_path.parent, prefix=".creds-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(creds, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._credentials_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

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


async def _default_refresh(refresh_token: str) -> dict:
    """Production refresh — POST the refresh_token grant."""

    def _blocking() -> dict:
        body = json.dumps(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _OAUTH_CLIENT_ID,
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - trusted URL
            _TOKEN_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.to_thread(_blocking)
