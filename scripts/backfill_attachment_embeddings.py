"""Backfill embeddings for attachments that don't yet have one.

Idempotent — selects rows where ``embedding IS NULL`` and runs them
through the same ``embed_attachment`` pipeline that the Telegram and
web upload paths use. Safe to re-run; skips files already embedded and
also skips files that vanish from disk.
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
log = logging.getLogger("backfill_attachment_embeddings")


async def main() -> None:
    store = MessageStore(POSTGRES_DSN)
    await store.connect()
    await run_migrations(store.pool)

    attachments = AttachmentStore(store.pool)
    pending = await attachments.list_unembedded()
    log.info("Found %d attachment rows without embedding", len(pending))

    embedded = 0
    skipped = 0
    for meta in pending:
        result = await embed_attachment(meta.file_path, meta.mime_type)
        if result is None:
            log.info(
                "Skipped #%d (%s, mime=%s)",
                meta.id,
                meta.file_path,
                meta.mime_type,
            )
            skipped += 1
            continue
        vector, embed_text = result
        await attachments.update_embedding(meta.id, vector, embed_text)
        log.info("Embedded #%d (%s)", meta.id, meta.file_path)
        embedded += 1

    log.info("Done. Embedded=%d Skipped=%d", embedded, skipped)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
