"""Persistent audit log for commands + state transitions.

One row per event. Every command that flows through the Telegram bridge
and every state transition ProcessManager drives lands here. The design
premise is that over-logging is cheaper to filter than under-logging is
to reconstruct.

`action` uses a namespaced convention (command.<name>, entity.<state>,
task.<op>) so category readouts are a single LIKE-prefix filter.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class AuditLog:
    """asyncpg-backed append-only audit journal."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def record(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Append one audit event.

        Fire-and-continue: any DB error is logged and swallowed so audit
        failure never takes down the caller's work.
        """
        try:
            await self.pool.execute(
                """
                INSERT INTO audit_log (actor, action, target, details)
                VALUES ($1, $2, $3, $4)
                """,
                actor,
                action,
                target,
                details,
            )
        except Exception:
            logger.exception("Failed to write audit event (actor=%s action=%s)", actor, action)

    async def recent(
        self,
        limit: int = 50,
        action_prefix: str | None = None,
    ) -> list[dict]:
        """Return recent audit events, newest first.

        Optional `action_prefix` filters via LIKE `'prefix%'` — useful for
        pulling just one category (e.g., `entity.` or `command.`).
        """
        if action_prefix is None:
            rows = await self.pool.fetch(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT $1",
                limit,
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT * FROM audit_log
                WHERE action LIKE $1 || '%'
                ORDER BY timestamp DESC
                LIMIT $2
                """,
                action_prefix,
                limit,
            )
        return [_row_to_dict(row) for row in rows]

    async def histogram(self, window_minutes: int = 60) -> list[dict[str, Any]]:
        """Per-minute event counts split by namespace prefix.

        Returns ``window_minutes`` rows
        ``{i, command, entity, task, git, vault}``, oldest→newest. The
        ``i`` index lets the caller render bars without joining
        timestamps client-side. ``vault`` was added in Sprint 25 for the
        payment-lead audit family.
        """
        rows = await self.pool.fetch(
            """
            WITH range_t AS (
                SELECT
                    date_trunc('minute', NOW())
                        - ($1::int - 1) * INTERVAL '1 minute' AS start_t
            ),
            buckets AS (
                SELECT
                    i,
                    (SELECT start_t FROM range_t) + i * INTERVAL '1 minute' AS bucket_start
                FROM generate_series(0, $1::int - 1) AS i
            )
            SELECT
                b.i,
                COUNT(*) FILTER (
                    WHERE split_part(a.action, '.', 1) = 'command'
                )::int AS command,
                COUNT(*) FILTER (
                    WHERE split_part(a.action, '.', 1) = 'entity'
                )::int AS entity,
                COUNT(*) FILTER (
                    WHERE split_part(a.action, '.', 1) = 'task'
                )::int AS task,
                COUNT(*) FILTER (
                    WHERE split_part(a.action, '.', 1) = 'git'
                )::int AS git,
                COUNT(*) FILTER (
                    WHERE split_part(a.action, '.', 1) = 'vault'
                )::int AS vault
            FROM buckets b
            LEFT JOIN audit_log a
                ON a.timestamp >= b.bucket_start
                AND a.timestamp < b.bucket_start + INTERVAL '1 minute'
            GROUP BY b.i
            ORDER BY b.i
            """,
            window_minutes,
        )
        return [dict(r) for r in rows]


def _row_to_dict(row: asyncpg.Record) -> dict:
    """Convert a row into a plain dict, decoding the JSONB details field."""
    details = row["details"]
    if isinstance(details, str):
        # asyncpg returns JSONB as str unless a type codec is registered.
        details = json.loads(details) if details else None
    return {
        "id": row["id"],
        "actor": row["actor"],
        "action": row["action"],
        "target": row["target"],
        "details": details,
        "timestamp": row["timestamp"],
    }
