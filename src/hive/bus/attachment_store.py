"""Persistent metadata for uploaded files.

Sprint 17 ships file transit: Telegram and web upload paths drop files
onto the VPS filesystem under ``UPLOADS_DIR`` and record one row per
file here. ``forwarded_to`` records which entity (if any) the caption
routed the file to; NULL means the file was stored without an agent
reading it.

Sprint 18 adds embedding columns. ``update_embedding`` is called after
``save`` (sync, but the row persists even if embedding fails). ``search``
mirrors ``BlueprintStore.search`` — cosine distance via pgvector with an
optional ``max_distance`` filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from hive.knowledge.embedder import embed_texts


@dataclass
class AttachmentMeta:
    """One row from the attachments table."""

    id: int
    file_path: str
    original_name: str | None
    mime_type: str | None
    size_bytes: int | None
    source: str
    actor: str | None
    forwarded_to: str | None
    created_at: datetime


class AttachmentStore:
    """asyncpg-backed store for upload metadata."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def _ensure_vector_codec(self, conn: asyncpg.Connection) -> None:
        """Register the pgvector codec on this connection.

        pgvector's Python codec is connection-scoped and pool connections
        are reused, so every method that reads/writes ``vector`` columns
        must register before issuing the query.
        """
        await register_vector(conn)

    async def save(
        self,
        *,
        file_path: str,
        original_name: str | None,
        mime_type: str | None,
        size_bytes: int | None,
        source: str,
        actor: str | None,
        forwarded_to: str | None = None,
    ) -> int:
        """Insert one attachment row and return its id."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO attachments
                (file_path, original_name, mime_type, size_bytes, source, actor, forwarded_to)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            file_path,
            original_name,
            mime_type,
            size_bytes,
            source,
            actor,
            forwarded_to,
        )
        return row["id"]

    async def update_embedding(
        self,
        attachment_id: int,
        embedding: list[float],
        embed_text: str,
    ) -> None:
        """Set the embedding + embed_text for an attachment row."""
        async with self.pool.acquire() as conn:
            await self._ensure_vector_codec(conn)
            await conn.execute(
                """
                UPDATE attachments
                SET embedding = $1, embed_text = $2
                WHERE id = $3
                """,
                embedding,
                embed_text,
                attachment_id,
            )

    async def search(
        self,
        query: str,
        limit: int = 5,
        max_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return attachments ranked by cosine similarity to the query text.

        Mirrors :meth:`BlueprintStore.search`. NULL-embedding rows are
        excluded by ``WHERE embedding IS NOT NULL``.
        """
        vectors = await embed_texts([query])
        if not vectors:
            return []
        query_vector = vectors[0]

        async with self.pool.acquire() as conn:
            await self._ensure_vector_codec(conn)
            if max_distance is None:
                rows = await conn.fetch(
                    """
                    SELECT id, file_path, original_name, mime_type,
                           embed_text, created_at,
                           embedding <=> $1 AS distance
                    FROM attachments
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> $1, id ASC
                    LIMIT $2
                    """,
                    query_vector,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, file_path, original_name, mime_type,
                           embed_text, created_at,
                           embedding <=> $1 AS distance
                    FROM attachments
                    WHERE embedding IS NOT NULL
                      AND embedding <=> $1 < $3
                    ORDER BY embedding <=> $1, id ASC
                    LIMIT $2
                    """,
                    query_vector,
                    limit,
                    max_distance,
                )
        return [dict(row) for row in rows]

    async def get(self, attachment_id: int) -> AttachmentMeta | None:
        """Fetch a single attachment by id, or None if missing."""
        row = await self.pool.fetchrow(
            "SELECT * FROM attachments WHERE id = $1",
            attachment_id,
        )
        return _row_to_meta(row) if row else None

    async def list_recent(self, limit: int = 20) -> list[AttachmentMeta]:
        """Return recent attachments newest first."""
        rows = await self.pool.fetch(
            "SELECT * FROM attachments ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [_row_to_meta(row) for row in rows]

    async def list_unembedded(self) -> list[AttachmentMeta]:
        """Return rows whose embedding is NULL — used by the backfill script."""
        rows = await self.pool.fetch(
            """
            SELECT id, file_path, original_name, mime_type, size_bytes,
                   source, actor, forwarded_to, created_at
            FROM attachments
            WHERE embedding IS NULL
            ORDER BY id ASC
            """
        )
        return [_row_to_meta(row) for row in rows]


def _row_to_meta(row: asyncpg.Record) -> AttachmentMeta:
    """Convert a DB row into an AttachmentMeta dataclass."""
    return AttachmentMeta(
        id=row["id"],
        file_path=row["file_path"],
        original_name=row["original_name"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        source=row["source"],
        actor=row["actor"],
        forwarded_to=row["forwarded_to"],
        created_at=row["created_at"],
    )
