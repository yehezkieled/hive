"""Persistent storage for Hive entity roster.

Keeps track of which entities exist, their role/model/state, and their
personality config path. On restart, entities are loaded via ``all()`` and
re-registered in the router (in IDLE state — Sprint 2a restores structure,
not running subprocesses).
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.models.vault import Vault


class EntityStore:
    """asyncpg-backed roster store for Hive entities."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def upsert(self, entity: Entity) -> None:
        """Insert or update an entity row by name."""
        # Extract hierarchy fields from subclass-specific attributes
        parent_name = _get_parent_name(entity)
        team_name = _get_team_name(entity)

        await self.pool.execute(
            """
            INSERT INTO entities
                (name, role, state, model, personality_path, pid, started_at,
                 session_id, parent_name, team_name,
                 permission_mode, loop_mode, current_priority,
                 worktree_path, task_id, last_activity_at, updated_at)
            VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                 $11, $12, $13, $14, $15, $16, NOW())
            ON CONFLICT (name) DO UPDATE SET
                role = EXCLUDED.role,
                state = EXCLUDED.state,
                model = EXCLUDED.model,
                personality_path = EXCLUDED.personality_path,
                pid = EXCLUDED.pid,
                started_at = EXCLUDED.started_at,
                session_id = EXCLUDED.session_id,
                parent_name = EXCLUDED.parent_name,
                team_name = EXCLUDED.team_name,
                permission_mode = EXCLUDED.permission_mode,
                loop_mode = EXCLUDED.loop_mode,
                current_priority = EXCLUDED.current_priority,
                worktree_path = EXCLUDED.worktree_path,
                task_id = EXCLUDED.task_id,
                last_activity_at = EXCLUDED.last_activity_at,
                updated_at = NOW()
            """,
            entity.name,
            entity.role,
            entity.state.value,
            entity.model,
            str(entity.personality_path) if entity.personality_path else None,
            entity.pid,
            entity.started_at,
            entity.session_id,
            parent_name,
            team_name,
            entity.permission_mode,
            entity.loop_mode,
            entity.current_priority,
            None,  # worktree_path — was Worker-only; Workers retired (Ticket 018)
            None,  # task_id — was Worker-only; Workers retired (Ticket 018)
            entity.last_activity_at,
        )

    async def load(self, name: str) -> Entity | None:
        """Fetch a single entity by name, or None if missing."""
        row = await self.pool.fetchrow(
            "SELECT * FROM entities WHERE name = $1",
            name,
        )
        return _row_to_entity(row) if row else None

    async def all(self) -> list[Entity]:
        """Fetch every persisted entity, ordered by name for stable output."""
        rows = await self.pool.fetch("SELECT * FROM entities ORDER BY name")
        return [_row_to_entity(row) for row in rows]

    async def delete(self, name: str) -> None:
        """Remove an entity from the roster."""
        await self.pool.execute("DELETE FROM entities WHERE name = $1", name)

    async def purge_role(self, role: str) -> int:
        """Delete every row for a retired role; return the count removed.

        Idempotent cleanup for startup — guards against a leftover
        ``role='worker'`` row (Worker retired, Ticket 018) zombie-restoring
        as a bare ``Entity`` after the subclass is gone.
        """
        result = await self.pool.execute("DELETE FROM entities WHERE role = $1", role)
        # asyncpg returns a status string like "DELETE 3"
        return int(result.split()[-1]) if result else 0


def _get_parent_name(entity: Entity) -> str | None:
    """Extract the parent_name for DB storage based on entity type."""
    if isinstance(entity, TeamLead):
        return entity.maestro_name or None
    return None


def _get_team_name(entity: Entity) -> str | None:
    """Extract the team_name for DB storage based on entity type."""
    if isinstance(entity, TeamLead):
        return entity.team_name or None
    return None


def _row_to_entity(row: asyncpg.Record) -> Entity:
    """Convert a row from the entities table back into the correct subclass.

    Restored entities always come back in IDLE state — the live state at the
    time of the last upsert is not useful after an orchestrator restart,
    because the subprocess died with the parent. The session_id IS preserved
    so the entity can --resume its prior conversation on the next call.
    """
    personality_path = Path(row["personality_path"]) if row["personality_path"] else None
    # Common kwargs shared by all entity types
    common = dict(
        name=row["name"],
        personality_path=personality_path,
        model=row["model"],
        state=EntityState.IDLE,
        pid=None,
        started_at=None,
        session_id=row["session_id"],
        permission_mode=row["permission_mode"] or "default",
        loop_mode=row["loop_mode"] or "ralph",
        current_priority=row["current_priority"] if row["current_priority"] is not None else 3,
        last_activity_at=row["last_activity_at"],
    )

    role = row["role"]
    if role == "maestro":
        return Maestro(**common)
    if role == "vault":
        return Vault(**common)
    if role == "lead":
        return TeamLead(
            **common,
            team_name=row["team_name"] or "",
            maestro_name=row["parent_name"] or "",
        )
    # Fallback for unknown / retired roles
    return Entity(**common, role=role)
