"""Re-chunk and re-embed every attachment from scratch.

Idempotent — for each attachment row, run the file through the chunked
``embed_attachment`` pipeline and bulk-insert via ``save_chunks`` (which
clears existing chunks first). Safe to re-run after a chunking knob
tweak (different target tokens, different overlap) or after switching
embedding providers. Skips files that vanish from disk or are empty.

Sprint 28 migration 024 drops ``attachments.embedding`` /
``attachments.embed_text``, so existing rows have no chunks until this
script (or a fresh upload) populates ``attachment_chunks``.
"""

from __future__ import annotations

import asyncio
import logging

from hive.bus.attachment_store import AttachmentStore
from hive.bus.migrations.runner import run_migrations
from hive.bus.store import MessageStore
from hive.config import POSTGRES_DSN
from hive.knowledge.attachment_embedder import embed_attachment

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rechunk_attachments")


async def main() -> None:
    store = MessageStore(POSTGRES_DSN)
    await store.connect()
    await run_migrations(store.pool)

    attachments = AttachmentStore(store.pool)
    async with store.pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, file_path, mime_type FROM attachments ORDER BY id")
    log.info("Found %d attachment rows", len(rows))

    rechunked = 0
    skipped = 0
    for row in rows:
        att_id = row["id"]
        file_path = row["file_path"]
        mime_type = row["mime_type"]
        chunks = await embed_attachment(file_path, mime_type)
        if not chunks:
            log.info("Skipped #%d (%s, mime=%s)", att_id, file_path, mime_type)
            skipped += 1
            continue
        await attachments.save_chunks(att_id, chunks)
        log.info(
            "Re-chunked #%d (%s) → %d chunks",
            att_id,
            file_path,
            len(chunks),
        )
        rechunked += 1

    log.info("Done. Re-chunked=%d Skipped=%d", rechunked, skipped)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
