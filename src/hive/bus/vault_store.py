"""Persistent storage for Vault pending actions and approval flow."""

from __future__ import annotations

import asyncpg


class VaultStore:
    """asyncpg-backed store for vault approval actions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_action(
        self,
        vault_name: str,
        description: str,
        requester: str,
    ) -> dict:
        """Create a pending action for user approval."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO vault_actions (vault_name, description, requester)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            vault_name,
            description,
            requester,
        )
        return dict(row)

    async def approve(self, action_id: int) -> dict | None:
        """Approve a pending action. Returns None if not found."""
        row = await self.pool.fetchrow(
            """
            UPDATE vault_actions
            SET status = 'approved', resolved_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            action_id,
        )
        return dict(row) if row else None

    async def deny(self, action_id: int) -> dict | None:
        """Deny a pending action. Returns None if not found."""
        row = await self.pool.fetchrow(
            """
            UPDATE vault_actions
            SET status = 'denied', resolved_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            action_id,
        )
        return dict(row) if row else None

    async def pending(self, vault_name: str) -> list[dict]:
        """List all pending actions for a vault."""
        rows = await self.pool.fetch(
            """
            SELECT * FROM vault_actions
            WHERE vault_name = $1 AND status = 'pending'
            ORDER BY created_at
            """,
            vault_name,
        )
        return [dict(r) for r in rows]

    async def log(self, vault_name: str, limit: int = 20) -> list[dict]:
        """Recent actions for a vault (all statuses)."""
        rows = await self.pool.fetch(
            """
            SELECT * FROM vault_actions
            WHERE vault_name = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            vault_name,
            limit,
        )
        return [dict(r) for r in rows]
