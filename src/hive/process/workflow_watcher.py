"""Workflow progress sweeper + in-memory store (Ticket 017, slice 117).

A single global sweeper (one tracked task, ~2s tick) iterates the registered
adapters, polls each for its in-flight Workflow runs, and feeds them to a
:class:`ProgressStore`. The store does the change-detection: it returns the
**discrete** transitions a tick produced — ``started`` / ``completed`` /
``failed`` / ``interrupted`` — and nothing for a plain count/phase tick. The
watcher turns each transition into exactly one notification; ticks never reach
the notification pipe (design.md fork C, ADR 0014).

The store's :meth:`ProgressStore.runs_for` is a **locked contract** consumed by
the dashboard view-model (slice 119): the non-terminal runs tracked for an
entity.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from hive.runtime.workflow_progress import WorkflowProgress

logger = logging.getLogger(__name__)

# An orphan is declared only when a run's files have not advanced within this
# many seconds AND the owning Turn is gone — long enough that a brief gap in a
# live run's journal writes never reads as dead.
_ORPHAN_STALE_WINDOW_S = 15.0

# Store-transition kind -> notification kind. ``failed`` and ``interrupted``
# both surface as ``workflow_failed`` (an orphan is an honest failure).
_NOTIFY_KIND = {
    "started": "workflow_started",
    "completed": "workflow_completed",
    "failed": "workflow_failed",
    "interrupted": "workflow_failed",
}


class _Manager(Protocol):
    _adapters: dict[str, object]

    async def _notify(self, message: str, kind: str = ..., data: dict | None = ...) -> None: ...


@dataclass(frozen=True)
class Transition:
    """One notification-worthy change in a run's lifecycle.

    ``kind`` is the store-level transition name (``started`` / ``completed`` /
    ``failed`` / ``interrupted``); ``entity`` is the owning Entity's name and
    ``run`` the snapshot that triggered it. The watcher maps ``kind`` to a
    notification kind (failed and interrupted both map to ``workflow_failed``).
    """

    kind: str
    entity: str
    run: WorkflowProgress


class ProgressStore:
    """Last-seen Workflow runs per entity + discrete change detection.

    Two pieces of state. ``_runs`` holds the runs we are currently watching as
    *running* (so ``runs_for`` can render them and we can detect their terminal
    transition). ``_terminal`` remembers every ``(entity, run_id)`` whose
    terminal event we have already emitted (or silently recorded) — because
    Claude Code does **not** delete ``wf_*.json`` when a run ends, so a finished
    run lingers on disk and would otherwise re-fire its notification on every
    ~2s sweep. ``_terminal`` makes each transition fire exactly once.
    """

    def __init__(self) -> None:
        # (entity, run_id) -> last-seen WorkflowProgress, *running* only.
        self._runs: dict[tuple[str, str], WorkflowProgress] = {}
        # (entity, run_id) whose terminal event was already handled — never re-emit.
        self._terminal: set[tuple[str, str]] = set()

    def upsert(
        self,
        entity_name: str,
        runs: list[WorkflowProgress],
        is_busy: bool,
        is_active: bool = True,
    ) -> list[Transition]:
        """Reconcile ``runs`` for ``entity_name`` and return the discrete
        transitions this tick produced.

        ``is_busy`` is the adapter's turn-in-flight flag; ``is_active`` is
        whether the run's files advanced recently (the no-hang liveness signal).
        An orphan needs **both** off — the Turn is gone *and* the files are
        frozen — so a transient ``is_busy`` dip on a live run does not
        false-fire ``interrupted`` (which would also poison the run into
        ``_terminal`` and suppress its real ``completed``).
        """
        transitions: list[Transition] = []
        for run in runs:
            key = (entity_name, run.run_id)
            if key in self._terminal:
                continue  # already handled; ignore the lingering on-disk file.
            previously_seen = key in self._runs
            orphaned = run.status == "running" and not is_busy and not is_active
            transition = self._classify(run, orphaned, previously_seen)
            if transition is not None:
                transitions.append(Transition(kind=transition, entity=entity_name, run=run))
            if run.status != "running" or orphaned:
                # Terminal (or orphaned-running) — stop tracking, never re-handle.
                self._runs.pop(key, None)
                self._terminal.add(key)
            else:
                self._runs[key] = run
        return transitions

    def runs_for(self, entity_name: str) -> list[WorkflowProgress]:
        """The running runs currently tracked for ``entity_name`` (``[]`` if
        none). **Locked contract** consumed by the dashboard view-model
        (slice 119) as ``process_manager.progress_store.runs_for(name)``."""
        return [run for (name, _run_id), run in self._runs.items() if name == entity_name]

    def _classify(
        self,
        run: WorkflowProgress,
        orphaned: bool,
        previously_seen: bool,
    ) -> str | None:
        if run.status == "running" and not orphaned:
            # Genuinely running: announce the first sighting, then stay quiet.
            return "started" if not previously_seen else None
        # Terminal (completed / failed / cancelled) or orphaned-running. Only
        # announce it for a run we actually watched running — a run that appears
        # already-terminal (e.g. a stale wf_*.json from before startup) is
        # recorded silently so it never pings.
        if not previously_seen:
            return None
        if run.status == "completed":
            return "completed"
        if run.status == "running":
            return "interrupted"  # orphan: Turn gone and files frozen.
        return "failed"


def _notification_text(kind: str, entity: str, run: WorkflowProgress) -> str:
    """One human-readable summary line per discrete transition. Carries no
    partials — only the start/done/fail summary (design.md fork C)."""
    progress = f"{run.done_count}/{run.agent_count}"
    label = run.name or run.run_id
    if kind == "started":
        return f"Workflow '{label}' started on {entity} ({run.agent_count} agents)."
    if kind == "completed":
        return f"Workflow '{label}' completed on {entity} ({progress})."
    if kind == "interrupted":
        return f"Workflow '{label}' interrupted on {entity} ({progress}) — Lead turn ended."
    return f"Workflow '{label}' failed on {entity} ({progress}, status={run.status})."


class WorkflowWatcher:
    """Global sweeper: every ``interval`` s, poll each adapter for Workflow
    runs, reconcile them into the store, and emit one notification per discrete
    transition. A plain tick emits nothing. Runs as one tracked task."""

    def __init__(self, manager: _Manager, store: ProgressStore, interval: float = 2.0) -> None:
        self._manager = manager
        self._store = store
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the sweeper loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="workflow-watcher-loop")

    async def stop(self) -> None:
        """Cancel the sweeper loop cleanly. Idempotent — no warning on shutdown."""
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            try:
                await self._sweep()
            except Exception:  # defense-in-depth — one bad tick must not kill the loop
                logger.exception("WorkflowWatcher sweep errored")
            await asyncio.sleep(self._interval)

    async def _sweep(self) -> None:
        """One pass over the registered adapters. Duck-types
        ``poll_workflow_progress`` so non-CC adapters are simply skipped."""
        for name, adapter in list(self._manager._adapters.items()):
            if not (
                hasattr(adapter, "poll_workflow_progress")
                and hasattr(adapter, "is_busy")
                and hasattr(adapter, "workflow_active")
            ):
                continue
            try:
                runs = adapter.poll_workflow_progress()
                is_busy = adapter.is_busy()
                is_active = adapter.workflow_active(_ORPHAN_STALE_WINDOW_S)
            except Exception:
                logger.debug("poll_workflow_progress failed for %s", name, exc_info=True)
                continue
            for transition in self._store.upsert(name, runs, is_busy, is_active):
                kind = _NOTIFY_KIND.get(transition.kind, "workflow_failed")
                text = _notification_text(transition.kind, name, transition.run)
                data = {
                    "entity": name,
                    "run_id": transition.run.run_id,
                    "name": transition.run.name,
                    "status": transition.run.status,
                    "done_count": transition.run.done_count,
                    "agent_count": transition.run.agent_count,
                }
                await self._manager._notify(text, kind, data)
