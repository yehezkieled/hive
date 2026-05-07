"""Re-chunk and re-embed every blueprint from scratch.

Idempotent — for each blueprint row, delete its existing chunks, split
the body via ``split_blueprint``, re-embed, and bulk-insert the new
chunks. Safe to re-run after a chunking-knob tweak (different target
tokens, different overlap) or after switching embedding providers.

Today the corpus is empty, so this is a no-op. The script future-proofs
the migration path: anyone who saves blueprints under v1 chunking and
then bumps ``HIVE_BLUEPRINT_CHUNK_TOKENS`` re-aligns by running this.
"""

from __future__ import annotations

import asyncio
import logging

from hive.bus.migrations.runner import run_migrations
from hive.bus.store import MessageStore
from hive.config import (
    BLUEPRINT_CHUNK_OVERLAP_TOKENS,
    BLUEPRINT_CHUNK_TOKENS,
    POSTGRES_DSN,
)
from hive.knowledge.chunking import split_blueprint
from hive.knowledge.embedder import embed_texts

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rechunk_blueprints")


async def main() -> None:
    store = MessageStore(POSTGRES_DSN)
    await store.connect()
    await run_migrations(store.pool)

    rechunked = 0
    skipped = 0
    async with store.pool.acquire() as conn:
        from pgvector.asyncpg import register_vector

        await register_vector(conn)
        rows = await conn.fetch("SELECT id, title, body FROM blueprints ORDER BY id")
        log.info("Found %d blueprints", len(rows))

        for row in rows:
            bp_id = row["id"]
            body = row["body"]
            chunks = split_blueprint(
                body,
                target_tokens=BLUEPRINT_CHUNK_TOKENS,
                overlap_tokens=BLUEPRINT_CHUNK_OVERLAP_TOKENS,
            )
            if not chunks:
                log.info("Skipped #%d %r (empty body)", bp_id, row["title"])
                skipped += 1
                continue

            embeddings = await embed_texts(chunks)
            async with conn.transaction():
                await conn.execute("DELETE FROM blueprint_chunks WHERE blueprint_id = $1", bp_id)
                await conn.executemany(
                    """
                    INSERT INTO blueprint_chunks
                        (blueprint_id, chunk_index, text, embedding)
                    VALUES ($1, $2, $3, $4)
                    """,
                    [(bp_id, i, chunk, embeddings[i]) for i, chunk in enumerate(chunks)],
                )
            log.info("Re-chunked #%d %r → %d chunks", bp_id, row["title"], len(chunks))
            rechunked += 1

    log.info("Done. Re-chunked=%d Skipped=%d", rechunked, skipped)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
