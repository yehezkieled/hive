"""Persistent metadata for uploaded files.

Sprint 17 ships file transit: Telegram and web upload paths drop files
onto the VPS filesystem under ``UPLOADS_DIR`` and record one row per
file here. ``forwarded_to`` records which entity (if any) the caption
routed the file to; NULL means the file was stored without an agent
reading it.

Sprint 18 added a single embedding column. Sprint 28 splits that into
``attachment_chunks`` (one row per chunk, mirroring ``blueprint_chunks``)
so long PDFs/text uploads no longer truncate at 8000 chars and search
ranks against the matching chunk instead of one whole-doc vector.
``save_chunks`` is the new write path; ``search`` joins
``attachment_chunks`` with ``DISTINCT ON (a.id)`` so each parent
attachment surfaces at most once with its best-matching chunk attached.
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

    async def save_chunks(
        self,
        attachment_id: int,
        chunks: list[tuple[str, list[float]]],
    ) -> None:
        """Bulk-insert chunks for an attachment under one transaction.

        Replaces any existing chunks for the row, so callers can use this
        as both first-write and rechunk-rewrite. Mirrors
        :meth:`BlueprintStore.save`'s chunk-insert step.
        """
        if not chunks:
            return
        async with self.pool.acquire() as conn:
            await self._ensure_vector_codec(conn)
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM attachment_chunks WHERE attachment_id = $1",
                    attachment_id,
                )
                await conn.executemany(
                    """
                    INSERT INTO attachment_chunks
                        (attachment_id, chunk_index, text, embedding)
                    VALUES ($1, $2, $3, $4)
                    """,
                    [(attachment_id, i, text, vector) for i, (text, vector) in enumerate(chunks)],
                )

    async def search(
        self,
        query: str,
        limit: int = 5,
        max_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return attachments ranked by their best matching chunk.

        Each parent attachment surfaces at most once: the inner
        ``DISTINCT ON (a.id)`` picks the chunk with the lowest cosine
        distance per attachment, and the outer ORDER BY ranks attachments
        by that distance. Result rows include ``chunk_text`` and
        ``chunk_index`` so callers can show only the matched section.
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
                    SELECT * FROM (
                        SELECT DISTINCT ON (a.id)
                            a.id, a.file_path, a.original_name, a.mime_type,
                            a.created_at,
                            c.text AS chunk_text,
                            c.chunk_index,
                            c.embedding <=> $1 AS distance
                        FROM attachments a
                        JOIN attachment_chunks c ON c.attachment_id = a.id
                        WHERE c.embedding IS NOT NULL
                        ORDER BY a.id, c.embedding <=> $1
                    ) sub
                    ORDER BY distance, id ASC
                    LIMIT $2
                    """,
                    query_vector,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM (
                        SELECT DISTINCT ON (a.id)
                            a.id, a.file_path, a.original_name, a.mime_type,
                            a.created_at,
                            c.text AS chunk_text,
                            c.chunk_index,
                            c.embedding <=> $1 AS distance
                        FROM attachments a
                        JOIN attachment_chunks c ON c.attachment_id = a.id
                        WHERE c.embedding IS NOT NULL
                          AND c.embedding <=> $1 < $3
                        ORDER BY a.id, c.embedding <=> $1
                    ) sub
                    ORDER BY distance, id ASC
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
        """Return rows that have no chunks yet — used by the backfill script."""
        rows = await self.pool.fetch(
            """
            SELECT id, file_path, original_name, mime_type, size_bytes,
                   source, actor, forwarded_to, created_at
            FROM attachments
            WHERE id NOT IN (SELECT DISTINCT attachment_id FROM attachment_chunks)
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
