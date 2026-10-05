"""Lifecycle manager — entity registration, spawn, kill and compact lifted out
of ProcessManager.

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
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hive.mcp.config import mcp_servers_enabled
from hive.models.entity import (
    Entity,
    EntityState,
    resolve_advisor,
)
from hive.process.skill_curation import skill_denylist_for
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.harness import RuntimeContext
from hive.runtime.harness_runtime import HarnessRuntime

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager

logger = logging.getLogger(__name__)


def _adapter_config_from_entity(entity: Entity) -> AdapterConfig:
    """Map an Entity to the harness-neutral AdapterConfig every adapter consumes."""
    # Merge two deny sources, de-duplicating while keeping first-seen
    # order: the entity's own tokens (the Vault's Bash/Write/Edit denylist,
    # personality ``## Tools`` override) and the skill denylist (Ticket 012).
    disallowed_tools = list(
        dict.fromkeys(list(entity.disallowed_tools) + skill_denylist_for(entity.role))
    )
    return AdapterConfig(
        model=entity.model,
        codex_usage=dict(entity.codex_usage),
        system_prompt=entity.system_prompt,
        allowed_tools=list(entity.allowed_tools),
        disallowed_tools=disallowed_tools,
        permission_mode=entity.permission_mode,
        role=entity.role,
        name=entity.name,
        mcp_config_path=Path(entity.mcp_config_path) if mcp_servers_enabled() else None,
        advisor=resolve_advisor(entity.model, entity.advisor, entity.role),
    )


# Settings every Hive spawn carries, whatever the entity's role (Ticket 067).
#
# ``remoteControlAtStartup: false`` — Hive drives its sessions over the PTY
# itself; they must never auto-connect to Claude Code Remote Control. With the
# key unset, the CLI falls through to its org/rollout default, which (when on)
# bridges every flag-less interactive session — so each entity surfaced in
# the Claude app, retitled by a poke. An explicit ``false`` in the
# ``--settings`` tier beats that default; only an explicit ``--remote-control``
# flag (which Hive never passes) would override it.
_BASE_SPAWN_SETTINGS: dict = {"remoteControlAtStartup": False}


def _spawn_settings_payload() -> dict:
    """The per-spawn settings: the role-independent base."""
    return dict(_BASE_SPAWN_SETTINGS)


def _spawn_settings_dir() -> Path:
    """Where per-spawn settings files live. Tests patch this (see conftest)."""
    return Path(tempfile.gettempdir()) / "hive-spawn-settings"


def _write_spawn_settings(entity_name: str, payload: dict) -> Path:
    """Write the per-spawn settings.json, return its path.

    Overwritten every spawn (restart-proof, same contract as the tool
    denylist). One stable path per entity so stale files don't accumulate.
    """
    spawn_dir = _spawn_settings_dir()
    spawn_dir.mkdir(parents=True, exist_ok=True)
    path = spawn_dir / f"{entity_name}.settings.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


class LifecycleManager:
    """Entity registration, spawn, kill and compact.

    One responsibility cluster lifted out of ProcessManager. All shared
    state lives on the facade and is reached via ``self._mgr``.
    """

    def __init__(self, mgr: ProcessManager) -> None:
        self._mgr = mgr

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

    async def _get_or_create_adapter(self, entity: Entity) -> HarnessRuntime:
        """Return the entity's HarnessRuntime, creating one if needed.

        One HarnessRuntime is cached per entity. It owns harness + mode selection
        (ADR 0029): headless on the preferred signed-in harness by default, a
        persistent PTY session only as the fallback — and spawns nothing until a
        turn needs it.
        """
        existing = self._mgr._adapters.get(entity.name)
        if existing is not None and existing.is_alive():
            return existing

        config = _adapter_config_from_entity(entity)
        # Every spawn gets a --settings file (Ticket 067: Remote Control opt-out).
        config.settings_path = _write_spawn_settings(entity.name, _spawn_settings_payload())
        cwd = None
        adapter = HarnessRuntime(
            RuntimeContext(
                config=config,
                cwd=cwd,
                gate_coordinator=self._mgr.gate_coordinator,
                entity_name=entity.name,
                on_gate_state=self._mgr._on_gate_state,
                resume_session_id=entity.session_id,
            ),
            self._mgr.harness_detector,
        )
        await adapter.start()
        async with self._mgr._state_lock:
            self._mgr._adapters[entity.name] = adapter
        return adapter

    async def kill_entity(self, name: str) -> None:
        """Kill an entity's subprocess and clean up.

        If a personality file exists for this entity and was auto-generated
        (frontmatter ``auto_generated: true``), it is deleted. User-authored
        files are always preserved.
        """
        adapter = self._mgr._adapters.pop(name, None)
        if adapter is not None:
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Failed to stop adapter for %s on kill", name)

        entity = self._mgr._entities.get(name)
        if entity:
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
        logger.info("Killed entity: %s", name)

    async def kill_all(self) -> None:
        """Gracefully shutdown all entities."""
        names = list(self._mgr._entities.keys())
        for name in names:
            await self._mgr.kill_entity(name)

    async def stop_all(self) -> None:
        """Stop all entity PTY adapters without deleting DB rows.

        Used on graceful shutdown so entities can be restored on next boot
        via restore(). Preserves session_id so the
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
            # looks stale while actively running.
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
