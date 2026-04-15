"""PostgreSQL message store for persistent message logging and querying.

Uses asyncpg with a connection pool. Schema is managed via versioned SQL
migrations in ``hive.bus.migrations``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from hive.bus.migrations import run_migrations


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register a codec so JSONB columns accept/return Python dicts."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class MessageStore:
    """asyncpg-backed message store. Preserves the Sprint 1 public interface."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Open the connection pool and run any pending migrations."""
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=2,
            max_size=10,
            init=_init_connection,
        )
        await run_migrations(self._pool)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("MessageStore not connected. Call connect() first.")
        return self._pool

    async def log_message(
        self,
        sender: str,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert a message and return its ID."""
        if conversation_id is None:
            conversation_id = uuid.uuid4().hex[:12]

        row = await self.pool.fetchrow(
            """
            INSERT INTO messages (sender, recipient, content, conversation_id, metadata)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            sender,
            recipient,
            content,
            conversation_id,
            metadata,
        )
        return row["id"]

    async def get_messages(
        self,
        recipient: str,
        since: datetime | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query messages for a recipient."""
        query = "SELECT * FROM messages WHERE recipient = $1"
        params: list[Any] = [recipient]

        if since is not None:
            params.append(since)
            query += f" AND timestamp > ${len(params)}"

        if status is not None:
            params.append(status)
            query += f" AND status = ${len(params)}"

        params.append(limit)
        query += f" ORDER BY timestamp ASC LIMIT ${len(params)}"

        rows = await self.pool.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_conversation(self, conversation_id: str) -> list[dict]:
        """Get all messages in a conversation thread."""
        rows = await self.pool.fetch(
            "SELECT * FROM messages WHERE conversation_id = $1 ORDER BY timestamp ASC",
            conversation_id,
        )
        return [dict(row) for row in rows]

    async def get_recent(self, limit: int = 20) -> list[dict]:
        """Get the most recent messages across all conversations."""
        rows = await self.pool.fetch(
            "SELECT * FROM messages ORDER BY timestamp DESC LIMIT $1",
            limit,
        )
        return [dict(row) for row in rows]

    async def update_status(self, message_id: int, status: str) -> None:
        """Update the status of a message."""
        await self.pool.execute(
            "UPDATE messages SET status = $1 WHERE id = $2",
            status,
            message_id,
        )

    async def count_messages(self, recipient: str | None = None) -> int:
        """Count messages, optionally filtered by recipient."""
        if recipient:
            return await self.pool.fetchval(
                "SELECT COUNT(*) FROM messages WHERE recipient = $1",
                recipient,
            )
        return await self.pool.fetchval("SELECT COUNT(*) FROM messages")
