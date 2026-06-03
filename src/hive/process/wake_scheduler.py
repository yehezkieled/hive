"""Wake scheduler — auto-wake on inbound peer messages and spawn-kickoff
flows lifted out of ProcessManager.

Collaborator object (Ticket 004): holds a back-reference to the owning
ProcessManager (``self._mgr``) and reaches all shared state and sibling
methods through it. It imports nothing from ``manager.py`` at runtime; the
manager type hint is under ``TYPE_CHECKING`` only.

The GC-tracking ``_wake_tasks`` set and the ``_wake_budget`` rate-limit
deque stay facade-owned; this collaborator mutates them via ``self._mgr``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)

_SPAWN_KICKOFF_TEXT = (
    "You've been spawned. Your contract is your system prompt — "
    "read it, plan, and begin executing. Spawn workers if the work "
    "warrants subdivision; report back when validation passes or "
    "you hit a blocker."
)

_WAKE_ON_INBOUND_TEXT = (
    "Auto-wake: you have new messages in your inbox. Read them, "
    "decide what (if anything) to do, and respond accordingly."
)

# Bounded wake rate per recipient — guards against runaway A↔B
# ping-pong. The drain phase prepends every queued message into the
# next session's prompt, so throttled wakes never lose data: the
# message stays in the queue and is read by the next wake or the
# 120m PriorityScheduler tick.
_WAKE_BUDGET_WINDOW_SECONDS = 60
_WAKE_BUDGET_MAX_PER_WINDOW = 6


class WakeScheduler:
    """Auto-wake-on-inbound and spawn-kickoff flows.

    One responsibility cluster lifted out of ProcessManager. All shared
    state lives on the facade and is reached via ``self._mgr``.
    """

    def __init__(self, mgr: ProcessManager) -> None:
        self._mgr = mgr

    async def _auto_kickoff(self, target: str) -> None:
        """Wake a freshly spawned lead/worker by sending the generic kickoff prompt.

        Runs as a detached task after ``_handle_actions`` returns so the
        parent dispatch's ``_last_*`` tracking isn't reset by the recursive
        send. Failures are logged + audited but never propagate.
        """
        try:
            await self._mgr.send_to_entity(target, _SPAWN_KICKOFF_TEXT)
        except Exception as exc:
            logger.warning("auto-kickoff for %s failed: %s", target, exc)
            try:
                await self._mgr._audit(
                    "entity.kickoff_failed",
                    target=target,
                    details={"reason": str(exc)},
                    actor="system",
                )
            except Exception:
                logger.exception("audit of kickoff_failed for %s also failed", target)

    def enable_wake_on_inbound(self) -> None:
        """Wire the router so peer messages auto-spawn a session for the recipient.

        Called once at startup from ``__main__``. Without this, queued
        messages sit idle until the 120m ``PriorityScheduler`` tick or
        a user poke. Opt-in (rather than wired in ``__init__``) so unit
        tests that seed queue state via ``router.route`` aren't
        disturbed by the auto-spawn.
        """
        self._mgr.router.wake_callback = self._on_inbound_wake

    def _on_inbound_wake(self, recipient: str) -> None:
        """Sync hook called by the router after a message lands in a queue.

        Schedules ``_wake_entity`` as a detached task so peer messages
        auto-spawn a session for the recipient. Skips unregistered
        recipients (e.g. ``user``) and applies a per-recipient rolling
        rate limit so a chatty A↔B pair can't burn through the API
        budget. Throttled wakes don't lose data: queued messages are
        still drained on the next wake or the 120m scheduler tick.
        """
        if recipient not in self._mgr._entities:
            return

        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=_WAKE_BUDGET_WINDOW_SECONDS)
        budget = self._mgr._wake_budget[recipient]
        while budget and budget[0] < cutoff:
            budget.popleft()

        if len(budget) >= _WAKE_BUDGET_MAX_PER_WINDOW:
            logger.warning(
                "wake-on-inbound throttled for %s: %d/%ds budget exhausted",
                recipient,
                _WAKE_BUDGET_MAX_PER_WINDOW,
                _WAKE_BUDGET_WINDOW_SECONDS,
            )
            audit_task = asyncio.create_task(
                self._mgr._audit(
                    "entity.wake_throttled",
                    target=recipient,
                    details={
                        "window_seconds": _WAKE_BUDGET_WINDOW_SECONDS,
                        "max_per_window": _WAKE_BUDGET_MAX_PER_WINDOW,
                    },
                    actor="system",
                )
            )
            self._mgr._wake_tasks.add(audit_task)
            audit_task.add_done_callback(self._mgr._wake_tasks.discard)
            return

        budget.append(now)
        audit_task = asyncio.create_task(
            self._mgr._audit(
                "entity.wake_scheduled",
                target=recipient,
                actor="system",
            )
        )
        self._mgr._wake_tasks.add(audit_task)
        audit_task.add_done_callback(self._mgr._wake_tasks.discard)
        task = asyncio.create_task(self._wake_entity(recipient))
        self._mgr._wake_tasks.add(task)
        task.add_done_callback(self._mgr._wake_tasks.discard)

    async def _wake_entity(self, recipient: str) -> None:
        """Spawn a session for ``recipient`` to drain queued messages.

        The drain phase in ``send_to_entity`` already prepends every
        queued message into the prompt — this method just nudges the
        model with a one-line wake notice. ``RuntimeError`` from the
        concurrency guard ("already running") is swallowed silently:
        the message stays in the queue and the entity will see it when
        its current session ends and the next wake or scheduler tick
        fires. All other failures are logged + audited.
        """
        try:
            await self._mgr.send_to_entity(recipient, _WAKE_ON_INBOUND_TEXT)
        except RuntimeError as exc:
            if "already running" in str(exc).lower():
                return
            logger.warning("wake-on-inbound for %s failed: %s", recipient, exc)
            try:
                await self._mgr._audit(
                    "entity.wake_failed",
                    target=recipient,
                    details={"reason": str(exc)},
                    actor="system",
                )
            except Exception:
                logger.exception("audit of wake_failed for %s also failed", recipient)
        except Exception as exc:
            logger.warning("wake-on-inbound for %s failed: %s", recipient, exc)
            try:
                await self._mgr._audit(
                    "entity.wake_failed",
                    target=recipient,
                    details={"reason": str(exc)},
                    actor="system",
                )
            except Exception:
                logger.exception("audit of wake_failed for %s also failed", recipient)
