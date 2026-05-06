"""Sprint 25 — end-to-end approval flow.

Wires ProcessManager.request_payment → approve_vault_action through a
real VaultStore + StubPaymentProvider, and asserts the full audit trail
fires for each terminal status: completed, denied (cap), failed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from hive.bus.audit_log import AuditLog
from hive.bus.router import MessageRouter
from hive.bus.vault_store import VaultStore
from hive.models.vault import Vault
from hive.notifications import Notification, NotificationDispatcher
from hive.process.manager import ProcessManager
from hive.vault.provider import StubPaymentProvider


class _CapturingChannel:
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
        payment_provider=StubPaymentProvider(),
        vault_daily_cap_cents=10_000,  # $100/day for tests
        vault_monthly_cap_cents=100_000,  # $1000/month for tests
        notification_dispatcher=NotificationDispatcher(),
    )
    vault = Vault(name="vault")
    mgr._entities[vault.name] = vault
    mgr.router.register(vault.name)
    try:
        yield mgr
    finally:
        await mgr.kill_all()


async def test_request_then_approve_executes(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    channel = _CapturingChannel()
    manager.notification_dispatcher.register(channel)

    action_id = await manager.request_payment(
        "vault",
        amount_cents=500,
        currency="USD",
        recipient="charity@example.com",
        idempotency_key="flow-1",
        reason="quarterly",
    )
    assert action_id is not None

    result = await manager.approve_vault_action(action_id)
    assert result is not None
    assert result["status"] == "completed"
    assert result["executed_at"] is not None

    events = await audit_log.recent(action_prefix="vault.")
    actions = [e["action"] for e in events]
    assert "vault.requested" in actions
    assert "vault.executed" in actions

    kinds = [n.kind for n in channel.notifications]
    assert "vault_action_pending" in kinds
    assert "vault_action_resolved" in kinds


async def test_force_fail_marks_failed(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    action_id = await manager.request_payment(
        "vault",
        amount_cents=300,
        currency="USD",
        recipient="r@example.com",
        idempotency_key="ff-flow-1",
        reason="FORCE_FAIL diagnostic",
    )
    assert action_id is not None

    result = await manager.approve_vault_action(action_id)
    assert result is not None
    assert result["status"] == "failed"
    assert result["denial_reason"] is not None
    assert "FORCE_FAIL" in result["denial_reason"]

    events = await audit_log.recent(action_prefix="vault.")
    assert any(e["action"] == "vault.failed" for e in events)


async def test_cap_exceeded_denies(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    # Tighten the daily cap below the next request.
    manager.vault_daily_cap_cents = 100  # $1.00

    action_id = await manager.request_payment(
        "vault",
        amount_cents=500,  # over the cap
        currency="USD",
        recipient="r@example.com",
        idempotency_key="cap-flow-1",
        reason="too big",
    )
    assert action_id is not None

    result = await manager.approve_vault_action(action_id)
    assert result is not None
    assert result["status"] == "denied"
    assert result["denial_reason"] is not None
    assert "daily" in result["denial_reason"]

    events = await audit_log.recent(action_prefix="vault.")
    assert any(e["action"] == "vault.cap_exceeded" for e in events)


async def test_idempotent_double_approve_returns_terminal_row(
    manager: ProcessManager,
    vault_store: VaultStore,
) -> None:
    action_id = await manager.request_payment(
        "vault",
        amount_cents=200,
        currency="USD",
        recipient="r@example.com",
        idempotency_key="idem-flow-1",
        reason="once",
    )
    assert action_id is not None

    first = await manager.approve_vault_action(action_id)
    assert first is not None
    assert first["status"] == "completed"
    first_executed_at = first["executed_at"]

    # A second approve must not re-execute or re-charge.
    second = await manager.approve_vault_action(action_id)
    assert second is not None
    assert second["status"] == "completed"
    assert second["executed_at"] == first_executed_at


async def test_approve_missing_action_returns_none(
    manager: ProcessManager,
) -> None:
    assert await manager.approve_vault_action(99_999) is None


async def test_legacy_generic_action_uses_approve_path(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    """Sprint 6 free-text actions still resolve via the legacy approve path."""
    row = await vault_store.create_action(
        vault_name="vault",
        description="legacy free-text action",
        requester="dev",
    )
    result = await manager.approve_vault_action(row["id"])
    assert result is not None
    assert result["status"] == "approved"

    events = await audit_log.recent(action_prefix="vault.")
    assert any(e["action"] == "vault.approved" for e in events)
    # No execution path for generic actions.
    assert not any(e["action"] == "vault.executed" for e in events)


async def test_deny_vault_action_records_audit(
    manager: ProcessManager,
    vault_store: VaultStore,
    audit_log: AuditLog,
) -> None:
    action_id = await manager.request_payment(
        "vault",
        amount_cents=100,
        currency="USD",
        recipient="r@example.com",
        idempotency_key="deny-flow-1",
        reason="user will deny",
    )
    assert action_id is not None

    result = await manager.deny_vault_action(action_id, reason="not authorised")
    assert result is not None
    assert result["status"] == "denied"
    assert result["denial_reason"] == "not authorised"

    events = await audit_log.recent(action_prefix="vault.")
    assert any(e["action"] == "vault.denied" for e in events)
