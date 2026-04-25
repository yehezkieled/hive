"""Tests for Sprint 12 Phase 4 — auto-recovery on task failures.

Covers:
- ``Task`` dataclass retry columns round-trip through the store.
- ``TaskStore.increment_retry`` / ``update_failure`` persist.
- ``ProcessManager.handle_task_failure`` retries then escalates.
- ``report_failure`` action type is parsed.
- Hierarchical escalation: worker -> lead -> maestro -> user TG notify.
- Audit rows land in the expected namespaces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from hive.bus.actions import parse_actions
from hive.bus.audit_log import AuditLog
from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.models.maestro import Maestro
from hive.models.task import TaskStatus
from hive.models.team_lead import TeamLead
from hive.models.worker import WorkerAgent
from hive.notifications import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _CapturingChannel:
    """Test channel that records every notification it receives."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, notification: Notification) -> None:
        self.messages.append(notification.text)


@pytest_asyncio.fixture
async def manager(
    router: MessageRouter,
    task_store: TaskStore,
    audit_log: AuditLog,
) -> AsyncIterator[ProcessManager]:
    mgr = ProcessManager(
        router=router,
        task_store=task_store,
        audit_log=audit_log,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        yield mgr
    finally:
        await mgr.kill_all()


def _populate_org(manager: ProcessManager) -> None:
    """Register maestro/lead/worker so escalation paths are resolvable."""
    maestro = Maestro(name="dev")
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    worker = WorkerAgent(
        name="dev.backend.w1",
        team_name="backend",
        lead_name="dev.backend",
        task_id=None,
    )
    for e in (maestro, lead, worker):
        manager._entities[e.name] = e
        manager.router.register(e.name)


# ---------------------------------------------------------------------------
# Model + store
# ---------------------------------------------------------------------------


async def test_task_defaults_include_retry_fields(task_store: TaskStore) -> None:
    task = await task_store.create(title="do the thing", created_by="user")
    assert task.retry_count == 0
    assert task.max_retries == 3
    assert task.failure_reason is None


async def test_increment_retry_bumps_and_records_reason(task_store: TaskStore) -> None:
    task = await task_store.create(title="flaky", created_by="user")
    updated = await task_store.increment_retry(task.id, "TimeoutError")
    assert updated is not None
    assert updated.retry_count == 1
    assert updated.failure_reason == "TimeoutError"


async def test_increment_retry_missing_task_returns_none(task_store: TaskStore) -> None:
    assert await task_store.increment_retry(9999, "never") is None


async def test_update_failure_sets_reason_without_bumping(task_store: TaskStore) -> None:
    task = await task_store.create(title="x", created_by="user")
    await task_store.increment_retry(task.id, "first")
    final = await task_store.update_failure(task.id, "gave up")
    assert final is not None
    assert final.retry_count == 1  # unchanged
    assert final.failure_reason == "gave up"


# ---------------------------------------------------------------------------
# Action parser
# ---------------------------------------------------------------------------


async def test_report_failure_parses() -> None:
    text = """
text before
<hive_actions>
[{"type": "report_failure", "reason": "tests fail"}]
</hive_actions>
"""
    _, actions = parse_actions(text)
    assert len(actions) == 1
    assert actions[0].type == "report_failure"
    assert actions[0].reason == "tests fail"
    assert actions[0].task_id is None


async def test_report_failure_accepts_task_id_override() -> None:
    text = '<hive_actions>[{"type":"report_failure","reason":"x","task_id":42}]</hive_actions>'
    _, actions = parse_actions(text)
    assert actions[0].task_id == 42


async def test_report_failure_without_reason_is_skipped() -> None:
    text = '<hive_actions>[{"type":"report_failure"}]</hive_actions>'
    _, actions = parse_actions(text)
    assert actions == []


# ---------------------------------------------------------------------------
# handle_task_failure — retry path
# ---------------------------------------------------------------------------


async def test_handle_task_failure_retries_below_limit(
    manager: ProcessManager,
    task_store: TaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_org(manager)
    task = await task_store.create(
        title="flaky task",
        assigned_to="dev.backend.w1",
        created_by="user",
    )
    worker = manager._entities["dev.backend.w1"]
    assert isinstance(worker, WorkerAgent)
    worker.task_id = task.id

    send = AsyncMock(return_value="ok")
    monkeypatch.setattr(manager, "send_to_entity", send)

    await manager.handle_task_failure(task.id, "exit code 1")

    assert send.await_count == 1
    name, prompt = send.await_args.args
    assert name == "dev.backend.w1"
    assert "retry 1/3" in prompt
    assert "exit code 1" in prompt

    row = await task_store.get(task.id)
    assert row is not None
    assert row.retry_count == 1
    assert row.failure_reason == "exit code 1"


async def test_handle_task_failure_escalates_worker_to_lead(
    manager: ProcessManager,
    task_store: TaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_org(manager)
    task = await task_store.create(
        title="doomed",
        assigned_to="dev.backend.w1",
        created_by="user",
    )
    # pre-bump to max_retries so the next failure triggers escalation
    for _ in range(3):
        await task_store.increment_retry(task.id, "prior")
    worker = manager._entities["dev.backend.w1"]
    assert isinstance(worker, WorkerAgent)
    worker.task_id = task.id

    send = AsyncMock()
    monkeypatch.setattr(manager, "send_to_entity", send)

    await manager.handle_task_failure(task.id, "final")

    # No retry — escalation path instead
    send.assert_not_called()
    # Lead received an escalation message via the router
    assert manager.router.has_pending("dev.backend")


async def test_handle_task_failure_notifies_user_when_maestro_escalates(
    manager: ProcessManager,
    task_store: TaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the assigned entity is a maestro (unusual), escalation pings the user."""
    _populate_org(manager)
    maestro = manager._entities["dev"]
    maestro.task_id = 0  # irrelevant; we attach task via assigned_to

    task = await task_store.create(title="up top", assigned_to="dev", created_by="user")
    for _ in range(3):
        await task_store.increment_retry(task.id, "p")

    channel = _CapturingChannel()
    manager.notification_dispatcher.register(channel)
    send = AsyncMock()
    monkeypatch.setattr(manager, "send_to_entity", send)

    await manager.handle_task_failure(task.id, "boom")

    send.assert_not_called()
    assert channel.messages, "Expected user-level TG notification on maestro escalation"
    assert "boom" in channel.messages[0]
    assert f"task #{task.id}" in channel.messages[0]


async def test_handle_task_failure_without_store_is_noop(
    router: MessageRouter,
) -> None:
    """Guard: calling handle_task_failure without a TaskStore should not crash."""
    mgr = ProcessManager(router=router)
    await mgr.handle_task_failure(1, "no store")  # should log-warn and return


# ---------------------------------------------------------------------------
# Audit rows
# ---------------------------------------------------------------------------


async def test_retry_audits_emit_expected_actions(
    manager: ProcessManager,
    task_store: TaskStore,
    audit_log: AuditLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populate_org(manager)
    task = await task_store.create(title="a", assigned_to="dev.backend.w1", created_by="user")
    worker = manager._entities["dev.backend.w1"]
    assert isinstance(worker, WorkerAgent)
    worker.task_id = task.id

    monkeypatch.setattr(manager, "send_to_entity", AsyncMock(return_value="ok"))

    await manager.handle_task_failure(task.id, "r1")
    # bump past max so next call escalates
    for _ in range(3):
        await task_store.increment_retry(task.id, "filler")

    await manager.handle_task_failure(task.id, "r2")

    rows = await audit_log.recent(limit=20)
    actions = {r["action"] for r in rows}
    assert "task.retry" in actions
    assert "task.escalated" in actions


async def test_gave_up_audit_when_user_level(
    manager: ProcessManager,
    task_store: TaskStore,
    audit_log: AuditLog,
) -> None:
    _populate_org(manager)
    task = await task_store.create(title="top", assigned_to="dev", created_by="user")
    for _ in range(3):
        await task_store.increment_retry(task.id, "filler")

    await manager.handle_task_failure(task.id, "final")

    rows = await audit_log.recent(limit=20)
    actions = {r["action"] for r in rows}
    assert "task.gave_up" in actions


# ---------------------------------------------------------------------------
# send_to_entity wiring of report_failure
# ---------------------------------------------------------------------------


async def test_task_status_preserved_on_retry(
    task_store: TaskStore,
) -> None:
    """Retry bookkeeping must not flip the status enum."""
    task = await task_store.create(title="x", created_by="user")
    await task_store.update_status(task.id, TaskStatus.IN_PROGRESS)
    await task_store.increment_retry(task.id, "whoops")

    refreshed = await task_store.get(task.id)
    assert refreshed is not None
    assert refreshed.status is TaskStatus.IN_PROGRESS
