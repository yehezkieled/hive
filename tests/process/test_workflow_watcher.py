"""Tests for the Workflow progress sweeper + ProgressStore (Ticket 017, slice 117).

The store holds the last-seen :class:`WorkflowProgress` per ``(entity, run_id)``
and emits **discrete** transitions (started / completed / failed / interrupted);
a plain count/phase tick emits nothing. The watcher is a tracked async loop that
sweeps ``manager._adapters`` and turns each transition into one ``_notify``.
"""

from __future__ import annotations

import asyncio

import pytest

from hive.process.workflow_watcher import ProgressStore, WorkflowWatcher
from hive.runtime.workflow_progress import WorkflowProgress


class _FakeAdapter:
    """Duck-types the bits the sweeper reads: scripted progress + busy/active."""

    def __init__(
        self, runs: list[WorkflowProgress], is_busy: bool = True, is_active: bool = True
    ) -> None:
        self.runs = runs
        self._busy = is_busy
        self._active = is_active

    def poll_workflow_progress(self) -> list[WorkflowProgress]:
        return self.runs

    def is_busy(self) -> bool:
        return self._busy

    def workflow_active(self, window: float) -> bool:
        return self._active


class _FakeManager:
    """Exposes only what the watcher touches: ``_adapters`` + async ``_notify``."""

    def __init__(self, adapters: dict[str, object]) -> None:
        self._adapters = adapters
        self.notifications: list[tuple[str, str, dict | None]] = []

    async def _notify(self, message: str, kind: str = "info", data: dict | None = None) -> None:
        self.notifications.append((message, kind, data))


def _run(run_id: str, status: str = "running", done: int = 0, agents: int = 3) -> WorkflowProgress:
    return WorkflowProgress(
        run_id=run_id,
        name="build",
        phase="fan-out",
        agent_count=agents,
        done_count=done,
        status=status,
        partials=None,
    )


def test_first_sighting_emits_started() -> None:
    store = ProgressStore()
    transitions = store.upsert("otter.team", [_run("wf_1")], is_busy=True)
    assert len(transitions) == 1
    assert transitions[0].kind == "started"
    assert transitions[0].run.run_id == "wf_1"


def test_running_to_completed_emits_completed_then_drops() -> None:
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running")], is_busy=True)
    transitions = store.upsert(
        "otter.team", [_run("wf_1", status="completed", done=3)], is_busy=True
    )
    assert [t.kind for t in transitions] == ["completed"]


def test_non_completed_terminal_emits_failed_then_drops() -> None:
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running")], is_busy=True)
    transitions = store.upsert("otter.team", [_run("wf_1", status="failed")], is_busy=True)
    assert [t.kind for t in transitions] == ["failed"]


def test_cancelled_terminal_also_emits_failed() -> None:
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running")], is_busy=True)
    transitions = store.upsert("otter.team", [_run("wf_1", status="cancelled")], is_busy=True)
    assert [t.kind for t in transitions] == ["failed"]


def test_orphan_running_but_not_busy_and_stale_emits_interrupted() -> None:
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running")], is_busy=True)
    # The owning Turn died: files still say running, adapter idle, files frozen.
    transitions = store.upsert(
        "otter.team", [_run("wf_1", status="running")], is_busy=False, is_active=False
    )
    assert [t.kind for t in transitions] == ["interrupted"]


def test_live_run_not_orphaned_on_is_busy_flicker() -> None:
    # is_busy can momentarily read False between turns; while the run's files
    # are still advancing (is_active) it must NOT be declared interrupted —
    # a false interrupt would also suppress the real completion.
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running")], is_busy=True)
    transitions = store.upsert(
        "otter.team", [_run("wf_1", status="running")], is_busy=False, is_active=True
    )
    assert transitions == []
    assert [r.run_id for r in store.runs_for("otter.team")] == ["wf_1"]


def test_terminal_run_not_re_emitted_when_file_lingers() -> None:
    # Claude Code does not delete wf_*.json when a run ends, so the completed
    # run reappears on the next sweep — it must NOT re-emit (the critical bug).
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running")], is_busy=True)  # started
    first = store.upsert("otter.team", [_run("wf_1", status="completed", done=3)], is_busy=True)
    assert [t.kind for t in first] == ["completed"]
    # Same completed file lingers on disk; sweep again → silence.
    again = store.upsert("otter.team", [_run("wf_1", status="completed", done=3)], is_busy=True)
    assert again == []


def test_preexisting_terminal_run_on_startup_is_silent() -> None:
    # On startup the store is empty; a stale completed run already on disk
    # (never witnessed running) must be recorded silently, not pinged.
    store = ProgressStore()
    transitions = store.upsert(
        "otter.team", [_run("old", status="completed", done=3)], is_busy=False
    )
    assert transitions == []
    # And it stays silent on the next sweep.
    assert (
        store.upsert("otter.team", [_run("old", status="completed", done=3)], is_busy=False) == []
    )


def test_runs_for_empty_unknown_entity() -> None:
    store = ProgressStore()
    assert store.runs_for("nobody") == []


def test_runs_for_excludes_terminal_dropped_runs() -> None:
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1"), _run("wf_2")], is_busy=True)
    # wf_1 completes (dropped); wf_2 still running (tracked).
    store.upsert(
        "otter.team",
        [_run("wf_1", status="completed"), _run("wf_2", status="running")],
        is_busy=True,
    )
    assert [r.run_id for r in store.runs_for("otter.team")] == ["wf_2"]


def test_plain_tick_emits_nothing_but_updates_store() -> None:
    store = ProgressStore()
    store.upsert("otter.team", [_run("wf_1", status="running", done=0)], is_busy=True)
    transitions = store.upsert("otter.team", [_run("wf_1", status="running", done=2)], is_busy=True)
    assert transitions == []
    # Store reflects the latest snapshot (done advanced to 2).
    runs = store.runs_for("otter.team")
    assert [(r.run_id, r.done_count) for r in runs] == [("wf_1", 2)]


@pytest.mark.asyncio
async def test_sweep_notifies_once_per_discrete_transition() -> None:
    adapter = _FakeAdapter([_run("wf_1", status="running")], is_busy=True)
    manager = _FakeManager({"otter.team": adapter})
    store = ProgressStore()
    watcher = WorkflowWatcher(manager, store)

    await watcher._sweep()  # first sighting → one "started"

    assert [n[1] for n in manager.notifications] == ["workflow_started"]


@pytest.mark.asyncio
async def test_sweep_emits_nothing_on_plain_tick() -> None:
    adapter = _FakeAdapter([_run("wf_1", status="running", done=0)], is_busy=True)
    manager = _FakeManager({"otter.team": adapter})
    store = ProgressStore()
    watcher = WorkflowWatcher(manager, store)

    await watcher._sweep()  # started
    manager.notifications.clear()
    adapter.runs = [_run("wf_1", status="running", done=1)]  # only count advanced
    await watcher._sweep()

    assert manager.notifications == []


@pytest.mark.asyncio
async def test_sweep_maps_completed_and_interrupted_to_notification_kinds() -> None:
    adapter = _FakeAdapter([_run("wf_1", status="running")], is_busy=True)
    manager = _FakeManager({"otter.team": adapter})
    store = ProgressStore()
    watcher = WorkflowWatcher(manager, store)

    await watcher._sweep()  # started
    manager.notifications.clear()
    adapter.runs = [_run("wf_1", status="completed")]
    await watcher._sweep()
    assert [n[1] for n in manager.notifications] == ["workflow_completed"]

    # An orphaned second run maps to workflow_failed.
    adapter.runs = [_run("wf_2", status="running")]
    adapter._busy = True
    adapter._active = True
    await watcher._sweep()  # wf_2 started
    manager.notifications.clear()
    adapter._busy = False  # Turn died...
    adapter._active = False  # ...and files frozen at running
    await watcher._sweep()
    assert [n[1] for n in manager.notifications] == ["workflow_failed"]


@pytest.mark.asyncio
async def test_sweep_skips_adapters_without_poll_method() -> None:
    class _Bare:
        pass

    manager = _FakeManager({"plain": _Bare()})
    watcher = WorkflowWatcher(manager, ProgressStore())
    await watcher._sweep()  # must not raise
    assert manager.notifications == []


def test_process_manager_has_progress_store_attr_default_none() -> None:
    from hive.bus.router import MessageRouter
    from hive.process.manager import ProcessManager

    manager = ProcessManager(router=MessageRouter(store=None))
    # Default None; slice 119 reads it as process_manager.progress_store.
    assert manager.progress_store is None
    # Assignable to a ProgressStore.
    store = ProgressStore()
    manager.progress_store = store
    assert manager.progress_store is store


@pytest.mark.asyncio
async def test_start_then_stop_cancels_cleanly() -> None:
    adapter = _FakeAdapter([_run("wf_1", status="running")], is_busy=True)
    manager = _FakeManager({"otter.team": adapter})
    watcher = WorkflowWatcher(manager, ProgressStore(), interval=0.01)

    await watcher.start()
    # Let the loop run at least one tick.
    for _ in range(50):
        if manager.notifications:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()  # no CancelledError / warning leaks

    assert any(n[1] == "workflow_started" for n in manager.notifications)
    # Idempotent stop.
    await watcher.stop()
