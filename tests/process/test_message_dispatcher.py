"""Unit tests for the ``MessageDispatcher`` collaborator (Ticket 004 slice 3).

These exercise ``MessageDispatcher`` in isolation against a *stub* manager —
no real ProcessManager, no Postgres, no Claude subprocess. The stub exposes
only the surface the dispatcher reaches through ``self._mgr``: the entity
registry, a fake router, the eight facade-owned ``_last_*`` introspection
lists, the ``_kickoff_tasks`` GC set, the ``_parse_failure_budget`` deque,
an ``_audit`` recorder, ``_notify``, ``_parent_of``, and patchable
cross-module facade methods (``request_mode_change``, ``spawn_worker`` …).

The most fragile seam in the whole split lives here: ``_handle_actions``
resets the ``_last_*`` lists by **rebinding** (``self._mgr._last_x = []``).
These tests assert the rebind lands on the facade-owned attribute — a local
rebind would leave ``mgr._last_*`` stale and silently break every assertion.

The DB/facade-level tests in ``test_process_manager`` still cover the same
flows end-to-end through the facade; these add fast, hermetic unit coverage
of the moved code and prove the composition pattern.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from hive.bus.actions import Action
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import Worker
from hive.process.message_dispatcher import (
    _PARSE_FAILURE_MAX_PER_WINDOW,
    _PARSE_FAILURE_WINDOW_SECONDS,
    MessageDispatcher,
)
from hive.process.wake_scheduler import (
    _WAKE_BUDGET_MAX_PER_WINDOW,
    _WAKE_ON_INBOUND_TEXT,
    WakeScheduler,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRouter:
    """Records ``route`` calls and holds real per-recipient queues.

    No real MessageStore needed. The queues back ``has_pending`` /
    ``get_next`` so ``send_to_entity``'s drain phase and the turn-end
    inbox check (Ticket 023, design D4) see genuine queue state.
    """

    def __init__(self) -> None:
        self.routed: list[tuple[str, str, str]] = []
        self._queues: dict[str, list[tuple[str, str]]] = defaultdict(list)

    async def route(self, sender: str, recipient: str, content: str) -> None:
        self.routed.append((sender, recipient, content))
        self._queues[recipient].append((sender, content))

    def has_pending(self, entity_name: str) -> bool:
        return bool(self._queues.get(entity_name))

    async def get_next(self, entity_name: str, timeout: float | None = None) -> _FakeMessage | None:
        queue = self._queues.get(entity_name)
        if not queue:
            return None
        sender, content = queue.pop(0)
        return _FakeMessage(sender=sender, content=content)


class _FakeMessage:
    """Just the two fields the drain phase reads off a routed message."""

    def __init__(self, sender: str, content: str) -> None:
        self.sender = sender
        self.content = content


class FakeTurnAdapter:
    """Adapter double for driving ``send_to_entity`` end-to-end.

    ``on_turn`` is an async hook that runs INSIDE the turn (between the
    drain phase and turn completion) — the seam for delivering mid-turn
    mail or parking on a fake interactive gate.
    """

    def __init__(
        self,
        response: str = "ok",
        on_turn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._response = response
        self._on_turn = on_turn
        self.prompts: list[str] = []

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        self.prompts.append(prompt)
        if self._on_turn is not None:
            await self._on_turn()
        usage: dict = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "session_id": "sess-1",
            "cost_usd": None,
        }
        return self._response, usage


class FakeGateCoordinator:
    """Doorbell-registry stand-in (Ticket 028): reports which entities are
    parked at an interactive gate via ``pending_request_id``."""

    def __init__(self, parked: set[str] | None = None) -> None:
        self._parked = parked or set()

    def pending_request_id(self, entity_name: str) -> int | None:
        return 42 if entity_name in self._parked else None


class StubManager:
    """Minimal stand-in for ProcessManager's dispatch-facing surface.

    Mirrors exactly the facade-owned state ``MessageDispatcher`` mutates via
    ``self._mgr``: ``_entities``, the eight ``_last_*`` introspection lists,
    the ``_kickoff_tasks`` GC set, the ``_parse_failure_budget`` rolling
    deque, ``router``, plus ``_audit``/``_notify``/``_parent_of`` and the
    cross-module facade methods the dispatcher calls. The cross-module
    methods default to recorders; tests swap in side effects as needed.
    """

    def __init__(self) -> None:
        self._entities: dict[str, object] = {}
        self.router = FakeRouter()
        self.scheduler = None
        # Pending-gate guard surface (Ticket 028). None by default = no
        # coordinator wired, so is_parked_at_gate is always False and the
        # existing full-turn tests are unaffected.
        self.gate_coordinator: FakeGateCoordinator | None = None

        # --- send_to_entity surface (turn-end inbox check, Ticket 023 D4) ---
        # A REAL WakeScheduler wired to this stub: the turn-end check must
        # go through the existing budget machinery, so the tests exercise
        # the genuine article, not a recorder.
        self.blueprint_store = None
        self.attachment_store = None
        self._compacting: set[str] = set()
        self._wake_tasks: set[asyncio.Task] = set()
        self._wake_budget: dict[str, deque[datetime]] = defaultdict(deque)
        self.wake = WakeScheduler(self)
        self.adapter = FakeTurnAdapter()
        # Wake follow-up sends land here (recorder), keeping unit tests
        # from recursing back into the dispatcher under test.
        self.sent: list[tuple[str, str]] = []
        self.persisted: list[object] = []
        self.usage_records: list[dict] = []

        # The eight facade-owned introspection lists. _handle_actions
        # REBINDS each of these (= []) through self._mgr; tests assert the
        # rebind lands here, not on a dispatcher-local copy.
        self._last_routed_actions: list[str] = ["STALE"]
        self._last_mode_requests: list[int] = [-1]
        self._last_failure_reports: list[int] = [-1]
        self._last_spawned_teams: list[str] = ["STALE"]
        self._last_spawned_workers: list[str] = ["STALE"]
        self._last_killed_entities: list[str] = ["STALE"]
        self._last_vault_requests: list[int] = [-1]
        self._last_kickoffs: list[str] = ["STALE"]

        self._kickoff_tasks: set[asyncio.Task] = set()
        self._parse_failure_budget: dict[str, deque[datetime]] = defaultdict(deque)

        self.audit_calls: list[tuple[str, str | None, dict | None]] = []
        self.notify_calls: list[tuple[str, str | None, dict | None]] = []
        self.kickoffs: list[str] = []

        # Cross-module facade methods (recorders by default).
        self.mode_change_calls: list[tuple[str, str, str | None]] = []
        self.spawned_worker_args: list[tuple] = []
        self.killed: list[str] = []
        self.task_failures: list[tuple[int, str]] = []

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
        kind: str | None = None,
        data: dict | None = None,
    ) -> None:
        self.notify_calls.append((message, kind, data))

    def _parent_of(self, entity: object) -> str | None:
        if isinstance(entity, Worker):
            return entity.lead_name or None
        if isinstance(entity, TeamLead):
            return entity.maestro_name or None
        return None

    # --- cross-module facade methods routed through self._mgr ---

    async def request_mode_change(
        self, requester: str, requested_mode: str, reason: str | None = None
    ) -> int:
        self.mode_change_calls.append((requester, requested_mode, reason))
        return 42

    async def handle_task_failure(self, task_id: int, error: str) -> None:
        self.task_failures.append((task_id, error))

    async def spawn_worker(self, lead: str, **kwargs) -> Worker:
        self.spawned_worker_args.append((lead, kwargs))
        worker = Worker(name=f"{lead}.w1", lead_name=lead)
        return worker

    async def kill_entity(self, target: str) -> None:
        self.killed.append(target)

    async def _auto_kickoff(self, target: str) -> None:
        self.kickoffs.append(target)

    # --- send_to_entity surface (turn-end inbox check, Ticket 023 D4) ---

    def _peer_directory_for(self, entity_name: str) -> str:
        return ""

    def is_parked_at_gate(self, entity_name: str) -> bool:
        gc = self.gate_coordinator
        return gc is not None and gc.pending_request_id(entity_name) is not None

    async def _persist(self, entity: object) -> None:
        self.persisted.append(entity)

    async def _record_usage(self, entity: object, usage: dict | None) -> None:
        if usage is not None:
            self.usage_records.append(usage)

    async def _get_or_create_adapter(self, entity: object) -> FakeTurnAdapter:
        return self.adapter

    async def send_to_entity(self, name: str, text: str) -> None:
        self.sent.append((name, text))


@pytest.fixture
def mgr() -> StubManager:
    return StubManager()


@pytest.fixture
def dispatcher(mgr: StubManager) -> MessageDispatcher:
    d = MessageDispatcher(mgr)
    # Mirror the facade thin-delegate so send_to_entity's tail call
    # (``self._mgr._handle_actions``) reaches the dispatcher under test.
    mgr._handle_actions = d._handle_actions  # type: ignore[attr-defined]
    return d


async def _drain_kickoffs(mgr: StubManager) -> None:
    while mgr._kickoff_tasks:
        await asyncio.gather(*list(mgr._kickoff_tasks))


async def _drain_wakes(mgr: StubManager) -> None:
    """Await every detached wake task the turn-end check spawned.

    The explicit ``sleep(0)`` matters: gathering an already-finished
    task returns without yielding to the loop, so the task's
    ``discard`` done-callback would never run and the loop would spin.
    """
    while mgr._wake_tasks:
        await asyncio.gather(*list(mgr._wake_tasks))
        await asyncio.sleep(0)


@contextmanager
def _hermetic_send_flags():
    """Pin the manager-module config flags ``send_to_entity`` reads.

    Auto-retrieve, auto-compact, and MCP config generation are all
    orthogonal to the turn-end inbox check — disable them so these
    tests stay hermetic (no knowledge stores, no config files).
    """
    with (
        patch("hive.process.manager.AUTO_RETRIEVE_ENABLED", False),
        patch("hive.process.manager.AUTO_COMPACT_ENABLED", False),
        patch("hive.process.manager.mcp_servers_enabled", lambda: False),
    ):
        yield


# ---------------------------------------------------------------------------
# The fragile seam — _last_* lists are REBOUND through the facade
# ---------------------------------------------------------------------------


async def test_all_last_lists_rebound_on_facade(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """Every ``_last_*`` list is reset to ``[]`` on the manager, not a local.

    Seeded with sentinel values; after a no-action dispatch they must be the
    empty facade-owned lists. A local rebind in the collaborator would leave
    these sentinels in place — the canary for the whole slice.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    await dispatcher._handle_actions("dev", "clean", [])

    assert mgr._last_routed_actions == []
    assert mgr._last_mode_requests == []
    assert mgr._last_failure_reports == []
    assert mgr._last_spawned_teams == []
    assert mgr._last_spawned_workers == []
    assert mgr._last_killed_entities == []
    assert mgr._last_vault_requests == []
    assert mgr._last_kickoffs == []


# ---------------------------------------------------------------------------
# _handle_actions — clean text + unknown entity
# ---------------------------------------------------------------------------


async def test_unknown_entity_returns_clean_text(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """An unregistered sender short-circuits, returning the clean text."""
    result = await dispatcher._handle_actions("ghost", "hello", [])
    assert result == "hello"
    # Nothing reset — the early return precedes the rebind block.
    assert mgr._last_routed_actions == ["STALE"]


async def test_clean_text_returned(dispatcher: MessageDispatcher, mgr: StubManager) -> None:
    """The dispatcher echoes the clean text back unchanged."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    result = await dispatcher._handle_actions("dev", "the analysis", [])
    assert result == "the analysis"


# ---------------------------------------------------------------------------
# Action routing — message, permission denied, mode change
# ---------------------------------------------------------------------------


async def test_message_action_routes_and_tracks(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A permitted message is routed and the recipient recorded in _last_*."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    mgr._entities["dev.backend"] = TeamLead(
        name="dev.backend", team_name="backend", maestro_name="dev"
    )

    actions = [Action(type="message", to="dev.backend", text="go")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert ("dev", "dev.backend", "go") in mgr.router.routed
    assert mgr._last_routed_actions == ["dev.backend"]
    assert any(a == "peer_message_sent" for (a, _t, _d) in mgr.audit_calls)


async def test_message_to_unknown_recipient_skipped(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """An unknown recipient is skipped: not delivered, not tracked.

    Since Ticket 023 the drop is no longer silent — the only routed
    message is the ``system -> sender`` rejection note.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    actions = [Action(type="message", to="dev.ghost", text="hi")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert [r for r in mgr.router.routed if r[0] != "system"] == []
    assert mgr._last_routed_actions == []


async def test_request_mode_change_tracked(dispatcher: MessageDispatcher, mgr: StubManager) -> None:
    """A request_mode_change action calls the facade and records the req id."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    actions = [Action(type="request_mode_change", requested_mode="bypass", reason="why")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert mgr.mode_change_calls == [("dev", "bypass", "why")]
    assert mgr._last_mode_requests == [42]


# ---------------------------------------------------------------------------
# report_failure — _task_id_for inference (intra-module call)
# ---------------------------------------------------------------------------


async def test_report_failure_infers_task_id_for_worker(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """report_failure with no task_id infers it from the worker's bound task."""
    worker = Worker(name="dev.backend.w1", lead_name="dev.backend", task_id=7)
    mgr._entities["dev.backend.w1"] = worker

    actions = [Action(type="report_failure", reason="boom")]
    await dispatcher._handle_actions("dev.backend.w1", "done", actions)

    assert mgr.task_failures == [(7, "boom")]
    assert mgr._last_failure_reports == [7]


async def test_report_failure_no_task_skipped(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A maestro (no bound task) reporting failure is skipped, not crashed."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    actions = [Action(type="report_failure", reason="boom")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert mgr.task_failures == []
    assert mgr._last_failure_reports == []


# ---------------------------------------------------------------------------
# spawn_worker — retired on every path (ADR 0013): deny, audit, no kickoff
# ---------------------------------------------------------------------------


async def test_spawn_worker_denied_no_spawn_no_kickoff(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A lead's spawn_worker is denied (ADR 0013): nothing spawns, no kickoff.

    The facade's ``spawn_worker`` is never reached, nothing lands in the
    ``_last_*`` lists, no kickoff task is scheduled, and the
    ``entity.spawn_worker_denied`` audit (Ticket 018's drainage proof)
    is emitted.
    """
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities["dev.backend"] = lead

    actions = [Action(type="spawn_worker", worker_name="w1", task_id=3)]
    await dispatcher._handle_actions("dev.backend", "done", actions)

    assert mgr.spawned_worker_args == []
    assert mgr._last_spawned_workers == []
    assert mgr._last_kickoffs == []
    assert mgr._kickoff_tasks == set()
    assert mgr.kickoffs == []
    denied = [(t, d) for (a, t, d) in mgr.audit_calls if a == "entity.spawn_worker_denied"]
    assert len(denied) == 1


async def test_spawn_worker_denied_lead_receives_rejection_note(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """The denied lead RECEIVES the rejection via the _reject_action plumbing.

    A deny without feedback would leave the lead sync-waiting forever on a
    phantom worker's report (ADR 0013). The note names the replacement —
    the Workflow tool — and must not invite a retry of spawn_worker.
    """
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities["dev.backend"] = lead

    actions = [Action(type="spawn_worker", worker_name="w1")]
    await dispatcher._handle_actions("dev.backend", "done", actions)

    # Feedback: a system note lands in the SENDER's queue.
    notes = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev.backend"]
    assert len(notes) == 1
    note_body = notes[0][2]
    assert "[action rejected]" in note_body
    assert "retired" in note_body
    assert "ADR 0013" in note_body
    assert "Workflow" in note_body  # names the replacement
    # Must NOT invite a retry of spawn_worker.
    assert "retry" not in note_body.lower()
    assert "resend" not in note_body.lower()

    # _reject_action's own audit fires alongside the drainage-proof audit.
    rejected = [(t, d) for (a, t, d) in mgr.audit_calls if a == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0][1]["action_type"] == "spawn_worker"
    denied = [(t, d) for (a, t, d) in mgr.audit_calls if a == "entity.spawn_worker_denied"]
    assert len(denied) == 1
    assert denied[0][1]["reason"] == "retired"


async def test_spawn_worker_denied_maestro_receives_rejection_note(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A maestro's spawn_worker is denied with the same feedback (ADR 0013).

    Even with no ``lead`` field — the retirement deny fires before the
    old missing-lead guard, so every actor gets the note.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    actions = [Action(type="spawn_worker")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert mgr.spawned_worker_args == []
    notes = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev"]
    assert len(notes) == 1
    note_body = notes[0][2]
    assert "[action rejected]" in note_body
    assert "retired" in note_body
    assert "Workflow" in note_body
    denied = [(t, d) for (a, t, d) in mgr.audit_calls if a == "entity.spawn_worker_denied"]
    assert len(denied) == 1
    assert denied[0][1]["reason"] == "retired"


async def test_kill_entity_action_tracked(dispatcher: MessageDispatcher, mgr: StubManager) -> None:
    """A permitted kill_entity routes through the facade and is recorded."""
    maestro = Maestro(name="dev", model="sonnet")
    target = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities["dev"] = maestro
    mgr._entities["dev.backend"] = target

    actions = [Action(type="kill_entity", target="dev.backend")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert mgr.killed == ["dev.backend"]
    assert mgr._last_killed_entities == ["dev.backend"]


# ---------------------------------------------------------------------------
# Alias resolution + rejection feedback (Ticket 023, design D2)
# ---------------------------------------------------------------------------


async def test_lead_maestro_alias_delivers_to_org_root(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A lead's ``to:"maestro"`` resolves to the org root before lookup."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    mgr._entities["dev.backend"] = TeamLead(
        name="dev.backend", team_name="backend", maestro_name="dev"
    )

    actions = [Action(type="message", to="maestro", text="proposal")]
    await dispatcher._handle_actions("dev.backend", "done", actions)

    assert ("dev.backend", "dev", "proposal") in mgr.router.routed
    assert mgr._last_routed_actions == ["dev"]


async def test_unknown_recipient_audits_rejection_and_notes_sender(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """An unknown recipient now feeds back: audit + system note to the sender.

    Before Ticket 023 the message was dropped with only a logger.warning —
    the sender waited forever (failure F2). The note names the failure and
    the correct form so the sender can self-correct next turn.
    """
    mgr._entities["dev.backend"] = TeamLead(
        name="dev.backend", team_name="backend", maestro_name="dev"
    )

    actions = [Action(type="message", to="maestro.strutils", text="breakdown")]
    await dispatcher._handle_actions("dev.backend", "done", actions)

    # The peer message itself was NOT routed.
    assert ("dev.backend", "maestro.strutils", "breakdown") not in mgr.router.routed
    assert mgr._last_routed_actions == []

    # Audit: action_rejected, actor=sender, target=attempted recipient.
    rejected = [(t, d) for (a, t, d) in mgr.audit_calls if a == "action_rejected"]
    assert len(rejected) == 1
    target, details = rejected[0]
    assert target == "maestro.strutils"
    assert details["sender"] == "dev.backend"
    assert "unknown" in details["reason"]

    # Feedback: a system note to the SENDER naming the failure + correct form.
    notes = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev.backend"]
    assert len(notes) == 1
    note_body = notes[0][2]
    assert "[action rejected]" in note_body
    assert "maestro.strutils" in note_body
    assert "maestro" in note_body  # the correct form: the alias


async def test_permission_denied_audits_rejection_and_notes_sender(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A permission-denied message feeds back the same way as unknown recipient.

    A worker may message only its lead — ``to:"maestro"`` resolves fine but
    is denied. The worker gets an audit + a system note naming the correct
    form (its parent) instead of a silent drop.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    mgr._entities["dev.backend"] = TeamLead(
        name="dev.backend", team_name="backend", maestro_name="dev"
    )
    mgr._entities["dev.backend.w1"] = Worker(name="dev.backend.w1", lead_name="dev.backend")

    actions = [Action(type="message", to="maestro", text="skip the chain")]
    await dispatcher._handle_actions("dev.backend.w1", "done", actions)

    # Not delivered, not tracked.
    assert ("dev.backend.w1", "dev", "skip the chain") not in mgr.router.routed
    assert mgr._last_routed_actions == []

    rejected = [(t, d) for (a, t, d) in mgr.audit_calls if a == "action_rejected"]
    assert len(rejected) == 1
    target, details = rejected[0]
    assert target == "maestro"
    assert details["sender"] == "dev.backend.w1"
    assert "permission" in details["reason"]

    notes = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev.backend.w1"]
    assert len(notes) == 1
    note_body = notes[0][2]
    assert "[action rejected]" in note_body
    assert "dev.backend" in note_body  # the correct form: its parent


async def test_maestro_self_alias_rejected_with_feedback(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A maestro's ``to:"maestro"`` resolves to itself — the existing
    self-message ban rejects it, and the feedback note explains why
    instead of dropping silently.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    actions = [Action(type="message", to="maestro", text="hello me")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert ("dev", "dev", "hello me") not in mgr.router.routed
    assert mgr._last_routed_actions == []

    rejected = [(t, d) for (a, t, d) in mgr.audit_calls if a == "action_rejected"]
    assert len(rejected) == 1
    target, details = rejected[0]
    assert target == "maestro"
    assert details["sender"] == "dev"

    notes = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev"]
    assert len(notes) == 1
    note_body = notes[0][2]
    assert "[action rejected]" in note_body
    assert "yourself" in note_body  # explains the alias resolved to the sender


async def test_maestro_parent_alias_rejected_with_feedback(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A maestro has no parent — ``to:"parent"`` is rejected with feedback."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    actions = [Action(type="message", to="parent", text="anyone up there?")]
    await dispatcher._handle_actions("dev", "done", actions)

    assert mgr._last_routed_actions == []
    rejected = [(t, d) for (a, t, d) in mgr.audit_calls if a == "action_rejected"]
    assert len(rejected) == 1
    notes = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev"]
    assert len(notes) == 1


async def test_worker_parent_alias_delivers_to_lead(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A worker's ``to:"parent"`` resolves to its immediate parent (the lead)."""
    mgr._entities["dev.backend"] = TeamLead(
        name="dev.backend", team_name="backend", maestro_name="dev"
    )
    mgr._entities["dev.backend.w1"] = Worker(name="dev.backend.w1", lead_name="dev.backend")

    actions = [Action(type="message", to="parent", text="done, tests green")]
    await dispatcher._handle_actions("dev.backend.w1", "done", actions)

    assert ("dev.backend.w1", "dev.backend", "done, tests green") in mgr.router.routed
    assert mgr._last_routed_actions == ["dev.backend"]


# ---------------------------------------------------------------------------
# _handle_parse_errors — debounce window + cap escalation
# ---------------------------------------------------------------------------


async def test_parse_error_under_cap_sends_feedback(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """Under the cap, a system->entity feedback message is routed."""
    worker = Worker(name="dev.backend.w1", lead_name="dev.backend")
    mgr._entities["dev.backend.w1"] = worker

    await dispatcher._handle_parse_errors(worker, ["bad json"])

    feedback = [r for r in mgr.router.routed if r[0] == "system" and r[1] == "dev.backend.w1"]
    assert len(feedback) == 1
    assert any(a == "entity.parse_failure_feedback" for (a, _t, _d) in mgr.audit_calls)
    # One timestamp recorded in the facade-owned budget.
    assert len(mgr._parse_failure_budget["dev.backend.w1"]) == 1


async def test_parse_error_over_cap_escalates_to_parent(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """Past the cap, feedback is suppressed and the parent is notified once."""
    worker = Worker(name="dev.backend.w1", lead_name="dev.backend")
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities["dev.backend.w1"] = worker
    mgr._entities["dev.backend"] = lead

    # Pre-fill the window to the cap so this call tips it over.
    now = datetime.now(UTC)
    mgr._parse_failure_budget["dev.backend.w1"].extend([now] * _PARSE_FAILURE_MAX_PER_WINDOW)

    await dispatcher._handle_parse_errors(worker, ["bad json"])

    # Escalation goes to the parent lead, not feedback to the worker.
    to_parent = [r for r in mgr.router.routed if r[1] == "dev.backend"]
    to_worker = [r for r in mgr.router.routed if r[1] == "dev.backend.w1"]
    assert len(to_parent) == 1
    assert to_worker == []
    assert any(a == "entity.parse_failure_capped" for (a, _t, _d) in mgr.audit_calls)


async def test_parse_error_stale_entries_expire(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """Timestamps older than the window are evicted before the cap check."""
    worker = Worker(name="dev.backend.w1", lead_name="dev.backend")
    mgr._entities["dev.backend.w1"] = worker

    stale = datetime.now(UTC) - timedelta(seconds=_PARSE_FAILURE_WINDOW_SECONDS + 1)
    mgr._parse_failure_budget["dev.backend.w1"].extend([stale] * _PARSE_FAILURE_MAX_PER_WINDOW)

    await dispatcher._handle_parse_errors(worker, ["bad json"])

    # All stale entries evicted, one fresh appended → under cap → feedback.
    assert len(mgr._parse_failure_budget["dev.backend.w1"]) == 1
    feedback = [r for r in mgr.router.routed if r[0] == "system"]
    assert len(feedback) == 1


async def test_parse_error_cap_for_maestro_notifies_user(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A maestro over cap has no Hive parent — escalation surfaces to the user."""
    maestro = Maestro(name="dev", model="sonnet")
    mgr._entities["dev"] = maestro

    now = datetime.now(UTC)
    mgr._parse_failure_budget["dev"].extend([now] * _PARSE_FAILURE_MAX_PER_WINDOW)

    await dispatcher._handle_parse_errors(maestro, ["bad json"])

    assert len(mgr.notify_calls) == 1
    assert any(a == "entity.parse_failure_capped" for (a, _t, _d) in mgr.audit_calls)


# ---------------------------------------------------------------------------
# _task_id_for — intra-module helper
# ---------------------------------------------------------------------------


def test_task_id_for_worker(dispatcher: MessageDispatcher, mgr: StubManager) -> None:
    """_task_id_for returns a worker's bound task_id."""
    mgr._entities["dev.backend.w1"] = Worker(
        name="dev.backend.w1", lead_name="dev.backend", task_id=9
    )
    assert dispatcher._task_id_for("dev.backend.w1") == 9


def test_task_id_for_non_worker(dispatcher: MessageDispatcher, mgr: StubManager) -> None:
    """_task_id_for returns None for a non-worker (or missing) entity."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    assert dispatcher._task_id_for("dev") is None
    assert dispatcher._task_id_for("nobody") is None


# ---------------------------------------------------------------------------
# Turn-end inbox check (Ticket 023, design D4)
#
# Wake-on-inbound is single-shot: a wake landing while the recipient is
# mid-turn is swallowed and nothing retries — queued mail parks until the
# 120m scheduler tick. New invariant: when a turn completes and the queue
# is non-empty, schedule a wake through the EXISTING budget machinery.
# ---------------------------------------------------------------------------


async def test_mail_arriving_mid_turn_wakes_once_at_completion(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """Mail queued while the entity is mid-turn → exactly one wake at turn end.

    The drain phase runs at turn START, so this mail (delivered inside
    ``send_turn``) is exactly the mail the just-completed turn could not
    have seen — the case the single-shot wake hole strands for 120m.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    async def deliver_mid_turn() -> None:
        await mgr.router.route("dev.backend", "dev", "ping from mid-turn")

    mgr.adapter = FakeTurnAdapter(on_turn=deliver_mid_turn)

    with _hermetic_send_flags():
        await dispatcher.send_to_entity("dev", "go")

    await _drain_wakes(mgr)
    wakes = [s for s in mgr.sent if s == ("dev", _WAKE_ON_INBOUND_TEXT)]
    assert len(wakes) == 1


async def test_turn_end_wake_uses_existing_budget_bookkeeping(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """The turn-end wake is accounted in the same budget as inbound wakes.

    Two observable consequences of reuse (not a fresh counter):
    1. the facade-owned ``_wake_budget`` deque gains exactly one
       timestamp, and the wake audits ``entity.wake_scheduled``;
    2. it draws down the SAME window inbound wakes use — with one slot
       left, the turn-end wake consumes it and the next inbound wake
       is throttled.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    async def deliver_mid_turn() -> None:
        await mgr.router.route("dev.backend", "dev", "ping from mid-turn")

    mgr.adapter = FakeTurnAdapter(on_turn=deliver_mid_turn)
    # Leave exactly one slot in the rolling window.
    now = datetime.now(UTC)
    mgr._wake_budget["dev"].extend([now] * (_WAKE_BUDGET_MAX_PER_WINDOW - 1))

    with _hermetic_send_flags():
        await dispatcher.send_to_entity("dev", "go")

    await _drain_wakes(mgr)
    # (1) one new timestamp in the shared deque + the scheduled audit.
    assert len(mgr._wake_budget["dev"]) == _WAKE_BUDGET_MAX_PER_WINDOW
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.wake_scheduled") == 1

    # (2) the window is now full — an ordinary inbound wake gets throttled.
    mgr.wake._on_inbound_wake("dev")
    await _drain_wakes(mgr)
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.wake_throttled") == 1
    assert actions.count("entity.wake_scheduled") == 1  # still just the one


async def test_empty_queue_at_completion_schedules_no_wake(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A turn that completes with an empty queue must not wake the entity.

    The check is for mail that arrived DURING the turn — a quiet turn
    must not burn wake budget or spawn a pointless follow-up session.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    mgr.adapter = FakeTurnAdapter()

    with _hermetic_send_flags():
        await dispatcher.send_to_entity("dev", "go")

    await _drain_wakes(mgr)
    assert mgr.sent == []
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert "entity.wake_scheduled" not in actions
    # No budget consumed either — the check is free when there's no mail.
    assert len(mgr._wake_budget["dev"]) == 0


async def test_exhausted_wake_budget_throttles_turn_end_wake(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """Budget exhausted → the turn-end wake is throttled: no send, no spin.

    Locks in the reuse contract: the check goes through the SAME
    per-recipient budget as inbound wakes, so an exhausted window drops
    the wake with a throttle audit. The mail stays queued for the 120m
    scheduler tick (the backstop) — and the turn itself completes
    normally, nothing crashes.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    async def deliver_mid_turn() -> None:
        await mgr.router.route("dev.backend", "dev", "ping from mid-turn")

    mgr.adapter = FakeTurnAdapter(response="all done", on_turn=deliver_mid_turn)
    now = datetime.now(UTC)
    mgr._wake_budget["dev"].extend([now] * _WAKE_BUDGET_MAX_PER_WINDOW)

    with _hermetic_send_flags():
        result = await dispatcher.send_to_entity("dev", "go")

    await _drain_wakes(mgr)
    assert result == "all done"  # the turn completed normally
    assert mgr.sent == []  # no wake send
    actions = [a for (a, _t, _d) in mgr.audit_calls]
    assert actions.count("entity.wake_throttled") == 1
    assert actions.count("entity.wake_scheduled") == 0
    # No spin: the mail is still parked for the scheduler tick to drain.
    assert mgr.router.has_pending("dev")


async def test_gate_resume_completion_also_runs_inbox_check(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A turn that parks at an interactive gate and resumes still wakes.

    On the PTY harness a gate blocks INSIDE ``send_turn`` until bridged
    (hold-and-inject, Ticket 003); the resumed turn returns through the
    same completion path. Simulated here by an adapter that parks on an
    event mid-turn: mail lands while parked, the 'gate' is approved, the
    turn completes — exactly one wake follows.
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")

    gate_parked = asyncio.Event()
    gate_approved = asyncio.Event()

    async def park_at_gate() -> None:
        gate_parked.set()
        await gate_approved.wait()  # blocked mid-turn, like a real gate

    mgr.adapter = FakeTurnAdapter(on_turn=park_at_gate)

    with _hermetic_send_flags():
        turn = asyncio.create_task(dispatcher.send_to_entity("dev", "go"))
        await gate_parked.wait()
        # Mail arrives while the turn is parked at the gate.
        await mgr.router.route("dev.backend", "dev", "ping during gate")
        assert mgr.sent == []  # nothing woke mid-gate
        gate_approved.set()  # the user approves; the turn resumes
        await turn

    await _drain_wakes(mgr)
    wakes = [s for s in mgr.sent if s == ("dev", _WAKE_ON_INBOUND_TEXT)]
    assert len(wakes) == 1


# ---------------------------------------------------------------------------
# Pending-gate guard (Ticket 028)
# ---------------------------------------------------------------------------


async def test_send_to_entity_skips_when_parked_at_gate(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A send to an entity parked at a gate must NOT reach the PTY.

    Typing a new-turn prompt into a PTY sitting on a TUI menu submits the
    highlighted default — the gate's "answer". So the chokepoint refuses to
    inject, returns a notice pointing at /approve, and leaves queued peer
    mail undrained (it re-delivers after the gate resolves).
    """
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    mgr.adapter = FakeTurnAdapter()
    mgr.gate_coordinator = FakeGateCoordinator(parked={"dev"})
    await mgr.router.route("dev.backend", "dev", "queued ping")

    with _hermetic_send_flags():
        result = await dispatcher.send_to_entity("dev", "facts poke")

    assert mgr.adapter.prompts == []  # PTY never reached
    assert "42" in result and "/approve" in result  # notice with request id
    assert mgr.router.has_pending("dev")  # peer mail NOT drained — survives


async def test_send_to_entity_proceeds_when_not_parked(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """No pending gate → the turn runs normally (regression guard)."""
    mgr._entities["dev"] = Maestro(name="dev", model="sonnet")
    mgr.adapter = FakeTurnAdapter(response="did the work")
    mgr.gate_coordinator = FakeGateCoordinator(parked=set())

    with _hermetic_send_flags():
        result = await dispatcher.send_to_entity("dev", "go")

    assert len(mgr.adapter.prompts) == 1  # the turn reached the PTY
    assert result == "did the work"
