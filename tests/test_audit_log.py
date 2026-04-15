"""Tests for the asyncpg-backed AuditLog."""

from hive.bus.audit_log import AuditLog


async def test_record_then_recent(audit_log: AuditLog) -> None:
    await audit_log.record(actor="user:42", action="command.status")
    rows = await audit_log.recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == "user:42"
    assert row["action"] == "command.status"
    assert row["target"] is None
    assert row["details"] is None
    assert row["timestamp"] is not None


async def test_recent_orders_newest_first(audit_log: AuditLog) -> None:
    await audit_log.record(actor="user:1", action="command.a")
    await audit_log.record(actor="user:1", action="command.b")
    await audit_log.record(actor="user:1", action="command.c")

    rows = await audit_log.recent(limit=10)
    assert [r["action"] for r in rows] == ["command.c", "command.b", "command.a"]


async def test_recent_respects_limit(audit_log: AuditLog) -> None:
    for i in range(5):
        await audit_log.record(actor="user:1", action=f"command.{i}")

    rows = await audit_log.recent(limit=3)
    assert len(rows) == 3


async def test_recent_filters_by_prefix(audit_log: AuditLog) -> None:
    await audit_log.record(actor="user:1", action="command.status")
    await audit_log.record(actor="system", action="entity.spawn", target="dev")
    await audit_log.record(actor="system", action="entity.kill", target="dev")
    await audit_log.record(actor="user:1", action="task.create", target="1")

    entity_rows = await audit_log.recent(action_prefix="entity.")
    assert len(entity_rows) == 2
    assert {r["action"] for r in entity_rows} == {"entity.spawn", "entity.kill"}

    command_rows = await audit_log.recent(action_prefix="command.")
    assert len(command_rows) == 1
    assert command_rows[0]["action"] == "command.status"


async def test_record_with_target_and_details(audit_log: AuditLog) -> None:
    await audit_log.record(
        actor="user:42",
        action="task.create",
        target="7",
        details={"title": "fix the thing", "priority": 2},
    )

    rows = await audit_log.recent(limit=1)
    row = rows[0]
    assert row["target"] == "7"
    assert row["details"] == {"title": "fix the thing", "priority": 2}


async def test_details_decodes_jsonb(audit_log: AuditLog) -> None:
    """Details must round-trip as a dict, not a string, even though
    the column is JSONB."""
    await audit_log.record(
        actor="system",
        action="entity.spawn",
        target="dev",
        details={"role": "maestro", "model": "sonnet"},
    )

    rows = await audit_log.recent(limit=1)
    details = rows[0]["details"]
    assert isinstance(details, dict)
    assert details["role"] == "maestro"
    assert details["model"] == "sonnet"


async def test_recent_empty(audit_log: AuditLog) -> None:
    rows = await audit_log.recent(limit=10)
    assert rows == []


async def test_record_ignores_dberror(audit_log: AuditLog) -> None:
    """A broken pool should not propagate — fire-and-continue contract."""

    class _BrokenPool:
        async def execute(self, *args, **kwargs):  # noqa: ANN001, ANN002
            raise RuntimeError("simulated DB failure")

    broken = AuditLog(pool=_BrokenPool())  # type: ignore[arg-type]
    # Should NOT raise, even though the pool raises.
    await broken.record(actor="system", action="entity.spawn", target="x")
