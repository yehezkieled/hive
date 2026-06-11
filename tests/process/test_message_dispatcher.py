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
from datetime import UTC, datetime, timedelta

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

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRouter:
    """Records ``route`` calls; no real MessageStore needed."""

    def __init__(self) -> None:
        self.routed: list[tuple[str, str, str]] = []

    async def route(self, sender: str, recipient: str, content: str) -> None:
        self.routed.append((sender, recipient, content))

    def has_pending(self, entity_name: str) -> bool:
        return False


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


@pytest.fixture
def mgr() -> StubManager:
    return StubManager()


@pytest.fixture
def dispatcher(mgr: StubManager) -> MessageDispatcher:
    return MessageDispatcher(mgr)


async def _drain_kickoffs(mgr: StubManager) -> None:
    while mgr._kickoff_tasks:
        await asyncio.gather(*list(mgr._kickoff_tasks))


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
# spawn_worker + kickoff — _kickoff_tasks GC-tracking, detached kickoff
# ---------------------------------------------------------------------------


async def test_spawn_worker_schedules_detached_kickoff(
    dispatcher: MessageDispatcher, mgr: StubManager
) -> None:
    """A spawned worker is tracked in _last_spawned_workers and kicked off.

    The kickoff is detached: it's scheduled as a task added to the
    facade-owned ``_kickoff_tasks`` set with a self-discard callback, and
    only runs after ``_handle_actions`` returns.
    """
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    mgr._entities["dev.backend"] = lead

    actions = [Action(type="spawn_worker", worker_name="w1", task_id=3)]
    await dispatcher._handle_actions("dev.backend", "done", actions)

    assert mgr._last_spawned_workers == ["dev.backend.w1"]
    assert mgr._last_kickoffs == ["dev.backend.w1"]
    # The kickoff task is tracked on the facade-owned set while in flight.
    assert len(mgr._kickoff_tasks) == 1

    await _drain_kickoffs(mgr)
    # Self-discard callback released it; the kickoff actually ran.
    assert mgr._kickoff_tasks == set()
    assert mgr.kickoffs == ["dev.backend.w1"]


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
