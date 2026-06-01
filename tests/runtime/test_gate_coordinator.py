"""Tests for GateCoordinator — park-then-wake lifecycle for one gate.

The coordinator owns the doorbell (asyncio.Event) and the approval-row
surface. ``resolve(entity, gate)`` creates a pending gate row, parks on the
doorbell forever (no timeout, never auto-decide), and on wake reads the
decision off the resolved row and returns the planned keystrokes.

Tested with a fake approval store + the real doorbell so no PTY or DB is
needed.
"""

from __future__ import annotations

import asyncio

import pytest

from hive.runtime.gate_coordinator import GateCoordinator
from hive.runtime.gates import Gate

_ENTER = "\r"
_DOWN = "\x1b[B"


class _FakeStore:
    """In-memory stand-in for ModeRequestStore, gate-row subset."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self._next_id = 1
        self.created: list[dict] = []

    async def create(
        self,
        requester: str,
        requested_mode: str,
        approver: str,
        reason: str | None = None,
        kind: str = "mode_request",
    ) -> dict:
        row = {
            "id": self._next_id,
            "requester": requester,
            "requested_mode": requested_mode,
            "approver": approver,
            "reason": reason,
            "kind": kind,
            "status": "pending",
        }
        self.rows[self._next_id] = row
        self.created.append(row)
        self._next_id += 1
        return dict(row)

    async def get(self, request_id: int) -> dict | None:
        row = self.rows.get(request_id)
        return dict(row) if row else None

    def _resolve(self, request_id: int, status: str) -> None:
        self.rows[request_id]["status"] = status

    def _resolve_ask(self, request_id: int, chosen_option: int) -> None:
        """Mark an ask gate approved with the user's chosen option index."""
        self.rows[request_id]["status"] = "approved"
        self.rows[request_id]["chosen_option"] = chosen_option


def _plan_gate() -> Gate:
    return Gate(kind="plan", payload={"plan": "1. ship it"})


async def test_resolve_blocks_until_doorbell_rings() -> None:
    """resolve() must not return while the gate is unanswered."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _plan_gate(), approver="user"))
    await asyncio.sleep(0.05)
    assert not task.done()  # parked, no auto-decide

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_resolve_creates_gate_kind_row() -> None:
    """A pending row tagged kind='gate' is the durable surface."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _plan_gate(), approver="user"))
    await asyncio.sleep(0.05)

    assert len(store.created) == 1
    row = store.created[0]
    assert row["kind"] == "gate"
    assert row["requester"] == "dev"
    assert row["approver"] == "user"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_approve_wakes_with_enter_keys() -> None:
    """Ringing the doorbell with an approved row returns the approve keys."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _plan_gate(), approver="user"))
    await asyncio.sleep(0.05)
    request_id = store.created[0]["id"]

    store._resolve(request_id, "approved")
    coordinator.ring("dev")

    keys = await asyncio.wait_for(task, timeout=1.0)
    assert keys == [_ENTER]


async def test_deny_wakes_with_navigation_keys() -> None:
    """A denied row returns the deny keystroke sequence."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _plan_gate(), approver="user"))
    await asyncio.sleep(0.05)
    request_id = store.created[0]["id"]

    store._resolve(request_id, "denied")
    coordinator.ring("dev")

    keys = await asyncio.wait_for(task, timeout=1.0)
    assert keys == [_DOWN, _DOWN, _ENTER]


def _ask_gate() -> Gate:
    return Gate(
        kind="ask",
        payload={
            "question": "Which database?",
            "options": ["Postgres", "SQLite", "MySQL"],
        },
    )


async def test_ask_gate_creates_gate_kind_row_with_question_reason() -> None:
    """An ask gate parks on the same gate-kind row; the reason is the question."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _ask_gate(), approver="user"))
    await asyncio.sleep(0.05)

    row = store.created[0]
    assert row["kind"] == "gate"
    assert row["requested_mode"] == "ask"
    assert "Which database?" in (row["reason"] or "")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_ask_wakes_with_option_navigation_keys() -> None:
    """Ringing an ask gate resolved to option index 2 returns Down x2 + Enter."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _ask_gate(), approver="user"))
    await asyncio.sleep(0.05)
    request_id = store.created[0]["id"]

    store._resolve_ask(request_id, 2)
    coordinator.ring("dev")

    keys = await asyncio.wait_for(task, timeout=1.0)
    assert keys == [_DOWN, _DOWN, _ENTER]


async def test_ask_first_option_wakes_with_enter_only() -> None:
    """Option index 0 needs no navigation — just Enter."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    task = asyncio.create_task(coordinator.resolve("dev", _ask_gate(), approver="user"))
    await asyncio.sleep(0.05)
    store._resolve_ask(store.created[0]["id"], 0)
    coordinator.ring("dev")

    keys = await asyncio.wait_for(task, timeout=1.0)
    assert keys == [_ENTER]


async def test_ring_unknown_entity_is_noop() -> None:
    """Ringing a doorbell that was never registered must not raise."""
    coordinator = GateCoordinator(_FakeStore())
    coordinator.ring("ghost")  # no registered gate — silently ignored


async def test_resolve_clears_doorbell_after_wake() -> None:
    """After a gate resolves the doorbell is cleared so a later gate parks
    again instead of waking instantly on the stale event."""
    store = _FakeStore()
    coordinator = GateCoordinator(store)

    first = asyncio.create_task(coordinator.resolve("dev", _plan_gate(), approver="user"))
    await asyncio.sleep(0.05)
    store._resolve(store.created[0]["id"], "approved")
    coordinator.ring("dev")
    await asyncio.wait_for(first, timeout=1.0)

    second = asyncio.create_task(coordinator.resolve("dev", _plan_gate(), approver="user"))
    await asyncio.sleep(0.05)
    assert not second.done()  # must park again, not wake on the stale ring

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
