"""Process manager — spawns, tracks, and kills Claude Code agent subprocesses."""

from __future__ import annotations

import logging
from pathlib import Path

from hive.bus.entity_store import EntityStore
from hive.bus.router import MessageRouter
from hive.bus.token_store import TokenStore
from hive.models.entity import Entity, EntityState
from hive.process.claude_session import ClaudeSession
from hive.process.worktree import WorktreeManager

logger = logging.getLogger(__name__)


class ProcessManager:
    """Manages all Claude Code subprocesses for Hive entities."""

    def __init__(
        self,
        router: MessageRouter,
        worktree_mgr: WorktreeManager | None = None,
        max_sessions: int = 3,
        entity_store: EntityStore | None = None,
        token_store: TokenStore | None = None,
    ) -> None:
        self.router = router
        self.worktree_mgr = worktree_mgr
        self.max_sessions = max_sessions
        self.entity_store = entity_store
        self.token_store = token_store
        self._entities: dict[str, Entity] = {}
        self._sessions: dict[str, ClaudeSession] = {}

    async def _persist(self, entity: Entity) -> None:
        """Persist an entity's current state to the entity store, if configured.

        Called after every state transition the manager drives. Kept at the
        manager level (not inside Entity.transition_to) so the Entity
        dataclass stays sync and DB-free for tests.
        """
        if self.entity_store is None:
            return
        try:
            await self.entity_store.upsert(entity)
        except Exception:
            # Persistence failure should not take down the orchestrator —
            # log and continue. The in-memory roster is still correct.
            logger.exception("Failed to persist entity %s", entity.name)

    async def _record_usage(self, entity: Entity, session: ClaudeSession) -> None:
        """Record token usage from a completed session, if a store is configured.

        Merges the entity's canonical ``model`` into the session's captured
        usage dict before handing it to the store. Fire-and-continue: any
        DB error is logged and swallowed, since token bookkeeping must not
        take down the user-facing send path.
        """
        if self.token_store is None:
            return
        usage = session.last_usage
        if usage is None:
            return
        try:
            await self.token_store.record(
                entity.name,
                {**usage, "model": entity.model},
            )
        except Exception:
            logger.exception("Failed to record token usage for %s", entity.name)

    @property
    def entities(self) -> dict[str, Entity]:
        return dict(self._entities)

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.is_alive)

    async def spawn_entity(self, entity: Entity, cwd: Path | None = None) -> ClaudeSession:
        """Spawn a Claude Code subprocess for an entity.

        Loads personality, builds CLI args, creates session, and starts it.
        """
        if self.active_count >= self.max_sessions:
            raise RuntimeError(
                f"Max concurrent sessions ({self.max_sessions}) reached. Kill an entity first."
            )

        if entity.name in self._sessions and self._sessions[entity.name].is_alive:
            raise RuntimeError(f"Entity {entity.name!r} is already running.")

        # Load personality if available
        entity.load_personality()

        # Build CLI args
        args = entity.build_cli_args()

        # Transition state
        entity.transition_to(EntityState.STARTING)
        await self._persist(entity)

        # Create and start session
        session = ClaudeSession(args=args, cwd=cwd)
        try:
            await session.start()
            entity.pid = session.pid
            entity.transition_to(EntityState.RUNNING)
        except Exception:
            entity.transition_to(EntityState.ERROR)
            await self._persist(entity)
            raise

        # Register in router for message delivery
        self.router.register(entity.name)

        # Track
        self._entities[entity.name] = entity
        self._sessions[entity.name] = session

        await self._persist(entity)

        logger.info(
            "Spawned entity %s (role=%s, model=%s, pid=%s)",
            entity.name,
            entity.role,
            entity.model,
            entity.pid,
        )
        return session

    async def send_to_entity(self, entity_name: str, prompt: str) -> str:
        """Send a prompt to an entity and get the response.

        For MVP, this is one-shot: spawns a new session each time.
        The session processes the prompt and returns the result.
        """
        entity = self._entities.get(entity_name)
        if entity is None:
            raise KeyError(f"Entity {entity_name!r} not found.")

        # For one-shot mode, we create a fresh session for each prompt
        args = entity.build_cli_args()
        session = ClaudeSession(args=args)
        await session.start()

        try:
            response = await session.send_prompt(prompt)
            await self._record_usage(entity, session)
        finally:
            await session.kill()

        return response

    async def kill_entity(self, name: str) -> None:
        """Kill an entity's subprocess and clean up."""
        session = self._sessions.get(name)
        if session:
            await session.kill()
            del self._sessions[name]

        entity = self._entities.get(name)
        if entity:
            if entity.state == EntityState.RUNNING:
                entity.transition_to(EntityState.STOPPED)
                await self._persist(entity)
            del self._entities[name]

        self.router.unregister(name)
        logger.info("Killed entity: %s", name)

    async def kill_all(self) -> None:
        """Gracefully shutdown all entities."""
        names = list(self._entities.keys())
        for name in names:
            await self.kill_entity(name)

    def get_status(self) -> list[dict]:
        """Return status of all tracked entities."""
        statuses = []
        for name, entity in self._entities.items():
            session = self._sessions.get(name)
            statuses.append(
                {
                    "name": name,
                    "role": entity.role,
                    "state": entity.state.value,
                    "model": entity.model,
                    "pid": entity.pid,
                    "alive": session.is_alive if session else False,
                    "uptime": entity.uptime_seconds,
                }
            )
        return statuses

    async def health_check(self) -> list[str]:
        """Check which sessions are dead but entities think they're running.

        Returns list of entity names that need attention.
        """
        unhealthy: list[str] = []
        for name, entity in self._entities.items():
            session = self._sessions.get(name)
            if entity.state == EntityState.RUNNING and (session is None or not session.is_alive):
                unhealthy.append(name)
                entity.transition_to(EntityState.ERROR)
                await self._persist(entity)
                logger.warning("Entity %s died unexpectedly", name)
        return unhealthy

    def restore(self, entity: Entity) -> None:
        """Re-register a persisted entity on orchestrator startup.

        Structural restoration only — no subprocess is spawned. The entity
        comes back in IDLE state (forced by EntityStore on load) so the next
        spawn goes through the normal IDLE -> STARTING -> RUNNING path. We
        can't reattach to the old PID because the subprocess died with the
        previous orchestrator.
        """
        self._entities[entity.name] = entity
        self.router.register(entity.name)
        logger.info(
            "Restored entity %s (role=%s, model=%s)",
            entity.name,
            entity.role,
            entity.model,
        )
