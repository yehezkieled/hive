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

import json
import logging
import sys
import tempfile
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
from hive.process.names import validate_name
from hive.process.ownership_policy import WritablePolicy, settings_payload, writable_policy
from hive.process.skill_curation import skill_denylist_for
from hive.process.tool_policy import role_tool_denylist
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

    No ``## Tools`` section is emitted. The role tool guard lives in
    ``tool_policy.role_tool_denylist`` (Ticket 015, ADR 0010), merged
    into the adapter config on every spawn — restart included — so it
    no longer depends on a personality file existing. A hand-written
    personality's ``## Tools`` section still works, as a per-Entity
    override.
    """
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
        f"{knowledge_section}"
    )


def _adapter_config_from_entity(entity: Entity) -> ClaudeAdapterConfig:
    """Map an Entity to the ClaudeAdapterConfig needed by ClaudeAdapter."""
    # Merge three deny sources, de-duplicating while keeping first-seen
    # order: the entity's own tokens (personality ``## Tools`` override),
    # the role tool guard (Ticket 015, ADR 0010 — runs on every spawn,
    # restart included), and the per-role skill denylist (Ticket 012).
    disallowed_tools = list(
        dict.fromkeys(
            list(entity.disallowed_tools)
            + role_tool_denylist(entity.role)
            + skill_denylist_for(entity.role)
        )
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
        is_pa=getattr(entity, "is_pa", False),
    )


def _maestro_fence(
    entity: Entity,
    *,
    is_pa: bool,
    own_root: str | None,
    owned_roots: list[str],
) -> WritablePolicy | None:
    """The ownership write-fence for a maestro spawn, or None if nothing to fence.

    Project ownership (Ticket 024, ADR 0017) only fences maestros — leaf work
    is already scoped by the worktree floor (ADR 0010). The PA is fenced *out*
    of every owned project root; a project maestro is fenced *into* its own
    root. A maestro with no fence target (PA with no owned projects, or a
    project maestro with no project yet) gets no policy.
    """
    if not isinstance(entity, Maestro):
        return None
    if is_pa:
        if not owned_roots:
            return None
        return writable_policy(is_pa=True, own_root=None, owned_roots=owned_roots)
    if not own_root:
        return None
    return writable_policy(is_pa=False, own_root=own_root, owned_roots=owned_roots)


def _guard_command() -> str:
    """The hook command that runs the ownership guard, using this interpreter.

    ``sys.executable`` is the service venv's python, which has ``hive``
    installed — so ``-m hive.hooks.ownership_guard`` resolves regardless of the
    spawned entity's cwd (a project maestro runs in its project root, not the
    Hive checkout).
    """
    return f"{sys.executable} -m hive.hooks.ownership_guard"


# Settings every Hive spawn carries, whatever the entity's role (Ticket 067).
#
# ``remoteControlAtStartup: false`` — Hive drives its sessions over the PTY
# itself; they must never auto-connect to Claude Code Remote Control. With the
# key unset, the CLI falls through to its org/rollout default, which (when on)
# bridges every flag-less interactive session — so each maestro surfaced in
# the Claude app, retitled by the scheduler poke. An explicit ``false`` in the
# ``--settings`` tier beats that default; only an explicit ``--remote-control``
# flag (which Hive never passes) would override it.
_BASE_SPAWN_SETTINGS: dict = {"remoteControlAtStartup": False}


def _spawn_settings_payload(ownership: dict | None) -> dict:
    """The per-spawn settings: the role-independent base plus an ownership fence.

    ``ownership`` is the Ticket 024 guard-hook payload for a fenced maestro, or
    None when the entity has nothing to fence (leads, the PA owning nothing).
    """
    return {**_BASE_SPAWN_SETTINGS, **(ownership or {})}


def _write_spawn_settings(entity_name: str, payload: dict) -> Path:
    """Write the per-spawn settings.json, return its path.

    Overwritten every spawn (restart-proof, same contract as the tool
    denylist). One stable path per entity so stale files don't accumulate.
    """
    spawn_dir = Path(tempfile.gettempdir()) / "hive-spawn-settings"
    spawn_dir.mkdir(parents=True, exist_ok=True)
    path = spawn_dir / f"{entity_name}.settings.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


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
        # Ticket 032: reject a path/ref-hostile maestro name before the
        # duplicate check and before any registration/persist.
        validate_name(name, kind="maestro name")

        if name in self._mgr._entities:
            raise ValueError(f"Entity {name!r} already exists.")

        maestro = Maestro(
            name=name,
            model=model,
            personality_path=personality_path,
        )
        # INTENTIONAL SHADOW (Ticket 023, design D6): this overrides the
        # Entity-level default (`Entity.permission_mode = "default"`,
        # models/entity.py) for newly registered maestros only. First-spawn
        # safety: a brand-new maestro's first turn must not stall on
        # permission prompts before any gate bridge exists for it. Existing
        # maestros restored from postgres keep their persisted mode. Do not
        # "fix" this back to the Entity default without revisiting D6.
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

    async def _ownership_spawn_overrides(self, entity: Entity) -> tuple[dict | None, Path | None]:
        """Per-spawn ownership fence: (settings payload, project-home cwd override).

        Builds the PreToolUse ownership-guard settings payload for a maestro
        (Ticket 024, ADR 0017) and, for a project maestro, returns its project
        root as the cwd override. Returns ``(None, None)`` for non-maestros,
        when no project store is wired (unit tests), or when the maestro has no
        fence target. The caller merges the payload into the spawn settings
        file — see ``_spawn_settings_payload``.
        """
        store = getattr(self._mgr, "project_store", None)
        if not isinstance(entity, Maestro) or store is None:
            return (None, None)
        owned_roots = await store.owned_roots()
        is_pa = entity.is_pa  # single source of truth (Ticket 033)
        own_root: str | None = None
        if not is_pa:
            project = await store.for_maestro(entity.name)
            own_root = str(project.root_path) if project else None
        policy = _maestro_fence(entity, is_pa=is_pa, own_root=own_root, owned_roots=owned_roots)
        payload: dict | None = None
        if policy is not None:
            payload = settings_payload(policy, guard_command=_guard_command())
        cwd = Path(own_root) if own_root else None
        return (payload, cwd)

    async def _get_or_create_adapter(self, entity: Entity) -> ClaudeAdapter:
        """Return a live PTY adapter for entity, creating one if needed.

        Adapters are cached per entity so the same persistent PTY process
        handles all of that entity's turns.
        """
        existing = self._mgr._adapters.get(entity.name)
        if existing is not None and existing.is_alive():
            return existing

        # Worktree floor across restarts (Ticket 015, ADR 0010): the entity
        # store does not round-trip worktree_path, so a lead restored from
        # persistence comes back path-less. Lazily (re-)provision here —
        # WorktreeManager.create is idempotent and hands back the existing
        # path when the worktree survived the restart.
        if isinstance(entity, TeamLead) and entity.worktree_path is None and self._mgr.worktree_mgr:
            entity.worktree_path = await self._mgr.worktree_mgr.create(
                entity.name, branch=f"hive/{entity.name}"
            )

        config = _adapter_config_from_entity(entity)
        lead_cwd = (
            Path(entity.worktree_path)
            if isinstance(entity, TeamLead) and entity.worktree_path
            else None
        )
        # Every spawn gets a --settings file (Ticket 067: Remote Control
        # opt-out). Project ownership (Ticket 024, ADR 0017) adds a PreToolUse
        # guard hook to it for a fenced maestro and homes a project maestro in
        # its project root; leads (worktree floor) and a PA owning nothing get
        # the base settings only.
        ownership_settings, maestro_cwd = await self._ownership_spawn_overrides(entity)
        config.settings_path = _write_spawn_settings(
            entity.name, _spawn_settings_payload(ownership_settings)
        )
        cwd = lead_cwd or maestro_cwd
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
        model: str = "opus",
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
        # Ticket 032: reject a path/ref-hostile team name BEFORE anything is
        # derived from it — before Maestro.create_team and, crucially, before
        # worktree_mgr.create() makes a worktree dir or ``hive/<name>`` branch.
        validate_name(team_name, kind="team name")

        entity = self._mgr._entities.get(maestro_name)
        if entity is None:
            raise KeyError(f"Maestro {maestro_name!r} not found.")
        if not isinstance(entity, Maestro):
            raise TypeError(f"Entity {maestro_name!r} is not a maestro.")

        # Delegate to Maestro.create_team (raises ValueError on duplicate)
        team = entity.create_team(team_name)

        lead_name = f"{maestro_name}.{team_name}"

        # Worktree floor (Ticket 015, ADR 0010): the lead runs in its own
        # worktree, so leaf agents launched from its session — even
        # non-isolated ones — never write to the live checkout.
        worktree_path = None
        if self._mgr.worktree_mgr:
            worktree_path = await self._mgr.worktree_mgr.create(
                lead_name, branch=f"hive/{lead_name}"
            )

        lead = TeamLead(
            name=lead_name,
            team_name=team_name,
            maestro_name=maestro_name,
            model=model,
            permission_mode=entity.permission_mode,
            worktree_path=worktree_path,
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
            # Clean up worktree for leads (worktree floor, 015)
            if isinstance(entity, TeamLead) and entity.worktree_path and self._mgr.worktree_mgr:
                try:
                    await self._mgr.worktree_mgr.remove(name)
                except Exception:
                    logger.exception("Failed to remove worktree for %s", name)

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
            # An adapter with a turn in flight is working, not idle —
            # last_activity_at only updates at turn start, so a long turn
            # (a lead's Workflow sync-wait, ADR 0010) looks stale while
            # actively running.
            adapter = self._mgr._adapters.get(name)
            if adapter is not None and adapter.is_busy():
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

    async def reconcile_worktrees(self) -> dict[str, list[str]]:
        """Crash-recovery pass over the worktree floor (Ticket 025, ADR 0016).

        Run once at startup, after entity restore + ``rebuild_hierarchy``.
        Two halves:

        - **Re-adopt**: every restored ``TeamLead`` comes back path-less (the
          entity store never round-trips ``worktree_path``). Derive its
          worktree from its name and set it eagerly — the idempotent
          ``create`` hands back the surviving dir untouched (uncommitted edits
          intact), or makes a fresh one if it's gone.
        - **Sweep orphans**: a worktree under ``WORKTREES_DIR`` with no owning
          lead (crash mid-spawn, or a failed-and-swallowed mid-kill removal).
          Prune stale git-admin records, remove clean orphans, and
          **quarantine** (keep + warn) any orphan holding uncommitted work —
          never delete unpushed work.

        Scoped strictly to ``WORKTREES_DIR`` via ``managed_worktrees`` — the
        main checkout and the developer's own ``.claude/worktrees/`` sessions
        are structurally out of reach. No-op when no worktree manager is wired.
        """
        report: dict[str, list[str]] = {
            "readopted": [],
            "pruned": [],
            "removed": [],
            "quarantined": [],
        }
        wt_mgr = self._mgr.worktree_mgr
        if wt_mgr is None:
            return report

        # 1. Prune stale git-admin records (working dir already gone) so they
        #    aren't mistaken for sweepable orphans below.
        report["pruned"] = await wt_mgr.prune()

        # 2. Eagerly re-adopt each restored lead's worktree. create() is
        #    idempotent — a surviving dir is returned untouched (edits intact).
        owned: set[str] = set()
        for entity in list(self._mgr._entities.values()):
            if not isinstance(entity, TeamLead):
                continue
            owned.add(entity.name)
            if entity.worktree_path is None:
                entity.worktree_path = await wt_mgr.create(
                    entity.name, branch=f"hive/{entity.name}"
                )
                report["readopted"].append(entity.name)
                await self._mgr._audit("worktree.readopted", target=entity.name)

        # 3. Sweep orphans — worktrees under WORKTREES_DIR with no owning lead.
        for wt in await wt_mgr.managed_worktrees():
            name = Path(wt["path"]).name
            if name in owned:
                continue
            if await wt_mgr.is_dirty(name):
                report["quarantined"].append(name)
                await self._mgr._audit("worktree.orphan_quarantined", target=name)
                logger.warning(
                    "Orphan worktree %s holds uncommitted work — quarantined, not removed",
                    name,
                )
            else:
                await wt_mgr.remove(name)
                report["removed"].append(name)
                await self._mgr._audit("worktree.orphan_removed", target=name)

        logger.info(
            "Worktree reconciliation: %d re-adopted, %d pruned, %d removed, %d quarantined",
            len(report["readopted"]),
            len(report["pruned"]),
            len(report["removed"]),
            len(report["quarantined"]),
        )
        return report
