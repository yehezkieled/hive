"""Persistent storage for the Hive project registry (Ticket 024).

Mirrors the ``EntityStore`` pattern: an asyncpg pool, ``INSERT ... ON CONFLICT
DO UPDATE`` for idempotent upserts, and typed errors for invariant violations.
The registry enforces ``1 project <-> <=1 maestro`` via ``assign``.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from hive.models.project import Project, ProjectOwnershipError


class ProjectStore:
    """asyncpg-backed store for the project registry."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def upsert(self, project: Project) -> None:
        """Insert or update a project row by name."""
        await self.pool.execute(
            """
            INSERT INTO projects (name, root_path, owning_maestro, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (name) DO UPDATE SET
                root_path = EXCLUDED.root_path,
                owning_maestro = EXCLUDED.owning_maestro,
                updated_at = NOW()
            """,
            project.name,
            str(project.root_path),
            project.owning_maestro,
        )

    async def load(self, name: str) -> Project | None:
        """Fetch a single project by name, or None if missing."""
        row = await self.pool.fetchrow(
            "SELECT * FROM projects WHERE name = $1",
            name,
        )
        return _row_to_project(row) if row else None

    async def by_root_path(self, root_path: str | Path) -> Project | None:
        """Fetch the project registered at a root path, or None if none is."""
        row = await self.pool.fetchrow(
            "SELECT * FROM projects WHERE root_path = $1",
            str(root_path),
        )
        return _row_to_project(row) if row else None

    async def owned_roots(self) -> list[str]:
        """Return the abs root_paths of all owned projects, sorted.

        The PA's write-fence list: every project that has an owning maestro.
        """
        rows = await self.pool.fetch(
            """
            SELECT root_path FROM projects
            WHERE owning_maestro IS NOT NULL
            ORDER BY root_path
            """
        )
        return [row["root_path"] for row in rows]

    async def for_maestro(self, maestro: str) -> Project | None:
        """Return the project this maestro owns, or None if it owns none."""
        row = await self.pool.fetchrow(
            "SELECT * FROM projects WHERE owning_maestro = $1",
            maestro,
        )
        return _row_to_project(row) if row else None

    async def all(self) -> list[Project]:
        """Fetch every project, ordered by name for stable output."""
        rows = await self.pool.fetch("SELECT * FROM projects ORDER BY name")
        return [_row_to_project(row) for row in rows]

    async def delete(self, name: str) -> None:
        """Remove a project from the registry."""
        await self.pool.execute("DELETE FROM projects WHERE name = $1", name)

    async def assign(self, name: str, maestro: str) -> None:
        """Assign ``maestro`` as the owner of project ``name``.

        Enforces the ``1 project <-> <=1 maestro`` invariant. No-op if the
        maestro already owns exactly this project (idempotent).
        """
        project = await self.load(name)
        if (
            project is not None
            and project.owning_maestro is not None
            and project.owning_maestro != maestro
        ):
            raise ProjectOwnershipError(
                f"project {name!r} is already owned by {project.owning_maestro!r}"
            )

        existing = await self.for_maestro(maestro)
        if existing is not None and existing.name != name:
            raise ProjectOwnershipError(
                f"maestro {maestro!r} already owns project {existing.name!r}"
            )

        await self.pool.execute(
            "UPDATE projects SET owning_maestro = $1, updated_at = NOW() WHERE name = $2",
            maestro,
            name,
        )


def _row_to_project(row: asyncpg.Record) -> Project:
    """Convert a projects row into a Project, restoring root_path as a Path."""
    return Project(
        name=row["name"],
        root_path=Path(row["root_path"]),
        owning_maestro=row["owning_maestro"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
