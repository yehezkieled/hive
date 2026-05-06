"""W2 dashboard health probes — Sprint 24 phase 1.

A `HealthMonitor` polls 5 subsystem probes on a 60-second tick, persists each
sample to ``health_log``, and exposes ``snapshot()`` for the dashboard view
model. The view-model path stays fully in-memory (no DB round-trip per request)
because each tick updates a per-subsystem ring buffer.

Probes return ``(status, summary)``. ``status`` is one of ``ok | warn | crit``;
``summary`` is a short human-readable line shown next to the bar chart.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

ProbeFunc = Callable[[], Awaitable[tuple[str, str]]]

_DISPLAY_NAMES = {
    "claude_api": "claude api",
}

_SUBSYSTEMS = ("orchestrator", "postgres", "claude_api", "heartbeat", "disk")


class HealthMonitor:
    """Polls subsystem probes, persists results, snapshots for dashboard."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        bridge: Any | None = None,
        process_manager: Any | None = None,
        data_dir: Path | str = "data/",
        tick_seconds: int = 60,
    ) -> None:
        self.pool = pool
        self.bridge = bridge
        self.process_manager = process_manager
        self.data_dir = Path(data_dir)
        self.tick_seconds = tick_seconds
        self._cache: dict[str, deque[dict]] = {s: deque(maxlen=60) for s in _SUBSYSTEMS}
        self._stopped = asyncio.Event()

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Loop probing every ``tick_seconds`` until ``stop_event`` (or own stop) fires."""
        await self._hydrate_cache()
        while not self._stopped.is_set() and (stop_event is None or not stop_event.is_set()):
            try:
                await self.tick()
            except Exception:
                logger.exception("HealthMonitor tick failed")
            try:
                wait_for = stop_event if stop_event is not None else self._stopped
                await asyncio.wait_for(wait_for.wait(), timeout=self.tick_seconds)
                break
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()

    async def tick(self) -> None:
        """Run all probes once, persist + cache results, prune retention window."""
        now = datetime.now(UTC)
        for subsystem, probe in self._probes().items():
            try:
                status, summary = await probe()
            except Exception as e:
                status, summary = "crit", f"probe error: {type(e).__name__}"
                logger.exception("Probe %s failed", subsystem)
            await self._persist(subsystem, status, summary, now)
            self._cache[subsystem].append({"status": status, "summary": summary, "ts": now})
        await self._prune()

    def snapshot(self) -> list[dict]:
        """Return W2 widget data: 5 rows × {name, summary, bars[60], lit}."""
        out: list[dict] = []
        for subsystem in _SUBSYSTEMS:
            samples = list(self._cache[subsystem])
            bars = [s["status"] for s in samples]
            if len(bars) < 60:
                bars = ["ok"] * (60 - len(bars)) + bars
            else:
                bars = bars[-60:]
            latest_summary = samples[-1]["summary"] if samples else "—"
            lit = sum(1 for b in bars if b != "ok")
            out.append(
                {
                    "name": _DISPLAY_NAMES.get(subsystem, subsystem),
                    "summary": latest_summary,
                    "bars": bars,
                    "lit": lit,
                }
            )
        return out

    def _probes(self) -> dict[str, ProbeFunc]:
        return {
            "orchestrator": self._probe_orchestrator,
            "postgres": self._probe_postgres,
            "claude_api": self._probe_claude_api,
            "heartbeat": self._probe_heartbeat,
            "disk": self._probe_disk,
        }

    async def _probe_orchestrator(self) -> tuple[str, str]:
        if self.process_manager is not None:
            count = len(self.process_manager.entities)
            return "ok", f"{count} entities"
        return "ok", "running"

    async def _probe_postgres(self) -> tuple[str, str]:
        try:
            await asyncio.wait_for(self.pool.fetchval("SELECT 1"), timeout=2.0)
            return "ok", "SELECT 1 ok"
        except Exception as e:
            return "crit", f"unreachable: {type(e).__name__}"

    async def _probe_claude_api(self) -> tuple[str, str]:
        recent_cutoff = datetime.now(UTC) - timedelta(minutes=5)
        idle_cutoff = datetime.now(UTC) - timedelta(minutes=30)
        try:
            recent = await self.pool.fetchval(
                "SELECT COUNT(*) FROM audit_log WHERE timestamp >= $1 AND action LIKE 'command.%'",
                recent_cutoff,
            )
            if recent > 0:
                return "ok", f"{recent} calls/5m"
            idle = await self.pool.fetchval(
                "SELECT COUNT(*) FROM audit_log WHERE timestamp >= $1 AND action LIKE 'command.%'",
                idle_cutoff,
            )
            if idle > 0:
                return "ok", f"idle ({idle}/30m)"
            return "warn", "no traffic 30m"
        except Exception as e:
            return "crit", f"audit query failed: {type(e).__name__}"

    async def _probe_heartbeat(self) -> tuple[str, str]:
        if self.bridge is None or not getattr(self.bridge, "heartbeat_enabled", False):
            return "ok", "disabled"
        last = getattr(self.bridge, "_last_heartbeat_at", None)
        interval = getattr(self.bridge, "heartbeat_interval_minutes", 30)
        if last is None:
            return "warn", "no fires yet"
        delta_min = (datetime.now(UTC) - last).total_seconds() / 60
        if delta_min > 2 * interval:
            return "crit", f"stale ({delta_min:.0f}m)"
        return "ok", f"{delta_min:.0f}m ago"

    async def _probe_disk(self) -> tuple[str, str]:
        try:
            usage = shutil.disk_usage(str(self.data_dir))
            pct = usage.used / usage.total * 100
            if pct >= 90:
                return "crit", f"{pct:.1f}% full"
            if pct >= 80:
                return "warn", f"{pct:.1f}% full"
            return "ok", f"{pct:.1f}% used"
        except Exception as e:
            return "crit", f"stat failed: {type(e).__name__}"

    async def _persist(self, subsystem: str, status: str, summary: str, ts: datetime) -> None:
        await self.pool.execute(
            "INSERT INTO health_log (subsystem, status, summary, ts) VALUES ($1, $2, $3, $4)",
            subsystem,
            status,
            summary,
            ts,
        )

    async def _prune(self) -> None:
        await self.pool.execute("DELETE FROM health_log WHERE ts < NOW() - INTERVAL '2 hours'")

    async def _hydrate_cache(self) -> None:
        rows = await self.pool.fetch(
            """
            SELECT subsystem, status, summary, ts
            FROM health_log
            WHERE ts >= NOW() - INTERVAL '60 minutes'
            ORDER BY ts ASC
            """
        )
        for r in rows:
            if r["subsystem"] in self._cache:
                self._cache[r["subsystem"]].append(
                    {"status": r["status"], "summary": r["summary"], "ts": r["ts"]}
                )
