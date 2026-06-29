"""Persistent storage for Web Push subscriptions (Ticket 041).

A browser registers a push subscription (endpoint + the p256dh/auth keys the
VAPID delivery path needs); the server stores it so it can fan a notification
out to every installed PWA. Shape mirrors the other bus stores: an asyncpg pool,
``fetchrow``/``fetch``/``execute``, returning ``dict(row)``.
"""

from __future__ import annotations

import asyncpg


class PushSubscriptionStore:
    """asyncpg-backed store for Web Push subscriptions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def upsert(self, sub: dict) -> None:
        """Store a subscription, keyed on its endpoint.

        Re-subscribing the same browser (same endpoint) refreshes its keys and
        user_agent rather than inserting a duplicate.
        """
        await self.pool.execute(
            """
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (endpoint) DO UPDATE SET
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth,
                user_agent = EXCLUDED.user_agent
            """,
            sub["endpoint"],
            sub["p256dh"],
            sub["auth"],
            sub.get("user_agent"),
        )

    async def all(self) -> list[dict]:
        """Every stored subscription, oldest first."""
        rows = await self.pool.fetch("SELECT * FROM push_subscriptions ORDER BY created_at")
        return [dict(r) for r in rows]

    async def delete(self, endpoint: str) -> None:
        """Drop a subscription by endpoint (e.g. after a 410 Gone from the push
        service, or when the browser unsubscribes)."""
        await self.pool.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = $1",
            endpoint,
        )
