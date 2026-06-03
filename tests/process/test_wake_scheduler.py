"""Unit tests for the ``WakeScheduler`` collaborator (Ticket 004 slice 2).

These exercise ``WakeScheduler`` in isolation against a *stub* manager — no
real ProcessManager, no Postgres. The stub exposes only the surface the
scheduler reaches through ``self._mgr``: the entity registry, the
facade-owned ``_wake_tasks`` GC set and ``_wake_budget`` rate-limit deque,
the router, an ``_audit`` recorder, and a patchable ``send_to_entity``.

The DB/facade-level wake tests in ``test_process_manager`` still cover the
same flows end-to-end through the facade; these add fast, hermetic unit
coverage of the moved code and prove the composition pattern (collaborator
reaching shared state via ``self._mgr``).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hive.process.wake_scheduler import (
    _SPAWN_KICKOFF_TEXT,
    _WAKE_BUDGET_MAX_PER_WINDOW,
    _WAKE_BUDGET_WINDOW_SECONDS,
    _WAKE_ON_INBOUND_TEXT,
    WakeScheduler,
)

# ---------------------------------------------------------------------------
# Stub manager
# ---------------------------------------------------------------------------


class StubManager:
    """Minimal stand-in for ProcessManager's wake-facing surface.

    Mirrors exactly the facade-owned state ``WakeScheduler`` mutates via
    ``self._mgr``: ``_entities``, the ``_wake_tasks`` GC set, the
    ``_wake_budget`` rolling-window deque, ``router`` (so
    ``enable_wake_on_inbound`` has something to wire), and an ``_audit``
    recorder. ``send_to_entity`` defaults to a no-op recorder; tests swap
    in their own side effects.
    """

    def __init__(self) -> None:
        self._entities: dict[str, object] = {}
        self._wake_tasks: set[asyncio.Task] = set()
        self._wake_budget: dict[str, deque[datetime]] = defaultdict(deque)
        self.router = SimpleNamespace(wake_callback=None)

        self.audit_calls: list[tuple[str, str | None, dict | None]] = []
        self.sent: list[tuple[str, str]] = []

    async def _audit(
        self,
        action: str,
        target: str | None = None,
        details: dict | None = None,
        actor: str = "system",
    ) -> None:
        self.audit_calls.append((action, target, details))

    async def send_to_entity(self, name: str, text: str) -> None:
        self.sent.append((name, text))


@pytest.fixture
def mgr() -> StubManager:
    return StubManager()


@pytest.fixture
def wake(mgr: StubManager) -> WakeScheduler:
    return WakeScheduler(mgr)


async def _drain(mgr: StubManager) -> None:
    """Await every detached wake task the scheduler spawned."""
    while mgr._wake_tasks:
        await asyncio.gather(*list(mgr._wake_tasks))


# ---------------------------------------------------------------------------
# enable_wake_on_inbound — router wiring
# ---------------------------------------------------------------------------


def test_enable_wires_router_callback(wake: WakeScheduler, mgr: StubManager) -> None:
    """enable_wake_on_inbound points the router's wake_callback at the hook."""
    assert mgr.router.wake_callback is None
    wake.enable_wake_on_inbound()
    assert mgr.router.wake_callback == wake._on_inbound_wake


# ---------------------------------------------------------------------------
# _on_inbound_wake — guards, scheduling, GC-tracking
# ---------------------------------------------------------------------------


def test_unregistered_recipient_schedules_nothing(wake: WakeScheduler, mgr: StubManager) -> None:
    """A recipient with no entity row (e.g. ``user``) is skipped silently."""
    wake._on_inbound_wake("user")
    assert mgr._wake_tasks == set()
    assert mgr._wake_budget == {}


async def test_wake_send_uses_inbound_text(wake: WakeScheduler, mgr: StubManager) -> None:
    """A registered recipient gets a wake send with the inbound text."""
    mgr._entities["alice.bob"] = object()
    wake._on_inbound_wake("alice.bob")
    await _drain(mgr)
    assert ("alice.bob", _WAKE_ON_INBOUND_TEXT) in mgr.sent


async def test_wake_task_is_gc_tracked_then_discarded(
    wake: WakeScheduler, mgr: StubManager
) -> None:
    """Scheduled tasks are added to the facade-owned set and self-discard.

    The set lives on ``self._mgr`` (never a local copy), and each task wires
    ``add_done_callback(...discard)`` so it's released once it completes.
    """
    mgr._entities["alice.bob"] = object()
    wake._on_inbound_wake("alice.bob")
    # The wake task + its audit task are both tracked while in flight.
    assert len(mgr._wake_tasks) >= 1
    await _drain(mgr)
    assert mgr._wake_tasks == set()


async def test_scheduled_wake_emits_audit(wake: WakeScheduler, mgr: StubManager) -> None:
    """A non-throttled wake records an ``entity.wake_scheduled`` audit event."""
    mgr._entities["alice.bob"] = object()
    wake._on_inbound_wake("alice.bob")
    await _drain(mgr)
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.wake_scheduled") == 1


# ---------------------------------------------------------------------------
# _wake_budget — rolling rate-limit window
# ---------------------------------------------------------------------------


async def test_throttle_after_budget_exhausted(wake: WakeScheduler, mgr: StubManager) -> None:
    """Past the per-window cap: extra wakes are throttled, not sent.

    The cap is ``_WAKE_BUDGET_MAX_PER_WINDOW`` per recipient. The (cap+1)th
    call inside the window sends nothing and audits ``entity.wake_throttled``.
    """
    mgr._entities["alice.bob"] = object()
    for _ in range(_WAKE_BUDGET_MAX_PER_WINDOW + 1):
        wake._on_inbound_wake("alice.bob")
    await _drain(mgr)

    sends = [s for s in mgr.sent if s == ("alice.bob", _WAKE_ON_INBOUND_TEXT)]
    assert len(sends) == _WAKE_BUDGET_MAX_PER_WINDOW
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.wake_throttled") == 1
    assert actions.count("entity.wake_scheduled") == _WAKE_BUDGET_MAX_PER_WINDOW


async def test_throttle_mutates_facade_budget_not_a_copy(
    wake: WakeScheduler, mgr: StubManager
) -> None:
    """The budget deque written is the facade-owned one (not a local copy)."""
    mgr._entities["alice.bob"] = object()
    wake._on_inbound_wake("alice.bob")
    await _drain(mgr)
    # The recipient's deque now holds one timestamp, on the manager's dict.
    assert len(mgr._wake_budget["alice.bob"]) == 1


async def test_stale_budget_entries_expire(wake: WakeScheduler, mgr: StubManager) -> None:
    """Timestamps older than the window are dropped, freeing the budget.

    Seed the deque full with timestamps just past the window edge; the next
    wake should evict them all and proceed (send, not throttle).
    """
    mgr._entities["alice.bob"] = object()
    stale = datetime.now(UTC) - timedelta(seconds=_WAKE_BUDGET_WINDOW_SECONDS + 1)
    mgr._wake_budget["alice.bob"].extend([stale] * _WAKE_BUDGET_MAX_PER_WINDOW)

    wake._on_inbound_wake("alice.bob")
    await _drain(mgr)

    sends = [s for s in mgr.sent if s == ("alice.bob", _WAKE_ON_INBOUND_TEXT)]
    assert len(sends) == 1
    # All stale entries evicted; only the fresh one remains.
    assert len(mgr._wake_budget["alice.bob"]) == 1


# ---------------------------------------------------------------------------
# _wake_entity — failure handling
# ---------------------------------------------------------------------------


async def test_wake_entity_silent_on_already_running(wake: WakeScheduler, mgr: StubManager) -> None:
    """'already running' RuntimeError is swallowed with no failure audit."""

    async def boom(name: str, text: str) -> None:
        raise RuntimeError("Entity alice.bob already running")

    mgr.send_to_entity = boom  # type: ignore[method-assign]
    await wake._wake_entity("alice.bob")
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.wake_failed" not in actions


async def test_wake_entity_audits_other_failures(wake: WakeScheduler, mgr: StubManager) -> None:
    """A non-'already running' error is logged + audited, not raised."""

    async def boom(name: str, text: str) -> None:
        raise ValueError("postgres down")

    mgr.send_to_entity = boom  # type: ignore[method-assign]
    await wake._wake_entity("alice.bob")  # must not raise
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.wake_failed") == 1


# ---------------------------------------------------------------------------
# _auto_kickoff — spawn kickoff send + failure handling
# ---------------------------------------------------------------------------


async def test_auto_kickoff_sends_spawn_text(wake: WakeScheduler, mgr: StubManager) -> None:
    """auto_kickoff nudges the freshly spawned target with the kickoff prompt."""
    await wake._auto_kickoff("dev.backend")
    assert mgr.sent == [("dev.backend", _SPAWN_KICKOFF_TEXT)]


async def test_auto_kickoff_audits_failure_without_raising(
    wake: WakeScheduler, mgr: StubManager
) -> None:
    """A failed kickoff send is audited as ``entity.kickoff_failed``, swallowed."""

    async def boom(name: str, text: str) -> None:
        raise RuntimeError("spawn race")

    mgr.send_to_entity = boom  # type: ignore[method-assign]
    await wake._auto_kickoff("dev.backend")  # must not raise
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.kickoff_failed") == 1
