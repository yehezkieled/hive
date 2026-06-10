"""Lifecycle manager — entity registration, spawn, kill, compact, and the
auto-personality file lifecycle lifted out of ProcessManager.

Collaborator object (Ticket 004): holds a back-reference to the owning
ProcessManager (``self._mgr``) and reaches all shared state and sibling
methods through it. It imports nothing from ``manager.py`` at module load;
the manager type hint is under ``TYPE_CHECKING`` only.

This is the lock-heavy slice. Every ``async with self._mgr._state_lock``
critical section in the orchestrator lives here. Each block is a verbatim
copy from the original ``ProcessManager`` — guarding only synchronous dict
mutations, never holding the lock across an ``await``. The single
non-reentrant ``asyncio.Lock`` stays facade-owned; this collaborator
acquires it through ``self._mgr``. ``kill_entity``'s pop block is
load-bearing — do not reorder or add awaits inside it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hive.mcp.config import mcp_servers_enabled
from hive.models.entity import (
    Entity,
    EntityState,
    is_auto_generated_personality,
    resolve_advisor,
)
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.worker import Worker
from hive.process.skill_curation import skill_denylist_for
from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


def _render_auto_personality(
    *,
    entity_name: str,
    role: str,
    model: str,
    display_name: str,
    personality: str,
) -> str:
    """Render the markdown body for an auto-generated personality file.

    Frontmatter ``auto_generated: true`` is the cleanup signal — only
    files with this flag are deleted on kill. User-authored files (no
    frontmatter) are always preserved.

    Maestros and leads default to read-only tools (Read, Grep, Glob)
    so the role boundary holds — they cannot drift into hands-on
    coding because Edit/Write/Bash aren't available. Workers and other
    roles inherit the platform default toolkit.

    The ``disallowedTools`` line blocks Claude Code's internal
    subagent tools (``Agent``/``Task``) and TodoWrite-family tools for
    coordinator roles. ``allowedTools`` alone is bypassed when the
    entity runs under ``--dangerously-skip-permissions`` (yolo mode);
    ``disallowedTools`` is still honored. Without this guard, a yolo
    lead spawns Claude Code's own subagents instead of Hive workers
    and the org never grows.
    """
    tools_section = ""
    if role in ("maestro", "lead"):
        tools_section = (
            "\n## Tools\n"
            "- allowedTools: Read Grep Glob\n"
            "- disallowedTools: Agent Task ExitPlanMode TodoWrite TaskCreate "
            "TaskUpdate TaskList TaskGet TaskOutput TaskStop\n"
        )
    knowledge_section = (
        "\n## Knowledge search\n"
        "You have a `search_knowledge(query, kind, limit)` MCP tool "
        "(via `hive-knowledge`).\n\n"
        "Call it when:\n"
        "- The auto-context above didn't include what you need\n"
        "- You're mid-task and realise different keywords might match better\n"
        "- You need more than the 1 result the auto-context gave you\n\n"
        "Tips:\n"
        "- Phrase the query like keywords, not a sentence "
        '("rate limit handling" not "how do I handle rate limits?")\n'
        '- `kind="blueprints"` for design notes; `kind="attachments"` for '
        'uploaded files; `kind="both"` if unsure\n'
        "- Distances < 0.3 are usually solid matches; > 0.6 is noise\n"
    )
    return (
        "---\n"
        "auto_generated: true\n"
        "---\n"
        f"# Entity: {display_name}\n\n"
        "## Identity\n"
        f"- **Name**: {entity_name}\n"
        f"- **Role**: {role}\n"
        f"- **Model**: {model}\n\n"
        "## System Prompt\n"
        f"You are {display_name}.\n\n"
        f"{personality}\n"
        f"{tools_section}"
        f"{knowledge_section}"
    )


def _adapter_config_from_entity(entity: Entity) -> ClaudeAdapterConfig:
    """Map an Entity to the ClaudeAdapterConfig needed by ClaudeAdapter."""
    # Merge the per-role skill denylist (Ticket 012) into the entity's own
    # disallowed tools, preserving existing tokens (e.g. Agent/Task for
    # maestro/lead) and de-duplicating while keeping first-seen order.
    disallowed_tools = list(
        dict.fromkeys(list(entity.disallowed_tools) + skill_denylist_for(entity.role))
    )
    return ClaudeAdapterConfig(
        model=entity.model,
        system_prompt=entity.system_prompt,
        allowed_tools=list(entity.allowed_tools),
        disallowed_tools=disallowed_tools,
        permission_mode=entity.permission_mode,
        loop_mode=entity.loop_mode,
        role=entity.role,
        name=entity.name,
        mcp_config_path=Path(entity.mcp_config_path) if mcp_servers_enabled() else None,
        advisor=resolve_advisor(entity.model, entity.advisor, entity.role),
    )


class LifecycleManager:
    """Entity registration, spawn, kill, compact, and personality lifecycle.

    One responsibility cluster lifted out of ProcessManager. All shared
    state lives on the facade and is reached via ``self._mgr``.
    """

    def __init__(self, mgr: ProcessManager) -> None:
        self._mgr = mgr

    def _personality_path(self, entity_name: str) -> Path:
        return self._mgr.personalities_dir / f"{entity_name}.md"

    def _maybe_write_auto_personality(
        self,
        *,
        entity_name: str,
        role: str,
        model: str,
        display_name: str | None,
        personality: str | None,
    ) -> Path | None:
        """Write an auto-generated personality file when both fields present.

        Pair-or-nothing: missing either field skips the write entirely.
        Existing files are never overwritten — user-authored files are
        protected, and re-spawning under the same name is a no-op.

        Returns the path that was written, or ``None`` if no file was
        created (pair incomplete, file already existed, or write failed).
        """
        if not display_name or not personality:
            return None
        path = self._personality_path(entity_name)
        if path.exists():
            logger.info("Skipping auto personality write — file exists at %s", path)
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _render_auto_personality(
                    entity_name=entity_name,
                    role=role,
                    model=model,
                    display_name=display_name,
                    personality=personality,
                )
            )
            logger.info("Wrote auto personality file: %s", path)
            return path
        except OSError:
            logger.exception("Failed to write personality file %s", path)
            return None

    def _maybe_delete_auto_personality(self, entity_name: str) -> None:
        """Delete the personality file if it exists and is auto-generated."""
        path = self._personality_path(entity_name)
        if not is_auto_generated_personality(path):
            return
        try:
            path.unlink()
            logger.info("Deleted auto personality file: %s", path)
        except OSError:
            logger.exception("Failed to delete personality file %s", path)

    async def register_maestro(
        self,
        name: str,
        model: str = "opus",
        personality_path: Path | None = None,
    ) -> Maestro:
        """Create and register a new maestro entity.

        Does not spawn a subprocess — the maestro stays IDLE until it
        receives its first message via send_to_entity.
        """
        if name in self._mgr._entities:
            raise ValueError(f"Entity {name!r} already exists.")

        maestro = Maestro(
            name=name,
            model=model,
            personality_path=personality_path,
        )
        # New maestros default to `yolo` so first-message tool calls
        # don't get auto-denied under headless `claude -p`. Existing
        # maestros restored from postgres keep their persisted mode.
        maestro.permission_mode = "yolo"
        if personality_path and personality_path.exists():
            maestro.load_personality()

        async with self._mgr._state_lock:
            self._mgr._entities[name] = maestro
        self._mgr.router.register(name)
        await self._mgr._persist(maestro)
        await self._mgr._audit(
            "entity.register",
            target=name,
            details={"role": "maestro", "model": model},
        )
        logger.info("Registered maestro: %s (model=%s)", name, model)
        return maestro

    async def register_entity(self, entity: Entity) -> None:
        """Register a pre-built entity in IDLE state without spawning a subprocess.

        Useful for tests and for restoring entities that were constructed
        externally. The entity must not already be registered.
        """
        if entity.name in self._mgr._entities:
            raise ValueError(f"Entity {entity.name!r} already exists.")
        async with self._mgr._state_lock:
            self._mgr._entities[entity.name] = entity
        self._mgr.router.register(entity.name)
        logger.info("Registered entity: %s (role=%s)", entity.name, entity.role)

    async def _get_or_create_adapter(self, entity: Entity) -> ClaudeAdapter:
        """Return a live PTY adapter for entity, creating one if needed.

        Adapters are cached per entity so the same persistent PTY process
        handles all of that entity's turns.
        """
        existing = self._mgr._adapters.get(entity.name)
        if existing is not None and existing.is_alive():
            return existing

        cwd = (
            Path(entity.worktree_path)
            if isinstance(entity, Worker) and entity.worktree_path
            else None
        )
        config = _adapter_config_from_entity(entity)
        adapter = ClaudeAdapter(
            config,
            cwd=cwd,
            gate_coordinator=self._mgr.gate_coordinator,
            entity_name=entity.name,
            on_gate_state=self._mgr._on_gate_state,
        )
        await adapter.start()
        async with self._mgr._state_lock:
            self._mgr._adapters[entity.name] = adapter
        return adapter

    async def create_team(
        self,
        maestro_name: str,
        team_name: str,
        model: str = "sonnet",
        display_name: str | None = None,
        personality: str | None = None,
    ) -> TeamLead:
        """Create a new team under a maestro.

        Registers a TeamLead entity named ``maestro.team``. The lead is
        not spawned as a subprocess — it stays IDLE until someone sends
        it a message via send_to_entity.

        If both ``display_name`` and ``personality`` are provided and no
        file exists at the target path, an auto-generated personality
        file is written. Pair-or-nothing: either both fields or neither.
        """
        entity = self._mgr._entities.get(maestro_name)
        if entity is None:
            raise KeyError(f"Maestro {maestro_name!r} not found.")
        if not isinstance(entity, Maestro):
            raise TypeError(f"Entity {maestro_name!r} is not a maestro.")

        # Delegate to Maestro.create_team (raises ValueError on duplicate)
        team = entity.create_team(team_name)

        lead_name = f"{maestro_name}.{team_name}"
        lead = TeamLead(
            name=lead_name,
            team_name=team_name,
            maestro_name=maestro_name,
            model=model,
            permission_mode=entity.permission_mode,
        )
        team.lead = lead_name

        async with self._mgr._state_lock:
            self._mgr._entities[lead_name] = lead
        self._mgr.router.register(lead_name)
        written_path = self._maybe_write_auto_personality(
            entity_name=lead_name,
            role="lead",
            model=model,
            display_name=display_name,
            personality=personality,
        )
        if written_path is not None:
            lead.personality_path = written_path
            lead.load_personality()
        await self._mgr._persist(lead)
        await self._mgr._audit(
            "entity.create_team",
            target=lead_name,
            details={"maestro": maestro_name, "team": team_name},
        )
        logger.info("Created team %s under maestro %s", team_name, maestro_name)
        return lead

    async def spawn_worker(
        self,
        lead_name: str,
        worker_name: str | None = None,
        task_id: int | None = None,
        display_name: str | None = None,
        personality: str | None = None,
    ) -> Worker:
        """Spawn a worker under a team lead.

        If worker_name is None, auto-generates ``w1``, ``w2``, etc.
        The worker is registered but not spawned as a subprocess — it
        stays IDLE until work is assigned via send_to_entity.

        If both ``display_name`` and ``personality`` are provided and no
        file exists at the target path, an auto-generated personality
        file is written. Pair-or-nothing: either both fields or neither.
        """
        lead = self._mgr._entities.get(lead_name)
        if lead is None:
            raise KeyError(f"Lead {lead_name!r} not found.")
        if not isinstance(lead, TeamLead):
            raise TypeError(f"Entity {lead_name!r} is not a team lead.")

        if len(lead.workers) >= lead.max_workers:
            raise RuntimeError(
                f"Lead {lead_name!r} already has "
                f"{len(lead.workers)}/{lead.max_workers} workers (max)."
            )

        if worker_name is None:
            # Auto-name: find the next available w<N>
            existing_nums = []
            for wname in lead.workers:
                suffix = wname.rsplit(".", 1)[-1]
                if suffix.startswith("w") and suffix[1:].isdigit():
                    existing_nums.append(int(suffix[1:]))
            n = max(existing_nums, default=0) + 1
            worker_name = f"w{n}"

        full_name = f"{lead_name}.{worker_name}"

        # Create worktree for isolated work if a WorktreeManager is configured
        worktree_path = None
        if self._mgr.worktree_mgr:
            worktree_path = await self._mgr.worktree_mgr.create(
                full_name, branch=f"hive/{full_name}"
            )

        worker = Worker(
            name=full_name,
            team_name=lead.team_name,
            lead_name=lead_name,
            model=lead.model,
            permission_mode=lead.permission_mode,
            task_id=task_id,
            worktree_path=worktree_path,
        )

        lead.workers.append(full_name)

        # Update the team's worker list in the parent maestro
        maestro = self._mgr._entities.get(lead.maestro_name)
        if isinstance(maestro, Maestro):
            team = maestro.get_team(lead.team_name)
            if team:
                team.workers.append(full_name)

        async with self._mgr._state_lock:
            self._mgr._entities[full_name] = worker
        self._mgr.router.register(full_name)
        written_path = self._maybe_write_auto_personality(
            entity_name=full_name,
            role="worker",
            model=lead.model,
            display_name=display_name,
            personality=personality,
        )
        if written_path is not None:
            worker.personality_path = written_path
            worker.load_personality()
        await self._mgr._persist(worker)
        await self._mgr._audit(
            "entity.spawn_worker",
            target=full_name,
            details={"lead": lead_name, "team": lead.team_name, "task_id": task_id},
        )
        logger.info("Spawned worker %s under lead %s", full_name, lead_name)
        return worker

    async def kill_team(self, maestro_name: str, team_name: str) -> None:
        """Kill a team — removes the lead and all its workers."""
        maestro = self._mgr._entities.get(maestro_name)
        if not isinstance(maestro, Maestro):
            return

        team = maestro.get_team(team_name)
        if team is None:
            return

        # Kill workers first, then the lead
        for worker_name in list(team.workers):
            await self._mgr.kill_entity(worker_name)
        if team.lead:
            await self._mgr.kill_entity(team.lead)

        maestro.remove_team(team_name)
        logger.info("Killed team %s under maestro %s", team_name, maestro_name)

    async def kill_entity(self, name: str) -> None:
        """Kill an entity's subprocess and clean up.

        If a personality file exists for this entity and was auto-generated
        (frontmatter ``auto_generated: true``), it is deleted. User-authored
        files are always preserved.
        """
        self._maybe_delete_auto_personality(name)

        adapter = self._mgr._adapters.pop(name, None)
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Failed to stop adapter for %s on kill", name)

        entity = self._mgr._entities.get(name)
        if entity:
            # Clean up worktree for workers
            if isinstance(entity, Worker) and entity.worktree_path and self._mgr.worktree_mgr:
                try:
                    await self._mgr.worktree_mgr.remove(name)
                except Exception:
                    logger.exception("Failed to remove worktree for %s", name)

            # Remove worker from parent lead's and team's worker lists
            if isinstance(entity, Worker) and entity.lead_name:
                lead = self._mgr._entities.get(entity.lead_name)
                if isinstance(lead, TeamLead) and name in lead.workers:
                    lead.workers.remove(name)
                # Also remove from the Team object on the maestro
                maestro_name = lead.maestro_name if isinstance(lead, TeamLead) else ""
                maestro = self._mgr._entities.get(maestro_name)
                if isinstance(maestro, Maestro):
                    team = maestro.get_team(entity.team_name)
                    if team and name in team.workers:
                        team.workers.remove(name)

            # When killing a lead, also drop the Team object on the maestro
            # so the team name can be reused. kill_team() already calls
            # maestro.remove_team — wrap in try/except so the two paths
            # remain idempotent.
            if isinstance(entity, TeamLead) and entity.maestro_name:
                maestro = self._mgr._entities.get(entity.maestro_name)
                if isinstance(maestro, Maestro):
                    try:
                        maestro.remove_team(entity.team_name)
                    except KeyError:
                        pass

            # Clear the transcript session_id so a stale resume isn't persisted to DB
            entity.session_id = None

            if entity.state == EntityState.RUNNING:
                entity.transition_to(EntityState.STOPPED)
            async with self._mgr._state_lock:
                self._mgr._entities.pop(name, None)

        # Remove from DB so dead entities don't reappear on restart
        if self._mgr.entity_store is not None:
            try:
                await self._mgr.entity_store.delete(name)
            except Exception:
                logger.exception("Failed to delete entity %s from DB", name)

        self._mgr.router.unregister(name)
        await self._mgr._audit("entity.kill", target=name)
        if self._mgr.scheduler is not None:
            self._mgr.scheduler.refund_autospawn(name)
        logger.info("Killed entity: %s", name)

    async def kill_all(self) -> None:
        """Gracefully shutdown all entities."""
        names = list(self._mgr._entities.keys())
        for name in names:
            await self._mgr.kill_entity(name)

    async def stop_all(self) -> None:
        """Stop all entity PTY adapters without deleting DB rows.

        Used on graceful shutdown so entities can be restored on next boot
        via restore() + rebuild_hierarchy(). Preserves session_id so the
        next session can --continue the prior conversation.
        """
        for name, adapter in list(self._mgr._adapters.items()):
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Failed to stop adapter for %s on shutdown", name)
        self._mgr._adapters.clear()

        if self._mgr.quota_monitor is not None:
            try:
                await self._mgr.quota_monitor.stop()
            except Exception:
                logger.exception("Failed to stop QuotaMonitor on shutdown")

        logger.info("Stopped %d entity sessions for restart", len(self._mgr._entities))

    async def compact_entity(self, entity_name: str) -> str:
        """Compact an entity's context: summarize, kill, re-register, seed.

        Returns the summary text on success.
        Raises KeyError if entity not found, ValueError if no active session.
        """
        entity = self._mgr._entities.get(entity_name)
        if entity is None:
            raise KeyError(f"Entity {entity_name!r} not found.")
        if not entity.session_id:
            raise ValueError(f"Entity {entity_name!r} has no active session to compact.")

        # Step 1: Ask entity to summarize its context
        summary = await self._mgr.send_to_entity(
            entity_name,
            "Summarize your entire conversation context in 3 concise bullet points. "
            "Include key decisions, current state, and next steps.",
        )

        # Step 2: Kill entity (clears session_id, removes from registry)
        await self._mgr.kill_entity(entity_name)

        # Step 3: Re-register entity in IDLE state
        async with self._mgr._state_lock:
            self._mgr._entities[entity_name] = entity
        self._mgr.router.register(entity_name)
        entity.session_id = None
        entity.state = EntityState.IDLE

        # Step 4: Seed new session with summary
        await self._mgr.send_to_entity(
            entity_name,
            f"Here is your prior context (compacted):\n{summary}\n\nContinue from here.",
        )

        await self._mgr._persist(entity)
        await self._mgr._audit(
            "entity.compact",
            target=entity_name,
            details={"summary_len": len(summary)},
        )
        logger.info("Compacted entity %s (summary: %d chars)", entity_name, len(summary))
        return summary

    async def kill_idle_entities(
        self,
        timeout_minutes: int,
        exempt_names: set[str] | None = None,
    ) -> list[str]:
        """Kill entities that have been idle longer than timeout_minutes.

        Returns list of killed entity names.
        Entities in exempt_names are never killed.
        """
        exempt = exempt_names or set()
        cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
        killed: list[str] = []

        for name, entity in list(self._mgr._entities.items()):
            if name in exempt:
                continue
            # A GATED entity is parked on an interactive gate awaiting the
            # user's decision (ADR 0004). It is intentionally idle and must
            # never be reaped, regardless of exempt_names.
            if entity.state == EntityState.GATED:
                continue
            if entity.last_activity_at is None:
                continue
            if entity.last_activity_at < cutoff:
                idle_minutes = int(
                    (datetime.now(UTC) - entity.last_activity_at).total_seconds() / 60
                )
                try:
                    await self._mgr.kill_entity(name)
                    await self._mgr._audit(
                        "entity.auto_kill_idle",
                        target=name,
                        details={"idle_minutes": idle_minutes},
                    )
                    await self._mgr._notify(
                        f"Auto-killed idle entity {name} (inactive {idle_minutes}m)"
                    )
                    killed.append(name)
                except Exception:
                    logger.exception("Failed to auto-kill idle entity %s", name)

        return killed
