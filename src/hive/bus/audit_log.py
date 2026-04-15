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
from datetime import datetime

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
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                actor,
                action,
                target,
                json.dumps(details) if details is not None else None,
            )
        except Exception:
            logger.exception(
                "Failed to write audit event (actor=%s action=%s)", actor, action
            )

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


def _format_event_row(event: dict) -> str:
    """Render one audit event as a one-liner."""
    ts: datetime = event["timestamp"]
    actor = event["actor"]
    action = event["action"]
    target = event["target"] or "-"
    return f"{ts:%H:%M:%S} {actor} {action} {target}"
