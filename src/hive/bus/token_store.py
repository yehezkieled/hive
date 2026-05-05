"""Persistent token-usage log for Hive entities.

One row per completed Claude session ``send_prompt`` call. Records the
``usage`` sub-object straight from the stream-json ``result`` event plus the
session id, model, and equivalent API cost. Aggregation happens at query
time via :meth:`TokenStore.totals`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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

    async def daily_cost(self, days: int = 30) -> list[dict[str, Any]]:
        """Per-day cost (zero-filled) plus Postgres day-of-week for baseline math."""
        rows = await self.pool.fetch(
            """
            WITH days AS (
                SELECT generate_series(
                    (CURRENT_DATE - ($1::int - 1) * INTERVAL '1 day')::date,
                    CURRENT_DATE,
                    INTERVAL '1 day'
                )::date AS d
            )
            SELECT
                to_char(days.d, 'YYYY-MM-DD') AS date,
                COALESCE(SUM(t.cost_usd), 0)::float8 AS cost,
                EXTRACT(DOW FROM days.d)::int AS dow
            FROM days
            LEFT JOIN token_usage t
                ON (t.recorded_at AT TIME ZONE 'UTC')::date = days.d
            GROUP BY days.d
            ORDER BY days.d
            """,
            days,
        )
        return [dict(r) for r in rows]

    async def token_burn(self, window: timedelta, buckets: int) -> list[dict[str, Any]]:
        """Time-bucketed token mix + cost; ``window`` split into ``buckets`` equal slices."""
        rows = await self.pool.fetch(
            """
            WITH range_t AS (
                SELECT NOW() - $1::interval AS start_t, NOW() AS end_t
            ),
            bucket_size AS (
                SELECT (end_t - start_t) / $2::int AS size FROM range_t
            ),
            bucket_idx AS (
                SELECT generate_series(0, $2::int - 1) AS i
            ),
            buckets AS (
                SELECT
                    bi.i,
                    (SELECT start_t FROM range_t)
                        + bi.i * (SELECT size FROM bucket_size) AS bucket_start,
                    (SELECT start_t FROM range_t)
                        + (bi.i + 1) * (SELECT size FROM bucket_size) AS bucket_end
                FROM bucket_idx bi
            )
            SELECT
                b.i,
                b.bucket_start AS ts,
                COALESCE(SUM(t.input_tokens), 0)::bigint AS input_tokens,
                COALESCE(SUM(t.output_tokens), 0)::bigint AS output_tokens,
                COALESCE(SUM(t.cache_creation_input_tokens), 0)::bigint
                    AS cache_creation_input_tokens,
                COALESCE(SUM(t.cache_read_input_tokens), 0)::bigint
                    AS cache_read_input_tokens,
                COALESCE(SUM(t.cost_usd), 0)::float8 AS cost
            FROM buckets b
            LEFT JOIN token_usage t
                ON t.recorded_at >= b.bucket_start
                AND t.recorded_at < b.bucket_end
            GROUP BY b.i, b.bucket_start
            ORDER BY b.i
            """,
            window,
            buckets,
        )
        return [dict(r) for r in rows]

    async def cost_by_entity_model(self, since: datetime) -> dict[str, dict[str, float]]:
        """Sparse ``{entity_name: {model: cost_usd}}`` matrix since ``since``."""
        rows = await self.pool.fetch(
            """
            SELECT
                entity_name,
                model,
                COALESCE(SUM(cost_usd), 0)::float8 AS cost
            FROM token_usage
            WHERE recorded_at >= $1
            GROUP BY entity_name, model
            """,
            since,
        )
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            out.setdefault(r["entity_name"], {})[r["model"]] = r["cost"]
        return out

    async def cache_stats(self, since: datetime) -> list[dict[str, Any]]:
        """Per-entity ``{name, hit_pct, cached_tokens, fresh_tokens}`` since ``since``.

        ``hit_pct = cache_read / (cache_read + input) * 100``. Entities with no
        activity in the window are excluded.
        """
        rows = await self.pool.fetch(
            """
            SELECT
                entity_name AS name,
                COALESCE(SUM(cache_read_input_tokens), 0)::bigint AS cached_tokens,
                COALESCE(SUM(input_tokens), 0)::bigint AS fresh_tokens
            FROM token_usage
            WHERE recorded_at >= $1
            GROUP BY entity_name
            ORDER BY entity_name
            """,
            since,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            cached = r["cached_tokens"]
            fresh = r["fresh_tokens"]
            total = cached + fresh
            hit_pct = (cached / total * 100.0) if total > 0 else 0.0
            out.append(
                {
                    "name": r["name"],
                    "hit_pct": round(hit_pct, 1),
                    "cached_tokens": cached,
                    "fresh_tokens": fresh,
                }
            )
        return out

    async def cache_baseline_7d(self, entity_names: list[str]) -> dict[str, float]:
        """Per-entity 7-day rolling cache-hit % baseline for ``entity_names``.

        Returns ``{name: baseline_pct}``. Entities listed but absent from
        ``token_usage`` over the past 7 days are omitted — callers fall back to
        the current-window hit rate so a brand-new entity doesn't render a
        misleading delta arrow against a synthetic zero baseline.
        """
        if not entity_names:
            return {}
        rows = await self.pool.fetch(
            """
            SELECT
                entity_name AS name,
                COALESCE(SUM(cache_read_input_tokens), 0)::bigint AS cached,
                COALESCE(SUM(input_tokens), 0)::bigint AS fresh
            FROM token_usage
            WHERE recorded_at >= NOW() - INTERVAL '7 days'
              AND entity_name = ANY($1::text[])
            GROUP BY entity_name
            """,
            entity_names,
        )
        out: dict[str, float] = {}
        for r in rows:
            total = r["cached"] + r["fresh"]
            if total > 0:
                out[r["name"]] = round(r["cached"] / total * 100.0, 1)
        return out

    async def cache_overall_daily(self, days: int = 7) -> list[float]:
        """Daily overall cache-hit-rate series, length ``days`` (0.0 on empty days)."""
        rows = await self.pool.fetch(
            """
            WITH days AS (
                SELECT generate_series(
                    (CURRENT_DATE - ($1::int - 1) * INTERVAL '1 day')::date,
                    CURRENT_DATE,
                    INTERVAL '1 day'
                )::date AS d
            )
            SELECT
                days.d,
                COALESCE(SUM(t.cache_read_input_tokens), 0)::bigint AS cached,
                COALESCE(SUM(t.input_tokens), 0)::bigint AS fresh
            FROM days
            LEFT JOIN token_usage t
                ON (t.recorded_at AT TIME ZONE 'UTC')::date = days.d
            GROUP BY days.d
            ORDER BY days.d
            """,
            days,
        )
        out: list[float] = []
        for r in rows:
            cached = r["cached"]
            fresh = r["fresh"]
            total = cached + fresh
            out.append(round((cached / total * 100.0), 1) if total > 0 else 0.0)
        return out
