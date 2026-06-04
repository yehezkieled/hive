"""Unit tests for the ``ApprovalHandler`` collaborator (Ticket 004 slice 1).

These exercise ``ApprovalHandler`` in isolation against a *stub* manager —
no real ProcessManager, no Postgres. The stub exposes only the surface the
handler reaches through ``self._mgr``: the stores, ``_audit`` / ``_notify``
recorders, the entity registry, and the cap / provider / router knobs.

The big DB-backed suites (``test_mode_approval``, ``test_auto_recovery``,
the gate tests in ``test_process_manager``) still cover the same flows
end-to-end through the facade; these add fast, hermetic unit coverage of
the moved code and prove the composition pattern (collaborator reaching
shared state via ``self._mgr``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.vault import Vault
from hive.models.worker import Worker
from hive.process.approval_handler import ApprovalHandler

# ---------------------------------------------------------------------------
# Stub manager
# ---------------------------------------------------------------------------


class StubManager:
    """Minimal stand-in for ProcessManager's surface the handler touches.

    Only the attributes ``ApprovalHandler`` reads via ``self._mgr`` are
    present. ``_audit`` / ``_notify`` / ``_persist`` record their calls so
    tests can assert on the audit/notify stream without a real dispatcher.
    """

    def __init__(self) -> None:
        self._entities: dict[str, object] = {}
        self.mode_request_store: AsyncMock | None = None
        self.vault_store: AsyncMock | None = None
        self.task_store: AsyncMock | None = None
        self.notification_dispatcher: AsyncMock | None = None
        self.gate_coordinator: object | None = None
        self.payment_provider: object | None = None
        self.router = AsyncMock()
        self.vault_daily_cap_cents = 0
        self.vault_monthly_cap_cents = 0
        self.vault_cap_currencies: tuple[str, ...] = ("AUD", "USD")

        self._gate_tasks: set[asyncio.Task] = set()

        self.audit_calls: list[tuple[str, str | None, dict | None]] = []
        self.notify_calls: list[tuple[str, str, dict | None]] = []
        self.persisted: list[object] = []
        self.sent: list[tuple[str, str]] = []

    async def _audit(
        self,
        action: str,
        target: str | None = None,
        details: dict | None = None,
        actor: str = "system",
    ) -> None:
        self.audit_calls.append((action, target, details))

    async def _notify(
        self,
        message: str,
        kind: str = "info",
        data: dict | None = None,
    ) -> None:
        self.notify_calls.append((message, kind, data))

    async def _persist(self, entity: object) -> None:
        self.persisted.append(entity)

    async def send_to_entity(self, name: str, text: str) -> str:
        self.sent.append((name, text))
        return ""

    def audit_actions(self) -> list[str]:
        return [call[0] for call in self.audit_calls]


@pytest.fixture
def mgr() -> StubManager:
    return StubManager()


@pytest.fixture
def handler(mgr: StubManager) -> ApprovalHandler:
    return ApprovalHandler(mgr)


def _populate_org(mgr: StubManager) -> None:
    """Register a maestro/lead/worker tree for approver/escalation paths."""
    maestro = Maestro(name="dev")
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    worker = Worker(name="dev.backend.w1", team_name="backend", lead_name="dev.backend")
    for entity in (maestro, lead, worker):
        mgr._entities[entity.name] = entity


# ---------------------------------------------------------------------------
# Pattern wiring
# ---------------------------------------------------------------------------


def test_handler_stores_back_ref(mgr: StubManager) -> None:
    handler = ApprovalHandler(mgr)
    assert handler._mgr is mgr


# ---------------------------------------------------------------------------
# _approver_for
# ---------------------------------------------------------------------------


def test_approver_for_maestro_is_user(handler: ApprovalHandler, mgr: StubManager) -> None:
    _populate_org(mgr)
    assert handler._approver_for(mgr._entities["dev"]) == "user"


def test_approver_for_lead_is_maestro(handler: ApprovalHandler, mgr: StubManager) -> None:
    _populate_org(mgr)
    assert handler._approver_for(mgr._entities["dev.backend"]) == "dev"


def test_approver_for_worker_is_lead(handler: ApprovalHandler, mgr: StubManager) -> None:
    _populate_org(mgr)
    assert handler._approver_for(mgr._entities["dev.backend.w1"]) == "dev.backend"


# ---------------------------------------------------------------------------
# request_mode_change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_mode_change_no_store_raises(handler: ApprovalHandler) -> None:
    with pytest.raises(ValueError, match="mode_request_store not configured"):
        await handler.request_mode_change("dev", "yolo")


@pytest.mark.asyncio
async def test_request_mode_change_invalid_mode_raises(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.mode_request_store = AsyncMock()
    with pytest.raises(ValueError, match="does not require approval"):
        await handler.request_mode_change("dev", "default")


@pytest.mark.asyncio
async def test_request_mode_change_unknown_requester_raises(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.mode_request_store = AsyncMock()
    with pytest.raises(KeyError):
        await handler.request_mode_change("ghost", "yolo")


@pytest.mark.asyncio
async def test_request_mode_change_maestro_notifies_user(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    _populate_org(mgr)
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.create.return_value = {"id": 7}

    request_id = await handler.request_mode_change("dev", "yolo", reason="ship it")

    assert request_id == 7
    mgr.mode_request_store.create.assert_awaited_once()
    assert "mode.request" in mgr.audit_actions()
    # Approver is the user, so a notification is fired.
    assert len(mgr.notify_calls) == 1
    assert mgr.notify_calls[0][1] == "mode_request"


@pytest.mark.asyncio
async def test_request_mode_change_worker_no_user_notify(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    _populate_org(mgr)
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.create.return_value = {"id": 9}

    await handler.request_mode_change("dev.backend.w1", "yolo")

    # Approver is the parent lead (not user) -> no Telegram notify.
    assert mgr.notify_calls == []
    assert "mode.request" in mgr.audit_actions()


# ---------------------------------------------------------------------------
# request_payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_payment_no_store_returns_none(handler: ApprovalHandler) -> None:
    assert (
        await handler.request_payment(
            "v",
            amount_cents=100,
            currency="USD",
            recipient="acme",
            idempotency_key="k1",
            reason="r",
        )
        is None
    )


@pytest.mark.asyncio
async def test_request_payment_non_vault_raises_and_audits(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    _populate_org(mgr)
    mgr.vault_store = AsyncMock()
    with pytest.raises(PermissionError):
        await handler.request_payment(
            "dev",
            amount_cents=100,
            currency="USD",
            recipient="acme",
            idempotency_key="k1",
            reason="r",
        )
    assert "vault.unauthorized" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_request_payment_bad_amount_raises(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.vault_store = AsyncMock()
    with pytest.raises(ValueError, match="amount_cents must be positive"):
        await handler.request_payment(
            "v",
            amount_cents=0,
            currency="USD",
            recipient="acme",
            idempotency_key="k1",
            reason="r",
        )


@pytest.mark.asyncio
async def test_request_payment_vault_records_and_notifies(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr._entities["bank"] = Vault(name="bank")
    mgr.vault_store = AsyncMock()
    mgr.vault_store.create_action.return_value = {"id": 42}

    request_id = await handler.request_payment(
        "bank",
        amount_cents=2500,
        currency="usd",
        recipient="acme",
        idempotency_key="key-1",
        reason="invoice",
    )

    assert request_id == 42
    # currency normalised to upper-case before the store call.
    _, kwargs = mgr.vault_store.create_action.call_args
    assert kwargs["currency"] == "USD"
    assert "vault.requested" in mgr.audit_actions()
    assert mgr.notify_calls[0][1] == "vault_action_pending"


# ---------------------------------------------------------------------------
# approve_vault_action — cap path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_vault_action_cap_exceeded_denies(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.vault_daily_cap_cents = 1000
    mgr.vault_store = AsyncMock()
    mgr.vault_store.get.return_value = {
        "status": "pending",
        "action_type": "payment",
        "amount_cents": 5000,
        "currency": "USD",
        "recipient": "acme",
        "vault_name": "bank",
    }
    # check_caps reads spend_total_cents off the store; force over-cap.
    mgr.vault_store.spend_total_cents.return_value = 0
    mgr.vault_store.deny.return_value = {"id": 1, "status": "denied"}

    result = await handler.approve_vault_action(1)

    assert result == {"id": 1, "status": "denied"}
    mgr.vault_store.deny.assert_awaited_once()
    assert "vault.cap_exceeded" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_approve_vault_action_executes_within_cap(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.vault_daily_cap_cents = 100000
    mgr.vault_monthly_cap_cents = 100000
    mgr.vault_store = AsyncMock()
    mgr.vault_store.get.return_value = {
        "status": "pending",
        "action_type": "payment",
        "amount_cents": 5000,
        "currency": "USD",
        "recipient": "acme",
        "vault_name": "bank",
    }
    mgr.vault_store.spend_total_cents.return_value = 0
    mgr.vault_store.mark_executed.return_value = {"id": 1, "status": "completed"}

    provider_result = SimpleNamespace(
        ok=True,
        error=None,
        provider="stub",
        reference="ref-1",
        to_payload=lambda: {"ref": "ref-1"},
    )
    mgr.payment_provider = SimpleNamespace(execute=AsyncMock(return_value=provider_result))

    result = await handler.approve_vault_action(1)

    assert result == {"id": 1, "status": "completed"}
    assert "vault.executed" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_approve_vault_action_non_pending_is_idempotent(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.vault_store = AsyncMock()
    mgr.vault_store.get.return_value = {"status": "completed", "id": 1}

    result = await handler.approve_vault_action(1)

    assert result == {"status": "completed", "id": 1}
    mgr.vault_store.deny.assert_not_awaited()
    mgr.vault_store.approve.assert_not_awaited()


# ---------------------------------------------------------------------------
# deny_vault_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_vault_action_audits_and_notifies(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.vault_store = AsyncMock()
    mgr.vault_store.deny.return_value = {"vault_name": "bank"}

    result = await handler.deny_vault_action(3, reason="no")

    assert result == {"vault_name": "bank"}
    assert "vault.denied" in mgr.audit_actions()
    assert mgr.notify_calls[0][1] == "vault_action_resolved"


# ---------------------------------------------------------------------------
# approve / deny mode request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_mode_request_updates_entity_mode(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    worker = Worker(name="dev.backend.w1", lead_name="dev.backend")
    mgr._entities[worker.name] = worker
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.approve.return_value = {
        "requester": "dev.backend.w1",
        "requested_mode": "yolo",
    }

    await handler.approve_mode_request(5)

    assert worker.permission_mode == "yolo"
    assert worker in mgr.persisted
    assert "mode.approve" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_deny_mode_request_audits(handler: ApprovalHandler, mgr: StubManager) -> None:
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.deny.return_value = {"requester": "dev"}

    await handler.deny_mode_request(5, reason="no")

    assert "mode.deny" in mgr.audit_actions()


# ---------------------------------------------------------------------------
# Interactive gate flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_gate_state_gated_transitions_and_notifies(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    # _on_gate_state is sync but schedules _notify_gate_waiting as a
    # background task, so it must run inside a live event loop.
    entity = Worker(name="dev.backend.w1", lead_name="dev.backend")
    entity.state = EntityState.RUNNING
    mgr._entities[entity.name] = entity
    mgr.notification_dispatcher = AsyncMock()

    handler._on_gate_state("dev.backend.w1", "gated")
    assert entity.state == EntityState.GATED

    # Let the detached _notify_gate_waiting task run.
    await asyncio.sleep(0)
    mgr.notification_dispatcher.dispatch.assert_awaited()


@pytest.mark.asyncio
async def test_on_gate_state_gated_tracks_then_discards_task(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    # The detached notify task must be held in _gate_tasks while in-flight so
    # it can't be GC'd, then dropped by the done-callback once it completes.
    entity = Worker(name="dev.backend.w1", lead_name="dev.backend")
    entity.state = EntityState.RUNNING
    mgr._entities[entity.name] = entity
    mgr.notification_dispatcher = AsyncMock()

    handler._on_gate_state("dev.backend.w1", "gated")

    # Tracked while the task is still running.
    assert len(mgr._gate_tasks) == 1

    # Drive the task to completion, then let the done-callback fire.
    task = next(iter(mgr._gate_tasks))
    await task
    await asyncio.sleep(0)

    # Discarded once it's done — no lingering reference.
    assert mgr._gate_tasks == set()


def test_on_gate_state_running_transitions_back(handler: ApprovalHandler, mgr: StubManager) -> None:
    entity = Worker(name="dev.backend.w1", lead_name="dev.backend")
    entity.state = EntityState.GATED
    mgr._entities[entity.name] = entity

    handler._on_gate_state("dev.backend.w1", "running")

    assert entity.state == EntityState.RUNNING


def test_on_gate_state_unknown_entity_noop(handler: ApprovalHandler, mgr: StubManager) -> None:
    # No entity registered -> returns without raising.
    handler._on_gate_state("ghost", "gated")


@pytest.mark.asyncio
async def test_approve_gate_rings_doorbell(handler: ApprovalHandler, mgr: StubManager) -> None:
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.approve.return_value = {"requester": "dev.backend.w1"}
    mgr.gate_coordinator = SimpleNamespace(ring=lambda name: rang.append(name))
    rang: list[str] = []

    result = await handler.approve_gate(11, chosen_option=2)

    assert result == {"requester": "dev.backend.w1"}
    mgr.mode_request_store.approve.assert_awaited_once_with(11, chosen_option=2)
    assert rang == ["dev.backend.w1"]
    assert "gate.approve" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_deny_gate_rings_doorbell(handler: ApprovalHandler, mgr: StubManager) -> None:
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.deny.return_value = {"requester": "dev.backend.w1"}
    rang: list[str] = []
    mgr.gate_coordinator = SimpleNamespace(ring=lambda name: rang.append(name))

    result = await handler.deny_gate(11, reason="keep planning")

    assert result == {"requester": "dev.backend.w1"}
    assert rang == ["dev.backend.w1"]
    assert "gate.deny" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_reconcile_orphaned_gates_denies_stale(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.list_pending.return_value = [
        {"id": 1, "requester": "dev.backend.w1"},
        {"id": 2, "requester": "dev.backend.w2"},
    ]
    mgr.mode_request_store.deny.side_effect = lambda gid, reason: {"id": gid, "status": "denied"}
    # A live doorbell on w2 means it is genuinely parked -> skip it.
    mgr.gate_coordinator = SimpleNamespace(
        pending_request_id=lambda name: 99 if name == "dev.backend.w2" else None
    )

    reconciled = await handler.reconcile_orphaned_gates()

    assert [row["id"] for row in reconciled] == [1]
    assert "gate.reconcile_stale" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_reconcile_orphaned_gates_no_store(handler: ApprovalHandler) -> None:
    assert await handler.reconcile_orphaned_gates() == []


# ---------------------------------------------------------------------------
# expire_old_mode_requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expire_old_mode_requests_audits_each(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    mgr.mode_request_store = AsyncMock()
    mgr.mode_request_store.expire_older_than.return_value = [
        {"id": 1, "requester": "dev", "requested_mode": "yolo"},
        {"id": 2, "requester": "dev.backend", "requested_mode": "yotree"},
    ]

    rows = await handler.expire_old_mode_requests(datetime.now(UTC))

    assert len(rows) == 2
    assert mgr.audit_actions().count("mode.expire") == 2


# ---------------------------------------------------------------------------
# _escalation_target_for
# ---------------------------------------------------------------------------


def test_escalation_target_worker_to_lead(handler: ApprovalHandler, mgr: StubManager) -> None:
    _populate_org(mgr)
    assert handler._escalation_target_for("dev.backend.w1") == "dev.backend"


def test_escalation_target_lead_to_maestro(handler: ApprovalHandler, mgr: StubManager) -> None:
    _populate_org(mgr)
    assert handler._escalation_target_for("dev.backend") == "dev"


def test_escalation_target_maestro_to_user(handler: ApprovalHandler, mgr: StubManager) -> None:
    _populate_org(mgr)
    assert handler._escalation_target_for("dev") == "user"


# ---------------------------------------------------------------------------
# handle_task_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_task_failure_retries_on_assignee(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    _populate_org(mgr)
    mgr.task_store = AsyncMock()
    mgr.task_store.increment_retry.return_value = SimpleNamespace(
        assigned_to="dev.backend.w1",
        retry_count=1,
        max_retries=3,
        title="ship",
        description=None,
    )

    await handler.handle_task_failure(7, "boom")

    assert mgr.sent and mgr.sent[0][0] == "dev.backend.w1"
    assert "task.retry" in mgr.audit_actions()


@pytest.mark.asyncio
async def test_handle_task_failure_escalates_to_lead(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    _populate_org(mgr)
    mgr.task_store = AsyncMock()
    mgr.task_store.increment_retry.return_value = SimpleNamespace(
        assigned_to="dev.backend.w1",
        retry_count=4,
        max_retries=3,
        title="ship",
        description=None,
    )

    await handler.handle_task_failure(7, "boom")

    assert "task.escalated" in mgr.audit_actions()
    # Escalation to a registered parent routes an internal message.
    mgr.router.route.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_task_failure_no_store_noop(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    # task_store is None -> early return, nothing audited.
    await handler.handle_task_failure(7, "boom")
    assert mgr.audit_calls == []


@pytest.mark.asyncio
async def test_handle_task_failure_gives_up_to_user(
    handler: ApprovalHandler, mgr: StubManager
) -> None:
    # Assigned entity not registered -> escalate to "user" -> notify, give up.
    mgr.task_store = AsyncMock()
    mgr.task_store.increment_retry.return_value = SimpleNamespace(
        assigned_to="ghost",
        retry_count=4,
        max_retries=3,
        title="ship",
        description=None,
    )

    await handler.handle_task_failure(7, "boom")

    actions = mgr.audit_actions()
    assert "task.escalated" in actions
    assert "task.gave_up" in actions
    assert len(mgr.notify_calls) == 1
