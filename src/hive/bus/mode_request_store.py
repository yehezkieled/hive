"""Persistent storage for yolo/yotree mode-elevation requests.

Shape mirrors VaultStore. An entity asks to be elevated; the approver
(user for maestro requests, parent maestro for lead requests, parent lead
for worker requests) resolves via /approve mode or /deny mode.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg


class ModeRequestStore:
    """asyncpg-backed store for mode-elevation approval flow."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create(
        self,
        requester: str,
        requested_mode: str,
        approver: str,
        reason: str | None = None,
        kind: str = "mode_request",
    ) -> dict:
        """Create a pending approval row.

        ``kind`` discriminates a permission-mode elevation request
        (``"mode_request"``, the default) from an interactive-gate decision
        (``"gate"``, Ticket 003). The row shape is shared; the kind keeps the
        two flows from leaking into each other's listings.
        """
        row = await self.pool.fetchrow(
            """
            INSERT INTO mode_requests (requester, requested_mode, approver, reason, kind)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            requester,
            requested_mode,
            approver,
            reason,
            kind,
        )
        return dict(row)

    async def get(self, request_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM mode_requests WHERE id = $1",
            request_id,
        )
        return dict(row) if row else None

    async def list_pending(self, approver: str, kind: str | None = None) -> list[dict]:
        """All pending requests awaiting this approver.

        Pass ``kind`` to scope to one flow (``"gate"`` or ``"mode_request"``);
        omit it to list every pending row regardless of kind.
        """
        if kind is None:
            rows = await self.pool.fetch(
                """
                SELECT * FROM mode_requests
                WHERE approver = $1 AND status = 'pending'
                ORDER BY created_at
                """,
                approver,
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT * FROM mode_requests
                WHERE approver = $1 AND status = 'pending' AND kind = $2
                ORDER BY created_at
                """,
                approver,
                kind,
            )
        return [dict(r) for r in rows]

    async def approve(self, request_id: int) -> dict | None:
        """Mark as approved. Returns None if not found or not pending."""
        row = await self.pool.fetchrow(
            """
            UPDATE mode_requests
            SET status = 'approved', resolved_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            request_id,
        )
        return dict(row) if row else None

    async def deny(self, request_id: int, reason: str | None = None) -> dict | None:
        """Mark as denied with optional reason. Returns None if not found or not pending."""
        row = await self.pool.fetchrow(
            """
            UPDATE mode_requests
            SET status = 'denied', resolved_at = NOW(),
                reason = COALESCE($2, reason)
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            request_id,
            reason,
        )
        return dict(row) if row else None

    async def expire_older_than(self, cutoff: datetime) -> list[dict]:
        """Mark pending requests created before `cutoff` as expired. Returns the expired rows."""
        rows = await self.pool.fetch(
            """
            UPDATE mode_requests
            SET status = 'expired', resolved_at = NOW()
            WHERE status = 'pending' AND created_at < $1
            RETURNING *
            """,
            cutoff,
        )
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 20) -> list[dict]:
        """Most recent mode requests regardless of status."""
        rows = await self.pool.fetch(
            """
            SELECT * FROM mode_requests
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
