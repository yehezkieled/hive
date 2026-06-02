"""Sprint 25 — manager-level tests for `request_payment` action.

Verifies the role-gating, audit emission, and notification fan-out for
the new payment-request flow. Persistence-layer behaviour is covered by
``test_vault_store.py``; parser behaviour by ``test_actions.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from hive.bus.audit_log import AuditLog
from hive.bus.router import MessageRouter
from hive.bus.vault_store import VaultStore
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.vault import Vault
from hive.models.worker import Worker
from hive.notifications import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager


class _CapturingChannel:
    """Test channel that records every notification it receives."""

    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    async def send(self, notification: Notification) -> None:
        self.notifications.append(notification)


@pytest_asyncio.fixture
async def manager(
    router: MessageRouter,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> AsyncIterator[ProcessManager]:
    mgr = ProcessManager(
        router=router,
        vault_store=vault_store,
        audit_log=audit_log,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        yield mgr
    finally:
        await mgr.kill_all()


def _populate_org_with_vault(manager: ProcessManager) -> None:
    """Register a minimal maestro/lead/worker tree plus a Vault entity."""
    maestro = Maestro(name="dev")
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    worker = Worker(name="dev.backend.w1", team_name="backend", lead_name="dev.backend")
    vault = Vault(name="vault")
    for e in (maestro, lead, worker, vault):
        manager._entities[e.name] = e
        manager.router.register(e.name)


async def test_request_payment_only_from_vault(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    """Non-Vault entities must be rejected and the rejection audited."""
    _populate_org_with_vault(manager)

    with pytest.raises(PermissionError):
        await manager.request_payment(
            "dev",
            amount_cents=500,
            currency="USD",
            recipient="test@example.com",
            idempotency_key="block-1",
            reason="should fail",
        )

    # No vault_actions row was created.
    assert await vault_store.pending("vault") == []
    assert await vault_store.pending("dev") == []

    # An unauthorized audit was emitted.
    events = await audit_log.recent(action_prefix="vault.")
    actions = [e["action"] for e in events]
    assert "vault.unauthorized" in actions
    assert "vault.requested" not in actions


async def test_request_payment_creates_row_and_audits(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    """A Vault requester gets a pending row, audit, and user notification."""
    _populate_org_with_vault(manager)
    channel = _CapturingChannel()
    manager.notification_dispatcher.register(channel)

    action_id = await manager.request_payment(
        "vault",
        amount_cents=750,
        currency="usd",  # lowercased on purpose; manager normalises
        recipient="charity@example.com",
        idempotency_key="ok-1",
        reason="quarterly donation",
    )
    assert action_id is not None

    row = await vault_store.get(action_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["action_type"] == "payment"
    assert row["amount_cents"] == 750
    assert row["currency"] == "USD"
    assert row["recipient"] == "charity@example.com"
    assert row["idempotency_key"] == "ok-1"

    events = await audit_log.recent(action_prefix="vault.")
    requested = [e for e in events if e["action"] == "vault.requested"]
    assert len(requested) == 1
    assert requested[0]["target"] == "vault"
    assert requested[0]["details"]["id"] == action_id

    assert len(channel.notifications) == 1
    note = channel.notifications[0]
    assert note.kind == "vault_action_pending"
    assert note.data["id"] == action_id
    assert note.data["amount_cents"] == 750


async def test_request_payment_duplicate_idempotency_key_audited(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    """Reusing an idempotency_key returns None and emits a duplicate audit."""
    _populate_org_with_vault(manager)

    first = await manager.request_payment(
        "vault",
        amount_cents=100,
        currency="USD",
        recipient="r@example.com",
        idempotency_key="dup-1",
        reason="first",
    )
    assert first is not None

    second = await manager.request_payment(
        "vault",
        amount_cents=200,
        currency="USD",
        recipient="r@example.com",
        idempotency_key="dup-1",
        reason="second",
    )
    assert second is None

    events = await audit_log.recent(action_prefix="vault.")
    actions = [e["action"] for e in events]
    assert "vault.duplicate_idempotency_key" in actions


async def test_request_payment_validates_amount_and_currency(
    manager: ProcessManager,
    vault_store: VaultStore,
) -> None:
    """Bad inputs raise ValueError before touching the store."""
    _populate_org_with_vault(manager)

    with pytest.raises(ValueError):
        await manager.request_payment(
            "vault",
            amount_cents=0,
            currency="USD",
            recipient="r@example.com",
            idempotency_key="bad-1",
            reason="zero",
        )

    with pytest.raises(ValueError):
        await manager.request_payment(
            "vault",
            amount_cents=100,
            currency="DOLLAR",
            recipient="r@example.com",
            idempotency_key="bad-2",
            reason="currency too long",
        )

    with pytest.raises(ValueError):
        await manager.request_payment(
            "vault",
            amount_cents=100,
            currency="USD",
            recipient="",
            idempotency_key="bad-3",
            reason="missing recipient",
        )

    assert await vault_store.pending("vault") == []
