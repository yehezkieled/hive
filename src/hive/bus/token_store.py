"""Persistent token-usage log for Hive entities.

One row per completed Claude session ``send_prompt`` call. Records the
``usage`` sub-object straight from the stream-json ``result`` event plus the
session id, model, and equivalent API cost. Aggregation happens at query
time via :meth:`TokenStore.totals`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

import asyncpg


class UsageEvent(TypedDict, total=False):
    """Shape of the data we record for one ``send_prompt`` call.

    Field names match the Anthropic API ``usage`` object (and the claude CLI
    stream-json result event) so we can pass the dict straight through
    without renaming.
    """

    session_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float | None


class TokenStore:
    """asyncpg-backed token usage log."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def record(self, entity_name: str, usage: UsageEvent) -> int:
        """Insert one usage row and return its id.

        ``usage`` is expected to carry the keys from ``UsageEvent``. Missing
        token fields default to 0; ``session_id`` and ``cost_usd`` are
        nullable.
        """
        row = await self.pool.fetchrow(
            """
            INSERT INTO token_usage (
                entity_name, session_id, model,
                input_tokens, output_tokens,
                cache_creation_input_tokens, cache_read_input_tokens,
                cost_usd
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            entity_name,
            usage.get("session_id"),
            usage.get("model", ""),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cache_creation_input_tokens", 0),
            usage.get("cache_read_input_tokens", 0),
            usage.get("cost_usd"),
        )
        return row["id"]

    async def totals(
        self,
        since: datetime | None = None,
        entity_name: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate counts + cost over a time window, optionally per entity.

        Returns a dict with keys:
        ``input_tokens``, ``output_tokens``, ``cache_creation_input_tokens``,
        ``cache_read_input_tokens``, ``cost_usd``, ``call_count``.
        """
        query = """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
                COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read_input_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd,
                COUNT(*) AS call_count
            FROM token_usage
            WHERE 1 = 1
        """
        params: list[Any] = []
        if since is not None:
            params.append(since)
            query += f" AND recorded_at >= ${len(params)}"
        if entity_name is not None:
            params.append(entity_name)
            query += f" AND entity_name = ${len(params)}"

        row = await self.pool.fetchrow(query, *params)
        return dict(row) if row else {}

    async def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent usage rows, newest first."""
        rows = await self.pool.fetch(
            "SELECT * FROM token_usage ORDER BY recorded_at DESC LIMIT $1",
            limit,
        )
        return [dict(row) for row in rows]
