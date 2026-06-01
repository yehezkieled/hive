"""Tests for ProcessManager.request_mode_change + approve/deny wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from hive.bus.mode_request_store import ModeRequestStore
from hive.bus.router import MessageRouter
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import WorkerAgent
from hive.notifications import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager


class _CapturingChannel:
    """Test channel that records every notification it receives."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, notification: Notification) -> None:
        self.messages.append(notification.text)


@pytest_asyncio.fixture
async def manager(
    router: MessageRouter,
    mode_request_store: ModeRequestStore,
) -> AsyncIterator[ProcessManager]:
    """ProcessManager wired to the session PG container via mode_request_store."""
    mgr = ProcessManager(
        router=router,
        mode_request_store=mode_request_store,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        yield mgr
    finally:
        await mgr.kill_all()


def _populate_org(manager: ProcessManager) -> None:
    """Register a minimal maestro/lead/worker tree."""
    maestro = Maestro(name="dev")
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    worker = WorkerAgent(name="dev.backend.w1", team_name="backend", lead_name="dev.backend")
    for e in (maestro, lead, worker):
        manager._entities[e.name] = e
        manager.router.register(e.name)


async def test_approver_for_maestro_is_user(manager: ProcessManager) -> None:
    _populate_org(manager)
    assert manager._approver_for(manager._entities["dev"]) == "user"


async def test_approver_for_lead_is_parent_maestro(manager: ProcessManager) -> None:
    _populate_org(manager)
    assert manager._approver_for(manager._entities["dev.backend"]) == "dev"


async def test_approver_for_worker_is_parent_lead(manager: ProcessManager) -> None:
    _populate_org(manager)
    assert manager._approver_for(manager._entities["dev.backend.w1"]) == "dev.backend"


async def test_request_mode_change_persists_row(
    manager: ProcessManager,
    mode_request_store: ModeRequestStore,
) -> None:
    _populate_org(manager)
    req_id = await manager.request_mode_change("dev.backend", "yotree", reason="refactor auth")
    row = await mode_request_store.get(req_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["requester"] == "dev.backend"
    assert row["requested_mode"] == "yotree"
    assert row["approver"] == "dev"
    assert row["reason"] == "refactor auth"


async def test_request_mode_change_rejects_non_dangerous_mode(
    manager: ProcessManager,
) -> None:
    _populate_org(manager)
    with pytest.raises(ValueError, match="does not require approval"):
        await manager.request_mode_change("dev", "plan")


async def test_request_mode_change_unknown_requester(manager: ProcessManager) -> None:
    with pytest.raises(KeyError):
        await manager.request_mode_change("ghost", "yolo")


async def test_maestro_request_notifies_user(
    manager: ProcessManager,
) -> None:
    _populate_org(manager)
    channel = _CapturingChannel()
    manager.notification_dispatcher.register(channel)
    await manager.request_mode_change("dev", "yolo", reason="quick CI fix")
    assert len(channel.messages) == 1
    assert "dev" in channel.messages[0]
    assert "yolo" in channel.messages[0]
    assert "quick CI fix" in channel.messages[0]


async def test_lead_request_does_not_notify_user(
    manager: ProcessManager,
) -> None:
    """Leads escalate to their maestro, not the user — no TG ping."""
    _populate_org(manager)
    channel = _CapturingChannel()
    manager.notification_dispatcher.register(channel)
    await manager.request_mode_change("dev.backend", "yotree")
    assert channel.messages == []


async def test_approve_updates_entity_mode(
    manager: ProcessManager,
) -> None:
    _populate_org(manager)
    req_id = await manager.request_mode_change("dev.backend", "yotree")
    entity = manager._entities["dev.backend"]
    assert entity.permission_mode == "default"

    row = await manager.approve_mode_request(req_id)
    assert row is not None
    assert row["status"] == "approved"
    assert entity.permission_mode == "yotree"


async def test_approve_missing_request_returns_none(
    manager: ProcessManager,
) -> None:
    assert await manager.approve_mode_request(99999) is None


async def test_deny_leaves_entity_mode_unchanged(
    manager: ProcessManager,
) -> None:
    _populate_org(manager)
    req_id = await manager.request_mode_change("dev.backend", "yolo")
    row = await manager.deny_mode_request(req_id, reason="use edit")
    assert row is not None
    assert row["status"] == "denied"
    assert row["reason"] == "use edit"
    assert manager._entities["dev.backend"].permission_mode == "default"


async def test_deny_missing_request_returns_none(manager: ProcessManager) -> None:
    assert await manager.deny_mode_request(99999) is None


async def test_expire_old_mode_requests(
    manager: ProcessManager,
    mode_request_store: ModeRequestStore,
) -> None:
    _populate_org(manager)
    req_id = await manager.request_mode_change("dev.backend", "yotree")
    async with mode_request_store.pool.acquire() as conn:
        await conn.execute(
            "UPDATE mode_requests SET created_at = NOW() - INTERVAL '1 hour' WHERE id = $1",
            req_id,
        )
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    expired = await manager.expire_old_mode_requests(cutoff)
    assert len(expired) == 1
    assert expired[0]["id"] == req_id
    assert expired[0]["status"] == "expired"


async def test_approve_without_store_is_noop(
    router: MessageRouter,
) -> None:
    """A manager without a mode_request_store silently no-ops on approve/deny."""
    mgr = ProcessManager(router=router)  # no mode_request_store
    assert await mgr.approve_mode_request(1) is None
    assert await mgr.deny_mode_request(1) is None


async def test_approve_gate_rings_doorbell(
    manager: ProcessManager,
    mode_request_store: ModeRequestStore,
) -> None:
    """approve_gate marks the gate row approved AND rings the coordinator's
    doorbell so the parked Turn wakes."""
    from unittest.mock import MagicMock

    coordinator = MagicMock()
    manager.gate_coordinator = coordinator

    row = await mode_request_store.create(
        requester="dev",
        requested_mode="plan",
        approver="user",
        reason="my plan",
        kind="gate",
    )
    resolved = await manager.approve_gate(row["id"])
    assert resolved is not None
    assert resolved["status"] == "approved"
    coordinator.ring.assert_called_once_with("dev")


async def test_deny_gate_rings_doorbell(
    manager: ProcessManager,
    mode_request_store: ModeRequestStore,
) -> None:
    """deny_gate marks the gate row denied AND rings the doorbell."""
    from unittest.mock import MagicMock

    coordinator = MagicMock()
    manager.gate_coordinator = coordinator

    row = await mode_request_store.create(
        requester="dev",
        requested_mode="plan",
        approver="user",
        kind="gate",
    )
    resolved = await manager.deny_gate(row["id"], reason="re-plan")
    assert resolved is not None
    assert resolved["status"] == "denied"
    assert resolved["reason"] == "re-plan"
    coordinator.ring.assert_called_once_with("dev")


async def test_approve_gate_missing_row_returns_none(
    manager: ProcessManager,
) -> None:
    """An unknown gate id resolves to None and never rings."""
    from unittest.mock import MagicMock

    coordinator = MagicMock()
    manager.gate_coordinator = coordinator
    assert await manager.approve_gate(99999) is None
    coordinator.ring.assert_not_called()
