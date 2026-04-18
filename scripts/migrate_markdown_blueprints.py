"""One-off: import existing markdown blueprints into the pgvector store.

Reads every *.md file under BLUEPRINTS_DIR (see hive.config), parses YAML
frontmatter (title/tags), embeds the body via OpenAI, and inserts a row.

Idempotent check: skips files whose title already exists in the DB.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from hive.bus.migrations.runner import run_migrations
from hive.bus.store import MessageStore
from hive.config import BLUEPRINTS_DIR, POSTGRES_DSN
from hive.knowledge.blueprints import BlueprintStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("migrate_blueprints")


def parse_md(path: Path) -> tuple[str, str, list[str]]:
    text = path.read_text()
    title = path.stem
    tags: list[str] = []
    body = text

    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            front = text[4:end]
            body = text[end + 5 :].strip()
            for line in front.splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip()
                elif line.startswith("- "):
                    tags.append(line[2:].strip())

    return title, body, tags


async def main() -> None:
    store = MessageStore(POSTGRES_DSN)
    await store.connect()
    await run_migrations(store.pool)

    bp_store = BlueprintStore(store.pool)
    existing = {row["title"] for row in await bp_store.list_all()}

    files = sorted(Path(BLUEPRINTS_DIR).glob("*.md"))
    log.info("Found %d markdown blueprints in %s", len(files), BLUEPRINTS_DIR)

    imported = 0
    for f in files:
        title, body, tags = parse_md(f)
        if title in existing:
            log.info("Skipping %s (already in DB)", title)
            continue
        bp_id = await bp_store.save(title, body, tags)
        log.info("Imported #%d: %s", bp_id, title)
        imported += 1

    log.info("Done. Imported %d new blueprints.", imported)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
