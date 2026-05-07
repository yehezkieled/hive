"""Hive MCP knowledge server.

Exposes one tool: ``search_knowledge(query, kind, limit)`` so an entity
can search blueprints and/or attachments on demand. Mirrors the
auto-retrieve render shape so agents see the same block format whether
the context arrives via auto-prepend (Sprint 11/18) or via this tool
(Sprint 27).

Why a tool, not a forced prepend: junior agents still get a slim
auto-context block (top_k=1, first turn only) as a safety net, but
smarter agents can call this tool mid-conversation when they realise
they need different keywords or more results.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os

import asyncpg
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ── Global state (lazily initialised on first tool call) ────────────────────
_entity_name: str = ""
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        from hive.config import POSTGRES_DSN

        postgres_dsn = os.environ.get("POSTGRES_DSN", str(POSTGRES_DSN))
        _pool = await asyncpg.create_pool(postgres_dsn)
    return _pool


# ── FastMCP server ───────────────────────────────────────────────────────────
mcp = FastMCP("hive-knowledge")


def _format_blueprints(hits: list[dict]) -> list[str]:
    if not hits:
        return []
    lines = ["## Blueprints"]
    for h in hits:
        lines.append(f"\n### {h['title']} (distance={h['distance']:.3f})")
        lines.append(h["chunk_text"])
    return lines


def _format_attachments(hits: list[dict]) -> list[str]:
    if not hits:
        return []
    lines = ["## Attachments"]
    for h in hits:
        snippet = (h.get("embed_text") or "")[:200].replace("\n", " ")
        name = h.get("original_name") or h["file_path"]
        mime = h.get("mime_type") or "unknown"
        lines.append(
            f"- {h['file_path']} ({mime}, original: {name}, distance={h['distance']:.3f})"
            + (f' — snippet: "{snippet}"' if snippet else "")
        )
    return lines


@mcp.tool()
async def search_knowledge(query: str, kind: str = "both", limit: int = 3) -> str:
    """Search Hive's knowledge base (blueprints + uploaded files).

    Use this when:
    - The auto-context didn't include what you need
    - You're mid-task and realise different keywords might match better
    - You need more than the 1 result the auto-context gave you

    Args:
        query: Search phrase. Phrase as keywords, not a sentence.
        kind: "blueprints" (design notes), "attachments" (uploaded files),
            or "both" (default).
        limit: Max results per kind. Default 3.

    Tips:
    - Distances < 0.3 are usually solid matches; > 0.6 is noise.
    - Each blueprint surfaces at most once with its best-matching chunk.
    """
    if kind not in ("blueprints", "attachments", "both"):
        return f"Invalid kind: {kind!r}. Use 'blueprints', 'attachments', or 'both'."

    if not query.strip():
        return "Query is empty."

    if limit < 1:
        return f"Invalid limit: {limit}. Must be >= 1."

    from hive.bus.attachment_store import AttachmentStore
    from hive.config import AUTO_RETRIEVE_MAX_DISTANCE
    from hive.knowledge.blueprints import BlueprintStore

    try:
        pool = await _get_pool()
    except Exception as exc:
        logger.error("knowledge MCP pool error for entity %r: %s", _entity_name, exc)
        return f"Knowledge search error: {exc}"

    blocks: list[str] = []

    if kind in ("blueprints", "both"):
        try:
            store = BlueprintStore(pool)
            hits = await store.search(query, limit=limit, max_distance=AUTO_RETRIEVE_MAX_DISTANCE)
            blocks.extend(_format_blueprints(hits))
        except Exception as exc:
            logger.exception("knowledge MCP blueprint search failed for entity %r", _entity_name)
            blocks.append(f"## Blueprints\n(search failed: {exc})")

    if kind in ("attachments", "both"):
        try:
            store = AttachmentStore(pool)
            hits = await store.search(query, limit=limit, max_distance=AUTO_RETRIEVE_MAX_DISTANCE)
            blocks.extend(_format_attachments(hits))
        except Exception as exc:
            logger.exception("knowledge MCP attachment search failed for entity %r", _entity_name)
            blocks.append(f"## Attachments\n(search failed: {exc})")

    if not blocks:
        return "No matching knowledge found."
    return "\n".join(blocks)


# ── Entry point ──────────────────────────────────────────────────────────────
def _close_pool_on_exit() -> None:
    """Best-effort pool cleanup registered via atexit."""
    global _pool
    if _pool is not None:
        try:
            asyncio.run(_pool.close())
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Hive MCP knowledge server")
    parser.add_argument("--entity", required=True, help="Entity name this server serves")
    args = parser.parse_args()

    global _entity_name
    _entity_name = args.entity

    logging.basicConfig(level=logging.WARNING)
    atexit.register(_close_pool_on_exit)
    mcp.run()


if __name__ == "__main__":
    main()
