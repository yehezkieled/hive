"""SQLite message store for persistent message logging and querying."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import aiosqlite


class MessageStore:
    """SQLite-backed message store with WAL mode for concurrent access."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database connection and create tables if needed."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                conversation_id TEXT,
                metadata TEXT
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_recipient
            ON messages(recipient, status)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id)
        """)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("MessageStore not connected. Call connect() first.")
        return self._db

    async def log_message(
        self,
        sender: str,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
        metadata: str | None = None,
    ) -> int:
        """Insert a message and return its ID."""
        if conversation_id is None:
            conversation_id = uuid.uuid4().hex[:12]

        cursor = await self.db.execute(
            """
            INSERT INTO messages (sender, recipient, content, timestamp, conversation_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sender, recipient, content, time.time(), conversation_id, metadata),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_messages(
        self,
        recipient: str,
        since: float | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query messages for a recipient."""
        query = "SELECT * FROM messages WHERE recipient = ?"
        params: list = [recipient]

        if since is not None:
            query += " AND timestamp > ?"
            params.append(since)

        if status is not None:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_conversation(self, conversation_id: str) -> list[dict]:
        """Get all messages in a conversation thread."""
        cursor = await self.db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_recent(self, limit: int = 20) -> list[dict]:
        """Get the most recent messages across all conversations."""
        cursor = await self.db.execute(
            "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_status(self, message_id: int, status: str) -> None:
        """Update the status of a message."""
        await self.db.execute(
            "UPDATE messages SET status = ? WHERE id = ?",
            (status, message_id),
        )
        await self.db.commit()

    async def count_messages(self, recipient: str | None = None) -> int:
        """Count messages, optionally filtered by recipient."""
        if recipient:
            cursor = await self.db.execute(
                "SELECT COUNT(*) FROM messages WHERE recipient = ?", (recipient,)
            )
        else:
            cursor = await self.db.execute("SELECT COUNT(*) FROM messages")
        row = await cursor.fetchone()
        return row[0]  # type: ignore[index]
