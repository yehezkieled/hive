"""PostgreSQL + pgvector blueprint store with Voyage semantic search.

Blueprints are post-project knowledge docs. They're stored as rows in the
``blueprints`` table with a body column (source of truth) and an embedding
column (derived from body via ``embed_texts``). Search is cosine-distance
ordered via pgvector's ``<=>`` operator.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

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
        """Save a blueprint, embed the body, store everything. Return the new id."""
        tags = tags or []
        vectors = await embed_texts([body])
        vector = vectors[0]

        async with self.pool.acquire() as conn:
            await self._ensure_vector_codec(conn)
            row = await conn.fetchrow(
                """
                INSERT INTO blueprints (title, body, tags, embedding)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                title,
                body,
                tags,
                vector,
            )
        return row["id"]

    async def search(
        self,
        query: str,
        limit: int = 5,
        max_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return blueprints ranked by cosine similarity to the query text.

        If ``max_distance`` is given, results above that cosine distance are
        dropped. With a small corpus this avoids prepending a barely-related
        blueprint to every prompt.
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
                    SELECT id, title, body, tags, created_at,
                           embedding <=> $1 AS distance
                    FROM blueprints
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
                    SELECT id, title, body, tags, created_at,
                           embedding <=> $1 AS distance
                    FROM blueprints
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

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all blueprints, newest first, without embeddings."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, title, body, tags, created_at "
                "FROM blueprints ORDER BY created_at DESC, id DESC"
            )
        return [dict(row) for row in rows]
