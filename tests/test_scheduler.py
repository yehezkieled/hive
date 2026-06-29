"""Tests for the priority scheduler — facts prompt + rate limit + run loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest_asyncio

from hive.bus.router import MessageRouter
from hive.bus.task_store import TaskStore
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.notifications import NotificationDispatcher
from hive.process.manager import ProcessManager
from hive.process.scheduler import PriorityScheduler, maestro_for_actor


@pytest_asyncio.fixture
async def manager(router: MessageRouter):
    mgr = ProcessManager(
        router=router,
        max_sessions=3,
        notification_dispatcher=NotificationDispatcher(),
    )
    try:
        yield mgr
    finally:
        await mgr.kill_all()


def test_maestro_for_actor_resolves_dotted_names() -> None:
    assert maestro_for_actor("dev") == "dev"
    assert maestro_for_actor("dev.backend") == "dev"
    assert maestro_for_actor("dev.backend.w1") == "dev"
    assert maestro_for_actor("ops.infra.w7") == "ops"


def test_rate_limit_attributes_to_root_maestro(manager: ProcessManager) -> None:
    sched = PriorityScheduler(process_manager=manager, spawn_limit=2)
    # Lead under "dev" should consume "dev"'s budget — never its own.
    assert sched.can_autospawn("dev.backend")
    sched.record_autospawn("dev.backend")
    sched.record_autospawn("dev.backend.w1")  # nested actor, same root
    assert sched.spawn_count("dev") == 2
    # At limit: even from a different sub-team, the root is exhausted.
    assert not sched.can_autospawn("dev.frontend")
    # Different maestro is unaffected.
    assert sched.can_autospawn("ops")


def test_rate_limit_window_resets(manager: ProcessManager) -> None:
    sched = PriorityScheduler(process_manager=manager, spawn_limit=1)
    sched.record_autospawn("dev")
    assert not sched.can_autospawn("dev")
    sched.reset_window()
    assert sched.can_autospawn("dev")
    assert sched.spawn_count("dev") == 0


def test_refund_autospawn_decrements(manager: ProcessManager) -> None:
    """refund_autospawn should give back one slot in the current window."""
    sched = PriorityScheduler(process_manager=manager, spawn_limit=3)
    sched.record_autospawn("dev")
    sched.record_autospawn("dev")
    assert sched.spawn_count("dev") == 2
    sched.refund_autospawn("dev")
    assert sched.spawn_count("dev") == 1


def test_refund_does_not_go_negative(manager: ProcessManager) -> None:
    """Refunding a fresh actor is a no-op — never below zero."""
    sched = PriorityScheduler(process_manager=manager, spawn_limit=3)
    sched.refund_autospawn("dev")
    assert sched.spawn_count("dev") == 0
    # Still safe to refund again.
    sched.refund_autospawn("dev")
    assert sched.spawn_count("dev") == 0


def test_refund_uses_org_attribution(manager: ProcessManager) -> None:
    """Refund must mirror record's root-maestro attribution.

    A nested actor like ``dev.team.w1`` should refund ``dev``'s counter
    so a kill credits the same budget that the spawn debited.
    """
    sched = PriorityScheduler(process_manager=manager, spawn_limit=3)
    sched.record_autospawn("dev.team.w1")
    sched.record_autospawn("dev.team.w2")
    assert sched.spawn_count("dev") == 2
    sched.refund_autospawn("dev.team.w1")
    assert sched.spawn_count("dev") == 1
    # Different maestro is unaffected.
    assert sched.spawn_count("ops") == 0


async def test_facts_prompt_capacity_and_budget(manager: ProcessManager) -> None:
    """Facts prompt surfaces the numbers the maestro needs to allocate."""
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")

    sched = PriorityScheduler(process_manager=manager, spawn_limit=3)
    facts = await sched.build_facts_prompt("dev")

    assert "Capacity:" in facts
    # max_sessions=3, no running entities → 3 free
    assert "3/3 slots free" in facts
    assert "Spawn budget this window: 3/3 remaining" in facts
    assert "Pending tasks by priority" in facts
    assert "Org snapshot" in facts
    assert "24h token cost" in facts
    # The prompt must self-identify so the maestro knows who's being asked.
    assert "You are dev" in facts


async def test_facts_prompt_groups_pending_by_priority(
    manager: ProcessManager, task_store: TaskStore
) -> None:
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")
    await task_store.create(title="urgent thing", priority=0)
    await task_store.create(title="another urgent", priority=0)
    await task_store.create(title="someday", priority=4)

    sched = PriorityScheduler(process_manager=manager, task_store=task_store)
    facts = await sched.build_facts_prompt("dev")

    assert "P0 (2 task(s))" in facts
    assert "urgent thing" in facts
    assert "another urgent" in facts
    assert "P4 (1 task(s))" in facts
    assert "someday" in facts


async def test_facts_prompt_org_snapshot_only_includes_own_tree(
    manager: ProcessManager,
) -> None:
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")
    manager._entities["dev.backend"] = TeamLead(
        name="dev.backend", maestro_name="dev", model="sonnet"
    )
    manager.router.register("dev.backend")
    # Different maestro org — must not bleed into dev's snapshot.
    manager._entities["ops"] = Maestro(name="ops", model="sonnet")
    manager.router.register("ops")

    sched = PriorityScheduler(process_manager=manager)
    facts = await sched.build_facts_prompt("dev")

    assert "dev.backend" in facts
    assert "ops" not in facts


async def test_facts_prompt_handles_empty_state(manager: ProcessManager) -> None:
    """No pending tasks, no token usage, no extra org — degrades gracefully."""
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")

    sched = PriorityScheduler(process_manager=manager)
    facts = await sched.build_facts_prompt("dev")

    assert "(none)" in facts  # no pending tasks
    assert "(no calls in last 24h)" in facts  # no token usage


async def test_facts_prompt_no_action_path_uses_prose_not_block(
    manager: ProcessManager,
) -> None:
    """The 'no change needed' path must steer the maestro to plain prose, NOT to
    wrapping 'no action needed' in a <hive_actions> block — the latter parses as
    malformed JSON and spams the scheduler-poke reject loop (Ticket 046)."""
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")

    sched = PriorityScheduler(process_manager=manager)
    facts = await sched.build_facts_prompt("dev")

    # Explicitly tell the maestro to omit the block when idle, and reply in prose.
    assert "do NOT emit a <hive_actions> block" in facts
    assert "plain prose" in facts
    # And it must NOT present the old ambiguous "emit <hive_actions> … or respond
    # 'no action needed'" juxtaposition that induced the malformed block.
    assert "or respond 'no action needed'" not in facts


async def test_run_once_pokes_each_alive_maestro(manager: ProcessManager) -> None:
    """run_once sends a facts prompt to every alive maestro and resets the budget window."""
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")
    manager._entities["ops"] = Maestro(name="ops", model="sonnet")
    manager.router.register("ops")
    # A non-maestro entity should NOT be poked.
    manager._entities["dev.backend"] = TeamLead(
        name="dev.backend", maestro_name="dev", model="sonnet"
    )
    manager.router.register("dev.backend")

    sent: list[tuple[str, str]] = []

    async def fake_send(name: str, prompt: str) -> str:
        sent.append((name, prompt))
        return ""

    manager.send_to_entity = fake_send  # type: ignore[method-assign]

    sched = PriorityScheduler(process_manager=manager)
    sched.record_autospawn("dev")  # pre-existing budget that should reset
    poked = await sched.run_once()

    assert sorted(poked) == ["dev", "ops"]
    assert {name for name, _ in sent} == {"dev", "ops"}
    # Window reset on each tick.
    assert sched.spawn_count("dev") == 0


async def test_run_once_for_does_not_reset_window(manager: ProcessManager) -> None:
    """Manual /eval is one-shot — must not wipe the autonomous-spawn budget."""
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")

    manager.send_to_entity = AsyncMock(return_value="")  # type: ignore[method-assign]

    sched = PriorityScheduler(process_manager=manager, spawn_limit=3)
    sched.record_autospawn("dev")
    facts = await sched.run_once_for("dev")

    assert "You are dev" in facts
    assert sched.spawn_count("dev") == 1  # preserved
    manager.send_to_entity.assert_awaited_once()


async def test_run_loop_exits_cleanly_on_stop(manager: ProcessManager) -> None:
    """The main loop must honour stop_event without a long sleep delay."""
    sched = PriorityScheduler(process_manager=manager, eval_interval_minutes=60)
    stop = asyncio.Event()
    task = asyncio.create_task(sched.run(stop))
    await asyncio.sleep(0)  # let the loop reach wait_for
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done()
    assert task.exception() is None


async def test_run_loop_swallows_run_once_errors(manager: ProcessManager) -> None:
    """A flaky tick must not kill the loop — the next interval should still fire."""
    sched = PriorityScheduler(process_manager=manager, eval_interval_minutes=60)
    calls = 0

    async def boom() -> list[str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated tick failure")

    sched.run_once = boom  # type: ignore[method-assign]
    # Force the wait to expire immediately so we exercise the run_once branch.
    sched.eval_interval = sched.eval_interval.__class__(seconds=0)
    stop = asyncio.Event()
    task = asyncio.create_task(sched.run(stop))
    # Give it a moment to spin once, then stop.
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert calls >= 1
    assert task.exception() is None


class _FakeGate:
    """Gate-coordinator stand-in (Ticket 028): reports parked entities."""

    def __init__(self, parked: set[str] | None = None) -> None:
        self._parked = parked or set()

    def pending_request_id(self, entity_name: str) -> int | None:
        return 7 if entity_name in self._parked else None


async def test_run_once_skips_maestro_parked_at_gate(manager: ProcessManager) -> None:
    """A maestro parked at an interactive gate is skipped — never poked.

    Poking a parked PTY submits the gate's highlighted default (an
    unauthorised decision); the scheduler must step over it.
    """
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")
    manager._entities["ops"] = Maestro(name="ops", model="sonnet")
    manager.router.register("ops")

    sent: list[str] = []

    async def fake_send(name: str, prompt: str) -> str:
        sent.append(name)
        return ""

    manager.send_to_entity = fake_send  # type: ignore[method-assign]
    manager.gate_coordinator = _FakeGate(parked={"dev"})  # type: ignore[assignment]

    sched = PriorityScheduler(process_manager=manager)
    poked = await sched.run_once()

    assert poked == ["ops"]  # dev skipped
    assert sent == ["ops"]  # dev's PTY never touched


async def test_run_once_skips_maestro_awaiting_decision(manager: ProcessManager) -> None:
    """A maestro parked on a user decision (awaiting_decision) is skipped.

    Ticket 029: while waiting for the human's reply, nothing but that reply may
    advance the maestro — a scheduler poke must not push it into acting
    unconfirmed. This is a SEPARATE check from the interactive-gate skip.
    """
    dev = Maestro(name="dev", model="sonnet")
    dev.awaiting_decision = True
    manager._entities["dev"] = dev
    manager.router.register("dev")
    manager._entities["ops"] = Maestro(name="ops", model="sonnet")
    manager.router.register("ops")

    sent: list[str] = []

    async def fake_send(name: str, prompt: str) -> str:
        sent.append(name)
        return ""

    manager.send_to_entity = fake_send  # type: ignore[method-assign]

    sched = PriorityScheduler(process_manager=manager)
    poked = await sched.run_once()

    assert poked == ["ops"]  # dev skipped — it's awaiting the user
    assert sent == ["ops"]


async def test_run_once_for_parked_returns_notice_without_poking(
    manager: ProcessManager,
) -> None:
    """Manual /eval on a parked maestro reports the gate, does not poke."""
    manager._entities["dev"] = Maestro(name="dev", model="sonnet")
    manager.router.register("dev")
    manager.send_to_entity = AsyncMock(return_value="")  # type: ignore[method-assign]
    manager.gate_coordinator = _FakeGate(parked={"dev"})  # type: ignore[assignment]

    sched = PriorityScheduler(process_manager=manager)
    result = await sched.run_once_for("dev")

    assert "gate" in result.lower()
    manager.send_to_entity.assert_not_awaited()


# ---------------------------------------------------------------------------
# Ticket 029 / #144: nudge cadence — re-ping the user about a pending decision
# ---------------------------------------------------------------------------


class _CapturingChannel:
    """Notification channel that records every notification's text + kind."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.kinds: list[str] = []

    async def send(self, notification) -> None:  # noqa: ANN001 — duck-typed
        self.messages.append(notification.text)
        self.kinds.append(notification.kind)


def _capture_notifications(manager: ProcessManager) -> _CapturingChannel:
    channel = _CapturingChannel()
    dispatcher = NotificationDispatcher()
    dispatcher.register(channel)
    manager.notification_dispatcher = dispatcher
    return channel


async def test_run_once_nudges_user_after_interval(manager: ProcessManager) -> None:
    """A maestro parked on a decision whose last nudge is older than the
    interval gets a reminder re-pinged to the user — but is still never poked."""
    channel = _capture_notifications(manager)

    dev = Maestro(name="dev", model="sonnet")
    dev.awaiting_decision = True
    dev.last_nudged_at = datetime.now(UTC) - timedelta(minutes=61)
    manager._entities["dev"] = dev
    manager.router.register("dev")

    sent: list[str] = []

    async def fake_send(name: str, prompt: str) -> str:
        sent.append(name)
        return ""

    manager.send_to_entity = fake_send  # type: ignore[method-assign]

    sched = PriorityScheduler(process_manager=manager, decision_nudge_minutes=60)
    poked = await sched.run_once()

    assert poked == []  # never poked — still awaiting the user
    assert sent == []  # PTY untouched
    assert any("dev" in m for m in channel.messages)  # user re-pinged
    assert "decision_reminder" in channel.kinds
    # clock reset so the next reminder waits another full interval
    assert datetime.now(UTC) - dev.last_nudged_at < timedelta(minutes=1)


async def test_run_once_does_not_nudge_within_interval(manager: ProcessManager) -> None:
    """A recently-nudged parked maestro is skipped silently — no spam."""
    channel = _capture_notifications(manager)

    dev = Maestro(name="dev", model="sonnet")
    dev.awaiting_decision = True
    dev.last_nudged_at = datetime.now(UTC) - timedelta(minutes=10)
    manager._entities["dev"] = dev
    manager.router.register("dev")

    sched = PriorityScheduler(process_manager=manager, decision_nudge_minutes=60)
    poked = await sched.run_once()

    assert poked == []
    assert channel.messages == []  # no reminder within the interval


async def test_run_once_establishes_nudge_baseline_after_restart(
    manager: ProcessManager,
) -> None:
    """A restored parked maestro (flag persisted, in-memory clock lost) gets a
    fresh baseline on the first tick — no immediate nudge storm."""
    channel = _capture_notifications(manager)

    dev = Maestro(name="dev", model="sonnet")
    dev.awaiting_decision = True
    dev.last_nudged_at = None  # restored: persisted flag, transient clock gone
    manager._entities["dev"] = dev
    manager.router.register("dev")

    sched = PriorityScheduler(process_manager=manager, decision_nudge_minutes=60)
    poked = await sched.run_once()

    assert poked == []
    assert channel.messages == []  # baseline established, not nudged this tick
    assert dev.last_nudged_at is not None  # clock armed for next interval
