"""Persistent storage for Vault pending actions and approval flow.

Sprint 6 shipped the original generic approval rows (free-text description).
Sprint 25 Phase 1 adds structured payment fields — ``amount_cents``,
``currency``, ``recipient``, ``idempotency_key``, ``action_type``, plus
``executed_at``/``execution_result``/``denial_reason`` for the lifecycle
that Phase 2 wires through ``ProcessManager.approve_vault_action``.

Status values:
- ``pending`` — created, awaiting approval
- ``approved`` — generic legacy actions (Sprint 6 path)
- ``denied`` — user denied or cap exceeded
- ``completed`` — payment executed successfully (Sprint 25 path)
- ``failed`` — payment provider returned a failure
"""

from __future__ import annotations

import json

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
        *,
        action_type: str = "generic",
        amount_cents: int | None = None,
        currency: str | None = None,
        recipient: str | None = None,
        idempotency_key: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        """Create a pending action for user approval.

        ``action_type`` defaults to ``generic`` for the Sprint 6 free-text
        flow. Set ``action_type='payment'`` plus the structured fields for
        the Sprint 25 payment flow. ``idempotency_key`` is enforced unique
        by ``idx_vault_actions_idempotency`` (NULL allowed for generic
        actions); duplicates raise ``asyncpg.UniqueViolationError``.
        """
        payload_json = json.dumps(payload) if payload is not None else "{}"
        row = await self.pool.fetchrow(
            """
            INSERT INTO vault_actions (
                vault_name, description, requester,
                action_type, amount_cents, currency, recipient,
                idempotency_key, payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            RETURNING *
            """,
            vault_name,
            description,
            requester,
            action_type,
            amount_cents,
            currency,
            recipient,
            idempotency_key,
            payload_json,
        )
        return dict(row)

    async def get(self, action_id: int) -> dict | None:
        """Fetch one action by id."""
        row = await self.pool.fetchrow(
            "SELECT * FROM vault_actions WHERE id = $1",
            action_id,
        )
        return dict(row) if row else None

    async def approve(self, action_id: int) -> dict | None:
        """Approve a pending action (legacy generic path). Returns None if not found."""
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

    async def deny(self, action_id: int, reason: str | None = None) -> dict | None:
        """Deny a pending action with an optional reason. Returns None if not found."""
        row = await self.pool.fetchrow(
            """
            UPDATE vault_actions
            SET status = 'denied', resolved_at = NOW(),
                denial_reason = COALESCE($2, denial_reason)
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            action_id,
            reason,
        )
        return dict(row) if row else None

    async def mark_executed(self, action_id: int, result: dict) -> dict | None:
        """Mark an action completed with the provider's success result."""
        row = await self.pool.fetchrow(
            """
            UPDATE vault_actions
            SET status = 'completed', resolved_at = NOW(),
                executed_at = NOW(), execution_result = $2::jsonb
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            action_id,
            json.dumps(result),
        )
        return dict(row) if row else None

    async def mark_failed(self, action_id: int, reason: str, result: dict | None = None) -> dict | None:
        """Mark an action failed (provider error). Records the reason and any result body."""
        result_json = json.dumps(result) if result is not None else None
        row = await self.pool.fetchrow(
            """
            UPDATE vault_actions
            SET status = 'failed', resolved_at = NOW(),
                executed_at = NOW(),
                denial_reason = $2,
                execution_result = $3::jsonb
            WHERE id = $1 AND status = 'pending'
            RETURNING *
            """,
            action_id,
            reason,
            result_json,
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

    async def spend_total_cents(
        self,
        vault_name: str,
        currency: str,
        since,
    ) -> int:
        """Sum amount_cents for completed payments in the window. Used by spend caps."""
        row = await self.pool.fetchrow(
            """
            SELECT COALESCE(SUM(amount_cents), 0)::BIGINT AS total
            FROM vault_actions
            WHERE vault_name = $1
              AND action_type = 'payment'
              AND currency = $2
              AND status = 'completed'
              AND executed_at >= $3
            """,
            vault_name,
            currency,
            since,
        )
        return int(row["total"]) if row else 0
