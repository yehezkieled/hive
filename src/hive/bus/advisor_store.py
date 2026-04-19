"""Persistent log of advisor tool calls made by Hive entities."""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg


class AdvisorStore:
    """asyncpg-backed log of advisor tool calls."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def record(
        self,
        entity_name: str,
        context: str | None,
        response: str | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        status: str,
    ) -> int:
        """Insert one advisor call row. Returns new row id."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO advisor_calls
                (entity_name, context, response, input_tokens, output_tokens, cost_usd, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            entity_name,
            context,
            response,
            input_tokens,
            output_tokens,
            cost_usd,
            status,
        )
        return row["id"]

    async def get_last_call(self, entity_name: str) -> datetime | None:
        """Return called_at of the most recent successful call, or None."""
        row = await self.pool.fetchrow(
            """
            SELECT called_at FROM advisor_calls
            WHERE entity_name = $1 AND status = 'success'
            ORDER BY called_at DESC LIMIT 1
            """,
            entity_name,
        )
        return row["called_at"] if row else None

    async def count_today(self, entity_name: str) -> int:
        """Count successful calls today (UTC) for this entity."""
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.pool.fetchval(
            """
            SELECT COUNT(*) FROM advisor_calls
            WHERE entity_name = $1 AND status = 'success' AND called_at >= $2
            """,
            entity_name,
            today,
        )
