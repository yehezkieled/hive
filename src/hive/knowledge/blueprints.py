"""PostgreSQL + pgvector blueprint store with chunked Voyage embeddings.

Blueprints are post-project knowledge docs. The body is the source of truth
on the ``blueprints`` row; embeddings live one-per-chunk in
``blueprint_chunks``. ``save`` splits body → embeds chunks in a single
Voyage call → bulk-inserts chunks in one transaction. ``search`` returns
each parent blueprint at most once with its best-matching chunk attached
as ``chunk_text`` — the auto-retrieve flow then prepends only that chunk
to agent prompts instead of the whole body.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from hive.config import BLUEPRINT_CHUNK_OVERLAP_TOKENS, BLUEPRINT_CHUNK_TOKENS
from hive.knowledge.chunking import split_blueprint
from hive.knowledge.embedder import embed_texts


class BlueprintStore:
    """asyncpg-backed semantic blueprint store."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def _ensure_vector_codec(self, conn: asyncpg.Connection) -> None:
        """Register the pgvector codec once per connection.

        pgvector ships a Python codec so asyncpg can encode/decode
        ``list[float]`` <-> ``vector``. We call this per-connection because
        pool connections are reused but the codec is connection-scoped.
        """
        await register_vector(conn)

    async def save(self, title: str, body: str, tags: list[str] | None = None) -> int:
        """Save a blueprint, split + embed its body, store everything. Return id.

        The body is split via ``split_blueprint`` (markdown-aware, code-fence
        safe). Short bodies return as a single chunk; long bodies fan out to
        N chunks of ~``BLUEPRINT_CHUNK_TOKENS`` each. All chunks are embedded
        in one Voyage call and inserted under one transaction so the
        blueprint and its chunks always commit together.
        """
        tags = tags or []
        chunks = split_blueprint(
            body,
            target_tokens=BLUEPRINT_CHUNK_TOKENS,
            overlap_tokens=BLUEPRINT_CHUNK_OVERLAP_TOKENS,
        )
        # Whitespace-only body → no chunks. We still insert the blueprint
        # row (caller asked us to save it), just with no embedded chunks.
        embeddings = await embed_texts(chunks) if chunks else []

        async with self.pool.acquire() as conn:
            await self._ensure_vector_codec(conn)
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO blueprints (title, body, tags)
                    VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    title,
                    body,
                    tags,
                )
                bp_id = row["id"]
                if chunks:
                    await conn.executemany(
                        """
                        INSERT INTO blueprint_chunks
                            (blueprint_id, chunk_index, text, embedding)
                        VALUES ($1, $2, $3, $4)
                        """,
                        [(bp_id, i, chunk, embeddings[i]) for i, chunk in enumerate(chunks)],
                    )
        return bp_id

    async def search(
        self,
        query: str,
        limit: int = 5,
        max_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return blueprints ranked by their best matching chunk.

        Each parent blueprint surfaces at most once: the inner ``DISTINCT ON
        (b.id)`` picks the chunk with the lowest cosine distance per
        blueprint, and the outer ORDER BY ranks blueprints by that distance.
        Result rows include ``chunk_text`` and ``chunk_index`` so callers
        (e.g. auto-retrieve) can show only the matched section.
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
                        SELECT DISTINCT ON (b.id)
                            b.id, b.title, b.body, b.tags, b.created_at,
                            c.text AS chunk_text,
                            c.chunk_index,
                            c.embedding <=> $1 AS distance
                        FROM blueprints b
                        JOIN blueprint_chunks c ON c.blueprint_id = b.id
                        WHERE c.embedding IS NOT NULL
                        ORDER BY b.id, c.embedding <=> $1
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
                        SELECT DISTINCT ON (b.id)
                            b.id, b.title, b.body, b.tags, b.created_at,
                            c.text AS chunk_text,
                            c.chunk_index,
                            c.embedding <=> $1 AS distance
                        FROM blueprints b
                        JOIN blueprint_chunks c ON c.blueprint_id = b.id
                        WHERE c.embedding IS NOT NULL
                          AND c.embedding <=> $1 < $3
                        ORDER BY b.id, c.embedding <=> $1
                    ) sub
                    ORDER BY distance, id ASC
                    LIMIT $2
                    """,
                    query_vector,
                    limit,
                    max_distance,
                )
        return [dict(row) for row in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all blueprints, newest first, without embeddings."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, title, body, tags, created_at "
                "FROM blueprints ORDER BY created_at DESC, id DESC"
            )
        return [dict(row) for row in rows]
