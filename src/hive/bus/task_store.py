"""Persistent storage for Hive tasks.

Sprint 2b ships minimal CRUD — create, get, list, update_status. No worker
consumption yet; `claim_next` with SELECT ... FOR UPDATE SKIP LOCKED lands
in Sprint 3 when workers exist to claim from the queue.
"""

from __future__ import annotations

import asyncpg

from hive.models.task import Task, TaskStatus


class TaskStore:
    """asyncpg-backed store for Hive tasks."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create(
        self,
        title: str,
        description: str | None = None,
        priority: int = 3,
        assigned_to: str | None = None,
        created_by: str = "system",
    ) -> Task:
        """Insert a new task and return the created row."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO tasks (title, description, priority, assigned_to, created_by)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            title,
            description,
            priority,
            assigned_to,
            created_by,
        )
        return _row_to_task(row)

    async def get(self, task_id: int) -> Task | None:
        """Fetch a single task by id, or None if missing."""
        row = await self.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
        return _row_to_task(row) if row else None

    async def list(
        self,
        status: TaskStatus | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """List tasks, optionally filtered by status.

        Orders by priority ascending (0 = most urgent first), then by
        creation time so same-priority tasks come out oldest-first.
        """
        if status is None:
            rows = await self.pool.fetch(
                "SELECT * FROM tasks ORDER BY priority ASC, created_at ASC LIMIT $1",
                limit,
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT * FROM tasks
                WHERE status = $1
                ORDER BY priority ASC, created_at ASC
                LIMIT $2
                """,
                status.value,
                limit,
            )
        return [_row_to_task(row) for row in rows]

    async def update_status(self, task_id: int, status: TaskStatus) -> None:
        """Update a task's status. Sets completed_at when moving to COMPLETED."""
        if status is TaskStatus.COMPLETED:
            await self.pool.execute(
                "UPDATE tasks SET status = $1, completed_at = NOW() WHERE id = $2",
                status.value,
                task_id,
            )
        else:
            await self.pool.execute(
                "UPDATE tasks SET status = $1 WHERE id = $2",
                status.value,
                task_id,
            )

    async def claim_next(self, entity_name: str) -> Task | None:
        """Atomically claim the highest-priority pending task.

        Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers
        never claim the same task. Returns None if the queue is empty.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'pending'
                    ORDER BY priority ASC, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """,
                )
                if row is None:
                    return None

                await conn.execute(
                    "UPDATE tasks SET status = 'in_progress', assigned_to = $1 WHERE id = $2",
                    entity_name,
                    row["id"],
                )

        # Re-fetch to get the updated row
        updated = await self.pool.fetchrow("SELECT * FROM tasks WHERE id = $1", row["id"])
        return _row_to_task(updated)


def _row_to_task(row: asyncpg.Record) -> Task:
    """Convert a row from the tasks table into a Task dataclass."""
    return Task(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        priority=row["priority"],
        assigned_to=row["assigned_to"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
