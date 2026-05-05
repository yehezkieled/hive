"""Tests for the Sprint 24 W2 health-probe scheduler."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from hive.observability.health_monitor import HealthMonitor


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {"dev": object(), "pa": object()}
    return pm


async def test_probe_orchestrator_counts_entities(store) -> None:
    monitor = HealthMonitor(store.pool, process_manager=_bare_pm())
    status, summary = await monitor._probe_orchestrator()
    assert status == "ok"
    assert "2 entities" in summary


async def test_probe_postgres_ok(store) -> None:
    monitor = HealthMonitor(store.pool)
    status, summary = await monitor._probe_postgres()
    assert status == "ok"
    assert summary == "SELECT 1 ok"


async def test_probe_disk_ok(store, tmp_data_dir) -> None:
    monitor = HealthMonitor(store.pool, data_dir=tmp_data_dir)
    status, summary = await monitor._probe_disk()
    assert status in {"ok", "warn", "crit"}
    assert "%" in summary


async def test_probe_heartbeat_disabled_when_no_bridge(store) -> None:
    monitor = HealthMonitor(store.pool)
    status, summary = await monitor._probe_heartbeat()
    assert status == "ok"
    assert summary == "disabled"


async def test_probe_heartbeat_warn_when_no_fires(store) -> None:
    bridge = SimpleNamespace(heartbeat_enabled=True, heartbeat_interval_minutes=30)
    monitor = HealthMonitor(store.pool, bridge=bridge)
    status, summary = await monitor._probe_heartbeat()
    assert status == "warn"
    assert "no fires" in summary


async def test_probe_heartbeat_crit_when_stale(store) -> None:
    stale = datetime.now(UTC) - timedelta(minutes=120)
    bridge = SimpleNamespace(
        heartbeat_enabled=True,
        heartbeat_interval_minutes=30,
        _last_heartbeat_at=stale,
    )
    monitor = HealthMonitor(store.pool, bridge=bridge)
    status, summary = await monitor._probe_heartbeat()
    assert status == "crit"
    assert "stale" in summary


async def test_probe_claude_api_no_traffic_warns(store) -> None:
    monitor = HealthMonitor(store.pool)
    status, summary = await monitor._probe_claude_api()
    assert status == "warn"
    assert "no traffic" in summary


async def test_probe_claude_api_recent_traffic_ok(store, audit_log) -> None:
    # Recent command audit row → status ok
    await audit_log.record(
        actor="dev", action="command.send", target="claude", details={"prompt": "hi"}
    )
    monitor = HealthMonitor(store.pool)
    status, summary = await monitor._probe_claude_api()
    assert status == "ok"
    assert "calls" in summary


async def test_tick_persists_and_caches(store, tmp_data_dir) -> None:
    monitor = HealthMonitor(store.pool, data_dir=tmp_data_dir, process_manager=_bare_pm())
    await monitor.tick()

    rows = await store.pool.fetch("SELECT subsystem, status FROM health_log ORDER BY subsystem")
    assert len(rows) == 5
    subsystems = {r["subsystem"] for r in rows}
    assert subsystems == {"orchestrator", "postgres", "claude_api", "heartbeat", "disk"}

    snapshot = monitor.snapshot()
    assert len(snapshot) == 5
    for row in snapshot:
        assert len(row["bars"]) == 60
        assert row["summary"] != "—"


async def test_snapshot_pads_short_history(store, tmp_data_dir) -> None:
    monitor = HealthMonitor(store.pool, data_dir=tmp_data_dir, process_manager=_bare_pm())
    await monitor.tick()
    snapshot = monitor.snapshot()
    assert all(len(row["bars"]) == 60 for row in snapshot)
    # First 59 are padding "ok", last is the real tick value
    for row in snapshot:
        assert row["bars"][:59] == ["ok"] * 59


async def test_snapshot_lit_counts_non_ok(store, tmp_data_dir) -> None:
    monitor = HealthMonitor(store.pool, data_dir=tmp_data_dir, process_manager=_bare_pm())
    # Inject a synthetic crit sample directly into the cache.
    monitor._cache["postgres"].append(
        {"status": "crit", "summary": "fake", "ts": datetime.now(UTC)}
    )
    snapshot = {row["name"]: row for row in monitor.snapshot()}
    assert snapshot["postgres"]["lit"] == 1


async def test_run_loop_stops_on_event(store, tmp_data_dir) -> None:
    monitor = HealthMonitor(
        store.pool,
        data_dir=tmp_data_dir,
        process_manager=_bare_pm(),
        tick_seconds=60,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(monitor.run(stop_event))
    # Give the loop one chance to run a tick before signalling stop.
    await asyncio.sleep(0.2)
    stop_event.set()
    await asyncio.wait_for(task, timeout=5.0)
    rows = await store.pool.fetch("SELECT COUNT(*) AS c FROM health_log")
    assert rows[0]["c"] >= 5  # one tick × 5 subsystems


async def test_hydrate_cache_loads_recent_rows(store, tmp_data_dir) -> None:
    # Seed the table with a row, then build a fresh monitor and assert it
    # picks the row up at startup so the bar history isn't blank after a
    # restart. health_log is not in the conftest truncate list, so clear
    # any prior rows from earlier tests in this file before seeding.
    await store.pool.execute("TRUNCATE TABLE health_log RESTART IDENTITY")
    now = datetime.now(UTC)
    await store.pool.execute(
        "INSERT INTO health_log (subsystem, status, summary, ts) VALUES ($1, $2, $3, $4)",
        "postgres",
        "warn",
        "seeded",
        now - timedelta(minutes=5),
    )
    monitor = HealthMonitor(store.pool, data_dir=tmp_data_dir)
    await monitor._hydrate_cache()
    snapshot = {row["name"]: row for row in monitor.snapshot()}
    # Latest summary for postgres reflects the seeded row.
    assert snapshot["postgres"]["summary"] == "seeded"
