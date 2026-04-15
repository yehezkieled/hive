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


class EntityStore:
    """asyncpg-backed roster store for Hive entities."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def upsert(self, entity: Entity) -> None:
        """Insert or update an entity row by name."""
        await self.pool.execute(
            """
            INSERT INTO entities
                (name, role, state, model, personality_path, pid, started_at, updated_at)
            VALUES
                ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (name) DO UPDATE SET
                role = EXCLUDED.role,
                state = EXCLUDED.state,
                model = EXCLUDED.model,
                personality_path = EXCLUDED.personality_path,
                pid = EXCLUDED.pid,
                started_at = EXCLUDED.started_at,
                updated_at = NOW()
            """,
            entity.name,
            entity.role,
            entity.state.value,
            entity.model,
            str(entity.personality_path) if entity.personality_path else None,
            entity.pid,
            entity.started_at,
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


def _row_to_entity(row: asyncpg.Record) -> Entity:
    """Convert a row from the entities table back into an Entity dataclass.

    Restored entities always come back in IDLE state — the live state at the
    time of the last upsert is not useful after an orchestrator restart,
    because the subprocess died with the parent.
    """
    personality_path = Path(row["personality_path"]) if row["personality_path"] else None
    return Entity(
        name=row["name"],
        role=row["role"],
        personality_path=personality_path,
        model=row["model"],
        state=EntityState.IDLE,
        pid=None,
        started_at=None,
    )
