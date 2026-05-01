"""Tests for Sprint 19 Phase 3b — preemption as last-resort safety net.

The maestro is the primary capacity manager via the priority scheduler.
Preemption only fires when ``spawn_entity`` is called at cap and the
new entity's priority is strictly better than some RUNNING entity.
These tests cover the safety-net additions: default-maestro exemption,
retry-after-preempt in ``spawn_entity``, and the env-flag gate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from hive.bus.audit_log import AuditLog
from hive.bus.router import MessageRouter
from hive.config import DEFAULT_MAESTRO
from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.notifications import NotificationDispatcher
from hive.process.manager import ProcessManager


@pytest_asyncio.fixture
async def manager(router: MessageRouter, audit_log: AuditLog) -> AsyncIterator[ProcessManager]:
    mgr = ProcessManager(
        router=router,
        max_sessions=1,
        audit_log=audit_log,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        yield mgr
    finally:
        await mgr.kill_all()


def _fake_running(mgr: ProcessManager, name: str, priority: int, role: str = "lead") -> None:
    """Plant a fake RUNNING entity (with a live session) into the manager.

    Used to drive ``active_count`` up without needing a real ``claude -p``
    subprocess — the preemption tests only care about state + priority.
    """
    if role == "maestro":
        entity = Maestro(name=name, model="sonnet")
    else:
        entity = TeamLead(name=name, maestro_name=name.split(".", 1)[0], model="sonnet")
    entity.current_priority = priority
    entity.transition_to(EntityState.STARTING)
    entity.transition_to(EntityState.RUNNING)
    mgr._entities[name] = entity
    mgr.router.register(name)
    session = AsyncMock()
    session.is_alive = True
    session.kill = AsyncMock()
    mgr._sessions[name] = session


async def test_preempt_never_kills_default_maestro(manager: ProcessManager) -> None:
    """Even at cap and worst-priority, the default maestro must survive."""
    _fake_running(manager, DEFAULT_MAESTRO, priority=4, role="maestro")

    result = await manager._preempt_for_priority(0)

    assert result is None
    assert DEFAULT_MAESTRO in manager.entities


async def test_preempt_skips_default_and_picks_next_worst(
    router: MessageRouter, audit_log: AuditLog
) -> None:
    """Default maestro is skipped — preemption picks the next-worst RUNNING entity."""
    mgr = ProcessManager(
        router=router,
        max_sessions=2,
        audit_log=audit_log,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        _fake_running(mgr, DEFAULT_MAESTRO, priority=4, role="maestro")
        _fake_running(mgr, "dev.backend", priority=3, role="lead")

        result = await mgr._preempt_for_priority(0)

        assert result == "dev.backend"
        assert DEFAULT_MAESTRO in mgr.entities
        assert "dev.backend" not in mgr.entities
    finally:
        await mgr.kill_all()


async def test_preempt_audit_logs_system_actor_and_reason(manager: ProcessManager) -> None:
    """A successful preempt writes an audit row tagged ``actor=system``."""
    _fake_running(manager, "dev.victim", priority=4, role="lead")

    result = await manager._preempt_for_priority(0)

    assert result == "dev.victim"
    events = await manager.audit_log.recent(action_prefix="entity.kill")  # type: ignore[union-attr]
    preempt_rows = [
        e
        for e in events
        if e["target"] == "dev.victim" and (e.get("details") or {}).get("reason") == "preempt"
    ]
    assert preempt_rows, f"No preempt audit row found among: {events}"
    assert preempt_rows[0]["actor"] == "system"


async def test_spawn_entity_preempts_then_retries_when_at_cap(manager: ProcessManager) -> None:
    """spawn_entity at cap should preempt a worse RUNNING entity, then succeed."""
    _fake_running(manager, "dev.victim", priority=4, role="lead")
    new_entity = TeamLead(name="dev.priority", maestro_name="dev", model="sonnet")
    new_entity.current_priority = 0  # P0 — strictly better than victim's p4

    fake_session = AsyncMock()
    fake_session.is_alive = True
    fake_session.start = AsyncMock()
    fake_session.kill = AsyncMock()

    with patch("hive.process.manager.ClaudeSession", return_value=fake_session):
        await manager.spawn_entity(new_entity)

    assert "dev.victim" not in manager.entities
    assert "dev.priority" in manager.entities
    assert manager._sessions["dev.priority"] is fake_session


async def test_spawn_entity_raises_when_no_preemption_candidate(manager: ProcessManager) -> None:
    """If no RUNNING entity is worse than the new one's priority, spawn must fail."""
    _fake_running(manager, "dev.boss", priority=0, role="lead")
    new_entity = TeamLead(name="dev.peer", maestro_name="dev", model="sonnet")
    new_entity.current_priority = 0  # tied — preemption never fires on equals

    with pytest.raises(RuntimeError, match="Max concurrent sessions"):
        await manager.spawn_entity(new_entity)
    assert "dev.boss" in manager.entities


async def test_spawn_entity_respects_preempt_disabled_flag(manager: ProcessManager) -> None:
    """With HIVE_PRIORITY_PREEMPT_ENABLED=false, spawn_entity at cap raises immediately."""
    _fake_running(manager, "dev.victim", priority=4, role="lead")
    new_entity = TeamLead(name="dev.priority", maestro_name="dev", model="sonnet")
    new_entity.current_priority = 0

    with patch("hive.config.PRIORITY_PREEMPT_ENABLED", False):
        with pytest.raises(RuntimeError, match="Max concurrent sessions"):
            await manager.spawn_entity(new_entity)

    # Victim is still running — no preemption was attempted.
    assert "dev.victim" in manager.entities


async def test_preempt_ignores_idle_entities(router: MessageRouter, audit_log: AuditLog) -> None:
    """IDLE entities don't hold a session slot; preemption must skip them."""
    mgr = ProcessManager(
        router=router,
        max_sessions=2,
        audit_log=audit_log,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        # IDLE entity with terrible priority — must be ignored.
        idle = TeamLead(name="dev.idle", maestro_name="dev", model="sonnet")
        idle.current_priority = 4
        mgr._entities["dev.idle"] = idle
        mgr.router.register("dev.idle")
        # RUNNING entity at better priority — must NOT be killed.
        _fake_running(mgr, "dev.busy", priority=2, role="lead")
        _fake_running(mgr, "dev.victim", priority=3, role="lead")

        # active_count = 2 (busy + victim) — at cap.
        result = await mgr._preempt_for_priority(0)

        # victim has the worst RUNNING priority (3) — that's the kill target.
        assert result == "dev.victim"
        assert "dev.idle" in mgr.entities  # untouched
        assert "dev.busy" in mgr.entities  # untouched
    finally:
        await mgr.kill_all()
