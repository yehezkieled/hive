"""PriorityScheduler — pokes maestros with facts prompts so they self-allocate.

The orchestrator never spawns or kills entities autonomously on its own.
Instead, every ``HIVE_PRIORITY_EVAL_INTERVAL_MINUTES`` the scheduler builds
a "facts" prompt for each alive maestro (free slots, pending tasks by
priority, org snapshot, 24h token cost, idle-time per entity) and sends
it via ``ProcessManager.send_to_entity``. The maestro decides whether to
emit ``spawn_team`` / ``kill_entity`` ``<hive_actions>`` or do nothing —
the orchestrator is a dumb facts pipe.

The scheduler also tracks per-maestro autonomous-spawn counts within the
current eval window. The dispatch site in :class:`ProcessManager` consults
``can_autospawn(actor)`` before executing a spawn action and calls
``record_autospawn(actor)`` on success. Counters reset each tick.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from hive.models.maestro import Maestro
from hive.models.task import TaskStatus

if TYPE_CHECKING:
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


def maestro_for_actor(actor: str) -> str:
    """Return the root maestro name for any dotted entity name.

    ``dev.backend`` → ``dev``; ``dev`` → ``dev``. Used to attribute
    autonomous spawns to the maestro at the top of the org tree, so a
    chatty lead doesn't bypass the rate limit by spawning teams under
    multiple maestros.
    """
    return actor.split(".", 1)[0]


class PriorityScheduler:
    """Background scheduler — facts prompts + autonomous-spawn rate limit."""

    def __init__(
        self,
        process_manager: ProcessManager,
        task_store: TaskStore | None = None,
        token_store: TokenStore | None = None,
        eval_interval_minutes: int = 120,
        spawn_limit: int = 3,
        decision_nudge_minutes: int = 60,
    ) -> None:
        self.process_manager = process_manager
        self.task_store = task_store
        self.token_store = token_store
        self.eval_interval = timedelta(minutes=eval_interval_minutes)
        self.spawn_limit = spawn_limit
        # #144: how long a parked maestro may sit before the user is re-pinged
        # about the pending decision. The maestro itself is never poked.
        self.decision_nudge = timedelta(minutes=decision_nudge_minutes)
        self._spawn_counts: dict[str, int] = defaultdict(int)
        self._window_started_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Rate limit
    # ------------------------------------------------------------------

    def reset_window(self) -> None:
        """Clear per-maestro spawn counters and mark a fresh window."""
        self._spawn_counts.clear()
        self._window_started_at = datetime.now(UTC)

    def can_autospawn(self, actor: str) -> bool:
        """Return True if ``actor``'s parent maestro is under the spawn limit."""
        return self._spawn_counts[maestro_for_actor(actor)] < self.spawn_limit

    def record_autospawn(self, actor: str) -> None:
        """Increment the spawn counter for ``actor``'s parent maestro."""
        self._spawn_counts[maestro_for_actor(actor)] += 1

    def refund_autospawn(self, actor: str) -> None:
        """Decrement the spawn counter for ``actor``'s parent maestro.

        Called on kill so a maestro can recover spawn budget within the
        same window after cleaning up an entity. Floors at zero — refunding
        a fresh actor is a no-op rather than going negative.
        """
        maestro = maestro_for_actor(actor)
        if self._spawn_counts[maestro] > 0:
            self._spawn_counts[maestro] -= 1

    def spawn_count(self, actor: str) -> int:
        return self._spawn_counts.get(maestro_for_actor(actor), 0)

    # ------------------------------------------------------------------
    # Facts prompt
    # ------------------------------------------------------------------

    async def build_facts_prompt(self, maestro_name: str) -> str:
        """Build the facts prompt for one maestro.

        The prompt is plain text — sections separated by blank lines so the
        LLM can pick out structure without us forcing JSON. Numbers come
        from ``ProcessManager.active_count`` / ``max_sessions`` (capacity),
        ``TaskStore.list`` (pending), and ``TokenStore.totals`` (cost).
        Any store may be None in tests; the prompt degrades gracefully.
        """
        pm = self.process_manager
        now = datetime.now(UTC)

        free_slots = pm.max_sessions - pm.active_count
        own_org = [
            (name, e) for name, e in pm.entities.items() if maestro_for_actor(name) == maestro_name
        ]
        org_size = len(own_org)
        used_budget = self._spawn_counts.get(maestro_name, 0)
        budget_left = max(0, self.spawn_limit - used_budget)

        # Pending tasks by priority. Limit the per-priority preview so
        # the prompt stays readable even with a long backlog.
        pending_by_priority: dict[int, list[str]] = defaultdict(list)
        if self.task_store is not None:
            try:
                pending = await self.task_store.list(status=TaskStatus.PENDING, limit=50)
                for t in pending:
                    pending_by_priority[t.priority].append(f"#{t.id} {t.title}")
            except Exception:
                logger.exception("scheduler: task list failed for %s", maestro_name)

        # Org snapshot — only entities under this maestro.
        org_lines: list[str] = []
        for name, e in sorted(own_org):
            idle_hint = ""
            if e.last_activity_at is not None:
                idle_minutes = int((now - e.last_activity_at).total_seconds() / 60)
                idle_hint = f", idle {idle_minutes}m"
            org_lines.append(
                f"  {name} [{e.role}] {e.state.value} p{e.current_priority}{idle_hint}"
            )

        # Per-entity 24h token cost — the maestro uses this to spot
        # entities that are burning budget without producing.
        cost_lines: list[str] = []
        if self.token_store is not None:
            since = now - timedelta(hours=24)
            for name, _ in own_org:
                try:
                    totals = await self.token_store.totals(since=since, entity_name=name)
                except Exception:
                    logger.exception("scheduler: token totals failed for %s", name)
                    continue
                calls = int(totals.get("call_count", 0))
                if calls == 0:
                    continue
                cost = float(totals.get("cost_usd", 0) or 0)
                cost_lines.append(f"  {name}: ${cost:.4f} over {calls} call(s)")

        interval_min = int(self.eval_interval.total_seconds() / 60)
        lines = [
            f"=== Hive scheduler eval (every {interval_min}m) ===",
            f"You are {maestro_name}, CEO of your org. Decide allocation based on these facts.",
            "",
            f"Capacity: {free_slots}/{pm.max_sessions} slots free",
            f"Org size: {org_size} entities under {maestro_name}",
            f"Spawn budget this window: {budget_left}/{self.spawn_limit} remaining",
            "",
            "Pending tasks by priority (lower = more urgent):",
        ]
        if pending_by_priority:
            for prio in sorted(pending_by_priority):
                titles = pending_by_priority[prio]
                lines.append(f"  P{prio} ({len(titles)} task(s)):")
                for t in titles[:5]:
                    lines.append(f"    {t}")
                if len(titles) > 5:
                    lines.append(f"    ... and {len(titles) - 5} more")
        else:
            lines.append("  (none)")

        lines.extend(["", "Org snapshot:"])
        if org_lines:
            lines.extend(org_lines)
        else:
            lines.append("  (just you)")

        lines.extend(["", "24h token cost (per entity):"])
        if cost_lines:
            lines.extend(cost_lines)
        else:
            lines.append("  (no calls in last 24h)")

        lines.extend(
            [
                "",
                "Decide: if the org needs a change, emit a <hive_actions> block with",
                "spawn_team / kill_entity. If it is appropriately sized for the workload,",
                "do NOT emit a <hive_actions> block at all — just reply in plain prose",
                "(e.g. 'no action needed'). A block whose body is not valid action JSON",
                "is rejected as malformed and re-pokes you, so never wrap prose in one.",
                "Brief reasoning in your response is logged in the audit trail.",
            ]
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tick / loop
    # ------------------------------------------------------------------

    async def _maybe_nudge_decision(self, maestro) -> None:  # noqa: ANN001
        """Re-ping the user about a maestro's pending decision (#144).

        Called only for an ``awaiting_decision`` maestro. Fires a reminder when
        a full ``decision_nudge`` interval has elapsed since the last nudge, then
        resets the clock. A restored maestro (flag persisted, in-memory clock
        lost on restart) has ``last_nudged_at is None`` — we arm a fresh baseline
        without nudging, so a restart never triggers a reminder storm. Never
        pokes the maestro itself — only the human is reminded.
        """
        now = datetime.now(UTC)
        if maestro.last_nudged_at is None:
            maestro.last_nudged_at = now  # arm baseline (post-restart), no nudge
            return
        if now - maestro.last_nudged_at < self.decision_nudge:
            return
        await self.process_manager._notify(
            f"⏰ {maestro.name} is still waiting on your decision — reply to it to continue.",
            kind="decision_reminder",
            data={"entity": maestro.name},
        )
        maestro.last_nudged_at = now
        logger.info("scheduler: nudged user about %s's pending decision", maestro.name)

    async def run_once(self) -> list[str]:
        """One scheduler tick — reset window, eval each alive maestro.

        Returns the list of maestro names that were poked (useful for
        tests and logging). Failures on individual maestros are swallowed
        so one broken entity doesn't block the rest.
        """
        self.reset_window()
        pm = self.process_manager
        maestros = [e for e in pm.entities.values() if isinstance(e, Maestro)]
        poked: list[str] = []
        for m in maestros:
            # Ticket 028: never poke a maestro parked at an interactive gate —
            # injecting into its PTY submits the gate's highlighted default (an
            # unauthorised decision). send_to_entity guards this too; skipping
            # here also avoids building the (DB-backed) facts prompt needlessly.
            if pm.is_parked_at_gate(m.name):
                logger.info("scheduler: skipping %s — parked at an interactive gate", m.name)
                continue
            # Ticket 029: a maestro waiting on a user decision must not be poked
            # — only the user's reply may advance it. Separate check from the
            # gate skip (awaiting_decision is a flag on a RUNNING/IDLE entity,
            # not a GATED state). #144: while skipping, re-ping the user once a
            # full nudge interval has passed so the question can't sit silently
            # forever — the maestro is still never poked.
            if m.awaiting_decision:
                await self._maybe_nudge_decision(m)
                logger.info("scheduler: skipping %s — awaiting a user decision", m.name)
                continue
            try:
                facts = await self.build_facts_prompt(m.name)
                await pm.send_to_entity(m.name, facts)
                poked.append(m.name)
                logger.info("scheduler: poked %s with facts prompt", m.name)
            except Exception:
                logger.exception("scheduler: tick failed for %s", m.name)
        return poked

    async def run_once_for(self, maestro_name: str) -> str:
        """Manual one-shot eval for a single maestro (used by /eval).

        Returns the facts prompt string so the caller can echo it back
        for transparency. Does not reset the spawn-counter window — that
        only happens on the periodic tick.
        """
        # Ticket 028: don't poke a maestro parked at an interactive gate.
        if self.process_manager.is_parked_at_gate(maestro_name):
            notice = (
                f"{maestro_name} is parked at an interactive gate; skipped (answer the gate first)."
            )
            logger.info("scheduler: %s", notice)
            return notice
        # Ticket 029: don't poke a maestro awaiting a user decision.
        awaiting = self.process_manager.entities.get(maestro_name)
        if awaiting is not None and awaiting.awaiting_decision:
            notice = f"{maestro_name} is awaiting a user decision; skipped (reply to it first)."
            logger.info("scheduler: %s", notice)
            return notice
        facts = await self.build_facts_prompt(maestro_name)
        await self.process_manager.send_to_entity(maestro_name, facts)
        return facts

    async def run(self, stop_event: asyncio.Event) -> None:
        """Main loop — sleep one interval, run_once, repeat until stop_event."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.eval_interval.total_seconds(),
                )
                break
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception:
                logger.exception("scheduler: run_once raised")
