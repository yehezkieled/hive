"""Tests for AuditLog.histogram (per-namespace, per-minute counts)."""

from hive.bus.audit_log import AuditLog


async def test_histogram_returns_window_buckets(audit_log: AuditLog) -> None:
    rows = await audit_log.histogram(window_minutes=10)
    assert len(rows) == 10
    assert [r["i"] for r in rows] == list(range(10))


async def test_histogram_zero_filled_when_empty(audit_log: AuditLog) -> None:
    rows = await audit_log.histogram(window_minutes=5)
    for r in rows:
        assert r["command"] == 0
        assert r["entity"] == 0
        assert r["task"] == 0
        assert r["git"] == 0


async def test_histogram_counts_by_namespace(audit_log: AuditLog) -> None:
    await audit_log.record(actor="u", action="command.status")
    await audit_log.record(actor="u", action="command.help")
    await audit_log.record(actor="u", action="entity.spawn")
    await audit_log.record(actor="u", action="task.create")
    await audit_log.record(actor="u", action="git.commit")
    # Foreign namespace — must not be counted by any bucket column
    await audit_log.record(actor="u", action="vault.approve")

    rows = await audit_log.histogram(window_minutes=10)
    totals = {
        "command": sum(r["command"] for r in rows),
        "entity": sum(r["entity"] for r in rows),
        "task": sum(r["task"] for r in rows),
        "git": sum(r["git"] for r in rows),
    }
    assert totals == {"command": 2, "entity": 1, "task": 1, "git": 1}


async def test_histogram_recent_event_in_last_bucket(audit_log: AuditLog) -> None:
    await audit_log.record(actor="u", action="command.now")

    rows = await audit_log.histogram(window_minutes=10)
    assert rows[-1]["command"] >= 1
    # Earlier buckets must not see this event
    assert sum(r["command"] for r in rows[:-1]) == 0


async def test_histogram_old_event_in_earlier_bucket(audit_log: AuditLog) -> None:
    pool = audit_log.pool
    await pool.execute(
        """INSERT INTO audit_log (actor, action, timestamp)
           VALUES ($1, $2, NOW() - INTERVAL '8 minutes')""",
        "u",
        "command.old",
    )
    await audit_log.record(actor="u", action="command.new")

    rows = await audit_log.histogram(window_minutes=10)
    # Two events in window
    assert sum(r["command"] for r in rows) == 2
    # Newest is in last bucket
    assert rows[-1]["command"] == 1
    # Old one falls in an earlier bucket
    assert sum(r["command"] for r in rows[:-1]) == 1


async def test_histogram_excludes_events_outside_window(audit_log: AuditLog) -> None:
    pool = audit_log.pool
    await pool.execute(
        """INSERT INTO audit_log (actor, action, timestamp)
           VALUES ($1, $2, NOW() - INTERVAL '2 hours')""",
        "u",
        "command.long_ago",
    )

    rows = await audit_log.histogram(window_minutes=10)
    assert sum(r["command"] for r in rows) == 0
