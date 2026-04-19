"""Hive MCP advisor server.

Exposes one tool: advisor(context) — fetches the calling entity's recent
message history from Postgres, builds an Opus review prompt, spawns a
one-shot claude -p subprocess, and returns the response.

Rate-limiting and call logging are handled via the advisor_calls table.
Invoked by Claude Code via --mcp-config; each entity gets its own instance.

Note: the claude subprocess call uses asyncio.create_subprocess_exec so
it does not block the event loop while waiting for Opus to respond.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

        postgres_dsn = os.environ.get("POSTGRES_DSN", POSTGRES_DSN)
        _pool = await asyncpg.create_pool(postgres_dsn)
    return _pool


# ── FastMCP server ───────────────────────────────────────────────────────────
mcp = FastMCP("hive-advisor")


@mcp.tool()
async def advisor(context: str = "") -> str:
    """Review my recent work. Call when stuck or before a major decision.

    Fetches recent message history and asks an Opus model for a second opinion.
    Rate-limited: once per 5 minutes per entity, 20 calls per day.
    """
    from hive.bus.advisor_store import AdvisorStore
    from hive.config import (
        ADVISOR_CONTEXT_MESSAGES,
        ADVISOR_COOLDOWN_SECONDS,
        ADVISOR_DAILY_LIMIT,
    )

    entity_name = _entity_name
    if not entity_name:
        return "Advisor not configured: entity name missing."

    pool = await _get_pool()
    advisor_store = AdvisorStore(pool)

    # ── Rate limiting ────────────────────────────────────────────────────────
    from datetime import UTC, datetime

    last_call = await advisor_store.get_last_call(entity_name)
    if last_call is not None:
        elapsed = (datetime.now(UTC) - last_call).total_seconds()
        if elapsed < ADVISOR_COOLDOWN_SECONDS:
            remaining = int(ADVISOR_COOLDOWN_SECONDS - elapsed)
            await advisor_store.record(
                entity_name, context, None, 0, 0, None, "rate_limited"
            )
            return f"Advisor rate-limited. Try again in {remaining}s."

    daily_count = await advisor_store.count_today(entity_name)
    if daily_count >= ADVISOR_DAILY_LIMIT:
        await advisor_store.record(entity_name, context, None, 0, 0, None, "rate_limited")
        return f"Advisor daily limit ({ADVISOR_DAILY_LIMIT} calls) reached."

    # ── Fetch recent messages ────────────────────────────────────────────────
    rows = await pool.fetch(
        """
        SELECT sender, recipient, content, timestamp FROM messages
        WHERE sender = $1 OR recipient = $1
        ORDER BY timestamp DESC
        LIMIT $2
        """,
        entity_name,
        ADVISOR_CONTEXT_MESSAGES,
    )
    messages_text = "\n".join(
        f"[{r['timestamp'].strftime('%H:%M')} {r['sender']} → {r['recipient']}]: {r['content']}"
        for r in reversed(rows)
    ) or "(no recent messages)"

    # ── Build prompt ─────────────────────────────────────────────────────────
    prompt = (
        f"You are an Opus advisor reviewing the recent work of Hive entity '{entity_name}'.\n\n"
        f"Recent message history:\n{messages_text}\n\n"
        f"{'Specific question: ' + context if context else ''}\n\n"
        "Review: identify drift from goals, missed steps, better approaches, or risks. "
        "Be specific and actionable. Keep your response under 400 words."
    ).strip()

    # ── Call Opus via claude -p (non-blocking) ───────────────────────────────
    input_tokens = output_tokens = 0
    cost_usd = None
    response_text = ""

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            "--model",
            "opus",
            "--output-format",
            "stream-json",
            "--verbose",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            await advisor_store.record(entity_name, context, None, 0, 0, None, "error")
            return "Advisor timed out after 120s."

        stdout = stdout_bytes.decode()
        text_parts: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif event.get("type") == "result":
                    usage = event.get("usage", {}) or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cost_usd = event.get("total_cost_usd")
            except json.JSONDecodeError:
                pass
        response_text = "".join(text_parts) or stdout[:2000]

    except Exception as exc:
        await advisor_store.record(entity_name, context, None, 0, 0, None, "error")
        return f"Advisor error: {exc}"

    await advisor_store.record(
        entity_name, context, response_text, input_tokens, output_tokens, cost_usd, "success"
    )
    return response_text or "Advisor returned an empty response."


# ── Entry point ──────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Hive MCP advisor server")
    parser.add_argument("--entity", required=True, help="Entity name this server serves")
    args = parser.parse_args()

    global _entity_name
    _entity_name = args.entity

    logging.basicConfig(level=logging.WARNING)  # quiet during MCP stdio
    mcp.run()  # runs stdio MCP server


if __name__ == "__main__":
    main()
